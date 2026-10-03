from types import SimpleNamespace
import pytest
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform import operations_publication as publication


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine=WorkbenchEngine(tmp_path/'tasks.db',{'code_version':'fixture'})
    engine.register_executor('worker',['publication'],engine.release)
    task=engine.create({'template':'publication','source_key':'fixture','scope':{'offer_id':'123','shops':['tiktok:LH_MY']}})
    task_id=task['task_id'];token=engine.claim(task_id,'worker')['lease_token']
    identity={'snapshot_digest':'r1','offer_id':'123','targets':['tiktok:LH_MY']}
    engine.complete_step(task_id,token,expected_step='facts',checkpoint={'native_r1':identity})
    monkeypatch.setattr(publication.native,'read_frozen',lambda *_:identity)
    def missing(*_):raise FileNotFoundError('missing image documents')
    monkeypatch.setattr(publication.native,'read_images',missing)
    return engine,task_id,token,SimpleNamespace(root=tmp_path,data_root=tmp_path,environment='stable'),identity


def test_new_text_input_resumes_exactly_once_without_becoming_image_approval(env):
    engine,task_id,token,profile,identity=env
    calls=[]
    class Bridge:
        def execute_images(self,task,output,notes):
            calls.append(notes)
            return {'status':'prepared','result':{'missing_inputs':['补充图片资料'],'evidence_paths':[]}}
    run,observe=publication.bindings(Bridge())
    run(engine,engine.get(task_id),token,profile)
    waiting=engine.get(task_id)
    assert waiting['required_action']['kind']=='input'
    assert not observe(engine,waiting,profile)
    engine.user_action(task_id,'provide-input',{'action_id':waiting['required_action']['action_id'],'note':'参考文件已补充'})
    token=engine.claim(task_id,'worker')['lease_token']
    run(engine,engine.get(task_id),token,profile)
    assert calls==[[],['参考文件已补充']]
    waiting=engine.get(task_id)
    assert waiting['current_step']=='images' and waiting['execution_state']=='waiting_user'
    assert not observe(engine,waiting,profile)
    assert calls==[[],['参考文件已补充']]


def test_real_image_receipt_can_resolve_bound_input_without_text_approval(env,monkeypatch):
    engine,task_id,token,profile,identity=env
    class Bridge:
        def execute_images(self,*_):return {'status':'prepared','result':{'missing_inputs':['请查看图片'],'evidence_paths':[]}}
    run,observe=publication.bindings(Bridge())
    run(engine,engine.get(task_id),token,profile)
    monkeypatch.setattr(publication.native,'read_images',lambda *_:{'native_r1':identity,'native_r2':{'real':'verified'}})
    assert observe(engine,engine.get(task_id),profile)
    token=engine.claim(task_id,'worker')['lease_token']
    run(engine,engine.get(task_id),token,profile)
    assert engine.get(task_id)['current_step']=='release'


def test_unknown_image_attempt_never_restarts_after_extra_note(env):
    engine,task_id,token,profile,identity=env
    engine.record_checkpoint(task_id,token,{'image_attempt':{'state':'unknown','inputs':'old','number':1}})
    with engine.transaction() as conn:
        engine._event(conn,task_id,'input_provided',{'note':'new data cannot resolve unknown charge'})
    class Bridge:
        def execute_images(self,*_):pytest.fail('unknown paid attempt must not restart')
    run,_=publication.bindings(Bridge())
    run(engine,engine.get(task_id),token,profile)
    assert engine.get(task_id)['execution_state']=='reconciliation_required'


def test_unbound_input_cannot_accept_generic_domain_approval(env):
    engine,task_id,token,profile,identity=env
    engine.wait_for_user(task_id,token,kind='input',label='input',reason='missing')
    with pytest.raises(ValueError):
        engine.accept_domain_receipt(task_id,{'receipt_id':'fake'},lambda *_:True)


def test_common_not_a_marketplace_and_completed_stage_identity_is_fixed(env,monkeypatch):
    engine,task_id,token,profile,identity=env
    native_identity={**identity,'targets':['miaoshou:COMMON','tiktok:LH_MY']}
    images={'native_r1':native_identity,'native_r2':{'digest':'images-original'}}
    monkeypatch.setattr(publication.native,'read_images',lambda *_:images)
    seen=[]
    monkeypatch.setattr(publication.registered,'_read_release',lambda *_:{})
    receipt={'plan_id':'original','approval_id':'approval-original','candidate_digest':'candidate-original'}
    def release(_release,identity,**kwargs):
        seen.append(identity['targets'])
        return receipt
    monkeypatch.setattr(publication.registered,'_release_receipt',release)
    task={'current_step':'readback','steps':[{'key':'images','checkpoint':{'native_r2':images['native_r2']}},
        {'key':'release','checkpoint':{'release_receipt':dict(receipt)}}]}
    assert publication._native_receipt(task,profile)['native_r1']['targets']==['miaoshou:COMMON','tiktok:LH_MY']
    assert seen==[['tiktok:LH_MY']]
    receipt['candidate_digest']='different'
    with pytest.raises(ValueError,match='final release identity'):
        publication._native_receipt(task,profile)
    images['native_r2']={'digest':'changed'}
    with pytest.raises(ValueError,match='image identity'):
        publication._native_receipt(task,profile)
