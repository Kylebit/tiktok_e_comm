"""Local, product-wide accounting at the actual paid request boundary.

This is an event ledger, not another facts or approval store. Callers pass the
existing round-1 identity, applicable policy and upper-layer verified historical
usage. Neither a JSON boolean nor this module proves external billing facts.
Lock order: product phase -> image/chat business -> short ledger lock. No ledger
lock is held while calling a provider. Interrupted reservations remain occupied.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping
import uuid

from modules.sourcing.image_generation_checkpoint import atomic_json, business_lock, digest
from shared_platform.publication_autopilot import validate_autopilot_policy
from shared_platform.publication_rounds import canonical_digest

SCHEMA = 'product-paid-requests/v1'
UNKNOWN = {'RESERVED', 'ATTEMPTED', 'UNKNOWN'}


class PaidRequestBlocked(ValueError):
    """No provider call is allowed until the named local contract is satisfied."""


def _sha(value: Any) -> str:
    value = str(value or '').removeprefix('sha256:')
    if len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise PaidRequestBlocked('paid evidence requires a complete SHA256')
    return value


def _read(path: Path) -> dict:
    if path.is_symlink() or path.is_junction():
        raise PaidRequestBlocked(f'paid record is redirected: {path.name}')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError) as error:
        raise PaidRequestBlocked(f'paid record requires recovery: {path.name}') from error
    if not isinstance(value, dict):
        raise PaidRequestBlocked('paid record must be an object')
    return value


def _pointer(value: Any, pointer: str) -> Any:
    if pointer == '':
        return value
    if not pointer.startswith('/'):
        raise PaidRequestBlocked('source identity requires an explicit JSON pointer')
    try:
        for key in pointer[1:].split('/'):
            key = key.replace('~1', '/').replace('~0', '~')
            value = value[int(key)] if isinstance(value, list) else value[key]
    except (KeyError, ValueError, TypeError, IndexError) as error:
        raise PaidRequestBlocked('historical source pointer is not present') from error
    return value


_LEGACY_IMAGE_SCHEMAS = {'brand-image-lingshi-checkpoint/v1': 'brand',
                         'localized-image-lingshi-checkpoint/v1': 'localized'}
_LEGACY_IMAGE_REPORTS = {'brand-image-generation.json': 'brand-image-generation/v1',
                         'brand-image-translation.json': 'brand-image-translation/v1',
                         'brand-image-rework.json': 'brand-image-rework/v1'}


def _legacy_usage_file(path: Path, root: Path) -> bytes:
    path, root = Path(path), Path(root)
    if (not path.is_file() or not path.resolve().is_relative_to(root.resolve())
            or any(parent.exists() and (parent.is_symlink() or parent.is_junction())
                   for parent in (path, *path.parents)) or path.stat().st_nlink != 1):
        raise PaidRequestBlocked('LEGACY_USAGE_SOURCE_UNAVAILABLE_OR_REDIRECTED')
    return path.read_bytes()


def _legacy_checkpoint_usage(path: Path, original: Mapping, offer_id: str) -> tuple[dict, dict]:
    """Attribute paid usage from original sources, never establish request ownership."""
    kind = _LEGACY_IMAGE_SCHEMAS.get(original.get('schema_version'))
    match = re.fullmatch(r'lingshi-(?:brand-)?([0-9a-f]{64})\.json', path.name)
    directory = path.parent.parent
    if (not kind or not match or original.get('identity_digest') != match.group(1)
            or original.get('client_business_id') != kind + '-' + match.group(1)[:40]
            or original.get('status') != 'COMPLETED'):
        raise PaidRequestBlocked('LEGACY_USAGE_CHECKPOINT_IDENTITY_OR_OUTCOME_UNPROVEN')
    cp_raw = _legacy_usage_file(path, directory)
    if json.loads(cp_raw) != original:
        raise PaidRequestBlocked('LEGACY_USAGE_CHECKPOINT_SOURCE_CHANGED')
    receipt = original.get('receipt')
    task = original.get('task_id')
    if (type(task) is not int or task <= 0 or not isinstance(receipt, dict)
            or receipt.get('task_id') != task or receipt.get('status') != 'COMPLETED'
            or receipt.get('outcome_unknown') is True or receipt.get('request_attempted') is not True
            or receipt.get('client_business_id') != original['client_business_id']
            or not str(receipt.get('provider') or '').startswith('lingshi')):
        raise PaidRequestBlocked('LEGACY_USAGE_TASK_OR_RECEIPT_UNPROVEN')
    output = path.with_suffix('.png')
    output_raw = _legacy_usage_file(output, directory)
    output_sha = hashlib.sha256(output_raw).hexdigest()
    if _sha(original.get('output_digest')) != output_sha or _sha(receipt.get('output_digest')) != output_sha:
        raise PaidRequestBlocked('LEGACY_USAGE_OUTPUT_CHANGED')
    snapshot_path = directory / 'round1-approved-snapshot.json'
    snapshot_raw = _legacy_usage_file(snapshot_path, directory)
    snapshot = json.loads(snapshot_raw)
    unsigned = dict(snapshot); seal = unsigned.pop('snapshot_digest', None)
    if (snapshot.get('schema_version') != 'round1-approved-snapshot/v1'
            or snapshot.get('offer_id') != offer_id or snapshot.get('status') != 'APPROVED'
            or not snapshot.get('product_approval_id') or not snapshot.get('canonical_targets')
            or seal != canonical_digest(unsigned)):
        raise PaidRequestBlocked('LEGACY_USAGE_ORIGINAL_R1_UNPROVEN')
    roles = {(brand['id'], asset['role']) for brand in snapshot.get('image_plan', {}).get('brand_plans', [])
             for asset in brand.get('generated_assets', [])}
    locales = {target.rsplit(':', 1)[-1] for target in snapshot['canonical_targets']}
    locale_countries = {'ms-MY': 'MY', 'th-TH': 'TH', 'vi-VN': 'VN', 'ru-RU': 'RU', 'es-MX': 'MX'}
    candidates = []
    for name, schema in _LEGACY_IMAGE_REPORTS.items():
        report_path = directory / name
        if not report_path.exists(): continue
        report_raw = _legacy_usage_file(report_path, directory)
        report = json.loads(report_raw)
        if report.get('schema_version') != schema or report.get('offer_id') != offer_id:
            raise PaidRequestBlocked('LEGACY_USAGE_REPORT_PRODUCT_CHANGED')
        if report.get('round1_snapshot_digest') is not None and report['round1_snapshot_digest'] != seal:
            raise PaidRequestBlocked('LEGACY_USAGE_REPORT_R1_CHANGED')
        for field in ('assets', 'superseded_assets', 'replacement_assets'):
            for index, row in enumerate(report.get(field) or []):
                if not isinstance(row, dict) or (row.get('task_id') or row.get('provider_task_id')) != task: continue
                business = {'kind': kind, 'offer_id': offer_id, 'brand_id': row.get('brand_id'), 'role': row.get('role')}
                if row.get('locale'): business['locale'] = row['locale']
                if ((business['brand_id'], business['role']) not in roles
                        or (kind == 'localized' and (business.get('locale') not in locale_countries
                            or not any(target.endswith(locale_countries[business['locale']]) for target in locales)))
                        or (kind == 'brand' and 'locale' in business)
                        or row.get('status') != 'COMPLETED' or row.get('outcome_unknown') is True
                        or row.get('provider') != receipt.get('provider') or row.get('model') != receipt.get('model')
                        or row.get('cost') != receipt.get('cost') or _sha(row.get('artifact_digest')) != output_sha
                        or (row.get('artifact_path') and Path(row['artifact_path']).resolve() != output.resolve())):
                    raise PaidRequestBlocked('LEGACY_USAGE_REPORT_TASK_OR_BUSINESS_CONFLICT')
                candidates.append((business, {'report_path': str(report_path),
                    'report_sha256': hashlib.sha256(report_raw).hexdigest(), 'report_pointer': f'/{field}/{index}'}))
    if not candidates or any(row[0] != candidates[0][0] for row in candidates):
        raise PaidRequestBlocked('LEGACY_USAGE_REPORT_MAPPING_MISSING_OR_CONFLICTING')
    business, source = candidates[0]
    proof = {'schema_version': 'legacy-paid-image-usage-provenance/v1', **source,
             'snapshot_path': str(snapshot_path), 'snapshot_sha256': hashlib.sha256(snapshot_raw).hexdigest(),
             'checkpoint_path': str(path), 'checkpoint_sha256': hashlib.sha256(cp_raw).hexdigest(),
             'artifact_path': str(output), 'artifact_sha256': output_sha, 'business': business,
             'authority': 'HISTORICAL_USAGE_ONLY_NO_REQUEST_REPLAY_OR_NEW_GRANT'}
    return proof, business


def validate_usage_baseline(value: Mapping[str, Any], *, offer_id: str) -> dict:
    """Check a verified usage inventory's bindings; never assert completeness ourselves.

    Each row is one prior request, including replaced, failed and unknown work.
    Source pointers bind original raw bytes and product identity. A zero inventory
    also needs explicit upstream completeness evidence; absence never means zero.
    """
    baseline = deepcopy(dict(value))
    if (baseline.get('schema_version') != 'paid-usage-baseline/v1'
            or baseline.get('offer_id') != offer_id
            or baseline.get('scope') != 'ALL_PRODUCT_PAID_REQUESTS'
            or baseline.get('completeness') != 'UPSTREAM_VERIFIED_COMPLETE'
            or not str(baseline.get('verified_by') or '').strip()
            or not str(baseline.get('evidence_ref') or '').startswith(('audit://', 'approval://'))):
        raise PaidRequestBlocked('complete, product-bound prior paid usage is required; missing usage is not zero')
    _sha(baseline.get('evidence_sha256'))
    try:
        stamp = datetime.fromisoformat(baseline['verified_at'])
        if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
            raise ValueError()
    except (KeyError, ValueError, TypeError) as error:
        raise PaidRequestBlocked('usage verification time is invalid') from error
    records = baseline.get('records')
    if not isinstance(records, list):
        raise PaidRequestBlocked('usage records must be explicit, including an empty verified inventory')
    seen = set()
    for row in records:
        if not isinstance(row, dict) or row.get('state') not in {'CONFIRMED', 'FAILED', 'UNKNOWN', 'ATTEMPTED'}:
            raise PaidRequestBlocked('historical request outcome is invalid')
        source = Path(str(row.get('source_path') or ''))
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != _sha(row.get('source_sha256')):
            raise PaidRequestBlocked('historical usage source bytes changed or are unavailable')
        original = _read(source)
        legacy_proof = row.get('legacy_usage_provenance')
        proof=row.get('business_proof')
        if legacy_proof is not None:
            if proof is not None or not isinstance(legacy_proof, list) or not legacy_proof:
                raise PaidRequestBlocked('LEGACY_USAGE_CANNOT_INVENT_ORIGINAL_BUSINESS_DIGEST')
            for item in legacy_proof:
                if not isinstance(item, dict): raise PaidRequestBlocked('LEGACY_USAGE_PROVENANCE_CHANGED')
                cp_path = Path(item['checkpoint_path'])
                cp_original = _read(cp_path)
                expected, _business = _legacy_checkpoint_usage(cp_path, cp_original, offer_id)
                request = _pointer(original, str(row.get('request_pointer') or ''))
                is_cp = original.get('schema_version') in _LEGACY_IMAGE_SCHEMAS
                if (item != expected or row['state'] != 'CONFIRMED'
                        or (is_cp and (original != cp_original or row['request_pointer'] != ''))
                        or (not is_cp and (original.get('schema_version') not in _LEGACY_IMAGE_REPORTS.values()
                            or original.get('offer_id') != offer_id
                            or (request.get('task_id') or request.get('provider_task_id')) != cp_original['task_id']
                            or request.get('provider') != cp_original['receipt']['provider']
                            or request.get('model') != cp_original['receipt']['model']
                            or request.get('cost') != cp_original['receipt'].get('cost')
                            or _sha(request.get('artifact_digest')) != expected['artifact_sha256']))):
                    raise PaidRequestBlocked('LEGACY_USAGE_PROVENANCE_CHANGED')
        elif proof is not None:
            if (not isinstance(proof,dict) or proof.get('offer_id')!=offer_id
                    or digest(proof)!=original.get('business_digest') or proof.get('kind')!=original.get('kind')):
                raise PaidRequestBlocked('historical checkpoint business proof is invalid')
        elif str(_pointer(original, str(row.get('offer_pointer') or ''))) != offer_id:
            raise PaidRequestBlocked('historical usage belongs to a different product')
        request = _pointer(original, str(row.get('request_pointer') or ''))
        if digest(request) != _sha(row.get('request_record_digest')):
            raise PaidRequestBlocked('historical request identity changed')
        if row.get('provider') not in {'lingshi', 'toapis', 'UNKNOWN'}:
            raise PaidRequestBlocked('historical provider must be retained or explicitly UNKNOWN')
        identity = (row['source_sha256'], row['request_pointer'])
        if identity in seen:
            raise PaidRequestBlocked('historical usage contains duplicate requests')
        seen.add(identity)
        if source.stat().st_mtime > stamp.timestamp():
            raise PaidRequestBlocked('historical usage evidence predates its source')
    return baseline


def discover_usage_baseline(*, repo_root: Path, offer_id: str, directory: Path, policy: Mapping, round1: Mapping | None = None) -> dict:
    """Inventory all known R1/R2 persistence roots; empty evidence is generated locally.

    Historical imports require actual request rows, never a report's asset total.
    Unrecognized paid raw/checkpoints report precise gaps instead of assuming zero.
    No credential, platform or database is opened by this inventory.
    """
    scopes = [directory, Path(repo_root)/'data/localized_image_reviews'/offer_id,
              Path(repo_root)/'data/localized_image_packs'/offer_id]
    manifest, records, gaps, seen = [], [], [], {}
    business_proofs={}
    for brand in (round1 or {}).get('image_plan',{}).get('brand_plans') or []:
        for asset in brand.get('generated_assets') or []:
            base={'offer_id':offer_id,'brand_id':brand['id'],'role':asset['role']}
            proof={'kind':'brand',**base}
            business_proofs[digest(proof)]=proof
            for locale in ('ms-MY','th-TH','vi-VN','ru-RU','es-MX'):
                proof={'kind':'localized',**base,'locale':locale}
                business_proofs[digest(proof)]=proof
    def retain(path,raw,sha,pointer,row,provider,identity,state='CONFIRMED',proof=None,legacy_provenance=None):
        normalized = 'lingshi' if provider.startswith('lingshi') else 'toapis' if provider.startswith(('toapis','toapi')) else 'UNKNOWN'
        identity=(normalized,*identity)
        business=proof or (legacy_provenance or {}).get('business')
        if business is None and row.get('brand_id') and row.get('role'):
            business={'kind':'localized' if row.get('locale') else 'brand','offer_id':offer_id,'brand_id':row['brand_id'],'role':row['role']}
            if row.get('locale'):business['locale']=row['locale']
        business_sha=digest(business) if business else None
        if identity in seen:
            previous=seen[identity]
            if previous['state']!=state or (previous['business'] and business_sha and previous['business']!=business_sha):
                gaps.append({'path':str(path),'pointer':pointer,'missing':'same provider task has conflicting original business/outcome evidence'})
            elif legacy_provenance is not None:
                proofs = previous['record'].setdefault('legacy_usage_provenance', [])
                if legacy_provenance not in proofs: proofs.append(legacy_provenance)
            return
        seen[identity]={'state':state,'business':business_sha}
        retained=directory/'paid-requests/baseline-sources'/f'{sha[:24]}.json'
        retained.parent.mkdir(parents=True,exist_ok=True)
        if retained.exists() and retained.read_bytes()!=raw:
            raise PaidRequestBlocked('short historical source path collision')
        if not retained.exists():
            from modules.sourcing.image_generation_checkpoint import atomic_bytes
            atomic_bytes(retained,raw)
        record={'state':state,'provider':normalized,'source_path':str(retained),'original_path':str(path),
                'source_sha256':sha,'offer_pointer':'/offer_id','request_pointer':pointer,'request_record_digest':digest(row)}
        if proof is not None:record['business_proof']=proof
        if legacy_provenance is not None:record['legacy_usage_provenance']=[legacy_provenance]
        seen[identity]['record']=record
        records.append(record)
    def signals(value, pointer=''):
        rows=[]
        if isinstance(value,dict):
            provider=str(value.get('provider') or '').lower()
            schema=str(value.get('schema_version') or '').lower()
            paid_provider=provider.startswith(('lingshi','toapi'))
            unresolved=value.get('status') in {'SUBMITTING','SUBMISSION_UNKNOWN','SUBMITTED','RECOVERY_REQUIRED','REJECTED_BEFORE_TASK'}
            if (value.get('outcome_unknown') is True or value.get('request_attempted') is True
                    or (unresolved and (paid_provider or 'checkpoint' in schema or value.get('task_id')))
                    or bool(value.get('model_calls') or value.get('model_call_count'))
                    or (isinstance(value.get('usage'),dict) and bool(value['usage']))
                    or (paid_provider and (value.get('task_id') or value.get('provider_task_id')))):
                rows.append((pointer,value))
            for key,item in value.items():
                rows.extend(signals(item,pointer+'/'+str(key).replace('~','~0').replace('/','~1')))
        elif isinstance(value,list):
            for i,item in enumerate(value):rows.extend(signals(item,pointer+'/'+str(i)))
        return rows
    def redirected(path):
        return path.is_symlink() or path.is_junction() or bool(path.lstat().st_file_attributes & 0x400) if os.name=='nt' else path.is_symlink()
    def inventory_paths(root):
        pending=[root]
        while pending:
            parent=pending.pop()
            for path in sorted(parent.iterdir()):
                if redirected(path) or not path.resolve().is_relative_to(root.resolve()):
                    gaps.append({'path':str(path),'missing':'non-redirected usage inventory'})
                    continue
                if path==directory/'paid-requests':
                    continue  # Only this product's actual ledger, never a nested namesake.
                if path.is_dir():pending.append(path)
                else:yield path
    for root in scopes:
        if root.exists() and redirected(root):
            raise PaidRequestBlocked(f'prior usage scope is redirected: {root}')
        manifest.append({'root':str(root.resolve()),'exists':root.exists()})
        if not root.exists():
            continue
        for path in inventory_paths(root):
            if re.fullmatch(r'\.lingshi-[0-9a-f]{24}\.lock',path.name):
                continue
            if path.is_symlink():
                gaps.append({'path':str(path),'missing':'non-redirected usage inventory'})
                continue
            if not path.is_file():
                continue
            # Images are evidence, not additional requests. All metadata remains inventoried.
            if path.suffix.lower() in {'.png','.jpg','.jpeg','.webp'}:
                continue
            raw = path.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            manifest.append({'path':str(path.resolve()),'sha256':sha,'size':len(raw)})
            checkpoint_match=re.fullmatch(r'lingshi-(brand|localized)-v2-[0-9a-f]{24}\.(json|identity|events\.jsonl)',path.name)
            if checkpoint_match:
                from modules.sourcing.image_generation_checkpoint import ImageCheckpoint
                main=path.with_name(path.name.split('.',1)[0]+'.json')
                try:
                    cp=ImageCheckpoint.from_path(main)
                    state=cp.read()
                    proof=business_proofs.get(cp.business_digest)
                    if proof is None:raise ValueError('no approved business identity proof')
                    if path!=main:continue  # Already validated paired identity and complete journal; both stay in manifest.
                    if state.get('revision')!=len(cp._events()):raise ValueError('snapshot requires local journal recovery')
                    if state.get('status')=='READY' and state.get('attempt')==0:continue
                    if state.get('attempt')!=0:raise ValueError('prior attempts require individual archived receipts')
                    task=state.get('task_id')
                    identity=('image',str(task)) if task else ('checkpoint',cp.request_digest,state['attempt'])
                    outcome='CONFIRMED' if state['status']=='COMPLETED' else 'FAILED' if state['status']=='FAILED' else 'UNKNOWN'
                    retain(path,raw,sha,'',json.loads(raw),'lingshi',identity,outcome,proof)
                except (ValueError,OSError,KeyError) as error:
                    gaps.append({'path':str(path),'missing':'intact checkpoint, original business proof and all attempts','detail':str(error)})
                continue
            if path.suffix.lower() in {'.md','.html','.htm','.csv','.pdf','.txt'}:
                continue  # Human review artifacts are inventoried, never inferred to be paid attempts.
            if path.suffix not in {'.json','.jsonl','.identity'}:
                if 'checkpoint' in str(path.parent).lower() or 'raw' in path.name.lower() or path.suffix=='.tmp':
                    gaps.append({'path':str(path),'missing':'recognized original request metadata'})
                continue
            try:
                doc = {'events':[json.loads(line) for line in raw.splitlines()]} if path.suffix=='.jsonl' else json.loads(raw)
            except ValueError:
                gaps.append({'path':str(path),'missing':'intact request metadata'})
                continue
            if not isinstance(doc,dict):
                gaps.append({'path':str(path),'missing':'product-bound usage object'})
                continue
            if doc.get('schema_version') in _LEGACY_IMAGE_SCHEMAS:
                try:
                    provenance, _business = _legacy_checkpoint_usage(path, doc, offer_id)
                    retain(path,raw,sha,'',doc,str(doc['receipt']['provider']),('image',str(doc['task_id'])),
                           legacy_provenance=provenance)
                except (ValueError,OSError,KeyError,TypeError) as error:
                    gaps.append({'path':str(path),'missing':'verified legacy paid usage provenance','detail':str(error)})
                continue
            paid_signals=signals(doc)
            if not paid_signals:
                continue
            unknowns=[pointer for pointer,row in paid_signals if row.get('outcome_unknown') is True
                or row.get('status') in {'SUBMITTING','SUBMISSION_UNKNOWN','RECOVERY_REQUIRED','REJECTED_BEFORE_TASK'}]
            if unknowns:
                if str(doc.get('offer_id') or '')==offer_id and all(str(_pointer(doc,p).get('provider') or '').startswith(('lingshi','toapi')) for p in unknowns):
                    for pointer in unknowns:
                        row=_pointer(doc,pointer)
                        task=row.get('task_id') or row.get('provider_task_id')
                        identity=('image',str(task)) if task else ('unknown',sha,pointer)
                        retain(path,raw,sha,pointer,row,str(row['provider']),identity,'UNKNOWN')
                    # Preserve all remaining paid rows too, rather than hiding successes behind one unknown.
                    gaps_for_remaining=[p for p,_ in paid_signals if not any(p==u or p.startswith(u+'/') for u in unknowns)]
                    if gaps_for_remaining:gaps.append({'path':str(path),'pointers':gaps_for_remaining,'missing':'individual remaining paid records alongside UNKNOWN'})
                else:
                    gaps.append({'path':str(path),'pointers':unknowns,'missing':'reconciled original paid outcome; generation_count=0 cannot hide UNKNOWN'})
                continue
            if str(doc.get('offer_id') or '') != offer_id:
                gaps.append({'path':str(path),'missing':'positive product identity / checkpoint business mapping'})
                continue
            candidates = []
            asset_fields = ('assets', 'superseded_assets', 'replacement_assets') if doc.get('schema_version') == 'brand-image-rework/v1' else ('assets',)
            for asset_field in asset_fields:
                for i, asset in enumerate(doc.get(asset_field) or []):
                    if not isinstance(asset,dict):
                        continue
                    if asset.get('external_generation_count') == 0 and not signals(asset):
                        continue
                    task = asset.get('provider_task_id') or asset.get('task_id')
                    provider = str(asset.get('provider') or '')
                    if asset.get('status') != 'COMPLETED' or not task or not provider:
                        gaps.append({'path':str(path),'pointer':f'/{asset_field}/{i}','missing':'original provider/task/outcome'})
                    else:
                        candidates.append((f'/{asset_field}/{i}',asset,provider,('image',str(task))))
            for i, inventory in enumerate(doc.get('text_inventories') or []):
                receipt = inventory.get('translation_receipt') or {}
                if receipt.get('model_calls') == 0:
                    continue
                if receipt.get('model_calls') != 1 or receipt.get('status') != 'AUTO_TRANSLATED' or not receipt.get('source_digest'):
                    gaps.append({'path':str(path),'pointer':f'/text_inventories/{i}','missing':'per-request translation receipt'})
                else:
                    if not inventory.get('source_url') or not (inventory.get('source_artifact_digest') or inventory.get('source_digest')):
                        gaps.append({'path':str(path),'pointer':f'/text_inventories/{i}','missing':'positive source image and per-request text receipt identity'})
                    else:
                        candidates.append((f'/text_inventories/{i}',inventory,str(receipt.get('provider') or ''),
                                           ('chat',inventory['source_url'],inventory.get('source_artifact_digest') or inventory.get('source_digest'),digest(receipt))))
            if (doc.get('request_attempted') is True and isinstance(doc.get('usage'),dict)
                    and doc.get('choices') and doc.get('provider') and doc.get('purpose')):
                if doc['purpose'] in policy['paid_models']['allowed_purposes']:
                    candidates.append(('',doc,str(doc['provider']),('chat-raw',str(doc['provider']),sha)))
                else:
                    manifest[-1]['excluded_paid_purpose']=doc['purpose']
                    continue
            covered=[pointer for pointer,*_rest in candidates]
            uncovered=[pointer for pointer,_row in paid_signals if not any(pointer==prefix or pointer.startswith(prefix+'/') for prefix in covered)]
            if uncovered:
                gaps.append({'path':str(path),'pointers':uncovered,'missing':'individual paid records outside the known receipt inventory'})
                continue
            if not candidates:
                gaps.append({'path':str(path),'missing':'complete individual request records; aggregate or raw usage needs bound migration'})
                continue
            for pointer,row,provider,identity in candidates:
                retain(path,raw,sha,pointer,row,provider,identity)
    if gaps:
        raise PaidRequestBlocked('PRIOR_PAID_USAGE_GAPS: '+json.dumps(gaps,ensure_ascii=False,sort_keys=True))
    return {'schema_version':'paid-usage-baseline/v1','offer_id':offer_id,'scope':'ALL_PRODUCT_PAID_REQUESTS',
            'completeness':'UPSTREAM_VERIFIED_COMPLETE','verified_by':'controlled-local-r1-r2-inventory/v1',
            'verified_at':datetime.now(timezone.utc).isoformat(),'evidence_ref':'audit://paid-usage/controlled-local-inventory',
            'evidence_sha256':digest(manifest),'inventory':manifest,'records':records,
            'completeness_boundary':'All configured R1/R2 product report and localized review/pack metadata roots. External or unrecorded work requires supplied verified usage evidence.'}


class PaidRequestContext:
    """One invocation referencing an existing product approval and shared ledger."""

    def __init__(self, *, offer_id: str, round1: Mapping[str, Any], policy: Mapping[str, Any],
                 reports_root: Path, usage_baseline: Mapping[str, Any] | None = None,
                 invocation_id: str | None = None):
        self.offer_id = str(offer_id)
        if not self.offer_id.isdigit():
            raise PaidRequestBlocked('paid context requires the canonical numeric Product Center id')
        self.round1 = deepcopy(dict(round1))
        unsigned = dict(self.round1)
        supplied = unsigned.pop('snapshot_digest', None)
        if (supplied != canonical_digest(unsigned) or self.round1.get('offer_id') != self.offer_id
                or self.round1.get('schema_version') != 'round1-approved-snapshot/v1'
                or self.round1.get('status') != 'APPROVED' or not self.round1.get('product_approval_id')
                or not self.round1.get('canonical_targets')):
            raise PaidRequestBlocked('paid context requires the intact existing round-1 approval')
        self.policy = validate_autopilot_policy(policy)
        self.cap = self.policy['paid_models']['maximum_confirmed_requests_per_product']
        self.binding = {'round1_snapshot_digest': supplied, 'product_approval_id': self.round1['product_approval_id'],
                        'target_digest': digest(self.round1['canonical_targets']), 'policy_digest': digest(self.policy)}
        self.directory = Path(reports_root).absolute() / self.offer_id
        self.root = self.directory / 'paid-requests'
        if any(path.exists() and (path.is_symlink() or path.is_junction()) for path in [self.root,self.directory,*self.directory.parents]):
            raise PaidRequestBlocked('paid storage path is redirected; use the verified canonical product root')
        self.path = self.root / 'events.jsonl'
        self.lock_key = digest({'scope': 'paid-budget', 'offer_id': self.offer_id})
        self.invocation_id = str(invocation_id or uuid.uuid4().hex)
        self.usage_baseline = deepcopy(dict(usage_baseline)) if usage_baseline is not None else None

    def phase_lock(self):
        return business_lock(self.directory, digest({'scope': 'round2-phase', 'offer_id': self.offer_id}))

    def authorize(self, purpose: str, model: str) -> None:
        if purpose not in self.policy['paid_models']['allowed_purposes'] or not str(model or '').strip():
            raise PaidRequestBlocked('paid purpose/model is outside existing authorization')
        models = self.policy['paid_models'].get('models_by_purpose')
        if models is not None and model not in models.get(purpose, []):
            raise PaidRequestBlocked('model is outside existing purpose authorization')

    def _load(self) -> tuple[list[dict], dict[str, dict]]:
        if not self.path.is_file():
            if any(self.root.glob('raw-*.json')):
                raise PaidRequestBlocked('paid journal is missing; preserve raw receipts for recovery')
            return [], {}
        rows = []
        entries: dict[str, dict] = {}
        previous = None
        try:
            raw = self.path.read_bytes()
            if not raw.endswith(b'\n'):
                raise ValueError('partial event')
            for line in raw.splitlines():
                row = json.loads(line)
                seal = row.pop('event_digest')
                if (digest(row) != seal or row['previous'] != previous or row['offer_id'] != self.offer_id
                        or row['schema_version'] != SCHEMA or row['sequence'] != len(rows)+1):
                    raise ValueError('event binding')
                row['event_digest'] = seal
                rows.append(row)
                previous = seal
                if row['event'] == 'RESERVE':
                    if row['key'] in entries:
                        raise ValueError('duplicate reservation')
                    entries[row['key']] = dict(row['request'], key=row['key'], state='RESERVED',
                        invocation_id=row['invocation_id'], binding=row['binding'])
                elif row['event'] in {'ATTEMPTED', 'UNKNOWN', 'RECEIVED', 'CONFIRMED', 'FAILED', 'RECONCILED'}:
                    entry = entries[row['key']]
                    entry.update(row.get('details') or {})
                    entry['state'] = row['details']['resolved_state'] if row['event']=='RECONCILED' else row['event']
            if not rows or rows[0]['event'] != 'BASELINE':
                raise ValueError('missing baseline')
        except (ValueError, KeyError, TypeError) as error:
            raise PaidRequestBlocked('paid journal is damaged; preserve it and reconcile before any request') from error
        return rows, entries

    def _append(self, rows: list[dict], event: str, **fields: Any) -> dict:
        row = {'schema_version': SCHEMA, 'offer_id': self.offer_id, 'sequence': len(rows)+1,
               'previous': rows[-1]['event_digest'] if rows else None, 'event': event,
               'at': datetime.now(timezone.utc).isoformat(), **fields}
        row['event_digest'] = digest(row)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.path.open('ab') as handle:
            handle.write((json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)+'\n').encode('utf-8'))
            handle.flush()
            os.fsync(handle.fileno())
        rows.append(row)
        return row

    def _initialize(self, rows: list[dict]) -> None:
        if rows:
            return
        baseline = self.usage_baseline
        if baseline is None:
            baseline_path = self.directory / 'paid-usage-baseline.json'
            if not baseline_path.is_file():
                baseline = discover_usage_baseline(repo_root=self.directory.parents[2], offer_id=self.offer_id, directory=self.directory,policy=self.policy,round1=self.round1)
            else:
                baseline = _read(baseline_path)
        validated = validate_usage_baseline(baseline, offer_id=self.offer_id)
        self._append(rows, 'BASELINE', baseline=validated, baseline_digest=digest(validated))

    def ensure_ready(self) -> None:
        with business_lock(self.root, self.lock_key):
            rows, _ = self._load()
            self._initialize(rows)

    def _historical(self, rows: list[dict]) -> list[dict]:
        records=deepcopy(rows[0]['baseline']['records']) if rows else []
        for event in rows:
            if event['event']=='HISTORY_RECONCILED':records[event['index']].update(event['resolution'])
        return records

    def inspect_history(self, index: int) -> dict:
        with business_lock(self.root,self.lock_key):
            rows,_entries=self._load();records=self._historical(rows)
            if type(index) is not int or not 0<=index<len(records):raise PaidRequestBlocked('unknown historical request index')
            row=records[index]
            source=Path(row['source_path'])
            if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest()!=row['source_sha256']:
                raise PaidRequestBlocked('historical retained source bytes changed')
            return {'offer_id':self.offer_id,'index':index,'record':row,'ledger_digest':rows[-1]['event_digest'],
                    'last_event_at':rows[-1]['at'],'original_request':_pointer(_read(source),row['request_pointer'])}

    def reconcile_history(self, *, evidence: Mapping) -> dict:
        """Bind upstream reconciliation to one preserved historical request; never erase usage.

        A corresponding old image checkpoint still needs its own S02-A task/owner
        evidence. This operation only resolves the product-budget history blocker.
        """
        observed=evidence.get('observed')
        if not isinstance(observed,dict):raise PaidRequestBlocked('historical reconciliation requires the full current observation')
        with self.phase_lock():
            current=self.inspect_history(observed.get('index'))
            if current!=observed:raise PaidRequestBlocked('historical reconciliation is stale or belongs to another request')
            if current['record']['state'] not in {'UNKNOWN','ATTEMPTED'} and current['record']['provider']!='UNKNOWN':
                raise PaidRequestBlocked('historical request is already resolved')
            try:
                stamp=datetime.fromisoformat(evidence['verified_at'])
                if stamp.tzinfo is None or stamp>datetime.now(timezone.utc) or stamp<datetime.fromisoformat(current['last_event_at']):raise ValueError()
            except (KeyError,TypeError,ValueError) as error:raise PaidRequestBlocked('historical reconciliation verification time is invalid') from error
            if not evidence.get('verified_by') or not str(evidence.get('evidence_ref') or '').startswith(('audit://','approval://')):
                raise PaidRequestBlocked('historical reconciliation requires an upstream verifier and hashed evidence')
            _sha(evidence.get('evidence_sha256'))
            provider=evidence.get('provider')
            if provider not in {'lingshi','toapis'} or current['record']['provider'] not in {provider,'UNKNOWN'}:
                raise PaidRequestBlocked('historical reconciliation provider conflicts')
            original=current['original_request'];known=original.get('task_id') or original.get('provider_task_id')
            task=evidence.get('task_id');outcome=evidence.get('outcome')
            if outcome=='no_task_no_charge':
                if task is not None or known or evidence.get('charge_status')!='none':raise PaidRequestBlocked('historical no-charge conflicts with a known task')
                state='NO_CHARGE'
            elif outcome=='task_verified_for_request':
                if not task or (known and str(known)!=str(task)):raise PaidRequestBlocked('historical task identity conflicts')
                state='CONFIRMED'
            else:raise PaidRequestBlocked('unsupported historical reconciliation outcome')
            with business_lock(self.root,self.lock_key):
                rows,_entries=self._load()
                if rows[-1]['event_digest']!=current['ledger_digest']:raise PaidRequestBlocked('historical ledger advanced during reconciliation')
                self._append(rows,'HISTORY_RECONCILED',index=current['index'],original_record_digest=digest(current['record']),
                    resolution={'state':state,'provider':provider,'reconciled_task_id':task,'evidence_binding_digest':digest(dict(evidence))},
                    evidence_ref=evidence['evidence_ref'],evidence_sha256=evidence['evidence_sha256'],verified_at=evidence['verified_at'])
        return self.inspect_history(current['index'])

    def bind_plan(self, name: str, identity: Mapping) -> str:
        """Freeze a technical input contract; rebuilding it never alters round 1."""
        value = {'offer_id':self.offer_id, 'round1':self.binding['round1_snapshot_digest'], 'identity':dict(identity),
                 'purpose_models':self.policy['paid_models'].get('models_by_purpose'),
                 'allowed_purposes':self.policy['paid_models']['allowed_purposes']}
        value['digest'] = digest(value)
        path = self.root / ('plan-' + digest(name)[:16] + '.json')
        with business_lock(self.root, self.lock_key):
            if path.is_file():
                if _read(path) != value:
                    proposal={'name':name,'old_plan_digest':_read(path)['digest'],'new_plan':value}
                    proposal['digest']=digest(proposal)
                    proposal_path=self.root/f'proposal-{proposal["digest"][:24]}.json'
                    if proposal_path.exists() and _read(proposal_path)!=proposal:
                        raise PaidRequestBlocked('short technical proposal path collision')
                    atomic_json(proposal_path,proposal)
                    raise PaidRequestBlocked(f'TECHNICAL_PLAN_DRIFT: inspect and activate the bound local proposal {proposal_path}; no new paid request')
            else:
                atomic_json(path, value)
        return value['digest']

    def activate_plan_rebuild(self, proposal_path: str | Path) -> dict:
        """Explicit local technical rebuild; current approved facts and all paid events stay intact."""
        from modules.sourcing.image_generation_checkpoint import atomic_bytes
        proposal_path=Path(proposal_path).resolve()
        if proposal_path.parent!=self.root or not proposal_path.name.startswith('proposal-'):
            raise PaidRequestBlocked('technical proposal must belong to this product ledger')
        proposal=_read(proposal_path)
        seal=proposal.pop('digest',None)
        if seal!=digest(proposal):raise PaidRequestBlocked('technical proposal identity is damaged')
        name=proposal.get('name');value=proposal.get('new_plan')
        if name not in {'brand','translation'} or not isinstance(value,dict):raise PaidRequestBlocked('unsupported technical projection')
        unsigned=dict(value);value_seal=unsigned.pop('digest',None)
        if value_seal!=digest(unsigned) or value.get('offer_id')!=self.offer_id or value.get('round1')!=self.binding['round1_snapshot_digest']:
            raise PaidRequestBlocked('technical proposal does not bind the current approved input')
        if (value.get('allowed_purposes')!=self.policy['paid_models']['allowed_purposes']
                or value.get('purpose_models')!=self.policy['paid_models'].get('models_by_purpose')):
            raise PaidRequestBlocked('technical proposal purpose/model authorization changed')
        with self.phase_lock(),business_lock(self.root,self.lock_key):
            rows,entries=self._load();self._initialize(rows)
            if any(r['state'] in UNKNOWN for r in entries.values()) or any(r['state'] in {'UNKNOWN','ATTEMPTED'} for r in self._historical(rows)):
                raise PaidRequestBlocked('unresolved product request prevents technical rebuild')
            path=self.root/('plan-'+digest(name)[:16]+'.json')
            old=_read(path)
            rebuilds=[row for row in rows if row['event']=='TECHNICAL_REBUILD']
            prior=next((row for row in reversed(rebuilds) if row.get('proposal_digest')==seal and row.get('new_plan_digest')==value['digest']),None)
            if old==value and prior is not None:
                for row in prior['archived']:
                    archive=Path(row['archive'])
                    if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest()!=row['sha256']:
                        raise PaidRequestBlocked('technical rebuild archive is incomplete; preserve current artifacts for recovery')
                receipt={'offer_id':self.offer_id,'proposal_digest':seal,'new_plan_digest':value['digest'],
                         'archived':prior['archived'],'round3_invalidation_required':True,'paid_requests_preserved':True}
                atomic_json(self.root/'rebuild-receipts'/f'{seal[:24]}.json',receipt)
                if rebuilds[-1]['proposal_digest']==seal:
                    atomic_json(self.directory/'round2-technical-invalidation.json',receipt)
                return receipt  # Never delete current new artifacts or append another rebuild event.
            if old.get('digest')!=proposal['old_plan_digest']:
                raise PaidRequestBlocked('technical proposal is stale; preserve it and inspect a current proposal')
            # The SHA-addressed archive makes each local projection invalidation reversible.
            names=['brand-image-translation.json','brand-image-translation-plan.json','automated-image-qa.json',
                   'round2-image-snapshot.json','round2-approved-snapshot.json']
            if name=='brand':names+=['brand-image-generation.json']
            archived=[]
            for original in [path,*[self.directory/n for n in names]]:
                if not original.is_file():continue
                raw=original.read_bytes();sha=hashlib.sha256(raw).hexdigest()
                target=self.root/'technical-history'/f'{sha[:24]}.json'
                if target.exists() and target.read_bytes()!=raw:raise PaidRequestBlocked('technical history short path collision')
                atomic_bytes(target,raw)
                archived.append({'path':str(original),'archive':str(target),'sha256':sha})
            # Log before replacing projections. A crash remains fail-closed and its exact proposal is reusable.
            self._append(rows,'TECHNICAL_REBUILD',proposal_digest=seal,old_plan_digest=old['digest'],
                         new_plan_digest=value['digest'],binding=self.binding,archived=archived)
            for row in archived:
                original=Path(row['path'])
                if original!=path:original.unlink()
            atomic_json(path,value)
            receipt={'offer_id':self.offer_id,'proposal_digest':seal,'new_plan_digest':value['digest'],
                     'archived':archived,'round3_invalidation_required':True,'paid_requests_preserved':True}
            atomic_json(self.directory/'round2-technical-invalidation.json',receipt)
            atomic_json(self.root/'rebuild-receipts'/f'{seal[:24]}.json',receipt)
        return receipt

    def _key(self, *, purpose: str, business: Mapping, request: Mapping, attempt: int) -> tuple[str, dict]:
        if type(attempt) is not int or not 0 <= attempt <= 3:
            raise PaidRequestBlocked('paid attempt is outside the governed limit')
        identity = {'purpose': purpose, 'business': dict(business), 'request_digest': digest(dict(request)),
                    'attempt': attempt, 'provider': 'lingshi'}
        return digest(identity), identity

    def reserve(self, *, purpose: str, model: str, business: Mapping, request: Mapping, attempt: int = 0) -> str:
        self.authorize(purpose, model)
        key, identity = self._key(purpose=purpose, business=business, request=request, attempt=attempt)
        with business_lock(self.root, self.lock_key):
            rows, entries = self._load()
            self._initialize(rows)
            if key in entries:
                raise PaidRequestBlocked('an existing paid attempt may only replay its receipt or reconcile; never resend')
            if (self.root/f'raw-{key[:24]}.json').exists():
                self._raw(key)  # Also validates the complete key on a short-path collision.
                raise PaidRequestBlocked('a retained raw receipt without its reservation requires recovery')
            baseline = self._historical(rows)
            if any(row['provider'] == 'UNKNOWN' or row['state'] == 'UNKNOWN' for row in baseline):
                raise PaidRequestBlocked('HISTORICAL_UNKNOWN: historical usage includes an unresolved request; reconcile before new paid work')
            if any(row['business'] == identity['business'] and row['state'] in UNKNOWN for row in entries.values()):
                raise PaidRequestBlocked('same business has an unknown paid attempt; changing prompt cannot bypass it')
            occupied = len(baseline) + len(entries)
            if occupied >= self.cap:
                raise PaidRequestBlocked(f'PAID_BUDGET_EXHAUSTED: occupied {occupied}, cap {self.cap}; no provider call')
            self._append(rows, 'RESERVE', key=key, request={**identity, 'model':model},
                         invocation_id=self.invocation_id, binding=self.binding, cap=self.cap)
        return key

    def record(self, key: str, event: str, **details: Any) -> None:
        if event not in {'ATTEMPTED', 'UNKNOWN', 'RECEIVED', 'CONFIRMED', 'FAILED'}:
            raise ValueError('unsupported paid event')
        with business_lock(self.root, self.lock_key):
            rows, entries = self._load()
            if key not in entries:
                raise PaidRequestBlocked('paid event has no durable reservation')
            current = entries[key]
            if current['state']=='CONFIRMED' and event=='RECEIVED':
                if details.get('task_id') not in (None,current.get('task_id')):
                    raise PaidRequestBlocked('received task conflicts with confirmed receipt')
                return
            if current['state'] == event and all(current.get(k) == v for k,v in details.items()):
                return
            self._append(rows, event, key=key, details=details)

    def entry(self, key: str) -> dict | None:
        with business_lock(self.root, self.lock_key):
            _, entries = self._load()
            return deepcopy(entries.get(key))

    def _raw(self, key: str) -> dict | None:
        path = self.root / f'raw-{key[:24]}.json'
        if not path.is_file():
            return None
        value = _read(path)
        seal = value.pop('digest', None)
        if (seal != digest(value) or value.get('key') != key or value.get('offer_id') != self.offer_id
                or not isinstance(value.get('response'), dict)):
            raise PaidRequestBlocked('paid raw receipt binding is invalid')
        return value['response']

    def receipt_binding(self, key: str) -> dict:
        entry=self.entry(key)
        if entry is None:raise PaidRequestBlocked('receipt has no paid ledger identity')
        raw=self._raw(key)
        return {'key':key,'offer_id':self.offer_id,'approval_binding':entry['binding'],
                'raw_digest':digest(raw) if raw else None,'ledger_path':str(self.path)}

    def validate_receipt_binding(self, binding: Mapping, *, purpose: str, business: Mapping, model: str) -> dict:
        if not isinstance(binding,Mapping):raise PaidRequestBlocked('receipt lacks its paid ledger binding')
        key=_sha(binding.get('key'));entry=self.entry(key)
        if (dict(binding)!=self.receipt_binding(key) or not entry or entry['purpose']!=purpose
                or entry['business']!=dict(business) or entry['model']!=model or entry['state'] not in {'RECEIVED','CONFIRMED'}):
            raise PaidRequestBlocked('receipt belongs to a different paid request or unresolved outcome')
        return entry

    def invoke(self, key: str, call: Callable[[], Mapping[str, Any]]) -> dict:
        with business_lock(self.root,self.lock_key):
            rows,entries=self._load()
            if key not in entries or entries[key]['state']!='RESERVED':
                raise PaidRequestBlocked('a paid attempt cannot enter provider twice')
            self._append(rows,'ATTEMPTED',key=key,details={})
        try:
            response = dict(call())
        except Exception:
            self.record(key, 'UNKNOWN')
            raise
        raw = {'offer_id':self.offer_id, 'key':key, 'response':response}
        raw['digest'] = digest(raw)
        # Raw is durable before parsing content or acknowledging into the projection.
        atomic_json(self.root / f'raw-{key[:24]}.json', raw)
        self.record(key, 'RECEIVED', raw_digest=digest(response), usage=response.get('usage'))
        return response

    def chat(self, *, purpose: str, model: str, messages: list, business: Mapping,
             call: Callable[[], Mapping[str, Any]], parameters: Mapping | None = None, attempt: int = 0) -> dict:
        self.authorize(purpose, model)
        request = {'model':model, 'messages':messages, 'parameters':dict(parameters or {}), 'round1':self.binding['round1_snapshot_digest']}
        while True:
            key, _ = self._key(purpose=purpose, business=business, request=request, attempt=attempt)
            with business_lock(self.root, digest({'scope':'chat', 'business':dict(business)})):
                existing = self.entry(key)
                if existing is not None:
                    if existing['state']=='FAILED_REFUNDED':
                        raise PaidRequestBlocked('known provider task failed and refunded; original attempt remains occupied, explicit bounded retry required')
                    if existing['state']=='NO_CHARGE':
                        attempt+=1
                        continue
                    raw = self._raw(key)
                    if raw is None:
                        raise PaidRequestBlocked('chat outcome is unknown; recover the bound raw response, never resend')
                    self.record(key, 'RECEIVED', raw_digest=digest(raw), usage=raw.get('usage'))
                    return {**raw,'_paid_request':self.receipt_binding(key)}
                key = self.reserve(purpose=purpose, model=model, business=business, request=request, attempt=attempt)
                response=self.invoke(key, call)
                return {**response,'_paid_request':self.receipt_binding(key)}

    def image_key(self, checkpoint, state: Mapping, purpose: str) -> str:
        return self._key(purpose=purpose, business={'kind':checkpoint.kind, 'business_digest':checkpoint.business_digest},
                         request={'checkpoint_request_digest':checkpoint.request_digest}, attempt=state['attempt'])[0]

    def image_reservation(self, checkpoint, state: Mapping, purpose: str) -> str:
        return self.reserve(purpose=purpose, model=checkpoint.model, business={'kind':checkpoint.kind, 'business_digest':checkpoint.business_digest},
                            request={'checkpoint_request_digest':checkpoint.request_digest}, attempt=state['attempt'])

    def image_recovery(self, checkpoint, state: Mapping, purpose: str) -> int | None:
        key = self.image_key(checkpoint, state, purpose)
        entry = self.entry(key)
        if entry is None:
            return None
        raw = self._raw(key)
        task = (raw.get('data') or {}).get('task_id') if raw else None
        if isinstance(task, str) and task.isdecimal():
            task = int(task)
        if type(task) is int and task > 0:
            if state.get('task_id') not in (None, task):
                raise PaidRequestBlocked('ledger and checkpoint disagree about the provider task')
            self.record(key, 'RECEIVED', task_id=task, raw_digest=digest(raw))
            return task
        if not state.get('task_id'):
            raise PaidRequestBlocked('image reservation/attempt has unknown outcome; never resend')
        return None

    def image_completed(self, checkpoint, state: Mapping, purpose: str, receipt: Mapping) -> None:
        key = self.image_key(checkpoint, state, purpose)
        if self.entry(key) is not None:
            self.record(key, 'CONFIRMED', task_id=receipt['task_id'], output_digest=receipt['output_digest'],
                         cost=receipt.get('cost'), receipt_digest=digest(dict(receipt)))

    def image_resume_attempt(self, checkpoint, state: Mapping, purpose: str, requested: int) -> int:
        if requested==state['attempt'] or state['attempt']==0:return requested
        prior=self.entry(self.image_key(checkpoint,{'attempt':state['attempt']-1},purpose))
        audit=state.get('reconciliation') or {}
        if (requested==0 and prior and prior['state']=='NO_CHARGE' and audit.get('outcome')=='no_task_no_charge'
                and audit.get('evidence_binding_digest')==prior.get('checkpoint_evidence_digest')):
            return state['attempt']
        return requested

    def inspect_request(self, key: str, *, checkpoint_path: str | Path | None = None) -> dict:
        """Read the exact local bindings to include in upstream-verified evidence."""
        key=_sha(key)
        with business_lock(self.root,self.lock_key):
            rows,entries=self._load()
            if key not in entries:raise PaidRequestBlocked('unknown paid request key')
            entry=entries[key]
            raw=self._raw(key)
            result={'offer_id':self.offer_id,'key':key,'entry':deepcopy(entry),
                    'ledger_digest':rows[-1]['event_digest'],'raw_digest':digest(raw) if raw else None,
                    'last_event_at':rows[-1]['at']}
        if checkpoint_path is not None:
            from modules.sourcing.image_generation_checkpoint import ImageCheckpoint,inspect_image_checkpoint
            path=Path(checkpoint_path).resolve()
            if not path.is_relative_to(self.directory):raise PaidRequestBlocked('checkpoint is outside this product report root')
            cp=ImageCheckpoint.from_path(path)
            observed=inspect_image_checkpoint(path)
            if self.image_key(cp,{'attempt':entry['attempt']},entry['purpose'])!=key:
                raise PaidRequestBlocked('checkpoint does not match the paid request identity')
            result.update(checkpoint_path=str(path),checkpoint=observed)
        return result

    def reconcile_request(self, *, evidence: Mapping) -> dict:
        """Apply already verified external facts; this local interface verifies only bindings.

        No-charge resolution retains the occupied slot conservatively. It permits
        the next bounded attempt; it never changes a successful charge into zero.
        Image checkpoint reconciliation precedes the final ledger projection. If
        interrupted there, the identical evidence resumes using its exact audit
        binding, without a provider call or accepting unrelated stale evidence.
        """
        from modules.sourcing.image_generation_checkpoint import inspect_image_checkpoint,reconcile_image_checkpoint
        supplied=deepcopy(dict(evidence))
        observed=supplied.get('observed')
        if not isinstance(observed,dict):raise PaidRequestBlocked('paid reconciliation requires the complete observed binding')
        key=_sha(observed.get('key'))
        outcome=supplied.get('outcome')
        if outcome not in {'task_verified_for_request','no_task_no_charge','raw_response_verified_for_request','failed_task_refunded'}:
            raise PaidRequestBlocked('unsupported paid reconciliation outcome')
        try:
            stamp=datetime.fromisoformat(supplied['verified_at'])
            if stamp.tzinfo is None or stamp>datetime.now(timezone.utc) or stamp<datetime.fromisoformat(observed['last_event_at']):raise ValueError()
        except (KeyError,TypeError,ValueError) as error:raise PaidRequestBlocked('paid verification time does not bind current evidence') from error
        if not str(supplied.get('verified_by') or '').strip() or not str(supplied.get('evidence_ref') or '').startswith(('audit://','approval://')):
            raise PaidRequestBlocked('paid reconciliation requires the upstream verifier and evidence reference')
        _sha(supplied.get('evidence_sha256'))
        with self.phase_lock():
            current=self.inspect_request(key,checkpoint_path=observed.get('checkpoint_path'))
            if (outcome=='failed_task_refunded' and current['entry']['state']=='FAILED_REFUNDED'
                    and current['entry'].get('reconciliation_digest')==digest(supplied)):
                return current
            if current!=observed:
                old_cp=observed.get('checkpoint')
                new_cp=current.get('checkpoint')
                from modules.sourcing.image_generation_checkpoint import ImageCheckpoint
                audit=(ImageCheckpoint.from_path(observed['checkpoint_path']).read().get('reconciliation') or {}) if new_cp else {}
                # Checkpoint advancement alone may be the completed first half of this exact local operation.
                cp_evidence=supplied.get('checkpoint_evidence')
                resumable=(old_cp and cp_evidence and current['entry']==observed['entry']
                    and current['ledger_digest']==observed['ledger_digest'] and current['raw_digest']==observed['raw_digest']
                    and audit.get('evidence_binding_digest')==digest(cp_evidence))
                if not resumable:raise PaidRequestBlocked('paid reconciliation evidence is stale or belongs to another request')
            if observed['entry']['state'] not in UNKNOWN:
                raise PaidRequestBlocked('only unresolved paid requests can be externally reconciled')
            raw=self._raw(key)
            refund_details={}
            if outcome=='no_task_no_charge':
                if supplied.get('task_id') is not None or supplied.get('charge_status')!='none' or raw is not None or observed['entry'].get('task_id'):
                    raise PaidRequestBlocked('no-task/no-charge contradicts a known response or task')
                resolved='NO_CHARGE'
            elif outcome=='failed_task_refunded':
                from decimal import Decimal, InvalidOperation
                response=supplied.get('provider_response');task=supplied.get('task_id')
                if (observed.get('checkpoint') or raw is not None or type(task) is not int or task<=0
                        or not isinstance(response,dict) or response.get('task_id')!=task
                        or type(response.get('task_id')) is not int
                        or response.get('model')!=observed['entry'].get('model')
                        or observed['entry'].get('task_id') not in (None,task)
                        or response.get('state')!='failed' or response.get('is_final') is not True
                        or response.get('refunded') is not True or response.get('result_url')
                        or response.get('result_urls') or response.get('result_type')
                        or digest(response)!=_sha(supplied.get('provider_response_sha256'))):
                    raise PaidRequestBlocked('failed-refunded task evidence conflicts with the exact unresolved chat request')
                try:
                    cost=Decimal(str(response['cost']));refunded=Decimal(str(response['refunded_amount']))
                    completed=datetime.fromisoformat(response['completed_at'])
                    if (not cost.is_finite() or cost!=0 or not refunded.is_finite() or refunded<=0
                            or completed.tzinfo is None or completed>stamp
                            or completed<datetime.fromisoformat(observed['last_event_at'])):raise ValueError()
                except (KeyError,TypeError,ValueError,InvalidOperation) as error:
                    raise PaidRequestBlocked('failed-refunded task needs final zero cost, positive refund and a bound completion time') from error
                resolved='FAILED_REFUNDED'
                refund_details={'provider_response':deepcopy(response),'provider_response_sha256':digest(response),
                    'refunded':True,'refunded_amount':str(refunded),'cost':str(cost),'occupied_slot_retained':True}
            elif outcome=='task_verified_for_request':
                if type(supplied.get('task_id')) is not int or supplied['task_id']<=0 or not observed.get('checkpoint'):
                    raise PaidRequestBlocked('verified image recovery requires an exact positive task and checkpoint')
                resolved='RECEIVED'
            else:
                if observed.get('checkpoint') or not isinstance(supplied.get('response'),dict):
                    raise PaidRequestBlocked('chat recovery requires the exact original raw response')
                if digest(supplied['response'])!=_sha(supplied.get('response_sha256')):
                    raise PaidRequestBlocked('verified response digest differs')
                if raw is not None and raw!=supplied['response']:
                    raise PaidRequestBlocked('verified response conflicts with retained original raw')
                resolved='RECEIVED'
            if observed.get('checkpoint'):
                cp_evidence=supplied.get('checkpoint_evidence')
                if not isinstance(cp_evidence,dict) or cp_evidence.get('outcome')!=outcome or cp_evidence.get('task_id')!=supplied.get('task_id'):
                    raise PaidRequestBlocked('image recovery requires the matching checkpoint evidence contract')
                if current==observed:reconcile_image_checkpoint(observed['checkpoint_path'],evidence=cp_evidence)
            if outcome=='raw_response_verified_for_request':
                envelope={'offer_id':self.offer_id,'key':key,'response':supplied['response']}
                envelope['digest']=digest(envelope)
                atomic_json(self.root/f'raw-{key[:24]}.json',envelope)
            with business_lock(self.root,self.lock_key):
                rows,entries=self._load()
                if rows[-1]['event_digest']!=observed['ledger_digest']:
                    raise PaidRequestBlocked('ledger advanced during reconciliation; inspect current evidence before resuming')
                self._append(rows,'RECONCILED',key=key,details={'resolved_state':resolved,
                    **refund_details,
                    'task_id':supplied.get('task_id'),'reconciliation_digest':digest(supplied),
                    'checkpoint_evidence_digest':digest(supplied['checkpoint_evidence']) if supplied.get('checkpoint_evidence') else None,
                    'evidence_ref':supplied['evidence_ref'],'evidence_sha256':supplied['evidence_sha256'],
                    'verified_at':supplied['verified_at'],'external_facts_verified_by_upstream':supplied['verified_by']})
        return self.inspect_request(key,checkpoint_path=observed.get('checkpoint_path'))

    def summary(self, *, planned: int | None = None) -> dict:
        with business_lock(self.root, self.lock_key):
            rows, entries = self._load()
        historical = self._historical(rows)
        states = list(entries.values())
        return {'schema_version':SCHEMA, 'offer_id':self.offer_id, 'planned':planned if planned is not None else getattr(self,'planned_requests',None),
                'planned_scope':'CURRENT_PHASE_APPROVED_REQUESTS','budget_scope':'ALL_PRODUCT_IN_POLICY_PAID_PURPOSES','cap':self.cap,
                'occupied':len(historical)+len(states), 'attempted':sum(r['state']!='RESERVED' for r in states)+len(historical),
                'unknown':sum(r['state'] in UNKNOWN for r in states)+sum(r['state'] in {'UNKNOWN','ATTEMPTED'} for r in historical),
                'confirmed':sum(r['state'] in {'RECEIVED','CONFIRMED','FAILED','FAILED_REFUNDED'} for r in states)+sum(r['state'] in {'CONFIRMED','FAILED'} for r in historical),
                'new_this_invocation':sum(r['invocation_id']==self.invocation_id and r['state']!='RESERVED' for r in states),
                'reserved_this_invocation':sum(r['invocation_id']==self.invocation_id for r in states),
                'historical_occupied':len(historical), 'approval_binding':self.binding,
                'reconciled_no_charge_retained':sum(r['state']=='NO_CHARGE' for r in states),
                'failed_refunded_retained':sum(r['state']=='FAILED_REFUNDED' for r in states),
                'by_purpose':{purpose:sum(r['purpose']==purpose for r in states) for purpose in sorted({r['purpose'] for r in states})},
                'usage_facts':[{'key':r['key'],'usage':r.get('usage'),'cost':r.get('cost')} for r in states if r.get('usage') is not None or r.get('cost') is not None],
                'ledger_path':str(self.path), 'ledger_digest':rows[-1]['event_digest'] if rows else None}


def require_paid_context(value: Any) -> PaidRequestContext:
    if not isinstance(value, PaidRequestContext):
        raise PaidRequestBlocked('PAID_CONTEXT_REQUIRED: use the approved R2 entry; this UI consumer awaits immutable-input/budget wiring')
    return value


def legacy_consumer_binding(context: PaidRequestContext, *, offer_id: str, bridge: Mapping | None,
                            source_url: str, locale: str | None = None) -> dict:
    """An adapter consumes existing approved scope; it never creates a new approval."""
    require_paid_context(context)
    if (not isinstance(bridge,Mapping) or bridge.get('offer_id')!=offer_id or context.offer_id!=offer_id
            or bridge.get('round1_snapshot_digest')!=context.round1['snapshot_digest']):
        raise PaidRequestBlocked('LEGACY_R2_BRIDGE_REQUIRED: bind the existing approved input before this consumer can call Lingshi')
    roles={(b['id'],r['role']) for b in context.round1['image_plan'].get('brand_plans') or [] for r in b.get('generated_assets') or []}
    rows=[r for r in bridge.get('tasks') or [] if r.get('source_url')==source_url and (locale is None or r.get('locale')==locale)]
    if not rows or len({(r.get('brand_id'),r.get('role')) for r in rows})!=1:
        raise PaidRequestBlocked('legacy source requires one exact approved brand/role binding')
    row=rows[0]
    if (row.get('brand_id'),row.get('role')) not in roles or not row.get('source_digest'):
        raise PaidRequestBlocked('legacy image role/source is outside the frozen round-1 plan')
    _sha(row['source_digest'])
    if locale is not None:
        countries={'ms-MY':'MY','th-TH':'TH','vi-VN':'VN','ru-RU':'RU','es-MX':'MX'}
        country=countries.get(locale)
        brand_prefix='HB_' if str(row['brand_id']).startswith('homebloom') else 'LH_'
        targets=context.round1['canonical_targets']
        allowed=any(target.endswith(':'+country) or target.endswith(':'+brand_prefix+country) for target in targets) if country else False
        if not allowed:raise PaidRequestBlocked('legacy locale is outside the frozen target scope')
    result={'offer_id':offer_id,'brand_id':row['brand_id'],'role':row['role']}
    result.update({'locale':locale} if locale else {'phase':'translation-text','source_digest':row['source_digest']})
    return result


def load_paid_context(*, offer_id: str, round1: Mapping, repo_root: Path, policy_path: Path | None = None,
                      usage_baseline_path: Path | None = None) -> PaidRequestContext:
    from shared_platform.publication_autopilot import load_autopilot_policy
    policy = load_autopilot_policy(policy_path or Path(repo_root)/'config/product_publication_autopilot_policy.json')
    baseline = _read(usage_baseline_path) if usage_baseline_path else None
    return PaidRequestContext(offer_id=offer_id, round1=round1, policy=policy,
                              reports_root=Path(repo_root)/'reports/product-preparation', usage_baseline=baseline)


def main(argv=None) -> int:
    """Local status/reconciliation/rebuild only. This CLI has no provider factory."""
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root',type=Path,required=True)
    parser.add_argument('--offer-id',required=True)
    parser.add_argument('--paid-policy',type=Path)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('status')
    inspect=commands.add_parser('inspect-request');inspect.add_argument('--key',required=True);inspect.add_argument('--checkpoint',type=Path)
    reconcile=commands.add_parser('reconcile-request');reconcile.add_argument('--evidence',type=Path,required=True)
    history=commands.add_parser('inspect-history');history.add_argument('--index',type=int,required=True)
    reconcile_history=commands.add_parser('reconcile-history');reconcile_history.add_argument('--evidence',type=Path,required=True)
    rebuild=commands.add_parser('activate-plan-rebuild');rebuild.add_argument('--proposal',type=Path,required=True)
    args=parser.parse_args(argv)
    snapshot=_read(args.repo_root/'reports/product-preparation'/args.offer_id/'round1-approved-snapshot.json')
    context=load_paid_context(offer_id=args.offer_id,round1=snapshot,repo_root=args.repo_root,policy_path=args.paid_policy)
    if args.command=='status':value=context.summary()
    elif args.command=='inspect-request':value=context.inspect_request(args.key,checkpoint_path=args.checkpoint)
    elif args.command=='reconcile-request':value=context.reconcile_request(evidence=_read(args.evidence))
    elif args.command=='inspect-history':value=context.inspect_history(args.index)
    elif args.command=='reconcile-history':value=context.reconcile_history(evidence=_read(args.evidence))
    else:value=context.activate_plan_rebuild(args.proposal)
    print(json.dumps(value,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
