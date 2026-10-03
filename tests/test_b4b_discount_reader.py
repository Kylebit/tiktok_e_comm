import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import runpy

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]/'skills/apply-product-discounts/scripts'
NAMES = sorted(path.stem for path in SCRIPTS.glob('*.py'))


def load(name):
    spec = importlib.util.spec_from_file_location('discount_reader_'+name, SCRIPTS/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('name', NAMES)
def test_every_canonical_python_entry_imports_and_help_is_local(name, monkeypatch, capsys):
    from core import config
    monkeypatch.setattr(config,'load_settings', lambda: pytest.fail('help must not read configuration'))
    monkeypatch.setattr(sys,'argv',[str(SCRIPTS/(name+'.py')),'--help'])
    module = load(name)
    with pytest.raises(SystemExit) as caught:
        module.main()
    assert caught.value.code == 0
    assert 'usage:' in capsys.readouterr().out.lower()
    with pytest.raises(SystemExit) as cli:
        runpy.run_path(str(SCRIPTS/(name+'.py')), run_name='__main__')
    assert cli.value.code == 0
    assert 'usage:' in capsys.readouterr().out.lower()


def test_real_reader_scopes_models_by_shop_item_and_model_and_preserves_gaps(tmp_path, monkeypatch):
    module = load('audit_existing_discount_evidence')
    root=tmp_path/'fixture'; (root/'data').mkdir(parents=True)
    database=root/'data/shop.db'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE shopee_products(shop_id INTEGER,item_id INTEGER,model_id INTEGER,seller_sku TEXT)')
        conn.executemany('INSERT INTO shopee_products VALUES(?,?,?,?)',[(1,101,7,'SKU-A'),(1,202,7,'SKU-B'),
            (1,303,8,'SKU-C'),(1,404,9,'SKU-X'),(1,404,9,'SKU-Y')])
    before=database.read_bytes()
    source=root/'source.json'; output=root/'output.json'
    source.write_text(json.dumps({'stores':[{'region':'PH','activities':[{'discount_id':55}]}]}))
    monkeypatch.setattr(module,'ROOT',root);monkeypatch.setattr(module,'SOURCE',source);monkeypatch.setattr(module,'OUTPUT',output)
    monkeypatch.setattr(module,'sync_shop_ids',lambda:{'PH':1})
    monkeypatch.setattr(module,'ensure_shop_token',lambda shop:'synthetic-only')
    calls=[]
    def get(path,shop,token,params):
        calls.append((path,shop,params))
        page=params['page_no']
        ids=[(101,7),(202,7)] if page==1 else [(303,99),(404,9),(505,None)]
        return {'response':{'more':page==1,'item_list':[{'item_id':item,'model_list':[
            {'model_id':model,'model_original_price':'100','model_promotion_price':'70'}]} for item,model in ids]}}
    from modules.shopee import client, auth, shops
    monkeypatch.setattr(client,'shop_get',get)
    monkeypatch.setattr(auth,'ensure_shop_token',lambda shop:'synthetic-only')
    monkeypatch.setattr(shops,'sync_shop_ids',lambda:{'PH':1})
    monkeypatch.setattr(sys,'argv',['audit_existing_discount_evidence.py','--source',str(source),'--output',str(output),'--database',str(database)])
    with pytest.raises(SystemExit) as cli:
        runpy.run_path(str(SCRIPTS/'audit_existing_discount_evidence.py'),run_name='__main__')
    assert cli.value.code == 0
    result=json.loads(output.read_text())
    assert {row['seller_sku'] for row in result['evidence']} == {'SKU-A','SKU-B'}
    by_sku={row['seller_sku']:row['observations'][0]['item_id'] for row in result['evidence']}
    assert by_sku=={'SKU-A':101,'SKU-B':202}
    assert {row['item_id'] for row in result['identity_gaps']}=={303,404,505}
    assert all(row['seller_sku'] is None for row in result['identity_gaps'])
    assert len(calls)==2 and all(row[0]=='/api/v2/discount/get_discount' for row in calls)
    assert database.read_bytes()==before
    assert result['approval_status']=='NOT_APPROVED'
    assert all(row['observations'][0]['identity_source']=='LOCAL_CACHED_SHOP_ITEM_MODEL' for row in result['evidence'])


@pytest.mark.parametrize('response',[{'response':{'more':False,'item_list':['bad']}}, {'response':{'more':'false','item_list':[]}}, {'error':'error','message':'fixture'}])
def test_discount_pagination_rejects_malformed_reference_pages(response, monkeypatch):
    module=load('audit_existing_discount_evidence')
    monkeypatch.setattr(module,'shop_get',lambda *a:response)
    with pytest.raises(RuntimeError): module._shopee_discount_items(1,'synthetic',55)
