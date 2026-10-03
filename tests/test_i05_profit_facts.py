"""I05 actual consumers with synthetic inputs and no production initialization."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
import sqlite3

import pytest

from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
from domains.data_operations.profit_settlement.cost_policy import resolve_temporary_cost_policy
from domains.data_operations.profit_settlement.shared_inputs import CostSnapshot, FxSnapshot
from domains.data_operations.profit_settlement import tiktok, shopee, ozon
from domains.product_operations import catalog_database_audit
from core.db import connect_readonly


SCHEMA = '''
CREATE TABLE products(sku_id TEXT, seller_sku TEXT, product_name TEXT, sku_name TEXT, image_url TEXT, currency TEXT, shop_cipher TEXT, updated_at INTEGER, product_id TEXT);
CREATE TABLE sku_costs(sku_id TEXT, cost_cny TEXT, updated_at INTEGER);
CREATE TABLE shopee_products(seller_sku TEXT, product_name TEXT, model_name TEXT, image_url TEXT, currency TEXT, region TEXT, shop_id TEXT, updated_at INTEGER, item_id TEXT, model_id TEXT, price REAL);
CREATE TABLE sku_logistics_weights(seller_sku TEXT, weight_g TEXT, package_count TEXT, depth_mm TEXT, width_mm TEXT, height_mm TEXT, updated_at INTEGER);
CREATE TABLE shops(cipher TEXT, shop_id TEXT, region TEXT);
CREATE TABLE shopee_shops(shop_id TEXT, region TEXT);
CREATE TABLE product_analytics(product_id TEXT, shop_cipher TEXT);
'''


def catalog_db(tmp_path):
    path = tmp_path / 'synthetic.db'
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO shops VALUES ('fixture','fixture','TH')")
        db.execute("INSERT INTO products VALUES ('variant','TEST-9000','Synthetic','One','','THB','fixture',20,'product')")
        db.executemany('INSERT INTO sku_costs VALUES (?,?,?)', [('variant','8',20), ('variant','1',10)])
    db.close()
    return path


def test_actual_catalog_digest_tracks_consumed_nonselected_cost_candidate(tmp_path, record_property):
    path = catalog_db(tmp_path)
    fixed_ns = 1788600000000000000
    os.utime(path, ns=(fixed_ns, fixed_ns))
    before = load_local_catalog(path)
    before_policy = resolve_temporary_cost_policy(before, ['TEST-9000'])
    with sqlite3.connect(path) as db:
        db.execute("UPDATE sku_costs SET cost_cny='9' WHERE updated_at=10")
    os.utime(path, ns=(fixed_ns, fixed_ns))
    after = load_local_catalog(path)
    after_policy = resolve_temporary_cost_policy(after, ['TEST-9000'])
    result = {'before': {'snapshot': before.snapshot_id, 'costs': dict(before.costs_by_sku), 'candidates': dict(before.cost_candidates_by_sku), 'policy': dict(before_policy.values)},
              'after': {'snapshot': after.snapshot_id, 'costs': dict(after.costs_by_sku), 'candidates': dict(after.cost_candidates_by_sku), 'policy': dict(after_policy.values)},
              'effective_at_equal': before.effective_at == after.effective_at}
    output = tmp_path / 'actual-catalog-identity.json'
    output.write_text(json.dumps(result, default=str, indent=2), encoding='utf-8')
    record_property('actual_probe', str(output))
    record_property('actual_probe_sha256', hashlib.sha256(output.read_bytes()).hexdigest())
    assert before.effective_at == after.effective_at
    assert before.costs_by_sku == after.costs_by_sku
    assert 'TEST-9000' not in before_policy.values
    assert 'TEST-9000' not in after_policy.values
    assert before_policy.issues[0]['code'] == after_policy.issues[0]['code'] == 'ambiguous_catalog_identity'
    assert before_policy.snapshot_id != after_policy.snapshot_id
    assert before.snapshot_id != after.snapshot_id


def settlement_row():
    return {'order_id': 'order', 'order_line_id': 'order:1', 'shop_id': 'fixture', 'region': 'TH',
            'platform_sku': 'variant', 'seller_sku': 'TEST-9000', 'canonical_sku': 'TEST-9000',
            'currency': 'THB', 'quantity': '1', 'buyer_paid_product_amount': '200',
            'buyer_cash_paid_product_amount': '200', 'net_settlement_amount': '100',
            'settlement_status': 'settled', 'occurred_at': '2026-08-03T12:00:00+07:00',
            'settled_at': '2026-08-05T12:00:00+07:00', 'source_snapshot_id': 'fixture-settlement',
            'fulfillment': {'mode': 'cross_border', 'classification_rule': 'fixture'},
            'fee_items': [{'code': 'commission', 'amount': '12', 'currency': 'THB', 'included_in_net_settlement': True}]}


def snapshots(cost='8', rate='0.2'):
    return (CostSnapshot.from_mapping({'TEST-9000': {'unit_cost_cny': cost, 'version': 'fixture', 'source': 'synthetic'}}, snapshot_id='caller-fixed-cost-label'),
            FxSnapshot.from_mapping({'THB': rate}, source='synthetic-fx', as_of='2026-08-06T00:00:00Z', snapshot_id='caller-fixed-fx-label'))


@pytest.mark.parametrize('engine', [tiktok, shopee, ozon], ids=['tiktok', 'shopee', 'ozon'])
@pytest.mark.parametrize('change', ['cost', 'fx'])
def test_report_identity_tracks_actual_content_even_with_reused_snapshot_labels(engine, change):
    costs, fx = snapshots()
    new_costs, new_fx = snapshots('9' if change == 'cost' else '8', '0.3' if change == 'fx' else '0.2')
    options = dict(period_start='2026-08-03', period_end='2026-08-09', generated_at=datetime(2026,8,10,tzinfo=timezone.utc), code_version='fixture')
    before = engine.build_weekly_report([settlement_row()], costs=costs, fx=fx, **options)
    after = engine.build_weekly_report([settlement_row()], costs=new_costs, fx=new_fx, **options)
    assert before.totals['profit_cny'] != after.totals['profit_cny']
    assert before.report_id != after.report_id
    assert before.idempotency_key != after.idempotency_key


def test_public_catalog_connection_preserves_transaction_and_concurrent_snapshot(tmp_path):
    path = catalog_db(tmp_path)
    with sqlite3.connect(path) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
    connection = connect_readonly(path)
    try:
        connection.execute('BEGIN')
        before = catalog_database_audit.audit_catalog_connection(connection).payload()
        with sqlite3.connect(path) as writer:
            writer.execute("UPDATE sku_costs SET cost_cny='9' WHERE updated_at=10")
        assert connection.in_transaction
        metadata = connection.execute('SELECT cost_cny FROM sku_costs WHERE updated_at=10').fetchone()[0]
        assert metadata == '1'
        again = catalog_database_audit.audit_catalog_connection(connection).payload()
        assert before['records'] == [{**row, 'observed_at': before['observed_at']} for row in again['records']]
        assert connection.in_transaction
    finally:
        connection.close()
    after = catalog_database_audit.audit_catalog_database(path).payload()
    amounts = {c['amount'] for c in after['records'][0]['cost']['candidates']}
    assert amounts == {'8', '9'}


@pytest.mark.parametrize('active,query_only', [(False, True), (True, False)])
def test_public_catalog_connection_rejects_unowned_transaction_or_writable_connection(tmp_path, active, query_only):
    path = catalog_db(tmp_path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        if query_only:
            connection.execute('PRAGMA query_only=ON')
        if active:
            connection.execute('BEGIN')
        with pytest.raises(ValueError, match='read-only transaction'):
            catalog_database_audit.audit_catalog_connection(connection)
        assert connection.in_transaction is active
        assert connection.execute('SELECT count(*) FROM products').fetchone()[0] == 1
    finally:
        connection.close()


def test_profit_catalog_preserves_six_digit_identity_and_full_review_sources(tmp_path):
    path = catalog_db(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE products SET seller_sku='990017'")
        db.execute("INSERT INTO products VALUES ('other','660017','Other','Two','','THB','fixture',1,'other-product')")
        db.execute("INSERT INTO sku_costs VALUES ('other','3',1)")
    catalog = load_local_catalog(path)
    assert catalog.seller_sku_by_platform_sku == {'variant': '990017', 'other': '660017'}
    assert set(catalog.cost_candidates_by_sku) == {'990017', '660017', '0017'}
    assert {record['historical_internal_sku'] for record in catalog.cost_records_by_sku['0017']} == {'0017'}
    assert {r['identity']['seller_sku'] for r in catalog.review['records']} == {'990017', '660017'}
    assert all(r['source']['row_locator'] for r in catalog.review['records'])
    assert catalog.snapshot_id == load_local_catalog(path).snapshot_id


def test_same_seller_sku_multiple_full_identities_cannot_choose_global_metadata_or_cost(tmp_path):
    path = catalog_db(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO shops VALUES ('other-shop','other-shop','MY')")
        db.execute("INSERT INTO products VALUES ('other','TEST-9000','Other','Two','','MYR','other-shop',1,'other-product')")
        db.execute("INSERT INTO sku_costs VALUES ('other','100',1)")
    catalog = load_local_catalog(path)
    assert 'TEST-9000' not in catalog.costs_by_sku
    assert 'TEST-9000' not in catalog.product_by_seller_sku
    assert 'TEST-9000' in catalog.blocked_skus
    policy = resolve_temporary_cost_policy(catalog, ['TEST-9000'])
    assert 'TEST-9000' not in policy.values


def test_temporary_policy_identity_tracks_candidates_even_when_selected_max_is_unchanged(tmp_path):
    path = catalog_db(tmp_path)
    fixed_ns = 1788600000000000000
    os.utime(path, ns=(fixed_ns, fixed_ns))
    before = resolve_temporary_cost_policy(load_local_catalog(path), ['TEST-9000'])
    with sqlite3.connect(path) as db:
        db.execute("UPDATE sku_costs SET cost_cny='2' WHERE updated_at=10")
    os.utime(path, ns=(fixed_ns, fixed_ns))
    after = resolve_temporary_cost_policy(load_local_catalog(path), ['TEST-9000'])
    assert 'TEST-9000' not in before.values and 'TEST-9000' not in after.values
    assert before.issues[0]['code'] == after.issues[0]['code'] == 'ambiguous_catalog_identity'
    assert before.snapshot_id != after.snapshot_id


@pytest.mark.parametrize('start,end,usable', [
    ('2026-08-03T00:00:00+07:00', '2026-08-10T00:00:00+07:00', False),
    ('2026-07-03T00:00:00+07:00', '2026-07-10T00:00:00+07:00', False),
    (None, None, False),
])
def test_temporary_policy_evaluates_cost_dates_for_calculation_period_not_observation(tmp_path, start, end, usable):
    path = catalog_db(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE sku_costs ADD COLUMN valid_from TEXT')
        db.execute('ALTER TABLE sku_costs ADD COLUMN valid_to TEXT')
        db.execute("UPDATE sku_costs SET valid_from='2026-08-01T00:00:00+07:00', valid_to='2026-09-01T00:00:00+07:00'")
    catalog = load_local_catalog(path)
    policy = resolve_temporary_cost_policy(catalog, ['TEST-9000'], period_start=start, period_end=end)
    assert ('TEST-9000' in policy.values) is usable
    if not usable:
        assert policy.issues and all(item['code'] != 'missing_cost_default_5_selected' for item in policy.issues)


@pytest.mark.parametrize('engine', [tiktok, shopee, ozon], ids=['tiktok', 'shopee', 'ozon'])
@pytest.mark.parametrize('case', ['empty', 'unsettled', 'outside_period', 'missing_cost', 'missing_fx'])
def test_absent_calculable_facts_are_not_zero_profit(engine, case):
    costs, fx = snapshots()
    rows = [settlement_row()]
    if case == 'empty': rows = []
    elif case == 'unsettled': rows[0]['settlement_status'] = 'pending'
    elif case == 'outside_period': rows[0]['settled_at'] = '2020-01-01T00:00:00+07:00'
    elif case == 'missing_cost': costs = CostSnapshot.from_mapping({})
    else: fx = FxSnapshot.from_mapping({}, source='failed-capture', as_of='2026-08-06T00:00:00Z')
    report = engine.build_weekly_report(rows, costs=costs, fx=fx, period_start='2026-08-03', period_end='2026-08-09', code_version='fixture')
    payload = report.payload()
    assert payload['totals']['profit_cny'] is None
    assert report.status == payload['status'] == ('needs_review' if case.startswith('missing') else 'no_data')
    assert payload['result_scope'] == 'no_calculated_facts'


@pytest.mark.parametrize('engine', [tiktok, shopee, ozon], ids=['tiktok', 'shopee', 'ozon'])
def test_partial_report_and_empty_html_are_explicit(engine):
    from domains.data_operations.profit_settlement.render import render_profit_report_html
    costs, fx = snapshots()
    kwargs = dict(costs=costs, fx=fx, period_start='2026-08-03', period_end='2026-08-09', code_version='fixture')
    rejected = {**settlement_row(), 'canonical_sku': 'unmapped', 'order_line_id': 'rejected'}
    partial = engine.build_weekly_report([settlement_row(), rejected], **kwargs).payload()
    assert partial['status'] == 'needs_review'
    assert partial['result_scope'] == 'partial_diagnostic'
    assert len(partial['order_lines']) == 1
    assert '部分数据诊断' in render_profit_report_html(partial)
    empty = engine.build_weekly_report([], **kwargs).payload()
    html = render_profit_report_html(empty)
    assert '无可计算事实' in html
    assert '<span>利润 CNY</span><strong>—</strong>' in html
    assert '<span>总成交额（用户实付）CNY</span><strong>—</strong>' in html
    assert 'CNY 0.00' not in html


def captured_evidence():
    return {
        'schema_version': 'settlement-evidence/v1', 'status': 'ready',
        'platform': 'tiktok', 'site': 'TH', 'shop_id': 'shop-fixture',
        'snapshot_id': 'captured-fixture', 'checksum': 'captured-content',
        'net_settlement_total_local': '100',
        'orders': [{'order_id': 'order-fixture', 'transaction_type': 'Order',
                    'currency': 'THB', 'net_settlement_amount': '100',
                    'buyer_total_amount': '200', 'settled_at': '2026-08-05T12:00:00+07:00',
                    'items': [{'platform_sku': 'variant-fixture', 'quantity': '1'}]}],
    }


@pytest.mark.parametrize('case', ['raw_sku', 'missing_net', 'missing_paid', 'unsettled', 'missing_total'])
def test_evidence_adapter_does_not_invent_money_or_sku(case):
    from types import SimpleNamespace
    from domains.data_operations.profit_settlement.settlement_evidence_adapter import adapt_settlement_evidence
    catalog = SimpleNamespace(seller_sku_by_platform_sku={'variant-fixture': '990017'}, costs_by_sku={'990017': Decimal('8')})
    evidence = captured_evidence()
    record = evidence['orders'][0]
    if case == 'missing_net': del record['net_settlement_amount']
    elif case == 'missing_paid': del record['buyer_total_amount']
    elif case == 'unsettled': record['settlement_status'] = 'pending'
    elif case == 'missing_total': del evidence['net_settlement_total_local']
    result = adapt_settlement_evidence(evidence, catalog, period_kind='weekly')
    if case == 'raw_sku':
        assert result.rows[0]['canonical_sku'] == '990017'
        assert result.rows[0]['shop_id'] == 'shop-fixture'
    elif case == 'missing_total':
        assert result.reconciliation['official_net_settlement_local'] is None
        assert result.reconciliation['unallocated_local'] is None
    else:
        assert result.rows == ()
        assert result.status != 'ready'


def test_actual_weekly_cli_uses_captured_fx_and_catalog_review(tmp_path, record_property):
    import importlib.util
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / 'domains/data_operations/skills/manage-profit-settlement/scripts/build_weekly_from_evidence.py'
    spec = importlib.util.spec_from_file_location('i05_weekly_consumer', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = tmp_path / 'data'
    data.mkdir()
    catalog_db(data).rename(data / 'shop.db')
    evidence = captured_evidence()
    evidence['orders'][0]['items'][0]['platform_sku'] = 'variant'
    evidence_path = tmp_path / 'tiktok_TH_2026-08-03_2026-08-09.settlement.json'
    evidence['shop_id'] = 'fixture'
    evidence_path.write_text(json.dumps(evidence), encoding='utf-8')
    fx_path = tmp_path / 'captured-fx.json'
    fx_path.write_text(json.dumps({'rates_cny': {'THB': '0.2'}, 'source': 'fixture-capture', 'as_of': '2026-08-06T00:00:00Z'}), encoding='utf-8')
    output = tmp_path / 'report'
    result = module.main(['--project-root', str(tmp_path), '--evidence-dir', str(tmp_path), '--platform', 'tiktok', '--site', 'TH', '--start', '2026-08-03', '--end', '2026-08-09', '--output', str(output), '--fx-input', str(fx_path), '--timezone', '+07:00'])
    assert result == 2  # conflicting local costs and incomplete fulfillment remain visible
    path = output / 'weekly_profit_TH_2026-08-03_2026-08-09.json'
    bundle = json.loads(path.read_text(encoding='utf-8'))
    report = bundle['reports']['tiktok']['report']
    assert bundle['input_mode'] == 'captured'
    assert bundle['network_reads_performed'] == []
    assert bundle['catalog_review']['schema_version']
    assert report['source']['catalog_snapshot_id'] == bundle['catalog_snapshot_id']
    assert report['period']['timezone'] == '+07:00'
    assert report['assumption_warnings'] == []
    assert any(issue['code'] == 'missing_cost' for issue in report['quality_issues'])
    assert bundle['cost_policy']['snapshot_id']
    assert (output / 'tiktok_TH_2026-08-03_2026-08-09.html').is_file()
    record_property('actual_consumer_json', str(path))


def test_bundle_period_uses_declared_timezone():
    from types import SimpleNamespace
    from domains.data_operations.profit_settlement.weekly_evidence_bundle import build_weekly_evidence_bundle
    evidence = captured_evidence()
    evidence['orders'][0]['settled_at'] = '2026-08-02T20:00:00Z'
    catalog = SimpleNamespace(seller_sku_by_platform_sku={'variant-fixture': 'TEST-9000'}, costs_by_sku={'TEST-9000': Decimal('8')})
    costs, fx = snapshots()
    kwargs = dict(period_start='2026-08-03', period_end='2026-08-09', costs=costs, fx=fx, platforms=('tiktok',), code_version='fixture')
    local = build_weekly_evidence_bundle({'tiktok': evidence}, catalog, calculation_timezone='+07:00', **kwargs)
    utc = build_weekly_evidence_bundle({'tiktok': evidence}, catalog, calculation_timezone='+00:00', **kwargs)
    assert len(local['reports']['tiktok']['report']['order_lines']) == 1
    assert utc['reports']['tiktok']['report']['totals']['profit_cny'] is None
    assert local['reports']['tiktok']['report']['report_id'] != utc['reports']['tiktok']['report']['report_id']


def captured_profile(tmp_path):
    database = catalog_db(tmp_path)
    evidence = captured_evidence()
    evidence['shop_id'] = 'fixture'
    evidence['orders'][0]['items'][0]['platform_sku'] = 'variant'
    evidence_path = tmp_path / 'evidence.json'
    evidence_path.write_text(json.dumps(evidence), encoding='utf-8')
    fx_path = tmp_path / 'fx.json'
    fx_path.write_text(json.dumps({'rates_cny': {'THB': '0.2'}, 'source': 'fixture-capture', 'as_of': '2026-08-06T00:00:00Z'}), encoding='utf-8')
    return {'schema_version': 'profit-captured-profile/v1', 'platform': 'tiktok', 'site': 'TH', 'shop_id': 'fixture',
            'start': '2026-08-03', 'end': '2026-08-09', 'timezone': '+07:00',
            'catalog_path': str(database), 'evidence_path': str(evidence_path), 'fx_path': str(fx_path),
            'allow_temporary_cost_policy': True}


def test_captured_consumer_repeat_override_and_no_assumption(tmp_path):
    from domains.data_operations.profit_settlement.captured_consumer import build_captured_weekly
    profile = captured_profile(tmp_path)
    first, again = build_captured_weekly(profile), build_captured_weekly(profile)
    assert first['reports']['tiktok']['report']['report_id'] == again['reports']['tiktok']['report']['report_id']
    changed = build_captured_weekly({**profile, 'fx_overrides': {'THB': '0.3'}})
    assert changed['reports']['tiktok']['report']['report_id'] != first['reports']['tiktok']['report']['report_id']
    assert changed['fx_provenance']['input_source'] == 'operator_override'
    assert changed['fx_provenance']['base_snapshot'] == first['fx_provenance']['base_snapshot']
    refused = build_captured_weekly({**profile, 'allow_temporary_cost_policy': False})
    assert refused['reports']['tiktok']['report']['totals']['profit_cny'] is None
    assert refused['cost_policy']['issues'][0]['code'] == 'ambiguous_catalog_identity'


@pytest.mark.parametrize('case', ['missing_database', 'wrong_scope', 'invalid_database', 'failed_fx', 'invalid_fx'])
def test_captured_consumer_failures_remain_unknown(tmp_path, case):
    from pathlib import Path
    from domains.data_operations.profit_settlement.captured_consumer import build_captured_weekly
    profile = captured_profile(tmp_path)
    if case == 'missing_database': profile['catalog_path'] = str(tmp_path / 'missing.db')
    elif case == 'wrong_scope': profile['shop_id'] = 'different-shop'
    elif case == 'invalid_database': Path(profile['catalog_path']).write_bytes(b'invalid sqlite')
    elif case == 'failed_fx': Path(profile['fx_path']).write_text('{}', encoding='utf-8')
    else: profile['fx_overrides'] = {'THB': 'NaN'}
    result = build_captured_weekly(profile)
    assert result['status'] in {'check_failed', 'needs_review'}
    assert result['network_reads_performed'] == []
    if result['reports']:
        assert result['reports']['tiktok']['report']['totals']['profit_cny'] is None
    else:
        assert result['result_scope'] == 'no_calculated_facts'


def test_captured_cli_json_html(tmp_path, record_property):
    from domains.data_operations.profit_settlement.cli import main
    profile = captured_profile(tmp_path)
    profile_path = tmp_path / 'profile.json'
    profile_path.write_text(json.dumps(profile), encoding='utf-8')
    output, html = tmp_path / 'consumer.json', tmp_path / 'consumer.html'
    assert main(['review-captured', '--profile', str(profile_path), '--output', str(output), '--html', str(html)]) == 2
    payload = json.loads(output.read_text(encoding='utf-8'))
    assert payload['reports']['tiktok']['report']['status'] == 'needs_review'
    rendered = html.read_text(encoding='utf-8')
    assert '阻断性质量问题' in rendered
    assert 'missing_cost' in rendered
    assert '<span>利润 CNY</span><strong>—</strong>' in rendered
    record_property('captured_cli_json', str(output))
    record_property('captured_cli_html', str(html))


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-Infinity'])
def test_nonfinite_inputs_cannot_be_money(value):
    assert CostSnapshot.from_mapping({'sku': value}).get('sku') is None
    assert FxSnapshot.from_mapping({'THB': value}, source='fixture', as_of='2026-08-06').get('THB') is None


@pytest.mark.parametrize('engine', [tiktok, shopee, ozon], ids=['tiktok', 'shopee', 'ozon'])
def test_nonfinite_settlement_is_rejected(engine):
    costs, fx = snapshots()
    row = {**settlement_row(), 'net_settlement_amount': 'NaN'}
    report = engine.build_weekly_report([row], costs=costs, fx=fx, period_start='2026-08-03', period_end='2026-08-09', code_version='fixture')
    assert report.status == 'needs_review'
    assert report.totals['profit_cny'] is None


@pytest.mark.parametrize('case', ['shop', 'site', 'currency', 'product'])
def test_captured_scope_cannot_borrow_another_catalog_identity(tmp_path, case):
    from pathlib import Path
    from domains.data_operations.profit_settlement.captured_consumer import build_captured_weekly
    profile = captured_profile(tmp_path)
    path = Path(profile['evidence_path'])
    evidence = json.loads(path.read_text())
    if case == 'shop': evidence['shop_id'] = profile['shop_id'] = 'other-shop'
    elif case == 'site': evidence['site'] = profile['site'] = 'MY'
    elif case == 'currency': evidence['orders'][0]['currency'] = 'MYR'
    else: evidence['orders'][0]['items'][0]['product_id'] = 'other-product'
    path.write_text(json.dumps(evidence))
    result = build_captured_weekly(profile)
    report = result['reports']['tiktok']['report']
    assert report['totals']['profit_cny'] is None
    assert 'catalog_scope_mismatch' in {item['code'] for item in report['quality_issues']}


def test_knowledge_reference_binds_content_without_approving(tmp_path):
    from domains.data_operations.profit_settlement.captured_consumer import build_captured_weekly
    from domains.data_operations.profit_settlement.knowledge_reference import profit_report_reference
    result = build_captured_weekly(captured_profile(tmp_path))
    item = result['reports']['tiktok']
    reference = item['knowledge_reference']
    assert reference['usage'] == 'reference'
    assert reference['knowledge_approval_status'] == 'not_requested'
    assert reference['current_execution_authority'] is False
    assert reference['fresh_business_facts_verified'] is False
    assert reference['report_refs'][0].startswith('audit://profit-report/')
    changed = profit_report_reference({**item['report'], 'status': 'ready'})
    assert changed['report_digest'] != reference['report_digest']
    assert reference['assumptions']['cost_warnings'] == []
    assert any(issue['code'] == 'missing_cost' for issue in reference['quality_issues'])


def test_report_includes_relevant_full_identity_issues_only(tmp_path):
    from domains.data_operations.profit_settlement.captured_consumer import build_captured_weekly
    profile = captured_profile(tmp_path)
    with sqlite3.connect(profile['catalog_path']) as db:
        db.execute("INSERT INTO products VALUES ('unrelated','UNUSED-SKU','Unused','One','','THB','fixture',20,'unrelated-product')")
    catalog = load_local_catalog(profile['catalog_path'])
    relevant = [item for item in catalog.review['issues'] if (item.get('identity') or {}).get('seller_sku') == 'TEST-9000']
    unrelated = [item for item in catalog.review['issues'] if (item.get('identity') or {}).get('seller_sku') == 'UNUSED-SKU']
    assert relevant and unrelated
    result = build_captured_weekly(profile)
    issues = result['reports']['tiktok']['report']['quality_issues']
    for original in relevant:
        assert any(item['code'] == original['code'] and item.get('identity') == original['identity'] for item in issues)
    assert not any((item.get('identity') or {}).get('seller_sku') == 'UNUSED-SKU' for item in issues)


@pytest.mark.parametrize('case', ['shop', 'platform'])
def test_full_profile_rejects_cross_identity_cost_and_weight(tmp_path, case):
    from pathlib import Path
    from domains.data_operations.profit_settlement.captured_consumer import build_captured_weekly
    from domains.data_operations.profit_settlement.settlement_evidence_adapter import adapt_settlement_evidence
    profile = captured_profile(tmp_path)
    with sqlite3.connect(profile['catalog_path']) as db:
        db.execute("UPDATE products SET seller_sku='990017'")
        db.execute("UPDATE sku_costs SET cost_cny='8'")
        db.execute("INSERT INTO sku_logistics_weights VALUES ('990017','500','1','10','20','30',20)")
    path = Path(profile['evidence_path'])
    evidence = json.loads(path.read_text())
    if case == 'shop':
        profile['shop_id'] = evidence['shop_id'] = 'B-shop'
    else:
        profile['platform'] = evidence['platform'] = 'shopee'
        evidence['orders'][0]['items'] = [{'seller_sku': '990017', 'quantity': '1', 'discounted_price': '200'}]
        evidence['orders'][0]['financial_components'] = [{'code': 'buyer_paid_shipping_fee', 'amount': '0'}, {'code': 'vat_on_imported_goods', 'amount': '13'}, {'code': 'th_import_duty', 'amount': '37'}]
    path.write_text(json.dumps(evidence))
    result = build_captured_weekly(profile)
    report = result['reports'][profile['platform']]['report']
    assert report['totals']['profit_cny'] is None
    assert report['order_lines'] == []
    adapted = adapt_settlement_evidence(evidence, load_local_catalog(profile['catalog_path']), period_kind='weekly')
    assert adapted.rows and all(row['canonical_sku'] == '' and 'unit_weight_g' not in row for row in adapted.rows)
    assert 'catalog_scope_mismatch' in {issue.code for issue in adapted.issues}


@pytest.mark.parametrize('case', ['schema', 'reference', 'timestamp'])
def test_public_review_failure_keeps_caller_transaction_open(tmp_path, case):
    path = catalog_db(tmp_path)
    if case == 'schema':
        with sqlite3.connect(path) as db:
            db.execute('DROP TABLE product_analytics')
    connection = connect_readonly(path)
    try:
        connection.execute('BEGIN')
        if case == 'timestamp':
            with pytest.raises(ValueError):
                catalog_database_audit.audit_catalog_connection(connection, observed_at='invalid')
        else:
            result = catalog_database_audit.audit_catalog_connection(connection, review_evidence={'schema_version': 'invalid'} if case == 'reference' else None)
            assert result.status == 'check_failed'
            assert result.product_count is None
        assert connection.in_transaction
        assert connection.execute('SELECT COUNT(*) FROM main.products').fetchone()[0] == 1
        assert connection.execute('PRAGMA query_only').fetchone()[0] == 1
    finally:
        connection.close()


def test_legacy_runner_reuses_catalog_identity_and_never_selects_conflict(tmp_path):
    from shared_platform.weekly_profit_runner import load_catalog_profit_inputs
    path = catalog_db(tmp_path)
    canonical = load_local_catalog(path)
    legacy = load_catalog_profit_inputs(path)
    assert legacy.seller_sku_by_platform_sku == canonical.seller_sku_by_platform_sku
    assert legacy.costs_by_sku == {}
    assert 'conflicting_cost' in {issue.code for issue in legacy.issues}
    with sqlite3.connect(path) as db:
        db.execute("UPDATE sku_costs SET cost_cny='8'")
    updated = load_catalog_profit_inputs(path)
    assert updated.version != legacy.version
    assert updated.costs_by_sku == {'TEST-9000': Decimal('8')}
