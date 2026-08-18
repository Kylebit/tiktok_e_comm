# -*- coding: utf-8 -*-
"""Validated service boundary for the SKU profit probes."""

from __future__ import annotations

import math
from typing import Any

from modules.finance import sku_profit_shopee, sku_profit_tk
from modules.finance.sku_profit_model import DEFAULT_AD_RATE


_PLATFORM_ALIASES = {
    "tiktok": "tiktok",
    "tk": "tiktok",
    "shopee": "shopee",
    "sp": "shopee",
    "both": "both",
    "all": "both",
}


def _norm_platform(platform: object) -> str:
    raw = str(platform or "both").strip().lower()
    if raw not in _PLATFORM_ALIASES:
        raise ValueError("platform must be tiktok, shopee, or both")
    return _PLATFORM_ALIASES[raw]


def _finite_number(
    value: object,
    *,
    field: str,
    minimum: float | None = None,
    maximum: float | None = None,
    minimum_inclusive: bool = True,
) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    if minimum is not None:
        invalid = number < minimum if minimum_inclusive else number <= minimum
        if invalid:
            operator = ">=" if minimum_inclusive else ">"
            raise ValueError(f"{field} must be {operator} {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field} must be <= {maximum}")
    return number


def _norm_ad_rate_fraction(ad_rate: object | None) -> float:
    if ad_rate is None:
        return DEFAULT_AD_RATE
    return _finite_number(ad_rate, field="ad_rate", minimum=0, maximum=1)


def _norm_ad_rate_percent(ad_rate_percent: object) -> float:
    value = _finite_number(
        ad_rate_percent,
        field="ad_rate_percent",
        minimum=0,
        maximum=100,
    )
    return value / 100.0


def _resolve_ad_rate(
    *,
    ad_rate: object | None,
    ad_rate_percent: object | None,
) -> float:
    if ad_rate is not None and ad_rate_percent is not None:
        raise ValueError("provide ad_rate fraction or ad_rate_percent, not both")
    if ad_rate_percent is not None:
        return _norm_ad_rate_percent(ad_rate_percent)
    return _norm_ad_rate_fraction(ad_rate)


def _norm_lookback_days(lookback_days: object | None) -> int | None:
    if lookback_days is None:
        return None
    if isinstance(lookback_days, bool):
        raise ValueError("lookback_days must be an integer from 1 to 365")
    try:
        value = int(lookback_days)
    except (TypeError, ValueError) as exc:
        raise ValueError("lookback_days must be an integer from 1 to 365") from exc
    if str(lookback_days).strip() not in {str(value), f"+{value}"}:
        raise ValueError("lookback_days must be an integer from 1 to 365")
    if not 1 <= value <= 365:
        raise ValueError("lookback_days must be an integer from 1 to 365")
    return value


def _norm_override(value: object | None, *, field: str) -> float | None:
    if value is None:
        return None
    return _finite_number(value, field=field, minimum=0, minimum_inclusive=False)


def estimate(
    sku: str,
    *,
    platform: str = "both",
    ad_rate: float | None = None,
    ad_rate_percent: float | None = None,
    lookback_days: int | None = None,
    sale_override: float | None = None,
    cost_override: float | None = None,
    force_fx_refresh: bool = False,
) -> dict[str, Any]:
    """Estimate one Seller SKU or exact platform SKU with validated inputs."""
    clean_sku = str(sku or "").strip()
    if not clean_sku:
        raise ValueError("sku is required")
    if len(clean_sku) > 128:
        raise ValueError("sku must not exceed 128 characters")

    normalized_platform = _norm_platform(platform)
    rate = _resolve_ad_rate(ad_rate=ad_rate, ad_rate_percent=ad_rate_percent)
    normalized_lookback = _norm_lookback_days(lookback_days)
    normalized_sale = _norm_override(sale_override, field="sale_override")
    normalized_cost = _norm_override(cost_override, field="cost_override")
    results: dict[str, Any] = {
        "ok": True,
        "sku": clean_sku,
        "ad_rate": rate,
        "ad_rate_percent": rate * 100,
        "lookback_days": normalized_lookback,
        "sale_override": normalized_sale,
        "cost_override": normalized_cost,
        "platforms": {},
        "partial": False,
        "todos": [
            {
                "id": "ads_spend_allocation",
                "title": "广告费按 SKU/订单实耗分摊",
                "status": "todo",
                "note": "当前仍使用统一广告费率，结果属于估算。",
            }
        ],
    }

    errors: list[str] = []
    kwargs: dict[str, Any] = {
        "ad_rate": rate,
        "force_fx_refresh": force_fx_refresh,
    }
    if normalized_sale is not None:
        kwargs["sale_override"] = normalized_sale
    if normalized_cost is not None:
        kwargs["cost_override"] = normalized_cost

    if normalized_platform in ("tiktok", "both"):
        tk_kwargs = dict(kwargs)
        if normalized_lookback is not None:
            tk_kwargs["lookback_days"] = normalized_lookback
        try:
            results["platforms"]["tiktok"] = sku_profit_tk.estimate(clean_sku, **tk_kwargs)
        except Exception as exc:  # noqa: BLE001
            results["platforms"]["tiktok"] = {
                "ok": False,
                "error": str(exc),
                "platform": "tiktok",
            }
            errors.append(f"tiktok: {exc}")

    if normalized_platform in ("shopee", "both"):
        sp_kwargs = dict(kwargs)
        # Shopee weekly snapshots are sparse, so retain the governed 45-day floor.
        user_lookback = normalized_lookback if normalized_lookback is not None else 45
        sp_kwargs["lookback_days"] = max(user_lookback, 45)
        try:
            results["platforms"]["shopee"] = sku_profit_shopee.estimate(clean_sku, **sp_kwargs)
        except Exception as exc:  # noqa: BLE001
            results["platforms"]["shopee"] = {
                "ok": False,
                "error": str(exc),
                "platform": "shopee",
            }
            errors.append(f"shopee: {exc}")

    successes = [bool(item.get("ok")) for item in results["platforms"].values()]
    any_ok = any(successes)
    all_ok = bool(successes) and all(successes)
    results["ok"] = any_ok
    results["partial"] = any_ok and not all_ok
    if not any_ok:
        results["error"] = "; ".join(
            (item.get("error") or "")
            for item in results["platforms"].values()
            if not item.get("ok")
        ) or ("; ".join(errors) or "all platform estimates failed")
    elif results["partial"]:
        results["warning"] = "部分平台失败：" + "; ".join(
            f"{key}: {item.get('error')}"
            for key, item in results["platforms"].items()
            if not item.get("ok")
        )

    compare = []
    for key, item in results["platforms"].items():
        if not item.get("ok"):
            compare.append({"platform": key, "ok": False, "error": item.get("error")})
            continue
        main = item.get("main") or {}
        product = item.get("product") or {}
        compare.append(
            {
                "platform": key,
                "ok": True,
                "sale_local": product.get("sale_local"),
                "cost_cny": product.get("cost_cny"),
                "profit_cny": main.get("profit_cny"),
                "margin_pct": main.get("margin_pct"),
                "label": main.get("label"),
                "confidence": main.get("confidence"),
                "fx": (item.get("fx") or {}).get("THB_CNY"),
            }
        )
    results["compare"] = compare
    return results


def estimate_batch(
    skus: list[str],
    *,
    platform: str = "both",
    ad_rate: float | None = None,
    ad_rate_percent: float | None = None,
    lookback_days: int | None = None,
    cost_override: float | None = None,
) -> dict[str, Any]:
    if not isinstance(skus, (list, tuple)):
        raise ValueError("skus must be an array")
    if len(skus) > 30:
        raise ValueError("batch supports at most 30 SKUs")
    normalized_platform = _norm_platform(platform)
    normalized_rate = _resolve_ad_rate(
        ad_rate=ad_rate,
        ad_rate_percent=ad_rate_percent,
    )
    normalized_lookback = _norm_lookback_days(lookback_days)
    normalized_cost = _norm_override(cost_override, field="cost_override")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in skus:
        if isinstance(raw, (dict, list, tuple, set)):
            raise ValueError("every SKU must be a string or number")
        sku = str(raw or "").strip()
        if not sku or sku in seen:
            continue
        seen.add(sku)
        data = estimate(
            sku,
            platform=normalized_platform,
            ad_rate=normalized_rate,
            lookback_days=normalized_lookback,
            cost_override=normalized_cost,
        )
        summary = {
            "sku": sku,
            "ok": data.get("ok"),
            "partial": data.get("partial"),
            "platforms": {},
        }
        for key, item in (data.get("platforms") or {}).items():
            main = item.get("main") or {}
            product = item.get("product") or {}
            summary["platforms"][key] = {
                "ok": item.get("ok"),
                "error": item.get("error"),
                "sale_local": product.get("sale_local"),
                "cost_cny": product.get("cost_cny"),
                "profit_cny": main.get("profit_cny"),
                "margin_pct": main.get("margin_pct"),
                "label": main.get("label"),
                "confidence": main.get("confidence"),
            }
        rows.append(summary)
    if not rows:
        raise ValueError("at least one non-empty SKU is required")
    success_count = sum(1 for row in rows if row["ok"])
    partial_count = sum(1 for row in rows if row["partial"])
    return {
        "ok": success_count > 0,
        "count": len(rows),
        "success_count": success_count,
        "failed_count": len(rows) - success_count,
        "partial_count": partial_count,
        "partial": success_count > 0
        and (success_count < len(rows) or partial_count > 0),
        "ad_rate": normalized_rate,
        "ad_rate_percent": normalized_rate * 100,
        "rows": rows,
    }


def list_hot_skus(platform: str = "both", limit: int = 20) -> dict[str, Any]:
    normalized_platform = _norm_platform(platform)
    out: dict[str, Any] = {"ok": True, "tiktok": [], "shopee": []}
    if normalized_platform in ("tiktok", "both"):
        out["tiktok"] = sku_profit_tk.list_hot_skus(limit=limit)
    if normalized_platform in ("shopee", "both"):
        out["shopee"] = sku_profit_shopee.list_hot_skus(limit=limit)
    return out
