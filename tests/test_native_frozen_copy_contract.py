"""Actual new-task decision consumes complete model displays; no provider seam."""
from copy import deepcopy
import pytest
from test_round1_workspace_freeze import live
from test_round1_auto_freeze import public_settings
from owned_new_publication_task import complete_new_task
from shared_platform import native_sole_final_execution as execution
from modules.products.release_adapters import _native_frozen_target


def test_real_new_three_model_copy_consumer_keeps_variants_and_rejects_payload_tampering(live,monkeypatch,tmp_path):
    runtime,market,*_=complete_new_task(live,monkeypatch,tmp_path)
    try:
        review=runtime.prepare(market['plan_id'])
        decision=runtime.decide(nonce=review['nonce'],review_digest=review['review_digest'])
        payload=market['payload'];label='tiktok:LH_PH'
        with runtime.operation() as service:
            active=execution._NativeExecution(service,decision['decision_id'],runtime)
            active.plan_id=market['plan_id'];token=execution._ACTIVE.set(active)
            try:
                frozen=_native_frozen_target(payload,channel='tiktok',site='LH_PH',target_label=label)
                shown=payload['product_facts']['content_by_target'][label]
                expected=payload['r3_marketplace_binding']['route']['target_facts'][label]
                assert frozen==expected and len(frozen['copy']['variants'])==3
                assert shown['variants']==[{'model_sku':r['seller_sku'],'display_name':r['display_name']}
                    for r in frozen['copy']['variants']]
                for damage in ('display','model','missing','extra','unknown-field'):
                    changed=deepcopy(payload);copy=changed['product_facts']['content_by_target'][label]
                    if damage=='display':copy['variants'][0]['display_name']='Unapproved display'
                    elif damage=='model':copy['variants'][0]['model_sku']='9999'
                    elif damage=='missing':copy.pop('variants')
                    elif damage=='extra':copy['variants'].append(deepcopy(copy['variants'][0]))
                    else:copy['unapproved_copy_field']='not approved'
                    with pytest.raises(RuntimeError,match='NATIVE_TARGET_COPY_PERSISTED_PAYLOAD_CHANGED'):
                        _native_frozen_target(changed,channel='tiktok',site='LH_PH',target_label=label)
            finally:execution._ACTIVE.reset(token)
        assert runtime.store.get_plan(market['plan_id'])['payload']==payload
    finally:runtime.close()
