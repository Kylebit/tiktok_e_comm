"""Production composition for the three frozen-v4 publication executors.

This module only binds ``PublicationPlatformRequest`` to the deterministic
platform boundaries.  Provider access, durable identity preparation and
official readback are injected by the caller.  A fresh TikTok v4 run may
prepare exact per-store drafts through its dedicated v4 boundary; it never
starts the legacy collect-box action.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from domains.channel_operations.tiktok_v4_execution import (
    TikTokCategoryResolver,
    TikTokStorefrontReadback,
    TikTokTargetPublisher,
    execute_tiktok_v4_plan,
    project_tiktok_v4_execution_plan,
)
from domains.channel_operations.tiktok_publisher import (
    sanitize_tiktok_provider_code,
    sanitize_tiktok_provider_field_path,
    sanitize_tiktok_provider_reason,
)
from modules.ozon.approved_publication_v4 import build_ozon_v4_executor, UpdateStocks, ReadbackStocks, PrepareStockUpdate
from modules.shopee.skill_regions import (
    ShopeeRegionRuntime,
    dispatch_selected_regions,
    readback_dispatched_regions,
    selected_region_targets,
)
from shared_platform.product_publication_runner import (
    PLATFORM_RESULT_SCHEMA_VERSION,
    PlatformExecutor,
    PublicationPlatformRequest,
)
from shared_platform.product_description_media import validate_description_media_preflight


_PLATFORM_ORDER = ("TIKTOK", "SHOPEE", "OZON")
_TARGET_STATUSES = frozenset({"PUBLISHED", "PROCESSING", "FAILED"})
_TIKTOK_EVIDENCE_FIELDS = frozenset({"target_label", "status", "stage", "provider_code", "provider_field_path", "provider_reason", "request_attempted", "outcome_unknown", "external_write_count"})
_TIKTOK_EVIDENCE_STAGES = frozenset({"IDENTITY", "PREPARATION", "PREFLIGHT", "SAVE", "PUBLISH", "READBACK", "EXECUTION"})

CollectBoxContextResolver = Callable[
    [PublicationPlatformRequest], Mapping[str, Mapping[str, object]]
]
TikTokDraftPreparer = Callable[
    [PublicationPlatformRequest], Mapping[str, object]
]
ShopeeGlobalItemIdResolver = Callable[[PublicationPlatformRequest], object]
OzonDispatchTransport = Callable[[dict[str, Any]], object]
OzonReadback = Callable[[tuple[str, ...]], Sequence[Mapping[str, Any]]]
OzonOfficialProfileResolver = Callable[[Mapping[str, Any]], Mapping[str, Any]]
OzonLocalizedCopyResolver = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class TikTokV4ExecutorDependencies:
    collectbox_context_resolver: CollectBoxContextResolver | None
    category_resolver: TikTokCategoryResolver | None
    publisher: TikTokTargetPublisher
    storefront_readback: TikTokStorefrontReadback
    draft_preparer: TikTokDraftPreparer | None = None


@dataclass(frozen=True)
class ShopeeRegionExecutorDependencies:
    global_item_id_resolver: ShopeeGlobalItemIdResolver
    runtime: ShopeeRegionRuntime
    poll_attempts: int = 3


@dataclass(frozen=True)
class OzonV4ExecutorDependencies:
    dispatch_variant: OzonDispatchTransport
    readback_variants: OzonReadback
    official_profile_resolver: OzonOfficialProfileResolver | None = None
    localized_copy_resolver: OzonLocalizedCopyResolver | None = None
    update_stocks: UpdateStocks | None = None
    readback_stocks: ReadbackStocks | None = None
    prepare_stock_update: PrepareStockUpdate | None = None
    catalog_account_resolver: Callable[[], Mapping[str, str]] | None = None
    catalog_observer: Callable | None = None


def _request_facts(
    request: object, *, platform: str
) -> tuple[tuple[str, ...], Mapping[str, object]]:
    if getattr(request, "platform", None) != platform:
        raise ValueError("publication request platform conflicts")
    labels = getattr(request, "target_labels", None)
    if (
        not isinstance(labels, tuple)
        or not labels
        or any(type(label) is not str or not label for label in labels)
        or len(labels) != len(set(labels))
    ):
        raise ValueError("publication request target scope is invalid")
    expected_prefix = platform.casefold() + ":"
    if any(not label.casefold().startswith(expected_prefix) for label in labels):
        raise ValueError("publication request target platform conflicts")
    snapshot = getattr(request, "snapshot", None)
    if not isinstance(snapshot, Mapping):
        raise ValueError("publication request snapshot is invalid")
    return labels, snapshot


def _result(
    platform: str,
    labels: tuple[str, ...],
    statuses: Mapping[str, str],
    *,
    dispatch_attempted: bool,
    readback_completed: bool,
    external_write_count: int | None,
    requires_human_action: bool | None = None,
    target_evidence: Mapping[str, Mapping[str, object] | None] | None = None,
) -> dict[str, object]:
    if set(statuses) != set(labels):
        raise ValueError("platform result target coverage conflicts")
    if any(status not in _TARGET_STATUSES for status in statuses.values()):
        raise ValueError("platform result status is invalid")
    if external_write_count is not None and (
        type(external_write_count) is not int or external_write_count < 0
    ):
        raise ValueError("platform result write count is invalid")
    failed = any(statuses[label] == "FAILED" for label in labels)
    if target_evidence is not None and set(target_evidence) != set(labels):
        raise ValueError("platform result target evidence coverage conflicts")
    return {
        "schema_version": PLATFORM_RESULT_SCHEMA_VERSION,
        "platform": platform,
        "targets": [
            {"target_label": label, "status": statuses[label], **({"evidence": target_evidence[label]} if target_evidence is not None else {})} for label in labels
        ],
        "dispatch_attempted": dispatch_attempted,
        "readback_completed": readback_completed,
        "external_write_count": external_write_count,
        "requires_human_action": (
            failed if requires_human_action is None else requires_human_action
        ),
    }


def _zero_write_failure(platform: str, labels: tuple[str, ...]) -> dict[str, object]:
    return _result(
        platform,
        labels,
        {label: "FAILED" for label in labels},
        dispatch_attempted=False,
        readback_completed=False,
        external_write_count=0,
        requires_human_action=True,
    )


def _shopee_preparation_failure(
    labels: tuple[str, ...],
    *,
    provider_code: str,
    provider_reason: str,
    provider_field_path: str,
) -> dict[str, object]:
    evidence = {
        label: {
            "target_label": label,
            "status": "FAILED",
            "stage": "PREPARATION",
            "provider_code": provider_code,
            "provider_field_path": provider_field_path,
            "provider_reason": provider_reason,
            "request_attempted": False,
            "outcome_unknown": False,
            "external_write_count": 0,
        }
        for label in labels
    }
    return _result(
        "SHOPEE",
        labels,
        {label: "FAILED" for label in labels},
        dispatch_attempted=False,
        readback_completed=False,
        external_write_count=0,
        requires_human_action=True,
        target_evidence=evidence,
    )


def _safe_shopee_preparation_failure(
    labels: tuple[str, ...], error: BaseException
) -> dict[str, object]:
    message = str(error)
    if "category" in message.casefold():
        code, reason, field = (
            "shopee_category_preparation_failed",
            "Shopee category preparation failed before mutation",
            "category",
        )
    elif any(word in message.casefold() for word in ("policy", "warehouse", "brand")):
        code, reason, field = (
            "shopee_policy_facts_drifted",
            "Shopee policy facts failed validation before mutation",
            "policy",
        )
    elif any(word in message.casefold() for word in ("token", "credential", "merchant identity")):
        code, reason, field = (
            "shopee_credential_preparation_failed",
            "Shopee credential preparation failed before mutation",
            "credential",
        )
    elif "mapping" in message.casefold():
        code, reason, field = (
            "shopee_mapping_preparation_failed",
            "Shopee mapping preparation failed before mutation",
            "mapping",
        )
    else:
        code, reason, field = (
            "shopee_preparation_failed",
            "Shopee preparation failed before mutation",
            "preparation",
        )
    return _shopee_preparation_failure(
        labels,
        provider_code=code,
        provider_reason=reason,
        provider_field_path=field,
    )


def _unknown_execution_failure(
    platform: str, labels: tuple[str, ...]
) -> dict[str, object]:
    return _result(
        platform,
        labels,
        {label: "FAILED" for label in labels},
        dispatch_attempted=True,
        readback_completed=False,
        external_write_count=None,
        requires_human_action=True,
    )


def _target_rows(
    value: object, *, labels: tuple[str, ...]
) -> dict[str, Mapping[str, object]]:
    if not isinstance(value, Mapping):
        raise ValueError("platform receipt is invalid")
    raw_rows = value.get("targets")
    if not isinstance(raw_rows, list):
        raise ValueError("platform target receipt is invalid")
    rows: dict[str, Mapping[str, object]] = {}
    for row in raw_rows:
        if not isinstance(row, Mapping):
            raise ValueError("platform target receipt is invalid")
        label = row.get("target_label")
        if type(label) is not str or label in rows:
            raise ValueError("platform target receipt identity is invalid")
        rows[label] = row
    if set(rows) != set(labels):
        raise ValueError("platform target receipt coverage conflicts")
    return rows


def _tiktok_result(
    receipt: object, *, labels: tuple[str, ...]
) -> dict[str, object]:
    rows = _target_rows(receipt, labels=labels)
    statuses: dict[str, str] = {}
    attempted: list[bool] = []
    readback_observed: list[bool] = []
    write_counts: list[int | None] = []
    target_evidence: dict[str, Mapping[str, object] | None] = {}
    for label in labels:
        row = rows[label]
        status = row.get("status")
        if status not in _TARGET_STATUSES:
            raise ValueError("TikTok target status is invalid")
        was_attempted = row.get("dispatch_attempted")
        if type(was_attempted) is not bool:
            raise ValueError("TikTok dispatch evidence is invalid")
        count = row.get("external_write_count")
        if count is not None and (type(count) is not int or count < 0):
            raise ValueError("TikTok write evidence is invalid")
        statuses[label] = str(status)
        target_evidence[label] = _tiktok_evidence(row.get("evidence"), label, str(status))
        attempted.append(was_attempted)
        # A legacy publisher receipt may retain its confirmed SAVE prefix while
        # the following PUBLISH is unknown. Keep that useful target evidence;
        # it cannot be presented as a complete count of platform mutations.
        evidence = target_evidence[label]
        write_counts.append(None if isinstance(evidence, Mapping) and evidence.get("outcome_unknown") is True else count)
        if was_attempted:
            readback_observed.append(row.get("readback_status") != "NOT_ATTEMPTED")
    return _result(
        "TIKTOK",
        labels,
        statuses,
        dispatch_attempted=any(attempted),
        readback_completed=bool(readback_observed) and all(readback_observed),
        external_write_count=(
            sum(count for count in write_counts if count is not None)
            if all(count is not None for count in write_counts)
            else None
        ),
        target_evidence=target_evidence,
    )


def _tiktok_evidence(value: object, label: str, status: str) -> Mapping[str, object] | None:
    if value is None:
        return None
    legacy_fields = set(_TIKTOK_EVIDENCE_FIELDS) - {"provider_field_path"}
    if not isinstance(value, Mapping) or (
        set(value) != set(_TIKTOK_EVIDENCE_FIELDS) and set(value) != legacy_fields
    ):
        raise ValueError("TikTok target evidence fields are invalid")
    if value.get("target_label") != label or value.get("status") != status:
        raise ValueError("TikTok target evidence identity conflicts")
    stage = value.get("stage")
    if stage not in _TIKTOK_EVIDENCE_STAGES:
        raise ValueError("TikTok target evidence stage is invalid")
    attempted, unknown, count = value.get("request_attempted"), value.get("outcome_unknown"), value.get("external_write_count")
    if type(attempted) is not bool or type(unknown) is not bool or (count is not None and (type(count) is not int or count < 0)):
        raise ValueError("TikTok target evidence is invalid")
    result = {"target_label": label, "status": status, "stage": stage, "provider_code": sanitize_tiktok_provider_code(value.get("provider_code")), "provider_reason": sanitize_tiktok_provider_reason(value.get("provider_reason")), "request_attempted": attempted, "outcome_unknown": unknown, "external_write_count": count}
    field_path = sanitize_tiktok_provider_field_path(value.get("provider_field_path"))
    if field_path:
        result["provider_field_path"] = field_path
    return result


def _tiktok_failure_evidence(labels: tuple[str, ...], *, stage: str, provider_code: str, provider_reason: str, request_attempted: bool, outcome_unknown: bool, external_write_count: int | None, provider_field_path: str = "") -> dict[str, Mapping[str, object]]:
    rows = {label: {"target_label": label, "status": "FAILED", "stage": stage, "provider_code": provider_code, "provider_reason": provider_reason, "request_attempted": request_attempted, "outcome_unknown": outcome_unknown, "external_write_count": external_write_count} for label in labels}
    safe_path = sanitize_tiktok_provider_field_path(provider_field_path)
    if safe_path:
        for row in rows.values():
            row["provider_field_path"] = safe_path
    return rows


def _tiktok_preparation_request_attempted(row: Mapping[str, object]) -> bool:
    count = row.get("external_write_count")
    writes = row.get("writes")
    return (
        count is None
        or (type(count) is int and count > 0)
        or (
            isinstance(writes, list)
            and any(
                isinstance(write, Mapping)
                and write.get("operation") != "IDENTITY_OBSERVED"
                for write in writes
            )
        )
    )


def build_tiktok_v4_executor(
    *,
    collectbox_context_resolver: CollectBoxContextResolver | None,
    draft_preparer: TikTokDraftPreparer | None = None,
    category_resolver: TikTokCategoryResolver | None,
    publisher: TikTokTargetPublisher,
    storefront_readback: TikTokStorefrontReadback,
) -> PlatformExecutor:
    """Bind durable TikTok identities and injected I/O to the v4 boundary."""

    sources = sum(
        value is not None
        for value in (collectbox_context_resolver, draft_preparer)
    )
    if sources != 1:
        raise TypeError("TikTok requires exactly one durable context source")
    if collectbox_context_resolver is not None and not callable(
        collectbox_context_resolver
    ):
        raise TypeError("TikTok collect-box context resolver must be callable")
    if draft_preparer is not None and not callable(draft_preparer):
        raise TypeError("TikTok v4 draft preparer must be callable")
    if category_resolver is not None and not callable(
        getattr(category_resolver, "resolve", None)
    ):
        raise TypeError("TikTok category resolver must provide resolve")
    if not callable(getattr(publisher, "preflight", None)) or not callable(
        getattr(publisher, "publish", None)
    ):
        raise TypeError("TikTok publisher must provide preflight and publish")
    if not callable(getattr(storefront_readback, "readback", None)):
        raise TypeError("TikTok storefront readback must provide readback")

    def execute(request: PublicationPlatformRequest) -> Mapping[str, Any]:
        labels, snapshot = _request_facts(request, platform="TIKTOK")
        preparation_write_count: int | None = 0
        accepted_save_targets: tuple[str,...] = ()
        execution_labels = labels
        unprepared: dict[str, dict[str, object]] = {}
        try:
            validate_description_media_preflight(snapshot,platform="TIKTOK",target_labels=labels)
            if draft_preparer is not None:
                preparation = _verified_tiktok_preparation(
                    draft_preparer(request),
                    request=request,
                    labels=labels,
                )
                contexts = preparation["collectbox_contexts"]
                preparation_write_count = preparation["external_write_count"]
                accepted_save_targets=tuple(row["target_label"] for row in preparation["targets"]
                    if row.get("status")=="PREPARED" and row.get("reason_code")=="DRAFT_SAVED")
                execution_labels = tuple(label for label in labels if label in accepted_save_targets)
                for row in preparation["targets"]:
                    label = row["target_label"]
                    if label in execution_labels:
                        continue
                    unknown = row.get("status") == "UNKNOWN" or row.get("external_write_count") is None
                    status = "PROCESSING" if unknown else "FAILED"
                    count = row.get("external_write_count")
                    unprepared[label] = {
                        "target_label": label, "status": status,
                        "dispatch_attempted": False, "readback_status": "NOT_ATTEMPTED",
                        # The complete preparation count is added once below.
                        "external_write_count": 0,
                        "evidence": _tiktok_failure_evidence(
                            (label,), stage="PREPARATION",
                            provider_code=("tiktok_preparation_unknown" if unknown else row.get("provider_code") or "tiktok_preparation_failed"),
                            provider_field_path=str(row.get("provider_field_path") or ""),
                            provider_reason=str(row.get("provider_reason") or "Preparation did not produce an accepted SAVE; publish was not sent"),
                            request_attempted=_tiktok_preparation_request_attempted(row),
                            outcome_unknown=unknown, external_write_count=count,
                        )[label],
                    }
                    unprepared[label]["evidence"]["status"] = status
                contexts = {label: context for label, context in contexts.items() if label in execution_labels}
            else:
                assert collectbox_context_resolver is not None
                contexts = collectbox_context_resolver(request)
            if not isinstance(contexts, Mapping):
                raise TypeError("TikTok durable contexts must be a mapping")
            contexts = {
                label: context
                for label, context in contexts.items()
                if label in execution_labels
            }
            if not execution_labels:
                result = _tiktok_result({"targets": list(unprepared.values())}, labels=labels)
                result["external_write_count"] = preparation_write_count
                result["requires_human_action"] = True
                return result
            plan = project_tiktok_v4_execution_plan(
                snapshot,
                collectbox_contexts=contexts,
                category_resolver=category_resolver,
                target_scope=execution_labels,
            )
        except Exception:
            if draft_preparer is not None:
                count_getter = getattr(draft_preparer, "write_count", None)
                if callable(count_getter):
                    try:
                        preparation_write_count = count_getter(request)
                    except Exception:
                        preparation_write_count = None
            return _result(
                "TIKTOK",
                labels,
                {label: "FAILED" for label in labels},
                dispatch_attempted=False,
                readback_completed=False,
                external_write_count=preparation_write_count,
                requires_human_action=True,
                target_evidence=_tiktok_failure_evidence(labels, stage="PREPARATION", provider_code="tiktok_preparation_failed", provider_reason="TikTok local preparation failed", request_attempted=preparation_write_count is None or preparation_write_count > 0, outcome_unknown=preparation_write_count is None, external_write_count=preparation_write_count),
            )
        try:
            execution_kwargs = {}
            if request.write_budget_ledger is not None:
                execution_kwargs["before_publish"] = lambda label: request.write_budget_ledger.reserve_target(label,"publish_target")
            if accepted_save_targets:
                execution_kwargs["accepted_save_targets"] = accepted_save_targets
            receipt = execute_tiktok_v4_plan(
                plan,
                publisher=publisher,
                storefront_readback=storefront_readback,
                **execution_kwargs,
            )
            # Official TikTok rows are optional because the currently composed
            # live dependency truthfully has no storefront GET.  When a future
            # official reader supplies rows, the durable sink owns all local
            # projection and rejects any identity that is not frozen here.
            observations = [
                row
                for target in receipt.get("targets", [])
                if isinstance(target, Mapping)
                and target.get("status") == "PUBLISHED"
                and isinstance(target.get("official_catalog_rows"), list)
                for row in target["official_catalog_rows"]
            ]
            if observations:
                from shared_platform.catalog_publication_sync import capture_readback

                capture_readback(request, observations)
            if unprepared:
                receipt = {**receipt, "targets": [*receipt["targets"], *unprepared.values()]}
            result = _tiktok_result(receipt, labels=labels)
            if unprepared:
                result["requires_human_action"] = True
            publish_count = result["external_write_count"]
            result["external_write_count"] = (
                preparation_write_count + publish_count
                if preparation_write_count is not None
                and publish_count is not None
                else None
            )
            return result
        except Exception:
            # An unexpected failure after entering execution cannot prove that
            # a transport did not receive a request.
            return _result("TIKTOK", labels, {label: "FAILED" for label in labels}, dispatch_attempted=True, readback_completed=False, external_write_count=None, requires_human_action=True, target_evidence=_tiktok_failure_evidence(labels, stage="EXECUTION", provider_code="transport_unknown", provider_reason="TikTok execution outcome is unknown", request_attempted=True, outcome_unknown=True, external_write_count=None))

    return execute


def _verified_tiktok_preparation(
    value: object,
    *,
    request: PublicationPlatformRequest,
    labels: tuple[str, ...],
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("TikTok v4 draft preparation receipt is invalid")
    receipt = dict(value)
    supplied_digest = receipt.pop("receipt_digest", None)
    canonical = json.dumps(
        receipt,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    expected_digest = "sha256:" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    if supplied_digest != expected_digest:
        raise ValueError("TikTok v4 draft preparation receipt drifted")
    expected_keys = {
        "schema_version",
        "snapshot_digest",
        "plan_id",
        "offer_id",
        "product_revision",
        "targets",
        "collectbox_contexts",
        "external_write_count",
        "publish_invoked",
        "status",
    }
    snapshot = request.snapshot
    targets = receipt.get("targets")
    contexts = receipt.get("collectbox_contexts")
    count = receipt.get("external_write_count")
    if (
        set(receipt) != expected_keys
        or receipt.get("schema_version")
        != "miaoshou-tiktok-v4-draft-preparation/v1"
        or receipt.get("snapshot_digest") != snapshot.get("snapshot_digest")
        or receipt.get("plan_id") != snapshot.get("plan_id")
        or receipt.get("offer_id") != snapshot.get("offer_id")
        or receipt.get("product_revision") != snapshot.get("product_revision")
        or receipt.get("publish_invoked") is not False
        or receipt.get("status")
        not in {"PREPARED", "PARTIAL", "FAILED", "UNKNOWN"}
        or not isinstance(targets, list)
        or len(targets) != len(labels)
        or any(not isinstance(row, Mapping) for row in targets)
        or {row.get("target_label") for row in targets} != set(labels)
        or not isinstance(contexts, Mapping)
        or not set(contexts).issubset(labels)
        or (count is not None and (type(count) is not int or count < 0))
    ):
        raise ValueError("TikTok v4 draft preparation receipt conflicts")
    receipt["receipt_digest"] = supplied_digest
    return receipt


def _synthetic_unknown_shopee_dispatch(
    labels: tuple[str, ...]
) -> dict[str, object]:
    return {
        "schema_version": "shopee-regional-dispatch/v1",
        "platform": "shopee",
        "target_count": len(labels),
        "targets": [
            {
                "target_label": label,
                "attempted": True,
                "accepted": False,
                "outcome": "UNKNOWN",
            }
            for label in labels
        ],
    }


def _shopee_status(
    dispatch: Mapping[str, object], readback: Mapping[str, object]
) -> str:
    dispatch_outcome = str(dispatch.get("outcome") or "").upper()
    readback_outcome = str(readback.get("outcome") or "").upper()
    if readback_outcome == "PUBLISHED" and dispatch.get("accepted") is True:
        return "PUBLISHED"
    if readback_outcome == "PROCESSING" and dispatch.get("accepted") is True:
        return "PROCESSING"
    if readback_outcome in {"UNKNOWN", "NOT_DISPATCHED"} and dispatch_outcome in {
        "ACCEPTED",
        "UNKNOWN",
    }:
        return "PROCESSING"
    return "FAILED"


def _shopee_provider_identity_bound(*rows: Mapping[str, object]) -> bool:
    """Require an explicit safe provider identity, never a truthy placeholder."""

    def valid(value: object) -> bool:
        text = str(value or "").strip()
        return bool(text) and text.isascii() and len(text) <= 255 and all(
            character.isalnum() or character in "._:-" for character in text
        )

    for row in rows:
        if any(
            valid(row.get(key))
            for key in (
                "provider_task_id", "existing_item_id", "item_id", "model_id",
                "global_item_id",
            )
        ):
            return True
        catalog_rows = row.get("official_catalog_rows")
        if isinstance(catalog_rows, list):
            for catalog_row in catalog_rows:
                identity = (
                    catalog_row.get("identity")
                    if isinstance(catalog_row, Mapping)
                    else None
                )
                if isinstance(identity, Mapping) and any(
                    valid(identity.get(key))
                    for key in ("product_id", "variant_id")
                ):
                    return True
    return False


def _shopee_result(
    dispatch: object,
    readback: object | None,
    *,
    labels: tuple[str, ...],
    readback_completed: bool,
    prior_external_write_count: int | None = 0,
    include_target_evidence: bool = False,
) -> dict[str, object]:
    dispatch_rows = _target_rows(dispatch, labels=labels)
    if readback_completed:
        readback_rows = _target_rows(readback, labels=labels)
    else:
        readback_rows = {
            label: {"target_label": label, "outcome": "UNKNOWN"}
            for label in labels
        }
    statuses = {
        label: _shopee_status(dispatch_rows[label], readback_rows[label])
        for label in labels
    }
    target_evidence: dict[str, Mapping[str, object]] = {}
    attempted: list[bool] = []
    accepted_count = 0
    unknown_write = False
    for label in labels:
        row = dispatch_rows[label]
        row_attempted = row.get("attempted")
        row_accepted = row.get("accepted")
        if type(row_attempted) is not bool or type(row_accepted) is not bool:
            raise ValueError("Shopee dispatch evidence is invalid")
        outcome = str(row.get("outcome") or "").upper()
        if not outcome:
            raise ValueError("Shopee dispatch outcome is invalid")
        attempted.append(row_attempted)
        row_write_count = row.get("external_write_count")
        if row_write_count is None:
            if "external_write_count" in row:
                unknown_write = True
            else:
                accepted_count += row_accepted
        elif type(row_write_count) is int and row_write_count >= 0:
            accepted_count += row_write_count
        else:
            raise ValueError("Shopee dispatch write count is invalid")
        if readback_completed:
            readback_write_count = readback_rows[label].get(
                "external_write_count", 0
            )
            if readback_write_count is None:
                unknown_write = True
            elif type(readback_write_count) is int and readback_write_count >= 0:
                accepted_count += readback_write_count
            else:
                raise ValueError("Shopee readback write count is invalid")
        unknown_write = unknown_write or outcome == "UNKNOWN"
        readback_row = readback_rows[label]
        readback_outcome = str(readback_row.get("outcome") or "UNKNOWN").upper()
        status = statuses[label]
        provider_code = str(readback_row.get("provider_code") or row.get("provider_code") or "").strip()
        if provider_code and (
            not provider_code.isascii()
            or any(not (character.isalnum() or character in "_-.:"
                        ) for character in provider_code)
        ):
            provider_code = "shopee_provider_error"
        if not provider_code:
            provider_code = {
                "PUBLISHED": "shopee_official_readback_verified",
                "PROCESSING": "shopee_provider_processing",
                "FAILED": "shopee_target_failed",
            }[status]
        provider_reason = {
            "PUBLISHED": "Shopee official readback matched the approved target",
            "PROCESSING": "Shopee accepted the target but final official readback is pending",
            "FAILED": "Shopee target failed during dispatch or official readback",
        }[status]
        target_count = row_write_count
        if readback_completed:
            rb_count = readback_row.get("external_write_count", 0)
            target_count = (
                target_count + rb_count
                if type(target_count) is int and type(rb_count) is int
                else None
            )
        target_evidence[label] = {
            "target_label": label,
            "status": status,
            "stage": "READBACK" if readback_completed else "DISPATCH",
            "provider_code": provider_code[:80],
            "provider_reason": provider_reason,
            "request_attempted": bool(
                row_attempted
                or readback_row.get("attempted") is True
                or target_count is None
                or (type(target_count) is int and target_count > 0)
            ),
            "outcome_unknown": outcome == "UNKNOWN" or readback_outcome == "UNKNOWN",
            "external_write_count": target_count,
            "provider_identity_bound": _shopee_provider_identity_bound(
                row, readback_row
            ),
        }
    return _result(
        "SHOPEE",
        labels,
        statuses,
        dispatch_attempted=any(attempted),
        readback_completed=readback_completed,
        external_write_count=(
            None
            if unknown_write or prior_external_write_count is None
            else prior_external_write_count + accepted_count
        ),
        target_evidence=target_evidence if include_target_evidence else None,
    )


def _shopee_resolver_write_count(
    resolver: ShopeeGlobalItemIdResolver,
    request: PublicationPlatformRequest,
) -> int | None:
    observer = getattr(resolver, "write_count", None)
    if not callable(observer):
        return 0
    value = observer(request)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("Shopee global resolver write count is invalid")
    return value


def build_shopee_region_executor(
    *,
    global_item_id_resolver: ShopeeGlobalItemIdResolver,
    runtime: ShopeeRegionRuntime,
    poll_attempts: int = 3,
) -> PlatformExecutor:
    """Bind an existing global product and injected runtime to regional Skill I/O."""

    if not callable(global_item_id_resolver):
        raise TypeError("Shopee global item resolver must be callable")
    if type(poll_attempts) is not int or not 1 <= poll_attempts <= 10:
        raise ValueError("Shopee poll attempts must be between 1 and 10")

    def execute(request: PublicationPlatformRequest) -> Mapping[str, Any]:
        labels, snapshot = _request_facts(request, platform="SHOPEE")
        checkpoint = None
        if request.checkpoint_root is not None:
            from shared_platform.shopee_publication_checkpoint import (
                ShopeePublicationCheckpointStore,
            )

            checkpoint = ShopeePublicationCheckpointStore(
                request.checkpoint_root, request
            )
        try:
            validate_description_media_preflight(snapshot,platform="SHOPEE",target_labels=labels)
            if tuple(selected_region_targets(snapshot,target_scope=labels)) != labels:
                raise ValueError("Shopee selected target order conflicts")
        except (TypeError, ValueError) as error:
            return _safe_shopee_preparation_failure(labels, error)
        regional_kwargs = {"target_scope":labels}
        if checkpoint is not None:
            regional_kwargs["on_target_result"] = checkpoint.record
        if request.write_budget_ledger is not None:
            regional_kwargs["before_mutation"] = lambda label,operation: request.write_budget_ledger.reserve_target(label,operation)
        try:
            global_item_id = global_item_id_resolver(request)
        except Exception as error:
            try:
                global_write_count = _shopee_resolver_write_count(
                    global_item_id_resolver, request
                )
            except Exception:
                global_write_count = None
            if global_write_count == 0:
                return _safe_shopee_preparation_failure(labels, error)
            return _result(
                "SHOPEE",
                labels,
                {label: "FAILED" for label in labels},
                dispatch_attempted=True,
                readback_completed=False,
                external_write_count=global_write_count,
                requires_human_action=True,
            )
        try:
            global_write_count = _shopee_resolver_write_count(
                global_item_id_resolver, request
            )
        except Exception:
            global_write_count = None

        try:
            if request.catalog_sink is not None:
                request.catalog_sink.prepare_shopee(request,global_item_id,runtime)
        except Exception:
            return _result('SHOPEE',labels,{label:'FAILED' for label in labels},dispatch_attempted=global_write_count!=0,readback_completed=False,external_write_count=global_write_count,requires_human_action=True)
        try:
            dispatch = dispatch_selected_regions(
                snapshot,
                global_item_id=global_item_id,
                runtime=runtime,
                **regional_kwargs,
            )
            _target_rows(dispatch, labels=labels)
        except Exception:
            dispatch = _synthetic_unknown_shopee_dispatch(labels)

        if request.catalog_sink is not None:
            try:
                request.catalog_sink.record_shopee_dispatch(request,dispatch)
            except Exception:
                request.catalog_sink.failures.append({'run_id':request.run_id,'code':'CATALOG_QUERY_IDENTITY_PERSISTENCE_FAILED'})

        # Readback runs after every entry into the dispatch boundary, including
        # an ambiguous transport outcome.  It is read-only and prevents unsafe
        # automatic resubmission when the write result is unknown.
        try:
            readback = readback_dispatched_regions(
                snapshot,
                dispatch,
                global_item_id=global_item_id,
                runtime=runtime,
                poll_attempts=poll_attempts,
                **regional_kwargs,
            )
            from shared_platform.catalog_publication_sync import capture_readback
            observations = [item for target in readback.get("targets", [])
                            for item in target.get("official_catalog_rows", [])]
            capture_readback(request, observations or [{"authority": "UNAVAILABLE", "verified": False}])
            return _shopee_result(
                dispatch,
                readback,
                labels=labels,
                readback_completed=True,
                prior_external_write_count=global_write_count,
                include_target_evidence=checkpoint is not None,
            )
        except Exception:
            return _shopee_result(
                dispatch,
                None,
                labels=labels,
                readback_completed=False,
                prior_external_write_count=global_write_count,
                include_target_evidence=checkpoint is not None,
            )

    return execute


def build_product_publication_platform_executors(
    *,
    platform_scope: Sequence[str],
    tiktok: TikTokV4ExecutorDependencies | None = None,
    shopee: ShopeeRegionExecutorDependencies | None = None,
    ozon: OzonV4ExecutorDependencies | None = None,
) -> dict[str, PlatformExecutor]:
    """Build the exact executor mapping required by ``ProductPublicationRunner``."""

    if isinstance(platform_scope, (str, bytes, bytearray)) or not isinstance(
        platform_scope, Sequence
    ):
        raise TypeError("platform_scope must be a sequence")
    requested = list(platform_scope)
    if (
        not requested
        or any(type(platform) is not str for platform in requested)
        or len(requested) != len(set(requested))
        or any(platform not in _PLATFORM_ORDER for platform in requested)
    ):
        raise ValueError("platform_scope is invalid")
    selected = set(requested)
    result: dict[str, PlatformExecutor] = {}
    if "TIKTOK" in selected:
        if not isinstance(tiktok, TikTokV4ExecutorDependencies):
            raise TypeError("TikTok executor dependencies are required")
        result["TIKTOK"] = build_tiktok_v4_executor(
            collectbox_context_resolver=tiktok.collectbox_context_resolver,
            draft_preparer=tiktok.draft_preparer,
            category_resolver=tiktok.category_resolver,
            publisher=tiktok.publisher,
            storefront_readback=tiktok.storefront_readback,
        )
    if "SHOPEE" in selected:
        if not isinstance(shopee, ShopeeRegionExecutorDependencies):
            raise TypeError("Shopee executor dependencies are required")
        result["SHOPEE"] = build_shopee_region_executor(
            global_item_id_resolver=shopee.global_item_id_resolver,
            runtime=shopee.runtime,
            poll_attempts=shopee.poll_attempts,
        )
    if "OZON" in selected:
        if not isinstance(ozon, OzonV4ExecutorDependencies):
            raise TypeError("Ozon executor dependencies are required")
        result["OZON"] = build_ozon_v4_executor(
            dispatch_variant=ozon.dispatch_variant,
            readback_variants=ozon.readback_variants,
            official_profile_resolver=ozon.official_profile_resolver,
            localized_copy_resolver=ozon.localized_copy_resolver,
            update_stocks=ozon.update_stocks,
            readback_stocks=ozon.readback_stocks,
            prepare_stock_update=ozon.prepare_stock_update,
            catalog_account_resolver=ozon.catalog_account_resolver,
            catalog_observer=ozon.catalog_observer,
        )
    return result


__all__ = [
    "CollectBoxContextResolver",
    "OzonV4ExecutorDependencies",
    "ShopeeGlobalItemIdResolver",
    "ShopeeRegionExecutorDependencies",
    "TikTokV4ExecutorDependencies",
    "TikTokDraftPreparer",
    "build_product_publication_platform_executors",
    "build_shopee_region_executor",
    "build_tiktok_v4_executor",
]
