"""New source only: old chain, actor and business parser are named offline boundaries."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import hmac,json,sqlite3
import pytest
from shared_platform import agent_prepared_source_successor as source,agent_prepared_publication_adoption as adoption
from shared_platform.operations_runtime import RuntimeProfile,runtime_manifest
from test_approved_native_r1_reader_source import git,pin
from test_native_ozon_shopee_existing_items_source import history as completion_history
from test_native_ozon_shopee_existing_items_source import regional_history,locale_history,stock_history,platform_history


@pytest.fixture
def segments(tmp_path):
    root=tmp_path/'discount';root.mkdir();git(root,'init')
    for name in source.COMBINED_COMPLETION_MECHANISM_FILES|source.COMBINED_COMPLETION_FILES|source.B0431_DISCOUNT_MECHANISM_FILES|source.B0431_DISCOUNT_FILES:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('original\n')
    def version():return dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    def commit(files,message):
        for name in files:(root/name).write_text(message+'\n')
        git(root,'add','.');git(root,'commit','-m',message);return version()
    git(root,'add','.');git(root,'commit','-m','original');old=version()
    previous_mechanism=commit(source.COMBINED_COMPLETION_MECHANISM_FILES,'old completion mechanism')
    previous_final=commit(source.COMBINED_COMPLETION_FILES,'old completion execution')
    prior_profile=RuntimeProfile(root,tmp_path/'ops','stable',previous_final['code_version'],previous_final['manifest_digest'])
    prior=source._combined_completion_segments(prior_profile,old,previous_mechanism)
    mechanism=commit(source.B0431_DISCOUNT_MECHANISM_FILES,'discount source')
    final=commit(source.B0431_DISCOUNT_FILES,'discount consumer')
    return root,old,prior,previous_final,mechanism,RuntimeProfile(root,tmp_path/'ops','stable',final['code_version'],final['manifest_digest'])


@pytest.fixture
def history(completion_history,tmp_path,monkeypatch):
    store,frozen,request,preimage,_,_,_=completion_history
    image=json.loads(Path(preimage['path']).read_bytes());intent=json.loads(Path(request['path']).read_bytes())
    old=dict(run_preimage=preimage,frozen_candidate=frozen,source_segments=dict(execution=dict(new_version=dict(code_version=image['source_head']))))
    with sqlite3.connect(store.path) as db:
        for row in image['native_final_budget_reservations']:
            key=(row['scope_key'],row['sequence'])
            if not db.execute('SELECT 1 FROM native_final_budget_dispatches WHERE scope_key=? AND sequence=?',key).fetchone():
                db.execute('INSERT INTO native_final_budget_dispatches VALUES(?,?,?)',(*key,'original-settled-before-discount'))
                db.execute('INSERT INTO native_final_budget_outcomes VALUES(?,?,?)',(*key,'ACCEPTED'))
        db.row_factory=sqlite3.Row
        for table in ('native_final_budget_dispatches','native_final_budget_outcomes'):image[table]=[dict(r) for r in db.execute('SELECT * FROM '+table)]
    from shared_platform.shopee_publication_checkpoint import _digest
    labels=[label for label in frozen['ordered_targets'] if label.startswith('shopee:')]
    expected=dict(item_ids={label:str(i+100) for i,label in enumerate(labels)},model_ids={label:{'200043'+str(i):str(i) for i in (1,2,3)} for label in labels})
    old['shopee_existing_item_completion_ref']=pin(tmp_path/'old-completion-proof.json',expected)
    image['shopee_checkpoint'].update(offer_id=intent['offer_id'],run_id='native-files',report_id='native-report')
    cp=Path(image['shopee_checkpoint_ref']['path']);image['shopee_checkpoint_ref']=pin(cp,image['shopee_checkpoint'])
    image['shopee_scoped_checkpoints']={}
    for label in labels:
        body=dict(schema_version='shopee-publication-checkpoint/v1',platform='SHOPEE',offer_id=intent['offer_id'],plan_id=frozen['plan_id'],
            run_id='native-files',report_id='native-report',revision=image['tiktok_checkpoint']['product_revision'],snapshot_digest=image['tiktok_checkpoint']['snapshot_digest'],
            target_labels=[label],events=[dict(target_label=label,stage='READBACK',outcome='PUBLISHED')],
            targets={label:dict(provider_item_id=expected['item_ids'][label],provider_model_ids=['1','2','3'])})
        body['checkpoint_digest']=_digest(body)
        cp=Path(image['shopee_checkpoint_ref']['path']).with_name('shopee-publication-checkpoint.'+label.split(':')[1]+'.json')
        image['shopee_scoped_checkpoints'][label]=dict(ref=pin(cp,body),body=body)
    encoded=adoption._bytes(dict(schema='offline-original-VN-failure4',target_label='shopee:VN')).decode()
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE release_target_runs SET status='FAILED',attempts=4 WHERE target_label='shopee:VN'")
        db.execute('INSERT INTO release_target_failure_events VALUES(?,?,?,?,?,?)',(image['run']['run_id'],'shopee:VN',4,encoded,sha256(encoded.encode()).hexdigest(),'original-failed4'))
        db.row_factory=sqlite3.Row
        for table in ('release_target_runs','release_target_failure_events'):image[table]=[dict(row) for row in db.execute('SELECT * FROM '+table)]
    preimage=pin(tmp_path/'post-completion.json',image)
    decision=pin(tmp_path/'decision.json',dict(task_id='offline-task',offer_id=intent['offer_id'],plan_id=frozen['plan_id'],candidate_digest=frozen['candidate_digest'],payload_digest=frozen['payload_digest'],original_decision_id=intent['decision_id'],original_review_digest=frozen['native_review_digest'],targets={'shopee:VN':{'existing_item_id':expected['item_ids']['shopee:VN']}}))
    targets=['promotion:'+label for label in frozen['ordered_targets'] if label.startswith(('tiktok:','shopee:'))]
    intent.update(schema='original-approved-b0431-companion-discount-source-request/v1',b0431_discount_decision_ref=decision,retained_ref=preimage,original_companion_action_targets=targets,
        limits=dict(source_event_max=1,original_companion_actions=8,per_action_maximum=1,tiktok_percent_off=35,shopee_percent_off=30),canonical_publication_gate_required=True,genuine_current_quote_required=True,
        new_publication_budget=False,new_activity=False,source_event_is_discount_authority=False,declare_publication_succeeded=False,replay_unknown=False,
        failed_shopee_readback_labels=['shopee:VN'],failed_shopee_readback_maximum=1,failed_shopee_readback_merchant_writes=0,failed_shopee_readback_new_attempts=0,failed_shopee_readback_new_budget=0)
    from shared_platform import native_approved_discount_prices as prices
    monkeypatch.setattr(prices,'read_b0431_discount_decision',source._r1_reader_pin,raising=False)
    return store,frozen,pin(tmp_path/'request.json',intent),preimage,decision,old


def test_exact_git_segments_preserve_old_scope_and_reject_outside_file(segments):
    root,old,prior,version,mechanism,profile=segments
    assert source._historical_combined_completion_segments(profile,old,prior)==prior
    assert set(source._b0431_discount_segments(profile,version,mechanism)['execution']['changed_files'])==source.B0431_DISCOUNT_FILES
    with pytest.raises(adoption.AdoptionBlocked):source._combined_completion_segments(profile,old,prior['mechanism']['new_version'])
    (root/'outside.py').write_text('outside');git(root,'add','.');git(root,'commit','-m','outside')
    current=RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))
    with pytest.raises(adoption.AdoptionBlocked):source._b0431_discount_segments(current,version,mechanism)


def test_history_cas_unknown_and_no_source_grant_or_false_publication(history):
    store,frozen,request,image_ref,decision,old=history
    value=source._b0431_discount_history(store,'offline-task',frozen,request,image_ref,decision,old,before_append=True)
    intent=json.loads(Path(request['path']).read_bytes())
    for change in ({'declare_publication_succeeded':True},{'source_event_is_discount_authority':True},{'new_activity':True},{'limits':dict(intent['limits'],shopee_percent_off=35)}):
        bad=pin(Path(request['path']).with_name('bad.json'),dict(intent,**change))
        with pytest.raises(adoption.AdoptionBlocked,match='EXACT_HISTORY_PINS'):source._b0431_discount_history(store,'offline-task',frozen,bad,image_ref,decision,old)
    image=json.loads(Path(image_ref['path']).read_bytes());broken=deepcopy(image);broken['native_final_budget_outcomes'].pop()
    unknown=pin(Path(image_ref['path']).with_name('unknown.json'),broken)
    with pytest.raises(adoption.AdoptionBlocked,match='UNRECONCILED_MUTATION'):source._b0431_discount_history(store,'offline-task',frozen,pin(Path(request['path']).with_name('unknown-request.json'),dict(intent,retained_ref=unknown)),unknown,decision,old)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE release_target_runs SET status='RUNNING',attempts=6 WHERE target_label='shopee:MY'")
        db.execute("INSERT INTO native_final_budget_reservations VALUES('sh',13,'shopee:MY','list_regional_item',1,'target','UNKNOWN')")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('sh',13,'later-original-effect')")
    assert source._b0431_discount_history(store,'offline-task',frozen,request,image_ref,decision,old)==value
    with pytest.raises(adoption.AdoptionBlocked,match='HISTORY_CAS_CHANGED'):source._b0431_discount_history(store,'offline-task',frozen,request,image_ref,decision,old,before_append=True)


def test_hmac_before_historical_and_explicit_cli_pins(tmp_path,monkeypatch):
    calls=[];key=b'offline';sid='owner'
    monkeypatch.setattr(source,'_verify_combined_completion_source',lambda *a,**k:calls.append(k))
    record=dict(schema_version=source.B0431_DISCOUNT_SCHEMA,owner_sid=sid,purpose='wrong');raw=adoption._bytes(record)
    envelope=dict(record_json=raw.decode(),record_digest=sha256(raw).hexdigest(),signature=hmac.new(key,raw,'sha256').hexdigest())
    with pytest.raises(adoption.AdoptionBlocked,match='PURPOSE_REQUIRED'):source._verify_b0431_discount_source({},None,{},connection=None,owner_sid=sid,key=key,events=[],rows=[(json.dumps(envelope),)])
    envelope['signature']='0'*64
    with pytest.raises(adoption.AdoptionBlocked,match='SIGNED_BINDING'):source._verify_b0431_discount_source({},None,{},connection=None,owner_sid=sid,key=key,events=[],rows=[(json.dumps(envelope),)])
    assert not calls
    from scripts.continue_agent_prepared_source import main
    path=tmp_path/'config.json';path.write_text('{}');base=['--deployment',str(path),'--receipt',str(tmp_path/'unused')]
    with pytest.raises(ValueError,match='B0431_DISCOUNT_SOURCE_EXACT_HISTORY_PINS'):main(base+['--approved-b0431-companion-discount-continuation'])
    full=['--approved-b0431-companion-discount-continuation']
    for option in ('mechanism-head','mechanism-manifest','technical-request','technical-request-sha256','run-preimage','run-preimage-sha256','decision','decision-sha256'):full+=['--discount-'+option,'offline']
    with pytest.raises(ValueError,match='SOURCE_SUCCESSOR_EXACT_SOURCE_ACTION_REQUIRED'):main(base+full+['--common-create-successor'])


def test_one_signed_append_current_task_events_CAS_and_original_rows_preserved(segments,history,monkeypatch):
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform import r3_native_final_approval as actor,release_store
    root,_,_,version,mechanism,profile=segments;store,frozen,request,preimage,decision,old=history;sid='owner';key=b'offline'
    engine=WorkbenchEngine(profile.data_root/'tasks.db',version)
    task_id=engine.create(dict(template='publication',source_key='discount',scope=dict(offer_id='8412287877065586543',shops=frozen['ordered_targets'],skus=['0431'])))['task_id']
    image=json.loads(Path(preimage['path']).read_bytes());image['source_head']=version['code_version'];preimage=pin(Path(preimage['path']),image)
    doc=json.loads(Path(decision['path']).read_bytes());doc['task_id']=task_id;decision=pin(Path(decision['path']),doc)
    intent=json.loads(Path(request['path']).read_bytes());intent.update(task_id=task_id,source_head=version['code_version'],b0431_discount_decision_ref=decision,retained_ref=preimage);request=pin(Path(request['path']),intent)
    old['source_segments']['execution']['new_version']=version;old.update(schema_version=source.COMBINED_COMPLETION_SCHEMA,owner_sid=sid,scope_digest='same')
    original=dict(schema_version=adoption.SCHEMA,owner_sid=sid,task_id=task_id,offer_id='8412287877065586543');raw=adoption._bytes(original);digest=sha256(raw).hexdigest();encoded=adoption._bytes(old)
    with engine.transaction() as db:
        db.execute('CREATE TABLE agent_prepared_publication_adoptions(task_id TEXT,record_json TEXT,record_digest TEXT,signature TEXT)')
        db.execute('INSERT INTO agent_prepared_publication_adoptions VALUES(?,?,?,?)',(task_id,raw.decode(),digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute("UPDATE workbench_execution SET state='reconciliation_required',step_index=2,action_json=?,checkpoint_json=? WHERE task_id=?",('{"old":"action"}','{"old":"checkpoint"}',task_id))
        engine._event(db,task_id,source.COMBINED_COMPLETION_EVENT,dict(record_json=encoded.decode(),record_digest=sha256(encoded).hexdigest(),signature=hmac.new(key,encoded,'sha256').hexdigest()))
        before=dict(engine._row(db,task_id))
    monkeypatch.setattr(adoption,'_owner_key',lambda _:(sid,key));monkeypatch.setattr(actor,'read_native_actor_identity',lambda _:dict(identity_verified=True,trust_mode='SAME_WINDOWS_USER',owner_sid=sid))
    monkeypatch.setattr(source,'_verify_combined_completion_source',lambda *a,**k:dict(old='signed prior chain boundary'))
    monkeypatch.setattr(release_store,'default_release_store',lambda:store)
    kwargs=dict(task_id=task_id,expected_adoption_digest=digest,actor_reader=object(),mechanism_head=mechanism['code_version'],mechanism_manifest=mechanism['manifest_digest'],technical_request=request,run_preimage=preimage,b0431_discount_decision_ref=decision)
    reader=source._b0431_discount_history
    def race(*a,**k):
        with engine.transaction() as db:engine._event(db,task_id,'current_progress',dict(preserved=True))
        return reader(*a,**k)
    monkeypatch.setattr(source,'_b0431_discount_history',race)
    with pytest.raises(adoption.AdoptionBlocked,match='CAS_CHANGED'):source.continue_b0431_discount_source(engine,profile,store,**kwargs)
    monkeypatch.setattr(source,'_b0431_discount_history',reader)
    with engine.transaction() as db:events=[tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id')]
    release_before=sha256(store.path.read_bytes()).hexdigest();source.continue_b0431_discount_source(engine,profile,store,**kwargs)
    assert sha256(store.path.read_bytes()).hexdigest()==release_before
    with engine.transaction() as db:
        after=dict(engine._row(db,task_id));assert {k:v for k,v in before.items() if k!='version_json'}=={k:v for k,v in after.items() if k!='version_json'}
        assert [tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id')][:-1]==events
        task=engine._project(db,engine._row(db,task_id));rows=db.execute('SELECT detail_json FROM workbench_events WHERE event_type=?',(source.B0431_DISCOUNT_EVENT,)).fetchall()
        assert source._verify_b0431_discount_source(task,profile,original,connection=db,owner_sid=sid,key=key,events=[],rows=rows)==dict(old='signed prior chain boundary')
    with pytest.raises(adoption.AdoptionBlocked,match='SINGLE_EVENT'):source.continue_b0431_discount_source(engine,profile,store,**kwargs)


def test_four_scoped_checkpoint_digest_CAS_and_permanent_prefix(history):
    from shared_platform.shopee_publication_checkpoint import _digest
    store,frozen,request,image_ref,decision,old=history;image=json.loads(Path(image_ref['path']).read_bytes())
    source._verify_b0431_shopee_checkpoints(image,frozen,old,before_append=True)
    missing=deepcopy(image);missing['shopee_scoped_checkpoints'].pop('shopee:MY')
    with pytest.raises(adoption.AdoptionBlocked,match='FOUR_SCOPED_CHECKPOINTS'):source._verify_b0431_shopee_checkpoints(missing,frozen,old)
    entry=image['shopee_scoped_checkpoints']['shopee:MY'];path=Path(entry['ref']['path']);body=deepcopy(entry['body'])
    body['events'].append(dict(target_label='shopee:MY',stage='READBACK',outcome='PUBLISHED'))
    body['checkpoint_digest']=_digest({k:v for k,v in body.items() if k!='checkpoint_digest'});pin(path,body)
    source._verify_b0431_shopee_checkpoints(image,frozen,old)
    with pytest.raises(adoption.AdoptionBlocked,match='SCOPED_CHECKPOINT_CAS_CHANGED'):source._verify_b0431_shopee_checkpoints(image,frozen,old,before_append=True)
    body['events'][0]['outcome']='changed';body['checkpoint_digest']=_digest({k:v for k,v in body.items() if k!='checkpoint_digest'});pin(path,body)
    with pytest.raises(adoption.AdoptionBlocked,match='SCOPED_CHECKPOINT_PREFIX_CHANGED'):source._verify_b0431_shopee_checkpoints(image,frozen,old)


def test_vn_getonly_scope_is_original_failed4_and_later_canonical_append_is_allowed(history):
    store,frozen,request,image_ref,decision,old=history
    value=source._b0431_discount_history(store,'offline-task',frozen,request,image_ref,decision,old,before_append=True)
    assert value['vn_get_only_target']=='shopee:VN'
    image=json.loads(Path(image_ref['path']).read_bytes());intent=json.loads(Path(request['path']).read_bytes())
    for change in ({'failed_shopee_readback_labels':['shopee:MY']},{'failed_shopee_readback_maximum':2},{'failed_shopee_readback_maximum':True},{'failed_shopee_readback_merchant_writes':1},{'failed_shopee_readback_new_attempts':1},{'failed_shopee_readback_new_budget':True}):
        with pytest.raises(adoption.AdoptionBlocked,match='EXACT_HISTORY_PINS'):
            source._b0431_discount_history(store,'offline-task',frozen,pin(Path(request['path']).with_name('wrong-vn-scope.json'),dict(intent,**change)),image_ref,decision,old)
    broken=deepcopy(image);next(r for r in broken['release_target_runs'] if r['target_label']=='shopee:VN')['attempts']=5
    altered=pin(Path(image_ref['path']).with_name('not-original-attempt4.json'),broken)
    wrong=pin(Path(request['path']).with_name('wrong-vn-image.json'),dict(intent,retained_ref=altered))
    with pytest.raises(adoption.AdoptionBlocked,match='ORIGINAL_VN_FAILED4'):source._b0431_discount_history(store,'offline-task',frozen,wrong,altered,decision,old)
    with sqlite3.connect(store.path) as db:db.execute("UPDATE release_target_runs SET status='SUCCEEDED' WHERE target_label='shopee:VN'")
    assert source._b0431_discount_history(store,'offline-task',frozen,request,image_ref,decision,old)==value
    with pytest.raises(adoption.AdoptionBlocked,match='HISTORY_CAS_CHANGED'):source._b0431_discount_history(store,'offline-task',frozen,request,image_ref,decision,old,before_append=True)
