"""Original immutable COMMON preparation source, market-only derived graph."""
from copy import deepcopy
import pytest

from modules.products import server
from shared_platform import publication_r3_image_bridge as bridge
from shared_platform.r3_common_source_facts import NativeCommonSourceReader
from shared_platform.r3_frozen_review_producer import DomainReviewBlocked
from test_native_r2_service_consumers import live, images_task, _complete_images, _runtime
from test_round1_workspace_freeze import live as original_live
from test_round1_auto_freeze import public_settings


def test_original_full_scope_source_derives_only_market_graph_and_rejects_rehashed_foreign_origins(images_task, monkeypatch, tmp_path):
    v=images_task
    task=_complete_images(v,monkeypatch)
    runtime=_runtime(v,monkeypatch,tmp_path,enabled=True)
    try:
        code,view=server._preview_r3_common_stage({'offer_id':task['scope']['offer_id'],
            'release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON']})
        assert code==200,view
        plan=runtime.store.create_plan(view['common']['plan']['payload'])
        payload=deepcopy(plan['payload'])
        complete=payload['r3_stage_binding']['marketplace_targets']
        assert complete==task['scope']['shops'] and 'miaoshou:COMMON' in complete
        assert payload['r3_stage_binding']['native_preparation_source']['targets']==complete
        reader=NativeCommonSourceReader(runtime.store)
        before=runtime.store.path.read_bytes()
        with runtime.store._connect_readonly() as db:
            db.execute('BEGIN')
            facts=reader.read_source_facts(db,plan['plan_id'])
        assert facts.graph.targets==tuple(label for label in complete if label!='miaoshou:COMMON')
        assert facts.graph.common_payload_digest==plan['payload_digest']
        assert runtime.store.get_plan(plan['plan_id'])['payload']==payload
        assert runtime.store.path.read_bytes()==before
        legacy=deepcopy(payload)
        del legacy['r3_stage_binding']['native_preparation_source']
        legacy['r3_stage_binding']['marketplace_targets']=[label for label in complete if label!='miaoshou:COMMON']
        legacy['plan_id']=bridge.common_stage_plan_id(legacy,offer_id=task['scope']['offer_id'])
        legacy_plan=runtime.store.create_plan(legacy)
        with runtime.store._connect_readonly() as db:
            db.execute('BEGIN')
            legacy_facts=reader.read_source_facts(db,legacy_plan['plan_id'])
        assert legacy_facts.graph.targets==facts.graph.targets
        assert legacy_facts.execution_authority is False
        for drift in ('missing-source','foreign-prepared','foreign-snapshot','unknown-common'):
            bad=deepcopy(payload)
            stage=bad['r3_stage_binding']
            if drift=='missing-source':del stage['native_preparation_source']
            elif drift=='foreign-prepared':stage['native_preparation_source']['prepared_reference']='r1-prepared:foreign'
            elif drift=='foreign-snapshot':stage['native_preparation_source']['round1_snapshot']['snapshot_digest']='sha256:'+'0'*64
            else:stage['marketplace_targets']=[label.replace('miaoshou:COMMON','miaoshou:UNKNOWN') for label in complete]
            # Use the original inert store/producer ID. Rehashing a caller
            # mutation cannot turn it into the stored native preparation.
            bad['plan_id']=bridge.common_stage_plan_id(bad,offer_id=task['scope']['offer_id'])
            invalid=runtime.store.create_plan(bad)
            before=runtime.store.path.read_bytes()
            with runtime.store._connect_readonly() as db:
                db.execute('BEGIN')
                with pytest.raises(DomainReviewBlocked,match='^COMMON_SOURCE_ORIGIN_IDENTITY_INVALID$'):
                    reader.read_source_facts(db,invalid['plan_id'])
            assert runtime.store.path.read_bytes()==before
            assert runtime.store.get_plan(invalid['plan_id'])['status']=='PENDING_APPROVAL'
        with runtime.store._connect_readonly() as db:
            assert db.execute('SELECT COUNT(*) FROM release_runs').fetchone()[0]==0
            assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0]==0
    finally:runtime.close()
