"""Request-local genuine adoption reuse and byte/SQL/input invalidation."""
from copy import deepcopy
import json
import subprocess

import pytest

from shared_platform import agent_prepared_publication_adoption as adoption
from shared_platform import workbench_publication_native as native
from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
from test_agent_prepared_adoption import prepared, _adopt, _verify
from test_agent_prepared_source_successor import continuation,run as continue_source


def _scope(profile):
    from shared_platform.release_store import ReleaseStore
    return adoption.verification_scope(profile,release_store=ReleaseStore(profile.root/'owned-release.db'))


def test_current_reuses_signed_adoption_without_repeating_hmac(prepared,monkeypatch):
    result=_adopt(prepared);engine,profile,facts=prepared
    bound=object.__new__(AgentPreparedCommonService)
    bound.engine=engine;bound.profile=profile;bound.task_id=result['task_id']
    from shared_platform.release_store import ReleaseStore
    bound.store=ReleaseStore(profile.root/'owned-release.db')
    checks=[];original=adoption.hmac.compare_digest
    def compared(*args):checks.append(1);return original(*args)
    monkeypatch.setattr(adoption.hmac,'compare_digest',compared)
    def read(task,actual_profile):
        row=adoption.verify(task,actual_profile,offer='8412287877065586543',targets=['shopee:PH'],
            prepared_reference=facts['prepared_reference'],snapshot_digest=facts['snapshot_digest'])
        return {'offer_id':row['offer_id'],'targets':row['targets'],
            'prepared_reference':facts['prepared_reference'],'snapshot_digest':facts['snapshot_digest']}
    # Only the R2 external fixture reader is substituted; adoption HMAC, real
    # SQLite origin rows and immutable source hashing remain production code.
    monkeypatch.setattr(native,'read_frozen',read)
    def images(task,actual_profile):
        read(task,actual_profile)
        return {'native_r2':facts['r2_identity']}
    monkeypatch.setattr(native,'read_images',images)
    assert bound._current()[0]['task_id']==result['task_id']
    assert len(checks)==1


@pytest.mark.parametrize('damage',['source_bytes','task_sql','input','config','expiry'])
def test_reuse_rejects_changed_inputs_instead_of_carrying_old_proof(prepared,monkeypatch,damage):
    result=_adopt(prepared);engine,profile,facts=prepared
    from modules.products import server
    with _scope(profile):
        assert _verify(prepared,result)['offer_id']=='8412287877065586543'
        if damage=='source_bytes':
            (profile.root/'snapshot.json').write_text('{"native":"changed"}')
        elif damage=='task_sql':
            # Any task/release commit invalidates, including a new budget-like
            # mutable table. Mutable state is never satisfied by the memo.
            with engine.transaction() as db:db.execute('CREATE TABLE synthetic_budget_change (used INTEGER)')
        elif damage=='input':
            facts['snapshot_digest']='sha256:'+'c'*64
        elif damage=='config':
            monkeypatch.setattr(server,'R3_STARTUP_CONFIG',object())
        else:
            now=adoption.time.monotonic()
            monkeypatch.setattr(adoption.time,'monotonic',lambda:now+11)
        with pytest.raises(adoption.AdoptionBlocked,match='ADOPTION_OPERATION_'):_verify(prepared,result)


def test_no_reuse_crosses_operations_and_returned_dict_is_not_the_cached_proof(prepared):
    result=_adopt(prepared);_,profile,_=prepared
    with _scope(profile) as counts:
        first=_verify(prepared,result);first['owner_sid']='caller'
        assert _verify(prepared,result)['owner_sid']!='caller'
        assert counts['full_verifications']==1 and counts['proof_reuses']==1
    with _scope(profile) as second:
        assert _verify(prepared,result)['owner_sid']!='caller'
        assert second['full_verifications']==1 and second['proof_reuses']==0


@pytest.mark.parametrize('mutable_fact',['approval','budget','target'])
def test_release_writes_are_never_satisfied_by_an_old_adoption_proof(prepared,monkeypatch,mutable_fact):
    from shared_platform import release_store
    import sqlite3
    result=_adopt(prepared);_,profile,_=prepared
    store=release_store.ReleaseStore(profile.root/'owned-release.db')
    with sqlite3.connect(store.path) as db:db.execute('CREATE TABLE observed_state (kind TEXT)')
    monkeypatch.setattr(release_store,'default_release_store',lambda:pytest.fail('Default store must not be consulted'))
    with adoption.verification_scope(profile,release_store=store):
        _verify(prepared,result)
        with sqlite3.connect(store.path) as db:db.execute('INSERT INTO observed_state VALUES (?)',(mutable_fact,))
        with pytest.raises(adoption.AdoptionBlocked,match='ADOPTION_OPERATION_DATABASE_CHANGED'):_verify(prepared,result)


def test_config_file_bytes_invalidate_even_with_same_config_object(prepared,monkeypatch):
    from modules.products import server
    from shared_platform.publication_runtime_config import capture_startup_config
    result=_adopt(prepared);_,profile,_=prepared
    (profile.root/'config').mkdir()
    path=profile.root/'config/miaoshou.local.json'
    path.write_text('{"app_id":"closed","app_secret":"closed"}')
    monkeypatch.setattr(server,'R3_STARTUP_CONFIG',capture_startup_config(root=profile.root,environ={}))
    with _scope(profile):
        _verify(prepared,result)
        path.write_text('{"app_id":"changed","app_secret":"closed"}')
        with pytest.raises(adoption.AdoptionBlocked,match='ADOPTION_OPERATION_SOURCE_CHANGED'):_verify(prepared,result)


@pytest.mark.parametrize('damage',['runtime_bytes','head'])
def test_runtime_bytes_and_git_head_invalidate_immutable_reuse(prepared,damage):
    engine,profile,facts=prepared
    def git(*args):
        return subprocess.check_output(['git','-C',str(profile.root),'-c','user.name=Owned Fixture',
            '-c','user.email=fixture@invalid.local',*args],stderr=subprocess.STDOUT)
    git('init','-q');(profile.root/'shared_platform').mkdir()
    source=profile.root/'shared_platform/owned.py';source.write_text('VALUE=1\n')
    git('add','shared_platform/owned.py');git('commit','-qm','owned fixture')
    from shared_platform.operations_runtime import runtime_manifest
    profile.version=git('rev-parse','HEAD').decode().strip()
    profile.manifest_digest=runtime_manifest(profile.root)
    engine.release.update(code_version=profile.version,manifest_digest=profile.manifest_digest)
    facts['execution_source_head']=profile.version
    result=_adopt(prepared)
    with _scope(profile):
        _verify(prepared,result)
        if damage=='runtime_bytes':source.write_text('VALUE=2\n')
        else:git('commit','--allow-empty','-qm','changed owned head')
        with pytest.raises(adoption.AdoptionBlocked,match='ADOPTION_OPERATION_RUNTIME_CHANGED'):_verify(prepared,result)


def test_no_explicit_release_store_keeps_full_original_verification(prepared,monkeypatch):
    result=_adopt(prepared);_,profile,_=prepared
    checks=[];original=adoption.hmac.compare_digest
    monkeypatch.setattr(adoption.hmac,'compare_digest',lambda *a:(checks.append(1),original(*a))[1])
    with adoption.verification_scope(profile) as counts:
        _verify(prepared,result);_verify(prepared,result)
    assert len(checks)==2 and counts['cache_disabled_reason']=='EXPLICIT_STORE_REQUIRED'


def test_same_source_cache_does_not_follow_arbitrary_capture_references(prepared,monkeypatch):
    from pathlib import Path
    from modules.products import server
    monkeypatch.setattr(server,'R3_STARTUP_CONFIG',None)
    _,profile,facts=prepared
    capture=profile.root/'unrelated-history-capture.json';capture.write_text('{}')
    snapshot=profile.root/'snapshot.json'
    snapshot.write_text(json.dumps({'path':str(capture),'sha256':adoption._sha(capture)}))
    facts['source_files']['snapshot.json']=adoption._sha(snapshot)
    result=_adopt(prepared)
    original=Path.read_bytes
    def read(path):
        if path==capture:pytest.fail('Arbitrary capture is not a verifier dependency')
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',read)
    with _scope(profile) as counts:
        _verify(prepared,result);_verify(prepared,result)
        assert counts['pinned_file_count']==2
        assert counts['pinned_file_bytes']==snapshot.stat().st_size+2


def test_original_profile_bytes_are_a_typed_immutable_dependency(prepared):
    result=_adopt(prepared);_,profile,_=prepared
    with _scope(profile):
        _verify(prepared,result)
        (profile.root/'original-profile.json').write_text('{"changed":true}')
        with pytest.raises(adoption.AdoptionBlocked,match='ADOPTION_OPERATION_SOURCE_CHANGED'):_verify(prepared,result)


def test_storeless_nested_scope_does_not_inherit_outer_authority(prepared,monkeypatch):
    result=_adopt(prepared);_,profile,_=prepared
    checks=[];original=adoption.hmac.compare_digest
    monkeypatch.setattr(adoption.hmac,'compare_digest',lambda *a:(checks.append(1),original(*a))[1])
    with _scope(profile):
        _verify(prepared,result)
        with adoption.verification_scope(profile):
            _verify(prepared,result);_verify(prepared,result)
    assert len(checks)==3


def test_historical_signed_successor_always_keeps_its_full_original_verifier(continuation):
    engine,profile,store,result,original=continuation
    continue_source(continuation)
    task=engine.get(result['task_id'])
    with adoption.verification_scope(profile,release_store=store) as counts:
        for unused in range(2):
            record=adoption.verify(task,profile,offer=original['offer_id'],targets=original['targets'],
                prepared_reference=original['facts']['prepared_reference'],snapshot_digest=original['facts']['snapshot_digest'])
            assert record==original
        assert counts['full_verifications']==2 and counts['proof_reuses']==0
        assert counts['cache_disabled_reason']=='HISTORICAL_SUCCESSOR_REQUIRES_FULL_VERIFICATION'


@pytest.mark.parametrize('filename',['snapshot.json','original-profile.json'])
def test_changed_source_between_verification_and_cache_capture_is_rejected(prepared,monkeypatch,filename):
    result=_adopt(prepared);_,profile,_=prepared
    original=adoption._verify_uncached
    def raced(*args,**kwargs):
        record=original(*args,**kwargs)
        (profile.root/filename).write_text('{"raced":true}')
        return record
    monkeypatch.setattr(adoption,'_verify_uncached',raced)
    with _scope(profile):
        with pytest.raises(adoption.AdoptionBlocked,match='ADOPTION_OPERATION_SOURCE_CHANGED'):_verify(prepared,result)
