"""Pure approval-time bridge into approved-publication-snapshot/v4 inputs.

The bridge consumes only the dashboard facts that are about to be approved and
the ReleasePlan payload built from those same facts.  Its output is copied into
that ReleasePlan before approval, so later stages never need to reconstruct a
snapshot from mutable Product Center state.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, ROUND_CEILING
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
    normalized_pricing = _normalized_pricing(
        payload.get("pricing"),
        targets=targets,
        model_skus=list(lineage_models.values()),
    )
    shopee_global_master = _shopee_global_master_inputs(
        payload=payload,
        pricing=normalized_pricing,
        targets=targets,
        model_skus=list(lineage_models.values()),
        approved_product_images=image_urls,
        sku_details_by_key=sku_details,
        sku_commercial_by_key=plan_commercial,
        variant_keys_by_model={
            model_sku: variant_key
            for variant_key, model_sku in lineage_models.items()
        },
    )
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
            {
                "pricing": normalized_pricing,
                "shopee_global_master": shopee_global_master,
            }
        ),
        "sku_lineage": lineage_digest,
    }
    return {
        "main_category": main_category,
        "description": description,
        "categories_by_target": categories_by_target,
        "sku_details_by_key": sku_details,
        "pricing": normalized_pricing,
        "shopee_global_master": shopee_global_master,
        "digests": digests,
    }


def _shopee_global_master_inputs(
    *,
    payload: Mapping[str, Any],
    pricing: Mapping[str, Any],
    targets: list[str],
    model_skus: list[str],
    approved_product_images: list[str],
    sku_details_by_key: Mapping[str, Mapping[str, Any]],
    sku_commercial_by_key: Mapping[str, Mapping[str, Any]],
    variant_keys_by_model: Mapping[str, str],
) -> dict[str, Any] | None:
    """Freeze one exact CNSC master contract without reusing regional facts.

    The approved ``master_price_source`` is the only authority for the CNSC
    master price.  Regional listing prices stay untouched in ``pricing``.
    """

    shopee_targets = [target for target in targets if target.startswith("shopee:")]
    if not shopee_targets:
        return None
    raw_pricing = _mapping(payload.get("pricing"), "approved pricing")
    master_source = _mapping(
        raw_pricing.get("master_price_source"),
        "Shopee global master price source",
    )
    region = _text(master_source.get("region"), "Shopee master source region").upper()
    target_key = _text(
        master_source.get("target_key"), "Shopee master source target_key"
    )
    target_label = f"shopee:{region}"
    selected = _mapping(
        pricing.get("selected_targets"), "selected target pricing"
    )
    if target_label not in shopee_targets or target_label not in selected:
        raise ApprovedPublicationSnapshotError(
            "Shopee global master price source is not selected"
        )
    matched: list[str] = []
    for label in shopee_targets:
        row = _mapping(selected.get(label), f"{label} pricing")
        source = row.get("source")
        if not isinstance(source, Mapping):
            continue
        source_key = source.get("target_key")
        if source_key == target_key:
            matched.append(label)
        if label == target_label and (
            source_key != target_key
            or _text(source.get("region"), f"{label} source region").upper()
            != region
        ):
            raise ApprovedPublicationSnapshotError(
                "Shopee global master price source drifted"
            )
    if matched != [target_label]:
        raise ApprovedPublicationSnapshotError(
            "Shopee global master price source is ambiguous"
        )

    source_row = _mapping(selected[target_label], f"{target_label} pricing")
    price_rows = source_row.get("sku_prices")
    if type(price_rows) is not list:
        raise ApprovedPublicationSnapshotError(
            "Shopee global master price source has no SKU prices"
        )
    prices_by_model: dict[str, dict[str, Any]] = {}
    for raw in price_rows:
        row = _mapping(raw, f"{target_label} master SKU price")
        model_sku = _text(row.get("model_sku"), "Shopee master model_sku")
        if model_sku not in model_skus or model_sku in prices_by_model:
            raise ApprovedPublicationSnapshotError(
                "Shopee global master SKU price identity conflicts"
            )
        prices_by_model[model_sku] = {
            "model_sku": model_sku,
            "amount": _positive_decimal_text(
                row.get("global_original_price_cny"),
                "Shopee global master CNY price",
            ),
            "currency": "CNY",
        }
    if list(prices_by_model) != model_skus:
        raise ApprovedPublicationSnapshotError(
            "Shopee global master SKU price coverage conflicts"
        )

    category_decision, decision_policy = _shopee_global_category_and_policy(payload)
    positions = _shopee_global_variant_image_positions(
        payload=payload,
        model_skus=model_skus,
        approved_product_images=approved_product_images,
        sku_details_by_key=sku_details_by_key,
        variant_keys_by_model=variant_keys_by_model,
    )
    price_source = {
        "target_label": target_label,
        "region": region,
        "target_key": target_key,
    }
    price_source["source_binding_digest"] = _digest(
        {
            "schema_version": "shopee-global-master-price-source/v1",
            **price_source,
        }
    )
    parcel_envelope = _shopee_parcel_envelope_inputs(
        model_skus=model_skus,
        variant_keys_by_model=variant_keys_by_model,
        sku_commercial_by_key=sku_commercial_by_key,
    )
    return {
        "schema_version": "shopee-global-master/v1",
        "price_source": price_source,
        "sku_original_prices_cny": [prices_by_model[model] for model in model_skus],
        "category_decision": category_decision,
        "parcel_envelope": parcel_envelope,
        "policy": decision_policy,
        "variant_image_positions": positions,
    }


def _shopee_parcel_envelope_inputs(
    *,
    model_skus: list[str],
    variant_keys_by_model: Mapping[str, str],
    sku_commercial_by_key: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    weights: list[Decimal] = []
    packages: list[list[Decimal]] = []
    for model_sku in model_skus:
        variant_key = variant_keys_by_model.get(model_sku)
        row = _mapping(
            sku_commercial_by_key.get(variant_key),
            f"Shopee {model_sku} parcel source",
        )
        weights.append(
            Decimal(
                _positive_decimal_text(
                    row.get("weight_kg"), f"Shopee {model_sku} weight"
                )
            )
        )
        package = row.get("package_cm")
        if type(package) is not list or len(package) != 3:
            raise ApprovedPublicationSnapshotError(
                f"Shopee {model_sku} package requires three dimensions"
            )
        packages.append(
            [
                Decimal(
                    _positive_decimal_text(
                        value, f"Shopee {model_sku} package dimension"
                    )
                )
                for value in package
            ]
        )
    return {
        "weight_kg": _positive_decimal_text(
            str(max(weights)), "Shopee parcel envelope weight"
        ),
        "package_cm": [
            int(
                max(package[index] for package in packages).to_integral_value(
                    rounding=ROUND_CEILING
                )
            )
            for index in range(3)
        ],
        "policy_version": "shopee-global-parcel-ceil-cm/v1",
    }


def _shopee_global_category_and_policy(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    bindings = payload.get("approved_channel_category_decisions")
    records = payload.get("_channel_category_decision_records")
    binding = bindings.get("shopee:GLOBAL") if isinstance(bindings, Mapping) else None
    record = records.get("shopee:GLOBAL") if isinstance(records, Mapping) else None
    if binding is None and record is None:
        decision = {
            "status": "DEFERRED_TO_SKILL",
            "category": None,
            "required_attributes": [],
            "source_decision_digest": None,
        }
        decision["decision_digest"] = _digest(
            {
                "schema_version": "shopee-global-category-decision/v1",
                **decision,
            }
        )
        return decision, _deferred_shopee_global_policy()
    if binding is None or record is None or type(record) is not str:
        raise ApprovedPublicationSnapshotError(
            "Shopee global category decision is incomplete"
        )
    try:
        from shared_platform.channel_category_decisions import (
            category_decision_execution_payload,
            category_decision_plan_binding,
            rehydrate_category_decision,
        )

        approved = rehydrate_category_decision(record)
        if (
            approved.get("product_id") != payload.get("product_id")
            or approved.get("product_revision") != payload.get("product_revision")
        ):
            raise ValueError("product identity drifted")
        if category_decision_plan_binding(approved) != dict(binding):
            raise ValueError("binding drifted")
        execution = category_decision_execution_payload(approved)
    except (TypeError, ValueError) as error:
        raise ApprovedPublicationSnapshotError(
            f"Shopee global category decision drifted: {error}"
        ) from None
    path = execution["category"]["path"]
    category = {
        "id": str(execution["category"]["category_id"]),
        "name": _text(path[-1].get("name"), "Shopee global category name"),
        "path": [
            {
                "id": str(node.get("category_id")),
                "name": _text(node.get("name"), "Shopee global category path name"),
            }
            for node in path
        ],
    }
    decision = {
        "status": "APPROVED",
        "category": category,
        "required_attributes": _json_list(
            execution["attribute_list"], "Shopee global required attributes"
        ),
        "source_decision_digest": _digest_text_with_prefix(
            approved["decision_digest"], "Shopee global category decision digest"
        ),
    }
    decision["decision_digest"] = _digest(
        {
            "schema_version": "shopee-global-category-decision/v1",
            **decision,
        }
    )
    selected_brand = approved["selected_brand"]
    selected_location = approved["selected_location"]
    return decision, {
        "brand": {
            "brand_id": selected_brand["brand_id"],
            "original_brand_name": selected_brand["original_brand_name"],
            "policy_version": "shopee-global-fixed-no-brand/v1",
        },
        "condition": approved["condition"],
        # Shopee CNSC rejects a non-preorder item when days_to_ship is zero.
        # The user policy remains non-preorder; freeze the provider-required
        # minimum DTS as an explicit immutable execution fact.
        "preorder": {"is_pre_order": False, "days_to_ship": 1},
        "stock": {
            "quantity": approved["seller_stock"]["quantity"],
            "policy_version": "shopee-global-fixed-stock/v1",
        },
        "warehouse": {
            # This is the user-approved policy label.  The exact provider
            # location identity remains the official location_id below.
            "display_name": "中国仓库",
            "location_id": selected_location["location_id"],
            "policy_version": "shopee-global-fixed-china-warehouse/v1",
            "status": "APPROVED",
        },
    }


def _deferred_shopee_global_policy() -> dict[str, Any]:
    return {
        "brand": {
            "brand_id": 0,
            "original_brand_name": "NoBrand",
            "policy_version": "shopee-global-fixed-no-brand/v1",
        },
        "condition": "NEW",
        "preorder": {"is_pre_order": False, "days_to_ship": 1},
        "stock": {
            "quantity": 200,
            "policy_version": "shopee-global-fixed-stock/v1",
        },
        "warehouse": {
            "display_name": "中国仓库",
            "location_id": None,
            "policy_version": "shopee-global-fixed-china-warehouse/v1",
            "status": "DEFERRED_TO_SKILL",
        },
    }


def _shopee_global_variant_image_positions(
    *,
    payload: Mapping[str, Any],
    model_skus: list[str],
    approved_product_images: list[str],
    sku_details_by_key: Mapping[str, Mapping[str, Any]],
    variant_keys_by_model: Mapping[str, str],
) -> list[dict[str, Any]]:
    product_facts = _mapping(payload.get("product_facts"), "product_facts")
    explicit = product_facts.get("shopee_global_variant_image_positions")
    explicit_by_model: dict[str, int] = {}
    if explicit is not None:
        if type(explicit) is not list:
            raise ApprovedPublicationSnapshotError(
                "Shopee global variant image positions must be a list"
            )
        for raw in explicit:
            row = _mapping(raw, "Shopee global variant image position")
            if set(row) != {"model_sku", "position"}:
                raise ApprovedPublicationSnapshotError(
                    "Shopee global variant image position fields are invalid"
                )
            model = _text(row.get("model_sku"), "Shopee variant image model_sku")
            position = row.get("position")
            if (
                model not in model_skus
                or model in explicit_by_model
                or type(position) is not int
                or position < 0
                or position >= len(approved_product_images)
            ):
                raise ApprovedPublicationSnapshotError(
                    "Shopee global variant image positions conflict"
                )
            explicit_by_model[model] = position
        if set(explicit_by_model) != set(model_skus):
            raise ApprovedPublicationSnapshotError(
                "Shopee global variant image position coverage conflicts"
            )
    positions: list[dict[str, Any]] = []
    for model in model_skus:
        if model in explicit_by_model:
            position = explicit_by_model[model]
        elif len(model_skus) == 1:
            position = 0
        else:
            variant_key = variant_keys_by_model[model]
            images = sku_details_by_key[variant_key].get("image_urls")
            first = images[0] if type(images) is list and images else None
            matches = [
                index
                for index, image in enumerate(approved_product_images)
                if image == first
            ]
            if len(matches) != 1:
                raise ApprovedPublicationSnapshotError(
                    "Shopee global variant image position is unavailable"
                )
            position = matches[0]
        positions.append(
            {
                "model_sku": model,
                "position": position,
                "image_url": approved_product_images[position],
            }
        )
    return positions


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


def _normalized_pricing(
    value: Any,
    *,
    targets: list[str],
    model_skus: list[str],
) -> dict[str, Any]:
    pricing = _json_object(_mapping(value, "approved pricing"), "approved pricing")
    selected = _mapping(pricing.get("selected_targets"), "selected target pricing")
    if set(selected) != set(targets):
        raise ApprovedPublicationSnapshotError("pricing target coverage conflicts")
    expected_models = set(model_skus)
    normalized_targets: dict[str, dict[str, Any]] = {}
    for target in targets:
        target_row = _json_object(
            _mapping(selected[target], f"{target} pricing"),
            f"{target} pricing",
        )
        if target == "miaoshou:COMMON":
            target_row.pop("sku_prices", None)
            normalized_targets[target] = target_row
            continue
        raw_rows = target_row.get("sku_prices")
        if type(raw_rows) is not list:
            raise ApprovedPublicationSnapshotError(
                f"{target} SKU prices must be a list"
            )
        by_model: dict[str, dict[str, Any]] = {}
        for raw in raw_rows:
            row = _mapping(raw, f"{target} SKU price")
            model = _text(row.get("model_sku"), f"{target} model_sku")
            if model not in expected_models or model in by_model:
                raise ApprovedPublicationSnapshotError(
                    f"{target} SKU price identity conflicts"
                )
            derived = row.get("derived_preview")
            if row.get("list_price") is not None and row.get("currency") is not None:
                normalized = {
                    "model_sku": model,
                    "list_price": row.get("list_price"),
                    "currency": row.get("currency"),
                }
                if target.startswith("shopee:") and row.get(
                    "global_original_price_cny"
                ) is not None:
                    normalized["global_original_price_cny"] = row.get(
                        "global_original_price_cny"
                    )
                if target == "ozon:RU" and row.get("old_price_cny") is not None:
                    normalized["old_price_cny"] = row.get("old_price_cny")
            elif target.startswith("shopee:") and isinstance(derived, Mapping):
                normalized = {
                    "model_sku": model,
                    "list_price": derived.get("local_original_price"),
                    "currency": derived.get("source_currency"),
                    "global_original_price_cny": derived.get(
                        "global_original_price_cny"
                    ),
                }
            elif target == "ozon:RU" and isinstance(derived, Mapping):
                normalized = {
                    "model_sku": model,
                    "list_price": derived.get("price_cny"),
                    "currency": "CNY",
                    "old_price_cny": derived.get("old_price_cny"),
                }
            else:
                raise ApprovedPublicationSnapshotError(
                    f"{target} provider price facts are missing"
                )
            by_model[model] = normalized
        if set(by_model) != expected_models:
            raise ApprovedPublicationSnapshotError(
                f"{target} SKU price coverage conflicts"
            )
        target_row["sku_prices"] = [by_model[model] for model in model_skus]
        normalized_targets[target] = target_row
    pricing["selected_targets"] = normalized_targets
    return pricing


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


def _digest_text_with_prefix(value: Any, name: str) -> str:
    return "sha256:" + _digest_text(value, name).removeprefix("sha256:")


def _positive_decimal_text(value: Any, name: str) -> str:
    if type(value) not in {str, int, float} or isinstance(value, bool):
        raise ApprovedPublicationSnapshotError(f"{name} must be a positive decimal")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ApprovedPublicationSnapshotError(
            f"{name} must be a positive decimal"
        ) from None
    if not number.is_finite() or number <= 0:
        raise ApprovedPublicationSnapshotError(f"{name} must be a positive decimal")
    normalized = format(number.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _json_list(value: Any, name: str) -> list[Any]:
    if type(value) is not list:
        raise ApprovedPublicationSnapshotError(f"{name} must be a list")
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
    if type(copied) is not list or any(not isinstance(row, dict) for row in copied):
        raise ApprovedPublicationSnapshotError(f"{name} must contain objects")
    return copied


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
