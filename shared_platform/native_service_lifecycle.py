"""One startup-owned lifetime for explicit new tasks and native decisions."""
from contextlib import ExitStack, contextmanager

SCOPE = 'explicit-new-task-and-decision/v1'
POST_PATHS = frozenset('/api/product-workspace/native-final/' + action
                       for action in ('prepare', 'decision', 'resume', 'readback'))
_RETIREMENT_UNKNOWN = []  # Strong owner refs; an unknown lifetime cannot be replaced.


@contextmanager
def installed_native_services(application, http, operations):
    """Never initialize a schema, scan historical tasks or enable the old worker."""
    from shared_platform.native_common_edit_boundary import install_service_boundary, restore_service_boundary
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    from shared_platform.native_windows_actor import NativeActorServiceConfig
    from shared_platform.native_task_preparation import install_explicit_new_task_preparation
    from shared_platform.release_store import default_release_store
    from shared_platform.native_common_technical_execution import _schema_installed
    from shared_platform.native_sole_final_decision import check_schema

    if (_RETIREMENT_UNKNOWN or getattr(http, 'native_service_retirement_unknown', None)
            or getattr(operations, 'native_service_retirement_unknown', None)):
        raise ValueError('NATIVE_SERVICE_RETIREMENT_UNKNOWN')
    store = default_release_store()
    # Existing-schema inspection only, before registering any worker/helper.
    # The migration itself is an explicit maintenance operation.
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        if not _schema_installed(db):
            raise ValueError('COMMON_TECHNICAL_SCHEMA_NOT_INSTALLED')
        check_schema(db)

    fields = ('_COMMON_STANDING_POLICY_READER', '_COMMON_DETAIL_OBSERVER_FACTORY',
              '_COMMON_SIGNING_CONTEXT_CONFIG', '_NATIVE_FINAL_SERVICE')
    previous = {name: getattr(application, name) for name in fields}
    absent = object()
    old_final = getattr(http, 'native_final_review', absent)
    old_worker = getattr(operations, 'new_task_worker', absent)
    def restore_application():
        for name, value in previous.items():
            setattr(application, name, value)
    def restore_http():
        if old_final is absent:
            if hasattr(http, 'native_final_review'): del http.native_final_review
        else: http.native_final_review = old_final
    def close_new_worker():
        current = getattr(operations, 'new_task_worker', absent)
        try:
            if current is not absent and current is not old_worker:
                try:
                    current.close()
                    thread = getattr(current, 'thread', None)
                    if ((thread is not None and thread.is_alive())
                            or any(not future.done() for future in getattr(current, 'inflight', {}).values())):
                        raise RuntimeError('NATIVE_NEW_TASK_RETIREMENT_UNKNOWN')
                except BaseException:
                    _RETIREMENT_UNKNOWN.append(current)
                    http.native_service_retirement_unknown = current
                    operations.native_service_retirement_unknown = current
                    raise
        finally:
            if old_worker is absent:
                if hasattr(operations, 'new_task_worker'): del operations.new_task_worker
            else: operations.new_task_worker = old_worker
    def close_final(final):
        try:
            final.close()
        except BaseException:
            # Includes execution-thread/client/helper unknown retirement. Keep
            # the entire original owner, not merely a PID or a success flag.
            _RETIREMENT_UNKNOWN.append(final)
            http.native_service_retirement_unknown = final
            operations.native_service_retirement_unknown = final
            raise
    with ExitStack() as stack:
        # Register restoration before the first factory: partial initialization
        # also restores its dependencies, not only normal serve shutdown.
        stack.callback(restore_application)
        config = application.R3_STARTUP_CONFIG
        application._install_service_common_standing_policy(config)
        application._install_service_common_detail_observer(config)
        application._install_service_common_signing_context(config)
        old_boundary, boundary = install_service_boundary(config)
        stack.callback(restore_service_boundary, old_boundary, boundary)
        stack.callback(close_new_worker)
        worker = install_explicit_new_task_preparation(operations)
        final = NativeSoleFinalService(store,
            NativeActorServiceConfig(operations.profile.data_root / 'native-final-owner', 'Kyle'),
            new_decision_execution_enabled=True, operations=operations)
        stack.callback(close_final, final)
        final.start()
        stack.callback(restore_http)
        http.native_final_review = final
        application._NATIVE_FINAL_SERVICE = final
        yield {'scope': SCOPE, 'worker': worker, 'final': final,
               'legacy_worker_enabled': False, 'historical_task_scan': False}
