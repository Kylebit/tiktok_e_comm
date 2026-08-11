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
from modules.ozon.approved_publication_v4 import build_ozon_v4_executor
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


_PLATFORM_ORDER = ("TIKTOK", "SHOPEE", "OZON")
_TARGET_STATUSES = frozenset({"PUBLISHED", "PROCESSING", "FAILED"})

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
    return {
        "schema_version": PLATFORM_RESULT_SCHEMA_VERSION,
        "platform": platform,
        "targets": [
            {"target_label": label, "status": statuses[label]} for label in labels
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
        attempted.append(was_attempted)
        write_counts.append(count)
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
        try:
            if draft_preparer is not None:
                preparation = _verified_tiktok_preparation(
                    draft_preparer(request),
                    request=request,
                    labels=labels,
                )
                contexts = preparation["collectbox_contexts"]
                preparation_write_count = preparation["external_write_count"]
            else:
                assert collectbox_context_resolver is not None
                contexts = collectbox_context_resolver(request)
            if not isinstance(contexts, Mapping):
                raise TypeError("TikTok durable contexts must be a mapping")
            plan = project_tiktok_v4_execution_plan(
                snapshot,
                collectbox_contexts=contexts,
                category_resolver=category_resolver,
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
            )
        try:
            receipt = execute_tiktok_v4_plan(
                plan,
                publisher=publisher,
                storefront_readback=storefront_readback,
            )
            result = _tiktok_result(receipt, labels=labels)
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
            return _unknown_execution_failure("TIKTOK", labels)

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


def _shopee_result(
    dispatch: object,
    readback: object | None,
    *,
    labels: tuple[str, ...],
    readback_completed: bool,
    prior_external_write_count: int | None = 0,
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
        if tuple(selected_region_targets(snapshot)) != labels:
            return _zero_write_failure("SHOPEE", labels)
        try:
            global_item_id = global_item_id_resolver(request)
        except Exception:
            try:
                global_write_count = _shopee_resolver_write_count(
                    global_item_id_resolver, request
                )
            except Exception:
                global_write_count = None
            if global_write_count == 0:
                return _zero_write_failure("SHOPEE", labels)
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
            dispatch = dispatch_selected_regions(
                snapshot,
                global_item_id=global_item_id,
                runtime=runtime,
            )
            _target_rows(dispatch, labels=labels)
        except Exception:
            dispatch = _synthetic_unknown_shopee_dispatch(labels)

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
            )
            return _shopee_result(
                dispatch,
                readback,
                labels=labels,
                readback_completed=True,
                prior_external_write_count=global_write_count,
            )
        except Exception:
            return _shopee_result(
                dispatch,
                None,
                labels=labels,
                readback_completed=False,
                prior_external_write_count=global_write_count,
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
