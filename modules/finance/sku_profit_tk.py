# -*- coding: utf-8 -*-
"""TikTok TH LivelyHive：当前售价 + 同款已结单校准。"""

from __future__ import annotations

import csv
import statistics
import time
from datetime import datetime, timezone
from typing import Any

from core import auth
from core.api_client import get as api_get
from core.config import ROOT, get
from core.db import connect, init_db
from core.shops import list_shops
from modules.finance.sku_key import seller_sku_tail4, sku_variants_for_lookup
from modules.finance.sku_profit_model import (
    DEFAULT_AD_RATE,
    DEFAULT_LOOKBACK_DAYS,
    MIN_POSTERIOR_SAMPLES,
    build_three_scenarios,
    enrich_comp,
    mark_outliers,
    pick_main_conclusion,
    prior_profit_from_breakdown,
    reprice_scenarios,
    suggest_rule_tweaks,
    summarize_nums,
    tk_prior_breakdown,
)
from modules.sourcing.fx_rates import get_exchange_rates
from modules.sourcing.new_product_workbench import DISCOUNT_RESERVE_RATE, SEA_TARGET_MARGIN

INCOME_DIR = ROOT / "CURSOR" / "Income_Data"
SHOP_NAME = "LivelyHive"
REGION = "TH"


def _fnum(val: Any) -> float:
    if val in (None, "", "/"):
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _live_fx(*, force_refresh: bool = False) -> dict[str, Any]:
    fx = get_exchange_rates(force_refresh=force_refresh)
    rate = float((fx.get("rates") or {}).get("THB") or 0)
    return {**fx, "THB": rate}


def resolve_product(sku_query: str) -> dict[str, Any] | None:
    """seller_sku 或平台 sku_id → TH LivelyHive 商品 + 货本。

    同 seller_sku 在多站点会有多行（GBP/VND/THB…），必须锁 TH 店铺 cipher。
    SKU 对齐只看末四位（990021 ≡ 0021）。
    """
    q = (sku_query or "").strip()
    if not q:
        return None
    init_db()
    conn = connect()

    th_cipher = ""
    try:
        tok = auth.ensure_valid_token()["access_token"]
        th_cipher = str(_th_shop(tok).get("cipher") or "")
    except Exception:
        th_cipher = ""

    candidates_q = sku_variants_for_lookup(q)
    tail = seller_sku_tail4(q)

    def _pick_cost(sku_id: str) -> float | None:
        c = conn.execute(
            "SELECT cost_cny FROM sku_costs WHERE sku_id = ? AND cost_cny > 0",
            (sku_id,),
        ).fetchone()
        if c:
            return float(c["cost_cny"])
        return None

    def _cost_by_tail(tail4: str) -> float | None:
        if not tail4:
            return None
        rows = conn.execute(
            """
            SELECT p.seller_sku, s.cost_cny
            FROM products p
            JOIN sku_costs s ON s.sku_id = p.sku_id AND s.cost_cny > 0
            ORDER BY s.updated_at DESC
            """
        ).fetchall()
        for r in rows:
            if seller_sku_tail4(str(r["seller_sku"] or "")) == tail4:
                return float(r["cost_cny"])
        return None

    def _find_th_by_seller_candidates(cands: list[str]):
        for cand in cands:
            if th_cipher:
                hit = conn.execute(
                    """
                    SELECT sku_id, seller_sku, product_id, product_name, sku_name, image_url, price, currency, shop_cipher
                    FROM products
                    WHERE seller_sku = ? AND shop_cipher = ?
                    LIMIT 1
                    """,
                    (cand, th_cipher),
                ).fetchone()
                if hit:
                    return hit
            hit = conn.execute(
                """
                SELECT sku_id, seller_sku, product_id, product_name, sku_name, image_url, price, currency, shop_cipher
                FROM products
                WHERE seller_sku = ? AND currency = 'THB'
                LIMIT 1
                """,
                (cand,),
            ).fetchone()
            if hit:
                return hit
        return None

    def _find_th_by_tail4(tail4: str):
        if not tail4:
            return None
        if th_cipher:
            rows = conn.execute(
                """
                SELECT sku_id, seller_sku, product_id, product_name, sku_name, image_url, price, currency, shop_cipher
                FROM products WHERE shop_cipher = ?
                """,
                (th_cipher,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT sku_id, seller_sku, product_id, product_name, sku_name, image_url, price, currency, shop_cipher
                FROM products WHERE currency = 'THB'
                """
            ).fetchall()
        for r in rows:
            if seller_sku_tail4(str(r["seller_sku"] or "")) == tail4:
                return r
        return None

    row = None
    # 1) 精确平台 sku_id（长 ID）
    if len(q) > 10:
        if th_cipher:
            row = conn.execute(
                """
                SELECT sku_id, seller_sku, product_id, product_name, sku_name, image_url, price, currency, shop_cipher
                FROM products WHERE sku_id = ? AND shop_cipher = ? LIMIT 1
                """,
                (q, th_cipher),
            ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT sku_id, seller_sku, product_id, product_name, sku_name, image_url, price, currency, shop_cipher
                FROM products WHERE sku_id = ? LIMIT 1
                """,
                (q,),
            ).fetchone()
            if row and th_cipher and str(row["shop_cipher"] or "") != th_cipher:
                row = None

    # 2) seller_sku 候选（含 99/66 前缀变体）
    if row is None:
        row = _find_th_by_seller_candidates(candidates_q)

    # 3) 末四位对齐
    if row is None and tail:
        row = _find_th_by_tail4(tail)

    if not row:
        # 目录里有同末四位但无 TH/THB
        others = conn.execute(
            "SELECT seller_sku, currency FROM products WHERE seller_sku IS NOT NULL AND seller_sku != ''"
        ).fetchall()
        hit_other = [r for r in others if seller_sku_tail4(str(r["seller_sku"] or "")) == tail]
        if hit_other:
            currencies = sorted({str(r["currency"] or "?") for r in hit_other})
            conn.close()
            return {
                "sku_id": "",
                "seller_sku": str(hit_other[0]["seller_sku"] or tail),
                "product_id": "",
                "product_name": "",
                "sku_name": "",
                "image_url": "",
                "db_price": 0.0,
                "currency": ",".join(currencies),
                "shop_cipher": "",
                "cost_cny": None,
                "is_th_listing": False,
                "other_region_only": True,
                "sku_tail4": tail,
            }
        conn.close()
        return None

    cost_cny = _pick_cost(str(row["sku_id"]))
    if cost_cny is None:
        cost_cny = _cost_by_tail(seller_sku_tail4(str(row["seller_sku"] or "")) or tail)

    is_th = (th_cipher and str(row["shop_cipher"] or "") == th_cipher) or str(row["currency"] or "") == "THB"
    out = {
        "sku_id": str(row["sku_id"] or ""),
        "seller_sku": str(row["seller_sku"] or ""),
        "product_id": str(row["product_id"] or ""),
        "product_name": row["product_name"] or "",
        "sku_name": row["sku_name"] or "",
        "image_url": row["image_url"] or "",
        "db_price": float(row["price"] or 0),
        "currency": row["currency"] or "THB",
        "shop_cipher": row["shop_cipher"] or "",
        "cost_cny": cost_cny,
        "is_th_listing": bool(is_th),
        "other_region_only": False,
        "sku_tail4": seller_sku_tail4(str(row["seller_sku"] or "")) or tail,
    }
    conn.close()
    return out


def _th_shop(access_token: str) -> dict[str, Any]:
    shops = list_shops(access_token)
    for s in shops:
        if s.get("region") == REGION and (s.get("name") or "") == SHOP_NAME:
            return s
    for s in shops:
        if s.get("region") == REGION:
            return s
    raise RuntimeError("未找到 TikTok TH 店铺")


def fetch_live_price_and_weight(product: dict[str, Any]) -> dict[str, Any]:
    """Product API 当前售价 + 重量；失败回退 DB。"""
    out: dict[str, Any] = {
        "sale_local": float(product.get("db_price") or 0),
        "list_price_local": float(product.get("db_price") or 0),
        "weight_kg": None,
        "price_source": "db",
        "weight_source": "missing",
        "warnings": [],
    }
    try:
        tok = auth.ensure_valid_token()["access_token"]
        shop = _th_shop(tok)
        cipher = shop["cipher"]
        pid = product.get("product_id")
        if pid:
            r = api_get(
                f"/product/202309/products/{pid}",
                tok,
                {"shop_cipher": cipher},
                debug=False,
            )
            data = r.get("data") or {}
            pw = data.get("package_weight") or {}
            if pw.get("value"):
                out["weight_kg"] = float(pw["value"])
                out["weight_source"] = "product_api"
            skus = data.get("skus") or []
            for sku in skus:
                if str(sku.get("id") or "") != product["sku_id"]:
                    continue
                price_obj = sku.get("price") or {}
                sale = price_obj.get("sale_price")
                if sale not in (None, ""):
                    out["sale_local"] = float(sale)
                    out["list_price_local"] = float(sale)
                    out["price_source"] = "product_api"
                break
    except Exception as exc:  # noqa: BLE001
        out["warnings"].append(f"拉现价失败，用目录价: {exc}")

    if not out["weight_kg"]:
        out["weight_kg"] = 0.5
        out["weight_source"] = "default_0.5kg"
        out["warnings"].append("缺少包裹重量，暂用 0.5kg")
    return out


def load_csv_comps(sku_id: str, cost_cny: float, fx: float, ad_rate: float) -> list[dict[str, Any]]:
    by_oid: dict[str, dict[str, Any]] = {}
    if not INCOME_DIR.is_dir():
        return []
    paths = sorted(INCOME_DIR.glob("income_TH_*.csv"))
    paths = [p for p in paths if "probe" not in p.name and "manual" not in p.name] or paths
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if (row.get("Type ") or "").strip() != "Order":
                    continue
                if str(row.get("SKU ID") or "").strip() != sku_id:
                    continue
                oid = (row.get("Order/adjustment ID  ") or "").strip()
                if not oid:
                    continue
                sale = _fnum(row.get("Subtotal after seller discounts"))
                settle = _fnum(row.get("Total settlement amount"))
                ship = abs(_fnum(row.get("Actual shipping fee")))
                cust = _fnum(row.get("Customer shipping fee"))
                rec = enrich_comp(
                    order_id=oid,
                    statement_date=row.get("Statement Date") or "",
                    sale_local=sale,
                    settlement_local=settle,
                    cost_cny=cost_cny,
                    fx=fx,
                    ad_rate=ad_rate,
                    commission_local=_fnum(row.get("TikTok Shop commission fee")),
                    affiliate_local=_fnum(row.get("Affiliate Commission")),
                    ship_net_local=ship - cust,
                    source=f"csv:{path.name}",
                )
                prev = by_oid.get(oid)
                if not prev or (rec["statement_date"] >= prev["statement_date"]):
                    by_oid[oid] = rec
    comps = mark_outliers(list(by_oid.values()))
    comps.sort(key=lambda c: c.get("statement_date") or "", reverse=True)
    return comps


def filter_lookback(comps: list[dict[str, Any]], days: int = DEFAULT_LOOKBACK_DAYS) -> list[dict[str, Any]]:
    if not comps:
        return []
    cutoff = time.time() - days * 86400
    out = []
    for c in comps:
        ds = (c.get("statement_date") or "").replace("-", "/")
        try:
            dt = datetime.strptime(ds[:10], "%Y/%m/%d").replace(tzinfo=timezone.utc)
            if dt.timestamp() >= cutoff:
                out.append(c)
        except ValueError:
            out.append(c)
    return out or comps[:40]


def _priors_for_sale(sale: float, cost: float, weight: float, fx: float, ad_rate: float):
    bd = tk_prior_breakdown(
        sale_local=sale, cost_cny=cost, weight_kg=weight, fx_cny_per_local=fx, ad_rate=ad_rate
    )
    prior_with = prior_profit_from_breakdown(bd, include_creator=True, include_tax=True, fx=fx)
    prior_no = prior_profit_from_breakdown(bd, include_creator=False, include_tax=False, fx=fx)
    return bd, prior_with, prior_no


def estimate(
    sku_query: str,
    *,
    ad_rate: float = DEFAULT_AD_RATE,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    sale_override: float | None = None,
    cost_override: float | None = None,
    force_fx_refresh: bool = False,
) -> dict[str, Any]:
    product = resolve_product(sku_query)
    if not product:
        return {"ok": False, "error": f"未找到 TikTok TH 上架 SKU: {sku_query}", "platform": "tiktok", "region": REGION}
    if not product.get("is_th_listing"):
        extra = ""
        if product.get("other_region_only"):
            extra = f"；目录仅有 {product.get('currency')} 站点记录"
        return {
            "ok": False,
            "error": (
                f"SKU {product.get('seller_sku') or sku_query} 无 TH LivelyHive 上架价"
                f"{extra}"
            ),
            "platform": "tiktok",
            "region": REGION,
            "product": product,
        }
    if cost_override is not None and float(cost_override) > 0:
        product = {**product, "cost_cny": float(cost_override), "cost_source": "manual_override"}
    elif product.get("cost_cny") is not None:
        product = {**product, "cost_source": product.get("cost_source") or "sku_costs"}
    if product.get("cost_cny") is None:
        return {
            "ok": False,
            "error": f"SKU {product.get('seller_sku')} 缺少货本（sku_costs，可填手动货本）",
            "platform": "tiktok",
            "region": REGION,
            "product": product,
        }

    fx_info = _live_fx(force_refresh=force_fx_refresh)
    fx = float(fx_info.get("THB") or 0)
    if fx <= 0:
        return {"ok": False, "error": "无法获取 THB 实时汇率", "platform": "tiktok"}

    live = fetch_live_price_and_weight(product)
    list_price = float(live["list_price_local"])
    cost = float(product["cost_cny"])
    weight = float(live["weight_kg"])

    comps_all = load_csv_comps(product["sku_id"], cost, fx, ad_rate)
    comps = filter_lookback(comps_all, lookback_days)
    usable_sales = [float(c["sale_local"]) for c in comps if not c.get("outlier") and c.get("sale_local")]
    recent_avg_sale = statistics.median(usable_sales) if usable_sales else None

    # 主售价：手动覆盖 > 近单中位实付（更贴近成交）> API 挂牌
    if sale_override and sale_override > 0:
        sale = float(sale_override)
        sale_basis = "manual_override"
    elif recent_avg_sale and recent_avg_sale > 0:
        sale = float(recent_avg_sale)
        sale_basis = "recent_comp_median_paid"
        if abs(sale - list_price) / max(list_price, 1) > 0.03:
            live["warnings"].append(
                f"主结论用近单实付中位 {sale:.1f} THB（挂牌 {list_price:.1f}）；下方另给挂牌价情景"
            )
    else:
        sale = list_price
        sale_basis = "list_or_api"

    # 定价预留折扣后的参考价（Treasury 35% reserve）——仅作对照
    reserved_sale = round(list_price * (1 - DISCOUNT_RESERVE_RATE), 2)

    bd, prior_with, prior_no = _priors_for_sale(sale, cost, weight, fx, ad_rate)
    scenarios = build_three_scenarios(
        sale_local=sale,
        cost_cny=cost,
        fx=fx,
        comps=comps,
        prior_with_creator={
            "profit_cny": prior_with["profit_cny"],
            "profit_local": prior_with["profit_local"],
            "margin_pct": prior_with["margin_pct"],
            "est_settlement_local": prior_with["est_settlement_local"],
        },
        prior_no_creator={
            "profit_cny": prior_no["profit_cny"],
            "profit_local": prior_no["profit_local"],
            "margin_pct": prior_no["margin_pct"],
            "est_settlement_local": prior_no["est_settlement_local"],
        },
        ad_rate=ad_rate,
    )
    usable_n = scenarios["sample_counts"]["all_usable"]
    main = pick_main_conclusion(scenarios=scenarios, usable_n=usable_n)

    # 多售价对照
    price_views = [
        reprice_scenarios(
            sale_local=sale,
            cost_cny=cost,
            fx=fx,
            comps=comps,
            prior_with={
                "profit_cny": prior_with["profit_cny"],
                "profit_local": prior_with["profit_local"],
                "margin_pct": prior_with["margin_pct"],
                "est_settlement_local": prior_with["est_settlement_local"],
            },
            prior_no={
                "profit_cny": prior_no["profit_cny"],
                "profit_local": prior_no["profit_local"],
                "margin_pct": prior_no["margin_pct"],
                "est_settlement_local": prior_no["est_settlement_local"],
            },
            ad_rate=ad_rate,
            label="主售价（用于主结论）",
        )
    ]
    if abs(list_price - sale) > 0.5:
        bd_l, pw_l, pn_l = _priors_for_sale(list_price, cost, weight, fx, ad_rate)
        price_views.append(
            reprice_scenarios(
                sale_local=list_price,
                cost_cny=cost,
                fx=fx,
                comps=comps,
                prior_with={
                    "profit_cny": pw_l["profit_cny"],
                    "profit_local": pw_l["profit_local"],
                    "margin_pct": pw_l["margin_pct"],
                    "est_settlement_local": pw_l["est_settlement_local"],
                },
                prior_no={
                    "profit_cny": pn_l["profit_cny"],
                    "profit_local": pn_l["profit_local"],
                    "margin_pct": pn_l["margin_pct"],
                    "est_settlement_local": pn_l["est_settlement_local"],
                },
                ad_rate=ad_rate,
                label="当前挂牌价",
            )
        )
    if reserved_sale > 0 and abs(reserved_sale - sale) > 0.5:
        bd_r, pw_r, pn_r = _priors_for_sale(reserved_sale, cost, weight, fx, ad_rate)
        price_views.append(
            reprice_scenarios(
                sale_local=reserved_sale,
                cost_cny=cost,
                fx=fx,
                comps=comps,
                prior_with={
                    "profit_cny": pw_r["profit_cny"],
                    "profit_local": pw_r["profit_local"],
                    "margin_pct": pw_r["margin_pct"],
                    "est_settlement_local": pw_r["est_settlement_local"],
                },
                prior_no={
                    "profit_cny": pn_r["profit_cny"],
                    "profit_local": pn_r["profit_local"],
                    "margin_pct": pn_r["margin_pct"],
                    "est_settlement_local": pn_r["est_settlement_local"],
                },
                ad_rate=ad_rate,
                label=f"定价预留折扣后（-{int(DISCOUNT_RESERVE_RATE*100)}%）",
            )
        )

    settings_fx = float((get("exchange_rates") or {}).get("THB") or 0)
    target_margin = float(SEA_TARGET_MARGIN.get(SHOP_NAME, 0.15) * 100)
    suggestions = suggest_rule_tweaks(
        model_logistics_local=bd.logistics_local,
        comps=comps,
        fx_settings=settings_fx,
        fx_live=fx,
        main_margin_pct=main.get("margin_pct"),
        target_margin_pct=target_margin,
    )

    return {
        "ok": True,
        "platform": "tiktok",
        "shop": SHOP_NAME,
        "region": REGION,
        "currency": "THB",
        "ad_rate": ad_rate,
        "ad_note": "统一按售价×22%估广告；Ads 实耗分摊为后续待办",
        "product": {
            **product,
            "sale_local": sale,
            "list_price_local": list_price,
            "recent_avg_sale_local": recent_avg_sale,
            "reserved_sale_local": reserved_sale,
            "sale_basis": sale_basis,
            "weight_kg": weight,
            "price_source": live["price_source"],
            "weight_source": live["weight_source"],
            "cost_cny": cost,
        },
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
            "rule_source": "SEA_REGION_RULES[TH] forward + ad_rate override 22%",
            "target_margin_pct": target_margin,
        },
        "posterior": {
            "lookback_days": lookback_days,
            "comps_total": len(comps_all),
            "comps_in_window": len(comps),
            "min_samples_for_posterior": MIN_POSTERIOR_SAMPLES,
            "recent_comps": comps[:20],
            "sale_stats": summarize_nums(usable_sales),
            "ship_stats": summarize_nums(
                [float(c.get("ship_net_local") or 0) for c in comps if not c.get("outlier")]
            ),
        },
        "scenarios": scenarios,
        "main": main,
        "price_views": price_views,
        "rule_suggestions": suggestions,
        "warnings": live.get("warnings") or [],
        "rule_writeback": "disabled_requires_manual_confirm",
    }


def list_hot_skus(limit: int = 30) -> list[dict[str, Any]]:
    """从结算 CSV 统计 TH 近单最多的 seller_sku（经 products 反查；优先有货本）。"""
    from collections import Counter

    init_db()
    conn = connect()
    th_cipher = ""
    try:
        tok = auth.ensure_valid_token()["access_token"]
        th_cipher = str(_th_shop(tok).get("cipher") or "")
    except Exception:
        th_cipher = ""

    sku_counts: Counter[str] = Counter()
    if INCOME_DIR.is_dir():
        paths = sorted(INCOME_DIR.glob("income_TH_*.csv"), reverse=True)
        paths = [p for p in paths if "probe" not in p.name and "manual" not in p.name][:3]
        for path in paths:
            with path.open(encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    if (row.get("Type ") or "").strip() != "Order":
                        continue
                    sid = str(row.get("SKU ID") or "").strip()
                    if sid:
                        sku_counts[sid] += 1
    out = []
    for sku_id, n in sku_counts.most_common(limit * 5):
        if th_cipher:
            r = conn.execute(
                """
                SELECT p.seller_sku, p.product_name, p.image_url, p.price, p.currency, s.cost_cny
                FROM products p
                LEFT JOIN sku_costs s ON s.sku_id = p.sku_id
                WHERE p.sku_id = ? AND p.shop_cipher = ?
                LIMIT 1
                """,
                (sku_id, th_cipher),
            ).fetchone()
        else:
            r = conn.execute(
                """
                SELECT p.seller_sku, p.product_name, p.image_url, p.price, p.currency, s.cost_cny
                FROM products p
                LEFT JOIN sku_costs s ON s.sku_id = p.sku_id
                WHERE p.sku_id = ? AND p.currency = 'THB'
                LIMIT 1
                """,
                (sku_id,),
            ).fetchone()
        if not r or not r["seller_sku"]:
            continue
        cost = r["cost_cny"]
        if cost is None:
            # 同末四位货本可复用
            hit_rows = conn.execute(
                """
                SELECT p.seller_sku, s.cost_cny FROM products p
                JOIN sku_costs s ON s.sku_id = p.sku_id AND s.cost_cny > 0
                """
            ).fetchall()
            tail = seller_sku_tail4(str(r["seller_sku"] or ""))
            for h in hit_rows:
                if seller_sku_tail4(str(h["seller_sku"] or "")) == tail:
                    cost = float(h["cost_cny"])
                    break
        if cost is None:
            continue  # 热销快捷只展示可估利润的 SKU
        out.append(
            {
                "seller_sku": r["seller_sku"],
                "sku_id": sku_id,
                "product_name": (r["product_name"] or "")[:80],
                "image_url": r["image_url"] or "",
                "price": r["price"],
                "cost_cny": float(cost),
                "order_lines": n,
                "platform": "tiktok",
            }
        )
        if len(out) >= limit:
            break
    conn.close()
    return out
