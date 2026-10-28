"""Real prepared R1/R2/paid, Git/HMAC chain and waiting-domain UNKNOWN.

Only the OS identity/key and provider transports are closed fixture seams.
No source verifier, native preparation reader or service._current is replaced.
"""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import hmac,json,sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest

from modules.products import server
from shared_platform import agent_prepared_publication_adoption as adoption
from shared_platform import agent_prepared_source_successor as source
from shared_platform import native_common_baseline_successor as successor
from shared_platform import native_common_technical_execution as technical
from shared_platform import manual_common_creation as manual,manual_common_successor as child
from shared_platform import publication_r3_image_bridge as bridge
from shared_platform.operations_runtime import runtime_manifest
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
from shared_platform.publication_paid_requests import load_paid_context
from test_native_common_service_facts import _setup,_installed,_closed_signed_transport
from test_native_common_retained_review_graph import _propose_market_inputs,_market
from test_service_common_observation import ClosedCommonTransport
from test_round1_workspace_freeze import live
from test_agent_prepared_source_successor import git
from test_manual_common_creation import intent
from test_manual_common_price_correction import insert_history


def _signed(db,table,record,key):
    raw=adoption._bytes(record);digest=sha256(raw).hexdigest()
    db.execute('CREATE TABLE IF NOT EXISTS '+table+' (task_id TEXT PRIMARY KEY,record_json TEXT NOT NULL,record_digest TEXT NOT NULL,signature TEXT NOT NULL)')
    db.execute('INSERT INTO '+table+' VALUES (?,?,?,?)',(record['task_id'],raw.decode(),digest,hmac.new(key,raw,'sha256').hexdigest()))
    return digest


@pytest.fixture
def real_chain(live,monkeypatch,tmp_path):
    _propose_market_inputs(live,monkeypatch)
    store,plan,payload,_=_setup(live,monkeypatch,tmp_path)
    _installed(monkeypatch,tmp_path,store)
    operations=server._NATIVE_FINAL_SERVICE._operations
    engine,old_profile=operations.engine,operations.profile
    task=engine.get(engine.dashboard()['tasks'][0]['task_id'])
    assert task['execution_state']=='waiting_domain' and task['worker'] is None
    root=old_profile.root;offer=payload['product_id'];sid='S-1-5-21-700';key=b'v'*32
    monkeypatch.setattr(adoption,'_owner_key',lambda *a,**kw:(sid,key))
    from shared_platform import r3_native_final_approval as actor
    monkeypatch.setattr(actor,'read_native_actor_identity',lambda _:dict(identity_verified=True,trust_mode='SAME_WINDOWS_USER',owner_sid=sid))
    # Ignore only untracked fixture data. Every source delta below is explicitly
    # force-added and the actual source_evidence checks a clean Git checkout.
    (root/'.git/info').mkdir(exist_ok=True)
    (root/'.git/info/exclude').write_text('*\n',encoding='utf-8')
    directory=root/'reports/product-preparation'/offer
    docs=bridge.load_r2_documents(offer);r1=docs['round1_snapshot']
    original_profile=root/'original-profile.json';original_profile.write_text('{}')
    pins={str((directory/name).relative_to(root)):sha256((directory/name).read_bytes()).hexdigest()
          for name in bridge.R2_DOCUMENTS.values()}
    for name in ('round1-auto-decision.json','paid-requests/events.jsonl'):
        pins[str((directory/name).relative_to(root))]=sha256((directory/name).read_bytes()).hexdigest()
    paid=load_paid_context(offer_id=offer,round1=r1,repo_root=root).summary()
    facts=dict(prepared_reference=payload['r3_stage_binding']['native_preparation_source']['prepared_reference'],
        snapshot_digest=r1['snapshot_digest'],r2_identity=bridge.validate_r2_identity(docs),
        preparation_source=payload['r3_stage_binding']['native_preparation_source'],
        source_identity_binding_sha256='b'*64,source_files=pins,paid_summary=paid,
        original_profile=dict(path=str(original_profile),sha256=sha256(original_profile.read_bytes()).hexdigest()),
        aliases={task['scope']['skus'][0]:payload['seller_sku']},internal_sku=task['scope']['skus'][0],seller_sku=payload['seller_sku'],
        business_history=dict(status='INITIALIZED_NO_LOCAL_BUSINESS_RUN_OR_APPROVAL',empty_business_history_verified=True))
    adoption.initialize_schema(engine,old_profile)
    with engine.transaction() as db:
        event=db.execute("SELECT id,created_at FROM workbench_events WHERE task_id=? AND event_type='workflow_created'",(task['task_id'],)).fetchone()
        original=dict(schema_version=adoption.SCHEMA,origin='AGENT_PREPARED_ADOPTION',task_id=task['task_id'],offer_id=offer,
            targets=task['scope']['shops'],release=task['version'],owner_sid=sid,task_created_event_id=event['id'],
            task_created_at=event['created_at'],facts=facts,marketplace_approval=False)
        raw=adoption._bytes(original);adoption_digest=sha256(raw).hexdigest()
        db.execute('INSERT INTO agent_prepared_publication_adoptions VALUES (?,?,?,?,?)',
            (task['task_id'],offer,raw.decode(),adoption_digest,hmac.new(key,raw,'sha256').hexdigest()))
        checkpoint=deepcopy(task['checkpoint']);checkpoint['native_r1_adoption']=dict(record_digest=adoption_digest,source_identity_binding_sha256='b'*64)
        db.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?',(adoption._bytes(checkpoint).decode(),task['task_id']))
    manual.initialize_schema(store.path);child.initialize_schema(store.path)
    value=intent();value.update(offer_id=offer,owner_sid=sid,seller_sku=payload['seller_sku'])
    value['create_body']['itemNum']=payload['seller_sku']
    value['create_body'].pop('price');parent_id='manual-common:'+manual._digest(value)
    insert_history(store.path,'manual_common_creations',parent_id,value,'UNKNOWN',1)
    parent=manual.ManualCommonCreateLedger(store.path).read(parent_id)
    def pin(name,value):
        path=root/name;path.write_text(json.dumps(value),encoding='utf-8');return path,sha256(path.read_bytes()).hexdigest()
    r15=dict(schema_version=child.DECISION_SCHEMA,action_class='AUTHORIZE_ONE_COMMON_CREATE_SUCCESSOR',actor='Kyle',
        maximum_new_create_attempts=1,combined_create_attempt_cap=2,original_unknown_retained=True,
        baseline_edit_attempt_cap_unchanged=1,same_task_and_preparation_root_required=True,
        candidate_content_changes_authorized=False,marketplace_approval=False,standing_authority=False)
    r15path,r15sha=pin('r15.json',r15)
    relation=dict(schema_version=child.CHILD_SCHEMA,parent_row_sha256=manual._digest(parent),task_id=task['task_id'],
        source_version=task['version'],source_chain_digest='c'*64,original_adoption_digest=adoption_digest,
        decision_path=str(r15path),decision_sha256=r15sha)
    child_id='manual-common-successor:'+manual._digest(relation)
    reply=dict(result='fail',message=child.KNOWN_REJECTION_REASON)
    response=dict(intent_id=child_id,provider_result='fail',provider_reason=child.KNOWN_REJECTION_REASON,
        zero_write_verified=False,returned_detail_id=None,response_sha256=manual._digest(reply),
        decoded_business_bytes_sha256=sha256(manual._bytes(reply)).hexdigest())
    insert_history(store.path,'manual_common_create_successors',child_id,value,'UNKNOWN',1,response=response,parent=parent_id,relation=relation)
    rejected=child.ManualCommonSuccessorLedger(store.path).read(child_id)
    r18={**r15,'schema_version':child.CORRECTION_SCHEMA,'decision_status':'USER_AUTHORIZED',
        'action_class':'AUTHORIZE_ONE_KNOWN_PRICE_REJECTION_CORRECTION','combined_create_attempt_cap':3,
        'child_unknown_and_response_retained':True}
    r18path,r18sha=pin('r18.json',r18)
    previous_version=task['version'];previous_digest=None;scope_digest=sha256(adoption._bytes(task['scope'])).hexdigest()
    file=root/'shared_platform/agent_prepared_common_service.py'
    for index in (1,2,3):
        file.write_text('# owned source revision '+str(index)+'\n',encoding='utf-8')
        git(root,'add','-f','shared_platform/agent_prepared_common_service.py');git(root,'commit','-m','source '+str(index))
        profile=replace(old_profile,version=git(root,'rev-parse','HEAD'),manifest_digest=runtime_manifest(root))
        schema=(source.SCHEMA,source.SECOND_SCHEMA,source.THIRD_SCHEMA)[index-1]
        table=('agent_prepared_source_successors','agent_prepared_source_successors_v2','agent_prepared_source_successors_v3')[index-1]
        record=dict(schema_version=schema,owner_sid=sid,task_id=task['task_id'],offer_id=offer,
            original_adoption_digest=adoption_digest,scope_digest=scope_digest,
            source=source.source_evidence(profile,previous_version,allowed_files=(source.ALLOWED_FILES,source.SECOND_ALLOWED_FILES,source.THIRD_ALLOWED_FILES)[index-1]))
        if index==2:record.update(previous_record_digest=previous_digest,original_intent_id=parent_id,
            original_manual_intent_sha256=manual._digest(parent),decision_path=str(r15path),decision_sha256=r15sha)
        if index==3:record.update(previous_record_digest=previous_digest,child_intent_id=child_id,
            rejected_child_sha256=manual._digest(rejected),decision_path=str(r18path),decision_sha256=r18sha)
        with engine.transaction() as db:
            digest=_signed(db,table,record,key)
            detail=dict(record_digest=digest,old_version=previous_version,new_version=source._version(profile))
            if index>1:detail['previous_record_digest']=previous_digest
            engine._event(db,task['task_id'],('source_code_successor','source_code_successor_v2','source_code_successor_v3')[index-1],detail)
        previous_version=source._version(profile);previous_digest=digest
    with engine.transaction() as db:db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',(adoption._bytes(previous_version).decode(),task['task_id']))
    # The known corrected CREATE is already VERIFIED before the old baseline,
    # exactly as production. Its rejected parent and original UNKNOWN remain.
    child.initialize_correction_schema(store.path)
    corrected=deepcopy(value);corrected['create_body']['price']=4.0
    correction_relation=dict(schema_version=child.CORRECTION_RELATION_SCHEMA,parent_row_sha256=manual._digest(rejected),
        task_id=task['task_id'],source_version=previous_version,source_chain_digest=previous_digest,
        original_adoption_digest=adoption_digest,create_body=corrected['create_body'],decision_path=str(r18path),decision_sha256=r18sha)
    correction_id='manual-common-correction:'+manual._digest(correction_relation)
    insert_history(store.path,'manual_common_create_corrections',correction_id,corrected,'UNKNOWN',1,
        parent=child_id,relation=correction_relation)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE manual_common_create_corrections SET state='VERIFIED',detail_id=?,returned_detail_id=? WHERE intent_id=?",(offer,offer,correction_id))
    payload=deepcopy(payload);payload['r3_stage_binding']['manual_common_binding']=dict(
        intent_id=correction_id,intent_digest=manual._digest(corrected),detail_id=offer)
    payload['plan_id']=bridge.common_stage_plan_id(payload,offer_id=offer)
    plan=store.create_plan(payload)
    with technical._existing_transaction(store) as db:
        transaction=technical._service_facts(store,db,plan['plan_id']);old=technical._reserve_in_transaction(store,db,transaction)
        technical._consume_in_transaction(db,transaction,old['run_id'])
    with store._connect_readonly() as db:
        snapshot=dict(run=dict(db.execute('SELECT * FROM release_runs').fetchone()),target=dict(db.execute('SELECT * FROM release_target_runs').fetchone()),
            release_target_submissions=[],release_target_failure_events=[],release_target_readbacks=[])
    decision=dict(schema_version=successor.DECISION_SCHEMA,decision_status='USER_AUTHORIZED',actor='Kyle',
        action_class='AUTHORIZE_ONE_COMMON_BASELINE_EDIT_SUCCESSOR',maximum_new_edit_attempts=1,combined_edit_attempt_cap=2,
        combined_create_attempt_cap=3,further_create_authorized=False,original_unknown_retained=True,original_outcome='UNKNOWN',
        zero_write_proven=False,same_task_and_preparation_root_required=True,candidate_content_changes_authorized=False,
        marketplace_approval=False,standing_authority=False,new_unknown_stops=True,task_id=task['task_id'],offer_id=offer,
        original_adoption_digest=adoption_digest,original_plan_id=plan['plan_id'],original_run_id=old['run_id'],
        original_payload_digest=plan['payload_digest'],original_ledger_sha256=successor._digest(snapshot),
        common_detail_id=offer,current_detail_sha256=successor._digest(ClosedCommonTransport.detail_for(payload)))
    decisionpath,decisionsha=pin('baseline-edit.json',decision);reader=successor.PinnedBaselineDecision(decisionpath,decisionsha)
    file.write_text('# owned source revision 4\n',encoding='utf-8')
    git(root,'add','-f','shared_platform/agent_prepared_common_service.py');git(root,'commit','-m','source 4')
    current=replace(old_profile,version=git(root,'rev-parse','HEAD'),manifest_digest=runtime_manifest(root))
    new_engine=WorkbenchEngine(current.data_root/'tasks.db',source._version(current))
    return SimpleNamespace(store=store,plan=plan,payload=payload,original=original,engine=new_engine,profile=current,
        task_id=task['task_id'],adoption_digest=adoption_digest,reader=reader,snapshot=snapshot,operations=operations)


def test_real_v4_hmac_git_waiting_domain_unknown_and_retained_graph(real_chain,monkeypatch):
    v=real_chain;before=v.engine.get(v.task_id)
    with v.engine.transaction() as db:
        old={table:[tuple(row) for row in db.execute('SELECT * FROM '+table)] for table in (
            'agent_prepared_publication_adoptions','agent_prepared_source_successors','agent_prepared_source_successors_v2','agent_prepared_source_successors_v3')}
    record=source.continue_fourth_source(v.engine,v.profile,v.store,task_id=v.task_id,
        expected_adoption_digest=v.adoption_digest,actor_reader=object(),decision_reader=v.reader)
    current=v.engine.get(v.task_id)
    assert current['checkpoint']==before['checkpoint'] and current['pending_observation']==before['pending_observation']
    assert current['execution_state']=='waiting_domain' and current['worker'] is None
    assert record['business_history_observation']['empty_business_history_verified'] is False
    assert v.original['facts']['business_history']['empty_business_history_verified'] is True
    assert source.continue_fourth_source(v.engine,v.profile,v.store,task_id=v.task_id,
        expected_adoption_digest=v.adoption_digest,actor_reader=object(),decision_reader=v.reader)==record
    with v.engine.transaction() as db:
        assert old=={table:[tuple(row) for row in db.execute('SELECT * FROM '+table)] for table in old}
        with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM agent_prepared_source_successors_v4')
    # Real current-source, adoption HMAC/events and native preparation readers.
    s=object.__new__(AgentPreparedCommonService);s.store=v.store;s.engine=v.engine;s.profile=v.profile;s.task_id=v.task_id;s.baseline_decision=v.reader
    v.operations.engine=v.engine;v.operations.profile=v.profile
    monkeypatch.setattr(server,'_AGENT_PREPARED_COMMON_SERVICE',s,raising=False)
    task,original=s._current()
    assert original==v.original and task['version']==source._version(v.profile)
    payload=successor.select_payload(v.store,v.payload);plan=v.store.create_plan(payload)
    calls=_closed_signed_transport(monkeypatch,v.store,payload)
    code,result=server._prepare_native_common_ledger(dict(offer_id=payload['product_id'],plan_id=plan['plan_id'],confirm_miaoshou_write=True),v.store)
    assert code==200 and result['native_run']['state']=='CONFIRMED_WRITE',result
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    with v.store._connect_readonly() as db:
        db.execute('BEGIN')
        reader=NativeCommonSourceReader(v.store)
        completed=reader.read_completed_baseline(db,plan['plan_id'],result['native_run']['run_id'])
        assert completed.status=='RETAINED_NATIVE_COMMON_BASELINE',completed
        assert dict(db.execute('SELECT * FROM release_runs WHERE run_id=?',(v.reader.read()['original_run_id'],)).fetchone())==v.snapshot['run']
    code,stages=server._publication_stages_for_request(dict(offer_id=payload['product_id']))
    assert code==200 and stages['common']['plan']['plan_id']==plan['plan_id'],stages
    assert stages['common']['status']=='RETAINED_TECHNICAL_BASELINE' and stages['common']['run']['run_id']==result['native_run']['run_id']
    assert calls.count('/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail')==1
