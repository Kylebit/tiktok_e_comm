"""Pinned web-only recovery: local browsing and edits, no automatic execution."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from urllib.parse import urlsplit, parse_qs


def local_post_allowed(path):
    return path in {'/api/catalog/cost', '/api/orbit/tasks'}


def bind_review_paths(config):
    """Preserve existing review evidence locations, without composing executors."""
    for key, variable in {'r2_review_runtime_root': 'ORBIT_R2_REVIEW_RUNTIME_ROOT',
                          'release_evidence_root': 'ORBIT_RELEASE_EVIDENCE_ROOT'}.items():
        if config.get(key):
            path = Path(config[key])
            if not path.is_absolute():
                raise ValueError(key + ' must be absolute')
            os.environ[variable] = str(path.resolve(strict=True))
    if config.get('release_source_identity_path'):
        path = Path(config['release_source_identity_path'])
        if not path.is_absolute():
            raise ValueError('release source identity path must be absolute')
        path = path.resolve(strict=True)
        if hashlib.sha256(path.read_bytes()).hexdigest() != config['release_source_identity_sha256']:
            raise ValueError('release source identity digest differs from deployment')
        os.environ['ORBIT_RELEASE_SOURCE_IDENTITY_PATH'] = str(path)
        os.environ['ORBIT_RELEASE_SOURCE_IDENTITY_SHA256'] = config['release_source_identity_sha256']
        os.environ['ORBIT_RELEASE_SOURCE_IDENTITY_OFFER_ID'] = config['release_evidence_offer_id']


def preflight_operations_schema(path):
    """Inspect an existing operations ledger without running a schema initializer.

    A web-only task GET executes the engine schema inside a transaction.  If a
    newly deployed release adds objects, that GET would otherwise migrate the
    stable ledger merely because somebody opened the task page.  Migration is
    a separate deployment operation, never part of this read-only preflight.
    """
    from contextlib import closing
    from shared_platform.workbench_engine import SCHEMA
    from shared_platform.workbench_store import _SCHEMA

    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError('OPERATIONS_LEDGER_MISSING: existing tasks.db required')
    sidecars = [str(path) + suffix for suffix in ('-wal', '-shm') if Path(str(path) + suffix).exists()]
    if sidecars:
        raise ValueError('OPERATIONS_LEDGER_UNCHECKPOINTED: ' + json.dumps(sidecars))

    with closing(sqlite3.connect(':memory:')) as expected:
        expected.executescript(_SCHEMA)
        expected.executescript(SCHEMA)
        required = {(kind, name): sql for kind, name, sql in expected.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','index','trigger') "
            "AND name NOT LIKE 'sqlite_%'")}

    # Path.as_uri percent-encodes URI delimiters in a ledger name.  A raw '#'
    # would otherwise make SQLite inspect a neighboring database as a fragment.
    uri = path.as_uri() + '?mode=ro&immutable=1'
    with closing(sqlite3.connect(uri, uri=True)) as existing:
        existing.execute('PRAGMA query_only=ON')
        opened = next((row[2] for row in existing.execute('PRAGMA database_list')
                       if row[1] == 'main'), '')
        if not opened or not Path(opened).samefile(path):
            raise ValueError('OPERATIONS_LEDGER_IDENTITY_MISMATCH')
        if existing.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('OPERATIONS_LEDGER_INTEGRITY_FAILED')
        actual = {(kind, name): sql for kind, name, sql in existing.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','index','trigger') "
            "AND name NOT LIKE 'sqlite_%'")}

    missing = sorted([{'type': kind, 'name': name} for kind, name in required.keys() - actual.keys()],
                     key=lambda row: (row['type'], row['name']))
    changed = sorted([{'type': kind, 'name': name} for kind, name in required.keys() & actual.keys()
                      if required[kind, name] != actual[kind, name]], key=lambda row: (row['type'], row['name']))
    if missing or changed:
        raise ValueError('OPERATIONS_SCHEMA_MIGRATION_REQUIRED: ' + json.dumps(
            {'missing': missing, 'changed': changed}, ensure_ascii=False, sort_keys=True))
    return {'schema_objects': len(required), 'missing': [], 'changed': [], 'database_writes': 0}


def preflight_deployment(config, root):
    root = Path(root).resolve(strict=True)
    if Path(config['code_root']).resolve(strict=True) != root:
        raise ValueError('deployment code root differs from launcher')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    if head != config['code_version']:
        raise ValueError('deployment HEAD differs from candidate')
    if subprocess.check_output(['git', 'status', '--porcelain=v1', '-uall'], cwd=root, text=True).strip():
        raise ValueError('deployment candidate must be clean')
    paths = {}
    for key in ('settings', 'catalog_database', 'catalog_weight_overrides', 'release_store_path',
                'report_store_path', 'operations_data_root', 'original_profit_asset_root', 'ozon_data_root'):
        path = Path(config[key])
        if not path.is_absolute():
            raise ValueError(key + ' must be absolute')
        paths[key] = path.resolve(strict=True)
    if not (paths['operations_data_root'] / 'tasks.db').is_file():
        raise ValueError('existing operations tasks.db is required; recovery cannot create an empty ledger')
    preflight_operations_schema(paths['operations_data_root'] / 'tasks.db')
    workbench = Path(config['workbench_store_path'])
    if not workbench.is_absolute():
        raise ValueError('workbench_store_path must be absolute')
    if workbench.resolve() == (paths['operations_data_root']/'tasks.db').resolve():
        raise ValueError('legacy planning store must not alias operations ledger')
    return paths


def verify_workbench_store_pin(profile):
    """Reject a path change between early import-time binding and full profile setup."""
    from shared_platform.workbench_store import configured_workbench_store_path, default_workbench_store

    expected = Path(profile['stores']['workbench'])
    if (default_workbench_store().path != expected
            or configured_workbench_store_path() != expected):
        raise ValueError('WORKBENCH_STORE_PIN_DRIFT')


def maintenance_handler(base, *, allow_local_posts=True, native_service_scope=None):
    class MaintenanceHandler(base):
        _REJECTED_POST_MAX_BODY = 1_048_576
        _REJECTED_POST_BODY_TIMEOUT = 2.0

        def _reject_blocked_post(self):
            """Consume only a small, declared body before a terminal 409.

            Closing a Windows socket with an unread POST body can reset the
            connection before the client receives our rejection. Never parse
            or delegate this body, and never wait without a byte/time bound.
            """
            self.close_connection = True
            lengths = self.headers.get_all('Content-Length', [])
            if self.headers.get_all('Transfer-Encoding', []):
                return self._json(400, {'ok': False, 'code': 'UNSUPPORTED_TRANSFER_ENCODING'})
            if not lengths:
                return self._json(411, {'ok': False, 'code': 'CONTENT_LENGTH_REQUIRED'})
            if len(lengths) != 1:
                return self._json(400, {'ok': False, 'code': 'INVALID_CONTENT_LENGTH'})
            raw_length = lengths[0]
            if not raw_length or not raw_length.isascii() or not raw_length.isdigit():
                return self._json(400, {'ok': False, 'code': 'INVALID_CONTENT_LENGTH'})
            digits = raw_length.lstrip('0') or '0'
            maximum = str(self._REJECTED_POST_MAX_BODY)
            if len(digits) > len(maximum) or (len(digits) == len(maximum) and digits > maximum):
                return self._json(413, {'ok': False, 'code': 'REQUEST_BODY_TOO_LARGE'})
            remaining = int(digits)
            deadline = time.monotonic() + self._REJECTED_POST_BODY_TIMEOUT
            previous_timeout = self.connection.gettimeout()
            incomplete = False
            try:
                while remaining:
                    available = deadline - time.monotonic()
                    if available <= 0:
                        incomplete = True
                        break
                    self.connection.settimeout(min(0.25, available))
                    # read() waits to fill its request across many recv calls;
                    # read1() returns after at most one raw read so a slow
                    # trickle cannot keep resetting the per-recv timeout.
                    chunk = self.rfile.read1(min(65_536, remaining))
                    if time.monotonic() >= deadline:
                        incomplete = True
                        break
                    if not chunk:
                        incomplete = True
                        break
                    remaining -= len(chunk)
            except OSError:
                incomplete = True
            finally:
                self.connection.settimeout(previous_timeout)
            if incomplete:
                return self._json(408, {'ok': False, 'code': 'REQUEST_BODY_INCOMPLETE'})
            return self._json(409, {'ok': False, 'code': 'WEB_ONLY_EXECUTION_PAUSED',
                                    'error': '维护模式：后台执行和历史任务操作已暂停。'})

        def do_GET(self):
            if urlsplit(self.path).path in {'/api/product-workspace/history', '/api/product-workspace/evidence'}:
                from shared_platform.product_workspace_evidence import history as products, product_evidence
                from shared_platform.publication_history import _existing
                try:
                    root = _existing('ORBIT_R2_REVIEW_RUNTIME_ROOT', directory=True)
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                    listing = urlsplit(self.path).path.endswith('/history')
                    allowed = {'limit'} if listing else {'offer_id'}
                    if set(query) - allowed or any(len(values) != 1 for values in query.values()):
                        raise ValueError('ambiguous evidence identity')
                    payload = products(root=root, limit=int(query.get('limit', ['50'])[0])) if listing else product_evidence(query.get('offer_id', [''])[0], root=root)
                    return self._json(200, payload)
                except (ValueError, OSError):
                    return self._json(409, {'ok': False, 'error': '历史商品资料连接不可用；现有报告仍可单独查看。'})
            if urlsplit(self.path).path == '/api/product-workspace/publication-history':
                from shared_platform.publication_history import history
                try:
                    offer = parse_qs(urlsplit(self.path).query).get('offer_id', [''])[0]
                    return self._json(200, history(offer))
                except ValueError:
                    return self._json(400, {'ok': False, 'error': 'invalid offer_id'})
            if urlsplit(self.path).path.startswith('/api/workbench/'):
                return self._json(503, {'ok': False, 'error': '旧规划库未接入维护模式；请使用首页任务工作台。'})
            return super().do_GET()

        def do_POST(self):
            path = urlsplit(self.path).path
            from shared_platform.native_service_lifecycle import SCOPE, POST_PATHS
            native_path = native_service_scope == SCOPE and path in POST_PATHS
            if not allow_local_posts or not (local_post_allowed(path) or native_path):
                return self._reject_blocked_post()
            host = self.headers.get('Host', '')
            expected = '127.0.0.1:' + str(self.server.server_port)
            if host != expected or self.headers.get('Origin') not in (None, 'http://' + expected):
                return self._json(403, {'ok': False, 'error': '仅允许本机同源请求'})
            capability = getattr(self.server, 'native_service_capability', None)
            if (native_service_scope == SCOPE and path == '/api/orbit/tasks'
                    and capability and capability['new_task_readiness'] == 'BLOCKED'):
                return self._json(409, {'ok': False, 'error': capability['agent_readiness'],
                                        'external_writes_performed': []})
            return super().do_POST()
    return MaintenanceHandler


def serve(config, root):
    """Restore existing stores. The worker is intentionally not an option here."""
    from domains.supply_chain_operations.captured_serving import deployment_capture
    selected_capture = deployment_capture(config)
    from scripts.stable_runtime_bootstrap import prebind_workbench_store
    prebound_workbench = prebind_workbench_store(config)
    paths = preflight_deployment(config, root)
    root = Path(root).resolve()
    from shared_platform.operations_runtime import RuntimeProfile
    environment = config.get('deployment_environment', 'stable')
    from shared_platform.native_service_lifecycle import SCOPE, POST_PATHS, installed_native_services
    native_scope = config.get('native_service_scope')
    if native_scope is not None and (native_scope != SCOPE or environment != 'stable'):
        raise ValueError('NATIVE_SERVICE_DEPLOYMENT_SCOPE_INVALID')
    if environment not in {'stable', 'preview'}:
        raise ValueError('deployment_environment must be stable or preview')
    if environment == 'preview':
        stable_data_root = config.get('stable_operations_data_root')
        if not stable_data_root or not Path(stable_data_root).is_absolute():
            raise ValueError('preview requires absolute stable_operations_data_root')
        if paths['operations_data_root'] == Path(stable_data_root).resolve(strict=True):
            raise ValueError('preview must use a separate operations data root')
    os.environ['ORBIT_OPERATIONS_ENV'] = environment
    os.environ['ORBIT_OPERATIONS_DATA_ROOT'] = str(paths['operations_data_root'])
    os.environ['ORBIT_SHOPEE_RECOVERY_ENABLED'] = '0'
    os.environ.pop('ORBIT_OPERATIONS_AGENT_EXECUTABLE', None)
    agent_capability = 'NATIVE_FIXED_AGENT_EXECUTABLE_REQUIRED'
    if native_scope == SCOPE:
        executable = config.get('agent_executable')
        if isinstance(executable, str) and Path(executable).is_absolute() and Path(executable).is_file():
            supplied = Path(executable)
            info = supplied.lstat()
            if (info.st_nlink == 1 and not getattr(info, 'st_file_attributes', 0) & 0x400):
                os.environ['ORBIT_OPERATIONS_AGENT_EXECUTABLE'] = str(supplied.resolve(strict=True))
                agent_capability = None
    os.environ['ORBIT_HIVE_SETTINGS'] = str(paths['settings'])
    os.environ['ORBIT_R3_CONFIG_ROOT'] = str(config.get('publication_runtime_config_root') or config['r3_config_root'])
    os.environ['TIKTOK_ECOMM_HOME'] = str(config['r3_config_root'])
    os.environ['ORBIT_OPERATIONS_CONFIG_ROOT'] = str(paths['settings'].parent.parent)
    bind_review_paths(config)
    if config.get('publication_history_root'):
        history_root = Path(config['publication_history_root'])
        if not history_root.is_absolute() or not history_root.is_dir():
            raise ValueError('publication_history_root must be an existing absolute directory')
        os.environ['ORBIT_PUBLICATION_HISTORY_ROOT'] = str(history_root.resolve(strict=True))
    else:
        os.environ.pop('ORBIT_PUBLICATION_HISTORY_ROOT', None)
    profile = RuntimeProfile.capture(root)
    if profile.manifest_digest != config['manifest_digest']:
        raise ValueError('deployment source manifest differs from candidate')
    from scripts.stable_runtime_bootstrap import configure_stable_runtime
    configured_profile = configure_stable_runtime(config, expected_workbench_store_path=prebound_workbench)
    verify_workbench_store_pin(configured_profile)
    from core import config as settings
    settings.CONFIG_PATH = paths['settings']
    settings.FALLBACK_CONFIG_PATHS = []
    settings.load_settings()
    from modules.catalog import weight_overrides
    weight_overrides.PATH = paths['catalog_weight_overrides']
    from shared_platform import original_profit_reports
    original_profit_reports.ASSET_ROOT = paths['original_profit_asset_root']
    from modules.products import server
    from shared_platform.bounded_web_server import BoundedThreadingHTTPServer
    from shared_platform.operations_service import get_runtime
    if native_scope == SCOPE:
        ready = Path(config['ready_path'])
        if ready.exists(): ready.unlink()
    http = BoundedThreadingHTTPServer(('127.0.0.1', int(config['port'])),
                               maintenance_handler(server.Handler, allow_local_posts=environment == 'stable',
                                                   native_service_scope=native_scope))
    runtime = None
    previous_common_observer = server._COMMON_DETAIL_OBSERVER_FACTORY
    installed_common_observer = None
    try:
        http.daemon_threads = True
        http.supply_chain_capture = selected_capture
        from core import db as catalog_database
        from shared_platform.catalog_images import ImageCache
        connection = catalog_database.connect_readonly()
        try:
            http.catalog_image_cache = ImageCache(connection, paths['operations_data_root'] / 'image-cache')
        finally:
            connection.close()
        runtime = get_runtime(http, root)
        receipt = {**profile.public(), 'pid': os.getpid(), 'port': http.server_port,
               'code_root': str(root), 'execution_mode': 'web-only', 'worker_enabled': False,
               'provider_executors_initialized': False, 'catalog_database': str(paths['catalog_database']),
               'operations_ledger': str(paths['operations_data_root']/'tasks.db'),
               'legacy_workbench_available': False,
               'supply_chain_capture': http.supply_chain_capture,
               'allowed_local_posts': ['/api/catalog/cost', '/api/orbit/tasks'] if environment == 'stable' else []}
        def write_ready():
            ready = Path(config['ready_path'])
            ready.parent.mkdir(parents=True, exist_ok=True)
            ready.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        if native_scope == SCOPE:
            from contextlib import ExitStack
            from shared_platform.native_profit_preparation import NativeProfitServiceConfig
            runtime.native_profit_config = None
            from shared_platform.native_delisting_preparation import NativeDelistingServiceConfig
            runtime.native_delisting_config = None
            delisting_capability = 'NATIVE_DELIST_SERVICE_BINDING_REQUIRED'
            try:
                runtime.native_delisting_config = NativeDelistingServiceConfig.capture(
                    runtime.profile,paths['settings'].parent.parent)
                delisting_capability = 'EXACT_IDENTITY_AND_PROVIDER_NOT_VERIFIED'
            except (ValueError,OSError) as error:
                delisting_capability = str(error) if isinstance(error,ValueError) else 'NATIVE_DELIST_FIXED_BINDING_UNAVAILABLE'
            profit_capability = 'NATIVE_PROFIT_SERVICE_BINDING_REQUIRED'
            if agent_capability is None:
                try:
                    runtime.native_profit_config = NativeProfitServiceConfig.capture(
                        runtime.profile, paths['settings'].parent.parent, executable)
                    profit_capability = 'BOUND_INPUTS_AND_REPORTS_NOT_VERIFIED'
                except (ValueError, OSError) as error:
                    # Missing financial configuration does not prevent the
                    # read-only workbench or grant ambient execution authority.
                    profit_capability = str(error) if isinstance(error, ValueError) else 'NATIVE_PROFIT_FIXED_BINDING_UNAVAILABLE'
            with ExitStack() as stack:
                # Fatal registration errors unwind and close the listener;
                # missing task/model/history capabilities remain task-specific
                # blocks and do not prevent the registered read-only workbench.
                stack.enter_context(installed_native_services(server, http, runtime))
                http.native_service_capability = {'status': 'REGISTERED', 'scope': SCOPE,
                    'new_task_readiness': 'BLOCKED' if agent_capability else 'EXECUTABLE_PRESENT_UNVERIFIED',
                    'agent_readiness': agent_capability or 'FIXED_EXECUTABLE_PRESENT_UNVERIFIED',
                    'paid_image_readiness': 'UNVERIFIED_UNTIL_FIXED_HISTORY_MODEL_AND_BUDGET_CHECKS',
                    'profit_scope':'EXPLICIT_NEW_POST_PROFIT_READONLY',
                    'profit_readiness':profit_capability,
                    'delisting_scope':'EXPLICIT_NEW_POST_DELIST_EXACT_SCOPE',
                    'delisting_readiness':delisting_capability,
                    'historical_task_scan': False}
                receipt['allowed_local_posts'] += sorted(POST_PATHS)
                receipt['execution_mode'] = 'scoped-native'
                receipt['native_service'] = http.native_service_capability
                write_ready()
                http.serve_forever()
        else:
            installed_common_observer = server._install_service_common_detail_observer(
                server.publication_runtime_config.capture_startup_config(root=root))
            write_ready()
            http.serve_forever()
    finally:
        if server._COMMON_DETAIL_OBSERVER_FACTORY is installed_common_observer:
            server._COMMON_DETAIL_OBSERVER_FACTORY = previous_common_observer
        try:
            current = runtime if runtime is not None else getattr(http, 'operations_runtime', None)
            if current is not None: current.worker.close()
        finally:
            http.server_close()
