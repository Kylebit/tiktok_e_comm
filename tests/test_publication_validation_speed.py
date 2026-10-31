"""Real owner/decision guards with closed external boundaries, no production state."""
import sqlite3
import json

import pytest

from shared_platform import native_sole_final_decision as final
from test_round1_workspace_freeze import live
from test_native_sole_final_decision import _completed,_counts
from shared_platform.final_review_server_admission import ApprovalBlocked


def test_recheck_current_common_read_releases_sqlite_writer(live, monkeypatch, tmp_path):
    service, market, *_ = _completed(live, monkeypatch, tmp_path)
    prepared_review=service.prepare(market['plan_id'])
    approved=service.decide(nonce=prepared_review['nonce'],review_digest=prepared_review['review_digest'])
    from modules.miaoshou import client
    original=client.NativeCommonDetailObserver.observe
    locks=[]
    def observe(observer, detail_id):
        # This probe obtains the real SQLite write reservation while the
        # actual closed signed READ is in progress. No mock timing assertion.
        with sqlite3.connect(service.store.path, timeout=0) as probe:
            try:probe.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError:locks.append('locked')
            else:locks.append('free');probe.rollback()
        return original(observer,detail_id)
    monkeypatch.setattr(client.NativeCommonDetailObserver,'observe',observe)
    result=service.recheck(approved['decision_id'])
    assert result['execution_recheck_succeeded']
    assert locks == ['free'], locks


def test_all_decision_paths_observe_outside_writer_then_short_cas(live,monkeypatch,tmp_path):
    service,market,*_=_completed(live,monkeypatch,tmp_path)
    from modules.miaoshou import client
    original=client.NativeCommonDetailObserver.observe
    locks=[]
    def observe(observer,detail_id):
        with sqlite3.connect(service.store.path,timeout=0) as probe:
            probe.execute('BEGIN IMMEDIATE')
            probe.execute('CREATE TABLE IF NOT EXISTS independent_writer (value INTEGER)')
            probe.execute('INSERT INTO independent_writer VALUES (1)')
            probe.commit()  # Not only a reservation: no live read lock blocks commit.
            locks.append('committed')
        return original(observer,detail_id)
    monkeypatch.setattr(client.NativeCommonDetailObserver,'observe',observe)
    first=service.prepare(market['plan_id'])
    second=service.refresh_nonce(nonce=first['nonce'],review_digest=first['review_digest'])
    approved=service.decide(nonce=second['nonce'],review_digest=second['review_digest'])
    assert service.recheck(approved['decision_id'])['execution_recheck_succeeded']
    assert locks==['committed']*4
    assert _counts(service.store)==(2,1,1,1)


@pytest.mark.parametrize('damage',['common_payload','retained_packet','targets','decision','signing_config','expired_observation'])
def test_observation_cannot_override_concurrent_mutable_or_scope_change(live,monkeypatch,tmp_path,damage):
    service,market,common,run,config,_=_completed(live,monkeypatch,tmp_path)
    first=service.prepare(market['plan_id'])
    approved=service.decide(nonce=first['nonce'],review_digest=first['review_digest'])
    original=service._observe_market_current
    def changed(plan_id):
        observation=original(plan_id)
        if damage=='signing_config':
            path=config.root/'config/miaoshou.local.json';value=json.loads(path.read_bytes())
            value['app_id']='owned-different-signing-app';path.write_text(json.dumps(value),encoding='utf-8')
        elif damage=='expired_observation':
            from shared_platform import native_common_signing_context as signing
            now=signing.time.monotonic()
            monkeypatch.setattr(signing.time,'monotonic',lambda:now+31)
        else:
            # A deliberately corrupt OWNED test DB models a concurrently
            # changed protected input, never a migration of real business data.
            with sqlite3.connect(service.store.path) as db:
                if damage in {'common_payload','targets'}:
                    db.execute('DROP TRIGGER trg_release_plan_immutable')
                    if damage=='common_payload':
                        db.execute('UPDATE release_plans SET payload_json=? WHERE plan_id=?',('{}',common['plan_id']))
                    else:db.execute('UPDATE release_plans SET target_labels_json=? WHERE plan_id=?',('[]',market['plan_id']))
                elif damage=='retained_packet':
                    db.execute('UPDATE release_target_readbacks SET evidence_json=? WHERE run_id=?',('{}',run))
                else:db.execute('UPDATE release_plans SET status=? WHERE plan_id=?',('SUPERSEDED',market['plan_id']))
        return observation
    monkeypatch.setattr(service,'_observe_market_current',changed)
    with pytest.raises((ApprovalBlocked,ValueError)):
        service.recheck(approved['decision_id'])
    assert _counts(service.store)==(1,1,1,1)


def test_fresh_common_rejection_is_not_replaced_by_retained_success(live,monkeypatch,tmp_path):
    service,market,*_=_completed(live,monkeypatch,tmp_path)
    first=service.prepare(market['plan_id'])
    approved=service.decide(nonce=first['nonce'],review_digest=first['review_digest'])
    from test_common_detail_observation import install_response,LocalResponse
    calls=install_response(monkeypatch,LocalResponse(b'{"result":"fail","code":"appNoPermission","data":null}'))
    with pytest.raises(ApprovalBlocked,match='CURRENT_COMMON_READ_UNVERIFIED'):
        service.recheck(approved['decision_id'])
    assert len(calls)==1 and _counts(service.store)==(1,1,1,1)


def test_caller_receipt_cannot_become_private_current_observation(live,monkeypatch,tmp_path):
    from shared_platform.native_common_signing_context import NativeCommonSigningContextReader
    from test_native_common_signing_context import _config
    from test_native_common_technical_execution import _setup
    store,plan,*_=_setup(live,monkeypatch,tmp_path)
    reader=NativeCommonSigningContextReader(_config(tmp_path),store)
    with store._connect_readonly() as db:
        with pytest.raises(ApprovalBlocked,match='GENUINE_CURRENT_OBSERVATION_REQUIRED'):
            reader.consume_current(db,plan['plan_id'],observation={'status':'CURRENT_SIGNED_COMMON_READ_ACCEPTED'})


