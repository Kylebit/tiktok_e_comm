"""Fresh exact-ID startup worker; real parent flow and closed official reads."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from modules.sourcing import new_product_workbench as workbench
from shared_platform import publication_rounds, release_control
from shared_platform.native_task_preparation import ExplicitNewTaskWorker, install_explicit_new_task_preparation
from shared_platform.operations_runtime import RuntimeProfile
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.worker_category_readonly_cli import ReadonlyCodexResult
from test_round1_workspace_freeze import live
from test_round1_auto_freeze import public_settings, complete_source
from test_worker_category_parent_flow import _selection
from test_native_parent_facts import _child, _events, _execute_direct
from owned_native_parent_profile import owned_profile


def _fresh(live, monkeypatch):
    complete_source(live)
    directory=publication_rounds.report_dir(live['offer'])
    packet=json.loads((directory/'first-review.json').read_text(encoding='utf-8'))
    images=packet.pop('image_execution_plan')
    (directory/'first-review.json').write_text(json.dumps(packet),encoding='utf-8')
    profile=owned_profile(live['root'])
    engine=WorkbenchEngine(profile.data_root/'tasks.db',{'code_version':profile.version,
                         'environment':profile.environment,'manifest_digest':profile.manifest_digest})
    runtime=SimpleNamespace(engine=engine,profile=profile)
    with monkeypatch.context() as startup:
        startup.setattr(ExplicitNewTaskWorker,'start',lambda self:None)
        worker=install_explicit_new_task_preparation(runtime)
    task=worker.create({'template':'publication','source_key':'fresh-category-parent',
        'scope':{'offer_id':live['offer'], 'shops':release_control.build_release_dashboard(
            offer_id=live['offer'])['publication_scope']['selected_labels']}})
    engine.register_executor(worker.worker_id,['publication'],engine.release,ttl=300)
    token=engine.claim(task['task_id'],worker.worker_id,ttl=300)['lease_token']
    monkeypatch.setenv('ORBIT_OPERATIONS_AGENT_EXECUTABLE',sys.executable)
    with engine.transaction() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='workbench_category_intents'").fetchone()
    assert live['fake'].calls==[]
    return engine,profile,worker,token,images


def _children(monkeypatch, images, damage=None):
    from shared_platform import worker_category_readonly_cli as cli
    facts=_child(monkeypatch,images)
    facts_run=cli.run_readonly_jsonl
    choices=[]
    def run(argv,prompt,**kwargs):
        schema=Path(argv[argv.index('--output-schema')+1])
        if schema.name!='choice.schema.json':return facts_run(argv,prompt,**kwargs)
        data=json.loads((schema.parent/'category-input.json').read_text(encoding='utf-8'))
        answer={'selection':json.dumps(_selection(data['projection'])),'missing_inputs':[]}
        choices.append((argv,prompt,kwargs,data))
        if damage:
            result=damage(answer,schema,data)
            if isinstance(result,ReadonlyCodexResult):return result
            if result is not None:answer=result
        return ReadonlyCodexResult(0,_events(answer))
    monkeypatch.setattr(cli,'run_readonly_jsonl',run)
    return choices,facts


def test_fresh_default_worker_produces_options_choice_capture_then_original_r1_freeze(live,monkeypatch):
    from shared_platform import workbench_publication_native as native
    engine,profile,worker,token,images=_fresh(live,monkeypatch)
    choices,facts=_children(monkeypatch,images)
    try:
        task=engine.get(next(iter(worker._task_ids)))
        worker.adapters['publication'](engine,task,token,profile)
        current=engine.get(task['task_id'])
        assert current['current_step']=='images' and current['steps'][0]['state']=='completed'
        assert native.read_frozen(current,profile)==current['steps'][0]['checkpoint']['native_r1']
        assert not current['required_action'] and len(choices)==len(facts)==1
        assert len(live['fake'].calls)==7
        for argv,prompt,kwargs,_ in choices+facts:
            assert argv[argv.index('--sandbox')+1]=='read-only' and token not in prompt
            assert kwargs['cwd']==profile.root and '--output-last-message' not in argv
        with engine.transaction() as db:
            intents=db.execute('SELECT action,status FROM workbench_category_intents ORDER BY action').fetchall()
            assert [tuple(row) for row in intents]==[('capture','SUCCEEDED'),('options','SUCCEEDED')]
            assert db.execute('SELECT COUNT(*) FROM workbench_category_agent_attempts').fetchone()[0]==2
        checkpoints=[event['detail']['checkpoint'] for event in engine.store.events(task['task_id'])
                     if event['event_type']=='checkpoint_saved']
        assert any(value.get('facts_attempt',{}).get('state')=='returned' for value in checkpoints)
    finally:worker.close()


def test_choice_unknown_recovery_never_launches_second_child_or_capture(live,monkeypatch):
    engine,profile,worker,token,images=_fresh(live,monkeypatch)
    choices,facts=_children(monkeypatch,images,lambda *_:ReadonlyCodexResult(None,'',timed_out=True))
    try:
        assert _execute_direct(engine,profile,worker,token)['status']=='unknown'
        assert _execute_direct(engine,profile,worker,token)['status']=='unknown'
        assert len(choices)==1 and not facts and len(live['fake'].calls)==5
        task=engine.get(next(iter(worker._task_ids)))
        worker.adapters['publication'](engine,task,token,profile)
        current=engine.get(task['task_id'])
        assert current['execution_state']=='reconciliation_required' and not current['required_action']
        assert current['checkpoint']['facts_attempt']['state']=='unknown'
        assert len(choices)==1 and not facts and len(live['fake'].calls)==5
    finally:worker.close()


@pytest.mark.parametrize('damage',['category','attribute','extra-path','duplicate'])
def test_invalid_child_choice_cannot_issue_capture_or_parent_fact_sidecars(live,monkeypatch,damage):
    engine,profile,worker,token,images=_fresh(live,monkeypatch)
    def alter(answer,*_):
        selection=json.loads(answer['selection'])
        if damage=='category':selection['selected_category_identity']='sha256:'+'0'*64
        elif damage=='attribute':selection['attribute_selections'][0]['attribute_identity_digest']='sha256:'+'0'*64
        elif damage=='extra-path':selection['output_path']='outside.json'
        else:
            answer['selection']=answer['selection'][:-1]+',"options_digest":"different"}'
            return
        answer['selection']=json.dumps(selection)
    choices,facts=_children(monkeypatch,images,alter)
    try:
        assert _execute_direct(engine,profile,worker,token)['status']=='unknown'
        assert len(choices)==1 and not facts and len(live['fake'].calls)==5
        with engine.transaction() as db:
            assert not db.execute("SELECT 1 FROM workbench_category_intents WHERE action='capture'").fetchone()
        assert not (publication_rounds.report_dir(live['offer'])/'first-review-image-plan.json').exists()
    finally:worker.close()


def test_live_product_context_drift_after_choice_blocks_capture_before_get(live,monkeypatch):
    engine,profile,worker,token,images=_fresh(live,monkeypatch)
    def drift(*_):
        state=workbench.load_state(live['offer']);state['review']['title']='Changed actual context'
        workbench.save_state(live['offer'],state)
    choices,facts=_children(monkeypatch,images,drift)
    try:
        assert _execute_direct(engine,profile,worker,token)['status']=='unknown'
        assert len(choices)==1 and not facts and len(live['fake'].calls)==5
        assert not (publication_rounds.report_dir(live['offer'])/'round1-approved-snapshot.json').exists()
    finally:worker.close()


def test_multilink_choice_input_is_rejected_before_capture_and_outside_stays_original(live,monkeypatch):
    import os
    engine,profile,worker,token,images=_fresh(live,monkeypatch)
    outside=live['root']/'outside-category-bytes';outside.write_bytes(b'original')
    def replace(answer,schema,data):
        path=schema.parent/'category-input.json';path.unlink();os.link(outside,path)
    choices,facts=_children(monkeypatch,images,replace)
    try:
        assert _execute_direct(engine,profile,worker,token)['status']=='unknown'
        assert len(choices)==1 and not facts and len(live['fake'].calls)==5
        assert outside.read_bytes()==b'original' and outside.stat().st_nlink==2
    finally:worker.close()
