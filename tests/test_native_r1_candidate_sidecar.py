"""Native candidate source observations only; no mutation admission or budget claim."""
import base64
import hashlib
import json

import pytest

from modules.products import server
from shared_platform import publication_rounds, release_control, round1_workspace
from test_round1_auto_freeze import complete_source, public_settings
from test_round1_workspace_freeze import live, prepared
from test_round1_sidecar_convergence import candidate


def _prepare_with_real_sidecar(live):
    complete_source(live)
    directory = publication_rounds.report_dir(live['offer'])
    dashboard = release_control.build_release_dashboard(offer_id=live['offer'])
    targets = dashboard['publication_scope']['selected_labels']
    assert 'shopee:MY' in targets
    plan = candidate('shopee:MY')
    plan['target_candidates'][0]['copy']['language'] = 'en'
    plan['target_candidates'][0]['copy']['title'] = dashboard['product']['title']
    plan['target_candidates'][0]['copy']['variants'][0]['seller_sku'] = dashboard['product']['seller_sku_candidate']
    expected = round1_workspace._module(server)._safe_candidate_plan(plan, targets)
    path = directory / 'first-review-candidate-plan.json'
    raw = (json.dumps(plan, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    path.write_bytes(raw)
    packet, request = prepared(live)
    record = round1_workspace.read_preparation(live['offer'], packet['prepared_reference'])
    assert record['packet'] == packet['packet']
    assert path.read_bytes() == raw
    return packet, request, record, path, raw, expected


def test_real_native_prepare_projects_fixed_sidecar_copy_and_retains_original_bytes(live):
    packet, _, record, _, raw, expected = _prepare_with_real_sidecar(live)
    actual = next(row for row in packet['packet']['targets'] if row['target'] == 'shopee:MY')
    # Meaningful old defect is the lost real candidate copy, not a missing new field.
    assert actual['copy'] == expected['target_candidates'][0]['copy']
    source = record['candidate_input']
    assert source['schema_version'] == 'round1-candidate-input/v1'
    assert source['present'] is True
    assert source['relative_name'] == 'first-review-candidate-plan.json'
    assert source['raw_sha256'] == hashlib.sha256(raw).hexdigest()
    assert source['byte_length'] == len(raw)
    assert base64.b64decode(source['raw_base64'], validate=True) == raw
    assert packet['packet']['external_write_count'] == 0


def test_native_prepared_source_rejects_byte_only_sidecar_drift(live):
    packet, _, record, path, raw, _ = _prepare_with_real_sidecar(live)
    changed = raw + b'\n'
    assert changed != raw and json.loads(changed) == json.loads(raw)
    path.write_bytes(changed)
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_CANDIDATE_INPUT_CHANGED'):
        round1_workspace.status(server, live['offer'], packet['prepared_reference'])
    assert round1_workspace.read_preparation(live['offer'], packet['prepared_reference']) == record
    assert path.read_bytes() == changed
