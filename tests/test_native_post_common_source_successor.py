"""Original terminal signed COMMON survives one pre-final software successor."""
from dataclasses import replace
from copy import deepcopy
import hashlib
import json
import subprocess

import pytest

from modules.products import server
from shared_platform import operations_publication_common as common
from shared_platform import native_frozen_source_successor as frozen_source
from shared_platform import workbench_publication_native as native
from shared_platform.operations_runtime import runtime_manifest
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.release_store import ReleaseAuthorizationError
from test_native_frozen_source_successor import _ready,_new_source
from test_round1_workspace_freeze import live
from test_round1_auto_freeze import public_settings
from test_native_common_service_facts import _installed,_closed_signed_transport


def _completed(live,monkeypatch,tmp_path,*,with_ready_receipt=False):
    engine,task,profile,frozen=_ready(live,monkeypatch)
    expected=frozen_source.observe(engine,task['task_id'],profile=profile,release_store=live['store'])
    profile,engine=_new_source(profile)
    frozen_source.register(engine,task['task_id'],profile=profile,release_store=live['store'],expected=expected)
    operations=server._NATIVE_FINAL_SERVICE._operations
    monkeypatch.setattr(operations,'engine',engine);monkeypatch.setattr(operations,'profile',profile)
    task=engine.get(task['task_id'])
    code,value=server._preview_r3_common_stage({'offer_id':live['offer'],'release_stage':'R3_COMMON',
        'publication_targets':['miaoshou:COMMON']})
    assert code==200,value
    plan=live['store'].create_plan(value['common']['plan']['payload'])
    from test_native_common_technical_execution import _install_owned
    _install_owned(live['store'],tmp_path)
    _installed(monkeypatch,tmp_path,live['store'])
    calls=_closed_signed_transport(monkeypatch,live['store'],plan['payload'])
    code,result=server._prepare_miaoshou_release({'offer_id':live['offer'],'plan_id':plan['plan_id'],
        'confirm_miaoshou_write':True})
    assert code==200 and result['native_run']['state']=='CONFIRMED_WRITE',result
    if with_ready_receipt:
        engine.register_executor('owned-common-observer',['publication'],engine.release)
        token=engine.claim(task['task_id'],'owned-common-observer')['lease_token']
        task=engine.get(task['task_id'])
        binding=common._verified_binding(task,{'plan':plan},profile)
        engine.await_domain(task['task_id'],token,label='Original COMMON technical readiness',
            reason='Original service context now available',receipt_binding=binding)
        monkeypatch.setattr(server._NATIVE_FINAL_SERVICE,'_new_decision_execution_enabled',True)
        waiting=engine.get(task['task_id']);view=common._read(server,waiting)
        assert common.observe(engine,waiting,profile,server_module=server) is True,(
            view['common']['status'],common._native_request_readiness(server,waiting,view))
    engine.register_executor('owned-common-worker',['publication'],engine.release)
    token=engine.claim(task['task_id'],'owned-common-worker')['lease_token']
    task=engine.get(task['task_id'])
    binding=common._verified_binding(task,{'plan':plan},profile)
    engine.await_domain(task['task_id'],token,label='Original technical candidate gap',
        reason='MARKETPLACE_CATEGORY_EVIDENCE_REQUIRED',receipt_binding=binding)
    return engine,engine.get(task['task_id']),profile,frozen,plan,result['native_run']['run_id'],calls


def _later(profile):
    path=profile.root/'shared_platform/owned_post_common_consumer.py'
    path.write_text('# original completed COMMON consumer repair\n',encoding='utf-8')
    def git(*args):return subprocess.check_output(['git','-C',str(profile.root),'-c',
        'user.name=Owned source successor','-c','user.email=owned@example.invalid',*args],text=True).strip()
    git('add','-f','--',str(path.relative_to(profile.root)));git('commit','-m','Owned post COMMON source')
    current=replace(profile,version=git('rev-parse','HEAD'),manifest_digest=runtime_manifest(profile.root))
    return current,WorkbenchEngine(current.data_root/'tasks.db',{'code_version':current.version,
        'environment':current.environment,'manifest_digest':current.manifest_digest})


def test_original_signed_common_and_pending_task_survive_one_pre_final_source(live,monkeypatch,tmp_path):
    engine,task,profile,frozen,plan,run_id,calls=_completed(live,monkeypatch,tmp_path)
    from shared_platform import native_post_common_source_successor as source
    expected=source.observe(engine,task['task_id'],profile=profile,release_store=live['store'])
    before_events=engine.store.events(task['task_id']);before_plan=live['store'].get_plan(plan['plan_id'])
    before_run=live['store'].get_run(run_id);calls_before=list(calls)
    later,current=_later(profile)
    record=source.register(current,task['task_id'],profile=later,release_store=live['store'],expected=expected)
    assert record['execution_authority'] is False and calls==calls_before
    actual=current.get(task['task_id'])
    assert actual['pending_observation']==task['pending_observation']
    assert actual['checkpoint']==task['checkpoint'] and actual['steps']==task['steps']
    assert actual['scope']==task['scope'] and actual['version']==current.release
    assert current.store.events(task['task_id'])[1:]==before_events
    assert current.explicit_new_post_task_ids()==(task['task_id'],)
    assert native.read_frozen(actual,later)==frozen
    assert native.read_images(actual,later)==expected['predecessors']['images']
    assert common._verified_binding(actual,{'plan':before_plan},later)==task['pending_observation']['receipt_binding']
    assert live['store'].get_plan(plan['plan_id'])==before_plan and live['store'].get_run(run_id)==before_run
    # Later ordinary channel ledger progress must not turn the old, terminal
    # COMMON into UNKNOWN. This is an owned ledger contract, not marketplace
    # publication evidence or a substitute for the real sole-final gate.
    future=live['store'].create_plan({'plan_id':'owned-later-channel-ledger',
        'product_id':live['offer'],'seller_sku':frozen['internal_sku'],
        'product_package_id':'owned-package','content_package_id':'owned-content','targets':['tiktok:LH_PH'],
        **{key:deepcopy(plan['payload'][key]) for key in ('source_product_identity','sku_lineage','product_revision')
           if key in plan['payload']}})
    live['store'].approve_plan(future['plan_id'],approved_by='Kyle',user_approved=True,
        confirmation_token=future['confirmation_token'])
    live['store'].start_run(future['plan_id'])
    assert native.read_images(actual,later)==expected['predecessors']['images']
    assert current.explicit_new_post_task_ids()==(task['task_id'],)
    with pytest.raises(ValueError,match='ALREADY_REGISTERED'):
        source.register(current,task['task_id'],profile=later,release_store=live['store'],expected=expected)


def test_unknown_or_changed_common_and_preexisting_approval_cannot_register(live,monkeypatch,tmp_path):
    engine,task,profile,_,plan,run_id,_=_completed(live,monkeypatch,tmp_path)
    from shared_platform import native_post_common_source_successor as source
    store=live['store']
    def observe():return source.observe(engine,task['task_id'],profile=profile,release_store=store)
    original=observe()
    with store._connect() as db:
        db.execute("UPDATE release_runs SET status='RUNNING',technical_execution_state='UNKNOWN' WHERE run_id=?",(run_id,))
    with pytest.raises(ValueError,match='SINGLE_TERMINAL_COMMON_REQUIRED'):observe()
    with store._connect() as db:
        db.execute("UPDATE release_runs SET status='SUCCEEDED',technical_execution_state='CONFIRMED_WRITE' WHERE run_id=?",(run_id,))
        row=dict(db.execute('SELECT * FROM release_target_readbacks WHERE run_id=?',(run_id,)).fetchone())
        bad=json.loads(row['evidence_json']);bad['checks']['images']=False
        raw=json.dumps(bad,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        db.execute('UPDATE release_target_readbacks SET evidence_json=?,evidence_digest=? WHERE run_id=?',
                   (raw,hashlib.sha256(raw.encode()).hexdigest(),run_id))
    with pytest.raises(ValueError):observe()
    with store._connect() as db:
        db.execute('UPDATE release_target_readbacks SET evidence_json=?,evidence_digest=? WHERE run_id=?',
                   (row['evidence_json'],row['evidence_digest'],run_id))
    assert observe()==original
    store.approve_plan(plan['plan_id'],approved_by='Kyle',user_approved=True,confirmation_token=plan['confirmation_token'])
    with pytest.raises(ValueError,match='FINAL_OR_CHANNEL_EFFECT_EXISTS'):observe()


def test_registered_source_rejects_scope_history_paid_and_common_tamper(live,monkeypatch,tmp_path):
    engine,task,profile,_,plan,run_id,_=_completed(live,monkeypatch,tmp_path)
    from shared_platform import native_post_common_source_successor as source
    expected=source.observe(engine,task['task_id'],profile=profile,release_store=live['store'])
    later,current=_later(profile)
    bad=deepcopy(expected);bad['scope']['shops']=['ozon:RU']
    with pytest.raises(ValueError,match='EXPECTED_ORIGINAL_OBSERVATION_CHANGED'):
        source.register(current,task['task_id'],profile=later,release_store=live['store'],expected=bad)
    source.register(current,task['task_id'],profile=later,release_store=live['store'],expected=expected)
    actual=current.get(task['task_id'])
    from pathlib import Path
    journal=Path(expected['predecessors']['paid']['directory'])/'events.jsonl';raw=journal.read_bytes()
    journal.write_bytes(raw+b'\nowned-negative')
    try:
        with pytest.raises(ValueError):native.read_images(actual,later)
    finally:journal.write_bytes(raw)
    with live['store']._connect() as db:
        row=dict(db.execute('SELECT * FROM release_target_submissions WHERE run_id=?',(run_id,)).fetchone())
        value=json.loads(row['evidence_json']);value['submission_accepted']=False
        encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        db.execute('UPDATE release_target_submissions SET evidence_json=?,evidence_digest=? WHERE run_id=?',
                   (encoded,hashlib.sha256(encoded.encode()).hexdigest(),run_id))
    try:
        with pytest.raises(ReleaseAuthorizationError,match='ACCEPTED_EDIT_RECEIPT_CHANGED'):native.read_frozen(actual,later)
    finally:
        with live['store']._connect() as db:
            db.execute('UPDATE release_target_submissions SET evidence_json=?,evidence_digest=? WHERE run_id=?',
                       (row['evidence_json'],row['evidence_digest'],run_id))
    assert native.read_images(actual,later)==expected['predecessors']['images']
    with current.transaction() as db:
        row=db.execute('SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type=?',
                       (task['task_id'],source.EVENT)).fetchone()
        value=json.loads(row['detail_json']);value['record']['scope']['shops']=['ozon:RU']
        value['record_sha256']=frozen_source._digest(value['record'])
        db.execute('UPDATE workbench_events SET detail_json=? WHERE id=?',(json.dumps(value),row['id']))
    with pytest.raises(ValueError,match='ORIGINAL_TASK_SCOPE_CHANGED'):current.explicit_new_post_task_ids()
    with current.transaction() as db:
        db.execute('UPDATE workbench_events SET detail_json=? WHERE id=?',(row['detail_json'],row['id']))
        earlier=db.execute("SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type='domain_observation_pending'",
                           (task['task_id'],)).fetchone()
        altered=json.loads(earlier['detail_json']);altered['reason']='owned-mutated-original-history'
        db.execute('UPDATE workbench_events SET detail_json=? WHERE id=?',(json.dumps(altered),earlier['id']))
    with pytest.raises(ValueError,match='ORIGINAL_HISTORY_CHANGED'):current.explicit_new_post_task_ids()


def test_actual_common_ready_engine_receipt_is_preserved_not_a_final_approval(live,monkeypatch,tmp_path):
    engine,task,profile,_,_,_,calls=_completed(live,monkeypatch,tmp_path,with_ready_receipt=True)
    from shared_platform import native_post_common_source_successor as source
    with engine.readonly_snapshot() as db:
        receipt=dict(db.execute('SELECT * FROM workbench_receipts WHERE task_id=?',(task['task_id'],)).fetchone())
    assert receipt['receipt_key'].startswith('common-ready:')
    before=list(calls)
    with engine.transaction() as db:
        db.execute('UPDATE workbench_receipts SET digest=? WHERE receipt_key=?',('0'*64,receipt['receipt_key']))
    with pytest.raises(ValueError,match='UNRECOGNIZED_ENGINE_RECEIPT'):
        source.observe(engine,task['task_id'],profile=profile,release_store=live['store'])
    with engine.transaction() as db:
        db.execute('UPDATE workbench_receipts SET digest=? WHERE receipt_key=?',(receipt['digest'],receipt['receipt_key']))
        db.execute('INSERT INTO workbench_receipts VALUES(?,?,?)',('common-ready:foreign',task['task_id'],'0'*64))
    with pytest.raises(ValueError,match='UNRECOGNIZED_ENGINE_RECEIPT'):
        source.observe(engine,task['task_id'],profile=profile,release_store=live['store'])
    with engine.transaction() as db:
        db.execute('DELETE FROM workbench_receipts WHERE receipt_key=?',('common-ready:foreign',))
    expected=source.observe(engine,task['task_id'],profile=profile,release_store=live['store'])
    later,current=_later(profile)
    source.register(current,task['task_id'],profile=later,release_store=live['store'],expected=expected)
    assert current.explicit_new_post_task_ids()==(task['task_id'],)
    assert native.read_images(current.get(task['task_id']),later)==expected['predecessors']['images']
    with current.readonly_snapshot() as db:
        assert dict(db.execute('SELECT * FROM workbench_receipts WHERE task_id=?',(task['task_id'],)).fetchone())==receipt
    assert calls==before
