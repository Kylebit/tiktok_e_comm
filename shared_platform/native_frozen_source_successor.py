"""One normal-task source update after complete R2, before COMMON dispatch.

The trusted local caller quiesces all product writers first. Registration is
append-only plus a version CAS, not an adoption, new POST or commerce grant.
Old frozen inputs, paid entries, task key and all earlier source links remain.
"""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import stat
import subprocess

EVENT='native_frozen_predecessor_source_successor'
SCHEMA='native-frozen-predecessor-source-successor/v1'
MAX_FILES=1024
MAX_BYTES=256*1024*1024


def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def _digest(value):return sha256(_json(value).encode()).hexdigest()
def _need(ok,code):
    if not ok:raise ValueError('FROZEN_SOURCE_'+code)
def _history(db,task_id,before=None):
    from shared_platform.native_unfrozen_source_successor import _history as history
    return history(db,task_id,before)


def _file(path,roots):
    path=Path(path)
    _need(path.is_absolute() and '..' not in path.parts
          and any(path.is_relative_to(root) for root in roots),'FILE_SCOPE_CHANGED')
    for parent in (path,*path.parents):
        info=parent.lstat()
        _need(not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&0x400,'FILE_REDIRECTED')
    before=path.stat();_need(stat.S_ISREG(before.st_mode) and before.st_nlink==1,'FILE_UNSAFE')
    raw=path.read_bytes();after=path.stat()
    _need((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==
          (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) and len(raw)==before.st_size,'FILE_CHANGED')
    return {'path':str(path),'sha256':sha256(raw).hexdigest(),'byte_length':len(raw)}


def _files_equal(seal,*,media=None):
    roots=[Path(x) for x in seal['roots']]
    _need(len(seal['files'])<=MAX_FILES and sum(x['byte_length'] for x in seal['files'])<=MAX_BYTES,
          'FILE_MANIFEST_EXCEEDS_BOUND')
    media_paths=set(seal['media_paths'])
    for row in seal['files']:
        if media is not None and (row['path'] in media_paths)!=media:continue
        _need(_file(row['path'],roots)==row,'SEALED_FILE_CHANGED')
    paid=Path(seal['paid']['directory'])
    current=sorted(str(p) for p in paid.rglob('*') if p.is_file() and p.suffix in {'.json','.jsonl'})
    _need(current==seal['paid']['json_files'],'PAID_FILE_SET_CHANGED')


def _ops_effects(db,task_id):
    from shared_platform.native_unfrozen_source_successor import _count
    values={name:_count(db,name,field+'=?',(task_id,)) for name,field in (
        ('workbench_external_tasks','task_id'),('workbench_receipts','task_id'),
        ('workbench_review_identity','task_id'),('workbench_publication_scope_reductions','task_id'),
        ('workbench_domain_operations','owner_task_id'))}
    _need(not any(values.values()),'TASK_EXTERNAL_OR_REVIEW_AUTHORITY_EXISTS')
    scope=json.loads(db.execute('SELECT scope_json FROM workbench_execution WHERE task_id=?',(task_id,)).fetchone()[0])
    from shared_platform.workbench_store import _json as resource_json
    for shop in scope['shops']:
        for sku in scope['skus']:
            resource='product:'+resource_json([shop,sku])
            _need(not db.execute('SELECT 1 FROM workbench_domain_locks WHERE resource=?',(resource,)).fetchone()
                  and not db.execute('SELECT 1 FROM workbench_resource_locks WHERE resource=? AND task_id<>?',
                                     (resource,task_id)).fetchone(),'COMPETING_PRODUCT_WRITER')
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='workbench_category_intents'").fetchone():
        _need(not db.execute("SELECT 1 FROM workbench_category_intents WHERE task_id=? AND status<>'SUCCEEDED'",
                            (task_id,)).fetchone(),'CATEGORY_OUTCOME_UNRESOLVED')
    return values


def _effects(db,store,task_id,offer):
    from shared_platform.native_unfrozen_source_successor import _count
    values=_ops_effects(db,task_id)
    with store._connect_readonly() as r:
        counts={name:_count(r,name,where,args) for name,where,args in (
            ('approved_publication_snapshots','offer_id=?',(offer,)),
            ('release_runs','plan_id IN (SELECT plan_id FROM release_plans WHERE product_id=?)',(offer,)),
            ('native_sole_final_decisions','plan_id IN (SELECT plan_id FROM release_plans WHERE product_id=?)',(offer,)),
            ('native_final_budget_scopes','plan_id IN (SELECT plan_id FROM release_plans WHERE product_id=?)',(offer,)))}
        _need(not any(counts.values()),'COMMON_CHANNEL_OR_FINAL_STARTED')
        if r.execute("SELECT 1 FROM sqlite_master WHERE name='manual_common_creations'").fetchone():
            _need(not r.execute('SELECT 1 FROM manual_common_creations WHERE offer_id=? AND attempts<>0',(offer,)).fetchone(),
                  'COMMON_CREATION_DISPATCHED')
        plans=r.execute('SELECT plan_id,target_labels_json,approved_at FROM release_plans WHERE product_id=?',(offer,)).fetchall()
        _need(all(json.loads(p['target_labels_json'])==['miaoshou:COMMON'] and p['approved_at'] is None for p in plans),
              'MARKETPLACE_OR_APPROVED_PLAN_EXISTS')
    return {'operations':values,'release':counts,'local_common_plan_ids':sorted(p['plan_id'] for p in plans)}


def _row_ready(row):
    steps=json.loads(row['steps_json']);scope=json.loads(row['scope_json'])
    _need(row['template']=='publication' and row['state'] in {'queued','reconciliation_required'}
          and row['step_index']==2 and not row['external_started']
          and all(row[k] is None for k in ('worker','lease_token','lease_until','action_json')),
          'IDLE_POST_R2_TASK_REQUIRED')
    _need([x['key'] for x in steps]==['facts','images','release','readback']
          and all(x['state']=='completed' for x in steps[:2])
          and all(x['state']=='pending' for x in steps[2:]) and scope.get('skus'),
          'COMPLETE_FROZEN_PREDECESSORS_REQUIRED')


def observe(engine,task_id,*,profile,release_store):
    """Bounded, nonmutating observation; never starts media or calls providers.

    profile identifies the original data/producer release. During registration
    the actual newly pinned consumer validates those retained facts, without
    claiming that the old source is the process's current execution version.
    """
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform.operations_runtime import RuntimeProfile
    from shared_platform.release_store import ReleaseStore,default_release_store
    from shared_platform import workbench_publication_native as native
    from shared_platform.native_unfrozen_source_successor import verified_explicit_origin
    _need(type(engine) is WorkbenchEngine and type(profile) is RuntimeProfile and type(release_store) is ReleaseStore
          and release_store.path.resolve()==default_release_store().path.resolve()
          and engine.store.path.resolve()==(profile.data_root/'tasks.db').resolve()
          and engine.release=={'code_version':profile.version,'environment':profile.environment,'manifest_digest':profile.manifest_digest},
          'EXACT_NORMAL_BINDINGS_REQUIRED')
    with engine.readonly_snapshot() as db:
        row=engine._row(db,task_id);_row_ready(row)
        _need(not db.execute('SELECT 1 FROM workbench_events WHERE task_id=? AND event_type=?',(task_id,EVENT)).fetchone(),
              'ALREADY_REGISTERED')
        verified_explicit_origin(db,row,engine.release)
        task=engine._project(db,row)
        source_key=db.execute('SELECT source_key FROM workbench_tasks WHERE task_id=?',(task_id,)).fetchone()[0]
        history=_history(db,task_id)
        effects=_effects(db,release_store,task_id,task['scope']['offer_id'])
    images=native.read_images(task,profile)
    completed=next(x for x in task['steps'] if x['key']=='images')['checkpoint']
    _need(completed.get('native_r2')==images['native_r2'],'R2_STEP_RECEIPT_CHANGED')
    from shared_platform import publication_r3_image_bridge as bridge,round1_workspace,publication_rounds
    offer=task['scope']['offer_id'];directory=profile.root/'reports/product-preparation'/offer
    documents=bridge.load_r2_documents(offer,reports_root=directory.parent,
        native_review_reader=native.NativeR2PreparedReviewReader(task,profile))
    preparation=round1_workspace.read_preparation(offer,images['native_r1']['prepared_reference'])
    from shared_platform.native_parent_images import NativeParentImagesAdapter
    from shared_platform.native_task_preparation import NativeR1FilesystemBoundary
    history_root,binding_digest=NativeParentImagesAdapter(engine,profile,NativeR1FilesystemBoundary(profile))._history_binding()
    from shared_platform.publication_paid_requests import PaidRequestContext,validate_usage_baseline
    from shared_platform.publication_autopilot import load_autopilot_policy
    policy_path=profile.root/'config/product_publication_autopilot_policy.json'
    context=PaidRequestContext(offer_id=offer,round1=documents['round1_snapshot'],
        policy=load_autopilot_policy(policy_path),reports_root=history_root/'reports/product-preparation')
    paid_rows,entries=context._load()
    _need(paid_rows and context.path.is_file(),'ORIGINAL_PAID_JOURNAL_REQUIRED')
    validate_usage_baseline(paid_rows[0]['baseline'],offer_id=offer)
    historical=context._historical(paid_rows)
    _need(all(v['state'] in {'RECEIVED','CONFIRMED','FAILED','FAILED_REFUNDED','NO_CHARGE'} for v in entries.values())
          and all(v['state'] in {'CONFIRMED','FAILED','NO_CHARGE'} and v['provider']!='UNKNOWN' for v in historical),
          'PAID_OUTCOME_UNRESOLVED')
    _need(all(v['binding']==context.binding for v in entries.values()),'PAID_ORIGINAL_BINDING_CHANGED')
    for key,value in entries.items():
        if value['state']=='RECEIVED':
            raw=context._raw(key)
            from modules.sourcing.image_generation_checkpoint import digest as paid_digest
            _need(raw is not None and paid_digest(raw)==value.get('raw_digest'),'PAID_RECEIVED_RAW_UNPROVEN')
    roots=sorted({directory,history_root/'reports/product-preparation'/offer,
        profile.root/'data/localized_image_packs'/offer,profile.root/'data/localized_image_reviews'/offer,
        history_root/'data/localized_image_packs'/offer,history_root/'data/localized_image_reviews'/offer,
        profile.root/'config'},key=str)
    paths={directory/name for name in bridge.R2_DOCUMENTS.values()}
    paths|={directory/'round1-auto-decision.json',policy_path,
            profile.root/'config/product_publication_paid_history.local.json'}
    if preparation.get('candidate_input',{}).get('present'):paths.add(directory/'first-review-candidate-plan.json')
    media_paths=set()
    for value in (documents['generation_result'],documents['translation_result']):
        for asset in value.get('assets') or []:
            artifact=asset.get('artifact_path')
            if asset.get('checkpoint_path'):
                checkpoint=Path(asset['checkpoint_path']);_file(checkpoint,roots);paths.add(checkpoint)
                from modules.sourcing.image_generation_checkpoint import ImageCheckpoint
                output=ImageCheckpoint.from_path(checkpoint).output_path
                _need(artifact in (None,str(output)),'R2_ORIGINAL_LOCAL_ARTIFACT_CHANGED')
                artifact=str(output)
            _need(artifact,'R2_ORIGINAL_LOCAL_ARTIFACT_REQUIRED')
            paths.add(Path(artifact))
            media_paths.add(str(Path(artifact)))
            _need(_file(artifact,roots)['sha256']==str(asset['artifact_digest']).removeprefix('sha256:'),
                  'R2_ORIGINAL_ARTIFACT_CHANGED')
    paid_files=sorted(str(p) for p in context.root.rglob('*') if p.is_file() and p.suffix in {'.json','.jsonl'})
    paths|={Path(p) for p in paid_files}
    baseline=context.directory/'paid-usage-baseline.json'
    if baseline.is_file():paths.add(baseline)
    _need(len(paths)<=MAX_FILES,'FILE_MANIFEST_EXCEEDS_BOUND')
    files=[_file(path,roots) for path in sorted(paths,key=str)]
    _need(sum(x['byte_length'] for x in files)<=MAX_BYTES,'FILE_MANIFEST_EXCEEDS_BOUND')
    return {'task_id':task_id,'source_key':source_key,'request_digest':row['request_digest'],
        'scope':json.loads(row['scope_json']),'from_release':engine.release,
        'checkpoint_raw':row['checkpoint_json'],'steps_raw':row['steps_json'],'registration_state':row['state'],
        'history_sha256':_digest(history),'predecessors':{'images':images,
            'prepared_reference':preparation['reference'],'preparation_digest':publication_rounds.canonical_digest(preparation),
            'roots':[str(x) for x in roots],'files':files,'media_paths':sorted(media_paths),'paid':{'directory':str(context.root),
                'json_files':paid_files,'binding_digest':binding_digest,'journal_digest':paid_rows[-1]['event_digest'],
                'entry_count':len(entries),'historical_count':len(historical),
                'states':dict(Counter(v['state'] for v in entries.values())),
                'usage_facts':[{'key':k,'usage':v.get('usage'),'cost':v.get('cost')} for k,v in sorted(entries.items())]}},
        'effects':effects}


def verify(db,row,original,grant,from_release,to_release):
    """Consume one appended source boundary; ongoing progress need not stay zero."""
    events=db.execute('SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type=? ORDER BY id',
                      (row['task_id'],EVENT)).fetchall()
    if not events:return from_release
    _need(len(events)==1,'MULTIPLE_FROZEN_SUCCESSORS_REJECTED')
    event=events[0];envelope=json.loads(event['detail_json'])
    _need(type(envelope) is dict and set(envelope)=={'record','record_sha256'},'EVENT_SHAPE_CHANGED')
    record=envelope['record'];_need(envelope['record_sha256']==_digest(record),'RECORD_DIGEST_CHANGED')
    _need(set(record)=={'schema_version','task_id','source_key','request_digest','scope','from_release','to_release',
        'checkpoint_raw','steps_raw','registration_state','history_sha256','predecessors','effects','source',
        'original_post_event_id','original_post_sha256','execution_authority'}
        and record['schema_version']==SCHEMA and record['execution_authority'] is False,'RECORD_CONTRACT_CHANGED')
    _need(record['task_id']==row['task_id'],'ORIGINAL_TASK_CHANGED')
    _need(record['source_key']==original['source_key'],'ORIGINAL_KEY_CHANGED')
    _need(record['request_digest']==row['request_digest']==original['request_digest'],'ORIGINAL_REQUEST_CHANGED')
    _need(record['scope']==json.loads(row['scope_json']),'ORIGINAL_SCOPE_CHANGED')
    _need(record['from_release']==from_release and record['to_release']==to_release
          and from_release!=to_release,'SOURCE_ROLE_LINEAGE_CHANGED')
    _need(record['original_post_event_id']==grant['id']
          and record['original_post_sha256']==sha256(grant['detail_json'].encode()).hexdigest(),'ORIGINAL_POST_CHANGED')
    _need(record['history_sha256']==_digest(_history(db,row['task_id'],event['id'])),'ORIGINAL_HISTORY_CHANGED')
    _need(record['source']['from_head']==from_release['code_version']
          and record['source']['to_head']==to_release['code_version']
          and record['source']['manifest_digest']==to_release['manifest_digest'], 'SOURCE_BINDING_CHANGED')
    _need(not any(record['effects']['operations'].values()) and not any(record['effects']['release'].values()),
          'REGISTRATION_WAS_NOT_PRECOMMON')
    steps=json.loads(record['steps_raw']);images=record['predecessors']['images']
    _need([x['key'] for x in steps]==['facts','images','release','readback']
          and all(x['state']=='completed' for x in steps[:2]) and all(x['state']=='pending' for x in steps[2:])
          and steps[0]['checkpoint']['native_r1']==images['native_r1']
          and steps[1]['checkpoint']['native_r2']==images['native_r2']
          and images['native_r1']['offer_id']==images['native_r2']['offer_id']==record['scope']['offer_id']
          and images['native_r1']['targets']==sorted(record['scope']['shops'])
          and images['native_r1']['internal_sku'] in record['scope']['skus']
          and images['native_r1']['prepared_reference']==record['predecessors']['prepared_reference']
          and images['native_r1']['snapshot_digest']==images['native_r2']['round1_snapshot_digest'],
          'REGISTERED_PREDECESSORS_INCOMPLETE')
    return to_release


def verify_native_owner(db,row,release,profile=None):
    """Existing frozen/common readers also consume the registered source proof."""
    if db.execute('SELECT 1 FROM workbench_events WHERE task_id=? AND event_type=?',(row['task_id'],EVENT)).fetchone():
        from shared_platform.native_unfrozen_source_successor import verified_explicit_origin
        verified_explicit_origin(db,row,release)
        envelope=json.loads(db.execute('SELECT detail_json FROM workbench_events WHERE task_id=? AND event_type=?',
                                      (row['task_id'],EVENT)).fetchone()[0])
        if profile is not None:
            _need(Path(envelope['record']['source']['root']).resolve()==profile.root.resolve(),
                  'CURRENT_CONSUMER_ROOT_CHANGED')
        return envelope['record']['predecessors']
    return None


def verify_native_files(seal,*,media=False):
    if seal is not None:_files_equal(seal,media=media)


def verify_native_media(task,profile):
    """Image bytes checked outside any operations/release DB transaction."""
    from contextlib import closing
    with closing(sqlite3.connect((profile.data_root/'tasks.db').resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        if not db.execute('SELECT 1 FROM workbench_events WHERE task_id=? AND event_type=?',
                          (task['task_id'],EVENT)).fetchone():return
        row=dict(db.execute('SELECT * FROM workbench_execution WHERE task_id=?',(task['task_id'],)).fetchone())
        release={'code_version':profile.version,'environment':profile.environment,'manifest_digest':profile.manifest_digest}
        _need(json.loads(row['scope_json'])==task['scope'] and json.loads(row['version_json'])==release,
              'CURRENT_MEDIA_OWNER_CHANGED')
        seal=verify_native_owner(db,row,release,profile)
    verify_native_files(seal,media=True)


def _stamp(path):
    path=Path(path)
    try:
        s=path.lstat()
    except FileNotFoundError:return None
    _need(not stat.S_ISLNK(s.st_mode) and not getattr(s,'st_file_attributes',0)&0x400,'MONITORED_FILE_REDIRECTED')
    return s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns


def register(engine,task_id,*,profile,release_store,expected):
    """Trusted local one-shot. Caller must quiesce old worker and all writers.

    All long source/paid/artifact observation is outside the short final CAS.
    No network, providers, DDL, new task, approval or existing-evidence rewrite.
    """
    from shared_platform.operations_runtime import RuntimeProfile,runtime_matches
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform.release_store import ReleaseStore
    _need(type(engine) is WorkbenchEngine and type(release_store) is ReleaseStore
          and type(profile) is RuntimeProfile and profile.environment=='stable'
          and runtime_matches(profile) and engine.release=={'code_version':profile.version,
              'environment':profile.environment,'manifest_digest':profile.manifest_digest},'CURRENT_SOURCE_REQUIRED')
    with engine.readonly_snapshot() as db:
        _need(not db.execute('SELECT 1 FROM workbench_events WHERE task_id=? AND event_type=?',(task_id,EVENT)).fetchone(),
              'ALREADY_REGISTERED')
    old=expected['from_release']
    original_profile=RuntimeProfile(profile.root,profile.data_root,old['environment'],old['code_version'],old['manifest_digest'])
    original_engine=WorkbenchEngine(engine.store.path,old)
    observed=observe(original_engine,task_id,profile=original_profile,release_store=release_store)
    _need(observed==expected,'EXPECTED_FROZEN_OBSERVATION_CHANGED')
    def git(*args):return subprocess.check_output(['git','-C',str(profile.root),*args],text=True).strip()
    git('merge-base','--is-ancestor',old['code_version'],profile.version)
    _need(old!=engine.release and not git('status','--porcelain=v1','-uall'),'CLEAN_DESCENDANT_SOURCE_REQUIRED')
    changes=git('diff','--name-status',old['code_version'],profile.version).splitlines()
    _need(changes,'SOURCE_CHANGE_REQUIRED')
    source={'root':str(profile.root),'from_head':old['code_version'],'to_head':profile.version,
            'manifest_digest':profile.manifest_digest,'changed_paths':changes}
    watched={Path(f['path']) for f in expected['predecessors']['files']}
    watched|={release_store.path,*(release_store.path.with_name(release_store.path.name+suffix)
                                for suffix in ('-wal','-shm','-journal'))}
    stamps={p:_stamp(p) for p in watched}
    from shared_platform.native_task_preparation import NativeR1FilesystemBoundary
    boundary=NativeR1FilesystemBoundary(profile)
    history_root=Path(expected['predecessors']['paid']['directory']).parents[3]
    pin=boundary._pin(sorted({Path(f['path']).parent for f in expected['predecessors']['files']},key=str),
                      release_parent=history_root)
    pin.__enter__();db=None
    try:
        db=sqlite3.connect(engine.store.path.resolve().as_uri()+'?mode=rw',uri=True,timeout=3)
        db.row_factory=sqlite3.Row
        db.execute('BEGIN IMMEDIATE')
        row=engine._row(db,task_id);_row_ready(row)
        _need(json.loads(row['version_json'])==old and row['checkpoint_json']==expected['checkpoint_raw']
              and row['steps_json']==expected['steps_raw'] and json.loads(row['scope_json'])==expected['scope']
              and row['request_digest']==expected['request_digest']
              and _digest(_history(db,task_id))==expected['history_sha256'],'FINAL_ORIGINAL_CAS_CHANGED')
        from shared_platform.native_unfrozen_source_successor import verified_explicit_origin
        verified_explicit_origin(db,row,old)
        _need(_ops_effects(db,task_id)==expected['effects']['operations'],'FINAL_EFFECTS_CHANGED')
        _need(all(_stamp(p)==s for p,s in stamps.items()),'OBSERVED_FILES_OR_RELEASE_GENERATION_CHANGED')
        grant=db.execute("SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type='explicit_new_post_preparation'",
                         (task_id,)).fetchone()
        record={**expected,'schema_version':SCHEMA,'to_release':engine.release,'source':source,
                'original_post_event_id':grant['id'],'original_post_sha256':sha256(grant['detail_json'].encode()).hexdigest(),
                'execution_authority':False}
        changed=db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=? AND version_json=? '
            'AND checkpoint_json=? AND steps_json=? AND scope_json=? AND worker IS NULL AND lease_token IS NULL '
            'AND lease_until IS NULL AND action_json IS NULL AND external_started=0',
            (_json(engine.release),task_id,row['version_json'],row['checkpoint_json'],row['steps_json'],row['scope_json'])).rowcount
        _need(changed==1,'VERSION_CAS_LOST')
        engine._event(db,task_id,EVENT,{'record':record,'record_sha256':_digest(record)})
        verified_explicit_origin(db,engine._row(db,task_id),engine.release)
        _need(all(_stamp(p)==s for p,s in stamps.items()),'OBSERVED_FILES_OR_RELEASE_GENERATION_CHANGED')
        db.commit();return record
    except BaseException:
        if db is not None:db.rollback()
        raise
    finally:
        if db is not None:db.close()
        pin.__exit__(None,None,None)
