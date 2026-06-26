"""飞书审批/修改意见 → 自动执行 MX 动作（改尺寸、上架、重发审批卡）。"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.feishu_manual_overrides import save_override
from modules.miaoshou.feishu_modify_parser import parse_modify_note
from modules.miaoshou.mx_confirm import get_confirm
from modules.miaoshou.mx_feishu_approval import default_chat_id
from modules.miaoshou.mx_migrate import claim_common_to_tiktok
from modules.miaoshou.mx_publish import publish_mx_listing
from scripts.mx_pop_pricing import KNOWN_BY_MATCH_KEY, fetch_cny_mxn, quote_match_key

LOG = ROOT / "data" / "mx_confirm" / "feishu_executor.log"


def _log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = msg.rstrip() + "\n"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line)
    print(msg, flush=True)


def _notify(text: str) -> None:
    try:
        from modules.hub.feishu_app import app_ready, send_message

        if app_ready():
            send_message(default_chat_id(), "text", {"text": text[:4000]})
    except Exception as exc:
        _log(f"[notify fail] {exc}")


def _package_cm(mk: str) -> tuple[int, int, int] | None:
    from modules.miaoshou.feishu_manual_overrides import load_overrides

    known = {**KNOWN_BY_MATCH_KEY.get(mk, {}), **load_overrides().get(mk, {})}
    if known.get("l"):
        return int(known["l"]), int(known["w"]), int(known["h"])
    return None


def handle_revision(match_key: str, modify_note: str) -> int:
    mk = match_key.zfill(4)[-4:]
    patch = parse_modify_note(modify_note)
    if not patch:
        _notify(f"⚠️ `{mk}` 修改意见无法解析，请写如：尺寸 20×10×7 cm 或 重量 200g")
        return 1
    merged = save_override(mk, patch, note=modify_note)
    q = quote_match_key(mk, cny_mxn=fetch_cny_mxn())
    _log(f"[revision] {mk} override={merged} list={q.list_price_ceil_mxn}")
    _notify(
        f"✅ `{mk}` 已应用修改意见\n"
        f"意见：{modify_note}\n"
        f"新上传原价：**{q.list_price_ceil_mxn} MXN** · 尺寸 **{q.package_cm}**\n"
        f"请在 Web 控制台重新审批：http://127.0.0.1:8765/mx"
    )
    from scripts.orbit_send_mx_approval import build_card_for_mk
    from modules.miaoshou.mx_confirm import _write  # noqa: PLC2701

    rate = fetch_cny_mxn()
    card, common_id = build_card_for_mk(mk, rate=rate)
    if common_id:
        card.collect_box_detail_id = common_id  # type: ignore[misc]
        _write(card)
    _notify(f"📋 `{mk}` 已更新并重新入 Web 待审 · {card.list_price_ceil_mxn} MXN")
    return 0


def handle_approve(match_key: str, confirm_token: str) -> int:
    mk = match_key.zfill(4)[-4:]
    card = get_confirm(confirm_token) if confirm_token else None
    if not card:
        _notify(f"⚠️ `{mk}` 批准收到，但确认单 token 无效或过期，请在 Cursor 对话说「继续上架 {mk}」")
        return 1

    from scripts.orbit_mx_migrate_prep import catalog_row, find_common_id

    row = catalog_row(mk)
    if not row:
        _notify(f"⚠️ `{mk}` 无目录/成本，无法上架")
        return 1
    common_id = find_common_id(str(row["product_id"]), mk=mk, seller_sku=str(row["seller_sku"]))
    if not common_id:
        _notify(f"⚠️ `{mk}` 已批准，但妙手公共采集箱尚无此链接，请先采集 PH product_id={row['product_id']}")
        return 1

    tk_map = claim_common_to_tiktok([common_id])
    tk = tk_map[common_id]
    q = quote_match_key(mk, cny_mxn=fetch_cny_mxn())
    _notify(f"🚀 `{mk}` 开始 MX 上架（TK={tk}，list={q.list_price_ceil_mxn} MXN）…")
    rc = publish_mx_listing(
        collect_box_detail_id=tk,
        seller_sku=row["seller_sku"],
        ph_product_id=str(row["product_id"]),
        master_region=row["region"] or "PH",
        publish=True,
        mxn_sale=q.sale_price_mxn,
        mxn_list=q.list_price_ceil_mxn,
        stock=200,
        weight_kg=q.weight_kg,
        package_cm=_package_cm(mk),
        pop_quote=q,
        volumetric_confirmed=True,
        skip_user_confirm=True,
        spanish_copy=True,
        confirm_token=confirm_token,
    )
    if rc == 0:
        _notify(f"✅ `{mk}` MX 上架完成 · list={q.list_price_ceil_mxn} MXN · TK={tk}")
    else:
        _notify(f"❌ `{mk}` 上架失败 exit={rc}，详见 feishu_executor.log")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", required=True, choices=["approve", "request_revision"])
    ap.add_argument("--match-key", required=True)
    ap.add_argument("--confirm-token", default="")
    ap.add_argument("--modify-note", default="")
    ap.add_argument("--task-id", default="")
    args = ap.parse_args()
    try:
        if args.action == "request_revision":
            return handle_revision(args.match_key, args.modify_note)
        return handle_approve(args.match_key, args.confirm_token)
    except Exception:
        _log(traceback.format_exc())
        _notify(f"❌ 执行器异常 `{args.match_key}`，见 feishu_executor.log")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
