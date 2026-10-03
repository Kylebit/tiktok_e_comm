"""Server-owned operations binding. One dispatcher per HTTP service instance."""
import os
import threading
from types import SimpleNamespace
from shared_platform.operations_runtime import RuntimeProfile, OperationsWorker, ControlledAgentBridge
from shared_platform.workbench_engine import WorkbenchEngine

_guard = threading.Lock()


def round1_review(runtime, offer, server_module):
    """Resolve only the exact prepared reference held by this version's task."""
    from shared_platform import round1_workspace
    def reference(task):
        current=(task.get('checkpoint') or {}).get('native_preparation') or {}
        if current.get('prepared_reference'):
            return current['prepared_reference']
        facts=next((step for step in task.get('steps',[])
                    if step.get('key')=='facts' and step.get('state')=='completed'),None)
        return ((facts or {}).get('checkpoint') or {}).get('native_r1',{}).get('prepared_reference')
    candidates = [t for t in runtime.engine.dashboard()['tasks'] if t['template']=='publication'
                  and t['scope'].get('offer_id')==offer and t['version']==runtime.engine.release
                  and t['execution_state'] not in {'completed','cancelled'}
                  and reference(t)]
    if not candidates:return None
    if len(candidates)!=1:raise ValueError('multiple tasks claim the same first-review preparation')
    ref=reference(candidates[0])
    return round1_workspace.status(server_module,offer,reference=ref)


def get_runtime(server, root, *, worker_enabled=False, resume_task_ids=()):
    with _guard:
        if getattr(server, 'operations_runtime', None) is not None:
            return server.operations_runtime
        profile = RuntimeProfile.capture(root)
        executable = os.environ.get('ORBIT_OPERATIONS_AGENT_EXECUTABLE')
        if worker_enabled:
            from pathlib import Path
            if not executable or not Path(executable).is_absolute() or not Path(executable).is_file():
                raise ValueError('worker requires an explicit absolute existing agent executable')
            if not isinstance(resume_task_ids, (tuple, list)) or any(
                    not isinstance(task_id, str) or not task_id.startswith('TASK-') for task_id in resume_task_ids):
                raise ValueError('resume_task_ids must contain exact task IDs')
            if len(set(resume_task_ids)) != len(resume_task_ids):
                raise ValueError('resume_task_ids must be unique')
        engine = WorkbenchEngine(profile.data_root / 'tasks.db', {'code_version': profile.version, 'environment': profile.environment, 'manifest_digest': profile.manifest_digest})
        if worker_enabled:
            for task_id in resume_task_ids:
                task = engine.get(task_id)
                if task['version'] != engine.release or task['execution_state'] not in {
                        'queued', 'executor_offline', 'waiting_user', 'waiting_domain'}:
                    raise ValueError('resume task is not an eligible exact-version waiting task: ' + task_id)
        if not worker_enabled:
            worker = OperationsWorker(engine, profile, {})
            runtime = SimpleNamespace(engine=engine, profile=profile, worker=worker, worker_enabled=False)
            server.operations_runtime = runtime
            return runtime
        from shared_platform.operations_publication import bindings as publication_bindings
        from shared_platform.workbench_delisting_adapter import run as delisting_run
        # Facts now requires a parent-owned, task-lease-bound R1 evidence
        # transport. A web-only server port is never a worker write channel.
        bridge = ControlledAgentBridge(executable, profile) if executable else None
        publication_run, publication_observe = publication_bindings(bridge)
        adapters = {'publication': publication_run}
        if profile.environment == 'stable':
            adapters['delisting'] = delisting_run
        if executable:
            from shared_platform.workbench_profit_adapter import adapter as profit_adapter
            from shared_platform.operations_profit_scope import resolve_scope
            from pathlib import Path
            execute_profit = profit_adapter(bridge)
            config_root = Path(os.environ.get('ORBIT_OPERATIONS_CONFIG_ROOT') or profile.root).resolve()
            def profit_run(engine,task,token,profile):
                if task['current_step']=='coverage' and not (task.get('checkpoint') or {}).get('scope_resolved'):
                    scope=resolve_scope(task['scope'],config_root)
                    engine.bind_scope(task['task_id'],token,scope)
                    engine.record_checkpoint(task['task_id'],token,{**(task.get('checkpoint') or {}),'scope_resolved':True,'scope_config_root':str(config_root)})
                    task=engine.get(task['task_id'])
                return execute_profit(engine,task,token,profile)
            adapters['profit'] = profit_run
        def observe(engine, task, profile):
            if task['template'] == 'publication':
                publication_observe(engine, task, profile)
        worker = OperationsWorker(engine, profile, adapters, observe=observe,
                                  admission_cutoff=engine.worker_admission_cutoff(),
                                  resume_task_ids=resume_task_ids)
        runtime = SimpleNamespace(engine=engine, profile=profile, worker=worker, worker_enabled=True)
        server.operations_runtime = runtime
        if adapters:
            worker.start()
        return runtime
