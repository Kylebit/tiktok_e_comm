"""Service-owned preparation provenance; no mutation or budget authority."""
from copy import deepcopy

import pytest

from modules.products import server
from shared_platform import operations_publication_common as common
from shared_platform import round1_workspace as workspace
from shared_platform import workbench_publication_native as native
from test_round1_workspace_freeze import live, prepared
from test_common_native_preparation_source import _native_common_chain


def test_actual_new_request_and_restart_share_persisted_initial_root(live):
    first, request = prepared(live)
    one = workspace.read_preparation(live['offer'], first['prepared_reference'])
    root = workspace.preparation_root_facts(one)
    assert root['status'] == 'PERSISTED_NATIVE_PREPARATION_ROOT'
    assert root['origin_request_id'] == request['request_id']
    code, second = live['call']('prepare', {**request, 'request_id': 'technical-new-request'})
    assert code == 200 and second['status'] == 'PREPARED', second
    assert second['prepared_reference'] != first['prepared_reference']
    two = workspace.read_preparation(live['offer'], second['prepared_reference'])
    assert two['preparation_root'] == one['preparation_root']
    assert two['request_id'] != one['request_id']
    live['restart']()
    before = live['store'].path.read_bytes()
    reopened = workspace.read_preparation(live['offer'], second['prepared_reference'])
    assert workspace.preparation_root_facts(reopened) == root
    assert live['store'].path.read_bytes() == before
    assert root['execution_authority'] is False
    assert root['confirmed_write_count'] == root['maximum_confirmed_writes'] == 'UNKNOWN'
    assert root['coverage_authority'] == 'UNKNOWN'
    assert len(live['fake'].calls) == 7


def test_actual_native_common_binding_consumes_original_root_without_quota(live, monkeypatch):
    engine, task, profile, frozen = _native_common_chain(live, monkeypatch)
    original = workspace.read_preparation(live['offer'], frozen['prepared_reference'])
    expected = workspace.preparation_root_facts(original)
    assert expected['status'] == 'PERSISTED_NATIVE_PREPARATION_ROOT'
    actual = common._verified_binding(task, common._read(server, task)['common'], profile)
    observed = task['pending_observation']['receipt_binding']['preparation_source']
    assert actual['preparation_source']['preparation_root'] == expected
    assert observed['preparation_root'] == expected
    assert observed['prepared_reference'] == frozen['prepared_reference']
    assert expected['execution_authority'] is False
    admission = task['checkpoint']['common_technical_admission']
    assert admission['status'] == 'BLOCKED'
    assert admission['execution_authority'] is False
    # The actual writer's current frozen reference is a continuation anchor.
    from modules.sourcing import new_product_workbench as workbench
    with live['store']._connect_readonly() as connection:
        successor = workspace._preparation_root(connection, scope=original['scope'],
            state=workbench.load_state(live['offer']),
            unbound_packet=original['unbound_packet'], request_id='later-technical-request', server=server)
    assert successor == original['preparation_root']
    assert native.read_frozen(engine.get(task['task_id']), profile) == frozen


def test_caller_root_is_rejected_without_persisting_or_adopting_origin(live):
    packet, request = prepared(live)
    with live['store']._connect_readonly() as connection:
        before = connection.execute('SELECT COUNT(*) FROM round1_workspace_preparations').fetchone()[0]
    code, value = live['call']('prepare', {**request, 'request_id': 'caller-new-cycle',
        'preparation_root': {'root_id': 'r1-cycle:' + '0' * 32}})
    assert code == 409 and value['status'] == 'BLOCKED', value
    with live['store']._connect_readonly() as connection:
        assert connection.execute('SELECT COUNT(*) FROM round1_workspace_preparations').fetchone()[0] == before
    broken = deepcopy(workspace.read_preparation(live['offer'], packet['prepared_reference']))
    broken['preparation_root'] = {'schema_version': workspace.PREPARATION_ROOT_SCHEMA,
        'status': 'PERSISTED_NATIVE_PREPARATION_ROOT', 'root_id': 'r1-cycle:' + '0' * 32,
        'offer_id': live['offer'], 'origin_request_id': 'unpersisted-caller-origin',
        'execution_authority': False}
    with pytest.raises(workspace.Round1WorkspaceError, match='R1_PREPARATION_ROOT_DOCUMENT_MISMATCH'):
        workspace.preparation_root_facts(broken)


def test_owned_historical_schema_cannot_reset_origin_with_new_request(live):
    packet, request = prepared(live)
    # Explicit historical-schema fixture: genuine prepared product inputs,
    # locally retained under its old schema with no root/authority field. This
    # does not claim it was produced by the current native root writer.
    legacy = deepcopy(workspace.read_preparation(live['offer'], packet['prepared_reference']))
    legacy.pop('preparation_root')
    legacy['request_id'] = 'owned-legacy-request'
    legacy.pop('reference')
    legacy['reference'] = 'r1-prepared:' + workspace.digest(legacy)[7:]
    legacy_request = {**request, 'request_id': legacy['request_id']}
    with workspace._transaction() as connection:
        connection.execute('INSERT INTO round1_workspace_preparations VALUES (?,?,?,?,?,?)',
            (legacy['request_id'], live['offer'], legacy['reference'],
             workspace._json(legacy_request), workspace._json(legacy), workspace.digest(legacy)))
    retained = workspace.read_preparation(live['offer'], legacy['reference'])
    unknown = workspace.preparation_root_facts(retained)
    assert unknown['status'] == 'UNKNOWN'
    assert unknown['reason'] == 'LEGACY_PREPARATION_ROOT_UNKNOWN'
    assert 'root_id' not in unknown and unknown['execution_authority'] is False
    code, next_preparation = live['call']('prepare', {**request, 'request_id': 'after-unknown-history'})
    assert code == 200 and next_preparation['status'] == 'PREPARED', next_preparation
    next_doc = workspace.read_preparation(live['offer'], next_preparation['prepared_reference'])
    next_root = workspace.preparation_root_facts(next_doc)
    assert next_root['status'] == 'UNKNOWN' and 'root_id' not in next_root
    assert next_root['reason'] == 'PREPARATION_ROOT_CONTINUATION_AMBIGUOUS'
    assert next_root['confirmed_write_count'] == next_root['maximum_confirmed_writes'] == 'UNKNOWN'
    assert next_root['execution_authority'] is False and len(live['fake'].calls) == 7


def test_actual_changed_state_retaining_old_reference_does_not_establish_continuation(live, monkeypatch):
    from modules.sourcing import new_product_workbench as workbench
    _, _, _, frozen = _native_common_chain(live, monkeypatch)
    original = workspace.read_preparation(live['offer'], frozen['prepared_reference'])
    assert workspace.preparation_root_facts(original)['status'] == 'PERSISTED_NATIVE_PREPARATION_ROOT'
    changed = workbench.load_state(live['offer'])
    changed['review']['title'] = 'Different actual product facts after frozen preparation'
    workbench.save_state(live['offer'], changed)
    current = workbench.load_state(live['offer'])
    assert current['product_approval']['round1_prepared_reference'] == original['reference']
    # Exercise the actual preparation writer's record-only root check with
    # persisted state and a genuine current producer packet. No whole reprepare
    # or release authority is claimed by this narrowly scoped regression.
    scope = {**original['scope'], 'product_center_revision': current['_revision']}
    packet, _, _ = workspace._packet(server, scope, bound=False)
    assert workspace._semantic_packet(packet) != workspace._semantic_packet(original['unbound_packet'])
    before = live['store'].path.read_bytes()
    with live['store']._connect_readonly() as connection:
        root = workspace._preparation_root(connection, scope=scope, state=current,
            unbound_packet=packet, request_id='changed-facts-new-request')
    assert root['status'] == 'UNKNOWN' and 'root_id' not in root
    assert root['reason'] == 'CURRENT_PREPARATION_CONTINUATION_UNKNOWN'
    assert root['execution_authority'] is False
    assert live['store'].path.read_bytes() == before
    assert workspace.read_preparation(live['offer'], original['reference']) == original


@pytest.mark.parametrize('damage', ['missing-decision', 'changed-decision', 'changed-state', 'no-service-context'])
def test_technical_continuation_requires_actual_decision_and_unchanged_source(live, monkeypatch, damage):
    import json
    from modules.sourcing import new_product_workbench as workbench
    from shared_platform import publication_rounds
    _, _, _, frozen = _native_common_chain(live, monkeypatch)
    original = workspace.read_preparation(live['offer'], frozen['prepared_reference'])
    decision_path = publication_rounds.report_dir(live['offer']) / 'round1-auto-decision.json'
    context = server
    if damage == 'missing-decision':
        decision_path.unlink()
    elif damage == 'changed-decision':
        decision = json.loads(decision_path.read_text(encoding='utf-8'))
        decision['policy_digest'] = 'sha256:' + '0' * 64
        decision_path.write_text(json.dumps(decision), encoding='utf-8')
    elif damage == 'changed-state':
        changed = workbench.load_state(live['offer'])
        changed['review']['title'] = 'Changed actual source after technical freeze'
        workbench.save_state(live['offer'], changed)
    else:
        context = None
    before = live['store'].path.read_bytes()
    with live['store']._connect_readonly() as connection:
        root = workspace._preparation_root(connection, scope=original['scope'],
            state=workbench.load_state(live['offer']), unbound_packet=original['unbound_packet'],
            request_id='untrusted-technical-continuation', server=context)
    assert root['status'] == 'UNKNOWN' and 'root_id' not in root
    assert root['reason'] == 'CURRENT_PREPARATION_CONTINUATION_UNKNOWN'
    assert root['execution_authority'] is False
    assert live['store'].path.read_bytes() == before
    assert workspace.read_preparation(live['offer'], original['reference']) == original
