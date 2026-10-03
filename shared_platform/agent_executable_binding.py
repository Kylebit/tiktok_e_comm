"""Deployment-owned executable binding; inspection never proves CLI readiness.

Discovery is opt-in, confined to one configured bin root and one version layer.
It never chooses the newest directory, searches PATH, or silently repairs a
missing explicit binding. A changed binary needs a newly reviewed digest.
"""
import hashlib
import os
from pathlib import Path
import re
import stat


MAX_BIN_ENTRIES = 32
MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
_VERSION_DIRECTORY = re.compile(r'[0-9a-f]{16}', re.IGNORECASE)
_SHA256 = re.compile(r'[0-9a-f]{64}')


def _absolute(value):
    if not isinstance(value, str) or not value:
        raise ValueError('NATIVE_AGENT_EXECUTABLE_ABSOLUTE_PATH_REQUIRED')
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('NATIVE_AGENT_EXECUTABLE_ABSOLUTE_PATH_REQUIRED')
    return path


def _safe_metadata(path, *, directory=False):
    try:
        info = path.lstat()
    except FileNotFoundError as error:
        raise ValueError('NATIVE_AGENT_EXECUTABLE_MISSING') from error
    if (getattr(info, 'st_file_attributes', 0) & 0x400
            or stat.S_ISLNK(info.st_mode)
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or (not directory and info.st_nlink != 1)):
        raise ValueError('NATIVE_AGENT_EXECUTABLE_UNSAFE_PATH')
    for parent in path.parents:
        ancestor = parent.lstat()
        if stat.S_ISLNK(ancestor.st_mode) or getattr(ancestor, 'st_file_attributes', 0) & 0x400:
            raise ValueError('NATIVE_AGENT_EXECUTABLE_UNSAFE_PATH')
    return info


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_nlink, getattr(info, 'st_file_attributes', 0))


def _digest(path):
    before = _safe_metadata(path)
    if before.st_size > MAX_EXECUTABLE_BYTES:
        raise ValueError('NATIVE_AGENT_EXECUTABLE_SIZE_LIMIT_EXCEEDED')
    digest = hashlib.sha256()
    total = 0
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_EXECUTABLE_BYTES:
                raise ValueError('NATIVE_AGENT_EXECUTABLE_SIZE_LIMIT_EXCEEDED')
            digest.update(chunk)
        closed = os.fstat(stream.fileno())
    after = _safe_metadata(path)
    if (total != before.st_size
            or not (_identity(before) == _identity(opened) == _identity(closed) == _identity(after))):
        raise ValueError('NATIVE_AGENT_EXECUTABLE_CHANGED_DURING_INSPECTION')
    return digest.hexdigest()


def _pin(value, *, required):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError('NATIVE_AGENT_EXECUTABLE_DIGEST_REQUIRED')
    return value


def inspect_agent_executable(config):
    """Return path metadata only; no environment, process, config or auth writes."""
    result = {'status': 'BLOCKED', 'reason': 'NATIVE_FIXED_AGENT_EXECUTABLE_REQUIRED',
              'path': None, 'sha256': None, 'source': None,
              'cli_verified': False, 'task_execution_verified': False}
    try:
        if 'agent_executable' in config:
            result['source'] = 'explicit'
            path = _absolute(config['agent_executable'])
            expected = _pin(config.get('agent_executable_sha256'), required=False)
        elif 'agent_executable_discovery' in config:
            result['source'] = 'controlled_discovery'
            discovery = config['agent_executable_discovery']
            if (not isinstance(discovery, dict)
                    or set(discovery) != {'bin_root', 'sha256'}):
                if isinstance(discovery, dict) and 'sha256' not in discovery:
                    raise ValueError('NATIVE_AGENT_EXECUTABLE_DIGEST_REQUIRED')
                raise ValueError('NATIVE_AGENT_EXECUTABLE_DISCOVERY_CONFIG_INVALID')
            expected = _pin(discovery['sha256'], required=True)
            root = _absolute(discovery['bin_root'])
            _safe_metadata(root, directory=True)
            candidates = []
            for number, entry in enumerate(root.iterdir(), start=1):
                if number > MAX_BIN_ENTRIES:
                    raise ValueError('NATIVE_AGENT_EXECUTABLE_DISCOVERY_LIMIT_EXCEEDED')
                if not _VERSION_DIRECTORY.fullmatch(entry.name):
                    continue
                _safe_metadata(entry, directory=True)
                candidate = entry / 'codex.exe'
                try:
                    candidate.lstat()
                except FileNotFoundError:
                    continue
                _safe_metadata(candidate)
                candidates.append(candidate)
            if not candidates:
                raise ValueError('NATIVE_AGENT_EXECUTABLE_DISCOVERY_EMPTY')
            if len(candidates) != 1:
                raise ValueError('NATIVE_AGENT_EXECUTABLE_DISCOVERY_AMBIGUOUS')
            path = candidates[0]
        else:
            return result
        actual = _digest(path)
        if expected is not None and actual != expected:
            raise ValueError('NATIVE_AGENT_EXECUTABLE_DIGEST_MISMATCH')
        result.update(status='PRESENT_UNVERIFIED', reason=None,
                      path=str(path.resolve(strict=True)), sha256=actual)
    except ValueError as error:
        result['reason'] = str(error)
    except OSError:
        result['reason'] = 'NATIVE_AGENT_EXECUTABLE_INSPECTION_UNAVAILABLE'
    return result
