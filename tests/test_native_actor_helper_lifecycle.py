"""Real Windows processes/Jobs; no formal startup, DB or provider."""
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from shared_platform import native_actor_helper as helper
from shared_platform import native_actor_launcher as launcher
from shared_platform.native_windows_actor import NativeActorServiceConfig
from shared_platform.final_review_server_admission import ApprovalBlocked

pytestmark=pytest.mark.skipif(os.name!='nt',reason='actual Windows owner helper required')


def _config(tmp_path):
    return NativeActorServiceConfig(tmp_path/'owned-supervisor','owned-test',120)


def _actual_job_zero(facts):
    assert facts['process_signaled'] is True
    assert facts['job_active']==0
    assert type(facts['exit_code']) is int


def test_real_owned_helper_ready_and_carrier_never_grants_execution(tmp_path):
    supervisor=helper.NativeActorHelperSupervisor(_config(tmp_path))
    try:
        endpoint=supervisor.endpoint()
        assert endpoint.process_id!=os.getpid()
        assert supervisor._process.facts()['job_active']==1
        path=supervisor.request_carrier()
        row=json.loads(path.read_bytes())
        assert set(row)=={'schema_version','session_id','capability','csrf'}
        assert row['schema_version']=='native-owner-session-carrier/v1'
        assert path.parent==endpoint.owner_root
        assert 'execution' not in row and 'approved_by' not in row
    finally:
        supervisor.close()
    _actual_job_zero(supervisor.history[-1])


def test_replacement_retires_real_job_before_new_generation(tmp_path):
    with helper.NativeActorHelperSupervisor(_config(tmp_path)) as supervisor:
        old=supervisor.endpoint(); old_process=supervisor._process
        new=supervisor.replace_owned()
        _actual_job_zero(old_process.facts())
        assert new.process_id!=old.process_id
        assert new.instance_id!=old.instance_id
        assert new.owner_root!=old.owner_root
        assert supervisor.history[-1]==old_process.facts()
        assert supervisor._process.facts()['job_active']==1
    _actual_job_zero(supervisor.history[-1])


def test_actual_exited_helper_is_replaced_without_request_replay(tmp_path):
    with helper.NativeActorHelperSupervisor(_config(tmp_path)) as supervisor:
        old=supervisor.endpoint(); process=supervisor._process
        assert process.k.TerminateJobObject(process.job,73)
        assert process.k.WaitForSingleObject(process.process,2000)==0
        new=supervisor.endpoint()
        _actual_job_zero(supervisor.history[-1])
        assert supervisor.history[-1]['exit_code']==73
        assert new.instance_id!=old.instance_id
        assert list(old.owner_root.glob('launcher-*.json'))==[]


def test_real_job_termination_failure_retains_handles_and_blocks_replace(tmp_path,monkeypatch):
    supervisor=helper.NativeActorHelperSupervisor(_config(tmp_path))
    endpoint=supervisor.endpoint(); process=supervisor._process
    original=process.k.TerminateJobObject
    try:
        monkeypatch.setattr(process.k,'TerminateJobObject',lambda *unused:0)
        with pytest.raises(ApprovalBlocked,match='TERMINATION_UNKNOWN'):
            supervisor.replace_owned()
        assert supervisor._unknown is True
        assert supervisor._process is process
        assert process.process and process.job
        assert process.facts()['job_active']==1
        with pytest.raises(ApprovalBlocked,match='CLOSED_OR_UNKNOWN'):
            supervisor.endpoint()
        assert supervisor._endpoint==endpoint
        assert supervisor.history==[]
    finally:
        monkeypatch.setattr(process.k,'TerminateJobObject',original)
        supervisor.close()
    _actual_job_zero(supervisor.history[-1])


def test_actual_job_unknown_query_never_counts_as_zero(tmp_path,monkeypatch):
    supervisor=helper.NativeActorHelperSupervisor(_config(tmp_path))
    supervisor.endpoint(); process=supervisor._process
    original=process.k.QueryInformationJobObject
    try:
        monkeypatch.setattr(process.k,'QueryInformationJobObject',lambda *unused:0)
        with pytest.raises(ApprovalBlocked,match='JOB_STATE_UNKNOWN'):
            supervisor.replace_owned()
        assert supervisor._unknown and supervisor._process is process
        assert process.process and process.job and supervisor.history==[]
    finally:
        monkeypatch.setattr(process.k,'QueryInformationJobObject',original)
        supervisor.close()
    _actual_job_zero(supervisor.history[-1])


def test_failed_carrier_is_not_replayed_but_owns_a_fresh_helper(tmp_path,monkeypatch):
    with helper.NativeActorHelperSupervisor(_config(tmp_path)) as supervisor:
        old=supervisor.endpoint(); calls=[]
        def lost_reply(root,nonce):
            calls.append((root,nonce))
            return [sys.executable,'-X','utf8','-B',str(Path(__file__)),
                '--owned-test-result-unknown',str(root),nonce]
        monkeypatch.setattr(helper,'_client_argv',lost_reply)
        with pytest.raises(ApprovalBlocked,match='CLIENT_RESULT_UNKNOWN'):
            supervisor.request_carrier()
        assert len(calls)==1  # One real owned client, no hidden request replay.
        _actual_job_zero(supervisor.client_history[-1])
        assert supervisor.client_history[-1]['exit_code']==74
        _actual_job_zero(supervisor.history[-1])
        assert supervisor.endpoint().instance_id!=old.instance_id
        assert supervisor._process.facts()['job_active']==1


def test_actual_client_quarantine_leaves_parent_clean_and_next_explicit_request_works(tmp_path,monkeypatch):
    before=(launcher._IO_POISONED,dict(launcher._IO_SLOTS))
    with helper.NativeActorHelperSupervisor(_config(tmp_path)) as supervisor:
        old=supervisor.endpoint(); original=helper._client_argv; calls=[]
        def fault_once(root,nonce):
            calls.append((root,nonce))
            if len(calls)==1:
                return [sys.executable,'-X','utf8','-B',str(Path(__file__)),
                    '--owned-test-client-quarantine',str(root),nonce]
            return original(root,nonce)
        monkeypatch.setattr(helper,'_client_argv',fault_once)
        with pytest.raises(ApprovalBlocked,match='CLIENT_RESULT_UNKNOWN'):
            supervisor.request_carrier()
        assert len(calls)==1
        assert supervisor.client_history[-1]['exit_code']==73
        _actual_job_zero(supervisor.client_history[-1])
        _actual_job_zero(supervisor.history[-1])
        proof=json.loads((calls[0][0]/'proof.json').read_bytes())
        assert proof=={'client_pending_read_actual':True,'poison':True,'slots':1,
            'refs_complete':True,'new_ipc_refused':True}
        assert (launcher._IO_POISONED,dict(launcher._IO_SLOTS))==before
        assert supervisor.endpoint().instance_id!=old.instance_id
        path=supervisor.request_carrier()  # A new explicit identity request.
        assert len(calls)==2 and path.is_file()
        _actual_job_zero(supervisor.client_history[-1])
        assert supervisor.client_history[-1]['exit_code']==0
        assert (launcher._IO_POISONED,dict(launcher._IO_SLOTS))==before


def test_unknown_actual_client_retirement_keeps_refs_and_blocks_new_request(tmp_path,monkeypatch):
    supervisor=helper.NativeActorHelperSupervisor(_config(tmp_path))
    supervisor.endpoint(); original_process=helper._OwnedProcess; clients=[]
    def owned_process(argv,cwd):
        result=original_process(argv,cwd)
        clients.append(result)
        if '--owned-test-sleeper' in argv:
            result._original_terminate=result.k.TerminateJobObject
            monkeypatch.setattr(result.k,'TerminateJobObject',lambda *args:0)
        return result
    monkeypatch.setattr(helper,'_OwnedProcess',owned_process)
    monkeypatch.setattr(helper,'_client_argv',lambda root,nonce:
        [sys.executable,'-X','utf8','-B',str(Path(__file__)),'--owned-test-sleeper'])
    try:
        with pytest.raises(ApprovalBlocked,match='TERMINATION_UNKNOWN'):
            supervisor.request_carrier(timeout_seconds=.05)
        assert supervisor._unknown and supervisor._client is clients[0]
        assert supervisor._client.process and supervisor._client.job
        assert supervisor._client.facts()['job_active']==1
        assert supervisor.client_history==[]
        with pytest.raises(ApprovalBlocked,match='CLOSED_OR_UNKNOWN'):
            supervisor.request_carrier()
        assert len(clients)==1
    finally:
        monkeypatch.setattr(clients[0].k,'TerminateJobObject',clients[0]._original_terminate)
        supervisor.close()
    _actual_job_zero(supervisor.client_history[-1])


@pytest.mark.parametrize('field', ['nonce','server_pid','owner_sid'])
def test_client_rejects_tampered_owner_metadata_without_io(tmp_path,field):
    from shared_platform import native_actor_client_worker as client
    with helper.NativeActorHelperSupervisor(_config(tmp_path)) as supervisor:
        endpoint=supervisor.endpoint(); nonce='a'*32
        root=supervisor.root/('metadata-'+field)
        helper.owner.create_owner_only_directory(root)
        row={'schema_version':'native-owner-client-request/v1','nonce':nonce,
            'owner_sid':endpoint.owner_sid,'server_pid':endpoint.process_id,
            'instance_id':endpoint.instance_id,'pipe_name':endpoint.pipe_name,
            'owner_root':str(endpoint.owner_root),'timeout_seconds':2}
        row[field]='foreign' if field!='server_pid' else -1
        (root/'request.json').write_bytes(helper.canonical_bytes(row))
        with pytest.raises(ApprovalBlocked,match='CLIENT_REQUEST_INVALID'):
            client.read_request(root,nonce)
        assert list(endpoint.owner_root.glob('launcher-*.json'))==[]


def test_unrelated_actual_process_is_not_stopped(tmp_path):
    # Independent process handle is never passed to this supervisor.
    other=subprocess.Popen([sys.executable,'-X','utf8','-B',str(Path(__file__)),
        '--owned-test-sleeper'],creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        with helper.NativeActorHelperSupervisor(_config(tmp_path)) as supervisor:
            supervisor.endpoint(); supervisor.replace_owned()
            assert other.poll() is None
        assert other.poll() is None
    finally:
        other.kill(); other.wait(timeout=2)


def test_real_pending_pipe_quarantine_exits_owned_job_before_recovery(tmp_path):
    root=tmp_path/'quarantine-peer'
    helper.owner.create_owner_only_directory(root)
    # Fault injection lives only in this owned child. ConnectNamedPipe is real;
    # cancellation/settle failure keeps actual pending addresses until exit.
    process=helper._OwnedProcess([sys.executable,'-X','utf8','-B',str(Path(__file__)),
        '--owned-test-quarantine',str(root)],ROOT)
    try:
        assert process.k.WaitForSingleObject(process.process,4000)==0
        facts=process.facts()
        _actual_job_zero(facts)
        assert facts['exit_code']==73
        proof=json.loads((root/'proof.json').read_bytes())
        assert proof=={'poison':True,'slots':1,'refs_complete':True,
            'pending_connect_actual':True,'new_ipc_refused':True}
    finally:
        process.stop()
    with helper.NativeActorHelperSupervisor(_config(tmp_path)) as supervisor:
        endpoint=supervisor.endpoint()
        assert endpoint.process_id!=process.pid
        assert supervisor._process.facts()['job_active']==1


def test_caller_dictionary_and_wrong_owner_cannot_start_helper(tmp_path,monkeypatch):
    with pytest.raises(ApprovalBlocked,match='NATIVE_CONFIG_REQUIRED'):
        helper.NativeActorHelperSupervisor({'root':str(tmp_path),'owner_sid':'caller'})
    supervisor=helper.NativeActorHelperSupervisor(_config(tmp_path))
    monkeypatch.setattr(helper.owner,'current_windows_owner_sid',lambda:'S-1-0-0')
    with pytest.raises(ApprovalBlocked,match='WRONG_OWNER'):
        supervisor.endpoint()
    assert supervisor._process is None
    supervisor.close()


def _quarantine_peer(root):
    service=launcher.bootstrap_service(NativeActorServiceConfig(root/'actor','owned-test',120))
    with launcher.NativeOwnerPipeLauncher(service) as pipe:
        original_wait=pipe.k.WaitForSingleObject
        pipe.k.CancelIoEx=lambda *args: (C.set_last_error(5) or 0)
        pipe.k.CancelIo=lambda *args: (C.set_last_error(5) or 0)
        pipe.k.WaitForSingleObject=lambda handle,millis:258 if millis==100 else original_wait(handle,millis)
        actual_connect=pipe.k.ConnectNamedPipe
        calls=[]
        def connect(handle,ov):
            result=actual_connect(handle,ov)
            error=C.get_last_error(); calls.append((bool(result),error))
            C.set_last_error(error)
            return result
        pipe.k.ConnectNamedPipe=connect
        try:
            pipe.serve_once(timeout_seconds=.05)
        except ApprovalBlocked as error:
            assert str(error)=='NATIVE_LAUNCHER_IO_QUARANTINED'
        else: raise AssertionError('expected actual pending quarantine')
        refs=next(iter(launcher._IO_SLOTS.values()))
        refused=False
        try: launcher._io_available()
        except ApprovalBlocked: refused=True
        (root/'proof.json').write_bytes(helper.canonical_bytes({
            'poison':launcher._IO_POISONED,'slots':len(launcher._IO_SLOTS),
            'refs_complete':type(refs) is tuple and len(refs)==6 and all(x is not None for x in refs),
            'pending_connect_actual':calls==[(False,997)],'new_ipc_refused':refused}))
    return 73


def _client_quarantine_peer(root,nonce):
    from shared_platform.native_actor_client_worker import read_request
    row,endpoint=read_request(root,nonce)
    a,k=launcher._apis()
    handle=k.CreateFileW(endpoint.pipe_name,0xC0000000,0,None,3,
        launcher._OVERLAPPED_IO|0x100000|0x20000,None)
    assert handle and handle!=launcher._INVALID_HANDLE
    original_wait=k.WaitForSingleObject; actual_read=k.ReadFile; calls=[]
    k.CancelIoEx=lambda *args:(C.set_last_error(5) or 0)
    k.CancelIo=lambda *args:(C.set_last_error(5) or 0)
    k.WaitForSingleObject=lambda event,millis:258 if millis==100 else original_wait(event,millis)
    def read(handle,buffer,size,transferred,ov):
        result=actual_read(handle,buffer,size,transferred,ov)
        error=C.get_last_error();calls.append((bool(result),error))
        C.set_last_error(error);return result
    k.ReadFile=read
    try:
        try: launcher._read(k,handle,50)
        except ApprovalBlocked as error:
            assert str(error)=='NATIVE_LAUNCHER_IO_QUARANTINED'
        else: raise AssertionError('expected actual pending client read')
        refused=False
        try: launcher._io_available()
        except ApprovalBlocked: refused=True
        refs=next(iter(launcher._IO_SLOTS.values()))
        (root/'proof.json').write_bytes(helper.canonical_bytes({
            'client_pending_read_actual':calls==[(False,997)],
            'poison':launcher._IO_POISONED,'slots':len(launcher._IO_SLOTS),
            'refs_complete':type(refs) is tuple and len(refs)==6 and all(x is not None for x in refs),
            'new_ipc_refused':refused}))
    finally:
        # Closing this pipe does not settle/release quarantined addresses.
        # They remain strongly referenced through actual process exit.
        k.CloseHandle(handle)
    return 73


if __name__=='__main__':
    if sys.argv[1:]==['--owned-test-sleeper']:
        time.sleep(20)
    elif len(sys.argv)==3 and sys.argv[1]=='--owned-test-quarantine':
        raise SystemExit(_quarantine_peer(Path(sys.argv[2])))
    elif len(sys.argv)==4 and sys.argv[1]=='--owned-test-client-quarantine':
        raise SystemExit(_client_quarantine_peer(Path(sys.argv[2]),sys.argv[3]))
    elif len(sys.argv)==4 and sys.argv[1]=='--owned-test-result-unknown':
        raise SystemExit(74)
    else: raise SystemExit(74)
