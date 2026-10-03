"""Real preparation owner lineage is observation, never budget authority."""
from shared_platform import operations_publication_common as common
from shared_platform import round1_workspace
from shared_platform.round1_category_evidence import digest
from modules.products import server
from test_common_native_preparation_source import _native_common_chain
from test_round1_workspace_freeze import live


def test_real_owner_binding_survives_blocked_observe_and_materialization(live, monkeypatch):
    engine, task, profile, frozen = _native_common_chain(live, monkeypatch)
    view = common._read(server, task)
    binding = common._verified_binding(task, view['common'], profile)
    preparation = round1_workspace.read_preparation(
        frozen['offer_id'], reference=frozen['prepared_reference'])
    source = binding['preparation_source']
    assert source == {
        'schema_version': 'common-preparation-source/v1',
        'status': 'VERIFIED_NATIVE_R1', 'execution_authority': False,
        'owner_task_id': task['task_id'],
        'prepared_reference': frozen['prepared_reference'],
        'preparation_digest': digest(preparation),
        'snapshot_digest': frozen['snapshot_digest'],
        'targets': frozen['targets'],
        'account_identity_digest': preparation['scope']['account_identity_digest'],
        'source_region': preparation['scope']['source_region'],
        'preparation_root': {**preparation['preparation_root'],
                             'confirmed_write_count': 'UNKNOWN',
                             'maximum_confirmed_writes': 'UNKNOWN',
                             'coverage_authority': 'UNKNOWN'}}
    assert source['preparation_root']['execution_authority'] is False
    # This pending binding was produced by actual common.run inside the same
    # native chain, not copied into an approved or READY fixture.
    assert task['pending_observation']['receipt_binding'] == binding
    assert task['checkpoint']['common_technical_admission']['binding'] == binding
    assert task['checkpoint']['common_technical_admission']['status'] == 'BLOCKED'
    assert common.observe(engine, task, profile, server_module=server) is False
    assert engine.get(task['task_id']) == task

    # A second independent original source reaches release with its real claim
    # still active. The production consumer owns checkpoint and domain waiting.
    from contextlib import contextmanager
    from test_round1_workspace_freeze import live as original_live
    from shared_platform import workbench_publication_native as native
    observed = []
    def active_probe(active_engine, active_task, token, active_profile):
        active_view = common._read(server, active_task)
        active_binding = common._verified_binding(active_task, active_view['common'], active_profile)
        frozen = native.read_frozen(active_task, active_profile)
        with active_engine.transaction() as db:
            active_engine._lease(db, active_task['task_id'], token)
        database = server._release_store().path.read_bytes()
        assert common._materialize_or_block(active_engine, server, active_task, token,
                                            active_view, active_profile) is False
        current = active_engine.get(active_task['task_id'])
        assert current['version'] == active_task['version'] == active_engine.release
        assert current['scope'] == active_task['scope']
        assert current['execution_state'] == 'waiting_domain'
        diagnostic = current['checkpoint']['post_common_diagnostic']
        assert diagnostic['status'] == 'BLOCKED' and diagnostic['blockers']
        assert diagnostic['execution_authority'] is False
        assert current['pending_observation']['receipt_binding'] == active_binding
        assert current['pending_observation']['label'] == '终审候选待准备'
        assert current['required_action'] is None and current['worker'] is None
        with active_engine.transaction() as db:
            row = active_engine._row(db, current['task_id'])
            assert row['external_started'] == 0 and row['lease_token'] is None
        assert native.read_frozen(current, active_profile) == frozen
        assert server._release_store().path.read_bytes() == database
        assert active_view['external_writes_performed'] == []
        observed.append(current['task_id'])
    probe_root = live['root'].parent / 'active-material-probe'
    probe_root.mkdir()
    with monkeypatch.context() as probe_patch:
        with contextmanager(original_live.__wrapped__)(probe_root, probe_patch) as probe_live:
            active_engine, current, _, _ = _native_common_chain(
                probe_live, probe_patch, active_release_probe=active_probe)
            assert observed == [current['task_id']]
            assert active_engine.get(current['task_id']) == current
    assert engine.get(task['task_id']) == task


def test_legacy_unknown_source_stays_blocked_without_provider_mutation(tmp_path, monkeypatch):
    from test_operations_publication_common import setup, claim
    engine, task_id, profile, provider, _, _ = setup(
        tmp_path, monkeypatch, synthetic_technical_authority=False)
    assert common.run(engine, engine.get(task_id), claim(engine, task_id),
                      profile, server_module=server) is False
    waiting = engine.get(task_id)
    diagnostic = waiting['checkpoint']['common_technical_admission']
    assert diagnostic['status'] == 'BLOCKED'
    assert diagnostic['binding']['preparation_source'] == {
        'schema_version': 'common-preparation-source/v1',
        'status': 'UNKNOWN', 'execution_authority': False}
    assert 'COMMON_PREPARATION_SOURCE_UNKNOWN' in diagnostic['blockers']
    assert provider.mutations == 0
    assert waiting['pending_observation']['receipt_binding'] == diagnostic['binding']
