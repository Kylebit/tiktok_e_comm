"""Self-contained immutable publication facts frozen at ReleasePlan approval."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
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
    "skus",
    "digests",
}


class ApprovedPublicationSnapshotError(ValueError):
    """Raised when approval inputs or a serialized snapshot fail closed."""


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
            "category": category,
            "source_identity": source_contract.payload(),
        },
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
    if set(product) != {"title", "description", "images", "category", "source_identity"}:
        raise ApprovedPublicationSnapshotError("snapshot product fields are invalid")
    _text(product.get("title"), "snapshot title")
    _text(product.get("description"), "snapshot description")
    _text_list(product.get("images"), "snapshot images")
    category = _string_mapping(product.get("category"), "snapshot category")
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
        if set(prices) != set(target_labels):
            raise ApprovedPublicationSnapshotError("snapshot SKU price coverage drifted")
        for target, price in prices.items():
            _money(price, f"snapshot {target} price")
    if len(sellers) != 1:
        raise ApprovedPublicationSnapshotError("snapshot seller SKU identity conflicts")
    if not offer_id:
        raise ApprovedPublicationSnapshotError("snapshot offer identity is missing")


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
        rows = _mapping_list(target_pricing.get("sku_prices"), f"{target} SKU prices")
        seen: set[str] = set()
        currency: str | None = None
        for row in rows:
            model = _digits(row.get("model_sku"), f"{target} model_sku")
            if model not in result or model in seen:
                raise ApprovedPublicationSnapshotError(
                    f"{target} SKU price identity conflicts"
                )
            price = _money(
                {"amount": row.get("list_price"), "currency": row.get("currency")},
                f"{target} price",
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
            if set(row) != {"target_label", "platform", "store"}:
                raise ApprovedPublicationSnapshotError("publication target fields are invalid")
            label = _text(row.get("target_label"), "target_label")
            platform = _text(row.get("platform"), "target platform")
            store = _text(row.get("store"), "target store")
        else:
            label = _text(raw, "target_label")
            if label.count(":") != 1:
                raise ApprovedPublicationSnapshotError("target_label is invalid")
            platform, store = label.split(":", 1)
            platform = _text(platform, "target platform")
            store = _text(store, "target store")
        if label != f"{platform}:{store}":
            raise ApprovedPublicationSnapshotError("target identity conflicts")
        projected.append(
            {"target_label": label, "platform": platform, "store": store}
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
    "validate_approved_publication_snapshot",
]
