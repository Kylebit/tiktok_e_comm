"""日报聚合：TK 队列 + Analytics + Ozon 待办（可选）。"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from core.config import ROOT, get
from core.db import connect, init_db
from modules.hub.feishu import feishu_config


def _row(*cells: str) -> list[dict]:
    return [{"tag": "text", "text": t} for t in cells if t is not None]


def _link(text: str, href: str) -> dict:
    return {"tag": "a", "text": text, "href": href}


def _load_ozon_pending(ozon_data_dir: str) -> dict:
    base = Path(ozon_data_dir)
    if not base.is_dir():
        return {"price_review": 0, "promo_review": 0, "unmigrated": 0}
    out = {"price_review": 0, "promo_review": 0, "unmigrated": 0}
    pr = base / "pending_price_review.json"
    if pr.is_file():
        try:
            data = json.loads(pr.read_text(encoding="utf-8"))
            out["price_review"] = len(data.get("rows") or [])
        except json.JSONDecodeError:
            pass
    promo = base / "pending_promo_review.json"
    if promo.is_file():
        try:
            data = json.loads(promo.read_text(encoding="utf-8"))
            rows = data.get("rows") or data if isinstance(data, list) else []
            out["promo_review"] = len(rows)
        except json.JSONDecodeError:
            pass
    tk_map = base / "tk_sku_map.json"
    migrated = base / "migrated_offers.json"
    if tk_map.is_file():
        try:
            tk = json.loads(tk_map.read_text(encoding="utf-8"))
            mig = set(json.loads(migrated.read_text(encoding="utf-8"))) if migrated.is_file() else set()
            out["unmigrated"] = sum(
                1 for v in tk.values()
                if isinstance(v, dict) and (v.get("seller_sku") or "") not in mig
            )
        except json.JSONDecodeError:
            pass
    return out


def collect_snapshot() -> dict:
    """汇总当前状态，供日报与 Web 使用。"""
    from core import auth
    from modules.products import analytics as analytics_mod
    from modules.products import deactivate as deact_mod
    from modules.products import images as image_mod
    from modules.products import promotions as promo_mod
    from modules.products import titles as title_mod

    init_db()
    conn = connect()
    product_stats = conn.execute(
        """SELECT s.region, COUNT(DISTINCT p.product_id) AS products,
                  SUM(CASE WHEN p.status = 'ACTIVATE' THEN 1 ELSE 0 END) AS active_skus
           FROM products p JOIN shops s ON s.cipher = p.shop_cipher
           GROUP BY s.region ORDER BY s.region"""
    ).fetchall()
    conn.close()

    analytics = analytics_mod.summary()
    cfg = feishu_config()
    ozon = _load_ozon_pending(cfg["ozon_data_dir"])

    token_ok = True
    token_note = ""
    try:
        tok = auth.load_token()
        if auth.is_access_expired(tok):
            token_ok = False
            token_note = "Access Token 已过期，请运行 python3 main.py auth"
        else:
            exp = auth.access_expires_at(tok)
            if exp:
                import time as _time
                days = int((exp.timestamp() - _time.time()) / 86400)
                if days <= 3:
                    token_note = f"Token 约 {days} 天后过期"
    except Exception as e:
        token_ok = False
        token_note = str(e)[:120]

    pending = {
        "titles": len(title_mod.load_queue("pending")),
        "promos": len(promo_mod.load_queue("pending")),
        "deactivate": len(deact_mod.load_queue("pending")),
        "images": len(image_mod.load_active_queue()),
    }
    pending_total = sum(pending.values()) + ozon["price_review"] + ozon["promo_review"]

    return {
        "generated_at": int(time.time()),
        "date": date.today().isoformat(),
        "token_ok": token_ok,
        "token_note": token_note,
        "regions": [dict(r) for r in product_stats],
        "analytics": analytics,
        "pending": pending,
        "pending_total": pending_total,
        "ozon": ozon,
        "console_base_url": cfg["console_base_url"],
    }


def build_feishu_post(snapshot: dict | None = None) -> tuple[str, list[list[dict]]]:
    snap = snapshot or collect_snapshot()
    base = snap["console_base_url"]
    title = f"跨境运营日报 · {snap['date']}"

    rows: list[list[dict]] = []
    rows.append(_row("📊 ", "数据快照（TikTok 东南亚）", "\n"))

    segs = (snap.get("analytics") or {}).get("segments") or {}
    if segs:
        seg_line = " · ".join(f"{k}类 {v}" for k, v in sorted(segs.items()))
        rows.append(_row(f"Analytics 28d：{seg_line}\n"))
    else:
        rows.append(_row("Analytics：尚未同步，运行 products analytics-sync\n"))

    for r in snap.get("regions") or []:
        rows.append(_row(f"  {r.get('region')}：{r.get('products')} 商品 / {r.get('active_skus')} 在售 SKU\n"))

    p = snap["pending"]
    oz = snap["ozon"]
    rows.append(_row("\n⏳ ", "待你确认 / 跟进", "\n"))
    rows.append(_row(
        f"  Listing 优化 {p['titles']} · 促销 {p['promos']} · 下架 {p['deactivate']} · 主图 {p['images']}\n"
    ))
    if oz["price_review"] or oz["promo_review"] or oz["unmigrated"]:
        rows.append(_row(
            f"  Ozon 改价 {oz['price_review']} · 促销 {oz['promo_review']} · 待搬运 {oz['unmigrated']}\n"
        ))
    if snap["pending_total"] == 0:
        rows.append(_row("  （暂无待办，一切正常）\n"))

    rows.append(_row("\n🔗 ", "快捷入口", "\n"))
    rows.append([
        _link("Listing", f"{base}/titles"),
        {"tag": "text", "text": " · "},
        _link("促销", f"{base}/promotions"),
        {"tag": "text", "text": " · "},
        _link("主图", f"{base}/images"),
        {"tag": "text", "text": " · "},
        _link("Analytics", f"{base}/analytics"),
        {"tag": "text", "text": "\n"},
    ])

    if snap.get("token_note"):
        rows.append(_row(f"\n⚠️ {snap['token_note']}\n"))

    return title, rows


def save_digest_log(snapshot: dict) -> Path:
    log_dir = ROOT / "data" / "digest"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"digest_{snapshot['date']}.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def preview_text() -> str:
    title, rows = build_feishu_post()
    lines = [title, ""]
    for row in rows:
        lines.append("".join(c.get("text", c.get("href", "")) for c in row))
    return "\n".join(lines)
