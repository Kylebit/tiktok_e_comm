"""Retained domain admission, original-owner leases, no duplicate task or R1/R2 work."""
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from shared_platform import original_prepared_continuation as module
from shared_platform.original_prepared_continuation import (
    OriginalContinuationPins, OriginalPreparedTaskContinuation, SourceRole, RetainedFile,
)
from shared_platform.operations_runtime import RuntimeProfile, runtime_manifest
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS, validate_r2_identity
from shared_platform.publication_rounds import canonical_digest
from shared_platform.round1_category_evidence import digest
from shared_platform.retained_sku_namespace import RetainedSkuNamespaceReader
from shared_platform import workbench_publication_native as native
from owned_common_review_fixtures import historical_completed_context


def pin(path):
    return RetainedFile(path, hashlib.sha256(path.read_bytes()).hexdigest())


def source_role(root, name, data_root):
    root.mkdir()
    (root / 'shared_platform').mkdir()
    (root / 'shared_platform/owned.py').write_text('SOURCE_ROLE = ' + repr(name) + '\n')
    def git(*args):
        return subprocess.run(['git', '-c', 'core.autocrlf=false', '-c', 'commit.gpgsign=false',
            '-c', 'user.name=Owned fixture', '-c', 'user.email=owned@example.invalid', *args],
            cwd=root, text=True, capture_output=True, check=True).stdout.strip()
    git('init', '--quiet'); git('add', 'shared_platform/owned.py')
    git('commit', '--quiet', '-m', 'Pin isolated source role')
    return SourceRole(name, RuntimeProfile(root, data_root, 'stable', git('rev-parse', 'HEAD'), runtime_manifest(root)))


@pytest.fixture
def retained(tmp_path, monkeypatch):
    from modules.products import server
    documents, _, store, _, io = historical_completed_context(tmp_path, monkeypatch)
    data = tmp_path / 'operations'; data.mkdir()
    roles = tuple(source_role(tmp_path / name, name, data) for name in
                  ('original', 'r1_controller', 'r1_recovery', 'r2_producer', 'service'))
    original, r2 = roles[0].profile, roles[3].profile
    identity = validate_r2_identity(documents)
    snapshot = documents['round1_snapshot']; offer = snapshot['offer_id']
    engine = WorkbenchEngine(data / 'tasks.db', {'code_version': original.version, 'environment': 'stable', 'manifest_digest': original.manifest_digest})
    task = engine.create({'template': 'publication', 'source_key': 'one-original-task',
                         'scope': {'offer_id': offer, 'shops': sorted(snapshot['canonical_targets']), 'skus': []}})
    prepared = {'scope': {'offer_id': offer, 'requested_targets': snapshot['canonical_targets']},
                'packet': documents['first_review'], 'approval_fingerprint': snapshot['product_approval_fingerprint']}
    prepared['reference'] = 'r1-prepared:' + digest(prepared)[7:]
    state = {'offer_id': offer, 'review': {'selected_sites': snapshot['workbench_tiktok_sites'], 'fields_locked': True,
             'seller_sku': snapshot['fact_snapshot']['product_facts']['seller_sku']},
             'product_approval': {'status': 'approved', 'approval_id': snapshot['product_approval_id'],
              'input_fingerprint': snapshot['product_approval_fingerprint'], 'round1_prepared_reference': prepared['reference'],
              'round1_review_digest': snapshot['first_review_digest']}}
    state_path = original.root / 'data/new_product_workbench' / (offer + '.json')
    state_path.parent.mkdir(parents=True); state_path.write_text(json.dumps(state))
    for role in roles:
        (role.profile.root / '.git/info/exclude').write_text('data/\nreports/\n')
    files = []
    for key, name in R2_DOCUMENTS.items():
        path = r2.root / 'reports/product-preparation' / offer / name
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(documents[key], ensure_ascii=False))
        files.append(pin(path))
    original_snapshot = original.root / 'reports/product-preparation' / offer / 'round1-approved-snapshot.json'
    original_snapshot.parent.mkdir(parents=True); original_snapshot.write_text(json.dumps(snapshot))
    with store._connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS round1_workspace_preparations (request_id TEXT PRIMARY KEY, offer_id TEXT,reference TEXT,request_json TEXT,document_json TEXT,document_digest TEXT)')
        db.execute('INSERT INTO round1_workspace_preparations VALUES(?,?,?,?,?,?)',
                   ('original-preparation', offer, prepared['reference'], '{}', json.dumps(prepared), digest(prepared)))
    checkpoint = {'native_preparation': {'prepared_reference': prepared['reference']}}
    with engine.transaction() as db:
        db.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?', (json.dumps(checkpoint), task['task_id']))
        engine._event(db, task['task_id'], 'checkpoint_saved', {'checkpoint': checkpoint})
        db.execute("UPDATE workbench_events SET created_at=? WHERE task_id=? AND event_type='checkpoint_saved'", ('2020-01-01T00:00:00+00:00', task['task_id']))
        engine._event(db, task['task_id'], 'prepared_writer_quiesced', {'owner_business_quiescence_reviewed': True})
        event = db.execute('SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type=?', (task['task_id'], 'prepared_writer_quiesced')).fetchone()
    pins = OriginalContinuationPins(task['task_id'], offer, 'original-executor', prepared['reference'],
        json.dumps(identity), event['id'], hashlib.sha256(event['detail_json'].encode()).hexdigest(), roles, tuple(files), pin(state_path))
    binding = OriginalPreparedTaskContinuation(engine, pins, release_store_path=store.path)
    monkeypatch.setattr(server, 'ROOT', roles[4].profile.root)
    monkeypatch.setattr(module, '_INSTALLED', None)
    module.install_original_continuation(binding)
    return binding, io, tmp_path


def test_different_original_r2_and_service_roots_read_one_retained_domain_identity(retained):
    binding, io, _ = retained
    documents = binding.read_documents()
    assert validate_r2_identity(documents) == json.loads(binding.pins.r2_identity_json)
    task = binding._original_task()
    assert native.read_frozen(task, binding.original_profile)['prepared_reference'] == binding.pins.prepared_reference
    assert native.read_images(task, binding.original_profile)['native_r2'] == json.loads(binding.pins.r2_identity_json)
    assert native.load_service_r2_documents(binding.pins.offer_id, SimpleNamespace(engine=binding.engine, profile=binding.original_profile)) == documents
    assert binding.original_profile.root != binding.service_profile.root
    assert io.mutations == 0


@pytest.mark.parametrize('drift', ['source', 'r2', 'state', 'a2_event', 'task_version', 'wrong_operations'])
def test_durable_source_or_receipt_drift_is_terminal(retained, drift):
    binding, io, _ = retained
    if drift == 'source': (binding.service_profile.root / 'shared_platform/owned.py').write_text('changed\n')
    if drift == 'r2': binding.pins.documents[2].path.write_text('{}')
    if drift == 'state': binding.pins.original_state.path.write_text('{}')
    if drift in ('a2_event', 'task_version'):
        with binding.engine.transaction() as db:
            if drift == 'a2_event': db.execute('UPDATE workbench_events SET detail_json=? WHERE id=?', ('{}', binding.pins.quiescence_event_id))
            else: db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?', ('{}', binding.pins.task_id))
    with pytest.raises(ValueError):
        if drift == 'wrong_operations': native.load_service_r2_documents(binding.pins.offer_id, SimpleNamespace(engine=binding.engine, profile=binding.service_profile))
        else: binding.read_documents()
    assert io.mutations == 0


def catalog(path):
    import sqlite3
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE shops(cipher TEXT,region TEXT); CREATE TABLE products(seller_sku TEXT,shop_cipher TEXT,product_id TEXT,sku_id TEXT); CREATE TABLE shopee_products(seller_sku TEXT);')
    return path


def test_original_executor_adopts_real_retained_steps_once_and_preserves_a2_event(retained):
    binding, io, tmp_path = retained
    reader = RetainedSkuNamespaceReader(binding, catalog_path=catalog(tmp_path / 'catalog.db'))
    engine = binding.engine
    engine.register_executor(binding.pins.original_worker, ['publication'], engine.release)
    token = engine.claim(binding.pins.task_id, binding.pins.original_worker, ttl=300)['lease_token']
    before = binding._original_task()
    after = binding.adopt_retained_stage_receipts(before, token, sku_reader=reader)
    assert after['current_step'] == 'release'
    assert [s['state'] for s in after['steps'][:2]] == ['completed', 'completed']
    assert after['scope']['shops'] == before['scope']['shops'] and after['version'] == before['version']
    assert after['steps'][1]['checkpoint']['native_r2'] == json.loads(binding.pins.r2_identity_json)
    with engine.transaction() as db:
        event = db.execute('SELECT event_type FROM workbench_events WHERE id=?', (binding.pins.quiescence_event_id,)).fetchone()
        assert event[0] == 'prepared_writer_quiesced'
        assert db.execute('SELECT count(*) FROM workbench_execution').fetchone()[0] == 1
        before_events = db.execute('SELECT count(*) FROM workbench_events').fetchone()[0]
    assert binding.adopt_retained_stage_receipts(after, token, sku_reader=reader)['current_step'] == 'release'
    with engine.transaction() as db:
        assert db.execute('SELECT count(*) FROM workbench_events').fetchone()[0] == before_events
    assert io.mutations == 0


def test_another_writer_cannot_adopt_receipts(retained):
    binding, io, tmp_path = retained
    engine = binding.engine
    engine.register_executor('another-writer', ['publication'], engine.release)
    token = engine.claim(binding.pins.task_id, 'another-writer')['lease_token']
    reader = RetainedSkuNamespaceReader(binding, catalog_path=catalog(tmp_path / 'catalog.db'))
    with pytest.raises(ValueError, match='ORIGINAL_WRITER_REQUIRED'):
        binding.adopt_retained_stage_receipts(binding._original_task(), token, sku_reader=reader)
    assert binding._original_task()['current_step'] == 'facts' and io.mutations == 0


@pytest.mark.parametrize('prefix', ['', '66', '77', '88', '99'])
def test_real_catalog_alias_conflict_blocks_before_stage_adoption(retained, prefix):
    import sqlite3
    binding, io, tmp_path = retained
    sku = binding.read_frozen(binding._original_task(), binding.original_profile)['internal_sku']
    path = catalog(tmp_path / 'catalog.db')
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO products VALUES(?,?,?,?)', (prefix + sku, 'owned-fixture-shop', 'other-product', 'other-variant'))
    reader = RetainedSkuNamespaceReader(binding, catalog_path=path)
    with pytest.raises(ValueError, match='CATALOG_CONFLICT'): reader.read_verified(sku)
    assert binding._original_task()['current_step'] == 'facts' and io.mutations == 0


def remove_only_test_cancellation_schema(engine):
    with engine.transaction() as db:
        db.execute('DROP TRIGGER workbench_publication_scope_reductions_no_update')
        db.execute('DROP TRIGGER workbench_publication_scope_reductions_no_delete')
        db.execute('DROP TABLE workbench_publication_scope_reductions')


def test_controlled_schema_adds_only_three_objects_and_keeps_every_old_row(retained):
    from shared_platform.prepared_target_schema_adoption import inspect_schema_adoption, adopt_schema
    binding, _, tmp_path = retained
    with binding.engine.transaction() as db:
        binding.engine._state(db, binding.pins.task_id, 'failed')
    remove_only_test_cancellation_schema(binding.engine)
    plan = inspect_schema_adoption(binding.engine)
    assert len(plan['missing']) == 3
    result = adopt_schema(binding, expected_logical_sha256=plan['logical_sha256'], backup_path=tmp_path / 'backup.db')
    assert len(result['objects_added']) == 3 and result['existing_table_rows_and_schema_unchanged'] is True
    installed = inspect_schema_adoption(binding.engine)
    assert installed['status'] == 'INSTALLED'
    assert adopt_schema(binding, expected_logical_sha256=installed['logical_sha256'], backup_path=tmp_path / 'unused.db')['status'] == 'ALREADY_INSTALLED'


def test_schema_never_retires_a_writer_or_ignores_changed_ledger(retained):
    from shared_platform.prepared_target_schema_adoption import inspect_schema_adoption, adopt_schema
    binding, _, tmp_path = retained
    remove_only_test_cancellation_schema(binding.engine)
    plan = inspect_schema_adoption(binding.engine)
    with pytest.raises(ValueError, match='CAS_CHANGED'):
        adopt_schema(binding, expected_logical_sha256='0' * 64, backup_path=tmp_path / 'wrong.db')
    with pytest.raises(ValueError, match='NOT_QUIESCED'):
        adopt_schema(binding, expected_logical_sha256=plan['logical_sha256'], backup_path=tmp_path / 'backup.db')
    assert inspect_schema_adoption(binding.engine)['status'] == 'EXACT_TARGET_ADDITIONS_REQUIRED'


def test_original_native_mode_cannot_create_security_state_without_explicit_permission(retained, monkeypatch):
    from modules.products import server
    from shared_platform.original_prepared_service import installed_original_service, SCOPE
    from shared_platform import local_operator_session as owner
    binding, _, tmp_path = retained
    path = tmp_path / 'public-startup-binding.json'
    pins = binding.pins
    config = {'schema':SCOPE,'task_id':pins.task_id,'offer_id':pins.offer_id,'original_worker':pins.original_worker,
        'prepared_reference':pins.prepared_reference,'r2_identity':json.loads(pins.r2_identity_json),
        'quiescence_event_id':pins.quiescence_event_id,'quiescence_detail_sha256':pins.quiescence_detail_sha256,
        'roles':[{'name':role.name,'root':str(role.profile.root),'data_root':str(role.profile.data_root),
            'environment':role.profile.environment,'head':role.profile.version,'manifest_digest':role.profile.manifest_digest} for role in pins.roles],
        'documents':[{'path':str(file.path),'sha256':file.sha256} for file in pins.documents],
        'original_state':{'path':str(pins.original_state.path),'sha256':pins.original_state.sha256},
        'target_request_digest':'1'*64,'target_reduction_digest':'2'*64}
    path.write_text(json.dumps(config))
    catalog_path=catalog(tmp_path/'catalog.db')
    paths={'operations_data_root':binding.engine.store.path.parent,'release_store_path':binding.release_store.path,'catalog_database':catalog_path}
    monkeypatch.setattr(owner,'current_windows_owner_sid',lambda:pytest.fail('owner lookup must not precede permission'))
    with pytest.raises(ValueError,match='EPHEMERAL_STATE_CREATION_NOT_AUTHORIZED'):
        with installed_original_service(server,SimpleNamespace(),{'original_continuation':{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},
            'native_ephemeral_state_creation_authorized':False},paths):pytest.fail('native mode must not start')
    assert not (paths['operations_data_root']/'native-final-owner').exists()
