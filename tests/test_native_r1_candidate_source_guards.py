"""Owned native source guards; no new actor, approval or budget authority."""
from copy import deepcopy
import json
import os

import pytest

from modules.products import server
from modules.sourcing import new_product_workbench as workbench
from shared_platform import publication_rounds, round1_workspace
from test_native_r1_candidate_sidecar import _prepare_with_real_sidecar
from test_round1_auto_freeze import complete_source, public_settings, request as auto_request
from test_round1_workspace_freeze import live, prepared


def _row_count(live):
    with live['store']._connect_readonly() as connection:
        return connection.execute('SELECT COUNT(*) FROM round1_workspace_preparations').fetchone()[0]


def test_absent_source_keeps_placeholder_and_explicit_absent_binding(live):
    complete_source(live)
    packet, _ = prepared(live)
    document = round1_workspace.read_preparation(live['offer'], packet['prepared_reference'])
    assert document['candidate_input'] == {
        'schema_version': 'round1-candidate-input/v1',
        'relative_name': 'first-review-candidate-plan.json', 'present': False,
        'byte_length': 0, 'raw_sha256': None, 'raw_base64': None}
    assert all(row['copy']['status'] == 'AGENT_GENERATION_REQUIRED' for row in packet['packet']['targets'])
    assert round1_workspace.status(server, live['offer'], packet['prepared_reference'])['status'] == 'PREPARED'


@pytest.mark.parametrize('change', ['created', 'deleted'])
def test_source_presence_change_cannot_be_adopted_by_prepared_reference(live, change):
    path = publication_rounds.report_dir(live['offer']) / 'first-review-candidate-plan.json'
    if change == 'created':
        complete_source(live)
        packet, _ = prepared(live)
        path.write_bytes(b'{}')
    else:
        packet, _, _, path, _, _ = _prepare_with_real_sidecar(live)
        path.unlink()
    document = round1_workspace.read_preparation(live['offer'], packet['prepared_reference'])
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_CANDIDATE_INPUT_CHANGED'):
        round1_workspace.status(server, live['offer'], packet['prepared_reference'])
    assert round1_workspace.read_preparation(live['offer'], packet['prepared_reference']) == document
    assert not workbench.load_state(live['offer']).get('product_approval')


@pytest.mark.parametrize('raw', [b'{', b'null', b'{"schema_version":"unsupported"}', b' ' * (1024 * 1024 + 1)],
                         ids=['bad-json', 'json-null', 'unsupported-schema', 'oversize'])
def test_present_invalid_source_never_falls_back_or_persists_preparation(live, raw):
    _, request, _, path, _, _ = _prepare_with_real_sidecar(live)
    before = _row_count(live)
    state = workbench.load_state(live['offer'])
    path.write_bytes(raw)
    code, result = live['call']('prepare', {**request, 'request_id': 'invalid-source-new-request'})
    assert code == 409 and result['status'] == 'BLOCKED', result
    assert _row_count(live) == before
    assert workbench.load_state(live['offer']) == state
    assert path.read_bytes() == raw


@pytest.mark.parametrize('shape', ['directory', 'file-symlink'])
def test_candidate_path_requires_owned_regular_file(live, shape):
    path = publication_rounds.report_dir(live['offer']) / 'first-review-candidate-plan.json'
    if shape == 'directory':
        path.mkdir()
    else:
        target = path.with_name('private-candidate-target.json')
        target.write_bytes(b'{}')
        os.symlink(target, path)
        assert path.is_symlink() and path.resolve() == target.resolve()
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_CANDIDATE_INPUT_INVALID'):
        round1_workspace._candidate_input(live['offer'])


def test_missing_offer_parent_is_not_absent_candidate(live):
    missing_offer = '3828811810'
    assert not publication_rounds.report_dir(missing_offer).exists()
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_CANDIDATE_SOURCE_PARENT_UNAVAILABLE'):
        round1_workspace._candidate_input(missing_offer)


def test_byte_drift_blocks_retry_and_auto_freeze_without_new_decision_or_state(live):
    packet, request, document, path, raw, _ = _prepare_with_real_sidecar(live)
    state = workbench.load_state(live['offer'])
    path.write_bytes(raw + b'\n')
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_CANDIDATE_INPUT_CHANGED'):
        round1_workspace.prepare(server, request)
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_CANDIDATE_INPUT_CHANGED'):
        round1_workspace.auto_freeze(server, auto_request(packet, live['offer']))
    # Exercise the technical readback recheck itself without fabricating an
    # approved state. PREPARED state has no decision authority to adopt.
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_CANDIDATE_INPUT_CHANGED'):
        round1_workspace._auto_recheck(server, document, {})
    assert workbench.load_state(live['offer']) == state
    assert round1_workspace.read_preparation(live['offer'], packet['prepared_reference']) == document
    directory = publication_rounds.report_dir(live['offer'])
    assert not (directory / 'round1-auto-decision.json').exists()
    assert not (directory / 'round1-approved-snapshot.json').exists()


def test_legacy_source_reader_only_accepts_absence_without_adopting_candidate(live):
    complete_source(live)
    packet, _ = prepared(live)
    actual = round1_workspace.read_preparation(live['offer'], packet['prepared_reference'])
    legacy = deepcopy(actual)
    legacy.pop('candidate_input')
    # This is the legacy reader compatibility contract, not a manufactured
    # persisted old row or an authority/owner marker.
    assert round1_workspace._checked_candidate_input(legacy)[1] is None
    path = publication_rounds.report_dir(live['offer']) / 'first-review-candidate-plan.json'
    path.write_bytes(b'{}')
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_CANDIDATE_INPUT_UNBOUND'):
        round1_workspace._checked_candidate_input(legacy)
    assert round1_workspace.read_preparation(live['offer'], packet['prepared_reference']) == actual


def test_source_replaced_during_prepare_is_rejected_before_insert(live, monkeypatch):
    packet, request, document, path, raw, _ = _prepare_with_real_sidecar(live)
    original = round1_workspace._packet
    calls = []
    def replace_after_packet(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(kwargs['bound'])
        if calls == [True, False]:
            path.write_bytes(raw + b'\n')
        return result
    monkeypatch.setattr(round1_workspace, '_packet', replace_after_packet)
    count = _row_count(live)
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_CANDIDATE_INPUT_CHANGED'):
        round1_workspace.prepare(server, {**request, 'request_id': 'replace-before-insert'})
    assert calls == [True, False]
    assert _row_count(live) == count
    assert round1_workspace.read_preparation(live['offer'], packet['prepared_reference']) == document
