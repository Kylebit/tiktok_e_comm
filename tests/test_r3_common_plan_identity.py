from copy import deepcopy

import pytest

from modules.products import server
from shared_platform import publication_r3_image_bridge as bridge
from test_b4b_common_stage import context, approve


def plans(tmp_path, monkeypatch):
    documents, _, store, request = context(tmp_path, monkeypatch)
    exact = approve(request)
    stored = store.get_plan(exact['plan_id'])
    dashboard, error = server._release_dashboard_for_request(exact)
    assert error is None
    raw, blockers = server._release_plan_payload_from_dashboard(dashboard)
    assert not blockers
    return documents, store, stored, raw


def test_real_common_producer_matches_its_stored_id(tmp_path, monkeypatch):
    _, store, stored, raw = plans(tmp_path, monkeypatch)
    assert server._approved_plan_matches_current_payload(stored, store.preview_plan(raw), current_source_payload=raw)


@pytest.mark.parametrize('damage', ['forged_id', 'offer', 'facts', 'stage', 'scope', 'targets', 'missing_binding', 'revision', 'lineage'])
def test_common_identity_cannot_use_same_id_or_stage_to_hide_drift(tmp_path, monkeypatch, damage):
    _, store, stored, raw = plans(tmp_path, monkeypatch)
    changed = deepcopy(raw)
    if damage == 'forged_id':
        stored = deepcopy(stored); stored['payload']['plan_id'] = changed['plan_id'] = 'r3-common:9000052:forged'
    elif damage == 'offer': changed['product_id'] = '123'
    elif damage == 'facts': changed['product_facts']['title'] = 'Different'
    elif damage == 'stage': changed['r3_stage_binding']['schema_version'] = 'r3-marketplace-stage/v1'
    elif damage == 'scope': changed['r3_stage_binding']['execution_scope'] = ['tiktok:LH_PH']
    elif damage == 'targets': changed['targets'].append('tiktok:LH_PH')
    elif damage == 'missing_binding': changed.pop('r3_stage_binding')
    elif damage == 'revision': changed['product_revision'] += 1
    else: changed['sku_lineage']['reservation']['idempotent'] = True
    assert not server._approved_plan_matches_current_payload(stored, store.preview_plan(changed), current_source_payload=changed)


def test_actual_inherited_resolution_reuses_original_common_lineage(tmp_path, monkeypatch):
    from shared_platform import release_store, release_control
    documents, store, stored, raw = plans(tmp_path, monkeypatch)
    identity = release_store._source_identity_contract(raw['source_product_identity'])
    context = store.source_sku_lineage_context(source_offer_id=identity.source_offer_id,
        source_authority=identity.source_authority, source_identity_digest=identity.identity_digest)
    inherited = release_control.resolve_sku_lineage_reservation(source_identity=identity,
        predecessor_records=context['predecessor_records'], existing_reservations=context['existing_reservations'])
    assert inherited.ready and inherited.lineage_mode == 'INHERITED_PREDECESSOR'
    next_payload = deepcopy(raw); next_payload['sku_lineage'] = inherited.payload()
    repeated = bridge.bind_common_stage_payload(next_payload, documents, store=store)
    assert repeated['sku_lineage'] == stored['payload']['sku_lineage']
    assert repeated['plan_id'] == stored['plan_id']
    assert server._approved_plan_matches_current_payload(stored, store.preview_plan(repeated), current_source_payload=repeated)
