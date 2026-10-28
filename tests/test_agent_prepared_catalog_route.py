"""Closed execution seam: the new route carries actual pinned catalog authority.

Existing decision/budget tests cover admission. Here only those two provider
seams are closed; native request construction, file pins, sink/outbox/projection
and canonical reader use the real implementations against synthetic files.
"""
from copy import deepcopy
from hashlib import sha256
import json,sqlite3
from types import SimpleNamespace
from test_catalog_cost_projection import catalog
from test_fridge_cover_channel_contracts import _explicit_identity,_digest
from shared_platform import agent_prepared_v4_targets as route

def _final():
    from test_approved_publication_snapshot import _approved_plan,_rebind
    from domains.product_operations import build_approved_publication_snapshot,validate_approved_publication_snapshot
    text=json.dumps(_approved_plan()).replace('0958','2000431').replace('0959','2000432').replace('3838616043','80001')
    text=text.replace('blue-38x45','manual-01').replace('pink-38x45','manual-02')
    plan=json.loads(text);body=plan['payload'];facts=body['product_facts']
    body['sku_lineage']['assignment']['model_skus'].append({'variant_key':'manual-03','model_sku':'2000433'})
    facts['selected_sku_keys'].append('manual-03')
    facts['sku_commercial_facts']['manual-03']=deepcopy(facts['sku_commercial_facts']['manual-01'])
    facts['sku_commercial_facts']['manual-03']['specification']['color']='Starry'
    for row in facts['sku_commercial_facts'].values():
        row['cost']={'amount':'4','currency':'CNY'};row['weight_kg']='0.2';row['package_cm']=['30','30','5']
    facts['image_urls'].append('https://img.example/third.jpg')
    for target in body['pricing']['selected_targets'].values():
        row=deepcopy(target['sku_prices'][0]);row['model_sku']='2000433';target['sku_prices'].append(row)
    master=body['shopee_global_master']
    master['parcel_envelope'].update(weight_kg='0.2',package_cm=[30,30,5])
    master['sku_original_prices_cny'].append({**master['sku_original_prices_cny'][0],'model_sku':'2000433'})
    master['variant_image_positions'].append({'model_sku':'2000433','position':2,'image_url':'https://img.example/third.jpg'})
    snapshot=build_approved_publication_snapshot(_rebind(plan)).payload()
    return validate_approved_publication_snapshot(snapshot).payload()

def test_new_task_route_passes_pinned_authority_to_real_sink_and_durable_projection(catalog,tmp_path,monkeypatch):
    from shared_platform import native_final_mutation_budget as budget
    from shared_platform.catalog_publication_sync import capture_readback
    from shared_platform.publication_canonical_aliases import load_alias_registry
    from modules.sourcing import new_product_workbench as workbench
    snapshot=_final();r1,binding=_explicit_identity()
    r1['canonical_targets']=[r['target_label'] for r in snapshot['publication_targets']]
    r1.pop('snapshot_digest');r1['snapshot_digest']=_digest(r1)
    binding['snapshot']['snapshot_digest']=r1['snapshot_digest']
    directory=tmp_path/'reports/product-preparation/80001';directory.mkdir(parents=True)
    raw=json.dumps(r1).encode();(directory/'round1-approved-snapshot.json').write_bytes(raw)
    binding['snapshot']['sha256']=sha256(raw).hexdigest()
    raw=json.dumps(binding).encode();source=directory/'SOURCE_AND_IDENTITY_BINDING.json';source.write_bytes(raw)
    pin=sha256(raw).hexdigest()
    state={'new_b_identity':{'canonical_to_B_provider_sku':binding['canonical_to_B_provider_sku']},'review':{'seller_sku':'2000431'}}
    state_dir=tmp_path/'data/new_product_workbench';state_dir.mkdir(parents=True)
    (state_dir/'80001.json').write_text(json.dumps(state))
    monkeypatch.setattr(workbench,'load_state',lambda offer,**kw:deepcopy(state))
    bound=SimpleNamespace(catalog_path=catalog,profile=SimpleNamespace(root=tmp_path,data_root=tmp_path/'operations'))
    record={'offer_id':'80001','facts':{'source_identity_binding_sha256':pin,'preparation_source':{'round1_snapshot':r1}}}
    monkeypatch.setattr(route,'_scope',lambda *a:(bound,{},record))
    plan={'plan_id':snapshot['plan_id'],'payload_digest':'a'*64}
    candidate={'write_budget':{'SHOPEE':{}}}
    monkeypatch.setattr(route,'_approved_inputs',lambda *a:(plan,snapshot,candidate))
    monkeypatch.setattr(budget,'build_native_budget_ledger',lambda *a,**kw:SimpleNamespace(snapshot=lambda:{'reservations':[]}))
    active=SimpleNamespace(store=SimpleNamespace(get_run=lambda _: {'run_id':'release-run:'+'a'*24}),
        runtime=SimpleNamespace(supervisor=SimpleNamespace(root=tmp_path/'native-owner')),decision_id='closed-decision')
    calls=[]
    def dependencies(*args):
        def execute(request):
            calls.append(request)
            assert request.catalog_sink.canonical_authority is not None
            observations=[]
            for n,sku in enumerate(snapshot['skus'],1):
                observations.append({'authority':'OFFICIAL','verified':True,'target_label':'shopee:PH',
                    'model_sku':sku['model_sku'],'variant_key':sku['variant_key'],
                    'identity':{'platform':'shopee','shop_key':'101','product_id':'301','variant_id':str(500+n),'seller_sku':sku['model_sku']},
                    'listing':{'name':'Closed fixture','variant_name':str(n),'region':'PH','currency':'PHP','price':'199','status':'NORMAL'}})
            capture_readback(request,observations)
            assert not request.catalog_sink.failures
            return {'targets':[{'target_label':'shopee:PH','status':'PUBLISHED','evidence':{}}],
                'dispatch_attempted':False,'readback_completed':True,'external_write_count':0}
        return execute
    monkeypatch.setattr(route,'_dependencies',dependencies)
    request=SimpleNamespace(channel='shopee',target_label='shopee:PH')
    result=route._execute(active,request)
    assert result.readback_verified and len(calls)==1
    registry=load_alias_registry(catalog)
    for n,sku in enumerate(snapshot['skus'],1):
        assert registry.resolve({'platform':'shopee','shop_key':'101','product_id':'301','variant_id':str(500+n),'seller_sku':sku['model_sku']})==f'043{n}'
    # Changed original companion bytes cannot create another typed capability
    # or enter even the closed executor despite the same caller snapshot.
    source.write_bytes(raw+b'\n')
    rejected=route._execute(active,request)
    assert not rejected.readback_verified and len(calls)==1
