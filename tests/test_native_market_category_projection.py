"""Original owned official capture -> same-snapshot native category projection."""
from copy import deepcopy

import pytest

from shared_platform import publication_r3_image_bridge as bridge
from shared_platform.release_store import ReleaseStore
from test_round1_category_observations import context, capture, prepare_module
from test_round1_sidecar_convergence import image_plan
from test_round1_workspace_freeze import live


def _prepared(tmp_path, monkeypatch):
    data, preview, fake, store = context(tmp_path, monkeypatch)
    receipt = capture(data)
    packet = prepare_module().prepare_offer(offer_id=data['offer_id'],
        requested_targets=data['requested_targets'], preview_builder=lambda _: preview,
        image_execution_plan=image_plan(), category_source_region='MY',
        category_observation=receipt['observer_reference'],
        category_account_digest=data['account_identity_digest'])
    assert packet['status'] == 'FIRST_REVIEW_READY', packet
    assert packet['targets'][0]['category'] == {
        'status':'EVIDENCE_BOUND', 'id':receipt['category']['id'],
        'name':receipt['category']['name'], 'receipt_digest':receipt['receipt_digest']}
    # Owned old production schema; no source/capability marker is installed.
    with store._transaction():
        pass
    return store, packet, receipt, fake


def _project(store, db, packet, label='shopee:MY'):
    category = next(row['category'] for row in packet['targets'] if row['target'] == 'shopee:MY')
    return bridge._marketplace_category_candidate(label, category, first_review=packet,
        common_payload={}, category_store=store, category_connection=db)


def test_native_evidence_is_projected_in_existing_snapshot_without_source_mutation(tmp_path, monkeypatch):
    store, packet, receipt, fake = _prepared(tmp_path, monkeypatch)
    before_packet = deepcopy(packet)
    before_db = store.path.read_bytes()
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        monkeypatch.setattr(store, '_connect_readonly', lambda: pytest.fail('Must reuse the consuming SQLite snapshot'))
        value = _project(store, db, packet)
        assert value['candidate'] == str(receipt['category']['id']) + ' · ' + receipt['category']['name']
        assert value['evidence_digest'] == receipt['receipt_digest']
    assert packet == before_packet and store.path.read_bytes() == before_db
    assert len(fake.calls) == 2  # Only the original closed official capture.


def test_public_native_graph_consumes_real_bound_receipt_without_a_second_snapshot(live, monkeypatch, tmp_path):
    from test_native_common_retained_review_graph import _completed, _market
    from shared_platform.r3_frozen_review_producer import read_stored_domain_graph
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    store, common, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    market = _market(store, common, run_id)
    documents = bridge.load_r2_documents(payload['product_id'])
    source_bytes = deepcopy(documents['first_review'])
    assert any(row['category']['status'] == 'EVIDENCE_BOUND'
               for row in documents['first_review']['targets'] if row['target'].startswith('shopee:'))
    before = store.path.read_bytes()
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        monkeypatch.setattr(store, '_connect_readonly', lambda: pytest.fail('Graph must reuse its actual SQLite snapshot'))
        graph = read_stored_domain_graph(db, market['plan_id'], native_reader=NativeCommonSourceReader(store))
        assert graph.marketplace_plan_id == market['plan_id']
        assert graph.common_run_id == run_id
        assert graph.targets == tuple(documents['round1_snapshot']['canonical_targets'])
    assert store.path.read_bytes() == before
    assert bridge.load_r2_documents(payload['product_id'])['first_review'] == source_bytes


@pytest.mark.parametrize('case', ['id', 'name', 'receipt-digest', 'revision', 'source-region', 'account'])
def test_native_category_rejects_actual_receipt_or_review_drift(tmp_path, monkeypatch, case):
    store, packet, receipt, fake = _prepared(tmp_path, monkeypatch)
    category = packet['targets'][0]['category']
    if case == 'id': category['id'] += 1
    elif case == 'name': category['name'] += ' changed'
    elif case == 'receipt-digest': category['receipt_digest'] = 'sha256:' + 'a'*64
    elif case == 'revision': packet['product_center_revision'] += 1
    elif case == 'source-region': packet['category_review_context']['source_region'] = 'PH'
    else: packet['category_evidence_binding']['receipt']['account_identity_digest'] = 'sha256:' + 'b'*64
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        with pytest.raises(ValueError): _project(store, db, packet)
    assert len(fake.calls) == 2


@pytest.mark.parametrize('label', ['tiktok:LH_PH', 'ozon:RU'])
def test_native_shopee_evidence_cannot_be_borrowed_by_another_platform(tmp_path, monkeypatch, label):
    store, packet, _, _ = _prepared(tmp_path, monkeypatch)
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        with pytest.raises(ValueError, match='MARKETPLACE_BOUND_CATEGORY_SCOPE_INVALID'):
            _project(store, db, packet, label)


@pytest.mark.parametrize('case', ['foreign-store', 'missing-store', 'no-transaction'])
def test_native_category_requires_the_consuming_store_and_snapshot(tmp_path, monkeypatch, case):
    store, packet, _, _ = _prepared(tmp_path, monkeypatch)
    supplied = store
    if case != 'no-transaction':
        supplied = ReleaseStore(tmp_path / (case + '.sqlite3'))
        if case == 'foreign-store':
            with supplied._transaction(): pass
    with store._connect_readonly() as db:
        if case != 'no-transaction': db.execute('BEGIN')
        with pytest.raises(ValueError): _project(supplied, db, packet)
    if case == 'missing-store': assert not supplied.path.exists()


def test_revoked_original_record_cannot_be_replaced_by_self_consistent_json(tmp_path, monkeypatch):
    store, packet, receipt, _ = _prepared(tmp_path, monkeypatch)
    original_packet = deepcopy(packet)
    store.invalidate_round1_category_observation(receipt['observer_reference'], 'SOURCE_REVOKED')
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        with pytest.raises(ValueError): _project(store, db, packet)
    assert packet == original_packet


def test_existing_proposed_category_contract_stays_unchanged():
    category = {'status':'PROPOSED', 'candidate':'600338 · Wall Stickers',
                'evidence_digest':'sha256:'+'a'*64}
    assert bridge._marketplace_category_candidate('tiktok:LH_PH', category,
        first_review={}, common_payload={}) is category


def _quality_inputs(live, monkeypatch, tmp_path):
    from test_native_common_retained_review_graph import _completed
    from test_b4b_release_compiler import policy, INCIDENTS
    from shared_platform.ozon_runtime_credentials import PinnedOzonCredentials
    from shared_platform.ozon_stock_warehouse_decision import resolve_warehouse_decision
    store, common, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    documents = bridge.load_r2_documents(payload['product_id'])
    warehouse = resolve_warehouse_decision(offer_id=payload['product_id'],
        round1=documents['round1_snapshot'], pinned=PinnedOzonCredentials('12345','owned-secret','a'*64),
        post_bound=lambda path, body, **kwargs: {'warehouses':[
            {'warehouse_id':71,'status':'active','is_kgt':False}]})
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        result = bridge.build_marketplace_stage_payload(documents, store.get_plan(common['plan_id']),
            store.get_run(run_id), policy=policy(40), incidents=INCIDENTS,
            ozon_stock_decision=warehouse['decision'], category_store=store, category_connection=db)
    return store, result


def test_real_quality_consumer_preserves_frozen_route_and_projects_technical_category(live, monkeypatch, tmp_path):
    from domains.product_operations.approved_publication_snapshot import build_publication_preview
    store, payload = _quality_inputs(live, monkeypatch, tmp_path)
    before = deepcopy(payload)
    snapshot = build_publication_preview(payload)
    decision = snapshot['shopee_global_master']['category_decision']
    receipt = payload['r3_marketplace_binding']['documents']['first_review']['category_evidence_binding']['receipt']
    assert decision['status'] == 'EVIDENCE_BOUND' and decision['category']['id'] == '101157'
    assert decision['required_attributes'] == receipt['selected_attributes']
    assert decision['source_decision_digest'] == receipt['receipt_digest']
    assert snapshot['shopee_global_master']['policy']['warehouse']['location_id'] is None
    assert snapshot['shopee_global_master']['policy']['warehouse']['status'] == 'DEFERRED_TO_SKILL'
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        monkeypatch.setattr(store, '_connect_readonly', lambda: pytest.fail('Quality must reuse the consuming snapshot'))
        evidence = bridge.marketplace_quality_evidence(snapshot, payload,
            category_store=store, category_connection=db)
    for label in receipt['requested_targets']:
        category = evidence['publication_bridge']['target_facts'][label]['category']
        assert category['authority'] == receipt['authority']
        assert category['evidence_digest'] == receipt['receipt_digest']
        assert category['candidate'].startswith('101157 · ')
    assert evidence['platform_preflight']['status'] == 'PASSED', evidence['platform_preflight']
    assert payload == before
    assert 'approved_channel_category_decisions' not in payload
    assert payload['r3_marketplace_binding']['route']['target_facts']['shopee:PH']['category'] == {
        'status':'EVIDENCE_BOUND', 'id':101157, 'name':receipt['category']['name'], 'receipt_digest':receipt['receipt_digest']}


@pytest.mark.parametrize('case', ['missing-store', 'changed-route', 'revoked-receipt', 'wrong-store', 'changed-attributes', 'cross-platform', 'false-approved-status'])
def test_quality_consumer_cannot_adopt_caller_evidence_or_another_store(live, monkeypatch, tmp_path, case):
    from domains.product_operations.approved_publication_snapshot import build_publication_preview
    store, payload = _quality_inputs(live, monkeypatch, tmp_path)
    if case in {'changed-attributes', 'false-approved-status'}:
        decision = payload['shopee_global_master']['category_decision']
        decision['required_attributes'] = []
        if case == 'false-approved-status':
            decision['status'] = 'APPROVED'
        unsigned = {key:value for key,value in decision.items() if key != 'decision_digest'}
        decision['decision_digest'] = bridge._canonical_digest({'schema_version':'shopee-global-category-decision/v1', **unsigned})
    snapshot = build_publication_preview(payload)
    if case == 'changed-route':
        payload['r3_marketplace_binding']['route']['target_facts']['shopee:PH']['category']['name'] = 'caller changed'
    if case == 'cross-platform':
        route = payload['r3_marketplace_binding']['route']
        route['target_facts']['tiktok:LH_PH']['category'] = deepcopy(route['target_facts']['shopee:PH']['category'])
    if case == 'revoked-receipt':
        receipt = payload['r3_marketplace_binding']['documents']['first_review']['category_evidence_binding']['receipt']
        store.invalidate_round1_category_observation(receipt['observer_reference'], 'SOURCE_REVOKED')
    other = ReleaseStore(tmp_path/'wrong-quality.sqlite3') if case == 'wrong-store' else store
    if other is not store:
        with other._transaction(): pass
    with other._connect_readonly() as db:
        db.execute('BEGIN')
        expected_error = ('^SOURCE_UNVERIFIED$' if case == 'revoked-receipt' else
            '^MARKETPLACE_BOUND_CATEGORY_GLOBAL_FACTS_CHANGED$' if case in {'changed-attributes', 'false-approved-status'} else 'MARKETPLACE_BOUND_CATEGORY')
        with pytest.raises(ValueError, match=expected_error):
            bridge.marketplace_quality_evidence(snapshot, payload,
                category_store=None if case == 'missing-store' else other,
                category_connection=db)
