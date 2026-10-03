from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from test_tiktok_lineage_protocol import _completion_manifest,_completion_addendum
from test_tiktok_continuation_report_compatibility import _zero_report,_rehost_receipt,_depth2_inputs
from shared_platform.tiktok_lineage_recovery import (
    compile_tiktok_approved_first_completion_zero_write_retry,
    compile_tiktok_approved_first_completion_zero_write_retry_depth2,
)
from shared_platform.tiktok_completion_receipts import verify_completion_receipts


def write(root,offer,directory,digest,value):
    path=root/offer/directory/(digest.removeprefix('sha256:')+'.json')
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf8')
    return path


def inputs(tmp_path,depth):
    original=_completion_manifest();addendum=_completion_addendum()
    write(tmp_path,original['lineage']['offer_id'],'execution-authority-addenda',original['authority_addendum_digest'],addendum)
    if depth==0:return original,None,None
    if depth==2:
        predecessor,report,run=_depth2_inputs()
        first_report=_zero_report(original);transport=_rehost_receipt(original)
        write(tmp_path,original['lineage']['offer_id'],'execution-transport-receipts',transport['receipt_digest'],transport)
        first_run={**deepcopy(run),'run_id':first_report['run_id'],'report_id':first_report['report_id'],
                   'final_report_id':first_report['report_id'],'request_identity':{'kind':'STANDARD','authority_digest':None}}
        return compile_tiktok_approved_first_completion_zero_write_retry_depth2(predecessor,report,source_run=run),(report,first_report),(run,first_run)
    report=_zero_report(original);transport=_rehost_receipt(original)
    manifest=compile_tiktok_approved_first_completion_zero_write_retry(original,report,transport_rehost_receipt=transport)
    write(tmp_path,original['lineage']['offer_id'],'execution-transport-receipts',transport['receipt_digest'],transport)
    run={'run_id':report['run_id'],'report_id':report['report_id'],'final_report_id':report['report_id'],'state':'COMPLETED',
         'offer_id':report['offer_id'],'revision':report['revision'],'plan_id':report['plan_id'],
         'snapshot_schema_version':report['snapshot']['schema_version'],'snapshot_digest':report['snapshot']['digest'],
         'platform_scope':['TIKTOK'],'target_count':10,'execution_identity':deepcopy(report['execution_identity']),
         'request_identity':{'kind':'STANDARD','authority_digest':None}}
    return manifest,report,run


def stores(report,run):
    reports=report if isinstance(report,tuple) else (report,)
    runs=run if isinstance(run,tuple) else (run,)
    return dict(run_store=SimpleNamespace(get_run_by_id=lambda run_id:deepcopy(next((r for r in runs if r and r['run_id']==run_id),None))),
                report_store=SimpleNamespace(get_report_by_run=lambda run_id:deepcopy(next((r for r in reports if r and r['run_id']==run_id),None))))


@pytest.mark.parametrize('depth',[0,1,2])
def test_trusted_receipts_recompile_exact_completion_without_writes(tmp_path,depth):
    manifest,report,run=inputs(tmp_path,depth)
    before={p:p.read_bytes() for p in tmp_path.rglob('*.json')}
    result=verify_completion_receipts(manifest,authority_root=tmp_path,**stores(report,run))
    assert result['manifest_digest']==manifest['manifest_digest']
    assert result['source_run_id']==((run[0] if isinstance(run,tuple) else run)['run_id'] if run else None)
    assert before=={p:p.read_bytes() for p in tmp_path.rglob('*.json')}


@pytest.mark.parametrize('change',['missing','changed','unknown','processing','report'])
def test_retry_receipt_or_source_drift_rejected(tmp_path,change):
    manifest,report,run=inputs(tmp_path,1)
    if change in {'missing','changed'}:
        path=next(tmp_path.rglob('*'+manifest['authority_addendum_digest'].removeprefix('sha256:')+'.json'))
        if change=='missing':path.unlink()
        else:path.write_text('{}',encoding='utf8')
    elif change=='report':report['revision']=999
    else:run['state']=change.upper()
    with pytest.raises((ValueError,FileNotFoundError)):
        verify_completion_receipts(manifest,authority_root=tmp_path,
            run_store=SimpleNamespace(get_run_by_id=lambda **kw:run),
            report_store=SimpleNamespace(get_report_by_run=lambda **kw:report))


@pytest.mark.parametrize('missing',['first_run','first_report','transport'])
def test_depth2_needs_complete_first_predecessor(tmp_path,missing):
    manifest,reports,runs=inputs(tmp_path,2)
    if missing=='first_run':runs=runs[:1]
    elif missing=='first_report':reports=reports[:1]
    else:next(tmp_path.rglob('execution-transport-receipts/*.json')).unlink()
    with pytest.raises((ValueError,FileNotFoundError)):
        verify_completion_receipts(manifest,authority_root=tmp_path,**stores(reports,runs))


@pytest.mark.parametrize('tamper',[None,'report_revision','run_revision','report_run','run_id'])
def test_new_exact_run_and_revision_are_bound_without_historical_constants(tamper):
    from test_tiktok_lineage_protocol import _lineage,_observations,RECOVERY
    import shared_platform.tiktok_lineage_recovery as protocol
    lineage=_lineage();lineage['revision']='7';lineage['targets']=[r for r in lineage['targets'] if r['target_label'] in RECOVERY]
    addendum=_completion_addendum();addendum['revision']='7'
    addendum['authority_receipt_digest']=protocol._canonical_digest({k:v for k,v in addendum.items() if k!='authority_receipt_digest'})
    manifest=protocol.compile_tiktok_approved_first_completion(lineage,_observations(),completion_target_labels=RECOVERY,authority_addendum=addendum)
    report=_zero_report(manifest,run_id='synthetic-revision-seven-run');report['revision']=7
    run={'run_id':report['run_id'],'report_id':report['report_id'],'final_report_id':report['report_id'],'state':'COMPLETED',
         'offer_id':report['offer_id'],'revision':7,'plan_id':report['plan_id'],
         'snapshot_schema_version':report['snapshot']['schema_version'],'snapshot_digest':report['snapshot']['digest'],
         'platform_scope':['TIKTOK'],'target_count':10,'execution_identity':deepcopy(report['execution_identity']),
         'request_identity':{'kind':'STANDARD','authority_digest':None}}
    if tamper=='report_revision':report['revision']=8
    elif tamper=='run_revision':run['revision']=8
    elif tamper=='report_run':report['run_id']='another-run'
    elif tamper=='run_id':run['run_id']='another-run'
    def verify():return protocol.validate_tiktok_approved_completion_zero_write_retry_source(
        report=report,original_manifest=manifest,source_run=run,expected_source_run_id='synthetic-revision-seven-run')
    if tamper:
        with pytest.raises(ValueError):verify()
    else:
        assert verify()
        assert protocol.compile_tiktok_approved_first_completion_zero_write_retry(manifest,report,
            transport_rehost_receipt=_rehost_receipt(manifest))['lineage']['revision']=='7'
