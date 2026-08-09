"""Pure approval-time bridge into approved-publication-snapshot/v4 inputs.

The bridge consumes only the dashboard facts that are about to be approved and
the ReleasePlan payload built from those same facts.  Its output is copied into
that ReleasePlan before approval, so later stages never need to reconstruct a
snapshot from mutable Product Center state.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from .approved_publication_snapshot import (
    ApprovedPublicationSnapshotError,
    publication_category_decision_digest,
)


def build_approved_publication_snapshot_inputs(
    *,
    dashboard: Mapping[str, Any],
    release_plan_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return complete, detached v4 projection inputs or fail closed.

    Provider categories are never inferred from the product taxonomy.  When no
    exact provider decision is already frozen, each real target is explicitly
    marked ``DEFERRED_TO_SKILL`` for official mapping during execution.
    """

    dashboard_copy = _json_object(dashboard, "approved dashboard")
    payload = _json_object(release_plan_payload, "ReleasePlan payload")
    product = _mapping(dashboard_copy.get("product"), "approved product")
    content = _mapping(dashboard_copy.get("content"), "approved content")
    listing_copy = _mapping(
        dashboard_copy.get("listing_copy"), "approved listing copy"
    )
    product_facts = _mapping(payload.get("product_facts"), "product_facts")

    if product.get("actual_product_approved") is not True:
        raise ApprovedPublicationSnapshotError("product facts are not approved")
    if content.get("approved") is not True:
        raise ApprovedPublicationSnapshotError("content facts are not approved")

    _same_text(product.get("offer_id"), payload.get("product_id"), "offer identity")
    if (
        type(product.get("revision")) is not int
        or product.get("revision") != payload.get("product_revision")
    ):
        raise ApprovedPublicationSnapshotError("product revision identity conflicts")
    _same_text(
        (product.get("actual_approval") or {}).get("package_id")
        if isinstance(product.get("actual_approval"), Mapping)
        else None,
        payload.get("product_package_id"),
        "product package identity",
    )
    _same_text(
        content.get("package_id"),
        payload.get("content_package_id"),
        "content package identity",
    )
    _same_text(product.get("title"), product_facts.get("title"), "title identity")

    targets = _text_list(payload.get("targets"), "publication targets")
    scope = _mapping(
        dashboard_copy.get("publication_scope"), "publication scope"
    )
    if (
        _text_list(
            scope.get("selected_labels"), "selected publication targets"
        )
        != targets
    ):
        raise ApprovedPublicationSnapshotError("publication target identity conflicts")

    plan_copy = _mapping(payload.get("listing_copy"), "plan listing copy")
    description = _text(
        plan_copy.get("shopee_description_en"), "approved description"
    )
    if description != _text(
        listing_copy.get("shopee_description_en"), "dashboard approved description"
    ):
        raise ApprovedPublicationSnapshotError("approved description identity conflicts")
    if plan_copy.get("status") != "adopted_in_product_facts":
        raise ApprovedPublicationSnapshotError("approved description is not adopted")

    image_urls = _ordered_image_urls(payload.get("images"), "approved images")
    dashboard_image_urls = _ordered_image_urls(
        content.get("images"), "dashboard approved images"
    )
    if image_urls != dashboard_image_urls:
        raise ApprovedPublicationSnapshotError("approved images identity conflicts")

    raw_category = _mapping(product_facts.get("category"), "main category")
    dashboard_category = _mapping(product.get("category"), "dashboard main category")
    if _json_object(raw_category, "main category") != _json_object(
        dashboard_category, "dashboard main category"
    ):
        raise ApprovedPublicationSnapshotError("main category identity conflicts")
    category_name = _text(raw_category.get("name"), "main category name")
    category_id = _optional_text(raw_category.get("id"))
    main_category = {
        "id": category_id or "product-semantic:" + _hex_digest(
            {"name": category_name}
        ),
        "name": category_name,
    }

    selected_keys = _text_list(
        product_facts.get("selected_sku_keys"), "selected SKU keys"
    )
    if (
        _text_list(
            product.get("selected_sku_keys"),
            "dashboard selected SKU keys",
        )
        != selected_keys
    ):
        raise ApprovedPublicationSnapshotError("selected SKU identity conflicts")
    plan_skus = _rows_by_key(
        product_facts.get("selected_skus"), "plan selected SKUs"
    )
    dashboard_skus = _rows_by_key(
        product.get("source_skus"), "dashboard selected SKUs"
    )
    if set(plan_skus) != set(selected_keys) or set(dashboard_skus) != set(
        selected_keys
    ):
        raise ApprovedPublicationSnapshotError("selected SKU coverage conflicts")

    lineage = _mapping(payload.get("sku_lineage"), "SKU lineage")
    assignment = _mapping(lineage.get("assignment"), "SKU assignment")
    lineage_models = _model_skus(assignment.get("model_skus"))
    if list(lineage_models) != selected_keys:
        raise ApprovedPublicationSnapshotError("SKU lineage coverage conflicts")
    plan_commercial = _mapping(
        product_facts.get("sku_commercial_facts"),
        "plan SKU commercial facts",
    )
    dashboard_commercial = _mapping(
        product.get("sku_commercial_facts"),
        "dashboard SKU commercial facts",
    )
    if set(plan_commercial) != set(selected_keys) or set(dashboard_commercial) != set(
        selected_keys
    ):
        raise ApprovedPublicationSnapshotError("SKU commercial fact coverage conflicts")

    sku_details: dict[str, dict[str, Any]] = {}
    for key in selected_keys:
        plan_row = plan_skus[key]
        dashboard_row = dashboard_skus[key]
        plan_label = _text(plan_row.get("label"), f"{key} specification")
        dashboard_label = _text(
            dashboard_row.get("label"), f"{key} dashboard specification"
        )
        if plan_label != dashboard_label:
            raise ApprovedPublicationSnapshotError(
                f"{key} specification identity conflicts"
            )
        expected_model = lineage_models[key]
        if (
            _text(plan_row.get("model_sku"), f"{key} plan model SKU")
            != expected_model
        ):
            raise ApprovedPublicationSnapshotError(
                f"{key} model SKU identity conflicts"
            )
        if _text(
            dashboard_row.get("model_sku"), f"{key} dashboard model SKU"
        ) != expected_model:
            raise ApprovedPublicationSnapshotError(
                f"{key} model SKU identity conflicts"
            )
        if _json_object(plan_commercial[key], f"{key} plan commercial facts") != (
            _json_object(
                dashboard_commercial[key],
                f"{key} dashboard commercial facts",
            )
        ):
            raise ApprovedPublicationSnapshotError(
                f"{key} commercial facts identity conflicts"
            )
        raw_specification = dashboard_row.get("specification")
        if raw_specification is None:
            specification = {"option": plan_label}
        elif isinstance(raw_specification, Mapping):
            specification = _string_mapping(
                raw_specification, f"{key} specification"
            )
        else:
            raise ApprovedPublicationSnapshotError(
                f"{key} specification has invalid type"
            )
        images = _variant_images(dashboard_row, key=key)
        if images is None:
            # The approved ordered product images are a valid explicit binding
            # for every variant when Product Center has no narrower per-SKU
            # image fact.  This is a frozen copy, not a later mutable read.
            images = list(image_urls)
        sku_details[key] = {
            "specification": specification,
            "image_urls": images,
        }

    categories = product_facts.get("categories_by_target")
    if categories is None:
        categories_by_target = _deferred_categories(targets)
    elif isinstance(categories, Mapping):
        categories_by_target = _json_object(
            categories, "categories_by_target"
        )
    else:
        raise ApprovedPublicationSnapshotError(
            "categories_by_target must be a mapping"
        )

    source = _mapping(payload.get("source_product_identity"), "source identity")
    source_digest = _digest_text(
        source.get("identity_digest"), "source identity digest"
    )
    lineage_digest = _lineage_digest(lineage)
    digests = {
        "source": source_digest,
        "content": _digest(
            {
                "content_package_id": payload.get("content_package_id"),
                "title": product_facts.get("title"),
                "description": description,
                "image_urls": image_urls,
                "sku_details_by_key": sku_details,
                "copy_identity": {
                    "input_signature": plan_copy.get("input_signature"),
                    "current_input_signature": plan_copy.get(
                        "current_input_signature"
                    ),
                },
            }
        ),
        "policy": _digest(
            {
                "product_package_id": payload.get("product_package_id"),
                "content_package_id": payload.get("content_package_id"),
                "targets": targets,
                "postpublish_promotion_policy": payload.get(
                    "approved_postpublish_promotion_policy"
                ),
            }
        ),
        "category": _digest(
            {
                "main_category": main_category,
                "categories_by_target": categories_by_target,
            }
        ),
        "pricing": _digest(
            _mapping(payload.get("pricing"), "approved pricing")
        ),
        "sku_lineage": lineage_digest,
    }
    return {
        "main_category": main_category,
        "description": description,
        "categories_by_target": categories_by_target,
        "sku_details_by_key": sku_details,
        "digests": digests,
    }


def _deferred_categories(targets: list[str]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for target in targets:
        if target.count(":") != 1:
            raise ApprovedPublicationSnapshotError("publication target is invalid")
        platform, site = target.split(":", 1)
        platform = _text(platform, f"{target} platform")
        site = _text(site, f"{target} site")
        status = (
            "NOT_APPLICABLE"
            if target == "miaoshou:COMMON"
            else "DEFERRED_TO_SKILL"
        )
        rows[target] = {
            "target_label": target,
            "platform": platform,
            "site": site,
            "store": site,
            "category": None,
            "decision": {
                "status": status,
                "decision_digest": publication_category_decision_digest(
                    target_label=target,
                    platform=platform,
                    site=site,
                    store=site,
                    status=status,
                ),
            },
        }
    return rows


def _rows_by_key(value: Any, name: str) -> dict[str, Mapping[str, Any]]:
    if type(value) is not list or not value:
        raise ApprovedPublicationSnapshotError(f"{name} must be a non-empty list")
    result: dict[str, Mapping[str, Any]] = {}
    for raw in value:
        row = _mapping(raw, name)
        key = _text(row.get("key"), f"{name} key")
        if key in result:
            raise ApprovedPublicationSnapshotError(f"{name} identities conflict")
        result[key] = row
    return result


def _model_skus(value: Any) -> dict[str, str]:
    if type(value) is not list or not value:
        raise ApprovedPublicationSnapshotError("model SKUs must be a non-empty list")
    result: dict[str, str] = {}
    seen: set[str] = set()
    for raw in value:
        row = _mapping(raw, "model SKU")
        key = _text(row.get("variant_key"), "model SKU variant_key")
        model = _text(row.get("model_sku"), "model_sku")
        if key in result or model in seen:
            raise ApprovedPublicationSnapshotError("model SKU identities conflict")
        result[key] = model
        seen.add(model)
    return result


def _variant_images(row: Mapping[str, Any], *, key: str) -> list[str] | None:
    raw = row.get("image_urls")
    if raw is not None:
        return _text_list(raw, f"{key} variant images")
    single = _optional_text(row.get("image_url"))
    return [single] if single else None


def _ordered_image_urls(value: Any, name: str) -> list[str]:
    if type(value) is not list or not value:
        raise ApprovedPublicationSnapshotError(f"{name} are missing")
    rows: list[tuple[int, str]] = []
    for raw in value:
        row = _mapping(raw, name)
        position = row.get("position")
        if type(position) is not int or position <= 0:
            raise ApprovedPublicationSnapshotError(f"{name} positions are invalid")
        rows.append((position, _text(row.get("image_url"), f"{name} URL")))
    rows.sort()
    if [position for position, _ in rows] != list(range(1, len(rows) + 1)):
        raise ApprovedPublicationSnapshotError(f"{name} positions are incomplete")
    urls = [url for _, url in rows]
    if len(urls) != len(set(urls)):
        raise ApprovedPublicationSnapshotError(f"{name} contain duplicates")
    return urls


def _lineage_digest(lineage: Mapping[str, Any]) -> str:
    direct = lineage.get("reservation_digest")
    if direct is None and isinstance(lineage.get("reservation"), Mapping):
        direct = lineage["reservation"].get("reservation_digest")
    return _digest_text(direct, "SKU lineage digest")


def _same_text(left: Any, right: Any, name: str) -> None:
    if _text(left, name) != _text(right, name):
        raise ApprovedPublicationSnapshotError(f"{name} conflicts")


def _string_mapping(value: Any, name: str) -> dict[str, str]:
    row = _mapping(value, name)
    result = {
        _text(key, f"{name} key"): _text(item, f"{name} value")
        for key, item in row.items()
    }
    if not result or len(result) != len(row):
        raise ApprovedPublicationSnapshotError(f"{name} is empty")
    return result


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ApprovedPublicationSnapshotError(f"{name} must be a mapping")
    return value


def _text_list(value: Any, name: str) -> list[str]:
    if type(value) is not list or not value:
        raise ApprovedPublicationSnapshotError(f"{name} must be a non-empty list")
    result = [_text(item, name) for item in value]
    if len(result) != len(set(result)):
        raise ApprovedPublicationSnapshotError(f"{name} contains duplicates")
    return result


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ApprovedPublicationSnapshotError(f"{name} must be non-empty text")
    return value.strip()


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    if type(value) is not str:
        raise ApprovedPublicationSnapshotError("optional text has invalid type")
    return value.strip()


def _digest_text(value: Any, name: str) -> str:
    text = _text(value, name)
    candidate = text.removeprefix("sha256:")
    if len(candidate) != 64 or any(
        character not in "0123456789abcdef" for character in candidate
    ):
        raise ApprovedPublicationSnapshotError(f"{name} is invalid")
    return text


def _json_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ApprovedPublicationSnapshotError(f"{name} must be a mapping")
    try:
        copied = json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError):
        raise ApprovedPublicationSnapshotError(
            f"{name} must be JSON serializable"
        ) from None
    if not isinstance(copied, dict):
        raise ApprovedPublicationSnapshotError(f"{name} must be an object")
    return copied


def _hex_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _digest(value: Any) -> str:
    return "sha256:" + _hex_digest(value)


__all__ = ["build_approved_publication_snapshot_inputs"]
