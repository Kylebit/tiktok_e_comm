"""Independent Shopee global-to-regional execution boundary for the Skill.

The module intentionally separates the provider write from official readback:

* dispatch never mutates ``global_sku_map``;
* every selected region is attempted independently;
* an unselected region is never touched;
* only exact official item/model/linkage readback records a shop item and
  therefore expands ``published_regions``.

The orchestration functions accept an injectable runtime so their state
semantics can be tested without credentials, network calls, or platform
writes.  ``OfficialShopeeRegionRuntime`` is the thin live adapter used by the
Skill CLI when the operator explicitly authorizes execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import time
from typing import Any, Mapping, Protocol


REGIONAL_CURRENCIES = {
    "PH": "PHP",
    "MY": "MYR",
    "TH": "THB",
    "VN": "VND",
}
REGIONAL_TARGETS = tuple(f"shopee:{region}" for region in REGIONAL_CURRENCIES)
CREATE_TASK_PATH = "/api/v2/global_product/create_publish_task"
TASK_RESULT_PATH = "/api/v2/global_product/get_publish_task_result"


class ShopeeRegionContractError(ValueError):
    """The frozen snapshot cannot produce an exact regional command."""


@dataclass(frozen=True)
class RegionContext:
    region: str
    shop_id: int
    merchant_id: int
    shop_token: str
    merchant_token: str


class ShopeeRegionRuntime(Protocol):
    def context(self, region: str) -> RegionContext: ...

    def global_models(
        self, context: RegionContext, global_item_id: str
    ) -> list[Mapping[str, object]]: ...

    def compatible_logistics(
        self,
        context: RegionContext,
        *,
        weight_kg: float,
        dimensions_cm: tuple[float, float, float],
    ) -> list[int]: ...

    def create_publish_task(
        self, context: RegionContext, body: Mapping[str, object]
    ) -> object: ...

    def publish_task_result(
        self, context: RegionContext, task_id: str
    ) -> object: ...

    def regional_item(
        self, context: RegionContext, item_id: str
    ) -> Mapping[str, object] | None: ...

    def regional_models(
        self, context: RegionContext, item_id: str
    ) -> list[Mapping[str, object]]: ...

    def resolved_global_item_id(
        self, context: RegionContext, item_id: str
    ) -> str: ...

    def record_verified_item(
        self,
        *,
        global_item_id: str,
        region: str,
        shop_id: int,
        item_id: str,
        model_id: str,
    ) -> None: ...


def selected_region_targets(snapshot: Mapping[str, object]) -> list[str]:
    """Return only explicitly approved Shopee regional targets.

    ``shopee:GLOBAL`` is deliberately excluded.  Global-only approval must
    produce an empty list and cannot be interpreted as four regional shops.
    """

    candidates: list[object] = []
    publication_targets = snapshot.get("publication_targets")
    if isinstance(publication_targets, list):
        for row in publication_targets:
            if isinstance(row, Mapping):
                candidates.append(row.get("target_label"))
    platforms = snapshot.get("platforms")
    shopee = platforms.get("shopee") if isinstance(platforms, Mapping) else None
    if isinstance(shopee, Mapping):
        rows = shopee.get("targets")
        if isinstance(rows, list):
            candidates.extend(rows)

    selected: list[str] = []
    for candidate in candidates:
        target = str(candidate or "").strip()
        if target not in REGIONAL_TARGETS or target in selected:
            continue
        selected.append(target)
    return selected


def dispatch_selected_regions(
    snapshot: Mapping[str, object],
    *,
    global_item_id: str,
    runtime: ShopeeRegionRuntime,
) -> dict[str, object]:
    """Submit one independent official publish task per selected region."""

    global_id = _positive_identity(global_item_id, "global item identity")
    targets = selected_region_targets(snapshot)
    results: list[dict[str, object]] = []
    for target in targets:
        region = target.split(":", 1)[1]
        try:
            approved_models = _approved_models(snapshot, target)
            parcel = _parcel_envelope(snapshot)
            context = runtime.context(region)
            official_models = runtime.global_models(context, global_id)
            tiers = _exact_global_tiers(official_models, approved_models)
            logistics = sorted(
                {
                    int(value)
                    for value in runtime.compatible_logistics(
                        context,
                        weight_kg=parcel[0],
                        dimensions_cm=parcel[1],
                    )
                    if type(value) is int and value > 0
                }
            )
            if not logistics:
                raise ShopeeRegionContractError(
                    "no official logistics channel supports the approved parcel"
                )
            body = _publish_body(
                region=region,
                shop_id=context.shop_id,
                global_item_id=global_id,
                approved_models=approved_models,
                tiers=tiers,
                logistics=logistics,
            )
        except Exception as error:
            results.append(
                _target_fact(
                    target,
                    attempted=False,
                    accepted=False,
                    outcome="NOT_ATTEMPTED",
                    message=str(error),
                )
            )
            continue

        try:
            response = runtime.create_publish_task(context, body)
        except Exception as error:
            # An exception raised by the write transport cannot prove whether
            # the provider received the request.
            results.append(
                _target_fact(
                    target,
                    attempted=True,
                    accepted=False,
                    outcome="UNKNOWN",
                    message=type(error).__name__,
                )
            )
            continue
        error_code, error_message = _provider_error(response)
        if error_code:
            results.append(
                _target_fact(
                    target,
                    attempted=True,
                    accepted=False,
                    outcome="REJECTED",
                    provider_code=error_code,
                    message=error_message,
                )
            )
            continue
        task_id = _publish_task_id(response)
        if not task_id:
            results.append(
                _target_fact(
                    target,
                    attempted=True,
                    accepted=False,
                    outcome="UNKNOWN",
                    message="official response did not contain publish_task_id",
                )
            )
            continue
        results.append(
            {
                **_target_fact(
                    target,
                    attempted=True,
                    accepted=True,
                    outcome="ACCEPTED",
                ),
                "provider_task_id": task_id,
                "shop_id": context.shop_id,
                "command_digest": _digest(body),
                "expected_model_count": len(approved_models),
                "selected_logistics_ids": logistics,
                "price_lineage_digest": _digest({"models": approved_models}),
            }
        )
    return _dispatch_envelope(global_id, targets, results)


def readback_dispatched_regions(
    snapshot: Mapping[str, object],
    dispatch: Mapping[str, object],
    *,
    global_item_id: str,
    runtime: ShopeeRegionRuntime,
    poll_attempts: int = 3,
) -> dict[str, object]:
    """Verify accepted tasks from official shop facts, target by target."""

    global_id = _positive_identity(global_item_id, "global item identity")
    attempts = max(1, min(int(poll_attempts), 10))
    dispatch_rows = {
        str(row.get("target_label") or ""): row
        for row in dispatch.get("targets") or []
        if isinstance(row, Mapping)
    }
    results: list[dict[str, object]] = []
    for target in selected_region_targets(snapshot):
        source = dispatch_rows.get(target)
        if not isinstance(source, Mapping) or source.get("accepted") is not True:
            results.append(
                _target_fact(
                    target,
                    attempted=False,
                    accepted=False,
                    outcome="NOT_DISPATCHED",
                    message="no accepted official regional task exists",
                )
            )
            continue
        task_id = str(source.get("provider_task_id") or "").strip()
        region = target.split(":", 1)[1]
        try:
            if not task_id:
                raise ShopeeRegionContractError("publish task identity is missing")
            approved_models = _approved_models(snapshot, target)
            context = runtime.context(region)
            expected_tiers = _exact_global_tiers(
                runtime.global_models(context, global_id), approved_models
            )
            task = _poll_task(runtime, context, task_id, attempts)
            status = str(task.get("publish_status") or "").strip().lower()
            if status not in {"success", "failed"}:
                results.append(
                    {
                        **_target_fact(
                            target,
                            attempted=True,
                            accepted=True,
                            outcome="PROCESSING",
                            message="official publish task is still processing",
                        ),
                        "provider_task_id": task_id,
                    }
                )
                continue
            if status == "failed":
                results.append(
                    {
                        **_target_fact(
                            target,
                            attempted=True,
                            accepted=True,
                            outcome="FAILED",
                            message="official publish task failed",
                        ),
                        "provider_task_id": task_id,
                    }
                )
                continue
            item_id = _task_item_id(task)
            if not item_id:
                raise ShopeeRegionContractError(
                    "successful publish task did not identify a shop item"
                )
            item = runtime.regional_item(context, item_id)
            models = runtime.regional_models(context, item_id)
            linkage = runtime.resolved_global_item_id(context, item_id)
            checks = _official_readback_checks(
                item=item,
                models=models,
                resolved_global_item_id=linkage,
                expected_global_item_id=global_id,
                expected_models=approved_models,
                expected_tiers=expected_tiers,
                expected_logistics=_positive_int_list(
                    source.get("selected_logistics_ids"),
                    "selected logistics",
                ),
                expected_image_count=_approved_image_count(snapshot),
                expected_item_sku=_expected_item_sku(snapshot),
                expected_category_id=_expected_category_id(snapshot, target),
                item_id=item_id,
            )
            verified = all(checks.values())
            if verified:
                model_ids = sorted(
                    str(row.get("model_id") or "")
                    for row in models
                    if isinstance(row, Mapping)
                    and str(row.get("model_id") or "").strip()
                )
                runtime.record_verified_item(
                    global_item_id=global_id,
                    region=region,
                    shop_id=context.shop_id,
                    item_id=item_id,
                    model_id=model_ids[0] if model_ids else "",
                )
            results.append(
                {
                    **_target_fact(
                        target,
                        attempted=True,
                        accepted=True,
                        outcome="PUBLISHED" if verified else "MISMATCH",
                        message=(
                            "official regional item verified"
                            if verified
                            else "official regional item does not match approved facts"
                        ),
                    ),
                    "provider_task_id": task_id,
                    "item_id": item_id,
                    "checks": checks,
                    "verified": verified,
                }
            )
        except Exception as error:
            results.append(
                {
                    **_target_fact(
                        target,
                        attempted=True,
                        accepted=True,
                        outcome="UNKNOWN",
                        message=str(error),
                    ),
                    "provider_task_id": task_id,
                }
            )
    verified_count = sum(row.get("verified") is True for row in results)
    return {
        "schema_version": "shopee-regional-readback/v1",
        "platform": "shopee",
        "global_item_id": global_id,
        "target_count": len(results),
        "verified_target_count": verified_count,
        "complete": bool(results) and verified_count == len(results),
        "targets": results,
    }


class OfficialShopeeRegionRuntime:
    """Thin adapter over the repository's audited official Shopee clients."""

    def context(self, region: str) -> RegionContext:
        from modules.shopee.auth import ensure_shop_token
        from modules.shopee.publish import _merchant_token, _shop_meta
        from modules.shopee.shops import sync_shop_ids

        clean = _region(region)
        shop_id = int(sync_shop_ids().get(clean) or 0)
        if shop_id <= 0:
            raise RuntimeError(f"official Shopee shop mapping is unavailable for {clean}")
        shop_token = ensure_shop_token(shop_id)
        merchant_id = int(_shop_meta(shop_id, shop_token).get("merchant_id") or 0)
        if merchant_id <= 0:
            raise RuntimeError("official Shopee merchant identity is unavailable")
        return RegionContext(
            region=clean,
            shop_id=shop_id,
            merchant_id=merchant_id,
            shop_token=shop_token,
            merchant_token=_merchant_token(shop_id, shop_token),
        )

    def global_models(
        self, context: RegionContext, global_item_id: str
    ) -> list[Mapping[str, object]]:
        from modules.shopee.client import merchant_get

        response = merchant_get(
            "/api/v2/global_product/get_global_model_list",
            context.merchant_id,
            context.merchant_token,
            {"global_item_id": int(global_item_id)},
        )
        _raise_provider_error(response, "official global model read")
        rows = (response.get("response") or {}).get("global_model") or []
        if not isinstance(rows, list):
            raise RuntimeError("official global model response is malformed")
        return [row for row in rows if isinstance(row, Mapping)]

    def compatible_logistics(
        self,
        context: RegionContext,
        *,
        weight_kg: float,
        dimensions_cm: tuple[float, float, float],
    ) -> list[int]:
        from modules.shopee.publish import _logistic_info

        rows = _logistic_info(
            context.shop_id,
            context.shop_token,
            None,
            region=context.region,
            weight_kg=weight_kg,
            dimensions_cm=dimensions_cm,
        )
        return [
            int(row["logistic_id"])
            for row in rows
            if isinstance(row, Mapping)
            and type(row.get("logistic_id")) is int
            and row["logistic_id"] > 0
        ]

    def create_publish_task(
        self, context: RegionContext, body: Mapping[str, object]
    ) -> object:
        from modules.shopee.client import merchant_post

        return merchant_post(
            CREATE_TASK_PATH,
            context.merchant_id,
            context.merchant_token,
            dict(body),
        )

    def publish_task_result(
        self, context: RegionContext, task_id: str
    ) -> object:
        from modules.shopee.client import merchant_get

        return merchant_get(
            TASK_RESULT_PATH,
            context.merchant_id,
            context.merchant_token,
            {"publish_task_id": int(task_id)},
        )

    def regional_item(
        self, context: RegionContext, item_id: str
    ) -> Mapping[str, object] | None:
        from modules.shopee.client import shop_get

        response = shop_get(
            "/api/v2/product/get_item_base_info",
            context.shop_id,
            context.shop_token,
            {"item_id_list": item_id},
        )
        _raise_provider_error(response, "official regional item read")
        rows = (response.get("response") or {}).get("item_list") or []
        matches = [
            row
            for row in rows
            if isinstance(row, Mapping)
            and str(row.get("item_id") or "") == item_id
        ]
        return matches[0] if len(matches) == 1 else None

    def regional_models(
        self, context: RegionContext, item_id: str
    ) -> list[Mapping[str, object]]:
        from modules.shopee.client import shop_get

        response = shop_get(
            "/api/v2/product/get_model_list",
            context.shop_id,
            context.shop_token,
            {"item_id": int(item_id)},
        )
        _raise_provider_error(response, "official regional model read")
        rows = (response.get("response") or {}).get("model") or []
        if not isinstance(rows, list):
            raise RuntimeError("official regional model response is malformed")
        return [row for row in rows if isinstance(row, Mapping)]

    def resolved_global_item_id(
        self, context: RegionContext, item_id: str
    ) -> str:
        from modules.shopee.client import resolve_global_item_id

        return str(
            resolve_global_item_id(
                context.shop_id,
                context.merchant_id,
                context.merchant_token,
                item_id,
            )
            or ""
        )

    def record_verified_item(
        self,
        *,
        global_item_id: str,
        region: str,
        shop_id: int,
        item_id: str,
        model_id: str,
    ) -> None:
        from modules.shopee.global_sku_map import record_shop_item

        record_shop_item(
            global_item_id,
            region,
            shop_id=shop_id,
            item_id=item_id,
            model_id=model_id,
        )


def _approved_models(
    snapshot: Mapping[str, object], target: str
) -> list[dict[str, str]]:
    rows = snapshot.get("skus")
    if not isinstance(rows, list) or not rows:
        raise ShopeeRegionContractError("approved SKU rows are unavailable")
    expected_currency = REGIONAL_CURRENCIES[target.split(":", 1)[1]]
    models: list[dict[str, str]] = []
    seen: set[str] = set()
    top_prices = snapshot.get("prices")
    target_prices = (
        top_prices.get(target) if isinstance(top_prices, Mapping) else None
    )
    sku_prices = (
        target_prices.get("sku_prices")
        if isinstance(target_prices, Mapping)
        else None
    )
    for row in rows:
        if not isinstance(row, Mapping):
            raise ShopeeRegionContractError("approved SKU row is malformed")
        model_sku = str(
            row.get("model_sku") or row.get("seller_sku") or ""
        ).strip()
        if not model_sku or model_sku in seen:
            raise ShopeeRegionContractError(
                "approved regional model SKU identity is incomplete"
            )
        seen.add(model_sku)
        amount: object = None
        currency: object = None
        global_price_cny: object = None
        prices = row.get("prices")
        price = prices.get(target) if isinstance(prices, Mapping) else None
        if isinstance(price, Mapping):
            amount = price.get("amount", price.get("local_original_price"))
            currency = price.get("currency")
            global_price_cny = price.get("global_original_price_cny")
        if amount is None and isinstance(sku_prices, Mapping):
            facts = sku_prices.get(model_sku)
            if not isinstance(facts, Mapping):
                facts = sku_prices.get(str(row.get("seller_sku") or ""))
            if isinstance(facts, Mapping):
                amount = facts.get("local_original_price")
                global_price_cny = facts.get(
                    "global_original_price_cny", facts.get("list_price")
                )
                currency = facts.get("currency") or (
                    target_prices.get("currency")
                    if isinstance(target_prices, Mapping)
                    else None
                )
        if (
            amount is None
            and len(rows) == 1
            and isinstance(target_prices, Mapping)
        ):
            amount = target_prices.get("local_original_price")
            currency = target_prices.get("currency")
            global_price_cny = target_prices.get("global_original_price_cny")
        if amount is None:
            raise ShopeeRegionContractError(
                f"{target} requires exact {expected_currency} model prices"
            )
        if global_price_cny is None:
            raise ShopeeRegionContractError(
                f"{target} requires exact per-SKU CNSC CNY price lineage"
            )
        clean_currency = str(currency or "").strip().upper()
        if clean_currency != expected_currency:
            raise ShopeeRegionContractError(
                f"{target} requires exact {expected_currency} model prices"
            )
        models.append(
            {
                "model_sku": model_sku,
                "price": str(_positive_decimal(amount, "regional model price")),
                "currency": clean_currency,
                "global_price_cny": str(
                    _positive_decimal(
                        global_price_cny, "CNSC global model price lineage"
                    )
                ),
            }
        )
    return models


def _parcel_envelope(
    snapshot: Mapping[str, object],
) -> tuple[float, tuple[float, float, float]]:
    rows = snapshot.get("skus")
    if not isinstance(rows, list) or not rows:
        raise ShopeeRegionContractError("approved SKU parcel facts are unavailable")
    weights: list[float] = []
    dimensions: list[tuple[float, float, float]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ShopeeRegionContractError("approved SKU parcel row is malformed")
        parcel = row.get("parcel")
        if isinstance(parcel, Mapping):
            weight = parcel.get("weight_kg")
            dims = parcel.get("package_cm")
            if isinstance(dims, Mapping):
                dims = [dims.get("length"), dims.get("width"), dims.get("height")]
        else:
            weight = row.get("weight_kg")
            dims = row.get("package_cm")
        if not isinstance(dims, (list, tuple)) or len(dims) != 3:
            raise ShopeeRegionContractError("approved SKU dimensions are incomplete")
        clean_weight = float(_positive_decimal(weight, "SKU weight"))
        clean_dims = tuple(
            float(_positive_decimal(value, "SKU dimension")) for value in dims
        )
        weights.append(clean_weight)
        dimensions.append(clean_dims)
    return (
        max(weights),
        tuple(max(row[index] for row in dimensions) for index in range(3)),
    )


def _approved_image_count(snapshot: Mapping[str, object]) -> int:
    product = snapshot.get("product")
    if isinstance(product, Mapping) and isinstance(product.get("images"), list):
        return len(product["images"])
    content = snapshot.get("content")
    if isinstance(content, Mapping) and isinstance(content.get("images"), list):
        return len(content["images"])
    return 0


def _expected_item_sku(snapshot: Mapping[str, object]) -> str:
    rows = snapshot.get("skus")
    values = {
        str(row.get("seller_sku") or "").strip()
        for row in rows or []
        if isinstance(row, Mapping) and str(row.get("seller_sku") or "").strip()
    }
    if len(values) != 1:
        raise ShopeeRegionContractError(
            "approved regional item SKU identity is incomplete"
        )
    return next(iter(values))


def _expected_category_id(
    snapshot: Mapping[str, object], target: str
) -> str:
    rows = snapshot.get("categories_by_target")
    row = rows.get(target) if isinstance(rows, Mapping) else None
    category = row.get("category") if isinstance(row, Mapping) else None
    return str(category.get("id") or "").strip() if isinstance(category, Mapping) else ""


def _positive_int_list(value: object, label: str) -> list[int]:
    if (
        not isinstance(value, list)
        or not value
        or any(type(row) is not int or row <= 0 for row in value)
        or len(set(value)) != len(value)
    ):
        raise ShopeeRegionContractError(f"{label} are invalid")
    return list(value)


def _exact_global_tiers(
    official_rows: list[Mapping[str, object]],
    approved_models: list[Mapping[str, str]],
) -> dict[str, list[int]]:
    by_sku: dict[str, list[list[int]]] = {}
    for row in official_rows:
        sku = str(row.get("global_model_sku") or "").strip()
        tier = row.get("tier_index")
        if (
            not sku
            or not isinstance(tier, list)
            or not tier
            or any(type(value) is not int or value < 0 for value in tier)
        ):
            continue
        by_sku.setdefault(sku, []).append(list(tier))
    expected = {row["model_sku"] for row in approved_models}
    if set(by_sku) != expected or any(len(by_sku[sku]) != 1 for sku in expected):
        raise ShopeeRegionContractError(
            "official global model set does not match the approved SKU set"
        )
    tiers = {sku: by_sku[sku][0] for sku in sorted(expected)}
    if len({tuple(value) for value in tiers.values()}) != len(tiers):
        raise ShopeeRegionContractError("official global model tiers are ambiguous")
    return tiers


def _publish_body(
    *,
    region: str,
    shop_id: int,
    global_item_id: str,
    approved_models: list[Mapping[str, str]],
    tiers: Mapping[str, list[int]],
    logistics: list[int],
) -> dict[str, object]:
    prices = [Decimal(row["price"]) for row in approved_models]
    return {
        "global_item_id": int(global_item_id),
        "shop_id": int(shop_id),
        "shop_region": region,
        "item": {
            "item_status": "NORMAL",
            "original_price": str(min(prices)),
            "logistic": [
                {"logistic_id": value, "enabled": True}
                for value in logistics
            ],
            "model": [
                {
                    "tier_index": list(tiers[row["model_sku"]]),
                    "original_price": row["price"],
                }
                for row in approved_models
            ],
        },
    }


def _official_readback_checks(
    *,
    item: Mapping[str, object] | None,
    models: list[Mapping[str, object]],
    resolved_global_item_id: str,
    expected_global_item_id: str,
    expected_models: list[Mapping[str, str]],
    expected_tiers: Mapping[str, list[int]],
    expected_logistics: list[int],
    expected_image_count: int,
    expected_item_sku: str,
    expected_category_id: str,
    item_id: str,
) -> dict[str, bool]:
    expected = {row["model_sku"]: row for row in expected_models}
    observed = {
        str(row.get("model_sku") or "").strip(): row
        for row in models
        if isinstance(row, Mapping) and str(row.get("model_sku") or "").strip()
    }
    model_prices_exact = set(observed) == set(expected)
    model_ids_present = set(observed) == set(expected)
    model_tiers_exact = set(observed) == set(expected)
    if model_prices_exact:
        for sku, facts in expected.items():
            price = _regional_model_price(observed[sku], facts["currency"])
            model_prices_exact = model_prices_exact and price == Decimal(
                facts["price"]
            )
            model_ids_present = model_ids_present and bool(
                str(observed[sku].get("model_id") or "").strip()
            )
            model_tiers_exact = model_tiers_exact and (
                observed[sku].get("tier_index") == expected_tiers.get(sku)
            )
    image_urls = []
    if isinstance(item, Mapping):
        image = item.get("image")
        if isinstance(image, Mapping):
            image_urls = image.get("image_url_list") or image.get("image_id_list") or []
    enabled_logistics = {
        int(row.get("logistic_id"))
        for row in (
            item.get("logistic_info")
            if isinstance(item, Mapping)
            and isinstance(item.get("logistic_info"), list)
            else []
        )
        if isinstance(row, Mapping)
        and row.get("enabled") is True
        and type(row.get("logistic_id")) is int
        and row["logistic_id"] > 0
    }
    observed_category_id = (
        str(item.get("category_id") or "").strip()
        if isinstance(item, Mapping)
        else ""
    )
    return {
        "item_identity_exact": (
            isinstance(item, Mapping)
            and str(item.get("item_id") or "") == item_id
        ),
        "normal_status_exact": (
            isinstance(item, Mapping)
            and str(item.get("item_status") or "").upper() == "NORMAL"
        ),
        "global_linkage_exact": (
            str(resolved_global_item_id or "") == expected_global_item_id
        ),
        "model_sku_set_exact": set(observed) == set(expected),
        "model_ids_present": model_ids_present,
        "model_tiers_exact": model_tiers_exact,
        "model_prices_exact": model_prices_exact,
        "item_sku_exact": (
            isinstance(item, Mapping)
            and str(item.get("item_sku") or "").strip() == expected_item_sku
        ),
        "selected_logistics_enabled": set(expected_logistics).issubset(
            enabled_logistics
        ),
        "category_exact_when_returned": (
            not observed_category_id
            or not expected_category_id
            or observed_category_id == expected_category_id
        ),
        "copy_present": (
            isinstance(item, Mapping)
            and bool(str(item.get("item_name") or "").strip())
            and bool(str(item.get("description") or "").strip())
        ),
        "images_present": (
            isinstance(image_urls, list)
            and len(image_urls) >= max(1, expected_image_count)
        ),
    }


def _regional_model_price(row: Mapping[str, object], currency: str) -> Decimal | None:
    prices = row.get("price_info")
    if isinstance(prices, Mapping):
        prices = [prices]
    if not isinstance(prices, list):
        value = row.get("original_price")
        return _optional_decimal(value)
    matches = [
        facts
        for facts in prices
        if isinstance(facts, Mapping)
        and str(facts.get("currency") or "").upper() == currency
    ]
    if len(matches) != 1:
        return None
    return _optional_decimal(matches[0].get("original_price"))


def _poll_task(
    runtime: ShopeeRegionRuntime,
    context: RegionContext,
    task_id: str,
    attempts: int,
) -> Mapping[str, object]:
    last: Mapping[str, object] = {}
    for attempt in range(attempts):
        raw = runtime.publish_task_result(context, task_id)
        code, message = _provider_error(raw)
        if code:
            raise RuntimeError(message or code)
        if not isinstance(raw, Mapping):
            raise RuntimeError("official publish task response is malformed")
        response = raw.get("response")
        if not isinstance(response, Mapping):
            raise RuntimeError("official publish task result is unavailable")
        last = response
        if str(response.get("publish_status") or "").lower() in {
            "success",
            "failed",
        }:
            return response
        if attempt + 1 < attempts:
            time.sleep(1)
    return last


def _dispatch_envelope(
    global_item_id: str,
    targets: list[str],
    results: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": "shopee-regional-dispatch/v1",
        "platform": "shopee",
        "global_item_id": global_item_id,
        "target_count": len(targets),
        "accepted_target_count": sum(row.get("accepted") is True for row in results),
        "rejected_target_count": sum(row.get("outcome") == "REJECTED" for row in results),
        "unknown_target_count": sum(row.get("outcome") == "UNKNOWN" for row in results),
        "not_attempted_target_count": sum(row.get("outcome") == "NOT_ATTEMPTED" for row in results),
        "targets": results,
    }


def _target_fact(
    target: str,
    *,
    attempted: bool,
    accepted: bool,
    outcome: str,
    provider_code: str = "",
    message: str = "",
) -> dict[str, object]:
    return {
        "target_label": target,
        "attempted": attempted,
        "accepted": accepted,
        "outcome": outcome,
        "provider_code": _safe_text(provider_code, 80),
        "message": _safe_text(message, 300),
    }


def _provider_error(response: object) -> tuple[str, str]:
    if not isinstance(response, Mapping):
        return "MALFORMED_RESPONSE", "official response is malformed"
    code = str(response.get("error") or "").strip()
    if code and code != "-":
        return code, str(response.get("message") or code)
    return "", ""


def _raise_provider_error(response: object, operation: str) -> None:
    code, message = _provider_error(response)
    if code:
        raise RuntimeError(f"{operation} failed: {_safe_text(message)}")


def _publish_task_id(response: object) -> str:
    if not isinstance(response, Mapping):
        return ""
    body = response.get("response")
    value = body.get("publish_task_id") if isinstance(body, Mapping) else None
    return str(value or "").strip()


def _task_item_id(task: Mapping[str, object]) -> str:
    value = task.get("item_id")
    success = task.get("success")
    if value is None and isinstance(success, Mapping):
        value = success.get("item_id")
    return str(value or "").strip()


def _positive_identity(value: object, label: str) -> str:
    clean = str(value or "").strip()
    if not clean.isdigit() or int(clean) <= 0:
        raise ShopeeRegionContractError(f"{label} is invalid")
    return clean


def _region(value: object) -> str:
    clean = str(value or "").strip().upper()
    if clean not in REGIONAL_CURRENCIES:
        raise ShopeeRegionContractError("unsupported Shopee region")
    return clean


def _positive_decimal(value: object, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ShopeeRegionContractError(f"{label} is invalid") from error
    if not number.is_finite() or number <= 0:
        raise ShopeeRegionContractError(f"{label} is invalid")
    return number


def _optional_decimal(value: object) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def _digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_text(value: object, limit: int = 300) -> str:
    text = " ".join(str(value or "").split())
    for marker in ("access_token", "refresh_token", "partner_key", "secret"):
        if marker in text.casefold():
            return "provider detail redacted"
    return text[:limit]
