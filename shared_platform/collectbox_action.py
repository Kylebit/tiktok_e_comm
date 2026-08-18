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
import uuid


SCHEMA_VERSION = "collectbox-action-status/v1"
REQUEST_SCHEMA_VERSION = "collectbox-platform-request/v1"
PLATFORMS = ("TIKTOK", "SHOPEE")
PENDING = "PENDING"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED_RETRYABLE = "FAILED_RETRYABLE"
RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
REPAIRED_SUCCEEDED = "REPAIRED_SUCCEEDED"
FAILED = "FAILED"
IMPORTED = "IMPORTED"
ALREADY_PRESENT = "ALREADY_PRESENT"
MIN_PLATFORM_SPACING_SECONDS = 3.0
_WRITE_CLASS = {
    "TIKTOK": "miaoshou:collectbox:claim:tiktok",
    "SHOPEE": "miaoshou:collectbox:claim:shopee",
}
_COLLECTBOX_TARGETS = {
    "TIKTOK": frozenset(
        {
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
        }
    ),
    "SHOPEE": frozenset(
        {"shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN"}
    ),
}
_COLLECTBOX_TARGET_OPERATIONS = {
    "TIKTOK": (
        "detail:create",
        "shop:claim",
        "detail:update",
    ),
    "SHOPEE": ("detail:update",),
}
_SHA256_EMPTY_LIST = hashlib.sha256(b"[]").hexdigest()
_TARGET_TERMINAL_STATUSES = frozenset(
    {SUCCEEDED, FAILED_RETRYABLE, RECONCILIATION_REQUIRED}
)
_PUBLIC_TARGET_OUTCOME_STATUSES = frozenset(
    {SUCCEEDED, REPAIRED_SUCCEEDED, FAILED}
)


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


def _allowed_write_classes(
    platform: str,
    approved_targets: tuple[str, ...],
) -> frozenset[str]:
    platform_name = platform.lower()
    selected_targets = _COLLECTBOX_TARGETS[platform].intersection(
        approved_targets
    )
    return frozenset(
        {
            _WRITE_CLASS[platform],
            *(
                f"miaoshou:collectbox:{platform_name}:{operation}:{target}"
                for operation in _COLLECTBOX_TARGET_OPERATIONS[platform]
                for target in selected_targets
            ),
        }
    )


def _platform_target_rows(
    platform: str,
    approved_targets: tuple[str, ...] | list[str],
    *,
    status: str = PENDING,
) -> list[dict[str, str]]:
    if platform not in PLATFORMS:
        raise ValueError("collect-box platform is invalid")
    return [
        {"target_label": target, "status": status}
        for target in approved_targets
        if target in _COLLECTBOX_TARGETS[platform]
    ]


def _pending_target_receipt(
    platform: str,
    approved_targets: tuple[str, ...] | list[str],
) -> str:
    return _canonical_json(
        {
            "schema_version": "collectbox-target-selection/v1",
            "targets": _platform_target_rows(platform, approved_targets),
        }
    )


def _targets_from_receipt(
    raw_receipt: object,
    platform: str,
    *,
    status: str,
) -> list[dict[str, str]]:
    try:
        receipt = json.loads(raw_receipt) if type(raw_receipt) is str else {}
    except (TypeError, ValueError):
        receipt = {}
    rows = receipt.get("targets") if isinstance(receipt, Mapping) else None
    if not isinstance(rows, list):
        return []
    labels = []
    for row in rows:
        label = row.get("target_label") if isinstance(row, Mapping) else None
        if (
            type(label) is not str
            or label not in _COLLECTBOX_TARGETS[platform]
            or label in labels
        ):
            return []
        labels.append(label)
    return [
        {"target_label": label, "status": status}
        for label in labels
    ]


def _projected_targets(raw_receipt: object, platform: str) -> list[dict[str, str]]:
    try:
        receipt = json.loads(raw_receipt) if type(raw_receipt) is str else {}
    except (TypeError, ValueError) as error:
        raise ValueError("collect-box target receipt is malformed") from error
    rows = receipt.get("targets") if isinstance(receipt, Mapping) else None
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError("collect-box target receipt is invalid")
    projected: list[dict[str, str]] = []
    seen: set[str] = set()
    allowed_statuses = {
        PENDING,
        RUNNING,
        SUCCEEDED,
        FAILED_RETRYABLE,
        RECONCILIATION_REQUIRED,
    }
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "target_label",
            "status",
        }:
            raise ValueError("collect-box target receipt row is invalid")
        label = row.get("target_label")
        status = row.get("status")
        if (
            type(label) is not str
            or label not in _COLLECTBOX_TARGETS[platform]
            or label in seen
            or status not in allowed_statuses
        ):
            raise ValueError("collect-box target receipt identity is invalid")
        seen.add(label)
        projected.append({"target_label": label, "status": status})
    return projected


def _projected_target_outcomes(
    raw_receipt: object,
    platform: str,
    expected_targets: list[dict[str, str]],
) -> list[dict[str, str | None]]:
    try:
        receipt = json.loads(raw_receipt) if type(raw_receipt) is str else {}
    except (TypeError, ValueError) as error:
        raise ValueError(
            "collect-box target outcome receipt is malformed"
        ) from error
    rows = (
        receipt.get("target_outcomes")
        if isinstance(receipt, Mapping)
        else None
    )
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError("collect-box target outcomes are invalid")
    projected: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "target_label",
            "status",
            "error_code",
            "detail_digest",
        }:
            raise ValueError("collect-box target outcome row is invalid")
        outcome = CollectBoxTargetOutcome(
            target_label=row.get("target_label"),
            status=row.get("status"),
            error_code=row.get("error_code"),
            detail_digest=row.get("detail_digest"),
        )
        if (
            outcome.target_label not in _COLLECTBOX_TARGETS[platform]
            or outcome.target_label in seen
        ):
            raise ValueError("collect-box target outcome identity is invalid")
        seen.add(outcome.target_label)
        projected.append(outcome.public_payload())
    if projected and [row["target_label"] for row in projected] != [
        row["target_label"] for row in expected_targets
    ]:
        raise ValueError("collect-box target outcome membership drifted")
    return projected


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
    projection = CollectBoxActionStore._empty_projection(
        identity,
        tuple(plan["targets"]),
    )
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
        approved_plan_identity(plan),
        tuple(plan["targets"]),
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
                    "targets": _platform_target_rows(
                        platform,
                        targets if isinstance(targets, list) else [],
                    ),
                    "target_outcomes": [],
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
class CollectBoxTargetOutcome:
    target_label: str
    status: str
    error_code: str | None = None
    detail_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self.target_label) is not str or not self.target_label.strip():
            raise ValueError("target outcome label is invalid")
        if self.target_label != self.target_label.strip():
            raise ValueError("target outcome label must be canonical")
        if self.status not in _PUBLIC_TARGET_OUTCOME_STATUSES:
            raise ValueError("target outcome status is invalid")
        if self.status == FAILED:
            if (
                type(self.error_code) is not str
                or not self.error_code
                or len(self.error_code) > 128
                or not self.error_code.isascii()
                or any(
                    char not in "abcdefghijklmnopqrstuvwxyz0123456789_"
                    for char in self.error_code
                )
                or not _is_sha256(self.detail_digest)
            ):
                raise ValueError("failed target outcome evidence is invalid")
        elif self.error_code is not None or self.detail_digest is not None:
            raise ValueError("successful target outcome error must be null")

    def public_payload(self) -> dict[str, str | None]:
        return {
            "target_label": self.target_label,
            "status": self.status,
            "error_code": self.error_code,
            "detail_digest": self.detail_digest,
        }


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
    target_statuses: tuple[tuple[str, str], ...] = ()
    target_outcomes: tuple[CollectBoxTargetOutcome, ...] = ()
    target_detail_identities: tuple["CollectBoxTargetDetailIdentity", ...] = ()

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
        if (
            type(self.target_statuses) is not tuple
            or any(
                type(value) is not tuple
                or len(value) != 2
                or type(value[0]) is not str
                or not value[0]
                or value[1] not in _TARGET_TERMINAL_STATUSES
                for value in self.target_statuses
            )
            or len({value[0] for value in self.target_statuses})
            != len(self.target_statuses)
        ):
            raise ValueError("target_statuses are invalid")
        if (
            type(self.target_outcomes) is not tuple
            or any(
                type(value) is not CollectBoxTargetOutcome
                for value in self.target_outcomes
            )
            or len({value.target_label for value in self.target_outcomes})
            != len(self.target_outcomes)
        ):
            raise ValueError("target_outcomes are invalid")
        if self.target_statuses and self.target_outcomes:
            raise ValueError(
                "legacy target_statuses and target_outcomes are exclusive"
            )
        if (
            type(self.target_detail_identities) is not tuple
            or any(
                type(value) is not CollectBoxTargetDetailIdentity
                for value in self.target_detail_identities
            )
            or len(
                {value.target_label for value in self.target_detail_identities}
            )
            != len(self.target_detail_identities)
        ):
            raise ValueError("target detail identities are invalid")
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
            if self.target_statuses and any(
                status != SUCCEEDED
                for _target, status in self.target_statuses
            ):
                raise ValueError("platform success requires target success")
            if self.target_outcomes and any(
                outcome.status == FAILED for outcome in self.target_outcomes
            ):
                raise ValueError("platform success cannot contain target failure")
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
            if self.target_outcomes and not any(
                outcome.status == FAILED for outcome in self.target_outcomes
            ):
                raise ValueError(
                    "platform failure requires an exact failed target outcome"
                )


@dataclass(frozen=True, repr=False)
class CollectBoxTargetDetailIdentity:
    """Server-internal draft identity; never included in public projections."""

    target_label: str
    detail_id: str
    shop_id: str

    def __post_init__(self) -> None:
        if (
            type(self.target_label) is not str
            or not self.target_label
            or self.target_label != self.target_label.strip()
        ):
            raise ValueError("target detail label is invalid")
        object.__setattr__(
            self,
            "detail_id",
            _canonical_positive_identifier(self.detail_id, "detail_id"),
        )
        object.__setattr__(
            self,
            "shop_id",
            _canonical_positive_identifier(self.shop_id, "shop_id"),
        )

    def internal_payload(self) -> dict[str, str]:
        identity = {
            "schema_version": "collectbox-target-detail-identity/v1",
            "target_label": self.target_label,
            "detail_id": self.detail_id,
            "shop_id": self.shop_id,
        }
        return {**identity, "identity_digest": _digest(identity)}


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
CREATE TABLE IF NOT EXISTS collectbox_action_batches (
    action_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    batch_sequence INTEGER NOT NULL,
    reimport_request_id TEXT NOT NULL,
    offer_id TEXT NOT NULL,
    product_revision INTEGER NOT NULL,
    payload_digest TEXT NOT NULL,
    targets_digest TEXT NOT NULL,
    common_identity_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    action_error_json TEXT,
    last_invoked_at REAL,
    execution_claimed_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    completed_at REAL,
    UNIQUE (plan_id, batch_sequence),
    UNIQUE (plan_id, reimport_request_id)
);
CREATE TABLE IF NOT EXISTS collectbox_action_batch_platforms (
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
    FOREIGN KEY (action_id) REFERENCES collectbox_action_batches(action_id)
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
    def _batch_tables_exist(connection: sqlite3.Connection) -> bool:
        names = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name IN (
                    'collectbox_action_batches',
                    'collectbox_action_batch_platforms'
                )
                """
            )
        }
        return names == {
            "collectbox_action_batches",
            "collectbox_action_batch_platforms",
        }

    @staticmethod
    def _action_id(plan_id: str) -> str:
        return f"collectbox-action:{hashlib.sha256(plan_id.encode()).hexdigest()[:24]}"

    @staticmethod
    def _batch_action_id(
        plan_id: str,
        batch_sequence: int,
        request_id: str,
    ) -> str:
        identity = _canonical_json(
            {
                "plan_id": plan_id,
                "batch_sequence": batch_sequence,
                "reimport_request_id": request_id,
            }
        )
        return (
            "collectbox-action-batch:"
            f"{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
        )

    @staticmethod
    def _validated_restart_request_id(value: object) -> str:
        if type(value) is not str or not value.strip():
            raise ValueError("reimport_request_id is required")
        clean = value.strip().lower()
        try:
            parsed = uuid.UUID(clean)
        except (AttributeError, ValueError) as error:
            raise ValueError("reimport_request_id is invalid") from error
        if str(parsed) != clean or parsed.version != 4:
            raise ValueError("reimport_request_id must be a canonical UUID v4")
        return clean

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
        return self._empty_projection(identity, tuple(plan["targets"]))

    def status(self, *, plan_id: str) -> dict[str, Any] | None:
        clean_plan_id = _nonempty_text(plan_id, "plan_id")
        if not self.path.is_file():
            return None
        with self._connect() as connection:
            if not self._tables_exist(connection):
                return None
            if self._batch_tables_exist(connection):
                batch = connection.execute(
                    """
                    SELECT * FROM collectbox_action_batches
                    WHERE plan_id = ?
                    ORDER BY batch_sequence DESC
                    LIMIT 1
                    """,
                    (clean_plan_id,),
                ).fetchone()
                if batch is not None:
                    return self._project(connection, batch, batched=True)
            row = connection.execute(
                "SELECT * FROM collectbox_actions WHERE plan_id = ?",
                (clean_plan_id,),
            ).fetchone()
            if row is None:
                return None
            return self._project(connection, row, batched=False)

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
            sources = [("collectbox_action_platforms", False)]
            if self._batch_tables_exist(connection):
                sources.append(("collectbox_action_batch_platforms", True))
            recovered = 0
            recovered_batch_ids: set[str] = set()
            for platform_table, batched in sources:
                rows = list(
                    connection.execute(
                        f"""
                        SELECT action_id, platform, receipt_json
                        FROM {platform_table}
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
                        (
                            "process stopped while the collect-box "
                            "invocation was in flight"
                        ),
                    )
                    receipt = {
                        "schema_version": "collectbox-platform-receipt/v1",
                        "status": RECONCILIATION_REQUIRED,
                        "outcome": None,
                        "targets": _targets_from_receipt(
                            row["receipt_json"],
                            platform,
                            status=RECONCILIATION_REQUIRED,
                        ),
                        "target_outcomes": [],
                        "platform_detail_id_digest": None,
                        "external_writes": [_WRITE_CLASS[platform]],
                        "external_write_count": None,
                        "evidence_digest": _digest({}),
                        "error": error,
                    }
                    connection.execute(
                        f"""
                        UPDATE {platform_table}
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
                        batched=batched,
                        finalize_pending=True,
                    )
                    if batched:
                        recovered_batch_ids.add(row["action_id"])
                    recovered += 1
            if self._batch_tables_exist(connection):
                orphan_batches = list(
                    connection.execute(
                        """
                        SELECT batch.action_id
                        FROM collectbox_action_batches AS batch
                        WHERE batch.execution_claimed_at IS NOT NULL
                          AND EXISTS (
                              SELECT 1
                              FROM collectbox_action_batch_platforms AS item
                              WHERE item.action_id = batch.action_id
                                AND item.status = ?
                          )
                          AND NOT EXISTS (
                              SELECT 1
                              FROM collectbox_action_batch_platforms AS item
                              WHERE item.action_id = batch.action_id
                                AND item.status = ?
                          )
                        ORDER BY batch.action_id
                        """,
                        (PENDING, RUNNING),
                    )
                )
                for batch in orphan_batches:
                    action_id = batch["action_id"]
                    error = _redacted_error(
                        "UNKNOWN",
                        "collectbox_interrupted_before_dispatch",
                        (
                            "process stopped before the pending platform "
                            "invocation began"
                        ),
                    )
                    for row in connection.execute(
                        """
                        SELECT platform, receipt_json
                        FROM collectbox_action_batch_platforms
                        WHERE action_id = ? AND status = ?
                        ORDER BY platform
                        """,
                        (action_id, PENDING),
                    ):
                        receipt = {
                            "schema_version": (
                                "collectbox-platform-receipt/v1"
                            ),
                            "status": FAILED_RETRYABLE,
                            "outcome": None,
                            "targets": _targets_from_receipt(
                                row["receipt_json"],
                                row["platform"],
                                status=FAILED_RETRYABLE,
                            ),
                            "target_outcomes": [],
                            "platform_detail_id_digest": None,
                            "external_writes": [],
                            "external_write_count": 0,
                            "evidence_digest": _digest({}),
                            "error": error,
                        }
                        connection.execute(
                            """
                            UPDATE collectbox_action_batch_platforms
                            SET status = ?, retry_allowed = 1,
                                outcome = NULL,
                                platform_detail_id = NULL,
                                platform_detail_id_digest = NULL,
                                external_writes_json = '[]',
                                external_write_count = 0,
                                receipt_json = ?, receipt_digest = ?,
                                error_json = ?, updated_at = ?
                            WHERE action_id = ? AND platform = ?
                              AND status = ?
                            """,
                            (
                                FAILED_RETRYABLE,
                                _canonical_json(receipt),
                                _digest(receipt),
                                _canonical_json(error),
                                recovered_at,
                                action_id,
                                row["platform"],
                                PENDING,
                            ),
                        )
                    connection.execute(
                        """
                        UPDATE collectbox_action_batches
                        SET execution_claimed_at = NULL, updated_at = ?
                        WHERE action_id = ?
                        """,
                        (recovered_at, action_id),
                    )
                    self._refresh_action(
                        connection,
                        action_id,
                        recovered_at,
                        batched=True,
                        finalize_pending=True,
                    )
                    if action_id not in recovered_batch_ids:
                        recovered += 1
            connection.commit()
            return recovered

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
        restart_existing: bool = False,
        restart_request_id: object = None,
        platform_scope: str | None = None,
    ) -> dict[str, Any]:
        if type(restart_existing) is not bool:
            raise ValueError("restart_existing must be a literal boolean")
        if not restart_existing and restart_request_id is not None:
            raise ValueError(
                "reimport_request_id requires restart_existing=true"
            )
        if platform_scope is not None and platform_scope not in PLATFORMS:
            raise ValueError("collect-box platform scope is invalid")
        identity = approved_plan_identity(plan)
        self._ensure_schema()
        clean_common_id = str(common_collect_box_detail_id).strip()
        common_digest = common_collectbox_identity_digest(
            identity["plan_id"],
            common_collect_box_detail_id,
        )
        approved_targets = tuple(plan["targets"])
        self._ensure_action(
            identity,
            common_digest,
            now(),
            approved_targets,
            platform_scope=platform_scope,
        )
        batched = False
        action_id = self._action_id(identity["plan_id"])
        if restart_existing:
            action_id, _created = self._ensure_restart_batch(
                identity,
                common_digest,
                self._validated_restart_request_id(restart_request_id),
                float(now()),
                approved_targets,
                platform_scope,
            )
            batched = True
            if not self._claim_restart_batch(action_id, float(now())):
                return self._wait_for_restart_batch(action_id)
        if batched:
            with self._connect() as connection:
                batch = connection.execute(
                    """
                    SELECT * FROM collectbox_action_batches
                    WHERE action_id = ?
                    """,
                    (action_id,),
                ).fetchone()
                assert batch is not None
                current = self._project(connection, batch, batched=True)
        else:
            current = self.status(plan_id=identity["plan_id"])
        assert current is not None
        if current["action"]["terminal"] is True:
            return current
        candidates = [
            row["platform"]
            for row in current["action"]["platforms"]
            if row["status"] in {PENDING, FAILED_RETRYABLE}
            and (platform_scope is None or row["platform"] == platform_scope)
        ]
        for platform in candidates:
            action_table = (
                "collectbox_action_batches"
                if batched
                else "collectbox_actions"
            )
            with self._connect() as connection:
                action = connection.execute(
                    f"SELECT * FROM {action_table} WHERE action_id = ?",
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
                batched=batched,
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
                    tuple(plan["targets"]),
                    batched=batched,
                )
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
                    tuple(plan["targets"]),
                    batched=batched,
                )
        if batched:
            with self._connect() as connection:
                batch = connection.execute(
                    """
                    SELECT * FROM collectbox_action_batches
                    WHERE action_id = ?
                    """,
                    (action_id,),
                ).fetchone()
                assert batch is not None
                return self._project(connection, batch, batched=True)
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

    def internal_tiktok_publish_contexts(
        self,
        *,
        plan_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Return exact internal TikTok draft identities for one latest batch.

        Raw Miaoshou identifiers are intentionally available only through this
        server-internal accessor.  The public projection exposes receipt and
        identity digests instead.
        """

        clean_plan_id = _nonempty_text(plan_id, "plan_id")
        if not self.path.is_file():
            return {}
        with self._connect() as connection:
            if not self._tables_exist(connection):
                return {}
            action = None
            platform_table = "collectbox_action_platforms"
            if self._batch_tables_exist(connection):
                action = connection.execute(
                    """
                    SELECT * FROM collectbox_action_batches
                    WHERE plan_id = ?
                    ORDER BY batch_sequence DESC
                    LIMIT 1
                    """,
                    (clean_plan_id,),
                ).fetchone()
                if action is not None:
                    platform_table = "collectbox_action_batch_platforms"
            if action is None:
                action = connection.execute(
                    "SELECT * FROM collectbox_actions WHERE plan_id = ?",
                    (clean_plan_id,),
                ).fetchone()
            if action is None:
                return {}
            row = connection.execute(
                f"""
                SELECT receipt_json, receipt_digest
                FROM {platform_table}
                WHERE action_id = ? AND platform = 'TIKTOK'
                """,
                (action["action_id"],),
            ).fetchone()
            if row is None or not row["receipt_json"]:
                return {}
            receipt = json.loads(row["receipt_json"])
            if (
                not isinstance(receipt, dict)
                or receipt.get("schema_version")
                != "collectbox-platform-receipt/v1"
                or _digest(receipt) != row["receipt_digest"]
            ):
                raise ValueError("collect-box TikTok receipt identity drifted")
            raw_identities = receipt.get("internal_target_details")
            if not isinstance(raw_identities, list):
                return {}
            contexts: dict[str, dict[str, Any]] = {}
            for raw in raw_identities:
                if not isinstance(raw, dict) or set(raw) != {
                    "schema_version",
                    "target_label",
                    "detail_id",
                    "shop_id",
                    "identity_digest",
                }:
                    raise ValueError("collect-box target detail proof is invalid")
                identity = CollectBoxTargetDetailIdentity(
                    target_label=raw["target_label"],
                    detail_id=raw["detail_id"],
                    shop_id=raw["shop_id"],
                ).internal_payload()
                if identity != raw or raw["target_label"] in contexts:
                    raise ValueError("collect-box target detail proof drifted")
                contexts[raw["target_label"]] = {
                    "schema_version": "collectbox-tiktok-publish-context/v1",
                    "plan_id": action["plan_id"],
                    "offer_id": action["offer_id"],
                    "product_revision": int(action["product_revision"]),
                    "payload_digest": action["payload_digest"],
                    "targets_digest": action["targets_digest"],
                    "action_id": action["action_id"],
                    "platform": "TIKTOK",
                    "common_identity_digest": action[
                        "common_identity_digest"
                    ],
                    "receipt_digest": row["receipt_digest"],
                    "target_detail_identity": identity,
                }
                binding = dict(contexts[raw["target_label"]])
                contexts[raw["target_label"]][
                    "publish_identity_digest"
                ] = _digest(binding)
            return contexts

    def _ensure_action(
        self,
        identity: Mapping[str, Any],
        common_digest: str,
        now: float,
        approved_targets: tuple[str, ...],
        *,
        platform_scope: str | None = None,
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
                action_platforms = (
                    (platform_scope,)
                    if platform_scope is not None
                    else PLATFORMS
                )
                connection.executemany(
                    """
                    INSERT INTO collectbox_action_platforms (
                        action_id, platform, status, receipt_json, updated_at
                    ) VALUES (?, ?, 'PENDING', ?, ?)
                    """,
                    [
                        (
                            action_id,
                            platform,
                            _pending_target_receipt(
                                platform,
                                approved_targets,
                            ),
                            now,
                        )
                        for platform in action_platforms
                    ],
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
                if platform_scope is not None:
                    # A scoped platform action is independent.  Rows for an
                    # unselected platform that have never left PENDING carry
                    # no provider fact and must not keep the selected action
                    # RUNNING forever.  Never remove attempted/terminal rows.
                    connection.execute(
                        """
                        DELETE FROM collectbox_action_platforms
                        WHERE action_id = ? AND platform != ?
                          AND status = 'PENDING'
                        """,
                        (action_id, platform_scope),
                    )
                    self._refresh_action(
                        connection,
                        action_id,
                        now,
                        batched=False,
                        finalize_pending=False,
                    )
            connection.commit()

    def _ensure_restart_batch(
        self,
        identity: Mapping[str, Any],
        common_digest: str,
        request_id: str,
        now: float,
        approved_targets: tuple[str, ...],
        platform_scope: str | None = None,
    ) -> tuple[str, bool]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM collectbox_action_batches
                WHERE plan_id = ? AND reimport_request_id = ?
                """,
                (identity["plan_id"], request_id),
            ).fetchone()
            if existing is not None:
                durable = {
                    "plan_id": existing["plan_id"],
                    "offer_id": existing["offer_id"],
                    "product_revision": existing["product_revision"],
                    "payload_digest": existing["payload_digest"],
                    "targets_digest": existing["targets_digest"],
                }
                if durable != dict(identity):
                    raise ValueError(
                        "collect-box reimport identity drifted"
                    )
                if existing["common_identity_digest"] != common_digest:
                    raise ValueError(
                        "common collect-box reimport identity drifted"
                    )
                connection.commit()
                return existing["action_id"], False

            latest_batch = connection.execute(
                """
                SELECT action_id, status, batch_sequence
                FROM collectbox_action_batches
                WHERE plan_id = ?
                ORDER BY batch_sequence DESC
                LIMIT 1
                """,
                (identity["plan_id"],),
            ).fetchone()
            if latest_batch is not None:
                prior_status = latest_batch["status"]
                batch_sequence = int(latest_batch["batch_sequence"]) + 1
            else:
                legacy = connection.execute(
                    """
                    SELECT status FROM collectbox_actions
                    WHERE plan_id = ?
                    """,
                    (identity["plan_id"],),
                ).fetchone()
                prior_status = legacy["status"] if legacy else None
                batch_sequence = 2
            if prior_status not in {SUCCEEDED, "PARTIAL_FAILED"}:
                raise ValueError(
                    "collect-box action must finish before a new import"
                )

            action_id = self._batch_action_id(
                identity["plan_id"],
                batch_sequence,
                request_id,
            )
            connection.execute(
                """
                INSERT INTO collectbox_action_batches (
                    action_id, plan_id, batch_sequence,
                    reimport_request_id, offer_id, product_revision,
                    payload_digest, targets_digest,
                    common_identity_digest, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'READY', ?, ?)
                """,
                (
                    action_id,
                    identity["plan_id"],
                    batch_sequence,
                    request_id,
                    identity["offer_id"],
                    identity["product_revision"],
                    identity["payload_digest"],
                    identity["targets_digest"],
                    common_digest,
                    now,
                    now,
                ),
            )
            batch_platforms = (
                (platform_scope,)
                if platform_scope is not None
                else PLATFORMS
            )
            connection.executemany(
                """
                INSERT INTO collectbox_action_batch_platforms (
                    action_id, platform, status, receipt_json, updated_at
                ) VALUES (?, ?, 'PENDING', ?, ?)
                """,
                [
                    (
                        action_id,
                        platform,
                        _pending_target_receipt(
                            platform,
                            approved_targets,
                        ),
                        now,
                    )
                    for platform in batch_platforms
                ],
            )
            connection.commit()
            return action_id, True

    def _claim_restart_batch(self, action_id: str, now: float) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            claimed = connection.execute(
                """
                UPDATE collectbox_action_batches
                SET execution_claimed_at = ?, updated_at = ?
                WHERE action_id = ?
                  AND status = 'READY'
                  AND execution_claimed_at IS NULL
                """,
                (now, now, action_id),
            ).rowcount
            connection.commit()
            return claimed == 1

    def _wait_for_restart_batch(
        self,
        action_id: str,
        *,
        timeout_seconds: float = 300.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            with self._connect() as connection:
                batch = connection.execute(
                    """
                    SELECT * FROM collectbox_action_batches
                    WHERE action_id = ?
                    """,
                    (action_id,),
                ).fetchone()
                if batch is None:
                    raise ValueError("collect-box reimport batch is missing")
                projection = self._project(
                    connection,
                    batch,
                    batched=True,
                )
            unfinished = any(
                row["status"] in {PENDING, RUNNING}
                for row in projection["action"]["platforms"]
            )
            if not unfinished:
                return projection
            if time.monotonic() >= deadline:
                return projection
            time.sleep(0.02)

    def _mark_running(
        self,
        action_id: str,
        platform: str,
        invoked_at: float,
        *,
        batched: bool = False,
    ) -> int:
        action_table = (
            "collectbox_action_batches" if batched else "collectbox_actions"
        )
        platform_table = (
            "collectbox_action_batch_platforms"
            if batched
            else "collectbox_action_platforms"
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, attempt_count
                FROM {platform_table}
                WHERE action_id = ? AND platform = ?
                """.format(platform_table=platform_table),
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
                UPDATE {platform_table}
                SET status = ?, attempt_count = ?, retry_allowed = 0,
                    outcome = NULL, platform_detail_id = NULL,
                    platform_detail_id_digest = NULL,
                    external_writes_json = '[]',
                    external_write_count = 0,
                    receipt_digest = NULL,
                    error_json = NULL, last_invoked_at = ?, updated_at = ?
                WHERE action_id = ? AND platform = ?
                """.format(platform_table=platform_table),
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
                UPDATE {action_table}
                SET status = ?, last_invoked_at = ?, updated_at = ?,
                    completed_at = NULL
                WHERE action_id = ?
                """.format(action_table=action_table),
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
        approved_targets: tuple[str, ...],
        *,
        batched: bool = False,
    ) -> None:
        platform_table = (
            "collectbox_action_batch_platforms"
            if batched
            else "collectbox_action_platforms"
        )
        allowed_write_classes = _allowed_write_classes(
            platform,
            approved_targets,
        )
        if any(
            write not in allowed_write_classes
            for write in result.external_writes
        ):
            raise ValueError("collect-box write class is invalid")
        evidence = _assert_redacted_evidence(result.receipt_evidence)
        selected_targets = tuple(
            target
            for target in approved_targets
            if target in _COLLECTBOX_TARGETS[platform]
        )
        if result.target_statuses:
            if tuple(
                target for target, _status in result.target_statuses
            ) != selected_targets:
                raise ValueError("collect-box target result identity drifted")
            target_rows = [
                {"target_label": target, "status": status}
                for target, status in result.target_statuses
            ]
        else:
            target_rows = _platform_target_rows(
                platform,
                approved_targets,
                status=result.status,
            )
        if result.target_outcomes:
            if tuple(
                outcome.target_label for outcome in result.target_outcomes
            ) != selected_targets:
                raise ValueError("collect-box target outcome identity drifted")
            target_outcome_rows = [
                outcome.public_payload()
                for outcome in result.target_outcomes
            ]
        else:
            target_outcome_rows = []
        internal_target_details = [
            identity.internal_payload()
            for identity in result.target_detail_identities
        ]
        internal_labels = tuple(
            row["target_label"] for row in internal_target_details
        )
        if any(label not in selected_targets for label in internal_labels) or (
            internal_labels
            != tuple(
                label
                for label in selected_targets
                if label in internal_labels
            )
        ):
            raise ValueError("collect-box target detail identity drifted")
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
            "targets": target_rows,
            "target_outcomes": target_outcome_rows,
            "internal_target_details": internal_target_details,
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
                UPDATE {platform_table}
                SET status = ?, retry_allowed = ?, outcome = ?,
                    platform_detail_id = ?,
                    platform_detail_id_digest = ?,
                    external_writes_json = ?,
                    external_write_count = ?,
                    receipt_json = ?, receipt_digest = ?,
                    error_json = ?, updated_at = ?
                WHERE action_id = ? AND platform = ?
                """.format(platform_table=platform_table),
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
            self._refresh_action(
                connection,
                action_id,
                now,
                batched=batched,
            )
            connection.commit()

    @staticmethod
    def _refresh_action(
        connection: sqlite3.Connection,
        action_id: str,
        now: float,
        *,
        batched: bool = False,
        finalize_pending: bool = False,
    ) -> None:
        action_table = (
            "collectbox_action_batches" if batched else "collectbox_actions"
        )
        platform_table = (
            "collectbox_action_batch_platforms"
            if batched
            else "collectbox_action_platforms"
        )
        statuses = [
            row["status"]
            for row in connection.execute(
                """
                SELECT status FROM {platform_table}
                WHERE action_id = ?
                """.format(platform_table=platform_table),
                (action_id,),
            )
        ]
        if statuses and all(status == SUCCEEDED for status in statuses):
            status = SUCCEEDED
            completed_at = now
        elif RUNNING in statuses or (
            not finalize_pending
            and PENDING in statuses
            and any(status != PENDING for status in statuses)
        ):
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
            UPDATE {action_table}
            SET status = ?, updated_at = ?, completed_at = ?
            WHERE action_id = ?
            """.format(action_table=action_table),
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
    def _empty_projection(
        identity: Mapping[str, Any],
        approved_targets: tuple[str, ...],
    ) -> dict[str, Any]:
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
                        "targets": _platform_target_rows(
                            platform,
                            approved_targets,
                        ),
                        "target_outcomes": [],
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
        *,
        batched: bool = False,
    ) -> dict[str, Any]:
        platform_table = (
            "collectbox_action_batch_platforms"
            if batched
            else "collectbox_action_platforms"
        )
        platforms = []
        union_writes = []
        total_count = 0
        all_counts_known = True
        retry_allowed = False
        for row in connection.execute(
            """
            SELECT * FROM {platform_table}
            WHERE action_id = ?
            ORDER BY CASE platform
                WHEN 'TIKTOK' THEN 0
                WHEN 'SHOPEE' THEN 1
                ELSE 99
            END
            """.format(platform_table=platform_table),
            (action["action_id"],),
        ):
            classes = json.loads(row["external_writes_json"] or "[]")
            targets = _projected_targets(
                row["receipt_json"],
                row["platform"],
            )
            target_outcomes = _projected_target_outcomes(
                row["receipt_json"],
                row["platform"],
                targets,
            )
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
                    "targets": targets,
                    "target_outcomes": target_outcomes,
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
        terminal = status in {SUCCEEDED, "PARTIAL_FAILED"}
        start_allowed = status == "READY" or terminal
        retry_allowed = False
        canonical_next_action = (
            {
                "action": (
                    "restart_collectbox_action"
                    if terminal
                    else "start_collectbox_action"
                ),
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
