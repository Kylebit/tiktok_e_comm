"""One existing 0989 product's technical media delivery; never a new import.

Explicit maintenance intakes precede every live phase. Missing Shopee evidence
blocks even DDL. Media acceptance is distinct from all-eight content/order
verification; stock remains on the original immutable shared2 ledger.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold, _json, _sha
from shared_platform.native_known_rejected_save_repair import _read_reference, _base_rows

PLAN = 'r3-marketplace:4039931504:b21fbd9f11f01e3d04fc0780'
RUN = 'release-run:715e2b6e14103dbcdb129128'
DECISION = 'native-final-decision:da22e734faad76bf1de4b00ffca5dba3421682d0ce94f68dc6595b7440935ef6'
SNAPSHOT = 'sha256:ed9fb745466e25ae91da35a21f349ccec5dd7703fc14d217e04fd4447472d1c1'
TARGET = 'ozon:RU'
PRODUCT = 6664920146
ACCOUNT = '4953064'
OFFER = '0989'
TASK = 5847550407
ROOT = Path('E:/OrbitEvidence/4039931504-publication-20261019')
LIVE_ROOT = ROOT / 'ROOT_OZON_MEDIA_RECOVERY_LIVE_V2.json'
VERIFY_ROOT = ROOT / 'ROOT_OZON_MEDIA_CONTENT_VERIFIED.json'
MODULE = 'shared_platform/native_ozon_existing_media_recovery.py'
ORIGINAL_TASK_READBACK_REF = {
    'path': str(ROOT / 'ozon-original-import-readonly/OFFICIAL_ORIGINAL_TASK_READBACK.json'),
    'sha256': '9a565bf19ae0ef164ac63bb8739d799748880e1b64ad168cd5b7761a3a915012',
}

IMPORT = '/v1/product/pictures/import'
INFO = '/v2/product/pictures/info'
SCHEMA = '''
CREATE TABLE native_ozon_media_grants (grant_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, body_json TEXT NOT NULL, body_sha256 TEXT NOT NULL);
CREATE TABLE native_ozon_media_attempts (grant_id TEXT PRIMARY KEY, request_json TEXT NOT NULL, request_sha256 TEXT NOT NULL, FOREIGN KEY(grant_id) REFERENCES native_ozon_media_grants(grant_id));
CREATE TABLE native_ozon_media_dispatches (grant_id TEXT PRIMARY KEY, FOREIGN KEY(grant_id) REFERENCES native_ozon_media_attempts(grant_id));
CREATE TABLE native_ozon_media_outcomes (grant_id TEXT PRIMARY KEY, response_json TEXT NOT NULL, response_sha256 TEXT NOT NULL, FOREIGN KEY(grant_id) REFERENCES native_ozon_media_dispatches(grant_id));
CREATE TABLE native_ozon_media_verifications (grant_id TEXT PRIMARY KEY, proof_ref_json TEXT NOT NULL, FOREIGN KEY(grant_id) REFERENCES native_ozon_media_outcomes(grant_id));
CREATE TABLE native_ozon_media_starts (grant_id TEXT PRIMARY KEY, target_attempt INTEGER NOT NULL CHECK(target_attempt=2), FOREIGN KEY(grant_id) REFERENCES native_ozon_media_verifications(grant_id));
'''
for _table in ('grants', 'attempts', 'dispatches', 'outcomes', 'verifications', 'starts'):
    for _event in ('UPDATE', 'DELETE'):
        SCHEMA += ('CREATE TRIGGER native_ozon_media_' + _table + '_no_' + _event.lower()
                   + ' BEFORE ' + _event + ' ON native_ozon_media_' + _table
                   + " BEGIN SELECT RAISE(ABORT,'immutable Ozon media recovery'); END;\n")


def need(value, code):
    if not value:
        raise NativeMutationBudgetHold('OZON_MEDIA_' + code)


def statements():
    result = []; current = ''
    for line in SCHEMA.splitlines(keepends=True):
        current += line
        if sqlite3.complete_statement(current):
            result.append(current.strip()); current = ''
    need(not current.strip(), 'SCHEMA_INCOMPLETE')
    return result


def check_schema(db, *, optional=False):
    expected = {re.search(r'CREATE (?:TABLE|TRIGGER) (\w+)', s)[1]: s for s in statements()}
    actual = {r['name']: r['sql'] for r in db.execute(
        "SELECT name,sql FROM sqlite_master WHERE name LIKE 'native_ozon_media_%'")}
    if not actual and optional:
        return False
    norm = lambda s: re.sub(r'\s+', ' ', s.strip().rstrip(';')).lower()
    need(set(actual) == set(expected) and all(norm(actual[k]) == norm(v) for k, v in expected.items()),
         'EXPLICIT_APPEND_ONLY_SCHEMA_REQUIRED')
    return True


def _reference(ref):
    return json.loads(_read_reference(ref))


def _https(value):
    need(type(value) is str, 'OFFICIAL_HTTPS_URL_REQUIRED')
    u = urlsplit(value)
    need(u.scheme == 'https' and bool(u.hostname) and not u.username and not u.password
         and not u.fragment and u.port in (None, 443), 'OFFICIAL_HTTPS_URL_REQUIRED')
    return value


def _identity(doc):
    need((doc.get('plan_id'), doc.get('run_id'), doc.get('decision_id'), doc.get('snapshot_digest'),
          doc.get('target_label'), doc.get('account_id'), doc.get('product_id'), doc.get('offer_id'))
         == (PLAN, RUN, DECISION, SNAPSHOT, TARGET, ACCOUNT, PRODUCT, OFFER), 'EXACT_IDENTITY_REQUIRED')


def _root(ref, *, verification=False):
    need(set(ref) == {'path', 'sha256'} and Path(ref['path']).resolve() ==
         (VERIFY_ROOT if verification else LIVE_ROOT).resolve(), 'FIXED_ROOT_REFERENCE_REQUIRED')
    doc = _reference(ref); _identity(doc)
    need(doc.get('schema') == ('root-ozon-media-content-verification/v1' if verification else
                              'root-ozon-existing-media-recovery/v1')
         and doc.get('status') == ('LIVE_TECHNICAL_MEDIA_VERIFICATION' if verification else 'LIVE_ADOPTION_APPROVED'),
         'LIVE_ROOT_REQUIRED')
    pins = _reference(doc['source_pins_ref'])
    package = Path(__file__).resolve().parents[1]
    required = (MODULE, 'shared_platform/native_prepared_v4_targets.py', 'modules/products/server.py')
    need(set(pins) == set(required), 'EXACT_THREE_REVIEWED_SOURCES_REQUIRED')
    if not all(hashlib.sha256((package/name).read_bytes()).hexdigest()==pins[name]['after_sha256'] for name in required):
        _exact_source_compatibility(ref,doc,pins,package,required)
    return doc


def delivery(body):
    """Writer wrapper: original current source gate before pure objects."""
    _identity(body)
    root = _root(body['root_ref'])
    return _delivery_objects(body, root)


def _delivery_objects(body, root):
    """Frozen source/imageID/CDN objects; caller must validate Root first."""
    need(root.get('grant_core_digest') == _sha(_json({k: v for k, v in body.items() if k != 'root_ref'}))
         and root.get('media_maximum') == 1 and type(root.get('media_maximum')) is int
         and root.get('original_import_task_id') == TASK, 'EXACT_ONE_MEDIA_ROOT_GRANT_REQUIRED')
    frozen = _reference(body['approved_snapshot_ref'])
    need(frozen.get('snapshot_digest') == SNAPSHOT, 'APPROVED_SNAPSHOT_REQUIRED')
    routes = frozen['product']['image_routing']['routes']
    urls = routes[TARGET]['ordered_images']
    need(len(urls) == 8 and len(set(urls)) == 8 and all(_https(u) for u in urls), 'EXACT_FROZEN_EIGHT_REQUIRED')
    cp = _reference(body['shopee_checkpoint_ref'])
    from shared_platform.native_prepared_v4_targets import _file_identity
    mapping = _file_identity(run_id=RUN, plan_id=PLAN, decision_id=DECISION, snapshot_digest=SNAPSHOT)
    need(cp.get('schema_version') == 'shopee-global-v4-checkpoint/v1' and cp.get('offer_id') == '4039931504'
         and str(cp.get('product_revision')) == '2' and cp.get('run_id') == mapping['file_run_id']
         and cp.get('report_id') == mapping['file_report_id'], 'EXACT_SAME_PRODUCT_CHECKPOINT_REQUIRED')
    global_id = cp.get('global_item_id')
    need(type(global_id) in (str, int) and str(global_id).isdigit() and int(global_id) > 0,
         'EXACT_GLOBAL_ITEM_REQUIRED')
    official = _reference(body['shopee_global_read_ref'])
    need(official.get('schema') == 'bound-shopee-global-image-readback/v1'
         and official.get('path') == '/api/v2/global_product/get_global_item_info'
         and official.get('params') == {'global_item_id_list': str(global_id)}
         and official.get('partner_id') == 2036649 and official.get('merchant_id') == 4970102
         and official.get('checkpoint_ref') == body['shopee_checkpoint_ref'], 'OFFICIAL_GLOBAL_READ_PROVENANCE_REQUIRED')
    raw = _reference(official['raw_response_ref'])
    need(raw.get('error') in (None, '', '-'), 'OFFICIAL_GLOBAL_ERROR')
    rows = raw.get('response', {}).get('global_item_list')
    need(type(rows) is list and len(rows) == 1 and str(rows[0].get('global_item_id')) == str(global_id),
         'OFFICIAL_GLOBAL_IDENTITY_CHANGED')
    ids = rows[0].get('image', {}).get('image_id_list')
    cdn = rows[0].get('image', {}).get('image_url_list')
    need(type(ids) is list and type(cdn) is list and len(ids) == len(cdn) == 8
         and all(type(i) is str and i for i in ids) and len(set(ids)) == 8
         and len(set(cdn)) == 8 and all(_https(u) for u in cdn), 'FULL_OFFICIAL_IMAGE_MAP_REQUIRED')
    bindings = cp.get('image_bindings')
    need(type(bindings) is dict and all(type(bindings.get(u)) is str for u in urls), 'SOURCE_TO_IMAGE_ID_REQUIRED')
    selected_ids = [bindings[u] for u in urls]
    need(len(set(selected_ids)) == 8 and set(selected_ids) == set(ids), 'COMPLETE_SOURCE_ID_JOIN_REQUIRED')
    selected = [dict(zip(ids, cdn))[i] for i in selected_ids]
    need(body.get('ordered_delivery_urls') == selected, 'ORDERED_OFFICIAL_DELIVERY_CHANGED')
    return urls, selected


def _original_import_receipt(row):
    """Exact retained official task/account/product join; never message inference."""
    need(row['external_id'] == 'ozon:import-task:' + str(TASK), 'ORIGINAL_IMPORT_TASK_REQUIRED')
    doc = _reference(ORIGINAL_TASK_READBACK_REF)
    need(doc.get('schema') == 'original-approved-ozon-import-task-readonly/v1'
         and doc.get('run_id') == RUN and doc.get('task_id') == str(TASK)
         and doc.get('account') == {'ozon_account_id': ACCOUNT,
             'ozon_credentials_sha256': '1d47669e405f0383d3a7bdd7f23d9983c2bec20cf375d453622a065122688bcf'}
         and doc.get('provider_mutation') == doc.get('DB_write') == doc.get('auth_refresh') == 0,
         'ORIGINAL_OFFICIAL_TASK_BINDING_REQUIRED')
    calls = doc.get('calls')
    need(type(calls) is list and len(calls) == 2, 'ORIGINAL_OFFICIAL_TASK_BINDING_REQUIRED')
    task, offer = calls
    need(task.get('endpoint') == '/v1/product/import/info' and task.get('request') == {'task_id': TASK}
         and task.get('response_ref') == {
             'path': str(ROOT / 'ozon-original-import-readonly/TASK_READBACK.json'),
             'sha256': '021a38813583d9f16e1f25c5f379a4a49e0f206811c5fef8b7ee46bf014f45f5'},
         'ORIGINAL_OFFICIAL_TASK_BINDING_REQUIRED')
    raw = _reference(task['response_ref'])
    need(raw == task.get('response'), 'ORIGINAL_OFFICIAL_TASK_RESPONSE_CHANGED')
    result = raw.get('result') or {}; items = result.get('items')
    need(type(items) is list and len(items) == 1 and result.get('total') == 1
         and items[0].get('offer_id') == OFFER and type(items[0].get('product_id')) is int
         and items[0]['product_id'] == PRODUCT and items[0].get('status') == 'imported',
         'ORIGINAL_OFFICIAL_IMPORTED_PRODUCT_REQUIRED')
    need(offer.get('endpoint') == '/v3/product/info/list'
         and offer.get('request') == {'offer_id': [OFFER], 'limit': 1000, 'visibility': 'ALL'}
         and offer.get('response_ref') == {
             'path': str(ROOT / 'ozon-original-import-readonly/OFFER_READBACK.json'),
             'sha256': 'dd5e9af8c8221c5f2276d5eff7faeb74f3522db02d75814681cf33cab23ceb09'},
         'ORIGINAL_OFFICIAL_OFFER_BINDING_REQUIRED')
    raw_offer = _reference(offer['response_ref']); entries = raw_offer.get('items')
    need(raw_offer == offer.get('response') and type(entries) is list and len(entries) == 1
         and type(entries[0].get('id')) is int and entries[0]['id'] == PRODUCT
         and entries[0].get('offer_id') == OFFER and entries[0].get('is_archived') is False,
         'ORIGINAL_OFFICIAL_OFFER_PRODUCT_REQUIRED')


def original(db, body, *, status, attempt):
    run = db.execute('SELECT plan_id FROM release_runs WHERE run_id=?', (RUN,)).fetchone()
    need(run is not None and run['plan_id'] == PLAN, 'ORIGINAL_RUN_CHANGED')
    rows = _base_rows(db, body['scope_key'], TARGET)
    need(rows and rows[0]['operation'] == 'import_variant' and rows[0]['outcome'] == 'ACCEPTED'
         and rows[0]['attempt_count'] == 1 and rows[0]['budget_scope'] == 'shared'
         and re.fullmatch('[0-9a-f]{64}', rows[0]['request_digest'] or ''), 'ORIGINAL_ACCEPTED_IMPORT_REQUIRED')
    scope = db.execute('SELECT * FROM native_final_budget_scopes WHERE scope_key=?', (body['scope_key'],)).fetchone()
    need(scope is not None and _sha(scope['binding_json']) == scope['binding_sha256'], 'ORIGINAL_BUDGET_CHANGED')
    binding = json.loads(scope['binding_json'])
    need((binding['plan_id'], binding['snapshot_digest'], binding['decision_id'], binding['platform'],
          binding['target_labels'], binding['shared_maximum'], binding['per_target_maximum'])
         == (PLAN, SNAPSHOT, DECISION, 'OZON', [TARGET], 2, 0), 'ORIGINAL_SHARED2_TARGET0_REQUIRED')
    need(rows[:1] == body['original_reservations'] and len(rows) <= 2, 'ORIGINAL_IMPORT_HISTORY_CHANGED')
    if len(rows) == 2:
        need(status == 'RUNNING' and rows[1]['operation'] == 'update_stock' and rows[1]['budget_scope'] == 'shared'
             and rows[1]['attempt_count'] == 1, 'NO_OTHER_OR_REPEATED_STOCK_SLOT')
    row = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, TARGET)).fetchone()
    need(row and row['status'] == status and row['attempts'] == attempt, 'ORIGINAL_TARGET_STATE_CHANGED')
    failures = db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? ORDER BY attempt',
                          (RUN, TARGET)).fetchall()
    if len(failures) == 1:
        need(failures[0]['attempt'] == 1 and failures[0]['evidence_digest'] == body['failure_evidence_digest']
             and _sha(failures[0]['evidence_json']) == body['failure_evidence_digest'], 'ORIGINAL_FAILURE_CHANGED')
        _original_import_receipt(row)
    else:
        past=_stock_zero_original(db,body,row,failures,status=status,attempt=attempt,budget_rows=rows)
        _original_import_receipt(past)
    for table in ('release_target_submissions', 'release_target_readbacks', 'release_target_repairs'):
        need(db.execute('SELECT 1 FROM ' + table + ' WHERE run_id=? AND target_label=?', (RUN, TARGET)).fetchone() is None,
             'EXISTING_SUBMISSION_OR_SUCCESS_NO_REPLAY')


def install_and_intake(db, body):
    """Explicit maintenance ONLY, within the caller's BEGIN IMMEDIATE."""
    need(db.in_transaction, 'EXPLICIT_MAINTENANCE_TRANSACTION_REQUIRED')
    delivery(body)  # All real inputs before even DDL; no synthetic live authority.
    original(db, body, status='FAILED', attempt=1)
    if not check_schema(db, optional=True):
        for sql in statements():
            db.execute(sql)
    check_schema(db)
    raw = _json(body); grant_id = _sha(raw)
    db.execute('INSERT INTO native_ozon_media_grants VALUES(?,?,?,?)', (grant_id, RUN, raw, _sha(raw)))
    return grant_id


def protected(active):
    from shared_platform.native_sole_final_execution import _ACTIVE, _NativeExecution
    need(type(active) is _NativeExecution and _ACTIVE.get() is active and active.plan_id == PLAN
         and active.decision_id == DECISION and active.service.store is active.store
         and active.service._actor()['owner_sid'] == active.owner_sid, 'PROTECTED_CURRENT_ACTOR_REQUIRED')
    from shared_platform.native_prepared_v4_targets import _scope
    _scope(active, active.store.get_plan(PLAN))


def _grant(db):
    if not check_schema(db, optional=True):
        return None
    row = db.execute('SELECT * FROM native_ozon_media_grants WHERE run_id=?', (RUN,)).fetchone()
    if row:
        need(_sha(row['body_json']) == row['grant_id'] == row['body_sha256'], 'PERSISTED_GRANT_CHANGED')
    return row


def recheck(active, db, row, *, status='FAILED', attempt=1):
    protected(active)
    receipt = active.service._decision(db, DECISION, owner_sid=active.owner_sid)
    need(receipt['snapshot_digest'] == SNAPSHOT, 'CURRENT_APPROVAL_CHANGED')
    body = json.loads(row['body_json']); delivery(body)
    original(db, body, status=status, attempt=attempt)
    need(_grant(db)['grant_id'] == row['grant_id'], 'CURRENT_GRANT_CHANGED')
    return body


def validate_import(response, urls):
    rows = response.get('result', {}).get('pictures') if type(response) is dict else None
    need(type(rows) is list and len(rows) == 8, 'MEDIA_ACCEPTANCE_UNPROVED')
    for i, row in enumerate(rows):
        need(row.get('product_id') == PRODUCT and row.get('url') == urls[i] and row.get('state') == 'imported'
             and type(row.get('is_primary')) is bool and row['is_primary'] == (i == 0)
             and row.get('is_360') is False and row.get('is_color') is False, 'MEDIA_ACCEPTANCE_UNPROVED')


def image_urls(response):
    rows = response.get('items') if type(response) is dict else None
    need(type(rows) is list and len(rows) == 1 and rows[0].get('product_id') == PRODUCT,
         'READBACK_PRODUCT_CHANGED')
    row = rows[0]
    need(row.get('errors') == [] and row.get('color_photo') == [] and row.get('photo_360') == []
         and type(row.get('primary_photo')) is list and len(row['primary_photo']) == 1
         and type(row.get('photo')) is list and len(row['photo']) == 7, 'READBACK_EIGHT_UNPROVED')
    urls = row['primary_photo'] + row['photo']
    need(len(set(urls)) == 8 and all(_https(u) for u in urls), 'READBACK_EIGHT_UNPROVED')
    return urls


def prepare_media_once(active, bound_post):
    """Protected technical phase; never retries an attempted media mutation.

    This runs while the original target is still FAILED/attempt1. It does not
    start attempt2, mark a storefront published, or reserve the stock slot.
    """
    protected(active)
    with active.store._connect_readonly() as db:
        row = _grant(db)
        if row is None:
            return None
        body = recheck(active, db, row)
        prior = db.execute('SELECT 1 FROM native_ozon_media_attempts WHERE grant_id=?', (row['grant_id'],)).fetchone()
    if prior:
        return row['grant_id']  # UNKNOWN also stays spent forever; no new wire.
    urls = delivery(body)[1]; request = {'product_id': PRODUCT, 'images': urls}
    with active.store._transaction() as db:
        recheck(active, db, row)
        db.execute('INSERT INTO native_ozon_media_attempts VALUES(?,?,?)',
                   (row['grant_id'], _json(request), _sha(_json(request))))
    # An interrupted process between the two durable records cannot retry.
    with active.store._transaction() as db:
        recheck(active, db, row)
        db.execute('INSERT INTO native_ozon_media_dispatches VALUES(?)', (row['grant_id'],))
    response = bound_post(IMPORT, request)  # Original exact pinned account transport only.
    validate_import(response, urls)
    raw = _json(response)
    with active.store._transaction() as db:
        recheck(active, db, row)
        db.execute('INSERT INTO native_ozon_media_outcomes VALUES(?,?,?)', (row['grant_id'], raw, _sha(raw)))
    return row['grant_id']


def read_pictures_once(active, bound_post):
    """Same-account exact-product READ for the operator's pinned evidence.

    Returns the actual response. Count/order-shape alone never grants stock.
    """
    protected(active)
    with active.store._connect_readonly() as db:
        row = _grant(db)
        need(row is not None, 'EXISTING_GRANT_REQUIRED')
        recheck(active, db, row)
        need(db.execute('SELECT 1 FROM native_ozon_media_outcomes WHERE grant_id=?', (row['grant_id'],)).fetchone(),
             'MEDIA_ACCEPTED_REQUIRED')
    value = bound_post(INFO, {'product_id': [PRODUCT]})
    image_urls(value)
    return value


def validate_verification(body, response, proof_ref):
    """Writer wrapper retains original live proof and delivery source gates."""
    root = _root(proof_ref, verification=True)
    need(root.get('grant_id') == _sha(_json(body)) and root.get('media_response_digest') == _sha(_json(response))
         and root.get('root_grant_ref') == body['root_ref'], 'TECHNICAL_PROOF_GRANT_CHANGED')
    return _verification_objects(body, response, root, delivery(body)[0])


def _verification_objects(body, response, root, sources):
    """Pure original eight-image visual and immutable accepted-response join."""
    need(root.get('grant_id') == _sha(_json(body)) and root.get('media_response_digest') == _sha(_json(response))
         and root.get('root_grant_ref') == body['root_ref'], 'TECHNICAL_PROOF_GRANT_CHANGED')
    observed = _reference(root['pictures_info_ref'])
    urls = image_urls(observed)
    assessment = _reference(root['content_assessment_ref'])
    need(assessment.get('schema') == 'ozon-eight-image-content-order-assessment/v1'
         and assessment.get('status') == 'PASS' and assessment.get('method') == 'ACTUAL_TECHNICAL_VISUAL_REVIEW'
         and assessment.get('pictures_info_ref') == root['pictures_info_ref'], 'FULL_CONTENT_ASSESSMENT_REQUIRED')
    positions = assessment.get('positions')
    need(type(positions) is list and len(positions) == 8, 'ALL_EIGHT_POSITIONS_REQUIRED')
    for i, item in enumerate(positions):
        need(item.get('position') == i + 1 and item.get('source_url_sha256') == _sha(sources[i])
             and item.get('ozon_image_url') == urls[i] and item.get('same_approved_image') is True
             and item.get('order_matches') is True and type(item.get('is_primary')) is bool
             and item['is_primary'] == (i == 0), 'FULL_CONTENT_ORDER_NOT_VERIFIED')
        _read_reference(item['actual_visual_evidence_ref'])
    return root


def intake_verification(db, grant_id, proof_ref):
    """Second explicit maintenance intake, no new media or approval."""
    need(db.in_transaction, 'EXPLICIT_MAINTENANCE_TRANSACTION_REQUIRED')
    check_schema(db); row = _grant(db)
    need(row and row['grant_id'] == grant_id, 'EXISTING_GRANT_REQUIRED')
    body = json.loads(row['body_json']); original(db, body, status='FAILED', attempt=1)
    outcome = db.execute('SELECT * FROM native_ozon_media_outcomes WHERE grant_id=?', (grant_id,)).fetchone()
    need(outcome and _sha(outcome['response_json']) == outcome['response_sha256'], 'MEDIA_ACCEPTED_REQUIRED')
    validate_import(json.loads(outcome['response_json']), delivery(body)[1])
    validate_verification(body, json.loads(outcome['response_json']), proof_ref)
    db.execute('INSERT INTO native_ozon_media_verifications VALUES(?,?)', (grant_id, _json(proof_ref)))


def verified(db, row):
    proof = db.execute('SELECT proof_ref_json FROM native_ozon_media_verifications WHERE grant_id=?', (row['grant_id'],)).fetchone()
    if not proof:
        return False
    outcome = db.execute('SELECT * FROM native_ozon_media_outcomes WHERE grant_id=?', (row['grant_id'],)).fetchone()
    need(outcome and _sha(outcome['response_json']) == outcome['response_sha256'], 'ACCEPTED_MEDIA_CHANGED')
    body = json.loads(row['body_json']); response = json.loads(outcome['response_json'])
    validate_import(response, delivery(body)[1])
    validate_verification(body, response, json.loads(proof['proof_ref_json']))
    return True


def activate_failed_targets(active):
    if getattr(active, 'plan_id', None) != PLAN or getattr(active, 'decision_id', None) != DECISION:
        return ()
    protected(active)
    with active.store._connect_readonly() as db:
        row = _grant(db)
        if row is None:
            return ()
        target = db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, TARGET)).fetchone()
        if target and (target['status'], target['attempts']) != ('FAILED', 1):
            return ()
        recheck(active, db, row)
        complete = verified(db, row)
        attempted = db.execute('SELECT 1 FROM native_ozon_media_attempts WHERE grant_id=?', (row['grant_id'],)).fetchone()
    if not complete:
        if attempted:
            return ()  # Accepted or UNKNOWN waits; no credential read or retry.
        from shared_platform.ozon_runtime_credentials import required_pinned_ozon_credentials
        from modules.ozon.client import ozon_post_bound
        pinned = required_pinned_ozon_credentials()
        need(str(pinned.account_id) == ACCOUNT, 'ACCOUNT_CHANGED')
        prepare_media_once(active, lambda path, body: ozon_post_bound(path, body, client_id=pinned.account_id, api_key=pinned.api_key))
        return ()  # Media ACCEPTED is not full verification or target success.
    with active.store._transaction() as db:
        recheck(active, db, row); need(verified(db, row), 'FULL_VERIFICATION_REQUIRED')
        db.execute('INSERT INTO native_ozon_media_starts VALUES(?,2)', (row['grant_id'],))
        changed = db.execute("UPDATE release_target_runs SET status='PENDING',completed_at=NULL WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=1", (RUN, TARGET))
        need(changed.rowcount == 1, 'ORIGINAL_TARGET_CAS_CHANGED')
    return (TARGET,)


def recovery_actions(active, run, actions):
    if getattr(active, 'plan_id', None) != PLAN or getattr(active, 'decision_id', None) != DECISION:
        return actions
    protected(active)
    with active.store._connect_readonly() as db:
        row = _grant(db)
        if row is None:
            return actions
        target = next((x for x in run['targets'] if x['target_label'] == TARGET), {})
        if (target.get('status'), target.get('attempts')) != ('PENDING', 1):
            return actions
        recheck(active, db, row, status='PENDING'); need(verified(db, row), 'FULL_VERIFICATION_REQUIRED')
        need(db.execute('SELECT 1 FROM native_ozon_media_starts WHERE grant_id=?', (row['grant_id'],)).fetchone(), 'ORIGINAL_START_REQUIRED')
    return [{**a, 'runnable': True, 'action_kind': 'NATIVE_OZON_EXISTING_MEDIA_STOCK_CONTINUATION',
             'reason_code': 'exact_existing_product_eight_images_verified_original_stock_slot_only'}
            if a.get('target_label') == TARGET else a for a in actions]


def for_active(active, base):
    if base.platform != 'OZON' or base.target_label != TARGET:
        return base
    protected(active)
    with base._connect(readonly=True) as db:
        row = _grant(db)
        if row is None:
            return base
        current=db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?',(RUN,TARGET)).fetchone()
        need(current is not None and current['attempts'] in (2,3,4),'EXACT_STOCK_TARGET_ATTEMPT_REQUIRED')
        stock_attempt=current['attempts']
        recheck(active, db, row, status='RUNNING', attempt=stock_attempt)
        need(verified(db, row), 'FULL_VERIFICATION_REQUIRED')
        need(db.execute('SELECT 1 FROM native_ozon_media_starts WHERE grant_id=?', (row['grant_id'],)).fetchone(), 'ORIGINAL_START_REQUIRED')
    return _StockOnlyLedger(active, base, row, attempt=stock_attempt)


def require_bound_account(ledger, account):
    if type(ledger) is _StockOnlyLedger:
        need(str(account) == ACCOUNT, 'ACCOUNT_CHANGED')


class _StockOnlyLedger:
    def __init__(self, active, base, row, *, attempt=2):
        self.active = active; self.base = base; self.row = row; self.attempt=attempt
    def __getattr__(self, key):
        return getattr(self.base, key)
    def _check(self):
        if self.base._recheck:
            self.base._recheck()
        with self.base._connect(readonly=True) as db:
            recheck(self.active, db, self.row, status='RUNNING', attempt=self.attempt)
            need(verified(db, self.row), 'FULL_VERIFICATION_REQUIRED')
    def reserve_shared(self, operation, *, count=1):
        self._check()
        need(operation == 'update_stock' and type(count) is int and count == 1, 'REIMPORT_OR_EXTRA_MUTATION_FORBIDDEN')
        need(callable(getattr(self, '_media_read_post', None)), 'BOUND_MEDIA_READ_TRANSPORT_REQUIRED')
        with self.base._connect(readonly=True) as db:
            proof = db.execute('SELECT proof_ref_json FROM native_ozon_media_verifications WHERE grant_id=?',
                               (self.row['grant_id'],)).fetchone()
        root = _root(json.loads(proof['proof_ref_json']), verification=True)
        approved_urls = image_urls(_reference(root['pictures_info_ref']))
        observed = self._media_read_post(INFO, {'product_id': [PRODUCT]})
        need(image_urls(observed) == approved_urls, 'CURRENT_MEDIA_CHANGED_NO_STOCK')
        self._check()  # READ completed before the original stock reservation.
        return self.base.reserve_shared(operation, count=count)
    def reserve_target(self, *args, **kwargs):
        need(False, 'ORIGINAL_TARGET_MAXIMUM_ZERO')
    def dispatch_reserved(self, operation, call, *args, **kwargs):
        self._check(); need(operation == 'update_stock', 'REIMPORT_FORBIDDEN')
        need(len(args) == 2 and args[0] == '/v2/products/stocks' and not kwargs,
             'ORIGINAL_STOCK_WIRE_REQUIRED')
        stocks = args[1].get('stocks') if type(args[1]) is dict else None
        need(type(stocks) is list and len(stocks) == 1 and set(stocks[0]) == {'offer_id', 'stock', 'warehouse_id'}
             and stocks[0]['offer_id'] == OFFER and type(stocks[0]['stock']) is int and stocks[0]['stock'] == 200
             and type(stocks[0]['warehouse_id']) is int and stocks[0]['warehouse_id'] > 0,
             'ORIGINAL_STOCK200_BODY_REQUIRED')
        return self.base.dispatch_reserved(operation, call, *args, **kwargs)
    def complete_reserved(self, operation, outcome):
        self._check(); need(operation == 'update_stock', 'REIMPORT_FORBIDDEN')
        return self.base.complete_reserved(operation, outcome)
    def wrap_post(self, post, *, provider):
        from shared_platform.native_final_mutation_budget import _DurableLedger
        need(provider == 'ozon' and callable(post), 'BOUND_OZON_TRANSPORT_REQUIRED')
        self._media_read_post = post
        wrapped = _DurableLedger.wrap_post(self, post, provider=provider)
        wrapped._ozon_absolute_stock_admission = self._absolute_stock_admission
        return wrapped
    def _absolute_stock_admission(self, offer_ids, warehouse_id, updates):
        # A private callback on this protected ledger, not an API Boolean.
        self._check()
        need(self.attempt == 4 and offer_ids == (OFFER,), 'ABSOLUTE_STOCK_ATTEMPT4_ONLY')
        root = _absolute_stock_root(json.loads(self.row['body_json']))
        need(type(warehouse_id) is int and warehouse_id == root['warehouse_id'], 'ABSOLUTE_STOCK_WAREHOUSE_CHANGED')
        need(type(updates) is tuple and len(updates) == 1 and type(updates[0]) is dict
             and set(updates[0]) <= {'offer_id','stock','warehouse_id'}
             and updates[0].get('offer_id') == OFFER and type(updates[0].get('stock')) is int
             and updates[0]['stock'] == 200 and updates[0].get('warehouse_id',warehouse_id) == warehouse_id,
             'ABSOLUTE_STOCK200_UPDATES_REQUIRED')
        with self.base._connect(readonly=True) as db:
            rows = _base_rows(db, json.loads(self.row['body_json'])['scope_key'], TARGET)
            need(len(rows) == 1 and rows == json.loads(self.row['body_json'])['original_reservations'],
                 'ABSOLUTE_STOCK_INTENT_ALREADY_EXISTS_NO_REPLAY')
        return warehouse_id
    def snapshot(self):
        value = deepcopy(self.base.snapshot())
        with self.base._connect(readonly=True) as db:
            attempt = db.execute('SELECT 1 FROM native_ozon_media_attempts WHERE grant_id=?', (self.row['grant_id'],)).fetchone()
            outcome = db.execute('SELECT 1 FROM native_ozon_media_outcomes WHERE grant_id=?', (self.row['grant_id'],)).fetchone()
        value['original_budget_attempts'] = deepcopy(value['attempts'])
        value['technical_media_budget'] = {'maximum': 1, 'attempts': int(bool(attempt)), 'outcome': 'ACCEPTED' if outcome else 'UNKNOWN'}
        if attempt:
            value['attempts']['total'] += 1
            value['reservations'].append({'sequence': 'ozon-media:' + self.row['grant_id'], 'scope': 'technical_media',
                'target_label': TARGET, 'mutation_target_label': TARGET, 'operation': 'repair_existing_media',
                'attempt_count': 1, 'outcome': 'ACCEPTED' if outcome else 'UNKNOWN'})
        return value
    @property
    def total_attempt_count(self):
        return self.snapshot()['attempts']['total']

SOURCE_COMPATIBILITY = ROOT / 'ROOT_OZON_SOURCE_COMPATIBILITY_NORMAL20.json'
SOURCE_ADOPTION_ROOT = ROOT / '20261021/ROOT_NORMAL20_SOURCE_ACCEPTANCE.json'
ABSOLUTE_SOURCE_COMPATIBILITY = ROOT / '20261021/ROOT_OZON_ABSOLUTE_STOCK_SOURCE_COMPATIBILITY.json'
ABSOLUTE_SOURCE_ADOPTION = ROOT / '20261021/ROOT_OZON_ABSOLUTE_STOCK_SOURCE_ACCEPTANCE.json'
ABSOLUTE_SOURCES = (MODULE, 'modules/ozon/approved_publication_v4.py', 'shared_platform/product_publication_live_dependencies.py')

def _exact_source_compatibility(ref,doc,pins,package,required):
    """Retain the original grant/proof; admit only Root's exact owned successor.

    This is source compatibility, not media/stock authority. All original
    delivery, verification, current-actor and remaining-budget checks still run.
    """
    successor = ABSOLUTE_SOURCE_COMPATIBILITY.is_file()
    selected_compatibility = ABSOLUTE_SOURCE_COMPATIBILITY if successor else SOURCE_COMPATIBILITY
    selected_adoption = ABSOLUTE_SOURCE_ADOPTION if successor else SOURCE_ADOPTION_ROOT
    compatibility=json.loads(selected_compatibility.read_bytes());_identity(compatibility)
    need(compatibility.get('schema')=='root-ozon-exact-source-compatibility/v1'
         and compatibility.get('status')=='LIVE_ADOPTION_APPROVED','EXACT_SOURCE_COMPATIBILITY_REQUIRED')
    if successor:
        need(compatibility.get('prior_source_compatibility_ref') == ABSOLUTE_PRIOR_COMPATIBILITY,
             'ABSOLUTE_PRIOR_SOURCE_COMPATIBILITY_REQUIRED')
        prior = _reference(ABSOLUTE_PRIOR_COMPATIBILITY)
        for key in ('original_live_root_ref','original_verification_root_ref','original_body_ref',
                    'accepted_response_ref','historical_source_root'):
            need(compatibility.get(key) == prior.get(key), 'ABSOLUTE_ORIGINAL_PROOF_HISTORY_CHANGED')
    live_ref=compatibility['original_live_root_ref'];proof_ref=compatibility['original_verification_root_ref']
    need(Path(live_ref['path']).resolve()==LIVE_ROOT.resolve()
         and Path(proof_ref['path']).resolve()==VERIFY_ROOT.resolve()
         and ref in (live_ref,proof_ref),'ORIGINAL_ROOT_COMPATIBILITY_CHANGED')
    live=_reference(live_ref);proof=_reference(proof_ref);_identity(live);_identity(proof)
    need(live.get('schema')=='root-ozon-existing-media-recovery/v1' and live.get('status')=='LIVE_ADOPTION_APPROVED'
         and proof.get('schema')=='root-ozon-media-content-verification/v1'
         and proof.get('status')=='LIVE_TECHNICAL_MEDIA_VERIFICATION'
         and live['source_pins_ref']==proof['source_pins_ref']
         and pins==_reference(live['source_pins_ref']) and proof.get('root_grant_ref')==live_ref,
         'ORIGINAL_ROOT_COMPATIBILITY_CHANGED')
    body=_reference(compatibility['original_body_ref']);response=_reference(compatibility['accepted_response_ref'])
    _identity(body)
    need(body.get('root_ref')==live_ref and _sha(_json(body))==proof.get('grant_id')
         and live.get('grant_core_digest')==_sha(_json({k:v for k,v in body.items() if k!='root_ref'}))
         and live.get('media_maximum')==1 and live.get('original_import_task_id')==TASK
         and proof.get('media_response_digest')==_sha(_json(response)),
         'ORIGINAL_GRANT_PROOF_COMPATIBILITY_CHANGED')
    validate_import(response,body['ordered_delivery_urls'])
    historical=Path(compatibility['historical_source_root'])
    need(historical.is_absolute() and historical.drive.upper()=='E:' and '..' not in historical.parts,
         'HISTORICAL_SOURCE_REQUIRED')
    for name in required:
        need(hashlib.sha256((historical/name).read_bytes()).hexdigest()==pins[name]['after_sha256'],
             'HISTORICAL_SOURCE_CHANGED')
    current=_reference(compatibility['current_exact_three_pins_ref'])
    need(set(current)==set(required),'EXACT_THREE_REVIEWED_SOURCES_REQUIRED')
    for name in required:
        need(hashlib.sha256((package/name).read_bytes()).hexdigest()==current[name]['after_sha256'],
             'COMPATIBILITY_CURRENT_SOURCE_CHANGED')
    need(Path(compatibility['owned_source_adoption_ref']['path']).resolve()==selected_adoption.resolve(),
         'FIXED_OWNED_SOURCE_ADOPTION_REQUIRED')
    adoption=_reference(compatibility['owned_source_adoption_ref'])
    need(adoption.get('schema')==('root-ozon-absolute-stock-source-acceptance/v1' if successor else 'root-normal20-source-acceptance/v1')
         and adoption.get('status')=='ACCEPTED_FOR_EXACT_OWNED_ADOPTION'
         and adoption.get('base_HEAD')==('bf50e5b7975f7cbe3c0509922315f063a6ed3051' if successor else '85d7efe37793dfd81baee397a4a7128f7ca3e09e')
         and adoption.get('Ozon_current_exact_three_pins_ref')==compatibility['current_exact_three_pins_ref']
         and re.fullmatch('[0-9a-f]{64}',adoption.get('expected_runtime_manifest') or '') is not None,
         'OWNED_SOURCE_ADOPTION_BINDING_CHANGED')
    delivery=_reference(adoption['source_delivery_ref']);manifest=_reference(adoption['source_manifest_ref'])
    need(delivery.get('source_manifest_ref')==adoption['source_manifest_ref']
         and delivery.get('ozon_exact_three_source_pins_ref')==compatibility['current_exact_three_pins_ref']
         and delivery.get('expected_runtime_manifest')==adoption['expected_runtime_manifest']
         and all(manifest['files'][n]==current[n]['after_sha256'] for n in required),
         'OWNED_SOURCE_DELIVERY_CHANGED')
    if successor:
        absolute = _reference(adoption['absolute_stock_source_pins_ref'])
        need(set(absolute) == set(ABSOLUTE_SOURCES), 'ABSOLUTE_EXACT_STOCK_SOURCES_REQUIRED')
        for name in ABSOLUTE_SOURCES:
            need(hashlib.sha256((package/name).read_bytes()).hexdigest() == absolute[name]['after_sha256']
                 == manifest['files'][name], 'ABSOLUTE_STOCK_CURRENT_SOURCE_CHANGED')

def has_verified_stock_work(store,decision_id):
    """Read-only FIFO hint; never constructs ACTIVE, starts a target or writes."""
    if decision_id!=DECISION:return False
    with store._connect_readonly() as db:
        row=_grant(db)
        if row is None:return False
        target=db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?',(RUN,TARGET)).fetchone()
        if target is None or target['status']!='FAILED' or target['attempts'] not in (1,2,3):return False
        if target['attempts']==3 and not ABSOLUTE_STOCK_ROOT.is_file():return False
        if target['attempts']==1 and db.execute('SELECT 1 FROM native_ozon_media_starts WHERE grant_id=?',(row['grant_id'],)).fetchone():return False
        body=json.loads(row['body_json']);delivery(body);original(db,body,status='FAILED',attempt=target['attempts'])
        return verified(db,row)

STOCK_ZERO_ROOT = ROOT / 'ROOT_OZON_STOCK_KNOWNZERO_ATTEMPT3.json'
STOCK_ZERO_FACTS = {
    'path': str(ROOT / '20261021/normal19-final-idle-readonly/CURRENT_RUN_DECISION_APPROVAL_AND_TARGETS.private.json'),
    'sha256': '6cd0a295f0f0a043e06c11b36720ae6633b3111cbd61a81127d47508db1f507c',
}
STOCK_ZERO_INITIAL = {
    'path': str(ROOT / '20261021/ozon-existing-media-actual/ORIGINAL_OZON_FAILURE_READONLY.private.json'),
    'sha256': '704a2b4d5c339001dc7ad2e4a167dcc23e1396089cae045bc8d3dc09d7c2b8ef',
}

def _stock_zero_original(db, body, row, failures, *, status, attempt, budget_rows):
    """Exact stock-prewire failure, not zero previous product side effects."""
    if len(failures) == 3:
        return _absolute_stock_original(db, body, row, failures, status=status, attempt=attempt, budget_rows=budget_rows)
    root=json.loads(STOCK_ZERO_ROOT.read_bytes());_identity(root)
    need(root.get('schema')=='root-ozon-stock-knownzero-attempt3/v1'
         and root.get('status')=='LIVE_ADOPTION_APPROVED'
         and root.get('grant_id')==_sha(_json(body))
         and root.get('from_target_attempt')==2 and root.get('next_target_attempt')==3
         and type(root.get('stock_maximum')) is int and root['stock_maximum']==1
         and root.get('facts_ref')==STOCK_ZERO_FACTS and root.get('initial_import_evidence_ref')==STOCK_ZERO_INITIAL,
         'EXACT_STOCK_ZERO_ROOT_REQUIRED')
    compatibility_ref=root['source_compatibility_ref']
    need(Path(compatibility_ref['path']).resolve()==SOURCE_COMPATIBILITY.resolve(),
         'STOCK_ZERO_CURRENT_SOURCE_COMPATIBILITY_REQUIRED')
    compatibility=_reference(compatibility_ref)
    need(compatibility.get('original_live_root_ref')==body['root_ref'],'STOCK_ZERO_ROOT_LINEAGE_CHANGED')
    _root(body['root_ref'])  # Original immutable Root/proof plus exact adopted source.
    facts=_reference(STOCK_ZERO_FACTS);initial=_reference(STOCK_ZERO_INITIAL)
    expected=[r for r in facts['targets'] if r['target_label']==TARGET]
    need(len(expected)==1 and expected[0]['status']=='FAILED' and expected[0]['attempts']==2
         and expected[0]['external_id'] is None,'STOCK_ZERO_FAILED2_FACT_REQUIRED')
    need((status,attempt) in {('FAILED',2),('PENDING',2),('RUNNING',3)}
         and row['idempotency_key']==expected[0]['idempotency_key'] and row['external_id'] is None
         and row['created_at']==expected[0]['created_at'],'STOCK_ZERO_EXACT_CURRENT_TARGET_REQUIRED')
    if status=='FAILED':need(dict(row)==expected[0],'STOCK_ZERO_FAILED2_ROW_CHANGED')
    observed=facts['target_evidence'][TARGET]
    need([dict(r) for r in failures]==observed['release_target_failure_events']
         and [r['attempt'] for r in failures]==[1,2]
         and all(_sha(r['evidence_json'])==r['evidence_digest'] for r in failures)
         and failures[0]['evidence_digest']==body['failure_evidence_digest']
         and failures[1]['evidence_digest']=='31862a1504bb56a1226ac09dcd83896a453c7938c1e6f32a8cd63e0de4cb65e5',
         'STOCK_ZERO_TWO_FAILURES_CHANGED')
    second=json.loads(failures[1]['evidence_json'])
    need(second.get('target_label')==TARGET and second.get('request_attempted') is False
         and second.get('outcome_unknown') is False and second.get('known_pre_submit_zero') is False
         and second.get('status')=='PROCESSING','STOCK_ZERO_NOT_GLOBAL_ZERO_REQUIRED')
    for suffix,expected_rows in facts['media'].items():
        need([dict(r) for r in db.execute('SELECT * FROM native_ozon_media_'+suffix)]==expected_rows,
             'STOCK_ZERO_MEDIA_HISTORY_CHANGED')
    need(facts['media']['starts']==[{'grant_id':_sha(_json(body)),'target_attempt':2}],
         'STOCK_ZERO_ORIGINAL_START2_REQUIRED')
    need(budget_rows[:1]==body['original_reservations'] and len(budget_rows) in ({1,2} if status=='RUNNING' else {1}),
         'STOCK_ZERO_STOCK_ALREADY_RESERVED_NO_REPLAY')
    # Original official import identity was persisted on attempt1. The later
    # failure retained no external_id; use its real pinned historical row,
    # never synthesize a provider identity onto the current row.
    past=initial['target']
    need(len(past)==1 and past[0]['target_label']==TARGET and past[0]['attempts']==1
         and past[0]['status']=='FAILED' and initial['failures']==[dict(failures[0])],
         'STOCK_ZERO_HISTORICAL_IMPORT_TARGET_CHANGED')
    return past[0]

_activate_before_stock_zero=activate_failed_targets
_actions_before_stock_zero=recovery_actions

def activate_failed_targets(active):
    prior=_activate_before_stock_zero(active)
    if getattr(active,'plan_id',None)!=PLAN or getattr(active,'decision_id',None)!=DECISION:return prior
    protected(active)
    with active.store._connect_readonly() as db:
        target=db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?',(RUN,TARGET)).fetchone()
        if target is None or (target['status'],target['attempts'])!=('FAILED',2):return prior
        row=_grant(db);need(row is not None,'EXISTING_GRANT_REQUIRED')
        recheck(active,db,row,status='FAILED',attempt=2);need(verified(db,row),'FULL_VERIFICATION_REQUIRED')
    with active.store._transaction() as db:
        recheck(active,db,row,status='FAILED',attempt=2);need(verified(db,row),'FULL_VERIFICATION_REQUIRED')
        changed=db.execute("UPDATE release_target_runs SET status='PENDING',completed_at=NULL WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=2 AND external_id IS NULL",(RUN,TARGET))
        need(changed.rowcount==1,'STOCK_ZERO_FAILED2_CAS_CHANGED')
    return (*prior,TARGET)

def recovery_actions(active,run,actions):
    prior=_actions_before_stock_zero(active,run,actions)
    if getattr(active,'plan_id',None)!=PLAN or getattr(active,'decision_id',None)!=DECISION:return prior
    target=next((r for r in run['targets'] if r['target_label']==TARGET),{})
    if (target.get('status'),target.get('attempts'))!=('PENDING',2):return prior
    protected(active)
    with active.store._connect_readonly() as db:
        row=_grant(db);need(row is not None,'EXISTING_GRANT_REQUIRED')
        recheck(active,db,row,status='PENDING',attempt=2);need(verified(db,row),'FULL_VERIFICATION_REQUIRED')
    return [{**a,'runnable':True,'action_kind':'NATIVE_OZON_EXACT_STOCK_ZERO_ATTEMPT3',
             'reason_code':'exact_previous_stock_prewire_zero_original_one_stock_slot'} if a.get('target_label')==TARGET else a for a in prior]


# This successor does not edit Root20, start2, the media grant/proof or failures.
ABSOLUTE_STOCK_ROOT = ROOT / '20261021/ROOT_OZON_ABSOLUTE_STOCK_ATTEMPT4.json'
ABSOLUTE_PRIOR_COMPATIBILITY = {
    'path': str(ROOT / 'ROOT_OZON_SOURCE_COMPATIBILITY_NORMAL20.json'),
    'sha256': '9ce377c21e04f3d6e63106b5459b18fcf6c82fe4be59c0b5a77798d9d081350b',
}
ABSOLUTE_FAILED3_FACTS = {
    'path': str(ROOT / '20261021/normal21-actual/PROGRESS_005.private.json'),
    'sha256': '793d18b78ee9d447ff995942d6ac5f80e2aa49b28ec29ca62bbb95b33f79b614',
}
ABSOLUTE_EMPTY_RESPONSE = {
    'path': str(ROOT / '20261021/ozon-stock-reader-two-actual-diagnosis/READ_02_RESPONSE.private.json'),
    'sha256': '990ee0b09b5df0cea92383aab0f6557dd88eeb7d0b845ce83f566a2b3733e9de',
}
ABSOLUTE_PRODUCT_INFO = {
    'path': str(ROOT / '20261021/ozon-current-normalized-three-readbacks/actual-once/READ_01_RESPONSE.private.json'),
    'sha256': '5dd3f298d99bed9030299abc6b6010978ceb7f0cc1d6be8de8f16e84527aeab7',
}
ABSOLUTE_WAREHOUSE_INFO = {
    'path': str(ROOT / '20261021/ozon-stock-reader-two-actual-diagnosis/READ_01_RESPONSE.private.json'),
    'sha256': '9186a9942aa6d18d061a060c4ba266f4c151d2d80c51f9300c0c17e3eac9d63e',
}


def _absolute_stock_root(body):
    root = json.loads(ABSOLUTE_STOCK_ROOT.read_bytes()); _identity(root)
    need(root.get('schema') == 'root-ozon-absolute-stock-knownzero-attempt4/v1'
         and root.get('status') == 'LIVE_ADOPTION_APPROVED'
         and root.get('grant_id') == _sha(_json(body))
         and root.get('from_target_attempt') == 3 and root.get('next_target_attempt') == 4
         and type(root.get('stock_maximum')) is int and root['stock_maximum'] == 1
         and type(root.get('absolute_stock')) is int and root['absolute_stock'] == 200
         and root.get('facts_ref') == ABSOLUTE_FAILED3_FACTS
         and root.get('baseline_unavailable_raw_ref') == ABSOLUTE_EMPTY_RESPONSE
         and root.get('existing_product_info_ref') == ABSOLUTE_PRODUCT_INFO
         and root.get('eligible_warehouse_ref') == ABSOLUTE_WAREHOUSE_INFO,
         'ABSOLUTE_STOCK_EXACT_ROOT_REQUIRED')
    need(Path(root['source_compatibility_ref']['path']).resolve() == ABSOLUTE_SOURCE_COMPATIBILITY.resolve(),
         'ABSOLUTE_STOCK_SOURCE_COMPATIBILITY_REQUIRED')
    compatibility = _reference(root['source_compatibility_ref'])
    need(compatibility.get('prior_source_compatibility_ref') == ABSOLUTE_PRIOR_COMPATIBILITY
         and compatibility.get('original_live_root_ref') == body['root_ref'],
         'ABSOLUTE_STOCK_ORIGINAL_ROOT_CHANGED')
    _root(body['root_ref'])  # Current full source compatibility, old grant untouched.
    empty = _reference(ABSOLUTE_EMPTY_RESPONSE)
    need(type(empty) is dict and empty == {'products': [], 'has_next': False, 'cursor': ''}
         and type(empty['has_next']) is bool, 'ABSOLUTE_BASELINE_UNAVAILABLE_NOT_ZERO_REQUIRED')
    info = _reference(ABSOLUTE_PRODUCT_INFO)
    rows = info.get('items'); need(type(rows) is list and len(rows) == 1, 'ABSOLUTE_EXACT_PRODUCT_REQUIRED')
    item = rows[0]; statuses = item.get('statuses', {})
    need(item.get('id') == PRODUCT and item.get('offer_id') == OFFER and item.get('sku') == 6076615549
         and statuses.get('is_created') is True and statuses.get('status') == 'price_sent'
         and statuses.get('status_failed') == '' and statuses.get('moderate_status') == 'approved'
         and statuses.get('validation_status') == 'success', 'ABSOLUTE_EXISTING_PRICE_SENT_PRODUCT_REQUIRED')
    warehouses = _reference(ABSOLUTE_WAREHOUSE_INFO).get('warehouses')
    need(type(warehouses) is list and all(type(r) is dict for r in warehouses), 'ABSOLUTE_WAREHOUSE_FACTS_REQUIRED')
    eligible = [r for r in warehouses if str(r.get('status') or '').strip().casefold() in {'active','created'}
                and r.get('is_kgt') is False]
    need(len(eligible) == 1 and type(root.get('warehouse_id')) is int
         and root['warehouse_id'] == eligible[0].get('warehouse_id') == 1020005018928780,
         'ABSOLUTE_EXACT_WAREHOUSE_REQUIRED')
    return root


def _absolute_stock_original(db, body, row, failures, *, status, attempt, budget_rows):
    _absolute_stock_root(body)
    facts = _reference(ABSOLUTE_FAILED3_FACTS)
    expected = [r for r in facts['targets'] if r['target_label'] == TARGET]
    need(len(expected) == 1 and expected[0]['status'] == 'FAILED' and expected[0]['attempts'] == 3
         and expected[0]['external_id'] is None, 'ABSOLUTE_EXACT_FAILED3_FACTS_REQUIRED')
    need((status,attempt) in {('FAILED',3),('PENDING',3),('RUNNING',4)}
         and row['idempotency_key'] == expected[0]['idempotency_key'] and row['external_id'] is None
         and row['created_at'] == expected[0]['created_at'], 'ABSOLUTE_FAILED3_STATE_CHANGED')
    if status == 'FAILED': need(dict(row) == expected[0], 'ABSOLUTE_FAILED3_BYTES_CHANGED')
    actual = facts['target_evidence'][TARGET]['release_target_failure_events']
    need([dict(r) for r in failures] == actual and [r['attempt'] for r in failures] == [1,2,3]
         and all(_sha(r['evidence_json']) == r['evidence_digest'] for r in failures), 'ABSOLUTE_THREE_FAILURES_CHANGED')
    prefix = _reference(STOCK_ZERO_FACTS)['target_evidence'][TARGET]['release_target_failure_events']
    need(actual[:2] == prefix and failures[0]['evidence_digest'] == body['failure_evidence_digest']
         and failures[2]['evidence_digest'] == '0628d604ea10e8ff2aa24ffbb068d4a42bea64f017868a11b7af9cbf7eb98624',
         'ABSOLUTE_FAILURE_PREFIX_CHANGED')
    third = json.loads(failures[2]['evidence_json'])
    result = third.get('canonical_platform_result', {}); targets = result.get('targets')
    need(type(targets) is list and len(targets) == 1 and targets[0].get('target_label') == TARGET,
         'ABSOLUTE_FAILED3_SCOPE_CHANGED')
    evidence = targets[0].get('evidence', {})
    need(third.get('request_attempted') is False and third.get('outcome_unknown') is False
         and third.get('known_pre_submit_zero') is False and third.get('external_writes_performed') == []
         and result.get('dispatch_attempted') is False and type(result.get('external_write_count')) is int
         and result['external_write_count'] == 0 and evidence.get('stage') == 'STOCK_READBACK'
         and evidence.get('provider_code') == 'ozon_stock_reconciliation_required'
         and evidence.get('request_attempted') is False and evidence.get('outcome_unknown') is False,
         'ABSOLUTE_STOCK_PREWIRE_ZERO_REQUIRED')
    for suffix, rows in facts['media'].items():
        need([dict(r) for r in db.execute('SELECT * FROM native_ozon_media_' + suffix)] == rows,
             'ABSOLUTE_MEDIA_HISTORY_CHANGED')
    need(facts['media']['starts'] == [{'grant_id':_sha(_json(body)), 'target_attempt':2}],
         'ABSOLUTE_ORIGINAL_START2_REQUIRED')
    need(budget_rows[:1] == body['original_reservations']
         and len(budget_rows) in ({1,2} if status == 'RUNNING' else {1}),
         'ABSOLUTE_STOCK_SLOT_ALREADY_EXISTS_NO_REPLAY')
    initial = _reference(STOCK_ZERO_INITIAL)
    need(len(initial['target']) == 1 and initial['target'][0]['attempts'] == 1
         and initial['target'][0]['target_label'] == TARGET and initial['failures'] == [dict(failures[0])],
         'ABSOLUTE_REAL_INITIAL_IMPORT_REQUIRED')
    return initial['target'][0]


_activate_before_absolute_stock = activate_failed_targets
_actions_before_absolute_stock = recovery_actions


def activate_failed_targets(active):
    prior = _activate_before_absolute_stock(active)
    if getattr(active,'plan_id',None) != PLAN or getattr(active,'decision_id',None) != DECISION: return prior
    protected(active)
    with active.store._connect_readonly() as db:
        target = db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN,TARGET)).fetchone()
        if target is None or (target['status'],target['attempts']) != ('FAILED',3) or not ABSOLUTE_STOCK_ROOT.is_file(): return prior
        row = _grant(db); need(row is not None, 'EXISTING_GRANT_REQUIRED')
        recheck(active,db,row,status='FAILED',attempt=3); need(verified(db,row), 'FULL_VERIFICATION_REQUIRED')
    with active.store._transaction() as db:
        recheck(active,db,row,status='FAILED',attempt=3); need(verified(db,row), 'FULL_VERIFICATION_REQUIRED')
        changed = db.execute("UPDATE release_target_runs SET status='PENDING',completed_at=NULL WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=3 AND external_id IS NULL", (RUN,TARGET))
        need(changed.rowcount == 1, 'ABSOLUTE_FAILED3_CAS_CHANGED')
    return (*prior,TARGET)


def recovery_actions(active,run,actions):
    prior = _actions_before_absolute_stock(active,run,actions)
    if getattr(active,'plan_id',None) != PLAN or getattr(active,'decision_id',None) != DECISION: return prior
    target = next((r for r in run['targets'] if r['target_label'] == TARGET), {})
    if (target.get('status'),target.get('attempts')) != ('PENDING',3): return prior
    protected(active)
    with active.store._connect_readonly() as db:
        row = _grant(db); need(row is not None, 'EXISTING_GRANT_REQUIRED')
        recheck(active,db,row,status='PENDING',attempt=3); need(verified(db,row), 'FULL_VERIFICATION_REQUIRED')
    return [{**a,'runnable':True,'action_kind':'NATIVE_OZON_ABSOLUTE200_KNOWNZERO_ATTEMPT4',
             'reason_code':'exact_three_stock_prewire_failures_original_one_absolute200_slot'}
            if a.get('target_label') == TARGET else a for a in prior]


def _registered_content_for_readback(db, row, frozen):
    """Validate registered historical content; never authorize a merchant write.

    The consumer owns native current-source/actor/decision/CAS gates. Historical
    objects must retain their actual accepted source and all original hashes;
    they are not new write grants for this current reader implementation.
    """
    check_schema(db)
    need(row is not None and row['run_id'] == RUN, 'READ_REGISTERED_GRANT_REQUIRED')
    body = json.loads(row['body_json']); _identity(body)
    need(_sha(row['body_json']) == row['body_sha256'] == row['grant_id']
         and row['body_json'] == _json(body), 'READ_REGISTERED_BODY_CHANGED')
    registered = db.execute('SELECT proof_ref_json FROM native_ozon_media_verifications WHERE grant_id=?',
                            (row['grant_id'],)).fetchone()
    outcome = db.execute('SELECT * FROM native_ozon_media_outcomes WHERE grant_id=?',
                         (row['grant_id'],)).fetchone()
    need(registered is not None and outcome is not None
         and _sha(outcome['response_json']) == outcome['response_sha256'], 'READ_REGISTERED_ACCEPTED_PROOF_REQUIRED')
    proof_ref = json.loads(registered['proof_ref_json'])
    # Existing pinned prior is an immutable historical locator, not a new
    # current-source compatibility or spent stock admission. Never scan latest.
    prior = _reference(ABSOLUTE_PRIOR_COMPATIBILITY); _identity(prior)
    need(prior.get('schema') == 'root-ozon-exact-source-compatibility/v1'
         and prior.get('status') == 'LIVE_ADOPTION_APPROVED', 'READ_HISTORICAL_COMPATIBILITY_REQUIRED')
    live_ref = prior['original_live_root_ref']; retained_proof_ref = prior['original_verification_root_ref']
    need(type(live_ref) is dict and set(live_ref) == {'path','sha256'}
         and type(retained_proof_ref) is dict and set(retained_proof_ref) == {'path','sha256'}
         and Path(live_ref['path']).resolve() == LIVE_ROOT.resolve()
         and Path(retained_proof_ref['path']).resolve() == VERIFY_ROOT.resolve()
         and body['root_ref'] == live_ref and proof_ref == retained_proof_ref,
         'READ_FIXED_REGISTERED_ROOTS_CHANGED')
    live = _reference(live_ref); proof = _reference(proof_ref)
    _identity(live); _identity(proof)
    need(live.get('schema') == 'root-ozon-existing-media-recovery/v1'
         and live.get('status') == 'LIVE_ADOPTION_APPROVED'
         and proof.get('schema') == 'root-ozon-media-content-verification/v1'
         and proof.get('status') == 'LIVE_TECHNICAL_MEDIA_VERIFICATION'
         and proof.get('root_grant_ref') == live_ref
         and live.get('source_pins_ref') == proof.get('source_pins_ref'), 'READ_ROOT_HISTORY_CHANGED')
    history_pins = _reference(live['source_pins_ref'])
    required = (MODULE, 'shared_platform/native_prepared_v4_targets.py', 'modules/products/server.py')
    need(type(history_pins) is dict and set(history_pins) == set(required), 'READ_EXACT_HISTORICAL_SOURCES_REQUIRED')
    history = Path(prior['historical_source_root'])
    need(history.is_absolute() and history.drive.upper() == 'E:' and '..' not in history.parts,
         'READ_HISTORICAL_SOURCE_ROOT_REQUIRED')
    for name in required:
        need(hashlib.sha256((history / name).read_bytes()).hexdigest() == history_pins[name]['after_sha256'],
             'READ_HISTORICAL_SOURCE_CHANGED')
    response = json.loads(outcome['response_json'])
    need(_reference(prior['original_body_ref']) == body
         and _reference(prior['accepted_response_ref']) == response
         and _sha(_json(response)) == outcome['response_sha256']
         and _reference(body['approved_snapshot_ref']) == frozen
         and frozen.get('snapshot_digest') == SNAPSHOT, 'READ_REGISTERED_FROZEN_RESPONSE_CHANGED')
    source_images, delivered_images = _delivery_objects(body, live)
    validate_import(response, delivered_images)
    _verification_objects(body, response, proof, source_images)
    # No transport, ACTIVE, DDL, recovery eligibility or budget reservation can
    # be returned from this pure historical-content leaf.
    return {'registered_proof_ref': proof_ref, 'historical_source_pins_ref': live['source_pins_ref'],
            'ordered_source_images': source_images, 'ordered_delivery_images': delivered_images,
            'ordered_ozon_images': image_urls(_reference(proof['pictures_info_ref']))}

