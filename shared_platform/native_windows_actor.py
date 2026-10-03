"""Native same-Windows-user identity, independent of publication authority.

The trusted local startup/launcher calls bootstrap_service and writes the
short-lived carrier to an owner-only file. No unauthenticated HTTP bootstrap is
provided. A consumer injects NativeWindowsActorProfileReader and calls
read_verified(); it must independently validate its frozen candidate, COMMON,
budget and decision transaction. No private fixture grant is accepted here.
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time

from shared_platform.common_offer_authority_store import canonical_bytes, digest, _safe_path
from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform import local_operator_session as owner

PROFILE_SCHEMA = 'native-windows-owner-profile/v1'
ACTOR_SCHEMA = 'native-windows-owner-identity/v1'
TRUST = 'SAME_WINDOWS_USER'
_BOOTSTRAP = object()


def _fail(reason):
    raise ApprovalBlocked(reason)


@dataclass(frozen=True)
class NativeActorServiceConfig:
    """Service startup inputs, never fields accepted from a browser request."""
    root: Path
    logical_profile: str
    session_seconds: int = 3600


@dataclass(frozen=True)
class NativeOperatorGrant:
    session_id: str
    capability: str = field(repr=False)
    csrf: str = field(repr=False)


@dataclass(frozen=True)
class _Session:
    owner_sid: str
    instance_id: str
    capability_digest: str
    csrf_digest: str
    expires_at_epoch: int


def _identity(info):
    # Windows path stat and handle stat use different ctime definitions.
    birth = getattr(info, 'st_birthtime_ns', None)
    if os.name == 'nt' and type(birth) is not int:
        _fail('NATIVE_ACTOR_FILE_IDENTITY_UNAVAILABLE')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, birth)


def _read_profile(config, *, expected_digest=None):
    try:
        sid = owner.current_windows_owner_sid()
        root = _safe_path(config.root)
        path = _safe_path(root/'native-owner.json')
        owner.verify_owner_only(root, sid)
        owner.verify_owner_only(path, sid, protected=False)
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 4096:
            _fail('NATIVE_ACTOR_PROFILE_INVALID')
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            raw = stream.read(4097)
            closed = os.fstat(stream.fileno())
        after = _safe_path(path).stat()
        if not _identity(before) == _identity(opened) == _identity(closed) == _identity(after):
            _fail('NATIVE_ACTOR_PROFILE_CHANGED')
        owner.verify_owner_only(root, sid)
        owner.verify_owner_only(path, sid, protected=False)
        value = json.loads(raw)
        if (type(value) is not dict or set(value) != {'schema_version','owner_sid','logical_profile',
                'instance_id','trust_mode','mapping_source'}
                or value['schema_version'] != PROFILE_SCHEMA or value['owner_sid'] != sid
                or value['logical_profile'] != config.logical_profile
                or value['trust_mode'] != TRUST
                or value['mapping_source'] != 'NATIVE_SAME_OWNER_STARTUP'
                or type(value['instance_id']) is not str
                or not re.fullmatch(r'[0-9a-f]{32}', value['instance_id'])
                or raw != canonical_bytes(value)
                or len(raw) != before.st_size
                or expected_digest is not None and digest(raw) != expected_digest
                or owner.current_windows_owner_sid() != sid):
            _fail('NATIVE_ACTOR_PROFILE_INVALID_OR_CHANGED')
        return value, digest(raw)
    except ApprovalBlocked:
        raise
    except Exception as error:
        raise ApprovalBlocked('NATIVE_ACTOR_OWNER_OR_PROFILE_UNVERIFIED') from error


class NativeWindowsActorService:
    def __init__(self, config, *, _bootstrap=None):
        if _bootstrap is not _BOOTSTRAP:
            _fail('NATIVE_ACTOR_SERVICE_STARTUP_REQUIRED')
        self.config = config
        self._profile, self._profile_digest = _read_profile(config)
        self._sessions = {}
        self._lock = threading.RLock()

    def _current(self):
        value, raw_digest = _read_profile(self.config, expected_digest=self._profile_digest)
        if value != self._profile:
            _fail('NATIVE_ACTOR_PROFILE_CHANGED')
        return value, raw_digest

    def grant_same_user(self):
        """Trusted local producer only; never expose as a localhost HTTP action."""
        value, _ = self._current()
        grant = NativeOperatorGrant(secrets.token_hex(16), secrets.token_hex(32), secrets.token_hex(32))
        with self._lock:
            if len(self._sessions) >= 128:
                _fail('NATIVE_ACTOR_SESSION_CAPACITY_REACHED')
            self._sessions[grant.session_id] = _Session(value['owner_sid'], value['instance_id'],
                digest(grant.capability.encode()), digest(grant.csrf.encode()),
                int(time.time()) + self.config.session_seconds)
        return grant

    def bootstrap_to_owner_file(self, *, filename):
        """Automatic local startup handoff; no identity confirmation or approval.

        Only the protected pathname is returned. The carrier never appears in
        a URL, stdout or an approval field. An existing file is not overwritten.
        """
        owner.LocalOperatorSessions._handoff_name(filename)
        value, _ = self._current()
        path = _safe_path(self.config.root/filename)
        if path.exists():
            _fail('NATIVE_ACTOR_HANDOFF_EXISTS')
        grant = self.grant_same_user()
        with path.open('xb') as stream:
            stream.write(canonical_bytes({'schema_version':'native-owner-session-carrier/v1',
                'session_id':grant.session_id, 'capability':grant.capability, 'csrf':grant.csrf}))
        owner.verify_owner_only(path, value['owner_sid'], protected=False)
        return path

    def grant_from_owner_file(self, *, filename):
        owner.LocalOperatorSessions._handoff_name(filename)
        value, _ = self._current()
        path = _safe_path(self.config.root/filename)
        owner.verify_owner_only(path, value['owner_sid'], protected=False)
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 1024:
            _fail('NATIVE_ACTOR_HANDOFF_INVALID')
        with path.open('rb') as stream:
            opened = os.fstat(stream.fileno())
            raw = stream.read(1025)
            closed = os.fstat(stream.fileno())
        after = _safe_path(path).stat()
        owner.verify_owner_only(path, value['owner_sid'], protected=False)
        if (not _identity(before) == _identity(opened) == _identity(closed) == _identity(after)
                or len(raw) != before.st_size):
            _fail('NATIVE_ACTOR_HANDOFF_CHANGED')
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            raise ApprovalBlocked('NATIVE_ACTOR_HANDOFF_INVALID') from None
        if (type(data) is not dict or set(data) != {'schema_version','session_id','capability','csrf'}
                or data['schema_version'] != 'native-owner-session-carrier/v1'
                or raw != canonical_bytes(data)):
            _fail('NATIVE_ACTOR_HANDOFF_INVALID')
        grant = NativeOperatorGrant(data['session_id'], data['capability'], data['csrf'])
        self.authenticate(grant)
        return grant

    def authenticate(self, grant):
        value, raw_digest = self._current()
        if (type(grant) is not NativeOperatorGrant
                or any(type(x) is not str or not x or len(x)>256
                    for x in (grant.session_id, grant.capability, grant.csrf))):
            _fail('NATIVE_ACTOR_TRUSTED_LOCAL_CARRIER_REQUIRED')
        with self._lock:
            row = self._sessions.get(grant.session_id)
            if (not row or row.owner_sid != value['owner_sid'] or row.instance_id != value['instance_id']
                    or not secrets.compare_digest(row.capability_digest, digest(grant.capability.encode()))
                    or not secrets.compare_digest(row.csrf_digest, digest(grant.csrf.encode()))):
                _fail('NATIVE_ACTOR_SESSION_INVALID_OR_EXPIRED')
            if row.expires_at_epoch <= int(time.time()):
                _fail('NATIVE_ACTOR_SESSION_EXPIRED')
        self._current()
        return {'schema_version':ACTOR_SCHEMA, 'identity_verified':True, 'owner_sid':value['owner_sid'],
            'logical_profile':value['logical_profile'], 'instance_id':value['instance_id'],
            'trust_mode':TRUST, 'mapping_source':value['mapping_source'],
            'mapping_digest':raw_digest, 'session_id':grant.session_id}


def bootstrap_service(config):
    """Call once from trusted native service startup; no manual install gate.

    The current Windows token creates an owner-only profile automatically on a
    fresh configured root. Existing malformed/private roots are never adopted.
    No release DB, candidate, approval, COMMON or budget state is written.
    """
    if (type(config) is not NativeActorServiceConfig or not isinstance(config.root, Path)
            or not config.root.is_absolute()
            or type(config.logical_profile) is not str or not config.logical_profile.strip()
            or type(config.session_seconds) is not int or not 1 <= config.session_seconds <= 3600):
        _fail('NATIVE_ACTOR_SERVICE_CONFIG_REQUIRED')
    root = _safe_path(config.root)
    if not root.exists():
        result = owner.create_owner_only_directory(root)
        profile = {'schema_version':PROFILE_SCHEMA, 'owner_sid':result['owner_sid'],
            'logical_profile':config.logical_profile, 'instance_id':secrets.token_hex(16),
            'trust_mode':TRUST, 'mapping_source':'NATIVE_SAME_OWNER_STARTUP'}
        with (root/'native-owner.json').open('xb') as stream:
            stream.write(canonical_bytes(profile))
    return NativeWindowsActorService(config, _bootstrap=_BOOTSTRAP)


class NativeWindowsActorProfileReader:
    """Inject into a native consumer; request actor/approved_by fields are absent."""
    def __init__(self, service, grant):
        if type(service) is not NativeWindowsActorService:
            _fail('NATIVE_ACTOR_SERVICE_READER_REQUIRED')
        self.service, self.grant = service, grant

    def read_verified(self):
        return self.service.authenticate(self.grant)
