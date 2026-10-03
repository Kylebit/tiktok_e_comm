"""Recompute declared source hashes from an explicit combined Git candidate.

This is a source-maintenance command, never a package repair or approval tool.
Historical retained-source evidence and unavailable capability stages survive.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from shared_platform.capability_runtime import checked_path, digest, file_content, tree_files
from scripts.package_agent_tools import manifest

# These stages explicitly make no executable-package claim. Unknown stages fail
# closed when files are missing; portable entries are never covered by this set.
UNAVAILABLE_STAGES = frozenset({
    'WORKFLOW_RUNTIME_REQUIRED', 'PENDING_B4B_COMPOSITION', 'HOST_IMAGEGEN_TEMPLATE',
    'EXTERNAL_PLUGIN', 'REFERENCE_ONLY', 'PRESERVED_DRAFT', 'STUB_NOT_IMPLEMENTED',
    'HOST_DISCOVERY_REQUIRED', 'LEGACY_SDK_REQUIRES_REVIEW', 'OPTIONAL_NOT_STARTED', 'RETIRED',
})


def refresh(root: Path, basis: str, *, write=False):
    head=subprocess.check_output(['git','-c','safe.directory='+str(root),'-C',str(root),'rev-parse','HEAD'],text=True).strip()
    if basis != head: raise ValueError('source basis must be the exact current Git HEAD')
    path=checked_path(root,'config/capability_catalog.json')
    value=json.loads(file_content(path))
    # Check the sealed content with its recorded generation checkpoint. A normal
    # commit changes HEAD, not the content hashes or the historical checkpoint.
    generation_basis=basis if write else value.get('source_basis_commit','')
    if not re.fullmatch(r'[0-9a-f]{40}',generation_basis):
        raise ValueError('recorded source basis must be a full Git commit')
    subprocess.check_call(['git','-c','safe.directory='+str(root),'-C',str(root),
                           'merge-base','--is-ancestor',generation_basis,head])
    value['source_basis_commit']=generation_basis
    value['package_version']='orbit-portable-tools/6'
    rows=value['skills']+value['tools']
    missing={}
    for row in rows:
        files={}
        if row in value['skills'] and row.get('source_path'):
            # The installer compares this map to the complete Skill tree only;
            # runtime dependencies belong to required_files, not its parity map.
            source=checked_path(root,row['source_path'])
            names=[p.relative_to(root).as_posix() for p in tree_files(source)]
        else:
            names=sorted(set(row.get('required_files',[])) | set(row.get('file_digests',{})))
        for name in names:
            candidate=checked_path(root,name)
            if candidate.is_file(): files[name]=hashlib.sha256(file_content(candidate)).hexdigest()
            else: missing.setdefault(row['id'],[]).append(name)
        for name in row.get('required_files',[]):
            if not checked_path(root,name).is_file() and name not in missing.get(row['id'],[]):
                missing.setdefault(row['id'],[]).append(name)
        row['file_digests']=files
        # Keep original source identity, including external preserved hashes.
        row['source']['composition_basis_commit']=generation_basis
        row['source']['composed_file_digest']=digest(files)
        row['source']['final_commit_binding']='composition basis plus generated current file_digests; historical source fields are provenance only'
    encoded=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    catalog_changed=file_content(path)!=encoded
    unavailable={r['id']:{'stage':r['stage'],'missing_files':missing[r['id']]}
                 for r in rows if r['id'] in missing and r['stage'] in UNAVAILABLE_STAGES}
    blocking={key:files for key,files in missing.items() if key not in unavailable}
    workflow_runtime_blockers={key:row for key,row in unavailable.items()
                               if row['stage']=='WORKFLOW_RUNTIME_REQUIRED'}
    result={'ok':False,'mode':'write' if write else 'check','checked_head':head,
        'basis':generation_basis,'catalog_changed':catalog_changed,'missing_declared_files':missing,
        'unavailable_capabilities':unavailable,'blocking_missing_files':blocking,
        'workflow_runtime_ready':not workflow_runtime_blockers,
        'workflow_runtime_blockers':workflow_runtime_blockers,
        'scope':'source manifest consistency; unavailable stages are not executable or healthy capabilities',
        'network_call_performed':False}
    if blocking: return result
    # Preflight every portable allowlist file before either generated file is
    # written, including requirements that a catalog row forgot to declare.
    manifest(root)
    if write: path.write_bytes(encoded)
    if catalog_changed and not write:
        return {**result,'next_action':'review candidate content changes, then explicitly --write'}
    runtime=manifest(root)
    runtime_path=checked_path(root,'config/tool_runtime_manifest.json')
    runtime_changed=json.loads(file_content(runtime_path))!=runtime
    if write: runtime_path.write_text(json.dumps(runtime,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return {**result,'ok':write or not runtime_changed,'runtime_changed':runtime_changed,
        'runtime_digest':runtime['digest']}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root',type=Path,required=True)
    parser.add_argument('--source-basis-commit',required=True)
    parser.add_argument('--write',action='store_true')
    args=parser.parse_args(argv)
    try:
        if checked_path(args.runtime_root, '.').resolve()!=ROOT: raise ValueError('runtime-root must match this source launcher')
        result=refresh(ROOT,args.source_basis_commit,write=args.write)
        print(json.dumps(result,ensure_ascii=False)); return 0 if result['ok'] else 1
    except (OSError,ValueError,subprocess.CalledProcessError) as error:
        print(json.dumps({'ok':False,'error':str(error)})); return 2


if __name__=='__main__':raise SystemExit(main())
