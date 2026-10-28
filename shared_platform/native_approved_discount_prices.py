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
