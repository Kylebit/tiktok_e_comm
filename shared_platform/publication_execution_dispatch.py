"""Durable control-plane starts, followed by bounded independent report reads.

This module calls only existing Product Center endpoints. A package is a routing
reference, never approval; the service still verifies its original approval and
per-write gates. The local journal prevents a second start after an uncertain
transport result or a process interruption. It is not a merchant result ledger.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request
from shared_platform.publication_provider_journal import (
    _checked_runtime_path, ensure_private_runtime_directory,
)


SCHEMA = "publication-execution-dispatch/v1"
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS publication_dispatch (
 scope_key TEXT PRIMARY KEY, binding_json TEXT NOT NULL,
 phase TEXT NOT NULL, revision INTEGER NOT NULL, owner TEXT NOT NULL,
 started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 next_action TEXT NOT NULL, next_check_at TEXT, blocked_by TEXT,
 run_id TEXT, report_id TEXT, result_json TEXT
);
CREATE TABLE IF NOT EXISTS publication_dispatch_responses (
 id INTEGER PRIMARY KEY AUTOINCREMENT, scope_key TEXT NOT NULL,
 kind TEXT NOT NULL, http_status INTEGER NOT NULL, raw_body BLOB NOT NULL,
 body_sha256 TEXT NOT NULL, observed_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _key(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _next_check(seconds: float = 1) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


@lru_cache(maxsize=1)
def _control():
    """Reuse the installed control wrapper's identity/report contracts."""
    scripts = Path(__file__).resolve().parents[1] / "skills/publish-approved-product/scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location(
        "orbit_publication_dispatch_control", scripts / "product_center_publication.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class DispatchJournal:
    """Short SQLite CAS transactions; no network or parsing under its writer lock."""
    def __init__(self, root: str | Path):
        self.root = _checked_runtime_path(Path(root)) / "private-publication-dispatch"
        self.path = self.root / "publication_dispatch.private.db"

    def initialize(self):
        ensure_private_runtime_directory(self.root, _key({"schema": SCHEMA, "root": str(self.root)}))
        _checked_runtime_path(self.path)
        with sqlite3.connect(self.path, timeout=10) as connection:
            connection.executescript(_SCHEMA_SQL)

    @contextmanager
    def _write(self):
        with sqlite3.connect(self.path, timeout=10) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            yield connection

    def get(self, key):
        if not self.path.is_file():
            return None
        ensure_private_runtime_directory(self.root, _key({"schema": SCHEMA, "root": str(self.root)}))
        _checked_runtime_path(self.path)
        with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10) as c:
            c.row_factory = sqlite3.Row
            row = c.execute("SELECT * FROM publication_dispatch WHERE scope_key=?", (key,)).fetchone()
            return dict(row) if row else None

    def claim(self, key, binding):
        """Only the INSERT winner may issue a start; an old INTENT is unknown."""
        encoded = _canonical(binding).decode()
        with self._write() as c:
            row = c.execute("SELECT binding_json FROM publication_dispatch WHERE scope_key=?", (key,)).fetchone()
            if row:
                if row[0] != encoded:
                    raise ValueError("dispatch scope is bound to different immutable facts")
                return False
            at = _now()
            c.execute("""INSERT INTO publication_dispatch
                (scope_key,binding_json,phase,revision,owner,started_at,updated_at,
                 next_action,next_check_at,blocked_by)
                VALUES (?,?, 'INTENT',1,?,?,?,'RECONCILE_ORIGINAL_START',?,?)""",
                (key, encoded, f"pid:{os.getpid()}", at, at, at, "ORIGINAL_START_OUTCOME_UNCONFIRMED"))
            return True

    def response(self, key, kind, status, raw):
        """Retain raw bytes and status BEFORE any JSON/identity extraction."""
        with self._write() as c:
            c.execute("""INSERT INTO publication_dispatch_responses
                (scope_key,kind,http_status,raw_body,body_sha256,observed_at)
                VALUES (?,?,?,?,?,?)""",
                (key, kind, status, raw, hashlib.sha256(raw).hexdigest(), _now()))

    def original_start(self, key):
        if not self.path.is_file():
            return None
        with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as c:
            row = c.execute("""SELECT http_status,raw_body,body_sha256 FROM publication_dispatch_responses
                WHERE scope_key=? AND kind='START' ORDER BY id LIMIT 1""", (key,)).fetchone()
            if row and hashlib.sha256(bytes(row[1])).hexdigest() != row[2]:
                raise ValueError("original dispatch response integrity mismatch")
            return (int(row[0]), bytes(row[1])) if row else None

    def update(self, key, *, phase, next_action, blocked_by=None,
               run_id=None, report_id=None, result=None, next_check_at=None):
        with self._write() as c:
            c.execute("""UPDATE publication_dispatch SET phase=?,revision=revision+1,
                updated_at=?,next_action=?,blocked_by=?,next_check_at=?,
                run_id=COALESCE(?,run_id),report_id=COALESCE(?,report_id),
                result_json=COALESCE(?,result_json) WHERE scope_key=?""",
                (phase, _now(), next_action, blocked_by, next_check_at, run_id, report_id,
                 _canonical(result).decode() if result is not None else None, key))


def _http_raw(url, *, payload=None, timeout_seconds=30):
    data = _canonical(payload) if payload is not None else None
    request = urllib.request.Request(url, data=data,
        method="POST" if payload is not None else "GET",
        headers={"Accept": "application/json", **({"Content-Type": "application/json"} if data else {})})
    # No implicit redirects: a start may not be replayed to a different origin.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout_seconds) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, b""


def _request(journal, key, kind, request, url, *, payload=None, timeout_seconds=30):
    if request is None:
        status, raw = _http_raw(url, payload=payload, timeout_seconds=timeout_seconds)
    else:
        # Injection replaces the external edge only. Production uses exact raw
        # bytes; legacy offline callbacks may return a decoded Mapping.
        status, body = request(url, payload=payload, timeout_seconds=timeout_seconds)
        raw = body if isinstance(body, bytes) else _canonical(body)
    journal.response(key, kind, status, raw)
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeError):
        parsed = {}
    return status, parsed if isinstance(parsed, Mapping) else {}


def _bound(base_url, offer_id, plan_id, platform, target_scope, package_digest=None,
           retry_of_run_id=None, recovery_manifest_digest=None):
    body = {"offer_id": offer_id, "plan_id": plan_id}
    if target_scope:
        body["target_scope"] = list(target_scope)
    if retry_of_run_id:
        body["retry_of_run_id"] = retry_of_run_id
    if recovery_manifest_digest:
        body["recovery_manifest_digest"] = recovery_manifest_digest
    identity = {"base_url": base_url.rstrip("/"), "platform": platform,
                "request": body}
    # A changed package cannot make an uncertain original scope look new.
    return _key(identity), {**identity, "package_digest": package_digest}


def _unknown_row(platform, reason):
    return _control()._row(platform, "PROCESSING", reason_code=reason)


def _start(journal, key, binding, *, request, request_timeout_seconds, allow_start):
    ctl = _control()
    row = journal.get(key)
    if row and row["binding_json"] != _canonical(binding).decode():
        raise ValueError("dispatch immutable package changed; reconcile original package")
    if row and row["phase"] == "TERMINAL":
        return json.loads(row["result_json"])
    platform = binding["platform"]
    try:
        original = journal.original_start(key)
    except ValueError:
        journal.update(key, phase="UNKNOWN", next_action="RECONCILE_ORIGINAL_START",
                       blocked_by="START_RESPONSE_INTEGRITY_INVALID")
        return _unknown_row(platform, "START_RESPONSE_INTEGRITY_INVALID")
    if row and row["run_id"] and row["report_id"]:
        return ctl._row(platform, "PROCESSING", run_id=row["run_id"], report_id=row["report_id"])
    if original is None and allow_start and journal.claim(key, binding):
        try:
            status, payload = _request(journal, key, "START", request,
                binding["base_url"] + ctl.PLATFORM_ENDPOINTS[platform],
                payload=binding["request"], timeout_seconds=request_timeout_seconds)
        except Exception:
            # Includes a persistence error AFTER a possibly successful wire.
            # Keep INTENT/response; never classify as a safe zero-write retry.
            journal.update(key, phase="UNKNOWN", next_action="RECONCILE_ORIGINAL_START",
                           blocked_by="START_OUTCOME_UNKNOWN", next_check_at=_next_check())
            return _unknown_row(platform, "START_OUTCOME_UNKNOWN")
    elif original is not None:
        status, raw = original
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            payload = {}
    else:
        if row:
            journal.update(key, phase="UNKNOWN", next_action="RECONCILE_ORIGINAL_START",
                           blocked_by="ORIGINAL_START_ID_MISSING", next_check_at=_next_check())
        return _unknown_row(platform, "ORIGINAL_START_ID_MISSING" if row else "NOT_STARTED")
    if status == 0 or status >= 500:
        journal.update(key, phase="UNKNOWN", next_action="RECONCILE_ORIGINAL_START",
                       blocked_by="START_OUTCOME_UNKNOWN", next_check_at=_next_check())
        return _unknown_row(platform, "START_OUTCOME_UNKNOWN")
    if status != 202:
        result = ctl._row(platform, "FAILED", reason_code="START_REJECTED")
        journal.update(key, phase="TERMINAL", next_action="INSPECT_REJECTION",
                       blocked_by="START_REJECTED", result=result)
        return result
    try:
        run_id, report_id = ctl._start_identity(payload, platform=platform)
    except (ValueError, TypeError, AttributeError):
        journal.update(key, phase="UNKNOWN", next_action="RECONCILE_ORIGINAL_START",
                       blocked_by="START_IDENTITY_INVALID", next_check_at=_next_check())
        return _unknown_row(platform, "START_IDENTITY_INVALID")
    journal.update(key, phase="SUBMITTED", next_action="READ_ORIGINAL_REPORT",
                   run_id=run_id, report_id=report_id, next_check_at=_next_check())
    return ctl._row(platform, "PROCESSING", run_id=run_id, report_id=report_id)


def run_platform_dispatch(*, base_url, offer_id, plan_id, platforms, journal_root,
                         request=None, target_scope=None, retry_of_run_id=None,
                         recovery_manifest_digest=None, package_digest=None,
                         request_timeout_seconds=30, poll_interval_seconds=1,
                         poll_timeout_seconds=300, max_parallel=3,
                         monotonic=None, sleep=None, allow_start=True):
    """Start selected independent platforms before waiting for any report."""
    import time
    ctl = _control()
    if type(max_parallel) is not int or not 1 <= max_parallel <= 3:
        raise ValueError("max_parallel must be an integer from 1 to 3")
    ctl._offer_id(offer_id)
    ctl._plan_id(plan_id)
    address = urllib.parse.urlsplit(base_url)
    if (address.scheme != "http" or address.hostname != "127.0.0.1" or not address.port
            or address.username or address.password or address.path not in {"", "/"}
            or address.query or address.fragment):
        raise ValueError("dispatch requires the explicitly bound loopback Product Center")
    platforms = tuple(platforms)
    if not platforms or len(set(platforms)) != len(platforms) or any(p not in ctl.PLATFORM_ORDER for p in platforms):
        raise ValueError("platforms must be unique supported canonical platforms")
    for value, positive in ((request_timeout_seconds, True), (poll_interval_seconds, True), (poll_timeout_seconds, False)):
        if type(value) not in {int, float} or not math.isfinite(value) or (value <= 0 if positive else value < 0):
            raise ValueError("dispatch time bounds are invalid")
    journal = DispatchJournal(journal_root)
    if allow_start:
        journal.initialize()
    elif not journal.path.is_file():
        return [ctl._row(p, "PROCESSING", reason_code="NOT_STARTED") for p in platforms]
    entries = {p: _bound(base_url, offer_id, plan_id, p, target_scope,
        package_digest, retry_of_run_id, recovery_manifest_digest) for p in platforms}
    # Loading contracts occurs outside worker threads and outside SQL locks.
    rows = {}
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(platforms))) as pool:
        futures = {p: pool.submit(_start, journal, *entries[p], request=request,
            request_timeout_seconds=request_timeout_seconds, allow_start=allow_start) for p in platforms}
        for p in platforms:
            rows[p] = futures[p].result()
    # All starts are now durable/known or unknown. Never serialize another
    # platform's start behind a 300-second remote processing poll.
    def poll(platform):
        row = rows[platform]
        if row.get("status") != "PROCESSING" or not row.get("run_id"):
            return row
        key, binding = entries[platform]
        def read(url, *, timeout_seconds):
            return _request(journal, key, "REPORT", request, url,
                            timeout_seconds=timeout_seconds)
        try:
            result = ctl._poll_report(base_url=base_url, offer_id=offer_id,
                plan_id=plan_id, platform=platform, run_id=row["run_id"],
                report_id=row["report_id"], request=read,
                request_timeout_seconds=request_timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                poll_timeout_seconds=poll_timeout_seconds,
                monotonic=monotonic or time.monotonic, sleep=sleep or time.sleep)
        except Exception:
            result = {**row, "reason_code": "REPORT_UNAVAILABLE"}
        terminal = result["status"] in ctl.TERMINAL_STATUSES
        journal.update(key, phase="TERMINAL" if terminal else "SUBMITTED",
            next_action="VIEW_RESULT" if terminal else "READ_ORIGINAL_REPORT",
            blocked_by=result.get("reason_code"), result=result,
            next_check_at=None if terminal else _next_check(poll_interval_seconds))
        return result
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(platforms))) as pool:
        futures = {p: pool.submit(poll, p) for p in platforms}
        return [futures[p].result() for p in platforms]


def _package(value):
    from shared_platform.publication_execution_package import validate_publication_execution_package
    package = validate_publication_execution_package(value)
    if package["runtime"].get("workflow_mode", "standard-v4") != "standard-v4":
        raise ValueError("native execution packages require the original native decision entry")
    return package


def _package_state(package, journal_root, rows=None):
    journal = DispatchJournal(journal_root)
    states = []
    for platform in package["platforms"]:
        key, _ = _bound(package["runtime"]["base_url"], package["offer_id"],
            package["plan_id"], platform, None, package["package_digest"])
        row = journal.get(key)
        states.append({"platform": platform, "state": row["phase"] if row else "NOT_STARTED",
            "revision": row["revision"] if row else 0,
            "owner": row["owner"] if row else None,
            "next_action": row["next_action"] if row else "EXECUTE_APPROVED_PACKAGE",
            "next_check_at": row["next_check_at"] if row else None,
            "blocked_by": row["blocked_by"] if row else None,
            "run_id": row["run_id"] if row else None,
            "report_id": row["report_id"] if row else None,
            "result": json.loads(row["result_json"]) if row and row["result_json"] else None})
    finished = all(s["state"] == "TERMINAL" for s in states)
    unknown = any(s["state"] in {"INTENT", "UNKNOWN"} for s in states)
    return {"schema_version": SCHEMA, "package_digest": package["package_digest"],
        "task_id": package["task_id"], "offer_id": package["offer_id"],
        "plan_id": package["plan_id"], "state": "TERMINAL" if finished else "UNKNOWN" if unknown else "PROCESSING" if journal.path.is_file() else "NOT_STARTED",
        "next_action": "VIEW_RESULT" if finished else "RECONCILE_ORIGINAL" if journal.path.is_file() else "EXECUTE_APPROVED_PACKAGE",
        "owner": next((s["owner"] for s in states if s["owner"]), None),
        "next_check_at": min((s["next_check_at"] for s in states if s["next_check_at"]), default=None),
        "blocked_by": [s["blocked_by"] for s in states if s["blocked_by"]],
        "platforms": rows if rows is not None else states,
        "recovery_states": states, "journal_path": str(journal.path)}


def dispatch_package(package: Mapping, *, journal_root: Path, request=None, **options):
    package = _package(package)
    rows = run_platform_dispatch(base_url=package["runtime"]["base_url"],
        offer_id=package["offer_id"], plan_id=package["plan_id"],
        platforms=tuple(package["platforms"]), journal_root=journal_root,
        package_digest=package["package_digest"], request=request, **options)
    return _package_state(package, journal_root, rows)


def reconcile_package(package: Mapping, *, journal_root: Path, request=None, **options):
    package = _package(package)
    rows = run_platform_dispatch(base_url=package["runtime"]["base_url"],
        offer_id=package["offer_id"], plan_id=package["plan_id"],
        platforms=tuple(package["platforms"]), journal_root=journal_root,
        package_digest=package["package_digest"], request=request,
        allow_start=False, **options)
    return _package_state(package, journal_root, rows)


def result_package(package: Mapping, *, journal_root: Path):
    """Status-only: never create a directory, database, schema, or external call."""
    return _package_state(_package(package), journal_root)
