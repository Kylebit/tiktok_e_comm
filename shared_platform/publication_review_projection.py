"""Read the registered product into the existing workspace's field contracts."""
from contextlib import contextmanager
from copy import deepcopy
import threading
from shared_platform.publication_r2_review import _load, _view, _require, _master_report
from shared_platform.publication_takeover import read_document


_dashboard_gate_guard = threading.Lock()
_dashboard_gates = {}


@contextmanager
def _dashboard_build_gate(offer_id):
    """Keep one registered offer's memory-heavy projection build in flight."""
    key = str(offer_id)
    with _dashboard_gate_guard:
        gate = _dashboard_gates.get(key)
        if gate is None:
            gate = [threading.Lock(), 0]
            _dashboard_gates[key] = gate
        gate[1] += 1
    try:
        with gate[0]:
            yield
    finally:
        with _dashboard_gate_guard:
            gate[1] -= 1
            if gate[1] == 0 and _dashboard_gates.get(key) is gate:
                del _dashboard_gates[key]


def frozen_pricing(first):
    targets={}
    stores=[]
    for target in first.get('targets',[]):
        label=target['target'];price=target.get('price') or {}
        calculation=price.get('calculation') or {}
        result=calculation.get('result') or {}
        store={'target_key':target.get('selection_key') or label.split(':')[1].lower(),
            'shop':'HomeBloom' if label.startswith('tiktok:HB_') else 'LivelyHive',
            'region':label.split(':')[1].split('_')[-1], 'currency':price.get('currency'),
            'list_price':price.get('amount'), 'sale_after_discount':result.get('sale_after_discount'),
            'estimated_profit_cny':result.get('estimated_profit_cny'),
            'min_profit_adjusted':result.get('min_profit_adjusted'),
            'status':price.get('status'), 'formula_parameters':calculation.get('inputs') or {},
            'calculation':calculation}
        sku_prices=[]
        for sku in price.get('sku_prices') or []:
            sku_prices.append(dict(sku,list_price=sku.get('amount'),label=sku.get('display_name')))
        targets[label]=dict(price,store_prices=[store],sku_prices=sku_prices,
            source_field='round1-approved-snapshot.first_review_digest',execution_authority=False)
        if label.startswith('tiktok:'):
            stores.append(store)
    return {'schema_version':'channel-pricing-preview/v1','status':'ready',
        'input':{k:first.get('product_facts',{}).get(k) for k in ('cost_cny','weight_kg','package_cm')},
        'selected_store_prices':stores,'all_legacy_store_prices':stores,'target_pricing':targets,
        'workbench_exchange_rates':{},'shopee_exchange_rates':{},'ozon_exchange_rates':{},
        'sku_pricing':[], 'blockers':[], 'source':'FROZEN_ROUND1','external_calls_performed':[]}


def dashboard(offer_id, *, runtime_root, publication_targets=None):
    # A dashboard rebuild materializes the complete frozen review and release
    # projection. Concurrent browser refreshes for the same offer used to
    # multiply that transient memory and surface as an empty MemoryError. Keep
    # the build read-through: do not cache mutable release-ledger projections.
    with _dashboard_build_gate(offer_id):
        return _dashboard(offer_id, runtime_root=runtime_root, publication_targets=publication_targets)


def _dashboard(offer_id, *, runtime_root, publication_targets=None):
    from shared_platform.release_control import build_release_dashboard
    project,registration,takeover,candidate,record=_load(offer_id,runtime_root)
    if publication_targets is not None:
        _require(publication_targets==registration['targets'], 'FROZEN_TARGETS_REQUIRED')
    first=read_document(project/'reports/product-preparation'/offer_id/'first-review.json',{})
    _require([r.get('target') for r in first.get('targets',[])]==registration['targets'], 'FROZEN_TARGETS_REQUIRED')
    result=build_release_dashboard(offer_id=offer_id,root=project,catalog_optional=True,
        publication_targets=registration['targets'],frozen_pricing=frozen_pricing(first))
    result['r2_candidate_review']=_view(registration,takeover,candidate,record)
    result['frozen_review_projection']=True
    result['frozen_first_review']={
        'offer_id':offer_id,'approved_revision':registration['approved_revision'],
        'snapshot_digest':registration['snapshot_digest'],
        **{key:deepcopy(first.get(key)) for key in (
            'product_facts','platform_categories','copy_review_sets','targets',
            'shared_review_facts','knowledge_basis','content_groups','audit_log')},
        'execution_authority':False}
    result['product']['seller_sku_governance'].update(available=None,source='frozen_round1_identity',next_available_sku_range=[])
    result['publication_scope']['selected_labels']=list(registration['targets'])
    result['publication_scope']['default_labels']=list(registration['targets'])
    # Avoid importing an earlier, superseded copy draft from the workbench state.
    groups=first.get('copy_review_sets') or []
    if groups:
        by_id={g['id']:g for g in groups}
        copy=deepcopy(result.get('listing_copy') or {})
        copy.update(semantic_master_en=groups[0].get('title_en',''),
            shopee_description_en=groups[0].get('description_en',''),notes_zh='',status='frozen_round1')
        copy['candidates']=[]
        for target in first['targets']:
            label=target['target'];channel,site=label.split(':')
            group=by_id.get('homebloom-sea' if site.startswith('HB_') else 'livelyhive-sea',groups[0])
            title=(target.get('copy') or {}).get('title') or group.get('title_en','')
            copy['candidates'].append({'channel':channel,'site':site,'title':title,
                'language':(target.get('copy') or {}).get('language'),
                'description':(target.get('copy') or {}).get('description'),
                'policy_check':'frozen_round1','limit':255})
        result['listing_copy']=copy
    result['first_review_image_plan']=dict(first.get('image_execution_plan') or {},status='FROZEN_ROUND1')
    generation=_master_report(registration)
    result['frozen_master_images']=[{
        'review_number':row.get('review_number'), 'brand_label':row.get('brand_label') or row.get('brand_id'),
        'role':row.get('role'),'recorded_status':row.get('status'),
        'local_url':f"/api/product-workspace/r2-candidate/master-image?offer_id={offer_id}&image_id={row.get('review_number')}&binding={registration['binding_sha256']}",
        'approved_for_publication':False} for row in generation.get('assets',[])]
    result['actual_release_gate']['ready']=False
    result['approval_rehearsal']['ready']=False
    return result


def source_preview(offer_id, *, runtime_root):
    project,*_=_load(offer_id,runtime_root)
    state=read_document(project/'data/new_product_workbench'/f'{offer_id}.json',{})
    return {'ok':True,'offer_id':offer_id,'revision':state['_revision'],
        'review':state.get('review') or {},'source':state['source'],'read_only':True}
