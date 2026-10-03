import json
import sqlite3
import pytest
from shared_platform.operations_profit_scope import resolve_scope


def project(tmp_path):
    (tmp_path/'config').mkdir();(tmp_path/'config/settings.json').write_text(json.dumps({'database':'shops.db','shopee':{'token_file':'shops.json'}}))
    with sqlite3.connect(tmp_path/'shops.db') as db:
        db.execute('CREATE TABLE shops(shop_id TEXT,region TEXT)');db.execute("INSERT INTO shops VALUES('tk1','MY')")
        db.execute('CREATE TABLE shopee_shops(shop_id TEXT,region TEXT)');db.execute("INSERT INTO shopee_shops VALUES('sp1','MY')")
    (tmp_path/'shops.json').write_text(json.dumps({'shops':{'sp1':{'shop_id':'sp1','region':'MY'},'sp2':{'shop_id':'sp2','region':'MY','sync_enabled':False,'access_token':''}}}))
    return {'month':'2026-08','platforms':['tiktok','shopee'],'sites':['MY']}


def test_all_local_stores_include_unlogged_secondary_without_mutation(tmp_path):
    scope=project(tmp_path);before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result=resolve_scope(scope,tmp_path)
    assert result['shops']==['sp1','sp2','tk1'];assert 'shops' not in scope
    assert all(p.read_bytes()==raw for p,raw in before.items())


def test_requested_shop_outside_platform_rejected(tmp_path):
    scope=project(tmp_path);scope.update(platforms=['tiktok'],shops=['sp1'])
    with pytest.raises(ValueError):resolve_scope(scope,tmp_path)


def test_missing_requested_site_never_silently_skipped(tmp_path):
    scope=project(tmp_path);scope['sites'].append('TH')
    with pytest.raises(ValueError):resolve_scope(scope,tmp_path)


def test_missing_settings_never_use_example_or_another_project(tmp_path):
    (tmp_path/'config').mkdir();(tmp_path/'config/settings.example.json').write_text('{}')
    with pytest.raises(ValueError):resolve_scope({'month':'2026-08'},tmp_path)


def test_conflicting_region_is_technical(tmp_path):
    scope=project(tmp_path);(tmp_path/'shops.json').write_text(json.dumps({'shops':{'sp1':{'region':'TH'}}}))
    with pytest.raises(ValueError):resolve_scope(scope,tmp_path)
