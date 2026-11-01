"""Bounded local source fixes for one genuine, still-unfrozen explicit task.

No HTTP producer, new task, approval, frozen-input rewrite or provider receipt.
First issuance requires zero effects. A second known binary-preflight block may
retain exactly one verified terminal read-only OPTIONS, never a capture/child.
Later consumers verify that recorded lineage,
not a demand that normal subsequent work remain forever zero. Same-user local
task ledger is the existing trust boundary, as for the original explicit POST.
"""
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess

EVENT = 'native_unfrozen_source_successor'
SCHEMA = 'native-unfrozen-source-successor/v1'


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return sha256(_json(value).encode()).hexdigest()


def _need(ok, code):
    if not ok:
        raise ValueError('SOURCE_SUCCESSOR_' + code)


def _history(db, task_id, before=None):
    sql = 'SELECT id,event_type,detail_json,created_at FROM workbench_events WHERE task_id=?'
    args = [task_id]
    if before is not None:
        sql += ' AND id<?'; args.append(before)
    return [dict(r) for r in db.execute(sql + ' ORDER BY id', args)]


def verified_explicit_origin(db, row, release):
    """Common admission for worker recovery and the real native v4 consumer."""
    task_id = row['task_id']
    grants = db.execute("SELECT id,detail_json FROM workbench_events WHERE task_id=? "
                        "AND event_type='explicit_new_post_preparation' ORDER BY id", (task_id,)).fetchall()
    _need(len(grants) == 1, 'UNIQUE_ORIGINAL_POST_REQUIRED')
    original = json.loads(grants[0]['detail_json'])
    current_scope = json.loads(row['scope_json'])
    task = db.execute('SELECT source_key FROM workbench_tasks WHERE task_id=?', (task_id,)).fetchone()
    _need(type(original) is dict and set(original) == {'request_digest','source_key','scope','release'}
          and type(original['scope']) is dict and task is not None
          and original['request_digest'] == row['request_digest'] and original['source_key'] == task['source_key']
          and original['scope'].get('offer_id') == current_scope.get('offer_id')
          and original['scope'].get('shops') == current_scope.get('shops')
          and json.loads(row['version_json']) == release, 'ORIGINAL_REQUEST_CHANGED')
    events = db.execute('SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',
                        (task_id, EVENT)).fetchall()
    if not events:
        _need(original['release'] == release, 'REGISTRATION_REQUIRED')
        return original
    _need(1 <= len(events) <= 2, 'MULTIPLE_CHAINS_REJECTED')
    envelopes = [json.loads(event['detail_json']) for event in events]
    _need(len({_digest(value) for value in envelopes}) == len(events), 'MULTIPLE_CHAINS_REJECTED')
    previous_release = original['release']
    for position,(event,envelope) in enumerate(zip(events,envelopes)):
        record = _verify_link(db,row,original,grants[0],event,envelope,previous_release,position)
        previous_release = record['to_release']
    _need(previous_release == release, 'TERMINAL_SOURCE_CHANGED')
    return original


def _verify_link(db,row,original,grant,event,envelope,previous_release,position):
    task_id=row['task_id']
    _need(type(envelope) is dict and set(envelope) == {'record','record_sha256'}, 'RECORD_SHAPE_CHANGED')
    record = envelope['record']
    _need(type(record) is dict and envelope['record_sha256'] == _digest(record), 'RECORD_DIGEST_CHANGED')
    _need(set(record) == {'schema_version','task_id','source_key','request_digest','scope','from_release',
          'to_release','original_post_event_id','original_post_sha256','history_sha256','checkpoint_raw',
          'checkpoint_sha256','steps_json','registration_state','zero_evidence','source','execution_authority'}
          and record['schema_version'] == SCHEMA and record['execution_authority'] is False,
          'RECORD_CONTRACT_CHANGED')
    _need(record['task_id'] == task_id and record['source_key'] == original['source_key']
          and record['request_digest'] == original['request_digest'] and record['scope'] == original['scope']
          and record['from_release'] == previous_release
          and record['from_release'] != record['to_release']
          and record['original_post_event_id'] == grant['id']
          and record['original_post_sha256'] == sha256(grant['detail_json'].encode()).hexdigest()
          and record['history_sha256'] == _digest(_history(db, task_id, event['id']))
          and record['checkpoint_sha256'] == sha256(record['checkpoint_raw'].encode()).hexdigest(),
          'ORIGINAL_LINEAGE_CHANGED')
    _need(record['source']['from_head'] == record['from_release']['code_version']
          and record['source']['to_head'] == record['to_release']['code_version']
          and record['source']['manifest_digest'] == record['to_release']['manifest_digest']
          and record['zero_evidence']['operations'] and record['zero_evidence']['release']
          and record['zero_evidence']['files_absent'] is True, 'REGISTRATION_PROOF_CHANGED')
    proof=record['zero_evidence']
    if position==0:
        _need(not any(proof['operations'].values()) and not any(proof['release'].values())
              and 'confirmed_readonly_options' not in proof,'REGISTRATION_PROOF_CHANGED')
    else:
        _need(record['registration_state']=='failed' and _known_binary_block(json.loads(record['checkpoint_raw']))
              and proof.get('confirmed_readonly_options'), 'EXACT_KNOWN_BINARY_BLOCK_REQUIRED')
        _options_counts(proof['operations'],proof['release'])
        intent=proof['confirmed_readonly_options']['intent']
        _need(intent['task_id']==task_id and intent['action']=='options' and intent['status']=='SUCCEEDED'
              and json.loads(intent['release_json'])==record['from_release']
              and _digest(intent)==proof['confirmed_readonly_options']['intent_sha256'], 'OPTIONS_PROOF_CHANGED')
    # Current checkpoint/lease/paid state may now have advanced legitimately.
    return record


def _known_binary_block(checkpoint):
    attempt=checkpoint.get('facts_attempt') or {}
    return (attempt.get('state')=='returned' and attempt.get('number')==2
            and attempt.get('result')=={'status':'blocked','reason':'readonly_native_binary_capability_unverified'})


def _options_counts(operations,release):
    _need(operations.get('workbench_category_intents')==1
          and all(value==0 for key,value in operations.items() if key!='workbench_category_intents')
          and all(release.get(key)==1 for key in ('round1_category_capture_requests','round1_category_options','round1_category_worker_origins'))
          and all(value==0 for key,value in release.items() if key not in {'round1_category_capture_requests','round1_category_options','round1_category_worker_origins'}),
          'ONLY_TERMINAL_READONLY_OPTIONS_ALLOWED')


def readonly_options_release(engine,db,intent,*,body=None,action='options'):
    """Permit only the exact terminal old OPTIONS sealed in link2; no dispatch grant."""
    value=dict(intent)
    if json.loads(value['release_json'])==engine.release:return engine.release
    _need(action=='options' and value['action']=='options' and value['status']=='SUCCEEDED', 'ONLY_CONFIRMED_OPTIONS_REUSABLE')
    row=engine._row(db,value['task_id'])
    verified_explicit_origin(db,row,engine.release)
    events=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',
                      (value['task_id'],EVENT)).fetchall()
    _need(len(events)==2,'CONFIRMED_OPTIONS_SOURCE_LINK_REQUIRED')
    record=json.loads(events[-1]['detail_json'])['record']
    sealed=record['zero_evidence']['confirmed_readonly_options']
    _need(value==sealed['intent'] and _digest(value)==sealed['intent_sha256']
          and (body is None or _json(body)==value['body_json']), 'OPTIONS_REUSE_BINDING_CHANGED')
    return json.loads(value['release_json'])


def _count(db, table, where, args):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return 0  # Original dispatch requires its durable table/row first.
    return db.execute('SELECT count(*) FROM ' + table + ' WHERE ' + where, args).fetchone()[0]


def _release_zero(store, task_id, offer, intent=None):
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        queries = {
            'round1_category_capture_requests': ('offer_id=?', (offer,)),
            'round1_category_options': ('offer_id=?', (offer,)),
            'round1_category_observations': ('offer_id=?', (offer,)),
            'round1_category_worker_origins': ('task_id=?', (task_id,)),
            'round1_workspace_preparations': ('offer_id=?', (offer,)),
            'approved_publication_snapshots': ('offer_id=?', (offer,)),
            'release_plans': ('product_id=?', (offer,)),
            'release_runs': ('plan_id IN (SELECT plan_id FROM release_plans WHERE product_id=?)', (offer,)),
            'native_sole_final_decisions': ('plan_id IN (SELECT plan_id FROM release_plans WHERE product_id=?)', (offer,)),
            'native_final_budget_scopes': ('plan_id IN (SELECT plan_id FROM release_plans WHERE product_id=?)', (offer,)),
        }
        counts = {t: _count(db, t, *query) for t, query in queries.items()}
    if intent is None:
        _need(not any(counts.values()), 'PREPARATION_OR_DOMAIN_EFFECT_EXISTS')
    return counts


def _seal_options(store,intent,scope,release,revision):
    """Read original immutable domain evidence; no provider or outcome update."""
    from shared_platform.worker_category_admission import request_id
    body=json.loads(intent['body_json']);reference=intent['result_reference']
    _need(intent['action']=='options' and intent['status']=='SUCCEEDED'
          and json.loads(intent['release_json'])==release
          and body['offer_id']==scope['offer_id'] and body['requested_targets']==scope['shops']
          and body['product_center_revision']==revision
          and intent['request_id']==request_id(intent['task_id'],'options',body), 'EXACT_TERMINAL_OPTIONS_REQUIRED')
    original=store.category_capture_request(intent['request_id'],scope['offer_id'],'source-fix-readonly')
    origin=store.category_worker_origin(intent['request_id'])
    options=store.category_options_record(reference,scope['offer_id'])
    progress=store.category_request_progress(intent['request_id'])
    request=json.loads(original['request_json']) if original else {}
    _need(original and original['status']=='SUCCEEDED' and original['observer_reference']==reference
          and all(request.get(key)==value for key,value in body.items())
          and request.get('_ui_request_digest')=='sha256:'+_digest(body)
          and origin and origin['task_id']==intent['task_id'] and origin['purpose']=='OPTIONS'
          and origin['release']==release and origin['ui_request_digest']=='sha256:'+_digest(body)
          and progress['purpose']=='OPTIONS' and progress['attempted']==progress['completed']
          and options['options_digest']==intent['options_digest']
          and options['review_input']['product_center_revision']==revision
          and options['source_region']==body['source_region']
          and options['account_identity_digest']==body['account_identity_digest'], 'ORIGINAL_OPTIONS_READBACK_CHANGED')
    return {'intent':intent,'intent_sha256':_digest(intent),'domain_request_sha256':_digest(original),
            'domain_origin_sha256':_digest(origin),'domain_options_sha256':_digest(options),
            'context':{key:body[key] for key in ('context_digest','account_identity_digest','product_center_revision','source_region')}}


def _files_zero(engine, profile, task_id, offer):
    from shared_platform.native_parent_images import NativeParentImagesAdapter
    from shared_platform.native_task_preparation import NativeR1FilesystemBoundary
    # Reuse the installed original binding; no caller-supplied empty history.
    history, binding_digest = NativeParentImagesAdapter(engine, profile,
        NativeR1FilesystemBoundary(profile))._history_binding()
    for root in {profile.root, history}:
        directory = root/'reports/product-preparation'/offer
        paths = [directory/name for name in ('round1-approved-snapshot.json','round1-auto-decision.json',
            'brand-image-generation.json','brand-image-translation.json','paid-requests',
            'brand-image-checkpoints-lingshi','brand-image-translation-checkpoints')]
        paths += [root/'data'/name/offer for name in ('localized_image_reviews','localized_image_packs')]
        for path in paths:
            _need(not any(p.exists() and (p.is_symlink() or p.is_junction()) for p in (path,*path.parents)),
                  'ARTIFACT_REDIRECTED')
            _need(not path.is_file() and (not path.exists() or not any(path.rglob('*'))),
                  'FROZEN_OR_PAID_ARTIFACT_EXISTS')
    artifacts = profile.data_root/'artifacts'/task_id
    _need(not any(p.exists() and (p.is_symlink() or p.is_junction()) for p in (artifacts,*artifacts.parents)),
          'CHILD_ARTIFACT_REDIRECTED')
    _need(not artifacts.exists() or not any(p.is_file() for p in artifacts.rglob('*')), 'CHILD_ARTIFACT_EXISTS')
    return binding_digest


def register_source_successor(engine, task_id, *, profile, release_store, expected):
    """Trusted local entry: one initial fix and one exact compatibility successor.

    Caller serializes product-input writers and quiesces its owned old worker.
    No task/grant/checkpoint/scope rewrite except current version_json CAS.
    Existing SQLite only: no store._connect/engine.transaction/schema migration.
    """
    from shared_platform.operations_runtime import RuntimeProfile, runtime_matches
    from shared_platform.release_store import ReleaseStore
    _need(type(profile) is RuntimeProfile and type(release_store) is ReleaseStore
          and profile.environment == 'stable' and bool(profile.manifest_digest)
          and engine.store.path.resolve() == (profile.data_root/'tasks.db').resolve(), 'PINNED_LOCAL_RUNTIME_REQUIRED')
    release = {'code_version':profile.version,'environment':profile.environment,'manifest_digest':profile.manifest_digest}
    _need(release == engine.release and runtime_matches(profile), 'CURRENT_SOURCE_CHANGED')
    old = expected['from_release']
    _need(set(old) == set(release) and old['environment'] == 'stable' and old != release, 'EXACT_PREDECESSOR_REQUIRED')
    def git(*args):
        return subprocess.run(['git', *args], cwd=profile.root, capture_output=True, text=True, check=True,
                              timeout=10).stdout.strip()
    git('merge-base','--is-ancestor',old['code_version'],profile.version)
    _need(not git('diff','--name-only','HEAD') and not git('diff','--cached','--name-only'), 'SOURCE_DIRTY')
    changed = git('diff','--name-status',old['code_version'],profile.version)
    _need(bool(changed), 'EMPTY_SOURCE_CHANGE')
    source = {'root':str(profile.root),'from_head':old['code_version'],'to_head':profile.version,
              'manifest_digest':profile.manifest_digest,'changed_paths':changed.splitlines()}
    _need(engine.store.path.is_file() and release_store.path.is_file(), 'EXISTING_DATABASES_REQUIRED')
    db = sqlite3.connect(engine.store.path.resolve().as_uri()+'?mode=rw',uri=True,timeout=3)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        db.execute('BEGIN IMMEDIATE')
        row = engine._row(db,task_id)
        links=db.execute('SELECT id FROM workbench_events WHERE task_id=? AND event_type=?',(task_id,EVENT)).fetchall()
        second=len(links)==1
        _need(len(links)<2,'MULTIPLE_CHAINS_REJECTED')
        task = db.execute('SELECT source_key FROM workbench_tasks WHERE task_id=?',(task_id,)).fetchone()
        _need(row['template']=='publication' and row['state'] in ({'failed'} if second else {'queued','reconciliation_required'})
              and row['step_index']==0 and not row['external_started']
              and all(row[k] is None for k in ('worker','lease_token','lease_until','action_json')),
              'IDLE_PREFACTS_TASK_REQUIRED')
        _need(json.loads(row['version_json']) == old and task['source_key'] == expected['source_key']
              and row['request_digest'] == expected['request_digest']
              and json.loads(row['scope_json']) == expected['scope']
              and expected['scope'].get('skus') == []
              and sha256(row['checkpoint_json'].encode()).hexdigest() == expected['checkpoint_sha256']
              and _digest(_history(db,task_id)) == expected['history_sha256'], 'EXPECTED_ORIGINAL_CHANGED')
        verified_explicit_origin(db,row,old)
        steps=json.loads(row['steps_json']);checkpoint=json.loads(row['checkpoint_json'])
        _need(all(step.get('state')=='pending' for step in steps)
              and set(checkpoint) <= {'native_preparation','facts_attempt'}
              and not (checkpoint.get('native_preparation') or {}).get('prepared_reference'), 'UNFROZEN_FACTS_REQUIRED')
        previous=checkpoint.get('facts_attempt')
        if second:
            _need(_known_binary_block(checkpoint),'EXACT_KNOWN_BINARY_BLOCK_REQUIRED')
        elif previous:
            _need(previous.get('state')=='unknown' and previous.get('number')==1
                  and previous.get('result')=={'status':'unknown','reason':'R1_PARENT_CATEGORY_REQUIRES_RECONCILIATION',
                                              'error_class':'ValueError'}, 'UNRESOLVED_ATTEMPT_NOT_PROVEN_PREFLIGHT')
        operations={t:_count(db,t,'task_id=?',(task_id,)) for t in ('workbench_category_intents',
            'workbench_category_agent_attempts','workbench_external_tasks','workbench_receipts',
            'workbench_review_identity','workbench_publication_scope_reductions')}
        operations['workbench_domain_operations']=_count(db,'workbench_domain_operations','owner_task_id=?',(task_id,))
        if not second:_need(not any(operations.values()), 'ORIGINAL_TASK_EFFECT_EXISTS')
        scope=json.loads(row['scope_json']);offer=scope['offer_id']
        locks=[dict(x) for x in db.execute('SELECT resource,task_id FROM workbench_resource_locks WHERE task_id=? OR resource=?',
                                          (task_id,'product-offer:'+offer))]
        _need(all(x=={'resource':'product-offer:'+offer,'task_id':task_id} for x in locks), 'RESOURCE_OWNER_CHANGED')
        intent=None
        if second:
            intents=db.execute('SELECT * FROM workbench_category_intents WHERE task_id=?',(task_id,)).fetchall()
            _need(len(intents)==1,'ONLY_TERMINAL_READONLY_OPTIONS_ALLOWED')
            intent=dict(intents[0])
        release_counts=_release_zero(release_store,task_id,offer,intent)
        options_proof=None
        if second:
            _options_counts(operations,release_counts)
            options_proof=_seal_options(release_store,intent,scope,old,
                checkpoint['native_preparation']['product_center_revision'])
        history_digest=_files_zero(engine,profile,task_id,offer)
        grant=db.execute("SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type='explicit_new_post_preparation'",(task_id,)).fetchone()
        record={'schema_version':SCHEMA,'task_id':task_id,'source_key':task['source_key'],
            'request_digest':row['request_digest'],'scope':scope,'from_release':old,'to_release':release,
            'original_post_event_id':grant['id'],'original_post_sha256':sha256(grant['detail_json'].encode()).hexdigest(),
            'history_sha256':expected['history_sha256'],'checkpoint_raw':row['checkpoint_json'],
            'checkpoint_sha256':expected['checkpoint_sha256'],'steps_json':row['steps_json'],
            'registration_state':row['state'], 'zero_evidence':{'operations':operations,'release':release_counts,
                'files_absent':True,'paid_history_binding_sha256':history_digest},
            'source':source,'execution_authority':False}
        if options_proof is not None:record['zero_evidence']['confirmed_readonly_options']=options_proof
        changed=db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=? AND version_json=? '
            'AND scope_json=? AND checkpoint_json=? AND steps_json=? AND request_digest=? AND state=? '
            'AND template=\'publication\' AND step_index=0 AND external_started=0 AND worker IS NULL '
            'AND lease_token IS NULL AND lease_until IS NULL AND action_json IS NULL',
            (_json(release),task_id,row['version_json'],row['scope_json'],row['checkpoint_json'],
             row['steps_json'],row['request_digest'],row['state'])).rowcount
        _need(changed==1,'CAS_LOST')
        engine._event(db,task_id,EVENT,{'record':record,'record_sha256':_digest(record)})
        # No state transition, lease, new POST, child or provider is granted.
        db.commit()
        return record
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
