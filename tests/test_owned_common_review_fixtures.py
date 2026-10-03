"""Separate actual native completion from historical private normalized data.

This owned positive needs the reviewed native service leaf in the composition.
It never creates a final decision or executes a marketplace/provider action.
"""
from shared_platform.native_common_edit_boundary import EDIT_PATH
from shared_platform.release_store import PLAN_PENDING_APPROVAL
from owned_common_review_fixtures import native_completed_marketplace
import pytest


def test_real_native_completed_matrix_has_one_write_and_no_common_or_final_approval(tmp_path, monkeypatch):
    with native_completed_marketplace(tmp_path, monkeypatch) as (documents, dashboard, store, view, io):
        common, market = view['common'], view['marketplace']
        review = market['native_final_review']
        assert common['plan']['status'] == PLAN_PENDING_APPROVAL
        assert common['run']['approval_id'] is None
        assert common['run']['technical_execution_state'] == 'CONFIRMED_WRITE'
        assert common['new_common_human_approval_needed'] is False
        assert io.calls.count(EDIT_PATH) == io.mutations == 1
        assert market['plan']['status'] == PLAN_PENDING_APPROVAL
        assert review['schema_version'] == 'native-sole-final-review/v1'
        assert review['approval_saved'] is False and review['decision_id'] is None
        assert review['execution_authority'] is False
        assert market['targets'] == documents['round1_snapshot']['canonical_targets']
        assert review['targets'] == market['targets']
        manifest = market['preview']['review_manifest']
        assert [row['target_label'] for row in manifest['targets']] == market['targets']
        assert manifest['variants'] and manifest['copy_sets'] and manifest['image_sets']
        assert market['target_results'] == []
        assert market['native_technical_details']['execution_authority'] is False
        assert market['native_technical_details']['provider_edit_permission'] == 'UNKNOWN'
        with store._connect_readonly() as db:
            assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM native_sole_final_decisions').fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM native_sole_final_nonces').fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM release_target_runs').fetchone()[0] == 1


def test_real_native_signed_wire_lineage_is_immutable_and_same_request_is_not_resent(tmp_path, monkeypatch):
    from modules.products import server
    from owned_common_review_fixtures import native_ready_context
    from test_service_common_observation import assert_native_receipt, common_evidence
    with native_ready_context(tmp_path, monkeypatch, pretty_wire=True) as (_, store, plan, payload, _, request, io):
        code, value = server._prepare_miaoshou_release(request)
        assert code == 200 and value['native_run']['state'] == 'CONFIRMED_WRITE', value
        # Read the actual persisted run; this adapter supplies no authority.
        actual = {'run':store.get_run(value['native_run']['run_id'])}
        evidence = assert_native_receipt(store, actual, io, io.factory_calls)
        assert io.responses[-1].startswith(b'{\n') and io.mutations == 1
        before = list(io.calls)
        code, repeated = server._prepare_miaoshou_release(request)
        assert code == 200 and repeated['idempotent'] is True, repeated
        assert io.calls == before and io.mutations == 1
        assert common_evidence(store, actual)[1] == evidence
        assert actual['run']['approval_id'] is None and actual['run']['targets'][0]['attempts'] == 1


def test_real_native_successor_only_reads_and_retains_original_seven_predecessor_fields(tmp_path, monkeypatch):
    from copy import deepcopy
    from modules.products import server
    from owned_common_review_fixtures import native_ready_context
    from test_native_common_technical_execution import _successor
    from test_service_common_observation import assert_native_receipt, common_evidence
    with native_ready_context(tmp_path, monkeypatch, pretty_wire=True) as (_, store, plan, payload, _, request, io):
        code, value = server._prepare_miaoshou_release(request)
        assert code == 200 and value['native_run']['state'] == 'CONFIRMED_WRITE', value
        original = {'run':store.get_run(value['native_run']['run_id'])}
        predecessor = deepcopy(common_evidence(store, original)[1])
        successor, _, _ = _successor(store, payload, 'owned-real-read-reuse')
        code, blocked = server._prepare_miaoshou_release({**request,'plan_id':successor['plan_id']})
        assert code == 409 and blocked['error']=='COMMON_CONFIRMED_WRITE_CAP_EXHAUSTED', blocked
        code, reused = server._prepare_miaoshou_release({'offer_id':payload['product_id'],
            'plan_id':successor['plan_id'], 'reuse_miaoshou_readback':True})
        assert code == 200 and reused['native_run']['state']=='READONLY_REUSE', reused
        assert reused['external_writes_performed']==[] and io.mutations==1
        current = {'run':store.get_run(reused['native_run']['run_id'])}
        evidence = assert_native_receipt(store, current, io, io.factory_calls)
        target = original['run']['targets'][0]
        assert evidence['predecessor'] == {'plan_id':plan['plan_id'], 'run_id':original['run']['run_id'],
            'payload_digest':plan['payload_digest'], 'common_status':target['status'],
            'common_external_id':target['external_id'],
            'common_readback_evidence_digest':target['readback']['evidence_digest'],
            'common_readback_verified_at':target['readback']['verified_at']}
        assert common_evidence(store, original)[1] == predecessor
        with store._connect_readonly() as db:
            assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0]==0


def test_real_native_wrong_readback_keeps_unknown_claim_and_does_not_reissue_edit(tmp_path, monkeypatch):
    import base64
    import json
    from types import SimpleNamespace
    from modules.products import server
    from modules.miaoshou import client
    from owned_common_review_fixtures import native_ready_context
    with native_ready_context(tmp_path, monkeypatch, damage_readback=True) as (_, store, plan, payload, _, request, io):
        original_open = client.urllib.request.urlopen
        committed_reads = []
        def closed_open(request, timeout):
            assert request.full_url in {
                client.OPEN_BASE_URL + EDIT_PATH,
                client.OPEN_BASE_URL + client.COMMON_DETAIL_OBSERVATION_PATH}
            if request.full_url.endswith(client.COMMON_DETAIL_OBSERVATION_PATH) and io.mutations == 1:
                # Inspect the real committed ledger before each post-acceptance READ;
                # no packet, authority or successful readback is manufactured here.
                with store._connect_readonly() as db:
                    row = db.execute('SELECT * FROM release_target_submissions').fetchone()
                    assert row is not None
                    packet = json.loads(row['evidence_json'])
                    assert json.loads(base64.b64decode(packet['edit_wire']['business_base64']))['result'] == 'success'
                    assert db.execute('SELECT technical_execution_state FROM release_runs').fetchone()[0] == 'UNKNOWN'
                    assert db.execute('SELECT COUNT(*) FROM release_target_readbacks').fetchone()[0] == 0
                    committed_reads.append(tuple(row))
            return original_open(request, timeout)  # Original closed signature/business/claim checks.
        monkeypatch.setattr(client.urllib.request, 'urlopen', closed_open)
        monkeypatch.setattr(client.urllib.request, 'build_opener',
                            lambda *handlers: SimpleNamespace(open=closed_open))
        code, failure = server._prepare_miaoshou_release(request)
        assert code == 409 and failure['error'] == 'COMMON_OBSERVATION_VERIFIED_READBACK_REQUIRED', failure
        assert failure['state'] == 'UNKNOWN' and failure['edit_request_attempted'] is True
        assert failure['external_write_outcome'] == 'UNKNOWN' and failure['edit_acceptance_retained'] is True
        assert io.mutations == 1 and len(committed_reads) == 1
        run_id = failure['native_run_id']
        original = store.get_run(run_id)
        original_plan = store.get_plan(plan['plan_id'])
        before = list(io.calls)
        reads_before = io.reads
        with store._connect_readonly() as db:
            accepted_before = tuple(db.execute('SELECT * FROM release_target_submissions').fetchone())
            target_identity = tuple(db.execute('SELECT run_id,target_label,attempts FROM release_target_runs').fetchone())
        code, held = server._prepare_miaoshou_release(request)
        assert code == 409 and held['error'] == 'COMMON_OBSERVATION_VERIFIED_READBACK_REQUIRED', held
        assert held['state'] == held['native_state'] == held['readback_outcome'] == 'UNKNOWN'
        assert held['native_run_id'] == run_id and held['edit_acceptance_retained'] is True
        assert held['request_attempted'] is True and held['edit_request_attempted'] is False
        assert held['native_attempt_not_redispatched'] is True and held['external_writes_performed'] == []
        assert io.mutations == 1 and io.reads == reads_before + 1
        assert io.calls[len(before):] == [client.COMMON_DETAIL_OBSERVATION_PATH]
        assert len(committed_reads) == 2 and committed_reads[0] == committed_reads[1] == accepted_before
        current = store.get_run(run_id)
        assert current['run_id'] == original['run_id'] and current['plan_id'] == original['plan_id']
        assert current['approval_id'] is None and current['targets'][0]['attempts'] == 1
        assert store.get_plan(plan['plan_id']) == original_plan
        with store._connect_readonly() as db:
            assert tuple(db.execute('SELECT * FROM release_target_submissions').fetchone()) == accepted_before
            assert tuple(db.execute('SELECT run_id,target_label,attempts FROM release_target_runs').fetchone()) == target_identity
            assert db.execute('SELECT COUNT(*) FROM release_runs').fetchone()[0] == 1
            assert db.execute('SELECT COUNT(*) FROM release_target_readbacks').fetchone()[0] == 0
            assert db.execute('SELECT technical_execution_state FROM release_runs').fetchone()[0] == 'UNKNOWN'
            assert db.execute('SELECT approval_id FROM release_runs').fetchone()[0] is None
            assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0



def test_real_native_lost_edit_reply_revision_churn_preserves_identity_and_unknown_no_replay(tmp_path, monkeypatch):
    from modules.products import server
    from owned_common_review_fixtures import native_ready_context
    from shared_platform import release_control
    with native_ready_context(tmp_path, monkeypatch, fail_edit=True) as (_, store, plan, payload, _, request, io):
        code, failure = server._prepare_miaoshou_release(request)
        assert code == 502 and failure['state']=='UNKNOWN' and failure['edit_request_attempted'] is True, failure
        run_id = failure['native_run_id']
        retained = store.get_run(run_id)
        original = store.get_plan(plan['plan_id'])
        before = list(io.calls)
        monkeypatch.setattr(release_control, 'build_release_dashboard',
            lambda **kw: pytest.fail('UNKNOWN must keep original identity before current revision'))
        code, held = server._prepare_miaoshou_release(request)
        assert code==409 and held['error']=='COMMON_PRIOR_ATTEMPT_RECONCILIATION_REQUIRED', held
        assert held['native_run']['run_id']==run_id
        assert io.calls==before and io.mutations==1
        assert store.get_plan(plan['plan_id'])==original and store.get_run(run_id)==retained
        assert retained['approval_id'] is None and retained['targets'][0]['attempts']==1
