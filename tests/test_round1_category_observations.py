"""Service capture, immutable store, offline restart, and official attribute semantics."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from shared_platform import release_store, release_control
from shared_platform.round1_category_evidence import digest, CategoryEvidenceError
from shared_platform.round1_category_observations import build_receipt, resolve_record
from shared_platform.publication_rounds import build_round1_snapshot
from shared_platform.publication_stock_policy import default_publication_stock_policy
from modules.shopee import global_plan_candidate as channel
from modules.shopee import oneclick_release as shopee
from modules.products import server
from test_shopee_global_plan_candidate import _OfficialReadFake

ROOT=Path(__file__).resolve().parents[1]


def prepare_module():
    spec=importlib.util.spec_from_file_location('r1_observer_prepare_fixture',ROOT/'skills/prepare-product-publication/scripts/prepare_product_publication.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def context(tmp_path,monkeypatch):
    fake=_OfficialReadFake()
    tree=[
        dict(attribute_id=11,original_attribute_name='Single',is_mandatory=True,input_type='SINGLE_SELECT',
             attribute_value_list=[dict(value_id=0,original_value_name='Zero')]),
        dict(attribute_id=12,original_attribute_name='Multi',is_mandatory=True,input_type='MULTI_SELECT',
             attribute_value_list=[dict(value_id=1,original_value_name='One',value_unit='cm'),dict(value_id=2,original_value_name='Two',value_unit='cm')]),
        dict(attribute_id=13,original_attribute_name='Text',is_mandatory=True,input_type='TEXT',
             attribute_value_list=[dict(value_id=0,original_value_name='Text')]),
    ]
    fake.attribute_rows_by_category[101]=tree
    def transport(region):
        original=fake.transport()
        return shopee.ShopeePrepareTransport(credentials=replace(original.credentials,region=region),
                                            merchant_get=fake.merchant_get,shop_get=original.shop_get)
    monkeypatch.setattr(shopee,'_prepare_transport_factory',transport)
    credentials=transport('MY').credentials
    account=digest(dict(region='MY',shop_id=credentials.shop_id,merchant_id=credentials.merchant_id))
    selected=[dict(attribute_id=11,attribute_value_list=[dict(value_id=0,original_value_name='Zero')]),
              dict(attribute_id=12,attribute_value_list=[dict(value_id=1,original_value_name='One',value_unit='cm'),dict(value_id=2,original_value_name='Two',value_unit='cm')]),
              dict(attribute_id=13,attribute_value_list=[dict(value_id=0,original_value_name='Explicit text')])]
    data=dict(offer_id='12345',product_center_revision=7,requested_targets=['shopee:MY','shopee:PH'],source_region='MY',
              account_identity_digest=account,category_id=101,selected_attributes=selected)
    preview=dict(ok=True,revision=7,review=dict(title='Fixture product',category='Fixture category',selected_sites=['shopee:MY','shopee:PH']))
    preview['review'].update(cost_cny=8,weight_kg=0.2,package_cm=[20,20,3])
    preview['pricing']={'sea':[{'id':'shopee_my','list_price':20,'currency':'MYR'},
                               {'id':'shopee_ph','list_price':200,'currency':'PHP'}]}
    monkeypatch.setattr(release_control,'build_release_dashboard',lambda **_:deepcopy(preview))
    store=release_store.ReleaseStore(tmp_path/'category.sqlite3')
    monkeypatch.setattr(release_store,'default_release_store',lambda:store)
    return data,preview,fake,store


def capture(data):
    status,result=server._round1_category_request('capture',data)
    assert status==200,result
    return result['receipt']


def test_capture_store_restart_prepare_and_freeze_without_provider(tmp_path,monkeypatch):
    data,preview,fake,store=context(tmp_path,monkeypatch)
    receipt=capture(data)
    assert receipt['official_read_count']==2 and len(fake.calls)==2
    assert receipt['source_region']=='MY' and receipt['account_identity_digest']==data['account_identity_digest']
    assert receipt['selected_attributes']==data['selected_attributes']
    assert receipt['auth_writes']==receipt['business_write_count']==0
    restarted=release_store.ReleaseStore(store.path)
    monkeypatch.setattr(release_store,'default_release_store',lambda:restarted)
    monkeypatch.setattr(shopee,'_prepare_transport_factory',lambda _:pytest.fail('offline restart must not load a transport'))
    module=prepare_module()
    plan=dict(schema_version='first-review-image-plan/v1',status='PROPOSED',source_actions=[],generated_assets=[],
              summary=dict(translation_positions=[],localized_output_count=0,net_new_output_count=0,paid_generation_required=False))
    packet=module.prepare_offer(offer_id=data['offer_id'],requested_targets=data['requested_targets'],preview_builder=lambda _:preview,
        image_execution_plan=plan,category_source_region='MY',category_observation=receipt['observer_reference'],category_account_digest=data['account_identity_digest'])
    assert packet['status']=='FIRST_REVIEW_READY',packet['blockers']
    snapshot=build_round1_snapshot(first_review=packet,state=dict(_revision=8,review={'selected_sites':[]},
        product_approval=dict(status='approved',approved_by='Kyle',approval_id='fixture',input_fingerprint='fixture')),
        approved_by='Kyle',report_directory=tmp_path)
    assert snapshot['fact_snapshot']['category_evidence_binding']['receipt']==receipt
    assert len(fake.calls)==2


@pytest.mark.parametrize('case',['single-many','wrong-unit','unknown-id','missing-mandatory','text-unit','text-empty'])
def test_actual_official_attribute_revalidation_rejects_invalid_intent(tmp_path,monkeypatch,case):
    data,_,_,store=context(tmp_path,monkeypatch)
    selected=data['selected_attributes']
    if case=='single-many':selected[0]['attribute_value_list']*=2
    elif case=='wrong-unit':selected[1]['attribute_value_list'][0]['value_unit']='kg'
    elif case=='unknown-id':selected[1]['attribute_value_list'][0]['value_id']=999
    elif case=='missing-mandatory':selected.pop()
    elif case=='text-unit':selected[2]['attribute_value_list'][0]['value_unit']='cm'
    else:selected[2]['attribute_value_list'][0]['original_value_name']=''
    status,result=server._round1_category_request('capture',data)
    assert status==409 and not result['ok']
    assert not store.path.exists()


@pytest.mark.parametrize('case',['revision','account','target','source-region','extra-observation'])
def test_capture_identity_failures_precede_reads(tmp_path,monkeypatch,case):
    data,_,fake,store=context(tmp_path,monkeypatch)
    if case=='revision':data['product_center_revision']=8
    elif case=='account':data['account_identity_digest']=digest('wrong account')
    elif case=='target':data['requested_targets']=['shopee:VN']
    elif case=='source-region':data['source_region']=None
    else:data['observation']={'authority':'official'}
    status,_=server._round1_category_request('capture',data)
    assert status in {400,409}
    assert fake.calls==[] and not store.path.exists()


@pytest.mark.parametrize('field,value',[('revision',8),('title','changed'),('category','changed'),('selected_sites',['shopee:PH'])])
def test_capture_rechecks_service_inputs_before_persistence(tmp_path,monkeypatch,field,value):
    data,preview,fake,store=context(tmp_path,monkeypatch)
    original=fake.merchant_get
    def mutate(path,params):
        response=original(path,params)
        if field=='revision':preview[field]=value
        else:preview['review'][field]=value
        return response
    fake.merchant_get=mutate
    status,_=server._round1_category_request('capture',data)
    assert status==409 and not store.path.exists()


def test_store_idempotence_concurrency_and_immutable_conflict(tmp_path,monkeypatch):
    data,_,_,store=context(tmp_path,monkeypatch);receipt=capture(data)
    observed=store.round1_category_observation(receipt['observer_reference'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:store.persist_round1_category_observation(observed),range(2)))
    assert results==[observed,observed]
    conflict=deepcopy(observed);conflict['observed_at']='2026-01-01T00:00:00+00:00'
    with pytest.raises(release_store.ImmutableReleaseError,match='CATEGORY_OBSERVATION_CONFLICT'):
        store.persist_round1_category_observation(conflict)
    assert store.round1_category_observation(receipt['observer_reference'])==observed


@pytest.mark.parametrize('case',['missing','account','revision','targets','region','facts'])
def test_offline_resolver_rejects_identity_drift(tmp_path,monkeypatch,case):
    data,_,_,store=context(tmp_path,monkeypatch);receipt=capture(data)
    review=receipt['review_input'];reference=receipt['observer_reference'];account=data['account_identity_digest']
    if case=='missing':reference='category-observation:missing'
    elif case=='account':account=digest('wrong')
    elif case=='revision':review['product_center_revision']+=1
    elif case=='targets':review['target_selection']['requested'].reverse()
    elif case=='region':review['category_review_context']['source_region']='PH'
    else:review['product_facts']['title']='changed'
    with pytest.raises(CategoryEvidenceError):resolve_record(store,reference,review=review,account_identity_digest=account)


def test_record_revocation_blocks_read_without_rewriting_snapshot(tmp_path,monkeypatch):
    data,_,_,store=context(tmp_path,monkeypatch);receipt=capture(data)
    reference=receipt['observer_reference']
    store.invalidate_round1_category_observation(reference,'SOURCE_REVOKED')
    store.invalidate_round1_category_observation(reference,'SOURCE_REVOKED')
    with pytest.raises(CategoryEvidenceError,match='CATEGORY_OBSERVATION_INVALIDATED'):
        store.round1_category_observation(reference)


def test_corrupt_record_rejected_after_direct_fixture_damage(tmp_path,monkeypatch):
    data,_,_,store=context(tmp_path,monkeypatch);receipt=capture(data)
    # Deliberate damage only to this synthetic DB; production immutable guard remains.
    import sqlite3
    with sqlite3.connect(store.path) as connection:
        connection.execute('DROP TRIGGER round1_category_observation_no_update')
        connection.execute("UPDATE round1_category_observations SET record_digest='wrong'")
    with pytest.raises(release_store.ImmutableReleaseError,match='CATEGORY_RECORD_CORRUPT'):
        store.round1_category_observation(receipt['observer_reference'])


def test_actual_cli_reference_and_no_implicit_capture(tmp_path,monkeypatch,capsys):
    data,preview,fake,_=context(tmp_path,monkeypatch);receipt=capture(data)
    module=prepare_module()
    monkeypatch.setattr(module,'_default_preview_builder',lambda:lambda _:deepcopy(preview))
    monkeypatch.setattr(shopee,'_prepare_transport_factory',lambda _:pytest.fail('CLI cannot capture'))
    output=tmp_path/'first-review.json'
    assert module.main(['--offer-id','12345','--targets','shopee:MY,shopee:PH','--category-source-region','MY',
                        '--category-observation',receipt['observer_reference'],'--category-account-digest',data['account_identity_digest'],
                        '--output',str(output)])==0
    packet=json.loads(output.read_text(encoding='utf-8'))
    assert packet['category_evidence_binding']['receipt']==receipt
    assert len(fake.calls)==2
    capsys.readouterr()


def test_self_consistent_file_cannot_register_rich_source(tmp_path,monkeypatch):
    data,preview,_,store=context(tmp_path,monkeypatch);receipt=capture(data)
    # A valid receipt alone has no trusted callback in the file input path.
    packet=prepare_module().prepare_offer(offer_id='12345',requested_targets=data['requested_targets'],
        category_source_region='MY',category_receipt=receipt,preview_builder=lambda _:preview)
    assert packet['category_evidence_binding']['code']=='SOURCE_UNVERIFIED'


def test_transport_region_mismatch_before_reads(tmp_path,monkeypatch):
    data,_,fake,store=context(tmp_path,monkeypatch)
    original=fake.transport()
    wrong=shopee.ShopeePrepareTransport(credentials=replace(original.credentials,region='PH'),
                                      merchant_get=original.merchant_get,shop_get=original.shop_get)
    monkeypatch.setattr(shopee,'_prepare_transport_factory',lambda _:wrong)
    status,result=server._round1_category_request('capture',data)
    assert status==409 and result['code']=='shopee_category_source_region_invalid'
    assert not fake.calls and not store.path.exists()


def test_missing_prepared_credentials_has_no_refresh_fallback(tmp_path,monkeypatch):
    data,_,fake,store=context(tmp_path,monkeypatch)
    def unavailable(_):
        raise shopee.ShopeeOneClickPreDispatchError('fixture expired credentials')
    monkeypatch.setattr(shopee,'_prepare_transport',unavailable)
    status,result=server._round1_category_request('capture',data)
    assert status==409 and result['code']=='shopee_category_prepared_credentials_required'
    assert not fake.calls and not store.path.exists()


def test_no_unrequested_ttl_and_exact_single_freeze_read(tmp_path,monkeypatch):
    data,_,_,store=context(tmp_path,monkeypatch);receipt=capture(data)
    observed=store.round1_category_observation(receipt['observer_reference'])
    observed['observed_at']='2000-01-01T00:00:00+00:00'
    observed['observer_reference']='category-observation:old-synthetic-capture'
    store.persist_round1_category_observation(observed)
    assert resolve_record(store,observed['observer_reference'],review=observed['review_input'],
                          account_identity_digest=data['account_identity_digest'])==observed
    packet=deepcopy(observed['review_input'])
    packet.update(schema='publication-preparation-decision/v1',status='FIRST_REVIEW_READY',
                  image_execution_plan={'schema_version':'first-review-image-plan/v1','status':'PROPOSED'},
                  publication_stock_policy=default_publication_stock_policy())
    bound=build_receipt(observed)
    packet['category_evidence_binding']={'status':'BOUND','receipt':bound}
    packet['targets']=[{'target':target,'category':{'id':101,'name':bound['category']['name'],'receipt_digest':bound['receipt_digest']}}
                       for target in data['requested_targets']]
    calls=[];original=store.round1_category_observation
    def read(reference):calls.append(reference);return original(reference)
    monkeypatch.setattr(store,'round1_category_observation',read)
    build_round1_snapshot(first_review=packet,state=dict(_revision=8,review={'selected_sites':[]},
        product_approval=dict(status='approved',approved_by='Kyle',approval_id='fixture',input_fingerprint='fixture')),
        approved_by='Kyle',report_directory=tmp_path)
    assert calls==[observed['observer_reference']]


def test_capture_never_runs_unrelated_release_migrations(tmp_path,monkeypatch):
    data,_,_,store=context(tmp_path,monkeypatch)
    monkeypatch.setattr(store,'_ensure_schema',lambda _:pytest.fail('general schema/migration must not run'))
    receipt=capture(data)
    import sqlite3
    with sqlite3.connect(store.path) as connection:
        tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables=={'round1_category_observations','round1_category_observation_invalidations'}
    assert store.round1_category_observation(receipt['observer_reference']) is not None
