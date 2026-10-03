"""Durable authority and mutation ledger for TikTok existing-detail recovery.

This boundary is intentionally separate from the ordinary publication retry
path.  It can only reuse provider objects whose exact detail/shop identity was
frozen in a recovery contract.  It never creates or claims a provider object.
Reservations are committed before a caller performs a provider mutation and
are never refunded, including after timeout or an unknown outcome.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence


CONTRACT_SCHEMA = "tiktok-existing-detail-route-recovery-runtime-contract/v1"
CLAIM_SCHEMA = "tiktok-existing-detail-route-recovery-claim/v1"
LEDGER_SCHEMA = "tiktok-existing-detail-route-recovery-ledger/v1"
ALLOWED_OPERATIONS = ("SAVE_DRAFT", "PUBLISH_TARGET")
FORBIDDEN_OPERATIONS = frozenset({"CREATE_DRAFT", "CLAIM_TO_SHOP"})
OUTCOMES = frozenset({"CONFIRMED", "FAILED_ZERO_WRITE", "UNKNOWN"})
_HEX = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")


class TikTokRecoveryContractError(ValueError):
    """The proposed authority differs from the immutable recovery facts."""


class TikTokRecoveryStateError(RuntimeError):
    """The requested reservation or result transition is unsafe."""


class TikTokRecoveryOutcomeUnknown(RuntimeError):
    """Transport could have mutated the provider; only readback may continue."""


class ExistingDetailRecoveryTransport(Protocol):
    """Narrow provider seam; deliberately has no create or claim operation."""

    def verify_existing_detail(self, target: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def save_existing_detail(self, target: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def publish_existing_detail(self, target: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _text(value: object, field: str, *, digits: bool = False) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise TikTokRecoveryContractError(f"{field} is invalid")
    if digits and (not value.isascii() or not value.isdigit() or int(value) <= 0):
        raise TikTokRecoveryContractError(f"{field} must be positive digits")
    return value


def _sha(value: object, field: str) -> str:
    text = _text(value, field)
    if not _HEX.fullmatch(text):
        raise TikTokRecoveryContractError(f"{field} must be a sha256 digest")
    return "sha256:" + text.removeprefix("sha256:")


def _ordered_labels(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TikTokRecoveryContractError("ordered_target_labels must be a sequence")
    labels = tuple(value)
    if (
        not labels
        or len(labels) != len(set(labels))
        or any(type(label) is not str or not label.startswith("tiktok:") for label in labels)
    ):
        raise TikTokRecoveryContractError("ordered_target_labels are invalid")
    return labels


def _validate_contract(contract: Mapping[str, Any]) -> tuple[tuple[str, ...], list[dict[str, Any]]]:
    if contract.get("schema_version") != CONTRACT_SCHEMA:
        raise TikTokRecoveryContractError("unsupported recovery contract schema")
    claimed_digest = _sha(contract.get("contract_digest"), "contract_digest")
    unsigned = {key: value for key, value in contract.items() if key != "contract_digest"}
    if _digest(unsigned) != claimed_digest:
        raise TikTokRecoveryContractError("recovery contract digest does not match")
    if contract.get("shared_mutation_budget") != 0:
        raise TikTokRecoveryContractError("shared mutation budget must be zero")
    if tuple(contract.get("forbidden_operations") or ()) != (
        "CREATE_DRAFT",
        "CLAIM_TO_SHOP",
    ):
        raise TikTokRecoveryContractError("forbidden operation set is incomplete")

    labels = _ordered_labels(contract.get("ordered_target_labels"))
    raw_targets = contract.get("targets")
    if not isinstance(raw_targets, list) or len(raw_targets) != len(labels):
        raise TikTokRecoveryContractError("target rows do not cover ordered target scope")
    targets: list[dict[str, Any]] = []
    for index, (label, raw) in enumerate(zip(labels, raw_targets)):
        if not isinstance(raw, Mapping) or raw.get("target_label") != label:
            raise TikTokRecoveryContractError(f"target row {index} is out of order")
        if raw.get("fresh_mutation_budget") != 2:
            raise TikTokRecoveryContractError(f"{label} budget must be exactly two")
        if tuple(raw.get("ordered_operations") or ()) != ALLOWED_OPERATIONS:
            raise TikTokRecoveryContractError(f"{label} operations are not save then publish")
        targets.append(
            {
                "target_label": label,
                "detail_id": _text(raw.get("detail_id"), "detail_id", digits=True),
                "shop_id": _text(raw.get("shop_id"), "shop_id", digits=True),
                "detail_identity_digest": _sha(
                    raw.get("detail_identity_digest"), "detail_identity_digest"
                ).removeprefix("sha256:"),
                "new_route_digest": _sha(raw.get("new_route_digest"), "new_route_digest"),
                "new_position_7_url_sha256": _sha(
                    raw.get("new_position_7_url_sha256"), "new_position_7_url_sha256"
                ).removeprefix("sha256:"),
                "fresh_mutation_budget": 2,
                "ordered_operations": list(ALLOWED_OPERATIONS),
            }
        )
    excluded = tuple(contract.get("excluded_targets") or ())
    if set(excluded).intersection(labels):
        raise TikTokRecoveryContractError("excluded target appears in recovery scope")
    return labels, targets


def build_recovery_claim(
    *,
    contract: Mapping[str, Any],
    candidate: Mapping[str, Any],
    approval: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind a fresh approval to the contract without registering or executing it."""

    labels, contract_targets = _validate_contract(contract)
    if candidate.get("schema_version") != "tiktok-target-scoped-recovery-final-candidate/v1":
        raise TikTokRecoveryContractError("candidate schema is invalid")
    candidate_digest = _sha(candidate.get("candidate_digest"), "candidate_digest")
    unsigned_candidate = {
        key: value for key, value in candidate.items() if key != "candidate_digest"
    }
    if _digest(unsigned_candidate) != candidate_digest:
        raise TikTokRecoveryContractError("candidate digest does not match")
    scope = candidate.get("scope")
    if not isinstance(scope, Mapping) or tuple(scope.get("ordered_target_labels") or ()) != labels:
        raise TikTokRecoveryContractError("candidate target order conflicts with contract")
    candidate_targets = candidate.get("targets")
    if not isinstance(candidate_targets, list) or len(candidate_targets) != len(labels):
        raise TikTokRecoveryContractError("candidate target rows are incomplete")
    for frozen, proposed in zip(contract_targets, candidate_targets):
        if not isinstance(proposed, Mapping) or proposed.get("target_label") != frozen["target_label"]:
            raise TikTokRecoveryContractError("candidate target order drifted")
        existing = proposed.get("existing_detail")
        position = proposed.get("position_7")
        budget = proposed.get("mutation_budget")
        if not isinstance(existing, Mapping) or not isinstance(position, Mapping) or not isinstance(budget, Mapping):
            raise TikTokRecoveryContractError("candidate target recovery facts are incomplete")
        observed = {
            "detail_id": str(existing.get("detail_id") or ""),
            "shop_id": str(existing.get("shop_id") or ""),
            "detail_identity_digest": str(existing.get("identity_digest") or ""),
            "new_route_digest": str(proposed.get("new_route_digest") or ""),
            "new_position_7_url_sha256": str(position.get("new_url_sha256") or ""),
        }
        expected = {key: frozen[key] for key in observed}
        if observed != expected:
            raise TikTokRecoveryContractError(
                f"{frozen['target_label']} detail or route identity drifted"
            )
        new_url = _text(position.get("new_url"), "new position 7 URL")
        if not new_url.startswith("https://"):
            raise TikTokRecoveryContractError(
                f"{frozen['target_label']} position 7 URL must be HTTPS"
            )
        if hashlib.sha256(new_url.encode("utf-8")).hexdigest() != frozen["new_position_7_url_sha256"]:
            raise TikTokRecoveryContractError(
                f"{frozen['target_label']} position 7 URL digest drifted"
            )
        frozen["new_position_7_url"] = new_url
        if budget.get("maximum") != 2 or tuple(budget.get("ordered_operations") or ()) != ALLOWED_OPERATIONS:
            raise TikTokRecoveryContractError(f"{frozen['target_label']} budget drifted")
        if set(budget.get("forbidden_operations") or ()) != FORBIDDEN_OPERATIONS:
            raise TikTokRecoveryContractError(f"{frozen['target_label']} forbidden operations drifted")

    snapshots = candidate.get("recovery_snapshots")
    source = candidate.get("source_authority")
    if not isinstance(snapshots, Mapping) or not isinstance(source, Mapping):
        raise TikTokRecoveryContractError("candidate authority facts are incomplete")
    source_run_id = _text(source.get("source_run_id"), "source_run_id")
    if source_run_id != contract.get("source_run_id"):
        raise TikTokRecoveryContractError("candidate source run conflicts with contract")
    business_digest = _sha(snapshots.get("business_snapshot_digest"), "business_snapshot_digest")
    execution_digest = _sha(snapshots.get("execution_snapshot_digest"), "execution_snapshot_digest")

    if approval.get("schema_version") != "tiktok-target-scoped-recovery-final-approval/v1":
        raise TikTokRecoveryContractError("approval schema is invalid")
    if approval.get("status") != "APPROVED":
        raise TikTokRecoveryContractError("recovery approval is not approved")
    required = {
        "candidate_digest": candidate_digest,
        "business_snapshot_digest": business_digest,
        "execution_snapshot_digest": execution_digest,
        "source_run_id": source_run_id,
        "ordered_target_labels": list(labels),
    }
    for field, expected in required.items():
        observed = approval.get(field)
        if observed != expected:
            raise TikTokRecoveryContractError(f"approval {field} drifted")
    approval_digest = _sha(approval.get("approval_digest"), "approval_digest")
    unsigned_approval = {key: value for key, value in approval.items() if key != "approval_digest"}
    if _digest(unsigned_approval) != approval_digest:
        raise TikTokRecoveryContractError("approval digest does not match")

    claim = {
        "schema_version": CLAIM_SCHEMA,
        "offer_id": _text(contract.get("offer_id"), "offer_id", digits=True),
        "seller_sku": _text(contract.get("seller_sku"), "seller_sku", digits=True),
        "platform": "TIKTOK",
        "source_run_id": source_run_id,
        "source_report_id": _text(contract.get("source_report_id"), "source_report_id"),
        "contract_digest": _sha(contract.get("contract_digest"), "contract_digest"),
        "candidate_digest": candidate_digest,
        "business_snapshot_digest": business_digest,
        "execution_snapshot_digest": execution_digest,
        "approval_digest": approval_digest,
        "ordered_target_labels": list(labels),
        "targets": contract_targets,
        "shared_mutation_budget": 0,
        "forbidden_operations": ["CREATE_DRAFT", "CLAIM_TO_SHOP"],
        "unknown_outcome_policy": "RECONCILIATION_REQUIRED_NO_RETRY",
        "provider_readback_required": True,
    }
    claim["claim_digest"] = _digest(claim)
    return claim


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tiktok_existing_detail_recovery_claims (
    claim_digest TEXT PRIMARY KEY,
    claim_json TEXT NOT NULL,
    source_run_id TEXT NOT NULL,
    candidate_digest TEXT NOT NULL,
    approval_digest TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tiktok_existing_detail_recovery_reservations (
    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_digest TEXT NOT NULL,
    target_label TEXT NOT NULL,
    operation TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    reservation_digest TEXT NOT NULL UNIQUE,
    UNIQUE (claim_digest, target_label, sequence),
    FOREIGN KEY (claim_digest) REFERENCES tiktok_existing_detail_recovery_claims(claim_digest)
);
CREATE TABLE IF NOT EXISTS tiktok_existing_detail_recovery_results (
    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_digest TEXT NOT NULL UNIQUE,
    outcome TEXT NOT NULL,
    provider_readback_verified INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    result_digest TEXT NOT NULL UNIQUE,
    FOREIGN KEY (reservation_digest) REFERENCES tiktok_existing_detail_recovery_reservations(reservation_digest)
);
"""


class TikTokExistingDetailRecoveryLedger:
    """SQLite claim and reservation ledger with atomic pre-write claims."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA)

    def register_claim(self, claim: Mapping[str, Any]) -> dict[str, Any]:
        """Persist already approved authority; this method grants no approval."""

        if claim.get("schema_version") != CLAIM_SCHEMA:
            raise TikTokRecoveryContractError("claim schema is invalid")
        claim_digest = _sha(claim.get("claim_digest"), "claim_digest")
        unsigned = {key: value for key, value in claim.items() if key != "claim_digest"}
        if _digest(unsigned) != claim_digest:
            raise TikTokRecoveryContractError("claim digest does not match")
        labels = _ordered_labels(claim.get("ordered_target_labels"))
        targets = claim.get("targets")
        if not isinstance(targets, list) or [row.get("target_label") for row in targets if isinstance(row, Mapping)] != list(labels):
            raise TikTokRecoveryContractError("claim target scope is incomplete")
        if claim.get("shared_mutation_budget") != 0:
            raise TikTokRecoveryContractError("claim shared budget must be zero")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat()
        body = _json(dict(claim))
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT claim_json FROM tiktok_existing_detail_recovery_claims WHERE claim_digest = ?",
                (claim_digest,),
            ).fetchone()
            if existing is not None:
                if existing["claim_json"] != body:
                    raise TikTokRecoveryStateError("claim digest stores different facts")
                return deepcopy(dict(claim))
            same_source = conn.execute(
                "SELECT claim_digest FROM tiktok_existing_detail_recovery_claims WHERE source_run_id = ?",
                (claim["source_run_id"],),
            ).fetchall()
            if same_source:
                raise TikTokRecoveryStateError("source run already has a different recovery claim")
            conn.execute(
                "INSERT INTO tiktok_existing_detail_recovery_claims VALUES (?, ?, ?, ?, ?, ?)",
                (
                    claim_digest,
                    body,
                    claim["source_run_id"],
                    claim["candidate_digest"],
                    claim["approval_digest"],
                    now,
                ),
            )
            conn.commit()
        return deepcopy(dict(claim))

    def load_claim(self, *, claim_digest: str) -> dict[str, Any]:
        """Load and revalidate the exact durable authority body."""

        safe_claim = _sha(claim_digest, "claim_digest")
        with self._connect() as conn:
            self._ensure_schema(conn)
            row = conn.execute(
                "SELECT claim_json FROM tiktok_existing_detail_recovery_claims WHERE claim_digest = ?",
                (safe_claim,),
            ).fetchone()
        if row is None:
            raise TikTokRecoveryStateError("recovery claim is not registered")
        try:
            claim = json.loads(row["claim_json"])
        except (TypeError, ValueError) as error:
            raise TikTokRecoveryStateError("durable recovery claim is invalid") from error
        if not isinstance(claim, Mapping) or claim.get("claim_digest") != safe_claim:
            raise TikTokRecoveryStateError("durable recovery claim identity drifted")
        unsigned = {key: value for key, value in claim.items() if key != "claim_digest"}
        if _digest(unsigned) != safe_claim or _json(claim) != row["claim_json"]:
            raise TikTokRecoveryStateError("durable recovery claim digest drifted")
        return deepcopy(dict(claim))

    def reserve(self, *, claim_digest: str, target_label: str, operation: str) -> dict[str, Any]:
        """Atomically commit the one allowed next mutation before network I/O."""

        safe_claim = _sha(claim_digest, "claim_digest")
        safe_target = _text(target_label, "target_label")
        safe_operation = _text(operation, "operation")
        if safe_operation in FORBIDDEN_OPERATIONS or safe_operation not in ALLOWED_OPERATIONS:
            raise TikTokRecoveryStateError("operation is forbidden for existing-detail recovery")
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT claim_json FROM tiktok_existing_detail_recovery_claims WHERE claim_digest = ?",
                (safe_claim,),
            ).fetchone()
            if row is None:
                raise TikTokRecoveryStateError("recovery claim is not registered")
            claim = json.loads(row["claim_json"])
            if safe_target not in claim["ordered_target_labels"]:
                raise TikTokRecoveryStateError("target is outside the approved recovery scope")
            reservations = conn.execute(
                "SELECT * FROM tiktok_existing_detail_recovery_reservations WHERE claim_digest = ? AND target_label = ? ORDER BY sequence",
                (safe_claim, safe_target),
            ).fetchall()
            if len(reservations) >= 2:
                raise TikTokRecoveryStateError("target mutation budget is exhausted")
            if reservations:
                prior_result = conn.execute(
                    "SELECT * FROM tiktok_existing_detail_recovery_results WHERE reservation_digest = ?",
                    (reservations[-1]["reservation_digest"],),
                ).fetchone()
                if prior_result is None:
                    raise TikTokRecoveryStateError("prior mutation outcome is unresolved")
                if prior_result["outcome"] == "UNKNOWN":
                    raise TikTokRecoveryStateError("target requires readback reconciliation")
                if prior_result["outcome"] != "CONFIRMED":
                    raise TikTokRecoveryStateError("prior operation was not confirmed")
            expected = ALLOWED_OPERATIONS[len(reservations)]
            if safe_operation != expected:
                raise TikTokRecoveryStateError(f"next operation must be {expected}")
            now = datetime.now(timezone.utc).isoformat()
            payload = {
                "claim_digest": safe_claim,
                "target_label": safe_target,
                "operation": safe_operation,
                "sequence": len(reservations) + 1,
                "created_at": now,
            }
            reservation_digest = _digest(payload)
            conn.execute(
                "INSERT INTO tiktok_existing_detail_recovery_reservations (claim_digest, target_label, operation, sequence, created_at, reservation_digest) VALUES (?, ?, ?, ?, ?, ?)",
                (safe_claim, safe_target, safe_operation, payload["sequence"], now, reservation_digest),
            )
            conn.commit()
        return {"schema_version": "tiktok-existing-detail-recovery-reservation/v1", **payload, "reservation_digest": reservation_digest}

    def record_result(
        self,
        *,
        reservation_digest: str,
        outcome: str,
        provider_readback_verified: bool = False,
    ) -> dict[str, Any]:
        safe_reservation = _sha(reservation_digest, "reservation_digest")
        safe_outcome = _text(outcome, "outcome")
        if safe_outcome not in OUTCOMES or type(provider_readback_verified) is not bool:
            raise TikTokRecoveryStateError("recovery result is invalid")
        with self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            reservation = conn.execute(
                "SELECT * FROM tiktok_existing_detail_recovery_reservations WHERE reservation_digest = ?",
                (safe_reservation,),
            ).fetchone()
            if reservation is None:
                raise TikTokRecoveryStateError("reservation is not durable")
            if reservation["operation"] == "PUBLISH_TARGET" and safe_outcome == "CONFIRMED" and not provider_readback_verified:
                raise TikTokRecoveryStateError("publish success requires provider readback")
            existing = conn.execute(
                "SELECT * FROM tiktok_existing_detail_recovery_results WHERE reservation_digest = ?",
                (safe_reservation,),
            ).fetchone()
            if existing is not None:
                if existing["outcome"] != safe_outcome or bool(existing["provider_readback_verified"]) != provider_readback_verified:
                    raise TikTokRecoveryStateError("reservation already stores a different result")
                return self._result_payload(existing)
            now = datetime.now(timezone.utc).isoformat()
            payload = {
                "reservation_digest": safe_reservation,
                "outcome": safe_outcome,
                "provider_readback_verified": provider_readback_verified,
                "created_at": now,
            }
            result_digest = _digest(payload)
            conn.execute(
                "INSERT INTO tiktok_existing_detail_recovery_results (reservation_digest, outcome, provider_readback_verified, created_at, result_digest) VALUES (?, ?, ?, ?, ?)",
                (safe_reservation, safe_outcome, int(provider_readback_verified), now, result_digest),
            )
            conn.commit()
            payload["result_digest"] = result_digest
            payload["schema_version"] = "tiktok-existing-detail-recovery-result/v1"
            return payload

    @staticmethod
    def _result_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": "tiktok-existing-detail-recovery-result/v1",
            "reservation_digest": row["reservation_digest"],
            "outcome": row["outcome"],
            "provider_readback_verified": bool(row["provider_readback_verified"]),
            "created_at": row["created_at"],
            "result_digest": row["result_digest"],
        }

    def snapshot(self, *, claim_digest: str) -> dict[str, Any]:
        safe_claim = _sha(claim_digest, "claim_digest")
        with self._connect() as conn:
            self._ensure_schema(conn)
            claim_row = conn.execute(
                "SELECT claim_json FROM tiktok_existing_detail_recovery_claims WHERE claim_digest = ?",
                (safe_claim,),
            ).fetchone()
            if claim_row is None:
                raise TikTokRecoveryStateError("recovery claim is not registered")
            claim = json.loads(claim_row["claim_json"])
            rows = conn.execute(
                "SELECT r.*, x.outcome, x.provider_readback_verified, x.result_digest FROM tiktok_existing_detail_recovery_reservations r LEFT JOIN tiktok_existing_detail_recovery_results x ON x.reservation_digest = r.reservation_digest WHERE r.claim_digest = ? ORDER BY r.target_label, r.sequence",
                (safe_claim,),
            ).fetchall()
        by_target: dict[str, list[dict[str, Any]]] = {label: [] for label in claim["ordered_target_labels"]}
        for row in rows:
            by_target[row["target_label"]].append(
                {
                    "sequence": row["sequence"],
                    "operation": row["operation"],
                    "reservation_digest": row["reservation_digest"],
                    "outcome": row["outcome"],
                    "provider_readback_verified": bool(row["provider_readback_verified"]) if row["outcome"] else False,
                }
            )
        targets = []
        for label in claim["ordered_target_labels"]:
            events = by_target[label]
            if events and events[-1]["outcome"] == "UNKNOWN":
                state = "RECONCILIATION_REQUIRED"
            elif len(events) == 2 and events[-1]["outcome"] == "CONFIRMED" and events[-1]["provider_readback_verified"]:
                state = "SUCCEEDED"
            elif events and events[-1]["outcome"] == "FAILED_ZERO_WRITE":
                state = "FAILED"
            elif events and events[-1]["outcome"] is None:
                state = "OUTCOME_PENDING"
            else:
                state = "READY"
            targets.append({"target_label": label, "state": state, "attempts": len(events), "budget": 2, "events": events})
        return {
            "schema_version": LEDGER_SCHEMA,
            "claim_digest": safe_claim,
            "source_run_id": claim["source_run_id"],
            "candidate_digest": claim["candidate_digest"],
            "approval_digest": claim["approval_digest"],
            "shared_attempts": 0,
            "targets": targets,
        }


def execute_registered_recovery(
    *,
    ledger: TikTokExistingDetailRecoveryLedger,
    claim: Mapping[str, Any],
    source_run_id: str,
    ordered_target_labels: Sequence[str],
    transport: ExistingDetailRecoveryTransport,
) -> dict[str, Any]:
    """Run the exact registered claim through a deliberately narrow transport.

    Callers must pass the source run and complete ordered label list again at
    execution time.  An exception at either provider mutation is conservative:
    the committed reservation becomes UNKNOWN and the target stops for
    readback reconciliation.  This function has no zero-write retry mode.
    """

    if not isinstance(claim, Mapping):
        raise TikTokRecoveryContractError("execution claim is invalid")
    claim_digest = _sha(claim.get("claim_digest"), "claim_digest")
    unsigned_claim = {key: value for key, value in claim.items() if key != "claim_digest"}
    if _digest(unsigned_claim) != claim_digest:
        raise TikTokRecoveryContractError("execution claim digest drifted")
    durable_claim = ledger.load_claim(claim_digest=claim_digest)
    if _json(dict(claim)) != _json(durable_claim):
        raise TikTokRecoveryContractError("execution claim differs from durable authority")
    claim = durable_claim
    current = ledger.snapshot(claim_digest=claim_digest)
    if current["source_run_id"] != _text(source_run_id, "source_run_id"):
        raise TikTokRecoveryContractError("execution source run drifted")
    labels = _ordered_labels(ordered_target_labels)
    if tuple(claim.get("ordered_target_labels") or ()) != labels:
        raise TikTokRecoveryContractError("execution target order drifted")
    if current["candidate_digest"] != claim.get("candidate_digest") or current["approval_digest"] != claim.get("approval_digest"):
        raise TikTokRecoveryContractError("registered execution authority drifted")
    frozen_by_label = {row["target_label"]: row for row in claim["targets"]}
    outcomes: list[dict[str, Any]] = []
    for label in labels:
        target = frozen_by_label[label]
        observed = transport.verify_existing_detail(deepcopy(target))
        expected_identity = {
            "detail_id": target["detail_id"],
            "shop_id": target["shop_id"],
            "detail_identity_digest": target["detail_identity_digest"],
        }
        if not isinstance(observed, Mapping) or any(observed.get(key) != value for key, value in expected_identity.items()):
            raise TikTokRecoveryContractError(f"{label} official existing detail identity drifted")
        target_outcome = "FAILED"
        for operation, method in (
            ("SAVE_DRAFT", transport.save_existing_detail),
            ("PUBLISH_TARGET", transport.publish_existing_detail),
        ):
            reservation = ledger.reserve(
                claim_digest=claim_digest,
                target_label=label,
                operation=operation,
            )
            try:
                result = method(deepcopy(target))
            except Exception:
                ledger.record_result(
                    reservation_digest=reservation["reservation_digest"],
                    outcome="UNKNOWN",
                )
                target_outcome = "RECONCILIATION_REQUIRED"
                break
            confirmed = isinstance(result, Mapping) and result.get("confirmed") is True
            if not confirmed:
                ledger.record_result(
                    reservation_digest=reservation["reservation_digest"],
                    outcome="FAILED_ZERO_WRITE",
                )
                target_outcome = "FAILED"
                break
            readback = bool(result.get("provider_readback_verified"))
            ledger.record_result(
                reservation_digest=reservation["reservation_digest"],
                outcome="CONFIRMED",
                provider_readback_verified=(readback if operation == "PUBLISH_TARGET" else False),
            )
            target_outcome = "SUCCEEDED" if operation == "PUBLISH_TARGET" else "READY"
        outcomes.append({"target_label": label, "outcome": target_outcome})
    return {
        "schema_version": "tiktok-existing-detail-route-recovery-execution/v1",
        "claim_digest": claim_digest,
        "source_run_id": source_run_id,
        "ordered_target_labels": list(labels),
        "targets": outcomes,
        "ledger": ledger.snapshot(claim_digest=claim_digest),
    }


__all__ = [
    "ALLOWED_OPERATIONS",
    "CLAIM_SCHEMA",
    "CONTRACT_SCHEMA",
    "LEDGER_SCHEMA",
    "TikTokExistingDetailRecoveryLedger",
    "TikTokRecoveryContractError",
    "TikTokRecoveryOutcomeUnknown",
    "TikTokRecoveryStateError",
    "build_recovery_claim",
    "execute_registered_recovery",
]
