"""Pure, directed reuse of one Model's existing visual QA after SKU correction.

No provider, filesystem, ledger, approval or budget operation lives here. The
normal native owner must admit the new R1 and verify the retained paid-history
binding. This receipt cannot create a paid request or marketplace authority.
"""
from copy import deepcopy
from collections.abc import Mapping
import hashlib
import json
import re

SCHEMA = 'sku-only-r2-derivation/v1'
EVALUATION = 'SKU_IDENTITY_ONLY_DERIVATION_NO_NEW_VISUAL_QA'
_DOCUMENTS = ('round1_snapshot','first_review','generation_result','translation_plan','translation_result','image_qa')
_SKU_FIELDS = {'seller_sku','model_sku','sku_id','sku_key','itemNum','sellerSku'}
_LINK_FIELDS = {'snapshot_digest','first_review_digest','image_plan_digest','receipt_digest','evidence_digest',
    'decision_digest','decision_receipt_digest','approval_digest','input_fingerprint','product_approval_fingerprint',
    'product_approval_id','product_package_id','package_id','product_center_revision',
    'approved_product_center_revision','reviewed_product_center_revision','approved_at',
    'prepared_reference','preparation_digest','preparation_root_id','origin_request_id','root_id',
    'input_lineage_manifest'}


def _digest(value):
    return 'sha256:'+hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _require(value, code):
    if not value:
        raise ValueError('SKU_ONLY_R2_'+code)


def _business(value, sku):
    if isinstance(value, Mapping):
        return {k:(deepcopy(v) if k=='category_evidence_binding' else
                   '__SAME_ONE_MODEL__' if k in _SKU_FIELDS and str(v)==sku else _business(v,sku))
                for k,v in value.items() if k not in _LINK_FIELDS}
    if isinstance(value,list):
        return [_business(v,sku) for v in value]
    return value


def _build(old_documents, new_round1_snapshot, new_first_review, correction_binding,
           paid_history_binding, verified_artifacts):
    from shared_platform.publication_r3_image_bridge import validate_r2_identity
    old = deepcopy(dict(old_documents)); new_r1=deepcopy(dict(new_round1_snapshot)); first=deepcopy(dict(new_first_review))
    _require(all(isinstance(old.get(k), Mapping) for k in _DOCUMENTS),'SIX_DOCUMENTS_REQUIRED')
    _require('sku_only_derivation' not in old['image_qa'],'DERIVATION_CHAIN_NOT_SUPPORTED')
    old_identity=validate_r2_identity(old)
    binding=deepcopy(dict(correction_binding)); paid=deepcopy(dict(paid_history_binding))
    offer=old_identity['offer_id']; old_r1=old['round1_snapshot']
    _require(binding.get('offer_id')==offer==new_r1.get('offer_id')==first.get('offer_id')==paid.get('offer_id'),'SAME_OFFER_REQUIRED')
    old_sku=binding.get('old_seller_sku'); new_sku=binding.get('new_seller_sku'); models=binding.get('models')
    _require(all(type(v) is str and re.fullmatch('[0-9]{4}',v) for v in (old_sku,new_sku)) and old_sku!=new_sku,'EXACT_DISTINCT_SKUS_REQUIRED')
    old_facts=old['first_review']['product_facts']; new_facts=first.get('product_facts') or {}
    _require(old_facts.get('seller_sku')==old_sku and new_facts.get('seller_sku')==new_sku,'SELLER_IDENTITY_CONFLICT')
    old_models=old_facts.get('skus') or []; new_models=new_facts.get('skus') or []
    _require(len(old_models)==len(new_models)==1 and type(models) is list and len(models)==1,'ONE_MODEL_REQUIRED')
    expected_model={'variant_key':old_models[0]['source_key'],'old_model_sku':old_sku,'new_model_sku':new_sku}
    _require(models==[expected_model] and new_models[0].get('source_key')==expected_model['variant_key']
        and old_models[0].get('seller_sku')==old_sku and new_models[0].get('seller_sku')==new_sku,'VARIANT_IDENTITY_CONFLICT')
    new_unsigned=deepcopy(new_r1); new_digest=new_unsigned.pop('snapshot_digest',None)
    _require(new_digest==_digest(new_unsigned) and new_digest!=old_r1['snapshot_digest']
        and binding.get('old_round1_snapshot_digest')==old_r1['snapshot_digest']
        and binding.get('new_round1_snapshot_digest')==new_digest,'DIRECTED_ROUND1_BINDING_CONFLICT')
    _require(new_r1.get('first_review_digest')==_digest(first),'NEW_FIRST_REVIEW_BINDING_CONFLICT')
    # An old capture is historical proof, not a newly captured input manifest.
    # The admitted native correction may remove its direct current-input link;
    # it must not manufacture a replacement. The original remains in receipt.
    for original,current in ((old['first_review'],first),(old_r1,new_r1)):
        if 'input_lineage_manifest' in current:
            _require(current['input_lineage_manifest']==original.get('input_lineage_manifest'),
                'ORIGINAL_INPUT_LINEAGE_CHANGED')
    # Every actual commercial/copy/category/stock/image-plan value survives;
    # only typed SKU leaves and named technical linkage fields may differ.
    _require(_business(old['first_review'],old_sku)==_business(first,new_sku),'FIRST_REVIEW_BUSINESS_CHANGED')
    stable=('canonical_targets','workbench_tiktok_sites','fact_snapshot','image_plan',
        'publication_stock_policy','approved_by','approval_authority','human_approval','external_write_count','next_round')
    _require(_business({k:old_r1.get(k) for k in stable},old_sku)==
        _business({k:new_r1.get(k) for k in stable},new_sku),'FROZEN_VISUAL_OR_COMMERCIAL_FACTS_CHANGED')
    _require(paid.get('unknown_request_count')==0 and type(paid.get('confirmed_request_count')) is int
        and paid['confirmed_request_count']>0 and type(paid.get('confirmed_request_ids')) is list
        and len(paid['confirmed_request_ids'])==len(set(paid['confirmed_request_ids']))==paid['confirmed_request_count']
        and all(type(v) is str and v for v in paid['confirmed_request_ids'])
        and all(type(paid.get(k)) is str and paid[k] for k in ('history_root','ledger_ref'))
        and re.fullmatch(r'(sha256:)?[0-9a-f]{64}',str(paid.get('ledger_sha256') or '')),'ORIGINAL_PAID_HISTORY_REQUIRED')
    assets=old['generation_result']['assets']
    _require(len(assets)==14 and len({a['artifact_digest'] for a in assets})==14
        and sorted([sum(a['brand_id']==b for a in assets) for b in {a['brand_id'] for a in assets}])==[7,7],
        'EXACT_TWO_BRAND_FOURTEEN_MASTERS_REQUIRED')
    all_assets=assets+list(old['translation_result'].get('assets') or [])
    _require(set(verified_artifacts)=={a['artifact_digest'] for a in all_assets}
        and all(type(v) is int and v>0 for v in verified_artifacts.values()),'VERIFIED_ARTIFACT_COVERAGE_REQUIRED')
    receipt={'schema_version':SCHEMA,'offer_id':offer,'correction_binding':binding,
        'old_r2_identity':old_identity,'original_documents':old,'paid_history_binding':paid,
        'verified_artifacts':dict(verified_artifacts),'visual_evaluation':EVALUATION,
        'original_visual_qa_digest':old['image_qa']['qa_digest'],
        'new_first_review_digest':_digest(first),'new_round1_snapshot_digest':new_digest,
        'paid_requests_added':0,'ocr_invocations_added':0,'visual_qa_invocations_added':0,
        'execution_authority':False,'new_paid_authority':False}
    receipt['receipt_digest']=_digest(receipt)
    docs=deepcopy(old);docs['round1_snapshot']=new_r1;docs['first_review']=first
    docs['generation_result']['round1_snapshot_digest']=new_digest
    qa=docs['image_qa'];qa.pop('qa_digest');qa['round1_snapshot_digest']=new_digest
    qa.update(evaluation_kind=EVALUATION,inherited_visual_qa_digest=receipt['original_visual_qa_digest'],sku_only_derivation=receipt)
    qa['qa_digest']=_digest(qa)
    return docs,receipt


def derive_sku_only_r2(old_documents, new_round1_snapshot, *, new_first_review,
                       correction_binding, paid_history_binding, artifact_bytes):
    """Return new documents in memory; caller owns any separately admitted save.

    artifact_bytes is read once by the original, pinned local owner. Only exact
    SHA and byte count are retained here, never another copy of PNG content.
    """
    verified={}
    for digest,raw in artifact_bytes.items():
        _require(type(raw) is bytes and 'sha256:'+hashlib.sha256(raw).hexdigest()==digest,'ARTIFACT_BYTES_CHANGED')
        verified[digest]=len(raw)
    documents,receipt=_build(old_documents,new_round1_snapshot,new_first_review,
        correction_binding,paid_history_binding,verified)
    from shared_platform.publication_r3_image_bridge import validate_r2_identity
    return {'documents':documents,'receipt':receipt,'r2_identity':validate_r2_identity(documents)}


def validate_sku_only_r2_derivation(documents):
    """Rebuild and compare every derived field; never bypass original QA."""
    qa=documents.get('image_qa') or {}; receipt=qa.get('sku_only_derivation')
    _require(isinstance(receipt, Mapping) and receipt.get('schema_version')==SCHEMA,'RECEIPT_REQUIRED')
    unsigned=dict(receipt);seal=unsigned.pop('receipt_digest',None)
    _require(seal==_digest(unsigned),'RECEIPT_DIGEST_CHANGED')
    rebuilt,_=_build(receipt['original_documents'],documents['round1_snapshot'],documents['first_review'],
        receipt['correction_binding'],receipt['paid_history_binding'],receipt['verified_artifacts'])
    _require(all(documents.get(k)==rebuilt[k] for k in _DOCUMENTS),'DERIVED_DOCUMENTS_CHANGED')
    return {'receipt_digest':seal,'visual_evaluation':EVALUATION,
        'original_visual_qa_digest':receipt['original_visual_qa_digest'],'paid_requests_added':0}
