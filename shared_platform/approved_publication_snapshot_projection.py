"""Strict field-name projection into the product-owned v4 snapshot contract.

The adapter is deliberately conservative: it may rename facts already frozen
inside an immutable ReleasePlan, but it never creates provider category
decisions, variant-image bindings, descriptions, or evidence digests.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from domains.product_operations import (
    APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION,
    ApprovedPublicationSnapshotError,
    build_approved_publication_snapshot,
)


@dataclass(frozen=True)
class PublicationSnapshotPlanProjection:
    ready: bool
    payload: dict[str, Any]
    missing_fields: tuple[str, ...]


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _text(value: object) -> str:
    return value.strip() if type(value) is str else ""


def _ordered_image_urls(value: object) -> list[str] | None:
    if type(value) is not list or not value:
        return None
    rows: list[tuple[int, str]] = []
    for row in value:
        if not isinstance(row, Mapping):
            return None
        position = row.get("position")
        url = _text(row.get("image_url"))
        if type(position) is not int or position <= 0 or not url:
            return None
        rows.append((position, url))
    rows.sort()
    if [position for position, _url in rows] != list(range(1, len(rows) + 1)):
        return None
    urls = [url for _position, url in rows]
    return urls if len(urls) == len(set(urls)) else None


def _candidate_payload(
    plan_payload: Mapping[str, Any],
    approved_inputs: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    candidate = deepcopy(dict(plan_payload))
    missing: list[str] = []
    lineage = candidate.get("sku_lineage")
    if isinstance(lineage, dict) and not lineage.get("reservation_digest"):
        reservation = lineage.get("reservation")
        if isinstance(reservation, Mapping) and _text(
            reservation.get("reservation_digest")
        ):
            lineage["reservation_digest"] = reservation[
                "reservation_digest"
            ]
    product_facts = candidate.get("product_facts")
    if not isinstance(product_facts, dict):
        return candidate, ["product_facts"]

    inputs = deepcopy(dict(approved_inputs or {}))
    if isinstance(inputs.get("main_category"), Mapping):
        product_facts["category"] = inputs["main_category"]
    if _text(inputs.get("description")):
        product_facts["description"] = inputs["description"]
    if isinstance(inputs.get("categories_by_target"), Mapping):
        product_facts["categories_by_target"] = inputs[
            "categories_by_target"
        ]
    sku_inputs = inputs.get("sku_details_by_key")
    commercial_rows = product_facts.get("sku_commercial_facts")
    if isinstance(sku_inputs, Mapping) and isinstance(commercial_rows, dict):
        for key, details in sku_inputs.items():
            if key in commercial_rows and isinstance(details, Mapping):
                if isinstance(details.get("specification"), Mapping):
                    commercial_rows[key]["specification"] = deepcopy(
                        details["specification"]
                    )
                if type(details.get("image_urls")) is list:
                    commercial_rows[key]["image_urls"] = list(
                        details["image_urls"]
                    )
    if isinstance(inputs.get("digests"), Mapping):
        candidate["digests"] = inputs["digests"]
    if isinstance(inputs.get("pricing"), Mapping):
        candidate["pricing"] = inputs["pricing"]
    if "shopee_global_master" in inputs:
        candidate["shopee_global_master"] = inputs[
            "shopee_global_master"
        ]

    if not _text(product_facts.get("description")):
        missing.append("product_facts.description")
    if not product_facts.get("image_urls"):
        ordered_urls = _ordered_image_urls(candidate.get("images"))
        if ordered_urls:
            product_facts["image_urls"] = ordered_urls
        else:
            missing.append("product_facts.image_urls")
    category = product_facts.get("category")
    if not isinstance(category, Mapping) or not _text(category.get("id")) or not _text(category.get("name")):
        missing.append("product_facts.category.id+name")

    categories = product_facts.get("categories_by_target")
    if not isinstance(categories, Mapping):
        # main_category is product taxonomy only and is never a provider ID.
        missing.append("product_facts.categories_by_target")

    commercial = product_facts.get("sku_commercial_facts")
    selected = product_facts.get("selected_sku_keys")
    if not isinstance(commercial, Mapping) or type(selected) is not list:
        missing.append("product_facts.sku_commercial_facts")
    else:
        for key in selected:
            row = commercial.get(key)
            path = f"product_facts.sku_commercial_facts[{key}]"
            if not isinstance(row, dict):
                missing.append(path)
                continue
            if not isinstance(row.get("specification"), Mapping) or not row.get("specification"):
                missing.append(path + ".specification")
            if not isinstance(row.get("cost"), Mapping):
                cost_cny = row.get("cost_cny")
                if type(cost_cny) in {str, int, float} and not isinstance(cost_cny, bool):
                    row["cost"] = {"amount": cost_cny, "currency": "CNY"}
                else:
                    missing.append(path + ".cost")
            if type(row.get("image_urls")) is not list or not row.get("image_urls"):
                missing.append(path + ".image_urls")

    if not isinstance(candidate.get("digests"), Mapping):
        missing.append("digests")
    if any(
        type(target) is str and target.startswith("shopee:")
        for target in (candidate.get("targets") or ())
    ) and not isinstance(candidate.get("shopee_global_master"), Mapping):
        missing.append("shopee_global_master")
    return candidate, list(dict.fromkeys(missing))


def project_release_plan_for_publication_snapshot(
    plan_payload: Mapping[str, Any],
    *,
    approved_inputs: Mapping[str, Any] | None = None,
) -> PublicationSnapshotPlanProjection:
    """Map exact frozen names and prove the result through the 01 validator."""

    if not isinstance(plan_payload, Mapping):
        raise TypeError("ReleasePlan payload must be a mapping")
    if approved_inputs is not None and not isinstance(approved_inputs, Mapping):
        raise TypeError("approved publication snapshot inputs must be a mapping")
    candidate, missing = _candidate_payload(plan_payload, approved_inputs)
    if missing:
        return PublicationSnapshotPlanProjection(
            ready=False,
            payload=deepcopy(dict(plan_payload)),
            missing_fields=tuple(missing),
        )

    candidate["approved_publication_snapshot_schema_version"] = (
        APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION
    )
    digest = _canonical_digest(candidate)
    plan_id = candidate.get("plan_id")
    approved_at = "2000-01-01T00:00:00+00:00"
    validation_plan = {
        "plan_id": plan_id,
        "product_id": candidate.get("product_id"),
        "targets": list(candidate.get("targets") or ()),
        "payload": candidate,
        "payload_digest": digest,
        "status": "APPROVED",
        "approved_at": approved_at,
        "approval": {
            "status": "APPROVED",
            "approved_by": "Kyle",
            "approved_at": approved_at,
            "user_approved": True,
            "plan_id": plan_id,
            "payload_digest": digest,
        },
    }
    try:
        build_approved_publication_snapshot(validation_plan)
    except ApprovedPublicationSnapshotError as error:
        return PublicationSnapshotPlanProjection(
            ready=False,
            payload=deepcopy(dict(plan_payload)),
            missing_fields=(f"v4_contract:{error}",),
        )
    return PublicationSnapshotPlanProjection(
        ready=True,
        payload=candidate,
        missing_fields=(),
    )


__all__ = [
    "PublicationSnapshotPlanProjection",
    "project_release_plan_for_publication_snapshot",
]
