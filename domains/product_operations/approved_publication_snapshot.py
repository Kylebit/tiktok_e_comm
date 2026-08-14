"""Self-contained immutable publication facts frozen at ReleasePlan approval."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import json
import re
from typing import Any

from .source_identity import (
    SCHEMA_VERSION as SOURCE_IDENTITY_SCHEMA_VERSION,
    SourceIdentityEvidence,
    SourceProductIdentity,
)


APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION = (
    "approved-publication-snapshot/v4"
)

_DIGEST = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")
_DIGITS = re.compile(r"[0-9]{1,32}\Z")
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_CONTROL_ONLY_TARGETS = frozenset({"miaoshou:COMMON"})
_NON_PROVIDER_CATEGORY_DECISION_SCHEMA_VERSION = (
    "publication-category-decision/v1"
)
_SHOPEE_GLOBAL_MASTER_SCHEMA_VERSION = "shopee-global-master/v1"
# User-approved provider rule: only Shopee envelope dimensions use per-axis
# centimetre ceilings.  Per-SKU parcel facts, exact weight, prices, and every
# other platform projection remain unchanged.
_SHOPEE_PARCEL_ENVELOPE_POLICY_VERSION = "shopee-global-parcel-ceil-cm/v1"
_BODY_KEYS = {
    "schema_version",
    "offer_id",
    "product_revision",
    "plan_id",
    "approved_at",
    "approved_by",
    "publication_targets",
    "bindings",
    "product",
    "categories_by_target",
    "shopee_global_master",
    "skus",
    "digests",
}


class ApprovedPublicationSnapshotError(ValueError):
    """Raised when approval inputs or a serialized snapshot fail closed."""


def publication_category_decision_digest(
    *,
    target_label: str,
    platform: str,
    site: str,
    store: str,
    status: str,
) -> str:
    """Return the reproducible digest for a category-less target decision."""

    if status not in {"DEFERRED_TO_SKILL", "NOT_APPLICABLE"}:
        raise ApprovedPublicationSnapshotError(
            "category-less decision status is invalid"
        )
    body = {
        "schema_version": _NON_PROVIDER_CATEGORY_DECISION_SCHEMA_VERSION,
        "target_label": _text(target_label, "category decision target_label"),
        "platform": _text(platform, "category decision platform"),
        "site": _text(site, "category decision site"),
        "store": _text(store, "category decision store"),
        "category": None,
        "status": status,
    }
    return "sha256:" + hashlib.sha256(
        _canonical_json(body).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class ApprovedPublicationSnapshot:
    """Immutable JSON document represented internally by canonical text."""

    _canonical_body_json: str
    snapshot_digest: str

    def __post_init__(self) -> None:
        try:
            body = json.loads(self._canonical_body_json)
        except (TypeError, ValueError) as exc:
            raise ApprovedPublicationSnapshotError(
                "snapshot canonical JSON is invalid"
            ) from exc
        if not isinstance(body, dict) or _canonical_json(body) != self._canonical_body_json:
            raise ApprovedPublicationSnapshotError(
                "snapshot body must use canonical JSON"
            )
        _validate_frozen_body(body)
        if self.snapshot_digest != _sha256(body):
            raise ApprovedPublicationSnapshotError(
                "snapshot_digest does not match canonical payload"
            )

    @property
    def schema_version(self) -> str:
        return APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION

    def canonical_payload(self) -> dict[str, Any]:
        """Return a detached JSON-ready body covered by snapshot_digest."""

        return json.loads(self._canonical_body_json)

    def canonical_json(self) -> str:
        return self._canonical_body_json

    def payload(self) -> dict[str, Any]:
        document = self.canonical_payload()
        document["snapshot_digest"] = self.snapshot_digest
        return document


def build_approved_publication_snapshot(
    approved_plan: Mapping[str, Any],
) -> ApprovedPublicationSnapshot:
    """Freeze one approved ReleasePlan into a self-contained v4 snapshot."""

    if not isinstance(approved_plan, Mapping):
        raise ApprovedPublicationSnapshotError("approved ReleasePlan is missing")
    if approved_plan.get("status") != "APPROVED":
        raise ApprovedPublicationSnapshotError("ReleasePlan is not approved")
    approval = _mapping(approved_plan.get("approval"), "approval")
    if approval.get("status") != "APPROVED" or approval.get("user_approved") is not True:
        raise ApprovedPublicationSnapshotError(
            "literal approved approval and user_approved=True are required"
        )
    approved_by = _text(approval.get("approved_by"), "approved_by")
    approved_at = _timestamp(
        approval.get("approved_at") or approved_plan.get("approved_at"),
        "approved_at",
    )
    outer_approved_at = approved_plan.get("approved_at")
    if outer_approved_at is not None and _timestamp(
        outer_approved_at, "ReleasePlan approved_at"
    ) != approved_at:
        raise ApprovedPublicationSnapshotError("approval timestamps conflict")

    payload = _mapping(approved_plan.get("payload"), "ReleasePlan payload")
    plan_id = _text(payload.get("plan_id"), "plan_id")
    offer_id = _digits(payload.get("product_id"), "offer_id")
    revision = _integer(payload.get("product_revision"), "product_revision")
    if revision < 0:
        raise ApprovedPublicationSnapshotError("product_revision cannot be negative")
    _same(approved_plan.get("plan_id"), plan_id, "ReleasePlan plan_id")
    _same(approved_plan.get("product_id"), offer_id, "ReleasePlan offer_id")
    _same(approval.get("plan_id"), plan_id, "approval plan_id")

    calculated_plan_digest = _sha256(payload)
    supplied_plan_digest = _digest(
        approved_plan.get("payload_digest"), "ReleasePlan payload_digest"
    )
    if supplied_plan_digest != calculated_plan_digest:
        raise ApprovedPublicationSnapshotError("ReleasePlan payload_digest drifted")
    if _digest(approval.get("payload_digest"), "approval payload_digest") != supplied_plan_digest:
        raise ApprovedPublicationSnapshotError("approval payload_digest drifted")

    targets = _targets(payload.get("targets"))
    if list(approved_plan.get("targets") or ()) != [row["target_label"] for row in targets]:
        raise ApprovedPublicationSnapshotError("ReleasePlan target selection drifted")

    product_facts = _mapping(payload.get("product_facts"), "product_facts")
    title = _text(product_facts.get("title"), "approved title")
    description = _text(product_facts.get("description"), "approved description")
    images = _text_list(product_facts.get("image_urls"), "approved product images")
    category = _string_mapping(product_facts.get("category"), "approved category")
    if not category.get("id") or not category.get("name"):
        raise ApprovedPublicationSnapshotError(
            "approved category requires id and name"
        )
    categories_by_target = _target_categories(
        product_facts.get("categories_by_target"),
        targets=targets,
    )

    source = _mapping(
        payload.get("source_product_identity"), "source_product_identity"
    )
    source_contract = _source_identity(source)
    source_digest = source_contract.identity_digest

    raw_digests = _mapping(payload.get("digests"), "approved digests")
    digests = {
        name: _digest(raw_digests.get(name), f"{name} digest")
        for name in (
            "source",
            "content",
            "policy",
            "category",
            "pricing",
            "sku_lineage",
        )
    }
    if digests["source"] != source_digest:
        raise ApprovedPublicationSnapshotError("source digest conflicts")

    lineage = _mapping(payload.get("sku_lineage"), "sku_lineage")
    if _digest(
        lineage.get("source_identity_digest"), "SKU lineage source digest"
    ) != source_digest:
        raise ApprovedPublicationSnapshotError("SKU lineage source identity conflicts")
    if _digest(
        lineage.get("reservation_digest"), "SKU lineage reservation digest"
    ) != digests["sku_lineage"]:
        raise ApprovedPublicationSnapshotError("SKU lineage digest conflicts")
    assignment = _mapping(lineage.get("assignment"), "SKU assignment")
    seller_sku = _digits(assignment.get("seller_sku"), "seller_sku")
    _same(payload.get("seller_sku"), seller_sku, "plan seller_sku")
    model_rows = _mapping_list(assignment.get("model_skus"), "model_skus")
    if not model_rows:
        raise ApprovedPublicationSnapshotError("at least one model SKU is required")
    selected_variants = _text_list(
        product_facts.get("selected_sku_keys"), "selected_sku_keys"
    )
    commercial = _mapping(
        product_facts.get("sku_commercial_facts"), "sku_commercial_facts"
    )
    variant_models: list[tuple[str, str]] = []
    for row in model_rows:
        variant_models.append(
            (
                _text(row.get("variant_key"), "variant_key"),
                _digits(row.get("model_sku"), "model_sku"),
            )
        )
    variants = [row[0] for row in variant_models]
    models = [row[1] for row in variant_models]
    if len(set(variants)) != len(variants) or len(set(models)) != len(models):
        raise ApprovedPublicationSnapshotError("SKU identities are ambiguous")
    if selected_variants != variants or set(commercial) != set(variants):
        raise ApprovedPublicationSnapshotError("SKU coverage conflicts")

    prices_by_model = _prices(
        payload.get("pricing"),
        target_labels=[row["target_label"] for row in targets],
        model_skus=models,
    )
    skus: list[dict[str, Any]] = []
    for variant_key, model_sku in variant_models:
        row = _mapping(commercial[variant_key], f"SKU {variant_key}")
        specification = _string_mapping(
            row.get("specification"), f"SKU {variant_key} specification"
        )
        if not specification:
            raise ApprovedPublicationSnapshotError("SKU specification is required")
        cost = _money(row.get("cost"), f"SKU {variant_key} cost")
        parcel = {
            "weight_kg": _positive_decimal(
                row.get("weight_kg"), f"SKU {variant_key} weight"
            ),
            "package_cm": _dimensions(
                row.get("package_cm"), f"SKU {variant_key} package"
            ),
        }
        skus.append(
            {
                "variant_key": variant_key,
                "seller_sku": seller_sku,
                "model_sku": model_sku,
                "specification": specification,
                "cost": cost,
                "parcel": parcel,
                "prices": prices_by_model[model_sku],
                "variant_images": _text_list(
                    row.get("image_urls"), f"SKU {variant_key} images"
                ),
            }
        )

    shopee_global_master = _shopee_global_master(
        payload.get("shopee_global_master"),
        targets=targets,
        skus=skus,
        approved_product_images=images,
    )

    body = {
        "schema_version": APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION,
        "offer_id": offer_id,
        "product_revision": revision,
        "plan_id": plan_id,
        "approved_at": approved_at,
        "approved_by": approved_by,
        "publication_targets": targets,
        "bindings": {
            "release_payload_digest": supplied_plan_digest,
            "product_package_id": _text(
                payload.get("product_package_id"), "product_package_id"
            ),
            "content_package_id": _text(
                payload.get("content_package_id"), "content_package_id"
            ),
        },
        "product": {
            "title": title,
            "description": description,
            "images": images,
            "main_category": category,
            "source_identity": source_contract.payload(),
        },
        "categories_by_target": categories_by_target,
        "shopee_global_master": shopee_global_master,
        "skus": skus,
        "digests": digests,
    }
    return _snapshot(body)


def approved_publication_snapshot_from_payload(
    payload: Mapping[str, Any],
) -> ApprovedPublicationSnapshot:
    """Deserialize an untrusted JSON-ready document and verify it fully."""

    if not isinstance(payload, Mapping):
        raise ApprovedPublicationSnapshotError("snapshot payload must be a mapping")
    document = _json_copy(payload, "snapshot payload")
    supplied_digest = _digest(
        document.pop("snapshot_digest", None), "snapshot_digest"
    )
    _validate_frozen_body(document)
    if _sha256(document) != supplied_digest:
        raise ApprovedPublicationSnapshotError("snapshot payload was tampered")
    return ApprovedPublicationSnapshot(_canonical_json(document), supplied_digest)


def validate_approved_publication_snapshot(
    snapshot: ApprovedPublicationSnapshot | Mapping[str, Any],
) -> ApprovedPublicationSnapshot:
    """Validate a typed snapshot or deserialize and validate a payload."""

    if type(snapshot) is ApprovedPublicationSnapshot:
        return approved_publication_snapshot_from_payload(snapshot.payload())
    return approved_publication_snapshot_from_payload(snapshot)


def _snapshot(body: Mapping[str, Any]) -> ApprovedPublicationSnapshot:
    _validate_frozen_body(body)
    canonical = _canonical_json(body)
    return ApprovedPublicationSnapshot(canonical, _sha256(body))


def _validate_frozen_body(body: Mapping[str, Any]) -> None:
    if set(body) != _BODY_KEYS:
        raise ApprovedPublicationSnapshotError("snapshot body fields are invalid")
    if body.get("schema_version") != APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION:
        raise ApprovedPublicationSnapshotError("snapshot schema_version is invalid")
    offer_id = _digits(body.get("offer_id"), "snapshot offer_id")
    revision = _integer(body.get("product_revision"), "snapshot product_revision")
    if revision < 0:
        raise ApprovedPublicationSnapshotError("snapshot revision cannot be negative")
    _text(body.get("plan_id"), "snapshot plan_id")
    _timestamp(body.get("approved_at"), "snapshot approved_at")
    _text(body.get("approved_by"), "snapshot approved_by")
    targets = _targets(body.get("publication_targets"), already_projected=True)
    target_labels = [row["target_label"] for row in targets]
    price_target_labels = [
        label for label in target_labels if label not in _CONTROL_ONLY_TARGETS
    ]

    bindings = _mapping(body.get("bindings"), "snapshot bindings")
    if set(bindings) != {
        "release_payload_digest",
        "product_package_id",
        "content_package_id",
    }:
        raise ApprovedPublicationSnapshotError("snapshot bindings are invalid")
    _digest(bindings.get("release_payload_digest"), "release payload digest")
    _text(bindings.get("product_package_id"), "product package binding")
    _text(bindings.get("content_package_id"), "content package binding")

    product = _mapping(body.get("product"), "snapshot product")
    if set(product) != {
        "title",
        "description",
        "images",
        "main_category",
        "source_identity",
    }:
        raise ApprovedPublicationSnapshotError("snapshot product fields are invalid")
    _text(product.get("title"), "snapshot title")
    _text(product.get("description"), "snapshot description")
    _text_list(product.get("images"), "snapshot images")
    category = _string_mapping(
        product.get("main_category"), "snapshot main category"
    )
    if not category.get("id") or not category.get("name"):
        raise ApprovedPublicationSnapshotError("snapshot category is incomplete")
    source = _mapping(product.get("source_identity"), "snapshot source identity")
    if set(source) != {
        "schema_version",
        "source_offer_id",
        "source_item_code",
        "source_authority",
        "provenance",
        "identity_digest",
    }:
        raise ApprovedPublicationSnapshotError("snapshot source identity fields are invalid")
    source_digest = _source_identity(source).identity_digest
    _target_categories(body.get("categories_by_target"), targets=targets)

    digests = _mapping(body.get("digests"), "snapshot digests")
    required_digests = {
        "source", "content", "policy", "category", "pricing", "sku_lineage"
    }
    if set(digests) != required_digests:
        raise ApprovedPublicationSnapshotError("snapshot digest coverage is invalid")
    normalized_digests = {
        key: _digest(value, f"snapshot {key} digest")
        for key, value in digests.items()
    }
    if normalized_digests["source"] != source_digest:
        raise ApprovedPublicationSnapshotError("snapshot source digest conflicts")

    rows = _mapping_list(body.get("skus"), "snapshot skus")
    if not rows:
        raise ApprovedPublicationSnapshotError("snapshot requires at least one SKU")
    sellers: set[str] = set()
    models: set[str] = set()
    variants: set[str] = set()
    for row in rows:
        if set(row) != {
            "variant_key",
            "seller_sku",
            "model_sku",
            "specification",
            "cost",
            "parcel",
            "prices",
            "variant_images",
        }:
            raise ApprovedPublicationSnapshotError("snapshot SKU fields are invalid")
        variant = _text(row.get("variant_key"), "snapshot variant_key")
        seller = _digits(row.get("seller_sku"), "snapshot seller_sku")
        model = _digits(row.get("model_sku"), "snapshot model_sku")
        if variant in variants or model in models:
            raise ApprovedPublicationSnapshotError("snapshot SKU identities conflict")
        variants.add(variant)
        models.add(model)
        sellers.add(seller)
        if not _string_mapping(row.get("specification"), "snapshot specification"):
            raise ApprovedPublicationSnapshotError("snapshot specification is empty")
        _money(row.get("cost"), "snapshot cost")
        parcel = _mapping(row.get("parcel"), "snapshot parcel")
        if set(parcel) != {"weight_kg", "package_cm"}:
            raise ApprovedPublicationSnapshotError("snapshot parcel fields are invalid")
        _positive_decimal(parcel.get("weight_kg"), "snapshot weight")
        _dimensions(parcel.get("package_cm"), "snapshot package")
        _text_list(row.get("variant_images"), "snapshot variant images")
        prices = _mapping(row.get("prices"), "snapshot prices")
        if set(prices) != set(price_target_labels):
            raise ApprovedPublicationSnapshotError("snapshot SKU price coverage drifted")
        for target, price in prices.items():
            _publication_price(price, f"snapshot {target} price", target=target)
    if len(sellers) != 1:
        raise ApprovedPublicationSnapshotError("snapshot seller SKU identity conflicts")
    _shopee_global_master(
        body.get("shopee_global_master"),
        targets=targets,
        skus=[dict(row) for row in rows],
        approved_product_images=_text_list(
            product.get("images"), "snapshot images"
        ),
    )
    if not offer_id:
        raise ApprovedPublicationSnapshotError("snapshot offer identity is missing")


def _shopee_global_master(
    value: Any,
    *,
    targets: list[dict[str, str]],
    skus: list[dict[str, Any]],
    approved_product_images: list[str],
) -> dict[str, Any] | None:
    shopee_targets = {
        row["target_label"]
        for row in targets
        if row["platform"] == "shopee"
    }
    if not shopee_targets:
        if value is not None:
            raise ApprovedPublicationSnapshotError(
                "Shopee global master exists without a Shopee target"
            )
        return None
    row = _mapping(value, "Shopee global master")
    if "parcel_envelope" not in row:
        raise ApprovedPublicationSnapshotError("Shopee parcel envelope is missing")
    if set(row) != {
        "schema_version",
        "price_source",
        "sku_original_prices_cny",
        "category_decision",
        "parcel_envelope",
        "policy",
        "variant_image_positions",
    } or row.get("schema_version") != _SHOPEE_GLOBAL_MASTER_SCHEMA_VERSION:
        raise ApprovedPublicationSnapshotError(
            "Shopee global master fields are invalid"
        )

    source = _mapping(row.get("price_source"), "Shopee master price source")
    if set(source) != {
        "target_label",
        "region",
        "target_key",
        "source_binding_digest",
    }:
        raise ApprovedPublicationSnapshotError(
            "Shopee global master price source fields are invalid"
        )
    target_label = _text(source.get("target_label"), "Shopee master target")
    region = _text(source.get("region"), "Shopee master region")
    target_key = _text(source.get("target_key"), "Shopee master target_key")
    if (
        target_label not in shopee_targets
        or target_label != f"shopee:{region}"
    ):
        raise ApprovedPublicationSnapshotError(
            "Shopee global master price source conflicts"
        )
    expected_source_digest = _sha256(
        {
            "schema_version": "shopee-global-master-price-source/v1",
            "target_label": target_label,
            "region": region,
            "target_key": target_key,
        }
    )
    if _digest(
        source.get("source_binding_digest"),
        "Shopee master source binding digest",
    ) != expected_source_digest:
        raise ApprovedPublicationSnapshotError(
            "Shopee global master price source digest conflicts"
        )

    models = [
        _digits(sku.get("model_sku"), "Shopee global master model_sku")
        for sku in skus
    ]
    price_rows = _mapping_list(
        row.get("sku_original_prices_cny"),
        "Shopee global master SKU prices",
    )
    normalized_prices: list[dict[str, str]] = []
    seen_prices: set[str] = set()
    sku_by_model = {str(sku.get("model_sku")): sku for sku in skus}
    for raw in price_rows:
        if set(raw) != {"model_sku", "amount", "currency"}:
            raise ApprovedPublicationSnapshotError(
                "Shopee global master SKU price fields are invalid"
            )
        model = _digits(raw.get("model_sku"), "Shopee global master model_sku")
        if model not in sku_by_model or model in seen_prices:
            raise ApprovedPublicationSnapshotError(
                "Shopee global master SKU price identities conflict"
            )
        if raw.get("currency") != "CNY":
            raise ApprovedPublicationSnapshotError(
                "Shopee global master currency must be CNY"
            )
        amount = _positive_decimal(raw.get("amount"), "Shopee global master price")
        source_price = _mapping(
            _mapping(sku_by_model[model].get("prices"), "snapshot prices").get(
                target_label
            ),
            "Shopee global master source target price",
        )
        expected_amount = source_price.get("global_original_price_cny")
        if expected_amount is None or _positive_decimal(
            expected_amount, "Shopee source global price"
        ) != amount:
            raise ApprovedPublicationSnapshotError(
                "Shopee global master price conflicts with its approved source"
            )
        seen_prices.add(model)
        normalized_prices.append(
            {"model_sku": model, "amount": amount, "currency": "CNY"}
        )
    if [price["model_sku"] for price in normalized_prices] != models:
        raise ApprovedPublicationSnapshotError(
            "Shopee global master SKU price coverage conflicts"
        )

    category_decision = _shopee_global_category_decision(
        row.get("category_decision")
    )
    parcel_envelope = _shopee_parcel_envelope(row.get("parcel_envelope"), skus=skus)
    policy = _shopee_global_policy(row.get("policy"))

    position_rows = _mapping_list(
        row.get("variant_image_positions"),
        "Shopee global variant image positions",
    )
    positions: list[dict[str, Any]] = []
    seen_positions: set[str] = set()
    for raw in position_rows:
        if set(raw) != {"model_sku", "position", "image_url"}:
            raise ApprovedPublicationSnapshotError(
                "Shopee global variant image position fields are invalid"
            )
        model = _digits(raw.get("model_sku"), "Shopee variant image model_sku")
        position = raw.get("position")
        image_url = _text(raw.get("image_url"), "Shopee variant image URL")
        if (
            model not in sku_by_model
            or model in seen_positions
            or type(position) is not int
            or position < 0
            or position >= len(approved_product_images)
            or approved_product_images[position] != image_url
        ):
            raise ApprovedPublicationSnapshotError(
                "Shopee global variant image positions conflict"
            )
        seen_positions.add(model)
        positions.append(
            {"model_sku": model, "position": position, "image_url": image_url}
        )
    if [position["model_sku"] for position in positions] != models:
        raise ApprovedPublicationSnapshotError(
            "Shopee global variant image position coverage conflicts"
        )
    return {
        "schema_version": _SHOPEE_GLOBAL_MASTER_SCHEMA_VERSION,
        "price_source": {
            "target_label": target_label,
            "region": region,
            "target_key": target_key,
            "source_binding_digest": expected_source_digest,
        },
        "sku_original_prices_cny": normalized_prices,
        "category_decision": category_decision,
        "parcel_envelope": parcel_envelope,
        "policy": policy,
        "variant_image_positions": positions,
    }


def _shopee_parcel_envelope(
    value: Any, *, skus: list[dict[str, Any]]
) -> dict[str, Any]:
    row = _mapping(value, "Shopee parcel envelope")
    if set(row) != {"weight_kg", "package_cm", "policy_version"}:
        raise ApprovedPublicationSnapshotError(
            "Shopee parcel envelope fields are invalid"
        )
    if row.get("policy_version") != _SHOPEE_PARCEL_ENVELOPE_POLICY_VERSION:
        raise ApprovedPublicationSnapshotError(
            "Shopee parcel envelope policy is invalid"
        )
    package = row.get("package_cm")
    if (
        not _sequence(package)
        or len(package) != 3
        or any(type(value) is not int or value <= 0 for value in package)
    ):
        raise ApprovedPublicationSnapshotError(
            "Shopee parcel envelope dimensions are invalid"
        )

    approved_weights: list[Decimal] = []
    approved_packages: list[list[Decimal]] = []
    for sku in skus:
        parcel = _mapping(sku.get("parcel"), "Shopee SKU parcel")
        approved_weights.append(
            Decimal(_positive_decimal(parcel.get("weight_kg"), "Shopee SKU weight"))
        )
        approved_packages.append(
            [
                Decimal(value)
                for value in _dimensions(
                    parcel.get("package_cm"), "Shopee SKU package"
                )
            ]
        )
    expected_weight = _positive_decimal(
        str(max(approved_weights)), "Shopee parcel envelope weight"
    )
    expected_package = [
        int(
            max(values[index] for values in approved_packages).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        for index in range(3)
    ]
    if (
        _positive_decimal(row.get("weight_kg"), "Shopee parcel envelope weight")
        != expected_weight
        or list(package) != expected_package
    ):
        raise ApprovedPublicationSnapshotError(
            "Shopee parcel envelope conflicts with approved SKU parcels"
        )
    return {
        "weight_kg": expected_weight,
        "package_cm": expected_package,
        "policy_version": _SHOPEE_PARCEL_ENVELOPE_POLICY_VERSION,
    }


def _shopee_global_category_decision(value: Any) -> dict[str, Any]:
    row = _mapping(value, "Shopee global category decision")
    if set(row) != {
        "status",
        "category",
        "required_attributes",
        "source_decision_digest",
        "decision_digest",
    }:
        raise ApprovedPublicationSnapshotError(
            "Shopee global category decision fields are invalid"
        )
    status = _text(row.get("status"), "Shopee global category status")
    attributes = row.get("required_attributes")
    if not _sequence(attributes) or any(
        not isinstance(attribute, Mapping) for attribute in attributes
    ):
        raise ApprovedPublicationSnapshotError(
            "Shopee global required attributes are invalid"
        )
    normalized_attributes = json.loads(
        json.dumps(
            list(attributes),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    if status == "DEFERRED_TO_SKILL":
        if (
            row.get("category") is not None
            or normalized_attributes
            or row.get("source_decision_digest") is not None
        ):
            raise ApprovedPublicationSnapshotError(
                "deferred Shopee global category cannot contain guessed facts"
            )
        expected = _sha256(
            {
                "schema_version": "shopee-global-category-decision/v1",
                "status": status,
                "category": None,
                "required_attributes": [],
                "source_decision_digest": None,
            }
        )
        if _digest(
            row.get("decision_digest"), "Shopee global category decision digest"
        ) != expected:
            raise ApprovedPublicationSnapshotError(
                "Shopee global category decision digest conflicts"
            )
        category = None
        source_decision_digest = None
        decision_digest = expected
    elif status == "APPROVED":
        raw_category = _mapping(row.get("category"), "Shopee global category")
        if set(raw_category) != {"id", "name", "path"}:
            raise ApprovedPublicationSnapshotError(
                "Shopee global category fields are invalid"
            )
        category_id = _text(raw_category.get("id"), "Shopee global category id")
        category_name = _text(
            raw_category.get("name"), "Shopee global category name"
        )
        path_rows = _mapping_list(
            raw_category.get("path"), "Shopee global category path"
        )
        path: list[dict[str, str]] = []
        for node in path_rows:
            if set(node) != {"id", "name"}:
                raise ApprovedPublicationSnapshotError(
                    "Shopee global category path fields are invalid"
                )
            path.append(
                {
                    "id": _text(node.get("id"), "Shopee global path id"),
                    "name": _text(node.get("name"), "Shopee global path name"),
                }
            )
        if not path or path[-1] != {"id": category_id, "name": category_name}:
            raise ApprovedPublicationSnapshotError(
                "Shopee global category path identity conflicts"
            )
        category = {"id": category_id, "name": category_name, "path": path}
        source_decision_digest = _digest(
            row.get("source_decision_digest"),
            "Shopee global source category decision digest",
        )
        expected = _sha256(
            {
                "schema_version": "shopee-global-category-decision/v1",
                "status": status,
                "category": category,
                "required_attributes": normalized_attributes,
                "source_decision_digest": source_decision_digest,
            }
        )
        if _digest(
            row.get("decision_digest"), "Shopee global category decision digest"
        ) != expected:
            raise ApprovedPublicationSnapshotError(
                "Shopee global category decision digest conflicts"
            )
        decision_digest = expected
    else:
        raise ApprovedPublicationSnapshotError(
            "Shopee global category status is invalid"
        )
    return {
        "status": status,
        "category": category,
        "required_attributes": normalized_attributes,
        "source_decision_digest": source_decision_digest,
        "decision_digest": decision_digest,
    }


def _shopee_global_policy(value: Any) -> dict[str, Any]:
    row = _mapping(value, "Shopee global policy")
    if set(row) != {"brand", "condition", "preorder", "stock", "warehouse"}:
        raise ApprovedPublicationSnapshotError("Shopee global policy fields are invalid")
    brand = _mapping(row.get("brand"), "Shopee global brand policy")
    if (
        set(brand) != {"brand_id", "original_brand_name", "policy_version"}
        or brand.get("brand_id") != 0
        or brand.get("original_brand_name") != "NoBrand"
        or brand.get("policy_version") != "shopee-global-fixed-no-brand/v1"
    ):
        raise ApprovedPublicationSnapshotError("Shopee global NoBrand policy drifted")
    if row.get("condition") != "NEW":
        raise ApprovedPublicationSnapshotError("Shopee global condition policy drifted")
    preorder = _mapping(row.get("preorder"), "Shopee global preorder policy")
    if preorder != {"is_pre_order": False, "days_to_ship": 1}:
        raise ApprovedPublicationSnapshotError("Shopee global preorder policy drifted")
    stock = _mapping(row.get("stock"), "Shopee global stock policy")
    if stock != {
        "quantity": 200,
        "policy_version": "shopee-global-fixed-stock/v1",
    }:
        raise ApprovedPublicationSnapshotError("Shopee global stock policy drifted")
    warehouse = _mapping(row.get("warehouse"), "Shopee global warehouse policy")
    if set(warehouse) != {
        "display_name",
        "location_id",
        "policy_version",
        "status",
    } or warehouse.get("display_name") != "中国仓库" or warehouse.get(
        "policy_version"
    ) != "shopee-global-fixed-china-warehouse/v1":
        raise ApprovedPublicationSnapshotError("Shopee global warehouse policy drifted")
    warehouse_status = warehouse.get("status")
    if warehouse_status == "DEFERRED_TO_SKILL":
        if warehouse.get("location_id") is not None:
            raise ApprovedPublicationSnapshotError(
                "deferred Shopee warehouse cannot contain a guessed location"
            )
        location_id = None
    elif warehouse_status == "APPROVED":
        location_id = _text(
            warehouse.get("location_id"), "Shopee global warehouse location_id"
        )
    else:
        raise ApprovedPublicationSnapshotError("Shopee global warehouse status is invalid")
    return {
        "brand": dict(brand),
        "condition": "NEW",
        "preorder": {"is_pre_order": False, "days_to_ship": 1},
        "stock": dict(stock),
        "warehouse": {
            "display_name": "中国仓库",
            "location_id": location_id,
            "policy_version": "shopee-global-fixed-china-warehouse/v1",
            "status": warehouse_status,
        },
    }


def _prices(
    raw_pricing: Any,
    *,
    target_labels: list[str],
    model_skus: list[str],
) -> dict[str, dict[str, dict[str, str]]]:
    pricing = _mapping(raw_pricing, "pricing")
    selected = _mapping(pricing.get("selected_targets"), "selected target pricing")
    if set(selected) != set(target_labels):
        raise ApprovedPublicationSnapshotError("pricing target coverage conflicts")
    result = {model: {} for model in model_skus}
    for target in target_labels:
        target_pricing = _mapping(selected[target], f"{target} pricing")
        if target in _CONTROL_ONLY_TARGETS:
            continue
        rows = _mapping_list(target_pricing.get("sku_prices"), f"{target} SKU prices")
        seen: set[str] = set()
        currency: str | None = None
        for row in rows:
            model = _digits(row.get("model_sku"), f"{target} model_sku")
            if model not in result or model in seen:
                raise ApprovedPublicationSnapshotError(
                    f"{target} SKU price identity conflicts"
                )
            price = _publication_price(
                {"amount": row.get("list_price"), "currency": row.get("currency")},
                f"{target} price",
                target=target,
                global_original_price_cny=row.get("global_original_price_cny"),
                old_price_cny=row.get("old_price_cny"),
            )
            if currency is not None and price["currency"] != currency:
                raise ApprovedPublicationSnapshotError(
                    f"{target} price currencies conflict"
                )
            currency = price["currency"]
            result[model][target] = price
            seen.add(model)
        if seen != set(model_skus):
            raise ApprovedPublicationSnapshotError(
                f"{target} SKU price coverage conflicts"
            )
    return result


def _target_categories(
    value: Any,
    *,
    targets: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    rows = _mapping(value, "categories_by_target")
    expected = {row["target_label"]: row for row in targets}
    if set(rows) != set(expected):
        missing = sorted(set(expected) - set(rows))
        extra = sorted(set(rows) - set(expected))
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if extra:
            detail.append("extra=" + ",".join(extra))
        raise ApprovedPublicationSnapshotError(
            "target category coverage conflicts"
            + (": " + "; ".join(detail) if detail else "")
        )
    result: dict[str, dict[str, Any]] = {}
    seen_identities: set[tuple[str, str, str, str]] = set()
    for label, target in expected.items():
        row = _mapping(rows[label], f"{label} category decision")
        if set(row) != {
            "target_label",
            "platform",
            "site",
            "store",
            "category",
            "decision",
        }:
            raise ApprovedPublicationSnapshotError(
                f"{label} category decision fields are invalid"
            )
        target_label = _text(row.get("target_label"), f"{label} target_label")
        platform = _text(row.get("platform"), f"{label} platform")
        site = _text(row.get("site"), f"{label} site")
        store = _text(row.get("store"), f"{label} store")
        identity = (target_label, platform, site, store)
        expected_identity = (
            label,
            target["platform"],
            target["site"],
            target["store"],
        )
        if identity != expected_identity or identity in seen_identities:
            raise ApprovedPublicationSnapshotError(
                f"{label} target category identity conflicts"
            )
        seen_identities.add(identity)
        decision = _mapping(row.get("decision"), f"{label} category decision audit")
        if set(decision) != {"status", "decision_digest"}:
            raise ApprovedPublicationSnapshotError(
                f"{label} category decision audit fields are invalid"
            )
        status = _text(decision.get("status"), f"{label} category status")
        decision_digest = _digest(
            decision.get("decision_digest"), f"{label} category decision_digest"
        )
        if label in _CONTROL_ONLY_TARGETS:
            if row.get("category") is not None or status != "NOT_APPLICABLE":
                raise ApprovedPublicationSnapshotError(
                    f"{label} control-only category must be explicitly NOT_APPLICABLE"
                )
            category: dict[str, Any] | None = None
        else:
            if status == "DEFERRED_TO_SKILL":
                if row.get("category") is not None:
                    raise ApprovedPublicationSnapshotError(
                        f"{label} deferred provider category must be null"
                    )
                expected_digest = publication_category_decision_digest(
                    target_label=target_label,
                    platform=platform,
                    site=site,
                    store=store,
                    status=status,
                )
                if decision_digest != expected_digest:
                    raise ApprovedPublicationSnapshotError(
                        f"{label} deferred category decision digest conflicts"
                    )
                category = None
            elif status != "APPROVED":
                raise ApprovedPublicationSnapshotError(
                    f"{label} provider category is not approved"
                )
            else:
                raw_category = _mapping(row.get("category"), f"{label} provider category")
                if set(raw_category) != {"id", "name", "path"}:
                    raise ApprovedPublicationSnapshotError(
                        f"{label} provider category fields are invalid"
                    )
                category_id = _text(raw_category.get("id"), f"{label} category id")
                category_name = _text(
                    raw_category.get("name"), f"{label} category name"
                )
                raw_path = _mapping_list(
                    raw_category.get("path"), f"{label} category path"
                )
                if not raw_path:
                    raise ApprovedPublicationSnapshotError(
                        f"{label} provider category path is empty"
                    )
                path: list[dict[str, str]] = []
                for node in raw_path:
                    if set(node) != {"id", "name"}:
                        raise ApprovedPublicationSnapshotError(
                            f"{label} category path fields are invalid"
                        )
                    path.append(
                        {
                            "id": _text(node.get("id"), f"{label} path id"),
                            "name": _text(node.get("name"), f"{label} path name"),
                        }
                    )
                if path[-1] != {"id": category_id, "name": category_name}:
                    raise ApprovedPublicationSnapshotError(
                        f"{label} provider category path identity conflicts"
                    )
                category = {
                    "id": category_id,
                    "name": category_name,
                    "path": path,
                }
        result[label] = {
            "target_label": target_label,
            "platform": platform,
            "site": site,
            "store": store,
            "category": category,
            "decision": {
                "status": status,
                "decision_digest": decision_digest,
            },
        }
    return result


def _source_identity(value: Mapping[str, Any]) -> SourceProductIdentity:
    if value.get("schema_version") != SOURCE_IDENTITY_SCHEMA_VERSION:
        raise ApprovedPublicationSnapshotError("source identity schema is invalid")
    raw_provenance = _mapping_list(
        value.get("provenance"), "source identity provenance"
    )
    try:
        provenance = tuple(
            SourceIdentityEvidence(
                path=row.get("path"),
                source_offer_id=row.get("source_offer_id"),
            )
            for row in raw_provenance
        )
        return SourceProductIdentity(
            source_offer_id=value.get("source_offer_id"),
            source_item_code=value.get("source_item_code"),
            source_authority=value.get("source_authority"),
            provenance=provenance,
            identity_digest=value.get("identity_digest"),
            schema_version=value.get("schema_version"),
        )
    except (TypeError, ValueError) as exc:
        raise ApprovedPublicationSnapshotError(
            f"source identity contract is invalid: {exc}"
        ) from None


def _targets(value: Any, *, already_projected: bool = False) -> list[dict[str, str]]:
    if not _sequence(value):
        raise ApprovedPublicationSnapshotError("publication targets are invalid")
    projected: list[dict[str, str]] = []
    for raw in value:
        if already_projected:
            row = _mapping(raw, "publication target")
            if set(row) != {"target_label", "platform", "site", "store"}:
                raise ApprovedPublicationSnapshotError("publication target fields are invalid")
            label = _text(row.get("target_label"), "target_label")
            platform = _text(row.get("platform"), "target platform")
            site = _text(row.get("site"), "target site")
            store = _text(row.get("store"), "target store")
        else:
            label = _text(raw, "target_label")
            if label.count(":") != 1:
                raise ApprovedPublicationSnapshotError("target_label is invalid")
            platform, store = label.split(":", 1)
            platform = _text(platform, "target platform")
            store = _text(store, "target store")
            site = store
        if label != f"{platform}:{site}" or site != store:
            raise ApprovedPublicationSnapshotError("target identity conflicts")
        projected.append(
            {
                "target_label": label,
                "platform": platform,
                "site": site,
                "store": store,
            }
        )
    labels = [row["target_label"] for row in projected]
    if len(labels) != len(set(labels)):
        raise ApprovedPublicationSnapshotError("publication targets are duplicated")
    return projected


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise ApprovedPublicationSnapshotError(f"{name} must be a string-keyed mapping")
    return value


def _mapping_list(value: Any, name: str) -> list[Mapping[str, Any]]:
    if not _sequence(value) or any(not isinstance(row, Mapping) for row in value):
        raise ApprovedPublicationSnapshotError(f"{name} must be a list of mappings")
    return list(value)


def _sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ApprovedPublicationSnapshotError(f"{name} must be a non-empty built-in str")
    return value


def _digits(value: Any, name: str) -> str:
    if type(value) is not str or not _DIGITS.fullmatch(value) or int(value) <= 0:
        raise ApprovedPublicationSnapshotError(f"{name} must be a positive digit string")
    return value


def _integer(value: Any, name: str) -> int:
    if type(value) is not int:
        raise ApprovedPublicationSnapshotError(f"{name} must be a built-in int")
    return value


def _text_list(value: Any, name: str) -> list[str]:
    if not _sequence(value) or not value:
        raise ApprovedPublicationSnapshotError(f"{name} must be a non-empty list")
    result = [_text(item, name) for item in value]
    if len(result) != len(set(result)):
        raise ApprovedPublicationSnapshotError(f"{name} contains duplicates")
    return result


def _string_mapping(value: Any, name: str) -> dict[str, str]:
    source = _mapping(value, name)
    result = {_text(key, name): _text(item, name) for key, item in source.items()}
    return dict(sorted(result.items()))


def _positive_decimal(value: Any, name: str) -> str:
    if type(value) not in {str, int, float} or isinstance(value, bool):
        raise ApprovedPublicationSnapshotError(f"{name} must be a positive decimal")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ApprovedPublicationSnapshotError(f"{name} must be a positive decimal") from None
    if not decimal.is_finite() or decimal <= 0:
        raise ApprovedPublicationSnapshotError(f"{name} must be a positive decimal")
    normalized = format(decimal.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized


def _money(value: Any, name: str) -> dict[str, str]:
    row = _mapping(value, name)
    if set(row) != {"amount", "currency"}:
        raise ApprovedPublicationSnapshotError(f"{name} fields are invalid")
    currency = row.get("currency")
    if type(currency) is not str or not _CURRENCY.fullmatch(currency):
        raise ApprovedPublicationSnapshotError(f"{name} currency is invalid")
    return {
        "amount": _positive_decimal(row.get("amount"), f"{name} amount"),
        "currency": currency,
    }


def _publication_price(
    value: Any,
    name: str,
    *,
    target: str,
    global_original_price_cny: Any = None,
    old_price_cny: Any = None,
) -> dict[str, str]:
    row = _mapping(value, name)
    allowed = {"amount", "currency"}
    if target.startswith("shopee:"):
        allowed.add("global_original_price_cny")
    if target == "ozon:RU":
        allowed.add("old_price_cny")
    if not set(row).issubset(allowed) or not {"amount", "currency"}.issubset(row):
        raise ApprovedPublicationSnapshotError(f"{name} fields are invalid")
    result = _money(
        {"amount": row.get("amount"), "currency": row.get("currency")},
        name,
    )
    global_value = (
        row.get("global_original_price_cny")
        if "global_original_price_cny" in row
        else global_original_price_cny
    )
    if global_value is not None:
        if not target.startswith("shopee:"):
            raise ApprovedPublicationSnapshotError(f"{name} fields are invalid")
        result["global_original_price_cny"] = _positive_decimal(
            global_value, f"{name} global CNY amount"
        )
    old_value = row.get("old_price_cny") if "old_price_cny" in row else old_price_cny
    if old_value is not None:
        if target != "ozon:RU":
            raise ApprovedPublicationSnapshotError(f"{name} fields are invalid")
        result["old_price_cny"] = _positive_decimal(
            old_value, f"{name} old CNY amount"
        )
    return result


def _dimensions(value: Any, name: str) -> list[str]:
    if not _sequence(value) or len(value) != 3:
        raise ApprovedPublicationSnapshotError(f"{name} requires three dimensions")
    return [_positive_decimal(item, name) for item in value]


def _timestamp(value: Any, name: str) -> str:
    text = _text(value, name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ApprovedPublicationSnapshotError(f"{name} must be ISO-8601") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ApprovedPublicationSnapshotError(f"{name} must include a timezone")
    return parsed.isoformat()


def _digest(value: Any, name: str) -> str:
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise ApprovedPublicationSnapshotError(f"{name} must be canonical SHA-256")
    return "sha256:" + value.removeprefix("sha256:")


def _same(value: Any, expected: str, name: str) -> None:
    if value != expected:
        raise ApprovedPublicationSnapshotError(f"{name} conflicts")


def _json_copy(value: Any, name: str) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        result = json.loads(encoded)
    except (TypeError, ValueError):
        raise ApprovedPublicationSnapshotError(f"{name} must be JSON serializable") from None
    if not isinstance(result, dict):
        raise ApprovedPublicationSnapshotError(f"{name} must be a JSON object")
    return result


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ApprovedPublicationSnapshotError(
            "snapshot payload must be JSON serializable"
        ) from None


def _sha256(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


__all__ = [
    "APPROVED_PUBLICATION_SNAPSHOT_SCHEMA_VERSION",
    "ApprovedPublicationSnapshot",
    "ApprovedPublicationSnapshotError",
    "approved_publication_snapshot_from_payload",
    "build_approved_publication_snapshot",
    "publication_category_decision_digest",
    "validate_approved_publication_snapshot",
]
