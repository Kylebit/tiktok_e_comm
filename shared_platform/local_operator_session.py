"""Private same-Windows-user capability bootstrap; no HTTP or caller SID trust.

Windows token/ACL APIs are the trust producer. All paths must remain direct,
owner-only, and bound to one explicit synthetic ReleaseStore instance.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

from shared_platform.common_offer_authority_store import (
    MODE, CommonAuthorityBlocked, _fail, _safe_path, canonical_bytes, digest,
)


class _SecurityAttributes(C.Structure):
    _fields_ = [("length", W.DWORD), ("descriptor", C.c_void_p), ("inherit", W.BOOL)]


class _AclSize(C.Structure):
    _fields_ = [("count", W.DWORD), ("used", W.DWORD), ("free", W.DWORD)]


def _windows():
    if os.name != "nt":
        _fail("WINDOWS_IDENTITY_REQUIRED")
    a, k = C.WinDLL("advapi32", use_last_error=True), C.WinDLL("kernel32", use_last_error=True)
    signatures = [
        (a.OpenProcessToken, W.BOOL, [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]),
        (a.OpenThreadToken, W.BOOL, [W.HANDLE, W.DWORD, W.BOOL, C.POINTER(W.HANDLE)]),
        (a.GetTokenInformation, W.BOOL, [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]),
        (a.ConvertSidToStringSidW, W.BOOL, [C.c_void_p, C.POINTER(W.LPWSTR)]),
        (a.ConvertStringSecurityDescriptorToSecurityDescriptorW, W.BOOL, [W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p), C.POINTER(W.DWORD)]),
        (a.GetNamedSecurityInfoW, W.DWORD, [W.LPWSTR, C.c_int, W.DWORD, C.POINTER(C.c_void_p), C.c_void_p, C.POINTER(C.c_void_p), C.c_void_p, C.POINTER(C.c_void_p)]),
        (a.GetSecurityDescriptorControl, W.BOOL, [C.c_void_p, C.POINTER(W.WORD), C.POINTER(W.DWORD)]),
        (a.GetAclInformation, W.BOOL, [C.c_void_p, C.c_void_p, W.DWORD, C.c_int]),
        (a.GetAce, W.BOOL, [C.c_void_p, W.DWORD, C.POINTER(C.c_void_p)]),
        (a.GetSecurityDescriptorDacl, W.BOOL, [C.c_void_p, C.POINTER(W.BOOL), C.POINTER(C.c_void_p), C.POINTER(W.BOOL)]),
        (a.SetNamedSecurityInfoW, W.DWORD, [W.LPWSTR, C.c_int, W.DWORD, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]),
        (k.GetCurrentProcess, W.HANDLE, []), (k.GetCurrentThread, W.HANDLE, []),
        (k.CloseHandle, W.BOOL, [W.HANDLE]), (k.LocalFree, C.c_void_p, [C.c_void_p]),
        (k.CreateDirectoryW, W.BOOL, [W.LPCWSTR, C.POINTER(_SecurityAttributes)]),
    ]
    for fn, result, args in signatures:
        fn.restype, fn.argtypes = result, args
    return a, k


def _sid_text(a, k, sid):
    text = W.LPWSTR()
    if not a.ConvertSidToStringSidW(sid, C.byref(text)):
        _fail("WINDOWS_SID_QUERY_FAILED")
    try:
        return text.value
    finally:
        k.LocalFree(C.cast(text, C.c_void_p))


def current_windows_owner_sid():
    """Read the effective token, including an impersonating thread token."""
    a, k = _windows()
    token = W.HANDLE()
    if not a.OpenThreadToken(k.GetCurrentThread(), 8, True, C.byref(token)):
        if C.get_last_error() != 1008 or not a.OpenProcessToken(k.GetCurrentProcess(), 8, C.byref(token)):
            _fail("WINDOWS_TOKEN_QUERY_FAILED")
    try:
        size = W.DWORD()
        a.GetTokenInformation(token, 1, None, 0, C.byref(size))
        if not 0 < size.value <= 65536:
            _fail("WINDOWS_TOKEN_QUERY_FAILED")
        raw = C.create_string_buffer(size.value)
        if not a.GetTokenInformation(token, 1, raw, size.value, C.byref(size)):
            _fail("WINDOWS_TOKEN_QUERY_FAILED")
        return _sid_text(a, k, C.cast(raw, C.POINTER(C.c_void_p))[0])
    finally:
        k.CloseHandle(token)


def verify_owner_only(path, expected_sid, *, protected=True):
    """Require one full-access user ACE; no Everyone/admin/group grant."""
    path = _safe_path(path)
    a, k = _windows()
    owner, acl, sd = C.c_void_p(), C.c_void_p(), C.c_void_p()
    rc = a.GetNamedSecurityInfoW(str(path), 1, 5, C.byref(owner), None, C.byref(acl), None, C.byref(sd))
    if rc or not sd or not owner or not acl:
        _fail("OWNER_ONLY_ACL_QUERY_FAILED")
    try:
        control, revision = W.WORD(), W.DWORD()
        info = _AclSize()
        if (not a.GetSecurityDescriptorControl(sd, C.byref(control), C.byref(revision))
                or protected and not control.value & 0x1000
                or _sid_text(a, k, owner) != expected_sid
                or not a.GetAclInformation(acl, C.byref(info), C.sizeof(info), 2)
                or info.count != 1):
            _fail("OWNER_ONLY_ACL_INVALID")
        ace = C.c_void_p()
        if not a.GetAce(acl, 0, C.byref(ace)):
            _fail("OWNER_ONLY_ACL_INVALID")
        header = C.string_at(ace, 8)
        if header[0] != 0 or int.from_bytes(header[4:8], "little") != 0x1F01FF:
            _fail("OWNER_ONLY_ACL_INVALID")
        if _sid_text(a, k, C.c_void_p(ace.value + 8)) != expected_sid:
            _fail("OWNER_ONLY_ACL_INVALID")
        return {"owner_sid": expected_sid, "owner_only_ace_count": 1,
                "protected_dacl": bool(control.value & 0x1000)}
    finally:
        k.LocalFree(sd)


def create_owner_only_directory(path):
    """Apply protected DACL atomically at directory creation, before secrets."""
    path = _safe_path(path)
    sid = current_windows_owner_sid()
    a, k = _windows()
    sd = C.c_void_p()
    if not a.ConvertStringSecurityDescriptorToSecurityDescriptorW(f"O:{sid}D:P(A;OICI;FA;;;{sid})", 1, C.byref(sd), None):
        _fail("OWNER_ONLY_DESCRIPTOR_FAILED")
    try:
        attrs = _SecurityAttributes(C.sizeof(_SecurityAttributes), sd, False)
        if not k.CreateDirectoryW(str(path), C.byref(attrs)):
            _fail("OWNER_ONLY_DIRECTORY_CREATE_FAILED")
    finally:
        k.LocalFree(sd)
    return verify_owner_only(path, sid)


def _protect_private_directory(path, sid):
    """Explicitly protect only a previously validated synthetic instance root."""
    a, k = _windows()
    sd, acl = C.c_void_p(), C.c_void_p()
    present, defaulted = W.BOOL(), W.BOOL()
    if not a.ConvertStringSecurityDescriptorToSecurityDescriptorW(f"D:P(A;OICI;FA;;;{sid})", 1, C.byref(sd), None):
        _fail("OWNER_ONLY_DESCRIPTOR_FAILED")
    try:
        if (not a.GetSecurityDescriptorDacl(sd, C.byref(present), C.byref(acl), C.byref(defaulted))
                or not present or not acl
                or a.SetNamedSecurityInfoW(str(_safe_path(path)), 1, 0x80000004, None, None, acl, None)):
            _fail("PRIVATE_OWNER_PROTECTION_FAILED")
    finally:
        k.LocalFree(sd)
    verify_owner_only(path, sid)


@dataclass(frozen=True)
class PrivateOperatorGrant:
    session_id: str
    capability: str = field(repr=False)
    csrf: str = field(repr=False)


class LocalOperatorSessions:
    """Explicit private service-owned bootstrap, default unreachable from HTTP."""
    def __init__(self, authority):
        self.authority = authority
        self.root = authority.root / "local-operator"

    def initialize_owner(self):
        self.authority._guard()
        _protect_private_directory(self.authority.root, current_windows_owner_sid())
        result = create_owner_only_directory(self.root)
        marker = {"schema_version": "private-local-owner/v1", "evidence_kind": MODE,
                  "owner_sid": result["owner_sid"], "instance_id": self.authority.marker["instance_id"]}
        with (self.root / "owner.json").open("xb") as f:
            f.write(canonical_bytes(marker))
        self._owner()
        return result

    def _owner(self):
        self.authority._guard()
        sid = current_windows_owner_sid()
        verify_owner_only(self.authority.root, sid)
        verify_owner_only(self.authority.store.path, sid, protected=False)
        verify_owner_only(self.root, sid)
        marker_path = self.root / "owner.json"
        verify_owner_only(marker_path, sid, protected=False)
        marker = json.loads(marker_path.read_bytes())
        if marker != {"schema_version": "private-local-owner/v1", "evidence_kind": MODE,
                      "owner_sid": sid, "instance_id": self.authority.marker["instance_id"]}:
            _fail("LOCAL_OWNER_INSTANCE_MISMATCH")
        return sid

    def grant_same_user(self):
        """Any program under this effective owner can bootstrap; no extra review."""
        sid = self._owner()
        grant = PrivateOperatorGrant(secrets.token_hex(16), secrets.token_hex(32), secrets.token_hex(32))
        now = int(time.time())
        with self.authority._transaction() as db:
            from shared_platform.private_final_decision_store import _check_private_final_schema
            _check_private_final_schema(db)
            db.execute("INSERT INTO private_local_sessions VALUES (?,?,?,?,?,?,?)",
                       (grant.session_id, digest(grant.capability.encode()), digest(grant.csrf.encode()),
                        sid, self.authority.marker["instance_id"], now + 3600, now))
        return grant

    def bootstrap_to_private_file(self, *, filename):
        """Local launcher handoff, protected file only; never URL/log/stdout."""
        self._handoff_name(filename)
        self._owner()
        destination = _safe_path(self.root / filename)
        if destination.exists():
            _fail("LOCAL_HANDOFF_EXISTS")
        grant = self.grant_same_user()
        with destination.open("xb") as f:
            f.write(canonical_bytes({"schema_version": "private-local-grant/v1", "session_id": grant.session_id,
                                     "capability": grant.capability, "csrf": grant.csrf}))
        if not stat.S_ISREG(destination.stat().st_mode):
            _fail("LOCAL_HANDOFF_NOT_REGULAR_FILE")
        verify_owner_only(destination, current_windows_owner_sid(), protected=False)
        return {"private_handoff_file": str(destination), "execution_authority": False}

    def grant_from_private_file(self, *, filename):
        self._handoff_name(filename)
        sid = self._owner()
        path = self.root / filename
        verify_owner_only(path, sid, protected=False)
        if not path.is_file() or path.stat().st_size > 1024:
            _fail("LOCAL_HANDOFF_BYTES_INVALID")
        value = json.loads(path.read_bytes())
        if set(value) != {"schema_version", "session_id", "capability", "csrf"} or value["schema_version"] != "private-local-grant/v1":
            _fail("LOCAL_HANDOFF_BYTES_INVALID")
        grant = PrivateOperatorGrant(value["session_id"], value["capability"], value["csrf"])
        with self.authority._transaction(readonly=True) as db:
            self.authenticate(db, grant)
        return grant

    @staticmethod
    def _handoff_name(filename):
        reserved = {"CON", "PRN", "AUX", "NUL", *("COM" + str(i) for i in range(1, 10)),
                    *("LPT" + str(i) for i in range(1, 10))}
        if (type(filename) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", filename)
                or filename.endswith(".") or filename.casefold() == "owner.json"
                or filename.split(".", 1)[0].upper() in reserved):
            _fail("LOCAL_HANDOFF_NAME_INVALID")

    def authenticate(self, db, grant):
        # No approved_by, caller SID, loopback, Origin or raw receipt substitutes.
        sid = self._owner()
        from shared_platform.private_final_decision_store import _check_private_final_schema
        _check_private_final_schema(db)
        if (type(grant) is not PrivateOperatorGrant
                or any(type(value) is not str or not value or len(value) > 256
                       for value in (grant.session_id, grant.capability, grant.csrf))):
            _fail("LOCAL_CAPABILITY_REQUIRED")
        row = db.execute("SELECT * FROM private_local_sessions WHERE session_id=?", (grant.session_id,)).fetchone()
        if (not row or row["owner_sid"] != sid or row["instance_id"] != self.authority.marker["instance_id"]
                or not secrets.compare_digest(row["capability_digest"], digest(grant.capability.encode()))
                or not secrets.compare_digest(row["csrf_digest"], digest(grant.csrf.encode()))):
            _fail("LOCAL_CAPABILITY_INVALID_OR_EXPIRED")
        # Only a matching owner/instance/capability/CSRF gets the precise TTL
        # diagnosis. An arbitrary expired session_id is not authentication.
        if row["expires_at_epoch"] <= int(time.time()):
            _fail("LOCAL_OPERATOR_SESSION_EXPIRED")
        return sid


_HANDOFF_SCHEMA = """CREATE TABLE operator_browser_handoffs (
    nonce_digest TEXT PRIMARY KEY, session_id TEXT NOT NULL, owner_sid TEXT NOT NULL,
    instance_id TEXT NOT NULL, origin TEXT NOT NULL, review_path TEXT NOT NULL,
    grant_json TEXT, expires_at_epoch INTEGER NOT NULL,
    consumed_at_epoch INTEGER,
    FOREIGN KEY(session_id) REFERENCES private_local_sessions(session_id))"""


def _browser_handoff_schema(db):
    row = db.execute("SELECT sql FROM sqlite_master WHERE name='operator_browser_handoffs' AND type='table'").fetchone()
    norm = lambda text: text.strip().rstrip(";")
    if not row or norm(row["sql"]) != norm(_HANDOFF_SCHEMA):
        _fail("BROWSER_HANDOFF_SCHEMA_UNAVAILABLE_OR_DRIFTED")


def initialize_private_browser_handoff(sessions):
    """Explicit owner-only setup; no default HTTP or regular ReleaseStore use."""
    if type(sessions) is not LocalOperatorSessions:
        _fail("LOCAL_OWNER_SESSION_PRODUCER_REQUIRED")
    sessions._owner()
    with sessions.authority._transaction() as db:
        from shared_platform.private_final_decision_store import _check_private_final_schema
        _check_private_final_schema(db)
        existing = db.execute("SELECT 1 FROM sqlite_master WHERE name='operator_browser_handoffs'").fetchone()
        if not existing:
            db.execute(_HANDOFF_SCHEMA)
        _browser_handoff_schema(db)


def _handoff_origin(port):
    if type(port) is not int or not 1 <= port <= 65535:
        _fail("BROWSER_HANDOFF_PORT_INVALID")
    return f"http://127.0.0.1:{port}"


def issue_private_browser_handoff(sessions, *, port, reservation_id, marketplace_plan_id=None):
    """Local Windows-token producer, never callable by ordinary unauth HTTP.

    The returned 60s carrier is independent of the reusable session secrets.
    A launcher may put this single-use carrier in a fragment. Neither capability
    nor CSRF is put there. The actual init page must clear it before any fetch.
    """
    if type(sessions) is not LocalOperatorSessions:
        _fail("LOCAL_OWNER_SESSION_PRODUCER_REQUIRED")
    origin = _handoff_origin(port)
    sid = sessions._owner()
    grant = sessions.grant_same_user()
    nonce = secrets.token_hex(32)
    with sessions.authority._transaction() as db:
        from urllib.parse import urlencode
        from shared_platform.private_final_decision_store import PrivateFinalDecisionStore, _check_private_final_schema
        _check_private_final_schema(db)
        sessions.authenticate(db, grant)
        _browser_handoff_schema(db)
        store = PrivateFinalDecisionStore(sessions.authority)
        if marketplace_plan_id is None:
            review, _ = store._build(db, reservation_id)
        else:
            from shared_platform.private_domain_final_review import build_registered_domain_review
            review, _ = build_registered_domain_review(store, db, common_reservation_id=reservation_id, marketplace_plan_id=marketplace_plan_id)
        review_path = '/product-workspace?' + urlencode({'offer_id': review.offer_id, 'plan_id': review.plan_id})
        secret = canonical_bytes({"session_id": grant.session_id, "capability": grant.capability,
                                  "csrf": grant.csrf}).decode()
        db.execute("INSERT INTO operator_browser_handoffs VALUES (?,?,?,?,?,?,?,?,NULL)",
                   (digest(nonce.encode()), grant.session_id, sid,
                    sessions.authority.marker["instance_id"], origin, review_path, secret, int(time.time()) + 60))
    return nonce


def consume_private_browser_handoff(sessions, nonce, *, port):
    """Server-owned atomic consume; only the carrier comes from the HTTP body."""
    if type(sessions) is not LocalOperatorSessions or type(nonce) is not str or not re.fullmatch(r"[0-9a-f]{64}", nonce):
        _fail("BROWSER_HANDOFF_INVALID")
    origin = _handoff_origin(port)
    sid = sessions._owner()
    with sessions.authority._transaction() as db:
        from shared_platform.private_final_decision_store import _check_private_final_schema
        _check_private_final_schema(db)
        _browser_handoff_schema(db)
        row = db.execute("SELECT * FROM operator_browser_handoffs WHERE nonce_digest=?", (digest(nonce.encode()),)).fetchone()
        if (not row or row["owner_sid"] != sid or row["instance_id"] != sessions.authority.marker["instance_id"]
                or row["origin"] != origin
                or row["expires_at_epoch"] <= int(time.time()) or row["consumed_at_epoch"] is not None or not row["grant_json"]):
            _fail("BROWSER_HANDOFF_EXPIRED_OR_USED")
        value = json.loads(row["grant_json"])
        grant = PrivateOperatorGrant(**value)
        sessions.authenticate(db, grant)
        if row["session_id"] != grant.session_id:
            _fail("BROWSER_HANDOFF_SESSION_MISMATCH")
        updated = db.execute("UPDATE operator_browser_handoffs SET consumed_at_epoch=?,grant_json=NULL WHERE nonce_digest=? AND consumed_at_epoch IS NULL", (int(time.time()), digest(nonce.encode())))
        if updated.rowcount != 1:
            _fail("BROWSER_HANDOFF_CONSUME_LOST")
        return grant, row['review_path']
