"""Durable local storage for report runs and the Orbit inbox.

The store is deliberately separate from the commerce database.  Importing or
reading it never creates a file; schema creation happens only when a caller
explicitly stores a report run.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core.config import ROOT


DEFAULT_REPORT_STORE_PATH = ROOT / "data" / "orbit_platform.db"
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

CREATE TABLE IF NOT EXISTS orbit_inbox (
    inbox_id TEXT PRIMARY KEY,
    report_run_id TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT,
    FOREIGN KEY (report_run_id) REFERENCES orbit_report_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_orbit_inbox_status_created
    ON orbit_inbox(status, created_at DESC);
"""


@dataclass(frozen=True)
class StoredReportResult:
    run_id: str
    inbox_id: str | None
    report_created: bool
    inbox_created: bool


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
    """SQLite-backed, idempotent report and local-inbox repository."""

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
        *,
        add_to_inbox: bool = True,
    ) -> StoredReportResult:
        """Persist one run and at most one local inbox item per idempotency key."""
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

            inbox_id = f"report:{run_id}" if add_to_inbox else None
            inbox_created = False
            if inbox_id:
                existing_inbox = conn.execute(
                    "SELECT inbox_id FROM orbit_inbox WHERE report_run_id = ?",
                    (run_id,),
                ).fetchone()
                inbox_created = existing_inbox is None
                if inbox_created:
                    period_label = f"{_text(period['start'])[:10]} – {_text(period['end'])[:10]}"
                    title = (
                        f"周度利润简报需要复核 · {period_label}"
                        if status != "ready"
                        else f"周度利润简报已生成 · {period_label}"
                    )
                    summary = {
                        "run_id": run_id,
                        "calculation_kind": report["calculation_kind"],
                        "status": status,
                        "period": dict(period),
                        "quality_issue_count": len(report.get("quality_issues") or []),
                        "negative_profit_count": len(report.get("negative_profit_skus") or []),
                    }
                    conn.execute(
                        """
                        INSERT INTO orbit_inbox (
                            inbox_id, report_run_id, category, title, severity,
                            status, payload_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, 'unread', ?, ?)
                        """,
                        (
                            inbox_id,
                            run_id,
                            _text(report["calculation_kind"]),
                            title,
                            "info" if status == "ready" else "warning",
                            _json_payload(summary),
                            now,
                        ),
                    )
            conn.commit()
        return StoredReportResult(run_id, inbox_id, report_created, inbox_created)

    def list_report_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        safe_limit = min(max(int(limit), 1), 100)
        with self._connect_readonly() as conn:
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

    def list_inbox(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        safe_limit = min(max(int(limit), 1), 100)
        filters: tuple[object, ...]
        if status:
            query = """
                SELECT inbox_id, report_run_id, category, title, severity,
                       status, payload_json, created_at, read_at
                FROM orbit_inbox
                WHERE status = ?
                ORDER BY created_at DESC, inbox_id DESC
                LIMIT ?
            """
            filters = (_text(status), safe_limit)
        else:
            query = """
                SELECT inbox_id, report_run_id, category, title, severity,
                       status, payload_json, created_at, read_at
                FROM orbit_inbox
                ORDER BY created_at DESC, inbox_id DESC
                LIMIT ?
            """
            filters = (safe_limit,)
        with self._connect_readonly() as conn:
            try:
                rows = conn.execute(query, filters).fetchall()
            except sqlite3.OperationalError:
                return []
        return [
            {
                "inbox_id": row["inbox_id"],
                "report_run_id": row["report_run_id"],
                "category": row["category"],
                "title": row["title"],
                "severity": row["severity"],
                "status": row["status"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
                "read_at": row["read_at"],
            }
            for row in rows
        ]


def default_report_store() -> ReportRunStore:
    return ReportRunStore(DEFAULT_REPORT_STORE_PATH)
