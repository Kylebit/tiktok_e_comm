"""Actual legacy CLI JSON/HTML with governed cost and explicit UTC offset."""
import csv
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest
from test_i05_profit_facts import catalog_db


@pytest.mark.parametrize('case', ['valid', 'out_of_period', 'conflicting_unselected', 'missing_cost', 'mixed', 'utc_enters', 'utc_exits'])
def test_actual_script_cost_policy(tmp_path, monkeypatch, record_property, case):
    data=tmp_path/'data';data.mkdir()
    catalog_db(data).rename(data/'shop.db')
    with sqlite3.connect(data/'shop.db') as db:
        db.execute('DELETE FROM sku_costs WHERE updated_at=10')
        db.execute("UPDATE products SET seller_sku='9000'")
        db.execute('ALTER TABLE sku_costs ADD COLUMN valid_from TEXT')
        db.execute('ALTER TABLE sku_costs ADD COLUMN valid_to TEXT')
        db.execute("UPDATE sku_costs SET valid_from='2026-08-03T00:00:00+07:00',valid_to='2026-08-10T00:00:00+07:00'")
        if case=='out_of_period':
            db.execute("UPDATE sku_costs SET valid_from='2026-09-01T00:00:00+07:00',valid_to='2026-10-01T00:00:00+07:00'")
        elif case=='conflicting_unselected':
            db.execute("INSERT INTO sku_costs VALUES ('variant','1',10,'2026-08-03T00:00:00+07:00','2026-08-10T00:00:00+07:00')")
        elif case=='missing_cost': db.execute('DELETE FROM sku_costs')
        elif case=='mixed':
            db.execute("INSERT INTO products VALUES ('bad-variant','9001','Missing cost','One','','THB','fixture',20,'bad-product')")
    income=tmp_path/'CURSOR/Income_Data';income.mkdir(parents=True)
    (tmp_path/'outputs').mkdir()
    stamp='2026-08-02T20:00:00Z' if case=='utc_enters' else '2026-08-09T20:00:00Z' if case=='utc_exits' else '2026-08-05'
    row={'Type':'Order','Order/adjustment ID':'same-shop-order','SKU ID':'variant','Statement Date':stamp,'Total settlement amount':'100','Currency':'THB','Quantity':'1','Subtotal after seller discounts':'200','shop_id':'fixture'}
    rows=[row]
    if case=='mixed': rows.append({**row,'Order/adjustment ID':'missing-cost-order','SKU ID':'bad-variant'})
    with (income/'income_TH_260803_260809.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(row));writer.writeheader();writer.writerows(rows)
    script=Path(__file__).resolve().parents[1]/'domains/data_operations/skills/manage-profit-settlement/scripts/generate_local_weekly.py'
    spec=importlib.util.spec_from_file_location('legacy_cost_script',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'_live_fx',lambda:{'rates':{'THB':'0.2'},'provider':'synthetic-fixed-FX','as_of':'2026-08-06T00:00:00Z'})
    seen=[]; original=module.adapt_local_profit_snapshots
    def adapt(*args,**kwargs):
        result=original(*args,**kwargs)
        seen.append({'resolved_costs':dict(kwargs['costs_by_sku']),'rows':result.payload()['rows']})
        return result
    monkeypatch.setattr(module,'adapt_local_profit_snapshots',adapt)
    monkeypatch.setattr(sys,'argv',['generate_local_weekly.py','--root',str(tmp_path),'--start','2026-08-03','--end','2026-08-09','--timezone','+07:00','--output',str(tmp_path/'reports')])
    assert module.main()==2
    result_path=tmp_path/'reports/weekly_profit_2026-08-03_2026-08-09.json'
    bundle=json.loads(result_path.read_text(encoding='utf-8'))
    report=bundle['reports']['tiktok']['report']
    html=(tmp_path/'reports/tiktok_2026-08-03_2026-08-09.html').read_text(encoding='utf-8')
    receipt=tmp_path/'actual-cost-consumer.json'
    receipt.write_text(json.dumps({'case':case,'bundle':bundle,'adapter_receipts':seen,'html_path':str(tmp_path/'reports/tiktok_2026-08-03_2026-08-09.html')},default=str,indent=2),encoding='utf-8')
    record_property('actual_consumer_receipt',str(receipt))
    allowed=case in {'valid','mixed','utc_enters'}
    assert report['totals']['product_cost_cny']==('8' if allowed else None)
    assert report['totals']['profit_cny']==('3.200' if allowed else None)
    assert bundle['cost_policy']['temporary_assumptions_selected'] is False
    assert bundle['cost_policy']['period_start']=='2026-08-03T00:00:00+07:00'
    assert bundle['cost_policy']['period_end_exclusive']=='2026-08-10T00:00:00+07:00'
    assert report['source']['cost_policy']['snapshot_id']==bundle['cost_policy']['snapshot_id']
    assert report['period']['timezone']=='+07:00'
    if case in {'out_of_period','conflicting_unselected','missing_cost','mixed'}:
        expected = (
            'unusable_cost_for_period' if case == 'out_of_period'
            else 'missing_cost' if case == 'conflicting_unselected'
            else 'cost_assumption_not_selected'
        )
        assert expected in {issue['code'] for issue in report['quality_issues']}
        assert expected in html
    if not allowed:
        assert '<span>商品成本 CNY</span><strong>—</strong>' in html
        assert '<span>利润 CNY</span><strong>—</strong>' in html
    if case in {'out_of_period','conflicting_unselected','missing_cost'}:
        assert not seen[0]['resolved_costs']
        assert seen[0]['rows'][0]['cost_cny'] is None
    if case=='utc_enters': assert report['order_lines'][0]['settled_at'].startswith('2026-08-03T03:00:00+07:00')
    if case=='utc_exits': assert bundle['reports']['tiktok']['adapter']['out_of_period_row_count']==1
    if case=='valid':
        with sqlite3.connect(data/'shop.db') as db:
            db.execute("UPDATE sku_costs SET valid_to='2026-08-11T00:00:00+07:00'")
        argv=list(sys.argv);argv[-1]=str(tmp_path/'reports-identity-change')
        monkeypatch.setattr(sys,'argv',argv)
        assert module.main()==2
        changed=json.loads((tmp_path/'reports-identity-change/weekly_profit_2026-08-03_2026-08-09.json').read_text(encoding='utf-8'))
        assert changed['reports']['tiktok']['report']['totals']==report['totals']
        assert changed['reports']['tiktok']['report']['report_id']!=report['report_id']
        assert changed['cost_policy']['snapshot_id']!=bundle['cost_policy']['snapshot_id']
        record_property('identity_change_receipt',str(tmp_path/'reports-identity-change/weekly_profit_2026-08-03_2026-08-09.json'))
