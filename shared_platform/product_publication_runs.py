"""Durable lifecycle for asynchronous Product Center publication runs.

Final publication reports remain immutable in ``product_publication_reports``.
This module owns only the small mutable run cursor plus an append-only event
trail.  A process restart never promotes a queued/running run to success and
never automatically replays provider work with an unknown write outcome.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.config import ROOT
from shared_platform.product_publication_reports import publication_report_id


DEFAULT_PRODUCT_PUBLICATION_RUN_DB = ROOT / "data" / "orbit_platform.db"
RUN_SCHEMA_VERSION = "product-publication-run/v1"
RUN_STATUS_SCHEMA_VERSION = "product-publication-run-status/v1"
SNAPSHOT_SCHEMA_VERSION = "approved-publication-snapshot/v4"
RUN_STATES = frozenset({"QUEUED", "RUNNING", "COMPLETED", "FAILED"})
_PLATFORMS = frozenset({"TIKTOK", "SHOPEE", "OZON"})
_SAFE_RUN_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEX_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_FAILURE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS product_publication_runs (
    run_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL UNIQUE,
    offer_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    plan_id TEXT NOT NULL,
    snapshot_schema_version TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    platform_scope_json TEXT NOT NULL,
    target_count INTEGER NOT NULL,
    state TEXT NOT NULL,
    final_report_id TEXT,
    failure_code TEXT,
    identity_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (state IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED'))
);
CREATE INDEX IF NOT EXISTS idx_product_publication_runs_offer
    ON product_publication_runs(offer_id, created_at DESC, run_id DESC);
CREATE TABLE IF NOT EXISTS product_publication_run_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    state TEXT NOT NULL,
    final_report_id TEXT,
    failure_code TEXT,
    created_at TEXT NOT NULL,
    event_digest TEXT NOT NULL,
    UNIQUE (run_id, sequence),
    FOREIGN KEY (run_id) REFERENCES product_publication_runs(run_id),
    CHECK (state IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED'))
);
"""


class ProductPublicationRunError(RuntimeError):
    """Base error for durable publication run lifecycle state."""


class ProductPublicationRunIntegrityError(ProductPublicationRunError):
    """Run identity or append-only events failed verification."""


@dataclass(frozen=True)
class StoredPublicationRun:
    run_id: str
    report_id: str
    state: str
    created: bool


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: object, name: str, *, max_length: int = 512) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip() or len(value) > max_length:
        raise ValueError(f"{name} is invalid")
    return value


def _run_id(value: object) -> str:
    run_id = _text(value, "run_id", max_length=128)
    if not _SAFE_RUN_PART.fullmatch(run_id) or run_id in {".", ".."}:
        raise ValueError("run_id is invalid")
    return run_id


def _offer_id(value: object) -> str:
    offer_id = _text(value, "offer_id", max_length=32)
    if not offer_id.isascii() or not offer_id.isdigit() or int(offer_id) <= 0:
        raise ValueError("offer_id is invalid")
    return offer_id


def _revision(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("revision must be a positive integer")
    return value


def _target_count(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("target_count must be a positive integer")
    return value


def _sha256(value: object) -> str:
    digest = _text(value, "snapshot_digest", max_length=71)
    if not _HEX_DIGEST.fullmatch(digest):
        raise ValueError("snapshot_digest must be a lowercase sha256 digest")
    return "sha256:" + digest.removeprefix("sha256:")


def _scope(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError("platform_scope must be a sequence")
    scope = tuple(value)
    if not scope or any(type(platform) is not str for platform in scope):
        raise ValueError("platform_scope is invalid")
    if len(scope) != len(set(scope)) or any(platform not in _PLATFORMS for platform in scope):
        raise ValueError("platform_scope is unsupported or duplicated")
    if len(scope) != 1:
        raise ValueError("each durable publication run must own exactly one platform")
    return tuple(platform for platform in ("TIKTOK", "SHOPEE", "OZON") if platform in set(scope))


def _identity_payload(
    *,
    run_id: str,
    report_id: str,
    offer_id: str,
    revision: int,
    plan_id: str,
    snapshot_digest: str,
    platform_scope: tuple[str, ...],
    target_count: int,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "report_id": report_id,
        "offer_id": offer_id,
        "revision": revision,
        "plan_id": plan_id,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_digest": snapshot_digest,
        "platform_scope": list(platform_scope),
        "target_count": target_count,
    }


def _event_payload(
    *,
    run_id: str,
    sequence: int,
    state: str,
    final_report_id: str | None,
    failure_code: str | None,
    created_at: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "sequence": sequence,
        "state": state,
        "final_report_id": final_report_id,
        "failure_code": failure_code,
        "created_at": created_at,
    }


class ProductPublicationRunStore:
    """SQLite run cursor with append-only lifecycle events."""

    def __init__(self, path: str | Path = DEFAULT_PRODUCT_PUBLICATION_RUN_DB) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _connect_readonly(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA)

    @staticmethod
    def _append_event(
        conn: sqlite3.Connection,
        *,
        run_id: str,
        sequence: int,
        state: str,
        final_report_id: str | None,
        failure_code: str | None,
        created_at: str,
    ) -> None:
        payload = _event_payload(
            run_id=run_id,
            sequence=sequence,
            state=state,
            final_report_id=final_report_id,
            failure_code=failure_code,
            created_at=created_at,
        )
        conn.execute(
            """
            INSERT INTO product_publication_run_events (
                run_id, sequence, state, final_report_id, failure_code,
                created_at, event_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sequence,
                state,
                final_report_id,
                failure_code,
                created_at,
                _digest(payload),
            ),
        )

    def create_run(
        self,
        *,
        run_id: str,
        offer_id: str,
        revision: int,
        plan_id: str,
        snapshot_digest: str,
        platform_scope: Sequence[str],
        target_count: int,
    ) -> StoredPublicationRun:
        safe_run_id = _run_id(run_id)
        safe_report_id = publication_report_id(safe_run_id)
        safe_offer_id = _offer_id(offer_id)
        safe_revision = _revision(revision)
        safe_plan_id = _text(plan_id, "plan_id")
        safe_digest = _sha256(snapshot_digest)
        safe_scope = _scope(platform_scope)
        safe_target_count = _target_count(target_count)
        identity = _identity_payload(
            run_id=safe_run_id,
            report_id=safe_report_id,
            offer_id=safe_offer_id,
            revision=safe_revision,
            plan_id=safe_plan_id,
            snapshot_digest=safe_digest,
            platform_scope=safe_scope,
            target_count=safe_target_count,
        )
        identity_digest = _digest(identity)
        now = _utc_now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._ensure_schema(conn)
            existing = conn.execute(
                "SELECT * FROM product_publication_runs WHERE run_id = ? OR report_id = ?",
                (safe_run_id, safe_report_id),
            ).fetchall()
            if existing:
                if len(existing) != 1 or existing[0]["identity_digest"] != identity_digest:
                    raise ValueError("publication run identity already stores different facts")
                run = self._row_to_run(conn, existing[0])
                return StoredPublicationRun(
                    run_id=run["run_id"],
                    report_id=run["report_id"],
                    state=run["state"],
                    created=False,
                )
            conn.execute(
                """
                INSERT INTO product_publication_runs (
                    run_id, report_id, offer_id, revision, plan_id,
                    snapshot_schema_version, snapshot_digest,
                    platform_scope_json, target_count, state,
                    final_report_id, failure_code, identity_digest,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', NULL, NULL, ?, ?, ?)
                """,
                (
                    safe_run_id,
                    safe_report_id,
                    safe_offer_id,
                    safe_revision,
                    safe_plan_id,
                    SNAPSHOT_SCHEMA_VERSION,
                    safe_digest,
                    _canonical_json(list(safe_scope)),
                    safe_target_count,
                    identity_digest,
                    now,
                    now,
                ),
            )
            self._append_event(
                conn,
                run_id=safe_run_id,
                sequence=1,
                state="QUEUED",
                final_report_id=None,
                failure_code=None,
                created_at=now,
            )
            conn.commit()
        return StoredPublicationRun(safe_run_id, safe_report_id, "QUEUED", True)

    def _transition(
        self,
        *,
        run_id: str,
        state: str,
        final_report_id: str | None = None,
        failure_code: str | None = None,
    ) -> dict[str, Any]:
        safe_run_id = _run_id(run_id)
        if state not in RUN_STATES or state == "QUEUED":
            raise ValueError("publication run transition is invalid")
        if state == "COMPLETED":
            final_report_id = _text(final_report_id, "final_report_id")
            failure_code = None
        elif state == "FAILED":
            if type(failure_code) is not str or not _FAILURE_CODE.fullmatch(failure_code):
                raise ValueError("failure_code is invalid")
            final_report_id = None
        else:
            final_report_id = failure_code = None

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM product_publication_runs WHERE run_id = ?",
                (safe_run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("publication run not found")
            current = row["state"]
            if current == state:
                if row["final_report_id"] != final_report_id or row["failure_code"] != failure_code:
                    raise ValueError("publication run terminal facts conflict")
                return self._row_to_run(conn, row)
            allowed = (
                (current == "QUEUED" and state in {"RUNNING", "FAILED"})
                or (current == "RUNNING" and state in {"COMPLETED", "FAILED"})
            )
            if not allowed:
                raise ValueError(f"publication run cannot transition from {current} to {state}")
            if state == "COMPLETED" and final_report_id != row["report_id"]:
                raise ValueError("final report identity conflicts with publication run")
            sequence = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM product_publication_run_events WHERE run_id = ?",
                (safe_run_id,),
            ).fetchone()[0]
            now = _utc_now()
            conn.execute(
                """
                UPDATE product_publication_runs
                SET state = ?, final_report_id = ?, failure_code = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (state, final_report_id, failure_code, now, safe_run_id),
            )
            self._append_event(
                conn,
                run_id=safe_run_id,
                sequence=sequence,
                state=state,
                final_report_id=final_report_id,
                failure_code=failure_code,
                created_at=now,
            )
            conn.commit()
            updated = conn.execute(
                "SELECT * FROM product_publication_runs WHERE run_id = ?",
                (safe_run_id,),
            ).fetchone()
            return self._row_to_run(conn, updated)

    def mark_running(self, *, run_id: str) -> dict[str, Any]:
        return self._transition(run_id=run_id, state="RUNNING")

    def mark_completed(self, *, run_id: str, final_report_id: str) -> dict[str, Any]:
        return self._transition(
            run_id=run_id, state="COMPLETED", final_report_id=final_report_id
        )

    def mark_failed(self, *, run_id: str, failure_code: str) -> dict[str, Any]:
        return self._transition(
            run_id=run_id, state="FAILED", failure_code=failure_code
        )

    def _row_to_run(self, conn: sqlite3.Connection, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        try:
            scope = tuple(json.loads(row["platform_scope_json"]))
        except (TypeError, json.JSONDecodeError) as error:
            raise ProductPublicationRunIntegrityError("publication run scope is invalid") from error
        identity = _identity_payload(
            run_id=row["run_id"],
            report_id=row["report_id"],
            offer_id=row["offer_id"],
            revision=row["revision"],
            plan_id=row["plan_id"],
            snapshot_digest=row["snapshot_digest"],
            platform_scope=_scope(scope),
            target_count=_target_count(row["target_count"]),
        )
        if row["snapshot_schema_version"] != SNAPSHOT_SCHEMA_VERSION or _digest(identity) != row["identity_digest"]:
            raise ProductPublicationRunIntegrityError("publication run identity digest does not match")
        events = conn.execute(
            "SELECT * FROM product_publication_run_events WHERE run_id = ? ORDER BY sequence",
            (row["run_id"],),
        ).fetchall()
        if not events or [event["sequence"] for event in events] != list(range(1, len(events) + 1)):
            raise ProductPublicationRunIntegrityError("publication run event sequence is invalid")
        for event in events:
            payload = _event_payload(
                run_id=event["run_id"],
                sequence=event["sequence"],
                state=event["state"],
                final_report_id=event["final_report_id"],
                failure_code=event["failure_code"],
                created_at=event["created_at"],
            )
            if _digest(payload) != event["event_digest"]:
                raise ProductPublicationRunIntegrityError("publication run event digest does not match")
        last = events[-1]
        if (
            last["state"] != row["state"]
            or last["final_report_id"] != row["final_report_id"]
            or last["failure_code"] != row["failure_code"]
            or last["created_at"] != row["updated_at"]
        ):
            raise ProductPublicationRunIntegrityError("publication run cursor conflicts with events")
        if row["state"] == "COMPLETED" and row["final_report_id"] != row["report_id"]:
            raise ProductPublicationRunIntegrityError("completed run final report identity conflicts")
        return {
            "schema_version": RUN_SCHEMA_VERSION,
            **identity,
            "state": row["state"],
            "final_report_id": row["final_report_id"],
            "failure_code": row["failure_code"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "event_count": len(events),
        }

    def get_run(self, *, report_id: str, offer_id: str) -> dict[str, Any] | None:
        safe_report_id = _text(report_id, "report_id")
        safe_offer_id = _offer_id(offer_id)
        if not self.path.is_file():
            return None
        with self._connect_readonly() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM product_publication_runs WHERE report_id = ? AND offer_id = ?",
                    (safe_report_id, safe_offer_id),
                ).fetchone()
                return self._row_to_run(conn, row)
            except sqlite3.OperationalError:
                return None

    def get_run_by_id(self, *, run_id: str) -> dict[str, Any] | None:
        safe_run_id = _run_id(run_id)
        if not self.path.is_file():
            return None
        with self._connect_readonly() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM product_publication_runs WHERE run_id = ?",
                    (safe_run_id,),
                ).fetchone()
                return self._row_to_run(conn, row)
            except sqlite3.OperationalError:
                return None


def public_publication_run_status(run: Mapping[str, Any]) -> dict[str, Any]:
    """Project a queued/running/failed run into the four-state UI contract."""

    state = run["state"]
    if state == "COMPLETED":
        raise ProductPublicationRunIntegrityError(
            "completed publication run is missing its immutable final report"
        )
    status = "FAILED" if state == "FAILED" else "PROCESSING"
    target_count = run["target_count"]
    platform = run["platform_scope"][0]
    summary = {
        "schema_version": "product-publication-summary/v1",
        "overall_status": status,
        "platforms": [
            {
                "platform": platform,
                "status": status,
                "target_count": target_count,
                "verified_count": 0,
                "processing_count": target_count if status == "PROCESSING" else 0,
                "failed_count": target_count if status == "FAILED" else 0,
            }
        ],
        "evidence": {
            "snapshot_verified": True,
            "dispatch_attempted": False if state == "QUEUED" else None,
            "readback_completed": False,
            "external_write_count": None,
        },
        "requires_human_action": status == "FAILED",
    }
    return {
        "schema_version": RUN_STATUS_SCHEMA_VERSION,
        "report_id": run["report_id"],
        "run_id": run["run_id"],
        "offer_id": run["offer_id"],
        "revision": run["revision"],
        "plan_id": run["plan_id"],
        "snapshot": {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "digest": run["snapshot_digest"],
        },
        "status": status,
        "status_label": "发布失败" if status == "FAILED" else "平台处理中",
        "summary": summary,
        "summary_digest": _digest(summary),
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
    }


def default_product_publication_run_store() -> ProductPublicationRunStore:
    return ProductPublicationRunStore()


__all__ = [
    "ProductPublicationRunError",
    "ProductPublicationRunIntegrityError",
    "ProductPublicationRunStore",
    "RUN_SCHEMA_VERSION",
    "RUN_STATUS_SCHEMA_VERSION",
    "StoredPublicationRun",
    "default_product_publication_run_store",
    "public_publication_run_status",
]
