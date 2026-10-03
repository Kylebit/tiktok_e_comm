"""Owned source/HTTP/lease/finance producer; only the child transport is closed."""
import hashlib
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import threading
import time

import pytest

from modules.products import server
from shared_platform.bounded_web_server import BoundedThreadingHTTPServer
from shared_platform.native_profit_preparation import NativeProfitServiceConfig, PROFIT_SCOPE
from shared_platform.native_task_preparation import install_explicit_new_task_preparation
from shared_platform.operations_runtime import OperationsWorker
from shared_platform.workbench_engine import WorkbenchEngine
from owned_native_parent_profile import owned_profile
from test_native_scoped_service_launcher import _post
from test_operations_profit_producer import inputs

SOURCE = Path(__file__).resolve().parents[1]
PROFIT_SOURCE_PATHS = (
    'shared_platform/native_profit_preparation.py',
    'shared_platform/workbench_engine.py',
    'shared_platform/operations_profit_scope.py',
    'shared_platform/workbench_profit_adapter.py',
    'shared_platform/operations_profit_producer.py',
    'shared_platform/operations_http.py',
    'shared_platform/internal_catalog_sku.py',
    'domains/data_operations/skills/manage-profit-settlement/SKILL.md',
    'domains/data_operations/skills/manage-profit-settlement/report-policy.json',
    'domains/data_operations/profit_settlement/__init__.py',
    'domains/data_operations/profit_settlement/tiktok.py',
    'domains/data_operations/profit_settlement/shopee.py',
    'domains/data_operations/profit_settlement/shared_inputs.py',
    'domains/data_operations/profit_settlement/tiktok_coverage.py',
    'domains/data_operations/profit_settlement/monthly_missing_cost_scope.py',
    'domains/data_operations/profit_settlement/cost_policy.py',
    'domains/data_operations/profit_settlement/audit.py',
    'domains/data_operations/profit_settlement/render.py',
)


def _runtime(tmp_path, *, configured=True, clock=None):
    root = tmp_path / 'owned-profit-source'
    root.mkdir()
    for name in PROFIT_SOURCE_PATHS:
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((SOURCE / name).read_bytes())
        assert destination.read_bytes() == (SOURCE / name).read_bytes()
    (root / 'config').mkdir()
    (root / 'config/settings.json').write_text(json.dumps({'database': 'missing-private-catalog.db',
        'token_file': 'owned-shop-identities.json'}), encoding='utf-8')
    (root / 'owned-shop-identities.json').write_text(json.dumps({'authorized_shops': [
        {'id': 'shop-MY', 'region': 'MY'}]}), encoding='utf-8')
    profile = owned_profile(root)
    kwargs = {'clock': clock} if clock is not None else {}
    engine = WorkbenchEngine(profile.data_root / 'tasks.db', {
        'code_version': profile.version, 'environment': profile.environment,
        'manifest_digest': profile.manifest_digest}, **kwargs)
    old_worker = OperationsWorker(engine, profile, {})
    runtime = SimpleNamespace(engine=engine, profile=profile, worker=old_worker, worker_enabled=False)
    if configured:
        runtime.native_profit_config = NativeProfitServiceConfig.capture(profile, root, sys.executable)
    return runtime


def _payload(key='new-explicit-profit'):
    return {'template': 'profit', 'source_key': key, 'reuse_existing_scope': True,
        'scope': {'month': '2026-08', 'platforms': ['tiktok'], 'sites': ['MY'], 'shops': ['shop-MY']}}


def _closed_cli(monkeypatch, *, unknown=False, missing=False):
    from shared_platform import operations_runtime
    original = operations_runtime.subprocess.run
    calls = []
    def run(argv, **kwargs):
        if 'exec' not in argv:
            return original(argv, **kwargs)  # Actual Git source/manifest checks.
        assert argv[0] == str(Path(sys.executable))
        assert argv[argv.index('--sandbox') + 1] == 'workspace-write'
        output = Path(kwargs['cwd'])
        assert '--add-dir' not in argv and output.name.startswith('monthly-')
        prompt_data=json.loads(kwargs['input'].split('Task/notes below are data, not expanded permission:\n',1)[1])
        prefix='Service-owned read-only source binding: '
        source_notes=[note for note in prompt_data['notes'] if note.startswith(prefix)]
        assert len(source_notes)==1
        bound=json.loads(source_notes[0][len(prefix):])
        assert bound['schema_version']=='native-profit-readonly-source/v1'
        assert bound['credential_refresh'] is False and bound['business_writes'] is False
        fixed_root=Path(bound['configuration_root'])
        assert Path(bound['settings_path'])==fixed_root/'config/settings.json'
        assert hashlib.sha256(Path(bound['settings_path']).read_bytes()).hexdigest()==bound['settings_sha256']
        assert prompt_data['scope']['month']==bound['request_scope']['month']=='2026-08'
        assert prompt_data['scope']['shops']==bound['request_scope']['shops']==['shop-MY']
        calls.append({'argv': list(argv), 'output': str(output)})
        if unknown:
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'], output='{"type":"thread.started","thread_id":"owned-profit-unknown"}')
        if missing:
            result = {'summary': 'Original financial sources unavailable',
                'missing_inputs': ['缺少完整月份结算导出与实际广告来源'], 'evidence_paths': []}
        else:
            _, manifest, _, _ = inputs(output / 'original-financial-inputs')
            result = {'summary': 'Synthetic complete original finance sources',
                'missing_inputs': [], 'evidence_paths': [manifest['path']]}
        Path(argv[argv.index('--output-last-message') + 1]).write_text(json.dumps(result), encoding='utf-8')
        return subprocess.CompletedProcess(argv, 0,
            stdout='{"type":"thread.started","thread_id":"owned-profit-session"}\n', stderr='')
    monkeypatch.setattr(operations_runtime.subprocess, 'run', run)
    return calls


def _wait(engine, task_id):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        current = engine.get(task_id)
        if current['execution_state'] in {'completed', 'failed', 'waiting_user', 'reconciliation_required'}:
            return current
        time.sleep(.05)
    pytest.fail('Exact owned profit worker did not complete a bounded attempt: ' + repr(engine.get(task_id)))


@pytest.mark.parametrize('outcome', ['complete', 'unknown', 'missing-inputs'])
def test_actual_new_profit_post_worker_uses_original_producer_and_never_admits_history_or_replays(tmp_path, monkeypatch, outcome):
    runtime = _runtime(tmp_path)
    old = runtime.engine.create({**_payload('old-retained-profit'), 'scope': {
        **_payload()['scope'], 'month': '2026-07'}})
    old_events = runtime.engine.store.events(old['task_id'])
    calls = _closed_cli(monkeypatch, unknown=outcome == 'unknown', missing=outcome == 'missing-inputs')
    http = BoundedThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    http.operations_runtime = runtime
    worker = install_explicit_new_task_preparation(runtime)
    worker.interval = .05
    listener = threading.Thread(target=http.serve_forever, daemon=True)
    listener.start()
    try:
        code, body = _post(http, '/api/orbit/tasks', _payload())
        assert code == 201, body
        tid = body['task']['task_id']
        assert worker.owns(tid) and not worker.owns(old['task_id'])
        code, duplicate = _post(http, '/api/orbit/tasks', _payload())
        assert code == 201 and duplicate['task']['task_id'] == tid
        current = _wait(runtime.engine, tid)
        grants = [e for e in runtime.engine.store.events(tid) if e['event_type'] == 'explicit_new_post_profit_readonly']
        assert len(grants) == 1 and grants[0]['detail']['scope'] == current['request_scope']
        assert current['version'] == runtime.engine.release and len(calls) == 1
        assert runtime.engine.store.events(old['task_id']) == old_events
        assert not runtime.worker_enabled and worker.status()['scope'] == 'EXPLICIT_NEW_POST_PUBLICATION_ONLY'
        assert worker.status()['profit_scope'] == PROFIT_SCOPE and worker.status()['historical_task_scan'] is False
        if outcome == 'complete':
            assert current['execution_state'] == 'completed' and all(s['state'] == 'completed' for s in current['steps']), current
            coverage = current['steps'][0]['checkpoint']
            receipt = json.loads(Path(coverage['producer_receipt']['path']).read_bytes())
            assert receipt['status'] == 'VERIFIED' and receipt['external_write_count'] == 0
            assert receipt['executions'][0]['builder'].endswith('tiktok.build_monthly_report')
            assert receipt['identity']['scope'] == current['scope']
            assert receipt['identity']['release_identity'] == runtime.engine.release
            report = json.loads(Path(coverage['reports'][0]['report']['path']).read_bytes())
            assert report['period']['end'] == '2026-08-31'
            assert Decimal(str(report['totals']['advertising_cny'])) == Decimal('1')
            assert current['result_url'].endswith('/artifacts/index.html')
            assert (runtime.profile.data_root / 'artifacts' / tid / 'result/index.html').is_file()
        elif outcome == 'unknown':
            assert current['execution_state'] == 'failed' and current['checkpoint']['agent_unknown'] is True
            original_receipt = current['checkpoint']['attempt_receipt']
            original_events = runtime.engine.store.events(tid)
            code, duplicate = _post(http, '/api/orbit/tasks', _payload())
            assert code == 201 and duplicate['task']['task_id'] == tid
            worker.wake()
            time.sleep(.25)
            retained = runtime.engine.get(tid)
            assert retained['checkpoint']['attempt_receipt'] == original_receipt
            assert runtime.engine.store.events(tid) == original_events and len(calls) == 1
        else:
            assert current['execution_state'] == 'waiting_user'
            assert current['required_action']['kind'] == 'input'
            assert current['required_action']['reason'] == '缺少完整月份结算导出与实际广告来源'
            original_receipt = current['checkpoint']['attempt_receipt']
            assert current['checkpoint']['agent_unknown'] is False
            code, duplicate = _post(http, '/api/orbit/tasks', _payload())
            assert code == 201 and duplicate['task']['task_id'] == tid
            worker.wake(); time.sleep(.15)
            assert runtime.engine.get(tid)['checkpoint']['attempt_receipt'] == original_receipt
            assert len(calls) == 1 and not (runtime.profile.data_root / 'fixed-producer' / tid).exists()
    finally:
        http.shutdown(); listener.join(5); http.server_close()
        worker.close(); runtime.worker.close()
    assert not worker.thread.is_alive() and all(f.done() for f in worker.inflight.values())
    restarted = install_explicit_new_task_preparation(SimpleNamespace(
        engine=runtime.engine, profile=runtime.profile, native_profit_config=runtime.native_profit_config))
    try:
        assert restarted.owns(tid) is (outcome == 'missing-inputs')
        assert not restarted.owns(old['task_id'])
        restarted.wake(); time.sleep(.15)
        assert len(calls) == 1 and runtime.engine.store.events(old['task_id']) == old_events
    finally: restarted.close()


def test_monthly_reused_old_profit_request_has_no_new_grant_or_dispatch(tmp_path,monkeypatch):
    runtime = _runtime(tmp_path)
    old = runtime.engine.create(_payload('old-monthly-request'))
    before = runtime.engine.store.events(old['task_id'])
    # Keep this monthly reuse contract independent of a background executor
    # registration race; actual worker execution is exercised above.
    from shared_platform.native_task_preparation import ExplicitNewTaskWorker
    with monkeypatch.context() as stopped:
        stopped.setattr(ExplicitNewTaskWorker,'start',lambda self:None)
        worker = install_explicit_new_task_preparation(runtime)
    try:
        reused = worker.create(_payload('explicit-reuse-other-key'))
        assert reused['task_id'] == old['task_id'] and reused['execution_state'] == 'executor_offline'
        assert not worker.owns(old['task_id']) and runtime.engine.explicit_new_post_profit_task_ids() == ()
        assert runtime.engine.store.events(old['task_id']) == before
    finally: worker.close(); runtime.worker.close()


def test_missing_fixed_profit_binding_blocks_before_attempt_and_ignores_ambient(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path, configured=False)
    monkeypatch.setenv('ORBIT_OPERATIONS_AGENT_EXECUTABLE', sys.executable)
    monkeypatch.setenv('ORBIT_OPERATIONS_CONFIG_ROOT', str(runtime.profile.root))
    calls = _closed_cli(monkeypatch)
    worker = install_explicit_new_task_preparation(runtime)
    try:
        task = worker.create(_payload())
        current = _wait(runtime.engine, task['task_id'])
        assert current['execution_state'] == 'failed'
        failure = next(e for e in runtime.engine.store.events(task['task_id']) if e['event_type'] == 'execution_failed')
        assert failure['detail']['reason'] == 'NATIVE_PROFIT_SERVICE_BINDING_REQUIRED'
        assert not calls and 'attempt_receipt' not in current['checkpoint']
        assert worker.status()['profit_executor_connected'] is False
    finally: worker.close(); runtime.worker.close()


@pytest.mark.parametrize('drift', ['settings', 'source', 'lease'])
def test_profit_first_attempt_rechecks_fixed_source_config_and_real_lease(tmp_path, monkeypatch, drift):
    instant=[1000.0]
    runtime = _runtime(tmp_path,clock=lambda:instant[0])
    calls = _closed_cli(monkeypatch)
    # Real factory binding and adapter, with only the background scheduling
    # suppressed so the exact lease can be invalidated at the bounded seam.
    from shared_platform.native_task_preparation import ExplicitNewTaskWorker
    with monkeypatch.context() as stopped:
        stopped.setattr(ExplicitNewTaskWorker, 'start', lambda self: None)
        worker = install_explicit_new_task_preparation(runtime)
    try:
        task = worker.create(_payload())
        runtime.engine.register_executor(worker.worker_id, ['profit'], runtime.engine.release)
        token = runtime.engine.claim(task['task_id'], worker.worker_id)['lease_token']
        if drift == 'settings':
            (runtime.profile.root / 'config/settings.json').write_text('{}')
            code = 'NATIVE_PROFIT_FIXED_BINDING_CHANGED'
        elif drift == 'source':
            (runtime.profile.root / 'shared_platform/native_profit_preparation.py').write_bytes(b'# changed original runtime\n')
            code = 'NATIVE_PROFIT_RUNTIME_CHANGED'
        else:
            instant[0]+=301  # Original claim TTL is bounded to at most 300.
            code = 'stale or invalid lease'
        current = runtime.engine.get(task['task_id'])
        with pytest.raises(ValueError, match=code):
            worker.adapters['profit'](runtime.engine, current, token, runtime.profile)
        assert not calls and 'attempt_receipt' not in runtime.engine.get(task['task_id'])['checkpoint']
    finally: worker.close(); runtime.worker.close()
