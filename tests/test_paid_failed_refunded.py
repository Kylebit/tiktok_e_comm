from copy import deepcopy
from datetime import datetime,timezone
import pytest
from test_publication_paid_entry import workflow,reconciliation_evidence
from shared_platform.publication_paid_requests import PaidRequestBlocked
from modules.sourcing.image_generation_checkpoint import digest


@pytest.fixture
def unknown(workflow):
    context=workflow.context()
    args=dict(purpose='translation_quality_assurance',model='fixture-qa',messages=[{'role':'user','content':'fixture'}],business={'phase':'fixture-qa'})
    def timeout():raise TimeoutError('synthetic provider wait')
    with pytest.raises(TimeoutError):context.chat(**args,call=timeout)
    _,entries=context._load();key=next(k for k,v in entries.items() if v['state']=='UNKNOWN')
    response={'task_id':123,'model':'fixture-qa','state':'failed','is_final':True,'refunded':True,
        'cost':0,'refunded_amount':'0.25','completed_at':datetime.now(timezone.utc).isoformat(),
        'result_url':'','result_urls':[],'result_type':''}
    evidence=reconciliation_evidence(context,key,outcome='failed_task_refunded',task_id=123,
        provider_response=response,provider_response_sha256=digest(response))
    return context,key,args,evidence


def test_refunded_failure_is_terminal_not_success_and_keeps_budget(unknown):
    context,key,args,evidence=unknown;before=context.summary()['occupied'];old=context.path.read_bytes()
    result=context.reconcile_request(evidence=evidence)
    assert result['entry']['state']=='FAILED_REFUNDED' and result['entry']['task_id']==123
    assert context.summary()['occupied']==before and context.summary()['unknown']==0
    assert context.summary()['failed_refunded_retained']==1 and context._raw(key) is None
    journal=context.path.read_bytes();assert journal.startswith(old)
    assert context.reconcile_request(evidence=evidence)==result and context.path.read_bytes()==journal
    with pytest.raises(PaidRequestBlocked,match='explicit bounded retry'):
        context.chat(**args,call=lambda:pytest.fail('must not submit'))
    # Even an explicit next attempt cannot use the refunded slot a second time.
    context.cap=before
    with pytest.raises(PaidRequestBlocked,match='BUDGET_EXHAUSTED'):
        context.chat(**args,attempt=1,call=lambda:pytest.fail('budget cannot shrink'))


@pytest.mark.parametrize('damage',['task','known_task','model','pending','not_final','not_refunded','cost','refund','nan','result','hash','stale','early','checkpoint'])
def test_incomplete_or_mismatched_provider_evidence_keeps_unknown(unknown,damage):
    context,key,args,evidence=unknown;value=deepcopy(evidence);response=value['provider_response']
    if damage=='task':response['task_id']=124
    elif damage=='known_task':
        context.record(key,'UNKNOWN',task_id=124)
        value['observed']=context.inspect_request(key);value['verified_at']=datetime.now(timezone.utc).isoformat()
    elif damage=='model':response['model']='other'
    elif damage=='pending':response['state']='processing'
    elif damage=='not_final':response['is_final']=False
    elif damage=='not_refunded':response['refunded']=False
    elif damage=='cost':response['cost']='0.25'
    elif damage=='refund':response['refunded_amount']=0
    elif damage=='nan':response['refunded_amount']='NaN'
    elif damage=='result':response['result_url']='https://fixture.invalid/completed.png'
    elif damage=='stale':value['observed']['entry']['request_digest']='b'*64
    elif damage=='early':response['completed_at']='2000-01-01T00:00:00+00:00'
    elif damage=='checkpoint':value['observed']['checkpoint']={'task_id':123}
    value['provider_response_sha256']='0'*64 if damage=='hash' else digest(response)
    before=context.path.read_bytes()
    with pytest.raises(PaidRequestBlocked):context.reconcile_request(evidence=value)
    assert context.path.read_bytes()==before and context.entry(key)['state']=='UNKNOWN'
