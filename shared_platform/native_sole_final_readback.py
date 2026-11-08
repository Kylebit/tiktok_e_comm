"""Bounded official READ for this service's explicit accepted submissions.

There is no publish/repair callable here. Sites without an authorised official
reader remain submitted. Neither a platform acceptance nor a listing-only Ozon
observation closes a frozen warehouse/quantity command.
"""
from dataclasses import dataclass
from contextvars import ContextVar
import hashlib
import sqlite3
import json

from shared_platform.final_review_server_admission import ApprovalBlocked


@dataclass(frozen=True)
class _SubmissionReadback:
    run_id: str
    target_label: str
    idempotency_key: str
    attempt: int
    external_id: str
    submission_digest: str
    evidence_json: str


@dataclass(frozen=True)
class _TikTokFailedReadback:
    facts_json: str
    source_json: str
    evidence_json: str


_TIKTOK_FAILED_SCOPE=ContextVar('native_tiktok_failed_publish_readback',default=None)


_VN_SCOPE=ContextVar('b0431_vn_get_only_readback',default=None)
_VN='shopee:VN'
_VN_FAILURE='18b7a18ffaecefcb24e290661d12913fe360249a4ceadd69f080f43be91c92df'
_VN_PURPOSE='ORIGINAL_APPROVED_B0431_TIKTOK35_SHOPEE30_COMPANION_DISCOUNT_AND_VN_GET_ONLY_CANONICAL_CONTINUATION'
_VN_REQUESTS={14:('create_publish_task','ca0dc9081a0fd36b373057604d5612d926292b261c1652f4cf9b10aec81acdf8'),
              21:('update_regional_copy','6cd8be5df97945daf5ec6846b4e4b78db8c0f136b21e126265b101fed2d24907'),
              22:('list_regional_item','1f6e060440d81431ca81f4f6ca3241ca9dd25f1cfafa142aa03e6cd8013dd81b')}


@dataclass(frozen=True)
class _ShopeeFailedReadback:
    state_digest: str
    source_digest: str
    evidence_json: str


def _vn_need(value,code):
    if not value:raise ApprovalBlocked('NATIVE_VN_GET_ONLY_'+code)


def _vn_facts(db):
    """Same-connection immutable joins; no source/file/SDK/DDL operation."""
    from shared_platform.native_approved_discount_prices import B_PLAN,B_RUN,B_PAYLOAD,B_CANDIDATE,B_DECISION
    from shared_platform.release_store import _plan_from_row,_validated_publication_snapshot_row
    plan=db.execute('SELECT * FROM release_plans WHERE plan_id=?',(B_PLAN,)).fetchone()
    snap=db.execute('SELECT * FROM approved_publication_snapshots WHERE plan_id=?',(B_PLAN,)).fetchone()
    target=db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',(B_RUN,_VN)).fetchone()
    run=db.execute('SELECT * FROM release_runs WHERE run_id=?',(B_RUN,)).fetchone()
    failure=db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? AND attempt=4',(B_RUN,_VN)).fetchone()
    _vn_need(plan is not None and snap is not None and target is not None and failure is not None
        and run is not None and run['plan_id']==B_PLAN,'ORIGINAL_ROWS_REQUIRED')
    frozen=_validated_publication_snapshot_row(snap,plan=_plan_from_row(plan))
    _vn_need(plan['payload_digest']==B_PAYLOAD and _ozon_sha(json.loads(plan['payload_json']))==B_PAYLOAD
        and target['attempts']==4 and target['status'] in {'FAILED','SUCCEEDED'}
        and failure['evidence_digest']==_VN_FAILURE
        and hashlib.sha256(failure['evidence_json'].encode()).hexdigest()==_VN_FAILURE
        and hashlib.sha256(snap['snapshot_json'].encode()).hexdigest()=='3e41486796ec38f8f59fa73655abf1bc3deb1df27d62c5222e38baa5d4d02fb1','ORIGINAL_FAILURE_SNAPSHOT_CHANGED')
    rows=[dict(r) for r in db.execute('''SELECT r.*,d.request_digest,o.outcome FROM native_final_budget_reservations r
        JOIN native_final_budget_scopes s USING(scope_key)
        LEFT JOIN native_final_budget_dispatches d USING(scope_key,sequence)
        LEFT JOIN native_final_budget_outcomes o USING(scope_key,sequence)
        WHERE s.plan_id=? ORDER BY r.scope_key,r.sequence''',(B_PLAN,))]
    _vn_need(rows and all(r['outcome'] in {'ACCEPTED','REJECTED'} and type(r['request_digest']) is str
        and len(r['request_digest'])==64 for r in rows),'UNRECONCILED_MUTATION')
    own=[r for r in rows if r['target_label']==_VN]
    _vn_need(len(own)==3 and {r['sequence'] for r in own}==set(_VN_REQUESTS)
        and all((r['operation'],r['request_digest'])==_VN_REQUESTS[r['sequence']]
            and r['outcome']=='ACCEPTED' and r['budget_scope']=='target' and r['attempt_count']==1 for r in own),'ACCEPTED_ORIGINAL_PREFIX_CHANGED')
    scopes=[dict(r) for r in db.execute('SELECT * FROM native_final_budget_scopes WHERE plan_id=? ORDER BY scope_key',(B_PLAN,))]
    sh=[r for r in scopes if r['platform']=='SHOPEE']
    _vn_need(len(sh)==1,'ORIGINAL_BUDGET_BINDING_CHANGED')
    binding=json.loads(sh[0]['binding_json'])
    _vn_need(binding==dict(plan_id=B_PLAN,snapshot_digest=snap['snapshot_digest'],platform='SHOPEE',
        target_labels=['shopee:PH','shopee:MY','shopee:TH','shopee:VN'],shared_maximum=10,per_target_maximum=9,
        payload_digest=B_PAYLOAD,candidate_digest=B_CANDIDATE,decision_id=B_DECISION)
        and _ozon_sha(binding)==sh[0]['binding_sha256'],'ORIGINAL_BUDGET_BINDING_CHANGED')
    return {'target':dict(target),'failure':dict(failure),'run':dict(run),'ledger':rows,'scopes':scopes,
            'snapshot':frozen,'payload_digest':plan['payload_digest']}


def eligible_shopee_failed_readback(store,decision_id):
    """A pure queue hint only; source/actor/official facts are verified later."""
    from shared_platform.native_approved_discount_prices import B_DECISION
    if decision_id!=B_DECISION:return eligible_provider_failed_readback(store,decision_id)
    try:
        with store._connect_readonly() as db:
            db.execute('BEGIN');facts=_vn_facts(db)
        return facts['target']['status']=='FAILED'
    except (ValueError,OSError,KeyError,TypeError,RuntimeError):return False


def _provider_need(value,code):
    if not value:raise ApprovalBlocked('NATIVE_PROVIDER_READBACK_'+code)


def eligible_provider_failed_readback(store, decision_id):
    """SQL-only hint. A failure/approval never itself authorizes the reader."""
    try:
        with store._connect_readonly() as db:
            rows=db.execute('''SELECT f.evidence_json FROM native_sole_final_decisions d
                JOIN release_runs r ON r.plan_id=d.plan_id
                JOIN release_target_runs t USING(run_id)
                JOIN release_target_failure_events f ON f.run_id=t.run_id
                  AND f.target_label=t.target_label AND f.attempt=t.attempts
                WHERE d.decision_id=? AND t.status='FAILED' AND t.target_label LIKE 'shopee:%' ''',
                (decision_id,)).fetchall()
        return any((json.loads(r['evidence_json'] or '{}').get('schema_version')=='native-scoped-v4-target-result/v1'
            and json.loads(r['evidence_json'] or '{}').get('outcome_unknown') is True) for r in rows)
    except (ValueError,OSError,KeyError,TypeError,RuntimeError,sqlite3.Error):return False


def _provider_failed_facts(db, active, target):
    """Pure original-row CAS material; no file, network, schema or retry."""
    from shared_platform.release_store import _plan_from_row,_validated_publication_snapshot_row
    row=db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',
        (target['run_id'],target['target_label'])).fetchone()
    run=db.execute('SELECT plan_id FROM release_runs WHERE run_id=?',(target['run_id'],)).fetchone()
    _provider_need(row is not None and run is not None and run['plan_id']==active.plan_id,'PROVIDER_ORIGINAL_TARGET_REQUIRED')
    failure=db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? AND attempt=?',
        (target['run_id'],target['target_label'],row['attempts'])).fetchone()
    _provider_need(row['status']=='FAILED' and row['attempts']>0 and failure is not None,'PROVIDER_ORIGINAL_FAILED_ATTEMPT_REQUIRED')
    evidence=json.loads(failure['evidence_json'] or '{}')
    _provider_need(hashlib.sha256(failure['evidence_json'].encode()).hexdigest()==failure['evidence_digest']
        and evidence.get('schema_version')=='native-scoped-v4-target-result/v1'
        and evidence.get('outcome_unknown') is True,'PROVIDER_ORIGINAL_UNKNOWN_REQUIRED')
    plan=_plan_from_row(db.execute('SELECT * FROM release_plans WHERE plan_id=?',(active.plan_id,)).fetchone())
    snapshot=_validated_publication_snapshot_row(db.execute(
        'SELECT * FROM approved_publication_snapshots WHERE plan_id=?',(active.plan_id,)).fetchone(),plan=plan)
    receipt=active.service._decision(db,active.decision_id,owner_sid=active.owner_sid)
    _provider_need(receipt['snapshot_digest']==snapshot['snapshot_digest'],'PROVIDER_ORIGINAL_SNAPSHOT_REQUIRED')
    ledger=[dict(r) for r in db.execute('''SELECT r.*,d.request_digest,o.outcome FROM native_final_budget_reservations r
        JOIN native_final_budget_scopes s USING(scope_key)
        LEFT JOIN native_final_budget_dispatches d USING(scope_key,sequence)
        LEFT JOIN native_final_budget_outcomes o USING(scope_key,sequence)
        WHERE s.plan_id=? AND s.platform='SHOPEE' AND (r.target_label=? OR r.budget_scope='shared')
        ORDER BY r.scope_key,r.sequence''',(active.plan_id,target['target_label']))]
    _provider_need(any(r['target_label']==target['target_label'] and r['operation']=='create_publish_task'
        and r['request_digest'] and r['outcome'] in {None,'ACCEPTED'} for r in ledger),'PROVIDER_ORIGINAL_CREATE_REQUEST_REQUIRED')
    return {'target':dict(row),'failure':dict(failure),'plan':plan,'snapshot':snapshot,'ledger':ledger}


def _provider_original_task(active, facts):
    from shared_platform.native_prepared_v4_targets import provider_request_for_readonly_recovery
    from shared_platform.publication_provider_journal import _scope,provider_recovery_state
    request=provider_request_for_readonly_recovery(active,facts['plan'],facts['target'],facts['snapshot'])
    root,identity=_scope(request)
    state=provider_recovery_state(root,identity)
    candidates=[]
    for row in state['intents']:
        original=row['original_request'];resource=original.get('resource') or {}
        task_id=row['original_identities'].get('response.publish_task_id')
        if (row['http_status']==200 and row['decode_status']=='DECODED_LOCALLY'
                and original.get('provider')=='shopee' and original.get('method')=='POST'
                and original.get('path')=='/api/v2/global_product/create_publish_task'
                and row['original_execution']==identity and task_id and task_id.isdecimal()
                and int(task_id)>0 and resource.get('shop_region')==facts['target']['target_label'].split(':')[1]
                and type(resource.get('shop_id')) is int and type(resource.get('global_item_id')) is int):
            candidates.append({'task_id':task_id,'resource':resource,'account':original['account'],
                'response_key':row['key'],'response_sha256':row['response_sha256'],
                'request_body_sha256':original['request_body_sha256'],
                'native_request_digest':original.get('native_request_digest')})
    _provider_need(len(candidates)==1,'PROVIDER_UNIQUE_RETAINED_TASK_REQUIRED')
    return candidates[0]


def _provider_official_reads(active, source):
    """Six explicit GETs, existing bound credentials only; no mutation method."""
    from shared_platform.native_canonical_provider_edges import _existing_shopee_context
    from modules.shopee.client import shop_get,merchant_get
    from modules.shopee.auth import token_path
    from modules.shopee import skill_regions as r
    region=source['resource']['shop_region'];path=token_path().resolve(strict=True)
    pin=hashlib.sha256(path.read_bytes()).digest()
    def guard():
        from shared_platform.native_sole_final_execution import _ACTIVE
        _provider_need(_ACTIVE.get() is active and active.service._actor()['owner_sid']==active.owner_sid,'PROVIDER_CURRENT_ACTOR_CHANGED')
        _provider_need(hashlib.sha256(path.read_bytes()).digest()==pin,'PROVIDER_EXISTING_AUTH_CHANGED')
        ctx=_existing_shopee_context(region)
        _provider_need(ctx.shop_id==source['resource']['shop_id']
            and str(ctx.merchant_id)==source['account'].get('merchant_id'),'PROVIDER_EXACT_ACCOUNT_CHANGED')
        return ctx
    responses=[]
    def read(domain,endpoint,params):
        ctx=guard()
        value=(shop_get(endpoint,ctx.shop_id,ctx.shop_token,params,call_guard=guard) if domain=='shop'
            else merchant_get(endpoint,ctx.merchant_id,ctx.merchant_token,params))
        guard();responses.append({'domain':domain,'path':endpoint,'params':params,'response':value})
        _provider_need(type(value) is dict and not r._provider_error(value)[0],'PROVIDER_OFFICIAL_READ_FAILED')
        return value['response']
    task=read('merchant',r.TASK_RESULT_PATH,{'publish_task_id':int(source['task_id'])})
    _provider_need(str(task.get('publish_status') or '').lower()=='success','PROVIDER_ORIGINAL_TASK_STILL_PENDING')
    item_id=r._positive_identity(r._task_item_id(task),'original task item')
    read('shop','/api/v2/product/get_item_base_info',{'item_id_list':item_id})
    read('shop','/api/v2/product/get_model_list',{'item_id':int(item_id)})
    read('merchant','/api/v2/global_product/get_global_item_id',
        {'shop_id':source['resource']['shop_id'],'item_id_list':item_id})
    read('merchant','/api/v2/global_product/get_global_model_list',{'global_item_id':source['resource']['global_item_id']})
    read('merchant','/api/v2/merchant/get_merchant_warehouse_location_list',{})
    return responses


def _provider_verified_evidence(facts, source, responses, *, material_only=False):
    """Pure frozen matcher, repeated in the short final CAS."""
    from decimal import Decimal
    from modules.shopee import skill_regions as r
    from domains.product_operations import publication_images_for_target
    label=facts['target']['target_label'];snapshot=facts['snapshot']
    _provider_need(type(responses) is list and len(responses)==6,'PROVIDER_EXACT_READ_SET_REQUIRED')
    task=responses[0]['response']['response'];item_id=r._positive_identity(r._task_item_id(task),'original item')
    expected=[('merchant',r.TASK_RESULT_PATH,{'publish_task_id':int(source['task_id'])}),
        ('shop','/api/v2/product/get_item_base_info',{'item_id_list':item_id}),
        ('shop','/api/v2/product/get_model_list',{'item_id':int(item_id)}),
        ('merchant','/api/v2/global_product/get_global_item_id',{'shop_id':source['resource']['shop_id'],'item_id_list':item_id}),
        ('merchant','/api/v2/global_product/get_global_model_list',{'global_item_id':source['resource']['global_item_id']}),
        ('merchant','/api/v2/merchant/get_merchant_warehouse_location_list',{})]
    _provider_need([(v.get('domain'),v.get('path'),v.get('params')) for v in responses]==expected
        and str(task.get('publish_status') or '').lower()=='success','PROVIDER_EXACT_READ_IDENTITIES_REQUIRED')
    _provider_need(all(type(v['response']) is dict and not r._provider_error(v['response'])[0] for v in responses),'PROVIDER_OFFICIAL_READ_FAILED')
    values=[v['response']['response'] for v in responses]
    items=values[1]['item_list'];models=values[2]['model'];links=values[3]['item_id_map'];global_models=values[4]['global_model']
    _provider_need(len(items)==len(links)==1 and str(items[0].get('item_id'))==item_id
        and str(links[0].get('item_id'))==item_id
        and str(links[0].get('global_item_id'))==str(source['resource']['global_item_id']),'PROVIDER_ORIGINAL_ASSOCIATION_REQUIRED')
    item=items[0];approved=r._approved_models(snapshot,label);title,description=r._approved_copy(snapshot,label)
    _provider_need(len(models)==len(approved) and len({str(m.get('model_id')) for m in models})==len(approved),'PROVIDER_EXACT_MODEL_SET_REQUIRED')
    images=tuple(publication_images_for_target(snapshot,label));official_images=tuple((item.get('image') or {}).get('image_url_list') or [])
    ids=tuple((item.get('image') or {}).get('image_id_list') or [])
    checks=r._official_readback_checks(item=item,models=models,
        resolved_global_item_id=str(links[0]['global_item_id']),expected_global_item_id=str(source['resource']['global_item_id']),
        expected_models=approved,expected_tiers=r._exact_global_tiers(global_models,approved),expected_logistics=[],
        expected_image_count=len(images),expected_item_sku=r._expected_item_sku(snapshot),
        expected_category_id=r._expected_category_id(snapshot,label),expected_title=title,expected_description=description,
        expected_region=source['resource']['shop_region'],repaired_copy=(title,description),
        expected_image_ids=ids,expected_description_image_ids=ids,item_id=item_id)
    checks['exact_ordered_image_urls']=official_images==images
    checks['image_identities_complete']=len(ids)==len(images) and len(set(ids))==len(ids)
    checks['frozen_category_exact']=str(item.get('category_id') or '')==r._expected_category_id(snapshot,label)
    weight,dims=r._parcel_envelope(snapshot);parcel=item.get('dimension') or {}
    checks['parcel_exact']=(Decimal(str(item.get('weight')))==Decimal(str(weight))
        and [Decimal(str(parcel.get(k))) for k in ('package_length','package_width','package_height')]==[Decimal(str(v)) for v in dims])
    policy=snapshot['shopee_global_master']['policy'];warehouse=policy['warehouse']
    locations=[v for v in values[5] if v.get('warehouse_name')==warehouse['display_name']]
    _provider_need(len(locations)==1,'PROVIDER_UNIQUE_FROZEN_WAREHOUSE_REQUIRED')
    location=str(locations[0]['location_id'])
    _provider_need(not warehouse.get('location_id') or str(warehouse['location_id'])==location,'PROVIDER_FROZEN_WAREHOUSE_CHANGED')
    def stock(row):
        rows=(row.get('stock_info_v2') or {}).get('seller_stock') or []
        by={v.get('location_id'):v.get('stock') for v in rows}
        return (len(by)==len(rows) and by.get(location)==policy['stock']['quantity']
            and all(type(k) is str and type(v) is int and v==(policy['stock']['quantity'] if k==location else 0) for k,v in by.items()))
    checks['frozen_stock_exact']=all(stock(m) for m in models)
    if material_only:
        return {'checks':checks,'item':item,'models':models,'global_models':global_models,
            'approved_models':approved,'image_ids':ids,'image_urls':images,
            'title':title,'description':description,'item_id':item_id}
    _provider_need(all(v is True for v in checks.values()),'PROVIDER_FROZEN_OFFICIAL_MATCH_REQUIRED')
    canonical={'platform':'SHOPEE','status':'PUBLISHED','readback_completed':True,
        'dispatch_attempted':False,'readback_external_write_count':0,
        'historical_unknown_preserved':True,'targets':[{'target_label':label,'status':'PUBLISHED',
            'evidence':{'verified':True,'provider_task_id':source['task_id'],'item_id':item_id,
                'checks':checks,'official_catalog_rows':r._catalog_observation_rows(label,
                    source['resource']['shop_id'],item_id,source['resource']['shop_region'],item,models,approved)}}]}
    return {'schema_version':'native-provider-original-readback/v1','source':'official_shopee_shop_api',
        'verified':True,'target_label':label,'item_id':item_id,'shop_id':source['resource']['shop_id'],
        'provider_task_id':source['task_id'],'checks':checks,'official_responses':responses,
        'canonical_platform_result':canonical,'snapshot_digest':snapshot['snapshot_digest'],
        'original_response':source,'external_writes_performed':[],
        'original_failure_digest':facts['failure']['evidence_digest'],
        'original_budget_state_digest':_ozon_sha(facts['ledger']),
        'historical_unknown_preserved':True}


def _observe_provider_failed(active, gate, data, target):
    from shared_platform.native_new_task_v4_targets import scope
    scope(active,gate['plan'])  # Exact new-task origin; historical lanes excluded.
    with active.store._connect_readonly() as db:
        db.execute('BEGIN');facts=_provider_failed_facts(db,active,target)
    source=_provider_original_task(active,facts)
    responses=_provider_official_reads(active,source)
    evidence=_provider_verified_evidence(facts,source,responses)
    refreshed,changed=active.gate(data,store=active.store)
    _provider_need(changed is None,'PROVIDER_CURRENT_SOURCE_CHANGED')
    scope(active,refreshed['plan'])
    _provider_need(_provider_original_task(active,facts)==source,'PROVIDER_ORIGINAL_RESPONSE_CHANGED')
    from shared_platform.release_store import _utc_now,_canonical_json
    raw=_canonical_json(evidence)
    with active.store._transaction() as db:
        fresh=_provider_failed_facts(db,active,target)
        _provider_need(_ozon_sha(fresh)==_ozon_sha(facts),'PROVIDER_READBACK_CAS_CHANGED')
        _provider_need(evidence==_provider_verified_evidence(fresh,source,responses),'PROVIDER_READBACK_EVIDENCE_CHANGED')
        active.store._require_active_run(db,target['run_id'])
        _provider_need(db.execute('SELECT 1 FROM release_target_readbacks WHERE run_id=? AND target_label=?',
            (target['run_id'],target['target_label'])).fetchone() is None,'PROVIDER_ALREADY_HAS_READBACK')
        now=_utc_now()
        db.execute('INSERT INTO release_target_readbacks(run_id,target_label,evidence_json,evidence_digest,verified_at) VALUES(?,?,?,?,?)',
            (target['run_id'],target['target_label'],raw,hashlib.sha256(raw.encode()).hexdigest(),now))
        changed=db.execute("UPDATE release_target_runs SET status='SUCCEEDED',external_id=?,error=NULL,updated_at=?,completed_at=? WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=? AND idempotency_key=?",
            (f"shopee:{source['resource']['shop_id']}:item:{evidence['item_id']}",now,now,
             target['run_id'],target['target_label'],target['attempts'],target['idempotency_key']))
        _provider_need(changed.rowcount==1,'PROVIDER_READBACK_TARGET_CAS_CHANGED')
        active.store._refresh_run_status(db,target['run_id'],now=now)
    return {'target_label':target['target_label'],**evidence}


def _vn_source(active,plan):
    from shared_platform.agent_prepared_v4_targets import _scope
    from shared_platform.native_approved_discount_prices import B_EVENT,B_TASK,B_DECISION,read_b0431_discount_decision
    from shared_platform.native_shopee_regional_continuation import _list_event,_ref
    _vn_need(active.decision_id==B_DECISION and active.service._actor()['owner_sid']==active.owner_sid,'ORIGINAL_ACTOR_REQUIRED')
    bound,task,_=_scope(active,plan)  # Genuine current full HMAC/R1/R2 chain first.
    with sqlite3.connect(bound.engine.store.path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        rows=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=?',(B_TASK,B_EVENT)).fetchall()
    _vn_need(len(rows)==1,'SINGLE_SIGNED_SOURCE_REQUIRED')
    envelope=json.loads(rows[0][0]);raw=envelope['record_json'];record=json.loads(raw)
    _vn_need(hashlib.sha256(raw.encode()).hexdigest()==envelope['record_digest']
        and record.get('purpose')==_VN_PURPOSE and record.get('vn_get_only_target')==_VN
        and record.get('vn_get_only_failure_digest')==_VN_FAILURE
        and record.get('vn_get_only_item_id')=='52420043241','SOURCE_PURPOSE_REQUIRED')
    intent=read_b0431_discount_decision(record['b0431_discount_decision_ref'])
    _vn_need(_ozon_sha(intent)==record['b0431_discount_decision_digest'],'SIGNED_INTENT_CHANGED')
    completion=_list_event(bound,task)
    _vn_need(completion is not None,'ORIGINAL_COMPLETION_SOURCE_REQUIRED')
    doc=completion['doc'];models=json.loads(_ref(doc['official_item_refs'][_VN]['models_ref']))['response']['model']
    return {'source_digest':envelope['record_digest'],'image_ids':doc['approved_image_ids'][_VN],
            'original_models':models}


class _VNGetOnly:
    """Four whitelisted official SDK GETs; no mutation callable is installed."""
    def __init__(self,active):self.active=active
    def mutate(self,*a,**k):raise ApprovalBlocked('NATIVE_VN_GET_ONLY_MUTATION_FORBIDDEN')
    create_publish_task=upload_regional_images=update_regional_copy=update_regional_images=update_regional_description=list_item=mutate
    def observe(self):
        from shared_platform.native_canonical_provider_edges import _existing_shopee_context
        from modules.shopee.client import shop_get,merchant_get
        from modules.shopee.auth import token_path
        path=token_path().resolve(strict=True);pin=hashlib.sha256(path.read_bytes()).digest()
        def guard():
            from shared_platform.native_sole_final_execution import _ACTIVE
            _vn_need(_ACTIVE.get() is self.active and self.active.service._actor()['owner_sid']==self.active.owner_sid,'CURRENT_ACTOR_CHANGED')
            _vn_need(hashlib.sha256(path.read_bytes()).digest()==pin,'EXISTING_AUTH_CHANGED')
            ctx=_existing_shopee_context('VN')
            _vn_need(ctx.shop_id==1723948773 and ctx.merchant_id==4970102,'EXACT_SHOP_MERCHANT_REQUIRED')
            return ctx
        reads=(('shop','/api/v2/product/get_item_base_info',{'item_id_list':'52420043241'}),
               ('shop','/api/v2/product/get_model_list',{'item_id':52420043241}),
               ('merchant','/api/v2/global_product/get_global_item_id',{'shop_id':1723948773,'item_id_list':'52420043241'}),
               ('merchant','/api/v2/global_product/get_global_model_list',{'global_item_id':43135542690}))
        responses=[]
        for domain,endpoint,params in reads:
            ctx=guard()
            value=(shop_get(endpoint,ctx.shop_id,ctx.shop_token,params,call_guard=guard) if domain=='shop'
                else merchant_get(endpoint,ctx.merchant_id,ctx.merchant_token,params))
            guard();responses.append({'path':endpoint,'params':params,'response':value})
        return responses


def _vn_verified_evidence(facts,material,responses):
    """Pure original strict matcher; used again inside the Store CAS."""
    from modules.shopee import skill_regions as r
    from shared_platform.native_shopee_regional_continuation import _completion_parcel,_completion_models
    expected=('/api/v2/product/get_item_base_info','/api/v2/product/get_model_list',
              '/api/v2/global_product/get_global_item_id','/api/v2/global_product/get_global_model_list')
    _vn_need(type(responses) is list and tuple(v.get('path') for v in responses)==expected,'EXACT_READ_ENDPOINTS_REQUIRED')
    _vn_need([v.get('params') for v in responses]==[{'item_id_list':'52420043241'},{'item_id':52420043241},
        {'shop_id':1723948773,'item_id_list':'52420043241'},{'global_item_id':43135542690}],'EXACT_READ_IDENTITIES_REQUIRED')
    values=[]
    for row in responses:
        value=row['response'];_vn_need(isinstance(value,dict) and not str(value.get('error') or '').strip(' -'),'OFFICIAL_READ_FAILED')
        values.append(value['response'])
    items=values[0]['item_list'];models=values[1]['model'];links=values[2]['item_id_map'];global_models=values[3]['global_model']
    _vn_need(len(items)==1 and str(items[0].get('item_id'))=='52420043241'
        and len(links)==1 and str(links[0].get('item_id'))=='52420043241'
        and str(links[0].get('global_item_id'))=='43135542690','EXACT_OFFICIAL_ASSOCIATION_REQUIRED')
    snapshot=facts['snapshot'];item=items[0];approved=r._approved_models(snapshot,_VN);title,description=r._approved_copy(snapshot,_VN)
    checks=r._official_readback_checks(item=item,models=models,resolved_global_item_id='43135542690',expected_global_item_id='43135542690',
        expected_models=approved,expected_tiers=r._exact_global_tiers(global_models,approved),expected_logistics=[],
        expected_image_count=r._approved_image_count(snapshot),expected_item_sku=r._expected_item_sku(snapshot),
        expected_category_id=r._expected_category_id(snapshot,_VN),expected_title=title,expected_description=description,
        expected_region='VN',repaired_copy=(title,description),expected_image_ids=tuple(material['image_ids']),
        expected_description_image_ids=tuple(material['image_ids']),item_id='52420043241')
    _vn_need(len(checks)==15 and all(v is True for v in checks.values()),'FROZEN_OFFICIAL_MATCH_REQUIRED')
    _completion_parcel(snapshot,item);_completion_models(snapshot,_VN,models,material['original_models'])
    target_evidence={'target_label':_VN,'stage':'READBACK','provider_code':'shopee_official_readback_verified',
        'provider_identity_bound':True,'outcome_unknown':False,'item_id':'52420043241','checks':checks,
        'external_write_count':3,'official_catalog_rows':r._catalog_observation_rows(_VN,1723948773,'52420043241','VN',item,models,approved)}
    canonical={'platform':'SHOPEE','status':'PUBLISHED','readback_completed':True,'dispatch_attempted':False,
        'external_write_count':3,'readback_external_write_count':0,
        'targets':[{'target_label':_VN,'status':'PUBLISHED','evidence':target_evidence}]}
    return {'schema_version':'native-scoped-v4-target-result/v1','target_label':_VN,'verified':True,
        'status':'PUBLISHED','outcome_unknown':False,'reconciliation_required':False,'request_attempted':False,
        'known_pre_submit_zero':False,'canonical_platform_result':canonical,'checks':checks,
        'official_responses':responses,'external_writes_performed':[],
        'native_shopee_failed_readback':{'original_failure_digest':_VN_FAILURE,'attempt':4,
            'source_event_record_digest':material['source_digest'],'state_digest':_ozon_sha(facts),
            'historical_accepted_target_mutations':3,'new_merchant_writes':0}}


def _trusted_vn_proof(active,proof):
    scope=_VN_SCOPE.get()
    _vn_need(type(proof) is _ShopeeFailedReadback and scope is not None
        and scope['active'] is active and scope.get('proof') is proof,'NONCALLER_PROOF_REQUIRED')
    return scope


def _observe_vn_failed(active,gate,data):
    from shared_platform.native_approved_discount_prices import B_DECISION
    material=_vn_source(active,gate['plan'])
    with active.store._connect_readonly() as db:
        db.execute('BEGIN');active.service._decision(db,B_DECISION,owner_sid=active.owner_sid);facts=_vn_facts(db)
    _vn_need(facts['target']['status']=='FAILED','ORIGINAL_FAILED4_REQUIRED')
    responses=_VNGetOnly(active).observe();evidence=_vn_verified_evidence(facts,material,responses)
    _,failure=active.gate(data,store=active.store)
    _vn_need(failure is None and _vn_source(active,gate['plan'])==material,'CURRENT_SOURCE_CHANGED_AFTER_READ')
    proof=_ShopeeFailedReadback(_ozon_sha(facts),material['source_digest'],json.dumps(evidence,ensure_ascii=False,sort_keys=True,separators=(',',':')))
    token=_VN_SCOPE.set({'active':active,'proof':proof,'facts':facts,'material':material})
    try:active.store._record_native_shopee_failed_readback(proof)
    finally:_VN_SCOPE.reset(token)
    return {'target_label':_VN,'verified':True,'source':'official_shopee_shop_api','external_writes_performed':[]}


def _tiktok_failed_facts(db,active,target):
    """Original failed-after-accepted-publish rows, in one existing snapshot."""
    from shared_platform.release_store import _plan_from_row,_validated_publication_snapshot_row
    label=target['target_label'];need=_provider_need
    need(label in {'tiktok:LH_PH','tiktok:LH_MY','tiktok:LH_TH','tiktok:LH_VN'},'TIKTOK_ORIGINAL_LH_TARGET_REQUIRED')
    row=db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',(target['run_id'],label)).fetchone()
    run=db.execute('SELECT plan_id FROM release_runs WHERE run_id=?',(target['run_id'],)).fetchone()
    need(row is not None and run is not None and run['plan_id']==active.plan_id
        and row['status']=='FAILED' and row['attempts']>0 and row['external_id'] is None,'TIKTOK_ORIGINAL_FAILED_ATTEMPT_REQUIRED')
    for name in ('submissions','readbacks','retry_operations'):
        need(not db.execute('SELECT 1 FROM release_target_'+name+' WHERE run_id=? AND target_label=?',
            (target['run_id'],label)).fetchone(),'TIKTOK_EXISTING_EXTERNAL_RESULT_NO_REPLACEMENT')
    failure=db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? AND attempt=?',
        (target['run_id'],label,row['attempts'])).fetchone()
    need(failure is not None and hashlib.sha256(failure['evidence_json'].encode()).hexdigest()==failure['evidence_digest'],
        'TIKTOK_ORIGINAL_FAILURE_DIGEST_REQUIRED')
    evidence=json.loads(failure['evidence_json']);canonical=evidence.get('canonical_platform_result') or {}
    results=canonical.get('targets') or [];dispatch=results[0].get('evidence') or {} if len(results)==1 else {}
    need(evidence.get('schema_version')=='native-scoped-v4-target-result/v1' and evidence.get('target_label')==label
        and evidence.get('status')=='FAILED' and evidence.get('verified') is False and evidence.get('outcome_unknown') is False
        and evidence.get('known_pre_submit_zero') is False and evidence.get('external_writes_performed')==[label]
        and canonical.get('platform')=='TIKTOK' and canonical.get('dispatch_attempted') is True
        and len(results)==1 and results[0].get('target_label')==label and dispatch.get('stage')=='PUBLISH'
        and dispatch.get('provider_code')=='success' and dispatch.get('request_attempted') is True
        and dispatch.get('outcome_unknown') is False and dispatch.get('external_write_count')==1,
        'TIKTOK_RETAINED_ACCEPTED_PUBLISH_REQUIRED')
    plan=_plan_from_row(db.execute('SELECT * FROM release_plans WHERE plan_id=?',(active.plan_id,)).fetchone())
    snapshot=_validated_publication_snapshot_row(db.execute('SELECT * FROM approved_publication_snapshots WHERE plan_id=?',
        (active.plan_id,)).fetchone(),plan=plan)
    receipt=active.service._decision(db,active.decision_id,owner_sid=active.owner_sid)
    need(receipt['snapshot_digest']==snapshot['snapshot_digest'],'TIKTOK_ORIGINAL_DECISION_CHANGED')
    scopes=[dict(r) for r in db.execute("SELECT * FROM native_final_budget_scopes WHERE plan_id=? AND platform='TIKTOK'",(active.plan_id,))]
    need(len(scopes)==1,'TIKTOK_ORIGINAL_BUDGET_REQUIRED');scope=scopes[0];binding=json.loads(scope['binding_json'])
    need(hashlib.sha256(scope['binding_json'].encode()).hexdigest()==scope['binding_sha256']
        and all(binding.get(k)==v for k,v in {'plan_id':active.plan_id,'decision_id':active.decision_id,
            'snapshot_digest':snapshot['snapshot_digest'],'payload_digest':plan['payload_digest'],'platform':'TIKTOK'}.items())
        and label in binding['target_labels'],'TIKTOK_ORIGINAL_BUDGET_BINDING_CHANGED')
    ledger=[dict(r) for r in db.execute('''SELECT r.*,d.request_digest,o.outcome FROM native_final_budget_reservations r
        LEFT JOIN native_final_budget_dispatches d USING(scope_key,sequence)
        LEFT JOIN native_final_budget_outcomes o USING(scope_key,sequence)
        WHERE r.scope_key=? AND (r.target_label=? OR r.budget_scope='shared') ORDER BY r.sequence''',(scope['scope_key'],label))]
    publishes=[r for r in ledger if r['target_label']==label and r['operation']=='publish_target']
    need(len(publishes)==1 and publishes[0]['budget_scope']=='target' and publishes[0]['attempt_count']==1
        and publishes[0]['outcome']=='ACCEPTED' and bool(publishes[0]['request_digest']),
        'TIKTOK_UNKNOWN_OR_REPEATED_PUBLISH_NO_REPLAY')
    return {'target':dict(row),'failure':dict(failure),'plan':plan,'snapshot':snapshot,'scope':scope,'ledger':ledger}


def _tiktok_failed_source(active,facts):
    """Read the same current file scope; no journal initialization or replay."""
    from shared_platform.native_prepared_v4_targets import provider_request_for_readonly_recovery,_file_identity
    from shared_platform.publication_provider_journal import _scope
    from shared_platform.native_provider_continuation import retained_tiktok_rows
    from shared_platform.product_publication_live_dependencies import TikTokV4DraftCheckpointStore,_TIKTOK_SITE_BY_TARGET
    from modules.miaoshou.tiktok_publisher import EXPECTED_SHOP_ID_BY_TARGET,PUBLISH_PATH
    from shared_platform.native_final_mutation_budget import _request_sha
    target=facts['target'];label=target['target_label'];snapshot=facts['snapshot'];need=_provider_need
    request=provider_request_for_readonly_recovery(active,facts['plan'],target,snapshot);root,identity=_scope(request)
    store=TikTokV4DraftCheckpointStore(root);path=store._path(request)
    need(path.is_file(),'TIKTOK_ORIGINAL_CHECKPOINT_REQUIRED');raw=path.read_bytes();checkpoint=store._verified(json.loads(raw),request)
    own=[e for e in checkpoint['events'] if e['target_label']==label]
    pairs={(e.get('detail_id'),e.get('shop_id')) for e in own}
    need(own and len(pairs)==1,'TIKTOK_ORIGINAL_CONTROL_IDENTITY_REQUIRED');detail,shop=next(iter(pairs))
    need(type(detail) is str and detail.isdecimal() and int(detail)>0 and shop==EXPECTED_SHOP_ID_BY_TARGET[label],
        'TIKTOK_ORIGINAL_CONTROL_SHOP_CHANGED')
    original=next(t for t in snapshot['publication_targets'] if t['target_label']==label)
    need(original['store']==original['site']==label.split(':')[1] and original['platform']=='tiktok'
        and _TIKTOK_SITE_BY_TARGET[label] in {'PH','MY','TH','VN'},'TIKTOK_FROZEN_STORE_COUNTRY_CHANGED')
    mapping=_file_identity(run_id=target['run_id'],plan_id=active.plan_id,decision_id=active.decision_id,snapshot_digest=snapshot['snapshot_digest'])
    need(json.loads(facts['failure']['evidence_json']).get('checkpoint_file_identity')==mapping,'TIKTOK_ORIGINAL_FILE_MAPPING_CHANGED')
    body={'detailIds':[int(detail)],'shopIds':[shop]}
    rows=retained_tiktok_rows(root,identity);publishes=[r for r in rows if r['request']['path']==PUBLISH_PATH]
    need(len(publishes)==1 and publishes[0]['phase']=='RESPONSE_RETAINED' and publishes[0]['http_status']==200
        and publishes[0]['outcome']=='ACCEPTED' and publishes[0]['request']['request_body_sha256']==hashlib.sha256(
            json.dumps(body,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
        'TIKTOK_ORIGINAL_ACCEPTED_PUBLISH_JOURNAL_REQUIRED')
    reserved=next(r for r in facts['ledger'] if r['target_label']==label and r['operation']=='publish_target')
    need(reserved['request_digest']==_request_sha('publish_target',(PUBLISH_PATH,body),{}),'TIKTOK_ORIGINAL_PUBLISH_WIRE_CHANGED')
    return {'identity':identity,'checkpoint_path':str(path),'checkpoint_sha256':hashlib.sha256(raw).hexdigest(),
        'detail_id':detail,'shop_id':shop,'region':_TIKTOK_SITE_BY_TARGET[label],'publish':publishes[0]}


def _tiktok_failed_command(facts,source):
    from domains.channel_operations.tiktok_v4_execution import _resolved_category,_target_command
    snapshot=facts['snapshot'];label=facts['target']['target_label']
    original=next(t for t in snapshot['publication_targets'] if t['target_label']==label)
    category,digest=_resolved_category(snapshot['categories_by_target'][label],target=original,
        product=snapshot['product'],skus=snapshot['skus'],resolver=None)
    return _target_command(snapshot,target=original,control={k:source[k] for k in ('detail_id','shop_id')},
        category=category,category_evidence_digest=digest)


def _tiktok_failed_verified_evidence(facts,source,responses,media_equivalence=None):
    """Pure replay through the actual complete frozen matcher, including CAS."""
    from shared_platform.native_tiktok_exact_readback import NativeTikTokExactStorefrontReadback,_official_identity,MIAOSHOU_SHOPS,SHOP_PATH
    command=_tiktok_failed_command(facts,source);label=command['target_label'];need=_provider_need
    need(type(responses) is list and len(responses)==5,'TIKTOK_EXACT_OFFICIAL_READ_SET_REQUIRED')
    if media_equivalence is None:
        # Only the current protected store CAS can recover the transient typed
        # proof. No persisted JSON, public flag or stored success is authority.
        trusted=_TIKTOK_FAILED_SCOPE.get() or {}
        if trusted.get('proof') is not None:media_equivalence=trusted.get('media_equivalence')
    if media_equivalence is not None:
        from shared_platform.native_tiktok_media_equivalence import recheck_media
        recheck_media(media_equivalence,facts,source,responses)
    need(responses[0]['path']==MIAOSHOU_SHOPS and responses[1]['path']==SHOP_PATH,'TIKTOK_ORIGINAL_SHOP_READS_REQUIRED')
    erp,official=(responses[i]['response'] for i in (0,1))
    need(official.get('code')==0,'TIKTOK_OFFICIAL_AUTHORIZATION_READ_REQUIRED')
    shop=_official_identity(erp,official,internal_id=source['shop_id'],region=source['region'])
    data=erp['data'];rows=(data.get('list') or data.get('rows') or data.get('shopList')) if isinstance(data,dict) else data
    total=data.get('totalCount',data.get('total')) if isinstance(data,dict) else None
    need(len(rows)<100 and (total is None or type(total) is int and total==len(rows))
        and (not isinstance(data,dict) or data.get('hasNextPage') in (None,False)),
        'TIKTOK_ERP_SHOP_IDENTITY_ENUMERATION_PARTIAL')
    control=[r for r in rows if str(r.get('shopId') or r.get('id') or '')==source['shop_id']
        and str(r.get('site') or r.get('region') or '').upper()==source['region']]
    official_id=str(shop.get('id') or shop.get('shop_id') or '')
    need(len(control)==1 and all(str(control[0][k])==official_id for k in ('platformShopId','tiktokShopId')
        if control[0].get(k) is not None and str(control[0][k])!=''),'TIKTOK_ALL_PRESENT_OFFICIAL_IDS_MUST_AGREE')
    position=0
    def replay(method,path,query,body):
        nonlocal position
        need(position<len(responses),'TIKTOK_OFFICIAL_READ_SET_CHANGED');row=responses[position];position+=1
        need((row['method'],row['path'],row['query'],row['body'])==(method,path,query,body),'TIKTOK_OFFICIAL_REQUEST_SCOPE_CHANGED')
        return row['response']
    inventory=json.loads(facts['failure']['evidence_json']).get('provider_readonly_observations',{}).get('tiktok_prewrite_inventory',{})
    reader=NativeTikTokExactStorefrontReadback(shop_post=lambda path,body:replay('POST',path,None,body),
        official_call=lambda method,path,_token,query,body:replay(method,path,query,body),
        token_loader=lambda:('immutable-official-read-set',lambda:None),inventory_bindings=inventory,
        media_equivalence=media_equivalence,inventory_search=True)
    matched=reader.readback(command=command,dispatch={})
    need(position==5 and matched.get('exact') is True and matched.get('status')=='VERIFIED'
        and matched.get('official_catalog_rows'),'TIKTOK_COMPLETE_FROZEN_OFFICIAL_MATCH_REQUIRED')
    binding=matched['official_catalog_rows'][0]['shop_binding']
    canonical={'schema_version':'product-publication-platform-result/v1','platform':'TIKTOK',
        'status':'PUBLISHED','readback_completed':True,'dispatch_attempted':False,'external_write_count':0,
        'targets':[{'target_label':label,'status':'PUBLISHED','evidence':{'target_label':label,'stage':'READBACK',
            'verified':True,'outcome_unknown':False,'provider_identity_bound':True,
            'provider_code':'tiktok_official_readback_verified','external_write_count':0,
            'official_catalog_rows':matched['official_catalog_rows']}}]}
    return {'schema_version':'native-scoped-v4-target-result/v1','source':'official_tiktok_shop_api',
        'native_failed_readback_schema':'native-tiktok-original-failed-readback/v1',
        'status':'PUBLISHED','outcome_unknown':False,'reconciliation_required':False,'request_attempted':False,
        'verified':True,'target_label':label,'external_writes_performed':[],'historical_failure_preserved':True,
        'canonical_platform_result':canonical,
        'original_failure_digest':facts['failure']['evidence_digest'],'snapshot_digest':facts['snapshot']['snapshot_digest'],
        'command_digest':command['command_digest'],'mapping':binding,'mapping_digest':_ozon_sha(binding),
        'checks':{'complete_frozen_match':True,'original_control_shop':True,'current_unique_two_provider_identity':True,
            'all_present_explicit_ids_agree':True},'official_responses':responses,'official_catalog_rows':matched['official_catalog_rows'],
        'inventory_evidence':matched['inventory_evidence'],
        **({'media_equivalence':matched['media_equivalence']} if media_equivalence is not None else {})}


def _observe_tiktok_failed(active,gate,data,target,media_equivalence_ref=None,media_cache=None):
    from shared_platform.native_new_task_v4_targets import scope
    from shared_platform.native_tiktok_exact_readback import NativeTikTokExactStorefrontReadback,MIAOSHOU_SHOPS,SHOP_PATH,SEARCH,DETAIL,INVENTORY
    scope(active,gate['plan'])
    with active.store._connect_readonly() as db:
        db.execute('BEGIN');facts=_tiktok_failed_facts(db,active,target)
    source=_tiktok_failed_source(active,facts);responses=[]
    # The existing reader owns its ordinary SDK/token guard. Only capture its
    # five real READs; no publisher, refresh or repair exists. A media ref may
    # only prove representations against this freshly captured SDK response.
    inventory=json.loads(facts['failure']['evidence_json']).get('provider_readonly_observations',{}).get('tiktok_prewrite_inventory',{})
    shop_post,official=NativeTikTokExactStorefrontReadback()._calls()
    def capture(method,path,query,body,invoke):
        from shared_platform.native_sole_final_execution import _ACTIVE
        _provider_need(_ACTIVE.get() is active and active.service._actor()['owner_sid']==active.owner_sid,'TIKTOK_CURRENT_ACTOR_CHANGED')
        _provider_need(len(responses)<5 and (path in {MIAOSHOU_SHOPS,SHOP_PATH,SEARCH,INVENTORY} or path.startswith(DETAIL.split('{')[0])),
            'TIKTOK_READ_ONLY_ENDPOINT_REQUIRED')
        value=invoke();responses.append({'method':method,'path':path,'query':query,'body':body,'response':value});return value
    reader=NativeTikTokExactStorefrontReadback(
        shop_post=lambda path,body:capture('POST',path,None,body,lambda:shop_post(path,body)),
        official_call=lambda method,path,_token,query,body:capture(method,path,query,body,lambda:official(method,path,query,body)),
        token_loader=lambda:('existing-sdk-token-guard',lambda:None),inventory_bindings=inventory,inventory_search=True)
    reader.readback(command=_tiktok_failed_command(facts,source),dispatch={})
    media=None
    if media_equivalence_ref is not None:
        _provider_need(len(responses)==5,'TIKTOK_CURRENT_COMPLETE_SDK_READ_SET_REQUIRED')
        from shared_platform.native_tiktok_media_equivalence import verify_media_ref
        media=verify_media_ref(media_equivalence_ref,facts,source,responses,cache=media_cache if media_cache is not None else {})
    evidence=_tiktok_failed_verified_evidence(facts,source,responses,media)
    refreshed,changed=active.gate(data,store=active.store)
    _provider_need(changed is None,'TIKTOK_CURRENT_SOURCE_CHANGED_AFTER_READ');scope(active,refreshed['plan'])
    _provider_need(_tiktok_failed_source(active,facts)==source,'TIKTOK_ORIGINAL_SOURCE_CHANGED_AFTER_READ')
    proof=_TikTokFailedReadback(json.dumps(facts,ensure_ascii=False,sort_keys=True,separators=(',',':')),
        json.dumps(source,ensure_ascii=False,sort_keys=True,separators=(',',':')),
        json.dumps(evidence,ensure_ascii=False,sort_keys=True,separators=(',',':')))
    token=_TIKTOK_FAILED_SCOPE.set({'active':active,'proof':proof,'media_equivalence':media})
    try:active.store._record_native_tiktok_failed_readback(proof)
    finally:_TIKTOK_FAILED_SCOPE.reset(token)
    return {'target_label':target['target_label'],**evidence}


def _official_observation(payload, target):
    from modules.products import release_adapters as adapters
    channel, site = target['target_label'].split(':', 1)
    if channel != 'tiktok' or site not in adapters.SEA_SITES:
        return {'verified': False, 'capability': 'OFFICIAL_EXACT_READBACK_UNAVAILABLE',
                'external_writes_performed': []}
    retained = (target.get('submission') or {}).get('evidence') or {}
    prior = retained.get('accepted_submission') or retained
    if (prior.get('accepted') is not True or type(prior.get('detail_id')) is not int
            or prior['detail_id'] <= 0 or type(prior.get('shop_id')) is not int
            or prior['shop_id'] <= 0 or target['external_id'] != str(prior['detail_id'])+':'+str(prior['shop_id'])):
        return {'verified': False, 'capability': 'SUBMISSION_READ_IDENTITY_UNAVAILABLE',
                'external_writes_performed': []}
    category = (((payload.get('product_facts') or {}).get('categories_by_target') or {}).get(target['target_label']) or {})
    category_id = (category.get('category') or {}).get('id')
    if (category.get('target_label') != target['target_label']
            or category.get('platform') != channel or category.get('site') != site
            or not isinstance(category_id, str) or not category_id.isascii() or not category_id.isdigit()):
        return {'verified': False, 'capability': 'FROZEN_TARGET_CATEGORY_UNAVAILABLE',
                'external_writes_performed': []}
    # The native frozen source currently retains a Miaoshou shop ID only.
    # TikTok's authorization ID is a different identity domain. Region, shop
    # name, matching SKU and even a previous regional READ cannot prove the
    # submitted shop. Until its exact official mapping is source-bound, no
    # regional first-shop query may close this original accepted submission.
    return {'verified': False, 'capability': 'FROZEN_OFFICIAL_SHOP_MAPPING_UNAVAILABLE',
            'target_label': target['target_label'], 'expected_category_id': category_id,
            'external_writes_performed': []}


def _ozon_need(value, code):
    if not value:
        raise ApprovalBlocked('NATIVE_OZON_READBACK_' + code)


def _ozon_sha(value):
    import hashlib
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _ozon_context(active, db, target, submission):
    """Read existing immutable rows/proofs only; no recovery admission or DDL."""
    from shared_platform import native_ozon_existing_media_recovery as media
    from shared_platform.native_sole_final_execution import _ACTIVE, _NativeExecution
    from shared_platform.release_store import _plan_from_row, _validated_publication_snapshot_row
    from modules.ozon.approved_publication_v4 import project_ozon_v4_variants
    from shared_platform.native_final_mutation_budget import _request_sha
    _ozon_need(type(active) is _NativeExecution and _ACTIVE.get() is active
        and active.plan_id == media.PLAN and active.decision_id == media.DECISION,
        'ORIGINAL_PROTECTED_SCOPE_REQUIRED')
    active.service._decision(db, active.decision_id, owner_sid=active.owner_sid)
    run = db.execute('SELECT * FROM release_runs WHERE run_id=?', (media.RUN,)).fetchone()
    plan_row = db.execute('SELECT * FROM release_plans WHERE plan_id=?', (media.PLAN,)).fetchone()
    snap_row = db.execute('SELECT * FROM approved_publication_snapshots WHERE plan_id=?', (media.PLAN,)).fetchone()
    _ozon_need(run is not None and run['plan_id'] == media.PLAN and plan_row is not None
        and snap_row is not None and target['run_id'] == media.RUN
        and target['target_label'] == media.TARGET and submission['run_id'] == media.RUN
        and submission['target_label'] == media.TARGET
        and target['attempts'] == 4 and target['status'] in {'FAILED','SUCCEEDED'}
        and target['external_id'] == submission['external_id'] == 'ozon:offer:' + media.OFFER,
        'ORIGINAL_SUBMISSION_IDENTITY_CHANGED')
    plan = _plan_from_row(plan_row)
    frozen = _validated_publication_snapshot_row(snap_row, plan=plan)
    _ozon_need(frozen['snapshot_digest'] == media.SNAPSHOT, 'FROZEN_SNAPSHOT_CHANGED')
    variants = project_ozon_v4_variants(frozen, target_labels=(media.TARGET,))
    _ozon_need(len(variants) == 1 and variants[0]['offer_id'] == media.OFFER,
        'EXACT_FROZEN_MODEL_REQUIRED')
    expected = variants[0]
    prior = json.loads(submission['evidence_json'])
    _ozon_need(submission['status'] == 'SUBMITTED_UNVERIFIED'
        and _ozon_sha(prior) == submission['evidence_digest']
        and prior.get('accepted') is True and prior.get('outcome_unknown') is False
        and prior.get('target_label') == media.TARGET,
        'KNOWN_ACCEPTED_SUBMISSION_REQUIRED')
    stock_proof = prior['canonical_platform_result']['targets'][0]['evidence']['stock_readonly_observations']['post']
    original_raw = stock_proof['raw_response']
    _ozon_need(type(original_raw) is dict and original_raw.get('has_next') is False
        and original_raw.get('cursor') == '' and type(original_raw.get('products')) is list
        and len(original_raw['products']) == 1, 'ORIGINAL_COMPLETE_STOCK_PROOF_REQUIRED')
    original_stock = original_raw['products'][0]
    warehouse = expected.get('stock_warehouse_id')
    sku = original_stock.get('sku')
    _ozon_need(type(sku) is int and sku > 0 and type(warehouse) is int and warehouse > 0
        and original_stock.get('offer_id') == media.OFFER
        and original_stock.get('product_id') == media.PRODUCT
        and original_stock.get('warehouse_id') == warehouse
        and type(original_stock.get('present')) is int
        and original_stock['present'] == expected['stock_quantity']
        and type(original_stock.get('free_stock')) is int
        and original_stock['free_stock'] == expected['stock_quantity']
        and type(original_stock.get('reserved')) is int and original_stock['reserved'] == 0,
        'ORIGINAL_EXACT_STOCK_IDENTITY_REQUIRED')
    budget = prior['mutation_budget']
    scope_key = budget['durable_scope']['scope_key']
    scope = db.execute('SELECT * FROM native_final_budget_scopes WHERE scope_key=?', (scope_key,)).fetchone()
    decision = db.execute('SELECT review_json FROM native_sole_final_decisions WHERE decision_id=?', (media.DECISION,)).fetchone()
    reviewed = json.loads(decision['review_json'])
    approved_budget = plan['payload']['r3_marketplace_binding']['reviewed_candidate_facts']['write_budget']['OZON']
    expected_binding = {'plan_id':media.PLAN,'snapshot_digest':media.SNAPSHOT,'platform':'OZON',
        'target_labels':approved_budget['target_labels'],'shared_maximum':approved_budget['shared_maximum'],
        'per_target_maximum':approved_budget['per_target_maximum'],'payload_digest':plan['payload_digest'],
        'candidate_digest':reviewed['candidate_digest'],'decision_id':media.DECISION}
    _ozon_need(scope is not None and scope['plan_id'] == media.PLAN
        and scope['snapshot_digest'] == media.SNAPSHOT and scope['platform'] == 'OZON'
        and _ozon_sha(json.loads(scope['binding_json'])) == scope['binding_sha256']
        and json.loads(scope['binding_json']) == expected_binding,
        'ORIGINAL_BUDGET_SCOPE_CHANGED')
    rows = [dict(row) for row in db.execute('''SELECT r.*,d.request_digest,o.outcome FROM native_final_budget_reservations r
        JOIN native_final_budget_scopes s USING(scope_key)
        LEFT JOIN native_final_budget_dispatches d USING(scope_key,sequence)
        LEFT JOIN native_final_budget_outcomes o USING(scope_key,sequence)
        WHERE s.plan_id=? ORDER BY r.scope_key,r.sequence''', (media.PLAN,))]
    _ozon_need(len(rows) == 62 and all(row['outcome'] in {'ACCEPTED','REJECTED'}
        and type(row['request_digest']) is str and len(row['request_digest']) == 64 for row in rows),
        'ORIGINAL_62_KNOWN_BUDGET_ROWS_REQUIRED')
    own = [row for row in rows if row['scope_key'] == scope_key]
    request_digest = _request_sha('update_stock', ('/v2/products/stocks', {'stocks':[
        {'offer_id': media.OFFER, 'stock': expected['stock_quantity'], 'warehouse_id': warehouse}]}), {})
    _ozon_need(len(own) == 2 and [(row['operation'],row['outcome']) for row in own]
        == [('import_variant','ACCEPTED'),('update_stock','ACCEPTED')]
        and own[1]['request_digest'] == request_digest
        and all(row['budget_scope'] == 'shared' and row['attempt_count'] == 1 for row in own)
        and budget['limits'] == {'shared_maximum':2,'per_target_maximum':0}
        and budget['base_attempts'] == {'shared':2,'per_target':{media.TARGET:0},'total':2},
        'ORIGINAL_STOCK_SLOT_NOT_EXACTLY_SPENT')
    grant = media._grant(db)
    _ozon_need(grant is not None, 'REGISTERED_IMAGE_PROOF_REQUIRED')
    content = media._registered_content_for_readback(db, grant, frozen)
    pictures = content['ordered_ozon_images']
    _ozon_need(content['ordered_source_images'] == expected['images'], 'REGISTERED_ORDERED_SOURCE_IMAGES_CHANGED')
    failures = [dict(row) for row in db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? ORDER BY attempt',
        (media.RUN, media.TARGET))]
    _ozon_need([row['attempt'] for row in failures] == [1,2,3], 'ORIGINAL_THREE_FAILURES_CHANGED')
    media_rows = {name:[dict(row) for row in db.execute('SELECT * FROM native_ozon_media_' + name + ' WHERE grant_id=?',
        (grant['grant_id'],))] for name in ('grants','attempts','dispatches','outcomes','verifications','starts')}
    _ozon_need(all(len(value) == 1 for value in media_rows.values()), 'ORIGINAL_MEDIA_LEDGER_CHANGED')
    return {'expected':expected, 'account_id':media.ACCOUNT, 'product_id':media.PRODUCT,
        'import_task_id':media.TASK,
        'sku':sku, 'warehouse_id':warehouse, 'ordered_gallery':pictures,
        'import_request_digest':own[0]['request_digest'],
        'registered_proof_ref':content['registered_proof_ref'],
        'snapshot_digest':frozen['snapshot_digest'],
        'source_state_digest':_ozon_sha({'budget':rows,'media':media_rows,'failures':failures,
            'submission_digest':submission['evidence_digest'],'target_attempt':target['attempts'],
            'target_key':target['idempotency_key'],'plan_digest':plan['payload_digest']})}


def _ozon_read_requests(context):
    offer = context['expected']['offer_id']
    return [('/v3/product/info/list', {'offer_id':[offer],'limit':1000,'visibility':'ALL'}),
        ('/v4/product/info/attributes', {'filter':{'offer_id':[offer],'visibility':'ALL'},'limit':1000}),
        ('/v1/product/info/description', {'offer_id':offer}),
        ('/v2/warehouse/list', {}),
        ('/v2/product/info/stocks-by-warehouse/fbs', {'offer_id':[offer],'limit':1000})]


def _ozon_verified_evidence(context, responses):
    """Deterministic raw-response recheck, also used inside original Store CAS."""
    from copy import deepcopy
    from shared_platform.product_publication_live_dependencies import (
        OfficialOzonV4Transport, build_ozon_import_item_from_frozen_variant)
    from shared_platform.native_final_mutation_budget import _request_sha
    from modules.ozon.approved_publication_v4 import _classify_variant, OzonDispatchFact
    requests = _ozon_read_requests(context)
    _ozon_need(type(responses) is list and len(responses) == len(requests), 'COMPLETE_READ_BUNDLE_REQUIRED')
    for row, (path,body) in zip(responses, requests):
        _ozon_need(type(row) is dict and set(row) == {'path','body','response'}
            and row['path'] == path and row['body'] == body and type(row['response']) is dict
            and row['response'].get('error') in (None, '', False), 'EXACT_READ_BUNDLE_REQUIRED')
    cursor = 0
    def retained_post(path, body):
        nonlocal cursor
        _ozon_need(cursor < len(responses) and (path,body) == requests[cursor], 'READ_ORDER_CHANGED')
        value = deepcopy(responses[cursor]['response']); cursor += 1
        return value
    transport = OfficialOzonV4Transport(post=retained_post, account_id=context['account_id'])
    offer = context['expected']['offer_id']
    normalized = transport.readback_variants((offer,))
    stocks = transport.readback_stocks((offer,))
    _ozon_need(cursor == 5 and len(normalized) == len(stocks) == 1, 'COMPLETE_EXACT_READ_REQUIRED')
    info = responses[0]['response']; attrs = responses[1]['response']
    _ozon_need(type(info.get('items')) is list and len(info['items']) == 1
        and info.get('has_next', False) is False and info.get('cursor', '') == ''
        and type(attrs.get('total')) is int and attrs['total'] == 1 and attrs.get('last_id') == ''
        and type(attrs.get('result')) is list and len(attrs['result']) == 1,
        'COMPLETE_SINGLE_PRODUCT_PAGE_REQUIRED')
    item = info['items'][0]; attribute_row = attrs['result'][0]
    expected = context['expected']; statuses = item.get('statuses') or {}
    _ozon_need(item.get('id') == attribute_row.get('id') == context['product_id']
        and item.get('sku') == attribute_row.get('sku') == context['sku']
        and item.get('offer_id') == attribute_row.get('offer_id') == offer
        and item.get('currency_code') == expected['currency'], 'OFFICIAL_COMPOSITE_IDENTITY_CHANGED')
    _ozon_need(item.get('is_archived') is False and item.get('is_autoarchived') is False
        and item.get('errors') == [] and statuses.get('status') == 'price_sent'
        and statuses.get('is_created') is True and statuses.get('status_failed') == ''
        and statuses.get('moderate_status') == 'approved' and statuses.get('validation_status') == 'success'
        and item.get('visibility_details') == {'has_price':True,'has_stock':True}
        and item.get('availabilities') == [{'availability':'AVAILABLE','reasons':[],
            'sku':context['sku'],'source':'sds'}], 'OFFICIAL_ONSALE_NOT_PROVED')
    # The original producer selected this dictionary profile. Rebuilding its
    # original frozen request must match the immutable accepted dispatch SHA;
    # current attribute values can never certify themselves.
    attributes = normalized[0]['attributes']
    brand = attributes.get('85')
    _ozon_need(type(brand) is list and len(brand) == 1, 'ORIGINAL_ATTRIBUTE_PROFILE_REQUIRED')
    profile = {'schema_version':'ozon-official-profile-resolution/v1','resolution':'EXACT',
        'description_category_id':int(expected['category']['id']), 'type_id':attribute_row.get('type_id'),
        'required_attributes':{'brand':{'attribute_id':85,**brand[0]},
            'model_name':{'attribute_id':9048},'product_type':{'attribute_id':8229,
                'dictionary_value_id':attribute_row.get('type_id'),'value':expected['category']['name']}}}
    import_item = build_ozon_import_item_from_frozen_variant({**expected,'official_profile':profile})
    _ozon_need(_request_sha('import_variant', ('/v3/product/import',{'items':[import_item]}), {})
        == context['import_request_digest'], 'ORIGINAL_ACCEPTED_IMPORT_FACTS_CHANGED')
    for row in import_item['attributes']:
        if row['id'] == 8229 and '8229' not in attributes:
            # Ozon returns product type as type_id instead of this optional
            # attribute row. Both current official rows must bind that type.
            _ozon_need(item.get('type_id') == attribute_row.get('type_id') == profile['type_id'],
                'OFFICIAL_PRODUCT_TYPE_CHANGED')
        else:
            _ozon_need(attributes.get(str(row['id'])) == row['values'], 'OFFICIAL_ATTRIBUTE_CHANGED')
    _ozon_need(normalized[0]['id'] == context['product_id']
        and _classify_variant(expected, normalized[0], OzonDispatchFact(outcome='ACCEPTED',
            task_id=str(context['import_task_id']))) == 'PUBLISHED'
        and normalized[0]['images'] == context['ordered_gallery'], 'FROZEN_CONTENT_OR_ORDER_CHANGED')
    stock = stocks[0]
    _ozon_need(stock.get('offer_id') == offer and stock.get('product_id') == context['product_id']
        and stock.get('sku') == context['sku'] and stock.get('warehouse_id') == context['warehouse_id']
        and type(stock.get('stock')) is int and stock['stock'] == expected['stock_quantity']
        and stock.get('free_stock') == expected['stock_quantity'] and stock.get('reserved') == 0,
        'EXACT_FROZEN_STOCK_REQUIRED')
    return {'source':'official_ozon_seller_api','verified':True,'region':'RU','seller_sku':offer,
        'account_id':context['account_id'],'product_id':context['product_id'],'sku':context['sku'],
        'warehouse_id':context['warehouse_id'],'snapshot_digest':context['snapshot_digest'],
        'source_state_digest':context['source_state_digest'],
        'registered_proof_ref':context['registered_proof_ref'],
        'checks':{name:True for name in ('single_exact_sku','title','description','price','currency',
            'ordered_approved_images','category','attributes','parcel','active','stock','stock_budget_spent')},
        'official_responses':responses,'external_writes_performed':[]}


def _ozon_official_observation(active, gate, target):
    """One platform, at most five exact SDK READs, no retry/publish callable."""
    from shared_platform.ozon_runtime_credentials import required_pinned_ozon_credentials
    from modules.ozon.client import ozon_post_bound
    from shared_platform.product_publication_live_dependencies import OfficialOzonV4Transport
    try:
        with active.store._connect_readonly() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',
                (gate['run']['run_id'], target['target_label'])).fetchone()
            submission = db.execute('SELECT * FROM release_target_submissions WHERE run_id=? AND target_label=?',
                (gate['run']['run_id'], target['target_label'])).fetchone()
            context = _ozon_context(active, db, row, submission)
        pinned = required_pinned_ozon_credentials()
        _ozon_need(pinned.account_id == context['account_id'], 'PINNED_ACCOUNT_CHANGED')
        requests = _ozon_read_requests(context); responses = []
        def read_once(path, body):
            _ozon_need(len(responses) < len(requests) and (path,body) == requests[len(responses)],
                'ENDPOINT_OR_READ_BUDGET_CHANGED')
            value = ozon_post_bound(path, body, client_id=pinned.account_id, api_key=pinned.api_key, timeout=30)
            responses.append({'path':path,'body':body,'response':value})
            return value
        transport = OfficialOzonV4Transport(post=read_once, account_id=pinned.account_id)
        transport.readback_variants((context['expected']['offer_id'],))
        transport.readback_stocks((context['expected']['offer_id'],))
        return _ozon_verified_evidence(context, responses)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
        # A capability HOLD ends this observation round. It cannot silently
        # spend another five READs via the original pending<3 scheduler.
        return {'verified':False,'capability':'OZON_EXACT_OFFICIAL_READBACK_HOLD',
            'error_type':type(error).__name__,'external_writes_performed':[]}


def observe_submissions(runtime, decision_id, *, target_labels=None, media_equivalence_ref=None):
    """Current decision recheck -> exact submission READ -> original store CAS."""
    from shared_platform.native_sole_final_execution import _NativeExecution, _ACTIVE
    from shared_platform.native_approved_discount_prices import B_DECISION
    with runtime.operation() as service:
        actor = service._actor()
        with runtime.store._connect_readonly() as db:
            db.execute('BEGIN')
            service._decision(db, decision_id, owner_sid=actor['owner_sid'])
            row = db.execute('SELECT plan_id FROM native_sole_final_decisions WHERE decision_id=?',
                             (decision_id,)).fetchone()
            plan_id = row['plan_id']
    plan = runtime.store.get_plan(plan_id)
    active = _NativeExecution(service, decision_id, runtime)
    active.plan_id = plan_id
    token = _ACTIVE.set(active)
    try:
        data = {'offer_id': plan['product_id'], 'plan_id': plan_id,
                'confirmation_token': plan['confirmation_token'], 'confirm_publish': True}
        gate, failure = active.gate(data, store=runtime.store)
        if failure:
            return {'pending': False, 'error': failure[1]['error'], 'observations': [],
                    'external_writes_performed': []}
        run = gate['run']
        observations = []
        retry_read = False
        from shared_platform.native_publication_cancellation import eligible_rows
        targets=eligible_rows((run or {}).get('targets') or [],gate.get('execution_cancellation'))
        if target_labels is not None:
            _provider_need(type(target_labels) is tuple and bool(target_labels) and len(set(target_labels))==len(target_labels)
                and set(target_labels)<={row['target_label'] for row in targets},'EXACT_SELECTED_READBACK_TARGETS_REQUIRED')
            targets=[row for row in targets if row['target_label'] in target_labels]
        media_cache={}  # one encode per unique pinned asset in this call only
        for target in targets:
            if target['status']=='FAILED' and target['target_label'].startswith('tiktok:LH_'):
                try:observations.append(_observe_tiktok_failed(active,gate,data,target,media_equivalence_ref,media_cache))
                except (ValueError,OSError,KeyError,TypeError,RuntimeError,sqlite3.Error) as error:
                    observations.append({'target_label':target['target_label'],'verified':False,
                        'capability':'TIKTOK_ORIGINAL_PUBLISH_READONLY_HOLD','error_type':type(error).__name__,
                        'error':str(error),'external_writes_performed':[]})
                continue
            if decision_id==B_DECISION and target['target_label']==_VN and target['status']=='FAILED' and eligible_shopee_failed_readback(runtime.store,decision_id):
                try:
                    observations.append(_observe_vn_failed(active,gate,data))
                except (ValueError,OSError,KeyError,TypeError,RuntimeError) as error:
                    observations.append({'target_label':_VN,'verified':False,
                        'capability':'VN_STRICT_GET_ONLY_CANONICAL_HOLD','error_type':type(error).__name__,
                        'external_writes_performed':[]})
                continue
            if (target['status']=='FAILED' and target['target_label'].startswith('shopee:')
                    and eligible_provider_failed_readback(runtime.store,decision_id)):
                try:
                    observations.append(_observe_provider_failed(active,gate,data,target))
                except (ValueError,OSError,KeyError,TypeError,RuntimeError,sqlite3.Error) as error:
                    observations.append({'target_label':target['target_label'],'verified':False,
                        'capability':'ORIGINAL_PROVIDER_RESPONSE_READONLY_HOLD',
                        'error_type':type(error).__name__,'external_writes_performed':[]})
                continue
            if target['status'] != 'SUBMITTED_UNVERIFIED':
                continue
            submission = target.get('submission') or {}
            if not submission.get('evidence_digest') or submission.get('external_id') != target['external_id']:
                # Invalid local receipt cannot authorise a READ/CAS for this
                # target, nor prevent independent targets from being observed.
                observations.append({'target_label': target['target_label'], 'verified': False,
                    'capability': 'NATIVE_SUBMISSION_RECEIPT_REQUIRED', 'external_writes_performed': []})
                continue
            try:
                evidence = (_ozon_official_observation(active, gate, target)
                    if target['target_label'] == 'ozon:RU'
                    else _official_observation(gate['payload'], target))
                if evidence.get('verified') is True and target['target_label'] == 'ozon:RU':
                    # Same original actor/current/frozen/common gate after I/O;
                    # no reader-mode selector is introduced into the write gate.
                    refreshed, changed = active.gate(data, store=runtime.store)
                    if changed:
                        raise ApprovalBlocked('NATIVE_OZON_READBACK_CURRENT_CHANGED')
                if evidence.get('verified') is True:
                    proof = _SubmissionReadback(run['run_id'], target['target_label'],
                        target['idempotency_key'], target['attempts'], target['external_id'],
                        submission['evidence_digest'],
                        json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
                    runtime.store._record_native_submission_readback(proof)
                elif not evidence.get('capability'):
                    retry_read = True
                observations.append({'target_label': target['target_label'], **evidence})
            except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
                # A failed READ never clears the original submission or retries
                # a write. The schedule is bounded even for unavailable APIs.
                if target['target_label'] == 'ozon:RU':
                    observations.append({'target_label': target['target_label'], 'verified': False,
                        'capability': 'OZON_EXACT_OFFICIAL_READBACK_HOLD',
                        'error_type': type(error).__name__, 'external_writes_performed': []})
                    continue
                retry_read = True
                observations.append({'target_label': target['target_label'], 'verified': False,
                    'error': str(error), 'external_writes_performed': []})
        return {'pending': retry_read, 'observations': observations,
                'run': runtime.store.get_run(run['run_id']) if run else None,
                'external_writes_performed': []}
    finally:
        _ACTIVE.reset(token)
