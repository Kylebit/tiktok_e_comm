"""Governed SH promotion adapter using one durable add and official readback.

The existing OneClick ledger records the sole write intent before the original
SDK POST. Returned errors, malformed replies and partial responses never prove
zero writes: only complete exact official membership readback closes an intent.
Request shape is retained from the original project SDK consumer and historical
execution reports, not a newly retrieved official documentation response.
"""
from collections.abc import Mapping
from decimal import Decimal,InvalidOperation
import hashlib
import json
import re
import time

from modules.tiktok.oneclick_promotion import TikTokPromotionBlocked,TikTokPromotionPreDispatchError
from shared_platform.postpublish_promotions import approved_promotion_action_policy,promotion_target_policy

LIST='/api/v2/discount/get_discount_list'
DETAIL='/api/v2/discount/get_discount'
MODEL='/api/v2/product/get_model_list'
ADD='/api/v2/discount/add_discount_item'
WRITE_CLASS='shopee:promotion:discount'
SCHEMA='oneclick-shopee-promotion/v2'
MAX_PAGES=100

def promotion_read_policy_digest():
    return _digest({'schema':SCHEMA,'list':LIST,'detail':DETAIL,'models':MODEL,
        'ongoing_unique':True,'complete_pages':True,'writes':ADD,'request_shape':'original-project-consumer-e785e940/v1','unknown':'never_replay',
        'existing_exact':'ZERO_WRITE_OFFICIAL_READBACK'})

def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def _hold(code):
    raise TikTokPromotionBlocked(code,code,category='SYSTEMIC_CONTRACT')

def _decimal(value):
    try:result=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):_hold('shopee_promotion_price_invalid')
    if type(value) is bool or not result.is_finite() or result<=0:_hold('shopee_promotion_price_invalid')
    return result

def _positive(value):
    if type(value) is not int or value<=0:_hold('shopee_promotion_identity_invalid')
    return value

def _identity(prerequisite):
    label=prerequisite['target_label'];external=prerequisite['external_id']
    if label not in {'shopee:MY','shopee:PH','shopee:TH','shopee:VN'}:_hold('shopee_promotion_target_invalid')
    matches=re.findall(r'(?:^|,)shopee:([0-9]+):item:([0-9]+)(?=,|$)',external)
    # MY's original accepted-task prefix is retained, never used as item ID.
    if len(matches)!=1:_hold('shopee_promotion_official_item_identity_missing')
    return label.split(':')[1],int(matches[0][0]),int(matches[0][1])

def _transport(action,prerequisite):
    from shared_platform.native_companion_promotions import _held
    from shared_platform.native_canonical_provider_edges import _existing_shopee_context
    from modules.shopee.auth import token_path
    from modules.shopee.client import shop_get
    scope=_held()
    if scope is None or scope['proofs'].get(action)!=prerequisite:_hold('shopee_promotion_native_scope_required')
    region,shop_id,item_id=_identity(prerequisite)
    token_file=token_path().resolve(strict=True);pin=hashlib.sha256(token_file.read_bytes()).digest()
    def guard():
        if _held() is not scope:_hold('shopee_promotion_native_scope_changed')
        if hashlib.sha256(token_file.read_bytes()).digest()!=pin:_hold('shopee_promotion_auth_source_changed')
        ctx=_existing_shopee_context(region) # Existing rows only; no refresh/sync.
        if ctx.shop_id!=shop_id:_hold('shopee_promotion_shop_binding_changed')
        return ctx
    def read(path,params):
        if path not in {LIST,DETAIL,MODEL}:_hold('shopee_promotion_endpoint_not_readonly')
        ctx=guard()
        # Existing single-hop SDK; guarded calls force attempts=1/no curl.
        try:value=shop_get(path,shop_id,ctx.shop_token,dict(params),call_guard=guard)
        except (OSError,ValueError,RuntimeError,TypeError):
            raise TikTokPromotionBlocked('shopee_promotion_official_read_failed',
                'Shopee promotion official READ failed; no write attempted',category='CAPABILITY') from None
        if (not isinstance(value,Mapping) or not (value.get('error') is None or type(value.get('error')) is str and value['error'] in {'','-'})
                or not isinstance(value.get('response'),Mapping)):
            _hold('shopee_promotion_official_read_failed')
        return value['response']
    def add(body):
        from modules.shopee.client import shop_post
        ctx=guard()
        # The SDK is attempts=1/no curl. This callable is obtained only inside
        # the original native scope; no raw transport is exposed as a route.
        return shop_post(ADD,shop_id,ctx.shop_token,body,call_guard=guard)
    read.add=add
    return read

def _pages(read,path,key,params,id_key):
    rows=[];seen=set()
    for page in range(1,MAX_PAGES+1):
        response=read(path,{**params,'page_no':page,'page_size':100})
        current=response.get(key);more=response.get('more')
        if type(current) is not list or type(more) is not bool:_hold('shopee_promotion_pagination_invalid')
        for row in current:
            if not isinstance(row,Mapping):_hold('shopee_promotion_page_row_invalid')
            identity=_positive(row.get(id_key))
            if identity in seen:_hold('shopee_promotion_pagination_duplicate')
            seen.add(identity);rows.append(dict(row))
        if more is False:return rows
        if not current:_hold('shopee_promotion_pagination_no_progress')
    _hold('shopee_promotion_page_limit')

def _observe(action,prerequisite,expected):
    region,shop_id,item_id=_identity(prerequisite);read=_transport(action,prerequisite)
    models=read(MODEL,{'item_id':item_id}).get('model')
    if type(models) is not list or not models:_hold('shopee_promotion_complete_models_unavailable')
    model_ids=[];skus=[]
    for row in models:
        if not isinstance(row,Mapping):_hold('shopee_promotion_model_invalid')
        model_ids.append(_positive(row.get('model_id')));skus.append(row.get('model_sku'))
        prices=row.get('price_info')
        if (type(prices) is not list or len(prices)!=1 or not isinstance(prices[0],Mapping)
                or prices[0].get('currency')!=expected['currency']
                or _decimal(prices[0].get('original_price'))!=_decimal(expected['list_price'])):
            _hold('shopee_promotion_official_base_price_changed')
    if len(model_ids)!=len(set(model_ids)) or sorted(skus)!=sorted(expected['seller_skus']):
        _hold('shopee_promotion_complete_sku_set_changed')
    activities=_pages(read,LIST,'discount_list',{'discount_status':'ongoing'},'discount_id')
    now=int(time.time())
    if len(activities)!=1:_hold('shopee_unique_ongoing_discount_unavailable')
    activity=activities[0]
    if (type(activity.get('start_time')) is not int or type(activity.get('end_time')) is not int
            or not activity['start_time']<=now<activity['end_time']
            or ('discount_status' in activity and activity['discount_status']!='ongoing')):
        _hold('shopee_promotion_activity_window_invalid')
    items=_pages(read,DETAIL,'item_list',{'discount_id':activity['discount_id']},'item_id')
    members=[r for r in items if r['item_id']==item_id]
    if len(members)>1:_hold('shopee_promotion_member_ambiguous')
    exact=False
    if members:
        values=members[0].get('model_list')
        if type(values) is not list or len(values)!=len(model_ids):_hold('shopee_promotion_member_models_changed')
        observed={}
        for value in values:
            if not isinstance(value,Mapping):_hold('shopee_promotion_member_invalid')
            mid=_positive(value.get('model_id'))
            if mid in observed:_hold('shopee_promotion_member_model_duplicate')
            observed[mid]=_decimal(value.get('model_promotion_price'))
        exact=set(observed)==set(model_ids) and all(v==_decimal(expected['sale_after_discount']) for v in observed.values())
    fresh_now=int(time.time())
    if not activity['start_time']<=fresh_now<activity['end_time']:
        _hold('shopee_promotion_activity_expired_after_membership_read')
    identity={'shop_id':shop_id,'item_id':item_id,'model_ids':sorted(model_ids),
        'seller_skus':sorted(skus),'currency':expected['currency'],'list_price':expected['list_price']}
    return {'activity_id':activity['discount_id'],'start_time':activity['start_time'],
        'end_time':activity['end_time'],'activity_identity_digest':_digest({k:activity[k] for k in ('discount_id','start_time','end_time')}),
        'product_identity_digest':_digest(identity),'membership_exact':exact,'member_present':bool(members),**identity}

def prepare_postpublish_promotion(request):
    from shared_platform.native_approved_discount_prices import retained_discount_price
    from modules.tiktok.oneclick_promotion import _approved_model_skus
    action=request.target_label;policy=promotion_target_policy(action)
    if policy['channel']!='shopee':_hold('shopee_promotion_target_invalid')
    approval=approved_promotion_action_policy(request.immutable_plan_payload,action)
    expected=retained_discount_price(request.immutable_plan_payload,policy['prerequisite_target'])
    expected={k:expected[k] for k in ('currency','list_price','sale_after_discount')}
    expected['seller_skus']=list(_approved_model_skus(request.immutable_plan_payload))
    prerequisite=request.prerequisite_context
    if not isinstance(prerequisite,Mapping) or prerequisite.get('target_label')!=policy['prerequisite_target']:
        _hold('shopee_promotion_official_prerequisite_missing')
    observed=_observe(action,prerequisite,expected)
    if observed['member_present'] and not observed['membership_exact']:
        return {'classification':'BLOCKED_CAPABILITY','reason_category':'CONTENT','reason_scope':'TARGET',
            'reason_code':'shopee_promotion_existing_member_conflict',
            'reason_detail':'Existing member conflicts with the frozen price; no update or delete is authorized',
            'command':None,'proof':None,'manual_after_submit':False}
    command={'schema_version':SCHEMA,'target_label':action,'idempotency_key':request.idempotency_key,
        'mode':'VERIFY_EXISTING_MEMBERSHIP_NO_WRITE' if observed['membership_exact'] else 'ADD_TO_EXISTING_DISCOUNT','expected':expected,'prerequisite':dict(prerequisite),
        'discount_percent':approval['discount_percent'],'action_policy_digest':approval['action_policy_digest'],**observed}
    return {'classification':'EXACT_READY_AUTOMATIC','reason_category':'CAPABILITY','reason_scope':'TARGET',
        'reason_code':'shopee_discount_preflight_exact','reason_detail':'Exact original activity, complete model identity and frozen decimal price; one add at most',
        'command':command,'proof':{'schema_version':SCHEMA,'command_digest':_digest(command)},'manual_after_submit':False}

def _unknown(detail):
    from modules.tiktok.oneclick_promotion import TikTokPromotionDispatchError
    error=TikTokPromotionDispatchError(detail,unknown=True,external_write_count=None,lower_bound=0,upper_bound=1)
    error.external_writes=(WRITE_CLASS,)
    return error

def _body(command):
    # Use JSON numeric prices as in the retained original consumer. Never round
    # PH/TH to integers. Fail before intent if JSON round-trip loses value.
    price=_decimal(command['expected']['sale_after_discount'])
    number=int(price) if price==price.to_integral_value() else float(price)
    if Decimal(str(json.loads(json.dumps(number,allow_nan=False))))!=price:
        raise TikTokPromotionPreDispatchError('shopee_discount_numeric_precision_unsupported')
    return {'discount_id':command['activity_id'],'item_list':[{'item_id':command['item_id'],
        'purchase_limit':0,'model_list':[{'model_id':mid,'model_promotion_price':number}
            for mid in command['model_ids']]}]}

def _success(command,write_count):
    expected={k:command[k] for k in ('activity_identity_digest','product_identity_digest','discount_percent')}
    evidence={**expected,'readback_exact':True}
    return {'canonical_status':'SUCCEEDED','reason_category':'CAPABILITY','reason_scope':'TARGET',
        'reason_code':'shopee_discount_official_readback_exact','reason_detail':'Complete official member/model prices match the frozen command',
        'external_writes':(WRITE_CLASS,) if write_count else (),
        'external_write_count':write_count,'confirmed_external_write_count_lower_bound':write_count,
        'possible_external_write_count_upper_bound':write_count,
        'external_id':'sha256:'+command['activity_identity_digest'],
        'submission_accepted':bool(write_count),'readback_verified':True,'dispatch_outcome_unknown':False,
        'evidence':{'schema_version':'shopee-promotion-readback/v1','existing_membership_exact':write_count==0,
            'official_readback_exact':True,'evidence_digest':_digest(evidence),**expected}}

def dispatch_postpublish_promotion(request):
    command=request.command.get('payload');proof=request.proof.get('payload')
    if (not isinstance(command,Mapping) or not isinstance(proof,Mapping)
            or command.get('schema_version')!=SCHEMA or command.get('target_label')!=request.target_label
            or command.get('idempotency_key')!=request.idempotency_key
            or command.get('mode') not in {'VERIFY_EXISTING_MEMBERSHIP_NO_WRITE','ADD_TO_EXISTING_DISCOUNT'}
            or proof.get('schema_version')!=SCHEMA or proof.get('command_digest')!=_digest(command)):
        raise TikTokPromotionPreDispatchError('shopee_promotion_prepared_identity_changed')
    # This observer rechecks the native scope, pinned token source, complete
    # pagination, exact model SKU set, currency and unchanged original prices.
    observed=_observe(request.target_label,command['prerequisite'],command['expected'])
    stable=tuple(k for k in observed if k not in {'membership_exact','member_present'})
    if any(observed[k]!=command[k] for k in stable):
        raise TikTokPromotionPreDispatchError('shopee_promotion_official_identity_drifted')
    if observed['membership_exact']:
        return _success(command,0)
    if observed['member_present'] or command['mode']!='ADD_TO_EXISTING_DISCOUNT':
        raise TikTokPromotionPreDispatchError('shopee_promotion_official_membership_drifted')
    body=_body(command)
    progress=getattr(request,'progress_recorder',None)
    if not callable(progress):
        raise TikTokPromotionPreDispatchError('shopee_promotion_durable_intent_recorder_required')
    transport=_transport(request.target_label,command['prerequisite'])
    if not callable(getattr(transport,'add',None)):
        raise TikTokPromotionPreDispatchError('shopee_promotion_original_sdk_writer_required')
    progress(request,(WRITE_CLASS,),'promotion_apply-1',
        {'prepared_command_digest':getattr(request,'prepared_command_digest'),'body_digest':_digest(body)},
        external_write_count=None,confirmed_external_write_count_lower_bound=0,
        possible_external_write_count_upper_bound=1,write_boundary='PRE_INVOCATION_INTENT')
    # After this durable boundary all failures retain UNKNOWN, even a structured
    # error_list. Never infer a zero-write rejection or retry the POST.
    try:
        response=transport.add(body)
    except Exception:
        raise _unknown('shopee_discount_add_transport_outcome_unknown') from None
    try:
        after=_observe(request.target_label,command['prerequisite'],command['expected'])
        exact=after['membership_exact'] and all(after[k]==command[k] for k in stable)
    except Exception:
        raise _unknown('shopee_discount_add_requires_official_reconciliation') from None
    if not exact:
        raise _unknown('shopee_discount_add_not_yet_exact_no_resend')
    progress(request,(WRITE_CLASS,),'promotion_apply-1',
        {'official_readback_digest':_digest(after),'response_shape_digest':_digest(sorted(response)) if isinstance(response,Mapping) else None},
        external_write_count=1,confirmed_external_write_count_lower_bound=1,
        possible_external_write_count_upper_bound=1,write_boundary='POST_RESPONSE_CONFIRMED')
    return _success(command,1)
