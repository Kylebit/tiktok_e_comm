"""Durable local storage for report runs.

The store is deliberately separate from the commerce database.  Importing or
reading it never creates a file; schema creation happens only when a caller
explicitly stores a report run.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core.config import ROOT


def configured_report_store_path() -> Path:
    selected = os.environ.get("ORBIT_REPORT_STORE_PATH")
    if not selected:
        return (ROOT / "data" / "orbit_platform.db").resolve()
    path = Path(selected).expanduser()
    if not path.is_absolute():
        raise ValueError("ORBIT_REPORT_STORE_PATH must be an absolute path")
    return path.resolve()


DEFAULT_REPORT_STORE_PATH = configured_report_store_path()
_ALLOWED_RUN_STATUSES = frozenset({"ready", "needs_review", "failed"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orbit_report_runs (
    run_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    calculation_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orbit_report_runs_created
    ON orbit_report_runs(created_at DESC);

"""


@dataclass(frozen=True)
class StoredReportResult:
    run_id: str
    report_created: bool


def _text(value: object) -> str:
    return str(value or "").strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validated_report(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("report payload must be a mapping")
    report = dict(payload)
    required = ("run_id", "idempotency_key", "calculation_kind", "status")
    missing = [name for name in required if not _text(report.get(name))]
    if missing:
        raise ValueError(f"missing report fields: {', '.join(missing)}")
    if _text(report["status"]) not in _ALLOWED_RUN_STATUSES:
        raise ValueError(f"unsupported report status: {report['status']}")
    period = report.get("period")
    if not isinstance(period, Mapping):
        raise ValueError("report period must be a mapping")
    if not _text(period.get("start")) or not _text(period.get("end")):
        raise ValueError("report period requires start and end")
    return report


class ReportRunStore:
    """SQLite-backed, idempotent report repository."""

    def __init__(self, path: str | Path = DEFAULT_REPORT_STORE_PATH) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _connect_readonly(self) -> sqlite3.Connection:
        source = self.path.resolve()
        conn = sqlite3.connect(
            source.as_uri() + "?mode=ro",
            uri=True,
            timeout=30,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA query_only=ON")
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA)

    def store_report_run(
        self,
        payload: Mapping[str, Any],
    ) -> StoredReportResult:
        """Persist one report per idempotency key without creating notifications."""
        report = _validated_report(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = _utc_now()
        run_id = _text(report["run_id"])
        idempotency_key = _text(report["idempotency_key"])
        status = _text(report["status"])
        period = report["period"]
        encoded = _json_payload(report)

        with self._connect() as conn:
            self._ensure_schema(conn)
            existing = conn.execute(
                "SELECT run_id FROM orbit_report_runs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing and _text(existing["run_id"]) != run_id:
                raise ValueError("idempotency_key already belongs to a different run_id")
            existing_run = conn.execute(
                "SELECT idempotency_key FROM orbit_report_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if existing_run and _text(existing_run["idempotency_key"]) != idempotency_key:
                raise ValueError("run_id already belongs to a different idempotency_key")
            report_created = existing is None
            if report_created:
                conn.execute(
                    """
                    INSERT INTO orbit_report_runs (
                        run_id, idempotency_key, calculation_kind, status,
                        period_start, period_end, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        idempotency_key,
                        _text(report["calculation_kind"]),
                        status,
                        _text(period["start"]),
                        _text(period["end"]),
                        encoded,
                        now,
                    ),
                )

            conn.commit()
        return StoredReportResult(run_id, report_created)

    def list_report_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        safe_limit = min(max(int(limit), 1), 100)
        with closing(self._connect_readonly()) as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT run_id, idempotency_key, calculation_kind, status,
                           period_start, period_end, payload_json, created_at
                    FROM orbit_report_runs
                    ORDER BY created_at DESC, run_id DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [
            {
                "run_id": row["run_id"],
                "idempotency_key": row["idempotency_key"],
                "calculation_kind": row["calculation_kind"],
                "status": row["status"],
                "period": {"start": row["period_start"], "end": row["period_end"]},
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]




def default_report_store() -> ReportRunStore:
    return ReportRunStore(DEFAULT_REPORT_STORE_PATH)
