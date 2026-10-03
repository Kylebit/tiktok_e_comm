import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
from shared_platform.operations_runtime import RuntimeProfile, OperationsWorker, preparation_adapter
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.workbench_profit_adapter import adapter as profit_adapter, validate_reports
from shared_platform.workbench_delisting_adapter import run as delist_run, scoped_plan


def engine(tmp_path):
    return WorkbenchEngine(tmp_path / 'tasks.db', {'code_version': 'a' * 40})


def test_dispatcher_reports_registration_failure_and_recovery(tmp_path, monkeypatch):
    from shared_platform import operations_runtime

    e = engine(tmp_path)
    monkeypatch.setattr(operations_runtime, 'runtime_matches', lambda profile: True)
    register = e.register_executor
    failing = {'value': True}

    def register_or_fail(*args, **kwargs):
        if failing['value']:
            raise OSError('synthetic database failure; details must not leak')
        return register(*args, **kwargs)

    monkeypatch.setattr(e, 'register_executor', register_or_fail)
    worker = OperationsWorker(e, RuntimeProfile(tmp_path, tmp_path, 'preview', 'a' * 40),
                              {'profit': lambda *args: None}, interval=.02)
    worker.start()
    try:
        limit = time.time() + 3
        while time.time() < limit and worker.status()['state'] != 'error':
            time.sleep(.02)
        failed = worker.status()
        assert failed['state'] == 'error'
        assert failed['last_error_type'] == 'OSError'
        assert failed['last_error_at']
        assert 'synthetic database failure' not in str(failed)
        failing['value'] = False
        worker.wake()
        limit = time.time() + 3
        while time.time() < limit and worker.status()['state'] != 'polling':
            time.sleep(.02)
        recovered = worker.status()
        assert recovered['state'] == 'polling'
        assert recovered['last_success_at']
    finally:
        worker.close()


def test_dispatcher_reports_source_mismatch_without_claiming_work(tmp_path, monkeypatch):
    from shared_platform import operations_runtime

    e = engine(tmp_path)
    old = e.create({'template': 'profit', 'scope': {'month': '2026-08'}, 'source_key': 'old'})
    monkeypatch.setattr(operations_runtime, 'runtime_matches', lambda profile: False)
    worker = OperationsWorker(e, RuntimeProfile(tmp_path, tmp_path, 'preview', 'a' * 40),
                              {'profit': lambda *args: pytest.fail('adapter invoked')}, interval=.02)
    worker.start()
    try:
        limit = time.time() + 3
        while time.time() < limit and worker.status()['state'] != 'source_mismatch':
            time.sleep(.02)
        assert worker.status()['state'] == 'source_mismatch'
        assert worker.status()['last_success_at'] is None
        assert e.get(old['task_id'])['execution_state'] == 'executor_offline'
    finally:
        worker.close()


def test_real_dispatcher_parallel_and_user_input_resume(tmp_path):
    e = engine(tmp_path)
    tasks = [e.create({'template':'profit','scope':{'month':'2026-08'},'source_key':str(i)}) for i in range(2)]
    calls = []
    def adapter(e,t,token,p):
        calls.append((t['task_id'],t['current_step']))
        if t['current_step'] == 'coverage' and not any(x['event_type']=='input_provided' for x in e.store.events(t['task_id'])):
            e.wait_for_user(t['task_id'],token,kind='input',label='补充账单',reason='缺实际广告')
        else:
            e.complete_step(t['task_id'],token,expected_step=t['current_step'],checkpoint={'source':'synthetic-test'})
    worker = OperationsWorker(e,RuntimeProfile(tmp_path,tmp_path,'preview','a'*40),{'profit':adapter},interval=.02)
    worker.start()
    try:
        limit=time.time()+5
        while time.time()<limit and any(e.get(t['task_id'])['execution_state']!='waiting_user' for t in tasks): time.sleep(.02)
        assert all(e.get(t['task_id'])['execution_state']=='waiting_user' for t in tasks)
        first=e.get(tasks[0]['task_id'])
        e.user_action(first['task_id'],'provide-input',{'note':'合成账单','action_id':first['required_action']['action_id']})
        worker.wake()
        limit=time.time()+5
        while time.time()<limit and e.get(first['task_id'])['execution_state']!='completed':time.sleep(.02)
        assert e.get(first['task_id'])['execution_state']=='completed'
        assert e.get(tasks[1]['task_id'])['execution_state']=='waiting_user'
        assert len(calls)>=5
    finally:worker.close()


def test_worker_admits_new_tasks_but_requires_exact_resume_for_old_queue(tmp_path):
    e = engine(tmp_path)
    old = e.create({'template':'profit','scope':{'month':'2026-08'},'source_key':'old-queued'})
    cutoff = datetime.now(timezone.utc).isoformat()
    new = e.create({'template':'profit','scope':{'month':'2026-09'},'source_key':'new-queued'})
    calls = []

    def synthetic_adapter(engine, task, token, profile):
        calls.append(task['task_id'])
        engine.complete_step(task['task_id'], token, expected_step=task['current_step'],
                             checkpoint={'synthetic': True})

    profile = RuntimeProfile(tmp_path,tmp_path,'preview','a'*40)
    worker = OperationsWorker(e,profile,{'profit':synthetic_adapter},interval=.02,
                              admission_cutoff=cutoff)
    worker.start()
    try:
        limit=time.time()+5
        while time.time()<limit and e.get(new['task_id'])['execution_state']!='completed':time.sleep(.02)
        assert e.get(new['task_id'])['execution_state']=='completed'
        assert e.get(old['task_id'])['execution_state']=='queued'
        assert old['task_id'] not in calls
    finally:worker.close()

    resumed = OperationsWorker(e,profile,{'profit':synthetic_adapter},interval=.02,
                               admission_cutoff=datetime.now(timezone.utc).isoformat(),
                               resume_task_ids=(old['task_id'],))
    resumed.start()
    try:
        limit=time.time()+5
        while time.time()<limit and e.get(old['task_id'])['execution_state']!='completed':time.sleep(.02)
        assert e.get(old['task_id'])['execution_state']=='completed'
        assert calls.count(old['task_id']) == 4
    finally:resumed.close()


def test_missing_agent_inputs_not_step_completion_and_crash_intent(tmp_path):
    e=engine(tmp_path)
    t=e.create({'template':'profit','scope':{'month':'2026-08'},'source_key':'missing'})
    e.register_executor('w',['profit'],e.release)
    token=e.claim(t['task_id'],'w')['lease_token']
    class Bridge:
        def prepare(self,*a,**kw):return {'status':'prepared','result':{'missing_inputs':['VN广告账单'],'summary':'缺资料','evidence_paths':[]}}
    preparation_adapter(Bridge())(e,e.get(t['task_id']),token,RuntimeProfile(tmp_path,tmp_path,'preview','a'*40))
    task=e.get(t['task_id'])
    assert task['execution_state']=='waiting_user'
    assert all(s['state']!='completed' for s in task['steps'])


def report_fixture():
    return {'schema_version':'profit-report/tiktok/v1','report_id':'synthetic','platform':'tiktok','period_kind':'monthly','period':{'start':'2026-08-01','end':'2026-08-15','basis':'order_created_at'},'status':'ready','quality_issues':[],
      'source':{'raw_row_count':1,'calculated_row_count':1,'all_non_cancelled_orders_settled':True,'coverage_snapshot_id':'synthetic-coverage','settlement_observed_through':'2026-09-09'},
      'order_lines':[{'identity':{'platform':'tiktok','shop_id':'synthetic-shop','order_id':'1','order_line_id':'1','region':'MY'},'settlement_status':'settled','settlement':{'net_amount_cny':'10'},'cost':{'total_cny':'2'},'advertising':{'amount_cny':'1'},'external_costs_cny':'0','profit_cny':'7'}],
      'totals':{'settlement_cny':'10','product_cost_cny':'2','advertising_cny':'1','external_costs_cny':'0','profit_cny':'7'}}


def test_monthly_acceptance_rejects_wrong_month_unsettled_and_money(tmp_path):
    scope={'month':'2026-08','platforms':['tiktok'],'sites':['MY']}
    file=tmp_path/'report.json'
    value=report_fixture();file.write_text(json.dumps(value))
    # A report without independently bound original sources is not evidence.
    with pytest.raises(ValueError): validate_reports([file],scope)
    for mutate in [lambda x:x['period'].update(start='2026-07-01'),lambda x:x['source'].update(all_non_cancelled_orders_settled=False),lambda x:x['totals'].update(profit_cny='8')]:
        bad=copy.deepcopy(value);mutate(bad);file.write_text(json.dumps(bad))
        with pytest.raises(ValueError):validate_reports([file],scope)


def test_preview_never_calls_delisting_skill(tmp_path):
    e=engine(tmp_path);t=e.create({'template':'delisting','scope':{'skus':['0001'],'shops':['shopee:MY']},'source_key':'delist'})
    e.register_executor('w',['delisting'],e.release);token=e.claim(t['task_id'],'w')['lease_token']
    delist_run(e,e.get(t['task_id']),token,RuntimeProfile(tmp_path,tmp_path,'preview','a'*40),skill=object())
    assert e.get(t['task_id'])['execution_state']=='failed'


def test_delisting_scope_discovery_does_not_read_other_platforms(tmp_path):
    calls=[]
    row={'target_label':'shopee:MY','product_id':'1','platform':'shopee','all_product_skus':['0001'],'requested_skus':['0001'],'current_status':'NORMAL'}
    skill=SimpleNamespace(_live_shopee_rows=lambda skus,targets:calls.append(targets) or [row],_live_verify=lambda r:r,_now=lambda:'test',_digest=lambda p:'digest')
    plan=scoped_plan(skill,{'0001'},{'shopee:MY'})
    assert calls==[{'shopee:MY'}]
    assert plan['expected_targets']==['shopee:MY']
    row['all_product_skus']=['0001','0002']
    with pytest.raises(ValueError):scoped_plan(skill,{'0001'},{'shopee:MY'})
