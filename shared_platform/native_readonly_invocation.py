"""Verified local integration controls for two native readonly proposal children.

Configuration is inspected in memory only. Auth is never opened. This is not
cloud absence proof, a hard file allowlist or proof of Windows sandbox behavior.
The generic prepare inherited-config guard retains its separate strict policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re

from shared_platform import agent_executable_binding as binary
from shared_platform.readonly_agent_config_guard import (
    ReadonlyAgentConfigBlocked, _identity, _path_metadata, _read_config,
)


# Exact installed bytes: Codex 0.160.0 local feature readback accepted these
# four names as false and the explicit MCP/notify/web-search keys. Generic
# --disable support or a version string alone cannot admit another binary.
_SUPPORTED_BINARY_SHA256 = frozenset({
    '37762753b554982eef1c109303d1be652b6397f1479e844794353a85650199c6',
})
_FEATURES = ('hooks', 'apps', 'plugins', 'multi_agent')
_MCP_NAME = re.compile(r'[A-Za-z0-9_-]{1,64}')
_MAX_MCP_NAMES = 32


def _blocked(reason):
    raise ReadonlyAgentConfigBlocked(reason)


def _metadata(path, fingerprints, *, directory=False, reject=False):
    info = _path_metadata(path, directory=directory)
    fingerprints[str(path)] = None if info is None else _identity(info)
    if reject and info is not None:
        _blocked('readonly_native_managed_layer_unverified')


def _names(value):
    if value.get('profile'):
        _blocked('readonly_native_profile_layer_unverified')
    servers = value.get('mcp_servers', {})
    if not isinstance(servers, dict) or len(servers) > _MAX_MCP_NAMES:
        _blocked('readonly_native_mcp_config_unverifiable')
    for name, server in servers.items():
        if (not isinstance(name, str) or not _MCP_NAME.fullmatch(name)
                or not isinstance(server, dict)
                or type(server.get('enabled', True)) is not bool):
            _blocked('readonly_native_mcp_config_unverifiable')
    for key in ('hooks', 'apps', 'plugins', 'plugin_marketplaces'):
        if key in value and not isinstance(value[key], (dict, list)):
            _blocked('readonly_native_integration_config_unverifiable')
    if 'notify' in value and (not isinstance(value['notify'], list)
            or any(not isinstance(item, str) for item in value['notify'])):
        _blocked('readonly_native_integration_config_unverifiable')
    features = value.get('features', {})
    if (not isinstance(features, dict)
            or any(key in features and type(features[key]) is not bool for key in _FEATURES)
            or features.get('remote_control')):
        _blocked('readonly_native_integration_layer_unverified')
    return set(servers)


def _capture(cwd):
    environment = dict(os.environ)
    if any(environment.get(key) for key in (
            'CODEX_PROFILE', 'CODEX_CONFIG', 'CODEX_CONFIG_FILE', 'CODEX_MANAGED_CONFIG')):
        _blocked('readonly_native_config_selector_unverified')
    home = Path(environment.get('CODEX_HOME') or str(Path.home()/'.codex'))
    root = Path(cwd)
    fingerprints = {}
    names = set()
    try:
        if (not home.is_absolute() or not root.is_absolute()
                or '..' in home.parts or '..' in root.parts
                or _path_metadata(root, directory=True) is None):
            _blocked('readonly_agent_config_path_unsafe')
        _metadata(home, fingerprints, directory=True)
        _metadata(root, fingerprints, directory=True)
        names.update(_names(_read_config(home/'config.toml', fingerprints, required=True)))
        folders = [root, *root.parents]
        if len(folders) > 32:
            _blocked('readonly_agent_config_path_unsafe')
        for folder in folders:
            local_home = folder/'.codex'
            _metadata(local_home, fingerprints, directory=True)
            local = _read_config(local_home/'config.toml', fingerprints)
            if local is not None:
                names.update(_names(local))
            # Bodies/manifests need not be opened when the verified runtime
            # disables their feature; ordinary path identity is still checked.
            _metadata(local_home/'hooks.json', fingerprints)
            _metadata(local_home/'plugins', fingerprints, directory=True)
        _metadata(home/'hooks.json', fingerprints)
        _metadata(home/'plugins', fingerprints, directory=True)
        if len(names) > _MAX_MCP_NAMES:
            _blocked('readonly_native_mcp_config_unverifiable')
        if os.name == 'nt':
            program_data = environment.get('ProgramData') or environment.get('PROGRAMDATA')
            if (not program_data or not Path(program_data).is_absolute()
                    or '..' in Path(program_data).parts):
                _blocked('readonly_agent_system_layer_path_unverified')
            system = Path(program_data)/'OpenAI/Codex'
        else:
            system = Path('/etc/codex')
        for path in [home/'managed_config.toml', home/'requirements.toml',
                     *(system/name for name in ('config.toml', 'requirements.toml', 'managed_config.toml'))]:
            _metadata(path, fingerprints, reject=True)
    except ReadonlyAgentConfigBlocked:
        raise
    except OSError:
        _blocked('readonly_agent_config_unreadable')
    if environment != dict(os.environ):
        _blocked('readonly_agent_environment_changed')
    return environment, fingerprints, tuple(sorted(names))


@dataclass(frozen=True, repr=False)
class NativeReadonlyInvocation:
    """No raw config or environment in repr/receipts; only builder creates plans."""
    executable: str
    cwd: Path
    _environment: dict = field(repr=False)
    _fingerprints: dict = field(repr=False)
    _names: tuple = field(repr=False)
    _binary_identity: tuple = field(repr=False)
    _binary_sha256: str = field(repr=False)

    def argv(self, schema):
        schema = Path(schema)
        if not schema.is_absolute() or '..' in schema.parts:
            _blocked('readonly_native_schema_path_unsafe')
        args = [self.executable, '--cd', str(self.cwd)]
        for feature in _FEATURES:
            args += ['--disable', feature]
        for name in self._names:
            args += ['--config', f'mcp_servers.{name}.enabled=false']
        return args + ['--config', 'notify=[]', '--config', 'web_search="disabled"',
            'exec', '--sandbox', 'read-only', '--ephemeral', '--json', '--color', 'never',
            '--output-schema', str(schema), '-']

    def verify_launch(self, argv, cwd):
        """Recheck right at Popen; return the unchanged captured environment."""
        try:
            if (Path(cwd) != self.cwd or self._binary_sha256 not in _SUPPORTED_BINARY_SHA256
                    or self._binary_identity != binary._identity(binary._safe_metadata(Path(self.executable)))
                    or list(argv) != self.argv(argv[-2])):
                _blocked('readonly_native_invocation_changed')
            environment, fingerprints, names = _capture(self.cwd)
            if (environment != self._environment or fingerprints != self._fingerprints
                    or names != self._names):
                _blocked('readonly_native_config_changed')
            if _path_metadata(Path(argv[-2])) is None:
                _blocked('readonly_native_schema_path_unsafe')
        except (OSError, ValueError, IndexError, TypeError):
            _blocked('readonly_native_invocation_changed')
        return dict(self._environment)


def prepare_readonly_invocation(executable, cwd):
    """Pure local preflight before reserving a new original child attempt."""
    try:
        path = binary._absolute(executable)
        before = binary._identity(binary._safe_metadata(path))
        digest = binary._digest(path)
        if digest not in _SUPPORTED_BINARY_SHA256:
            _blocked('readonly_native_binary_capability_unverified')
        identity = binary._identity(binary._safe_metadata(path))
        if identity != before:
            _blocked('readonly_native_binary_changed')
        environment, fingerprints, names = _capture(cwd)
        return NativeReadonlyInvocation(str(path), Path(cwd), environment,
            fingerprints, names, identity, digest)
    except ReadonlyAgentConfigBlocked:
        raise
    except (OSError, ValueError):
        _blocked('readonly_native_binary_unverifiable')
