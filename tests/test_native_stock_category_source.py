"""One source event retains real accepted claims; no actor/provider fixture proof."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import hmac,json,sqlite3,os
import pytest
from shared_platform import agent_prepared_source_successor as source,agent_prepared_publication_adoption as adoption
from shared_platform.operations_runtime import RuntimeProfile,runtime_manifest
from shared_platform.native_prepared_v4_targets import _file_identity
from test_approved_native_r1_reader_source import git,pin
from test_approved_platform_source import history as platform_history
from test_native_tiktok_ozon_recovery_source import segments as native_segments


@pytest.fixture
def segments(tmp_path):
    root=tmp_path/'stock-source';root.mkdir();git(root,'init')
    for name in source.STOCK_CATEGORY_MECHANISM_FILES|source.STOCK_CATEGORY_FILES:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('old\n')
    git(root,'add','.');git(root,'commit','-m','prior signed source')
    old=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.STOCK_CATEGORY_MECHANISM_FILES:(root/name).write_text('new bounded mechanism\n')
    git(root,'add','.');git(root,'commit','-m','mechanism')
    mechanism=dict(code_version=git(root,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(root))
    for name in source.STOCK_CATEGORY_FILES:(root/name).write_text('bounded stock category continuation\n')
    git(root,'add','.');git(root,'commit','-m','business correction')
    return root,old,mechanism,RuntimeProfile(root,tmp_path/'ops','stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))


@pytest.fixture
def history(platform_history,tmp_path):
    store,frozen,request,preimage,_=platform_history
    intent=json.loads(Path(request['path']).read_bytes());image=json.loads(Path(preimage['path']).read_bytes())
    run_id='release-run:'+'a'*24;intent['original_run_id']=run_id;image['run']['run_id']=run_id
    with sqlite3.connect(store.path) as db:
        for table in ('release_runs','release_target_runs','release_target_submissions','release_target_readbacks','release_target_failure_events'):
            db.execute('UPDATE '+table+' SET run_id=?',(run_id,))
    snapshot=dict(offer_id=intent['offer_id'],product_revision=6,snapshot_digest='sha256:'+'a'*64,product=dict(stock_policy=dict(
        schema_version='publication-default-stock/v1',quantity_per_sku=200,scope='EACH_SELECTED_SKU',source='SYSTEM_GOVERNED_DEFAULT',review_round='ROUND1')))
    raw=adoption._bytes(snapshot).decode();identity=_file_identity(run_id=image['run']['run_id'],plan_id=frozen['plan_id'],decision_id=intent['decision_id'],snapshot_digest=snapshot['snapshot_digest'])
    owner=tmp_path/'native-final-owner';(owner/'native-final-file-identities').mkdir(parents=True)
    identity_ref=pin(owner/'native-final-file-identities'/(identity['file_run_id']+'.json'),identity)
    from modules.miaoshou.tiktok_v4_drafts import EXPECTED_SHOP_ID_BY_TARGET
    checkpoint=dict(schema_version='tiktok-v4-draft-checkpoint/v1',run_id=identity['file_run_id'],offer_id=snapshot['offer_id'],
        product_revision=6,snapshot_digest=snapshot['snapshot_digest'],events=[],external_write_count=8,receipt=None)
    for n,label in enumerate(sorted(source.NATIVE_RECOVERY_TARGETS)):
        checkpoint['events'] += [dict(target_label=label,operation=operation,outcome='ACCEPTED',detail_id=str(100+n),shop_id=str(EXPECTED_SHOP_ID_BY_TARGET[label])) for operation in ('CREATE_DRAFT','CLAIM_TO_SHOP')]
    checkpoint['checkpoint_digest']='sha256:'+sha256(adoption._bytes(checkpoint)).hexdigest()
    image['tiktok_checkpoint']=checkpoint
    checkpoint_path=owner/snapshot['offer_id']/'6'/identity['file_run_id']/'tiktok-draft-checkpoint.json';checkpoint_path.parent.mkdir(parents=True)
    image['tiktok_checkpoint_ref']=pin(checkpoint_path,checkpoint)
    image['all_targets_idle']=True
    with sqlite3.connect(store.path) as db:
        db.row_factory=sqlite3.Row
        db.execute('CREATE TABLE approved_publication_snapshots(plan_id TEXT,snapshot_json TEXT,snapshot_digest TEXT)')
        db.execute('INSERT INTO approved_publication_snapshots VALUES(?,?,?)',(frozen['plan_id'],raw,snapshot['snapshot_digest']))
        db.execute("UPDATE release_target_runs SET attempts=2 WHERE target_label!='ozon:RU'")
        db.execute("UPDATE release_target_runs SET attempts=2 WHERE target_label='ozon:RU'")
        db.execute('INSERT INTO release_target_failure_events VALUES(?,?,?,?,?,?)',(run_id,'ozon:RU',2,'terminal-validation-no-dispatch','ozon-original-failure','now'))
        for label in sorted(source.NATIVE_RECOVERY_TARGETS|source.STOCK_CATEGORY_SHOPEE):
            zero=label in source.STOCK_CATEGORY_SHOPEE
            cumulative=0 if zero else max(i+1 for i,event in enumerate(checkpoint['events']) if event['target_label']==label)
            proof=dict(schema_version='native-scoped-v4-target-result/v1',target_label=label,known_pre_submit_zero=zero,
                request_attempted=False,outcome_unknown=False,external_writes_performed=[] if zero else [label],
                checkpoint_file_identity=identity,checkpoint_file_identity_ref=identity_ref,
                canonical_platform_result=dict(schema_version='product-publication-platform-result/v1',dispatch_attempted=False,external_write_count=cumulative,
                    targets=[dict(target_label=label,evidence=dict(external_write_count=0 if zero else 2,outcome_unknown=False))]))
            encoded=adoption._bytes(proof).decode()
            db.execute('INSERT INTO release_target_failure_events VALUES(?,?,?,?,?,?)',(image['run']['run_id'],label,2,encoded,sha256(encoded.encode()).hexdigest(),'now'))
        db.execute("INSERT INTO native_final_budget_scopes VALUES('tk',?,'TIKTOK')",(frozen['plan_id'],))
        for n,label in enumerate(sorted(source.NATIVE_RECOVERY_TARGETS)):
            for offset,operation in enumerate(('create_platform_draft','claim_draft')):
                sequence=2*n+offset+1
                db.execute('INSERT INTO native_final_budget_reservations VALUES(?,?,?,?,?)',('tk',sequence,label,operation,1))
                db.execute('INSERT INTO native_final_budget_dispatches VALUES(?,?,?)',('tk',sequence,'original-'+str(sequence)))
                db.execute("INSERT INTO native_final_budget_outcomes VALUES(?,?,'ACCEPTED')",('tk',sequence))
        for table in source.STOCK_CATEGORY_MEDIA_TABLES:
            db.execute('CREATE TABLE '+table+' (grant_id TEXT, '+('run_id' if table.endswith('_grants') else 'evidence')+' TEXT)')
            db.execute('INSERT INTO '+table+' VALUES(?,?)',('original-media3',image['run']['run_id'] if table.endswith('_grants') else 'original-media3-evidence'))
        for name in ('release_target_runs','release_target_submissions','release_target_readbacks','release_target_failure_events',
            'native_final_budget_scopes','native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes',*source.STOCK_CATEGORY_MEDIA_TABLES):
            image[name]=[dict(r) for r in db.execute('SELECT * FROM '+name)]
    intent.update(schema='original-approved-stock-category-continuation-source-request/v1',accepted_draft_targets=sorted(source.NATIVE_RECOVERY_TARGETS),
        known_zero_targets=sorted(source.STOCK_CATEGORY_SHOPEE),append_requires_all_mutations_idle=True,recreate_or_reclaim=False,reset_or_increase_budget=False,
        preserve_all_original_content_effects_and_history=True,approved_stock_quantity_per_sku=200,snapshot_digest=snapshot['snapshot_digest'],approved_snapshot_json_sha256=sha256(raw.encode()).hexdigest(),
        diagnostics={k:pin(tmp_path/(k+'.json'),dict(exact_captured_diagnosis=k)) for k in ('tiktok_stock','shopee_category')})
    return store,frozen,pin(Path(request['path']),intent),pin(Path(preimage['path']),image)


def test_exact_source_segments_and_old_native_Git_remains_strict_without_new_purpose(segments,native_segments):
    root,old,mechanism,profile=segments
    assert set(source._stock_category_segments(profile,old,mechanism)['correction']['changed_files'])==source.STOCK_CATEGORY_FILES
    (root/'modules/products/extra.py').write_text('forbidden\n');git(root,'add','.');git(root,'commit','-m','unrelated')
    with pytest.raises(adoption.AdoptionBlocked):source._stock_category_segments(RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root)),old,mechanism)
    root,old,mechanism,profile=native_segments;proof=source._native_recovery_segments(profile,old,mechanism)
    (root/'shared_platform/agent_prepared_source_successor.py').write_text('next source purpose\n');git(root,'add','.');git(root,'commit','-m','later')
    current=RuntimeProfile(root,profile.data_root,'stable',git(root,'rev-parse','HEAD'),runtime_manifest(root))
    assert source._historical_native_recovery_segments(current,old,proof)==proof
    with pytest.raises(adoption.AdoptionBlocked):source._native_recovery_segments(current,old,mechanism)


def test_accepted_claim_and_media_prefix_preserved_while_normal_future_mutations_grow(history):
    store,frozen,request,preimage=history
    expected=source._stock_category_history(store,'offline-task',frozen,request,preimage,before_append=True)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE release_target_runs SET status='RUNNING',attempts=3 WHERE target_label='tiktok:LH_MY'")
        db.execute("INSERT INTO native_final_budget_reservations VALUES('tk',9,'tiktok:LH_MY','save_draft',1)")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('tk',9,'new-save-unknown')")
        db.execute("UPDATE release_target_runs SET status='SUCCEEDED',attempts=3 WHERE target_label='ozon:RU'")
        db.execute("INSERT INTO native_final_budget_reservations VALUES('ozon',4,'ozon:RU','update_stock',1)")
        db.execute("INSERT INTO native_final_budget_dispatches VALUES('ozon',4,'new-stock-unknown')")
    image=json.loads(Path(preimage['path']).read_bytes());path=Path(image['tiktok_checkpoint_ref']['path']);cp=json.loads(path.read_bytes())
    cp.pop('checkpoint_digest');cp['events'].append(dict(target_label='tiktok:LH_MY',operation='SAVE_DRAFT',outcome='UNKNOWN',detail_id='100',shop_id='13295169'))
    cp['external_write_count']=None;cp['checkpoint_digest']='sha256:'+sha256(adoption._bytes(cp)).hexdigest();path.write_bytes(adoption._bytes(cp))
    assert source._stock_category_history(store,'offline-task',frozen,request,preimage)==expected
    with pytest.raises(adoption.AdoptionBlocked,match='HISTORY_CAS_CHANGED'):source._stock_category_history(store,'offline-task',frozen,request,preimage,before_append=True)
    cp.pop('checkpoint_digest');cp['events'][0]['detail_id']='changed';cp['checkpoint_digest']='sha256:'+sha256(adoption._bytes(cp)).hexdigest();path.write_bytes(adoption._bytes(cp))
    with pytest.raises(adoption.AdoptionBlocked,match='CHECKPOINT_PREFIX_CHANGED'):source._stock_category_history(store,'offline-task',frozen,request,preimage)


def test_no_false_zero_unknown_or_rewriting_prior_effects(history):
    store,frozen,request,preimage=history;path=Path(preimage['path']);image=json.loads(path.read_bytes())
    changed=deepcopy(image);row=next(r for r in changed['release_target_failure_events'] if r['attempt']==2 and r['target_label'].startswith('tiktok:'))
    proof=json.loads(row['evidence_json']);proof['known_pre_submit_zero']=True;row['evidence_json']=adoption._bytes(proof).decode();row['evidence_digest']=sha256(row['evidence_json'].encode()).hexdigest()
    wrong=pin(path.with_name('false-zero.json'),changed)
    with pytest.raises(adoption.AdoptionBlocked,match='TYPED_ORIGINAL_EFFECTS'):source._stock_category_history(store,'offline-task',frozen,request,wrong,before_append=True)
    changed=deepcopy(image);changed['native_final_budget_outcomes'].pop();wrong=pin(path.with_name('unknown.json'),changed)
    with pytest.raises(adoption.AdoptionBlocked,match='UNKNOWN_INFLIGHT'):source._stock_category_history(store,'offline-task',frozen,request,wrong,before_append=True)
    with sqlite3.connect(store.path) as db:db.execute("UPDATE native_final_budget_dispatches SET request_digest='tampered' WHERE scope_key='tk' AND sequence=1")
    with pytest.raises(adoption.AdoptionBlocked,match='PRIOR_BUDGET_CHANGED'):source._stock_category_history(store,'offline-task',frozen,request,preimage)


def test_new_signature_and_purpose_precede_historical_native_verifier(monkeypatch):
    calls=[];key=b'offline';sid='owner'
    monkeypatch.setattr(source,'_verify_native_recovery_source',lambda *a,**k:calls.append(k))
    record=dict(schema_version=source.STOCK_CATEGORY_SCHEMA,owner_sid=sid,purpose='wrong');raw=adoption._bytes(record)
    envelope=dict(record_json=raw.decode(),record_digest=sha256(raw).hexdigest(),signature=hmac.new(key,raw,'sha256').hexdigest())
    with pytest.raises(adoption.AdoptionBlocked,match='PURPOSE_REQUIRED'):source._verify_stock_category_source({},None,{},connection=None,owner_sid=sid,key=key,events=[],rows=[(json.dumps(envelope),)])
    envelope['signature']='0'*64
    with pytest.raises(adoption.AdoptionBlocked,match='SIGNED_BINDING'):source._verify_stock_category_source({},None,{},connection=None,owner_sid=sid,key=key,events=[],rows=[(json.dumps(envelope),)])
    assert calls==[]


def test_real_retained_four_failure_counts_match_original_checkpoint_prefix():
    folder=os.environ.get('ORBIT_STOCK_CATEGORY_RETAINED')
    if not folder:pytest.skip('explicit credential-free original artifact directory not requested')
    folder=Path(folder)
    failure=(folder/'ALL_TIKTOK_ATTEMPT2_FAILURES.private.json').read_bytes()
    raw=(folder/'TIKTOK_CHECKPOINT_ALL_FOUR_CREATED_CLAIMED.private.json').read_bytes()
    assert sha256(failure).hexdigest()=='4269ffa35e80cb4a5533520d4b631c17bc7a30b3d87dd9fcd4918daad7e5b503'
    assert sha256(raw).hexdigest()=='931c0be4dae4a1dd9f3c48b21b85412831a470bd3a8e8f445c86aa9dc37284d8'
    cp=json.loads(raw);observed={}
    for row in json.loads(failure):
        assert sha256(row['evidence_json'].encode()).hexdigest()==row['evidence_digest']
        proof=json.loads(row['evidence_json']);label=row['target_label']
        assert source._stock_category_preparation(proof,label,cp)
        observed[label]=proof['canonical_platform_result']['external_write_count']
        if label!='tiktok:LH_MY':
            changed=deepcopy(proof);changed['canonical_platform_result']['external_write_count']=2
            assert not source._stock_category_preparation(changed,label,cp)
    assert observed==dict(zip(('tiktok:LH_MY','tiktok:LH_PH','tiktok:LH_TH','tiktok:LH_VN'),(2,4,6,8)))
    original=(folder/'ORIGINAL_RUN_WITH_SH4_ATTEMPT2_FOR_OFFLINE_REPAIR.private.json').read_bytes()
    assert sha256(original).hexdigest()=='12aae78097d023001c08a07b1e583e6cb8f219c2e79369eb571b31a193abb06a'
    sh=[row for row in json.loads(original)['tables']['release_target_failure_events'] if row['attempt']==2 and row['target_label'] in source.STOCK_CATEGORY_SHOPEE]
    assert {row['target_label'] for row in sh}==source.STOCK_CATEGORY_SHOPEE
    for row in sh:
        assert sha256(row['evidence_json'].encode()).hexdigest()==row['evidence_digest']
        assert source._stock_category_preparation(json.loads(row['evidence_json']),row['target_label'],cp)


def test_old_media_and_checkpoint_path_are_not_replaceable(history):
    store,frozen,request,preimage=history;path=Path(preimage['path']);image=json.loads(path.read_bytes())
    changed=deepcopy(image);changed['tiktok_checkpoint_ref']=pin(path.with_name('captured-copy.json'),image['tiktok_checkpoint'])
    with pytest.raises(adoption.AdoptionBlocked,match='ORIGINAL_CLAIM_PREFIX_REQUIRED'):
        source._stock_category_history(store,'offline-task',frozen,request,pin(path.with_name('wrong-path.json'),changed),before_append=True)
    changed=deepcopy(image);changed['native_ozon_media_outcomes']=[]
    with pytest.raises(adoption.AdoptionBlocked,match='UNKNOWN_INFLIGHT'):
        source._stock_category_history(store,'offline-task',frozen,request,pin(path.with_name('media-unknown.json'),changed),before_append=True)
    with sqlite3.connect(store.path) as db:db.execute("UPDATE native_ozon_media_outcomes SET evidence='tampered'")
    with pytest.raises(adoption.AdoptionBlocked,match='PRIOR_MEDIA_CHANGED'):
        source._stock_category_history(store,'offline-task',frozen,request,preimage)


def test_full_task_event_CAS_single_hmac_append_and_no_approved_or_run_changes(segments,history,monkeypatch):
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform import r3_native_final_approval as actor,release_store
    root,old,mechanism,profile=segments;store,frozen,request,preimage=history;sid='owner';key=b'offline-key'
    engine=WorkbenchEngine(profile.data_root/'tasks.db',old)
    task_id=engine.create(dict(template='publication',source_key='stock-category',scope=dict(offer_id='8412287877065586543',shops=frozen['ordered_targets'],skus=['0431'])))['task_id']
    intent=json.loads(Path(request['path']).read_bytes());intent.update(task_id=task_id,source_head=old['code_version']);request=pin(Path(request['path']),intent)
    image=json.loads(Path(preimage['path']).read_bytes());image['source_head']=old['code_version'];preimage=pin(Path(preimage['path']),image)
    original=dict(schema_version=adoption.SCHEMA,owner_sid=sid,task_id=task_id,offer_id='8412287877065586543');raw=adoption._bytes(original);digest=sha256(raw).hexdigest()
    prior=dict(schema_version=source.NATIVE_RECOVERY_SCHEMA,owner_sid=sid,scope_digest='scope',frozen_candidate=frozen,source_segments=dict(retry=dict(new_version=old)));encoded=adoption._bytes(prior)
    with engine.transaction() as db:
        db.execute('CREATE TABLE agent_prepared_publication_adoptions(task_id TEXT,record_json TEXT,record_digest TEXT,signature TEXT)')
        db.execute('INSERT INTO agent_prepared_publication_adoptions VALUES(?,?,?,?)',(task_id,raw.decode(),digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute("UPDATE workbench_execution SET state='reconciliation_required',step_index=2,action_json=?,checkpoint_json=? WHERE task_id=?",('{"retained":"action"}','{"retained":"checkpoint"}',task_id))
        engine._event(db,task_id,source.NATIVE_RECOVERY_EVENT,dict(record_json=encoded.decode(),record_digest=sha256(encoded).hexdigest(),signature=hmac.new(key,encoded,'sha256').hexdigest()))
        before=dict(engine._row(db,task_id));events_before=[tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id')]
    monkeypatch.setattr(adoption,'_owner_key',lambda _:(sid,key));monkeypatch.setattr(actor,'read_native_actor_identity',lambda _:dict(identity_verified=True,trust_mode='SAME_WINDOWS_USER',owner_sid=sid))
    # The prior full business chain is a named boundary; Git and new history/HMAC/CAS are genuine.
    monkeypatch.setattr(source,'_verify_native_recovery_source',lambda *a,**k:dict(original='existing verified chain boundary'))
    monkeypatch.setattr(release_store,'default_release_store',lambda:store)
    kwargs=dict(task_id=task_id,expected_adoption_digest=digest,actor_reader=object(),mechanism_head=mechanism['code_version'],mechanism_manifest=mechanism['manifest_digest'],technical_request=request,run_preimage=preimage)
    read_history=source._stock_category_history
    def race(*a,**k):
        with engine.transaction() as db:db.execute("UPDATE workbench_execution SET action_json='{}' WHERE task_id=?",(task_id,))
        return read_history(*a,**k)
    monkeypatch.setattr(source,'_stock_category_history',race)
    with pytest.raises(adoption.AdoptionBlocked,match='CAS_CHANGED'):source.continue_stock_category_source(engine,profile,store,**kwargs)
    with engine.transaction() as db:db.execute('UPDATE workbench_execution SET action_json=? WHERE task_id=?',(before['action_json'],task_id))
    monkeypatch.setattr(source,'_stock_category_history',read_history);release_before=sha256(store.path.read_bytes()).hexdigest()
    source.continue_stock_category_source(engine,profile,store,**kwargs)
    assert sha256(store.path.read_bytes()).hexdigest()==release_before
    with engine.transaction() as db:
        after=dict(engine._row(db,task_id));assert {k:v for k,v in before.items() if k!='version_json'}=={k:v for k,v in after.items() if k!='version_json'}
        assert [tuple(r) for r in db.execute('SELECT * FROM workbench_events ORDER BY id')][:-1]==events_before
        task=engine._project(db,engine._row(db,task_id));rows=db.execute('SELECT detail_json FROM workbench_events WHERE event_type=?',(source.STOCK_CATEGORY_EVENT,)).fetchall()
        assert source._verify_stock_category_source(task,profile,original,connection=db,owner_sid=sid,key=key,events=[],rows=rows)==dict(original='existing verified chain boundary')
    with pytest.raises(adoption.AdoptionBlocked,match='SINGLE_EVENT'):source.continue_stock_category_source(engine,profile,store,**kwargs)
