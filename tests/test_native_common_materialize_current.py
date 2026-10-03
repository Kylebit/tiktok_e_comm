"""A real completed COMMON keeps its owner when market configuration blocks."""
from modules.products import server
from shared_platform import operations_publication_common as common
from shared_platform import publication_runtime_config as configuration
from shared_platform import workbench_publication_native as native
from test_round1_workspace_freeze import live as original_live
from test_round1_auto_freeze import public_settings
from test_native_r2_service_consumers import live, images_task, _runtime, _complete_images
from test_native_common_technical_execution import _install_owned
from test_native_common_service_facts import _installed, _closed_signed_transport


def test_original_common_completion_with_blocked_market_configuration_retains_current_owner_and_one_write(images_task, monkeypatch, tmp_path, request):
    v = images_task
    task = _complete_images(v, monkeypatch)
    original_frozen = native.read_frozen(task, v['profile'])
    installed = _runtime(v, monkeypatch, tmp_path, enabled=True)
    request.addfinalizer(installed.close)
    offer = task['scope']['offer_id']
    code, view = server._preview_r3_common_stage({'offer_id': offer,
        'release_stage': 'R3_COMMON', 'publication_targets': ['miaoshou:COMMON']})
    assert code == 200, view
    preview = view['common']['plan']
    assert installed.store.get_plan(preview['plan_id']) is None
    _install_owned(installed.store, tmp_path)
    _installed(monkeypatch, tmp_path, installed.store)
    empty = tmp_path / 'missing-market-configuration'
    empty.mkdir()
    startup = configuration.capture_startup_config(root=empty, environ={
        'ORBIT_R3_POLICY_PATH': 'policy.json', 'ORBIT_R3_INCIDENT_REGISTRY_PATH': 'incidents.json'})
    monkeypatch.setattr(server, 'R3_STARTUP_CONFIG', startup)
    status, configured = configuration.diagnose(startup)
    assert status['status'] == 'BLOCKED' and configured == {}
    assert {row['status'] for row in status['documents'].values()} == {'MISSING'}
    calls = _closed_signed_transport(monkeypatch, installed.store, preview['payload'])
    installed.start()
    assert common.run(v['engine'], task, v['token'], v['profile']) is False
    current = v['engine'].get(task['task_id'])
    assert current['execution_state'] == 'waiting_domain' and current['current_step'] == 'release'
    assert current['required_action'] is None
    assert current['pending_observation']['reason'] == 'R3_CONFIGURATION_BLOCKED'
    assert current['pending_observation']['label'] == '终审候选待准备'
    assert current['checkpoint']['post_common_diagnostic']['blockers'] == ['R3_CONFIGURATION_BLOCKED']
    assert current['version'] == task['version'] and current['scope'] == task['scope']
    assert native.read_frozen(current, v['profile']) == original_frozen
    assert calls.count('/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail') == 1
    plan = installed.store.get_plan(preview['plan_id'])
    assert plan['approval'] is None and plan['status'] == 'PENDING_APPROVAL'
    assert all(plan[key] == preview[key] for key in ('plan_id', 'payload_digest', 'payload'))
    with installed.store._connect_readonly() as db:
        db.execute('BEGIN')
        rows = db.execute('SELECT * FROM release_runs WHERE plan_id=?', (plan['plan_id'],)).fetchall()
        assert len(rows) == 1 and rows[0]['technical_execution_state'] == 'CONFIRMED_WRITE'
        run_id = rows[0]['run_id']
        assert db.execute('SELECT COUNT(*) FROM release_approvals WHERE plan_id=?', (plan['plan_id'],)).fetchone()[0] == 0
    # Ordinary repeated stage reads retain this exact completed binding; they
    # cannot turn a configuration diagnostic into another transport write.
    current_bytes = (v['profile'].data_root / 'tasks.db').read_bytes()
    for _ in range(2):
        code, retained = server._publication_stages_for_request({'offer_id': offer})
        assert code == 200 and retained['common']['status'] == 'RETAINED_TECHNICAL_BASELINE', retained
        assert retained['common']['run']['run_id'] == run_id
    assert (v['profile'].data_root / 'tasks.db').read_bytes() == current_bytes
    assert calls.count('/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail') == 1
    with installed.store._connect_readonly() as db:
        db.execute('BEGIN')
        repeated = db.execute('SELECT run_id FROM release_runs WHERE plan_id=?', (plan['plan_id'],)).fetchall()
        assert [row['run_id'] for row in repeated] == [run_id]
