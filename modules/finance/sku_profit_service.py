# -*- coding: utf-8 -*-
"""SKU 利润估算服务入口。"""

from __future__ import annotations

from typing import Any

from modules.finance import sku_profit_shopee, sku_profit_tk
from modules.finance.sku_profit_model import DEFAULT_AD_RATE, DEFAULT_LOOKBACK_DAYS


def _norm_ad_rate(ad_rate: float | None) -> float:
    if ad_rate is None:
        return DEFAULT_AD_RATE
    v = float(ad_rate)
    return v if v <= 1 else v / 100.0


def estimate(
    sku: str,
    *,
    platform: str = "both",
    ad_rate: float | None = None,
    lookback_days: int | None = None,
    sale_override: float | None = None,
    cost_override: float | None = None,
    force_fx_refresh: bool = False,
) -> dict[str, Any]:
    """platform: tiktok | shopee | both"""
    sku = (sku or "").strip()
    if not sku:
        return {"ok": False, "error": "需要 sku 参数"}

    plat = (platform or "both").strip().lower()
    rate = _norm_ad_rate(ad_rate)
    results: dict[str, Any] = {
        "ok": True,
        "sku": sku,
        "ad_rate": rate,
        "lookback_days": lookback_days,
        "sale_override": sale_override,
        "cost_override": cost_override,
        "platforms": {},
        "partial": False,
        "todos": [
            {
                "id": "ads_spend_allocation",
                "title": "广告费按 SKU/订单实耗分摊",
                "status": "todo",
                "note": "很难；当前统一 22%，后续再做",
            }
        ],
    }

    errors: list[str] = []
    kwargs: dict[str, Any] = {
        "ad_rate": rate,
        "force_fx_refresh": force_fx_refresh,
    }
    if sale_override is not None:
        kwargs["sale_override"] = sale_override
    if cost_override is not None:
        kwargs["cost_override"] = cost_override

    if plat in ("tiktok", "tk", "both", "all"):
        tk_kwargs = dict(kwargs)
        if lookback_days is not None:
            tk_kwargs["lookback_days"] = int(lookback_days)
        try:
            results["platforms"]["tiktok"] = sku_profit_tk.estimate(sku, **tk_kwargs)
        except Exception as exc:  # noqa: BLE001
            results["platforms"]["tiktok"] = {"ok": False, "error": str(exc), "platform": "tiktok"}
            errors.append(f"tiktok: {exc}")

    if plat in ("shopee", "sp", "both", "all"):
        sp_kwargs = dict(kwargs)
        # 周报稀疏：Shopee 至少 45 天
        user_lb = int(lookback_days) if lookback_days is not None else 45
        sp_kwargs["lookback_days"] = max(user_lb, 45)
        try:
            results["platforms"]["shopee"] = sku_profit_shopee.estimate(sku, **sp_kwargs)
        except Exception as exc:  # noqa: BLE001
            results["platforms"]["shopee"] = {"ok": False, "error": str(exc), "platform": "shopee"}
            errors.append(f"shopee: {exc}")

    oks = [p.get("ok") for p in results["platforms"].values()]
    any_ok = any(oks)
    all_ok = bool(oks) and all(oks)
    results["ok"] = any_ok
    results["partial"] = any_ok and not all_ok
    if not any_ok:
        results["error"] = "; ".join(
            (p.get("error") or "") for p in results["platforms"].values() if not p.get("ok")
        ) or ("; ".join(errors) or "全部平台估算失败")
    elif results["partial"]:
        results["warning"] = "部分平台失败：" + "; ".join(
            f"{k}: {p.get('error')}" for k, p in results["platforms"].items() if not p.get("ok")
        )

    # 双平台对照摘要（便于一眼比）
    compare = []
    for k, p in results["platforms"].items():
        if not p.get("ok"):
            compare.append({"platform": k, "ok": False, "error": p.get("error")})
            continue
        m = p.get("main") or {}
        prod = p.get("product") or {}
        compare.append(
            {
                "platform": k,
                "ok": True,
                "sale_local": prod.get("sale_local"),
                "cost_cny": prod.get("cost_cny"),
                "profit_cny": m.get("profit_cny"),
                "margin_pct": m.get("margin_pct"),
                "label": m.get("label"),
                "confidence": m.get("confidence"),
                "fx": (p.get("fx") or {}).get("THB_CNY"),
            }
        )
    results["compare"] = compare
    return results


def estimate_batch(
    skus: list[str],
    *,
    platform: str = "both",
    ad_rate: float | None = None,
    lookback_days: int | None = None,
    cost_override: float | None = None,
) -> dict[str, Any]:
    rows = []
    for raw in skus:
        sku = str(raw or "").strip()
        if not sku:
            continue
        data = estimate(
            sku,
            platform=platform,
            ad_rate=ad_rate,
            lookback_days=lookback_days,
            cost_override=cost_override,
        )
        summary = {"sku": sku, "ok": data.get("ok"), "partial": data.get("partial"), "platforms": {}}
        for k, p in (data.get("platforms") or {}).items():
            m = p.get("main") or {}
            prod = p.get("product") or {}
            summary["platforms"][k] = {
                "ok": p.get("ok"),
                "error": p.get("error"),
                "sale_local": prod.get("sale_local"),
                "cost_cny": prod.get("cost_cny"),
                "profit_cny": m.get("profit_cny"),
                "margin_pct": m.get("margin_pct"),
                "label": m.get("label"),
                "confidence": m.get("confidence"),
            }
        rows.append(summary)
    return {
        "ok": True,
        "count": len(rows),
        "ad_rate": _norm_ad_rate(ad_rate),
        "rows": rows,
    }


def list_hot_skus(platform: str = "both", limit: int = 20) -> dict[str, Any]:
    plat = (platform or "both").strip().lower()
    out: dict[str, Any] = {"ok": True, "tiktok": [], "shopee": []}
    if plat in ("tiktok", "tk", "both", "all"):
        out["tiktok"] = sku_profit_tk.list_hot_skus(limit=limit)
    if plat in ("shopee", "sp", "both", "all"):
        out["shopee"] = sku_profit_shopee.list_hot_skus(limit=limit)
    return out
