"""Exact Ozon-only exception. All identities and provider replies are fixtures."""
from copy import deepcopy
import hashlib
import json
import pytest
from test_agent_prepared_adoption import prepared


@pytest.fixture
def structure_sources(prepared):
    from shared_platform import agent_prepared_publication_adoption as adoption
    from shared_platform import publication_ozon_card_structure as structure
    from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
    from shared_platform.release_store import RELEASE_TARGET_LABELS
    from test_fridge_cover_channel_contracts import _explicit_identity, _digest
    engine, profile, facts = prepared
    r1, binding = _explicit_identity()
    targets = list(RELEASE_TARGET_LABELS[1:])
    r1.pop('snapshot_digest'); r1['canonical_targets'] = sorted(targets); r1['snapshot_digest'] = _digest(r1)
    directory = profile.root/'reports/product-preparation/80001';directory.mkdir(parents=True)
    r1path, bindingpath = directory/'round1-approved-snapshot.json',directory/'SOURCE_AND_IDENTITY_BINDING.json'
    r1path.write_bytes(structure._bytes(r1))
    binding['snapshot'] = {'snapshot_digest':r1['snapshot_digest'],'sha256':adoption._sha(r1path)}
    bindingpath.write_bytes(structure._bytes(binding))
    facts.update(snapshot_digest=r1['snapshot_digest'],source_identity_binding_sha256=adoption._sha(bindingpath))
    facts['source_files'].update({str(p.relative_to(profile.root)):adoption._sha(p) for p in (r1path,bindingpath)})
    receipt = {'schema_version':'orbit-user-decisions/v1','actor':'USER','offer_id':'80001',
        'round1_snapshot_digest':r1['snapshot_digest'],
        'round1_snapshot_file':{'sha256':adoption._sha(r1path)},
        'source_identity_binding':{'sha256':adoption._sha(bindingpath)},
        'approved_for_publication':False,'sole_final_review_still_required':True,
        'ozon_decision':{'decision':'OZON_THREE_INDEPENDENT_B_CARDS','target':'ozon:RU',
            'approved_for_publication':False,'preserve_other_14_targets':True,'preserve_original_A':True,
            'product_content_cost_parcel_prices_and_gallery_unchanged':True,
            'other_target_structure':'ONE_PRODUCT_THREE_VARIANTS','links_per_ozon_shop':3,
            'variants_per_ozon_card':1,'quantity_per_variant':1,'target_count_unchanged':15,
            'category_id':53968796,'type_id':970663046,'canonical_skus':['0431','0432','0433'],
            'model_skus':['2000431','2000432','2000433']}}
    receiptpath=profile.root/'decision.json';receiptpath.write_bytes(structure._bytes(receipt))
    successor=structure.build_successor(round1_snapshot=r1,identity_binding=binding,
        source_binding_sha256=adoption._sha(bindingpath),decision_receipt=receipt,
        decision_receipt_path=receiptpath,decision_receipt_sha256=adoption._sha(receiptpath))
    sidecar=profile.root/'successor.json';sidecar.write_bytes(structure._bytes(successor))
    result=adoption.adopt(engine,profile,offer_id='80001',targets=targets,source_key='exact-ozon',actor_reader=object())
    service=object.__new__(AgentPreparedCommonService);service.profile=profile
    def current():
        task=engine.get(result['task_id'])
        record=adoption.verify(task,profile,offer='80001',targets=sorted(targets),
            prepared_reference=facts['prepared_reference'],snapshot_digest=facts['snapshot_digest'])
        return task,record
    service._current=current
    reader=structure.OzonCardStructureReader(service,path=sidecar,expected_sha256=adoption._sha(sidecar))
    return reader,receiptpath,sidecar,r1path,targets


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def denied(*args, **kwargs):
        raise AssertionError('offline Ozon structure test attempted network I/O')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


def test_independent_card_import_uses_distinct_models_and_one_piece():
    from shared_platform.publication_ozon_card_structure import independent_card
    from shared_platform.product_publication_live_dependencies import build_ozon_import_item_from_frozen_variant
    from test_fridge_cover_channel_contracts import _fridge_profile_replay
    from test_ozon_toilet_paper_holder_profile import variant
    resolver, _ = _fridge_profile_replay()
    profile = resolver({'schema_version':'approved-publication-snapshot/v4',
        'product':{'main_category':{'name':'冰箱防尘盖布带侧袋'}},
        'categories_by_target':{'ozon:RU':{'category':{'id':'53968796','type_id':'970663046'}}}})
    items=[]
    for model in ('2000431','2000432','2000433'):
        row=variant();row['offer_id']=model;row['approved_seller_sku']='2000431'
        row['category']={'id':'53968796','type_id':'970663046'};row['official_profile']=profile
        row['independent_card']=independent_card(model)
        items.append(build_ozon_import_item_from_frozen_variant(row))
    attrs=[{r['id']:r['values'] for r in item['attributes']} for item in items]
    assert len({a[9048][0]['value'] for a in attrs})==3
    assert [a[8513][0]['value'] for a in attrs]==['1']*3
    assert all((i['description_category_id'],i['type_id'])==(53968796,970663046) for i in items)
    assert all(12132 not in a for a in attrs) # Optional subtype remains unverified.


def test_untrusted_structure_json_does_not_disable_default_guard():
    from modules.ozon.approved_publication_v4 import project_ozon_v4_variants,OzonApprovedPublicationError
    from test_fridge_cover_channel_contracts import _fridge_profile_replay
    from test_ozon_toilet_paper_holder_profile import variant
    row=variant()
    snapshot={'schema_version':'approved-publication-snapshot/v4','offer_id':'80001','snapshot_digest':'sha256:'+'a'*64,
        'publication_targets':[{'target_label':'ozon:RU','platform':'ozon','site':'RU','store':'RU'}],
        'product':{'main_category':{'name':'冰箱防尘盖布带侧袋'},'title':row['title'],'description':row['description'],'images':row['images'],
            'content_by_target':{'ozon:RU':{'locale':'ru-RU','title':row['title'],'description':row['description']}},
            'ozon_card_structure':{'mode':'THREE_INDEPENDENT_CARDS','user_approved':True}},
        'categories_by_target':{'ozon:RU':{'category':None,'decision':{'status':'DEFERRED_TO_SKILL'}}},
        'skus':[{'variant_key':str(i),'seller_sku':'2000431','model_sku':str(2000431+i),
            'specification':{'Pattern':name},'parcel':row['parcel'],'variant_images':row['images'],
            'prices':{'ozon:RU':{'amount':row['price'],'old_price_cny':row['old_price'],'currency':'CNY'}}}
            for i,name in enumerate(['Daisy','Feather','Starry Sky'])]}
    resolver,_=_fridge_profile_replay()
    with pytest.raises(OzonApprovedPublicationError,match='SINGLE_CARD_PATTERN_VARIATION_UNVERIFIED'):
        project_ozon_v4_variants(snapshot,target_labels=('ozon:RU',),official_profile_resolver=resolver)


def test_card_readback_requires_distinct_model_and_one_piece():
    from shared_platform.publication_ozon_card_structure import independent_card, card_readback_matches
    card=independent_card('2000431')
    observed={'attributes':{'9048':[{'value':card['model_name']}],'8513':[{'value':'1'}]}}
    assert card_readback_matches(card,observed)
    for drift in ('model','quantity','missing'):
        bad=deepcopy(observed)
        if drift=='model':bad['attributes']['9048'][0]['value']='2000432-refrigerator-cover'
        elif drift=='quantity':bad['attributes']['8513'][0]['value']='2'
        else:bad['attributes'].pop('9048')
        assert not card_readback_matches(card,bad)


def test_trusted_successor_preserves_full_scope_and_separate_user_authority(structure_sources):
    reader,*_,targets=structure_sources
    projection=reader.read()
    assert projection['selected_target_labels']==targets
    assert len(projection['unchanged_target_labels'])==14
    assert len(projection['cards'])==3 and projection['marketplace_approval'] is False
    from shared_platform.publication_ozon_card_structure import OzonCardStructureReader
    from types import SimpleNamespace
    with pytest.raises(ValueError,match='NATIVE_SERVICE_REQUIRED'):
        OzonCardStructureReader(SimpleNamespace(),path=reader.path,expected_sha256=reader.sha256)


@pytest.mark.parametrize('changed',['receipt','sidecar','r1'])
def test_trusted_reader_rechecks_original_pins_on_every_read(structure_sources,changed):
    reader,receipt,sidecar,r1,_=structure_sources
    path={'receipt':receipt,'sidecar':sidecar,'r1':r1}[changed]
    path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='CHANGED'):
        reader.read()


def test_trusted_v4_projection_and_final_review_bind_all_15_targets(structure_sources,monkeypatch):
    from shared_platform import publication_ozon_card_structure as structure,publication_autopilot
    from modules.products import server
    from modules.ozon.approved_publication_v4 import project_ozon_v4_variants,OzonApprovedPublicationError
    from test_fridge_cover_channel_contracts import _fridge_profile_replay
    from test_ozon_toilet_paper_holder_profile import variant
    reader,*_,targets=structure_sources
    monkeypatch.setattr(structure,'_INSTALLED',reader);monkeypatch.setattr(server,'ROOT',reader.service.profile.root)
    row=variant();projection=reader.read()
    snapshot={'schema_version':'approved-publication-snapshot/v4','offer_id':'80001','snapshot_digest':'sha256:'+'a'*64,
        'publication_targets':[dict(target_label=t,platform=t.split(':')[0],site=t.split(':')[1],store=t.split(':')[1]) for t in targets],
        'product':{'main_category':{'name':'冰箱防尘盖布带侧袋'},'title':row['title'],'description':row['description'],
            'images':row['images'],'content_by_target':{t:{'locale':'ru-RU' if t=='ozon:RU' else 'en',
                'title':row['title'],'description':row['description']} for t in targets},
            'ozon_card_structure':projection},
        'categories_by_target':{'ozon:RU':{'category':None,'decision':{'status':'DEFERRED_TO_SKILL'}}},
        'skus':[{'variant_key':c['variant_key'],'seller_sku':'2000431','model_sku':c['model_sku'],
            'specification':{'Pattern':name},'parcel':row['parcel'],'variant_images':row['images'],
            'prices':{'ozon:RU':{'amount':row['price'],'old_price_cny':row['old_price'],'currency':'CNY'}}}
            for c,name in zip(projection['cards'],['Daisy','Feather','Starry Sky'])]}
    resolver,_=_fridge_profile_replay()
    before=deepcopy(snapshot)
    projected=project_ozon_v4_variants(snapshot,target_labels=('ozon:RU',),official_profile_resolver=resolver)
    assert len(projected)==3 and all(s['independent_card']['quantity_per_pack']==1 for s in projected)
    assert snapshot==before
    manifest=publication_autopilot._build_final_review_manifest(snapshot,snapshot['publication_targets'],snapshot['skus'],None)
    assert len(manifest['targets'])==15
    assert manifest['targets'][-1]['listing_structure']['card_count']==3
    assert all(t['listing_structure']['variants_per_card']==3 for t in manifest['targets'][:-1])
    profile=resolver(snapshot);profile.pop('independent_card_attributes')
    with pytest.raises(OzonApprovedPublicationError,match='OFFICIAL_METADATA_REQUIRED'):
        project_ozon_v4_variants(snapshot,target_labels=('ozon:RU',),official_profile_resolver=lambda _:profile)
    changed=deepcopy(snapshot)
    changed['categories_by_target']['ozon:RU']['category']={'id':'53968796','type_id':'92641'}
    with pytest.raises(ValueError,match='type conflicts'):
        project_ozon_v4_variants(changed,target_labels=('ozon:RU',),official_profile_resolver=resolver)
    changed=deepcopy(snapshot);changed['product']['ozon_card_structure']['cards'][0]['quantity_per_pack']=2
    with pytest.raises(OzonApprovedPublicationError,match='SINGLE_CARD'):
        project_ozon_v4_variants(changed,target_labels=('ozon:RU',),official_profile_resolver=resolver)
    changed=deepcopy(snapshot);changed['publication_targets'].pop(0)
    with pytest.raises(ValueError):
        project_ozon_v4_variants(changed,target_labels=('ozon:RU',),official_profile_resolver=resolver)
    monkeypatch.setattr(structure,'_INSTALLED',None)
    with pytest.raises(OzonApprovedPublicationError,match='SINGLE_CARD'):
        project_ozon_v4_variants(snapshot,target_labels=('ozon:RU',),official_profile_resolver=resolver)


def test_actual_snapshot_builder_and_compiler_bind_structure_without_new_approval(structure_sources):
    from domains.product_operations.approved_publication_snapshot import build_approved_publication_snapshot,validate_approved_publication_snapshot
    from shared_platform.publication_autopilot import compile_release_candidate
    from test_approved_publication_snapshot import _approved_plan,_rebind
    from test_b4b_release_compiler import policy,INCIDENTS
    reader,*_,targets=structure_sources
    projection=reader.read();plan=_approved_plan();payload=plan['payload']
    payload['product_id']='80001';payload['targets']=targets;payload['seller_sku']='2000431'
    models=[c['model_sku'] for c in projection['cards']]
    keys=[c['variant_key'] for c in projection['cards']]
    payload['sku_lineage']['assignment']={'seller_sku':'2000431','model_skus':[
        {'variant_key':k,'model_sku':m} for k,m in zip(keys,models)]}
    facts=payload['product_facts'];facts['title']='Household textile';facts['description']='Three floral patterns.'
    facts['category']={'id':'household-textile','name':'Household textile'}
    facts['selected_sku_keys']=keys
    template=deepcopy(next(iter(facts['sku_commercial_facts'].values())))
    facts['sku_commercial_facts']={k:{**deepcopy(template),'specification':{'Pattern':name},
        'cost':{'amount':'4','currency':'CNY'},'weight_kg':'0.2','package_cm':['30','30','5']}
        for k,name in zip(keys,['Daisy','Feather','Starry Sky'])}
    originals=deepcopy(facts['categories_by_target'])
    prices=deepcopy(payload['pricing']['selected_targets'])
    facts['categories_by_target']={};payload['pricing']['selected_targets']={}
    for target in targets:
        platform,site=target.split(':');source={'tiktok':'tiktok:LH_PH','shopee':'shopee:PH','ozon':'ozon:RU'}[platform]
        category=deepcopy(originals[source]);category.update(target_label=target,platform=platform,site=site,store=site)
        facts['categories_by_target'][target]=category
        targetprice=deepcopy(prices[source]);base=targetprice['sku_prices'][0]
        targetprice['sku_prices']=[{**deepcopy(base),'model_sku':m} for m in models]
        payload['pricing']['selected_targets'][target]=targetprice
    master=payload['shopee_global_master']
    master['sku_original_prices_cny']=[{'model_sku':m,'amount':'40.12','currency':'CNY'} for m in models]
    master['variant_image_positions']=[{'model_sku':m,'position':0,'image_url':facts['image_urls'][0]} for m in models]
    master['parcel_envelope'].update(weight_kg='0.2',package_cm=[30,30,5])
    facts['ozon_card_structure']=projection
    approved=build_approved_publication_snapshot(_rebind(plan)).payload()
    assert validate_approved_publication_snapshot(approved).payload()==approved
    assert approved['product']['ozon_card_structure']==projection and len(approved['publication_targets'])==15
    compiled=compile_release_candidate(approved,policy=policy(40),incident_registry=INCIDENTS)
    assert compiled['review_manifest']['product']['ozon_card_structure']==projection
    assert len(compiled['review_manifest']['targets'])==15
    original=deepcopy(plan);original['payload']['product_facts'].pop('ozon_card_structure')
    original_snapshot=build_approved_publication_snapshot(_rebind(original)).payload()
    old=compile_release_candidate(original_snapshot,policy=policy(40),incident_registry=INCIDENTS)
    assert old['candidate_digest']!=compiled['candidate_digest']
    assert original_snapshot['snapshot_digest']!=approved['snapshot_digest']
    assert not {'human_approval','approval_receipt'} & set(compiled)
