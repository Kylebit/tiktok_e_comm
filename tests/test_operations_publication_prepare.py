from types import SimpleNamespace
import json
from shared_platform import publication_rounds
import pytest
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform import operations_publication_prepare as prepare
from shared_platform import operations_runtime


@pytest.fixture
def env(tmp_path, monkeypatch):
    release={'code_version':'fixture','environment':'preview','manifest_digest':'fixture-manifest'}
    engine=WorkbenchEngine(tmp_path/'tasks.db',release)
    engine.register_executor('worker',['publication'],engine.release)
    task=engine.create({'template':'publication','source_key':'fixture','scope':{'offer_id':'123','shops':['tiktok:LH_MY']}})
    tid=task['task_id'];token=engine.claim(tid,'worker')['lease_token']
    state={'packet':{'status':'DECISION_REQUIRED','blockers':['AGENT_GENERATION_REQUIRED']},'ready':False}
    # Original workflow tests mock adoption, but the new image gate consumes
    # a real owned input through the unchanged original image-plan validator.
    monkeypatch.setattr(publication_rounds,'REPORTS_ROOT',tmp_path/'reports/product-preparation')
    directory=publication_rounds.report_dir('123');directory.mkdir(parents=True)
    plan={'schema_version':'first-review-image-plan/v1','status':'PROPOSED',
          'source_actions':[],'generated_assets':[],'brand_plans':[],
          'summary':{'translation_positions':[],'localized_output_count':0,
                     'net_new_output_count':0,'paid_generation_required':False}}
    (directory/'first-review.json').write_text(json.dumps({'offer_id':'123',
        'product_center_revision':1,'image_execution_plan':plan}),encoding='utf-8')
    monkeypatch.setattr(prepare.native,'_server',lambda *_:object())
    monkeypatch.setattr(prepare.native,'read_frozen',lambda *_:None)
    monkeypatch.setattr(prepare,'_current',lambda *_:({'review_path':'fixture'},state['packet']))
    def review(*_):
        packet={'status':'FIRST_REVIEW_READY','blockers':[],'offer_id':'123','target_selection':{'requested':['tiktok:LH_MY']}}
        return {'prepared_reference':'real-ref','round1_prepared_review':{'prepared_reference':'real-ref','review_digest':'sha256:'+'a'*64,'status':'PREPARED' if state['ready'] else 'BLOCKED','packet':packet}}
    monkeypatch.setattr(prepare.native,'_prepare_workspace_review',review)
    return engine,tid,token,SimpleNamespace(root=tmp_path,data_root=tmp_path,
        version='fixture',environment='preview',manifest_digest='fixture-manifest'),state


def test_skeleton_is_technical_work_not_user_review(env):
    engine,tid,token,profile,state=env
    prepare.run(engine,engine.get(tid),token,profile)
    task=engine.get(tid)
    assert task['execution_state']=='failed' and not task['required_action']


def test_only_actual_complete_domain_packet_can_reach_technical_boundary(env):
    engine,tid,token,profile,state=env
    class Bridge:
        def execute_facts(self,*_,**__):
            state.update(packet={'status':'DECISION_REQUIRED','blockers':['CATEGORY_RECEIPT_UNAVAILABLE']},ready=True)
            return {'status':'prepared','result':{}}
    prepare.run(engine,engine.get(tid),token,profile,Bridge())
    task=engine.get(tid)
    assert task['current_step']=='facts' and task['execution_state']=='reconciliation_required'
    assert not task['required_action']
    assert task['checkpoint']['native_preparation']['prepared_reference']=='real-ref'
    assert not any(s['state']=='completed' for s in task['steps'])


def test_agent_prepared_self_assertion_does_not_complete_review(env):
    engine,tid,token,profile,state=env
    class Bridge:
        def execute_facts(self,*_,**__):return {'status':'prepared','result':{'approved':True,'status':'FIRST_REVIEW_READY'}}
    prepare.run(engine,engine.get(tid),token,profile,Bridge())
    assert engine.get(tid)['execution_state']=='failed'
    assert not engine.get(tid)['required_action']


def test_read_only_facts_observation_waits_for_parent_persistence(env):
    engine,tid,token,profile,state=env
    class Bridge:
        def execute_facts(self,*_,**__):
            return {'status':'observed','result':{'summary':'read only',
                'missing_inputs':[],'evidence_paths':[]}}
    prepare.run(engine,engine.get(tid),token,profile,Bridge())
    task=engine.get(tid)
    assert task['execution_state']=='reconciliation_required'
    assert task['current_step']=='facts' and not task['required_action']
    assert task['checkpoint']['facts_attempt']['result']['status']=='observed'


def test_missing_text_input_starts_exactly_one_new_preparation(env):
    engine,tid,token,profile,state=env;calls=[]
    class Bridge:
        def execute_facts(self,task,output,notes,*,lease_token):
            calls.append(notes)
            return {'status':'prepared','result':{'missing_inputs':['商品材料']}}
    prepare.run(engine,engine.get(tid),token,profile,Bridge())
    task=engine.get(tid)
    assert task['required_action']['kind']=='input'
    engine.user_action(tid,'provide-input',{'action_id':task['required_action']['action_id'],'note':'材料是木质'})
    token=engine.claim(tid,'worker')['lease_token']
    prepare.run(engine,engine.get(tid),token,profile,Bridge())
    assert calls==[[],['材料是木质']]
    assert engine.get(tid)['current_step']=='facts'


def test_unknown_attempt_never_relaunches(env):
    engine,tid,token,profile,state=env
    engine.record_checkpoint(tid,token,{'facts_attempt':{'state':'started','number':1}})
    class Bridge:
        def execute_facts(self,*_,**__):pytest.fail('unknown session must not be relaunched')
    prepare.run(engine,engine.get(tid),token,profile,Bridge())
    assert engine.get(tid)['execution_state']=='reconciliation_required'


def test_different_prepared_reference_cannot_complete_technical_adoption(env,monkeypatch):
    engine,tid,token,profile,state=env
    state.update(packet={'status':'FIRST_REVIEW_READY','blockers':[]},ready=True)
    monkeypatch.setattr(operations_runtime,'_R1_FILESYSTEM_BOUNDARY_VERIFIED',True)
    monkeypatch.setattr(prepare.native,'_server',lambda *_:object())
    from shared_platform import publication_autopilot,round1_workspace
    monkeypatch.setattr(publication_autopilot,'load_autopilot_policy',lambda:{'status':'ACTIVE',
        'review_contract':{'intermediate_human_approval_required':False}})
    monkeypatch.setattr(round1_workspace,'auto_freeze',lambda *_:{'status':'FROZEN',
        'human_approval':False,'external_write_count':0,'persisted_readback':True,
        'offer_id':'123','prepared_reference':'real-ref',
        'snapshot':{'snapshot_digest':'expected-snapshot'}})
    calls=[0]
    def frozen(*_):
        calls[0]+=1
        return None if calls[0]==1 else {'offer_id':'123','targets':['tiktok:LH_MY'],
            'snapshot_digest':'expected-snapshot','prepared_reference':'different-ref'}
    monkeypatch.setattr(prepare.native,'read_frozen',frozen)
    prepare.run(engine,engine.get(tid),token,profile)
    assert engine.get(tid)['execution_state']=='reconciliation_required'
    assert engine.get(tid)['current_step']=='facts'
    assert not engine.get(tid)['required_action']
