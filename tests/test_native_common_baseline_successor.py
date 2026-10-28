"""Exact UNKNOWN successor and SDK durability; all transports/DBs are owned."""
import json
from hashlib import sha256
from pathlib import Path
import pytest
from copy import deepcopy
from types import SimpleNamespace

from shared_platform import native_common_baseline_successor as successor
from shared_platform.release_store import ReleaseAuthorizationError


def test_missing_new_typed_authority_is_closed(tmp_path):
    assert successor.installed_decision({}) is None
    path = tmp_path/'not-authorized.json'
    path.write_text(json.dumps({'schema_version':successor.DECISION_SCHEMA,
        'decision_status':'NOT_AUTHORIZED'}), encoding='utf-8')
    reader = successor.PinnedBaselineDecision(path,sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ReleaseAuthorizationError,match='NEW_EXPLICIT_EDIT_DECISION_REQUIRED'):
        reader.read()


def test_caller_dictionary_is_not_an_installed_decision():
    with pytest.raises(ReleaseAuthorizationError,match='PIN_REQUIRED'):
        successor.installed_decision({'agent_prepared_common_baseline_decision':{'approved':True}})


from test_round1_workspace_freeze import live
from test_native_common_technical_execution import _setup
from test_native_common_service_facts import _installed,_closed_signed_transport
from test_service_common_observation import ClosedCommonTransport
from shared_platform import native_common_technical_execution as technical
from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
from modules.products import server


def authorized_owned_successor(live,monkeypatch,tmp_path):
    store,plan,payload,_=_setup(live,monkeypatch,tmp_path)
    _installed(monkeypatch,tmp_path,store)
    with technical._existing_transaction(store) as db:
        facts=technical._service_facts(store,db,plan['plan_id'])
        old=technical._reserve_in_transaction(store,db,facts)
        technical._consume_in_transaction(db,facts,old['run_id'])
    # Historical 2b3a8ce shape: UNKNOWN1, no response retained. This is not a
    # new dispatch and cannot acquire an accepted receipt from the later run.
    with store._connect_readonly() as db:
        snapshot=dict(run=dict(db.execute('SELECT * FROM release_runs').fetchone()),
            target=dict(db.execute('SELECT * FROM release_target_runs').fetchone()))
        for table in ('release_target_submissions','release_target_failure_events','release_target_readbacks'):
            snapshot[table]=[]
    adoption=dict(offer_id=payload['product_id'])
    decision=dict(schema_version=successor.DECISION_SCHEMA,decision_status='USER_AUTHORIZED',actor='Kyle',
        action_class='AUTHORIZE_ONE_COMMON_BASELINE_EDIT_SUCCESSOR',maximum_new_edit_attempts=1,
        combined_edit_attempt_cap=2,combined_create_attempt_cap=3,further_create_authorized=False,
        original_unknown_retained=True,original_outcome='UNKNOWN',zero_write_proven=False,
        same_task_and_preparation_root_required=True,candidate_content_changes_authorized=False,
        marketplace_approval=False,standing_authority=False,new_unknown_stops=True,
        task_id='TASK-owned',offer_id=payload['product_id'],original_adoption_digest=successor._digest(adoption),
        original_plan_id=plan['plan_id'],original_run_id=old['run_id'],original_payload_digest=plan['payload_digest'],
        original_ledger_sha256=successor._digest(snapshot),common_detail_id=payload['product_id'],
        current_detail_sha256=successor._digest(ClosedCommonTransport.detail_for(payload)))
    path=tmp_path/'edit-decision.json';path.write_text(json.dumps(decision),encoding='utf-8')
    reader=successor.PinnedBaselineDecision(path,sha256(path.read_bytes()).hexdigest())
    service=object.__new__(AgentPreparedCommonService)
    service.store=store;service.baseline_decision=reader
    # Native preparation, real signed SDK/claim/readback and native graph are
    # used here. Genuine task HMAC/Git v4 is covered separately by its tests.
    service._current=lambda:(dict(task_id=decision['task_id']),adoption)
    monkeypatch.setattr(server,'_AGENT_PREPARED_COMMON_SERVICE',service,raising=False)
    new_payload=successor.select_payload(store,payload)
    new_plan=store.create_plan(new_payload)
    return store,plan,payload,snapshot,reader,new_plan,new_payload,service


@pytest.mark.parametrize('lost',[False,True],ids=['accepted-complete','new-unknown-stops'])
def test_one_new_managed_edit_preserves_old_unknown_and_cap(live,monkeypatch,tmp_path,lost):
    store,old,payload,snapshot,reader,plan,new_payload,_=authorized_owned_successor(live,monkeypatch,tmp_path)
    calls=_closed_signed_transport(monkeypatch,store,new_payload,fail_edit=lost)
    request=dict(offer_id=payload['product_id'],plan_id=plan['plan_id'],confirm_miaoshou_write=True)
    code,result=server._prepare_native_common_ledger(request,store)
    assert code==(502 if lost else 200),result
    with store._connect_readonly() as db:
        assert dict(db.execute('SELECT * FROM release_runs WHERE run_id=?',(reader.read()['original_run_id'],)).fetchone())==snapshot['run']
        assert dict(db.execute('SELECT * FROM release_target_runs WHERE run_id=?',(reader.read()['original_run_id'],)).fetchone())==snapshot['target']
        assert db.execute('SELECT count(*) FROM release_runs').fetchone()[0]==2
        assert db.execute('SELECT sum(attempts) FROM release_target_runs').fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM release_target_submissions WHERE run_id=?',(reader.read()['original_run_id'],)).fetchone()[0]==0
    before=list(calls)
    code,again=server._prepare_native_common_ledger(request,store)
    assert code==(409 if lost else 200),again
    assert calls==before and calls.count('/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail')==1
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        with pytest.raises(ReleaseAuthorizationError,match='ACCEPTED_EDIT_RECEIPT_MISSING'):
            technical.accepted_edit_for_recovery(store,db,old['plan_id'],reader.read()['original_run_id'])
        if not lost:
            from shared_platform.native_common_budget_facts import census_same_snapshot
            from shared_platform.r3_common_source_facts import NativeCommonSourceReader
            raw=census_same_snapshot(store,db,new_payload,NativeCommonSourceReader(store).read_source_facts(db,plan['plan_id']))
            assert [r['run_id'] for r in raw['unresolved_attempts']]==[reader.read()['original_run_id']]
            assert successor.unresolved_after_authorized_successor(store,db,new_payload,raw)==[]


def test_preimage_drift_refuses_before_second_reserve(live,monkeypatch,tmp_path):
    store,old,payload,snapshot,reader,plan,new_payload,_=authorized_owned_successor(live,monkeypatch,tmp_path)
    from modules.miaoshou import client
    from test_common_detail_observation import LocalResponse
    calls=_closed_signed_transport(monkeypatch,store,new_payload)
    original=client.urllib.request.urlopen
    def changed(request,timeout):
        value=original(request,timeout)
        if request.full_url.endswith(client.COMMON_DETAIL_OBSERVATION_PATH):
            detail=ClosedCommonTransport.detail_for(payload);detail['title']='drift'
            return LocalResponse(json.dumps(dict(result='success',data=dict(editCommonCollectBoxDetail=detail,ossMd5='md5'))).encode(),url=request.full_url)
        return value
    monkeypatch.setattr(client.urllib.request,'build_opener',lambda *a:SimpleNamespace(open=changed))
    monkeypatch.setattr(client.urllib.request,'urlopen',changed)
    code,result=server._prepare_native_common_ledger(dict(offer_id=payload['product_id'],plan_id=plan['plan_id'],confirm_miaoshou_write=True),store)
    assert code==409 and result['error']=='COMMON_BASELINE_SUCCESSOR_OFFICIAL_PREIMAGE_CHANGED',result
    with store._connect_readonly() as db:assert db.execute('SELECT count(*) FROM release_runs').fetchone()[0]==1
