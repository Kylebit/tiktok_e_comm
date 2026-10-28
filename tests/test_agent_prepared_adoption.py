"""New current-time origin and immutable recovery, synthetic closed sources."""
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import pytest
from shared_platform import agent_prepared_publication_adoption as adoption
from shared_platform.workbench_engine import WorkbenchEngine

@pytest.fixture
def prepared(tmp_path,monkeypatch):
    profile=SimpleNamespace(root=tmp_path,data_root=tmp_path/'operations',environment='stable',version='owned-head')
    engine=WorkbenchEngine(profile.data_root/'tasks.db',{'code_version':profile.version,'environment':'stable','manifest_digest':'owned'})
    sid='S-1-5-21-100';key=b'k'*32
    monkeypatch.setattr(adoption,'_owner_key',lambda *a,**k:(sid,key))
    original=tmp_path/'original-profile.json';original.write_text('{}')
    snapshot=tmp_path/'snapshot.json';snapshot.write_text('{"native":"original"}')
    facts=dict(prepared_reference='r1-prepared:owned',snapshot_digest='sha256:'+'a'*64,
       r2_identity={'round1_snapshot_digest':'sha256:'+'a'*64},preparation_source={},
       source_identity_binding_sha256='b'*64,source_files={'snapshot.json':adoption._sha(snapshot)},
       original_profile={'path':str(original),'sha256':adoption._sha(original)},
       source_head_at_R1='ancestor',execution_source_head=profile.version,
       paid_summary={'unknown':0,'occupied':16,'cap':40},
       aliases={'0431':'2000431','0432':'2000432','0433':'2000433'},internal_sku='0431',seller_sku='2000431')
    monkeypatch.setattr(adoption,'_verify_sources',lambda *a,**k:facts)
    from shared_platform import r3_native_final_approval as actor
    monkeypatch.setattr(actor,'read_native_actor_identity',lambda _:dict(identity_verified=True,trust_mode='SAME_WINDOWS_USER',owner_sid=sid))
    adoption.initialize_schema(engine,profile)
    return engine,profile,facts

def _adopt(v):
    engine,profile,facts=v
    return adoption.adopt(engine,profile,offer_id='8412287877065586543',targets=['shopee:PH'],
       source_key='owned-new-origin',actor_reader=object())

def _verify(v,result):
    engine,profile,facts=v
    return adoption.verify(engine.get(result['task_id']),profile,offer='8412287877065586543',
       targets=['shopee:PH'],prepared_reference=facts['prepared_reference'],snapshot_digest=facts['snapshot_digest'])

def test_adoption_is_new_real_task_origin_and_yields_only_local_preparation(prepared):
    result=_adopt(prepared);engine,_,_=prepared
    task=engine.get(result['task_id'])
    assert task['execution_state']=='queued' and task['current_step']=='release'
    assert task['worker'] is None and [x['state'] for x in task['steps'][:2]]==['completed','completed']
    record=_verify(prepared,result)
    assert record['origin']=='AGENT_PREPARED_ADOPTION' and record['marketplace_approval'] is False
    with engine.transaction() as db:
        events=[dict(x) for x in db.execute('SELECT * FROM workbench_events WHERE task_id=? ORDER BY id',(task['task_id'],))]
    assert [x['event_type'] for x in events].count('workflow_created')==1
    assert events[-1]['event_type']=='prepared_lease_yielded'
    assert result['external_write_count']==0

def test_self_declared_task_metadata_is_not_adoption_authority(prepared):
    engine,profile,facts=prepared
    task=engine.create(dict(template='publication',source_key='caller',scope={'offer_id':'8412287877065586543'}))
    with pytest.raises(adoption.AdoptionBlocked,match='SIGNED_ORIGIN_MISSING'):
        adoption.verify(task,profile,offer='8412287877065586543',targets=['shopee:PH'],
          prepared_reference=facts['prepared_reference'],snapshot_digest=facts['snapshot_digest'])

def test_signed_origin_and_pinned_source_cannot_be_replaced(prepared):
    result=_adopt(prepared);engine,profile,_=prepared
    assert _verify(prepared,result)['task_id']==result['task_id']
    with engine.transaction() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE agent_prepared_publication_adoptions SET signature='forged'")
    (profile.root/'snapshot.json').write_text('{"native":"changed"}')
    with pytest.raises(adoption.AdoptionBlocked,match='SOURCE_BYTES_CHANGED'):_verify(prepared,result)

def test_yield_does_not_discard_an_unknown_external_action(prepared):
    engine,_,_=prepared
    task=engine.create(dict(template='publication',source_key='external',scope={'skus':['0431'],'shops':['shopee:PH']}))
    engine.register_executor('owned',['publication'],engine.release)
    token=engine.claim(task['task_id'],'owned')['lease_token']
    engine.mark_external_started(task['task_id'],token)
    with pytest.raises(ValueError,match='unresolved external'):
        engine.yield_prepared_lease(task['task_id'],token)
