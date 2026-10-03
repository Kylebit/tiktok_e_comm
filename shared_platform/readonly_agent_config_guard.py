"""Bounded local inheritance checks, without loading auth or changing a route.

This checks discoverable local inputs only. Local absence is not proof that
account-managed defaults/hooks will be absent when Codex authenticates, and a
passed local gate is not a declaration of complete runtime isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import stat
import tomllib

_MAX_CONFIG_BYTES = 256 * 1024
_MAX_ANCESTORS = 32


class ReadonlyAgentConfigBlocked(ValueError):
    """Only a fixed nonsecret reason may cross the public bridge boundary."""


@dataclass(repr=False)
class LocalConfigSnapshot:
    environment: dict[str, str] = field(repr=False)
    fingerprints: dict[str, tuple] = field(repr=False)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_nlink,
            getattr(info, 'st_file_attributes', 0))


def _path_metadata(path, *, directory=False):
    """Check unresolved ordinary ancestors, including those of an absent leaf."""
    if (not path.is_absolute() or len(path.parents) > _MAX_ANCESTORS
            or path.as_posix().startswith('//')
            or (os.name == 'nt' and (len(path.drive) != 2
                or not path.drive[0].isalpha() or path.drive[1] != ':'))):
        raise ReadonlyAgentConfigBlocked('readonly_agent_config_path_unsafe')
    for item in [*reversed(path.parents), path]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            if item == path:
                return None
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400
                or (item != path and not stat.S_ISDIR(info.st_mode))):
            raise ReadonlyAgentConfigBlocked('readonly_agent_config_path_unsafe')
        if item == path:
            if directory:
                if not stat.S_ISDIR(info.st_mode):
                    raise ReadonlyAgentConfigBlocked('readonly_agent_config_path_unsafe')
            elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ReadonlyAgentConfigBlocked('readonly_agent_config_path_unsafe')
            return info


def _read_config(path, fingerprints, *, required=False):
    before = _path_metadata(path)
    if before is None:
        fingerprints[str(path)] = None
        if required:
            raise ReadonlyAgentConfigBlocked('readonly_agent_user_config_missing')
        return None
    if before.st_size > _MAX_CONFIG_BYTES:
        raise ReadonlyAgentConfigBlocked('readonly_agent_config_size_limit')
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if _identity(before) != _identity(opened):
            raise ReadonlyAgentConfigBlocked('readonly_agent_config_changed')
        raw = stream.read(_MAX_CONFIG_BYTES + 1)
        if _identity(opened) != _identity(os.fstat(stream.fileno())):
            raise ReadonlyAgentConfigBlocked('readonly_agent_config_changed')
    if len(raw) > _MAX_CONFIG_BYTES:
        raise ReadonlyAgentConfigBlocked('readonly_agent_config_size_limit')
    after = _path_metadata(path)
    if after is None or _identity(before) != _identity(after):
        raise ReadonlyAgentConfigBlocked('readonly_agent_config_changed')
    try:
        value = tomllib.loads(raw.decode('utf-8'))
    except (UnicodeError, tomllib.TOMLDecodeError):
        # Never expose TOML parser messages or configuration contents.
        raise ReadonlyAgentConfigBlocked('readonly_agent_config_parse_failed') from None
    fingerprints[str(path)] = (_identity(after), hashlib.sha256(raw).digest())
    return value


def _inspect_config(value):
    servers = value.get('mcp_servers', {})
    if not isinstance(servers, dict):
        raise ReadonlyAgentConfigBlocked('readonly_agent_mcp_config_unverifiable')
    for server in servers.values():
        if not isinstance(server, dict) or type(server.get('enabled', True)) is not bool:
            raise ReadonlyAgentConfigBlocked('readonly_agent_mcp_config_unverifiable')
        if server.get('enabled', True):
            raise ReadonlyAgentConfigBlocked('readonly_agent_enabled_mcp')
    # Hooks from different layers accumulate. Do not use a user feature flag,
    # absent trust record or a disabled matcher as proof that all hooks are off.
    if value.get('hooks'):
        raise ReadonlyAgentConfigBlocked('readonly_agent_hooks_configured')
    if value.get('notify'):
        raise ReadonlyAgentConfigBlocked('readonly_agent_notify_configured')
    for key in ('hooks', 'notify'):
        if key in value and not isinstance(value[key], (dict, list)):
            raise ReadonlyAgentConfigBlocked('readonly_agent_integration_config_unverifiable')
    if value.get('profile'):
        raise ReadonlyAgentConfigBlocked('readonly_agent_profile_layer_unverified')
    # Do not load plugins, connector tool arguments or their manifests to infer
    # enabled state. Ambiguous additional integration sources fail closed.
    if any(value.get(key) for key in ('apps', 'plugins', 'plugin_marketplaces')):
        raise ReadonlyAgentConfigBlocked('readonly_agent_plugin_or_app_layer_unverified')
    features = value.get('features', {})
    if not isinstance(features, dict):
        raise ReadonlyAgentConfigBlocked('readonly_agent_integration_config_unverifiable')
    if any(features.get(key) for key in ('apps', 'plugins', 'remote_control')):
        raise ReadonlyAgentConfigBlocked('readonly_agent_plugin_or_app_layer_unverified')


def _reject_present(path, fingerprints, reason, *, directory=False):
    info = _path_metadata(path, directory=directory)
    fingerprints[str(path)] = None if info is None else _identity(info)
    if info is not None:
        if directory:
            # A bounded existence check, no recursive scan or manifest reads.
            if next(path.iterdir(), None) is None:
                return
        raise ReadonlyAgentConfigBlocked(reason)


def capture_local_config(cwd):
    environment = dict(os.environ)
    home = Path(environment.get('CODEX_HOME') or str(Path.home() / '.codex'))
    root = Path(cwd)
    if not home.is_absolute() or not root.is_absolute():
        raise ReadonlyAgentConfigBlocked('readonly_agent_config_path_unsafe')
    if any(environment.get(key) for key in ('CODEX_PROFILE', 'CODEX_CONFIG',
                                           'CODEX_CONFIG_FILE', 'CODEX_MANAGED_CONFIG')):
        raise ReadonlyAgentConfigBlocked('readonly_agent_config_selector_unverified')
    fingerprints = {}
    try:
        _path_metadata(home, directory=True)
        if _path_metadata(root, directory=True) is None:
            raise ReadonlyAgentConfigBlocked('readonly_agent_config_path_unsafe')
        user = _read_config(home/'config.toml', fingerprints, required=True)
        _inspect_config(user)
        _reject_present(home/'hooks.json', fingerprints, 'readonly_agent_hooks_file_unverified')
        folders = [root, *root.parents]
        if len(folders) > _MAX_ANCESTORS:
            raise ReadonlyAgentConfigBlocked('readonly_agent_config_path_unsafe')
        for folder in folders:
            local = _read_config(folder/'.codex/config.toml', fingerprints)
            if local is not None:
                _inspect_config(local)
            _reject_present(folder/'.codex/hooks.json', fingerprints,
                            'readonly_agent_hooks_file_unverified')
        _reject_present(home/'plugins', fingerprints,
                        'readonly_agent_plugin_or_app_layer_unverified', directory=True)
        managed = [home/'managed_config.toml', home/'requirements.toml']
        if os.name == 'nt':
            program_data = environment.get('ProgramData') or environment.get('PROGRAMDATA')
            if not program_data or not Path(program_data).is_absolute():
                raise ReadonlyAgentConfigBlocked('readonly_agent_system_layer_path_unverified')
            system = Path(program_data)/'OpenAI/Codex'
        else:
            system = Path('/etc/codex')
        managed += [system/name for name in ('config.toml', 'requirements.toml', 'managed_config.toml')]
        for path in managed:
            _reject_present(path, fingerprints, 'readonly_agent_managed_layer_unverified')
    except ReadonlyAgentConfigBlocked:
        raise
    except OSError:
        raise ReadonlyAgentConfigBlocked('readonly_agent_config_unreadable') from None
    if environment != dict(os.environ):
        raise ReadonlyAgentConfigBlocked('readonly_agent_environment_changed')
    return LocalConfigSnapshot(environment, fingerprints)


def verify_local_config(snapshot, cwd):
    if snapshot.environment != dict(os.environ):
        raise ReadonlyAgentConfigBlocked('readonly_agent_environment_changed')
    try:
        current = capture_local_config(cwd)
    except ReadonlyAgentConfigBlocked:
        raise ReadonlyAgentConfigBlocked('readonly_agent_config_changed') from None
    if current.fingerprints != snapshot.fingerprints:
        raise ReadonlyAgentConfigBlocked('readonly_agent_config_changed')
