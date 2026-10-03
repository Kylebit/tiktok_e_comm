import json
from pathlib import Path

import pytest

from shared_platform.frozen_sku_assignment import frozen_model_skus
from shared_platform.publication_rounds import canonical_digest
from shared_platform.release_control import build_release_dashboard
from test_release_control import _release_fixture


def fixture():
    directory = Path(__file__).parent / 'fixtures/b4b_common_actual'
    if not directory.exists():
        from test_b4b_common_stage import FIXTURE
        directory = FIXTURE
    snapshot = json.loads((directory / 'round1-approved-snapshot.json').read_text(encoding='utf-8'))
    facts = snapshot['fact_snapshot']['product_facts']
    state = {'offer_id': snapshot['offer_id'], 'product_approval': {
        'status': 'approved', 'approval_id': snapshot['product_approval_id'],
        'input_fingerprint': snapshot['product_approval_fingerprint']}, 'review': {
        'fields_locked': True, 'seller_sku': facts['seller_sku'],
        'selected_sites': snapshot['workbench_tiktok_sites'],
        'selected_sku_keys': [row['source_key'] for row in facts['skus']]}}
    source = {'skus': [{'key': row['source_key']} for row in facts['skus']]}
    return snapshot, state, source


def test_actual_round1_fixture_mapping_is_reused():
    snapshot, state, source = fixture()
    assert frozen_model_skus(snapshot, offer_id=state['offer_id'], state=state,
        source=source, seller_sku=state['review']['seller_sku']) == [
            row['seller_sku'] for row in snapshot['fact_snapshot']['product_facts']['skus']]


def test_multivariant_uses_source_key_not_position_or_parent_increment():
    snapshot, state, source = fixture()
    state['review']['selected_sku_keys'] = ['blue', 'red']
    source['skus'] = [{'key': 'red'}, {'key': 'blue'}]
    snapshot['fact_snapshot']['product_facts']['skus'] = [
        {'source_key': 'red', 'seller_sku': '098802'},
        {'source_key': 'blue', 'seller_sku': '098801'}]
    snapshot.pop('snapshot_digest')
    snapshot['snapshot_digest'] = canonical_digest(snapshot)
    assert frozen_model_skus(snapshot, offer_id=state['offer_id'], state=state,
        source=source, seller_sku=state['review']['seller_sku']) == ['098801', '098802']


@pytest.mark.parametrize('damage', ['digest', 'approval', 'selection', 'source', 'duplicate_source', 'scope', 'unlocked', 'duplicate_frozen'])
def test_mismatches_fail_closed(damage):
    snapshot, state, source = fixture()
    if damage == 'digest': snapshot['offer_id'] = '123'
    elif damage == 'approval': state['product_approval']['approval_id'] = 'other'
    elif damage == 'selection': state['review']['selected_sku_keys'] = ['other']
    elif damage == 'source': source['skus'] = []
    elif damage == 'duplicate_source': source['skus'] *= 2
    elif damage == 'scope': state['review']['selected_sites'] = ['other']
    elif damage == 'unlocked': state['review']['fields_locked'] = False
    else:
        snapshot['fact_snapshot']['product_facts']['skus'] *= 2
        snapshot.pop('snapshot_digest')
        snapshot['snapshot_digest'] = canonical_digest(snapshot)
    with pytest.raises(ValueError, match='FROZEN_R1'):
        frozen_model_skus(snapshot, offer_id=state['offer_id'], state=state,
            source=source, seller_sku=state['review']['seller_sku'])


def test_real_dashboard_preserves_frozen_model_without_inventing_predecessor(tmp_path, monkeypatch):
    from core import config
    monkeypatch.setattr(config, '_cache', {'exchange_rates': {'THB': 0.2}})
    root, db = _release_fixture(tmp_path)
    path = root / 'data/new_product_workbench/3828811808.json'
    state = json.loads(path.read_text(encoding='utf-8'))
    snapshot, _, _ = fixture()
    state['review'].update(fields_locked=True, seller_sku='0988')
    state['source'] = {'source_authority': '1688', 'skus': [{'key': 'size-large', 'name': 'Large'}]}
    state['product_approval'] = {'status': 'approved', 'approval_id': 'synthetic-approved',
                               'input_fingerprint': 'synthetic-fingerprint'}
    snapshot.update(offer_id=state['offer_id'], product_approval_id='synthetic-approved',
        product_approval_fingerprint='synthetic-fingerprint', workbench_tiktok_sites=['lh_th'])
    snapshot['fact_snapshot']['product_facts'].update(seller_sku='0988',
        skus=[{'source_key': 'size-large', 'seller_sku': '0988'}])
    snapshot.pop('snapshot_digest')
    snapshot['snapshot_digest'] = canonical_digest(snapshot)
    path.write_text(json.dumps(state), encoding='utf-8')
    args = dict(root=root, database_path=db, offer_id=state['offer_id'],
                report_store_path=root / 'data/nonexistent-release.db')
    legacy = build_release_dashboard(**args)
    frozen = build_release_dashboard(**args, frozen_round1=snapshot)
    assert legacy['_sku_lineage']['assignment']['model_skus'][0]['model_sku'] != '0988'
    assert frozen['_sku_lineage']['lineage_mode'] == 'NEW_SOURCE'
    assert frozen['_sku_lineage']['assignment']['model_skus'] == [
        {'variant_key': 'size-large', 'model_sku': '0988'}]
    assert frozen['product']['source_skus'][0]['model_sku'] == '0988'
    assert not args['report_store_path'].exists()
