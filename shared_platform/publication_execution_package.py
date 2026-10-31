"""Repository-bound publication preparation and complete, inert execution packs.

The existing domain compiler owns facts, QA and the sole review identity. This
module joins executable dependencies before that review; it never manufactures
operator authority, installs schemas, starts services or refreshes credentials.
"""
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
import hashlib,html,importlib.util,json,os,re,sqlite3,stat,subprocess
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA='publication-execution-package/v1'
PROFILE_SCHEMA='publication-execution-profile/v1'
ROOT=Path(__file__).resolve().parents[1]
PLATFORMS=('TIKTOK','SHOPEE','OZON')

class ExecutionPackageError(ValueError):pass

class NativePublicationPreflightBlocked(ExecutionPackageError):
    def __init__(self,report):
        self.report=report
        super().__init__('NATIVE_PUBLICATION_PREFLIGHT_BLOCKED:'+','.join(row['code'] for row in report['blockers']))
def _need(value,code):
    if not value:raise ExecutionPackageError(code)
def _bytes(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value):return hashlib.sha256(_bytes(value)).hexdigest()
def _object(value):return type(value) is dict
def _list(value):return type(value) is list
def _id(value):return type(value) is str and re.fullmatch('[0-9]{1,32}',value) is not None
def _sha(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value.removeprefix('sha256:')) is not None


def local_path(value,*,directory=False,must_exist=True):
    _need(type(value) is str and value and Path(value).is_absolute() and '..' not in Path(value).parts,'EXPLICIT_LOCAL_PATH_REQUIRED')
    p=Path(value)
    _need(not str(p).startswith(('\\\\','//')),'NETWORK_ROOT_FORBIDDEN')
    for item in (p,*p.parents):
        if item.exists() or item.is_symlink():
            info=item.lstat()
            _need(not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&0x400,'REPARSE_ROOT_FORBIDDEN')
    if must_exist:_need(p.is_dir() if directory else p.is_file(),'BOUND_PATH_MISSING')
    return p.resolve()


def read_json_ref(ref,*,root=None):
    _need(_object(ref) and set(ref)=={'path','sha256'} and _sha(ref['sha256']),'INPUT_REFERENCE_REQUIRED')
    p=local_path(ref['path']);raw=p.read_bytes()
    if root is not None:_need(p.is_relative_to(Path(root).resolve()),'INPUT_OUTSIDE_BOUND_ROOT')
    _need(len(raw)<=16*1024*1024 and hashlib.sha256(raw).hexdigest()==ref['sha256'].removeprefix('sha256:'),'INPUT_REFERENCE_CHANGED')
    value=json.loads(raw.decode('utf-8-sig'));_need(_object(value),'INPUT_OBJECT_REQUIRED');return value


def check_execution_profile(path):
    p=local_path(str(path));raw=p.read_bytes();value=json.loads(raw.decode('utf-8-sig'))
    required={'schema_version','source_root','expected_source_head','settings_path','data_root','inputs_root',
              'output_root','reports_root','checkpoint_root','release_store_path','runtime'}
    _need(_object(value) and set(value)==required and value['schema_version']==PROFILE_SCHEMA,'EXECUTION_PROFILE_INVALID')
    root=local_path(value['source_root'],directory=True);_need(root==ROOT,'EXECUTION_SOURCE_ROOT_MISMATCH')
    env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')};env['GIT_OPTIONAL_LOCKS']='0'
    def git(*args):return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,check=True,env=env,timeout=20).stdout.strip()
    _need(Path(git('rev-parse','--show-toplevel')).resolve()==root,'EXECUTION_GIT_ROOT_CHANGED')
    _need(git('rev-parse','HEAD')==value['expected_source_head'],'EXECUTION_SOURCE_HEAD_CHANGED')
    settings=local_path(value['settings_path']);data=local_path(value['data_root'],directory=True)
    inputs=local_path(value['inputs_root'],directory=True);output=local_path(value['output_root'],directory=True)
    release=local_path(value['release_store_path'])
    reports=local_path(value['reports_root'],directory=True);checkpoints=local_path(value['checkpoint_root'],directory=True)
    _need(not data.is_relative_to(root) and all(p.is_relative_to(data) for p in (output,inputs,reports,checkpoints)),
          'EXECUTION_ARTIFACT_ROOTS_MUST_BE_TASK_DATA_OUTSIDE_SOURCE')
    runtime=_runtime(value['runtime'])
    _need(Path(runtime['expected_source_root']).resolve()==root and runtime['expected_source_head']==value['expected_source_head'],
          'EXECUTION_RUNTIME_SOURCE_CHANGED')
    from shared_platform.operations_runtime import runtime_manifest
    return {**value,'profile_ref':{'path':str(p),'sha256':hashlib.sha256(raw).hexdigest()},
        'source_identity':{'root':str(root),'head':value['expected_source_head'],'manifest_digest':runtime_manifest(root)},
        'settings_ref':{'path':str(settings),'sha256':hashlib.sha256(settings.read_bytes()).hexdigest()},
        'source_dirty':git('status','--porcelain=v1','-uall'),'runtime':runtime}


def _runtime(value):
    _need(_object(value) and {'base_url','expected_source_root','expected_source_head'}<=set(value)
          <={'base_url','expected_source_root','expected_source_head','workflow_mode'},'RUNTIME_BINDING_REQUIRED')
    u=urlsplit(value['base_url'])
    _need(u.scheme=='http' and u.hostname=='127.0.0.1' and u.port and not u.username and not u.password
          and not u.query and not u.fragment and u.path in {'','/'},'EXPLICIT_LOOPBACK_RUNTIME_REQUIRED')
    _need(value.get('workflow_mode','standard-v4') in {'standard-v4','native-sole-final'},'RUNTIME_WORKFLOW_UNSUPPORTED')
    _need(Path(value['expected_source_root']).is_absolute() and re.fullmatch('[0-9a-f]{40}',value['expected_source_head']),
          'RUNTIME_SOURCE_IDENTITY_REQUIRED')
    return deepcopy(value)


def _domain_material(inputs):
    """Actual R1/R2/R3 consumer; no placeholder producer or all-success handler."""
    if 'documents' in inputs:
        from shared_platform.publication_r3_image_bridge import build_marketplace_review_material
        return build_marketplace_review_material(inputs['documents'],inputs['common_plan'],inputs['common_run'],
            policy=inputs['policy'],incidents=inputs['incident_registry'],
            promotion_policy=inputs.get('promotion_policy'),ozon_stock_decision=inputs.get('ozon_stock_decision'))
    # A retained service-frozen payload uses the same original v4 projection and
    # quality compiler; local checksums grant no COMMON or final-decision rights.
    from domains.product_operations.approved_publication_snapshot import build_publication_preview
    from shared_platform.publication_autopilot import compile_release_preview
    payload=inputs['immutable_plan_payload'];snapshot=build_publication_preview(payload)
    candidate=compile_release_preview(snapshot,policy=inputs['policy'],incident_registry=inputs['incident_registry'],
                                      durable_evidence=inputs.get('durable_evidence'))
    return {'payload':deepcopy(payload),'snapshot':snapshot,'candidate':candidate}


def _asset_checks(snapshot,assets,block,*,documents=None):
    from domains.product_operations import publication_images_for_target
    from PIL import Image
    by_url={};seen_paths=set()
    if not _list(assets) or not assets:block('R2_IMAGE_ARTIFACTS_MISSING');return
    for row in assets:
        try:
            _need(_object(row) and {'url','path','sha256','role','brand'}<=set(row),'IMAGE_ARTIFACT_INVALID')
            _need(urlsplit(row['url']).scheme=='https' and row['url'] not in by_url,'IMAGE_ROUTE_DUPLICATE')
            p=local_path(row['path']);raw=p.read_bytes()
            _need(hashlib.sha256(raw).hexdigest()==row['sha256'].removeprefix('sha256:'),'IMAGE_BYTES_CHANGED')
            with Image.open(p) as im:im.verify()
            by_url[row['url']]=row;seen_paths.add(p)
        except (OSError,ValueError,KeyError,TypeError) as error:block(str(error) if isinstance(error,ExecutionPackageError) else 'IMAGE_ARTIFACT_UNREADABLE')
    for target in snapshot.get('publication_targets',[]):
        label=target['target_label']
        try:
            urls=list(publication_images_for_target(snapshot,label))
            # Getter returns image rows in some legacy snapshots, strings in v4.
            urls=[v.get('url') or v.get('image_url') if isinstance(v,dict) else v for v in urls]
            rows=[by_url[u] for u in urls if u in by_url]
            _need(len(rows)==len(urls) and len({r['sha256'] for r in rows})>=7,'GALLERY_DISTINCT_SEVEN_ARTIFACTS_REQUIRED')
        except (ValueError,KeyError,TypeError) as error:block(str(error) if isinstance(error,ExecutionPackageError) else 'TARGET_IMAGE_ROUTE_INVALID',label)
    if documents is not None:
        from shared_platform.publication_gallery_contract import validate_gallery_plan
        try:validate_gallery_plan(documents['round1_snapshot'])
        except (ValueError,KeyError,TypeError) as error:block(str(error))


def inspect_execution_dependencies(payload,snapshot,candidate,inputs):
    blockers=[]
    def block(code,target=None):
        row={'code':code,**({'target_label':target} if target else {})}
        if row not in blockers:blockers.append(row)
    _asset_checks(snapshot,inputs.get('image_assets'),block,documents=inputs.get('documents'))
    common=inputs.get('common_readback')
    if not _object(common):block('COMMON_READBACK_MISSING')
    else:
        from shared_platform.native_common_budget_facts import _READBACK_CHECKS
        checks=common.get('checks') or {}
        if (common.get('verified') is not True or not _READBACK_CHECKS<=set(checks)
                or any(checks[k] is not True for k in _READBACK_CHECKS)):
            block('COMMON_COMPLETE_FIELD_READBACK_REQUIRED')
        if str(common.get('offer_id'))!=str(payload.get('product_id')):block('COMMON_PRODUCT_IDENTITY_CHANGED')
    observed=inputs.get('capability_observations')
    if not _object(observed):block('CAPABILITY_OBSERVATIONS_MISSING');observed={}
    from modules.products.release_adapters import production_adapter_registry,ADAPTER_NAMES
    registry=production_adapter_registry()
    pricing=payload.get('pricing',{}).get('selected_targets',{})
    models=[r.get('model_sku') for r in snapshot.get('skus',[])]
    for label in payload.get('targets',[]):
        platform=label.split(':',1)[0]
        if ADAPTER_NAMES.get(platform) not in registry:block('PLATFORM_ADAPTER_NOT_INSTALLED',label)
        cap=observed.get(label)
        if not _object(cap):block('TARGET_CAPABILITY_OBSERVATION_MISSING',label);continue
        needed={'category_attributes','variant_images','price_precision','publication_readback','inventory_readback','existing_auth'}
        if any(cap.get(k) is not True for k in needed):block('TARGET_EXECUTION_CAPABILITY_INCOMPLETE',label)
        if not _sha(cap.get('evidence_digest')):block('TARGET_CAPABILITY_EVIDENCE_REQUIRED',label)
        rows=pricing.get(label,{}).get('sku_prices',[])
        if sorted(r.get('model_sku') for r in rows)!=sorted(models):block('PRICE_COMPLETE_MODEL_COVERAGE_REQUIRED',label)
    from shared_platform.postpublish_promotions import approved_promotion_action_policy
    for action in candidate.get('companion_actions',{}).get('actions',[]):
        if action.get('action_kind')=='POSTPUBLISH_DIRECT_DISCOUNT':
            label=action['prerequisite_target']
            try:
                policy=approved_promotion_action_policy(payload,action['action_target'])
                price=pricing[label]['store_prices']
                _need(len(price)==1,'EXACT_DISCOUNT_PRICE_REQUIRED')
                quote=price[0];listprice=Decimal(str(quote['list_price']))
                _need(all(Decimal(str(r.get('list_price',r.get('amount'))))==listprice for r in pricing[label]['sku_prices']),
                      'DISCOUNT_UNIFORM_MODEL_PRICES_REQUIRED')
                _need(policy['discount_percent']==quote['discount_reserve_pct'],'DISCOUNT_POLICY_CHANGED')
            except (ValueError,KeyError,TypeError,InvalidOperation):block('DISCOUNT_CONSUMER_FACTS_INCOMPLETE',label)
            if (observed.get(label) or {}).get('existing_discount_activity') is not True:block('DISCOUNT_ACTIVITY_CAPABILITY_MISSING',label)
    if any(a.get('action_kind')=='POSTPUBLISH_DIRECT_DISCOUNT' for a in candidate.get('companion_actions',{}).get('actions',[])):
        required={'oneclick_release_jobs','oneclick_release_targets','oneclick_release_events','oneclick_release_outcomes',
                  'oneclick_release_manual_acceptances','oneclick_release_write_occurrences'}
        if not required<=set(inputs.get('installed_schema_tables') or []):block('ONECLICK_SCHEMA_NOT_INSTALLED')
    return blockers


def compile_publication_execution_package(inputs,*,task_id,source_identity,runtime,approval_ref=None):
    _need(_object(inputs) and type(task_id) is str and task_id,'PACKAGE_INPUTS_REQUIRED')
    _need(_object(source_identity) and {'root','head','manifest_digest'}==set(source_identity),'SOURCE_IDENTITY_REQUIRED')
    runtime=_runtime(runtime)
    material={};blockers=[]
    if 'documents' not in inputs and not _object(inputs.get('immutable_plan_payload')):
        blockers.append({'code':'PUBLICATION_PAYLOAD_MISSING'})
    else:
        try:material=_domain_material(inputs)
        except (ValueError,KeyError,TypeError,OSError) as error:
            blockers.append({'code':'ORIGINAL_PUBLICATION_COMPILER_REJECTED','detail':type(error).__name__+':'+str(error)[:300]})
    payload=material.get('payload') or inputs.get('immutable_plan_payload') or {}
    snapshot=material.get('snapshot') or {};candidate=material.get('candidate') or {}
    blockers.extend(candidate.get('blockers') or [])
    blockers.extend(inspect_execution_dependencies(payload,snapshot,candidate,inputs))
    targets=list(payload.get('targets') or [])
    body={'schema_version':SCHEMA,'task_id':task_id,'offer_id':str(payload.get('product_id') or ''),
        'plan_id':str(payload.get('plan_id') or ''),'ordered_targets':targets,
        'platforms':[p for p in PLATFORMS if any(t.split(':',1)[0].upper()==p for t in targets)],
        'source_identity':deepcopy(source_identity),'runtime':runtime,
        'immutable_plan_payload':deepcopy(payload),'snapshot':snapshot,'candidate':candidate,
        'approval_ref':deepcopy(approval_ref),'status':'BLOCKED' if blockers else 'READY_FOR_FINAL_REVIEW',
        'blockers':blockers,'execution_authorized':False,'prepared_input_digest':digest(inputs)}
    body['package_digest']=digest(body);return body


def validate_publication_execution_package(package):
    _need(_object(package) and package.get('schema_version')==SCHEMA,'PACKAGE_SCHEMA_INVALID')
    body={k:v for k,v in package.items() if k!='package_digest'}
    _need(package.get('package_digest')==digest(body),'PACKAGE_DIGEST_CHANGED')
    _need(package.get('status')=='READY_FOR_FINAL_REVIEW' and package.get('blockers')==[],'PACKAGE_NOT_READY')
    _need(package.get('execution_authorized') is False,'PACKAGE_CANNOT_GRANT_EXECUTION_AUTHORITY')
    _runtime(package['runtime'])
    payload=package['immutable_plan_payload'];candidate=package['candidate']
    _need(_id(package['offer_id']) and payload.get('product_id')==package['offer_id']
          and payload.get('plan_id')==package['plan_id'] and candidate.get('plan_id')==package['plan_id'],
          'PACKAGE_PRODUCT_PLAN_CHANGED')
    targets=package['ordered_targets']
    _need(_list(targets) and targets and len(targets)==len(set(targets))
          and payload.get('targets')==targets and candidate.get('target_labels')==targets,'PACKAGE_TARGET_SCOPE_CHANGED')
    _need(package['platforms']==[p for p in PLATFORMS if any(t.split(':',1)[0].upper()==p for t in targets)],
          'PACKAGE_PLATFORM_SCOPE_CHANGED')
    from domains.product_operations.approved_publication_snapshot import validate_publication_preview
    validate_publication_preview(package['snapshot'])
    unsigned={k:v for k,v in candidate.items() if k!='candidate_digest'}
    _need(candidate.get('candidate_digest')==digest(unsigned),'PACKAGE_CANDIDATE_CHANGED')
    return deepcopy(package)


def render_package_review(package):
    """Actual frozen text/images/prices, not a summary of counts or digests."""
    candidate=package.get('candidate') or {};manifest=candidate.get('review_manifest') or {}
    esc=lambda v:html.escape(str(v),quote=True)
    parts=['<!doctype html><meta charset="utf-8"><title>Orbit publication review</title>',
        '<style>body{font:15px system-ui;max-width:1400px;margin:24px auto;color:#172238}img{width:180px;height:180px;object-fit:contain}section{border:1px solid #ccd6e5;padding:16px;margin:12px}pre{white-space:pre-wrap}table{border-collapse:collapse}td,th{padding:7px;border:1px solid #ddd}</style>',
        '<h1>商品上架完整候选</h1>',f'<p>Offer {esc(package.get("offer_id"))} · {esc(package.get("status"))}</p>',
        '<p>本页为当前冻结候选展示，不提供额外批准按钮。沿原可信终审入口只批准一次。</p>']
    for block in package.get('blockers',[]):parts.append('<p role="alert">'+esc(block)+'</p>')
    for row in manifest.get('copy_sets',[]):
        parts.append('<section><h2>'+esc(row.get('targets') or row.get('target_labels') or row.get('locale'))+'</h2><pre>'+esc(row.get('copy') or row)+'</pre></section>')
    parts.append('<section><h2>全部规格与商品事实</h2><pre>'+esc(json.dumps(manifest.get('variants') or package.get('snapshot',{}).get('skus'),ensure_ascii=False,indent=2))+'</pre></section>')
    for row in manifest.get('targets',[]):parts.append('<section><h2>'+esc(row.get('target_label'))+'</h2><pre>'+esc(json.dumps(row,ensure_ascii=False,indent=2))+'</pre></section>')
    from domains.product_operations import publication_images_for_target
    for label in package.get('ordered_targets',[]):
        parts.append('<section><h2>'+esc(label)+' 图库</h2>')
        try:
            for image in publication_images_for_target(package['snapshot'],label):
                url=(image.get('url') or image.get('image_url')) if isinstance(image,dict) else image
                parts.append('<img src="'+esc(url)+'" alt="'+esc(label)+' 商品图" loading="lazy">')
        except (KeyError,ValueError):parts.append('<p role="alert">图库不可用</p>')
        parts.append('</section>')
    return '\n'.join(parts)


def preflight_native_review(runtime,plan_id):
    """Service-owned, zero-write full dependency gate before the one review.

    Uses original domain graph/COMMON reader and installed schemas. Caller JSON
    and observation booleans cannot replace these reads or create authority.
    No HTTP, SDK, token renewal, paid operation, DDL or cached decision here.
    """
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    from shared_platform.r3_frozen_review_producer import read_stored_domain_graph
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    from domains.product_operations.approved_publication_snapshot import build_publication_preview
    from shared_platform.publication_r3_image_bridge import marketplace_quality_evidence
    _need(type(runtime) is NativeSoleFinalService,'NATIVE_REVIEW_RUNTIME_REQUIRED')
    plan=runtime.store.get_plan(plan_id)
    _need(plan is not None,'NATIVE_REVIEW_ORIGINAL_PLAN_REQUIRED')
    payload=plan['payload'];blockers=[]
    def block(code,target=None):
        row={'code':code,**({'target_label':target} if target else {})}
        if row not in blockers:blockers.append(row)
    with runtime.store._connect_readonly() as db:
        db.execute('BEGIN')
        graph=read_stored_domain_graph(db,plan_id,native_reader=NativeCommonSourceReader(runtime.store))
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        snapshot=build_publication_preview(payload)
        evidence=marketplace_quality_evidence(snapshot,payload,category_store=runtime.store,category_connection=db)
    candidate=json.loads(graph.display_bytes)
    for check in evidence['platform_preflight']['checks']:
        if check['status']!='PASSED':block(check['code'],check.get('target_label'))
    documents=payload['r3_marketplace_binding']['documents']
    assets=[]
    for row in documents['generation_result'].get('assets',[]):
        path=row.get('artifact_path') or row.get('local_path')
        if not path:block('R2_LOCAL_IMAGE_ARTIFACT_MISSING');continue
        assets.append({'url':row.get('public_url'),'path':path,'sha256':row.get('artifact_digest'),
                       'role':row.get('role'),'brand':row.get('brand_id')})
    _asset_checks(snapshot,assets,block)
    from shared_platform.postpublish_promotions import approved_promotion_action_policy
    actions=(candidate.get('companion_actions') or {}).get('actions') or []
    for action in actions:
        if action.get('action_kind')=='POSTPUBLISH_DIRECT_DISCOUNT':
            try:approved_promotion_action_policy(payload,action['action_target'])
            except (ValueError,KeyError,TypeError):block('DISCOUNT_CONSUMER_FACTS_INCOMPLETE',action.get('prerequisite_target'))
    if any(a.get('action_kind')=='POSTPUBLISH_DIRECT_DISCOUNT' for a in actions):
        required={'oneclick_release_jobs','oneclick_release_targets','oneclick_release_events','oneclick_release_outcomes',
                  'oneclick_release_manual_acceptances','oneclick_release_write_occurrences'}
        if not required<=tables:block('ONECLICK_SCHEMA_NOT_INSTALLED')
    from shared_platform.native_final_mutation_budget import _EXPECTED_SCHEMA
    with runtime.store._connect_readonly() as db:
        for name in _EXPECTED_SCHEMA:
            if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(name,)).fetchone() is None:
                block('NATIVE_MUTATION_BUDGET_SCHEMA_NOT_INSTALLED');break
    # Prove the same startup-selected catalog and canonical Model identity that
    # the new-task adapter will consume, before asking for human approval.
    if (getattr(runtime._operations,'new_task_worker',None) is not None
            and getattr(runtime._operations,'original_continuation',None) is None
            and getattr(runtime._operations,'agent_prepared_common',None) is None):
        bound=os.environ.get('ORBIT_CATALOG_DATABASE')
        if not bound or not Path(bound).is_absolute() or not Path(bound).is_file():block('CATALOG_STARTUP_BINDING_REQUIRED')
        if any(not re.fullmatch(r'[0-9]{4}',str(r.get('model_sku',''))) for r in snapshot['skus']):
            block('CANONICAL_ALIAS_AUTHORITY_REQUIRED')
    report={'schema_version':'native-publication-preflight/v1','offer_id':plan['product_id'],'plan_id':plan_id,
        'candidate_digest':graph.candidate_digest,'targets':list(graph.targets),'blockers':blockers,
        'status':'BLOCKED' if blockers else 'READY','execution_authority':False}
    report['preflight_digest']=digest(report)
    if blockers:raise NativePublicationPreflightBlocked(report)
    return report
