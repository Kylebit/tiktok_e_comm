"""Official three-variant readback to exact canonical cost/order consumers."""
from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from test_catalog_cost_projection import catalog
from test_fridge_cover_channel_contracts import _explicit_identity, _digest


def _authority(tmp_path, mutation=None):
    from shared_platform.publication_canonical_aliases import create_frozen_alias_authority
    r1, binding = _explicit_identity()
    r1['canonical_targets']=['shopee:PH','tiktok:LH_PH']
    r1.pop('snapshot_digest');r1['snapshot_digest']=_digest(r1)
    binding['snapshot']['snapshot_digest']=r1['snapshot_digest']
    r1raw = json.dumps(r1).encode()
    binding['snapshot']['sha256'] = hashlib.sha256(r1raw).hexdigest()
    (tmp_path/'round1-approved-snapshot.json').write_bytes(r1raw)
    raw = json.dumps(binding).encode()
    (tmp_path/'SOURCE_AND_IDENTITY_BINDING.json').write_bytes(raw)
    state = {'new_b_identity': {'canonical_to_B_provider_sku': binding['canonical_to_B_provider_sku']},
             'review': {'seller_sku': '2000431'}}
    final = {'schema_version': 'approved-publication-snapshot/v4', 'offer_id': '80001',
             'publication_targets': [{'target_label': 'shopee:PH'}, {'target_label': 'tiktok:LH_PH'}],
             'skus': [{'variant_key': f'manual-0{i}', 'model_sku': f'200043{i}'} for i in range(1,4)]}
    if mutation=='variant':final['skus'][0]['variant_key']='replacement-source'
    if mutation=='target':final['publication_targets'][0]['target_label']='shopee:MY'
    final['snapshot_digest'] = _digest(final)
    authority = create_frozen_alias_authority(offer_id='80001', round1_snapshot=r1, state=state,
        report_directory=tmp_path, expected_binding_sha256=hashlib.sha256(raw).hexdigest(),
        publication_snapshot=final)
    return authority, final


@pytest.mark.parametrize('mutation',['variant','target'])
def test_factory_rejects_lineage_or_target_drift_with_recomputed_digest(tmp_path,mutation):
    with pytest.raises(ValueError,match='CANONICAL_FINAL_'):
        _authority(tmp_path,mutation)


def _packet(tmp_path, platform='shopee'):
    authority, final = _authority(tmp_path)
    rows=[]
    for n in range(1,4):
        model=f'200043{n}'
        rows.append({'authority':'OFFICIAL', 'verified':True, 'target_label':'shopee:PH' if platform=='shopee' else 'tiktok:LH_PH',
          'model_sku': model, 'variant_key':f'manual-0{n}', 'approved_cost':'4',
          'identity': {'platform':platform,'shop_key':'101','product_id':'301','variant_id':str(500+n),'seller_sku':model},
          'listing': {'name':'Fridge Cover','variant_name':f'Pattern {n}','region':'PH','currency':'PHP','price':'199','status':'NORMAL'}})
        if platform=='tiktok':
            rows[-1]['shop_binding']={'schema_version':'official-tiktok-shop-binding/v1',
                'control_shop_id':'8','official_shop_id':'911','shop_cipher':'101','region':'PH','name':'Fixture Shop'}
    rows=authority.bind_rows(rows, snapshot_digest=final['snapshot_digest'], target_labels=(rows[0]['target_label'],))
    return {'schema':'catalog-publication-observation/v1','offer_id':'80001','receipt_id':'canonical-first',
            'snapshot_digest':final['snapshot_digest'],'rows':rows}, authority


def test_official_three_aliases_share_canonical_cost_and_keep_raw_identity(catalog,tmp_path):
    from shared_platform.catalog_cost_projection import project
    from shared_platform.catalog_sku_costs import grouped, read_current
    from shared_platform.publication_canonical_aliases import read_alias_registry
    packet, authority = _packet(tmp_path)
    assert project(catalog, packet, canonical_authority=authority)['status']=='COMPLETE'
    assert project(catalog, packet, canonical_authority=authority)['replayed'] is True
    with sqlite3.connect(catalog) as conn:
        conn.row_factory=sqlite3.Row
        registry=read_alias_registry(conn)
        assert len(grouped(conn))==3
        for n,row in enumerate(packet['rows'],1):
            assert registry.resolve(row['identity'])==f'043{n}'
            assert read_current(conn,row['identity'])['amount']=='4'
        wrong={**packet['rows'][0]['identity'],'shop_key':'102'}
        assert registry.resolve(wrong) is None
        assert conn.execute('SELECT seller_sku FROM shopee_products ORDER BY model_id').fetchall()[0][0]=='2000431'


def test_caller_json_and_partial_readback_cannot_register_alias(catalog,tmp_path):
    from shared_platform.catalog_cost_projection import project
    packet, authority = _packet(tmp_path)
    with pytest.raises(ValueError,match='CANONICAL.*AUTHORITY'):
        project(catalog,packet)
    incomplete=deepcopy(packet);incomplete['rows'].pop()
    with pytest.raises(ValueError,match='COMPLETE'):
        project(catalog,incomplete,canonical_authority=authority)


def test_registered_alias_resolves_order_demand_only_for_full_official_identity(catalog,tmp_path):
    from shared_platform.catalog_cost_projection import project
    from shared_platform.publication_canonical_aliases import load_alias_registry
    from domains.supply_chain_operations.order_demand import aggregate_tiktok_orders
    packet, authority = _packet(tmp_path,'tiktok')
    project(catalog,packet,canonical_authority=authority)
    registry=load_alias_registry(catalog)
    order={'id':'order','status':'IN_TRANSIT','paid_time':1785542400,
           'line_items':[{'seller_sku':'2000431','product_id':'301','sku_id':'501'}]}
    rows, evidence=aggregate_tiktok_orders([order],'PH',alias_registry=registry,shop_key='101')
    assert rows['0431']['units']==1 and rows['0431']['source_aliases']=={'2000431'}
    assert not aggregate_tiktok_orders([order],'PH',alias_registry=registry,shop_key='102')[0]
    order['line_items'][0].pop('product_id')
    assert not aggregate_tiktok_orders([order],'PH',alias_registry=registry,shop_key='101')[0]


def test_alias_registration_and_listing_roll_back_together(catalog,tmp_path):
    from shared_platform.catalog_cost_projection import project
    packet, authority=_packet(tmp_path)
    def fail(stage):
        if stage=='before_receipt':raise OSError('injected local transaction failure')
    with pytest.raises(OSError):project(catalog,packet,canonical_authority=authority,fault=fail)
    with sqlite3.connect(catalog) as conn:
        for table in ('catalog_canonical_bindings','catalog_official_observations','shopee_products'):
            assert conn.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
    assert project(catalog,packet,canonical_authority=authority)['status']=='COMPLETE'


def test_complete_three_models_cannot_register_as_split_products(tmp_path):
    packet,authority=_packet(tmp_path)
    rows=deepcopy(packet['rows'])
    for row in rows:row.pop('canonical_binding')
    rows[1]['identity']['product_id']='different-product'
    with pytest.raises(ValueError,match='SINGLE_PRODUCT'):
        authority.bind_rows(rows,snapshot_digest=packet['snapshot_digest'],target_labels=('shopee:PH',))


def test_shopee_order_and_escrow_use_the_same_exact_registered_alias(catalog,tmp_path):
    from shared_platform.catalog_cost_projection import project
    from shared_platform.publication_canonical_aliases import load_alias_registry
    from domains.supply_chain_operations.order_demand import aggregate_shopee_orders
    from domains.supply_chain_operations.shopee_demand import aggregate_escrow_details
    packet,authority=_packet(tmp_path)
    project(catalog,packet,canonical_authority=authority)
    registry=load_alias_registry(catalog)
    item={'item_id':301,'model_id':501,'model_sku':'2000431','model_quantity_purchased':2,'quantity_purchased':2}
    rows,_=aggregate_shopee_orders([{'order_sn':'order','order_status':'SHIPPED','create_time':1785542400,'item_list':[item]}],
        'PH',alias_registry=registry,shop_key='101')
    assert rows['0431']['units']==2
    escrow,_=aggregate_escrow_details([{'release_ts':1785542400,'detail':{'order_income':{'items':[item]}}}],
        window_days=31,recent_cutoff_ts=1785456000,alias_registry=registry,shop_key='101')
    assert escrow['0431']['units']==2 and escrow['0431']['sourceAliases']==['2000431']


def test_actual_catalog_and_settlement_keep_raw_and_canonical_separate(catalog,tmp_path):
    from shared_platform.catalog_cost_projection import project
    from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
    from domains.data_operations.profit_settlement.settlement_evidence_adapter import adapt_settlement_evidence
    packet,authority=_packet(tmp_path,'tiktok')
    project(catalog,packet,canonical_authority=authority)
    actual=load_local_catalog(catalog,cost_view='current')
    assert str(actual.costs_by_sku['0431'])=='4'
    evidence={'schema_version':'settlement-evidence/v1','status':'ready','platform':'tiktok','site':'PH','shop_id':'911',
        'snapshot_id':'fixture','checksum':'fixture','net_settlement_total_local':'199',
        'orders':[{'order_id':'order','transaction_type':'Order','currency':'PHP','net_settlement_amount':'199',
          'buyer_total_amount':'199','settled_at':'2026-08-15T00:00:00+08:00',
          'financial_components':[{'code':'merchant_shipping_fee','amount':'0','currency':'PHP'}],
          'items':[{'platform_sku':'501','product_id':'301','seller_sku':'2000431','quantity':'1'}]}]}
    adapted=adapt_settlement_evidence(evidence,actual,period_kind='weekly')
    assert adapted.rows[0]['seller_sku']=='2000431'
    assert adapted.rows[0]['canonical_sku']=='0431'
    assert adapted.rows[0]['catalog_identity']['shop_key']=='101'
    from domains.data_operations.profit_settlement.monthly_missing_cost_scope import canonical_settlement_item_sku
    assert canonical_settlement_item_sku(actual,evidence,evidence['orders'][0],evidence['orders'][0]['items'][0])=='0431'
    evidence['orders'][0]['items'][0].pop('product_id')
    assert canonical_settlement_item_sku(actual,evidence,evidence['orders'][0],evidence['orders'][0]['items'][0])==''
    rejected=adapt_settlement_evidence(evidence,actual,period_kind='weekly',seller_sku_by_platform_sku={'501':'0431'})
    assert rejected.rows[0]['canonical_sku']==''


def test_exact_native_storefront_produces_official_per_sku_rows_and_no_invented_id():
    from shared_platform.native_tiktok_exact_readback import NativeTikTokExactStorefrontReadback,SHOP_PATH,SEARCH,DETAIL
    from modules.miaoshou.tiktok_publisher import EXPECTED_SHOP_ID_BY_TARGET
    from domains.channel_operations.tiktok_v4_execution import _readback_fact
    label='tiktok:LH_PH';internal=str(EXPECTED_SHOP_ID_BY_TARGET[label])
    image='https://example.invalid/fridge.jpg';description=f'<p>Fridge cover</p><img src="{image}">'
    wanted=[{'model_sku':f'200043{n}','variant_key':f'manual-0{n}','price':'199','currency':'PHP',
             'specification':{'Pattern':name}} for n,name in enumerate(('Daisy','Feather','Starry Sky'),1)]
    expected={'expected_title':'Fridge cover','expected_description':description,'expected_images':[image],
       'expected_category_id':'700665','expected_weight_kg':'.2','expected_package_cm':[30,30,5],
       'expected_warehouse_inventory':{'warehouses':[{'warehouse_id':'w','stock':1}]}}
    command={'target_label':label,'target':{'site':'LH_PH'},'control':{'shop_id':internal},'skus':wanted,
       'publisher_snapshot':{'targets':[expected]}}
    detail={'id':'301','title':'Fridge cover','description':description,'status':'ACTIVATE','category_id':'700665',
       'package_weight':{'unit':'KG','value':'.2'},'package_dimensions':{'unit':'CM','length':30,'width':30,'height':5},
       'main_images':[{'urls':[image]}], 'skus':[{'id':str(500+n),'seller_sku':s['model_sku'],
          'price':{'sale_price':'199','currency':'PHP'},'sales_attributes':[{'name':'Pattern','value_name':s['specification']['Pattern']}],
          'inventory':[{'warehouse_id':'w','quantity':1}]} for n,s in enumerate(wanted,1)]}
    def official(method,path,token,query,body):
        if path==SHOP_PATH:return {'code':0,'data':{'shops':[{'id':'911','name':'Fixture Shop','region':'PH','cipher':'cipher-1'}]}}
        if path==SEARCH:return {'code':0,'data':{'products':[{'id':'301','skus':detail['skus']}],'next_page_token':'','total_count':1}}
        assert path==DETAIL.format(product_id='301')
        return {'code':0,'data':deepcopy(detail)}
    reader=NativeTikTokExactStorefrontReadback(official_call=official,token_loader=lambda:('inert-token',lambda:None),
        shop_post=lambda *args:{'result':'success','data':{'list':[{'shopId':internal,'shopName':'Fixture Shop','site':'PH','platform':'TIKTOK'}],'total':1}})
    result=reader.readback(command=command,dispatch={})
    assert result['status']=='VERIFIED' and len(result['official_catalog_rows'])==3
    assert result['official_catalog_rows'][0]['identity']=={'platform':'tiktok','shop_key':'cipher-1','product_id':'301','variant_id':'501','seller_sku':'2000431'}
    assert _readback_fact(result,label,command)['official_catalog_rows']==result['official_catalog_rows']
    detail['skus'][0].pop('id')
    assert reader.readback(command=command,dispatch={})['exact'] is False
