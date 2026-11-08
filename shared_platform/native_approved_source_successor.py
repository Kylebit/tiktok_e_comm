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
COMPATIBILITY_SCHEMA = 'native-approved-source-code-compatibility/v1'
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
    'modules/shopee/skill_regions.py',
    'modules/shopee/global_v4_executor.py',
    'shared_platform/product_publication_executors.py',
    'tests/test_shopee_skill_regions.py',
    'modules/products/server.py',
    'shared_platform/native_final_mutation_budget.py',
    'shared_platform/native_known_rejected_save_repair.py',
    'shared_platform/native_accepted_save_core_repair.py',
    'tests/test_native_known_rejected_save_repair.py',
    'shared_platform/native_provider_continuation.py',
    'shared_platform/release_store.py',
    'tests/test_native_provider_response_recovery.py',
    'shared_platform/product_publication_live_dependencies.py',
    'shared_platform/sku_only_publication_correction.py',
    'shared_platform/r3_technical_category_evidence.py',
    'tests/test_r3_technical_category_evidence.py',
    'shared_platform/workbench_engine.py',
    'tests/test_workbench_final_review_evidence.py',
    'shared_platform/native_companion_promotions.py',
    'tests/test_native_promotion_auth_reprepare.py',
    'modules/tiktok/oneclick_promotion.py',
    'shared_platform/native_tiktok_exact_readback.py',
    'tests/test_native_tiktok_promotion_identity.py',
    'tests/test_native_tiktok_failed_readback.py',
})


def _need(ok, code):
    if not ok: raise ValueError('APPROVED_SOURCE_' + code)


def _events(db, task_id):
    rows = db.execute('SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type=?',
                      (task_id, EVENT)).fetchall()
    result = []
    for row in sorted(rows,key=lambda item:item['id']):
        envelope = json.loads(row['detail_json'])
        _need(set(envelope) == {'record','record_sha256'}
              and envelope['record_sha256'] == _digest(envelope['record']), 'RECORD_DIGEST_CHANGED')
        result.append((row,envelope['record']))
    return result


def _event(db, task_id):
    """The original restriction/predecessor consumers retain the chain root."""
    rows = _events(db,task_id)
    return rows[0] if rows else (None,None)


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


def _related_rows(db, keys):
    """Reuse the original schema/row semantic seals for explicit relation keys."""
    sealed = []
    for table, sql in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        columns = {r[1] for r in db.execute('PRAGMA table_info("'+table+'")')}
        fields = sorted(columns & keys.keys())
        clauses = []; args = []
        for field in fields:
            if keys[field]:
                clauses.append('"'+field+'" IN ('+','.join('?'*len(keys[field]))+')'); args += keys[field]
        if not clauses: continue
        rows = [dict(r) for r in db.execute('SELECT * FROM "'+table+'" WHERE '+' OR '.join(clauses),args)]
        sealed.append({'table':table,'schema_sha256':sha256(sql.encode()).hexdigest(),
                       'count':len(rows),'rows_sha256':_digest(sorted(rows,key=_json))})
    return sealed


def _task_context(db, task_id):
    operations = [r[0] for r in db.execute(
        'SELECT operation_id FROM workbench_domain_operations WHERE owner_task_id=?',(task_id,))]
    return _related_rows(db,{'task_id':[task_id],'owner_task_id':[task_id],'operation_id':operations})


def _related_jobs(db, plan_ids):
    jobs = set()
    for table, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'oneclick_%'"):
        columns = {r[1] for r in db.execute('PRAGMA table_info("'+table+'")')}
        if {'job_id','plan_id'} <= columns:
            jobs.update(r[0] for r in db.execute('SELECT job_id FROM "'+table+'" WHERE plan_id IN ('+
                ','.join('?'*len(plan_ids))+')',plan_ids))
    return sorted(jobs)


def _business(store, db, *, initial=True, anchor=None):
    from shared_platform.native_sole_final_decision import NativeSoleFinalDecisionService, _frozen, check_schema
    from shared_platform.local_operator_session import current_windows_owner_sid
    from shared_platform.release_store import _plan_from_row
    from shared_platform.r3_frozen_review_producer import read_stored_domain_graph
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    check_schema(db)
    anchor = anchor or {'decision_id':DECISION,'plan_id':PLAN,'candidate_digest':CANDIDATE,
        'snapshot_digest':SNAPSHOT,'accepted_external_id':OZON}
    decision_id, plan_id = anchor['decision_id'], anchor['plan_id']
    # This existing method reads the atomic approval relation only. No actor
    # reader, actor dictionary, READ RPC or execution scope is manufactured.
    receipt = NativeSoleFinalDecisionService(store,None)._decision(db,decision_id,owner_sid=current_windows_owner_sid())
    decision = db.execute('SELECT * FROM native_sole_final_decisions WHERE decision_id=?',(decision_id,)).fetchone()
    plan = _plan_from_row(db.execute('SELECT * FROM release_plans WHERE plan_id=?',(plan_id,)).fetchone())
    review = _frozen(decision['review_json'])
    _need(decision['plan_id'] == plan['plan_id'] == plan_id
          and receipt['snapshot_digest'] == anchor['snapshot_digest']
          and review.candidate_digest == anchor['candidate_digest'] and sorted(plan['targets']) == sorted(review.targets),
          'ORIGINAL_DECISION_CHANGED')
    if initial:
        _need(plan['product_id'] == OFFER and plan['seller_sku'] == '9457','ORIGINAL_DECISION_CHANGED')
    graph = read_stored_domain_graph(db,plan_id,native_reader=NativeCommonSourceReader(store))
    _need((graph.offer_id,graph.candidate_digest,graph.round1_digest,graph.round2_digest) ==
          (plan['product_id'],anchor['candidate_digest'],review.round1_digest,review.round2_digest), 'ORIGINAL_FROZEN_GRAPH_CHANGED')
    run_id = 'release-run:' + plan['payload_digest'][:24]
    runs = db.execute('SELECT run_id FROM release_runs WHERE plan_id=?',(plan_id,)).fetchall()
    _need(len(runs) == 1 and runs[0]['run_id'] == run_id, 'ORIGINAL_RUN_CHANGED')
    run = store._run_in_transaction(db,run_id)
    scopes = [dict(r) for r in db.execute('SELECT * FROM native_final_budget_scopes WHERE plan_id=?',(plan_id,))]
    budgets = [[dict(r) for r in db.execute('SELECT * FROM native_final_budget_'+name+
        ' WHERE scope_key IN (SELECT scope_key FROM native_final_budget_scopes WHERE plan_id=?)',(plan_id,))]
        for name in ('reservations','dispatches','outcomes')]
    if initial:
        _partial(run,scopes,*budgets)
    # Seal all same-Offer historical rows, not just the latest target display.
    plan_ids = [r[0] for r in db.execute('SELECT plan_id FROM release_plans WHERE product_id=?',(plan['product_id'],))]
    run_ids = [r[0] for r in db.execute('SELECT run_id FROM release_runs WHERE plan_id IN ('+
        ','.join('?'*len(plan_ids))+')',plan_ids)]
    keys = {'offer_id':[plan['product_id']],'product_id':[plan['product_id']],'plan_id':plan_ids,'run_id':run_ids,
            'scope_key':[r['scope_key'] for r in scopes]}
    # OneClick child rows are keyed by job, including promotion UNKNOWN locks.
    keys['job_id'] = _related_jobs(db,plan_ids)
    keys['grant_id'] = []
    for table in ('native_save_repair_grants','native_accepted_core_grants'):
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
            keys['grant_id'] += [r[0] for r in db.execute('SELECT grant_id FROM '+table+
                ' WHERE scope_key IN ('+','.join('?'*len(keys['scope_key']))+')',keys['scope_key'])]
    sealed = _related_rows(db,keys)
    return {'decision_id':decision_id,'plan_id':plan_id,'candidate_digest':anchor['candidate_digest'],
            'snapshot_digest':anchor['snapshot_digest'],'run_id':run_id,'accepted_external_id':anchor['accepted_external_id'],
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


def _verify_initial(db, row, original, grant, from_release, to_release):
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


def _event_ref(event):
    return {'event_id':event['id'],'detail_sha256':sha256(event['detail_json'].encode()).hexdigest()}


def _compatibility_row(row):
    # A durable RUNNING/UNKNOWN outcome or domain lock is retained, not retried.
    _need(row['template'] == 'publication'
          and all(row[k] is None for k in ('worker','lease_token','lease_until')),
          'OWNED_EXECUTOR_MUST_BE_QUIESCENT')


def _verify_compatibility(db, row, original, grant, event, record, parent, root, previous):
    _need(set(record) == {'schema_version','task_id','source_key','request_digest','scope',
        'from_release','to_release','original_post_event_id','original_post_sha256','history_sha256',
        'task_row','task_context','business','images','predecessors','cancellation','parent','source',
        'execution_authority'} and record['schema_version'] == COMPATIBILITY_SCHEMA
        and record['execution_authority'] is False, 'COMPATIBILITY_RECORD_CHANGED')
    _need(record['parent'] == _event_ref(parent)
          and record['history_sha256'] == _digest(_history(db,row['task_id'],event['id'])),
          'COMPATIBILITY_HISTORY_CHANGED')
    _need(record['task_id'] == row['task_id'] == root['task_id']
          and record['source_key'] == original['source_key']
          and record['request_digest'] == original['request_digest'] == row['request_digest']
          and record['scope'] == root['scope'] == json.loads(row['scope_json'])
          and record['original_post_event_id'] == grant['id']
          and record['original_post_sha256'] == sha256(grant['detail_json'].encode()).hexdigest()
          and record['from_release'] == previous and record['to_release'] != previous,
          'COMPATIBILITY_ORIGINAL_IDENTITY_CHANGED')
    retained = record['task_row']; _compatibility_row(retained)
    _need(retained['task_id'] == row['task_id'] and retained['request_digest'] == row['request_digest']
          and json.loads(retained['scope_json']) == record['scope']
          and json.loads(retained['version_json']) == previous, 'COMPATIBILITY_TASK_PREIMAGE_CHANGED')
    _need(all(record['business'].get(k) == root['business'].get(k) for k in
          ('decision_id','plan_id','candidate_digest','snapshot_digest','run_id','accepted_external_id',
           'release_store_path','receipt'))
          and all(record[k] == root[k] for k in ('images','predecessors','cancellation')),
          'COMPATIBILITY_APPROVAL_OR_PREDECESSORS_CHANGED')
    source = record['source']; changes = source['changed_paths']
    _need(source['root'] == root['source']['root']
          and source['from_head'] == previous['code_version']
          and source['to_head'] == record['to_release']['code_version']
          and source['manifest_digest'] == record['to_release']['manifest_digest']
          and changes and all(r.split('\t')[0] in {'A','M'} and r.split('\t')[1] in ALLOWED_FILES for r in changes),
          'ONLY_REVIEWED_CODE_DELTA_REQUIRED')
    return record['to_release']


def verify(db, row, original, grant, from_release, to_release):
    links = _events(db,row['task_id'])
    if not links: return from_release
    event, root = links[0]
    current = _verify_initial(db,row,original,grant,from_release,root['to_release'])
    parent = event
    for event, record in links[1:]:
        current = _verify_compatibility(db,row,original,grant,event,record,parent,root,current)
        parent = event
    _need(current == to_release,'TERMINAL_COMPATIBILITY_SOURCE_CHANGED')
    return current


def observe_compatibility(engine, task_id, *, profile, release_store):
    """Read an existing approved source chain without classifying writes as zero.

    The local maintainer must first retire owned execution processes. Existing
    UNKNOWN rows/locks, actions and checkpoints are evidence to retain, not an
    eligibility grant. No attempt, approval, resource or state is changed here.
    """
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform.operations_runtime import RuntimeProfile
    from shared_platform.release_store import ReleaseStore, default_release_store
    from shared_platform.native_unfrozen_source_successor import verified_explicit_origin
    from shared_platform.native_frozen_source_successor import verify_native_owner, verify_native_files, _retained_producer
    from shared_platform import workbench_publication_native as native
    from shared_platform.native_publication_cancellation import authority
    _need(type(engine) is WorkbenchEngine and type(profile) is RuntimeProfile
          and type(release_store) is ReleaseStore
          and release_store.path.resolve() == default_release_store().path.resolve()
          and engine.store.path.resolve() == (profile.data_root/'tasks.db').resolve()
          and engine.release == {'code_version':profile.version,'environment':profile.environment,
                                'manifest_digest':profile.manifest_digest}, 'EXACT_SOURCE_CONFIG_DATA_REQUIRED')
    with engine.readonly_snapshot() as db:
        row = engine._row(db,task_id); _compatibility_row(row)
        links = _events(db,task_id); _need(bool(links),'REGISTERED_APPROVED_PREDECESSOR_REQUIRED')
        root = links[0][1]
        _need(Path(root['source']['root']).resolve() == profile.root.resolve(),'CURRENT_CONSUMER_ROOT_CHANGED')
        verified_explicit_origin(db,row,engine.release)
        task = engine._project(db,row)
        source_key = db.execute('SELECT source_key FROM workbench_tasks WHERE task_id=?',(task_id,)).fetchone()[0]
        history = _digest(_history(db,task_id)); context = _task_context(db,task_id)
        seal = verify_native_owner(db,row,engine.release,profile)
    _need(seal == root['predecessors'],'ORIGINAL_NATIVE_SOURCE_SEAL_REQUIRED')
    verify_native_files(seal,media=True)
    with _retained_producer(task,profile): images = native.read_images(task,profile)
    cancellation = authority(root['cancellation']['authority'])
    _need(images == root['images'] and cancellation == root['cancellation'],'ORIGINAL_IMAGES_OR_RESTRICTION_CHANGED')
    with closing(release_store._connect_readonly()) as db:
        db.execute('BEGIN'); business = _business(release_store,db,initial=False,anchor=root['business'])
    return {'task_id':task_id,'source_key':source_key,'request_digest':row['request_digest'],
        'scope':json.loads(row['scope_json']),'from_release':engine.release,
        'task_row':dict(row),'task_context':context,'history_sha256':history,
        'parent':_event_ref(links[-1][0]),'business':business,'images':images,
        'predecessors':seal,'cancellation':cancellation}


def _append_compatibility(engine, db, expected, business, source):
    """Transaction core: exactly one version CAS and one appended source event."""
    task_id = expected['task_id']; row = engine._row(db,task_id); _compatibility_row(row)
    links = _events(db,task_id)
    _need(bool(links) and _event_ref(links[-1][0]) == expected['parent'], 'COMPATIBILITY_PARENT_CHANGED')
    _need(dict(row) == expected['task_row'] and _task_context(db,task_id) == expected['task_context']
          and _digest(_history(db,task_id)) == expected['history_sha256']
          and json.loads(row['version_json']) == expected['from_release'], 'COMPATIBILITY_TASK_CAS_CHANGED')
    _need(business == expected['business'],'ORIGINAL_BUSINESS_ROWS_CHANGED')
    grant = db.execute("SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type='explicit_new_post_preparation'",
                       (task_id,)).fetchone()
    _need(grant is not None,'UNIQUE_ORIGINAL_POST_REQUIRED')
    record = {**expected,'schema_version':COMPATIBILITY_SCHEMA,'to_release':engine.release,'source':source,
        'original_post_event_id':grant['id'],'original_post_sha256':sha256(grant['detail_json'].encode()).hexdigest(),
        'execution_authority':False}
    changed = db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=? AND version_json=?',
                         (_json(engine.release),task_id,row['version_json'])).rowcount
    _need(changed == 1,'VERSION_CAS_LOST')
    engine._event(db,task_id,EVENT,{'record':record,'record_sha256':_digest(record)})
    current = engine._row(db,task_id)
    _need({k:v for k,v in current.items() if k != 'version_json'} ==
          {k:v for k,v in row.items() if k != 'version_json'},'ORIGINAL_TASK_FIELDS_CHANGED')
    verify(db,current,json.loads(grant['detail_json']),grant,links[0][1]['from_release'],engine.release)
    return record


def register_compatibility(engine, task_id, *, profile, release_store, expected):
    """Append code compatibility; preserve even unresolved business occupancy."""
    from shared_platform.operations_runtime import RuntimeProfile, runtime_matches
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform.native_task_preparation import NativeR1FilesystemBoundary
    from shared_platform.native_unfrozen_source_successor import verified_explicit_origin
    _need(profile.environment == 'stable' and runtime_matches(profile)
          and engine.release == {'code_version':profile.version,'environment':profile.environment,
                                'manifest_digest':profile.manifest_digest}
          and expected['task_id'] == task_id,'CURRENT_CONSUMER_REQUIRED')
    old = expected['from_release']; seal = expected['predecessors']
    stamps = {Path(f['path']):_stamp(f['path']) for f in seal['files']}
    cancellation_path = Path(expected['cancellation']['authority']['path'])
    stamps[cancellation_path] = _stamp(cancellation_path)
    producer = RuntimeProfile(profile.root,profile.data_root,old['environment'],old['code_version'],old['manifest_digest'])
    _need(observe_compatibility(WorkbenchEngine(engine.store.path,old),task_id,profile=producer,
                               release_store=release_store) == expected,'EXPECTED_OBSERVATION_CHANGED')
    def git(*args): return subprocess.check_output(['git','-C',str(profile.root),*args],text=True).strip()
    git('merge-base','--is-ancestor',old['code_version'],profile.version)
    changes = git('diff','--name-status',old['code_version'],profile.version).splitlines()
    _need(old != engine.release and not git('status','--porcelain=v1','-uall') and changes
          and all(r.split('\t')[0] in {'A','M'} and r.split('\t')[1] in ALLOWED_FILES for r in changes),
          'ONLY_REVIEWED_CODE_DELTA_REQUIRED')
    source = {'root':str(profile.root),'from_head':old['code_version'],'to_head':profile.version,
              'manifest_digest':profile.manifest_digest,'changed_paths':changes}
    boundary = NativeR1FilesystemBoundary(profile)
    parents = {Path(f['path']).parent for f in seal['files']}
    correction = seal.get('sku_only_correction'); authority_paths = []
    if correction is not None:
        authority_paths = [Path(p) for p,d in correction['original_files'].items() if d == correction['authority_sha256']]
        _need(len(authority_paths) == 1 and any(Path(f['path']) == authority_paths[0]
              and f['sha256'] == correction['authority_sha256'] for f in seal['files']),'AUTHORITY_FILE_CHANGED')
        parents.discard(authority_paths[0].parent)
    with ExitStack() as stack:
        stack.enter_context(boundary._pin(sorted(parents,key=str),release_parent=Path(seal['paid']['directory']).parents[3]))
        for parent in {p.parent for p in authority_paths}|{cancellation_path.parent}:
            stack.enter_context(boundary._pin([parent],release_parent=parent))
        release_db = stack.enter_context(closing(release_store._connect_readonly())); release_db.execute('BEGIN')
        root = _event_from_expected_root(engine,task_id)
        business = _business(release_store,release_db,initial=False,anchor=root['business'])
        db = stack.enter_context(closing(sqlite3.connect(engine.store.path.resolve().as_uri()+'?mode=rw',uri=True,timeout=3)))
        db.row_factory = sqlite3.Row; db.execute('BEGIN IMMEDIATE')
        try:
            _need(all(_stamp(p) == s for p,s in stamps.items()),'ORIGINAL_FILES_CHANGED')
            record = _append_compatibility(engine,db,expected,business,source)
            verified_explicit_origin(db,engine._row(db,task_id),engine.release)
            _need(all(_stamp(p) == s for p,s in stamps.items()),'ORIGINAL_FILES_CHANGED')
            db.commit(); return record
        except BaseException:
            db.rollback(); raise


def _event_from_expected_root(engine, task_id):
    with engine.readonly_snapshot() as db:
        return _event(db,task_id)[1]


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
