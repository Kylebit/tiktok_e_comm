"""Guarded execution state machine for an authorised release plan.

The orchestrator creates an immutable, token-bound plan.  This module is the
next boundary: it schedules target adapters in dependency order and records
retryable per-target outcomes.  It deliberately contains no marketplace
imports.  Production adapters must be injected only after they can consume
the unified request, validate its confirmation token, preserve its
idempotency key, and verify their own read-back.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from domains.channel_operations.omnichannel_orchestrator import (
    ADAPTER_NAMES,
    CHANNEL_ORDER,
    ChannelExecutionPlan,
    OmnichannelPublicationPlan,
)


ExecutionStatus = Literal["PENDING", "SUCCESS", "FAILED", "BLOCKED"]
RETRYABLE_STATUSES = frozenset({"PENDING", "FAILED"})


class ReleaseExecutionError(ValueError):
    """Raised before any adapter call when execution authority is invalid."""


@dataclass(frozen=True)
class ExecutionBlocker:
    code: str
    detail: str
    dependency_target: str | None = None


@dataclass(frozen=True)
class AdapterExecutionRequest:
    plan_id: str
    confirmation_token: str
    approval_scope_digest: str
    product_id: str
    seller_sku: str
    product_package_id: str
    content_package_id: str
    channel: str
    site: str
    target_label: str
    idempotency_key: str


@dataclass(frozen=True)
class AdapterExecutionResult:
    succeeded: bool
    readback_verified: bool
    detail: str
    external_reference: str | None = None
    readback_evidence: Mapping[str, Any] | None = None


AdapterCallable = Callable[[AdapterExecutionRequest], AdapterExecutionResult]


@dataclass(frozen=True)
class AdapterRegistration:
    adapter_name: str
    execute: AdapterCallable | None
    consumes_unified_plan: bool
    validates_confirmation_token: bool
    preserves_idempotency_key: bool
    verifies_readback: bool
    blocker: ExecutionBlocker | None = None

    @property
    def executable(self) -> bool:
        return bool(
            self.execute is not None
            and self.consumes_unified_plan
            and self.validates_confirmation_token
            and self.preserves_idempotency_key
            and self.verifies_readback
            and self.blocker is None
        )


@dataclass(frozen=True)
class TargetExecutionRecord:
    channel: str
    site: str
    target_label: str
    idempotency_key: str
    status: ExecutionStatus = "PENDING"
    attempts: int = 0
    readback_verified: bool = False
    external_reference: str | None = None
    last_detail: str = ""
    blocker: ExecutionBlocker | None = None


@dataclass(frozen=True)
class ReleaseExecutionReport:
    plan_id: str
    confirmation_token: str
    records: tuple[TargetExecutionRecord, ...]
    adapter_calls_performed: tuple[str, ...]
    complete: bool


def production_adapter_registry() -> dict[str, AdapterRegistration]:
    """Return the conservative production registry for the current repository.

    Legacy functions existing in the repository are not sufficient evidence
    of compatibility with the unified plan contract.  None currently validate
    this plan's token and idempotency key *and* perform governed read-back, so
    every registration is intentionally non-executable.
    """

    reasons = {
        "miaoshou": (
            "legacy Miaoshou commit does not yet consume the unified release "
            "request and persist token-bound read-back"
        ),
        "tiktok": (
            "legacy TikTok paths, including MX/GB, do not yet validate the "
            "unified confirmation token and request schema"
        ),
        "shopee": (
            "Shopee has no audited unified-plan adapter with verified read-back"
        ),
        "ozon": (
            "Ozon has no audited unified-plan adapter with verified read-back"
        ),
    }
    return {
        adapter_name: AdapterRegistration(
            adapter_name=adapter_name,
            execute=None,
            consumes_unified_plan=False,
            validates_confirmation_token=False,
            preserves_idempotency_key=False,
            verifies_readback=False,
            blocker=ExecutionBlocker(
                code="adapter_not_unified",
                detail=reasons[channel],
            ),
        )
        for channel, adapter_name in ADAPTER_NAMES.items()
    }


def execute_release_plan(
    plan: OmnichannelPublicationPlan,
    *,
    adapter_registry: Mapping[str, AdapterRegistration] | None = None,
    prior_records: Mapping[str, TargetExecutionRecord] | None = None,
) -> ReleaseExecutionReport:
    """Execute eligible targets once, preserving dependency and retry state.

    Only ``PENDING`` and ``FAILED`` records are considered.  Successful or
    permanently blocked records are never called again.  A dependency that has
    not succeeded leaves the target retryable without consuming an attempt.
    """

    if plan.dry_run or not plan.execution_authorized:
        raise ReleaseExecutionError(
            "an authorised non-dry-run plan is required before adapter execution"
        )

    registry = dict(
        production_adapter_registry()
        if adapter_registry is None
        else adapter_registry
    )
    records = _initial_records(plan, prior_records or {})
    calls: list[str] = []
    ordered_targets = sorted(
        plan.targets,
        key=lambda target: (
            CHANNEL_ORDER.index(target.channel),
            target.site,
        ),
    )
    for target in ordered_targets:
        label = _target_label(target)
        record = records[label]
        if record.status not in RETRYABLE_STATUSES:
            continue

        if not target.executable:
            records[label] = replace(
                record,
                status="BLOCKED",
                blocker=ExecutionBlocker(
                    code="target_preflight_failed",
                    detail=_preflight_failure_detail(target),
                ),
            )
            continue

        dependency_blocker = _dependency_blocker(target, records)
        if dependency_blocker is not None:
            records[label] = replace(record, blocker=dependency_blocker)
            continue

        registration = registry.get(target.adapter)
        if registration is None:
            records[label] = replace(
                record,
                status="BLOCKED",
                blocker=ExecutionBlocker(
                    code="adapter_not_registered",
                    detail=f"no adapter registration exists for {target.adapter}",
                ),
            )
            continue
        if registration.adapter_name != target.adapter:
            records[label] = replace(
                record,
                status="BLOCKED",
                blocker=ExecutionBlocker(
                    code="adapter_registration_mismatch",
                    detail=(
                        f"registration {registration.adapter_name} cannot "
                        f"execute target adapter {target.adapter}"
                    ),
                ),
            )
            continue
        if not registration.executable:
            records[label] = replace(
                record,
                status="BLOCKED",
                blocker=registration.blocker
                or ExecutionBlocker(
                    code="adapter_contract_incomplete",
                    detail=(
                        f"{target.adapter} is not approved for unified "
                        "token-bound execution and read-back"
                    ),
                ),
            )
            continue

        request = _adapter_request(plan, target)
        calls.append(label)
        try:
            result = registration.execute(request)  # type: ignore[misc]
        except Exception as error:
            records[label] = replace(
                record,
                status="FAILED",
                attempts=record.attempts + 1,
                readback_verified=False,
                last_detail=str(error),
                blocker=ExecutionBlocker(
                    code="adapter_exception",
                    detail=str(error),
                ),
            )
            continue
        if not isinstance(result, AdapterExecutionResult):
            records[label] = replace(
                record,
                status="FAILED",
                attempts=record.attempts + 1,
                readback_verified=False,
                last_detail="adapter returned an invalid result contract",
                blocker=ExecutionBlocker(
                    code="invalid_adapter_result",
                    detail="adapter must return AdapterExecutionResult",
                ),
            )
            continue
        if not result.succeeded or not result.readback_verified:
            code = (
                "adapter_failed"
                if not result.succeeded
                else "readback_not_verified"
            )
            records[label] = replace(
                record,
                status="FAILED",
                attempts=record.attempts + 1,
                readback_verified=False,
                external_reference=result.external_reference,
                last_detail=result.detail,
                blocker=ExecutionBlocker(code=code, detail=result.detail),
            )
            continue
        records[label] = replace(
            record,
            status="SUCCESS",
            attempts=record.attempts + 1,
            readback_verified=True,
            external_reference=result.external_reference,
            last_detail=result.detail,
            blocker=None,
        )

    ordered_records = tuple(
        records[_target_label(target)] for target in ordered_targets
    )
    return ReleaseExecutionReport(
        plan_id=plan.plan_id,
        confirmation_token=plan.approval.confirmation_token,
        records=ordered_records,
        adapter_calls_performed=tuple(calls),
        complete=bool(ordered_records)
        and all(record.status == "SUCCESS" for record in ordered_records),
    )


def _initial_records(
    plan: OmnichannelPublicationPlan,
    prior_records: Mapping[str, TargetExecutionRecord],
) -> dict[str, TargetExecutionRecord]:
    target_labels = {_target_label(target) for target in plan.targets}
    unknown = sorted(set(prior_records) - target_labels)
    if unknown:
        raise ReleaseExecutionError(
            "prior records contain targets outside this plan: "
            + ", ".join(unknown)
        )
    records: dict[str, TargetExecutionRecord] = {}
    for target in plan.targets:
        label = _target_label(target)
        prior = prior_records.get(label)
        if prior is not None:
            if prior.status not in {"PENDING", "SUCCESS", "FAILED", "BLOCKED"}:
                raise ReleaseExecutionError(
                    f"prior status is invalid for {label}: {prior.status}"
                )
            if prior.idempotency_key != target.idempotency_key:
                raise ReleaseExecutionError(
                    f"prior idempotency key does not match {label}"
                )
            if prior.target_label != label:
                raise ReleaseExecutionError(
                    f"prior target identity does not match {label}"
                )
            if prior.status == "SUCCESS" and not prior.readback_verified:
                raise ReleaseExecutionError(
                    f"successful prior record lacks verified read-back for {label}"
                )
            records[label] = prior
            continue
        records[label] = TargetExecutionRecord(
            channel=target.channel,
            site=target.site,
            target_label=label,
            idempotency_key=target.idempotency_key,
        )
    return records


def _dependency_blocker(
    target: ChannelExecutionPlan,
    records: Mapping[str, TargetExecutionRecord],
) -> ExecutionBlocker | None:
    for dependency in target.depends_on:
        label = _dependency_target_label(dependency)
        if label is None:
            return ExecutionBlocker(
                code="dependency_not_explicit",
                detail=(
                    f"{_target_label(target)} requires an explicit upstream "
                    f"target, not {dependency}"
                ),
            )
        record = records.get(label)
        if record is None:
            return ExecutionBlocker(
                code="dependency_not_selected",
                detail=f"required dependency {label} is not in this plan",
                dependency_target=label,
            )
        if record.status != "SUCCESS" or not record.readback_verified:
            return ExecutionBlocker(
                code="dependency_not_verified",
                detail=f"required dependency {label} has not succeeded with read-back",
                dependency_target=label,
            )
    return None


def _dependency_target_label(value: str) -> str | None:
    parts = str(value or "").split(":")
    if len(parts) < 3:
        return None
    channel, site = parts[0].strip().lower(), parts[1].strip().upper()
    if channel not in CHANNEL_ORDER or not site or site == "MASTER":
        return None
    return f"{channel}:{site}"


def _preflight_failure_detail(target: ChannelExecutionPlan) -> str:
    failures = [
        check.detail for check in target.preflight if not check.passed
    ]
    return "; ".join(failures) or f"{_target_label(target)} is not executable"


def _adapter_request(
    plan: OmnichannelPublicationPlan,
    target: ChannelExecutionPlan,
) -> AdapterExecutionRequest:
    approval = plan.approval
    return AdapterExecutionRequest(
        plan_id=plan.plan_id,
        confirmation_token=approval.confirmation_token,
        approval_scope_digest=approval.approval_scope_digest,
        product_id=approval.product_id,
        seller_sku=approval.seller_sku,
        product_package_id=approval.product_package_id,
        content_package_id=approval.content_package_id,
        channel=target.channel,
        site=target.site,
        target_label=_target_label(target),
        idempotency_key=target.idempotency_key,
    )


def _target_label(target: ChannelExecutionPlan) -> str:
    return f"{target.channel}:{target.site}"
