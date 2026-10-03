"""Persisted final approval -> real runner -> Ozon I/O -> durable budget receipt."""
from copy import deepcopy
import pytest
from shared_platform.publication_autopilot import (
    compile_release_candidate, build_final_approval_receipt, persist_release_candidate,
    persist_final_approval_receipt, load_release_candidate, load_final_approval_receipt,
)
from shared_platform.product_publication_runner import ProductPublicationRunner
from modules.ozon.approved_publication_v4 import OzonDispatchFact,OzonStockDispatchFact,build_ozon_v4_executor
from test_product_publication_runner import _SnapshotStore,_report_store
from test_b4b_release_compiler import policy,INCIDENTS
from test_b4b_ozon_partial_budget import ozon_snapshot
from test_ozon_approved_publication_v4 import _published_item


def _prepared(tmp_path,value=None):
    value=value or ozon_snapshot()
    candidate=compile_release_candidate(value,policy=policy(3),incident_registry=INCIDENTS,platform_scope=('OZON',))
    assert candidate['status']=='READY_FOR_FINAL_REVIEW',candidate['blockers']
    approval=build_final_approval_receipt(candidate,approved_by='Kyle')
    persist_release_candidate(candidate,reports_root=tmp_path)
    persist_final_approval_receipt(approval,candidate,reports_root=tmp_path)
    loaded=load_release_candidate(value['offer_id'],candidate['candidate_digest'],reports_root=tmp_path)
    return value,loaded,load_final_approval_receipt(loaded,reports_root=tmp_path)


def test_final_approved_packet_preserves_real_mutation_budget_and_replay(tmp_path):
    value,candidate,approval=_prepared(tmp_path/'preparation')
    imports=[];stocks=[]
    def dispatch(payload):
        imports.append(deepcopy(payload))
        return OzonDispatchFact(outcome='ACCEPTED',task_id='task-'+payload['offer_id'])
    executor=build_ozon_v4_executor(dispatch_variant=dispatch,
        readback_variants=lambda ids:[_published_item(row,item_id=index+1) for index,row in enumerate(imports) if row['offer_id'] in ids],
        update_stocks=lambda rows:stocks.append(rows) or OzonStockDispatchFact(outcome='ACCEPTED'),
        readback_stocks=lambda ids:[{'offer_id':identity,'stock':200 if stocks else 0} for identity in ids])
    runner=ProductPublicationRunner(release_store=_SnapshotStore(value),report_store=_report_store(tmp_path))
    kwargs=dict(run_id='approved-ozon-budget',offer_id=value['offer_id'],plan_id=value['plan_id'],
        platform_scope=('OZON',),platform_executors={'OZON':executor},release_candidate=candidate,final_approval=approval)
    receipt=runner.run(**kwargs)
    assert receipt.report['status']=='PUBLISHED'
    assert len(imports)==2 and len(stocks)==1
    budget=receipt.report['mutation_budgets'][0]
    assert budget['attempts']=={'shared':3,'per_target':{'ozon:RU':0},'total':3}
    assert [row['operation'] for row in budget['reservations']]==['import_variant','import_variant','update_stock']
    assert receipt.report['release_authorization']=={'candidate_digest':candidate['candidate_digest'],'approval_digest':approval['approval_digest']}
    assert runner.run(**kwargs).replayed is True
    assert len(imports)==2 and len(stocks)==1


@pytest.mark.parametrize('drift',['approval_missing','approval_digest','candidate_digest','snapshot','execution_snapshot','target'])
def test_final_approved_runner_rejects_drift_before_any_executor(tmp_path,drift):
    value,candidate,approval=_prepared(tmp_path/'preparation')
    if drift=='approval_missing':approval=None
    elif drift=='approval_digest':approval['approval_digest']='a'*64
    elif drift=='candidate_digest':candidate['write_budget']['OZON']['shared_maximum']=99
    elif drift=='snapshot':value['snapshot_digest']='sha256:'+'b'*64
    elif drift=='execution_snapshot':
        from shared_platform.publication_autopilot import _canonical_digest
        candidate['approved_execution_snapshot_digest']='sha256:'+'b'*64
        candidate.pop('candidate_digest')
        candidate['candidate_digest']=_canonical_digest(candidate)
        approval=build_final_approval_receipt(candidate,approved_by='Kyle')
    calls=[]
    kwargs={'target_scope':('tiktok:LH_PH',)} if drift=='target' else {}
    runner=ProductPublicationRunner(release_store=_SnapshotStore(value),report_store=_report_store(tmp_path))
    with pytest.raises((TypeError,ValueError,RuntimeError)):
        runner.run(run_id='invalid-approved-ozon',offer_id=value['offer_id'],plan_id=value['plan_id'],
            platform_scope=('OZON',),platform_executors={'OZON':lambda request:calls.append(request)},
            release_candidate=candidate,final_approval=approval,**kwargs)
    assert calls==[]


@pytest.mark.parametrize('approved',[False,True])
@pytest.mark.parametrize('include_candidate_digest',[False,True])
def test_server_loads_durable_final_packet_and_keeps_existing_claim_guard(tmp_path,monkeypatch,approved,include_candidate_digest):
    from test_b4b_ozon_partial_budget import real_ozon_store
    from modules.products import server
    from shared_platform.product_publication_runs import ProductPublicationRunStore
    store,plan,value=real_ozon_store(tmp_path)
    reports=_report_store(tmp_path)
    root=reports.reports_root.parent/'product-preparation'
    value,candidate,approval=_prepared(root,value)
    if not approved:
        from shared_platform.publication_autopilot import final_approval_receipt_path
        final_approval_receipt_path(candidate,reports_root=root).unlink()
    calls=[]
    def executor(request):
        calls.append(request)
        return {'schema_version':'product-publication-platform-result/v1','platform':'OZON',
            'targets':[{'target_label':'ozon:RU','status':'FAILED'}],'dispatch_attempted':False,
            'readback_completed':False,'external_write_count':0,'requires_human_action':True}
    monkeypatch.setattr(server,'_release_store',lambda:store)
    monkeypatch.setattr(server,'_product_publication_report_store',lambda:reports)
    monkeypatch.setattr(server,'_product_publication_run_store',lambda:ProductPublicationRunStore(tmp_path/'runs.db'))
    monkeypatch.setattr(server,'_product_publication_platform_executors',lambda:{'OZON':executor})
    monkeypatch.setattr(server,'_product_publication_execution_identity',lambda _:{'skill_digest':'1'*64,'git_commit':'2'*40,'code_digest':'3'*64})
    monkeypatch.setattr(server,'_launch_product_publication_background',lambda action:action())
    request={'offer_id':value['offer_id'],'plan_id':value['plan_id'],'candidate_digest':candidate['candidate_digest']}
    if not include_candidate_digest:
        request.pop('candidate_digest')
    status,result=server._start_product_publication(request,platform='OZON')
    assert status==(202 if approved else 409),result
    assert len(calls)==int(approved)
    if approved:
        assert calls[0].write_budget_ledger is not None
        assert reports.get_report_by_run(run_id=result['run_id'])['release_authorization']['candidate_digest']==candidate['candidate_digest']
        assert server._start_product_publication(request,platform='OZON')[1]['run_id']==result['run_id']
        assert len(calls)==1


def _zero_write_executor(calls):
    def execute(request):
        calls.append(request)
        return {'schema_version':'product-publication-platform-result/v1','platform':'OZON',
            'targets':[{'target_label':'ozon:RU','status':'FAILED'}],'dispatch_attempted':False,
            'readback_completed':False,'external_write_count':0,'requires_human_action':True}
    return execute


@pytest.mark.parametrize('authority',['legacy','pending','approved'])
def test_direct_runner_resolves_durable_authority_without_request_opt_in(tmp_path,authority):
    value=ozon_snapshot()
    reports=_report_store(tmp_path)
    root=reports.reports_root.parent/'product-preparation'
    if authority!='legacy':
        value,candidate,approval=_prepared(root,value)
        if authority=='pending':
            from shared_platform.publication_autopilot import final_approval_receipt_path
            final_approval_receipt_path(candidate,reports_root=root).unlink()
    calls=[]
    runner=ProductPublicationRunner(release_store=_SnapshotStore(value),report_store=reports)
    def run():
        return runner.run(run_id='implicit-authority',offer_id=value['offer_id'],plan_id=value['plan_id'],
            platform_scope=('OZON',),platform_executors={'OZON':_zero_write_executor(calls)})
    if authority=='pending':
        with pytest.raises(ValueError,match='requires final marketplace approval'):
            run()
        assert calls==[]
    else:
        receipt=run()
        assert len(calls)==1
        assert (calls[0].write_budget_ledger is not None)==(authority=='approved')
        assert ('release_authorization' in receipt.report)==(authority=='approved')


def test_pending_packet_does_not_revoke_old_valid_approval_but_multiple_approvals_are_explicit(tmp_path):
    from shared_platform.publication_autopilot import resolve_persisted_execution_authority
    value,candidate,approval=_prepared(tmp_path)
    newer=compile_release_candidate(value,policy=policy(4),incident_registry=INCIDENTS,platform_scope=('OZON',))
    persist_release_candidate(newer,reports_root=tmp_path)
    kwargs=dict(snapshot=value,platform_scope=('OZON',),target_labels=('ozon:RU',),reports_root=tmp_path)
    assert resolve_persisted_execution_authority(**kwargs)[0]['candidate_digest']==candidate['candidate_digest']
    persist_final_approval_receipt(build_final_approval_receipt(newer,approved_by='Kyle'),newer,reports_root=tmp_path)
    with pytest.raises(ValueError,match='multiple final approvals'):
        resolve_persisted_execution_authority(**kwargs)
    assert resolve_persisted_execution_authority(**kwargs,candidate_digest=candidate['candidate_digest'])[0]==candidate


def test_corrupt_persisted_candidate_never_falls_back_to_legacy(tmp_path):
    from shared_platform.publication_autopilot import resolve_persisted_execution_authority,release_candidate_path
    value,candidate,approval=_prepared(tmp_path)
    release_candidate_path(candidate,reports_root=tmp_path).write_text('{broken',encoding='utf-8')
    with pytest.raises(ValueError,match='unavailable'):
        resolve_persisted_execution_authority(snapshot=value,platform_scope=('OZON',),target_labels=('ozon:RU',),reports_root=tmp_path)


@pytest.mark.parametrize('kind',['candidate','approval'])
def test_persisted_authority_refuses_fixture_symlinks_outside_report_root(tmp_path,kind):
    from shared_platform.publication_autopilot import release_candidate_path,final_approval_receipt_path
    root=tmp_path/'preparation'
    value,candidate,approval=_prepared(root)
    path=(release_candidate_path if kind=='candidate' else final_approval_receipt_path)(candidate,reports_root=root)
    outside=tmp_path/'outside-fixture.json'
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ValueError,match='regular local file'):
        if kind=='candidate':
            load_release_candidate(value['offer_id'],candidate['candidate_digest'],reports_root=root)
        else:
            load_final_approval_receipt(candidate,reports_root=root)


@pytest.mark.parametrize('kind',['candidate','approval'])
@pytest.mark.parametrize('target_exists',[False,True])
def test_authority_persistence_refuses_directory_links_before_creating_or_reading(tmp_path,kind,target_exists):
    from shared_platform.publication_autopilot import release_candidate_path,final_approval_receipt_path
    value,candidate,approval=_prepared(tmp_path/'source')
    root=tmp_path/'destination'
    path=(release_candidate_path if kind=='candidate' else final_approval_receipt_path)(candidate,reports_root=root)
    path.parent.parent.mkdir(parents=True)
    outside=tmp_path/'outside'
    if target_exists:
        outside.mkdir()
    path.parent.symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='regular local file'):
        if kind=='candidate':
            persist_release_candidate(candidate,reports_root=root)
        else:
            persist_final_approval_receipt(approval,candidate,reports_root=root)
    assert not (outside/path.name).exists()
    assert outside.exists()==target_exists


def test_dangling_candidate_directory_link_is_not_legacy_authority(tmp_path):
    from shared_platform.publication_autopilot import release_candidate_path,resolve_persisted_execution_authority
    value,candidate,approval=_prepared(tmp_path/'source')
    root=tmp_path/'destination'
    directory=release_candidate_path(candidate,reports_root=root).parent
    directory.parent.mkdir(parents=True)
    directory.symlink_to(tmp_path/'missing-outside',target_is_directory=True)
    with pytest.raises(ValueError,match='regular local file'):
        resolve_persisted_execution_authority(snapshot=value,platform_scope=('OZON',),target_labels=('ozon:RU',),reports_root=root)


def test_replay_rejects_mutated_candidate_even_if_approval_digest_string_is_unchanged(tmp_path):
    value,candidate,approval=_prepared(tmp_path/'preparation')
    calls=[]
    runner=ProductPublicationRunner(release_store=_SnapshotStore(value),report_store=_report_store(tmp_path))
    kwargs=dict(run_id='replay-immutable',offer_id=value['offer_id'],plan_id=value['plan_id'],
        platform_scope=('OZON',),platform_executors={'OZON':_zero_write_executor(calls)},
        release_candidate=candidate,final_approval=approval)
    runner.run(**kwargs)
    candidate['write_budget']['OZON']['shared_maximum']=99
    with pytest.raises(ValueError,match='digest drifted'):
        runner.run(**kwargs)
    assert len(calls)==1
def test_final_review_candidate_is_stable_across_external_approval_metadata():
    from copy import deepcopy
    from shared_platform import publication_autopilot as authority

    snapshot = ozon_snapshot()
    preview = deepcopy(snapshot)
    preview.pop('snapshot_digest')
    preview.pop('approved_at')
    preview.pop('approved_by')
    preview['schema_version'] = 'publication-snapshot-preview/v1'
    preview['status'] = 'NOT_APPROVED'
    preview['preview_digest'] = 'sha256:' + authority._canonical_digest(preview)
    candidate = authority.compile_release_preview(
        preview,
        policy=policy(4),
        incident_registry=INCIDENTS,
        platform_scope=('OZON',),
    )
    frozen = deepcopy(candidate)
    assert candidate['schema_version'] == authority.CANDIDATE_SCHEMA
    assert candidate['status'] == 'READY_FOR_FINAL_REVIEW'
    assert candidate['snapshot_digest'] == authority._business_snapshot_digest(snapshot)
    # Preview-produced candidates, including the persisted f2b85a Offer 395
    # authority, predate the explicit execution-snapshot field.  Their exact
    # business digest remains a complete, fail-closed compatibility binding.
    assert 'business_snapshot_digest' not in candidate
    assert 'approved_execution_snapshot_digest' not in candidate
    assert authority.validate_release_candidate_for_execution(
        candidate,
        snapshot=snapshot,
        platform_scope=('OZON',),
        target_labels=('ozon:RU',),
    ) == candidate
    with pytest.raises(ValueError):
        authority.validate_release_candidate_for_execution(
            candidate,
            snapshot={
                'offer_id': snapshot['offer_id'],
                'plan_id': snapshot['plan_id'],
                'product_revision': snapshot['product_revision'],
                'snapshot_digest': snapshot['snapshot_digest'],
            },
            platform_scope=('OZON',),
            target_labels=('ozon:RU',),
        )
    receipt = authority.build_final_approval_receipt(candidate, approved_by='Kyle')
    assert candidate == frozen
    assert receipt['candidate_digest'] == candidate['candidate_digest']
    assert receipt['snapshot_digest'] == candidate['snapshot_digest']
    assert receipt['target_labels'] == candidate['target_labels']

    drifted = deepcopy(snapshot)
    drifted['product']['title'] += ' drift'
    with pytest.raises(ValueError):
        authority.validate_release_candidate_for_execution(
            candidate,
            snapshot=drifted,
            platform_scope=('OZON',),
            target_labels=('ozon:RU',),
        )


def test_business_bound_candidate_accepts_real_post_approval_snapshot_time():
    from copy import deepcopy
    from shared_platform import publication_autopilot as authority

    first = ozon_snapshot()
    candidate = authority.compile_release_candidate(
        first,
        policy=policy(4),
        incident_registry=INCIDENTS,
        platform_scope=('OZON',),
    )
    candidate.pop('approved_execution_snapshot_digest')
    candidate.pop('candidate_digest')
    candidate['candidate_digest'] = authority._canonical_digest(candidate)

    second = deepcopy(first)
    second['approved_at'] = '2031-02-03T04:05:06+00:00'
    second.pop('snapshot_digest')
    second['snapshot_digest'] = 'sha256:' + authority._canonical_digest(second)
    assert first['snapshot_digest'] != second['snapshot_digest']
    assert authority._business_snapshot_digest(first) == authority._business_snapshot_digest(second)

    for snapshot in (first, second):
        assert authority.validate_release_candidate_for_execution(
            candidate,
            snapshot=snapshot,
            platform_scope=('OZON',),
            target_labels=('ozon:RU',),
        ) == candidate


def test_business_bound_successor_requires_actual_execution_binding_receipt(tmp_path):
    from shared_platform import publication_autopilot as authority

    snapshot = ozon_snapshot()
    candidate = authority.compile_release_candidate(
        snapshot,
        policy=policy(4),
        incident_registry=INCIDENTS,
        platform_scope=('OZON',),
    )
    candidate.pop('approved_execution_snapshot_digest')
    candidate.pop('candidate_digest')
    candidate['candidate_digest'] = authority._canonical_digest(candidate)

    missing = authority.build_final_approval_receipt(
        candidate, approved_by='Kyle'
    )
    with pytest.raises(ValueError, match='execution snapshot binding is missing'):
        authority.validate_final_approval_receipt(
            missing, candidate, snapshot=snapshot
        )

    bound = authority.build_final_approval_receipt(
        candidate, approved_by='Kyle', execution_snapshot=snapshot
    )
    assert bound['approved_execution_snapshot_digest'] == snapshot['snapshot_digest']
    assert authority.validate_final_approval_receipt(
        bound, candidate, snapshot=snapshot
    ) == bound

    drifted = dict(snapshot)
    drifted['snapshot_digest'] = 'sha256:' + 'f' * 64
    with pytest.raises(ValueError, match='execution snapshot binding drifted'):
        authority.validate_final_approval_receipt(
            bound, candidate, snapshot=drifted
        )


def test_runner_rejects_business_bound_successor_without_execution_binding(tmp_path):
    from shared_platform import publication_autopilot as authority

    snapshot = ozon_snapshot()
    candidate = authority.compile_release_candidate(
        snapshot,
        policy=policy(4),
        incident_registry=INCIDENTS,
        platform_scope=('OZON',),
    )
    candidate.pop('approved_execution_snapshot_digest')
    candidate.pop('candidate_digest')
    candidate['candidate_digest'] = authority._canonical_digest(candidate)
    missing = authority.build_final_approval_receipt(candidate, approved_by='Kyle')
    calls = []
    runner = ProductPublicationRunner(
        release_store=_SnapshotStore(snapshot), report_store=_report_store(tmp_path)
    )
    with pytest.raises(ValueError, match='execution snapshot binding is missing'):
        runner.run(
            run_id='missing-successor-execution-binding',
            offer_id=snapshot['offer_id'],
            plan_id=snapshot['plan_id'],
            platform_scope=('OZON',),
            platform_executors={'OZON': _zero_write_executor(calls)},
            release_candidate=candidate,
            final_approval=missing,
        )
    assert calls == []

    bound = authority.build_final_approval_receipt(
        candidate, approved_by='Kyle', execution_snapshot=snapshot
    )
    receipt = runner.run(
        run_id='bound-successor-execution-binding',
        offer_id=snapshot['offer_id'],
        plan_id=snapshot['plan_id'],
        platform_scope=('OZON',),
        platform_executors={'OZON': _zero_write_executor(calls)},
        release_candidate=candidate,
        final_approval=bound,
    )
    assert receipt.report['status'] == 'FAILED'
    assert len(calls) == 1


def test_blocked_preview_preserves_quality_blockers_and_is_not_final_authority():
    from copy import deepcopy
    from shared_platform import publication_autopilot as authority

    snapshot = ozon_snapshot()
    preview = deepcopy(snapshot)
    preview.pop('snapshot_digest')
    preview.pop('approved_at')
    preview.pop('approved_by')
    preview['schema_version'] = 'publication-snapshot-preview/v1'
    preview['status'] = 'NOT_APPROVED'
    preview['preview_digest'] = 'sha256:' + authority._canonical_digest(preview)

    candidate = authority.compile_release_preview(
        preview,
        policy=policy(4),
        incident_registry=INCIDENTS,
        platform_scope=('OZON',),
        target_scope=('ozon:UNKNOWN',),
    )

    assert candidate['schema_version'] == 'publication-candidate-preview/v1'
    assert candidate['status'] == 'PREVIEW_BLOCKED'
    assert candidate['approval_status'] == 'NOT_APPROVED'
    assert candidate['preview_snapshot_digest'] == preview['preview_digest']
    assert 'snapshot_digest' not in candidate
    assert any(row['code'] == 'target_scope_conflict' for row in candidate['blockers'])
