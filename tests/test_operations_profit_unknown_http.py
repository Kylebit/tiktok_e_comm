"""Web-only task API exposes bounded profit UNKNOWN evidence without execution."""

import hashlib
import json
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

from shared_platform.operations_runtime import RuntimeProfile
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.workbench_profit_adapter import _begin_agent_attempt


def test_web_only_profit_unknown_projection_is_read_only_and_scoped(tmp_path, monkeypatch):
    from modules.products import server
    from shared_platform import operations_service
    from shared_platform.operations_launch import maintenance_handler

    profile = RuntimeProfile(tmp_path, tmp_path / 'runtime', 'stable', 'a' * 40)
    engine = WorkbenchEngine(tmp_path / 'tasks.db', {'code_version': profile.version,
        'environment': profile.environment, 'manifest_digest': profile.manifest_digest})
    engine.register_executor('fixture', ['profit', 'publication'], engine.release)
    profit = engine.create({'template': 'profit', 'scope': {'month': '2026-08'},
        'source_key': 'unknown-profit'})
    profit_id = profit['task_id']
    token = engine.claim(profit_id, 'fixture')['lease_token']
    output, started = _begin_agent_attempt(engine, engine.get(profit_id), token, profile, 0)
    output.mkdir(parents=True)
    raw = b'{"summary":"private financial source text"}'
    (output / 'agent-result.json').write_bytes(raw)
    engine.record_checkpoint(profit_id, token, {**started,
        'agent_result': {'session_id': 'fixture-session',
        'result': {'summary': 'private financial source text'}}})
    other = engine.create({'template': 'publication', 'scope': {'offer_id': '123'},
        'source_key': 'not-profit'})
    other_token = engine.claim(other['task_id'], 'fixture')['lease_token']
    engine.record_checkpoint(other['task_id'], other_token, {'agent_attempt_started': True,
        'attempt_output': str(output)})
    invalid = engine.create({'template': 'profit', 'scope': {'month': '2026-08'},
        'source_key': 'invalid-profit-path'})
    invalid_token = engine.claim(invalid['task_id'], 'fixture')['lease_token']
    engine.record_checkpoint(invalid['task_id'], invalid_token, {'agent_attempt_started': True,
        'attempt_output': str(tmp_path / 'private' / 'source.json')})

    class Worker:
        def status(self):
            return {'running': False, 'state': 'stopped'}
        def wake(self):
            raise AssertionError('GET must not wake the worker')

    runtime = SimpleNamespace(engine=engine, profile=profile, worker=Worker(),
                              worker_enabled=False)
    monkeypatch.setattr(operations_service, 'get_runtime', lambda *args: runtime)
    http = ThreadingHTTPServer(('127.0.0.1', 0), maintenance_handler(server.Handler))
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()

    def call(method, path):
        connection = HTTPConnection('127.0.0.1', http.server_port)
        connection.request(method, path, '{}' if method == 'POST' else None,
                           {'Content-Type': 'application/json'} if method == 'POST' else {})
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body

    try:
        code, detail = call('GET', '/api/orbit/tasks/' + profit_id)
        assert code == 200
        readback = detail['task']['profit_unknown_readback']
        assert readback['status'] == 'UNKNOWN'
        assert readback['attempt_output'] == str(output)
        assert readback['agent_result_file']['sha256'] == hashlib.sha256(raw).hexdigest()
        assert readback['agent_result_file']['ownership'] == 'UNVERIFIED'
        assert readback['session_id'] == 'fixture-session'
        assert readback['automatic_resume_allowed'] is False
        assert 'private financial source text' not in json.dumps(detail)
        code, listing = call('GET', '/api/orbit/tasks')
        assert code == 200
        by_id = {task['task_id']: task for task in listing['tasks']}
        assert by_id[profit_id]['profit_unknown_readback']['status'] == 'UNKNOWN'
        assert 'profit_unknown_readback' not in by_id[other['task_id']]
        assert by_id[invalid['task_id']]['profit_unknown_readback']['status'] == 'BLOCKED'
        assert by_id[invalid['task_id']]['profit_unknown_readback']['attempt_output'] is None
        assert 'private financial source text' not in json.dumps(listing)
        assert call('POST', '/api/orbit/tasks/' + profit_id + '/retry')[0] == 409
        assert engine.get(profit_id)['execution_state'] == 'running'
    finally:
        http.shutdown(); http.server_close(); thread.join(timeout=5)
