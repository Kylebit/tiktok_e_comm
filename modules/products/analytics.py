"""TikTok Shop Analytics：拉取商品表现、计算 CTR 分段。"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta, timezone

from core import auth, shops
from core.api_client import get as api_get
from core.config import get
from core.db import connect, init_db

PERF_PATH = "/analytics/202405/shop_products/performance"

SEGMENT_HIGH_INTEREST = "A"  # 高 CTR、0 单 → Listing 优化
SEGMENT_LOW_EXPOSURE = "B"  # 低 CTR、0 单 → 标题/主图
SEGMENT_WEAK = "C"  # 有单但偏弱
SEGMENT_DEAD = "D"  # 低 CTR + 0 单 → 下架候选


def _analytics_cfg() -> dict:
    cfg = get("analytics") or {}
    return {
        "window_days": int(cfg.get("window_days", 28)),
        "high_ctr_multiplier": float(cfg.get("high_ctr_multiplier", 1.5)),
        "low_ctr_multiplier": float(cfg.get("low_ctr_multiplier", 0.5)),
    }


def _migrate(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS product_analytics (
            product_id TEXT NOT NULL,
            shop_cipher TEXT NOT NULL,
            region TEXT,
            orders INTEGER DEFAULT 0,
            units_sold INTEGER DEFAULT 0,
            gmv REAL DEFAULT 0,
            click_through_rate REAL DEFAULT 0,
            ctr_median REAL DEFAULT 0,
            segment TEXT,
            window_days INTEGER DEFAULT 28,
            synced_at INTEGER,
            PRIMARY KEY (product_id, shop_cipher)
        )"""
    )


def fetch_shop_performance(
    token: str,
    cipher: str,
    days: int | None = None,
) -> list[dict]:
    """拉取单店全部商品 analytics（分页）。"""
    cfg = _analytics_cfg()
    window = days if days is not None else cfg["window_days"]
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=window)
    products: list[dict] = []
    page_token = ""
    while True:
        qp: dict[str, str] = {
            "shop_cipher": cipher,
            "start_date_ge": start.isoformat(),
            "end_date_lt": end.isoformat(),
            "page_size": "100",
            "sort_field": "click_through_rate",
            "sort_order": "DESC",
        }
        if page_token:
            qp["page_token"] = page_token
        r = api_get(PERF_PATH, token, qp)
        if r.get("code") != 0:
            raise RuntimeError(r.get("message", "Analytics 拉取失败"))
        data = r.get("data") or {}
        batch = data.get("products") or []
        products.extend(batch)
        page_token = data.get("next_page_token") or ""
        if not page_token or not batch:
            break
        time.sleep(0.15)
    return products


def _median_ctr(products: list[dict]) -> float:
    ctrs = [
        float(p.get("click_through_rate") or 0)
        for p in products
        if float(p.get("click_through_rate") or 0) > 0
    ]
    if not ctrs:
        return 0.0
    return statistics.median(ctrs)


def classify_segment(
    orders: int,
    ctr: float,
    median_ctr: float,
    *,
    high_mult: float | None = None,
    low_mult: float | None = None,
) -> str | None:
    cfg = _analytics_cfg()
    high_mult = high_mult if high_mult is not None else cfg["high_ctr_multiplier"]
    low_mult = low_mult if low_mult is not None else cfg["low_ctr_multiplier"]
    if median_ctr <= 0:
        return None
    if orders == 0:
        if ctr >= median_ctr * high_mult:
            return SEGMENT_HIGH_INTEREST
        if ctr < median_ctr * low_mult:
            return SEGMENT_LOW_EXPOSURE
        if ctr < median_ctr:
            return SEGMENT_DEAD
        return None
    return SEGMENT_WEAK


def sync_all(region: str | None = None, quiet: bool = False) -> dict:
    """拉取各站 analytics 并写入 product_analytics。"""
    init_db()
    conn = connect()
    _migrate(conn)
    cfg = _analytics_cfg()
    window = cfg["window_days"]
    token = auth.access_token()
    now = int(time.time())
    total = 0
    by_segment: dict[str, int] = {}

    for shop in shops.list_shops(token):
        reg = (shop.get("region") or "").upper()
        if region and reg != region.upper():
            continue
        cipher = shop.get("cipher") or shop.get("shop_cipher", "")
        if not cipher:
            continue
        if not quiet:
            print(f"  Analytics {reg}...", end=" ", flush=True)
        try:
            prods = fetch_shop_performance(token, cipher, days=window)
        except RuntimeError as e:
            if not quiet:
                print(f"失败: {e}")
            continue
        median = _median_ctr(prods)
        if not quiet:
            print(f"{len(prods)} SKU · 中位 CTR {median:.4f}")

        for p in prods:
            pid = str(p.get("id") or "")
            if not pid:
                continue
            orders = int(p.get("orders") or 0)
            ctr = float(p.get("click_through_rate") or 0)
            gmv_obj = p.get("gmv") or {}
            gmv = float(gmv_obj.get("amount") or 0)
            seg = classify_segment(orders, ctr, median)
            if seg:
                by_segment[seg] = by_segment.get(seg, 0) + 1
            conn.execute(
                """INSERT INTO product_analytics (
                    product_id, shop_cipher, region, orders, units_sold, gmv,
                    click_through_rate, ctr_median, segment, window_days, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_id, shop_cipher) DO UPDATE SET
                    region=excluded.region,
                    orders=excluded.orders,
                    units_sold=excluded.units_sold,
                    gmv=excluded.gmv,
                    click_through_rate=excluded.click_through_rate,
                    ctr_median=excluded.ctr_median,
                    segment=excluded.segment,
                    window_days=excluded.window_days,
                    synced_at=excluded.synced_at""",
                (
                    pid,
                    cipher,
                    reg,
                    orders,
                    int(p.get("units_sold") or 0),
                    gmv,
                    ctr,
                    median,
                    seg or "",
                    window,
                    now,
                ),
            )
            total += 1
        time.sleep(0.1)

    conn.commit()
    conn.close()
    return {"total": total, "by_segment": by_segment, "window_days": window}


def load_analytics(
    segment: str | None = None,
    region: str | None = None,
    shop_cipher: str | None = None,
) -> list[dict]:
    init_db()
    conn = connect()
    _migrate(conn)
    sql = """
        SELECT a.*, p.product_name, p.image_url, p.seller_sku,
               SUM(p.stock) AS stock_total
        FROM product_analytics a
        LEFT JOIN products p ON p.product_id = a.product_id AND p.shop_cipher = a.shop_cipher
        WHERE 1=1
    """
    params: list = []
    if segment:
        sql += " AND a.segment = ?"
        params.append(segment)
    if region:
        sql += " AND a.region = ?"
        params.append(region.upper())
    if shop_cipher:
        sql += " AND a.shop_cipher = ?"
        params.append(shop_cipher)
    sql += " GROUP BY a.product_id, a.shop_cipher ORDER BY a.click_through_rate DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def summary(region: str | None = None) -> dict:
    init_db()
    conn = connect()
    _migrate(conn)
    sql = """
        SELECT segment, region, COUNT(*) AS cnt
        FROM product_analytics WHERE segment != ''
    """
    params: list = []
    if region:
        sql += " AND region = ?"
        params.append(region.upper())
    sql += " GROUP BY segment, region ORDER BY region, segment"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    out: dict = {"segments": {}, "regions": {}}
    for r in rows:
        seg = r["segment"]
        reg = r["region"]
        cnt = r["cnt"]
        out["segments"][seg] = out["segments"].get(seg, 0) + cnt
        out["regions"].setdefault(reg, {})[seg] = cnt
    cfg = _analytics_cfg()
    out["window_days"] = cfg["window_days"]
    out["high_ctr_multiplier"] = cfg["high_ctr_multiplier"]
    return out
