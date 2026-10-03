"""Bounded official READ for this service's explicit accepted submissions.

There is no publish/repair callable here. Sites without an authorised official
reader remain submitted. Neither a platform acceptance nor a listing-only Ozon
observation closes a frozen warehouse/quantity command.
"""
from dataclasses import dataclass
import json

from shared_platform.final_review_server_admission import ApprovalBlocked


@dataclass(frozen=True)
class _SubmissionReadback:
    run_id: str
    target_label: str
    idempotency_key: str
    attempt: int
    external_id: str
    submission_digest: str
    evidence_json: str


def _official_observation(payload, target):
    from modules.products import release_adapters as adapters
    channel, site = target['target_label'].split(':', 1)
    if channel != 'tiktok' or site not in adapters.SEA_SITES:
        return {'verified': False, 'capability': 'OFFICIAL_EXACT_READBACK_UNAVAILABLE',
                'external_writes_performed': []}
    retained = (target.get('submission') or {}).get('evidence') or {}
    prior = retained.get('accepted_submission') or retained
    if (prior.get('accepted') is not True or type(prior.get('detail_id')) is not int
            or prior['detail_id'] <= 0 or type(prior.get('shop_id')) is not int
            or prior['shop_id'] <= 0 or target['external_id'] != str(prior['detail_id'])+':'+str(prior['shop_id'])):
        return {'verified': False, 'capability': 'SUBMISSION_READ_IDENTITY_UNAVAILABLE',
                'external_writes_performed': []}
    category = (((payload.get('product_facts') or {}).get('categories_by_target') or {}).get(target['target_label']) or {})
    category_id = (category.get('category') or {}).get('id')
    if (category.get('target_label') != target['target_label']
            or category.get('platform') != channel or category.get('site') != site
            or not isinstance(category_id, str) or not category_id.isascii() or not category_id.isdigit()):
        return {'verified': False, 'capability': 'FROZEN_TARGET_CATEGORY_UNAVAILABLE',
                'external_writes_performed': []}
    # The native frozen source currently retains a Miaoshou shop ID only.
    # TikTok's authorization ID is a different identity domain. Region, shop
    # name, matching SKU and even a previous regional READ cannot prove the
    # submitted shop. Until its exact official mapping is source-bound, no
    # regional first-shop query may close this original accepted submission.
    return {'verified': False, 'capability': 'FROZEN_OFFICIAL_SHOP_MAPPING_UNAVAILABLE',
            'target_label': target['target_label'], 'expected_category_id': category_id,
            'external_writes_performed': []}


def observe_submissions(runtime, decision_id):
    """Current decision recheck -> exact submission READ -> original store CAS."""
    from shared_platform.native_sole_final_execution import _NativeExecution, _ACTIVE
    with runtime.operation() as service:
        actor = service._actor()
        with runtime.store._connect_readonly() as db:
            db.execute('BEGIN')
            service._decision(db, decision_id, owner_sid=actor['owner_sid'])
            row = db.execute('SELECT plan_id FROM native_sole_final_decisions WHERE decision_id=?',
                             (decision_id,)).fetchone()
            plan_id = row['plan_id']
    plan = runtime.store.get_plan(plan_id)
    active = _NativeExecution(service, decision_id, runtime)
    active.plan_id = plan_id
    token = _ACTIVE.set(active)
    try:
        data = {'offer_id': plan['product_id'], 'plan_id': plan_id,
                'confirmation_token': plan['confirmation_token'], 'confirm_publish': True}
        gate, failure = active.gate(data, store=runtime.store)
        if failure:
            return {'pending': False, 'error': failure[1]['error'], 'observations': [],
                    'external_writes_performed': []}
        run = gate['run']
        observations = []
        retry_read = False
        for target in (run or {}).get('targets') or []:
            if target['status'] != 'SUBMITTED_UNVERIFIED':
                continue
            submission = target.get('submission') or {}
            if not submission.get('evidence_digest') or submission.get('external_id') != target['external_id']:
                raise ApprovalBlocked('NATIVE_SUBMISSION_RECEIPT_REQUIRED')
            try:
                evidence = _official_observation(gate['payload'], target)
                if evidence.get('verified') is True:
                    proof = _SubmissionReadback(run['run_id'], target['target_label'],
                        target['idempotency_key'], target['attempts'], target['external_id'],
                        submission['evidence_digest'],
                        json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
                    runtime.store._record_native_submission_readback(proof)
                elif not evidence.get('capability'):
                    retry_read = True
                observations.append({'target_label': target['target_label'], **evidence})
            except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
                # A failed READ never clears the original submission or retries
                # a write. The schedule is bounded even for unavailable APIs.
                retry_read = True
                observations.append({'target_label': target['target_label'], 'verified': False,
                    'error': str(error), 'external_writes_performed': []})
        return {'pending': retry_read, 'observations': observations,
                'run': runtime.store.get_run(run['run_id']) if run else None,
                'external_writes_performed': []}
    finally:
        _ACTIVE.reset(token)
