"""Fixed monthly calculator entrypoint. Agent output is input, never a report."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from shared_platform.workbench_profit_adapter import _coverage_orders, _datetime, _read, validate_reports


SOURCE_KEYS = ('settlement', 'coverage', 'month_coverage', 'orders_source', 'costs', 'fx', 'advertising')
POLICY_PATH = Path(__file__).resolve().parents[1] / 'domains/data_operations/skills/manage-profit-settlement/report-policy.json'


class AwaitingSettlement(ValueError):
    """The selected month has no fully settled consecutive reporting dates yet."""


def _encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def _ref(path):
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _persist(path, value):
    raw = _encoded(value)
    if path.is_symlink():
        raise ValueError('profit output cannot be a symlink')
    try:
        with path.open('xb') as handle:
            handle.write(raw)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ValueError('immutable producer output conflict: ' + path.name)
    return _ref(path)


def _producer_identity():
    from domains.data_operations.profit_settlement import shopee, tiktok, shared_inputs
    return {str(Path(module.__file__).name): hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (shopee, tiktok, shared_inputs)} | {
                'operations_profit_producer.py': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'report-policy.json': hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()}


def build_verified_reports(input_manifest, scope, output_dir, release_identity):
    """Calculate from a frozen ``profit-producer-input/v1`` manifest.

    Each reports entry has platform/site/shop_id, rows {path,sha256} (a JSON
    list of normalized domain rows), plus the seven original-source references
    in SOURCE_KEYS. It cannot contain an agent-written report. The existing
    domain source validator independently checks the produced results.
    """
    from domains.data_operations.profit_settlement import shopee, tiktok
    from domains.data_operations.profit_settlement.shared_inputs import CostSnapshot, FxSnapshot
    if not isinstance(release_identity, dict) or not release_identity.get('code_version'):
        raise ValueError('fixed producer requires current code release identity')
    manifest_ref = input_manifest if isinstance(input_manifest, dict) else _ref(Path(input_manifest))
    manifest = _read(manifest_ref)
    if (not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'reports'}
            or manifest['schema_version'] != 'profit-producer-input/v1'
            or not isinstance(manifest['reports'], list) or not manifest['reports']):
        raise ValueError('profit producer input manifest required')
    start = date.fromisoformat(scope['month'] + '-01')
    month_end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    identity = {'input_manifest': manifest_ref, 'scope': scope, 'release_identity': release_identity,
                'producer_sources': _producer_identity()}
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipt_path = output / 'producer-receipt.json'
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text(encoding='utf-8'))
        if previous.get('identity') != identity or previous.get('status') != 'VERIFIED':
            raise ValueError('producer receipt binds different inputs or release')
        _read(manifest_ref)
        for entry in manifest['reports']:
            _read(entry['rows'])
        for ref in previous['reports']:
            _read(ref)
        _read(previous['verification_manifest'])
        validate_reports([r['path'] for r in previous['reports']], scope,
                         manifest_path=previous['verification_manifest']['path'])
        return {**previous, 'receipt': _ref(receipt_path), 'reused': True,
                'evidence_paths': [r['path'] for r in previous['reports']] + [previous['verification_manifest']['path']]}
    seen, reports, entries, executions = set(), [], [], []
    for entry in manifest['reports']:
        if set(entry) != {'platform', 'site', 'shop_id', 'rows', *SOURCE_KEYS}:
            raise ValueError('producer report entry has unsupported inputs')
        platform, site, shop = entry['platform'], entry['site'], str(entry['shop_id'])
        key = (platform, site, shop)
        if key in seen or platform not in {'tiktok', 'shopee'}:
            raise ValueError('duplicate or unsupported producer target')
        seen.add(key)
        rows = _read(entry['rows'])
        if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
            raise ValueError('producer requires normalized source rows')
        for row in rows:
            if row.get('shop_id') != shop or row.get('region') != site:
                raise ValueError('normalized source row target mismatch')
        sources = {name: _read(entry[name]) for name in SOURCE_KEYS}
        full = sources['month_coverage']
        _coverage_orders(full, platform=platform, site=site, start=start,
                         end=month_end, settlement=sources['settlement'])
        from domains.data_operations.profit_settlement.monthly_missing_cost_scope import monthly_cost_period
        _, site_end = monthly_cost_period(site, start, month_end,
                                          full['created_period']['timezone'])
        missing = [_datetime(r['order_created_at']).astimezone(site_end.tzinfo).date()
                   for r in full['unsettled_non_cancelled_orders']]
        cutoff = min(missing) - timedelta(days=1) if missing else month_end
        if cutoff < start:
            raise AwaitingSettlement('所选月份尚无连续完全结算日期，请等待结算后提供更新的官方快照')
        if cutoff > month_end:
            raise ValueError('settlement coverage contains an invalid cutoff')
        observed = date.fromisoformat(full['settlement_observed_through'])
        costs = CostSnapshot.from_mapping(sources['costs']['records'], snapshot_id=sources['costs']['snapshot_id'])
        fxdoc = sources['fx']
        fx = FxSnapshot.from_mapping(fxdoc['rates_cny'], source=fxdoc['source'], as_of=fxdoc['as_of'], snapshot_id=fxdoc['snapshot_id'])
        policy=json.loads(POLICY_PATH.read_text(encoding='utf-8'))
        parameters = {'period_start': start.isoformat(), 'period_end': cutoff.isoformat(), 'period_basis': 'order_created_at',
                      'code_version': release_identity['code_version'], 'local_fulfillment_fee_cny': policy[platform]['local_fulfillment_fee_cny_per_order']}
        if full.get('schema_version') == platform + '-order-settlement-coverage/v2':
            parameters.update(period_site=site, period_timezone=full['created_period']['timezone'])
        # Stable source observation time makes crash/restart output reproducible.
        generated = datetime.combine(observed, datetime.min.time(), timezone.utc)
        builder = tiktok.build_monthly_report if platform == 'tiktok' else shopee.build_monthly_report
        report = builder(rows, costs=costs, fx=fx, actual_advertising=sources['advertising'],
                         generated_at=generated, **parameters).payload()
        report['source'].update(all_non_cancelled_orders_settled=sources['coverage']['all_non_cancelled_orders_settled'],
            coverage_snapshot_id=sources['coverage']['snapshot_id'], settlement_observed_through=observed.isoformat())
        name = platform + '_' + site + '_' + hashlib.sha256(shop.encode()).hexdigest()[:12] + '.json'
        ref = _persist(output / name, report)
        reports.append(ref)
        entries.append({k: deepcopy(v) for k, v in entry.items() if k != 'rows'} | {'report': ref})
        executions.append({'target': list(key), 'builder': builder.__module__ + '.build_monthly_report',
                           'parameters': parameters, 'normalized_rows': entry['rows'],
                           'source_refs': {k: entry[k] for k in SOURCE_KEYS}, 'report': ref})
    verification = _persist(output / 'verification-manifest.json', {'schema_version': 'profit-verification-manifest/v1', 'reports': entries})
    validate_reports([r['path'] for r in reports], scope, manifest_path=verification['path'])
    receipt = {'schema_version': 'profit-producer-receipt/v1', 'status': 'VERIFIED', 'identity': identity,
               'executions': executions, 'reports': reports, 'verification_manifest': verification,
               'external_write_count': 0}
    proof = _persist(receipt_path, receipt)
    return {**receipt, 'receipt': proof, 'reused': False,
            'evidence_paths': [r['path'] for r in reports] + [verification['path']]}
