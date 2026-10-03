import hashlib
import sqlite3

import pytest

from core.db import connect_readonly
from domains.product_operations.catalog_database_audit import audit_catalog_connection
from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
from domains.data_operations.profit_settlement.cost_policy import resolve_temporary_cost_policy
from shared_platform.catalog_sku_costs import SCHEMA, digest
from test_i05_profit_facts import catalog_db


@pytest.mark.parametrize('mode', ['dated', 'conflicting', 'current_only'])
def test_historical_profit_never_substitutes_current_manual_cost(tmp_path, mode):
    path = catalog_db(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE products SET seller_sku='9000'")
        conn.execute('ALTER TABLE sku_costs ADD COLUMN valid_from TEXT')
        conn.execute('ALTER TABLE sku_costs ADD COLUMN valid_to TEXT')
        conn.execute("UPDATE sku_costs SET valid_from='2026-08-01T00:00:00+00:00',valid_to='2026-09-01T00:00:00+00:00'")
        if mode == 'dated': conn.execute('DELETE FROM sku_costs WHERE updated_at=10')
        if mode == 'current_only': conn.execute('DELETE FROM sku_costs')
        conn.executescript(SCHEMA)
        conn.execute('INSERT INTO catalog_sku_costs VALUES(?,?,?,?)', ('internal:'+digest('9000'), '99', 1, '{}'))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    current = connect_readonly(path)
    try:
        current.execute('BEGIN')
        review = audit_catalog_connection(current).payload()
        assert {c['amount'] for c in review['records'][0]['cost']['candidates']} == {'99'}
    finally:
        current.close()
    historical = load_local_catalog(path)
    records = historical.cost_records_by_sku['9000']
    assert {c['amount'] for c in records} == ({'8'} if mode == 'dated' else {'8', '1'} if mode == 'conflicting' else set())
    assert all(c['valid_from'] == '2026-08-01T00:00:00+00:00' for c in records)
    assert all(c['valid_to'] == '2026-09-01T00:00:00+00:00' for c in records)
    policy = resolve_temporary_cost_policy(historical, ['9000'], period_start='2026-08-01T00:00:00+00:00', period_end='2026-09-01T00:00:00+00:00')
    if mode == 'dated': assert policy.values['9000']['unit_cost_cny'] == '8'
    elif mode == 'conflicting':
        assert '9000' in historical.blocked_skus
        assert '9000' not in policy.values
        assert any(issue['code'] == 'ambiguous_catalog_identity' and issue['canonical_sku'] == '9000' for issue in policy.issues)
        assert not any(w.code == 'conflicting_cost_high_selected' for w in policy.warnings)
    else:
        assert '9000' not in policy.values
        assert '9000' in historical.blocked_skus
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_historical_invalid_legacy_cost_is_reviewable_without_current_resolver(tmp_path, monkeypatch):
    path = catalog_db(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE products SET seller_sku='9000'")
        conn.execute("UPDATE sku_costs SET cost_cny='invalid'")
    import shared_platform.catalog_sku_costs as current
    def forbidden(*args, **kwargs):
        raise AssertionError('historical view must not run current inherited resolver')
    monkeypatch.setattr(current, 'read_current', forbidden)
    snapshot = load_local_catalog(path)
    assert snapshot.review['status'] == 'needs_review'
    assert snapshot.cost_records_by_sku['9000']
    assert all(not r['valid_value'] for r in snapshot.cost_records_by_sku['9000'])
    policy = resolve_temporary_cost_policy(snapshot, ['9000'])
    assert '9000' not in policy.values
