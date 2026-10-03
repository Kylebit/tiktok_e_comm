"""Actual owned Windows helper/client authentication; no formal install/grant."""
import json
import os
from pathlib import Path
import sys
import time

import pytest

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from shared_platform import native_actor_helper as helper
from shared_platform import native_actor_launcher as pipe
from shared_platform import native_actor_authentication as auth
from shared_platform.native_windows_actor import NativeActorServiceConfig
from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform.r3_native_final_approval import read_native_actor_identity

pytestmark=pytest.mark.skipif(os.name!='nt',reason='actual Windows owner authentication required')


@pytest.fixture
def supervisor(tmp_path):
    value=helper.NativeActorHelperSupervisor(NativeActorServiceConfig(tmp_path/'owned','owned-auth',120))
    before=(pipe._IO_POISONED,dict(pipe._IO_SLOTS))
    try: yield value
    finally:
        value.close()
        for row in value.history+value.client_history:
            assert row['process_signaled'] is True and row['job_active']==0
        assert (pipe._IO_POISONED,dict(pipe._IO_SLOTS))==before


def _reader(supervisor):
    return auth.NativeHelperActorProfileReader(supervisor,supervisor.request_carrier())


def test_original_consumer_reads_real_helper_session_across_processes(supervisor):
    reader=_reader(supervisor)
    identity=read_native_actor_identity(reader)
    carrier=json.loads(reader.carrier.read_bytes())
    assert identity['identity_verified'] is True
    assert identity['owner_sid']==helper.owner.current_windows_owner_sid()
    assert identity['instance_id']==reader.endpoint.instance_id
    assert identity['logical_profile']=='owned-auth'
    assert identity['trust_mode']=='SAME_WINDOWS_USER'
    assert identity['mapping_source']=='NATIVE_SAME_OWNER_STARTUP'
    assert identity['session_id']==carrier['session_id']
    assert set(identity)=={'schema_version','identity_verified','owner_sid','logical_profile',
        'instance_id','trust_mode','mapping_source','mapping_digest','session_id'}
    assert read_native_actor_identity(reader)==identity
    assert reader.endpoint.process_id!=os.getpid()
    assert reader.birth==auth.process_birth(supervisor._process.process)
    assert supervisor._process.facts()['job_active']==1
    assert len(supervisor.client_history)==3  # carrier, two original session validations
    assert all(row['exit_code']==0 for row in supervisor.client_history)


def test_real_expiry_is_rechecked_without_new_session(tmp_path):
    with helper.NativeActorHelperSupervisor(NativeActorServiceConfig(tmp_path/'expiry','owned-auth',1)) as value:
        reader=_reader(value)
        time.sleep(1.1)
        with pytest.raises(ApprovalBlocked,match='NATIVE_ACTOR_SESSION_EXPIRED'):
            read_native_actor_identity(reader)
        assert len(list(reader.endpoint.owner_root.glob('launcher-*.json')))==1
        assert value._process.facts()['job_active']==1
        assert value.client_history[-1]['exit_code']==0  # known rejected RPC, not transport unknown
    assert value.history[-1]['process_signaled'] and value.history[-1]['job_active']==0


@pytest.mark.parametrize('field',['capability','csrf','session_id'])
def test_real_carrier_tampering_cannot_authorize_original_session(supervisor,field):
    reader=_reader(supervisor); raw=json.loads(reader.carrier.read_bytes())
    raw[field]='0'*len(raw[field])
    reader.carrier.write_bytes(helper.canonical_bytes(raw))
    with pytest.raises(ApprovalBlocked,match='NATIVE_ACTOR_SESSION_INVALID_OR_EXPIRED'):
        read_native_actor_identity(reader)
    assert supervisor.client_history[-1]['exit_code']==0
    assert len(list(reader.endpoint.owner_root.glob('launcher-*.json')))==1


def test_real_generation_replacement_cannot_adopt_old_reader(supervisor):
    reader=_reader(supervisor); old=reader.endpoint
    replacement=supervisor.replace_owned()
    assert replacement.instance_id!=old.instance_id
    count=len(supervisor.client_history)
    with pytest.raises(ApprovalBlocked,match='SERVER_IDENTITY_CHANGED'):
        read_native_actor_identity(reader)
    assert len(supervisor.client_history)==count
    assert not list(replacement.owner_root.glob('launcher-*.json'))


def test_wrong_birth_rejected_before_any_new_client(supervisor):
    reader=_reader(supervisor); count=len(supervisor.client_history)
    with pytest.raises(ApprovalBlocked,match='SERVER_IDENTITY_CHANGED'):
        supervisor.authenticate_carrier(reader.carrier,expected_endpoint=reader.endpoint,
            expected_birth=reader.birth+1)
    assert len(supervisor.client_history)==count
    assert supervisor._client is None


def test_owner_file_from_other_real_helper_is_not_adopted(supervisor,tmp_path):
    with helper.NativeActorHelperSupervisor(NativeActorServiceConfig(tmp_path/'foreign','owned-auth',120)) as other:
        path=other.request_carrier()
        with pytest.raises(ApprovalBlocked,match='CARRIER_WRONG_GENERATION'):
            auth.NativeHelperActorProfileReader(supervisor,path)
        assert supervisor.client_history==[]
    assert other.history[-1]['process_signaled'] and other.history[-1]['job_active']==0


def test_profile_tampering_is_not_private_bootstrap(supervisor):
    reader=_reader(supervisor); path=reader.endpoint.owner_root/'native-owner.json'
    original=path.read_bytes(); row=json.loads(original)
    row['mapping_source']='SYNTHETIC_TEST_ONLY'
    path.write_bytes(helper.canonical_bytes(row))
    count=len(supervisor.client_history)
    try:
        with pytest.raises(ApprovalBlocked,match='PROFILE_INVALID_OR_CHANGED'):
            read_native_actor_identity(reader)
        assert len(supervisor.client_history)==count
    finally: path.write_bytes(original)


def test_unknown_authentication_result_is_never_replayed(supervisor,monkeypatch):
    reader=_reader(supervisor); old=reader.endpoint; original=helper._client_argv; calls=[]
    def fault(root,nonce):
        calls.append((root,nonce))
        return [sys.executable,'-X','utf8','-B',str(Path(__file__)),
            '--owned-auth-result-unknown',str(root),nonce]
    monkeypatch.setattr(helper,'_client_argv',fault)
    with pytest.raises(ApprovalBlocked,match='CLIENT_RESULT_UNKNOWN'):
        read_native_actor_identity(reader)
    assert len(calls)==1
    assert supervisor.client_history[-1]['exit_code']==74
    assert supervisor.client_history[-1]['job_active']==0
    assert supervisor.endpoint().instance_id!=old.instance_id
    assert len(list(old.owner_root.glob('launcher-*.json')))==1
    monkeypatch.setattr(helper,'_client_argv',original)
    with pytest.raises(ApprovalBlocked,match='SERVER_IDENTITY_CHANGED'):
        read_native_actor_identity(reader)
    assert len(calls)==1
    assert read_native_actor_identity(_reader(supervisor))['identity_verified'] is True


def test_actor_dictionary_and_private_reader_cannot_supply_native_identity():
    class PrivateReader:
        def read_verified(self): return {'identity_verified':True,'owner_sid':'caller'}
    for value in ({'approved_by':'caller','identity_verified':True},PrivateReader()):
        with pytest.raises(ApprovalBlocked,match='NATIVE_ACTOR_SERVICE_READER_REQUIRED'):
            read_native_actor_identity(value)


def test_auth_client_rejects_wrong_owner_metadata_before_io(supervisor):
    reader=_reader(supervisor); root=supervisor.root/'bad-auth-metadata'
    helper.owner.create_owner_only_directory(root)
    row={'schema_version':auth.CLIENT_SCHEMA,'nonce':'a'*32,'owner_sid':'S-1-0-0',
        'server_pid':reader.endpoint.process_id,'server_birth_100ns':reader.birth,
        'instance_id':reader.endpoint.instance_id,'logical_profile':'owned-auth',
        'pipe_name':reader.endpoint.pipe_name,'owner_root':str(reader.endpoint.owner_root),
        'filename':reader.carrier.name,'timeout_seconds':2}
    (root/'request.json').write_bytes(helper.canonical_bytes(row))
    before=(pipe._IO_POISONED,dict(pipe._IO_SLOTS))
    with pytest.raises(ApprovalBlocked,match='CLIENT_REQUEST_INVALID'):
        auth.read_client_request(root,row['nonce'])
    assert (pipe._IO_POISONED,dict(pipe._IO_SLOTS))==before


if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='--owned-auth-result-unknown':
        raise SystemExit(74)
    raise SystemExit(74)
