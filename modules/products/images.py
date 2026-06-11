"""B 类低 CTR 商品：AI 主图候选 → 预览下载 → 人工上传 Seller Center。"""

from __future__ import annotations

import json
import re
import time
import zipfile
from pathlib import Path

from core import auth
from core.api_client import get as api_get
from core.config import ROOT, get
from core.db import connect, init_db
from modules.products import analytics as analytics_mod
from modules.products import image_ai


def _migrate(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS image_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL,
            shop_cipher TEXT NOT NULL,
            region TEXT,
            product_name TEXT,
            image_url TEXT,
            seller_sku TEXT,
            click_through_rate REAL,
            ctr_median REAL,
            analytics_orders INTEGER DEFAULT 0,
            source_image_urls TEXT,
            image_prompt TEXT,
            generated_paths TEXT,
            selected_path TEXT,
            status TEXT DEFAULT 'pending',
            error TEXT,
            created_at INTEGER,
            done_at INTEGER,
            UNIQUE(product_id, shop_cipher)
        )"""
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(image_queue)").fetchall()}
    if "source_image_urls" not in cols:
        conn.execute("ALTER TABLE image_queue ADD COLUMN source_image_urls TEXT")
    if "scan_source" not in cols:
        conn.execute("ALTER TABLE image_queue ADD COLUMN scan_source TEXT DEFAULT 'b_class'")


def search_products(
    query: str | None = None,
    region: str | None = None,
    limit: int = 40,
) -> list[dict]:
    """从本地商品库搜索（任意在售 SKU）。"""
    init_db()
    conn = connect()
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
    if query:
        q = f"%{query.strip()}%"
        sql += " AND (p.product_name LIKE ? OR p.seller_sku LIKE ? OR p.product_id LIKE ?)"
        params.extend([q, q, q])
    sql += " GROUP BY p.product_id, p.shop_cipher ORDER BY stock_total DESC LIMIT ?"
    params.append(max(1, min(limit, 100)))
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def _upsert_queue_row(
    conn,
    *,
    product_id: str,
    shop_cipher: str,
    region: str | None,
    product_name: str,
    image_url: str,
    seller_sku: str,
    ctr: float,
    ctr_median: float,
    orders: int,
    source_urls: list[str],
    variant_meta: list[dict],
    paths: list[str],
    err_note: str,
    scan_source: str,
    now: int,
) -> None:
    conn.execute(
        """INSERT INTO image_queue (
            product_id, shop_cipher, region, product_name, image_url, seller_sku,
            click_through_rate, ctr_median, analytics_orders, source_image_urls,
            image_prompt, generated_paths, status, error, scan_source, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_id, shop_cipher) DO UPDATE SET
            region=excluded.region,
            product_name=excluded.product_name,
            image_url=excluded.image_url,
            seller_sku=excluded.seller_sku,
            click_through_rate=excluded.click_through_rate,
            ctr_median=excluded.ctr_median,
            analytics_orders=excluded.analytics_orders,
            source_image_urls=excluded.source_image_urls,
            image_prompt=excluded.image_prompt,
            generated_paths=excluded.generated_paths,
            status=excluded.status,
            error=excluded.error,
            scan_source=excluded.scan_source,
            created_at=excluded.created_at,
            selected_path=NULL,
            done_at=NULL""",
        (
            product_id,
            shop_cipher,
            region,
            product_name,
            image_url,
            seller_sku,
            ctr,
            ctr_median,
            orders,
            json.dumps(source_urls, ensure_ascii=False),
            image_ai.format_variant_note(variant_meta) if variant_meta else "",
            json.dumps(paths, ensure_ascii=False),
            "pending" if paths else "failed",
            err_note or None,
            scan_source,
            now,
        ),
    )


def generate_for_product(
    product_id: str,
    shop_cipher: str,
    *,
    main_recipe_ids: list[str] | None = None,
    custom_scenes: list[dict] | None = None,
    include_default_scenes: bool = False,
    explore_recipe_ids: list[str] | None = None,
    use_explore_recipes: bool = False,
    scan_source: str = "manual",
    quiet: bool = False,
) -> bool:
    """为指定商品生成主图+自定义场景（不限制 B 类）。"""
    init_db()
    conn = connect()
    _migrate(conn)
    if not image_ai.image_enabled():
        conn.close()
        raise RuntimeError("未配置 Photoroom API Key")

    token = auth.access_token()
    now = int(time.time())
    row = conn.execute(
        """SELECT MAX(p.product_name) AS product_name, MAX(p.image_url) AS image_url,
                  MAX(p.seller_sku) AS seller_sku, MAX(s.region) AS region
           FROM products p JOIN shops s ON s.cipher = p.shop_cipher
           WHERE p.product_id = ? AND p.shop_cipher = ?""",
        (product_id, shop_cipher),
    ).fetchone()
    region = row["region"] if row else None
    product_name = (row["product_name"] if row else "") or ""

    paths: list[str] = []
    source_urls: list[str] = []
    variant_meta: list[dict] = []
    err_note = ""
    try:
        detail = _fetch_detail(token, shop_cipher, product_id)
        if not product_name:
            product_name = detail.get("title") or ""
        out_dir = image_ai.output_dir_for(product_id, shop_cipher)
        paths, source_urls, variant_meta = image_ai.generate_variants_from_listing(
            detail,
            out_dir,
            region=region,
            product_name=product_name,
            main_recipe_ids=main_recipe_ids,
            custom_scenes=custom_scenes,
            include_default_scenes=include_default_scenes,
            explore_recipe_ids=explore_recipe_ids,
            use_eval_recipes=False if (main_recipe_ids is not None or explore_recipe_ids or custom_scenes) else None,
            use_explore_recipes=use_explore_recipes,
        )
        if not quiet:
            print(f"  ✓ {region} {product_name[:40]} · {len(paths)} 张")
    except Exception as e:
        err_note = str(e)[:300]
        if not quiet:
            print(f"  失败: {err_note}")

    _upsert_queue_row(
        conn,
        product_id=product_id,
        shop_cipher=shop_cipher,
        region=region,
        product_name=product_name,
        image_url=source_urls[0] if source_urls else (row["image_url"] if row else ""),
        seller_sku=(row["seller_sku"] if row else "") or "",
        ctr=0,
        ctr_median=0,
        orders=0,
        source_urls=source_urls,
        variant_meta=variant_meta,
        paths=paths,
        err_note=err_note,
        scan_source=scan_source,
        now=now,
    )
    conn.commit()
    conn.close()
    return bool(paths)


def generate_for_products(
    items: list[dict],
    *,
    main_recipe_ids: list[str] | None = None,
    custom_scenes: list[dict] | None = None,
    include_default_scenes: bool = False,
    explore_recipe_ids: list[str] | None = None,
    use_explore_recipes: bool = False,
    quiet: bool = False,
) -> int:
    n = 0
    for it in items:
        ok = generate_for_product(
            it["product_id"],
            it["shop_cipher"],
            main_recipe_ids=main_recipe_ids,
            custom_scenes=custom_scenes,
            include_default_scenes=include_default_scenes,
            explore_recipe_ids=explore_recipe_ids,
            use_explore_recipes=use_explore_recipes,
            scan_source=it.get("scan_source") or "manual",
            quiet=quiet,
        )
        if ok:
            n += 1
    return n


def _fetch_detail(token: str, cipher: str, product_id: str) -> dict:
    r = api_get(f"/product/202309/products/{product_id}", token, {"shop_cipher": cipher})
    if r.get("code") != 0:
        raise RuntimeError(r.get("message", f"商品详情失败 {product_id}"))
    return r.get("data") or {}


def scan_b_class(
    limit: int = 10,
    region: str | None = None,
    variants: int | None = None,
    quiet: bool = False,
) -> int:
    """28 天 Analytics B 类（低 CTR · 0 单）→ 基于 listing main_images 白底候选。"""
    init_db()
    conn = connect()
    _migrate(conn)

    def _log(msg: str) -> None:
        if not quiet:
            print(msg)

    if not image_ai.image_enabled():
        conn.close()
        raise RuntimeError(
            "未配置 Photoroom API Key。请在 config/settings.json 填写 "
            "images.photoroom_api_key（基于现有 listing 图抠白底，非文生图）"
        )

    _log("\n[1/3] 同步 28 天 Analytics...")
    analytics_mod.sync_all(region=region, quiet=quiet)

    _log("\n[2/3] 筛选 B 类（低 CTR · 0 单）...")
    rows = analytics_mod.load_analytics(
        segment=analytics_mod.SEGMENT_LOW_EXPOSURE,
        region=region,
    )
    candidates: list[dict] = []
    for row in rows:
        if int(row.get("stock_total") or 0) < 1:
            continue
        candidates.append(row)

    candidates.sort(key=lambda x: (float(x.get("click_through_rate") or 0), -int(x.get("stock_total") or 0)))
    candidates = candidates[:limit]
    if not candidates:
        conn.close()
        if not quiet:
            print("  未找到 B 类（低曝光）商品")
        return 0

    token = auth.access_token()
    now = int(time.time())
    var_n = variants
    if var_n is None:
        var_n = int((get("images") or {}).get("variants_per_product") or 3)

    _log(f"\n[3/3] 基于 listing 原图生成白底候选 ({len(candidates)} 个 × 最多 {var_n} 张)...")
    n = 0
    ai_delay = float(get("ai", {}).get("request_delay_sec", 0.35))

    for i, row in enumerate(candidates):
        pid = row["product_id"]
        cipher = row["shop_cipher"]
        item = {
            "product_id": pid,
            "shop_cipher": cipher,
            "region": row.get("region"),
            "old_title": row.get("product_name") or "",
            "seller_sku": row.get("seller_sku") or "",
            "units_sold": 0,
            "stock": int(row.get("stock_total") or 0),
        }
        paths: list[str] = []
        source_urls: list[str] = []
        variant_meta: list[dict] = []
        err_note = ""
        try:
            detail = _fetch_detail(token, cipher, pid)
            out_dir = image_ai.output_dir_for(pid, cipher)
            paths, source_urls, variant_meta = image_ai.generate_variants_from_listing(
                detail,
                out_dir,
                count=var_n,
                region=item.get("region"),
                product_name=row.get("product_name") or detail.get("title") or "",
            )
            if not quiet:
                ctr = float(row.get("click_through_rate") or 0) * 100
                print(
                    f"  [{i + 1}/{len(candidates)}] {item['region']} CTR {ctr:.2f}% "
                    f"✓ {len(paths)} 张（listing 原图 {len(source_urls)} 张）"
                )
        except Exception as e:
            err_note = str(e)[:300]
            if not quiet:
                print(f"  [{i + 1}/{len(candidates)}] {item['region']} 失败: {err_note}")

        conn.execute(
            """INSERT INTO image_queue (
                product_id, shop_cipher, region, product_name, image_url, seller_sku,
                click_through_rate, ctr_median, analytics_orders, source_image_urls,
                image_prompt, generated_paths, status, error, scan_source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id, shop_cipher) DO UPDATE SET
                region=excluded.region,
                product_name=excluded.product_name,
                image_url=excluded.image_url,
                seller_sku=excluded.seller_sku,
                click_through_rate=excluded.click_through_rate,
                ctr_median=excluded.ctr_median,
                analytics_orders=excluded.analytics_orders,
                source_image_urls=excluded.source_image_urls,
                image_prompt=excluded.image_prompt,
                generated_paths=excluded.generated_paths,
                status=excluded.status,
                error=excluded.error,
                scan_source=excluded.scan_source,
                created_at=excluded.created_at,
                selected_path=NULL,
                done_at=NULL""",
            (
                pid,
                cipher,
                row.get("region"),
                row.get("product_name") or "",
                (source_urls[0] if source_urls else row.get("image_url") or ""),
                row.get("seller_sku") or "",
                float(row.get("click_through_rate") or 0),
                float(row.get("ctr_median") or 0),
                int(row.get("orders") or 0),
                json.dumps(source_urls, ensure_ascii=False),
                image_ai.format_variant_note(variant_meta) if variant_meta else "",
                json.dumps(paths, ensure_ascii=False),
                "pending" if paths else "failed",
                err_note or None,
                "b_class",
                now,
            ),
        )
        if paths:
            n += 1
        if ai_delay > 0 and i + 1 < len(candidates):
            time.sleep(ai_delay)

    conn.commit()
    conn.close()
    if not quiet:
        print(f"  ✅ 已生成 {n} 个商品的主图候选")
        print(f"  预览: python3 main.py serve --page images")
    return n


def _parse_json_list(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except json.JSONDecodeError:
        return []


def _parse_paths(raw) -> list[str]:
    return _parse_json_list(raw)


def load_active_queue(region: str | None = None) -> list[dict]:
    """待处理 + 失败（供页面展示；不含 done/skipped）。"""
    pending = load_queue("pending", region=region)
    failed = load_queue("failed", region=region)
    return pending + failed


def load_queue(status: str | None = "pending", region: str | None = None) -> list[dict]:
    init_db()
    conn = connect()
    _migrate(conn)
    sql = "SELECT * FROM image_queue"
    params: list = []
    clauses: list[str] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if region:
        clauses.append("region = ?")
        params.append(region.upper())
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY click_through_rate ASC, created_at DESC"
    rows = []
    for r in conn.execute(sql, params).fetchall():
        item = dict(r)
        item["generated_paths"] = _parse_paths(item.get("generated_paths"))
        item["source_image_urls"] = _parse_json_list(item.get("source_image_urls"))
        rows.append(item)
    conn.close()
    return rows


def resolve_image_path(rel_path: str) -> Path | None:
    """只允许读取 exports/main_images 下的文件。"""
    if not rel_path or ".." in rel_path:
        return None
    p = (ROOT / rel_path).resolve()
    allowed = (ROOT / "exports" / "main_images").resolve()
    try:
        p.relative_to(allowed)
    except ValueError:
        return None
    return p if p.is_file() else None


def mark_done(row_id: int, selected_path: str | None = None) -> bool:
    init_db()
    conn = connect()
    _migrate(conn)
    now = int(time.time())
    cur = conn.execute(
        """UPDATE image_queue SET status = 'done', selected_path = ?, done_at = ?, error = NULL
           WHERE id = ? AND status = 'pending'""",
        (selected_path or "", now, row_id),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def mark_skipped(row_id: int) -> bool:
    init_db()
    conn = connect()
    _migrate(conn)
    cur = conn.execute(
        "UPDATE image_queue SET status = 'skipped' WHERE id = ? AND status IN ('pending', 'failed')",
        (row_id,),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def _queue_row_by_id(row_id: int) -> dict | None:
    rows = load_queue(status=None)
    return next((r for r in rows if r["id"] == row_id), None)


def export_slot_zip(row_id: int) -> Path | None:
    """按 TikTok 槽位顺序打包已生成图片 + 上传说明。"""
    row = _queue_row_by_id(row_id)
    if not row:
        return None
    paths = row.get("generated_paths") or []
    if not paths:
        return None
    meta: list[dict] = []
    try:
        raw = row.get("image_prompt") or "[]"
        parsed = json.loads(raw)
        meta = parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        pass

    entries: list[tuple[int, str, str, Path]] = []
    for m in meta:
        if not m or m.get("partial_errors") or m.get("error"):
            continue
        pi = m.get("path_index")
        if pi is None or pi < 0 or pi >= len(paths):
            continue
        fp = resolve_image_path(paths[pi])
        if not fp:
            continue
        slot = int(m.get("tiktok_slot") or 99)
        entries.append((slot, m.get("recipe_id") or "img", m.get("label") or "", fp))
    if not entries:
        return None
    entries.sort(key=lambda x: (x[0], x[1]))

    out_dir = ROOT / "exports" / "image_zips"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_pid = re.sub(r"[^\w\-]+", "_", row.get("product_id") or "product")[:24]
    zip_path = out_dir / f"{safe_pid}_queue{row_id}.zip"

    lines = [
        "TikTok Shop 图片上传顺序建议（探索阶段 · 按槽位排序）",
        f"商品: {row.get('product_name') or ''}",
        f"SKU: {row.get('seller_sku') or ''}",
        "",
    ]
    for slot, rid, label, _fp in entries:
        lines.append(f"  槽位 {slot:02d} · {label} ({rid})")
    lines.extend(["", "槽位 6–9（开箱/信息图/变体/评价）需人工补充。", ""])

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("00_UPLOAD_ORDER.txt", "\n".join(lines))
        for slot, rid, label, fp in entries:
            safe_label = re.sub(r"[^\w\-]+", "_", label)[:36] or "img"
            arcname = f"{slot:02d}_{rid}_{safe_label}{fp.suffix}"
            zf.write(fp, arcname)
    return zip_path
