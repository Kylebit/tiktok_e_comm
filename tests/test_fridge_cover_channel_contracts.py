"""Focused regressions for the observed fridge-cover consumer failures.

Provider-shaped examples are inert normalized metadata; no real account,
database, approval, upload, or provider transport is consumed by these tests.
"""
from copy import deepcopy
import hashlib
import json

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def denied(*args, **kwargs):
        raise AssertionError('offline channel contract attempted a network connection')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


def _digest(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _shopee_decision():
    category={'id':'101154','name':'Furniture & Appliance Covers','path':[{'id':'101154','name':'Furniture & Appliance Covers'}]}
    selected=[{'attribute_id':100818,'attribute_value_list':[{'value_id':4228,'original_value_name':'No'}]},
              {'attribute_id':100942,'attribute_value_list':[{'value_id':0,'original_value_name':'128*52*2'}]}]
    decision={'status':'EVIDENCE_BOUND','category':category,'required_attributes':selected,'source_decision_digest':'sha256:'+'a'*64}
    decision['decision_digest']=_digest({'schema_version':'shopee-global-category-decision/v1',**decision})
    return {'approved_category':category,'category_decision':decision}


def _shopee_tree():
    return [{'attribute_id':100818,'original_attribute_name':'Seasonal Decoration','is_mandatory':True,'input_type':'SINGLE_SELECT',
             'attribute_value_list':[{'value_id':4228,'original_value_name':'No'},{'value_id':4227,'original_value_name':'Yes'}]},
            {'attribute_id':100942,'original_attribute_name':'Dimension (L x W x H)','is_mandatory':True,'input_type':'TEXT',
             'attribute_value_list':[{'value_id':0,'original_value_name':''}]}]


def test_shopee_native_receipt_keeps_text_value_zero_and_original_status():
    from modules.shopee.global_v4_live_runtime import _approved_required_attributes
    command=_shopee_decision();before=deepcopy(command)
    required,missing=_approved_required_attributes(command,_shopee_tree(),selected_category_id='101154')
    assert not missing
    assert required==command['category_decision']['required_attributes']
    assert command==before


@pytest.mark.parametrize('mutation',['digest','scope','enum','unit'])
def test_shopee_native_receipt_rejects_unbound_or_changed_values(mutation):
    from modules.shopee.global_v4_live_runtime import _approved_required_attributes,ShopeeGlobalV4LiveRuntimeError
    command=_shopee_decision()
    if mutation=='digest':command['category_decision']['decision_digest']='sha256:'+'b'*64
    if mutation=='scope':command['approved_category']['id']='101157'
    if mutation=='enum':command['category_decision']['required_attributes'][0]['attribute_value_list'][0]['value_id']=999
    if mutation=='unit':command['category_decision']['required_attributes'][1]['attribute_value_list'][0]['value_unit']='cm'
    if mutation in {'enum','unit'}:
        unsigned={k:v for k,v in command['category_decision'].items() if k!='decision_digest'}
        command['category_decision']['decision_digest']=_digest({'schema_version':'shopee-global-category-decision/v1',**unsigned})
    with pytest.raises(ShopeeGlobalV4LiveRuntimeError):
        _approved_required_attributes(command,_shopee_tree(),selected_category_id='101154')


def test_fridge_cover_exact_semantic_uses_dust_cover_not_magnets_or_wallpaper():
    from shared_platform.product_publication_live_dependencies import _tiktok_profile,LivePublicationDependencyError
    from shared_platform.product_snapshot import project_approved_tiktok_category_decisions
    product={'main_category':{'name':'冰箱防尘盖布带侧袋'}}
    assert _tiktok_profile(product)['primary']=='700665'
    assert project_approved_tiktok_category_decisions(product['main_category'],targets=('tiktok:LH_PH',))['tiktok:LH_PH']['category_id']=='700665'
    with pytest.raises(LivePublicationDependencyError):
        _tiktok_profile({'main_category':{'name':'unclassified fabric'},'title':'fridge top cover'})


def test_frozen_ozon_pair_has_lossless_technical_projection():
    from shared_platform.publication_r3_image_bridge import parse_marketplace_category_candidate
    original={'status':'PROPOSED','candidate':'53968796 / 970663046 · Accessories and Spare Parts for Refrigerators · Чехол',
              'evidence_digest':'sha256:'+'a'*64}
    before=deepcopy(original)
    parsed=parse_marketplace_category_candidate('ozon:RU',original)
    assert parsed['id']=='53968796' and parsed['type_id']=='970663046'
    assert original==before
    with pytest.raises(ValueError):parse_marketplace_category_candidate('tiktok:LH_PH',original)


def _explicit_identity():
    snapshot={'offer_id':'80001','fact_snapshot':{'product_facts':{'seller_sku':'2000431','skus':[
        {'source_key':'manual-01','seller_sku':'2000431'},{'source_key':'manual-02','seller_sku':'2000432'},
        {'source_key':'manual-03','seller_sku':'2000433'}]}}}
    snapshot['snapshot_digest']=_digest(snapshot)
    binding={'schema':'orbit-new-B-R1-source-and-identity-binding/v1','offer_id':'80001',
      'snapshot':{'snapshot_digest':snapshot['snapshot_digest']},
      'canonical_to_B_provider_sku':{'0431':'2000431','0432':'2000432','0433':'2000433'},
      'source_positions':[{'position':i+1,'internal_sku':sku} for i,sku in enumerate(['0431','0432','0433'])]}
    return snapshot,binding


def test_explicit_frozen_map_resolves_alias_without_broad_suffix_inference():
    from shared_platform.publication_variant_identity import resolve_explicit_variant_aliases
    from shared_platform.internal_catalog_sku import internal_sku
    snapshot,binding=_explicit_identity()
    result=resolve_explicit_variant_aliases(offer_id='80001',round1_snapshot=snapshot,identity_binding=binding)
    assert result['internal_sku']=='0431'
    assert result['variant_key_to_internal_sku']=={'manual-01':'0431','manual-02':'0432','manual-03':'0433'}
    assert internal_sku('2000431')=='2000431'
    bad=deepcopy(binding);bad['canonical_to_B_provider_sku']['9999']=bad['canonical_to_B_provider_sku'].pop('0431')
    with pytest.raises(ValueError):resolve_explicit_variant_aliases(offer_id='80001',round1_snapshot=snapshot,identity_binding=bad)


def test_shopee_free_text_readback_checks_literal_and_unit():
    from modules.shopee.global_v4_executor import _verify_readback,ShopeeGlobalV4Error
    from test_shopee_global_v4_executor import _request,_Runtime
    from modules.shopee.global_v4_executor import project_shopee_global_v4_command
    command=project_shopee_global_v4_command(_request().snapshot)
    runtime=_Runtime();runtime.command=command
    observed=runtime.read_global_item('9001');observed['category_id']='101154'
    selected=_shopee_decision()['category_decision']['required_attributes']
    observed['attribute_list']=deepcopy(selected)
    observed['attribute_list'][1]['attribute_value_list'][0]['original_value_name']='30*30*5'
    with pytest.raises(ShopeeGlobalV4Error,match='required attributes'):
        _verify_readback(command,global_item_id='9001',item=observed,model_response=runtime.read_global_models('9001'),
          expected_image_ids=None,expected_category={'id':'101154'},expected_required_attributes=selected)


def test_explicit_alias_reader_requires_pinned_companion_bytes(tmp_path):
    from shared_platform.publication_variant_identity import read_explicit_variant_aliases
    snapshot,binding=_explicit_identity()
    snapshot_raw=json.dumps(snapshot,ensure_ascii=False).encode()
    (tmp_path/'round1-approved-snapshot.json').write_bytes(snapshot_raw)
    binding['snapshot']['sha256']=hashlib.sha256(snapshot_raw).hexdigest()
    raw=json.dumps(binding,ensure_ascii=False).encode()
    (tmp_path/'SOURCE_AND_IDENTITY_BINDING.json').write_bytes(raw)
    pin=hashlib.sha256(raw).hexdigest()
    state={'review':{'seller_sku':'2000431'},'new_b_identity':{'canonical_to_B_provider_sku':binding['canonical_to_B_provider_sku']}}
    args=dict(offer_id='80001',round1_snapshot=snapshot,state=state,report_directory=tmp_path)
    assert read_explicit_variant_aliases(**args,expected_binding_sha256=pin)['internal_sku']=='0431'
    with pytest.raises(ValueError,match='PIN_REQUIRED'):
        read_explicit_variant_aliases(**args,expected_binding_sha256=None)
    (tmp_path/'SOURCE_AND_IDENTITY_BINDING.json').write_bytes(raw+b'\n')
    with pytest.raises(ValueError,match='BYTES_CHANGED'):
        read_explicit_variant_aliases(**args,expected_binding_sha256=pin)


def _fridge_metadata():
    from pathlib import Path
    return json.loads(Path(__file__).with_name('fixtures').joinpath('fridge_cover_official_category.json').read_text(encoding='utf-8'))


def _fridge_profile_replay():
    from shared_platform.product_publication_live_dependencies import OfficialOzonFridgeMagnetProfileResolver
    data=_fridge_metadata();calls=[]
    def post(path,body):
        calls.append((path,deepcopy(body)))
        assert body['language']=='DEFAULT'
        if path=='/v1/description-category/tree':return deepcopy(data['tree'])
        assert (body['description_category_id'],body['type_id'])==(53968796,970663046)
        if path=='/v1/description-category/attribute':return deepcopy(data['attributes'])
        if path=='/v1/description-category/attribute/values/search':return deepcopy(data['dictionaries'][str(body['attribute_id'])])
        raise AssertionError(path)
    return OfficialOzonFridgeMagnetProfileResolver(post=post),calls


def test_real_fridge_profile_resolves_required_metadata_without_new_claims():
    resolver,calls=_fridge_profile_replay()
    snapshot={'schema_version':'approved-publication-snapshot/v4','product':{'main_category':{'name':'冰箱防尘盖布带侧袋'}},
      'categories_by_target':{'ozon:RU':{'category':{'id':'53968796','type_id':'970663046','name':'Accessories'}}}}
    before=deepcopy(snapshot);resolved=resolver(snapshot)
    assert resolved['type_id']==970663046
    assert set(resolved['required_attributes'])=={'brand','model_name','product_type'}
    assert resolved['required_attributes']['brand']['dictionary_value_id']==126745801
    assert len(calls)==4 and snapshot==before
    assert _fridge_metadata()['variative_attribute_ids']==[8513]
    bad=deepcopy(snapshot);bad['categories_by_target']['ozon:RU']['category']['type_id']='92641'
    from shared_platform.product_publication_live_dependencies import LivePublicationDependencyError
    with pytest.raises(LivePublicationDependencyError,match='type conflicts'):resolver(bad)


def test_ozon_fridge_pattern_group_blocks_before_any_dispatch():
    from modules.ozon.approved_publication_v4 import project_ozon_v4_variants,OzonApprovedPublicationError
    from test_ozon_toilet_paper_holder_profile import variant
    base=variant()
    snapshot={'schema_version':'approved-publication-snapshot/v4','snapshot_digest':'sha256:'+'a'*64,
      'publication_targets':[{'target_label':'ozon:RU','platform':'ozon','site':'RU','store':'RU'}],
      'product':{'main_category':{'name':'冰箱防尘盖布带侧袋'},'title':base['title'],'description':base['description'],
        'images':base['images'],'content_by_target':{'ozon:RU':{'locale':'ru-RU','title':base['title'],'description':base['description']}}},
      'categories_by_target':{'ozon:RU':{'category':None,'decision':{'status':'DEFERRED_TO_SKILL'}}},
      'skus':[{'variant_key':str(i),'seller_sku':'2000431','model_sku':str(2000431+i),
        'specification':{'Pattern':name},'parcel':base['parcel'],'variant_images':base['images'],
        'prices':{'ozon:RU':{'amount':base['price'],'old_price_cny':base['old_price'],'currency':'CNY'}}}
        for i,name in enumerate(['Daisy','Feather','Starry Sky'])]}
    resolver,_=_fridge_profile_replay()
    with pytest.raises(OzonApprovedPublicationError,match='SINGLE_CARD_PATTERN_VARIATION_UNVERIFIED'):
        project_ozon_v4_variants(snapshot,target_labels=('ozon:RU',),official_profile_resolver=resolver)
    without_resolver=deepcopy(snapshot)
    without_resolver['categories_by_target']['ozon:RU']={'target_label':'ozon:RU','platform':'ozon','site':'RU','store':'RU','category':{'id':'53968796','type_id':'970663046','name':'Accessories',
        'path':[{'id':'53968796','name':'Accessories'}]},'decision':{'status':'APPROVED'}}
    with pytest.raises(OzonApprovedPublicationError,match='SINGLE_CARD_PATTERN_VARIATION_UNVERIFIED'):
        project_ozon_v4_variants(without_resolver,target_labels=('ozon:RU',))
    # An inert different type under the same parent is outside this exact
    # metadata-specific gate; this does not claim its live profile is enabled.
    other_type=deepcopy(without_resolver)
    other_type['categories_by_target']['ozon:RU']['category']['type_id']='123456789'
    assert len(project_ozon_v4_variants(other_type,target_labels=('ozon:RU',)))==3
    from modules.ozon.approved_publication_v4 import execute_ozon_v4_publication
    dispatched=[]
    result=execute_ozon_v4_publication(snapshot,target_labels=('ozon:RU',),official_profile_resolver=resolver,
      dispatch_variant=lambda variant:dispatched.append(variant),readback_variants=lambda offers:[])
    assert result['external_write_count']==0 and not dispatched
    evidence=result['targets'][0]['evidence']
    assert evidence['stage']=='PREPARATION' and evidence['provider_code']=='ozon_single_card_variation_unverified'


def test_ozon_category_type_pair_survives_actual_snapshot_validation():
    from test_approved_publication_snapshot import _approved_plan
    from domains.product_operations import build_approved_publication_snapshot
    from domains.product_operations.approved_publication_snapshot import validate_approved_publication_snapshot,ApprovedPublicationSnapshotError
    snapshot=build_approved_publication_snapshot(_approved_plan()).payload()
    category=snapshot['categories_by_target']['ozon:RU']['category']
    category['type_id']='970663046'
    snapshot.pop('snapshot_digest');snapshot['snapshot_digest']=_digest(snapshot)
    assert validate_approved_publication_snapshot(snapshot).payload()['categories_by_target']['ozon:RU']['category']['type_id']=='970663046'
    bad=deepcopy(snapshot);target=next(v for k,v in bad['categories_by_target'].items() if k.startswith('shopee:'))
    target['category']['type_id']='970663046';bad.pop('snapshot_digest');bad['snapshot_digest']=_digest(bad)
    with pytest.raises(ApprovedPublicationSnapshotError):validate_approved_publication_snapshot(bad)


@pytest.mark.parametrize('drift',[None,'category','dimension'])
def test_shopee_actual_create_consumer_verifies_native_cover_category_and_attributes(drift):
    from dataclasses import replace
    from test_shopee_global_v4_executor import _request,_Runtime
    from modules.shopee.global_v4_executor import project_shopee_global_v4_command,ShopeeGlobalV4Resolver,ShopeeGlobalV4Error
    request=_request();body=deepcopy(request.snapshot)
    decision=_shopee_decision()['category_decision']
    body['shopee_global_master']['category_decision']=deepcopy(decision)
    for label,row in body['categories_by_target'].items():
        if label.startswith('shopee:'):
            row['category']=deepcopy(decision['category'])
            row['decision']={'status':'APPROVED','decision_digest':decision['source_decision_digest']}
    body.pop('snapshot_digest');body['snapshot_digest']=_digest(body)
    request=replace(request,snapshot=body)
    command=project_shopee_global_v4_command(body)
    runtime=_Runtime();runtime.command=command
    preparation=runtime.prepare_creation(command)
    preparation['category']=deepcopy(decision['category'])
    preparation['required_attributes']=deepcopy(decision['required_attributes'])
    runtime.prepare_override=preparation
    def observed(item):
        item['category_id']='101157' if drift=='category' else '101154'
        item['attribute_list']=deepcopy(decision['required_attributes'])
        if drift=='dimension':item['attribute_list'][1]['attribute_value_list'][0]['original_value_name']='30*30*5'
    runtime.mutate_item=observed
    resolver=ShopeeGlobalV4Resolver(runtime=runtime)
    if drift is None:assert resolver(request)=='9001'
    else:
        with pytest.raises(ShopeeGlobalV4Error,match='category drifted|required attributes drifted'):resolver(request)
