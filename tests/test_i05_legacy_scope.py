"""Actual legacy CLI and preview consumers with owned synthetic snapshots."""
import csv
from datetime import date, datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest
from test_i05_profit_facts import catalog_db
from shared_platform.weekly_profit_runner import build_weekly_profit_preview


@pytest.mark.parametrize('case', ['same_shop', 'other_shop', 'missing_shop', 'other_platform', 'raw_sku_mismatch', 'variant_mismatch', 'mixed'])
def test_legacy_consumers_verify_scope(tmp_path, monkeypatch, case, record_property):
    data = tmp_path/'data'; data.mkdir()
    catalog_db(data).rename(data/'shop.db')
    with sqlite3.connect(data/'shop.db') as db:
        db.execute('DELETE FROM sku_costs WHERE updated_at=10')
        db.execute("UPDATE products SET seller_sku='9000'")
        db.execute("INSERT INTO sku_logistics_weights VALUES ('9000','500','1','100','100','100',20)")
        # Unrelated catalog defects must not block this valid SKU's calculation.
        db.execute("INSERT INTO products VALUES ('bad','UNRELATED','Bad','','','THB','unknown',20,'bad')")
    income=tmp_path/'CURSOR/Income_Data'; income.mkdir(parents=True)
    shop = '' if case == 'missing_shop' else 'other-shop' if case in {'other_shop','mixed'} else 'fixture'
    row={'Type':'Order','Order/adjustment ID':'order-B','SKU ID':'variant','Statement Date':'2026-08-05','Total settlement amount':'100','Currency':'THB','Quantity':'1','Subtotal after seller discounts':'200','shop_id':shop,'platform':'shopee' if case=='other_platform' else 'tiktok'}
    if case=='raw_sku_mismatch': row['Seller SKU']='TEST-9000'
    if case=='variant_mismatch': row.update({'SKU ID':'other-variant','Seller SKU':'9000'})
    rows=[row]
    if case=='mixed': rows.append({**row,'Order/adjustment ID':'order-A','shop_id':'fixture'})
    with (income/'income_TH_260803_260809.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(row)); writer.writeheader(); writer.writerows(rows)
    outputs=tmp_path/'outputs';outputs.mkdir()
    shopee={'headers':[{'name':n} for n in ['Order SN','SKU','Release Time','Currency']], 'rows':[{'cells':['shopee-B','9000','2026-08-05','THB'],'region':'TH','currency':'THB','settlement':'100','quantity':'1','subtotal':'200','shop_id':'other-shop'}]}
    (outputs/'weekly_shopee_profit_20260803_20260809.html').write_text('const DATA = '+json.dumps(shopee)+';')
    fx=tmp_path/'synthetic-fx.json';fx.write_text(json.dumps({'exchange_rates':{'THB':'0.2'}}))
    script=Path(__file__).resolve().parents[1]/'domains/data_operations/skills/manage-profit-settlement/scripts/generate_local_weekly.py'
    spec=importlib.util.spec_from_file_location('legacy_script',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'_live_fx',lambda:{'rates':{'THB':'0.2'},'provider':'synthetic-FX-boundary','as_of':'2026-08-06T00:00:00Z'})
    observed=[]; original=module.enrich_settlement_row
    def watch(row,catalog):
        result=original(row,catalog);observed.append(result);return result
    monkeypatch.setattr(module,'enrich_settlement_row',watch)
    monkeypatch.setattr(sys,'argv',['generate_local_weekly.py','--root',str(tmp_path),'--start','2026-08-03','--end','2026-08-09','--timezone','+07:00','--output',str(tmp_path/'reports')])
    assert module.main()==2
    bundle=json.loads((tmp_path/'reports/weekly_profit_2026-08-03_2026-08-09.json').read_text(encoding='utf-8'))
    preview=build_weekly_profit_preview(root=tmp_path,settings_path=fx,period_start=date(2026,8,3),period_end=date(2026,8,9),generated_at=datetime(2026,8,10,tzinfo=timezone.utc),code_version='synthetic-I05-B')
    receipt=tmp_path/'actual-consumers.json'
    receipt.write_text(json.dumps({'case':case,'rows':observed,'script':bundle,'preview':preview.report.payload(),'preview_summary':preview.summary(),'preview_adaptation':preview.source_adaptation.payload()},default=str,indent=2),encoding='utf-8')
    record_property('actual_consumer_receipt',str(receipt))
    good=case in {'same_shop','mixed'}
    tiktok=bundle['reports']['tiktok']['report']
    assert tiktok['totals']['product_cost_cny']==('8' if good else None)
    assert tiktok['totals']['profit_cny']==('3.200' if good else None)
    assert preview.summary()['preliminary_profit_cny']==('12.0' if good else None)
    assert bundle['reports']['shopee']['report']['totals']['profit_cny'] is None
    assert 'catalog_scope_mismatch' in {i['code'] for i in bundle['reports']['shopee']['report']['quality_issues']}
    assert 'catalog_scope_mismatch' in (tmp_path/'reports/shopee_2026-08-03_2026-08-09.html').read_text(encoding='utf-8')
    for item in observed:
        if item.get('catalog_scope_issue'):
            assert item.get('unit_weight_g') is None
            assert item.get('cost_cny') is None
            assert item['canonical_sku']==''
            assert item['seller_sku']==('TEST-9000' if case=='raw_sku_mismatch' and item['platform']=='tiktok' else '9000')
    tiktok_row=next(x for x in observed if x['order_id']=='order-B')
    assert tiktok_row['shop_id']==shop
    if case=='same_shop':
        assert tiktok_row['unit_weight_g']=='500'
        assert tiktok_row['catalog_identity']['shop_key']=='fixture'
    else:
        expected='catalog_scope_unverified' if case=='missing_shop' else 'catalog_scope_mismatch'
        assert expected in {i['code'] for i in tiktok['quality_issues']}
        assert any(expected in i.code for i in preview.report.quality_issues)
