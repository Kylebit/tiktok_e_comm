"""U00: real handlers, generated config only, no business/provider I/O."""
from __future__ import annotations

import http.client
import importlib
import json
from pathlib import Path
import sqlite3
import socket
from copy import deepcopy
from threading import Thread
from http.server import ThreadingHTTPServer
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def entry_http(tmp_path, monkeypatch):
    import core.config as config
    blocked = []

    def forbidden(*args, **kwargs):
        blocked.append('configuration initialization / database / provider')
        raise AssertionError(blocked[-1])

    monkeypatch.setattr(config, 'load_settings', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    settings = tmp_path / 'config' / 'settings.json'
    settings.parent.mkdir()
    settings.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(config, 'CONFIG_PATH', settings)
    monkeypatch.setattr(config, 'FALLBACK_CONFIG_PATHS', [])
    ozon = tmp_path / 'ozon'
    ozon.mkdir()
    monkeypatch.setattr(config, '_cache', {'ozon': {'data_dir': str(ozon)}})
    monkeypatch.setattr(config, '_cache_source', settings)
    monkeypatch.setattr(config, '_cache_source_stat', (settings.stat().st_size, settings.stat().st_mtime_ns))
    from shared_platform import report_store, release_store, workbench_store
    monkeypatch.setattr(report_store, 'DEFAULT_REPORT_STORE_PATH', tmp_path / 'data/orbit_platform.db')
    monkeypatch.setattr(release_store, 'DEFAULT_RELEASE_STORE_PATH', tmp_path / 'data/orbit_platform.db')
    monkeypatch.setattr(workbench_store, 'DEFAULT_WORKBENCH_STORE_PATH', tmp_path / 'data/orbit_workbench.db')
    monkeypatch.setattr(workbench_store.WorkbenchStore.__init__, '__defaults__', (tmp_path / 'data/orbit_workbench.db',))
    profile = tmp_path / 'runtime-profile.json'
    stores = {
        'catalog': str((tmp_path / 'data/shop.db').resolve()),
        'reports_release': str((tmp_path / 'data/orbit_platform.db').resolve()),
        'workbench': str((tmp_path / 'data/orbit_workbench.db').resolve()),
        'ozon': str(ozon.resolve()),
    }
    profile.write_text(json.dumps({'profile_id': 'u00-isolated-fixture', 'settings_path': str(settings), 'stores': stores}), encoding='utf-8')
    monkeypatch.setenv('ORBIT_RUNTIME_PROFILE', str(profile))
    module = importlib.import_module('modules.products.server')
    from shared_platform.runtime_identity import capture_runtime_identity
    monkeypatch.setattr(module, 'RUNTIME_IDENTITY', capture_runtime_identity('orbit-hive-local-console', root=ROOT, web_root=ROOT/'web'))
    assert Path(module.__file__).resolve().is_relative_to(ROOT)
    server = ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
    server.fixture_allowed_ports = {server.server_port}
    original_connect = socket.socket.connect
    def only_fixture(sock, address):
        if address[0] != '127.0.0.1' or address[1] not in server.fixture_allowed_ports:
            return forbidden()
        return original_connect(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', only_fixture)
    thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()

    def get(target):
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        try:
            conn.request('GET', target)
            response = conn.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            conn.close()
    try:
        yield get, server, profile
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert blocked == []


def test_registry_has_unique_capabilities_and_no_retired_notifications():
    from shared_platform.orbit_registry import navigation_payload
    payload = navigation_payload()
    assert payload.get('capabilities')
    assert {r['label'] for r in payload['regions']} == {'工作台', '商品发布', '内容', '店铺', '供应', '数据', '知识工具'}
    assert len({r['id'] for r in payload['capabilities']}) == len(payload['capabilities'])
    assert 'CAP-00-NOTIFY' not in {r['id'] for r in payload['capabilities']}
    assert len(payload['tools']) >= 9


def test_supply_real_handler_serves_snapshot_and_relative_assets(entry_http):
    get, _, _ = entry_http
    status, headers, body = get('/supply-chain/')
    assert status == 200
    assert body == (ROOT / 'domains/supply_chain_operations/dashboard/index.html').read_bytes()
    assert "frame-ancestors 'none'" in headers['Content-Security-Policy']
    for name in ('app.js', 'data.js', 'styles.css', 'inbound-batches.html'):
        assert get('/supply-chain/' + name)[0] == 200
    for name in ('../README.md', '%2e%2e/README.md', 'README.md', 'assets/../../transport_history.py'):
        assert get('/supply-chain/' + name)[0] == 404


def test_old_name_only_health_is_never_ready():
    from scripts.product_publication_runtime import service_specs, probe_service
    spec = service_specs()[0]
    result = probe_service(spec, fetch_json=lambda *_: {'ok': True, 'service': spec.expected_service}, port_check=lambda *_: True)
    assert result['state'] not in {'READY', 'HEALTHY'}


def test_current_c2_capabilities_keep_retirement_and_honest_tool_states():
    from shared_platform.orbit_registry import navigation_payload
    payload = navigation_payload()
    caps = {row['id']: row for row in payload['capabilities']}
    assert '旧批量写入口已退役' in caps['CAP-03-PUBLISH']['description']
    assert 'CAP-00-NOTIFY' not in caps
    assert caps['CAP-05-ADS']['entry_available'] is False
    assert caps['CAP-01-SHADOW']['entry_available'] is False
    assert 'CAP-03-AFFILIATE' not in caps
    assert all(row['business_verified'] is False for row in caps.values())
    assert {'tikhub', 'duoplus', 'minimax-h3', 'prepare-product-publication', 'prepare-product-images', 'publish-approved-product'} <= {row['id'] for row in payload['tools']}
    assert all(row.get('account_verified') is False for row in payload['tools'])


@pytest.mark.parametrize('route', ['/product-workspace', '/new-product', '/new-product.html', '/product-workspace.html', '/catalog', '/sourcing', '/settlement', '/billing', '/sku-profit', '/analytics', '/internal/release'])
def test_retained_real_page_routes(entry_http, route):
    get, _, _ = entry_http
    status, headers, body = get(route)
    assert status == 200
    assert body.strip()
    assert headers['X-Content-Type-Options'] == 'nosniff'


def test_profit_entry_links_to_retained_july_report(entry_http):
    get, _, _ = entry_http
    status, _, body = get('/profit')
    assert status == 200
    assert b'/static/profit_hub.js' in body
    assert b'/profit-original/artifacts/profit_reports_monthly/2026-07/index.html' in body


@pytest.mark.parametrize('route', ['/', '/index.html'])
def test_home_is_task_workbench(entry_http, route):
    get, _, _ = entry_http
    status, _, body = get(route)
    assert status == 200
    assert '任务工作台'.encode() in body


@pytest.mark.parametrize('route', ['/workbench', '/workbench.html'])
def test_retired_workbench_page_redirects_to_current_task_home(entry_http, route):
    get, _, _ = entry_http
    status, headers, body = get(route)
    assert status == 308 and headers['Location'] == '/'
    assert b'/api/workbench/' not in body
    status, _, home = get('/')
    assert status == 200 and '任务工作台'.encode() in home


def test_task_capability_points_to_current_home_and_publication_deeplink_stays_put(entry_http):
    from shared_platform.orbit_registry import navigation_payload

    caps = {row['id']: row for row in navigation_payload()['capabilities']}
    task = caps['CAP-00-TASKS']
    assert task['href'] == '/' and task['asset'] == 'web/task_workspace.html'
    get, _, _ = entry_http
    status, _, body = get('/product-workspace?offer_id=3956742887&round=final')
    assert status == 200 and body == (ROOT / 'web/product_workspace.html').read_bytes()


def test_legacy_cost_page_redirects_to_catalog(entry_http):
    get, _, _ = entry_http
    status, headers, _ = get('/costs')
    assert status == 308 and headers['Location'] == '/catalog'


@pytest.mark.parametrize('route', ['/ai-image-studio', '/ai-images', '/new-product/images', '/localized-image-review', '/titles', '/images', '/mx', '/uk', '/promotions', '/deactivate', '/ozon', '/rus'])
def test_retired_standalone_content_pages_are_unavailable(entry_http, route):
    get, _, _ = entry_http
    assert get(route)[0] == 404
    assert get(route + '.html')[0] == 404


def test_real_http_identity_compares_to_independent_profile_and_candidate(entry_http):
    get, server, profile = entry_http
    from scripts.product_publication_runtime import service_specs, probe_service
    status, _, raw = get('/api/health')
    payload = json.loads(raw)
    assert status == 200 and payload['state'] == 'READY'
    assert payload['configuration_initialized'] is True
    assert payload['business_execution_verified'] is False
    spec = service_specs(root=ROOT, profile_path=profile)[0]._replace(port=server.server_port, health_url=f'http://127.0.0.1:{server.server_port}/api/health')
    result = probe_service(spec)
    assert result['state'] == 'READY'
    assert result['identity']['stores'] == result['expected']['profile']['stores']


@pytest.mark.parametrize('module_name,handler_name,service', [('modules.sourcing.new_product_server','NewProductHandler','new_product'), ('modules.ozon.rus_server','OrbitRusHandler','orbit_rus')])
def test_other_real_handlers_emit_same_identity_contract(entry_http, module_name, handler_name, service):
    _, parent, profile = entry_http
    from scripts.product_publication_runtime import ServiceSpec, probe_service
    module = importlib.import_module(module_name)
    from shared_platform.runtime_identity import capture_runtime_identity
    module.RUNTIME_IDENTITY = capture_runtime_identity(service, root=ROOT, web_root=ROOT/'web')
    server = ThreadingHTTPServer(('127.0.0.1',0),getattr(module,handler_name))
    parent.fixture_allowed_ports.add(server.server_port)
    thread=Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True); thread.start()
    try:
        spec=ServiceSpec(service,server.server_port,f'http://127.0.0.1:{server.server_port}/health',service,(),str(ROOT),str(profile))
        assert probe_service(spec)['state']=='READY'
        if service == 'orbit_rus':
            for route in ('/', '/ozon', '/ozon.html', '/rus', '/rus.html'):
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                try:
                    conn.request('GET', route)
                    response = conn.getresponse()
                    assert response.status == 404
                    response.read()
                finally:
                    conn.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


@pytest.mark.parametrize('field,value,state', [
    ('service','wrong','WRONG_SERVICE'), ('contract_version',None,'UNKNOWN'),
    ('code_root','C:/u00-wrong-root','SOURCE_MISMATCH'), ('commit','0'*40,'SOURCE_MISMATCH'),
    ('build',{'digest':'wrong','missing':[]},'SOURCE_MISMATCH'),
    ('assets',{'digest':'wrong','missing':[]},'ASSET_MISMATCH'),
    ('assets',{'digest':'wrong','missing':['fixture.js']},'ASSET_MISSING'),
    ('profile',{'profile_id':'wrong','settings_path':'wrong'},'CONFIG_MISMATCH'),
    ('stores',{},'DATA_PROFILE_MISMATCH'), ('configuration_initialized',False,'UNKNOWN'),
    ('missing_dependencies',['fixture-package'],'DEPENDENCY_UNAVAILABLE'),
    ('build',{},'UNKNOWN'), ('profile',None,'UNKNOWN'),
])
def test_identity_negative_matrix_never_allows_start(entry_http, field, value, state):
    get, _, profile = entry_http
    from scripts.product_publication_runtime import service_specs, probe_service, start_runtime
    payload=json.loads(get('/api/health')[2]); payload[field]=value
    spec=service_specs(root=ROOT,profile_path=profile)[0]
    def probe(item): return probe_service(item,fetch_json=lambda *_:deepcopy(payload),port_check=lambda *_:True)
    assert probe(spec)['state']==state
    launched=[]
    result=start_runtime(specs=(spec,),probe=probe,launcher=lambda item:launched.append(item),timeout_seconds=0)
    assert not result['ok'] and launched==[]


def test_runtime_does_not_initialize_missing_config_and_detects_fallback(entry_http, monkeypatch):
    import core.config as config
    get, _, _=entry_http
    monkeypatch.setattr(config,'_cache',None)
    assert json.loads(get('/api/health')[2])['state']=='UNKNOWN'
    monkeypatch.setattr(config,'settings_path',lambda:Path('C:/u00-generated-fallback/settings.json'))
    assert json.loads(get('/api/health')[2])['state']=='CONFIG_MISMATCH'


def test_unattributed_cache_stays_unknown(entry_http, monkeypatch):
    import core.config as config
    get, _, _=entry_http
    monkeypatch.setattr(config,'_cache_source',None)
    monkeypatch.setattr(config,'_cache_source_stat',None)
    assert json.loads(get('/api/health')[2])['state']=='UNKNOWN'


def test_existing_record_collections_do_not_create_missing_store(entry_http):
    get, _, profile=entry_http
    store=Path(json.loads(profile.read_text(encoding='utf-8'))['stores']['reports_release'])
    assert not store.exists()
    assert get('/api/orbit/inbox?limit=50')[0] == 404
    for path in ('/api/orbit/report-runs?limit=50',):
        status, _, raw=get(path)
        assert status==200 and json.loads(raw)['items']==[]
    assert not store.exists()


def test_real_occupied_port_cannot_trigger_replacement(entry_http):
    _, parent, profile=entry_http
    from http.server import BaseHTTPRequestHandler
    from scripts.product_publication_runtime import service_specs, probe_service, start_runtime
    class Unrelated(BaseHTTPRequestHandler):
        def do_GET(self): self.send_error(503)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Unrelated)
    parent.fixture_allowed_ports.add(server.server_port)
    thread=Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);thread.start()
    try:
        spec=service_specs(profile_path=profile)[0]._replace(port=server.server_port,health_url=f'http://127.0.0.1:{server.server_port}/api/health')
        assert probe_service(spec)['state']=='PORT_IN_USE'
        launched=[]
        result=start_runtime(specs=(spec,),launcher=lambda item:launched.append(item),timeout_seconds=0)
        assert not result['ok'] and launched==[] and thread.is_alive()
    finally:server.shutdown();server.server_close();thread.join(timeout=5)


def test_outside_static_symlink_is_not_hashed_or_reported_ready(entry_http, tmp_path, monkeypatch):
    from shared_platform import runtime_identity as identity
    get, _, _=entry_http
    tree=tmp_path/'code';tree.mkdir()
    for name in identity.ENTRY_FILES + identity.ASSET_FILES:
        target=tree/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('generated fixture',encoding='utf-8')
    (tree/'shared_platform/entry_catalog.json').write_text('{"supply_images":[]}',encoding='utf-8')
    sentinel=tmp_path/'outside.js';sentinel.write_text('generated outside sentinel',encoding='utf-8')
    asset=tree/'web/static/orbit_product.js';asset.unlink();asset.symlink_to(sentinel)
    monkeypatch.setattr(identity,'_commit',lambda *_:'a'*40)
    module=importlib.import_module('modules.products.server')
    monkeypatch.setattr(module,'WEB_DIR',tree/'web')
    outside_reads=[];original=Path.read_bytes
    def tracked_read(path):
        if path.resolve()==sentinel:outside_reads.append(str(path))
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',tracked_read)
    startup=identity.capture_runtime_identity('orbit-hive-local-console',root=tree)
    actual=identity.health_payload('orbit-hive-local-console',root=tree,startup=startup)
    assert get('/static/orbit_product.js')[0]==404
    assert actual['state']!='READY'
    assert outside_reads==[]


def test_identity_hashes_only_explicit_files_and_unchanged_files_use_cache(entry_http, monkeypatch):
    from shared_platform import runtime_identity as identity
    get,_,_=entry_http
    get('/api/health')
    before=identity._file_hash.cache_info()
    def no_recursive_scan(*args,**kwargs): raise AssertionError('recursive health hashing forbidden')
    monkeypatch.setattr(Path,'rglob',no_recursive_scan)
    get('/api/health')
    after=identity._file_hash.cache_info()
    assert before.misses==after.misses and after.hits>before.hits


@pytest.mark.parametrize('before_first_health', [True, False])
def test_running_source_identity_cannot_adopt_changed_disk(entry_http, tmp_path, monkeypatch, before_first_health):
    from shared_platform import runtime_identity as identity
    _, _, profile = entry_http
    tree=tmp_path/'code';tree.mkdir()
    for name in identity.ENTRY_FILES + identity.ASSET_FILES:
        target=tree/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('generated fixture',encoding='utf-8')
    (tree/'shared_platform/entry_catalog.json').write_text('{"supply_images":[]}',encoding='utf-8')
    monkeypatch.setattr(identity,'_commit',lambda *_:'a'*40)
    startup = identity.capture_runtime_identity('orbit-hive-local-console',root=tree) if hasattr(identity,'capture_runtime_identity') else None
    def health():
        return identity.health_payload('orbit-hive-local-console',root=tree,**({'startup':startup} if startup else {}))
    if not before_first_health: assert health()['state']=='READY'
    (tree/'main.py').write_text('generated changed implementation',encoding='utf-8')
    actual=health()
    expected=identity.expected_identity(tree,'orbit-hive-local-console',profile_path=profile)
    assert identity.compare_identity(actual,expected)=='SOURCE_MISMATCH'


def test_old_config_cache_cannot_be_relabelled_with_new_settings_path(entry_http, tmp_path, monkeypatch):
    import core.config as config
    from shared_platform import runtime_identity as identity
    _, _, profile = entry_http
    original=config.CONFIG_PATH
    # A source annotation must come from load_settings; this fixture is synthetic.
    monkeypatch.setattr(config,'_cache_source',original,raising=False)
    monkeypatch.setattr(config,'_cache_source_stat',(original.stat().st_size,original.stat().st_mtime_ns),raising=False)
    startup=identity.capture_runtime_identity('orbit-hive-local-console',root=ROOT) if hasattr(identity,'capture_runtime_identity') else None
    replacement=tmp_path/'config-b/settings.json';replacement.parent.mkdir();replacement.write_text('{}',encoding='utf-8')
    monkeypatch.setattr(config,'CONFIG_PATH',replacement)
    data=json.loads(profile.read_text(encoding='utf-8'));data['settings_path']=str(replacement)
    data['stores']['catalog']=str(replacement.parent/'data/shop.db')
    profile.write_text(json.dumps(data),encoding='utf-8')
    actual=identity.health_payload('orbit-hive-local-console',root=ROOT,**({'startup':startup} if startup else {}))
    assert actual['state']=='CONFIG_MISMATCH'


def test_health_consumes_all_explicit_store_pins_and_fails_closed_on_drift(entry_http, tmp_path, monkeypatch):
    from shared_platform import runtime_identity as identity
    from shared_platform import report_store, release_store, workbench_store

    _, _, profile_path = entry_http
    profile = json.loads(profile_path.read_text(encoding='utf-8'))
    stores = {
        'catalog': str((tmp_path / 'catalog.db').resolve()),
        'report': str((tmp_path / 'report.db').resolve()),
        'release': str((tmp_path / 'release.db').resolve()),
        'workbench': str((tmp_path / 'workbench.db').resolve()),
        'ozon': str((tmp_path / 'ozon-pinned').resolve()),
    }
    profile['stores'] = stores
    profile_path.write_text(json.dumps(profile), encoding='utf-8')
    for name, value in {
        'ORBIT_CATALOG_DATABASE': stores['catalog'],
        'ORBIT_REPORT_STORE_PATH': stores['report'],
        'ORBIT_RELEASE_STORE_PATH': stores['release'],
        'ORBIT_WORKBENCH_STORE_PATH': stores['workbench'],
        'ORBIT_OZON_DATA_ROOT': stores['ozon'],
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(report_store, 'DEFAULT_REPORT_STORE_PATH', Path(stores['report']))
    monkeypatch.setattr(release_store, 'DEFAULT_RELEASE_STORE_PATH', Path(stores['release']))
    monkeypatch.setattr(workbench_store.WorkbenchStore.__init__, '__defaults__', (Path(stores['workbench']),))
    startup = identity.capture_runtime_identity(
        'orbit-hive-local-console', root=ROOT, web_root=ROOT / 'web'
    )
    healthy = identity.health_payload(
        'orbit-hive-local-console', root=ROOT, web_root=ROOT / 'web', startup=startup
    )
    assert healthy['state'] == 'READY'
    assert healthy['stores'] == stores

    monkeypatch.setenv('ORBIT_CATALOG_DATABASE', str((tmp_path / 'wrong.db').resolve()))
    drifted = identity.health_payload(
        'orbit-hive-local-console', root=ROOT, web_root=ROOT / 'web', startup=startup
    )
    assert drifted['state'] == 'DATA_PROFILE_MISMATCH'
