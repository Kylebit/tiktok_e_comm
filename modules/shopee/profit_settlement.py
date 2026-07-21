"""Shopee profit summaries from locally saved escrow report snapshots."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from core.config import ROOT

OUTPUT_DIR = ROOT / "outputs"
REPORT_GLOB = "weekly_shopee_profit_*.html"
REGION_CURRENCY = {"PH": "PHP", "MY": "MYR", "TH": "THB", "VN": "VND"}


def parse_iso_date(value: str | None) -> date:
    if not value:
        raise ValueError("missing date")
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def _round(value: float | int | None, digits: int = 2) -> float:
    return round(float(value or 0), digits)


def _extract_data(path: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    marker = "const DATA = "
    start = text.find(marker)
    if start < 0:
        return None
    start += len(marker)
    end = text.find(";\n", start)
    if end < 0:
        end = text.find(";</script>", start)
    if end < 0:
        return None
    return json.loads(text[start:end])


def _report_meta(path: Path) -> dict[str, Any]:
    data = _extract_data(path) or {}
    rows = data.get("rows") or []
    profit_cny = sum(float(row.get("profit_cny") or 0) for row in rows)
    settlement_cny = 0.0
    gmv_cny = 0.0
    rates = data.get("rates") or {}
    regions = sorted({str(row.get("region") or "") for row in rows if row.get("region")})
    for row in rows:
        rate = float(rates.get(str(row.get("region") or ""), 0) or 0)
        settlement_cny += float(row.get("settlement") or 0) * rate
        gmv_cny += float(row.get("subtotal") or 0) * rate
    m = re.search(r"weekly_shopee_profit_(\d{8})_(\d{8})\.html$", path.name)
    start = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}" if m else data.get("start")
    end = f"{m.group(2)[:4]}-{m.group(2)[4:6]}-{m.group(2)[6:]}" if m else data.get("end")
    json_name = path.with_suffix(".feishu.json").name
    return {
        "html": path.name,
        "json": json_name if (OUTPUT_DIR / json_name).is_file() else "",
        "month": f"{start} ~ {end}",
        "release_date": f"{start} ~ {end}",
        "regions": regions,
        "order_count": len(rows),
        "gmv_cny": _round(gmv_cny),
        "settlement_cny": _round(settlement_cny),
        "profit_cny": _round(profit_cny),
        "margin_pct": _round(profit_cny / gmv_cny * 100) if gmv_cny else None,
        "mtime": int(path.stat().st_mtime),
    }


def list_reports(limit: int = 12) -> list[dict[str, Any]]:
    if not OUTPUT_DIR.is_dir():
        return []
    return [_report_meta(p) for p in sorted(OUTPUT_DIR.glob(REPORT_GLOB), reverse=True)[:limit]]


def _header_index(headers: list[dict[str, Any]], name: str) -> int:
    for i, item in enumerate(headers):
        if item.get("name") == name:
            return i
    return -1


def _row_date(row: dict[str, Any], release_idx: int, purchase_idx: int) -> date | None:
    cells = row.get("cells") or []
    raw = ""
    if release_idx >= 0 and release_idx < len(cells):
        raw = str(cells[release_idx] or "")
    if not raw and purchase_idx >= 0 and purchase_idx < len(cells):
        raw = str(cells[purchase_idx] or "")
    if not raw:
        return None
    try:
        return parse_iso_date(raw)
    except ValueError:
        return None


def _fx_payload(snapshot_rates: dict[str, Any]) -> tuple[dict[str, float], dict[str, Any]]:
    """Prefer the shared live FX feed, retaining report rates as a safe fallback."""
    fallback = {
        region: float(snapshot_rates.get(region) or 0)
        for region in REGION_CURRENCY
        if float(snapshot_rates.get(region) or 0) > 0
    }
    try:
        from modules.sourcing.fx_rates import get_exchange_rates

        source = get_exchange_rates()
        live = bool(source.get("live"))
        source_rates = source.get("rates") or {}
        rates = {
            region: float(source_rates.get(currency) or 0)
            for region, currency in REGION_CURRENCY.items()
            if float(source_rates.get(currency) or 0) > 0
        }
        if live and rates:
            return rates, {**source, "rates": rates, "live": True, "fallback": False}
        return fallback, {
            **source,
            "rates": fallback,
            "live": False,
            "stale": bool(source.get("stale")),
            "fallback": True,
            "fallback_source": "report_snapshot",
        }
    except Exception as exc:  # noqa: BLE001 - a saved report must remain viewable offline.
        return fallback, {
            "provider": "report_snapshot",
            "as_of": None,
            "rates": fallback,
            "live": False,
            "stale": False,
            "error": str(exc),
            "fallback": True,
        }
def settlement_summary(start: date, end: date) -> dict[str, Any]:
    if end < start:
        raise ValueError("end date cannot be earlier than start date")
    orders: list[dict[str, Any]] = []
    by_region: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str, str]] = set()

    for path in sorted(OUTPUT_DIR.glob(REPORT_GLOB)) if OUTPUT_DIR.is_dir() else []:
        data = _extract_data(path)
        if not data:
            continue
        headers = data.get("headers") or []
        rates, fx = _fx_payload(data.get("rates") or {})
        release_idx = _header_index(headers, "Release Time")
        purchase_idx = _header_index(headers, "Purchase Date")
        order_idx = _header_index(headers, "Order SN")
        sku_idx = _header_index(headers, "SKU")
        product_idx = _header_index(headers, "Product Name")
        status_idx = _header_index(headers, "Status")
        currency_idx = _header_index(headers, "Currency")
        sale_idx = _header_index(headers, "Sale Price (Paid)")

        for row in data.get("rows") or []:
            released = _row_date(row, release_idx, purchase_idx)
            if not released or released < start or released > end:
                continue
            cells = row.get("cells") or []
            order_sn = str(cells[order_idx] if order_idx >= 0 and order_idx < len(cells) else "")
            sku = str(cells[sku_idx] if sku_idx >= 0 and sku_idx < len(cells) else "")
            dedupe_key = (order_sn, sku, str(row.get("region") or ""))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            region = str(row.get("region") or "")
            rate = float(rates.get(region, 0) or 0)
            subtotal = float(row.get("subtotal") or 0)
            settlement = float(row.get("settlement") or 0)
            settlement_cny = settlement * rate
            gmv_cny = subtotal * rate
            ad_cny = float(row.get("ad_cost") or 0) * rate
            product_cost_cny = float(row.get("product_cost") or 0)
            profit_cny = settlement_cny - ad_cny - product_cost_cny
            rec = {
                "release_date": released.isoformat(),
                "region": region,
                "currency": row.get("currency") or (cells[currency_idx] if currency_idx >= 0 and currency_idx < len(cells) else ""),
                "order_sn": order_sn,
                "sku": sku,
                "product_name": cells[product_idx] if product_idx >= 0 and product_idx < len(cells) else "",
                "status": cells[status_idx] if status_idx >= 0 and status_idx < len(cells) else "",
                "sale_price": cells[sale_idx] if sale_idx >= 0 and sale_idx < len(cells) else row.get("revenue"),
                "subtotal": _round(subtotal),
                "settlement": _round(settlement),
                "settlement_cny": _round(settlement_cny),
                "ad_cost_cny": _round(ad_cny),
                "product_cost_cny": _round(product_cost_cny),
                "profit_cny": _round(profit_cny),
                "margin_pct": _round(profit_cny / gmv_cny * 100) if gmv_cny else None,
                "image_url": row.get("image_url") or "",
                "source_file": path.name,
            }
            orders.append(rec)
            agg = by_region.setdefault(
                region,
                {
                    "region": region,
                    "currency": rec["currency"],
                    "order_count": 0,
                    "gmv_cny": 0.0,
                    "settlement_cny": 0.0,
                    "ad_cost_cny": 0.0,
                    "product_cost_cny": 0.0,
                    "profit_cny": 0.0,
                },
            )
            agg["order_count"] += 1
            agg["gmv_cny"] += gmv_cny
            agg["settlement_cny"] += settlement_cny
            agg["ad_cost_cny"] += ad_cny
            agg["product_cost_cny"] += float(row.get("product_cost") or 0)
            agg["profit_cny"] += profit_cny

    orders.sort(key=lambda item: (item["release_date"], item["region"], item["order_sn"]))
    regions = []
    for row in sorted(by_region.values(), key=lambda item: item["region"]):
        gmv = float(row["gmv_cny"] or 0)
        row = {**row}
        for key in ("gmv_cny", "settlement_cny", "ad_cost_cny", "product_cost_cny", "profit_cny"):
            row[key] = _round(row[key])
        row["margin_pct"] = _round(row["profit_cny"] / gmv * 100) if gmv else None
        regions.append(row)

    totals = {
        "order_count": len(orders),
        "gmv_cny": _round(sum(row["gmv_cny"] for row in regions)),
        "settlement_cny": _round(sum(row["settlement_cny"] for row in regions)),
        "ad_cost_cny": _round(sum(row["ad_cost_cny"] for row in regions)),
        "product_cost_cny": _round(sum(row["product_cost_cny"] for row in regions)),
        "profit_cny": _round(sum(row["profit_cny"] for row in regions)),
    }
    totals["margin_pct"] = _round(totals["profit_cny"] / totals["gmv_cny"] * 100) if totals["gmv_cny"] else None
    return {
        "ok": True,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "source": "local_weekly_shopee_profit_snapshots",
        "summary": totals,
        "regions": regions,
        "orders": orders,
        "available_reports": list_reports(),
        "fx": fx if orders else {"provider": "report_snapshot", "rates": {}, "live": False},
    }
