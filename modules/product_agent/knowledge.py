"""Obsidian-backed product-publication knowledge snapshots and retrieval."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping
from shared_platform.capability_runtime import checked_path, tree_files


LEGACY_SCHEMA = "product-publication-knowledge/v1"
SCHEMA_VERSION = "product-publication-knowledge/v2"
REVIEW_SCHEMA = "product-publication-knowledge-review/v1"
DEFAULT_SECTION = Path("99-后台") / "01-系统规则" / "商品发布Agent"
OBSIDIAN_ENV = "PRODUCT_AGENT_OBSIDIAN_VAULT"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def version_digest(value: Any) -> str:
    return 'sha256:' + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', value):
        raise ValueError('complete lowercase SHA-256 digest required')
    return value


def _relative(value: str | Path) -> str:
    text = value.as_posix() if isinstance(value, Path) else str(value)
    if '\\' in text or ':' in text or any(p in {'', '.', '..'} for p in text.split('/')):
        raise ValueError('source path must be an exact relative POSIX path')
    return PurePosixPath(text).as_posix()


def _root(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    # Validate ancestors too; choosing a root through a junction is not a new source.
    return checked_path(Path(absolute.anchor), absolute)


def source_identity(vault_root: Path, section: Path, source_id: str) -> dict:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', source_id):
        raise ValueError('explicit stable source_id required')
    root = _root(vault_root)
    return {'source_id': source_id, 'vault_root': os.path.normcase(str(root.resolve())).replace('\\', '/'),
            'section': _relative(section)}


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    stage='PREPARING';primary=None
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # A same-directory hard-link publishes the complete file without replacing
        # another writer's old snapshot. The unique temporary name is then removed.
        stage='LINKING';os.link(temp_name, path);stage='PUBLISHED'
    except BaseException as error:
        primary=error
        error.output_commit_state='NOT_PUBLISHED' if stage=='PREPARING' else 'UNKNOWN' if stage=='LINKING' else 'PUBLISHED'
        raise
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        except OSError as cleanup:
            if primary is not None:
                primary.add_note('Snapshot temporary-file cleanup also failed: '+repr(cleanup))
            else:
                cleanup.output_commit_state=stage
                raise


def discover_obsidian_vault(*, obsidian_config: Path | None = None) -> Path:
    explicit = os.environ.get(OBSIDIAN_ENV, "").strip()
    if explicit:
        vault = _root(Path(explicit).expanduser())
        if not checked_path(vault, '.obsidian').is_dir():
            raise FileNotFoundError(f"{OBSIDIAN_ENV} is not an Obsidian vault: {vault}")
        return vault

    if obsidian_config is None:
        raise ValueError('select an explicit vault/profile or pass an explicit obsidian_config; recent-open discovery is disabled')
    config = _root(obsidian_config)
    document = json.loads(config.read_text(encoding="utf-8"))
    vaults = document.get("vaults") if isinstance(document, Mapping) else None
    if not isinstance(vaults, Mapping):
        raise ValueError("Obsidian configuration contains no vault registry")
    candidates: dict[str, Path] = {}
    for row in vaults.values():
        if not isinstance(row, Mapping) or not str(row.get("path") or "").strip():
            continue
        path = _root(Path(str(row["path"])).expanduser())
        if checked_path(path, '.obsidian').is_dir():
            candidates[os.path.normcase(str(path))] = path
    if not candidates:
        raise FileNotFoundError("no accessible Obsidian vault is registered")
    if len(candidates) != 1:
        raise ValueError('multiple vaults require an explicit --vault/profile: ' + json.dumps([str(p) for p in candidates.values()], ensure_ascii=False))
    return next(iter(candidates.values()))


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    metadata: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        value = raw.strip()
        if not key:
            continue
        if value.startswith("[") and value.endswith("]"):
            try:
                metadata[key] = json.loads(value)
                continue
            except json.JSONDecodeError:
                pass
        metadata[key] = value
    return metadata, text[end + 5 :].lstrip()


def _source_documents(vault_root: Path, section: Path) -> dict[str, dict]:
    vault = _root(vault_root); source = checked_path(vault, _relative(section))
    if not source.is_dir(): raise FileNotFoundError('explicit knowledge section missing: '+str(source))
    # Complete path preflight precedes the first document read, including links in excluded notes.
    paths = [p for p in tree_files(source) if p.suffix.lower() == '.md']
    if len(paths) > 1000: raise ValueError('knowledge section exceeds the bounded 1000-document inspection scope')
    names = [p.relative_to(vault).as_posix() for p in paths]
    if len({n.casefold() for n in names}) != len(names): raise ValueError('case/alias collision in knowledge source paths')
    documents = {}
    for path, relative in zip(paths, names):
        checked_path(vault,path)
        if path.stat().st_size > 2_000_000:raise ValueError('knowledge document exceeds 2 MB: '+relative)
        with path.open('rb') as handle:raw=handle.read(2_000_001)
        if len(raw) > 2_000_000: raise ValueError('knowledge document exceeds 2 MB: '+relative)
        text = raw.decode('utf-8')
        metadata, body = _frontmatter(text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n'))
        documents[relative] = {'path': relative, 'title': str(metadata.get('title') or path.stem),
            'metadata': metadata, 'content': body, 'source_text': text,
            'content_digest': 'sha256:'+hashlib.sha256(raw).hexdigest()}
    return documents


def _review(value: Mapping[str, Any], expected_digest: str | None = None) -> dict:
    manifest = deepcopy(dict(value))
    fields = {'schema_version','source','review_id','review_scope','reviewed_at','instruction_ref','documents','manifest_digest'}
    if set(manifest) != fields or manifest['schema_version'] != REVIEW_SCHEMA:
        raise ValueError('unsupported or incomplete reviewed manifest')
    actual = version_digest({k:v for k,v in manifest.items() if k != 'manifest_digest'})
    if _digest(manifest['manifest_digest']) != actual: raise ValueError('review manifest digest mismatch')
    if expected_digest is not None and _digest(expected_digest) != actual: raise ValueError('review manifest differs from the expected reviewed digest')
    source = manifest['source']
    if not isinstance(source, dict) or set(source) != {'source_id','vault_root','section'}:
        raise ValueError('review manifest requires a complete source identity')
    if (not isinstance(source['source_id'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',source['source_id'])
            or not isinstance(source['vault_root'],str) or not Path(source['vault_root']).is_absolute()):
        raise ValueError('review source requires a stable ID and explicit absolute root')
    _relative(source['section'])
    if not all(isinstance(manifest[k],str) and manifest[k].strip() for k in ('review_id','review_scope','reviewed_at','instruction_ref')):
        raise ValueError('review manifest requires scope, date and existing instruction reference')
    if datetime.fromisoformat(manifest['reviewed_at']).tzinfo is None: raise ValueError('reviewed_at requires a timezone')
    if not re.fullmatch(r'(?:audit|fixture)://[^\s]{1,220}',manifest['instruction_ref']):
        raise ValueError('review instruction reference must identify upstream evidence')
    if not isinstance(manifest['documents'],list) or len(manifest['documents']) > 1000:
        raise ValueError('review documents must be a bounded explicit list')
    ids=set();names=set()
    required={'document_id','path','source_digest','usage','source_date','supersedes','report_refs'}
    for row in manifest['documents']:
        if not isinstance(row,dict) or set(row)!=required: raise ValueError('incomplete reviewed document identity')
        path=_relative(row['path'])
        if not PurePosixPath(path).is_relative_to(PurePosixPath(source['section'])):
            raise ValueError('reviewed document is outside its section')
        if not isinstance(row['document_id'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',row['document_id']):
            raise ValueError('stable document_id required')
        if row['document_id'].casefold() in ids or path.casefold() in names: raise ValueError('review document identity/path alias collision')
        ids.add(row['document_id'].casefold());names.add(path.casefold());_digest(row['source_digest'])
        if row['usage'] not in {'runtime_rule','reference'}: raise ValueError('review usage must be runtime_rule or reference')
        if not isinstance(row['source_date'],str) or not row['source_date']: raise ValueError('source date required')
        if not isinstance(row['supersedes'],list) or any(not isinstance(x,str) or not x for x in row['supersedes']):
            raise ValueError('supersedes must preserve explicit prior document identities')
        if not isinstance(row['report_refs'],list) or any(not isinstance(x,str) or not re.fullmatch(r'(?:https://|audit://|fixture://)[^\s]+',x) for x in row['report_refs']):
            raise ValueError('execution report references must use explicit evidence or HTTPS links')
    return manifest


def inspect_sources(vault_root: Path, *, section: Path = DEFAULT_SECTION, source_id: str = 'explicit-source',
                    review_manifest: Mapping[str,Any] | None = None, expected_review_digest: str | None = None) -> dict:
    documents = _source_documents(vault_root,section)
    source=source_identity(vault_root,section,source_id)
    manifest=_review(review_manifest,expected_review_digest) if review_manifest is not None else None
    if manifest is not None and manifest['source']!=source: raise ValueError('review manifest source differs from the selected vault/section/source_id')
    reviewed={r['path']:r for r in manifest['documents']} if manifest else {}
    rows=[];gaps=[]
    for path,document in documents.items():
        approval=reviewed.get(path)
        if approval and approval['source_digest']!=document['content_digest']:
            reason='SOURCE_DIGEST_CHANGED';gaps.append({'path':path,'reason':reason,'expected':approval['source_digest'],'actual':document['content_digest']})
        elif not approval: reason='NOT_IN_REVIEWED_MANIFEST'
        elif approval['usage']=='reference': reason='REFERENCE_ONLY'
        elif expected_review_digest is None: reason='EXPECTED_REVIEW_DIGEST_REQUIRED'
        else: reason='REVIEWED_RUNTIME_RULE'
        rows.append({'path':path,'title':document['title'],'source_digest':document['content_digest'],
            'frontmatter_status':document['metadata'].get('status'),'decision':reason,
            'document_id':approval['document_id'] if approval else None,'source_date':approval['source_date'] if approval else document['metadata'].get('updated'),
            'supersedes':approval['supersedes'] if approval else [],'report_refs':approval['report_refs'] if approval else [],
            'original_ref':checked_path(_root(vault_root),path).as_uri(),
            'review_scope':manifest['review_scope'] if approval else None,'current_execution_authority':False})
    actual_names={path.casefold():path for path in documents}
    for path in reviewed.keys()-documents.keys():
        alias=actual_names.get(path.casefold())
        gaps.append({'path':path,'reason':'REVIEW_PATH_ALIAS_MISMATCH' if alias else 'REVIEWED_SOURCE_MISSING','actual_path':alias})
    if manifest is None:gaps.append({'reason':'REVIEW_MANIFEST_REQUIRED'})
    elif expected_review_digest is None:gaps.append({'reason':'EXPECTED_REVIEW_DIGEST_REQUIRED'})
    return {'schema_version':'product-publication-knowledge-inspection/v1','source':source,'documents':rows,'gaps':gaps,
        'review_manifest_digest':manifest['manifest_digest'] if manifest else None,
        'reviewed_at':manifest['reviewed_at'] if manifest else None,'ready':not gaps,
        'current_execution_authority':False,'fresh_business_facts_verified':False}


def build_snapshot(vault_root: Path, *, section: Path = DEFAULT_SECTION, source_id: str = 'explicit-source',
                   review_manifest: Mapping[str,Any] | None = None, expected_review_digest: str | None = None,
                   mode: str = 'runtime') -> dict[str,Any]:
    if mode not in {'runtime','reference'}: raise ValueError('knowledge export mode must be runtime or reference')
    inspection=inspect_sources(vault_root,section=section,source_id=source_id,review_manifest=review_manifest,expected_review_digest=expected_review_digest)
    if inspection['gaps']: raise ValueError('review manifest/source gaps: '+json.dumps(inspection['gaps'],ensure_ascii=False))
    manifest=_review(review_manifest,expected_review_digest)
    documents=_source_documents(vault_root,section); selected=[]
    for row in sorted(manifest['documents'],key=lambda r:r['path']):
        if row['usage']!=('runtime_rule' if mode=='runtime' else 'reference'):continue
        document=documents[row['path']]
        if document['content_digest']!=row['source_digest']: raise ValueError('source changed during export: '+row['path'])
        selected.append({**document,'document_id':row['document_id']})
    if not selected: raise ValueError('review manifest has no approved '+mode+' documents')
    snapshot={'schema_version':SCHEMA_VERSION,'section':_relative(section),'source':inspection['source'],
              'review_manifest':manifest,'documents':selected}
    if mode=='reference': snapshot['knowledge_mode']='reference'
    snapshot['knowledge_version']=version_digest(snapshot)
    return snapshot


def sync_snapshot(output: Path | None = None, *, vault_root: Path | None = None, section: Path = DEFAULT_SECTION,
                  source_id: str = 'explicit-source', review_manifest: Mapping[str,Any] | None = None,
                  expected_review_digest: str | None = None, output_root: Path | None = None, write: bool = False,
                  mode: str = 'runtime') -> dict[str,Any]:
    if vault_root is None: raise ValueError('explicit vault_root required; no recent-vault fallback')
    snapshot=build_snapshot(vault_root,section=section,source_id=source_id,review_manifest=review_manifest,expected_review_digest=expected_review_digest,mode=mode)
    if write:
        if output is None or output_root is None: raise ValueError('explicit output and trusted output_root required for local write')
        target=checked_path(_root(output_root),output)
        if target.is_relative_to(_root(vault_root)): raise ValueError('snapshot output must not modify the source vault')
        if target.exists():
            previous=json.loads(target.read_text(encoding='utf-8'))
            if previous!=snapshot:raise ValueError('snapshot output exists; preserve old bytes and choose a new versioned path')
        else:_atomic_json(target,snapshot)
    return snapshot


def _terms(value: str) -> list[str]:
    lowered = value.casefold()
    ascii_terms = re.findall(r"[a-z0-9][a-z0-9_.-]+", lowered)
    chinese_runs = re.findall(r"[\u3400-\u9fff]+", lowered)
    chinese_terms: list[str] = []
    for run in chinese_runs:
        chinese_terms.append(run)
        chinese_terms.extend(run[index : index + 2] for index in range(max(0, len(run) - 1)))
    return list(dict.fromkeys(ascii_terms + chinese_terms))


@dataclass(frozen=True)
class KnowledgeMatch:
    path: str
    title: str
    score: int
    content: str
    metadata: Mapping[str, Any]
    knowledge_version: str = ''
    usage: str = 'runtime_rule'

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "score": self.score,
            "content": self.content,
            "metadata": dict(self.metadata),
            "knowledge_version": self.knowledge_version,
            "usage": self.usage,
        }


def validate_snapshot(snapshot: Mapping[str,Any]) -> dict:
    value=deepcopy(dict(snapshot));schema=value.get('schema_version')
    if schema not in {SCHEMA_VERSION,LEGACY_SCHEMA}:raise ValueError('unsupported knowledge snapshot schema')
    keys={'schema_version','section','documents','knowledge_version'}
    if schema==SCHEMA_VERSION:
        keys|={'source','review_manifest'}
        if 'knowledge_mode' in value:
            if value['knowledge_mode']!='reference':raise ValueError('unsupported knowledge snapshot mode')
            keys.add('knowledge_mode')
    elif 'vault_name' in value:keys.add('vault_name')
    if set(value)!=keys:raise ValueError('unexpected or incomplete knowledge snapshot fields')
    identity={k:v for k,v in value.items() if k not in {'knowledge_version','vault_name'}}
    if _digest(value['knowledge_version'])!=version_digest(identity):raise ValueError('knowledge version digest mismatch')
    section=_relative(value['section']);documents=value['documents']
    if not isinstance(documents,list) or not 1<=len(documents)<=1000:raise ValueError('knowledge documents must be a bounded nonempty list')
    review=None;expected={}
    if schema==SCHEMA_VERSION:
        review=_review(value['review_manifest'])
        if value['source']!=review['source'] or section!=review['source']['section']:raise ValueError('knowledge source and review binding differ')
        selected_usage='reference' if value.get('knowledge_mode')=='reference' else 'runtime_rule'
        expected={r['path']:r for r in review['documents'] if r['usage']==selected_usage}
    seen=set()
    for row in documents:
        required={'path','title','metadata','content','content_digest'}
        if schema==SCHEMA_VERSION:required|={'source_text','document_id'}
        if not isinstance(row,dict) or set(row)!=required:raise ValueError('invalid knowledge document fields')
        path=_relative(row['path'])
        if not PurePosixPath(path).is_relative_to(PurePosixPath(section)):raise ValueError('knowledge document outside section')
        if path.casefold() in seen:raise ValueError('knowledge document path alias collision')
        seen.add(path.casefold());_digest(row['content_digest'])
        if not isinstance(row['title'],str) or not isinstance(row['content'],str) or not isinstance(row['metadata'],dict):
            raise ValueError('invalid knowledge text/metadata')
        if schema==SCHEMA_VERSION:
            bound=expected.get(path)
            if bound is None or bound['document_id']!=row['document_id'] or bound['source_digest']!=row['content_digest']:
                raise ValueError('knowledge document lacks its exact reviewed source identity')
            text=row['source_text']
            if not isinstance(text,str) or 'sha256:'+hashlib.sha256(text.encode('utf-8')).hexdigest()!=row['content_digest']:
                raise ValueError('knowledge source text digest mismatch')
            metadata,body=_frontmatter(text.lstrip('\ufeff').replace('\r\n','\n').replace('\r','\n'))
            if metadata!=row['metadata'] or body!=row['content'] or str(metadata.get('title') or PurePosixPath(path).stem)!=row['title']:
                raise ValueError('knowledge content/metadata differs from the preserved source text')
    if schema==SCHEMA_VERSION and set(expected)!={r['path'] for r in documents}:raise ValueError('knowledge snapshot omits reviewed runtime documents')
    return value


class KnowledgeBase:
    def __init__(self, snapshot: Mapping[str, Any], *, expected_version: str | None = None,
                 expected_review_digest: str | None = None, mode: str = 'runtime'):
        value=validate_snapshot(snapshot)
        if mode not in {'runtime','replay','reference'}:raise ValueError('knowledge mode must be runtime, reference or replay')
        if expected_version is not None and _digest(expected_version)!=value['knowledge_version']:
            raise ValueError('knowledge version differs from the task expected version')
        legacy=value['schema_version']==LEGACY_SCHEMA
        is_reference=value.get('knowledge_mode')=='reference'
        if is_reference != (mode=='reference'):
            raise ValueError('reference snapshot requires explicit reference mode and runtime snapshot cannot use reference mode')
        if legacy:
            if mode!='replay':raise ValueError('legacy v1 is read-only replay; source text/review binding were not retained')
            if expected_review_digest is not None:raise ValueError('legacy v1 has no reviewed manifest digest')
        else:
            if expected_review_digest is not None and _digest(expected_review_digest)!=value['review_manifest']['manifest_digest']:
                raise ValueError('knowledge review digest differs from the task approved manifest')
            if mode=='runtime' and expected_version is None and expected_review_digest is None:
                raise ValueError('runtime knowledge requires an expected version or approved review manifest digest')
            if mode=='reference' and expected_version is None:
                raise ValueError('reference knowledge requires the task expected version')
        self._snapshot=value;self._version=value['knowledge_version'];self.mode=mode

    @property
    def snapshot(self) -> dict:
        return deepcopy(self._snapshot)

    @property
    def version(self) -> str:
        return self._version

    def provenance(self) -> dict:
        legacy=self._snapshot['schema_version']==LEGACY_SCHEMA
        review=self._snapshot.get('review_manifest',{})
        return {'knowledge_version':self.version,'mode':self.mode,'review_status':'LEGACY_UNVERIFIABLE_REVIEW' if legacy else 'MANIFEST_BOUND',
            'source':deepcopy(self._snapshot.get('source')),'source_text_preserved':not legacy,
            'reviewed_at':review.get('reviewed_at'),'review_scope':review.get('review_scope'),
            'current_execution_authority':False,'fresh_business_facts_verified':False,
            'limitations':['v1 raw source text and review scope were not stored; content_digest cannot prove those missing bytes'] if legacy else []}

    @classmethod
    def from_path(cls, path: Path, **kwargs) -> "KnowledgeBase":
        payload = json.loads(_root(path).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("knowledge snapshot must be an object")
        return cls(payload,**kwargs)

    def search(
        self,
        query: str,
        *,
        filters: Mapping[str, str] | None = None,
        limit: int = 6,
    ) -> list[KnowledgeMatch]:
        if type(limit) is not int or limit < 1 or limit > 20:
            raise ValueError("knowledge search limit must be between 1 and 20")
        query_terms = _terms(query)
        normalized_filters = {
            str(key): str(value).casefold()
            for key, value in (filters or {}).items()
            if str(value).strip()
        }
        matches: list[KnowledgeMatch] = []
        for row in self._snapshot.get("documents") or ():
            if not isinstance(row, Mapping):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
            if any(
                str(metadata.get(key) or "").casefold() != value
                for key, value in normalized_filters.items()
            ):
                continue
            title = str(row.get("title") or "")
            content = str(row.get("content") or "")
            haystack = f"{title}\n{json.dumps(metadata, ensure_ascii=False)}\n{content}".casefold()
            score = sum(4 if term in title.casefold() else 1 for term in query_terms if term in haystack)
            if not query_terms:
                score = 1
            if score:
                matches.append(
                    KnowledgeMatch(
                        path=str(row.get("path") or ""),
                        title=title,
                        score=score,
                        content=content,
                        metadata=deepcopy(metadata),
                        knowledge_version=self.version,
                        usage='historical_reference' if self.mode=='replay' else 'reference' if self.mode=='reference' else 'runtime_rule',
                    )
                )
        matches.sort(key=lambda item: (-item.score, item.path.casefold()))
        return matches[:limit]


__all__ = [
    "DEFAULT_SECTION",
    "KnowledgeBase",
    "KnowledgeMatch",
    "build_snapshot",
    "discover_obsidian_vault",
    "sync_snapshot",
    "inspect_sources",
    "source_identity",
    "validate_snapshot",
    "version_digest",
]
