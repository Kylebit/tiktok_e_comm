"""Durable control-plane state for governed multi-channel releases.

The store owns no marketplace clients and performs no commerce-database
writes.  Callers must pass an explicit SQLite path in tests or may opt into
the default Orbit platform database at integration time.  Reading a missing
store is side-effect free; schema creation only happens on a write.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config import ROOT


DEFAULT_RELEASE_STORE_PATH = ROOT / "data" / "orbit_platform.db"

# One common draft, ten TikTok stores/sites, four Shopee countries and Ozon RU.
# A plan may select any non-empty subset, but cannot invent another adapter
# name or country.
RELEASE_TARGET_LABELS: tuple[str, ...] = (
    "miaoshou:COMMON",
    "tiktok:LH_PH",
    "tiktok:LH_MY",
    "tiktok:LH_TH",
    "tiktok:LH_VN",
    "tiktok:HB_PH",
    "tiktok:HB_MY",
    "tiktok:HB_TH",
    "tiktok:HB_VN",
    "tiktok:MX",
    "tiktok:GB",
    "shopee:PH",
    "shopee:MY",
    "shopee:TH",
    "shopee:VN",
    "ozon:RU",
)
_TARGET_SET = frozenset(RELEASE_TARGET_LABELS)

PLAN_PENDING_APPROVAL = "PENDING_APPROVAL"
PLAN_APPROVED = "APPROVED"
SUPERSEDED = "SUPERSEDED"

RUN_PENDING = "PENDING"
RUN_RUNNING = "RUNNING"
RUN_PARTIAL_FAILED = "PARTIAL_FAILED"
RUN_FAILED = "FAILED"
RUN_SUCCEEDED = "SUCCEEDED"
RUN_AWAITING_MANUAL_VERIFICATION = "AWAITING_MANUAL_VERIFICATION"
RUN_COMPLETED_WITH_MANUAL_VERIFICATION = "COMPLETED_WITH_MANUAL_VERIFICATION"

TARGET_PENDING = "PENDING"
TARGET_RUNNING = "RUNNING"
TARGET_FAILED = "FAILED"
TARGET_SUCCEEDED = "SUCCEEDED"
TARGET_SUBMITTED_UNVERIFIED = "SUBMITTED_UNVERIFIED"
TARGET_MANUALLY_VERIFIED = "MANUALLY_VERIFIED"


class ReleaseStoreError(RuntimeError):
    """Base error for an invalid release-store operation."""


class ReleaseAuthorizationError(ReleaseStoreError):
    """The exact Kyle approval gate was not satisfied."""


class ImmutableReleaseError(ReleaseStoreError):
    """An existing immutable record was presented with different content."""


class SkuReservationConflict(ReleaseStoreError):
    """The numeric last-four seller SKU is reserved by another active plan."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS release_plans (
    plan_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    seller_sku TEXT NOT NULL,
    sku_key TEXT NOT NULL,
    product_package_id TEXT NOT NULL,
    content_package_id TEXT NOT NULL,
    target_labels_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL UNIQUE,
    confirmation_token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN ('PENDING_APPROVAL', 'APPROVED', 'SUPERSEDED')
    ),
    created_at TEXT NOT NULL,
    approved_at TEXT,
    superseded_at TEXT,
    superseded_by_plan_id TEXT,
    supersede_reason TEXT,
    FOREIGN KEY (superseded_by_plan_id) REFERENCES release_plans(plan_id)
);
CREATE INDEX IF NOT EXISTS idx_release_plans_product_created
    ON release_plans(product_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_release_plans_status
    ON release_plans(status, created_at DESC);

CREATE TABLE IF NOT EXISTS release_approvals (
    approval_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE,
    payload_digest TEXT NOT NULL,
    confirmation_token TEXT NOT NULL,
    approved_by TEXT NOT NULL CHECK (approved_by = 'Kyle'),
    user_approved INTEGER NOT NULL CHECK (user_approved = 1),
    status TEXT NOT NULL CHECK (status IN ('APPROVED', 'SUPERSEDED')),
    approved_at TEXT NOT NULL,
    superseded_at TEXT,
    FOREIGN KEY (plan_id) REFERENCES release_plans(plan_id)
);
CREATE INDEX IF NOT EXISTS idx_release_approvals_status
    ON release_approvals(status, approved_at DESC);

CREATE TABLE IF NOT EXISTS release_runs (
    run_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE,
    approval_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'PENDING', 'RUNNING', 'PARTIAL_FAILED', 'FAILED',
            'SUCCEEDED', 'SUPERSEDED'
        )
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (plan_id) REFERENCES release_plans(plan_id),
    FOREIGN KEY (approval_id) REFERENCES release_approvals(approval_id)
);
CREATE INDEX IF NOT EXISTS idx_release_runs_status
    ON release_runs(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS release_target_runs (
    run_id TEXT NOT NULL,
    target_label TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN ('PENDING', 'RUNNING', 'FAILED', 'SUCCEEDED', 'SUPERSEDED')
    ),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    external_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (run_id, target_label),
    FOREIGN KEY (run_id) REFERENCES release_runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_release_target_runs_status
    ON release_target_runs(run_id, status);

CREATE TABLE IF NOT EXISTS release_target_readbacks (
    run_id TEXT NOT NULL,
    target_label TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    PRIMARY KEY (run_id, target_label),
    FOREIGN KEY (run_id, target_label)
        REFERENCES release_target_runs(run_id, target_label)
);

CREATE TABLE IF NOT EXISTS release_target_submissions (
    run_id TEXT NOT NULL,
    target_label TEXT NOT NULL,
    external_id TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('SUBMITTED_UNVERIFIED', 'MANUALLY_VERIFIED')
    ),
    submitted_at TEXT NOT NULL,
    verified_by TEXT,
    verified_at TEXT,
    verification_evidence_json TEXT,
    verification_evidence_digest TEXT,
    PRIMARY KEY (run_id, target_label),
    FOREIGN KEY (run_id, target_label)
        REFERENCES release_target_runs(run_id, target_label)
);
CREATE INDEX IF NOT EXISTS idx_release_target_submissions_status
    ON release_target_submissions(run_id, status);

CREATE TABLE IF NOT EXISTS release_sku_reservations (
    reservation_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE,
    product_id TEXT NOT NULL,
    seller_sku TEXT NOT NULL,
    sku_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('ACTIVE', 'RELEASED', 'SUPERSEDED')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    released_at TEXT,
    FOREIGN KEY (plan_id) REFERENCES release_plans(plan_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_release_active_sku_key
    ON release_sku_reservations(sku_key)
    WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_release_sku_product
    ON release_sku_reservations(product_id, status);

CREATE TRIGGER IF NOT EXISTS trg_release_plan_immutable
BEFORE UPDATE OF
    plan_id, product_id, seller_sku, sku_key, product_package_id,
    content_package_id, target_labels_json, payload_json, payload_digest,
    confirmation_token, created_at
ON release_plans
BEGIN
    SELECT RAISE(ABORT, 'release plan payload is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_release_approval_immutable
BEFORE UPDATE OF
    approval_id, plan_id, payload_digest, confirmation_token,
    approved_by, user_approved, approved_at
ON release_approvals
BEGIN
    SELECT RAISE(ABORT, 'release approval is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_release_run_identity_immutable
BEFORE UPDATE OF run_id, plan_id, approval_id, created_at
ON release_runs
BEGIN
    SELECT RAISE(ABORT, 'release run identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_release_target_identity_immutable
BEFORE UPDATE OF run_id, target_label, idempotency_key, created_at
ON release_target_runs
BEGIN
    SELECT RAISE(ABORT, 'release target identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_release_sku_reservation_immutable
BEFORE UPDATE OF
    reservation_id, plan_id, product_id, seller_sku, sku_key, created_at
ON release_sku_reservations
BEGIN
    SELECT RAISE(ABORT, 'release SKU reservation identity is immutable');
END;
"""


def _text(value: object) -> str:
    return str(value or "").strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("release payload must be JSON-serializable") from error


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _legacy_unverified_submission(row: Mapping[str, Any]) -> dict[str, Any] | None:
    error = _text(row.get("error")).lower()
    external_id = _text(row.get("external_id"))
    if not (
        row.get("status") == TARGET_FAILED
        and external_id
        and "official" in error
        and "readback" in error
        and any(
            marker in error
            for marker in ("unavailable", "no authorised", "no authorized")
        )
    ):
        return None
    evidence = {
        "source": "legacy_release_run_ledger",
        "accepted": True,
        "external_id": external_id,
        "legacy_attempts": row.get("attempts"),
        "legacy_detail": row.get("error"),
        "migration": "accepted_without_official_readback/v1",
    }
    encoded = _canonical_json(evidence)
    return {
        "external_id": external_id,
        "evidence": evidence,
        "evidence_digest": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "status": TARGET_SUBMITTED_UNVERIFIED,
        "submitted_at": row.get("completed_at"),
        "verified_by": None,
        "verified_at": None,
        "verification_evidence": None,
        "verification_evidence_digest": None,
        "legacy_inferred": True,
    }


def _required_text(payload: Mapping[str, Any], field: str) -> str:
    value = _text(payload.get(field))
    if not value:
        raise ValueError(f"release plan requires {field}")
    return value


def _sku_key(seller_sku: str) -> str:
    if not seller_sku.isdigit() or len(seller_sku) > 32:
        raise ValueError("seller_sku must contain 1-32 digits")
    return seller_sku[-4:].zfill(4)


def _target_labels(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ValueError("release plan targets must be a non-empty list")
    raw = [_text(label) for label in value]
    if not raw or any(not label for label in raw):
        raise ValueError("release plan targets must be a non-empty list")
    if len(set(raw)) != len(raw):
        raise ValueError("release plan targets must not contain duplicates")
    unsupported = sorted(set(raw) - _TARGET_SET)
    if unsupported:
        raise ValueError(f"unsupported release targets: {', '.join(unsupported)}")
    selected = set(raw)
    return tuple(label for label in RELEASE_TARGET_LABELS if label in selected)


def _validated_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("release plan payload must be a mapping")
    plan = dict(payload)
    for field in (
        "plan_id",
        "product_id",
        "seller_sku",
        "product_package_id",
        "content_package_id",
    ):
        plan[field] = _required_text(plan, field)
    _sku_key(plan["seller_sku"])
    plan["targets"] = list(_target_labels(plan.get("targets")))
    return plan


def preview_release_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable identity/token for a plan without opening SQLite."""
    plan = _validated_plan(payload)
    encoded = _canonical_json(plan)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return {
        "plan_id": plan["plan_id"],
        "product_id": plan["product_id"],
        "seller_sku": plan["seller_sku"],
        "product_package_id": plan["product_package_id"],
        "content_package_id": plan["content_package_id"],
        "targets": list(plan["targets"]),
        "payload": plan,
        "payload_digest": digest,
        "confirmation_token": f"PUBLISH-{digest[:16].upper()}",
        "status": "NOT_PERSISTED",
        "persisted": False,
        "approved": False,
    }


def _target_idempotency_key(payload_digest: str, target_label: str) -> str:
    target_digest = _sha256(
        {
            "payload_digest": payload_digest,
            "target_label": target_label,
        }
    )
    channel, site = target_label.split(":", 1)
    return f"publish:{channel}:{site}:{target_digest[:24]}"


def _plan_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "plan_id": row["plan_id"],
        "product_id": row["product_id"],
        "seller_sku": row["seller_sku"],
        "sku_key": row["sku_key"],
        "product_package_id": row["product_package_id"],
        "content_package_id": row["content_package_id"],
        "targets": json.loads(row["target_labels_json"]),
        "payload": json.loads(row["payload_json"]),
        "payload_digest": row["payload_digest"],
        "confirmation_token": row["confirmation_token"],
        "status": row["status"],
        "created_at": row["created_at"],
        "approved_at": row["approved_at"],
        "superseded_at": row["superseded_at"],
        "superseded_by_plan_id": row["superseded_by_plan_id"],
        "supersede_reason": row["supersede_reason"],
    }


class ReleaseStore:
    """Transactional SQLite repository for the V1 release state machine."""

    def __init__(self, path: str | Path = DEFAULT_RELEASE_STORE_PATH) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _connect_readonly(self) -> sqlite3.Connection:
        source = self.path.resolve()
        connection = sqlite3.connect(
            source.as_uri() + "?mode=ro",
            uri=True,
            timeout=30,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA query_only=ON")
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(_SCHEMA)
        self._backfill_legacy_unverified_submissions(connection)

    def _backfill_legacy_unverified_submissions(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """Classify old accepted/no-readback failures without retrying them.

        Earlier releases represented a successful Miaoshou submission with no
        authorised marketplace readback as ``FAILED``.  The publish endpoint
        consequently retried those rows.  Preserve the physical legacy row for
        schema compatibility, but add a durable submission receipt that makes
        the public state terminal and non-retryable.
        """

        rows = connection.execute(
            """
            SELECT target.run_id, target.target_label, target.external_id,
                   target.error, target.attempts, target.completed_at
            FROM release_target_runs AS target
            LEFT JOIN release_target_submissions AS submission
              ON submission.run_id = target.run_id
             AND submission.target_label = target.target_label
            WHERE target.status = 'FAILED'
              AND target.external_id IS NOT NULL
              AND submission.run_id IS NULL
              AND lower(COALESCE(target.error, '')) LIKE '%official%'
              AND lower(COALESCE(target.error, '')) LIKE '%readback%'
              AND (
                    lower(COALESCE(target.error, '')) LIKE '%unavailable%'
                 OR lower(COALESCE(target.error, '')) LIKE '%no authorised%'
                 OR lower(COALESCE(target.error, '')) LIKE '%no authorized%'
              )
            """
        ).fetchall()
        for row in rows:
            evidence = {
                "source": "legacy_release_run_ledger",
                "accepted": True,
                "external_id": row["external_id"],
                "legacy_attempts": row["attempts"],
                "legacy_detail": row["error"],
                "migration": "accepted_without_official_readback/v1",
            }
            encoded = _canonical_json(evidence)
            connection.execute(
                """
                INSERT OR IGNORE INTO release_target_submissions (
                    run_id, target_label, external_id, evidence_json,
                    evidence_digest, status, submitted_at
                ) VALUES (?, ?, ?, ?, ?, 'SUBMITTED_UNVERIFIED', ?)
                """,
                (
                    row["run_id"],
                    row["target_label"],
                    row["external_id"],
                    encoded,
                    hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                    row["completed_at"] or _utc_now(),
                ),
            )

    @contextmanager
    def _transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            self._ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def available_targets(self) -> tuple[str, ...]:
        """Return the exact V1 target allowlist in dependency-display order."""
        return RELEASE_TARGET_LABELS

    def preview_plan(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Build the exact write-free preview consumed by the formal UI."""
        return preview_release_plan(payload)

    def create_plan(
        self,
        payload: Mapping[str, Any],
        *,
        supersedes_plan_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist an immutable plan and reserve its numeric seller SKU.

        Repeating the same plan ID and digest is idempotent.  Reusing the plan
        ID for different content is rejected.  ``supersedes_plan_id`` performs
        successor creation, old-plan supersession and SKU hand-off atomically.
        """
        plan = _validated_plan(payload)
        plan_id = plan["plan_id"]
        predecessor_id = _text(supersedes_plan_id) or None
        if predecessor_id == plan_id:
            raise ValueError("a release plan cannot supersede itself")
        encoded = _canonical_json(plan)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        token = f"PUBLISH-{digest[:16].upper()}"
        targets_json = _canonical_json(plan["targets"])
        sku_key = _sku_key(plan["seller_sku"])
        now = _utc_now()

        try:
            with self._transaction() as connection:
                existing = connection.execute(
                    "SELECT * FROM release_plans WHERE plan_id = ?",
                    (plan_id,),
                ).fetchone()
                if existing:
                    if existing["payload_digest"] != digest:
                        raise ImmutableReleaseError(
                            "plan_id already belongs to a different payload digest"
                        )
                    result = _plan_from_row(existing)
                    result["created"] = False
                    return result

                predecessor = None
                if predecessor_id:
                    predecessor = connection.execute(
                        "SELECT * FROM release_plans WHERE plan_id = ?",
                        (predecessor_id,),
                    ).fetchone()
                    if not predecessor:
                        raise ReleaseStoreError("superseded release plan was not found")
                    if predecessor["status"] == SUPERSEDED:
                        raise ReleaseStoreError("superseded release plan is already superseded")
                    if predecessor["product_id"] != plan["product_id"]:
                        raise ReleaseStoreError(
                            "a successor plan must belong to the same product_id"
                        )

                connection.execute(
                    """
                    INSERT INTO release_plans (
                        plan_id, product_id, seller_sku, sku_key,
                        product_package_id, content_package_id,
                        target_labels_json, payload_json, payload_digest,
                        confirmation_token, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan_id,
                        plan["product_id"],
                        plan["seller_sku"],
                        sku_key,
                        plan["product_package_id"],
                        plan["content_package_id"],
                        targets_json,
                        encoded,
                        digest,
                        token,
                        PLAN_PENDING_APPROVAL,
                        now,
                    ),
                )
                if predecessor is not None:
                    self._supersede_in_transaction(
                        connection,
                        predecessor_id,
                        superseded_by_plan_id=plan_id,
                        reason="replaced by a newer immutable release plan",
                        now=now,
                    )

                connection.execute(
                    """
                    INSERT INTO release_sku_reservations (
                        reservation_id, plan_id, product_id, seller_sku,
                        sku_key, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
                    """,
                    (
                        f"sku-reservation:{plan_id}",
                        plan_id,
                        plan["product_id"],
                        plan["seller_sku"],
                        sku_key,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM release_plans WHERE plan_id = ?",
                    (plan_id,),
                ).fetchone()
                result = _plan_from_row(row)
                result["created"] = True
                return result
        except sqlite3.IntegrityError as error:
            if "release_sku_reservations.sku_key" in str(error):
                raise SkuReservationConflict(
                    f"seller SKU key {sku_key} is already reserved"
                ) from error
            raise

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        with self._connect_readonly() as connection:
            try:
                row = connection.execute(
                    "SELECT * FROM release_plans WHERE plan_id = ?",
                    (_text(plan_id),),
                ).fetchone()
                if not row:
                    return None
                result = _plan_from_row(row)
                approval = connection.execute(
                    "SELECT * FROM release_approvals WHERE plan_id = ?",
                    (_text(plan_id),),
                ).fetchone()
                reservation = connection.execute(
                    "SELECT * FROM release_sku_reservations WHERE plan_id = ?",
                    (_text(plan_id),),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        result["approval"] = dict(approval) if approval else None
        result["sku_reservation"] = dict(reservation) if reservation else None
        return result

    def active_plan_for_product(self, product_id: str) -> dict[str, Any] | None:
        """Return the newest non-superseded plan without creating the store."""
        if not self.path.is_file():
            return None
        with self._connect_readonly() as connection:
            try:
                row = connection.execute(
                    """
                    SELECT plan_id FROM release_plans
                    WHERE product_id = ? AND status != 'SUPERSEDED'
                    ORDER BY created_at DESC, plan_id DESC
                    LIMIT 1
                    """,
                    (_text(product_id),),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        return self.get_plan(row["plan_id"]) if row else None

    def approve_plan(
        self,
        plan_id: str,
        *,
        approved_by: str,
        user_approved: bool,
        confirmation_token: str,
    ) -> dict[str, Any]:
        """Persist Kyle's exact approval for one immutable payload digest."""
        if user_approved is not True:
            raise ReleaseAuthorizationError("literal user_approved=True is required")
        if _text(approved_by) != "Kyle":
            raise ReleaseAuthorizationError("approved_by must be Kyle")
        with self._transaction() as connection:
            plan = connection.execute(
                "SELECT * FROM release_plans WHERE plan_id = ?",
                (_text(plan_id),),
            ).fetchone()
            if not plan:
                raise ReleaseStoreError("release plan was not found")
            if plan["status"] == SUPERSEDED:
                raise ReleaseAuthorizationError("a superseded plan cannot be approved")
            if _text(confirmation_token) != plan["confirmation_token"]:
                raise ReleaseAuthorizationError(
                    "confirmation token does not match the immutable release plan"
                )
            existing = connection.execute(
                "SELECT * FROM release_approvals WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()
            if existing:
                if (
                    existing["payload_digest"] != plan["payload_digest"]
                    or existing["confirmation_token"] != plan["confirmation_token"]
                    or existing["approved_by"] != "Kyle"
                ):
                    raise ImmutableReleaseError(
                        "release plan already has a different approval"
                    )
                return {**dict(existing), "created": False}

            now = _utc_now()
            approval_id = f"release-approval:{plan['payload_digest'][:24]}"
            connection.execute(
                """
                INSERT INTO release_approvals (
                    approval_id, plan_id, payload_digest, confirmation_token,
                    approved_by, user_approved, status, approved_at
                ) VALUES (?, ?, ?, ?, 'Kyle', 1, 'APPROVED', ?)
                """,
                (
                    approval_id,
                    plan["plan_id"],
                    plan["payload_digest"],
                    plan["confirmation_token"],
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE release_plans
                SET status = 'APPROVED', approved_at = ?
                WHERE plan_id = ?
                """,
                (now, plan["plan_id"]),
            )
            row = connection.execute(
                "SELECT * FROM release_approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            return {**dict(row), "created": True}

    def start_run(
        self,
        plan_id: str,
        *,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Create one durable run and one idempotent target row per selection."""
        with self._transaction() as connection:
            plan = connection.execute(
                "SELECT * FROM release_plans WHERE plan_id = ?",
                (_text(plan_id),),
            ).fetchone()
            if not plan:
                raise ReleaseStoreError("release plan was not found")
            if plan["status"] != PLAN_APPROVED:
                raise ReleaseAuthorizationError(
                    "release plan requires an active Kyle approval"
                )
            approval = connection.execute(
                """
                SELECT * FROM release_approvals
                WHERE plan_id = ? AND status = 'APPROVED'
                """,
                (plan["plan_id"],),
            ).fetchone()
            if not approval:
                raise ReleaseAuthorizationError(
                    "release plan requires an active Kyle approval"
                )
            existing = connection.execute(
                "SELECT run_id FROM release_runs WHERE plan_id = ?",
                (plan["plan_id"],),
            ).fetchone()
            if existing:
                return self._run_in_transaction(connection, existing["run_id"])

            clean_run_id = _text(run_id) or f"release-run:{plan['payload_digest'][:24]}"
            conflict = connection.execute(
                "SELECT plan_id FROM release_runs WHERE run_id = ?",
                (clean_run_id,),
            ).fetchone()
            if conflict:
                raise ImmutableReleaseError(
                    "run_id already belongs to a different release plan"
                )
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO release_runs (
                    run_id, plan_id, approval_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'PENDING', ?, ?)
                """,
                (
                    clean_run_id,
                    plan["plan_id"],
                    approval["approval_id"],
                    now,
                    now,
                ),
            )
            targets = json.loads(plan["target_labels_json"])
            connection.executemany(
                """
                INSERT INTO release_target_runs (
                    run_id, target_label, idempotency_key, status,
                    attempts, created_at, updated_at
                ) VALUES (?, ?, ?, 'PENDING', 0, ?, ?)
                """,
                [
                    (
                        clean_run_id,
                        label,
                        _target_idempotency_key(plan["payload_digest"], label),
                        now,
                        now,
                    )
                    for label in targets
                ],
            )
            return self._run_in_transaction(connection, clean_run_id)

    def begin_target(self, run_id: str, target_label: str) -> dict[str, Any]:
        """Claim one pending target attempt before any adapter is called."""
        with self._transaction() as connection:
            row = self._target_for_update(connection, run_id, target_label)
            self._require_active_run(connection, row["run_id"])
            if row["status"] != TARGET_PENDING:
                raise ReleaseStoreError(
                    f"target must be PENDING before begin; found {row['status']}"
                )
            now = _utc_now()
            connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'RUNNING', attempts = attempts + 1,
                    error = NULL, completed_at = NULL,
                    updated_at = ?
                WHERE run_id = ? AND target_label = ?
                """,
                (now, row["run_id"], row["target_label"]),
            )
            connection.execute(
                """
                UPDATE release_runs
                SET status = 'RUNNING', updated_at = ?, completed_at = NULL
                WHERE run_id = ?
                """,
                (now, row["run_id"]),
            )
            return dict(
                connection.execute(
                    """
                    SELECT * FROM release_target_runs
                    WHERE run_id = ? AND target_label = ?
                    """,
                    (row["run_id"], row["target_label"]),
                ).fetchone()
            )

    def record_target_success(
        self,
        run_id: str,
        target_label: str,
        *,
        external_id: str | None = None,
        readback_evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record verified success; repeated identical readback is idempotent."""
        evidence_json = (
            _canonical_json(dict(readback_evidence))
            if readback_evidence is not None
            else None
        )
        evidence_digest = (
            hashlib.sha256(evidence_json.encode("utf-8")).hexdigest()
            if evidence_json is not None
            else None
        )
        with self._transaction() as connection:
            row = self._target_for_update(connection, run_id, target_label)
            clean_external_id = _text(external_id) or None
            if row["status"] == TARGET_SUCCEEDED:
                if (row["external_id"] or None) != clean_external_id:
                    raise ImmutableReleaseError(
                        "successful target already has a different external_id"
                    )
                existing_evidence = connection.execute(
                    """
                    SELECT evidence_json, evidence_digest
                    FROM release_target_readbacks
                    WHERE run_id = ? AND target_label = ?
                    """,
                    (row["run_id"], row["target_label"]),
                ).fetchone()
                if evidence_json is not None and existing_evidence:
                    if existing_evidence["evidence_digest"] != evidence_digest:
                        raise ImmutableReleaseError(
                            "successful target already has different readback evidence"
                        )
                elif evidence_json is not None:
                    connection.execute(
                        """
                        INSERT INTO release_target_readbacks (
                            run_id, target_label, evidence_json,
                            evidence_digest, verified_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            row["run_id"],
                            row["target_label"],
                            evidence_json,
                            evidence_digest,
                            _utc_now(),
                        ),
                    )
                result = dict(row)
                result["readback"] = (
                    json.loads(evidence_json)
                    if evidence_json is not None
                    else (
                        json.loads(existing_evidence["evidence_json"])
                        if existing_evidence
                        else None
                    )
                )
                return result
            self._require_active_run(connection, row["run_id"])
            if row["status"] != TARGET_RUNNING:
                raise ReleaseStoreError(
                    f"target must be RUNNING before success; found {row['status']}"
                )
            now = _utc_now()
            if evidence_json is not None:
                connection.execute(
                    """
                    INSERT INTO release_target_readbacks (
                        run_id, target_label, evidence_json,
                        evidence_digest, verified_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row["run_id"],
                        row["target_label"],
                        evidence_json,
                        evidence_digest,
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'SUCCEEDED', external_id = ?, error = NULL,
                    updated_at = ?, completed_at = ?
                WHERE run_id = ? AND target_label = ?
                """,
                (
                    clean_external_id,
                    now,
                    now,
                    row["run_id"],
                    row["target_label"],
                ),
            )
            self._refresh_run_status(connection, row["run_id"], now=now)
            return dict(
                connection.execute(
                    """
                    SELECT * FROM release_target_runs
                    WHERE run_id = ? AND target_label = ?
                    """,
                    (row["run_id"], row["target_label"]),
                ).fetchone()
            )

    def record_target_failure(
        self,
        run_id: str,
        target_label: str,
        *,
        error: str,
        external_id: str | None = None,
    ) -> dict[str, Any]:
        """Record one failed attempt without changing its idempotency key."""
        clean_error = _text(error)
        if not clean_error:
            raise ValueError("target failure requires an error")
        clean_error = clean_error[:4000]
        with self._transaction() as connection:
            row = self._target_for_update(connection, run_id, target_label)
            clean_external_id = _text(external_id) or None
            if row["status"] == TARGET_FAILED:
                if (
                    row["error"] == clean_error
                    and (row["external_id"] or None) == clean_external_id
                ):
                    return dict(row)
                raise ImmutableReleaseError(
                    "failed target already records a different result"
                )
            self._require_active_run(connection, row["run_id"])
            if row["status"] != TARGET_RUNNING:
                raise ReleaseStoreError(
                    f"target must be RUNNING before failure; found {row['status']}"
                )
            now = _utc_now()
            connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'FAILED', external_id = ?, error = ?,
                    updated_at = ?, completed_at = ?
                WHERE run_id = ? AND target_label = ?
                """,
                (
                    clean_external_id,
                    clean_error,
                    now,
                    now,
                    row["run_id"],
                    row["target_label"],
                ),
            )
            self._refresh_run_status(connection, row["run_id"], now=now)
            return dict(
                connection.execute(
                    """
                    SELECT * FROM release_target_runs
                    WHERE run_id = ? AND target_label = ?
                    """,
                    (row["run_id"], row["target_label"]),
                ).fetchone()
            )

    def record_target_submission(
        self,
        run_id: str,
        target_label: str,
        *,
        external_id: str,
        submission_evidence: Mapping[str, Any],
        detail: str,
    ) -> dict[str, Any]:
        """Persist one accepted submission that has no authorised API readback.

        This is deliberately not a failure and deliberately not a verified
        success.  The companion receipt makes the target terminal for automatic
        execution while retaining the legacy physical target status required
        by existing SQLite databases.
        """

        clean_external_id = _text(external_id)
        clean_detail = _text(detail)
        if not clean_external_id:
            raise ValueError("accepted submission requires an external_id")
        if not clean_detail:
            raise ValueError("accepted submission requires a detail")
        evidence = dict(submission_evidence)
        if evidence.get("accepted") is not True:
            raise ValueError("submission evidence must record accepted=true")
        evidence_json = _canonical_json(evidence)
        evidence_digest = hashlib.sha256(
            evidence_json.encode("utf-8")
        ).hexdigest()
        with self._transaction() as connection:
            row = self._target_for_update(connection, run_id, target_label)
            existing = connection.execute(
                """
                SELECT * FROM release_target_submissions
                WHERE run_id = ? AND target_label = ?
                """,
                (row["run_id"], row["target_label"]),
            ).fetchone()
            if existing:
                if (
                    existing["external_id"] != clean_external_id
                    or existing["evidence_digest"] != evidence_digest
                ):
                    raise ImmutableReleaseError(
                        "accepted target already has different submission evidence"
                    )
                run = self._run_in_transaction(connection, row["run_id"])
                return next(
                    target
                    for target in run["targets"]
                    if target["target_label"] == row["target_label"]
                )
            self._require_active_run(connection, row["run_id"])
            if row["status"] != TARGET_RUNNING:
                raise ReleaseStoreError(
                    "target must be RUNNING before accepted submission; "
                    f"found {row['status']}"
                )
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO release_target_submissions (
                    run_id, target_label, external_id, evidence_json,
                    evidence_digest, status, submitted_at
                ) VALUES (?, ?, ?, ?, ?, 'SUBMITTED_UNVERIFIED', ?)
                """,
                (
                    row["run_id"],
                    row["target_label"],
                    clean_external_id,
                    evidence_json,
                    evidence_digest,
                    now,
                ),
            )
            # Old stores constrain the physical status enum.  FAILED is only a
            # compatibility carrier; _run_in_transaction exposes the truthful
            # SUBMITTED_UNVERIFIED state and retries exclude receipt rows.
            connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'FAILED', external_id = ?, error = ?,
                    updated_at = ?, completed_at = ?
                WHERE run_id = ? AND target_label = ?
                """,
                (
                    clean_external_id,
                    clean_detail[:4000],
                    now,
                    now,
                    row["run_id"],
                    row["target_label"],
                ),
            )
            self._refresh_run_status(connection, row["run_id"], now=now)
            run = self._run_in_transaction(connection, row["run_id"])
            return next(
                target
                for target in run["targets"]
                if target["target_label"] == row["target_label"]
            )

    def record_manual_verification(
        self,
        run_id: str,
        target_label: str,
        *,
        verified_by: str,
        user_verified: bool,
        verification_evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Close an API-less target using an explicit Kyle verification."""

        if verified_by != "Kyle" or user_verified is not True:
            raise ReleaseAuthorizationError(
                "manual target verification requires explicit Kyle confirmation"
            )
        evidence = dict(verification_evidence)
        marketplace_product_id = _text(evidence.get("marketplace_product_id"))
        if not marketplace_product_id:
            raise ValueError(
                "manual verification requires the marketplace product ID"
            )
        required_checks = (
            "identity_matches",
            "seller_sku_matches",
            "single_listing_for_sku",
            "title_matches",
            "price_matches",
            "images_match",
            "logistics_match",
        )
        if any(evidence.get(check) is not True for check in required_checks):
            raise ValueError(
                "manual verification requires all listing checks to be true"
            )
        evidence_json = _canonical_json(evidence)
        evidence_digest = hashlib.sha256(
            evidence_json.encode("utf-8")
        ).hexdigest()
        with self._transaction() as connection:
            row = self._target_for_update(connection, run_id, target_label)
            self._require_active_run(connection, row["run_id"])
            submission = connection.execute(
                """
                SELECT * FROM release_target_submissions
                WHERE run_id = ? AND target_label = ?
                """,
                (row["run_id"], row["target_label"]),
            ).fetchone()
            if not submission:
                raise ReleaseStoreError(
                    "manual verification requires an accepted submission receipt"
                )
            if submission["status"] == TARGET_MANUALLY_VERIFIED:
                if (
                    submission["verified_by"] != verified_by
                    or submission["verification_evidence_digest"]
                    != evidence_digest
                ):
                    raise ImmutableReleaseError(
                        "manual verification is already recorded with different evidence"
                    )
                run = self._run_in_transaction(connection, row["run_id"])
                return next(
                    target
                    for target in run["targets"]
                    if target["target_label"] == row["target_label"]
                )
            now = _utc_now()
            connection.execute(
                """
                UPDATE release_target_submissions
                SET status = 'MANUALLY_VERIFIED', verified_by = ?,
                    verified_at = ?, verification_evidence_json = ?,
                    verification_evidence_digest = ?
                WHERE run_id = ? AND target_label = ?
                """,
                (
                    verified_by,
                    now,
                    evidence_json,
                    evidence_digest,
                    row["run_id"],
                    row["target_label"],
                ),
            )
            run = self._run_in_transaction(connection, row["run_id"])
            return next(
                target
                for target in run["targets"]
                if target["target_label"] == row["target_label"]
            )

    def retry_failed_targets(
        self,
        run_id: str,
        target_labels: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Reset only failed targets; successful external results are retained."""
        with self._transaction() as connection:
            self._require_active_run(connection, _text(run_id))
            failed_rows = connection.execute(
                """
                SELECT target.target_label
                FROM release_target_runs AS target
                LEFT JOIN release_target_submissions AS submission
                  ON submission.run_id = target.run_id
                 AND submission.target_label = target.target_label
                WHERE target.run_id = ?
                  AND target.status = 'FAILED'
                  AND submission.run_id IS NULL
                ORDER BY target.target_label
                """,
                (_text(run_id),),
            ).fetchall()
            failed = {row["target_label"] for row in failed_rows}
            if not failed:
                raise ReleaseStoreError("release run has no failed targets to retry")
            if target_labels is None:
                selected = failed
            else:
                selected = {_text(label) for label in target_labels}
                if not selected or not selected.issubset(failed):
                    raise ReleaseStoreError(
                        "retry targets must be a non-empty subset of failed targets"
                    )
            now = _utc_now()
            placeholders = ",".join("?" for _ in selected)
            connection.execute(
                f"""
                UPDATE release_target_runs
                SET status = 'PENDING', error = NULL,
                    updated_at = ?, completed_at = NULL
                WHERE run_id = ? AND target_label IN ({placeholders})
                """,
                (now, _text(run_id), *sorted(selected)),
            )
            self._refresh_run_status(connection, _text(run_id), now=now)
            return self._run_in_transaction(connection, _text(run_id))

    def recover_interrupted_targets(
        self,
        run_id: str,
        target_labels: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Return explicitly selected stale RUNNING targets to PENDING.

        This is only a crash-recovery control-plane operation.  Marketplace
        adapters must still perform an idempotent read-back before any new
        submission, so recovery cannot duplicate an already-created listing.
        """

        with self._transaction() as connection:
            self._require_active_run(connection, _text(run_id))
            running_rows = connection.execute(
                """
                SELECT target_label FROM release_target_runs
                WHERE run_id = ? AND status = 'RUNNING'
                ORDER BY target_label
                """,
                (_text(run_id),),
            ).fetchall()
            running = {row["target_label"] for row in running_rows}
            if not running:
                raise ReleaseStoreError("release run has no interrupted targets")
            if target_labels is None:
                selected = running
            else:
                selected = {_text(label) for label in target_labels}
                if not selected or not selected.issubset(running):
                    raise ReleaseStoreError(
                        "recovery targets must be a non-empty subset of RUNNING targets"
                    )
            now = _utc_now()
            placeholders = ",".join("?" for _ in selected)
            connection.execute(
                f"""
                UPDATE release_target_runs
                SET status = 'PENDING',
                    error = 'recovered after an interrupted worker',
                    updated_at = ?, completed_at = NULL
                WHERE run_id = ? AND target_label IN ({placeholders})
                """,
                (now, _text(run_id), *sorted(selected)),
            )
            self._refresh_run_status(connection, _text(run_id), now=now)
            return self._run_in_transaction(connection, _text(run_id))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        with self._connect_readonly() as connection:
            try:
                row = connection.execute(
                    "SELECT run_id FROM release_runs WHERE run_id = ?",
                    (_text(run_id),),
                ).fetchone()
                return (
                    self._run_in_transaction(connection, row["run_id"])
                    if row
                    else None
                )
            except sqlite3.OperationalError:
                return None

    def supersede_plan(
        self,
        plan_id: str,
        *,
        superseded_by_plan_id: str | None = None,
        reason: str = "release plan inputs changed",
    ) -> dict[str, Any]:
        """Invalidate approval and unfinished execution without deleting history."""
        with self._transaction() as connection:
            successor = _text(superseded_by_plan_id) or None
            if successor:
                exists = connection.execute(
                    "SELECT 1 FROM release_plans WHERE plan_id = ?",
                    (successor,),
                ).fetchone()
                if not exists:
                    raise ReleaseStoreError("successor release plan was not found")
            self._supersede_in_transaction(
                connection,
                _text(plan_id),
                superseded_by_plan_id=successor,
                reason=_text(reason) or "release plan inputs changed",
                now=_utc_now(),
            )
            row = connection.execute(
                "SELECT * FROM release_plans WHERE plan_id = ?",
                (_text(plan_id),),
            ).fetchone()
            return _plan_from_row(row)

    def active_sku_reservations(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with self._connect_readonly() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT * FROM release_sku_reservations
                    WHERE status = 'ACTIVE'
                    ORDER BY sku_key, plan_id
                    """
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [dict(row) for row in rows]

    def database_health(self) -> dict[str, Any]:
        """Return read-only SQLite integrity evidence for operations/tests."""
        if not self.path.is_file():
            return {"exists": False}
        with self._connect_readonly() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = [
                tuple(row) for row in connection.execute("PRAGMA foreign_key_check")
            ]
            busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        return {
            "exists": True,
            "integrity_check": integrity,
            "foreign_key_violations": foreign_keys,
            "busy_timeout": busy_timeout,
        }

    def _target_for_update(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        target_label: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM release_target_runs
            WHERE run_id = ? AND target_label = ?
            """,
            (_text(run_id), _text(target_label)),
        ).fetchone()
        if not row:
            raise ReleaseStoreError("release target run was not found")
        return row

    def _require_active_run(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT r.*, p.status AS plan_status
            FROM release_runs AS r
            JOIN release_plans AS p ON p.plan_id = r.plan_id
            WHERE r.run_id = ?
            """,
            (_text(run_id),),
        ).fetchone()
        if not row:
            raise ReleaseStoreError("release run was not found")
        if row["plan_status"] != PLAN_APPROVED or row["status"] == SUPERSEDED:
            raise ReleaseAuthorizationError("release run belongs to a superseded plan")
        if row["status"] == RUN_SUCCEEDED:
            raise ReleaseStoreError("release run is already complete")
        return row

    def _run_in_transaction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> dict[str, Any]:
        run = connection.execute(
            "SELECT * FROM release_runs WHERE run_id = ?",
            (_text(run_id),),
        ).fetchone()
        if not run:
            raise ReleaseStoreError("release run was not found")
        targets = connection.execute(
            """
            SELECT * FROM release_target_runs
            WHERE run_id = ?
            ORDER BY rowid
            """,
            (run["run_id"],),
        ).fetchall()
        try:
            readback_rows = connection.execute(
                """
                SELECT target_label, evidence_json, evidence_digest, verified_at
                FROM release_target_readbacks
                WHERE run_id = ?
                """,
                (run["run_id"],),
            )
            readbacks = {
                row["target_label"]: {
                    "evidence": json.loads(row["evidence_json"]),
                    "evidence_digest": row["evidence_digest"],
                    "verified_at": row["verified_at"],
                }
                for row in readback_rows
            }
        except sqlite3.OperationalError:
            # Old stores remain readable before the next controlled write
            # creates the additive evidence table.
            readbacks = {}
        try:
            submission_rows = connection.execute(
                """
                SELECT target_label, external_id, evidence_json,
                       evidence_digest, status, submitted_at, verified_by,
                       verified_at, verification_evidence_json,
                       verification_evidence_digest
                FROM release_target_submissions
                WHERE run_id = ?
                """,
                (run["run_id"],),
            )
            submissions = {
                row["target_label"]: {
                    "external_id": row["external_id"],
                    "evidence": json.loads(row["evidence_json"]),
                    "evidence_digest": row["evidence_digest"],
                    "status": row["status"],
                    "submitted_at": row["submitted_at"],
                    "verified_by": row["verified_by"],
                    "verified_at": row["verified_at"],
                    "verification_evidence": (
                        json.loads(row["verification_evidence_json"])
                        if row["verification_evidence_json"]
                        else None
                    ),
                    "verification_evidence_digest": row[
                        "verification_evidence_digest"
                    ],
                }
                for row in submission_rows
            }
        except sqlite3.OperationalError:
            submissions = {}
        target_payloads: list[dict[str, Any]] = []
        for row in targets:
            payload = dict(row)
            payload["storage_status"] = payload["status"]
            payload["readback"] = readbacks.get(row["target_label"])
            payload["submission"] = (
                submissions.get(row["target_label"])
                or _legacy_unverified_submission(payload)
            )
            if payload["submission"]:
                payload["status"] = payload["submission"]["status"]
            target_payloads.append(payload)
        result = {**dict(run), "targets": target_payloads}
        logical_statuses = [target["status"] for target in target_payloads]
        success_statuses = {TARGET_SUCCEEDED, TARGET_MANUALLY_VERIFIED}
        if logical_statuses and all(
            status in success_statuses for status in logical_statuses
        ):
            result["status"] = (
                RUN_COMPLETED_WITH_MANUAL_VERIFICATION
                if TARGET_MANUALLY_VERIFIED in logical_statuses
                else RUN_SUCCEEDED
            )
            result["completed_at"] = max(
                str(target.get("completed_at") or "")
                for target in target_payloads
            ) or result.get("completed_at")
        elif (
            TARGET_SUBMITTED_UNVERIFIED in logical_statuses
            and not any(
                status in {TARGET_PENDING, TARGET_RUNNING, TARGET_FAILED}
                for status in logical_statuses
            )
        ):
            result["status"] = RUN_AWAITING_MANUAL_VERIFICATION
        elif TARGET_FAILED in logical_statuses:
            completed = sum(
                status in {
                    TARGET_SUCCEEDED,
                    TARGET_SUBMITTED_UNVERIFIED,
                    TARGET_MANUALLY_VERIFIED,
                }
                for status in logical_statuses
            )
            result["status"] = RUN_PARTIAL_FAILED if completed else RUN_FAILED
        return result

    def _refresh_run_status(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        *,
        now: str,
    ) -> None:
        statuses = [
            row["status"]
            for row in connection.execute(
                "SELECT status FROM release_target_runs WHERE run_id = ?",
                (run_id,),
            )
        ]
        if statuses and all(status == TARGET_SUCCEEDED for status in statuses):
            status = RUN_SUCCEEDED
            completed_at = now
        elif TARGET_FAILED in statuses and TARGET_SUCCEEDED in statuses:
            status = RUN_PARTIAL_FAILED
            completed_at = None
        elif TARGET_FAILED in statuses:
            status = RUN_FAILED
            completed_at = None
        elif TARGET_RUNNING in statuses or TARGET_SUCCEEDED in statuses:
            status = RUN_RUNNING
            completed_at = None
        else:
            status = RUN_PENDING
            completed_at = None
        connection.execute(
            """
            UPDATE release_runs
            SET status = ?, updated_at = ?, completed_at = ?
            WHERE run_id = ?
            """,
            (status, now, completed_at, run_id),
        )

    def _supersede_in_transaction(
        self,
        connection: sqlite3.Connection,
        plan_id: str,
        *,
        superseded_by_plan_id: str | None,
        reason: str,
        now: str,
    ) -> None:
        plan = connection.execute(
            "SELECT * FROM release_plans WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        if not plan:
            raise ReleaseStoreError("release plan was not found")
        if plan["status"] == SUPERSEDED:
            same_successor = (
                (plan["superseded_by_plan_id"] or None)
                == (superseded_by_plan_id or None)
            )
            if same_successor:
                return
            raise ImmutableReleaseError(
                "release plan was already superseded by another successor"
            )
        connection.execute(
            """
            UPDATE release_plans
            SET status = 'SUPERSEDED', superseded_at = ?,
                superseded_by_plan_id = ?, supersede_reason = ?
            WHERE plan_id = ?
            """,
            (now, superseded_by_plan_id, reason[:1000], plan_id),
        )
        connection.execute(
            """
            UPDATE release_approvals
            SET status = 'SUPERSEDED', superseded_at = ?
            WHERE plan_id = ? AND status = 'APPROVED'
            """,
            (now, plan_id),
        )
        run_rows = connection.execute(
            """
            SELECT run_id FROM release_runs
            WHERE plan_id = ? AND status != 'SUCCEEDED'
            """,
            (plan_id,),
        ).fetchall()
        for run in run_rows:
            connection.execute(
                """
                UPDATE release_target_runs
                SET status = 'SUPERSEDED', updated_at = ?, completed_at = ?
                WHERE run_id = ? AND status != 'SUCCEEDED'
                """,
                (now, now, run["run_id"]),
            )
            connection.execute(
                """
                UPDATE release_runs
                SET status = 'SUPERSEDED', updated_at = ?, completed_at = ?
                WHERE run_id = ?
                """,
                (now, now, run["run_id"]),
            )
        connection.execute(
            """
            UPDATE release_sku_reservations
            SET status = 'SUPERSEDED', updated_at = ?, released_at = ?
            WHERE plan_id = ? AND status = 'ACTIVE'
            """,
            (now, now, plan_id),
        )


def default_release_store() -> ReleaseStore:
    """Return the production-path store without opening or creating it."""
    return ReleaseStore(DEFAULT_RELEASE_STORE_PATH)
