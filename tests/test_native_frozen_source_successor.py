"""Normal original R1/R2/paid lineage survives one pre-COMMON source update."""
from dataclasses import replace
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from modules.products import server
from shared_platform import operations_publication_common as common
from shared_platform import workbench_publication_native as native
from shared_platform.operations_runtime import RuntimeProfile,runtime_manifest
from shared_platform.workbench_engine import WorkbenchEngine
from test_round1_workspace_freeze import live
from test_round1_auto_freeze import public_settings


def _ready(live,monkeypatch):
    from test_common_native_preparation_source import _native_common_chain
    old_create=WorkbenchEngine.create
    def create(engine,payload):
        if payload.get('source_key','').startswith('native-source-'):
            return engine.create_for_explicit_post(payload)[0]
        return old_create(engine,payload)
    monkeypatch.setattr(WorkbenchEngine,'create',create)
    engine,task,profile,frozen=_native_common_chain(live,monkeypatch,active_release_probe=lambda *_:None)
    token=None
    with engine.readonly_snapshot() as db:
        token=engine._row(db,task['task_id'])['lease_token']
    engine.yield_prepared_lease(task['task_id'],token)
    binding=profile.root/'config/product_publication_paid_history.local.json'
    binding.write_text(json.dumps({'schema_version':'product-paid-history-roots/v1',
        'service_root':str(profile.root),'paid_history_root':str(profile.root)}),encoding='utf-8')
    (profile.root/'.git/info').mkdir(exist_ok=True)
    (profile.root/'.git/info/exclude').write_text('*\n',encoding='utf-8')
    return engine,engine.get(task['task_id']),profile,frozen


def _new_source(profile):
    path=profile.root/'shared_platform/owned_later_consumer.py'
    path.write_text('"""Owned isolated next consumer source; no business behavior."""\n',encoding='utf-8')
    def git(*args):return subprocess.check_output(['git','-C',str(profile.root),'-c',
        'user.name=Owned source successor','-c','user.email=owned@example.invalid',*args],text=True).strip()
    git('add','-f','--',str(path.relative_to(profile.root)))
    git('commit','-m','Owned compatible consumer source')
    current=replace(profile,version=git('rev-parse','HEAD'),manifest_digest=runtime_manifest(profile.root))
    release={'code_version':current.version,'environment':current.environment,'manifest_digest':current.manifest_digest}
    return current,WorkbenchEngine(current.data_root/'tasks.db',release)


def test_real_normal_origin_frozen_and_common_consumers_accept_one_sealed_source(live,monkeypatch):
    engine,task,profile,frozen=_ready(live,monkeypatch)
    from shared_platform import native_frozen_source_successor as successor
    observed=successor.observe(engine,task['task_id'],profile=profile,release_store=live['store'])
    original_task_key=next(e for e in engine.store.events(task['task_id']) if e['event_type']=='explicit_new_post_preparation')
    next_profile,next_engine=_new_source(profile)
    event=successor.register(next_engine,task['task_id'],profile=next_profile,
        release_store=live['store'],expected=observed)
    assert event['execution_authority'] is False
    assert next_engine.explicit_new_post_task_ids()==(task['task_id'],)
    current=next_engine.get(task['task_id'])
    assert current['version']==next_engine.release and current['scope']==task['scope']
    assert next(e for e in engine.store.events(task['task_id']) if e['event_type']=='explicit_new_post_preparation')==original_task_key
    assert native.read_frozen(current,next_profile)==frozen
    images=native.read_images(current,next_profile)
    assert images==observed['predecessors']['images']
    operations=server._NATIVE_FINAL_SERVICE._operations
    monkeypatch.setattr(operations,'engine',next_engine);monkeypatch.setattr(operations,'profile',next_profile)
    documents=native.load_service_r2_documents(live['offer'],operations)
    from shared_platform.publication_r3_image_bridge import validate_r2_identity
    assert validate_r2_identity(documents)==images['native_r2']
    code,preview=server._preview_r3_common_stage({'offer_id':live['offer'],
        'release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON']})
    assert code==200 and preview['ok'] is True
    binding=common._verified_binding(current,preview['common'],next_profile)
    assert binding['preparation_source']['prepared_reference']==frozen['prepared_reference']
    with pytest.raises(ValueError,match='ALREADY_REGISTERED'):
        successor.register(next_engine,task['task_id'],profile=next_profile,
            release_store=live['store'],expected=observed)
    # Issuance is pre-COMMON, not a prohibition on legitimate later progress.
    next_engine.register_executor('owned-current-consumer',['publication'],next_engine.release)
    token=next_engine.claim(task['task_id'],'owned-current-consumer')['lease_token']
    next_engine.record_checkpoint(task['task_id'],token,
        {**current['checkpoint'],'owned_later_observation':{'execution_authority':False}})
    assert next_engine.explicit_new_post_task_ids()==(task['task_id'],)
    assert native.read_images(next_engine.get(task['task_id']),next_profile)==images


def test_registration_rejects_active_incomplete_unknown_and_existing_operation(live,monkeypatch):
    engine,task,profile,_=_ready(live,monkeypatch)
    from shared_platform import native_frozen_source_successor as successor
    def observe():return successor.observe(engine,task['task_id'],profile=profile,release_store=live['store'])
    original=observe()
    with engine.transaction() as db:
        db.execute('UPDATE workbench_execution SET worker=? WHERE task_id=?',('still-active',task['task_id']))
    with pytest.raises(ValueError,match='IDLE_POST_R2_TASK_REQUIRED'):observe()
    with engine.transaction() as db:
        incomplete=json.loads(original['steps_raw']);incomplete[1]['state']='pending'
        db.execute('UPDATE workbench_execution SET worker=NULL,steps_json=? WHERE task_id=?',
                   (json.dumps(incomplete),task['task_id']))
    with pytest.raises(ValueError,match='COMPLETE_FROZEN_PREDECESSORS_REQUIRED'):observe()
    with engine.transaction() as db:
        db.execute('UPDATE workbench_execution SET steps_json=? WHERE task_id=?',(original['steps_raw'],task['task_id']))
    from shared_platform.publication_paid_requests import PaidRequestContext
    from shared_platform.publication_autopilot import load_autopilot_policy
    from shared_platform import publication_r3_image_bridge as bridge
    documents=bridge.load_r2_documents(live['offer'])
    context=PaidRequestContext(offer_id=live['offer'],round1=documents['round1_snapshot'],
        policy=load_autopilot_policy(profile.root/'config/product_publication_autopilot_policy.json'),
        reports_root=profile.root/'reports/product-preparation')
    _,entries=context._load();entry=next(iter(entries.values()))
    journal=context.path.read_bytes()
    context.reserve(purpose=entry['purpose'],model=entry['model'],business={'owned_unresolved':True},request={'fixture':True})
    with pytest.raises(ValueError,match='PAID_OUTCOME_UNRESOLVED'):observe()
    # Restore only this negative fixture; no actual attempt or provider call.
    context.path.write_bytes(journal)
    assert observe()==original
    assert engine.begin_domain_operation('owned-no-dispatch-reservation',skus=task['scope']['skus'],
        shops=task['scope']['shops'],owner_task_id=task['task_id'])['acquired']
    with pytest.raises(ValueError,match='TASK_EXTERNAL_OR_REVIEW_AUTHORITY_EXISTS'):observe()


def test_real_consumers_reject_origin_scope_paid_document_and_media_tamper(live,monkeypatch):
    engine,task,profile,_=_ready(live,monkeypatch)
    from shared_platform import native_frozen_source_successor as successor
    observed=successor.observe(engine,task['task_id'],profile=profile,release_store=live['store'])
    next_profile,next_engine=_new_source(profile)
    bad=deepcopy(observed);bad['scope']['shops']=['ozon:RU']
    with pytest.raises(ValueError,match='EXPECTED_FROZEN_OBSERVATION_CHANGED'):
        successor.register(next_engine,task['task_id'],profile=next_profile,release_store=live['store'],expected=bad)
    successor.register(next_engine,task['task_id'],profile=next_profile,release_store=live['store'],expected=observed)
    current=next_engine.get(task['task_id'])
    paid=Path(observed['predecessors']['paid']['directory'])/'events.jsonl'
    document=next(Path(f['path']) for f in observed['predecessors']['files']
                  if Path(f['path']).name=='round1-approved-snapshot.json')
    media_path=Path(observed['predecessors']['media_paths'][0])
    for path,reader in ((paid,native.read_frozen),(document,native.read_frozen),(media_path,native.read_images)):
        raw=path.read_bytes();path.write_bytes(raw+b'\nowned-tamper')
        try:
            with pytest.raises(ValueError):reader(current,next_profile)
        finally:path.write_bytes(raw)
    assert native.read_images(current,next_profile)==observed['predecessors']['images']
    with next_engine.transaction() as db:
        original_scope=db.execute('SELECT scope_json FROM workbench_execution WHERE task_id=?',(task['task_id'],)).fetchone()[0]
        changed=json.loads(original_scope);changed['shops']=['ozon:RU']
        db.execute('UPDATE workbench_execution SET scope_json=? WHERE task_id=?',(json.dumps(changed),task['task_id']))
    with pytest.raises(ValueError,match='ORIGINAL_REQUEST_CHANGED'):next_engine.explicit_new_post_task_ids()
    with next_engine.transaction() as db:
        db.execute('UPDATE workbench_execution SET scope_json=? WHERE task_id=?',(original_scope,task['task_id']))
        grant=db.execute("SELECT id,detail_json FROM workbench_events WHERE task_id=? AND event_type='explicit_new_post_preparation'",(task['task_id'],)).fetchone()
        bad=json.loads(grant['detail_json']);bad['source_key']='copied-post'
        db.execute('UPDATE workbench_events SET detail_json=? WHERE id=?',(json.dumps(bad),grant['id']))
    with pytest.raises(ValueError,match='ORIGINAL_REQUEST_CHANGED'):next_engine.explicit_new_post_task_ids()


def test_foreign_product_writer_is_rejected_before_source_registration(live,monkeypatch):
    engine,task,profile,_=_ready(live,monkeypatch)
    from shared_platform import native_frozen_source_successor as successor
    assert engine.begin_domain_operation('owned-other-writer-reservation',skus=task['scope']['skus'],
        shops=task['scope']['shops'],owner_task_id=None)['acquired']
    with pytest.raises(ValueError,match='COMPETING_PRODUCT_WRITER'):
        successor.observe(engine,task['task_id'],profile=profile,release_store=live['store'])
