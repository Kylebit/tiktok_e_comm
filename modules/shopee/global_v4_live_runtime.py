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
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unicodedata
from typing import Any

from modules.shopee.global_v4_executor import UpdateReceipt


_PRICE_READBACK_ATTEMPTS = 3
_PRICE_READBACK_DELAY_SECONDS = 1.0


class ShopeeGlobalV4LiveRuntimeError(RuntimeError):
    """Official Shopee facts are missing, ambiguous, or conflict with v4."""


class _ShopeeGlobalV4PreSubmitError(ShopeeGlobalV4LiveRuntimeError):
    """A model update failed before the provider POST was attempted."""

    provider_write_attempted = False


class ShopeeGlobalModelUpdateFailure(ShopeeGlobalV4LiveRuntimeError):
    """Credential-free classification for one attempted model mutation."""

    _KINDS = frozenset(
        {
            "ACCEPTED_UNVERIFIED",
            "BUSINESS_REJECTED",
            "MALFORMED_RESPONSE",
            "TRANSPORT_UNKNOWN",
        }
    )

    def __init__(
        self,
        *,
        kind: str,
        provider_code: str = "",
        http_status: int | None = None,
        request_id_digest: str = "",
        outcome_unknown: bool,
    ) -> None:
        if (
            kind not in self._KINDS
            or type(provider_code) is not str
            or provider_code != _safe_provider_code(provider_code)
            or (
                http_status is not None
                and (type(http_status) is not int or not 100 <= http_status <= 599)
            )
            or type(request_id_digest) is not str
            or (
                request_id_digest
                and not re.fullmatch(r"sha256:[0-9a-f]{64}", request_id_digest)
            )
            or type(outcome_unknown) is not bool
            or (
                kind
                in {
                    "ACCEPTED_UNVERIFIED",
                    "MALFORMED_RESPONSE",
                    "TRANSPORT_UNKNOWN",
                }
                and outcome_unknown is not True
            )
        ):
            raise ValueError("Shopee global model failure classification is invalid")
        super().__init__(f"Shopee global model update failed ({kind})")
        self.stage = "update_price"
        self.kind = kind
        self.provider_code = provider_code
        self.http_status = http_status
        self.request_id_digest = request_id_digest
        self.provider_write_attempted = True
        self.outcome_unknown = outcome_unknown


def _safe_provider_code(value: object) -> str:
    code = str(value or "").strip()
    if len(code) > 80 or not re.fullmatch(r"[A-Za-z0-9._-]*", code):
        return ""
    return code


def _request_id_digest(value: object) -> str:
    request_id = str(value or "").strip()
    if not request_id:
        return ""
    return "sha256:" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()


def _safe_http_status(error: BaseException) -> int | None:
    value = getattr(error, "code", None)
    if type(value) is int and 100 <= value <= 599:
        return value
    return None


def _normalized_seller_stock(value: object) -> list[dict[str, object]] | None:
    candidates = [value] if isinstance(value, Mapping) else value
    if not isinstance(candidates, list) or not candidates:
        return None
    normalized: list[dict[str, object]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            return None
        location_id = str(candidate.get("location_id") or "").strip()
        stock = candidate.get("stock")
        if not location_id or type(stock) is not int or stock < 0:
            return None
        normalized.append({"location_id": location_id, "stock": stock})
    return normalized


def _normalized_stock_info(value: object) -> list[dict[str, object]] | None:
    if not isinstance(value, list) or not value:
        return None
    normalized: list[dict[str, object]] = []
    for candidate in value:
        if not isinstance(candidate, Mapping):
            return None
        location_id = str(candidate.get("stock_location_id") or "").strip()
        quantities = (
            candidate.get("current_stock"),
            candidate.get("normal_stock"),
            candidate.get("reserved_stock"),
        )
        if (
            candidate.get("stock_type") != 2
            or not location_id
            or any(type(quantity) is not int or quantity < 0 for quantity in quantities)
        ):
            return None
        # Official observed quantities describe current provider inventory;
        # they are not the frozen approved publish quantity.
        normalized.append({"location_id": location_id})
    return normalized


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
    "餐垫杯垫": frozenset(
        {"餐垫杯垫", "placematscoasters", "placematcoaster", "placemat", "coaster"}
    ),
    "placematscoasters": frozenset(
        {"餐垫杯垫", "placematscoasters", "placematcoaster", "placemat", "coaster"}
    ),
    "墙纸壁纸": frozenset(
        {"墙纸壁纸", "wallpaperswallstickers", "wallpaperwallstickers"}
    ),
}

_WALLPAPER_CATEGORY_ID = "101157"
_WALLPAPER_SEASONAL_ATTRIBUTE_ID = 100818
_WALLPAPER_NON_SEASONAL_VALUE_ID = 4228


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
    command: Mapping[str, Any], official_rows: Sequence[Mapping[str, object]], *,
    selected_category_id: str | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    mandatory = {
        int(row["attribute_id"]): row
        for row in official_rows
        if row.get("is_mandatory") is True
    }
    decision = command.get("category_decision")
    if not mandatory:
        return [], []
    if (
        selected_category_id == _WALLPAPER_CATEGORY_ID
        and isinstance(decision, Mapping)
        and decision.get("status") == "DEFERRED_TO_SKILL"
        and set(mandatory) == {_WALLPAPER_SEASONAL_ATTRIBUTE_ID}
    ):
        candidates = [
            value for value in mandatory[_WALLPAPER_SEASONAL_ATTRIBUTE_ID].get("attribute_value_list", [])
            if isinstance(value, Mapping)
            and value.get("value_id") == _WALLPAPER_NON_SEASONAL_VALUE_ID
            and _semantic_key(value.get("original_value_name")) == "no"
        ]
        if len(candidates) == 1:
            return [{"attribute_id": _WALLPAPER_SEASONAL_ATTRIBUTE_ID,
                     "attribute_value_list": [{"value_id": _WALLPAPER_NON_SEASONAL_VALUE_ID,
                                               "original_value_name": "No"}]}], []
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
    required, missing = _approved_required_attributes(
        command, attributes, selected_category_id=selected["id"]
    )
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
        price_readback_wait: Callable[[float], object] = time.sleep,
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
        if not callable(price_readback_wait):
            raise TypeError("Shopee price readback wait must be callable")
        self._price_readback_wait = price_readback_wait
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

    def _reusable_image_bindings(
        self, image_urls: tuple[str, ...]
    ) -> dict[str, str] | None:
        active = self._active.get()
        command = active.get("command") if isinstance(active, Mapping) else None
        if self._checkpoint_root is None or not isinstance(command, Mapping):
            return None
        offer_id = str(command.get("offer_id") or "").strip()
        revision = command.get("product_revision")
        if (
            not offer_id.isdigit()
            or type(revision) is not int
            or revision <= 0
        ):
            return None
        parent = self._checkpoint_root / offer_id / str(revision)
        if not parent.is_dir():
            return None
        candidates: list[tuple[int, Path]] = []
        for path in parent.glob("*/shopee-global-checkpoint.json"):
            try:
                candidates.append((path.stat().st_mtime_ns, path))
            except OSError:
                continue
        for _modified, path in sorted(candidates, reverse=True):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            bindings = value.get("image_bindings") if isinstance(value, Mapping) else None
            if (
                value.get("schema_version") != "shopee-global-v4-checkpoint/v1"
                or value.get("offer_id") != offer_id
                or value.get("product_revision") != revision
                or not isinstance(bindings, Mapping)
                or set(bindings) != set(image_urls)
            ):
                continue
            normalized = {
                url: str(bindings[url] or "").strip() for url in image_urls
            }
            if (
                all(normalized.values())
                and len(set(normalized.values())) == len(normalized)
            ):
                return normalized
        return None

    def upload_global_images(self, image_urls: Sequence[str]) -> Mapping[str, object]:
        if (
            self._image_upload is None
            or not isinstance(image_urls, tuple)
            or not image_urls
            or len(image_urls) != len(set(image_urls))
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee image upload scope is invalid")
        reusable = self._reusable_image_bindings(image_urls)
        if reusable is not None:
            active = self._active.get()
            if active is None:
                raise ShopeeGlobalV4LiveRuntimeError(
                    "Shopee execution context is unavailable"
                )
            active["image_bindings"] = deepcopy(reusable)
            return reusable
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

    def _checkpointed_image_bindings(self, image_urls: tuple[str, ...]) -> dict[str, str]:
        """Recover only unambiguous prior source-URL/image-ID receipts."""

        active = self._active.get()
        command = active.get("command") if isinstance(active, Mapping) else None
        if self._checkpoint_root is None or not isinstance(command, Mapping):
            return {}
        offer_id = str(command.get("offer_id") or "").strip()
        revision = command.get("product_revision")
        if not offer_id.isdigit() or type(revision) is not int or revision <= 0:
            return {}
        parent = self._checkpoint_root / offer_id / str(revision)
        if not parent.is_dir():
            return {}
        recovered: dict[str, str] = {}
        for path in parent.glob("*/shopee-global-checkpoint.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            bindings = value.get("image_bindings") if isinstance(value, Mapping) else None
            if (
                value.get("schema_version") != "shopee-global-v4-checkpoint/v1"
                or value.get("offer_id") != offer_id
                or value.get("product_revision") != revision
                or not isinstance(bindings, Mapping)
                or not set(bindings).issubset(image_urls)
            ):
                continue
            for url, raw_image_id in bindings.items():
                image_id = str(raw_image_id or "").strip()
                if not image_id:
                    raise ShopeeGlobalV4LiveRuntimeError(
                        "Shopee checkpointed image identity is invalid"
                    )
                previous = recovered.setdefault(str(url), image_id)
                if previous != image_id:
                    raise ShopeeGlobalV4LiveRuntimeError(
                        "Shopee checkpointed image identity conflicts"
                    )
        if len(set(recovered.values())) != len(recovered):
            raise ShopeeGlobalV4LiveRuntimeError(
                "Shopee checkpointed image identities are ambiguous"
            )
        return recovered

    def checkpointed_upload_global_images(
        self, request: object, image_urls: Sequence[str]
    ) -> tuple[Mapping[str, object], int]:
        """Upload only missing frozen images and checkpoint every receipt."""

        if (
            self._image_upload is None
            or not isinstance(image_urls, tuple)
            or not image_urls
            or len(image_urls) != len(set(image_urls))
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee image upload scope is invalid")
        active = self._active.get()
        if active is None:
            raise ShopeeGlobalV4LiveRuntimeError("Shopee execution context is unavailable")
        bindings = self._checkpointed_image_bindings(image_urls)
        active["image_bindings"] = deepcopy(bindings)
        uploaded = 0
        for position, url in enumerate(image_urls):
            if type(url) is not str or not url.startswith("https://"):
                raise ShopeeGlobalV4LiveRuntimeError("Shopee image URL is invalid")
            if url in bindings:
                continue
            image_id = str(self._image_upload(url, position)).strip()
            if not image_id or image_id in bindings.values():
                raise ShopeeGlobalV4LiveRuntimeError(
                    "Shopee uploaded image identity is invalid"
                )
            bindings[url] = image_id
            active["image_bindings"] = deepcopy(bindings)
            self._checkpoint_update(request, {"image_bindings": deepcopy(bindings)})
            uploaded += 1
        return deepcopy(bindings), uploaded

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
        context, command = self._provider_context()
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
            or any(type(value) is not int or value <= 0 for value in package)
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

    def update_global_item(
        self, global_item_id: str, payload: Mapping[str, Any]
    ) -> None:
        """Converge only frozen master copy on an already verified global item."""

        if self._merchant_post is None or not isinstance(payload, Mapping):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee update transport is unavailable")
        context, _command = self._provider_context()
        item_id = str(global_item_id or "").strip()
        title = payload.get("title")
        description = payload.get("description")
        if (
            not item_id.isdigit()
            or int(item_id) <= 0
            or type(title) is not str
            or not title.strip()
            or title != title.strip()
            or len(title) > 255
            or type(description) is not str
            or not description.strip()
            or description != description.strip()
            or len(description) > 5000
            or set(payload) != {"title", "description"}
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global copy update is invalid")
        self._provider_response(
            self._merchant_post(
                "/api/v2/global_product/update_global_item",
                int(context["merchant_id"]),
                str(context["merchant_token"]),
                {
                    "global_item_id": int(item_id),
                    "global_item_name": title,
                    "description": description,
                },
            ),
            "Shopee global copy update",
        )

    def update_existing_global_item(
        self, global_item_id: str, payload: Mapping[str, Any]
    ) -> UpdateReceipt:
        """Attempt one in-place update and reconcile only a lost response."""

        if self._merchant_post is None or not isinstance(payload, Mapping):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee update transport is unavailable")
        context, _command = self._provider_context()
        item_id = str(global_item_id or "").strip()
        title = payload.get("title")
        description = payload.get("description")
        image_ids = payload.get("image_ids")
        parcel = payload.get("parcel")
        package = parcel.get("package_cm") if isinstance(parcel, Mapping) else None
        try:
            weight = float(parcel.get("weight_kg")) if isinstance(parcel, Mapping) else 0
            dimensions = (
                [float(value) for value in package]
                if isinstance(package, list)
                else []
            )
        except (TypeError, ValueError):
            weight, dimensions = 0, []
        if (
            not item_id.isdigit()
            or int(item_id) <= 0
            or type(title) is not str
            or not title.strip()
            or title != title.strip()
            or len(title) > 255
            or type(description) is not str
            or not description.strip()
            or description != description.strip()
            or len(description) > 5000
            or not isinstance(image_ids, list)
            or not image_ids
            or any(
                type(image_id) is not str or not image_id.strip()
                for image_id in image_ids
            )
            or len(image_ids) != len(set(image_ids))
            or not isinstance(package, list)
            or len(package) != 3
            or any(type(value) is not int or value <= 0 for value in package)
            or weight <= 0 or len(dimensions) != 3 or min(dimensions) <= 0
            or set(payload) != {"title", "description", "image_ids", "parcel"}
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global image update is invalid")
        body = {
            "global_item_id": int(item_id),
            "global_item_name": title,
            "description": description,
            "image": {"image_id_list": list(image_ids)},
            "weight": weight,
            "dimension": {
                "package_length": dimensions[0],
                "package_width": dimensions[1],
                "package_height": dimensions[2],
            },
        }
        try:
            provider_value = self._merchant_post(
                "/api/v2/global_product/update_global_item",
                int(context["merchant_id"]),
                str(context["merchant_token"]),
                body,
            )
        except Exception as transport_error:
            try:
                observed = self.read_global_item(item_id)
            except Exception:
                raise transport_error
            parcel_observed = (
                observed.get("parcel") if isinstance(observed, Mapping) else None
            )
            package_observed = (
                parcel_observed.get("package_cm")
                if isinstance(parcel_observed, Mapping)
                else None
            )
            try:
                exact = (
                    observed.get("title") == title
                    and observed.get("description") == description
                    and observed.get("image_ids") == list(image_ids)
                    and Decimal(str(parcel_observed.get("weight_kg")))
                    == Decimal(str(weight))
                    and [Decimal(str(value)) for value in package_observed]
                    == [Decimal(str(value)) for value in dimensions]
                )
            except (AttributeError, InvalidOperation, TypeError, ValueError):
                exact = False
            if exact:
                return UpdateReceipt(attempted_count=1, reconciled_by_readback=True)
            raise transport_error
        self._provider_response(provider_value, "Shopee global image update")
        return UpdateReceipt(attempted_count=1, reconciled_by_readback=False)

    def persist_existing_global_update_receipt(
        self, request: object, receipt: UpdateReceipt
    ) -> None:
        if type(receipt) is not UpdateReceipt:
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global update receipt is invalid")
        self._checkpoint_update(
            request,
            {
                "existing_global_item_update": {
                    "attempted_count": receipt.attempted_count,
                    "reconciled_by_readback": receipt.reconciled_by_readback,
                }
            },
        )

    def update_existing_global_tier_variation(
        self, global_item_id: str, payload: Mapping[str, Any]
    ) -> UpdateReceipt:
        """Attempt one frozen tier update and reconcile only a lost response."""

        if self._merchant_post is None or not isinstance(payload, Mapping):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee tier update transport is unavailable")
        context, _command = self._provider_context()
        item_id = str(global_item_id or "").strip()
        names = payload.get("variation_names")
        models = payload.get("models")
        if (
            not item_id.isdigit()
            or int(item_id) <= 0
            or not isinstance(names, list)
            or not 1 <= len(names) <= 2
            or any(
                type(name) is not str or not name.strip() or name != name.strip()
                for name in names
            )
            or len(names) != len(set(names))
            or not isinstance(models, list)
            or not models
            or set(payload) != {"variation_names", "models"}
        ):
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global tier update is invalid")
        options: list[list[str]] = [[] for _ in names]
        image_by_first_option: dict[str, str] = {}
        seen_model_skus: set[str] = set()
        seen_option_values: set[tuple[str, ...]] = set()
        for model in models:
            values = model.get("option_values") if isinstance(model, Mapping) else None
            model_sku = (
                str(model.get("model_sku") or "").strip()
                if isinstance(model, Mapping)
                else ""
            )
            image_id = (
                str(model.get("variant_image_id") or "").strip()
                if isinstance(model, Mapping)
                else ""
            )
            if (
                not isinstance(values, list)
                or len(values) != len(names)
                or any(
                    type(value) is not str
                    or not value.strip()
                    or value != value.strip()
                    for value in values
                )
                or not model_sku
                or model_sku in seen_model_skus
                or tuple(values) in seen_option_values
                or not image_id
                or set(model) != {"model_sku", "option_values", "variant_image_id"}
            ):
                raise ShopeeGlobalV4LiveRuntimeError("Shopee global tier update is invalid")
            seen_model_skus.add(model_sku)
            seen_option_values.add(tuple(values))
            previous = image_by_first_option.setdefault(values[0], image_id)
            if previous != image_id:
                raise ShopeeGlobalV4LiveRuntimeError(
                    "Shopee first variation image identity conflicts"
                )
            for index, value in enumerate(values):
                if value not in options[index]:
                    options[index].append(value)
        tiers = []
        for index, name in enumerate(names):
            option_list = []
            for value in options[index]:
                row: dict[str, object] = {"option": value}
                if index == 0:
                    row["image"] = {"image_id": image_by_first_option[value]}
                option_list.append(row)
            tiers.append({"name": name, "option_list": option_list})
        body = {"global_item_id": int(item_id), "tier_variation": tiers}
        try:
            provider_value = self._merchant_post(
                "/api/v2/global_product/update_tier_variation",
                int(context["merchant_id"]),
                str(context["merchant_token"]),
                body,
            )
        except Exception as transport_error:
            try:
                observed = self.read_global_models(item_id)
            except Exception:
                raise transport_error
            observed_rows = observed.get("models") if isinstance(observed, Mapping) else None
            by_sku: dict[str, Mapping[str, Any]] = {}
            if isinstance(observed_rows, list):
                for row in observed_rows:
                    sku = str(row.get("model_sku") or "").strip() if isinstance(row, Mapping) else ""
                    if not sku or sku in by_sku:
                        by_sku = {}
                        break
                    by_sku[sku] = row
            expected = {str(row["model_sku"]): row for row in models}
            exact = (
                isinstance(observed, Mapping)
                and observed.get("variation_names") == list(names)
                and set(by_sku) == set(expected)
                and len(by_sku) == len(models)
            )
            if exact:
                for model_sku, frozen in expected.items():
                    row = by_sku[model_sku]
                    global_model_id = str(row.get("global_model_id") or "").strip()
                    if (
                        not global_model_id.isdigit()
                        or int(global_model_id) <= 0
                        or row.get("option_values") != frozen["option_values"]
                        or str(row.get("variant_image_id") or "").strip()
                        != frozen["variant_image_id"]
                    ):
                        exact = False
                        break
            if exact:
                return UpdateReceipt(attempted_count=1, reconciled_by_readback=True)
            raise transport_error
        self._provider_response(provider_value, "Shopee global tier image update")
        return UpdateReceipt(attempted_count=1, reconciled_by_readback=False)

    def persist_existing_global_tier_update_receipt(
        self, request: object, receipt: UpdateReceipt
    ) -> None:
        if type(receipt) is not UpdateReceipt:
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global tier receipt is invalid")
        self._checkpoint_update(
            request,
            {
                "existing_global_tier_update": {
                    "attempted_count": receipt.attempted_count,
                    "reconciled_by_readback": receipt.reconciled_by_readback,
                }
            },
        )

    def update_existing_global_models(
        self, global_item_id: str, payload: Mapping[str, Any]
    ) -> UpdateReceipt:
        """Bind fresh official Model IDs and attempt one frozen price update."""

        if (
            self._merchant_post is None
            or self._merchant_get is None
            or not isinstance(payload, Mapping)
        ):
            raise _ShopeeGlobalV4PreSubmitError(
                "Shopee global model update transport is unavailable"
            )
        try:
            context, _command = self._provider_context()
        except Exception as error:
            raise _ShopeeGlobalV4PreSubmitError(
                "Shopee global model update context is unavailable"
            ) from error
        item_id = str(global_item_id or "").strip()
        models = payload.get("models")
        if (
            not item_id.isdigit()
            or int(item_id) <= 0
            or not isinstance(models, list)
            or not models
            or set(payload) != {"models"}
        ):
            raise _ShopeeGlobalV4PreSubmitError(
                "Shopee global price update payload is invalid"
            )

        frozen_by_sku: dict[str, dict[str, object]] = {}
        for row in models:
            model_sku = (
                str(row.get("model_sku") or "").strip()
                if isinstance(row, Mapping)
                else ""
            )
            option_values = row.get("option_values") if isinstance(row, Mapping) else None
            try:
                price = Decimal(str(row.get("price_cny")))
            except (AttributeError, InvalidOperation, TypeError, ValueError):
                price = Decimal(0)
            if (
                not isinstance(row, Mapping)
                or set(row) != {"model_sku", "option_values", "price_cny"}
                or not model_sku
                or model_sku in frozen_by_sku
                or not isinstance(option_values, list)
                or not option_values
                or any(type(value) is not str or not value for value in option_values)
                or not price.is_finite()
                or price <= 0
            ):
                raise _ShopeeGlobalV4PreSubmitError(
                    "Shopee frozen global model identity is invalid"
                )
            frozen_by_sku[model_sku] = {
                "option_values": list(option_values),
                "price": price,
            }

        try:
            fresh = self.read_global_models(item_id)
        except Exception as error:
            raise _ShopeeGlobalV4PreSubmitError(
                "Shopee official global model binding is unavailable"
            ) from error
        fresh_rows = fresh.get("models") if isinstance(fresh, Mapping) else None
        official_by_sku: dict[str, Mapping[str, Any]] = {}
        official_ids: set[str] = set()
        if isinstance(fresh_rows, list):
            for row in fresh_rows:
                model_sku = (
                    str(row.get("model_sku") or "").strip()
                    if isinstance(row, Mapping)
                    else ""
                )
                model_id = (
                    str(row.get("global_model_id") or "").strip()
                    if isinstance(row, Mapping)
                    else ""
                )
                if (
                    not model_sku
                    or model_sku in official_by_sku
                    or not model_id.isdigit()
                    or int(model_id) <= 0
                    or model_id in official_ids
                ):
                    raise _ShopeeGlobalV4PreSubmitError(
                        "Shopee official global model identity is ambiguous"
                    )
                official_by_sku[model_sku] = row
                official_ids.add(model_id)
        if set(official_by_sku) != set(frozen_by_sku) or len(fresh_rows or []) != len(
            frozen_by_sku
        ):
            raise _ShopeeGlobalV4PreSubmitError(
                "Shopee official global model coverage is incomplete"
            )

        price_rows = []
        submitted_id_by_sku: dict[str, str] = {}
        for model_sku in frozen_by_sku:
            frozen = frozen_by_sku[model_sku]
            official = official_by_sku[model_sku]
            if official.get("option_values") != frozen["option_values"]:
                raise _ShopeeGlobalV4PreSubmitError(
                    "Shopee official global model binding is incomplete"
                )
            submitted_id_by_sku[model_sku] = str(official["global_model_id"])
            price_rows.append(
                {
                    "global_model_id": int(official["global_model_id"]),
                    "original_price": float(frozen["price"]),
                }
            )

        def readback_exact() -> tuple[bool, bool]:
            try:
                observed = self.read_global_models(item_id)
            except Exception:
                return False, False
            observed_rows = (
                observed.get("models") if isinstance(observed, Mapping) else None
            )
            if not isinstance(observed_rows, list):
                return True, False
            observed_by_sku: dict[str, Mapping[str, Any]] = {}
            observed_ids: set[str] = set()
            for row in observed_rows:
                model_sku = (
                    str(row.get("model_sku") or "").strip()
                    if isinstance(row, Mapping)
                    else ""
                )
                model_id = (
                    str(row.get("global_model_id") or "").strip()
                    if isinstance(row, Mapping)
                    else ""
                )
                if (
                    not model_sku
                    or model_sku in observed_by_sku
                    or not model_id.isdigit()
                    or int(model_id) <= 0
                    or model_id in observed_ids
                ):
                    return True, False
                observed_by_sku[model_sku] = row
                observed_ids.add(model_id)
            if (
                len(observed_rows) != len(frozen_by_sku)
                or set(observed_by_sku) != set(frozen_by_sku)
            ):
                return True, False
            for model_sku, frozen in frozen_by_sku.items():
                row = observed_by_sku[model_sku]
                try:
                    price_exact = Decimal(str(row.get("price_cny"))) == frozen["price"]
                except (InvalidOperation, TypeError, ValueError):
                    price_exact = False
                if (
                    str(row.get("global_model_id") or "").strip()
                    != submitted_id_by_sku[model_sku]
                    or row.get("option_values") != frozen["option_values"]
                    or not price_exact
                ):
                    return True, False
            return True, True

        body = {"global_item_id": int(item_id), "price_list": price_rows}
        failure_kind = ""
        provider_code = ""
        http_status: int | None = None
        request_id_digest = ""
        accepted = False
        try:
            provider_value = self._merchant_post(
                "/api/v2/global_product/update_price",
                int(context["merchant_id"]),
                str(context["merchant_token"]),
                body,
            )
        except Exception as transport_error:
            failure_kind = "TRANSPORT_UNKNOWN"
            http_status = _safe_http_status(transport_error)
        else:
            if not isinstance(provider_value, Mapping):
                failure_kind = "MALFORMED_RESPONSE"
            else:
                raw_error = str(provider_value.get("error") or "").strip()
                request_id_digest = _request_id_digest(
                    provider_value.get("request_id")
                )
                if raw_error and raw_error != "-":
                    failure_kind = "BUSINESS_REJECTED"
                    provider_code = _safe_provider_code(raw_error)
                else:
                    accepted = True

        if accepted:
            for attempt in range(_PRICE_READBACK_ATTEMPTS):
                if attempt:
                    self._price_readback_wait(_PRICE_READBACK_DELAY_SECONDS)
                _readback_available, exact = readback_exact()
                if exact:
                    return UpdateReceipt(
                        attempted_count=1, reconciled_by_readback=False
                    )
            failure_kind = "ACCEPTED_UNVERIFIED"
            readback_available = True
        else:
            readback_available, exact = readback_exact()
            if exact:
                return UpdateReceipt(attempted_count=1, reconciled_by_readback=True)
        raise ShopeeGlobalModelUpdateFailure(
            kind=failure_kind,
            provider_code=provider_code,
            http_status=http_status,
            request_id_digest=request_id_digest,
            outcome_unknown=(
                failure_kind != "BUSINESS_REJECTED" or not readback_available
            ),
        )

    def persist_existing_global_model_update_receipt(
        self, request: object, receipt: UpdateReceipt
    ) -> None:
        if type(receipt) is not UpdateReceipt:
            raise ShopeeGlobalV4LiveRuntimeError("Shopee global model receipt is invalid")
        self._checkpoint_update(
            request,
            {
                "existing_global_model_update": {
                    "attempted_count": receipt.attempted_count,
                    "reconciled_by_readback": receipt.reconciled_by_readback,
                }
            },
        )

    def persist_existing_global_model_update_failure(
        self, request: object, failure: Mapping[str, object]
    ) -> None:
        expected = {
            "stage",
            "kind",
            "provider_code",
            "http_status",
            "request_id_digest",
            "provider_write_attempted",
            "outcome_unknown",
        }
        if not isinstance(failure, Mapping) or set(failure) != expected:
            raise ShopeeGlobalV4LiveRuntimeError(
                "Shopee global model failure evidence is invalid"
            )
        kind = failure.get("kind")
        provider_code = failure.get("provider_code")
        http_status = failure.get("http_status")
        request_digest = failure.get("request_id_digest")
        outcome_unknown = failure.get("outcome_unknown")
        if (
            failure.get("stage") != "update_price"
            or kind not in ShopeeGlobalModelUpdateFailure._KINDS
            or type(provider_code) is not str
            or provider_code != _safe_provider_code(provider_code)
            or (
                http_status is not None
                and (type(http_status) is not int or not 100 <= http_status <= 599)
            )
            or type(request_digest) is not str
            or (
                request_digest
                and not re.fullmatch(r"sha256:[0-9a-f]{64}", request_digest)
            )
            or failure.get("provider_write_attempted") is not True
            or type(outcome_unknown) is not bool
            or (
                kind
                in {
                    "ACCEPTED_UNVERIFIED",
                    "MALFORMED_RESPONSE",
                    "TRANSPORT_UNKNOWN",
                }
                and outcome_unknown is not True
            )
        ):
            raise ShopeeGlobalV4LiveRuntimeError(
                "Shopee global model failure evidence is invalid"
            )
        self._checkpoint_update(
            request,
            {"existing_global_model_update_failure": deepcopy(dict(failure))},
        )

    def initialize_global_models(
        self, global_item_id: str, payload: Mapping[str, Any]
    ) -> object:
        if self._merchant_post is None or self._merchant_get is None:
            raise ShopeeGlobalV4LiveRuntimeError("Shopee model transport is unavailable")
        context, command = self._provider_context()
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
        provider_error: Exception | None = None
        try:
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
        except Exception as error:
            # Shopee can commit the model change and still return a provider
            # error (observed for an already-applied one-level variation).
            # The exact official identity readback is authoritative; never
            # retry this write merely because its immediate response disagrees.
            provider_error = error
        try:
            raw = self._read_global_models_raw(global_item_id)
        except Exception:
            if provider_error is not None:
                raise provider_error
            raise
        identities = {
            str(row.get("global_model_sku") or "").strip(): str(
                row.get("global_model_id") or ""
            ).strip()
            for row in raw["models"]
        }
        if set(identities) != {str(row["model_sku"]) for row in models} or any(
            not value.isdigit() or int(value) <= 0 for value in identities.values()
        ):
            if provider_error is not None:
                raise provider_error
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
        context, command = self._provider_context()
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
        approved_image_urls = list(command["product"]["images"])
        for model in command["models"]:
            variant_image_url = model["variant_image_url"]
            if variant_image_url not in approved_image_urls:
                approved_image_urls.append(variant_image_url)
        approved_bindings = active.get("image_bindings") if active is not None else None
        if not isinstance(approved_bindings, Mapping) or set(approved_bindings) != set(
            approved_image_urls
        ):
            approved_bindings = self._reusable_image_bindings(tuple(approved_image_urls))
        if active is not None and approved_bindings is not None:
            active["image_bindings"] = deepcopy(dict(approved_bindings))
        return {
            "global_item_id": str(row.get("global_item_id") or ""),
            "status": str(row.get("global_item_status") or ""),
            "title": row.get("global_item_name"),
            "description": row.get("description") or row.get("global_item_description"),
            "category_id": str(row.get("category_id") or ""),
            "attribute_list": deepcopy(row.get("attribute_list")),
            "image_urls": image_urls,
            "image_ids": image_ids,
            "approved_image_bindings": deepcopy(dict(approved_bindings or {})),
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
                    "global_model_id": str(row.get("global_model_id") or "").strip(),
                    "model_sku": str(row.get("global_model_sku") or "").strip(),
                    "option_values": option_values,
                    "price_cny": price_value,
                    "variant_image_url": variant_image_url,
                    "variant_image_id": variant_image_id,
                    "status": str(row.get("global_model_status") or row.get("status") or "").upper(),
                    "seller_stock": (
                        _normalized_seller_stock(row.get("seller_stock"))
                        if row.get("seller_stock") is not None
                        else _normalized_stock_info(row.get("stock_info"))
                    ),
                }
            )
        return {
            "variation_names": [str(row.get("name") or "") for row in tiers],
            "models": normalized,
        }


__all__ = [
    "OfficialShopeeGlobalV4Runtime",
    "ShopeeGlobalModelUpdateFailure",
    "ShopeeGlobalV4LiveRuntimeError",
    "build_official_shopee_global_v4_runtime",
    "select_exact_official_category",
]
