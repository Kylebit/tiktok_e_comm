"""Explicit captured SKU evidence and existing pure estimate; no provider calls."""
from datetime import date,datetime,time,timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import base64,json,math,re
from .captured_coverage import read_local,stamp,require,SCOPE
from .cost_policy import resolve_temporary_cost_policy
from .local_catalog import load_local_catalog
from .shared_inputs import FxSnapshot
from .knowledge_base import _checksum
from modules.finance import sku_profit_model as model

IDENTITY=('platform','shop_key','product_id','variant_id','seller_sku')

def identity_valid(value):
    return isinstance(value,dict) and set(value)==set(IDENTITY) and all(type(v) is str and v.strip() and not any(m in v for m in ('...','…','*')) for v in value.values())

def number(value,positive=False):
    require(type(value) in (str,int,float),'number_missing')
    result=Decimal(str(value));require(result.is_finite() and math.isfinite(float(result)) and (not positive or result>0),'number_invalid')
    return float(result)

def local_image(folder,ref):
    if not ref:return {'state':'unknown','reason':'未提供本地图'}
    try:
        name=ref['file'];require(type(name) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*\.png',name),'invalid_image_name')
        assets=(folder/'sku-assets').resolve();path=(assets/name).resolve()
        require(assets.is_relative_to(folder.resolve()) and path.is_relative_to(assets),'image_path_escape')
        with path.open('rb') as stream:raw=stream.read(1024*1024+1)
        require(len(raw)<=1024*1024 and sha256(raw).hexdigest()==ref['sha256'],'image_hash_or_size')
        require(raw[:8]==b'\x89PNG\r\n\x1a\n' and raw[12:16]==b'IHDR' and all(0<int.from_bytes(raw[n:n+4],'big')<=2048 for n in (16,20)),'invalid_png')
        return {'state':'available','sha256':ref['sha256'],'data_url':'data:image/png;base64,'+base64.b64encode(raw).decode()}
    except (OSError,KeyError,TypeError,ValueError):return {'state':'unknown','reason':'本地图路径、摘要或PNG校验未通过'}

def build_captured_sku(profile,identity=None):
    result={'schema_version':'profit-captured-sku/v1','status':'check_failed','approval_status':'ESTIMATE_ONLY','estimate':None,'choices':[],
            'network_reads_performed':[],'local_writes_performed':[],'external_writes_performed':[]}
    try:
        require(profile.get('schema_version')=='profit-captured-profile/v1','profile_invalid')
        scope={k:profile[k] for k in SCOPE};require(scope['platform'] in {'tiktok','shopee','ozon'},'platform_invalid')
        start,end=(date.fromisoformat(scope[k]) for k in ('start','end'));require(start<=end,'period_invalid')
        zone=stamp('2000-01-01T00:00:00'+scope['timezone']).tzinfo
        low,high=datetime.combine(start,time.min,zone),datetime.combine(end+timedelta(days=1),time.min,zone)
        require(profile.get('sku_evidence_path') and profile.get('sku_evidence_sha256'),'sku_source_not_selected')
        source,source_sha=read_local(profile['sku_evidence_path']);require(source_sha==profile['sku_evidence_sha256'],'sku_source_changed')
        require(source.get('schema_version')=='profit-sku-capture/v1' and source.get('scope')==scope,'sku_source_scope_mismatch')
        catalog_path=Path(profile['catalog_path']);require(catalog_path.is_absolute(),'absolute_catalog_required')
        catalog_sha=sha256(catalog_path.read_bytes()).hexdigest();require(catalog_sha==source['catalog_sha256'],'catalog_source_changed')
        catalog=load_local_catalog(catalog_path)
        require(catalog.snapshot_id==source['catalog_snapshot_id'] and sha256(catalog_path.read_bytes()).hexdigest()==catalog_sha,'catalog_snapshot_changed')
        records=[row for row in catalog.review['records'] if row['identity_valid'] and row['identity']['platform']==scope['platform'] and row['identity']['shop_key']==scope['shop_id'] and row['display_metadata']['region']==scope['site']]
        result.update(scope=scope,status='input_required',sources={'sku_sha256':source_sha,'catalog_sha256':catalog_sha,'catalog_snapshot_id':catalog.snapshot_id},
                      choices=[{'identity':row['identity'],'name':row['display_metadata']['product_name'],'specification':row['specification']} for row in records])
        if identity is None:return result
        require(identity_valid(identity),'full_identity_required')
        matches=[row for row in records if row['identity']==identity];require(len(matches)==1,'exact_catalog_identity_not_unique')
        row=matches[0];entries=source['entries'];require(isinstance(entries,list),'entries_invalid')
        entries=[e for e in entries if e.get('identity')==identity];require(len(entries)==1,'exact_source_identity_not_unique')
        entry=entries[0];gaps=[]
        result.update(identity=identity,name=row['display_metadata']['product_name'],specification=row['specification'],gaps=gaps,
                      image=local_image(Path(profile['sku_evidence_path']).parent,entry.get('image')),listing=None,recent=None)
        listing=entry.get('listing');currency=row['sale_currency']
        if listing:
            try:
                require(listing.get('currency')==currency and listing.get('source') and listing.get('version'),'listing_source_missing')
                stamp(listing['as_of']);number(listing['amount'],True);result['listing']={**listing,'sha256':_checksum(listing)}
            except (ValueError,TypeError,KeyError,ArithmeticError):gaps.append('listing_invalid')
        else:gaps.append('listing_missing')
        policy=resolve_temporary_cost_policy(catalog,{identity['seller_sku']},period_start=low,period_end=high)
        seller=identity['seller_sku'];selected=policy.values.get(seller)
        cost_issues=[v for v in policy.issues if v['canonical_sku']==seller]
        if any(w.canonical_sku==seller for w in policy.warnings):selected=None;cost_issues.append({'code':'cost_conflict_or_missing_no_assumption'})
        if not selected:gaps.append('cost_unresolved')
        result['cost']={'selected':selected,'issues':cost_issues,'candidates':row['cost']['candidates'],'matching':row['matching'],'policy_snapshot_id':policy.snapshot_id,'authority':'local_observation_not_approved_fact'}
        fx=None
        try:
            raw_fx,fx_sha=read_local(profile['fx_path']);require(fx_sha==source['fx_sha256'],'fx_source_changed');stamp(raw_fx['as_of'])
            base=FxSnapshot.from_mapping(raw_fx['rates_cny'],source=raw_fx['source'],as_of=raw_fx['as_of'],snapshot_id=raw_fx.get('snapshot_id'))
            overrides=profile.get('fx_overrides') or {};require(type(overrides) is dict,'fx_overrides_invalid')
            for k,v in overrides.items():require(re.fullmatch('[A-Z]{3}',k),'fx_currency_invalid');number(v,True)
            value=overrides.get(currency,base.get(currency));fx=number(str(value),True)
            result['fx']={'base':base.payload(),'overrides':overrides,'selected_currency':currency,'selected_rate':str(value),'sha256':fx_sha}
        except (OSError,ValueError,TypeError,KeyError,ArithmeticError):gaps.append('fx_missing_or_changed');result['fx']=None
        samples=entry.get('samples');valid=[]
        if samples:
            try:
                require(samples.get('source') and samples.get('version') and samples.get('time_basis')=='settled_at','sample_source_missing')
                as_of=stamp(samples['as_of']);require(type(samples['rows']) is list,'sample_rows_missing');seen=set();excluded=0
                for sample in samples['rows']:
                    require(sample.get('identity')==identity and sample.get('currency')==currency,'sample_identity_or_currency_mismatch')
                    key=sample.get('order_id');require(type(key) is str and key.strip() and not any(m in key for m in ('...','…','*')) and key not in seen,'sample_identity_missing_or_duplicate');seen.add(key)
                    when=stamp(sample['settled_at']);require(low<=when<high and when<=as_of,'sample_outside_window')
                    status=sample.get('status');require(status in {'settled','unsettled','cancelled'},'sample_status_unknown')
                    if status!='settled':excluded+=1;continue
                    require(type(sample.get('quantity')) is int and sample['quantity']>0,'sample_quantity_missing')
                    require(sample.get('net_basis')=='platform_net_before_external_cost_and_ads','sample_net_basis_unknown')
                    valid.append({'order_id':key,'sale':number(sample['paid_product_amount'],True)/sample['quantity'],'net':number(sample['net_settlement_amount'])/sample['quantity'],'settled_at':sample['settled_at']})
                result['recent']={**(model.summarize_nums([v['sale'] for v in valid]) or {'n':0,'median':None}),'currency':currency,'source':samples['source'],'version':samples['version'],'as_of':samples['as_of'],'window':scope,'sha256':_checksum(samples),'provided_rows':len(samples['rows']),'excluded_rows':excluded,'scope':'provided_settled_rows_only_not_market_or_posterior_return'}
            except (ValueError,TypeError,KeyError,AttributeError,ArithmeticError):gaps.append('sample_evidence_invalid');valid=[]
        if not valid:gaps.append('samples_missing_or_empty')
        basis=profile.get('sku_price_basis');sale=None
        if basis=='listing' and result['listing']:sale=number(result['listing']['amount'],True)
        elif basis=='recent_median' and result['recent'] and result['recent']['median'] is not None:sale=result['recent']['median']
        else:gaps.append('selected_price_unavailable')
        try:ad_rate=number(profile['ad_rate']);require(0<=ad_rate<=1,'ad_rate_invalid')
        except (KeyError,ValueError,ArithmeticError):ad_rate=None;gaps.append('explicit_ad_rate_missing')
        model_file=Path(model.__file__)
        result['model']={'function':'sku_profit_model.estimate_from_ratio','sha256':sha256(model_file.read_bytes()).hexdigest(),'price_basis':basis,'ad_rate':ad_rate,'note':'按提供样本净结算比估算；净结算内平台费不再扣除，仅另扣明确商品成本与广告假设；不是结算事实或完整先验/后验分析。'}
        if sale is not None and selected and fx is not None and ad_rate is not None and valid:
            cost=number(selected['unit_cost_cny'],True)
            comps=[model.enrich_comp(order_id=v['order_id'],statement_date=v['settled_at'],sale_local=v['sale'],settlement_local=v['net'],cost_cny=cost,fx=fx,ad_rate=ad_rate,source=samples['version']) for v in valid]
            result['estimate']=model.estimate_from_ratio(sale_local=sale,comps=comps,cost_cny=cost,fx=fx,ad_rate=ad_rate)
            # The old model supplies zero shipping defaults. This capture has no
            # shipping components: suppress that ancillary statistic, not profit.
            result['estimate']['comp_ship_stats']=None
            result['estimate']['unavailable_components']=['shipping_components_not_captured']
        from .captured_waterfall import build_waterfall
        result['waterfall']=build_waterfall(entry,result,low,high)
        from .captured_samples import build_sample_audit
        result['sample_audit']=build_sample_audit(entry,result,currency,low,high)
        result['status']='estimate_available' if result['estimate'] else 'needs_review'
        result['evidence_id']='captured-sku:'+_checksum(result)
        json.dumps(result,allow_nan=False)
        return result
    except (OSError,ValueError,TypeError,KeyError,AttributeError,ArithmeticError) as exc:
        return {'schema_version':'profit-captured-sku/v1','status':'check_failed','error':{'code':str(exc) if type(exc) is ValueError and re.fullmatch('[a-z_]+',str(exc)) else 'sku_input_check_failed'},'estimate':None,'choices':[],'approval_status':'ESTIMATE_ONLY','network_reads_performed':[],'local_writes_performed':[],'external_writes_performed':[]}
