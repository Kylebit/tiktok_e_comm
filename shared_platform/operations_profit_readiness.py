"""Read-only, offline preflight for a frozen monthly profit input bundle.

This is a diagnostic boundary, not a worker or an authorization to calculate.
It never asks a provider for data, persists a task, or writes a report. A passing
result establishes only that local inputs satisfy the checked contract; source
authenticity and final arithmetic still belong to the fixed producer and audit.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json

from domains.data_operations.profit_settlement.monthly_missing_cost_scope import monthly_cost_period
from domains.data_operations.profit_settlement.shared_inputs import CostSnapshot, FxSnapshot
from domains.data_operations.profit_settlement.tiktok_coverage import build_coverage
from shared_platform.internal_catalog_sku import internal_sku
from shared_platform.operations_profit_producer import SOURCE_KEYS
from shared_platform.workbench_profit_adapter import _coverage_orders, _datetime, _read, _source_value


def _month_bounds(month):
    if not isinstance(month, str) or len(month) != 7:
        raise ValueError('explicit YYYY-MM task month required')
    start = date.fromisoformat(month + '-01')
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return start, end


def _check_entry(entry, start, month_end):
    platform, site, shop = entry['platform'], entry['site'], str(entry['shop_id'])
    full = _read(entry['month_coverage'])
    _, local_end = monthly_cost_period(site, start, month_end,
                                       full['created_period']['timezone'])
    site_zone = local_end.tzinfo
    def local_date(value):
        return _datetime(value).astimezone(site_zone).date()
    settlement = _read(entry['settlement'])
    if (not isinstance(settlement, dict) or settlement.get('schema_version') != 'settlement-evidence/v1'
            or settlement.get('status') != 'ready' or settlement.get('issues')
            or (settlement.get('platform'), settlement.get('site'), str(settlement.get('shop_id'))) != (platform, site, shop)
            or not isinstance(settlement.get('orders'), list)):
        raise ValueError('settlement evidence is incomplete or targets a different shop')
    digest = hashlib.sha256(json.dumps(settlement['orders'], ensure_ascii=False, sort_keys=True,
                                   separators=(',', ':')).encode()).hexdigest()
    if settlement.get('checksum') != digest or settlement.get('snapshot_id') != platform + '-settlement:' + digest:
        raise ValueError('settlement source checksum or snapshot identity mismatch')
    if {str(r.get('shop_id') or '') for r in settlement['orders']} != {shop}:
        raise ValueError('settlement orders do not bind one exact shop')
    observed = date.fromisoformat(full['settlement_observed_through'])
    if any(not r.get('settled_at') or local_date(r['settled_at']) > observed
           for r in settlement['orders']):
        raise ValueError('Finance settlement occurred after declared observation date')

    original = _read(entry['orders_source'])
    if (not isinstance(original, dict) or (original.get('platform'), original.get('site'), str(original.get('shop_id'))) != (platform, site, shop)
            or original.get('period') != {'start': start.isoformat(), 'end': month_end.isoformat()}
            or original.get('complete') is not True or original.get('next_cursor') is not None
            or not isinstance(original.get('orders'), list)
            or original.get('total_count') != len(original['orders']) or not original.get('source')):
        raise ValueError('complete original monthly order export is required')
    full_orders, full_rebuilt = _coverage_orders(full, platform=platform, site=site,
        start=start, end=month_end, settlement=settlement)
    if any(not start <= local_date(r['order_created_at']) <= month_end
           for r in original['orders'] + full_orders):
        raise ValueError('original order falls outside site-local requested month')
    original_coverage = build_coverage(orders=original['orders'],
        settled_order_ids={str(r['order_id']) for r in settlement['orders'] if r.get('order_id')},
        start=start, end=month_end, as_of=date.fromisoformat(full['settlement_observed_through']),
        settlement_snapshot_id=settlement['snapshot_id'], site=site,
        timezone_name=full['created_period']['timezone'],
        date_basis=full.get('date_basis') or 'legacy_timestamp_date')
    if original_coverage['checksum'] != full_rebuilt['checksum']:
        raise ValueError('monthly coverage omits original created orders')
    missing = [local_date(r['order_created_at']) for r in full_rebuilt['unsettled_non_cancelled_orders']]
    cutoff = min(missing) - timedelta(days=1) if missing else month_end
    if cutoff < start:
        raise ValueError('no consecutive fully settled created-order date yet')
    if observed < cutoff:
        raise ValueError('settlement observation does not reach proposed cutoff')
    coverage = _read(entry['coverage'])
    if (coverage.get('schema_version') != full.get('schema_version')
            or coverage.get('date_basis') != full.get('date_basis')):
        raise ValueError('cutoff and full-month coverage date basis differ')
    cutoff_orders, rebuilt = _coverage_orders(coverage, platform=platform, site=site,
        start=start, end=cutoff, settlement=settlement)
    expected = [r for r in full_orders if local_date(r['order_created_at']) <= cutoff]
    if (sorted(cutoff_orders, key=lambda r: r['order_id']) != sorted(expected, key=lambda r: r['order_id'])
            or not rebuilt['all_non_cancelled_orders_settled']
            or coverage['settlement_observed_through'] != full['settlement_observed_through']):
        raise ValueError('cutoff coverage is not the latest consecutive settled prefix')
    if any(not start <= local_date(r['order_created_at']) <= cutoff for r in cutoff_orders):
        raise ValueError('cutoff coverage contains order outside site-local settled prefix')

    costs_doc, fx_doc = _read(entry['costs']), _read(entry['fx'])
    if not isinstance(fx_doc.get('source'), str) or not fx_doc['source'].strip():
        raise ValueError('FX snapshot has no explicit source')
    costs = CostSnapshot.from_mapping(costs_doc['records'], snapshot_id=costs_doc['snapshot_id'])
    fx = FxSnapshot.from_mapping(fx_doc['rates_cny'], source=fx_doc['source'],
                                 as_of=fx_doc['as_of'], snapshot_id=fx_doc['snapshot_id'])
    _datetime(fx.as_of)
    settled_ids = {r['order_id'] for r in rebuilt['settled_orders']}
    expected_lines = set()
    for order in original['orders']:
        if order['order_id'] not in settled_ids:
            continue
        if not isinstance(order.get('lines'), list) or not order['lines']:
            raise ValueError('settled original order has no item lines')
        for line in order['lines']:
            sku = internal_sku(line.get('seller_sku'))
            key = (str(order['order_id']), str(line.get('order_line_id') or ''))
            record = costs.get(sku)
            raw_cost = costs_doc['records'].get(sku)
            if (not key[1] or key in expected_lines or len(sku) != 4 or not sku.isdigit()
                    or str(line.get('canonical_sku')) != sku or record is None
                    or not isinstance(raw_cost, dict)
                    or not isinstance(raw_cost.get('source'), str) or not raw_cost['source'].strip()
                    or record.version == 'unspecified' or not record.source
                    or local_date(record.effective_at) > local_date(order['order_created_at'])):
                raise ValueError('historical SKU cost or original order line is missing or ambiguous')
            quantity = Decimal(str(line.get('quantity')))
            if not quantity.is_finite() or quantity <= 0:
                raise ValueError('original order line quantity is invalid')
            expected_lines.add(key)
    rows = _read(entry['rows'])
    if not isinstance(rows, list) or not rows:
        raise ValueError('normalized settled rows are missing')
    row_lines = {(str(r.get('order_id')), str(r.get('order_line_id'))) for r in rows if isinstance(r, dict)}
    if (len(row_lines) != len(rows) or row_lines != expected_lines
            or any(r.get('shop_id') != shop or r.get('region') != site for r in rows)):
        raise ValueError('normalized rows do not bind every original settled line')
    if any(fx.get(str(r.get('currency'))) is None for r in rows):
        raise ValueError('FX snapshot lacks a currency used by settled rows')

    ads = _read(entry['advertising'])
    if (not isinstance(ads, dict) or ads.get('mode') != 'actual'
            or (ads.get('platform'), ads.get('site'), str(ads.get('shop_id'))) != (platform, site, shop)
            or ads.get('period') != {'start': start.isoformat(), 'end': cutoff.isoformat()}
            or not ads.get('source') or not ads.get('snapshot_id')):
        raise ValueError('actual advertising source for exact cutoff is required')
    _datetime(ads['as_of'])
    ad_raw = _read(ads['source_file'])
    if any(ad_raw.get(k) != ads.get(k) for k in ('platform', 'site', 'shop_id', 'period')):
        raise ValueError('original advertising identity or period differs')
    amount = Decimal(str(_source_value(ad_raw, ads.get('amount_path'))))
    rate = fx.get(str(_source_value(ad_raw, ads.get('currency_path'))))
    if (not amount.is_finite() or amount < 0 or rate is None
            or amount * rate != Decimal(str(ads['total_cny']))):
        raise ValueError('actual advertising is not derived from original amount and FX')
    return {'platform': platform, 'site': site, 'shop_id': shop, 'cutoff': cutoff.isoformat(),
            'settlement_snapshot_id': settlement['snapshot_id'],
            'month_coverage_snapshot_id': full['snapshot_id']}


def inspect_offline_readiness(input_manifest, scope, *, adapter_available=False, prior_attempt_unknown=False):
    """Inspect frozen local files without worker launch, output files, or DB writes.

    ``INPUT_CONTRACT_VALID`` is deliberately weaker than profit completion or
    source authenticity. ``adapter_available`` is an explicit caller assertion,
    never inferred from this module or the presence of historical reports.
    """
    if prior_attempt_unknown:
        return {'status': 'BLOCKED', 'reason': 'PRIOR_ATTEMPT_UNKNOWN', 'targets': []}
    if not adapter_available:
        return {'status': 'BLOCKED', 'reason': 'WORKER_ADAPTER_UNAVAILABLE', 'targets': []}
    if input_manifest is None:
        return {'status': 'BLOCKED', 'reason': 'INPUT_MANIFEST_MISSING', 'targets': []}
    try:
        start, end = _month_bounds(scope['month'])
        platforms, sites, shops = scope['platforms'], scope['sites'], scope['shops']
        if (not all(isinstance(x, list) and x for x in (platforms, sites, shops))
                or set(platforms) - {'tiktok', 'shopee'} or set(sites) - {'MY', 'TH', 'VN', 'PH'}
                or len(set(platforms)) != len(platforms) or len(set(sites)) != len(sites)
                or len(set(shops)) != len(shops)):
            raise ValueError('exact resolved platform, site and shop scope required')
        manifest = _read(input_manifest)
        if (not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'reports'}
                or manifest['schema_version'] != 'profit-producer-input/v1'
                or not isinstance(manifest['reports'], list) or not manifest['reports']):
            raise ValueError('profit-producer-input/v1 manifest required; historical HTML is not input')
        targets, seen = [], set()
        for entry in manifest['reports']:
            if not isinstance(entry, dict) or set(entry) != {'platform', 'site', 'shop_id', 'rows', *SOURCE_KEYS}:
                raise ValueError('producer entry requires exactly the frozen input references')
            key = (entry['platform'], entry['site'], str(entry['shop_id']))
            if key in seen or key[0] not in platforms or key[1] not in sites or key[2] not in shops:
                raise ValueError('duplicate or outside-scope producer target')
            seen.add(key)
            targets.append(_check_entry(entry, start, end))
        if ({(p, s) for p, s, _ in seen} != {(p, s) for p in platforms for s in sites}
                or {shop for _, _, shop in seen} != set(shops)):
            raise ValueError('producer targets do not cover exact requested scope')
        return {'status': 'INPUT_CONTRACT_VALID', 'reason': None, 'targets': targets,
                'source_authenticity_verified': False, 'profit_calculated': False}
    except (OSError, ValueError, KeyError, TypeError, IndexError, ArithmeticError) as exc:
        return {'status': 'BLOCKED', 'reason': 'INPUT_CONTRACT_INVALID',
                'detail': str(exc), 'targets': [], 'profit_calculated': False}
