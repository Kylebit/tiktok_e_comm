"""解析飞书 @ 机器人指令并生成回复。"""

from __future__ import annotations

import re


def normalize_user_text(raw: str) -> str:
    """去掉 @机器人 占位符。"""
    t = raw or ""
    t = re.sub(r"@_\w+\s*", "", t)
    t = re.sub(r"@\S+\s*", "", t)
    return t.strip()


def parse_command(text: str) -> tuple[str, str]:
    t = normalize_user_text(text)
    if not t:
        return "help", ""
    lower = t.lower()
    if t.startswith("确认促销"):
        return "confirm_promo", t.replace("确认促销", "", 1).strip()
    if t.startswith("确认下架"):
        return "confirm_deact", t.replace("确认下架", "", 1).strip()
    mapping = {
        "帮助": "help",
        "help": "help",
        "日报": "digest",
        "digest": "digest",
        "状态": "status",
        "status": "status",
        "促销": "promo",
        "promo": "promo",
        "下架": "deact",
        "deact": "deact",
    }
    parts = t.split(None, 1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    cmd = mapping.get(head, mapping.get(head.lower(), head.lower()))
    return cmd, rest


def _parse_indices(args: str) -> list[int]:
    out: list[int] = []
    for part in re.split(r"[\s,，、]+", args.strip()):
        if not part:
            continue
        if part.isdigit():
            out.append(int(part))
    return out


def handle_command(text: str) -> str:
    cmd, args = parse_command(text)
    if cmd in ("help",):
        return _help_text()
    if cmd in ("digest",):
        from modules.hub import digest as digest_mod
        return digest_mod.preview_text()
    if cmd in ("status",):
        from modules.hub import digest as digest_mod
        s = digest_mod.collect_snapshot()
        p = s["pending"]
        oz = s["ozon"]
        lines = [
            f"📊 {s['date']} 状态",
            f"TikTok 待办：Listing {p['titles']} · 促销 {p['promos']} · 下架 {p['deactivate']} · 主图 {p['images']}",
            f"Ozon：改价 {oz['price_review']} · 促销 {oz['promo_review']} · 待搬运 {oz['unmigrated']}",
        ]
        if s.get("token_note"):
            lines.append(f"⚠️ {s['token_note']}")
        base = s.get("console_base_url") or ""
        if base:
            lines.append(f"控制台 {base}")
        return "\n".join(lines)
    if cmd in ("promo",):
        return _list_promos()
    if cmd in ("deact",):
        return _list_deactivate()
    if cmd in ("confirm_promo",):
        return _confirm_promo(args)
    if cmd in ("confirm_deact",):
        return _confirm_deact(args)
    return f"未识别指令「{cmd}」。发送「帮助」查看可用命令。"


def _help_text() -> str:
    return (
        "🤖 跨境运营助手 · 指令\n"
        "━━━━━━━━━━━━━━\n"
        "日报 — 发送今日运营摘要\n"
        "状态 — 待办数量\n"
        "促销 — 列出待确认促销（带编号）\n"
        "下架 — 列出待确认下架\n"
        "确认促销 1 3 — 推送编号 1、3 的促销\n"
        "确认下架 1 — 推送编号 1 的下架\n"
        "帮助 — 本说明\n"
        "━━━━━━━━━━━━━━\n"
        "在群里 @机器人 + 指令即可。"
    )


def _list_promos(limit: int = 8) -> str:
    from modules.products import promotions as promo_mod
    rows = promo_mod.load_queue("pending")[:limit]
    if not rows:
        return "暂无待确认促销。"
    lines = ["📋 待确认促销（回复：确认促销 编号）", ""]
    for i, r in enumerate(rows, 1):
        name = (r.get("product_name") or "")[:36]
        act = r.get("action") or "adjust"
        reg = r.get("region") or ""
        sku = r.get("seller_sku") or ""
        lines.append(f"[{i}] {reg} · {act} · SKU {sku}")
        lines.append(f"    {name}")
    if len(promo_mod.load_queue("pending")) > limit:
        lines.append(f"\n… 还有 {len(promo_mod.load_queue('pending')) - limit} 条，请打开控制台")
    return "\n".join(lines)


def _list_deactivate(limit: int = 8) -> str:
    from modules.products import deactivate as deact_mod
    rows = deact_mod.load_queue("pending")[:limit]
    if not rows:
        return "暂无待确认下架。"
    lines = ["📋 待确认下架（回复：确认下架 编号）", ""]
    for i, r in enumerate(rows, 1):
        name = (r.get("product_name") or "")[:36]
        reg = r.get("region") or ""
        lines.append(f"[{i}] {reg} · {name}")
    return "\n".join(lines)


def _confirm_promo(args: str) -> str:
    from modules.products import promotions as promo_mod
    idxs = _parse_indices(args)
    if not idxs:
        return "请指定编号，例如：确认促销 1 3"
    rows = promo_mod.load_queue("pending")
    ids: list[int] = []
    for n in idxs:
        if 1 <= n <= len(rows):
            ids.append(int(rows[n - 1]["id"]))
    if not ids:
        return "编号无效，先发送「促销」查看列表。"
    try:
        stats = promo_mod.push_approved(ids)
        return (
            f"✅ 促销推送完成\n"
            f"成功 {stats.get('ok', 0)} · 失败 {stats.get('fail', 0)} · 跳过 {stats.get('skip', 0)}"
        )
    except Exception as e:
        return f"❌ 推送失败：{str(e)[:200]}"


def _confirm_deact(args: str) -> str:
    from modules.products import deactivate as deact_mod
    idxs = _parse_indices(args)
    if not idxs:
        return "请指定编号，例如：确认下架 1"
    rows = deact_mod.load_queue("pending")
    ids: list[int] = []
    for n in idxs:
        if 1 <= n <= len(rows):
            ids.append(int(rows[n - 1]["id"]))
    if not ids:
        return "编号无效，先发送「下架」查看列表。"
    try:
        stats = deact_mod.push_approved(ids)
        return f"✅ 下架推送完成 · 成功 {stats.get('ok', 0)} · 失败 {stats.get('fail', 0)}"
    except Exception as e:
        return f"❌ 下架失败：{str(e)[:200]}"
