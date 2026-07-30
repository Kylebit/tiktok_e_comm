"""Strict first-party Shopee NEW_GLOBAL read-only observation.

The official APIs in this module produce *candidate evidence*, not approved
listing facts.  In particular:

* a category recommendation is never a category decision;
* selecting a category causes its path and attribute tree to be fetched again;
* required attribute values are never selected or synthesized here;
* brand, seller stock, and seller location are never defaulted; and
* community/generated SDK metadata is not an authority source.

Only redacted counts, booleans, rule codes, and SHA-256 digests are exposed.
Raw category/attribute/brand/location identifiers remain in the frozen
server-internal observation object and are excluded from ``repr`` and the
public projection.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any
import unicodedata

from modules.shopee.oneclick_release import ShopeePrepareTransport
from shared_platform.channel_category_decisions import (
    seller_stock_source_digest,
)
from shared_platform.shopee_global_plan import (
    NEW_GLOBAL,
    OFFICIAL_AUTHORITY,
    OFFICIAL_OBSERVATION_SCHEMA_VERSION,
    ShopeeGlobalPlanCandidate,
    build_shopee_global_plan_candidate,
)


SCHEMA_VERSION = "shopee-official-new-global-candidate-observation/v1"
CATEGORY_OPTIONS_SCHEMA_VERSION = (
    "channel-category-options-observation/v2"
)
CATEGORY_OBSERVER_REQUEST_SCHEMA_VERSION = (
    "channel-category-observer-request/v2"
)
CATEGORY_ATTRIBUTE_SELECTION_EXECUTION_SCHEMA_VERSION = (
    "channel-category-attribute-selection-execution/v1"
)
CREATION_DEFAULT_POLICY_VERSION = (
    "shopee-new-global-explicit-creation-proposal/v1"
)
PROPOSED_SELLER_STOCK_QUANTITY = 200
CATEGORY_RECOMMEND_PATH = (
    "/api/v2/global_product/category_recommend"
)
CATEGORY_PATH_PATH = "/api/v2/global_product/get_category"
ATTRIBUTE_TREE_PATH = "/api/v2/global_product/get_attribute_tree"
BRAND_LIST_PATH = "/api/v2/global_product/get_brand_list"
SELLER_LOCATION_PATH = (
    "/api/v2/merchant/get_merchant_warehouse_location_list"
)
AUDITED_OFFICIAL_READ_ENDPOINTS = frozenset(
    {
        CATEGORY_RECOMMEND_PATH,
        CATEGORY_PATH_PATH,
        ATTRIBUTE_TREE_PATH,
        BRAND_LIST_PATH,
        SELLER_LOCATION_PATH,
    }
)

# Kept only as a regression sentinel: generated metadata cannot enable an
# endpoint, validate a schema, or enter an evidence digest.
GENERATED_SDK_METADATA_AUTHORITY = "untrusted_endpoint_hint_only"


class ShopeeGlobalPlanCandidateError(RuntimeError):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        category: str = "CAPABILITY",
    ) -> None:
        super().__init__(detail)
        self.classification = "BLOCKED_CAPABILITY"
        self.reason_category = category
        self.reason_scope = "TARGET"
        self.reason_code = code


@dataclass(frozen=True)
class OfficialNewGlobalObservation:
    """Server-internal official facts with a redacted public projection."""

    schema_version: str
    authority: str
    recommendation_observed: bool
    selected_category_observed: bool
    selected_category_was_recommended: bool | None
    category_candidate_count: int
    category_path_count: int
    attribute_count: int
    required_attribute_count: int
    required_attribute_decision_count: int
    required_attributes_complete: bool
    brand_candidate_count: int
    seller_location_count: int
    stock_decision_present: bool
    rule_ids: tuple[str, ...]
    recommendation_digest: str
    selected_category_digest: str | None
    attribute_tree_digest: str | None
    brand_authority_digest: str
    seller_location_authority_digest: str
    evidence_digest: str
    _recommended_category_ids: tuple[int, ...] = field(
        repr=False, compare=False
    )
    _selected_category_path: tuple[Mapping[str, object], ...] = field(
        repr=False, compare=False
    )
    _attribute_tree: tuple[Mapping[str, object], ...] = field(
        repr=False, compare=False
    )
    _brand_rows: tuple[Mapping[str, object], ...] = field(
        repr=False, compare=False
    )
    _location_rows: tuple[Mapping[str, object], ...] = field(
        repr=False, compare=False
    )

    def public_projection(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "authority": self.authority,
            "checks": {
                "recommendation_observed": self.recommendation_observed,
                "selected_category_observed": (
                    self.selected_category_observed
                ),
                "selected_category_was_recommended": (
                    self.selected_category_was_recommended
                ),
                "required_attributes_complete": (
                    self.required_attributes_complete
                ),
                "stock_decision_present": self.stock_decision_present,
                "no_default_category": True,
                "no_default_required_attribute": True,
                "no_default_brand": True,
                "no_default_stock": True,
                "no_default_location": True,
            },
            "counts": {
                "category_candidates": self.category_candidate_count,
                "category_path": self.category_path_count,
                "attributes": self.attribute_count,
                "required_attributes": self.required_attribute_count,
                "required_attribute_decisions": (
                    self.required_attribute_decision_count
                ),
                "brand_candidates": self.brand_candidate_count,
                "seller_locations": self.seller_location_count,
            },
            "rule_ids": list(self.rule_ids),
            "digests": {
                "recommendation_digest": self.recommendation_digest,
                "selected_category_digest": self.selected_category_digest,
                "attribute_tree_digest": self.attribute_tree_digest,
                "brand_authority_digest": self.brand_authority_digest,
                "seller_location_authority_digest": (
                    self.seller_location_authority_digest
                ),
                "evidence_digest": self.evidence_digest,
            },
        }


def observe_official_new_global_candidate(
    *,
    approved_title: object,
    selected_category_id: object = None,
    transport: ShopeePrepareTransport,
) -> OfficialNewGlobalObservation:
    """Read official candidate facts without approving or mutating anything."""

    if type(approved_title) is not str or not approved_title.strip():
        raise _error("shopee_global_candidate_title_invalid", "CONTENT")
    if not isinstance(transport, ShopeePrepareTransport):
        raise _error("shopee_global_candidate_transport_invalid")
    selected_id = _optional_positive_int(
        selected_category_id, "shopee_selected_category_invalid"
    )

    recommendation_raw = _official_get(
        transport,
        CATEGORY_RECOMMEND_PATH,
        {"global_item_name": approved_title.strip()},
    )
    recommendation_response = _response_mapping(
        recommendation_raw, "shopee_category_recommendation_invalid"
    )
    recommended = _positive_unique_ids(
        recommendation_response.get("category_id_list"),
        "shopee_category_recommendation_invalid",
        allow_empty=False,
    )

    category_path: tuple[Mapping[str, object], ...] = ()
    attribute_tree: tuple[Mapping[str, object], ...] = ()
    category_path_digest = None
    attribute_tree_digest = None
    required_count = 0
    selected_was_recommended = None
    rules = ["category:recommendation_unapproved"]
    if selected_id is not None:
        selected_was_recommended = selected_id in recommended
        category_path = _read_category_path(transport, selected_id)
        category_path_digest = _digest(category_path)
        # A category decision never reuses a previously fetched tree.
        attribute_tree = _read_attribute_tree(transport, selected_id)
        attribute_tree_digest = _digest(attribute_tree)
        required_count = sum(
            row["is_mandatory"] is True for row in attribute_tree
        )
        rules.append("category:selected_official_path_revalidated")
        if not selected_was_recommended:
            rules.append("category:selected_outside_recommendations")

    brands = _read_all_brands(transport, selected_id)
    locations = _read_seller_locations(transport)
    recommendation_digest = _digest(recommended)
    brand_digest = _digest(brands)
    location_digest = _digest(locations)
    # This observer intentionally has no approved attribute decisions or stock
    # decision.  It therefore cannot silently create a READY execution plan.
    evidence_payload = {
        "schema_version": SCHEMA_VERSION,
        "authority": OFFICIAL_AUTHORITY,
        "recommendation_digest": recommendation_digest,
        "selected_category_digest": category_path_digest,
        "attribute_tree_digest": attribute_tree_digest,
        "brand_authority_digest": brand_digest,
        "seller_location_authority_digest": location_digest,
        "selected_category_was_recommended": selected_was_recommended,
        "counts": {
            "recommendations": len(recommended),
            "category_path": len(category_path),
            "attributes": len(attribute_tree),
            "required_attributes": required_count,
            "required_attribute_decisions": 0,
            "brands": len(brands),
            "locations": len(locations),
        },
        "rules": sorted(rules),
    }
    return OfficialNewGlobalObservation(
        schema_version=SCHEMA_VERSION,
        authority=OFFICIAL_AUTHORITY,
        recommendation_observed=True,
        selected_category_observed=selected_id is not None,
        selected_category_was_recommended=selected_was_recommended,
        category_candidate_count=len(recommended),
        category_path_count=len(category_path),
        attribute_count=len(attribute_tree),
        required_attribute_count=required_count,
        required_attribute_decision_count=0,
        required_attributes_complete=required_count == 0,
        brand_candidate_count=len(brands),
        seller_location_count=len(locations),
        stock_decision_present=False,
        rule_ids=tuple(sorted(rules)),
        recommendation_digest=recommendation_digest,
        selected_category_digest=category_path_digest,
        attribute_tree_digest=attribute_tree_digest,
        brand_authority_digest=brand_digest,
        seller_location_authority_digest=location_digest,
        evidence_digest=_digest(evidence_payload),
        _recommended_category_ids=recommended,
        _selected_category_path=category_path,
        _attribute_tree=attribute_tree,
        _brand_rows=brands,
        _location_rows=locations,
    )


def observe_channel_category_options(
    request: Mapping[str, object],
) -> dict[str, object]:
    """Implement the 00-owned category-options observation seam exactly.

    The optional selection is server-rehydrated approved data.  Its path,
    attribute tree, and selected values are revalidated from official reads.
    """

    expected = {
        "schema_version",
        "channel",
        "mode",
        "context",
        "approved_title",
        "approved_title_digest",
        "current_selection",
        "current_attribute_selection",
    }
    if (
        not isinstance(request, Mapping)
        or set(request) != expected
        or request.get("schema_version")
        != CATEGORY_OBSERVER_REQUEST_SCHEMA_VERSION
        or request.get("channel") != "shopee"
        or request.get("mode") != "NEW_GLOBAL"
    ):
        raise _error("shopee_category_observer_request_invalid")
    title = _nonempty_string(
        request.get("approved_title"),
        "shopee_category_observer_title_invalid",
    )
    if request.get("approved_title_digest") != hashlib.sha256(
        unicodedata.normalize("NFC", title.strip()).encode("utf-8")
    ).hexdigest():
        raise _error("shopee_category_observer_title_digest_invalid")
    context = _category_context(request.get("context"))
    selection = _current_selection(
        request.get("current_selection"),
        expected_context_digest=_digest(context),
    )
    attribute_selection = _current_attribute_selection(
        request.get("current_attribute_selection"),
        expected_context=context,
    )
    if selection is not None and attribute_selection is not None:
        raise _error("shopee_category_selection_invalid", "CONTENT")
    transport = _category_prepare_transport()
    raw = _official_get(
        transport,
        CATEGORY_RECOMMEND_PATH,
        {"global_item_name": title.strip()},
    )
    response = _response_mapping(
        raw, "shopee_category_recommendation_invalid"
    )
    recommended = _positive_unique_ids(
        response.get("category_id_list"),
        "shopee_category_recommendation_invalid",
        allow_empty=False,
    )
    recommended_id = recommended[0]
    option_ids = list(recommended)
    selected_id = (
        selection["category"]["category_id"]
        if selection is not None
        else None
    )
    if selected_id is not None and selected_id not in option_ids:
        option_ids.append(selected_id)
    recommendation_digest = _digest(recommended)
    observed_options: list[
        tuple[
            int,
            tuple[Mapping[str, object], ...],
            tuple[Mapping[str, object], ...],
        ]
    ] = []
    for category_id in option_ids:
        path = _read_category_path(transport, category_id)
        tree = _read_attribute_tree(transport, category_id)
        observed_options.append((category_id, path, tree))
    attribute_category_id = None
    if attribute_selection is not None:
        matching_categories = [
            category_id
            for category_id, _path, tree in observed_options
            if _digest(tree)
            == attribute_selection["attribute_tree_digest"]
        ]
        if len(matching_categories) != 1:
            raise _error("shopee_attribute_selection_drift", "CONTENT")
        attribute_category_id = matching_categories[0]
    options: list[dict[str, object]] = []
    for category_id, path, tree in observed_options:
        required_rows = [
            row for row in tree if row["is_mandatory"] is True
        ]
        selected_attributes: list[dict[str, object]] = []
        missing_required = _missing_required_projection(required_rows)
        if selection is not None and category_id == selected_id:
            selected_attributes = _revalidate_selected_attributes(
                selection=selection,
                category_path=path,
                attribute_tree=tree,
            )
            selected_ids = {
                row["attribute_id"] for row in selected_attributes
            }
            missing_required = _missing_required_projection(
                [
                    row
                    for row in required_rows
                    if row["attribute_id"] not in selected_ids
                ]
            )
        elif (
            attribute_selection is not None
            and category_id == attribute_category_id
        ):
            selected_attributes = _revalidate_attribute_rows(
                attribute_selection["selected_attributes"],
                attribute_tree=tree,
            )
            selected_ids = {
                row["attribute_id"] for row in selected_attributes
            }
            missing_required = _missing_required_projection(
                [
                    row
                    for row in required_rows
                    if row["attribute_id"] not in selected_ids
                ]
            )
        options.append(
            {
                "category_id": category_id,
                "name": path[-1]["name"],
                "path": [dict(row) for row in path],
                "path_complete": True,
                "category_evidence_digest": _digest(path),
                "selected_attributes": selected_attributes,
                "attributes_complete": True,
                "attribute_tree_digest": _digest(tree),
                "required_attribute_count": len(required_rows),
                "required_values_complete": not missing_required,
                "missing_required_attributes": missing_required,
            }
        )
    authority_category_id = selected_id or recommended_id
    brand_options = _brand_option_projection(
        _read_all_brands(transport, authority_category_id)
    )
    location_options = _location_option_projection(
        _read_seller_locations(transport)
    )
    creation_defaults = _creation_default_projection()
    if selection is not None:
        _revalidate_execution_choices(
            selection,
            brand_options=brand_options,
            location_options=location_options,
            creation_defaults=creation_defaults,
        )
    return {
        "schema_version": CATEGORY_OPTIONS_SCHEMA_VERSION,
        "channel": "shopee",
        "mode": "NEW_GLOBAL",
        "authority": "shopee_official_category_get",
        "recommendation_source": {
            "authority": "shopee_official_category_recommend",
            "evidence_digest": recommendation_digest,
        },
        "recommended_category_id": recommended_id,
        "options": options,
        "brand_options": brand_options,
        "location_options": location_options,
        "creation_defaults": creation_defaults,
    }


def build_official_new_global_candidate(
    request: object,
    seed: Mapping[str, object],
    transport: ShopeePrepareTransport,
) -> ShopeeGlobalPlanCandidate:
    """Production adapter factory used by the one-click observer seam.

    The result is deliberately blocked until the server-owned approval
    contract carries exact selected attribute, brand, stock, location,
    condition, preorder, variation, and model decisions.
    """

    selection_raw = seed.get("category_decision_execution")
    selection = None
    selected_category_id = None
    if selection_raw is not None:
        if not isinstance(request, Mapping):
            raise _error("shopee_category_selection_context_invalid")
        context = {
            "schema_version": CATEGORY_OBSERVER_REQUEST_SCHEMA_VERSION,
            "product_id": request.get("offer_id"),
            "product_revision": request.get("product_revision"),
            "channel": "shopee",
            "mode": "NEW_GLOBAL",
            "source_identity_digest": seed.get("source_identity_digest"),
            "sku_lineage_digest": seed.get("sku_lineage_digest"),
            "approved_copy_digest": seed.get("approved_copy_digest"),
            "targets_digest": _digest(sorted(request.get("targets", []))),
        }
        selection = _current_selection(
            selection_raw, expected_context_digest=_digest(context)
        )
        selected_category_id = selection["category"]["category_id"]
    observation = observe_official_new_global_candidate(
        approved_title=seed.get("title"),
        selected_category_id=selected_category_id,
        transport=transport,
    )
    category: object = None
    attributes: object = []
    attributes_complete = False
    attribute_tree_digest = observation.attribute_tree_digest
    if selection is not None:
        selected_attributes = _revalidate_selected_attributes(
            selection=selection,
            category_path=observation._selected_category_path,
            attribute_tree=observation._attribute_tree,
        )
        category = {
            "category_id": selection["category"]["category_id"],
            "path": [
                dict(row) for row in selection["category"]["path"]
            ],
            "path_complete": True,
            "evidence_digest": selection["category"]["evidence_digest"],
        }
        attributes = [
            {
                "attribute_id": row["attribute_id"],
                "attribute_value_list": [
                    {
                        "value_id": value["value_id"],
                        "original_value_name": value[
                            "original_value_name"
                        ],
                        **(
                            {"value_unit": value["value_unit"]}
                            if "value_unit" in value
                            else {}
                        ),
                    }
                    for value in row["attribute_value_list"]
                ],
            }
            for row in selected_attributes
        ]
        attributes_complete = True
        attribute_tree_digest = selection["attribute_tree_digest"]
        _revalidate_execution_choices(
            selection,
            brand_options=_brand_option_projection(
                observation._brand_rows
            ),
            location_options=_location_option_projection(
                observation._location_rows
            ),
            creation_defaults=_creation_default_projection(),
        )
        _revalidate_single_sku_default_mapping(
            request=request,
            seed=seed,
            selection=selection,
        )
    return build_shopee_global_plan_candidate(
        mode=NEW_GLOBAL,
        observation_authority=OFFICIAL_AUTHORITY,
        observation_schema_version=OFFICIAL_OBSERVATION_SCHEMA_VERSION,
        observation_evidence_digest=observation.evidence_digest,
        source_identity_schema_version=seed.get(
            "source_identity_schema_version"
        ),
        source_identity_digest=seed.get("source_identity_digest"),
        sku_lineage_schema_version=seed.get("sku_lineage_schema_version"),
        sku_lineage_digest=seed.get("sku_lineage_digest"),
        content_package_digest=seed.get("content_package_digest"),
        title=seed.get("title"),
        description=seed.get("description"),
        approved_copy_digest=seed.get("approved_copy_digest"),
        ordered_approved_images=seed.get("ordered_approved_images"),
        approved_source_image_manifest_digest=seed.get(
            "approved_source_image_manifest_digest"
        ),
        selected_image_positions=seed.get("selected_image_positions"),
        parcel=seed.get("parcel"),
        target_pricing=seed.get("target_pricing"),
        policy_digest=seed.get("policy_digest"),
        category=category,
        attributes=attributes,
        attributes_complete=attributes_complete,
        attribute_tree_digest=attribute_tree_digest,
        brand=(selection["brand"] if selection is not None else None),
        seller_stock=(
            selection["seller_stock"] if selection is not None else None
        ),
        location=(
            selection["location"] if selection is not None else None
        ),
        condition=(
            selection["condition"] if selection is not None else None
        ),
        preorder=(
            selection["preorder"] if selection is not None else None
        ),
        variations=(
            selection["tier_variation"] if selection is not None else []
        ),
        variations_complete=selection is not None,
        models=(
            selection["global_model"] if selection is not None else []
        ),
    )


def _read_category_path(
    transport: ShopeePrepareTransport, selected_id: int
) -> tuple[Mapping[str, object], ...]:
    raw = _official_get(
        transport, CATEGORY_PATH_PATH, {"category_id": selected_id}
    )
    response = _response_mapping(raw, "shopee_category_path_invalid")
    rows = _mapping_list(
        response.get("category_list"), "shopee_category_path_invalid"
    )
    if not rows:
        raise _error("shopee_category_path_invalid", "CONTENT")
    result: list[Mapping[str, object]] = []
    previous_id = 0
    seen: set[int] = set()
    for index, row in enumerate(rows):
        if set(row) != {
            "category_id",
            "parent_category_id",
            "original_category_name",
            "has_children",
        }:
            raise _error("shopee_category_path_invalid", "CONTENT")
        category_id = _positive_int(
            row["category_id"], "shopee_category_path_invalid"
        )
        parent_id = _nonnegative_int(
            row["parent_category_id"], "shopee_category_path_invalid"
        )
        name = _nonempty_string(
            row["original_category_name"], "shopee_category_path_invalid"
        )
        if type(row["has_children"]) is not bool:
            raise _error("shopee_category_path_invalid", "CONTENT")
        if category_id in seen or parent_id != previous_id:
            raise _error("shopee_category_path_invalid", "CONTENT")
        if index < len(rows) - 1 and row["has_children"] is not True:
            raise _error("shopee_category_path_invalid", "CONTENT")
        seen.add(category_id)
        previous_id = category_id
        result.append({"category_id": category_id, "name": name})
    if previous_id != selected_id or rows[-1]["has_children"] is not False:
        raise _error("shopee_category_path_invalid", "CONTENT")
    return tuple(result)


def _category_prepare_transport() -> ShopeePrepareTransport:
    """Resolve one prepared merchant identity without refreshing credentials."""

    from modules.shopee.oneclick_release import _prepare_transport

    available = []
    for region in ("PH", "MY", "TH", "VN"):
        try:
            available.append(_prepare_transport(region))
        except Exception:
            continue
    if not available:
        raise _error("shopee_category_observer_transport_invalid", "AUTH")
    merchant_ids = {
        value.credentials.merchant_id for value in available
    }
    if len(merchant_ids) != 1:
        raise _error(
            "shopee_category_observer_merchant_ambiguous", "AUTH"
        )
    return available[0]


def _read_attribute_tree(
    transport: ShopeePrepareTransport, selected_id: int
) -> tuple[Mapping[str, object], ...]:
    raw = _official_get(
        transport,
        ATTRIBUTE_TREE_PATH,
        {"category_id": selected_id, "language": "en"},
    )
    response = _response_mapping(raw, "shopee_attribute_tree_invalid")
    rows = _mapping_list(
        response.get("attribute_list"), "shopee_attribute_tree_invalid"
    )
    seen: set[int] = set()
    normalized: list[Mapping[str, object]] = []
    for row in rows:
        if set(row) != {
            "attribute_id",
            "original_attribute_name",
            "is_mandatory",
            "input_type",
            "attribute_value_list",
        }:
            raise _error("shopee_attribute_tree_invalid", "CONTENT")
        attribute_id = _positive_int(
            row["attribute_id"], "shopee_attribute_tree_invalid"
        )
        if attribute_id in seen or type(row["is_mandatory"]) is not bool:
            raise _error("shopee_attribute_tree_invalid", "CONTENT")
        seen.add(attribute_id)
        name = _nonempty_string(
            row["original_attribute_name"],
            "shopee_attribute_tree_invalid",
        )
        input_type = _nonempty_string(
            row["input_type"], "shopee_attribute_tree_invalid"
        )
        if input_type not in {
            "SINGLE_SELECT",
            "MULTI_SELECT",
            "TEXT",
        }:
            raise _error("shopee_attribute_tree_invalid", "CONTENT")
        values = _mapping_list(
            row["attribute_value_list"],
            "shopee_attribute_tree_invalid",
        )
        value_rows: list[Mapping[str, object]] = []
        value_ids: set[int] = set()
        for value in values:
            if (
                set(value) - {"value_id", "original_value_name", "value_unit"}
                or not {"value_id", "original_value_name"}.issubset(value)
            ):
                raise _error("shopee_attribute_tree_invalid", "CONTENT")
            value_id = _nonnegative_int(
                value["value_id"], "shopee_attribute_tree_invalid"
            )
            if value_id in value_ids:
                raise _error("shopee_attribute_tree_invalid", "CONTENT")
            value_ids.add(value_id)
            normalized_value: dict[str, object] = {
                "value_id": value_id,
                "original_value_name": _nonempty_string(
                    value["original_value_name"],
                    "shopee_attribute_tree_invalid",
                ),
            }
            if "value_unit" in value:
                normalized_value["value_unit"] = _nonempty_string(
                    value["value_unit"],
                    "shopee_attribute_tree_invalid",
                )
            value_rows.append(normalized_value)
        normalized.append(
            {
                "attribute_id": attribute_id,
                "original_attribute_name": name,
                "is_mandatory": row["is_mandatory"],
                "input_type": input_type,
                "attribute_value_list": value_rows,
            }
        )
    return tuple(normalized)


def _category_context(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _error("shopee_category_observer_context_invalid")
    expected = {
        "schema_version",
        "product_id",
        "product_revision",
        "channel",
        "mode",
        "source_identity_digest",
        "sku_lineage_digest",
        "approved_copy_digest",
        "targets_digest",
    }
    if (
        set(value) != expected
        or value.get("schema_version")
        != CATEGORY_OBSERVER_REQUEST_SCHEMA_VERSION
        or value.get("channel") != "shopee"
        or value.get("mode") != "NEW_GLOBAL"
        or type(value.get("product_id")) is not str
        or not value["product_id"].isdigit()
        or type(value.get("product_revision")) is not int
        or value["product_revision"] < 0
        or any(
            not _is_digest(value.get(field))
            for field in (
                "source_identity_digest",
                "sku_lineage_digest",
                "approved_copy_digest",
                "targets_digest",
            )
        )
    ):
        raise _error("shopee_category_observer_context_invalid")
    return dict(value)


def _current_attribute_selection(
    value: object, *, expected_context: Mapping[str, object]
) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _error("shopee_attribute_selection_invalid", "CONTENT")
    expected = {
        "schema_version",
        "product_id",
        "product_revision",
        "channel",
        "mode",
        "selection_digest",
        "context_digest",
        "options_digest",
        "category_identity_digest",
        "selected_brand_identity_digest",
        "selected_location_identity_digest",
        "selected_creation_fact_identity_digest",
        "attribute_tree_digest",
        "selected_attributes",
    }
    if (
        set(value) != expected
        or value.get("schema_version")
        != CATEGORY_ATTRIBUTE_SELECTION_EXECUTION_SCHEMA_VERSION
        or value.get("product_id") != expected_context["product_id"]
        or value.get("product_revision")
        != expected_context["product_revision"]
        or value.get("context_digest") != _digest(expected_context)
        or value.get("channel") != "shopee"
        or value.get("mode") != "NEW_GLOBAL"
        or type(value.get("product_revision")) is not int
        or value["product_revision"] < 0
        or any(
            not _is_digest(value.get(field))
            for field in (
                "selection_digest",
                "options_digest",
                "category_identity_digest",
                "selected_brand_identity_digest",
                "selected_location_identity_digest",
                "selected_creation_fact_identity_digest",
                "attribute_tree_digest",
            )
        )
        or type(value.get("selected_attributes")) is not list
    ):
        raise _error("shopee_attribute_selection_invalid", "CONTENT")
    normalized_attributes = _canonical_attribute_rows(
        value["selected_attributes"],
        code="shopee_attribute_selection_invalid",
    )
    return {
        **dict(value),
        "selected_attributes": normalized_attributes,
    }


def _current_selection(
    value: object, *, expected_context_digest: str
) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _error("shopee_category_selection_invalid", "CONTENT")
    expected = {
        "schema_version",
        "decision_digest",
        "context_digest",
        "options_digest",
        "selected_category_identity_digest",
        "category",
        "attribute_list",
        "attributes_complete",
        "attribute_tree_digest",
        "brand",
        "seller_stock",
        "location",
        "condition",
        "preorder",
        "tier_variation",
        "global_model",
    }
    if (
        set(value) != expected
        or value.get("schema_version")
        != "channel-category-decision-execution/v2"
        or value.get("context_digest") != expected_context_digest
        or value.get("attributes_complete") is not True
        or any(
            not _is_digest(value.get(field))
            for field in (
                "decision_digest",
                "options_digest",
                "selected_category_identity_digest",
                "attribute_tree_digest",
            )
        )
        or not isinstance(value.get("category"), Mapping)
        or not isinstance(value.get("attribute_list"), list)
        or not isinstance(value.get("brand"), Mapping)
        or not isinstance(value.get("seller_stock"), Mapping)
        or not isinstance(value.get("location"), Mapping)
        or type(value.get("condition")) is not str
        or not isinstance(value.get("preorder"), Mapping)
        or not isinstance(value.get("tier_variation"), list)
        or not isinstance(value.get("global_model"), list)
    ):
        raise _error("shopee_category_selection_invalid", "CONTENT")
    return dict(value)


def _revalidate_selected_attributes(
    *,
    selection: Mapping[str, object],
    category_path: tuple[Mapping[str, object], ...],
    attribute_tree: tuple[Mapping[str, object], ...],
) -> list[dict[str, object]]:
    category = selection["category"]
    if (
        not isinstance(category, Mapping)
        or set(category)
        != {
            "category_id",
            "path",
            "path_complete",
            "evidence_digest",
        }
        or category.get("path_complete") is not True
        or category.get("path") != [dict(row) for row in category_path]
        or category.get("evidence_digest") != _digest(category_path)
        or selection.get("attribute_tree_digest") != _digest(attribute_tree)
    ):
        raise _error("shopee_category_selection_drift", "CONTENT")
    return _revalidate_attribute_rows(
        selection["attribute_list"],
        attribute_tree=attribute_tree,
    )


def _canonical_attribute_rows(
    value: object, *, code: str
) -> list[dict[str, object]]:
    if type(value) is not list or not value:
        raise _error(code, "CONTENT")
    result: list[dict[str, object]] = []
    seen: set[int] = set()
    for row in value:
        if (
            not isinstance(row, Mapping)
            or set(row) != {"attribute_id", "attribute_value_list"}
        ):
            raise _error(code, "CONTENT")
        attribute_id = _positive_int(row["attribute_id"], code)
        values = row["attribute_value_list"]
        if (
            attribute_id in seen
            or type(values) is not list
            or not values
        ):
            raise _error(code, "CONTENT")
        seen.add(attribute_id)
        normalized_values: list[dict[str, object]] = []
        identities: set[tuple[int, str, str | None]] = set()
        for value_row in values:
            if not isinstance(value_row, Mapping) or (
                set(value_row)
                - {"value_id", "original_value_name", "value_unit"}
                or not {
                    "value_id",
                    "original_value_name",
                }.issubset(value_row)
            ):
                raise _error(code, "CONTENT")
            value_id = _nonnegative_int(value_row["value_id"], code)
            original_value_name = _nonempty_string(
                value_row["original_value_name"], code
            )
            normalized_value: dict[str, object] = {
                "value_id": value_id,
                "original_value_name": original_value_name,
            }
            value_unit = None
            if "value_unit" in value_row:
                value_unit = _nonempty_string(
                    value_row["value_unit"], code
                )
                normalized_value["value_unit"] = value_unit
            identity = (
                value_id,
                original_value_name,
                value_unit,
            )
            if identity in identities:
                raise _error(code, "CONTENT")
            identities.add(identity)
            normalized_values.append(normalized_value)
        normalized_values.sort(
            key=lambda item: (
                item["value_id"],
                item["original_value_name"],
                item.get("value_unit") or "",
            )
        )
        result.append(
            {
                "attribute_id": attribute_id,
                "attribute_value_list": normalized_values,
            }
        )
    result.sort(key=lambda item: item["attribute_id"])
    if result != value:
        raise _error(code, "CONTENT")
    return result


def _revalidate_attribute_rows(
    value: object,
    *,
    attribute_tree: tuple[Mapping[str, object], ...],
) -> list[dict[str, object]]:
    canonical = _canonical_attribute_rows(
        value,
        code="shopee_selected_attributes_invalid",
    )
    tree_by_id = {row["attribute_id"]: row for row in attribute_tree}
    result: list[dict[str, object]] = []
    seen: set[int] = set()
    for row in canonical:
        attribute_id = row["attribute_id"]
        tree_row = tree_by_id.get(attribute_id)
        values = row["attribute_value_list"]
        if (
            attribute_id in seen
            or tree_row is None
        ):
            raise _error("shopee_selected_attributes_invalid", "CONTENT")
        seen.add(attribute_id)
        input_type = tree_row["input_type"]
        official_rows = tree_row["attribute_value_list"]
        if input_type == "SINGLE_SELECT" and len(values) != 1:
            raise _error("shopee_selected_attributes_invalid", "CONTENT")
        if input_type == "TEXT":
            if (
                len(values) != 1
                or len(official_rows) != 1
                or values[0]["value_id"]
                != official_rows[0]["value_id"]
                or "value_unit" in values[0]
            ):
                raise _error(
                    "shopee_selected_attributes_invalid", "CONTENT"
                )
            normalized_text = unicodedata.normalize(
                "NFC", values[0]["original_value_name"].strip()
            )
            if (
                not normalized_text
                or values[0]["original_value_name"] != normalized_text
            ):
                raise _error(
                    "shopee_selected_attributes_invalid", "CONTENT"
                )
            result.append(
                {
                    "attribute_id": attribute_id,
                    "attribute_value_list": [
                        {
                            "value_id": values[0]["value_id"],
                            "original_value_name": normalized_text,
                        }
                    ],
                }
            )
            continue
        official_values = {
            entry["value_id"]: entry for entry in official_rows
        }
        normalized: list[dict[str, object]] = []
        for value in values:
            value_id = value["value_id"]
            display = value["original_value_name"]
            official_value = official_values.get(value_id)
            if (
                official_value is None
                or official_value["original_value_name"] != display
                or official_value.get("value_unit") != value.get("value_unit")
            ):
                raise _error(
                    "shopee_selected_attributes_drift", "CONTENT"
                )
            normalized_value: dict[str, object] = {
                "value_id": value_id,
                "original_value_name": display,
            }
            if "value_unit" in value:
                normalized_value["value_unit"] = value["value_unit"]
            normalized.append(normalized_value)
        result.append(
            {
                "attribute_id": attribute_id,
                "attribute_value_list": normalized,
            }
        )
    mandatory_ids = {
        row["attribute_id"]
        for row in attribute_tree
        if row["is_mandatory"] is True
    }
    if not mandatory_ids.issubset(seen):
        raise _error("shopee_required_attributes_incomplete", "CONTENT")
    return result


def _missing_required_projection(
    rows: list[Mapping[str, object]],
) -> list[dict[str, object]]:
    kinds = {
        "SINGLE_SELECT": "SINGLE",
        "MULTI_SELECT": "MULTI",
        "TEXT": "TEXT",
    }
    result = []
    for row in rows:
        official_values = row["attribute_value_list"]
        if row["input_type"] == "TEXT":
            if (
                len(official_values) != 1
                or "value_unit" in official_values[0]
            ):
                raise _error("shopee_attribute_tree_invalid", "CONTENT")
            options = []
            text_value_id = _nonnegative_int(
                official_values[0]["value_id"],
                "shopee_attribute_tree_invalid",
            )
        else:
            if not official_values:
                raise _error("shopee_attribute_tree_invalid", "CONTENT")
            options = []
            for value in official_values:
                value_id = _nonnegative_int(
                    value["value_id"], "shopee_attribute_tree_invalid"
                )
                options.append(
                    {
                        "value_id": value_id,
                        "original_value_name": value[
                            "original_value_name"
                        ],
                        "recommended": False,
                    }
                )
            text_value_id = None
        result.append(
            {
                "attribute_id": row["attribute_id"],
                "label": row["original_attribute_name"],
                "selection_kind": kinds[row["input_type"]],
                "option_values": options,
                "text_value_id": text_value_id,
            }
        )
    return result


def _brand_option_projection(
    rows: tuple[Mapping[str, object], ...],
) -> list[dict[str, object]]:
    result = []
    for row in rows:
        evidence = _digest(
            {
                "schema_version": "shopee-official-brand-option/v1",
                "brand_id": row["brand_id"],
                "original_brand_name": row["original_brand_name"],
            }
        )
        result.append(
            {
                "brand_id": row["brand_id"],
                "original_brand_name": row["original_brand_name"],
                "evidence_digest": evidence,
                "recommended": bool(
                    row["brand_id"] == 0
                    and unicodedata.normalize(
                        "NFC", row["original_brand_name"]
                    )
                    .strip()
                    .casefold()
                    == "no brand"
                ),
            }
        )
    return result


def _location_option_projection(
    rows: tuple[Mapping[str, object], ...],
) -> list[dict[str, object]]:
    result = []
    single_official_location = len(rows) == 1
    for row in rows:
        evidence = _digest(
            {
                "schema_version": "shopee-official-location-option/v1",
                "location_id": row["location_id"],
                "display_name": row["warehouse_name"],
            }
        )
        result.append(
            {
                "location_id": row["location_id"],
                "display_name": row["warehouse_name"],
                "evidence_digest": evidence,
                "recommended": single_official_location,
            }
        )
    return result


def _creation_default_projection() -> dict[str, object]:
    payload = {
        "schema_version": CREATION_DEFAULT_POLICY_VERSION,
        "seller_stock_quantity": PROPOSED_SELLER_STOCK_QUANTITY,
        "condition": "NEW",
        "preorder": {"is_pre_order": False, "days_to_ship": 0},
    }
    return {
        "seller_stock_quantity": payload["seller_stock_quantity"],
        "condition": payload["condition"],
        "preorder": dict(payload["preorder"]),
        "evidence_digest": _digest(payload),
    }


def _revalidate_execution_choices(
    selection: Mapping[str, object],
    *,
    brand_options: list[dict[str, object]],
    location_options: list[dict[str, object]],
    creation_defaults: Mapping[str, object],
) -> None:
    brand = selection["brand"]
    if (
        not isinstance(brand, Mapping)
        or set(brand)
        != {"brand_id", "original_brand_name", "evidence_digest"}
        or sum(
            option["brand_id"] == brand.get("brand_id")
            and option["original_brand_name"]
            == brand.get("original_brand_name")
            and option["evidence_digest"] == brand.get("evidence_digest")
            for option in brand_options
        )
        != 1
    ):
        raise _error("shopee_selected_brand_drift", "CONTENT")
    location = selection["location"]
    matched_locations = [
        option
        for option in location_options
        if option["location_id"] == location.get("location_id")
        and option["evidence_digest"] == location.get("evidence_digest")
    ] if isinstance(location, Mapping) else []
    if (
        not isinstance(location, Mapping)
        or set(location) != {"location_id", "evidence_digest"}
        or len(matched_locations) != 1
    ):
        raise _error("shopee_selected_location_drift", "LOGISTICS")
    stock = selection["seller_stock"]
    preorder = selection["preorder"]
    location_option = matched_locations[0]
    location_identity_digest = _digest(
        {
            "schema_version": "channel-location-option-identity/v1",
            "location_id": location_option["location_id"],
            "display_name": location_option["display_name"],
            "evidence_digest": location_option["evidence_digest"],
            "recommended": location_option["recommended"],
        }
    )
    if (
        not isinstance(stock, Mapping)
        or set(stock)
        != {
            "source",
            "source_digest",
            "quantity",
            "approval_reference",
        }
        or stock.get("source") != "kyle-explicit-seller-stock/v1"
        or stock.get("quantity")
        != creation_defaults.get("seller_stock_quantity")
        or not _is_digest(stock.get("approval_reference"))
        or stock.get("source_digest")
        != seller_stock_source_digest(
            context_digest=selection["context_digest"],
            creation_fact_identity_digest=stock[
                "approval_reference"
            ],
            location_identity_digest=location_identity_digest,
            quantity=stock["quantity"],
        )
        or selection.get("condition") != creation_defaults.get("condition")
        or preorder != creation_defaults.get("preorder")
    ):
        raise _error("shopee_creation_decision_drift", "CONTENT")


def _revalidate_single_sku_default_mapping(
    *,
    request: object,
    seed: Mapping[str, object],
    selection: Mapping[str, object],
) -> None:
    if not isinstance(request, Mapping):
        raise _error("shopee_sku_lineage_mapping_invalid", "CONTENT")
    lineage = request.get("sku_lineage")
    assignment = (
        lineage.get("assignment") if isinstance(lineage, Mapping) else None
    )
    rows = (
        assignment.get("model_skus")
        if isinstance(assignment, Mapping)
        else None
    )
    if (
        not isinstance(rows, list)
        or not rows
        or any(not isinstance(row, Mapping) for row in rows)
    ):
        raise _error("shopee_sku_lineage_mapping_invalid", "CONTENT")
    model_skus = [row.get("model_sku") for row in rows]
    if any(type(value) is not str or not value for value in model_skus):
        raise _error("shopee_sku_lineage_mapping_invalid", "CONTENT")
    if len(model_skus) != 1:
        # Multi-SKU can only proceed when an exact, separately approved
        # variation mapping exists.  The v2 platform intentionally does not
        # derive one, so this remains an actionable capability blocker.
        raise _error(
            "shopee_multi_sku_variation_mapping_required", "CAPABILITY"
        )
    positions = seed.get("selected_image_positions")
    pricing = seed.get("target_pricing")
    stock = selection.get("seller_stock")
    if (
        not isinstance(positions, list)
        or not positions
        or type(positions[0]) is not int
        or positions[0] <= 0
        or not isinstance(pricing, Mapping)
        or not isinstance(stock, Mapping)
    ):
        raise _error("shopee_single_sku_mapping_invalid", "CONTENT")
    expected_tier = [
        {
            "name": "Default",
            "option_list": [
                {
                    "option": "Default",
                    "approved_image_position": positions[0],
                }
            ],
        }
    ]
    expected_models = [
        {
            "global_model_sku": model_skus[0],
            "tier_index": [0],
            "original_price_cny": pricing.get(
                "global_original_price"
            ),
            "seller_stock_quantity": stock.get("quantity"),
        }
    ]
    if (
        selection.get("tier_variation") != expected_tier
        or selection.get("global_model") != expected_models
    ):
        raise _error("shopee_single_sku_mapping_drift", "CONTENT")


def _read_all_brands(
    transport: ShopeePrepareTransport, selected_id: int | None
) -> tuple[Mapping[str, object], ...]:
    if selected_id is None:
        return ()
    offset = 0
    expected_total: int | None = None
    rows: list[Mapping[str, object]] = []
    seen_offsets: set[int] = set()
    seen_ids: set[int] = set()
    for _page in range(100):
        if offset in seen_offsets:
            raise _error("shopee_brand_pagination_invalid")
        seen_offsets.add(offset)
        raw = _official_get(
            transport,
            BRAND_LIST_PATH,
            {
                "category_id": selected_id,
                "page_size": 100,
                "offset": offset,
                "status": "NORMAL",
                "language": "en",
            },
        )
        response = _response_mapping(raw, "shopee_brand_list_invalid")
        page_rows = _mapping_list(
            response.get("brand_list"), "shopee_brand_list_invalid"
        )
        total = _nonnegative_int(
            response.get("total_count"), "shopee_brand_list_invalid"
        )
        if expected_total is None:
            expected_total = total
        if total != expected_total or type(response.get("has_next_page")) is not bool:
            raise _error("shopee_brand_list_invalid")
        for row in page_rows:
            if set(row) != {"brand_id", "original_brand_name"}:
                raise _error("shopee_brand_list_invalid")
            brand_id = _nonnegative_int(
                row["brand_id"], "shopee_brand_list_invalid"
            )
            if brand_id in seen_ids:
                raise _error("shopee_brand_list_invalid")
            seen_ids.add(brand_id)
            rows.append(
                {
                    "brand_id": brand_id,
                    "original_brand_name": _nonempty_string(
                        row["original_brand_name"],
                        "shopee_brand_list_invalid",
                    ),
                }
            )
        if response["has_next_page"] is False:
            if len(rows) != total:
                raise _error("shopee_brand_pagination_invalid")
            return tuple(rows)
        next_offset = _positive_int(
            response.get("next_offset"), "shopee_brand_pagination_invalid"
        )
        if next_offset <= offset:
            raise _error("shopee_brand_pagination_invalid")
        offset = next_offset
    raise _error("shopee_brand_pagination_invalid")


def _read_seller_locations(
    transport: ShopeePrepareTransport,
) -> tuple[Mapping[str, object], ...]:
    raw = _official_get(transport, SELLER_LOCATION_PATH, {})
    response = _response_value(raw, "shopee_seller_location_invalid")
    rows = _mapping_list(response, "shopee_seller_location_invalid")
    seen: set[str] = set()
    normalized: list[Mapping[str, object]] = []
    for row in rows:
        if set(row) != {"location_id", "warehouse_name"}:
            raise _error("shopee_seller_location_invalid", "LOGISTICS")
        location_id = _nonempty_string(
            row["location_id"], "shopee_seller_location_invalid"
        )
        if location_id in seen:
            raise _error("shopee_seller_location_invalid", "LOGISTICS")
        seen.add(location_id)
        normalized.append(
            {
                "location_id": location_id,
                "warehouse_name": _nonempty_string(
                    row["warehouse_name"],
                    "shopee_seller_location_invalid",
                ),
            }
        )
    return tuple(normalized)


def _official_get(
    transport: ShopeePrepareTransport,
    path: str,
    params: Mapping[str, object],
) -> object:
    if path not in AUDITED_OFFICIAL_READ_ENDPOINTS:
        raise _error("shopee_unapproved_observation_endpoint")
    try:
        return transport.merchant_get(path, dict(params))
    except ShopeeGlobalPlanCandidateError:
        raise
    except (TimeoutError, OSError) as error:
        raise _error(
            "shopee_official_observation_transport_unavailable"
        ) from error
    except Exception as error:
        raise _error("shopee_official_observation_failed") from error


def _response_value(value: object, code: str) -> object:
    if not isinstance(value, Mapping):
        raise _error(code)
    allowed = {"error", "message", "request_id", "response"}
    if set(value) - allowed or "response" not in value or value.get("error"):
        category = "AUTH" if _looks_auth_error(value.get("error")) else "CAPABILITY"
        raise _error(code, category)
    return value["response"]


def _response_mapping(value: object, code: str) -> Mapping[str, object]:
    response = _response_value(value, code)
    if not isinstance(response, Mapping):
        raise _error(code)
    return response


def _mapping_list(value: object, code: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list) or any(
        not isinstance(row, Mapping) for row in value
    ):
        raise _error(code)
    return value


def _positive_unique_ids(
    value: object, code: str, *, allow_empty: bool
) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise _error(code)
    result = tuple(_positive_int(item, code) for item in value)
    if (not allow_empty and not result) or len(result) != len(set(result)):
        raise _error(code)
    return result


def _optional_positive_int(value: object, code: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, code)


def _positive_int(value: object, code: str) -> int:
    if type(value) is not int or value <= 0:
        raise _error(code)
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if type(value) is not int or value < 0:
        raise _error(code)
    return value


def _nonempty_string(value: object, code: str) -> str:
    if type(value) is not str or not value.strip():
        raise _error(code)
    return value


def _looks_auth_error(value: object) -> bool:
    return type(value) is str and any(
        marker in value.casefold()
        for marker in ("token", "auth", "permission", "credential")
    )


def _is_digest(value: object) -> bool:
    return bool(
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _error(
    code: str, category: str = "CAPABILITY"
) -> ShopeeGlobalPlanCandidateError:
    return ShopeeGlobalPlanCandidateError(
        code, "official Shopee candidate observation failed", category=category
    )


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
