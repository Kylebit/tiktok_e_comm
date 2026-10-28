"""Startup-owned consumer of one durable product target cancellation.

The cancellation document remains immutable and carries no execution authority.
Every consumer invocation rereads the task and cancellation in one read snapshot.
Only the service startup may install this concrete reader; no HTTP proof/flag is
accepted. The original task scope and COMMON/R1/R2 identity are never rewritten.
"""
from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import re
import sqlite3

from shared_platform.prepared_target_reduction import canonical_digest, select_marketplace_targets


@dataclass(frozen=True)
class PreparedTargetConsumerPins:
    task_id: str
    offer_id: str
    reduction_digest: str
    request_digest: str
    version_json: str
    prepared_reference: str
    r2_identity_json: str
    original_worker: str
    reinstatement_digest: str | None = None


class PreparedTargetReductionConsumer:
    def __init__(self, engine, pins, *, service_root):
        from shared_platform.workbench_engine import WorkbenchEngine
        if type(engine) is not WorkbenchEngine or type(pins) is not PreparedTargetConsumerPins:
            raise ValueError('PREPARED_TARGET_CONCRETE_CONSUMER_REQUIRED')
        if (not pins.task_id or not pins.original_worker or not re.fullmatch(r'[0-9]{1,32}', pins.offer_id)
                or engine.release != json.loads(pins.version_json)
                or any(not re.fullmatch(r'[0-9a-f]{64}', value)
                       for value in (pins.reduction_digest, pins.request_digest))
                or pins.reinstatement_digest is not None and (type(pins.reinstatement_digest) is not str
                    or not re.fullmatch(r'[0-9a-f]{64}', pins.reinstatement_digest))
                or not re.fullmatch(r'r1-prepared:[0-9a-f]{64}', pins.prepared_reference)):
            raise ValueError('PREPARED_TARGET_STARTUP_PINS_INVALID')
        self.engine, self.pins = engine, pins
        self.service_root = Path(service_root).resolve(strict=True)

    def reduction_identity(self, identity, *, payload_binding=None):
        successor = getattr(self, 'image_successor', None)
        if successor is None:
            return identity
        from shared_platform.original_images_successor import OriginalImagesSuccessor
        if (type(successor) is not OriginalImagesSuccessor
                or successor.continuation.engine is not self.engine
                or json.loads(self.pins.r2_identity_json) != successor.old_identity):
            raise ValueError('PREPARED_TARGET_IMAGE_SUCCESSOR_PRIVATE_BINDING_CHANGED')
        if payload_binding is not None and identity == successor.current_identity:
            return successor.validate_payload_binding(payload_binding)
        return successor.reduction_identity(identity)

    def read_verified(self, offer_id, *, r2_identity=None):
        if getattr(self,'native_completed_service',None) is not None:
            return self._read_completed_api(offer_id,r2_identity=r2_identity)
        if str(offer_id) != self.pins.offer_id:
            raise ValueError('PREPARED_TARGET_CONSUMER_OFFER_CONFLICT')
        if r2_identity is not None:
            r2_identity = self.reduction_identity(r2_identity)
        path = self.engine.store.path
        # mode=ro prevents creation/initialization/expiry, including absent DBs.
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            try:
                stored = db.execute('SELECT request_digest,record_json FROM workbench_publication_scope_reductions WHERE task_id=?',
                                    (self.pins.task_id,)).fetchone()
                task = db.execute('SELECT scope_json,version_json,checkpoint_json FROM workbench_execution WHERE task_id=? AND template=?',
                                  (self.pins.task_id, 'publication')).fetchone()
            except sqlite3.Error as error:
                raise ValueError('PREPARED_TARGET_DURABLE_RECORD_UNAVAILABLE') from error
            if stored is None or task is None:
                raise ValueError('PREPARED_TARGET_DURABLE_RECORD_REQUIRED')
            record = json.loads(stored['record_json'])
            scope, version, checkpoint = (json.loads(task[name]) for name in ('scope_json', 'version_json', 'checkpoint_json'))
            identity = json.loads(self.pins.r2_identity_json)
            original = record['original_scope']
            # Binding the frozen SKU later is the normal original-task action;
            # cancellation never gives authority to change its historical shops.
            same_scope = {key: value for key, value in scope.items() if key != 'skus'} == {
                key: value for key, value in original.items() if key != 'skus'}
            skus = scope.get('skus')
            if (record['task_id'] != self.pins.task_id or record['offer_id'] != self.pins.offer_id
                    or stored['request_digest'] != self.pins.request_digest
                    or record['request_digest'] != self.pins.request_digest
                    or record['reduction_digest'] != self.pins.reduction_digest
                    or version != json.loads(self.pins.version_json) or version != record['original_version']
                    or record['prepared_reference'] != self.pins.prepared_reference
                    or (checkpoint.get('native_preparation') or {}).get('prepared_reference') != self.pins.prepared_reference
                    or not same_scope or not isinstance(skus, list)
                    or (skus != original.get('skus') and not (len(skus) == 1 and re.fullmatch(r'[0-9]{4}', str(skus[0]))))
                    or record['predecessor_lineage']['predecessor_R2_identity'] != identity
                    or r2_identity is not None and r2_identity != identity):
                raise ValueError('PREPARED_TARGET_DURABLE_LINEAGE_CHANGED')
            # Full digest/authority/R1/R2/original/effective scope validation.
            select_marketplace_targets(original['shops'], offer_id=self.pins.offer_id,
                                       r2_identity=identity, reduction=record)
            events = db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=?',
                                (self.pins.task_id, 'publication_target_cancelled')).fetchall()
            if not any(json.loads(row[0]).get('reduction_digest') == record['reduction_digest'] for row in events):
                raise ValueError('PREPARED_TARGET_INTAKE_EVENT_REQUIRED')
            if scope.get('skus') == original.get('skus') and hashlib.sha256(task['checkpoint_json'].encode()).hexdigest() != record['checkpoint_sha256']:
                raise ValueError('PREPARED_TARGET_UNRESOLVED_PREPARATION_CHANGED')
            table = 'workbench_publication_target_reinstatements'
            restored = (db.execute('SELECT request_digest,record_json FROM '+table+' WHERE task_id=?',
                                   (self.pins.task_id,)).fetchone()
                        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() else None)
            if restored is not None or self.pins.reinstatement_digest is not None:
                if restored is None or self.pins.reinstatement_digest is None:
                    raise ValueError('PREPARED_TARGET_REINSTATEMENT_CONSUMER_NOT_ADOPTED')
                from shared_platform.prepared_target_reinstatement import validate_reinstatement
                successor = validate_reinstatement(json.loads(restored['record_json']), record)
                if (successor['reinstatement_digest'] != self.pins.reinstatement_digest
                        or restored['request_digest'] != successor['request_digest']):
                    raise ValueError('PREPARED_TARGET_REINSTATEMENT_PIN_CHANGED')
                predecessor_event = db.execute('SELECT detail_json FROM workbench_events WHERE id=? AND task_id=? AND event_type=?',
                    (successor['cancellation_event_id'], self.pins.task_id, 'publication_target_cancelled')).fetchone()
                successor_events = db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=?',
                    (self.pins.task_id, 'publication_target_reinstated')).fetchall()
                if (predecessor_event is None or json.loads(predecessor_event[0]).get('reduction_digest') != record['reduction_digest']
                        or not any(json.loads(row[0]).get('reinstatement_digest') == successor['reinstatement_digest']
                            and json.loads(row[0]).get('cancellation_event_id') == successor['cancellation_event_id']
                            for row in successor_events)):
                    raise ValueError('PREPARED_TARGET_REINSTATEMENT_EVENT_REQUIRED')
                record = {**record, 'reinstatement': successor}
            return deepcopy(record)

    def _read_completed_api(self,offer_id,*,r2_identity=None):
        """Same concrete consumer, backed by the genuine combined source event."""
        from shared_platform import agent_prepared_source_successor as source,agent_prepared_publication_adoption as adoption
        service=self.native_completed_service
        if str(offer_id)!=self.pins.offer_id or service.profile.version!=self.engine.release['code_version']:
            raise ValueError('API_SCOPE_CURRENT_SERVICE_REQUIRED')
        sid,key=adoption._owner_key(service.profile)
        with sqlite3.connect(self.engine.store.path.resolve().as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            raw=self.engine._row(db,self.pins.task_id);task=self.engine._project(db,raw)
            row=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(self.pins.task_id,)).fetchone()
            original=source._signed(row,key=key,schema=adoption.SCHEMA,owner_sid=sid)
            events=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',
                (self.pins.task_id,source.GALLERY_EVENT)).fetchall()
            if len(events)!=2:raise ValueError('API_SCOPE_TWO_GENUINE_EVENTS_REQUIRED')
            first_envelope=json.loads(events[0]['detail_json']);last_envelope=json.loads(events[1]['detail_json'])
            first=source._signed(first_envelope,key=key,schema=source.GALLERY_SCHEMA,owner_sid=sid)
            record=source._signed(last_envelope,key=key,schema=source.GALLERY_SCHEMA,owner_sid=sid)
            source.verify_historical_source(service.profile.root,first['source'],allowed_files=source.GALLERY_FILES)
            if (record.get('purpose')!='COMPLETED_COMMON_API_ONLY_VARIANT_IMAGE_PROJECTION' or record.get('revision')!=2
                or record['previous_record_digest']!=first_envelope['record_digest']
                or record['source']['old_version']!=first['source']['new_version']
                or record['original_adoption_digest']!=row['record_digest']):
                raise ValueError('API_SCOPE_GENUINE_CURRENT_SOURCE_REQUIRED')
            if source.verify_completed_gallery_current(task,service.profile,original,connection=db,owner_sid=sid,key=key,events=events)!=record:
                raise ValueError('API_SCOPE_GENUINE_CURRENT_SOURCE_REQUIRED')
        reduction=record.get('api_target_selection')
        if (reduction is None or reduction['reduction_digest']!=self.pins.reduction_digest
            or r2_identity is not None and r2_identity!=reduction['predecessor_lineage']['predecessor_R2_identity']):
            raise ValueError('API_SCOPE_SIGNED_CONSUMER_CHANGED')
        from shared_platform.prepared_target_reduction import build_completed_api_selection
        selected_task={**task,'checkpoint':reduction['checkpoint_snapshot']}
        if (any(task['checkpoint'].get(k)!=v for k,v in reduction['checkpoint_snapshot'].items())
            or reduction!=build_completed_api_selection(task=selected_task,original=original,
                version=record['source']['new_version'],preserved=record['preserved'],pins=reduction['authority'])):
            raise ValueError('API_SCOPE_ORIGINAL_CHECKPOINT_CHANGED')
        return deepcopy(reduction)

    def read_effective_scope(self, offer_id):
        record = self.read_verified(offer_id)
        return deepcopy((record.get('reinstatement') or record)['effective_scope'])

    def validate_payload(self, payload, *, target_labels=None):
        record = self.read_verified(payload.get('product_id'))
        identity = json.loads(self.pins.r2_identity_json)
        labels = payload.get('targets')
        if labels == ['miaoshou:COMMON']:
            stage = payload.get('r3_stage_binding') or {}
            if (stage.get('r2_identity') != identity
                    or sorted(stage.get('marketplace_targets') or []) != record['original_scope']['shops']
                    or stage.get('round1_snapshot_digest') != identity['round1_snapshot_digest']):
                raise ValueError('PREPARED_TARGET_COMMON_PREDECESSOR_CHANGED')
        else:
            binding = payload.get('r3_marketplace_binding') or {}
            supplied_identity = self.reduction_identity(binding.get('r2_identity'), payload_binding=binding)
            expected = select_marketplace_targets(record['original_scope']['shops'],
                offer_id=self.pins.offer_id, r2_identity=identity, reduction=record)
            if (sorted(labels or []) != sorted(expected)
                    or binding.get('target_reduction') != record or supplied_identity != identity):
                raise ValueError('PREPARED_TARGET_MARKETPLACE_SCOPE_CHANGED')
        if target_labels is not None and (not target_labels or len(set(target_labels)) != len(target_labels)
                                         or not set(target_labels) <= set(labels)):
            raise ValueError('PREPARED_TARGET_DISPATCH_SCOPE_CHANGED')
        return record


_INSTALLED = None


def completed_api_consumer(service):
    """Startup only, after the adopted service revalidated its signed current source."""
    from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
    from shared_platform.agent_prepared_source_successor import GALLERY_EVENT
    if type(service) is not AgentPreparedCommonService:raise ValueError('API_SCOPE_CONCRETE_ADOPTED_SERVICE_REQUIRED')
    with sqlite3.connect(service.engine.store.path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',
            (service.task_id,GALLERY_EVENT)).fetchall()
    if len(rows)!=2:return None
    envelope=json.loads(rows[1][0]);record=json.loads(envelope['record_json']);scope=record.get('api_target_selection')
    if scope is None:return None
    result=object.__new__(PreparedTargetReductionConsumer)
    result.engine=service.engine;result.service_root=Path(service.profile.root).resolve();result.native_completed_service=service
    result.pins=PreparedTargetConsumerPins(service.task_id,scope['offer_id'],scope['reduction_digest'],scope['request_digest'],
        json.dumps(service.engine.release),scope['prepared_reference'],json.dumps(scope['predecessor_lineage']['predecessor_R2_identity']),
        'native-explicit-decision')
    result.read_verified(scope['offer_id'])
    return result


def effective_marketplace_targets(offer_id,original_targets):
    consumer=configured_target_consumer(offer_id)
    if consumer is None:return list(original_targets)
    record=consumer.read_verified(offer_id)
    if sorted(original_targets)!=record['original_scope']['shops']:
        raise ValueError('API_SCOPE_ORIGINAL_TARGETS_CHANGED')
    return select_marketplace_targets(list(original_targets),offer_id=str(offer_id),
        r2_identity=record['predecessor_lineage']['predecessor_R2_identity'],reduction=record)


def install_prepared_target_consumer(consumer):
    """Private startup hook; construction is not an approval or writer lease."""
    global _INSTALLED
    if type(consumer) is not PreparedTargetReductionConsumer:
        raise ValueError('PREPARED_TARGET_CONCRETE_CONSUMER_REQUIRED')
    from modules.products import server
    if Path(server.ROOT).resolve() != consumer.service_root:
        raise ValueError('PREPARED_TARGET_SERVICE_ROOT_CHANGED')
    consumer.read_verified(consumer.pins.offer_id)
    if _INSTALLED is not None and _INSTALLED is not consumer:
        raise ValueError('PREPARED_TARGET_CONSUMER_ALREADY_INSTALLED')
    _INSTALLED = consumer


def configured_target_consumer(offer_id):
    consumer = _INSTALLED
    if consumer is None or str(offer_id) != consumer.pins.offer_id:
        return None
    from modules.products import server
    if Path(server.ROOT).resolve() != consumer.service_root:
        raise ValueError('PREPARED_TARGET_SERVICE_ROOT_CHANGED')
    return consumer
