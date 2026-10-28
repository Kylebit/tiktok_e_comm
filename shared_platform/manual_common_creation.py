"""One product-bound manual COMMON CREATE, distinct from the one baseline EDIT.

The ledger is local recovery evidence, never a provider permission grant. Only
the installed preparation service supplies validated native source and transport.
No constructor/GET initializes tables or sends a request.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

CREATE_PATH='/open/v1/product/common_collect_box/common_collect_box/add_common_collect_box_detail'
CREATE_SCHEMA='manual-common-create-intent/v1'
_CREATE_DISPATCH=ContextVar('manual_common_create_dispatch',default=None)
_CREATE_RESPONSE_CAPTURE=ContextVar('manual_common_create_response_capture',default=None)
_SCHEMA="""
CREATE TABLE IF NOT EXISTS manual_common_creations (
 intent_id TEXT PRIMARY KEY, offer_id TEXT NOT NULL UNIQUE,
 preparation_root_id TEXT NOT NULL UNIQUE, intent_json TEXT NOT NULL,
 intent_digest TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('RESERVED','UNKNOWN','VERIFIED')),
 attempts INTEGER NOT NULL CHECK(attempts IN (0,1)), detail_id TEXT UNIQUE,
 result_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 returned_detail_id TEXT, create_response_json TEXT);
CREATE TABLE IF NOT EXISTS manual_common_creation_observations (
 intent_id TEXT NOT NULL, observation_digest TEXT NOT NULL, observation_json TEXT NOT NULL,
 PRIMARY KEY(intent_id,observation_digest), FOREIGN KEY(intent_id) REFERENCES manual_common_creations(intent_id));
CREATE TRIGGER IF NOT EXISTS manual_common_observation_no_update BEFORE UPDATE ON manual_common_creation_observations
 BEGIN SELECT RAISE(ABORT,'immutable manual common observation'); END;
CREATE TRIGGER IF NOT EXISTS manual_common_observation_no_delete BEFORE DELETE ON manual_common_creation_observations
 BEGIN SELECT RAISE(ABORT,'immutable manual common observation'); END;
CREATE TRIGGER IF NOT EXISTS manual_common_creation_no_delete BEFORE DELETE ON manual_common_creations
 BEGIN SELECT RAISE(ABORT,'immutable manual common creation'); END;
CREATE TRIGGER IF NOT EXISTS manual_common_creation_identity BEFORE UPDATE ON manual_common_creations
 WHEN NEW.intent_id IS NOT OLD.intent_id OR NEW.offer_id IS NOT OLD.offer_id
 OR NEW.preparation_root_id IS NOT OLD.preparation_root_id OR NEW.intent_json IS NOT OLD.intent_json
 OR NEW.intent_digest IS NOT OLD.intent_digest OR NEW.created_at IS NOT OLD.created_at
 OR OLD.state='VERIFIED' OR NEW.attempts<OLD.attempts
 OR (OLD.create_response_json IS NOT NULL AND (NEW.create_response_json IS NOT OLD.create_response_json OR NEW.returned_detail_id IS NOT OLD.returned_detail_id))
 OR (OLD.state='UNKNOWN' AND NEW.state NOT IN ('UNKNOWN','VERIFIED'))
 BEGIN SELECT RAISE(ABORT,'immutable manual common intent or terminal state'); END;
"""

class ManualCommonBlocked(ValueError):pass
def _bytes(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def _digest(v):return sha256(_bytes(v)).hexdigest()
def _now():return datetime.now(timezone.utc).isoformat()
def _deny(code):raise ManualCommonBlocked(code)

def initialize_schema(path):
    """Explicit local maintenance only; never called by service/GET/constructor."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path) as db:db.executescript(_SCHEMA)

def _validated(value):
    keys={'schema_version','offer_id','preparation_root_id','round1_digest','r2_digest',
      'source_identity_binding_sha256','owner_sid','seller_sku','model_skus','target_labels',
      'forbidden_detail_ids','create_body','authority','maximum_create_attempts','maximum_baseline_edits'}
    if type(value) is not dict or set(value)!=keys or value['schema_version']!=CREATE_SCHEMA:_deny('MANUAL_COMMON_INTENT_INVALID')
    if value['maximum_create_attempts']!=1 or value['maximum_baseline_edits']!=1:_deny('MANUAL_COMMON_CAP_INVALID')
    for field in ('offer_id','seller_sku'):
        if type(value[field]) is not str or not value[field].isascii() or not value[field].isdigit():_deny('MANUAL_COMMON_IDENTITY_INVALID')
    for field in ('model_skus','target_labels','forbidden_detail_ids'):
        rows=value[field]
        if type(rows) is not list or len(rows)!=len(set(rows)) or any(type(x) is not str or not x for x in rows):_deny('MANUAL_COMMON_SCOPE_INVALID')
    if not value['model_skus'] or not value['target_labels'] or not value['owner_sid'].startswith('S-1-'):_deny('MANUAL_COMMON_SCOPE_INVALID')
    body=value['create_body']
    if (type(body) is not dict or not {'title','itemNum'}<=set(body)<= {'title','itemNum','price','skuMap'} or body['itemNum']!=value['seller_sku']
        or type(body['title']) is not str or not body['title'].strip() or value['authority']!='CURRENT_NEW_PRODUCT_PREPARATION'):_deny('MANUAL_COMMON_CREATE_BODY_INVALID')
    for field in ('round1_digest','r2_digest','source_identity_binding_sha256'):
        text=value[field].removeprefix('sha256:')
        if len(text)!=64 or any(c not in '0123456789abcdef' for c in text):_deny('MANUAL_COMMON_SOURCE_DIGEST_INVALID')
    return deepcopy(value)

def validate_create_body(body):
    """Official conditional DTO: a no-SKU product needs a numeric price.

    Existing historical bodies remain readable. New reservations/transports
    fail before spending a slot, never by rewriting a historical intent.
    """
    if (type(body) is not dict or not {'title','itemNum'}<=set(body)<= {'title','itemNum','price','skuMap'}
        or type(body['title']) is not str or not body['title'].strip()
        or type(body['itemNum']) is not str or not body['itemNum'].isascii() or not body['itemNum'].isdigit()
        or ('skuMap' in body and type(body['skuMap']) is not dict)):_deny('MANUAL_COMMON_CREATE_BODY_INVALID')
    if not body.get('skuMap') or 'price' in body:
        price=body.get('price')
        if type(price) not in (int,float) or not math.isfinite(price) or price<=0:
            _deny('MANUAL_COMMON_NO_SKU_NUMERIC_PRICE_REQUIRED')
    return deepcopy(body)

class ManualCommonCreateLedger:
    table='manual_common_creations'
    observations='manual_common_creation_observations'
    def __init__(self,path):self.path=Path(path)
    @contextmanager
    def _transaction(self):
        db=sqlite3.connect(self.path.resolve().as_uri()+'?mode=rw',uri=True,isolation_level=None,timeout=30)
        db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE');yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()
    @staticmethod
    def _projection(row):
        if row is None:_deny('MANUAL_COMMON_INTENT_MISSING')
        value=dict(row)
        if _digest(json.loads(value['intent_json']))!=value['intent_digest']:_deny('MANUAL_COMMON_INTENT_CORRUPT')
        value['intent']=json.loads(value.pop('intent_json'))
        result=value.pop('result_json')
        value['result']=json.loads(result) if result else None
        response=value.pop('create_response_json')
        value['create_response']=json.loads(response) if response else None
        return value
    def read(self,intent_id):
        if type(self) is ManualCommonCreateLedger and intent_id.startswith(('manual-common-successor:','manual-common-correction:')):
            return _ledger_for(self.path,intent_id).read(intent_id)
        with sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
            return self._projection(db.execute(f'SELECT * FROM {self.table} WHERE intent_id=?',(intent_id,)).fetchone())
    def for_offer(self,offer_id):
        if type(self) is ManualCommonCreateLedger:
            from shared_platform.manual_common_successor import effective_child
            child=effective_child(self.path,offer_id)
            if child is not None:return child
        return self.original_for_offer(offer_id)
    def original_for_offer(self,offer_id):
        """Historical intent lookup, deliberately excluding effective children."""
        if not self.path.is_file():return None
        with sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            try:row=db.execute('SELECT * FROM manual_common_creations WHERE offer_id=?',(offer_id,)).fetchone()
            except sqlite3.OperationalError:return None
            return self._projection(row) if row else None
    def reserve(self,intent):
        intent=_validated(intent);fingerprint=_digest(intent);identity='manual-common:'+fingerprint
        with self._transaction() as db:
            old=db.execute('SELECT * FROM manual_common_creations WHERE offer_id=?',(intent['offer_id'],)).fetchone()
            if old:
                if old['preparation_root_id']!=intent['preparation_root_id']:_deny('MANUAL_COMMON_EXISTING_PRODUCT_REQUIRES_RECONCILIATION')
                if old['intent_digest']!=fingerprint:_deny('MANUAL_COMMON_INTENT_CONFLICT')
            else:
                validate_create_body(intent['create_body'])
                stamp=_now()
                db.execute('INSERT INTO manual_common_creations VALUES (?,?,?,?,?,\'RESERVED\',0,NULL,NULL,?,?,NULL,NULL)',
                    (identity,intent['offer_id'],intent['preparation_root_id'],_bytes(intent).decode(),fingerprint,stamp,stamp))
        return self.read(identity)
    @staticmethod
    def _verify_detail(row,detail):
        if type(detail) is not dict:_deny('MANUAL_COMMON_IDENTITY_UNVERIFIED')
        identity=str(detail.get('commonCollectBoxDetailId') or detail.get('id') or '')
        source=row['intent']
        if (not identity.isascii() or not identity.isdigit() or int(identity)<=0
            or identity in source['forbidden_detail_ids'] or identity==source['offer_id']
            or str(detail.get('itemNum') or '')!=source['seller_sku']):_deny('MANUAL_COMMON_IDENTITY_UNVERIFIED')
        seed=source['create_body'].get('skuMap')
        if seed is not None:
            observed=detail.get('skuMap')
            if (type(observed) is not dict or set(observed)!=set(seed)
                or any(str(observed[key].get('itemNum') or '')!=str(seed[key]['itemNum']) for key in seed)):
                _deny('MANUAL_COMMON_VARIANTS_UNVERIFIED')
        return identity
    def _complete(self,intent_id,detail):
        row=self.read(intent_id)
        observed=str(detail.get('commonCollectBoxDetailId') or detail.get('id') or '') if type(detail) is dict else None
        observation=dict(intent_id=intent_id,returned_detail_id=row['returned_detail_id'],observed_detail_id=observed,
            response_sha256=_digest(detail),observed_at=_now())
        with self._transaction() as db:
            db.execute(f'INSERT INTO {self.observations} VALUES (?,?,?)',
                (intent_id,_digest(observation),_bytes(observation).decode()))
        if row['create_response'] is not None and observed!=row['returned_detail_id']:_deny('MANUAL_COMMON_RETURNED_READBACK_ID_CONFLICT')
        identity=self._verify_detail(row,detail)
        result={'detail_id':identity,'seller_sku':row['intent']['seller_sku'],'readback_digest':_digest(detail),'verified_at':_now()}
        with self._transaction() as db:
            current=self._projection(db.execute(f'SELECT * FROM {self.table} WHERE intent_id=?',(intent_id,)).fetchone())
            if current['state']=='VERIFIED':
                if current['detail_id']!=identity:_deny('MANUAL_COMMON_IDENTITY_CONFLICT')
                return current
            if current['state']!='UNKNOWN' or current['attempts']!=1:_deny('MANUAL_COMMON_ORIGINAL_ATTEMPT_REQUIRED')
            db.execute(f'UPDATE {self.table} SET state=\'VERIFIED\',detail_id=?,result_json=?,updated_at=? WHERE intent_id=?',
                (identity,_bytes(result).decode(),_now(),intent_id))
        return self.read(intent_id)
    def _retain_create_response(self,intent_id,reply,*,redaction_values=(),decoded_business_bytes_sha256=None):
        marker=_CREATE_RESPONSE_CAPTURE.get()
        row=self.read(intent_id)
        if marker!=(self.path.resolve(),intent_id,row['intent_digest']):
            _deny('MANUAL_COMMON_ORIGINAL_RESPONSE_CONTEXT_REQUIRED')
        if type(reply) is not dict:_deny('MANUAL_COMMON_RESPONSE_OBJECT_REQUIRED')
        from modules.miaoshou.client import (_business_rejection_details,sanitize_provider_code,
            sanitize_provider_reason,sanitize_provider_field_path)
        code,field,reason=_business_rejection_details(reply)
        def safe(value):
            text=str(value)
            for secret in redaction_values:
                if secret:text=text.replace(str(secret),'[redacted]')
            return text
        data=reply.get('data');value=data.get('commonCollectBoxDetailId') if type(data) is dict else None
        candidate=str(value) if type(value) in (str,int) else None
        if candidate is not None and (not candidate.isascii() or not candidate.isdigit() or len(candidate)>32 or int(candidate)<=0):candidate=None
        receipt=dict(schema_version='manual-common-create-return/v1',intent_id=intent_id,
            returned_detail_id=candidate,response_sha256=_digest(reply),
            response_digest_kind='CANONICAL_DECODED_BUSINESS_JSON',
            provider_result=safe(sanitize_provider_code(reply.get('result'))),provider_code=safe(code),
            provider_field_path=safe(sanitize_provider_field_path(field)),
            provider_reason=safe(sanitize_provider_reason(reason if str(reply.get('result')).lower()!='success'
                else reply.get('message') or 'Accepted business response')),
            zero_write_verified=False,received_at=_now())
        if decoded_business_bytes_sha256 is not None:
            if (type(decoded_business_bytes_sha256) is not str or len(decoded_business_bytes_sha256)!=64
                or any(c not in '0123456789abcdef' for c in decoded_business_bytes_sha256)):_deny('MANUAL_COMMON_RESPONSE_DIGEST_INVALID')
            receipt['decoded_business_bytes_sha256']=decoded_business_bytes_sha256
        with self._transaction() as db:
            current=db.execute(f'SELECT state,attempts,create_response_json FROM {self.table} WHERE intent_id=?',(intent_id,)).fetchone()
            if current is None or current['state']!='UNKNOWN' or current['attempts']!=1:
                _deny('MANUAL_COMMON_ORIGINAL_RETURN_CONFLICT')
            if current['create_response_json'] is not None:
                retained=json.loads(current['create_response_json'])
                if retained.get('response_sha256')!=receipt['response_sha256'] or retained.get('returned_detail_id')!=candidate:
                    _deny('MANUAL_COMMON_ORIGINAL_RETURN_CONFLICT')
                if decoded_business_bytes_sha256 is not None and retained.get('decoded_business_bytes_sha256')!=decoded_business_bytes_sha256:
                    _deny('MANUAL_COMMON_ORIGINAL_RETURN_CONFLICT')
            else:
                db.execute(f'UPDATE {self.table} SET returned_detail_id=?,create_response_json=?,updated_at=? WHERE intent_id=?',
                    (candidate,_bytes(receipt).decode(),_now(),intent_id))
        return self.read(intent_id)
    def create(self,intent_id,*,post_create,read_detail):
        with self._transaction() as db:
            row=self._projection(db.execute(f'SELECT * FROM {self.table} WHERE intent_id=?',(intent_id,)).fetchone())
            if row['state']=='VERIFIED':return row
            if row['state']!='RESERVED' or row['attempts']!=0:_deny('MANUAL_COMMON_ORIGINAL_RECONCILIATION_REQUIRED')
            validate_create_body(row['intent']['create_body'])
            # Committed before transport. A process death can never enable replay.
            db.execute(f'UPDATE {self.table} SET state=\'UNKNOWN\',attempts=1,updated_at=? WHERE intent_id=?',(_now(),intent_id))
        marker=(self.path.resolve(),intent_id,row['intent_digest']);token=_CREATE_DISPATCH.set(marker)
        capture_token=_CREATE_RESPONSE_CAPTURE.set(marker)
        try:
            reply=post_create(deepcopy(row['intent']['create_body']))
            if type(reply) is not dict:_deny('MANUAL_COMMON_CREATE_OUTCOME_UNKNOWN')
            # The returned provider identity is durable before any assertion,
            # secondary GET or response interpretation can fail.
            retained=self._retain_create_response(intent_id,reply)
            if reply.get('result')!='success':_deny('MANUAL_COMMON_CREATE_OUTCOME_UNKNOWN')
            identity=retained['returned_detail_id'] or ''
            if (not identity.isascii() or not identity.isdigit() or int(identity)<=0
                or identity in row['intent']['forbidden_detail_ids'] or identity==row['offer_id']):_deny('MANUAL_COMMON_IDENTITY_UNVERIFIED')
            return self._complete(intent_id,read_detail(identity))
        except ManualCommonBlocked:raise
        except Exception:_deny('MANUAL_COMMON_CREATE_OUTCOME_UNKNOWN')
        finally:
            _CREATE_RESPONSE_CAPTURE.reset(capture_token)
            _CREATE_DISPATCH.reset(token)
    def reconcile(self,intent_id,*,candidates,complete):
        row=self.read(intent_id)
        if row['state']=='VERIFIED':return row
        if row['state']!='UNKNOWN' or complete is not True or type(candidates) is not list or len(candidates)!=1:_deny('MANUAL_COMMON_UNIQUE_COMPLETE_RECONCILIATION_REQUIRED')
        return self._complete(intent_id,candidates[0])
    def reconcile_returned(self,intent_id,*,read_detail):
        row=self.read(intent_id)
        if row['state']=='VERIFIED':return row
        identity=row['returned_detail_id']
        if (row['state']!='UNKNOWN' or row['attempts']!=1 or row['create_response'] is None
            or type(identity) is not str or not identity.isascii() or not identity.isdigit() or int(identity)<=0
            or identity==row['offer_id'] or identity in row['intent']['forbidden_detail_ids']):
            _deny('MANUAL_COMMON_ORIGINAL_RETURNED_ID_REQUIRED')
        try:return self._complete(intent_id,read_detail(identity))
        except ManualCommonBlocked:raise
        except Exception:_deny('MANUAL_COMMON_ORIGINAL_RETURN_READBACK_UNKNOWN')

def _ledger_for(path,intent_id):
    if intent_id.startswith('manual-common-correction:'):
        from shared_platform.manual_common_successor import ManualCommonCorrectionLedger
        return ManualCommonCorrectionLedger(path)
    if intent_id.startswith('manual-common-successor:'):
        from shared_platform.manual_common_successor import ManualCommonSuccessorLedger
        return ManualCommonSuccessorLedger(path)
    return ManualCommonCreateLedger(path)

def require_create_dispatch(body):
    marker=_CREATE_DISPATCH.get()
    if marker is None:_deny('MANUAL_COMMON_CREATE_MANAGED_DISPATCH_REQUIRED')
    path,intent_id,fingerprint=marker;row=_ledger_for(path,intent_id).read(intent_id)
    if (row['state']!='UNKNOWN' or row['attempts']!=1 or row['intent_digest']!=fingerprint
        or row['intent']['create_body']!=body):_deny('MANUAL_COMMON_CREATE_DISPATCH_CHANGED')
    _CREATE_DISPATCH.set(None)

def retain_create_sdk_response(path,body,reply,*,redaction_values=(),decoded_business_bytes_sha256=None):
    """Retain only the currently dispatched original CREATE; never authorize retry."""
    marker=_CREATE_RESPONSE_CAPTURE.get()
    if path!=CREATE_PATH or marker is None:_deny('MANUAL_COMMON_ORIGINAL_RESPONSE_CONTEXT_REQUIRED')
    ledger_path,intent_id,fingerprint=marker;ledger=_ledger_for(ledger_path,intent_id)
    row=ledger.read(intent_id)
    if (row['state']!='UNKNOWN' or row['attempts']!=1 or row['intent_digest']!=fingerprint
        or row['intent']['create_body']!=body):_deny('MANUAL_COMMON_RESPONSE_BINDING_CHANGED')
    return ledger._retain_create_response(intent_id,reply,redaction_values=redaction_values,
        decoded_business_bytes_sha256=decoded_business_bytes_sha256)

def common_detail_id(payload,*,store=None,connection=None):
    """Resolve a provider identity only from the immutable native CREATE record."""
    binding=(payload.get('r3_stage_binding') or {}).get('manual_common_binding')
    if binding is None:
        source=payload.get('source_product_identity') or {}
        if source.get('source_authority')=='manual-intake':_deny('MANUAL_COMMON_PROVIDER_BINDING_REQUIRED')
        return str(payload['product_id'])
    if type(binding) is not dict or set(binding)!={'intent_id','intent_digest','detail_id'}:_deny('MANUAL_COMMON_BINDING_INVALID')
    if store is None:
        from shared_platform.release_store import default_release_store
        store=default_release_store()
    if connection is None:row=ManualCommonCreateLedger(store.path).read(binding['intent_id'])
    else:
        ledger=_ledger_for(store.path,binding['intent_id'])
        row=ledger._projection(connection.execute(f'SELECT * FROM {ledger.table} WHERE intent_id=?',(binding['intent_id'],)).fetchone())
        if ledger.table!='manual_common_creations':ledger.verify_parent(row)
    if (row['state']!='VERIFIED' or row['offer_id']!=payload['product_id']
        or row['intent_digest']!=binding['intent_digest'] or row['detail_id']!=binding['detail_id']):_deny('MANUAL_COMMON_BINDING_UNVERIFIED')
    return row['detail_id']

def bound_publication_common(snapshot,*,store=None):
    """Follow the stored v4 snapshot to its exact completed COMMON parent."""
    if store is None:
        from shared_platform.release_store import default_release_store
        store=default_release_store()
    actual=store.approved_publication_snapshot(offer_id=snapshot['offer_id'],plan_id=snapshot['plan_id'])
    if actual!=snapshot:_deny('MANUAL_COMMON_STORED_PUBLICATION_SNAPSHOT_REQUIRED')
    market=store.get_plan(snapshot['plan_id']);stage=(market['payload'].get('r3_marketplace_binding') or {})
    common=store.get_plan(stage.get('common_plan_id',''))
    if (not common or common['product_id']!=snapshot['offer_id']
        or common['payload_digest']!=stage.get('common_payload_digest')):_deny('MANUAL_COMMON_STORED_PARENT_REQUIRED')
    identity=common_detail_id(common['payload'],store=store)
    binding=common['payload']['r3_stage_binding'].get('manual_common_binding')
    if binding is None:_deny('MANUAL_COMMON_STORED_PARENT_REQUIRED')
    return identity,ManualCommonCreateLedger(store.path).read(binding['intent_id'])

def new_common_variant_rows(payload,current):
    """Seed only the exact newly created blank COMMON in its one baseline EDIT.

    CREATE uses the documented title/itemNum DTO. Existing/partially populated
    variants can never enter this path. The original immutable EDIT builder
    still owns fields, labels, logistics and the cap1 dispatch boundary.
    """
    binding=(payload.get('r3_stage_binding') or {}).get('manual_common_binding')
    if binding is None or current.get('skuMap'):return None
    from shared_platform.release_store import default_release_store
    store=default_release_store();identity=common_detail_id(payload,store=store)
    row=ManualCommonCreateLedger(store.path).read(binding['intent_id'])
    if (ManualCommonCreateLedger._verify_detail(row,current)!=identity
        or _digest(current)!=row['result']['readback_digest']):_deny('MANUAL_COMMON_INITIAL_BLANK_DETAIL_CHANGED')
    from modules.products.release_adapters import _r3_common_frozen_sku_numbers,_expected_miaoshou_sku_labels
    numbers=_r3_common_frozen_sku_numbers(payload)
    if not numbers or sorted(numbers.values())!=sorted(row['intent']['model_skus']):_deny('MANUAL_COMMON_FROZEN_VARIANTS_CHANGED')
    labels=_expected_miaoshou_sku_labels(payload,set(numbers))
    commercial=payload['product_facts']['sku_commercial_facts']
    return {key:{'itemNum':model,'specLabel':labels[key],
                 'price':str(commercial.get(key,commercial.get(';'+key+';',{}))['cost_cny'])}
            for key,model in numbers.items()}
