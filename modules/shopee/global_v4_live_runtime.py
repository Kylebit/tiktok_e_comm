"""Official Shopee CNSC runtime for the frozen-v4 global master.

This module is the provider edge for :mod:`modules.shopee.global_v4_executor`.
It never reads mutable Product Center facts.  Category selection starts from
the frozen user-approved semantic category and may choose only one official,
publishable Shopee leaf with an exact semantic alias.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from typing import Any


class ShopeeGlobalV4LiveRuntimeError(RuntimeError):
    """Official Shopee facts are missing, ambiguous, or conflict with v4."""


def _semantic_key(value: object) -> str:
    if type(value) is not str:
        return ""
    return re.sub(
        r"[^0-9a-z\u4e00-\u9fff]+",
        "",
        unicodedata.normalize("NFKC", value).strip().casefold(),
    )


_EXACT_CATEGORY_ALIASES = {
    "冰箱贴": frozenset(
        {
            "冰箱贴",
            "fridgemagnet",
            "fridgemagnets",
            "refrigeratormagnet",
            "refrigeratormagnets",
        }
    ),
    "fridgemagnet": frozenset(
        {
            "冰箱贴",
            "fridgemagnet",
            "fridgemagnets",
            "refrigeratormagnet",
            "refrigeratormagnets",
        }
    ),
    "fridgemagnets": frozenset(
        {
            "冰箱贴",
            "fridgemagnet",
            "fridgemagnets",
            "refrigeratormagnet",
            "refrigeratormagnets",
        }
    ),
    "墙贴": frozenset({"墙贴", "wallsticker", "wallstickers", "walldecal", "walldecals"}),
    "wallsticker": frozenset({"墙贴", "wallsticker", "wallstickers", "walldecal", "walldecals"}),
    "wallstickers": frozenset({"墙贴", "wallsticker", "wallstickers", "walldecal", "walldecals"}),
}


def _approved_semantic_aliases(main_category: Mapping[str, Any]) -> frozenset[str]:
    if not isinstance(main_category, Mapping) or type(main_category.get("name")) is not str:
        raise ShopeeGlobalV4LiveRuntimeError("approved main category is unavailable")
    leaf = re.split(r"\s*(?:>|＞|/|／)\s*", main_category["name"].strip())[-1]
    key = _semantic_key(leaf)
    if not key:
        raise ShopeeGlobalV4LiveRuntimeError("approved main category is unavailable")
    return _EXACT_CATEGORY_ALIASES.get(key, frozenset({key}))


def select_exact_official_category(
    main_category: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return one exact official leaf; never infer from title or description."""

    aliases = _approved_semantic_aliases(main_category)
    matches: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping) or candidate.get("publishable") is not True:
            continue
        category_id = str(candidate.get("id") or "").strip()
        name = str(candidate.get("name") or "").strip()
        path = candidate.get("path")
        if (
            not category_id.isdigit()
            or int(category_id) <= 0
            or not name
            or not isinstance(path, list)
            or not path
            or any(not isinstance(row, Mapping) for row in path)
            or _semantic_key(name) not in aliases
        ):
            continue
        normalized_path = [
            {"id": str(row.get("id") or "").strip(), "name": str(row.get("name") or "").strip()}
            for row in path
        ]
        if any(not row["id"] or not row["name"] for row in normalized_path):
            continue
        if normalized_path[-1] != {"id": category_id, "name": name}:
            continue
        matches.append(
            {
                "id": category_id,
                "name": name,
                "path": normalized_path,
                **{
                    key: deepcopy(candidate[key])
                    for key in (
                        "required_attributes",
                        "missing_required_attributes",
                    )
                    if key in candidate
                },
            }
        )
    if len(matches) != 1:
        raise ShopeeGlobalV4LiveRuntimeError(
            "Shopee exact semantic category is unavailable or ambiguous"
        )
    return matches[0]


def _default_context_resolver(command: Mapping[str, Any]) -> Mapping[str, object]:
    from modules.shopee.auth import ensure_merchant_token, ensure_shop_token, load_tokens
    from modules.shopee.shops import sync_shop_ids

    source = command.get("price_source")
    region = str(source.get("region") or "").upper() if isinstance(source, Mapping) else ""
    shop_id = sync_shop_ids().get(region)
    if isinstance(shop_id, bool) or not isinstance(shop_id, int) or shop_id <= 0:
        raise ShopeeGlobalV4LiveRuntimeError("Shopee master source shop is unavailable")
    shop_token = ensure_shop_token(shop_id)
    store = load_tokens()
    shop = (store.get("shops") or {}).get(str(shop_id)) or {}
    merchant_id = shop.get("merchant_id")
    if isinstance(merchant_id, bool) or not isinstance(merchant_id, int) or merchant_id <= 0:
        candidates = [
            int(value)
            for value in (store.get("merchant_id_list") or [])
            if str(value).isdigit() and int(value) > 0
        ]
        merchant_id = candidates[0] if len(set(candidates)) == 1 else None
    if not isinstance(merchant_id, int) or merchant_id <= 0:
        raise ShopeeGlobalV4LiveRuntimeError("Shopee merchant identity is unavailable")
    merchant_token = ensure_merchant_token(merchant_id, shop_id=shop_id)
    return {
        "region": region,
        "shop_id": shop_id,
        "shop_token": shop_token,
        "merchant_id": merchant_id,
        "merchant_token": merchant_token,
    }


def _approved_required_attributes(
    command: Mapping[str, Any], official_rows: Sequence[Mapping[str, object]]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    mandatory = {
        int(row["attribute_id"]): row
        for row in official_rows
        if row.get("is_mandatory") is True
    }
    decision = command.get("category_decision")
    if not mandatory:
        return [], []
    if not isinstance(decision, Mapping) or decision.get("status") != "APPROVED":
        return [], [
            {
                "attribute_id": attribute_id,
                "name": str(row.get("original_attribute_name") or ""),
            }
            for attribute_id, row in sorted(mandatory.items())
        ]
    approved = decision.get("required_attributes")
    if not isinstance(approved, list) or any(not isinstance(row, Mapping) for row in approved):
        raise ShopeeGlobalV4LiveRuntimeError("Shopee approved attributes are invalid")
    if {int(row.get("attribute_id") or 0) for row in approved} != set(mandatory):
        raise ShopeeGlobalV4LiveRuntimeError("Shopee approved attributes drifted")
    for row in approved:
        official = mandatory[int(row["attribute_id"])]
        official_values = {
            int(value["value_id"]): value
            for value in official.get("attribute_value_list", [])
        }
        selected = row.get("attribute_value_list")
        if not isinstance(selected, list) or not selected:
            raise ShopeeGlobalV4LiveRuntimeError("Shopee approved attributes are incomplete")
        for value in selected:
            if not isinstance(value, Mapping) or int(value.get("value_id") or -1) not in official_values:
                raise ShopeeGlobalV4LiveRuntimeError("Shopee approved attribute value drifted")
    return deepcopy(approved), []


def _default_official_fact_reader(
    command: Mapping[str, Any], context: Mapping[str, object]
) -> Mapping[str, object]:
    from modules.shopee.client import merchant_get, shop_get
    from modules.shopee.global_plan_candidate import (
        ATTRIBUTE_TREE_PATH,
        CATEGORY_RECOMMEND_PATH,
        ShopeeGlobalPlanCandidateError,
        _read_all_brands,
        _read_attribute_tree,
        _read_category_path,
        _read_seller_locations,
    )
    from modules.shopee.oneclick_release import ShopeeCredentials, ShopeePrepareTransport

    credentials = ShopeeCredentials(
        region=str(context["region"]),
        shop_id=int(context["shop_id"]),
        shop_token=str(context["shop_token"]),
        merchant_id=int(context["merchant_id"]),
        merchant_token=str(context["merchant_token"]),
    )
    transport = ShopeePrepareTransport(
        credentials=credentials,
        merchant_get=lambda path, params: merchant_get(
            path, credentials.merchant_id, credentials.merchant_token, dict(params)
        ),
        shop_get=lambda path, params=None: shop_get(
            path, credentials.shop_id, credentials.shop_token, dict(params or {})
        ),
    )
    raw = transport.merchant_get(
        CATEGORY_RECOMMEND_PATH,
        {"global_item_name": str((command.get("product") or {}).get("title") or "")},
    )
    response = raw.get("response") if isinstance(raw, Mapping) else None
    if not isinstance(response, Mapping) or raw.get("error") not in {None, "", "-"}:
        raise ShopeeGlobalV4LiveRuntimeError("Shopee category recommendation failed")
    fields = [key for key in ("category_id", "category_id_list") if key in response]
    if len(fields) != 1:
        raise ShopeeGlobalV4LiveRuntimeError("Shopee category recommendation is malformed")
    ids = response[fields[0]]
    ids = ids if isinstance(ids, list) else [ids]
    candidates: list[dict[str, object]] = []
    for value in ids:
        if isinstance(value, bool) or not str(value).isdigit() or int(str(value)) <= 0:
            continue
        category_id = int(str(value))
        try:
            path = _read_category_path(transport, category_id)
        except ShopeeGlobalPlanCandidateError:
            candidates.append(
                {
                    "id": str(category_id),
                    "name": str(category_id),
                    "path": [{"id": str(category_id), "name": str(category_id)}],
                    "publishable": False,
                }
            )
            continue
        normalized_path = [
            {"id": str(row["category_id"]), "name": str(row["name"])} for row in path
        ]
        candidates.append(
            {
                "id": str(category_id),
                "name": normalized_path[-1]["name"],
                "path": normalized_path,
                "publishable": True,
            }
        )
    selected = select_exact_official_category(command.get("main_category"), candidates)
    attributes = _read_attribute_tree(transport, int(selected["id"]))
    required, missing = _approved_required_attributes(command, attributes)
    for candidate in candidates:
        if candidate["id"] == selected["id"]:
            candidate["required_attributes"] = required
            candidate["missing_required_attributes"] = missing
    brands = _read_all_brands(transport, int(selected["id"]))
    no_brand = [
        row
        for row in brands
        if row.get("brand_id") == 0
        and _semantic_key(row.get("original_brand_name")) == "nobrand"
    ]
    locations = _read_seller_locations(transport)
    warehouses = [
        row
        for row in locations
        if unicodedata.normalize("NFC", str(row.get("warehouse_name") or "")).strip()
        == "中国仓库"
    ]
    if len(no_brand) != 1 or len(warehouses) != 1:
        raise ShopeeGlobalV4LiveRuntimeError("Shopee brand or warehouse policy is unavailable")
    return {
        "authority": "SHOPEE_OFFICIAL",
        "candidates": candidates,
        "brand": {
            "brand_id": 0,
            "original_brand_name": str(no_brand[0]["original_brand_name"]),
        },
        "warehouse": {
            "location_id": str(warehouses[0]["location_id"]),
            "display_name": str(warehouses[0]["warehouse_name"]),
        },
    }


def _default_image_upload(url: str, position: int) -> str:
    from modules.shopee.client import upload_image
    from modules.shopee.oneclick_release import _download_public_https_image

    prepared = _download_public_https_image(url)
    with tempfile.TemporaryDirectory(prefix="shopee_global_v4_image_") as directory:
        path = Path(directory) / f"image_{position}{prepared.suffix}"
        path.write_bytes(prepared.content)
        response = upload_image(path, scene="normal")
    image_id = ""
    if isinstance(response, Mapping):
        image_id = str(response.get("image_id") or "").strip()
        image_info = response.get("image_info")
        if not image_id and isinstance(image_info, Mapping):
            image_id = str(image_info.get("image_id") or "").strip()
        image_info_list = response.get("image_info_list")
        if not image_id and isinstance(image_info_list, list):
            for row in image_info_list:
                if not isinstance(row, Mapping):
                    continue
                nested = row.get("image_info")
                if isinstance(nested, Mapping):
                    image_id = str(nested.get("image_id") or "").strip()
                else:
                    image_id = str(row.get("image_id") or "").strip()
                if image_id:
                    break
    if not image_id:
        raise ShopeeGlobalV4LiveRuntimeError("Shopee uploaded image identity is unavailable")
    return image_id


def build_official_shopee_global_v4_runtime(
    *, checkpoint_root: Path | None = None
) -> "OfficialShopeeGlobalV4Runtime":
    from core.config import ROOT
    from modules.shopee.client import merchant_get, merchant_post
    from modules.shopee.global_sku_map import global_item_id_for_match_key

    return OfficialShopeeGlobalV4Runtime(
        context_resolver=_default_context_resolver,
        official_fact_reader=_default_official_fact_reader,
        mapping_lookup=global_item_id_for_match_key,
        merchant_get_transport=merchant_get,
        merchant_post_transport=merchant_post,
        image_upload_transport=_default_image_upload,
        checkpoint_root=checkpoint_root or ROOT / "reports" / "product-publication",
    )


class OfficialShopeeGlobalV4Runtime:
    """Production-shaped runtime with injectable deterministic provider edges."""

    def __init__(
        self,
        *,
        context_resolver: Callable[[Mapping[str, Any]], Mapping[str, object]],
        official_fact_reader: Callable[
            [Mapping[str, Any], Mapping[str, object]], Mapping[str, object]
        ],
        mapping_lookup: Callable[[str], str | None],
        merchant_get_transport: Callable[
            [str, int, str, dict[str, object]], Mapping[str, object]
        ] | None = None,
        merchant_post_transport: Callable[
            [str, int, str, dict[str, object]], Mapping[str, object]
        ] | None = None,
        image_upload_transport: Callable[[str, int], str] | None = None,
        checkpoint_root: Path | None = None,
    ) -> None:
        if not all(
            callable(value)
            for value in (context_resolver, official_fact_reader, mapping_lookup)
        ):
            raise TypeError("Shopee live runtime dependencies must be callable")
        self._context_resolver = context_resolver
        self._official_fact_reader = official_fact_reader
        self._mapping_lookup = mapping_lookup
        self._merchant_get = merchant_get_transport
        self._merchant_post = merchant_post_transport
        self._image_upload = image_upload_transport
        self._checkpoint_root = Path(checkpoint_root) if checkpoint_root else None
        self._active: ContextVar[dict[str, object] | None] = ContextVar(
            "shopee_global_v4_active", default=None
        )

    def lookup_global_item_ids(
        self, command: Mapping[str, Any]
    ) -> Mapping[str, object]:
        if not isinstance(command, Mapping):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global command is invalid")
        models = command.get("models")
        if not isinstance(models, list) or not models or any(
            not isinstance(row, Mapping)
            or type(row.get("model_sku")) is not str
            or not row["model_sku"].strip()
            for row in models
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global models are invalid")
        context = self._context_resolver(command)
        if not isinstance(context, Mapping):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee merchant context is invalid")
        self._active.set(
            {
                "command": deepcopy(dict(command)),
                "context": deepcopy(dict(context)),
                "image_bindings": {},
                "item_image_bindings": {},
            }
        )
        return {
            row["model_sku"]: self._mapping_lookup(row["model_sku"])
            for row in models
        }

    def prepare_creation(
        self, command: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        active = self._active.get()
        if active is None or active["command"] != command:
            raise ShopeeGlobalV4LiveRuntimeError("Shopee execution context is unavailable")
        facts = self._official_fact_reader(command, active["context"])
        if (
            not isinstance(facts, Mapping)
            or facts.get("authority") != "SHOPEE_OFFICIAL"
            or not isinstance(facts.get("candidates"), list)
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee official facts are invalid")
        category = select_exact_official_category(
            command.get("main_category"), facts["candidates"]
        )
        policy = command.get("policy")
        brand = facts.get("brand")
        warehouse = facts.get("warehouse")
        if not isinstance(policy, Mapping) or not isinstance(brand, Mapping):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee official policy facts are invalid")
        if (
            brand.get("brand_id") != (policy.get("brand") or {}).get("brand_id")
            or brand.get("original_brand_name")
            != (policy.get("brand") or {}).get("original_brand_name")
            or not isinstance(warehouse, Mapping)
            or warehouse.get("display_name")
            != (policy.get("warehouse") or {}).get("display_name")
            or not str(warehouse.get("location_id") or "").strip()
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee official policy facts drifted")
        return {
            "authority": "SHOPEE_OFFICIAL",
            "recommendation_count": 1,
            "category": {
                "id": category["id"],
                "name": category["name"],
                "path": deepcopy(category["path"]),
            },
            "required_attributes": deepcopy(category.get("required_attributes", [])),
            "missing_required_attributes": deepcopy(
                category.get("missing_required_attributes", [])
            ),
            "warehouse": {
                "location_id": str(warehouse["location_id"]),
                "display_name": str(warehouse["display_name"]),
            },
        }

    def _provider_context(self) -> tuple[dict[str, object], dict[str, Any]]:
        active = self._active.get()
        if active is None:
            raise ShopeeGlobalV4LiveRuntimeError("Shopee execution context is unavailable")
        context = active.get("context")
        command = active.get("command")
        if not isinstance(context, Mapping) or not isinstance(command, Mapping):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee execution context is invalid")
        merchant_id = context.get("merchant_id")
        merchant_token = context.get("merchant_token")
        if (
            isinstance(merchant_id, bool)
            or not isinstance(merchant_id, int)
            or merchant_id <= 0
            or type(merchant_token) is not str
            or not merchant_token
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee merchant identity is invalid")
        return dict(context), dict(command)

    @staticmethod
    def _provider_response(value: object, operation: str) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise ShopeeGlobalV4LiveRuntimeError(f"{operation} response is malformed")
        error = str(value.get("error") or "").strip()
        if error and error != "-":
            raise ShopeeGlobalV4LiveRuntimeError(f"{operation} was rejected")
        response = value.get("response")
        if not isinstance(response, Mapping):
            raise ShopeeGlobalV4LiveRuntimeError(f"{operation} response is malformed")
        return response

    def upload_global_images(self, image_urls: Sequence[str]) -> Mapping[str, object]:
        if (
            self._image_upload is None
            or not isinstance(image_urls, tuple)
            or not image_urls
            or len(image_urls) != len(set(image_urls))
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee image upload scope is invalid")
        bindings: dict[str, str] = {}
        for position, url in enumerate(image_urls):
            if type(url) is not str or not url.startswith("https://"):
                raise ShopeeGlobalV4LiveRuntimeError("Shopee image URL is invalid")
            image_id = str(self._image_upload(url, position)).strip()
            if not image_id or image_id in bindings.values():
                raise ShopeeGlobalV4LiveRuntimeError(
                    "Shopee uploaded image identity is invalid"
                )
            bindings[url] = image_id
        active = self._active.get()
        if active is None:
            raise ShopeeGlobalV4LiveRuntimeError("Shopee execution context is unavailable")
        active["image_bindings"] = deepcopy(bindings)
        return bindings

    def _checkpoint_update(self, request: object, update: Mapping[str, object]) -> None:
        if self._checkpoint_root is None:
            raise ShopeeGlobalV4LiveRuntimeError("Shopee checkpoint root is unavailable")
        snapshot = getattr(request, "snapshot", None)
        offer_id = str(snapshot.get("offer_id") or "") if isinstance(snapshot, Mapping) else ""
        revision = snapshot.get("product_revision") if isinstance(snapshot, Mapping) else None
        run_id = str(getattr(request, "run_id", "") or "").strip()
        report_id = str(getattr(request, "report_id", "") or "").strip()
        if (
            not offer_id.isdigit()
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
            or not run_id
            or not report_id
            or any(value in run_id for value in ("/", "\\", ".."))
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee checkpoint identity is invalid")
        target = (
            self._checkpoint_root
            / offer_id
            / str(revision)
            / run_id
            / "shopee-global-checkpoint.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "schema_version": "shopee-global-v4-checkpoint/v1",
            "offer_id": offer_id,
            "product_revision": revision,
            "run_id": run_id,
            "report_id": report_id,
        }
        if target.is_file():
            try:
                current = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ShopeeGlobalV4LiveRuntimeError(
                    "Shopee checkpoint is unreadable"
                ) from error
            if not isinstance(current, dict) or any(
                current.get(key) != payload[key]
                for key in ("schema_version", "offer_id", "product_revision", "run_id", "report_id")
            ):
                raise ShopeeGlobalV4LiveRuntimeError("Shopee checkpoint identity drifted")
            payload.update(current)
        payload.update(deepcopy(dict(update)))
        handle, temporary_name = tempfile.mkstemp(
            prefix=".shopee-global-", suffix=".json", dir=target.parent
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
            os.replace(temporary_name, target)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def persist_image_identities(
        self, request: object, bindings: Mapping[str, str]
    ) -> None:
        self._checkpoint_update(request, {"image_bindings": deepcopy(dict(bindings))})

    def create_global_item(self, payload: Mapping[str, Any]) -> object:
        if self._merchant_post is None or not isinstance(payload, Mapping):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee create transport is unavailable")
        context, _command = self._provider_context()
        product = payload.get("product")
        parcel = payload.get("parcel")
        category = payload.get("category")
        policy = payload.get("policy")
        warehouse = payload.get("warehouse")
        models = payload.get("models")
        if not all(isinstance(value, Mapping) for value in (product, parcel, category, policy, warehouse)):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee create payload is invalid")
        if not isinstance(models, list) or not models or not isinstance(product.get("image_ids"), list):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee create payload is incomplete")
        package = parcel.get("package_cm")
        stock = policy.get("stock")
        brand = policy.get("brand")
        preorder = policy.get("preorder")
        if (
            not isinstance(package, list)
            or len(package) != 3
            or not isinstance(stock, Mapping)
            or not isinstance(brand, Mapping)
            or not isinstance(preorder, Mapping)
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee create policy is invalid")
        body = {
            "category_id": int(str(category["id"])),
            "global_item_name": product["title"],
            "description": product["description"],
            "original_price": float(models[0]["price_cny"]),
            "weight": float(parcel["weight_kg"]),
            "dimension": {
                "package_length": float(package[0]),
                "package_width": float(package[1]),
                "package_height": float(package[2]),
            },
            "image": {"image_id_list": list(product["image_ids"])},
            "attribute_list": deepcopy(payload.get("required_attributes", [])),
            "brand": deepcopy(dict(brand)),
            "condition": policy.get("condition"),
            "seller_stock": [
                {
                    "location_id": str(warehouse["location_id"]),
                    "stock": int(stock["quantity"]),
                }
            ],
            "pre_order": deepcopy(dict(preorder)),
        }
        response = self._provider_response(
            self._merchant_post(
                "/api/v2/global_product/add_global_item",
                int(context["merchant_id"]),
                str(context["merchant_token"]),
                body,
            ),
            "Shopee global create",
        )
        global_item_id = str(response.get("global_item_id") or "").strip()
        if not global_item_id.isdigit() or int(global_item_id) <= 0:
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global identity is unavailable")
        return global_item_id

    def persist_global_identity(
        self, request: object, global_item_id: str, models: Sequence[str]
    ) -> None:
        _context, command = self._provider_context()
        from modules.shopee.global_sku_map import upsert_global_group_entry

        by_sku = {row["model_sku"]: row for row in command["models"]}
        upsert_global_group_entry(
            global_item_id,
            match_keys=list(models),
            title=command["product"]["title"],
            tier_name=" / ".join(command["variation_names"]),
            models=[
                {
                    "global_model_sku": model_sku,
                    "model_name": " / ".join(by_sku[model_sku]["option_values"]),
                }
                for model_sku in models
            ],
        )
        self._checkpoint_update(
            request,
            {"global_item_id": str(global_item_id), "model_skus": list(models)},
        )

    def initialize_global_models(
        self, global_item_id: str, payload: Mapping[str, Any]
    ) -> object:
        if self._merchant_post is None or self._merchant_get is None:
            raise ShopeeGlobalV4LiveRuntimeError("Shopee model transport is unavailable")
        context, _command = self._provider_context()
        names = payload.get("variation_names")
        models = payload.get("models")
        if (
            not isinstance(names, list)
            or not 1 <= len(names) <= 2
            or not isinstance(models, list)
            or not models
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global model payload is invalid")
        option_lists: list[list[str]] = [[] for _ in names]
        image_by_first_option: dict[str, str] = {}
        normalized_models = []
        for row in models:
            values = row.get("option_values") if isinstance(row, Mapping) else None
            if not isinstance(values, list) or len(values) != len(names):
                raise ShopeeGlobalV4LiveRuntimeError("Shopee option matrix is invalid")
            indices = []
            for index, value in enumerate(values):
                if value not in option_lists[index]:
                    option_lists[index].append(value)
                indices.append(option_lists[index].index(value))
            image_id = str(row.get("variant_image_id") or "").strip()
            previous = image_by_first_option.setdefault(values[0], image_id)
            if not image_id or previous != image_id:
                raise ShopeeGlobalV4LiveRuntimeError(
                    "Shopee first variation image identity conflicts"
                )
            normalized_models.append(
                {
                    "tier_index": indices,
                    "global_model_sku": row["model_sku"],
                    "original_price": float(row["price_cny"]),
                    "seller_stock": [
                        {
                            "location_id": str(row["warehouse_location_id"]),
                            "stock": int(row["stock"]["quantity"]),
                        }
                    ],
                }
            )
        tiers = []
        for index, name in enumerate(names):
            options = []
            for option in option_lists[index]:
                entry: dict[str, object] = {"option": option}
                if index == 0:
                    entry["image"] = {"image_id": image_by_first_option[option]}
                options.append(entry)
            tiers.append({"name": name, "option_list": options})
        self._provider_response(
            self._merchant_post(
                "/api/v2/global_product/init_tier_variation",
                int(context["merchant_id"]),
                str(context["merchant_token"]),
                {
                    "global_item_id": int(global_item_id),
                    "tier_variation": tiers,
                    "global_model": normalized_models,
                },
            ),
            "Shopee global model initialization",
        )
        raw = self._read_global_models_raw(global_item_id)
        identities = {
            str(row.get("global_model_sku") or "").strip(): str(
                row.get("global_model_id") or ""
            ).strip()
            for row in raw["models"]
        }
        if set(identities) != {str(row["model_sku"]) for row in models} or any(
            not value.isdigit() or int(value) <= 0 for value in identities.values()
        ):
            raise ShopeeGlobalV4LiveRuntimeError(
                "Shopee official model identities are incomplete"
            )
        return identities

    def persist_global_model_identities(
        self,
        request: object,
        global_item_id: str,
        identities: Mapping[str, str],
    ) -> None:
        from modules.shopee.global_sku_map import load_map, save_map

        data = load_map()
        entry = data.get(str(global_item_id))
        if not isinstance(entry, dict):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global mapping is unavailable")
        rows = entry.get("models")
        if not isinstance(rows, list):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee model mapping is unavailable")
        by_sku = {
            str(row.get("global_model_sku") or "").strip(): row
            for row in rows
            if isinstance(row, dict)
        }
        if set(by_sku) != set(identities):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee model mapping coverage conflicts")
        for model_sku, global_model_id in identities.items():
            by_sku[model_sku]["global_model_id"] = str(global_model_id)
        save_map(data)
        self._checkpoint_update(
            request,
            {"global_item_id": str(global_item_id), "global_model_ids": dict(identities)},
        )

    def retire_global_identity(
        self,
        request: object,
        global_item_id: str,
        model_skus: Sequence[str],
        reason: str,
    ) -> None:
        from modules.shopee.global_sku_map import load_map, save_map

        data = load_map()
        entry = data.get(str(global_item_id))
        if not isinstance(entry, dict):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee retired mapping is unavailable")
        keys = {str(entry.get("match_key") or "").strip(), *map(str, entry.get("match_keys") or [])}
        if not set(model_skus).issubset(keys):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee retired mapping identity conflicts")
        entry["retired_match_key"] = entry.get("match_key")
        entry["retired_match_keys"] = list(entry.get("match_keys") or [])
        entry["match_key"] = ""
        entry["match_keys"] = []
        entry["retired_reason"] = str(reason)
        save_map(data)
        self._checkpoint_update(
            request,
            {"retired_global_item_id": str(global_item_id), "retired_reason": str(reason)},
        )

    def _read_global_models_raw(self, global_item_id: str) -> dict[str, object]:
        if self._merchant_get is None:
            raise ShopeeGlobalV4LiveRuntimeError("Shopee model readback is unavailable")
        context, _command = self._provider_context()
        response = self._provider_response(
            self._merchant_get(
                "/api/v2/global_product/get_global_model_list",
                int(context["merchant_id"]),
                str(context["merchant_token"]),
                {"global_item_id": int(global_item_id)},
            ),
            "Shopee global model readback",
        )
        models = response.get("global_model")
        tiers = response.get("tier_variation")
        if not isinstance(models, list) or not isinstance(tiers, list):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global model readback is malformed")
        return {"models": models, "tiers": tiers}

    def read_global_item(self, global_item_id: str) -> Mapping[str, Any]:
        if self._merchant_get is None:
            raise ShopeeGlobalV4LiveRuntimeError("Shopee item readback is unavailable")
        context, _command = self._provider_context()
        response = self._provider_response(
            self._merchant_get(
                "/api/v2/global_product/get_global_item_info",
                int(context["merchant_id"]),
                str(context["merchant_token"]),
                {"global_item_id_list": str(global_item_id)},
            ),
            "Shopee global item readback",
        )
        rows = response.get("global_item_list")
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global item is missing or ambiguous")
        row = rows[0]
        image = row.get("image")
        dimension = row.get("dimension")
        if not isinstance(image, Mapping) or not isinstance(dimension, Mapping):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global item facts are incomplete")
        image_ids = list(image.get("image_id_list") or [])
        image_urls = list(image.get("image_url_list") or [])
        if len(image_ids) != len(image_urls):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee image readback is incomplete")
        active = self._active.get()
        if active is not None:
            active["item_image_bindings"] = dict(zip(map(str, image_ids), map(str, image_urls)))
        return {
            "global_item_id": str(row.get("global_item_id") or ""),
            "status": str(row.get("global_item_status") or ""),
            "title": row.get("global_item_name"),
            "description": row.get("description") or row.get("global_item_description"),
            "image_urls": image_urls,
            "image_ids": image_ids,
            "parcel": {
                "weight_kg": row.get("weight"),
                "package_cm": [
                    dimension.get("package_length"),
                    dimension.get("package_width"),
                    dimension.get("package_height"),
                ],
            },
        }

    def read_global_models(self, global_item_id: str) -> Mapping[str, Any]:
        raw = self._read_global_models_raw(global_item_id)
        tiers = raw["tiers"]
        models = raw["models"]
        if any(not isinstance(row, Mapping) for row in tiers + models):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee model readback is malformed")
        active = self._active.get() or {}
        image_bindings = active.get("item_image_bindings")
        if not isinstance(image_bindings, Mapping):
            image_bindings = {}
        normalized = []
        for row in models:
            indices = row.get("tier_index")
            if not isinstance(indices, list) or len(indices) != len(tiers):
                raise ShopeeGlobalV4LiveRuntimeError("Shopee model tier identity is invalid")
            option_values = []
            variant_image_id = ""
            variant_image_url = ""
            for index, tier_index in enumerate(indices):
                options = tiers[index].get("option_list")
                if not isinstance(options, list) or type(tier_index) is not int or not 0 <= tier_index < len(options):
                    raise ShopeeGlobalV4LiveRuntimeError("Shopee model tier identity is invalid")
                option = options[tier_index]
                if not isinstance(option, Mapping):
                    raise ShopeeGlobalV4LiveRuntimeError("Shopee model option is invalid")
                option_values.append(option.get("option"))
                if index == 0:
                    image = option.get("image")
                    if isinstance(image, Mapping):
                        variant_image_id = str(image.get("image_id") or "").strip()
                        variant_image_url = str(
                            image.get("image_url")
                            or image_bindings.get(variant_image_id)
                            or ""
                        ).strip()
            price = row.get("price_info")
            price_value = price.get("original_price") if isinstance(price, Mapping) else row.get("original_price")
            normalized.append(
                {
                    "model_sku": str(row.get("global_model_sku") or "").strip(),
                    "option_values": option_values,
                    "price_cny": price_value,
                    "variant_image_url": variant_image_url,
                    "variant_image_id": variant_image_id,
                    "status": str(row.get("global_model_status") or row.get("status") or "").upper(),
                }
            )
        return {
            "variation_names": [str(row.get("name") or "") for row in tiers],
            "models": normalized,
        }


__all__ = [
    "OfficialShopeeGlobalV4Runtime",
    "ShopeeGlobalV4LiveRuntimeError",
    "build_official_shopee_global_v4_runtime",
    "select_exact_official_category",
]
