"""Server-owned channel category observations and explicit local decisions.

The marketplace observer may recommend categories and return official
alternatives.  A recommendation is never an approval.  Only this module can
turn one exact offered identity digest into a Kyle-approved decision that may
be bound into an immutable release plan.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any


OPTIONS_SCHEMA_VERSION = "channel-category-options/v1"
OBSERVER_SCHEMA_VERSION = "channel-category-options-observation/v1"
DECISION_SCHEMA_VERSION = "channel-category-decision/v1"
PLAN_BINDING_SCHEMA_VERSION = "channel-category-decision-binding/v1"
EXECUTION_SCHEMA_VERSION = "channel-category-decision-execution/v1"
PREVIEW_SCHEMA_VERSION = "channel-category-decision-preview/v1"
OBSERVER_REQUEST_SCHEMA_VERSION = "channel-category-observer-request/v1"

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
    }
    payload["options_digest"] = digest_json(payload)
    return payload


def blocked_category_options(
    *,
    context: Mapping[str, object],
    reason_code: str,
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
        "reason": {
            "category": "CAPABILITY",
            "code": clean_code,
        },
        "next_action": {
            "action": "wait_for_channel_capability",
            "target_focus": SHOPEE_GLOBAL_TARGET,
        },
    }


def approve_category_decision(
    options: object,
    *,
    product_id: object,
    product_revision: object,
    selected_category_identity_digest: object,
    approved_by: object,
    confirm_channel_category_selection: object,
) -> dict[str, Any]:
    """Approve one exact offered option; never infer the recommendation."""

    snapshot = validate_category_options(options)
    if approved_by != "Kyle":
        raise ChannelCategoryDecisionError("approved_by must be Kyle")
    if confirm_channel_category_selection is not True:
        raise ChannelCategoryDecisionError(
            "literal confirm_channel_category_selection=true is required"
        )
    clean_product_id = _digits(product_id, "product ID")
    if type(product_revision) is not int or product_revision < 0:
        raise ChannelCategoryDecisionError(
            "product revision must be a non-negative int"
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
    payload: dict[str, Any] = {
        "schema_version": DECISION_SCHEMA_VERSION,
        "product_id": clean_product_id,
        "product_revision": product_revision,
        "channel": snapshot["channel"],
        "mode": snapshot["mode"],
        "context_digest": snapshot["context_digest"],
        "options_digest": snapshot["options_digest"],
        "selected_category_identity_digest": selected_digest,
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
        "required_attribute_count": option["required_attribute_count"],
        "required_values_complete": True,
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
                            "option_values": missing["option_values"],
                        }
                        for missing in row[
                            "missing_required_attributes"
                        ]
                    ],
                }
                for row in snapshot["options"]
            ],
        },
        context=snapshot["context"],
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
        "selected_is_recommended",
        "recommendation_source",
        "selected_category",
        "selected_attributes",
        "attributes_complete",
        "attribute_tree_digest",
        "required_attribute_count",
        "required_values_complete",
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
    ):
        raise ChannelCategoryDecisionError(
            "category decision identity is invalid"
        )
    _digits(decision["product_id"], "product ID")
    for field in (
        "context_digest",
        "options_digest",
        "selected_category_identity_digest",
        "attribute_tree_digest",
        "decision_digest",
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
    canonical = {
        **dict(decision),
        "selected_category": category,
        "selected_attributes": attributes,
        "recommendation_source": dict(recommendation),
    }
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
        "selected_attributes_digest": digest_json(
            decision["selected_attributes"]
        ),
        "approved_by": "Kyle",
    }
    binding["binding_digest"] = digest_json(binding)
    return binding


def category_decision_execution_payload(value: object) -> dict[str, Any]:
    """Return the exact server-internal facts a channel observer may consume."""

    decision = validate_category_decision(value)
    selected_category = decision["selected_category"]
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
                        "label": row["label"],
                        "selection_kind": row["selection_kind"],
                        "option_identity_digest": row[
                            "option_identity_digest"
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
        "selected_attributes": attributes,
        "attribute_tree_digest": attribute_tree_digest,
        "required_attribute_count": required_count,
        "attributes_complete": option["attributes_complete"],
        "required_values_complete": option[
            "required_values_complete"
        ],
        "missing_required_attributes": missing_required,
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
            raw = _attribute_value(entry, "required attribute option")
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
        option_identity_digest = digest_json(
            {
                "schema_version": (
                    "channel-category-required-attribute-options/v1"
                ),
                "attribute_id": attribute_id,
                "selection_kind": selection_kind,
                "option_values": normalized_values,
            }
        )
        result.append(
            {
                "attribute_id": attribute_id,
                "label": label,
                "selection_kind": selection_kind,
                "option_values": normalized_values,
                "option_identity_digest": option_identity_digest,
            }
        )
    result.sort(key=lambda row: row["attribute_id"])
    return result


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
    "blocked_category_options",
    "build_category_options",
    "canonical_json",
    "category_decision_plan_binding",
    "category_decision_execution_payload",
    "decision_matches_global_plan",
    "digest_json",
    "is_digest",
    "public_options_projection",
    "rehydrate_category_decision",
    "serialize_category_decision",
    "validate_category_decision",
    "validate_category_options",
]
