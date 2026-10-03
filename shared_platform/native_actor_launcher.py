"""Owner-token native launcher channel. Never installed or opened on import.

The trusted per-user startup calls start_native_actor(config). An explicit
owner-only, remote-rejecting pipe authenticates the effective client token;
only after successful RevertToSelf may the native session producer run. No
caller SID, approval field, localhost HTTP action or private grant is accepted.
"""
from dataclasses import dataclass
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import secrets
import threading

from shared_platform import local_operator_session as owner
from shared_platform.common_offer_authority_store import canonical_bytes
from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform.native_windows_actor import (
    NativeActorServiceConfig, NativeWindowsActorService, bootstrap_service,
)

_REQUEST = canonical_bytes({'schema_version':'native-owner-launcher/v1', 'action':'owner-session'})
_ACK = canonical_bytes({'schema_version':'native-owner-launcher/v1', 'action':'received'})
_MAX_MESSAGE = 2048
_REMOTE_REJECT = 8
_FIRST_INSTANCE = 0x80000
_OVERLAPPED_IO = 0x40000000
_INVALID_HANDLE = C.c_void_p(-1).value
_CANCEL_SETTLE_MS = 100
_IO_LIMIT = 4
_IO_LOCK = threading.Lock()
_IO_SLOTS = {}
_IO_POISONED = False


def _deny(reason):
    raise ApprovalBlocked(reason)


class _Overlapped(C.Structure):
    _fields_ = [('internal', C.c_size_t), ('internal_high', C.c_size_t),
                ('offset', W.DWORD), ('offset_high', W.DWORD), ('event', W.HANDLE)]


def _apis():
    if os.name != 'nt':
        _deny('NATIVE_LAUNCHER_WINDOWS_REQUIRED')
    a, k = owner._windows()
    signatures = [
        (k.CreateNamedPipeW, W.HANDLE, [W.LPCWSTR,W.DWORD,W.DWORD,W.DWORD,W.DWORD,W.DWORD,W.DWORD,C.POINTER(owner._SecurityAttributes)]),
        (k.ConnectNamedPipe, W.BOOL, [W.HANDLE,C.POINTER(_Overlapped)]),
        (k.DisconnectNamedPipe, W.BOOL, [W.HANDLE]),
        (k.CreateFileW, W.HANDLE, [W.LPCWSTR,W.DWORD,W.DWORD,C.c_void_p,W.DWORD,W.DWORD,W.HANDLE]),
        (k.SetNamedPipeHandleState, W.BOOL, [W.HANDLE,C.POINTER(W.DWORD),C.c_void_p,C.c_void_p]),
        (k.GetNamedPipeServerProcessId, W.BOOL, [W.HANDLE,C.POINTER(W.ULONG)]),
        (k.GetCurrentProcessId, W.DWORD, []),
        (k.CreateEventW, W.HANDLE, [C.c_void_p,W.BOOL,W.BOOL,W.LPCWSTR]),
        (k.ReadFile, W.BOOL, [W.HANDLE,C.c_void_p,W.DWORD,C.POINTER(W.DWORD),C.POINTER(_Overlapped)]),
        (k.WriteFile, W.BOOL, [W.HANDLE,C.c_void_p,W.DWORD,C.POINTER(W.DWORD),C.POINTER(_Overlapped)]),
        (k.WaitForSingleObject, W.DWORD, [W.HANDLE,W.DWORD]),
        (k.GetOverlappedResult, W.BOOL, [W.HANDLE,C.POINTER(_Overlapped),C.POINTER(W.DWORD),W.BOOL]),
        (k.CancelIoEx, W.BOOL, [W.HANDLE,C.POINTER(_Overlapped)]),
        (k.CancelIo, W.BOOL, [W.HANDLE]),
        (a.ImpersonateNamedPipeClient, W.BOOL, [W.HANDLE]),
        (a.RevertToSelf, W.BOOL, []),
        (a.GetSecurityInfo,W.DWORD,[W.HANDLE,C.c_int,W.DWORD,C.POINTER(C.c_void_p),C.c_void_p,C.POINTER(C.c_void_p),C.c_void_p,C.POINTER(C.c_void_p)]),
    ]
    for fn, result, args in signatures:
        fn.restype, fn.argtypes = result, args
    return a, k


def _milliseconds(seconds):
    if type(seconds) not in (int,float) or not 0 < seconds <= 5:
        _deny('NATIVE_LAUNCHER_BOUNDED_TIMEOUT_REQUIRED')
    return max(1, int(seconds*1000))


def _check_io_locked():
    if _IO_POISONED or len(_IO_SLOTS) >= _IO_LIMIT:
        _deny('NATIVE_LAUNCHER_IO_QUARANTINE_REQUIRES_PROCESS_RESTART')


def _io_available():
    with _IO_LOCK:
        _check_io_locked()


def _allocate_ipc(operation):
    with _IO_LOCK:
        _check_io_locked()
        return operation()


def _reserve_io():
    with _IO_LOCK:
        _check_io_locked()
        slot = object()
        _IO_SLOTS[slot] = None
        return slot


def _quarantine_io(slot, k, handle, event, ov, transferred, operation):
    global _IO_POISONED
    with _IO_LOCK:
        # Slot was reserved before starting I/O. Keep every address the OS can
        # still access, including the operation closure's read/write buffer.
        # Never reclaim on CloseHandle or merely successful cancellation.
        _IO_SLOTS[slot] = (k, handle, event, ov, transferred, operation)
        _IO_POISONED = True


def _overlapped(k, handle, operation, timeout_ms, *, connected_ok=False, operation_factory=None):
    """Bounded wait; unresolved I/O is retained until this process exits.

    At most four operations are admitted. One quarantine permanently refuses
    further launcher IPC; no background wait or automatic reclamation masks an
    unknown driver outcome. Recovery requires automatic replacement of the
    controlled owner helper; that startup boundary is not installed here.
    """
    slot = _reserve_io()
    try:
        with _IO_LOCK:
            if _IO_POISONED:
                _deny('NATIVE_LAUNCHER_IO_QUARANTINE_REQUIRES_PROCESS_RESTART')
            event = k.CreateEventW(None, True, False, None)
    except BaseException:
        with _IO_LOCK:
            _IO_SLOTS.pop(slot)
        raise
    if not event:
        with _IO_LOCK:
            _IO_SLOTS.pop(slot)
        _deny('NATIVE_LAUNCHER_EVENT_UNAVAILABLE')
    ov = transferred = None
    pending = False
    quarantined = False
    try:
        ov, transferred = _Overlapped(event=event), W.DWORD()
        # A callback/API exception may happen after the kernel accepted I/O.
        # Until the return value proves otherwise, retain its live addresses.
        with _IO_LOCK:
            if _IO_POISONED:
                _deny('NATIVE_LAUNCHER_IO_QUARANTINE_REQUIRES_PROCESS_RESTART')
            if operation_factory is not None:
                operation = operation_factory()
            pending = True
            ok = operation(C.byref(ov), C.byref(transferred))
        error = 0 if ok else C.get_last_error()
        if connected_ok and error == 535:  # Client connected before ConnectNamedPipe.
            pending = False
            return 0
        if not ok and error != 997:
            pending = False
            _deny('NATIVE_LAUNCHER_IO_FAILED')
        if ok:
            pending = False
        if not ok:
            pending = True
            wait = k.WaitForSingleObject(event, timeout_ms)
            if wait != 0:
                # The buffers remain alive until cancellation is settled. A
                # timeout cannot create a grant or leave an owned operation.
                cancelled = k.CancelIoEx(handle, C.byref(ov))
                cancel_error = 0 if cancelled else C.get_last_error()
                if cancel_error not in (0, 1168):
                    # This operation was issued by this same thread. Recover
                    # cancellation using the original thread-scoped API; even
                    # a failed cancellation request must not release the live
                    # OVERLAPPED/event/buffer before actual completion.
                    k.CancelIo(handle)
                if k.WaitForSingleObject(event, _CANCEL_SETTLE_MS) != 0:
                    _quarantine_io(slot,k,handle,event,ov,transferred,operation)
                    quarantined = True
                    _deny('NATIVE_LAUNCHER_IO_QUARANTINED')
                completed = k.GetOverlappedResult(handle, C.byref(ov), C.byref(transferred), False)
                if not completed and C.get_last_error() in (6, 996, 997):
                    _quarantine_io(slot,k,handle,event,ov,transferred,operation)
                    quarantined = True
                    _deny('NATIVE_LAUNCHER_IO_QUARANTINED')
                pending = False
                if cancel_error not in (0, 1168):
                    _deny('NATIVE_LAUNCHER_CANCEL_FAILED')
                _deny('NATIVE_LAUNCHER_IO_TIMEOUT')
            completed = k.GetOverlappedResult(handle, C.byref(ov), C.byref(transferred), False)
            if not completed and C.get_last_error() in (6, 996, 997):
                _quarantine_io(slot,k,handle,event,ov,transferred,operation)
                quarantined = True
                _deny('NATIVE_LAUNCHER_IO_QUARANTINED')
            pending = False
            if not completed:
                _deny('NATIVE_LAUNCHER_IO_FAILED')
        elif not k.GetOverlappedResult(handle, C.byref(ov), C.byref(transferred), False):
            _deny('NATIVE_LAUNCHER_IO_FAILED')
        return transferred.value
    finally:
        # Even a Python/API error during cancellation cannot free live memory.
        if pending and not quarantined:
            _quarantine_io(slot,k,handle,event,ov,transferred,operation)
            quarantined = True
        if not quarantined:
            try:
                closed = k.CloseHandle(event)
            except BaseException:
                _quarantine_io(slot,k,handle,event,ov,transferred,operation)
                raise
            if not closed:
                _quarantine_io(slot,k,handle,event,ov,transferred,operation)
                _deny('NATIVE_LAUNCHER_EVENT_CLOSE_UNKNOWN')
            with _IO_LOCK:
                _IO_SLOTS.pop(slot)


def _read(k, handle, timeout_ms):
    buffer = None
    def operation():
        nonlocal buffer
        buffer = C.create_string_buffer(_MAX_MESSAGE+1)
        return lambda ov,n: k.ReadFile(handle,buffer,len(buffer),n,ov)
    count = _overlapped(k, handle, None, timeout_ms, operation_factory=operation)
    if not 0 < count <= _MAX_MESSAGE:
        _deny('NATIVE_LAUNCHER_MESSAGE_INVALID')
    return buffer.raw[:count]


def _write(k, handle, raw, timeout_ms):
    if type(raw) is not bytes or not 0 < len(raw) <= _MAX_MESSAGE:
        _deny('NATIVE_LAUNCHER_MESSAGE_INVALID')
    def operation():
        buffer = C.create_string_buffer(raw)
        return lambda ov,n: k.WriteFile(handle,buffer,len(raw),n,ov)
    count = _overlapped(k, handle, None, timeout_ms, operation_factory=operation)
    if count != len(raw):
        _deny('NATIVE_LAUNCHER_WRITE_INCOMPLETE')


def _verify_pipe_owner(a,k,handle,sid):
    sd,actual_owner,acl = C.c_void_p(),C.c_void_p(),C.c_void_p()
    if a.GetSecurityInfo(handle,1,5,C.byref(actual_owner),None,C.byref(acl),None,C.byref(sd)):
        _deny('NATIVE_LAUNCHER_OWNER_ACL_UNAVAILABLE')
    try:
        control,revision,info = W.WORD(),W.DWORD(),owner._AclSize()
        if (not sd or not actual_owner or not acl
                or not a.GetSecurityDescriptorControl(sd,C.byref(control),C.byref(revision))
                or not control.value & 0x1000
                or owner._sid_text(a,k,actual_owner) != sid
                or not a.GetAclInformation(acl,C.byref(info),C.sizeof(info),2)
                or info.count != 1):
            _deny('NATIVE_LAUNCHER_OWNER_ACL_INVALID')
        ace = C.c_void_p()
        if not a.GetAce(acl,0,C.byref(ace)):
            _deny('NATIVE_LAUNCHER_OWNER_ACL_INVALID')
        header = C.string_at(ace,8)
        if (header[0] != 0 or int.from_bytes(header[4:8],'little') != 0x1F01FF
                or owner._sid_text(a,k,C.c_void_p(ace.value+8)) != sid):
            _deny('NATIVE_LAUNCHER_OWNER_ACL_INVALID')
    finally:
        if sd:
            k.LocalFree(sd)


@dataclass(frozen=True)
class NativeLauncherEndpoint:
    """Created by trusted native startup, never parsed from an HTTP body."""
    pipe_name: str
    process_id: int
    owner_sid: str
    instance_id: str
    owner_root: Path


class NativeOwnerPipeLauncher:
    def __init__(self, service):
        if type(service) is not NativeWindowsActorService:
            _deny('NATIVE_LAUNCHER_NATIVE_SERVICE_REQUIRED')
        _io_available()
        profile, _ = service._current()
        self.service = service
        self.a, self.k = _apis()
        self.endpoint = NativeLauncherEndpoint(
            r'\\.\pipe\OrbitNativeOwner-'+profile['instance_id'],
            int(self.k.GetCurrentProcessId()), profile['owner_sid'],
            profile['instance_id'], service.config.root)
        self._handle = None
        self._used = False
        self._poisoned = False
        sd = C.c_void_p()
        if not self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f"O:{profile['owner_sid']}D:P(A;;FA;;;{profile['owner_sid']})",1,C.byref(sd),None):
            _deny('NATIVE_LAUNCHER_OWNER_DESCRIPTOR_FAILED')
        try:
            attrs = owner._SecurityAttributes(C.sizeof(owner._SecurityAttributes),sd,False)
            handle = _allocate_ipc(lambda: self.k.CreateNamedPipeW(self.endpoint.pipe_name,
                3|_OVERLAPPED_IO|_FIRST_INSTANCE, 4|2|_REMOTE_REJECT,
                1,_MAX_MESSAGE+1,_MAX_MESSAGE+1,0,C.byref(attrs)))
            if handle == _INVALID_HANDLE or not handle:
                _deny('NATIVE_LAUNCHER_PIPE_CREATE_FAILED')
            self._handle = handle
            try:
                _verify_pipe_owner(self.a,self.k,handle,self.endpoint.owner_sid)
            except BaseException:
                self.close()
                raise
        finally:
            self.k.LocalFree(sd)

    def _verified_peer(self):
        """Impersonation failure never falls back to the service process token."""
        if not self.a.ImpersonateNamedPipeClient(self._handle):
            _deny('NATIVE_LAUNCHER_PEER_IMPERSONATION_FAILED')
        try:
            token = W.HANDLE()
            if not self.a.OpenThreadToken(self.k.GetCurrentThread(),8,True,C.byref(token)):
                _deny('NATIVE_LAUNCHER_PEER_TOKEN_REQUIRED')
            try:
                sid = owner.current_windows_owner_sid()
            finally:
                self.k.CloseHandle(token)
            if sid != self.endpoint.owner_sid:
                _deny('NATIVE_LAUNCHER_WRONG_OWNER')
        finally:
            if not self.a.RevertToSelf():
                # Caller must terminate this owned native service thread. It
                # cannot proceed under the untrusted token or issue any grant.
                self._poisoned = True
                raise RuntimeError('NATIVE_LAUNCHER_REVERT_FAILED_FATAL')

    def serve_once(self, *, timeout_seconds=2):
        timeout = _milliseconds(timeout_seconds)
        if not self._handle or self._used or self._poisoned:
            _deny('NATIVE_LAUNCHER_CLOSED_OR_USED')
        self._used = True
        try:
            _verify_pipe_owner(self.a,self.k,self._handle,self.endpoint.owner_sid)
            _overlapped(self.k,self._handle,
                lambda ov,n: self.k.ConnectNamedPipe(self._handle,ov),timeout,connected_ok=True)
            request_raw = _read(self.k,self._handle,timeout)
            auth_request = None
            if request_raw != _REQUEST:
                from shared_platform import native_actor_authentication as auth
                auth_request = auth.decode_request(request_raw,self.endpoint,self.service.config.logical_profile)
            self._verified_peer()
            if auth_request is not None:
                result = auth.serve_authentication(self.service,auth_request)
                _write(self.k,self._handle,canonical_bytes(result),timeout)
                if _read(self.k,self._handle,timeout) != _ACK:
                    _deny('NATIVE_LAUNCHER_RESULT_NOT_ACKNOWLEDGED')
                return result
            # Native filesystem/owner/session producers run only AFTER revert.
            filename = 'launcher-'+secrets.token_hex(16)+'.json'
            path = self.service.bootstrap_to_owner_file(filename=filename)
            _write(self.k,self._handle,canonical_bytes({
                'schema_version':'native-owner-launcher-result/v1',
                'instance_id':self.endpoint.instance_id,'filename':path.name}),timeout)
            if _read(self.k,self._handle,timeout) != _ACK:
                _deny('NATIVE_LAUNCHER_RESULT_NOT_ACKNOWLEDGED')
            return path
        finally:
            self.close()

    def close(self):
        if self._handle:
            self.k.DisconnectNamedPipe(self._handle)
            self.k.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def request_owner_carrier(endpoint, *, timeout_seconds=2):
    """Trusted native launcher, owner-token pipe; no credentials in URLs/logs."""
    timeout = _milliseconds(timeout_seconds)
    _io_available()
    if type(endpoint) is not NativeLauncherEndpoint:
        _deny('NATIVE_LAUNCHER_STARTUP_ENDPOINT_REQUIRED')
    a,k = _apis()
    if owner.current_windows_owner_sid() != endpoint.owner_sid:
        _deny('NATIVE_LAUNCHER_WRONG_OWNER')
    # Explicit SQOS permits an impersonation token, never a delegating token.
    handle = _allocate_ipc(lambda: k.CreateFileW(endpoint.pipe_name,0xC0000000,0,None,3,
        _OVERLAPPED_IO|0x100000|0x20000,None))
    if handle == _INVALID_HANDLE or not handle:
        _deny('NATIVE_LAUNCHER_CONNECT_FAILED')
    try:
        pid = W.ULONG()
        if not k.GetNamedPipeServerProcessId(handle,C.byref(pid)) or pid.value != endpoint.process_id:
            _deny('NATIVE_LAUNCHER_SERVER_IDENTITY_CHANGED')
        mode = W.DWORD(2)
        if not k.SetNamedPipeHandleState(handle,C.byref(mode),None,None):
            _deny('NATIVE_LAUNCHER_MODE_FAILED')
        _write(k,handle,_REQUEST,timeout)
        raw = _read(k,handle,timeout)
        try:
            result = json.loads(raw)
        except (ValueError,TypeError):
            _deny('NATIVE_LAUNCHER_RESULT_INVALID')
        if (type(result) is not dict or set(result) != {'schema_version','instance_id','filename'}
                or result['schema_version'] != 'native-owner-launcher-result/v1'
                or result['instance_id'] != endpoint.instance_id or raw != canonical_bytes(result)):
            _deny('NATIVE_LAUNCHER_RESULT_INVALID')
        owner.LocalOperatorSessions._handoff_name(result['filename'])
        path = owner._safe_path(endpoint.owner_root/result['filename'])
        owner.verify_owner_only(endpoint.owner_root,endpoint.owner_sid)
        owner.verify_owner_only(path,endpoint.owner_sid,protected=False)
        _write(k,handle,_ACK,timeout)
        return path
    finally:
        k.CloseHandle(handle)


def start_native_actor(config):
    """Controlled per-user startup factory. Not called by existing web startup.

    Creates its explicit owner root if absent and opens only this owned pipe.
    It performs no release/COMMON/budget/approval/HTTP/worker registration.
    """
    if type(config) is not NativeActorServiceConfig:
        _deny('NATIVE_LAUNCHER_NATIVE_CONFIG_REQUIRED')
    _io_available()
    return NativeOwnerPipeLauncher(bootstrap_service(config))
