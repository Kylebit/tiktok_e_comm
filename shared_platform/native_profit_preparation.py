"""Exact new profit tasks using the existing stable read-only finance producer.

Only the deployment launcher supplies this binding. It does not authorize old
tasks, provider writes, request replay or guessed financial inputs.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import stat

from shared_platform.operations_runtime import ControlledAgentBridge, runtime_matches

PROFIT_SCOPE = 'EXPLICIT_NEW_POST_PROFIT_READONLY'


def _regular(path):
    path = Path(path)
    leaf = path.lstat()
    if (not stat.S_ISREG(leaf.st_mode) or leaf.st_nlink != 1
            or getattr(leaf, 'st_file_attributes', 0) & 0x400):
        raise ValueError('NATIVE_PROFIT_FIXED_FILE_INVALID')
    for parent in path.parents:
        if getattr(parent.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('NATIVE_PROFIT_FIXED_ROOT_INVALID')
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class NativeProfitServiceConfig:
    profile: object
    root: Path
    executable: Path
    settings_sha256: str
    executable_sha256: str

    @classmethod
    def capture(cls, profile, root, executable):
        if root is None or executable is None:
            raise ValueError('NATIVE_PROFIT_SERVICE_BINDING_REQUIRED')
        root, executable = Path(root), Path(executable)
        if not root.is_absolute() or not executable.is_absolute():
            raise ValueError('NATIVE_PROFIT_FIXED_BINDING_REQUIRED')
        settings = root / 'config/settings.json'
        if not settings.is_file():
            raise ValueError('NATIVE_PROFIT_SETTINGS_REQUIRED')
        if not executable.is_file():
            raise ValueError('NATIVE_PROFIT_FIXED_AGENT_EXECUTABLE_REQUIRED')
        return cls(profile, root, executable, _regular(settings), _regular(executable))

    def check(self, engine, task, token, profile):
        if (profile is not self.profile or profile.environment != 'stable'
                or task['template'] != 'profit' or task['version'] != engine.release
                or engine.release != {'code_version': profile.version,
                    'environment': profile.environment, 'manifest_digest': profile.manifest_digest}
                or not runtime_matches(profile)):
            raise ValueError('NATIVE_PROFIT_RUNTIME_CHANGED')
        if (_regular(self.root / 'config/settings.json') != self.settings_sha256
                or _regular(self.executable) != self.executable_sha256):
            raise ValueError('NATIVE_PROFIT_FIXED_BINDING_CHANGED')
        with engine.transaction() as db:
            row = engine._lease(db, task['task_id'], token)
            if (json.loads(row['scope_json']) != task['scope']
                    or json.loads(row['version_json']) != engine.release):
                raise ValueError('NATIVE_PROFIT_TASK_OR_LEASE_CHANGED')
        if task['task_id'] not in engine.explicit_new_post_profit_task_ids():
            raise ValueError('NATIVE_PROFIT_NEW_POST_GRANT_REQUIRED')


class _LeasedProfitBridge(ControlledAgentBridge):
    def __init__(self, config, engine, token):
        super().__init__(str(config.executable), config.profile)
        self.config, self.engine, self.token = config, engine, token

    def execute_monthly(self, task, output_dir, notes, *, timeout=1800):
        # Recheck after the original adapter's durable attempt reservation and
        # immediately before its original fixed subprocess entry.
        self.config.check(self.engine, task, self.token, self.profile)
        bound_source = json.dumps({'schema_version': 'native-profit-readonly-source/v1',
            'configuration_root': str(self.config.root),
            'settings_path': str(self.config.root / 'config/settings.json'),
            'settings_sha256': self.config.settings_sha256,
            'request_scope': task['request_scope'],
            'credential_refresh': False, 'business_writes': False}, sort_keys=True)
        return super().execute_monthly(task, output_dir,
            [*notes, 'Service-owned read-only source binding: ' + bound_source], timeout=timeout)


def bindings(engine, profile, config):
    """Missing capabilities block this exact task; they never grant fallback."""
    def run(current_engine, task, token, current_profile):
        if current_engine is not engine or current_profile is not profile:
            raise ValueError('NATIVE_PROFIT_SERVICE_RUNTIME_CONFLICT')
        if type(config) is not NativeProfitServiceConfig:
            engine.fail(task['task_id'], token, 'NATIVE_PROFIT_SERVICE_BINDING_REQUIRED')
            return
        config.check(engine, task, token, profile)
        from shared_platform.operations_profit_scope import resolve_scope
        original = task.get('request_scope')
        if not isinstance(original, dict):
            raise ValueError('NATIVE_PROFIT_ORIGINAL_REQUEST_REQUIRED')
        try:
            scope = resolve_scope(original, config.root)
        except ValueError as error:
            engine.fail(task['task_id'], token, '利润范围无法绑定：' + str(error))
            return
        checkpoint = task.get('checkpoint') or {}
        if task['current_step'] == 'coverage' and not checkpoint.get('scope_resolved'):
            engine.bind_scope(task['task_id'], token, scope)
            engine.record_checkpoint(task['task_id'], token, {
                **checkpoint, 'scope_resolved': True, 'scope_config_root': str(config.root)})
            task = engine.get(task['task_id'])
        elif scope != task['scope'] or (task['current_step'] == 'coverage'
                and checkpoint.get('scope_config_root') != str(config.root)):
            raise ValueError('NATIVE_PROFIT_RESOLVED_SCOPE_CHANGED')
        config.check(engine, task, token, profile)
        from shared_platform.workbench_profit_adapter import adapter
        return adapter(_LeasedProfitBridge(config, engine, token))(engine, task, token, profile)
    return run
