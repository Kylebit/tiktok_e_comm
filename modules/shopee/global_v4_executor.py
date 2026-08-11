"""Frozen-v4 Shopee CNSC global-product convergence boundary.

The module contains no credential, dashboard, ReleasePlan or HTTP knowledge.
It projects one already-approved ``approved-publication-snapshot/v4`` into a
deterministic global-product command and delegates provider I/O plus durable
identity checkpoints to an injected runtime.  This keeps policy decisions in
the frozen snapshot and transport details in the live dependency layer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from domains.product_operations import validate_approved_publication_snapshot
from modules.shopee.skill_regions import REGIONAL_TARGETS, selected_region_targets


class ShopeeGlobalV4Error(RuntimeError):
    """The frozen master cannot be safely converged or officially verified."""


class ShopeeGlobalV4Runtime(Protocol):
    """Normalized live boundary; provider adapters implement these operations."""

    def lookup_global_item_ids(
        self, command: Mapping[str, Any]
    ) -> Mapping[str, object]: ...

    def prepare_creation(
        self, command: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def upload_global_images(
        self, image_urls: Sequence[str]
    ) -> Mapping[str, object]: ...

    def persist_image_identities(
        self, request: object, bindings: Mapping[str, str]
    ) -> None: ...

    def create_global_item(self, payload: Mapping[str, Any]) -> object: ...

    def persist_global_identity(
        self, request: object, global_item_id: str, models: Sequence[str]
    ) -> None: ...

    def initialize_global_models(
        self, global_item_id: str, payload: Mapping[str, Any]
    ) -> object: ...

    def persist_global_model_identities(
        self,
        request: object,
        global_item_id: str,
        identities: Mapping[str, str],
    ) -> None: ...

    def retire_global_identity(
        self,
        request: object,
        global_item_id: str,
        model_skus: Sequence[str],
        reason: str,
    ) -> None: ...

    def read_global_item(self, global_item_id: str) -> Mapping[str, Any]: ...

    def read_global_models(self, global_item_id: str) -> Mapping[str, Any]: ...


def _text(value: object, name: str, *, max_length: int = 5000) -> str:
    if type(value) is not str:
        raise ShopeeGlobalV4Error(f"{name} is invalid")
    result = value.strip()
    if not result or result != value or len(result) > max_length:
        raise ShopeeGlobalV4Error(f"{name} is invalid")
    return result


def _decimal(value: object, name: str) -> str:
    if isinstance(value, bool) or type(value) not in {str, int, float, Decimal}:
        raise ShopeeGlobalV4Error(f"{name} is invalid")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ShopeeGlobalV4Error(f"{name} is invalid") from None
    if not number.is_finite() or number <= 0:
        raise ShopeeGlobalV4Error(f"{name} is invalid")
    rendered = format(number.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _same_decimal(left: object, right: object) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ShopeeGlobalV4Error(f"{name} is invalid")
    return value


def _rows(value: object, name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not value or any(
        not isinstance(row, Mapping) for row in value
    ):
        raise ShopeeGlobalV4Error(f"{name} is invalid")
    return list(value)


def _https_urls(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ShopeeGlobalV4Error(f"{name} is invalid")
    result = []
    for candidate in value:
        url = _text(candidate, name, max_length=2048)
        if not url.startswith("https://"):
            raise ShopeeGlobalV4Error(f"{name} is invalid")
        result.append(url)
    if len(result) != len(set(result)):
        raise ShopeeGlobalV4Error(f"{name} contains duplicates")
    return result


def _parcel_envelope(skus: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    weights: list[Decimal] = []
    packages: list[tuple[Decimal, Decimal, Decimal]] = []
    for row in skus:
        parcel = _mapping(row.get("parcel"), "Shopee SKU parcel")
        package = parcel.get("package_cm")
        if not isinstance(package, list) or len(package) != 3:
            raise ShopeeGlobalV4Error("Shopee SKU parcel is invalid")
        weights.append(Decimal(_decimal(parcel.get("weight_kg"), "Shopee SKU weight")))
        packages.append(
            tuple(Decimal(_decimal(value, "Shopee package dimension")) for value in package)
        )
    return {
        "weight_kg": _decimal(max(weights), "Shopee parcel weight"),
        "package_cm": [
            _decimal(max(package[index] for package in packages), "Shopee package dimension")
            for index in range(3)
        ],
    }


def project_shopee_global_v4_command(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Project only immutable v4 facts into one exact CNSC master command."""

    frozen = validate_approved_publication_snapshot(snapshot).payload()
    master = _mapping(frozen.get("shopee_global_master"), "Shopee global master")
    if master.get("schema_version") != "shopee-global-master/v1":
        raise ShopeeGlobalV4Error("Shopee global master schema is invalid")
    product = _mapping(frozen.get("product"), "approved Shopee product")
    product_images = _https_urls(product.get("images"), "approved Shopee image")
    skus = _rows(frozen.get("skus"), "approved Shopee SKUs")
    master_prices = _rows(
        master.get("sku_original_prices_cny"), "Shopee master prices"
    )
    prices: dict[str, str] = {}
    for row in master_prices:
        model_sku = _text(row.get("model_sku"), "Shopee model SKU", max_length=128)
        if model_sku in prices or str(row.get("currency") or "").upper() != "CNY":
            raise ShopeeGlobalV4Error("Shopee master price identity is ambiguous")
        prices[model_sku] = _decimal(row.get("amount"), "Shopee master price")

    image_positions = _rows(
        master.get("variant_image_positions"), "Shopee variant image positions"
    )
    images_by_sku: dict[str, str] = {}
    for row in image_positions:
        model_sku = _text(row.get("model_sku"), "Shopee model SKU", max_length=128)
        url = _text(row.get("image_url"), "Shopee variant image", max_length=2048)
        position = row.get("position")
        if (
            model_sku in images_by_sku
            or type(position) is not int
            or position < 0
            or position >= len(product_images)
            or product_images[position] != url
        ):
            raise ShopeeGlobalV4Error("Shopee variant image identity conflicts")
        images_by_sku[model_sku] = url

    variation_names: list[str] | None = None
    models: list[dict[str, Any]] = []
    seen_options: set[tuple[str, ...]] = set()
    seen_skus: set[str] = set()
    for row in skus:
        model_sku = _text(row.get("model_sku"), "Shopee model SKU", max_length=128)
        if model_sku in seen_skus:
            raise ShopeeGlobalV4Error("Shopee model SKU coverage is ambiguous")
        seen_skus.add(model_sku)
        specification = _mapping(row.get("specification"), "Shopee specification")
        if not 1 <= len(specification) <= 2:
            raise ShopeeGlobalV4Error("Shopee variations require one or two dimensions")
        names = [_text(name, "Shopee variation name", max_length=14) for name in specification]
        values = [
            _text(value, "Shopee variation option", max_length=30)
            for value in specification.values()
        ]
        if variation_names is None:
            variation_names = names
        elif variation_names != names:
            raise ShopeeGlobalV4Error("Shopee variation dimensions conflict")
        options = tuple(values)
        if options in seen_options:
            raise ShopeeGlobalV4Error("Shopee variation option identity is ambiguous")
        seen_options.add(options)
        if model_sku not in prices or model_sku not in images_by_sku:
            raise ShopeeGlobalV4Error("Shopee master SKU coverage is incomplete")
        models.append(
            {
                "model_sku": model_sku,
                "option_values": values,
                "price_cny": prices[model_sku],
                "variant_image_url": images_by_sku[model_sku],
            }
        )
    if set(prices) != seen_skus or set(images_by_sku) != seen_skus:
        raise ShopeeGlobalV4Error("Shopee master SKU coverage is incomplete")

    policy = deepcopy(_mapping(master.get("policy"), "Shopee global policy"))
    return {
        "schema_version": "shopee-global-v4-command/v1",
        "snapshot_digest": _text(
            frozen.get("snapshot_digest"), "snapshot digest", max_length=80
        ),
        "offer_id": _text(frozen.get("offer_id"), "offer id", max_length=32),
        "product_revision": frozen.get("product_revision"),
        "master_schema_version": master["schema_version"],
        "product": {
            "title": _text(product.get("title"), "Shopee title", max_length=255),
            "description": _text(
                product.get("description"), "Shopee description", max_length=5000
            ),
            "images": product_images,
        },
        "variation_names": variation_names or [],
        "models": models,
        "parcel": _parcel_envelope(skus),
        "category_decision": deepcopy(master["category_decision"]),
        "policy": policy,
    }


def _official_category_path(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ShopeeGlobalV4Error("Shopee official category path is incomplete")
    result: list[dict[str, str]] = []
    for row in value:
        candidate = _mapping(row, "Shopee official category path")
        result.append(
            {
                "id": _text(candidate.get("id"), "Shopee category id", max_length=64),
                "name": _text(candidate.get("name"), "Shopee category name", max_length=255),
            }
        )
    return result


def _validate_preparation(
    command: Mapping[str, Any], preparation: object
) -> dict[str, Any]:
    facts = _mapping(preparation, "Shopee official preparation")
    if facts.get("authority") != "SHOPEE_OFFICIAL":
        raise ShopeeGlobalV4Error("Shopee creation facts are not official")
    decision = _mapping(command.get("category_decision"), "Shopee category decision")
    status = decision.get("status")
    if status == "DEFERRED_TO_SKILL" and facts.get("recommendation_count") != 1:
        raise ShopeeGlobalV4Error("Shopee category recommendation is ambiguous")
    if status not in {"DEFERRED_TO_SKILL", "APPROVED"}:
        raise ShopeeGlobalV4Error("Shopee category decision is not executable")
    category = _mapping(facts.get("category"), "Shopee official category")
    normalized_category = {
        "id": _text(category.get("id"), "Shopee category id", max_length=64),
        "name": _text(category.get("name"), "Shopee category name", max_length=255),
        "path": _official_category_path(category.get("path")),
    }
    if normalized_category["path"][-1] != {
        "id": normalized_category["id"],
        "name": normalized_category["name"],
    }:
        raise ShopeeGlobalV4Error("Shopee official category path conflicts")
    attributes = facts.get("required_attributes")
    missing = facts.get("missing_required_attributes")
    if not isinstance(attributes, list) or not isinstance(missing, list) or missing:
        raise ShopeeGlobalV4Error("Shopee required attributes are incomplete")
    if any(not isinstance(row, Mapping) for row in attributes):
        raise ShopeeGlobalV4Error("Shopee required attributes are malformed")
    approved_category = decision.get("category")
    approved_attributes = decision.get("required_attributes")
    if status == "APPROVED" and (
        normalized_category != approved_category or attributes != approved_attributes
    ):
        raise ShopeeGlobalV4Error("Shopee approved category facts drifted")
    warehouse = _mapping(facts.get("warehouse"), "Shopee official warehouse")
    normalized_warehouse = {
        "location_id": _text(
            warehouse.get("location_id"), "Shopee warehouse location", max_length=128
        ),
        "display_name": _text(
            warehouse.get("display_name"), "Shopee warehouse name", max_length=255
        ),
    }
    frozen_warehouse = _mapping(
        _mapping(command.get("policy"), "Shopee policy").get("warehouse"),
        "Shopee warehouse policy",
    )
    if normalized_warehouse["display_name"] != frozen_warehouse.get("display_name"):
        raise ShopeeGlobalV4Error("Shopee warehouse identity drifted")
    if frozen_warehouse.get("status") == "APPROVED" and (
        normalized_warehouse["location_id"] != frozen_warehouse.get("location_id")
    ):
        raise ShopeeGlobalV4Error("Shopee approved warehouse identity drifted")
    return {
        "category": normalized_category,
        "required_attributes": deepcopy(attributes),
        "warehouse": normalized_warehouse,
    }


def _identity(value: object, name: str) -> str:
    if isinstance(value, bool):
        raise ShopeeGlobalV4Error(f"{name} is invalid")
    result = str(value or "").strip()
    if not result.isdigit() or int(result) <= 0:
        raise ShopeeGlobalV4Error(f"{name} is invalid")
    return result


def _status(item: Mapping[str, Any]) -> str:
    status = str(item.get("status") or item.get("global_item_status") or "").strip().upper()
    if not status:
        raise ShopeeGlobalV4Error("Shopee official global item status is unavailable")
    return status


def _verify_readback(
    command: Mapping[str, Any],
    *,
    global_item_id: str,
    item: object,
    model_response: object,
    expected_image_ids: Mapping[str, str] | None,
) -> None:
    observed_item = _mapping(item, "Shopee global item readback")
    if _identity(
        observed_item.get("global_item_id"), "Shopee global item identity"
    ) != global_item_id:
        raise ShopeeGlobalV4Error("Shopee official global item identity conflicts")
    if _status(observed_item) != "NORMAL":
        raise ShopeeGlobalV4Error("Shopee official global item is not NORMAL")
    product = command["product"]
    if (
        observed_item.get("title") != product["title"]
        or observed_item.get("description") != product["description"]
        or observed_item.get("image_urls") != product["images"]
    ):
        raise ShopeeGlobalV4Error("Shopee official title, description or images drifted")
    image_ids = observed_item.get("image_ids")
    if not isinstance(image_ids, list) or len(image_ids) != len(product["images"]) or any(
        not str(value or "").strip() for value in image_ids
    ):
        raise ShopeeGlobalV4Error("Shopee official global image identities are incomplete")
    parcel = _mapping(observed_item.get("parcel"), "Shopee global parcel")
    package = parcel.get("package_cm")
    expected_package = command["parcel"]["package_cm"]
    if (
        not _same_decimal(parcel.get("weight_kg"), command["parcel"]["weight_kg"])
        or not isinstance(package, list)
        or len(package) != 3
        or any(not _same_decimal(package[index], expected_package[index]) for index in range(3))
    ):
        raise ShopeeGlobalV4Error("Shopee official global parcel drifted")

    response = _mapping(model_response, "Shopee global model readback")
    if response.get("variation_names") != command["variation_names"]:
        raise ShopeeGlobalV4Error("Shopee official variation names drifted")
    observed_models = _rows(response.get("models"), "Shopee official global models")
    by_sku: dict[str, Mapping[str, Any]] = {}
    for row in observed_models:
        model_sku = str(row.get("model_sku") or "").strip()
        if not model_sku or model_sku in by_sku:
            raise ShopeeGlobalV4Error("Shopee official SKU coverage is ambiguous")
        by_sku[model_sku] = row
    expected = {row["model_sku"]: row for row in command["models"]}
    if set(by_sku) != set(expected) or len(observed_models) != len(expected):
        raise ShopeeGlobalV4Error("Shopee official SKU coverage is incomplete")
    for model_sku, facts in expected.items():
        observed = by_sku[model_sku]
        if str(observed.get("status") or "").upper() != "NORMAL":
            raise ShopeeGlobalV4Error("Shopee official model is not NORMAL")
        if observed.get("option_values") != facts["option_values"]:
            raise ShopeeGlobalV4Error("Shopee official variant options drifted")
        if not _same_decimal(observed.get("price_cny"), facts["price_cny"]):
            raise ShopeeGlobalV4Error("Shopee official CNY model price drifted")
        if observed.get("variant_image_url") != facts["variant_image_url"]:
            raise ShopeeGlobalV4Error("Shopee official variant image drifted")
        observed_image_id = str(observed.get("variant_image_id") or "").strip()
        if not observed_image_id:
            raise ShopeeGlobalV4Error("Shopee official variant image is missing")
        if expected_image_ids is not None and (
            observed_image_id != expected_image_ids[facts["variant_image_url"]]
        ):
            raise ShopeeGlobalV4Error("Shopee official variant image identity drifted")


class ShopeeGlobalV4Resolver:
    """Converge one global master and return its ID only after official verification."""

    def __init__(self, *, runtime: ShopeeGlobalV4Runtime) -> None:
        self._runtime = runtime
        self._write_counts: dict[tuple[str, str], int | None] = {}

    @staticmethod
    def _key(request: object) -> tuple[str, str]:
        return (
            _text(getattr(request, "run_id", None), "run id", max_length=255),
            _text(getattr(request, "report_id", None), "report id", max_length=255),
        )

    def write_count(self, request: object) -> int | None:
        return self._write_counts.get(self._key(request), 0)

    def __call__(self, request: object) -> str:
        key = self._key(request)
        self._write_counts[key] = 0
        if getattr(request, "platform", None) != "SHOPEE":
            raise ShopeeGlobalV4Error("publication platform identity conflicts")
        snapshot = getattr(request, "snapshot", None)
        if not isinstance(snapshot, Mapping):
            raise ShopeeGlobalV4Error("approved v4 snapshot is unavailable")
        expected_targets = tuple(selected_region_targets(snapshot))
        labels = getattr(request, "target_labels", None)
        if (
            not isinstance(labels, tuple)
            or labels != expected_targets
            or not labels
            or any(label not in REGIONAL_TARGETS for label in labels)
        ):
            raise ShopeeGlobalV4Error("Shopee regional target scope conflicts")
        command = project_shopee_global_v4_command(snapshot)
        model_skus = [row["model_sku"] for row in command["models"]]
        mapped = self._runtime.lookup_global_item_ids(deepcopy(command))
        if not isinstance(mapped, Mapping) or set(mapped) != set(model_skus):
            raise ShopeeGlobalV4Error("Shopee global item mapping coverage conflicts")
        values = [str(mapped[model_sku] or "").strip() for model_sku in model_skus]
        present = [value for value in values if value]
        if present and len(present) != len(values):
            raise ShopeeGlobalV4Error("Shopee global item mapping is partial")
        if present and len(set(present)) != 1:
            raise ShopeeGlobalV4Error("Shopee global item mapping is ambiguous")
        if present:
            existing_id = _identity(present[0], "Shopee global item identity")
            item = self._runtime.read_global_item(existing_id)
            status = _status(_mapping(item, "Shopee global item readback"))
            if status == "NORMAL":
                models = self._runtime.read_global_models(existing_id)
                _verify_readback(
                    command,
                    global_item_id=existing_id,
                    item=item,
                    model_response=models,
                    expected_image_ids=None,
                )
                return existing_id
            if status != "DELETED":
                raise ShopeeGlobalV4Error(
                    "Shopee mapped global item is neither NORMAL nor DELETED"
                )
            self._runtime.retire_global_identity(
                request,
                existing_id,
                model_skus,
                "SHOPEE_OFFICIAL_DELETED",
            )

        preparation = _validate_preparation(
            command, self._runtime.prepare_creation(deepcopy(command))
        )
        all_images = list(command["product"]["images"])
        for row in command["models"]:
            if row["variant_image_url"] not in all_images:
                all_images.append(row["variant_image_url"])
        try:
            raw_bindings = self._runtime.upload_global_images(tuple(all_images))
        except Exception:
            self._write_counts[key] = None
            raise
        self._write_counts[key] = 1
        if not isinstance(raw_bindings, Mapping) or set(raw_bindings) != set(all_images):
            raise ShopeeGlobalV4Error("Shopee uploaded image identity coverage conflicts")
        bindings = {
            url: _text(raw_bindings[url], "Shopee image identity", max_length=255)
            for url in all_images
        }
        self._runtime.persist_image_identities(request, deepcopy(bindings))
        create_payload = {
            "category": preparation["category"],
            "required_attributes": preparation["required_attributes"],
            "policy": deepcopy(command["policy"]),
            "warehouse": preparation["warehouse"],
            "product": {
                **deepcopy(command["product"]),
                "image_ids": [bindings[url] for url in command["product"]["images"]],
            },
            "parcel": deepcopy(command["parcel"]),
        }
        try:
            raw_global_item_id = self._runtime.create_global_item(create_payload)
        except Exception:
            self._write_counts[key] = None
            raise
        self._write_counts[key] = 2
        global_item_id = _identity(raw_global_item_id, "Shopee global item identity")
        self._runtime.persist_global_identity(
            request, global_item_id, list(model_skus)
        )
        model_payload = {
            "variation_names": list(command["variation_names"]),
            "models": [
                {
                    **deepcopy(row),
                    "variant_image_id": bindings[row["variant_image_url"]],
                    "stock": deepcopy(command["policy"]["stock"]),
                    "warehouse_location_id": preparation["warehouse"]["location_id"],
                }
                for row in command["models"]
            ],
        }
        try:
            raw_model_identities = self._runtime.initialize_global_models(
                global_item_id, model_payload
            )
        except Exception:
            self._write_counts[key] = None
            raise
        self._write_counts[key] = 3
        if (
            not isinstance(raw_model_identities, Mapping)
            or set(raw_model_identities) != set(model_skus)
        ):
            raise ShopeeGlobalV4Error(
                "Shopee created model identity coverage conflicts"
            )
        model_identities = {
            model_sku: _identity(
                raw_model_identities[model_sku], "Shopee global model identity"
            )
            for model_sku in model_skus
        }
        if len(set(model_identities.values())) != len(model_identities):
            raise ShopeeGlobalV4Error("Shopee global model identities are ambiguous")
        self._runtime.persist_global_model_identities(
            request,
            global_item_id,
            deepcopy(model_identities),
        )
        item = self._runtime.read_global_item(global_item_id)
        models = self._runtime.read_global_models(global_item_id)
        _verify_readback(
            command,
            global_item_id=global_item_id,
            item=item,
            model_response=models,
            expected_image_ids=bindings,
        )
        return global_item_id


__all__ = [
    "ShopeeGlobalV4Error",
    "ShopeeGlobalV4Resolver",
    "ShopeeGlobalV4Runtime",
    "project_shopee_global_v4_command",
]
