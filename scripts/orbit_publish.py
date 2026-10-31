"""One source/config/data-bound Agent CLI over the original publication path.

No synthetic approval, provider fallback, automatic retries, schema creation or
service startup. prepare admits an explicit new task to the existing R1/R2/
COMMON worker. review/execute retain that same task and sole final decision.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import argparse,hashlib,json,os
from urllib.parse import urlencode,quote,urlsplit
from urllib.request import Request,urlopen,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError
from shared_platform.publication_execution_package import (
    ExecutionPackageError,check_execution_profile,compile_publication_execution_package,
    validate_publication_execution_package,read_json_ref,render_package_review,digest,local_path)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ExecutionPackageError('RUNTIME_REDIRECT_FORBIDDEN')


def request_json(base,path,body=None):
    raw=None if body is None else json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()
    request=Request(base.rstrip('/')+path,data=raw,headers={'Content-Type':'application/json','Origin':base.rstrip('/')},
                    method='GET' if raw is None else 'POST')
    try:
        response=build_opener(_NoRedirect).open(request,timeout=30)
    except HTTPError as response:
        return response.code,json.loads(response.read(8*1024*1024))
    with response:
        return response.status,json.loads(response.read(8*1024*1024))


def verify_runtime(bound,request=request_json):
    base=bound['runtime']['base_url']
    code,health=request(base,'/api/health')
    if (code!=200 or health.get('root')!=bound['source_root']
            or health.get('commit')!=bound['expected_source_head']
            or (health.get('profile') or {}).get('settings_path')!=bound['settings_path']):
        raise ExecutionPackageError('EXECUTION_RUNTIME_SOURCE_CONFIG_CHANGED')
    code,runtime=request(base,'/api/orbit/home/runtime')
    if (code!=200 or runtime.get('version')!=bound['expected_source_head']
            or runtime.get('manifest_digest')!=bound['source_identity']['manifest_digest']
            or Path(runtime.get('data_root','')).resolve()!=Path(bound['data_root']).resolve()):
        raise ExecutionPackageError('EXECUTION_RUNTIME_DATA_OR_MANIFEST_CHANGED')
    return runtime


def _write_once(path,value):
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2).encode()
    with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())


def _mutation(bound,operation,body,request):
    """Durable request intent; UNKNOWN cannot silently become another POST."""
    scope={'profile':bound['profile_ref'],'operation':operation,'body':body}
    directory=Path(bound['checkpoint_root'])/'agent-entry';directory.mkdir(exist_ok=True)
    identity=digest(scope);intent=directory/(identity+'.intent.json');result=directory/(identity+'.result.json')
    if intent.exists():
        if result.is_file():return json.loads(result.read_bytes())['http_status'],json.loads(result.read_bytes())['result']
        raise ExecutionPackageError('ORIGINAL_ENTRY_REQUEST_UNKNOWN_READBACK_REQUIRED:'+str(intent))
    _write_once(intent,{'schema_version':'publication-entry-intent/v1',**scope,'state':'UNKNOWN'})
    code,value=request(bound['runtime']['base_url'],operation,body)
    _write_once(result,{'http_status':code,'result':value,'intent_digest':identity})
    return code,value


def _stage(bound,offer,request):
    return request(bound['runtime']['base_url'],'/api/product-workspace/publication-stages?'+urlencode({'offer_id':offer}))


def run(args,*,request=request_json):
    bound=check_execution_profile(args.profile)
    if args.command=='binding':return {'ok':True,**bound}
    if args.command=='preflight':
        inputs=read_json_ref({'path':args.inputs,'sha256':args.inputs_sha256},root=bound['inputs_root'])
        pack=compile_publication_execution_package(inputs,task_id=args.task_id,
            source_identity=bound['source_identity'],runtime=bound['runtime'])
        if args.output:
            path=local_path(args.output,must_exist=False)
            if not path.is_relative_to(Path(bound['output_root'])):raise ExecutionPackageError('OUTPUT_OUTSIDE_BOUND_ROOT')
            _write_once(path,pack)
        return pack
    verify_runtime(bound,request)
    mode=bound['runtime'].get('workflow_mode','standard-v4')
    if args.command=='prepare':
        if mode!='native-sole-final':raise ExecutionPackageError('NEW_TASK_REQUIRES_NATIVE_SOLE_FINAL_MODE')
        if bound['source_dirty']:raise ExecutionPackageError('PREPARATION_SOURCE_DIRTY')
        targets=args.targets.split(',')
        if not args.offer_id.isascii() or not args.offer_id.isdigit() or not targets or len(set(targets))!=len(targets):
            raise ExecutionPackageError('EXACT_OFFER_AND_UNIQUE_TARGETS_REQUIRED')
        body={'template':'publication','source_key':args.source_key,
              'scope':{'offer_id':args.offer_id,'shops':targets}}
        return _mutation(bound,'/api/orbit/tasks',body,request)[1]
    if args.command in {'status','result'}:
        code,value=request(bound['runtime']['base_url'],'/api/orbit/home/tasks/'+quote(args.task_id,safe=''))
        if code!=200:return value
        task=value['task'];offer=task.get('scope',{}).get('offer_id')
        if task.get('template')!='publication' or not offer:raise ExecutionPackageError('ORIGINAL_PUBLICATION_TASK_REQUIRED')
        _,stage=_stage(bound,offer,request)
        return {'task':value,'publication':stage,'execution_authority':False}
    if mode=='native-sole-final':
        code,value=_stage(bound,args.offer_id,request)
        if code!=200:return value
        market=value.get('marketplace') or {};review=market.get('native_final_review') or {}
        if str(review.get('offer_id'))!=args.offer_id:raise ExecutionPackageError('ORIGINAL_NATIVE_REVIEW_REQUIRED')
        if args.command=='review':
            # A view is not a decision. The unchanged original frontend remains
            # the sole protected approval channel, with the complete manifest.
            return {'review':review,'candidate':market.get('preview'),'blockers':market.get('blockers'),
                'review_url':bound['runtime']['base_url'].rstrip('/')+'/product-workspace?'+urlencode({'offer_id':args.offer_id,'round':'final'}),
                'execution_authority':False}
        if (review.get('approval_saved') is not True or review.get('decision_id')!=args.decision_id):
            raise ExecutionPackageError('ORIGINAL_STORED_SOLE_DECISION_REQUIRED')
        if args.command=='reconcile':
            # Explicit provider READ reconciliation through the existing queue;
            # never calls execute, replaces UNKNOWN or republishes a listing.
            return _mutation(bound,'/api/product-workspace/native-final/readback',{'decision_id':args.decision_id},request)[1]
        if args.command=='execute':
            if bound['source_dirty']:raise ExecutionPackageError('EXECUTION_SOURCE_DIRTY')
            return _mutation(bound,'/api/product-workspace/native-final/resume',{'decision_id':args.decision_id},request)[1]
    if args.command in {'execute','reconcile'}:
        package=validate_publication_execution_package(read_json_ref({'path':args.package,'sha256':args.package_sha256},root=bound['inputs_root']))
        if package['source_identity']!=bound['source_identity'] or package['runtime']!=bound['runtime']:
            raise ExecutionPackageError('EXECUTION_PACKAGE_PROFILE_CHANGED')
        from shared_platform.publication_execution_dispatch import dispatch_package,reconcile_package
        fn=dispatch_package if args.command=='execute' else reconcile_package
        return fn(package,journal_root=bound['checkpoint_root'])
    raise ExecutionPackageError('WORKFLOW_COMMAND_UNSUPPORTED')


def main(argv=None):
    parser=argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--profile',required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('binding')
    p=sub.add_parser('prepare');p.add_argument('--offer-id',required=True);p.add_argument('--targets',required=True);p.add_argument('--source-key',required=True)
    for name in ('status','result'):
        p=sub.add_parser(name);p.add_argument('--task-id',required=True)
    p=sub.add_parser('preflight');p.add_argument('--inputs',required=True);p.add_argument('--inputs-sha256',required=True);p.add_argument('--task-id',required=True);p.add_argument('--output')
    p=sub.add_parser('review');p.add_argument('--offer-id',required=True)
    for name in ('execute','reconcile'):
        p=sub.add_parser(name);p.add_argument('--offer-id');p.add_argument('--decision-id');p.add_argument('--package');p.add_argument('--package-sha256')
    try:value=run(parser.parse_args(argv));print(json.dumps(value,ensure_ascii=False,indent=2));return 0
    except (OSError,ValueError,KeyError,TypeError) as error:
        print(json.dumps({'ok':False,'error':str(error),'execution_authority':False},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
