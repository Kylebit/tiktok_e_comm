"""飞书派单 → 发现 UK 候选 SKU → POP → Web 审批收件箱（不自动发布）。"""
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
from modules.miaoshou.uk_web_approval import uk_approval_url
from modules.miaoshou.mx_collect_match import discover_collect_ready, load_collect_index
from modules.catalog.tk_sku_groups import collapse_match_keys_to_units, expand_match_keys, expand_skip_keys
from modules.miaoshou.migrate_dispatch import queue_uk_unit, scan_ready_units
from scripts.orbit_send_uk_approval import DEFAULT_TASK, build_card_for_mk
from scripts.orbit_uk_migrate_prep import catalog_row, prep_one
from scripts.uk_pop_pricing import fetch_cny_gbp

LOG = ROOT / "data" / "uk_confirm" / "feishu_dispatch.log"
OUT = ROOT / "data" / "uk_confirm" / "feishu_dispatch_last.json"


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = msg.rstrip() + "\n"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line)
    print(msg, flush=True)


def _published_match_keys() -> set[str]:
    from modules.miaoshou.uk_web_approval import published_match_keys

    return published_match_keys()


def discover_candidates(*, prefix: str, limit: int) -> tuple[list[list[str]], list[dict]]:
    skip = expand_skip_keys(_published_match_keys())
    rate = fetch_cny_gbp()

    try:
        index = load_collect_index()
        pool = discover_collect_ready(prefix=prefix, limit=500, skip=skip, index=index)
        if pool:
            return scan_ready_units(pool, limit=limit, skip=skip, prep_fn=prep_one, rate=rate)
    except Exception as exc:
        _log(f"collect discover fallback: {exc}")

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
    pool = [str(mk).zfill(4)[-4:] for (mk,) in rows if str(mk).zfill(4)[-4:] not in skip]
    return scan_ready_units(pool, limit=limit, skip=skip, prep_fn=prep_one, rate=rate)


def resolve_keys(
    *, prefix: str, count: int, match_keys: list[str] | None = None
) -> tuple[list[list[str]], list[dict], list[str]]:
    if match_keys:
        expanded = expand_match_keys(match_keys)
        units = collapse_match_keys_to_units(expanded)
        out_units: list[list[str]] = []
        skipped: list[dict] = []
        rate = fetch_cny_gbp()
        for unit in units:
            unit_ok = True
            for mk in unit:
                if not catalog_row(mk):
                    unit_ok = False
                    break
                prep = prep_one(mk, rate=rate)
                if prep.get("status") != "ready":
                    unit_ok = False
                    skipped.append(
                        {
                            "mk": mk,
                            "reason": prep.get("status") or prep.get("reason"),
                            "pid": prep.get("product_id"),
                            "group": unit if len(unit) > 1 else None,
                        }
                    )
            if unit_ok:
                out_units.append(unit)
        flat = [mk for u in out_units for mk in u]
        return out_units, skipped, flat
    units, skipped = discover_candidates(prefix=prefix, limit=count)
    flat = [mk for u in units for mk in u]
    return units, skipped, flat


def run_dispatch(
    *,
    prefix: str,
    count: int,
    chat_id: str,
    task_id: str,
    match_keys: list[str] | None = None,
) -> dict:
    rate = fetch_cny_gbp()
    units, pre_skipped, keys = resolve_keys(prefix=prefix, count=count, match_keys=match_keys)
    result: dict = {
        "mode": "explicit" if match_keys else "discover",
        "prefix": prefix,
        "requested": count,
        "found": len(units),
        "units": units,
        "keys": keys,
        "queued_web": [],
        "need_collect": list(pre_skipped),
        "errors": [],
        "missing_catalog": [],
    }
    if match_keys:
        wanted = {str(k).zfill(4)[-4:] for k in expand_match_keys(match_keys)}
        result["missing_catalog"] = sorted(wanted - set(keys))

    if not units:
        if match_keys:
            result["summary"] = f"指定 SKU 均无目录/成本：{', '.join(match_keys)}"
        else:
            result["summary"] = f"未找到 {prefix}xx 可迁移候选（目标 {count} 个 ready）"
        return result

    for unit in units:
        try:
            for mk in unit:
                prep = prep_one(mk, rate=rate)
                if prep.get("status") != "ready":
                    result["need_collect"].append(
                        {"mk": mk, "reason": prep.get("status"), "pid": prep.get("product_id"), "group": unit}
                    )
                    raise RuntimeError(f"{mk} 非 ready")
            row = queue_uk_unit(unit, rate=rate, build_single=build_card_for_mk)
            result["queued_web"].append(row)
            _log(f"web queued {row['kind']} {row['mk']} token={row['token']}")
        except Exception as exc:
            label = unit[0] if len(unit) == 1 else f"{unit[0]}–{unit[-1]}"
            result["errors"].append({"mk": label, "error": str(exc), "match_keys": unit})
            _log(f"error {label}: {exc}")

    sent = len(result["queued_web"])
    need = len(result["need_collect"])
    mode = "指定 SKU" if match_keys else "自动发现"
    result["summary"] = (
        f"UK 派单（{mode}）· 目标 **{count}** 个搬运单元 · 已入 Web **{sent}** 个"
        f" · 跳过待采集 **{need}** 个"
    )
    return result


def notify(chat_id: str, result: dict) -> None:
    if not app_ready():
        return
    inbox = uk_approval_url()
    lines = [f"🇬🇧 UK · {result.get('summary', 'done')}"]
    lines.append(f"\n👉 **Web 审批：** {inbox}")
    if result.get("queued_web"):
        lines.append("\n**已入 Web 待审：**")
        for row in result["queued_web"][:12]:
            kind = row.get("kind", "single")
            keys = row.get("match_keys") or [row.get("mk")]
            label = ",".join(keys) if kind == "group" else row["mk"]
            lines.append(f"• `{label}` · £{row.get('list_gbp')} · **{kind}**")
    if result.get("need_collect"):
        lines.append("\n**已跳过（待采集）：**")
        for row in result["need_collect"][:8]:
            lines.append(f"• `{row['mk']}` · pid={row.get('pid')}")
    send_message(chat_id, "text", {"text": "\n".join(lines)[:4000]})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="00")
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--keys", default="", help="逗号分隔对齐码")
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
