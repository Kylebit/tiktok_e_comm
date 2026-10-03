"""Owned real Windows pipe fixtures only; nothing starts at collection."""
import ctypes as C
from ctypes import wintypes as W
from dataclasses import replace
import json
import threading

import pytest

from shared_platform import native_actor_launcher as launch
from shared_platform import local_operator_session as owner
from shared_platform.common_offer_authority_store import canonical_bytes
from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform.native_windows_actor import (
    NativeActorServiceConfig, NativeWindowsActorProfileReader,
)


def _owned(tmp_path):
    return launch.start_native_actor(NativeActorServiceConfig(tmp_path/'native-owner','owned-pipe-test',60))


def _thread(server, timeout=2):
    result = {}
    def serve():
        try:
            result['path'] = server.serve_once(timeout_seconds=timeout)
        except BaseException as error:
            result['error'] = error
    thread = threading.Thread(target=serve)
    thread.start()
    return thread, result


def _join(thread):
    thread.join(8)
    assert not thread.is_alive(), 'owned native pipe thread did not settle'


def _raw_request(server, raw):
    _,k = launch._apis()
    handle = k.CreateFileW(server.endpoint.pipe_name,0xC0000000,0,None,3,
        launch._OVERLAPPED_IO|0x100000|0x20000,None)
    assert handle and handle != launch._INVALID_HANDLE
    try:
        mode = W.DWORD(2)
        assert k.SetNamedPipeHandleState(handle,C.byref(mode),None,None)
        launch._write(k,handle,raw,2000)
        with pytest.raises(ApprovalBlocked, match='NATIVE_LAUNCHER_IO_FAILED'):
            launch._read(k,handle,2000)
    finally:
        k.CloseHandle(handle)


def test_actual_same_owner_pipe_produces_native_reader_without_new_review(tmp_path):
    with _owned(tmp_path) as server:
        sid = owner.current_windows_owner_sid()
        thread,result = _thread(server)
        path = launch.request_owner_carrier(server.endpoint)
        _join(thread)
        assert result == {'path':path}
        grant = server.service.grant_from_owner_file(filename=path.name)
        facts = NativeWindowsActorProfileReader(server.service,grant).read_verified()
        assert facts['identity_verified'] is True
        assert facts['owner_sid'] == sid
        assert facts['trust_mode'] == 'SAME_WINDOWS_USER'
        assert owner.current_windows_owner_sid() == sid
        assert owner.verify_owner_only(path,sid,protected=False)['owner_only_ace_count'] == 1
        assert set(json.loads(path.read_bytes())) == {'schema_version','session_id','capability','csrf'}
        assert not server._handle


def test_actual_peer_is_reverted_before_native_carrier_producer(tmp_path,monkeypatch):
    with _owned(tmp_path) as server:
        calls = []
        original_revert = server.a.RevertToSelf
        original_produce = server.service.bootstrap_to_owner_file
        def revert():
            ok = original_revert()
            calls.append(('revert',bool(ok)))
            return ok
        def produce(**kwargs):
            assert calls == [('revert',True)]
            a,k = launch._apis()
            token = W.HANDLE()
            assert not a.OpenThreadToken(k.GetCurrentThread(),8,True,C.byref(token))
            assert C.get_last_error() == 1008
            calls.append(('produce',True))
            return original_produce(**kwargs)
        monkeypatch.setattr(server.a,'RevertToSelf',revert)
        monkeypatch.setattr(server.service,'bootstrap_to_owner_file',produce)
        thread,result = _thread(server)
        launch.request_owner_carrier(server.endpoint)
        _join(thread)
        assert 'error' not in result
        assert calls == [('revert',True),('produce',True)]


def test_real_closed_pipe_impersonation_failure_never_grants(tmp_path):
    with _owned(tmp_path) as server:
        server.close()
        with pytest.raises(ApprovalBlocked, match='NATIVE_LAUNCHER_PEER_IMPERSONATION_FAILED'):
            server._verified_peer()
        assert server.service._sessions == {}
        assert list(server.service.config.root.glob('launcher-*.json')) == []


def test_wrong_effective_peer_sid_is_refused_and_reverted(tmp_path,monkeypatch):
    with _owned(tmp_path) as server:
        real_sid = owner.current_windows_owner_sid
        def peer_sid():
            a,k = launch._apis()
            token = W.HANDLE()
            if a.OpenThreadToken(k.GetCurrentThread(),8,True,C.byref(token)):
                k.CloseHandle(token)
                return 'S-1-5-21-1-2-3-9876'
            return real_sid()
        monkeypatch.setattr(owner,'current_windows_owner_sid',peer_sid)
        thread,result = _thread(server)
        with pytest.raises(ApprovalBlocked, match='NATIVE_LAUNCHER_IO_FAILED'):
            launch.request_owner_carrier(server.endpoint)
        _join(thread)
        assert str(result['error']) == 'NATIVE_LAUNCHER_WRONG_OWNER'
        assert server.service._sessions == {}
        assert owner.current_windows_owner_sid() == server.endpoint.owner_sid


def test_request_actor_dictionary_cannot_issue_a_native_grant(tmp_path):
    with _owned(tmp_path) as server:
        thread,result = _thread(server)
        _raw_request(server,canonical_bytes({'schema_version':'native-owner-launcher/v1',
            'action':'owner-session','approved_by':server.endpoint.owner_sid,'actor':'trusted'}))
        _join(thread)
        assert str(result['error']) == 'NATIVE_ACTOR_AUTH_CONTEXT_INVALID'
        assert set(result) == {'error'} and not server._handle
        assert server.service._sessions == {}


def test_no_peer_timeout_settles_our_handle_without_grant(tmp_path):
    with _owned(tmp_path) as server:
        with pytest.raises(ApprovalBlocked,match='NATIVE_LAUNCHER_IO_TIMEOUT'):
            server.serve_once(timeout_seconds=.05)
        assert not server._handle
        assert server.service._sessions == {}


def test_remote_reject_first_instance_owner_dacl_and_pipe_squatting(tmp_path,monkeypatch):
    with _owned(tmp_path) as server:
        # Genuine occupied first instance prevents a second server from adopting
        # the same name. Constants bind explicit creation flags, not a real
        # remote request or a fabricated successful wrong-user identity.
        assert launch._REMOTE_REJECT == 8 and launch._FIRST_INSTANCE == 0x80000
        launch._verify_pipe_owner(server.a,server.k,server._handle,server.endpoint.owner_sid)
        with pytest.raises(ApprovalBlocked,match='NATIVE_LAUNCHER_PIPE_CREATE_FAILED'):
            launch.NativeOwnerPipeLauncher(server.service)
        assert server.service._sessions == {}


def test_native_startup_rejects_caller_dictionary_and_private_service(tmp_path):
    with pytest.raises(ApprovalBlocked,match='NATIVE_LAUNCHER_NATIVE_CONFIG_REQUIRED'):
        launch.start_native_actor({'root':tmp_path,'actor':'owner'})
    with pytest.raises(ApprovalBlocked,match='NATIVE_LAUNCHER_NATIVE_SERVICE_REQUIRED'):
        launch.NativeOwnerPipeLauncher(object())
    with pytest.raises(ApprovalBlocked,match='NATIVE_LAUNCHER_STARTUP_ENDPOINT_REQUIRED'):
        launch.request_owner_carrier({'pipe_name':'local'})


def test_restart_old_carrier_cannot_authenticate_new_native_instance(tmp_path):
    with _owned(tmp_path) as first:
        thread,result = _thread(first)
        path = launch.request_owner_carrier(first.endpoint)
        _join(thread)
        assert 'error' not in result
        grant = first.service.grant_from_owner_file(filename=path.name)
    with launch.start_native_actor(first.service.config) as second:
        with pytest.raises(ApprovalBlocked,match='NATIVE_ACTOR_SESSION_INVALID_OR_EXPIRED'):
            second.service.authenticate(grant)
        assert second.service._sessions == {}


def test_actual_server_pid_mismatch_refuses_before_request(tmp_path):
    with _owned(tmp_path) as server:
        thread,result = _thread(server)
        with pytest.raises(ApprovalBlocked,match='NATIVE_LAUNCHER_SERVER_IDENTITY_CHANGED'):
            launch.request_owner_carrier(replace(server.endpoint,process_id=server.endpoint.process_id+1))
        _join(thread)
        assert 'error' in result
        assert server.service._sessions == {}


@pytest.mark.parametrize('fallback_report_error', [False, True], ids=['fallback-success','fallback-error-after-real-cancel'])
def test_real_pending_connect_cancel_error_settles_before_event_and_handle_close(tmp_path,monkeypatch,fallback_report_error):
    # Only error reports are injected. The operation is a genuine pending
    # ConnectNamedPipe and cancellation/completion are actual Windows calls.
    # The second branch proves an error return cannot bypass the completion
    # barrier after a cancellation already took effect in the kernel.
    with _owned(tmp_path) as server:
        original_cancel = server.k.CancelIo
        original_result = server.k.GetOverlappedResult
        original_close = server.k.CloseHandle
        calls=[]
        event=[]
        def cancel_ex(handle,ov):
            assert handle == server._handle
            event.append(C.cast(ov,C.POINTER(launch._Overlapped)).contents.event)
            calls.append('cancel-ex-error')
            C.set_last_error(5)
            return False
        def cancel(handle):
            assert original_cancel(handle), 'actual same-thread cancellation must succeed'
            calls.append('actual-cancel')
            if fallback_report_error:
                C.set_last_error(5)
                return False
            return True
        def settled(handle,ov,transferred,wait):
            assert not wait and calls == ['cancel-ex-error','actual-cancel']
            ok=original_result(handle,ov,transferred,wait)
            error=0 if ok else C.get_last_error()
            assert not ok and error == 995, 'actual pending connect must complete cancelled'
            calls.append('settled')
            C.set_last_error(error)
            return ok
        def close(handle):
            if event and handle == event[0]:
                assert calls == ['cancel-ex-error','actual-cancel','settled']
                calls.append('event-closed')
            elif handle == server._handle:
                assert calls[-1] == 'event-closed'
                calls.append('pipe-closed')
            return original_close(handle)
        monkeypatch.setattr(server.k,'CancelIoEx',cancel_ex)
        monkeypatch.setattr(server.k,'CancelIo',cancel)
        monkeypatch.setattr(server.k,'GetOverlappedResult',settled)
        monkeypatch.setattr(server.k,'CloseHandle',close)
        with pytest.raises(ApprovalBlocked,match='NATIVE_LAUNCHER_CANCEL_FAILED'):
            server.serve_once(timeout_seconds=.05)
        assert calls == ['cancel-ex-error','actual-cancel','settled','event-closed','pipe-closed']
        assert not server._handle and not server.service._sessions
        assert list(server.service.config.root.glob('launcher-*.json')) == []


@pytest.mark.parametrize('cancel_raises',[False,True],ids=['double-refusal','api-exception'])
def test_actual_uncancelled_read_is_bounded_and_retains_buffer_until_real_completion(tmp_path,monkeypatch,cancel_raises):
    import time
    assert launch._IO_SLOTS == {} and not launch._IO_POISONED
    with _owned(tmp_path) as server:
        k=server.k
        client=k.CreateFileW(server.endpoint.pipe_name,0xC0000000,0,None,3,
            launch._OVERLAPPED_IO|0x100000|0x20000,None)
        assert client and client != launch._INVALID_HANDLE
        try:
            launch._overlapped(k,server._handle,lambda ov,n:k.ConnectNamedPipe(server._handle,ov),2000,connected_ok=True)
            # Inject cancellation refusal, leaving a genuine kernel ReadFile
            # pending because the connected client deliberately sends nothing.
            def refuse(*args):
                if cancel_raises:
                    raise RuntimeError('owned cancel API failure')
                C.set_last_error(5)
                return False
            monkeypatch.setattr(k,'CancelIoEx',refuse)
            monkeypatch.setattr(k,'CancelIo',refuse)
            begin=time.monotonic()
            expected=RuntimeError if cancel_raises else ApprovalBlocked
            message='owned cancel API failure' if cancel_raises else 'NATIVE_LAUNCHER_IO_QUARANTINED'
            with pytest.raises(expected,match=message):
                launch._read(k,server._handle,50)
            assert time.monotonic()-begin < 2
            assert launch._IO_POISONED and len(launch._IO_SLOTS)==1
            retained=next(iter(launch._IO_SLOTS.values()))
            _,_,event,ov,_,operation=retained
            assert ov.event==event and k.WaitForSingleObject(event,0)==258
            assert any(isinstance(cell.cell_contents,C.Array) and len(cell.cell_contents)==launch._MAX_MESSAGE+1
                for cell in operation.__closure__)
            assert not server.service._sessions
            with pytest.raises(ApprovalBlocked,match='NATIVE_LAUNCHER_IO_QUARANTINE_REQUIRES_PROCESS_RESTART'):
                launch.request_owner_carrier(server.endpoint)
            with pytest.raises(ApprovalBlocked,match='NATIVE_LAUNCHER_IO_QUARANTINE_REQUIRES_PROCESS_RESTART'):
                launch.start_native_actor(server.service.config)
        finally:
            k.CloseHandle(client)
            server.close()
            # Only the owned TEST reclaims after a real completion signal.
            # Production intentionally has no reset/reclaim interface.
            for retained in launch._IO_SLOTS.values():
                if retained is not None:
                    assert k.WaitForSingleObject(retained[2],2000)==0
                    k.CloseHandle(retained[2])
            launch._IO_SLOTS.clear()
            launch._IO_POISONED=False


def test_four_actual_pending_connects_reserve_capacity_before_fifth_io(tmp_path,monkeypatch):
    from contextlib import ExitStack
    assert launch._IO_SLOTS == {} and not launch._IO_POISONED
    for n in range(4):
        (tmp_path/str(n)).mkdir()
    with ExitStack() as stack:
        servers=[stack.enter_context(_owned(tmp_path/str(n))) for n in range(4)]
        k=servers[0].k
        actual_wait=k.WaitForSingleObject
        ready=threading.Event(); release=threading.Event(); lock=threading.Lock()
        entered=[]
        def wait(event,ms):
            if ms==50:
                with lock:
                    entered.append(event)
                    if len(entered)==4:ready.set()
                assert release.wait(2)
            return actual_wait(event,ms)
        for server in servers:
            monkeypatch.setattr(server.k,'WaitForSingleObject',wait)
        threads=[_thread(server,.05) for server in servers]
        try:
            assert ready.wait(2)
            assert len(launch._IO_SLOTS)==launch._IO_LIMIT==4
            with pytest.raises(ApprovalBlocked,match='NATIVE_LAUNCHER_IO_QUARANTINE_REQUIRES_PROCESS_RESTART'):
                launch._reserve_io()
            def no_buffer(*args):
                pytest.fail('capacity refusal must precede new buffer allocation')
            monkeypatch.setattr(C,'create_string_buffer',no_buffer)
            with pytest.raises(ApprovalBlocked,match='NATIVE_LAUNCHER_IO_QUARANTINE_REQUIRES_PROCESS_RESTART'):
                launch._read(k,servers[0]._handle,50)
            assert len(launch._IO_SLOTS)==4
        finally:
            release.set()
            for thread,result in threads:
                _join(thread)
                assert str(result['error'])=='NATIVE_LAUNCHER_IO_TIMEOUT'
        assert launch._IO_SLOTS=={} and not launch._IO_POISONED
        assert all(not server.service._sessions for server in servers)


@pytest.mark.parametrize('constructor', ['OVERLAPPED', 'transferred'])
def test_actual_event_and_slot_close_when_pre_io_construction_raises(tmp_path, monkeypatch, constructor):
    assert launch._IO_SLOTS == {} and not launch._IO_POISONED
    with _owned(tmp_path) as server:
        k = server.k
        create, close = k.CreateEventW, k.CloseHandle
        events, closed, issued = [], [], []
        def event(*args):
            handle = create(*args)
            assert handle and len(launch._IO_SLOTS) == 1
            events.append(handle)
            return handle
        def close_event(handle):
            ok = close(handle)
            if handle in events:
                assert ok and not issued
                closed.append(handle)
            return ok
        def fail(*args, **kwargs):
            assert len(events) == 1 and len(launch._IO_SLOTS) == 1
            raise MemoryError('owned pre-IO construction failure')
        def operation(ov, transferred):
            issued.append(True)
            return k.ConnectNamedPipe(server._handle, ov)
        with monkeypatch.context() as scoped:
            scoped.setattr(k, 'CreateEventW', event)
            scoped.setattr(k, 'CloseHandle', close_event)
            scoped.setattr(launch if constructor == 'OVERLAPPED' else W,
                '_Overlapped' if constructor == 'OVERLAPPED' else 'DWORD', fail)
            with pytest.raises(MemoryError, match='^owned pre-IO construction failure$'):
                launch._overlapped(k, server._handle, operation, 50)
        assert len(events) == 1 and closed == events and not issued
        assert k.WaitForSingleObject(events[0], 0) == 0xFFFFFFFF
        assert C.get_last_error() == 6
        assert launch._IO_SLOTS == {} and not launch._IO_POISONED
        assert server.service._sessions == {}
