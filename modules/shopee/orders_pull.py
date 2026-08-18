# -*- coding: utf-8 -*-
"""Shopee 已拨款订单拉取（payment escrow API）→ 本地周报兼容快照。

数据源：
  GET /api/v2/payment/get_escrow_list
  GET /api/v2/payment/get_escrow_detail

写出：
  outputs/weekly_shopee_profit_{YYYYMMDD}_{YYYYMMDD}.html  （供 billing / sku_profit 复用）
  outputs/shopee_escrow_{REGION}_{YYYYMMDD}_{YYYYMMDD}.json （原始规范化行）
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from core.config import ROOT, get
from core.db import connect, init_db
from modules.finance.sku_key import seller_sku_tail4
from modules.shopee.auth import ensure_shop_token
from modules.shopee.client import shop_get
from modules.shopee.shops import sync_shop_ids

OUTPUT_DIR = ROOT / "outputs"
CHUNK_DAYS = 14  # escrow_list 单次窗口保守按 14 天切
DETAIL_SLEEP_SEC = 0.05


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _fmt_ts(ts: int | float | None) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return ""


def _cost_by_seller_sku(seller_sku: str) -> float | None:
    if not seller_sku:
        return None
    init_db()
    conn = connect()
    tail = seller_sku_tail4(seller_sku)
    rows = conn.execute(
        """
        SELECT p.seller_sku, s.cost_cny
        FROM products p
        JOIN sku_costs s ON s.sku_id = p.sku_id AND s.cost_cny > 0
        ORDER BY s.updated_at DESC
        """
    ).fetchall()
    conn.close()
    for row in rows:
        if seller_sku_tail4(str(row["seller_sku"] or "")) == tail:
            return float(row["cost_cny"])
    return None


def _shop_ctx(region: str) -> tuple[int, str]:
    reg = (region or "TH").upper()
    shops = sync_shop_ids()
    if reg not in shops:
        raise RuntimeError(f"Shopee 未配置 {reg} 主店（sync_shop_ids）")
    shop_id = int(shops[reg])
    return shop_id, ensure_shop_token(shop_id)


def iter_escrow_list(
    shop_id: int,
    token: str,
    *,
    time_from: int,
    time_to: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = 1
    while True:
        resp = shop_get(
            "/api/v2/payment/get_escrow_list",
            shop_id,
            token,
            {
                "release_time_from": int(time_from),
                "release_time_to": int(time_to),
                "page_size": 100,
                "page_no": page,
            },
        )
        if resp.get("error"):
            raise RuntimeError(resp.get("message") or resp.get("error") or str(resp))
        body = resp.get("response") or {}
        batch = body.get("escrow_list") or []
        out.extend(batch)
        if not body.get("more"):
            break
        page += 1
        if page > 200:
            break
    return out


def fetch_escrow_detail(shop_id: int, token: str, order_sn: str) -> dict[str, Any]:
    resp = shop_get(
        "/api/v2/payment/get_escrow_detail",
        shop_id,
        token,
        {"order_sn": order_sn},
    )
    if resp.get("error"):
        raise RuntimeError(resp.get("message") or resp.get("error") or str(resp))
    return resp.get("response") or {}


def _normalize_detail(
    *,
    region: str,
    currency: str,
    list_row: dict[str, Any],
    detail: dict[str, Any],
) -> list[dict[str, Any]]:
    """一单可能多 SKU → 多行；结算金额按 discounted_price 比例分摊。"""
    oi = detail.get("order_income") or {}
    settlement = _f(oi.get("escrow_amount_after_adjustment") or oi.get("escrow_amount"))
    if settlement == 0:
        settlement = _f(list_row.get("payout_amount"))
    release_ts = list_row.get("escrow_release_time")
    release_str = _fmt_ts(release_ts)
    order_sn = str(detail.get("order_sn") or list_row.get("order_sn") or "")
    items = oi.get("items") or []
    if not items:
        items = [
            {
                "model_sku": "",
                "item_name": "",
                "discounted_price": _f(oi.get("buyer_total_amount")),
                "selling_price": _f(oi.get("buyer_total_amount")),
                "quantity_purchased": 1,
            }
        ]

    weights = []
    for it in items:
        w = _f(it.get("discounted_price") or it.get("selling_price")) * max(
            int(it.get("quantity_purchased") or 1), 1
        )
        weights.append(max(w, 0.01))
    total_w = sum(weights) or 1.0

    rows = []
    for it, w in zip(items, weights):
        sku = str(it.get("model_sku") or it.get("item_sku") or "").strip()
        sale = _f(it.get("discounted_price") or it.get("selling_price"))
        qty = max(int(it.get("quantity_purchased") or 1), 1)
        subtotal = sale * qty if sale else w
        share = settlement * (w / total_w)
        unit_cost = _cost_by_seller_sku(sku)
        product_cost = unit_cost * qty if unit_cost is not None else None
        rows.append(
            {
                "region": region,
                "currency": currency,
                "order_sn": order_sn,
                "seller_sku": sku,
                "product_name": str(it.get("item_name") or it.get("model_name") or "")[:200],
                "status": "COMPLETED",
                "sale_local": round(subtotal, 2),
                "settlement_local": round(share, 2),
                "quantity": qty,
                "release_time": release_str,
                "release_ts": int(release_ts or 0),
                # ``product_cost`` remains the backwards-compatible total
                # line cost.  New consumers should use the explicit fields.
                "unit_cost_cny": unit_cost,
                "product_cost_cny": product_cost,
                "product_cost": product_cost,
                "commission_fee": _f(oi.get("commission_fee")) * (w / total_w),
                "service_fee": _f(oi.get("service_fee")) * (w / total_w),
                "source": "shopee_api_escrow",
            }
        )
    return rows


def _headers() -> list[dict[str, str]]:
    return [
        {"name": "Purchase Date"},
        {"name": "Order SN"},
        {"name": "SKU"},
        {"name": "Product Name"},
        {"name": "Status"},
        {"name": "Currency"},
        {"name": "Quantity"},
        {"name": "Sale Price (Paid)"},
        {"name": "Settlement"},
        {"name": "Release Time"},
    ]


def _row_to_html_row(norm: dict[str, Any]) -> dict[str, Any]:
    cells = [
        norm.get("release_time") or "",
        norm.get("order_sn") or "",
        norm.get("seller_sku") or "",
        norm.get("product_name") or "",
        norm.get("status") or "COMPLETED",
        norm.get("currency") or "THB",
        norm.get("quantity") or 1,
        norm.get("sale_local") or 0,
        norm.get("settlement_local") or 0,
        norm.get("release_time") or "",
    ]
    return {
        "region": norm.get("region"),
        "currency": norm.get("currency"),
        "file": "Shopee escrow API",
        "cells": cells,
        "image_url": "",
        "quantity": norm.get("quantity") or 1,
        "unit_cost_cny": norm.get("unit_cost_cny") or 0,
        "product_cost_cny": norm.get("product_cost_cny") or norm.get("product_cost") or 0,
        # Compatibility for current report readers: this is now always the
        # total cost for the order line, never the per-unit cost.
        "product_cost": norm.get("product_cost_cny") or norm.get("product_cost") or 0,
        "ad_cost": 0,
        "subtotal": norm.get("sale_local") or 0,
        "settlement": norm.get("settlement_local") or 0,
        "revenue": norm.get("sale_local") or 0,
        "local_shipping": False,
        "cost_matched": bool(norm.get("product_cost")),
        "profit_cny": None,
        "profit_local": None,
        "margin_pct": None,
        "source": "shopee_api_escrow",
    }


def write_weekly_html(
    *,
    region: str,
    start: date,
    end: date,
    norms: list[dict[str, Any]],
) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rates = dict(get("exchange_rates") or {})
    data = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "headers": _headers(),
        "rows": [_row_to_html_row(n) for n in norms],
        "regions": [region],
        "rates": rates,
        "adRates": {},
        "localShippingFeeCny": {},
        "exchangeRateSource": "settings",
        "source": "shopee_api_escrow",
        "pulled_at": datetime.now(timezone.utc).isoformat(),
    }
    name = f"weekly_shopee_profit_{_ymd(start)}_{_ymd(end)}.html"
    path = OUTPUT_DIR / name
    # 仅嵌入 DATA，billing/sku_profit 只读 const DATA
    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'/>"
        f"<title>Shopee escrow {region} {start}~{end}</title></head><body>"
        f"<h1>Shopee {region} escrow API snapshot</h1>"
        f"<p>orders={len(norms)} · pulled via get_escrow_list/detail</p>"
        f"<script>\nconst DATA = {json.dumps(data, ensure_ascii=False)};\n</script>"
        "</body></html>"
    )
    path.write_text(html, encoding="utf-8")
    return path


def write_json_snapshot(
    *,
    region: str,
    start: date,
    end: date,
    norms: list[dict[str, Any]],
) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"shopee_escrow_{region}_{_ymd(start)}_{_ymd(end)}.json"
    payload = {
        "region": region,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "pulled_at": datetime.now(timezone.utc).isoformat(),
        "count": len(norms),
        "rows": norms,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def pull_region(
    region: str = "TH",
    *,
    start: date | None = None,
    end: date | None = None,
    lookback_days: int = 14,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    """按拨款释放时间拉取已结订单。"""
    end = end or datetime.now(timezone.utc).date()
    start = start or (end - timedelta(days=max(int(lookback_days), 1) - 1))
    if end < start:
        raise ValueError("结束日期不能早于开始日期")

    shop_id, token = _shop_ctx(region)
    currency = {"TH": "THB", "MY": "MYR", "PH": "PHP", "VN": "VND"}.get(region.upper(), "THB")

    # 切窗拉取 escrow_list
    list_rows: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=CHUNK_DAYS - 1), end)
        t0 = int(datetime(cursor.year, cursor.month, cursor.day, tzinfo=timezone.utc).timestamp())
        t1 = int(
            (
                datetime(chunk_end.year, chunk_end.month, chunk_end.day, tzinfo=timezone.utc)
                + timedelta(days=1)
            ).timestamp()
        )
        batch = iter_escrow_list(shop_id, token, time_from=t0, time_to=t1)
        list_rows.extend(batch)
        cursor = chunk_end + timedelta(days=1)

    # 去重 order_sn（保留最新 release）
    by_sn: dict[str, dict[str, Any]] = {}
    for row in list_rows:
        sn = str(row.get("order_sn") or "")
        if not sn:
            continue
        prev = by_sn.get(sn)
        if not prev or int(row.get("escrow_release_time") or 0) >= int(
            prev.get("escrow_release_time") or 0
        ):
            by_sn[sn] = row

    print(f"  [shopee {region}] escrow_list={len(by_sn)} orders · fetching details…", flush=True)

    norms: list[dict[str, Any]] = []
    errors: list[str] = []
    total = len(by_sn)
    for i, (sn, list_row) in enumerate(by_sn.items()):
        if on_progress:
            on_progress(sn, i + 1, total)
        if (i + 1) % 20 == 0 or i == 0:
            print(f"  [shopee {region}] detail {i + 1}/{total}", flush=True)
        try:
            detail = fetch_escrow_detail(shop_id, token, sn)
            norms.extend(
                _normalize_detail(
                    region=region.upper(),
                    currency=currency,
                    list_row=list_row,
                    detail=detail,
                )
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{sn}: {exc}")
        time.sleep(DETAIL_SLEEP_SEC)

    norms.sort(key=lambda r: r.get("release_ts") or 0, reverse=True)
    json_path = write_json_snapshot(region=region.upper(), start=start, end=end, norms=norms)
    html_path = None
    if norms:
        html_path = write_weekly_html(region=region.upper(), start=start, end=end, norms=norms)
    return {
        "ok": True,
        "platform": "shopee",
        "region": region.upper(),
        "shop_id": shop_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "escrow_orders": total,
        "line_rows": len(norms),
        "html": str(html_path) if html_path else "",
        "json": str(json_path),
        "errors": errors[:20],
        "error_count": len(errors),
    }
