"""One explicit source-only continuation of the same adopted task.

The original adoption, checkpoints, intent and paid history stay immutable.
There is no HTTP producer and no general task-version migration API.
"""
from datetime import datetime,timezone
from hashlib import sha256
import hmac,json,sqlite3,subprocess
from pathlib import Path
from shared_platform import agent_prepared_publication_adoption as adoption

SCHEMA='agent-prepared-source-code-successor/v1'
ALLOWED_FILES=frozenset({
 'shared_platform/agent_prepared_common_service.py',
 'shared_platform/agent_prepared_publication_adoption.py',
 'shared_platform/agent_prepared_source_successor.py',
 'scripts/continue_agent_prepared_source.py',
 'tests/test_agent_prepared_common_list.py',
 'tests/test_agent_prepared_source_successor.py',
})
TABLE='''CREATE TABLE IF NOT EXISTS agent_prepared_source_successors (
 task_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, record_digest TEXT NOT NULL,
 signature TEXT NOT NULL)'''


def _fail(code):raise adoption.AdoptionBlocked(code)
def _version(profile):return dict(code_version=profile.version,environment=profile.environment,manifest_digest=profile.manifest_digest)


def source_evidence(profile,old_version,*,allowed_files=ALLOWED_FILES,required_changed_file='shared_platform/agent_prepared_common_service.py'):
    from shared_platform.operations_runtime import runtime_matches
    if profile.environment!='stable' or not runtime_matches(profile):_fail('SOURCE_SUCCESSOR_CURRENT_SOURCE_REQUIRED')
    root=Path(profile.root)
    def git(*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
    if git('status','--porcelain=v1','-uall'):_fail('SOURCE_SUCCESSOR_CLEAN_SOURCE_REQUIRED')
    old=old_version['code_version']
    if old==profile.version or subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',old,profile.version],capture_output=True).returncode:
        _fail('SOURCE_SUCCESSOR_ANCESTOR_REQUIRED')
    changes=[line.split('\t') for line in git('diff','--name-status',old,profile.version).splitlines()]
    if (not changes or any(len(row)!=2 or row[0] not in {'A','M'} or row[1] not in allowed_files for row in changes)
        or required_changed_file not in {row[1] for row in changes}):
        _fail('SOURCE_SUCCESSOR_EXACT_TECHNICAL_FILES_REQUIRED')
    return {'old_version':old_version,'new_version':_version(profile),
        'changed_files':{name:sha256((root/name).read_bytes()).hexdigest() for _,name in changes}}


def _manual_intent(store,offer):
    with store._connect_readonly() as db:
        rows=db.execute('SELECT intent_id,intent_digest,state,attempts FROM manual_common_creations WHERE offer_id=?',(offer,)).fetchall()
    if len(rows)!=1 or rows[0]['state']!='RESERVED' or rows[0]['attempts']!=0:
        _fail('SOURCE_SUCCESSOR_ORIGINAL_UNDISPATCHED_INTENT_REQUIRED')
    return dict(rows[0])


def verify_successor(task,profile,original,*,connection,owner_sid,key):
    gallery=connection.execute("SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id",(task['task_id'],GALLERY_EVENT)).fetchall()
    if gallery:return verify_completed_gallery_current(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,events=gallery)
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_prepared_source_successors_v6'").fetchone():
        sixth=connection.execute('SELECT * FROM agent_prepared_source_successors_v6 WHERE task_id=?',(task['task_id'],)).fetchone()
        if sixth is not None:return verify_sixth_successor(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,row=sixth)
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_prepared_source_successors_v5'").fetchone():
        fifth=connection.execute('SELECT * FROM agent_prepared_source_successors_v5 WHERE task_id=?',(task['task_id'],)).fetchone()
        if fifth is not None:return verify_fifth_successor(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,row=fifth)
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_prepared_source_successors_v4'").fetchone():
        fourth=connection.execute('SELECT * FROM agent_prepared_source_successors_v4 WHERE task_id=?',(task['task_id'],)).fetchone()
        if fourth is not None:return verify_fourth_successor(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,row=fourth)
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_prepared_source_successors_v3'").fetchone():
        third=connection.execute('SELECT * FROM agent_prepared_source_successors_v3 WHERE task_id=?',(task['task_id'],)).fetchone()
        if third is not None:return verify_third_successor(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,row=third)
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_prepared_source_successors_v2'").fetchone():
        second=connection.execute('SELECT * FROM agent_prepared_source_successors_v2 WHERE task_id=?',(task['task_id'],)).fetchone()
        if second is not None:return verify_second_successor(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,row=second)
    try:row=connection.execute('SELECT * FROM agent_prepared_source_successors WHERE task_id=?',(task['task_id'],)).fetchone()
    except sqlite3.OperationalError:_fail('SOURCE_SUCCESSOR_SIGNED_RECORD_REQUIRED')
    if row is None:_fail('SOURCE_SUCCESSOR_SIGNED_RECORD_REQUIRED')
    record=json.loads(row['record_json']);raw=adoption._bytes(record)
    if (sha256(raw).hexdigest()!=row['record_digest']
        or not hmac.compare_digest(hmac.new(key,raw,'sha256').hexdigest(),row['signature'])
        or record.get('schema_version')!=SCHEMA or record.get('owner_sid')!=owner_sid
        or record.get('task_id')!=task['task_id'] or record.get('offer_id')!=original['offer_id']
        or record.get('original_adoption_digest')!=sha256(adoption._bytes(original)).hexdigest()
        or record.get('scope_digest')!=sha256(adoption._bytes(task['scope'])).hexdigest()
        or record.get('source')!=source_evidence(profile,original['release'])
        or task.get('version')!=_version(profile)):
        _fail('SOURCE_SUCCESSOR_SIGNED_BINDING_CHANGED')
    event=connection.execute("SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type='source_code_successor' ORDER BY id DESC LIMIT 1",(task['task_id'],)).fetchone()
    if event is None or json.loads(event['detail_json'])!={'record_digest':row['record_digest'],'old_version':original['release'],'new_version':task['version']}:
        _fail('SOURCE_SUCCESSOR_GENUINE_EVENT_REQUIRED')
    return record


SECOND_SCHEMA='agent-prepared-source-code-successor/v2'
SECOND_ALLOWED_FILES=frozenset({
 'shared_platform/manual_common_successor.py','shared_platform/manual_common_creation.py',
 'shared_platform/agent_prepared_common_service.py','shared_platform/agent_prepared_source_successor.py',
 'modules/miaoshou/client.py','scripts/continue_agent_prepared_source.py',
 'skills/publish-approved-product/scripts/prepare_publication_execution.py',
 'tests/test_manual_common_sdk_response.py','tests/test_manual_common_successor.py',
 'tests/test_agent_prepared_source_successor_v2.py',
})

def _signed(row,*,key,schema,owner_sid):
    if row is None:_fail('SOURCE_SUCCESSOR_SIGNED_RECORD_REQUIRED')
    record=json.loads(row['record_json']);raw=adoption._bytes(record)
    if (sha256(raw).hexdigest()!=row['record_digest'] or not hmac.compare_digest(hmac.new(key,raw,'sha256').hexdigest(),row['signature'])
        or record.get('schema_version')!=schema or record.get('owner_sid')!=owner_sid):_fail('SOURCE_SUCCESSOR_SIGNED_BINDING_CHANGED')
    return record

def _git_objects(root,version,names):
    """Read only the bounded changed historical blobs, in one batch."""
    root=str(root)
    request=''.join(version+':'+name+'\n' for name in names).encode()
    output=subprocess.check_output(['git','-C',root,'cat-file','--batch'],input=request)
    result={};offset=0
    for name in names:
        end=output.index(b'\n',offset);header=output[offset:end].split()
        if len(header)!=3 or header[1]!=b'blob':_fail('SOURCE_SUCCESSOR_REAL_GIT_OBJECT_REQUIRED')
        size=int(header[2]);offset=end+1;result[name]=output[offset:offset+size];offset+=size+1
    return result

def verify_historical_source(root,source,*,allowed_files):
    old=source['old_version'];new=source['new_version']
    if old['environment']!='stable' or new['environment']!='stable':_fail('SOURCE_SUCCESSOR_HISTORICAL_ENVIRONMENT_CHANGED')
    if subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',old['code_version'],new['code_version']],capture_output=True).returncode:
        _fail('SOURCE_SUCCESSOR_ANCESTOR_REQUIRED')
    changes=[line.split('\t') for line in subprocess.check_output(
        ['git','-C',str(root),'diff','--name-status',old['code_version'],new['code_version']],text=True).splitlines()]
    if (not changes or any(len(row)!=2 or row[0] not in {'A','M'} or row[1] not in allowed_files for row in changes)
        or {row[1] for row in changes}!=set(source['changed_files'])):_fail('SOURCE_SUCCESSOR_HISTORICAL_FILES_CHANGED')
    objects=_git_objects(root,new['code_version'],[row[1] for row in changes])
    # Historical runtime manifests describe the then-current Windows checkout
    # (mixed LF/CRLF). Their observations remain authenticated by the v1 HMAC;
    # do not pretend raw normalized Git bytes reproduce those runtime bytes.
    for version in (old,new):
        value=version.get('manifest_digest')
        if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
            _fail('SOURCE_SUCCESSOR_HISTORICAL_MANIFEST_CHANGED')
    for name,raw in objects.items():
        crlf=raw.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
        if source['changed_files'][name] not in {sha256(raw).hexdigest(),sha256(crlf).hexdigest()}:
            _fail('SOURCE_SUCCESSOR_HISTORICAL_FILES_CHANGED')

def _first_link(task,profile,original,*,connection,owner_sid,key):
    row=connection.execute('SELECT * FROM agent_prepared_source_successors WHERE task_id=?',(task['task_id'],)).fetchone()
    record=_signed(row,key=key,schema=SCHEMA,owner_sid=owner_sid)
    if (record.get('task_id')!=task['task_id'] or record.get('offer_id')!=original['offer_id']
        or record.get('original_adoption_digest')!=sha256(adoption._bytes(original)).hexdigest()
        or record.get('scope_digest')!=sha256(adoption._bytes(task['scope'])).hexdigest()
        or record['source']['old_version']!=original['release']):_fail('SOURCE_SUCCESSOR_FIRST_LINK_CHANGED')
    verify_historical_source(profile.root,record['source'],allowed_files=ALLOWED_FILES)
    detail={'record_digest':row['record_digest'],'old_version':original['release'],'new_version':record['source']['new_version']}
    events=connection.execute("SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type='source_code_successor'",(task['task_id'],)).fetchall()
    if len(events)!=1 or json.loads(events[0][0])!=detail:_fail('SOURCE_SUCCESSOR_GENUINE_EVENT_REQUIRED')
    return record,row['record_digest']

def verify_second_successor(task,profile,original,*,connection,owner_sid,key,row,historical=False):
    record=_signed(row,key=key,schema=SECOND_SCHEMA,owner_sid=owner_sid)
    first,first_digest=_first_link(task,profile,original,connection=connection,owner_sid=owner_sid,key=key)
    expected_source=record.get('source')
    if historical:
        verify_historical_source(profile.root,expected_source,allowed_files=SECOND_ALLOWED_FILES)
    else:expected_source=source_evidence(profile,first['source']['new_version'],allowed_files=SECOND_ALLOWED_FILES)
    if (record.get('task_id')!=task['task_id'] or record.get('offer_id')!=original['offer_id']
        or record.get('original_adoption_digest')!=sha256(adoption._bytes(original)).hexdigest()
        or record.get('previous_record_digest')!=first_digest
        or record.get('scope_digest')!=first['scope_digest']
        or record['source']['old_version']!=first['source']['new_version']
        or record.get('source')!=expected_source
        or (not historical and task.get('version')!=_version(profile))):_fail('SOURCE_SUCCESSOR_SECOND_BINDING_CHANGED')
    detail={'record_digest':row['record_digest'],'previous_record_digest':first_digest,
        'old_version':first['source']['new_version'],'new_version':record['source']['new_version']}
    events=connection.execute("SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type='source_code_successor_v2'",(task['task_id'],)).fetchall()
    if len(events)!=1 or json.loads(events[0][0])!=detail:_fail('SOURCE_SUCCESSOR_GENUINE_EVENT_REQUIRED')
    from shared_platform.manual_common_creation import ManualCommonCreateLedger,_digest
    from shared_platform.manual_common_successor import PinnedCommonSuccessorDecision
    from shared_platform.release_store import default_release_store
    parent=ManualCommonCreateLedger(default_release_store().path).read(record['original_intent_id'])
    if _digest(parent)!=record['original_manual_intent_sha256']:_fail('SOURCE_SUCCESSOR_ORIGINAL_UNKNOWN_CHANGED')
    PinnedCommonSuccessorDecision(Path(record['decision_path']),record['decision_sha256']).read()
    return record

THIRD_SCHEMA='agent-prepared-source-code-successor/v3'
THIRD_ALLOWED_FILES=frozenset({
 'shared_platform/manual_common_creation.py','shared_platform/manual_common_successor.py',
 'shared_platform/agent_prepared_common_service.py','shared_platform/agent_prepared_source_successor.py',
 'scripts/continue_agent_prepared_source.py','tests/test_manual_common_price_correction.py',
 'tests/test_manual_common_creation.py','tests/test_manual_common_successor.py',
 'tests/test_agent_prepared_common_integration.py',
})

def _second_historical(task,profile,original,*,connection,owner_sid,key):
    row=connection.execute('SELECT * FROM agent_prepared_source_successors_v2 WHERE task_id=?',(task['task_id'],)).fetchone()
    return verify_second_successor(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,row=row,historical=True),row['record_digest']

def verify_third_successor(task,profile,original,*,connection,owner_sid,key,row,historical=False):
    from shared_platform.manual_common_creation import ManualCommonCreateLedger,_digest
    from shared_platform.manual_common_successor import PinnedCommonCorrectionDecision,validate_known_price_rejection
    from shared_platform.release_store import default_release_store
    record=_signed(row,key=key,schema=THIRD_SCHEMA,owner_sid=owner_sid)
    previous,digest=_second_historical(task,profile,original,connection=connection,owner_sid=owner_sid,key=key)
    expected_source=record.get('source')
    if historical:verify_historical_source(profile.root,expected_source,allowed_files=THIRD_ALLOWED_FILES)
    else:expected_source=source_evidence(profile,previous['source']['new_version'],allowed_files=THIRD_ALLOWED_FILES)
    if (record.get('task_id')!=task['task_id'] or record.get('offer_id')!=original['offer_id']
        or record.get('original_adoption_digest')!=sha256(adoption._bytes(original)).hexdigest()
        or record.get('scope_digest')!=previous['scope_digest'] or record.get('previous_record_digest')!=digest
        or record['source']['old_version']!=previous['source']['new_version']
        or record.get('source')!=expected_source
        or (not historical and task.get('version')!=_version(profile))):_fail('SOURCE_SUCCESSOR_THIRD_BINDING_CHANGED')
    detail=dict(record_digest=row['record_digest'],previous_record_digest=digest,
        old_version=previous['source']['new_version'],new_version=record['source']['new_version'])
    events=connection.execute("SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type='source_code_successor_v3'",(task['task_id'],)).fetchall()
    if len(events)!=1 or json.loads(events[0][0])!=detail:_fail('SOURCE_SUCCESSOR_GENUINE_EVENT_REQUIRED')
    child=ManualCommonCreateLedger(default_release_store().path).read(record['child_intent_id'])
    validate_known_price_rejection(child)
    if _digest(child)!=record['rejected_child_sha256']:_fail('SOURCE_SUCCESSOR_REJECTED_CHILD_CHANGED')
    PinnedCommonCorrectionDecision(Path(record['decision_path']),record['decision_sha256']).read()
    return record

FOURTH_SCHEMA='agent-prepared-source-code-successor/v4'
FOURTH_ALLOWED_FILES=frozenset({
 'modules/miaoshou/client.py','modules/products/server.py','shared_platform/release_store.py',
 'shared_platform/native_common_edit_boundary.py','shared_platform/native_common_technical_execution.py',
 'shared_platform/native_common_baseline_successor.py','shared_platform/native_common_baseline_source.py',
 'shared_platform/native_sole_final_service.py','shared_platform/native_sole_final_decision.py',
 'shared_platform/agent_prepared_common_service.py','shared_platform/agent_prepared_source_successor.py',
 'scripts/continue_agent_prepared_source.py','tests/test_native_common_baseline_successor.py',
 'tests/test_native_common_edit_outcomes.py','tests/test_agent_prepared_source_successor_v4.py',
 'docs/contracts/common-baseline-successor.NOT_AUTHORIZED.json',
})

def _third_historical(task,profile,original,*,connection,owner_sid,key):
    row=connection.execute('SELECT * FROM agent_prepared_source_successors_v3 WHERE task_id=?',(task['task_id'],)).fetchone()
    return verify_third_successor(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,row=row,historical=True),row['record_digest']

def verify_fourth_successor(task,profile,original,*,connection,owner_sid,key,row,historical=False):
    from shared_platform.native_common_baseline_successor import PinnedBaselineDecision,relation,original_snapshot
    from shared_platform.release_store import default_release_store
    record=_signed(row,key=key,schema=FOURTH_SCHEMA,owner_sid=owner_sid)
    previous,digest=_third_historical(task,profile,original,connection=connection,owner_sid=owner_sid,key=key)
    reader=PinnedBaselineDecision(Path(record['decision_path']),record['decision_sha256'])
    decision=reader.read()
    expected_source=record.get('source')
    if historical:verify_historical_source(profile.root,expected_source,allowed_files=FOURTH_ALLOWED_FILES)
    else:expected_source=source_evidence(profile,previous['source']['new_version'],allowed_files=FOURTH_ALLOWED_FILES)
    if (record.get('task_id')!=task['task_id'] or record.get('offer_id')!=original['offer_id']
        or record.get('original_adoption_digest')!=sha256(adoption._bytes(original)).hexdigest()
        or record.get('scope_digest')!=previous['scope_digest'] or record.get('previous_record_digest')!=digest
        or record.get('baseline_relation')!=relation(reader) or decision['task_id']!=task['task_id']
        or decision['original_adoption_digest']!=record['original_adoption_digest']
        or record['source']['old_version']!=previous['source']['new_version'] or record.get('source')!=expected_source
        or (not historical and task.get('version')!=_version(profile))):_fail('SOURCE_SUCCESSOR_FOURTH_BINDING_CHANGED')
    events=connection.execute("SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type='source_code_successor_v4'",(task['task_id'],)).fetchall()
    detail=dict(record_digest=row['record_digest'],previous_record_digest=digest,
        old_version=previous['source']['new_version'],new_version=record['source']['new_version'])
    if len(events)!=1 or json.loads(events[0][0])!=detail:_fail('SOURCE_SUCCESSOR_GENUINE_EVENT_REQUIRED')
    with default_release_store()._connect_readonly() as db:
        db.execute('BEGIN');original_snapshot(db,decision)
    return record

FIFTH_SCHEMA='agent-prepared-source-code-successor/v5'
FIFTH_ALLOWED_FILES=frozenset({
 'shared_platform/agent_prepared_common_service.py','shared_platform/agent_prepared_source_successor.py',
 'shared_platform/native_common_baseline_successor.py','shared_platform/workbench_engine.py',
 'scripts/continue_agent_prepared_source.py','tests/test_native_common_baseline_lock_fix.py',
})

def _fourth_historical(task,profile,original,*,connection,owner_sid,key):
    row=connection.execute('SELECT * FROM agent_prepared_source_successors_v4 WHERE task_id=?',(task['task_id'],)).fetchone()
    return verify_fourth_successor(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,row=row,historical=True),row['record_digest']

def verify_fifth_successor(task,profile,original,*,connection,owner_sid,key,row,historical=False):
    record=_signed(row,key=key,schema=FIFTH_SCHEMA,owner_sid=owner_sid)
    previous,digest=_fourth_historical(task,profile,original,connection=connection,owner_sid=owner_sid,key=key)
    expected_source=record.get('source')
    if historical:verify_historical_source(profile.root,expected_source,allowed_files=FIFTH_ALLOWED_FILES)
    else:expected_source=source_evidence(profile,previous['source']['new_version'],allowed_files=FIFTH_ALLOWED_FILES)
    if (record.get('task_id')!=task['task_id'] or record.get('offer_id')!=original['offer_id']
        or record.get('original_adoption_digest')!=sha256(adoption._bytes(original)).hexdigest()
        or record.get('scope_digest')!=previous['scope_digest'] or record.get('previous_record_digest')!=digest
        or record.get('baseline_relation')!=previous['baseline_relation']
        or record.get('decision_path')!=previous['decision_path'] or record.get('decision_sha256')!=previous['decision_sha256']
        or record.get('purpose')!='COMMON_BASELINE_LOCAL_LOCK_REPAIR' or record.get('new_edit_attempts')!=0
        or record.get('marketplace_approval') is not False
        or record['source']['old_version']!=previous['source']['new_version'] or record.get('source')!=expected_source
        or (not historical and task.get('version')!=_version(profile))):_fail('SOURCE_SUCCESSOR_FIFTH_BINDING_CHANGED')
    events=connection.execute("SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type='source_code_successor_v5'",(task['task_id'],)).fetchall()
    detail=dict(record_digest=row['record_digest'],previous_record_digest=digest,
        old_version=previous['source']['new_version'],new_version=record['source']['new_version'])
    if len(events)!=1 or json.loads(events[0][0])!=detail:_fail('SOURCE_SUCCESSOR_GENUINE_EVENT_REQUIRED')
    return record

SIXTH_SCHEMA='agent-prepared-source-code-successor/v6'
SIXTH_ALLOWED_FILES=frozenset({
 'shared_platform/agent_prepared_common_service.py','shared_platform/agent_prepared_source_successor.py',
 'shared_platform/native_common_baseline_successor.py','shared_platform/native_common_colormap_correction.py',
 'shared_platform/native_common_technical_execution.py','shared_platform/release_store.py',
 'modules/products/release_adapters.py','modules/products/server.py','scripts/continue_agent_prepared_source.py',
 'skills/publish-approved-product/scripts/prepare_publication_execution.py',
 'docs/contracts/common-colormap-correction.NOT_AUTHORIZED.json','tests/test_native_common_colormap_correction.py',
})


def _fifth_historical(task,profile,original,*,connection,owner_sid,key):
    row=connection.execute('SELECT * FROM agent_prepared_source_successors_v5 WHERE task_id=?',(task['task_id'],)).fetchone()
    return verify_fifth_successor(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,row=row,historical=True),row['record_digest']


def verify_sixth_successor(task,profile,original,*,connection,owner_sid,key,row,historical=False):
    from shared_platform.native_common_colormap_correction import PinnedColorMapDecision,relation,rejected_snapshot
    from shared_platform.release_store import default_release_store
    record=_signed(row,key=key,schema=SIXTH_SCHEMA,owner_sid=owner_sid)
    previous,digest=_fifth_historical(task,profile,original,connection=connection,owner_sid=owner_sid,key=key)
    reader=PinnedColorMapDecision(Path(record['decision_path']),record['decision_sha256']);decision=reader.read()
    expected_source=record.get('source')
    if historical:verify_historical_source(profile.root,expected_source,allowed_files=SIXTH_ALLOWED_FILES)
    else:expected_source=source_evidence(profile,previous['source']['new_version'],allowed_files=SIXTH_ALLOWED_FILES)
    if (record.get('task_id')!=task['task_id'] or record.get('offer_id')!=original['offer_id']
        or record.get('original_adoption_digest')!=sha256(adoption._bytes(original)).hexdigest()
        or record.get('scope_digest')!=previous['scope_digest'] or record.get('previous_record_digest')!=digest
        or record.get('baseline_relation')!=previous['baseline_relation'] or record.get('correction_relation')!=relation(reader)
        or decision['baseline_decision']!=dict(path=previous['decision_path'],sha256=previous['decision_sha256'])
        or decision['task_id']!=task['task_id'] or decision['original_adoption_digest']!=record['original_adoption_digest']
        or {r.get('canonical_sku'):r.get('model_sku') for r in decision['variant_bindings']}!=original['facts']['aliases']
        or record.get('purpose')!='COMMON_KNOWN_COLORMAP_REJECTION_CORRECTION' or record.get('maximum_new_edit_attempts')!=1
        or record.get('marketplace_approval') is not False
        or record['source']['old_version']!=previous['source']['new_version'] or record.get('source')!=expected_source
        or (not historical and task.get('version')!=_version(profile))):_fail('SOURCE_SUCCESSOR_SIXTH_BINDING_CHANGED')
    events=connection.execute("SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type='source_code_successor_v6'",(task['task_id'],)).fetchall()
    detail=dict(record_digest=row['record_digest'],previous_record_digest=digest,
        old_version=previous['source']['new_version'],new_version=record['source']['new_version'])
    if len(events)!=1 or json.loads(events[0][0])!=detail:_fail('SOURCE_SUCCESSOR_GENUINE_EVENT_REQUIRED')
    with default_release_store()._connect_readonly() as db:
        db.execute('BEGIN');rejected_snapshot(db,reader)
    return record

GALLERY_EVENT='source_code_gallery_compatibility'
GALLERY_SCHEMA='agent-prepared-gallery-source-compatibility/v1'
GALLERY_FILE='shared_platform/publication_gallery_contract.py'
GALLERY_FILES=frozenset({GALLERY_FILE,'shared_platform/agent_prepared_source_successor.py',
    'scripts/continue_agent_prepared_source.py','tests/test_frozen_refrigerator_gallery.py'})
VARIANT_FILE='shared_platform/publication_r3_image_bridge.py'
VARIANT_FILES=frozenset({VARIANT_FILE,'shared_platform/agent_prepared_source_successor.py',
    'scripts/continue_agent_prepared_source.py','tests/test_native_variant_image_projection.py',
    'modules/ozon/approved_publication_v4.py','shared_platform/publication_autopilot.py',
    'web/static/publication_original_adapter.js'})
API_SCOPE_FILES=VARIANT_FILES|frozenset({'shared_platform/prepared_target_reduction.py',
    'shared_platform/prepared_target_consumer.py','shared_platform/agent_prepared_common_service.py',
    'shared_platform/publication_ozon_card_structure.py','shared_platform/native_sole_final_service.py',
    'shared_platform/agent_prepared_v4_targets.py','shared_platform/native_sole_final_tasks.py',
    'shared_platform/operations_publication.py','shared_platform/publication_canonical_aliases.py',
    'shared_platform/native_final_mutation_budget.py','tests/test_completed_api_target_selection.py'})


def _sixth_historical(task,profile,original,*,connection,owner_sid,key):
    row=connection.execute('SELECT * FROM agent_prepared_source_successors_v6 WHERE task_id=?',(task['task_id'],)).fetchone()
    return verify_sixth_successor(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,row=row,historical=True),row['record_digest']


def _gallery_inputs(profile,store,original,previous,*,before_append=False):
    """Reconcile the completed phase, never assert the old empty-history state."""
    from shared_platform import publication_r3_image_bridge as bridge,publication_gallery_contract as gallery
    from shared_platform.native_common_budget_facts import _stored_preparation,_source_payload
    from shared_platform.native_common_colormap_correction import PinnedColorMapDecision,rejected_snapshot,correction_payload
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    from shared_platform.native_common_retained_completion import read_retained_completion
    from shared_platform.publication_paid_requests import load_paid_context
    from shared_platform.immutable_approval_files import require_local_path
    root=Path(profile.root);facts=original['facts'];offer=original['offer_id'];directory=root/'reports/product-preparation'/offer
    for relative,expected in facts['source_files'].items():
        path=root/relative;require_local_path(path,root=root)
        if sha256(path.read_bytes()).hexdigest()!=expected:_fail('GALLERY_SOURCE_FROZEN_INPUT_CHANGED')
    old=facts['original_profile'];path=Path(old['path']);require_local_path(path,root=path.parent)
    if sha256(path.read_bytes()).hexdigest()!=old['sha256']:_fail('GALLERY_SOURCE_FROZEN_INPUT_CHANGED')
    documents={name:json.loads((directory/file).read_bytes()) for name,file in bridge.R2_DOCUMENTS.items()}
    r1=documents['round1_snapshot']
    if (r1['snapshot_digest']!=facts['snapshot_digest'] or gallery.digest(r1['image_plan'])!=r1['image_plan_digest']):
        _fail('GALLERY_SOURCE_EXACT_R1_REQUIRED')
    reader=PinnedColorMapDecision(Path(previous['decision_path']),previous['decision_sha256']);decision=reader.read()
    with store._connect_readonly() as db:
        db.execute('BEGIN');prepared=_stored_preparation(db,offer,facts['prepared_reference'])
        if facts['preparation_source']!=_source_payload(db,prepared,r1):_fail('GALLERY_SOURCE_NATIVE_R1_CHANGED')
        documents['first_review']=prepared['packet'];rejected_snapshot(db,reader)
        oldplan=db.execute('SELECT payload_json FROM release_plans WHERE plan_id=?',(decision['original_plan_id'],)).fetchone()
        payload=correction_payload(db,json.loads(oldplan[0]),reader,repo_root=root)
        if payload['plan_id']!=previous['corrected_plan_id'] or sha256(adoption._bytes(payload)).hexdigest()!=previous['corrected_payload_digest']:
            _fail('GALLERY_SOURCE_COMPLETED_PLAN_CHANGED')
        runs=db.execute('SELECT * FROM release_runs WHERE plan_id=?',(payload['plan_id'],)).fetchall()
        if len(runs)!=1:_fail('GALLERY_SOURCE_ONE_COMPLETED_COMMON_REQUIRED')
        run=runs[0];completion=read_retained_completion(NativeCommonSourceReader(store),db,payload['plan_id'],run['run_id'])
        if completion.state!='CONFIRMED_WRITE':_fail('GALLERY_SOURCE_COMPLETED_COMMON_REQUIRED')
        expected={decision['original_run_id'],decision['rejected_run_id'],run['run_id']}
        actual={r[0] for r in db.execute('SELECT r.run_id FROM release_runs r JOIN release_target_runs t USING(run_id) JOIN release_plans p USING(plan_id) WHERE p.product_id=? AND t.target_label=?',(offer,'miaoshou:COMMON'))}
        if actual!=expected:_fail('GALLERY_SOURCE_COMMON_HISTORY_CHANGED')
        if before_append and (db.execute('SELECT 1 FROM release_approvals a JOIN release_plans p USING(plan_id) WHERE p.product_id=?',(offer,)).fetchone()
            or {r[0] for r in db.execute('SELECT r.run_id FROM release_runs r JOIN release_plans p USING(plan_id) WHERE p.product_id=?',(offer,))}!=expected):
            _fail('GALLERY_SOURCE_UNBOUND_MARKETPLACE_HISTORY')
        history={}
        for table in ('release_runs','release_target_runs','release_target_submissions','release_target_failure_events','release_target_readbacks'):
            history[table]=[dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE run_id IN (?,?,?) ORDER BY run_id',tuple(sorted(expected)))]
        creates={table:[dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE offer_id=?',(offer,))] for table in
            ('manual_common_creations','manual_common_create_successors','manual_common_create_corrections')}
        if any(len(rows)!=1 or rows[0]['attempts']!=1 for rows in creates.values()):_fail('GALLERY_SOURCE_CREATE_HISTORY_CHANGED')
    if bridge.validate_r2_identity(documents)!=facts['r2_identity']:_fail('GALLERY_SOURCE_R2_CHANGED')
    paid,proof=adoption._verify_paid_history(load_paid_context(offer_id=offer,round1=r1,repo_root=root))
    if paid!=facts['paid_summary'] or proof!=facts.get('image_unknown_successor_proof'):_fail('GALLERY_SOURCE_PAID_HISTORY_CHANGED')
    return dict(round1_snapshot_digest=r1['snapshot_digest'],image_plan_digest=r1['image_plan_digest'],
        frozen_inputs_digest=sha256(adoption._bytes(facts['source_files'])).hexdigest(),
        common_plan_id=payload['plan_id'],common_payload_digest=previous['corrected_payload_digest'],
        common_run_id=run['run_id'],common_readback_digest=completion.readback_digest,
        common_history_digest=sha256(adoption._bytes(history)).hexdigest(),create_history_digest=sha256(adoption._bytes(creates)).hexdigest())


def verify_gallery_source(task,profile,original,*,connection,owner_sid,key,events,historical=False):
    from shared_platform.release_store import default_release_store
    if not 1<=len(events)<=2:_fail('GALLERY_SOURCE_BOUNDED_COMPATIBILITY_EVENTS_REQUIRED')
    previous,digest=_sixth_historical(task,profile,original,connection=connection,owner_sid=owner_sid,key=key)
    preserved=_gallery_inputs(profile,default_release_store(),original,previous)
    old_version=previous['source']['new_version']
    for index,event in enumerate(events):
        envelope=json.loads(event['detail_json']);record=_signed(envelope,key=key,schema=GALLERY_SCHEMA,owner_sid=owner_sid)
        allowed,required=(GALLERY_FILES,GALLERY_FILE) if index==0 else (VARIANT_FILES,VARIANT_FILE)
        purpose=('COMPLETED_COMMON_FROZEN_GALLERY_ROLE_COMPATIBILITY' if index==0
            else 'COMPLETED_COMMON_FROZEN_VARIANT_IMAGE_PROJECTION')
        if index==1 and record.get('api_target_selection') is not None:
            from shared_platform.prepared_target_reduction import build_completed_api_selection
            selection=record['api_target_selection'];allowed=API_SCOPE_FILES
            purpose='COMPLETED_COMMON_API_ONLY_VARIANT_IMAGE_PROJECTION'
            selected_task={**task,'checkpoint':selection['checkpoint_snapshot']}
            if (any(task['checkpoint'].get(k)!=v for k,v in selection['checkpoint_snapshot'].items())
                or selection!=build_completed_api_selection(task=selected_task,original=original,
                version=record['source']['new_version'],preserved=preserved,pins=selection['authority'])):
                _fail('API_SCOPE_SIGNED_SELECTION_CHANGED')
        evidence=record.get('source') or {}
        if historical or index<len(events)-1:
            verify_historical_source(profile.root,evidence,allowed_files=allowed)
        elif evidence!=source_evidence(profile,old_version,allowed_files=allowed,required_changed_file=required):
            _fail('GALLERY_SOURCE_COMPATIBILITY_CHANGED')
        if (record.get('task_id')!=task['task_id'] or record.get('offer_id')!=original['offer_id']
            or record.get('original_adoption_digest')!=sha256(adoption._bytes(original)).hexdigest()
            or record.get('previous_record_digest')!=digest or record.get('scope_digest')!=previous['scope_digest']
            or record.get('purpose')!=purpose or record.get('revision',1)!=index+1
            or record.get('new_edit_attempts')!=0 or record.get('external_write_count')!=0 or record.get('marketplace_approval') is not False
            or evidence.get('old_version')!=old_version or required not in evidence.get('changed_files',{})
            or record.get('preserved')!=preserved):
            _fail('GALLERY_SOURCE_COMPATIBILITY_CHANGED')
        old_version=evidence['new_version'];digest=envelope['record_digest']
    if not historical and task.get('version')!=_version(profile):_fail('GALLERY_SOURCE_COMPATIBILITY_CHANGED')
    return record


UI_EVENT='source_code_native_final_image_ui_compatibility'
UI_SCHEMA='agent-prepared-native-final-image-ui-source/v1'
UI_PURPOSE='FROZEN_NATIVE_FINAL_IMAGES_AND_CONTENT_DISPLAY'
UI_MECHANISM_FILES=frozenset({'shared_platform/agent_prepared_source_successor.py',
    'shared_platform/prepared_target_consumer.py','scripts/continue_agent_prepared_source.py',
    'tests/test_native_final_image_ui.py'})
UI_FILES=frozenset({'web/static/publication_original_adapter.js','web/static/publication_original_review.css'})


def _ui_request(pins,task,original,frozen):
    from shared_platform.immutable_approval_files import require_local_path
    if not isinstance(pins,dict) or set(pins)!={'path','sha256'}:_fail('NATIVE_FINAL_UI_REQUEST_PIN_REQUIRED')
    path=Path(pins['path'])
    if not path.is_absolute():_fail('NATIVE_FINAL_UI_REQUEST_PIN_REQUIRED')
    require_local_path(path,root=path.parent);raw=path.read_bytes();request=json.loads(raw)
    if (sha256(raw).hexdigest()!=pins['sha256'] or request.get('schema_version')!='orbit-user-ui-defect-and-fix-scope/v1'
        or request.get('task_id')!=task['task_id'] or request.get('offer_id')!=original['offer_id']
        or request.get('preserve_candidate_digest')!=frozen['candidate_digest'] or request.get('preserve_plan_id')!=frozen['plan_id']
        or request.get('preserve_native_review_digest')!=frozen['native_review_digest'] or request.get('local_ui_source_repair') is not True
        or request.get('original_gallery_event_limit')!=2
        or any(request.get(k) is not False for k in ('marketplace_final_approval','new_paid_images','new_COMMON_or_create','merchant_writes','new_credential_authority'))):
        _fail('NATIVE_FINAL_UI_REQUEST_BINDING_CHANGED')
    return pins


def _ui_source_segments(profile,old_version,mechanism_version):
    """Authenticate the bootstrap separately; the second Git segment is UI only."""
    names=subprocess.check_output(['git','-C',str(profile.root),'diff','--name-only',
        old_version['code_version'],mechanism_version['code_version']],text=True).splitlines()
    if not set(UI_MECHANISM_FILES)-{'tests/test_native_final_image_ui.py'} <= set(names):
        _fail('NATIVE_FINAL_UI_EXACT_MECHANISM_REQUIRED')
    if not set(names)<=UI_MECHANISM_FILES:_fail('NATIVE_FINAL_UI_EXACT_MECHANISM_REQUIRED')
    # No bootstrap file may change again in the frontend-only segment.
    mechanism=dict(old_version=old_version,new_version=mechanism_version,
        changed_files={name:sha256((Path(profile.root)/name).read_bytes()).hexdigest() for name in names})
    verify_historical_source(profile.root,mechanism,allowed_files=UI_MECHANISM_FILES)
    frontend=source_evidence(profile,mechanism_version,allowed_files=UI_FILES,
        required_changed_file='web/static/publication_original_adapter.js')
    if set(frontend['changed_files'])!=UI_FILES:_fail('NATIVE_FINAL_UI_EXACT_FRONTEND_REQUIRED')
    if mechanism_version['manifest_digest']!=profile.manifest_digest:_fail('NATIVE_FINAL_UI_MECHANISM_MANIFEST_CHANGED')
    return dict(mechanism=mechanism,frontend=frontend)


def _ui_frozen_candidate(store,offer,*,before_append=False):
    """Read stored bytes only: no compilation, finalization, nonce or approval."""
    from shared_platform.final_review_server_admission import FrozenReview
    from shared_platform.native_common_retained_completion import read_retained_completion
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    from shared_platform.native_common_technical_execution import _binding
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        rows=db.execute('SELECT * FROM release_plans WHERE product_id=?',(offer,)).fetchall()
        rows=[r for r in rows if 'r3_marketplace_binding' in json.loads(r['payload_json'])]
        if len(rows)!=1:_fail('NATIVE_FINAL_UI_ONE_FROZEN_PLAN_REQUIRED')
        plan=rows[0];raw=plan['payload_json'].encode();payload=json.loads(raw)
        if sha256(raw).hexdigest()!=plan['payload_digest'] or raw!=adoption._bytes(payload):
            _fail('NATIVE_FINAL_UI_STORED_PLAN_CHANGED')
        if before_append and (plan['status']!='PENDING_APPROVAL'
            or db.execute('SELECT 1 FROM release_approvals WHERE plan_id=?',(plan['plan_id'],)).fetchone()
            or db.execute('SELECT 1 FROM native_sole_final_decisions WHERE plan_id=?',(plan['plan_id'],)).fetchone()
            or db.execute('SELECT 1 FROM release_runs WHERE plan_id=?',(plan['plan_id'],)).fetchone()):
            _fail('NATIVE_FINAL_UI_UNAPPROVED_UNDISPATCHED_REQUIRED')
        snapshot=db.execute('SELECT * FROM publication_business_snapshots WHERE plan_id=?',(plan['plan_id'],)).fetchone()
        if snapshot is None or snapshot['release_payload_digest']!=plan['payload_digest']:
            _fail('NATIVE_FINAL_UI_BUSINESS_SNAPSHOT_REQUIRED')
        binding=payload['r3_marketplace_binding'];candidate={**binding['reviewed_candidate_facts'],
            'snapshot_digest':snapshot['snapshot_digest']}
        candidate['candidate_digest']=sha256(adoption._bytes(candidate)).hexdigest()
        manifest=candidate['review_manifest'];targets=candidate['target_labels']
        if (manifest['manifest_digest']!=sha256(adoption._bytes({k:v for k,v in manifest.items() if k!='manifest_digest'})).hexdigest()
            or targets!=json.loads(plan['target_labels_json'])
            or targets!=[r['target_label'] for r in manifest['targets']]
            or candidate['offer_id']!=offer or candidate['plan_id']!=plan['plan_id']):
            _fail('NATIVE_FINAL_UI_FROZEN_MATRIX_CHANGED')
        completion=read_retained_completion(NativeCommonSourceReader(store),db,binding['common_plan_id'],binding['common_run_id'])
        if completion.state!='CONFIRMED_WRITE':_fail('NATIVE_FINAL_UI_COMPLETED_COMMON_REQUIRED')
        run=db.execute('SELECT * FROM release_runs WHERE run_id=?',(binding['common_run_id'],)).fetchone()
        credential=_binding(run);context=credential['native_account_context']
        budget=dict(scope='IMMUTABLE_PREPARED_BASELINE',root_id=completion.preparation_root_id,
            r1_account_digest=completion.account_identity_digest,signing_context_digest=context['common_signing_context_digest'],
            mutation_sha256=credential['mutation_sha256'],maximum_confirmed_writes=1,confirmed_writes=1)
        critical='sha256:'+sha256(adoption._bytes(dict(candidate={k:v for k,v in candidate.items()
            if k not in {'candidate_digest','snapshot_digest'}},documents=binding['documents'],common_readback=binding['common_readback']))).hexdigest()
        review=FrozenReview(offer,plan['plan_id'],candidate['candidate_digest'],critical,tuple(targets),
            binding['common_plan_id'],binding['common_run_id'],'sha256:'+sha256(adoption._bytes(binding['common_readback'])).hexdigest(),
            'sha256:'+sha256(adoption._bytes(budget)).hexdigest(),binding['documents']['round1_snapshot']['snapshot_digest'],
            binding['r2_identity']['identity_digest'],'native-final:'+sha256(adoption._bytes([offer,plan['plan_id']])).hexdigest())
        return dict(plan_id=plan['plan_id'],payload_digest=plan['payload_digest'],candidate_digest=candidate['candidate_digest'],
            candidate_sha256=sha256(adoption._bytes(candidate)).hexdigest(),manifest_digest=manifest['manifest_digest'],
            business_snapshot_digest=snapshot['snapshot_digest'],business_snapshot_sha256=sha256(snapshot['snapshot_json'].encode()).hexdigest(),
            native_review_digest=review.digest(),ordered_targets=targets)


def verify_completed_gallery_current(task,profile,original,*,connection,owner_sid,key,events,historical=False):
    """Return the original gallery/API record, retaining its original version."""
    if not historical:
        activation=connection.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',
            (task['task_id'],TIKTOK_ACTIVATION_EVENT)).fetchall()
        if activation:return _verify_tiktok_activation_source(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,events=events,rows=activation)
        correction=connection.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',
            (task['task_id'],LOCALE_DRAFT_EVENT)).fetchall()
        if correction:return _verify_locale_draft_source(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,events=events,rows=correction)
        stock=connection.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',
            (task['task_id'],STOCK_CATEGORY_EVENT)).fetchall()
        if stock:return _verify_stock_category_source(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,events=events,rows=stock)
        native_zero=connection.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',
            (task['task_id'],NATIVE_RECOVERY_EVENT)).fetchall()
        if native_zero:return _verify_native_recovery_source(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,events=events,rows=native_zero)
        compatibility=connection.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',
            (task['task_id'],PLATFORM_EVENT)).fetchall()
        if compatibility:return _verify_platform_compatibility(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,events=events,rows=compatibility)
        reader=connection.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',
            (task['task_id'],R1_READER_EVENT)).fetchall()
        if reader:return _verify_r1_reader(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,events=events,rows=reader)
    rows=connection.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',
        (task['task_id'],UI_EVENT)).fetchall()
    if not rows:return verify_gallery_source(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,events=events)
    if len(rows)!=1 or len(events)!=2:_fail('NATIVE_FINAL_UI_SINGLE_EVENT_REQUIRED')
    prior=verify_gallery_source(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,events=events,historical=True)
    record=_signed(json.loads(rows[0]['detail_json']),key=key,schema=UI_SCHEMA,owner_sid=owner_sid)
    from shared_platform.release_store import default_release_store
    if (record.get('purpose')!=UI_PURPOSE or record.get('task_id')!=task['task_id'] or record.get('offer_id')!=original['offer_id']
        or record.get('original_adoption_digest')!=sha256(adoption._bytes(original)).hexdigest()
        or record.get('previous_gallery_digest')!=json.loads(events[-1]['detail_json'])['record_digest']
        or record.get('scope_digest')!=sha256(adoption._bytes(task['scope'])).hexdigest()
        or record.get('preserved')!=prior['preserved'] or record.get('api_selection_digest')!=(prior.get('api_target_selection') or {}).get('reduction_digest')
        or record.get('source_segments')!=(_historical_ui_segments(profile,prior['source']['new_version'],record['source_segments']) if historical else
            _ui_source_segments(profile,prior['source']['new_version'],record['source_segments']['mechanism']['new_version']))
        or record.get('frozen_candidate')!=_ui_frozen_candidate(default_release_store(),original['offer_id'])
        or record.get('user_request')!=_ui_request(record.get('user_request'),task,original,record['frozen_candidate'])
        or (not historical and task.get('version')!=_version(profile)) or record.get('external_write_count')!=0
        or record.get('new_edit_attempts')!=0 or record.get('marketplace_approval') is not False):
        _fail('NATIVE_FINAL_UI_SIGNED_BINDING_CHANGED')
    return prior


R1_READER_EVENT='source_code_approved_native_r1_reader_compatibility'
R1_READER_SCHEMA='agent-prepared-approved-native-r1-reader-source/v1'
R1_READER_PURPOSE='ORIGINAL_APPROVED_NATIVE_DECISION_R1_ADOPTION_READER_COMPATIBILITY'
R1_READER_MECHANISM_FILES=frozenset({'shared_platform/agent_prepared_source_successor.py',
    'scripts/continue_agent_prepared_source.py','tests/test_approved_native_r1_reader_source.py'})
R1_READER_FILES=frozenset({'shared_platform/workbench_publication_native.py','tests/test_adopted_frozen_reader.py'})


def _historical_ui_segments(profile,old_version,segments):
    mechanism=segments['mechanism'];frontend=segments['frontend']
    if (mechanism['old_version']!=old_version or frontend['old_version']!=mechanism['new_version']
        or set(mechanism['changed_files'])-{'tests/test_native_final_image_ui.py'}!=UI_MECHANISM_FILES-{'tests/test_native_final_image_ui.py'}
        or set(frontend['changed_files'])!=UI_FILES
        or mechanism['new_version']['manifest_digest']!=frontend['new_version']['manifest_digest']):
        _fail('NATIVE_FINAL_UI_EXACT_MECHANISM_REQUIRED')
    verify_historical_source(profile.root,mechanism,allowed_files=UI_MECHANISM_FILES)
    verify_historical_source(profile.root,frontend,allowed_files=UI_FILES)
    return segments


def _r1_reader_segments(profile,old_version,mechanism_version):
    names=subprocess.check_output(['git','-C',str(profile.root),'diff','--name-only',
        old_version['code_version'],mechanism_version['code_version']],text=True).splitlines()
    if (not R1_READER_MECHANISM_FILES-{'tests/test_approved_native_r1_reader_source.py'}<=set(names)
        or not set(names)<=R1_READER_MECHANISM_FILES):_fail('R1_READER_EXACT_MECHANISM_REQUIRED')
    mechanism=dict(old_version=old_version,new_version=mechanism_version,
        changed_files={name:sha256((Path(profile.root)/name).read_bytes()).hexdigest() for name in names})
    verify_historical_source(profile.root,mechanism,allowed_files=R1_READER_MECHANISM_FILES)
    reader=source_evidence(profile,mechanism_version,allowed_files=R1_READER_FILES,
        required_changed_file='shared_platform/workbench_publication_native.py')
    return dict(mechanism=mechanism,reader=reader)


def _r1_reader_pin(pins):
    from shared_platform.immutable_approval_files import require_local_path
    if not isinstance(pins,dict) or set(pins)!={'path','sha256'}:_fail('R1_READER_IMMUTABLE_PIN_REQUIRED')
    path=Path(pins['path'])
    if not path.is_absolute():_fail('R1_READER_IMMUTABLE_PIN_REQUIRED')
    require_local_path(path,root=path.parent);raw=path.read_bytes()
    if sha256(raw).hexdigest()!=pins['sha256']:_fail('R1_READER_IMMUTABLE_PIN_CHANGED')
    return json.loads(raw)


def _r1_reader_approved(store,offer,owner_sid,frozen,work_order,preimage,*,task_id,before_append=False):
    """Preserve approval bytes forever; zero execution is an append-only gate."""
    order=_r1_reader_pin(work_order);image=_r1_reader_pin(preimage)
    authority=order.get('authority',{}).get('current_durable_native_decision',{})
    if (order.get('schema')!='orbit-new-product-b-link-work-order/v1' or order.get('revision')!=46
        or order.get('source',{}).get('new_numeric_B_offer_id')!=offer
        or order.get('native_task',{}).get('task_id')!=task_id or image.get('task',{}).get('task_id')!=task_id
        or image.get('source_head')!=order.get('source',{}).get('HEAD')
        or authority.get('verified_plan_status')!='APPROVED' or authority.get('approval_count')!=1
        or authority.get('decision_count')!=1 or authority.get('release_run_count')!=0
        or image.get('schema_version')!='post-approval-preservation-preimage/v1'
        or image.get('decision_id')!=authority.get('decision_id') or image.get('plan_id')!=frozen['plan_id']):
        _fail('R1_READER_ORIGINAL_APPROVAL_AUTHORITY_REQUIRED')
    with store._connect_readonly() as db:
        db.execute('BEGIN');rows={}
        for name,table in (('plan','release_plans'),('decision','native_sole_final_decisions'),('approval','release_approvals'),
            ('approved_publication_snapshots','approved_publication_snapshots'),('publication_business_snapshots','publication_business_snapshots')):
            found=db.execute('SELECT * FROM '+table+' WHERE plan_id=?',(frozen['plan_id'],)).fetchall()
            if len(found)!=1:_fail('R1_READER_ONE_ORIGINAL_APPROVAL_REQUIRED')
            row=dict(found[0]);expected=image[name] if name in {'plan','decision','approval'} else image['approved_snapshot_rows'][name]['rows'][0]
            # Original preimage uses json.dumps' default ensure_ascii=True.
            # Preserve that existing evidence format, including NULL/numbers.
            row_sha=sha256(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if (list(row)!=expected['column_names'] or row_sha!=expected['row_sha256']
                or any(sha256(row[k].encode()).hexdigest()!=value for k,value in expected['column_raw_sha256'].items())):
                _fail('R1_READER_ORIGINAL_APPROVAL_BYTES_CHANGED')
            rows[name]=row
        decision=rows['decision'];approval=rows['approval'];plan=rows['plan']
        if (decision['decision_id']!=authority['decision_id'] or decision['owner_sid']!=owner_sid
            or decision['review_digest']!=authority['review_digest'] or decision['review_digest']!=frozen['native_review_digest']
            or decision['decision_id']!='native-final-decision:'+sha256(decision['review_json'].encode()).hexdigest()
            or approval['approval_id']!=decision['approval_id'] or approval['status']!='APPROVED'
            or approval['user_approved']!=1 or approval['approved_by']!='Kyle' or plan['status']!='APPROVED'
            or approval['payload_digest']!=plan['payload_digest'] or approval['confirmation_token']!=plan['confirmation_token']
            or rows['approved_publication_snapshots']['snapshot_digest']!=decision['snapshot_digest']):
            _fail('R1_READER_ORIGINAL_APPROVAL_BINDING_CHANGED')
        if before_append:
            if (db.execute('SELECT 1 FROM release_runs WHERE plan_id=?',(plan['plan_id'],)).fetchone()
                or db.execute('SELECT 1 FROM release_sku_reservations WHERE plan_id=?',(plan['plan_id'],)).fetchone()
                or db.execute('SELECT 1 FROM native_final_budget_scopes WHERE plan_id=?',(plan['plan_id'],)).fetchone()):
                _fail('R1_READER_ZERO_RUN_BUDGET_DISPATCH_REQUIRED')
        return {name:sha256(adoption._bytes(row)).hexdigest() for name,row in rows.items()}


def _verify_r1_reader(task,profile,original,*,connection,owner_sid,key,events,rows,historical=False):
    if len(rows)!=1:_fail('R1_READER_SINGLE_EVENT_REQUIRED')
    prior=verify_completed_gallery_current(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,events=events,historical=True)
    ui=json.loads(connection.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=?',
        (task['task_id'],UI_EVENT)).fetchone()[0]);old=_signed(ui,key=key,schema=UI_SCHEMA,owner_sid=owner_sid)
    record=_signed(json.loads(rows[0]['detail_json']),key=key,schema=R1_READER_SCHEMA,owner_sid=owner_sid)
    from shared_platform.release_store import default_release_store
    frozen=_ui_frozen_candidate(default_release_store(),original['offer_id'])
    if (record.get('purpose')!=R1_READER_PURPOSE or record.get('task_id')!=task['task_id'] or record.get('offer_id')!=original['offer_id']
        or record.get('original_adoption_digest')!=sha256(adoption._bytes(original)).hexdigest()
        or record.get('previous_ui_digest')!=ui['record_digest'] or record.get('scope_digest')!=old['scope_digest']
        or record.get('preserved')!=prior['preserved'] or record.get('api_selection_digest')!=old['api_selection_digest']
        or record.get('frozen_candidate')!=frozen or frozen!=old['frozen_candidate']
        or record.get('source_segments')!=(_historical_r1_segments(profile,old['source_segments']['frontend']['new_version'],record['source_segments']) if historical else
            _r1_reader_segments(profile,old['source_segments']['frontend']['new_version'],record['source_segments']['mechanism']['new_version']))
        or record.get('approved_rows')!=_r1_reader_approved(default_release_store(),original['offer_id'],owner_sid,frozen,record['work_order'],record['preimage'],task_id=task['task_id'])
        or (not historical and task.get('version')!=_version(profile)) or record.get('external_write_count')!=0
        or record.get('new_edit_attempts')!=0 or record.get('marketplace_approval') is not False):
        _fail('R1_READER_SIGNED_BINDING_CHANGED')
    return prior


PLATFORM_EVENT='source_code_approved_platform_preparation_compatibility'
PLATFORM_SCHEMA='agent-prepared-approved-platform-compatibility-source/v1'
PLATFORM_MECHANISM_FILES=frozenset({'shared_platform/agent_prepared_source_successor.py',
    'scripts/continue_agent_prepared_source.py','tests/test_approved_platform_source.py'})
PLATFORM_FILES=frozenset({'shared_platform/product_publication_live_dependencies.py','modules/shopee/global_copy.py',
    'modules/products/server.py','tests/test_b0431_platform_compatibility.py'})


def _historical_r1_segments(profile,old_version,segments):
    mechanism=segments['mechanism'];reader=segments['reader']
    if (mechanism['old_version']!=old_version or reader['old_version']!=mechanism['new_version']
        or not R1_READER_MECHANISM_FILES-{'tests/test_approved_native_r1_reader_source.py'}<=set(mechanism['changed_files'])
        or 'shared_platform/workbench_publication_native.py' not in reader['changed_files']):
        _fail('R1_READER_EXACT_MECHANISM_REQUIRED')
    verify_historical_source(profile.root,mechanism,allowed_files=R1_READER_MECHANISM_FILES)
    verify_historical_source(profile.root,reader,allowed_files=R1_READER_FILES)
    return segments


def _platform_segments(profile,old_version,mechanism_version):
    names=subprocess.check_output(['git','-C',str(profile.root),'diff','--name-only',
        old_version['code_version'],mechanism_version['code_version']],text=True).splitlines()
    if (not PLATFORM_MECHANISM_FILES-{'tests/test_approved_platform_source.py'}<=set(names)
        or not set(names)<=PLATFORM_MECHANISM_FILES):_fail('PLATFORM_SOURCE_EXACT_MECHANISM_REQUIRED')
    mechanism=dict(old_version=old_version,new_version=mechanism_version,
        changed_files={name:sha256((Path(profile.root)/name).read_bytes()).hexdigest() for name in names})
    verify_historical_source(profile.root,mechanism,allowed_files=PLATFORM_MECHANISM_FILES)
    compatibility=source_evidence(profile,mechanism_version,allowed_files=PLATFORM_FILES,
        required_changed_file='shared_platform/product_publication_live_dependencies.py')
    if not PLATFORM_FILES-{'tests/test_b0431_platform_compatibility.py'}<=set(compatibility['changed_files']):
        _fail('PLATFORM_SOURCE_EXACT_COMPATIBILITY_REQUIRED')
    return dict(mechanism=mechanism,compatibility=compatibility)


def _platform_history(store,task_id,frozen,request,run_preimage,ozon_readback,*,before_append=False):
    """Bind prior effects; later managed retries may grow this approved history."""
    intent=_r1_reader_pin(request);image=_r1_reader_pin(run_preimage);ozon=_r1_reader_pin(ozon_readback)
    if (intent.get('schema')!='original-approved-platform-compatibility-request/v1'
        or intent.get('task_id')!=task_id or intent.get('offer_id')!=str(image['run']['plan_id']).split(':')[1]
        or intent.get('plan_id')!=frozen['plan_id'] or intent.get('candidate_digest')!=frozen['candidate_digest']
        or intent.get('review_digest')!=frozen['native_review_digest']
        or intent.get('payload_digest')!=frozen['payload_digest']
        or intent.get('decision_id')!='native-final-decision:'+frozen['native_review_digest'].removeprefix('sha256:')
        or intent.get('original_run_id')!=image['run']['run_id'] or intent.get('source_head')!=image.get('source_head')
        or any(intent.get(k) is not False for k in ('new_marketplace_approval','reimport_ozon'))
        or any(intent.get(k) is not True for k in ('preserve_original_approval_candidate_content_common_images_and_targets',
            'preserve_attempts_failures_submissions_readbacks_budgets_and_source_history'))
        or image['run']['plan_id']!=frozen['plan_id'] or image.get('all_targets_terminal') is not True
        or ozon.get('schema_version')!='original-Ozon-three-imports-readonly/v1'
        or ozon.get('original_task_ids')!=[5906902718,5906902998,5906903305]
        or {str(r['offer_id']):r['id'] for r in ozon.get('normalized_identities',[])}!={'2000431':6760984630,'2000432':6760985024,'2000433':6760985460}
        or any(r['statuses'].get('moderate_status')!='approved' or r['statuses'].get('is_created') is not True for r in ozon['normalized_identities'])):
        _fail('PLATFORM_SOURCE_ORIGINAL_HISTORY_PINS_CHANGED')
    labels={r['target_label'] for r in image['release_target_runs']}
    if labels!=set(frozen['ordered_targets']) or any(r['status']!='FAILED' or r['attempts']!=1 for r in image['release_target_runs']):
        _fail('PLATFORM_SOURCE_ORIGINAL_ATTEMPT_REQUIRED')
    zero_labels=labels-{'ozon:RU'}
    if set(intent.get('known_zero_targets',[]))!=zero_labels or intent.get('limits',{}).get('source_event_max')!=1:
        _fail('PLATFORM_SOURCE_ORIGINAL_HISTORY_PINS_CHANGED')
    ozon_targets=[r for r in image['release_target_runs'] if r['target_label']=='ozon:RU']
    original_imports='ozon:import-task:5906902718,ozon:import-task:5906902998,ozon:import-task:5906903305'
    if (len(ozon_targets)!=1 or ozon_targets[0]['external_id']!=original_imports
        or len(image['release_target_submissions'])!=1 or image['release_target_submissions'][0]['target_label']!='ozon:RU'
        or image['release_target_submissions'][0]['external_id']!=original_imports
        or any(r['target_label'] in zero_labels for r in image['native_final_budget_reservations'])):
        _fail('PLATFORM_SOURCE_ORIGINAL_OZON_IMPORTS_REQUIRED')
    if {r['target_label'] for r in image['release_target_failure_events']}!=zero_labels:
        _fail('PLATFORM_SOURCE_TYPED_ZERO_FAILURES_REQUIRED')
    for row in image['release_target_failure_events']:
        value=json.loads(row['evidence_json'])
        if (row['attempt']!=1 or sha256(row['evidence_json'].encode()).hexdigest()!=row['evidence_digest']
            or value.get('known_pre_submit_zero') is not True or value.get('request_attempted') is not False
            or value.get('outcome_unknown') is not False or value.get('external_writes_performed')!=[]
            or value.get('mutation_budget',{}).get('reservations')!=[]):_fail('PLATFORM_SOURCE_TYPED_ZERO_FAILURES_REQUIRED')
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        run=db.execute('SELECT * FROM release_runs WHERE run_id=?',(image['run']['run_id'],)).fetchone()
        if run is None:_fail('PLATFORM_SOURCE_ORIGINAL_RUN_CHANGED')
        if before_append and db.execute('SELECT COUNT(*) FROM release_runs WHERE plan_id=?',(frozen['plan_id'],)).fetchone()[0]!=1:
            _fail('PLATFORM_SOURCE_ORIGINAL_RUN_CHANGED')
        fixed=('run_id','plan_id','approval_id','created_at','technical_admission_json','technical_admission_digest','technical_execution_state')
        if any(run[k]!=image['run'][k] for k in fixed) or (before_append and dict(run)!=image['run']):
            _fail('PLATFORM_SOURCE_ORIGINAL_RUN_CHANGED')
        for table in ('release_target_runs','release_target_submissions','release_target_readbacks','release_target_failure_events'):
            current=[dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE run_id=?',(run['run_id'],))];prior=image[table]
            if before_append:
                if sorted(adoption._bytes(r) for r in current)!=sorted(adoption._bytes(r) for r in prior):_fail('PLATFORM_SOURCE_ORIGINAL_ATTEMPT_CHANGED')
            elif table=='release_target_runs':
                by_label={r['target_label']:r for r in current}
                if any(r['target_label'] not in by_label or any(by_label[r['target_label']][k]!=r[k] for k in ('run_id','idempotency_key','created_at'))
                    or by_label[r['target_label']]['attempts']<r['attempts'] for r in prior):_fail('PLATFORM_SOURCE_ORIGINAL_ATTEMPT_CHANGED')
            elif table=='release_target_submissions':
                immutable=('run_id','target_label','external_id','evidence_json','evidence_digest','submitted_at')
                if any(not any(all(r[k]==old[k] for k in immutable) for r in current) for old in prior):_fail('PLATFORM_SOURCE_ORIGINAL_SUBMISSION_CHANGED')
            elif any(adoption._bytes(r) not in {adoption._bytes(v) for v in current} for r in prior):_fail('PLATFORM_SOURCE_ORIGINAL_EVIDENCE_CHANGED')
        for table in ('native_final_budget_scopes','native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes'):
            sql=('SELECT * FROM '+table+' WHERE plan_id=?' if table.endswith('_scopes') else
                'SELECT b.* FROM '+table+' b JOIN native_final_budget_scopes s USING(scope_key) WHERE s.plan_id=?')
            current={adoption._bytes(dict(r)) for r in db.execute(sql,(frozen['plan_id'],))};prior={adoption._bytes(r) for r in image[table]}
            if (before_append and current!=prior) or not prior<=current:_fail('PLATFORM_SOURCE_ORIGINAL_BUDGET_CHANGED')
    return dict(original_run_id=image['run']['run_id'],original_history_digest=sha256(adoption._bytes(image)).hexdigest(),
        original_ozon_tasks=ozon['original_task_ids'],original_ozon_products={'2000431':6760984630,'2000432':6760985024,'2000433':6760985460})


def _verify_platform_compatibility(task,profile,original,*,connection,owner_sid,key,events,rows,historical=False):
    if len(rows)!=1:_fail('PLATFORM_SOURCE_SINGLE_EVENT_REQUIRED')
    reader_rows=connection.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task['task_id'],R1_READER_EVENT)).fetchall()
    prior=_verify_r1_reader(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,events=events,rows=reader_rows,historical=True)
    old=_signed(json.loads(reader_rows[0][0]),key=key,schema=R1_READER_SCHEMA,owner_sid=owner_sid)
    record=_signed(json.loads(rows[0][0]),key=key,schema=PLATFORM_SCHEMA,owner_sid=owner_sid)
    from shared_platform.release_store import default_release_store
    if (record.get('task_id')!=task['task_id'] or record.get('offer_id')!=original['offer_id']
        or record.get('original_adoption_digest')!=sha256(adoption._bytes(original)).hexdigest()
        or record.get('previous_r1_reader_digest')!=json.loads(reader_rows[0][0])['record_digest']
        or record.get('scope_digest')!=old['scope_digest'] or record.get('frozen_candidate')!=old['frozen_candidate']
        or _r1_reader_pin(record['technical_request']).get('source_head')!=old['source_segments']['reader']['new_version']['code_version']
        or record.get('source_segments')!=(_historical_platform_segments(profile,old['source_segments']['reader']['new_version'],record['source_segments']) if historical else
            _platform_segments(profile,old['source_segments']['reader']['new_version'],record['source_segments']['mechanism']['new_version']))
        or record.get('history')!=_platform_history(default_release_store(),task['task_id'],old['frozen_candidate'],record['technical_request'],record['run_preimage'],record['ozon_readback'])
        or (not historical and task.get('version')!=_version(profile)) or record.get('external_write_count')!=0 or record.get('marketplace_approval') is not False):
        _fail('PLATFORM_SOURCE_SIGNED_BINDING_CHANGED')
    return prior


NATIVE_RECOVERY_EVENT='source_code_approved_native_tiktok_ozon_recovery_compatibility'
NATIVE_RECOVERY_SCHEMA='agent-prepared-approved-native-tiktok-ozon-recovery-source/v1'
NATIVE_RECOVERY_PURPOSE='ORIGINAL_NATIVE_TIKTOK_ZERO_AND_OZON_THREE_CARD_MEDIA_STOCK_RECOVERY'
NATIVE_RECOVERY_MECHANISM_FILES=frozenset({'shared_platform/agent_prepared_source_successor.py',
    'scripts/continue_agent_prepared_source.py','tests/test_native_tiktok_ozon_recovery_source.py'})
NATIVE_RECOVERY_FILES=frozenset({'shared_platform/native_ozon_three_card_media_recovery.py',
    'shared_platform/agent_prepared_v4_targets.py','modules/products/server.py',
    'shared_platform/native_sole_final_service.py','modules/ozon/approved_publication_v4.py',
    'tests/test_b0431_native_tiktok_zero.py','tests/test_b0431_ozon_media_recovery.py'})
NATIVE_RECOVERY_TARGETS=frozenset({'tiktok:LH_MY','tiktok:LH_PH','tiktok:LH_TH','tiktok:LH_VN'})


def _historical_platform_segments(profile,old_version,segments):
    mechanism=segments['mechanism'];compatibility=segments['compatibility']
    if (mechanism['old_version']!=old_version or compatibility['old_version']!=mechanism['new_version']
        or not PLATFORM_MECHANISM_FILES-{'tests/test_approved_platform_source.py'}<=set(mechanism['changed_files'])
        or not PLATFORM_FILES-{'tests/test_b0431_platform_compatibility.py'}<=set(compatibility['changed_files'])):
        _fail('PLATFORM_SOURCE_EXACT_MECHANISM_REQUIRED')
    verify_historical_source(profile.root,mechanism,allowed_files=PLATFORM_MECHANISM_FILES)
    verify_historical_source(profile.root,compatibility,allowed_files=PLATFORM_FILES)
    return segments


def _native_recovery_segments(profile,old_version,mechanism_version):
    names=subprocess.check_output(['git','-C',str(profile.root),'diff','--name-only',
        old_version['code_version'],mechanism_version['code_version']],text=True).splitlines()
    if (not NATIVE_RECOVERY_MECHANISM_FILES-{'tests/test_native_tiktok_ozon_recovery_source.py'}<=set(names)
        or not set(names)<=NATIVE_RECOVERY_MECHANISM_FILES):_fail('NATIVE_RECOVERY_SOURCE_EXACT_MECHANISM_REQUIRED')
    mechanism=dict(old_version=old_version,new_version=mechanism_version,
        changed_files={name:sha256((Path(profile.root)/name).read_bytes()).hexdigest() for name in names})
    verify_historical_source(profile.root,mechanism,allowed_files=NATIVE_RECOVERY_MECHANISM_FILES)
    retry=source_evidence(profile,mechanism_version,allowed_files=NATIVE_RECOVERY_FILES,required_changed_file='modules/products/server.py')
    if not {name for name in NATIVE_RECOVERY_FILES if not name.startswith('tests/')}<=set(retry['changed_files']):
        _fail('NATIVE_RECOVERY_SOURCE_EXACT_COMBINED_FILES_REQUIRED')
    return dict(mechanism=mechanism,retry=retry)


def _native_recovery_media3(request,ref):
    """Only bind the explicit finite authorization; this never creates its grant."""
    intent=_r1_reader_pin(request)
    if not isinstance(ref,dict) or set(ref)!={'path','sha256'} or intent.get('ozon_media3_decision_ref')!=ref:
        _fail('NATIVE_RECOVERY_SOURCE_MEDIA3_PIN_REQUIRED')
    _r1_reader_pin(ref)
    from shared_platform.native_ozon_three_card_media_recovery import read_media3_decision
    decision=read_media3_decision(ref)
    return sha256(adoption._bytes(decision)).hexdigest()


def _native_recovery_history(store,task_id,frozen,request,run_preimage,*,before_append=False):
    """Freeze this idle run, including all real Shopee/Ozon effects, without zeroing them."""
    intent=_r1_reader_pin(request);image=_r1_reader_pin(run_preimage)
    if (intent.get('schema')!='original-approved-native-tiktok-ozon-recovery-source-request/v1'
        or intent.get('task_id')!=task_id or intent.get('offer_id')!=frozen['plan_id'].split(':')[1]
        or any(intent.get(k)!=frozen[v] for k,v in (('plan_id','plan_id'),('candidate_digest','candidate_digest'),('payload_digest','payload_digest'),('review_digest','native_review_digest')))
        or intent.get('decision_id')!='native-final-decision:'+frozen['native_review_digest'].removeprefix('sha256:')
        or intent.get('source_head')!=image.get('source_head') or intent.get('original_run_id')!=image['run']['run_id']
        or image['run']['plan_id']!=frozen['plan_id'] or image.get('all_targets_idle') is not True
        or set(intent.get('known_zero_targets',[]))!=NATIVE_RECOVERY_TARGETS or intent.get('limits',{}).get('source_event_max')!=1
        or intent.get('append_requires_original_worker_and_all_mutations_idle') is not True
        or intent.get('Shopee_must_be_zero_or_terminal') is not False
        or any(intent.get(k) is not False for k in ('new_marketplace_approval','reimport_ozon'))
        or any(intent.get(k) is not True for k in ('preserve_original_approval_candidate_content_common_images_and_targets',
            'preserve_attempts_failures_submissions_readbacks_budgets_and_source_history'))):
        _fail('NATIVE_RECOVERY_SOURCE_EXACT_HISTORY_PINS_REQUIRED')
    targets={r['target_label']:r for r in image['release_target_runs']}
    failures=[r for r in image['release_target_failure_events'] if r['target_label'] in NATIVE_RECOVERY_TARGETS]
    if (set(targets)!=set(frozen['ordered_targets']) or {r['target_label'] for r in failures}!=NATIVE_RECOVERY_TARGETS or len(failures)!=4
        or any(r['status'] not in {'PENDING','FAILED','SUCCEEDED','VERIFIED'} for r in targets.values())
        or any(targets[k]['status']!='FAILED' or targets[k]['attempts']!=1 for k in NATIVE_RECOVERY_TARGETS)
        or any(r['target_label'] in NATIVE_RECOVERY_TARGETS for r in image['native_final_budget_reservations']+image['release_target_submissions']+image['release_target_readbacks'])):
        _fail('NATIVE_RECOVERY_SOURCE_UNDISPATCHED_FOUR_REQUIRED')
    # This is the pinned idle baseline, not a requirement on future mutations.
    reserved={(r['scope_key'],r['sequence']) for r in image['native_final_budget_reservations']}
    dispatched={(r['scope_key'],r['sequence']) for r in image['native_final_budget_dispatches']}
    settled={(r['scope_key'],r['sequence']) for r in image['native_final_budget_outcomes'] if r['outcome'] in {'ACCEPTED','REJECTED'}}
    if reserved!=dispatched or dispatched!=settled:_fail('NATIVE_RECOVERY_SOURCE_UNKNOWN_INFLIGHT_REQUIRED_RESOLUTION')
    for row in failures:
        proof=json.loads(row['evidence_json'])
        if (row['attempt']!=1 or sha256(row['evidence_json'].encode()).hexdigest()!=row['evidence_digest']
            or proof.get('schema_version')!='native-scoped-v4-target-result/v1' or proof.get('target_label')!=row['target_label']
            or proof.get('known_pre_submit_zero') is not True or proof.get('request_attempted') is not False
            or proof.get('outcome_unknown') is not False or proof.get('external_writes_performed')!=[]
            or proof.get('mutation_budget',{}).get('reservations')!=[]):_fail('NATIVE_RECOVERY_SOURCE_NATIVE_WRAPPER_PROOF_REQUIRED')
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        row=db.execute('SELECT * FROM release_runs WHERE run_id=?',(image['run']['run_id'],)).fetchone()
        fixed=('run_id','plan_id','approval_id','created_at','technical_admission_json','technical_admission_digest','technical_execution_state')
        if row is None or any(row[k]!=image['run'][k] for k in fixed) or (before_append and dict(row)!=image['run']):
            _fail('NATIVE_RECOVERY_SOURCE_ORIGINAL_RUN_CHANGED')
        for table in ('release_target_runs','release_target_submissions','release_target_readbacks','release_target_failure_events'):
            current=[dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE run_id=?',(image['run']['run_id'],))];old=image[table]
            if before_append:
                if sorted(adoption._bytes(r) for r in current)!=sorted(adoption._bytes(r) for r in old):_fail('NATIVE_RECOVERY_SOURCE_HISTORY_CAS_CHANGED')
            elif table=='release_target_runs':
                mapped={r['target_label']:r for r in current}
                if any(r['target_label'] not in mapped or mapped[r['target_label']]['attempts']<r['attempts']
                    or any(mapped[r['target_label']][k]!=r[k] for k in ('run_id','idempotency_key','created_at')) for r in old):_fail('NATIVE_RECOVERY_SOURCE_ORIGINAL_TARGET_CHANGED')
            elif table=='release_target_submissions':
                fields=('run_id','target_label','external_id','evidence_json','evidence_digest','submitted_at')
                if any(not any(all(r[k]==v[k] for k in fields) for r in current) for v in old):_fail('NATIVE_RECOVERY_SOURCE_PRIOR_SUBMISSION_CHANGED')
            elif not {adoption._bytes(r) for r in old}<={adoption._bytes(r) for r in current}:_fail('NATIVE_RECOVERY_SOURCE_PRIOR_EVIDENCE_CHANGED')
        for table in ('native_final_budget_scopes','native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes'):
            query=('SELECT * FROM '+table+' WHERE plan_id=?' if table.endswith('_scopes') else
                'SELECT b.* FROM '+table+' b JOIN native_final_budget_scopes s USING(scope_key) WHERE s.plan_id=?')
            current={adoption._bytes(dict(r)) for r in db.execute(query,(frozen['plan_id'],))};old={adoption._bytes(r) for r in image[table]}
            if not old<=current or (before_append and current!=old):_fail('NATIVE_RECOVERY_SOURCE_PRIOR_BUDGET_CHANGED')
    return dict(original_run_id=image['run']['run_id'],idle_history_digest=sha256(adoption._bytes(image)).hexdigest(),known_zero_targets=sorted(NATIVE_RECOVERY_TARGETS))


def _verify_native_recovery_source(task,profile,original,*,connection,owner_sid,key,events,rows,historical=False):
    if len(rows)!=1:_fail('NATIVE_RECOVERY_SOURCE_SINGLE_EVENT_REQUIRED')
    # Authenticate this distinct purpose before allowing historical platform source.
    record=_signed(json.loads(rows[0][0]),key=key,schema=NATIVE_RECOVERY_SCHEMA,owner_sid=owner_sid)
    if record.get('purpose')!=NATIVE_RECOVERY_PURPOSE:_fail('NATIVE_RECOVERY_SOURCE_PURPOSE_REQUIRED')
    previous=connection.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task['task_id'],PLATFORM_EVENT)).fetchall()
    prior=_verify_platform_compatibility(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,events=events,rows=previous,historical=True)
    old=_signed(json.loads(previous[0][0]),key=key,schema=PLATFORM_SCHEMA,owner_sid=owner_sid)
    from shared_platform.release_store import default_release_store
    if (record.get('task_id')!=task['task_id'] or record.get('offer_id')!=original['offer_id']
        or record.get('original_adoption_digest')!=sha256(adoption._bytes(original)).hexdigest()
        or record.get('previous_platform_digest')!=json.loads(previous[0][0])['record_digest']
        or record.get('scope_digest')!=old['scope_digest'] or record.get('frozen_candidate')!=old['frozen_candidate']
        or record.get('ozon_media3_authorization_digest')!=_native_recovery_media3(record['technical_request'],record.get('ozon_media3_decision_ref'))
        or _r1_reader_pin(record['technical_request']).get('source_head')!=old['source_segments']['compatibility']['new_version']['code_version']
        or record.get('source_segments')!=(_historical_native_recovery_segments(profile,old['source_segments']['compatibility']['new_version'],record['source_segments']) if historical else
            _native_recovery_segments(profile,old['source_segments']['compatibility']['new_version'],record['source_segments']['mechanism']['new_version']))
        or record.get('history')!=_native_recovery_history(default_release_store(),task['task_id'],old['frozen_candidate'],record['technical_request'],record['run_preimage'])
        or (not historical and task.get('version')!=_version(profile)) or record.get('external_write_count')!=0 or record.get('marketplace_approval') is not False):
        _fail('NATIVE_RECOVERY_SOURCE_SIGNED_BINDING_CHANGED')
    return prior


def continue_native_tiktok_ozon_recovery_source(engine,profile,store,*,task_id,expected_adoption_digest,actor_reader,
    mechanism_head,mechanism_manifest,technical_request,run_preimage,ozon_media3_decision_ref):
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    sid,key=adoption._owner_key(profile);actor=read_native_actor_identity(actor_reader)
    if actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER' or actor.get('owner_sid')!=sid:
        _fail('SOURCE_SUCCESSOR_TRUSTED_SAME_USER_REQUIRED')
    with engine.transaction() as db:
        before=dict(engine._row(db,task_id));task=engine._project(db,before)
        row=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(task_id,)).fetchone()
        original=_signed(row,key=key,schema=adoption.SCHEMA,owner_sid=sid)
        if row['record_digest']!=expected_adoption_digest:_fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
        if db.execute('SELECT 1 FROM workbench_events WHERE task_id=? AND event_type=?',(task_id,NATIVE_RECOVERY_EVENT)).fetchone():_fail('NATIVE_RECOVERY_SOURCE_SINGLE_EVENT_REQUIRED')
        events=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task_id,GALLERY_EVENT)).fetchall()
        previous=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task_id,PLATFORM_EVENT)).fetchall()
        _verify_platform_compatibility(task,profile,original,connection=db,owner_sid=sid,key=key,events=events,rows=previous,historical=True)
        old=_signed(json.loads(previous[0][0]),key=key,schema=PLATFORM_SCHEMA,owner_sid=sid)
        oldevents=[tuple(r) for r in db.execute('SELECT * FROM workbench_events WHERE task_id=? ORDER BY id',(task_id,))]
    version=old['source_segments']['compatibility']['new_version']
    if (json.loads(before['version_json'])!=version or before['state']!='reconciliation_required' or before['step_index']!=2
        or before['worker'] is not None or before['lease_token'] is not None or before['lease_until'] is not None or before['external_started']):
        _fail('NATIVE_RECOVERY_SOURCE_IDLE_ORIGINAL_RUN_REQUIRED')
    if _r1_reader_pin(technical_request).get('source_head')!=version['code_version']:_fail('NATIVE_RECOVERY_SOURCE_ORIGINAL_SOURCE_CHANGED')
    segments=_native_recovery_segments(profile,version,dict(code_version=mechanism_head,environment='stable',manifest_digest=mechanism_manifest))
    authorization=_native_recovery_media3(technical_request,ozon_media3_decision_ref)
    history=_native_recovery_history(store,task_id,old['frozen_candidate'],technical_request,run_preimage,before_append=True)
    record=dict(schema_version=NATIVE_RECOVERY_SCHEMA,purpose=NATIVE_RECOVERY_PURPOSE,task_id=task_id,offer_id=original['offer_id'],owner_sid=sid,
        continued_at=datetime.now(timezone.utc).isoformat(),original_adoption_digest=expected_adoption_digest,
        previous_platform_digest=json.loads(previous[0][0])['record_digest'],scope_digest=old['scope_digest'],
        source_segments=segments,frozen_candidate=old['frozen_candidate'],history=history,technical_request=technical_request,run_preimage=run_preimage,
        ozon_media3_decision_ref=ozon_media3_decision_ref,ozon_media3_authorization_digest=authorization,external_write_count=0,marketplace_approval=False)
    raw=adoption._bytes(record)
    with engine.transaction() as db:
        if (dict(engine._row(db,task_id))!=before or [tuple(r) for r in db.execute('SELECT * FROM workbench_events WHERE task_id=? ORDER BY id',(task_id,))]!=oldevents
            or db.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state='inflight'",(task_id,)).fetchone()
            or _native_recovery_media3(technical_request,ozon_media3_decision_ref)!=authorization
            or _native_recovery_history(store,task_id,old['frozen_candidate'],technical_request,run_preimage,before_append=True)!=history):
            _fail('NATIVE_RECOVERY_SOURCE_CAS_CHANGED')
        engine._event(db,task_id,NATIVE_RECOVERY_EVENT,dict(record_json=raw.decode(),record_digest=sha256(raw).hexdigest(),signature=hmac.new(key,raw,'sha256').hexdigest()))
        db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',(adoption._bytes(_version(profile)).decode(),task_id))
    return record


STOCK_CATEGORY_EVENT='source_code_approved_native_stock_category_continuation_compatibility'
STOCK_CATEGORY_SCHEMA='agent-prepared-approved-stock-category-continuation-source/v1'
STOCK_CATEGORY_PURPOSE='ORIGINAL_APPROVED_TIKTOK_STOCK_SHOPEE_CATEGORY_AND_DRAFT_CONTINUATION'
STOCK_CATEGORY_MECHANISM_FILES=frozenset({'shared_platform/agent_prepared_source_successor.py',
    'scripts/continue_agent_prepared_source.py','tests/test_native_stock_category_source.py'})
STOCK_CATEGORY_FILES=frozenset({'modules/miaoshou/tiktok_v4_drafts.py','modules/products/server.py',
    'modules/shopee/global_v4_live_runtime.py','tests/test_b0431_tiktok_approved_stock.py',
    'tests/test_b0431_shopee_attribute_identity.py'})
STOCK_CATEGORY_SHOPEE=frozenset({'shopee:MY','shopee:PH','shopee:TH','shopee:VN'})
STOCK_CATEGORY_MEDIA_TABLES=tuple('native_ozon_media_'+name for name in ('grants','attempts','dispatches','outcomes','verifications','starts'))


def _historical_native_recovery_segments(profile,old_version,segments):
    mechanism=segments['mechanism'];retry=segments['retry']
    if (mechanism['old_version']!=old_version or retry['old_version']!=mechanism['new_version']
        or not NATIVE_RECOVERY_MECHANISM_FILES-{'tests/test_native_tiktok_ozon_recovery_source.py'}<=set(mechanism['changed_files'])
        or not {name for name in NATIVE_RECOVERY_FILES if not name.startswith('tests/')}<=set(retry['changed_files'])):
        _fail('STOCK_CATEGORY_SOURCE_ORIGINAL_SEGMENTS_REQUIRED')
    verify_historical_source(profile.root,mechanism,allowed_files=NATIVE_RECOVERY_MECHANISM_FILES)
    verify_historical_source(profile.root,retry,allowed_files=NATIVE_RECOVERY_FILES)
    return segments


def _stock_category_segments(profile,old_version,mechanism_version):
    names=subprocess.check_output(['git','-C',str(profile.root),'diff','--name-only',old_version['code_version'],mechanism_version['code_version']],text=True).splitlines()
    if (not STOCK_CATEGORY_MECHANISM_FILES-{'tests/test_native_stock_category_source.py'}<=set(names)
        or not set(names)<=STOCK_CATEGORY_MECHANISM_FILES):_fail('STOCK_CATEGORY_SOURCE_EXACT_MECHANISM_REQUIRED')
    mechanism=dict(old_version=old_version,new_version=mechanism_version,
        changed_files={name:sha256((Path(profile.root)/name).read_bytes()).hexdigest() for name in names})
    verify_historical_source(profile.root,mechanism,allowed_files=STOCK_CATEGORY_MECHANISM_FILES)
    correction=source_evidence(profile,mechanism_version,allowed_files=STOCK_CATEGORY_FILES,required_changed_file='modules/miaoshou/tiktok_v4_drafts.py')
    if not {name for name in STOCK_CATEGORY_FILES if not name.startswith('tests/')}<=set(correction['changed_files']):
        _fail('STOCK_CATEGORY_SOURCE_EXACT_FILES_REQUIRED')
    return dict(mechanism=mechanism,correction=correction)


def _stock_category_preparation(proof,label,checkpoint):
    canonical=proof.get('canonical_platform_result',{});zero=label in STOCK_CATEGORY_SHOPEE
    targets=canonical.get('targets',[])
    detail=targets[0].get('evidence',{}) if len(targets)==1 and targets[0].get('target_label')==label else {}
    positions=[i+1 for i,event in enumerate(checkpoint['events']) if event['target_label']==label]
    # Durable preparation reports the shared checkpoint's cumulative count,
    # whereas this target's detail reports its own create+claim pair.
    return (canonical.get('schema_version')=='product-publication-platform-result/v1'
        and canonical.get('dispatch_attempted') is False
        and type(canonical.get('external_write_count')) is int
        and canonical['external_write_count']==(0 if zero else positions[-1] if positions else None)
        and type(detail.get('external_write_count')) is int and detail['external_write_count']==(0 if zero else 2)
        and detail.get('outcome_unknown') is False)


def _stock_category_history(store,task_id,frozen,request,run_preimage,*,before_append=False):
    """Retain real accepted drafts and zero SH failures; never relabel TK zero."""
    intent=_r1_reader_pin(request);image=_r1_reader_pin(run_preimage)
    if (intent.get('schema')!='original-approved-stock-category-continuation-source-request/v1'
        or intent.get('task_id')!=task_id or intent.get('offer_id')!=frozen['plan_id'].split(':')[1]
        or any(intent.get(k)!=frozen[v] for k,v in (('plan_id','plan_id'),('candidate_digest','candidate_digest'),('payload_digest','payload_digest'),('review_digest','native_review_digest')))
        or intent.get('decision_id')!='native-final-decision:'+frozen['native_review_digest'].removeprefix('sha256:')
        or intent.get('source_head')!=image.get('source_head') or intent.get('original_run_id')!=image['run']['run_id']
        or image['run']['plan_id']!=frozen['plan_id'] or image.get('all_targets_idle') is not True
        or set(intent.get('accepted_draft_targets',[]))!=NATIVE_RECOVERY_TARGETS
        or set(intent.get('known_zero_targets',[]))!=STOCK_CATEGORY_SHOPEE
        or intent.get('limits',{}).get('source_event_max')!=1 or intent.get('append_requires_all_mutations_idle') is not True
        or any(intent.get(k) is not False for k in ('new_marketplace_approval','recreate_or_reclaim','reset_or_increase_budget'))
        or intent.get('preserve_all_original_content_effects_and_history') is not True
        or type(intent.get('approved_stock_quantity_per_sku')) is not int or intent['approved_stock_quantity_per_sku']!=200
        or set(intent.get('diagnostics',{}))!={'tiktok_stock','shopee_category'}):
        _fail('STOCK_CATEGORY_SOURCE_EXACT_HISTORY_PINS_REQUIRED')
    for ref in intent['diagnostics'].values():_r1_reader_pin(ref)
    targets={r['target_label']:r for r in image['release_target_runs']}
    second=[r for r in image['release_target_failure_events'] if r['attempt']==2 and r['target_label'] in NATIVE_RECOVERY_TARGETS|STOCK_CATEGORY_SHOPEE]
    if (set(targets)!=set(frozen['ordered_targets'])
        or any(r['status'] not in {'PENDING','FAILED','SUCCEEDED','VERIFIED'} for r in targets.values())
        or any(targets[k]['status']!='FAILED' or targets[k]['attempts']!=2 for k in NATIVE_RECOVERY_TARGETS|STOCK_CATEGORY_SHOPEE)
        or {r['target_label'] for r in second}!=NATIVE_RECOVERY_TARGETS|STOCK_CATEGORY_SHOPEE or len(second)!=8):
        _fail('STOCK_CATEGORY_SOURCE_ORIGINAL_FAILED2_REQUIRED')
    mapping=None;checkpoint_root=None
    for row in second:
        proof=json.loads(row['evidence_json']);zero=row['target_label'] in STOCK_CATEGORY_SHOPEE
        if (sha256(row['evidence_json'].encode()).hexdigest()!=row['evidence_digest']
            or proof.get('schema_version')!='native-scoped-v4-target-result/v1' or proof.get('target_label')!=row['target_label']
            or proof.get('known_pre_submit_zero') is not zero or proof.get('request_attempted') is not False or proof.get('outcome_unknown') is not False
            or proof.get('external_writes_performed')!=([] if zero else [row['target_label']])
            or not _stock_category_preparation(proof,row['target_label'],image['tiktok_checkpoint'])):
            _fail('STOCK_CATEGORY_SOURCE_TYPED_ORIGINAL_EFFECTS_REQUIRED')
        identity=proof['checkpoint_file_identity']
        if (identity.get('release_run_id')!=image['run']['run_id'] or identity.get('plan_id')!=frozen['plan_id']
            or identity.get('decision_id')!=intent['decision_id'] or identity.get('execution_snapshot_digest')!=intent.get('snapshot_digest')):
            _fail('STOCK_CATEGORY_SOURCE_CHECKPOINT_IDENTITY_CHANGED')
        if not zero:
            if mapping is not None and mapping!=identity:_fail('STOCK_CATEGORY_SOURCE_CHECKPOINT_IDENTITY_CHANGED')
            mapping=identity
            identity_ref=proof['checkpoint_file_identity_ref']
            if (_r1_reader_pin(identity_ref)!=identity
                or Path(identity_ref['path']).name!=identity['file_run_id']+'.json'
                or Path(identity_ref['path']).parent.name!='native-final-file-identities'):
                _fail('STOCK_CATEGORY_SOURCE_CHECKPOINT_IDENTITY_CHANGED')
            root=Path(identity_ref['path']).parent.parent
            if checkpoint_root is not None and checkpoint_root.resolve()!=root.resolve():_fail('STOCK_CATEGORY_SOURCE_CHECKPOINT_IDENTITY_CHANGED')
            checkpoint_root=root
    reserved={(r['scope_key'],r['sequence']) for r in image['native_final_budget_reservations']}
    dispatched={(r['scope_key'],r['sequence']) for r in image['native_final_budget_dispatches']}
    settled={(r['scope_key'],r['sequence']) for r in image['native_final_budget_outcomes'] if r['outcome'] in {'ACCEPTED','REJECTED'}}
    if reserved!=dispatched or dispatched!=settled:_fail('STOCK_CATEGORY_SOURCE_UNKNOWN_INFLIGHT')
    media=[{r['grant_id'] for r in image[table]} for table in STOCK_CATEGORY_MEDIA_TABLES[:4]]
    if not all(value==media[0] for value in media):_fail('STOCK_CATEGORY_SOURCE_UNKNOWN_INFLIGHT')
    scopes={r['scope_key']:r for r in image['native_final_budget_scopes']}
    tk=[r for r in image['native_final_budget_reservations'] if scopes[r['scope_key']]['platform']=='TIKTOK']
    if (len(tk)!=8 or any(scopes[r['scope_key']]['platform']=='SHOPEE' for r in image['native_final_budget_reservations'])
        or any(r['target_label'] in STOCK_CATEGORY_SHOPEE for r in image['release_target_submissions']+image['release_target_readbacks'])
        or any({r['operation'] for r in tk if r['target_label']==label}!={'create_platform_draft','claim_draft'}
            or len([r for r in tk if r['target_label']==label])!=2 for label in NATIVE_RECOVERY_TARGETS)
        or any(next(r['outcome'] for r in image['native_final_budget_outcomes'] if (r['scope_key'],r['sequence'])==(v['scope_key'],v['sequence']))!='ACCEPTED' for v in tk)):
        _fail('STOCK_CATEGORY_SOURCE_EXACT_ACCEPTED_DRAFT_BUDGET_REQUIRED')
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        snapshot_row=db.execute('SELECT snapshot_json,snapshot_digest FROM approved_publication_snapshots WHERE plan_id=?',(frozen['plan_id'],)).fetchone()
        if (snapshot_row is None or snapshot_row['snapshot_digest']!=intent.get('snapshot_digest')
            or sha256(snapshot_row['snapshot_json'].encode()).hexdigest()!=intent.get('approved_snapshot_json_sha256')):
            _fail('STOCK_CATEGORY_SOURCE_ORIGINAL_SNAPSHOT_CHANGED')
        snapshot=json.loads(snapshot_row['snapshot_json'])
        from domains.product_operations.approved_publication_snapshot import _publication_stock_policy
        policy=_publication_stock_policy(snapshot['product'].get('stock_policy'))
        if policy is None or policy['quantity_per_sku']!=200:_fail('STOCK_CATEGORY_SOURCE_ORIGINAL_STOCK_REQUIRED')
        row=db.execute('SELECT * FROM release_runs WHERE run_id=?',(image['run']['run_id'],)).fetchone()
        fixed=('run_id','plan_id','approval_id','created_at','technical_admission_json','technical_admission_digest','technical_execution_state')
        if row is None or any(row[k]!=image['run'][k] for k in fixed) or (before_append and dict(row)!=image['run']):_fail('STOCK_CATEGORY_SOURCE_ORIGINAL_RUN_CHANGED')
        for table in ('release_target_runs','release_target_submissions','release_target_readbacks','release_target_failure_events'):
            current=[dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE run_id=?',(image['run']['run_id'],))];old=image[table]
            if before_append:
                if sorted(adoption._bytes(r) for r in current)!=sorted(adoption._bytes(r) for r in old):_fail('STOCK_CATEGORY_SOURCE_HISTORY_CAS_CHANGED')
            elif table=='release_target_runs':
                by_label={r['target_label']:r for r in current}
                if any(r['target_label'] not in by_label or by_label[r['target_label']]['attempts']<r['attempts'] or any(by_label[r['target_label']][k]!=r[k] for k in ('run_id','idempotency_key','created_at')) for r in old):_fail('STOCK_CATEGORY_SOURCE_ORIGINAL_TARGET_CHANGED')
            elif table=='release_target_submissions':
                fields=('run_id','target_label','external_id','evidence_json','evidence_digest','submitted_at')
                if any(not any(all(r[k]==v[k] for k in fields) for r in current) for v in old):_fail('STOCK_CATEGORY_SOURCE_ORIGINAL_SUBMISSION_CHANGED')
            elif not {adoption._bytes(r) for r in old}<={adoption._bytes(r) for r in current}:_fail('STOCK_CATEGORY_SOURCE_PRIOR_EVIDENCE_CHANGED')
        for table in ('native_final_budget_scopes','native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes'):
            query=('SELECT * FROM '+table+' WHERE plan_id=?' if table.endswith('_scopes') else 'SELECT b.* FROM '+table+' b JOIN native_final_budget_scopes s USING(scope_key) WHERE s.plan_id=?')
            current={adoption._bytes(dict(r)) for r in db.execute(query,(frozen['plan_id'],))};old={adoption._bytes(r) for r in image[table]}
            if not old<=current or (before_append and current!=old):_fail('STOCK_CATEGORY_SOURCE_PRIOR_BUDGET_CHANGED')
        for table in STOCK_CATEGORY_MEDIA_TABLES:
            query=('SELECT * FROM '+table+' WHERE run_id=?' if table.endswith('_grants') else 'SELECT b.* FROM '+table+' b JOIN native_ozon_media_grants g USING(grant_id) WHERE g.run_id=?')
            current={adoption._bytes(dict(r)) for r in db.execute(query,(image['run']['run_id'],))};old={adoption._bytes(r) for r in image[table]}
            if not old<=current or (before_append and current!=old):_fail('STOCK_CATEGORY_SOURCE_PRIOR_MEDIA_CHANGED')
    checkpoint=image['tiktok_checkpoint'];ref=image['tiktok_checkpoint_ref']
    # Verify the existing checkpoint byte format without importing live
    # dependency factories (their module import loads private configuration).
    def verified_checkpoint(value):
        body={k:v for k,v in value.items() if k!='checkpoint_digest'}
        expected=dict(schema_version='tiktok-v4-draft-checkpoint/v1',run_id=mapping['file_run_id'],
            **{k:snapshot[k] for k in ('offer_id','product_revision','snapshot_digest')})
        if (value.get('checkpoint_digest')!='sha256:'+sha256(adoption._bytes(body)).hexdigest()
            or any(value.get(k)!=v for k,v in expected.items()) or not isinstance(value.get('events'),list)):
            _fail('STOCK_CATEGORY_SOURCE_CHECKPOINT_IDENTITY_CHANGED')
        return value
    verified_checkpoint(checkpoint)
    events=checkpoint['events']
    from modules.miaoshou.tiktok_v4_drafts import EXPECTED_SHOP_ID_BY_TARGET
    expected_path=checkpoint_root/str(snapshot['offer_id'])/str(snapshot['product_revision'])/mapping['file_run_id']/'tiktok-draft-checkpoint.json'
    if (Path(ref['path']).resolve()!=expected_path.resolve() or checkpoint.get('external_write_count')!=8
        or len(events)!=8 or any(r['outcome']!='ACCEPTED' or r['shop_id']!=str(EXPECTED_SHOP_ID_BY_TARGET.get(r['target_label']))
            or not isinstance(r['detail_id'],str) or not r['detail_id'].isdigit() for r in events)
        or any({r['operation'] for r in events if r['target_label']==label}!={'CREATE_DRAFT','CLAIM_TO_SHOP'}
            or len([r for r in events if r['target_label']==label])!=2
            or len({(r['detail_id'],r['shop_id']) for r in events if r['target_label']==label})!=1 for label in NATIVE_RECOVERY_TARGETS)):
        _fail('STOCK_CATEGORY_SOURCE_ORIGINAL_CLAIM_PREFIX_REQUIRED')
    raw=Path(ref['path']).read_bytes();current=verified_checkpoint(json.loads(raw))
    if (current['events'][:len(events)]!=events or (before_append and (sha256(raw).hexdigest()!=ref['sha256'] or current!=checkpoint))):
        _fail('STOCK_CATEGORY_SOURCE_CHECKPOINT_PREFIX_CHANGED')
    return dict(original_run_id=image['run']['run_id'],history_digest=sha256(adoption._bytes(image)).hexdigest(),
        approved_stock_policy_digest=sha256(adoption._bytes(policy)).hexdigest(),original_checkpoint_digest=checkpoint['checkpoint_digest'])


def _verify_stock_category_source(task,profile,original,*,connection,owner_sid,key,events,rows,historical=False):
    if len(rows)!=1:_fail('STOCK_CATEGORY_SOURCE_SINGLE_EVENT_REQUIRED')
    record=_signed(json.loads(rows[0][0]),key=key,schema=STOCK_CATEGORY_SCHEMA,owner_sid=owner_sid)
    if record.get('purpose')!=STOCK_CATEGORY_PURPOSE:_fail('STOCK_CATEGORY_SOURCE_PURPOSE_REQUIRED')
    previous=connection.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task['task_id'],NATIVE_RECOVERY_EVENT)).fetchall()
    prior=_verify_native_recovery_source(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,events=events,rows=previous,historical=True)
    old=_signed(json.loads(previous[0][0]),key=key,schema=NATIVE_RECOVERY_SCHEMA,owner_sid=owner_sid)
    from shared_platform.release_store import default_release_store
    if (record.get('task_id')!=task['task_id'] or record.get('offer_id')!=original['offer_id']
        or record.get('original_adoption_digest')!=sha256(adoption._bytes(original)).hexdigest()
        or record.get('previous_native_recovery_digest')!=json.loads(previous[0][0])['record_digest']
        or record.get('scope_digest')!=old['scope_digest'] or record.get('frozen_candidate')!=old['frozen_candidate']
        or _r1_reader_pin(record['technical_request']).get('source_head')!=old['source_segments']['retry']['new_version']['code_version']
        or record.get('source_segments')!=(_historical_stock_category_segments(profile,old['source_segments']['retry']['new_version'],record['source_segments']) if historical else
            _stock_category_segments(profile,old['source_segments']['retry']['new_version'],record['source_segments']['mechanism']['new_version']))
        or record.get('history')!=_stock_category_history(default_release_store(),task['task_id'],old['frozen_candidate'],record['technical_request'],record['run_preimage'])
        or (not historical and task.get('version')!=_version(profile)) or record.get('external_write_count')!=0 or record.get('marketplace_approval') is not False):
        _fail('STOCK_CATEGORY_SOURCE_SIGNED_BINDING_CHANGED')
    return prior


def continue_stock_category_source(engine,profile,store,*,task_id,expected_adoption_digest,actor_reader,
    mechanism_head,mechanism_manifest,technical_request,run_preimage):
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    sid,key=adoption._owner_key(profile);actor=read_native_actor_identity(actor_reader)
    if actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER' or actor.get('owner_sid')!=sid:_fail('SOURCE_SUCCESSOR_TRUSTED_SAME_USER_REQUIRED')
    with engine.transaction() as db:
        before=dict(engine._row(db,task_id));task=engine._project(db,before)
        row=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(task_id,)).fetchone()
        original=_signed(row,key=key,schema=adoption.SCHEMA,owner_sid=sid)
        if row['record_digest']!=expected_adoption_digest:_fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
        if db.execute('SELECT 1 FROM workbench_events WHERE task_id=? AND event_type=?',(task_id,STOCK_CATEGORY_EVENT)).fetchone():_fail('STOCK_CATEGORY_SOURCE_SINGLE_EVENT_REQUIRED')
        events=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task_id,GALLERY_EVENT)).fetchall()
        previous=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task_id,NATIVE_RECOVERY_EVENT)).fetchall()
        _verify_native_recovery_source(task,profile,original,connection=db,owner_sid=sid,key=key,events=events,rows=previous,historical=True)
        old=_signed(json.loads(previous[0][0]),key=key,schema=NATIVE_RECOVERY_SCHEMA,owner_sid=sid)
        oldevents=[tuple(r) for r in db.execute('SELECT * FROM workbench_events WHERE task_id=? ORDER BY id',(task_id,))]
    version=old['source_segments']['retry']['new_version']
    if (json.loads(before['version_json'])!=version or before['state']!='reconciliation_required' or before['step_index']!=2
        or any(before[k] is not None for k in ('worker','lease_token','lease_until')) or before['external_started']):_fail('STOCK_CATEGORY_SOURCE_IDLE_REQUIRED')
    if _r1_reader_pin(technical_request).get('source_head')!=version['code_version']:_fail('STOCK_CATEGORY_SOURCE_ORIGINAL_SOURCE_CHANGED')
    segments=_stock_category_segments(profile,version,dict(code_version=mechanism_head,environment='stable',manifest_digest=mechanism_manifest))
    history=_stock_category_history(store,task_id,old['frozen_candidate'],technical_request,run_preimage,before_append=True)
    record=dict(schema_version=STOCK_CATEGORY_SCHEMA,purpose=STOCK_CATEGORY_PURPOSE,task_id=task_id,offer_id=original['offer_id'],owner_sid=sid,
        continued_at=datetime.now(timezone.utc).isoformat(),original_adoption_digest=expected_adoption_digest,previous_native_recovery_digest=json.loads(previous[0][0])['record_digest'],
        scope_digest=old['scope_digest'],source_segments=segments,frozen_candidate=old['frozen_candidate'],history=history,
        technical_request=technical_request,run_preimage=run_preimage,external_write_count=0,marketplace_approval=False)
    raw=adoption._bytes(record)
    with engine.transaction() as db:
        if (dict(engine._row(db,task_id))!=before or [tuple(r) for r in db.execute('SELECT * FROM workbench_events WHERE task_id=? ORDER BY id',(task_id,))]!=oldevents
            or db.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state='inflight'",(task_id,)).fetchone()
            or _stock_category_history(store,task_id,old['frozen_candidate'],technical_request,run_preimage,before_append=True)!=history):_fail('STOCK_CATEGORY_SOURCE_CAS_CHANGED')
        engine._event(db,task_id,STOCK_CATEGORY_EVENT,dict(record_json=raw.decode(),record_digest=sha256(raw).hexdigest(),signature=hmac.new(key,raw,'sha256').hexdigest()))
        db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',(adoption._bytes(_version(profile)).decode(),task_id))
    return record


LOCALE_DRAFT_EVENT='source_code_approved_native_tiktok_locale_draft_correction'
LOCALE_DRAFT_SCHEMA='agent-prepared-approved-native-tiktok-locale-draft-correction-source/v1'
LOCALE_DRAFT_PURPOSE='ORIGINAL_APPROVED_TIKTOK_LOCALIZED_DRAFT_CORRECTION_AND_RESERVED_PUBLISH_CONTINUATION'
LOCALE_DRAFT_MECHANISM_FILES=frozenset({'shared_platform/agent_prepared_source_successor.py',
    'scripts/continue_agent_prepared_source.py','tests/test_native_tiktok_locale_draft_source.py'})
LOCALE_DRAFT_FILES=frozenset({'modules/miaoshou/tiktok_v4_drafts.py',
    'shared_platform/native_tiktok_locale_draft_correction.py','shared_platform/agent_prepared_v4_targets.py',
    'modules/products/server.py','shared_platform/native_sole_final_service.py',
    'tests/test_b0431_tiktok_locale_draft_correction.py'})


def _historical_stock_category_segments(profile,old_version,segments):
    mechanism=segments['mechanism'];correction=segments['correction']
    if (mechanism['old_version']!=old_version or correction['old_version']!=mechanism['new_version']
        or not STOCK_CATEGORY_MECHANISM_FILES-{'tests/test_native_stock_category_source.py'}<=set(mechanism['changed_files'])
        or not {name for name in STOCK_CATEGORY_FILES if not name.startswith('tests/')}<=set(correction['changed_files'])):
        _fail('LOCALE_DRAFT_SOURCE_ORIGINAL_SEGMENTS_REQUIRED')
    verify_historical_source(profile.root,mechanism,allowed_files=STOCK_CATEGORY_MECHANISM_FILES)
    verify_historical_source(profile.root,correction,allowed_files=STOCK_CATEGORY_FILES)
    return segments


def _locale_draft_segments(profile,old_version,mechanism_version):
    names=subprocess.check_output(['git','-C',str(profile.root),'diff','--name-only',old_version['code_version'],mechanism_version['code_version']],text=True).splitlines()
    if (not LOCALE_DRAFT_MECHANISM_FILES-{'tests/test_native_tiktok_locale_draft_source.py'}<=set(names)
        or not set(names)<=LOCALE_DRAFT_MECHANISM_FILES):_fail('LOCALE_DRAFT_SOURCE_EXACT_MECHANISM_REQUIRED')
    mechanism=dict(old_version=old_version,new_version=mechanism_version,
        changed_files={name:sha256((Path(profile.root)/name).read_bytes()).hexdigest() for name in names})
    verify_historical_source(profile.root,mechanism,allowed_files=LOCALE_DRAFT_MECHANISM_FILES)
    correction=source_evidence(profile,mechanism_version,allowed_files=LOCALE_DRAFT_FILES,required_changed_file='shared_platform/native_tiktok_locale_draft_correction.py')
    if not {name for name in LOCALE_DRAFT_FILES if not name.startswith('tests/')}<=set(correction['changed_files']):
        _fail('LOCALE_DRAFT_SOURCE_EXACT_FILES_REQUIRED')
    return dict(mechanism=mechanism,correction=correction)


def _locale_draft_proof(request,ref):
    from shared_platform.native_tiktok_locale_draft_correction import read_correction_proof
    if _r1_reader_pin(request).get('tiktok_draft_correction_ref')!=ref:_fail('LOCALE_DRAFT_SOURCE_EXACT_PROOF_REQUIRED')
    return read_correction_proof(ref)


def _locale_draft_history(store,task_id,frozen,request,run_preimage,proof_ref,*,before_append=False):
    """Only the four proven never-dispatched reservations are an idle exception."""
    intent=_r1_reader_pin(request);image=_r1_reader_pin(run_preimage);proof=_locale_draft_proof(request,proof_ref)
    decision='native-final-decision:'+frozen['native_review_digest'].removeprefix('sha256:')
    if (intent.get('schema')!='original-approved-tiktok-locale-draft-correction-source-request/v1'
        or intent.get('task_id')!=task_id or intent.get('offer_id')!=frozen['plan_id'].split(':')[1]
        or any(intent.get(k)!=frozen[v] for k,v in (('plan_id','plan_id'),('candidate_digest','candidate_digest'),('payload_digest','payload_digest'),('review_digest','native_review_digest')))
        or intent.get('decision_id')!=decision or intent.get('source_head')!=image.get('source_head')
        or intent.get('original_run_id')!=image['run']['run_id'] or image['run']['plan_id']!=frozen['plan_id']
        or image.get('all_targets_idle') is not True or intent.get('append_requires_all_mutations_idle') is not True
        or intent.get('limits')!=dict(source_event_max=1,extra_save_maximum=4,extra_save_per_target=1,original_target_cap=3)
        or any(intent.get(k) is not False for k in ('new_marketplace_approval','recreate_or_reclaim','reset_original_budget'))
        or intent.get('preserve_all_original_content_effects_and_history') is not True
        or any(proof.get(k)!=v for k,v in dict(task_id=task_id,offer_id=intent['offer_id'],plan_id=frozen['plan_id'],
            run_id=image['run']['run_id'],decision_id=decision,candidate_digest=frozen['candidate_digest'],
            snapshot_digest=intent.get('snapshot_digest'),base_source_head=image['source_head']).items())):
        _fail('LOCALE_DRAFT_SOURCE_EXACT_HISTORY_PINS_REQUIRED')
    retained=_r1_reader_pin(proof['retained_ref']);checkpoint=_r1_reader_pin(proof['checkpoint_ref'])
    targets={r['target_label']:r for r in image['release_target_runs']}
    failures=[r for r in image['release_target_failure_events'] if r['attempt']==3 and r['target_label'] in NATIVE_RECOVERY_TARGETS]
    if (set(targets)!=set(frozen['ordered_targets']) or any(r['status'] not in {'PENDING','FAILED','SUCCEEDED','VERIFIED','SUBMITTED'} for r in targets.values())
        or any(targets[k]['status']!='FAILED' or targets[k]['attempts']!=3 for k in NATIVE_RECOVERY_TARGETS)
        or sorted(adoption._bytes(r) for r in failures)!=sorted(adoption._bytes(r) for r in retained['original_failure3_rows'])
        or any(r['target_label'] in NATIVE_RECOVERY_TARGETS for r in image['release_target_submissions'])):
        _fail('LOCALE_DRAFT_SOURCE_ORIGINAL_FAILED3_REQUIRED')
    scopes={r['scope_key']:r for r in image['native_final_budget_scopes']}
    if any(r['platform']=='TIKTOK_DRAFT_CORRECTION' for r in scopes.values()):_fail('LOCALE_DRAFT_SOURCE_NEW_LEDGER_MUST_NOT_PREEXIST')
    reserved={(r['scope_key'],r['sequence']):r for r in image['native_final_budget_reservations']}
    dispatched={(r['scope_key'],r['sequence']):r for r in image['native_final_budget_dispatches']}
    outcomes={(r['scope_key'],r['sequence']):r for r in image['native_final_budget_outcomes']}
    slots={(r['scope_key'],r['sequence']):r for r in proof['not_sent_slots']}
    if (len(slots)!=4 or {r['target_label'] for r in slots.values()}!=NATIVE_RECOVERY_TARGETS
        or set(reserved)-set(dispatched)!=set(slots) or set(dispatched)!=set(outcomes)
        or any(r['outcome'] not in {'ACCEPTED','REJECTED'} for r in outcomes.values())):
        _fail('LOCALE_DRAFT_SOURCE_OTHER_UNKNOWN_INFLIGHT')
    for key,slot in slots.items():
        row=reserved.get(key,{});saved=(slot['scope_key'],slot['saved_sequence'])
        if (row.get('target_label')!=slot['target_label'] or row.get('operation')!='publish_target'
            or row.get('budget_scope')!='target' or row.get('attempt_count')!=1 or row.get('initial_outcome')!='UNKNOWN'
            or scopes.get(slot['scope_key'],{}).get('platform')!='TIKTOK'
            or reserved.get(saved,{}).get('target_label')!=slot['target_label'] or reserved.get(saved,{}).get('operation')!='save_draft'
            or outcomes.get(saved,{}).get('outcome')!='ACCEPTED'):
            _fail('LOCALE_DRAFT_SOURCE_EXACT_NOT_SENT_REQUIRED')
    tk=[]
    for key,row in reserved.items():
        if scopes[row['scope_key']]['platform']=='TIKTOK':
            tk.append({**row,'request_digest':dispatched.get(key,{}).get('request_digest'),
                'outcome':outcomes.get(key,{}).get('outcome')})
    if sorted(adoption._bytes(r) for r in tk)!=sorted(adoption._bytes(r) for r in retained['native_TikTok_budget_rows']):
        _fail('LOCALE_DRAFT_SOURCE_ORIGINAL_SIXTEEN_REQUIRED')
    media=[{r['grant_id'] for r in image[table]} for table in STOCK_CATEGORY_MEDIA_TABLES[:4]]
    if not all(value==media[0] for value in media):_fail('LOCALE_DRAFT_SOURCE_OTHER_UNKNOWN_INFLIGHT')
    if (image['tiktok_checkpoint']!=checkpoint or image['tiktok_checkpoint_ref']!=retained['original_CP_ref']):
        _fail('LOCALE_DRAFT_SOURCE_ORIGINAL_CHECKPOINT_REQUIRED')
    raw=Path(image['tiktok_checkpoint_ref']['path']).read_bytes();current=json.loads(raw)
    body={k:v for k,v in current.items() if k!='checkpoint_digest'}
    if (current.get('checkpoint_digest')!='sha256:'+sha256(adoption._bytes(body)).hexdigest()
        or any(current.get(k)!=checkpoint[k] for k in ('schema_version','run_id','offer_id','product_revision','snapshot_digest'))
        or current.get('events',[])[:len(checkpoint['events'])]!=checkpoint['events']
        or (before_append and (sha256(raw).hexdigest()!=image['tiktok_checkpoint_ref']['sha256'] or current!=checkpoint))):
        _fail('LOCALE_DRAFT_SOURCE_CHECKPOINT_PREFIX_CHANGED')
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        snap=db.execute('SELECT snapshot_json,snapshot_digest FROM approved_publication_snapshots WHERE plan_id=?',(frozen['plan_id'],)).fetchone()
        if (snap is None or snap['snapshot_digest']!=proof['snapshot_digest']
            or sha256(snap['snapshot_json'].encode()).hexdigest()!=proof['snapshot_ref']['sha256']):
            _fail('LOCALE_DRAFT_SOURCE_ORIGINAL_SNAPSHOT_CHANGED')
        row=db.execute('SELECT * FROM release_runs WHERE run_id=?',(image['run']['run_id'],)).fetchone()
        fixed=('run_id','plan_id','approval_id','created_at','technical_admission_json','technical_admission_digest','technical_execution_state')
        if row is None or any(row[k]!=image['run'][k] for k in fixed) or (before_append and dict(row)!=image['run']):_fail('LOCALE_DRAFT_SOURCE_ORIGINAL_RUN_CHANGED')
        for table in ('release_target_runs','release_target_submissions','release_target_readbacks','release_target_failure_events'):
            rows=[dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE run_id=?',(image['run']['run_id'],))];old=image[table]
            if before_append:
                if sorted(adoption._bytes(r) for r in rows)!=sorted(adoption._bytes(r) for r in old):_fail('LOCALE_DRAFT_SOURCE_HISTORY_CAS_CHANGED')
            elif table=='release_target_runs':
                by_label={r['target_label']:r for r in rows}
                if set(by_label)!=set(targets) or any(by_label[r['target_label']]['attempts']<r['attempts'] or any(by_label[r['target_label']][k]!=r[k] for k in ('run_id','idempotency_key','created_at')) for r in old):_fail('LOCALE_DRAFT_SOURCE_ORIGINAL_TARGET_CHANGED')
            elif table=='release_target_submissions':
                fields=('run_id','target_label','external_id','evidence_json','evidence_digest','submitted_at')
                if any(not any(all(r[k]==v[k] for k in fields) for r in rows) for v in old):_fail('LOCALE_DRAFT_SOURCE_ORIGINAL_SUBMISSION_CHANGED')
            elif not {adoption._bytes(r) for r in old}<={adoption._bytes(r) for r in rows}:_fail('LOCALE_DRAFT_SOURCE_PRIOR_EVIDENCE_CHANGED')
        for table in ('native_final_budget_scopes','native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes'):
            query=('SELECT * FROM '+table+' WHERE plan_id=?' if table.endswith('_scopes') else 'SELECT b.* FROM '+table+' b JOIN native_final_budget_scopes s USING(scope_key) WHERE s.plan_id=?')
            rows={adoption._bytes(dict(r)) for r in db.execute(query,(frozen['plan_id'],))};old={adoption._bytes(r) for r in image[table]}
            if not old<=rows or (before_append and rows!=old):_fail('LOCALE_DRAFT_SOURCE_PRIOR_BUDGET_CHANGED')
        for table in STOCK_CATEGORY_MEDIA_TABLES:
            query=('SELECT * FROM '+table+' WHERE run_id=?' if table.endswith('_grants') else 'SELECT b.* FROM '+table+' b JOIN native_ozon_media_grants g USING(grant_id) WHERE g.run_id=?')
            rows={adoption._bytes(dict(r)) for r in db.execute(query,(image['run']['run_id'],))};old={adoption._bytes(r) for r in image[table]}
            if not old<=rows or (before_append and rows!=old):_fail('LOCALE_DRAFT_SOURCE_PRIOR_MEDIA_CHANGED')
    return dict(original_run_id=image['run']['run_id'],history_digest=sha256(adoption._bytes(image)).hexdigest(),
        tiktok_draft_correction_digest=sha256(adoption._bytes(proof)).hexdigest(),original_checkpoint_digest=checkpoint['checkpoint_digest'])


def _verify_locale_draft_source(task,profile,original,*,connection,owner_sid,key,events,rows,historical=False):
    if len(rows)!=1:_fail('LOCALE_DRAFT_SOURCE_SINGLE_EVENT_REQUIRED')
    record=_signed(json.loads(rows[0][0]),key=key,schema=LOCALE_DRAFT_SCHEMA,owner_sid=owner_sid)
    if record.get('purpose')!=LOCALE_DRAFT_PURPOSE:_fail('LOCALE_DRAFT_SOURCE_PURPOSE_REQUIRED')
    previous=connection.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task['task_id'],STOCK_CATEGORY_EVENT)).fetchall()
    prior=_verify_stock_category_source(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,events=events,rows=previous,historical=True)
    old=_signed(json.loads(previous[0][0]),key=key,schema=STOCK_CATEGORY_SCHEMA,owner_sid=owner_sid)
    from shared_platform.release_store import default_release_store
    if (record.get('task_id')!=task['task_id'] or record.get('offer_id')!=original['offer_id']
        or record.get('original_adoption_digest')!=sha256(adoption._bytes(original)).hexdigest()
        or record.get('previous_stock_category_digest')!=json.loads(previous[0][0])['record_digest']
        or record.get('scope_digest')!=old['scope_digest'] or record.get('frozen_candidate')!=old['frozen_candidate']
        or _r1_reader_pin(record['technical_request']).get('source_head')!=old['source_segments']['correction']['new_version']['code_version']
        or record.get('source_segments')!=(_historical_locale_draft_segments(profile,old['source_segments']['correction']['new_version'],record['source_segments']) if historical else
            _locale_draft_segments(profile,old['source_segments']['correction']['new_version'],record['source_segments']['mechanism']['new_version']))
        or record.get('history')!=_locale_draft_history(default_release_store(),task['task_id'],old['frozen_candidate'],record['technical_request'],record['run_preimage'],record['tiktok_draft_correction_ref'])
        or record.get('tiktok_draft_correction_digest')!=record['history']['tiktok_draft_correction_digest']
        or (not historical and task.get('version')!=_version(profile)) or record.get('external_write_count')!=0 or record.get('marketplace_approval') is not False):
        _fail('LOCALE_DRAFT_SOURCE_SIGNED_BINDING_CHANGED')
    return prior


def continue_locale_draft_source(engine,profile,store,*,task_id,expected_adoption_digest,actor_reader,
    mechanism_head,mechanism_manifest,technical_request,run_preimage,tiktok_draft_correction_ref):
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    sid,key=adoption._owner_key(profile);actor=read_native_actor_identity(actor_reader)
    if actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER' or actor.get('owner_sid')!=sid:_fail('SOURCE_SUCCESSOR_TRUSTED_SAME_USER_REQUIRED')
    with engine.transaction() as db:
        before=dict(engine._row(db,task_id));task=engine._project(db,before)
        row=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(task_id,)).fetchone()
        original=_signed(row,key=key,schema=adoption.SCHEMA,owner_sid=sid)
        if row['record_digest']!=expected_adoption_digest:_fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
        if db.execute('SELECT 1 FROM workbench_events WHERE task_id=? AND event_type=?',(task_id,LOCALE_DRAFT_EVENT)).fetchone():_fail('LOCALE_DRAFT_SOURCE_SINGLE_EVENT_REQUIRED')
        events=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task_id,GALLERY_EVENT)).fetchall()
        previous=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task_id,STOCK_CATEGORY_EVENT)).fetchall()
        _verify_stock_category_source(task,profile,original,connection=db,owner_sid=sid,key=key,events=events,rows=previous,historical=True)
        old=_signed(json.loads(previous[0][0]),key=key,schema=STOCK_CATEGORY_SCHEMA,owner_sid=sid)
        oldevents=[tuple(r) for r in db.execute('SELECT * FROM workbench_events WHERE task_id=? ORDER BY id',(task_id,))]
    version=old['source_segments']['correction']['new_version']
    if (json.loads(before['version_json'])!=version or before['state']!='reconciliation_required' or before['step_index']!=2
        or any(before[k] is not None for k in ('worker','lease_token','lease_until')) or before['external_started']):_fail('LOCALE_DRAFT_SOURCE_IDLE_REQUIRED')
    if _r1_reader_pin(technical_request).get('source_head')!=version['code_version']:_fail('LOCALE_DRAFT_SOURCE_ORIGINAL_SOURCE_CHANGED')
    segments=_locale_draft_segments(profile,version,dict(code_version=mechanism_head,environment='stable',manifest_digest=mechanism_manifest))
    history=_locale_draft_history(store,task_id,old['frozen_candidate'],technical_request,run_preimage,tiktok_draft_correction_ref,before_append=True)
    record=dict(schema_version=LOCALE_DRAFT_SCHEMA,purpose=LOCALE_DRAFT_PURPOSE,task_id=task_id,offer_id=original['offer_id'],owner_sid=sid,
        continued_at=datetime.now(timezone.utc).isoformat(),original_adoption_digest=expected_adoption_digest,previous_stock_category_digest=json.loads(previous[0][0])['record_digest'],
        scope_digest=old['scope_digest'],source_segments=segments,frozen_candidate=old['frozen_candidate'],history=history,
        technical_request=technical_request,run_preimage=run_preimage,tiktok_draft_correction_ref=tiktok_draft_correction_ref,
        tiktok_draft_correction_digest=history['tiktok_draft_correction_digest'],external_write_count=0,marketplace_approval=False)
    raw=adoption._bytes(record)
    with engine.transaction() as db:
        if (dict(engine._row(db,task_id))!=before or [tuple(r) for r in db.execute('SELECT * FROM workbench_events WHERE task_id=? ORDER BY id',(task_id,))]!=oldevents
            or db.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state='inflight'",(task_id,)).fetchone()
            or _locale_draft_history(store,task_id,old['frozen_candidate'],technical_request,run_preimage,tiktok_draft_correction_ref,before_append=True)!=history):_fail('LOCALE_DRAFT_SOURCE_CAS_CHANGED')
        engine._event(db,task_id,LOCALE_DRAFT_EVENT,dict(record_json=raw.decode(),record_digest=sha256(raw).hexdigest(),signature=hmac.new(key,raw,'sha256').hexdigest()))
        db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',(adoption._bytes(_version(profile)).decode(),task_id))
    return record


TIKTOK_ACTIVATION_EVENT='source_code_approved_native_tiktok_activation_connection_compatibility'
TIKTOK_ACTIVATION_SCHEMA='agent-prepared-approved-native-tiktok-activation-connection-source/v1'
TIKTOK_ACTIVATION_PURPOSE='ORIGINAL_APPROVED_TIKTOK_DRAFT_CORRECTION_ACTIVATION_CONNECTION_FIX'
TIKTOK_ACTIVATION_MECHANISM_FILES=frozenset({'shared_platform/agent_prepared_source_successor.py',
    'scripts/continue_agent_prepared_source.py','tests/test_native_tiktok_activation_source.py'})
TIKTOK_ACTIVATION_FILES=frozenset({'shared_platform/native_tiktok_locale_draft_correction.py',
    'tests/test_b0431_tiktok_locale_draft_correction.py'})


def _historical_locale_draft_segments(profile,old_version,segments):
    mechanism=segments['mechanism'];correction=segments['correction']
    if (mechanism['old_version']!=old_version or correction['old_version']!=mechanism['new_version']
        or not LOCALE_DRAFT_MECHANISM_FILES-{'tests/test_native_tiktok_locale_draft_source.py'}<=set(mechanism['changed_files'])
        or not {name for name in LOCALE_DRAFT_FILES if not name.startswith('tests/')}<=set(correction['changed_files'])):
        _fail('TIKTOK_ACTIVATION_SOURCE_ORIGINAL_SEGMENTS_REQUIRED')
    verify_historical_source(profile.root,mechanism,allowed_files=LOCALE_DRAFT_MECHANISM_FILES)
    verify_historical_source(profile.root,correction,allowed_files=LOCALE_DRAFT_FILES)
    return segments


def _tiktok_activation_segments(profile,old_version,mechanism_version):
    names=subprocess.check_output(['git','-C',str(profile.root),'diff','--name-only',old_version['code_version'],mechanism_version['code_version']],text=True).splitlines()
    if (not TIKTOK_ACTIVATION_MECHANISM_FILES-{'tests/test_native_tiktok_activation_source.py'}<=set(names)
        or not set(names)<=TIKTOK_ACTIVATION_MECHANISM_FILES):_fail('TIKTOK_ACTIVATION_SOURCE_EXACT_MECHANISM_REQUIRED')
    mechanism=dict(old_version=old_version,new_version=mechanism_version,
        changed_files={name:sha256((Path(profile.root)/name).read_bytes()).hexdigest() for name in names})
    verify_historical_source(profile.root,mechanism,allowed_files=TIKTOK_ACTIVATION_MECHANISM_FILES)
    correction=source_evidence(profile,mechanism_version,allowed_files=TIKTOK_ACTIVATION_FILES,
        required_changed_file='shared_platform/native_tiktok_locale_draft_correction.py')
    return dict(mechanism=mechanism,correction=correction)


def _tiktok_activation_history(store,task_id,frozen,request,run_preimage,old,*,before_append=False):
    """A local connection repair inherits the existing correction authority."""
    intent=_r1_reader_pin(request);image=_r1_reader_pin(run_preimage)
    proof=_locale_draft_proof(old['technical_request'],old['tiktok_draft_correction_ref'])
    if (intent.get('schema')!='original-approved-tiktok-activation-connection-fix-source-request/v1'
        or intent.get('task_id')!=task_id or intent.get('offer_id')!=proof['offer_id']
        or any(intent.get(k)!=frozen[v] for k,v in (('plan_id','plan_id'),('candidate_digest','candidate_digest'),('payload_digest','payload_digest'),('review_digest','native_review_digest')))
        or intent.get('decision_id')!=proof['decision_id'] or intent.get('original_run_id')!=image['run']['run_id']
        or image['run']['run_id']!=proof['run_id'] or image['run']['plan_id']!=frozen['plan_id']
        or intent.get('source_head')!=image.get('source_head') or intent['source_head']!=old['source_segments']['correction']['new_version']['code_version']
        or intent.get('original_correction_proof_ref')!=old['tiktok_draft_correction_ref']
        or intent.get('limits')!={'source_event_max':1} or intent.get('append_requires_all_mutations_idle') is not True
        or image.get('all_targets_idle') is not True or intent.get('preserve_all_original_content_effects_and_history') is not True
        or any(intent.get(k) is not False for k in ('new_marketplace_approval','new_business_budget','new_draft_correction_authority'))):
        _fail('TIKTOK_ACTIVATION_SOURCE_EXACT_HISTORY_PINS_REQUIRED')
    _r1_reader_pin(intent['diagnostic_ref'])
    targets={r['target_label']:r for r in image['release_target_runs']}
    if (set(targets)!=set(frozen['ordered_targets'])
        or any(r['status'] not in {'PENDING','FAILED','SUCCEEDED','VERIFIED','SUBMITTED'} for r in targets.values())
        or any(targets[k]['status'] not in {'FAILED','PENDING'} or targets[k]['attempts']!=3 for k in NATIVE_RECOVERY_TARGETS)):
        _fail('TIKTOK_ACTIVATION_SOURCE_ORIGINAL_TARGETS_REQUIRED')
    scopes={r['scope_key']:r for r in image['native_final_budget_scopes']}
    reserved={(r['scope_key'],r['sequence']):r for r in image['native_final_budget_reservations']}
    dispatched={(r['scope_key'],r['sequence']):r for r in image['native_final_budget_dispatches']}
    outcomes={(r['scope_key'],r['sequence']):r for r in image['native_final_budget_outcomes']}
    slots={(r['scope_key'],r['sequence']) for r in proof['not_sent_slots']}
    if (set(reserved)-set(dispatched)!=slots or set(dispatched)!=set(outcomes)
        or any(r['outcome'] not in {'ACCEPTED','REJECTED'} for r in outcomes.values())
        or any(r['platform']=='TIKTOK_DRAFT_CORRECTION' for r in scopes.values())):
        _fail('TIKTOK_ACTIVATION_SOURCE_OTHER_UNKNOWN_OR_NEW_EFFECT')
    retained=_r1_reader_pin(proof['retained_ref']);checkpoint=_r1_reader_pin(proof['checkpoint_ref'])
    tk=[{**r,'request_digest':dispatched.get(k,{}).get('request_digest'),'outcome':outcomes.get(k,{}).get('outcome')}
        for k,r in reserved.items() if scopes[r['scope_key']]['platform']=='TIKTOK']
    if (sorted(adoption._bytes(r) for r in tk)!=sorted(adoption._bytes(r) for r in retained['native_TikTok_budget_rows'])
        or image['tiktok_checkpoint']!=checkpoint or image['tiktok_checkpoint_ref']!=retained['original_CP_ref']):
        _fail('TIKTOK_ACTIVATION_SOURCE_ORIGINAL_EFFECTS_CHANGED')
    media=[{r['grant_id'] for r in image[table]} for table in STOCK_CATEGORY_MEDIA_TABLES[:4]]
    if not all(value==media[0] for value in media):_fail('TIKTOK_ACTIVATION_SOURCE_OTHER_UNKNOWN_OR_NEW_EFFECT')
    if before_append and sha256(Path(image['tiktok_checkpoint_ref']['path']).read_bytes()).hexdigest()!=image['tiktok_checkpoint_ref']['sha256']:
        _fail('TIKTOK_ACTIVATION_SOURCE_CHECKPOINT_CAS_CHANGED')
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        row=db.execute('SELECT * FROM release_runs WHERE run_id=?',(image['run']['run_id'],)).fetchone()
        fixed=('run_id','plan_id','approval_id','created_at','technical_admission_json','technical_admission_digest','technical_execution_state')
        if row is None or any(row[k]!=image['run'][k] for k in fixed) or (before_append and dict(row)!=image['run']):_fail('TIKTOK_ACTIVATION_SOURCE_ORIGINAL_RUN_CHANGED')
        for table in ('release_target_runs','release_target_submissions','release_target_readbacks','release_target_failure_events'):
            current=[dict(r) for r in db.execute('SELECT * FROM '+table+' WHERE run_id=?',(image['run']['run_id'],))];rows=image[table]
            if before_append:
                if sorted(adoption._bytes(r) for r in current)!=sorted(adoption._bytes(r) for r in rows):_fail('TIKTOK_ACTIVATION_SOURCE_HISTORY_CAS_CHANGED')
            elif table=='release_target_runs':
                current={r['target_label']:r for r in current}
                if set(current)!=set(targets) or any(current[r['target_label']]['attempts']<r['attempts'] or any(current[r['target_label']][k]!=r[k] for k in ('run_id','idempotency_key','created_at')) for r in rows):_fail('TIKTOK_ACTIVATION_SOURCE_ORIGINAL_TARGET_CHANGED')
            elif table=='release_target_submissions':
                fields=('run_id','target_label','external_id','evidence_json','evidence_digest','submitted_at')
                if any(not any(all(r[k]==v[k] for k in fields) for r in current) for v in rows):_fail('TIKTOK_ACTIVATION_SOURCE_ORIGINAL_SUBMISSION_CHANGED')
            elif not {adoption._bytes(r) for r in rows}<={adoption._bytes(r) for r in current}:_fail('TIKTOK_ACTIVATION_SOURCE_PRIOR_EVIDENCE_CHANGED')
        for table in ('native_final_budget_scopes','native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes'):
            query=('SELECT * FROM '+table+' WHERE plan_id=?' if table.endswith('_scopes') else 'SELECT b.* FROM '+table+' b JOIN native_final_budget_scopes s USING(scope_key) WHERE s.plan_id=?')
            current={adoption._bytes(dict(r)) for r in db.execute(query,(frozen['plan_id'],))};rows={adoption._bytes(r) for r in image[table]}
            if not rows<=current or (before_append and rows!=current):_fail('TIKTOK_ACTIVATION_SOURCE_PRIOR_BUDGET_CHANGED')
        for table in STOCK_CATEGORY_MEDIA_TABLES:
            query=('SELECT * FROM '+table+' WHERE run_id=?' if table.endswith('_grants') else 'SELECT b.* FROM '+table+' b JOIN native_ozon_media_grants g USING(grant_id) WHERE g.run_id=?')
            current={adoption._bytes(dict(r)) for r in db.execute(query,(image['run']['run_id'],))};rows={adoption._bytes(r) for r in image[table]}
            if not rows<=current or (before_append and rows!=current):_fail('TIKTOK_ACTIVATION_SOURCE_PRIOR_MEDIA_CHANGED')
    return dict(original_run_id=image['run']['run_id'],history_digest=sha256(adoption._bytes(image)).hexdigest(),
        original_correction_digest=sha256(adoption._bytes(proof)).hexdigest())


def _verify_tiktok_activation_source(task,profile,original,*,connection,owner_sid,key,events,rows):
    if len(rows)!=1:_fail('TIKTOK_ACTIVATION_SOURCE_SINGLE_EVENT_REQUIRED')
    record=_signed(json.loads(rows[0][0]),key=key,schema=TIKTOK_ACTIVATION_SCHEMA,owner_sid=owner_sid)
    if record.get('purpose')!=TIKTOK_ACTIVATION_PURPOSE:_fail('TIKTOK_ACTIVATION_SOURCE_PURPOSE_REQUIRED')
    previous=connection.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task['task_id'],LOCALE_DRAFT_EVENT)).fetchall()
    prior=_verify_locale_draft_source(task,profile,original,connection=connection,owner_sid=owner_sid,key=key,events=events,rows=previous,historical=True)
    old=_signed(json.loads(previous[0][0]),key=key,schema=LOCALE_DRAFT_SCHEMA,owner_sid=owner_sid)
    from shared_platform.release_store import default_release_store
    if (record.get('task_id')!=task['task_id'] or record.get('offer_id')!=original['offer_id']
        or record.get('original_adoption_digest')!=sha256(adoption._bytes(original)).hexdigest()
        or record.get('previous_locale_source_digest')!=json.loads(previous[0][0])['record_digest']
        or record.get('scope_digest')!=old['scope_digest'] or record.get('frozen_candidate')!=old['frozen_candidate']
        or record.get('source_segments')!=_tiktok_activation_segments(profile,old['source_segments']['correction']['new_version'],record['source_segments']['mechanism']['new_version'])
        or record.get('history')!=_tiktok_activation_history(default_release_store(),task['task_id'],old['frozen_candidate'],record['technical_request'],record['run_preimage'],old)
        or task.get('version')!=_version(profile) or record.get('external_write_count')!=0 or record.get('marketplace_approval') is not False):
        _fail('TIKTOK_ACTIVATION_SOURCE_SIGNED_BINDING_CHANGED')
    return prior


def continue_tiktok_activation_source(engine,profile,store,*,task_id,expected_adoption_digest,actor_reader,
    mechanism_head,mechanism_manifest,technical_request,run_preimage):
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    sid,key=adoption._owner_key(profile);actor=read_native_actor_identity(actor_reader)
    if actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER' or actor.get('owner_sid')!=sid:_fail('SOURCE_SUCCESSOR_TRUSTED_SAME_USER_REQUIRED')
    with engine.transaction() as db:
        before=dict(engine._row(db,task_id));task=engine._project(db,before)
        row=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(task_id,)).fetchone()
        original=_signed(row,key=key,schema=adoption.SCHEMA,owner_sid=sid)
        if row['record_digest']!=expected_adoption_digest:_fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
        if db.execute('SELECT 1 FROM workbench_events WHERE task_id=? AND event_type=?',(task_id,TIKTOK_ACTIVATION_EVENT)).fetchone():_fail('TIKTOK_ACTIVATION_SOURCE_SINGLE_EVENT_REQUIRED')
        events=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task_id,GALLERY_EVENT)).fetchall()
        previous=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task_id,LOCALE_DRAFT_EVENT)).fetchall()
        _verify_locale_draft_source(task,profile,original,connection=db,owner_sid=sid,key=key,events=events,rows=previous,historical=True)
        old=_signed(json.loads(previous[0][0]),key=key,schema=LOCALE_DRAFT_SCHEMA,owner_sid=sid)
        oldevents=[tuple(r) for r in db.execute('SELECT * FROM workbench_events WHERE task_id=? ORDER BY id',(task_id,))]
    version=old['source_segments']['correction']['new_version']
    if (json.loads(before['version_json'])!=version or before['state']!='reconciliation_required' or before['step_index']!=2
        or any(before[k] is not None for k in ('worker','lease_token','lease_until')) or before['external_started']):_fail('TIKTOK_ACTIVATION_SOURCE_IDLE_REQUIRED')
    segments=_tiktok_activation_segments(profile,version,dict(code_version=mechanism_head,environment='stable',manifest_digest=mechanism_manifest))
    history=_tiktok_activation_history(store,task_id,old['frozen_candidate'],technical_request,run_preimage,old,before_append=True)
    record=dict(schema_version=TIKTOK_ACTIVATION_SCHEMA,purpose=TIKTOK_ACTIVATION_PURPOSE,task_id=task_id,offer_id=original['offer_id'],owner_sid=sid,
        continued_at=datetime.now(timezone.utc).isoformat(),original_adoption_digest=expected_adoption_digest,previous_locale_source_digest=json.loads(previous[0][0])['record_digest'],
        scope_digest=old['scope_digest'],source_segments=segments,frozen_candidate=old['frozen_candidate'],history=history,
        technical_request=technical_request,run_preimage=run_preimage,external_write_count=0,marketplace_approval=False)
    raw=adoption._bytes(record)
    with engine.transaction() as db:
        if (dict(engine._row(db,task_id))!=before or [tuple(r) for r in db.execute('SELECT * FROM workbench_events WHERE task_id=? ORDER BY id',(task_id,))]!=oldevents
            or db.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state='inflight'",(task_id,)).fetchone()
            or _tiktok_activation_history(store,task_id,old['frozen_candidate'],technical_request,run_preimage,old,before_append=True)!=history):_fail('TIKTOK_ACTIVATION_SOURCE_CAS_CHANGED')
        engine._event(db,task_id,TIKTOK_ACTIVATION_EVENT,dict(record_json=raw.decode(),record_digest=sha256(raw).hexdigest(),signature=hmac.new(key,raw,'sha256').hexdigest()))
        db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',(adoption._bytes(_version(profile)).decode(),task_id))
    return record


def continue_approved_platform_compatibility(engine,profile,store,*,task_id,expected_adoption_digest,actor_reader,
    mechanism_head,mechanism_manifest,technical_request,run_preimage,ozon_readback):
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    sid,key=adoption._owner_key(profile);actor=read_native_actor_identity(actor_reader)
    if actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER' or actor.get('owner_sid')!=sid:
        _fail('SOURCE_SUCCESSOR_TRUSTED_SAME_USER_REQUIRED')
    with engine.transaction() as db:
        before=dict(engine._row(db,task_id));task=engine._project(db,before)
        row=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(task_id,)).fetchone()
        original=_signed(row,key=key,schema=adoption.SCHEMA,owner_sid=sid)
        if row['record_digest']!=expected_adoption_digest:_fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
        if db.execute('SELECT 1 FROM workbench_events WHERE task_id=? AND event_type=?',(task_id,PLATFORM_EVENT)).fetchone():_fail('PLATFORM_SOURCE_SINGLE_EVENT_REQUIRED')
        events=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task_id,GALLERY_EVENT)).fetchall()
        readers=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task_id,R1_READER_EVENT)).fetchall()
        _verify_r1_reader(task,profile,original,connection=db,owner_sid=sid,key=key,events=events,rows=readers,historical=True)
        old=_signed(json.loads(readers[0][0]),key=key,schema=R1_READER_SCHEMA,owner_sid=sid)
        oldevents=[tuple(r) for r in db.execute('SELECT * FROM workbench_events WHERE task_id=? ORDER BY id',(task_id,))]
    if (json.loads(before['version_json'])!=old['source_segments']['reader']['new_version'] or before['state']!='reconciliation_required' or before['step_index']!=2
        or before['worker'] is not None or before['lease_token'] is not None or before['lease_until'] is not None or before['external_started']):
        _fail('PLATFORM_SOURCE_IDLE_ORIGINAL_RUN_REQUIRED')
    segments=_platform_segments(profile,old['source_segments']['reader']['new_version'],
        dict(code_version=mechanism_head,environment='stable',manifest_digest=mechanism_manifest))
    if _r1_reader_pin(technical_request).get('source_head')!=old['source_segments']['reader']['new_version']['code_version']:
        _fail('PLATFORM_SOURCE_ORIGINAL_SOURCE_CHANGED')
    history=_platform_history(store,task_id,old['frozen_candidate'],technical_request,run_preimage,ozon_readback,before_append=True)
    record=dict(schema_version=PLATFORM_SCHEMA,task_id=task_id,offer_id=original['offer_id'],owner_sid=sid,
        continued_at=datetime.now(timezone.utc).isoformat(),original_adoption_digest=expected_adoption_digest,
        previous_r1_reader_digest=json.loads(readers[0][0])['record_digest'],scope_digest=old['scope_digest'],
        source_segments=segments,frozen_candidate=old['frozen_candidate'],history=history,
        technical_request=technical_request,run_preimage=run_preimage,ozon_readback=ozon_readback,external_write_count=0,marketplace_approval=False)
    raw=adoption._bytes(record)
    with engine.transaction() as db:
        if (dict(engine._row(db,task_id))!=before or [tuple(r) for r in db.execute('SELECT * FROM workbench_events WHERE task_id=? ORDER BY id',(task_id,))]!=oldevents
            or db.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state='inflight'",(task_id,)).fetchone()
            or _platform_history(store,task_id,old['frozen_candidate'],technical_request,run_preimage,ozon_readback,before_append=True)!=history):
            _fail('PLATFORM_SOURCE_CAS_CHANGED')
        engine._event(db,task_id,PLATFORM_EVENT,dict(record_json=raw.decode(),record_digest=sha256(raw).hexdigest(),signature=hmac.new(key,raw,'sha256').hexdigest()))
        db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',(adoption._bytes(_version(profile)).decode(),task_id))
    return record


def continue_approved_native_r1_reader(engine,profile,store,*,task_id,expected_adoption_digest,actor_reader,
    mechanism_head,mechanism_manifest,candidate_digest,work_order,preimage):
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    sid,key=adoption._owner_key(profile);actor=read_native_actor_identity(actor_reader)
    if actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER' or actor.get('owner_sid')!=sid:
        _fail('SOURCE_SUCCESSOR_TRUSTED_SAME_USER_REQUIRED')
    with engine.transaction() as db:
        before=dict(engine._row(db,task_id));task=engine._project(db,before)
        row=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(task_id,)).fetchone()
        original=_signed(row,key=key,schema=adoption.SCHEMA,owner_sid=sid)
        if row['record_digest']!=expected_adoption_digest:_fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
        if db.execute('SELECT 1 FROM workbench_events WHERE task_id=? AND event_type=?',(task_id,R1_READER_EVENT)).fetchone():_fail('R1_READER_SINGLE_EVENT_REQUIRED')
        events=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task_id,GALLERY_EVENT)).fetchall()
        prior=verify_completed_gallery_current(task,profile,original,connection=db,owner_sid=sid,key=key,events=events,historical=True)
        uirows=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=?',(task_id,UI_EVENT)).fetchall()
        if len(uirows)!=1 or len(events)!=2:_fail('R1_READER_ORIGINAL_UI_AND_TWO_GALLERY_REQUIRED')
        ui=json.loads(uirows[0][0]);old=_signed(ui,key=key,schema=UI_SCHEMA,owner_sid=sid)
        oldevents=[tuple(r) for r in db.execute('SELECT * FROM workbench_events WHERE task_id=? ORDER BY id',(task_id,))]
    if (json.loads(before['version_json'])!=old['source_segments']['frontend']['new_version'] or before['state']!='waiting_domain' or before['step_index']!=2
        or before['worker'] is not None or before['lease_token'] is not None or before['lease_until'] is not None or before['external_started']):
        _fail('R1_READER_IDLE_APPROVED_TASK_REQUIRED')
    segments=_r1_reader_segments(profile,old['source_segments']['frontend']['new_version'],
        dict(code_version=mechanism_head,environment='stable',manifest_digest=mechanism_manifest))
    frozen=_ui_frozen_candidate(store,original['offer_id'])
    if frozen!=old['frozen_candidate'] or frozen['candidate_digest']!=candidate_digest:_fail('R1_READER_EXACT_CANDIDATE_REQUIRED')
    approved=_r1_reader_approved(store,original['offer_id'],sid,frozen,work_order,preimage,task_id=task_id,before_append=True)
    record=dict(schema_version=R1_READER_SCHEMA,purpose=R1_READER_PURPOSE,task_id=task_id,offer_id=original['offer_id'],owner_sid=sid,
        continued_at=datetime.now(timezone.utc).isoformat(),original_adoption_digest=expected_adoption_digest,previous_ui_digest=ui['record_digest'],
        scope_digest=old['scope_digest'],api_selection_digest=old['api_selection_digest'],preserved=prior['preserved'],source_segments=segments,
        frozen_candidate=frozen,approved_rows=approved,work_order=work_order,preimage=preimage,new_edit_attempts=0,external_write_count=0,marketplace_approval=False)
    raw=adoption._bytes(record)
    with engine.transaction() as db:
        if (dict(engine._row(db,task_id))!=before
            or [tuple(r) for r in db.execute('SELECT * FROM workbench_events WHERE task_id=? ORDER BY id',(task_id,))]!=oldevents
            or db.execute("SELECT 1 FROM workbench_domain_operations WHERE owner_task_id=? AND state='inflight'",(task_id,)).fetchone()
            or _ui_frozen_candidate(store,original['offer_id'])!=frozen
            or _r1_reader_approved(store,original['offer_id'],sid,frozen,work_order,preimage,task_id=task_id,before_append=True)!=approved):
            _fail('R1_READER_CAS_CHANGED')
        engine._event(db,task_id,R1_READER_EVENT,dict(record_json=raw.decode(),record_digest=sha256(raw).hexdigest(),signature=hmac.new(key,raw,'sha256').hexdigest()))
        db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',(adoption._bytes(_version(profile)).decode(),task_id))
    return record


def continue_native_final_image_ui(engine,profile,store,*,task_id,expected_adoption_digest,actor_reader,mechanism_head,candidate_digest,user_request):
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    sid,key=adoption._owner_key(profile);actor=read_native_actor_identity(actor_reader)
    if actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER' or actor.get('owner_sid')!=sid:
        _fail('SOURCE_SUCCESSOR_TRUSTED_SAME_USER_REQUIRED')
    with engine.transaction() as db:
        before=dict(engine._row(db,task_id));task=engine._project(db,before)
        row=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(task_id,)).fetchone()
        original=_signed(row,key=key,schema=adoption.SCHEMA,owner_sid=sid)
        if row['record_digest']!=expected_adoption_digest:_fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
        events=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task_id,GALLERY_EVENT)).fetchall()
        if db.execute('SELECT 1 FROM workbench_events WHERE task_id=? AND event_type=?',(task_id,UI_EVENT)).fetchone():
            _fail('NATIVE_FINAL_UI_SINGLE_EVENT_REQUIRED')
        if len(events)!=2:_fail('NATIVE_FINAL_UI_TWO_GALLERY_EVENTS_REQUIRED')
        prior=verify_gallery_source(task,profile,original,connection=db,owner_sid=sid,key=key,events=events,historical=True)
    if (json.loads(before['version_json'])!=prior['source']['new_version'] or before['state']!='waiting_domain' or before['step_index']!=2
        or before['worker'] is not None or before['lease_token'] is not None or before['lease_until'] is not None or before['external_started']):
        _fail('NATIVE_FINAL_UI_IDLE_FROZEN_TASK_REQUIRED')
    mechanism_version={**_version(profile),'code_version':mechanism_head}
    segments=_ui_source_segments(profile,prior['source']['new_version'],mechanism_version)
    frozen=_ui_frozen_candidate(store,original['offer_id'],before_append=True)
    if frozen['candidate_digest']!=candidate_digest:_fail('NATIVE_FINAL_UI_EXACT_CANDIDATE_REQUIRED')
    _ui_request(user_request,task,original,frozen)
    record=dict(schema_version=UI_SCHEMA,purpose=UI_PURPOSE,task_id=task_id,offer_id=original['offer_id'],owner_sid=sid,
        continued_at=datetime.now(timezone.utc).isoformat(),original_adoption_digest=expected_adoption_digest,
        previous_gallery_digest=json.loads(events[-1]['detail_json'])['record_digest'],scope_digest=sha256(adoption._bytes(task['scope'])).hexdigest(),
        api_selection_digest=(prior.get('api_target_selection') or {}).get('reduction_digest'),preserved=prior['preserved'],
        source_segments=segments,frozen_candidate=frozen,user_request=user_request,new_edit_attempts=0,external_write_count=0,marketplace_approval=False)
    raw=adoption._bytes(record)
    with engine.transaction() as db:
        if (dict(engine._row(db,task_id))!=before
            or [r['detail_json'] for r in db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task_id,GALLERY_EVENT))]
                !=[r['detail_json'] for r in events]
            or db.execute('SELECT 1 FROM workbench_events WHERE task_id=? AND event_type=?',(task_id,UI_EVENT)).fetchone()
            or _ui_frozen_candidate(store,original['offer_id'],before_append=True)!=frozen):
            _fail('NATIVE_FINAL_UI_CAS_CHANGED')
        engine._event(db,task_id,UI_EVENT,dict(record_json=raw.decode(),record_digest=sha256(raw).hexdigest(),signature=hmac.new(key,raw,'sha256').hexdigest()))
        db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',(adoption._bytes(_version(profile)).decode(),task_id))
    return record


def continue_gallery_source(engine,profile,store,*,task_id,expected_adoption_digest,actor_reader,variant_image_projection=False,api_scope_pins=None):
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    sid,key=adoption._owner_key(profile);actor=read_native_actor_identity(actor_reader)
    if actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER' or actor.get('owner_sid')!=sid:
        _fail('SOURCE_SUCCESSOR_TRUSTED_SAME_USER_REQUIRED')
    task=engine.get(task_id)
    with engine.transaction() as db:
        row=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(task_id,)).fetchone()
        original=_signed(row,key=key,schema=adoption.SCHEMA,owner_sid=sid)
        if row['record_digest']!=expected_adoption_digest or (task.get('checkpoint') or {}).get('native_r1_adoption')!={
            'record_digest':expected_adoption_digest,'source_identity_binding_sha256':original['facts']['source_identity_binding_sha256']}:
            _fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
        events=db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task_id,GALLERY_EVENT)).fetchall()
        if events and (not variant_image_projection or len(events)==2):
            return verify_gallery_source(task,profile,original,connection=db,owner_sid=sid,key=key,events=events)
        if variant_image_projection and len(events)!=1:_fail('GALLERY_SOURCE_ORIGINAL_COMPATIBILITY_REQUIRED')
        prior=None
        if variant_image_projection:
            prior=verify_gallery_source(task,profile,original,connection=db,owner_sid=sid,key=key,events=events,historical=True)
        previous,digest=_sixth_historical(task,profile,original,connection=db,owner_sid=sid,key=key)
    preserved=_gallery_inputs(profile,store,original,previous,before_append=True)
    old_version=prior['source']['new_version'] if prior else previous['source']['new_version']
    if api_scope_pins is not None and not prior:_fail('API_SCOPE_ORIGINAL_GALLERY_EVENT_REQUIRED')
    evidence=source_evidence(profile,old_version,allowed_files=API_SCOPE_FILES if api_scope_pins is not None else VARIANT_FILES if prior else GALLERY_FILES,
        required_changed_file=VARIANT_FILE if prior else GALLERY_FILE)
    if prior:digest=json.loads(events[0]['detail_json'])['record_digest']
    record=dict(schema_version=GALLERY_SCHEMA,task_id=task_id,offer_id=original['offer_id'],owner_sid=sid,
        continued_at=datetime.now(timezone.utc).isoformat(),original_adoption_digest=expected_adoption_digest,
        previous_record_digest=digest,scope_digest=previous['scope_digest'],source=evidence,preserved=preserved,
        purpose='COMPLETED_COMMON_FROZEN_GALLERY_ROLE_COMPATIBILITY',new_edit_attempts=0,external_write_count=0,marketplace_approval=False)
    if prior:record.update(revision=2,purpose='COMPLETED_COMMON_FROZEN_VARIANT_IMAGE_PROJECTION')
    if api_scope_pins is not None:
        from shared_platform.prepared_target_reduction import build_completed_api_selection
        if 'shared_platform/prepared_target_reduction.py' not in evidence['changed_files']:_fail('API_SCOPE_REQUIRED_SOURCE_FILE_MISSING')
        record.update(purpose='COMPLETED_COMMON_API_ONLY_VARIANT_IMAGE_PROJECTION',
            api_target_selection=build_completed_api_selection(task=task,original=original,version=_version(profile),preserved=preserved,pins=api_scope_pins))
    raw=adoption._bytes(record)
    with engine.transaction() as db:
        current=engine._row(db,task_id)
        if (json.loads(current['version_json'])!=old_version or json.loads(current['scope_json'])!=task['scope']
            or json.loads(current['checkpoint_json'])!=task.get('checkpoint') or current['state']!=task['execution_state']
            or current['state']!='waiting_domain' or current['step_index']!=2
            or json.loads(current['action_json'] or 'null')!=task.get('pending_observation')
            or current['worker'] is not None or current['lease_token'] is not None or current['lease_until'] is not None or current['external_started']
            or [row['detail_json'] for row in db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',(task_id,GALLERY_EVENT))]
                != [row['detail_json'] for row in events]
            or _gallery_inputs(profile,store,original,previous,before_append=True)!=preserved):
            _fail('GALLERY_SOURCE_CURRENT_IDLE_TASK_CHANGED')
        engine._event(db,task_id,GALLERY_EVENT,dict(record_json=raw.decode(),record_digest=sha256(raw).hexdigest(),signature=hmac.new(key,raw,'sha256').hexdigest()))
        db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',(adoption._bytes(_version(profile)).decode(),task_id))
    return record


def continue_sixth_source(engine,profile,store,*,task_id,expected_adoption_digest,actor_reader,decision_reader):
    from shared_platform.native_common_colormap_correction import PinnedColorMapDecision,relation,rejected_snapshot,correction_payload
    from shared_platform.native_common_baseline_successor import PinnedBaselineDecision,verify_frozen_sources
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    sid,key=adoption._owner_key(profile);actor=read_native_actor_identity(actor_reader)
    if actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER' or actor.get('owner_sid')!=sid:
        _fail('SOURCE_SUCCESSOR_TRUSTED_SAME_USER_REQUIRED')
    if type(decision_reader) is not PinnedColorMapDecision:_fail('COMMON_COLORMAP_FINITE_TECHNICAL_DECISION_REQUIRED')
    decision=decision_reader.read();task=engine.get(task_id)
    with engine.transaction() as db:
        row=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(task_id,)).fetchone()
        original=_signed(row,key=key,schema=adoption.SCHEMA,owner_sid=sid)
        if (row['record_digest']!=expected_adoption_digest or decision['task_id']!=task_id or decision['offer_id']!=original['offer_id']
            or {r.get('canonical_sku'):r.get('model_sku') for r in decision['variant_bindings']}!=original['facts']['aliases']
            or decision['original_adoption_digest']!=expected_adoption_digest or (task.get('checkpoint') or {}).get('native_r1_adoption')!={
                'record_digest':expected_adoption_digest,'source_identity_binding_sha256':original['facts']['source_identity_binding_sha256']}):
            _fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
        previous,digest=_fifth_historical(task,profile,original,connection=db,owner_sid=sid,key=key)
        if task['version']==_version(profile):return verify_successor(task,profile,original,connection=db,owner_sid=sid,key=key)
    if decision['baseline_decision']!=dict(path=previous['decision_path'],sha256=previous['decision_sha256']):
        _fail('SOURCE_SUCCESSOR_SIXTH_ORIGINAL_DECISION_REQUIRED')
    oldreader=PinnedBaselineDecision(Path(previous['decision_path']),previous['decision_sha256'])
    history=verify_frozen_sources(profile,store,original,oldreader,colormap_reader=decision_reader)
    evidence=source_evidence(profile,previous['source']['new_version'],allowed_files=SIXTH_ALLOWED_FILES)
    with store._connect_readonly() as db:
        db.execute('BEGIN');old=db.execute('SELECT payload_json FROM release_plans WHERE plan_id=?',(decision['original_plan_id'],)).fetchone()
        payload=correction_payload(db,json.loads(old[0]),decision_reader,repo_root=profile.root)
    record=dict(schema_version=SIXTH_SCHEMA,task_id=task_id,offer_id=original['offer_id'],
        continued_at=datetime.now(timezone.utc).isoformat(),owner_sid=sid,actor_session_digest=sha256(adoption._bytes(actor)).hexdigest(),
        original_adoption_digest=expected_adoption_digest,previous_record_digest=digest,scope_digest=previous['scope_digest'],
        source=evidence,baseline_relation=previous['baseline_relation'],correction_relation=relation(decision_reader),
        decision_path=str(decision_reader.path),decision_sha256=decision_reader.sha256,
        corrected_plan_id=payload['plan_id'],corrected_payload_digest=sha256(adoption._bytes(payload)).hexdigest(),
        purpose='COMMON_KNOWN_COLORMAP_REJECTION_CORRECTION',maximum_new_edit_attempts=1,
        combined_edit_attempt_cap=3,marketplace_approval=False,external_write_count=0,business_history_observation=history)
    raw=adoption._bytes(record);record_digest=sha256(raw).hexdigest()
    with engine.transaction() as db:
        current=engine._row(db,task_id)
        if (json.loads(current['version_json'])!=previous['source']['new_version'] or json.loads(current['scope_json'])!=task['scope']
            or json.loads(current['checkpoint_json'])!=task.get('checkpoint') or current['state']!=task['execution_state']
            or current['state']!='waiting_domain' or current['step_index']!=2
            or json.loads(current['action_json'] or 'null')!=task.get('pending_observation')
            or current['worker'] is not None or current['lease_token'] is not None or current['lease_until'] is not None or current['external_started']):
            _fail('SOURCE_SUCCESSOR_CURRENT_IDLE_TASK_REQUIRED')
        with store._connect_readonly() as release_db:
            release_db.execute('BEGIN');rejected_snapshot(release_db,decision_reader)
        db.execute('CREATE TABLE IF NOT EXISTS agent_prepared_source_successors_v6 (task_id TEXT PRIMARY KEY,record_json TEXT NOT NULL,record_digest TEXT NOT NULL,signature TEXT NOT NULL)')
        for action in ('UPDATE','DELETE'):
            db.execute('CREATE TRIGGER IF NOT EXISTS source_successor_v6_no_'+action.lower()+' BEFORE '+action+" ON agent_prepared_source_successors_v6 BEGIN SELECT RAISE(ABORT,'immutable source successor v6'); END")
        db.execute('INSERT INTO agent_prepared_source_successors_v6 VALUES (?,?,?,?)',(task_id,raw.decode(),record_digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',(adoption._bytes(_version(profile)).decode(),task_id))
        engine._event(db,task_id,'source_code_successor_v6',dict(record_digest=record_digest,previous_record_digest=digest,
            old_version=previous['source']['new_version'],new_version=_version(profile)))
    return record

def continue_fifth_source(engine,profile,store,*,task_id,expected_adoption_digest,actor_reader,decision_reader):
    from shared_platform.native_common_baseline_successor import PinnedBaselineDecision,verify_frozen_sources
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    sid,key=adoption._owner_key(profile);actor=read_native_actor_identity(actor_reader)
    if actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER' or actor.get('owner_sid')!=sid:
        _fail('SOURCE_SUCCESSOR_TRUSTED_SAME_USER_REQUIRED')
    if type(decision_reader) is not PinnedBaselineDecision:_fail('COMMON_BASELINE_NEW_EXPLICIT_EDIT_DECISION_REQUIRED')
    decision_reader.read();task=engine.get(task_id)
    with engine.transaction() as db:
        row=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(task_id,)).fetchone()
        original=_signed(row,key=key,schema=adoption.SCHEMA,owner_sid=sid)
        if (row['record_digest']!=expected_adoption_digest or (task.get('checkpoint') or {}).get('native_r1_adoption')!={
            'record_digest':expected_adoption_digest,'source_identity_binding_sha256':original['facts']['source_identity_binding_sha256']}):
            _fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
        previous,digest=_fourth_historical(task,profile,original,connection=db,owner_sid=sid,key=key)
        if task['version']==_version(profile):return verify_successor(task,profile,original,connection=db,owner_sid=sid,key=key)
    if str(decision_reader.path)!=previous['decision_path'] or decision_reader.sha256!=previous['decision_sha256']:
        _fail('SOURCE_SUCCESSOR_FIFTH_REUSES_ORIGINAL_DECISION_REQUIRED')
    history=verify_frozen_sources(profile,store,original,decision_reader)
    source=source_evidence(profile,previous['source']['new_version'],allowed_files=FIFTH_ALLOWED_FILES)
    record=dict(schema_version=FIFTH_SCHEMA,task_id=task_id,offer_id=original['offer_id'],
        continued_at=datetime.now(timezone.utc).isoformat(),owner_sid=sid,actor_session_digest=sha256(adoption._bytes(actor)).hexdigest(),
        original_adoption_digest=expected_adoption_digest,previous_record_digest=digest,scope_digest=previous['scope_digest'],
        source=source,baseline_relation=previous['baseline_relation'],decision_path=previous['decision_path'],decision_sha256=previous['decision_sha256'],
        purpose='COMMON_BASELINE_LOCAL_LOCK_REPAIR',new_edit_attempts=0,marketplace_approval=False,external_write_count=0,
        business_history_observation=history)
    raw=adoption._bytes(record);record_digest=sha256(raw).hexdigest()
    with engine.transaction() as db:
        current=engine._row(db,task_id)
        if (json.loads(current['version_json'])!=previous['source']['new_version'] or json.loads(current['scope_json'])!=task['scope']
            or json.loads(current['checkpoint_json'])!=task.get('checkpoint') or current['state']!=task['execution_state']
            or current['state'] not in {'queued','executor_offline','waiting_domain'} or current['step_index']!=2
            or json.loads(current['action_json'] or 'null')!=(task.get('pending_observation') or task.get('required_action'))
            or current['worker'] is not None or current['lease_token'] is not None or current['lease_until'] is not None or current['external_started']):
            _fail('SOURCE_SUCCESSOR_CURRENT_IDLE_TASK_REQUIRED')
        db.execute('CREATE TABLE IF NOT EXISTS agent_prepared_source_successors_v5 (task_id TEXT PRIMARY KEY,record_json TEXT NOT NULL,record_digest TEXT NOT NULL,signature TEXT NOT NULL)')
        for action in ('UPDATE','DELETE'):
            db.execute('CREATE TRIGGER IF NOT EXISTS source_successor_v5_no_'+action.lower()+' BEFORE '+action+" ON agent_prepared_source_successors_v5 BEGIN SELECT RAISE(ABORT,'immutable source successor v5'); END")
        db.execute('INSERT INTO agent_prepared_source_successors_v5 VALUES (?,?,?,?)',(task_id,raw.decode(),record_digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',(adoption._bytes(_version(profile)).decode(),task_id))
        engine._event(db,task_id,'source_code_successor_v5',dict(record_digest=record_digest,previous_record_digest=digest,
            old_version=previous['source']['new_version'],new_version=_version(profile)))
    return record

def continue_fourth_source(engine,profile,store,*,task_id,expected_adoption_digest,actor_reader,decision_reader):
    from shared_platform.native_common_baseline_successor import PinnedBaselineDecision,relation,original_snapshot
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    sid,key=adoption._owner_key(profile);actor=read_native_actor_identity(actor_reader)
    if actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER' or actor.get('owner_sid')!=sid:
        _fail('SOURCE_SUCCESSOR_TRUSTED_SAME_USER_REQUIRED')
    if type(decision_reader) is not PinnedBaselineDecision:_fail('COMMON_BASELINE_NEW_EXPLICIT_EDIT_DECISION_REQUIRED')
    decision=decision_reader.read();task=engine.get(task_id)
    with engine.transaction() as db:
        row=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(task_id,)).fetchone()
        original=_signed(row,key=key,schema=adoption.SCHEMA,owner_sid=sid)
        if (row['record_digest']!=expected_adoption_digest or decision['task_id']!=task_id
            or decision['offer_id']!=original['offer_id'] or decision['original_adoption_digest']!=expected_adoption_digest
            or (task.get('checkpoint') or {}).get('native_r1_adoption')!={
                'record_digest':expected_adoption_digest,'source_identity_binding_sha256':original['facts']['source_identity_binding_sha256']}):
            _fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
        previous,digest=_third_historical(task,profile,original,connection=db,owner_sid=sid,key=key)
        if task['version']==_version(profile):return verify_successor(task,profile,original,connection=db,owner_sid=sid,key=key)
    source=source_evidence(profile,previous['source']['new_version'],allowed_files=FOURTH_ALLOWED_FILES)
    from shared_platform.native_common_baseline_successor import verify_frozen_sources
    history=verify_frozen_sources(profile,store,original,decision_reader)
    with store._connect_readonly() as db:
        db.execute('BEGIN');original_snapshot(db,decision)
    record=dict(schema_version=FOURTH_SCHEMA,task_id=task_id,offer_id=original['offer_id'],
        continued_at=datetime.now(timezone.utc).isoformat(),owner_sid=sid,actor_session_digest=sha256(adoption._bytes(actor)).hexdigest(),
        original_adoption_digest=expected_adoption_digest,previous_record_digest=digest,scope_digest=previous['scope_digest'],
        source=source,baseline_relation=relation(decision_reader),business_history_observation=history,decision_path=str(decision_reader.path),
        decision_sha256=decision_reader.sha256,marketplace_approval=False,external_write_count=0)
    raw=adoption._bytes(record);record_digest=sha256(raw).hexdigest()
    with engine.transaction() as db:
        current=engine._row(db,task_id)
        if (json.loads(current['version_json'])!=previous['source']['new_version'] or json.loads(current['scope_json'])!=task['scope']
            or json.loads(current['checkpoint_json'])!=task.get('checkpoint') or current['state'] not in {'queued','executor_offline','waiting_domain'}
            or current['step_index']!=2 or current['worker'] is not None or current['lease_token'] is not None or current['external_started']):
            _fail('SOURCE_SUCCESSOR_CURRENT_IDLE_TASK_REQUIRED')
        with store._connect_readonly() as release_db:
            release_db.execute('BEGIN');original_snapshot(release_db,decision_reader.read())
        db.execute('CREATE TABLE IF NOT EXISTS agent_prepared_source_successors_v4 (task_id TEXT PRIMARY KEY,record_json TEXT NOT NULL,record_digest TEXT NOT NULL,signature TEXT NOT NULL)')
        for action in ('UPDATE','DELETE'):
            db.execute('CREATE TRIGGER IF NOT EXISTS source_successor_v4_no_'+action.lower()+' BEFORE '+action+" ON agent_prepared_source_successors_v4 BEGIN SELECT RAISE(ABORT,'immutable source successor v4'); END")
        db.execute('INSERT INTO agent_prepared_source_successors_v4 VALUES (?,?,?,?)',(task_id,raw.decode(),record_digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',(adoption._bytes(_version(profile)).decode(),task_id))
        engine._event(db,task_id,'source_code_successor_v4',dict(record_digest=record_digest,previous_record_digest=digest,
            old_version=previous['source']['new_version'],new_version=_version(profile)))
    return record

def continue_third_source(engine,profile,store,*,task_id,expected_adoption_digest,actor_reader,decision_reader):
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    from shared_platform.manual_common_creation import _digest
    from shared_platform.manual_common_successor import PinnedCommonCorrectionDecision,ManualCommonCorrectionLedger,stored_child
    from shared_platform.agent_prepared_common_service import frozen_create_body
    sid,key=adoption._owner_key(profile);actor=read_native_actor_identity(actor_reader)
    if actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER' or actor.get('owner_sid')!=sid:
        _fail('SOURCE_SUCCESSOR_TRUSTED_SAME_USER_REQUIRED')
    if type(decision_reader) is not PinnedCommonCorrectionDecision:_fail('SOURCE_CORRECTION_INSTALLED_DECISION_REQUIRED')
    task=engine.get(task_id)
    with engine.transaction() as db:
        row=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(task_id,)).fetchone()
        original=_signed(row,key=key,schema=adoption.SCHEMA,owner_sid=sid)
        if row['record_digest']!=expected_adoption_digest:_fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
        if original['task_id']!=task_id or (task.get('checkpoint') or {}).get('native_r1_adoption')!={
            'record_digest':expected_adoption_digest,'source_identity_binding_sha256':original['facts']['source_identity_binding_sha256']}:
            _fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
        previous,digest=_second_historical(task,profile,original,connection=db,owner_sid=sid,key=key)
        if task['version']==_version(profile):return verify_successor(task,profile,original,connection=db,owner_sid=sid,key=key)
    source=source_evidence(profile,previous['source']['new_version'],allowed_files=THIRD_ALLOWED_FILES)
    facts=adoption._verify_sources(profile,original['offer_id'],original['targets'])
    if {k:v for k,v in facts.items() if k!='execution_source_head'}!={k:v for k,v in original['facts'].items() if k!='execution_source_head'}:
        _fail('SOURCE_SUCCESSOR_PREPARATION_OR_PAID_FACTS_CHANGED')
    child=stored_child(store.path,original['offer_id']);product=original['facts']['preparation_source']['round1_snapshot']['fact_snapshot']['product_facts']
    body=frozen_create_body(product,original['facts']['seller_sku'])
    # The exact same validator used by reservation checks the new pin/scope.
    # Validation is read-only here; the schema/slot is not created by this call.
    correction=ManualCommonCorrectionLedger(store.path,decision_reader=decision_reader)
    correction.correction_relation(child,task=task,adoption_record=original,source_chain_digest=digest,create_body=body)
    record=dict(schema_version=THIRD_SCHEMA,task_id=task_id,offer_id=original['offer_id'],continued_at=datetime.now(timezone.utc).isoformat(),
        owner_sid=sid,actor_session_digest=_digest(actor),original_adoption_digest=expected_adoption_digest,
        previous_record_digest=digest,scope_digest=previous['scope_digest'],source=source,
        child_intent_id=child['intent_id'],rejected_child_sha256=_digest(child),corrected_create_body=body,
        decision_path=str(decision_reader.path),decision_sha256=decision_reader.sha256,marketplace_approval=False,external_write_count=0)
    raw=adoption._bytes(record);record_digest=sha256(raw).hexdigest()
    with engine.transaction() as db:
        current=engine._row(db,task_id)
        if (json.loads(current['version_json'])!=previous['source']['new_version'] or json.loads(current['scope_json'])!=task['scope']
            or json.loads(current['checkpoint_json'])!=task.get('checkpoint') or current['state'] not in {'queued','executor_offline'}
            or current['step_index']!=2 or current['worker'] is not None or current['lease_token'] is not None or current['external_started']):
            _fail('SOURCE_SUCCESSOR_CURRENT_IDLE_TASK_REQUIRED')
        if stored_child(store.path,original['offer_id'])!=child:_fail('SOURCE_SUCCESSOR_REJECTED_CHILD_CHANGED')
        db.execute('CREATE TABLE IF NOT EXISTS agent_prepared_source_successors_v3 (task_id TEXT PRIMARY KEY,record_json TEXT NOT NULL,record_digest TEXT NOT NULL,signature TEXT NOT NULL)')
        for action in ('UPDATE','DELETE'):
            db.execute('CREATE TRIGGER IF NOT EXISTS source_successor_v3_no_'+action.lower()+' BEFORE '+action+" ON agent_prepared_source_successors_v3 BEGIN SELECT RAISE(ABORT,'immutable source successor v3'); END")
        db.execute('INSERT INTO agent_prepared_source_successors_v3 VALUES (?,?,?,?)',(task_id,raw.decode(),record_digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',(adoption._bytes(_version(profile)).decode(),task_id))
        engine._event(db,task_id,'source_code_successor_v3',dict(record_digest=record_digest,previous_record_digest=digest,
            old_version=previous['source']['new_version'],new_version=_version(profile)))
    return record

def continue_second_source(engine,profile,store,*,task_id,expected_adoption_digest,actor_reader,decision_reader):
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    from shared_platform.manual_common_successor import PinnedCommonSuccessorDecision
    from shared_platform.manual_common_creation import ManualCommonCreateLedger,_digest
    sid,key=adoption._owner_key(profile);actor=read_native_actor_identity(actor_reader)
    if actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER' or actor.get('owner_sid')!=sid:
        _fail('SOURCE_SUCCESSOR_TRUSTED_SAME_USER_REQUIRED')
    if type(decision_reader) is not PinnedCommonSuccessorDecision:_fail('SOURCE_SUCCESSOR_INSTALLED_DECISION_REQUIRED')
    task=engine.get(task_id)
    with engine.transaction() as db:
        row=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(task_id,)).fetchone()
        original=_signed(row,key=key,schema=adoption.SCHEMA,owner_sid=sid)
        if row['record_digest']!=expected_adoption_digest:_fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
        if original['task_id']!=task_id or (task.get('checkpoint') or {}).get('native_r1_adoption')!={
            'record_digest':expected_adoption_digest,'source_identity_binding_sha256':original['facts']['source_identity_binding_sha256']}:
            _fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
        origin=db.execute("SELECT created_at FROM workbench_events WHERE id=? AND task_id=? AND event_type='workflow_created'",
            (original['task_created_event_id'],task_id)).fetchone()
        if origin is None or origin[0]!=original['task_created_at']:_fail('SOURCE_SUCCESSOR_GENUINE_EVENT_REQUIRED')
        first,first_digest=_first_link(task,profile,original,connection=db,owner_sid=sid,key=key)
        if task['version']==_version(profile):
            return verify_successor(task,profile,original,connection=db,owner_sid=sid,key=key)
    source=source_evidence(profile,first['source']['new_version'],allowed_files=SECOND_ALLOWED_FILES)
    facts=adoption._verify_sources(profile,original['offer_id'],original['targets'])
    if {k:v for k,v in facts.items() if k!='execution_source_head'}!={k:v for k,v in original['facts'].items() if k!='execution_source_head'}:
        _fail('SOURCE_SUCCESSOR_PREPARATION_OR_PAID_FACTS_CHANGED')
    parent=ManualCommonCreateLedger(store.path).for_offer(original['offer_id'])
    if parent is None or parent['state']!='UNKNOWN' or parent['attempts']!=1 or parent.get('parent_intent_id') is not None:
        _fail('SOURCE_SUCCESSOR_EXACT_ORIGINAL_UNKNOWN_REQUIRED')
    if (parent['intent_id']!=first['original_manual_intent']['intent_id']
        or parent['intent_digest']!=first['original_manual_intent']['intent_digest']
        or parent['returned_detail_id'] is not None or parent['create_response'] is not None):
        _fail('SOURCE_SUCCESSOR_EXACT_ORIGINAL_UNKNOWN_REQUIRED')
    # Validate the installed decision before any task-version mutation, but do
    # not reserve a child here or change the original CREATE row.
    decision=decision_reader.read()
    expected=dict(task_id=task_id,offer_id=parent['offer_id'],original_intent_id=parent['intent_id'],
        original_adoption_digest=expected_adoption_digest,round1_snapshot_digest=parent['intent']['round1_digest'],
        source_identity_binding_sha256=parent['intent']['source_identity_binding_sha256'],seller_sku=parent['intent']['seller_sku'],
        model_skus=parent['intent']['model_skus'],targets=parent['intent']['target_labels'],canonical_skus=sorted(original['facts']['aliases']))
    if any(decision.get(k)!=v for k,v in expected.items()) or parent['intent']['owner_sid']!=sid:_fail('SOURCE_SUCCESSOR_DECISION_SCOPE_CHANGED')
    record=dict(schema_version=SECOND_SCHEMA,task_id=task_id,offer_id=original['offer_id'],
        continued_at=datetime.now(timezone.utc).isoformat(),owner_sid=sid,actor_session_digest=sha256(adoption._bytes(actor)).hexdigest(),
        original_adoption_digest=expected_adoption_digest,previous_record_digest=first_digest,scope_digest=first['scope_digest'],
        source=source,original_intent_id=parent['intent_id'],original_manual_intent_sha256=_digest(parent),decision_sha256=decision_reader.sha256,
        decision_path=str(decision_reader.path),marketplace_approval=False,external_write_count=0)
    raw=adoption._bytes(record);digest=sha256(raw).hexdigest()
    with engine.transaction() as db:
        current=engine._row(db,task_id)
        if (json.loads(current['version_json'])!=first['source']['new_version'] or json.loads(current['scope_json'])!=task['scope']
            or json.loads(current['checkpoint_json'])!=task.get('checkpoint') or current['state'] not in {'queued','executor_offline'}
            or current['step_index']!=2 or current['worker'] is not None or current['lease_token'] is not None or current['external_started']):
            _fail('SOURCE_SUCCESSOR_CURRENT_IDLE_TASK_REQUIRED')
        if ManualCommonCreateLedger(store.path).read(parent['intent_id'])!=parent:_fail('SOURCE_SUCCESSOR_INTENT_CHANGED')
        db.execute('CREATE TABLE IF NOT EXISTS agent_prepared_source_successors_v2 (task_id TEXT PRIMARY KEY,record_json TEXT NOT NULL,record_digest TEXT NOT NULL,signature TEXT NOT NULL)')
        for action in ('UPDATE','DELETE'):
            db.execute('CREATE TRIGGER IF NOT EXISTS source_successor_v2_no_'+action.lower()+' BEFORE '+action+" ON agent_prepared_source_successors_v2 BEGIN SELECT RAISE(ABORT,'immutable source successor v2'); END")
        db.execute('INSERT INTO agent_prepared_source_successors_v2 VALUES (?,?,?,?)',(task_id,raw.decode(),digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',(adoption._bytes(_version(profile)).decode(),task_id))
        engine._event(db,task_id,'source_code_successor_v2',{'record_digest':digest,'previous_record_digest':first_digest,
            'old_version':first['source']['new_version'],'new_version':_version(profile)})
    return record


def continue_source(engine,profile,store,*,task_id,expected_adoption_digest,actor_reader):
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    sid,key=adoption._owner_key(profile);actor=read_native_actor_identity(actor_reader)
    if actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER' or actor.get('owner_sid')!=sid:
        _fail('SOURCE_SUCCESSOR_TRUSTED_SAME_USER_REQUIRED')
    task=engine.get(task_id);frozen=(task.get('checkpoint') or {}).get('native_r1') or {}
    original=adoption.verify(task,profile,offer=task['scope']['offer_id'],targets=sorted(task['scope']['shops']),
        prepared_reference=frozen['prepared_reference'],snapshot_digest=frozen['snapshot_digest'])
    if sha256(adoption._bytes(original)).hexdigest()!=expected_adoption_digest:
        _fail('SOURCE_SUCCESSOR_ORIGINAL_ADOPTION_REQUIRED')
    if task['version']==_version(profile):
        with sqlite3.connect((Path(profile.data_root)/'tasks.db').resolve().as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            return verify_successor(task,profile,original,connection=db,owner_sid=sid,key=key)
    source=source_evidence(profile,original['release'])
    facts=adoption._verify_sources(profile,original['offer_id'],original['targets'])
    if {k:v for k,v in facts.items() if k!='execution_source_head'}!={k:v for k,v in original['facts'].items() if k!='execution_source_head'}:
        _fail('SOURCE_SUCCESSOR_PREPARATION_OR_PAID_FACTS_CHANGED')
    intent=_manual_intent(store,original['offer_id'])
    record=dict(schema_version=SCHEMA,task_id=task_id,offer_id=original['offer_id'],
        continued_at=datetime.now(timezone.utc).isoformat(),owner_sid=sid,
        actor_session_digest=sha256(adoption._bytes(actor)).hexdigest(),
        original_adoption_digest=sha256(adoption._bytes(original)).hexdigest(),
        scope_digest=sha256(adoption._bytes(task['scope'])).hexdigest(),source=source,
        original_manual_intent=intent,marketplace_approval=False,external_write_count=0)
    raw=adoption._bytes(record);digest=sha256(raw).hexdigest()
    with engine.transaction() as db:
        current=engine._row(db,task_id)
        if (json.loads(current['version_json'])!=original['release']
            or json.loads(current['scope_json'])!=task['scope']
            or json.loads(current['checkpoint_json'])!=task.get('checkpoint')
            or current['state'] not in {'queued','executor_offline'} or current['step_index']!=2
            or current['worker'] is not None or current['lease_token'] is not None or current['external_started']):
            _fail('SOURCE_SUCCESSOR_CURRENT_IDLE_TASK_REQUIRED')
        if _manual_intent(store,original['offer_id'])!=intent:_fail('SOURCE_SUCCESSOR_INTENT_CHANGED')
        db.execute(TABLE)
        for action in ('UPDATE','DELETE'):
            db.execute('CREATE TRIGGER IF NOT EXISTS source_successor_no_'+action.lower()+
                ' BEFORE '+action+" ON agent_prepared_source_successors BEGIN SELECT RAISE(ABORT,'immutable source successor'); END")
        db.execute('INSERT INTO agent_prepared_source_successors VALUES (?,?,?,?)',
            (task_id,raw.decode(),digest,hmac.new(key,raw,'sha256').hexdigest()))
        db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',
            (adoption._bytes(_version(profile)).decode(),task_id))
        engine._event(db,task_id,'source_code_successor',{'record_digest':digest,
            'old_version':original['release'],'new_version':_version(profile)})
    adoption.verify(engine.get(task_id),profile,offer=original['offer_id'],targets=original['targets'],
        prepared_reference=frozen['prepared_reference'],snapshot_digest=frozen['snapshot_digest'])
    return record
