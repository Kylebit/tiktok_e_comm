"""Agent-facing task CLI; uses the same durable ledger and receipt boundaries."""
import argparse
import json
import sys
import os
import subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from shared_platform.operations_runtime import RuntimeProfile
from shared_platform.workbench_engine import WorkbenchEngine


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True, type=Path)
    sub=parser.add_subparsers(dest='command', required=True)
    create=sub.add_parser('create');create.add_argument('--template',choices=['publication','delisting','profit'],required=True);create.add_argument('--scope-json',type=Path,required=True);create.add_argument('--source-key',required=True);create.add_argument('--title')
    status=sub.add_parser('status');status.add_argument('--task')
    sub.add_parser('install-stable-profile')
    attach=sub.add_parser('attach');attach.add_argument('--task',required=True);attach.add_argument('--thread',required=True);attach.add_argument('--owner',required=True);attach.add_argument('--observed-at',required=True);attach.add_argument('--observed-status',required=True);attach.add_argument('--evidence-ref',default='')
    args=parser.parse_args()
    profile=RuntimeProfile.capture(ROOT)
    e=WorkbenchEngine(args.data_root.resolve()/'tasks.db',{'code_version':profile.version,'environment':profile.environment,'manifest_digest':profile.manifest_digest})
    if args.command=='install-stable-profile':
        if profile.environment != 'stable':
            raise ValueError('only a stable deployment can install the shared profile')
        dirty=subprocess.run(['git','status','--porcelain'],cwd=ROOT,capture_output=True,text=True,check=True).stdout
        if dirty.strip():
            raise ValueError('stable profile requires a clean committed candidate')
        pointer=Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'OrbitHive/operations-runtime.json'
        pointer.parent.mkdir(parents=True,exist_ok=True)
        value={'schema_version':'orbit-operations-profile/v1','environment':'stable','data_root':str(args.data_root.resolve()),'source_root':str(ROOT),'release_identity':e.release}
        temporary=pointer.with_suffix('.tmp-'+str(os.getpid()))
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,pointer)
        result={'installed':str(pointer),'release_identity':e.release}
    elif args.command=='create':
        result=e.create({'template':args.template,'scope':json.loads(args.scope_json.read_text(encoding='utf-8')),'source_key':args.source_key,'title':args.title})
    elif args.command=='attach':
        result=e.attach_external(args.task,external_id=args.thread,owner=args.owner,observed_at=args.observed_at,observed_status=args.observed_status,evidence_ref=args.evidence_ref)
    else: result=e.get(args.task) if args.task else e.dashboard()
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
