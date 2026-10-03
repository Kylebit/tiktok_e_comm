from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import sqlite3
import pytest

from shared_platform.operations_runtime import RuntimeProfile
from shared_platform.workbench_engine import WorkbenchEngine


_RUNTIME_ENVIRONMENT_NAMES = (
    'ORBIT_RUNTIME_PROFILE','ORBIT_PUBLICATION_REQUIRE_VERIFIED_RUNTIME','ORBIT_CATALOG_DATABASE',
    'ORBIT_REPORT_STORE_PATH','ORBIT_RELEASE_STORE_PATH','ORBIT_WORKBENCH_STORE_PATH',
    'ORBIT_OZON_DATA_ROOT','ORBIT_OPERATIONS_ENV','ORBIT_HIVE_SETTINGS','ORBIT_R3_CONFIG_ROOT',
    'ORBIT_OPERATIONS_AGENT_EXECUTABLE','TIKTOK_ECOMM_HOME','ORBIT_OPERATIONS_CONFIG_ROOT',
    'ORBIT_R2_REVIEW_RUNTIME_ROOT','ORBIT_RELEASE_EVIDENCE_ROOT',
    'ORBIT_RELEASE_SOURCE_IDENTITY_PATH','ORBIT_RELEASE_SOURCE_IDENTITY_SHA256',
    'ORBIT_RELEASE_SOURCE_IDENTITY_OFFER_ID',
)


@pytest.fixture(autouse=True)
def isolated_runtime_environment(tmp_path, monkeypatch):
    original = {name: os.environ[name] for name in _RUNTIME_ENVIRONMENT_NAMES if name in os.environ}
    monkeypatch.setenv('ORBIT_OPERATIONS_DATA_ROOT', str(tmp_path/'operations'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'no-shared-profile'))
    monkeypatch.setenv('ORBIT_SHOPEE_RECOVERY_ENABLED', '0')
    for name in _RUNTIME_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)
    try:
        yield
    finally:
        # The launcher intentionally configures the process via direct
        # ``os.environ`` writes.  Keys created after ``delenv`` cannot be
        # restored by monkeypatch when they did not exist at fixture setup.
        # Restore this test module's complete process boundary explicitly so
        # later HTTP tests do not inherit the stable-runtime publication gate.
        for name in _RUNTIME_ENVIRONMENT_NAMES:
            if name in original:
                os.environ[name] = original[name]
            else:
                os.environ.pop(name, None)


def test_web_runtime_defaults_paused_without_agent_and_preserves_queue(tmp_path, monkeypatch):
    from shared_platform import operations_service as service
    profile = RuntimeProfile(tmp_path, tmp_path, 'stable', 'a' * 40)
    monkeypatch.setattr(service.RuntimeProfile, 'capture', lambda root: profile)
    monkeypatch.setenv('ORBIT_OPERATIONS_AGENT_EXECUTABLE', str(tmp_path/'missing.exe'))
    monkeypatch.setattr(service.OperationsWorker, 'start', lambda self: pytest.fail('worker started'))
    monkeypatch.setattr(service, 'ControlledAgentBridge', lambda *args: pytest.fail('agent bridge initialized'))
    before = WorkbenchEngine(tmp_path/'tasks.db', {'code_version': 'a'*40})
    task = before.create({'template':'profit','scope':{'month':'2026-08'},'source_key':'existing'})
    runtime = service.get_runtime(SimpleNamespace(server_port=0), tmp_path)
    try:
        assert runtime.worker_enabled is False
        assert not runtime.worker.thread.is_alive()
        assert runtime.engine.get(task['task_id'])['execution_state'] == task['execution_state']
        assert runtime.worker.adapters == {}
        with sqlite3.connect(tmp_path / 'tasks.db') as conn:
            assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name='workbench_worker_admissions'").fetchone() is None
    finally:
        runtime.worker.close()


def test_execute_requires_explicit_existing_agent_before_engine_creation(tmp_path, monkeypatch):
    from shared_platform import operations_service as service
    monkeypatch.delenv('ORBIT_OPERATIONS_AGENT_EXECUTABLE', raising=False)
    monkeypatch.setattr(service.RuntimeProfile, 'capture', lambda root: RuntimeProfile(tmp_path,tmp_path,'stable','a'*40))
    with pytest.raises(ValueError, match='explicit absolute'):
        service.get_runtime(SimpleNamespace(server_port=0), tmp_path, worker_enabled=True)
    assert not (tmp_path/'tasks.db').exists()


def test_worker_composition_does_not_admit_preexisting_queue_without_exact_resume(tmp_path, monkeypatch):
    from shared_platform import operations_service as service

    profile = RuntimeProfile(tmp_path,tmp_path,'stable','a'*40)
    monkeypatch.setattr(service.RuntimeProfile,'capture',lambda root: profile)
    agent = tmp_path/'codex.exe';agent.write_bytes(b'synthetic-not-executed')
    monkeypatch.setenv('ORBIT_OPERATIONS_AGENT_EXECUTABLE',str(agent))
    monkeypatch.setattr(service.OperationsWorker,'start',lambda self: None)
    existing = WorkbenchEngine(tmp_path/'tasks.db',{'code_version':'a'*40,'environment':'stable','manifest_digest':''})
    old = existing.create({'template':'profit','scope':{'month':'2026-08'},'source_key':'old-profit'})
    server = SimpleNamespace(server_port=0)
    runtime = service.get_runtime(server,tmp_path,worker_enabled=True)
    try:
        assert runtime.worker_enabled is True
        assert set(runtime.worker.adapters)=={'publication','delisting','profit'}
        assert not runtime.worker._admitted(runtime.engine.get(old['task_id']))
        new = runtime.engine.create({'template':'profit','scope':{'month':'2026-09'},'source_key':'new-profit'})
        assert runtime.worker._admitted(runtime.engine.get(new['task_id']))
    finally:runtime.worker.close()
    resumed = service.get_runtime(SimpleNamespace(server_port=0),tmp_path,worker_enabled=True,
                                  resume_task_ids=(old['task_id'],))
    try:
        assert resumed.worker._admitted(resumed.engine.get(old['task_id']))
    finally:resumed.worker.close()
    with pytest.raises(KeyError):
        service.get_runtime(SimpleNamespace(server_port=0),tmp_path,worker_enabled=True,
                            resume_task_ids=('TASK-nonexistent',))


def test_worker_restart_keeps_first_admission_boundary_for_same_release(tmp_path, monkeypatch):
    from shared_platform import operations_service as service

    profile = RuntimeProfile(tmp_path, tmp_path, 'stable', 'a' * 40)
    monkeypatch.setattr(service.RuntimeProfile, 'capture', lambda root: profile)
    agent = tmp_path / 'codex.exe'
    agent.write_bytes(b'synthetic-not-executed')
    monkeypatch.setenv('ORBIT_OPERATIONS_AGENT_EXECUTABLE', str(agent))
    monkeypatch.setattr(service.OperationsWorker, 'start', lambda self: None)
    engine = WorkbenchEngine(tmp_path / 'tasks.db', {
        'code_version': profile.version, 'environment': profile.environment,
        'manifest_digest': profile.manifest_digest})
    old = engine.create({'template': 'profit', 'scope': {'month': '2026-07'},
                         'source_key': 'old-before-worker'})

    first = service.get_runtime(SimpleNamespace(server_port=0), tmp_path, worker_enabled=True)
    try:
        boundary = first.worker.admission_cutoff
        new = first.engine.create({'template': 'profit', 'scope': {'month': '2026-08'},
                                   'source_key': 'new-after-worker'})
        assert not first.worker._admitted(first.engine.get(old['task_id']))
        assert first.worker._admitted(first.engine.get(new['task_id']))
    finally:
        first.worker.close()

    restarted = service.get_runtime(SimpleNamespace(server_port=0), tmp_path, worker_enabled=True)
    try:
        assert restarted.worker.admission_cutoff == boundary
        assert not restarted.worker._admitted(restarted.engine.get(old['task_id']))
        assert restarted.worker._admitted(restarted.engine.get(new['task_id']))
    finally:
        restarted.worker.close()


def test_worker_admission_boundary_is_release_scoped_and_atomic(tmp_path, monkeypatch):
    path = tmp_path / 'tasks.db'
    first_release = {'code_version': 'a' * 40, 'environment': 'stable', 'manifest_digest': 'one'}
    other_release = {'code_version': 'b' * 40, 'environment': 'stable', 'manifest_digest': 'two'}
    first = WorkbenchEngine(path, first_release)
    first.create({'template': 'profit', 'scope': {'month': '2026-07'}, 'source_key': 'old'})

    def activate(_):
        return WorkbenchEngine(path, dict(reversed(list(first_release.items())))).worker_admission_cutoff()

    with ThreadPoolExecutor(max_workers=4) as pool:
        cutoffs = list(pool.map(activate, range(8)))
    assert len(set(cutoffs)) == 1
    assert first.worker_admission_cutoff() == cutoffs[0]

    newer = WorkbenchEngine(path, other_release)
    monkeypatch.setattr('shared_platform.workbench_engine._now', lambda: '2030-01-01T00:00:00+00:00')
    assert newer.worker_admission_cutoff() == '2030-01-01T00:00:00+00:00'
    with first.transaction() as conn:
        assert conn.execute('SELECT COUNT(*) FROM workbench_worker_admissions').fetchone()[0] == 2


def test_invalid_exact_resume_does_not_pin_worker_admission(tmp_path, monkeypatch):
    from shared_platform import operations_service as service

    profile = RuntimeProfile(tmp_path, tmp_path, 'stable', 'a' * 40)
    monkeypatch.setattr(service.RuntimeProfile, 'capture', lambda root: profile)
    agent = tmp_path / 'codex.exe'
    agent.write_bytes(b'synthetic-not-executed')
    monkeypatch.setenv('ORBIT_OPERATIONS_AGENT_EXECUTABLE', str(agent))
    engine = WorkbenchEngine(tmp_path / 'tasks.db', {
        'code_version': profile.version, 'environment': profile.environment,
        'manifest_digest': profile.manifest_digest})
    engine.create({'template': 'profit', 'scope': {'month': '2026-07'},
                   'source_key': 'existing-before-worker'})

    with pytest.raises(KeyError):
        service.get_runtime(SimpleNamespace(server_port=0), tmp_path, worker_enabled=True,
                            resume_task_ids=('TASK-nonexistent',))
    with sqlite3.connect(tmp_path / 'tasks.db') as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='workbench_worker_admissions'").fetchone() is None


def test_web_write_allowlist_blocks_commerce_and_existing_task_actions():
    from shared_platform.operations_launch import local_post_allowed
    assert local_post_allowed('/api/catalog/cost')
    assert local_post_allowed('/api/orbit/tasks')
    for path in ['/api/product-workspace/publish','/api/orbit/tasks/x/retry',
                 '/api/orbit/tasks/x/provide-input','/api/orbit/tasks/x/cancel',
                 '/api/catalog/sync','/api/ozon/migrate','/api/catalog/cost/extra']:
        assert not local_post_allowed(path)


def test_preview_handler_rejects_every_post_before_base_handler():
    from http.client import HTTPConnection
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from shared_platform.operations_launch import maintenance_handler

    class Base(BaseHTTPRequestHandler):
        def _json(self, status, body):
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            pytest.fail('preview delegated a write to the base handler')

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(('127.0.0.1', 0), maintenance_handler(Base, allow_local_posts=False))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        for route in ('/api/catalog/cost', '/api/orbit/tasks', '/api/product-workspace/publish'):
            connection = HTTPConnection('127.0.0.1', http.server_port)
            connection.request('POST', route, '{}', {'Content-Type': 'application/json'})
            response = connection.getresponse()
            body = json.loads(response.read())
            connection.close()
            assert response.status == 409
            assert body['code'] == 'WEB_ONLY_EXECUTION_PAUSED'
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


@pytest.mark.parametrize('legacy_store_exists', [False, True])
def test_maintenance_legacy_workbench_api_never_opens_or_creates_old_store(tmp_path, monkeypatch, legacy_store_exists):
    from http.client import HTTPConnection
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from shared_platform import workbench_store
    from shared_platform.operations_launch import maintenance_handler

    old_store = tmp_path / 'old-workbench.db'
    if legacy_store_exists:
        old_store.write_bytes(b'legacy-store-unchanged')
    original = old_store.read_bytes() if legacy_store_exists else None
    monkeypatch.setattr(workbench_store, 'default_workbench_store',
                        lambda: pytest.fail('retired API opened legacy store'))

    class Base(BaseHTTPRequestHandler):
        def _json(self, status, body):
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            pytest.fail('retired API delegated GET to legacy handler')

        def do_POST(self):
            pytest.fail('retired API delegated POST to legacy handler')

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(('127.0.0.1', 0), maintenance_handler(Base))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        for method, expected in [('GET', 503), ('POST', 409)]:
            conn = HTTPConnection('127.0.0.1', http.server_port, timeout=5)
            conn.request(method, '/api/workbench/tasks', '{}' if method == 'POST' else None)
            response = conn.getresponse()
            assert response.status == expected
            response.read()
            conn.close()
        assert (old_store.read_bytes() if old_store.exists() else None) == original
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def test_launcher_rejects_wrong_head_before_touching_store(tmp_path, monkeypatch):
    from shared_platform.operations_launch import preflight_deployment
    monkeypatch.setattr('subprocess.check_output', lambda *a, **k: 'a'*40+'\n')
    with pytest.raises(ValueError, match='HEAD'):
        preflight_deployment({'code_root':str(tmp_path),'code_version':'b'*40}, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_missing_operations_ledger_is_not_created(tmp_path, monkeypatch):
    from shared_platform.operations_launch import preflight_deployment
    monkeypatch.setattr('subprocess.check_output', lambda args, **kwargs: '' if 'status' in args else 'a'*40+'\n')
    config = {'code_root': str(tmp_path), 'code_version':'a'*40}
    for key in ('settings','catalog_database','catalog_weight_overrides','release_store_path',
                'report_store_path','operations_data_root','original_profit_asset_root','ozon_data_root'):
        config[key] = str(tmp_path)
    with pytest.raises(ValueError, match='existing operations tasks.db'):
        preflight_deployment(config,tmp_path)
    assert not (tmp_path/'tasks.db').exists()


def test_maintenance_http_blocks_direct_execution_and_allows_local_cost(tmp_path, monkeypatch):
    from http.client import HTTPConnection
    import sqlite3
    import threading
    from http.server import ThreadingHTTPServer
    from core import db
    from modules.products import server
    from shared_platform.operations_launch import maintenance_handler
    from shared_platform.catalog_sku_costs import grouped, state
    from shared_platform.catalog_images import ImageCache
    from shared_platform.catalog_cost_projection import digest
    from modules.catalog.sku_directory import list_skus
    path = tmp_path/'catalog.db'
    monkeypatch.setenv('ORBIT_CATALOG_DATABASE', str(path))
    monkeypatch.setattr(server,'_initialize_product_publication_platform_executors',lambda:pytest.fail('provider initialization'))
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO shops(cipher,region) VALUES('s','MY')")
        conn.execute("INSERT INTO products(sku_id,shop_cipher,product_id,seller_sku,product_name,image_url) VALUES('v','s','p','660001','Synthetic','https://example.invalid/image.png')")
        conn.commit()
    row = list_skus()['items'][0]
    http = ThreadingHTTPServer(('127.0.0.1',0),maintenance_handler(server.Handler))
    with db.connect_readonly() as conn:
        http.catalog_image_cache=ImageCache(conn,tmp_path/'image-cache')
    image_key=digest('https://example.invalid/image.png')
    (tmp_path/'image-cache'/(image_key+'.bin')).write_bytes(b'synthetic-cached-image')
    (tmp_path/'image-cache'/(image_key+'.json')).write_text(json.dumps({'mime':'image/png'}))
    monkeypatch.setattr(ImageCache,'_read',lambda *a:pytest.fail('external image read'))
    thread = threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    def post(route,body):
        c=HTTPConnection('127.0.0.1',http.server_port)
        c.request('POST',route,json.dumps(body),{'Content-Type':'application/json'})
        response=c.getresponse();result=(response.status,json.loads(response.read()));c.close();return result
    try:
        for route in ['/api/product-workspace/publish','/api/ozon/migrate','/api/orbit/tasks/old/retry']:
            assert post(route,{})[0] == 409
        status,result = post('/api/catalog/cost',{'entity_key':row['entity_key'],'revision':row['cost']['revision'],'cost_cny':'6'})
        assert status == 200 and result['amount'] == '6'
        assert list_skus()['items'][0]['cost']['amount'] == '6'
        status,result = post('/api/orbit/tasks',{'template':'profit','scope':{'month':'2026-08'},'source_key':'new-local'})
        assert status == 201
        assert http.operations_runtime.worker_enabled is False
        assert not http.operations_runtime.worker.thread.is_alive()
        runtime=http.operations_runtime
        runtime.engine.register_executor('stale-but-unexpired',['publication','profit','delisting'],runtime.engine.release)
        assert runtime.engine.dashboard()['executor']['connected'] is True
        for route in ['/api/orbit/tasks','/api/orbit/operations-runtime']:
            c=HTTPConnection('127.0.0.1',http.server_port);c.request('GET',route)
            response=c.getresponse();body=json.loads(response.read());c.close()
            assert response.status==200 and body['executor']['connected'] is False
            assert body['dispatcher']['state'] == 'stopped'
            assert body['dispatcher']['running'] is False
            assert all(t['executor_connected'] is False for t in body.get('tasks',[]))
        runtime.worker_enabled = True
        monkeypatch.setattr(runtime.worker, 'status', lambda: {'state': 'error', 'running': True,
            'last_error_type': 'OSError', 'last_error_at': '2026-09-22T08:00:00Z'})
        for route in ['/api/orbit/tasks','/api/orbit/operations-runtime']:
            c=HTTPConnection('127.0.0.1',http.server_port);c.request('GET',route)
            response=c.getresponse();body=json.loads(response.read());c.close()
            assert response.status == 200
            assert body['dispatcher']['state'] == 'error'
            assert body['executor']['connected'] is False
            assert all(t['executor_connected'] is False for t in body.get('tasks',[]))
        monkeypatch.setattr(runtime.worker, 'status', lambda: {'state': 'polling', 'running': True})
        for route in ['/api/orbit/tasks','/api/orbit/operations-runtime']:
            c=HTTPConnection('127.0.0.1',http.server_port);c.request('GET',route)
            response=c.getresponse();body=json.loads(response.read());c.close()
            assert response.status == 200
            assert body['executor']['connected'] is True
        runtime.worker_enabled = False
        c=HTTPConnection('127.0.0.1',http.server_port);c.request('GET','/api/catalog/image?key='+image_key)
        response=c.getresponse();body=response.read();c.close()
        assert response.status==200 and body==b'synthetic-cached-image'
    finally:
        http.shutdown();http.server_close();thread.join()
        if getattr(http,'operations_runtime',None):
            http.operations_runtime.worker.close()


@pytest.mark.parametrize('environment', ['stable', 'preview'])
def test_pinned_web_launcher_without_cli_does_not_compose_or_start_worker(tmp_path, monkeypatch, environment):
    import subprocess
    from http.server import ThreadingHTTPServer
    from core import config as settings
    from modules.catalog import weight_overrides
    from modules.products import server
    from shared_platform import original_profit_reports
    from shared_platform.operations_launch import serve
    from shared_platform.operations_runtime import OperationsWorker, runtime_manifest
    root=Path(__file__).resolve().parents[1]
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    check_output=subprocess.check_output
    monkeypatch.setattr(subprocess,'check_output',lambda args,**kw: '' if args[:2]==['git','status'] else check_output(args,**kw))
    for key in ('CONFIG_PATH','FALLBACK_CONFIG_PATHS','_cache','_cache_source','_cache_source_stat'):
        monkeypatch.setattr(settings,key,getattr(settings,key))
    monkeypatch.setattr(settings,'_cache',None)
    monkeypatch.setattr(weight_overrides,'PATH',weight_overrides.PATH)
    monkeypatch.setattr(original_profit_reports,'ASSET_ROOT',original_profit_reports.ASSET_ROOT)
    monkeypatch.setattr(server,'_initialize_product_publication_platform_executors',lambda:pytest.fail('provider initialized'))
    monkeypatch.setattr(OperationsWorker,'start',lambda self:pytest.fail('worker started'))
    served=[]
    monkeypatch.setattr(ThreadingHTTPServer,'serve_forever',lambda self:served.append(hasattr(self,'catalog_image_cache')))
    config={'code_root':str(root),'code_version':head,'manifest_digest':runtime_manifest(root),'port':0,
            'r3_config_root':str(tmp_path),'runtime_profile_path':str(tmp_path/'profile.json'),
            'runtime_profile_id':'synthetic-maintenance','ready_path':str(tmp_path/'ready.json'),
            'workbench_store_path':str(tmp_path/'absent-legacy.db'),'agent_executable':str(tmp_path/'absent.exe')}
    for key in ('settings','catalog_database','catalog_weight_overrides','release_store_path','report_store_path'):
        path=tmp_path/key;path.write_text('{}');config[key]=str(path)
    for key in ('operations_data_root','original_profit_asset_root','ozon_data_root'):
        path=tmp_path/key;path.mkdir();config[key]=str(path)
    if environment == 'preview':
        stable_root = tmp_path / 'stable-operations'
        stable_root.mkdir()
        config['deployment_environment'] = 'preview'
        config['stable_operations_data_root'] = str(stable_root)
    from core import db
    Path(config['catalog_database']).write_bytes(b'')
    monkeypatch.setenv('ORBIT_CATALOG_DATABASE',config['catalog_database'])
    db.init_db()
    WorkbenchEngine(Path(config['operations_data_root'])/'tasks.db',{'code_version':head}).create(
        {'template':'profit','scope':{'month':'2026-08'},'source_key':'startup-existing'})
    serve(config,root)
    ready=json.loads((tmp_path/'ready.json').read_text())
    assert ready['execution_mode']=='web-only' and ready['worker_enabled'] is False
    assert ready['environment'] == environment
    assert ready['allowed_local_posts'] == (['/api/catalog/cost', '/api/orbit/tasks'] if environment == 'stable' else [])
    assert ready['provider_executors_initialized'] is False
    assert served == [True]
    assert not (tmp_path/'absent-legacy.db').exists()
    if environment == 'preview':
        config['stable_operations_data_root'] = config['operations_data_root']
        with pytest.raises(ValueError, match='separate operations data root'):
            serve(config, root)
        assert served == [True]


def test_existing_review_paths_are_pinned_and_identity_drift_is_rejected(tmp_path, monkeypatch):
    import os
    from shared_platform.operations_launch import bind_review_paths
    identity=tmp_path/'identity.json';identity.write_text('{}')
    config={'r2_review_runtime_root':str(tmp_path),'release_evidence_root':str(tmp_path),
            'release_source_identity_path':str(identity),'release_source_identity_sha256':hashlib.sha256(identity.read_bytes()).hexdigest(),
            'release_evidence_offer_id':'synthetic'}
    with monkeypatch.context() as env:
        for name in ('ORBIT_R2_REVIEW_RUNTIME_ROOT', 'ORBIT_RELEASE_EVIDENCE_ROOT',
                     'ORBIT_RELEASE_SOURCE_IDENTITY_PATH', 'ORBIT_RELEASE_SOURCE_IDENTITY_SHA256',
                     'ORBIT_RELEASE_SOURCE_IDENTITY_OFFER_ID'):
            env.setenv(name, '')
        bind_review_paths(config)
        assert os.environ['ORBIT_R2_REVIEW_RUNTIME_ROOT']==str(tmp_path)
        assert os.environ['ORBIT_RELEASE_SOURCE_IDENTITY_PATH']==str(identity)
        identity.write_text('{"changed":true}')
        with pytest.raises(ValueError,match='identity digest'):
            bind_review_paths(config)
