"""All captured sample rows for display-only audit; estimator sets are unchanged."""
from collections import Counter
import math
from .captured_coverage import stamp
from .captured_waterfall import amount,fmt
from .knowledge_base import _checksum
from modules.finance import sku_profit_model as model

def clean(value):
    if type(value) is float and not math.isfinite(value):return {'invalid_numeric':str(value)}
    if type(value) is dict:return {k:clean(v) for k,v in value.items()}
    if type(value) is list:return [clean(v) for v in value]
    return value

def build_sample_audit(entry,base,currency,low,high):
    samples=entry.get('samples')
    out={'schema_version':'profit-captured-sample-audit/v1','status':'missing','rows':[],
         'counts':{'captured':None,'settled_in_window':None,'eligible':None,'returned':0},
         'scope':base['scope'],'identity':base['identity'],'capture_sha256':base['sources']['sku_sha256'],
         'assumptions':{'cost':base.get('cost',{}).get('selected'),'fx':base.get('fx'),'ad_rate':base['model']['ad_rate'],'profit_kind':'per_item_estimate_not_realized_fact'},
         'population_note':'All rows from this selected capture only; display filters never alter base or waterfall estimator populations.',
         'model_sha256':base['model']['sha256'],'outlier_rule':'sku_profit_model.is_outlier_comp: net <= 0 or four-decimal enriched net/paid ratio < 0.1; flag only, no estimator exclusion'}
    if type(samples) is not dict or type(samples.get('rows')) is not list:
        out['error']='sample_rows_not_captured';return out
    raw=samples['rows'];digest=_checksum(samples);out.update(status='available',sample_sha256=digest,source={k:clean(samples.get(k)) for k in ('source','version','as_of','time_basis')})
    metadata=all(type(samples.get(k)) is str and samples[k].strip() for k in ('source','version')) and samples.get('time_basis')=='settled_at'
    try:asof=stamp(samples['as_of'])
    except (ValueError,TypeError,KeyError,AttributeError):asof=None;metadata=False
    ids=Counter(r.get('order_id') for r in raw if type(r) is dict and type(r.get('order_id')) is str)
    water=base.get('waterfall') or {};affiliation={}
    if water.get('status')=='available' and (water.get('posterior') or {}).get('status')=='available':
        affiliation={r['order_id']:r['affiliate_state'] for r in entry['waterfall'].get('sample_fees',[])}
    inputs={'cost':(base.get('cost',{}).get('selected') or {}).get('unit_cost_cny'),'fx':(base.get('fx') or {}).get('selected_rate'),'ad':base['model']['ad_rate']}
    missing=[k+'_missing' for k,v in inputs.items() if v is None]
    for index,original in enumerate(raw):
        row=original if type(original) is dict else {};reasons=[];key=row.get('order_id');identity_ok=row.get('identity')==base['identity'] and row.get('currency')==currency
        if not identity_ok:reasons.append('identity_or_currency_mismatch')
        key_ok=type(key) is str and key.strip() and not any(m in key for m in ('...','…','*'))
        if not key_ok:reasons.append('full_order_identity_missing')
        elif ids[key]>1:reasons.append('duplicate_order_identity')
        if not metadata:reasons.append('sample_provenance_invalid')
        try:when=stamp(row['settled_at']);in_time=low<=when<high and asof is not None and when<=asof
        except (ValueError,TypeError,KeyError,AttributeError):in_time=False
        if not in_time:reasons.append('outside_or_unknown_settlement_window')
        if row.get('status')!='settled':reasons.append('not_settled')
        quantity=row.get('quantity');quantity_ok=type(quantity) is int and quantity>0
        if not quantity_ok:reasons.append('quantity_missing_or_invalid')
        paid=net=None
        try:paid=amount(row.get('paid_product_amount'));assert paid>0
        except (ValueError,TypeError,ArithmeticError,AssertionError):paid=None;reasons.append('paid_amount_missing_or_invalid')
        try:net=amount(row.get('net_settlement_amount'))
        except (ValueError,TypeError,ArithmeticError):reasons.append('net_amount_missing_or_invalid')
        if row.get('net_basis')!='platform_net_before_external_cost_and_ads':reasons.append('net_basis_unknown')
        eligible=not reasons;derived=None;ratio=None;outlier=None
        if paid is not None and net is not None:
            ratio=round(float(net/paid),4)
            if not math.isfinite(ratio):ratio=None
            if ratio is not None and identity_ok and in_time and row.get('status')=='settled':outlier=model.is_outlier_comp({'settle_ratio':ratio,'settlement_local':float(net)})
        if eligible and not missing:
            derived=model.enrich_comp(order_id=key,statement_date=row['settled_at'],sale_local=float(paid)/quantity,settlement_local=float(net)/quantity,cost_cny=float(inputs['cost']),fx=float(inputs['fx']),ad_rate=float(inputs['ad']),source=samples['version'])
            if not all(v is None or type(v) is not float or math.isfinite(v) for v in derived.values()):derived=None;reasons.append('derived_number_invalid')
            if derived:ratio=derived['settle_ratio'];outlier=model.is_outlier_comp(derived)
        profit=derived['profit_cny'] if derived else None
        profit_class='unknown' if profit is None else 'negative' if profit<0 else 'positive' if profit>0 else 'zero'
        out['rows'].append({'row_key':digest+':'+str(index),'capture_index':index,'order_id':clean(key),'identity':clean(row.get('identity')),'settled_at':clean(row.get('settled_at')),
             'currency':clean(row.get('currency')),'quantity':quantity if quantity_ok else None,'paid_total_local':fmt(paid),'net_total_local':fmt(net),
             'paid_per_item_local':fmt(paid/quantity) if paid is not None and quantity_ok else None,'net_per_item_local':fmt(net/quantity) if net is not None and quantity_ok else None,
             'settlement_ratio':ratio,'affiliate_state':affiliation.get(key,'unknown') if eligible else 'unknown','outlier':outlier,'eligible':eligible,
             'settled_in_window':bool(identity_ok and key_ok and in_time and row.get('status')=='settled'),'profit_cny':profit,'profit_class':profit_class,'margin_pct':derived['margin_pct'] if derived else None,
             'used_in_base_estimate':bool(row.get('status')=='settled' and base.get('estimate')),'used_in_waterfall_all_estimate':bool(row.get('status')=='settled' and ((water.get('posterior') or {}).get('all') or {}).get('estimate')),
             'missing_reasons':reasons+missing,'source':clean(samples.get('source')),'source_version':clean(samples.get('version')),'raw_record':clean(original)})
    out['counts']={'captured':len(raw),'settled_in_window':sum(r['settled_in_window'] for r in out['rows']),'eligible':sum(r['eligible'] for r in out['rows']),'returned':len(out['rows'])}
    return out
