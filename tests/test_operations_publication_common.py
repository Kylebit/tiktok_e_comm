"""Real R1/R2 fixture, ReleaseStore and COMMON adapter; transport is synthetic."""
from types import SimpleNamespace
from contextlib import contextmanager
import pytest
from modules.products import server
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.operations_publication_common import run,observe
from test_b4b_common_stage import context,CommonTransport,approve,OFFER


def setup(tmp_path,monkeypatch,*,environment='stable',timeout=False,multivariant=False,
          synthetic_technical_authority=False):
    # Domain transports below are synthetic; never discover a user's live ledger.
    monkeypatch.delenv('ORBIT_OPERATIONS_DATA_ROOT',raising=False)
    monkeypatch.setenv('ORBIT_OPERATIONS_PROFILE',str(tmp_path/'unconfigured-profile.json'))
    if multivariant:
        from test_b4b_publication_preview import multivariant_context
        documents,dashboard,store,request,transport=multivariant_context(tmp_path,monkeypatch)
    else:
        documents,dashboard,store,request=context(tmp_path,monkeypatch)
        transport=CommonTransport(monkeypatch,timeout=timeout,
            synthetic_technical_authority=synthetic_technical_authority)
    status,view=server._preview_r3_common_stage(request);assert status==200
    plan=view['common']['plan'];stage=plan['payload']['r3_stage_binding']
    engine=WorkbenchEngine(tmp_path/'tasks.db',{'code_version':'fixture','environment':environment})
    engine.register_executor('worker',['publication'],engine.release,ttl=300)
    task=engine.create({'template':'publication','source_key':'common-fixture','scope':{'offer_id':OFFER,'skus':['0952'],'shops':stage['marketplace_targets']}})
    token=engine.claim(task['task_id'],'worker')['lease_token']
    engine.complete_step(task['task_id'],token,expected_step='facts',checkpoint={'native_r1':{'snapshot_digest':stage['round1_snapshot_digest']}})
    token=engine.claim(task['task_id'],'worker')['lease_token']
    engine.complete_step(task['task_id'],token,expected_step='images',checkpoint={'native_r2':stage['r2_identity']})
    profile=SimpleNamespace(root=server.ROOT,environment=environment)
    if synthetic_technical_authority:
        # Transport/recovery mechanics need a synthetic future authority seam.
        # This does not represent a current Offer-wide budget or standing policy.
        from shared_platform import operations_publication_common as common
        monkeypatch.setattr(common,'_common_technical_admission',
                            lambda binding,plan,**context:{'status':'READY','binding':binding,
                                                  'receipt_digest':'synthetic-authority-only'})
    return engine,task['task_id'],profile,transport,request,store


def claim(engine,task):return engine.claim(task,'worker')['lease_token']


@contextmanager
def native_completed_task(tmp_path, monkeypatch):
    """Original owner task plus one real signed COMMON completion, no approvals."""
    from owned_common_review_fixtures import native_completed_marketplace
    from shared_platform import operations_publication
    with native_completed_marketplace(tmp_path, monkeypatch, task_runtime=True) as (_, _, store, view, io):
        installed = server._NATIVE_FINAL_SERVICE
        engine, profile = installed._operations.engine, installed._operations.profile
        # The actual preparation chain creates one owner. Do not manufacture a
        # copied checkpoint/plan grant for a second task.
        with engine.transaction() as db:
            rows = db.execute("SELECT task_id FROM workbench_execution WHERE template='publication'").fetchall()
        assert len(rows) == 1
        task = rows[0]['task_id']
        original = engine.get(task)
        from shared_platform.operations_publication_common import _verified_binding
        assert _verified_binding(original, view['common'], profile)['preparation_source']['owner_task_id'] == task
        assert original['execution_state'] == 'waiting_domain'
        assert observe(engine, original, profile, server_module=server) is True
        token = engine.claim(task, 'native-source-worker', ttl=300)['lease_token']
        run_task, _ = operations_publication.bindings()
        run_task(engine, engine.get(task), token, profile)
        waiting = engine.get(task)
        assert waiting['execution_state'] == 'waiting_user'
        assert waiting['required_action']['kind'] == 'review'
        assert waiting['required_action']['label'] == '审核最终发布包'
        assert io.mutations == 1
        assert view['common']['run']['approval_id'] is None
        with store._connect_readonly() as db:
            assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM native_sole_final_decisions').fetchone()[0] == 0
        yield engine, task, profile, io, {'offer_id':view['offer_id']}, store


def test_common_binding_preserves_frozen_identity_with_common_target(tmp_path,monkeypatch):
    from copy import deepcopy
    from shared_platform.operations_publication_common import _binding
    engine,task,profile,transport,request,store=setup(tmp_path,monkeypatch)
    common=deepcopy(server._preview_r3_common_stage(request)[1]['common'])
    frozen=deepcopy(common)
    current=engine.get(task)
    receipt=_binding(current,common)
    assert receipt['round1_snapshot_digest']==common['plan']['payload']['r3_stage_binding']['round1_snapshot_digest']
    assert common==frozen
    assert transport.mutations==0
    common['plan']['payload']['r3_stage_binding']['marketplace_targets'].append('miaoshou:COMMON')
    with pytest.raises(ValueError):_binding(current,common)
    common=deepcopy(frozen)
    common['plan']['payload']['r3_stage_binding']['marketplace_targets'].append(common['plan']['payload']['r3_stage_binding']['marketplace_targets'][0])
    with pytest.raises(ValueError):_binding(current,common)
    common=deepcopy(frozen)
    common['plan']['payload']['r3_stage_binding']['marketplace_targets'].append('tiktok:wrong-shop')
    with pytest.raises(ValueError):_binding(current,common)


def test_unapproved_common_does_not_create_a_second_human_review(tmp_path,monkeypatch):
    engine,task,profile,transport,request,store=setup(tmp_path,monkeypatch)
    assert run(engine,engine.get(task),claim(engine,task),profile,server_module=server) is False
    blocked=engine.get(task)
    assert blocked['execution_state']=='waiting_domain'
    assert blocked['required_action'] is None
    assert blocked['pending_observation']['kind']=='observe'
    assert blocked['pending_observation']['receipt_binding']['plan_id']==blocked['checkpoint']['common_technical_admission']['binding']['plan_id']
    assert blocked['allowed_actions']['retry'] is False
    admission=blocked['checkpoint']['common_technical_admission']
    assert admission['status']=='BLOCKED'
    assert admission['blockers']==[
        'COMMON_STANDING_POLICY_AUTHORITY_UNKNOWN',
        'COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN',
        'COMMON_PREPARATION_SOURCE_UNKNOWN']
    assert admission['final_review_available'] is False
    assert admission['execution_authority'] is False
    assert admission['external_writes_performed']==[]
    assert store.active_plan_for_product(request['offer_id']) is None
    assert transport.mutations==0


def test_legacy_approved_common_still_blocks_unknown_offer_budget(tmp_path,monkeypatch):
    engine,task,profile,transport,request,store=setup(tmp_path,monkeypatch)
    approve(request);token=claim(engine,task)
    current=engine.get(task)
    current['checkpoint']['common_technical_admission']={'status':'READY'}
    assert run(engine,current,token,profile,server_module=server) is False
    assert transport.mutations==0


def test_common_observation_cannot_promote_old_approval_or_unknown_budget(tmp_path,monkeypatch):
    engine,task,profile,transport,request,store=setup(tmp_path,monkeypatch)
    approve(request)
    assert run(engine,engine.get(task),claim(engine,task),profile,server_module=server) is False
    waiting=engine.get(task)
    assert waiting['execution_state']=='waiting_domain'
    assert observe(engine,waiting,profile,server_module=server) is False
    assert engine.get(task)['execution_state']=='waiting_domain'
    forged=dict(waiting, pending_observation=None, required_action={
        **waiting['pending_observation'], 'kind':'review'})
    assert observe(engine,forged,profile,server_module=server) is None
    assert engine.get(task)['execution_state']=='waiting_domain'
    assert transport.mutations==0
    assert server._preview_r3_common_stage(request)[1]['common']['status']=='READY_TO_SYNC'
    assert engine.get(task)['current_step']=='release'
    assert engine.get(task)['execution_state']=='waiting_domain'
    assert engine.get(task)['required_action'] is None
    assert engine.get(task)['pending_observation']['receipt_binding']==engine.get(task)['checkpoint']['common_technical_admission']['binding']
    assert 'COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN' in engine.get(task)['checkpoint']['common_technical_admission']['blockers']
    assert transport.mutations==0


def test_preview_approved_plan_cannot_write(tmp_path,monkeypatch):
    engine,task,profile,transport,request,store=setup(tmp_path,monkeypatch,environment='preview')
    approve(request)
    with pytest.raises(ValueError,match='预览'):run(engine,engine.get(task),claim(engine,task),profile,server_module=server)
    assert transport.mutations==0


def test_unknown_common_preserves_task_lock_no_reissue(tmp_path,monkeypatch):
    from owned_common_review_fixtures import native_ready_context
    # Historical approval cannot install the native writer, even if its old
    # synthetic transport is configured to lose a reply. It attempts zero EDIT.
    with monkeypatch.context() as historical_patch:
        engine, task, profile, transport, request, store = setup(
            tmp_path/'historical', historical_patch, timeout=True)
        approve(request)
        assert run(engine,engine.get(task),claim(engine,task),profile,server_module=server) is False
        assert transport.mutations == 0
        assert engine.get(task)['checkpoint']['common_technical_admission']['status'] == 'BLOCKED'
        assert engine.get(task)['checkpoint']['common_technical_admission']['execution_authority'] is False
        from shared_platform.operations_publication_common import _verified_binding
        historical_view = server._publication_stages_for_request({'offer_id':request['offer_id']})[1]
        historical_source = _verified_binding(engine.get(task), historical_view['common'], profile)['preparation_source']
        assert historical_source['status'] == 'UNKNOWN' and historical_source['execution_authority'] is False
    with native_ready_context(tmp_path, monkeypatch, fail_edit=True) as (_, store, plan, _, _, request, transport):
        status, unknown = server._prepare_miaoshou_release(request)
        assert status == 502 and unknown['state'] == 'UNKNOWN', unknown
        operations = server._NATIVE_FINAL_SERVICE._operations
        engine, profile = operations.engine, operations.profile
        assert profile.root.resolve() == server.ROOT.resolve()
        assert engine.release == {'code_version':profile.version, 'environment':profile.environment,
                                  'manifest_digest':profile.manifest_digest}
        with engine.transaction() as db:
            rows = db.execute("SELECT task_id FROM workbench_execution WHERE template='publication'").fetchall()
        assert len(rows) == 1
        task = rows[0]['task_id']
        original_run = store.get_run(unknown['native_run_id'])
        before = list(transport.calls)
        assert observe(engine,engine.get(task),profile,server_module=server) is False
        current=engine.get(task);assert current['execution_state']=='reconciliation_required';assert transport.mutations==1
        assert transport.calls == before and store.get_run(unknown['native_run_id']) == original_run
        assert original_run['approval_id'] is None and original_run['targets'][0]['attempts'] == 1
        assert engine.claim(task,'native-source-worker') is None
        other=engine.create({'template':'delisting','source_key':'other','scope':{'skus':current['scope']['skus'],'shops':current['scope']['shops']}})
        engine.register_executor('delister',['delisting'],engine.release)
        assert engine.claim(other['task_id'],'delister') is None


def test_task_r2_identity_mismatch_never_uses_other_approved_r2(tmp_path,monkeypatch):
    engine,task,profile,transport,request,store=setup(tmp_path,monkeypatch)
    current=engine.get(task);current['checkpoint']['native_r2']['round1_snapshot_digest']='other'
    with pytest.raises(ValueError,match='R1/R2'):run(engine,current,claim(engine,task),profile,server_module=server)
    assert transport.mutations==0


def test_real_final_preview_survives_persisted_pending_plan_and_restart(tmp_path,monkeypatch):
    with native_completed_task(tmp_path, monkeypatch) as (engine,task,profile,transport,request,store):
        assert engine.get(task)['execution_state']=='waiting_user'
        status,view=server._publication_stages_for_request(request);assert status==200
        assert view['marketplace']['preview']['review_manifest']
        pending=store.create_plan(view['marketplace']['plan']['payload']);assert pending['status']=='PENDING_APPROVAL'
        resumed=WorkbenchEngine(profile.data_root/'tasks.db',engine.release)
        assert resumed.get(task)['execution_state']=='waiting_user'
        assert resumed.get(task)['required_action']==engine.get(task)['required_action']
        status,restored=server._publication_stages_for_request(request);assert status==200
        assert restored['marketplace']['preview']['review_manifest']==view['marketplace']['preview']['review_manifest']
        assert transport.mutations==1
        assert store.get_plan(pending['plan_id'])['status']=='PENDING_APPROVAL'
        assert resumed.get(task)['current_step']=='release'


def test_saved_unapproved_final_plan_read_projection_keeps_complete_manifest(tmp_path,monkeypatch):
    with native_completed_task(tmp_path, monkeypatch) as (engine,task,profile,transport,request,store):
        assert engine.get(task)['execution_state']=='waiting_user'
        status,before=server._publication_stages_for_request(request);assert status==200
        original=before['marketplace']['preview'];original_plan=before['marketplace']['plan']
        store.create_plan(original_plan['payload'])
        status,after=server._publication_stages_for_request(request);assert status==200
        assert after['marketplace'].get('preview'), 'saved pending plan lost its visible final candidate'
        assert after['marketplace']['preview']['candidate_digest']==original['candidate_digest']
        assert after['marketplace']['preview']['review_manifest']==original['review_manifest']
        assert after['marketplace']['plan']['plan_id']==original_plan['plan_id']
        assert store.get_plan(original_plan['plan_id'])['status']=='PENDING_APPROVAL'
        assert transport.mutations==1
