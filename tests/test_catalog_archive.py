import sqlite3
import pytest
from test_catalog_cost_projection import catalog
from test_catalog_sku_directory import seed_global
from shared_platform.catalog_cost_projection import digest
from shared_platform.catalog_sku_costs import members,grouped,state,save
from modules.catalog.sku_directory import list_skus

def packet():
    from shared_platform.catalog_archive import identity_key
    return [{'identity':dict(platform='tiktok',shop_key='shop1',product_id='p99',variant_id='v99',seller_sku='660975'),
       'entity_key':'internal:'+digest('0975'),'legacy_entity_key':None,'internal_sku':'0975','binding':'INTERNAL_SKU',
       'name':'Archived product','spec':'20 cm','image_url':'https://example.com/archive.png','region':'MY',
       'product_status':'SELLER_DEACTIVATED','variant_status':'DEACTIVATE',
       'observed_at':'2026-09-07T15:00:00Z','evidence_kind':'OFFICIAL_PRODUCT_READBACK'}]

def install(path,rows):
    from shared_platform.catalog_archive import import_members
    return import_members(path,rows,expected_digest=digest(rows),evidence_ref='fixture://official',captured_at='2026-09-07T15:00:00Z')

def test_archive_survives_channel_cache_removal_and_keeps_manual_cost(catalog):
    rows=packet();install(catalog,rows)
    data=list_skus();assert data['total']==1
    row=data['items'][0];save(catalog,row['entity_key'],'12',row['cost']['revision'])
    with sqlite3.connect(catalog) as c:c.execute('DELETE FROM products');c.execute('DELETE FROM shopee_products')
    row=list_skus()['items'][0];assert row['sku']=='0975' and row['cost']['amount']=='12'
    assert row['channel_evidence'][0]['product_status']=='SELLER_DEACTIVATED'
    assert install(catalog,rows)['inserted']==0

def test_inactive_channel_rows_remain_internal_products(catalog):
    seed_global(catalog)
    with sqlite3.connect(catalog) as c:c.execute("UPDATE products SET status='SELLER_DEACTIVATED'")
    assert list_skus()['total']==1 and list_skus()['items'][0]['member_count']==2

def test_archive_rejects_sku_drift_and_does_not_touch_channel_tables(catalog):
    rows=packet();rows[0]['internal_sku']='0976'
    with pytest.raises(ValueError,match='sku_mismatch'):install(catalog,rows)
    with sqlite3.connect(catalog) as c:
        assert c.execute('SELECT count(*) FROM products').fetchone()[0]==0
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_archive_members'").fetchone()

def test_archive_image_is_registered(catalog,tmp_path):
    from shared_platform.catalog_images import ImageCache
    install(catalog,packet())
    with sqlite3.connect(catalog) as c:
        cache=ImageCache(c,tmp_path/'images')
        assert digest('https://example.com/archive.png') in cache.urls

def test_archive_keeps_inherited_cost_and_deduplicates_live_identity(catalog):
    from shared_platform.catalog_sku_costs import source_cost
    seed_global(catalog)
    with sqlite3.connect(catalog) as c:
        c.row_factory=sqlite3.Row;rows=members(c)
        for row in rows:
            row.update(observed_at='2026-09-07T15:00:00Z',evidence_kind='PRESERVED_LOCAL_CATALOG',archived_cost=source_cost(c,row))
    install(catalog,rows)
    assert list_skus()['coverage']['source_identities']==2
    with sqlite3.connect(catalog) as c:c.execute('DELETE FROM products')
    assert list_skus()['items'][0]['cost']['choices']==['5','5.5']

def test_archive_preserves_missing_sku_by_exact_identity(catalog):
    from shared_platform.catalog_archive import identity_key
    rows=packet();row=rows[0];row['identity']['seller_sku']='';row['internal_sku']=''
    row['binding']='MISSING_SKU';row['entity_key']='unbound:'+identity_key(row['identity'])
    install(catalog,rows)
    result=list_skus();assert result['coverage']['missing_sku_records']==1

def test_conflicting_import_rolls_back_new_rows(catalog):
    from copy import deepcopy
    original=packet();install(catalog,original)
    first=deepcopy(original[0]);first['identity']['variant_id']='v-new'
    conflict=deepcopy(original[0]);conflict['name']='Conflicting source'
    with pytest.raises(ValueError,match='identity_conflict'):install(catalog,[first,conflict])
    with sqlite3.connect(catalog) as c:
        assert c.execute('SELECT count(*) FROM catalog_archive_members').fetchone()[0]==1

def test_explicit_kg_archive_weight_and_logistics_priority(catalog):
    rows=packet();rows[0]['weight_source']={'field':'weight_kg','unit':'kg','value':0.08};install(catalog,rows)
    assert list_skus()['items'][0]['weight_g']==80
    with sqlite3.connect(catalog) as c:
        c.execute('CREATE TABLE IF NOT EXISTS sku_logistics_weights(seller_sku TEXT,weight_g REAL)')
        c.execute('INSERT INTO sku_logistics_weights(seller_sku,weight_g) VALUES(?,?)',('660975',95))
    row=list_skus()['items'][0];assert row['weight_g']==95 and row['weight_source']=='LOGISTICS_RECORD'

@pytest.mark.parametrize('source',[{'value':0.08},{'field':'weight_kg','unit':'g','value':0.08},
    {'field':'weight_kg','unit':'kg','value':True},{'field':'weight_kg','unit':'kg','value':'Infinity'},
    {'field':'weight_kg','unit':'kg','value':-1}])
def test_archive_weight_never_guesses_unit_or_invalid_value(catalog,source):
    rows=packet();rows[0]['weight_source']=source;install(catalog,rows)
    assert list_skus()['items'][0]['weight_g'] is None

def test_conflicting_archive_weights_remain_unresolved(catalog):
    from copy import deepcopy
    rows=packet();rows[0]['weight_source']={'field':'weight_kg','unit':'kg','value':0.08}
    other=deepcopy(rows[0]);other['identity']['variant_id']='other';other['weight_source']['value']=0.09
    install(catalog,rows+[other]);row=list_skus()['items'][0]
    assert row['weight_g'] is None and row['weight_status']=='CONFLICT'
