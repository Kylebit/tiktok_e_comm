"""Prepare exact Miaoshou TikTok drafts from one frozen v4 snapshot.

This boundary deliberately does not accept a ReleasePlan payload.  Product,
SKU, price, parcel, image and target facts come only from the validated
``approved-publication-snapshot/v4`` document.  The injected transport owns
the provider mutations and the injected resolver owns deferred official
category decisions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from typing import Callable, Protocol

from domains.product_operations.approved_publication_snapshot import (
    ApprovedPublicationSnapshotError,
    validate_approved_publication_snapshot,
)
from modules.miaoshou.client import MiaoshouBusinessRejectedError, post_open
from modules.miaoshou.tiktok_publisher import EXPECTED_SHOP_ID_BY_TARGET
from shared_platform.collectbox_action import CollectBoxTargetDetailIdentity


PREPARATION_SCHEMA_VERSION = "miaoshou-tiktok-v4-draft-preparation/v1"
CONTEXT_SCHEMA_VERSION = "collectbox-tiktok-v4-publish-context/v1"
DRAFT_SCHEMA_VERSION = "miaoshou-tiktok-v4-draft/v1"

DETAIL_CREATE_PATH = (
    "/open/v1/product/common_collect_box/common_collect_box/claimed"
)
SHOP_CLAIM_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/claim_to_shop"
)
SAVE_SITE_DRAFT_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/"
    "save_site_collect_item_info"
)
SAVE_SHOP_DRAFT_PATH = (
    "/open/v1/product/collect_box/tiktok/collect_box/"
    "save_shop_collect_item_info"
)
_SITE_DRAFT_SITES = frozenset({"PH", "MY", "TH", "VN"})
_OUTCOMES = frozenset({"ACCEPTED", "REJECTED", "UNKNOWN"})
_OPERATIONS = frozenset({"CREATE_DRAFT", "CLAIM_TO_SHOP", "CLAIM_OR_CREATE", "SAVE_DRAFT"})


class TikTokV4DraftPreparationError(ValueError):
    """The immutable v4 preparation contract failed before provider work."""


@dataclass(frozen=True)
class DraftWriteFact:
    """One provider-mutation truth, optionally carrying an exact identity."""

    operation: str
    outcome: str
    detail_id: str | None = None
    shop_id: str | None = None

    def __post_init__(self) -> None:
        if self.operation not in _OPERATIONS:
            raise ValueError("TikTok draft write operation is invalid")
        if self.outcome not in _OUTCOMES:
            raise ValueError("TikTok draft write outcome is invalid")
        detail_id = _optional_positive_id(self.detail_id, "detail_id")
        shop_id = _optional_positive_id(self.shop_id, "shop_id")
        if (detail_id is None) != (shop_id is None):
            raise ValueError("TikTok draft write identity is incomplete")
        object.__setattr__(self, "detail_id", detail_id)
        object.__setattr__(self, "shop_id", shop_id)

    def public_fact(self) -> dict[str, str]:
        return {"operation": self.operation, "outcome": self.outcome}


class TikTokCategoryResolver(Protocol):
    def resolve(
        self,
        *,
        target: dict[str, str],
        product: dict[str, object],
        skus: list[dict[str, object]],
    ) -> Mapping[str, object] | None: ...


class TikTokV4DraftTransport(Protocol):
    def claim_or_create(
        self, *, target: Mapping[str, object], ordinal: int
    ) -> DraftWriteFact | Sequence[DraftWriteFact]: ...

    def save_draft(
        self,
        *,
        identity: Mapping[str, str],
        draft: Mapping[str, object],
    ) -> DraftWriteFact: ...


def prepare_tiktok_v4_drafts(
    snapshot: Mapping[str, object],
    *,
    category_resolver: TikTokCategoryResolver | None,
    transport: TikTokV4DraftTransport,
) -> dict[str, object]:
    """Claim/create and save every selected TikTok target independently."""

    try:
        frozen = validate_approved_publication_snapshot(snapshot).payload()
    except (ApprovedPublicationSnapshotError, TypeError, ValueError) as error:
        raise TikTokV4DraftPreparationError(
            f"approved-publication-snapshot/v4 is invalid: {error}"
        ) from None
    if not callable(getattr(transport, "claim_or_create", None)) or not callable(
        getattr(transport, "save_draft", None)
    ):
        raise TikTokV4DraftPreparationError("TikTok draft transport is invalid")

    selected = [
        row for row in frozen["publication_targets"] if row["platform"] == "tiktok"
    ]
    if not selected:
        raise TikTokV4DraftPreparationError("snapshot selects no TikTok targets")

    target_results: list[dict[str, object]] = []
    contexts: dict[str, dict[str, object]] = {}
    all_writes: list[DraftWriteFact] = []
    for ordinal, target in enumerate(selected):
        label = target["target_label"]
        shop_id = EXPECTED_SHOP_ID_BY_TARGET.get(label)
        if shop_id is None:
            target_results.append(_local_failure(label, "TARGET_UNSUPPORTED"))
            continue
        try:
            category = _target_category(
                frozen,
                target=target,
                resolver=category_resolver,
            )
        except Exception:
            target_results.append(_local_failure(label, "CATEGORY_UNAVAILABLE"))
            continue

        provider_target = deepcopy(dict(target))
        provider_target["shop_id"] = shop_id
        draft = _draft_payload(
            frozen,
            target=target,
            category=category,
        )
        target_writes: list[DraftWriteFact] = []
        try:
            raw_claim = transport.claim_or_create(
                target=deepcopy(provider_target),
                ordinal=ordinal,
            )
            claim_facts = _claim_facts(raw_claim, shop_id=shop_id)
        except Exception:
            claim_facts = (DraftWriteFact("CLAIM_OR_CREATE", "UNKNOWN"),)
        target_writes.extend(claim_facts)
        all_writes.extend(claim_facts)
        try:
            identity = _last_identity(claim_facts, label=label, shop_id=shop_id)
        except TikTokV4DraftPreparationError:
            target_results.append(
                _target_result(
                    label,
                    status="UNKNOWN",
                    reason_code="CLAIM_IDENTITY_AMBIGUOUS",
                    writes=target_writes,
                )
            )
            continue
        if identity is not None:
            contexts[label] = _collectbox_context(frozen, identity=identity)

        claim_outcome = _combined_outcome(claim_facts)
        if claim_outcome != "ACCEPTED" or identity is None:
            target_results.append(
                _target_result(
                    label,
                    status="UNKNOWN" if claim_outcome == "UNKNOWN" else "FAILED",
                    reason_code=(
                        "CLAIM_OUTCOME_UNKNOWN"
                        if claim_outcome == "UNKNOWN"
                        else "CLAIM_REJECTED"
                    ),
                    writes=target_writes,
                )
            )
            continue

        try:
            raw_save = transport.save_draft(
                identity=deepcopy(identity),
                draft=deepcopy(draft),
            )
            save = _save_fact(raw_save, identity=identity)
        except MiaoshouBusinessRejectedError:
            save = DraftWriteFact(
                "SAVE_DRAFT",
                "REJECTED",
                detail_id=identity["detail_id"],
                shop_id=identity["shop_id"],
            )
        except Exception:
            save = DraftWriteFact(
                "SAVE_DRAFT",
                "UNKNOWN",
                detail_id=identity["detail_id"],
                shop_id=identity["shop_id"],
            )
        target_writes.append(save)
        all_writes.append(save)
        status = {
            "ACCEPTED": "PREPARED",
            "REJECTED": "FAILED",
            "UNKNOWN": "UNKNOWN",
        }[save.outcome]
        reason = {
            "ACCEPTED": "DRAFT_SAVED",
            "REJECTED": "SAVE_REJECTED",
            "UNKNOWN": "SAVE_OUTCOME_UNKNOWN",
        }[save.outcome]
        target_results.append(
            _target_result(
                label,
                status=status,
                reason_code=reason,
                writes=target_writes,
            )
        )

    result: dict[str, object] = {
        "schema_version": PREPARATION_SCHEMA_VERSION,
        "snapshot_digest": frozen["snapshot_digest"],
        "plan_id": frozen["plan_id"],
        "offer_id": frozen["offer_id"],
        "product_revision": frozen["product_revision"],
        "targets": target_results,
        "collectbox_contexts": contexts,
        "external_write_count": _write_count(all_writes),
        "publish_invoked": False,
        "status": (
            "PREPARED"
            if all(row["status"] == "PREPARED" for row in target_results)
            else "PARTIAL"
            if any(row["status"] == "PREPARED" for row in target_results)
            else "UNKNOWN"
            if any(row["status"] == "UNKNOWN" for row in target_results)
            else "FAILED"
        ),
    }
    result["receipt_digest"] = "sha256:" + _digest(result)
    return result


def _target_category(
    frozen: Mapping[str, object],
    *,
    target: Mapping[str, str],
    resolver: TikTokCategoryResolver | None,
) -> dict[str, object]:
    row = frozen["categories_by_target"][target["target_label"]]
    decision = row["decision"]
    if decision["status"] == "APPROVED":
        return _category(row["category"])
    if decision["status"] != "DEFERRED_TO_SKILL" or row["category"] is not None:
        raise TikTokV4DraftPreparationError("TikTok category decision is invalid")
    if resolver is None:
        raise TikTokV4DraftPreparationError("TikTok category resolver is unavailable")
    resolved = resolver.resolve(
        target=deepcopy(dict(target)),
        product=deepcopy(dict(frozen["product"])),
        skus=deepcopy(list(frozen["skus"])),
    )
    if not isinstance(resolved, Mapping):
        raise TikTokV4DraftPreparationError("TikTok category is unavailable")
    value = resolved.get("category") if "category" in resolved else resolved
    if "target_label" in resolved and resolved.get("target_label") != target["target_label"]:
        raise TikTokV4DraftPreparationError("TikTok category target drifted")
    if "enabled" in resolved and resolved.get("enabled") is not True:
        raise TikTokV4DraftPreparationError("TikTok category is disabled")
    if "metadata_valid" in resolved and resolved.get("metadata_valid") is not True:
        raise TikTokV4DraftPreparationError("TikTok category metadata is invalid")
    return _category(value)


def _category(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {"id", "name", "path"}:
        raise TikTokV4DraftPreparationError("TikTok category is malformed")
    category_id = value.get("id")
    name = value.get("name")
    path = value.get("path")
    if (
        type(category_id) is not str
        or not category_id.isascii()
        or not category_id.isdigit()
        or int(category_id) <= 0
        or type(name) is not str
        or not name.strip()
        or not isinstance(path, list)
        or not path
        or any(
            not isinstance(row, Mapping)
            or set(row) != {"id", "name"}
            or type(row.get("id")) is not str
            or not row["id"]
            or type(row.get("name")) is not str
            or not row["name"].strip()
            for row in path
        )
    ):
        raise TikTokV4DraftPreparationError("TikTok category is invalid")
    normalized_path = [dict(row) for row in path]
    if normalized_path[-1] != {"id": category_id, "name": name}:
        raise TikTokV4DraftPreparationError("TikTok category path drifted")
    return {"id": category_id, "name": name, "path": normalized_path}


def _draft_payload(
    frozen: Mapping[str, object],
    *,
    target: Mapping[str, str],
    category: Mapping[str, object],
) -> dict[str, object]:
    label = target["target_label"]
    skus = [
        {
            "variant_key": row["variant_key"],
            "seller_sku": row["seller_sku"],
            "model_sku": row["model_sku"],
            "specification": deepcopy(row["specification"]),
            "price": row["prices"][label]["amount"],
            "currency": row["prices"][label]["currency"],
            "parcel": deepcopy(row["parcel"]),
            "images": deepcopy(row["variant_images"]),
        }
        for row in frozen["skus"]
    ]
    parent = _parent_parcel(skus)
    return {
        "schema_version": DRAFT_SCHEMA_VERSION,
        "snapshot_digest": frozen["snapshot_digest"],
        "plan_id": frozen["plan_id"],
        "offer_id": frozen["offer_id"],
        "product_revision": frozen["product_revision"],
        "target_label": label,
        "title": frozen["product"]["title"],
        "description": frozen["product"]["description"],
        "images": deepcopy(frozen["product"]["images"]),
        "category": deepcopy(dict(category)),
        "parent_parcel": parent,
        "skus": skus,
    }


def _parent_parcel(skus: Sequence[Mapping[str, object]]) -> dict[str, object]:
    weights = [Decimal(str(row["parcel"]["weight_kg"])) for row in skus]
    dimensions = [
        [Decimal(str(value)) for value in row["parcel"]["package_cm"]]
        for row in skus
    ]
    return {
        "weight_kg": _decimal_text(max(weights)),
        "package_cm": [
            _decimal_text(max(row[index] for row in dimensions))
            for index in range(3)
        ],
    }


def _claim_facts(
    value: DraftWriteFact | Sequence[DraftWriteFact], *, shop_id: str
) -> tuple[DraftWriteFact, ...]:
    facts = (value,) if type(value) is DraftWriteFact else tuple(value)
    if not facts or any(type(row) is not DraftWriteFact for row in facts):
        raise TypeError("TikTok claim transport returned invalid facts")
    claim_operations = {"CREATE_DRAFT", "CLAIM_TO_SHOP", "CLAIM_OR_CREATE"}
    if any(row.operation not in claim_operations for row in facts):
        raise ValueError("TikTok claim transport operation drifted")
    for row in facts:
        if row.shop_id is not None and row.shop_id != shop_id:
            raise ValueError("TikTok claim transport shop identity drifted")
    return facts


def _save_fact(value: object, *, identity: Mapping[str, str]) -> DraftWriteFact:
    if type(value) is not DraftWriteFact or value.operation != "SAVE_DRAFT":
        raise TypeError("TikTok save transport returned an invalid fact")
    if value.detail_id != identity["detail_id"] or value.shop_id != identity["shop_id"]:
        raise ValueError("TikTok save transport identity drifted")
    return value


def _last_identity(
    facts: Sequence[DraftWriteFact], *, label: str, shop_id: str
) -> dict[str, str] | None:
    identities = [
        (row.detail_id, row.shop_id)
        for row in facts
        if row.detail_id is not None
    ]
    if not identities:
        return None
    if len(set(identities)) != 1 or identities[-1][1] != shop_id:
        raise TikTokV4DraftPreparationError("TikTok draft identity is ambiguous")
    return {
        "target_label": label,
        "detail_id": str(identities[-1][0]),
        "shop_id": shop_id,
    }


def _combined_outcome(facts: Sequence[DraftWriteFact]) -> str:
    if any(row.outcome == "UNKNOWN" for row in facts):
        return "UNKNOWN"
    if any(row.outcome == "REJECTED" for row in facts):
        return "REJECTED"
    return "ACCEPTED"


def _collectbox_context(
    frozen: Mapping[str, object], *, identity: Mapping[str, str]
) -> dict[str, object]:
    target_identity = CollectBoxTargetDetailIdentity(
        target_label=identity["target_label"],
        detail_id=identity["detail_id"],
        shop_id=identity["shop_id"],
    ).internal_payload()
    context: dict[str, object] = {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "snapshot_digest": frozen["snapshot_digest"],
        "plan_id": frozen["plan_id"],
        "offer_id": frozen["offer_id"],
        "product_revision": frozen["product_revision"],
        "release_payload_digest": frozen["bindings"]["release_payload_digest"],
        "target_detail_identity": target_identity,
    }
    context["context_digest"] = "sha256:" + _digest(context)
    return context


def _target_result(
    label: str,
    *,
    status: str,
    reason_code: str,
    writes: Sequence[DraftWriteFact],
) -> dict[str, object]:
    return {
        "target_label": label,
        "status": status,
        "reason_code": reason_code,
        "writes": [row.public_fact() for row in writes],
        "external_write_count": _write_count(writes),
    }


def _local_failure(label: str, reason_code: str) -> dict[str, object]:
    return {
        "target_label": label,
        "status": "FAILED",
        "reason_code": reason_code,
        "writes": [],
        "external_write_count": 0,
    }


def _write_count(facts: Sequence[DraftWriteFact]) -> int | None:
    if any(row.outcome == "UNKNOWN" for row in facts):
        return None
    return sum(row.outcome == "ACCEPTED" for row in facts)


def _optional_positive_id(value: object, name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not str(value).isdigit() or int(str(value)) <= 0:
        raise ValueError(f"TikTok {name} is invalid")
    return str(int(str(value)))


def _positive_id(value: object, name: str) -> str:
    result = _optional_positive_id(value, name)
    if result is None:
        raise ValueError(f"TikTok {name} is required")
    return result


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


class MiaoshouOpenApiTikTokV4DraftTransport:
    """Thin OpenAPI seam over the audited claim and draft-save endpoints."""

    def __init__(
        self,
        *,
        common_detail_id: str,
        initial_platform_detail_id: str | None = None,
        post: Callable[[str, dict[str, object]], Mapping[str, object]] = post_open,
    ) -> None:
        self._common_detail_id = _positive_id(common_detail_id, "common_detail_id")
        self._initial_platform_detail_id = _optional_positive_id(
            initial_platform_detail_id, "initial_platform_detail_id"
        )
        if not callable(post):
            raise TypeError("Miaoshou OpenAPI post transport is invalid")
        self._post = post

    def claim_or_create(
        self, *, target: Mapping[str, object], ordinal: int
    ) -> tuple[DraftWriteFact, ...]:
        label = str(target.get("target_label") or "")
        shop_id = _positive_id(target.get("shop_id"), "shop_id")
        if (
            EXPECTED_SHOP_ID_BY_TARGET.get(label) != shop_id
            or type(ordinal) is not int
            or ordinal < 0
        ):
            raise TikTokV4DraftPreparationError("Miaoshou target identity is invalid")
        facts: list[DraftWriteFact] = []
        detail_id = self._initial_platform_detail_id if ordinal == 0 else None
        if detail_id is None:
            try:
                response = self._post(
                    DETAIL_CREATE_PATH,
                    {
                        "detailSerialNumberPlatformList": [
                            {
                                "detailId": int(self._common_detail_id),
                                "platform": "tiktok",
                                "serialNumber": ordinal + 1,
                            }
                        ]
                    },
                )
            except MiaoshouBusinessRejectedError:
                return (DraftWriteFact("CREATE_DRAFT", "REJECTED"),)
            except Exception:
                return (DraftWriteFact("CREATE_DRAFT", "UNKNOWN"),)
            try:
                detail_id = _created_detail_id(
                    response, common_detail_id=self._common_detail_id
                )
            except Exception:
                return (DraftWriteFact("CREATE_DRAFT", "UNKNOWN"),)
            facts.append(
                DraftWriteFact(
                    "CREATE_DRAFT",
                    "ACCEPTED",
                    detail_id=detail_id,
                    shop_id=shop_id,
                )
            )
        try:
            self._post(
                SHOP_CLAIM_PATH,
                {"detailIds": [int(detail_id)], "shopIds": [int(shop_id)]},
            )
        except MiaoshouBusinessRejectedError:
            facts.append(
                DraftWriteFact(
                    "CLAIM_TO_SHOP",
                    "REJECTED",
                    detail_id=detail_id,
                    shop_id=shop_id,
                )
            )
        except Exception:
            facts.append(
                DraftWriteFact(
                    "CLAIM_TO_SHOP",
                    "UNKNOWN",
                    detail_id=detail_id,
                    shop_id=shop_id,
                )
            )
        else:
            facts.append(
                DraftWriteFact(
                    "CLAIM_TO_SHOP",
                    "ACCEPTED",
                    detail_id=detail_id,
                    shop_id=shop_id,
                )
            )
        return tuple(facts)

    def save_draft(
        self,
        *,
        identity: Mapping[str, str],
        draft: Mapping[str, object],
    ) -> DraftWriteFact:
        label = str(identity.get("target_label") or "")
        detail_id = _positive_id(identity.get("detail_id"), "detail_id")
        shop_id = _positive_id(identity.get("shop_id"), "shop_id")
        if EXPECTED_SHOP_ID_BY_TARGET.get(label) != shop_id:
            raise TikTokV4DraftPreparationError("Miaoshou save identity drifted")
        info = _miaoshou_draft_info(draft)
        site = label.rsplit("_", 1)[-1].upper()
        if site in _SITE_DRAFT_SITES:
            path = SAVE_SITE_DRAFT_PATH
            body = {
                "detailId": int(detail_id),
                "site": site,
                "siteCollectItemInfo": info,
                "ossMd5": "",
            }
        else:
            path = SAVE_SHOP_DRAFT_PATH
            body = {
                "detailId": int(detail_id),
                "shopId": int(shop_id),
                "shopCollectItemInfo": info,
                "ossMd5": "",
            }
        try:
            self._post(path, body)
        except MiaoshouBusinessRejectedError:
            return DraftWriteFact(
                "SAVE_DRAFT", "REJECTED", detail_id=detail_id, shop_id=shop_id
            )
        except Exception:
            return DraftWriteFact(
                "SAVE_DRAFT", "UNKNOWN", detail_id=detail_id, shop_id=shop_id
            )
        return DraftWriteFact(
            "SAVE_DRAFT", "ACCEPTED", detail_id=detail_id, shop_id=shop_id
        )


def _created_detail_id(
    response: Mapping[str, object], *, common_detail_id: str
) -> str:
    data = response.get("data") if isinstance(response, Mapping) else None
    root = data.get("platformCollectBoxDetailIdMap") if isinstance(data, Mapping) else None
    mapping = root.get("tiktok") if isinstance(root, Mapping) else None
    raw = mapping.get(common_detail_id) if isinstance(mapping, Mapping) else None
    if raw is None and isinstance(mapping, Mapping):
        raw = mapping.get(int(common_detail_id))
    return _positive_id(raw, "platform_detail_id")


def _miaoshou_draft_info(draft: Mapping[str, object]) -> dict[str, object]:
    if draft.get("schema_version") != DRAFT_SCHEMA_VERSION:
        raise TikTokV4DraftPreparationError("Miaoshou v4 draft is invalid")
    category = _category(draft.get("category"))
    parent = draft.get("parent_parcel")
    skus = draft.get("skus")
    if not isinstance(parent, Mapping) or not isinstance(skus, list) or not skus:
        raise TikTokV4DraftPreparationError("Miaoshou v4 draft facts are invalid")
    package = parent["package_cm"]
    sku_map: dict[str, object] = {}
    for row in skus:
        if not isinstance(row, Mapping):
            raise TikTokV4DraftPreparationError("Miaoshou v4 SKU is invalid")
        parcel = row["parcel"]
        sku_package = parcel["package_cm"]
        sku_map[str(row["variant_key"])] = {
            "itemNum": row["model_sku"],
            "sellerSku": row["seller_sku"],
            "specification": deepcopy(row["specification"]),
            "price": float(Decimal(str(row["price"]))),
            "priceIncludeVat": float(Decimal(str(row["price"]))),
            "currency": row["currency"],
            "weight": float(Decimal(str(parcel["weight_kg"]))),
            "packageLength": float(Decimal(str(sku_package[0]))),
            "packageWidth": float(Decimal(str(sku_package[1]))),
            "packageHeight": float(Decimal(str(sku_package[2]))),
            "imgUrls": deepcopy(row["images"]),
        }
    return {
        "title": draft["title"],
        "notes": draft["description"],
        "notesText": draft["description"],
        "imgUrls": deepcopy(draft["images"]),
        "cid": category["id"],
        "weight": float(Decimal(str(parent["weight_kg"]))),
        "packageLength": float(Decimal(str(package[0]))),
        "packageWidth": float(Decimal(str(package[1]))),
        "packageHeight": float(Decimal(str(package[2]))),
        "skuMap": sku_map,
    }


__all__ = [
    "CONTEXT_SCHEMA_VERSION",
    "DRAFT_SCHEMA_VERSION",
    "DraftWriteFact",
    "MiaoshouOpenApiTikTokV4DraftTransport",
    "PREPARATION_SCHEMA_VERSION",
    "TikTokV4DraftPreparationError",
    "prepare_tiktok_v4_drafts",
]
