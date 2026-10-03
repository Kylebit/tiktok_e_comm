from copy import deepcopy
import pytest
from test_tiktok_continuation_report_compatibility import _zero_report
from shared_platform.product_publication_runner import _validate_platform_result


def _result():
    report=_zero_report()
    return {'schema_version':'product-publication-platform-result/v1','platform':'TIKTOK',
            'targets':report['targets'],'dispatch_attempted':False,'readback_completed':True,
            'external_write_count':0,'requires_human_action':True,
            'continuation_evidence':report['continuation_evidence']}


def test_existing_runner_keeps_complete_continuation_result():
    result=_result();labels=tuple(row['target_label'] for row in result['targets'])
    checked=_validate_platform_result(result,platform='TIKTOK',expected_targets=labels)
    assert len(checked.targets)==10
    assert checked.continuation_evidence==result['continuation_evidence']


@pytest.mark.parametrize('drift',['digest','platform','public_status','write_count','coverage'])
def test_runner_rejects_false_continuation_projection(drift):
    result=_result();labels=tuple(row['target_label'] for row in result['targets'])
    if drift=='digest':result['continuation_evidence']['result_digest']='sha256:'+'0'*64
    elif drift=='platform':result['platform']='SHOPEE'
    elif drift=='public_status':result['targets'][0]['status']='FAILED'
    elif drift=='write_count':result['external_write_count']=1
    else:result['targets'].pop()
    with pytest.raises(ValueError):_validate_platform_result(result,platform=result['platform'],expected_targets=labels)


def test_existing_runner_persists_and_replays_full_ten_target_evidence(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from shared_platform.product_publication_runner import ProductPublicationRunner, PreparedPublicationRun
    from shared_platform.product_publication_reports import ProductPublicationReportStore
    result=_result();lineage=result['continuation_evidence']['lineage']
    snapshot={'offer_id':lineage['offer_id'],'plan_id':lineage['plan_id'],
              'snapshot_digest':lineage['snapshot_digest'],'product_revision':int(lineage['revision'])}
    labels=tuple(row['target_label'] for row in result['targets'])
    monkeypatch.setattr('shared_platform.product_publication_runner.prepare_product_publication_run',
        lambda **kw:PreparedPublicationRun(offer_id=lineage['offer_id'],
            revision=int(lineage['revision']),plan_id=lineage['plan_id'],
            snapshot_digest=lineage['snapshot_digest'],platform_scope=('TIKTOK',),
            snapshot=deepcopy(snapshot),target_labels_by_platform={'TIKTOK':labels}))
    monkeypatch.setattr('shared_platform.publication_autopilot.validate_release_candidate_for_execution',lambda c,**kw:c)
    monkeypatch.setattr('shared_platform.publication_autopilot.validate_final_approval_receipt',lambda a,c,**kw:a)
    candidate={'candidate_digest':lineage['candidate_digest'].removeprefix('sha256:'),
               'write_budget':{'TIKTOK':{'shared_maximum':0,'per_target_maximum':2}}}
    approval={'approval_digest':lineage['approval_digest'].removeprefix('sha256:')}
    store=ProductPublicationReportStore(tmp_path/'reports.db',reports_root=tmp_path/'reports')
    release=SimpleNamespace(approved_publication_snapshot=lambda **kw:deepcopy(snapshot))
    runner=ProductPublicationRunner(release_store=release,report_store=store)
    calls=[]
    def executor(request):calls.append(request.run_id);return deepcopy(result)
    kwargs=dict(run_id='synthetic-continuation-run',offer_id=lineage['offer_id'],plan_id=lineage['plan_id'],
                platform_scope=('TIKTOK',),platform_executors={'TIKTOK':executor},release_candidate=candidate,
                final_approval=approval)
    first=runner.run(**kwargs);second=runner.run(**kwargs)
    assert first.report['continuation_evidence']==result['continuation_evidence']
    assert len(first.report['targets'])==10 and second.replayed
    assert second.report['continuation_evidence']==first.report['continuation_evidence']
    assert calls==['synthetic-continuation-run']
