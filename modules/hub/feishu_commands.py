"""Feishu bot command parsing for product image and 1688 purchase links."""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

URL_RE = re.compile(r"(https?://[^\s]+)")
SAVE_LINK_HINT_RE = re.compile(r"^(\d{4,})\s*采购链接", re.IGNORECASE)
GET_LINK_HINT_RE = re.compile(r"^发?\s*(\d{4,})\s*的?\s*采购链接$", re.IGNORECASE)
GET_IMAGE_HINT_RE = re.compile(r"^发?\s*(\d{4,})\s*的?\s*主图$", re.IGNORECASE)


def normalize_user_text(raw: str) -> str:
    text = raw or ""
    text = re.sub(r"@_\w+\s*", "", text)
    text = re.sub(r"@\S+\s*", "", text)
    return text.strip()


def extract_all_skus(text: str) -> list[str]:
    normalized = normalize_user_text(text)
    if not normalized:
        return []
    skus: list[str] = []
    for token in normalized.split():
        if token.isdigit() and len(token) >= 4:
            skus.append(token)
            continue
        break
    return skus


def extract_url(text: str) -> str:
    normalized = normalize_user_text(text)
    match = URL_RE.search(normalized)
    return match.group(1) if match else ""


def parse_command(text: str) -> tuple[str, str]:
    normalized = normalize_user_text(text)
    if not normalized:
        return "help", ""

    lowered = normalized.lower()
    if lowered in {"帮助", "help"}:
        return "help", ""
    if lowered in {"日报", "digest"}:
        return "digest", ""
    if lowered in {"状态", "status"}:
        return "status", ""
    if lowered in {"促销", "promo"}:
        return "promo", ""
    if lowered in {"下架", "deact"}:
        return "deact", ""
    if lowered.startswith("确认促销"):
        return "confirm_promo", normalized.replace("确认促销", "", 1).strip()
    if lowered.startswith("确认下架"):
        return "confirm_deact", normalized.replace("确认下架", "", 1).strip()

    save_match = SAVE_LINK_HINT_RE.match(normalized)
    url = extract_url(normalized)
    if save_match and url:
        return "save_purchase_link", f"{save_match.group(1)}|{url}"

    get_link_match = GET_LINK_HINT_RE.match(normalized)
    if get_link_match:
        return "get_purchase_link", get_link_match.group(1)

    get_image_match = GET_IMAGE_HINT_RE.match(normalized)
    if get_image_match:
        return "send_main_image", get_image_match.group(1)

    skus = extract_all_skus(normalized)
    if url and skus:
        if len(skus) == 1:
            return "save_purchase_link", f"{skus[0]}|{url}"
        return "batch_save_link", f"{','.join(skus)}|{url}"

    if skus:
        if len(skus) == 1:
            return "send_both", skus[0]
        return "batch_send_both", ",".join(skus)

    return "help", ""


def _help_text() -> str:
    return "\n".join(
        [
            "可用命令：",
            "1. 0927 -> 发送主图并返回采购链接",
            "2. 0927 https://qr.1688.com/... -> 保存采购链接",
            "3. 0001 0002 https://qr.1688.com/... -> 批量保存采购链接",
            "4. 发0927采购链接 / 发0927主图",
            "5. 帮助 / 状态 / 日报 / 促销 / 下架",
        ]
    )


def _status_text() -> str:
    from modules.hub import digest as digest_mod

    snapshot = digest_mod.collect_snapshot()
    pending = snapshot["pending"]
    ozon = snapshot["ozon"]
    lines = [
        f"状态快照 {snapshot['date']}",
        (
            f"TikTok 待处理: 标题 {pending['titles']} / 促销 {pending['promos']} / "
            f"下架 {pending['deactivate']} / 主图 {pending['images']}"
        ),
        (
            f"Ozon 待处理: 价格复核 {ozon['price_review']} / 促销复核 {ozon['promo_review']} / "
            f"未迁移 {ozon['unmigrated']}"
        ),
    ]
    if snapshot.get("token_note"):
        lines.append(f"Token: {snapshot['token_note']}")
    base = snapshot.get("console_base_url") or ""
    if base:
        lines.append(f"控制台: {base}")
    return "\n".join(lines)


def _list_promos(limit: int = 8) -> str:
    from modules.products import promotions as promo_mod

    rows = promo_mod.load_queue("pending")
    if not rows:
        return "当前没有待确认的促销任务。"
    lines = ["待确认促销：", ""]
    for idx, row in enumerate(rows[:limit], 1):
        name = (row.get("product_name") or "")[:36]
        action = row.get("action") or "adjust"
        region = row.get("region") or ""
        sku = row.get("seller_sku") or ""
        lines.append(f"[{idx}] {region} / {action} / SKU {sku}")
        lines.append(f"    {name}")
    if len(rows) > limit:
        lines.append(f"\n还有 {len(rows) - limit} 条未展示。")
    return "\n".join(lines)


def _list_deactivate(limit: int = 8) -> str:
    from modules.products import deactivate as deact_mod

    rows = deact_mod.load_queue("pending")
    if not rows:
        return "当前没有待确认的下架任务。"
    lines = ["待确认下架：", ""]
    for idx, row in enumerate(rows[:limit], 1):
        region = row.get("region") or ""
        name = (row.get("product_name") or "")[:36]
        lines.append(f"[{idx}] {region} / {name}")
    return "\n".join(lines)


def _parse_indices(args: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"[\s,、]+", args.strip()):
        if part.isdigit():
            out.append(int(part))
    return out


def _confirm_promo(args: str) -> str:
    from modules.products import promotions as promo_mod

    indices = _parse_indices(args)
    if not indices:
        return "请输入要确认的促销序号，例如：确认促销 1 3"
    rows = promo_mod.load_queue("pending")
    ids = [int(rows[n - 1]["id"]) for n in indices if 1 <= n <= len(rows)]
    if not ids:
        return "没有匹配到可确认的促销序号。"
    try:
        stats = promo_mod.push_approved(ids)
    except Exception as exc:
        return f"促销提交失败：{str(exc)[:200]}"
    return f"促销已提交：成功 {stats.get('ok', 0)} / 失败 {stats.get('fail', 0)} / 跳过 {stats.get('skip', 0)}"


def _confirm_deact(args: str) -> str:
    from modules.products import deactivate as deact_mod

    indices = _parse_indices(args)
    if not indices:
        return "请输入要确认的下架序号，例如：确认下架 1"
    rows = deact_mod.load_queue("pending")
    ids = [int(rows[n - 1]["id"]) for n in indices if 1 <= n <= len(rows)]
    if not ids:
        return "没有匹配到可确认的下架序号。"
    try:
        stats = deact_mod.push_approved(ids)
    except Exception as exc:
        return f"下架提交失败：{str(exc)[:200]}"
    return f"下架已提交：成功 {stats.get('ok', 0)} / 失败 {stats.get('fail', 0)}"


def _get_db_path() -> str:
    from core.db import db_path

    return str(db_path())


def _connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_purchase_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS purchasing_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL,
            platform TEXT DEFAULT '1688',
            url TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            parent_sku TEXT,
            UNIQUE(sku, platform)
        )
        """
    )
    conn.commit()


def _choose_best_match(matches: Iterable[tuple | sqlite3.Row], sku: str) -> tuple | sqlite3.Row | None:
    matches = list(matches)
    if not matches:
        return None
    for row in matches:
        if str(row[1]) == sku:
            return row
    return min(matches, key=lambda row: len(str(row[1] or "")))


def _resolve_sku_record(sku: str) -> tuple[str, str]:
    from modules.hub import feishu_app as app_mod

    matches = app_mod._find_sku(sku)
    best = _choose_best_match(matches, sku)
    if best:
        seller_sku = str(best[1] or sku)
        parent_sku = sku if len(sku) == 4 and sku.isdigit() else seller_sku[-4:]
        return seller_sku, parent_sku
    return sku, sku[-4:] if len(sku) >= 4 else sku


def _handle_save_purchase_link(arg: str) -> str:
    try:
        sku, url = arg.split("|", 1)
    except ValueError:
        return "保存采购链接失败：参数格式不对。"
    sku = sku.strip()
    url = url.strip()
    if not sku or not url:
        return "保存采购链接失败：SKU 或链接为空。"

    canonical_sku, parent_sku = _resolve_sku_record(sku)
    conn = _connect_db()
    try:
        _ensure_purchase_table(conn)
        conn.execute(
            """
            INSERT INTO purchasing_links (sku, platform, url, parent_sku, created_at, updated_at)
            VALUES (?, '1688', ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(sku, platform) DO UPDATE SET
                url=excluded.url,
                parent_sku=excluded.parent_sku,
                updated_at=datetime('now')
            """,
            (canonical_sku, url, parent_sku),
        )
        conn.commit()
    finally:
        conn.close()
    return f"✅ 已保存 SKU {canonical_sku} 采购链接"


def _handle_batch_save_link(arg: str) -> str:
    try:
        sku_csv, url = arg.split("|", 1)
    except ValueError:
        return "批量保存采购链接失败：参数格式不对。"
    skus = [sku.strip() for sku in sku_csv.split(",") if sku.strip()]
    if not skus:
        return "批量保存采购链接失败：没有 SKU。"
    saved: list[str] = []
    for sku in skus:
        _handle_save_purchase_link(f"{sku}|{url}")
        saved.append(sku)
    return f"✅ 已批量保存 {len(saved)} 个 SKU 的采购链接：{', '.join(saved)}"


def _handle_get_purchase_link(sku: str) -> str:
    from modules.hub import feishu_app as app_mod

    return app_mod.reply_product_link(sku.strip())


def _handle_send_main_image(sku: str, message_id: str | None) -> str:
    if not message_id:
        return "需要 message_id 才能发图，请通过 @机器人 方式发送指令"
    from modules.hub import feishu_app as app_mod

    try:
        return app_mod.reply_product_image(message_id, sku.strip())
    except Exception:
        try:
            best = _choose_best_match(app_mod._find_sku(sku.strip()), sku.strip())
            if best:
                product_name = str(best[2] or best[1] or sku).strip()
                return f"[{product_name}] 图片暂时无法加载，请稍后再试"
        except Exception:
            pass
        return f"[{sku.strip()}] 图片暂时无法加载，请稍后再试"


def _handle_send_both(sku: str, message_id: str | None) -> str:
    image_status = _handle_send_main_image(sku, message_id)
    if image_status.startswith("需要 message_id"):
        return image_status
    link = _handle_get_purchase_link(sku)
    if image_status and link:
        return f"{image_status}\n\n{link}"
    return link or image_status or "未找到采购链接"


def _handle_batch_send_both(sku_csv: str, message_id: str | None) -> str:
    skus = [sku.strip() for sku in sku_csv.split(",") if sku.strip()]
    if not skus:
        return "没有可发送的 SKU"
    lines: list[str] = []
    for sku in skus:
        try:
            lines.append(_handle_send_both(sku, message_id))
        except Exception as exc:
            lines.append(f"{sku} 处理失败：{str(exc)[:120]}")
    return "\n\n".join(lines)


def handle_command(text: str, message_id: str | None = None) -> str:
    cmd, args = parse_command(text)
    if cmd == "help":
        return _help_text()
    if cmd == "digest":
        from modules.hub import digest as digest_mod

        return digest_mod.preview_text()
    if cmd == "status":
        return _status_text()
    if cmd == "promo":
        return _list_promos()
    if cmd == "deact":
        return _list_deactivate()
    if cmd == "confirm_promo":
        return _confirm_promo(args)
    if cmd == "confirm_deact":
        return _confirm_deact(args)
    if cmd == "save_purchase_link":
        return _handle_save_purchase_link(args)
    if cmd == "batch_save_link":
        return _handle_batch_save_link(args)
    if cmd == "get_purchase_link":
        return _handle_get_purchase_link(args)
    if cmd == "send_main_image":
        return _handle_send_main_image(args, message_id)
    if cmd == "send_both":
        return _handle_send_both(args, message_id)
    if cmd == "batch_send_both":
        return _handle_batch_send_both(args, message_id)
    return _help_text()
