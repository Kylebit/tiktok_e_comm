"""Versioned site-local coverage at calendar-month boundaries."""
from datetime import date
from hashlib import sha256
import json
from pathlib import Path

import pytest

from test_operations_profit_producer import inputs
from domains.data_operations.profit_settlement.tiktok_coverage import build_coverage
from shared_platform.workbench_profit_adapter import _coverage_orders
from shared_platform.operations_profit_producer import build_verified_reports


@pytest.mark.parametrize('site,zone,created', [
    ('MY', 'Asia/Kuala_Lumpur', '2026-07-31T18:30:00+00:00'),
    ('PH', 'Asia/Manila', '2026-07-31T18:30:00+00:00'),
    ('TH', 'Asia/Bangkok', '2026-07-31T18:30:00+00:00'),
    ('VN', 'Asia/Ho_Chi_Minh', '2026-07-31T18:30:00+00:00'),
])
def test_new_coverage_uses_site_local_august_first(site, zone, created):
    order = {'order_id': 'o1', 'order_created_at': created, 'order_status': 'COMPLETED'}
    coverage = build_coverage(orders=[order], settled_order_ids={'o1'},
        start=date(2026, 8, 1), end=date(2026, 8, 31), as_of=date(2026, 9, 9),
        settlement_snapshot_id='synthetic', site=site, timezone_name=zone,
        date_basis='site_local_order_created_at')
    assert coverage['schema_version'].endswith('/v2')
    assert coverage['date_basis'] == 'site_local_order_created_at'
    settlement = {'snapshot_id': 'synthetic', 'orders': [{'order_id': 'o1'}]}
    orders, rebuilt = _coverage_orders(coverage, platform='tiktok', site=site,
        start=date(2026, 8, 1), end=date(2026, 8, 31), settlement=settlement)
    assert len(orders) == 1 and rebuilt['all_non_cancelled_orders_settled']


def test_legacy_v1_boundary_requires_rebuild_not_silent_reinterpretation():
    order = {'order_id': 'o1', 'order_created_at': '2026-07-31T18:30:00+00:00',
             'order_status': 'COMPLETED'}
    legacy = build_coverage(orders=[order], settled_order_ids={'o1'},
        start=date(2026, 8, 1), end=date(2026, 8, 31), as_of=date(2026, 9, 9),
        settlement_snapshot_id='synthetic', site='MY', timezone_name='Asia/Kuala_Lumpur')
    with pytest.raises(ValueError, match='v2 reconciliation'):
        _coverage_orders(legacy, platform='tiktok', site='MY',
            start=date(2026, 8, 1), end=date(2026, 8, 31),
            settlement={'snapshot_id': 'synthetic', 'orders': [{'order_id': 'o1'}]})


def test_v2_rejects_order_from_next_site_local_month_and_wrong_timezone():
    order = {'order_id': 'o1', 'order_created_at': '2026-08-31T18:30:00+00:00',
             'order_status': 'COMPLETED'}
    with pytest.raises(ValueError, match='site-local created period'):
        build_coverage(orders=[order], settled_order_ids={'o1'},
            start=date(2026, 8, 1), end=date(2026, 8, 31), as_of=date(2026, 9, 9),
            settlement_snapshot_id='synthetic', site='MY',
            timezone_name='Asia/Kuala_Lumpur', date_basis='site_local_order_created_at')
    with pytest.raises(ValueError, match='exact supported site'):
        build_coverage(orders=[order], settled_order_ids={'o1'},
            start=date(2026, 8, 1), end=date(2026, 8, 31), as_of=date(2026, 9, 9),
            settlement_snapshot_id='synthetic', site='MY',
            timezone_name='Asia/Bangkok', date_basis='site_local_order_created_at')


@pytest.mark.parametrize('platform', ['tiktok', 'shopee'])
def test_fixed_producer_keeps_local_august_first_for_both_platforms(tmp_path, platform):
    bundle, manifest, scope, row = inputs(tmp_path, platform=platform)
    doc = json.loads(Path(manifest['path']).read_text(encoding='utf-8'))
    entry = doc['reports'][0]
    created = '2026-07-31T18:30:00+00:00'  # August 1 in MY
    settlement = json.loads(Path(entry['settlement']['path']).read_text(encoding='utf-8'))
    settlement['orders'][0]['order_created_at'] = created
    digest = sha256(json.dumps(settlement['orders'], sort_keys=True,
        ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    settlement.update(checksum=digest, snapshot_id=platform + '-settlement:' + digest)
    entry['settlement'] = bundle['write']('settlement.json', settlement)
    original = json.loads(Path(entry['orders_source']['path']).read_text(encoding='utf-8'))
    original['orders'][0]['order_created_at'] = created
    entry['orders_source'] = bundle['write']('original-orders.json', original)
    coverage = build_coverage(orders=settlement['orders'], settled_order_ids={'o1'},
        start=date(2026, 8, 1), end=date(2026, 8, 31), as_of=date(2026, 9, 9),
        settlement_snapshot_id=settlement['snapshot_id'], site='MY',
        timezone_name='Asia/Kuala_Lumpur', date_basis='site_local_order_created_at')
    coverage.update(platform=platform, schema_version=platform + '-order-settlement-coverage/v2')
    for key in ('month_coverage', 'coverage'):
        entry[key] = bundle['write'](key + '.json', coverage)
    row.update(occurred_at=created, source_snapshot_id=settlement['snapshot_id'])
    entry['rows'] = bundle['write']('rows.json', [row])
    manifest = bundle['write']('producer-input.json', doc)
    receipt = build_verified_reports(manifest, scope, tmp_path / 'fixed', {'code_version': 'v2-fixture'})
    report = json.loads(Path(receipt['reports'][0]['path']).read_text(encoding='utf-8'))
    assert report['status'] == 'ready'
    assert len(report['order_lines']) == 1
    assert report['period']['timezone'] == 'Asia/Kuala_Lumpur'


def test_first_site_local_unsettled_date_sets_previous_local_cutoff(tmp_path):
    bundle, manifest, scope, _ = inputs(tmp_path)
    doc = json.loads(Path(manifest['path']).read_text(encoding='utf-8'))
    entry = doc['reports'][0]
    settlement = json.loads(Path(entry['settlement']['path']).read_text(encoding='utf-8'))
    pending = {'order_id': 'o2', 'shop_id': 'shop-MY',
               'order_created_at': '2026-08-19T18:30:00+00:00',  # MY August 20
               'order_status': 'DELIVERED', 'settlement_status': 'unsettled'}
    original = json.loads(Path(entry['orders_source']['path']).read_text(encoding='utf-8'))
    original['orders'].append(pending)
    original['total_count'] = 2
    entry['orders_source'] = bundle['write']('original-orders.json', original)
    full = build_coverage(orders=original['orders'], settled_order_ids={'o1'},
        start=date(2026, 8, 1), end=date(2026, 8, 31), as_of=date(2026, 9, 9),
        settlement_snapshot_id=settlement['snapshot_id'], site='MY',
        timezone_name='Asia/Kuala_Lumpur', date_basis='site_local_order_created_at')
    partial = build_coverage(orders=[original['orders'][0]], settled_order_ids={'o1'},
        start=date(2026, 8, 1), end=date(2026, 8, 19), as_of=date(2026, 9, 9),
        settlement_snapshot_id=settlement['snapshot_id'], site='MY',
        timezone_name='Asia/Kuala_Lumpur', date_basis='site_local_order_created_at')
    for key, coverage in [('month_coverage', full), ('coverage', partial)]:
        entry[key] = bundle['write'](key + '.json', coverage)
    ads = json.loads(Path(entry['advertising']['path']).read_text(encoding='utf-8'))
    ads['period']['end'] = '2026-08-19'
    raw_ads = json.loads(Path(ads['source_file']['path']).read_text(encoding='utf-8'))
    raw_ads['period']['end'] = '2026-08-19'
    ads['source_file'] = bundle['write']('original-ads.json', raw_ads)
    entry['advertising'] = bundle['write']('advertising.json', ads)
    manifest = bundle['write']('producer-input.json', doc)
    receipt = build_verified_reports(manifest, scope, tmp_path / 'cutoff', {'code_version': 'v2-fixture'})
    report = json.loads(Path(receipt['reports'][0]['path']).read_text(encoding='utf-8'))
    assert report['period']['end'] == '2026-08-19'
    assert len(report['order_lines']) == 1
