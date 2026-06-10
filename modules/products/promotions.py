"""促销优化：折扣调整、加入促销、限时秒杀、优惠券查看/建议。"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime

from core import auth
from core.api_client import get as api_get
from core.api_client import post as api_post
from core.api_client import put as api_put
from core.config import get
from core.db import connect, init_db
from modules.finance.profit_engine import exchange_rate_for
from modules.products import costs as cost_mod
from modules.products import sales

ACTIVITY_SEARCH = "/promotion/202309/activities/search"
COUPON_SEARCH = "/promotion/202406/coupons/search"
COUPON_API_VER = "202406"

REGION_CURRENCY = {"MY": "MYR", "VN": "VND", "TH": "THB", "PH": "PHP"}


def _promo_unique_ok(conn) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='promo_queue'"
    ).fetchone()
    if not row:
        return False
    sql = row[0] or ""
    if "UNIQUE(product_id, shop_cipher, action)" in sql:
        return True
    for idx in conn.execute("PRAGMA index_list('promo_queue')").fetchall():
        if not idx[2]:
            continue
        cols = [
            r[2]
            for r in conn.execute(f"PRAGMA index_info('{idx[1]}')").fetchall()
        ]
        if cols == ["product_id", "shop_cipher", "action"]:
            return True
    return False


def _migrate_promo_queue(conn) -> None:
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='promo_queue_old'"
    ).fetchone() and conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='promo_queue'"
    ).fetchone() and _promo_unique_ok(conn):
        conn.execute("DROP TABLE promo_queue_old")

    has_table = bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='promo_queue'"
        ).fetchone()
    )
    if has_table and not _promo_unique_ok(conn):
        conn.execute("ALTER TABLE promo_queue RENAME TO promo_queue_old")
        has_table = False

    conn.execute(
        """CREATE TABLE IF NOT EXISTS promo_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL,
            shop_cipher TEXT NOT NULL,
            activity_id TEXT NOT NULL,
            region TEXT,
            image_url TEXT,
            seller_sku TEXT,
            product_name TEXT,
            list_price REAL,
            currency TEXT,
            old_discount REAL,
            suggested_discount REAL,
            new_discount REAL,
            promo_price REAL,
            flash_price REAL,
            margin_pct REAL,
            units_sold INTEGER DEFAULT 0,
            order_count INTEGER DEFAULT 0,
            stock INTEGER DEFAULT 0,
            in_activity INTEGER DEFAULT 1,
            action TEXT DEFAULT 'adjust',
            status TEXT DEFAULT 'pending',
            error TEXT,
            created_at INTEGER,
            pushed_at INTEGER,
            UNIQUE(product_id, shop_cipher, action)
        )"""
    )

    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='promo_queue_old'"
    ).fetchone():
        old_cols = {r[1] for r in conn.execute("PRAGMA table_info('promo_queue_old')")}
        sel_flash = "flash_price" if "flash_price" in old_cols else "NULL"
        sel_action = "action" if "action" in old_cols else "'adjust'"
        conn.execute(
            f"""INSERT OR IGNORE INTO promo_queue (
                id, product_id, shop_cipher, activity_id, region, image_url, seller_sku,
                product_name, list_price, currency, old_discount, suggested_discount,
                new_discount, promo_price, flash_price, margin_pct, units_sold, order_count,
                stock, in_activity, action, status, error, created_at, pushed_at
            )
            SELECT id, product_id, shop_cipher, activity_id, region, image_url, seller_sku,
                product_name, list_price, currency, old_discount, suggested_discount,
                new_discount, promo_price, {sel_flash}, margin_pct, units_sold, order_count,
                stock, in_activity, {sel_action}, status, error, created_at, pushed_at
            FROM promo_queue_old"""
        )
        conn.execute("DROP TABLE promo_queue_old")

    cols = {row[1] for row in conn.execute("PRAGMA table_info(promo_queue)")}
    for name, ddl in (
        ("flash_price", "ALTER TABLE promo_queue ADD COLUMN flash_price REAL"),
        ("action", "ALTER TABLE promo_queue ADD COLUMN action TEXT DEFAULT 'adjust'"),
        ("click_through_rate", "ALTER TABLE promo_queue ADD COLUMN click_through_rate REAL"),
        ("ctr_median", "ALTER TABLE promo_queue ADD COLUMN ctr_median REAL"),
        ("analytics_orders", "ALTER TABLE promo_queue ADD COLUMN analytics_orders INTEGER"),
    ):
        if name not in cols:
            conn.execute(ddl)
    _migrate_promo_push_log(conn)


def _analytics_stats(conn, product_id: str, shop_cipher: str) -> dict:
    row = conn.execute(
        """SELECT click_through_rate, ctr_median, orders
           FROM product_analytics WHERE product_id = ? AND shop_cipher = ?""",
        (product_id, shop_cipher),
    ).fetchone()
    if not row:
        return {}
    return {
        "click_through_rate": float(row["click_through_rate"] or 0) or None,
        "ctr_median": float(row["ctr_median"] or 0) or None,
        "analytics_orders": int(row["orders"] or 0),
    }


def _enrich_item_stats(conn, item: dict) -> dict:
    if item.get("click_through_rate") is None:
        stats = _analytics_stats(conn, item["product_id"], item["shop_cipher"])
        for k, v in stats.items():
            if item.get(k) is None and v is not None:
                item[k] = v
    return item


def _upsert_promo_queue(conn, item: dict, now: int) -> None:
    _enrich_item_stats(conn, item)
    action = item.get("action") or "adjust"
    conn.execute(
        """DELETE FROM promo_queue
           WHERE product_id = ? AND shop_cipher = ? AND action = ?""",
        (item["product_id"], item["shop_cipher"], action),
    )
    conn.execute(
        """INSERT INTO promo_queue (
            product_id, shop_cipher, activity_id, region, image_url, seller_sku,
            product_name, list_price, currency, old_discount, suggested_discount,
            new_discount, promo_price, flash_price, margin_pct, units_sold, order_count,
            stock, in_activity, action, click_through_rate, ctr_median, analytics_orders,
            status, error, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (
            item["product_id"],
            item["shop_cipher"],
            item["activity_id"],
            item["region"],
            item["image_url"],
            item["seller_sku"],
            item["product_name"],
            item["list_price"],
            item["currency"],
            item["old_discount"],
            item["suggested_discount"],
            item["new_discount"],
            item["promo_price"],
            item.get("flash_price"),
            item["margin_pct"],
            item["units_sold"],
            item["order_count"],
            item["stock"],
            item["in_activity"],
            action,
            item.get("click_through_rate"),
            item.get("ctr_median"),
            item.get("analytics_orders"),
            (item.get("note") or "")[:200] or None,
            now,
        ),
    )


def _migrate_coupon_drafts(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS coupon_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_cipher TEXT NOT NULL,
            region TEXT,
            title TEXT,
            config_json TEXT,
            note TEXT,
            status TEXT DEFAULT 'draft',
            created_at INTEGER
        )"""
    )


def _promo_settings() -> dict:
    cfg = get("promotion", {}) or {}
    return {
        "bump_pct": float(cfg.get("bump_pct", 5)),
        "max_discount": float(cfg.get("max_discount", 40)),
        "min_margin_pct": float(cfg.get("min_margin_pct", 15)),
        "min_discount": float(cfg.get("min_discount", 5)),
        "add_initial_discount": float(cfg.get("add_initial_discount", 15)),
        "flash_discount_pct": float(cfg.get("flash_discount_pct", 20)),
        "flash_duration_hours": int(cfg.get("flash_duration_hours", 48)),
        "flash_start_delay_hours": int(cfg.get("flash_start_delay_hours", 1)),
        "coupon_min_spend_multiplier": float(cfg.get("coupon_min_spend_multiplier", 2.5)),
        "coupon_amount_ratio": float(cfg.get("coupon_amount_ratio", 0.08)),
        "push_delay_sec": float(cfg.get("push_delay_sec", 1.2)),
        "cooldown_days": int(cfg.get("cooldown_days", 15)),
    }


def _migrate_promo_push_log(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS promo_push_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL,
            shop_cipher TEXT NOT NULL,
            action TEXT,
            new_discount REAL,
            flash_price REAL,
            pushed_at INTEGER NOT NULL
        )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_promo_push_log_lookup
           ON promo_push_log(product_id, shop_cipher, pushed_at DESC)"""
    )
    if conn.execute("SELECT COUNT(*) FROM promo_push_log").fetchone()[0] == 0:
        for row in conn.execute(
            """SELECT product_id, shop_cipher, action, new_discount, flash_price, pushed_at
               FROM promo_queue WHERE status = 'pushed' AND pushed_at IS NOT NULL"""
        ).fetchall():
            conn.execute(
                """INSERT INTO promo_push_log (
                    product_id, shop_cipher, action, new_discount, flash_price, pushed_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    row["product_id"],
                    row["shop_cipher"],
                    row["action"] or "adjust",
                    row["new_discount"],
                    row["flash_price"],
                    row["pushed_at"],
                ),
            )


def _promo_cooldown_cutoff() -> int:
    days = _promo_settings()["cooldown_days"]
    return int(time.time()) - days * 86400


def _recently_adjusted_promo(conn, product_id: str, shop_cipher: str) -> bool:
    cutoff = _promo_cooldown_cutoff()
    row = conn.execute(
        """SELECT 1 FROM promo_push_log
           WHERE product_id = ? AND shop_cipher = ? AND pushed_at >= ?
           LIMIT 1""",
        (product_id, shop_cipher, cutoff),
    ).fetchone()
    if row:
        return True
    row = conn.execute(
        """SELECT 1 FROM promo_queue
           WHERE product_id = ? AND shop_cipher = ? AND status = 'pushed'
             AND pushed_at IS NOT NULL AND pushed_at >= ?
           LIMIT 1""",
        (product_id, shop_cipher, cutoff),
    ).fetchone()
    return bool(row)


def _record_promo_push(row, pushed_at: int) -> None:
    conn = connect()
    try:
        _migrate_promo_push_log(conn)
        conn.execute(
            """INSERT INTO promo_push_log (
                product_id, shop_cipher, action, new_discount, flash_price, pushed_at
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                row["product_id"],
                row["shop_cipher"],
                row["action"] or "adjust",
                row["new_discount"],
                row["flash_price"],
                pushed_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _format_price_amount(price: float, currency: str) -> str:
    if currency.upper() in ("VND", "IDR"):
        return str(int(round(price)))
    return f"{price:.2f}"


def _currency_symbol(currency: str) -> str:
    return {"MYR": "RM", "VND": "₫", "THB": "฿", "PHP": "₱"}.get(currency.upper(), currency)


def search_activities(token: str, cipher: str, status: str = "ONGOING") -> list[dict]:
    r = api_post(
        ACTIVITY_SEARCH,
        token,
        {"shop_cipher": cipher, "page_size": "20"},
        {"page_size": 20, "status": status},
    )
    if r.get("code") != 0:
        raise RuntimeError(r.get("message", "促销活动搜索失败"))
    return (r.get("data") or {}).get("activities") or []


def get_activity(token: str, cipher: str, activity_id: str) -> dict:
    r = api_get(
        f"/promotion/202309/activities/{activity_id}",
        token,
        {"shop_cipher": cipher},
    )
    if r.get("code") != 0:
        raise RuntimeError(r.get("message", f"活动详情失败 {activity_id}"))
    return r.get("data") or {}


def find_ongoing_direct_discount(token: str, cipher: str) -> tuple[str, dict] | None:
    acts = search_activities(token, cipher, "ONGOING")
    direct = next(
        (a for a in acts if (a.get("activity_type") or "") == "DIRECT_DISCOUNT"),
        None,
    )
    if not direct:
        return None
    act_id = str(direct.get("id") or direct.get("activity_id") or "")
    if not act_id:
        return None
    return act_id, get_activity(token, cipher, act_id)


def list_ongoing_by_shop(token: str | None = None, region: str | None = None) -> list[dict]:
    token = token or auth.access_token()
    from core import shops

    out: list[dict] = []
    for shop in shops.list_shops(token):
        cipher = shop.get("cipher") or shop.get("shop_cipher", "")
        reg = (shop.get("region") or "").upper()
        if region and reg != region.upper():
            continue
        try:
            acts = search_activities(token, cipher, "ONGOING")
        except RuntimeError:
            continue
        for act in acts:
            act_id = str(act.get("id") or act.get("activity_id") or "")
            if not act_id:
                continue
            try:
                detail = get_activity(token, cipher, act_id)
            except RuntimeError:
                detail = act
            products = detail.get("products") or []
            out.append({
                "region": reg,
                "shop_cipher": cipher,
                "activity_id": act_id,
                "title": act.get("title") or detail.get("title") or "",
                "activity_type": act.get("activity_type") or detail.get("activity_type"),
                "product_count": len(products),
                "begin_time": detail.get("begin_time") or act.get("begin_time"),
                "end_time": detail.get("end_time") or act.get("end_time"),
            })
    return out


def create_activity(
    token: str,
    cipher: str,
    title: str,
    activity_type: str,
    begin_time: int,
    end_time: int,
    product_level: str = "PRODUCT",
) -> str:
    r = api_post(
        "/promotion/202309/activities",
        token,
        {"shop_cipher": cipher},
        {
            "title": title,
            "activity_type": activity_type,
            "begin_time": begin_time,
            "end_time": end_time,
            "product_level": product_level,
        },
    )
    if r.get("code") != 0:
        raise RuntimeError(r.get("message", "创建活动失败"))
    act_id = (r.get("data") or {}).get("activity_id")
    if not act_id:
        raise RuntimeError("创建活动未返回 activity_id")
    return str(act_id)


def deactivate_activity(token: str, cipher: str, activity_id: str) -> tuple[bool, str]:
    r = api_post(
        f"/promotion/202309/activities/{activity_id}/deactivate",
        token,
        {"shop_cipher": cipher},
        {},
    )
    if r.get("code") == 0:
        return True, ""
    return False, r.get("message", str(r))[:200]


def _estimate_margin_pct(price: float, currency: str, discount_pct: float, cost_cny: float) -> float | None:
    if price <= 0 or cost_cny <= 0:
        return None
    rate = exchange_rate_for(currency)
    if not rate:
        return None
    promo_local = price * (1 - discount_pct / 100)
    revenue_cny = promo_local * rate
    if revenue_cny <= 0:
        return None
    return (revenue_cny - cost_cny) / revenue_cny * 100


def _estimate_margin_from_price(list_price: float, promo_price: float, currency: str, cost_cny: float) -> float | None:
    if list_price <= 0 or cost_cny <= 0:
        return None
    rate = exchange_rate_for(currency)
    if not rate:
        return None
    revenue_cny = promo_price * rate
    if revenue_cny <= 0:
        return None
    return (revenue_cny - cost_cny) / revenue_cny * 100


def _suggest_discount(
    current: float,
    units: int,
    price: float,
    currency: str,
    cost_cny: float,
    cfg: dict,
) -> tuple[float, str | None]:
    bump = cfg["bump_pct"]
    max_d = cfg["max_discount"]
    min_d = cfg["min_discount"]
    min_margin = cfg["min_margin_pct"]

    if units <= 0:
        target = min(current + bump, max_d) if current > 0 else min(cfg["add_initial_discount"], max_d)
        note = f"近期待售，建议 +{bump:g}% 折扣" if current > 0 else f"建议初始折扣 {target:g}%"
    else:
        return current, "已有动销，暂不调整"

    if cost_cny > 0 and price > 0:
        while target > min_d:
            margin = _estimate_margin_pct(price, currency, target, cost_cny)
            if margin is None or margin >= min_margin:
                break
            target -= 1
        if current > 0 and target <= current:
            return current, f"受最低毛利 {min_margin:g}% 限制，无法加深折扣"

    if current > 0 and target <= current:
        return current, "已达折扣上限或无需调整"

    return round(target, 1), note


def _suggest_flash_price(price: float, currency: str, cost_cny: float, cfg: dict) -> tuple[float, float, str | None]:
    pct = cfg["flash_discount_pct"]
    max_d = cfg["max_discount"]
    min_margin = cfg["min_margin_pct"]
    discount = min(pct, max_d)
    flash_price = price * (1 - discount / 100) if price > 0 else 0

    if cost_cny > 0 and price > 0:
        while discount > cfg["min_discount"]:
            margin = _estimate_margin_from_price(price, flash_price, currency, cost_cny)
            if margin is None or margin >= min_margin:
                break
            discount -= 1
            flash_price = price * (1 - discount / 100)

    note = f"限时秒杀建议 {discount:g}%  off"
    return round(flash_price, 2), round(discount, 1), note


def _product_rows(conn, cipher: str) -> list:
    return conn.execute(
        """SELECT p.product_id, p.shop_cipher,
                  MAX(p.product_name) AS product_name,
                  MAX(p.image_url) AS image_url,
                  MAX(p.seller_sku) AS seller_sku,
                  MAX(p.price) AS list_price,
                  MAX(p.currency) AS currency,
                  SUM(p.stock) AS stock_total,
                  MAX(p.sku_id) AS sample_sku
           FROM products p
           WHERE p.shop_cipher = ? AND p.status = 'ACTIVATE' AND p.product_id != ''
           GROUP BY p.product_id, p.shop_cipher""",
        (cipher,),
    ).fetchall()


def scan_low_velocity(
    days: int = 30,
    max_units: int = 1,
    limit: int = 30,
    region: str | None = None,
    scope: str = "adjust",
    quiet: bool = False,
) -> int:
    """scope: adjust | add | flash | all"""
    init_db()
    conn = connect()
    _migrate_promo_queue(conn)
    conn.commit()
    cfg = _promo_settings()
    scopes = {"adjust", "add", "flash", "all"}
    if scope not in scopes:
        raise ValueError(f"scope 必须是 {scopes} 之一")

    def _log(msg: str) -> None:
        if not quiet:
            print(msg)

    _log(f"\n[1/3] 统计近 {days} 天动销...")
    sold = sales.aggregate_product_sales(days=days, region=region)
    cost_map = cost_mod.get_all_costs()
    token = auth.access_token()
    now = int(time.time())

    _log("\n[2/3] 拉取各站促销活动...")
    from core import shops

    candidates: list[dict] = []
    for shop in shops.list_shops(token):
        reg = (shop.get("region") or "").upper()
        if region and reg != region.upper():
            continue
        cipher = shop.get("cipher") or shop.get("shop_cipher", "")
        direct_info = find_ongoing_direct_discount(token, cipher)
        if not direct_info and scope in ("adjust", "add", "all"):
            if not quiet:
                print(f"  {reg}: 无进行中的直接折扣活动，跳过 adjust/add")
        promo_products: dict = {}
        act_id = ""
        if direct_info:
            act_id, detail = direct_info
            promo_products = {str(p.get("id")): p for p in (detail.get("products") or []) if p.get("id")}

        rows = _product_rows(conn, cipher)
        for row in rows:
            pid = row["product_id"]
            key = (pid, cipher)
            units = sold.get(key, {}).get("units", 0)
            if units > max_units:
                continue
            if int(row["stock_total"] or 0) < 1:
                continue
            if _recently_adjusted_promo(conn, pid, cipher):
                continue

            price = float(row["list_price"] or 0)
            currency = row["currency"] or REGION_CURRENCY.get(reg, "")
            sku_id = row["sample_sku"] or ""
            cost_cny = cost_map.get(sku_id, 0)
            in_promo = pid in promo_products
            base = {
                "product_id": pid,
                "shop_cipher": cipher,
                "activity_id": act_id,
                "region": reg,
                "product_name": row["product_name"] or "",
                "image_url": row["image_url"] or "",
                "seller_sku": row["seller_sku"] or "",
                "list_price": price,
                "currency": currency,
                "units_sold": units,
                "order_count": sold.get(key, {}).get("orders", 0),
                "stock": int(row["stock_total"] or 0),
            }
            stats = _analytics_stats(conn, pid, cipher)
            base.update(stats)

            if scope in ("adjust", "all") and in_promo and act_id:
                old_discount = float(promo_products[pid].get("discount") or 0)
                suggested, note = _suggest_discount(old_discount, units, price, currency, cost_cny, cfg)
                promo_price = price * (1 - suggested / 100) if price > 0 else 0
                margin = _estimate_margin_pct(price, currency, suggested, cost_cny)
                candidates.append({
                    **base,
                    "old_discount": old_discount,
                    "suggested_discount": suggested,
                    "new_discount": suggested,
                    "promo_price": round(promo_price, 2),
                    "flash_price": None,
                    "margin_pct": round(margin, 1) if margin is not None else None,
                    "in_activity": 1,
                    "action": "adjust",
                    "note": note,
                })

            if scope in ("add", "all") and not in_promo and act_id and units <= max_units:
                suggested, note = _suggest_discount(0, units, price, currency, cost_cny, cfg)
                promo_price = price * (1 - suggested / 100) if price > 0 else 0
                margin = _estimate_margin_pct(price, currency, suggested, cost_cny)
                candidates.append({
                    **base,
                    "old_discount": 0,
                    "suggested_discount": suggested,
                    "new_discount": suggested,
                    "promo_price": round(promo_price, 2),
                    "flash_price": None,
                    "margin_pct": round(margin, 1) if margin is not None else None,
                    "in_activity": 0,
                    "action": "add",
                    "note": note or "未在促销活动中，建议加入",
                })

            if scope in ("flash", "all") and units <= max_units:
                flash_price, flash_disc, note = _suggest_flash_price(price, currency, cost_cny, cfg)
                margin = _estimate_margin_from_price(price, flash_price, currency, cost_cny)
                candidates.append({
                    **base,
                    "activity_id": "",
                    "old_discount": 0,
                    "suggested_discount": flash_disc,
                    "new_discount": flash_disc,
                    "promo_price": round(flash_price, 2),
                    "flash_price": round(flash_price, 2),
                    "margin_pct": round(margin, 1) if margin is not None else None,
                    "in_activity": 0,
                    "action": "flash",
                    "note": note,
                })

    candidates.sort(key=lambda x: (x["units_sold"], -x["stock"]))
    candidates = candidates[:limit]

    if not candidates:
        conn.close()
        if not quiet:
            print("  未找到符合条件的商品")
        return 0

    _log(f"\n[3/3] 写入建议 ({len(candidates)} 个)...")
    n = 0
    for item in candidates:
        _upsert_promo_queue(conn, item, now)
        n += 1

    conn.commit()
    conn.close()
    if not quiet:
        print(f"  ✅ 已写入 {n} 条待确认")
    return n


def scan_analytics_high_interest(
    limit: int = 30,
    region: str | None = None,
    scope: str = "all",
    quiet: bool = False,
) -> int:
    """A 类（28天 CTR≥中位×1.5 · 0单）→ 促销：未在活动则 add，已在则 adjust，可选 flash。"""
    from modules.products import analytics as analytics_mod

    init_db()
    conn = connect()
    _migrate_promo_queue(conn)
    cfg = _promo_settings()
    scopes = {"adjust", "add", "flash", "all"}
    if scope not in scopes:
        raise ValueError(f"scope 必须是 {scopes} 之一")

    def _log(msg: str) -> None:
        if not quiet:
            print(msg)

    _log("\n[1/3] 同步 28 天 Analytics...")
    analytics_mod.sync_all(region=region, quiet=quiet)

    _log("\n[2/3] 筛选 A 类并拉取促销活动...")
    a_rows = analytics_mod.load_analytics(
        segment=analytics_mod.SEGMENT_HIGH_INTEREST,
        region=region,
    )
    cost_map = cost_mod.get_all_costs()
    token = auth.access_token()
    now = int(time.time())
    candidates: list[dict] = []

    from core import shops

    shop_ciphers = {
        (s.get("cipher") or s.get("shop_cipher", "")): (s.get("region") or "").upper()
        for s in shops.list_shops(token)
    }

    promo_cache: dict[str, tuple[str, dict]] = {}
    for row in a_rows:
        cipher = row["shop_cipher"]
        pid = row["product_id"]
        if int(row.get("stock_total") or 0) < 1:
            continue
        if _recently_adjusted_promo(conn, pid, cipher):
            continue

        if cipher not in promo_cache:
            direct_info = find_ongoing_direct_discount(token, cipher)
            if direct_info:
                act_id, detail = direct_info
                promo_products = {
                    str(p.get("id")): p for p in (detail.get("products") or []) if p.get("id")
                }
                promo_cache[cipher] = (act_id, promo_products)
            else:
                promo_cache[cipher] = ("", {})

        act_id, promo_products = promo_cache[cipher]
        in_promo = pid in promo_products

        prod_rows = conn.execute(
            """SELECT MAX(p.product_name) AS product_name, MAX(p.image_url) AS image_url,
                      MAX(p.seller_sku) AS seller_sku, MAX(p.price) AS list_price,
                      MAX(p.currency) AS currency,
                      (SELECT sku_id FROM products p2
                       WHERE p2.product_id = p.product_id AND p2.shop_cipher = p.shop_cipher
                       LIMIT 1) AS sample_sku,
                      SUM(p.stock) AS stock_total
               FROM products p
               WHERE p.product_id = ? AND p.shop_cipher = ? AND p.status = 'ACTIVATE'
               GROUP BY p.product_id, p.shop_cipher""",
            (pid, cipher),
        ).fetchone()
        if not prod_rows:
            continue

        reg = row.get("region") or shop_ciphers.get(cipher, "")
        price = float(prod_rows["list_price"] or 0)
        currency = prod_rows["currency"] or REGION_CURRENCY.get(reg, "")
        sku_id = prod_rows["sample_sku"] or ""
        cost_cny = cost_map.get(sku_id, 0)
        ctr = float(row.get("click_through_rate") or 0)
        ctr_note = f"A类 CTR {(ctr * 100):.2f}% · 28天0单"

        base = {
            "product_id": pid,
            "shop_cipher": cipher,
            "activity_id": act_id,
            "region": reg,
            "product_name": prod_rows["product_name"] or row.get("product_name") or "",
            "image_url": prod_rows["image_url"] or row.get("image_url") or "",
            "seller_sku": prod_rows["seller_sku"] or row.get("seller_sku") or "",
            "list_price": price,
            "currency": currency,
            "units_sold": 0,
            "order_count": 0,
            "stock": int(prod_rows["stock_total"] or 0),
            "click_through_rate": ctr,
            "ctr_median": float(row.get("ctr_median") or 0) or None,
            "analytics_orders": int(row.get("orders") or 0),
        }

        if scope in ("adjust", "all") and in_promo and act_id:
            old_discount = float(promo_products[pid].get("discount") or 0)
            suggested, note = _suggest_discount(old_discount, 0, price, currency, cost_cny, cfg)
            promo_price = price * (1 - suggested / 100) if price > 0 else 0
            margin = _estimate_margin_pct(price, currency, suggested, cost_cny)
            candidates.append({
                **base,
                "old_discount": old_discount,
                "suggested_discount": suggested,
                "new_discount": suggested,
                "promo_price": round(promo_price, 2),
                "flash_price": None,
                "margin_pct": round(margin, 1) if margin is not None else None,
                "in_activity": 1,
                "action": "adjust",
                "note": f"{ctr_note} · {note}" if note else ctr_note,
            })

        if scope in ("add", "all") and not in_promo and act_id:
            suggested, note = _suggest_discount(0, 0, price, currency, cost_cny, cfg)
            promo_price = price * (1 - suggested / 100) if price > 0 else 0
            margin = _estimate_margin_pct(price, currency, suggested, cost_cny)
            candidates.append({
                **base,
                "old_discount": 0,
                "suggested_discount": suggested,
                "new_discount": suggested,
                "promo_price": round(promo_price, 2),
                "flash_price": None,
                "margin_pct": round(margin, 1) if margin is not None else None,
                "in_activity": 0,
                "action": "add",
                "note": f"{ctr_note} · {note or '高兴趣未转化，建议加入促销'}",
            })

        if scope in ("flash", "all"):
            flash_price, flash_disc, note = _suggest_flash_price(price, currency, cost_cny, cfg)
            margin = _estimate_margin_from_price(price, flash_price, currency, cost_cny)
            candidates.append({
                **base,
                "activity_id": "",
                "old_discount": 0,
                "suggested_discount": flash_disc,
                "new_discount": flash_disc,
                "promo_price": round(flash_price, 2),
                "flash_price": round(flash_price, 2),
                "margin_pct": round(margin, 1) if margin is not None else None,
                "in_activity": 0,
                "action": "flash",
                "note": f"{ctr_note} · {note}",
            })

    candidates.sort(key=lambda x: (-float(x.get("click_through_rate") or 0), -x["stock"]))
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict] = []
    for c in candidates:
        key = (c["product_id"], c["shop_cipher"], c["action"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    deduped = deduped[:limit]

    if not deduped:
        conn.close()
        if not quiet:
            print("  未找到 A 类促销候选（需有进行中的直接折扣活动才能 add/adjust）")
        return 0

    _log(f"\n[3/3] 写入促销建议 ({len(deduped)} 个)...")
    n = 0
    for item in deduped:
        _upsert_promo_queue(conn, item, now)
        n += 1

    conn.commit()
    conn.close()
    if not quiet:
        print(f"  ✅ 已写入 {n} 条 A 类促销待确认")
    return n


def load_queue(
    status: str | None = "pending",
    action: str | None = None,
    region: str | None = None,
) -> list[dict]:
    init_db()
    conn = connect()
    _migrate_promo_queue(conn)
    sql = """
        SELECT q.*,
               a.click_through_rate AS _a_ctr,
               a.ctr_median AS _a_ctr_median,
               a.orders AS _a_orders
        FROM promo_queue q
        LEFT JOIN product_analytics a
          ON a.product_id = q.product_id AND a.shop_cipher = q.shop_cipher
    """
    params: list = []
    clauses: list[str] = []
    if status:
        clauses.append("q.status = ?")
        params.append(status)
    if action:
        clauses.append("q.action = ?")
        params.append(action)
    if region:
        clauses.append("q.region = ?")
        params.append(region.upper())
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY q.units_sold ASC, q.stock DESC"
    rows: list[dict] = []
    for r in conn.execute(sql, params).fetchall():
        item = dict(r)
        if item.get("click_through_rate") is None and item.get("_a_ctr") is not None:
            item["click_through_rate"] = item["_a_ctr"]
        if item.get("ctr_median") is None and item.get("_a_ctr_median") is not None:
            item["ctr_median"] = item["_a_ctr_median"]
        if item.get("analytics_orders") is None and item.get("_a_orders") is not None:
            item["analytics_orders"] = item["_a_orders"]
        for k in ("_a_ctr", "_a_ctr_median", "_a_orders"):
            item.pop(k, None)
        rows.append(item)
    conn.close()
    return rows


def save_edits(items: list[dict]) -> int:
    init_db()
    conn = connect()
    _migrate_promo_queue(conn)
    cfg = _promo_settings()
    n = 0
    for it in items:
        pid = str(it.get("product_id", ""))
        cipher = str(it.get("shop_cipher", ""))
        action = str(it.get("action") or "adjust")
        if not pid or not cipher:
            continue
        if action == "flash":
            try:
                flash_price = float(it.get("flash_price") or it.get("promo_price") or 0)
            except (TypeError, ValueError):
                continue
            if flash_price <= 0:
                continue
            conn.execute(
                """UPDATE promo_queue SET flash_price = ?, promo_price = ?, status = 'pending'
                   WHERE product_id = ? AND shop_cipher = ? AND action = 'flash'""",
                (flash_price, flash_price, pid, cipher),
            )
        else:
            try:
                new_d = float(it.get("new_discount", 0))
            except (TypeError, ValueError):
                continue
            if new_d <= 0:
                continue
            new_d = min(max(new_d, cfg["min_discount"]), cfg["max_discount"])
            conn.execute(
                """UPDATE promo_queue SET new_discount = ?, status = 'pending'
                   WHERE product_id = ? AND shop_cipher = ? AND action = ?""",
                (new_d, pid, cipher, action),
            )
        n += 1
    conn.commit()
    conn.close()
    return n


def push_discount(
    token: str,
    cipher: str,
    activity_id: str,
    product_id: str,
    discount: float,
) -> tuple[bool, str]:
    body = {
        "activity_id": activity_id,
        "products": [{
            "id": product_id,
            "discount": str(int(discount) if discount == int(discount) else discount),
            "quantity_limit": -1,
            "quantity_per_user": -1,
        }],
    }
    try:
        r = api_put(
            f"/promotion/202309/activities/{activity_id}/products",
            token,
            {"shop_cipher": cipher},
            body,
        )
    except RuntimeError as e:
        return False, str(e)[:200]
    if r.get("code") == 0:
        return True, ""
    return False, r.get("message", str(r))[:200]


def push_flash_product(
    token: str,
    cipher: str,
    activity_id: str,
    product_id: str,
    flash_price: float,
    currency: str,
) -> tuple[bool, str]:
    body = {
        "activity_id": activity_id,
        "products": [{
            "id": product_id,
            "activity_price_amount": _format_price_amount(flash_price, currency),
            "quantity_limit": -1,
            "quantity_per_user": -1,
        }],
    }
    try:
        r = api_put(
            f"/promotion/202309/activities/{activity_id}/products",
            token,
            {"shop_cipher": cipher},
            body,
        )
    except RuntimeError as e:
        return False, str(e)[:200]
    if r.get("code") == 0:
        return True, ""
    return False, r.get("message", str(r))[:200]


def push_approved(ids: list[int] | None = None) -> dict:
    init_db()
    conn = connect()
    _migrate_promo_queue(conn)
    if ids:
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT * FROM promo_queue WHERE id IN ({placeholders}) AND status = 'pending'",
            ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM promo_queue WHERE status = 'pending'"
        ).fetchall()
    conn.close()

    token = auth.access_token()
    cfg = _promo_settings()
    delay = cfg["push_delay_sec"]
    ok = fail = skip = 0
    errors: list[str] = []
    now = int(time.time())
    rate_limited = False

    flash_rows = [r for r in rows if (r["action"] or "adjust") == "flash"]
    other_rows = [r for r in rows if (r["action"] or "adjust") != "flash"]

    flash_groups: dict[str, list] = defaultdict(list)
    for row in flash_rows:
        flash_groups[row["shop_cipher"]].append(row)

    for cipher, group in flash_groups.items():
        region = group[0]["region"] or ""
        begin = now + cfg["flash_start_delay_hours"] * 3600
        end = begin + cfg["flash_duration_hours"] * 3600
        title = f"商家秒杀 {datetime.now():%Y/%m/%d %H:%M:%S}"
        try:
            act_id = create_activity(token, cipher, title, "FLASHSALE", begin, end)
        except RuntimeError as e:
            for row in group:
                fail += 1
                errors.append(f"{region} 创建秒杀失败: {e}")
                _set_row_status(row["id"], "failed", str(e)[:200])
            continue

        for row in group:
            if rate_limited:
                break
            flash_price = float(row["flash_price"] or row["promo_price"] or 0)
            if flash_price <= 0:
                skip += 1
                _set_row_status(row["id"], "skipped", "秒杀价无效")
                continue
            success, err = push_flash_product(
                token, cipher, act_id, row["product_id"], flash_price, row["currency"] or ""
            )
            if success:
                ok += 1
                _set_row_status(row["id"], "pushed", None, pushed_at=now, activity_id=act_id)
                _record_promo_push(row, now)
            else:
                if "429" in err or "Too many requests" in err or "36009037" in err:
                    rate_limited = True
                    errors.append(f"{region}: 触发限流，已暂停剩余推送（请稍后重试未成功项）")
                    break
                fail += 1
                errors.append(f"{region} {row['product_id'][:12]}…: {err}")
                _set_row_status(row["id"], "failed", err)
            time.sleep(delay)

    for row in other_rows:
        if rate_limited:
            break
        row_id = row["id"]
        action = row["action"] or "adjust"
        old_d = float(row["old_discount"] or 0)
        new_d = float(row["new_discount"] or 0)
        activity_id = row["activity_id"]

        if action == "add":
            if new_d <= 0 or not activity_id:
                skip += 1
                _set_row_status(row_id, "skipped", "无效折扣或缺少活动")
                continue
        elif abs(new_d - old_d) < 0.01:
            skip += 1
            _set_row_status(row_id, "skipped", "折扣未修改")
            continue

        success, err = push_discount(token, row["shop_cipher"], activity_id, row["product_id"], new_d)
        if success:
            ok += 1
            _set_row_status(row_id, "pushed", None, pushed_at=now, old_discount=new_d)
            _record_promo_push(row, now)
        else:
            if "429" in err or "Too many requests" in err or "36009037" in err:
                rate_limited = True
                errors.append(f"{row['region']}: 触发限流，已暂停剩余推送（请稍后重试未成功项）")
                break
            fail += 1
            errors.append(f"{row['region']} {row['product_id'][:12]}…: {err}")
            _set_row_status(row_id, "failed", err)
        time.sleep(delay)

    return {"ok": ok, "fail": fail, "skip": skip, "errors": errors, "rate_limited": rate_limited}


def _set_row_status(
    row_id: int,
    status: str,
    error: str | None,
    pushed_at: int | None = None,
    old_discount: float | None = None,
    activity_id: str | None = None,
) -> None:
    conn = connect()
    try:
        if pushed_at and old_discount is not None:
            conn.execute(
                """UPDATE promo_queue SET status = ?, pushed_at = ?, error = NULL, old_discount = ?
                   WHERE id = ?""",
                (status, pushed_at, old_discount, row_id),
            )
        elif pushed_at and activity_id:
            conn.execute(
                """UPDATE promo_queue SET status = ?, pushed_at = ?, error = NULL, activity_id = ?
                   WHERE id = ?""",
                (status, pushed_at, activity_id, row_id),
            )
        else:
            conn.execute(
                "UPDATE promo_queue SET status = ?, error = ? WHERE id = ?",
                (status, error, row_id),
            )
        conn.commit()
    finally:
        conn.close()


# ── 优惠券（202406 API：查询可用，创建需后台手动） ──

def list_coupons(token: str | None = None, region: str | None = None) -> list[dict]:
    token = token or auth.access_token()
    from core import shops

    out: list[dict] = []
    for shop in shops.list_shops(token):
        reg = (shop.get("region") or "").upper()
        if region and reg != region.upper():
            continue
        cipher = shop.get("cipher") or shop.get("shop_cipher", "")
        page_token = ""
        while True:
            qp = {"shop_cipher": cipher, "page_size": "20"}
            if page_token:
                qp["page_token"] = page_token
            body: dict = {"page_size": 20}
            r = api_post(COUPON_SEARCH, token, qp, body)
            if r.get("code") != 0:
                break
            data = r.get("data") or {}
            for c in data.get("coupons") or []:
                out.append({**c, "region": reg, "shop_cipher": cipher})
            page_token = data.get("next_page_token") or ""
            if not page_token:
                break
            time.sleep(0.2)
    return out


def get_coupon(token: str, cipher: str, coupon_id: str) -> dict:
    r = api_get(
        f"/promotion/{COUPON_API_VER}/coupons/{coupon_id}",
        token,
        {"shop_cipher": cipher},
    )
    if r.get("code") != 0:
        raise RuntimeError(r.get("message", "优惠券详情失败"))
    return (r.get("data") or {}).get("coupon") or r.get("data") or {}


def _median_price(conn, cipher: str) -> float:
    rows = conn.execute(
        """SELECT price FROM products
           WHERE shop_cipher = ? AND status = 'ACTIVATE' AND price > 0
           ORDER BY price""",
        (cipher,),
    ).fetchall()
    if not rows:
        return 0
    prices = [float(r["price"]) for r in rows]
    mid = len(prices) // 2
    return prices[mid] if len(prices) % 2 else (prices[mid - 1] + prices[mid]) / 2


def scan_coupon_suggestions(region: str | None = None, limit: int = 4, quiet: bool = False) -> int:
    init_db()
    conn = connect()
    _migrate_coupon_drafts(conn)
    cfg = _promo_settings()
    from core import shops

    token = auth.access_token()
    now = int(time.time())
    n = 0

    for shop in shops.list_shops(token):
        reg = (shop.get("region") or "").upper()
        if region and reg != region.upper():
            continue
        cipher = shop.get("cipher") or shop.get("shop_cipher", "")
        currency = REGION_CURRENCY.get(reg, "")
        median = _median_price(conn, cipher)
        if median <= 0:
            continue

        min_spend = max(10, median * cfg["coupon_min_spend_multiplier"])
        amount = max(1, min_spend * cfg["coupon_amount_ratio"])
        if currency == "VND":
            min_spend = round(min_spend, -3)
            amount = round(amount, -2)
        else:
            min_spend = round(min_spend, 2)
            amount = round(amount, 2)

        sym = _currency_symbol(currency)
        config = {
            "title": f"满{min_spend:g}{sym}减{amount:g}{sym} {datetime.now():%m/%d}",
            "display_type": "REGULAR",
            "target_buyer_segment": "ALL",
            "product_scope": "FULL_SHOP",
            "discount": {
                "type": "AMOUNT_OFF",
                "reduction_amount": {"amount": str(amount), "currency": sym},
            },
            "threshold": {
                "type": "MIN_SPEND",
                "min_spend": {"amount": str(min_spend), "currency": sym},
            },
            "claim_duration": {
                "start_time": now + 3600,
                "end_time": now + 86400 * 14,
            },
            "redemption_duration": {"type": "RELATIVE", "relative_time": 3},
            "usage_limits": {"redemption_limit": 200, "single_buyer_claim_limit": 2},
        }
        note = (
            f"基于站点中位价 {median:g} {currency} 生成；"
            "Open API 暂不支持创建优惠券，请复制配置到 Seller Center → 营销 → 优惠券"
        )
        conn.execute(
            """INSERT INTO coupon_drafts (shop_cipher, region, title, config_json, note, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'draft', ?)""",
            (cipher, reg, config["title"], json.dumps(config, ensure_ascii=False), note, now),
        )
        n += 1
        if n >= limit:
            break

    conn.commit()
    conn.close()
    if not quiet:
        print(f"  ✅ 已生成 {n} 条优惠券建议")
    return n


def load_coupon_drafts(status: str = "draft") -> list[dict]:
    init_db()
    conn = connect()
    _migrate_coupon_drafts(conn)
    rows = conn.execute(
        "SELECT * FROM coupon_drafts WHERE status = ? ORDER BY created_at DESC",
        (status,),
    ).fetchall()
    out = []
    for r in rows:
        row = dict(r)
        try:
            row["config"] = json.loads(row["config_json"] or "{}")
        except json.JSONDecodeError:
            row["config"] = {}
        out.append(row)
    conn.close()
    return out


def mark_coupon_draft_used(draft_id: int) -> None:
    init_db()
    conn = connect()
    _migrate_coupon_drafts(conn)
    conn.execute(
        "UPDATE coupon_drafts SET status = 'manual_created' WHERE id = ?",
        (draft_id,),
    )
    conn.commit()
    conn.close()
