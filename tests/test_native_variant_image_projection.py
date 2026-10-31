"""Frozen B image projection and bounded source continuation; no provider access."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,os,runpy,subprocess,socket
import pytest
from shared_platform import publication_r3_image_bridge as bridge,agent_prepared_source_successor as source
from shared_platform.operations_runtime import runtime_manifest
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.release_store import ReleaseStore,ReleaseAuthorizationError
from modules.products import server
from test_round1_workspace_freeze import live
from test_agent_prepared_source_successor_v4 import real_chain
from test_native_common_colormap_correction import test_real_rejection_v6_public_commit_full_readback_and_stages as _completed_v6
from test_agent_prepared_source_successor import git


def test_actual_readonly_projection_routes_manifest_and_closed_negatives():
    from retained_fixture_gate import require_retained_inputs
    require_retained_inputs('ORBIT_VARIANT_RO_DIAGNOSTIC')
    # The explicitly supplied audit script opens only URI mode=ro/query_only;
    # it reconstructs the authentic inert Ozon structure, never an installed authority.
    connect,network=ReleaseStore._connect,socket.create_connection
    try:ns=runpy.run_path(os.environ['ORBIT_VARIANT_RO_DIAGNOSTIC'])
    finally:ReleaseStore._connect=connect;socket.create_connection=network
    payload,docs,plan,run,snapshot=map(ns.get,('payload','docs','p','run','snapshot'))
    original=deepcopy(plan['payload'])
    assert len(snapshot['publication_targets'])==15 and len(snapshot['product']['images'])==8
    assert [r['position'] for r in snapshot['shopee_global_master']['variant_image_positions']]==[0,1,2]
    for label,row in ns['report']['tiktok_original_draft_consumer'].items():
        assert row['product_images']==8
        route=payload['localized_image_routing']['routes'][label]['ordered_images']
        assert [s['images'] for s in row['variant_images']]==[[route[i]] for i in range(3)]
    cards=ns['report']['manifest_ozon_cards'];expected=ns['report']['ozon_same_pure_resolver']
    ru=payload['localized_image_routing']['routes']['ozon:RU']['ordered_images']
    for i,(card,value) in enumerate(zip(cards,expected)):
        assert card['hero_image_url']==ru[i]
        assert [r['url'] for r in card['images']]==value['images']==[ru[i]]+[u for u in ru if u!=ru[i]]
        assert card['image_count']==8 and set(value['images'])==set(ru)
    assert payload['images']==original['images']
    assert plan['payload']==original  # No mutation of the accepted COMMON payload.
    assert payload['r3_marketplace_binding']['variant_image_projection']['r2_identity']==bridge.validate_r2_identity(docs)
    for fault in ('duplicate','wrong_role','wrong_model'):
        wrong=deepcopy(payload);relation=wrong['native_common_colormap_correction']['relation']['variant_bindings']
        if fault=='duplicate':relation[1]['image_url']=relation[0]['image_url']
        elif fault=='wrong_role':relation[0]['role']='refrigerator_cover_starry'
        else:relation[0]['model_sku']='2000000'
        with pytest.raises((ValueError,ReleaseAuthorizationError)):bridge._project_frozen_variant_images(wrong,docs,plan,run)
    structure=snapshot['product']['ozon_card_structure']
    for fault in ('wrong_hero','duplicate_route'):
        wrong=deepcopy(snapshot)
        if fault=='wrong_hero':wrong['skus'][1]['variant_images']=wrong['skus'][0]['variant_images']
        else:wrong['product']['image_routing']['routes']['ozon:RU']['ordered_images'][1]=ru[0]
        with pytest.raises(ValueError):bridge.independent_card_gallery(wrong,wrong['skus'][1],structure)
    historical=deepcopy(snapshot)
    for sku in historical['skus']:sku['variant_images']=list(historical['product']['images'])
    assert bridge.independent_card_gallery(historical,historical['skus'][0],structure) is None
    assert bridge.independent_card_gallery(snapshot,snapshot['skus'][0],{'round1_snapshot_digest':'other'}) is None


def test_true_completed_v6_gallery_history_second_revision_and_original_consumer(real_chain,monkeypatch,tmp_path):
    v=real_chain;_completed_v6(v,monkeypatch,tmp_path)
    from test_b4b_release_compiler import INCIDENTS
    incident=server.R3_STARTUP_CONFIG.root/server.R3_STARTUP_CONFIG.incidents
    incident.parent.mkdir(parents=True,exist_ok=True);incident.write_text(json.dumps(INCIDENTS))
    service=server._AGENT_PREPARED_COMMON_SERVICE
    root=Path(__file__).resolve().parents[1];base='d39cc2cd2bfd039ef85a8fdf47d8de2cccf6983b'
    def adopt_head():
        v.profile=replace(v.profile,version=git(v.profile.root,'rev-parse','HEAD'),manifest_digest=runtime_manifest(v.profile.root))
        v.engine=WorkbenchEngine(v.profile.data_root/'tasks.db',source._version(v.profile))
    def continue_source(second=False):
        return source.continue_gallery_source(v.engine,v.profile,v.store,task_id=v.task_id,
            expected_adoption_digest=v.adoption_digest,actor_reader=object(),variant_image_projection=second)
    for name in (source.GALLERY_FILE,'shared_platform/agent_prepared_source_successor.py','scripts/continue_agent_prepared_source.py'):
        path=v.profile.root/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(subprocess.check_output(['git','-C',str(root),'show',base+':'+name]))
        git(v.profile.root,'add','-f',name)
    git(v.profile.root,'commit','-m','original frozen gallery compatibility');adopt_head()
    first=continue_source()
    before=v.engine.get(v.task_id);release_before=v.store.path.read_bytes()
    with v.engine.transaction() as db:
        old_event=db.execute('SELECT detail_json FROM workbench_events WHERE event_type=?',(source.GALLERY_EVENT,)).fetchone()[0]
        old_tables={table:[tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in (
            'agent_prepared_publication_adoptions',*[f'agent_prepared_source_successors{suffix}' for suffix in ('','_v2','_v3','_v4','_v5','_v6')])}
    for name in source.VARIANT_FILES:
        path=v.profile.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes((root/name).read_bytes())
        git(v.profile.root,'add','-f',name)
    git(v.profile.root,'commit','-m','exact frozen variant image projection');adopt_head()
    with pytest.raises(ValueError):continue_source()  # Old purpose cannot authorize new files.
    record=continue_source(True)
    assert record['revision']==2 and record['new_edit_attempts']==record['external_write_count']==0
    assert record['preserved']==first['preserved'] and record['source']['old_version']==first['source']['new_version']
    assert continue_source(True)==record
    assert v.store.path.read_bytes()==release_before
    current=v.engine.get(v.task_id)
    assert all(current[k]==before[k] for k in ('scope','checkpoint','pending_observation','execution_state','current_step'))
    with v.engine.transaction() as db:
        events=db.execute('SELECT detail_json FROM workbench_events WHERE event_type=? ORDER BY id',(source.GALLERY_EVENT,)).fetchall()
        assert len(events)==2 and events[0][0]==old_event
        assert old_tables=={table:[tuple(r) for r in db.execute('SELECT * FROM '+table)] for table in old_tables}
    v.operations.engine=v.engine;v.operations.profile=v.profile;service.engine=v.engine;service.profile=v.profile
    assert service._current()[0]['version']==source._version(v.profile)
    # A new offer-only marketplace budget policy must not reinterpret the completed
    # COMMON credential or source history. Reinstall the real startup policy reader.
    from shared_platform import publication_runtime_config as runtime,publication_common_standing_policy as standing
    original_config=server.R3_STARTUP_CONFIG
    original_common=server._service_common_standing_policy_reader()._config
    raw=(original_common.root/original_common.policy).read_bytes();policy=json.loads(raw)
    budgets=deepcopy(policy['write_budgets'])
    for name,maximum in (('TIKTOK',10),('SHOPEE',10),('OZON',4)):budgets[name]['shared_maximum']=maximum
    policy.setdefault('write_budget_overrides',{})[v.payload['product_id']]=budgets
    relative='reports/fixture-marketplace-budget.json'
    (original_config.root/relative).write_text(json.dumps(policy),encoding='utf-8')
    env=dict(ORBIT_R3_CONFIG_ROOT=str(original_config.root),ORBIT_R3_POLICY_PATH=relative,
        ORBIT_R3_INCIDENT_REGISTRY_PATH=original_config.incidents)
    from hashlib import sha256
    env.update(ORBIT_COMMON_CREDENTIALS_ROOT=str(original_common.common_credentials_root or original_common.root),
        ORBIT_COMMON_CREDENTIALS_SHA256=sha256(runtime.common_signing_bytes(original_common)).hexdigest())
    derived=runtime.capture_startup_config(root=original_config.root,environ=env)
    old_digest=standing.inspect_service_policy(server._service_common_standing_policy_reader()).raw_digest
    monkeypatch.setattr(server,'R3_STARTUP_CONFIG',derived);server._install_service_common_standing_policy(derived)
    server._install_service_common_signing_context(derived)
    policy_facts=standing.inspect_service_policy(server._service_common_standing_policy_reader())
    assert policy_facts.status=='KNOWN_LOCAL_USER_INTENT' and policy_facts.maximum_confirmed_writes==1,policy_facts.diagnostic()
    assert policy_facts.raw_digest!=old_digest
    assert runtime.diagnose(derived)[0]['status']=='CONFIGURED'
    assert service._current()[0]['version']==source._version(v.profile)
    from shared_platform.native_common_retained_completion import read_retained_completion
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    with v.store._connect_readonly() as db:
        db.execute('BEGIN')
        completion=read_retained_completion(NativeCommonSourceReader(v.store),db,record['preserved']['common_plan_id'],record['preserved']['common_run_id'])
    assert completion.state=='CONFIRMED_WRITE' and v.store.path.read_bytes()==release_before
    # Existing finalize must consume the new source through genuine native readers.
    # The old synthetic wallpaper fixture retains its own unrelated gallery gate.
    from modules.miaoshou import client
    monkeypatch.setattr(client,'post_open',lambda *a,**k:pytest.fail('Completed COMMON cannot dispatch'))
    from shared_platform.ozon_runtime_credentials import load_pinned_ozon_credentials
    from shared_platform.ozon_stock_warehouse_decision import resolve_warehouse_decision
    from hashlib import sha256
    credentials=tmp_path/'owned-ozon.json';credentials.write_text(json.dumps(dict(client_id='12345',api_key='closed-owned-key')))
    digest=sha256(credentials.read_bytes()).hexdigest()
    monkeypatch.setenv('ORBIT_OZON_CREDENTIALS_PATH',str(credentials));monkeypatch.setenv('ORBIT_OZON_CREDENTIALS_SHA256',digest)
    monkeypatch.setenv('ORBIT_OZON_EXPECTED_ACCOUNT_ID','12345')
    docs=bridge.load_r2_documents(v.payload['product_id'])
    receipt=resolve_warehouse_decision(offer_id=v.payload['product_id'],round1=docs['round1_snapshot'],
        pinned=load_pinned_ozon_credentials(credentials,expected_sha256=digest,expected_account_id='12345'),
        post_bound=lambda *a,**k:dict(warehouses=[dict(warehouse_id=71,status='active',is_kgt=False)]))
    (v.profile.root/'reports/product-preparation'/v.payload['product_id']/'ozon-warehouse-readback.json').write_text(json.dumps(receipt))
    code,result=server._preview_r3_marketplace_stage(dict(offer_id=v.payload['product_id']))
    assert code==409 and result['error']=='GALLERY_UNVERIFIED_SIZE_OR_INSTALLATION',result
    # A third source change cannot mint a third compatibility event.
    path=v.profile.root/source.VARIANT_FILE;path.write_bytes(path.read_bytes()+b'\n# no third revision\n')
    git(v.profile.root,'add','-f',source.VARIANT_FILE);git(v.profile.root,'commit','-m','unsupported third revision');adopt_head()
    with pytest.raises(ValueError):continue_source(True)
