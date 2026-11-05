"""Explicit SKU-only commercial generation, retaining the known COMMON result.

This local producer has no transport, image executor or marketplace approval.
The ordinary COMMON EDIT boundary and final-review service remain the writers.
"""
from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

SCHEMA = 'sku-only-publication-correction/v1'
POINTER = 'sku-only-generation.json'
EVENT = 'sku_only_generation_admitted'


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _digest(value):
    return 'sha256:' + sha256(_bytes(value)).hexdigest()


def _fail(reason):
    raise ValueError('SKU_CORRECTION_' + reason)


def replace_sku_identity(value, old_sku, new_sku):
    """Only identity fields change; copy, variant keys and evidence stay literal."""
    if isinstance(value, list):
        return [replace_sku_identity(item, old_sku, new_sku) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    result = {}
    for key, item in value.items():
        if key in {'category_evidence_binding', 'input_lineage_manifest'}:
            result[key] = deepcopy(item)  # Historical evidence is never rewritten.
        elif key in {'seller_sku', 'model_sku', 'internal_sku'} and item == old_sku:
            result[key] = new_sku
        else:
            result[key] = replace_sku_identity(item, old_sku, new_sku)
    return result


def report_directory(reports_root, offer_id):
    """Resolve a local report projection, never authority from a path alone."""
    base = Path(reports_root) / offer_id
    pointer = base / POINTER
    if not pointer.exists():
        return base
    from shared_platform.immutable_approval_files import require_local_path
    require_local_path(pointer, root=reports_root)
    record = json.loads(pointer.read_bytes())
    unsigned = dict(record)
    supplied = unsigned.pop('correction_digest', None)
    generation = record.get('generation_id')
    if (record.get('schema_version') != SCHEMA or record.get('offer_id') != offer_id
            or supplied != _digest(unsigned) or not isinstance(generation, str)
            or not re.fullmatch(r'[0-9a-f]{32}', generation)):
        _fail('REPORT_POINTER_INVALID')
    directory = base / 'sku-corrections' / generation
    require_local_path(directory, root=reports_root, allow_directory=True)
    return directory


def _read_record(reports_root, offer_id):
    directory = report_directory(reports_root, offer_id)
    base = Path(reports_root) / offer_id
    return None if directory == base else json.loads((base / POINTER).read_bytes())


def _known_common(store, db, *, offer_id, plan_id, run_id, initial=False):
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    from shared_platform.native_common_retained_completion import read_retained_completion
    completion = read_retained_completion(NativeCommonSourceReader(store), db, plan_id, run_id)
    if completion.state != 'CONFIRMED_WRITE':
        _fail('KNOWN_COMMON_WRITE_REQUIRED')
    plan = db.execute('SELECT * FROM release_plans WHERE plan_id=?', (plan_id,)).fetchone()
    if plan is None or plan['product_id'] != offer_id:
        _fail('COMMON_OFFER_CHANGED')
    from shared_platform.manual_common_creation import common_detail_id
    payload = json.loads(plan['payload_json'])
    # Every pre-existing market attempt/decision and every unresolved operation
    # is a refusal. A new generation cannot reset their authority or result.
    for run in db.execute('SELECT r.*,p.payload_json FROM release_runs r JOIN release_plans p USING(plan_id) WHERE p.product_id=?', (offer_id,)) if initial else ():
        if run['run_id'] == run_id:
            continue
        if json.loads(run['payload_json']).get('targets') != ['miaoshou:COMMON']:
            _fail('MARKETPLACE_ATTEMPT_EXISTS')
        if run['technical_execution_state'] not in {'CONFIRMED_WRITE', 'READONLY_REUSE', 'PROVEN_NOT_DISPATCHED'}:
            _fail('PRIOR_OUTCOME_REQUIRES_RECONCILIATION')
    if initial and db.execute('SELECT 1 FROM release_approvals a JOIN release_plans p USING(plan_id) WHERE p.product_id=?', (offer_id,)).fetchone():
        _fail('PRIOR_MARKETPLACE_APPROVAL_EXISTS')
    if initial and db.execute('SELECT 1 FROM release_final_review_decisions WHERE offer_id=?', (offer_id,)).fetchone():
        _fail('PRIOR_FINAL_DECISION_EXISTS')
    if initial:
        for old_plan in db.execute('SELECT target_labels_json,status FROM release_plans WHERE product_id=?', (offer_id,)):
            if json.loads(old_plan['target_labels_json']) != ['miaoshou:COMMON'] and old_plan['status'] != 'SUPERSEDED':
                _fail('OLD_MARKETPLACE_CANDIDATE_NOT_SUPERSEDED')
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_sole_final_decisions'").fetchone():
            if db.execute('SELECT 1 FROM native_sole_final_decisions d JOIN release_plans p USING(plan_id) WHERE p.product_id=?', (offer_id,)).fetchone():
                _fail('PRIOR_FINAL_DECISION_EXISTS')
    readback = db.execute("SELECT * FROM release_target_readbacks WHERE run_id=? AND target_label='miaoshou:COMMON'", (run_id,)).fetchone()
    return {'plan_id': plan_id, 'run_id': run_id, 'payload_digest': plan['payload_digest'],
            'detail_id': common_detail_id(payload, connection=db),
            'readback_digest': readback['evidence_digest']}


def verify_generation(store, *, reports_root, offer_id, task=None, tasks_path=None, connection=None):
    """Verify the append-only grant and the immutable original sources."""
    record = _read_record(reports_root, offer_id)
    if record is None:
        return None
    database = Path(record['tasks_path'])
    if Path(record['release_store_path']).resolve() != store.path.resolve():
        _fail('RELEASE_STORE_CHANGED')
    if tasks_path is not None and database.resolve() != Path(tasks_path).resolve():
        _fail('TASK_STORE_CHANGED')
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=?', (record['task_id'], EVENT)).fetchall()
        old = db.execute('SELECT state,worker,lease_token,external_started FROM workbench_execution WHERE task_id=?', (record['predecessor_task_id'],)).fetchone()
        current = db.execute('SELECT e.*,t.source_key FROM workbench_execution e JOIN workbench_tasks t USING(task_id) WHERE task_id=?', (record['task_id'],)).fetchone()
        if (len(rows) != 1 or json.loads(rows[0]['detail_json']) != record
                or old is None or old['state'] != 'cancelled' or old['worker'] or old['lease_token'] or old['external_started']
                or current is None or json.loads(current['scope_json']) != record['scope']
                or current['source_key'] != record['source_key']
                or (task is not None and (task['task_id'] != record['task_id'] or task['scope'] != record['scope']
                                         or task['version'] != json.loads(current['version_json'])))):
            _fail('TASK_LINEAGE_CHANGED')
        current_release = json.loads(current['version_json'])
        if current_release != record['release']:
            from shared_platform.native_unfrozen_source_successor import verified_explicit_origin
            from shared_platform.native_frozen_source_successor import verify_native_owner, verify_native_files
            original = verified_explicit_origin(db, current, current_release)
            seal = verify_native_owner(db, current, current_release)
            if original['release'] != record['release'] or seal is None or seal.get('sku_only_correction') != record:
                _fail('CODE_ONLY_SOURCE_LINEAGE_REQUIRED')
            verify_native_files(seal, media=False)
    from shared_platform.immutable_approval_files import require_local_path
    for supplied, expected in record['original_files'].items():
        path = Path(supplied)
        require_local_path(path, root=path.parent)
        if sha256(path.read_bytes()).hexdigest() != expected:
            _fail('ORIGINAL_SOURCE_CHANGED')
    # An existing consumer snapshot must retain its own transaction. Opening
    # another reader while a writer waits to commit can deadlock that snapshot.
    # _known_common validates the supplied connection's store and transaction.
    with (store._connect_readonly() if connection is None else nullcontext(connection)) as db:
        if connection is None:
            db.execute('BEGIN')
        baseline = _known_common(store, db, offer_id=offer_id,
            plan_id=record['common']['plan_id'], run_id=record['common']['run_id'])
        if baseline != record['common']:
            _fail('COMMON_BASELINE_CHANGED')
    return record


def admit_generation(engine, profile, store, *, predecessor_task_id, source_key,
                     new_sku, common_plan_id, common_run_id, authority_path,
                     authority_sha256, documents, original_paths, catalog_path, paid_context):
    """Create one genuine semantic Task and new report namespace, with no writes
    outside local TaskDB/files. Caller must subsequently use apply_preparation.
    """
    from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS, validate_r2_identity
    from shared_platform.immutable_approval_files import persist_immutable_bytes, require_local_path
    from shared_platform import round1_workspace
    from modules.sourcing import new_product_workbench as workbench
    release = {'code_version': profile.version, 'environment': profile.environment,
               'manifest_digest': profile.manifest_digest}
    if (engine.release != release or Path(engine.store.path).resolve() != (Path(profile.data_root) / 'tasks.db').resolve()
            or Path(workbench.STATE_DIR).resolve() != (Path(profile.root) / 'data/new_product_workbench').resolve()):
        _fail('RUNTIME_SOURCE_CONFIG_DATA_CHANGED')
    old = engine.get(predecessor_task_id)
    offer = old['scope']['offer_id']
    root = Path(profile.root) / 'reports/product-preparation'
    if old['execution_state'] != 'cancelled' or old.get('worker'):
        _fail('PREDECESSOR_NOT_CANCELLED')
    if not re.fullmatch(r'[0-9]{4}', str(new_sku)) or new_sku == '0000':
        _fail('NEW_SKU_INVALID')
    authority = Path(authority_path)
    require_local_path(authority, root=authority.parent)
    if sha256(authority.read_bytes()).hexdigest() != authority_sha256:
        _fail('AUTHORITY_CHANGED')
    old_r1 = documents['round1_snapshot']
    old_sku = old_r1['fact_snapshot']['product_facts']['seller_sku']
    if old_sku == new_sku or old_r1['offer_id'] != offer:
        _fail('SEMANTIC_IDENTITY_REQUIRED')
    if sorted(old_r1['canonical_targets']) != old['scope']['shops'] or old['scope']['skus'] != [old_sku]:
        _fail('ORIGINAL_SCOPE_CHANGED')
    validate_r2_identity(documents)
    from shared_platform.publication_paid_requests import require_paid_context
    paid_context = require_paid_context(paid_context)
    if paid_context.offer_id != offer or paid_context.round1 != old_r1:
        _fail('ORIGINAL_PAID_OWNER_REQUIRED')
    paid_summary = paid_context.summary()
    if paid_summary['unknown'] or paid_summary['confirmed'] != paid_summary['occupied']:
        _fail('PAID_OUTCOME_REQUIRES_RECONCILIATION')
    paid_rows, paid_entries = paid_context._load()
    paid_ids = sorted([*paid_entries,
        *('history:' + row['request_record_digest'] for row in paid_context._historical(paid_rows))])
    if (len(paid_ids) != len(set(paid_ids)) or len(paid_ids) != paid_summary['confirmed']
            or not paid_rows or paid_rows[-1]['event_digest'] != paid_summary['ledger_digest']):
        _fail('ORIGINAL_PAID_HISTORY_CHANGED')
    from shared_platform.release_control import _known_seller_skus, _next_available_seller_skus
    occupied = _known_seller_skus(Path(catalog_path), strict=True)
    next_values = _next_available_seller_skus(occupied, store.active_reserved_sku_keys(),
        requested_count=1, additional_occupied_skus=occupied)
    if next_values != (new_sku,):
        _fail('CURRENT_FULL_CATALOG_SUCCESSOR_CHANGED')
    state = workbench.load_state(offer)
    if (state.get('review') or {}).get('seller_sku') != old_sku:
        _fail('ORIGINAL_STATE_IDENTITY_CHANGED')
    reference = (state.get('product_approval') or {}).get('round1_prepared_reference')
    from shared_platform.publication_rounds import validate_round2_input
    if validate_round2_input(offer, state) != old_r1:
        _fail('ORIGINAL_CURRENT_FREEZE_CHANGED')
    prepared = round1_workspace.read_preparation(offer, reference)
    if prepared['packet'] != documents['first_review']:
        _fail('ORIGINAL_PREPARATION_CHANGED')
    pins = {}
    for name in R2_DOCUMENTS:
        path = Path(original_paths[name])
        require_local_path(path, root=path.parent)
        if name != 'first_review' and json.loads(path.read_bytes()) != documents[name]:
            _fail('ORIGINAL_DOCUMENT_CHANGED')
        pins[str(path)] = sha256(path.read_bytes()).hexdigest()
    pins[str(authority)] = authority_sha256
    pins[str(paid_context.path)] = sha256(paid_context.path.read_bytes()).hexdigest()
    warehouse_path = root / offer / 'ozon-warehouse-readback.json'
    if 'ozon:RU' in old_r1['canonical_targets']:
        require_local_path(warehouse_path, root=root)
        if not warehouse_path.is_file():
            _fail('ORIGINAL_WAREHOUSE_RECEIPT_REQUIRED')
        pins[str(warehouse_path)] = sha256(warehouse_path.read_bytes()).hexdigest()
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        common = _known_common(store, db, offer_id=offer, plan_id=common_plan_id, run_id=common_run_id, initial=True)
    identity = {'offer_id': offer, 'predecessor_task_id': predecessor_task_id,
                'old_sku': old_sku, 'new_sku': new_sku, 'source_key': source_key,
                'old_snapshot_digest': old_r1['snapshot_digest'], 'common': common,
                'authority_sha256': authority_sha256}
    generation = sha256(_bytes(identity)).hexdigest()[:32]
    directory = root / offer / 'sku-corrections' / generation
    pointer = root / offer / POINTER
    if pointer.exists():
        record = verify_generation(store, reports_root=root, offer_id=offer)
        if record['generation_id'] != generation:
            _fail('GENERATION_ALREADY_EXISTS')
        return record
    if not source_key or source_key == old.get('source_key'):
        _fail('NEW_SOURCE_KEY_REQUIRED')
    task, created = engine.create_for_explicit_post({'template': 'publication',
        'source_key': source_key, 'title': 'SKU更正 ' + old_sku + ' → ' + new_sku,
        'scope': {**old['scope'], 'skus': [new_sku]}})
    if not created:
        _fail('NEW_TASK_KEY_ALREADY_USED')
    record = {**identity, 'schema_version': SCHEMA, 'generation_id': generation,
              'task_id': task['task_id'], 'scope': task['scope'],
              'release': engine.release,
              'tasks_path': str(engine.store.path), 'release_store_path': str(store.path),
              'catalog_path': str(Path(catalog_path).resolve()),
              'original_prepared_reference': reference, 'original_files': pins,
              'original_state_digest': _digest(state), 'paid_summary': paid_summary,
              'paid_request_ids': paid_ids,
              'paid_history_root': str(paid_context.directory.parents[2]),
              'original_warehouse_path': str(warehouse_path) if 'ozon:RU' in old_r1['canonical_targets'] else None,
              'preparation_root': round1_workspace.preparation_root_facts(prepared),
              'marketplace_approval': False, 'maximum_new_common_edits': 1,
              'created_at': datetime.now(timezone.utc).isoformat()}
    record['correction_digest'] = _digest(record)
    directory.mkdir(parents=True, exist_ok=False)
    with engine.transaction() as db:
        engine._event(db, task['task_id'], EVENT, record)
        engine._event(db, predecessor_task_id, 'sku_only_semantic_successor',
                      {'task_id': task['task_id'], 'correction_digest': record['correction_digest']})
    persist_immutable_bytes(pointer, _bytes(record), root=root)
    return record


def dashboard_generation(store, *, reports_root, offer_id, lineage_plan):
    """Do not let a retained old successful COMMON force the rejected SKU."""
    record = verify_generation(store, reports_root=reports_root, offer_id=offer_id)
    if record is None:
        return None, lineage_plan
    if lineage_plan is not None:
        sku = str(lineage_plan.get('seller_sku') or '')
        if sku == record['old_sku']:
            lineage_plan = None
        elif sku != record['new_sku']:
            _fail('UNBOUND_COMMERCIAL_GENERATION')
    return record, lineage_plan


def check_common_preimage(store, db, payload, current_response):
    """Bind one new EDIT to the known original draft and exact identity delta.

    The managed transport calls this before reserve/UNKNOWN. Existing
    UNKNOWN/completed operation handling remains the original native handling.
    """
    source = (payload.get('r3_stage_binding') or {}).get('native_preparation_source') or {}
    proof = (source.get('round1_snapshot') or {}).get('sku_only_correction')
    if proof is None:
        return
    from shared_platform.publication_rounds import REPORTS_ROOT
    record = verify_generation(store, reports_root=REPORTS_ROOT, offer_id=payload['product_id'])
    if record is None or proof.get('correction_digest') != record['correction_digest']:
        _fail('COMMON_GENERATION_REQUIRED')
    if (source.get('preparation_root') or {}).get('root_id') != 'r1-cycle:' + record['generation_id']:
        _fail('COMMON_NEW_ROOT_REQUIRED')
    original = json.loads(db.execute('SELECT payload_json FROM release_plans WHERE plan_id=?',
        (record['common']['plan_id'],)).fetchone()[0])
    from modules.products.release_adapters import _immutable_miaoshou_common_draft, readback_miaoshou_common
    old_draft = _immutable_miaoshou_common_draft(original)
    new_draft = _immutable_miaoshou_common_draft(payload)
    expected = deepcopy(old_draft)
    expected['itemNum'] = record['new_sku']
    # Per-variant Model SKU lives in the frozen payload's SKU facts, not in
    # this generic envelope; the existing serializer verifies those separately.
    if new_draft != expected or payload['seller_sku'] != record['new_sku']:
        _fail('COMMON_NON_IDENTITY_DELTA')
    evidence = readback_miaoshou_common(original, post=lambda path, body: deepcopy(current_response))
    if evidence.get('verified') is not True:
        _fail('COMMON_ORIGINAL_OFFICIAL_PREIMAGE_CHANGED')
    for run in db.execute('SELECT r.*,p.payload_json FROM release_runs r JOIN release_plans p USING(plan_id) WHERE p.product_id=?', (payload['product_id'],)):
        if run['run_id'] == record['common']['run_id']:
            continue
        if json.loads(run['payload_json']).get('targets') != ['miaoshou:COMMON']:
            _fail('MARKETPLACE_ATTEMPT_EXISTS')
        if run['plan_id'] != payload['plan_id']:
            _fail('UNBOUND_COMMON_CORRECTION_ATTEMPT')
        if run['technical_execution_state'] not in {'RESERVED', 'PROVEN_NOT_DISPATCHED'}:
            _fail('COMMON_CORRECTION_ALREADY_CONSUMED')


def apply_preparation(engine, profile, store, *, offer_id, documents, derived_documents):
    """Persist the derivative and complete the genuine new facts/images steps.

    ``derived_documents`` must be produced by sku_only_r2_derivation. All
    validation and immutable writes precede the sole Product Center state CAS.
    A crash leaves original evidence and the new intent visible; never rerun a
    provider as recovery. This function performs no provider request.
    """
    from shared_platform import publication_rounds as rounds, round1_workspace as workspace
    from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS, validate_r2_identity
    from shared_platform.immutable_approval_files import persist_immutable_bytes
    from modules.sourcing import new_product_workbench as workbench
    root = Path(profile.root) / 'reports/product-preparation'
    record = verify_generation(store, reports_root=root, offer_id=offer_id,
                               tasks_path=engine.store.path)
    if record is None:
        _fail('GENERATION_REQUIRED')
    task = engine.get(record['task_id'])
    if task['execution_state'] != 'queued' or task['current_step'] != 'facts':
        _fail('NEW_TASK_NOT_QUIESCENT')
    original = workspace.read_preparation(offer_id, record['original_prepared_reference'])
    validate_r2_identity(documents)
    if documents['first_review'] != original['packet'] or documents['round1_snapshot']['snapshot_digest'] != record['old_snapshot_digest']:
        _fail('ORIGINAL_PREPARATION_CHANGED')
    from shared_platform.release_control import _known_seller_skus, _next_available_seller_skus
    occupied = _known_seller_skus(Path(record['catalog_path']), strict=True)
    if _next_available_seller_skus(occupied, store.active_reserved_sku_keys(),
            requested_count=1, additional_occupied_skus=occupied) != (record['new_sku'],):
        _fail('CURRENT_FULL_CATALOG_SUCCESSOR_CHANGED')
    expected_first, expected_r1 = preparation_documents(record, documents)
    if derived_documents['first_review'] != expected_first or derived_documents['round1_snapshot'] != expected_r1:
        _fail('DERIVED_R1_CHANGED')
    identity = validate_r2_identity(derived_documents)
    receipt = (derived_documents['image_qa'].get('sku_only_derivation') or {})
    if ((receipt.get('correction_binding') or {}).get('correction_digest') != record['correction_digest']
            or receipt.get('original_documents') != documents):
        _fail('R2_CORRECTION_RECEIPT_REQUIRED')
    _check_paid_binding(record, receipt.get('paid_history_binding') or {})
    directory = report_directory(root, offer_id)
    before = workbench.load_state(offer_id)
    if (before.get('review') or {}).get('seller_sku') != record['old_sku']:
        _fail('CURRENT_STATE_CHANGED')
    if _digest(before) != record['original_state_digest']:
        _fail('CURRENT_STATE_CHANGED')
    worker = 'sku-only-preparation:' + record['generation_id']
    engine.register_executor(worker, ['publication'], engine.release, ttl=300)
    claim = engine.claim(task['task_id'], worker, ttl=300)
    if claim is None:
        _fail('NEW_TASK_CLAIM_REQUIRED')
    token = claim['lease_token']
    prepared = deepcopy(original)
    prepared.pop('reference')
    prepared.update(packet=expected_first, unbound_packet=replace_sku_identity(original['unbound_packet'], record['old_sku'], record['new_sku']),
        request_id='sku-correction-' + record['generation_id'],
        state_before=before, approval_fingerprint=expected_r1['product_approval_fingerprint'])
    prepared['unbound_packet'].pop('input_lineage_manifest', None)
    prepared['preparation_root'] = {'schema_version': workspace.PREPARATION_ROOT_SCHEMA,
        'status': 'PERSISTED_NATIVE_PREPARATION_ROOT', 'root_id': 'r1-cycle:' + record['generation_id'],
        'offer_id': offer_id, 'origin_request_id': prepared['request_id'], 'execution_authority': False}
    prepared['sku_only_correction'] = {'correction_digest': record['correction_digest'],
        'original_prepared_reference': record['original_prepared_reference']}
    prepared['reference'] = 'r1-prepared:' + _digest(prepared)[7:]
    approval = deepcopy(before['product_approval'])
    approval.update(status='approved', approval_id=expected_r1['product_approval_id'],
        seller_sku=record['new_sku'], input_fingerprint=prepared['approval_fingerprint'],
        approved_at=expected_r1['approved_at'], round1_prepared_reference=prepared['reference'],
        package_id='product:' + offer_id + ':' + record['new_sku'],
        round1_review_digest=_digest(expected_first), decision_digest=record['correction_digest'],
        human_approval=False, sku_only_correction_digest=record['correction_digest'])
    for name in ('superseded_at', 'superseded_by', 'superseded_revision', 'superseded_fields'):
        approval.pop(name, None)
    for key, name in R2_DOCUMENTS.items():
        persist_immutable_bytes(directory / name, _bytes(derived_documents[key]), root=root)
    if record['original_warehouse_path'] is not None:
        from shared_platform.ozon_stock_warehouse_decision import derive_sku_only_warehouse_receipt
        from shared_platform.ozon_runtime_credentials import required_pinned_ozon_credentials
        warehouse = derive_sku_only_warehouse_receipt(
            json.loads(Path(record['original_warehouse_path']).read_bytes()),
            old_round1=documents['round1_snapshot'], new_round1=expected_r1,
            pinned=required_pinned_ozon_credentials())
        persist_immutable_bytes(directory / 'ozon-warehouse-readback.json', _bytes(warehouse), root=root)
    # Preserve any original capture proof as historical evidence, never rewrite
    # it into a claim that these newly bound facts were captured again.
    persist_immutable_bytes(directory / 'sku-only-original-preparation.json', _bytes(original), root=root)
    with workspace._transaction() as db:
        db.execute('INSERT INTO round1_workspace_preparations VALUES (?,?,?,?,?,?)',
            (prepared['request_id'], offer_id, prepared['reference'],
             workspace._json({'sku_only_correction': record['correction_digest']}),
             workspace._json(prepared), _digest(prepared)))
    next_state = deepcopy(before)
    next_state['review'] = replace_sku_identity(before['review'], record['old_sku'], record['new_sku'])
    next_state['review'].update(seller_sku=record['new_sku'], fields_locked=True)
    next_state['product_approval'] = approval
    next_state['sku_only_correction_digest'] = record['correction_digest']
    # The existing local state CAS retains a commercial supersession record.
    next_state.setdefault('commercial_supersessions', []).append({
        'source': SCHEMA, 'status': 'superseded', 'reason': 'Explicit SKU-only identity correction',
        'changed_fields': ['seller_sku', 'model_sku'], 'prior_approval_id': before['product_approval']['approval_id'],
        'correction_digest': record['correction_digest']})
    saved = workbench.save_state(offer_id, next_state, mirror_content=False)
    if saved['_revision'] != expected_r1['approved_product_center_revision']:
        _fail('STATE_CAS_REVISION_CHANGED')
    frozen = {'offer_id': offer_id, 'targets': record['scope']['shops'],
        'snapshot_digest': expected_r1['snapshot_digest'], 'prepared_reference': prepared['reference'],
        'internal_sku': record['new_sku'], 'seller_sku': record['new_sku']}
    checkpoint = {'sku_only_correction': record['correction_digest'],
        'native_preparation': {'prepared_reference': prepared['reference']}, 'native_r1': frozen}
    engine.record_checkpoint(task['task_id'], token, checkpoint)
    engine.complete_step(task['task_id'], token, expected_step='facts', checkpoint=checkpoint)
    checkpoint['native_r2'] = identity
    engine.complete_step(task['task_id'], token, expected_step='images', checkpoint=checkpoint)
    # The ordinary COMMON plan producer owns the actual namespace claim. Keep
    # this Task's lease until its typed ACTIVE reservation is durable; a check
    # and a queued Task alone do not reserve a SKU.
    from shared_platform.workbench_publication_native import _server
    server = _server(profile)
    dashboard, failure = server._release_dashboard_for_request({
        'offer_id': offer_id, 'release_stage': 'R3_COMMON',
        'publication_targets': ['miaoshou:COMMON']})
    if failure is not None:
        _fail('COMMON_RESERVATION_PREVIEW_FAILED:' + str(failure[1].get('error') or failure[0]))
    payload, blockers = server._release_plan_payload_from_dashboard(dashboard, common_store=store)
    if blockers:
        _fail('COMMON_RESERVATION_BLOCKED:' + ';'.join(map(str, blockers)))
    source = (payload.get('r3_stage_binding') or {}).get('native_preparation_source') or {}
    if (payload.get('targets') != ['miaoshou:COMMON'] or payload.get('seller_sku') != record['new_sku']
            or source.get('prepared_reference') != prepared['reference']
            or source.get('round1_snapshot_digest') != expected_r1['snapshot_digest']):
        _fail('COMMON_RESERVATION_IDENTITY_CHANGED')
    with engine.transaction() as db:
        engine._lease(db, task['task_id'], token)
    plan = store.create_plan(payload)  # Existing atomic ACTIVE/unique checks.
    checkpoint['sku_only_common_reservation'] = {
        'plan_id': plan['plan_id'], 'payload_digest': plan['payload_digest'],
        'seller_sku': record['new_sku'], 'status': 'ACTIVE_PLAN_RESERVATION'}
    engine.record_checkpoint(task['task_id'], token, checkpoint)
    engine.yield_prepared_lease(task['task_id'], token)
    return {'task_id': task['task_id'], 'native_r1': frozen, 'native_r2': identity,
            'common_plan_id': plan['plan_id'], 'common_payload_digest': plan['payload_digest'],
            'correction_digest': record['correction_digest'], 'external_write_count': 0,
            'marketplace_approval': False}


def preparation_documents(record, documents, *, current_revision=None):
    """Build only the newly bound R1 inputs; the original documents stay intact."""
    from modules.sourcing import new_product_workbench as workbench
    before = workbench.load_state(record['offer_id']) if current_revision is None else {'_revision': current_revision}
    first = replace_sku_identity(documents['first_review'], record['old_sku'], record['new_sku'])
    first.pop('input_lineage_manifest', None)
    r1 = deepcopy(documents['round1_snapshot'])
    r1.pop('snapshot_digest')
    r1.pop('input_lineage_manifest', None)
    r1.update(first_review_digest=_digest(first), approved_at=record['created_at'],
        approved_product_center_revision=before['_revision'] + 1,
        product_approval_id='product-sku-correction:' + record['generation_id'],
        product_approval_fingerprint=_digest({'original': r1['product_approval_fingerprint'],
                                             'correction': record['correction_digest']}),
        decision_receipt_digest=record['correction_digest'], human_approval=False,
        sku_only_correction={'correction_digest': record['correction_digest'],
            'original_snapshot_digest': record['old_snapshot_digest'],
            'original_prepared_reference': record['original_prepared_reference']})
    r1['fact_snapshot'] = {key: deepcopy(first.get(key) or ({} if key in {
        'product_facts', 'shared_review_facts', 'publication_stock_policy', 'content_groups'} else []))
        for key in documents['round1_snapshot']['fact_snapshot']}
    r1['snapshot_digest'] = _digest(r1)
    return first, r1


def _check_paid_binding(record, paid):
    original = record['paid_summary']
    path = str(Path(paid.get('ledger_ref') or ''))
    expected_path = str(Path(original['ledger_path']))
    if (paid.get('offer_id') != record['offer_id'] or path != expected_path
            or str(paid.get('ledger_sha256') or '').removeprefix('sha256:') != record['original_files'].get(expected_path)
            or paid.get('confirmed_request_count') != original['confirmed']
            or sorted(paid.get('confirmed_request_ids') or []) != record['paid_request_ids']
            or paid.get('unknown_request_count') != 0):
        _fail('ORIGINAL_PAID_BINDING_CHANGED')


def original_paid_binding(record):
    """Project the retained ledger; do not create a new paid context or budget."""
    summary = record['paid_summary']
    return {'offer_id': record['offer_id'], 'history_root': record['paid_history_root'],
        'ledger_ref': summary['ledger_path'],
        'ledger_sha256': record['original_files'][str(Path(summary['ledger_path']))],
        'confirmed_request_count': summary['confirmed'], 'unknown_request_count': summary['unknown'],
        'confirmed_request_ids': record['paid_request_ids']}


def derive_preparation(record, documents, *, artifact_bytes, paid_history_binding=None):
    """Use the pure image derivation producer with the exact new Task binding."""
    from shared_platform.sku_only_r2_derivation import derive_sku_only_r2
    if paid_history_binding is None:
        paid_history_binding = original_paid_binding(record)
    _check_paid_binding(record, paid_history_binding)
    first, snapshot = preparation_documents(record, documents)
    models = documents['first_review']['product_facts']['skus']
    if len(models) != 1:
        _fail('ONE_MODEL_REQUIRED')
    binding = {'offer_id': record['offer_id'], 'old_seller_sku': record['old_sku'],
        'new_seller_sku': record['new_sku'],
        'models': [{'variant_key': models[0]['source_key'],
                    'old_model_sku': record['old_sku'], 'new_model_sku': record['new_sku']}],
        'old_round1_snapshot_digest': record['old_snapshot_digest'],
        'new_round1_snapshot_digest': snapshot['snapshot_digest'],
        'correction_digest': record['correction_digest'],
        'predecessor_task_id': record['predecessor_task_id'], 'task_id': record['task_id']}
    return derive_sku_only_r2(documents, snapshot, new_first_review=first,
        correction_binding=binding, paid_history_binding=paid_history_binding,
        artifact_bytes=artifact_bytes)


def read_frozen(task, profile, store):
    root = Path(profile.root) / 'reports/product-preparation'
    offer = task['scope']['offer_id']
    release = {'code_version': profile.version, 'environment': profile.environment,
               'manifest_digest': profile.manifest_digest}
    from shared_platform.operations_runtime import runtime_matches
    from shared_platform.native_frozen_source_successor import observes_retained_producer
    if (task.get('version') != release
            or (not observes_retained_producer(task,profile) and not runtime_matches(profile))):
        _fail('CURRENT_RUNTIME_SOURCE_CHANGED')
    record = verify_generation(store, reports_root=root, offer_id=offer, task=task,
                               tasks_path=profile.data_root / 'tasks.db')
    if record is None:
        return None
    from shared_platform import publication_rounds as rounds, round1_workspace as workspace
    from modules.sourcing import new_product_workbench as workbench
    state = workbench.load_state(offer)
    if state.get('sku_only_correction_digest') != record['correction_digest']:
        _fail('PREPARATION_NOT_APPLIED')
    snapshot = rounds.validate_round2_input(offer, state)
    prepared = workspace.read_preparation(offer, state['product_approval']['round1_prepared_reference'])
    if (prepared.get('sku_only_correction', {}).get('correction_digest') != record['correction_digest']
            or prepared['packet']['product_facts']['seller_sku'] != record['new_sku']
            or _digest(prepared['packet']) != snapshot['first_review_digest']):
        _fail('PREPARATION_BINDING_CHANGED')
    return {'offer_id': offer, 'targets': record['scope']['shops'],
        'snapshot_digest': snapshot['snapshot_digest'], 'prepared_reference': prepared['reference'],
        'internal_sku': record['new_sku'], 'seller_sku': record['new_sku']}
