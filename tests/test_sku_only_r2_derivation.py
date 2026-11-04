"""Original owned R1/R2/COMMON -> identity-only image reuse, no provider edge."""
from copy import deepcopy
from pathlib import Path
import hashlib
import pytest

from shared_platform import publication_r3_image_bridge as bridge
from shared_platform.sku_only_r2_derivation import derive_sku_only_r2, validate_sku_only_r2_derivation
from test_round1_workspace_freeze import live
from test_r3_technical_category_evidence import _completed


def _changed(value,old,new):
    if isinstance(value,dict):
        return {k:(deepcopy(v) if k in {'category_evidence_binding','input_lineage_manifest'} else
                   new if k in {'seller_sku','model_sku','sku_id','sku_key','itemNum','sellerSku'} and v==old
                   else _changed(v,old,new)) for k,v in value.items()}
    if isinstance(value,list):return [_changed(v,old,new) for v in value]
    return value


def test_same14_original_bytes_rebind_identity_and_keep_visual_QA_and_paid_history(live,monkeypatch,tmp_path):
    # This product has no localized outputs. Select zero translations through
    # the original proposed-plan producer before it writes any R2 artifact.
    import test_publication_paid_entry as media
    original_workflow=media.Workflow
    class NoLocalizedWorkflow(original_workflow):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            build=self.entry.build_approved_brand_translation_plan
            def no_localized(*a,**k):
                k['selections']={}
                return build(*a,**k)
            monkeypatch.setattr(self.entry,'build_approved_brand_translation_plan',no_localized)
    monkeypatch.setattr(media,'Workflow',NoLocalizedWorkflow)
    store,common,payload,signing,run_id=_completed(live,monkeypatch,tmp_path,single_variant_gallery=True)
    original=bridge.load_r2_documents(payload['product_id']);before=deepcopy(original);db_before=store.path.read_bytes()
    old=original['first_review']['product_facts']['seller_sku'];new='9457'
    first=_changed(original['first_review'],old,new);r1=_changed(original['round1_snapshot'],old,new)
    first.pop('input_lineage_manifest',None);r1.pop('input_lineage_manifest',None)
    r1['first_review_digest']=bridge._canonical_digest(first);r1.pop('snapshot_digest')
    r1['snapshot_digest']=bridge._canonical_digest(r1)
    binding={'offer_id':r1['offer_id'],'old_round1_snapshot_digest':before['round1_snapshot']['snapshot_digest'],
        'new_round1_snapshot_digest':r1['snapshot_digest'],'old_seller_sku':old,'new_seller_sku':new,
        'models':[{'variant_key':first['product_facts']['skus'][0]['source_key'],'old_model_sku':old,'new_model_sku':new}]}
    paid={'offer_id':r1['offer_id'],'history_root':str(tmp_path/'original-history'),
        'ledger_ref':str(tmp_path/'original-history/ledger.jsonl'),'ledger_sha256':'a'*64,
        'confirmed_request_count':18,'unknown_request_count':0,'confirmed_request_ids':['owned-original-'+str(i) for i in range(18)]}
    artifact_bytes={row['artifact_digest']:Path(row['artifact_path']).read_bytes() for row in
        original['generation_result']['assets']+original['translation_result']['assets']}
    assert all('sha256:'+hashlib.sha256(raw).hexdigest()==sha for sha,raw in artifact_bytes.items())
    naive=deepcopy(original);naive.update(round1_snapshot=r1,first_review=first)
    with pytest.raises(ValueError,match='R2_'):
        bridge.validate_r2_identity(naive)  # Original unmodified R2 correctly refuses a new R1.
    value=derive_sku_only_r2(original,r1,new_first_review=first,correction_binding=binding,
        paid_history_binding=paid,artifact_bytes=artifact_bytes)
    docs=value['documents'];receipt=value['receipt'];qa=docs['image_qa']
    assert bridge.validate_r2_identity(docs)==value['r2_identity']
    assert value['r2_identity']['round1_snapshot_digest']==r1['snapshot_digest']
    assert receipt['old_r2_identity']==bridge.validate_r2_identity(original)
    assert qa['inherited_visual_qa_digest']==original['image_qa']['qa_digest']!=qa['qa_digest']
    assert qa['evaluation_kind']=='SKU_IDENTITY_ONLY_DERIVATION_NO_NEW_VISUAL_QA'
    assert receipt['original_documents']['image_qa']==original['image_qa']
    assert docs['first_review']['category_evidence_binding']==original['first_review']['category_evidence_binding']
    assert receipt['paid_history_binding']==paid and receipt['paid_requests_added']==receipt['ocr_invocations_added']==receipt['visual_qa_invocations_added']==0
    assert docs['generation_result']['assets']==original['generation_result']['assets']
    assert docs['translation_result']==original['translation_result'] and docs['translation_plan']==original['translation_plan']
    assert original==before and bridge.load_r2_documents(payload['product_id'])==before and store.path.read_bytes()==db_before
    for field in ('cost_cny','weight_kg','package_cm'):
        wrong=deepcopy(first);wrong['product_facts'][field]=99
        altered=deepcopy(r1);altered['first_review_digest']=bridge._canonical_digest(wrong);altered['fact_snapshot']['product_facts']=deepcopy(wrong['product_facts']);altered.pop('snapshot_digest');altered['snapshot_digest']=bridge._canonical_digest(altered)
        wrong_binding={**binding,'new_round1_snapshot_digest':altered['snapshot_digest']}
        with pytest.raises(ValueError,match='BUSINESS_CHANGED|COMMERCIAL_FACTS_CHANGED'):
            derive_sku_only_r2(original,altered,new_first_review=wrong,correction_binding=wrong_binding,paid_history_binding=paid,artifact_bytes=artifact_bytes)
    for fault in ('bytes','paid-offer','paid-unknown','role','QA','reverse'):
        raw=deepcopy(artifact_bytes);history=deepcopy(paid);old_docs=deepcopy(original);direction=deepcopy(binding)
        if fault=='bytes':raw[next(iter(raw))]=b'changed'
        elif fault=='paid-offer':history['offer_id']='999999'
        elif fault=='paid-unknown':history['unknown_request_count']=1
        elif fault=='role':old_docs['generation_result']['assets'][0]['role']='other-role'
        elif fault=='QA':old_docs['image_qa']['status']='FAILED'
        else:direction['old_round1_snapshot_digest'],direction['new_round1_snapshot_digest']=direction['new_round1_snapshot_digest'],direction['old_round1_snapshot_digest']
        with pytest.raises(ValueError):
            derive_sku_only_r2(old_docs,r1,new_first_review=first,correction_binding=direction,paid_history_binding=history,artifact_bytes=raw)
    changed=deepcopy(docs);changed['generation_result']['assets'][0]['public_url']='https://fixture.invalid/other.png'
    with pytest.raises(ValueError,match='DERIVED_DOCUMENTS_CHANGED'):
        validate_sku_only_r2_derivation(changed)
