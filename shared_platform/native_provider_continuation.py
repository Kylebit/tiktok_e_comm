"""Continue only unexecuted approved steps after an original retained CREATE.

Runs within the original native attempt, decision, platform lock and durable
ledger. It never calls the global resolver or create_publish_task. A lost wire,
ambiguous identity, changed source, repeated operation or exhausted ceiling is
a HOLD. The original UNKNOWN reservation is never changed or released; only
its separately append-only confirmed outcome may be recorded after exact READ.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from domains.channel_operations.release_executor import AdapterExecutionResult
from shared_platform.final_review_server_admission import ApprovalBlocked


def _tiktok_active(active):
    from shared_platform.native_sole_final_execution import _ACTIVE,_NativeExecution
    return (type(active) is _NativeExecution and _ACTIVE.get() is active
        and active.platform_scope==('TIKTOK',) and active.service.store is active.store
        and active.service._actor()['owner_sid']==active.owner_sid)


def retained_tiktok_rows(root,identity):
    """Original bytes only. No journal initialization, response replay or I/O."""
    import gzip
    from shared_platform.publication_provider_journal import ProviderResponseJournal,_checked_runtime_path,_user_identity
    journal=ProviderResponseJournal(_checked_runtime_path(Path(root)),identity)
    _region_need(journal.path.is_file(),'ORIGINAL_TIKTOK_JOURNAL_REQUIRED')
    _checked_runtime_path(journal.path)
    if os.name=='nt':
        from shared_platform.local_operator_session import verify_owner_only
        verify_owner_only(journal.root,_user_identity())
    import sqlite3
    with sqlite3.connect(journal.path.as_uri()+'?mode=ro',uri=True,timeout=5) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        rows=[dict(r) for r in db.execute('SELECT * FROM provider_response_intents ORDER BY started_at')]
    result=[]
    from shared_platform.native_final_mutation_budget import _json,_sha
    for row in rows:
        request=json.loads(row['request_identity_json'])
        _region_need(json.loads(row['identity_json'])==identity and _sha(_json(request))==row['key']
            and request.get('provider')=='miaoshou' and request.get('method')=='POST','ORIGINAL_TIKTOK_JOURNAL_SCOPE_CHANGED')
        raw=row['response_body'];outcome='UNKNOWN'
        if raw is not None:
            raw=bytes(raw)
            _region_need(len(raw)<=2_000_000 and hashlib.sha256(raw).hexdigest()==row['response_sha256'],'ORIGINAL_TIKTOK_RESPONSE_CHANGED')
            if raw[:2]==b'\x1f\x8b':raw=gzip.decompress(raw)
            _region_need(len(raw)<=2_000_000,'ORIGINAL_TIKTOK_RESPONSE_CHANGED')
            value=json.loads(raw.decode('utf-8'))
            if row['phase']=='RESPONSE_RETAINED' and row['http_status']==200:
                outcome='ACCEPTED' if str(value.get('result') or '').lower()=='success' else 'REJECTED'
        result.append({'key':row['key'],'request':request,'response_sha256':row['response_sha256'],
            'outcome':outcome,'phase':row['phase'],'http_status':row['http_status']})
    return result


@dataclass(frozen=True)
class _NativeTikTokContinuationProof:
    active: object
    facts_json: str
    source_pin: tuple | None = None

    def validate(self,store,db,row):
        from shared_platform.native_final_mutation_budget import _json
        _region_need(_tiktok_active(self.active) and self.active.store is store,'CURRENT_NATIVE_TIKTOK_ACTOR_REQUIRED')
        expected=json.loads(self.facts_json)
        _region_need(self.active.plan_id==expected['binding']['plan_id']
            and self.active.decision_id==expected['binding']['decision_id'],'ORIGINAL_TIKTOK_SCOPE_CHANGED')
        _region_need(dict(row)==expected['target'],'ORIGINAL_TIKTOK_TARGET_CHANGED')
        current=_tiktok_continuation_facts(self.active,db,expected['binding'],row['target_label'],self.source_pin)
        _region_need(_json(current)==self.facts_json,'ORIGINAL_TIKTOK_CONTINUATION_CHANGED')
        self.active.service._decision(db,expected['binding']['decision_id'],owner_sid=self.active.owner_sid)
        if expected['mode']=='RETAINED_CLAIM':
            record=expected['base'][-1]
            db.execute('INSERT INTO native_final_budget_outcomes VALUES(?,?,?)',
                (expected['scope_key'],record['sequence'],'ACCEPTED'))
        elif expected['mode']=='REPAIR_ACCEPTED' and not expected['repair']['outcomes']:
            db.execute('INSERT INTO native_save_repair_outcomes VALUES(?,?)',(expected['grant']['grant_id'],'ACCEPTED'))
        return {'schema_version':'native-original-tiktok-continuation/v1','mode':expected['mode'],
            'original_attempt':row['attempts'],'original_reservations_and_grant_preserved':True,
            'known_pre_submit_zero':False,'facts_sha256':hashlib.sha256(self.facts_json.encode()).hexdigest()}


def _tiktok_continuation_facts(active,db,binding,label,source_pin=None):
    from shared_platform.native_final_mutation_budget import _json,_sha
    from shared_platform.native_prepared_v4_targets import _file_identity
    from shared_platform import native_known_rejected_save_repair as repair
    target=db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',(binding['run_id'],label)).fetchone()
    _region_need(target is not None and target['status']=='FAILED' and not target['external_id'],'ORIGINAL_TIKTOK_FAILED_REQUIRED')
    for table in ('release_target_submissions','release_target_readbacks','release_target_repairs','release_target_retry_operations'):
        _region_need(not db.execute('SELECT 1 FROM '+table+' WHERE run_id=? AND target_label=?',(binding['run_id'],label)).fetchone(),
            'TIKTOK_ACCEPTED_OR_REPAIRED_NO_REPLAY')
    scopes=db.execute("SELECT * FROM native_final_budget_scopes WHERE plan_id=? AND platform='TIKTOK'",(binding['plan_id'],)).fetchall()
    _region_need(len(scopes)==1,'ORIGINAL_TIKTOK_SCOPE_REQUIRED');scope=scopes[0];frozen=json.loads(scope['binding_json'])
    _region_need(_sha(scope['binding_json'])==scope['binding_sha256'] and all(frozen[k]==binding[k]
        for k in ('plan_id','snapshot_digest','candidate_digest','decision_id')) and frozen['per_target_maximum']==3
        and label in frozen['target_labels'],'ORIGINAL_TIKTOK_BUDGET_CHANGED')
    base=repair._base_rows(db,scope['scope_key'],label)
    mapping=_file_identity(run_id=binding['run_id'],plan_id=binding['plan_id'],decision_id=binding['decision_id'],snapshot_digest=binding['snapshot_digest'])
    root=Path(active.runtime.supervisor.root)
    cp_path=root/binding['offer_id']/str(binding['product_revision'])/mapping['file_run_id']/'tiktok-draft-checkpoint.json'
    cp=json.loads(cp_path.read_bytes());copy=dict(cp);supplied=copy.pop('checkpoint_digest',None)
    _region_need(supplied=='sha256:'+_sha(_json(copy)) and cp['run_id']==mapping['file_run_id']
        and cp['offer_id']==binding['offer_id'] and cp['snapshot_digest']==binding['snapshot_digest'],'ORIGINAL_TIKTOK_CP_CHANGED')
    events=[e for e in cp['events'] if e['target_label']==label]
    identity=dict(run_id=mapping['file_run_id'],report_id=mapping['file_report_id'],platform='TIKTOK',
        target_labels=[label],snapshot_digest=binding['snapshot_digest'],offer_id=binding['offer_id'],plan_id=binding['plan_id'])
    wire=retained_tiktok_rows(root,identity)
    _region_need(not any('/save_move_collect_task' in r['request']['path'] or r['outcome']=='UNKNOWN' for r in wire),
        'TIKTOK_UNKNOWN_OR_PUBLISHED_REQUIRES_RECONCILIATION')
    failures=[dict(r) for r in db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? ORDER BY attempt',
        (binding['run_id'],label))]
    _region_need([r['attempt'] for r in failures]==list(range(1,target['attempts']+1))
        and all(_sha(r['evidence_json'])==r['evidence_digest'] for r in failures),'ORIGINAL_TIKTOK_FAILURE_HISTORY_CHANGED')
    result={'binding':binding,'target':dict(target),'scope_key':scope['scope_key'],'base':base,'events':events,
        'failures':failures,'wire':wire}
    grant=db.execute('SELECT * FROM native_save_repair_grants WHERE scope_key=? AND target_label=?',(scope['scope_key'],label)).fetchone()
    if grant is not None:
        body=json.loads(grant['body_json'])
        _region_need(_sha(grant['body_json'])==grant['body_sha256']==grant['grant_id'],'ORIGINAL_REPAIR_GRANT_CHANGED')
        _region_need(target['attempts']>=2 and len(failures)==target['attempts'],'ORIGINAL_REPAIR_ATTEMPTS_CHANGED')
        repair.validate_original(db,body,target_status='FAILED',attempt=target['attempts'],continuation=True,source_pin=source_pin)
        repair.validate_root_addendum(body,source_pin=source_pin)
        aux={name:[dict(r) for r in db.execute('SELECT * FROM native_save_repair_'+name+' WHERE grant_id=?',(grant['grant_id'],))]
            for name in ('starts','reservations','dispatches','outcomes','verifications')}
        _region_need(aux['starts']==[{'grant_id':grant['grant_id'],'target_attempt':2}]
            and not aux['verifications'] and aux['reservations'] in ([],[
                {'grant_id':grant['grant_id'],'operation':'save_draft','attempt_count':1}])
            and aux['outcomes'] in ([],[{'grant_id':grant['grant_id'],'outcome':'ACCEPTED'}]),'ORIGINAL_REPAIR_START_CHANGED')
        repair.validate_events(events[:3],body)
        extra=[r for r in wire if '/save_' in r['request']['path'] and r['key']!=body['provider_rejection']['key']]
        if aux['dispatches']:
            _region_need(len(aux['reservations'])==len(aux['dispatches'])==len(extra)==1
                and extra[0]['outcome']=='ACCEPTED' and (len(events)==4 or len(events)==5 and events[3]['outcome']=='UNKNOWN')
                and events[-1]['operation']=='SAVE_DRAFT' and events[-1]['outcome']=='ACCEPTED',
                'REPAIR_DISPATCH_REQUIRES_EXACT_RETAINED_ACCEPTANCE')
            result['mode']='REPAIR_ACCEPTED'
        else:
            _region_need(not extra and not aux['outcomes'] and len(aux['reservations'])<=1
                and (len(events)==3 or len(events)==4 and events[-1]['operation']=='SAVE_DRAFT' and events[-1]['outcome']=='UNKNOWN'),
                'REPAIR_UNDISPATCHED_PREFIX_CHANGED')
            result['mode']='REPAIR_RESERVED' if aux['reservations'] else 'REPAIR_UNRESERVED'
        _region_need(all((e['detail_id'],e['shop_id'])==(body['detail_id'],body['shop_id']) for e in events),
            'ORIGINAL_REPAIR_CP_IDENTITY_CHANGED')
        result.update(grant=dict(grant),repair=aux)
    else:
        _region_need(target['attempts']==1 and len(failures)==1 and len(base)==2 and len(events)==2
            and [(r['operation'],r['outcome']) for r in base]==[('create_platform_draft','ACCEPTED'),('claim_draft','UNKNOWN')]
            and base[-1]['request_digest'] and [(e['operation'],e['outcome']) for e in events]==[
                ('CREATE_DRAFT','ACCEPTED'),('CLAIM_TO_SHOP','ACCEPTED')],'EXACT_RETAINED_CLAIM_REQUIRED')
        claim=[r for r in wire if r['request']['path'].endswith('/claim_to_shop')]
        from modules.miaoshou.tiktok_v4_drafts import EXPECTED_SHOP_ID_BY_TARGET
        _region_need(len(wire)==2 and len(claim)==1 and all(r['outcome']=='ACCEPTED' for r in wire)
            and wire[0]['request']['path'].endswith('/claimed') and wire[1]['request']['path'].endswith('/claim_to_shop')
            and len({(e['detail_id'],e['shop_id']) for e in events})==1 and events[0]['shop_id']==EXPECTED_SHOP_ID_BY_TARGET[label],
            'EXACT_RETAINED_CLAIM_RESPONSE_REQUIRED')
        result['mode']='RETAINED_CLAIM'
    return result


def tiktok_continuation_proofs(active,gate):
    """Mint exact original-attempt proofs after the normal current native gate."""
    if not _tiktok_active(active):return {}
    from shared_platform.native_new_task_v4_targets import scope
    from shared_platform.native_final_mutation_budget import _json
    from shared_platform import native_known_rejected_save_repair as repair
    plan=gate['plan'];scope(active,plan)
    with active.store._connect_readonly() as source_db:
        if not repair.check_schema(source_db,optional=True):return {}
        grants=[json.loads(r[0]) for r in source_db.execute('SELECT body_json FROM native_save_repair_grants WHERE run_id=?',
            ('release-run:'+plan['payload_digest'][:24],))]
    # Full registered source validation occurs outside a ReleaseStore write or
    # retained read transaction. Inside claim only its immutable pin is checked.
    source_pins={body['target_label']:repair.retained_source_pin(active,body) for body in grants}
    with active.store._connect_readonly() as db:
        db.execute('BEGIN');receipt=active.service._decision(db,active.decision_id,owner_sid=active.owner_sid)
        frozen=json.loads(db.execute('SELECT snapshot_json FROM approved_publication_snapshots WHERE plan_id=?',(plan['plan_id'],)).fetchone()[0])
        decision=db.execute('SELECT review_json FROM native_sole_final_decisions WHERE decision_id=?',(active.decision_id,)).fetchone()
        binding=dict(plan_id=plan['plan_id'],run_id='release-run:'+plan['payload_digest'][:24],decision_id=active.decision_id,
            snapshot_digest=receipt['snapshot_digest'],candidate_digest=json.loads(decision['review_json'])['candidate_digest'],
            offer_id=frozen['offer_id'],product_revision=frozen['product_revision'])
        proofs={}
        for target in (gate['run'] or {}).get('targets',()):
            label=target['target_label']
            if target['status']!='FAILED' or not label.startswith('tiktok:'):continue
            try:
                pin=source_pins.get(label)
                facts=_tiktok_continuation_facts(active,db,binding,label,pin)
            except (ValueError,OSError,KeyError,TypeError,RuntimeError):continue
            proofs[label]=_NativeTikTokContinuationProof(active,_json(facts),pin)
    active._tiktok_continuation_proofs=proofs
    return proofs


def _region_need(value,code):
    if not value:raise ValueError('NATIVE_REGIONAL_UNSENT_'+code)


def _regional_active(active):
    from shared_platform.native_sole_final_execution import _ACTIVE,_NativeExecution
    return (type(active) is _NativeExecution and _ACTIVE.get() is active
        and active.platform_scope==('SHOPEE',)
        and active.service.store is active.store
        and active.service._actor()['owner_sid']==active.owner_sid)


def _regional_unsent_db_facts(db,run_id,label,binding):
    """A regional absence, never a claim that the accepted Global was zero."""
    from shared_platform.native_final_mutation_budget import _json,_sha
    target=db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',(run_id,label)).fetchone()
    run=db.execute('SELECT plan_id FROM release_runs WHERE run_id=?',(run_id,)).fetchone()
    _region_need(target is not None and run is not None and run['plan_id']==binding['plan_id']
        and label in binding['target_labels'] and label.startswith('shopee:')
        and (target['attempts']==1 and target['status'] in {'FAILED','RUNNING'}
             or target['attempts']==2 and target['status']=='FAILED' and target['error']=='database is locked')
        and not target['external_id'],'ORIGINAL_FIRST_REGIONAL_ATTEMPT_REQUIRED')
    for table in ('release_target_submissions','release_target_readbacks','release_target_repairs','release_target_retry_operations'):
        _region_need(not db.execute('SELECT 1 FROM '+table+' WHERE run_id=? AND target_label=?',(run_id,label)).fetchone(),
            'REGIONAL_RESULT_REQUIRES_RECONCILIATION')
    failures=[dict(r) for r in db.execute('SELECT * FROM release_target_failure_events WHERE run_id=? AND target_label=? ORDER BY attempt',(run_id,label))]
    # The local SQLite failure can occur before the adapter returns typed
    # evidence. Retain that missing attempt-2 row explicitly; do not relabel
    # the first preparation failure as the later lock failure.
    _region_need(len(failures)==(1 if target['status']=='FAILED' else 0),'ORIGINAL_FAILURE_CARDINALITY_CHANGED')
    for event in failures:
        value=json.loads(event['evidence_json'])
        _region_need(event['attempt']==1 and _sha(event['evidence_json'])==event['evidence_digest']
            and value.get('schema_version')=='native-scoped-v4-target-result/v1'
            and value.get('target_label')==label and value.get('status')=='FAILED'
            and value.get('outcome_unknown') is False and value.get('reconciliation_required') is False,
            'UNKNOWN_FAILURE_REQUIRES_RECONCILIATION')
        identity=value.get('checkpoint_file_identity') or {}
        _region_need(identity.get('decision_id')==binding['decision_id']
            and identity.get('plan_id')==binding['plan_id'] and identity.get('release_run_id')==run_id
            and identity.get('execution_snapshot_digest')==binding['snapshot_digest'],
            'ORIGINAL_FAILURE_SCOPE_CHANGED')
    scopes=db.execute('SELECT * FROM native_final_budget_scopes WHERE plan_id=? AND platform=?',(binding['plan_id'],'SHOPEE')).fetchall()
    _region_need(len(scopes)==1,'ORIGINAL_BUDGET_SCOPE_REQUIRED');scope=dict(scopes[0])
    _region_need(scope['binding_json']==_json(binding) and scope['binding_sha256']==_sha(_json(binding)),
        'FROZEN_BUDGET_BINDING_CHANGED')
    rows=[dict(r) for r in db.execute('SELECT r.*,d.request_digest,o.outcome FROM native_final_budget_reservations r '
        'LEFT JOIN native_final_budget_dispatches d USING(scope_key,sequence) '
        'LEFT JOIN native_final_budget_outcomes o USING(scope_key,sequence) WHERE r.scope_key=? ORDER BY r.sequence',(scope['scope_key'],))]
    _region_need(not any(r['budget_scope']=='target' and r['target_label']==label for r in rows),
        'REGIONAL_RESERVATION_REQUIRES_RECONCILIATION')
    shared=[r for r in rows if r['budget_scope']=='shared']
    _region_need(len(shared)>=3 and sum(r['attempt_count'] for r in shared)==binding['shared_maximum']
        and all(r['attempt_count']==1 and r['outcome']=='ACCEPTED' and re.fullmatch('[0-9a-f]{64}',r['request_digest'] or '') for r in shared)
        and [r['operation'] for r in shared]==['upload_image']*(len(shared)-2)+['create_global_item','initialize_models'],
        'ACCEPTED_GLOBAL_PREFIX_REQUIRED')
    return {'target':dict(target),'failures':failures,'binding':binding,'scope':scope,'shared':shared}


def _regional_file_pin(path):
    from shared_platform.shopee_publication_checkpoint import _io_path
    path=Path(path);io=_io_path(path)
    return str(path),hashlib.sha256(io.read_bytes()).hexdigest() if io.is_file() else None


def _assert_retired_executors(executors):
    """Absent-only: a reused/unreadable PID never proves this old owner retired."""
    _region_need(os.name=='nt' and type(executors) is list and executors
        and all(type(r) is dict and set(r)=={'process_id','creation_date'}
            and type(r['process_id']) is int and r['process_id']>0 and r['creation_date'] is None for r in executors)
        and len({r['process_id'] for r in executors})==len(executors),'EXACT_RETIRED_PROCESS_IDENTITIES_REQUIRED')
    filters=' OR '.join('ProcessId='+str(r['process_id']) for r in executors)
    command="$ErrorActionPreference='Stop'; $rows=@(Get-CimInstance Win32_Process -Filter '"+filters+"'); ConvertTo-Json -InputObject @($rows | Select-Object ProcessId) -Compress"
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],
            capture_output=True,text=True,check=True,timeout=15)
        rows=json.loads(result.stdout)
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        raise ValueError('NATIVE_REGIONAL_UNSENT_RETIREMENT_PROCESS_READ_FAILED') from error
    _region_need(rows==[],'ORIGINAL_EXECUTOR_STILL_PRESENT')


def _read_regional_retirement(active,binding,run_id,task_id,ref):
    from shared_platform.immutable_approval_files import require_local_path
    from shared_platform.native_unfrozen_source_successor import verified_explicit_origin
    from shared_platform.native_approved_source_successor import _events
    def pinned(value):
        _region_need(type(value) is dict and set(value)=={'path','sha256'},'OWNED_RETIREMENT_PIN_REQUIRED')
        path=Path(value['path']);require_local_path(path,root=path.parent);raw=path.read_bytes()
        _region_need(len(raw)<=2_000_000 and hashlib.sha256(raw).hexdigest()==value['sha256'],
            'OWNED_RETIREMENT_FILE_CHANGED')
        return json.loads(raw)
    doc=pinned(ref)
    _region_need(doc.get('schema')=='native-owned-executors-retired/v1'
        and doc.get('task_id')==task_id and doc.get('plan_id')==binding['plan_id']
        and doc.get('run_id')==run_id and doc.get('decision_id')==binding['decision_id']
        and doc.get('owner_sid')==active.owner_sid,'ORIGINAL_EXECUTOR_SCOPE_CHANGED')
    intent=pinned(doc['original_intent_ref']);launch=pinned(doc['original_launch_ref']);absence=pinned(doc['absence_read_ref'])
    _region_need(all(intent.get(k)==doc[k] for k in ('task_id','plan_id','decision_id'))
        and intent.get('source')==doc['source_head']==launch.get('source_head')
        and launch.get('pid') in [r['process_id'] for r in doc['executors']]
        and absence.get('schema')=='native-owned-executors-absence-read/v1'
        and absence.get('process_ids')==[r['process_id'] for r in doc['executors']]
        and absence.get('processes')==[] and absence.get('all_absent') is True
        and absence.get('read_succeeded') is True,'ORIGINAL_RETIREMENT_EVIDENCE_CHANGED')
    operations=active.runtime._operations;engine=operations.engine
    with engine.readonly_snapshot() as db:
        row=engine._row(db,task_id);verified_explicit_origin(db,row,engine.release)
        links=_events(db,task_id)
        _region_need(links and links[-1][1]['to_release']==engine.release
            and any(doc['source_head'] in {r['from_release']['code_version'],r['to_release']['code_version']}
                for _,r in links),'RETIRED_EXECUTOR_SOURCE_NOT_IN_ORIGINAL_CHAIN')
    _assert_retired_executors(doc['executors'])
    refs=[ref,doc['original_intent_ref'],doc['original_launch_ref'],doc['absence_read_ref']]
    return doc,tuple((r['path'],r['sha256']) for r in refs)


@dataclass(frozen=True)
class _RegionalUnsentProof:
    active: object
    facts_json: str
    file_pins: tuple
    official_json: str
    retirement_ref: tuple | None = None

    def validate(self,store,db,row):
        from shared_platform.native_final_mutation_budget import _json
        _region_need(_regional_active(self.active) and self.active.store is store,'CURRENT_NATIVE_ACTOR_REQUIRED')
        facts=json.loads(self.facts_json);binding=facts['binding'];official=json.loads(self.official_json)
        _region_need(self.active.plan_id==binding['plan_id'] and self.active.decision_id==binding['decision_id']
            and dict(row)==facts['target'] and official['official_regional_item_absent'] is True
            and official['official_existing_global_exact'] is True and official['new_shared_writes']==0,
            'ORIGINAL_SCOPE_OR_OFFICIAL_READ_CHANGED')
        _region_need(_json(_regional_unsent_db_facts(db,row['run_id'],row['target_label'],binding))==self.facts_json,
            'REGIONAL_PREIMAGE_CHANGED')
        _region_need(all(_regional_file_pin(path)==(path,digest) for path,digest in self.file_pins),
            'ORIGINAL_CHECKPOINT_CHANGED')
        retirement=None
        if row['status']=='RUNNING':
            _region_need(self.retirement_ref is not None,'OWNED_RETIREMENT_PIN_REQUIRED')
            ref=dict(zip(('path','sha256'),self.retirement_ref))
            retirement,_=_read_regional_retirement(self.active,binding,row['run_id'],official['task_id'],ref)
        return {'schema_version':'native-regional-unsent-continuation/v1','target_label':row['target_label'],
            'original_attempt':row['attempts'],'original_status':row['status'],'known_pre_submit_zero':False,
            'original_target':facts['target'],
            'settle_unrecorded_failure':row['status']=='FAILED' and row['attempts']==2,
            'regional_request_attempted':False,'shared_accepted_prefix_preserved':True,
            'scope_key':facts['scope']['scope_key'],'official':official,'file_pins':list(self.file_pins),
            'owned_retirement_ref':dict(zip(('path','sha256'),self.retirement_ref)) if retirement is not None else None,
            'original_facts_sha256':hashlib.sha256(self.facts_json.encode()).hexdigest()}


def regional_unsent_proofs(active,gate):
    """Read exact same-run unsent regions under the original SH partition lock.

    The trusted maintainer retires old owned executors before code succession.
    This is not a public proof intake or an UNKNOWN-request retry. Original
    first attempts and all shared reservations remain durable facts.
    """
    if not _regional_active(active):return {}
    from shared_platform.native_new_task_v4_targets import scope
    from shared_platform.release_store import _validated_publication_snapshot_row
    from shared_platform.r3_frozen_review_producer import read_stored_domain_graph
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    from shared_platform.native_final_mutation_budget import _DurableLedger,_json
    from shared_platform.native_prepared_v4_targets import _file_identity
    from shared_platform.native_canonical_provider_edges import shopee_dependencies
    from modules.shopee.global_v4_executor import project_shopee_global_v4_command,_verify_readback
    from shared_platform.publication_autopilot import validate_release_candidate_for_execution
    from modules.shopee.client import merchant_get
    from modules.shopee.skill_regions import _provider_error
    from shared_platform.shopee_publication_checkpoint import ShopeePublicationCheckpointStore,_io_path
    from types import SimpleNamespace
    plan=gate['plan'];_,task,_,_=scope(active,plan);rid='release-run:'+plan['payload_digest'][:24]
    labels=[t['target_label'] for t in (gate['run'] or {}).get('targets',())
        if t['target_label'] in [r['channel']+':'+r['site'] for r in gate['target_rows']]
        and t['target_label'].startswith('shopee:') and (
            t['attempts']==1 and t['status'] in {'FAILED','RUNNING'}
            or t['attempts']==2 and t['status']=='FAILED' and t.get('error')=='database is locked')]
    if not labels:return {}
    with active.store._connect_readonly() as db:
        db.execute('BEGIN');receipt=active.service._decision(db,active.decision_id,owner_sid=active.owner_sid)
        frozen=db.execute('SELECT * FROM approved_publication_snapshots WHERE plan_id=?',(plan['plan_id'],)).fetchone()
        snapshot=_validated_publication_snapshot_row(frozen,plan=plan)
        graph=read_stored_domain_graph(db,plan['plan_id'],native_reader=NativeCommonSourceReader(active.store))
        candidate=json.loads(graph.display_bytes)
    _region_need(snapshot['snapshot_digest']==receipt['snapshot_digest'],'ORIGINAL_SNAPSHOT_CHANGED')
    validate_release_candidate_for_execution(candidate,snapshot=snapshot,platform_scope=('SHOPEE',),target_labels=labels)
    budget=candidate['write_budget']['SHOPEE']
    binding=dict(plan_id=plan['plan_id'],snapshot_digest=snapshot['snapshot_digest'],platform='SHOPEE',
        target_labels=budget['target_labels'],shared_maximum=budget['shared_maximum'],
        per_target_maximum=budget['per_target_maximum'],payload_digest=plan['payload_digest'],
        candidate_digest=candidate['candidate_digest'],decision_id=active.decision_id)
    facts={}
    with active.store._connect_readonly() as db:
        db.execute('BEGIN')
        for label in labels:
            try:facts[label]=_regional_unsent_db_facts(db,rid,label,binding)
            except ValueError:continue
    if not facts:return {}
    retirement_ref=None;retirement_files=()
    if any(value['target']['status']=='RUNNING' for value in facts.values()):
        retirement_ref={'path':os.environ.get('ORBIT_NATIVE_REGIONAL_RETIREMENT_PATH'),
                        'sha256':os.environ.get('ORBIT_NATIVE_REGIONAL_RETIREMENT_SHA256')}
        _region_need(all(type(v) is str and v for v in retirement_ref.values()),'OWNED_RETIREMENT_PIN_REQUIRED')
        _,retirement_files=_read_regional_retirement(active,binding,rid,task['task_id'],retirement_ref)
    mapping=_file_identity(run_id=rid,plan_id=plan['plan_id'],decision_id=active.decision_id,snapshot_digest=snapshot['snapshot_digest'])
    request=SimpleNamespace(run_id=mapping['file_run_id'],report_id=mapping['file_report_id'],snapshot=snapshot,
        platform='SHOPEE',target_labels=tuple(facts))
    parent=Path(active.runtime.supervisor.root)/snapshot['offer_id']/str(snapshot['product_revision'])/mapping['file_run_id']
    global_path=parent/'shopee-global-checkpoint.json';global_pin=_regional_file_pin(global_path)
    _region_need(global_pin[1] is not None,'ORIGINAL_GLOBAL_CHECKPOINT_REQUIRED')
    checkpoint=json.loads(_io_path(global_path).read_bytes())
    _region_need(checkpoint['schema_version']=='shopee-global-v4-checkpoint/v1'
        and all(checkpoint[k]==getattr(request,k) for k in ('run_id','report_id'))
        and checkpoint['offer_id']==snapshot['offer_id'] and checkpoint['product_revision']==snapshot['product_revision'],
        'ORIGINAL_GLOBAL_CHECKPOINT_IDENTITY_CHANGED')
    ledger=_DurableLedger(store=active.store,binding=binding,target_label=next(iter(facts)))
    deps=shopee_dependencies(ledger,active.runtime.supervisor.root,[]);global_runtime=deps.global_item_id_resolver._runtime
    command=project_shopee_global_v4_command(snapshot);mapped=global_runtime.lookup_global_item_ids(command)
    _region_need(mapped and set(mapped)==set(checkpoint['model_skus'])
        and all(str(v)==checkpoint['global_item_id'] for v in mapped.values()),'EXACT_EXISTING_GLOBAL_MAPPING_REQUIRED')
    global_id=checkpoint['global_item_id'];item=global_runtime.read_global_item(global_id);models=global_runtime.read_global_models(global_id)
    _verify_readback(command,global_item_id=global_id,item=item,model_response=models,expected_image_ids=checkpoint['image_bindings'])
    _region_need({m['model_sku']:m['global_model_id'] for m in models['models']}==checkpoint['global_model_ids'],
        'EXACT_EXISTING_GLOBAL_MODELS_REQUIRED')
    ctx=deps.runtime.context(next(iter(facts)).split(':')[1])
    response=merchant_get('/api/v2/global_product/get_published_list',ctx.merchant_id,ctx.merchant_token,{'global_item_id':int(global_id)})
    _region_need(not _provider_error(response)[0] and type(response.get('response')) is dict
        and type(response['response'].get('published_item')) is list,'OFFICIAL_ASSOCIATIONS_REQUIRED')
    associations=response['response']['published_item'];proofs={}
    _region_need(all(type(r) is dict and type(r.get('shop_id')) is int and r['shop_id']>0
        and str(r.get('item_id') or '').isdigit() and re.fullmatch('[A-Z]{2}',str(r.get('shop_region') or '').upper())
        for r in associations) and len({r['shop_id'] for r in associations})==len(associations)
        and not any(response['response'].get(k) is True for k in ('more','has_next_page')),
        'COMPLETE_UNAMBIGUOUS_ASSOCIATIONS_REQUIRED')
    for label,value in facts.items():
        context=deps.runtime.context(label.split(':')[1])
        if any(str(r.get('shop_region') or '').upper()==context.region or r.get('shop_id')==context.shop_id for r in associations):continue
        regional=ShopeePublicationCheckpointStore(active.runtime.supervisor.root,SimpleNamespace(**{**vars(request),'target_labels':(label,)}))
        retained=regional.load()
        if retained['events'] or retained['targets']:continue
        files=(global_pin,_regional_file_pin(regional.path),_regional_file_pin(regional.legacy_path))
        if value['target']['status']=='RUNNING':files+=retirement_files
        official={'official_regional_item_absent':True,'official_existing_global_exact':True,
            'task_id':task['task_id'],
            'global_item_id':global_id,'global_model_ids':checkpoint['global_model_ids'],'shop_id':context.shop_id,
            'merchant_id':context.merchant_id,'new_shared_writes':0,
            'official_associations_sha256':hashlib.sha256(_json(response).encode()).hexdigest()}
        proofs[label]=_RegionalUnsentProof(active,_json(value),files,_json(official),
            tuple(retirement_ref[k] for k in ('path','sha256')) if value['target']['status']=='RUNNING' else None)
    _region_need(_regional_file_pin(global_path)==global_pin,'ORIGINAL_GLOBAL_CHECKPOINT_CHANGED')
    return proofs


def _need(value, code):
    if not value:raise ApprovalBlocked('NATIVE_ORIGINAL_CONTINUATION_'+code)


def _plan_actions(material):
    checks=material['checks'];actions=[]
    if not checks['exact_ordered_image_urls'] or not checks['image_identities_complete']:
        actions.extend(('UPLOAD_APPROVED_IMAGES','UPDATE_APPROVED_IMAGES'))
    if not checks['copy_exact']:actions.append('UPDATE_APPROVED_COPY')
    if ('UPLOAD_APPROVED_IMAGES' in actions or 'UPDATE_APPROVED_COPY' in actions
            or not checks['description_images_exact'] or not checks['description_type_extended']):
        actions.append('UPDATE_APPROVED_DESCRIPTION')
    if not checks['normal_status_exact']:actions.append('LIST_ORIGINAL_ITEM')
    return actions


_OPERATIONS={'UPLOAD_APPROVED_IMAGES':'upload_regional_image','UPDATE_APPROVED_IMAGES':'update_regional_images',
    'UPDATE_APPROVED_COPY':'update_regional_copy','UPDATE_APPROVED_DESCRIPTION':'update_description_media',
    'LIST_ORIGINAL_ITEM':'list_regional_item'}


def continue_original_result(active, adapter_request, native_request, result, ledger):
    """Return an actual verified adapter result, or None to retain UNKNOWN.

    This is not a FAILED-target retry API. The original native consumer may
    call it only while its initial claimed target is still RUNNING.
    """
    if (native_request.platform!='SHOPEE' or result.get('readback_completed') is not True
            or result.get('external_write_count') is not None):return None
    from shared_platform.native_sole_final_execution import _ACTIVE,_NativeExecution
    from shared_platform.native_new_task_v4_targets import _approved_inputs
    from shared_platform import native_sole_final_readback as readback
    from shared_platform.publication_provider_journal import retained_provider_executor
    from shared_platform.shopee_publication_checkpoint import ShopeePublicationCheckpointStore
    try:
        _need(type(active) is _NativeExecution and _ACTIVE.get() is active,'CURRENT_NATIVE_SCOPE_REQUIRED')
        plan,snapshot,candidate=_approved_inputs(active,adapter_request)
        _need(snapshot==native_request.snapshot,'FROZEN_SNAPSHOT_CHANGED')
        run=active.store.get_run('release-run:'+plan['payload_digest'][:24])
        target=next(t for t in run['targets'] if t['target_label']==adapter_request.target_label)
        _need(target['status']=='RUNNING' and target['attempts']==1,'ORIGINAL_RUNNING_ATTEMPT_REQUIRED')
        budget=ledger.snapshot();own=[r for r in budget['reservations'] if r['mutation_target_label']==target['target_label']]
        _need(not any(r['scope']=='shared' and r['outcome']=='UNKNOWN' for r in budget['reservations']),
              'SHARED_UNKNOWN_REQUIRES_READONLY_RECONCILIATION')
        unknown=[r for r in own if r['outcome']=='UNKNOWN']
        _need(len(unknown)==1 and unknown[0]['operation']=='create_publish_task','EXACT_ORIGINAL_CREATE_UNKNOWN_REQUIRED')
        facts={'plan':plan,'snapshot':snapshot,'target':target,'ledger':budget['reservations'],
               'failure':{'evidence_digest':None}}
        source=readback._provider_original_task(active,facts)
        responses=readback._provider_official_reads(active,source)
        material=readback._provider_verified_evidence(facts,source,responses,material_only=True)
        identity_checks={'item_identity_exact','global_linkage_exact','model_sku_set_exact','model_ids_present',
            'model_tiers_exact','model_prices_exact','item_sku_exact','applicable_logistics_enabled',
            'frozen_category_exact','parcel_exact','frozen_stock_exact'}
        _need(all(material['checks'][name] is True for name in identity_checks),'ORIGINAL_ITEM_IDENTITY_OR_FACTS_MISMATCH')
        pending=ledger._pending.get('create_publish_task')
        _need(pending is not None and pending['sequence']==unknown[0]['sequence'],
              'ORIGINAL_PENDING_RESERVATION_REQUIRED')
        with ledger._connect(readonly=True) as db:
            dispatch=db.execute('SELECT request_digest FROM native_final_budget_dispatches WHERE scope_key=? AND sequence=?',
                (ledger.scope_key,pending['sequence'])).fetchone()
        _need(dispatch is not None and source.get('native_request_digest')==dispatch['request_digest'],
              'ORIGINAL_WIRE_AND_LEDGER_REQUEST_CHANGED')
        # This appends confirmation of that SAME original request. Its initial
        # UNKNOWN reservation and charged attempt are immutable and retained.
        ledger.complete_reserved('create_publish_task','ACCEPTED')
        actions=_plan_actions(material)
        required=sum(len(material['image_urls']) if name=='UPLOAD_APPROVED_IMAGES' else 1 for name in actions)
        remaining=budget['limits']['per_target_maximum']-budget['attempts']['per_target'][target['target_label']]
        _need(required<=remaining,'REMAINING_FROZEN_BUDGET_INSUFFICIENT')
        _need(all(not any(r['operation']==_OPERATIONS[name] for r in own) for name in actions),
              'PRIOR_REMAINING_STEP_REQUIRES_READONLY_RECONCILIATION')
        checkpoint=ShopeePublicationCheckpointStore(native_request.checkpoint_root,native_request)
        checkpoint.record('DISPATCH',{'target_label':target['target_label'],'attempted':False,'accepted':True,
            'outcome':'ACCEPTED','provider_task_id':source['task_id'],'existing_item_id':material['item_id'],
            'message':'original task confirmed by exact official GET; original attempt and initial UNKNOWN preserved',
            'external_write_count':0})
        references=[]
        from shared_platform.native_canonical_provider_edges import shopee_dependencies
        runtime=shopee_dependencies(ledger,native_request.checkpoint_root,references).runtime
        context=runtime.context(source['resource']['shop_region'])
        _need(context.shop_id==source['resource']['shop_id']
            and str(context.merchant_id)==source['account']['merchant_id'],'ORIGINAL_ACCOUNT_CHANGED')
        ids=material['image_ids'];performed=[]
        def execute(_request):
            nonlocal ids
            for action in actions:
                # Recheck the unchanged full original decision before every
                # reservation. The ledger performs its own final short CAS.
                current,_,_=_approved_inputs(active,adapter_request)
                _need(current['payload_digest']==plan['payload_digest'],'CURRENT_SOURCE_CHANGED')
                operation=_OPERATIONS[action]
                if action=='UPLOAD_APPROVED_IMAGES':
                    value=runtime.upload_regional_images(context,material['image_urls'],
                        before_upload=lambda:ledger.reserve_target(target['target_label'],operation))
                    ids=tuple(value['image_ids'])
                    _need(len(ids)==len(material['image_urls']) and len(set(ids))==len(ids),'UPLOADED_IMAGE_IDENTITIES_INCOMPLETE')
                else:
                    ledger.reserve_target(target['target_label'],operation)
                    if action=='UPDATE_APPROVED_IMAGES':runtime.update_regional_images(context,material['item_id'],image_ids=ids)
                    elif action=='UPDATE_APPROVED_COPY':runtime.update_regional_copy(context,material['item_id'],title=material['title'],description=material['description'])
                    elif action=='UPDATE_APPROVED_DESCRIPTION':runtime.update_regional_description(context,material['item_id'],description=material['description'],image_ids=ids)
                    elif action=='LIST_ORIGINAL_ITEM':runtime.list_item(context,material['item_id'])
                    else:raise ApprovalBlocked('NATIVE_ORIGINAL_CONTINUATION_UNCLASSIFIED_ACTION')
                performed.append(action)
            return readback._provider_official_reads(active,source)
        final_responses=retained_provider_executor(execute)(native_request)
        evidence=readback._provider_verified_evidence(facts,source,final_responses)
        current,_,_=_approved_inputs(active,adapter_request)
        _need(current['payload_digest']==plan['payload_digest'],'CURRENT_SOURCE_CHANGED_AFTER_READ')
        _need(readback._provider_original_task(active,facts)==source,'ORIGINAL_RESPONSE_CHANGED')
        from shared_platform.catalog_publication_sync import capture_readback
        capture_readback(native_request,[row for target_row in evidence['canonical_platform_result']['targets']
            for row in target_row['evidence']['official_catalog_rows']])
        checkpoint.record('READBACK',{'target_label':target['target_label'],'attempted':False,'accepted':True,
            'outcome':'PUBLISHED','provider_task_id':source['task_id'],'item_id':material['item_id'],
            'message':'original task and only remaining frozen actions officially verified',
            'external_write_count':sum(len(material['image_urls']) if a=='UPLOAD_APPROVED_IMAGES' else 1 for a in performed)})
        canonical=evidence['canonical_platform_result']
        final_budget=ledger.snapshot()
        _need(any(r['operation']=='create_publish_task' and r['outcome']=='ACCEPTED'
            and r['sequence']==unknown[0]['sequence'] for r in final_budget['reservations']),
            'ORIGINAL_REQUEST_CONFIRMATION_REQUIRED')
        proof={'schema_version':'native-scoped-v4-target-result/v1','target_label':target['target_label'],
            'verified':True,'status':'PUBLISHED','outcome_unknown':False,'reconciliation_required':False,
            'request_attempted':True,'known_pre_submit_zero':False,'canonical_platform_result':canonical,
            'mutation_budget':final_budget,'external_writes_performed':[target['target_label']],
            'original_sdk_result':deepcopy(result),'provider_original_readback':evidence,
            'catalog_projection_failures':deepcopy(getattr(native_request.catalog_sink,'failures',[])),
            'approved_remaining_steps':{'planned':actions,'performed':performed,'same_original_attempt':True,
                'new_create_calls':0,'original_initial_unknown_preserved':True,
                'original_outcome_confirmation_appended':True}}
        return AdapterExecutionResult(True,True,'Original task and approved remaining steps verified',
            f"shopee:{context.shop_id}:item:{material['item_id']}",proof)
    except (ValueError,OSError,KeyError,TypeError,RuntimeError) as error:
        # Original target/result/checkpoint/UNKNOWN budget remain the durable
        # reconciliation source. Never fall back to the full publish executor.
        if type(result) is dict:
            reason=str(error)
            if not re.fullmatch(r'[A-Z][A-Z0-9_]{1,180}',reason):reason='NATIVE_ORIGINAL_CONTINUATION_LOCAL_HOLD'
            result['original_task_continuation']={'state':'HOLD','blocked_by':[reason],
                'next_action':'RECONCILE_ORIGINAL_TARGET_READONLY','new_create_calls':0,
                'original_unknown_preserved':True}
        return None
