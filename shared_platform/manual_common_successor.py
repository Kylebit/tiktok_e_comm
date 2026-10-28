"""One installed decision-bound child CREATE; the original UNKNOWN is retained."""
from dataclasses import dataclass
from pathlib import Path
import json,sqlite3
from shared_platform import manual_common_creation as manual

DECISION_SCHEMA='common-create-successor-user-decision/v1'
CHILD_SCHEMA='manual-common-create-successor/v1'

@dataclass(frozen=True)
class PinnedCommonSuccessorDecision:
    path: Path
    sha256: str
    def read(self):
        if not isinstance(self.path,Path) or not self.path.is_absolute():manual._deny('COMMON_SUCCESSOR_ABSOLUTE_PIN_REQUIRED')
        raw=self.path.read_bytes()
        if manual.sha256(raw).hexdigest()!=self.sha256:manual._deny('COMMON_SUCCESSOR_DECISION_PIN_CHANGED')
        value=json.loads(raw.decode('utf-8-sig'))
        required=dict(schema_version=DECISION_SCHEMA,action_class='AUTHORIZE_ONE_COMMON_CREATE_SUCCESSOR',actor='Kyle',
            maximum_new_create_attempts=1,combined_create_attempt_cap=2,original_unknown_retained=True,
            baseline_edit_attempt_cap_unchanged=1,same_task_and_preparation_root_required=True,
            candidate_content_changes_authorized=False,marketplace_approval=False,standing_authority=False)
        if any(type(value.get(k)) is not type(v) or value.get(k)!=v for k,v in required.items()):
            manual._deny('COMMON_SUCCESSOR_EXACT_DECISION_REQUIRED')
        return value

def installed_decision(config):
    pin=config.get('agent_prepared_common_successor_decision')
    if pin is None:return None
    if type(pin) is not dict or set(pin)!={'path','sha256'}:manual._deny('COMMON_SUCCESSOR_STARTUP_PIN_REQUIRED')
    reader=PinnedCommonSuccessorDecision(Path(pin['path']),pin['sha256']);reader.read();return reader

def initialize_schema(path):
    """Explicit maintenance only. Never invoked by a service constructor/GET."""
    schema=_child_schema('manual_common_create_successors','manual_common_successor_observations','manual_common_successor_')
    schema+='''CREATE TRIGGER IF NOT EXISTS manual_common_original_retained
      BEFORE UPDATE ON manual_common_creations
      WHEN EXISTS(SELECT 1 FROM manual_common_create_successors WHERE parent_intent_id=OLD.intent_id)
      BEGIN SELECT RAISE(ABORT,'original UNKNOWN retained by successor'); END;'''
    with sqlite3.connect(path) as db:db.executescript(schema)

def _child_schema(table,observations,prefix):
    schema=manual._SCHEMA.replace('manual_common_creation_observations',observations)
    schema=schema.replace('manual_common_creations',table)
    schema=schema.replace('manual_common_observation_',prefix+'observation_')
    schema=schema.replace('manual_common_creation_',prefix)
    schema=schema.replace('returned_detail_id TEXT, create_response_json TEXT);',
        'returned_detail_id TEXT, create_response_json TEXT, parent_intent_id TEXT NOT NULL UNIQUE, relation_json TEXT NOT NULL, relation_digest TEXT NOT NULL);')
    schema=schema.replace('WHEN NEW.intent_id IS NOT OLD.intent_id',
        'WHEN NEW.parent_intent_id IS NOT OLD.parent_intent_id OR NEW.relation_json IS NOT OLD.relation_json OR NEW.relation_digest IS NOT OLD.relation_digest OR NEW.intent_id IS NOT OLD.intent_id')
    return schema

class ManualCommonSuccessorLedger(manual.ManualCommonCreateLedger):
    table='manual_common_create_successors'
    observations='manual_common_successor_observations'
    identity_prefix='manual-common-successor:'
    reader_type=PinnedCommonSuccessorDecision
    def __init__(self,path,*,decision_reader=None):
        super().__init__(path);self.decision_reader=decision_reader
    @staticmethod
    def _projection(row):
        value=manual.ManualCommonCreateLedger._projection(row)
        relation=json.loads(value.pop('relation_json'))
        if manual._digest(relation)!=value['relation_digest']:manual._deny('COMMON_SUCCESSOR_RELATION_CHANGED')
        value['relation']=relation;return value
    def verify_parent(self,row):
        parent=manual.ManualCommonCreateLedger(self.path).read(row['parent_intent_id'])
        relation=row['relation']
        if (relation.get('schema_version')!=CHILD_SCHEMA or relation.get('parent_row_sha256')!=manual._digest(parent)
            or parent['state']!='UNKNOWN' or parent['attempts']!=1
            or parent['intent']!=row['intent'] or row['offer_id']!=parent['offer_id']
            or row['preparation_root_id']!=parent['preparation_root_id']
            or row['intent_id']!='manual-common-successor:'+manual._digest(relation)):
            manual._deny('COMMON_SUCCESSOR_ORIGINAL_UNKNOWN_CHANGED')
        return parent
    def read(self,intent_id):
        row=super().read(intent_id);self.verify_parent(row);return row
    def reserve(self,intent):manual._deny('COMMON_SUCCESSOR_MANAGED_RESERVATION_REQUIRED')
    def reserve_successor(self,parent,*,task,adoption_record,owner_sid,source_chain_digest):
        if type(self.decision_reader) is not PinnedCommonSuccessorDecision:manual._deny('COMMON_SUCCESSOR_INSTALLED_DECISION_REQUIRED')
        decision=self.decision_reader.read();source=parent['intent']
        if (type(source_chain_digest) is not str or len(source_chain_digest)!=64
            or any(c not in '0123456789abcdef' for c in source_chain_digest)):manual._deny('COMMON_SUCCESSOR_SIGNED_SOURCE_CHAIN_REQUIRED')
        expected=dict(offer_id=parent['offer_id'],task_id=task['task_id'],original_intent_id=parent['intent_id'],
            original_adoption_digest=manual._digest(adoption_record),round1_snapshot_digest=source['round1_digest'],
            source_identity_binding_sha256=source['source_identity_binding_sha256'],seller_sku=source['seller_sku'],
            model_skus=source['model_skus'],targets=source['target_labels'],
            canonical_skus=sorted(adoption_record['facts']['aliases']))
        if (any(decision.get(k)!=v for k,v in expected.items()) or owner_sid!=source['owner_sid']
            or owner_sid!=adoption_record['owner_sid'] or parent['state']!='UNKNOWN' or parent['attempts']!=1
            or parent['returned_detail_id'] is not None or parent['create_response'] is not None):
            manual._deny('COMMON_SUCCESSOR_EXACT_UNKNOWN_SCOPE_REQUIRED')
        current=manual.ManualCommonCreateLedger(self.path).read(parent['intent_id'])
        if current!=parent:manual._deny('COMMON_SUCCESSOR_PARENT_CHANGED')
        relation=dict(schema_version=CHILD_SCHEMA,parent_row_sha256=manual._digest(parent),
            decision_path=str(self.decision_reader.path),decision_sha256=self.decision_reader.sha256,
            task_id=task['task_id'],source_version=task['version'],source_chain_digest=source_chain_digest,
            original_adoption_digest=expected['original_adoption_digest'])
        return self._reserve_child(parent,source,relation)
    def _reserve_child(self,parent,source,relation):
        identity=self.identity_prefix+manual._digest(relation)
        with self._transaction() as db:
            # Uniqueness is independent of caller request keys or Task identity.
            old=db.execute(f'SELECT * FROM {self.table} WHERE offer_id=? OR preparation_root_id=? OR parent_intent_id=?',
                (parent['offer_id'],parent['preparation_root_id'],parent['intent_id'])).fetchone()
            parent_ledger=manual._ledger_for(self.path,parent['intent_id'])
            actual=parent_ledger._projection(db.execute(f'SELECT * FROM {parent_ledger.table} WHERE intent_id=?',(parent['intent_id'],)).fetchone())
            if actual!=parent:manual._deny('COMMON_SUCCESSOR_PARENT_CHANGED')
            if old:
                if old['intent_id']!=identity:manual._deny('COMMON_SUCCESSOR_SLOT_ALREADY_OCCUPIED')
            else:
                manual.validate_create_body(source['create_body'])
                stamp=manual._now()
                db.execute(f'INSERT INTO {self.table} VALUES (?,?,?,?,?,\'RESERVED\',0,NULL,NULL,?,?,NULL,NULL,?,?,?)',
                    (identity,parent['offer_id'],parent['preparation_root_id'],manual._bytes(source).decode(),
                     manual._digest(source),stamp,stamp,parent['intent_id'],manual._bytes(relation).decode(),manual._digest(relation)))
        return self.read(identity)
    def create(self,intent_id,**kwargs):
        row=self.read(intent_id)
        if type(self.decision_reader) is not self.reader_type:manual._deny('COMMON_SUCCESSOR_INSTALLED_DECISION_REQUIRED')
        self.decision_reader.read()
        if (str(self.decision_reader.path)!=row['relation']['decision_path']
            or self.decision_reader.sha256!=row['relation']['decision_sha256']):manual._deny('COMMON_SUCCESSOR_DECISION_PIN_CHANGED')
        return super().create(intent_id,**kwargs)
    def observe_existing(self,intent_id,candidates):
        self.read(intent_id)
        observation=dict(kind='EXISTING_COMMON_BEFORE_SUCCESSOR',observed_at=manual._now(),
            detail_ids=[str(x.get('commonCollectBoxDetailId') or x.get('id') or '') for x in candidates],
            readback_sha256=manual._digest(candidates))
        with self._transaction() as db:
            db.execute(f'INSERT INTO {self.observations} VALUES (?,?,?)',
                (intent_id,manual._digest(observation),manual._bytes(observation).decode()))
        return observation

CORRECTION_SCHEMA='common-create-price-correction-user-decision/v1'
CORRECTION_RELATION_SCHEMA='manual-common-create-price-correction/v1'
KNOWN_REJECTION_REASON='无SKU时，产品价格（price）必填'

@dataclass(frozen=True)
class PinnedCommonCorrectionDecision:
    path: Path
    sha256: str
    def read(self):
        if not isinstance(self.path,Path) or not self.path.is_absolute():manual._deny('COMMON_CORRECTION_ABSOLUTE_PIN_REQUIRED')
        raw=self.path.read_bytes()
        if manual.sha256(raw).hexdigest()!=self.sha256:manual._deny('COMMON_CORRECTION_DECISION_PIN_CHANGED')
        value=json.loads(raw.decode('utf-8-sig'))
        required=dict(schema_version=CORRECTION_SCHEMA,actor='Kyle',decision_status='USER_AUTHORIZED',action_class='AUTHORIZE_ONE_KNOWN_PRICE_REJECTION_CORRECTION',
            maximum_new_create_attempts=1,combined_create_attempt_cap=3,original_unknown_retained=True,
            child_unknown_and_response_retained=True,baseline_edit_attempt_cap_unchanged=1,
            same_task_and_preparation_root_required=True,candidate_content_changes_authorized=False,
            marketplace_approval=False,standing_authority=False)
        if any(type(value.get(k)) is not type(v) or value.get(k)!=v for k,v in required.items()):
            manual._deny('COMMON_CORRECTION_EXACT_DECISION_REQUIRED')
        return value

def installed_correction_decision(config):
    pin=config.get('agent_prepared_common_correction_decision')
    if pin is None:return None
    if type(pin) is not dict or set(pin)!={'path','sha256'}:manual._deny('COMMON_CORRECTION_STARTUP_PIN_REQUIRED')
    reader=PinnedCommonCorrectionDecision(Path(pin['path']),pin['sha256']);reader.read();return reader

def validate_known_price_rejection(child):
    response=child.get('create_response') if type(child) is dict else None
    if (child is None or child['state']!='UNKNOWN' or child['attempts']!=1 or child['returned_detail_id'] is not None
        or set(child['intent']['create_body'])!={'title','itemNum'}
        or type(response) is not dict or response.get('provider_result')!='fail'
        or response.get('provider_reason')!=KNOWN_REJECTION_REASON or response.get('zero_write_verified') is not False
        or response.get('returned_detail_id') is not None):manual._deny('COMMON_CORRECTION_KNOWN_PRICE_REJECTION_REQUIRED')
    return response

def initialize_correction_schema(path):
    schema=_child_schema('manual_common_create_corrections','manual_common_correction_observations','manual_common_correction_')
    schema+='''CREATE TRIGGER IF NOT EXISTS manual_common_child_retained
      BEFORE UPDATE ON manual_common_create_successors
      WHEN EXISTS(SELECT 1 FROM manual_common_create_corrections WHERE parent_intent_id=OLD.intent_id)
      BEGIN SELECT RAISE(ABORT,'rejected child retained by correction'); END;'''
    with sqlite3.connect(path) as db:db.executescript(schema)

class ManualCommonCorrectionLedger(ManualCommonSuccessorLedger):
    table='manual_common_create_corrections'
    observations='manual_common_correction_observations'
    identity_prefix='manual-common-correction:'
    reader_type=PinnedCommonCorrectionDecision
    def verify_parent(self,row):
        parent=ManualCommonSuccessorLedger(self.path).read(row['parent_intent_id'])
        validate_known_price_rejection(parent);relation=row['relation']
        source=dict(parent['intent']);source['create_body']=relation['create_body']
        if (relation.get('schema_version')!=CORRECTION_RELATION_SCHEMA or relation['parent_row_sha256']!=manual._digest(parent)
            or row['intent']!=source or row['offer_id']!=parent['offer_id'] or row['preparation_root_id']!=parent['preparation_root_id']
            or row['intent_id']!=self.identity_prefix+manual._digest(relation)):
            manual._deny('COMMON_CORRECTION_REJECTED_CHILD_CHANGED')
        return parent
    def reserve_correction(self,parent,*,task,adoption_record,source_chain_digest,create_body):
        relation=self.correction_relation(parent,task=task,adoption_record=adoption_record,
            source_chain_digest=source_chain_digest,create_body=create_body)
        source=dict(parent['intent']);source['create_body']=manual.validate_create_body(create_body)
        return self._reserve_child(parent,source,relation)
    def correction_relation(self,parent,*,task,adoption_record,source_chain_digest,create_body):
        if type(self.decision_reader) is not PinnedCommonCorrectionDecision:manual._deny('COMMON_CORRECTION_INSTALLED_DECISION_REQUIRED')
        response=validate_known_price_rejection(parent);decision=self.decision_reader.read();source=dict(parent['intent'])
        manual.validate_create_body(create_body)
        if set(create_body)!={'title','itemNum','price'} or create_body['price']!=4.0:
            manual._deny('COMMON_CORRECTION_EXACT_PRICE_DTO_REQUIRED')
        expected=dict(task_id=task['task_id'],offer_id=parent['offer_id'],original_intent_id=parent['parent_intent_id'],
            child_intent_id=parent['intent_id'],child_response_sha256=response['response_sha256'],
            child_decoded_business_bytes_sha256=response['decoded_business_bytes_sha256'],
            original_adoption_digest=manual._digest(adoption_record),round1_snapshot_digest=source['round1_digest'],
            source_identity_binding_sha256=source['source_identity_binding_sha256'],seller_sku=source['seller_sku'],
            model_skus=source['model_skus'],canonical_skus=sorted(adoption_record['facts']['aliases']),targets=source['target_labels'],
            corrected_create_body=create_body,corrected_create_body_sha256=manual._digest(create_body))
        if (any(decision.get(k)!=v for k,v in expected.items()) or source['owner_sid']!=adoption_record['owner_sid']
            or any(create_body[k]!=source['create_body'].get(k) for k in ('title','itemNum'))
            or type(source_chain_digest) is not str or len(source_chain_digest)!=64):manual._deny('COMMON_CORRECTION_EXACT_SCOPE_REQUIRED')
        source['create_body']=manual.validate_create_body(create_body)
        relation=dict(schema_version=CORRECTION_RELATION_SCHEMA,parent_row_sha256=manual._digest(parent),
            task_id=task['task_id'],source_version=task['version'],source_chain_digest=source_chain_digest,
            original_adoption_digest=expected['original_adoption_digest'],create_body=create_body,
            price_derivation='FROZEN_R1_CNY_COST_COMMON_TECHNICAL_SEED_NOT_MARKETPLACE_SALE_PRICE',
            decision_path=str(self.decision_reader.path),decision_sha256=self.decision_reader.sha256)
        return relation

def stored_child(path,offer_id):
    return _stored(path,offer_id,'manual_common_create_successors',ManualCommonSuccessorLedger)

def _stored(path,offer_id,table,ledger_type):
    if not Path(path).is_file():return None
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() is None:return None
        row=db.execute(f'SELECT intent_id FROM {table} WHERE offer_id=?',(offer_id,)).fetchone()
    if row is None:return None
    return ledger_type(path).read(row['intent_id'])

def effective_child(path,offer_id):
    return (_stored(path,offer_id,'manual_common_create_corrections',ManualCommonCorrectionLedger)
        or stored_child(path,offer_id))
