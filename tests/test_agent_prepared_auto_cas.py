"""Adoption reuses the native technical CAS, never a human actor assertion.

The native auto-freeze producer creates all R1 sources in a synthetic store.
R2 is a separate boundary: a sentinel proves it is reached only after the R1
proof passes, without pretending that this fixture supplies complete R2.
"""
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from modules.sourcing import new_product_workbench as workbench
from shared_platform import agent_prepared_publication_adoption as adoption
from shared_platform import publication_r3_image_bridge as bridge
from shared_platform import publication_rounds as rounds, round1_workspace
from shared_platform.release_store import default_release_store
from modules.products import server
from test_round1_workspace_freeze import live, prepared
from test_round1_auto_freeze import complete_source, public_settings, request


class ReachedSeparateR2Validation(RuntimeError):
    pass


@pytest.fixture
def frozen(live, monkeypatch):
    complete_source(live)
    packet, _ = prepared(live)
    result = round1_workspace.auto_freeze(server, request(packet, live['offer']))
    directory = rounds.report_dir(live['offer'])
    state = workbench.load_state(live['offer'])
    document = round1_workspace.read_preparation(live['offer'], packet['prepared_reference'])
    profile = SimpleNamespace(root=live['root'])
    monkeypatch.setattr(bridge, 'R2_DOCUMENTS', {})
    def r2_boundary(_):
        raise ReachedSeparateR2Validation('R1 CAS passed; R2 is not supplied by this fixture')
    monkeypatch.setattr(bridge, 'validate_r2_identity', r2_boundary)
    return profile, live['offer'], result['snapshot'], state, document, directory


def verify_r1_boundary(frozen):
    profile, offer, snapshot, *_ = frozen
    return adoption._verify_sources(profile, offer, snapshot['canonical_targets'])


def test_native_auto_cas_does_not_impersonate_preparation_actor(frozen):
    profile, offer, snapshot, state, document, directory = frozen
    assert document['actor'] == 'Kyle'
    assert state['product_approval']['approved_by'] == rounds.AUTOPILOT_ACTOR
    # The original human guard must continue rejecting this technical decision.
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_APPROVAL_SCOPE_CHANGED'):
        round1_workspace._same_state(document, state)
    paths = [profile.root/'data/new_product_workbench'/f'{offer}.json',
             directory/'round1-auto-decision.json', directory/'round1-approved-snapshot.json',
             default_release_store().path]
    before = {path: path.read_bytes() for path in paths}
    assert adoption._verify_native_auto_r1(document, state, snapshot, directory) == (
        directory/'round1-auto-decision.json', sha256(before[directory/'round1-auto-decision.json']).hexdigest())
    with pytest.raises(ReachedSeparateR2Validation):
        verify_r1_boundary(frozen)
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize('change', [
    'missing_receipt', 'corrupt_receipt', 'different_review',
    'receipt_digest', 'reviewed_revision', 'approved_revision', 'snapshot_time',
    'state_actor', 'state_revision', 'state_fact', 'state_decision', 'state_fingerprint',
    'state_authority', 'state_reference', 'state_time', 'state_review_digest',
])
def test_auto_cas_rejects_receipt_or_state_drift_before_r2(frozen, change):
    profile, offer, snapshot, state, document, directory = frozen
    decision_path = directory/'round1-auto-decision.json'
    decision = json.loads(decision_path.read_text(encoding='utf-8'))
    if change == 'missing_receipt':
        decision_path.unlink()
    elif change in {'corrupt_receipt', 'different_review'}:
        decision['first_review_digest'] = 'sha256:'+'f'*64
        if change == 'different_review':
            decision.pop('decision_digest')
            decision['decision_digest'] = rounds.canonical_digest(decision)
        decision_path.write_text(json.dumps(decision), encoding='utf-8')
    elif change in {'receipt_digest', 'reviewed_revision', 'approved_revision', 'snapshot_time'}:
        snapshot = deepcopy(snapshot)
        if change == 'receipt_digest': snapshot['decision_receipt_digest'] = 'sha256:'+'f'*64
        elif change == 'reviewed_revision': snapshot['reviewed_product_center_revision'] += 1
        elif change == 'approved_revision': snapshot['approved_product_center_revision'] += 1
        else: snapshot['approved_at'] = '2000-01-01T00:00:00+00:00'
        snapshot.pop('snapshot_digest')
        snapshot['snapshot_digest'] = rounds.canonical_digest(snapshot)
        (directory/'round1-approved-snapshot.json').write_text(json.dumps(snapshot), encoding='utf-8')
    else:
        state = deepcopy(state)
        if change == 'state_actor': state['product_approval']['approved_by'] = 'Kyle'
        elif change == 'state_revision': state['_revision'] += 1
        elif change == 'state_fact': state['review']['title'] = 'Changed after CAS'
        elif change == 'state_decision': state['product_approval']['decision_digest'] = 'sha256:'+'f'*64
        elif change == 'state_fingerprint': state['product_approval']['input_fingerprint'] = 'f'*64
        elif change == 'state_authority': state['product_approval']['approval_authority'] = 'CALLER'
        elif change == 'state_reference': state['product_approval']['round1_prepared_reference'] = 'r1-prepared:other'
        elif change == 'state_time': state['product_approval']['approved_at'] = '2000-01-01T00:00:00+00:00'
        else: state['product_approval']['round1_review_digest'] = 'sha256:'+'f'*64
        (profile.root/'data/new_product_workbench'/f'{offer}.json').write_text(json.dumps(state), encoding='utf-8')
    with pytest.raises((ValueError, OSError)):
        verify_r1_boundary(frozen)


@pytest.mark.parametrize('change', ['source_revision', 'native_root'])
def test_auto_cas_needs_original_source_revision_and_native_root(frozen, change):
    _, _, snapshot, state, document, directory = frozen
    changed = deepcopy(document)
    if change == 'source_revision':
        changed['scope']['product_center_revision'] += 1
    else:
        changed['preparation_root'] = {
            'schema_version': round1_workspace.PREPARATION_ROOT_SCHEMA,
            'status': 'UNKNOWN', 'reason': 'LEGACY_PREPARATION_ROOT_UNKNOWN',
            'execution_authority': False,
        }
    with pytest.raises(adoption.AdoptionBlocked, match='ADOPTION_CURRENT_R1_CAS_CHANGED'):
        adoption._verify_native_auto_r1(changed, state, snapshot, directory)


@pytest.mark.parametrize('projection', ['native', 'public_facts'])
def test_companion_accepts_only_native_root_or_exact_native_facts(frozen, projection):
    *_, document, directory = frozen
    native = round1_workspace._check_preparation_root(document)
    binding = native if projection == 'native' else round1_workspace.preparation_root_facts(document)
    adoption._verify_native_root_binding(document, native, binding)


@pytest.mark.parametrize('change', [
    'claimed_count', 'claimed_limit', 'claimed_coverage', 'authority',
    'root_id', 'missing_projection_field', 'extra_field', 'source_root',
])
def test_companion_root_cannot_add_authority_or_drift_lineage(frozen, change):
    *_, document, directory = frozen
    native = round1_workspace._check_preparation_root(document)
    binding = round1_workspace.preparation_root_facts(document)
    if change == 'claimed_count': binding['confirmed_write_count'] = 0
    elif change == 'claimed_limit': binding['maximum_confirmed_writes'] = 1
    elif change == 'claimed_coverage': binding['coverage_authority'] = 'COMPLETE'
    elif change == 'authority': binding['execution_authority'] = True
    elif change == 'root_id': binding['root_id'] = 'r1-cycle:'+'f'*32
    elif change == 'missing_projection_field': binding.pop('coverage_authority')
    elif change == 'extra_field': binding['caller_authority'] = 'TRUSTED'
    else: native = {**native, 'root_id': 'r1-cycle:'+'f'*32}
    with pytest.raises(adoption.AdoptionBlocked, match='ADOPTION_EXTERNAL_SOURCE_BINDING_CHANGED'):
        adoption._verify_native_root_binding(document, native, binding)
