"""Accept an observed existing promotion price without replaying its attempt.

Only original OneClick events and job business projection change. Original
commands, outcomes, target status and open write intents remain immutable facts.
This module owns no provider, credential, HTTP or schema capability.
"""
from decimal import Decimal
import hashlib
import json
from pathlib import Path

EVENT = 'PROMOTION_EXISTING_PRICE_VARIANCE_ACCEPTED'
SCHEMA = 'oneclick-existing-promotion-price-acceptance/v1'


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _require(ok, code):
    if not ok:
        from shared_platform.oneclick_release_controlplane import SystemicIdentityError
        raise SystemicIdentityError(code)


def _pin(ref):
    from shared_platform.immutable_approval_files import require_local_path
    _require(type(ref) is dict and set(ref) == {'path', 'sha256'}, 'PRICE_ACCEPTANCE_PIN_REQUIRED')
    path = Path(ref['path']); require_local_path(path, root=path.parent)
    raw = path.read_bytes()
    _require(len(raw) <= 2_000_000 and _sha(raw) == ref['sha256'], 'PRICE_ACCEPTANCE_PIN_CHANGED')
    return json.loads(raw)


def _fingerprint(row):
    return _sha(_bytes({key: row[key] for key in ('job_id', 'target_label', 'status', 'dispatch_count',
        'command_digest', 'proof_digest', 'result_json', 'pending_write_intent_digest',
        'cumulative_external_write_lower_bound', 'cumulative_external_write_upper_bound')}))


def accepted_existing_price(db, row):
    """Read one exact append-only acceptance; never reinterpret the old result."""
    events = db.execute('SELECT event_json,event_digest FROM oneclick_release_events WHERE job_id=? '
        'AND target_label=? AND event_type=?', (row['job_id'], row['target_label'], EVENT)).fetchall()
    if not events:
        return None
    _require(len(events) == 1, 'PRICE_ACCEPTANCE_EVENT_AMBIGUOUS')
    value = json.loads(events[0]['event_json'])
    outcome = db.execute('SELECT receipt_digest,receipt_json FROM oneclick_release_outcomes '
        'WHERE job_id=? AND target_label=? AND attempt=?',
        (row['job_id'], row['target_label'], row['dispatch_count'])).fetchone()
    _require(_bytes(value).decode() == events[0]['event_json'] and _sha(_bytes(value)) == events[0]['event_digest']
        and value.get('schema_version') == SCHEMA and value['job_id'] == row['job_id']
        and value['target_label'] == row['target_label'] and value['attempt'] == row['dispatch_count']
        and row['status'] == 'RECONCILIATION_REQUIRED' and value['original_target_fingerprint'] == _fingerprint(row)
        and outcome is not None and _sha(outcome['receipt_json'].encode()) == outcome['receipt_digest']
        and value['original_outcome_receipt_digest'] == outcome['receipt_digest'], 'PRICE_ACCEPTANCE_ORIGINAL_ATTEMPT_CHANGED')
    return value


def _official_member(readback, target, command):
    _require(readback.get('status') == 'ORIGINAL_PH_TH_READBACK_CLOSED_NO_WRITE'
        and readback.get('business_writes') == 0, 'PRICE_ACCEPTANCE_OFFICIAL_READBACK_REQUIRED')
    reads = [r for r in readback['reads'] if r['target'] == target['target']]
    _require(bool(reads), 'PRICE_ACCEPTANCE_COMPLETE_PAGES_REQUIRED')
    items = []; seen = set()
    for index, record in enumerate(reads, 1):
        _require(record['params'] == {'discount_id': target['activity_id'], 'page_no': index, 'page_size': 100},
            'PRICE_ACCEPTANCE_PAGE_IDENTITY_CHANGED')
        raw = _pin(record['response_ref'])
        response = raw.get('response')
        _require(raw.get('error') in {None, '', '-'} and type(response) is dict
            and response.get('discount_id') == command['activity_id']
            and response.get('start_time') == command['start_time'] and response.get('end_time') == command['end_time']
            and type(response.get('more')) is bool and response['more'] == (index < len(reads))
            and type(response.get('item_list')) is list, 'PRICE_ACCEPTANCE_OFFICIAL_PAGE_INVALID')
        for item in response['item_list']:
            _require(type(item) is dict and type(item.get('item_id')) is int and item['item_id'] not in seen,
                'PRICE_ACCEPTANCE_MEMBER_DUPLICATE')
            seen.add(item['item_id']); items.append(item)
    members = [r for r in items if r['item_id'] == command['item_id']]
    _require(len(members) == 1, 'PRICE_ACCEPTANCE_EXACT_MEMBER_REQUIRED')
    models = members[0].get('model_list')
    _require(type(models) is list and len(models) == len(command['model_ids']) == 1
        and [r.get('model_id') for r in models] == command['model_ids'] == [target['model_id']]
        and Decimal(str(models[0]['model_original_price'])) == Decimal(str(command['expected']['list_price']))
        and Decimal(str(models[0]['model_promotion_price'])) == Decimal(target['accepted_official_price']),
        'PRICE_ACCEPTANCE_OFFICIAL_MODEL_PRICE_CHANGED')
    return {'shop_id': command['shop_id'], 'activity_id': command['activity_id'], 'item_id': command['item_id'],
        'model_ids': command['model_ids'], 'currency': command['expected']['currency'],
        'original_price': str(command['expected']['list_price']), 'accepted_price': target['accepted_official_price'],
        'complete_membership_digest': _sha(_bytes(items)), 'response_refs': [r['response_ref'] for r in reads]}


def record_existing_price_acceptance(store, *, authorization_ref, expected_outcome_receipt_digests):
    """Local-only, same original job/attempt; no DDL, provider or intent reset."""
    authority = _pin(authorization_ref)
    _require(authority.get('schema') == 'root-user-accepted-existing-promotion-price-variance/v1'
        and authority.get('status') == 'USER_ACCEPTED_ACTUAL_EXISTING_PRICES_NO_NEW_MERCHANT_WRITE'
        and authority.get('authority', {}).get('actor') == 'Kyle'
        and bool(authority['authority'].get('answer')) and bool(authority['authority'].get('question_id'))
        and all(authority.get('limits', {}).get(k) == 0 for k in
            ('new_auth', 'merchant_POST_PUT_DELETE', 'publication', 'other_targets')),
        'PRICE_ACCEPTANCE_EXPLICIT_USER_AUTHORITY_REQUIRED')
    labels = [r['target'] for r in authority['targets']]
    _require(labels and len(set(labels)) == len(labels)
        and set(labels) == set(expected_outcome_receipt_digests)
        and all(label.startswith('promotion:shopee:') for label in labels), 'PRICE_ACCEPTANCE_EXACT_TARGETS_REQUIRED')
    readback = _pin(authority['official_readback_ref'])
    _require(store.path.is_file(), 'PRICE_ACCEPTANCE_EXISTING_DB_REQUIRED')
    db = store._connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        job = db.execute('SELECT * FROM oneclick_release_jobs WHERE job_id=?', (authority['job_id'],)).fetchone()
        _require(job is not None and job['status'] in {'BLOCKED', 'SUCCEEDED'}, 'PRICE_ACCEPTANCE_JOB_NOT_TERMINAL')
        plan = db.execute('SELECT * FROM release_plans WHERE plan_id=?', (job['plan_id'],)).fetchone()
        approval = db.execute("SELECT 1 FROM release_approvals WHERE plan_id=? AND payload_digest=? "
            "AND status='APPROVED' AND approved_by='Kyle' AND user_approved=1", (job['plan_id'], job['payload_digest'])).fetchone()
        decision = db.execute('SELECT plan_id FROM native_sole_final_decisions WHERE decision_id=?',
            (authority['original_decision'],)).fetchone()
        _require(plan is not None and plan['status'] == 'APPROVED' and plan['product_id'] == authority['offer_id']
            and plan['payload_digest'] == job['payload_digest']
            and _sha(plan['payload_json'].encode()) == plan['payload_digest'] and approval is not None
            and decision is not None and decision['plan_id'] == job['plan_id'], 'PRICE_ACCEPTANCE_APPROVAL_IDENTITY_CHANGED')
        pending = []
        for target in authority['targets']:
            label = target['target']
            row = db.execute('SELECT * FROM oneclick_release_targets WHERE job_id=? AND target_label=?',
                (job['job_id'], label)).fetchone()
            _require(row is not None and row['status'] == 'RECONCILIATION_REQUIRED'
                and row['dispatch_count'] == target['attempt'] and target['attempt'] > 0
                and row['pending_write_intent_json'] is not None, 'PRICE_ACCEPTANCE_ORIGINAL_UNKNOWN_REQUIRED')
            outcome = db.execute('SELECT * FROM oneclick_release_outcomes WHERE job_id=? AND target_label=? AND attempt=?',
                (job['job_id'], label, target['attempt'])).fetchone()
            _require(outcome is not None and outcome['receipt_digest'] == expected_outcome_receipt_digests[label]
                and _sha(outcome['receipt_json'].encode()) == outcome['receipt_digest']
                and json.loads(outcome['receipt_json'])['outcome']['class'] == 'RECONCILIATION_REQUIRED',
                'PRICE_ACCEPTANCE_OUTCOME_IDENTITY_CHANGED')
            _require(_sha(row['command_json'].encode()) == row['command_digest']
                and _sha(row['proof_json'].encode()) == row['proof_digest']
                and _sha(row['pending_write_intent_json'].encode()) == row['pending_write_intent_digest'],
                'PRICE_ACCEPTANCE_PREPARED_BYTES_CHANGED')
            intent = json.loads(row['pending_write_intent_json'])
            occurrences = db.execute('SELECT * FROM oneclick_release_write_occurrences '
                'WHERE job_id=? AND target_label=? AND attempt=?',
                (job['job_id'], label, target['attempt'])).fetchall()
            _require(intent['intended_classes'] == ['shopee:promotion:discount']
                and intent['confirmed_lower_bound'] == row['cumulative_external_write_lower_bound'] == 0
                and intent['possible_upper_bound'] == row['cumulative_external_write_upper_bound'] == 1
                and len(occurrences) == 1 and occurrences[0]['status'] == 'OPEN'
                and occurrences[0]['confirmed_lower_bound'] == 0 and occurrences[0]['possible_upper_bound'] == 1,
                'PRICE_ACCEPTANCE_ORIGINAL_WRITE_OCCUPANCY_CHANGED')
            command = json.loads(row['command_json'])['payload']
            _require(command['target_label'] == label and command['mode'] == 'ADD_TO_EXISTING_DISCOUNT'
                and all(command[key] == target[key] for key in ('shop_id', 'activity_id', 'item_id'))
                and command['model_ids'] == [target['model_id']]
                and command['expected']['currency'] == target['currency']
                and command['expected']['seller_skus'] == [authority['seller_sku']]
                and Decimal(str(command['expected']['sale_after_discount'])) == Decimal(target['original_frozen_price'])
                and Decimal(target['accepted_official_price']) > 0
                and Decimal(target['accepted_official_price']) != Decimal(target['original_frozen_price']),
                'PRICE_ACCEPTANCE_FROZEN_COMMAND_CHANGED')
            observed = _official_member(readback, target, command)
            value = {'schema_version': SCHEMA, 'job_id': job['job_id'], 'target_label': label,
                'attempt': target['attempt'], 'original_target_fingerprint': _fingerprint(row),
                'original_outcome_receipt_digest': outcome['receipt_digest'], 'authorization_ref': authorization_ref,
                'official_readback_ref': authority['official_readback_ref'], 'official_observation': observed,
                'original_frozen_price': target['original_frozen_price'],
                'accepted_price': target['accepted_official_price'], 'currency': target['currency'],
                'exact_original_frozen_price_verified': False, 'user_accepted_existing_price': True,
                'original_unknown_and_write_occupancy_preserved': True, 'new_external_writes': []}
            existing = accepted_existing_price(db, row)
            if existing is not None:
                _require(existing == value, 'PRICE_ACCEPTANCE_ALREADY_DIFFERENT')
            else:
                pending.append((label, value))
        from shared_platform.oneclick_release_controlplane import _utc_now
        now = _utc_now()
        for label, value in pending:
            store._event(db, job['job_id'], label, EVENT, value, now)
        if pending:
            store._refresh_job(db, job['job_id'])
        result = store._project_job_in_transaction(db, job['job_id'])
        db.commit()
        return {'idempotent': not pending, 'acceptance_events_appended': len(pending),
            'external_writes_performed': [], 'original_attempts_unchanged': True, 'job': result}
    except BaseException:
        db.rollback(); raise
    finally:
        db.close()
