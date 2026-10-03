"""Project the accepted v2 index into local read-only knowledge-page metadata."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

SCHEMA = 'orbit-knowledge-iteration/v1'
INDEX_SCHEMA = 'orbit-knowledge-readonly-index/v2'
PROJECT_ROOT = Path(__file__).absolute().parents[2]

def validate_output(guides: Path, index_path: Path):
    """Fail before any write, independently of projection/schema success."""
    raw=guides.absolute()
    expected=PROJECT_ROOT/'web/static/knowledge_guides.json'
    if '..' in raw.parts:
        raise ValueError('Output traversal is not allowed')
    for path in (raw,*raw.parents,expected,*expected.parents):
        if path.is_symlink() or (path.exists() and getattr(os.lstat(path),'st_file_attributes',0)&0x400):
            raise ValueError('Output symlinks and reparse points are not allowed')
    if raw.resolve()!=expected.resolve():
        raise ValueError('Output must be this project web/static/knowledge_guides.json')
    try:
        declared=json.loads(index_path.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        declared={}
    # A malformed schema cannot discard an independently declared Vault boundary.
    if isinstance(declared,dict) and declared.get('vault_root'):
        if not isinstance(declared['vault_root'],str):
            raise ValueError('Invalid Vault boundary')
        if raw.resolve().is_relative_to(Path(declared['vault_root']).resolve()):
            raise ValueError('Output must be outside the declared Vault')
    return raw

def digest(path):
    return 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()

def safe_note(vault, relative):
    target = (vault / relative).resolve()
    if not target.is_relative_to(vault.resolve()):
        raise ValueError('Index note must stay inside the declared Vault')
    return target

def project(index_path: Path, candidate_path: Path):
    result = {'schema': SCHEMA, 'projected_at': datetime.now(timezone.utc).isoformat(),
              'index_path': str(index_path.resolve()), 'candidate_manifest_path': str(candidate_path.resolve()),
              'available': False, 'candidate': None, 'execution_authority': False}
    try:
        index = json.loads(index_path.read_text(encoding='utf-8'))
        if index['schema_version'] != INDEX_SCHEMA or index.get('read_only') is not True:
            raise ValueError('Unsupported index')
        datetime.strptime(index['audit_date'], '%Y-%m-%d')
        vault = Path(index['vault_root'])
        rows = index['documents']
        if not isinstance(rows, list):
            raise ValueError('Invalid documents')
        states = Counter()
        for row in rows:
            note = safe_note(vault, row['path'])
            states['missing' if not note.is_file() else 'changed' if digest(note) != row['content_digest'] else 'unchanged'] += 1
        result.update(available=True, index_digest=digest(index_path), vault_path=str(vault),
                      audit_date=index['audit_date'], indexed_at=index['generated_at_utc'],
                      notes=len(rows), by_kind=dict(Counter(r['kind'] for r in rows)),
                      by_freshness=dict(Counter(r['freshness'] for r in rows)), source_check=dict(states))
    except (OSError, ValueError, KeyError, TypeError):
        result['problem'] = '索引尚未提供或格式无效，请先生成只读索引。'
    try:
        candidate = json.loads(candidate_path.read_text(encoding='utf-8'))
        if candidate['schema_version'] != 'orbit-knowledge-candidate-manifest/v1' or candidate['status'] != 'CANDIDATE_NOT_REVIEWED' or candidate.get('current_execution_authority') is not False:
            raise ValueError('Only unreviewed candidate projection is supported')
        note = safe_note(Path(candidate['candidate_vault']), candidate['candidate_path'])
        source_rows=[]
        for row in candidate['source_evidence']:
            path=Path(row['path'])
            source_rows.append({'path':str(path),'digest':row['digest'],
                                'matches':path.is_file() and digest(path)==row['digest']})
        matches=note.is_file() and digest(note)==candidate['candidate_digest']
        learnings=[]
        if matches:
            text=note.read_text(encoding='utf-8')
            if '## 可复用经验候选\n' in text:
                section=text.split('## 可复用经验候选\n',1)[1].split('\n## ',1)[0]
                learnings=[line.strip()[:500] for line in section.splitlines() if line.strip()][:4]
        result['candidate']={'title':note.stem, 'path':str(note), 'digest':candidate['candidate_digest'],
            'matches':matches, 'learnings':learnings,
            'observed_at':candidate['source_observed_at'],'verified_commit':candidate['verified_head'],
            'sources':source_rows, 'reviewed':False}
    except (OSError,ValueError,KeyError,TypeError):
        result['candidate_problem']='暂无可核对的未评审经验候选。'
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--index',required=True,type=Path)
    p.add_argument('--candidate',required=True,type=Path)
    p.add_argument('--guides',required=True,type=Path)
    args=p.parse_args()
    try:
        output=validate_output(args.guides,args.index)
    except ValueError as exc:
        raise SystemExit(str(exc))
    existing=json.loads(args.guides.read_text(encoding='utf-8'))
    if existing.get('schema')!='orbit-knowledge-guides/v1':
        raise SystemExit('Expected existing knowledge guides; nothing written')
    projection=project(args.index,args.candidate)
    existing['iteration']=projection
    output.write_text(json.dumps(existing,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'available':projection['available'],'notes':projection.get('notes'), 'source_check':projection.get('source_check'),'vault_writes':0},ensure_ascii=False))

if __name__=='__main__':
    main()
