"""A new direct-user decision must restore only the cancelled target, append-only."""
from copy import deepcopy
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_prepared_target_reduction import prepared, record, encoded, rows, TARGETS
from test_prepared_target_consumer import common_payload
from shared_platform.prepared_target_consumer import (
    PreparedTargetConsumerPins, PreparedTargetReductionConsumer, install_prepared_target_consumer,
)


def restoration_input(prepared):
    engine, task_id, _, _, checkpoint = prepared
    reduction = record(prepared)
    with engine.transaction() as db:
        cancellation = db.execute('SELECT id FROM workbench_events WHERE task_id=? AND event_type=?',
                                  (task_id, 'publication_target_cancelled')).fetchone()[0]
        # Synthetic fixture only. Production adoption is a separate explicit operation.
        db.execute('CREATE TABLE workbench_publication_target_reinstatements '
                   '(task_id TEXT PRIMARY KEY REFERENCES workbench_execution(task_id), '
                   'request_digest TEXT NOT NULL, record_json TEXT NOT NULL)')
        for action in ('UPDATE', 'DELETE'):
            db.execute('CREATE TRIGGER workbench_publication_target_reinstatements_no_' + action.lower()
                       + ' BEFORE ' + action + ' ON workbench_publication_target_reinstatements '
                       + "BEGIN SELECT RAISE(ABORT, 'target reinstatement is immutable'); END")
    authority = {
        'schema': 'exact-product-target-reinstatement-authority/v1',
        'task_id': task_id, 'offer_id': '4039931504', 'decision': 'RESTORE_EXACT_CANCELLED_TARGET',
        'restored_target': reduction['authority']['removed_target']['target'],
        'configured_shop_id': reduction['authority']['removed_target']['configured_shop_id'],
        'complete_target_labels': TARGETS, 'target_count': 15,
        'user_message_id': 'direct-new-user-message', 'user_message_at_utc': '2026-10-10T02:00:00Z',
        'verbatim_user_instruction': '重新加入 Ozon，发布 15 店',
        'received_via_parent_thread': 'current-coordinator', 'authority_origin': 'DIRECT_USER_MESSAGE',
        'new_final_publication_approval_granted': False, 'new_paid_or_refresh_authorization_granted': False,
        'new_security_or_service_authorization_granted': False,
    }
    raw = encoded(authority)
    request = {
        'schema': 'prepared-product-target-reinstatement-request/v1', 'task_id': task_id,
        'offer_id': '4039931504', 'cancellation_event_id': cancellation,
        'cancellation_digest': reduction['reduction_digest'],
        'cancellation_request_digest': reduction['request_digest'],
        'authority_sha256': hashlib.sha256(raw).hexdigest(),
        'desired_business_scope': deepcopy(reduction['original_scope']),
        'predecessor_lineage': deepcopy(reduction['predecessor_lineage']),
        'execution_authority': False, 'user_final_publication_approval': False,
        'business_writes': 0, 'paid_calls': 0, 'new_refreshes': 0,
    }
    return reduction, request, raw, cancellation


def restore(prepared, inputs, **changes):
    _, request, raw, event = inputs
    arguments = dict(request=request, expected_version=prepared[0].release,
        expected_checkpoint_sha256=prepared[4], expected_cancellation_event_id=event,
        authority_bytes=raw, expected_authority_sha256=hashlib.sha256(raw).hexdigest())
    arguments.update(changes)
    return prepared[0].record_publication_target_reinstatement(prepared[1], **arguments)


def all_rows(engine):
    value = rows(engine)
    with engine.transaction() as db:
        value['workbench_publication_target_reinstatements'] = [tuple(row) for row in db.execute(
            'SELECT * FROM workbench_publication_target_reinstatements')]
    return value


def installed_consumer(prepared, reduction, restored, tmp_path, monkeypatch, *, pin=True):
    from modules.products import server
    from shared_platform import prepared_target_consumer as module
    monkeypatch.setattr(server, 'ROOT', tmp_path)
    monkeypatch.setattr(module, '_INSTALLED', None)
    kwargs = {'reinstatement_digest': restored['reinstatement_digest']} if pin else {}
    consumer = PreparedTargetReductionConsumer(prepared[0], PreparedTargetConsumerPins(
        reduction['task_id'], reduction['offer_id'], reduction['reduction_digest'], reduction['request_digest'],
        json.dumps(prepared[0].release), reduction['prepared_reference'],
        json.dumps(reduction['predecessor_lineage']['predecessor_R2_identity']), 'original-product-executor', **kwargs),
        service_root=tmp_path)
    install_prepared_target_consumer(consumer)
    return consumer


def test_direct_user_restoration_preserves_cancelled_history_and_restores_fifteen(prepared, tmp_path, monkeypatch):
    inputs = restoration_input(prepared); before = all_rows(prepared[0])
    restored = restore(prepared, inputs)
    after = all_rows(prepared[0])
    for table in before:
        if table not in {'workbench_events', 'workbench_publication_target_reinstatements'}:
            assert after[table] == before[table]
    assert len(after['workbench_events']) == len(before['workbench_events']) + 1
    assert restored['execution_authority'] is False and restored['final_publication_approval'] is False
    consumer = installed_consumer(prepared, inputs[0], restored, tmp_path, monkeypatch)
    envelope = consumer.read_verified('4039931504')
    assert envelope['reinstatement'] == restored
    assert envelope['effective_scope']['shops'] == inputs[0]['effective_scope']['shops']  # original cancellation
    from shared_platform.prepared_target_reduction import select_marketplace_targets
    assert select_marketplace_targets(TARGETS, offer_id='4039931504',
        r2_identity=inputs[0]['predecessor_lineage']['predecessor_R2_identity'], reduction=envelope) == TARGETS
    assert prepared[0].get(prepared[1])['effective_scope']['shops'] == TARGETS
    consumer.validate_payload(common_payload(inputs[0]), target_labels=['miaoshou:COMMON'])
    market = {'product_id': '4039931504', 'targets': TARGETS, 'r3_marketplace_binding': {
        'r2_identity': inputs[0]['predecessor_lineage']['predecessor_R2_identity'], 'target_reduction': envelope}}
    consumer.validate_payload(market, target_labels=['ozon:RU'])
    prepared[0].register_executor(consumer.pins.original_worker, ['publication'], prepared[0].release)
    claimed = prepared[0].claim(prepared[1], consumer.pins.original_worker)
    prepared[0].bind_scope(prepared[1], claimed['lease_token'], {**inputs[0]['original_scope'], 'skus':['0989']})
    with prepared[0].transaction() as db:
        assert sum(row[0].startswith('product:') for row in db.execute(
            'SELECT resource FROM workbench_resource_locks WHERE task_id=?',(prepared[1],))) == 15


def test_exact_replay_is_idempotent_under_concurrent_coordinators(prepared):
    inputs = restoration_input(prepared); first = restore(prepared, inputs); before=all_rows(prepared[0])
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(lambda _: restore(prepared, inputs), range(2))) == [first, first]
    assert all_rows(prepared[0]) == before


@pytest.mark.parametrize('drift', ['missing_authority','authority_hash','other_offer','other_task','shop','targets',
    'paid','approval','security','event','cancel_digest','r1','r2','checkpoint','version','later_event',
    'external','worker','domain','domain_lock','resource_lock','attached','review','action','late_step'])
def test_missing_authority_or_any_drift_rejects_without_mutation(prepared, drift):
    inputs = restoration_input(prepared); changes={}; request=deepcopy(inputs[1]); authority=json.loads(inputs[2])
    if drift == 'missing_authority': changes['authority_bytes']=None
    if drift == 'authority_hash': changes['expected_authority_sha256']='0'*64
    if drift == 'other_offer': authority['offer_id']='another-offer'
    if drift == 'other_task': request['task_id']='another-task'
    if drift == 'shop': authority['configured_shop_id']='another-shop'
    if drift == 'targets': authority['complete_target_labels'].append('ozon:another-shop'); authority['target_count']=16
    if drift == 'paid': authority['new_paid_or_refresh_authorization_granted']=True
    if drift == 'approval': authority['new_final_publication_approval_granted']=True
    if drift == 'security': authority['new_security_or_service_authorization_granted']=True
    if drift == 'event': request['cancellation_event_id'] += 1
    if drift == 'cancel_digest': request['cancellation_digest']='0'*64
    if drift == 'r1': request['predecessor_lineage']['predecessor_R1_snapshot_digest']='sha256:'+'0'*64
    if drift == 'r2': request['predecessor_lineage']['predecessor_R2_identity']['identity_digest']='sha256:'+'0'*64
    if drift == 'checkpoint': changes['expected_checkpoint_sha256']='0'*64
    if drift == 'version': changes['expected_version']={'code_version':'another'}
    raw=encoded(authority);request['authority_sha256']=hashlib.sha256(raw).hexdigest()
    if drift not in {'missing_authority','authority_hash'}:
        changes.update(authority_bytes=raw,expected_authority_sha256=hashlib.sha256(raw).hexdigest())
    changes['request']=request
    with prepared[0].transaction() as db:
        if drift == 'later_event': prepared[0]._event(db,prepared[1],'unrelated_checkpoint',{})
        if drift == 'external': db.execute('UPDATE workbench_execution SET external_started=1 WHERE task_id=?',(prepared[1],))
        if drift == 'worker': db.execute('UPDATE workbench_execution SET worker=? WHERE task_id=?',('another',prepared[1]))
        if drift == 'domain': db.execute('INSERT INTO workbench_domain_operations VALUES(?,?,?,?,?,?)',
            ('unresolved','[]',prepared[1],'unknown','','2026-10-10T01:00:00Z'))
        if drift == 'domain_lock': db.execute('INSERT INTO workbench_domain_operations VALUES(?,?,?,?,?,?)',
            ('locked','[]',prepared[1],'completed','','2026-10-10T01:00:00Z')); db.execute(
                'INSERT INTO workbench_domain_locks VALUES(?,?)',('product-offer:4039931504','locked'))
        if drift == 'resource_lock': db.execute('INSERT INTO workbench_resource_locks VALUES(?,?)',('product-offer:4039931504',prepared[1]))
        if drift == 'attached': db.execute('INSERT INTO workbench_external_tasks VALUES(?,?)',(prepared[1],'{}'))
        if drift == 'review': db.execute('INSERT INTO workbench_review_identity VALUES(?,?,?)',(prepared[1],'single-final-review/v1',1))
        if drift == 'action': db.execute("UPDATE workbench_execution SET action_json='{}' WHERE task_id=?",(prepared[1],))
        if drift == 'late_step': db.execute('UPDATE workbench_execution SET step_index=1 WHERE task_id=?',(prepared[1],))
    before=all_rows(prepared[0])
    with pytest.raises(ValueError): restore(prepared,inputs,**changes)
    assert all_rows(prepared[0]) == before


def test_restoration_record_is_immutable_and_does_not_unlock_legacy_consumer(prepared, tmp_path, monkeypatch):
    inputs=restoration_input(prepared); restored=restore(prepared,inputs)
    with pytest.raises(ValueError,match='REINSTATEMENT'):
        installed_consumer(prepared,inputs[0],restored,tmp_path,monkeypatch,pin=False)
    for sql in ('DELETE FROM workbench_publication_target_reinstatements WHERE task_id=?',
                "UPDATE workbench_publication_target_reinstatements SET request_digest='changed' WHERE task_id=?"):
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            with prepared[0].transaction() as db: db.execute(sql,(prepared[1],))


@pytest.mark.parametrize('drift',['missing_event','changed_record','other_pin','legacy_payload','scope_broader'])
def test_restored_consumer_rechecks_durable_receipt_at_use(prepared,tmp_path,monkeypatch,drift):
    inputs=restoration_input(prepared); restored=restore(prepared,inputs)
    consumer=installed_consumer(prepared,inputs[0],restored,tmp_path,monkeypatch)
    envelope=consumer.read_verified('4039931504')
    payload={'product_id':'4039931504','targets':TARGETS,'r3_marketplace_binding':{
        'r2_identity':inputs[0]['predecessor_lineage']['predecessor_R2_identity'],'target_reduction':envelope}}
    if drift == 'missing_event':
        with prepared[0].transaction() as db: db.execute("DELETE FROM workbench_events WHERE task_id=? AND event_type='publication_target_reinstated'",(prepared[1],))
    if drift == 'changed_record':
        with prepared[0].transaction() as db:
            db.execute('DROP TRIGGER workbench_publication_target_reinstatements_no_update')
            altered=deepcopy(restored);altered['authority']['configured_shop_id']='other'
            db.execute('UPDATE workbench_publication_target_reinstatements SET record_json=? WHERE task_id=?',
                       (json.dumps(altered),prepared[1]))
    if drift == 'other_pin': consumer.pins=__import__('dataclasses').replace(consumer.pins,reinstatement_digest='0'*64)
    if drift == 'legacy_payload': payload['r3_marketplace_binding']['target_reduction']=inputs[0];payload['targets']=inputs[0]['effective_scope']['shops']
    if drift == 'scope_broader': payload['targets']=[*TARGETS,'ozon:other']
    with pytest.raises(ValueError): consumer.validate_payload(payload,target_labels=['ozon:RU'])


def test_common_domain_coordination_includes_the_restored_target(prepared,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from shared_platform import operations_domain_guard as guard, release_store
    inputs=restoration_input(prepared);restored=restore(prepared,inputs)
    consumer=installed_consumer(prepared,inputs[0],restored,tmp_path,monkeypatch)
    seen=[]
    monkeypatch.setattr(release_store,'default_release_store',lambda:SimpleNamespace(get_run=lambda _:None))
    monkeypatch.setattr(prepared[0],'begin_domain_operation',lambda _id,**kw:seen.append(kw) or {'acquired':True})
    payload={**common_payload(inputs[0]),'seller_sku':'0989'}
    guard.begin_common({'product_id':'4039931504','targets':['miaoshou:COMMON'],'plan_id':'synthetic-common',
                       'payload_digest':'1'*64,'payload':payload},tmp_path)
    assert seen[0]['shops']==TARGETS and seen[0]['owner_task_id']==prepared[1]


@pytest.mark.parametrize('drift',[None,'approval_claim','capability_missing','old_fourteen'])
def test_final_review_uses_restored_scope_without_relaxing_other_gates(prepared,tmp_path,monkeypatch,drift):
    from types import SimpleNamespace
    from modules.products import server
    from shared_platform import operations_publication_common as common
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    from shared_platform.final_review_server_admission import ApprovalBlocked
    inputs=restoration_input(prepared);restored=restore(prepared,inputs)
    installed_consumer(prepared,inputs[0],restored,tmp_path,monkeypatch)
    bound={'offer_id':'4039931504','preparation_source':{'status':'VERIFIED_NATIVE_R1'}}
    monkeypatch.setattr(common,'_verified_binding',lambda *a,**kw:bound)
    review={'schema_version':'native-sole-final-review/v1','approval_saved':False,'plan_id':'synthetic-market',
            'targets':TARGETS,'execution_authority':False,'review_digest':'digest'}
    if drift=='approval_claim':review['approval_saved']=True
    if drift=='old_fourteen':review['targets']=inputs[0]['effective_scope']['shops']
    current={'ok':True,'common':{},'marketplace':{'status':'APPROVAL_REQUIRED','final_review_available':True,
        'native_final_review':review}}
    monkeypatch.setattr(server,'_publication_stages_for_request',lambda _: (200,current))
    # Execute only the existing readiness method; never start an actor, Job, ACL or pipe.
    service=object.__new__(NativeSoleFinalService)
    service._operations=None if drift=='capability_missing' else SimpleNamespace(profile=object())
    service._closed=False
    task={'scope':deepcopy(inputs[0]['original_scope'])}
    view={'common':{},'marketplace':{'plan':{'plan_id':'synthetic-market'}}}
    if drift is None:
        result=service.final_review_readiness(task,view)
        assert result['status']=='READY' and result['execution_authority'] is False
    else:
        with pytest.raises(ApprovalBlocked):service.final_review_readiness(task,view)


def test_schema_requires_explicit_adoption_with_snapshot_cas_and_original_history_preserved(prepared,tmp_path):
    from types import SimpleNamespace
    from shared_platform.prepared_target_reinstatement import adopt_reinstatement_schema,SCHEMA
    from shared_platform.original_prepared_continuation import OriginalPreparedTaskContinuation
    from scripts.migrate_operations_task_schema import _fingerprint
    reduction=record(prepared)
    engine,task_id,*_=prepared
    # Synthetic source-pin verification is outside this migration test. No service is installed.
    binding=object.__new__(OriginalPreparedTaskContinuation)
    binding.engine=engine;binding.pins=SimpleNamespace(task_id=task_id)
    binding.verify_sources=lambda:None
    with engine.transaction() as db:
        db.execute('UPDATE workbench_execution SET state=? WHERE task_id=?',('failed',task_id))
        before=_fingerprint(db)
    result=adopt_reinstatement_schema(binding,expected_logical_sha256=before['logical_sha256'],backup_path=tmp_path/'schema-backup.db')
    assert result['status']=='INSTALLED' and len(result['objects_added'])==3
    with engine.transaction() as db:
        after=_fingerprint(db)
        assert {k:v for k,v in after['tables'].items() if k in before['tables']}==before['tables']
        assert all(after['objects'][k]==v for k,v in before['objects'].items())
    assert (tmp_path/'schema-backup.db').is_file()
    with sqlite3.connect(tmp_path/'schema-backup.db') as db:
        assert _fingerprint(db)['logical_sha256']==before['logical_sha256']
    with pytest.raises(ValueError,match='BACKUP'):
        adopt_reinstatement_schema(binding,expected_logical_sha256=after['logical_sha256'],backup_path=tmp_path/'schema-backup.db')
    replay=adopt_reinstatement_schema(binding,expected_logical_sha256=after['logical_sha256'],backup_path=tmp_path/'unused-backup.db')
    assert replay=={'status':'ALREADY_INSTALLED','database_writes':0}
    assert not (tmp_path/'unused-backup.db').exists()


@pytest.mark.parametrize('drift',['hash','live_writer','partial_schema','conflicting_schema'])
def test_schema_drift_or_live_writer_never_adds_objects(prepared,tmp_path,drift):
    from types import SimpleNamespace
    from shared_platform.prepared_target_reinstatement import adopt_reinstatement_schema,SCHEMA
    from shared_platform.original_prepared_continuation import OriginalPreparedTaskContinuation
    from scripts.migrate_operations_task_schema import _fingerprint
    engine,task_id,*_=prepared
    record(prepared)
    binding=object.__new__(OriginalPreparedTaskContinuation)
    binding.engine=engine;binding.pins=SimpleNamespace(task_id=task_id);binding.verify_sources=lambda:None
    with engine.transaction() as db:
        db.execute('UPDATE workbench_execution SET state=? WHERE task_id=?',('failed',task_id))
        if drift=='live_writer':db.execute('UPDATE workbench_execution SET worker=? WHERE task_id=?',('original',task_id))
        if drift=='partial_schema':db.execute(next(iter(SCHEMA.values())))
        if drift=='conflicting_schema':db.execute('CREATE TABLE workbench_publication_target_reinstatements (task_id TEXT)')
        before=_fingerprint(db)
    with pytest.raises(ValueError):
        adopt_reinstatement_schema(binding,expected_logical_sha256='0'*64 if drift=='hash' else before['logical_sha256'],
                                  backup_path=tmp_path/'schema-backup.db')
    with engine.transaction() as db:assert _fingerprint(db)==before
