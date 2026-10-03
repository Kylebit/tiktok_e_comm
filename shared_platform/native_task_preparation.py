"""Preparation for explicit new HTTP tasks; no historical dispatcher or paid agent.

The parent invokes the original R1 producer while local directory handles prevent
renames/reparse replacement. A child result never becomes a prepared receipt.
"""
from contextlib import contextmanager
import ctypes as C
import json
import os
import re
import stat
from pathlib import Path, PureWindowsPath
import threading

from shared_platform.operations_runtime import OperationsWorker, runtime_matches


class R1FilesystemUnavailable(ValueError):
    pass


class NativeR1FilesystemBoundary:
    """Service-owned parent boundary, scoped to this runtime's paths and release."""
    def __init__(self, profile):
        self.profile = profile
        self._lock = threading.RLock()
        self._active = threading.local()

    def verified_for(self, engine, task, token, profile):
        return (profile is self.profile and getattr(self._active,'claim',None)
                == (id(engine),task['task_id'],token))

    def _check_leaf(self,path):
        try:value=Path(path).lstat()
        except FileNotFoundError:return
        if (not stat.S_ISREG(value.st_mode) or value.st_nlink!=1
                or getattr(value,'st_file_attributes',0)&0x400):
            raise R1FilesystemUnavailable('R1_PARENT_FILE_REPARSE_OR_MULTILINK')

    def _configured_local_directory(self, supplied, *, release_parent=None):
        """Lexical service bindings, not a caller-selected drive or resolved link."""
        path=PureWindowsPath(supplied)
        roots=[PureWindowsPath(self.profile.root),PureWindowsPath(self.profile.data_root)]
        if release_parent is not None:roots.append(PureWindowsPath(release_parent))
        if (not path.is_absolute() or not re.fullmatch(r'[A-Za-z]:',path.drive)
                or '..' in path.parts or any(not root.is_absolute()
                or not re.fullmatch(r'[A-Za-z]:',root.drive) or '..' in root.parts for root in roots)
                or not any(path.is_relative_to(root) for root in roots)):
            raise R1FilesystemUnavailable('R1_CONFIGURED_LOCAL_DIRECTORY_REQUIRED')
        return path

    @contextmanager
    def _pin(self, paths, *, release_parent=None):
        if os.name != 'nt':
            raise R1FilesystemUnavailable('R1_WINDOWS_DIRECTORY_PIN_UNAVAILABLE')
        from ctypes import wintypes as W

        class FileTime(C.Structure):
            _fields_ = [('low', W.DWORD), ('high', W.DWORD)]
        class Information(C.Structure):
            _fields_ = [('attributes', W.DWORD), ('created', FileTime),
                ('accessed', FileTime), ('written', FileTime),
                ('volume', W.DWORD), ('size_high', W.DWORD), ('size_low', W.DWORD),
                ('links', W.DWORD), ('index_high', W.DWORD), ('index_low', W.DWORD)]
        kernel = C.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [W.LPCWSTR,W.DWORD,W.DWORD,C.c_void_p,W.DWORD,W.DWORD,W.HANDLE]
        kernel.CreateFileW.restype = W.HANDLE
        kernel.GetFileInformationByHandle.argtypes = [W.HANDLE,C.POINTER(Information)]
        kernel.GetFileInformationByHandle.restype = W.BOOL
        kernel.CloseHandle.argtypes = [W.HANDLE]
        kernel.CloseHandle.restype = W.BOOL
        handles = []; identities = []; seen = set()
        try:
            for supplied in paths:
                # abspath normalizes syntax without resolving a junction.
                self._configured_local_directory(supplied,release_parent=release_parent)
                path = Path(os.path.abspath(supplied))
                for directory in [*reversed(path.parents), path]:
                    key = str(directory).casefold()
                    if key in seen: continue
                    # The parent has already pinned each ancestor before creation.
                    if not directory.exists(): directory.mkdir()
                    handle = kernel.CreateFileW(str(directory), 0x80, 0x3, None, 3,
                                               0x02200000, None)
                    if handle in (None, C.c_void_p(-1).value):
                        raise R1FilesystemUnavailable('R1_DIRECTORY_PIN_OPEN_FAILED')
                    handles.append(handle)
                    value = Information()
                    if (not kernel.GetFileInformationByHandle(handle, C.byref(value))
                            or value.attributes & 0x400 or not value.attributes & 0x10
                            or value.links != 1):
                        raise R1FilesystemUnavailable('R1_DIRECTORY_PIN_REPARSE_OR_LINK')
                    seen.add(key)
                    identities.append((handle, (value.volume,value.index_high,value.index_low)))
            yield
            for handle, identity in identities:
                value = Information()
                if (not kernel.GetFileInformationByHandle(handle, C.byref(value))
                        or value.attributes & 0x400 or not value.attributes & 0x10
                        or value.links != 1
                        or (value.volume,value.index_high,value.index_low) != identity):
                    raise R1FilesystemUnavailable('R1_DIRECTORY_PIN_IDENTITY_CHANGED')
        finally:
            for handle in reversed(handles): kernel.CloseHandle(handle)

    @contextmanager
    def hold(self, engine, task, token, profile):
        from shared_platform import workbench_publication_native as native
        from shared_platform import publication_rounds, release_store
        from modules.sourcing import new_product_workbench as workbench
        if profile is not self.profile or engine.release != task.get('version'):
            raise R1FilesystemUnavailable('R1_PARENT_RUNTIME_CONFLICT')
        if not re.fullmatch(r'TASK-[0-9]{8}-[0-9]+',str(task.get('task_id',''))):
            raise R1FilesystemUnavailable('R1_PARENT_TASK_ID_REQUIRED')
        offer, _ = native._scope(task)
        release = {'code_version':profile.version,'environment':profile.environment,
                   'manifest_digest':profile.manifest_digest}
        if release != task['version'] or not runtime_matches(profile):
            raise R1FilesystemUnavailable('R1_PARENT_RELEASE_CHANGED')
        # Reject a forged/stale task before even creating its local directory.
        if token is None:
            original=engine.read_r1_technical_recovery(task['task_id'])['task']
            if original!=task or task['task_id'] not in engine.explicit_new_post_task_ids():
                raise R1FilesystemUnavailable('R1_PARENT_RECOVERY_ORIGIN_CHANGED')
        else:
            with engine.transaction() as db:
                row = engine._lease(db,task['task_id'],token)
                if json.loads(row['scope_json']) != task['scope']:
                    raise R1FilesystemUnavailable('R1_PARENT_TASK_OR_LEASE_CHANGED')
        native._server(profile)
        if (Path(workbench.STATE_DIR) != profile.root / 'data/new_product_workbench'
                or Path(publication_rounds.REPORTS_ROOT) != profile.root / 'reports/product-preparation'):
            raise R1FilesystemUnavailable('R1_PARENT_SOURCE_DIRECTORY_CONFLICT')
        release_parent=release_store.default_release_store().path.parent
        with self._lock, self._pin([profile.data_root / 'artifacts' / task['task_id'],
                                   Path(workbench.STATE_DIR),
                                   release_parent,
                                   profile.root / 'outputs/image_suite_from_miaoshou' / offer,
                                   profile.root / 'reports/product-preparation' / offer],
                                  release_parent=release_parent):
            if token is not None:
                with engine.transaction() as db:
                    row = engine._lease(db,task['task_id'],token)
                    if (json.loads(row['scope_json']) != task['scope']
                            or json.loads(row['version_json']) != release):
                        raise R1FilesystemUnavailable('R1_PARENT_TASK_OR_LEASE_CHANGED')
            directory=profile.root/'reports/product-preparation'/offer
            for name in ('first-review.json','first-review.json.tmp','first-review-candidate-plan.json',
                         'first-review-image-plan.json','round1-auto-decision.json','round1-approved-snapshot.json'):
                self._check_leaf(directory/name)
            self._check_leaf(Path(workbench.STATE_DIR)/(offer+'.json'))
            previous = getattr(self._active,'claim',None)
            self._active.claim = (id(engine),task['task_id'],token)
            try:
                yield self
            finally:
                self._active.claim = previous

    def run(self, engine, task, token, profile, *, facts_adapter=None):
        from shared_platform import operations_publication_prepare as preparation
        try:
            with self.hold(engine,task,token,profile) as pinned:
                if facts_adapter is not None:
                    from shared_platform.native_parent_facts import NativeParentFactsAdapter
                    if (type(facts_adapter) is not NativeParentFactsAdapter
                            or facts_adapter.engine is not engine or facts_adapter.profile is not profile
                            or facts_adapter.boundary is not pinned):
                        raise R1FilesystemUnavailable('R1_PARENT_FACTS_ADAPTER_CONFLICT')
                return preparation.run(engine,task,token,profile,bridge=facts_adapter,filesystem_boundary=pinned)
        except (R1FilesystemUnavailable, OSError) as error:
            engine.observe_reconciliation(task['task_id'],token,
                '商品准备目录暂不可用，保留原任务核对：'+str(error))
            return True

    def recover(self,engine,task,profile):
        from shared_platform import operations_publication_prepare as preparation
        with self.hold(engine,task,None,profile):
            return preparation.recover_technical(engine,task,profile,filesystem_boundary=self)


class ExplicitNewTaskWorker(OperationsWorker):
    """Only IDs created by this service's explicit POST may be considered."""
    def __init__(self, engine, profile, boundary, *, profit_config=None, delisting_config=None):
        from shared_platform import operations_publication
        super().__init__(engine,profile,{})
        from shared_platform.native_parent_facts import NativeParentFactsAdapter
        self.facts_adapter = NativeParentFactsAdapter(engine,profile,boundary,worker_id=self.worker_id)
        from shared_platform.native_parent_images import NativeParentImagesAdapter
        self.images_adapter = NativeParentImagesAdapter(engine,profile,boundary)
        run, observe = operations_publication.bindings(bridge=self.facts_adapter,filesystem_boundary=boundary,
                                                      image_bridge=self.images_adapter)
        self.adapters['publication'], self.observe = run, observe
        from shared_platform.native_profit_preparation import bindings, NativeProfitServiceConfig
        self.adapters['profit'] = bindings(engine, profile, profit_config)
        self.profit_connected = type(profit_config) is NativeProfitServiceConfig
        from shared_platform.native_delisting_preparation import bindings as delisting_bindings, NativeDelistingServiceConfig
        self.adapters['delisting'] = delisting_bindings(engine,profile,delisting_config)
        self.delisting_connected = type(delisting_config) is NativeDelistingServiceConfig
        self.boundary = boundary
        self._admission_lock = threading.RLock()
        self._task_ids = (set(engine.explicit_new_post_task_ids()) | set(engine.explicit_new_post_profit_task_ids())
                          | set(engine.explicit_new_post_delisting_task_ids()))
        if len(self._task_ids) > 32:
            raise ValueError('NATIVE_NEW_POST_RECOVERY_CAPACITY_EXCEEDED')
        self.child_recovery_seen = set()

    def create(self, payload):
        with self._admission_lock:
            if self.stop_event.is_set(): raise ValueError('新任务准备服务已停止')
            for task_id in list(self._task_ids):
                if self.engine.get(task_id)['execution_state'] in {'completed','cancelled','failed'}:
                    self._task_ids.remove(task_id)
            if payload.get('template') not in {'publication', 'profit', 'delisting'}:
                return self.engine.create(payload)
            task, created = self.engine.create_for_explicit_post(payload,allow_new=len(self._task_ids)<32)
            if created: self._task_ids.add(task['task_id'])
        if created: self.wake()
        return task

    def owns(self, task_id):
        with self._admission_lock: return task_id in self._task_ids

    def status(self):
        value = super().status()
        with self._admission_lock: value['explicit_new_task_count'] = len(self._task_ids)
        return {**value,'scope':'EXPLICIT_NEW_POST_PUBLICATION_ONLY',
                'historical_task_scan':False,'paid_image_executor_connected':True,
                'profit_scope':'EXPLICIT_NEW_POST_PROFIT_READONLY',
                'profit_executor_connected':self.profit_connected,
                'profit_readiness':'BOUND_INPUTS_AND_REPORTS_NOT_VERIFIED' if self.profit_connected else 'NATIVE_PROFIT_SERVICE_BINDING_REQUIRED',
                'delisting_scope':'EXPLICIT_NEW_POST_DELIST_EXACT_SCOPE',
                'delisting_executor_connected':self.delisting_connected,
                'delisting_readiness':'EXACT_IDENTITY_AND_PROVIDER_NOT_VERIFIED' if self.delisting_connected else 'NATIVE_DELIST_SERVICE_BINDING_REQUIRED',
                'paid_image_readiness':'UNVERIFIED_UNTIL_TASK_POLICY_PROVIDER_AND_BUDGET_CHECKS'}

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                if not runtime_matches(self.profile):
                    self._mark('source_mismatch')
                else:
                    self.engine.register_executor(self.worker_id,['publication','profit','delisting'],self.engine.release,ttl=30)
                    for tid, future in list(self.inflight.items()):
                        if future.done(): del self.inflight[tid]
                    with self._admission_lock: ids = tuple(self._task_ids)
                    for tid in ids:
                        task = self.engine.get(tid)  # never dashboard/history admission
                        if task['version'] != self.engine.release: continue
                        if (task['template']=='publication' and task['execution_state']=='reconciliation_required'
                                and (task.get('checkpoint') or {}).get('r1_auto_intent')
                                and tid not in self.r1_recovery_seen):
                            self.r1_recovery_seen.add(tid)
                            self.boundary.recover(self.engine,task,self.profile)
                            task=self.engine.get(tid)
                        if (task['template']=='publication' and task['execution_state']=='reconciliation_required'
                                and (task.get('checkpoint') or {}).get('facts_attempt')
                                and tid not in self.child_recovery_seen):
                            self.child_recovery_seen.add(tid)
                            if self.facts_adapter.queue_retained_recovery(task):
                                task=self.engine.get(tid)
                        if (task['template']=='publication' and task['execution_state']=='reconciliation_required'
                                and task['current_step']=='images'
                                and (task.get('checkpoint') or {}).get('image_attempt')):
                            if self.images_adapter.queue_retained_recovery(task):
                                task=self.engine.get(tid)
                        if self.observe and task['template']=='publication' and task['execution_state'] in {'waiting_user','waiting_domain'}:
                            self.observe(self.engine,task,self.profile)
                            task = self.engine.get(tid)
                        if len(self.inflight) >= 2: break
                        if tid in self.inflight or task['execution_state'] != 'queued': continue
                        lease = self.engine.claim(tid,self.worker_id,ttl=60)
                        if lease: self.inflight[tid] = self.pool.submit(self._run,tid,lease['lease_token'])
                    self._mark('polling')
            except Exception as error:
                self._mark('error',error_type=type(error).__name__)
            self.wake_event.wait(self.interval)
            self.wake_event.clear()


def install_explicit_new_task_preparation(runtime):
    """Startup-owned local preparation; legacy worker and paid paths stay separate."""
    if getattr(runtime,'new_task_worker',None) is not None:
        raise ValueError('NATIVE_NEW_TASK_WORKER_ALREADY_INSTALLED')
    boundary = NativeR1FilesystemBoundary(runtime.profile)
    worker = ExplicitNewTaskWorker(runtime.engine,runtime.profile,boundary,
        profit_config=getattr(runtime,'native_profit_config',None),
        delisting_config=getattr(runtime,'native_delisting_config',None))
    runtime.new_task_worker = worker
    worker.start()
    return worker
