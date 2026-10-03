from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import json
import threading

import pytest

from shared_platform import product_publication_closure as closure
from test_r3_status_projection import approved_context,seed_historical_report
from modules.products import server
from shared_platform import publication_r3_image_bridge as bridge


def dependencies(store,tmp_path):
    return dict(release_store=store,run_store=server._product_publication_run_store(),
        report_store=server._product_publication_report_store(),authority_root=bridge.REPORTS_ROOT,
        closure_root=tmp_path/'closures')


def forged_closure(data,snapshot,receipt):
    return closure.build_publication_closure(offer_id=data['offer_id'],revision=snapshot['product_revision'],
        plan_id=data['plan_id'],snapshot_digest=snapshot['snapshot_digest'],
        recorded_by='Synthetic reviewer',recorded_at=datetime.now(timezone.utc).isoformat(),
        targets=[dict(target_label='tiktok:LH_MY',source_status='PUBLISHED',resolution='OFFICIAL_READBACK_VERIFIED',
            evidence_code='ACCEPTED',source_run_id='status-run',platform_identity_bound=True,manual_handoff=None)],
        source_reports=[dict(run_id='status-run',report_path=receipt.stored.report_path,report_digest='sha256:'+'a'*64)])


def record_boundary(document,store,tmp_path):
    # Before the application service existed, the only persistence boundary was
    # the schema-only store. Keep that exact baseline path for defect evidence.
    service=getattr(closure,'PublicationClosureService',None)
    if service is None:return closure.store_publication_closure(document,root=tmp_path/'closures')
    return service(**dependencies(store,tmp_path)).record({'status':'READY_TO_RECORD',
        'closure':document,'input_digest':'sha256:'+'c'*64})


def test_real_failed_report_cannot_be_relabelled_complete(tmp_path,monkeypatch):
    store,data,_,market,snapshot=approved_context(tmp_path,monkeypatch)
    receipt=seed_historical_report(store,data,market,snapshot,state='FAILED')
    forged=forged_closure(data,snapshot,receipt)
    with pytest.raises(ValueError):record_boundary(forged,store,tmp_path)
    assert not list((tmp_path/'closures').rglob('closure-report.json'))


def test_corrupt_closure_history_does_not_fall_back(tmp_path,monkeypatch):
    store,data,_,market,snapshot=approved_context(tmp_path,monkeypatch)
    receipt=seed_historical_report(store,data,market,snapshot,state='FAILED')
    document=forged_closure(data,snapshot,receipt)
    first=closure.store_publication_closure(document,root=tmp_path/'closures')
    corrupt=first.parent.parent/'closure-corrupt'/'closure-report.json'
    corrupt.parent.mkdir();corrupt.write_text('{broken')
    with pytest.raises(ValueError):closure.latest_publication_closure(data['offer_id'],root=tmp_path/'closures')


def test_concurrent_same_id_never_overwrites_complete_facts(tmp_path,monkeypatch):
    from test_product_publication_closure import _closure
    first=_closure();second=deepcopy(first)
    second['recorded_by']='Another reviewer'
    second['closure_digest']=closure._sha256({k:v for k,v in second.items() if k!='closure_digest'})
    barrier=threading.Barrier(2);real_replace=closure.os.replace
    def replace(*args,**kwargs):
        barrier.wait(timeout=10)
        return real_replace(*args,**kwargs)
    monkeypatch.setattr(closure.os,'replace',replace)
    def write(document):
        try:return closure.store_publication_closure(document,root=tmp_path),None
        except ValueError as error:return None,str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(write,[first,second]))
    assert sum(error is None for _,error in results)==1
    path=next(path for path,error in results if error is None)
    assert json.loads(path.read_text()) in (first,second)


def declare_private_closure_observation(service, receipt, *, fixture_status):
    """Explicit unit-test fact, never persisted as official provider evidence."""
    from shared_platform.product_publication_reports import _digest
    assert receipt.report['status'] == fixture_status
    for target in receipt.report['targets']:
        assert target['status'] == fixture_status
        key = (receipt.report['run_id'], receipt.report['report_id'],
               _digest(receipt.report), target['target_label'])
        service._private_unit_observations[key] = fixture_status


def install_private_closure_projector(monkeypatch, observations):
    """Isolate closure mechanisms after an explicitly simulated observation.

    The real projector still validates immutable sources, authority, timestamps,
    unknown history and identities. Only exact private fixture declarations are
    adapted; new or changed reports never inherit an observation automatically.
    This is not a producer/admission/official-readback integration test.
    """
    from shared_platform import publication_status_projection as projection
    original = projection.project_execution
    def key(label, source):
        source = source or {}
        return (source.get('run_id'), source.get('report_id'),
                source.get('report_digest'), label)
    def private_projector(**kwargs):
        result = original(**kwargs)
        for row in result['target_results']:
            label = row['target_label']
            declared = observations.get(key(label, row['source']))
            history = row.get('history', [])
            permitted = {'TARGET_OFFICIAL_READBACK_UNAVAILABLE', 'EARLIER_RUN_UNRESOLVED'}
            all_history_declared_known = bool(history) and all(
                key(label, item['source']) in observations
                and item['outcome_unknown'] is False for item in history)
            derived_none = (row['outcome_unknown'] is None
                            and 'EARLIER_RUN_UNRESOLVED' in row['blockers']
                            and all_history_declared_known)
            if (declared is None or (row['outcome_unknown'] is not False and not derived_none)
                    or set(row['blockers']) - permitted or not history
                    or not all_history_declared_known):
                continue
            assert row['reported_status'] == declared
            row.update(status=declared, official_success=declared=='PUBLISHED', outcome_unknown=False,
                       readback_completed=True,
                       lifecycle='READBACK_ONLY' if declared=='PROCESSING' else 'REPORT_RECORDED',
                       next_action='READ_EXISTING_REPORT', blockers=[])
            for item in history:
                item.update(status=item['reported_status'], readback_completed=True, blockers=[])
        for platform in result['platforms']:
            rows = [r for r in result['target_results'] if r['target_label'] in platform['target_labels']]
            platform['status'] = projection._status(rows)
            if all(row['status']!='RECONCILIATION_REQUIRED' for row in rows):
                platform['next_action'] = 'READ_EXISTING_REPORT'
        result['status'] = projection._status(result['target_results'])
        result['execution_summary']['official_success_count'] = sum(
            row['official_success'] for row in result['target_results'])
        return result
    monkeypatch.setattr(projection, 'project_execution', private_projector)


def synthetic_closure_context(tmp_path,monkeypatch,second_state='PUBLISHED'):
    """Private closure unit fixture; does not certify report or provider truth."""
    store,data,io,market,snapshot=approved_context(tmp_path,monkeypatch)
    first=seed_historical_report(store,data,market,snapshot,run_id='first',state='PUBLISHED')
    second=seed_historical_report(store,data,market,snapshot,run_id='second',platform=market['targets'][1].split(':')[0].upper(),state=second_state)
    service=closure.PublicationClosureService(**dependencies(store,tmp_path))
    service._private_unit_observations={}
    declare_private_closure_observation(service,first,fixture_status='PUBLISHED')
    declare_private_closure_observation(service,second,fixture_status=second_state)
    install_private_closure_projector(monkeypatch,service._private_unit_observations)
    inputs=dict(offer_id=data['offer_id'],plan_id=data['plan_id'],recorded_by='Synthetic reviewer',
        recorded_at=(datetime.now(timezone.utc)+timedelta(seconds=5)).isoformat())
    return store,data,io,market,snapshot,service,inputs


@pytest.mark.parametrize('reported_state',['PUBLISHED','PROCESSING'])
def test_real_report_projection_without_target_observer_cannot_prepare_closure(tmp_path,monkeypatch,reported_state):
    # Intentionally bypass the private unit projector. Real ReportStore and
    # production projection must reject stage/global-flag claims as observation.
    store,data,io,market,snapshot=approved_context(tmp_path,monkeypatch)
    for index,label in enumerate(market['targets']):
        seed_historical_report(store,data,market,snapshot,run_id=f'unobserved-{index}',
            platform=label.split(':')[0].upper(),state=reported_state)
    service=closure.PublicationClosureService(**dependencies(store,tmp_path))
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    prepared=service.prepare(offer_id=data['offer_id'],plan_id=data['plan_id'],recorded_by='Synthetic reviewer',
        recorded_at=(datetime.now(timezone.utc)+timedelta(seconds=5)).isoformat())
    assert prepared['status']=='BLOCKED' and prepared['closure'] is None
    assert prepared['writes_performed']==[]
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert io.mutations==1 and not (tmp_path/'closures').exists()
    assert all(row['reported_status']==reported_state and row['readback_completed'] is None
               and row['reported_readback_completed'] is True and not row['official_success']
               and 'TARGET_OFFICIAL_READBACK_UNAVAILABLE' in row['blockers']
               for row in prepared['target_results'])


@pytest.mark.parametrize('history_kind',['unregistered','declared_unknown'])
def test_private_closure_projector_keeps_unverified_earlier_history_blocked(tmp_path,monkeypatch,history_kind):
    store,data,io,market,snapshot,service,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    unknown = history_kind=='declared_unknown'
    older=seed_historical_report(store,data,market,snapshot,run_id='unverified-older',
        state='FAILED' if unknown else 'PROCESSING',unknown=unknown)
    if unknown:
        # Even an explicit private declaration cannot erase actual unknown
        # history; the original per-history outcome remains authoritative.
        declare_private_closure_observation(service,older,fixture_status='FAILED')
    current=seed_historical_report(store,data,market,snapshot,run_id='declared-current',state='PUBLISHED')
    declare_private_closure_observation(service,current,fixture_status='PUBLISHED')
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    prepared=service.prepare(**inputs)
    assert prepared['status']=='BLOCKED' and prepared['closure'] is None
    row=prepared['target_results'][0]
    assert row['reported_status']=='PUBLISHED' and row['source']['run_id']=='declared-current'
    assert row['official_success'] is False and row['readback_completed'] is None
    assert row['outcome_unknown'] is (True if unknown else None)
    assert 'EARLIER_RUN_UNRESOLVED' in row['blockers']
    assert any(item['source']['run_id']=='unverified-older' for item in row['history'])
    assert prepared['writes_performed']==[] and io.mutations==1
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert not (tmp_path/'closures').exists()


@pytest.mark.parametrize('second_state',['PUBLISHED','PROCESSING','FAILED'])
def test_real_bound_service_roundtrip_preserves_full_scope(tmp_path,monkeypatch,second_state):
    store,data,io,market,snapshot,service,inputs=synthetic_closure_context(tmp_path,monkeypatch,second_state)
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    prepared=service.prepare(**inputs)
    assert prepared['status']=='READY_TO_RECORD'
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    document=prepared['closure']
    assert [row['target_label'] for row in document['targets']]==market['targets']
    assert document['targets'][1]['source_status']==second_state
    assert document['summary']['verified_count']==(2 if second_state=='PUBLISHED' else 1)
    path=service.record(prepared)
    assert service.record(prepared)==path
    assert service.latest(offer_id=data['offer_id'],plan_id=data['plan_id'])==document
    assert io.mutations==1


@pytest.mark.parametrize('case',['no_run','unknown','missing','newer_failure','old_unknown'])
def test_prepare_blocks_unclosable_evidence_without_fabricating_sources(tmp_path,monkeypatch,case):
    store,data,io,market,snapshot=approved_context(tmp_path,monkeypatch)
    if case!='no_run':
        receipt=seed_historical_report(store,data,market,snapshot,state='FAILED' if case in {'unknown','old_unknown'} else 'PUBLISHED',
                        unknown=case in {'unknown','old_unknown'})
        if case=='missing':(server._product_publication_report_store().reports_root/receipt.stored.report_path).unlink()
        if case in {'old_unknown','newer_failure'}:
            seed_historical_report(store,data,market,snapshot,run_id='newer',state='PUBLISHED' if case=='old_unknown' else 'FAILED')
    service=closure.PublicationClosureService(**dependencies(store,tmp_path))
    prepared=service.prepare(offer_id=data['offer_id'],plan_id=data['plan_id'],recorded_by='Synthetic reviewer',
        recorded_at=datetime.now(timezone.utc).isoformat())
    assert prepared['status']=='BLOCKED' and prepared['closure'] is None
    assert 'business_complete' not in prepared
    assert [row['target_label'] for row in prepared['target_results']]==market['targets']
    assert prepared['writes_performed']==[] and not (tmp_path/'closures').exists()


@pytest.mark.parametrize('change',['report_deleted','new_failure','new_unknown','superseded','input','digest'])
def test_record_revalidates_after_prepare(tmp_path,monkeypatch,change):
    store,data,_,market,snapshot,service,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    prepared=service.prepare(**inputs)
    if change=='report_deleted':
        report=service.report_store.get_report_by_run(run_id='first')
        (service.report_store.reports_root/report['report_path']).unlink()
    elif change in {'new_failure','new_unknown'}:
        seed_historical_report(store,data,market,snapshot,run_id='newer',state='FAILED',unknown=change=='new_unknown')
    elif change=='superseded':store.supersede_plan(data['plan_id'],reason='Synthetic supersession')
    elif change=='input':
        prepared['closure']['recorded_by']='Forged reviewer'
        prepared['closure']['closure_digest']=closure._sha256({k:v for k,v in prepared['closure'].items() if k!='closure_digest'})
    else:prepared['input_digest']='sha256:'+'b'*64
    with pytest.raises(ValueError):service.record(prepared)
    assert not list((tmp_path/'closures').rglob('closure-report.json'))


def test_manual_handoff_retains_explicit_target_person_time_and_note(tmp_path,monkeypatch):
    _,data,_,market,_,service,inputs=synthetic_closure_context(tmp_path,monkeypatch,'FAILED')
    handoff={'accepted_by':'Explicit synthetic owner','accepted_at':datetime.now(timezone.utc).isoformat(),
             'note':'Synthetic explicit disposition for this target'}
    prepared=service.prepare(**inputs,manual_handoffs={market['targets'][1]:handoff})
    document=prepared['closure'];row=document['targets'][1]
    assert row['manual_handoff']==handoff and row['source_status']=='FAILED'
    assert row['resolution']=='MANUAL_HANDOFF_ACCEPTED' and not row['platform_identity_bound']
    assert document['summary']['verified_count']==1
    service.record(prepared)
    assert service.latest(offer_id=data['offer_id'],plan_id=data['plan_id'])==document


@pytest.mark.parametrize('handoff',[None,{}, {'accepted_by':'Owner','accepted_at':'2000-01-01T00:00:00Z','note':'Too early'}])
def test_manual_handoff_never_defaults_missing_evidence(tmp_path,monkeypatch,handoff):
    _,_,_,market,_,service,inputs=synthetic_closure_context(tmp_path,monkeypatch,'FAILED')
    with pytest.raises(ValueError):service.prepare(**inputs,manual_handoffs={market['targets'][1]:handoff})


def test_bound_latest_rejects_stale_completion_and_other_plan(tmp_path,monkeypatch):
    store,data,_,market,snapshot,service,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    service.record(service.prepare(**inputs))
    assert service.latest(offer_id=data['offer_id'],plan_id='foreign-plan') is None
    seed_historical_report(store,data,market,snapshot,run_id='later',state='FAILED')
    with pytest.raises(ValueError):service.latest(offer_id=data['offer_id'],plan_id=data['plan_id'])


@pytest.mark.parametrize('position',['root','file','ancestor'])
def test_links_are_rejected_before_closure_read_or_write(tmp_path,position):
    from test_product_publication_closure import _closure
    document=_closure();root=tmp_path/'closures';outside=tmp_path/'outside';outside.mkdir()
    if position=='root':root.symlink_to(outside,target_is_directory=True)
    elif position=='ancestor':
        root.mkdir();(root/document['offer_id']).symlink_to(outside,target_is_directory=True)
    else:
        path=closure.store_publication_closure(document,root=root)
        external=outside/'external.json';external.write_bytes(path.read_bytes())
        path.unlink();path.symlink_to(external)
    before={str(p):p.read_bytes() for p in outside.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):closure.store_publication_closure(document,root=root)
    with pytest.raises(ValueError):closure.latest_publication_closure(document['offer_id'],root=root)
    assert before=={str(p):p.read_bytes() for p in outside.rglob('*') if p.is_file()}


def test_source_report_link_cannot_supply_closure_truth(tmp_path,monkeypatch):
    _,_,_,_,_,service,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    report=service.report_store.get_report_by_run(run_id='first')
    path=service.report_store.reports_root/report['report_path']
    outside=service.report_store.reports_root/'linked-report.json';outside.write_bytes(path.read_bytes())
    path.unlink();path.symlink_to(outside)
    try:result=service.prepare(**inputs)
    except ValueError:return
    assert result['status']=='BLOCKED'


@pytest.mark.parametrize('field',['report_digest','report_path','source_run_id','target_label','source_status','snapshot_digest'])
def test_forged_ready_preview_cannot_override_bound_facts(tmp_path,monkeypatch,field):
    _,_,_,_,_,service,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    prepared=service.prepare(**inputs);document=prepared['closure']
    if field in {'report_digest','report_path'}:
        document['source_reports'][0][field]='sha256:'+'f'*64 if field=='report_digest' else 'foreign/report.json'
    elif field=='snapshot_digest':document[field]='sha256:'+'f'*64
    elif field=='source_status':document['targets'][0][field]='FAILED'
    else:document['targets'][0][field]='foreign' if field=='source_run_id' else 'tiktok:foreign'
    document['closure_digest']=closure._sha256({k:v for k,v in document.items() if k!='closure_digest'})
    with pytest.raises(ValueError):service.record(prepared)
    assert not (tmp_path/'closures').exists()


@pytest.mark.parametrize('store_name',['release_store','run_store','report_store'])
def test_evidence_database_links_are_rejected(tmp_path,monkeypatch,store_name):
    _,_,_,_,_,service,inputs=synthetic_closure_context(tmp_path,monkeypatch)
    store=getattr(service,store_name)
    link=tmp_path/(store_name+'-link.db');link.symlink_to(store.path)
    store.path=link
    with pytest.raises(ValueError):service.prepare(**inputs)


def test_closure_root_junction_is_rejected(tmp_path):
    import os,subprocess
    from test_product_publication_closure import _closure
    if os.name!='nt':pytest.skip('Windows junction test')
    outside=tmp_path/'outside';outside.mkdir();junction=tmp_path/'junction'
    result=subprocess.run(['cmd.exe','/c','mklink','/J',str(junction),str(outside)],capture_output=True)
    assert result.returncode==0,result.stderr
    document=_closure()
    with pytest.raises(ValueError):closure.store_publication_closure(document,root=junction)
    with pytest.raises(ValueError):closure.latest_publication_closure(document['offer_id'],root=junction)
    assert list(outside.iterdir())==[]


def test_exact_plan_history_does_not_choose_newer_foreign_plan(tmp_path):
    from test_product_publication_closure import _closure
    original=_closure();foreign=deepcopy(original)
    foreign.update(plan_id='foreign-plan',closure_id='closure:foreign',recorded_at='2027-01-01T00:00:00Z')
    foreign['closure_digest']=closure._sha256({k:v for k,v in foreign.items() if k!='closure_digest'})
    closure.store_publication_closure(original,root=tmp_path)
    closure.store_publication_closure(foreign,root=tmp_path)
    assert closure.latest_publication_closure(original['offer_id'],plan_id=original['plan_id'],root=tmp_path)==original
