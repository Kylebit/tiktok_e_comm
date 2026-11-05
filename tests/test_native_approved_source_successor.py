"""One local contract regression: retain known work, only remove eligibility.

No actor fixture, maintenance registration, production DB or transport is used.
"""
from copy import deepcopy
from hashlib import sha256
import json
import sqlite3
from types import SimpleNamespace

import pytest

from shared_platform import native_approved_source_successor as source
from shared_platform import native_publication_cancellation as cancel


def test_append_code_compatibility_retains_partial_unknown_and_refuses_tampering(tmp_path):
    # Real SQLite rows exercise the transaction core; no actor/provider fixture.
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    business_db=sqlite3.connect(':memory:');business_db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE workbench_events(id INTEGER PRIMARY KEY,task_id,event_type,detail_json,created_at);
        CREATE TABLE workbench_tasks(task_id PRIMARY KEY,source_key);
        CREATE TABLE workbench_execution(task_id PRIMARY KEY,request_digest,scope_json,version_json,
          template,state,worker,lease_token,lease_until,external_started,checkpoint_json,steps_json,action_json);
        CREATE TABLE workbench_domain_operations(operation_id,owner_task_id,state);
        CREATE TABLE workbench_domain_locks(operation_id,resource);''')
    business_db.executescript('''CREATE TABLE release_target_runs(run_id,target_label,status,attempts);
        CREATE TABLE native_final_budget_reservations(scope_key,sequence,initial_outcome);
        CREATE TABLE oneclick_release_jobs(job_id,plan_id,status);
        CREATE TABLE oneclick_release_targets(job_id,target_label,status);
        CREATE TABLE oneclick_release_write_occurrences(job_id,occurrence_id,status);''')
    business_db.executemany('INSERT INTO release_target_runs VALUES(?,?,?,?)',[
        ('original-run','shopee:TH','SUCCEEDED',1),('original-run','shopee:PH','FAILED',1),
        ('original-run','shopee:VN','RUNNING',1),('original-run','tiktok:fixture','FAILED',1)])
    business_db.execute("INSERT INTO native_final_budget_reservations VALUES('original-budget',12,'UNKNOWN')")
    business_db.execute("INSERT INTO oneclick_release_jobs VALUES('original-promotion',?,'UNKNOWN')",(source.PLAN,))
    business_db.execute("INSERT INTO oneclick_release_targets VALUES('original-promotion','shopee:TH','UNKNOWN')")
    business_db.execute("INSERT INTO oneclick_release_write_occurrences VALUES('original-promotion','request-one','OPEN')")
    business_db.commit()
    old={'code_version':'old','environment':'stable','manifest_digest':'old-manifest'}
    current={'code_version':'current','environment':'stable','manifest_digest':'current-manifest'}
    updated={'code_version':'updated','environment':'stable','manifest_digest':'updated-manifest'}
    scope={'offer_id':source.OFFER,'shops':['shopee:TH','shopee:PH','shopee:VN','tiktok:fixture','ozon:RU'],'skus':['9457']}
    original={'source_key':'original-key','request_digest':'original-request','scope':scope,'release':old}
    grant={'id':1,'detail_json':source._json(original)}
    db.execute('INSERT INTO workbench_events VALUES(?,?,?,?,?)',(1,source.TASK,'explicit_new_post_preparation',grant['detail_json'],'before'))
    document={'schema':'orbit-publication-platform-cancellation/v1','actor':'USER','platform':'OZON',
        'user_message':'ozon不用上品了这个平台全部取消','scope':'Cancel Ozon for current Offer4106902637 and future new-product publication defaults',
        'effect':'NO_FURTHER_OZON_PUBLICATION_IMAGE_OR_STOCK_MUTATIONS','original_decision_id':source.DECISION,
        'original_candidate_digest':source.CANDIDATE,'remaining_platforms':['TIKTOK','SHOPEE'],
        'existing_listing_delisting_authorized':False,'new_image_budget_granted':False,'new_approval_required':False}
    path=tmp_path/'cancellation.json';path.write_text(source._json(document),encoding='utf8')
    restriction=cancel.authority({'path':str(path),'sha256':sha256(path.read_bytes()).hexdigest()})
    identity={'decision_id':source.DECISION,'plan_id':source.PLAN,'candidate_digest':source.CANDIDATE,
        'snapshot_digest':source.SNAPSHOT,'run_id':'original-run','accepted_external_id':source.OZON,
        'release_store_path':'original-release.db','receipt':{'approval':'original'}}
    root={'schema_version':source.SCHEMA,'task_id':source.TASK,'source_key':original['source_key'],
        'request_digest':original['request_digest'],'scope':scope,'from_release':old,'to_release':current,
        'checkpoint_raw':'original checkpoint','steps_raw':'original steps','action_raw':'original action',
        'registration_state':'reconciliation_required','history_sha256':source._digest(source._history(db,source.TASK)),
        'images':{'paid':'same18','media':'same14'},'predecessors':{},'business':identity,'cancellation':restriction,
        'source':{'root':str(tmp_path),'from_head':'old','to_head':'current','manifest_digest':'current-manifest',
            'changed_paths':['M\tshared_platform/native_sole_final_decision.py']},
        'original_post_event_id':1,'original_post_sha256':sha256(grant['detail_json'].encode()).hexdigest(),'execution_authority':False}
    root_raw=source._json({'record':root,'record_sha256':source._digest(root)})
    db.execute('INSERT INTO workbench_events VALUES(?,?,?,?,?)',(2,source.TASK,source.EVENT,root_raw,'original-registration'))
    values=(source.TASK,original['request_digest'],source._json(scope),source._json(current),'publication',
        'reconciliation_required',None,None,None,1,'{"outcome_unknown":true,"attempt":1}',
        '[{"state":"completed"},{"state":"pending"}]','{"kind":"observe","original":true}')
    db.execute('INSERT INTO workbench_execution VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',values)
    db.execute('INSERT INTO workbench_tasks VALUES(?,?)',(source.TASK,original['source_key']))
    db.execute("INSERT INTO workbench_domain_operations VALUES('unknown-operation',?,'UNKNOWN')",(source.TASK,))
    db.execute("INSERT INTO workbench_domain_locks VALUES('unknown-operation','original-product-lock')")
    db.commit()
    def row(connection,task):return dict(connection.execute('SELECT * FROM workbench_execution WHERE task_id=?',(task,)).fetchone())
    def event(connection,task,kind,detail):connection.execute('INSERT INTO workbench_events(task_id,event_type,detail_json,created_at) VALUES(?,?,?,?)',(task,kind,source._json(detail),'append'))
    engine=SimpleNamespace(release=updated,_row=row,_event=event)
    def business():
        keys={'run_id':['original-run'],'scope_key':['original-budget'],'job_id':source._related_jobs(business_db,[source.PLAN])}
        return {**identity,'historical_rows':source._related_rows(business_db,keys)}
    before=row(db,source.TASK);business_before=business()
    assert {r['table'] for r in business_before['historical_rows']} >= {
        'oneclick_release_targets','oneclick_release_write_occurrences','native_final_budget_reservations'}
    expected={'task_id':source.TASK,'source_key':original['source_key'],'request_digest':original['request_digest'],
        'scope':scope,'from_release':current,'task_row':before,'task_context':source._task_context(db,source.TASK),
        'history_sha256':source._digest(source._history(db,source.TASK)),'parent':source._event_ref(source._events(db,source.TASK)[-1][0]),
        'business':business_before,'images':root['images'],'predecessors':root['predecessors'],'cancellation':restriction}
    delta={'root':str(tmp_path),'from_head':'current','to_head':'updated','manifest_digest':'updated-manifest',
        'changed_paths':['M\tmodules/shopee/skill_regions.py']}
    business_db.execute("UPDATE oneclick_release_targets SET status='PENDING'");business_db.commit()
    with pytest.raises(ValueError,match='ORIGINAL_BUSINESS_ROWS_CHANGED'):
        with db:source._append_compatibility(engine,db,expected,business(),delta)
    business_db.execute("UPDATE oneclick_release_targets SET status='UNKNOWN'");business_db.commit()
    db.execute("UPDATE workbench_domain_locks SET resource='changed-lock'");db.commit()
    with pytest.raises(ValueError,match='COMPATIBILITY_TASK_CAS_CHANGED'):
        with db:source._append_compatibility(engine,db,expected,business(),delta)
    db.execute("UPDATE workbench_domain_locks SET resource='original-product-lock'");db.commit()
    with db:record=source._append_compatibility(engine,db,expected,business(),delta)
    after=row(db,source.TASK)
    assert {k:v for k,v in after.items() if k!='version_json'} == {k:v for k,v in before.items() if k!='version_json'}
    assert json.loads(after['version_json'])==updated and business()==business_before
    assert db.execute('SELECT detail_json FROM workbench_events WHERE id=2').fetchone()[0]==root_raw
    assert source.verify(db,after,original,grant,old,updated)==updated
    assert len(source._events(db,source.TASK))==2 and source._event(db,source.TASK)[1]==root
    # Further code repair after real progress appends again; neither old link
    # nor newly retained UNKNOWN checkpoint/event is replaced by a zero state.
    db.execute('UPDATE workbench_execution SET checkpoint_json=?',('{"outcome_unknown":true,"attempt":2}',))
    event(db,source.TASK,'provider_unknown_retained',{'original_attempt':2});db.commit()
    latest={**updated,'code_version':'latest','manifest_digest':'latest-manifest'}
    expected2={**expected,'from_release':updated,'task_row':row(db,source.TASK),
        'task_context':source._task_context(db,source.TASK),'history_sha256':source._digest(source._history(db,source.TASK)),
        'parent':source._event_ref(source._events(db,source.TASK)[-1][0])}
    engine.release=latest
    with db:source._append_compatibility(engine,db,expected2,business(),
        {**delta,'from_head':'updated','to_head':'latest','manifest_digest':'latest-manifest'})
    after=row(db,source.TASK)
    assert source.verify(db,after,original,grant,old,latest)==latest
    assert len(source._events(db,source.TASK))==3 and business()==business_before
    changed=deepcopy(record);changed['parent']['detail_sha256']='0'*64
    db.execute('UPDATE workbench_events SET detail_json=? WHERE id=3',
        (source._json({'record':changed,'record_sha256':source._digest(changed)}),))
    with pytest.raises(ValueError,match='COMPATIBILITY_HISTORY_CHANGED'):
        source.verify(db,after,original,grant,old,latest)
    db.close();business_db.close()


def test_saved_approval_and_accepted_import_survive_cancelled_execution_scope(tmp_path):
    labels=[f'tiktok:fixture{i}' for i in range(10)]+[f'shopee:fixture{i}' for i in range(4)]+['ozon:RU']
    targets=[{'target_label':label,'status':'PENDING','attempts':0} for label in labels[:-1]]
    targets.append({'target_label':'ozon:RU','status':'SUBMITTED_UNVERIFIED','attempts':1,
        'external_id':source.OZON,'submission':{'external_id':source.OZON,'evidence':{
            'schema_version':'native-scoped-v4-target-result/v1','accepted':True,'outcome_unknown':False,
            'external_writes_performed':['ozon:RU']}}})
    binding=source._json({'decision_id':source.DECISION,'candidate_digest':source.CANDIDATE})
    scopes=[{'scope_key':'original-budget','platform':'OZON','plan_id':source.PLAN,
        'snapshot_digest':source.SNAPSHOT,'binding_json':binding,'binding_sha256':sha256(binding.encode()).hexdigest()}]
    reservations=[{'scope_key':'original-budget','sequence':1,'target_label':'ozon:RU','budget_scope':'shared',
        'operation':'import_variant','attempt_count':1,'initial_outcome':'UNKNOWN'}]
    dispatches=[{'scope_key':'original-budget','sequence':1,'request_digest':'a'*64}]
    outcomes=[{'scope_key':'original-budget','sequence':1,'outcome':'ACCEPTED'}]
    original=deepcopy([targets,scopes,reservations,dispatches,outcomes])
    source._partial({'targets':targets},scopes,reservations,dispatches,outcomes)
    for damaged in ([],[dict(outcomes[0],outcome='UNKNOWN')]):
        with pytest.raises(ValueError):source._partial({'targets':targets},scopes,reservations,dispatches,damaged)
    changed=deepcopy(targets);changed[0]['attempts']=1
    with pytest.raises(ValueError):source._partial({'targets':changed},scopes,reservations,dispatches,outcomes)
    changed=deepcopy(targets);changed[-1]['status']='PENDING';changed[-1]['attempts']=0
    with pytest.raises(ValueError):source._partial({'targets':changed},scopes,reservations,dispatches,outcomes)

    document={'schema':'orbit-publication-platform-cancellation/v1','actor':'USER','platform':'OZON',
        'user_message':'ozon不用上品了这个平台全部取消',
        'scope':'Cancel Ozon for current Offer4106902637 and future new-product publication defaults',
        'effect':'NO_FURTHER_OZON_PUBLICATION_IMAGE_OR_STOCK_MUTATIONS',
        'original_decision_id':source.DECISION,'original_candidate_digest':source.CANDIDATE,
        'remaining_platforms':['TIKTOK','SHOPEE'],'existing_listing_delisting_authorized':False,
        'new_image_budget_granted':False,'new_approval_required':False}
    path=tmp_path/'user-cancellation.json';raw=source._json(document).encode();path.write_bytes(raw)
    cancellation=cancel.authority({'path':str(path),'sha256':sha256(raw).hexdigest()})
    assert cancel.eligible_labels(labels,cancellation)==labels[:-1]
    assert cancel.eligible_rows(targets,cancellation)==targets[:-1]
    # A different future new product also inherits this restrictive default.
    assert cancel.eligible_labels(['ozon:RU','tiktok:another-shop'],cancellation)==['tiktok:another-shop']
    assert [targets,scopes,reservations,dispatches,outcomes]==original

    # Verify the appended proof against an actual SQLite history prefix.
    with sqlite3.connect(':memory:') as db:
        db.row_factory=sqlite3.Row
        db.execute('CREATE TABLE workbench_events(id INTEGER PRIMARY KEY,task_id,event_type,detail_json,created_at)')
        db.execute('CREATE TABLE workbench_external_tasks(task_id)')
        db.execute('CREATE TABLE workbench_domain_operations(owner_task_id)')
        db.execute('CREATE TABLE workbench_publication_scope_reductions(task_id)')
        old={'code_version':'old','environment':'stable','manifest_digest':'old-manifest'}
        new={'code_version':'new','environment':'stable','manifest_digest':'new-manifest'}
        grant={'id':1,'detail_json':source._json({'original':'explicit-post'})}
        db.execute('INSERT INTO workbench_events VALUES(?,?,?,?,?)',(1,source.TASK,'explicit_new_post_preparation',grant['detail_json'],'before'))
        scope={'offer_id':source.OFFER,'shops':labels,'skus':['9457']}
        idle={'task_id':source.TASK,'template':'publication','state':'reconciliation_required','step_index':2,
            'external_started':0,'worker':None,'lease_token':None,'lease_until':None,'action_json':None,
            'scope_json':source._json(scope),
            'steps_json':source._json([{'key':key,'state':'completed' if i<2 else 'pending'}
                for i,key in enumerate(('facts','images','release','readback'))]),
            'checkpoint_json':source._json({'native_final_decision':{'decision_id':source.DECISION,
                'plan_id':source.PLAN,'phase':'RECORDED','official_readback_complete':False,'outcome_unknown':False,
                'target_results':[{k:t[k] for k in ('target_label','status','attempts')} for t in targets]}})}
        source._row_ready(db,idle)
        unknown=deepcopy(idle);checkpoint=json.loads(unknown['checkpoint_json'])
        checkpoint['native_final_decision']['outcome_unknown']=True
        unknown['checkpoint_json']=source._json(checkpoint)
        with pytest.raises(ValueError,match='EXACT_IDLE_APPROVED_TASK_REQUIRED'):source._row_ready(db,unknown)
        with pytest.raises(ValueError,match='EXACT_IDLE_APPROVED_TASK_REQUIRED'):
            source._row_ready(db,dict(idle,worker='still-active'))
        original_request={'source_key':'same-key','request_digest':'same-request'}
        row={'task_id':source.TASK,'request_digest':'same-request','scope_json':source._json(scope)}
        record={'schema_version':source.SCHEMA,'task_id':source.TASK,**original_request,'scope':scope,
            'from_release':old,'to_release':new,'checkpoint_raw':'unchanged','steps_raw':'unchanged',
            'registration_state':'reconciliation_required',
            'action_raw':'unchanged','history_sha256':source._digest(source._history(db,source.TASK)),
            'images':{},'predecessors':{},'cancellation':cancellation,
            'business':{'decision_id':source.DECISION,'plan_id':source.PLAN,'candidate_digest':source.CANDIDATE,
                'snapshot_digest':source.SNAPSHOT,'accepted_external_id':source.OZON},
            'source':{'from_head':'old','to_head':'new','manifest_digest':'new-manifest',
                'changed_paths':['M\tshared_platform/native_sole_final_decision.py']},
            'original_post_event_id':1,'original_post_sha256':sha256(grant['detail_json'].encode()).hexdigest(),
            'execution_authority':False}
        def store(value):
            db.execute('INSERT OR REPLACE INTO workbench_events VALUES(?,?,?,?,?)',
                (2,source.TASK,source.EVENT,source._json({'record':value,'record_sha256':source._digest(value)}),'after'))
        store(record)
        assert source.verify(db,row,original_request,grant,old,new)==new
        changed=deepcopy(record);changed['business']['snapshot_digest']='a different approval'
        store(changed)
        with pytest.raises(ValueError,match='ORIGINAL_APPROVAL_CHANGED'):
            source.verify(db,row,original_request,grant,old,new)
        store(record);path.write_bytes(raw+b' ')
        with pytest.raises(ValueError,match='CANCELLATION_AUTHORITY_CHANGED'):
            source.verify(db,row,original_request,grant,old,new)
