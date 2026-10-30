"""Read exact retained 403/0989 price facts, never execution authority.

Only the original immutable payload can use this missing-projection fallback.
The Shopee receipt grants its four rates, not a provider action or adapter.
"""
from pathlib import Path
from collections.abc import Mapping
from decimal import Decimal,InvalidOperation
import hashlib,json
from shared_platform.postpublish_promotions import PostpublishPromotionContractError

PAYLOAD='715e2b6e14103dbcdb12912834aa4571f2430a084500fbf02971a0d3d47b0de4'
PLAN='r3-marketplace:4039931504:b21fbd9f11f01e3d04fc0780'
DECISION='native-final-decision:da22e734faad76bf1de4b00ffca5dba3421682d0ce94f68dc6595b7440935ef6'
CANDIDATE='87dae52c9dda4b7c822e07d2071e2a1d3c87466901e3f05751cb26954766026f'
SH_TARGETS=['shopee:MY','shopee:PH','shopee:TH','shopee:VN']
AUTHORITY_PATH=Path('E:/OrbitEvidence/4039931504-publication-20261019/20261021/ROOT_SHOPEE_FOUR_DISCOUNT_30PCT_AUTHORITY_20261021.json')
AUTHORITY_SHA='684207d72d91d5096bc97a4ade0b6011288b248a7c66e39f77cbad061b315cb6'
def _need(ok,code):
 if not ok:raise PostpublishPromotionContractError('RETAINED_DISCOUNT_'+code)
def _digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def _decimal(v):
 try:d=Decimal(str(v))
 except (InvalidOperation,ValueError,TypeError):raise PostpublishPromotionContractError('RETAINED_DISCOUNT_DECIMAL_INVALID') from None
 _need(type(v) is not bool and d.is_finite() and d>0,'DECIMAL_INVALID');return d
def _one(rows,field,value):
 _need(type(rows) is list,'ROWS_REQUIRED');found=[r for r in rows if isinstance(r,Mapping) and r.get(field)==value]
 _need(len(found)==1,'UNIQUE_ROW_REQUIRED');return found[0]
def _money(v):return format(v.normalize(),'f')
def _sh_rate():
 try:raw=AUTHORITY_PATH.read_bytes();a=json.loads(raw)
 except (OSError,ValueError):raise PostpublishPromotionContractError('RETAINED_DISCOUNT_RATE_AUTHORITY_UNAVAILABLE') from None
 _need(hashlib.sha256(raw).hexdigest()==AUTHORITY_SHA,'RATE_AUTHORITY_CHANGED')
 _need(a.get('schema')=='user-shopee-four-store-discount-rate-addendum/v1'
       and a.get('status')=='EXPLICIT_USER_RATE_AUTHORITY_RECORDED_NOT_EXECUTED'
       and (a.get('offer_id'),a.get('seller_sku'),a.get('plan_id'),a.get('candidate_digest'),a.get('payload_digest'),a.get('decision_id'))==('4039931504','0989',PLAN,CANDIDATE,PAYLOAD,DECISION)
       and a.get('target_labels')==SH_TARGETS and type(a.get('discount_percent')) is int and a['discount_percent']==30 and a.get('price_multiplier')=='0.70','EXACT_FOUR_RATE_AUTHORITY_REQUIRED')
 return Decimal(a['discount_percent']),{'path':str(AUTHORITY_PATH),'sha256':AUTHORITY_SHA}

def retained_discount_price(payload,target,*,expected_currency=None):
 """No transport, job, claim, approval or business mutation is produced."""
 if isinstance(payload,Mapping) and payload.get('product_id')==B_OFFER:
  return _b0431_price(payload,target,expected_currency=expected_currency)
 _need(isinstance(payload,Mapping) and payload.get('product_id')=='4039931504' and payload.get('seller_sku')=='0989' and payload.get('plan_id')==PLAN,'EXACT_ORIGINAL_PRODUCT_REQUIRED')
 _need(_digest(payload)==PAYLOAD,'IMMUTABLE_PAYLOAD_CHANGED')
 from shared_platform.publication_r3_image_bridge import validate_r2_identity
 binding=payload['r3_marketplace_binding'];documents=binding['documents']
 identity=validate_r2_identity(documents);_need(identity==binding['r2_identity'],'R1_FIRST_R2_IDENTITY_CHANGED')
 selected=payload['pricing']['selected_targets'].get(target)
 _need(isinstance(selected,Mapping) and 'store_prices' not in selected,'MISSING_PROJECTION_ONLY')
 price=_one(selected.get('sku_prices'),'model_sku','0989')
 _need(len(selected['sku_prices'])==1,'SINGLE_MODEL_REQUIRED')
 amount=_decimal(price.get('list_price'));currency=price.get('currency')
 _need(type(currency) is str and currency==currency.upper() and (expected_currency is None or currency==expected_currency),'CURRENCY_CHANGED')
 candidate=binding['reviewed_candidate_facts'];_need(target in candidate['target_labels'] and 'promotion:'+target in [a.get('action_target') for a in candidate['companion_actions']['actions']],'ORIGINAL_COMPANION_REQUIRED')
 reviewed=_one(candidate['review_manifest']['targets'],'target_label',target)
 approved=_one(reviewed.get('prices'),'model_sku','0989')
 _need(len(reviewed['prices'])==1 and _decimal(approved.get('amount'))==amount and approved.get('currency')==currency,'REVIEWED_PRICE_CHANGED')
 variant=_one(candidate['review_manifest']['variants'],'model_sku','0989')
 first=_one(documents['first_review']['targets'],'target',target)
 r1=_one(documents['round1_snapshot']['fact_snapshot']['targets'],'target',target)
 _need(first['price']==r1['price'],'FIRST_REVIEW_PRICE_CHANGED')
 authority=None
 if target in SH_TARGETS:
  percent,authority=_sh_rate();sale=amount*(Decimal(1)-percent/100);origin='EXPLICIT_USER_FOUR_SH_RATE_ADDENDUM'
 elif target in {'tiktok:MX','tiktok:GB'}:
  region='mx' if target=='tiktok:MX' else 'uk'
  origin_value=documents['round1_snapshot']['fact_snapshot']['product_facts']['parcel_planning']['pricing_impact'][variant['variant_key']]['value'][region]
  percent=_decimal(origin_value['header_meta']['discount_reserve_pct']);sale=_decimal(origin_value['sale_after_discount_local'])
  _need(_decimal(origin_value['list_price'])==amount and origin_value['currency']==currency,'R1_VALUE_PRICE_CHANGED');origin='ROUND1_SELECTED_VALUE_'+region
 elif target.startswith(('tiktok:LH_','tiktok:HB_')):
  quote=_one(first['price']['sku_prices'],'model_sku','0989');calculation=quote['calculation']
  _need(len(first['price']['sku_prices'])==1 and _decimal(quote['amount'])==amount and quote['currency']==currency,'FIRST_SELECTED_PRICE_CHANGED')
  percent=_decimal(calculation['inputs']['discount_reserve_pct']);sale=_decimal(calculation['result']['sale_after_discount'])
  _need(_decimal(calculation['result']['list_price'])==amount and calculation['result']['currency']==currency,'FIRST_CALCULATION_CHANGED');origin='FIRST_REVIEW_EXACT_TARGET_CALCULATION'
 else:raise PostpublishPromotionContractError('RETAINED_DISCOUNT_TARGET_OUT_OF_SCOPE')
 _need(percent==percent.to_integral_value() and percent<100 and sale==amount*(Decimal(1)-percent/100),'EXACT_RATE_ARITHMETIC_REQUIRED')
 # No rounding rule is invented. All fourteen retained values are exact at
 # their original decimal scale. Provider submission precision is a later gate.
 result={'schema':'retained-approved-discount-price/v1','offer_id':'4039931504','plan_id':PLAN,'target_label':target,'model_sku':'0989','currency':currency,'list_price':_money(amount),'discount_percent':int(percent),'sale_after_discount':_money(sale),
         'payload_digest':PAYLOAD,'round1_snapshot_digest':identity['round1_snapshot_digest'],'first_review_digest':identity['first_review_digest'],'r2_identity_digest':_digest(identity),'origin':origin,'rate_authority_ref':authority,'rounding_applied':False,'execution_authorized':False}
 result['binding_digest']=_digest(result);return result


# This is a second, private immutable lineage, not an extension of 403's rate
# authority. A file/quote alone never opens its execution context.
B_OFFER='8412287877065586543'
B_TASK='TASK-20261023-001'
B_PLAN='r3-marketplace:8412287877065586543:07707b4b7901ca9b97b08acd'
B_PAYLOAD='25e66f7250d31cbb39ff8d0e7276b439c851e2ed147b6048040f533addebcf64'
B_DECISION='native-final-decision:7a10d3ac157c55c6949b663c5b63194b2c43eab81b0fee88eda6aae412838fe2'
B_CANDIDATE='c5eacc9725ecefa012af02c6d4f5130c6098c9bedab65e2159357a708c73a5cc'
B_RUN='release-run:25e66f7250d31cbb39ff8d0e'
B_MODELS=['2000431','2000432','2000433']
B_TARGETS=('tiktok:LH_PH','tiktok:LH_MY','tiktok:LH_TH','tiktok:LH_VN',
           'shopee:PH','shopee:MY','shopee:TH','shopee:VN')
B_SH={'shopee:PH':(1527371343,'47970054181','PHP','441','308.70'),
      'shopee:MY':(1561117812,'44920064243','MYR','34','23.80'),
      'shopee:TH':(1561124013,'53420052020','THB','251','175.70'),
      'shopee:VN':(1723948773,'52420043241','VND','294000','205800.00')}
B_DECISION_SHA='033172b36222dbe64b1364068984fcb607b399516489dd3f398ae294c448ed20'
B_EVENT='source_code_approved_b0431_original_companion_discount_continuation'


def read_b0431_discount_decision(ref):
 """Pure pinned user-intent reader for the source producer; not a grant."""
 _need(isinstance(ref,Mapping) and set(ref)=={'path','sha256'}
       and ref.get('sha256')==B_DECISION_SHA,'B0431_DECISION_PIN_REQUIRED')
 try:
  path=Path(ref['path']);_need(path.is_absolute(),'B0431_ABSOLUTE_REFERENCE_REQUIRED')
  raw=path.read_bytes();doc=json.loads(raw)
 except (OSError,TypeError,ValueError) as error:
  raise PostpublishPromotionContractError('RETAINED_DISCOUNT_B0431_DECISION_UNAVAILABLE') from error
 _need(hashlib.sha256(raw).hexdigest()==B_DECISION_SHA,'B0431_DECISION_CHANGED')
 _need(isinstance(doc,Mapping) and doc.get('schema')=='b0431-shopee-discount-supplemental-user-intent/v1'
       and doc.get('status')=='EXPLICIT_USER_INTENT_REQUIRES_GENUINE_SERVER_BOUND_INTAKE_NOT_CALLER_AUTHORITY'
       and (doc.get('task_id'),doc.get('offer_id'),doc.get('plan_id'),doc.get('payload_digest'),
            doc.get('candidate_digest'),doc.get('original_decision_id'),doc.get('original_review_digest'))
       ==(B_TASK,B_OFFER,B_PLAN,B_PAYLOAD,B_CANDIDATE,B_DECISION,'sha256:'+B_DECISION.split(':')[1])
       and doc.get('user_answer')=='30%' and list(doc.get('targets') or {})==list(B_SH)
       and doc.get('original_content_and_approval_unchanged') is True
       and doc.get('new_product_review') is False and doc.get('new_activity') is False
       and doc.get('use_only_unique_current_ongoing_direct_discount') is True
       and doc.get('activity_catalogue_full_pages_required') is True
       and doc.get('full_exact_item_and_three_model_readback_required') is True,'B0431_EXACT_DECISION_REQUIRED')
 for label,(shop,item,currency,amount,sale) in B_SH.items():
  target=doc['targets'][label];rows=target.get('models')
  _need(target.get('shop_id')==shop and target.get('existing_item_id')==item
        and type(rows) is list and [r.get('model_sku') for r in rows]==B_MODELS,'B0431_EXACT_SH_MODELS_REQUIRED')
  for row in rows:
   _need(row.get('currency')==currency and row.get('original_approved_list_price')==amount
         and type(row.get('discount_percent_off')) is int and row['discount_percent_off']==30
         and row.get('discount_factor')=='0.70' and row.get('exact_sale_price')==sale
         and _decimal(sale)==_decimal(amount)*Decimal('0.70'),'B0431_EXACT_SH_PRICE_REQUIRED')
 return doc


def _b0431_price(payload,target,*,expected_currency):
 from shared_platform.native_companion_promotions import _held
 scope=_held()
 _need(scope is not None and scope.get('plan_id')==B_PLAN and scope.get('run_id')==B_RUN
       and scope['active'].decision_id==B_DECISION
       and 'b0431_discount_decision_ref' in scope,'B0431_PROTECTED_SOURCE_CONTEXT_REQUIRED')
 doc=read_b0431_discount_decision(scope['b0431_discount_decision_ref'])
 _need(_digest(doc)==scope.get('b0431_discount_decision_digest'),'B0431_SIGNED_DECISION_CHANGED')
 _need(payload.get('plan_id')==B_PLAN and payload.get('seller_sku')=='2000431'
       and _digest(payload)==B_PAYLOAD and target in B_TARGETS,'B0431_ORIGINAL_PAYLOAD_TARGET_REQUIRED')
 from shared_platform.publication_r3_image_bridge import validate_r2_identity
 binding=payload['r3_marketplace_binding'];documents=binding['documents'];identity=validate_r2_identity(documents)
 _need(identity==binding['r2_identity'],'B0431_R1_R2_CHANGED')
 candidate=binding['reviewed_candidate_facts']
 actions=[a['action_target'] for a in candidate['companion_actions']['actions']
          if a.get('action_kind')=='POSTPUBLISH_DIRECT_DISCOUNT']
 _need(actions==['promotion:'+t for t in B_TARGETS],'B0431_ORIGINAL_EIGHT_ACTIONS_CHANGED')
 selected=payload['pricing']['selected_targets'].get(target)
 _need(isinstance(selected,Mapping) and 'store_prices' not in selected,'B0431_MISSING_PROJECTION_ONLY')
 reviewed=_one(candidate['review_manifest']['targets'],'target_label',target)
 first=_one(documents['first_review']['targets'],'target',target)
 r1=_one(documents['round1_snapshot']['fact_snapshot']['targets'],'target',target)
 _need(first['price']==r1['price'],'B0431_R1_FIRST_PRICE_CHANGED')
 rows=selected.get('sku_prices');approved=reviewed.get('prices');quotes=first['price'].get('sku_prices')
 _need(all(type(v) is list and [r.get('model_sku') for r in v]==B_MODELS
           for v in (rows,approved,quotes)),'B0431_COMPLETE_THREE_MODELS_REQUIRED')
 result_rows=[]
 for price,shown,quote in zip(rows,approved,quotes):
  amount=_decimal(price.get('list_price'));currency=price.get('currency')
  _need(currency==shown.get('currency')==quote.get('currency')
        and (expected_currency is None or currency==expected_currency)
        and amount==_decimal(shown.get('amount'))==_decimal(quote.get('amount')),'B0431_REVIEWED_PRICE_CHANGED')
  if target in B_SH:
   rate=doc['targets'][target]['models'][B_MODELS.index(price['model_sku'])]
   _need(amount==_decimal(rate['original_approved_list_price']) and currency==rate['currency'],'B0431_SH_PRICE_CHANGED')
   percent=Decimal(rate['discount_percent_off']);sale=_decimal(rate['exact_sale_price']);origin='B0431_EXPLICIT_SH_R81'
  else:
   calculation=quote.get('calculation') or {};display=shown.get('calculation') or {}
   percent=_decimal((calculation.get('inputs') or {}).get('discount_reserve_pct'))
   sale=_decimal((calculation.get('result') or {}).get('sale_after_discount'))
   _need(percent==35 and display==calculation
         and amount==_decimal(calculation['result']['list_price'])
         and currency==calculation['result']['currency'],'B0431_FROZEN_TK35_CHANGED')
   origin='B0431_ORIGINAL_R1_FIRST_FINAL_TK35'
  _need(sale==amount*(Decimal(1)-percent/100),'B0431_EXACT_ARITHMETIC_REQUIRED')
  result_rows.append((currency,amount,int(percent),sale))
 _need(len(set(result_rows))==1,'B0431_UNIFORM_MODEL_PRICES_REQUIRED')
 currency,amount,percent,sale=result_rows[0]
 result={'schema':'retained-approved-discount-price/v1','offer_id':B_OFFER,'plan_id':B_PLAN,
         'target_label':target,'model_skus':list(B_MODELS),'currency':currency,'list_price':_money(amount),
         'discount_percent':percent,'sale_after_discount':_money(sale),'payload_digest':B_PAYLOAD,
         'candidate_digest':B_CANDIDATE,'decision_id':B_DECISION,
         'round1_snapshot_digest':identity['round1_snapshot_digest'],
         'first_review_digest':identity['first_review_digest'],'r2_identity_digest':_digest(identity),
         'origin':origin,'rate_authority_ref':scope['b0431_discount_decision_ref'],
         'source_event_record_digest':scope['b0431_discount_source_digest'],
         'rate_intent_digest':_digest(doc),'rounding_applied':False,'execution_authorized':False}
 result['binding_digest']=_digest(result);return result
