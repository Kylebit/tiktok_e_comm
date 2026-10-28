from copy import deepcopy
from hashlib import sha256
import json,sqlite3,subprocess
from types import SimpleNamespace
from pathlib import Path
import pytest

from shared_platform import agent_prepared_source_successor as successor
from shared_platform import agent_prepared_publication_adoption as adoption
from shared_platform.release_store import ReleaseStore
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.operations_runtime import runtime_manifest
from test_agent_prepared_adoption import prepared,_adopt


@pytest.fixture
def continuation(prepared,monkeypatch):
    result=_adopt(prepared);old_engine,old_profile,facts=prepared
    profile=SimpleNamespace(root=old_profile.root,data_root=old_profile.data_root,
        environment='stable',version='new-compatibility-head',manifest_digest='new-manifest')
    engine=WorkbenchEngine(profile.data_root/'tasks.db',successor._version(profile))
    ledger=ReleaseStore(profile.root/'release.db')
    with sqlite3.connect(ledger.path) as db:
        db.execute('CREATE TABLE manual_common_creations (intent_id TEXT,intent_digest TEXT,state TEXT,attempts INTEGER,offer_id TEXT)')
        db.execute("INSERT INTO manual_common_creations VALUES ('original-intent','original-digest','RESERVED',0,'8412287877065586543')")
    original=adoption.verify(old_engine.get(result['task_id']),old_profile,offer='8412287877065586543',
        targets=['shopee:PH'],prepared_reference=facts['prepared_reference'],snapshot_digest=facts['snapshot_digest'])
    monkeypatch.setattr(successor,'source_evidence',lambda p,v:{'old_version':v,'new_version':successor._version(p),'changed_files':{'synthetic':'digest'}})
    return engine,profile,ledger,result,original


def run(v):
    engine,profile,ledger,result,_=v
    return successor.continue_source(engine,profile,ledger,task_id=result['task_id'],
        expected_adoption_digest=result['adoption_digest'],actor_reader=object())


def test_source_successor_keeps_one_task_original_adoption_checkpoint_intent_and_signature(continuation):
    engine,profile,ledger,result,original=continuation
    task=engine.get(result['task_id']);checkpoint=deepcopy(task['checkpoint']);intent=ledger.path.read_bytes()
    with engine.transaction() as db:
        original_row=tuple(db.execute('SELECT * FROM agent_prepared_publication_adoptions').fetchone())
    record=run(continuation)
    current=engine.get(task['task_id'])
    assert current['version']==engine.release and current['checkpoint']==checkpoint
    assert ledger.path.read_bytes()==intent and record['original_manual_intent']['attempts']==0
    assert record['marketplace_approval'] is False and record['external_write_count']==0
    assert run(continuation)==record
    with engine.transaction() as db:
        assert tuple(db.execute('SELECT * FROM agent_prepared_publication_adoptions').fetchone())==original_row
        assert db.execute('SELECT COUNT(*) FROM workbench_tasks').fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM workbench_events WHERE event_type='source_code_successor'").fetchone()[0]==1
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE agent_prepared_source_successors SET signature='caller'")


@pytest.mark.parametrize('change',['unknown_create','active_lease','changed_paid','wrong_adoption'])
def test_source_successor_refuses_reset_or_unbound_changes(continuation,monkeypatch,change):
    engine,profile,ledger,result,original=continuation
    if change=='unknown_create':
        with sqlite3.connect(ledger.path) as db:db.execute("UPDATE manual_common_creations SET state='UNKNOWN',attempts=1")
    elif change=='active_lease':
        with engine.transaction() as db:db.execute("UPDATE workbench_execution SET worker='old-owner',lease_token='lease'")
    elif change=='changed_paid':
        facts=deepcopy(original['facts']);facts['paid_summary']['occupied']+=1
        monkeypatch.setattr(adoption,'_verify_sources',lambda *a,**k:facts)
    else:result['adoption_digest']='f'*64
    before=engine.get(result['task_id'])['version']
    with pytest.raises(adoption.AdoptionBlocked):run(continuation)
    assert engine.get(result['task_id'])['version']==before


def test_task_json_version_edit_without_signed_successor_is_rejected(continuation):
    engine,profile,_,result,original=continuation
    with engine.transaction() as db:
        db.execute('UPDATE workbench_execution SET version_json=?',(adoption._bytes(engine.release).decode(),))
    with pytest.raises(adoption.AdoptionBlocked,match='SOURCE_SUCCESSOR_SIGNED_RECORD_REQUIRED'):
        adoption.verify(engine.get(result['task_id']),profile,offer=original['offer_id'],targets=original['targets'],
            prepared_reference=original['facts']['prepared_reference'],snapshot_digest=original['facts']['snapshot_digest'])


def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),'-c','user.name=Orbit Hive Codex','-c','user.email=codex@orbit-hive.local',*args],text=True,stderr=subprocess.DEVNULL).strip()


@pytest.mark.parametrize('extra',['allowed','disallowed'])
def test_actual_git_source_scope_and_manifest_are_required(tmp_path,extra):
    git(tmp_path,'init')
    common=tmp_path/'shared_platform/agent_prepared_common_service.py';common.parent.mkdir();common.write_text('old')
    git(tmp_path,'add','shared_platform/agent_prepared_common_service.py');git(tmp_path,'commit','-m','original')
    old={'code_version':git(tmp_path,'rev-parse','HEAD'),'environment':'stable','manifest_digest':runtime_manifest(tmp_path)}
    common.write_text('official_detailList')
    if extra=='disallowed':(tmp_path/'unrelated.txt').write_text('scope expansion')
    git(tmp_path,'add','.');git(tmp_path,'commit','-m','technical source successor')
    profile=SimpleNamespace(root=tmp_path,version=git(tmp_path,'rev-parse','HEAD'),environment='stable',manifest_digest=runtime_manifest(tmp_path))
    if extra=='allowed':
        proof=successor.source_evidence(profile,old)
        assert proof['new_version']['code_version']==profile.version
        assert set(proof['changed_files'])=={'shared_platform/agent_prepared_common_service.py'}
    else:
        with pytest.raises(adoption.AdoptionBlocked,match='SOURCE_SUCCESSOR_EXACT_TECHNICAL_FILES_REQUIRED'):
            successor.source_evidence(profile,old)
