"""One local source successor for a sealed COMMON, before any final grant.

This does not approve a candidate or authorize a second COMMON. The caller
quiesces the original owned worker and product writers; history is append-only.
"""
from dataclasses import asdict
from pathlib import Path
import json
import sqlite3
import subprocess
from functools import lru_cache
import ast

from shared_platform.native_frozen_source_successor import _json,_digest,_history,_stamp

EVENT='native_post_common_source_successor'
SCHEMA='native-post-common-source-successor/v1'


def _need(ok,code):
    if not ok:raise ValueError('POST_COMMON_SOURCE_'+code)


def _events(db,task_id):
    rows=db.execute('SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',
                    (task_id,EVENT)).fetchall()
    _need(len(rows)<=2,'MULTIPLE_SUCCESSORS_REJECTED')
    values=[]
    for row in rows:
        envelope=json.loads(row['detail_json'])
        _need(type(envelope) is dict and set(envelope)=={'record','record_sha256'}
              and envelope['record_sha256']==_digest(envelope['record']),'RECORD_DIGEST_CHANGED')
        values.append((row,envelope['record']))
    return values


def _event(db,task_id):
    values=_events(db,task_id)
    return values[-1] if values else (None,None)


def predecessor_release(db,task_id,current_release):
    """The original first-frozen role stays before the entire postCOMMON chain."""
    values=_events(db,task_id)
    return values[0][1]['from_release'] if values else current_release


def _row_ready(row):
    steps=json.loads(row['steps_json']);action=json.loads(row['action_json'] or '{}')
    _need(row['template']=='publication' and row['state']=='waiting_domain' and row['step_index']==2
          and row['external_started']==0 and all(row[k] is None for k in ('worker','lease_token','lease_until'))
          and action.get('kind')=='observe' and action.get('receipt_binding',{}).get('adapter')=='publication-common/v1',
          'IDLE_COMMON_TECHNICAL_OBSERVATION_REQUIRED')
    _need([s['key'] for s in steps]==['facts','images','release','readback']
          and all(s['state']=='completed' for s in steps[:2])
          and all(s['state']=='pending' for s in steps[2:]),'SEALED_PREDECESSORS_REQUIRED')
    return action['receipt_binding']


@lru_cache(maxsize=32)
def _admission(store_path,run_id,digest):
    # Only immutable bytes addressed by their signed retained digest are cached.
    # _common still verifies the current HMAC/history/budget on every admission.
    with sqlite3.connect(Path(store_path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
        row=db.execute('SELECT * FROM release_runs WHERE run_id=?',(run_id,)).fetchone()
        _need(row is not None and row['technical_admission_digest']==digest,'COMMON_ADMISSION_CHANGED')
        from shared_platform.native_common_technical_execution import _binding
        value=_binding(row)
        return _json(value)


@lru_cache(maxsize=64)
def _registered_git(root,head):
    """Historical immutable Git blobs, never a new source's coverage assertion."""
    def blob(name):return subprocess.check_output(['git','-C',root,'show',head+':'+name])
    tree=ast.parse(blob('shared_platform/native_common_edit_boundary.py').decode('utf-8-sig'))
    names=[ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
           and any(isinstance(t,ast.Name) and t.id=='REGISTERED_SOURCE_PATHS' for t in n.targets)]
    _need(len(names)==1 and type(names[0]) is tuple and len(names[0])==10,'HISTORICAL_COVERAGE_CONTRACT_CHANGED')
    _need(all(type(n) is str and not Path(n).is_absolute() and '..' not in Path(n).parts for n in names[0]),
          'HISTORICAL_COVERAGE_CONTRACT_CHANGED')
    return tuple((name,blob(name)) for name in names[0])


def _source_readiness(db,task_id,accepted,retained,known):
    prior=db.execute('SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type=? AND id<? ORDER BY id DESC',
                     (task_id,EVENT,accepted['id'])).fetchone()
    _need(prior is not None,'COMMON_READY_SOURCE_UNPROVEN')
    envelope=json.loads(prior['detail_json']);record=envelope['record']
    _need(envelope['record_sha256']==_digest(record),'RECORD_DIGEST_CHANGED')
    root=record['source']['root'];release=record['to_release']
    proof={'source_event_id':prior['id'],'source_event_sha256':_digest_bytes(prior['detail_json'].encode()),
           'root':root,'release':release}
    blobs=_registered_git(root,release['code_version'])
    if known is None:
        from shared_platform.operations_runtime import RuntimeProfile,runtime_matches
        _need(runtime_matches(RuntimeProfile(Path(root),Path(root),release['environment'],
                    release['code_version'],release['manifest_digest'])),'ORIGINAL_COVERAGE_SOURCE_NOT_CURRENT')
        files={name:_digest_bytes((Path(root)/name).read_bytes()) for name,_ in blobs}
    else:
        _need(set(known)=={*proof,'files','registered_source_digest','coverage_digest'}
              and all(known[k]==v for k,v in proof.items()),'COMMON_READY_SOURCE_CHANGED')
        files=known['files']
    _need(type(files) is dict and set(files)=={name for name,_ in blobs},'COMMON_READY_SOURCE_FILES_CHANGED')
    for name,raw in blobs:
        lf=raw.replace(b'\r\n',b'\n')
        _need(files[name] in {_digest_bytes(lf),_digest_bytes(lf.replace(b'\n',b'\r\n'))},
              'COMMON_READY_SOURCE_FILES_CHANGED')
    admission=json.loads(_admission(retained['store_path'],retained['completion']['run_id'],retained['technical_admission_digest']))
    registered=_digest(files)
    coverage=_digest({'schema_version':'native-service-common-path-coverage/v1',
        'scope':'THIS_SERVICE_PREPARED_BASELINE_ONLY','registered_source_digest':registered,
        'plan_id':admission['plan_id'],'preparation_root_id':admission['preparation_root_id'],
        'mutation_digest':admission['mutation_sha256'],'observed_schema':'EXACT_NATIVE_TECHNICAL_SCHEMA',
        'provider_edit_permission':'UNKNOWN','external_offer_history':'NOT_ASSERTED'})
    proof.update(files=files,registered_source_digest=registered,coverage_digest=coverage)
    _need(known is None or proof==known,'COMMON_READY_SOURCE_CHANGED')
    return _digest({**admission,'coverage_digest':coverage}),proof


def _common_ready_receipt(db,task_id,binding,retained,*,receipt_key=None,before=None,known=None):
    """Only the original, verified COMMON technical wake-up is non-final.

    Reproduce the original producer's two different JSON encodings. This is
    not permission based on a receipt prefix, nor on today's pending action.
    """
    from shared_platform.operations_publication_common import _digest as common_digest
    from shared_platform.workbench_store import _json as receipt_json
    clause='task_id=?';args=[task_id]
    if receipt_key is not None:clause+=' AND receipt_key=?';args.append(receipt_key)
    receipts=db.execute('SELECT * FROM workbench_receipts WHERE '+clause,args).fetchall()
    _need(len(receipts)<=2,'UNRECOGNIZED_ENGINE_RECEIPT')
    if not receipts:return None
    if receipt_key is None and len(receipts)==2:
        proofs=[_common_ready_receipt(db,task_id,binding,retained,receipt_key=r['receipt_key'],before=before,
                    known=next((p for p in (known if isinstance(known,list) else [])
                                if p['receipt']['receipt_key']==r['receipt_key']),None)) for r in receipts]
        return sorted(proofs,key=lambda p:p['accepted_event_id'])
    receipt=dict(receipts[0]);events=db.execute(
        'SELECT id,event_type,detail_json FROM workbench_events WHERE task_id=?'+
        (' AND id<?' if before is not None else '')+' ORDER BY id',
        (task_id,before) if before is not None else (task_id,)).fetchall()
    accepted=[e for e in events if e['event_type']=='domain_receipt_accepted'
              and json.loads(e['detail_json']).get('receipt_id')==receipt['receipt_key']]
    _need(len(accepted)==1,'COMMON_READY_ACCEPTANCE_UNPROVEN')
    accepted=accepted[0]
    pending=[e for e in events if e['id']<accepted['id'] and e['event_type']=='domain_observation_pending']
    _need(bool(pending),'COMMON_READY_ORIGINAL_ACTION_MISSING')
    pending=pending[-1];action=json.loads(pending['detail_json'])
    _need(action.get('kind')=='observe' and action.get('receipt_binding')==binding
          and type(action.get('action_id')) is str and bool(action['action_id']),
          'COMMON_READY_ORIGINAL_BINDING_CHANGED')
    status='RETAINED_TECHNICAL_BASELINE';evidence=retained['technical_admission_digest']
    key='common-ready:'+common_digest([binding,status,evidence])
    coverage=None
    if receipt['receipt_key']!=key:
        evidence,coverage=_source_readiness(db,task_id,accepted,retained,(known or {}).get('readiness_source'))
        key='common-ready:'+common_digest([binding,status,evidence])
    original={'receipt_id':key,'action_id':action['action_id']}
    _need(receipt['receipt_key']==key and receipt['digest']==_digest_bytes(receipt_json(original).encode()),
          'UNRECOGNIZED_ENGINE_RECEIPT')
    proof={'receipt':receipt,'action_event_id':pending['id'],
            'action_event_sha256':_digest_bytes(pending['detail_json'].encode()),
            'accepted_event_id':accepted['id'],
            'accepted_event_sha256':_digest_bytes(accepted['detail_json'].encode()),
            'common_status':status,'technical_admission_digest':evidence}
    if coverage is not None:proof['readiness_source']=coverage
    return proof


def _ops(db,row,retained,known=None):
    from shared_platform.native_unfrozen_source_successor import _count
    from shared_platform.workbench_store import _json as resource_json
    task_id=row['task_id'];scope=json.loads(row['scope_json'])
    values={table:_count(db,table,'task_id=?',(task_id,)) for table in
        ('workbench_external_tasks','workbench_review_identity','workbench_publication_scope_reductions')}
    _need(not any(values.values()),'FINAL_OR_EXTERNAL_AUTHORITY_EXISTS')
    operations=[dict(r) for r in db.execute('SELECT * FROM workbench_domain_operations WHERE owner_task_id=?',(task_id,))]
    _need(not operations,'TASK_DOMAIN_OPERATION_EXISTS')
    for shop in scope['shops']:
        for sku in scope['skus']:
            resource='product:'+resource_json([shop,sku])
            _need(not db.execute('SELECT 1 FROM workbench_domain_locks WHERE resource=?',(resource,)).fetchone()
                  and not db.execute('SELECT 1 FROM workbench_resource_locks WHERE resource=? AND task_id<>?',
                                     (resource,task_id)).fetchone(),'COMPETING_PRODUCT_WRITER')
    receipt=_common_ready_receipt(db,task_id,json.loads(row['action_json'])['receipt_binding'],retained,known=known)
    return values,receipt


def _common(store,plan_id):
    """Real same-snapshot reader verifies stored transport/HMAC/acceptance."""
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    from shared_platform.native_common_retained_completion import read_retained_completion
    from shared_platform.native_common_budget_facts import census_same_snapshot
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        plan=db.execute('SELECT * FROM release_plans WHERE plan_id=?',(plan_id,)).fetchone()
        _need(plan is not None,'ORIGINAL_PLAN_MISSING')
        payload=json.loads(plan['payload_json'])
        runs=db.execute('SELECT * FROM release_runs WHERE plan_id IN '
            '(SELECT plan_id FROM release_plans WHERE product_id=? AND target_labels_json=?)',
            (plan['product_id'],'["miaoshou:COMMON"]')).fetchall()
        _need(len(runs)==1 and runs[0]['plan_id']==plan_id and runs[0]['approval_id'] is None
              and runs[0]['status']=='SUCCEEDED' and runs[0]['technical_execution_state']=='CONFIRMED_WRITE',
              'SINGLE_TERMINAL_COMMON_REQUIRED')
        reader=NativeCommonSourceReader(store)
        completion=read_retained_completion(reader,db,plan_id,runs[0]['run_id'])
        facts=reader.read_source_facts(db,plan_id)
        census=census_same_snapshot(store,db,payload,facts)
        _need(census['status']=='LOCAL_PERSISTED_CENSUS' and census['local_observed_confirmed_writes']==1
              and census['local_observed_readonly_reuses']==0 and not census['unresolved_attempts']
              and not census['unclassified_history'],'COMMON_BUDGET_OR_OUTCOME_UNRESOLVED')
        target=db.execute('SELECT attempts FROM release_target_runs WHERE run_id=?',(runs[0]['run_id'],)).fetchone()
        _need(target['attempts']==1,'SINGLE_ORIGINAL_COMMON_ATTEMPT_REQUIRED')
        records=[{'table':r.table,'identity':list(r.identity),'record_sha256':_digest_bytes(r.record_bytes)}
                 for r in facts.records]
        return {'store_path':str(store.path.resolve()),'plan_id':plan_id,'payload_digest':plan['payload_digest'],
                'completion':asdict(completion),'records':records,'census':census,
                'technical_admission_digest':runs[0]['technical_admission_digest']}


def _digest_bytes(raw):
    from hashlib import sha256
    return sha256(raw).hexdigest()


def _release_zero(store,offer,common_plan_id):
    from shared_platform.native_unfrozen_source_successor import _count
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        values={name:_count(db,name,where,args) for name,where,args in (
            ('approved_publication_snapshots','offer_id=?',(offer,)),
            ('release_approvals','plan_id IN (SELECT plan_id FROM release_plans WHERE product_id=?)',(offer,)),
            ('native_sole_final_decisions','plan_id IN (SELECT plan_id FROM release_plans WHERE product_id=?)',(offer,)),
            ('native_final_budget_scopes','plan_id IN (SELECT plan_id FROM release_plans WHERE product_id=?)',(offer,)),
            ('release_runs','plan_id IN (SELECT plan_id FROM release_plans WHERE product_id=?) AND plan_id<>?',
             (offer,common_plan_id)))}
        _need(not any(values.values()),'FINAL_OR_CHANNEL_EFFECT_EXISTS')
        plans=db.execute('SELECT target_labels_json,approved_at,status FROM release_plans WHERE product_id=?',(offer,)).fetchall()
        _need(all(p['approved_at'] is None and p['status']=='PENDING_APPROVAL' for p in plans),
              'APPROVED_OR_CHANGED_PLAN_EXISTS')
    return values


def observe(engine,task_id,*,profile,release_store,retained_receipts=None):
    from shared_platform.operations_runtime import RuntimeProfile
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform.release_store import ReleaseStore,default_release_store
    from shared_platform.native_unfrozen_source_successor import verified_explicit_origin
    from shared_platform import native_frozen_source_successor as first,workbench_publication_native as native
    from shared_platform import operations_publication_common as common
    _need(type(engine) is WorkbenchEngine and type(profile) is RuntimeProfile and type(release_store) is ReleaseStore
          and release_store.path.resolve()==default_release_store().path.resolve()
          and engine.store.path.resolve()==(profile.data_root/'tasks.db').resolve()
          and engine.release=={'code_version':profile.version,'environment':profile.environment,
                              'manifest_digest':profile.manifest_digest},'EXACT_NORMAL_BINDINGS_REQUIRED')
    with engine.readonly_snapshot() as db:
        row=engine._row(db,task_id);binding=_row_ready(row)
        _need(len(_events(db,task_id))<2,'ALREADY_REGISTERED')
        original=verified_explicit_origin(db,row,engine.release)
        old=db.execute('SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type=?',
                       (task_id,first.EVENT)).fetchall()
        _need(len(old)==1,'ORIGINAL_FROZEN_SOURCE_REQUIRED')
        predecessor=first.verify_native_owner(db,row,engine.release,profile)
        task=engine._project(db,row);history=_digest(_history(db,task_id))
    images=native.read_images(task,profile)
    _need(images==predecessor['images'],'FROZEN_PREDECESSORS_CHANGED')
    plan=release_store.get_plan(binding['plan_id'])
    _need(common._verified_binding(task,{'plan':plan},profile)==binding,'ORIGINAL_PENDING_BINDING_CHANGED')
    retained=_common(release_store,binding['plan_id'])
    _need(retained['payload_digest']==binding['payload_digest']
          and retained['completion']['offer_id']==task['scope']['offer_id'],'ORIGINAL_COMMON_BINDING_CHANGED')
    with engine.readonly_snapshot() as db:
        latest=engine._row(db,task_id)
        _need(dict(latest)==dict(row) and _digest(_history(db,task_id))==history,'ORIGINAL_TASK_CHANGED_DURING_OBSERVATION')
        ops,receipt=_ops(db,latest,retained,retained_receipts)
    return {'task_id':task_id,'source_key':original['source_key'],'request_digest':row['request_digest'],
        'scope':task['scope'],'from_release':engine.release,'checkpoint_raw':row['checkpoint_json'],
        'steps_raw':row['steps_json'],'action_raw':row['action_json'],'history_sha256':history,
        'original_frozen_event_id':old[0]['id'],'original_frozen_event_sha256':_digest_bytes(old[0]['detail_json'].encode()),
        'predecessors':predecessor,'common':retained,'common_ready_receipt':receipt,
        'zero_final':{'operations':ops,'release':_release_zero(release_store,task['scope']['offer_id'],binding['plan_id'])}}


def verify(db,row,original,grant,from_release,to_release):
    """At most two exact same-phase links, preserving every original record."""
    values=_events(db,row['task_id'])
    if not values:return from_release
    previous=from_release;baseline=values[0][1]
    for event,record in values:
        _need(record['from_release']==previous,'SOURCE_LINEAGE_CHANGED')
        _need(record['common']==baseline['common']
              and {k:v for k,v in record['predecessors'].items() if k!='post_common'}==
                  {k:v for k,v in baseline['predecessors'].items() if k!='post_common'},
              'ORIGINAL_SEALED_PREDECESSORS_CHANGED')
        previous=_verify_link(db,row,original,grant,event,record,previous,record['to_release'])
    _need(previous==to_release,'SOURCE_LINEAGE_CHANGED')
    return previous


def _verify_link(db,row,original,grant,event,record,from_release,to_release):
    """Each admission's own original history/CAS proof remains mandatory."""
    _need(set(record)=={'schema_version','task_id','source_key','request_digest','scope','from_release','to_release',
        'checkpoint_raw','steps_raw','action_raw','history_sha256','original_frozen_event_id','original_frozen_event_sha256',
        'predecessors','common','common_ready_receipt','zero_final','source','execution_authority'} and record['schema_version']==SCHEMA
        and record['execution_authority'] is False,'RECORD_CONTRACT_CHANGED')
    _need(record['task_id']==row['task_id'] and record['source_key']==original['source_key']
          and record['request_digest']==row['request_digest']==original['request_digest']
          and record['scope']==json.loads(row['scope_json']),'ORIGINAL_TASK_SCOPE_CHANGED')
    _need(record['from_release']==from_release and record['to_release']==to_release and from_release!=to_release
          and record['source']['from_head']==from_release['code_version']
          and record['source']['to_head']==to_release['code_version']
          and record['source']['manifest_digest']==to_release['manifest_digest'],'SOURCE_LINEAGE_CHANGED')
    _need(record['history_sha256']==_digest(_history(db,row['task_id'],event['id'])),'ORIGINAL_HISTORY_CHANGED')
    from shared_platform.native_frozen_source_successor import EVENT as first_event
    prior=db.execute('SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type=?',
                     (row['task_id'],first_event)).fetchall()
    _need(len(prior)==1 and prior[0]['id']==record['original_frozen_event_id']
          and _digest_bytes(prior[0]['detail_json'].encode())==record['original_frozen_event_sha256'],
          'ORIGINAL_FROZEN_SOURCE_CHANGED')
    _need(not any(record['zero_final']['operations'].values()) and not any(record['zero_final']['release'].values()),
          'REGISTRATION_WAS_NOT_PREFINAL')
    pending=json.loads(record['action_raw']);common=record['common'];r1=record['predecessors']['images']['native_r1']
    if record['common_ready_receipt'] is not None:
        proofs=record['common_ready_receipt'] if isinstance(record['common_ready_receipt'],list) else [record['common_ready_receipt']]
        for proof in proofs:
            _need(_common_ready_receipt(db,row['task_id'],pending['receipt_binding'],common,
                receipt_key=proof['receipt']['receipt_key'],before=event['id'],known=proof)==proof,
                'ORIGINAL_COMMON_READY_RECEIPT_CHANGED')
    steps=json.loads(record['steps_raw'])
    _need(pending['kind']=='observe' and pending['receipt_binding']['adapter']=='publication-common/v1'
          and pending['receipt_binding']['plan_id']==common['plan_id']
          and pending['receipt_binding']['payload_digest']==common['payload_digest']
          and common['completion']['offer_id']==record['scope']['offer_id']==r1['offer_id']
          and common['completion']['state']=='CONFIRMED_WRITE'
          and steps[0]['checkpoint']['native_r1']==r1
          and steps[1]['checkpoint']['native_r2']==record['predecessors']['images']['native_r2'],
          'REGISTERED_PREDECESSOR_BINDING_CHANGED')
    return to_release


def retained_seal(db,task_id):
    _,record=_event(db,task_id)
    return record['common'] if record is not None else None


def verify_retained(seal):
    if seal is None:return
    from shared_platform.release_store import default_release_store
    store=default_release_store()
    _need(str(store.path.resolve())==seal['store_path'] and _common(store,seal['plan_id'])==seal,
          'RETAINED_COMMON_CHANGED')


def register(engine,task_id,*,profile,release_store,expected):
    """One idle local CAS; no COMMON request, approval, checkpoint rewrite/DDL."""
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform.operations_runtime import RuntimeProfile,runtime_matches
    from shared_platform.release_store import ReleaseStore
    _need(type(engine) is WorkbenchEngine and type(profile) is RuntimeProfile and type(release_store) is ReleaseStore
          and profile.environment=='stable' and runtime_matches(profile)
          and engine.release=={'code_version':profile.version,'environment':profile.environment,
                              'manifest_digest':profile.manifest_digest},'CURRENT_PINNED_SOURCE_REQUIRED')
    with engine.readonly_snapshot() as db:
        _need(len(_events(db,task_id))<2
              and json.loads(engine._row(db,task_id)['version_json'])==expected['from_release'],'ALREADY_REGISTERED')
    old=expected['from_release'];producer=RuntimeProfile(profile.root,profile.data_root,
        old['environment'],old['code_version'],old['manifest_digest'])
    _need(observe(WorkbenchEngine(engine.store.path,old),task_id,profile=producer,release_store=release_store,
                  retained_receipts=expected['common_ready_receipt'])==expected,
          'EXPECTED_ORIGINAL_OBSERVATION_CHANGED')
    git=lambda *args:subprocess.check_output(['git','-C',str(profile.root),*args],text=True).strip()
    git('merge-base','--is-ancestor',old['code_version'],profile.version)
    _need(old!=engine.release and not git('status','--porcelain=v1','-uall'),'CLEAN_DESCENDANT_REQUIRED')
    changes=git('diff','--name-status',old['code_version'],profile.version).splitlines();_need(changes,'SOURCE_CHANGE_REQUIRED')
    watched={Path(f['path']) for f in expected['predecessors']['files']}
    watched|={release_store.path,*(release_store.path.with_name(release_store.path.name+s) for s in ('-wal','-shm','-journal'))}
    stamps={p:_stamp(p) for p in watched}
    from shared_platform.native_task_preparation import NativeR1FilesystemBoundary
    history_root=Path(expected['predecessors']['paid']['directory']).parents[3]
    with NativeR1FilesystemBoundary(profile)._pin(sorted({Path(f['path']).parent for f in expected['predecessors']['files']},key=str),
                                                 release_parent=history_root):
        db=sqlite3.connect(engine.store.path.resolve().as_uri()+'?mode=rw',uri=True,timeout=3);db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE');row=engine._row(db,task_id);_row_ready(row)
            _need(json.loads(row['version_json'])==old and row['checkpoint_json']==expected['checkpoint_raw']
                  and row['steps_json']==expected['steps_raw'] and row['action_json']==expected['action_raw']
                  and json.loads(row['scope_json'])==expected['scope'] and row['request_digest']==expected['request_digest']
                  and _digest(_history(db,task_id))==expected['history_sha256'],'FINAL_ORIGINAL_CAS_CHANGED')
            from shared_platform.native_unfrozen_source_successor import verified_explicit_origin
            verified_explicit_origin(db,row,old)
            _need(_ops(db,row,expected['common'],expected['common_ready_receipt'])==
                  (expected['zero_final']['operations'],expected['common_ready_receipt']),'FINAL_EFFECTS_CHANGED')
            _need(all(_stamp(p)==s for p,s in stamps.items()),'OBSERVED_GENERATION_CHANGED')
            record={**expected,'schema_version':SCHEMA,'to_release':engine.release,'execution_authority':False,
                'source':{'root':str(profile.root),'from_head':old['code_version'],'to_head':profile.version,
                          'manifest_digest':profile.manifest_digest,'changed_paths':changes}}
            changed=db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=? AND version_json=? '
                'AND checkpoint_json=? AND steps_json=? AND action_json=? AND scope_json=? AND state=? '
                'AND worker IS NULL AND lease_token IS NULL AND lease_until IS NULL AND external_started=0',
                (_json(engine.release),task_id,row['version_json'],row['checkpoint_json'],row['steps_json'],
                 row['action_json'],row['scope_json'],'waiting_domain')).rowcount
            _need(changed==1,'VERSION_CAS_LOST')
            engine._event(db,task_id,EVENT,{'record':record,'record_sha256':_digest(record)})
            verified_explicit_origin(db,engine._row(db,task_id),engine.release)
            _need(all(_stamp(p)==s for p,s in stamps.items()),'OBSERVED_GENERATION_CHANGED')
            db.commit();return record
        except BaseException:db.rollback();raise
        finally:db.close()
