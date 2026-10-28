"""Finite existing-product media repair under the original native decision.

The independent media ledger never changes the original import/stock ceiling.
Missing the separately signed source event and exact user grant refuses writes.
"""
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import os
import sqlite3

from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold, _DurableLedger, _json, _sha
from shared_platform.native_known_rejected_save_repair import _read_reference

EVENT = 'source_code_approved_native_tiktok_ozon_recovery_compatibility'
PLAN = 'r3-marketplace:8412287877065586543:07707b4b7901ca9b97b08acd'
RUN = 'release-run:25e66f7250d31cbb39ff8d0e'
DECISION = 'native-final-decision:7a10d3ac157c55c6949b663c5b63194b2c43eab81b0fee88eda6aae412838fe2'
SNAPSHOT = 'sha256:8eecd1c0b7beadcaddc7a8655f25b5f9e981729c4cb44e33dbd5aac17eaaa3de'
SNAPSHOT_RAW_SHA = '3e41486796ec38f8f59fa73655abf1bc3deb1df27d62c5222e38baa5d4d02fb1'
ACCOUNT = '4953064'
PRODUCTS = {'2000431': 6760984630, '2000432': 6760985024, '2000433': 6760985460}
TASKS = (5906902718, 5906902998, 5906903305)
TARGET = 'ozon:RU'
PARTIAL_EVENT = 'source_code_approved_native_ozon_two_card_stock_and_shopee_frozen_completion_continuation'
PARTIAL_OFFERS = ('2000432', '2000433')
PARTIAL_WAREHOUSE = 1020005018928780
TERMINAL_SHA = '627fec2e75acfe1a2bdcd886ecb272c677e230b17fe82a5474460325cbd455af'
IMPORT = '/v1/product/pictures/import'
INFO = '/v2/product/pictures/info'
_TITLE_SCOPE = ContextVar('native_ozon_three_card_media_title_scope', default=None)
PROPOSAL_SHA = '6ddd0f13c6aadbae1d05006a1fb3c78cfa9fabbdeb4a598a95290d48f9ef6ca9'


def need(value, code):
    if not value:
        raise NativeMutationBudgetHold('OZON_THREE_MEDIA_' + code)


def read_media3_decision(ref):
    """Pure typed user-pin reader shared with the signed source continuation."""
    need(type(ref) is dict and set(ref) == {'path', 'sha256'}, 'EXPLICIT_USER_DECISION_REQUIRED')
    doc = json.loads(_read_reference(ref))
    expected = dict(schema='explicit-user-existing-ozon-three-card-media-authority/v1',
        user_answer='允许额外3次补图，保留原库存额度', task_id='TASK-20261023-001', offer_id='8412287877065586543',
        plan_id=PLAN, decision_id=DECISION, candidate_digest='c5eacc9725ecefa012af02c6d4f5130c6098c9bedab65e2159357a708c73a5cc',
        account_id=ACCOUNT, target_label=TARGET, endpoint=IMPORT, products=PRODUCTS, extra_media_maximum=3,
        media_maximum_per_existing_product=1, reuse_original_eight_images_and_exact_order=True,
        original_OZON_shared_cap_remains=4, original_import_mutations_preserved=3, original_stock_remaining_reserved=1,
        new_marketplace_content_approval=False, new_product_import=False, new_image_generation=False,
        new_copy=False, budget_reset_or_borrow=False)
    need(all(type(doc.get(k)) is type(v) and doc.get(k) == v for k, v in expected.items()), 'EXACT_USER_AUTHORITY_REQUIRED')
    proposal_ref = doc.get('proposal_ref')
    need(type(proposal_ref) is dict and proposal_ref.get('sha256') == PROPOSAL_SHA, 'FROZEN_PROPOSAL_REQUIRED')
    proposal = json.loads(_read_reference(proposal_ref))
    need(proposal.get('plan_id') == PLAN and proposal.get('decision_id') == DECISION
        and proposal.get('approved_snapshot_digest') == SNAPSHOT
        and proposal.get('approved_snapshot_json_sha256') == SNAPSHOT_RAW_SHA
        and proposal.get('account_id') == ACCOUNT and proposal.get('facts', {}).get('products') == PRODUCTS
        and proposal['facts'].get('original_import_tasks') == list(TASKS), 'ORIGINAL_PROPOSAL_IDENTITY_REQUIRED')
    requests = proposal.get('requests_not_executed')
    need(type(requests) is list and len(requests) == 3
        and [row.get('offer_id') for row in requests] == list(PRODUCTS), 'EXACT_THREE_REQUESTS_REQUIRED')
    for row in requests:
        body = row.get('body');offer = row['offer_id']
        need(type(body) is dict and set(body) == {'product_id', 'images'} and type(body['product_id']) is int
            and body['product_id'] == PRODUCTS[offer] and type(body['images']) is list and len(body['images']) == 8
            and len(set(body['images'])) == 8 and row.get('endpoint') == IMPORT
            and row.get('mutation_attempts') == 1 and _sha(_json(body)) == row.get('request_canonical_sha256'),
            'EXACT_FROZEN_MEDIA_REQUEST_REQUIRED')
    return doc


def _stock_rows(image):
    scopes = {r['scope_key'] for r in image['native_final_budget_scopes'] if r['platform'] == 'OZON'}
    dispatches = {(r['scope_key'], r['sequence']): r for r in image['native_final_budget_dispatches']}
    outcomes = {(r['scope_key'], r['sequence']): r for r in image['native_final_budget_outcomes']}
    return [{**{k:r[k] for k in ('sequence','target_label','budget_scope','operation','attempt_count')},
        'request_digest': dispatches.get((r['scope_key'], r['sequence']), {}).get('request_digest'),
        'outcome': outcomes.get((r['scope_key'], r['sequence']), {}).get('outcome',r['initial_outcome'])}
        for r in sorted(image['native_final_budget_reservations'], key=lambda r: r['sequence']) if r['scope_key'] in scopes]


def _two_stock_body():
    return {'stocks': [dict(offer_id=offer, stock=200, warehouse_id=PARTIAL_WAREHOUSE) for offer in PARTIAL_OFFERS]}


def _exact_duplicate(item):
    errors = item.get('validation_errors', item.get('errors')) or []
    return any(row.get('code') == 'SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT'
        and {'name': 'Duplicates', 'value': '2000432 - 4953064'} in (row.get('texts') or {}).get('params', []) for row in errors)


def read_two_card_stock_proof(ref):
    """Original stock command subset; this pure reader creates no ledger/grant."""
    doc = json.loads(_read_reference(ref))
    expected = dict(schema='native-ozon-two-eligible-card-stock-technical-authority/v1', status='ROOT_TECHNICAL_EXECUTION_APPROVED',
        task_id='TASK-20261023-001', offer_id='8412287877065586543', plan_id=PLAN, run_id=RUN, decision_id=DECISION,
        candidate_digest='c5eacc9725ecefa012af02c6d4f5130c6098c9bedab65e2159357a708c73a5cc',
        snapshot_digest=SNAPSHOT, account_id=ACCOUNT, products=PRODUCTS, selected_offers=list(PARTIAL_OFFERS),
        blocked_offer='2000431', stock_per_selected=200, warehouse_id=PARTIAL_WAREHOUSE, original_shared_maximum=4,
        original_accepted_imports=3, remaining_stock_batches=1, new_import=False, new_media=False, new_budget=False,
        new_human_review=False, full_three_card_success=False, stock_body=_two_stock_body())
    need(all(type(doc.get(k)) is type(v) and doc[k] == v for k, v in expected.items()), 'EXACT_TWO_CARD_STOCK_PROOF_REQUIRED')
    image = json.loads(_read_reference(doc['retained_ref']))
    need(doc.get('base_source_head') == '8734a3a957811d4352f081cb3d82b741a6e67c45'
        and image.get('source_head') == doc['base_source_head'], 'EXACT_PARTIAL_SOURCE_REQUIRED')
    target = next(r for r in image['release_target_runs'] if r['target_label'] == TARGET)
    rows = _stock_rows(image);failures = [r for r in image['release_target_failure_events'] if r['target_label'] == TARGET]
    need((target['run_id'], target['status'], target['attempts']) == (RUN, 'FAILED', 2)
        and len(failures) == 1 and failures[0]['attempt'] == 2 and _sha(failures[0]['evidence_json']) == failures[0]['evidence_digest'],
        'ORIGINAL_FAILED2_REQUIRED')
    need(len(rows) == 3 and all(r['operation'] == 'import_variant' and r['attempt_count'] == 1 and r['budget_scope'] == 'shared'
        and r['outcome'] == 'ACCEPTED' and r['request_digest'] for r in rows), 'THREE_IMPORTS_UNUSED_STOCK_SLOT_REQUIRED')
    scope = next(r for r in image['native_final_budget_scopes'] if r['platform'] == 'OZON')
    binding = json.loads(scope['binding_json'])
    need(binding['shared_maximum'] == 4 and binding['per_target_maximum'] == 0 and binding['decision_id'] == DECISION
        and binding['snapshot_digest'] == SNAPSHOT and _sha(scope['binding_json']) == scope['binding_sha256'], 'ORIGINAL_SHARED4_REQUIRED')
    terminal = doc['official_terminal_ref'];need(terminal.get('sha256') == TERMINAL_SHA, 'ORIGINAL_DUPLICATE_EVIDENCE_REQUIRED')
    items = json.loads(_read_reference(terminal))['items'];by_offer = {r['offer_id']: r for r in items}
    need(len(items) == 3 and set(by_offer) == set(PRODUCTS) and all(by_offer[k]['id'] == v for k, v in PRODUCTS.items())
        and _exact_duplicate(by_offer['2000431']) and all(by_offer[k]['errors'] == []
            and by_offer[k]['statuses']['moderate_status'] == 'approved' and by_offer[k]['statuses']['validation_status'] == 'success'
            and not by_offer[k]['statuses']['status_failed'] for k in PARTIAL_OFFERS), 'EXACT_DUPLICATE_AND_TWO_APPROVED_REQUIRED')
    return doc


def _partial_material(ref, doc):
    return dict(ref=ref, doc=doc, digest=_sha(_json(doc)), image=json.loads(_read_reference(doc['retained_ref'])))


def _partial_event(bound, task):
    with sqlite3.connect(bound.engine.store.path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=?', (task['task_id'], PARTIAL_EVENT)).fetchall()
    if not rows:return None
    need(len(rows) == 1, 'SINGLE_PARTIAL_SOURCE_EVENT_REQUIRED')
    envelope = json.loads(rows[0][0]);raw = envelope.get('record_json')
    need(type(raw) is str and _sha(raw) == envelope.get('record_digest'), 'PARTIAL_SIGNED_ENVELOPE_REQUIRED')
    record = json.loads(raw);ref = record['ozon_two_card_stock_ref'];doc = read_two_card_stock_proof(ref)
    need(record['ozon_two_card_stock_digest'] == _sha(_json(doc)), 'PARTIAL_SIGNED_PROOF_CHANGED')
    return _partial_material(ref, doc)


def _context(active):
    from shared_platform.native_sole_final_execution import _NativeExecution, _ACTIVE
    from shared_platform.agent_prepared_v4_targets import _scope
    need(type(active) is _NativeExecution and _ACTIVE.get() is active and active.plan_id == PLAN
        and active.decision_id == DECISION and active.service.store is active.store,
        'PROTECTED_ORIGINAL_NATIVE_DECISION_REQUIRED')
    need(active.service._actor()['owner_sid'] == active.owner_sid, 'CURRENT_ORIGINAL_ACTOR_REQUIRED')
    bound, task, record = _scope(active, active.store.get_plan(PLAN))  # genuine full source/HMAC verification first
    with sqlite3.connect(bound.engine.store.path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=?',
            (task['task_id'], EVENT)).fetchall()
    need(len(rows) == 1, 'NEW_DISTINCT_SIGNED_SOURCE_EVENT_REQUIRED')
    event = json.loads(json.loads(rows[0][0])['record_json'])
    ref = event.get('ozon_media3_decision_ref');decision = read_media3_decision(ref)
    proposal = json.loads(_read_reference(decision['proposal_ref']))
    with active.store._connect_readonly() as db:
        db.execute('BEGIN');receipt = active.service._decision(db, DECISION, owner_sid=active.owner_sid)
        row = db.execute('SELECT snapshot_json FROM approved_publication_snapshots WHERE plan_id=?', (PLAN,)).fetchone()
        need(receipt['snapshot_digest'] == SNAPSHOT and row is not None and _sha(row[0]) == SNAPSHOT_RAW_SHA,
            'ORIGINAL_APPROVED_SNAPSHOT_CHANGED')
        snapshot = json.loads(row[0])
        target = dict(db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, TARGET)).fetchone())
        failures = [dict(row) for row in db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? ORDER BY attempt', (RUN, TARGET))]
        submissions = [dict(row) for row in db.execute('SELECT * FROM release_target_submissions WHERE run_id=? AND target_label=?', (RUN, TARGET))]
        installed = None
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='native_ozon_media_grants'").fetchone():
            from shared_platform.native_ozon_existing_media_recovery import check_schema
            check_schema(db)
            installed = db.execute('SELECT * FROM native_ozon_media_grants WHERE run_id=?', (RUN,)).fetchone()
    from shared_platform.publication_ozon_card_structure import require_verified_snapshot_structure
    from shared_platform.publication_r3_image_bridge import independent_card_gallery
    structure = require_verified_snapshot_structure(snapshot)
    by_model = {row['model_sku']: row for row in snapshot['skus']}
    for request in proposal['requests_not_executed']:
        need(request['body']['images'] == independent_card_gallery(snapshot, by_model[request['offer_id']], structure),
            'ORIGINAL_EIGHT_IMAGE_ORDER_CHANGED')
    body = dict(schema='native-three-card-existing-media-grant/v1', plan_id=PLAN, run_id=RUN,
        decision_id=DECISION, snapshot_digest=SNAPSHOT, account_id=ACCOUNT, products=PRODUCTS,
        user_ref=ref, proposal_ref=decision['proposal_ref'], requests=proposal['requests_not_executed'])
    if installed is None:
        need((target['status'], target['attempts']) == ('FAILED', 1) and not failures, 'ORIGINAL_FAILED1_EMPTY_FAILURE_HISTORY_REQUIRED')
        body.update(original_target_preimage=target, original_failure_rows=failures, original_submission_rows=submissions)
    else:
        stored = json.loads(installed['body_json'])
        need(_sha(installed['body_json']) == installed['grant_id'] == installed['body_sha256']
            and {k: v for k, v in stored.items() if k not in {'original_target_preimage', 'original_failure_rows', 'original_submission_rows'}} == body,
            'PERSISTED_GRANT_CHANGED')
        body = stored
    return dict(active=active, body=body, grant_id=_sha(_json(body)), snapshot=snapshot, partial=_partial_event(bound, task),
        root=active.runtime.supervisor.root / 'native-ozon-three-card-media' / _sha(_json(body)))


def _original(db, ctx, *, state='FAILED', attempt=1, stock_allowed=False):
    if ctx.get('partial'):return _partial_original(db, ctx, state=state, attempt=attempt, stock_allowed=stock_allowed)
    active = ctx['active'];receipt = active.service._decision(db, DECISION, owner_sid=active.owner_sid)
    need(receipt['snapshot_digest'] == SNAPSHOT, 'ORIGINAL_APPROVAL_CHANGED')
    run = db.execute('SELECT * FROM release_runs WHERE run_id=?', (RUN,)).fetchone()
    need(run and run['plan_id'] == PLAN and run['approval_id'] == receipt['release_approval_id']
        and run['status'] not in {'SUCCEEDED', 'SUPERSEDED'}, 'ORIGINAL_ACTIVE_RUN_CHANGED')
    target = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, TARGET)).fetchone()
    need(target and target['status'] == state and target['attempts'] == attempt, 'ORIGINAL_TARGET_STATE_CHANGED')
    events = db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? ORDER BY attempt',
        (RUN, TARGET)).fetchall()
    need([dict(row) for row in events] == ctx['body']['original_failure_rows'] == [], 'ORIGINAL_EMPTY_FAILURE_HISTORY_CHANGED')
    original = ctx['body']['original_target_preimage']
    need((original['status'], original['attempts']) == ('FAILED', 1)
        and all(target[key] == original[key] for key in ('run_id', 'target_label', 'idempotency_key', 'created_at')),
        'ORIGINAL_TARGET_PREIMAGE_CHANGED')
    if attempt == 1:
        need(target['error'] == original['error'] and target['external_id'] == original['external_id'], 'ORIGINAL_TARGET_FAILURE_CHANGED')
    scope = db.execute("SELECT * FROM native_final_budget_scopes WHERE plan_id=? AND snapshot_digest=? AND platform='OZON'",
        (PLAN, SNAPSHOT)).fetchone()
    need(scope and _sha(scope['binding_json']) == scope['binding_sha256'], 'ORIGINAL_BUDGET_REQUIRED')
    binding = json.loads(scope['binding_json'])
    need((binding['plan_id'], binding['snapshot_digest'], binding['decision_id'], binding['platform'],
        binding['target_labels'], binding['shared_maximum'], binding['per_target_maximum'])
        == (PLAN, SNAPSHOT, DECISION, 'OZON', [TARGET], 4, 0), 'ORIGINAL_SHARED4_UNCHANGED_REQUIRED')
    from shared_platform.native_known_rejected_save_repair import _base_rows
    reservations = _base_rows(db, scope['scope_key'], TARGET)
    need(len(reservations) in ((3, 4) if stock_allowed else (3,))
        and all(row['operation'] == 'import_variant' and row['outcome'] == 'ACCEPTED'
            and row['budget_scope'] == 'shared' and row['attempt_count'] == 1 for row in reservations[:3]),
        'EXACT_THREE_ACCEPTED_IMPORTS_REQUIRED')
    if len(reservations) == 4:
        need(reservations[3]['operation'] == 'update_stock' and reservations[3]['budget_scope'] == 'shared'
            and reservations[3]['attempt_count'] == 1, 'ONLY_ORIGINAL_ONE_STOCK_SLOT')
    submissions = [dict(row) for row in db.execute('SELECT * FROM release_target_submissions WHERE run_id=? AND target_label=?', (RUN, TARGET))]
    reference = ','.join('ozon:import-task:' + str(task) for task in TASKS)
    need(submissions == ctx['body']['original_submission_rows'] and len(submissions) == 1
        and submissions[0]['external_id'] == original['external_id'] == target['external_id'] == reference
        and submissions[0]['status'] == 'SUBMITTED_UNVERIFIED'
        and _sha(submissions[0]['evidence_json']) == submissions[0]['evidence_digest'],
        'ORIGINAL_ACCEPTED_SUBMISSION_CHANGED')
    for table in ('release_target_readbacks', 'release_target_repairs'):
        need(db.execute('SELECT 1 FROM '+table+' WHERE run_id=? AND target_label=?', (RUN, TARGET)).fetchone() is None,
            'EXISTING_RESULT_NO_REPLAY')
    return target


def _partial_original(db, ctx, *, state, attempt, stock_allowed=False):
    image = ctx['partial']['image'];active = ctx['active']
    receipt = active.service._decision(db, DECISION, owner_sid=active.owner_sid)
    run = db.execute('SELECT * FROM release_runs WHERE run_id=?', (RUN,)).fetchone()
    need(receipt['snapshot_digest'] == SNAPSHOT and run and run['plan_id'] == PLAN
        and run['approval_id'] == receipt['release_approval_id'] and run['status'] not in {'SUCCEEDED','SUPERSEDED'}, 'PARTIAL_APPROVAL_CHANGED')
    frozen = db.execute('SELECT snapshot_json FROM approved_publication_snapshots WHERE plan_id=?', (PLAN,)).fetchone()
    need(frozen and _sha(frozen[0]) == SNAPSHOT_RAW_SHA, 'PARTIAL_FROZEN_SNAPSHOT_CHANGED')
    target = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, TARGET)).fetchone()
    old = next(r for r in image['release_target_runs'] if r['target_label'] == TARGET)
    need(target and (target['status'], target['attempts']) == (state, attempt) and attempt in (2, 3)
        and all(target[k] == old[k] for k in ('run_id', 'target_label', 'idempotency_key', 'created_at', 'external_id')), 'PARTIAL_ORIGINAL_TARGET_CHANGED')
    failures = [r for r in image['release_target_failure_events'] if r['target_label'] == TARGET]
    need([dict(r) for r in db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? ORDER BY attempt', (RUN, TARGET))] == failures,
        'PARTIAL_FAILURE2_CHANGED')
    for table in ('native_ozon_media_grants', 'native_ozon_media_attempts', 'native_ozon_media_dispatches',
                  'native_ozon_media_outcomes', 'native_ozon_media_verifications', 'native_ozon_media_starts'):
        current = [dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE grant_id=?', (ctx['grant_id'],))]
        need(current == [r for r in image[table] if r['grant_id'] == ctx['grant_id']] and len(current) == 1, 'PARTIAL_MEDIA_HISTORY_CHANGED')
    old_scope = next(r for r in image['native_final_budget_scopes'] if r['platform'] == 'OZON')
    scope = db.execute('SELECT * FROM native_final_budget_scopes WHERE scope_key=?', (old_scope['scope_key'],)).fetchone()
    need(scope and dict(scope) == old_scope, 'PARTIAL_ORIGINAL_BUDGET_CHANGED')
    from shared_platform.native_known_rejected_save_repair import _base_rows
    current = _base_rows(db, scope['scope_key'], TARGET);old_rows = _stock_rows(image)
    need(current[:3] == old_rows and len(current) in ((3, 4) if stock_allowed else (3,)), 'PARTIAL_ONLY_ONE_ORIGINAL_STOCK_SLOT')
    if len(current) == 4:
        need(current[3]['operation'] == 'update_stock' and current[3]['budget_scope'] == 'shared' and current[3]['attempt_count'] == 1,
            'PARTIAL_ORIGINAL_STOCK_ONLY')
    submissions = [dict(r) for r in db.execute('SELECT * FROM release_target_submissions WHERE run_id=? AND target_label=?', (RUN, TARGET))]
    need(submissions == [r for r in image['release_target_submissions'] if r['target_label'] == TARGET], 'PARTIAL_IMPORT_SUBMISSION_CHANGED')
    return target


def _intake(ctx):
    """Explicit resume only: reuse the already reviewed append-only schema."""
    from shared_platform.native_ozon_existing_media_recovery import check_schema, statements
    with ctx['active'].store._transaction() as db:
        _original(db, ctx)
        if not check_schema(db, optional=True):
            for statement in statements():db.execute(statement)
        row = db.execute('SELECT * FROM native_ozon_media_grants WHERE run_id=?', (RUN,)).fetchone()
        raw = _json(ctx['body'])
        if row is None:
            db.execute('INSERT INTO native_ozon_media_grants VALUES(?,?,?,?)', (ctx['grant_id'], RUN, raw, _sha(raw)))
        else:
            need(row['grant_id'] == ctx['grant_id'] and row['body_json'] == raw and row['body_sha256'] == _sha(raw),
                'PERSISTED_GRANT_CHANGED')
        requests = _json([row['body'] for row in ctx['body']['requests']])
        prior = db.execute('SELECT * FROM native_ozon_media_attempts WHERE grant_id=?', (ctx['grant_id'],)).fetchone()
        if prior is None:
            db.execute('INSERT INTO native_ozon_media_attempts VALUES(?,?,?)', (ctx['grant_id'], requests, _sha(requests)))
        else:need(prior['request_json'] == requests and prior['request_sha256'] == _sha(requests), 'ORIGINAL_REQUESTS_CHANGED')


def _media_ledger(ctx):
    active = ctx['active']
    binding = dict(plan_id=PLAN, snapshot_digest=SNAPSHOT, platform='OZON_MEDIA', target_labels=[TARGET],
        shared_maximum=3, per_target_maximum=0, decision_id=DECISION, grant_id=ctx['grant_id'],
        user_decision_ref=ctx['body']['user_ref'], requests_sha256=_sha(_json(ctx['body']['requests'])))
    def check(db=None):
        if db is None:
            need(_context(active)['grant_id'] == ctx['grant_id'], 'CURRENT_SOURCE_OR_GRANT_CHANGED')
        else:
            _original(db, ctx)
    return _DurableLedger(store=active.store, binding=binding, target_label=TARGET, recheck=check)


def _write_once(path, value):
    raw = _json(value).encode();path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
    except FileExistsError:
        need(path.read_bytes() == raw, 'IMMUTABLE_RESPONSE_CHANGED')
    return {'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest()}


def _preflight(post):
    for offer, task_id in zip(PRODUCTS, TASKS):
        response = post('/v1/product/import/info', {'task_id': task_id})
        result = response.get('result', {}) if type(response) is dict else {}
        rows = result.get('items')
        need(type(rows) is list and len(rows) == 1 and result.get('total') == 1
            and rows[0].get('offer_id') == offer and rows[0].get('product_id') == PRODUCTS[offer]
            and rows[0].get('status') == 'imported', 'EXACT_ORIGINAL_IMPORTED_TASK_REQUIRED')
    response = post('/v3/product/info/list', {'offer_id': list(PRODUCTS), 'limit': 1000, 'visibility': 'ALL'})
    rows = response.get('items') if type(response) is dict else None
    need(type(rows) is list and len(rows) == 3 and {row.get('offer_id'): row.get('id') for row in rows} == PRODUCTS
        and all(row.get('is_archived') is False and (row.get('statuses') or {}).get('is_created') is True for row in rows),
        'EXACT_EXISTING_PRODUCTS_REQUIRED')


def _prepare_media(ctx, post):
    ledger = _media_ledger(ctx)
    _preflight(post)  # original accepted products first; never call product/import
    responses = []
    for request in ctx['body']['requests']:
        body = request['body'];operation = 'repair_existing_media_' + str(body['product_id'])
        prior = [row for row in ledger.snapshot()['reservations'] if row['operation'] == operation]
        artifact = ctx['root'] / (str(body['product_id']) + '.acceptance.json')
        if prior:
            if len(prior) != 1 or prior[0]['outcome'] != 'ACCEPTED':return False
            need(artifact.is_file(), 'ACCEPTED_RESPONSE_ARTIFACT_REQUIRED')
            response = json.loads(artifact.read_bytes())
            validate_media_acceptance(response, body);responses.append(response)
            continue
        ledger.reserve_shared(operation)
        with ctx['active'].store._transaction() as db:
            _original(db, ctx)
            db.execute('INSERT OR IGNORE INTO native_ozon_media_dispatches VALUES(?)', (ctx['grant_id'],))
        try:
            response = ledger.dispatch_reserved(operation, post, IMPORT, deepcopy(body))
        except Exception:
            return False  # durable UNKNOWN is spent; no retry or later card dispatch
        try:
            validate_media_acceptance(response, body)
        except (NativeMutationBudgetHold, ValueError, TypeError, KeyError):
            _write_once(ctx['root'] / (str(body['product_id']) + '.unverified-response.json'),
                {'request_sha256': _sha(_json(body)), 'official_response_sha256': _sha(_json(response)),
                 'product_id': body['product_id'], 'acceptance': 'UNVERIFIED'})
            return False
        # Keep only the safe protocol fields, plus exact response digest.
        retained = {'official_response_sha256': _sha(_json(response)), 'result': {'pictures': [{key: row[key] for key in
            ('product_id', 'url', 'state', 'is_primary', 'is_360', 'is_color')}
            for row in response['result']['pictures']]}}
        _write_once(artifact, retained)
        ledger.complete_reserved(operation, 'ACCEPTED');responses.append(retained)
    raw = _json({'grant_id': ctx['grant_id'], 'responses': responses})
    with ctx['active'].store._transaction() as db:
        _original(db, ctx)
        row = db.execute('SELECT * FROM native_ozon_media_outcomes WHERE grant_id=?', (ctx['grant_id'],)).fetchone()
        if row is None:
            db.execute('INSERT INTO native_ozon_media_outcomes VALUES(?,?,?)', (ctx['grant_id'], raw, _sha(raw)))
        else:
            need(row['response_json'] == raw and row['response_sha256'] == _sha(raw), 'ACCEPTED_RESPONSES_CHANGED')
    return True


def _validate_verification(ctx, proof_ref):
    proof = json.loads(_read_reference(proof_ref))
    need(proof.get('schema_version') == 'ozon-three-card-media-content-assessment/v1'
        and proof.get('status') == 'PASS' and proof.get('method') == 'ACTUAL_TECHNICAL_VISUAL_REVIEW'
        and proof.get('grant_id') == ctx['grant_id'] and proof.get('user_decision_ref') == ctx['body']['user_ref'],
        'COMPLETE_CONTENT_ORDER_PROOF_REQUIRED')
    observed = picture_rows(json.loads(_read_reference(proof['pictures_info_ref'])))
    cards = proof.get('cards')
    need(type(cards) is list and len(cards) == 3 and [row.get('offer_id') for row in cards] == list(PRODUCTS),
        'ALL_THREE_CARD_CONTENT_PROOFS_REQUIRED')
    for card, request in zip(cards, ctx['body']['requests']):
        need(card.get('product_id') == PRODUCTS[card['offer_id']], 'PROOF_PRODUCT_CHANGED')
        positions = card.get('positions')
        need(type(positions) is list and len(positions) == 8, 'ALL_EIGHT_CONTENT_POSITIONS_REQUIRED')
        for index, item in enumerate(positions):
            need(item.get('position') == index + 1 and item.get('role') == request['ordered_roles'][index]
                and item.get('source_url_sha256') == _sha(request['body']['images'][index])
                and item.get('ozon_image_url') == observed[card['product_id']][index]
                and item.get('same_approved_image') is True and item.get('order_matches') is True
                and type(item.get('is_primary')) is bool and item['is_primary'] == (index == 0),
                'CONTENT_HERO_OR_ORDER_UNVERIFIED')
            _read_reference(item['actual_visual_evidence_ref'])
    return observed


def _verified(db, ctx):
    from shared_platform.native_ozon_existing_media_recovery import check_schema
    check_schema(db)
    row = db.execute('SELECT * FROM native_ozon_media_outcomes WHERE grant_id=?', (ctx['grant_id'],)).fetchone()
    proof = db.execute('SELECT proof_ref_json FROM native_ozon_media_verifications WHERE grant_id=?', (ctx['grant_id'],)).fetchone()
    if row is None or proof is None:return None
    need(_sha(row['response_json']) == row['response_sha256'], 'ACCEPTED_RESPONSE_DIGEST_CHANGED')
    responses = json.loads(row['response_json'])
    need(responses.get('grant_id') == ctx['grant_id'] and len(responses.get('responses') or []) == 3,
        'ALL_THREE_ACCEPTANCES_REQUIRED')
    for response, request in zip(responses['responses'], ctx['body']['requests']):
        validate_media_acceptance(response, request['body'])
    return _validate_verification(ctx, json.loads(proof['proof_ref_json']))


def intake_verification(active, proof_ref):
    """Explicit technical evidence intake; no provider call or new approval."""
    ctx = _context(active);_validate_verification(ctx, proof_ref)
    with active.store._transaction() as db:
        _original(db, ctx)
        row = db.execute('SELECT 1 FROM native_ozon_media_outcomes WHERE grant_id=?', (ctx['grant_id'],)).fetchone()
        need(row is not None, 'ACCEPTED_MEDIA_REQUIRED')
        old = db.execute('SELECT proof_ref_json FROM native_ozon_media_verifications WHERE grant_id=?', (ctx['grant_id'],)).fetchone()
        if old is None:
            db.execute('INSERT INTO native_ozon_media_verifications VALUES(?,?)', (ctx['grant_id'], _json(proof_ref)))
        else:need(old[0] == _json(proof_ref), 'IMMUTABLE_VERIFICATION_CHANGED')
        need(_verified(db, ctx) is not None, 'COMPLETE_VERIFICATION_REQUIRED')


def activate_failed_targets(active):
    if getattr(active, 'plan_id', None) != PLAN or getattr(active, 'decision_id', None) != DECISION:return ()
    ctx = _context(active)
    if ctx.get('partial'):
        with active.store._transaction() as db:
            active.store._require_active_run(db, RUN)
            row = db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, TARGET)).fetchone()
            if tuple(row) not in {('FAILED', 2), ('PENDING', 2)}:return ()
            _original(db, ctx, state=row['status'], attempt=2)
            need(_verified(db, ctx) is not None, 'PARTIAL_ORIGINAL_GALLERIES_UNVERIFIED')
            if row['status'] == 'FAILED':
                changed = db.execute("UPDATE release_target_runs SET status='PENDING',completed_at=NULL WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=2", (RUN, TARGET))
                need(changed.rowcount == 1, 'PARTIAL_FAILED2_CAS_CHANGED')
        return (TARGET,)
    target = next(row for row in active.store.get_run(RUN)['targets'] if row['target_label'] == TARGET)
    state = target.get('storage_status', target['status'])
    if (state, target['attempts']) not in {('FAILED', 1), ('PENDING', 1)}:return ()
    if state == 'FAILED':_intake(ctx)
    with active.store._connect_readonly() as db:
        _original(db, ctx, state=state)
        complete = _verified(db, ctx)
    if complete is None:
        if state != 'FAILED':return ()
        from shared_platform.ozon_runtime_credentials import required_pinned_ozon_credentials
        from modules.ozon.client import ozon_post_bound
        pinned = required_pinned_ozon_credentials();need(str(pinned.account_id) == ACCOUNT, 'BOUND_ACCOUNT_REQUIRED')
        _prepare_media(ctx, lambda path, body: ozon_post_bound(path, body, client_id=pinned.account_id, api_key=pinned.api_key))
        return ()  # Accepted is not all-eight content verification.
    with active.store._transaction() as db:
        _original(db, ctx, state=state);need(_verified(db, ctx) is not None, 'FULL_VERIFICATION_REQUIRED')
        if state == 'FAILED':
            db.execute('INSERT INTO native_ozon_media_starts VALUES(?,2)', (ctx['grant_id'],))
            changed = db.execute("UPDATE release_target_runs SET status='PENDING',completed_at=NULL "
                "WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=1", (RUN, TARGET))
            need(changed.rowcount == 1, 'ORIGINAL_TARGET_CAS_CHANGED')
    return (TARGET,)


def recovery_actions(active, run, actions):
    if getattr(active, 'plan_id', None) != PLAN:return actions
    ctx = _context(active)
    if ctx.get('partial'):
        with active.store._connect_readonly() as db:
            row = db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, TARGET)).fetchone()
            if not row or tuple(row) != ('PENDING', 2):return actions
            _original(db, ctx, state='PENDING', attempt=2)
        return [{**row, 'runnable':True, 'action_kind':'NATIVE_OZON_TWO_CARD_PARTIAL_STOCK',
            'reason_code':'original_two_eligible_cards_original_one_stock_slot_0431_stays_blocked'} if row.get('target_label') == TARGET else row for row in actions]
    with active.store._connect_readonly() as db:
        row = db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, TARGET)).fetchone()
        if not row or tuple(row) != ('PENDING', 1):return actions
        _original(db, ctx, state='PENDING');need(_verified(db, ctx) is not None, 'FULL_VERIFICATION_REQUIRED')
        need(db.execute('SELECT 1 FROM native_ozon_media_starts WHERE grant_id=?', (ctx['grant_id'],)).fetchone(), 'ORIGINAL_START_REQUIRED')
    return [{**row, 'runnable': True, 'action_kind': 'NATIVE_OZON_THREE_CARD_STOCK_CONTINUATION',
        'reason_code': 'original_three_products_eight_images_verified_original_one_stock_slot'}
        if row.get('target_label') == TARGET else row for row in actions]


class _StockOnlyLedger:
    def __init__(self, ctx, base):
        self.ctx, self.base, self.raw_post = ctx, base, None
        self.current_duplicate = None
    def __getattr__(self, key):return getattr(self.base, key)
    def _check(self):
        current = _context(self.ctx['active'])
        need(current['grant_id'] == self.ctx['grant_id'] and (not self.ctx.get('partial') or
            (current.get('partial') or {}).get('digest') == self.ctx['partial']['digest']), 'CURRENT_SOURCE_OR_GRANT_CHANGED')
        with self.base._connect(readonly=True) as db:
            _original(db, self.ctx, state='RUNNING', attempt=3 if self.ctx.get('partial') else 2, stock_allowed=True)
            verified = _verified(db, self.ctx)
            need(verified is not None, 'ALL_THREE_GALLERIES_VERIFIED_REQUIRED')
        return verified
    def reserve_shared(self, operation, *, count=1):
        approved = self._check()
        need(operation == 'update_stock' and type(count) is int and count == 1, 'REIMPORT_OR_EXTRA_MUTATION_FORBIDDEN')
        need(callable(self.raw_post), 'PINNED_ACCOUNT_TRANSPORT_REQUIRED')
        observed = picture_rows(self.raw_post(INFO, {'product_id': list(PRODUCTS.values())}))
        need(observed == approved, 'CURRENT_EIGHT_IMAGE_ORDER_CHANGED')
        self._check()
        return self.base.reserve_shared(operation, count=count)
    def reserve_target(self, *args, **kwargs):need(False, 'ORIGINAL_TARGET_MAXIMUM_ZERO')
    def _stock_body(self, body):
        stocks = body.get('stocks') if type(body) is dict else None
        offers = PARTIAL_OFFERS if self.ctx.get('partial') else PRODUCTS
        need(type(stocks) is list and len(stocks) == len(offers) and {row.get('offer_id') for row in stocks} == set(offers)
            and all(set(row) == {'offer_id', 'stock', 'warehouse_id'} and type(row['stock']) is int and row['stock'] == 200
                and type(row['warehouse_id']) is int and row['warehouse_id'] > 0 for row in stocks)
            and len({row['warehouse_id'] for row in stocks}) == 1, 'EXACT_ORIGINAL_THREE_STOCK200_REQUIRED')
        warehouse = self.ctx['snapshot']['product'].get('ozon_stock_decision', {}).get('warehouse_id')
        need(type(warehouse) is int and stocks[0]['warehouse_id'] == warehouse, 'FROZEN_STOCK_WAREHOUSE_REQUIRED')
        return warehouse
    def dispatch_reserved(self, operation, call, *args, **kwargs):
        self._check();need(operation == 'update_stock' and len(args) == 2
            and args[0] == '/v2/products/stocks' and not kwargs, 'ONLY_ORIGINAL_STOCK_ENDPOINT')
        self._stock_body(args[1])
        return self.base.dispatch_reserved(operation, call, *args, **kwargs)
    def complete_reserved(self, operation, outcome):
        self._check();need(operation == 'update_stock', 'REIMPORT_FORBIDDEN')
        return self.base.complete_reserved(operation, outcome)
    def _absolute_stock_admission(self, offer_ids, warehouse_id, updates):
        self._check()
        offers = PARTIAL_OFFERS if self.ctx.get('partial') else PRODUCTS
        need(set(offer_ids) == set(offers) and len(offer_ids) == len(offers)
            and type(warehouse_id) is int, 'EXACT_ORIGINAL_ABSOLUTE_STOCK_SCOPE')
        body = {'stocks': [{**row, 'warehouse_id': row.get('warehouse_id', warehouse_id)} for row in updates]}
        need(self._stock_body(body) == warehouse_id, 'FROZEN_STOCK_WAREHOUSE_REQUIRED')
        with self.base._connect(readonly=True) as db:
            _original(db, self.ctx, state='RUNNING', attempt=3 if self.ctx.get('partial') else 2, stock_allowed=False)
        return warehouse_id
    def partial_stock_selection(self, variants, existing_items, statuses):
        if not self.ctx.get('partial'):return None
        self._check();by_offer = {r['offer_id']:r for r in existing_items}
        need(len(existing_items) == 3 and set(by_offer) == set(PRODUCTS) and all(by_offer[k].get('id') == v for k,v in PRODUCTS.items())
            and self.current_duplicate is not None, 'PARTIAL_CURRENT_DUPLICATE_OR_IDENTITY_CHANGED')
        status = {r['offer_id']:s for r,s in zip(variants,statuses)}
        need(status.get('2000431') == 'FAILED' and all(status.get(k) == 'PUBLISHED' for k in PARTIAL_OFFERS),
            'PARTIAL_TWO_CONTENT_READBACKS_REQUIRED')
        return tuple(r for r in variants if r['offer_id'] in PARTIAL_OFFERS)
    def wrap_post(self, post, *, provider):
        need(provider == 'ozon' and callable(post), 'BOUND_OZON_TRANSPORT_REQUIRED')
        self.raw_post = post
        def current_read(endpoint,body,*args,**kwargs):
            value = post(endpoint,body,*args,**kwargs)
            if self.ctx.get('partial') and endpoint == '/v3/product/info/list':
                self.current_duplicate = None
                need(not args and not kwargs and body == dict(offer_id=list(PRODUCTS),limit=1000,visibility='ALL'), 'PARTIAL_CURRENT_EXACT_READ_SCOPE_REQUIRED')
                items = value.get('items') if type(value) is dict else None
                by_offer = {r.get('offer_id'):r for r in items} if type(items) is list else {}
                need(type(items) is list and len(items)==3 and set(by_offer)==set(PRODUCTS)
                    and all(by_offer[k].get('id')==v for k,v in PRODUCTS.items()) and _exact_duplicate(by_offer['2000431'])
                    and all(not by_offer[k].get('errors') for k in PARTIAL_OFFERS), 'PARTIAL_CURRENT_DUPLICATE_OR_IDENTITY_CHANGED')
                self.current_duplicate = dict(offer_id='2000431',product_id=PRODUCTS['2000431'],
                    code='SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT',duplicate_offer='2000432',account_id=ACCOUNT,response_sha256=_sha(_json(value)))
            return value
        wrapped = _DurableLedger.wrap_post(self, current_read, provider=provider)
        wrapped._ozon_absolute_stock_admission = self._absolute_stock_admission
        return wrapped
    def snapshot(self):
        value = deepcopy(self.base.snapshot())
        value['technical_media_budget'] = _media_ledger(self.ctx).snapshot()
        return value


def for_active(active, base):
    if getattr(active, 'plan_id', None) != PLAN or base.platform != 'OZON' or base.target_label != TARGET:return base
    ctx = _context(active)
    with base._connect(readonly=True) as db:
        _original(db, ctx, state='RUNNING', attempt=3 if ctx.get('partial') else 2, stock_allowed=True)
        need(_verified(db, ctx) is not None, 'COMPLETE_VERIFICATION_REQUIRED')
        need(db.execute('SELECT 1 FROM native_ozon_media_starts WHERE grant_id=?', (ctx['grant_id'],)).fetchone(),
            'ORIGINAL_START_REQUIRED')
    return _StockOnlyLedger(ctx, base)


def bind_executor(active, ledger, execute):
    if type(ledger) is not _StockOnlyLedger:return execute
    def run(request):
        ledger._check()
        token = _TITLE_SCOPE.set(dict(active=active, snapshot_digest=SNAPSHOT, products=PRODUCTS))
        try:return execute(request)
        finally:_TITLE_SCOPE.reset(token)
    return run


def require_bound_account(ledger, account):
    if type(ledger) is _StockOnlyLedger:need(str(account) == ACCOUNT, 'BOUND_ACCOUNT_REQUIRED')


def retain_original_reference(active, result, ledger=None):
    """Retain three real import receipts; never replace them with an offer ID."""
    if getattr(active, 'plan_id', None) != PLAN or getattr(active, 'decision_id', None) != DECISION:return result
    from dataclasses import replace
    reference = ','.join('ozon:import-task:' + str(task) for task in TASKS)
    with active.store._connect_readonly() as db:
        receipt = active.service._decision(db, DECISION, owner_sid=active.owner_sid)
        row = db.execute('SELECT * FROM release_target_submissions WHERE run_id=? AND target_label=?', (RUN, TARGET)).fetchone()
        need(receipt['snapshot_digest'] == SNAPSHOT and row and row['external_id'] == reference
            and _sha(row['evidence_json']) == row['evidence_digest'], 'ORIGINAL_IMPORT_RECEIPTS_CHANGED')
    evidence = deepcopy(result.readback_evidence or {})
    if type(ledger) is _StockOnlyLedger and ledger.ctx.get('partial'):
        ledger._check()
        canonical = evidence.get('canonical_platform_result') or {}
        targets = canonical.get('targets') or []
        partial = (targets[0].get('evidence') or {}).get('partial_stock') if len(targets) == 1 else None
        if partial is not None:
            need(not result.succeeded and not result.readback_verified and targets[0].get('status') == 'FAILED'
                and partial.get('selected_offers') == list(PARTIAL_OFFERS) and partial.get('blocked_offer') == '2000431'
                and partial.get('all_three_success') is False, 'PARTIAL_MUST_KEEP_AGGREGATE_FAILED')
            prior = next(r for r in ledger.ctx['partial']['image']['release_target_failure_events'] if r['target_label'] == TARGET)
            evidence['native_ozon_two_card_stock'] = dict(schema='native-ozon-two-card-stock-result/v1', plan_id=PLAN,
                run_id=RUN, decision_id=DECISION, snapshot_digest=SNAPSHOT, target_label=TARGET, attempt=3,
                prior_failure_digest=prior['evidence_digest'], prior_submission_digest=row['evidence_digest'],
                grant_id=ledger.ctx['grant_id'], proof_ref=ledger.ctx['partial']['ref'],
                proof_digest=ledger.ctx['partial']['digest'], stock_body=_two_stock_body(), current_duplicate=ledger.current_duplicate)
    if result.succeeded and result.readback_verified:
        need(type(ledger) is _StockOnlyLedger, 'PROTECTED_STOCK_RESULT_REQUIRED')
        ledger._check()
        canonical = evidence.get('canonical_platform_result') or {}
        rows = canonical.get('targets') or []
        need(evidence.get('verified') is True and canonical.get('readback_completed') is True
            and len(rows) == 1 and rows[0].get('target_label') == TARGET and rows[0].get('status') == 'PUBLISHED'
            and (rows[0].get('evidence') or {}).get('stage') == 'STOCK_READBACK', 'FULL_OFFICIAL_STOCK_READBACK_REQUIRED')
        evidence['native_submission_readback'] = dict(decision_id=DECISION, plan_id=PLAN, run_id=RUN,
            target_label=TARGET, attempt=2, prior_submission_digest=row['evidence_digest'], prior_external_id=reference,
            continuation='ORIGINAL_THREE_CARD_VERIFIED_MEDIA_AND_STOCK', grant_id=ledger.ctx['grant_id'])
    return replace(result, external_reference=reference, readback_evidence=evidence)


def has_pending_work(store, decision_id, operations):
    """Pure queue eligibility; execution still needs the protected actor."""
    if decision_id != DECISION:return False
    from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
    bound = getattr(operations, 'agent_prepared_common', None)
    if type(bound) is not AgentPreparedCommonService or bound.store is not store:return False
    task, record = bound._current()  # complete signed current source, never a cached user dict
    with sqlite3.connect(bound.engine.store.path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=?', (task['task_id'], EVENT)).fetchall()
    if len(rows) != 1:return False
    ref = json.loads(json.loads(rows[0][0])['record_json']).get('ozon_media3_decision_ref')
    read_media3_decision(ref)
    partial = _partial_event(bound, task)
    with store._connect_readonly() as db:
        row = db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, TARGET)).fetchone()
        allowed = {('FAILED', 2), ('PENDING', 2)} if partial else {('FAILED', 1), ('PENDING', 1)}
        if not row or tuple(row) not in allowed:return False
        if partial:
            scope = next(r for r in partial['image']['native_final_budget_scopes'] if r['platform'] == 'OZON')
            from shared_platform.native_known_rejected_save_repair import _base_rows
            return _base_rows(db, scope['scope_key'], TARGET) == _stock_rows(partial['image'])
        return True


def partial_stock_projection(db, run_id, target):
    """Same-connection, read-only projection; never grants a mutation or retry."""
    if run_id != RUN or target.get('target_label') != TARGET or target.get('storage_status') != 'FAILED' or target.get('attempts') != 3:
        return None
    failures = target.get('failure_events') or []
    if len(failures) != 2 or [r['attempt'] for r in failures] != [2, 3]:return None
    latest = failures[-1];evidence = latest.get('evidence') or {}
    marker = evidence.get('native_ozon_two_card_stock') or {}
    if marker.get('schema') != 'native-ozon-two-card-stock-result/v1':return None
    expected = dict(plan_id=PLAN,run_id=RUN,decision_id=DECISION,snapshot_digest=SNAPSHOT,target_label=TARGET,
        attempt=3,prior_failure_digest=failures[0]['evidence_digest'],stock_body=_two_stock_body())
    need(all(marker.get(k) == v for k,v in expected.items()), 'PARTIAL_RESULT_IDENTITY_CHANGED')
    submission = target.get('submission') or {}
    need(submission.get('evidence_digest') == marker.get('prior_submission_digest')
        and submission.get('external_id') == ','.join('ozon:import-task:'+str(task) for task in TASKS), 'PARTIAL_IMPORT_RECEIPTS_CHANGED')
    canonical = evidence.get('canonical_platform_result') or {};rows = canonical.get('targets') or []
    need(len(rows) == 1 and rows[0].get('target_label') == TARGET and rows[0].get('status') == 'FAILED', 'PARTIAL_AGGREGATE_MUST_STAY_FAILED')
    partial = (rows[0].get('evidence') or {}).get('partial_stock') or {}
    need(partial.get('schema') == 'ozon-two-eligible-card-stock-partial/v1' and partial.get('selected_offers') == list(PARTIAL_OFFERS)
        and partial.get('blocked_offer') == '2000431' and partial.get('blocked_code') == 'SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT'
        and partial.get('all_three_success') is False, 'PARTIAL_RESULT_SCOPE_CHANGED')
    scope = db.execute("SELECT * FROM native_final_budget_scopes WHERE plan_id=? AND platform='OZON'", (PLAN,)).fetchone()
    need(scope and _sha(scope['binding_json']) == scope['binding_sha256'], 'PARTIAL_RESULT_BUDGET_REQUIRED')
    binding = json.loads(scope['binding_json'])
    need(binding['decision_id'] == DECISION and binding['snapshot_digest'] == SNAPSHOT
        and binding['shared_maximum'] == 4 and binding['per_target_maximum'] == 0, 'PARTIAL_RESULT_ORIGINAL_CAP_REQUIRED')
    from shared_platform.native_known_rejected_save_repair import _base_rows
    ledger = _base_rows(db,scope['scope_key'],TARGET)
    need(len(ledger) in (3,4) and all(r['operation']=='import_variant' and r['outcome']=='ACCEPTED' for r in ledger[:3]), 'PARTIAL_RESULT_IMPORT_PREFIX_CHANGED')
    if len(ledger) == 4:
        from shared_platform.native_final_mutation_budget import _request_sha
        stock = ledger[3]
        need(stock['operation']=='update_stock' and stock['budget_scope']=='shared' and stock['attempt_count']==1
            and stock['request_digest'] in (None,_request_sha('update_stock',('/v2/products/stocks',_two_stock_body()),{})), 'PARTIAL_RESULT_ONLY_EXACT_TWO_STOCK')
    if partial.get('stock_verified') is True:
        from modules.ozon.approved_publication_v4 import _exact_stock_readback
        reads = partial.get('stock_readbacks') or []
        need(reads and _exact_stock_readback(reads[-1],variants=tuple(dict(offer_id=k,stock_quantity=200) for k in PARTIAL_OFFERS))
            and all(type(r.get('warehouse_id')) is int and r['warehouse_id']==PARTIAL_WAREHOUSE for r in reads[-1])
            and (len(ledger)==3 or ledger[3]['outcome']=='ACCEPTED'), 'PARTIAL_EXACT_TWO_OFFICIAL_STOCK_READBACK_REQUIRED')
    return dict(schema='native-ozon-two-card-stock-partial-projection/v1', status='FAILED', partial=True,
        all_three_success=False,stock_verified=partial.get('stock_verified') is True,
        outcome_unknown=evidence.get('outcome_unknown') is True,selected_offers=list(PARTIAL_OFFERS),
        blocked_offer='2000431',blocked_code=partial['blocked_code'],cards=partial['cards'],
        stock_readbacks=partial.get('stock_readbacks') or [],proof_ref=marker.get('proof_ref'),
        proof_digest=marker.get('proof_digest'),original_failure_digest=marker['prior_failure_digest'])


def read_pictures_once(active):
    ctx = _context(active)
    with active.store._connect_readonly() as db:
        _original(db, ctx)
        need(db.execute('SELECT 1 FROM native_ozon_media_outcomes WHERE grant_id=?', (ctx['grant_id'],)).fetchone(),
            'ALL_ACCEPTED_MEDIA_REQUIRED')
    from shared_platform.ozon_runtime_credentials import required_pinned_ozon_credentials
    from modules.ozon.client import ozon_post_bound
    pinned = required_pinned_ozon_credentials();need(str(pinned.account_id) == ACCOUNT, 'BOUND_ACCOUNT_REQUIRED')
    response = ozon_post_bound(INFO, {'product_id': list(PRODUCTS.values())}, client_id=pinned.account_id, api_key=pinned.api_key)
    picture_rows(response)
    return response


def validate_media_acceptance(response, request):
    rows = response.get('result', {}).get('pictures') if type(response) is dict else None
    need(type(rows) is list and len(rows) == 8, 'ACCEPTANCE_UNPROVED')
    for position, row in enumerate(rows):
        need(type(row) is dict and row.get('product_id') == request['product_id']
            and row.get('url') == request['images'][position] and row.get('state') == 'imported'
            and type(row.get('is_primary')) is bool and row['is_primary'] == (position == 0)
            and row.get('is_360') is False and row.get('is_color') is False, 'ACCEPTANCE_UNPROVED')


def picture_rows(response):
    rows = response.get('items') if type(response) is dict else None
    need(type(rows) is list and len(rows) == 3, 'EXACT_THREE_PICTURES_REQUIRED')
    result = {}
    for row in rows:
        need(type(row) is dict and row.get('product_id') in PRODUCTS.values()
            and row['product_id'] not in result and row.get('errors') == []
            and row.get('color_photo') == [] and row.get('photo_360') == []
            and type(row.get('primary_photo')) is list and len(row['primary_photo']) == 1
            and type(row.get('photo')) is list and len(row['photo']) == 7, 'ALL_EIGHT_PICTURES_REQUIRED')
        urls = row['primary_photo'] + row['photo']
        need(len(set(urls)) == 8 and all(type(url) is str and url.startswith('https://') for url in urls),
            'ALL_EIGHT_DISTINCT_HTTPS_REQUIRED')
        result[row['product_id']] = urls
    need(set(result) == set(PRODUCTS.values()), 'EXACT_THREE_PRODUCTS_REQUIRED')
    return result


def title_matches(expected, observed):
    """Pure comparison; default remains exact outside the protected scope."""
    if observed.get('name') == expected.get('title'):
        return True
    scope = _TITLE_SCOPE.get()
    if scope is None:
        return False
    from shared_platform.native_sole_final_execution import _ACTIVE
    if scope.get('active') is not _ACTIVE.get():return False
    offer = expected.get('offer_id');frozen = expected.get('title')
    return (offer in PRODUCTS and observed.get('offer_id') == offer
        and observed.get('id') == PRODUCTS[offer] and scope.get('snapshot_digest') == SNAPSHOT
        and scope.get('products') == PRODUCTS and type(frozen) is str and frozen.count(' | ') == 1
        and observed.get('name') == frozen.replace(' | ', ' ', 1))
