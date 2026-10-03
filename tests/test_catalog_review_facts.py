"""Real quality CLI -> audit -> read-only SQLite, synthetic schemas/rows only."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from domains.product_operations import catalog_database_audit as audit
from scripts.database_maintenance import main


SCHEMA = '''
CREATE TABLE shops (cipher TEXT PRIMARY KEY, shop_id TEXT, region TEXT, updated_at INTEGER);
CREATE TABLE products (sku_id TEXT, shop_cipher TEXT, product_id TEXT, seller_sku TEXT,
    currency TEXT, sku_name TEXT, status TEXT, updated_at INTEGER);
CREATE TABLE sku_costs (sku_id TEXT, cost_cny, currency TEXT, note TEXT, updated_at INTEGER);
CREATE TABLE shopee_shops (shop_id INTEGER PRIMARY KEY, region TEXT, updated_at INTEGER);
CREATE TABLE shopee_products (model_id TEXT, shop_id INTEGER, item_id TEXT, seller_sku TEXT,
    currency TEXT, price REAL, model_name TEXT, status TEXT, updated_at INTEGER);
CREATE TABLE product_analytics (product_id TEXT, shop_cipher TEXT, synced_at INTEGER);
CREATE TABLE sku_logistics_weights (seller_sku TEXT, weight_g INTEGER, updated_at INTEGER);
'''


def database(tmp_path, sql=''):
    path = tmp_path / 'catalog.db'
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA + sql)
    return path


def cli(path, capsys, *args):
    # These cases audit the source observations, including their time bounds
    # and conflicts. A separate current SKU projection may summarize them.
    exit_code = main(['quality', '--database', str(path), '--cost-view', 'historical', *args])
    return exit_code, json.loads(capsys.readouterr().out)


def historical_audit(path):
    return audit.audit_catalog_database(path, cost_view='historical')


def test_global_tail4_and_cross_shop_variant_candidates_are_not_resolved_cost(tmp_path):
    path = database(tmp_path, '''
    INSERT INTO shops VALUES ('TH', 's1', 'TH', 1), ('MY', 's2', 'MY', 1);
    INSERT INTO products VALUES ('v1','TH','p1','660017','THB','one','ACTIVE',1);
    INSERT INTO products VALUES ('v2','MY','p2','990017','MYR','two','ACTIVE',1);
    INSERT INTO products VALUES ('v3','TH','p3','660017','THB','three','ACTIVE',1);
    INSERT INTO products VALUES ('v4','TH','p3','660017','THB','four','ACTIVE',1);
    INSERT INTO sku_costs VALUES ('v1',3,'CNY','synthetic source',1);
    ''')
    report = historical_audit(path)
    assert report.fallback_resolved_cost_rows == 0
    assert report.unresolved_cost_rows == 3
    payload = report.payload()
    assert payload['status'] == 'needs_review'
    assert {row['identity']['seller_sku'] for row in payload['records']} == {'660017', '990017'}
    assert any(row['matching']['suffix_candidates'] for row in payload['records'])


@pytest.mark.parametrize('state', ['no_data', 'verified', 'check_failed_missing', 'check_failed_schema'])
def test_quality_cli_distinguishes_empty_valid_failed_and_verified(tmp_path, capsys, state, record_property):
    if state == 'check_failed_missing':
        path = tmp_path / 'absent.db'
    elif state == 'check_failed_schema':
        path = tmp_path / 'broken-schema.db'
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE unrelated (id INTEGER)')
    else:
        path = database(tmp_path, '''
        INSERT INTO shops VALUES ('TH','s1','TH',1);
        INSERT INTO products VALUES ('v1','TH','p1','660017','THB','one','ACTIVE',1);
        INSERT INTO sku_costs VALUES ('v1',3,'CNY','synthetic source',1);
        ''' if state == 'verified' else '')
    code, result = cli(path, capsys)
    expected = 'check_failed' if state.startswith('check_failed') else state
    assert result['status'] == expected
    assert result['verified'] is (expected == 'verified')
    assert result['counts']['products'] == (None if expected == 'check_failed' else (1 if expected == 'verified' else 0))
    assert code == (3 if expected == 'check_failed' else 0)
    if state == 'check_failed_missing':
        assert not path.exists()
    out = tmp_path / ('actual-' + state + '.json')
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    record_property('actual_cli_sample', str(out))
    record_property('actual_cli_sample_sha256', hashlib.sha256(out.read_bytes()).hexdigest())


def test_quality_cli_preserves_compatibility_exit_and_emits_complete_shopee_issue(tmp_path, capsys):
    path = database(tmp_path, '''
    INSERT INTO shopee_shops VALUES (7,'MY',1);
    INSERT INTO shopee_products VALUES ('m1',7,'i1','0017','MYR',0,'synthetic model','NORMAL',1);
    ''')
    code, report = cli(path, capsys)
    assert code == 0  # Existing compatibility semantics: JSON is authoritative.
    assert report['status'] == 'needs_review'
    assert report['verified'] is False
    row = report['records'][0]
    assert row['identity']['platform'] == 'shopee'
    assert row['identity']['shop_key'] == '7'
    assert row['identity']['product_id'] == 'i1'
    assert row['identity']['variant_id'] == 'm1'
    assert row['identity']['seller_sku'] == '0017'
    assert row['cost']['selected_amount'] is None
    assert report['issues'] and all(issue['source'] and issue['observed_at'] and issue['processing_status'] for issue in report['issues'])
    code, guarded = cli(path, capsys, '--fail-on-review')
    assert code == 2 and guarded['status'] == 'needs_review'


def test_repeated_real_audit_never_writes_business_rows(tmp_path, monkeypatch):
    path = database(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    statements = []
    original = audit.connect_readonly
    def trace(path):
        connection = original(path)
        assert connection.execute('PRAGMA query_only').fetchone()[0] == 1
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(audit, 'connect_readonly', trace)
    first = historical_audit(path).payload()
    second = historical_audit(path).payload()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert first['status'] == second['status'] == 'no_data'
    assert not any(sql.lstrip().split()[0].upper() in {'INSERT','UPDATE','DELETE','ALTER','CREATE','DROP','REPLACE'} for sql in statements)


def identity(shop='TH', product='p1', variant='v1', sku='660017', platform='tiktok'):
    return dict(zip(('platform', 'shop_key', 'product_id', 'variant_id', 'seller_sku'), (platform, shop, product, variant, sku)))


def approved_alias(source, target):
    row = {'status': 'APPROVED', 'alias_identity': source, 'canonical_identity': target,
           'approval_ref': 'fixture://approved-variant-alias/1', 'approved_by': 'synthetic-owner',
           'approved_at': '2026-09-05T00:00:00+00:00'}
    row['evidence_digest'] = 'sha256:' + hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
    return row


def evidence_file(tmp_path, aliases=(), history=()):
    path = tmp_path / 'identity-evidence.json'
    path.write_text(json.dumps({'schema_version': 'catalog-review-evidence/v1', 'aliases': list(aliases), 'historical_products': list(history)}), encoding='utf-8')
    return path


PAIRED = '''
INSERT INTO shops VALUES ('TH','s1','TH',1), ('MY','s2','MY',1);
INSERT INTO products VALUES ('v1','TH','p1','660017','THB','first spec','ACTIVE',1);
INSERT INTO products VALUES ('v2','MY','p2','990017','MYR','equivalent approved spec','ACTIVE',1);
INSERT INTO sku_costs VALUES ('v1',3,'CNY','invoice fixture',10);
'''


def test_actual_cli_keeps_approved_alias_distinct_without_rewriting_raw_identity(tmp_path, capsys, record_property):
    path = database(tmp_path, PAIRED)
    evidence = evidence_file(tmp_path, [approved_alias(identity('MY','p2','v2','990017'), identity())])
    before = path.read_bytes()
    code, report = cli(path, capsys, '--identity-evidence', str(evidence), '--fail-on-review')
    assert code == 2 and report['status'] == 'needs_review'
    target = next(r for r in report['records'] if r['identity']['variant_id'] == 'v2')
    assert target['matching']['basis'] == 'declared_approved_alias'
    assert target['identity'] == identity('MY','p2','v2','990017')
    assert target['raw_identity']['seller_sku'] == '990017'
    assert target['cost']['status'] == 'unresolved_candidates'
    assert target['cost']['selected_amount'] is None
    assert target['cost']['candidates'][0]['matching_basis'] == 'alias_reference'
    assert target['cost']['candidates'][0]['currency'] == 'CNY'
    assert target['sale_currency'] == 'MYR'
    assert report['cost_coverage']['fallback_resolved_rows'] == 0
    assert path.read_bytes() == before
    output = tmp_path / 'actual-cli-review.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    record_property('actual_cli_sample', str(output))
    record_property('actual_cli_sample_sha256', hashlib.sha256(output.read_bytes()).hexdigest())


@pytest.mark.parametrize('change', ['draft', 'missing_scope', 'missing_provenance', 'digest_drift'])
def test_unapproved_or_drifted_alias_evidence_never_promotes_candidates(tmp_path, capsys, change):
    path = database(tmp_path, PAIRED)
    alias = approved_alias(identity('MY','p2','v2','990017'), identity())
    if change == 'draft': alias['status'] = 'DRAFT'
    elif change == 'missing_scope': alias['alias_identity'].pop('shop_key')
    elif change == 'missing_provenance': alias.pop('approval_ref')
    else: alias['canonical_identity']['product_id'] = 'different-product'
    evidence = evidence_file(tmp_path, [alias])
    code, result = cli(path, capsys, '--identity-evidence', str(evidence))
    assert code == 3 and result['status'] == 'check_failed'
    assert result['counts']['products'] is None
    assert result['error']['code'] == 'invalid_review_evidence'


def test_conflicting_approved_aliases_remain_ambiguous(tmp_path, capsys):
    path = database(tmp_path, PAIRED + '''
    INSERT INTO products VALUES ('v3','TH','p3','0099','THB','different spec','ACTIVE',1);
    INSERT INTO sku_costs VALUES ('v3',9,'CNY','different invoice',11);
    ''')
    src = identity('MY','p2','v2','990017')
    evidence = evidence_file(tmp_path, [approved_alias(src, identity()), approved_alias(src, identity('TH','p3','v3','0099'))])
    _, report = cli(path, capsys, '--identity-evidence', str(evidence))
    row = next(r for r in report['records'] if r['identity'] == src)
    assert row['matching']['basis'] == 'ambiguous'
    assert row['cost']['status'] == 'unresolved_candidates'
    assert row['cost']['selected_amount'] is None
    assert len(row['cost']['candidates']) == 2
    assert any(i['code'] == 'ambiguous_full_identity_or_alias' for i in report['issues'])


def test_same_variant_key_across_shops_is_not_an_exact_cost_identity(tmp_path):
    path = database(tmp_path, PAIRED + '''
    INSERT INTO products VALUES ('v1','MY','p-other','1234','MYR','another variant','ACTIVE',1);
    ''')
    report = historical_audit(path).payload()
    rows = [r for r in report['records'] if r['identity']['variant_id'] == 'v1']
    assert len(rows) == 2
    assert all(r['cost']['status'] == 'unresolved_candidates' for r in rows)
    assert all(r['cost']['candidates'][0]['matching_basis'] == 'ambiguous_variant_key' for r in rows)
    assert all(len(r['cost']['candidates'][0]['applicable_identities']) == 2 for r in rows)


def test_complete_conflicting_cost_candidates_keep_sources_times_and_no_max_choice(tmp_path, capsys, record_property):
    path = database(tmp_path, '''
    INSERT INTO shops VALUES ('TH','s1','TH',1);
    INSERT INTO products VALUES ('v1','TH','p1','660017','THB','one variant','ACTIVE',1);
    INSERT INTO sku_costs VALUES ('v1',1,'CNY','old invoice',10), ('v1',9,'CNY','new invoice',20);
    ''')
    _, report = cli(path, capsys)
    assert report['counts']['products'] == 1  # A multi-cost join must not multiply product rows.
    row = report['records'][0]
    assert row['cost']['status'] == 'conflicting'
    assert row['cost']['selected_amount'] is None
    assert [(c['amount'], c['currency'], c['source']['source_updated_at'], c['source']['note']) for c in row['cost']['candidates']] == [('1','CNY',10,'old invoice'), ('9','CNY',20,'new invoice')]
    assert [c['source']['row_locator']['rowid'] for c in row['cost']['candidates']] == [1, 2]
    assert all(c['valid_from'] is None and c['valid_to'] is None for c in row['cost']['candidates'])
    assert all(c['applicable_specifications'] == ['one variant'] for c in row['cost']['candidates'])
    out = tmp_path / 'actual-conflict-review.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    record_property('actual_cli_sample', str(out))
    record_property('actual_cli_sample_sha256', hashlib.sha256(out.read_bytes()).hexdigest())


def test_current_sku_projection_does_not_choose_one_conflicting_legacy_observation(tmp_path):
    path = database(tmp_path, '''
    INSERT INTO shops VALUES ('TH','s1','TH',1);
    INSERT INTO products VALUES ('v1','TH','p1','660017','THB','one variant','ACTIVE',1);
    INSERT INTO sku_costs VALUES ('v1',1,'CNY','old invoice',10), ('v1',9,'CNY','new invoice',20);
    ''')
    row = audit.audit_catalog_database(path).payload()['records'][0]
    assert row['cost']['status'] == 'conflicting'
    assert row['cost']['selected_amount'] is None
    assert [c['amount'] for c in row['cost']['candidates']] == ['1', '9']


@pytest.mark.parametrize('value,currency', [(None, 'CNY'), (0, 'CNY'), (3, 'USD')])
def test_current_sku_projection_does_not_inherit_invalid_legacy_cost(tmp_path, value, currency):
    path = database(tmp_path, '''
    INSERT INTO shops VALUES ('TH','s1','TH',1);
    INSERT INTO products VALUES ('v1','TH','p1','660017','THB','one variant','ACTIVE',1);
    ''')
    with sqlite3.connect(path) as connection:
        connection.execute('INSERT INTO sku_costs VALUES (?,?,?,?,?)', ('v1', value, currency, 'invoice', 10))
    row = audit.audit_catalog_database(path).payload()['records'][0]
    assert row['cost']['status'] == 'invalid'
    assert row['cost']['selected_amount'] is None
    assert row['cost']['candidates'][0]['source']['note'] == 'invoice'


def test_current_sku_projection_does_not_erase_expired_legacy_time_bound(tmp_path):
    path = database(tmp_path, '''
    INSERT INTO shops VALUES ('TH','s1','TH',1);
    INSERT INTO products VALUES ('v1','TH','p1','660017','THB','one variant','ACTIVE',1);
    ALTER TABLE sku_costs ADD COLUMN valid_from TEXT;
    ALTER TABLE sku_costs ADD COLUMN valid_to TEXT;
    INSERT INTO sku_costs VALUES ('v1',3,'CNY','expired invoice',10,NULL,'2000-01-01T00:00:00+00:00');
    ''')
    row = audit.audit_catalog_database(path).payload()['records'][0]
    assert row['cost']['status'] == 'unresolved_candidates'
    assert row['cost']['selected_amount'] is None
    assert row['cost']['candidates'][0]['time_applicability'] == 'expired'


@pytest.mark.parametrize('amount', [None, 0, -1, 'NaN', 'Infinity', 'bad amount'])
def test_unknown_invalid_and_nonpositive_costs_are_not_defaults(tmp_path, amount):
    path = database(tmp_path, '''
    INSERT INTO shops VALUES ('TH','s1','TH',1);
    INSERT INTO products VALUES ('v1','TH','p1','660017','THB','one','ACTIVE',1);
    ''')
    with sqlite3.connect(path) as connection:
        connection.execute('INSERT INTO sku_costs VALUES (?,?,?,?,?)', ('v1', amount, 'CNY', 'synthetic', 1))
    row = historical_audit(path).payload()['records'][0]
    assert row['cost']['status'] == 'invalid'
    assert row['cost']['selected_amount'] is None
    assert row['cost']['candidates'][0]['amount'] == (str(amount) if amount in (0, -1) else None)


def test_declared_cost_currency_conflict_is_visible_without_conversion(tmp_path):
    path = database(tmp_path, PAIRED.replace("3,'CNY'", "3,'USD'"))
    row = historical_audit(path).payload()['records'][0]
    candidate = row['cost']['candidates'][0]
    assert row['sale_currency'] == 'THB'
    assert candidate['currency'] == 'USD' and candidate['column_currency'] == 'CNY'
    assert not candidate['valid_value'] and row['cost']['status'] == 'invalid'


def test_equal_decimal_cost_observations_do_not_become_false_conflict(tmp_path):
    path = database(tmp_path, '''
    INSERT INTO shops VALUES ('TH','s1','TH',1);
    INSERT INTO products VALUES ('v1','TH','p1','660017','THB','one','ACTIVE',1);
    INSERT INTO sku_costs VALUES ('v1','3','CNY','first',1), ('v1','3.0','CNY','second',2);
    ''')
    row = historical_audit(path).payload()['records'][0]
    assert row['cost']['status'] == 'available'
    assert len(row['cost']['candidates']) == 2
    assert row['cost']['selected_amount'] is None


def test_historical_missing_invalid_and_approved_analytics_are_separate_and_retained(tmp_path, capsys, record_property):
    path = database(tmp_path, PAIRED + '''
    INSERT INTO product_analytics VALUES ('p1','TH',10), ('old-approved','TH',10),
      ('known-retired','TH',10), ('unknown-old','TH',10), (NULL,'TH',10), ('p1','MY',10);
    ''')
    history = [{'platform':'tiktok','shop_key':'TH','product_id':'known-retired', 'retired_at':'2025-01-01T00:00:00+00:00','source_ref':'fixture://retired-product/snapshot'}]
    aliases = [approved_alias(identity('TH','old-approved','old-v','0001'), identity())]
    evidence = evidence_file(tmp_path, aliases, history)
    before = path.read_bytes()
    _, report = cli(path, capsys, '--identity-evidence', str(evidence))
    assert [row['classification'] for row in report['analytics']] == ['exact_product','alias_reference','historical_product','missing_mapping','invalid_identity','missing_mapping']
    assert report['analytics'][2]['processing_status'] == 'retained_pending_review'
    assert report['analytics'][3]['historical_evidence'] == []
    assert report['analytics'][-1]['candidate_products'][0]['shop_key'] == 'TH'
    assert path.read_bytes() == before
    assert all(row['suggested_action'] == 'retain_original_fact' and not row['apply_allowed'] for row in report['analytics'])
    out = tmp_path / 'actual-analytics-review.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    record_property('actual_cli_sample', str(out))
    record_property('actual_cli_sample_sha256', hashlib.sha256(out.read_bytes()).hexdigest())


def test_same_sku_across_shops_is_a_candidate_not_same_shop_duplicate(tmp_path):
    path = database(tmp_path, PAIRED.replace("'990017'", "'660017'") + "INSERT INTO sku_costs VALUES ('v2',3,'CNY','invoice2',10);")
    report = historical_audit(path).payload()
    assert report['identity']['same_shop_seller_sku_duplicates'] == []
    assert len(report['records']) == 2
    assert all(len(r['matching']['same_seller_sku_candidates']) == 1 for r in report['records'])
    assert {r['identity']['shop_key'] for r in report['records']} == {'TH','MY'}


def test_readonly_failure_is_not_empty_data(tmp_path, monkeypatch):
    path = database(tmp_path)
    def inaccessible(path):
        raise sqlite3.OperationalError('synthetic read denied')
    monkeypatch.setattr(audit, 'connect_readonly', inaccessible)
    result = historical_audit(path).payload()
    assert result['status'] == 'check_failed' and not result['verified']
    assert result['counts']['products'] is None


def test_invalid_identity_is_never_labeled_exact(tmp_path):
    path = database(tmp_path, PAIRED.replace("'990017'", "NULL"))
    row = next(r for r in historical_audit(path).payload()['records'] if r['identity']['variant_id'] == 'v2')
    assert row['raw_identity']['seller_sku'] is None
    assert row['matching']['basis'] == 'invalid_identity'


def test_identity_issue_updates_each_affected_record_processing_state(tmp_path):
    path = database(tmp_path, '''
    INSERT INTO shops VALUES ('TH','s1','TH',1);
    INSERT INTO products VALUES ('v1','TH','p1','660017','THB','one','ACTIVE',1), ('v2','TH','p2','660017','THB','two','ACTIVE',1);
    INSERT INTO sku_costs VALUES ('v1',3,'CNY','one',1), ('v2',3,'CNY','two',1);
    ''')
    result = historical_audit(path).payload()
    assert len(result['identity']['same_shop_seller_sku_duplicates']) == 1
    assert all(r['cost']['status'] == 'available' for r in result['records'])
    assert all(r['processing_status'] == 'needs_review' for r in result['records'])


def test_cost_candidate_decimal_text_is_lossless(tmp_path):
    value = '1234567890123456789012345678.123456789'
    path = database(tmp_path, '''
    INSERT INTO shops VALUES ('TH','s1','TH',1);
    INSERT INTO products VALUES ('v1','TH','p1','660017','THB','one','ACTIVE',1);
    ''')
    with sqlite3.connect(path) as connection:
        connection.execute('INSERT INTO sku_costs VALUES (?,?,?,?,?)', ('v1', value, 'CNY', 'precise fixture', 1))
    candidate = historical_audit(path).payload()['records'][0]['cost']['candidates'][0]
    assert candidate['amount'] == value and candidate['raw_amount'] == value


def test_analytics_alias_with_unresolved_competing_target_is_ambiguous(tmp_path, capsys):
    path = database(tmp_path, PAIRED + "INSERT INTO product_analytics VALUES ('old-approved','TH',10);")
    old = identity('TH','old-approved','old-v','0001')
    evidence = evidence_file(tmp_path, [approved_alias(old, identity()), approved_alias(old, identity('TH','not-present','gone-v','1111'))])
    _, result = cli(path, capsys, '--identity-evidence', str(evidence))
    assert result['analytics'][0]['classification'] == 'ambiguous_mapping'
    assert result['analytics'][0]['processing_status'] == 'needs_review'


def test_declared_approval_json_is_not_a_verified_authority_or_cost_resolution(tmp_path, capsys):
    path = database(tmp_path, PAIRED)
    evidence = evidence_file(tmp_path, [approved_alias(identity('MY','p2','v2','990017'), identity())])
    code, result = cli(path, capsys, '--identity-evidence', str(evidence), '--fail-on-review')
    assert code == 2 and result['status'] == 'needs_review'
    assert result['cost_coverage']['fallback_resolved_rows'] == 0
    reference = result['alias_references'][0]
    assert reference['verification']['authority_verified'] is False
    assert reference['verification']['level'] == 'content_and_identity_binding_only'


@pytest.mark.parametrize('case', ['expired', 'source_not_observed'])
def test_alias_reference_reports_expiry_and_source_binding_without_authority(tmp_path, capsys, case):
    path = database(tmp_path, PAIRED)
    src = identity('MY','p2','v2','990017') if case == 'expired' else identity('MY','unobserved','v2','990017')
    alias = approved_alias(src, identity())
    if case == 'expired':
        alias['valid_until'] = '2020-01-01T00:00:00+00:00'
        alias['evidence_digest'] = 'sha256:' + hashlib.sha256(json.dumps({k:v for k,v in alias.items() if k != 'evidence_digest'}, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
    evidence = evidence_file(tmp_path, [alias])
    _, result = cli(path, capsys, '--identity-evidence', str(evidence))
    assert result['alias_references'][0]['verification']['scope_status'] == case
    assert result['cost_coverage']['fallback_resolved_rows'] == 0


@pytest.mark.parametrize('stamp', ['not-a-date', '2026-99-30T00:00:00Z', '2026-09-05T00:00:00'])
def test_invalid_or_timezone_free_alias_date_is_check_failed(tmp_path, capsys, stamp):
    path = database(tmp_path, PAIRED)
    alias = approved_alias(identity('MY','p2','v2','990017'), identity())
    alias['approved_at'] = stamp
    alias['evidence_digest'] = 'sha256:' + hashlib.sha256(json.dumps({k:v for k,v in alias.items() if k != 'evidence_digest'}, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
    evidence = evidence_file(tmp_path, [alias])
    code, result = cli(path, capsys, '--identity-evidence', str(evidence))
    assert code == 3 and result['error']['code'] == 'invalid_review_evidence'


def test_nonfinite_reference_content_cannot_escape_failure_envelope(tmp_path, capsys):
    path = database(tmp_path)
    evidence = evidence_file(tmp_path)
    evidence.write_text('{"schema_version":"catalog-review-evidence/v1","extra":NaN}', encoding='utf-8')
    code, result = cli(path, capsys, '--identity-evidence', str(evidence))
    assert code == 3 and result['status'] == 'check_failed'


def test_cli_bad_reference_file_does_not_read_database(tmp_path, capsys, monkeypatch):
    def no_database(*args, **kwargs):
        raise AssertionError('invalid reference file must fail before database access')
    monkeypatch.setattr(audit, 'connect_readonly', no_database)
    evidence = tmp_path / 'bad-evidence.json'
    evidence.write_text('{broken', encoding='utf-8')
    code, result = cli(tmp_path / 'not-opened.db', capsys, '--identity-evidence', str(evidence))
    assert code == 3 and result['error']['code'] == 'review_evidence_read_failed'


def test_individual_issue_binds_database_and_row_source(tmp_path):
    path = database(tmp_path, PAIRED)
    result = historical_audit(path).payload()
    assert result['issues']
    assert all(Path(i['source']['database_path']) == path.resolve() for i in result['issues'])
    assert all(i['source']['table'] and i['source']['row_locator']['rowid'] for i in result['issues'])
    row = result['records'][0]
    assert row['source']['source_updated_at'] == 1
    assert result['business_writes'] == 0 and not result['apply_allowed']


def test_suffix_candidate_cost_disagreement_is_reviewable_without_merging_variants(tmp_path, capsys):
    path = database(tmp_path, PAIRED + "INSERT INTO sku_costs VALUES ('v2',9,'CNY','other variant invoice',20);")
    _, report = cli(path, capsys)
    assert len(report['records']) == 2
    assert all(r['cost']['status'] == 'available' for r in report['records'])
    assert report['cost_coverage']['conflicts'] == []  # No proof these are one cost scope.
    assert report['cost_coverage']['candidate_conflicts']
    assert report['status'] == 'needs_review'
    assert any(i['code'] == 'candidate_cost_disagreement' for i in report['issues'])


@pytest.mark.parametrize('valid_from,valid_to,state', [
    (None, '2000-01-01T00:00:00+00:00', 'expired'),
    ('2999-01-01T00:00:00+00:00', None, 'not_yet_effective'),
    ('not-a-date', None, 'invalid'),
    (None, '2026-09-05T00:00:00', 'invalid'),
    ('2025-01-01T00:00:00+00:00', '2020-01-01T00:00:00+00:00', 'invalid'),
    ('2000-01-01T00:00:00+00:00', '2999-01-01T00:00:00+00:00', 'current_declared_interval'),
])
def test_explicit_cost_time_bounds_distinguish_current_declaration_from_invalid_or_inapplicable(tmp_path, capsys, valid_from, valid_to, state, record_property):
    path = database(tmp_path, '''
    INSERT INTO shops VALUES ('TH','s1','TH',1);
    INSERT INTO products VALUES ('v1','TH','p1','660017','THB','one','ACTIVE',1);
    ALTER TABLE sku_costs ADD COLUMN valid_from TEXT;
    ALTER TABLE sku_costs ADD COLUMN valid_to TEXT;
    ''')
    with sqlite3.connect(path) as connection:
        connection.execute('INSERT INTO sku_costs VALUES (?,?,?,?,?,?,?)', ('v1', 3, 'CNY', 'dated fixture', 1, valid_from, valid_to))
    before = path.read_bytes()
    code, report = cli(path, capsys, '--fail-on-review')
    is_current = state == 'current_declared_interval'
    assert code == (0 if is_current else 2)
    assert report['status'] == ('verified' if is_current else 'needs_review')
    row = report['records'][0]
    candidate = row['cost']['candidates'][0]
    assert candidate['amount'] == '3' and candidate['valid_value'] is True
    assert candidate['time_applicability'] == state
    assert candidate['date_validation'] == ('failed' if state == 'invalid' else 'passed')
    assert candidate['authority_verified'] is False
    assert (candidate['valid_from'], candidate['valid_to']) == (valid_from, valid_to)
    assert candidate['usable_identity_match'] is is_current
    assert row['cost']['status'] == ('available' if is_current else 'unresolved_candidates')
    assert row['cost']['resolved_candidate_ids'] == ([candidate['candidate_id']] if is_current else [])
    assert row['cost']['selected_amount'] is None
    assert any(i['code'] == 'cost_time_' + state for i in report['issues']) is (not is_current)
    assert path.read_bytes() == before
    if state == 'expired':
        out = tmp_path / 'actual-expired-cost-review.json'
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        record_property('actual_cli_sample', str(out))
        record_property('actual_cli_sample_sha256', hashlib.sha256(out.read_bytes()).hexdigest())
