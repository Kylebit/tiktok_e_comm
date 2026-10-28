"""One explicit EDIT successor, retaining the original unresolved claim.

Only startup pins and a genuine signed same-task v4 source link select this
path. An unchanged GET is never zero-write proof. No CREATE or marketplace
authority is introduced, and a consumed successor can only be read back.
"""
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re

from shared_platform.release_store import ReleaseAuthorizationError

DECISION_SCHEMA = 'native-common-baseline-successor-decision/v1'
RELATION_SCHEMA = 'native-common-baseline-successor/v1'
KEY = 'native_common_baseline_successor'


def _bytes(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def _digest(value):return sha256(_bytes(value)).hexdigest()
def _fail(code):raise ReleaseAuthorizationError(code)


@dataclass(frozen=True)
class PinnedBaselineDecision:
    path: Path
    sha256: str

    def read(self):
        if not self.path.is_absolute() or not re.fullmatch('[0-9a-f]{64}',str(self.sha256)):
            _fail('COMMON_BASELINE_SUCCESSOR_PIN_REQUIRED')
        raw=self.path.read_bytes()
        if sha256(raw).hexdigest()!=self.sha256:_fail('COMMON_BASELINE_SUCCESSOR_DECISION_CHANGED')
        from shared_platform.native_common_signing_context import _unique_object
        value=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=_unique_object)
        required=dict(schema_version=DECISION_SCHEMA,decision_status='USER_AUTHORIZED',actor='Kyle',
            action_class='AUTHORIZE_ONE_COMMON_BASELINE_EDIT_SUCCESSOR',maximum_new_edit_attempts=1,
            combined_edit_attempt_cap=2,combined_create_attempt_cap=3,further_create_authorized=False,
            original_unknown_retained=True,original_outcome='UNKNOWN',zero_write_proven=False,
            same_task_and_preparation_root_required=True,candidate_content_changes_authorized=False,
            marketplace_approval=False,standing_authority=False,new_unknown_stops=True)
        if type(value) is not dict or any(type(value.get(k)) is not type(v) or value.get(k)!=v for k,v in required.items()):
            _fail('COMMON_BASELINE_NEW_EXPLICIT_EDIT_DECISION_REQUIRED')
        for name in ('original_adoption_digest','original_payload_digest','original_ledger_sha256','current_detail_sha256'):
            if not re.fullmatch('[0-9a-f]{64}',str(value.get(name))):_fail('COMMON_BASELINE_SUCCESSOR_EXACT_SCOPE_REQUIRED')
        for name in ('task_id','offer_id','original_plan_id','original_run_id','common_detail_id'):
            if type(value.get(name)) is not str or not value[name]:_fail('COMMON_BASELINE_SUCCESSOR_EXACT_SCOPE_REQUIRED')
        return value


def installed_decision(config):
    pin=config.get('agent_prepared_common_baseline_decision')
    if pin is None:return None
    if type(pin) is not dict or set(pin)!={'path','sha256'}:_fail('COMMON_BASELINE_SUCCESSOR_PIN_REQUIRED')
    reader=PinnedBaselineDecision(Path(pin['path']),pin['sha256']);reader.read();return reader


def original_snapshot(db,decision):
    run=db.execute('SELECT * FROM release_runs WHERE run_id=? AND plan_id=?',
        (decision['original_run_id'],decision['original_plan_id'])).fetchone()
    plan=db.execute('SELECT * FROM release_plans WHERE plan_id=?',(decision['original_plan_id'],)).fetchone()
    target=db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',
        (decision['original_run_id'],'miaoshou:COMMON')).fetchone()
    if (run is None or plan is None or target is None or run['approval_id'] is not None
        or run['technical_execution_state']!='UNKNOWN' or target['status']!='RUNNING' or target['attempts']!=1
        or plan['product_id']!=decision['offer_id'] or plan['payload_digest']!=decision['original_payload_digest']):
        _fail('COMMON_BASELINE_SUCCESSOR_ORIGINAL_UNKNOWN_CHANGED')
    from shared_platform import native_common_technical_execution as technical
    facts=technical._facts_for_existing_run(db,decision['original_plan_id'],decision['original_run_id'])
    if technical._provider_id(db,facts)!=decision['common_detail_id']:_fail('COMMON_BASELINE_SUCCESSOR_COMMON_ID_CHANGED')
    snapshot={'run':dict(run),'target':dict(target)}
    for table in ('release_target_submissions','release_target_failure_events','release_target_readbacks'):
        snapshot[table]=[dict(row) for row in db.execute('SELECT * FROM '+table+' WHERE run_id=? ORDER BY target_label',
            (run['run_id'],))]
    if any(snapshot[t] for t in ('release_target_submissions','release_target_failure_events','release_target_readbacks')):
        _fail('COMMON_BASELINE_SUCCESSOR_ORIGINAL_RECEIPTS_CHANGED')
    if _digest(snapshot)!=decision['original_ledger_sha256']:_fail('COMMON_BASELINE_SUCCESSOR_ORIGINAL_UNKNOWN_CHANGED')
    return plan,snapshot


def relation(reader):
    if type(reader) is not PinnedBaselineDecision:_fail('COMMON_BASELINE_NEW_EXPLICIT_EDIT_DECISION_REQUIRED')
    decision=reader.read()
    return {**{name:decision[name] for name in ('task_id','offer_id','original_adoption_digest','original_plan_id',
        'original_run_id','original_payload_digest','original_ledger_sha256','common_detail_id','current_detail_sha256')},
        'schema_version':RELATION_SCHEMA,'decision_path':str(reader.path),'decision_sha256':reader.sha256}


def successor_payload(original,reader):
    decision=reader.read()
    if original.get('plan_id')!=decision['original_plan_id'] or _digest(original)!=decision['original_payload_digest']:
        _fail('COMMON_BASELINE_SUCCESSOR_FROZEN_PAYLOAD_CHANGED')
    if KEY in original:_fail('COMMON_BASELINE_SUCCESSOR_CHAIN_FORBIDDEN')
    payload=deepcopy(original);payload[KEY]=relation(reader)
    from shared_platform.publication_r3_image_bridge import common_stage_plan_id
    payload['plan_id']=common_stage_plan_id(payload,offer_id=payload['product_id'])
    return payload


def _service(store):
    from modules.products import server
    from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
    service=getattr(server,'_AGENT_PREPARED_COMMON_SERVICE',None)
    if (type(service) is not AgentPreparedCommonService or service.store.path.resolve()!=store.path.resolve()
        or type(getattr(service,'baseline_decision',None)) is not PinnedBaselineDecision):
        _fail('COMMON_BASELINE_NEW_EXPLICIT_EDIT_DECISION_REQUIRED')
    task,adoption=service._current()  # Genuine HMAC/event/Git v4, current R1/R2.
    decision=service.baseline_decision.read()
    if (task['task_id']!=decision['task_id'] or _digest(adoption)!=decision['original_adoption_digest']
        or adoption['offer_id']!=decision['offer_id']):_fail('COMMON_BASELINE_SUCCESSOR_TASK_CHANGED')
    return service


def select_payload(store,payload):
    """Read-only projection selected only by installed service configuration."""
    from modules.products import server
    candidate=getattr(server,'_AGENT_PREPARED_COMMON_SERVICE',None)
    if getattr(candidate,'colormap_decision',None) is not None:
        from shared_platform.native_common_colormap_correction import select_payload as corrected
        return corrected(store,payload)
    if getattr(candidate,'baseline_decision',None) is None:return payload
    service=_service(store)
    if payload['product_id']!=service.baseline_decision.read()['offer_id']:return payload
    with store._connect_readonly() as db:
        db.execute('BEGIN');original_snapshot(db,service.baseline_decision.read())
    return successor_payload(payload,service.baseline_decision)


def context(store,db,payload):
    from shared_platform.native_common_colormap_correction import KEY as color_key,context as corrected
    if color_key in payload:return corrected(store,db,payload)
    if KEY not in payload:return None
    service=_service(store);reader=service.baseline_decision;decision=reader.read()
    original,_=original_snapshot(db,decision)
    if payload!=successor_payload(json.loads(original['payload_json']),reader):
        _fail('COMMON_BASELINE_SUCCESSOR_FROZEN_PAYLOAD_CHANGED')
    return decision


def unresolved_after_authorized_successor(store,db,payload,census):
    """Retain census truth; permit only the one explicitly accepted old risk."""
    decision=context(store,db,payload)
    excluded=retained_run_ids(decision)
    return [row for row in census['unresolved_attempts'] if row.get('run_id') not in excluded]


def retained_run_ids(decision):
    """Only a verified context may supply the exact retained risk identities."""
    if not decision:return frozenset()
    return frozenset([decision['original_run_id']]+([decision['rejected_run_id']] if 'rejected_run_id' in decision else []))


def check_preimage(store,db,payload,current_response):
    decision=context(store,db,payload)
    if decision and _digest((current_response.get('data') or {}).get('editCommonCollectBoxDetail'))!=decision['current_detail_sha256']:
        _fail('COMMON_BASELINE_SUCCESSOR_OFFICIAL_PREIMAGE_CHANGED')


def _domain_receipt_snapshot(task):
    return deepcopy(dict(task_id=task['task_id'],version=task['version'],scope=task['scope'],
        checkpoint=task.get('checkpoint') or {},step=task['current_step'],state=task['execution_state'],
        action=task.get('pending_observation') or task.get('required_action'),worker=task.get('worker')))


def prepare_domain_receipt(engine,task_id,receipt,verifier):
    """Only this installed successor verifies outside the TaskDB write lock.

    The complete source/HMAC/domain verifier still runs. Its exact Task/action
    snapshot is then compared against the same locked connection by the engine.
    Ordinary domain receipts retain their existing path.
    """
    import sys
    server=sys.modules.get('modules.products.server')
    if server is None or getattr(server,'_AGENT_PREPARED_COMMON_SERVICE',None) is None:return None
    from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
    service=getattr(server,'_AGENT_PREPARED_COMMON_SERVICE',None)
    if (type(service) is not AgentPreparedCommonService or getattr(service,'engine',None) is not engine
        or getattr(service,'task_id',None)!=task_id or type(getattr(service,'baseline_decision',None)) is not PinnedBaselineDecision):
        return None
    task,original=service._current()
    decision=service.baseline_decision.read()
    if (decision['task_id']!=task_id or decision['original_adoption_digest']!=_digest(original)):
        _fail('COMMON_BASELINE_SUCCESSOR_TASK_CHANGED')
    snapshot=_domain_receipt_snapshot(task);action=snapshot['action'] or {}
    if receipt.get('action_id')!=action.get('action_id') or not verifier(receipt,action.get('receipt_binding') or {}):
        raise ValueError('domain receipt is not verified for current frozen scope')
    after,_=service._current()
    if _domain_receipt_snapshot(after)!=snapshot:
        raise ValueError('DOMAIN_RECEIPT_CURRENT_TASK_CHANGED')
    return dict(task=snapshot,receipt=deepcopy(receipt),binding=deepcopy(action['receipt_binding']))


def verify_frozen_sources(profile,store,original,reader,*,colormap_reader=None):
    """Recheck signed immutable preparation, never assert empty old history.

    New-task adoption deliberately requires no business run. A same-task
    continuation instead revalidates its actual retained R1/R2/paid sources,
    while keeping that historical empty-history observation unchanged.
    """
    from shared_platform.immutable_approval_files import require_local_path
    from shared_platform import publication_r3_image_bridge as bridge
    from shared_platform.native_common_budget_facts import (_stored_preparation,_source_payload)
    from shared_platform.publication_paid_requests import load_paid_context
    from shared_platform.agent_prepared_publication_adoption import _verify_paid_history
    root=Path(profile.root);facts=original['facts'];source=facts['preparation_source'];offer=original['offer_id']
    directory=root/'reports/product-preparation'/offer
    pins=facts['source_files']
    required={str((directory/name).relative_to(root)) for name in bridge.R2_DOCUMENTS.values()}
    required.add(str((directory/'paid-requests/events.jsonl').relative_to(root)))
    if not required.issubset(pins):_fail('COMMON_BASELINE_SUCCESSOR_FROZEN_SOURCE_PINS_REQUIRED')
    for relative,expected in pins.items():
        path=root/relative;require_local_path(path,root=root)
        if sha256(path.read_bytes()).hexdigest()!=expected:_fail('COMMON_BASELINE_SUCCESSOR_FROZEN_SOURCE_CHANGED')
    old=facts['original_profile'];require_local_path(old['path'],root=Path(old['path']).parent)
    if sha256(Path(old['path']).read_bytes()).hexdigest()!=old['sha256']:
        _fail('COMMON_BASELINE_SUCCESSOR_FROZEN_SOURCE_CHANGED')
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        prepared=_stored_preparation(db,offer,facts['prepared_reference'])
        if source!=_source_payload(db,prepared,source['round1_snapshot']):
            _fail('COMMON_BASELINE_SUCCESSOR_NATIVE_R1_CHANGED')
        decision=reader.read();original_snapshot(db,decision)
        expected_runs={decision['original_run_id']}
        if colormap_reader is not None:
            from shared_platform.native_common_colormap_correction import rejected_snapshot
            rejected_snapshot(db,colormap_reader);expected_runs.add(colormap_reader.read()['rejected_run_id'])
        runs=db.execute('SELECT r.run_id FROM release_runs r JOIN release_plans p USING(plan_id) WHERE p.product_id=?',(offer,)).fetchall()
        if (decision['offer_id']!=offer or {row['run_id'] for row in runs}!=expected_runs
            or db.execute('SELECT 1 FROM release_approvals a JOIN release_plans p USING(plan_id) WHERE p.product_id=?',(offer,)).fetchone()):
            _fail('COMMON_BASELINE_SUCCESSOR_UNBOUND_BUSINESS_HISTORY')
    documents={key:json.loads((directory/name).read_bytes()) for key,name in bridge.R2_DOCUMENTS.items()}
    documents['first_review']=prepared['packet']
    if bridge.validate_r2_identity(documents)!=facts['r2_identity']:_fail('COMMON_BASELINE_SUCCESSOR_R2_CHANGED')
    paid,proof=_verify_paid_history(load_paid_context(offer_id=offer,round1=source['round1_snapshot'],repo_root=root))
    if paid!=facts['paid_summary'] or proof!=facts.get('image_unknown_successor_proof'):
        _fail('COMMON_BASELINE_SUCCESSOR_PAID_HISTORY_CHANGED')
    return dict(status='EXACT_ORIGINAL_UNKNOWN_RETAINED_NEW_EDIT_DECISION_BOUND',
        empty_business_history_verified=False,original_run_id=decision['original_run_id'],
        original_ledger_sha256=decision['original_ledger_sha256'])
