"""飞书派单 → 发现 MX 候选 SKU → POP → 发审批卡（不自动发布）。"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.hub.feishu_app import app_ready, send_message
from modules.miaoshou.mx_feishu_approval import default_chat_id
from modules.miaoshou.mx_web_approval import mx_approval_url
from scripts.mx_pop_pricing import fetch_cny_mxn, quote_match_key
from scripts.orbit_mx_migrate_prep import catalog_row, prep_one
from scripts.orbit_send_mx_approval import DEFAULT_TASK, build_card_for_mk
from modules.miaoshou.mx_collect_match import discover_collect_ready, load_collect_index

LOG = ROOT / "data" / "mx_confirm" / "feishu_dispatch.log"
OUT = ROOT / "data" / "mx_confirm" / "feishu_dispatch_last.json"


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = msg.rstrip() + "\n"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line)
    print(msg, flush=True)


def _published_match_keys() -> set[str]:
    from modules.miaoshou.mx_web_approval import published_match_keys

    return published_match_keys()


def discover_candidates(*, prefix: str, limit: int) -> tuple[list[str], list[dict]]:
    """从采集箱扫描，只返回 prep=ready 的 SKU；待采集/无成本跳过且不计入 limit。"""
    skip = _published_match_keys()
    rate = fetch_cny_mxn()
    ready: list[str] = []
    skipped: list[dict] = []

    def _scan_pool(pool: list[str]) -> None:
        for mk in pool:
            if len(ready) >= limit:
                break
            prep = prep_one(mk, rate=rate)
            if prep.get("status") == "ready":
                ready.append(mk)
            else:
                skipped.append(
                    {
                        "mk": mk,
                        "reason": prep.get("status") or prep.get("reason"),
                        "pid": prep.get("product_id"),
                    }
                )

    try:
        index = load_collect_index()
        pool = discover_collect_ready(
            prefix=prefix, limit=500, skip=skip, index=index
        )
        if pool:
            _scan_pool(pool)
            return ready, skipped
    except Exception as exc:
        _log(f"collect discover fallback: {exc}")

    # 兜底：目录顺序扫描，仍只计 ready
    conn = sqlite3.connect(ROOT / "data" / "shop.db")
    rows = conn.execute(
        """
        SELECT DISTINCT SUBSTR(p.seller_sku, -4) AS mk
        FROM products p
        JOIN sku_costs sc ON sc.sku_id = p.sku_id
        WHERE sc.cost_cny IS NOT NULL AND sc.cost_cny > 0
          AND LENGTH(p.seller_sku) >= 4
          AND SUBSTR(p.seller_sku, -4) GLOB ?
        ORDER BY mk
        """,
        (f"{prefix}??",),
    ).fetchall()
    pool = []
    for (mk,) in rows:
        mk = str(mk).zfill(4)[-4:]
        if mk in skip or mk in ready:
            continue
        pool.append(mk)
    _scan_pool(pool)
    return ready, skipped


def resolve_keys(
    *, prefix: str, count: int, match_keys: list[str] | None = None
) -> tuple[list[str], list[dict]]:
    """显式 SKU 列表优先；否则自动发现 count 个 ready（待采集跳过不计数）。"""
    if match_keys:
        out: list[str] = []
        skipped: list[dict] = []
        seen: set[str] = set()
        rate = fetch_cny_mxn()
        for raw in match_keys:
            mk = str(raw).zfill(4)[-4:]
            if mk in seen:
                continue
            seen.add(mk)
            if not catalog_row(mk):
                continue
            prep = prep_one(mk, rate=rate)
            if prep.get("status") == "ready":
                out.append(mk)
            else:
                skipped.append(
                    {
                        "mk": mk,
                        "reason": prep.get("status") or prep.get("reason"),
                        "pid": prep.get("product_id"),
                    }
                )
        return out, skipped
    return discover_candidates(prefix=prefix, limit=count)


def run_dispatch(
    *,
    prefix: str,
    count: int,
    chat_id: str,
    task_id: str,
    match_keys: list[str] | None = None,
) -> dict:
    rate = fetch_cny_mxn()
    keys, pre_skipped = resolve_keys(prefix=prefix, count=count, match_keys=match_keys)
    result: dict = {
        "mode": "explicit" if match_keys else "discover",
        "prefix": prefix,
        "requested": count,
        "found": len(keys),
        "keys": keys,
        "cards_sent": [],
        "queued_web": [],
        "need_collect": list(pre_skipped),
        "errors": [],
        "missing_catalog": [],
    }
    if match_keys:
        wanted = {str(k).zfill(4)[-4:] for k in match_keys}
        result["missing_catalog"] = sorted(wanted - set(keys))

    if not keys:
        if match_keys:
            result["summary"] = f"指定 SKU 均无目录/成本：{', '.join(match_keys)}"
        else:
            result["summary"] = (
                f"未找到 {prefix}xx 可迁移候选（目标 {count} 个 ready，待采集已跳过不计数）"
            )
        return result

    for mk in keys:
        try:
            prep = prep_one(mk, rate=rate)
            if prep.get("status") != "ready":
                result["need_collect"].append(
                    {"mk": mk, "reason": prep.get("status"), "pid": prep.get("product_id")}
                )
                continue
            card, common_id = build_card_for_mk(mk, rate=rate)
            if common_id:
                card.collect_box_detail_id = common_id  # type: ignore[misc]
                from modules.miaoshou.mx_confirm import _write  # noqa: PLC2701

                _write(card)
            result["queued_web"].append(
                {
                    "mk": mk,
                    "token": card.token,
                    "list_mxn": prep.get("list_mxn"),
                    "common_id": common_id,
                    "web_url": mx_approval_url(card.token),
                }
            )
            _log(f"web queued {mk} token={card.token} list={prep.get('list_mxn')}")
        except Exception as exc:
            result["errors"].append({"mk": mk, "error": str(exc)})
            _log(f"error {mk}: {exc}")

    sent = len(result["queued_web"])
    need = len(result["need_collect"])
    mode = "指定 SKU" if match_keys else "自动发现"
    result["summary"] = (
        f"派单完成（{mode}）· 目标 **{count}** 个 ready · 已入 Web **{sent}** 个"
        f"{'（未满，采集箱可用不足）' if not match_keys and sent < count else ''}"
        f" · 跳过待采集 **{need}** 个（不计入目标）"
    )
    return result


def notify(chat_id: str, result: dict) -> None:
    if not app_ready():
        return
    inbox = mx_approval_url()
    lines = [f"📋 OrbitHive-Cursor · {result.get('summary', 'done')}"]
    lines.append(f"\n👉 **请在 Web 控制台审批（不在群内点卡）：** {inbox}")
    if result.get("queued_web"):
        lines.append("\n**已入 Web 待审：**")
        for row in result["queued_web"][:12]:
            lines.append(f"• `{row['mk']}` · {row.get('list_mxn')} MXN")
    if result.get("need_collect"):
        lines.append(f"\n**已跳过（待采集，不计入目标 {result.get('requested', '?')} 个）：**")
        for row in result["need_collect"][:8]:
            lines.append(f"• `{row['mk']}` · pid={row.get('pid')}")
    if result.get("missing_catalog"):
        lines.append("\n**无目录/成本：**")
        for mk in result["missing_catalog"][:8]:
            lines.append(f"• `{mk}`")
    if result.get("errors"):
        lines.append(f"\n⚠️ 错误 {len(result['errors'])} 个，见 feishu_dispatch.log")
    send_message(chat_id, "text", {"text": "\n".join(lines)[:4000]})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="09")
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--keys", default="", help="逗号分隔对齐码，如 0810,0811,0814")
    ap.add_argument("--chat-id", default="")
    ap.add_argument("--task-id", default=DEFAULT_TASK)
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args()
    cid = args.chat_id.strip() or default_chat_id()
    explicit = [k.strip() for k in args.keys.split(",") if k.strip()] if args.keys else None
    result = run_dispatch(
        prefix=args.prefix,
        count=args.count,
        chat_id=cid,
        task_id=args.task_id,
        match_keys=explicit,
    )
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(json.dumps(result, ensure_ascii=False))
    if args.notify:
        notify(cid, result)
    return 0 if result.get("queued_web") or result.get("need_collect") else 1


if __name__ == "__main__":
    raise SystemExit(main())
