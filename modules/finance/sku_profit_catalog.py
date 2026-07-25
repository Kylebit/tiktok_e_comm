# -*- coding: utf-8 -*-
"""近 N 天有单的 TH SKU 全量利润估算（末四位对齐）。"""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.config import ROOT
from core.db import connect, init_db
from modules.finance.sku_key import seller_sku_tail4
from modules.finance.sku_profit_service import estimate
from modules.shopee.profit_settlement import REPORT_GLOB, _extract_data, _header_index

INCOME_DIR = ROOT / "CURSOR" / "Income_Data"
OUTPUT_DIR = ROOT / "outputs"


def _parse_tk_date(raw: str) -> date | None:
    text = (raw or "").strip().replace("-", "/")[:10]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y/%m/%d").date()
    except ValueError:
        return None


def _parse_sp_date(raw: str) -> date | None:
    text = (raw or "").strip()[:10]
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def collect_ordered_skus(lookback_days: int = 90) -> dict[str, Any]:
    """按末四位汇总近 lookback_days 有结算单的 SKU。"""
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=max(lookback_days, 1) - 1)
    init_db()
    conn = connect()

    # platform sku_id → seller_sku (prefer THB)
    sku_id_map: dict[str, str] = {}
    for r in conn.execute(
        "SELECT sku_id, seller_sku, currency FROM products WHERE seller_sku IS NOT NULL AND seller_sku != ''"
    ).fetchall():
        sid = str(r["sku_id"] or "")
        seller = str(r["seller_sku"] or "")
        if not sid or not seller:
            continue
        if sid not in sku_id_map or str(r["currency"] or "") == "THB":
            sku_id_map[sid] = seller

    tk_counts: Counter[str] = Counter()
    tk_examples: dict[str, str] = {}
    if INCOME_DIR.is_dir():
        paths = sorted(INCOME_DIR.glob("income_TH_*.csv"))
        paths = [p for p in paths if "probe" not in p.name and "manual" not in p.name] or paths
        for path in paths:
            with path.open(encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    if (row.get("Type ") or "").strip() != "Order":
                        continue
                    d = _parse_tk_date(row.get("Statement Date") or "")
                    if d and d < cutoff:
                        continue
                    sid = str(row.get("SKU ID") or "").strip()
                    if not sid:
                        continue
                    seller = sku_id_map.get(sid) or ""
                    tail = seller_sku_tail4(seller) if seller else ""
                    if not tail:
                        continue
                    tk_counts[tail] += 1
                    tk_examples.setdefault(tail, seller)

    sp_counts: Counter[str] = Counter()
    sp_examples: dict[str, str] = {}
    if OUTPUT_DIR.is_dir():
        for path in sorted(OUTPUT_DIR.glob(REPORT_GLOB)):
            data = _extract_data(path)
            if not data:
                continue
            headers = data.get("headers") or []
            sku_idx = _header_index(headers, "SKU")
            release_idx = _header_index(headers, "Release Time")
            for row in data.get("rows") or []:
                if str(row.get("region") or "") != "TH":
                    continue
                cells = row.get("cells") or []
                raw_sku = str(cells[sku_idx] if sku_idx >= 0 and sku_idx < len(cells) else "").strip()
                if not raw_sku or "," in raw_sku:
                    continue
                released = _parse_sp_date(
                    str(cells[release_idx] if release_idx >= 0 and release_idx < len(cells) else "")
                )
                if released and released < cutoff:
                    continue
                tail = seller_sku_tail4(raw_sku)
                if not tail:
                    continue
                sp_counts[tail] += 1
                sp_examples.setdefault(tail, raw_sku)

    conn.close()
    all_tails = sorted(set(tk_counts) | set(sp_counts))
    rows = []
    for tail in all_tails:
        rows.append(
            {
                "sku_tail4": tail,
                "tiktok_orders": int(tk_counts.get(tail, 0)),
                "shopee_orders": int(sp_counts.get(tail, 0)),
                "tiktok_seller_sku": tk_examples.get(tail, f"99{tail}"),
                "shopee_seller_sku": sp_examples.get(tail, tail),
                "query_sku": tk_examples.get(tail) or sp_examples.get(tail) or tail,
            }
        )
    rows.sort(key=lambda r: r["tiktok_orders"] + r["shopee_orders"], reverse=True)
    return {
        "ok": True,
        "lookback_days": lookback_days,
        "cutoff": cutoff.isoformat(),
        "count": len(rows),
        "tiktok_sku_n": len(tk_counts),
        "shopee_sku_n": len(sp_counts),
        "rows": rows,
    }


def estimate_ordered_skus(
    *,
    lookback_days: int = 90,
    platform: str = "both",
    ad_rate: float | None = 0.22,
    estimate_lookback_days: int | None = None,
) -> dict[str, Any]:
    catalog = collect_ordered_skus(lookback_days=lookback_days)
    est_lb = estimate_lookback_days if estimate_lookback_days is not None else lookback_days
    results = []
    total = len(catalog["rows"])
    for idx, item in enumerate(catalog["rows"], start=1):
        q = item["query_sku"]
        print(f"  [{idx}/{total}] estimate {q} (tail {item['sku_tail4']})…", flush=True)
        data = estimate(
            q,
            platform=platform,
            ad_rate=ad_rate,
            lookback_days=est_lb,
        )
        summary = {
            "sku_tail4": item["sku_tail4"],
            "query_sku": q,
            "order_lines_tk": item["tiktok_orders"],
            "order_lines_sp": item["shopee_orders"],
            "ok": data.get("ok"),
            "partial": data.get("partial"),
            "platforms": {},
        }
        for k, p in (data.get("platforms") or {}).items():
            m = p.get("main") or {}
            prod = p.get("product") or {}
            summary["platforms"][k] = {
                "ok": p.get("ok"),
                "error": p.get("error"),
                "seller_sku": prod.get("seller_sku"),
                "sale_local": prod.get("sale_local"),
                "cost_cny": prod.get("cost_cny"),
                "cost_source": prod.get("cost_source"),
                "profit_cny": m.get("profit_cny"),
                "margin_pct": m.get("margin_pct"),
                "label": m.get("label"),
                "confidence": m.get("confidence"),
            }
        results.append(summary)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    out_json = OUTPUT_DIR / f"sku_profit_th_{lookback_days}d_{stamp}.json"
    out_csv = OUTPUT_DIR / f"sku_profit_th_{lookback_days}d_{stamp}.csv"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "estimate_lookback_days": est_lb,
        "catalog": {
            "count": catalog["count"],
            "cutoff": catalog["cutoff"],
            "tiktok_sku_n": catalog["tiktok_sku_n"],
            "shopee_sku_n": catalog["shopee_sku_n"],
        },
        "rows": results,
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "sku_tail4",
                "query_sku",
                "tk_orders",
                "sp_orders",
                "tk_ok",
                "tk_seller_sku",
                "tk_sale",
                "tk_cost",
                "tk_profit_cny",
                "tk_margin_pct",
                "tk_label",
                "tk_error",
                "sp_ok",
                "sp_seller_sku",
                "sp_sale",
                "sp_cost",
                "sp_profit_cny",
                "sp_margin_pct",
                "sp_label",
                "sp_error",
            ]
        )
        for r in results:
            tk = r["platforms"].get("tiktok") or {}
            sp = r["platforms"].get("shopee") or {}
            w.writerow(
                [
                    r["sku_tail4"],
                    r["query_sku"],
                    r["order_lines_tk"],
                    r["order_lines_sp"],
                    tk.get("ok"),
                    tk.get("seller_sku"),
                    tk.get("sale_local"),
                    tk.get("cost_cny"),
                    tk.get("profit_cny"),
                    tk.get("margin_pct"),
                    tk.get("label"),
                    tk.get("error"),
                    sp.get("ok"),
                    sp.get("seller_sku"),
                    sp.get("sale_local"),
                    sp.get("cost_cny"),
                    sp.get("profit_cny"),
                    sp.get("margin_pct"),
                    sp.get("label"),
                    sp.get("error"),
                ]
            )

    payload["json"] = str(out_json)
    payload["csv"] = str(out_csv)
    return payload
