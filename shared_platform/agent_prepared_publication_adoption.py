"""Honest, signed adoption of an already Agent-prepared product into a new task.

This is a new origin at the actual adoption time. It neither backdates R1 nor
creates a human approval. The old task-origin validator remains unchanged for
every task without this independently re-read, owner-signed source record.
"""
from hashlib import sha256
import hmac,json,os,sqlite3,subprocess
from pathlib import Path
from datetime import datetime,timezone

SCHEMA='agent-prepared-publication-adoption/v1'
TABLE="""CREATE TABLE IF NOT EXISTS agent_prepared_publication_adoptions (
 task_id TEXT PRIMARY KEY, offer_id TEXT NOT NULL UNIQUE, record_json TEXT NOT NULL,
 record_digest TEXT NOT NULL, signature TEXT NOT NULL)"""
TRIGGERS=tuple('CREATE TRIGGER IF NOT EXISTS agent_prepared_adoption_no_'+action.lower()+
 ' BEFORE '+action+' ON agent_prepared_publication_adoptions BEGIN SELECT RAISE(ABORT,\'immutable adoption origin\'); END'
 for action in ('UPDATE','DELETE'))
class AdoptionBlocked(ValueError):pass
def _bytes(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def _sha(path):return sha256(Path(path).read_bytes()).hexdigest()
def _read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def _fail(code):raise AdoptionBlocked(code)
def _owner_key(profile,*,initialize=False):
    from shared_platform import local_operator_session as owner
    root=Path(profile.data_root)/'native-adoption-owner';key=root/'signing.key'
    sid=owner.current_windows_owner_sid()
    if initialize and not root.exists():owner.create_owner_only_directory(root)
    owner.verify_owner_only(root,sid)
    if initialize and not key.exists():
        with key.open('xb') as f:f.write(os.urandom(32));f.flush();os.fsync(f.fileno())
    owner.verify_owner_only(key,sid,protected=False)
    raw=key.read_bytes()
    if len(raw)!=32:_fail('ADOPTION_OWNER_KEY_INVALID')
    return sid,raw

def initialize_schema(engine,profile):
    """Explicit maintenance, on the new task ledger and private owner directory."""
    _owner_key(profile,initialize=True)
    with engine.transaction() as db:
        db.execute(TABLE)
        for sql in TRIGGERS:db.execute(sql)


def _verify_paid_history(context):
    paid=context.summary()
    successor_proof=None
    if paid['unknown']:
        from shared_platform.publication_image_successor import verify_successor_adoption
        try:successor_proof=verify_successor_adoption(context)
        except (ValueError,OSError,KeyError):_fail('ADOPTION_PAID_HISTORY_RECONCILIATION_REQUIRED')
    if paid['occupied']>paid['cap']:_fail('ADOPTION_PAID_HISTORY_RECONCILIATION_REQUIRED')
    return paid,successor_proof


def _verify_native_auto_r1(prepared,state,snapshot,directory):
    """Read the original policy decision and prove its native state CAS.

    A preparation's human operator remains its provenance; it is not the actor
    of a subsequent policy decision. Neither that actor mismatch nor a plain
    Product Center approved flag can stand in for this complete proof.
    """
    from shared_platform import publication_rounds as rounds,round1_workspace
    from shared_platform.immutable_approval_files import require_local_path
    decision_path=directory/'round1-auto-decision.json'
    require_local_path(decision_path,root=directory)
    decision_bytes=decision_path.read_bytes()
    decision=rounds.validate_round1_auto_decision(json.loads(decision_bytes),prepared['packet'])
    scope=prepared['scope'];before=prepared['state_before']
    root=round1_workspace._check_preparation_root(prepared)
    if (root.get('status')!='PERSISTED_NATIVE_PREPARATION_ROOT'
        or scope['offer_id']!=snapshot['offer_id']
        or root.get('offer_id')!=snapshot['offer_id']
        or scope['product_center_revision']!=before['_revision']
        or prepared['packet'].get('product_center_revision')!=before['_revision']
        or snapshot.get('reviewed_product_center_revision')!=before['_revision']
        or snapshot.get('approved_product_center_revision')!=state['_revision']
        or snapshot.get('approved_by')!=rounds.AUTOPILOT_ACTOR
        or snapshot.get('approved_at')!=decision['decided_at']
        or snapshot.get('decision_receipt_digest')!=decision['decision_digest']
        or snapshot.get('first_review_digest')!=decision['first_review_digest']
        or snapshot.get('product_approval_fingerprint')!=prepared['approval_fingerprint']
        or round1_workspace._auto_same_state(prepared,state,decision)!='APPROVED'):
        _fail('ADOPTION_CURRENT_R1_CAS_CHANGED')
    return decision_path,sha256(decision_bytes).hexdigest()


def _verify_native_root_binding(prepared,source_root,binding_root):
    from shared_platform import round1_workspace
    native=round1_workspace._check_preparation_root(prepared)
    public=round1_workspace.preparation_root_facts(prepared)
    if source_root!=native or binding_root not in (native,public):
        _fail('ADOPTION_EXTERNAL_SOURCE_BINDING_CHANGED')


def _verify_business_history(store,offer_id,*,allow_uninitialized=False):
    """Only pre-maintenance source inspection may report an absent schema.

    Absence is not a verified empty history. The default adoption path must
    read the installed business ledger again after explicit schema maintenance.
    """
    required={'release_plans','release_runs','release_approvals'}
    with store._connect_readonly() as db:
        present=required & {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not present:
            if not allow_uninitialized:_fail('ADOPTION_BUSINESS_HISTORY_SCHEMA_REQUIRED')
            return {'status':'UNINITIALIZED_BUSINESS_SCHEMA_REQUIRES_MAINTENANCE',
                'empty_business_history_verified':False}
        if present!=required:_fail('ADOPTION_BUSINESS_HISTORY_SCHEMA_INCOMPLETE')
        if db.execute('SELECT 1 FROM release_plans p JOIN release_runs r USING(plan_id) WHERE p.product_id=? LIMIT 1',(offer_id,)).fetchone():_fail('ADOPTION_EXISTING_BUSINESS_RUN_REQUIRES_RECONCILIATION')
        if db.execute('SELECT 1 FROM release_approvals a JOIN release_plans p USING(plan_id) WHERE p.product_id=? LIMIT 1',(offer_id,)).fetchone():_fail('ADOPTION_EXISTING_HUMAN_DECISION_FORBIDDEN')
    return {'status':'INITIALIZED_NO_LOCAL_BUSINESS_RUN_OR_APPROVAL',
        'empty_business_history_verified':True}


def _verify_sources(profile,offer_id,expected_targets,*,allow_uninitialized_release_schema=False):
    from modules.sourcing import new_product_workbench as workbench
    from shared_platform import publication_rounds as rounds,round1_workspace,publication_r3_image_bridge as bridge
    from shared_platform.release_store import default_release_store
    from shared_platform.native_common_budget_facts import read_preparation_source
    from shared_platform.publication_paid_requests import load_paid_context
    from shared_platform.immutable_approval_files import require_local_path
    root=Path(profile.root);directory=root/'reports/product-preparation'/offer_id
    state=workbench.load_state(offer_id,state_dir=root/'data/new_product_workbench')
    snapshot=rounds.validate_round2_input(offer_id,state,reports_root=root/'reports/product-preparation')
    approval=state.get('product_approval') or {}
    if (snapshot.get('human_approval') is not False or snapshot.get('approval_authority')!='ACTIVE_AUTOPILOT_POLICY'
        or not snapshot.get('decision_receipt_digest') or not approval.get('round1_prepared_reference')
        or sorted(snapshot.get('canonical_targets') or [])!=sorted(expected_targets)):_fail('ADOPTION_NATIVE_AUTO_R1_REQUIRED')
    prepared=round1_workspace.read_preparation(offer_id,approval['round1_prepared_reference'])
    decision_path,decision_sha256=_verify_native_auto_r1(prepared,state,snapshot,directory)
    # Read actual immutable files; the native preparation packet, not the mutable
    # incomplete sidecar, supplies the auto-prepared first-review facts.
    documents={k:_read(directory/name) for k,name in bridge.R2_DOCUMENTS.items()}
    documents['first_review']=prepared['packet']
    identity=bridge.validate_r2_identity(documents)
    source=read_preparation_source(default_release_store(),documents,state).payload()
    if source['preparation_root'].get('status')!='PERSISTED_NATIVE_PREPARATION_ROOT':_fail('ADOPTION_NATIVE_ROOT_REQUIRED')
    binding_path=directory/'SOURCE_AND_IDENTITY_BINDING.json';require_local_path(binding_path,root=directory)
    binding=_read(binding_path)
    identity_meta=state.get('new_b_identity') or {}
    aliases=identity_meta.get('canonical_to_B_provider_sku') or {}
    if (len(aliases)!=3 or len(set(aliases.values()))!=3
        or sorted(aliases)!=['0431','0432','0433']
        or sorted(aliases.values())!=sorted(x['seller_sku'] for x in snapshot['fact_snapshot']['product_facts']['skus'])
        or not expected_targets or len(set(expected_targets))!=len(expected_targets)):_fail('ADOPTION_EXACT_FROZEN_VARIANT_TARGETS_REQUIRED')
    # This narrow reader is also consumed by read_frozen; no suffix-based global
    # SKU parser is changed by this origin.
    from shared_platform.publication_variant_identity import read_explicit_variant_aliases
    variant_identity=read_explicit_variant_aliases(offer_id=offer_id,round1_snapshot=snapshot,state=state,
        report_directory=directory,expected_binding_sha256=_sha(binding_path))
    intake=root/'data/product_intake'/offer_id/'record.json';require_local_path(intake,root=root/'data/product_intake')
    record=_read(intake)
    if record.get('offer_id')!=offer_id or record.get('source_mode')!='manual_intake' or len(record.get('images') or [])!=3:_fail('ADOPTION_MANUAL_INTAKE_REQUIRED')
    _verify_native_root_binding(prepared,source['preparation_root'],binding.get('native_preparation_root'))
    if (binding.get('native_prepared_reference')!=approval['round1_prepared_reference']
        or binding.get('canonical_targets')!=snapshot['canonical_targets']
        or (binding.get('manual_intake_record') or {}).get('sha256')!=_sha(intake)
        or Path((binding.get('manual_intake_record') or {}).get('path','')).resolve()!=intake.resolve()):_fail('ADOPTION_EXTERNAL_SOURCE_BINDING_CHANGED')
    profile_ref=binding.get('profile') or {};old_profile_path=Path(profile_ref.get('path',''))
    require_local_path(old_profile_path,root=old_profile_path.parent)
    if not old_profile_path.is_absolute() or _sha(old_profile_path)!=profile_ref.get('sha256'):_fail('ADOPTION_ORIGINAL_PROFILE_CHANGED')
    old_profile=_read(old_profile_path)
    pins={str(intake.relative_to(root)):_sha(intake),str(binding_path.relative_to(root)):_sha(binding_path),
        str(decision_path.relative_to(root)):decision_sha256}
    for image in record['images']:
        path=intake.parent/'images'/image['filename'];require_local_path(path,root=intake.parent)
        if _sha(path)!=image['sha256']:_fail('ADOPTION_INTAKE_IMAGE_CHANGED')
        pins[str(path.relative_to(root))]=_sha(path)
    for name in bridge.R2_DOCUMENTS.values():
        path=directory/name;require_local_path(path,root=directory);pins[str(path.relative_to(root))]=_sha(path)
    paid_path=directory/'paid-requests/events.jsonl'
    if not paid_path.is_file() or not paid_path.stat().st_size:_fail('ADOPTION_PAID_HISTORY_REQUIRED')
    context=load_paid_context(offer_id=offer_id,round1=snapshot,repo_root=root)
    paid,successor_proof=_verify_paid_history(context)
    if successor_proof is not None:
        # This image-only proof retains every original UNKNOWN and occupied slot.
        # No merchant journal or unresolved business action is covered by it.
        pins.update(successor_proof['source_files'])
    pins[str(paid_path.relative_to(root))]=_sha(paid_path)
    # The producer head is external companion evidence, never inserted into or
    # reconstructed inside the original native R1 snapshot schema.
    r1_head=(binding.get('source') or {}).get('head') or binding.get('source_head')
    if not r1_head:_fail('ADOPTION_R1_SOURCE_COMMIT_REQUIRED')
    if old_profile.get('expected_source_head')!=r1_head or Path(old_profile.get('source_root','')).resolve()!=root.resolve():_fail('ADOPTION_ORIGINAL_PROFILE_BINDING_CHANGED')
    if subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',r1_head,profile.version],capture_output=True).returncode!=0:_fail('ADOPTION_R1_SOURCE_NOT_ANCESTOR')
    # Refuse an already-started business history instead of using a new task to
    # reset the create/edit limits or silently adopt an old marketplace decision.
    history=_verify_business_history(default_release_store(),offer_id,
        allow_uninitialized=allow_uninitialized_release_schema)
    return dict(prepared_reference=approval['round1_prepared_reference'],snapshot_digest=snapshot['snapshot_digest'],
      r2_identity=identity,preparation_source=source,source_identity_binding_sha256=_sha(binding_path),
      source_files=pins,source_head_at_R1=r1_head,execution_source_head=profile.version,paid_summary=paid,
      business_history=history,
      original_profile=dict(path=str(old_profile_path),sha256=profile_ref['sha256']),
      aliases=aliases,internal_sku=variant_identity['internal_sku'],seller_sku=variant_identity['seller_sku'],
      **({'image_unknown_successor_proof':successor_proof} if successor_proof is not None else {}))

def adopt(engine,profile,*,offer_id,targets,source_key,actor_reader):
    """Trusted local producer. No HTTP input can supply a receipt or actor."""
    from shared_platform.r3_native_final_approval import read_native_actor_identity
    actor=read_native_actor_identity(actor_reader);sid,key=_owner_key(profile)
    if actor.get('identity_verified') is not True or actor.get('trust_mode')!='SAME_WINDOWS_USER' or actor.get('owner_sid')!=sid:_fail('ADOPTION_TRUSTED_SAME_USER_REQUIRED')
    facts=_verify_sources(profile,offer_id,targets)
    with sqlite3.connect((Path(profile.data_root)/'tasks.db').resolve().as_uri()+'?mode=ro',uri=True) as existing:
        existing.row_factory=sqlite3.Row
        old=existing.execute('SELECT task_id FROM agent_prepared_publication_adoptions WHERE offer_id=?',(offer_id,)).fetchone()
    if old:
        task=engine.get(old['task_id'])
        original=verify(task,profile,offer=offer_id,targets=sorted(targets),prepared_reference=facts['prepared_reference'],snapshot_digest=facts['snapshot_digest'])
        return dict(task_id=task['task_id'],adoption_digest=sha256(_bytes(original)).hexdigest(),
            adopted_at=original['adopted_at'],origin=original['origin'],external_write_count=0,marketplace_approval=False)
    task=engine.create(dict(template='publication',source_key=source_key,title='冰箱防尘盖布带侧袋 · 新B',
      scope=dict(offer_id=offer_id,shops=sorted(targets),skus=sorted(facts['aliases']))))
    with engine.transaction() as db:
        old=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(task['task_id'],)).fetchone()
        if old:return verify(task,profile,offer=offer_id,targets=sorted(targets),prepared_reference=facts['prepared_reference'],snapshot_digest=facts['snapshot_digest'])
        event=db.execute("SELECT id,created_at FROM workbench_events WHERE task_id=? AND event_type='workflow_created' ORDER BY id LIMIT 1",(task['task_id'],)).fetchone()
        record=dict(schema_version=SCHEMA,origin='AGENT_PREPARED_ADOPTION',task_id=task['task_id'],offer_id=offer_id,
          targets=sorted(targets),release=engine.release,adopted_at=datetime.now(timezone.utc).isoformat(),
          task_created_event_id=event['id'],task_created_at=event['created_at'],owner_sid=sid,
          actor_session_digest=sha256(_bytes(actor)).hexdigest(),facts=facts,marketplace_approval=False)
        raw=_bytes(record);signature=hmac.new(key,raw,'sha256').hexdigest()
        db.execute('INSERT INTO agent_prepared_publication_adoptions VALUES (?,?,?,?,?)',(task['task_id'],offer_id,raw.decode(),sha256(raw).hexdigest(),signature))
    worker='agent-prepared-adoption';engine.register_executor(worker,['publication'],engine.release)
    lease=engine.claim(task['task_id'],worker,ttl=300)['lease_token']
    adoption=dict(record_digest=sha256(raw).hexdigest(),source_identity_binding_sha256=facts['source_identity_binding_sha256'])
    frozen=dict(offer_id=offer_id,targets=sorted(targets),snapshot_digest=facts['snapshot_digest'],prepared_reference=facts['prepared_reference'],internal_sku=facts['internal_sku'],seller_sku=facts['seller_sku'])
    checkpoint=dict(native_r1_adoption=adoption,native_preparation=dict(prepared_reference=facts['prepared_reference']),native_r1=frozen)
    engine.record_checkpoint(task['task_id'],lease,checkpoint)
    engine.complete_step(task['task_id'],lease,expected_step='facts',checkpoint=checkpoint)
    checkpoint['native_r2']=facts['r2_identity']
    engine.complete_step(task['task_id'],lease,expected_step='images',checkpoint=checkpoint)
    engine.yield_prepared_lease(task['task_id'],lease)
    return dict(task_id=task['task_id'],adoption_digest=sha256(raw).hexdigest(),adopted_at=record['adopted_at'],origin=record['origin'],external_write_count=0,marketplace_approval=False)

def verify(task,profile,*,offer,targets,prepared_reference,snapshot_digest):
    sid,key=_owner_key(profile)
    path=Path(profile.data_root)/'tasks.db'
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        row=db.execute('SELECT * FROM agent_prepared_publication_adoptions WHERE task_id=?',(task['task_id'],)).fetchone()
        if row is None:_fail('ADOPTION_SIGNED_ORIGIN_MISSING')
        record=json.loads(row['record_json']);raw=_bytes(record)
        if (sha256(raw).hexdigest()!=row['record_digest'] or not hmac.compare_digest(hmac.new(key,raw,'sha256').hexdigest(),row['signature'])
            or record['owner_sid']!=sid or record['offer_id']!=offer or record['targets']!=sorted(targets)
            or record['facts']['prepared_reference']!=prepared_reference
            or record['facts']['snapshot_digest']!=snapshot_digest):_fail('ADOPTION_SIGNED_ORIGIN_CHANGED')
        if record['release']!=task.get('version'):
            from shared_platform.agent_prepared_source_successor import verify_successor
            verify_successor(task,profile,record,connection=db,owner_sid=sid,key=key)
        current=db.execute('SELECT scope_json,checkpoint_json FROM workbench_execution WHERE task_id=?',(task['task_id'],)).fetchone()
        if json.loads(current['scope_json'])!=task['scope'] or json.loads(current['checkpoint_json'])!=task.get('checkpoint'):_fail('ADOPTION_TASK_CHANGED')
        origin=db.execute("SELECT created_at FROM workbench_events WHERE id=? AND task_id=? AND event_type='workflow_created'",(record['task_created_event_id'],task['task_id'])).fetchone()
        if origin is None or origin['created_at']!=record['task_created_at']:_fail('ADOPTION_GENUINE_TASK_EVENT_REQUIRED')
    pin=(task.get('checkpoint') or {}).get('native_r1_adoption') or {}
    if pin!={'record_digest':row['record_digest'],'source_identity_binding_sha256':record['facts']['source_identity_binding_sha256']}:_fail('ADOPTION_TASK_PIN_CHANGED')
    for relative,expected in record['facts']['source_files'].items():
        path=Path(profile.root)/relative
        from shared_platform.immutable_approval_files import require_local_path
        require_local_path(path,root=profile.root)
        if _sha(path)!=expected:_fail('ADOPTION_SOURCE_BYTES_CHANGED')
    original=record['facts']['original_profile']
    if _sha(original['path'])!=original['sha256']:_fail('ADOPTION_ORIGINAL_PROFILE_CHANGED')
    successor=record['facts'].get('image_unknown_successor_proof')
    if successor is not None:
        from shared_platform.publication_paid_requests import load_paid_context
        from shared_platform.publication_rounds import load_round1_snapshot
        from shared_platform.publication_image_successor import verify_successor_adoption
        root=Path(profile.root)
        context=load_paid_context(offer_id=offer,round1=load_round1_snapshot(offer,reports_root=root/'reports/product-preparation'),repo_root=root)
        try:
            if verify_successor_adoption(context)!=successor:_fail('ADOPTION_IMAGE_SUCCESSOR_PROOF_CHANGED')
        except (ValueError,OSError,KeyError):_fail('ADOPTION_IMAGE_SUCCESSOR_PROOF_CHANGED')
    return record
