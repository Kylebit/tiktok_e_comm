"""Actual complete domain producer and native decision, private data only."""
import json
import pytest

from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform.r3_native_final_approval import (
    NativeSoleDecisionConsumer, initialize_private_fixture_actor_profile,
)
from test_local_operator_original_review import _complete_domain_fixture


def prepared(tmp_path, monkeypatch):
    decisions, documents, dashboard, data, market, fake, reservation = _complete_domain_fixture(tmp_path, monkeypatch)
    actor = initialize_private_fixture_actor_profile(decisions)
    grant = decisions.sessions.grant_same_user()
    review = decisions.prepare(grant, reservation_id=reservation, marketplace_plan_id=data['plan_id'])
    return decisions, actor, grant, review, data, fake


def table_rows(decisions, names):
    with decisions._transaction(readonly=True) as db:
        return {name: [tuple(row) for row in db.execute('SELECT * FROM '+name)] for name in names}


def test_actual_sole_decision_atomically_persists_release_approval_and_v4(tmp_path, monkeypatch):
    decisions, actor, grant, review, data, fake = prepared(tmp_path, monkeypatch)
    result = NativeSoleDecisionConsumer(decisions, actor_reader=actor).decide(
        grant, nonce=review['nonce'], review_digest=review['review_digest'])
    plan = decisions.authority.store.get_plan(data['plan_id'])
    assert plan['status'] == 'APPROVED' and plan['approval']['approval_id'] == result['release_approval_id']
    snapshot = decisions.authority.store.approved_publication_snapshot(offer_id=data['offer_id'], plan_id=data['plan_id'])
    assert snapshot['plan_id'] == data['plan_id']
    assert [row['target_label'] for row in snapshot['publication_targets']] == list(review['review']['targets'])
    rows = table_rows(decisions, ['private_final_decisions','private_domain_final_approvals','private_final_nonces'])
    assert len(rows['private_final_decisions']) == len(rows['private_domain_final_approvals']) == 1
    binding = json.loads(rows['private_domain_final_approvals'][0][2])
    assert binding['schema_version'] == 'native-sole-decision-binding/v1'
    assert binding['review']['plan_id'] == data['plan_id']
    assert binding['actor']['owner_sid'] == decisions.sessions._owner()
    assert binding['actor']['logical_profile'] == 'Kyle'
    assert binding['actor']['mapping_source'] == 'EXPLICIT_PRIVATE_FIXTURE_BOOTSTRAP'
    assert binding['actor']['instance_id'] == decisions.authority.marker['instance_id']
    assert binding['actor']['evidence_kind'] == 'SYNTHETIC_TEST_ONLY'
    assert binding['execution_authority'] is False and result['execution_authority'] is False
    assert binding['release_approval_id'] == plan['approval']['approval_id']
    assert rows['private_final_nonces'][0][4] == rows['private_final_decisions'][0][0]
    assert fake.mutations == 1


def test_same_native_click_replay_preserves_one_approval_and_frozen_snapshot(tmp_path, monkeypatch):
    decisions, actor, grant, review, data, fake = prepared(tmp_path, monkeypatch)
    consumer = NativeSoleDecisionConsumer(decisions, actor_reader=actor)
    first = consumer.decide(grant, nonce=review['nonce'], review_digest=review['review_digest'])
    names=['private_final_decisions','private_domain_final_approvals','private_final_nonces','release_approvals','approved_publication_snapshots']
    before = table_rows(decisions, names)
    second = consumer.decide(grant, nonce=review['nonce'], review_digest=review['review_digest'])
    assert first['release_approval_id'] == second['release_approval_id']
    assert second['execution_authority'] is False and table_rows(decisions,names) == before
    assert len(before['approved_publication_snapshots']) == 1 and fake.mutations == 1


def test_snapshot_failure_rolls_back_nonce_decision_and_release_rows(tmp_path, monkeypatch):
    from shared_platform import release_store
    decisions, actor, grant, review, data, fake = prepared(tmp_path, monkeypatch)
    names=['private_final_decisions','private_domain_final_approvals','private_final_nonces','release_approvals','approved_publication_snapshots']
    before=table_rows(decisions,names)
    def fail(*args, **kwargs): raise RuntimeError('injected snapshot failure')
    monkeypatch.setattr(release_store,'_persist_publication_snapshot_in_transaction',fail)
    with pytest.raises(RuntimeError, match='injected snapshot failure'):
        NativeSoleDecisionConsumer(decisions,actor_reader=actor).decide(grant,nonce=review['nonce'],review_digest=review['review_digest'])
    assert table_rows(decisions,names)==before
    assert decisions.authority.store.get_plan(data['plan_id'])['status']=='PENDING_APPROVAL'
    assert fake.mutations == 1


def test_missing_real_reader_stays_blocked_before_any_store_access():
    class ForbiddenStore:
        def __getattribute__(self,name): raise AssertionError('untrusted store accessed')
    with pytest.raises(ApprovalBlocked, match='COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED'):
        NativeSoleDecisionConsumer(ForbiddenStore(), actor_reader={'approved_by':'Kyle'}).decide(
            object(), nonce='not-authority', review_digest='not-authority')


def test_private_legacy_decision_read_contract_remains_unchanged(tmp_path,monkeypatch):
    decisions, actor, grant, review, data, fake=prepared(tmp_path,monkeypatch)
    result=decisions.decide(grant,nonce=review['nonce'],review_digest=review['review_digest'])
    assert decisions.approval(grant,review_digest=review['review_digest'])==result
    assert result['private_stage']=='APPROVED' and result['execution_authority'] is False
    assert decisions.authority.store.get_plan(data['plan_id'])['approval'] is None
    with decisions._transaction(readonly=True) as db:
        raw=db.execute('SELECT domain_binding_json FROM private_domain_final_approvals').fetchone()[0]
    from shared_platform.common_offer_authority_store import canonical_bytes
    assert raw.encode()==canonical_bytes(review['review'])


def test_synthetic_release_approval_cannot_enter_legacy_store_or_runner(tmp_path,monkeypatch):
    from shared_platform.release_store import ReleaseAuthorizationError
    from shared_platform.registered_r3_legacy_publish_guard import require_legacy_publish_admission, RegisteredR3PublishAdmissionBlocked
    decisions, actor, grant, review, data, fake=prepared(tmp_path,monkeypatch)
    NativeSoleDecisionConsumer(decisions,actor_reader=actor).decide(grant,nonce=review['nonce'],review_digest=review['review_digest'])
    plan=decisions.authority.store.get_plan(data['plan_id'])
    with pytest.raises(ReleaseAuthorizationError,match='synthetic'):
        decisions.authority.store.start_run(data['plan_id'])
    with pytest.raises(RegisteredR3PublishAdmissionBlocked):
        require_legacy_publish_admission(data['offer_id'],release_store=decisions.authority.store,plan_id=data['plan_id'])
    with decisions._transaction(readonly=True) as db:
        assert db.execute('SELECT count(*) FROM release_runs WHERE plan_id=?',(data['plan_id'],)).fetchone()[0]==0
        assert db.execute('SELECT COALESCE(sum(t.attempts),0) FROM release_target_runs t JOIN release_runs r ON t.run_id=r.run_id WHERE r.plan_id=?',(data['plan_id'],)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM release_target_failure_events f JOIN release_runs r ON f.run_id=r.run_id WHERE r.plan_id=?',(data['plan_id'],)).fetchone()[0]==0
    assert fake.mutations == 1


def test_existing_private_approval_is_not_silently_upgraded_or_rewritten(tmp_path,monkeypatch):
    from shared_platform.common_offer_authority_store import CommonAuthorityBlocked
    decisions, actor, grant, review, data, fake=prepared(tmp_path,monkeypatch)
    decisions.decide(grant,nonce=review['nonce'],review_digest=review['review_digest'])
    names=['private_final_decisions','private_domain_final_approvals','private_final_nonces','release_approvals','approved_publication_snapshots']
    before=table_rows(decisions,names)
    with pytest.raises(CommonAuthorityBlocked,match='NATIVE_DECISION_CONTRACT_DIFFERENT'):
        NativeSoleDecisionConsumer(decisions,actor_reader=actor).decide(grant,nonce=review['nonce'],review_digest=review['review_digest'])
    assert table_rows(decisions,names)==before and fake.mutations==1


def test_actor_mapping_drift_consumes_no_nonce_or_decision(tmp_path,monkeypatch):
    decisions, actor, grant, review, data, fake=prepared(tmp_path,monkeypatch)
    names=['private_final_decisions','private_domain_final_approvals','private_final_nonces','release_approvals','approved_publication_snapshots']
    before=table_rows(decisions,names)
    value=json.loads(actor.path.read_bytes());value['owner_sid']='S-1-5-21-wrong-fixture-owner'
    actor.path.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(ApprovalBlocked,match='NATIVE_ACTOR_PROFILE_BINDING_INVALID'):
        NativeSoleDecisionConsumer(decisions,actor_reader=actor).decide(grant,nonce=review['nonce'],review_digest=review['review_digest'])
    assert table_rows(decisions,names)==before and fake.mutations==1


def test_existing_synthetic_run_cannot_claim_a_target_attempt(tmp_path,monkeypatch):
    from shared_platform.release_store import ReleaseAuthorizationError, _utc_now
    decisions, actor, grant, review, data, fake=prepared(tmp_path,monkeypatch)
    result=NativeSoleDecisionConsumer(decisions,actor_reader=actor).decide(grant,nonce=review['nonce'],review_digest=review['review_digest'])
    # A preexisting row is deliberately inserted in private fixture storage.
    # This proves the target guard itself, even if a historical run existed.
    run='private-preexisting-run';label=review['review']['targets'][0];now=_utc_now()
    with decisions._transaction() as db:
        db.execute("INSERT INTO release_runs(run_id,plan_id,approval_id,status,created_at,updated_at) VALUES(?,?,?,'PENDING',?,?)",
            (run,data['plan_id'],result['release_approval_id'],now,now))
        db.execute("INSERT INTO release_target_runs(run_id,target_label,idempotency_key,status,attempts,created_at,updated_at) VALUES(?,?,?,'PENDING',0,?,?)",
            (run,label,'private-native-target',now,now))
    names=['release_runs','release_target_runs','release_target_failure_events','release_target_submissions','release_target_readbacks']
    before=table_rows(decisions,names)
    with pytest.raises(ReleaseAuthorizationError,match='synthetic'):
        decisions.authority.store.begin_target(run,label)
    assert table_rows(decisions,names)==before
    with decisions._transaction(readonly=True) as db:
        assert db.execute('SELECT attempts FROM release_target_runs WHERE run_id=? AND target_label=?',(run,label)).fetchone()[0]==0
    assert fake.mutations==1
