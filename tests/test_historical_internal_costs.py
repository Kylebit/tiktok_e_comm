from datetime import date
from decimal import Decimal
import sqlite3
import pytest

from test_i05_profit_facts import catalog_db
from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
from domains.data_operations.profit_settlement.monthly_missing_cost_scope import resolve_monthly_missing_cost_policy, monthly_cost_period
from domains.data_operations.profit_settlement.cost_policy import resolve_temporary_cost_policy
from domains.data_operations.profit_settlement.settlement_evidence_adapter import _seller_sku


@pytest.mark.parametrize('other', ['770001', '1234560001', '550001', 'custom0001'])
def test_traceable_group_retains_source_identity_and_rejects_arbitrary_suffix(tmp_path, other):
    path = catalog_db(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE products SET seller_sku='660001'")
        c.execute('DELETE FROM sku_costs WHERE updated_at=10')
        c.execute('ALTER TABLE sku_costs ADD COLUMN valid_from TEXT')
        c.execute('ALTER TABLE sku_costs ADD COLUMN valid_to TEXT')
        c.execute("UPDATE sku_costs SET valid_from='2026-08-01T00:00:00+07:00',valid_to='2026-09-01T00:00:00+07:00'")
        c.execute("INSERT INTO shops VALUES ('other','other','TH')")
        c.execute("INSERT INTO products VALUES ('other-variant',?,'Other','One','','THB','other',20,'other-product')", (other,))
    catalog = load_local_catalog(path)
    assert catalog.cost_records_by_sku['0001']
    records = catalog.cost_records_by_sku[other]
    assert bool(records) is (other == '770001')
    for r in records:
        assert r['currency'] == 'CNY'
        assert r['valid_from'] == '2026-08-01T00:00:00+07:00'
        assert r['valid_to'] == '2026-09-01T00:00:00+07:00'
        assert r['applicable_identities'][0]['seller_sku'] == '660001'
        assert r['applicable_identities'][0]['shop_key'] == 'fixture'
        assert r['source']['table'] == 'sku_costs'
    assert _seller_sku('tiktok', 'v', {'seller_sku': other}, catalog, {}) == ('0001' if other == '770001' else other)


@pytest.mark.parametrize('kind,expected', [('in_period','8'), ('expired',None), ('conflict',None), ('disjoint','8')])
@pytest.mark.parametrize('shared', [True, False])
def test_monthly_period_cost_selection_and_no_unapproved_conflict(tmp_path, kind, expected, shared):
    path=catalog_db(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE products SET seller_sku='0933'")
        c.execute('ALTER TABLE sku_costs ADD COLUMN valid_from TEXT')
        c.execute('ALTER TABLE sku_costs ADD COLUMN valid_to TEXT')
        c.execute("UPDATE sku_costs SET valid_from='2026-08-01T00:00:00+07:00',valid_to='2026-09-01T00:00:00+07:00'")
        if kind in {'in_period','expired'}: c.execute('DELETE FROM sku_costs WHERE updated_at=10')
        if kind=='expired': c.execute("UPDATE sku_costs SET valid_to='2026-08-02T00:00:00+07:00'")
        if kind=='disjoint': c.execute("UPDATE sku_costs SET valid_to='2026-08-01T00:00:00+07:00',valid_from='2026-07-01T00:00:00+07:00' WHERE updated_at=10")
    cat=load_local_catalog(path)
    start,end=date(2026,8,1),date(2026,8,31)
    if shared:
        e={'platform':'tiktok','site':'TH','shop_id':'fixture','orders':[{'order_id':'o','shop_id':'fixture','region':'TH','order_created_at':'2026-08-03T10:00:00+07:00','settlement_status':'settled','items':[{'platform_sku':'variant','seller_sku':'0933'}]}]}
        policy,_=resolve_monthly_missing_cost_policy(cat,e,{'0933'},site='TH',start=start,end=end,allow_missing_default=True,timezone_name='Asia/Bangkok')
    else:
        low,high=monthly_cost_period('TH',start,end,'Asia/Bangkok')
        policy=resolve_temporary_cost_policy(cat,{'0933'},allow_missing_default=True,allow_conflict_high=False,period_start=low,period_end=high)
    assert (policy.values.get('0933') or {}).get('unit_cost_cny') == expected
    assert not any(w.code=='conflicting_cost_high_selected' for w in policy.warnings)


def test_monthly_timezone_cannot_be_another_site():
    with pytest.raises(ValueError): monthly_cost_period('TH',date(2026,8,1),date(2026,8,31),'Asia/Manila')


def test_unmapped_shop_history_cannot_supply_same_internal_sku(tmp_path):
    path=catalog_db(tmp_path)
    with sqlite3.connect(path) as c:
        c.execute("UPDATE products SET seller_sku='660001',shop_cipher='unknown-shop'")
        c.execute("INSERT INTO products VALUES ('other-variant','770001','Other','One','','THB','fixture',20,'other-product')")
    cat=load_local_catalog(path)
    assert not cat.cost_records_by_sku.get('770001')
    assert not cat.cost_records_by_sku.get('0001')
    assert '0001' not in cat.costs_by_sku
