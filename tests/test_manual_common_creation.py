"""Durable CREATE ownership at a closed transport seam, never real credentials."""
from copy import deepcopy
import json
import sqlite3
import pytest

from shared_platform import manual_common_creation as manual


@pytest.fixture
def ledger(tmp_path):
    path=tmp_path/'release.db'
    manual.initialize_schema(path)
    return manual.ManualCommonCreateLedger(path)


def intent():
    return dict(schema_version='manual-common-create-intent/v1',offer_id='8412287877065586543',
        preparation_root_id='root:owned',round1_digest='sha256:'+'a'*64,r2_digest='sha256:'+'b'*64,
        source_identity_binding_sha256='c'*64,owner_sid='S-1-5-21-1',
        seller_sku='2000431',model_skus=['2000431','2000432','2000433'],
        target_labels=['shopee:PH','tiktok:LH_PH'],forbidden_detail_ids=['40831680584'],
        create_body={'title':'Owned fridge cover','itemNum':'2000431','price':4.0},
        authority='CURRENT_NEW_PRODUCT_PREPARATION',maximum_create_attempts=1,maximum_baseline_edits=1)


def test_unknown_is_durable_before_create_and_cannot_be_replayed(ledger):
    value=ledger.reserve(intent());calls=[]
    def dropped(body):
        calls.append(body)
        assert ledger.read(value['intent_id'])['state']=='UNKNOWN'
        raise TimeoutError('owned lost response')
    with pytest.raises(manual.ManualCommonBlocked,match='UNKNOWN'):
        ledger.create(value['intent_id'],post_create=dropped,read_detail=lambda _:pytest.fail('no returned identity'))
    with pytest.raises(manual.ManualCommonBlocked,match='RECONCILIATION'):
        ledger.create(value['intent_id'],post_create=dropped,read_detail=lambda _:None)
    assert len(calls)==1 and ledger.read(value['intent_id'])['attempts']==1


def test_verified_returned_id_is_unique_and_original_a_is_never_adopted(ledger):
    value=ledger.reserve(intent());calls=[]
    def create(body):calls.append(body);return {'result':'success','data':{'commonCollectBoxDetailId':9001}}
    result=ledger.create(value['intent_id'],post_create=create,
        read_detail=lambda i:{'commonCollectBoxDetailId':i,'itemNum':'2000431'})
    assert result['state']=='VERIFIED' and result['detail_id']=='9001'
    assert ledger.create(value['intent_id'],post_create=lambda _:pytest.fail('repeat'),read_detail=lambda _:None)==result
    changed=intent();changed['preparation_root_id']='other-root'
    with pytest.raises(manual.ManualCommonBlocked,match='EXISTING_PRODUCT'):
        ledger.reserve(changed)
    assert len(calls)==1


def test_wrong_id_or_wrong_seller_remains_unknown_and_never_writes_again(ledger):
    value=ledger.reserve(intent())
    with pytest.raises(manual.ManualCommonBlocked,match='IDENTITY'):
        ledger.create(value['intent_id'],post_create=lambda _: {'result':'success','data':{'commonCollectBoxDetailId':40831680584}},
            read_detail=lambda _:pytest.fail('must reject A before detail read'))
    assert ledger.read(value['intent_id'])['state']=='UNKNOWN'


def test_reconciliation_only_adopts_one_exact_complete_official_candidate(ledger):
    value=ledger.reserve(intent())
    with pytest.raises(manual.ManualCommonBlocked):
        ledger.create(value['intent_id'],post_create=lambda _: (_ for _ in ()).throw(TimeoutError()),read_detail=lambda _:None)
    with pytest.raises(manual.ManualCommonBlocked,match='UNIQUE_COMPLETE'):
        ledger.reconcile(value['intent_id'],candidates=[{'commonCollectBoxDetailId':9001,'itemNum':'2000431'}],complete=False)
    result=ledger.reconcile(value['intent_id'],candidates=[{'commonCollectBoxDetailId':9001,'itemNum':'2000431'}],complete=True)
    assert result['state']=='VERIFIED' and result['attempts']==1
    with sqlite3.connect(ledger.path) as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM manual_common_creations')


def test_changed_payload_or_scope_does_not_reset_budget(ledger):
    value=ledger.reserve(intent())
    changed=intent();changed['create_body']['title']='changed'
    with pytest.raises(manual.ManualCommonBlocked,match='CONFLICT'):ledger.reserve(changed)
    changed=intent();changed['maximum_create_attempts']=2
    with pytest.raises(manual.ManualCommonBlocked,match='CAP'):ledger.reserve(changed)
    assert ledger.read(value['intent_id'])['attempts']==0


def test_manual_internal_offer_cannot_be_used_as_provider_common_id():
    with pytest.raises(manual.ManualCommonBlocked,match='PROVIDER_BINDING_REQUIRED'):
        manual.common_detail_id({'product_id':'8412287877065586543',
            'source_product_identity':{'source_authority':'manual-intake'}})

def test_returned_create_id_survives_get_failure_and_recovers_only_that_id(ledger):
    value=ledger.reserve(intent());posts=[]
    def accepted(body):
        posts.append(body)
        return {'result':'success','data':{'commonCollectBoxDetailId':9001}}
    def lost_read(identity):
        assert identity=='9001'
        raise TimeoutError('closed GET failed after accepted POST')
    with pytest.raises(manual.ManualCommonBlocked):
        ledger.create(value['intent_id'],post_create=accepted,read_detail=lost_read)
    original=ledger.read(value['intent_id'])
    assert original['state']=='UNKNOWN' and original['returned_detail_id']=='9001'
    assert original['create_response']['intent_id']==value['intent_id']
    assert len(original['create_response']['response_sha256'])==64
    # A new process instance reads the durable original response identity.
    restarted=manual.ManualCommonCreateLedger(ledger.path)
    with pytest.raises(manual.ManualCommonBlocked,match='ID_CONFLICT'):
        restarted.reconcile_returned(value['intent_id'],read_detail=lambda _: {'commonCollectBoxDetailId':'9002','itemNum':'2000431'})
    assert restarted.read(value['intent_id'])['returned_detail_id']=='9001'
    with sqlite3.connect(ledger.path) as db:
        observed=json.loads(db.execute('SELECT observation_json FROM manual_common_creation_observations').fetchone()[0])
    assert observed['returned_detail_id']=='9001' and observed['observed_detail_id']=='9002'
    def exact_read(identity):
        assert identity=='9001'
        return {'commonCollectBoxDetailId':identity,'itemNum':'2000431'}
    result=restarted.reconcile_returned(value['intent_id'],read_detail=exact_read)
    assert result['state']=='VERIFIED' and result['detail_id']=='9001' and len(posts)==1
