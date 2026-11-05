"""One explicitly maintained code-only continuation of SKU9457's saved decision.

No schema, approval, retry, provider, run or budget writer is provided here.
The local caller first retires this task's owned executors. Historical business
rows stay literal; subsequent execution still enters the original native gates.
"""
from contextlib import closing, ExitStack
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess

from shared_platform.native_frozen_source_successor import _json, _digest, _history, _stamp

EVENT = 'native_approved_source_code_successor'
SCHEMA = 'native-approved-source-code-successor/v1'
TASK = 'TASK-20261104-001'
OFFER = '4106902637'
PLAN = 'r3-marketplace:4106902637:eb83bb1188db53eaac4d4dac'
DECISION = 'native-final-decision:663942dec46b7dababa36a8a5dea58933cee7983e5e695a7a474abc52bef8aaf'
CANDIDATE = 'fc9e164be2d75c5f9a9021d10de0bce6e75045056e7a0fe37e339ae043dd5cab'
SNAPSHOT = 'sha256:486fb40bd07d3fc1c369ebbc8d787db7238aaa50cab42e3849eee9e6130f8767'
OZON = 'ozon:import-task:5966602063'
ALLOWED_FILES = frozenset({
    'scripts/orbit_publish.py',
    'web/static/product_workspace_flow.js',
    'shared_platform/native_approved_source_successor.py',
    'shared_platform/native_unfrozen_source_successor.py',
    'shared_platform/native_sole_final_decision.py',
    'shared_platform/native_publication_cancellation.py',
    'shared_platform/native_sole_final_execution.py',
    'shared_platform/native_sole_final_service.py',
    'shared_platform/native_sole_final_readback.py',
    'shared_platform/native_sole_final_tasks.py',
    'shared_platform/native_task_preparation.py',
    'tests/test_native_approved_source_successor.py',
    'tests/test_native_sole_final_decision.py',
})


def _need(ok, code):
    if not ok: raise ValueError('APPROVED_SOURCE_' + code)


def _event(db, task_id):
    rows = db.execute('SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type=?',
                      (task_id, EVENT)).fetchall()
    _need(len(rows) <= 1, 'ONE_CONTINUATION_ONLY')
    if not rows: return None, None
    envelope = json.loads(rows[0]['detail_json'])
    _need(set(envelope) == {'record','record_sha256'}
          and envelope['record_sha256'] == _digest(envelope['record']), 'RECORD_DIGEST_CHANGED')
    return rows[0], envelope['record']


def predecessor_release(db, task_id, current_release):
    _, record = _event(db, task_id)
    return record['from_release'] if record is not None else current_release


def _row_ready(db, row):
    steps = json.loads(row['steps_json']); action = json.loads(row['action_json'] or '{}')
    scope = json.loads(row['scope_json'])
    final = json.loads(row['checkpoint_json']).get('native_final_decision') or {}
    retained = final.get('target_results') or []
    known_idle_reconciliation = (row['state'] == 'reconciliation_required' and row['action_json'] is None
        and final.get('decision_id') == DECISION and final.get('plan_id') == PLAN
        and final.get('phase') == 'RECORDED' and final.get('official_readback_complete') is False
        and final.get('outcome_unknown') is False and len(retained) == 15
        and len({r['target_label'] for r in retained}) == 15
        and all(r['status'] == ('SUBMITTED_UNVERIFIED' if r['target_label']=='ozon:RU' else 'PENDING')
                and r['attempts'] == (1 if r['target_label']=='ozon:RU' else 0) for r in retained)
        and any(r['target_label']=='ozon:RU' for r in retained))
    original_observation = (row['state'] == 'waiting_domain' and action.get('kind') == 'observe'
        and action.get('action_id') == 'native-final:' + DECISION)
    _need(row['task_id'] == TASK and row['template'] == 'publication'
          and (original_observation or known_idle_reconciliation) and row['step_index'] == 2
          and row['external_started'] == 0
          and all(row[k] is None for k in ('worker','lease_token','lease_until'))
          and scope['offer_id'] == OFFER and scope['skus'] == ['9457'] and len(scope['shops']) == 15,
          'EXACT_IDLE_APPROVED_TASK_REQUIRED')
    _need([s['key'] for s in steps] == ['facts','images','release','readback']
          and all(s['state'] == 'completed' for s in steps[:2])
          and all(s['state'] == 'pending' for s in steps[2:]), 'ORIGINAL_STEPS_CHANGED')
    _need(not db.execute('SELECT 1 FROM workbench_external_tasks WHERE task_id=?',(TASK,)).fetchone()
          and not db.execute('SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=?',(TASK,)).fetchone()
          and not db.execute('SELECT 1 FROM workbench_publication_scope_reductions WHERE task_id=?',(TASK,)).fetchone(),
          'EXTERNAL_OPERATION_OR_SCOPE_CHANGE')


def _partial(run, scopes, reservations, dispatches, outcomes):
    """The retained ACCEPTED import is known work, never a zero-write claim."""
    rows = run['targets']; ozon = [r for r in rows if r['target_label'] == 'ozon:RU']
    pending = [r for r in rows if r['target_label'] != 'ozon:RU']
    _need(len(rows) == 15 and len(ozon) == 1 and len(pending) == 14
          and len({r['target_label'] for r in rows}) == 15, 'TARGET_COVERAGE_CHANGED')
    _need(all(r['status'] == 'PENDING' and r['attempts'] == 0 and not r.get('external_id')
              and not any(r.get(k) for k in ('submission','readback','failure_events')) for r in pending),
          'UNSENT_TARGET_CHANGED')
    target = ozon[0]; submission = target.get('submission') or {}; evidence = submission.get('evidence') or {}
    _need(target['status'] == 'SUBMITTED_UNVERIFIED' and target['attempts'] == 1
          and target['external_id'] == submission.get('external_id') == OZON
          and evidence.get('schema_version') == 'native-scoped-v4-target-result/v1'
          and evidence.get('accepted') is True and evidence.get('outcome_unknown') is False
          and evidence.get('external_writes_performed') == ['ozon:RU']
          and not target.get('readback') and not target.get('failure_events'), 'ORIGINAL_ACCEPTED_IMPORT_CHANGED')
    _need(len(scopes) == len(reservations) == len(dispatches) == len(outcomes) == 1,
          'ORIGINAL_BUDGET_CARDINALITY_CHANGED')
    scope, reservation, dispatch, outcome = scopes[0], reservations[0], dispatches[0], outcomes[0]
    binding = json.loads(scope['binding_json'])
    _need(scope['platform'] == 'OZON' and scope['plan_id'] == PLAN and scope['snapshot_digest'] == SNAPSHOT
          and sha256(scope['binding_json'].encode()).hexdigest() == scope['binding_sha256']
          and binding['decision_id'] == DECISION and binding['candidate_digest'] == CANDIDATE
          and all(r['scope_key'] == scope['scope_key'] for r in (reservation,dispatch,outcome))
          and reservation['sequence'] == dispatch['sequence'] == outcome['sequence'] == 1
          and reservation['budget_scope'] == 'shared' and reservation['attempt_count'] == 1
          and reservation['target_label'] == 'ozon:RU'
          and reservation['operation'] == 'import_variant' and reservation['initial_outcome'] == 'UNKNOWN'
          and len(dispatch['request_digest']) == 64
          and outcome['outcome'] == 'ACCEPTED', 'ORIGINAL_ACCEPTED_BUDGET_CHANGED')


def _business(store, db):
    from shared_platform.native_sole_final_decision import NativeSoleFinalDecisionService, _frozen, check_schema
    from shared_platform.local_operator_session import current_windows_owner_sid
    from shared_platform.release_store import _plan_from_row
    from shared_platform.r3_frozen_review_producer import read_stored_domain_graph
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    check_schema(db)
    # This existing method reads the atomic approval relation only. No actor
    # reader, actor dictionary, READ RPC or execution scope is manufactured.
    receipt = NativeSoleFinalDecisionService(store,None)._decision(db,DECISION,owner_sid=current_windows_owner_sid())
    decision = db.execute('SELECT * FROM native_sole_final_decisions WHERE decision_id=?',(DECISION,)).fetchone()
    plan = _plan_from_row(db.execute('SELECT * FROM release_plans WHERE plan_id=?',(PLAN,)).fetchone())
    review = _frozen(decision['review_json'])
    _need(decision['plan_id'] == plan['plan_id'] == PLAN and plan['product_id'] == OFFER
          and plan['seller_sku'] == '9457' and receipt['snapshot_digest'] == SNAPSHOT
          and review.candidate_digest == CANDIDATE and sorted(plan['targets']) == sorted(review.targets),
          'ORIGINAL_DECISION_CHANGED')
    graph = read_stored_domain_graph(db,PLAN,native_reader=NativeCommonSourceReader(store))
    _need((graph.offer_id,graph.candidate_digest,graph.round1_digest,graph.round2_digest) ==
          (OFFER,CANDIDATE,review.round1_digest,review.round2_digest), 'ORIGINAL_FROZEN_GRAPH_CHANGED')
    run_id = 'release-run:' + plan['payload_digest'][:24]
    runs = db.execute('SELECT run_id FROM release_runs WHERE plan_id=?',(PLAN,)).fetchall()
    _need(len(runs) == 1 and runs[0]['run_id'] == run_id, 'ORIGINAL_RUN_CHANGED')
    run = store._run_in_transaction(db,run_id)
    scopes = [dict(r) for r in db.execute('SELECT * FROM native_final_budget_scopes WHERE plan_id=?',(PLAN,))]
    budgets = [[dict(r) for r in db.execute('SELECT * FROM native_final_budget_'+name+
        ' WHERE scope_key IN (SELECT scope_key FROM native_final_budget_scopes WHERE plan_id=?)',(PLAN,))]
        for name in ('reservations','dispatches','outcomes')]
    _partial(run,scopes,*budgets)
    # Seal all same-Offer historical rows, not just the latest target display.
    plan_ids = [r[0] for r in db.execute('SELECT plan_id FROM release_plans WHERE product_id=?',(OFFER,))]
    run_ids = [r[0] for r in db.execute('SELECT run_id FROM release_runs WHERE plan_id IN ('+
        ','.join('?'*len(plan_ids))+')',plan_ids)]
    keys = {'offer_id':[OFFER],'product_id':[OFFER],'plan_id':plan_ids,'run_id':run_ids,
            'scope_key':[r['scope_key'] for r in scopes]}
    sealed = []
    for table, sql in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        columns = {r[1] for r in db.execute('PRAGMA table_info("'+table+'")')}
        fields = sorted(columns & keys.keys())
        if not fields: continue
        clauses = []; args = []
        for field in fields:
            if keys[field]:
                clauses.append('"'+field+'" IN ('+','.join('?'*len(keys[field]))+')'); args += keys[field]
        if not clauses: continue
        rows = [dict(r) for r in db.execute('SELECT * FROM "'+table+'" WHERE '+' OR '.join(clauses),args)]
        sealed.append({'table':table,'schema_sha256':sha256(sql.encode()).hexdigest(),
                       'count':len(rows),'rows_sha256':_digest(sorted(rows,key=_json))})
    return {'decision_id':DECISION,'plan_id':PLAN,'candidate_digest':CANDIDATE,
            'snapshot_digest':SNAPSHOT,'run_id':run_id,'accepted_external_id':OZON,
            'release_store_path':str(store.path.resolve()),'receipt':receipt,'historical_rows':sealed,
            'target_results':[{'target_label':r['target_label'],'status':r['status'],'attempts':r['attempts']}
                              for r in run['targets']]}


def observe(engine, task_id, *, profile, release_store, cancellation_authority):
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform.operations_runtime import RuntimeProfile
    from shared_platform.release_store import ReleaseStore, default_release_store
    from shared_platform.native_unfrozen_source_successor import verified_explicit_origin
    from shared_platform.native_frozen_source_successor import verify_native_owner, verify_native_files, _retained_producer
    from shared_platform import workbench_publication_native as native
    from shared_platform.native_publication_cancellation import authority
    cancellation = authority(cancellation_authority)
    _need(type(engine) is WorkbenchEngine and type(profile) is RuntimeProfile
          and type(release_store) is ReleaseStore and task_id == TASK
          and release_store.path.resolve() == default_release_store().path.resolve()
          and engine.store.path.resolve() == (profile.data_root/'tasks.db').resolve()
          and engine.release == {'code_version':profile.version,'environment':profile.environment,
                                'manifest_digest':profile.manifest_digest}, 'EXACT_SOURCE_CONFIG_DATA_REQUIRED')
    with engine.readonly_snapshot() as db:
        row = engine._row(db,TASK); _row_ready(db,row)
        _need(_event(db,TASK)[0] is None,'ALREADY_REGISTERED')
        verified_explicit_origin(db,row,engine.release)
        task = engine._project(db,row)
        source_key = db.execute('SELECT source_key FROM workbench_tasks WHERE task_id=?',(TASK,)).fetchone()[0]
        history = _digest(_history(db,TASK))
        seal = verify_native_owner(db,row,engine.release,profile)
    _need(seal is not None,'ORIGINAL_NATIVE_SOURCE_SEAL_REQUIRED')
    verify_native_files(seal,media=False)
    with _retained_producer(task,profile): images = native.read_images(task,profile)
    _need(images['native_r1'] == task['steps'][0]['checkpoint']['native_r1']
          and images['native_r2'] == task['steps'][1]['checkpoint']['native_r2'], 'ORIGINAL_IMAGES_CHANGED')
    with closing(release_store._connect_readonly()) as db:
        db.execute('BEGIN'); business = _business(release_store,db)
    if row['state'] == 'reconciliation_required':
        final = json.loads(row['checkpoint_json'])['native_final_decision']
        _need(final['approval_id'] == business['receipt']['release_approval_id']
              and sorted(final['target_results'],key=_json) == sorted(business['target_results'],key=_json),
              'KNOWN_IDLE_CHECKPOINT_CHANGED')
    return {'task_id':TASK,'source_key':source_key,'request_digest':row['request_digest'],
            'scope':task['scope'],'from_release':engine.release,'registration_state':row['state'],
            'checkpoint_raw':row['checkpoint_json'],
            'steps_raw':row['steps_json'],'action_raw':row['action_json'],'history_sha256':history,
            'images':images,'predecessors':seal,'business':business,'cancellation':cancellation}


def verify(db, row, original, grant, from_release, to_release):
    event, record = _event(db,row['task_id'])
    if record is None: return from_release
    _need(set(record) == {'schema_version','task_id','source_key','request_digest','scope',
          'from_release','to_release','checkpoint_raw','steps_raw','action_raw','history_sha256',
          'images','predecessors','business','cancellation','source','original_post_event_id','registration_state',
          'original_post_sha256','execution_authority'}
          and record['schema_version'] == SCHEMA and record['execution_authority'] is False
          and record['task_id'] == row['task_id'] == TASK
          and record['source_key'] == original['source_key']
          and record['request_digest'] == original['request_digest'] == row['request_digest']
          and record['scope'] == json.loads(row['scope_json'])
          and record['from_release'] == from_release and record['to_release'] == to_release
          and record['registration_state'] in {'waiting_domain','reconciliation_required'}
          and from_release != to_release, 'ORIGINAL_TASK_OR_SOURCE_CHANGED')
    _need(record['original_post_event_id'] == grant['id']
          and record['original_post_sha256'] == sha256(grant['detail_json'].encode()).hexdigest()
          and record['history_sha256'] == _digest(_history(db,TASK,event['id'])), 'ORIGINAL_HISTORY_CHANGED')
    _need(record['business']['decision_id'] == DECISION and record['business']['plan_id'] == PLAN
          and record['business']['candidate_digest'] == CANDIDATE
          and record['business']['snapshot_digest'] == SNAPSHOT
          and record['business']['accepted_external_id'] == OZON
          and record['source']['from_head'] == from_release['code_version']
          and record['source']['to_head'] == to_release['code_version']
          and record['source']['manifest_digest'] == to_release['manifest_digest'], 'ORIGINAL_APPROVAL_CHANGED')
    from shared_platform.native_publication_cancellation import authority
    _need(record['cancellation'] == authority(record['cancellation']['authority']), 'CANCELLATION_RECORD_CHANGED')
    changes = record['source']['changed_paths']
    _need(changes and all(r.split('\t')[0] in {'A','M'} and r.split('\t')[1] in ALLOWED_FILES
                         for r in changes), 'ONLY_REVIEWED_CODE_DELTA_REQUIRED')
    return to_release


def register(engine, task_id, *, profile, release_store, expected):
    """Trusted one-shot local maintenance: one version CAS and appended event."""
    from shared_platform.operations_runtime import RuntimeProfile, runtime_matches
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform.native_task_preparation import NativeR1FilesystemBoundary
    stamps = {Path(f['path']):_stamp(f['path']) for f in expected['predecessors']['files']}
    cancellation_path = Path(expected['cancellation']['authority']['path'])
    stamps[cancellation_path] = _stamp(cancellation_path)
    _need(profile.environment == 'stable' and runtime_matches(profile)
          and engine.release == {'code_version':profile.version,'environment':profile.environment,
                                'manifest_digest':profile.manifest_digest}, 'CURRENT_CONSUMER_REQUIRED')
    old = expected['from_release']
    producer = RuntimeProfile(profile.root,profile.data_root,old['environment'],old['code_version'],old['manifest_digest'])
    _need(observe(WorkbenchEngine(engine.store.path,old),task_id,profile=producer,release_store=release_store,
                  cancellation_authority=expected['cancellation']['authority'])
          == expected, 'EXPECTED_OBSERVATION_CHANGED')
    def git(*args): return subprocess.check_output(['git','-C',str(profile.root),*args],text=True).strip()
    git('merge-base','--is-ancestor',old['code_version'],profile.version)
    changes = git('diff','--name-status',old['code_version'],profile.version).splitlines()
    _need(old != engine.release and not git('status','--porcelain=v1','-uall') and changes
          and all(r.split('\t')[0] in {'A','M'} and r.split('\t')[1] in ALLOWED_FILES for r in changes),
          'ONLY_REVIEWED_CODE_DELTA_REQUIRED')
    source = {'root':str(profile.root),'from_head':old['code_version'],'to_head':profile.version,
              'manifest_digest':profile.manifest_digest,'changed_paths':changes}
    seal = expected['predecessors']; boundary = NativeR1FilesystemBoundary(profile)
    parents = {Path(f['path']).parent for f in seal['files']}
    correction = seal['sku_only_correction']
    authority = [Path(p) for p,d in correction['original_files'].items() if d == correction['authority_sha256']]
    _need(len(authority) == 1 and any(Path(f['path']) == authority[0]
          and f['sha256'] == correction['authority_sha256'] for f in seal['files']), 'AUTHORITY_FILE_CHANGED')
    parents.discard(authority[0].parent)
    _need(all(_stamp(p) == s for p,s in stamps.items()), 'ORIGINAL_FILES_CHANGED')
    with ExitStack() as stack:
        stack.enter_context(boundary._pin(sorted(parents,key=str),release_parent=Path(seal['paid']['directory']).parents[3]))
        stack.enter_context(boundary._pin([authority[0].parent],release_parent=authority[0].parent))
        if cancellation_path.parent != authority[0].parent:
            stack.enter_context(boundary._pin([cancellation_path.parent],release_parent=cancellation_path.parent))
        db = stack.enter_context(closing(sqlite3.connect(engine.store.path.resolve().as_uri()+'?mode=rw',uri=True,timeout=3)))
        db.row_factory = sqlite3.Row
        release_db = stack.enter_context(closing(release_store._connect_readonly()))
        release_db.execute('BEGIN')
        _need(_business(release_store,release_db) == expected['business'], 'ORIGINAL_BUSINESS_ROWS_CHANGED')
        db.execute('BEGIN IMMEDIATE')
        try:
            row = engine._row(db,TASK); _row_ready(db,row)
            _need(_event(db,TASK)[0] is None and json.loads(row['version_json']) == old
                  and row['state'] == expected['registration_state']
                  and all(row[k] == expected[v] for k,v in (
                      ('checkpoint_json','checkpoint_raw'),('steps_json','steps_raw'),('action_json','action_raw')))
                  and json.loads(row['scope_json']) == expected['scope']
                  and row['request_digest'] == expected['request_digest']
                  and _digest(_history(db,TASK)) == expected['history_sha256'], 'ORIGINAL_TASK_CAS_CHANGED')
            grant = db.execute("SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type='explicit_new_post_preparation'",(TASK,)).fetchone()
            record = {**expected,'schema_version':SCHEMA,'to_release':engine.release,'source':source,
                      'original_post_event_id':grant['id'],
                      'original_post_sha256':sha256(grant['detail_json'].encode()).hexdigest(),'execution_authority':False}
            _need(all(_stamp(p) == s for p,s in stamps.items()), 'ORIGINAL_FILES_CHANGED')
            changed = db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=? AND version_json=?',
                                 (_json(engine.release),TASK,row['version_json'])).rowcount
            _need(changed == 1,'VERSION_CAS_LOST')
            engine._event(db,TASK,EVENT,{'record':record,'record_sha256':_digest(record)})
            from shared_platform.native_unfrozen_source_successor import verified_explicit_origin
            verified_explicit_origin(db,engine._row(db,TASK),engine.release)
            _need(all(_stamp(p) == s for p,s in stamps.items()), 'ORIGINAL_FILES_CHANGED')
            db.commit()
            return record
        except BaseException:
            db.rollback(); raise
