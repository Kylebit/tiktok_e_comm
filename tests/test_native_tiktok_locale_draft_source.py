"""Source/CAS tests; the business proof reader is a named independent boundary."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import hmac,json,sqlite3,sys
import pytest
from shared_platform import agent_prepared_source_successor as source,agent_prepared_publication_adoption as adoption
from shared_platform.operations_runtime import RuntimeProfile,runtime_manifest
from test_approved_native_r1_reader_source import git,pin
from test_approved_platform_source import history as platform_history
from test_native_stock_category_source import history as stock_history,segments as stock_segments


@pytest.fixture
def segments(tmp_path):
    root=tmp_path/'locale';root.mkdir();git(root,'init')
    for name in source.LOCALE_DRAFT_MECHANISM_FILES|source.LOCALE_DRAFT_FILES:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('old\n')
    git(root,'add','.');git(root,'commit','-m','original stock source')
    old=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.LOCALE_DRAFT_MECHANISM_FILES:(root/name).write_text('bounded source mechanism\n')
    git(root,'add','.');git(root,'commit','-m','mechanism')
    mechanism=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.LOCALE_DRAFT_FILES:(root/name).write_text('localized correction\n')
    git(root,'add','.');git(root,'commit','-m','business correction')
    return root,old,mechanism,RuntimeProfile(root,tmp_path/'ops','stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))


@pytest.fixture
def history(stock_history,tmp_path,monkeypatch):
    store,frozen,request,preimage=stock_history
    intent=json.loads(Path(request['path']).read_bytes());image=json.loads(Path(preimage['path']).read_bytes())
    cp=image['tiktok_checkpoint'];cp.pop('checkpoint_digest');slots=[]
    for n,label in enumerate(sorted(source.NATIVE_RECOVERY_TARGETS)):
        original=next(r for r in cp['events'] if r['target_label']==label)
        cp['events'].append(dict(original,operation='SAVE_DRAFT'))
        slots.append(dict(scope_key='tk',sequence=10+2*n,target_label=label,detail_id=original['detail_id'],shop_id=original['shop_id'],saved_sequence=9+2*n,failure_evidence_digest='failure3-'+label))
    cp['external_write_count']=12;cp['checkpoint_digest']='sha256:'+sha256(adoption._bytes(cp)).hexdigest()
    image['tiktok_checkpoint_ref']=pin(Path(image['tiktok_checkpoint_ref']['path']),cp)
    checkpoint_ref=pin(tmp_path/'retained-checkpoint.json',cp)
    with sqlite3.connect(store.path) as db:
        db.row_factory=sqlite3.Row
        for column,declaration in (('budget_scope',"TEXT DEFAULT 'target'"),('initial_outcome',"TEXT DEFAULT 'UNKNOWN'")):
            db.execute('ALTER TABLE native_final_budget_reservations ADD COLUMN '+column+' '+declaration)
        for slot in slots:
            label=slot['target_label'];saved=slot['saved_sequence'];sequence=slot['sequence']
            db.execute("UPDATE release_target_runs SET attempts=3 WHERE target_label=?",(label,))
            db.execute('INSERT INTO release_target_failure_events VALUES(?,?,?,?,?,?)',(image['run']['run_id'],label,3,'retained-business-failure3',slot['failure_evidence_digest'],'later'))
            for seq,operation in ((saved,'save_draft'),(sequence,'publish_target')):
                db.execute("INSERT INTO native_final_budget_reservations VALUES('tk',?,?,?,1,'target','UNKNOWN')",(seq,label,operation))
            db.execute("INSERT INTO native_final_budget_dispatches VALUES('tk',?,?)",(saved,'accepted-save-'+label))
            db.execute("INSERT INTO native_final_budget_outcomes VALUES('tk',?,'ACCEPTED')",(saved,))
        # SH progress is genuine baseline state for this fixture, not forced zero/terminal.
        db.execute("UPDATE release_target_runs SET status='SUCCEEDED',attempts=3 WHERE target_label='shopee:MY'")
        db.execute("UPDATE release_target_runs SET status='PENDING' WHERE target_label LIKE 'shopee:%' AND target_label!='shopee:MY'")
        db.execute("INSERT INTO native_final_budget_scopes VALUES('sh',?,'SHOPEE')",(frozen['plan_id'],))
        db.execute("INSERT INTO native_final_budget_reservations VALUES('sh',1,'shopee:MY','create_global',1,'shared','UNKNOWN')")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('sh',1,'original-sh-result')")
        db.execute("INSERT INTO native_final_budget_outcomes VALUES('sh',1,'ACCEPTED')")
        for table in ('release_target_runs','release_target_submissions','release_target_readbacks','release_target_failure_events',
            'native_final_budget_scopes','native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes'):
            image[table]=[dict(r) for r in db.execute('SELECT * FROM '+table)]
        joined=[dict(r) for r in db.execute("SELECT r.*,d.request_digest,o.outcome FROM native_final_budget_reservations r LEFT JOIN native_final_budget_dispatches d USING(scope_key,sequence) LEFT JOIN native_final_budget_outcomes o USING(scope_key,sequence) WHERE r.scope_key='tk' ORDER BY sequence")]
    retained=dict(original_failure3_rows=[r for r in image['release_target_failure_events'] if r['attempt']==3],native_TikTok_budget_rows=joined,original_CP_ref=image['tiktok_checkpoint_ref'])
    proof=dict(task_id='offline-task',offer_id=intent['offer_id'],plan_id=frozen['plan_id'],run_id=image['run']['run_id'],decision_id=intent['decision_id'],candidate_digest=frozen['candidate_digest'],snapshot_digest=intent['snapshot_digest'],base_source_head=image['source_head'],
        retained_ref=pin(tmp_path/'retained.json',retained),checkpoint_ref=checkpoint_ref,snapshot_ref=dict(path=str(tmp_path/'snapshot.json'),sha256=intent['approved_snapshot_json_sha256']),not_sent_slots=slots)
    proof_ref=pin(tmp_path/'technical-proof.json',proof)
    intent.update(schema='original-approved-tiktok-locale-draft-correction-source-request/v1',tiktok_draft_correction_ref=proof_ref,
        limits=dict(source_event_max=1,extra_save_maximum=4,extra_save_per_target=1,original_target_cap=3),reset_original_budget=False)
    # Independent strict proof parsing is tested by its business owner and actual input gate.
    # Here only that parser is a named boundary; pins, source/HMAC/CAS and RO SQL are real.
    monkeypatch.setitem(sys.modules,'shared_platform.native_tiktok_locale_draft_correction',SimpleNamespace(read_correction_proof=source._r1_reader_pin))
    return store,frozen,pin(Path(request['path']),intent),pin(Path(preimage['path']),image),proof_ref


def test_exact_new_segments_and_prior_stock_segments_are_historical_only(segments,stock_segments):
    root,old,mechanism,profile=segments
    assert set(source._locale_draft_segments(profile,old,mechanism)['correction']['changed_files'])==source.LOCALE_DRAFT_FILES
    (root/'modules/products/unrelated.py').write_text('forbidden');git(root,'add','.');git(root,'commit','-m','outside scope')
    with pytest.raises(adoption.AdoptionBlocked):source._locale_draft_segments(RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root)),old,mechanism)
    root,old,mechanism,profile=stock_segments;proof=source._stock_category_segments(profile,old,mechanism)
    (root/'shared_platform/agent_prepared_source_successor.py').write_text('next purpose');git(root,'add','.');git(root,'commit','-m','later')
    current=RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))
    assert source._historical_stock_category_segments(current,old,proof)==proof
    with pytest.raises(adoption.AdoptionBlocked):source._stock_category_segments(current,old,mechanism)


def test_only_proven_four_unknown_slots_allowed_and_runtime_can_grow(history):
    store,frozen,request,preimage,proof=history
    expected=source._locale_draft_history(store,'offline-task',frozen,request,preimage,proof,before_append=True)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE release_target_runs SET status='RUNNING',attempts=4 WHERE target_label='tiktok:LH_MY'")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('tk',10,'first-real-publish-dispatch')")
        db.execute("INSERT INTO native_final_budget_scopes VALUES('corr',?,'TIKTOK_DRAFT_CORRECTION')",(frozen['plan_id'],))
        db.execute("INSERT INTO native_final_budget_reservations VALUES('corr',1,'tiktok:LH_MY','save_draft',1,'target','UNKNOWN')")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('corr',1,'correction-can-be-inflight-after-append')")
    assert source._locale_draft_history(store,'offline-task',frozen,request,preimage,proof)==expected
    with pytest.raises(adoption.AdoptionBlocked,match='HISTORY_CAS_CHANGED'):source._locale_draft_history(store,'offline-task',frozen,request,preimage,proof,before_append=True)
    with sqlite3.connect(store.path) as db:db.execute("UPDATE native_final_budget_dispatches SET request_digest='tampered' WHERE scope_key='tk' AND sequence=9")
    with pytest.raises(adoption.AdoptionBlocked,match='PRIOR_BUDGET_CHANGED'):source._locale_draft_history(store,'offline-task',frozen,request,preimage,proof)


def test_unproven_unknown_dispatch_checkpoint_or_proof_changes_refused(history):
    store,frozen,request,preimage,proof=history;path=Path(preimage['path']);image=json.loads(path.read_bytes())
    changed=deepcopy(image);changed['native_final_budget_outcomes'].pop();ref=pin(path.with_name('other-unknown.json'),changed)
    with pytest.raises(adoption.AdoptionBlocked,match='OTHER_UNKNOWN_INFLIGHT'):source._locale_draft_history(store,'offline-task',frozen,request,ref,proof,before_append=True)
    changed=deepcopy(image);changed['native_final_budget_dispatches'].append(dict(scope_key='tk',sequence=10,request_digest='possibly-sent'))
    with pytest.raises(adoption.AdoptionBlocked,match='OTHER_UNKNOWN_INFLIGHT'):source._locale_draft_history(store,'offline-task',frozen,request,pin(path.with_name('sent.json'),changed),proof)
    wrong=pin(path.with_name('another-proof.json'),json.loads(Path(proof['path']).read_bytes()))
    with pytest.raises(adoption.AdoptionBlocked,match='EXACT_PROOF_REQUIRED'):source._locale_draft_history(store,'offline-task',frozen,request,preimage,wrong)
    cp_path=Path(image['tiktok_checkpoint_ref']['path']);cp=json.loads(cp_path.read_bytes());cp.pop('checkpoint_digest');cp['events'][0]['detail_id']='changed';cp['checkpoint_digest']='sha256:'+sha256(adoption._bytes(cp)).hexdigest();cp_path.write_bytes(adoption._bytes(cp))
    with pytest.raises(adoption.AdoptionBlocked,match='CHECKPOINT_PREFIX_CHANGED'):source._locale_draft_history(store,'offline-task',frozen,request,preimage,proof)


def test_signature_and_new_purpose_are_required_before_old_historical_verifier(monkeypatch):
    calls=[];key=b'offline';sid='owner'
    monkeypatch.setattr(source,'_verify_stock_category_source',lambda *a,**k:calls.append(k))
    record=dict(schema_version=source.LOCALE_DRAFT_SCHEMA,owner_sid=sid,purpose='wrong');raw=adoption._bytes(record)
    envelope=dict(record_json=raw.decode(),record_digest=sha256(raw).hexdigest(),signature=hmac.new(key,raw,'sha256').hexdigest())
    with pytest.raises(adoption.AdoptionBlocked,match='PURPOSE_REQUIRED'):source._verify_locale_draft_source({},None,{},connection=None,owner_sid=sid,key=key,events=[],rows=[(json.dumps(envelope),)])
    envelope['signature']='0'*64
    with pytest.raises(adoption.AdoptionBlocked,match='SIGNED_BINDING'):source._verify_locale_draft_source({},None,{},connection=None,owner_sid=sid,key=key,events=[],rows=[(json.dumps(envelope),)])
    assert calls==[]


def test_cli_requires_exact_pins_and_one_explicit_action_before_runtime_access(tmp_path):
    from scripts.continue_agent_prepared_source import main
    config=tmp_path/'deployment.json';config.write_text('{}')
    base=['--deployment',str(config),'--receipt',str(tmp_path/'unused.json')]
    with pytest.raises(ValueError,match='LOCALE_DRAFT_SOURCE_EXACT_HISTORY_PINS_REQUIRED'):
        main(base+['--approved-tiktok-locale-draft-correction'])
    with pytest.raises(ValueError,match='LOCALE_DRAFT_SOURCE_EXACT_HISTORY_PINS_REQUIRED'):
        main(base+['--locale-draft-mechanism-head','not-an-action'])
    full=['--approved-tiktok-locale-draft-correction']
    for option in ('locale-draft-mechanism-head','locale-draft-mechanism-manifest','locale-draft-technical-request',
        'locale-draft-technical-request-sha256','locale-draft-run-preimage','locale-draft-run-preimage-sha256',
        'tiktok-draft-correction-proof','tiktok-draft-correction-proof-sha256'):
        full+=['--'+option,'offline-pin']
    with pytest.raises(ValueError,match='SOURCE_SUCCESSOR_EXACT_SOURCE_ACTION_REQUIRED'):
        main(base+full+['--common-create-successor'])
    assert not (tmp_path/'unused.json').exists()


def test_one_signed_append_full_task_events_CAS_and_release_bytes_preserved(segments,history,monkeypatch):
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform import r3_native_final_approval as actor,release_store
    root,old,mechanism,profile=segments;store,frozen,request,preimage,proof_ref=history;sid='owner';key=b'offline-key'
    engine=WorkbenchEngine(profile.data_root/'tasks.db',old)
    task_id=engine.create(dict(template='publication',source_key='locale-draft',scope=dict(offer_id='8412287877065586543',shops=frozen['ordered_targets'],skus=['0431'])))['task_id']
    image=json.loads(Path(preimage['path']).read_bytes());image['source_head']=old['code_version'];preimage=pin(Path(preimage['path']),image)
    proof=json.loads(Path(proof_ref['path']).read_bytes());proof.update(task_id=task_id,base_source_head=old['code_version']);proof_ref=pin(Path(proof_ref['path']),proof)
    intent=json.loads(Path(request['path']).read_bytes());intent.update(task_id=task_id,source_head=old['code_version'],tiktok_draft_correction_ref=proof_ref);request=pin(Path(request['path']),intent)
    original=dict(schema_version=adoption.SCHEMA,owner_sid=sid,task_id=task_id,offer_id='8412287877065586543');raw=adoption._bytes(original);digest=sha256(raw).hexdigest()
    prior=dict(schema_version=source.STOCK_CATEGORY_SCHEMA,owner_sid=sid,scope_digest='scope',frozen_candidate=frozen,source_segments=dict(correction=dict(new_version=old)));encoded=adoption._bytes(prior)
    with engine.transaction() as db:
        db.execute('CREATE TABLE agent_prepared_publication_adoptions(task_id TEXT,record_json TEXT,record_digest TEXT,signature TEXT)')
        db.execute('INSERT INTO agent_prepared_publication_adoptions VALUES(?,?,?,?)',(task_id,raw.decode(),digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute("UPDATE workbench_execution SET state='reconciliation_required',step_index=2,action_json=?,checkpoint_json=? WHERE task_id=?",('{"preserved":"action"}','{"preserved":"checkpoint"}',task_id))
        engine._event(db,task_id,source.STOCK_CATEGORY_EVENT,dict(record_json=encoded.decode(),record_digest=sha256(encoded).hexdigest(),signature=hmac.new(key,encoded,'sha256').hexdigest()))
        before=dict(engine._row(db,task_id));oldevents=[tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id')]
    monkeypatch.setattr(adoption,'_owner_key',lambda _:(sid,key));monkeypatch.setattr(actor,'read_native_actor_identity',lambda _:dict(identity_verified=True,trust_mode='SAME_WINDOWS_USER',owner_sid=sid))
    monkeypatch.setattr(source,'_verify_stock_category_source',lambda *a,**k:dict(original='prior genuine chain boundary'))
    monkeypatch.setattr(release_store,'default_release_store',lambda:store)
    kwargs=dict(task_id=task_id,expected_adoption_digest=digest,actor_reader=object(),mechanism_head=mechanism['code_version'],mechanism_manifest=mechanism['manifest_digest'],technical_request=request,run_preimage=preimage,tiktok_draft_correction_ref=proof_ref)
    read_history=source._locale_draft_history
    def race(*a,**k):
        with engine.transaction() as db:db.execute("UPDATE workbench_execution SET action_json='{}' WHERE task_id=?",(task_id,))
        return read_history(*a,**k)
    monkeypatch.setattr(source,'_locale_draft_history',race)
    with pytest.raises(adoption.AdoptionBlocked,match='CAS_CHANGED'):source.continue_locale_draft_source(engine,profile,store,**kwargs)
    with engine.transaction() as db:db.execute('UPDATE workbench_execution SET action_json=? WHERE task_id=?',(before['action_json'],task_id))
    monkeypatch.setattr(source,'_locale_draft_history',read_history);release_before=sha256(store.path.read_bytes()).hexdigest()
    source.continue_locale_draft_source(engine,profile,store,**kwargs)
    assert sha256(store.path.read_bytes()).hexdigest()==release_before
    with engine.transaction() as db:
        after=dict(engine._row(db,task_id));assert {k:v for k,v in before.items() if k!='version_json'}=={k:v for k,v in after.items() if k!='version_json'}
        assert [tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id')][:-1]==oldevents
        task=engine._project(db,engine._row(db,task_id));rows=db.execute('SELECT detail_json FROM workbench_events WHERE event_type=?',(source.LOCALE_DRAFT_EVENT,)).fetchall()
        assert source._verify_locale_draft_source(task,profile,original,connection=db,owner_sid=sid,key=key,events=[],rows=rows)==dict(original='prior genuine chain boundary')
    with pytest.raises(adoption.AdoptionBlocked,match='SINGLE_EVENT'):source.continue_locale_draft_source(engine,profile,store,**kwargs)
