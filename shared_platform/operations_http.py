"""Same-origin local HTTP surface for the trusted operations engine."""
import ipaddress
import json
import hashlib
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit


def paused_projection(value):
    """Do not advertise stale executor leases; preserve durable task facts."""
    import copy
    value = copy.deepcopy(value)
    if 'executor' in value:
        value['executor'] = {**value['executor'], 'connected': False, 'templates': []}
    tasks = value.get('tasks', []) + ([value['task']] if 'task' in value else [])
    for task in tasks:
        task['executor_connected'] = False
    return value


def profit_unknown_projection(value, profile):
    """Add bounded read-only recovery hints; hide agent-authored checkpoint text."""
    from shared_platform.workbench_profit_adapter import inspect_unknown_attempt
    tasks = value.get('tasks', []) + ([value['task']] if 'task' in value else [])
    for task in tasks:
        checkpoint = task.get('checkpoint') or {}
        if (task.get('template') != 'profit' or task.get('current_step') != 'coverage'
                or not isinstance(checkpoint, dict)
                or not (checkpoint.get('agent_attempt_started') or checkpoint.get('agent_unknown'))):
            continue
        try:
            readback = inspect_unknown_attempt(task, profile)
            if readback['status'] != 'UNKNOWN':
                readback['attempt_output'] = None
                readback['session_id'] = None
            task['profit_unknown_readback'] = readback
        except (OSError, ValueError, TypeError):
            task['profit_unknown_readback'] = {
                'status': 'BLOCKED', 'session_readback': 'NOT_VERIFIED',
                'agent_result_file': None, 'automatic_resume_allowed': False,
            }
        task['checkpoint'] = {
            'agent_attempt_started': bool(checkpoint.get('agent_attempt_started')),
            'agent_unknown': bool(checkpoint.get('agent_unknown')),
        }
        if value.get('task') is task:
            for event in value.get('events', []):
                if event.get('event_type') == 'checkpoint_saved':
                    event['detail'] = {'profit_unknown_readback': 'see bounded projection'}
    return value


def handle(handler, *, method, runtime):
    path = urlsplit(handler.path).path
    if not path.startswith('/api/orbit/tasks') and path != '/api/orbit/operations-runtime':
        return False
    host = handler.headers.get('Host', '')
    try:
        local_host = urlsplit('http://' + host)
        valid_host = local_host.netloc == host and not any((local_host.username, local_host.password, local_host.path, local_host.query, local_host.fragment))
        trusted = valid_host and len(handler.headers.get_all('Host') or []) == 1 and len(handler.headers.get_all('Origin') or []) <= 1 and ipaddress.ip_address(handler.client_address[0]).is_loopback and local_host.hostname in {'127.0.0.1', 'localhost', '::1'} and local_host.port == handler.server.server_port
    except ValueError:
        trusted = False
    origin = handler.headers.get('Origin')
    if not trusted or (origin and origin != 'http://' + host):
        handler._json(403, {'error': '仅允许本机同源工作台请求'})
        return True
    if path == '/api/orbit/operations-runtime' and method == 'GET':
        value = runtime.profile.public()
        if runtime.profile.environment == 'preview':
            from shared_platform.workbench_engine import ReceiptSnapshotUnavailable
            try:
                dashboard = runtime.engine.read_runtime_status()
            except (ReceiptSnapshotUnavailable, OSError, KeyError, ValueError, sqlite3.Error):
                handler._json(503, {'ok': False, 'code': 'READONLY_RUNTIME_SNAPSHOT_UNAVAILABLE',
                                    'error': 'A stable read-only operations snapshot is unavailable.'})
                return True
        else:
            dashboard = runtime.engine.dashboard()
        executor = dashboard['executor']
        enabled = getattr(runtime, 'worker_enabled', False)
        dispatcher = runtime.worker.status()
        ready = enabled and dispatcher['running'] and dispatcher['state'] == 'polling'
        templates = executor.get('templates', []) if ready else []
        if not ready:
            executor = {**executor, 'templates': [], 'connected': False}
        value.update(executor=executor, business_execution_enabled=runtime.profile.environment == 'stable' and 'delisting' in templates,
                     capabilities={'publication': {'connected': 'publication' in templates, 'review': 'original_domain', 'image_cli_configured': enabled and bool(__import__('os').environ.get('ORBIT_OPERATIONS_AGENT_EXECUTABLE'))},
                                   'delisting': {'connected': 'delisting' in templates, 'writes': 'exact_user_scope_with_provider_readback'},
                                   'profit': {'connected': 'profit' in templates, 'completion': 'verified_monthly_source_bundle'}})
        value.update(worker_enabled=enabled, execution_mode='worker' if enabled else 'web-only',
                     domain_guard=dashboard['domain_guard'],
                     dispatcher=dispatcher,
                      maintenance_message='' if enabled else '后台执行已暂停；可查看已有任务和商品，历史任务不会自动继续。')
        from shared_platform.native_sole_final_service import NativeSoleFinalService
        native_final=getattr(handler.server,'native_final_review',None)
        native_installed=(type(native_final) is NativeSoleFinalService and
            native_final._new_decision_execution_enabled and not native_final._closed)
        value['native_decision_execution']={'installed':native_installed,
            'scope':'EXPLICIT_NATIVE_DECISION_ONLY','historical_task_scan':False,
            'current_source_recheck_required':True}
        preparation = getattr(runtime,'new_task_worker',None)
        value['new_task_preparation'] = (preparation.status() if preparation else {
            'running':False,'scope':'EXPLICIT_NEW_POST_PUBLICATION_ONLY',
            'historical_task_scan':False,'paid_image_executor_connected':False})
        if preparation and preparation.status()['running']:
            profit = value['new_task_preparation']
            value['capabilities']['profit'].update(
                connected=profit.get('profit_executor_connected') is True,
                scope=profit.get('profit_scope'), readiness=profit.get('profit_readiness'),
                historical_task_scan=False, automatic_unknown_replay=False)
            value['capabilities']['delisting'].update(
                connected=profit.get('delisting_executor_connected') is True,
                scope=profit.get('delisting_scope'), readiness=profit.get('delisting_readiness'),
                historical_task_scan=False, automatic_unknown_replay=False)
        if native_installed and not enabled:
            value['maintenance_message']='历史任务后台保持暂停；新原生最终审核决定自动重核并执行，未知结果不重复提交。'
        if preparation and preparation.status()['running'] and not enabled:
            value['maintenance_message']='历史任务后台保持暂停；本次新建商品任务自动准备，技术缺项在任务中显示。'
            if native_installed:
                value['maintenance_message']+='新最终审核决定自动重核并执行，未知结果不重复提交。'
        handler._json(200, value)
        return True
    parts = path.strip('/').split('/')
    if len(parts) == 5 and parts[:3] == ['api', 'orbit', 'tasks'] and parts[4] == 'delisting-receipt':
        if method != 'GET':
            handler._json(405, {'error': '回执只支持读取'})
            return True
        from shared_platform.workbench_engine import ReceiptSnapshotUnavailable
        try:
            from shared_platform.workbench_delisting_receipt import build_receipt, render_receipt, receipt_url
            from shared_platform.immutable_approval_files import require_local_path
            if urlsplit(handler.path).query or receipt_url(parts[3]) != path:
                raise ValueError()
            database = Path(runtime.engine.store.path).absolute()
            if database != Path(runtime.profile.data_root).absolute() / 'tasks.db':
                raise ValueError()
            require_local_path(database, root=runtime.profile.data_root)
            receipt = build_receipt(runtime.engine.read_delisting_receipt_inputs(parts[3]), runtime.profile)
            raw = render_receipt(receipt).encode('utf-8')
            handler.send_response(200)
            handler.send_header('Content-Type', 'text/html; charset=utf-8')
            handler.send_header('Content-Length', str(len(raw)))
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('X-Content-Type-Options', 'nosniff')
            handler.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; sandbox allow-top-navigation-by-user-activation")
            handler.end_headers()
            handler.wfile.write(raw)
        except ReceiptSnapshotUnavailable:
            handler._json(503, {'error': 'RECEIPT_SNAPSHOT_UNAVAILABLE',
                               'message': '回执证据暂不可读：数据库需要稳定快照。未更改日志、未查询平台、未执行下架。'})
        except (KeyError, ValueError, TypeError, OSError, sqlite3.DatabaseError):
            handler._json(404, {'error': '该任务没有可读取的下架回执'})
        return True
    if method == 'GET' and len(parts) == 6 and parts[:3] == ['api', 'orbit', 'tasks'] and parts[4] == 'artifacts':
        try:
            task = runtime.engine.get(parts[3])
            checkpoint = task.get('checkpoint') or {}
            name = parts[5]
            expected = checkpoint.get('artifact_files', {}).get(name)
            if not expected or not name.endswith('.html'):
                raise ValueError()
            directory = Path(checkpoint['artifact_dir']).resolve(strict=True)
            artifact = (directory / name).resolve(strict=True)
            if not directory.is_relative_to(runtime.profile.data_root) or artifact.parent != directory:
                raise ValueError()
            raw = artifact.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError()
            handler.send_response(200)
            handler.send_header('Content-Type', 'text/html; charset=utf-8')
            handler.send_header('Content-Length', str(len(raw)))
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('Content-Security-Policy', "default-src 'none'; img-src https: data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; sandbox allow-scripts allow-top-navigation-by-user-activation")
            handler.end_headers()
            handler.wfile.write(raw)
        except (KeyError, ValueError, OSError):
            handler._json(404, {'error': '该任务没有可验证的报告文件'})
        return True
    payload = None
    if method == 'POST':
        try:
            lengths = handler.headers.get_all('Content-Length') or []
            if len(lengths) != 1 or handler.headers.get('Transfer-Encoding') or handler.headers.get_content_type() != 'application/json':
                raise ValueError()
            size = int(lengths[0])
            if not 0 < size <= 65536:
                raise ValueError()
            payload = json.loads(handler.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, TypeError):
            handler._json(400, {'error': '请求必须是 64KB 以内的 JSON 对象'})
            return True
    from shared_platform.workbench_http import dispatch
    preparation = getattr(runtime,'new_task_worker',None)
    response = dispatch(runtime.engine, method, handler.path, payload,
                        create_handler=preparation.create if preparation else None)
    if response is None:
        handler._json(404, {'error': '任务接口不存在'})
    else:
        if response[0] < 300 and ('task' in response[1] or 'tasks' in response[1]):
            from shared_platform.workbench_delisting_receipt import public_projection
            response = (response[0], public_projection(response[1]))
        dispatcher = runtime.worker.status()
        if method == 'GET' and parts == ['api', 'orbit', 'tasks'] and response[0] == 200:
            response[1]['dispatcher'] = dispatcher
        if method == 'GET' and response[0] == 200 and ('task' in response[1] or 'tasks' in response[1]):
            response = (response[0], profit_unknown_projection(response[1], runtime.profile))
        if not (getattr(runtime, 'worker_enabled', False)
                and dispatcher['running'] and dispatcher['state'] == 'polling'):
            response = (response[0], paused_projection(response[1]))
            if preparation and preparation.status()['running']:
                for task in response[1].get('tasks',[]) + ([response[1]['task']] if 'task' in response[1] else []):
                    if preparation.owns(task['task_id']):
                        task['executor_connected'] = (preparation.status()['profit_executor_connected'] is True
                            if task['template']=='profit' else preparation.status()['delisting_executor_connected'] is True
                            if task['template']=='delisting' else True)
                        task['preparation_scope'] = ('EXPLICIT_NEW_POST_PROFIT_READONLY'
                            if task['template'] == 'profit' else 'EXPLICIT_NEW_POST_DELIST_EXACT_SCOPE'
                            if task['template']=='delisting' else 'EXPLICIT_NEW_POST_PUBLICATION_ONLY')
        handler._json(*response)
        if method == 'POST' and response[0] < 300:
            runtime.worker.wake()
            if preparation: preparation.wake()
    return True
