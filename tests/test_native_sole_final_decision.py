"""Actual full preparation, signed CLOSED transport and real Windows actor.

No private decision store, synthetic actor, provider EDIT or formal installation.
The closed provider replies are fixtures, not a claim of production permission.
"""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from modules.products import server
from shared_platform import native_sole_final_decision as final
from shared_platform import native_common_technical_execution as technical
from shared_platform import native_windows_actor as actor
from shared_platform import release_store
from shared_platform.final_review_server_admission import ApprovalBlocked
from test_round1_workspace_freeze import live
from test_native_common_technical_execution import _setup
from test_native_common_service_facts import _installed, _closed_signed_transport
from test_native_common_retained_review_graph import _propose_market_inputs, _market


def _completed(live, monkeypatch, tmp_path, *, install=True):
    _propose_market_inputs(live, monkeypatch)
    store, plan, payload, _ = _setup(live, monkeypatch, tmp_path)
    config, _ = _installed(monkeypatch, tmp_path, store)
    calls = _closed_signed_transport(monkeypatch, store, payload)
    code, result = server._prepare_miaoshou_release({'offer_id':payload['product_id'],
        'plan_id':plan['plan_id'], 'confirm_miaoshou_write':True})
    assert code == 200 and result['native_run']['state'] == 'CONFIRMED_WRITE', result
    market = _market(store, plan, result['native_run']['run_id'])
    os_service = actor.bootstrap_service(actor.NativeActorServiceConfig(tmp_path/'os-owner', 'Kyle'))
    os_reader = actor.NativeWindowsActorProfileReader(os_service, os_service.grant_same_user())
    if install:
        with technical._existing_transaction(store) as db:
            for statement in final.TABLES+final.TRIGGERS:db.execute(statement)
    service = final.NativeSoleFinalDecisionService(store, os_reader)
    return service, market, plan, result['native_run']['run_id'], config, calls


def _counts(store):
    with store._connect_readonly() as db:
        return tuple(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
            for table in ('native_sole_final_nonces','native_sole_final_decisions',
                          'release_approvals','approved_publication_snapshots'))


def test_real_complete_graph_native_owner_nonce_approval_and_snapshot_are_atomic(live, monkeypatch, tmp_path):
    service, market, common, run, config, calls = _completed(live, monkeypatch, tmp_path)
    prepared = service.prepare(market['plan_id'])
    assert prepared['manifest']['targets'] and prepared['manifest']['variants']
    assert tuple(r['target_label'] for r in prepared['manifest']['targets']) == prepared['review']['targets']
    assert prepared['execution_authority'] is False and _counts(service.store) == (1,0,0,0)
    approved = service.decide(nonce=prepared['nonce'],review_digest=prepared['review_digest'])
    assert approved['execution_authority'] is False and approved['execution_recheck_required'] is True
    assert _counts(service.store) == (1,1,1,1)
    assert service.store.get_plan(market['plan_id'])['status'] == release_store.PLAN_APPROVED
    assert service.store.get_plan(common['plan_id'])['status'] == release_store.PLAN_PENDING_APPROVAL
    with service.store._connect_readonly() as db:
        assert db.execute('SELECT approval_id FROM release_runs WHERE run_id=?',(run,)).fetchone()[0] is None
        nonce = db.execute('SELECT * FROM native_sole_final_nonces').fetchone()
        assert nonce['decision_id'] == approved['decision_id']
        assert final._hash(nonce['current_read_json'].encode()) == nonce['current_read_sha256']
    assert service.decide(nonce=prepared['nonce'],review_digest=prepared['review_digest']) == approved
    assert _counts(service.store) == (1,1,1,1)
    checked = service.recheck(approved['decision_id'])
    assert checked['execution_recheck_succeeded'] is True and checked['execution_authority'] is False
    assert calls.count('/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail') == 1


def test_two_real_nonce_submissions_serialize_to_one_decision_and_approval(live, monkeypatch, tmp_path):
    service, market, *_ = _completed(live, monkeypatch, tmp_path)
    first, second = service.prepare(market['plan_id']), service.prepare(market['plan_id'])
    def submit(value):return service.decide(nonce=value['nonce'],review_digest=value['review_digest'])
    with ThreadPoolExecutor(max_workers=2) as pool:results = list(pool.map(submit,[first,second]))
    assert results[0] == results[1] and _counts(service.store) == (2,1,1,1)


def test_original_approval_writer_failure_rolls_back_nonce_decision_and_snapshot(live, monkeypatch, tmp_path):
    service, market, *_ = _completed(live, monkeypatch, tmp_path)
    value = service.prepare(market['plan_id'])
    original = service.store._persist_approved_plan_in_transaction
    def fail_after_real_write(*args,**kwargs):
        original(*args,**kwargs)
        raise OSError('owned crash after real approved snapshot write')
    monkeypatch.setattr(service.store,'_persist_approved_plan_in_transaction',fail_after_real_write)
    with pytest.raises(OSError,match='owned crash'):
        service.decide(nonce=value['nonce'],review_digest=value['review_digest'])
    assert _counts(service.store) == (1,0,0,0)
    assert service.store.get_plan(market['plan_id'])['status'] == release_store.PLAN_PENDING_APPROVAL
    with service.store._connect_readonly() as db:
        assert db.execute('SELECT decision_id FROM native_sole_final_nonces').fetchone()[0] is None


def test_technical_signing_json_reformat_keeps_business_approval_without_new_review(live, monkeypatch, tmp_path):
    service, market, common, run, config, _ = _completed(live, monkeypatch, tmp_path)
    value = service.prepare(market['plan_id'])
    signing = config.root/'config/miaoshou.local.json'
    data = json.loads(signing.read_bytes())
    signing.write_text(json.dumps(data,indent=3)+'\n',encoding='utf-8')
    approved = service.decide(nonce=value['nonce'],review_digest=value['review_digest'])
    assert _counts(service.store) == (1,1,1,1)
    assert service.recheck(approved['decision_id'])['execution_recheck_succeeded'] is True


def test_genuine_same_owner_session_refresh_preserves_displayed_review_and_existing_decision(live, monkeypatch, tmp_path):
    service, market, *_ = _completed(live, monkeypatch, tmp_path)
    first = service.prepare(market['plan_id'])
    os_service = service.actor_reader.service
    service.actor_reader = actor.NativeWindowsActorProfileReader(os_service,os_service.grant_same_user())
    renewed = service.refresh_nonce(nonce=first['nonce'],review_digest=first['review_digest'])
    assert renewed['review_digest'] == first['review_digest'] and renewed['nonce'] != first['nonce']
    assert renewed['new_human_review_required'] is False and _counts(service.store) == (2,0,0,0)
    approved = service.decide(nonce=renewed['nonce'],review_digest=renewed['review_digest'])
    service.actor_reader = actor.NativeWindowsActorProfileReader(os_service,os_service.grant_same_user())
    assert service.refresh_nonce(nonce=renewed['nonce'],review_digest=renewed['review_digest']) == approved
    assert service.decide(nonce=renewed['nonce'],review_digest=renewed['review_digest']) == approved
    assert _counts(service.store) == (2,1,1,1)


@pytest.mark.parametrize('damage',['wrong-nonce','wrong-digest','foreign-session','expired'])
def test_real_nonce_is_bound_to_exact_owner_session_review_and_expiry(live, monkeypatch, tmp_path, damage):
    service, market, *_ = _completed(live, monkeypatch, tmp_path)
    value = service.prepare(market['plan_id'])
    if damage == 'wrong-nonce':value['nonce'] = '0'*64
    elif damage == 'wrong-digest':value['review_digest'] = 'sha256:'+'0'*64
    elif damage == 'foreign-session':
        current = service.actor_reader.service
        service.actor_reader = actor.NativeWindowsActorProfileReader(current,current.grant_same_user())
    else:
        original = final.time.time
        monkeypatch.setattr(final.time,'time',lambda:original()+301)
    with pytest.raises(ApprovalBlocked,match='NATIVE_SOLE_FINAL_NONCE_'):
        service.decide(nonce=value['nonce'],review_digest=value['review_digest'])
    assert _counts(service.store) == (1,0,0,0)


def test_original_retained_packet_drift_preserves_existing_approval_and_blocks_recheck(live, monkeypatch, tmp_path):
    service, market, common, run, *_ = _completed(live, monkeypatch, tmp_path)
    value = service.prepare(market['plan_id'])
    approved = service.decide(nonce=value['nonce'],review_digest=value['review_digest'])
    with technical._existing_transaction(service.store) as db:
        db.execute('UPDATE release_target_readbacks SET evidence_json=? WHERE run_id=?',('{}',run))
    before = service.store.path.read_bytes()
    with pytest.raises((ValueError,release_store.ReleaseAuthorizationError)):
        service.recheck(approved['decision_id'])
    assert service.store.path.read_bytes() == before and _counts(service.store) == (1,1,1,1)


def test_native_decision_cannot_authorize_legacy_market_run(live, monkeypatch, tmp_path):
    service, market, *_ = _completed(live, monkeypatch, tmp_path)
    value = service.prepare(market['plan_id'])
    with pytest.raises(release_store.ReleaseAuthorizationError,match='native sole final decision requires'):
        service.store.approve_plan(market['plan_id'],approved_by='Kyle',user_approved=True,
                                  confirmation_token=market['confirmation_token'])
    assert _counts(service.store) == (1,0,0,0)
    service.decide(nonce=value['nonce'],review_digest=value['review_digest'])
    before = service.store.path.read_bytes()
    with technical._existing_transaction(service.store) as db:
        with pytest.raises(release_store.ReleaseAuthorizationError,match='native sole final decision requires'):
            service.store._reject_final_review_legacy_authority(db,market['plan_id'])
    assert service.store.path.read_bytes() == before


def test_missing_explicit_schema_and_caller_actor_never_create_native_authority(live, monkeypatch, tmp_path):
    service, market, *_ = _completed(live, monkeypatch, tmp_path,install=False)
    before = service.store.path.read_bytes()
    with pytest.raises(ApprovalBlocked,match='SCHEMA_NOT_INSTALLED'):service.prepare(market['plan_id'])
    assert service.store.path.read_bytes() == before
    forged = final.NativeSoleFinalDecisionService(service.store,{'approved_by':'Kyle','identity_verified':True})
    with pytest.raises(ApprovalBlocked,match='SERVICE_READER_REQUIRED'):forged.prepare(market['plan_id'])
    assert service.store.path.read_bytes() == before
    missing = release_store.ReleaseStore(tmp_path/'missing-final.sqlite3')
    absent = final.NativeSoleFinalDecisionService(missing,service.actor_reader)
    with pytest.raises((ValueError,release_store.ReleaseAuthorizationError)):
        absent.prepare(market['plan_id'])
    assert not missing.path.exists()
