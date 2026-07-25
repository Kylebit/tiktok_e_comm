# -*- coding: utf-8 -*-
"""Shopee TH：当前售价 + 周报已结单校准（先验弱于 TK）。"""

from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta
from typing import Any

from core.config import ROOT, get
from core.db import connect, init_db
from modules.finance.sku_key import seller_sku_tail4, same_seller_sku, sku_variants_for_lookup
from modules.finance.sku_profit_model import (
    DEFAULT_AD_RATE,
    DEFAULT_LOOKBACK_DAYS,
    MIN_POSTERIOR_SAMPLES,
    apply_affiliate_quantile_split,
    build_three_scenarios,
    enrich_comp,
    mark_outliers,
    pick_main_conclusion,
    profit_from_settlement,
    reprice_scenarios,
    suggest_rule_tweaks,
    summarize_nums,
)
from modules.shopee.profit_settlement import REPORT_GLOB, _extract_data, _header_index
from modules.sourcing.fx_rates import get_exchange_rates

REGION = "TH"
OUTPUT_DIR = ROOT / "outputs"


def _live_fx(*, force_refresh: bool = False) -> dict[str, Any]:
    fx = get_exchange_rates(force_refresh=force_refresh)
    rate = float((fx.get("rates") or {}).get("THB") or 0)
    return {**fx, "THB": rate}


def _cost_from_weekly(seller_sku: str) -> float | None:
    """周报 product_cost 中位，作 sku_costs 缺失时的回退。"""
    if not OUTPUT_DIR.is_dir() or not seller_sku:
        return None
    tail = seller_sku_tail4(seller_sku)
    vals: list[float] = []
    for path in sorted(OUTPUT_DIR.glob(REPORT_GLOB), reverse=True)[:8]:
        data = _extract_data(path)
        if not data:
            continue
        headers = data.get("headers") or []
        sku_idx = _header_index(headers, "SKU")
        for row in data.get("rows") or []:
            if str(row.get("region") or "") != REGION:
                continue
            cells = row.get("cells") or []
            sku = str(cells[sku_idx] if sku_idx >= 0 and sku_idx < len(cells) else "").strip()
            if "," in sku:
                continue  # 多 SKU 行跳过
            if seller_sku_tail4(sku) != tail:
                continue
            # Weekly snapshots now expose both unit and total line cost.  A
            # SKU price estimate needs the unit cost; old snapshots retain
            # product_cost as the compatible fallback.
            pc = row.get("unit_cost_cny") or row.get("product_cost")
            if pc not in (None, "", 0, 0.0):
                try:
                    vals.append(float(pc))
                except (TypeError, ValueError):
                    pass
    if not vals:
        return None
    return float(statistics.median(vals))


def resolve_product(sku_query: str) -> dict[str, Any] | None:
    q = (sku_query or "").strip()
    if not q:
        return None
    init_db()
    conn = connect()
    variants = sku_variants_for_lookup(q)
    tail = seller_sku_tail4(q)
    row = None
    for v in variants:
        row = conn.execute(
            """
            SELECT model_id, item_id, seller_sku, product_name, model_name, image_url, price, currency, status
            FROM shopee_products
            WHERE region = ? AND seller_sku = ?
            LIMIT 1
            """,
            (REGION, v),
        ).fetchone()
        if row:
            break
    if row is None and tail:
        # 末四位对齐扫 TH 店
        for r in conn.execute(
            """
            SELECT model_id, item_id, seller_sku, product_name, model_name, image_url, price, currency, status
            FROM shopee_products WHERE region = ?
            """,
            (REGION,),
        ).fetchall():
            if seller_sku_tail4(str(r["seller_sku"] or "")) == tail:
                row = r
                break

    cost = None
    cost_source = None
    seller = str(row["seller_sku"] or "") if row else q
    seller_tail = seller_sku_tail4(seller) if row else tail
    if row and seller_tail:
        cost_rows = conn.execute(
            """
            SELECT p.seller_sku, s.cost_cny
            FROM products p
            JOIN sku_costs s ON s.sku_id = p.sku_id AND s.cost_cny > 0
            ORDER BY s.updated_at DESC
            """
        ).fetchall()
        for r in cost_rows:
            if seller_sku_tail4(str(r["seller_sku"] or "")) == seller_tail:
                cost = float(r["cost_cny"])
                cost_source = "sku_costs_via_tk_seller_sku_tail4"
                break
    conn.close()
    if not row:
        return None
    if cost is None:
        weekly = _cost_from_weekly(seller)
        if weekly is not None:
            cost = weekly
            cost_source = "shopee_weekly_product_cost"
    return {
        "model_id": str(row["model_id"] or ""),
        "item_id": str(row["item_id"] or ""),
        "seller_sku": str(row["seller_sku"] or ""),
        "product_name": row["product_name"] or "",
        "model_name": row["model_name"] or "",
        "image_url": row["image_url"] or "",
        "sale_local": float(row["price"] or 0),
        "currency": row["currency"] or "THB",
        "status": row["status"] or "",
        "cost_cny": cost,
        "cost_source": cost_source,
        "price_source": "shopee_products_db",
        "sku_tail4": seller_tail,
    }


def _parse_release(cell: str) -> date | None:
    if not cell:
        return None
    text = str(cell).strip()
    for fmt, n in (
        ("%Y-%m-%d %H:%M", 16),
        ("%Y/%m/%d %H:%M", 16),
        ("%Y-%m-%d", 10),
        ("%Y/%m/%d", 10),
    ):
        try:
            return datetime.strptime(text[:n], fmt).date()
        except ValueError:
            continue
    return None


def load_weekly_comps(
    seller_sku: str,
    cost_cny: float,
    fx: float,
    ad_rate: float,
    *,
    lookback_days: int = 45,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回 (同 SKU comps, TH 全站 comps 供弱先验)。Shopee 周报稀疏，默认 45 天。"""
    if not OUTPUT_DIR.is_dir():
        return [], []
    cutoff = date.today() - timedelta(days=lookback_days)
    same: list[dict[str, Any]] = []
    all_th: list[dict[str, Any]] = []
    all_th_unfiltered: list[dict[str, Any]] = []
    latest_by_order_sku: dict[
        tuple[str, str], tuple[tuple[str, str], date | None, str, dict[str, Any]]
    ] = {}
    tail = seller_sku_tail4(seller_sku)

    for path in sorted(OUTPUT_DIR.glob(REPORT_GLOB), reverse=True):
        data = _extract_data(path)
        if not data:
            continue
        headers = data.get("headers") or []
        order_idx = _header_index(headers, "Order SN")
        sku_idx = _header_index(headers, "SKU")
        release_idx = _header_index(headers, "Release Time")
        for row_index, row in enumerate(data.get("rows") or []):
            if str(row.get("region") or "") != REGION:
                continue
            cells = row.get("cells") or []
            sku = str(cells[sku_idx] if sku_idx >= 0 and sku_idx < len(cells) else "").strip()
            order_sn = str(cells[order_idx] if order_idx >= 0 and order_idx < len(cells) else "")
            released = _parse_release(
                str(cells[release_idx] if release_idx >= 0 and release_idx < len(cells) else "")
            )
            sale = float(row.get("subtotal") or 0)
            settle = float(row.get("settlement") or 0)
            rec = enrich_comp(
                order_id=order_sn or f"{path.name}:{sku}",
                statement_date=released.isoformat() if released else "",
                sale_local=sale,
                settlement_local=settle,
                cost_cny=float(row.get("product_cost") or cost_cny or 0),
                fx=fx,
                ad_rate=ad_rate,
                commission_local=0.0,
                affiliate_local=0.0,
                ship_net_local=0.0,
                source=f"weekly:{path.name}",
            )
            identity = (
                (order_sn, sku)
                if order_sn and sku
                else (f"{path.name}:{row_index}", sku)
            )
            rank = (released.isoformat() if released else "", path.name)
            previous = latest_by_order_sku.get(identity)
            if previous is None or rank >= previous[0]:
                latest_by_order_sku[identity] = (rank, released, sku, rec)

    for _, released, sku, rec in latest_by_order_sku.values():
        all_th_unfiltered.append(rec)
        if released and released < cutoff:
            continue
        all_th.append(rec)
        if same_seller_sku(sku, seller_sku) or (
            tail and seller_sku_tail4(sku) == tail
        ):
            same.append(rec)

    # 窗口内为空时，退回未过滤 TH 池做弱先验
    if not all_th and all_th_unfiltered:
        all_th = all_th_unfiltered
    same = mark_outliers(same)
    all_th = mark_outliers(all_th)
    same = apply_affiliate_quantile_split(same)
    all_th = apply_affiliate_quantile_split(all_th)
    same.sort(key=lambda c: c.get("statement_date") or "", reverse=True)
    all_th.sort(key=lambda c: c.get("statement_date") or "", reverse=True)
    return same, all_th


def _weak_prior_from_pool(
    sale_local: float,
    cost_cny: float,
    fx: float,
    pool: list[dict[str, Any]],
    ad_rate: float,
    *,
    low_ratio: bool,
) -> dict[str, Any] | None:
    usable = [c for c in pool if not c.get("outlier") and c.get("settle_ratio") is not None]
    usable = [c for c in usable if float(c["settle_ratio"]) > 0.1]
    if not usable:
        return None
    ratios = sorted(float(c["settle_ratio"]) for c in usable)
    if low_ratio:
        # 有达人/高费：P25
        ratio = ratios[max(0, len(ratios) // 4)]
    else:
        # 无达人/低费：P75
        ratio = ratios[min(len(ratios) - 1, (3 * len(ratios)) // 4)]
    settle = sale_local * ratio
    return {
        **profit_from_settlement(
            settlement_local=settle,
            sale_local=sale_local,
            cost_cny=cost_cny,
            fx_cny_per_local=fx,
            ad_rate=ad_rate,
        ),
        "est_settlement_local": round(settle, 2),
        "median_settle_ratio": round(ratio, 4),
        "evidence_basis": "inferred_settlement_ratio",
        "note": "Shopee 弱先验：用 TH 周报结算比分位近似（非完整费率栈）",
    }


def estimate(
    sku_query: str,
    *,
    ad_rate: float = DEFAULT_AD_RATE,
    lookback_days: int = 45,
    sale_override: float | None = None,
    cost_override: float | None = None,
    force_fx_refresh: bool = False,
) -> dict[str, Any]:
    product = resolve_product(sku_query)
    if not product:
        return {"ok": False, "error": f"未找到 Shopee TH SKU: {sku_query}", "platform": "shopee", "region": REGION}
    if cost_override is not None and float(cost_override) > 0:
        product = {**product, "cost_cny": float(cost_override), "cost_source": "manual_override"}
    if product.get("cost_cny") is None:
        return {
            "ok": False,
            "error": (
                f"SKU {product.get('seller_sku')} 缺少货本"
                f"（可填手动货本，或对齐 TK seller_sku / 周报 product_cost）"
            ),
            "platform": "shopee",
            "region": REGION,
            "product": product,
        }

    fx_info = _live_fx(force_refresh=force_fx_refresh)
    fx = float(fx_info.get("THB") or 0)
    if fx <= 0:
        return {"ok": False, "error": "无法获取 THB 实时汇率", "platform": "shopee"}

    list_price = float(product["sale_local"])
    cost = float(product["cost_cny"])
    same, all_th = load_weekly_comps(
        product["seller_sku"], cost, fx, ad_rate, lookback_days=lookback_days
    )

    usable_sales = [float(c["sale_local"]) for c in same if not c.get("outlier") and c.get("sale_local")]
    recent_avg = statistics.median(usable_sales) if usable_sales else None
    if sale_override and sale_override > 0:
        sale = float(sale_override)
        sale_basis = "manual_override"
    elif recent_avg and recent_avg > 0:
        sale = float(recent_avg)
        sale_basis = "recent_comp_median_paid"
    else:
        sale = list_price
        sale_basis = "db_price"

    product = {
        **product,
        "sale_local": sale,
        "list_price_local": list_price,
        "recent_avg_sale_local": recent_avg,
        "sale_basis": sale_basis,
        "cost_cny": cost,
    }

    prior_with = _weak_prior_from_pool(sale, cost, fx, all_th or same, ad_rate, low_ratio=True)
    prior_no = _weak_prior_from_pool(sale, cost, fx, all_th or same, ad_rate, low_ratio=False)

    # 全店样本只用于弱先验，绝不能冒充这个 SKU 的后验样本。
    comps = same
    scenarios = build_three_scenarios(
        sale_local=sale,
        cost_cny=cost,
        fx=fx,
        comps=comps,
        prior_with_creator=prior_with,
        prior_no_creator=prior_no,
        ad_rate=ad_rate,
    )
    usable_n = scenarios["sample_counts"]["all_usable"]
    main = pick_main_conclusion(scenarios=scenarios, usable_n=usable_n)

    warnings: list[str] = []
    if product.get("cost_source") == "shopee_weekly_product_cost":
        warnings.append("货本来自 Shopee 周报 product_cost 中位（非 sku_costs 主库），请核对")
    if sale_basis == "recent_comp_median_paid" and list_price and abs(sale - list_price) / max(list_price, 1) > 0.03:
        warnings.append(
            f"主结论用近单实付中位 {sale:.1f} THB（目录价 {list_price:.1f}）；下方另给目录价情景"
        )
    if usable_n < MIN_POSTERIOR_SAMPLES:
        warnings.append(
            f"同款近单不足 {MIN_POSTERIOR_SAMPLES}（可用 {usable_n}），主结论偏保守/弱先验"
        )

    def _prior_slice(p: dict[str, Any] | None) -> dict[str, Any] | None:
        if not p:
            return None
        return {
            "profit_cny": p.get("profit_cny"),
            "profit_local": p.get("profit_local"),
            "margin_pct": p.get("margin_pct"),
            "est_settlement_local": p.get("est_settlement_local"),
            "evidence_basis": p.get("evidence_basis"),
        }

    price_views = [
        reprice_scenarios(
            sale_local=sale,
            cost_cny=cost,
            fx=fx,
            comps=comps,
            prior_with=_prior_slice(prior_with),
            prior_no=_prior_slice(prior_no),
            ad_rate=ad_rate,
            label="主售价（用于主结论）",
        )
    ]
    if abs(list_price - sale) > 0.5:
        pw = _weak_prior_from_pool(list_price, cost, fx, all_th or same, ad_rate, low_ratio=True)
        pn = _weak_prior_from_pool(list_price, cost, fx, all_th or same, ad_rate, low_ratio=False)
        price_views.append(
            reprice_scenarios(
                sale_local=list_price,
                cost_cny=cost,
                fx=fx,
                comps=comps,
                prior_with=_prior_slice(pw),
                prior_no=_prior_slice(pn),
                ad_rate=ad_rate,
                label="当前目录价",
            )
        )

    settings_fx = float((get("exchange_rates") or {}).get("THB") or 0)
    suggestions = suggest_rule_tweaks(
        model_logistics_local=0.0,
        comps=comps,
        fx_settings=settings_fx,
        fx_live=fx,
        main_margin_pct=main.get("margin_pct"),
        target_margin_pct=15.0,
    )
    suggestions.insert(
        0,
        {
            "code": "shopee_weak_prior",
            "title": "Shopee 先验较弱",
            "detail": "Shopee 无 Treasury 级费率栈；达人情景用结算比分位近似。改规则仍须你确认。",
        },
    )

    return {
        "ok": True,
        "platform": "shopee",
        "shop": "Shopee TH 主店",
        "region": REGION,
        "currency": "THB",
        "ad_rate": ad_rate,
        "ad_note": "统一按售价×22%估广告；Ads 实耗分摊为后续待办",
        "product": product,
        "fx": {
            "THB_CNY": fx,
            "live": fx_info.get("live"),
            "cached": fx_info.get("cached"),
            "stale": fx_info.get("stale"),
            "as_of": fx_info.get("as_of"),
            "fetched_at": fx_info.get("fetched_at"),
            "provider": fx_info.get("provider"),
            "settings_THB": settings_fx,
        },
        "prior": {
            "with_affiliate": prior_with,
            "no_affiliate": prior_no,
            "rule_source": "weak: TH weekly settle-ratio percentiles",
            "target_margin_pct": 15.0,
        },
        "posterior": {
            "lookback_days": lookback_days,
            "comps_same_sku": len(same),
            "comps_th_pool": len(all_th),
            "min_samples_for_posterior": MIN_POSTERIOR_SAMPLES,
            "recent_comps": same[:20],
            "sale_stats": summarize_nums(usable_sales),
            "affiliate_note": "周报未拆达人佣金，has_affiliate 为结算比中位分档近似",
        },
        "scenarios": scenarios,
        "main": main,
        "price_views": price_views,
        "rule_suggestions": suggestions,
        "warnings": warnings,
        "rule_writeback": "disabled_requires_manual_confirm",
    }


def list_hot_skus(limit: int = 30) -> list[dict[str, Any]]:
    from collections import Counter

    if not OUTPUT_DIR.is_dir():
        return []
    counts: Counter[str] = Counter()
    for path in OUTPUT_DIR.glob(REPORT_GLOB):
        data = _extract_data(path)
        if not data:
            continue
        headers = data.get("headers") or []
        sku_idx = _header_index(headers, "SKU")
        for row in data.get("rows") or []:
            if str(row.get("region") or "") != REGION:
                continue
            cells = row.get("cells") or []
            sku = str(cells[sku_idx] if sku_idx >= 0 and sku_idx < len(cells) else "").strip()
            if sku:
                counts[sku] += 1
    out = []
    for sku, n in counts.most_common(limit * 4):
        p = resolve_product(sku)
        if not p or p.get("cost_cny") is None:
            continue
        out.append(
            {
                "seller_sku": p["seller_sku"],
                "product_name": (p.get("product_name") or "")[:80],
                "image_url": p.get("image_url") or "",
                "price": p.get("list_price_local") or p.get("sale_local"),
                "cost_cny": p.get("cost_cny"),
                "order_lines": n,
                "platform": "shopee",
            }
        )
        if len(out) >= limit:
            break
    return out
