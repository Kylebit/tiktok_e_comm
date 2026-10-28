"""Bounded second CREATE uses synthetic ledgers and an offline SDK only."""
from copy import deepcopy
import json,sqlite3
from types import SimpleNamespace
import pytest
from shared_platform import manual_common_creation as manual
from shared_platform import manual_common_successor as child
from test_manual_common_creation import intent
from test_manual_common_sdk_response import sdk

@pytest.fixture
def recovery(tmp_path):
    path=tmp_path/'release.db';manual.initialize_schema(path);child.initialize_schema(path)
    parent=manual.ManualCommonCreateLedger(path);row=parent.reserve(intent())
    with pytest.raises(manual.ManualCommonBlocked):
        parent.create(row['intent_id'],post_create=lambda _:(_ for _ in ()).throw(TimeoutError()),read_detail=lambda _:None)
    row=parent.read(row['intent_id']);task={'task_id':'TASK-fixture','version':{'code_version':'new-source','manifest_digest':'new-manifest'}}
    record={'offer_id':row['offer_id'],'owner_sid':row['intent']['owner_sid'],'facts':{
        'snapshot_digest':row['intent']['round1_digest'],'source_identity_binding_sha256':row['intent']['source_identity_binding_sha256'],
        'aliases':dict(zip(['0431','0432','0433'],row['intent']['model_skus']))}}
    decision=dict(schema_version=child.DECISION_SCHEMA,action_class='AUTHORIZE_ONE_COMMON_CREATE_SUCCESSOR',actor='Kyle',
        offer_id=row['offer_id'],task_id=task['task_id'],original_intent_id=row['intent_id'],
        original_adoption_digest=manual._digest(record),round1_snapshot_digest=row['intent']['round1_digest'],
        source_identity_binding_sha256=row['intent']['source_identity_binding_sha256'],seller_sku='2000431',
        model_skus=row['intent']['model_skus'],canonical_skus=['0431','0432','0433'],targets=row['intent']['target_labels'],
        maximum_new_create_attempts=1,combined_create_attempt_cap=2,original_unknown_retained=True,
        baseline_edit_attempt_cap_unchanged=1,same_task_and_preparation_root_required=True,
        candidate_content_changes_authorized=False,marketplace_approval=False,standing_authority=False)
    pin=tmp_path/'decision.json';pin.write_text(json.dumps(decision),encoding='utf-8')
    reader=child.PinnedCommonSuccessorDecision(pin,manual.sha256(pin.read_bytes()).hexdigest())
    ledger=child.ManualCommonSuccessorLedger(path,decision_reader=reader)
    return parent,ledger,task,record,row,reader

def reserve(v):
    parent,ledger,task,record,row,_=v
    return ledger.reserve_successor(row,task=task,adoption_record=record,owner_sid=row['intent']['owner_sid'],source_chain_digest='e'*64)

def test_successor_durable_unknown_original_unchanged_and_no_third_create(recovery):
    parent,ledger,_,_,original,_=recovery;value=reserve(recovery);calls=[]
    def dropped(body):
        calls.append(body);assert ledger.read(value['intent_id'])['state']=='UNKNOWN'
        raise TimeoutError()
    for _ in range(2):
        with pytest.raises(manual.ManualCommonBlocked):ledger.create(value['intent_id'],post_create=dropped,read_detail=lambda _:None)
    assert len(calls)==1 and parent.read(original['intent_id'])==original
    assert reserve(recovery)['attempts']==1
    with sqlite3.connect(ledger.path) as db:
        assert db.execute('SELECT sum(attempts) FROM manual_common_creations').fetchone()[0]+db.execute('SELECT sum(attempts) FROM manual_common_create_successors').fetchone()[0]==2
        with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM manual_common_create_successors')
        with pytest.raises(sqlite3.IntegrityError):db.execute("UPDATE manual_common_creations SET updated_at='rewrite'")

def test_verified_child_is_effective_common_and_sdk_id_is_durable(recovery,monkeypatch):
    parent,ledger,_,_,original,_=recovery;value=reserve(recovery)
    reply={'result':'success','data':{'commonCollectBoxDetailId':9002}}
    with pytest.raises(manual.ManualCommonBlocked):
        ledger.create(value['intent_id'],post_create=sdk(monkeypatch,reply),read_detail=lambda _:(_ for _ in ()).throw(TimeoutError()))
    assert ledger.read(value['intent_id'])['returned_detail_id']=='9002'
    verified=ledger.reconcile_returned(value['intent_id'],read_detail=lambda i:{'commonCollectBoxDetailId':i,'itemNum':'2000431'})
    assert parent.for_offer(original['offer_id'])==verified
    payload={'product_id':original['offer_id'],'r3_stage_binding':{'manual_common_binding':{k:verified[k] for k in ('intent_id','intent_digest','detail_id')}}}
    assert manual.common_detail_id(payload,store=SimpleNamespace(path=ledger.path))=='9002'
    assert parent.read(original['intent_id'])==original

def test_sdk_rejection_retained_in_child_without_reset(recovery,monkeypatch):
    _,ledger,_,_,_,_=recovery;value=reserve(recovery)
    with pytest.raises(manual.ManualCommonBlocked):
        ledger.create(value['intent_id'],post_create=sdk(monkeypatch,{'result':'error','message':'Rejected fixture-secret-456'}),read_detail=lambda _:pytest.fail('read'))
    row=ledger.read(value['intent_id']);assert row['attempts']==1 and row['create_response']
    assert 'fixture-secret-456' not in json.dumps(row)

@pytest.mark.parametrize('changed',['pin','task','owner','adoption','parent','caller_reader'])
def test_successor_rejects_scope_actor_receipt_or_parent_changes(recovery,changed):
    parent,ledger,task,record,row,reader=recovery
    if changed=='pin':reader.path.write_text('{}')
    elif changed=='task':task['task_id']='TASK-other'
    elif changed=='owner':row=deepcopy(row);row['intent']['owner_sid']='S-1-other'
    elif changed=='adoption':record['offer_id']='other'
    elif changed=='parent':row=deepcopy(row);row['state']='RESERVED'
    else:ledger.decision_reader={'approved':True}
    with pytest.raises(manual.ManualCommonBlocked):
        ledger.reserve_successor(row,task=task,adoption_record=record,owner_sid=row['intent']['owner_sid'],source_chain_digest='e'*64)

def test_constructor_does_not_migrate(tmp_path):
    path=tmp_path/'absent.db';child.ManualCommonSuccessorLedger(path)
    assert not path.exists()

def test_found_original_is_observed_and_never_creates_child(recovery):
    from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
    parent,ledger,task,record,original,_=recovery
    service=object.__new__(AgentPreparedCommonService);service.ledger=parent;service.successor=ledger
    service.correction=None
    service._current=lambda:(task,record);service._actor=lambda:None
    service._source_chain=lambda _: 'e'*64
    service._intent=lambda *a:original['intent'];service._token=lambda _: 'bound'
    service._scan_common=lambda _:[{'commonCollectBoxDetailId':'9000','itemNum':'2000431'}]
    service._post=lambda *a:pytest.fail('must not CREATE')
    _,preview=service.preview({'offer_id':original['offer_id']})
    value=preview['common']['plan']
    with pytest.raises(manual.ManualCommonBlocked,match='RECONCILIATION:9000'):
        service.commit(dict(offer_id=original['offer_id'],release_stage='R3_COMMON',publication_targets=['miaoshou:COMMON'],
            plan_id=value['plan_id'],confirmation_token='bound',confirm_miaoshou_write=True))
    assert ledger.read(value['plan_id'])['attempts']==0 and parent.read(original['intent_id'])==original
    with sqlite3.connect(ledger.path) as db:
        observation=json.loads(db.execute('SELECT observation_json FROM manual_common_successor_observations').fetchone()[0])
    assert observation['detail_ids']==['9000'] and len(observation['readback_sha256'])==64
