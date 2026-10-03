"""Trusted same-owner helper lifetime. Nothing starts on import.

Only this component's process handles and unnamed Job are controlled. A
carrier is an identity handoff, never publication authority. Cross-process
authenticate/sole-consumer installation is deliberately a separate contract.
"""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

from shared_platform import local_operator_session as owner
from shared_platform.common_offer_authority_store import canonical_bytes, _safe_path
from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform.native_actor_launcher import NativeLauncherEndpoint
from shared_platform.native_windows_actor import NativeActorServiceConfig
from shared_platform.native_windows_actor import _identity


class _Startup(C.Structure):
    _fields_ = [('cb',W.DWORD),('reserved',W.LPWSTR),('desktop',W.LPWSTR),
        ('title',W.LPWSTR),('x',W.DWORD),('y',W.DWORD),('sx',W.DWORD),
        ('sy',W.DWORD),('chars_x',W.DWORD),('chars_y',W.DWORD),
        ('fill',W.DWORD),('flags',W.DWORD),('show',W.WORD),
        ('reserved2_size',W.WORD),('reserved2',C.c_void_p),
        ('stdin',W.HANDLE),('stdout',W.HANDLE),('stderr',W.HANDLE)]


class _StartupEx(C.Structure):
    _fields_ = [('startup',_Startup),('attributes',C.c_void_p)]


class _Process(C.Structure):
    _fields_ = [('process',W.HANDLE),('thread',W.HANDLE),
                ('pid',W.DWORD),('tid',W.DWORD)]


class _BasicLimit(C.Structure):
    _fields_ = [('process_time',C.c_int64),('job_time',C.c_int64),
        ('flags',W.DWORD),('min_ws',C.c_size_t),('max_ws',C.c_size_t),
        ('active_limit',W.DWORD),('affinity',C.c_size_t),
        ('priority',W.DWORD),('scheduling',W.DWORD)]


class _Limits(C.Structure):
    _fields_ = [('basic',_BasicLimit),('io',C.c_uint64*6),
        ('process_memory',C.c_size_t),('job_memory',C.c_size_t),
        ('peak_process',C.c_size_t),('peak_job',C.c_size_t)]


class _Accounting(C.Structure):
    _fields_ = [('user',C.c_int64),('kernel',C.c_int64),
        ('period_user',C.c_int64),('period_kernel',C.c_int64),
        ('faults',W.DWORD),('total',W.DWORD),('active',W.DWORD),
        ('terminated',W.DWORD)]


def _deny(reason):
    raise ApprovalBlocked(reason)


def _kernel():
    if os.name != 'nt':
        _deny('NATIVE_HELPER_WINDOWS_REQUIRED')
    k = C.WinDLL('kernel32',use_last_error=True)
    for name,result,args in [
        ('CreateJobObjectW',W.HANDLE,[C.c_void_p,W.LPCWSTR]),
        ('SetInformationJobObject',W.BOOL,[W.HANDLE,C.c_int,C.c_void_p,W.DWORD]),
        ('QueryInformationJobObject',W.BOOL,[W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.c_void_p]),
        ('TerminateJobObject',W.BOOL,[W.HANDLE,W.UINT]),
        ('InitializeProcThreadAttributeList',W.BOOL,[C.c_void_p,W.DWORD,W.DWORD,C.POINTER(C.c_size_t)]),
        ('UpdateProcThreadAttribute',W.BOOL,[C.c_void_p,W.DWORD,C.c_size_t,C.c_void_p,C.c_size_t,C.c_void_p,C.c_void_p]),
        ('DeleteProcThreadAttributeList',None,[C.c_void_p]),
        ('CreateProcessW',W.BOOL,[W.LPCWSTR,W.LPWSTR,C.c_void_p,C.c_void_p,W.BOOL,W.DWORD,C.c_void_p,W.LPCWSTR,C.c_void_p,C.POINTER(_Process)]),
        ('ResumeThread',W.DWORD,[W.HANDLE]),
        ('WaitForSingleObject',W.DWORD,[W.HANDLE,W.DWORD]),
        ('GetExitCodeProcess',W.BOOL,[W.HANDLE,C.POINTER(W.DWORD)]),
        ('CloseHandle',W.BOOL,[W.HANDLE]),
    ]:
        fn=getattr(k,name); fn.restype=result; fn.argtypes=args
    return k


def _bound(seconds):
    if type(seconds) not in (int,float) or not 0 < seconds <= 5:
        _deny('NATIVE_HELPER_BOUNDED_TIMEOUT_REQUIRED')
    return seconds


def _read_metadata(path, sid):
    """Owner-only canonical metadata, fresh handle/path identity, bounded size."""
    path=_safe_path(path)
    owner.verify_owner_only(path.parent,sid)
    owner.verify_owner_only(path,sid,protected=False)
    before=path.stat()
    if not 0<before.st_size<=4096: _deny('NATIVE_HELPER_METADATA_INVALID')
    with path.open('rb') as stream:
        opened=os.fstat(stream.fileno()); raw=stream.read(4097)
        closed=os.fstat(stream.fileno())
    after=_safe_path(path).stat()
    owner.verify_owner_only(path.parent,sid)
    owner.verify_owner_only(path,sid,protected=False)
    if (not _identity(before)==_identity(opened)==_identity(closed)==_identity(after)
            or len(raw)!=before.st_size):
        _deny('NATIVE_HELPER_METADATA_CHANGED')
    row=json.loads(raw)
    if type(row) is not dict or raw!=canonical_bytes(row):
        _deny('NATIVE_HELPER_METADATA_INVALID')
    return row


def _client_argv(root, nonce):
    # Fixed source entry only; neither executable nor argv comes from HTTP.
    return [sys.executable,'-X','utf8','-B','-m',
            'shared_platform.native_actor_client_worker',str(root),nonce]


class _OwnedProcess:
    """Atomic Job-at-creation prevents a suspended orphan before assignment."""
    def __init__(self, argv, cwd):
        self.k=_kernel(); self.job=self.process=self.thread=None
        self.pid=None; self.retired=None
        k=self.k; attributes=None; initialized=False
        try:
            self.job=k.CreateJobObjectW(None,None)
            if not self.job: raise C.WinError(C.get_last_error())
            limits=_Limits(); limits.basic.flags=0x2000|8
            limits.basic.active_limit=1  # Helpers must not spawn other programs.
            if not k.SetInformationJobObject(self.job,9,C.byref(limits),C.sizeof(limits)):
                raise C.WinError(C.get_last_error())
            size=C.c_size_t()
            k.InitializeProcThreadAttributeList(None,1,0,C.byref(size))
            if not size.value or size.value>65536:
                _deny('NATIVE_HELPER_JOB_ATTRIBUTE_UNAVAILABLE')
            attributes=C.create_string_buffer(size.value)
            if not k.InitializeProcThreadAttributeList(attributes,1,0,C.byref(size)):
                raise C.WinError(C.get_last_error())
            initialized=True
            jobs=(W.HANDLE*1)(self.job)
            if not k.UpdateProcThreadAttribute(attributes,0,0x2000D,jobs,C.sizeof(jobs),None,None):
                raise C.WinError(C.get_last_error())
            startup=_StartupEx(); startup.startup.cb=C.sizeof(startup)
            startup.attributes=C.cast(attributes,C.c_void_p)
            process=_Process()
            command=C.create_unicode_buffer(subprocess.list2cmdline(argv))
            if not k.CreateProcessW(argv[0],command,None,None,False,
                    0x8|0x80000|4,None,str(cwd),C.byref(startup),C.byref(process)):
                raise C.WinError(C.get_last_error())
            self.process,self.thread,self.pid=process.process,process.thread,int(process.pid)
            if k.ResumeThread(self.thread)==0xFFFFFFFF:
                raise C.WinError(C.get_last_error())
            if not k.CloseHandle(self.thread):
                raise C.WinError(C.get_last_error())
            self.thread=None
        except BaseException:
            # Never lose a successfully created owned process on startup error.
            if self.process:
                try: self.stop(2)
                except BaseException: _UNKNOWN_OWNED.append(self)
            elif self.job:
                if not k.CloseHandle(self.job): _UNKNOWN_OWNED.append(self)
                else: self.job=None
            raise
        finally:
            if initialized: k.DeleteProcThreadAttributeList(attributes)

    def facts(self):
        if self.retired is not None: return dict(self.retired)
        wait=self.k.WaitForSingleObject(self.process,0)
        if wait not in (0,258): _deny('NATIVE_HELPER_EXIT_UNKNOWN')
        accounting=_Accounting()
        if not self.k.QueryInformationJobObject(self.job,1,C.byref(accounting),C.sizeof(accounting),None):
            _deny('NATIVE_HELPER_JOB_STATE_UNKNOWN')
        code=W.DWORD()
        if not self.k.GetExitCodeProcess(self.process,C.byref(code)):
            _deny('NATIVE_HELPER_EXIT_UNKNOWN')
        return {'pid':self.pid,'process_signaled':wait==0,
                'job_active':int(accounting.active),'exit_code':int(code.value)}

    def stop(self, seconds=2):
        deadline=time.monotonic()+_bound(seconds)
        if self.retired is not None: return dict(self.retired)
        initial=self.facts()
        if not (initial['process_signaled'] and initial['job_active']==0):
            if not self.k.TerminateJobObject(self.job,73):
                _deny('NATIVE_HELPER_TERMINATION_UNKNOWN')
        while True:
            facts=self.facts()
            if facts['process_signaled'] and facts['job_active']==0: break
            if time.monotonic()>=deadline: _deny('NATIVE_HELPER_TERMINATION_UNKNOWN')
            time.sleep(min(.01,max(0,deadline-time.monotonic())))
        # Exit and active=0 are proven before handles are released.
        for name in ('thread','process','job'):
            handle=getattr(self,name)
            if handle and not self.k.CloseHandle(handle):
                _deny('NATIVE_HELPER_HANDLE_CLOSE_UNKNOWN')
            setattr(self,name,None)
        self.retired=facts
        return dict(facts)


_UNKNOWN_OWNED=[]  # Strong refs for unknown startup cleanup; no silent adoption.


class NativeActorHelperSupervisor:
    """Trusted startup factory input only; never a client request dictionary.

    Fresh generation follows verified owned retirement. A failed carrier
    request is never automatically reissued: its result may already exist.
    """
    def __init__(self, config):
        if type(config) is not NativeActorServiceConfig:
            _deny('NATIVE_HELPER_NATIVE_CONFIG_REQUIRED')
        # Reuse original producer validation without issuing a session or IPC.
        if (not isinstance(config.root,Path) or not config.root.is_absolute()
                or type(config.logical_profile) is not str or not config.logical_profile.strip()
                or type(config.session_seconds) is not int or not 1<=config.session_seconds<=3600):
            _deny('NATIVE_HELPER_NATIVE_CONFIG_REQUIRED')
        self.config=config; self.sid=owner.current_windows_owner_sid()
        self.root=_safe_path(config.root)
        if not self.root.exists(): owner.create_owner_only_directory(self.root)
        owner.verify_owner_only(self.root,self.sid)
        self._lock=threading.RLock(); self._process=None; self._endpoint=None
        self._generation_root=None; self._nonce=None; self.history=[]
        self._closed=False; self._unknown=False
        self._client=None; self.client_history=[]

    def _retire(self, seconds):
        if self._process is not None:
            try: facts=self._process.stop(seconds)
            except BaseException:
                self._unknown=True
                raise
            self.history.append(facts)
            self._process=None; self._endpoint=None

    def endpoint(self, *, timeout_seconds=5):
        timeout=_bound(timeout_seconds)
        with self._lock:
            if self._closed or self._unknown or _UNKNOWN_OWNED:
                _deny('NATIVE_HELPER_CLOSED_OR_UNKNOWN')
            if owner.current_windows_owner_sid()!=self.sid:
                _deny('NATIVE_HELPER_WRONG_OWNER')
            owner.verify_owner_only(_safe_path(self.root),self.sid)
            if self._process is not None:
                facts=self._process.facts()
                if not facts['process_signaled'] and self._endpoint is not None:
                    return self._endpoint
                self._retire(timeout)
            self._nonce=secrets.token_hex(16)
            self._generation_root=self.root/('helper-'+self._nonce)
            owner.create_owner_only_directory(self._generation_root)
            argv=[sys.executable,'-X','utf8','-B','-m','shared_platform.native_actor_helper_worker',
                  str(self._generation_root),self.config.logical_profile,
                  str(self.config.session_seconds),self._nonce]
            self._process=_OwnedProcess(argv,Path(__file__).resolve().parents[1])
            deadline=time.monotonic()+timeout
            try:
                while time.monotonic()<deadline:
                    facts=self._process.facts()
                    if facts['process_signaled']: _deny('NATIVE_HELPER_STARTUP_EXITED')
                    path=_safe_path(self._generation_root/'ready.json')
                    if path.exists():
                        owner.verify_owner_only(path,self.sid,protected=False)
                        with path.open('rb') as stream: raw=stream.read(4097)
                        if not 0<len(raw)<=4096: _deny('NATIVE_HELPER_READY_INVALID')
                        row=json.loads(raw)
                        if (type(row) is not dict or set(row)!=
                                {'schema_version','nonce','pid','owner_sid','instance_id','pipe_name'}
                                or raw!=canonical_bytes(row)
                                or row['schema_version']!='native-owner-helper-ready/v1'
                                or row['nonce']!=self._nonce or row['pid']!=self._process.pid
                                or row['owner_sid']!=self.sid
                                or type(row['instance_id']) is not str
                                or len(row['instance_id'])!=32
                                or any(c not in '0123456789abcdef' for c in row['instance_id'])
                                or row['pipe_name']!=r'\\.\pipe\OrbitNativeOwner-'+row['instance_id']):
                            _deny('NATIVE_HELPER_READY_INVALID')
                        self._endpoint=NativeLauncherEndpoint(row['pipe_name'],row['pid'],self.sid,
                                row['instance_id'],self._generation_root/'actor')
                        if self._process.facts()['process_signaled']:
                            _deny('NATIVE_HELPER_STARTUP_EXITED')
                        return self._endpoint
                    time.sleep(.01)
                _deny('NATIVE_HELPER_STARTUP_TIMEOUT')
            except BaseException:
                self._retire(timeout)
                raise

    def replace_owned(self, *, timeout_seconds=5):
        """Explicit unhealthy recovery; never retries a session request."""
        with self._lock:
            if self._closed or self._unknown: _deny('NATIVE_HELPER_CLOSED_OR_UNKNOWN')
            self._retire(_bound(timeout_seconds))
            return self.endpoint(timeout_seconds=timeout_seconds)

    def request_carrier(self, *, timeout_seconds=2):
        """Retire/recreate on transport failure without resending the request.

        Even a missing ACK may follow creation of a carrier. The original
        error remains an unknown request result; recovery only makes a fresh
        owned endpoint available for a subsequent explicit local request.
        """
        timeout=_bound(timeout_seconds)
        with self._lock:
            endpoint=self.endpoint(timeout_seconds=timeout)
            nonce=secrets.token_hex(16)
            root=self.root/('client-'+nonce)
            owner.create_owner_only_directory(root)
            row={'schema_version':'native-owner-client-request/v1','nonce':nonce,
                'owner_sid':self.sid,'server_pid':endpoint.process_id,
                'instance_id':endpoint.instance_id,'pipe_name':endpoint.pipe_name,
                'owner_root':str(endpoint.owner_root),'timeout_seconds':timeout}
            with (root/'request.json').open('xb') as stream:
                stream.write(canonical_bytes(row))
            owner.verify_owner_only(root/'request.json',self.sid,protected=False)
            self._client=_OwnedProcess(_client_argv(root,nonce),Path(__file__).resolve().parents[1])
            try:
                # Three original bounded write/read/write operations. Native
                # CreateFile has no WaitNamedPipe/retry; the whole child also
                # has an explicit upper deadline and actual owned retirement.
                deadline=time.monotonic()+3*timeout+.5
                while not self._client.facts()['process_signaled']:
                    if time.monotonic()>=deadline:
                        _deny('NATIVE_HELPER_CLIENT_DEADLINE_UNKNOWN')
                    time.sleep(.01)
                client=self._client
                self._retire_client(timeout)
                if client.retired['exit_code']!=0:
                    _deny('NATIVE_HELPER_CLIENT_RESULT_UNKNOWN')
                result=_read_metadata(root/'result.json',self.sid)
                if (set(result)!={'schema_version','nonce','client_pid','server_pid','instance_id','filename'}
                        or result['schema_version']!='native-owner-client-result/v1'
                        or result['nonce']!=nonce or result['client_pid']!=client.pid
                        or result['server_pid']!=endpoint.process_id
                        or result['instance_id']!=endpoint.instance_id):
                    _deny('NATIVE_HELPER_CLIENT_RESULT_INVALID')
                owner.LocalOperatorSessions._handoff_name(result['filename'])
                path=_safe_path(endpoint.owner_root/result['filename'])
                owner.verify_owner_only(endpoint.owner_root,self.sid)
                owner.verify_owner_only(path,self.sid,protected=False)
                return path
            except BaseException:
                self._retire_client(timeout)
                self._retire(timeout)
                self.endpoint(timeout_seconds=timeout)
                raise

    def authenticate_carrier(self, carrier, *, expected_endpoint, expected_birth, timeout_seconds=2):
        """One real session validation; no automatic replay or session issuance."""
        from shared_platform import native_actor_authentication as auth
        timeout=_bound(timeout_seconds)
        if not isinstance(carrier,Path) or type(expected_endpoint) is not NativeLauncherEndpoint:
            _deny('NATIVE_ACTOR_SERVICE_READER_REQUIRED')
        with self._lock:
            endpoint=self.endpoint(timeout_seconds=timeout)
            if (endpoint!=expected_endpoint or type(expected_birth) is not int
                    or auth.process_birth(self._process.process)!=expected_birth):
                _deny('NATIVE_ACTOR_SERVER_IDENTITY_CHANGED')
            path=_safe_path(carrier)
            if path.parent!=_safe_path(endpoint.owner_root):
                _deny('NATIVE_ACTOR_CARRIER_WRONG_GENERATION')
            owner.LocalOperatorSessions._handoff_name(path.name)
            owner.verify_owner_only(endpoint.owner_root,self.sid)
            owner.verify_owner_only(path,self.sid,protected=False)
            auth.verified_profile(self.config,endpoint)
            nonce=secrets.token_hex(16); root=self.root/('client-'+nonce)
            owner.create_owner_only_directory(root)
            row={'schema_version':auth.CLIENT_SCHEMA,'nonce':nonce,'owner_sid':self.sid,
                'server_pid':endpoint.process_id,'server_birth_100ns':expected_birth,
                'instance_id':endpoint.instance_id,'logical_profile':self.config.logical_profile,
                'pipe_name':endpoint.pipe_name,'owner_root':str(endpoint.owner_root),
                'filename':path.name,'timeout_seconds':timeout}
            with (root/'request.json').open('xb') as stream:
                stream.write(canonical_bytes(row))
            owner.verify_owner_only(root/'request.json',self.sid,protected=False)
            try:
                self._client=_OwnedProcess(_client_argv(root,nonce),Path(__file__).resolve().parents[1])
                deadline=time.monotonic()+3*timeout+.5
                while not self._client.facts()['process_signaled']:
                    if time.monotonic()>=deadline:
                        _deny('NATIVE_HELPER_CLIENT_DEADLINE_UNKNOWN')
                    time.sleep(.01)
                client=self._client
                self._retire_client(timeout)
                if client.retired['exit_code']!=0:
                    _deny('NATIVE_HELPER_CLIENT_RESULT_UNKNOWN')
                result=_read_metadata(root/'result.json',self.sid)
                expected={'schema_version':auth.CLIENT_RESULT_SCHEMA,'nonce':nonce,
                    'client_pid':client.pid,'server_pid':endpoint.process_id,
                    'server_birth_100ns':expected_birth,'instance_id':endpoint.instance_id}
                if (set(result)!=set(expected)|{'reply'}
                        or any(type(result[key]) is not type(value) or result[key]!=value
                               for key,value in expected.items())
                        or self._process.facts()['process_signaled']
                        or auth.process_birth(self._process.process)!=expected_birth):
                    _deny('NATIVE_HELPER_CLIENT_RESULT_INVALID')
                reply=auth.check_result(result['reply'],row)
            except BaseException:
                self._retire_client(timeout)
                self._retire(timeout)
                # Recovery is for a future explicit request, never this one.
                self.endpoint(timeout_seconds=timeout)
                raise
            # Known rejection is a completed RPC, not an unknown transport.
            return auth.verified_identity(reply,self.config,endpoint)

    def _retire_client(self, seconds):
        if self._client is not None:
            try: facts=self._client.stop(seconds)
            except BaseException:
                self._unknown=True
                raise
            self.client_history.append(facts)
            self._client=None

    def close(self, *, timeout_seconds=2):
        with self._lock:
            self._closed=True
            self._retire_client(_bound(timeout_seconds))
            self._retire(_bound(timeout_seconds))

    def __enter__(self): return self
    def __exit__(self,*unused): self.close()
