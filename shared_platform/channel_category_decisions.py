"""Server-owned channel category observations and explicit local decisions.

The marketplace observer may recommend categories and return official
alternatives.  A recommendation is never an approval.  Only this module can
turn one exact offered identity digest into a Kyle-approved decision that may
be bound into an immutable release plan.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any
import unicodedata


OPTIONS_SCHEMA_VERSION = "channel-category-options/v2"
OBSERVER_SCHEMA_VERSION = "channel-category-options-observation/v2"
DECISION_SCHEMA_VERSION = "channel-category-decision/v2"
PLAN_BINDING_SCHEMA_VERSION = "channel-category-decision-binding/v2"
EXECUTION_SCHEMA_VERSION = "channel-category-decision-execution/v2"
PREVIEW_SCHEMA_VERSION = "channel-category-decision-preview/v2"
OBSERVER_REQUEST_SCHEMA_VERSION = "channel-category-observer-request/v2"
ATTRIBUTE_SELECTION_SCHEMA_VERSION = (
    "channel-category-attribute-selection/v1"
)
ATTRIBUTE_SELECTION_EXECUTION_SCHEMA_VERSION = (
    "channel-category-attribute-selection-execution/v1"
)

SHOPEE_CHANNEL = "shopee"
SHOPEE_NEW_GLOBAL = "NEW_GLOBAL"
SHOPEE_GLOBAL_TARGET = "shopee:GLOBAL"
SHOPEE_OFFICIAL_AUTHORITY = "shopee_official_category_get"


class ChannelCategoryDecisionError(ValueError):
    """The options, selection, or persisted decision is not exact."""


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ChannelCategoryDecisionError(
            "category decision must be canonical JSON"
        ) from error


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def seller_stock_source_digest(
    *,
    context_digest: object,
    creation_fact_identity_digest: object,
    location_identity_digest: object,
    quantity: object,
) -> str:
    """Return the one server-owned digest for an explicit seller-stock fact."""

    return digest_json(
        {
            "schema_version": "kyle-explicit-seller-stock-decision/v1",
            "context_digest": _digest(
                context_digest,
                "seller stock context digest",
            ),
            "creation_fact_identity_digest": _digest(
                creation_fact_identity_digest,
                "seller stock creation fact identity digest",
            ),
            "location_identity_digest": _digest(
                location_identity_digest,
                "seller stock location identity digest",
            ),
            "quantity": _positive_int(
                quantity,
                "seller stock quantity",
            ),
        }
    )


def is_digest(value: object) -> bool:
    return bool(
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def build_category_options(
    observed: object,
    *,
    context: Mapping[str, object],
    creation_seed: Mapping[str, object],
) -> dict[str, Any]:
    """Validate one strict official observation and derive offered identities."""

    context_payload = _category_context(context)
    value = _exact_mapping(
        observed,
        {
            "schema_version",
            "channel",
            "mode",
            "authority",
            "recommendation_source",
            "recommended_category_id",
            "options",
            "brand_options",
            "location_options",
            "creation_defaults",
        },
        "category options observation",
    )
    if (
        value["schema_version"] != OBSERVER_SCHEMA_VERSION
        or value["channel"] != SHOPEE_CHANNEL
        or value["mode"] != SHOPEE_NEW_GLOBAL
        or value["authority"] != SHOPEE_OFFICIAL_AUTHORITY
    ):
        raise ChannelCategoryDecisionError(
            "category options observation authority is invalid"
        )
    recommendation = _exact_mapping(
        value["recommendation_source"],
        {"authority", "evidence_digest"},
        "category recommendation source",
    )
    recommendation_authority = _nonempty_text(
        recommendation["authority"],
        "recommendation authority",
    )
    recommendation_evidence_digest = _digest(
        recommendation["evidence_digest"],
        "recommendation evidence digest",
    )
    recommended_category_id = _positive_int(
        value["recommended_category_id"],
        "recommended category ID",
    )
    rows = value["options"]
    if (
        type(rows) is not list
        or not rows
        or any(not isinstance(row, Mapping) for row in rows)
    ):
        raise ChannelCategoryDecisionError(
            "official category alternatives are invalid"
        )
    options = [
        _category_option(
            row,
            recommendation_authority=recommendation_authority,
            recommendation_evidence_digest=(
                recommendation_evidence_digest
            ),
            recommended_category_id=recommended_category_id,
        )
        for row in rows
    ]
    category_ids = [row["category_id"] for row in options]
    identities = [row["category_identity_digest"] for row in options]
    if (
        len(category_ids) != len(set(category_ids))
        or len(identities) != len(set(identities))
        or category_ids.count(recommended_category_id) != 1
    ):
        raise ChannelCategoryDecisionError(
            "official category alternatives are ambiguous"
        )
    options.sort(key=lambda row: row["category_identity_digest"])
    recommended = next(
        row
        for row in options
        if row["category_id"] == recommended_category_id
    )
    brands = _brand_options(value["brand_options"])
    locations = _location_options(value["location_options"])
    defaults = _creation_defaults(value["creation_defaults"])
    normalized_creation_seed = _creation_seed(creation_seed)
    creation_fact_option = _creation_fact_option(
        defaults,
        normalized_creation_seed,
    )
    payload: dict[str, Any] = {
        "schema_version": OPTIONS_SCHEMA_VERSION,
        "channel": SHOPEE_CHANNEL,
        "mode": SHOPEE_NEW_GLOBAL,
        "authority": SHOPEE_OFFICIAL_AUTHORITY,
        "context": context_payload,
        "context_digest": digest_json(context_payload),
        "recommendation_source": {
            "authority": recommendation_authority,
            "evidence_digest": recommendation_evidence_digest,
        },
        "recommended_category_identity_digest": recommended[
            "category_identity_digest"
        ],
        "options": options,
        "brand_options": brands,
        "location_options": locations,
        "creation_defaults": defaults,
        "creation_seed": normalized_creation_seed,
        "creation_fact_option": creation_fact_option,
    }
    payload["options_digest"] = digest_json(payload)
    return payload


def blocked_category_options(
    *,
    context: Mapping[str, object],
    reason_code: str,
    reason_category: str = "CAPABILITY",
    next_action: str = "wait_for_channel_capability",
) -> dict[str, Any]:
    """Return a redacted, non-approvable capability projection."""

    context_payload = _category_context(context)
    clean_code = _nonempty_text(reason_code, "reason code")
    return {
        "schema_version": OPTIONS_SCHEMA_VERSION,
        "channel": SHOPEE_CHANNEL,
        "mode": SHOPEE_NEW_GLOBAL,
        "status": "BLOCKED_CAPABILITY",
        "context_digest": digest_json(context_payload),
        "options_digest": None,
        "recommended_category_identity_digest": None,
        "recommendation_source": None,
        "options": [],
        "brand_options": [],
        "location_options": [],
        "creation_defaults": None,
        "creation_seed": None,
        "creation_fact_option": None,
        "reason": {
            "category": _nonempty_text(
                reason_category,
                "reason category",
            ),
            "code": clean_code,
        },
        "next_action": {
            "action": _nonempty_text(next_action, "next action"),
            "target_focus": SHOPEE_GLOBAL_TARGET,
        },
    }


def approve_category_decision(
    options: object,
    *,
    product_id: object,
    product_revision: object,
    selected_category_identity_digest: object,
    selected_brand_identity_digest: object,
    selected_location_identity_digest: object,
    selected_creation_fact_identity_digest: object,
    attribute_selection_digest: object,
    approved_by: object,
    confirm_channel_category_selection: object,
    confirm_seller_stock_quantity: object,
    confirm_condition_and_preorder: object,
) -> dict[str, Any]:
    """Approve one exact offered option; never infer the recommendation."""

    snapshot = validate_category_options(options)
    if approved_by != "Kyle":
        raise ChannelCategoryDecisionError("approved_by must be Kyle")
    if confirm_channel_category_selection is not True:
        raise ChannelCategoryDecisionError(
            "literal confirm_channel_category_selection=true is required"
        )
    if (
        confirm_seller_stock_quantity is not True
        or confirm_condition_and_preorder is not True
    ):
        raise ChannelCategoryDecisionError(
            "literal stock and condition consent is required"
        )
    clean_product_id = _digits(product_id, "product ID")
    if type(product_revision) is not int or product_revision < 0:
        raise ChannelCategoryDecisionError(
            "product revision must be a non-negative int"
        )
    if (
        snapshot["context"]["product_id"] != clean_product_id
        or snapshot["context"]["product_revision"] != product_revision
    ):
        raise ChannelCategoryDecisionError(
            "category decision product identity changed"
        )
    selected_digest = _digest(
        selected_category_identity_digest,
        "selected category identity digest",
    )
    selected = [
        row
        for row in snapshot["options"]
        if row["category_identity_digest"] == selected_digest
    ]
    if len(selected) != 1:
        raise ChannelCategoryDecisionError(
            "selected category was not offered by the current observation"
        )
    option = selected[0]
    if option["approval_ready"] is not True:
        raise ChannelCategoryDecisionError(
            "selected category lacks official attribute tree or required values"
        )
    selected_brand_digest = _digest(
        selected_brand_identity_digest,
        "selected brand identity digest",
    )
    selected_location_digest = _digest(
        selected_location_identity_digest,
        "selected location identity digest",
    )
    selected_creation_digest = _digest(
        selected_creation_fact_identity_digest,
        "selected creation fact identity digest",
    )
    brands = [
        row
        for row in snapshot["brand_options"]
        if row["brand_identity_digest"] == selected_brand_digest
    ]
    locations = [
        row
        for row in snapshot["location_options"]
        if row["location_identity_digest"] == selected_location_digest
    ]
    creation_fact = snapshot["creation_fact_option"]
    if (
        len(brands) != 1
        or len(locations) != 1
        or creation_fact["creation_fact_identity_digest"]
        != selected_creation_digest
    ):
        raise ChannelCategoryDecisionError(
            "selected NEW_GLOBAL create facts were not offered"
        )
    brand = brands[0]
    location = locations[0]
    recommended_brands = [
        row for row in snapshot["brand_options"] if row["recommended"] is True
    ]
    recommended_locations = [
        row
        for row in snapshot["location_options"]
        if row["recommended"] is True
    ]
    if (
        len(recommended_brands) != 1
        or len(recommended_locations) != 1
        or brand["brand_identity_digest"]
        != recommended_brands[0]["brand_identity_digest"]
        or location["location_identity_digest"]
        != recommended_locations[0]["location_identity_digest"]
    ):
        raise ChannelCategoryDecisionError(
            "fixed Shopee brand or seller location policy was not selected"
        )
    clean_attribute_selection_digest = _digest(
        attribute_selection_digest,
        "attribute selection digest",
    )
    seller_stock = {
        "source": "kyle-explicit-seller-stock/v1",
        "source_digest": seller_stock_source_digest(
            context_digest=snapshot["context_digest"],
            creation_fact_identity_digest=selected_creation_digest,
            location_identity_digest=selected_location_digest,
            quantity=creation_fact["seller_stock_quantity"],
        ),
        "quantity": creation_fact["seller_stock_quantity"],
        "approval_reference": selected_creation_digest,
    }
    payload: dict[str, Any] = {
        "schema_version": DECISION_SCHEMA_VERSION,
        "product_id": clean_product_id,
        "product_revision": product_revision,
        "channel": snapshot["channel"],
        "mode": snapshot["mode"],
        "context_digest": snapshot["context_digest"],
        "options_digest": snapshot["options_digest"],
        "selected_category_identity_digest": selected_digest,
        "recommended_category_identity_digest": snapshot[
            "recommended_category_identity_digest"
        ],
        "selected_is_recommended": (
            selected_digest
            == snapshot["recommended_category_identity_digest"]
        ),
        "recommendation_source": dict(
            snapshot["recommendation_source"]
        ),
        "selected_category": {
            "category_id": option["category_id"],
            "name": option["name"],
            "path": [dict(row) for row in option["path"]],
            "path_complete": True,
            "evidence_digest": option["category_evidence_digest"],
        },
        "selected_attributes": [
            _copy_json(row) for row in option["selected_attributes"]
        ],
        "attributes_complete": True,
        "attribute_tree_digest": option["attribute_tree_digest"],
        "attribute_selection_digest": (
            clean_attribute_selection_digest
        ),
        "required_attribute_count": option["required_attribute_count"],
        "required_values_complete": True,
        "selected_brand_identity_digest": selected_brand_digest,
        "recommended_brand_identity_digest": next(
            (
                row["brand_identity_digest"]
                for row in snapshot["brand_options"]
                if row["recommended"] is True
            ),
            None,
        ),
        "selected_brand": _copy_json(brand),
        "selected_location_identity_digest": selected_location_digest,
        "recommended_location_identity_digest": next(
            (
                row["location_identity_digest"]
                for row in snapshot["location_options"]
                if row["recommended"] is True
            ),
            None,
        ),
        "selected_location": _copy_json(location),
        "selected_creation_fact_identity_digest": (
            selected_creation_digest
        ),
        "seller_stock": seller_stock,
        "condition": creation_fact["condition"],
        "preorder": _copy_json(creation_fact["preorder"]),
        "tier_variation": _copy_json(
            creation_fact["tier_variation"]
        ),
        "global_model": _copy_json(creation_fact["global_model"]),
        "creation_fact_option_evidence_digest": creation_fact[
            "option_evidence_digest"
        ],
        "creation_defaults_evidence_digest": creation_fact[
            "creation_defaults_evidence_digest"
        ],
        "stock_quantity_confirmed": True,
        "condition_and_preorder_confirmed": True,
        "approved_by": "Kyle",
    }
    payload["decision_digest"] = digest_json(payload)
    return validate_category_decision(payload)


def validate_category_options(value: object) -> dict[str, Any]:
    """Recompute a complete internal options snapshot."""

    snapshot = _exact_mapping(
        value,
        {
            "schema_version",
            "channel",
            "mode",
            "authority",
            "context",
            "context_digest",
            "recommendation_source",
            "recommended_category_identity_digest",
            "options",
            "brand_options",
            "location_options",
            "creation_defaults",
            "creation_seed",
            "creation_fact_option",
            "options_digest",
        },
        "category options",
    )
    rebuilt = build_category_options(
        {
            "schema_version": OBSERVER_SCHEMA_VERSION,
            "channel": snapshot["channel"],
            "mode": snapshot["mode"],
            "authority": snapshot["authority"],
            "recommendation_source": snapshot[
                "recommendation_source"
            ],
            "recommended_category_id": next(
                row["category_id"]
                for row in snapshot["options"]
                if row["category_identity_digest"]
                == snapshot["recommended_category_identity_digest"]
            ),
            "options": [
                {
                    "category_id": row["category_id"],
                    "name": row["name"],
                    "path": row["path"],
                    "path_complete": row["path_complete"],
                    "category_evidence_digest": row[
                        "category_evidence_digest"
                    ],
                    "selected_attributes": row["selected_attributes"],
                    "attributes_complete": row["attributes_complete"],
                    "attribute_tree_digest": row[
                        "attribute_tree_digest"
                    ],
                    "required_attribute_count": row[
                        "required_attribute_count"
                    ],
                    "required_values_complete": row[
                        "required_values_complete"
                    ],
                    "missing_required_attributes": [
                        {
                            "attribute_id": missing["attribute_id"],
                            "label": missing["label"],
                            "selection_kind": missing[
                                "selection_kind"
                            ],
                            "option_values": [
                                {
                                    "value_id": value["value_id"],
                                    "original_value_name": value[
                                        "original_value_name"
                                    ],
                                    "recommended": value[
                                        "recommended"
                                    ],
                                }
                                for value in missing["option_values"]
                            ],
                            "text_value_id": missing[
                                "text_value_id"
                            ],
                        }
                        for missing in row[
                            "missing_required_attributes"
                        ]
                    ],
                }
                for row in snapshot["options"]
            ],
            "brand_options": [
                {
                    "brand_id": row["brand_id"],
                    "original_brand_name": row[
                        "original_brand_name"
                    ],
                    "evidence_digest": row["evidence_digest"],
                    "recommended": row["recommended"],
                }
                for row in snapshot["brand_options"]
            ],
            "location_options": [
                {
                    "location_id": row["location_id"],
                    "display_name": row["display_name"],
                    "evidence_digest": row["evidence_digest"],
                    "recommended": row["recommended"],
                }
                for row in snapshot["location_options"]
            ],
            "creation_defaults": snapshot["creation_defaults"],
        },
        context=snapshot["context"],
        creation_seed=snapshot["creation_seed"],
    )
    if rebuilt != dict(snapshot):
        raise ChannelCategoryDecisionError(
            "category options snapshot digest or content drifted"
        )
    return rebuilt


def validate_category_decision(value: object) -> dict[str, Any]:
    """Strictly reconstruct an immutable approved decision."""

    required = {
        "schema_version",
        "product_id",
        "product_revision",
        "channel",
        "mode",
        "context_digest",
        "options_digest",
        "selected_category_identity_digest",
        "recommended_category_identity_digest",
        "selected_is_recommended",
        "recommendation_source",
        "selected_category",
        "selected_attributes",
        "attributes_complete",
        "attribute_tree_digest",
        "attribute_selection_digest",
        "required_attribute_count",
        "required_values_complete",
        "selected_brand_identity_digest",
        "recommended_brand_identity_digest",
        "selected_brand",
        "selected_location_identity_digest",
        "recommended_location_identity_digest",
        "selected_location",
        "selected_creation_fact_identity_digest",
        "seller_stock",
        "condition",
        "preorder",
        "tier_variation",
        "global_model",
        "creation_fact_option_evidence_digest",
        "creation_defaults_evidence_digest",
        "stock_quantity_confirmed",
        "condition_and_preorder_confirmed",
        "approved_by",
        "decision_digest",
    }
    decision = _exact_mapping(value, required, "category decision")
    if (
        decision["schema_version"] != DECISION_SCHEMA_VERSION
        or decision["channel"] != SHOPEE_CHANNEL
        or decision["mode"] != SHOPEE_NEW_GLOBAL
        or decision["approved_by"] != "Kyle"
        or type(decision["product_revision"]) is not int
        or decision["product_revision"] < 0
        or type(decision["selected_is_recommended"]) is not bool
        or decision["attributes_complete"] is not True
        or decision["required_values_complete"] is not True
        or decision["stock_quantity_confirmed"] is not True
        or decision["condition_and_preorder_confirmed"] is not True
    ):
        raise ChannelCategoryDecisionError(
            "category decision identity is invalid"
        )
    _digits(decision["product_id"], "product ID")
    for field in (
        "context_digest",
        "options_digest",
        "selected_category_identity_digest",
        "recommended_category_identity_digest",
        "attribute_tree_digest",
        "attribute_selection_digest",
        "selected_brand_identity_digest",
        "selected_location_identity_digest",
        "selected_creation_fact_identity_digest",
        "creation_fact_option_evidence_digest",
        "creation_defaults_evidence_digest",
        "decision_digest",
    ):
        _digest(decision[field], field)
    for field in (
        "recommended_brand_identity_digest",
        "recommended_location_identity_digest",
    ):
        _digest(decision[field], field)
    if (
        type(decision["required_attribute_count"]) is not int
        or decision["required_attribute_count"] < 0
    ):
        raise ChannelCategoryDecisionError(
            "required attribute count is invalid"
        )
    category = _category_payload(decision["selected_category"])
    attributes = _selected_attributes(
        decision["selected_attributes"],
        require_nonempty=decision["required_attribute_count"] > 0,
    )
    recommendation = _exact_mapping(
        decision["recommendation_source"],
        {"authority", "evidence_digest"},
        "recommendation source",
    )
    _nonempty_text(recommendation["authority"], "recommendation authority")
    _digest(recommendation["evidence_digest"], "recommendation evidence")
    brand = _selected_brand(decision["selected_brand"])
    location = _selected_location(decision["selected_location"])
    seller_stock = _seller_stock(decision["seller_stock"])
    condition = _condition(decision["condition"])
    preorder = _preorder(decision["preorder"])
    creation = _creation_plan_payload(
        seller_stock_quantity=seller_stock["quantity"],
        condition=condition,
        preorder=preorder,
        tier_variation=decision["tier_variation"],
        global_model=decision["global_model"],
    )
    canonical = {
        **dict(decision),
        "selected_category": category,
        "selected_attributes": attributes,
        "recommendation_source": dict(recommendation),
        "selected_brand": brand,
        "selected_location": location,
        "seller_stock": seller_stock,
        "condition": condition,
        "preorder": preorder,
        "tier_variation": creation["tier_variation"],
        "global_model": creation["global_model"],
    }
    selected_identity_payload = {
        "schema_version": "channel-category-option-identity/v1",
        "channel": decision["channel"],
        "mode": decision["mode"],
        "category": category,
        "recommendation_authority": recommendation["authority"],
        "recommendation_evidence_digest": recommendation[
            "evidence_digest"
        ],
    }
    if (
        digest_json(selected_identity_payload)
        != decision["selected_category_identity_digest"]
        or decision["selected_is_recommended"]
        != (
            decision["selected_category_identity_digest"]
            == decision["recommended_category_identity_digest"]
        )
    ):
        raise ChannelCategoryDecisionError(
            "selected category identity is not truthful"
        )
    brand_identity = _brand_identity(brand)
    location_identity = _location_identity(location)
    creation_identity = _creation_fact_identity(
        {
            "seller_stock_quantity": seller_stock["quantity"],
            "condition": condition,
            "preorder": preorder,
            "tier_variation": creation["tier_variation"],
            "global_model": creation["global_model"],
            "creation_defaults_evidence_digest": (
                decision["creation_defaults_evidence_digest"]
            ),
        }
    )
    if (
        creation_identity["option_evidence_digest"]
        != decision["creation_fact_option_evidence_digest"]
    ):
        raise ChannelCategoryDecisionError(
            "creation fact option evidence drifted"
        )
    if seller_stock["source_digest"] != seller_stock_source_digest(
        context_digest=decision["context_digest"],
        creation_fact_identity_digest=decision[
            "selected_creation_fact_identity_digest"
        ],
        location_identity_digest=decision[
            "selected_location_identity_digest"
        ],
        quantity=seller_stock["quantity"],
    ):
        raise ChannelCategoryDecisionError(
            "seller stock source digest drifted"
        )
    if (
        brand_identity["brand_identity_digest"]
        != decision["selected_brand_identity_digest"]
        or location_identity["location_identity_digest"]
        != decision["selected_location_identity_digest"]
        or creation_identity["creation_fact_identity_digest"]
        != decision["selected_creation_fact_identity_digest"]
        or decision["selected_brand_identity_digest"]
        != decision["recommended_brand_identity_digest"]
        or decision["selected_location_identity_digest"]
        != decision["recommended_location_identity_digest"]
        or brand["recommended"]
        != (
            decision["selected_brand_identity_digest"]
            == decision["recommended_brand_identity_digest"]
        )
        or location["recommended"]
        != (
            decision["selected_location_identity_digest"]
            == decision["recommended_location_identity_digest"]
        )
    ):
        raise ChannelCategoryDecisionError(
            "selected NEW_GLOBAL create-fact identity is not truthful"
        )
    supplied_digest = canonical.pop("decision_digest")
    canonical["decision_digest"] = digest_json(canonical)
    if canonical["decision_digest"] != supplied_digest:
        raise ChannelCategoryDecisionError(
            "category decision digest drifted"
        )
    return canonical


def category_decision_plan_binding(value: object) -> dict[str, Any]:
    decision = validate_category_decision(value)
    binding = {
        "schema_version": PLAN_BINDING_SCHEMA_VERSION,
        "channel": decision["channel"],
        "mode": decision["mode"],
        "decision_digest": decision["decision_digest"],
        "context_digest": decision["context_digest"],
        "options_digest": decision["options_digest"],
        "selected_category_identity_digest": decision[
            "selected_category_identity_digest"
        ],
        "attribute_tree_digest": decision["attribute_tree_digest"],
        "attribute_selection_digest": decision[
            "attribute_selection_digest"
        ],
        "selected_attributes_digest": digest_json(
            decision["selected_attributes"]
        ),
        "selected_brand_identity_digest": decision[
            "selected_brand_identity_digest"
        ],
        "selected_location_identity_digest": decision[
            "selected_location_identity_digest"
        ],
        "selected_creation_fact_identity_digest": decision[
            "selected_creation_fact_identity_digest"
        ],
        "approved_by": "Kyle",
    }
    binding["binding_digest"] = digest_json(binding)
    return binding


def category_decision_execution_payload(value: object) -> dict[str, Any]:
    """Return the exact server-internal facts a channel observer may consume."""

    decision = validate_category_decision(value)
    selected_category = decision["selected_category"]
    selected_brand = decision["selected_brand"]
    selected_location = decision["selected_location"]
    return {
        "schema_version": EXECUTION_SCHEMA_VERSION,
        "decision_digest": decision["decision_digest"],
        "context_digest": decision["context_digest"],
        "options_digest": decision["options_digest"],
        "selected_category_identity_digest": decision[
            "selected_category_identity_digest"
        ],
        "category": {
            "category_id": selected_category["category_id"],
            "path": _copy_json(selected_category["path"]),
            "path_complete": True,
            "evidence_digest": selected_category["evidence_digest"],
        },
        "attribute_list": _copy_json(
            decision["selected_attributes"]
        ),
        "attributes_complete": True,
        "attribute_tree_digest": decision["attribute_tree_digest"],
        "attribute_selection_digest": decision[
            "attribute_selection_digest"
        ],
        "brand": {
            "brand_id": selected_brand["brand_id"],
            "original_brand_name": selected_brand[
                "original_brand_name"
            ],
            "evidence_digest": selected_brand["evidence_digest"],
        },
        "seller_stock": _copy_json(decision["seller_stock"]),
        "location": {
            "location_id": selected_location["location_id"],
            "evidence_digest": selected_location["evidence_digest"],
        },
        "condition": decision["condition"],
        "preorder": _copy_json(decision["preorder"]),
        "tier_variation": _copy_json(decision["tier_variation"]),
        "global_model": _copy_json(decision["global_model"]),
    }


def public_options_projection(
    options: object,
    *,
    decision: object | None = None,
) -> dict[str, Any]:
    snapshot = validate_category_options(options)
    current = (
        validate_category_decision(decision)
        if decision is not None
        else None
    )
    rows = []
    for option in snapshot["options"]:
        rows.append(
            {
                "category_identity_digest": option[
                    "category_identity_digest"
                ],
                "display_name": option["name"],
                "path_labels": [row["name"] for row in option["path"]],
                "recommended": (
                    option["category_identity_digest"]
                    == snapshot[
                        "recommended_category_identity_digest"
                    ]
                ),
                "approval_ready": option["approval_ready"],
                "attribute_status": (
                    "READY"
                    if option["approval_ready"]
                    else "BLOCKED_REQUIRED_VALUES"
                ),
                "required_attribute_count": option[
                    "required_attribute_count"
                ],
                "selected_attribute_count": len(
                    option["selected_attributes"]
                ),
                "missing_required_attributes": [
                    {
                        "attribute_identity_digest": row[
                            "attribute_identity_digest"
                        ],
                        "label": row["label"],
                        "selection_kind": row["selection_kind"],
                        "option_values": [
                            {
                                "option_identity_digest": value[
                                    "option_identity_digest"
                                ],
                                "display_label": value[
                                    "original_value_name"
                                ],
                                "recommended": value["recommended"],
                            }
                            for value in row["option_values"]
                        ],
                    }
                    for row in option["missing_required_attributes"]
                ],
                "attribute_tree_digest": option[
                    "attribute_tree_digest"
                ],
                "option_evidence_digest": option[
                    "option_evidence_digest"
                ],
            }
        )
    selection = None
    if (
        current is not None
        and current["context_digest"] == snapshot["context_digest"]
        and current["options_digest"] == snapshot["options_digest"]
    ):
        selection = {
            "decision_digest": current["decision_digest"],
            "selected_category_identity_digest": current[
                "selected_category_identity_digest"
            ],
            "selected_is_recommended": current[
                "selected_is_recommended"
            ],
            "attribute_tree_digest": current[
                "attribute_tree_digest"
            ],
            "approved_by": "Kyle",
            "selected_brand": {
                "brand_identity_digest": current[
                    "selected_brand_identity_digest"
                ],
                "display_name": current["selected_brand"][
                    "original_brand_name"
                ],
                "selected_is_recommended": (
                    current["recommended_brand_identity_digest"]
                    is not None
                    and
                    current["selected_brand_identity_digest"]
                    == current["recommended_brand_identity_digest"]
                ),
            },
            "selected_location": {
                "location_identity_digest": current[
                    "selected_location_identity_digest"
                ],
                "display_name": current["selected_location"][
                    "display_name"
                ],
                "selected_is_recommended": (
                    current["recommended_location_identity_digest"]
                    is not None
                    and
                    current["selected_location_identity_digest"]
                    == current[
                        "recommended_location_identity_digest"
                    ]
                ),
            },
            "creation_fact_identity_digest": current[
                "selected_creation_fact_identity_digest"
            ],
            "attribute_selection_digest": current[
                "attribute_selection_digest"
            ],
            "seller_stock_quantity": current["seller_stock"][
                "quantity"
            ],
            "condition": current["condition"],
            "preorder": _copy_json(current["preorder"]),
            "variation_summary": _variation_summary(
                current["tier_variation"],
                current["global_model"],
            ),
        }
    ready_count = sum(row["approval_ready"] is True for row in rows)
    return {
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "status": (
            "SELECTED"
            if selection is not None
            else (
                "READY_FOR_SELECTION"
                if ready_count > 0
                else "BLOCKED_CAPABILITY"
            )
        ),
        "target_label": SHOPEE_GLOBAL_TARGET,
        "mode": SHOPEE_NEW_GLOBAL,
        "options_digest": snapshot["options_digest"],
        "recommendation": {
            "source": dict(snapshot["recommendation_source"]),
            "category_identity_digest": snapshot[
                "recommended_category_identity_digest"
            ],
        },
        "options": rows,
        "brand_options": [
            {
                "brand_identity_digest": row[
                    "brand_identity_digest"
                ],
                "display_name": row["original_brand_name"],
                "recommended": row["recommended"],
                "option_evidence_digest": row[
                    "option_evidence_digest"
                ],
            }
            for row in snapshot["brand_options"]
        ],
        "location_options": [
            {
                "location_identity_digest": row[
                    "location_identity_digest"
                ],
                "display_name": row["display_name"],
                "recommended": row["recommended"],
                "option_evidence_digest": row[
                    "option_evidence_digest"
                ],
            }
            for row in snapshot["location_options"]
        ],
        "creation_fact_option": {
            "creation_fact_identity_digest": snapshot[
                "creation_fact_option"
            ]["creation_fact_identity_digest"],
            "seller_stock_quantity": snapshot[
                "creation_fact_option"
            ]["seller_stock_quantity"],
            "condition": snapshot["creation_fact_option"]["condition"],
            "preorder": _copy_json(
                snapshot["creation_fact_option"]["preorder"]
            ),
            "variation_summary": _copy_json(
                snapshot["creation_fact_option"][
                    "variation_summary"
                ]
            ),
            "recommended": True,
            "option_evidence_digest": snapshot[
                "creation_fact_option"
            ]["option_evidence_digest"],
        },
        "selection": selection,
        "blocker": None,
        "next_action": {
            "action": (
                "review_shopee_global_plan"
                if selection is not None
                else (
                    "select_channel_category"
                    if ready_count > 0
                    else "complete_official_category_attributes"
                )
            ),
            "target_focus": SHOPEE_GLOBAL_TARGET,
        },
    }


def resolve_required_attribute_selections(
    options: object,
    *,
    selected_category_identity_digest: object,
    selected_brand_identity_digest: object,
    selected_location_identity_digest: object,
    selected_creation_fact_identity_digest: object,
    required_attribute_selections: object,
    approval_request_digest: object,
    approved_by: object,
    confirm_channel_category_selection: object,
    confirm_seller_stock_quantity: object,
    confirm_condition_and_preorder: object,
    confirm_required_attribute_selections: object,
) -> dict[str, Any]:
    """Resolve only offered opaque values into a durable Kyle intent."""

    snapshot = validate_category_options(options)
    if (
        approved_by != "Kyle"
        or confirm_channel_category_selection is not True
        or confirm_seller_stock_quantity is not True
        or confirm_condition_and_preorder is not True
        or confirm_required_attribute_selections is not True
    ):
        raise ChannelCategoryDecisionError(
            "explicit Kyle attribute selection consent is required"
        )
    category_digest = _digest(
        selected_category_identity_digest,
        "selected category identity digest",
    )
    matches = [
        row
        for row in snapshot["options"]
        if row["category_identity_digest"] == category_digest
    ]
    if len(matches) != 1:
        raise ChannelCategoryDecisionError(
            "selected category was not offered"
        )
    option = matches[0]
    brand_digest = _digest(
        selected_brand_identity_digest,
        "selected brand identity digest",
    )
    location_digest = _digest(
        selected_location_identity_digest,
        "selected location identity digest",
    )
    creation_digest = _digest(
        selected_creation_fact_identity_digest,
        "selected creation fact identity digest",
    )
    if (
        sum(
            row["brand_identity_digest"] == brand_digest
            for row in snapshot["brand_options"]
        )
        != 1
        or sum(
            row["location_identity_digest"] == location_digest
            for row in snapshot["location_options"]
        )
        != 1
        or snapshot["creation_fact_option"][
            "creation_fact_identity_digest"
        ]
        != creation_digest
    ):
        raise ChannelCategoryDecisionError(
            "selected NEW_GLOBAL approval intent was not offered"
        )
    missing = option["missing_required_attributes"]
    if (
        type(required_attribute_selections) is not list
        or any(
            not isinstance(row, Mapping)
            for row in required_attribute_selections
        )
    ):
        raise ChannelCategoryDecisionError(
            "required attribute selections are invalid"
        )
    resolved = []
    seen_attributes = set()
    for raw_selection in required_attribute_selections:
        selection = _exact_mapping(
            raw_selection,
            {
                "attribute_identity_digest",
                "selection_kind",
                "selected_option_identity_digests",
                "text_value",
                "confirm_attribute_selection",
            },
            "required attribute selection",
        )
        if selection["confirm_attribute_selection"] is not True:
            raise ChannelCategoryDecisionError(
                "required attribute selection consent is missing"
            )
        identity = _digest(
            selection["attribute_identity_digest"],
            "required attribute identity digest",
        )
        offered = [
            row
            for row in missing
            if row["attribute_identity_digest"] == identity
        ]
        if len(offered) != 1 or identity in seen_attributes:
            raise ChannelCategoryDecisionError(
                "required attribute selection was not offered"
            )
        seen_attributes.add(identity)
        attribute = offered[0]
        if selection["selection_kind"] != attribute["selection_kind"]:
            raise ChannelCategoryDecisionError(
                "required attribute selection kind drifted"
            )
        option_digests = selection[
            "selected_option_identity_digests"
        ]
        if type(option_digests) is not list or any(
            not is_digest(value) for value in option_digests
        ):
            raise ChannelCategoryDecisionError(
                "required attribute option selection is invalid"
            )
        selected_values = []
        if attribute["selection_kind"] == "TEXT":
            if option_digests or type(selection["text_value"]) is not str:
                raise ChannelCategoryDecisionError(
                    "TEXT attribute selection is invalid"
                )
            text = unicodedata.normalize(
                "NFC",
                selection["text_value"].strip(),
            )
            if (
                not 1 <= len(text) <= 120
                or any(
                    unicodedata.category(character).startswith("C")
                    for character in text
                )
            ):
                raise ChannelCategoryDecisionError(
                    "TEXT attribute value is invalid"
                )
            selected_values = [
                {
                    "value_id": attribute["text_value_id"],
                    "original_value_name": text,
                }
            ]
        else:
            if selection["text_value"] is not None:
                raise ChannelCategoryDecisionError(
                    "selectable attribute text must be null"
                )
            if (
                (
                    attribute["selection_kind"] == "SINGLE"
                    and len(option_digests) != 1
                )
                or (
                    attribute["selection_kind"] == "MULTI"
                    and not option_digests
                )
                or len(option_digests) != len(set(option_digests))
            ):
                raise ChannelCategoryDecisionError(
                    "required attribute option cardinality is invalid"
                )
            offered_values = {
                row["option_identity_digest"]: row
                for row in attribute["option_values"]
            }
            if any(
                digest not in offered_values for digest in option_digests
            ):
                raise ChannelCategoryDecisionError(
                    "required attribute option was not offered"
                )
            selected_values = [
                {
                    key: value
                    for key, value in offered_values[digest].items()
                    if key
                    in {
                        "value_id",
                        "original_value_name",
                        "value_unit",
                    }
                }
                for digest in option_digests
            ]
        resolved.append(
            {
                "attribute_id": attribute["attribute_id"],
                "attribute_value_list": selected_values,
            }
        )
    if seen_attributes != {
        row["attribute_identity_digest"] for row in missing
    }:
        raise ChannelCategoryDecisionError(
            "required attribute selections are incomplete"
        )
    selected_attributes = _selected_attributes(
        [*option["selected_attributes"], *resolved],
        require_nonempty=option["required_attribute_count"] > 0,
    )
    payload = {
        "schema_version": ATTRIBUTE_SELECTION_SCHEMA_VERSION,
        "product_id": snapshot["context"]["product_id"],
        "product_revision": snapshot["context"]["product_revision"],
        "channel": snapshot["channel"],
        "mode": snapshot["mode"],
        "context_digest": snapshot["context_digest"],
        "options_digest": snapshot["options_digest"],
        "category_identity_digest": category_digest,
        "selected_brand_identity_digest": brand_digest,
        "selected_location_identity_digest": location_digest,
        "selected_creation_fact_identity_digest": creation_digest,
        "attribute_tree_digest": option["attribute_tree_digest"],
        "selected_attributes": selected_attributes,
        "selection_count": len(required_attribute_selections),
        "approval_request_digest": _digest(
            approval_request_digest,
            "attribute approval request digest",
        ),
        "approved_by": "Kyle",
    }
    payload["selection_digest"] = digest_json(payload)
    return validate_attribute_selection(payload)


def validate_attribute_selection(value: object) -> dict[str, Any]:
    selection = _exact_mapping(
        value,
        {
            "schema_version",
            "product_id",
            "product_revision",
            "channel",
            "mode",
            "context_digest",
            "options_digest",
            "category_identity_digest",
            "selected_brand_identity_digest",
            "selected_location_identity_digest",
            "selected_creation_fact_identity_digest",
            "attribute_tree_digest",
            "selected_attributes",
            "selection_count",
            "approval_request_digest",
            "approved_by",
            "selection_digest",
        },
        "category attribute selection",
    )
    if (
        selection["schema_version"] != ATTRIBUTE_SELECTION_SCHEMA_VERSION
        or selection["channel"] != SHOPEE_CHANNEL
        or selection["mode"] != SHOPEE_NEW_GLOBAL
        or selection["approved_by"] != "Kyle"
        or type(selection["product_revision"]) is not int
        or selection["product_revision"] < 0
        or type(selection["selection_count"]) is not int
        or selection["selection_count"] < 0
    ):
        raise ChannelCategoryDecisionError(
            "category attribute selection identity is invalid"
        )
    _digits(selection["product_id"], "product ID")
    for field in (
        "context_digest",
        "options_digest",
        "category_identity_digest",
        "selected_brand_identity_digest",
        "selected_location_identity_digest",
        "selected_creation_fact_identity_digest",
        "attribute_tree_digest",
        "selection_digest",
        "approval_request_digest",
    ):
        _digest(selection[field], field)
    canonical = {
        **dict(selection),
        "selected_attributes": _selected_attributes(
            selection["selected_attributes"],
            require_nonempty=selection["selection_count"] > 0,
        ),
    }
    supplied = canonical.pop("selection_digest")
    canonical["selection_digest"] = digest_json(canonical)
    if canonical["selection_digest"] != supplied:
        raise ChannelCategoryDecisionError(
            "category attribute selection digest drifted"
        )
    return canonical


def attribute_selection_execution_payload(value: object) -> dict[str, Any]:
    selection = validate_attribute_selection(value)
    return {
        "schema_version": (
            ATTRIBUTE_SELECTION_EXECUTION_SCHEMA_VERSION
        ),
        "product_id": selection["product_id"],
        "product_revision": selection["product_revision"],
        "channel": selection["channel"],
        "mode": selection["mode"],
        "selection_digest": selection["selection_digest"],
        "context_digest": selection["context_digest"],
        "options_digest": selection["options_digest"],
        "category_identity_digest": selection[
            "category_identity_digest"
        ],
        "selected_brand_identity_digest": selection[
            "selected_brand_identity_digest"
        ],
        "selected_location_identity_digest": selection[
            "selected_location_identity_digest"
        ],
        "selected_creation_fact_identity_digest": selection[
            "selected_creation_fact_identity_digest"
        ],
        "attribute_tree_digest": selection["attribute_tree_digest"],
        "selected_attributes": _copy_json(
            selection["selected_attributes"]
        ),
    }


def attribute_selection_matches_options(
    options: object,
    selection: object,
) -> bool:
    """Require an official recheck to echo the exact approved attributes."""

    try:
        snapshot = validate_category_options(options)
        selected = validate_attribute_selection(selection)
    except ChannelCategoryDecisionError:
        return False
    if (
        selected["product_id"] != snapshot["context"]["product_id"]
        or selected["product_revision"]
        != snapshot["context"]["product_revision"]
        or selected["channel"] != snapshot["channel"]
        or selected["mode"] != snapshot["mode"]
        or selected["context_digest"] != snapshot["context_digest"]
    ):
        return False
    rows = [
        row
        for row in snapshot["options"]
        if row["category_identity_digest"]
        == selected["category_identity_digest"]
    ]
    return bool(
        len(rows) == 1
        and rows[0]["attribute_tree_digest"]
        == selected["attribute_tree_digest"]
        and rows[0]["selected_attributes"]
        == selected["selected_attributes"]
        and rows[0]["attributes_complete"] is True
        and rows[0]["required_values_complete"] is True
        and rows[0]["missing_required_attributes"] == []
        and rows[0]["approval_ready"] is True
    )


def serialize_attribute_selection(value: object) -> str:
    return canonical_json(validate_attribute_selection(value))


def rehydrate_attribute_selection(value: object) -> dict[str, Any]:
    if type(value) is not str or not value:
        raise ChannelCategoryDecisionError(
            "category attribute selection record is invalid"
        )
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ChannelCategoryDecisionError(
            "category attribute selection record is invalid"
        ) from error
    if canonical_json(decoded) != value:
        raise ChannelCategoryDecisionError(
            "category attribute selection record is not canonical"
        )
    return validate_attribute_selection(decoded)


def serialize_category_decision(value: object) -> str:
    return canonical_json(validate_category_decision(value))


def rehydrate_category_decision(value: object) -> dict[str, Any]:
    if type(value) is not str or not value:
        raise ChannelCategoryDecisionError(
            "category decision record must be canonical JSON"
        )
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ChannelCategoryDecisionError(
            "category decision record is invalid JSON"
        ) from error
    if canonical_json(decoded) != value:
        raise ChannelCategoryDecisionError(
            "category decision record is not canonical JSON"
        )
    return validate_category_decision(decoded)


def decision_matches_global_plan(
    decision: object,
    plan_payload: object,
) -> bool:
    try:
        selected = validate_category_decision(decision)
    except ChannelCategoryDecisionError:
        return False
    if not isinstance(plan_payload, Mapping):
        return False
    execution = category_decision_execution_payload(selected)
    return bool(
        plan_payload.get("mode") == SHOPEE_NEW_GLOBAL
        and plan_payload.get("category") == execution["category"]
        and plan_payload.get("attribute_list")
        == execution["attribute_list"]
        and plan_payload.get("attributes_complete") is True
        and plan_payload.get("attribute_tree_digest")
        == selected["attribute_tree_digest"]
        and plan_payload.get("brand") == execution["brand"]
        and plan_payload.get("seller_stock")
        == execution["seller_stock"]
        and plan_payload.get("location") == execution["location"]
        and plan_payload.get("condition") == execution["condition"]
        and plan_payload.get("preorder") == execution["preorder"]
        and plan_payload.get("tier_variation")
        == execution["tier_variation"]
        and plan_payload.get("global_model")
        == execution["global_model"]
        and plan_payload.get("variations_complete") is True
    )


def _category_context(value: object) -> dict[str, Any]:
    context = _exact_mapping(
        value,
        {
            "schema_version",
            "product_id",
            "product_revision",
            "channel",
            "mode",
            "source_identity_digest",
            "sku_lineage_digest",
            "approved_copy_digest",
            "targets_digest",
        },
        "category context",
    )
    if (
        context["schema_version"] != OBSERVER_REQUEST_SCHEMA_VERSION
        or context["channel"] != SHOPEE_CHANNEL
        or context["mode"] != SHOPEE_NEW_GLOBAL
        or type(context["product_revision"]) is not int
        or context["product_revision"] < 0
    ):
        raise ChannelCategoryDecisionError(
            "category context identity is invalid"
        )
    _digits(context["product_id"], "product ID")
    for field in (
        "source_identity_digest",
        "sku_lineage_digest",
        "approved_copy_digest",
        "targets_digest",
    ):
        _digest(context[field], field)
    return dict(context)


def _category_option(
    value: Mapping[str, object],
    *,
    recommendation_authority: str,
    recommendation_evidence_digest: str,
    recommended_category_id: int,
) -> dict[str, Any]:
    option = _exact_mapping(
        value,
        {
            "category_id",
            "name",
            "path",
            "path_complete",
            "category_evidence_digest",
            "selected_attributes",
            "attributes_complete",
            "attribute_tree_digest",
            "required_attribute_count",
            "required_values_complete",
            "missing_required_attributes",
        },
        "category option",
    )
    category_id = _positive_int(option["category_id"], "category ID")
    name = _nonempty_text(option["name"], "category name")
    category = _category_payload(
        {
            "category_id": category_id,
            "name": name,
            "path": option["path"],
            "path_complete": option["path_complete"],
            "evidence_digest": option["category_evidence_digest"],
        }
    )
    attributes = _selected_attributes(
        option["selected_attributes"],
        require_nonempty=False,
    )
    missing_required = _missing_required_attributes(
        option["missing_required_attributes"]
    )
    required_count = option["required_attribute_count"]
    if type(required_count) is not int or required_count < 0:
        raise ChannelCategoryDecisionError(
            "required attribute count is invalid"
        )
    if (
        option["attributes_complete"] is not True
        and option["attributes_complete"] is not False
    ) or (
        option["required_values_complete"] is not True
        and option["required_values_complete"] is not False
    ):
        raise ChannelCategoryDecisionError(
            "attribute completeness flags must be literal booleans"
        )
    attribute_tree_digest = _digest(
        option["attribute_tree_digest"],
        "attribute tree digest",
    )
    identity_payload = {
        "schema_version": "channel-category-option-identity/v1",
        "channel": SHOPEE_CHANNEL,
        "mode": SHOPEE_NEW_GLOBAL,
        "category": category,
        "recommendation_authority": recommendation_authority,
        "recommendation_evidence_digest": (
            recommendation_evidence_digest
        ),
    }
    result = {
        "category_id": category_id,
        "name": name,
        "path": category["path"],
        "path_complete": True,
        "category_evidence_digest": category["evidence_digest"],
        "selected_attributes": attributes,
        "attributes_complete": option["attributes_complete"],
        "attribute_tree_digest": attribute_tree_digest,
        "required_attribute_count": required_count,
        "required_values_complete": option[
            "required_values_complete"
        ],
        "missing_required_attributes": missing_required,
        "recommended": category_id == recommended_category_id,
        "approval_ready": bool(
            option["attributes_complete"] is True
            and option["required_values_complete"] is True
            and not missing_required
            and (
                required_count == 0
                or len(attributes) >= required_count
            )
        ),
        "category_identity_digest": digest_json(identity_payload),
    }
    result["option_evidence_digest"] = digest_json(result)
    return result


def _category_payload(value: object) -> dict[str, Any]:
    category = _exact_mapping(
        value,
        {
            "category_id",
            "name",
            "path",
            "path_complete",
            "evidence_digest",
        },
        "selected category",
    )
    category_id = _positive_int(category["category_id"], "category ID")
    name = _nonempty_text(category["name"], "category name")
    path = category["path"]
    if (
        category["path_complete"] is not True
        or type(path) is not list
        or not path
        or any(not isinstance(row, Mapping) for row in path)
    ):
        raise ChannelCategoryDecisionError(
            "official category path is incomplete"
        )
    normalized_path = []
    seen = set()
    for row in path:
        item = _exact_mapping(
            row, {"category_id", "name"}, "category path row"
        )
        row_id = _positive_int(item["category_id"], "path category ID")
        if row_id in seen:
            raise ChannelCategoryDecisionError(
                "official category path is ambiguous"
            )
        seen.add(row_id)
        normalized_path.append(
            {
                "category_id": row_id,
                "name": _nonempty_text(item["name"], "path name"),
            }
        )
    if normalized_path[-1]["category_id"] != category_id:
        raise ChannelCategoryDecisionError(
            "official category path does not end at the category"
        )
    return {
        "category_id": category_id,
        "name": name,
        "path": normalized_path,
        "path_complete": True,
        "evidence_digest": _digest(
            category["evidence_digest"],
            "category evidence digest",
        ),
    }


def _selected_attributes(
    value: object,
    *,
    require_nonempty: bool,
) -> list[dict[str, Any]]:
    if (
        type(value) is not list
        or (require_nonempty and not value)
        or any(not isinstance(row, Mapping) for row in value)
    ):
        raise ChannelCategoryDecisionError(
            "selected official attributes are invalid"
        )
    result = []
    seen = set()
    for row in value:
        item = _exact_mapping(
            row,
            {"attribute_id", "attribute_value_list"},
            "selected attribute",
        )
        attribute_id = _positive_int(
            item["attribute_id"], "attribute ID"
        )
        values = item["attribute_value_list"]
        if (
            attribute_id in seen
            or type(values) is not list
            or not values
            or any(not isinstance(entry, Mapping) for entry in values)
        ):
            raise ChannelCategoryDecisionError(
                "selected official attributes are ambiguous"
            )
        seen.add(attribute_id)
        normalized_values = []
        value_identities = set()
        for entry in values:
            raw = _attribute_value(entry, "attribute value")
            value_identity = (
                raw["value_id"],
                raw["original_value_name"],
                raw.get("value_unit"),
            )
            if value_identity in value_identities:
                raise ChannelCategoryDecisionError(
                    "selected attribute values are duplicated"
                )
            value_identities.add(value_identity)
            normalized_values.append(raw)
        normalized_values.sort(
            key=lambda row: (
                row["value_id"],
                row["original_value_name"],
                row.get("value_unit") or "",
            )
        )
        result.append(
            {
                "attribute_id": attribute_id,
                "attribute_value_list": normalized_values,
            }
        )
    result.sort(key=lambda row: row["attribute_id"])
    return result


def _missing_required_attributes(value: object) -> list[dict[str, Any]]:
    if type(value) is not list or any(
        not isinstance(row, Mapping) for row in value
    ):
        raise ChannelCategoryDecisionError(
            "missing required attribute projection is invalid"
        )
    result = []
    seen = set()
    for row in value:
        item = _exact_mapping(
            row,
            {
                "attribute_id",
                "label",
                "selection_kind",
                "option_values",
                "text_value_id",
            },
            "missing required attribute",
        )
        attribute_id = _positive_int(
            item["attribute_id"], "required attribute ID"
        )
        if attribute_id in seen:
            raise ChannelCategoryDecisionError(
                "missing required attributes are ambiguous"
            )
        seen.add(attribute_id)
        label = _nonempty_text(item["label"], "required attribute label")
        selection_kind = _nonempty_text(
            item["selection_kind"], "attribute selection kind"
        )
        if selection_kind not in {"SINGLE", "MULTI", "TEXT"}:
            raise ChannelCategoryDecisionError(
                "attribute selection kind is invalid"
            )
        raw_values = item["option_values"]
        if type(raw_values) is not list or any(
            not isinstance(entry, Mapping) for entry in raw_values
        ):
            raise ChannelCategoryDecisionError(
                "required attribute option values are invalid"
            )
        normalized_values = []
        option_identities = set()
        for entry in raw_values:
            raw_option = _exact_mapping(
                entry,
                (
                    {
                        "value_id",
                        "original_value_name",
                        "recommended",
                        "value_unit",
                    }
                    if isinstance(entry, Mapping)
                    and "value_unit" in entry
                    else {
                        "value_id",
                        "original_value_name",
                        "recommended",
                    }
                ),
                "required attribute option",
            )
            if type(raw_option["recommended"]) is not bool:
                raise ChannelCategoryDecisionError(
                    "required attribute recommendation is invalid"
                )
            raw_value = {
                "value_id": raw_option["value_id"],
                "original_value_name": raw_option[
                    "original_value_name"
                ],
            }
            if "value_unit" in raw_option:
                raw_value["value_unit"] = raw_option["value_unit"]
            raw = _attribute_value(
                raw_value,
                "required attribute option",
            )
            raw["recommended"] = raw_option["recommended"]
            raw["option_identity_digest"] = digest_json(
                {
                    "schema_version": (
                        "channel-category-required-attribute-option/v1"
                    ),
                    "attribute_id": attribute_id,
                    **raw,
                }
            )
            value_identity = (
                raw["value_id"],
                raw["original_value_name"],
                raw.get("value_unit"),
            )
            if value_identity in option_identities:
                raise ChannelCategoryDecisionError(
                    "required attribute options are duplicated"
                )
            option_identities.add(value_identity)
            normalized_values.append(raw)
        normalized_values.sort(
            key=lambda row: (
                row["value_id"],
                row["original_value_name"],
                row.get("value_unit") or "",
            )
        )
        text_value_id = item["text_value_id"]
        if selection_kind == "TEXT":
            if (
                normalized_values
                or type(text_value_id) is not int
                or text_value_id < 0
            ):
                raise ChannelCategoryDecisionError(
                    "TEXT attribute options are invalid"
                )
        elif text_value_id is not None or not normalized_values:
            raise ChannelCategoryDecisionError(
                "selectable attribute options are invalid"
            )
        attribute_identity_digest = digest_json(
            {
                "schema_version": (
                    "channel-category-required-attribute/v1"
                ),
                "attribute_id": attribute_id,
                "label": label,
                "selection_kind": selection_kind,
                "option_values": normalized_values,
                "text_value_id": text_value_id,
            }
        )
        option_identity_digest = digest_json(
            {
                "schema_version": (
                    "channel-category-required-attribute-options/v1"
                ),
                "attribute_id": attribute_id,
                "selection_kind": selection_kind,
                "option_values": normalized_values,
                "text_value_id": text_value_id,
            }
        )
        result.append(
            {
                "attribute_id": attribute_id,
                "label": label,
                "selection_kind": selection_kind,
                "option_values": normalized_values,
                "text_value_id": text_value_id,
                "attribute_identity_digest": (
                    attribute_identity_digest
                ),
                "option_identity_digest": option_identity_digest,
            }
        )
    result.sort(key=lambda row: row["attribute_id"])
    return result


def _brand_options(value: object) -> list[dict[str, Any]]:
    if type(value) is not list or not value or any(
        not isinstance(row, Mapping) for row in value
    ):
        raise ChannelCategoryDecisionError(
            "official brand alternatives are invalid"
        )
    result = []
    seen = set()
    for row in value:
        item = _exact_mapping(
            row,
            {
                "brand_id",
                "original_brand_name",
                "evidence_digest",
                "recommended",
            },
            "brand option",
        )
        brand_id = item["brand_id"]
        if type(brand_id) is not int or brand_id < 0:
            raise ChannelCategoryDecisionError("brand ID is invalid")
        if brand_id in seen or type(item["recommended"]) is not bool:
            raise ChannelCategoryDecisionError(
                "official brand alternatives are ambiguous"
            )
        seen.add(brand_id)
        brand_name = _nonempty_text(
            item["original_brand_name"],
            "brand name",
        )
        recommended = bool(
            brand_id == 0
            and _normalized_brand_name(brand_name).replace(" ", "")
            == "nobrand"
        )
        if item["recommended"] is not recommended:
            raise ChannelCategoryDecisionError(
                "official brand recommendation is not truthful"
            )
        base = {
            "brand_id": brand_id,
            "original_brand_name": brand_name,
            "evidence_digest": _digest(
                item["evidence_digest"],
                "brand evidence digest",
            ),
            "recommended": recommended,
        }
        result.append(_brand_identity(base))
    if sum(row["recommended"] is True for row in result) != 1:
        raise ChannelCategoryDecisionError(
            "fixed no-brand policy option is unavailable or ambiguous"
        )
    result.sort(key=lambda row: row["brand_identity_digest"])
    return result


def _brand_identity(value: Mapping[str, object]) -> dict[str, Any]:
    base = {
        "brand_id": value["brand_id"],
        "original_brand_name": value["original_brand_name"],
        "evidence_digest": value["evidence_digest"],
        "recommended": value["recommended"],
    }
    identity = digest_json(
        {
            "schema_version": "channel-brand-option-identity/v1",
            **base,
        }
    )
    result = {
        **base,
        "brand_identity_digest": identity,
    }
    result["option_evidence_digest"] = digest_json(result)
    return result


def _selected_brand(value: object) -> dict[str, Any]:
    row = _exact_mapping(
        value,
        {
            "brand_id",
            "original_brand_name",
            "evidence_digest",
            "recommended",
            "brand_identity_digest",
            "option_evidence_digest",
        },
        "selected brand",
    )
    brand_id = row["brand_id"]
    if type(brand_id) is not int or brand_id < 0:
        raise ChannelCategoryDecisionError("brand ID is invalid")
    if type(row["recommended"]) is not bool:
        raise ChannelCategoryDecisionError(
            "selected brand recommendation is invalid"
        )
    rebuilt = _brand_identity(
        {
            "brand_id": brand_id,
            "original_brand_name": _nonempty_text(
                row["original_brand_name"],
                "brand name",
            ),
            "evidence_digest": _digest(
                row["evidence_digest"],
                "brand evidence digest",
            ),
            "recommended": row["recommended"],
        }
    )
    if rebuilt != dict(row):
        raise ChannelCategoryDecisionError(
            "selected brand identity drifted"
        )
    return rebuilt


def _location_options(value: object) -> list[dict[str, Any]]:
    if type(value) is not list or not value or any(
        not isinstance(row, Mapping) for row in value
    ):
        raise ChannelCategoryDecisionError(
            "official seller location alternatives are invalid"
        )
    result = []
    seen = set()
    for row in value:
        item = _exact_mapping(
            row,
            {
                "location_id",
                "display_name",
                "evidence_digest",
                "recommended",
            },
            "seller location option",
        )
        location_id = _nonempty_text(
            item["location_id"],
            "seller location ID",
        )
        if location_id in seen or type(item["recommended"]) is not bool:
            raise ChannelCategoryDecisionError(
                "official seller locations are ambiguous"
            )
        seen.add(location_id)
        display_name = _nonempty_text(
            item["display_name"],
            "seller location name",
        )
        expected_recommended = (
            unicodedata.normalize("NFC", display_name).strip()
            == "中国仓库"
        )
        if item["recommended"] is not expected_recommended:
            raise ChannelCategoryDecisionError(
                "official seller location recommendation is not truthful"
            )
        base = {
            "location_id": location_id,
            "display_name": display_name,
            "evidence_digest": _digest(
                item["evidence_digest"],
                "seller location evidence digest",
            ),
            "recommended": expected_recommended,
        }
        result.append(_location_identity(base))
    if sum(row["recommended"] is True for row in result) != 1:
        raise ChannelCategoryDecisionError(
            "fixed China warehouse option is unavailable or ambiguous"
        )
    result.sort(key=lambda row: row["location_identity_digest"])
    return result


def _location_identity(value: Mapping[str, object]) -> dict[str, Any]:
    base = {
        "location_id": value["location_id"],
        "display_name": value["display_name"],
        "evidence_digest": value["evidence_digest"],
        "recommended": value["recommended"],
    }
    identity = digest_json(
        {
            "schema_version": "channel-location-option-identity/v1",
            **base,
        }
    )
    result = {
        **base,
        "location_identity_digest": identity,
    }
    result["option_evidence_digest"] = digest_json(result)
    return result


def _selected_location(value: object) -> dict[str, Any]:
    row = _exact_mapping(
        value,
        {
            "location_id",
            "display_name",
            "evidence_digest",
            "recommended",
            "location_identity_digest",
            "option_evidence_digest",
        },
        "selected seller location",
    )
    if type(row["recommended"]) is not bool:
        raise ChannelCategoryDecisionError(
            "selected seller location recommendation is invalid"
        )
    rebuilt = _location_identity(
        {
            "location_id": _nonempty_text(
                row["location_id"],
                "seller location ID",
            ),
            "display_name": _nonempty_text(
                row["display_name"],
                "seller location name",
            ),
            "evidence_digest": _digest(
                row["evidence_digest"],
                "seller location evidence digest",
            ),
            "recommended": row["recommended"],
        }
    )
    if rebuilt != dict(row):
        raise ChannelCategoryDecisionError(
            "selected seller location identity drifted"
        )
    return rebuilt


def _creation_defaults(value: object) -> dict[str, Any]:
    row = _exact_mapping(
        value,
        {
            "seller_stock_quantity",
            "condition",
            "preorder",
            "evidence_digest",
        },
        "NEW_GLOBAL creation defaults",
    )
    quantity = _positive_int(
        row["seller_stock_quantity"],
        "seller stock quantity",
    )
    return {
        "seller_stock_quantity": quantity,
        "condition": _condition(row["condition"]),
        "preorder": _preorder(row["preorder"]),
        "evidence_digest": _digest(
            row["evidence_digest"],
            "creation defaults evidence digest",
        ),
    }


def _creation_seed(value: object) -> dict[str, Any]:
    row = _exact_mapping(
        value,
        {
            "schema_version",
            "sku_lineage_digest",
            "model_sku",
            "selected_image_position",
            "global_original_price_cny",
        },
        "NEW_GLOBAL creation seed",
    )
    if row["schema_version"] != "channel-category-creation-seed/v1":
        raise ChannelCategoryDecisionError(
            "NEW_GLOBAL creation seed version is invalid"
        )
    return {
        "schema_version": "channel-category-creation-seed/v1",
        "sku_lineage_digest": _digest(
            row["sku_lineage_digest"],
            "creation SKU lineage digest",
        ),
        "model_sku": _nonempty_text(row["model_sku"], "model SKU"),
        "selected_image_position": _positive_int(
            row["selected_image_position"],
            "selected image position",
        ),
        "global_original_price_cny": _positive_decimal_text(
            row["global_original_price_cny"],
            "global original price",
        ),
    }


def _creation_fact_option(
    defaults: Mapping[str, object],
    seed: Mapping[str, object],
) -> dict[str, Any]:
    variation = [
        {
            "name": "Default",
            "option_list": [
                {
                    "option": "Default",
                    "approved_image_position": seed[
                        "selected_image_position"
                    ],
                }
            ],
        }
    ]
    models = [
        {
            "global_model_sku": seed["model_sku"],
            "tier_index": [0],
            "original_price_cny": seed[
                "global_original_price_cny"
            ],
            "seller_stock_quantity": defaults[
                "seller_stock_quantity"
            ],
        }
    ]
    return _creation_fact_identity(
        {
            "seller_stock_quantity": defaults[
                "seller_stock_quantity"
            ],
            "condition": defaults["condition"],
            "preorder": defaults["preorder"],
            "tier_variation": variation,
            "global_model": models,
            "creation_defaults_evidence_digest": defaults[
                "evidence_digest"
            ],
        }
    )


def _creation_fact_identity(value: Mapping[str, object]) -> dict[str, Any]:
    creation = _creation_plan_payload(
        seller_stock_quantity=value["seller_stock_quantity"],
        condition=value["condition"],
        preorder=value["preorder"],
        tier_variation=value["tier_variation"],
        global_model=value["global_model"],
    )
    evidence_digest = _digest(
        value["creation_defaults_evidence_digest"],
        "creation defaults evidence digest",
    )
    base = {
        **creation,
        "creation_defaults_evidence_digest": evidence_digest,
    }
    identity = digest_json(
        {
            "schema_version": (
                "channel-new-global-creation-fact-identity/v1"
            ),
            **base,
        }
    )
    result = {
        **base,
        "creation_fact_identity_digest": identity,
        "variation_summary": _variation_summary(
            creation["tier_variation"],
            creation["global_model"],
        ),
    }
    result["option_evidence_digest"] = digest_json(result)
    return result


def _creation_plan_payload(
    *,
    seller_stock_quantity: object,
    condition: object,
    preorder: object,
    tier_variation: object,
    global_model: object,
) -> dict[str, Any]:
    quantity = _positive_int(
        seller_stock_quantity,
        "seller stock quantity",
    )
    clean_condition = _condition(condition)
    clean_preorder = _preorder(preorder)
    if (
        type(tier_variation) is not list
        or len(tier_variation) != 1
        or not isinstance(tier_variation[0], Mapping)
    ):
        raise ChannelCategoryDecisionError(
            "single-SKU default variation is invalid"
        )
    tier = _exact_mapping(
        tier_variation[0],
        {"name", "option_list"},
        "default variation",
    )
    options = tier["option_list"]
    if (
        tier["name"] != "Default"
        or type(options) is not list
        or len(options) != 1
        or not isinstance(options[0], Mapping)
    ):
        raise ChannelCategoryDecisionError(
            "single-SKU default variation is invalid"
        )
    option = _exact_mapping(
        options[0],
        {"option", "approved_image_position"},
        "default variation option",
    )
    if option["option"] != "Default":
        raise ChannelCategoryDecisionError(
            "single-SKU default variation is invalid"
        )
    image_position = _positive_int(
        option["approved_image_position"],
        "approved image position",
    )
    if (
        type(global_model) is not list
        or len(global_model) != 1
        or not isinstance(global_model[0], Mapping)
    ):
        raise ChannelCategoryDecisionError(
            "single-SKU default model is invalid"
        )
    model = _exact_mapping(
        global_model[0],
        {
            "global_model_sku",
            "tier_index",
            "original_price_cny",
            "seller_stock_quantity",
        },
        "default global model",
    )
    if model["tier_index"] != [0] or model[
        "seller_stock_quantity"
    ] != quantity:
        raise ChannelCategoryDecisionError(
            "single-SKU default model is invalid"
        )
    return {
        "seller_stock_quantity": quantity,
        "condition": clean_condition,
        "preorder": clean_preorder,
        "tier_variation": [
            {
                "name": "Default",
                "option_list": [
                    {
                        "option": "Default",
                        "approved_image_position": image_position,
                    }
                ],
            }
        ],
        "global_model": [
            {
                "global_model_sku": _nonempty_text(
                    model["global_model_sku"],
                    "global model SKU",
                ),
                "tier_index": [0],
                "original_price_cny": _positive_decimal_text(
                    model["original_price_cny"],
                    "global model original price",
                ),
                "seller_stock_quantity": quantity,
            }
        ],
    }


def _seller_stock(value: object) -> dict[str, Any]:
    row = _exact_mapping(
        value,
        {
            "source",
            "source_digest",
            "quantity",
            "approval_reference",
        },
        "seller stock",
    )
    if row["source"] != "kyle-explicit-seller-stock/v1":
        raise ChannelCategoryDecisionError(
            "seller stock source is invalid"
        )
    return {
        "source": row["source"],
        "source_digest": _digest(
            row["source_digest"],
            "seller stock source digest",
        ),
        "quantity": _positive_int(row["quantity"], "seller stock"),
        "approval_reference": _digest(
            row["approval_reference"],
            "seller stock approval reference",
        ),
    }


def _condition(value: object) -> str:
    clean = _nonempty_text(value, "condition")
    if clean not in {"NEW", "USED"}:
        raise ChannelCategoryDecisionError("condition is invalid")
    return clean


def _preorder(value: object) -> dict[str, Any]:
    row = _exact_mapping(
        value,
        {"is_pre_order", "days_to_ship"},
        "preorder",
    )
    if (
        type(row["is_pre_order"]) is not bool
        or type(row["days_to_ship"]) is not int
        or row["days_to_ship"] < 0
        or (
            row["is_pre_order"] is True
            and row["days_to_ship"] <= 0
        )
        or (
            row["is_pre_order"] is False
            and row["days_to_ship"] != 0
        )
    ):
        raise ChannelCategoryDecisionError("preorder is invalid")
    return dict(row)


def _variation_summary(
    tier_variation: object,
    global_model: object,
) -> dict[str, int]:
    position = tier_variation[0]["option_list"][0][
        "approved_image_position"
    ]
    return {
        "tier_count": len(tier_variation),
        "model_count": len(global_model),
        "model_sku_count": len(
            {
                row["global_model_sku"]
                for row in global_model
            }
        ),
        "approved_image_position": position,
    }


def _positive_decimal_text(value: object, field: str) -> str:
    if type(value) not in {str, int}:
        raise ChannelCategoryDecisionError(f"{field} is invalid")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ChannelCategoryDecisionError(
            f"{field} is invalid"
        ) from error
    if not parsed.is_finite() or parsed <= 0:
        raise ChannelCategoryDecisionError(f"{field} is invalid")
    return format(parsed, "f")


def _exact_mapping(
    value: object,
    keys: set[str],
    field: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ChannelCategoryDecisionError(f"{field} shape is invalid")
    return value


def _attribute_value(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ChannelCategoryDecisionError(f"{field} shape is invalid")
    keys = set(value)
    if not {"value_id", "original_value_name"} <= keys or not keys <= {
        "value_id",
        "original_value_name",
        "value_unit",
    }:
        raise ChannelCategoryDecisionError(f"{field} shape is invalid")
    value_id = value["value_id"]
    if type(value_id) is not int or value_id < 0:
        raise ChannelCategoryDecisionError(f"{field} ID is invalid")
    normalized = {
        "value_id": value_id,
        "original_value_name": _nonempty_text(
            value["original_value_name"],
            f"{field} name",
        ),
    }
    if "value_unit" in value:
        normalized["value_unit"] = _nonempty_text(
            value["value_unit"],
            f"{field} unit",
        )
    return normalized


def _nonempty_text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ChannelCategoryDecisionError(f"{field} is invalid")
    return value.strip()


def _normalized_brand_name(value: str) -> str:
    return " ".join(
        unicodedata.normalize("NFC", value).strip().casefold().split()
    )


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ChannelCategoryDecisionError(f"{field} is invalid")
    return value


def _digits(value: object, field: str) -> str:
    if (
        type(value) is not str
        or not value.isascii()
        or not value.isdigit()
        or not 1 <= len(value) <= 32
        or int(value) <= 0
    ):
        raise ChannelCategoryDecisionError(f"{field} is invalid")
    return value


def _digest(value: object, field: str) -> str:
    if not is_digest(value):
        raise ChannelCategoryDecisionError(f"{field} is invalid")
    return value


def _copy_json(value: object) -> Any:
    return json.loads(canonical_json(value))


__all__ = [
    "ChannelCategoryDecisionError",
    "ATTRIBUTE_SELECTION_EXECUTION_SCHEMA_VERSION",
    "ATTRIBUTE_SELECTION_SCHEMA_VERSION",
    "DECISION_SCHEMA_VERSION",
    "EXECUTION_SCHEMA_VERSION",
    "OBSERVER_REQUEST_SCHEMA_VERSION",
    "OBSERVER_SCHEMA_VERSION",
    "OPTIONS_SCHEMA_VERSION",
    "PLAN_BINDING_SCHEMA_VERSION",
    "PREVIEW_SCHEMA_VERSION",
    "SHOPEE_CHANNEL",
    "SHOPEE_GLOBAL_TARGET",
    "SHOPEE_NEW_GLOBAL",
    "SHOPEE_OFFICIAL_AUTHORITY",
    "approve_category_decision",
    "attribute_selection_execution_payload",
    "attribute_selection_matches_options",
    "blocked_category_options",
    "build_category_options",
    "canonical_json",
    "category_decision_plan_binding",
    "category_decision_execution_payload",
    "decision_matches_global_plan",
    "digest_json",
    "is_digest",
    "public_options_projection",
    "rehydrate_attribute_selection",
    "rehydrate_category_decision",
    "serialize_category_decision",
    "serialize_attribute_selection",
    "seller_stock_source_digest",
    "resolve_required_attribute_selections",
    "validate_attribute_selection",
    "validate_category_decision",
    "validate_category_options",
]
