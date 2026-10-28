from modules.products import server
from shared_platform import operations_domain_guard as guard
from shared_platform.workbench_engine import WorkbenchEngine
from test_b4b_common_stage import context,CommonTransport,approve


def setup_guard(tmp_path,monkeypatch,timeout=False):
    documents,dashboard,store,request=context(tmp_path,monkeypatch)
    transport=CommonTransport(monkeypatch,timeout=timeout)
    _,view=server._preview_r3_common_stage(request)
    plan=view['common']['plan'];approve(request)
    engine=WorkbenchEngine(tmp_path/'guard.db',{'code_version':'fixture'})
    engine.register_executor('worker',['delisting','profit'],engine.release)
    monkeypatch.setattr(guard,'engine_for',lambda *_:engine)
    payload={**request,'release_stage':'R3_COMMON','plan_id':plan['plan_id'],
             'confirmation_token':store.get_plan(plan['plan_id'])['confirmation_token'],
             'publication_targets':['miaoshou:COMMON'],'confirm_miaoshou_write':True}
    return engine,plan,transport,payload


def test_original_common_write_rejected_by_same_sku_store_delisting(tmp_path,monkeypatch):
    engine,plan,transport,payload=setup_guard(tmp_path,monkeypatch)
    shops=plan['payload']['r3_stage_binding']['marketplace_targets']
    task=engine.create({'template':'delisting','source_key':'blocking','scope':{'skus':['0952'],'shops':shops}})
    assert engine.claim(task['task_id'],'worker')
    code,result=server._prepare_miaoshou_release(payload)
    assert code>=400 and transport.mutations==0


def test_original_common_unknown_holds_same_scope_until_readback(tmp_path,monkeypatch):
    from owned_common_review_fixtures import native_ready_context
    # Use real prepared/schema/managed service facts. The old APPROVED fixture
    # is legitimately refused before EDIT; do not bypass that production gate.
    # Only the lowest signed urllib transport is a closed local fake here.
    with native_ready_context(tmp_path,monkeypatch,fail_edit=True) as (_,store,plan,_,_,request,transport):
        code,result=server._prepare_miaoshou_release(request)
        assert code>=400 and result['state']=='UNKNOWN' and transport.mutations==1, result
        engine=server._NATIVE_FINAL_SERVICE._operations.engine
        with engine.transaction() as db:
            original_id=db.execute("SELECT task_id FROM workbench_execution WHERE template='publication'").fetchone()[0]
        original=engine.get(original_id)
        shops,skus=original['scope']['shops'],original['scope']['skus']
        engine.register_executor('worker',['delisting','profit'],engine.release)
        monkeypatch.setattr(guard,'engine_for',lambda *_:engine)
        task=engine.create({'template':'delisting','source_key':'blocked','scope':{'skus':skus,'shops':shops}})
        assert engine.claim(task['task_id'],'worker') is None
        independent_sku=next(str(number).zfill(4) for number in range(1,10000) if str(number).zfill(4) not in skus)
        other=engine.create({'template':'delisting','source_key':'independent','scope':{'skus':[independent_sku],'shops':shops}})
        assert engine.claim(other['task_id'],'worker')
        # Unknown provider output cannot release the original same-scope locks.
        guard.reconcile_common(plan,{'verified':False},server.ROOT)
        assert engine.claim(task['task_id'],'worker') is None
        calls=list(transport.calls);run=store.get_run(result['native_run_id'])
        again,held=server._prepare_miaoshou_release(request)
        assert again==409 and held['error']=='COMMON_PRIOR_ATTEMPT_RECONCILIATION_REQUIRED', held
        assert transport.calls==calls and transport.mutations==1
        assert store.get_run(result['native_run_id'])==run
        assert run['approval_id'] is None and run['targets'][0]['attempts']==1


def test_legacy_common_fixture_cannot_borrow_native_write_authority(tmp_path,monkeypatch):
    _,_,transport,payload=setup_guard(tmp_path,monkeypatch,timeout=True)
    code,result=server._prepare_miaoshou_release(payload)
    assert code>=400 and transport.mutations==0
    errors=[result.get('error','')]+[row.get('error','') for row in (result.get('run') or {}).get('targets',[])]
    assert any(code in str(value) for value in errors for code in
               ('COMMON_EDIT_BOUNDARY_NOT_INSTALLED','COMMON_EDIT_PERSISTED_NATIVE_BASELINE_REQUIRED')), result


def test_common_guard_projects_common_out_of_owner_marketplace_scope(tmp_path,monkeypatch):
    engine,plan,transport,payload=setup_guard(tmp_path,monkeypatch)
    from copy import deepcopy
    plan=deepcopy(plan)
    shops=plan['payload']['r3_stage_binding']['marketplace_targets']
    shops.append('miaoshou:COMMON')
    engine.register_executor('publisher',['publication'],engine.release)
    task=engine.create({'template':'publication','source_key':'native-common','scope':{
        'offer_id':str(plan['product_id']),'skus':['0952'],'shops':shops}})
    assert engine.claim(task['task_id'],'publisher')
    # Read-only projection must identify its own task instead of conflicting with it.
    reservation=guard.begin_common(plan,server.ROOT)
    assert reservation[2]['acquired'] is True
    assert transport.mutations==0
