"""Monthly Skill agent execution and deterministic report acceptance.

No weekly calculator fallback and no completion based only on agent prose.
"""
from __future__ import annotations
import hashlib
import json
import re
import uuid
from pathlib import Path
from datetime import date, datetime, timedelta
from decimal import Decimal


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
    with path.open('xb') as stream:
        stream.write(raw)
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def _read(ref):
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
        raise ValueError('evidence reference requires exact path and SHA-256')
    raw = Path(ref['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('frozen profit evidence changed')
    return json.loads(raw)


def _scope_sha256(scope):
    raw = json.dumps(scope, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _begin_agent_attempt(engine, task, token, profile, input_count):
    """Atomically reserve one agent launch in the task ledger before subprocess work."""
    task_id = task['task_id']
    attempt_id = uuid.uuid4().hex
    output = (Path(profile.data_root).resolve() / 'artifacts' / task_id /
              ('monthly-' + attempt_id))
    receipt = {
        'schema_version': 'profit-agent-attempt/v1', 'task_id': task_id,
        'release': task['version'], 'scope_sha256': _scope_sha256(task['scope']),
        'output_dir': str(output), 'attempt_id': attempt_id,
        'input_count': input_count,
    }
    with engine.transaction() as conn:
        row = engine._lease(conn, task_id, token)
        current = json.loads(row['checkpoint_json'])
        if (row['template'] != 'profit' or json.loads(row['version_json']) != task['version']
                or _scope_sha256(json.loads(row['scope_json'])) != receipt['scope_sha256']):
            raise ValueError('profit attempt task identity changed')
        prior = current.get('attempt_receipt') or {}
        if (current.get('agent_attempt_started') or current.get('agent_unknown')
                or (prior and current.get('input_count', 0) >= input_count)):
            return None
        started = {**current, 'agent_attempt_started': True,
                   'agent_unknown': False, 'agent_result': None,
                   'attempt_output': str(output), 'input_count': input_count,
                   'attempt_receipt': receipt}
        conn.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?',
                     (json.dumps(started, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False), task_id))
        engine._event(conn, task_id, 'checkpoint_saved', {'checkpoint': started})
    return output, started


def _datetime(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('official date must include timezone')
    return result


def _source_value(document, pointer):
    if not isinstance(pointer, list) or not pointer:
        raise ValueError('advertising requires a deterministic original source field path')
    for part in pointer:
        if isinstance(document, list) and isinstance(part, int):
            document = document[part]
        elif isinstance(document, dict) and isinstance(part, str):
            document = document[part]
        else:
            raise ValueError('invalid original advertising field path')
    return document


def _manifest(paths):
    matches = []
    for raw in paths:
        path = Path(raw)
        if path.suffix == '.json' and path.is_file():
            body = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(body, dict) and body.get('schema_version') == 'profit-verification-manifest/v1':
                matches.append((path, body))
    if len(matches) != 1 or not isinstance(matches[0][1].get('reports'), list):
        raise ValueError('monthly verification requires one source manifest, not an agent assertion')
    return matches[0]


def _coverage_orders(coverage, *, platform, site, start, end, settlement):
    from domains.data_operations.profit_settlement.monthly_missing_cost_scope import monthly_cost_period
    from domains.data_operations.profit_settlement.tiktok_coverage import SITE_LOCAL_BASIS
    version = coverage.get('schema_version')
    if version == platform + '-order-settlement-coverage/v2' and coverage.get('date_basis') == SITE_LOCAL_BASIS:
        basis = SITE_LOCAL_BASIS
    elif version == platform + '-order-settlement-coverage/v1' and 'date_basis' not in coverage:
        basis = 'legacy_timestamp_date'
    else:
        raise ValueError('coverage date basis/version requires explicit reconciliation')
    _, period_end = monthly_cost_period(site, start, end,
                                        coverage.get('created_period', {}).get('timezone'))
    zone = period_end.tzinfo
    if (coverage.get('platform') != platform or coverage.get('site') != site
            or coverage.get('created_period', {}).get('start') != start.isoformat()
            or coverage.get('created_period', {}).get('end') != end.isoformat()
            or coverage.get('settlement_snapshot_id') != settlement.get('snapshot_id')):
        raise ValueError('coverage identity/period/settlement snapshot mismatch')
    date.fromisoformat(coverage['settlement_observed_through'])
    groups = [coverage.get(k) for k in ('settled_orders','cancelled_without_settlement_orders','unsettled_non_cancelled_orders')]
    if any(not isinstance(group, list) for group in groups):
        raise ValueError('coverage needs complete redacted order lists')
    orders = [r for group in groups for r in group]
    ids = [r['order_id'] for r in orders]
    if len(set(ids)) != len(ids):
        raise ValueError('coverage contains duplicate order identities')
    for row in orders:
        stamp = _datetime(row['order_created_at'])
        local_day = stamp.astimezone(zone).date()
        if basis == 'legacy_timestamp_date' and stamp.date() != local_day:
            raise ValueError('legacy coverage crossing site date requires v2 reconciliation')
        if not start <= local_day <= end:
            raise ValueError('coverage contains orders outside its declared period')
    settled_ids = {str(r.get('order_id')) for r in settlement.get('orders', []) if r.get('order_id')}
    from domains.data_operations.profit_settlement.tiktok_coverage import build_coverage
    rebuilt = build_coverage(orders=orders, settled_order_ids=settled_ids, start=start, end=end,
        as_of=date.fromisoformat(coverage['settlement_observed_through']),
        settlement_snapshot_id=settlement['snapshot_id'], site=site,
        timezone_name=coverage['created_period']['timezone'], date_basis=basis)
    for field in ('counts','all_non_cancelled_orders_settled','settled_orders','cancelled_without_settlement_orders','unsettled_non_cancelled_orders','checksum'):
        if coverage.get(field) != rebuilt[field]:
            raise ValueError('coverage disagrees with underlying order/settlement evidence: ' + field)
    return orders, rebuilt


def _verify_sources(report, entry, scope):
    from domains.data_operations.profit_settlement.shared_inputs import CostSnapshot, FxSnapshot
    platform, site, shop = entry['platform'], entry['site'], str(entry['shop_id'])
    if not shop or (scope.get('shops') and shop not in scope['shops']):
        raise ValueError('report shop is outside exact requested shop scope')
    start, end = date.fromisoformat(report['period']['start']), date.fromisoformat(report['period']['end'])
    month_end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    if start.day != 1 or start > end or end > month_end:
        raise ValueError('invalid monthly cutoff')
    source = report['source']
    settlement = _read(entry['settlement'])
    if (settlement.get('schema_version') != 'settlement-evidence/v1' or settlement.get('status') != 'ready'
            or settlement.get('issues') or settlement.get('platform') != platform or settlement.get('site') != site
            or not settlement.get('snapshot_id')):
        raise ValueError('settlement evidence is incomplete or mismatched')
    checksum = hashlib.sha256(json.dumps(settlement.get('orders'), ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if settlement.get('checksum') != checksum or settlement['snapshot_id'] != platform + '-settlement:' + checksum:
        raise ValueError('settlement content checksum mismatch')
    source_shops = {str(r.get('shop_id') or settlement.get('shop_id') or '') for r in settlement.get('orders', []) if r.get('order_id')}
    if source_shops != {shop}:
        raise ValueError('settlement source does not prove one exact shop')
    coverage, full = _read(entry['coverage']), _read(entry['month_coverage'])
    if (coverage.get('schema_version') != full.get('schema_version')
            or coverage.get('date_basis') != full.get('date_basis')):
        raise ValueError('cutoff and full-month coverage date basis differ')
    if (full.get('date_basis') == 'site_local_order_created_at'
            and report['period'].get('timezone') != full['created_period']['timezone']):
        raise ValueError('report timezone differs from versioned site-local coverage')
    original_orders = _read(entry['orders_source'])
    if (original_orders.get('platform') != platform or original_orders.get('site') != site
            or str(original_orders.get('shop_id')) != shop
            or original_orders.get('period') != {'start': start.isoformat(), 'end': month_end.isoformat()}
            or original_orders.get('complete') is not True
            or original_orders.get('next_cursor') is not None
            or not isinstance(original_orders.get('orders'), list)
            or original_orders.get('total_count') != len(original_orders['orders'])
            or not original_orders.get('source')):
        raise ValueError('complete original monthly order export identity/pagination evidence missing')
    orders, rebuilt = _coverage_orders(coverage, platform=platform, site=site, start=start, end=end, settlement=settlement)
    all_orders, full_rebuilt = _coverage_orders(full, platform=platform, site=site, start=start, end=month_end, settlement=settlement)
    from domains.data_operations.profit_settlement.tiktok_coverage import build_coverage
    basis = full.get('date_basis') or 'legacy_timestamp_date'
    from domains.data_operations.profit_settlement.monthly_missing_cost_scope import monthly_cost_period
    _, site_end = monthly_cost_period(site, start, month_end, full['created_period']['timezone'])
    def local_date(value):
        return _datetime(value).astimezone(site_end.tzinfo).date()
    original_coverage = build_coverage(orders=original_orders['orders'],
        settled_order_ids={str(r['order_id']) for r in settlement['orders'] if r.get('order_id')},
        start=start, end=month_end, as_of=date.fromisoformat(full['settlement_observed_through']),
        settlement_snapshot_id=settlement['snapshot_id'], site=site,
        timezone_name=full['created_period']['timezone'], date_basis=basis)
    if original_coverage['checksum'] != full_rebuilt['checksum']:
        raise ValueError('monthly coverage excludes orders from the complete original export')
    cutoff_orders = [r for r in all_orders if local_date(r['order_created_at']) <= end]
    if sorted(orders, key=lambda r:r['order_id']) != sorted(cutoff_orders, key=lambda r:r['order_id']):
        raise ValueError('cutoff coverage omits orders from the full month')
    missing_dates = [local_date(r['order_created_at']) for r in full_rebuilt['unsettled_non_cancelled_orders']]
    cutoff = min(missing_dates) - timedelta(days=1) if missing_dates else month_end
    if end != cutoff or not rebuilt['all_non_cancelled_orders_settled']:
        raise ValueError('report is not the latest contiguous fully settled created-date cutoff')
    if (source.get('coverage_snapshot_id') != coverage.get('snapshot_id')
            or source.get('settlement_observed_through') != coverage.get('settlement_observed_through')
            or coverage.get('settlement_observed_through') != full.get('settlement_observed_through')
            or date.fromisoformat(source['settlement_observed_through']) < end):
        raise ValueError('report cutoff evidence/as-of binding mismatch')
    expected_orders = {r['order_id'] for r in rebuilt['settled_orders']}
    report_orders = {r['identity']['order_id'] for r in report['order_lines']}
    if report_orders != expected_orders:
        raise ValueError('report is missing settled parent orders or includes unrelated orders')
    from shared_platform.internal_catalog_sku import internal_sku
    original_lines = {}
    for order in original_orders['orders']:
        if order['order_id'] not in expected_orders:
            continue
        if not isinstance(order.get('lines'), list) or not order['lines']:
            raise ValueError('original settled order has no complete SKU/quantity lines')
        for line in order['lines']:
            key = (str(order['order_id']), str(line.get('order_line_id') or ''))
            canonical = internal_sku(line.get('seller_sku'))
            if (not key[1] or key in original_lines or len(canonical) != 4 or not canonical.isdigit()
                    or str(line.get('canonical_sku')) != canonical):
                raise ValueError('original order line has missing or ambiguous SKU mapping')
            original_lines[key] = line
    actual_keys = [(str(r['identity']['order_id']), str(r['identity']['order_line_id'])) for r in report['order_lines']]
    if len(set(actual_keys)) != len(actual_keys) or set(actual_keys) != set(original_lines):
        raise ValueError('report does not contain exactly the original settled order lines')
    for row in report['order_lines']:
        line = original_lines[(str(row['identity']['order_id']), str(row['identity']['order_line_id']))]
        product = row['product']
        quantity = Decimal(str(line.get('quantity')))
        if (not quantity.is_finite() or quantity <= 0
                or str(product.get('seller_sku')) != str(line.get('seller_sku'))
                or str(product.get('canonical_sku')) != str(line.get('canonical_sku'))
                or Decimal(str(product.get('quantity'))) != quantity
                or Decimal(str(row.get('cost', {}).get('quantity'))) != quantity):
            raise ValueError('report SKU/quantity differs from original sold order line')
    for order_id in report_orders:
        source_net = sum((Decimal(str(r['net_settlement_amount'])) for r in settlement['orders']
                          if str(r.get('order_id') or r.get('related_order_id')) == order_id), Decimal('0'))
        report_net = sum((Decimal(str(r['settlement']['net_amount_local'])) for r in report['order_lines']
                          if r['identity']['order_id'] == order_id), Decimal('0'))
        if not source_net.is_finite() or abs(source_net - report_net) > Decimal('1e-12'):
            raise ValueError('reported settlement does not reconcile with official source')
    cost_doc, fx_doc = _read(entry['costs']), _read(entry['fx'])
    costs = CostSnapshot.from_mapping(cost_doc['records'], snapshot_id=cost_doc['snapshot_id'])
    fx = FxSnapshot.from_mapping(fx_doc['rates_cny'], source=fx_doc['source'], as_of=fx_doc['as_of'], snapshot_id=fx_doc['snapshot_id'])
    if source.get('cost_snapshot') != costs.payload() or source.get('fx_snapshot') != fx.payload():
        raise ValueError('cost or FX snapshot does not match the report')
    _datetime(fx.as_of)
    ad = _read(entry['advertising'])
    if (report.get('calculation_kind') != 'realized_settlement_with_actual_ads'
            or ad.get('mode') != 'actual' or ad.get('platform') != platform or ad.get('site') != site
            or str(ad.get('shop_id')) != shop or ad.get('period') != {'start':start.isoformat(),'end':end.isoformat()}
            or not ad.get('snapshot_id') or not ad.get('source') or not ad.get('source_file')):
        raise ValueError('monthly actual advertising source/period missing; no inherited estimate allowed')
    _datetime(ad['as_of']); ad_raw = Path(ad['source_file']['path']).read_bytes()
    if hashlib.sha256(ad_raw).hexdigest() != ad['source_file']['sha256']:
        raise ValueError('original advertising source changed')
    ad_total = Decimal(str(ad['total_cny']))
    original_ad = json.loads(ad_raw)
    if any(original_ad.get(key) != ad.get(key) for key in ('platform','site','shop_id','period')):
        raise ValueError('original advertising identity/period differs from manifest')
    source_amount = Decimal(str(_source_value(original_ad, ad.get('amount_path'))))
    source_currency = str(_source_value(original_ad, ad.get('currency_path')))
    source_rate = fx.get(source_currency)
    if not source_amount.is_finite() or source_amount < 0 or source_rate is None or source_amount * source_rate != ad_total:
        raise ValueError('advertising total is not derived from the frozen original amount and FX')
    if not ad_total.is_finite() or ad_total < 0 or Decimal(str(report['totals']['advertising_cny'])) != ad_total:
        raise ValueError('actual advertising amount mismatch')
    for row in report['order_lines']:
        identity, cost, rate, advertising = row['identity'], row.get('cost') or {}, row.get('fx') or {}, row.get('advertising') or {}
        if identity.get('shop_id') != shop or not identity.get('order_id') or not identity.get('order_line_id'):
            raise ValueError('order line shop/order identity is incomplete or different')
        if not start <= local_date(row.get('occurred_at')) <= end:
            raise ValueError('order line outside cutoff')
        if local_date(row.get('settled_at')) > date.fromisoformat(source['settlement_observed_through']):
            raise ValueError('settlement falls after evidence as-of')
        record = costs.get(row.get('product', {}).get('canonical_sku'))
        if (not record or cost.get('snapshot_id') != costs.snapshot_id or cost.get('version') != record.version
                or record.version == 'unspecified' or cost.get('source') != record.source or not record.source
                or Decimal(str(cost.get('unit_cost_cny'))) != record.unit_cost_cny
                or cost.get('effective_at') != record.effective_at):
            raise ValueError('order cost provenance missing or mismatched')
        if local_date(record.effective_at) > local_date(row['occurred_at']):
            raise ValueError('current cost cannot silently replace earlier order cost')
        if (rate.get('snapshot_id') != fx.snapshot_id or rate.get('checksum') != fx.checksum
                or rate.get('source') != fx.source or rate.get('as_of') != fx.as_of
                or Decimal(str(rate.get('rate_cny_per_local'))) != fx.get(row['settlement']['currency'])):
            raise ValueError('order FX provenance missing or mismatched')
        if (advertising.get('mode') != 'allocated_actual_ads' or advertising.get('snapshot_id') != ad['snapshot_id']
                or advertising.get('source') != ad['source'] or advertising.get('as_of') != ad['as_of']):
            raise ValueError('order actual advertising provenance missing or mismatched')


def validate_reports(paths, scope, *, manifest_path=None):
    from domains.data_operations.profit_settlement.audit import audit_profit_report
    platforms = set(scope.get('platforms') or ['tiktok', 'shopee'])
    sites = set(scope.get('sites') or ['MY', 'TH', 'VN', 'PH'])
    month = scope['month']
    _, manifest = _manifest([manifest_path] if manifest_path else paths)
    entries = {}
    for entry in manifest['reports']:
        key = (entry.get('platform'), entry.get('site'), str(entry.get('shop_id')))
        if key in entries:
            raise ValueError('duplicate manifest platform/site/shop')
        _read(entry['report'])
        entries[key] = entry
    if scope.get('shops') and {str(e.get('shop_id')) for e in entries.values()} != set(scope['shops']):
        raise ValueError('monthly report set does not cover exact requested shops')
    found = {}
    for path in paths:
        path = Path(path)
        if path.suffix != '.json' or not path.is_file():
            continue
        report = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(report, dict) or not str(report.get('schema_version', '')).startswith('profit-report/'):
            continue
        period, source = report.get('period') or {}, report.get('source') or {}
        regions = {str(r.get('identity', {}).get('region', '')).upper() for r in report.get('order_lines', [])}
        if len(regions) != 1:
            raise ValueError('monthly report needs one exact site')
        site = next(iter(regions))
        shops = {str(r.get('identity', {}).get('shop_id', '')) for r in report.get('order_lines', [])}
        if len(shops) != 1:
            raise ValueError('monthly report needs one exact shop')
        key = (report.get('platform'), site, next(iter(shops)))
        if key[0] not in platforms or key[1] not in sites:
            raise ValueError('report outside requested platform/site scope')
        if report.get('period_kind') != 'monthly' or period.get('start') != month + '-01' or not str(period.get('end', '')).startswith(month + '-') or period.get('basis') != 'order_created_at':
            raise ValueError('report is not requested created-order monthly cutoff')
        if source.get('all_non_cancelled_orders_settled') is not True or not source.get('coverage_snapshot_id') or not source.get('settlement_observed_through'):
            raise ValueError('monthly cutoff lacks complete settlement coverage evidence')
        audit = audit_profit_report(report).payload()
        if audit['status'] != 'PASSED' or report.get('quality_issues'):
            raise ValueError('profit report failed independent arithmetic/data-quality audit')
        if key in found:
            raise ValueError('duplicate platform/site report')
        entry = entries.get(key)
        if not entry or hashlib.sha256(path.read_bytes()).hexdigest() != entry['report']['sha256']:
            raise ValueError('report is not bound to verified source manifest')
        _verify_sources(report, entry, scope)
        found[key] = (path, report, audit)
    if {(p,s) for p,s,_ in found} != {(p, s) for p in platforms for s in sites}:
        raise ValueError('monthly report set incomplete for requested platforms/sites')
    if set(found) != set(entries) or (scope.get('shops') and {shop for _,_,shop in found} != set(scope['shops'])):
        raise ValueError('not all manifest shops have actually validated reports')
    return found


def inspect_unknown_attempt(task, profile):
    """Read only the original profit attempt, without certifying or replaying it.

    A writable agent-result file and a session ID parsed from subprocess stdout
    are leads for reconciliation, not proof that the original agent stopped or
    that its financial inputs belong to the same session. No recovery action is
    authorized by this projection.
    """
    checkpoint = task.get('checkpoint') or {}
    result = checkpoint.get('agent_result') or {}
    session_id = result.get('session_id') if isinstance(result, dict) else None
    base = (Path(profile.data_root).resolve() / 'artifacts' / str(task.get('task_id'))).resolve()
    receipt = checkpoint.get('attempt_receipt')
    response = {
        'status': 'BLOCKED', 'task_id': task.get('task_id'),
        'release': task.get('version'), 'attempt_output': None,
        'attempt_id': None,
        'session_id': None, 'session_readback': 'NOT_VERIFIED',
        'agent_result_file': None, 'automatic_resume_allowed': False,
    }
    version = task.get('version') or {}
    if (getattr(profile, 'version', None) is not None
            and (not isinstance(version, dict)
                 or version.get('code_version') != profile.version
                 or version.get('environment') != getattr(profile, 'environment', None)
                 or version.get('manifest_digest') != getattr(profile, 'manifest_digest', None))):
        return response
    if (task.get('template') != 'profit' or task.get('current_step') != 'coverage'
            or not (checkpoint.get('agent_attempt_started') or checkpoint.get('agent_unknown'))):
        return response
    raw = checkpoint.get('attempt_output')
    if (not isinstance(raw, str) or not raw or not isinstance(receipt, dict)
            or set(receipt) != {'schema_version', 'task_id', 'release', 'scope_sha256',
                                'output_dir', 'attempt_id', 'input_count'}
            or receipt['schema_version'] != 'profit-agent-attempt/v1'
            or receipt['task_id'] != task.get('task_id')
            or receipt['release'] != task.get('version')
            or receipt['scope_sha256'] != _scope_sha256(task.get('scope'))
            or receipt['output_dir'] != raw
            or not isinstance(receipt['attempt_id'], str)
            or not re.fullmatch(r'[0-9a-f]{32}', receipt['attempt_id'])
            or type(receipt['input_count']) is not int or receipt['input_count'] < 0
            or receipt['input_count'] != checkpoint.get('input_count')):
        return response
    output = Path(raw)
    if (not output.is_absolute() or output.parent.resolve() != base
            or output.name != 'monthly-' + receipt['attempt_id']
            or output.resolve() != output):
        return response
    response['status'] = 'UNKNOWN'
    response['attempt_output'] = raw
    response['attempt_id'] = receipt['attempt_id']
    response['session_id'] = session_id if isinstance(session_id, str) and session_id else None
    response['session_readback'] = 'REPORTED_UNVERIFIED' if response['session_id'] else 'NOT_RECORDED'
    candidate = output / 'agent-result.json'
    if candidate.is_file() and not candidate.is_symlink():
        if candidate.stat().st_size > 1024 * 1024:
            return response
        data = candidate.read_bytes()
        response['agent_result_file'] = {
            'path': str(candidate), 'sha256': hashlib.sha256(data).hexdigest(),
            'ownership': 'UNVERIFIED',
        }
    return response


def adapter(bridge):
    def run(engine, task, token, profile):
        step, task_id = task['current_step'], task['task_id']
        if step == 'coverage':
            checkpoint = task.get('checkpoint') or {}
            if checkpoint.get('agent_attempt_started') or checkpoint.get('agent_unknown'):
                engine.fail(task_id, token, '上次利润执行会话结果未知，需先核验原会话，不能再次启动')
                return
            notes = [e['detail']['note'] for e in engine.store.events(task_id) if e['event_type'] == 'input_provided']
            result = checkpoint.get('agent_result')
            known_prepared = result and result.get('status') == 'prepared'
            new_input = len(notes) > checkpoint.get('input_count', 0)
            if not known_prepared or (result['result']['missing_inputs'] and new_input):
                reserved = _begin_agent_attempt(engine, task, token, profile, len(notes))
                if reserved is None:
                    return
                output, checkpoint = reserved
                result = bridge.execute_monthly(task, output, notes)
                engine.record_checkpoint(task_id, token, {**checkpoint,'agent_attempt_started':False,'agent_result': result, 'agent_unknown': result['status'] == 'unknown', 'input_count': len(notes), 'attempt_output': str(output)})
            if result['status'] != 'prepared':
                engine.fail(task_id, token, result['reason'])
                return
            if result['result']['missing_inputs']:
                engine.wait_for_user(task_id, token, kind='input', label='补充利润计算资料', reason='；'.join(result['result']['missing_inputs']), url='/profit')
                return
            from shared_platform.operations_profit_producer import build_verified_reports, AwaitingSettlement
            inputs=[]
            for name in result['result']['evidence_paths']:
                path=Path(name).resolve(strict=True)
                if path.suffix.lower()!='.json':continue
                body=json.loads(path.read_text(encoding='utf-8'))
                if isinstance(body,dict) and body.get('schema_version')=='profit-producer-input/v1':inputs.append(path)
            if len(inputs)!=1:raise ValueError('exactly one financial input manifest is required; agent reports are not accepted')
            input_digest=hashlib.sha256(inputs[0].read_bytes()).hexdigest()
            fixed_output=profile.data_root/'fixed-producer'/task_id/input_digest
            attempt_path=checkpoint.get('attempt_output') or (engine.get(task_id).get('checkpoint') or {}).get('attempt_output')
            if attempt_path and fixed_output.resolve().is_relative_to(Path(attempt_path).resolve()):
                raise ValueError('fixed producer output cannot share agent writable workspace')
            try:
                produced=build_verified_reports({'path':str(inputs[0]),'sha256':input_digest},task['scope'],fixed_output,engine.release)
            except AwaitingSettlement as error:
                result['result']['missing_inputs']=[str(error)]
                engine.record_checkpoint(task_id,token,{**(engine.get(task_id).get('checkpoint') or {}),'agent_result':result})
                engine.wait_for_user(task_id,token,kind='input',label='等待完整结算资料',reason=str(error),url='/profit')
                return
            reports = validate_reports(produced['evidence_paths'], task['scope'])
            manifest_path, manifest = _manifest(produced['evidence_paths'])
            manifest_ref = {'path': str(manifest_path), 'sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest()}
            saved = []
            for (platform, site, shop), (path, report, audit) in sorted(reports.items()):
                ref = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                saved.append({'platform': platform, 'site': site, 'shop_id': shop, 'report': ref, 'audit': audit, 'cutoff': report['period']['end']})
            engine.complete_step(task_id, token, expected_step=step, checkpoint={'reports': saved, 'manifest': manifest_ref, 'producer_receipt': produced['receipt'], 'agent_session_id': result.get('session_id')})
            return
        coverage = next(s['checkpoint'] for s in task['steps'] if s['key'] == 'coverage')
        records = coverage['reports']
        _read(coverage['producer_receipt'])
        _read(coverage['manifest'])
        reports = validate_reports([r['report']['path'] for r in records], task['scope'], manifest_path=coverage['manifest']['path'])
        for record in records:
            _read(record['report'])
        if step != 'report':
            engine.complete_step(task_id, token, expected_step=step, checkpoint={'report_digests': [r['report']['sha256'] for r in records], 'verified_count': len(records)})
            return
        from domains.data_operations.profit_settlement.render import render_profit_report_html
        import html
        output = profile.data_root / 'artifacts' / task_id / 'result'
        output.mkdir(parents=True, exist_ok=True)
        files, links = {}, []
        for (platform, site, shop), (_, report, _) in sorted(reports.items()):
            name = platform + '_' + site + '_' + hashlib.sha256(shop.encode()).hexdigest()[:12] + '.html'
            raw = render_profit_report_html(report).encode('utf-8')
            (output / name).write_bytes(raw)
            files[name] = hashlib.sha256(raw).hexdigest()
            links.append('<li><a href="' + name + '">' + html.escape(platform + ' ' + site + ' ' + shop + ' 截至 ' + report['period']['end']) + '</a></li>')
        raw = ('<!doctype html><meta charset="utf-8"><title>月度利润</title><h1>' + html.escape(task['scope']['month']) + ' 已完整结算范围利润</h1><p>按各站已完全结算的下单日期截止；原始快照和金额复核已保留。</p><ul>' + ''.join(links) + '</ul>').encode('utf-8')
        (output / 'index.html').write_bytes(raw)
        files['index.html'] = hashlib.sha256(raw).hexdigest()
        engine.complete_step(task_id, token, expected_step=step, checkpoint={'artifact_files': files, 'artifact_dir': str(output)}, result_url='/api/orbit/tasks/' + task_id + '/artifacts/index.html')
    return run
