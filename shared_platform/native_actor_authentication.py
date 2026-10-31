"""Fixed same-owner authentication RPC. Identity facts, never execution grant.

Only a real owned helper's process handle supplies the birth identity. Client
overlapped I/O belongs to the replaceable owned client process, not the web
process. No endpoint, actor dictionary or bootstrap comes from HTTP.
"""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import time
import re

from shared_platform import local_operator_session as owner
from shared_platform.common_offer_authority_store import canonical_bytes
from shared_platform.final_review_server_admission import ApprovalBlocked

REQUEST_SCHEMA = 'native-owner-authenticate-request/v1'
RESULT_SCHEMA = 'native-owner-authenticate-result/v2'
CLIENT_SCHEMA = 'native-owner-authenticate-client/v1'
CLIENT_RESULT_SCHEMA = 'native-owner-authenticate-client-result/v1'
_CONTEXT = {'nonce','server_pid','server_birth_100ns','instance_id','logical_profile'}


def _deny(reason):
    raise ApprovalBlocked(reason)


def _kernel():
    if os.name != 'nt': _deny('NATIVE_ACTOR_WINDOWS_REQUIRED')
    k=C.WinDLL('kernel32',use_last_error=True)
    for name,result,args in [
        ('GetProcessTimes',W.BOOL,[W.HANDLE,C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME),C.POINTER(W.FILETIME)]),
        ('GetCurrentProcess',W.HANDLE,[]),
        ('OpenProcess',W.HANDLE,[W.DWORD,W.BOOL,W.DWORD]),
        ('WaitForSingleObject',W.DWORD,[W.HANDLE,W.DWORD]),
        ('CloseHandle',W.BOOL,[W.HANDLE]),
    ]:
        fn=getattr(k,name); fn.restype=result; fn.argtypes=args
    return k


def process_birth(handle):
    k=_kernel(); creation,exit_time,kernel,user=(W.FILETIME() for _ in range(4))
    if not k.GetProcessTimes(handle,C.byref(creation),C.byref(exit_time),C.byref(kernel),C.byref(user)):
        _deny('NATIVE_ACTOR_PROCESS_BIRTH_UNVERIFIED')
    value=(creation.dwHighDateTime<<32)|creation.dwLowDateTime
    if value<=0: _deny('NATIVE_ACTOR_PROCESS_BIRTH_UNVERIFIED')
    return value


def current_birth():
    k=_kernel()
    return process_birth(k.GetCurrentProcess())


def verify_server_process(pid,birth):
    k=_kernel(); handle=k.OpenProcess(0x1000|0x100000,False,pid)
    if not handle: _deny('NATIVE_ACTOR_SERVER_IDENTITY_CHANGED')
    try:
        if k.WaitForSingleObject(handle,0)!=258 or process_birth(handle)!=birth:
            _deny('NATIVE_ACTOR_SERVER_IDENTITY_CHANGED')
    finally:
        if not k.CloseHandle(handle): _deny('NATIVE_ACTOR_PROCESS_HANDLE_CLOSE_UNKNOWN')


def _check_context(row):
    if (type(row) is not dict or not _CONTEXT<=set(row)
            or type(row['server_pid']) is not int or row['server_pid']<=0
            or type(row['server_birth_100ns']) is not int or row['server_birth_100ns']<=0
            or any(type(row[key]) is not str or not re.fullmatch('[0-9a-f]{32}',row[key])
                   for key in ('nonce','instance_id'))
            or type(row['logical_profile']) is not str or not 0<len(row['logical_profile'])<=256):
        _deny('NATIVE_ACTOR_AUTH_CONTEXT_INVALID')


def decode_request(raw,endpoint,logical_profile):
    try: row=json.loads(raw)
    except (ValueError,TypeError): _deny('NATIVE_ACTOR_AUTH_REQUEST_INVALID')
    _check_context(row)
    if (set(row)!=_CONTEXT|{'schema_version','action','filename'}
            or row['schema_version']!=REQUEST_SCHEMA or row['action']!='authenticate-owner-session'
            or raw!=canonical_bytes(row) or len(raw)>2048
            or row['server_pid']!=endpoint.process_id
            or row['server_birth_100ns']!=current_birth()
            or row['instance_id']!=endpoint.instance_id
            or row['logical_profile']!=logical_profile):
        _deny('NATIVE_ACTOR_AUTH_REQUEST_INVALID')
    owner.LocalOperatorSessions._handoff_name(row['filename'])
    return row


def serve_authentication(service,row):
    """Called only after real pipe impersonation and successful RevertToSelf."""
    result={key:row[key] for key in _CONTEXT}
    result['schema_version']=RESULT_SCHEMA
    try:
        grant=service.grant_from_owner_file(filename=row['filename'])
        result['identity'],result['session_expires_at_epoch']=service._authenticate_with_expiry(grant)
        result['error']=None
    except ApprovalBlocked as error:
        # A known session rejection is acknowledged; unknown transport errors
        # still have no response and are never replayed by the supervisor.
        result['identity']=None
        result['session_expires_at_epoch']=None
        result['error']=str(error)
        if not 0<len(result['error'])<=256: raise
    return result


def check_result(row,context):
    _check_context(row)
    if (set(row)!=_CONTEXT|{'schema_version','identity','error','session_expires_at_epoch'}
            or row['schema_version']!=RESULT_SCHEMA
            or any(row[key]!=context[key] for key in _CONTEXT)
            or not (row['error'] is None and type(row['identity']) is dict
                    and type(row['session_expires_at_epoch']) is int and row['session_expires_at_epoch']>0
                    or row['identity'] is None and row['session_expires_at_epoch'] is None and type(row['error']) is str
                    and 0<len(row['error'])<=256)):
        _deny('NATIVE_ACTOR_AUTH_RESULT_INVALID')
    return row


def request_authentication(endpoint,row,*,timeout_seconds):
    """Only the isolated client worker calls this fixed pipe operation."""
    from shared_platform import native_actor_launcher as pipe
    timeout=pipe._milliseconds(timeout_seconds); pipe._io_available()
    import time
    deadline=time.monotonic()+min(timeout,2000)/1000
    if type(endpoint) is not pipe.NativeLauncherEndpoint:
        _deny('NATIVE_LAUNCHER_STARTUP_ENDPOINT_REQUIRED')
    _check_context(row)
    if owner.current_windows_owner_sid()!=endpoint.owner_sid:
        _deny('NATIVE_LAUNCHER_WRONG_OWNER')
    verify_server_process(endpoint.process_id,row['server_birth_100ns'])
    a,k=pipe._apis()
    handle,remaining=pipe._connect_before_frame(k,endpoint,deadline)
    try:
        pipe._verify_pipe_owner(a,k,handle,endpoint.owner_sid)
        pid=W.ULONG()
        if not k.GetNamedPipeServerProcessId(handle,C.byref(pid)) or pid.value!=endpoint.process_id:
            _deny('NATIVE_LAUNCHER_SERVER_IDENTITY_CHANGED')
        verify_server_process(endpoint.process_id,row['server_birth_100ns'])
        mode=W.DWORD(2)
        if not k.SetNamedPipeHandleState(handle,C.byref(mode),None,None):
            _deny('NATIVE_LAUNCHER_MODE_FAILED')
        request={key:row[key] for key in _CONTEXT|{'filename'}}
        request.update(schema_version=REQUEST_SCHEMA,action='authenticate-owner-session')
        pipe._write(k,handle,canonical_bytes(request),remaining())
        raw=pipe._read(k,handle,remaining())
        try: result=json.loads(raw)
        except (ValueError,TypeError): _deny('NATIVE_ACTOR_AUTH_RESULT_INVALID')
        check_result(result,row)
        if raw!=canonical_bytes(result): _deny('NATIVE_ACTOR_AUTH_RESULT_INVALID')
        verify_server_process(endpoint.process_id,row['server_birth_100ns'])
        pipe._write(k,handle,pipe._ACK,remaining())
        return result
    finally:
        k.CloseHandle(handle)


def read_client_request(root,nonce):
    from shared_platform import native_actor_helper as helper
    from shared_platform.native_actor_launcher import NativeLauncherEndpoint
    sid=owner.current_windows_owner_sid(); root=owner._safe_path(root)
    owner.verify_owner_only(root,sid)
    row=helper._read_metadata(root/'request.json',sid)
    _check_context(row)
    if (set(row)!=_CONTEXT|{'schema_version','owner_sid','pipe_name','owner_root','filename','timeout_seconds'}
            or row['schema_version']!=CLIENT_SCHEMA or row['nonce']!=nonce or row['owner_sid']!=sid
            or row['pipe_name']!=r'\\.\pipe\OrbitNativeOwner-'+row['instance_id']
            or type(row['owner_root']) is not str):
        _deny('NATIVE_HELPER_CLIENT_REQUEST_INVALID')
    helper._bound(row['timeout_seconds']); owner.LocalOperatorSessions._handoff_name(row['filename'])
    actor_root=owner._safe_path(Path(row['owner_root'])); owner.verify_owner_only(actor_root,sid)
    owner.verify_owner_only(owner._safe_path(actor_root/row['filename']),sid,protected=False)
    return row,NativeLauncherEndpoint(row['pipe_name'],row['server_pid'],sid,row['instance_id'],actor_root)


def verified_profile(config,endpoint):
    from shared_platform.native_windows_actor import NativeActorServiceConfig,_read_profile
    profile,digest=_read_profile(NativeActorServiceConfig(endpoint.owner_root,config.logical_profile,config.session_seconds))
    if profile['instance_id']!=endpoint.instance_id:
        _deny('NATIVE_ACTOR_SERVER_IDENTITY_CHANGED')
    return profile,digest


def verified_identity(result,config,endpoint):
    from shared_platform.native_windows_actor import ACTOR_SCHEMA,TRUST
    if result['error'] is not None: _deny(result['error'])
    identity=result['identity']
    profile,digest=verified_profile(config,endpoint)
    expected={'schema_version':ACTOR_SCHEMA,'identity_verified':True,'owner_sid':endpoint.owner_sid,
        'logical_profile':config.logical_profile,'instance_id':endpoint.instance_id,'trust_mode':TRUST,
        'mapping_source':profile['mapping_source'],'mapping_digest':digest}
    if (set(identity)!=set(expected)|{'session_id'}
            or any(type(identity[key]) is not type(value) or identity[key]!=value for key,value in expected.items())
            or type(identity['session_id']) is not str or not re.fullmatch('[0-9a-f]{32}',identity['session_id'])):
        _deny('NATIVE_ACTOR_AUTH_IDENTITY_INVALID')
    return dict(identity)


class NativeHelperActorProfileReader:
    """Service-owned injection. Pins one actual helper generation and carrier."""
    def __init__(self,supervisor,carrier):
        from shared_platform.native_actor_helper import NativeActorHelperSupervisor
        if type(supervisor) is not NativeActorHelperSupervisor or not isinstance(carrier,Path):
            _deny('NATIVE_ACTOR_SERVICE_READER_REQUIRED')
        self.supervisor=supervisor
        with supervisor._lock:
            self.endpoint=supervisor.endpoint()
            self.birth=process_birth(supervisor._process.process)
            self.carrier=owner._safe_path(carrier)
            if self.carrier.parent!=owner._safe_path(self.endpoint.owner_root):
                _deny('NATIVE_ACTOR_CARRIER_WRONG_GENERATION')
            owner.LocalOperatorSessions._handoff_name(self.carrier.name)
            owner.verify_owner_only(self.carrier,self.endpoint.owner_sid,protected=False)

    def read_verified(self):
        lease=_OPERATION_IDENTITY.get()
        if lease is not None and lease.reader is self:
            return lease.read()
        return self.supervisor.authenticate_carrier(self.carrier,
            expected_endpoint=self.endpoint,expected_birth=self.birth)

    @contextmanager
    def verified_operation(self):
        """One real RPC for this lexical operation, never a persistent grant.

        Every use still checks the actual Windows owner, helper birth,
        profile/carrier bytes and exact server-issued session deadline.
        Independent write-boundary readers outside this scope still RPC.
        """
        existing=_OPERATION_IDENTITY.get()
        if existing is not None and existing.reader is self:
            existing.read()
            yield
            existing.read()
            return
        before=_carrier_identity(self)
        identity,deadline=self.supervisor.authenticate_carrier(self.carrier,
            expected_endpoint=self.endpoint,expected_birth=self.birth,_with_expiry=True)
        if before!=_carrier_identity(self):_deny('NATIVE_ACTOR_HANDOFF_CHANGED')
        lease=_VerifiedOperationIdentity(self,identity,deadline,before)
        lease.read()
        token=_OPERATION_IDENTITY.set(lease)
        try:yield
        finally:_OPERATION_IDENTITY.reset(token)


_OPERATION_IDENTITY=ContextVar('native_verified_operation_identity',default=None)


def _carrier_identity(reader):
    """Read the protected carrier without exposing any capability bytes."""
    from shared_platform.native_windows_actor import _identity
    path=owner._safe_path(reader.carrier)
    owner.verify_owner_only(path,reader.endpoint.owner_sid,protected=False)
    before=path.stat()
    with path.open('rb') as stream:
        opened=os.fstat(stream.fileno());raw=stream.read(1025);closed=os.fstat(stream.fileno())
    after=owner._safe_path(path).stat()
    if (not 0<len(raw)<=1024 or not _identity(before)==_identity(opened)==_identity(closed)==_identity(after)):
        _deny('NATIVE_ACTOR_HANDOFF_CHANGED')
    return _identity(after),hashlib.sha256(raw).digest()


class _VerifiedOperationIdentity:
    """Only a real reader's successful protected RPC constructs this value."""
    def __init__(self,reader,identity,deadline,carrier_identity):
        self.reader=reader;self.identity=dict(identity);self.deadline=deadline
        self.carrier_identity=carrier_identity

    def read(self):
        reader=self.reader;supervisor=reader.supervisor
        with supervisor._lock:
            endpoint=supervisor.endpoint()
            if (endpoint!=reader.endpoint or owner.current_windows_owner_sid()!=endpoint.owner_sid
                    or process_birth(supervisor._process.process)!=reader.birth):
                _deny('NATIVE_ACTOR_SERVER_IDENTITY_CHANGED')
            if type(self.deadline) is not int or int(time.time())>=self.deadline:
                _deny('NATIVE_ACTOR_SESSION_EXPIRED')
            owner.verify_owner_only(endpoint.owner_root,endpoint.owner_sid)
            _,digest=verified_profile(supervisor.config,endpoint)
            if digest!=self.identity['mapping_digest'] or _carrier_identity(reader)!=self.carrier_identity:
                _deny('NATIVE_ACTOR_PROFILE_OR_HANDOFF_CHANGED')
            return dict(self.identity)
