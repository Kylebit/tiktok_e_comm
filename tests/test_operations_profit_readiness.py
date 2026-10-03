"""Offline-only counterexamples for profit input readiness."""
from datetime import date
from hashlib import sha256
import json
from pathlib import Path

import pytest

from test_operations_profit_producer import inputs
from domains.data_operations.profit_settlement.tiktok_coverage import build_coverage
from shared_platform.operations_profit_readiness import inspect_offline_readiness


def _fixture(tmp_path):
    bundle, manifest, scope, _ = inputs(tmp_path)
    return bundle, manifest, scope


def _rewrite(bundle, manifest, mutate):
    data = json.loads(Path(manifest['path']).read_text(encoding='utf-8'))
    mutate(data['reports'][0])
    return bundle['write']('producer-input.json', data)


def test_readiness_does_not_equate_synthetic_inputs_with_profit_or_source_authenticity(tmp_path):
    _, manifest, scope = _fixture(tmp_path)
    result = inspect_offline_readiness(manifest, scope, adapter_available=True)
    assert result['status'] == 'INPUT_CONTRACT_VALID', result
    assert result['targets'][0]['cutoff'] == '2026-08-31'
    assert result['profit_calculated'] is False
    assert result['source_authenticity_verified'] is False
    assert not (tmp_path / 'producer-receipt.json').exists()


def test_july_archive_is_not_an_input_or_an_adapter(tmp_path):
    _, manifest, scope = _fixture(tmp_path)
    assert inspect_offline_readiness(manifest, scope)['reason'] == 'WORKER_ADAPTER_UNAVAILABLE'
    historical = tmp_path / '2026-07.html'
    historical.write_text('<h1>July complete report</h1>', encoding='utf-8')
    from hashlib import sha256
    ref = {'path': str(historical), 'sha256': sha256(historical.read_bytes()).hexdigest()}
    result = inspect_offline_readiness(ref, scope, adapter_available=True)
    assert result['status'] == 'BLOCKED'
    assert result['reason'] == 'INPUT_CONTRACT_INVALID'
    assert not any(tmp_path.rglob('producer-receipt.json'))


def test_unknown_attempt_blocks_even_when_inputs_are_locally_complete(tmp_path):
    _, manifest, scope = _fixture(tmp_path)
    assert inspect_offline_readiness(manifest, scope, adapter_available=True,
                                     prior_attempt_unknown=True)['reason'] == 'PRIOR_ATTEMPT_UNKNOWN'


@pytest.mark.parametrize('missing', ['settlement', 'month_coverage', 'orders_source', 'costs', 'fx', 'advertising'])
def test_missing_frozen_source_ref_blocks(tmp_path, missing):
    bundle, manifest, scope = _fixture(tmp_path)
    manifest = _rewrite(bundle, manifest, lambda entry: entry.pop(missing))
    result = inspect_offline_readiness(manifest, scope, adapter_available=True)
    assert result['status'] == 'BLOCKED'
    assert result['reason'] == 'INPUT_CONTRACT_INVALID'


def test_incomplete_created_order_export_blocks(tmp_path):
    bundle, manifest, scope = _fixture(tmp_path)
    def mutate(entry):
        doc = json.loads(Path(entry['orders_source']['path']).read_text(encoding='utf-8'))
        doc['next_cursor'] = 'more-orders'
        entry['orders_source'] = bundle['write']('original-orders.json', doc)
    result = inspect_offline_readiness(_rewrite(bundle, manifest, mutate), scope, adapter_available=True)
    assert result['status'] == 'BLOCKED'
    assert 'complete original monthly order export' in result['detail']


def test_missing_historical_sku_cost_blocks(tmp_path):
    bundle, manifest, scope = _fixture(tmp_path)
    def mutate(entry):
        doc = json.loads(Path(entry['costs']['path']).read_text(encoding='utf-8'))
        doc['records'].clear()
        entry['costs'] = bundle['write']('costs.json', doc)
    result = inspect_offline_readiness(_rewrite(bundle, manifest, mutate), scope, adapter_available=True)
    assert result['status'] == 'BLOCKED'
    assert 'historical SKU cost' in result['detail']


def test_missing_settled_currency_fx_blocks(tmp_path):
    bundle, manifest, scope = _fixture(tmp_path)
    def mutate(entry):
        rows = json.loads(Path(entry['rows']['path']).read_text(encoding='utf-8'))
        rows[0]['currency'] = 'PHP'
        entry['rows'] = bundle['write']('rows.json', rows)
    result = inspect_offline_readiness(_rewrite(bundle, manifest, mutate), scope, adapter_available=True)
    assert result['status'] == 'BLOCKED'
    assert 'FX snapshot lacks' in result['detail']


def test_unsettled_first_day_has_no_safe_cutoff(tmp_path):
    bundle, manifest, scope = _fixture(tmp_path)
    def mutate(entry):
        full = json.loads(Path(entry['month_coverage']['path']).read_text(encoding='utf-8'))
        full['unsettled_non_cancelled_orders'] = [full['settled_orders'].pop()]
        entry['month_coverage'] = bundle['write']('month_coverage.json', full)
    result = inspect_offline_readiness(_rewrite(bundle, manifest, mutate), scope, adapter_available=True)
    assert result['status'] == 'BLOCKED'


def test_wrong_month_and_shop_are_not_reused(tmp_path):
    _, manifest, scope = _fixture(tmp_path)
    for change in ({'month': '2026-07'}, {'shops': ['other-shop']}):
        result = inspect_offline_readiness(manifest, {**scope, **change}, adapter_available=True)
        assert result['status'] == 'BLOCKED'


def test_used_cost_record_must_name_its_original_source(tmp_path):
    bundle, manifest, scope = _fixture(tmp_path)
    def mutate(entry):
        doc = json.loads(Path(entry['costs']['path']).read_text(encoding='utf-8'))
        doc['records']['0001'].pop('source')
        entry['costs'] = bundle['write']('costs.json', doc)
    result = inspect_offline_readiness(_rewrite(bundle, manifest, mutate), scope, adapter_available=True)
    assert result['status'] == 'BLOCKED'
    assert 'historical SKU cost' in result['detail']


def test_fx_snapshot_must_name_its_original_source(tmp_path):
    bundle, manifest, scope = _fixture(tmp_path)
    def mutate(entry):
        doc = json.loads(Path(entry['fx']['path']).read_text(encoding='utf-8'))
        doc['source'] = ''
        entry['fx'] = bundle['write']('fx.json', doc)
    result = inspect_offline_readiness(_rewrite(bundle, manifest, mutate), scope, adapter_available=True)
    assert result['status'] == 'BLOCKED'
    assert 'FX snapshot has no explicit source' in result['detail']


def test_observation_must_cover_actual_settlement_timestamp(tmp_path):
    bundle, manifest, scope = _fixture(tmp_path)
    def mutate(entry):
        doc = json.loads(Path(entry['month_coverage']['path']).read_text(encoding='utf-8'))
        doc['settlement_observed_through'] = '2026-08-01'
        for key in ('month_coverage', 'coverage'):
            entry[key] = bundle['write'](key + '.json', doc)
    result = inspect_offline_readiness(_rewrite(bundle, manifest, mutate), scope, adapter_available=True)
    assert result['status'] == 'BLOCKED'
    assert 'Finance settlement occurred after' in result['detail']


def test_utc_august_but_site_local_september_order_is_outside_month(tmp_path):
    bundle, manifest, scope = _fixture(tmp_path)
    def mutate(entry):
        created = '2026-08-31T18:30:00+00:00'  # 2026-09-01 in MY
        settlement = json.loads(Path(entry['settlement']['path']).read_text(encoding='utf-8'))
        settlement['orders'][0]['order_created_at'] = created
        digest = sha256(json.dumps(settlement['orders'], sort_keys=True,
            ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        settlement['checksum'] = digest
        settlement['snapshot_id'] = 'tiktok-settlement:' + digest
        entry['settlement'] = bundle['write']('settlement.json', settlement)
        original = json.loads(Path(entry['orders_source']['path']).read_text(encoding='utf-8'))
        original['orders'][0]['order_created_at'] = created
        entry['orders_source'] = bundle['write']('original-orders.json', original)
        coverage = build_coverage(orders=settlement['orders'], settled_order_ids={'o1'},
            start=date(2026, 8, 1), end=date(2026, 8, 31), as_of=date(2026, 9, 9),
            settlement_snapshot_id=settlement['snapshot_id'], site='MY',
            timezone_name='Asia/Kuala_Lumpur')
        coverage.update(platform='tiktok', schema_version='tiktok-order-settlement-coverage/v1')
        for key in ('month_coverage', 'coverage'):
            entry[key] = bundle['write'](key + '.json', coverage)
        rows = json.loads(Path(entry['rows']['path']).read_text(encoding='utf-8'))
        rows[0]['occurred_at'] = created
        rows[0]['source_snapshot_id'] = settlement['snapshot_id']
        entry['rows'] = bundle['write']('rows.json', rows)
    result = inspect_offline_readiness(_rewrite(bundle, manifest, mutate), scope, adapter_available=True)
    assert result['status'] == 'BLOCKED'
    assert 'legacy coverage crossing site date requires v2 reconciliation' in result['detail']
