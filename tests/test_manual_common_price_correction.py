"""Actual missing-price defect and one fail-closed, receipt-bound correction."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import hashlib,json,sqlite3
import pytest
from shared_platform import manual_common_creation as manual
from shared_platform import manual_common_successor as child
from shared_platform import agent_prepared_source_successor as source
from shared_platform import agent_prepared_common_service as service
from shared_platform import agent_prepared_publication_adoption as adoption
from shared_platform.release_store import ReleaseStore
from shared_platform.workbench_engine import WorkbenchEngine
from test_manual_common_creation import intent
from test_agent_prepared_adoption import prepared,_adopt

def test_missing_no_sku_price_is_rejected_before_reservation(tmp_path):
    path=tmp_path/'release.db';manual.initialize_schema(path);ledger=manual.ManualCommonCreateLedger(path)
    value=intent();value['create_body'].pop('price')
    with pytest.raises(manual.ManualCommonBlocked,match='PRICE_REQUIRED'):ledger.reserve(value)
    assert ledger.original_for_offer(value['offer_id']) is None

@pytest.mark.parametrize('price',[None,True,0,-1,float('nan'),float('inf'),'4'])
def test_no_sku_price_must_be_finite_positive_json_number(price):
    with pytest.raises(manual.ManualCommonBlocked):
        manual.validate_create_body({'title':'frozen','itemNum':'2000431','price':price})

def insert_history(path,table,identity,value,state,attempts,*,response=None,parent=None,relation=None):
    row=[identity,value['offer_id'],value['preparation_root_id'],manual._bytes(value).decode(),manual._digest(value),
        state,attempts,None,None,'historical-created','historical-updated',None,manual._bytes(response).decode() if response else None]
    if parent is not None:row += [parent,manual._bytes(relation).decode(),manual._digest(relation)]
    with sqlite3.connect(path) as db:db.execute(f'INSERT INTO {table} VALUES ({",".join("?" for _ in row)})',row)

@pytest.fixture
def correction(prepared,monkeypatch):
    engine,old_profile,facts=prepared
    product=dict(title='Owned fridge cover',cost_cny='4',skus=[dict(seller_sku=m,cost_cny='4') for m in facts['aliases'].values()])
    facts['preparation_source']=dict(preparation_root={'root_id':'root:owned'},round1_snapshot={'fact_snapshot':{'product_facts':product}})
    result=_adopt(prepared)
    original=adoption.verify(engine.get(result['task_id']),old_profile,offer='8412287877065586543',targets=['shopee:PH'],
        prepared_reference=facts['prepared_reference'],snapshot_digest=facts['snapshot_digest'])
    store=ReleaseStore(old_profile.root/'release.db');manual.initialize_schema(store.path)
    value=intent();value.update(owner_sid=original['owner_sid'],round1_digest=facts['snapshot_digest'],
        source_identity_binding_sha256=facts['source_identity_binding_sha256'],target_labels=original['targets'],
        r2_digest='sha256:'+manual._digest(facts['r2_identity']),forbidden_detail_ids=[original['offer_id']])
    value['create_body'].pop('price')  # Historical2-key intent, never rewritten.
    parent_id='manual-common:'+manual._digest(value)
    insert_history(store.path,'manual_common_creations',parent_id,value,'RESERVED',0)
    monkeypatch.setattr(source,'source_evidence',lambda p,v,**kw:dict(old_version=v,new_version=source._version(p),changed_files={'synthetic':'digest'}))
    monkeypatch.setattr(source,'verify_historical_source',lambda *a,**kw:None)
    def profile(version):return SimpleNamespace(root=old_profile.root,data_root=old_profile.data_root,environment='stable',version=version,manifest_digest=version+'-manifest')
    first=profile('first');first_engine=WorkbenchEngine(first.data_root/'tasks.db',source._version(first))
    source.continue_source(first_engine,first,store,task_id=result['task_id'],expected_adoption_digest=result['adoption_digest'],actor_reader=object())
    with sqlite3.connect(store.path) as db:db.execute("UPDATE manual_common_creations SET state='UNKNOWN',attempts=1")
    parent=manual.ManualCommonCreateLedger(store.path).read(parent_id)
    pin=old_profile.root/'r15.json'
    r15=dict(schema_version=child.DECISION_SCHEMA,actor='Kyle',action_class='AUTHORIZE_ONE_COMMON_CREATE_SUCCESSOR',
        maximum_new_create_attempts=1,combined_create_attempt_cap=2,original_unknown_retained=True,baseline_edit_attempt_cap_unchanged=1,
        same_task_and_preparation_root_required=True,candidate_content_changes_authorized=False,marketplace_approval=False,standing_authority=False,
        task_id=result['task_id'],offer_id=original['offer_id'],original_intent_id=parent_id,original_adoption_digest=result['adoption_digest'],
        round1_snapshot_digest=value['round1_digest'],source_identity_binding_sha256=value['source_identity_binding_sha256'],
        seller_sku=value['seller_sku'],model_skus=value['model_skus'],canonical_skus=sorted(facts['aliases']),targets=value['target_labels'])
    pin.write_text(json.dumps(r15));reader=child.PinnedCommonSuccessorDecision(pin,hashlib.sha256(pin.read_bytes()).hexdigest())
    second=profile('second');second_engine=WorkbenchEngine(second.data_root/'tasks.db',source._version(second))
    source.continue_second_source(second_engine,second,store,task_id=result['task_id'],expected_adoption_digest=result['adoption_digest'],actor_reader=object(),decision_reader=reader)
    child.initialize_schema(store.path)
    with second_engine.transaction() as db:v2_digest=db.execute('SELECT record_digest FROM agent_prepared_source_successors_v2').fetchone()[0]
    relation=dict(schema_version=child.CHILD_SCHEMA,parent_row_sha256=manual._digest(parent),decision_path=str(pin),decision_sha256=reader.sha256,
        task_id=result['task_id'],source_version=source._version(second),source_chain_digest=v2_digest,original_adoption_digest=result['adoption_digest'])
    child_id='manual-common-successor:'+manual._digest(relation)
    reply=dict(result='fail',message=child.KNOWN_REJECTION_REASON)
    response=dict(intent_id=child_id,provider_result='fail',provider_reason=child.KNOWN_REJECTION_REASON,zero_write_verified=False,
        returned_detail_id=None,response_sha256=manual._digest(reply),decoded_business_bytes_sha256=hashlib.sha256(manual._bytes(reply)).hexdigest())
    insert_history(store.path,'manual_common_create_successors',child_id,value,'UNKNOWN',1,response=response,parent=parent_id,relation=relation)
    rejected=child.ManualCommonSuccessorLedger(store.path).read(child_id)
    decision={**r15,'schema_version':child.CORRECTION_SCHEMA,'decision_status':'USER_AUTHORIZED','action_class':'AUTHORIZE_ONE_KNOWN_PRICE_REJECTION_CORRECTION',
        'combined_create_attempt_cap':3,'child_unknown_and_response_retained':True,'child_intent_id':child_id,
        'child_response_sha256':response['response_sha256'],'child_decoded_business_bytes_sha256':response['decoded_business_bytes_sha256'],
        'corrected_create_body':service.frozen_create_body(product,value['seller_sku'])}
    decision['corrected_create_body_sha256']=manual._digest(decision['corrected_create_body'])
    correction_pin=old_profile.root/'correction-decision.json';correction_pin.write_text(json.dumps(decision))
    correction_reader=child.PinnedCommonCorrectionDecision(correction_pin,hashlib.sha256(correction_pin.read_bytes()).hexdigest())
    current=profile('third');current_engine=WorkbenchEngine(current.data_root/'tasks.db',source._version(current))
    from shared_platform import release_store
    monkeypatch.setattr(release_store,'default_release_store',lambda:store)
    from shared_platform import publication_common_standing_policy as policy
    from modules.products import server
    monkeypatch.setattr(server,'_service_common_standing_policy_reader',lambda:object())
    monkeypatch.setattr(policy,'inspect_service_policy',lambda _:SimpleNamespace(status='KNOWN_LOCAL_USER_INTENT',maximum_confirmed_writes=1))
    return SimpleNamespace(engine=current_engine,profile=current,store=store,result=result,original=original,parent=parent,
        rejected=rejected,reader=correction_reader,old_reader=reader,body=decision['corrected_create_body'])

def continue_source(v,reader=True):
    return source.continue_third_source(v.engine,v.profile,v.store,task_id=v.result['task_id'],expected_adoption_digest=v.result['adoption_digest'],
        actor_reader=object(),decision_reader=v.reader if reader else None)

def bound_service(v):
    s=object.__new__(service.AgentPreparedCommonService);s.profile=v.profile;s.store=v.store;s.ledger=manual.ManualCommonCreateLedger(v.store.path)
    s.successor=child.ManualCommonSuccessorLedger(v.store.path,decision_reader=v.old_reader)
    s.correction=child.ManualCommonCorrectionLedger(v.store.path,decision_reader=v.reader)
    return s

def test_new_receipt_required_and_v3_preserves_v1_v2_and_old_unknowns(correction):
    v=correction;before=v.engine.get(v.result['task_id'])
    with pytest.raises(adoption.AdoptionBlocked):continue_source(v,False)
    with v.engine.transaction() as db:
        old=[tuple(db.execute('SELECT * FROM '+t).fetchone()) for t in ('agent_prepared_publication_adoptions','agent_prepared_source_successors','agent_prepared_source_successors_v2')]
    record=continue_source(v);assert continue_source(v)==record
    assert v.engine.get(v.result['task_id'])['checkpoint']==before['checkpoint']
    child.initialize_correction_schema(v.store.path)
    task=v.engine.get(v.result['task_id']);s=bound_service(v)
    ledger,row=s._select_intent(task,v.original)
    assert row['intent']['create_body']==v.body and row['relation']['price_derivation'].startswith('FROZEN_R1_CNY_COST')
    with v.engine.transaction() as db:
        assert old==[tuple(db.execute('SELECT * FROM '+t).fetchone()) for t in ('agent_prepared_publication_adoptions','agent_prepared_source_successors','agent_prepared_source_successors_v2')]
        assert db.execute('SELECT count(*) FROM workbench_tasks').fetchone()[0]==1
    with pytest.raises(manual.ManualCommonBlocked):
        child.ManualCommonCorrectionLedger(v.store.path).reserve_correction(v.rejected,task=task,adoption_record=v.original,source_chain_digest='e'*64,create_body=v.body)

def test_sdk_corrected_price_dto_and_repeat_selection_after_verified(correction,monkeypatch):
    v=correction;continue_source(v);child.initialize_correction_schema(v.store.path)
    s=bound_service(v);task=v.engine.get(v.result['task_id']);ledger,row=s._select_intent(task,v.original);wire=[]
    from modules.miaoshou import client
    class Response:
        status=200;headers={}
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def geturl(self):return client.OPEN_BASE_URL+manual.CREATE_PATH
        def read(self):return b'{"result":"success","data":{"commonCollectBoxDetailId":9003}}'
    class Opener:
        def open(self,request,**kwargs):wire.append(json.loads(request.data));return Response()
    monkeypatch.setattr(client.urllib.request,'build_opener',lambda *a:Opener())
    monkeypatch.setattr(client,'_wait_for_open_slot',lambda:None)
    verified=ledger.create(row['intent_id'],post_create=lambda body:client.post_open(manual.CREATE_PATH,body,cfg={'app_id':'fixture','app_secret':'fixture-secret'}),
        read_detail=lambda i:dict(commonCollectBoxDetailId=i,itemNum='2000431'))
    assert wire==[dict(title='Owned fridge cover',itemNum='2000431',price=4.0)]
    assert type(wire[0]['price']) is float and verified['detail_id']=='9003'
    # Critical real path: later preview/commit/baseline/finalize selection must
    # use the original2-key intent, while effective binding uses correction.
    for _ in range(2):assert s._select_intent(task,v.original)[1]==verified
    assert adoption.verify(task,v.profile,offer=v.original['offer_id'],targets=v.original['targets'],
        prepared_reference=v.original['facts']['prepared_reference'],snapshot_digest=v.original['facts']['snapshot_digest'])==v.original
    binding={k:verified[k] for k in ('intent_id','intent_digest','detail_id')}
    assert manual.common_detail_id(dict(product_id=v.original['offer_id'],r3_stage_binding={'manual_common_binding':binding}),store=v.store)=='9003'
    assert s.ledger.read(v.parent['intent_id'])==v.parent and s.ledger.read(v.rejected['intent_id'])==v.rejected

def test_correction_unknown_spends_total_cap3_and_never_replays(correction):
    v=correction;continue_source(v);child.initialize_correction_schema(v.store.path)
    s=bound_service(v);task=v.engine.get(v.result['task_id']);ledger,row=s._select_intent(task,v.original);calls=[]
    def lost(body):calls.append(body);assert ledger.read(row['intent_id'])['state']=='UNKNOWN';raise TimeoutError()
    for _ in range(2):
        with pytest.raises(manual.ManualCommonBlocked):ledger.create(row['intent_id'],post_create=lost,read_detail=lambda _:None)
    assert len(calls)==1 and s._select_intent(task,v.original)[1]['attempts']==1
    with sqlite3.connect(v.store.path) as db:
        assert sum(db.execute('SELECT sum(attempts) FROM '+t).fetchone()[0] for t in ('manual_common_creations','manual_common_create_successors','manual_common_create_corrections'))==3
        with pytest.raises(sqlite3.IntegrityError):db.execute("UPDATE manual_common_create_successors SET updated_at='changed'")
