"""Captured fee explanations around existing pure models; no live rules or IO."""
from decimal import Decimal
import math
from .captured_coverage import require,stamp
from .knowledge_base import _checksum
from modules.finance import sku_profit_model as model

FEES=('logistics_local','commission_local','transaction_local','extra_local','creator_local','affiliate_local','seller_tax_local','fixed_fee_local')
PARTS=('goods_local',*FEES,'ad_local')

def amount(value):
    require(type(value) in (str,int,float),'fee_number_missing')
    d=Decimal(str(value));require(d.is_finite() and math.isfinite(float(d)),'fee_number_invalid');return d

def fmt(value):return format(value.normalize(),'f') if value is not None else None

def prior(source,key,sale,cost,fx,ad):
    out={'status':'missing','estimate':None,'components':{k:None for k in PARTS},'missing':list(PARTS),'extra_cap':None}
    if source is None:return out
    try:
        require(type(source) is dict and type(source.get('components')) is dict,'prior_components_invalid')
        require(set(source['components'])<=set(PARTS),'prior_component_unknown')
        values={k:amount(v) if v is not None else None for k,v in source['components'].items()}
        require(all(v is None or v>=0 for v in values.values()),'prior_negative_fee')
        out['components']={k:fmt(values.get(k)) for k in PARTS};out['missing']=[k for k in PARTS if values.get(k) is None]
        include_tax=source.get('include_tax');require(type(include_tax) is bool,'tax_inclusion_missing')
        out['include_tax']=include_tax;out['include_creator']=key=='with_affiliate'
        captured_sale=amount(source['sale_local']);out['sale_local']=fmt(captured_sale)
        if sale is None or cost is None or fx is None or ad is None:
            out.update(status='partial',error='selected_model_inputs_missing');return out
        require(captured_sale==sale,'prior_price_changed')
        if key=='no_affiliate':
            require(all(values.get(k) in (None,Decimal(0)) for k in ('creator_local','affiliate_local')),'contradictory_no_affiliate')
        if values.get('goods_local') is not None:require(abs(values['goods_local']-cost/fx)<=Decimal('.01'),'prior_cost_changed')
        if values.get('ad_local') is not None:require(abs(values['ad_local']-sale*ad)<=Decimal('.01'),'prior_ad_changed')
        cap=source.get('extra_cap')
        if cap is not None:
            require(type(cap) is dict and type(cap.get('extra_cap_hit')) is bool,'cap_evidence_invalid')
            raw,limit=amount(cap['uncapped_amount']),amount(cap['cap_amount']);require(raw>=0 and limit>=0,'cap_evidence_invalid')
            hit=limit>0 and raw>limit;require(cap['extra_cap_hit']==hit,'cap_flag_mismatch')
            if values.get('extra_local') is not None:require(values['extra_local']==(limit if hit else raw),'cap_applied_amount_mismatch')
            out['extra_cap']={**cap,'validation':'captured_amount_consistency_only_not_live_rule'}
        if out['missing'] or cap is None:
            out.update(status='partial');
            if cap is None:out['missing'].append('extra_cap_hit')
            return out
        bd=model.PriorBreakdown(sale_local=float(sale),**{k:float(values[k]) for k in PARTS},extra_cap_hit=cap['extra_cap_hit'])
        result=model.prior_profit_from_breakdown(bd,include_creator=out['include_creator'],include_tax=include_tax,fx=float(fx))
        # Guard the legacy affiliate asymmetry instead of changing its formula.
        require(abs(Decimal(str(result['est_settlement_local']))-values['goods_local']-values['ad_local']-Decimal(str(result['profit_local'])))<=Decimal('.02'),'prior_model_not_reconciled')
        out.update(status='complete',estimate=result,known_deductions_local=fmt(sum(values[k] for k in PARTS if include_tax or k!='seller_tax_local')))
        return out
    except (ValueError,TypeError,KeyError,ArithmeticError):
        out.update(status='invalid',estimate=None,error='invalid_or_conflicting_prior_capture');return out

def build_waterfall(entry,base,low,high):
    result={'schema_version':'profit-captured-waterfall/v1','status':'missing','approval_status':'ESTIMATE_ONLY','prior':{},'posterior':None,'observed':None,'error':'waterfall_evidence_not_captured'}
    source=entry.get('waterfall')
    if source is None:return result
    try:
        require(type(source) is dict and source.get('identity')==base['identity'],'waterfall_identity_mismatch')
        currency=(base.get('listing') or base.get('recent') or {}).get('currency')
        require(source.get('currency')==currency and currency,'waterfall_currency_mismatch')
        require(all(type(source.get(k)) is str and source[k].strip() for k in ('source','version')),'waterfall_provenance_missing')
        require(source.get('creator_affiliate_basis')=='distinct_non_overlapping','creator_affiliate_basis_unknown')
        asof=stamp(source['as_of']);require(stamp(source['valid_from'])<=low and stamp(source['valid_to'])>=high and asof>=high,'waterfall_outside_validity')
        result.update(status='available',error=None,currency=currency,source={k:source[k] for k in ('source','version','as_of','valid_from','valid_to','creator_affiliate_basis')},sha256=_checksum(source),scope=base['scope'],identity=base['identity'])
        basis=base['model']['price_basis'];sale=(base.get('listing') or {}).get('amount') if basis=='listing' else (base.get('recent') or {}).get('median')
        sale=amount(sale) if sale is not None else None
        cost=(base.get('cost',{}).get('selected') or {}).get('unit_cost_cny');cost=amount(cost) if cost is not None else None
        fx=(base.get('fx') or {}).get('selected_rate');fx=amount(fx) if fx is not None else None
        ad=base['model']['ad_rate'];ad=amount(ad) if ad is not None else None
        priors=source.get('prior',{});require(type(priors) is dict and set(priors)<=set(('with_affiliate','no_affiliate')),'prior_scenarios_invalid')
        result['prior']={k:prior(priors.get(k),k,sale,cost,fx,ad) for k in ('with_affiliate','no_affiliate')}
        if not base.get('recent') or not base['recent'].get('n'):
            result['posterior']={'status':'missing','error':'valid_settled_samples_missing'};return result
        samples=entry['samples'];require(stamp(samples['as_of'])<=asof,'fee_capture_before_sample_capture')
        rows=[r for r in samples['rows'] if r['status']=='settled']
        fees=source.get('sample_fees',[]);require(type(fees) is list,'sample_fee_rows_invalid')
        by_id={};ids={r['order_id'] for r in rows}
        for row in fees:
            require(type(row) is dict and row.get('identity')==base['identity'] and row.get('currency')==currency,'fee_row_scope_mismatch')
            key=row.get('order_id');require(key in ids and key not in by_id,'fee_row_unknown_or_duplicate')
            require(row.get('affiliate_state') in ('with','without','unknown'),'affiliate_state_missing')
            parts=row.get('components');require(type(parts) is dict and set(parts)<=set(FEES),'fee_components_invalid')
            parts={k:amount(v) if v is not None else None for k,v in parts.items()}
            if row['affiliate_state']=='without':require(all(parts.get(k) in (None,Decimal(0)) for k in ('creator_local','affiliate_local')),'fee_affiliate_contradiction')
            by_id[key]={'parts':parts,'affiliate_state':row['affiliate_state']}
        gross=sum(amount(r['paid_product_amount']) for r in rows);net=sum(amount(r['net_settlement_amount']) for r in rows)
        amounts={k:[by_id.get(r['order_id'],{}).get('parts',{}).get(k) for r in rows] for k in FEES}
        complete=all(all(v is not None for v in values) for values in amounts.values())
        known={k:sum(v for v in values if v is not None) for k,values in amounts.items()}
        delta=gross-net-sum(known.values())
        result['observed']={'kind':'captured_sample_observation_not_approved_fact','sample_count':len(rows),'quantity':sum(r['quantity'] for r in rows),'gross_local':fmt(gross),'net_local':fmt(net),
            'components':{k:{'amount':fmt(known[k]) if all(v is not None for v in values) else None,'known_subtotal':fmt(known[k]) if any(v is not None for v in values) else None,'known_rows':sum(v is not None for v in values)} for k,values in amounts.items()},
            'known_deductions_local':fmt(sum(known.values())) if any(v is not None for values in amounts.values() for v in values) else None,'unexplained_delta_local':fmt(delta),'reconciliation':'complete' if complete and abs(delta)<=Decimal('.01') else 'mismatch' if complete else 'partial',
            'note':'原币样本总额；销售减净结算与已知费用核对，差额未分类；费用已在净结算内，不再次扣除。'}
        groups={'all':[],'with_affiliate':[],'no_affiliate':[]};counts={k:0 for k in groups};unknown=0
        for row in rows:
            state=by_id.get(row['order_id'],{}).get('affiliate_state','unknown')
            counts['all']+=1
            if state!='unknown':counts['with_affiliate' if state=='with' else 'no_affiliate']+=1
            if state=='unknown':unknown+=1
            if None in (sale,cost,fx,ad):continue
            comp=model.enrich_comp(order_id=row['order_id'],statement_date=row['settled_at'],sale_local=float(amount(row['paid_product_amount'])/row['quantity']),settlement_local=float(amount(row['net_settlement_amount'])/row['quantity']),cost_cny=float(cost),fx=float(fx),ad_rate=float(ad),source=source['version'])
            groups['all'].append(comp)
            if state!='unknown':groups['with_affiliate' if state=='with' else 'no_affiliate'].append(comp)
        posterior={'status':'available','unknown_affiliate_count':unknown,'sample_scope':'provided_settled_samples_only_no_outlier_filter_or_full_coverage_claim','model':'sku_profit_model.estimate_from_ratio','model_sha256':base['model']['sha256']}
        for key,comps in groups.items():
            estimate=model.estimate_from_ratio(sale_local=float(sale),comps=comps,cost_cny=float(cost),fx=float(fx),ad_rate=float(ad)) if comps else None
            if estimate:estimate.update(comp_ship_stats=None,unavailable_components=['shipping_statistics_not_inferred'])
            posterior[key]={'estimate':estimate,'n':counts[key],'sale_local':fmt(sale),'goods_local':fmt(cost/fx) if cost is not None and fx else None,'platform_net_difference_local':fmt(sale-Decimal(str(estimate['est_settlement_local']))) if estimate else None}
        result['posterior']=posterior
        return result
    except (ValueError,TypeError,KeyError,AttributeError,ArithmeticError):
        return {'schema_version':'profit-captured-waterfall/v1','status':'invalid','approval_status':'ESTIMATE_ONLY','prior':{},'posterior':None,'observed':None,'error':'waterfall_capture_invalid_or_stale'}
