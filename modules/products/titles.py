"""Listing 优化：低动销 / Analytics 高兴趣低转化 → 标题+详情 → 推送 TikTok。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from core import auth
from core.api_client import get as api_get, post as api_post
from core.config import ROOT, get
from core.db import connect, init_db
from modules.products import analytics as analytics_mod
from modules.products import keyword_intel
from modules.products import sales
from modules.products import title_ai

OUTPUT = ROOT / "web" / "titles.html"
TITLE_MAX = 255


def _migrate_title_queue(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS title_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL,
            shop_cipher TEXT NOT NULL,
            region TEXT,
            image_url TEXT,
            seller_sku TEXT,
            old_title TEXT,
            suggested_title TEXT,
            new_title TEXT,
            units_sold INTEGER DEFAULT 0,
            order_count INTEGER DEFAULT 0,
            stock INTEGER DEFAULT 0,
            category_leaf TEXT,
            status TEXT DEFAULT 'pending',
            error TEXT,
            created_at INTEGER,
            pushed_at INTEGER,
            UNIQUE(product_id, shop_cipher)
        )"""
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(title_queue)")}
    for name, ddl in (
        ("keyword_intel", "ALTER TABLE title_queue ADD COLUMN keyword_intel TEXT"),
        ("old_description", "ALTER TABLE title_queue ADD COLUMN old_description TEXT"),
        ("suggested_description", "ALTER TABLE title_queue ADD COLUMN suggested_description TEXT"),
        ("new_description", "ALTER TABLE title_queue ADD COLUMN new_description TEXT"),
        ("click_through_rate", "ALTER TABLE title_queue ADD COLUMN click_through_rate REAL"),
        ("ctr_median", "ALTER TABLE title_queue ADD COLUMN ctr_median REAL"),
        ("segment", "ALTER TABLE title_queue ADD COLUMN segment TEXT"),
        ("scan_mode", "ALTER TABLE title_queue ADD COLUMN scan_mode TEXT"),
    ):
        if name not in cols:
            conn.execute(ddl)


def _dedupe_parts(title: str) -> list[str]:
    parts = re.split(r"[,，/|]+", title)
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        p = re.sub(r"\s+", " ", p.strip())
        if not p or len(p) < 2:
            continue
        key = p.lower()[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def suggest_title_rules(
    title: str,
    category_leaf: str = "",
    sku_hint: str = "",
) -> str:
    """规则化标题备选（AI 不可用时）。"""
    parts = _dedupe_parts(title)
    if not parts:
        parts = [title.strip()]

    lead = parts[0]
    if category_leaf and category_leaf.lower() not in lead.lower()[:60]:
        lead = f"{category_leaf} {lead}"

    tail: list[str] = []
    if sku_hint and sku_hint.lower() not in lead.lower():
        tail.append(sku_hint)
    for p in parts[1:3]:
        if p.lower() not in lead.lower():
            tail.append(p)

    out = lead
    if tail:
        out = f"{lead}, {', '.join(tail)}"
    out = re.sub(r"\s+", " ", out).strip(" ,")
    if len(out) > TITLE_MAX:
        out = out[: TITLE_MAX - 1].rsplit(" ", 1)[0]
    return out[:TITLE_MAX]


def suggest_title(detail: dict, item: dict) -> tuple[str, str, str | None, dict]:
    """生成标题建议。返回 (title, category_leaf, error_note, keyword_intel)。"""
    title, _, leaf, note, intel = suggest_listing(detail, item, listing_only=False)
    return title, leaf, note, intel


def suggest_listing(
    detail: dict,
    item: dict,
    *,
    listing_only: bool = True,
) -> tuple[str, str, str, str | None, dict]:
    """生成标题+详情。返回 (title, description, category_leaf, error_note, keyword_intel)。"""
    leaf = _category_leaf(detail)
    intel = keyword_intel.lookup(
        leaf,
        item.get("region") or "",
        item.get("old_title") or "",
    )
    ctx = title_ai.build_product_context(detail, item, intel=intel)
    leaf = ctx.get("category_leaf") or leaf
    old_desc = detail.get("description") or item.get("old_description") or ""
    try:
        if listing_only:
            title, desc = title_ai.suggest_listing_ai(ctx)
        else:
            title = title_ai.suggest_title_ai(ctx)
            desc = old_desc
        return title, desc, leaf, None, intel
    except Exception as e:
        if not get("ai.fallback_to_rules", True):
            raise
        sku_names = [
            (s.get("sales_attributes") or [{}])[0].get("value_name", "")
            for s in detail.get("skus") or []
        ]
        sku_hint = next((x for x in sku_names if x), "")
        fallback = suggest_title_rules(item.get("old_title") or "", leaf, sku_hint)
        return fallback, old_desc, leaf, f"AI 失败，已用规则备选: {e}"[:200], intel


def _category_leaf(detail: dict) -> str:
    chain = detail.get("category_chains") or []
    if not chain:
        return ""
    return chain[-1].get("local_name") or ""


def _fetch_detail(token: str, cipher: str, product_id: str) -> dict:
    r = api_get(f"/product/202309/products/{product_id}", token, {"shop_cipher": cipher})
    if r.get("code") != 0:
        raise RuntimeError(r.get("message", f"商品详情失败 {product_id}"))
    return r.get("data") or {}


def scan_low_velocity(
    days: int = 30,
    max_units: int = 1,
    limit: int = 30,
    region: str | None = None,
    min_stock: int = 1,
    build_html: bool = True,
    quiet: bool = False,
) -> int:
    init_db()
    conn = connect()
    _migrate_title_queue(conn)

    def _log(msg: str) -> None:
        if not quiet:
            print(msg)

    _log(f"\n[1/3] 统计近 {days} 天动销...")
    sold = sales.aggregate_product_sales(days=days, region=region)

    _log(f"\n[2/3] 筛选低动销商品...")
    if not title_ai.ai_enabled():
        conn.close()
        raise RuntimeError(
            "未配置 AI API Key。请在 config/settings.json 填写 ai.api_key，"
            "或设置环境变量 OPENAI_API_KEY 后重试"
        )

    sql = """
        SELECT p.product_id, p.shop_cipher, s.region,
               MAX(p.product_name) AS product_name,
               MAX(p.image_url) AS image_url,
               MAX(p.seller_sku) AS seller_sku,
               SUM(p.stock) AS stock_total
        FROM products p
        JOIN shops s ON s.cipher = p.shop_cipher
        WHERE p.status = 'ACTIVATE' AND p.product_id != ''
    """
    params: list = []
    if region:
        sql += " AND s.region = ?"
        params.append(region.upper())
    sql += " GROUP BY p.product_id, p.shop_cipher ORDER BY stock_total DESC"
    rows = conn.execute(sql, params).fetchall()

    candidates: list[dict] = []
    for row in rows:
        key = (row["product_id"], row["shop_cipher"])
        units = sold.get(key, {}).get("units", 0)
        if units > max_units:
            continue
        if int(row["stock_total"] or 0) < min_stock:
            continue
        candidates.append({
            "product_id": row["product_id"],
            "shop_cipher": row["shop_cipher"],
            "region": row["region"],
            "old_title": row["product_name"] or "",
            "image_url": row["image_url"] or "",
            "seller_sku": row["seller_sku"] or "",
            "units_sold": units,
            "order_count": sold.get(key, {}).get("orders", 0),
            "stock": int(row["stock_total"] or 0),
        })

    candidates.sort(key=lambda x: (x["units_sold"], -x["stock"]))
    candidates = candidates[:limit]
    if not candidates:
        conn.close()
        if not quiet:
            print("  未找到符合条件的低动销商品")
        return 0

    token = auth.access_token()
    now = int(time.time())
    _log(f"\n[3/3] AI 生成标题建议 ({len(candidates)} 个)...")
    ai_delay = float(get("ai.request_delay_sec", 0.35))
    n = 0
    for i, item in enumerate(candidates):
        try:
            detail = _fetch_detail(token, item["shop_cipher"], item["product_id"])
            suggested, leaf, note, intel = suggest_title(detail, item)
            item["category_leaf"] = leaf
            item["suggested_title"] = suggested
            item["new_title"] = suggested
            item["keyword_intel"] = intel
            if note:
                item["error"] = note
            if not quiet:
                print(f"  [{i + 1}/{len(candidates)}] {item['region']} AI ✓")
        except Exception as e:
            item["category_leaf"] = ""
            item["suggested_title"] = item["old_title"]
            item["new_title"] = item["old_title"]
            item["error"] = str(e)[:200]
            if not quiet:
                print(f"  [{i + 1}/{len(candidates)}] {item['region']} 失败: {item['error']}")
        if ai_delay > 0 and i + 1 < len(candidates):
            time.sleep(ai_delay)

        conn.execute(
            """INSERT INTO title_queue (
                product_id, shop_cipher, region, image_url, seller_sku,
                old_title, suggested_title, new_title, units_sold, order_count,
                stock, category_leaf, keyword_intel, scan_mode, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'velocity', 'pending', ?)
            ON CONFLICT(product_id, shop_cipher) DO UPDATE SET
                region=excluded.region,
                image_url=excluded.image_url,
                seller_sku=excluded.seller_sku,
                old_title=excluded.old_title,
                suggested_title=excluded.suggested_title,
                new_title=excluded.new_title,
                units_sold=excluded.units_sold,
                order_count=excluded.order_count,
                stock=excluded.stock,
                category_leaf=excluded.category_leaf,
                keyword_intel=excluded.keyword_intel,
                scan_mode=excluded.scan_mode,
                status='pending',
                error=NULL,
                created_at=excluded.created_at""",
            (
                item["product_id"],
                item["shop_cipher"],
                item["region"],
                item["image_url"],
                item["seller_sku"],
                item["old_title"],
                item["suggested_title"],
                item["new_title"],
                item["units_sold"],
                item["order_count"],
                item["stock"],
                item.get("category_leaf", ""),
                json.dumps(item.get("keyword_intel") or {}, ensure_ascii=False),
                now,
            ),
        )
        n += 1
        time.sleep(0.1)

    conn.commit()
    conn.close()
    if build_html:
        path = build_review_html()
        print(f"  ✅ 已写入 {n} 条待确认标题")
        print(f"  📄 确认页: {path}")
        print("  下一步: python3 main.py serve")
    elif not quiet:
        print(f"  ✅ 已写入 {n} 条待确认标题")
    return n


def scan_analytics_high_interest(
    limit: int = 30,
    region: str | None = None,
    build_html: bool = True,
    quiet: bool = False,
) -> int:
    """28 天 Analytics：CTR ≥ 中位×1.5 且 0 单 → AI 标题+详情打包。"""
    init_db()
    conn = connect()
    _migrate_title_queue(conn)

    def _log(msg: str) -> None:
        if not quiet:
            print(msg)

    if not title_ai.ai_enabled():
        conn.close()
        raise RuntimeError(
            "未配置 AI API Key。请在 config/settings.json 填写 ai.api_key，"
            "或设置环境变量 OPENAI_API_KEY 后重试"
        )

    _log("\n[1/3] 同步 28 天 Analytics...")
    analytics_mod.sync_all(region=region, quiet=quiet)

    _log("\n[2/3] 筛选 A 类（高 CTR · 0 单）...")
    rows = analytics_mod.load_analytics(
        segment=analytics_mod.SEGMENT_HIGH_INTEREST,
        region=region,
    )
    candidates: list[dict] = []
    for row in rows:
        stock = int(row.get("stock_total") or 0)
        if stock < 1:
            continue
        candidates.append({
            "product_id": row["product_id"],
            "shop_cipher": row["shop_cipher"],
            "region": row["region"],
            "old_title": row.get("product_name") or "",
            "image_url": row.get("image_url") or "",
            "seller_sku": row.get("seller_sku") or "",
            "units_sold": int(row.get("units_sold") or 0),
            "order_count": int(row.get("orders") or 0),
            "stock": stock,
            "click_through_rate": float(row.get("click_through_rate") or 0),
            "ctr_median": float(row.get("ctr_median") or 0),
            "segment": analytics_mod.SEGMENT_HIGH_INTEREST,
        })

    candidates.sort(key=lambda x: (-x["click_through_rate"], -x["stock"]))
    candidates = candidates[:limit]
    if not candidates:
        conn.close()
        if not quiet:
            print("  未找到 A 类（高兴趣低转化）商品")
        return 0

    token = auth.access_token()
    now = int(time.time())
    _log(f"\n[3/3] AI 生成标题+详情 ({len(candidates)} 个)...")
    ai_delay = float(get("ai.request_delay_sec", 0.35))
    n = 0
    for i, item in enumerate(candidates):
        try:
            detail = _fetch_detail(token, item["shop_cipher"], item["product_id"])
            item["old_description"] = detail.get("description") or ""
            suggested_title, suggested_desc, leaf, note, intel = suggest_listing(
                detail, item, listing_only=True
            )
            item["category_leaf"] = leaf
            item["suggested_title"] = suggested_title
            item["new_title"] = suggested_title
            item["suggested_description"] = suggested_desc
            item["new_description"] = suggested_desc
            item["keyword_intel"] = intel
            if note:
                item["error"] = note
            if not quiet:
                ctr_pct = item["click_through_rate"] * 100
                print(f"  [{i + 1}/{len(candidates)}] {item['region']} CTR {ctr_pct:.2f}% AI ✓")
        except Exception as e:
            item["category_leaf"] = ""
            item["old_description"] = item.get("old_description") or ""
            item["suggested_title"] = item["old_title"]
            item["new_title"] = item["old_title"]
            item["suggested_description"] = item.get("old_description") or ""
            item["new_description"] = item.get("old_description") or ""
            item["keyword_intel"] = {}
            item["error"] = str(e)[:200]
            if not quiet:
                print(f"  [{i + 1}/{len(candidates)}] {item['region']} 失败: {item['error']}")
        if ai_delay > 0 and i + 1 < len(candidates):
            time.sleep(ai_delay)

        conn.execute(
            """INSERT INTO title_queue (
                product_id, shop_cipher, region, image_url, seller_sku,
                old_title, suggested_title, new_title,
                old_description, suggested_description, new_description,
                units_sold, order_count, stock, category_leaf, keyword_intel,
                click_through_rate, ctr_median, segment, scan_mode,
                status, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'analytics', 'pending', ?, ?)
            ON CONFLICT(product_id, shop_cipher) DO UPDATE SET
                region=excluded.region,
                image_url=excluded.image_url,
                seller_sku=excluded.seller_sku,
                old_title=excluded.old_title,
                suggested_title=excluded.suggested_title,
                new_title=excluded.new_title,
                old_description=excluded.old_description,
                suggested_description=excluded.suggested_description,
                new_description=excluded.new_description,
                units_sold=excluded.units_sold,
                order_count=excluded.order_count,
                stock=excluded.stock,
                category_leaf=excluded.category_leaf,
                keyword_intel=excluded.keyword_intel,
                click_through_rate=excluded.click_through_rate,
                ctr_median=excluded.ctr_median,
                segment=excluded.segment,
                scan_mode=excluded.scan_mode,
                status='pending',
                error=excluded.error,
                created_at=excluded.created_at""",
            (
                item["product_id"],
                item["shop_cipher"],
                item["region"],
                item["image_url"],
                item["seller_sku"],
                item["old_title"],
                item["suggested_title"],
                item["new_title"],
                item.get("old_description") or "",
                item.get("suggested_description") or "",
                item.get("new_description") or "",
                item["units_sold"],
                item["order_count"],
                item["stock"],
                item.get("category_leaf", ""),
                json.dumps(item.get("keyword_intel") or {}, ensure_ascii=False),
                item["click_through_rate"],
                item["ctr_median"],
                item.get("segment") or "",
                item.get("error"),
                now,
            ),
        )
        n += 1
        time.sleep(0.1)

    conn.commit()
    conn.close()
    if build_html:
        path = build_review_html()
        print(f"  ✅ 已写入 {n} 条待确认 Listing")
        print(f"  📄 确认页: {path}")
        print("  下一步: python3 main.py serve --page titles")
    elif not quiet:
        print(f"  ✅ 已写入 {n} 条待确认 Listing")
    return n


def load_queue(status: str | None = "pending") -> list[dict]:
    init_db()
    conn = connect()
    _migrate_title_queue(conn)
    sql = "SELECT * FROM title_queue"
    params: list = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY units_sold ASC, stock DESC"
    rows = []
    for r in conn.execute(sql, params).fetchall():
        row = dict(r)
        raw = row.get("keyword_intel")
        if raw:
            try:
                row["keyword_intel"] = json.loads(raw)
            except json.JSONDecodeError:
                row["keyword_intel"] = {}
        else:
            row["keyword_intel"] = {}
        rows.append(row)
    conn.close()
    return rows


def save_edits(items: list[dict]) -> int:
    init_db()
    conn = connect()
    _migrate_title_queue(conn)
    n = 0
    for it in items:
        pid = str(it.get("product_id", ""))
        cipher = str(it.get("shop_cipher", ""))
        new_title = (it.get("new_title") or "").strip()
        new_desc = it.get("new_description")
        if not pid or not cipher:
            continue
        if new_title:
            conn.execute(
                """UPDATE title_queue SET new_title = ?, status = 'pending'
                   WHERE product_id = ? AND shop_cipher = ?""",
                (new_title[:TITLE_MAX], pid, cipher),
            )
            n += 1
        if new_desc is not None:
            conn.execute(
                """UPDATE title_queue SET new_description = ?, status = 'pending'
                   WHERE product_id = ? AND shop_cipher = ?""",
                (new_desc, pid, cipher),
            )
            if not new_title:
                n += 1
    conn.commit()
    conn.close()
    return n


def push_listing(
    token: str,
    cipher: str,
    product_id: str,
    title: str | None = None,
    description: str | None = None,
) -> tuple[bool, str]:
    body: dict = {}
    if title:
        body["title"] = title[:TITLE_MAX]
    if description:
        body["description"] = description
    if not body:
        return False, "无修改内容"
    path = f"/product/202309/products/{product_id}/partial_edit"
    r = api_post(path, token, {"shop_cipher": cipher}, body)
    if r.get("code") == 0:
        return True, ""
    return False, r.get("message", str(r))[:200]


def push_title(token: str, cipher: str, product_id: str, title: str) -> tuple[bool, str]:
    return push_listing(token, cipher, product_id, title=title)


def push_approved(ids: list[int] | None = None) -> dict:
    init_db()
    conn = connect()
    _migrate_title_queue(conn)
    if ids:
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT * FROM title_queue WHERE id IN ({placeholders}) AND status = 'pending'",
            ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM title_queue WHERE status = 'pending'"
        ).fetchall()
    conn.close()

    token = auth.access_token()
    ok = fail = skip = 0
    errors: list[str] = []
    now = int(time.time())

    for row in rows:
        new_title = (row["new_title"] or "").strip()
        old_title = (row["old_title"] or "").strip()
        new_desc = row["new_description"] if "new_description" in row.keys() else None
        old_desc = row["old_description"] if "old_description" in row.keys() else None
        if new_desc is not None:
            new_desc = (new_desc or "").strip()
        if old_desc is not None:
            old_desc = (old_desc or "").strip()
        row_id = row["id"]
        product_id = row["product_id"]
        shop_cipher = row["shop_cipher"]

        title_changed = bool(new_title and new_title != old_title)
        desc_changed = bool(
            new_desc is not None and old_desc is not None and new_desc != old_desc
        )
        if not title_changed and not desc_changed:
            conn = connect()
            try:
                conn.execute(
                    "UPDATE title_queue SET status = 'skipped', error = '内容未修改' WHERE id = ?",
                    (row_id,),
                )
                conn.commit()
            finally:
                conn.close()
            skip += 1
            continue

        push_title_val = new_title if title_changed else None
        push_desc_val = new_desc if desc_changed else None
        success, err = push_listing(
            token, shop_cipher, product_id,
            title=push_title_val,
            description=push_desc_val,
        )
        conn = connect()
        try:
            if success:
                conn.execute(
                    """UPDATE title_queue SET status = 'pushed', pushed_at = ?, error = NULL
                       WHERE id = ?""",
                    (now, row_id),
                )
                if title_changed:
                    conn.execute(
                        """UPDATE products SET product_name = ?, updated_at = ?
                           WHERE product_id = ? AND shop_cipher = ?""",
                        (new_title, now, product_id, shop_cipher),
                    )
                ok += 1
            else:
                conn.execute(
                    "UPDATE title_queue SET status = 'failed', error = ? WHERE id = ?",
                    (err, row_id),
                )
                fail += 1
                errors.append(f"{product_id}: {err}")
            conn.commit()
        finally:
            conn.close()
        delay = float(get("promotion.push_delay_sec", 1.2))
        time.sleep(delay)

    return {"ok": ok, "fail": fail, "skip": skip, "errors": errors}


def build_review_html(output: Path | None = None) -> Path:
    items = load_queue("pending")
    out = output or OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    data_json = json.dumps(items, ensure_ascii=False)
    generated = time.strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>低动销标题优化</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 16px; background: #f5f5f5; }}
    h1 {{ font-size: 1.25rem; margin: 0 0 4px; }}
    .hint {{ color: #666; font-size: 13px; margin-bottom: 12px; }}
    .toolbar {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; align-items: center; }}
    button {{ padding: 8px 14px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; }}
    .btn-primary {{ background: #fe2c55; color: #fff; }}
    .btn-secondary {{ background: #fff; border: 1px solid #ddd; }}
    .card {{ background: #fff; border-radius: 8px; padding: 12px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
    .row {{ display: flex; gap: 12px; }}
    .thumb {{ width: 72px; height: 72px; object-fit: cover; border-radius: 6px; background: #eee; flex-shrink: 0; }}
    .meta {{ font-size: 12px; color: #888; margin-bottom: 6px; }}
    label {{ font-size: 12px; color: #666; display: block; margin-top: 8px; }}
    textarea {{ width: 100%; min-height: 56px; padding: 8px; border: 1px solid #ddd; border-radius: 6px; font-size: 13px; resize: vertical; }}
    .old {{ color: #999; font-size: 13px; line-height: 1.4; }}
    .chk {{ margin-top: 10px; font-size: 14px; }}
    #status {{ margin-left: 8px; font-size: 13px; color: #666; }}
  </style>
</head>
<body>
  <h1>低动销标题优化</h1>
  <p class="hint">生成于 {generated} · 勾选并修改标题后，点击「推送到 TikTok」</p>
  <div class="toolbar">
    <button class="btn-secondary" onclick="selectAll(true)">全选</button>
    <button class="btn-secondary" onclick="selectAll(false)">全不选</button>
    <button class="btn-primary" onclick="pushSelected()">推送到 TikTok</button>
    <span id="status"></span>
  </div>
  <div id="list"></div>
  <script>
    var items = {data_json};

    function esc(s) {{
      return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
    }}

    function render() {{
      var el = document.getElementById('list');
      if (!items.length) {{
        el.innerHTML = '<p class="hint">暂无待处理项，请先运行: python3 main.py products title-scan</p>';
        return;
      }}
      el.innerHTML = items.map(function(it, i) {{
        return '<div class="card" data-idx="' + i + '">' +
          '<div class="row">' +
            (it.image_url ? '<img class="thumb" src="' + esc(it.image_url) + '">' : '<div class="thumb"></div>') +
            '<div style="flex:1">' +
              '<div class="meta">' + esc(it.region) + ' · 近30天销量 ' + it.units_sold + ' · 库存 ' + it.stock +
                (it.seller_sku ? ' · SKU ' + esc(it.seller_sku) : '') + '</div>' +
              '<label>原标题</label><div class="old">' + esc(it.old_title) + '</div>' +
              '<label>新标题（可编辑，最多 255 字）</label>' +
              '<textarea id="title-' + i + '" maxlength="255" oninput="items[' + i + '].new_title=this.value">' + esc(it.new_title || it.suggested_title) + '</textarea>' +
              '<label class="chk"><input type="checkbox" id="chk-' + i + '" checked> 确认推送</label>' +
            '</div>' +
          '</div></div>';
      }}).join('');
    }}

    function selectAll(v) {{
      items.forEach(function(_, i) {{
        var c = document.getElementById('chk-' + i);
        if (c) c.checked = v;
      }});
    }}

    function pushSelected() {{
      var payload = [];
      items.forEach(function(it, i) {{
        var c = document.getElementById('chk-' + i);
        if (!c || !c.checked) return;
        var t = document.getElementById('title-' + i);
        payload.push({{
          id: it.id,
          product_id: it.product_id,
          shop_cipher: it.shop_cipher,
          new_title: t ? t.value.trim() : (it.new_title || '')
        }});
      }});
      if (!payload.length) {{ alert('请至少勾选一条'); return; }}
      document.getElementById('status').textContent = '推送中...';
      fetch('/api/titles/push', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{items: payload}})
      }}).then(function(r) {{ return r.json(); }}).then(function(res) {{
        if (!res.ok) {{ alert(res.error || '失败'); return; }}
        document.getElementById('status').textContent =
          '成功 ' + res.ok_count + ' · 失败 ' + res.fail_count + ' · 跳过 ' + res.skip_count;
        if (res.fail_count === 0) setTimeout(function() {{ location.reload(); }}, 800);
      }}).catch(function(e) {{ alert(e); }});
    }}

    render();
  </script>
</body>
</html>"""
    out.write_text(html, encoding="utf-8")
    return out
