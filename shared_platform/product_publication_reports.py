"""Durable, redacted publication reports produced by the publication Skill.

This module is storage and readback only.  It never launches a Skill, imports a
channel adapter, or performs an external request.  Report paths are derived by
the server from validated immutable identities rather than accepted from a
caller.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from uuid import uuid4

from core.config import ROOT


DEFAULT_PRODUCT_PUBLICATION_REPORT_DB = ROOT / "data" / "orbit_platform.db"
DEFAULT_PRODUCT_PUBLICATION_REPORT_ROOT = ROOT / "reports" / "product-publication"
REPORT_SCHEMA_VERSION = "product-publication-report/v1"
SUMMARY_SCHEMA_VERSION = "product-publication-summary/v1"
SNAPSHOT_SCHEMA_VERSION = "approved-publication-snapshot/v4"
API_SCHEMA_VERSION = "product-publication-report-api/v1"

PUBLICATION_STATUSES = frozenset({"PUBLISHED", "PROCESSING", "PARTIAL", "FAILED"})
STATUS_LABELS = {
    "PUBLISHED": "发布成功",
    "PROCESSING": "平台处理中",
    "PARTIAL": "部分成功",
    "FAILED": "发布失败",
}
_PLATFORMS = frozenset({"TIKTOK", "SHOPEE", "OZON"})
_SAFE_RUN_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEX_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "report_id",
        "run_id",
        "offer_id",
        "revision",
        "plan_id",
        "snapshot",
        "status",
        "summary",
    }
)
_SUMMARY_FIELDS = frozenset(
    {
        "schema_version",
        "overall_status",
        "platforms",
        "evidence",
        "requires_human_action",
    }
)
_PLATFORM_FIELDS = frozenset(
    {
        "platform",
        "status",
        "target_count",
        "verified_count",
        "processing_count",
        "failed_count",
    }
)
_EVIDENCE_FIELDS = frozenset(
    {
        "snapshot_verified",
        "dispatch_attempted",
        "readback_completed",
        "external_write_count",
    }
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS product_publication_reports (
    report_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    offer_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    plan_id TEXT NOT NULL,
    snapshot_schema_version TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    report_path TEXT NOT NULL UNIQUE,
    summary_digest TEXT NOT NULL,
    redacted_summary_json TEXT NOT NULL,
    envelope_digest TEXT NOT NULL,
    report_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (offer_id, revision, run_id)
);
CREATE INDEX IF NOT EXISTS idx_product_publication_reports_offer_revision
    ON product_publication_reports(offer_id, revision, created_at DESC, report_id DESC);
"""


class ProductPublicationReportError(RuntimeError):
    """Base error for the durable publication report boundary."""


class ProductPublicationReportIntegrityError(ProductPublicationReportError):
    """Stored metadata or the server-owned report file failed verification."""


@dataclass(frozen=True)
class StoredPublicationReport:
    report_id: str
    report_path: str
    summary_digest: str
    created: bool


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _exact_text(value: object, name: str, *, max_length: int = 512) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip() or len(value) > max_length:
        raise ValueError(f"{name} is invalid")
    return value


def _exact_nonnegative_int(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _offer_id(value: object) -> str:
    offer_id = _exact_text(value, "offer_id", max_length=32)
    if not offer_id.isascii() or not offer_id.isdigit():
        raise ValueError("offer_id is invalid")
    return offer_id


def _revision(value: object) -> int:
    revision = _exact_nonnegative_int(value, "revision")
    if revision == 0:
        raise ValueError("revision must be positive")
    return revision


def _run_id(value: object) -> str:
    run_id = _exact_text(value, "run_id", max_length=128)
    if not _SAFE_RUN_PART.fullmatch(run_id) or run_id in {".", ".."}:
        raise ValueError("run_id is not a safe report path component")
    return run_id


def _sha256(value: object, name: str) -> str:
    digest = _exact_text(value, name, max_length=71)
    if not _HEX_DIGEST.fullmatch(digest):
        raise ValueError(f"{name} must be a lowercase sha256 digest")
    return "sha256:" + digest.removeprefix("sha256:")


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], name: str) -> None:
    actual = set(value)
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        extra = sorted(actual - set(expected))
        raise ValueError(f"{name} fields are invalid; missing={missing}; extra={extra}")


def _validated_summary(value: object, *, report_status: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("summary must be a mapping")
    _exact_fields(value, _SUMMARY_FIELDS, "summary")
    if value["schema_version"] != SUMMARY_SCHEMA_VERSION:
        raise ValueError("unsupported publication summary schema")
    if value["overall_status"] != report_status:
        raise ValueError("summary status does not match report status")
    if type(value["requires_human_action"]) is not bool:
        raise TypeError("requires_human_action must be a boolean")

    raw_platforms = value["platforms"]
    if type(raw_platforms) is not list:
        raise TypeError("summary platforms must be a list")
    platforms: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_platforms):
        if not isinstance(raw, Mapping):
            raise TypeError(f"summary platforms[{index}] must be a mapping")
        _exact_fields(raw, _PLATFORM_FIELDS, f"summary platforms[{index}]")
        platform = _exact_text(raw["platform"], "platform", max_length=16)
        status = _exact_text(raw["status"], "platform status", max_length=16)
        if platform not in _PLATFORMS or platform in seen:
            raise ValueError("summary platform is unsupported or duplicated")
        if status not in PUBLICATION_STATUSES:
            raise ValueError("summary platform status is unsupported")
        seen.add(platform)
        counts = {
            name: _exact_nonnegative_int(raw[name], name)
            for name in (
                "target_count",
                "verified_count",
                "processing_count",
                "failed_count",
            )
        }
        if sum(counts[name] for name in ("verified_count", "processing_count", "failed_count")) > counts["target_count"]:
            raise ValueError("summary platform counts exceed target_count")
        platforms.append({"platform": platform, "status": status, **counts})

    evidence = value["evidence"]
    if not isinstance(evidence, Mapping):
        raise TypeError("summary evidence must be a mapping")
    _exact_fields(evidence, _EVIDENCE_FIELDS, "summary evidence")
    safe_evidence: dict[str, Any] = {}
    for name in ("snapshot_verified", "dispatch_attempted", "readback_completed"):
        if type(evidence[name]) is not bool:
            raise TypeError(f"summary evidence {name} must be a boolean")
        safe_evidence[name] = evidence[name]
    external_write_count = evidence["external_write_count"]
    if external_write_count is not None:
        external_write_count = _exact_nonnegative_int(
            external_write_count, "external_write_count"
        )
    safe_evidence["external_write_count"] = external_write_count
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "overall_status": report_status,
        "platforms": platforms,
        "evidence": safe_evidence,
        "requires_human_action": value["requires_human_action"],
    }


def validate_publication_report(value: object) -> dict[str, Any]:
    """Validate the version+digest envelope without interpreting product facts."""
    if not isinstance(value, Mapping):
        raise TypeError("publication report must be a mapping")
    _exact_fields(value, _REPORT_FIELDS, "publication report")
    if value["schema_version"] != REPORT_SCHEMA_VERSION:
        raise ValueError("unsupported publication report schema")
    report_id = _exact_text(value["report_id"], "report_id")
    run_id = _run_id(value["run_id"])
    offer_id = _offer_id(value["offer_id"])
    revision = _revision(value["revision"])
    plan_id = _exact_text(value["plan_id"], "plan_id")
    status = _exact_text(value["status"], "status", max_length=16)
    if status not in PUBLICATION_STATUSES:
        raise ValueError("unsupported publication report status")
    snapshot = value["snapshot"]
    if not isinstance(snapshot, Mapping):
        raise TypeError("snapshot envelope must be a mapping")
    _exact_fields(
        snapshot, frozenset({"schema_version", "digest"}), "snapshot envelope"
    )
    if snapshot["schema_version"] != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported approved publication snapshot schema")
    snapshot_digest = _sha256(snapshot["digest"], "snapshot digest")
    summary = _validated_summary(value["summary"], report_status=status)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "run_id": run_id,
        "offer_id": offer_id,
        "revision": revision,
        "plan_id": plan_id,
        "snapshot": {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "digest": snapshot_digest,
        },
        "status": status,
        "summary": summary,
    }


class ProductPublicationReportStore:
    """SQLite index plus server-owned JSON report files."""

    def __init__(
        self,
        path: str | Path = DEFAULT_PRODUCT_PUBLICATION_REPORT_DB,
        *,
        reports_root: str | Path = DEFAULT_PRODUCT_PUBLICATION_REPORT_ROOT,
    ) -> None:
        self.path = Path(path)
        self.reports_root = Path(reports_root)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
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

    def _relative_report_path(self, report: Mapping[str, Any]) -> str:
        return PurePosixPath(
            report["offer_id"], str(report["revision"]), report["run_id"], "report.json"
        ).as_posix()

    def _resolved_report_file(self, report_path: str) -> Path:
        pure = PurePosixPath(report_path)
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            raise ValueError("report_path is invalid")
        root = self.reports_root.resolve()
        candidate = root.joinpath(*pure.parts).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("report_path escapes the publication report root")
        return candidate

    def store_report(self, value: object) -> StoredPublicationReport:
        report = validate_publication_report(value)
        report_path = self._relative_report_path(report)
        report_file = self._resolved_report_file(report_path)
        summary_json = _canonical_json(report["summary"])
        summary_digest = _digest(report["summary"])
        envelope_digest = _digest(report)
        now = _utc_now()

        self.path.parent.mkdir(parents=True, exist_ok=True)
        report_file.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._ensure_schema(conn)
            existing = conn.execute(
                """
                SELECT report_id, run_id, offer_id, revision, report_path,
                       summary_digest, envelope_digest
                FROM product_publication_reports
                WHERE report_id = ? OR run_id = ? OR report_path = ?
                """,
                (report["report_id"], report["run_id"], report_path),
            ).fetchall()
            if existing:
                if len(existing) != 1 or any(
                    row["report_id"] != report["report_id"]
                    or row["run_id"] != report["run_id"]
                    or row["offer_id"] != report["offer_id"]
                    or row["revision"] != report["revision"]
                    or row["report_path"] != report_path
                    or row["summary_digest"] != summary_digest
                    or row["envelope_digest"] != envelope_digest
                    for row in existing
                ):
                    raise ValueError("publication report identity already stores different facts")
                # Reuse the ordinary verified read path so a replay cannot hide
                # a damaged or externally modified report file.
                self._row_to_report(
                    conn.execute(
                        "SELECT * FROM product_publication_reports WHERE report_id = ?",
                        (report["report_id"],),
                    ).fetchone()
                )
                return StoredPublicationReport(
                    report["report_id"], report_path, summary_digest, False
                )

            file_payload = {
                **report,
                "report_path": report_path,
                "summary_digest": summary_digest,
                "created_at": now,
                "updated_at": now,
            }
            report_digest = _digest(file_payload)
            encoded = (_canonical_json(file_payload) + "\n").encode("utf-8")
            temp_file = report_file.with_name(f".{report_file.name}.{uuid4().hex}.tmp")
            try:
                temp_file.write_bytes(encoded)
                temp_file.replace(report_file)
                conn.execute(
                    """
                    INSERT INTO product_publication_reports (
                        report_id, run_id, offer_id, revision, plan_id,
                        snapshot_schema_version, snapshot_digest, status,
                        report_path, summary_digest, redacted_summary_json,
                        envelope_digest, report_digest, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report["report_id"],
                        report["run_id"],
                        report["offer_id"],
                        report["revision"],
                        report["plan_id"],
                        report["snapshot"]["schema_version"],
                        report["snapshot"]["digest"],
                        report["status"],
                        report_path,
                        summary_digest,
                        summary_json,
                        envelope_digest,
                        report_digest,
                        now,
                        now,
                    ),
                )
                conn.commit()
            finally:
                if temp_file.exists():
                    temp_file.unlink()
        return StoredPublicationReport(report["report_id"], report_path, summary_digest, True)

    def _row_to_report(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        try:
            summary = json.loads(row["redacted_summary_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise ProductPublicationReportIntegrityError(
                "stored publication summary is invalid"
            ) from error
        if _digest(summary) != row["summary_digest"]:
            raise ProductPublicationReportIntegrityError(
                "stored publication summary digest does not match"
            )
        report_file = self._resolved_report_file(row["report_path"])
        if not report_file.is_file():
            raise ProductPublicationReportIntegrityError(
                "server-owned publication report file is missing"
            )
        try:
            file_payload = json.loads(report_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProductPublicationReportIntegrityError(
                "server-owned publication report file is invalid"
            ) from error
        if _digest(file_payload) != row["report_digest"]:
            raise ProductPublicationReportIntegrityError(
                "server-owned publication report digest does not match"
            )
        expected = {
            "report_id": row["report_id"],
            "run_id": row["run_id"],
            "offer_id": row["offer_id"],
            "revision": row["revision"],
            "plan_id": row["plan_id"],
            "snapshot": {
                "schema_version": row["snapshot_schema_version"],
                "digest": row["snapshot_digest"],
            },
            "status": row["status"],
            "summary": summary,
            "report_path": row["report_path"],
            "summary_digest": row["summary_digest"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for name, expected_value in expected.items():
            if file_payload.get(name) != expected_value:
                raise ProductPublicationReportIntegrityError(
                    f"server-owned publication report {name} does not match the index"
                )
        if file_payload.get("schema_version") != REPORT_SCHEMA_VERSION:
            raise ProductPublicationReportIntegrityError(
                "server-owned publication report schema does not match"
            )
        if _digest({name: file_payload[name] for name in _REPORT_FIELDS}) != row["envelope_digest"]:
            raise ProductPublicationReportIntegrityError(
                "server-owned publication report envelope does not match"
            )
        return {"schema_version": REPORT_SCHEMA_VERSION, **expected}

    def get_report(self, *, report_id: str, offer_id: str) -> dict[str, Any] | None:
        safe_report_id = _exact_text(report_id, "report_id")
        safe_offer_id = _offer_id(offer_id)
        if not self.path.is_file():
            return None
        with self._connect_readonly() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM product_publication_reports WHERE report_id = ? AND offer_id = ?",
                    (safe_report_id, safe_offer_id),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        return self._row_to_report(row)

    def get_report_by_path(
        self, *, offer_id: str, report_path: str
    ) -> dict[str, Any] | None:
        safe_offer_id = _offer_id(offer_id)
        if type(report_path) is not str:
            raise TypeError("report_path must be a string")
        pure = PurePosixPath(report_path)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts or pure.parts[0] != safe_offer_id:
            raise ValueError("report_path is outside the requested offer")
        self._resolved_report_file(report_path)
        if not self.path.is_file():
            return None
        with self._connect_readonly() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM product_publication_reports WHERE report_path = ? AND offer_id = ?",
                    (pure.as_posix(), safe_offer_id),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        return self._row_to_report(row)

    def list_reports(
        self, *, offer_id: str, revision: int | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        safe_offer_id = _offer_id(offer_id)
        safe_revision = _revision(revision) if revision is not None else None
        safe_limit = _exact_nonnegative_int(limit, "limit")
        if safe_limit == 0 or safe_limit > 100:
            raise ValueError("limit must be between 1 and 100")
        if not self.path.is_file():
            return []
        if safe_revision is None:
            query = "SELECT * FROM product_publication_reports WHERE offer_id = ? ORDER BY created_at DESC, report_id DESC LIMIT ?"
            parameters: tuple[object, ...] = (safe_offer_id, safe_limit)
        else:
            query = "SELECT * FROM product_publication_reports WHERE offer_id = ? AND revision = ? ORDER BY created_at DESC, report_id DESC LIMIT ?"
            parameters = (safe_offer_id, safe_revision, safe_limit)
        with self._connect_readonly() as conn:
            try:
                rows = conn.execute(query, parameters).fetchall()
            except sqlite3.OperationalError:
                return []
        return [self._row_to_report(row) for row in rows]

    def latest_report(
        self, *, offer_id: str, revision: int | None = None
    ) -> dict[str, Any] | None:
        reports = self.list_reports(offer_id=offer_id, revision=revision, limit=1)
        return reports[0] if reports else None


def public_publication_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return the Product Center projection, intentionally excluding file paths."""
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_id": report["report_id"],
        "run_id": report["run_id"],
        "offer_id": report["offer_id"],
        "revision": report["revision"],
        "plan_id": report["plan_id"],
        "snapshot": dict(report["snapshot"]),
        "status": report["status"],
        "status_label": STATUS_LABELS[report["status"]],
        "summary": dict(report["summary"]),
        "summary_digest": report["summary_digest"],
        "created_at": report["created_at"],
        "updated_at": report["updated_at"],
    }


def default_product_publication_report_store() -> ProductPublicationReportStore:
    return ProductPublicationReportStore()
