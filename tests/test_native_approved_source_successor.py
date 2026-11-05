"""One local contract regression: retain known work, only remove eligibility.

No actor fixture, maintenance registration, production DB or transport is used.
"""
from copy import deepcopy
from hashlib import sha256
import json
import sqlite3

import pytest

from shared_platform import native_approved_source_successor as source
from shared_platform import native_publication_cancellation as cancel


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
