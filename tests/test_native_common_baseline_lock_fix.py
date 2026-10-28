"""Real successor commit/observer entry points may not reenter TaskDB writers."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import pytest

from modules.products import server
from shared_platform import agent_prepared_source_successor as source
from shared_platform import native_common_baseline_successor as successor
from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
from shared_platform import manual_common_creation as manual,manual_common_successor as child
from shared_platform import operations_publication_common as common
from test_agent_prepared_source_successor_v4 import real_chain
from test_round1_workspace_freeze import live
from test_native_common_service_facts import _closed_signed_transport
from test_agent_prepared_source_successor import git
from shared_platform.operations_runtime import runtime_manifest
from shared_platform.workbench_engine import WorkbenchEngine


def _bound(v,monkeypatch):
    source.continue_fourth_source(v.engine,v.profile,v.store,task_id=v.task_id,
        expected_adoption_digest=v.adoption_digest,actor_reader=object(),decision_reader=v.reader)
    v.operations.engine=v.engine;v.operations.profile=v.profile
    server._NATIVE_FINAL_SERVICE._new_decision_execution_enabled=True
    s=object.__new__(AgentPreparedCommonService)
    s.store=v.store;s.engine=v.engine;s.profile=v.profile;s.task_id=v.task_id;s.baseline_decision=v.reader
    s.ledger=manual.ManualCommonCreateLedger(v.store.path)
    s.correction=child.ManualCommonCorrectionLedger(v.store.path);s.successor=None
    s._actor=lambda:None  # Closed OS identity seam; source/HMAC readers are real.
    monkeypatch.setattr(server,'_AGENT_PREPARED_COMMON_SERVICE',s,raising=False)
    # Model the real original baseline observation, after verified third CREATE.
    with v.engine.transaction() as db:
        row=v.engine._row(db,v.task_id);action=json.loads(row['action_json'])
        action['receipt_binding']['plan_id']=v.plan['plan_id']
        action['receipt_binding']['payload_digest']=v.plan['payload_digest']
        db.execute('UPDATE workbench_execution SET action_json=? WHERE task_id=?',(source.adoption._bytes(action).decode(),v.task_id))
        v.engine._event(db,v.task_id,'domain_observation_pending',action)
    root=v.profile.root
    old_task=v.engine.get(v.task_id)
    with v.engine.transaction() as db:
        old_rows={name:[tuple(row) for row in db.execute('SELECT * FROM '+name)] for name in (
            'agent_prepared_publication_adoptions','agent_prepared_source_successors','agent_prepared_source_successors_v2',
            'agent_prepared_source_successors_v3','agent_prepared_source_successors_v4')}
    (root/'shared_platform/agent_prepared_common_service.py').write_text('# local lock repair source 5\n')
    engine_file=root/'shared_platform/workbench_engine.py'
    engine_file.write_bytes((Path(__file__).resolve().parents[1]/'shared_platform/workbench_engine.py').read_bytes())
    git(root,'add','-f','shared_platform/agent_prepared_common_service.py','shared_platform/workbench_engine.py')
    git(root,'commit','-m','local lock repair source 5')
    current=replace(v.profile,version=git(root,'rev-parse','HEAD'),manifest_digest=runtime_manifest(root))
    v.engine=WorkbenchEngine(current.data_root/'tasks.db',source._version(current));v.profile=current
    new_path=root/'same-authority-wrong-path.json';new_path.write_bytes(v.reader.path.read_bytes())
    with pytest.raises(ValueError,match='REUSES_ORIGINAL_DECISION_REQUIRED'):
        source.continue_fifth_source(v.engine,v.profile,v.store,task_id=v.task_id,expected_adoption_digest=v.adoption_digest,
            actor_reader=object(),decision_reader=successor.PinnedBaselineDecision(new_path,v.reader.sha256))
    record=source.continue_fifth_source(v.engine,v.profile,v.store,task_id=v.task_id,expected_adoption_digest=v.adoption_digest,
        actor_reader=object(),decision_reader=v.reader)
    assert record['decision_sha256']==v.reader.sha256 and record['new_edit_attempts']==0
    assert source.continue_fifth_source(v.engine,v.profile,v.store,task_id=v.task_id,expected_adoption_digest=v.adoption_digest,
        actor_reader=object(),decision_reader=v.reader)==record
    after=v.engine.get(v.task_id)
    assert after['checkpoint']==old_task['checkpoint'] and after['pending_observation']==old_task['pending_observation']
    with v.engine.transaction() as db:
        assert old_rows=={name:[tuple(row) for row in db.execute('SELECT * FROM '+name)] for name in old_rows}
    v.operations.engine=v.engine;v.operations.profile=v.profile
    s.engine=v.engine;s.profile=v.profile
    return s


def test_real_commit_and_observer_do_not_reenter_task_writer(real_chain,monkeypatch):
    v=real_chain;s=_bound(v,monkeypatch)
    original=v.engine.transaction;active=[False]
    @contextmanager
    def no_nested_writer():
        assert not active[0],'NESTED_TASK_BEGIN_IMMEDIATE'
        with original() as db:
            active[0]=True
            try:yield db
            finally:active[0]=False
    monkeypatch.setattr(v.engine,'transaction',no_nested_writer)
    payload=successor.select_payload(v.store,v.payload);preview=v.store.preview_plan(payload)
    calls=_closed_signed_transport(monkeypatch,v.store,payload)
    data=dict(offer_id=payload['product_id'],release_stage='R3_COMMON',publication_targets=['miaoshou:COMMON'],
        plan_id=preview['plan_id'],confirmation_token=preview['confirmation_token'],confirm_miaoshou_write=True)
    # A real race after complete source/domain validation must fail the locked
    # action CAS, without a receipt or new provider claim.
    action=v.engine.get(v.task_id)['pending_observation']
    prepare=successor.prepare_domain_receipt
    def raced(*args,**kwargs):
        prepared=prepare(*args,**kwargs)
        changed=deepcopy(action);changed['reason']='changed after validation'
        with sqlite3.connect(v.engine.store.path) as db:
            db.execute('UPDATE workbench_execution SET action_json=? WHERE task_id=?',(json.dumps(changed),v.task_id))
        return prepared
    with monkeypatch.context() as race:
        race.setattr(successor,'prepare_domain_receipt',raced)
        with pytest.raises(ValueError,match='DOMAIN_RECEIPT_CURRENT_TASK_CHANGED'):s.commit(data)
    assert calls==[]
    with sqlite3.connect(v.engine.store.path) as db:
        db.execute('UPDATE workbench_execution SET action_json=? WHERE task_id=?',(json.dumps(action),v.task_id))
    code,result=s.commit(data)
    assert code==200,result
    with v.store._connect_readonly() as db:
        runs=db.execute('SELECT * FROM release_runs ORDER BY created_at').fetchall()
        assert len(runs)==2 and runs[0]['technical_execution_state']=='UNKNOWN' and runs[1]['technical_execution_state']=='CONFIRMED_WRITE'
        assert dict(runs[0])==v.snapshot['run']
    assert calls.count('/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail')==1
    task=v.engine.get(v.task_id)
    assert task['execution_state']=='waiting_domain'  # Existing gallery gate remains.
    before=list(calls)
    # Real observer enters its receipt verifier; it may queue the unchanged
    # retained baseline but cannot issue another provider mutation.
    observed=common.observe(v.engine,task,v.profile,server_module=server)
    assert observed is True
    assert calls==before
