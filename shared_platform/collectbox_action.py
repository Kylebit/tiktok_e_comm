"""Durable approved-plan control plane for Miaoshou collect-box claims.

The platform owns identity, persistence, retry selection, and public
redaction.  Channel-owned code receives one ephemeral server-derived common
collect-box detail ID and returns one typed platform result.  This module does
not import or call a channel client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Mapping


SCHEMA_VERSION = "collectbox-action-status/v1"
REQUEST_SCHEMA_VERSION = "collectbox-platform-request/v1"
PLATFORMS = ("TIKTOK", "SHOPEE")
PENDING = "PENDING"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED_RETRYABLE = "FAILED_RETRYABLE"
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
IMPORTED = "IMPORTED"
ALREADY_PRESENT = "ALREADY_PRESENT"
MIN_PLATFORM_SPACING_SECONDS = 3.0
_WRITE_CLASS = {
    "TIKTOK": "miaoshou:collectbox:claim:tiktok",
    "SHOPEE": "miaoshou:collectbox:claim:shopee",
}
_SHA256_EMPTY_LIST = hashlib.sha256(b"[]").hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _nonempty_text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value.strip()


def _canonical_positive_identifier(value: object, field: str) -> str:
    if type(value) is int:
        if value <= 0:
            raise ValueError(f"{field} must be positive")
        return str(value)
    if type(value) is not str:
        raise ValueError(f"{field} must be a built-in int or string")
    if (
        not value
        or not value.isascii()
        or not value.isdigit()
        or (len(value) > 1 and value.startswith("0"))
        or int(value) <= 0
    ):
        raise ValueError(f"{field} must be a canonical positive decimal ID")
    return value


def approved_plan_identity(plan: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, Mapping):
        raise ValueError("approved plan must be a mapping")
    if (
        plan.get("status") != "APPROVED"
        or not isinstance(plan.get("approval"), Mapping)
        or plan["approval"].get("status") != "APPROVED"
        or plan["approval"].get("approved_by") != "Kyle"
    ):
        raise ValueError("collect-box action requires a Kyle-approved plan")
    plan_id = _nonempty_text(plan.get("plan_id"), "plan_id")
    offer_id = _canonical_positive_identifier(
        plan.get("product_id"), "offer_id"
    )
    if len(offer_id) > 32:
        raise ValueError("offer_id is too long")
    # The durable ReleasePlan stores the exact revision inside its immutable
    # payload.  A synthetic top-level field is not execution authority.
    revision = (
        plan.get("payload", {}).get("product_revision")
        if isinstance(plan.get("payload"), Mapping)
        else None
    )
    if type(revision) is not int or revision < 1:
        raise ValueError("product_revision must be a positive built-in int")
    payload_digest = plan.get("payload_digest")
    if not _is_sha256(payload_digest):
        raise ValueError("payload_digest must be sha256")
    targets = plan.get("targets")
    if (
        not isinstance(targets, list)
        or not targets
        or any(type(value) is not str or not value.strip() for value in targets)
        or len(set(targets)) != len(targets)
    ):
        raise ValueError("approved plan targets must be unique strings")
    targets_digest = _digest(targets)
    return {
        "plan_id": plan_id,
        "offer_id": offer_id,
        "product_revision": revision,
        "payload_digest": payload_digest,
        "targets_digest": targets_digest,
    }


def _public_plan_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the immutable fields exposed by the frozen HTTP schema."""

    return {
        "plan_id": identity["plan_id"],
        "product_revision": identity["product_revision"],
        "payload_digest": identity["payload_digest"],
        "targets_digest": identity["targets_digest"],
    }


def common_collectbox_identity_digest(plan_id: str, detail_id: object) -> str:
    clean_plan_id = _nonempty_text(plan_id, "plan_id")
    clean_id = _canonical_positive_identifier(
        detail_id, "common collect-box detail ID"
    )
    if len(clean_id) > 32:
        raise ValueError("common collect-box detail ID is too long")
    return _digest(
        {
            "schema_version": "common-collectbox-identity/v1",
            "plan_id": clean_plan_id,
            "common_collect_box_detail_id": clean_id,
        }
    )


def blocked_identity_projection(
    *,
    plan: Mapping[str, Any],
    category: str,
    code: str,
    detail: str,
) -> dict[str, Any]:
    identity = approved_plan_identity(plan)
    projection = CollectBoxActionStore._empty_projection(identity)
    projection["ok"] = False
    projection["action"].update(
        {
            "status": "BLOCKED_IDENTITY",
            "start_allowed": False,
            "retry_allowed": False,
            "terminal": True,
            "error": _redacted_error(category, code, detail),
        }
    )
    projection["canonical_next_action"] = None
    return projection


def ready_projection(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Build the pure, non-persisted two-platform READY projection."""

    return CollectBoxActionStore._empty_projection(
        approved_plan_identity(plan)
    )


def invalid_plan_projection(
    plan: Mapping[str, Any],
    *,
    detail: str,
) -> dict[str, Any]:
    """Keep a malformed legacy plan visible without inventing authority."""

    payload = plan.get("payload") if isinstance(plan.get("payload"), Mapping) else {}
    targets = plan.get("targets")
    targets_digest = (
        _digest(targets)
        if isinstance(targets, list)
        and all(type(value) is str and value for value in targets)
        and len(set(targets)) == len(targets)
        else None
    )
    error = _redacted_error(
        "IDENTITY",
        "collectbox_approved_plan_identity_invalid",
        detail,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "persisted": False,
        "approved_plan": {
            "plan_id": plan.get("plan_id") if type(plan.get("plan_id")) is str else None,
            "product_revision": (
                payload.get("product_revision")
                if type(payload.get("product_revision")) is int
                and not isinstance(payload.get("product_revision"), bool)
                else None
            ),
            "payload_digest": plan.get("payload_digest") if _is_sha256(plan.get("payload_digest")) else None,
            "targets_digest": targets_digest,
        },
        "action": {
            "action_id": None,
            "status": "BLOCKED_IDENTITY",
            "start_allowed": False,
            "retry_allowed": False,
            "terminal": True,
            "error": error,
            "platforms": [
                {
                    "platform": platform,
                    "status": PENDING,
                    "outcome": None,
                    "attempt_count": 0,
                    "retry_allowed": False,
                    "receipt_digest": None,
                    "platform_detail_id_digest": None,
                    "external_writes": {"count": 0, "classes": []},
                    "error": None,
                }
                for platform in PLATFORMS
            ],
        },
        "external_writes_performed": [],
        "external_write_count": 0,
        "canonical_next_action": None,
    }


def _redacted_error(
    category: object,
    code: object,
    detail: object,
) -> dict[str, str]:
    clean_category = _nonempty_text(category, "error category")
    clean_code = _nonempty_text(code, "error code")
    clean_detail = _nonempty_text(detail, "error detail")
    return {
        "category": clean_category,
        "code": clean_code,
        "detail_digest": hashlib.sha256(
            clean_detail.encode("utf-8")
        ).hexdigest(),
    }


def _assert_redacted_evidence(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("receipt evidence must be a mapping")
    forbidden = {
        "token",
        "raw_response",
        "response",
        "title",
        "description",
        "url",
        "image_id",
        "commoncollectboxdetailid",
        "platform_detail_id",
    }

    def visit(node: object) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                if type(key) is not str or key.casefold() in forbidden:
                    raise ValueError("receipt evidence contains a raw field")
                visit(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                visit(child)
        elif node is not None and type(node) not in {
            str,
            int,
            float,
            bool,
        }:
            raise ValueError("receipt evidence contains an invalid value")

    copied = json.loads(_canonical_json(value))
    visit(copied)
    return copied


@dataclass(frozen=True)
class CollectBoxPlatformRequest:
    action_id: str
    plan_id: str
    platform: str
    common_collect_box_detail_id: str
    common_collectbox_identity_digest: str
    payload_digest: str
    targets_digest: str
    idempotency_key: str
    approved_plan_payload: Mapping[str, Any] = field(repr=False)
    approved_targets: tuple[str, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.approved_plan_payload, Mapping):
            raise ValueError("approved_plan_payload must be a mapping")
        copied_payload = json.loads(_canonical_json(self.approved_plan_payload))
        if (
            type(self.approved_targets) is not tuple
            or not self.approved_targets
            or any(
                type(value) is not str or not value.strip()
                for value in self.approved_targets
            )
            or len(set(self.approved_targets)) != len(self.approved_targets)
        ):
            raise ValueError("approved_targets must be unique strings")
        if _digest(list(self.approved_targets)) != self.targets_digest:
            raise ValueError("approved target identity drifted")
        object.__setattr__(self, "approved_plan_payload", copied_payload)

    @property
    def schema_version(self) -> str:
        return REQUEST_SCHEMA_VERSION


@dataclass(frozen=True)
class CollectBoxPlatformResult:
    status: str
    outcome: str | None = None
    platform_detail_id: str | None = None
    external_writes: tuple[str, ...] = ()
    external_write_count: int | None = 0
    receipt_evidence: Mapping[str, Any] | None = None
    error_category: str | None = None
    error_code: str | None = None
    error_detail: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {
            SUCCEEDED,
            FAILED_RETRYABLE,
            RECONCILIATION_REQUIRED,
        }:
            raise ValueError("collect-box result status is invalid")
        if (
            not isinstance(self.external_writes, tuple)
            or any(
                type(value) is not str or not value
                for value in self.external_writes
            )
            or len(set(self.external_writes)) != len(self.external_writes)
        ):
            raise ValueError("external_writes must be unique strings")
        if self.external_write_count is not None and (
            type(self.external_write_count) is not int
            or self.external_write_count < 0
        ):
            raise ValueError("external_write_count is invalid")
        _assert_redacted_evidence(self.receipt_evidence)
        if self.status == SUCCEEDED:
            if self.outcome not in {IMPORTED, ALREADY_PRESENT}:
                raise ValueError("success requires an exact outcome")
            object.__setattr__(
                self,
                "platform_detail_id",
                _canonical_positive_identifier(
                    self.platform_detail_id,
                    "platform_detail_id",
                ),
            )
            if self.outcome == IMPORTED and (
                self.external_write_count is None
                or self.external_write_count < 1
                or not self.external_writes
            ):
                raise ValueError("IMPORTED requires confirmed writes")
            if self.outcome == ALREADY_PRESENT and (
                self.external_write_count != 0 or self.external_writes
            ):
                raise ValueError("ALREADY_PRESENT must be zero-write")
        else:
            if self.outcome is not None or self.platform_detail_id is not None:
                raise ValueError("non-success result cannot carry an outcome")
            _redacted_error(
                self.error_category or "CHANNEL",
                self.error_code or "collectbox_invocation_failed",
                self.error_detail or "collect-box invocation failed",
            )
            if self.status == FAILED_RETRYABLE and (
                self.external_write_count != 0 or self.external_writes
            ):
                raise ValueError(
                    "FAILED_RETRYABLE must prove zero external writes"
                )


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS collectbox_actions (
    action_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE,
    offer_id TEXT NOT NULL,
    product_revision INTEGER NOT NULL,
    payload_digest TEXT NOT NULL,
    targets_digest TEXT NOT NULL,
    common_identity_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    action_error_json TEXT,
    last_invoked_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    completed_at REAL
);
CREATE TABLE IF NOT EXISTS collectbox_action_platforms (
    action_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    retry_allowed INTEGER NOT NULL DEFAULT 0,
    outcome TEXT,
    platform_detail_id TEXT,
    platform_detail_id_digest TEXT,
    external_writes_json TEXT NOT NULL DEFAULT '[]',
    external_write_count INTEGER,
    receipt_json TEXT,
    receipt_digest TEXT,
    error_json TEXT,
    last_invoked_at REAL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (action_id, platform),
    FOREIGN KEY (action_id) REFERENCES collectbox_actions(action_id)
);
"""


class CollectBoxActionStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _ensure_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA_SQL)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _tables_exist(connection: sqlite3.Connection) -> bool:
        names = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name IN (
                    'collectbox_actions', 'collectbox_action_platforms'
                )
                """
            )
        }
        return names == {
            "collectbox_actions",
            "collectbox_action_platforms",
        }

    @staticmethod
    def _action_id(plan_id: str) -> str:
        return f"collectbox-action:{hashlib.sha256(plan_id.encode()).hexdigest()[:24]}"

    def preview(
        self,
        *,
        plan: Mapping[str, Any],
        common_collectbox_identity_digest: str,
    ) -> dict[str, Any]:
        identity = approved_plan_identity(plan)
        if not _is_sha256(common_collectbox_identity_digest):
            raise ValueError("common collect-box identity digest is invalid")
        persisted = self.status(plan_id=identity["plan_id"])
        if persisted is not None:
            self._require_public_identity(persisted, identity)
            return persisted
        return self._empty_projection(identity)

    def status(self, *, plan_id: str) -> dict[str, Any] | None:
        clean_plan_id = _nonempty_text(plan_id, "plan_id")
        if not self.path.is_file():
            return None
        with self._connect() as connection:
            if not self._tables_exist(connection):
                return None
            row = connection.execute(
                "SELECT * FROM collectbox_actions WHERE plan_id = ?",
                (clean_plan_id,),
            ).fetchone()
            if row is None:
                return None
            return self._project(connection, row)

    def recover_interrupted(
        self,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> int:
        """Fail closed persisted invocations interrupted by process death.

        This is an explicit startup seam.  Ordinary GET/status construction is
        pure and never invokes it, while a live ``start`` call never recovers
        its own just-written RUNNING row.
        """

        if not self.path.is_file():
            return 0
        recovered_at = float(now())
        with self._connect() as connection:
            if not self._tables_exist(connection):
                return 0
            connection.execute("BEGIN IMMEDIATE")
            rows = list(
                connection.execute(
                    """
                    SELECT action_id, platform
                    FROM collectbox_action_platforms
                    WHERE status = ?
                    ORDER BY action_id, platform
                    """,
                    (RUNNING,),
                )
            )
            for row in rows:
                platform = row["platform"]
                error = _redacted_error(
                    "UNKNOWN",
                    "collectbox_interrupted_after_dispatch",
                    "process stopped while the collect-box invocation was in flight",
                )
                receipt = {
                    "schema_version": "collectbox-platform-receipt/v1",
                    "status": RECONCILIATION_REQUIRED,
                    "outcome": None,
                    "platform_detail_id_digest": None,
                    "external_writes": [_WRITE_CLASS[platform]],
                    "external_write_count": None,
                    "evidence_digest": _digest({}),
                    "error": error,
                }
                connection.execute(
                    """
                    UPDATE collectbox_action_platforms
                    SET status = ?, retry_allowed = 0, outcome = NULL,
                        platform_detail_id = NULL,
                        platform_detail_id_digest = NULL,
                        external_writes_json = ?,
                        external_write_count = NULL,
                        receipt_json = ?, receipt_digest = ?,
                        error_json = ?, updated_at = ?
                    WHERE action_id = ? AND platform = ? AND status = ?
                    """,
                    (
                        RECONCILIATION_REQUIRED,
                        _canonical_json([_WRITE_CLASS[platform]]),
                        _canonical_json(receipt),
                        _digest(receipt),
                        _canonical_json(error),
                        recovered_at,
                        row["action_id"],
                        platform,
                        RUNNING,
                    ),
                )
                self._refresh_action(
                    connection,
                    row["action_id"],
                    recovered_at,
                )
            connection.commit()
            return len(rows)

    def start(
        self,
        *,
        plan: Mapping[str, Any],
        common_collect_box_detail_id: object,
        adapter: Callable[
            [CollectBoxPlatformRequest], CollectBoxPlatformResult
        ],
        now: Callable[[], float] = time.monotonic,
        wait: Callable[[float], None] = time.sleep,
    ) -> dict[str, Any]:
        identity = approved_plan_identity(plan)
        self._ensure_schema()
        clean_common_id = str(common_collect_box_detail_id).strip()
        common_digest = common_collectbox_identity_digest(
            identity["plan_id"],
            common_collect_box_detail_id,
        )
        action_id = self._action_id(identity["plan_id"])
        self._ensure_action(identity, common_digest, now())
        current = self.status(plan_id=identity["plan_id"])
        assert current is not None
        if current["action"]["terminal"] is True:
            return current
        candidates = [
            row["platform"]
            for row in current["action"]["platforms"]
            if row["status"] in {PENDING, FAILED_RETRYABLE}
        ]
        for platform in candidates:
            with self._connect() as connection:
                action = connection.execute(
                    "SELECT * FROM collectbox_actions WHERE action_id = ?",
                    (action_id,),
                ).fetchone()
                assert action is not None
                last_invoked_at = action["last_invoked_at"]
            current_time = float(now())
            if last_invoked_at is not None:
                remaining = (
                    float(last_invoked_at)
                    + MIN_PLATFORM_SPACING_SECONDS
                    - current_time
                )
                if remaining > 0:
                    wait(remaining)
                    current_time = float(now())
            attempt = self._mark_running(
                action_id,
                platform,
                current_time,
            )
            request = CollectBoxPlatformRequest(
                action_id=action_id,
                plan_id=identity["plan_id"],
                platform=platform,
                common_collect_box_detail_id=clean_common_id,
                common_collectbox_identity_digest=common_digest,
                payload_digest=identity["payload_digest"],
                targets_digest=identity["targets_digest"],
                idempotency_key=_digest(
                    {
                        "schema_version": REQUEST_SCHEMA_VERSION,
                        "action_id": action_id,
                        "platform": platform,
                        "attempt": attempt,
                    }
                ),
                approved_plan_payload=plan["payload"],
                approved_targets=tuple(plan["targets"]),
            )
            try:
                result = adapter(request)
                if not isinstance(result, CollectBoxPlatformResult):
                    raise TypeError(
                        "collect-box adapter returned an invalid result"
                    )
                self._record_result(
                    action_id,
                    platform,
                    result,
                    current_time,
                )
                if result.status == RECONCILIATION_REQUIRED:
                    break
            except Exception as error:
                self._record_result(
                    action_id,
                    platform,
                    CollectBoxPlatformResult(
                        status=RECONCILIATION_REQUIRED,
                        external_writes=(_WRITE_CLASS[platform],),
                        external_write_count=None,
                        error_category="UNKNOWN",
                        error_code="collectbox_invocation_ambiguous",
                        error_detail=f"{type(error).__name__}:{error}",
                    ),
                    current_time,
                )
                break
        projected = self.status(plan_id=identity["plan_id"])
        assert projected is not None
        return projected

    def internal_platform_detail_ids(
        self,
        *,
        plan_id: str,
    ) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        with self._connect() as connection:
            if not self._tables_exist(connection):
                return {}
            action = connection.execute(
                "SELECT action_id FROM collectbox_actions WHERE plan_id = ?",
                (_nonempty_text(plan_id, "plan_id"),),
            ).fetchone()
            if action is None:
                return {}
            return {
                row["platform"]: row["platform_detail_id"]
                for row in connection.execute(
                    """
                    SELECT platform, platform_detail_id
                    FROM collectbox_action_platforms
                    WHERE action_id = ? AND platform_detail_id IS NOT NULL
                    """,
                    (action["action_id"],),
                )
            }

    def _ensure_action(
        self,
        identity: Mapping[str, Any],
        common_digest: str,
        now: float,
    ) -> None:
        action_id = self._action_id(identity["plan_id"])
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM collectbox_actions WHERE plan_id = ?",
                (identity["plan_id"],),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO collectbox_actions (
                        action_id, plan_id, offer_id, product_revision,
                        payload_digest, targets_digest,
                        common_identity_digest, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'READY', ?, ?)
                    """,
                    (
                        action_id,
                        identity["plan_id"],
                        identity["offer_id"],
                        identity["product_revision"],
                        identity["payload_digest"],
                        identity["targets_digest"],
                        common_digest,
                        now,
                        now,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO collectbox_action_platforms (
                        action_id, platform, status, updated_at
                    ) VALUES (?, ?, 'PENDING', ?)
                    """,
                    [(action_id, platform, now) for platform in PLATFORMS],
                )
            else:
                durable = {
                    "plan_id": existing["plan_id"],
                    "offer_id": existing["offer_id"],
                    "product_revision": existing["product_revision"],
                    "payload_digest": existing["payload_digest"],
                    "targets_digest": existing["targets_digest"],
                }
                if durable != dict(identity):
                    raise ValueError(
                        "collect-box approved plan identity drifted"
                    )
                if existing["common_identity_digest"] != common_digest:
                    raise ValueError(
                        "common collect-box identity drifted"
                    )
            connection.commit()

    def _mark_running(
        self,
        action_id: str,
        platform: str,
        invoked_at: float,
    ) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, attempt_count
                FROM collectbox_action_platforms
                WHERE action_id = ? AND platform = ?
                """,
                (action_id, platform),
            ).fetchone()
            if row is None or row["status"] not in {
                PENDING,
                FAILED_RETRYABLE,
            }:
                raise ValueError("collect-box platform is not retryable")
            attempt = int(row["attempt_count"]) + 1
            connection.execute(
                """
                UPDATE collectbox_action_platforms
                SET status = ?, attempt_count = ?, retry_allowed = 0,
                    outcome = NULL, platform_detail_id = NULL,
                    platform_detail_id_digest = NULL,
                    external_writes_json = '[]',
                    external_write_count = 0,
                    receipt_json = NULL, receipt_digest = NULL,
                    error_json = NULL, last_invoked_at = ?, updated_at = ?
                WHERE action_id = ? AND platform = ?
                """,
                (
                    RUNNING,
                    attempt,
                    invoked_at,
                    invoked_at,
                    action_id,
                    platform,
                ),
            )
            connection.execute(
                """
                UPDATE collectbox_actions
                SET status = ?, last_invoked_at = ?, updated_at = ?,
                    completed_at = NULL
                WHERE action_id = ?
                """,
                (RUNNING, invoked_at, invoked_at, action_id),
            )
            connection.commit()
            return attempt

    def _record_result(
        self,
        action_id: str,
        platform: str,
        result: CollectBoxPlatformResult,
        now: float,
    ) -> None:
        expected_class = _WRITE_CLASS[platform]
        platform_name = platform.lower()
        allowed_prefixes = (
            f"miaoshou:collectbox:{platform_name}:detail:update:",
            *(
                (
                    "miaoshou:collectbox:tiktok:detail:create:",
                    "miaoshou:collectbox:tiktok:shop:claim:",
                )
                if platform == "TIKTOK"
                else ()
            ),
        )
        if any(
            write != expected_class
            and not any(write.startswith(prefix) for prefix in allowed_prefixes)
            for write in result.external_writes
        ):
            raise ValueError("collect-box write class is invalid")
        evidence = _assert_redacted_evidence(result.receipt_evidence)
        platform_detail_id = (
            _canonical_positive_identifier(
                result.platform_detail_id,
                "platform_detail_id",
            )
            if result.platform_detail_id is not None
            else None
        )
        platform_detail_digest = (
            _digest(
                {
                    "schema_version": "collectbox-platform-detail/v1",
                    "action_id": action_id,
                    "platform": platform,
                    "platform_detail_id": platform_detail_id,
                }
            )
            if platform_detail_id
            else None
        )
        error = (
            _redacted_error(
                result.error_category or "CHANNEL",
                result.error_code or "collectbox_invocation_failed",
                result.error_detail or "collect-box invocation failed",
            )
            if result.status != SUCCEEDED
            else None
        )
        receipt = {
            "schema_version": "collectbox-platform-receipt/v1",
            "status": result.status,
            "outcome": result.outcome,
            "platform_detail_id_digest": platform_detail_digest,
            "external_writes": list(result.external_writes),
            "external_write_count": result.external_write_count,
            "evidence_digest": _digest(evidence),
            "error": error,
        }
        receipt_digest = _digest(receipt)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE collectbox_action_platforms
                SET status = ?, retry_allowed = ?, outcome = ?,
                    platform_detail_id = ?,
                    platform_detail_id_digest = ?,
                    external_writes_json = ?,
                    external_write_count = ?,
                    receipt_json = ?, receipt_digest = ?,
                    error_json = ?, updated_at = ?
                WHERE action_id = ? AND platform = ?
                """,
                (
                    result.status,
                    int(result.status == FAILED_RETRYABLE),
                    result.outcome,
                    platform_detail_id,
                    platform_detail_digest,
                    _canonical_json(list(result.external_writes)),
                    result.external_write_count,
                    _canonical_json(receipt),
                    receipt_digest,
                    _canonical_json(error) if error else None,
                    now,
                    action_id,
                    platform,
                ),
            )
            self._refresh_action(connection, action_id, now)
            connection.commit()

    @staticmethod
    def _refresh_action(
        connection: sqlite3.Connection,
        action_id: str,
        now: float,
    ) -> None:
        statuses = [
            row["status"]
            for row in connection.execute(
                """
                SELECT status FROM collectbox_action_platforms
                WHERE action_id = ?
                """,
                (action_id,),
            )
        ]
        if statuses and all(status == SUCCEEDED for status in statuses):
            status = SUCCEEDED
            completed_at = now
        elif RUNNING in statuses:
            status = RUNNING
            completed_at = None
        elif any(
            status in {FAILED_RETRYABLE, RECONCILIATION_REQUIRED}
            for status in statuses
        ):
            status = "PARTIAL_FAILED"
            completed_at = now
        else:
            status = "READY"
            completed_at = None
        connection.execute(
            """
            UPDATE collectbox_actions
            SET status = ?, updated_at = ?, completed_at = ?
            WHERE action_id = ?
            """,
            (status, now, completed_at, action_id),
        )

    @staticmethod
    def _require_public_identity(
        projection: Mapping[str, Any],
        identity: Mapping[str, Any],
    ) -> None:
        if projection.get("approved_plan") != _public_plan_identity(identity):
            raise ValueError("collect-box approved plan identity drifted")

    @staticmethod
    def _empty_projection(identity: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "persisted": False,
            "approved_plan": _public_plan_identity(identity),
            "action": {
                "action_id": None,
                "status": "READY",
                "start_allowed": True,
                "retry_allowed": False,
                "terminal": False,
                "error": None,
                "platforms": [
                    {
                        "platform": platform,
                        "status": PENDING,
                        "outcome": None,
                        "attempt_count": 0,
                        "retry_allowed": False,
                        "receipt_digest": None,
                        "platform_detail_id_digest": None,
                        "external_writes": {
                            "count": 0,
                            "classes": [],
                        },
                        "error": None,
                    }
                    for platform in PLATFORMS
                ],
            },
            "external_writes_performed": [],
            "external_write_count": 0,
            "canonical_next_action": {
                "action": "start_collectbox_action",
                "target_focus": None,
            },
        }

    def _project(
        self,
        connection: sqlite3.Connection,
        action: sqlite3.Row,
    ) -> dict[str, Any]:
        platforms = []
        union_writes = []
        total_count = 0
        all_counts_known = True
        retry_allowed = False
        for row in connection.execute(
            """
            SELECT * FROM collectbox_action_platforms
            WHERE action_id = ?
            ORDER BY CASE platform
                WHEN 'TIKTOK' THEN 0
                WHEN 'SHOPEE' THEN 1
                ELSE 99
            END
            """,
            (action["action_id"],),
        ):
            classes = json.loads(row["external_writes_json"] or "[]")
            for value in classes:
                if value not in union_writes:
                    union_writes.append(value)
            count = row["external_write_count"]
            if count is None:
                all_counts_known = False
            else:
                total_count += int(count)
            row_retry = bool(row["retry_allowed"])
            retry_allowed = retry_allowed or row_retry
            platforms.append(
                {
                    "platform": row["platform"],
                    "status": row["status"],
                    "outcome": row["outcome"],
                    "attempt_count": int(row["attempt_count"]),
                    "retry_allowed": row_retry,
                    "receipt_digest": row["receipt_digest"],
                    "platform_detail_id_digest": (
                        row["platform_detail_id_digest"]
                    ),
                    "external_writes": {
                        "count": count,
                        "classes": classes,
                    },
                    "error": (
                        json.loads(row["error_json"])
                        if row["error_json"]
                        else None
                    ),
                }
            )
        status = action["status"]
        start_allowed = status == "READY" or retry_allowed
        terminal = status == SUCCEEDED or (
            status == "PARTIAL_FAILED" and not retry_allowed
        )
        canonical_next_action = (
            {
                "action": "start_collectbox_action",
                "target_focus": None,
            }
            if start_allowed
            else (
                {
                    "action": "read_collectbox_status",
                    "target_focus": None,
                }
                if status == RUNNING
                else None
            )
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "persisted": True,
            "approved_plan": {
                "plan_id": action["plan_id"],
                "product_revision": int(action["product_revision"]),
                "payload_digest": action["payload_digest"],
                "targets_digest": action["targets_digest"],
            },
            "action": {
                "action_id": action["action_id"],
                "status": status,
                "start_allowed": start_allowed,
                "retry_allowed": retry_allowed,
                "terminal": terminal,
                "error": (
                    json.loads(action["action_error_json"])
                    if action["action_error_json"]
                    else None
                ),
                "platforms": platforms,
            },
            "external_writes_performed": union_writes,
            "external_write_count": (
                total_count if all_counts_known else None
            ),
            "canonical_next_action": canonical_next_action,
        }
