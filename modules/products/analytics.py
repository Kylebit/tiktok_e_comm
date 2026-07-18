"""TikTok Shop Analytics：拉取商品表现、计算 CTR 分段与 GPM。"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta, timezone

from core import auth, shops
from core.api_client import get as api_get
from core.config import get
from core.db import connect, init_db

PERF_PATH = "/analytics/202405/shop_products/performance"
PERF_DETAIL_PATH = "/analytics/202405/shop_products/{product_id}/performance"

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
        # MY 实测近 30 天 GPM 中位约 21 RM；默认取 20 作为「较好」门槛
        "good_gpm_threshold": float(cfg.get("good_gpm_threshold", 20.0)),
    }


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def compute_gpm(gmv: float, views: float) -> float:
    """GPM = GMV / views * 1000（当地币 / 千次曝光）。"""
    gmv_v = _f(gmv)
    views_v = _f(views)
    if views_v <= 0:
        return 0.0
    return gmv_v / views_v * 1000.0


def _migrate(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS product_analytics (
            product_id TEXT NOT NULL,
            shop_cipher TEXT NOT NULL,
            region TEXT,
            orders INTEGER DEFAULT 0,
            units_sold INTEGER DEFAULT 0,
            gmv REAL DEFAULT 0,
            views REAL DEFAULT 0,
            gpm REAL DEFAULT 0,
            click_through_rate REAL DEFAULT 0,
            ctr_median REAL DEFAULT 0,
            segment TEXT,
            window_days INTEGER DEFAULT 28,
            synced_at INTEGER,
            PRIMARY KEY (product_id, shop_cipher)
        )"""
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(product_analytics)")}
    for name, ddl in (
        ("views", "ALTER TABLE product_analytics ADD COLUMN views REAL DEFAULT 0"),
        ("gpm", "ALTER TABLE product_analytics ADD COLUMN gpm REAL DEFAULT 0"),
    ):
        if name not in cols:
            conn.execute(ddl)


def _gmv_amount(product: dict) -> float:
    gmv_obj = product.get("gmv") or {}
    if isinstance(gmv_obj, dict):
        return _f(gmv_obj.get("amount"))
    return _f(gmv_obj)


def _views_from_payload(payload: dict) -> float:
    """优先原生 views；列表接口常缺省，详情接口用 impressions（曝光）或 page_views。"""
    for key in ("views", "impressions", "page_views"):
        if payload.get(key) is not None and _f(payload.get(key)) > 0:
            return _f(payload.get(key))
    return 0.0


def fetch_product_views(
    token: str,
    cipher: str,
    product_id: str,
    *,
    start_date: str,
    end_date: str,
) -> float:
    """单品 performance 详情：取 impressions（曝光）作为 views。"""
    path = PERF_DETAIL_PATH.format(product_id=product_id)
    r = api_get(
        path,
        token,
        {
            "shop_cipher": cipher,
            "start_date_ge": start_date,
            "end_date_lt": end_date,
            "currency": "LOCAL",
        },
    )
    if r.get("code") != 0:
        raise RuntimeError(r.get("message", f"Analytics 单品失败 product_id={product_id}"))
    data = r.get("data") or {}
    intervals = (data.get("performance") or {}).get("intervals") or []
    if not intervals:
        return _views_from_payload(data)
    # 区间汇总：通常仅一段 ALL
    total = 0.0
    for iv in intervals:
        v = _views_from_payload(iv)
        if v > 0:
            total += v
    return total


def fetch_shop_performance(
    token: str,
    cipher: str,
    days: int | None = None,
    *,
    enrich_views: bool = False,
    quiet: bool = True,
) -> list[dict]:
    """拉取单店全部商品 analytics（分页）。

    列表接口通常返回 click_through_rate/gmv/orders/units_sold；
    views（曝光）若缺失且 enrich_views=True，则逐条补拉单品详情的 impressions。
    """
    cfg = _analytics_cfg()
    window = days if days is not None else cfg["window_days"]
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=window)
    start_s, end_s = start.isoformat(), end.isoformat()
    products: list[dict] = []
    page_token = ""
    while True:
        qp: dict[str, str] = {
            "shop_cipher": cipher,
            "start_date_ge": start_s,
            "end_date_lt": end_s,
            "page_size": "100",
            "sort_field": "click_through_rate",
            "sort_order": "DESC",
            "currency": "LOCAL",
        }
        if page_token:
            qp["page_token"] = page_token
        r = api_get(PERF_PATH, token, qp)
        if r.get("code") != 0:
            raise RuntimeError(r.get("message", "Analytics 拉取失败"))
        data = r.get("data") or {}
        batch = data.get("products") or []
        for p in batch:
            views = _views_from_payload(p)
            if views:
                p["views"] = views
            p["_gmv_amount"] = _gmv_amount(p)
        products.extend(batch)
        page_token = data.get("next_page_token") or ""
        if not page_token or not batch:
            break
        time.sleep(0.15)

    if enrich_views:
        need = [p for p in products if _views_from_payload(p) <= 0 and p.get("id")]
        if not quiet:
            print(f"  补拉曝光 views {len(need)}/{len(products)}…", flush=True)
        for i, p in enumerate(need, 1):
            pid = str(p.get("id") or "")
            try:
                views = fetch_product_views(
                    token, cipher, pid, start_date=start_s, end_date=end_s
                )
                p["views"] = views
            except Exception as exc:
                p["views"] = 0.0
                p["_views_error"] = str(exc)
            if not quiet and (i % 25 == 0 or i == len(need)):
                print(f"    views {i}/{len(need)}", flush=True)
            time.sleep(0.12)

    for p in products:
        views = _views_from_payload(p)
        p["views"] = views
        p["gpm"] = compute_gpm(_gmv_amount(p), views)
    return products


def _median_ctr(products: list[dict]) -> float:
    ctrs = [
        _f(p.get("click_through_rate"))
        for p in products
        if _f(p.get("click_through_rate")) > 0
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


def is_ctr_gpm_boost_candidate(
    ctr: float,
    gpm: float,
    median_ctr: float,
    *,
    high_mult: float | None = None,
    gpm_threshold: float | None = None,
) -> bool:
    """CTR ≥ 中位×high_ctr_multiplier 且 GPM ≥ good_gpm_threshold。"""
    cfg = _analytics_cfg()
    high_mult = high_mult if high_mult is not None else cfg["high_ctr_multiplier"]
    gpm_threshold = (
        gpm_threshold if gpm_threshold is not None else cfg["good_gpm_threshold"]
    )
    if median_ctr <= 0:
        return False
    return _f(ctr) >= median_ctr * high_mult and _f(gpm) >= gpm_threshold


def filter_ctr_gpm_candidates(
    products: list[dict],
    *,
    median_ctr: float | None = None,
    high_mult: float | None = None,
    gpm_threshold: float | None = None,
    allow_relaxed_ctr: bool = True,
) -> list[dict]:
    """从已带 click_through_rate / gpm / views 的商品列表筛双优候选。

    主规则：CTR ≥ 中位×high_mult 且 GPM ≥ 阈值。
    若主规则为空且 allow_relaxed_ctr：回退 CTR ≥ 中位 且 GPM ≥ 阈值
    （高 CTR×1.5 商品常 0 单导致 GPM=0，严格交集可能为空）。
    """
    cfg = _analytics_cfg()
    med = _median_ctr(products) if median_ctr is None else float(median_ctr)
    high_mult = high_mult if high_mult is not None else cfg["high_ctr_multiplier"]
    gpm_threshold = (
        gpm_threshold if gpm_threshold is not None else cfg["good_gpm_threshold"]
    )

    def _pack(p: dict, *, tier: str) -> dict:
        ctr = _f(p.get("click_through_rate"))
        gpm = _f(p.get("gpm"))
        if gpm <= 0 and p.get("views") is not None:
            gpm = compute_gpm(_gmv_amount(p), p.get("views"))
        row = dict(p)
        row["click_through_rate"] = ctr
        row["gpm"] = gpm
        row["views"] = _f(p.get("views"))
        row["gmv"] = _gmv_amount(p)
        row["ctr_median"] = med
        row["filter_tier"] = tier
        return row

    out = [
        _pack(p, tier="strict")
        for p in products
        if is_ctr_gpm_boost_candidate(
            _f(p.get("click_through_rate")),
            _f(p.get("gpm"))
            or compute_gpm(_gmv_amount(p), p.get("views")),
            med,
            high_mult=high_mult,
            gpm_threshold=gpm_threshold,
        )
    ]
    if not out and allow_relaxed_ctr and med > 0:
        out = [
            _pack(p, tier="relaxed_ctr")
            for p in products
            if _f(p.get("click_through_rate")) >= med
            and (
                _f(p.get("gpm"))
                or compute_gpm(_gmv_amount(p), p.get("views"))
            )
            >= gpm_threshold
        ]
    out.sort(key=lambda r: (-_f(r.get("gpm")), -_f(r.get("click_through_rate"))))
    return out


def _upsert_analytics_row(
    conn,
    *,
    product_id: str,
    cipher: str,
    region: str,
    orders: int,
    units_sold: int,
    gmv: float,
    views: float,
    gpm: float,
    ctr: float,
    median: float,
    segment: str,
    window: int,
    now: int,
) -> None:
    conn.execute(
        """INSERT INTO product_analytics (
            product_id, shop_cipher, region, orders, units_sold, gmv, views, gpm,
            click_through_rate, ctr_median, segment, window_days, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_id, shop_cipher) DO UPDATE SET
            region=excluded.region,
            orders=excluded.orders,
            units_sold=excluded.units_sold,
            gmv=excluded.gmv,
            views=excluded.views,
            gpm=excluded.gpm,
            click_through_rate=excluded.click_through_rate,
            ctr_median=excluded.ctr_median,
            segment=excluded.segment,
            window_days=excluded.window_days,
            synced_at=excluded.synced_at""",
        (
            product_id,
            cipher,
            region,
            orders,
            units_sold,
            gmv,
            views,
            gpm,
            ctr,
            median,
            segment,
            window,
            now,
        ),
    )


def sync_all(
    region: str | None = None,
    quiet: bool = False,
    *,
    days: int | None = None,
    enrich_views: bool | None = None,
) -> dict:
    """拉取各站 analytics 并写入 product_analytics。"""
    init_db()
    conn = connect()
    _migrate(conn)
    cfg = _analytics_cfg()
    window = days if days is not None else cfg["window_days"]
    # MY 选品需要曝光；其它区默认不补拉以免过慢
    if enrich_views is None:
        enrich_views = bool(region and region.upper() == "MY")
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
            prods = fetch_shop_performance(
                token,
                cipher,
                days=window,
                enrich_views=enrich_views,
                quiet=quiet,
            )
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
            ctr = _f(p.get("click_through_rate"))
            gmv = _gmv_amount(p)
            views = _f(p.get("views"))
            gpm = _f(p.get("gpm")) or compute_gpm(gmv, views)
            seg = classify_segment(orders, ctr, median)
            if seg:
                by_segment[seg] = by_segment.get(seg, 0) + 1
            _upsert_analytics_row(
                conn,
                product_id=pid,
                cipher=cipher,
                region=reg,
                orders=orders,
                units_sold=int(p.get("units_sold") or 0),
                gmv=gmv,
                views=views,
                gpm=gpm,
                ctr=ctr,
                median=median,
                segment=seg or "",
                window=window,
                now=now,
            )
            total += 1
        time.sleep(0.1)

    conn.commit()
    conn.close()
    return {
        "total": total,
        "by_segment": by_segment,
        "window_days": window,
        "enrich_views": bool(enrich_views),
    }


def _product_catalog_map(cipher: str) -> dict[str, dict]:
    """product_id → 主图/名/seller_sku/sku_ids（对齐 affiliate_invites）。"""
    init_db()
    conn = connect()
    rows = conn.execute(
        """SELECT product_id, sku_id, seller_sku, product_name, image_url, price, currency
           FROM products WHERE shop_cipher = ? AND product_id IS NOT NULL""",
        (cipher,),
    ).fetchall()
    conn.close()
    out: dict[str, dict] = {}
    for r in rows:
        pid = str(r["product_id"] or "")
        if not pid:
            continue
        rec = out.setdefault(
            pid,
            {
                "product_id": pid,
                "product_name": r["product_name"] or "",
                "image_url": r["image_url"] or "",
                "seller_sku": r["seller_sku"] or "",
                "sku_ids": [],
                "price": float(r["price"] or 0),
                "currency": r["currency"] or "MYR",
            },
        )
        sku = str(r["sku_id"] or "")
        if sku and sku not in rec["sku_ids"]:
            rec["sku_ids"].append(sku)
        if not rec["seller_sku"] and r["seller_sku"]:
            rec["seller_sku"] = r["seller_sku"]
        if not rec["image_url"] and r["image_url"]:
            rec["image_url"] = r["image_url"]
        if not rec["product_name"] and r["product_name"]:
            rec["product_name"] = r["product_name"]
    return out


def build_my_boost_payload(
    *,
    days: int = 30,
    quiet: bool = False,
) -> dict:
    """拉取 LivelyHive MY 近 N 天表现，筛 CTR×GPM 双优，组装报告数据。"""
    cfg = _analytics_cfg()
    token = auth.access_token()
    my_shops = [
        s for s in shops.list_shops(token) if (s.get("region") or "").upper() == "MY"
    ]
    if not my_shops:
        raise RuntimeError("未找到 region=MY 的店铺（请确认 LivelyHive token）")
    shop = my_shops[0]
    cipher = shop.get("cipher") or shop.get("shop_cipher") or ""
    shop_name = shop.get("name") or shop.get("shop_name") or "LivelyHive"
    if not cipher:
        raise RuntimeError("MY 店铺缺少 shop_cipher")

    if not quiet:
        print(f"  MY {shop_name} 拉取 {days} 天 analytics（含 views）…", flush=True)
    prods = fetch_shop_performance(
        token, cipher, days=days, enrich_views=True, quiet=quiet
    )
    median = _median_ctr(prods)
    gpm_threshold = cfg["good_gpm_threshold"]
    high_mult = cfg["high_ctr_multiplier"]
    candidates_raw = filter_ctr_gpm_candidates(
        prods,
        median_ctr=median,
        high_mult=high_mult,
        gpm_threshold=gpm_threshold,
        allow_relaxed_ctr=True,
    )
    filter_tier = (
        str(candidates_raw[0].get("filter_tier") or "strict")
        if candidates_raw
        else "strict"
    )

    # 落库
    init_db()
    conn = connect()
    _migrate(conn)
    now = int(time.time())
    for p in prods:
        pid = str(p.get("id") or "")
        if not pid:
            continue
        gmv = _gmv_amount(p)
        views = _f(p.get("views"))
        gpm = _f(p.get("gpm")) or compute_gpm(gmv, views)
        ctr = _f(p.get("click_through_rate"))
        seg = classify_segment(int(p.get("orders") or 0), ctr, median) or ""
        _upsert_analytics_row(
            conn,
            product_id=pid,
            cipher=cipher,
            region="MY",
            orders=int(p.get("orders") or 0),
            units_sold=int(p.get("units_sold") or 0),
            gmv=gmv,
            views=views,
            gpm=gpm,
            ctr=ctr,
            median=median,
            segment=seg,
            window=days,
            now=now,
        )
    conn.commit()
    conn.close()

    catalog = _product_catalog_map(cipher)
    commission = float(get("affiliate.default_commission_rate_pct", 15))
    creator_dir = str(get("affiliate.creator_list_dir", "data/creator_lists"))

    candidates: list[dict] = []
    for p in candidates_raw:
        pid = str(p.get("id") or "")
        cat = catalog.get(pid) or {}
        sku_ids = list(cat.get("sku_ids") or [])
        primary_sku = sku_ids[0] if sku_ids else ""
        candidates.append(
            {
                "product_id": pid,
                "sku_id": primary_sku,
                "sku_ids": sku_ids,
                "seller_sku": cat.get("seller_sku") or "",
                "product_name": cat.get("product_name") or "",
                "image_url": cat.get("image_url") or "",
                "click_through_rate": round(_f(p.get("click_through_rate")), 4),
                "gpm": round(_f(p.get("gpm")), 4),
                "views": round(_f(p.get("views")), 2),
                "gmv": round(_f(p.get("gmv")), 2),
                "orders": int(p.get("orders") or 0),
                "units_sold": int(p.get("units_sold") or 0),
                "suggested_commission_pct": commission,
                "shop_cipher": cipher,
                "region": "MY",
                "creator_list_dir": creator_dir,
                "filter_tier": p.get("filter_tier") or filter_tier,
            }
        )

    gpms_all = [
        compute_gpm(_gmv_amount(p), p.get("views"))
        for p in prods
        if _f(p.get("views")) > 0
    ]
    return {
        "scan_time": now,
        "region": "MY",
        "shop": shop_name,
        "shop_cipher": cipher,
        "window_days": days,
        "ctr_median": round(median, 4),
        "ctr_threshold": round(median * high_mult, 4),
        "high_ctr_multiplier": high_mult,
        "good_gpm_threshold": gpm_threshold,
        "gpm_median_shop": round(statistics.median(gpms_all), 4) if gpms_all else 0.0,
        "total_products": len(prods),
        "candidate_count": len(candidates),
        "filter_tier": filter_tier,
        "filter_note": (
            f"CTR≥中位×{high_mult} 且 GPM≥{gpm_threshold}"
            if filter_tier == "strict"
            else f"严格交集为空，已回退：CTR≥中位 且 GPM≥{gpm_threshold}"
        ),
        "suggested_commission_pct": commission,
        "creator_list_dir": creator_dir,
        "affiliate_align": {
            "table": "affiliate_invites",
            "keys": ["product_id", "sku_id"],
            "creator_lists": creator_dir,
        },
        "candidates": candidates,
    }


def run_my_ctr_gpm_boost(*, days: int = 30, quiet: bool = False) -> dict:
    """生成 MY CTR/GPM 候选清单（HTML/JSON/CSV）并返回路径摘要。"""
    from modules.products import build_page

    payload = build_my_boost_payload(days=days, quiet=quiet)
    paths = build_page.build_livelyhive_my_boost_report(payload)
    if not quiet:
        print(
            f"  候选 {payload['candidate_count']}/{payload['total_products']} · "
            f"CTR中位 {payload['ctr_median']} · 门槛 CTR≥{payload['ctr_threshold']} "
            f"GPM≥{payload['good_gpm_threshold']}",
            flush=True,
        )
        for k, v in paths.items():
            print(f"  {k}: {v}", flush=True)
    return {"payload": payload, "paths": paths}


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
    out["good_gpm_threshold"] = cfg["good_gpm_threshold"]
    return out
