"""Exact original Shopee local-input failure continuation, not generic retry.

No schema, approval, reservation or provider operation is created here. Only
the protected original executor may restore its four proven pre-submit-zero
attempt-1 rows to PENDING; the original begin_target owns attempt 2.
"""
import hashlib
import json

from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold

PLAN = 'r3-marketplace:4039931504:b21fbd9f11f01e3d04fc0780'
RUN = 'release-run:715e2b6e14103dbcdb129128'
DECISION = 'native-final-decision:da22e734faad76bf1de4b00ffca5dba3421682d0ce94f68dc6595b7440935ef6'
SNAPSHOT = 'sha256:ed9fb745466e25ae91da35a21f349ccec5dd7703fc14d217e04fd4447472d1c1'
LABELS = frozenset({'shopee:MY', 'shopee:PH', 'shopee:TH', 'shopee:VN'})
CODE = 'NATIVE_V4_LOCAL_DEPENDENCY_UNAVAILABLE'


def _need(value, code):
    if not value:
        raise NativeMutationBudgetHold(code)


def _protected(active):
    from shared_platform.native_sole_final_execution import _NativeExecution, _ACTIVE
    _need(type(active) is _NativeExecution and _ACTIVE.get() is active
          and active.plan_id == PLAN and active.decision_id == DECISION
          and active.service.store is active.store
          and active.service._actor()['owner_sid'] == active.owner_sid,
          'SHOPEE_ZERO_PROTECTED_CURRENT_EXECUTION_REQUIRED')
    from shared_platform.native_prepared_v4_targets import _scope
    _scope(active, active.store.get_plan(PLAN))


def _approved(active, db):
    checked = active.service._decision(db, DECISION, owner_sid=active.owner_sid)
    _need(checked['snapshot_digest'] == SNAPSHOT, 'SHOPEE_ZERO_ORIGINAL_SNAPSHOT_CHANGED')
    run = active.store._require_active_run(db, RUN)
    _need(run['plan_id'] == PLAN, 'SHOPEE_ZERO_ORIGINAL_RUN_CHANGED')


def _validate_target(db, label, status):
    _need(label in LABELS and status in {'FAILED', 'PENDING'}, 'SHOPEE_ZERO_EXACT_TARGET_REQUIRED')
    row = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, label)).fetchone()
    _need(row is not None and row['status'] == status and row['attempts'] == 1
          and row['external_id'] is None and row['error'] == CODE,
          'SHOPEE_ZERO_EXACT_LOCAL_FAILURE_REQUIRED')
    failures = db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? ORDER BY attempt',
                          (RUN, label)).fetchall()
    _need(len(failures) == 1 and failures[0]['attempt'] == 1, 'SHOPEE_ZERO_SINGLE_ORIGINAL_FAILURE_REQUIRED')
    event = failures[0]
    raw = event['evidence_json']
    _need(hashlib.sha256(raw.encode('utf-8')).hexdigest() == event['evidence_digest'],
          'SHOPEE_ZERO_FAILURE_DIGEST_CHANGED')
    evidence = json.loads(raw)
    expected = {'schema_version': 'native-scoped-v4-target-result/v1', 'target_label': label,
                'verified': False, 'status': 'FAILED', 'request_attempted': False, 'outcome_unknown': False,
                'known_pre_submit_zero': True, 'reconciliation_required': False, 'external_writes_performed': []}
    _need(type(evidence) is dict and set(evidence) == set(expected)
          and all(type(evidence[key]) is type(value) and evidence[key] == value for key, value in expected.items()),
          'SHOPEE_ZERO_TYPED_PRE_SUBMIT_ZERO_REQUIRED')
    for table in ('release_target_submissions', 'release_target_readbacks', 'release_target_repairs'):
        _need(db.execute('SELECT 1 FROM ' + table + ' WHERE run_id=? AND target_label=?', (RUN, label)).fetchone() is None,
              'SHOPEE_ZERO_EXISTING_BUSINESS_EFFECT_NO_REPLAY')
    # Any reservation for this target is already a mutation attempt, even if
    # it has no dispatch yet. Never borrow the TikTok undispatched branch.
    _need(db.execute('''SELECT 1 FROM native_final_budget_reservations r
        JOIN native_final_budget_scopes s USING(scope_key)
        WHERE s.plan_id=? AND r.target_label=?''', (PLAN, label)).fetchone() is None,
        'SHOPEE_ZERO_EXISTING_RESERVATION_NO_REPLAY')
    return row


def activate_failed_targets(active):
    if getattr(active, 'plan_id', None) != PLAN or getattr(active, 'decision_id', None) != DECISION:
        return ()
    _protected(active)
    selected = []
    with active.store._transaction() as db:
        _approved(active, db)
        rows = db.execute('SELECT * FROM release_target_runs WHERE run_id=? ORDER BY target_label', (RUN,)).fetchall()
        for row in rows:
            if row['target_label'] in LABELS and row['status'] == 'FAILED' and row['attempts'] == 1 and row['error'] == CODE:
                _validate_target(db, row['target_label'], 'FAILED')
                selected.append(row['target_label'])
        for label in selected:
            # Keep the original code until begin_target clears it. This also
            # makes an interrupted-before-begin PENDING row independently
            # recognizable without deleting or editing the old failure event.
            changed = db.execute('''UPDATE release_target_runs SET status='PENDING',completed_at=NULL
                WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=1 AND error=?''',
                (RUN, label, CODE))
            _need(changed.rowcount == 1, 'SHOPEE_ZERO_ORIGINAL_TARGET_CAS_CHANGED')
    return tuple(selected)


def recovery_actions(active, run, actions):
    if getattr(active, 'plan_id', None) != PLAN or getattr(active, 'decision_id', None) != DECISION:
        return actions
    _protected(active)
    _need(run.get('run_id') == RUN, 'SHOPEE_ZERO_ORIGINAL_RUN_CHANGED')
    eligible = set()
    with active.store._connect_readonly() as db:
        _approved(active, db)
        for row in run['targets']:
            if row['target_label'] in LABELS and row['status'] == 'PENDING' and row['attempts'] == 1 and row['error'] == CODE:
                _validate_target(db, row['target_label'], 'PENDING')
                eligible.add(row['target_label'])
    return [{**action, 'action_kind': 'NATIVE_SHOPEE_KNOWN_ZERO_CONTINUATION', 'runnable': True,
             'reason_code': 'original_native_shopee_local_input_corrected_no_prior_mutation'}
            if action.get('target_label') in eligible else action for action in actions]


# Attempt 1 remains the original implementation above. This additional branch
# recognizes only the four immutable, observed attempt-2 pre-submit failures.
_activate_first_attempt = activate_failed_targets
_first_attempt_actions = recovery_actions
APP_ID = 2036649
EXACT_SHOPS = {'MY': 1561117812, 'PH': 1527371343, 'TH': 1561124013, 'VN': 1723948773}
SECOND_ERROR_SHA = '08717046e7e5520efdfab58a650f6f9bcc49b897cb44c297e148069b54f7584e'
FAILURE_PINS = {
    'shopee:MY': ('165c0a799a9e2a08fe5e847f1222e7daf2bf8d4f57505cac125726ea33f6da97', '68f754b3b727db581b678987d71c4c66bcdf0c7fde71b30133d0d9e1dc610289'),
    'shopee:PH': ('c1466c67a6bbd4983d0e621b5352a339f7c1b69e3136528a7a6cb5019e6e9fca', '38d1d0febccac7510aa209e87810c40dca7c94a51436d1e44835a2d7979764c4'),
    'shopee:TH': ('fdb7aec27b248689a73f08b50b42961855922194eb4850cb756b00a5eb488bd6', '16d68350f8377837e75c175213e11152255b62685492e0e37b5f28e167d4131f'),
    'shopee:VN': ('1086b73983b33da962a5ea6b0ab0f249edfde53389fb65abfd6aa5d3acee78db', 'a17c32cefd4b5d6a0288aa5565e6b69876ab07bdcd59790a8c79c255b2421263'),
}


def _validate_second_zero(db, label, status):
    # Failure-event rows retain evidence, not their former target.error. Pin
    # the already-recorded attempt-1 observation instead of inventing a column.
    from pathlib import Path
    source = Path('E:/OrbitEvidence/4039931504-publication-20261019/actual-monitor/STATE_022.json')
    raw_source = source.read_bytes()
    _need(hashlib.sha256(raw_source).hexdigest() == 'a4faa8e96d199cf2dc17249ccfe35f11735138b853eee3921ed270cb3d1ed797',
          'SHOPEE_ZERO_SECOND_ORIGINAL_ERROR_EVIDENCE_CHANGED')
    original = [r for r in json.loads(raw_source)['target_states'] if r['target_label'] == label]
    _need(len(original) == 1 and original[0]['status'] == 'FAILED' and original[0]['attempts'] == 1
          and original[0]['safe_error_code'] == CODE, 'SHOPEE_ZERO_SECOND_ORIGINAL_ERROR_REQUIRED')
    _need(label in LABELS and status in {'FAILED', 'PENDING'}, 'SHOPEE_ZERO_SECOND_EXACT_TARGET_REQUIRED')
    row = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, label)).fetchone()
    _need(row is not None and row['status'] == status and row['attempts'] == 2
          and row['external_id'] is None and type(row['error']) is str
          and hashlib.sha256(row['error'].encode()).hexdigest() == SECOND_ERROR_SHA,
          'SHOPEE_ZERO_SECOND_EXACT_FAILURE_REQUIRED')
    events = db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? ORDER BY attempt', (RUN, label)).fetchall()
    _need([event['attempt'] for event in events] == [1, 2], 'SHOPEE_ZERO_SECOND_TWO_RETAINED_FAILURES_REQUIRED')
    for index, event in enumerate(events):
        raw = event['evidence_json']
        _need(hashlib.sha256(raw.encode()).hexdigest() == event['evidence_digest'] == FAILURE_PINS[label][index],
              'SHOPEE_ZERO_SECOND_IMMUTABLE_FAILURE_CHANGED')
        facts = json.loads(raw)
        required = {'schema_version': 'native-scoped-v4-target-result/v1', 'target_label': label,
                    'verified': False, 'status': 'FAILED', 'request_attempted': False, 'outcome_unknown': False,
                    'known_pre_submit_zero': True, 'reconciliation_required': False, 'external_writes_performed': []}
        _need(all(type(facts.get(key)) is type(value) and facts.get(key) == value for key, value in required.items()),
              'SHOPEE_ZERO_SECOND_TYPED_ZERO_REQUIRED')
        if index == 0:
            _need(set(facts) == set(required), 'SHOPEE_ZERO_SECOND_ORIGINAL_LOCAL_FAILURE_CHANGED')
        else:
            canonical = facts.get('canonical_platform_result') or {}
            targets = canonical.get('targets') or []
            _need(canonical.get('platform') == 'SHOPEE' and canonical.get('dispatch_attempted') is False
                  and canonical.get('readback_completed') is False and type(canonical.get('external_write_count')) is int
                  and canonical['external_write_count'] == 0 and len(targets) == 1
                  and targets[0].get('target_label') == label and targets[0].get('status') == 'FAILED'
                  and (targets[0].get('evidence') or {}).get('provider_code') == 'shopee_preparation_failed',
                  'SHOPEE_ZERO_SECOND_CANONICAL_PREPARATION_REQUIRED')
    for table in ('release_target_submissions', 'release_target_readbacks', 'release_target_repairs'):
        _need(db.execute('SELECT 1 FROM ' + table + ' WHERE run_id=? AND target_label=?', (RUN, label)).fetchone() is None,
              'SHOPEE_ZERO_SECOND_EXISTING_EFFECT_NO_REPLAY')
    # Shared GLOBAL mutations also preclude a claim of zero regional effects.
    for table in ('native_final_budget_reservations', 'native_final_budget_dispatches', 'native_final_budget_outcomes'):
        _need(db.execute('SELECT 1 FROM native_final_budget_scopes s JOIN ' + table + ' r USING(scope_key) '
                         'WHERE s.plan_id=? AND s.platform=?', (PLAN, 'SHOPEE')).fetchone() is None,
              'SHOPEE_ZERO_SECOND_EXISTING_BUDGET_OR_OUTCOME_NO_REPLAY')
    return row


def _second_auth_ready(active):
    """Original selected SDK inputs and exact official READ; never refresh.

    All four local contexts must be valid before the first network request.
    Missing/expired credentials or absent official merchant identity hold only
    these targets; they do not interrupt the independent TikTok continuation.
    """
    from modules.shopee.auth import token_path
    from modules.shopee.config import shopee_config
    from modules.shopee.client import shop_get
    from shared_platform.native_canonical_provider_edges import _existing_shopee_context
    from shared_platform.final_review_server_admission import ApprovalBlocked

    class CredentialUnavailable(ValueError):
        pass
    try:
        config = shopee_config()
        if config.get('partner_id') != APP_ID or config.get('environment') != 'live' or config.get('enabled') is not True:
            return False
        path = token_path().resolve(strict=True)
        raw = path.read_bytes()
        store = json.loads(raw)
        mapping = store.get('sync_shop_ids') or {}
        if any(type(mapping.get(region)) is not int or mapping[region] != shop for region, shop in EXACT_SHOPS.items()):
            return False
        contexts = {region: _existing_shopee_context(region, store) for region in EXACT_SHOPS}
        if contexts['MY'].merchant_id != 4970102:
            return False
    except (OSError, ValueError, TypeError, KeyError):
        return False

    def guard():
        # Current actor/source/scope failures remain global HOLD. Only local
        # credential availability may become the Shopee-only unavailable case.
        try:
            _protected(active)
        except (NativeMutationBudgetHold, ApprovalBlocked):
            raise
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
            # The SDK invokes this guard inside its READ error boundary too.
            # Preserve source/current failures as a global HOLD with cause.
            raise NativeMutationBudgetHold('SHOPEE_ZERO_SECOND_NATIVE_CURRENT_UNAVAILABLE') from error
        try:
            _need(path.read_bytes() == raw and shopee_config() == config, 'SHOPEE_ZERO_SECOND_AUTH_SOURCE_CHANGED')
            for region, context in contexts.items():
                _need(_existing_shopee_context(region, store) == context, 'SHOPEE_ZERO_SECOND_CONTEXT_EXPIRED_OR_CHANGED')
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise CredentialUnavailable('SHOPEE_ZERO_SECOND_CREDENTIAL_UNAVAILABLE') from error

    try:
        for region, context in contexts.items():
            guard()
            try:
                reply = shop_get('/api/v2/shop/get_shop_info', context.shop_id, context.shop_token, call_guard=guard)
            except (NativeMutationBudgetHold, ApprovalBlocked):
                raise
            except (OSError, ValueError, TypeError, KeyError, RuntimeError):
                return False
            if type(reply) is not dict or reply.get('error') not in (None, '', '-'):
                return False
            # The original signed SDK call binds the exact requested shop.
            # A present wrapper is authoritative: malformed never falls back.
            official = reply['response'] if 'response' in reply else reply
            if type(official) is not dict or 'merchant_id' not in official or 'region' not in official:
                return False
            for identity in (official,) if official is reply else (official, reply):
                if ('merchant_id' in identity
                        and (type(identity['merchant_id']) not in {str, int}
                             or str(identity['merchant_id']) != str(context.merchant_id))):
                    return False
                if 'region' in identity and identity['region'] != region:
                    return False
                if ('shop_id' in identity
                        and (type(identity['shop_id']) not in {str, int}
                             or str(identity['shop_id']) != str(EXACT_SHOPS[region]))):
                    return False
        guard()
    except CredentialUnavailable:
        return False
    return True


def activate_failed_targets(active):
    original = _activate_first_attempt(active)
    if getattr(active, 'plan_id', None) != PLAN or getattr(active, 'decision_id', None) != DECISION:
        return original
    _protected(active)
    with active.store._connect_readonly() as db:
        _approved(active, db)
        selected = [row['target_label'] for row in db.execute('SELECT * FROM release_target_runs WHERE run_id=?', (RUN,))
                    if row['target_label'] in LABELS and row['status'] == 'FAILED' and row['attempts'] == 2]
        for label in selected:
            _validate_second_zero(db, label, 'FAILED')
    if not selected or not _second_auth_ready(active):
        return original
    _protected(active)
    with active.store._transaction() as db:
        _approved(active, db)
        for label in selected:
            row = _validate_second_zero(db, label, 'FAILED')
            changed = db.execute("UPDATE release_target_runs SET status='PENDING',completed_at=NULL "
                                 "WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=2 AND error=? AND external_id IS NULL",
                                 (RUN, label, row['error']))
            _need(changed.rowcount == 1, 'SHOPEE_ZERO_SECOND_TARGET_CAS_CHANGED')
    return (*original, *selected)


def recovery_actions(active, run, actions):
    original = _first_attempt_actions(active, run, actions)
    if getattr(active, 'plan_id', None) != PLAN or getattr(active, 'decision_id', None) != DECISION:
        return original
    _protected(active)
    _need(run.get('run_id') == RUN, 'SHOPEE_ZERO_ORIGINAL_RUN_CHANGED')
    eligible = set()
    with active.store._connect_readonly() as db:
        _approved(active, db)
        for row in run['targets']:
            if row['target_label'] in LABELS and row['status'] == 'PENDING' and row['attempts'] == 2:
                _validate_second_zero(db, row['target_label'], 'PENDING')
                eligible.add(row['target_label'])
    if not eligible or not _second_auth_ready(active):
        return original
    return [{**action, 'action_kind': 'NATIVE_SHOPEE_EXPIRED_KNOWN_ZERO_CONTINUATION', 'runnable': True,
             'reason_code': 'exact_two_preserved_zero_failures_current_official_auth_ready'}
            if action.get('target_label') in eligible else action for action in original]


# Separate, exact third-failure successor. The two implementations above and
# their immutable evidence/attempt rules remain unchanged. No schema or budget.
_activate_through_second = activate_failed_targets
_actions_through_second = recovery_actions
THIRD_FAILURE_PINS = {
    'shopee:MY': '97a67985c99c3ccf7d98ff7746095b0649399e8c65d78b4eb7b4487a0d956d05',
    'shopee:PH': '90db800dfed806d65877eaa50ebdc4e0294b29a779598161e6b6f376aa5863fc',
    'shopee:TH': '1883314f4459ea6784c61b924c78c6ed56236b7c65e88aec0de15d354973aa70',
    'shopee:VN': '1d67ed7ca298b8311006b79ffc654651dd13da3c2cdf9f342893437f35dfcb1f',
}


def _third_frozen_rows():
    from pathlib import Path
    root = Path('E:/OrbitEvidence/4039931504-publication-20261019/normal15-idle-continuation-facts')
    try:
        raw = (root / 'SHOPEE_FOUR_FAILURE3.private.json').read_bytes()
        _need(hashlib.sha256(raw).hexdigest() == 'd8aed371d5ce0a13c95e9ca00a588373487da421010da9773ab2297e7cb356bb',
              'SHOPEE_ZERO_THIRD_SOURCE_FAILURES_CHANGED')
        failures = json.loads(raw)
        raw = (root / 'ORIGINAL_FIFTEEN_TARGETS.private.json').read_bytes()
        _need(hashlib.sha256(raw).hexdigest() == 'aae0d10a1d823ed3d9330e29223426a568094ff0e9b155767a98c04042a99c30',
              'SHOPEE_ZERO_THIRD_SOURCE_TARGETS_CHANGED')
        targets = [row for row in json.loads(raw) if row['target_label'] in LABELS]
        _need(len(failures) == len(targets) == 4 and {r['target_label'] for r in failures} == LABELS
              and {r['target_label'] for r in targets} == LABELS, 'SHOPEE_ZERO_THIRD_EXACT_FOUR_FACTS_REQUIRED')
        return {r['target_label']: r for r in targets}, {r['target_label']: r for r in failures}
    except NativeMutationBudgetHold:
        raise
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise NativeMutationBudgetHold('SHOPEE_ZERO_THIRD_FROZEN_FACTS_UNAVAILABLE') from error


def _validate_third_zero(db, label, status):
    _need(label in LABELS and status in {'FAILED', 'PENDING'}, 'SHOPEE_ZERO_THIRD_EXACT_TARGET_REQUIRED')
    original, third = _third_frozen_rows()
    before = original[label]
    _need(before['run_id'] == RUN and before['status'] == 'FAILED' and before['attempts'] == 3
          and before['external_id'] is None and type(before['error']) is str,
          'SHOPEE_ZERO_THIRD_ORIGINAL_ROW_REQUIRED')
    row = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?', (RUN, label)).fetchone()
    _need(row is not None and row['status'] == status and row['attempts'] == 3 and row['external_id'] is None
          and row['idempotency_key'] == before['idempotency_key'] and row['error'] == before['error'],
          'SHOPEE_ZERO_THIRD_EXACT_FAILURE_REQUIRED')
    events = db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? ORDER BY attempt', (RUN, label)).fetchall()
    _need([e['attempt'] for e in events] == [1, 2, 3] and dict(events[-1]) == third[label],
          'SHOPEE_ZERO_THIRD_THREE_RETAINED_FAILURES_REQUIRED')
    pins = (*FAILURE_PINS[label], THIRD_FAILURE_PINS[label])
    for index, event in enumerate(events):
        raw = event['evidence_json']
        _need(hashlib.sha256(raw.encode()).hexdigest() == event['evidence_digest'] == pins[index],
              'SHOPEE_ZERO_THIRD_IMMUTABLE_FAILURE_CHANGED')
        facts = json.loads(raw)
        required = {'schema_version': 'native-scoped-v4-target-result/v1', 'target_label': label,
                    'verified': False, 'status': 'FAILED', 'request_attempted': False, 'outcome_unknown': False,
                    'known_pre_submit_zero': True, 'reconciliation_required': False, 'external_writes_performed': []}
        _need(all(type(facts.get(k)) is type(v) and facts.get(k) == v for k, v in required.items()),
              'SHOPEE_ZERO_THIRD_TYPED_ZERO_REQUIRED')
        if index == 0:
            _need(set(facts) == set(required), 'SHOPEE_ZERO_THIRD_FIRST_FAILURE_CHANGED')
        else:
            canonical = facts.get('canonical_platform_result') or {}
            targets = canonical.get('targets') or []
            _need(canonical.get('platform') == 'SHOPEE' and canonical.get('dispatch_attempted') is False
                  and canonical.get('readback_completed') is False and type(canonical.get('external_write_count')) is int
                  and canonical['external_write_count'] == 0 and len(targets) == 1
                  and targets[0].get('target_label') == label and targets[0].get('status') == 'FAILED'
                  and (targets[0].get('evidence') or {}).get('request_attempted') is False
                  and (targets[0].get('evidence') or {}).get('outcome_unknown') is False,
                  'SHOPEE_ZERO_THIRD_CANONICAL_ZERO_REQUIRED')
    for table in ('release_target_submissions', 'release_target_readbacks', 'release_target_repairs'):
        _need(db.execute('SELECT 1 FROM ' + table + ' WHERE run_id=? AND target_label=?', (RUN, label)).fetchone() is None,
              'SHOPEE_ZERO_THIRD_EXTERNAL_EFFECT_NO_REPLAY')
    for table in ('native_final_budget_reservations', 'native_final_budget_dispatches', 'native_final_budget_outcomes'):
        _need(db.execute('SELECT 1 FROM native_final_budget_scopes s JOIN ' + table + ' r USING(scope_key) '
                         'WHERE s.plan_id=? AND s.platform=?', (PLAN, 'SHOPEE')).fetchone() is None,
              'SHOPEE_ZERO_THIRD_ANY_SHARED_REGIONAL_ATTEMPT_NO_REPLAY')
    return row


def activate_failed_targets(active):
    original = _activate_through_second(active)
    if getattr(active, 'plan_id', None) != PLAN or getattr(active, 'decision_id', None) != DECISION:
        return original
    _protected(active)
    with active.store._connect_readonly() as db:
        _approved(active, db)
        selected = [r['target_label'] for r in db.execute('SELECT * FROM release_target_runs WHERE run_id=?', (RUN,))
                    if r['target_label'] in LABELS and r['status'] == 'FAILED' and r['attempts'] == 3]
        for label in selected:
            _validate_third_zero(db, label, 'FAILED')
    if not selected or not _second_auth_ready(active):
        return original
    _protected(active)
    with active.store._transaction() as db:
        _approved(active, db)
        for label in selected:
            row = _validate_third_zero(db, label, 'FAILED')
            changed = db.execute("UPDATE release_target_runs SET status='PENDING',completed_at=NULL "
                                 "WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=3 AND external_id IS NULL AND error=? AND idempotency_key=?",
                                 (RUN, label, row['error'], row['idempotency_key']))
            _need(changed.rowcount == 1, 'SHOPEE_ZERO_THIRD_CAS_CHANGED')
    return (*original, *selected)


def recovery_actions(active, run, actions):
    original = _actions_through_second(active, run, actions)
    if getattr(active, 'plan_id', None) != PLAN or getattr(active, 'decision_id', None) != DECISION:
        return original
    _protected(active)
    _need(run.get('run_id') == RUN, 'SHOPEE_ZERO_THIRD_RUN_CHANGED')
    eligible = set()
    with active.store._connect_readonly() as db:
        _approved(active, db)
        for row in run['targets']:
            if row['target_label'] in LABELS and row['status'] == 'PENDING' and row['attempts'] == 3:
                _validate_third_zero(db, row['target_label'], 'PENDING')
                eligible.add(row['target_label'])
    if not eligible or not _second_auth_ready(active):
        return original
    return [{**a, 'runnable': True, 'action_kind': 'NATIVE_SHOPEE_EXACT_THIRD_KNOWN_ZERO_CONTINUATION',
             'reason_code': 'exact_three_preserved_zero_failures_current_official_auth_ready'}
            if a.get('target_label') in eligible else a for a in original]


# The following exact successor leaves all prior attempts/branches immutable.
_activate_through_third = activate_failed_targets
_actions_through_third = recovery_actions
FOURTH_FACT_PINS = {
 'shopee:MY': ('95faebee0b27bdcb1c99640a66080bbae0ff7d546d57694bdc536d599ebed42b','528e0008993957f5aa79e2032b8d9b27c2abe42163e997116fd24e270ba93870'),
 'shopee:PH': ('2b9e62a18e7d1e8ff50c95919d481715ae69abce3a528fa2030bac39cd7a584c','b5326e337ecf996d1ce13114b4fb2b8c972928406d3ce02f823c5833c881882a'),
 'shopee:TH': ('45d9fd3afc8024dfc0bd4ee86a57ac28fc20eeef0085e901a765db457a2e1442','96ad18c1e0b627e6731bea92974ad5a61bcf047e4f355743ccd852c13ae7a5cd'),
 'shopee:VN': ('5aa04cbca7f0bd1aa0eab6108171a97d16ea73f5532163f41aeb2341aa47bb43','34928d0318d6b2c21840e4b37c12f591747455ecd0c2e13084539c7bd2852dc3'),
}

def _fourth_frozen(label):
    from pathlib import Path
    _need(label in LABELS,'SHOPEE_ZERO_FOURTH_EXACT_TARGET_REQUIRED')
    path=Path('E:/OrbitEvidence/4039931504-publication-20261019/actual-monitor')/('NORMAL16_'+label.split(':')[1]+'_EVIDENCE_001.private.json')
    raw=path.read_bytes()
    _need(hashlib.sha256(raw).hexdigest()==FOURTH_FACT_PINS[label][0],'SHOPEE_ZERO_FOURTH_SOURCE_FACTS_CHANGED')
    value=json.loads(raw);rows=value['rows']
    _need(value['target']==label and len(rows['release_target_runs'])==1
          and rows['release_target_submissions']==[] and rows['release_target_readbacks']==[],
          'SHOPEE_ZERO_FOURTH_ORIGINAL_FACTS_REQUIRED')
    return rows

def _validate_fourth_zero(db,label,status):
    _need(status in {'FAILED','PENDING'},'SHOPEE_ZERO_FOURTH_STATE_REQUIRED')
    source=_fourth_frozen(label);before=source['release_target_runs'][0]
    _need(before['run_id']==RUN and before['target_label']==label and before['status']=='FAILED'
          and before['attempts']==4 and before['external_id'] is None,'SHOPEE_ZERO_FOURTH_ORIGINAL_ROW_REQUIRED')
    row=db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',(RUN,label)).fetchone()
    _need(row and (row['status'],row['attempts'],row['external_id'],row['idempotency_key'],row['error'])
          ==(status,4,None,before['idempotency_key'],before['error']),'SHOPEE_ZERO_FOURTH_CURRENT_ROW_CHANGED')
    events=[dict(r) for r in db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? ORDER BY attempt',(RUN,label))]
    _need(events==source['release_target_failure_events'] and [r['attempt'] for r in events]==[1,2,3,4],
          'SHOPEE_ZERO_FOURTH_FOUR_RETAINED_FAILURES_REQUIRED')
    pins=(*FAILURE_PINS[label],THIRD_FAILURE_PINS[label],FOURTH_FACT_PINS[label][1])
    for index,event in enumerate(events):
        _need(hashlib.sha256(event['evidence_json'].encode()).hexdigest()==event['evidence_digest']==pins[index],
              'SHOPEE_ZERO_FOURTH_IMMUTABLE_FAILURE_CHANGED')
        proof=json.loads(event['evidence_json'])
        required={'schema_version':'native-scoped-v4-target-result/v1','target_label':label,'verified':False,
                  'status':'FAILED','request_attempted':False,'outcome_unknown':False,'known_pre_submit_zero':True,
                  'reconciliation_required':False,'external_writes_performed':[]}
        _need(all(type(proof.get(k)) is type(v) and proof.get(k)==v for k,v in required.items()),
              'SHOPEE_ZERO_FOURTH_TYPED_ZERO_REQUIRED')
        if index==0:_need(set(proof)==set(required),'SHOPEE_ZERO_FOURTH_FIRST_FAILURE_CHANGED')
        else:
            canonical=proof.get('canonical_platform_result') or {};targets=canonical.get('targets') or []
            _need(canonical.get('platform')=='SHOPEE' and canonical.get('dispatch_attempted') is False
                  and canonical.get('readback_completed') is False and type(canonical.get('external_write_count')) is int
                  and canonical['external_write_count']==0 and len(targets)==1 and targets[0].get('target_label')==label
                  and targets[0].get('status')=='FAILED' and (targets[0].get('evidence') or {}).get('request_attempted') is False
                  and (targets[0].get('evidence') or {}).get('outcome_unknown') is False,'SHOPEE_ZERO_FOURTH_CANONICAL_ZERO_REQUIRED')
            if index==3:
                evidence=targets[0]['evidence']
                _need(evidence.get('provider_code')=='shopee_category_preparation_failed'
                      and evidence.get('provider_reason')=='Shopee category preparation failed before mutation [phase=GLOBAL_RESOLVER; exception=ShopeeGlobalV4Error]',
                      'SHOPEE_ZERO_FOURTH_EXACT_RESOLVER_FAILURE_REQUIRED')
    for table in ('release_target_submissions','release_target_readbacks','release_target_repairs'):
        _need(db.execute('SELECT 1 FROM '+table+' WHERE run_id=? AND target_label=?',(RUN,label)).fetchone() is None,
              'SHOPEE_ZERO_FOURTH_EXISTING_EFFECT_NO_REPLAY')
    for table in ('native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes'):
        _need(db.execute('SELECT 1 FROM native_final_budget_scopes s JOIN '+table+' r USING(scope_key) WHERE s.plan_id=? AND s.platform=?',(PLAN,'SHOPEE')).fetchone() is None,
              'SHOPEE_ZERO_FOURTH_ANY_SHARED_REGIONAL_ATTEMPT_NO_REPLAY')
    return row

def activate_failed_targets(active):
    original=_activate_through_third(active)
    if getattr(active,'plan_id',None)!=PLAN or getattr(active,'decision_id',None)!=DECISION:return original
    _protected(active)
    with active.store._connect_readonly() as db:
        _approved(active,db)
        selected=[r['target_label'] for r in db.execute('SELECT * FROM release_target_runs WHERE run_id=?',(RUN,))
                  if r['target_label'] in LABELS and r['status']=='FAILED' and r['attempts']==4]
        for label in selected:_validate_fourth_zero(db,label,'FAILED')
    if not selected or not _second_auth_ready(active):return original
    _protected(active)
    with active.store._transaction() as db:
        _approved(active,db)
        for label in selected:
            row=_validate_fourth_zero(db,label,'FAILED')
            changed=db.execute("UPDATE release_target_runs SET status='PENDING',completed_at=NULL WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=4 AND external_id IS NULL AND error=? AND idempotency_key=?",(RUN,label,row['error'],row['idempotency_key']))
            _need(changed.rowcount==1,'SHOPEE_ZERO_FOURTH_CAS_CHANGED')
    return (*original,*selected)

def recovery_actions(active,run,actions):
    original=_actions_through_third(active,run,actions)
    if getattr(active,'plan_id',None)!=PLAN or getattr(active,'decision_id',None)!=DECISION:return original
    _protected(active);_need(run.get('run_id')==RUN,'SHOPEE_ZERO_FOURTH_RUN_CHANGED');eligible=set()
    with active.store._connect_readonly() as db:
        _approved(active,db)
        for row in run['targets']:
            if row['target_label'] in LABELS and row['status']=='PENDING' and row['attempts']==4:
                _validate_fourth_zero(db,row['target_label'],'PENDING');eligible.add(row['target_label'])
    if not eligible or not _second_auth_ready(active):return original
    return [{**a,'runnable':True,'action_kind':'NATIVE_SHOPEE_EXACT_FOURTH_KNOWN_ZERO_CONTINUATION',
             'reason_code':'four_preserved_zero_failures_current_official_auth_ready'} if a.get('target_label') in eligible else a for a in original]

EXISTING_SOURCE_NAMES = ('shared_platform/native_tiktok_core_save_before_publish.py', 'shared_platform/native_accepted_save_core_repair.py', 'shared_platform/product_publication_executors.py', 'shared_platform/native_final_mutation_budget.py', 'shared_platform/native_canonical_provider_edges.py', 'shared_platform/native_prepared_v4_targets.py', 'shared_platform/native_shopee_known_zero_recovery.py', 'modules/miaoshou/tiktok_v4_drafts.py', 'modules/products/server.py', 'shared_platform/native_ozon_existing_media_recovery.py', 'modules/shopee/global_copy.py', 'shared_platform/native_tiktok_exact_readback.py', 'modules/shopee/global_v4_live_runtime.py', 'modules/shopee/global_v4_executor.py')

# Exact accepted-create continuation. This is deliberately separate from every
# known-zero branch above: these targets already have a provider side effect.
from pathlib import Path
import re
EXISTING_ROOT = Path('E:/OrbitEvidence/4039931504-publication-20261019/ROOT_SHOPEE_EXISTING_PH_TH_CONTINUATION_NORMAL19.json')
EXISTING_TARGETS = {
    'shopee:PH': (1527371343, 45569618248, 336640048710, 14, 'update_description_media'),
    'shopee:TH': (1561124013, 48919616789, 381640058626, 15, 'update_regional_copy'),
}

def _existing_root():
    if not EXISTING_ROOT.is_file():return None
    from shared_platform.native_known_rejected_save_repair import _read_reference
    root=json.loads(EXISTING_ROOT.read_bytes())
    _need(root.get('schema')=='root-exact-shopee-existing-PH-TH-continuation/v1'
          and root.get('status')=='LIVE_ADOPTION_APPROVED'
          and (root.get('plan_id'),root.get('run_id'),root.get('decision_id'),root.get('snapshot_digest'))==(PLAN,RUN,DECISION,SNAPSHOT)
          and root.get('global_item_id')==52619584366 and root.get('partner_id')==2036649 and root.get('merchant_id')==4970102
          and set(root.get('targets') or {})==set(EXISTING_TARGETS),'SHOPEE_EXISTING_EXACT_ROOT_REQUIRED')
    pins=json.loads(_read_reference(root['source_pins_ref']));app=Path(__file__).resolve().parents[1]
    _need(set(pins)==set(EXISTING_SOURCE_NAMES),'SHOPEE_EXISTING_SOURCE_SET_CHANGED')
    for name,row in pins.items():
        path=app/name
        _need(path.resolve().is_relative_to(app) and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==row['after_sha256'],'SHOPEE_EXISTING_SOURCE_CHANGED')
    for label,(shop,item,model,sequence,operation) in EXISTING_TARGETS.items():
        entry=root['targets'][label]
        _need((entry.get('shop_id'),entry.get('item_id'),entry.get('model_id'),entry.get('model_sku'),entry.get('create_sequence'))==(shop,item,model,'0989',sequence)
              and type(entry.get('create_request_sha256')) is str
              and re.fullmatch('[0-9a-f]{64}',entry['create_request_sha256']) is not None
              and entry.get('allowed_operations')==[operation,'list_regional_item']
              and entry.get('maximum_new_technical_writes')==2,'SHOPEE_EXISTING_EXACT_TARGET_REQUIRED')
        observed=json.loads(_read_reference(entry['model_observation_ref']))
        models=json.loads(_read_reference(entry['model_response_ref']))
        base=json.loads(_read_reference(entry['base_response_ref']))
        relationships=json.loads(_read_reference(entry['relationships_ref']))
        _need((observed.get('target_label'),observed.get('shop_id'),observed.get('item_id'),observed.get('global_item_id'))
              ==(label,shop,item,52619584366)
              and Path(observed['official_response_ref']['path']).resolve()==Path(entry['model_response_ref']['path']).resolve()
              and observed['official_response_ref']['sha256']==entry['model_response_ref']['sha256']
              and models.get('error')==base.get('error')==relationships.get('error')=='',
              'SHOPEE_EXISTING_OFFICIAL_EVIDENCE_CHANGED')
        model_rows=models.get('response',{}).get('model') or []
        item_rows=base.get('response',{}).get('item_list') or []
        joined=[r for r in relationships.get('response',{}).get('published_item') or []
                if r.get('shop_id')==shop and r.get('item_id')==item]
        _need(len(model_rows)==1 and model_rows[0].get('model_sku')=='0989'
              and model_rows[0].get('model_id')==model and observed.get('all_models')==model_rows
              and len(item_rows)==1 and item_rows[0].get('item_id')==item and len(joined)==1,
              'SHOPEE_EXISTING_OFFICIAL_ITEM_MODEL_JOIN_CHANGED')
    return root

def _existing_root_unchanged(root):
    _need(_existing_root()==root,'SHOPEE_EXISTING_ROOT_CHANGED')

def _existing_state(db,root,label,*,status,attempt,allow_pending=False):
    from shared_platform.native_known_rejected_save_repair import _read_reference,_base_rows
    entry=root['targets'][label];expected=json.loads(_read_reference(entry['original_target_ref']))
    failures=json.loads(_read_reference(entry['original_failures_ref']));original=json.loads(_read_reference(entry['original_accepted_rows_ref']))
    _need(expected['run_id']==RUN and expected['target_label']==label and expected['status']=='FAILED' and expected['attempts']==5
          and expected['external_id'] is None and bool(expected['idempotency_key']),'SHOPEE_EXISTING_ORIGINAL_FAILED5_REQUIRED')
    row=db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',(RUN,label)).fetchone()
    _need(row is not None and row['status']==status and row['attempts']==attempt and row['external_id'] is None
          and row['idempotency_key']==expected['idempotency_key'],'SHOPEE_EXISTING_CURRENT_TARGET_CHANGED')
    if status=='FAILED':_need(dict(row)==expected,'SHOPEE_EXISTING_FAILED_ROW_CHANGED')
    current=[dict(r) for r in db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? ORDER BY attempt',(RUN,label))]
    _need(current==failures and [r['attempt'] for r in failures]==[1,2,3,4,5]
          and all(hashlib.sha256(r['evidence_json'].encode()).hexdigest()==r['evidence_digest'] for r in failures),'SHOPEE_EXISTING_FIVE_FAILURES_REQUIRED')
    last=json.loads(failures[-1]['evidence_json'])
    _need(last.get('target_label')==label and last.get('request_attempted') is True and last.get('known_pre_submit_zero') is False
          and last.get('status')=='PROCESSING','SHOPEE_EXISTING_ACCEPTED_CREATE_FAILURE_REQUIRED')
    scope=db.execute('SELECT * FROM native_final_budget_scopes WHERE scope_key=?',(entry['scope_key'],)).fetchone()
    _need(scope is not None and hashlib.sha256(scope['binding_json'].encode()).hexdigest()==scope['binding_sha256'],'SHOPEE_EXISTING_SCOPE_CHANGED')
    binding=json.loads(scope['binding_json'])
    _need(dict(scope)==original['scope'] and binding==original['binding'],'SHOPEE_EXISTING_ORIGINAL_SCOPE_CHANGED')
    _need((binding['plan_id'],binding['snapshot_digest'],binding['decision_id'],binding['platform'],binding['target_labels'],binding['per_target_maximum'])
          ==(PLAN,SNAPSHOT,DECISION,'SHOPEE',['shopee:PH','shopee:MY','shopee:TH','shopee:VN'],9)
          and binding['shared_maximum']==10,'SHOPEE_EXISTING_ORIGINAL_BUDGET_CHANGED')
    rows=_base_rows(db,entry['scope_key'],label)
    shared=db.execute('''SELECT d.request_digest,coalesce(o.outcome,r.initial_outcome) outcome
        FROM native_final_budget_reservations r LEFT JOIN native_final_budget_dispatches d USING(scope_key,sequence)
        LEFT JOIN native_final_budget_outcomes o USING(scope_key,sequence)
        WHERE r.scope_key=? AND r.budget_scope='shared' ''',(entry['scope_key'],)).fetchall()
    _need(all(r['outcome'] in {'ACCEPTED','REJECTED'} and r['request_digest'] for r in shared),
          'SHOPEE_EXISTING_SHARED_UNKNOWN_NO_REPLAY')
    own=lambda values:[r for r in values if r['budget_scope']=='target']
    original_rows=[r for r in original['rows'] if r['reservation']['target_label']==label]
    _need(len(original_rows)==1,'SHOPEE_EXISTING_ORIGINAL_ACCEPTED_ROWS_CHANGED')
    record=original_rows[0];reservation=record['reservation'];dispatch=record['dispatch'];outcome=record['outcome']
    baseline={k:reservation[k] for k in ('sequence','target_label','budget_scope','operation','attempt_count')}
    baseline.update(request_digest=dispatch['request_digest'],outcome=outcome['outcome'])
    current_own=own(rows)
    _need(reservation['scope_key']==dispatch['scope_key']==outcome['scope_key']==entry['scope_key']
          and reservation['sequence']==dispatch['sequence']==outcome['sequence']==entry['create_sequence']
          and baseline['target_label']==label and baseline['budget_scope']=='target'
          and baseline['operation']=='create_publish_task' and baseline['attempt_count']==1
          and baseline['outcome']=='ACCEPTED' and baseline['request_digest']==entry['create_request_sha256']
          and all(r['outcome'] in {'ACCEPTED','REJECTED'} and r['request_digest'] for r in rows if r['budget_scope']=='shared')
          and current_own[:1]==[baseline] and len(current_own)<=3,'SHOPEE_EXISTING_ORIGINAL_ACCEPTED_ROWS_CHANGED')
    extra=current_own[1:];ops=[r['operation'] for r in extra];primary=EXISTING_TARGETS[label][4]
    _need(ops in ([],[primary],['list_regional_item'],[primary,'list_regional_item'])
          and all(r['target_label']==label and r['attempt_count']==1 for r in extra),'SHOPEE_EXISTING_EXTRA_OR_REPEATED_OPERATION')
    for index,r in enumerate(extra):
        pending=allow_pending and index==len(extra)-1 and r['request_digest'] is None and r['outcome']=='UNKNOWN'
        _need(pending or (r['outcome']=='ACCEPTED' and bool(r['request_digest'])),'SHOPEE_EXISTING_UNKNOWN_NO_REPLAY')
    if status!='RUNNING':_need(not extra,'SHOPEE_EXISTING_ALREADY_STARTED_NO_REPLAY')
    for table in ('release_target_submissions','release_target_readbacks','release_target_repairs'):
        _need(db.execute('SELECT 1 FROM '+table+' WHERE run_id=? AND target_label=?',(RUN,label)).fetchone() is None,'SHOPEE_EXISTING_TERMINAL_EFFECT_EXISTS')
    return row,entry,current_own

_activate_before_existing=activate_failed_targets
_actions_before_existing=recovery_actions

def has_existing_item_work(store,decision_id):
    """Read-only FIFO hint after submit's actor/decision recheck, never authority.

    The actual CAS, reservation and SDK calls still require the original
    protected execution inside the original server lock.
    """
    if decision_id!=DECISION:return False
    with store._connect_readonly() as db:
        labels=[r['target_label'] for r in db.execute('SELECT * FROM release_target_runs WHERE run_id=?',(RUN,))
                if r['target_label'] in EXISTING_TARGETS and r['status']=='FAILED' and r['attempts']==5]
        if not labels:return False
        root=_existing_root()
        if root is None:return False
        for label in labels:_existing_state(db,root,label,status='FAILED',attempt=5)
    return bool(labels)

def _existing_auth_ready(active,labels):
    _protected(active)
    from modules.shopee.auth import token_path
    from modules.shopee.config import shopee_config
    from shared_platform.native_canonical_provider_edges import _existing_shopee_context
    from shared_platform.final_review_server_admission import ApprovalBlocked
    try:
        config=shopee_config();_need(config.get('partner_id')==2036649,'SHOPEE_EXISTING_APP_CHANGED')
        stored=json.loads(token_path().read_bytes())
        for label in labels:
            ctx=_existing_shopee_context(label.split(':')[1],stored)
            _need(ctx.shop_id==EXISTING_TARGETS[label][0] and ctx.merchant_id==4970102,'SHOPEE_EXISTING_AUTH_CONTEXT_CHANGED')
    except (NativeMutationBudgetHold,ApprovalBlocked):raise
    except (ValueError,OSError,KeyError,TypeError):return False
    return True

def activate_failed_targets(active):
    prior=_activate_before_existing(active)
    if getattr(active,'plan_id',None)!=PLAN or getattr(active,'decision_id',None)!=DECISION:return prior
    _protected(active)
    with active.store._connect_readonly() as db:
        _approved(active,db)
        labels=[r['target_label'] for r in db.execute('SELECT * FROM release_target_runs WHERE run_id=?',(RUN,))
                if r['target_label'] in EXISTING_TARGETS and r['status']=='FAILED' and r['attempts']==5]
        if not labels:return prior
        root=_existing_root()
        if root is None:return prior
        for label in labels:_existing_state(db,root,label,status='FAILED',attempt=5)
    if not _existing_auth_ready(active,labels):return prior
    _protected(active)
    with active.store._transaction() as db:
        _existing_root_unchanged(root)
        _approved(active,db)
        for label in labels:
            row,_,_=_existing_state(db,root,label,status='FAILED',attempt=5)
            changed=db.execute("UPDATE release_target_runs SET status='PENDING',completed_at=NULL WHERE run_id=? AND target_label=? AND status='FAILED' AND attempts=5 AND idempotency_key=? AND external_id IS NULL",(RUN,label,row['idempotency_key']))
            _need(changed.rowcount==1,'SHOPEE_EXISTING_FAILED5_CAS_CHANGED')
    return (*prior,*labels)

def recovery_actions(active,run,actions):
    prior=_actions_before_existing(active,run,actions)
    if getattr(active,'plan_id',None)!=PLAN or getattr(active,'decision_id',None)!=DECISION:return prior
    _protected(active);_need(run.get('run_id')==RUN,'SHOPEE_EXISTING_RUN_CHANGED');labels=[]
    if not any(r['target_label'] in EXISTING_TARGETS and r['status']=='PENDING' and r['attempts']==5 for r in run['targets']):return prior
    root=_existing_root()
    if root is None:return prior
    with active.store._connect_readonly() as db:
        _approved(active,db)
        for row in run['targets']:
            if row['target_label'] in EXISTING_TARGETS and row['status']=='PENDING' and row['attempts']==5:
                _existing_state(db,root,row['target_label'],status='PENDING',attempt=5);labels.append(row['target_label'])
    if not labels or not _existing_auth_ready(active,labels):return prior
    return [{**action,'runnable':True,'action_kind':'NATIVE_SHOPEE_EXACT_EXISTING_ITEM_CONTINUATION',
             'reason_code':'original_accepted_create_exact_existing_item_remaining_budget'} if action.get('target_label') in labels else action for action in prior]

def existing_item_executor(active,ledger,references,dependencies):
    label=ledger.target_label
    if label not in EXISTING_TARGETS:return None
    root=_existing_root()
    if root is None:return None
    _protected(active)
    with active.store._connect_readonly() as db:
        _approved(active,db);_,entry,_=_existing_state(db,root,label,status='RUNNING',attempt=6)
    from copy import deepcopy
    from modules.shopee.skill_regions import (readback_dispatched_regions,_approved_copy,_approved_models,_exact_global_tiers,
        _official_readback_checks,_expected_category_id)
    from shared_platform.product_publication_executors import _shopee_result
    from shared_platform.product_description_media import validate_description_media_preflight
    from shared_platform.shopee_publication_checkpoint import ShopeePublicationCheckpointStore
    base=dependencies.runtime;shop,item,model,sequence,primary=EXISTING_TARGETS[label]
    region=label.split(':')[1];global_id='52619584366'
    class Existing(type(base)):
        def context(self,selected):
            _need(selected==region,'SHOPEE_EXISTING_CONTEXT_CHANGED');ctx=super().context(selected)
            _need(ctx.shop_id==shop and ctx.merchant_id==4970102,'SHOPEE_EXISTING_CONTEXT_CHANGED');return ctx
        def regional_item(self,ctx,iid):
            _need(ctx.shop_id==shop and str(iid)==str(item),'SHOPEE_EXISTING_ITEM_CHANGED');row=super().regional_item(ctx,iid)
            _need(str(row.get('item_id'))==str(item) and tuple(row.get('image',{}).get('image_id_list') or [])==gallery,'SHOPEE_EXISTING_GALLERY_OR_ITEM_CHANGED');return row
        def regional_models(self,ctx,iid):
            _need(ctx.shop_id==shop and str(iid)==str(item),'SHOPEE_EXISTING_ITEM_CHANGED');rows=super().regional_models(ctx,iid)
            _need(len(rows)==1 and rows[0].get('model_sku')=='0989' and str(rows[0].get('model_id'))==str(model),'SHOPEE_EXISTING_MODEL_CHANGED')
            row=rows[0];stock=row.get('stock_info_v2') or {};seller=stock.get('seller_stock') or []
            _need(stock.get('summary_info',{}).get('total_available_stock')==200
                  and len([r for r in seller if r.get('location_id')=='CNZ' and r.get('stock')==200])==1
                  and all(r.get('stock')==0 for r in seller if r.get('location_id')!='CNZ')
                  and str(row.get('weight'))=='0.2' and row.get('dimension')=={'package_length':20,'package_width':12,'package_height':6}
                  and row.get('model_status')=='MODEL_NORMAL','SHOPEE_EXISTING_STOCK_OR_PARCEL_CHANGED');return rows
        def resolved_global_item_id(self,ctx,iid):
            value=super().resolved_global_item_id(ctx,iid);_need(str(value)==global_id,'SHOPEE_EXISTING_GLOBAL_CHANGED');return value
        def update_regional_copy(self,ctx,iid,*,title,description):
            _need(primary=='update_regional_copy' and str(iid)==str(item) and (title,description)==copy,'SHOPEE_EXISTING_COPY_WIRE_CHANGED')
            return super().update_regional_copy(ctx,iid,title=title,description=description)
        def update_regional_description(self,ctx,iid,*,description,image_ids):
            _need(primary=='update_description_media' and str(iid)==str(item) and description==copy[1] and tuple(image_ids)==gallery,'SHOPEE_EXISTING_DESCRIPTION_WIRE_CHANGED')
            return super().update_regional_description(ctx,iid,description=description,image_ids=image_ids)
        def list_item(self,ctx,iid):
            _need(str(iid)==str(item) and ctx.shop_id==shop,'SHOPEE_EXISTING_LIST_WIRE_CHANGED');return super().list_item(ctx,iid)
        def create_publish_task(self,*a,**k):raise NativeMutationBudgetHold('SHOPEE_EXISTING_CREATE_FORBIDDEN')
        def upload_regional_images(self,*a,**k):raise NativeMutationBudgetHold('SHOPEE_EXISTING_UPLOAD_FORBIDDEN')
        def update_regional_images(self,*a,**k):raise NativeMutationBudgetHold('SHOPEE_EXISTING_GALLERY_WRITE_FORBIDDEN')
        def enable_applicable_logistics(self,*a,**k):raise NativeMutationBudgetHold('SHOPEE_EXISTING_LOGISTICS_WRITE_FORBIDDEN')
    runtime=object.__new__(Existing);runtime.__dict__.update(base.__dict__)
    gallery=();copy=();request_snapshot=None;selected_logistics=[];expected_tiers={}
    def before(target,operation):
        _need(target==label and operation in (primary,'list_regional_item'),'SHOPEE_EXISTING_OPERATION_FORBIDDEN')
        ctx=runtime.context(region);current=runtime.regional_item(ctx,str(item));models=runtime.regional_models(ctx,str(item));runtime.resolved_global_item_id(ctx,str(item))
        enabled=sorted(r['logistic_id'] for r in current.get('logistic_info') or [] if r.get('enabled') is True)
        _need(enabled==sorted(selected_logistics),'SHOPEE_EXISTING_CURRENT_LOGISTICS_CHANGED')
        checks=_official_readback_checks(item=current,models=models,resolved_global_item_id=global_id,expected_global_item_id=global_id,
            expected_models=_approved_models(request_snapshot,label),expected_tiers=expected_tiers,expected_logistics=selected_logistics,
            expected_image_count=8,expected_item_sku='0989',expected_category_id=_expected_category_id(request_snapshot,label),
            expected_title=copy[0],expected_description=copy[1],expected_region=region,repaired_copy=None,expected_image_ids=gallery,
            expected_description_image_ids=gallery,item_id=str(item))
        ignored={'normal_status_exact','copy_exact','description_images_exact','description_type_extended'} if operation==primary else {'normal_status_exact'}
        _need(all(value for key,value in checks.items() if key not in ignored),'SHOPEE_EXISTING_PREWRITE_FACT_CHANGED')
        _protected(active)
        _existing_root_unchanged(root)
        with active.store._connect_readonly() as db:
            _approved(active,db);_,_,own=_existing_state(db,root,label,status='RUNNING',attempt=6)
            _need(operation not in [r['operation'] for r in own] and len(own)<3,'SHOPEE_EXISTING_SLOT_ALREADY_USED')
        ledger.reserve_target(label,operation)
    def execute(request):
        nonlocal gallery,copy,request_snapshot,selected_logistics,expected_tiers
        _existing_root_unchanged(root)
        _need(request.platform=='SHOPEE' and request.target_labels==(label,) and request.write_budget_ledger is ledger,'SHOPEE_EXISTING_REQUEST_CHANGED')
        validate_description_media_preflight(request.snapshot,platform='SHOPEE',target_labels=request.target_labels)
        request_snapshot=request.snapshot;copy=_approved_copy(request.snapshot,label)
        from shared_platform.native_known_rejected_save_repair import _read_reference
        cp=json.loads(_read_reference(root['global_checkpoint_ref']))
        gallery=tuple(cp['image_bindings'][u] for u in request.snapshot['product']['image_routing']['routes'][label]['ordered_images'])
        _need(str(cp.get('global_item_id'))==global_id and len(gallery)==len(set(gallery))==8
              and cp.get('offer_id')=='4039931504' and cp.get('product_revision')==2
              and cp.get('run_id')==request.run_id and cp.get('report_id')==request.report_id
              and cp.get('model_skus')==['0989'],'SHOPEE_EXISTING_APPROVED_GALLERY_CHANGED')
        ctx=runtime.context(region);expected_tiers=_exact_global_tiers(runtime.global_models(ctx,global_id),_approved_models(request.snapshot,label))
        selected_logistics=entry['current_enabled_logistics_ids']
        _need(type(selected_logistics) is list and selected_logistics and all(type(v) is int and v>0 for v in selected_logistics),'SHOPEE_EXISTING_ORIGINAL_LOGISTICS_REQUIRED')
        dispatch={'targets':[{'target_label':label,'accepted':True,'attempted':False,'outcome':'ACCEPTED','existing_item_id':str(item),
                             'shop_id':shop,'selected_logistics_ids':selected_logistics,'external_write_count':0}]}
        checkpoint=ShopeePublicationCheckpointStore(request.checkpoint_root,request)
        readback=readback_dispatched_regions(request.snapshot,dispatch,global_item_id=global_id,runtime=runtime,poll_attempts=1,
            before_mutation=before,on_target_result=checkpoint.record,target_scope=request.target_labels)
        result=_shopee_result(dispatch,readback,labels=request.target_labels,readback_completed=True,include_target_evidence=True)
        from shared_platform.native_known_rejected_save_repair import _base_rows
        with active.store._connect_readonly() as db:
            own=[r for r in _base_rows(db,entry['scope_key'],label) if r['budget_scope']=='target']
        result['dispatch_attempted']=any(r['request_digest'] for r in own[1:])
        return result
    return execute
