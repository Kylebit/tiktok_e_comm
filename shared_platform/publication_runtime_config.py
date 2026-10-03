"""Trusted startup paths for existing R3 contracts; never an approval mechanism."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import stat
from typing import Mapping

from shared_platform import publication_autopilot as authority


@dataclass(frozen=True)
class StartupConfig:
    root: Path
    policy: str
    incidents: str
    explicit_policy: bool
    explicit_incidents: bool


def capture_startup_config(*, root: Path, environ: Mapping[str, str] | None = None) -> StartupConfig:
    """Capture process startup configuration without reading files or creating defaults."""
    env = os.environ if environ is None else environ
    return StartupConfig(
        Path(env.get('ORBIT_R3_CONFIG_ROOT', str(root))),
        env.get('ORBIT_R3_POLICY_PATH', 'config/product_publication_autopilot_policy.json'),
        env.get('ORBIT_R3_INCIDENT_REGISTRY_PATH', 'skills/publish-approved-product/references/incident-registry.json'),
        'ORBIT_R3_POLICY_PATH' in env or 'ORBIT_R3_CONFIG_ROOT' in env,
        'ORBIT_R3_INCIDENT_REGISTRY_PATH' in env or 'ORBIT_R3_CONFIG_ROOT' in env,
    )


class ConfigReadError(ValueError):
    pass


def _checked_path(root: Path, relative: str) -> Path:
    # Check lexical boundaries before stat/open, including Windows paths on POSIX.
    windows = PureWindowsPath(relative)
    rel = Path(relative)
    if (not root.is_absolute() or '..' in root.parts or not relative
            or rel.is_absolute() or windows.drive or windows.root
            or '..' in windows.parts or '..' in rel.parts or ':' in relative):
        raise ConfigReadError('UNSAFE_PATH')
    path = root / rel
    path.relative_to(root)
    # Do not resolve first: that would conceal a link in the configured root.
    for part in [*reversed(path.parents), path]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ConfigReadError('UNSAFE_PATH')
        if part != path and not stat.S_ISDIR(info.st_mode):
            raise ConfigReadError('UNSAFE_PATH')
    return path


def _read(root: Path, relative: str) -> bytes:
    path = _checked_path(root, relative)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ConfigReadError('UNSAFE_PATH')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        # Recheck before reading, including directory links introduced during open.
        _checked_path(root, relative)
        after = path.stat()
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino) or (
                after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
            raise ConfigReadError('CHANGED_DURING_READ')
        if opened.st_size > 2 * 1024 * 1024:
            raise ConfigReadError('INVALID')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        final = os.fstat(fd)
        if len(raw) > 2 * 1024 * 1024 or (opened.st_size, opened.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
            raise ConfigReadError('CHANGED_DURING_READ')
        return raw
    finally:
        os.close(fd)


def diagnose(config: StartupConfig) -> tuple[dict, dict]:
    """Return a redacted public summary and private validated compiler inputs.

    VALID uses the existing validator, including its existing authority fields.
    No new policy fields, permissions, attestations or installation fallbacks.
    """
    documents, values = {}, {}
    for name, relative, explicit, validator in (
        ('policy', config.policy, config.explicit_policy, authority.validate_autopilot_policy),
        ('incident_registry', config.incidents, config.explicit_incidents, authority.validate_incident_registry),
    ):
        row = {'source': 'STARTUP_CONFIG' if explicit else 'PROJECT_DEFAULT',
               'status': 'MISSING', 'content_digest': None}
        documents[name] = row
        try:
            raw = _read(config.root, relative)
            row['content_digest'] = hashlib.sha256(raw).hexdigest()
            try:
                value = json.loads(raw.decode('utf-8-sig'))
            except (UnicodeError, ValueError, RecursionError):
                raise ConfigReadError('MALFORMED') from None
            if not isinstance(value, dict):
                raise ConfigReadError('INVALID')
            try:
                values[name] = validator(value)
            except (ValueError, TypeError, KeyError, RecursionError) as error:
                # Categorize only errors already rejected by the existing contract.
                if name == 'policy' and str(error) in {
                    'autopilot policy is not active',
                    'paid policy requires attributable existing user authority',
                }:
                    raise ConfigReadError('AUTHORITY_UNCONFIRMED') from None
                raise ConfigReadError('INVALID') from None
            row['status'] = 'VALID'
        except FileNotFoundError:
            row['status'] = 'MISSING'
        except ConfigReadError as error:
            row['status'] = str(error)
        except (OSError, ValueError):
            row['status'] = 'UNREADABLE'
    configured = all(row['status'] == 'VALID' for row in documents.values())
    public = {'schema_version': 'publication-runtime-config/v1',
              'status': 'CONFIGURED' if configured else 'BLOCKED',
              'applies_to': 'NEW_MARKETPLACE_PREVIEW', 'documents': documents,
              'blockers': [f'R3_CONFIG_{name.upper()}_{row["status"]}'
                           for name, row in documents.items() if row['status'] != 'VALID']}
    return public, values if configured else {}


def redact_http_documents(value):
    """Keep frozen server documents private without changing their stored digests."""
    if isinstance(value, dict):
        if value.get('schema_version') in {authority.POLICY_SCHEMA, authority.INCIDENT_SCHEMA}:
            return {'schema_version': value['schema_version'], 'redacted': True,
                    'document_digest': authority._canonical_digest(value)}
        return {key: redact_http_documents(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_http_documents(item) for item in value]
    return value
