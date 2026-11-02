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


def _completed(live,monkeypatch,tmp_path,*,with_ready_receipt=False,registered_source=False):
    if registered_source:
        import owned_native_parent_profile as owned
        from shared_platform.native_common_edit_boundary import REGISTERED_SOURCE_PATHS
        monkeypatch.setattr(owned,'SOURCE_PATHS',tuple(dict.fromkeys((*owned.SOURCE_PATHS,*REGISTERED_SOURCE_PATHS))))
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
    _,active=_installed(monkeypatch,tmp_path,live['store'])
    if registered_source:
        monkeypatch.setattr(active,'_source_root',profile.root)
        monkeypatch.setattr(active,'_source_digest',active._read_source_digest())
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
        diagnostic=common._native_request_readiness(server,waiting,view)
        assert view['common']['status']=='RETAINED_TECHNICAL_BASELINE' and diagnostic['status']=='READY'
        original={'receipt_id':'common-ready:'+common._digest([binding,view['common']['status'],diagnostic['receipt_digest']]),
                  'action_id':waiting['pending_observation']['action_id']}
        def verify(value,expected):
            latest=common._read(server,waiting)
            return (value==original and expected==binding and latest['common']['status']==view['common']['status']
                    and common._verified_binding(waiting,latest['common'],profile)==binding
                    and common._native_request_readiness(server,waiting,latest)==diagnostic)
        assert engine.accept_domain_receipt(task['task_id'],original,verify)
    engine.register_executor('owned-common-worker',['publication'],engine.release)
    token=engine.claim(task['task_id'],'owned-common-worker')['lease_token']
    task=engine.get(task['task_id'])
    binding=common._verified_binding(task,{'plan':plan},profile)
    engine.await_domain(task['task_id'],token,label='Original technical candidate gap',
        reason='MARKETPLACE_CATEGORY_EVIDENCE_REQUIRED',receipt_binding=binding)
    return engine,engine.get(task['task_id']),profile,frozen,plan,result['native_run']['run_id'],calls


def _later(profile,*,name='owned_post_common_consumer',registered_change=False):
    path=profile.root/('shared_platform/'+name+'.py')
    path.write_text('# original completed COMMON consumer repair\n',encoding='utf-8')
    def git(*args):return subprocess.check_output(['git','-C',str(profile.root),'-c',
        'user.name=Owned source successor','-c','user.email=owned@example.invalid',*args],text=True).strip()
    if registered_change:
        registered=profile.root/'modules/products/server.py'
        raw=registered.read_bytes();newline=b'\r\n' if b'\r\n' in raw else b'\n'
        registered.write_bytes(raw+newline+b'# Owned registered source generation.'+newline)
        git('add','-f','--',str(registered.relative_to(profile.root)))
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


def test_second_same_phase_source_preserves_original_receipt_and_reconciliation(live,monkeypatch,tmp_path):
    engine,task,profile,_,_,_,calls=_completed(live,monkeypatch,tmp_path,with_ready_receipt=True)
    from shared_platform import native_post_common_source_successor as source
    first=source.observe(engine,task['task_id'],profile=profile,release_store=live['store'])
    p1,e1=_later(profile)
    source.register(e1,task['task_id'],profile=p1,release_store=live['store'],expected=first)
    with e1.readonly_snapshot() as db:
        original_event=dict(db.execute('SELECT * FROM workbench_events WHERE task_id=? AND event_type=?',
                                      (task['task_id'],source.EVENT)).fetchone())
    current=e1.get(task['task_id']);action=current['pending_observation']
    e1.observe_reconciliation(task['task_id'],None,'Owned startup observer diagnostic',action_id=action['action_id'])
    receipt={'outcome':'applied','provider_readback_ref':first['common']['completion']['readback_digest']}
    e1.reconcile_external(task['task_id'],receipt,lambda value,scope:
        value==receipt and scope==task['scope'] and source._common(live['store'],first['common']['plan_id'])==first['common'])
    e1.register_executor('owned-same-phase-observer',['publication'],e1.release)
    token=e1.claim(task['task_id'],'owned-same-phase-observer')['lease_token']
    e1.await_domain(task['task_id'],token,label='Original remaining technical serializer gap',
                    reason='Owned full-candidate diagnostic',receipt_binding=action['receipt_binding'])
    second=source.observe(e1,task['task_id'],profile=p1,release_store=live['store'])
    assert json.loads(second['checkpoint_raw'])['reconciliation']==receipt
    p2,e2=_later(p1,name='owned_second_serializer_repair')
    before=list(calls)
    source.register(e2,task['task_id'],profile=p2,release_store=live['store'],expected=second)
    actual=e2.get(task['task_id'])
    assert e2.explicit_new_post_task_ids()==(task['task_id'],)
    assert native.read_images(actual,p2)==second['predecessors']['images']
    assert native.read_frozen(actual,p2)==second['predecessors']['images']['native_r1']
    assert actual['checkpoint']['reconciliation']==receipt and calls==before
    with e2.readonly_snapshot() as db:
        events=[dict(e) for e in db.execute('SELECT * FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',
                                           (task['task_id'],source.EVENT))]
    assert len(events)==2 and events[0]==original_event
    assert json.loads(events[1]['detail_json'])['record']['from_release']==e1.release
    with pytest.raises(ValueError,match='ALREADY_REGISTERED'):
        source.observe(e2,task['task_id'],profile=p2,release_store=live['store'])


def test_terminal_common_new_action_uses_real_final_review_not_old_common_wake(live,monkeypatch,tmp_path):
    from owned_new_publication_task import complete_new_task
    from shared_platform import publication_r3_image_bridge as bridge
    native_material=bridge.build_marketplace_review_material
    from shared_platform.release_store import ReleaseStore
    import test_native_common_service_facts as service_fixture
    from test_native_scoped_service_launcher import _configured_market
    original_config=service_fixture._service_config;startup_configs=[]
    def configured(*args,**kwargs):
        config=original_config(*args,**kwargs);_configured_market(config);startup_configs.append(config);return config
    monkeypatch.setattr(service_fixture,'_service_config',configured)
    original_create=ReleaseStore.create_plan;deferred=[]
    def defer_market(store,payload,**kwargs):
        if payload.get('r3_marketplace_binding') and not deferred:
            deferred.append(payload)
            return store.preview_plan(payload)
        return original_create(store,payload,**kwargs)
    monkeypatch.setattr(ReleaseStore,'create_plan',defer_market)
    runtime,market,task,calls=complete_new_task(live,monkeypatch,tmp_path)
    # The shared fixture's old material-only policy override must not replace
    # the actual native producer/rebuilder used by this end-to-end observer.
    monkeypatch.setattr(bridge,'build_marketplace_review_material',native_material)
    engine,profile=runtime._operations.engine,runtime._operations.profile
    try:
        from shared_platform.ozon_runtime_credentials import PinnedOzonCredentials
        from shared_platform.ozon_stock_warehouse_decision import resolve_warehouse_decision
        from shared_platform import publication_r3_image_bridge as bridge
        pinned=PinnedOzonCredentials('12345','owned-secret','a'*64)
        monkeypatch.setattr('shared_platform.ozon_runtime_credentials.required_pinned_ozon_credentials',lambda:pinned)
        documents=native.load_service_r2_documents(live['offer'],runtime._operations)
        warehouse=resolve_warehouse_decision(offer_id=live['offer'],round1=documents['round1_snapshot'],pinned=pinned,
            post_bound=lambda *a,**k:{'warehouses':[dict(warehouse_id=71,status='active',is_kgt=False)]})
        bridge._report_path(live['offer'],'ozon-warehouse-readback.json').write_text(json.dumps(warehouse),encoding='utf-8')
        assert runtime.store.get_plan(market['plan_id']) is None
        current=engine.get(task['task_id']);action=current['pending_observation']
        view=common._read(server,current)
        assert view['common']['status']=='RETAINED_TECHNICAL_BASELINE'
        assert not view.get('marketplace',{}).get('candidate')
        binding=common._verified_binding(current,view['common'],profile)
        original=common._native_request_readiness(server,current,view)
        assert original['status']=='READY'
        historical={'receipt_id':'common-ready:'+common._digest([binding,'RETAINED_TECHNICAL_BASELINE',original['receipt_digest']]),
                    'action_id':action['action_id']}
        # Exercise the original historical technical receipt protocol against
        # genuine retained domain facts, without rewriting any old evidence.
        def verify(value,scope):
            latest=common._read(server,current)
            return (value==historical and scope==binding
                    and common._verified_binding(current,latest['common'],profile)==binding
                    and common._native_request_readiness(server,current,latest)==original)
        assert engine.accept_domain_receipt(task['task_id'],historical,verify)
        engine.register_executor('owned-final-candidate-observer',['publication'],engine.release)
        token=engine.claim(task['task_id'],'owned-final-candidate-observer')['lease_token']
        engine.await_domain(task['task_id'],token,label='Remaining local candidate preparation',
                            reason='Owned original action',receipt_binding=binding)
        waiting=engine.get(task['task_id']);before=list(calls)
        assert waiting['pending_observation']['action_id']!=historical['action_id']
        diagnostics=[];materialize=common._materialize_final
        def traced(*args,**kwargs):
            try:return materialize(*args,**kwargs)
            except common.PostCommonReadinessBlocked as error:
                diagnostics.append(error.diagnostic);raise
        monkeypatch.setattr(common,'_materialize_final',traced)
        # A real missing local R3 configuration stays a technical observation,
        # never a new UNKNOWN COMMON, provider request or repeated old wake.
        assert common.observe(engine,waiting,profile,server_module=server) is False
        assert diagnostics[-1]['blockers']==['R3_CONFIGURATION_BLOCKED']
        with engine.readonly_snapshot() as db:
            assert engine._row(db,task['task_id'])['state']=='waiting_domain'
            assert db.execute('SELECT count(*) FROM workbench_receipts WHERE task_id=?',(task['task_id'],)).fetchone()[0]==1
        assert calls==before
        monkeypatch.setattr(server,'R3_STARTUP_CONFIG',startup_configs[-1])
        resumed=common.observe(engine,waiting,profile,server_module=server)
        if not resumed:
            actual_stage=common._read(server,waiting);actual_market=actual_stage.get('marketplace') or {}
            detail={key:actual_market.get(key) for key in ('status','blockers','native_preflight')}
            detail['native_error']=(actual_market.get('native_final_review') or {}).get('error')
            pytest.fail(json.dumps({'diagnostics':diagnostics,'actual_market':detail},ensure_ascii=False))
        with engine.readonly_snapshot() as db:
            assert engine._row(db,task['task_id'])['state']=='queued'
            rows=[dict(r) for r in db.execute('SELECT * FROM workbench_receipts WHERE task_id=?',(task['task_id'],))]
        assert len(rows)==2 and any(r['receipt_key']==historical['receipt_id'] for r in rows)
        actual=common._read(server,engine.get(task['task_id']))
        diagnostic=common._post_common_readiness(server,engine.get(task['task_id']),actual)
        assert diagnostic['status']=='READY' and diagnostic['execution_authority'] is False
        assert calls==before
        with runtime.store._connect_readonly() as db:
            assert db.execute('SELECT count(*) FROM native_sole_final_decisions').fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM release_approvals').fetchone()[0]==0
    finally:runtime.close()


def test_second_source_verifies_both_original_generation_specific_common_wakes(live,monkeypatch,tmp_path):
    from shared_platform import native_post_common_source_successor as source,native_common_edit_boundary as boundary
    engine,task,profile,_,_,_,calls=_completed(live,monkeypatch,tmp_path,with_ready_receipt=True,registered_source=True)
    first=source.observe(engine,task['task_id'],profile=profile,release_store=live['store'])
    p1,e1=_later(profile,registered_change=True)
    source.register(e1,task['task_id'],profile=p1,release_store=live['store'],expected=first)
    operations=server._NATIVE_FINAL_SERVICE._operations
    monkeypatch.setattr(operations,'engine',e1);monkeypatch.setattr(operations,'profile',p1)
    replacement=boundary.NativeCommonEditBoundary(boundary._ACTIVE._config)
    monkeypatch.setattr(replacement,'_source_root',p1.root)
    monkeypatch.setattr(replacement,'_source_digest',replacement._read_source_digest())
    monkeypatch.setattr(boundary,'_ACTIVE',replacement)
    waiting=e1.get(task['task_id']);view=common._read(server,waiting)
    diagnostic=common._native_request_readiness(server,waiting,view)
    assert diagnostic['status']=='READY' and diagnostic['receipt_digest']!=first['common']['technical_admission_digest']
    binding=waiting['pending_observation']['receipt_binding']
    second={'receipt_id':'common-ready:'+common._digest([binding,'RETAINED_TECHNICAL_BASELINE',diagnostic['receipt_digest']]),
            'action_id':waiting['pending_observation']['action_id']}
    def verify(value,expected):
        actual=common._read(server,waiting)
        return (value==second and expected==binding and common._verified_binding(waiting,actual['common'],p1)==binding
                and common._native_request_readiness(server,waiting,actual)==diagnostic)
    assert e1.accept_domain_receipt(task['task_id'],second,verify)
    e1.register_executor('owned-second-generation-observer',['publication'],e1.release)
    token=e1.claim(task['task_id'],'owned-second-generation-observer')['lease_token']
    e1.await_domain(task['task_id'],token,label='Original serializer remains pending',reason='Owned original local gap',receipt_binding=binding)
    with e1.readonly_snapshot() as db:
        receipts=[dict(r) for r in db.execute('SELECT * FROM workbench_receipts WHERE task_id=? ORDER BY receipt_key',(task['task_id'],))]
    assert len(receipts)==2
    observed=source.observe(e1,task['task_id'],profile=p1,release_store=live['store'])
    assert isinstance(observed['common_ready_receipt'],list) and len(observed['common_ready_receipt'])==2
    generation=next(p for p in observed['common_ready_receipt'] if p.get('readiness_source'))
    assert generation['technical_admission_digest']==diagnostic['receipt_digest']
    assert generation['readiness_source']['release']==e1.release
    bad=deepcopy(observed)
    next(p for p in bad['common_ready_receipt'] if p.get('readiness_source'))['readiness_source']['files']['modules/products/server.py']='0'*64
    with pytest.raises(ValueError,match='COMMON_READY_SOURCE_FILES_CHANGED'):
        source.observe(e1,task['task_id'],profile=p1,release_store=live['store'],retained_receipts=bad['common_ready_receipt'])
    with e1.transaction() as db:
        db.execute('UPDATE workbench_receipts SET receipt_key=? WHERE receipt_key=?',('common-ready:foreign-second',second['receipt_id']))
    with pytest.raises(ValueError,match='COMMON_READY_ACCEPTANCE_UNPROVEN'):
        source.observe(e1,task['task_id'],profile=p1,release_store=live['store'])
    with e1.transaction() as db:
        db.execute('UPDATE workbench_receipts SET receipt_key=? WHERE receipt_key=?',(second['receipt_id'],'common-ready:foreign-second'))
    with e1.transaction() as db:db.execute('INSERT INTO workbench_receipts VALUES(?,?,?)',('common-ready:foreign-third',task['task_id'],'0'*64))
    with pytest.raises(ValueError,match='UNRECOGNIZED_ENGINE_RECEIPT'):
        source.observe(e1,task['task_id'],profile=p1,release_store=live['store'])
    with e1.transaction() as db:db.execute('DELETE FROM workbench_receipts WHERE receipt_key=?',('common-ready:foreign-third',))
    p2,e2=_later(p1,name='owned_final_serializer');before=list(calls)
    source.register(e2,task['task_id'],profile=p2,release_store=live['store'],expected=observed)
    assert e2.explicit_new_post_task_ids()==(task['task_id'],)
    assert native.read_images(e2.get(task['task_id']),p2)==observed['predecessors']['images'] and calls==before
    with e2.readonly_snapshot() as db:
        assert [dict(r) for r in db.execute('SELECT * FROM workbench_receipts WHERE task_id=? ORDER BY receipt_key',(task['task_id'],))]==receipts
