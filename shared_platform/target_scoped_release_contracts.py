"""Stable shared-platform contracts for one governed release target action.

The platform owns authority, durable state and proof consumption. Channel
operations owns the official proof providers and marketplace adapters. This
module deliberately contains no marketplace imports.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


SHOPEE_SAFE_PRE_SUBMIT_RETRY = "shopee_safe_pre_submit_retry_v1"
OZON_EXISTING_PRODUCT_STOCK_RECONCILIATION = (
    "ozon_existing_product_stock_reconciliation_v1"
)
TARGET_SCOPED_OPERATION_KINDS: dict[str, str] = {
    "shopee:MY": SHOPEE_SAFE_PRE_SUBMIT_RETRY,
    "shopee:VN": SHOPEE_SAFE_PRE_SUBMIT_RETRY,
    "ozon:RU": OZON_EXISTING_PRODUCT_STOCK_RECONCILIATION,
}

_SENSITIVE_KEY_PARTS = (
    "access_token",
    "refresh_token",
    "confirmation_token",
    "authorization",
    "cookie",
    "secret",
    "raw_response",
)


class TargetScopedContractError(ValueError):
    """A target-scoped request, proof or result violated the stable contract."""


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise TargetScopedContractError(
            "target-scoped evidence must be JSON-serializable"
        ) from error


def canonical_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def operation_kind_for_target(target_label: str) -> str:
    label = str(target_label or "").strip()
    try:
        return TARGET_SCOPED_OPERATION_KINDS[label]
    except KeyError as error:
        raise TargetScopedContractError(
            f"target-scoped action is not supported for {label or 'empty target'}"
        ) from error


def _strict_non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TargetScopedContractError(
            f"{field} must be a non-negative integer"
        )
    return value


def _required_text(value: object, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TargetScopedContractError(f"{field} is required")
    return text


def _assert_redacted(value: object, *, path: str = "evidence") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                raise TargetScopedContractError(
                    f"{path}.{key} contains a forbidden sensitive field"
                )
            _assert_redacted(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_redacted(item, path=f"{path}[{index}]")


def _parse_utc(value: object, field: str) -> datetime:
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise TargetScopedContractError(
            f"{field} must be an ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise TargetScopedContractError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def target_failure_digest(
    *,
    target_label: str,
    attempts: int,
    error: object,
    failure_event_digests: list[str] | tuple[str, ...],
) -> str:
    return canonical_digest(
        {
            "target_label": _required_text(target_label, "target_label"),
            "attempts": _strict_non_negative_int(attempts, "attempts"),
            "error": str(error or ""),
            "failure_event_digests": [
                str(value or "").strip() for value in failure_event_digests
            ],
        }
    )


def target_preflight_digest(
    *,
    plan_id: str,
    run_id: str,
    target_label: str,
    operation_kind: str,
    product_revision: int,
    payload_digest: str,
    failure_attempt: int,
    failure_digest: str,
    target_idempotency_key: str,
) -> str:
    expected_kind = operation_kind_for_target(target_label)
    if operation_kind != expected_kind:
        raise TargetScopedContractError(
            "operation_kind does not match the server target allowlist"
        )
    return canonical_digest(
        {
            "schema_version": "target-scoped-preflight/v1",
            "plan_id": _required_text(plan_id, "plan_id"),
            "run_id": _required_text(run_id, "run_id"),
            "target_label": _required_text(target_label, "target_label"),
            "operation_kind": operation_kind,
            "product_revision": _strict_non_negative_int(
                product_revision, "product_revision"
            ),
            "payload_digest": _required_text(
                payload_digest, "payload_digest"
            ),
            "failure_attempt": _strict_non_negative_int(
                failure_attempt, "failure_attempt"
            ),
            "failure_digest": _required_text(
                failure_digest, "failure_digest"
            ),
            "target_idempotency_key": _required_text(
                target_idempotency_key, "target_idempotency_key"
            ),
        }
    )


@dataclass(frozen=True)
class TargetScopedOperationRequest:
    """Exact server-authorized request passed to a channel proof/adapter seam."""

    plan_id: str
    confirmation_token: str
    approval_scope_digest: str
    product_id: str
    seller_sku: str
    product_package_id: str
    content_package_id: str
    run_id: str
    target_label: str
    operation_kind: str
    product_revision: int
    payload_digest: str
    preflight_digest: str
    failure_attempt: int
    failure_digest: str
    target_idempotency_key: str
    approved_by: str = "Kyle"

    def __post_init__(self) -> None:
        for field in (
            "plan_id",
            "confirmation_token",
            "approval_scope_digest",
            "product_id",
            "seller_sku",
            "product_package_id",
            "content_package_id",
            "run_id",
            "target_label",
            "operation_kind",
            "payload_digest",
            "preflight_digest",
            "failure_digest",
            "target_idempotency_key",
        ):
            _required_text(getattr(self, field), field)
        _strict_non_negative_int(self.product_revision, "product_revision")
        _strict_non_negative_int(self.failure_attempt, "failure_attempt")
        if self.approved_by != "Kyle":
            raise TargetScopedContractError(
                "target-scoped action requires approved_by=Kyle"
            )
        expected_kind = operation_kind_for_target(self.target_label)
        if self.operation_kind != expected_kind:
            raise TargetScopedContractError(
                "operation_kind does not match target_label"
            )
        expected_preflight = target_preflight_digest(
            plan_id=self.plan_id,
            run_id=self.run_id,
            target_label=self.target_label,
            operation_kind=self.operation_kind,
            product_revision=self.product_revision,
            payload_digest=self.payload_digest,
            failure_attempt=self.failure_attempt,
            failure_digest=self.failure_digest,
            target_idempotency_key=self.target_idempotency_key,
        )
        if self.preflight_digest != expected_preflight:
            raise TargetScopedContractError(
                "preflight_digest does not match target failure identity"
            )

    @property
    def confirmation_token_digest(self) -> str:
        return hashlib.sha256(
            self.confirmation_token.encode("utf-8")
        ).hexdigest()

    def durable_identity(self) -> dict[str, Any]:
        """Return the immutable operation identity without persisting secrets."""

        return {
            "schema_version": "target-scoped-operation-request/v1",
            "plan_id": self.plan_id,
            "confirmation_token_digest": self.confirmation_token_digest,
            "approval_scope_digest": self.approval_scope_digest,
            "product_id": self.product_id,
            "seller_sku": self.seller_sku,
            "product_package_id": self.product_package_id,
            "content_package_id": self.content_package_id,
            "run_id": self.run_id,
            "target_label": self.target_label,
            "operation_kind": self.operation_kind,
            "product_revision": self.product_revision,
            "payload_digest": self.payload_digest,
            "preflight_digest": self.preflight_digest,
            "failure_attempt": self.failure_attempt,
            "failure_digest": self.failure_digest,
            "target_idempotency_key": self.target_idempotency_key,
            "approved_by": self.approved_by,
        }

    def operation_digest(self, proof_digest: str) -> str:
        return canonical_digest(
            {
                **self.durable_identity(),
                "proof_digest": _required_text(
                    proof_digest, "proof_digest"
                ),
            }
        )


@dataclass(frozen=True)
class OfficialTargetProof:
    """Redacted official proof bound to one exact target failure attempt."""

    schema_version: str
    operation_kind: str
    plan_id: str
    run_id: str
    target_label: str
    product_revision: int
    payload_digest: str
    preflight_digest: str
    failure_attempt: int
    failure_digest: str
    provided_by: str
    allow_refresh: bool
    observed_at: str
    expires_at: str
    checks: Mapping[str, bool]
    semantic_evidence: Mapping[str, Any]
    redacted_summary: Mapping[str, Any]
    external_writes_performed: tuple[str, ...]
    proof_digest: str

    @classmethod
    def from_value(
        cls,
        value: object,
        *,
        request: TargetScopedOperationRequest,
        now: datetime | None = None,
    ) -> "OfficialTargetProof":
        if isinstance(value, cls):
            value = value.durable_payload()
        if not isinstance(value, Mapping):
            raise TargetScopedContractError(
                "official target proof must be a mapping"
            )
        else:
            raw = dict(value)
            checks = raw.get("checks")
            semantic = raw.get("semantic_evidence")
            summary = raw.get("redacted_summary") or {}
            writes = raw.get("external_writes_performed")
            if not isinstance(checks, Mapping) or not checks:
                raise TargetScopedContractError(
                    "official target proof requires named checks"
                )
            if not isinstance(semantic, Mapping) or not semantic:
                raise TargetScopedContractError(
                    "official target proof requires semantic_evidence"
                )
            if not isinstance(summary, Mapping):
                raise TargetScopedContractError(
                    "redacted_summary must be a mapping"
                )
            if not isinstance(writes, (list, tuple)):
                raise TargetScopedContractError(
                    "external_writes_performed must be a list"
                )
            semantic_payload = {
                "schema_version": str(
                    raw.get("schema_version")
                    or "official-target-proof/v1"
                ),
                "operation_kind": str(raw.get("operation_kind") or ""),
                "plan_id": str(raw.get("plan_id") or ""),
                "run_id": str(raw.get("run_id") or ""),
                "target_label": str(raw.get("target_label") or ""),
                "product_revision": raw.get("product_revision"),
                "payload_digest": str(raw.get("payload_digest") or ""),
                "preflight_digest": str(raw.get("preflight_digest") or ""),
                "failure_attempt": raw.get("failure_attempt"),
                "failure_digest": str(raw.get("failure_digest") or ""),
                "provided_by": str(raw.get("provided_by") or ""),
                "allow_refresh": raw.get("allow_refresh"),
                "checks": dict(checks),
                "semantic_evidence": dict(semantic),
                "external_writes_performed": list(writes),
            }
            computed_digest = canonical_digest(semantic_payload)
            supplied_digest = str(raw.get("proof_digest") or "").strip()
            if supplied_digest and supplied_digest != computed_digest:
                raise TargetScopedContractError(
                    "official proof_digest does not match semantic evidence"
                )
            proof = cls(
                schema_version=semantic_payload["schema_version"],
                operation_kind=semantic_payload["operation_kind"],
                plan_id=semantic_payload["plan_id"],
                run_id=semantic_payload["run_id"],
                target_label=semantic_payload["target_label"],
                product_revision=_strict_non_negative_int(
                    semantic_payload["product_revision"],
                    "product_revision",
                ),
                payload_digest=semantic_payload["payload_digest"],
                preflight_digest=semantic_payload["preflight_digest"],
                failure_attempt=_strict_non_negative_int(
                    semantic_payload["failure_attempt"],
                    "failure_attempt",
                ),
                failure_digest=semantic_payload["failure_digest"],
                provided_by=semantic_payload["provided_by"],
                allow_refresh=semantic_payload["allow_refresh"] is True,
                observed_at=str(raw.get("observed_at") or ""),
                expires_at=str(raw.get("expires_at") or ""),
                checks=dict(checks),
                semantic_evidence=dict(semantic),
                redacted_summary=dict(summary),
                external_writes_performed=tuple(str(item) for item in writes),
                proof_digest=computed_digest,
            )

        expected = {
            "operation_kind": request.operation_kind,
            "plan_id": request.plan_id,
            "run_id": request.run_id,
            "target_label": request.target_label,
            "product_revision": request.product_revision,
            "payload_digest": request.payload_digest,
            "preflight_digest": request.preflight_digest,
            "failure_attempt": request.failure_attempt,
            "failure_digest": request.failure_digest,
        }
        actual = {field: getattr(proof, field) for field in expected}
        if actual != expected:
            raise TargetScopedContractError(
                "official target proof identity does not match the request"
            )
        if proof.provided_by != "03":
            raise TargetScopedContractError(
                "official target proof must be provided by channel operations"
            )
        if proof.allow_refresh:
            raise TargetScopedContractError(
                "official target proof must use allow_refresh=false"
            )
        if proof.external_writes_performed:
            raise TargetScopedContractError(
                "official target proof must perform zero external writes"
            )
        if any(value is not True for value in proof.checks.values()):
            raise TargetScopedContractError(
                "official target proof did not pass every required check"
            )
        _assert_redacted(proof.semantic_evidence)
        _assert_redacted(proof.redacted_summary, path="redacted_summary")
        observed = _parse_utc(proof.observed_at, "observed_at")
        expires = _parse_utc(proof.expires_at, "expires_at")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if observed > current:
            raise TargetScopedContractError(
                "official target proof observed_at is in the future"
            )
        if expires <= current or expires <= observed:
            raise TargetScopedContractError(
                "official target proof is expired"
            )
        return proof

    def durable_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation_kind": self.operation_kind,
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "target_label": self.target_label,
            "product_revision": self.product_revision,
            "payload_digest": self.payload_digest,
            "preflight_digest": self.preflight_digest,
            "failure_attempt": self.failure_attempt,
            "failure_digest": self.failure_digest,
            "provided_by": self.provided_by,
            "allow_refresh": self.allow_refresh,
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
            "checks": dict(self.checks),
            "semantic_evidence": dict(self.semantic_evidence),
            "redacted_summary": dict(self.redacted_summary),
            "external_writes_performed": list(
                self.external_writes_performed
            ),
            "proof_digest": self.proof_digest,
        }


@dataclass(frozen=True)
class TargetScopedOperationResult:
    """Normalized, redacted outcome from one channel operation call."""

    succeeded: bool
    readback_verified: bool
    detail: str
    external_reference: str | None
    submission_accepted: bool
    evidence: Mapping[str, Any]

    @classmethod
    def from_value(cls, value: object) -> "TargetScopedOperationResult":
        if isinstance(value, cls):
            result = value
        elif isinstance(value, Mapping):
            raw = dict(value)
            evidence = raw.get("readback_evidence")
            if evidence is None:
                evidence = raw.get("evidence")
            result = cls(
                succeeded=raw.get("succeeded") is True,
                readback_verified=raw.get("readback_verified") is True,
                detail=str(raw.get("detail") or "").strip(),
                external_reference=(
                    str(raw.get("external_reference") or "").strip() or None
                ),
                submission_accepted=raw.get("submission_accepted") is True,
                evidence=dict(evidence or {}),
            )
        else:
            evidence = getattr(value, "readback_evidence", None)
            result = cls(
                succeeded=getattr(value, "succeeded", None) is True,
                readback_verified=(
                    getattr(value, "readback_verified", None) is True
                ),
                detail=str(getattr(value, "detail", "") or "").strip(),
                external_reference=(
                    str(
                        getattr(value, "external_reference", "") or ""
                    ).strip()
                    or None
                ),
                submission_accepted=(
                    getattr(value, "submission_accepted", None) is True
                ),
                evidence=dict(evidence or {}),
            )
        if not result.detail:
            raise TargetScopedContractError(
                "target-scoped adapter result requires detail"
            )
        writes = result.evidence.get("external_writes_performed")
        if not isinstance(writes, (list, tuple)):
            raise TargetScopedContractError(
                "adapter result must explicitly report external_writes_performed"
            )
        _assert_redacted(result.evidence, path="result_evidence")
        return result

    @property
    def external_writes_performed(self) -> list[str]:
        return [
            str(value)
            for value in (
                self.evidence.get("external_writes_performed") or ()
            )
            if str(value)
        ]

    @property
    def outcome(self) -> str:
        if (
            self.succeeded
            and self.readback_verified
            and self.evidence.get("verified") is True
        ):
            return "SUCCEEDED"
        if (
            not self.succeeded
            and not self.readback_verified
            and not self.external_reference
            and not self.submission_accepted
            and self.evidence.get("pre_submit_failure") is True
            and not self.external_writes_performed
        ):
            return "FAILED_PRE_SUBMIT"
        return "RECONCILIATION_REQUIRED"

    def durable_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "target-scoped-operation-result/v1",
            "succeeded": self.succeeded,
            "readback_verified": self.readback_verified,
            "detail": self.detail,
            "external_reference": self.external_reference,
            "submission_accepted": self.submission_accepted,
            "evidence": dict(self.evidence),
            "external_writes_performed": self.external_writes_performed,
            "outcome": self.outcome,
        }
