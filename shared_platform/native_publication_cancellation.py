"""Consume the user's durable Ozon cancellation without changing an approval.

The record is appended by the reviewed local source-maintenance producer. It
only removes execution eligibility; original plans, results and budgets remain
the authority for what was approved and what actually happened.
"""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path


def authority(pin):
    from shared_platform.native_approved_source_successor import DECISION, CANDIDATE, _need
    _need(type(pin) is dict and set(pin) == {'path','sha256'}, 'CANCELLATION_PIN_REQUIRED')
    path = Path(pin['path'])
    _need(path.is_absolute() and '..' not in path.parts, 'CANCELLATION_PATH_INVALID')
    raw = path.read_bytes()
    _need(sha256(raw).hexdigest() == pin['sha256'], 'CANCELLATION_AUTHORITY_CHANGED')
    value = json.loads(raw)
    _need(value.get('schema') == 'orbit-publication-platform-cancellation/v1'
          and value.get('actor') == 'USER' and value.get('platform') == 'OZON'
          and value.get('user_message') == 'ozon不用上品了这个平台全部取消'
          and value.get('scope') == 'Cancel Ozon for current Offer4106902637 and future new-product publication defaults'
          and value.get('effect') == 'NO_FURTHER_OZON_PUBLICATION_IMAGE_OR_STOCK_MUTATIONS'
          and value.get('original_decision_id') == DECISION
          and value.get('original_candidate_digest') == CANDIDATE
          and value.get('remaining_platforms') == ['TIKTOK','SHOPEE']
          and value.get('existing_listing_delisting_authorized') is False
          and value.get('new_image_budget_granted') is False
          and value.get('new_approval_required') is False, 'EXPLICIT_CANCELLATION_REQUIRED')
    return {'authority':dict(pin), 'document':value, 'excluded_platforms':['OZON'],
            'execution_authority':False, 'existing_results_preserved':True}


def read(engine):
    """Read the single registered restriction from the bound operations DB."""
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform.native_approved_source_successor import TASK, SCHEMA, _event, _need
    if engine is None:return None
    _need(type(engine) is WorkbenchEngine, 'CANCELLATION_BOUND_ENGINE_REQUIRED')
    with engine.readonly_snapshot() as db:
        _, record = _event(db,TASK)
    if record is None:return None
    _need(record['schema_version'] == SCHEMA and record['task_id'] == TASK
          and record['execution_authority'] is False, 'CANCELLATION_RECORD_CHANGED')
    restriction = authority(record['cancellation']['authority'])
    _need(record['cancellation'] == restriction, 'CANCELLATION_RECORD_CHANGED')
    return restriction


def for_operations(operations):
    return read(getattr(operations,'engine',None)) if operations is not None else None


def eligible_labels(labels, cancellation):
    """Filter in original order; never add, rename or relabel a target."""
    return [label for label in labels if cancellation is None
            or label.split(':',1)[0].upper() not in cancellation['excluded_platforms']]


def eligible_rows(rows, cancellation):
    labels = set(eligible_labels([row['target_label'] for row in rows],cancellation))
    return [row for row in rows if row['target_label'] in labels]


def future_payload(engine, payload):
    """Apply the new-product default only before a new idempotency key exists.

    An existing request still resolves to its literal historical scope. The
    execution gate independently enforces cancellation even for that request.
    """
    if payload.get('template') != 'publication':return payload
    restriction = read(engine)
    if restriction is None:return payload
    with engine.readonly_snapshot() as db:
        original = db.execute("SELECT e.detail_json FROM workbench_tasks t JOIN workbench_events e USING(task_id) "
            "WHERE t.source_key=? AND e.event_type='explicit_new_post_preparation'",
            (str(payload.get('source_key','')).strip(),)).fetchone()
        if original and any(label.split(':',1)[0].upper()=='OZON'
                for label in json.loads(original[0])['scope']['shops']):return payload
    result = deepcopy(payload)
    scope = result.setdefault('scope',{})
    scope['shops'] = eligible_labels(scope.get('shops') or [],restriction)
    return result
