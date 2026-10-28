"""Real immutable COMMON builder over a synthetic new provider-bound ledger."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from shared_platform import manual_common_creation as manual
from shared_platform import agent_prepared_common_service as service
from shared_platform import agent_prepared_v4_targets as targets
from modules.products.release_adapters import _build_immutable_common_edit
from test_manual_common_creation import intent
from test_round1_workspace_freeze import live

def test_explicit_bootstrap_preserves_native_preparation_and_uses_real_same_owner(live,tmp_path,monkeypatch):
    from test_native_common_write_census import _origin
    from shared_platform.workbench_engine import WorkbenchEngine
    from scripts.adopt_agent_prepared_product import initialize_local_schemas
    from shared_platform import agent_prepared_publication_adoption as adoption
    from shared_platform.native_windows_actor import NativeActorServiceConfig,bootstrap_service,NativeWindowsActorProfileReader
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    # Existing genuinely prepared synthetic source is maintained in place;
    # no workstation DB or credential is copied into this test.
    store,_,_=_origin(live,monkeypatch)
    with store._connect_readonly() as db:
        before={table:[tuple(x) for x in db.execute('SELECT * FROM '+table)]
                for table in ('round1_category_observations','round1_workspace_preparations','release_plans','release_approvals')}
    assert before['round1_workspace_preparations'] and before['release_plans']
    profile=SimpleNamespace(data_root=tmp_path/'operations',root=tmp_path,version='owned-bootstrap')
    engine=WorkbenchEngine(profile.data_root/'tasks.db',{'code_version':profile.version})
    initialize_local_schemas(engine,profile,store)
    sid,_=adoption._owner_key(profile)
    actor=bootstrap_service(NativeActorServiceConfig(profile.data_root/'native-final-owner','Kyle'))
    identity=read_native_actor_identity(NativeWindowsActorProfileReader(actor,actor.grant_same_user()))
    assert identity['owner_sid']==sid and identity['trust_mode']=='SAME_WINDOWS_USER'
    with store._connect_readonly() as db:
        assert before=={table:[tuple(x) for x in db.execute('SELECT * FROM '+table)] for table in before}
        assert list(db.execute('PRAGMA foreign_key_check'))==[]
        assert db.execute('SELECT count(*) FROM release_approvals').fetchone()[0]==0
    assert engine.dashboard()['tasks']==[]

def test_one_documented_create_then_baseline_builds_exact_three_frozen_variants(tmp_path,monkeypatch):
    path=tmp_path/'owned.db';manual.initialize_schema(path);ledger=manual.ManualCommonCreateLedger(path)
    row=ledger.reserve(intent());posts=[]
    blank={'commonCollectBoxDetailId':9001,'itemNum':'2000431','skuMap':{},'providerOwned':'preserved'}
    def create(body):
        posts.append(body)
        assert set(body)=={'title','itemNum','price'} and body['price']==4.0
        return {'result':'success','data':{'commonCollectBoxDetailId':9001}}
    row=ledger.create(row['intent_id'],post_create=create,read_detail=lambda _:deepcopy(blank))
    from shared_platform import release_store
    monkeypatch.setattr(release_store,'default_release_store',lambda:SimpleNamespace(path=path))
    keys=['manual-01','manual-02','manual-03'];models=['2000431','2000432','2000433']
    payload=dict(product_id=row['offer_id'],seller_sku='2000431',targets=['miaoshou:COMMON'],
        r3_stage_binding=dict(schema_version='r3-common-stage/v1',execution_scope=['miaoshou:COMMON'],
          manual_common_binding={k:row[k] for k in ('intent_id','intent_digest','detail_id')}),
        images=[{'image_url':'https://owned.invalid/one.jpg'}],video_urls=[],
        listing_copy={'shopee_description_en':'Owned source facts'},
        sku_lineage={'assignment':{'model_skus':[{'variant_key':k,'model_sku':m} for k,m in zip(keys,models)]}},
        product_facts=dict(title='Owned fridge cover',source_offer_id=row['offer_id'],selected_sku_keys=keys,
          weight_kg='0.2',package_cm=['30','30','5'],
          selected_skus=[{'key':k,'model_sku':m,'label':label} for k,m,label in zip(keys,models,['Daisy','Feather','Starry'])],
          sku_commercial_facts={k:{'cost_cny':'4','weight_kg':'0.2','package_cm':['30','30','5']} for k in keys}))
    updated=_build_immutable_common_edit(payload,blank)
    assert updated['providerOwned']=='preserved' and len(posts)==1
    assert {k:updated['skuMap'][k]['itemNum'] for k in keys}==dict(zip(keys,models))
    assert {x['specLabel'] for x in updated['skuMap'].values()}=={'Daisy','Feather','Starry'}
    assert all((x['weight'],x['packageLength'],x['packageWidth'],x['packageHeight'])==(.2,30,30,5) for x in updated['skuMap'].values())
    changed=deepcopy(blank);changed['providerOwned']='foreign edit'
    with pytest.raises(manual.ManualCommonBlocked,match='BLANK_DETAIL_CHANGED'):
        _build_immutable_common_edit(payload,changed)

def test_preparation_admission_rejects_caller_receipts_or_actor_fields(monkeypatch):
    bound=object.__new__(service.AgentPreparedCommonService)
    monkeypatch.setattr(bound,'owns',lambda offer:offer=='8412287877065586543')
    monkeypatch.setattr(bound,'_actor',lambda:None)
    http=SimpleNamespace(agent_prepared_common=bound)
    request={'offer_id':'8412287877065586543','release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON']}
    assert service.admit_preparation_request(http,'/api/product-workspace/r3-common/preview',request)==request
    for field in ('approved_by','user_approved','actor','source_identity_binding_sha256'):
        with pytest.raises(manual.ManualCommonBlocked,match='REQUEST_FIELDS'):
            service.admit_preparation_request(http,'/api/product-workspace/r3-common/preview',{**request,field:True})

def test_new_route_cannot_borrow_caller_scope_or_the_original_403_route():
    with pytest.raises(ValueError,match='PROTECTED_V4_SCOPE'):
        targets.registry_for_adopted(SimpleNamespace(),{'product_id':'8412287877065586543'}, {})
