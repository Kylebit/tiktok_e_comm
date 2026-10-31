"""One source/config/data-bound Agent CLI over the original publication path.

No synthetic approval, provider fallback, automatic retries, schema creation or
service startup. prepare admits an explicit new task to the existing R1/R2/
COMMON worker. review/execute retain that same task and sole final decision.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import argparse,hashlib,json,os,uuid
from urllib.parse import urlencode,quote,urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler,ProxyHandler
from urllib.error import HTTPError
from shared_platform.publication_execution_package import (
    ExecutionPackageError,check_execution_profile,compile_publication_execution_package,
    validate_publication_execution_package,read_json_ref,render_package_review,digest,local_path)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ExecutionPackageError('RUNTIME_REDIRECT_FORBIDDEN')


def request_json(base,path,body=None,*,capture=None):
    raw=None if body is None else json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()
    request=Request(base.rstrip('/')+path,data=raw,headers={'Content-Type':'application/json','Origin':base.rstrip('/')},
                    method='GET' if raw is None else 'POST')
    try:
        response=build_opener(ProxyHandler({}),_NoRedirect).open(request,timeout=30)
    except HTTPError as error:response=error
    with response:
        status=response.status;wire=response.read(8*1024*1024+1)
        if capture is not None:capture(status,wire)
        if len(wire)>8*1024*1024:raise ExecutionPackageError('LOCAL_RESPONSE_TOO_LARGE_RECONCILE_ORIGINAL')
        return status,json.loads(wire)


def verify_runtime(bound,request=request_json):
    base=bound['runtime']['base_url']
    code,health=request(base,'/api/health')
    def same_path(left,right):
        return (isinstance(left,str) and isinstance(right,str)
                and Path(left).resolve()==Path(right).resolve())
    if (code!=200 or not same_path(health.get('root'),bound['source_root'])
            or health.get('commit')!=bound['expected_source_head']
            or not same_path((health.get('profile') or {}).get('settings_path'),bound['settings_path'])):
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


def _mutation(bound,operation,body,request,*,resume_known=False,read_only=False):
    """Durable request intent; UNKNOWN cannot silently become another POST."""
    scope={'endpoint':bound['runtime']['base_url'].rstrip('/'),'operation':operation,'body':body}
    binding={key:bound.get(key) for key in ('profile_ref','source_identity','settings_ref','data_root')}
    directory=Path(bound['checkpoint_root'])/'agent-entry';directory.mkdir(exist_ok=True)
    identity=digest(scope)
    if read_only:
        if operation!='/api/product-workspace/native-final/readback':raise ExecutionPackageError('READBACK_ROUTE_REQUIRED')
        identity+='-'+uuid.uuid4().hex # Explicit READ observations may refresh.
    generation=0
    while True:
        key=identity+('' if generation==0 else '-'+str(generation))
        intent=directory/(key+'.intent.json');result=directory/(key+'.result.json')
        if not intent.exists():break
        if json.loads(intent.read_bytes()).get('binding')!=binding:
            raise ExecutionPackageError('ORIGINAL_ENTRY_BINDING_CHANGED_RECONCILE_REQUIRED:'+str(intent))
        if not result.is_file():raise ExecutionPackageError('ORIGINAL_ENTRY_REQUEST_UNKNOWN_READBACK_REQUIRED:'+str(intent))
        previous=json.loads(result.read_bytes())
        if not resume_known or not 200<=previous['http_status']<300:return previous['http_status'],previous['result']
        generation+=1
    _write_once(intent,{'schema_version':'publication-entry-intent/v1',**scope,'binding':binding,'state':'UNKNOWN'})
    def capture(status,wire):
        path=directory/(key+'.http-body.private.bin')
        with path.open('xb') as stream:stream.write(wire);stream.flush();os.fsync(stream.fileno())
        _write_once(directory/(key+'.http-response.json'),{'http_status':status,'body_path':str(path),
            'body_sha256':hashlib.sha256(wire).hexdigest(),'body_bytes':len(wire),
            'truncated':len(wire)>8*1024*1024,'intent_digest':identity})
    # The injectable closed edge used by unit tests has no raw HTTP interface.
    # Production's fixed local client persists bytes before its JSON decoder.
    code,value=(request(bound['runtime']['base_url'],operation,body,capture=capture)
        if request is request_json else request(bound['runtime']['base_url'],operation,body))
    _write_once(result,{'http_status':code,'result':value,'intent_digest':identity})
    return code,value


def _stage(bound,offer,request):
    return request(bound['runtime']['base_url'],'/api/product-workspace/publication-stages?'+urlencode({'offer_id':offer}))


def _brief(value,keys):
    if not isinstance(value,dict):return {}
    result={}
    for key in keys:
        item=value.get(key)
        if key in value and (item is None or type(item) in (str,int,float,bool)):
            result[key]=item[:512] if isinstance(item,str) else item
    return result


def _brief_blockers(value):
    rows=value if isinstance(value,list) else []
    return [(row[:512] if isinstance(row,str) else _brief(row,('code','target_label','detail','reason')))
        for row in rows[:64] if isinstance(row,(str,dict))]


def _target_summary(row):
    value=_brief(row,('target_label','platform','status','state','reason','error','next_action',
        'owner','next_check_at','submitted','submission_accepted','readback_verified'))
    if isinstance(row.get('blocked_by'),list):value['blocked_by']=_brief_blockers(row['blocked_by'])
    if isinstance(row.get('blockers'),list):value['blockers']=_brief_blockers(row['blockers'])
    return value


def _plan_ref(value):
    return _brief(value,('plan_id','payload_digest','status','product_id','run_id','state'))


def publication_summary(stage,bound,offer):
    """Small observation only; original GET and frozen review remain intact."""
    market=stage.get('marketplace') or {};common=stage.get('common') or {}
    native=market.get('native_final_review') or {};candidate=market.get('preview') or {}
    result={'schema_version':'publication-agent-summary/v1','offer_id':offer,'execution_authority':False,
        'source_stage':{'method':'GET','url':bound['runtime']['base_url'].rstrip('/')+
            '/api/product-workspace/publication-stages?'+urlencode({'offer_id':offer}),
            'read_digest':digest(stage),'digest_encoding':'CANONICAL_JSON_SHA256',
            'full_bytes':len(json.dumps(stage,ensure_ascii=False,separators=(',',':')).encode())},
        'review_url':bound['runtime']['base_url'].rstrip('/')+'/product-workspace?'+urlencode({'offer_id':offer,'round':'final'}),
        'common':{**_brief(common,('status','user_status','next_action','reason')),
            'blockers':_brief_blockers(common.get('blockers')),'plan':_plan_ref(common.get('plan')),
            'run':_plan_ref(common.get('run'))},
        'marketplace':{**_brief(market,('status','native_execution_current_phase','native_execution_in_progress',
            'native_execution_queued','native_readback_resume_available','next_action','owner','next_check_at')),
            'blockers':_brief_blockers(market.get('blockers')),'plan':_plan_ref(market.get('plan')),
            'run':_plan_ref(market.get('run')),
            'native_final_review':_brief(native,('status','approval_saved','decision_id','plan_id','review_digest','candidate_digest','offer_id')),
            'native_preflight':{**_brief(market.get('native_preflight'),('status','preflight_digest')),
                'blockers':_brief_blockers((market.get('native_preflight') or {}).get('blockers'))}}}
    labels=market.get('targets') or candidate.get('target_labels') or []
    result['marketplace']['targets']=[v for v in labels if isinstance(v,str)]
    rows=market.get('target_results') or market.get('result_rows') or (market.get('run') or {}).get('targets') or []
    if isinstance(rows,dict):rows=[{'target_label':key,**(value if isinstance(value,dict) else {'status':value})} for key,value in rows.items()]
    result['marketplace']['target_results']=[_target_summary(row)
        for row in rows if isinstance(row,dict)]
    manifest=candidate.get('review_manifest') or {}
    image_sets=manifest.get('image_sets') or []
    # Equal platform galleries are displayed once; count exactly the frozen
    # distinct ordered sets, not duplicated target references or one viewport.
    galleries={digest(s.get('images',[])):s.get('images',[]) for s in image_sets if isinstance(s,dict)}
    counts={'target_count':len(result['marketplace']['targets']),
        'variants_count':len(manifest.get('variants') or []),
        'images_count':sum(len(v) for v in galleries.values()),
        'image_sets_count':len(galleries),'copy_sets_count':len(manifest.get('copy_sets') or [])}
    result['marketplace']['candidate']={**_brief(candidate,('candidate_digest','snapshot_digest','status','plan_id')),**counts}
    result['marketplace']['review_binding']=_brief(native.get('review'),('critical_content_digest','common_plan_id',
        'common_run_id','common_readback_digest','common_budget_digest','round1_digest','round2_digest','action_id'))
    result['marketplace']['manifest_digest']=manifest.get('manifest_digest')
    return result


def task_summary(value):
    task=value.get('task') or {};scope=task.get('scope') or {}
    return {**_brief(value,('ok','schema_version')),'task':{
        **_brief(task,('task_id','template','execution_state','current_step','status','created_at','updated_at',
            'phase','next_action','owner','next_check_at')),
        'scope':{**_brief(scope,('offer_id',)),'shops':[v for v in scope.get('shops',[]) if isinstance(v,str)]},
        'blocked_by':_brief_blockers(task.get('blocked_by')),
        'blockers':_brief_blockers(task.get('blockers'))}}


def run(args,*,request=request_json):
    bound=check_execution_profile(args.profile)
    if args.command=='binding':return {'ok':True,**bound}
    if args.command=='preflight':
        inputs=read_json_ref({'path':args.inputs,'sha256':args.inputs_sha256},root=bound['inputs_root'])
        from shared_platform.release_store import ReleaseStore
        pack=compile_publication_execution_package(inputs,task_id=args.task_id,
            source_identity=bound['source_identity'],runtime=bound['runtime'],
            category_store=ReleaseStore(bound['release_store_path']))
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
        return {'task':task_summary(value),'publication':publication_summary(stage,bound,offer),'execution_authority':False}
    if mode=='native-sole-final':
        code,value=_stage(bound,args.offer_id,request)
        if code!=200:return value
        market=value.get('marketplace') or {};review=market.get('native_final_review') or {}
        if str(review.get('offer_id'))!=args.offer_id:raise ExecutionPackageError('ORIGINAL_NATIVE_REVIEW_REQUIRED')
        if args.command=='review':
            # A view is not a decision. The unchanged original frontend remains
            # the sole protected approval channel, with the complete manifest.
            summary=publication_summary(value,bound,args.offer_id)
            return {'review':summary['marketplace']['native_final_review'],
                'candidate':summary['marketplace']['candidate'],'blockers':summary['marketplace']['blockers'],
                'publication':summary,'review_url':summary['review_url'],'execution_authority':False}
        if (review.get('approval_saved') is not True or review.get('decision_id')!=args.decision_id):
            raise ExecutionPackageError('ORIGINAL_STORED_SOLE_DECISION_REQUIRED')
        if args.command=='reconcile':
            # Explicit provider READ reconciliation through the existing queue;
            # never calls execute, replaces UNKNOWN or republishes a listing.
            return _mutation(bound,'/api/product-workspace/native-final/readback',{'decision_id':args.decision_id},request,read_only=True)[1]
        if args.command=='execute':
            if bound['source_dirty']:raise ExecutionPackageError('EXECUTION_SOURCE_DIRTY')
            if market.get('native_execution_in_progress') is True:
                return {'publication':value,'execution_in_progress':True,'new_human_review_required':False}
            return _mutation(bound,'/api/product-workspace/native-final/resume',{'decision_id':args.decision_id},request,resume_known=True)[1]
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
