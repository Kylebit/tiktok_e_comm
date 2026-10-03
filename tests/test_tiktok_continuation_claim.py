from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from test_tiktok_continuation_admission import fixture
from shared_platform.product_publication_runs import ProductPublicationRunStore
from shared_platform.product_publication_reports import ProductPublicationReportStore
from shared_platform.tiktok_continuation_claim import claim_tiktok_continuation
from shared_platform.tiktok_continuation_admission import _digest


def context(tmp_path,monkeypatch):
    data,trusted=fixture(True)
    monkeypatch.setattr('shared_platform.publication_autopilot.validate_release_candidate_for_execution',lambda c,**kw:c)
    monkeypatch.setattr('shared_platform.publication_autopilot.validate_final_approval_receipt',lambda a,c,**kw:a)
    monkeypatch.setattr('shared_platform.tiktok_continuation_admission.assert_manifest_matches_approved_snapshot',lambda *a,**kw:None)
    prepared=SimpleNamespace(offer_id=data['offer_id'],revision=5,plan_id=data['plan_id'],
        snapshot_digest=data['snapshot_digest'],platform_scope=('TIKTOK',),
        target_labels_by_platform={'TIKTOK':trusted['policy']['approved_target_labels']})
    return dict(data=data,completion=True,prepared=prepared,
        execution_identity={'skill_digest':'1'*64,'git_commit':'2'*40,'code_digest':'3'*64},
        run_store=ProductPublicationRunStore(tmp_path/'runs.db'),
        report_store=ProductPublicationReportStore(tmp_path/'reports.db',reports_root=tmp_path/'reports'),
        context_reader=lambda _:deepcopy(trusted)),trusted


def test_concurrent_repeated_claim_has_one_durable_owner(tmp_path,monkeypatch):
    kwargs,_=context(tmp_path,monkeypatch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims=list(pool.map(lambda _:claim_tiktok_continuation(**kwargs),range(4)))
    assert sum(row.created for row in claims)==1
    assert len({row.run_id for row in claims})==1
    assert kwargs['run_store'].get_run_by_id(run_id=claims[0].run_id)['target_count']==10


def test_existing_claim_entry_reuses_tiktok_contract_without_mixing_shopee(tmp_path,monkeypatch):
    from shared_platform.product_publication_runner import claim_product_publication_request
    kwargs,_=context(tmp_path,monkeypatch)
    ctx={key:kwargs[key] for key in ('data','completion','context_reader')}
    base={key:kwargs[key] for key in ('prepared','execution_identity','run_store','report_store')}
    base.update(platform='TIKTOK',release_store=None,tiktok_continuation_context=ctx)
    assert claim_product_publication_request(**base).created
    assert not claim_product_publication_request(**base).created
    with pytest.raises(ValueError,match='cannot mix'):
        claim_product_publication_request(**base,recovery_manifest_digest='sha256:'+'a'*64)


def test_reportless_failed_submission_cannot_get_new_run(tmp_path,monkeypatch):
    kwargs,trusted=context(tmp_path,monkeypatch);store=kwargs['run_store']
    first=claim_tiktok_continuation(**kwargs)
    store.mark_running(run_id=first.run_id)
    store.mark_failed(run_id=first.run_id,failure_code='RUNNER_INFRASTRUCTURE_FAILED')
    assert not claim_tiktok_continuation(**kwargs).created
    # A new policy identity cannot bypass the persisted failed attempt.
    trusted['policy']['retired_target_labels']=['tiktok:LH_PH']
    trusted['policy']['policy_digest']=_digest({k:v for k,v in trusted['policy'].items() if k!='policy_digest'})
    with pytest.raises(ValueError,match='overlapping'):claim_tiktok_continuation(**kwargs)


def test_policy_change_between_preflight_and_transaction_rejects_claim(tmp_path,monkeypatch):
    kwargs,trusted=context(tmp_path,monkeypatch);calls=[]
    def reader(_):
        calls.append(True);copy=deepcopy(trusted)
        if len(calls)>1:
            copy['policy']['retired_target_labels']=['tiktok:LH_PH']
            copy['policy']['policy_digest']=_digest({k:v for k,v in copy['policy'].items() if k!='policy_digest'})
        return copy
    kwargs['context_reader']=reader
    with pytest.raises(ValueError,match='changed before claim'):claim_tiktok_continuation(**kwargs)
    assert len(calls)==2


def test_full_scope_required_and_retired_scope_never_claimed(tmp_path,monkeypatch):
    kwargs,trusted=context(tmp_path,monkeypatch)
    kwargs['prepared'].target_labels_by_platform={'TIKTOK':['tiktok:LH_MY']}
    with pytest.raises(ValueError,match='full approved'):claim_tiktok_continuation(**kwargs)
    kwargs['prepared'].target_labels_by_platform={'TIKTOK':trusted['policy']['approved_target_labels']}
    trusted['policy']['retired_target_labels']=[kwargs['data']['continuation_manifest']['completion_target_labels'][0]]
    trusted['policy']['policy_digest']=_digest({k:v for k,v in trusted['policy'].items() if k!='policy_digest'})
    with pytest.raises(ValueError,match='retired'):claim_tiktok_continuation(**kwargs)


@pytest.mark.parametrize('state',['QUEUED','RUNNING','FAILED'])
def test_unknown_or_inflight_attempt_blocks_other_policy(tmp_path,monkeypatch,state):
    kwargs,trusted=context(tmp_path,monkeypatch);first=claim_tiktok_continuation(**kwargs)
    if state=='RUNNING':kwargs['run_store'].mark_running(run_id=first.run_id)
    if state=='FAILED':kwargs['run_store'].mark_failed(run_id=first.run_id,failure_code='RUNNER_INFRASTRUCTURE_FAILED')
    trusted['policy']['retired_target_labels']=['tiktok:LH_PH']
    trusted['policy']['policy_digest']=_digest({k:v for k,v in trusted['policy'].items() if k!='policy_digest'})
    with pytest.raises(ValueError):claim_tiktok_continuation(**kwargs)


def test_unconfigured_retry_proof_cannot_claim(tmp_path,monkeypatch):
    kwargs,_=context(tmp_path,monkeypatch)
    kwargs['data']['continuation_manifest']['zero_write_retry']={'source_run_id':'unknown'}
    with pytest.raises(ValueError):claim_tiktok_continuation(**kwargs)


@pytest.mark.parametrize('status',['FAILED','UNKNOWN','PROCESSING'])
def test_exact_source_run_must_prove_no_submission(tmp_path,monkeypatch,status):
    from test_tiktok_continuation_report_compatibility import _zero_report
    kwargs,_=context(tmp_path,monkeypatch)
    data,trusted=fixture(False);manifest=data['continuation_manifest']
    report=_zero_report(manifest,run_id=manifest['source_run_id'])
    selected=set(manifest['recovery_target_labels'])
    for row in report['targets']:
        if row['target_label'] in selected:
            row['status']=status
            row['evidence']={'target_label':row['target_label'],'status':status,'stage':'PREPARATION',
                'provider_code':'PREPARATION_FAILED','provider_reason':'synthetic preparation failure',
                'outcome_unknown':status=='UNKNOWN','request_attempted':False,'external_write_count':0}
    if status=='FAILED':
        kwargs['report_store'].store_report(report)
        report=kwargs['report_store'].get_report_by_run(run_id=manifest['source_run_id'])
    manifest['source_evidence_digest']=_digest(report)
    manifest['manifest_digest']=_digest({k:v for k,v in manifest.items() if k!='manifest_digest'})
    trusted['policy']['manifest_digest']=manifest['manifest_digest']
    trusted['policy']['policy_digest']=_digest({k:v for k,v in trusted['policy'].items() if k!='policy_digest'})
    kwargs.update(data=data,completion=False,context_reader=lambda _:deepcopy(trusted))
    if status!='FAILED':kwargs['report_store']=SimpleNamespace(get_report_by_run=lambda **kw:deepcopy(report))
    store=kwargs['run_store']
    store.create_run(run_id=manifest['source_run_id'],offer_id=data['offer_id'],revision=5,
                     plan_id=data['plan_id'],snapshot_digest=data['snapshot_digest'],
                     platform_scope=('TIKTOK',),target_count=10,execution_identity=kwargs['execution_identity'])
    store.mark_running(run_id=manifest['source_run_id'])
    store.mark_completed(run_id=manifest['source_run_id'],final_report_id='publication-report:'+manifest['source_run_id'])
    if status=='FAILED':assert claim_tiktok_continuation(**kwargs).created
    else:
        with pytest.raises(ValueError,match='not proved zero-write'):claim_tiktok_continuation(**kwargs)


@pytest.mark.parametrize('tamper',[None,'candidate','approval'])
def test_claim_uses_actual_complete_approval_documents(tmp_path,monkeypatch,tamper):
    from tiktok_full_authority_fixture import full_authority
    value,candidate,approval=full_authority()
    data,trusted=fixture(True);manifest=data['continuation_manifest']
    lineage=manifest['lineage']
    lineage.update(offer_id=value['offer_id'],revision=str(value['product_revision']),
                   plan_id=value['plan_id'],snapshot_digest=value['snapshot_digest'],
                   candidate_digest='sha256:'+candidate['candidate_digest'],approval_digest='sha256:'+approval['approval_digest'])
    manifest['manifest_digest']=_digest({k:v for k,v in manifest.items() if k!='manifest_digest'})
    data.update({k:lineage[k] for k in ('offer_id','plan_id','snapshot_digest','candidate_digest')})
    trusted.update(snapshot=value,candidate=candidate,approval=approval)
    trusted['policy']['lineage']={k:lineage[k] for k in ('offer_id','revision','plan_id','snapshot_digest','candidate_digest','approval_digest')}
    trusted['policy']['manifest_digest']=manifest['manifest_digest']
    trusted['policy']['policy_digest']=_digest({k:v for k,v in trusted['policy'].items() if k!='policy_digest'})
    # The fixture's transport facts are tested separately; do not mock either
    # complete candidate or final approval validator in this authority test.
    monkeypatch.setattr('shared_platform.tiktok_continuation_admission.assert_manifest_matches_approved_snapshot',lambda *a,**kw:None)
    if tamper:trusted[tamper]['plan_id']='different-plan-with-original-digest'
    prepared=SimpleNamespace(offer_id=value['offer_id'],revision=value['product_revision'],plan_id=value['plan_id'],
        snapshot_digest=value['snapshot_digest'],platform_scope=('TIKTOK',),target_labels_by_platform={'TIKTOK':trusted['policy']['approved_target_labels']})
    kwargs=dict(data=data,completion=True,prepared=prepared,context_reader=lambda _:deepcopy(trusted),
        run_store=ProductPublicationRunStore(tmp_path/'runs.db'),report_store=ProductPublicationReportStore(tmp_path/'reports.db',reports_root=tmp_path/'reports'),
        execution_identity={'skill_digest':'1'*64,'git_commit':'2'*40,'code_digest':'3'*64})
    if tamper:
        with pytest.raises(ValueError):claim_tiktok_continuation(**kwargs)
    else:assert claim_tiktok_continuation(**kwargs).created
