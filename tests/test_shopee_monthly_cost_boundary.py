import importlib.util
import json
from pathlib import Path
import sqlite3
import pytest
from test_i05_profit_facts import catalog_db


@pytest.mark.parametrize('kind,expected', [('valid','8'), ('expired',None), ('conflict',None)])
def test_actual_shopee_main_passes_monthly_cost_boundary(tmp_path, monkeypatch, kind, expected):
    path=catalog_db(tmp_path)
    data=tmp_path/'data';data.mkdir();path.rename(data/'shop.db')
    with sqlite3.connect(data/'shop.db') as c:
        c.execute("UPDATE products SET seller_sku='0933'")
        c.execute('ALTER TABLE sku_costs ADD COLUMN valid_from TEXT')
        c.execute('ALTER TABLE sku_costs ADD COLUMN valid_to TEXT')
        c.execute("UPDATE sku_costs SET valid_from='2026-08-01T00:00:00+07:00',valid_to='2026-09-01T00:00:00+07:00'")
        if kind!='conflict':c.execute('DELETE FROM sku_costs WHERE updated_at=10')
        if kind=='expired':c.execute("UPDATE sku_costs SET valid_to='2026-08-02T00:00:00+07:00'")
    ev=tmp_path/'evidence.json';ev.write_text(json.dumps({'platform':'shopee','site':'TH','status':'ready'}),encoding='utf-8')
    script=Path(__file__).resolve().parents[1]/'domains/data_operations/skills/manage-profit-settlement/scripts/build_shopee_monthly_from_evidence.py'
    spec=importlib.util.spec_from_file_location('shopee_monthly_boundary',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    from types import SimpleNamespace
    # Stop at the real policy boundary; this is not a complete money-report test.
    monkeypatch.setattr(module,'adapt_settlement_evidence',lambda *a,**k:SimpleNamespace(rows=[{'canonical_sku':'0933'}]))
    real=module.resolve_temporary_cost_policy
    class BoundaryVerified(Exception): pass
    def verify(cat, required, **kwargs):
        assert kwargs['period_start'].isoformat()=='2026-08-01T00:00:00+07:00'
        assert kwargs['period_end'].isoformat()=='2026-09-01T00:00:00+07:00'
        assert kwargs['allow_conflict_high'] is False
        policy=real(cat,required,**kwargs)
        assert (policy.values.get('0933') or {}).get('unit_cost_cny')==expected
        assert not any(w.code=='conflicting_cost_high_selected' for w in policy.warnings)
        raise BoundaryVerified
    monkeypatch.setattr(module,'resolve_temporary_cost_policy',verify)
    with pytest.raises(BoundaryVerified):
        module.main(['--evidence',str(ev),'--project-root',str(tmp_path),'--output',str(tmp_path/'never-created'),'--start','2026-08-01','--end','2026-08-31','--site','TH','--ad-rate','0'])
    assert not (tmp_path/'never-created').exists()
