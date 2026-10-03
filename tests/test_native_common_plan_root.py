"""Real persisted preparation → original COMMON plan, not quota authority."""
from shared_platform import round1_workspace as workspace
from shared_platform import operations_publication_common as common
from modules.products import server
from test_round1_workspace_freeze import live
from test_common_native_preparation_source import _native_common_chain


def test_actual_common_plan_persists_same_native_preparation_root(live, monkeypatch):
    _, task, _, frozen = _native_common_chain(live, monkeypatch)
    original = workspace.read_preparation(live['offer'], frozen['prepared_reference'])
    root = workspace.preparation_root_facts(original)
    observed = task['pending_observation']['receipt_binding']['preparation_source']
    assert root['status'] == 'PERSISTED_NATIVE_PREPARATION_ROOT'
    assert observed['preparation_root'] == root
    payload = common._read(server, task)['common']['plan']['payload']
    store = server._release_store()
    persisted = store.create_plan(payload)
    live['restart']()
    reopened = store.get_plan(persisted['plan_id'])
    binding = reopened['payload']['r3_stage_binding'].get('native_preparation_source')
    assert binding is not None, 'COMMON immutable plan lost its genuinely persisted native preparation root'
    assert binding['prepared_reference'] == original['reference']
    assert binding['preparation_root']['root_id'] == root['root_id']
    assert binding['offer_id'] == frozen['offer_id']
    assert binding['round1_snapshot_digest'] == frozen['snapshot_digest']
    assert binding['execution_authority'] is False


def test_legacy_common_preview_does_not_adopt_caller_preparation_root(tmp_path, monkeypatch):
    from test_b4b_common_stage import context
    _, _, store, request = context(tmp_path, monkeypatch)
    code, first = server._preview_r3_common_stage(request)
    assert code == 200 and not store.path.exists(), first
    claimed = {'root_id': 'r1-cycle:' + '0' * 32, 'status': 'PERSISTED_NATIVE_PREPARATION_ROOT',
               'execution_authority': True}
    code, second = server._preview_r3_common_stage({**request, 'native_preparation_source': claimed})
    assert code == 200 and not store.path.exists(), second
    assert second['common']['plan']['payload'] == first['common']['plan']['payload']
    assert second['common']['plan']['payload']['r3_stage_binding'].get('native_preparation_source') is None
    assert second['external_writes_performed'] == []
