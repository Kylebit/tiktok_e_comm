"""Read-only, server-owned evidence classifier for an exact legacy ReleasePlan.

This is a point-in-time classification, not write authority. Callers must recheck
under their mutation lock/transaction. No HTTP route uses this module yet.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3

from shared_platform.workbench_final_review_evidence import _verified_task_image
from shared_platform.release_store import (
    RELEASE_TARGET_LABELS, _final_review_digest, _prefixed_sha256_json, preview_release_plan,
)
from shared_platform.internal_catalog_sku import internal_sku


LEGACY_ALLOWED = 'LEGACY_ALLOWED'
NEW_MODE_FORBIDDEN = 'NEW_MODE_FORBIDDEN'
AMBIGUOUS = 'AMBIGUOUS/UNAVAILABLE'
_MARKETPLACE_TARGETS = frozenset(RELEASE_TARGET_LABELS) - {'miaoshou:COMMON'}


@dataclass(frozen=True)
class LegacyAdmission:
    status: str
    reason: str
    offer_id: str
    plan_id: str
    task_id: str | None = None


def _object(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('expected object')
    return value


def _list(raw):
    value = json.loads(raw)
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        raise ValueError('expected identity list')
    return value


def _digest(value):
    """Use the final-review digest contract, requiring the producer's prefix."""
    return (type(value) is str and value.startswith('sha256:')
            and _final_review_digest(value, field='frozen digest') == value)


def _r2_identity(identity, *, offer_id, r1_digest):
    fields = {'schema_version', 'offer_id', 'round1_snapshot_digest',
              'first_review_digest', 'generation_identity_digest',
              'generation_digest', 'translation_plan_digest',
              'translation_result_digest', 'qa_digest', 'artifact_digests',
              'identity_digest'}
    if (type(identity) is not dict or set(identity) != fields
            or identity['schema_version'] != 'publication-r2-identity/v1'
            or identity['offer_id'] != offer_id
            or identity['round1_snapshot_digest'] != r1_digest
            or not all(_digest(identity[field]) for field in (
                'first_review_digest', 'generation_identity_digest',
                'generation_digest', 'translation_plan_digest',
                'translation_result_digest', 'qa_digest'))):
        return False
    artifacts = identity['artifact_digests']
    if (type(artifacts) is not list or not artifacts
            or not all(_digest(value) for value in artifacts)
            or artifacts != sorted(set(artifacts))):
        return False
    unsigned = {field: value for field, value in identity.items() if field != 'identity_digest'}
    return (_digest(identity['identity_digest'])
            and identity['identity_digest'] == _prefixed_sha256_json(unsigned))


def _stage_evidence(stage, *, offer_id, targets):
    if (type(stage) is not dict or set(stage) != {
            'schema_version', 'execution_scope', 'image_approval_scope',
            'write_approval_source', 'round1_snapshot_digest', 'r2_identity',
            'marketplace_targets'}
            or stage['schema_version'] != 'r3-common-stage/v1'
            or stage['execution_scope'] != ['miaoshou:COMMON']
            or stage['image_approval_scope'] != 'ROUND2_IMAGES_ONLY'
            or stage['write_approval_source'] != 'ReleaseStore'
            or stage['marketplace_targets'] != targets
            or not _digest(stage['round1_snapshot_digest'])):
        return False
    return _r2_identity(stage['r2_identity'], offer_id=offer_id,
                        r1_digest=stage['round1_snapshot_digest'])


def _frozen_r1(identity, *, offer_id, seller_sku, targets, r1_digest, product_revision):
    if type(identity) is not dict:
        return False
    if set(identity) == {'offer_id', 'targets', 'snapshot_digest',
                         'prepared_reference', 'internal_sku', 'seller_sku'}:
        reference = identity['prepared_reference']
        return (identity['offer_id'] == offer_id
                and identity['targets'] == sorted(targets)
                and identity['snapshot_digest'] == r1_digest
                and (reference is None or type(reference) is str and bool(reference))
                and identity['internal_sku'] == seller_sku
                and type(identity['seller_sku']) is str
                and internal_sku(identity['seller_sku']) == seller_sku)
    # The registered adapter carries the R1 approved_product_center_revision.
    # The COMMON producer uses that same frozen revision as product_revision.
    return (set(identity) == {'offer_id', 'snapshot_digest', 'approved_revision',
                             'binding_sha256', 'targets', 'seller_sku', 'internal_sku'}
            and identity['offer_id'] == offer_id
            and identity['targets'] == sorted(targets)
            and identity['snapshot_digest'] == r1_digest
            and type(identity['approved_revision']) is int
            and identity['approved_revision'] == product_revision
            and type(identity['binding_sha256']) is str
            and len(identity['binding_sha256']) == 64
            and all(c in '0123456789abcdef' for c in identity['binding_sha256'])
            and identity['internal_sku'] == seller_sku
            and type(identity['seller_sku']) is str
            and internal_sku(identity['seller_sku']) == seller_sku)


def _plan_row(row):
    plan = dict(row)
    plan['payload'] = _object(plan['payload_json'])
    plan['targets'] = _list(plan['target_labels_json'])
    expected = preview_release_plan(plan['payload'])
    if any(plan[key] != expected[key] for key in
           ('plan_id', 'product_id', 'seller_sku', 'payload_digest', 'confirmation_token')) \
            or plan['targets'] != expected['targets']:
        raise ValueError('stored ReleasePlan identity or digest changed')
    return plan


def _approval_consistent(db, plan):
    rows = db.execute('SELECT * FROM release_approvals WHERE plan_id=?', (plan['plan_id'],)).fetchall()
    if plan['status'] == 'PENDING_APPROVAL':
        return not rows
    return (plan['status'] == 'APPROVED' and len(rows) == 1
            and rows[0]['status'] == 'APPROVED' and rows[0]['approved_by'] == 'Kyle'
            and rows[0]['user_approved'] == 1
            and rows[0]['payload_digest'] == plan['payload_digest']
            and rows[0]['confirmation_token'] == plan['confirmation_token'])


def _task_claim(db, row, *, plan, common, targets, events):
    """Return exact legacy/new claim, unrelated, or unresolved plausible claim."""
    scope = _object(row['scope_json'])
    action = _object(row['action_json']) if row['action_json'] else {}
    binding = action.get('receipt_binding')
    claimed_plan = (binding.get('common_plan_id') or binding.get('plan_id')) if isinstance(binding, dict) else None
    if claimed_plan != common['plan_id']:
        if (row['template'] != 'publication' or scope.get('offer_id') != plan['product_id']
                or (isinstance(claimed_plan, str) and bool(claimed_plan))):
            return None
        return 'uncertain'
    if (row['template'] != 'publication' or scope.get('offer_id') != plan['product_id']
            or scope.get('skus') != [plan['seller_sku']]
            or scope.get('shops') != sorted(targets)):
        return 'uncertain'
    if row['step_index'] != 2 or row['state'] != 'waiting_user' or row['task_status'] != 'waiting_approval':
        return 'uncertain'
    if (row['external_started'] or row['worker'] is not None or row['lease_token'] is not None
            or row['lease_until'] is not None
            or db.execute('SELECT 1 FROM workbench_external_tasks WHERE task_id=?',
                          (row['task_id'],)).fetchone()
            or db.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? "
                          "AND state<>'completed'", (row['task_id'],)).fetchone()):
        return 'uncertain'
    steps = json.loads(row['steps_json'])
    if (not isinstance(steps, list) or len(steps) <= 2 or not isinstance(steps[2], dict)
            or steps[2].get('key') != 'release'):
        return 'uncertain'
    if action.get('kind') != 'review' or not isinstance(binding, dict):
        return 'uncertain'
    action_id = action.get('action_id')
    matching_events = [e for e in events if e[0] == action_id]
    if (not isinstance(action_id, str) or not action_id or len(matching_events) != 1
            or matching_events[0][1] != row['task_id'] or matching_events[0][2] != action):
        return 'uncertain'
    scope_digest = hashlib.sha256(row['scope_json'].encode()).hexdigest()
    stage = common['payload'].get('r3_stage_binding') or {}
    frozen_r1 = (steps[0].get('checkpoint') or {}).get('native_r1') or {}
    frozen_r2 = (steps[1].get('checkpoint') or {}).get('native_r2') or {}
    # Registered fixtures use the same frozen identities under their existing
    # receipt names. A missing checkpoint never proves an old-mode allowance.
    if not frozen_r1 and not frozen_r2:
        first = (steps[0].get('checkpoint') or {}).get('publication_identity') or {}
        images = steps[1].get('checkpoint') or {}
        receipt = images.get('image_receipt') or {}
        if (first and first == images.get('publication_identity')
                and type(receipt) is dict
                and receipt.get('binding_sha256') == first.get('binding_sha256')
                and type(receipt.get('revision')) is int
                and receipt['revision'] >= 0
                and type(receipt.get('selection_digest')) is str
                and len(receipt['selection_digest']) == 64
                and all(c in '0123456789abcdef' for c in receipt['selection_digest'])):
            frozen_r1 = first
            frozen_r2 = receipt.get('consumer_identity') or {}
    if (not _frozen_r1(frozen_r1, offer_id=plan['product_id'],
                       seller_sku=plan['seller_sku'], targets=targets,
                       r1_digest=stage['round1_snapshot_digest'],
                       product_revision=common['payload']['product_revision'])
            or not _r2_identity(frozen_r2, offer_id=plan['product_id'],
                                r1_digest=stage['round1_snapshot_digest'])):
        return 'uncertain'
    if (binding.get('offer_id') != plan['product_id']
            or binding.get('scope_digest') != scope_digest
            or binding.get('common_payload_digest', binding.get('payload_digest')) != common['payload_digest']
            or binding.get('round1_digest', binding.get('round1_snapshot_digest'))
               != stage.get('round1_snapshot_digest')
            or frozen_r1.get('snapshot_digest') != stage.get('round1_snapshot_digest')
            or frozen_r2 != stage.get('r2_identity')):
        return 'uncertain'
    if row['review_mode'] is None:
        if (set(binding) != {'adapter', 'offer_id', 'plan_id', 'payload_digest',
                             'scope_digest', 'round1_snapshot_digest'}
                or binding.get('adapter') != 'publication-common/v1'
                or binding.get('plan_id') != common['plan_id']):
            return 'uncertain'
        return 'legacy'
    if row['review_mode'] != 'single-final-review/v1' or row['generation'] != 1:
        return 'uncertain'
    if (set(binding) != {'offer_id', 'revision', 'sku', 'round1_digest', 'round2_digest',
                         'targets', 'common_plan_id', 'common_payload_digest',
                         'common_token_digest', 'preview_digest', 'adapter', 'review_mode',
                         'task_id', 'action_id', 'generation', 'step_index', 'scope_digest'}
            or binding.get('adapter') != 'single-final-review/v1'
            or binding.get('review_mode') != row['review_mode']
            or binding.get('task_id') != row['task_id']
            or binding.get('action_id') != action_id
            or binding.get('generation') != row['generation']
            or binding.get('step_index') != row['step_index']
            or binding.get('sku') != plan['seller_sku']
            or binding.get('targets') != targets
            or binding.get('round2_digest') != stage['r2_identity'].get('identity_digest')
            or not binding.get('revision') or not binding.get('preview_digest')
            or binding.get('revision') != str(common['payload'].get('product_revision'))
            or binding.get('common_token_digest') !=
               'sha256:' + hashlib.sha256(common['confirmation_token'].encode()).hexdigest()):
        return 'uncertain'
    return 'new'


def classify_legacy_plan_admission(task_database_path, release_database_path, *, offer_id, plan_id):
    """Classify only an existing exact COMMON or marketplace plan.

    Both paths must be server-configured absolute paths. Body mode/task_id are
    never inputs. Missing ledger, plan, association, or inconsistent evidence is
    ambiguous. The task image is captured once and queried only in memory.
    """
    result = lambda status, reason, task_id=None: LegacyAdmission(status, reason, offer_id, plan_id, task_id)
    if not isinstance(offer_id, str) or not offer_id or not isinstance(plan_id, str) or not plan_id:
        return result(AMBIGUOUS, 'exact_identity_required')
    task_path, release_path = Path(task_database_path), Path(release_database_path)
    if (not task_path.is_absolute() or '..' in task_path.parts
            or not release_path.is_absolute() or '..' in release_path.parts):
        return result(AMBIGUOUS, 'absolute_configured_paths_required')
    try:
        task_image = _verified_task_image(task_path)
        release_image = _verified_task_image(release_path)
        task_db = sqlite3.connect(':memory:')
        task_db.row_factory = sqlite3.Row
        try:
            task_db.deserialize(task_image)
            task_db.execute('PRAGMA query_only=ON')
            task_db.execute('BEGIN')
            release_db = sqlite3.connect(':memory:')
            try:
                release_db.row_factory = sqlite3.Row
                release_db.deserialize(release_image)
                release_db.execute('PRAGMA query_only=ON')
                release_db.execute('BEGIN')
                plan_row = release_db.execute('SELECT * FROM release_plans WHERE plan_id=?', (plan_id,)).fetchone()
                if plan_row is None or plan_row['product_id'] != offer_id:
                    return result(AMBIGUOUS, 'exact_plan_missing_or_offer_mismatch')
                plan = _plan_row(plan_row)
                if (plan['payload'].get('product_id') != offer_id
                        or plan['payload'].get('seller_sku') != plan['seller_sku']
                        or plan['payload'].get('targets') != plan['targets']):
                    return result(AMBIGUOUS, 'plan_payload_identity_conflict')
                if plan['status'] not in ('PENDING_APPROVAL', 'APPROVED'):
                    return result(AMBIGUOUS, 'plan_not_active')
                if not _approval_consistent(release_db, plan):
                    return result(AMBIGUOUS, 'plan_approval_conflict')
                if plan['targets'] == ['miaoshou:COMMON']:
                    common = plan
                else:
                    market_binding = plan['payload'].get('r3_marketplace_binding') or {}
                    if market_binding.get('schema_version') != 'r3-marketplace-stage/v1':
                        return result(AMBIGUOUS, 'marketplace_common_binding_missing')
                    common_row = release_db.execute('SELECT * FROM release_plans WHERE plan_id=?',
                                                    (market_binding.get('common_plan_id'),)).fetchone()
                    if common_row is None:
                        return result(AMBIGUOUS, 'common_plan_missing')
                    common = _plan_row(common_row)
                    if (common['product_id'] != offer_id or common['seller_sku'] != plan['seller_sku']
                            or common['targets'] != ['miaoshou:COMMON']
                            or common['status'] != 'APPROVED'
                            or market_binding.get('common_payload_digest') != common['payload_digest']):
                        return result(AMBIGUOUS, 'marketplace_common_identity_conflict')
                    if not _approval_consistent(release_db, common):
                        return result(AMBIGUOUS, 'common_approval_conflict')
                stage = common['payload'].get('r3_stage_binding')
                if not isinstance(stage, dict):
                    return result(AMBIGUOUS, 'ordered_target_or_stage_conflict')
                targets = stage.get('marketplace_targets')
                if (stage.get('schema_version') != 'r3-common-stage/v1'
                        or not isinstance(targets, list) or not targets
                        or len(set(targets)) != len(targets)
                        or any(type(t) is not str or t not in _MARKETPLACE_TARGETS
                               for t in targets)
                        or (plan is not common and plan['targets'] != targets)):
                    return result(AMBIGUOUS, 'ordered_target_or_stage_conflict')
                if (type(common['payload'].get('product_revision')) is not int
                        or common['payload']['product_revision'] < 0
                        or not _stage_evidence(stage, offer_id=offer_id, targets=targets)):
                    return result(AMBIGUOUS, 'common_source_identity_incomplete')
                decisions = release_db.execute('SELECT * FROM release_final_review_decisions WHERE common_plan_id=?',
                                               (common['plan_id'],)).fetchall()
                if len(decisions) > 1:
                    return result(AMBIGUOUS, 'multiple_final_review_decisions')
                has_decision = bool(decisions)
                if has_decision:
                    decision = decisions[0]
                    if (decision['offer_id'] != offer_id or decision['seller_sku'] != plan['seller_sku']
                            or decision['common_payload_digest'] != common['payload_digest']):
                        return result(AMBIGUOUS, 'final_review_decision_conflict')
                rows = task_db.execute('SELECT e.*,t.status task_status,i.review_mode,i.generation '
                                       'FROM workbench_execution e JOIN workbench_tasks t USING(task_id) '
                                       'LEFT JOIN workbench_review_identity i USING(task_id)').fetchall()
                event_rows = task_db.execute("SELECT task_id,detail_json FROM workbench_events "
                                             "WHERE event_type='user_action_required'").fetchall()
                events = []
                for event in event_rows:
                    detail = _object(event['detail_json'])
                    events.append((detail.get('action_id'), event['task_id'], detail))
                claims = [(row['task_id'], _task_claim(task_db, row, plan=plan, common=common,
                                                      targets=targets, events=events)) for row in rows]
                claims = [(task_id, claim) for task_id, claim in claims if claim is not None]
                if len(claims) > 1 or (claims and claims[0][1] == 'uncertain'):
                    return result(AMBIGUOUS, 'missing_or_multiple_exact_task_claims')
                if has_decision:
                    if claims and claims[0][1] == 'legacy':
                        return result(AMBIGUOUS, 'legacy_action_conflicts_with_final_review_decision')
                    return result(NEW_MODE_FORBIDDEN, 'exact_final_review_decision',
                                  claims[0][0] if claims else None)
                if not claims:
                    return result(AMBIGUOUS, 'missing_exact_task_claim')
                task_id, claim = claims[0]
                if claim == 'new':
                    return result(NEW_MODE_FORBIDDEN, 'exact_new_mode_task', task_id)
                return result(LEGACY_ALLOWED, 'exact_original_legacy_action', task_id)
            finally:
                release_db.close()
        finally:
            task_db.close()
    except Exception as error:  # Any unrecognized or unreadable evidence fails closed.
        return result(AMBIGUOUS, type(error).__name__)
