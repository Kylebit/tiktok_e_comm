"""Trusted cancellation intake for a prepared, unsubmitted publication task.

The original scope and R1/R2 evidence stay immutable. This is a cancellation
record, never publication authority. A consumer must explicitly adopt it before
the original task can run again; no HTTP endpoint admits caller-supplied proof.
"""
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path

API_SCHEMA = 'prepared-publication-api-target-selection/v1'
API_KEEP = ('ozon:RU','shopee:MY','shopee:PH','shopee:TH','shopee:VN',
    'tiktok:LH_MY','tiktok:LH_PH','tiktok:LH_TH','tiktok:LH_VN')
API_EXCLUDED = ('tiktok:GB','tiktok:HB_MY','tiktok:HB_PH','tiktok:HB_TH','tiktok:HB_VN','tiktok:MX')


def _pinned_document(pin):
    from shared_platform.immutable_approval_files import require_local_path
    if type(pin) is not dict or set(pin) != {'path','sha256'}:
        raise ValueError('API_SCOPE_EXACT_PIN_REQUIRED')
    path=Path(pin['path']);require_local_path(path,root=path.parent)
    raw=path.read_bytes()
    if not path.is_absolute() or hashlib.sha256(raw).hexdigest()!=pin['sha256']:
        raise ValueError('API_SCOPE_PIN_CHANGED')
    return json.loads(raw)


def build_completed_api_selection(*, task, original, version, preserved, pins):
    """One B-only post-COMMON scope receipt; source/adoption scopes remain immutable."""
    if type(pins) is not dict or set(pins)!={'user_decision','api_readonly'}:
        raise ValueError('API_SCOPE_TWO_PINNED_INPUTS_REQUIRED')
    user=_pinned_document(pins['user_decision']);api=_pinned_document(pins['api_readonly'])
    offer=original['offer_id'];old=original['targets']
    bylabel={r['target']:r for r in api.get('targets') or []}
    if (offer!='8412287877065586543' or task['task_id']!='TASK-20261023-001'
        or user.get('schema')!='orbit-user-scope-reduction-decision/v1'
        or user.get('user_quote')!='那你这一轮只发布有api的店铺好了'
        or user.get('task_id')!=task['task_id'] or user.get('offer_id')!=offer
        or user.get('marketplace_final_approval') is not False
        or any(user.get(k) is not False for k in ('new_common_create_or_edit','new_paid_generation','new_account_or_OAuth'))
        or api.get('schema_version')!='new-b-api-only-target-readonly/v2'
        or api.get('task_id')!=task['task_id'] or api.get('offer_id')!=offer
        or api.get('original_adoption_order')!=old
        or (api.get('adoption_order_authority') or {}).get('original_adoption_digest')!=canonical_digest(original)
        or api.get('retained_targets')!=list(API_KEEP) or api.get('excluded_targets')!=list(API_EXCLUDED)
        or api.get('remaining_uncertain_targets')!=[] or api.get('retained_target_count')!=9
        or api.get('intended_product_link_count')!=11 or set(bylabel)!=set(old)
        or [label for label in old if label in API_KEEP]!=list(API_KEEP)
        or set(old)!=set(API_KEEP+API_EXCLUDED)):
        raise ValueError('API_SCOPE_EXACT_B_AUTHORITY_REQUIRED')
    for label in old:
        row=bylabel[label]
        if label in API_KEEP:
            expected='API_SUPPORTED_WITH_AUTHORIZED_RENEWAL' if label.startswith('shopee:') else 'API_READY'
            if (row.get('classification')!=expected or row.get('api_executor_bound') is not True
                or row.get('requires_browser') is not False or row.get('retained_for_api_only_scope') is not True):
                raise ValueError('API_SCOPE_EXECUTION_AND_READBACK_REQUIRED')
        elif row.get('classification')!='NO_BOUND_API_READBACK' or row.get('retained_for_api_only_scope') is not False:
            raise ValueError('API_SCOPE_EXCLUDED_IDENTITY_CHANGED')
    from shared_platform.release_store import _target_labels
    scope=deepcopy(task['scope'])
    if sorted(scope['shops'])!=old:raise ValueError('API_SCOPE_ORIGINAL_TASK_CHANGED')
    body=dict(schema_version=API_SCHEMA,task_id=task['task_id'],offer_id=offer,
        original_scope=scope,effective_scope={**scope,'shops':list(API_KEEP)},
        ordered_marketplace_targets=list(_target_labels(API_KEEP)),excluded_targets=list(API_EXCLUDED),
        original_version=deepcopy(version),prepared_reference=original['facts']['prepared_reference'],
        original_adoption_digest=canonical_digest(original),checkpoint_sha256=canonical_digest(task['checkpoint']),
        checkpoint_snapshot=deepcopy(task['checkpoint']),
        authority=deepcopy(pins),predecessor_lineage=dict(predecessor_R2_identity=deepcopy(original['facts']['r2_identity'])),
        preserved=deepcopy(preserved),execution_authority=False,final_publication_approval=False,
        shared_budgets=dict(TIKTOK=4,SHOPEE=10,OZON=4))
    body['request_digest']=canonical_digest(pins)
    body['reduction_digest']=canonical_digest(body)
    return body


def validate_completed_api_selection(record):
    body=deepcopy(record);digest=body.pop('reduction_digest',None)
    if (body.get('schema_version')!=API_SCHEMA or canonical_digest(body)!=digest
        or body.get('offer_id')!='8412287877065586543'
        or body.get('effective_scope',{}).get('shops')!=list(API_KEEP)
        or body.get('excluded_targets')!=list(API_EXCLUDED)
        or body.get('execution_authority') is not False or body.get('final_publication_approval') is not False):
        raise ValueError('API_SCOPE_DERIVED_RECORD_CHANGED')
    _pinned_document(body['authority']['user_decision']);_pinned_document(body['authority']['api_readonly'])
    return record


def canonical_digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _labels(value):
    if (type(value) is not list or not value or any(type(x) is not str or not x.strip() for x in value)
            or len(set(value)) != len(value)):
        raise ValueError('target labels must be exact and unique')
    return sorted(value)


def _digest(value, *, prefix=''):
    if type(value) is not str or not re.fullmatch(re.escape(prefix) + '[0-9a-f]{64}', value):
        raise ValueError('frozen lineage digest is invalid')
    return value


def build_prepared_target_reduction(request, *, task_id, scope, version,
                                    checkpoint_sha256, prepared_reference,
                                    predecessor_review_bytes):
    """Validate the coordinator's exact request against pinned historical bytes.

    The coordinator supplies a fresh same-version task/checkpoint observation.
    Historical review bytes prove lineage and configured shop identity; they do
    not prove an absent writer or a current provider result.
    """
    try:
        request = deepcopy(request)
        if request['schema'] != 'detached-product-task-target-reduction-request/v1' or request['task_id'] != task_id:
            raise ValueError('target reduction request belongs to another task')
        authority, lineage = request['scope_authority'], request['predecessor_lineage']
        removed = authority['removed_target']
        offer = scope['offer_id']
        original = _labels(scope['shops'])
        label = removed['target']
        if (authority['schema'] != 'exact-product-scope-reduction-authority/v1'
                or authority['common_collect_box_id'] != offer or not offer
                or type(label) is not str or label == 'miaoshou:COMMON' or label not in original
                or removed['status'] != 'USER_CANCELLED_THIS_PRODUCT_ONLY'
                or type(removed['configured_shop_id']) is not str or not removed['configured_shop_id']
                or any(type(authority.get(key)) is not str or not authority[key].strip() for key in
                       ('user_message_id', 'user_message_at_utc', 'verbatim_user_instruction', 'received_via_parent_thread'))):
            raise ValueError('exact cancellation authority is incomplete or conflicts')
        from datetime import datetime
        message_at = datetime.fromisoformat(authority['user_message_at_utc'].replace('Z', '+00:00'))
        if message_at.utcoffset() is None:
            raise ValueError('cancellation authority timestamp needs a timezone')
        if (authority['other_Ozon_business_changed'] is not False
                or authority['new_publication_approval_granted'] is not False
                or authority['new_paid_or_refresh_authorization_granted'] is not False
                or request['execution_authority'] is not False
                or request['user_final_publication_approval'] is not False
                or any(type(request[key]) is not int or request[key] != 0
                       for key in ('business_writes', 'paid_calls', 'new_refreshes'))
                or lineage['new_R1_or_R2_approval_or_digest_created'] is not False):
            raise ValueError('cancellation must not inherit approval or broaden authority')
        remaining = [x for x in original if x != label]
        desired = {**deepcopy(scope), 'shops': remaining}
        if (not remaining or request['desired_business_scope'] != desired
                or _labels(authority['remaining_target_labels']) != remaining
                or type(authority['remaining_target_count']) is not int
                or authority['remaining_target_count'] != len(remaining)
                or _labels(lineage['predecessor_target_labels']) != original
                or type(lineage['predecessor_target_count']) is not int
                or lineage['predecessor_target_count'] != len(original)):
            raise ValueError('cancellation must remove only its exact target')
        if (type(predecessor_review_bytes) is not bytes
                or hashlib.sha256(predecessor_review_bytes).hexdigest() !=
                   _digest(lineage['predecessor_review_file_sha256'])):
            raise ValueError('frozen predecessor bytes changed')
        predecessor = json.loads(predecessor_review_bytes.decode('utf-8'))
        targets = predecessor['configured_targets']
        if (predecessor['common_collectbox_id'] != offer
                or predecessor['product_center_revision'] != lineage['predecessor_product_center_revision']
                or _labels([row['target'] for row in targets]) != original
                or [row['configured_shop_id'] for row in targets if row['target'] == label] != [removed['configured_shop_id']]
                or predecessor['snapshot_digest'] != _digest(lineage['predecessor_R1_snapshot_digest'], prefix='sha256:')
                or predecessor['first_review_digest'] != _digest(lineage['predecessor_first_review_digest'], prefix='sha256:')
                or predecessor['R2_identity'] != lineage['predecessor_R2_identity']
                or predecessor['R2_identity']['offer_id'] != offer
                or predecessor['R2_identity']['round1_snapshot_digest'] != predecessor['snapshot_digest']
                or predecessor['R2_identity']['first_review_digest'] != predecessor['first_review_digest']):
            raise ValueError('frozen predecessor product, shop or R1/R2 lineage conflicts')
        _digest(predecessor['R2_identity']['identity_digest'], prefix='sha256:')
        _digest(checkpoint_sha256)
        _digest(prepared_reference, prefix='r1-prepared:')
        if type(version) is not dict or not version.get('code_version'):
            raise ValueError('original pinned version is required')
        body = {
            'schema_version': 'prepared-publication-target-reduction/v1',
            'task_id': task_id, 'offer_id': offer, 'request_digest': canonical_digest(request),
            'original_scope': deepcopy(scope), 'effective_scope': desired,
            'original_version': deepcopy(version), 'checkpoint_sha256': checkpoint_sha256,
            'prepared_reference': prepared_reference, 'authority': authority,
            'predecessor_lineage': lineage, 'consumer_adopted': False,
            'execution_authority': False, 'final_publication_approval': False,
        }
        body['reduction_digest'] = canonical_digest(body)
        return body
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('target reduction proof is incomplete or invalid') from error


def select_marketplace_targets(original_targets, *, offer_id, r2_identity, reduction):
    """Select retained targets for an inert compiler, preserving original order.

    This pure seam cannot admit a task, approve a candidate or execute a target.
    A runtime caller must reread the immutable cancellation record itself.
    """
    body = deepcopy(reduction)
    if body.get('schema_version')==API_SCHEMA:
        validate_completed_api_selection(body)
        original=[x for x in original_targets if x!='miaoshou:COMMON']
        if (offer_id!=body['offer_id'] or sorted(original)!=body['original_scope']['shops']
            or r2_identity!=body['predecessor_lineage']['predecessor_R2_identity']):
            raise ValueError('API_SCOPE_FROZEN_LINEAGE_CHANGED')
        return [label for label in original if label in API_KEEP]
    try:
        reinstatement = body.pop('reinstatement', None)
        original_reduction = deepcopy(body)
        digest = body.pop('reduction_digest')
        original = [x for x in original_targets if x != 'miaoshou:COMMON']
        removed = body['authority']['removed_target']['target']
        effective = [x for x in original if x != removed]
        if (canonical_digest(body) != digest
                or body['schema_version'] != 'prepared-publication-target-reduction/v1'
                or body['offer_id'] != offer_id
                or body['execution_authority'] is not False
                or body['final_publication_approval'] is not False
                or body['predecessor_lineage']['predecessor_R2_identity'] != r2_identity
                or _labels(original) != body['original_scope']['shops']
                or removed == 'miaoshou:COMMON' or removed not in original
                or sorted(effective) != body['effective_scope']['shops']):
            raise ValueError('target reduction compiler lineage conflicts')
        if reinstatement is not None:
            from shared_platform.prepared_target_reinstatement import validate_reinstatement
            validate_reinstatement(reinstatement, original_reduction)
            return original
        return effective
    except (KeyError, TypeError) as error:
        raise ValueError('target reduction compiler proof is incomplete') from error
