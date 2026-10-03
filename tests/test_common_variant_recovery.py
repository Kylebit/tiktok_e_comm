import hashlib
import pytest
from shared_platform import common_variant_recovery as recovery
from shared_platform import common_config_recovery as config_recovery
from test_common_config_recovery import failed
from test_operations_publication_common import claim
from shared_platform import operations_publication_common as common
from modules.products import server

def failed_second(tmp_path,monkeypatch):
    args,transport,request,profile=failed(tmp_path,monkeypatch)
    config_recovery.reconcile_historical_config_failure(**args)
    from modules.miaoshou import client
    def variant(*a,**kw):raise KeyError('th3655-58*22cm*4pcs')
    monkeypatch.setattr(client,'post_open',variant)
    common.run(args['engine'],args['engine'].get(args['task_id']),claim(args['engine'],args['task_id']),profile,server_module=server)
    monkeypatch.setattr(recovery,'VERSION','fixture')
    monkeypatch.setattr(recovery,'SOURCES',config_recovery.SOURCES)
    monkeypatch.setattr(recovery,'_verify_local_replay',lambda *a: {'detail_calls':1,'edit_calls':0})
    args.update(detail_path=tmp_path/'detail.json',detail_sha256='fixture')
    return args

def test_second_attempt_recovery_keeps_both_failure_events(tmp_path,monkeypatch):
    args=failed_second(tmp_path,monkeypatch)
    proof=recovery.reconcile_historical_variant_failure(**args)
    assert proof['attempt']==2 and proof['request_attempted'] is False and proof['readonly_request_count']==1
    assert recovery.reconcile_historical_variant_failure(**args)==proof
    target=args['store'].get_run(args['run_id'])['targets'][0]
    assert target['attempts']==2 and target['status']=='FAILED' and len(target['failure_events'])==2
    assert target['readback'] is None
    assert args['store'].common_reconciliation_reference(args['engine'].get(args['task_id'])['scope']['offer_id']) is None

@pytest.mark.parametrize('damage',['source','version','attempt','error','external_id','replay'])
def test_variant_recovery_rejects_unproved_history(tmp_path,monkeypatch,damage):
    args=failed_second(tmp_path,monkeypatch)
    if damage=='source':(args['source_root']/'client.txt').write_text('changed')
    elif damage=='version':monkeypatch.setattr(recovery,'VERSION','wrong')
    elif damage=='replay':monkeypatch.setattr(recovery,'_verify_local_replay',lambda *a: (_ for _ in ()).throw(ValueError('no replay')))
    else:
        field={'attempt':'attempts','error':'error','external_id':'external_id'}[damage]
        value={'attempt':3,'error':'network timeout','external_id':'123'}[damage]
        with args['store']._transaction() as db:db.execute(f'UPDATE release_target_runs SET {field}=? WHERE run_id=?',(value,args['run_id']))
    with pytest.raises(ValueError):recovery.reconcile_historical_variant_failure(**args)
    assert args['engine'].get(args['task_id'])['execution_state']=='reconciliation_required'
