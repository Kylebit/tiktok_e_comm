"""One localized SAVE per original B draft; continue only its unsent publish slot.

The original approval, checkpoint and cap3 ledger remain immutable. This private
path never turns a target with accepted effects into a zero-write retry.
"""
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
from types import SimpleNamespace

from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold, _DurableLedger, _json, _sha, _request_sha
from shared_platform.native_known_rejected_save_repair import _read_reference

EVENT = 'source_code_approved_native_tiktok_locale_draft_correction'
SAVED_EVENT = 'source_code_approved_native_shopee_regional_and_tiktok_saved_publish_continuation'
SAVED_FAILURE_PINS = {
    'tiktok:LH_PH': 'ec6577fcf48e693fe57b4cfc4456893f87c2c509195dbf0c424567df1080fc9e',
    'tiktok:LH_TH': 'e48d873f414343f88b9bf163a7eb2890d08900eb602df4478fa103ebf4eba268',
    'tiktok:LH_VN': '681e313300b07fa853d1901b39e9c1d0d1359216a2a3bab5637826eb26c47c0d'}
PLAN = 'r3-marketplace:8412287877065586543:07707b4b7901ca9b97b08acd'
RUN = 'release-run:25e66f7250d31cbb39ff8d0e'
DECISION = 'native-final-decision:7a10d3ac157c55c6949b663c5b63194b2c43eab81b0fee88eda6aae412838fe2'
SNAPSHOT = 'sha256:8eecd1c0b7beadcaddc7a8655f25b5f9e981729c4cb44e33dbd5aac17eaaa3de'
CANDIDATE = 'c5eacc9725ecefa012af02c6d4f5130c6098c9bedab65e2159357a708c73a5cc'
BASE = '1dd2d705fb4793b99123c47931832d9cefb21b3e'
FILE_RUN = 'native-files-24c3e512eb40b27ee4b58b72a18171caf87359e8c2099676920970900eaab939'
PLATFORM = 'TIKTOK_DRAFT_CORRECTION'
EXACT = {
    'tiktok:LH_MY': ('3487094129', '13295169', 9, 10, '824692276403fd9c4e96d96fdf651bc8fda72daf0646779ce963784eccd46929'),
    'tiktok:LH_PH': ('3487101697', '7676267', 11, 12, '4a3de5c3d7f7eb0c5e7078a5b0a01993eddbdf77658e49ddb4c78956a8863bd1'),
    'tiktok:LH_TH': ('3487109341', '13295228', 13, 14, 'a594d1865cf097df71e64a5f57683ec3d9d80b89dc6f11de129e0a6c3b926480'),
    'tiktok:LH_VN': ('3487114463', '13295291', 15, 16, 'c00e3d0781c37319f1be1b18bd77ddfb50612c4b76f7b62243c9120471592c8a')}
PINS = {
    'standing_authority_ref': 'a8d50c29b67eea25f9bf8b5d9fe9bf42f5055ff3f0831a939acebae062c52262',
    'snapshot_ref': '3e41486796ec38f8f59fa73655abf1bc3deb1df27d62c5222e38baa5d4d02fb1',
    'retained_ref': '93416c2f7c4fcfd0f8d7035fd0d7e703e8720a49a05ffc38fadad4897fe991cf',
    'checkpoint_ref': '1644c3ed4d20c6a3ccc0cf6802e2c2bf309e2eb6a2b88c7d8d089a95e0428e4a',
    'not_sent_proof_ref': 'cbd1501d7298b970dc547b8756082976927d732ab5f89c7eb952ac8e2477eaa7',
    'locale_diagnostic_ref': 'ae58e61c54ccc9cad0de9c96c3ca07df2c381fabec37680a6b0bec4ba2ea86bf'}


def need(value, code):
    if not value:raise NativeMutationBudgetHold('TIKTOK_LOCALE_CORRECTION_' + code)


def _ref(ref):
    need(type(ref) is dict and set(ref) == {'path', 'sha256'}, 'EXACT_LOCAL_PIN_REQUIRED')
    return _read_reference(ref)


def read_correction_proof(ref):
    """Pure typed pin reader shared with the new signed source purpose."""
    doc = json.loads(_ref(ref))
    expected = dict(schema='native-tiktok-localized-draft-correction-technical-authority/v1',
        status='ROOT_TECHNICAL_EXECUTION_APPROVED', task_id='TASK-20261023-001', offer_id='8412287877065586543',
        plan_id=PLAN, run_id=RUN, decision_id=DECISION, candidate_digest=CANDIDATE, snapshot_digest=SNAPSHOT,
        base_source_head=BASE, target_labels=list(EXACT), correction_platform=PLATFORM,
        extra_save_maximum=4, extra_save_per_target=1, original_target_cap=3,
        new_human_content_review=False, new_create_or_claim=False, reset_original_unknown=False)
    need(all(type(doc.get(k)) is type(v) and doc[k] == v for k, v in expected.items()), 'EXACT_TECHNICAL_AUTHORITY_REQUIRED')
    values = {}
    for key, digest in PINS.items():
        need(type(doc.get(key)) is dict and doc[key].get('sha256') == digest, 'ORIGINAL_PIN_CHANGED')
        values[key] = json.loads(_ref(doc[key]))
    image = values['retained_ref'];cp = values['checkpoint_ref'];proof = values['not_sent_proof_ref']
    rows = image.get('native_TikTok_budget_rows');failures = image.get('original_failure3_rows')
    need(type(rows) is list and len(rows) == 16 and type(failures) is list and len(failures) == 4,
        'ORIGINAL_SIXTEEN_ROWS_REQUIRED')
    slots = []
    for label, (detail, shop, saved, publish, digest) in EXACT.items():
        own = [r for r in rows if r['target_label'] == label]
        need([r['operation'] for r in own] == ['create_platform_draft', 'claim_draft', 'save_draft', 'publish_target']
            and [r['sequence'] for r in own[-2:]] == [saved, publish]
            and all(r['outcome'] == 'ACCEPTED' and r['request_digest'] for r in own[:3])
            and own[-1]['outcome'] is None and own[-1]['request_digest'] is None
            and own[-1]['initial_outcome'] == 'UNKNOWN', 'EXACT_NOT_SENT_SLOT_REQUIRED')
        failure = next((r for r in failures if r['target_label'] == label), None)
        need(failure and failure['run_id'] == RUN and failure['attempt'] == 3
            and failure['evidence_digest'] == digest == _sha(failure['evidence_json']), 'ORIGINAL_FAILURE3_REQUIRED')
        events = [e for e in cp['events'] if e['target_label'] == label]
        need([e['operation'] for e in events] == ['CREATE_DRAFT', 'CLAIM_TO_SHOP', 'SAVE_DRAFT']
            and all((e['outcome'], e['detail_id'], e['shop_id']) == ('ACCEPTED', detail, shop) for e in events),
            'TWELVE_ACCEPTED_CHECKPOINT_EVENTS_REQUIRED')
        slots.append(dict(scope_key=own[-1]['scope_key'], sequence=publish, target_label=label,
            detail_id=detail, shop_id=shop, saved_sequence=saved, failure_evidence_digest=digest))
    need(doc.get('not_sent_slots') == slots and len(cp['events']) == cp['external_write_count'] == 12
        and cp['run_id'] == FILE_RUN and cp['snapshot_digest'] == SNAPSHOT
        and values['snapshot_ref']['snapshot_digest'] == SNAPSHOT, 'FROZEN_IDENTITY_CHANGED')
    need(proof.get('schema') == 'four-original-reserved-publish-not-sent-gate/v1'
        and proof.get('source_head') == BASE and len(proof.get('matrix', [])) == 4
        and all(r.get('operation_delivery_classification') == 'NOT_SENT_FOR_PUBLISH_ONLY'
            and r.get('publish_dispatch_row') is False and r.get('publish_outcome_row') is False
            and r.get('overall_target_zero') is False for r in proof['matrix']), 'WIRE_NOT_SENT_PROOF_REQUIRED')
    return doc


def _event_ref(bound, task):
    with sqlite3.connect(bound.engine.store.path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=?',
            (task['task_id'], EVENT)).fetchall()
    need(len(rows) == 1, 'NEW_DISTINCT_SIGNED_SOURCE_EVENT_REQUIRED')
    envelope = json.loads(rows[0][0]);raw = envelope.get('record_json')
    need(type(raw) is str and _sha(raw) == envelope.get('record_digest'), 'SIGNED_SOURCE_ENVELOPE_REQUIRED')
    record = json.loads(raw)
    ref = record.get('tiktok_draft_correction_ref');doc = read_correction_proof(ref)
    need(record.get('tiktok_draft_correction_digest') == _sha(_json(doc)), 'SIGNED_TECHNICAL_PROOF_CHANGED')
    return ref, doc


def _image_budget(image, platform):
    scopes = {r['scope_key'] for r in image['native_final_budget_scopes'] if r['platform'] == platform}
    dispatches = {(r['scope_key'], r['sequence']): r for r in image['native_final_budget_dispatches']}
    outcomes = {(r['scope_key'], r['sequence']): r for r in image['native_final_budget_outcomes']}
    return [{**r, 'request_digest': dispatches.get((r['scope_key'], r['sequence']), {}).get('request_digest'),
        'outcome': outcomes.get((r['scope_key'], r['sequence']), {}).get('outcome')}
        for r in sorted(image['native_final_budget_reservations'], key=lambda r: r['sequence']) if r['scope_key'] in scopes]


def read_saved_publish_continuation_proof(ref):
    """Same original accepted SAVE, NOT_SENT publish only; no new authority/cap."""
    doc = json.loads(_ref(ref));labels = doc.get('target_labels')
    expected = dict(schema='native-tiktok-accepted-save-unsent-publish-continuation/v1',
        status='ROOT_TECHNICAL_EXECUTION_APPROVED', task_id='TASK-20261023-001', offer_id='8412287877065586543',
        plan_id=PLAN, run_id=RUN, decision_id=DECISION, candidate_digest=CANDIDATE, snapshot_digest=SNAPSHOT,
        before_attempt=4, no_new_SAVE=True, no_new_CREATE_or_CLAIM=True, no_new_budget=True)
    need(all(type(doc.get(k)) is type(v) and doc[k] == v for k, v in expected.items())
        and type(labels) is list and labels and labels == [label for label in EXACT if label in labels]
        and set(labels) <= set(SAVED_FAILURE_PINS) and len(labels) == len(set(labels)), 'EXACT_SAVED_CONTINUATION_PROOF_REQUIRED')
    need(type(doc.get('base_source_head')) is str and len(doc['base_source_head']) == 40
        and all(c in '0123456789abcdef' for c in doc['base_source_head']), 'SAVED_SOURCE_HEAD_REQUIRED')
    old_ref = doc['original_correction_authority_ref']
    need(old_ref.get('sha256') == 'f46b1b49519e3d16094453f165ab2643111b74df28dbc0234273ebaba3eed10c', 'ORIGINAL_CORRECTION_AUTHORITY_REQUIRED')
    original = read_correction_proof(old_ref)
    need(doc['checkpoint_ref'].get('sha256') == PINS['checkpoint_ref']
        and _ref(doc['checkpoint_ref']) == _ref(original['checkpoint_ref']), 'ORIGINAL_CP12_CHANGED')
    image = json.loads(_ref(doc['retained_ref']));rows = _image_budget(image, 'TIKTOK')
    need(image.get('source_head') == doc['base_source_head'], 'SAVED_CURRENT_IMAGE_SOURCE_CHANGED')
    correction = _image_budget(image, PLATFORM)
    scopes = [r for r in image['native_final_budget_scopes'] if r['platform'] == PLATFORM]
    binding = dict(plan_id=PLAN, snapshot_digest=SNAPSHOT, platform=PLATFORM, target_labels=list(EXACT),
        shared_maximum=0, per_target_maximum=1, decision_id=DECISION, candidate_digest=CANDIDATE,
        technical_proof_digest=_sha(_json(original)), technical_proof_ref=old_ref)
    need(len(rows) == 16 and len(scopes) == 1 and json.loads(scopes[0]['binding_json']) == binding
        and scopes[0]['binding_sha256'] == _sha(_json(binding)), 'ORIGINAL_ACCEPTED_CORRECTION_SCOPE_REQUIRED')
    targets = {r['target_label']: r for r in image['release_target_runs']};slots = []
    need(set(doc['failure_refs']) == set(labels), 'EXACT_FAILURE4_FILES_REQUIRED')
    for label in labels:
        failure_ref = doc['failure_refs'][label]
        need(failure_ref.get('sha256') == SAVED_FAILURE_PINS[label], 'ORIGINAL_FAILURE4_PIN_REQUIRED')
        failure = json.loads(_ref(failure_ref))['failure_rows']
        need(len(failure) == 1 and failure[0]['run_id'] == RUN and failure[0]['target_label'] == label
            and failure[0]['attempt'] == 4 and _sha(failure[0]['evidence_json']) == failure[0]['evidence_digest'], 'ORIGINAL_FAILURE4_REQUIRED')
        need(failure[0] in image['release_target_failure_events'], 'FAILURE4_NOT_IN_CURRENT_IMAGE')
        value = json.loads(failure[0]['evidence_json'])
        canonical = value['canonical_platform_result']['targets'][0]
        need(canonical['target_label'] == label and canonical['status'] == 'FAILED'
            and canonical['evidence']['stage'] == 'PUBLISH' and canonical['evidence']['provider_code'] == 'transport_unknown', 'EXACT_PUBLISH_READ_FAILURE_REQUIRED')
        target = targets[label]
        need(target['status'] == 'FAILED' and target['attempts'] == 4, 'ORIGINAL_FAILED4_REQUIRED')
        detail, shop, _, sequence, _ = EXACT[label]
        slot = next(r for r in rows if r['sequence'] == sequence)
        saved = [r for r in correction if r['target_label'] == label]
        need(slot['target_label'] == label and slot['operation'] == 'publish_target'
            and slot['initial_outcome'] == 'UNKNOWN' and slot['request_digest'] is None and slot['outcome'] is None,
            'ORIGINAL_PUBLISH_NOT_SENT_REQUIRED')
        need(len(saved) == 1 and saved[0]['budget_scope'] == 'target' and saved[0]['operation'] == 'save_draft'
            and saved[0]['attempt_count'] == 1 and saved[0]['request_digest'] and saved[0]['outcome'] == 'ACCEPTED', 'ORIGINAL_CORRECTIVE_SAVE_ACCEPTED_REQUIRED')
        slots.append(dict(target_label=label, scope_key=slot['scope_key'], sequence=sequence, detail_id=detail, shop_id=shop,
            failure4_evidence_digest=failure[0]['evidence_digest'], correction_scope_key=saved[0]['scope_key'],
            correction_sequence=saved[0]['sequence'], correction_request_digest=saved[0]['request_digest']))
    need(doc.get('not_sent_slots') == slots, 'EXACT_SAVED_NOT_SENT_SLOTS_REQUIRED')
    return doc


def _saved_event(bound, task):
    with sqlite3.connect(bound.engine.store.path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=?', (task['task_id'], SAVED_EVENT)).fetchall()
    if not rows:return None
    need(len(rows) == 1, 'SINGLE_SAVED_CONTINUATION_EVENT_REQUIRED')
    envelope = json.loads(rows[0][0]);raw = envelope.get('record_json')
    need(type(raw) is str and _sha(raw) == envelope.get('record_digest'), 'SIGNED_SAVED_ENVELOPE_REQUIRED')
    record = json.loads(raw);ref = record['tiktok_saved_publish_continuation_ref'];doc = read_saved_publish_continuation_proof(ref)
    need(record['tiktok_saved_publish_continuation_digest'] == _sha(_json(doc)), 'SIGNED_SAVED_PROOF_CHANGED')
    return _saved_material(ref, doc)


def _saved_material(ref, doc):
    return dict(ref=ref, doc=doc, digest=_sha(_json(doc)), image=json.loads(_ref(doc['retained_ref'])),
        failures={label: json.loads(_ref(doc['failure_refs'][label]))['failure_rows'][0] for label in doc['target_labels']})


def _material(ref, doc, root):
    image = json.loads(_ref(doc['retained_ref']));snapshot = json.loads(_ref(doc['snapshot_ref']))
    cp_raw = _ref(doc['checkpoint_ref']);cp = json.loads(cp_raw)
    return dict(ref=ref, doc=doc, proof_digest=_sha(_json(doc)), image=image, snapshot=snapshot,
        checkpoint=cp, checkpoint_raw=cp_raw, root=root)


def _context(active):
    from shared_platform.native_sole_final_execution import _NativeExecution, _ACTIVE
    from shared_platform.agent_prepared_v4_targets import _scope
    need(type(active) is _NativeExecution and _ACTIVE.get() is active and active.plan_id == PLAN
        and active.decision_id == DECISION and active.service.store is active.store, 'PROTECTED_ORIGINAL_DECISION_REQUIRED')
    need(active.service._actor()['owner_sid'] == active.owner_sid, 'CURRENT_ACTOR_REQUIRED')
    # Genuine current source/HMAC/adoption verifies the new purpose before the ref is consumed.
    bound, task, _ = _scope(active, active.store.get_plan(PLAN))
    ref, doc = _event_ref(bound, task)
    ctx = _material(ref, doc, active.runtime.supervisor.root);ctx['active'] = active
    ctx['saved_continuation'] = _saved_event(bound, task)
    _checkpoint(ctx)
    return ctx


def _checkpoint(ctx):
    from shared_platform.product_publication_live_dependencies import TikTokV4DraftCheckpointStore
    request = SimpleNamespace(platform='TIKTOK', snapshot=ctx['snapshot'], run_id=FILE_RUN)
    store = TikTokV4DraftCheckpointStore(ctx['root']);path = store._path(request)
    need(path.is_file() and path.read_bytes() == ctx['checkpoint_raw'], 'ORIGINAL_CP12_CHANGED')
    need(store.load(request) == ctx['checkpoint'], 'VERIFIED_CP12_REQUIRED')


def _rows(db, scope):
    return [dict(r) for r in db.execute('SELECT r.*,d.request_digest,o.outcome '
        'FROM native_final_budget_reservations r LEFT JOIN native_final_budget_dispatches d USING(scope_key,sequence) '
        'LEFT JOIN native_final_budget_outcomes o USING(scope_key,sequence) WHERE r.scope_key=? ORDER BY r.sequence', (scope,))]


def _facts(db, ctx):
    row = db.execute('SELECT snapshot_json FROM approved_publication_snapshots WHERE plan_id=?', (PLAN,)).fetchone()
    need(row and _sha(row[0]) == PINS['snapshot_ref'], 'ORIGINAL_APPROVED_SNAPSHOT_CHANGED')
    scope = db.execute("SELECT * FROM native_final_budget_scopes WHERE plan_id=? AND snapshot_digest=? AND platform='TIKTOK'",
        (PLAN, SNAPSHOT)).fetchone()
    need(scope and _sha(scope['binding_json']) == scope['binding_sha256'], 'ORIGINAL_BUDGET_REQUIRED')
    binding = json.loads(scope['binding_json'])
    frozen_order = [r['target_label'] for r in ctx['snapshot']['publication_targets'] if r['target_label'] in EXACT]
    need(set(frozen_order) == set(EXACT) and binding.get('target_labels') == frozen_order and binding.get('shared_maximum') == 4
        and binding.get('per_target_maximum') == 3 and binding.get('decision_id') == DECISION
        and binding.get('candidate_digest') == CANDIDATE, 'ORIGINAL_CAP3_CHANGED')
    current = _rows(db, scope['scope_key']);old = ctx['image']['native_TikTok_budget_rows']
    need(len(current) == len(old) == 16, 'ORIGINAL_SIXTEEN_RESERVATIONS_CHANGED')
    for actual, original in zip(current, old):
        if original['operation'] != 'publish_target':need(actual == original, 'ACCEPTED_PREFIX_CHANGED')
        else:
            need({k: v for k, v in actual.items() if k not in {'request_digest', 'outcome'}}
                == {k: v for k, v in original.items() if k not in {'request_digest', 'outcome'}}, 'OLD_PUBLISH_RESERVATION_CHANGED')
            need(actual['outcome'] in (None, 'ACCEPTED', 'REJECTED')
                and (actual['outcome'] is None or actual['request_digest']), 'OLD_PUBLISH_OUTCOME_INVALID')
            from modules.miaoshou.tiktok_publisher import PUBLISH_PATH
            detail, shop, _, _, _ = EXACT[actual['target_label']]
            need(actual['request_digest'] in (None, _request_sha('publish_target',
                (PUBLISH_PATH, {'detailIds': [int(detail)], 'shopIds': [shop]}), {})), 'ORIGINAL_PUBLISH_BODY_CHANGED')
    for original in ctx['image']['original_failure3_rows']:
        row = db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? AND attempt=3',
            (RUN, original['target_label'])).fetchone()
        need(row and dict(row) == original, 'ORIGINAL_FAILURE3_CHANGED')
    return binding, current


def _technical(ctx, label, recheck=None):
    binding = dict(plan_id=PLAN, snapshot_digest=SNAPSHOT, platform=PLATFORM, target_labels=list(EXACT),
        shared_maximum=0, per_target_maximum=1, decision_id=DECISION, candidate_digest=CANDIDATE,
        technical_proof_digest=ctx['proof_digest'], technical_proof_ref=ctx['ref'])
    return _DurableLedger(store=ctx['active'].store, binding=binding, target_label=label, recheck=recheck)


def _saved_facts(db, ctx, label):
    proof = ctx.get('saved_continuation')
    need(proof and label in proof['doc']['target_labels'], 'SAVED_TARGET_NOT_AUTHORIZED')
    original = proof['failures'][label]
    failure = db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? AND attempt=4', (RUN, label)).fetchone()
    need(failure and dict(failure) == original, 'ORIGINAL_FAILURE4_CHANGED')
    target = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, label)).fetchone()
    old_target = next(r for r in proof['image']['release_target_runs'] if r['target_label'] == label)
    need(target and all(target[k] == old_target[k] for k in ('run_id', 'target_label', 'idempotency_key', 'created_at', 'external_id')),
        'ORIGINAL_SAVED_TARGET_IDENTITY_CHANGED')
    old_scope = next(r for r in proof['image']['native_final_budget_scopes'] if r['platform'] == PLATFORM)
    scope = db.execute('SELECT * FROM native_final_budget_scopes WHERE scope_key=?', (old_scope['scope_key'],)).fetchone()
    need(scope and dict(scope) == old_scope, 'ACCEPTED_CORRECTION_BINDING_CHANGED')
    rows = [r for r in _rows(db, scope['scope_key']) if r['target_label'] == label]
    need(rows == [r for r in _image_budget(proof['image'], PLATFORM) if r['target_label'] == label], 'ACCEPTED_CORRECTION_CHANGED')


def activate_failed_targets(active):
    if getattr(active, 'plan_id', None) != PLAN:return ()
    ctx = _context(active);labels = []
    # Constructors inspect schema/binding on their own readonly connection.
    # Finish those external reads before taking the ReleaseStore writer; the
    # same connection rechecks each immutable binding immediately before CAS.
    technical = {label: _technical(ctx, label) for label in EXACT}
    with active.store._transaction() as db:
        active.service._decision(db, DECISION, owner_sid=active.owner_sid)
        active.store._require_active_run(db, RUN);_, rows = _facts(db, ctx)
        for label in EXACT:
            target = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, label)).fetchone()
            slot = next(r for r in rows if r['sequence'] == EXACT[label][3])
            ledger = technical[label]
            ledger._check_schema(db);ledger._binding(db, create=False)
            extra = ledger._rows(db)
            if target['attempts'] == 4 and target['status'] in {'FAILED', 'PENDING'}:
                if not ctx.get('saved_continuation') or label not in ctx['saved_continuation']['doc']['target_labels']:continue
                _saved_facts(db, ctx, label)
                need(slot['request_digest'] is None and slot['outcome'] is None, 'SAVED_PUBLISH_ALREADY_DISPATCHED_OR_UNKNOWN')
                if target['status'] == 'FAILED':
                    changed = db.execute("UPDATE release_target_runs SET status='PENDING',completed_at=NULL WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=4",
                        (RUN, label));need(changed.rowcount == 1, 'FAILED4_PENDING4_CAS_CHANGED')
                labels.append(label);continue
            if (target['attempts'] != 3 or target['status'] not in {'FAILED', 'PENDING'}
                    or slot['request_digest'] is not None or slot['outcome'] is not None or extra):continue
            need([r[0] for r in db.execute('SELECT attempt FROM release_target_failure_events '
                'WHERE run_id=? AND target_label=? ORDER BY attempt', (RUN, label))] == [1, 2, 3], 'EXACT_FAILURE_HISTORY_REQUIRED')
            if target['status'] == 'FAILED':
                changed = db.execute("UPDATE release_target_runs SET status='PENDING',completed_at=NULL "
                    "WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=3", (RUN, label))
                need(changed.rowcount == 1, 'FAILED3_PENDING3_CAS_CHANGED')
            labels.append(label)
    return tuple(labels)


class _CorrectionLedger:
    def __init__(self, ctx, base, *, saved_only=False):
        self.ctx, self.base = ctx, base
        self.saved_only = saved_only
        self.prepared = None;self.target = None;self.fresh_match = False
        self.extra = _technical(ctx, base.target_label, self._recheck)
    def __getattr__(self, key):return getattr(self.base, key)
    def _recheck(self, db=None):
        # Full source/file verification is outside SQLite's writer transaction.
        # Its same-connection branch only compares immutable rows and the actor.
        self.base._recheck(db)
        if db is None:
            current = _context(self.ctx['active'])
            need(current['proof_digest'] == self.ctx['proof_digest'], 'CURRENT_SOURCE_OR_PROOF_CHANGED')
            need(not self.saved_only or (current.get('saved_continuation') or {}).get('digest') == self.ctx['saved_continuation']['digest'], 'CURRENT_SAVED_PROOF_CHANGED')
            with self.base._connect(readonly=True) as current_db:
                _facts(current_db, self.ctx)
                if self.saved_only:_saved_facts(current_db, self.ctx, self.base.target_label)
        else:
            _facts(db, self.ctx)
            if self.saved_only:_saved_facts(db, self.ctx, self.base.target_label)
    def reserve_shared(self, *args, **kwargs):need(False, 'NO_CREATE_OR_CLAIM')
    def reserve_target(self, label, operation, *, count=1):
        need(label == self.base.target_label and type(count) is int and count == 1, 'EXACT_TARGET_REQUIRED')
        self._recheck()
        if operation == 'publish_target':
            with self.base._connect(readonly=True) as db:
                _, rows = _facts(db, self.ctx);slot = next(r for r in rows if r['sequence'] == EXACT[label][3])
                need(slot['request_digest'] is None and slot['outcome'] is None, 'OLD_PUBLISH_ALREADY_DISPATCHED')
            record = dict(sequence=slot['sequence'], scope='target', target_label=label,
                operation=operation, attempt_count=1, outcome='UNKNOWN')
            self.base._pending[operation] = record
            return dict(record)
        need(not self.saved_only, 'ACCEPTED_SAVE_MUST_NOT_REPEAT')
        need(operation == 'save_draft' and self.prepared is not None and not self.fresh_match, 'ONE_PREPARED_CORRECTIVE_SAVE_ONLY')
        return self.extra.reserve_target(label, operation, count=count)
    def dispatch_reserved(self, operation, call, *args, **kwargs):
        self._recheck()
        if operation == 'save_draft':
            need(not self.saved_only, 'ACCEPTED_SAVE_MUST_NOT_REPEAT')
            need(self.prepared is not None and args == (self.prepared['path'], self.prepared['body'])
                and not kwargs, 'EXACT_PREPARED_CORRECTION_REQUIRED')
            return self.extra.dispatch_reserved(operation, call, *args, **kwargs)
        from modules.miaoshou.tiktok_publisher import PUBLISH_PATH
        label = self.base.target_label;detail, shop, _, _, _ = EXACT[label]
        need(operation == 'publish_target' and self.fresh_match and self.target is not None
            and args == (PUBLISH_PATH, {'detailIds': [int(detail)], 'shopIds': [shop]}) and not kwargs,
            'FRESH_EXACT_MATCH_BEFORE_ORIGINAL_PUBLISH_REQUIRED')
        with self.extra._connect(readonly=True) as db:
            rows = [r for r in self.extra._rows(db) if r['target_label'] == label]
            need(not rows or len(rows) == 1 and rows[0]['outcome'] == 'ACCEPTED', 'CORRECTIVE_SAVE_UNKNOWN_OR_REJECTED')
        return self.base.dispatch_reserved(operation, call, *args, **kwargs)
    def complete_reserved(self, operation, outcome):
        self._recheck()
        return (self.extra if operation == 'save_draft' else self.base).complete_reserved(operation, outcome)
    def wrap_post(self, post, *, provider):
        need(provider == 'miaoshou', 'EXACT_PROVIDER_REQUIRED')
        return _DurableLedger.wrap_post(self, post, provider=provider)
    def snapshot(self):
        value = deepcopy(self.base.snapshot());value['technical_draft_correction_budget'] = self.extra.snapshot()
        return value


def for_active(active, base):
    if getattr(active, 'plan_id', None) != PLAN or base.platform != 'TIKTOK' or base.target_label not in EXACT:return base
    ctx = _context(active)
    with base._connect(readonly=True) as db:
        _facts(db, ctx)
        target = db.execute('SELECT status,attempts FROM release_target_runs WHERE run_id=? AND target_label=?',
            (RUN, base.target_label)).fetchone()
        saved_only = bool(target and tuple(target) == ('RUNNING', 5))
        if saved_only:_saved_facts(db, ctx, base.target_label)
        else:need(target and tuple(target) == ('RUNNING', 4), 'ORIGINAL_CONTINUATION_BEGIN4_REQUIRED')
    return _CorrectionLedger(ctx, base, saved_only=saved_only)


def bind_publisher(publisher, ledger):
    if type(ledger) is not _CorrectionLedger:return publisher
    original = publisher.transport
    from domains.channel_operations.tiktok_v4_execution import project_tiktok_v4_execution_plan
    from domains.channel_operations.tiktok_publisher import _validate_snapshot
    from modules.miaoshou.tiktok_v4_drafts import _collectbox_context
    label = ledger.base.target_label;detail, shop, _, _, _ = EXACT[label]
    context = _collectbox_context(ledger.ctx['snapshot'],
        identity=dict(target_label=label, detail_id=detail, shop_id=shop))
    plan = project_tiktok_v4_execution_plan(ledger.ctx['snapshot'],
        collectbox_contexts={label: context}, category_resolver=None, target_scope=(label,))
    expected = _validate_snapshot(plan['targets'][0]['publisher_snapshot'])['targets'][0]
    class ExactTransport:
        def __getattr__(self, key):return getattr(original, key)
        def _target(self, target):need(target == expected, 'FROZEN_LOCALIZED_TARGET_CHANGED')
        def prepare_approved_draft(self, target, draft):
            self._target(target);ledger._recheck()
            prepared = original.prepare_approved_draft(target, draft)
            ledger.prepared = deepcopy(prepared);ledger.target = deepcopy(target);ledger.fresh_match = False
            return prepared
        def post_save_draft_matches(self, target, draft):
            self._target(target);matched = original.post_save_draft_matches(target, draft)
            ledger.target = deepcopy(target);ledger.fresh_match = matched is True
            return matched
        def save_prepared_draft(self, target, prepared):
            self._target(target)
            from modules.miaoshou.client import MiaoshouBusinessRejectedError
            try:return original.save_prepared_draft(target, prepared)
            except MiaoshouBusinessRejectedError:
                ledger.complete_reserved('save_draft', 'REJECTED')
                raise
        def submit(self, target):
            self._target(target);ledger._recheck()
            # The canonical publisher otherwise submits immediately after a repair.
            # Require an actual new exact GET, including all original strict fields.
            draft = original.read_draft(target)
            need(self.post_save_draft_matches(target, draft), 'CORRECTED_DRAFT_EXACT_READBACK_REQUIRED')
            return original.submit(target)
    return replace(publisher, transport=ExactTransport())


def bind_preparer(preparer, ledger, resolver):
    if type(ledger) is not _CorrectionLedger:return preparer
    from shared_platform.product_publication_live_dependencies import _ResumableTikTokDraftTransport, TikTokV4DraftCheckpointStore, _request_snapshot
    from modules.miaoshou.tiktok_v4_drafts import prepare_tiktok_v4_drafts
    class NeverMutation:
        def claim_or_create(self, **kwargs):need(False, 'RECREATE_FORBIDDEN')
        def prepare_save_draft(self, **kwargs):need(False, 'UNBOUND_PREPARATION_FORBIDDEN')
        def save_prepared_draft(self, **kwargs):need(False, 'ORIGINAL_SAVE_REPLAY_FORBIDDEN')
    class AcceptedPreparer:
        def __call__(self, request):
            ledger._recheck();_checkpoint(ledger.ctx)
            need(tuple(request.target_labels) == (ledger.base.target_label,), 'EXACT_REQUEST_SCOPE_REQUIRED')
            transport = _ResumableTikTokDraftTransport(request=request,
                store=TikTokV4DraftCheckpointStore(ledger.ctx['root']), transport=NeverMutation())
            receipt = dict(prepare_tiktok_v4_drafts(_request_snapshot(request, 'TIKTOK'),
                category_resolver=resolver, transport=transport, target_scope=tuple(request.target_labels)))
            receipt['external_write_count'] = 12
            receipt.pop('receipt_digest', None);receipt['receipt_digest'] = 'sha256:' + _sha(_json(receipt))
            _checkpoint(ledger.ctx)  # No finalize: original CP12 bytes stay intact.
            return receipt
        def write_count(self, request):_checkpoint(ledger.ctx);return 12
    return AcceptedPreparer()


def has_pending_work(store, decision_id, operations):
    """Queue availability only; protected activation and per-wire checks decide."""
    if decision_id != DECISION or operations is None:return False
    from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
    bound = getattr(operations, 'agent_prepared_common', None)
    if type(bound) is not AgentPreparedCommonService or bound.store is not store:return False
    try:
        ref, doc = _event_ref(bound, {'task_id': 'TASK-20261023-001'})
        ctx = _material(ref, doc, None)  # Availability is not execution or a CP mutation.
        ctx['saved_continuation'] = _saved_event(bound, {'task_id': 'TASK-20261023-001'})
        with store._connect_readonly() as db:
            _, rows = _facts(db, ctx)
            return any((row['attempts'] == 3 or row['attempts'] == 4 and ctx.get('saved_continuation')
                and row['target_label'] in ctx['saved_continuation']['doc']['target_labels']) and row['status'] in {'FAILED', 'PENDING'}
                and any(slot['target_label'] == row['target_label'] and slot['operation'] == 'publish_target'
                    and slot['request_digest'] is None and slot['outcome'] is None for slot in rows)
                for row in db.execute('SELECT * FROM release_target_runs WHERE run_id=?', (RUN,)))
    except (NativeMutationBudgetHold, ValueError, OSError, KeyError, TypeError, AttributeError):return False
