"""MX 上架审批 — Web 收件箱（替代飞书群审批卡）。"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from core.config import ROOT
from modules.miaoshou.mx_confirm import (
    CONFIRM_DIR,
    MxConfirmCard,
    MxGroupConfirmCard,
    approve_confirm,
    approve_group_confirm,
    get_confirm,
    get_group_confirm,
    mark_published,
    reject_confirm,
    reject_group_confirm,
)

SKIP_PREFIXES = (
    "feishu_",
    "batch_",
    "orbit_",
    "prep_",
    "batch_queue",
)
SKIP_NAMES = {
    "feishu_dispatch_last.json",
    "feishu_manual_overrides.json",
    "orbit_dry_run.json",
}


def mx_approval_url(token: str | None = None, *, port: int = 8765) -> str:
    base = f"http://127.0.0.1:{port}/mx"
    return f"{base}?token={token}" if token else base


def _card_path(path: Path) -> tuple[str, str] | None:
    name = path.name
    if name in SKIP_NAMES:
        return None
    if any(name.startswith(p) for p in SKIP_PREFIXES):
        return None
    if name.startswith("group_") and name.endswith(".json"):
        return ("group", name[len("group_") : -5])
    if name.endswith(".json") and not name.startswith("group_"):
        return ("single", name[:-5])
    return None


def _load_raw(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def card_summary(data: dict[str, Any], *, kind: str, token: str) -> dict[str, Any]:
    if kind == "group":
        keys = data.get("match_keys") or []
        mk = keys[0] if keys else "????"
        label = f"{mk}–{keys[-1]}" if len(keys) > 1 else mk
        variants = data.get("variants") or []
        list_mxn = variants[0].get("list_price_ceil_mxn") if variants else None
    else:
        mk = str(data.get("match_key") or "????")
        label = mk
        list_mxn = data.get("list_price_ceil_mxn")
    return {
        "token": token,
        "kind": kind,
        "status": data.get("status") or "unknown",
        "match_key": mk,
        "label": label,
        "product_name": str(data.get("product_name") or "")[:120],
        "main_image_url": data.get("main_image_url") or "",
        "list_price_ceil_mxn": list_mxn,
        "sale_price_mxn": data.get("sale_price_mxn"),
        "net_profit_mxn": data.get("net_profit_mxn"),
        "volumetric_dominates": bool(data.get("volumetric_dominates")),
        "created_at": data.get("created_at"),
        "approved_at": data.get("approved_at"),
        "rejected_at": data.get("rejected_at"),
        "web_url": mx_approval_url(token),
    }


def published_match_keys() -> set[str]:
    """已从 batch 日志 / web 发布记录确认上架的对齐码。"""
    skip: set[str] = set()
    if not CONFIRM_DIR.is_dir():
        return skip
    for path in CONFIRM_DIR.glob("batch_*publish*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for row in data:
            if row.get("status") == "ok":
                if row.get("mk"):
                    skip.add(str(row["mk"]).zfill(4)[-4:])
                for k in row.get("keys") or []:
                    skip.add(str(k).zfill(4)[-4:])
    web_log = CONFIRM_DIR / "web_publish.log"
    if web_log.is_file():
        for line in web_log.read_text(encoding="utf-8", errors="ignore").splitlines():
            if " rc=0 " not in line:
                continue
            head = line.split(" rc=0")[0].strip().split()
            if len(head) >= 3:
                skip.add(str(head[2]).zfill(4)[-4:])
    return skip


def _reject_any(*, kind: str, token: str) -> None:
    if kind == "group":
        reject_group_confirm(token)
    else:
        reject_confirm(token)


def archive_stale_pending(*, dry_run: bool = False) -> dict[str, Any]:
    """归档重复 pending、以及已上架 SKU 的旧确认单。"""
    published = published_match_keys()
    grouped: dict[str, list[tuple[float, str, str]]] = {}
    archived: list[dict[str, str]] = []

    for path in CONFIRM_DIR.glob("*.json"):
        parsed = _card_path(path)
        if not parsed:
            continue
        kind, token = parsed
        data = _load_raw(path)
        if not data or str(data.get("status") or "") != "pending":
            continue
        if kind == "group":
            keys = data.get("match_keys") or []
            mk = str(keys[0]).zfill(4)[-4:] if keys else "????"
        else:
            mk = str(data.get("match_key") or "????").zfill(4)[-4:]
        created = float(data.get("created_at") or 0)
        grouped.setdefault(mk, []).append((created, token, kind))

    for mk, entries in grouped.items():
        entries.sort(key=lambda x: x[0], reverse=True)
        if mk in published:
            for _, token, kind in entries:
                archived.append({"match_key": mk, "token": token, "reason": "already_published"})
                if not dry_run:
                    try:
                        _reject_any(kind=kind, token=token)
                    except Exception:
                        pass
            continue
        for _, token, kind in entries[1:]:
            archived.append({"match_key": mk, "token": token, "reason": "duplicate"})
            if not dry_run:
                try:
                    _reject_any(kind=kind, token=token)
                except Exception:
                    pass

    return {"archived": archived, "count": len(archived), "published_skip": sorted(published)}


def clear_pending_inbox(*, reason: str = "cleared") -> dict[str, Any]:
    """清空全部 pending 确认单（标为 rejected）。"""
    cleared: list[dict[str, str]] = []
    for path in CONFIRM_DIR.glob("*.json"):
        parsed = _card_path(path)
        if not parsed:
            continue
        kind, token = parsed
        data = _load_raw(path)
        if not data or str(data.get("status") or "") != "pending":
            continue
        if kind == "group":
            keys = data.get("match_keys") or []
            mk = str(keys[0]).zfill(4)[-4:] if keys else "????"
        else:
            mk = str(data.get("match_key") or "????").zfill(4)[-4:]
        try:
            _reject_any(kind=kind, token=token)
            cleared.append({"match_key": mk, "token": token, "reason": reason})
        except Exception:
            pass
    return {"cleared": cleared, "count": len(cleared)}


def list_cards(*, status: str | None = "pending", auto_archive: bool = True) -> list[dict[str, Any]]:
    if auto_archive and status == "pending":
        archive_stale_pending()
    if not CONFIRM_DIR.is_dir():
        return []
    published = published_match_keys() if status == "pending" else set()
    rows: list[dict[str, Any]] = []
    seen_mk: set[str] = set()
    for path in CONFIRM_DIR.glob("*.json"):
        parsed = _card_path(path)
        if not parsed:
            continue
        kind, token = parsed
        data = _load_raw(path)
        if not data or "token" not in data:
            continue
        st = str(data.get("status") or "")
        if status and st != status:
            continue
        summary = card_summary(data, kind=kind, token=token)
        mk = str(summary.get("match_key") or "").zfill(4)[-4:]
        if status == "pending":
            if mk in published:
                continue
            if mk in seen_mk:
                continue
            seen_mk.add(mk)
        rows.append(summary)
    rows.sort(key=lambda r: float(r.get("created_at") or 0), reverse=True)
    return rows


def get_card_detail(token: str) -> dict[str, Any] | None:
    card = get_confirm(token)
    if card:
        data = asdict(card)
        data["kind"] = "single"
        data["web_url"] = mx_approval_url(token)
        data["total_expense_mxn"] = round(
            card.cost_mxn
            + card.logistics_hidden_mxn
            + card.import_tax_mxn
            + card.platform_commission_mxn
            + card.sfp_fee_mxn
            + card.affiliate_mxn
            + card.ad_mxn
            + card.per_item_fee_mxn,
            2,
        )
        return data
    group = get_group_confirm(token)
    if group:
        data = asdict(group)
        data["variants"] = [asdict(v) for v in group.variants]
        data["kind"] = "group"
        data["web_url"] = mx_approval_url(token)
        return data
    return None


def approve_token(token: str) -> dict[str, Any]:
    if get_confirm(token):
        card = approve_confirm(token)
        return {"ok": True, "kind": "single", "status": card.status, "match_key": card.match_key}
    if get_group_confirm(token):
        card = approve_group_confirm(token)
        return {
            "ok": True,
            "kind": "group",
            "status": card.status,
            "match_keys": card.match_keys,
        }
    raise KeyError(f"确认单不存在: {token}")


def reject_token(token: str) -> dict[str, Any]:
    card = get_confirm(token)
    if not card:
        raise KeyError(f"确认单不存在: {token}")
    updated = reject_confirm(token)
    return {"ok": True, "status": updated.status, "match_key": updated.match_key}


def apply_override(token: str, *, length_cm: int, width_cm: int, height_cm: int, note: str = "") -> dict[str, Any]:
    from modules.miaoshou.feishu_manual_overrides import save_override
    from scripts.mx_pop_pricing import fetch_cny_mxn, quote_match_key

    card = get_confirm(token)
    if not card:
        raise KeyError(f"确认单不存在: {token}")
    if card.status != "pending":
        raise RuntimeError(f"确认单状态为 {card.status}，无法修改")
    mk = card.match_key
    patch = {"l": length_cm, "w": width_cm, "h": height_cm}
    save_override(mk, patch, note=note or f"Web 修改 {length_cm}×{width_cm}×{height_cm} cm")
    q = quote_match_key(mk, cny_mxn=fetch_cny_mxn())
    # 更新落盘卡片上的报价字段
    card.package_cm = str(q.package_cm)
    card.weight_kg = float(q.weight_kg)
    card.weight_source = str(q.weight_source)
    card.volumetric_kg = float(q.volumetric_kg)
    card.billable_kg = float(q.billable_kg)
    card.logistics_hidden_mxn = float(q.logistics_hidden_mxn)
    card.sale_price_mxn = float(q.sale_price_mxn)
    card.list_price_ceil_mxn = int(q.list_price_ceil_mxn)
    card.list_price_mxn = float(q.list_price_mxn)
    card.net_profit_mxn = float(q.net_profit_mxn)
    card.cost_mxn = float(q.cost_mxn)
    card.pop_sale_mxn = float(q.pop_sale_mxn)
    card.import_tax_mxn = float(q.import_tax_mxn)
    card.platform_commission_mxn = float(q.platform_commission_mxn)
    card.sfp_fee_mxn = float(q.sfp_fee_mxn)
    card.per_item_fee_mxn = float(q.per_item_fee_mxn)
    card.affiliate_mxn = float(q.affiliate_mxn)
    card.ad_mxn = float(q.ad_mxn)
    card.net_income_mxn = float(q.net_income_mxn)
    card.profit_margin_on_sale_pct = float(q.profit_margin_on_sale_pct)
    card.shipping_tier_kg = float(q.shipping_tier_kg)
    card.shipping_card_mxn = float(q.shipping_card_mxn)
    card.volumetric_dominates = float(q.volumetric_kg) > float(q.weight_kg)
    from modules.miaoshou.mx_confirm import _write  # noqa: PLC2701

    _write(card)
    return {"ok": True, "match_key": mk, "list_price_ceil_mxn": card.list_price_ceil_mxn, "package_cm": card.package_cm}


def publish_token(token: str) -> dict[str, Any]:
    """批准后的真实 MX 上架（单 SKU）。"""
    from modules.miaoshou.feishu_manual_overrides import load_overrides
    from modules.miaoshou.mx_migrate import claim_common_to_tiktok
    from modules.miaoshou.mx_publish import publish_mx_listing
    from scripts.mx_pop_pricing import KNOWN_BY_MATCH_KEY, fetch_cny_mxn, quote_match_key
    from scripts.orbit_mx_migrate_prep import catalog_row, find_common_id

    card = get_confirm(token)
    if not card:
        raise KeyError(f"确认单不存在: {token}")
    if card.status != "approved":
        raise RuntimeError(f"请先批准（当前: {card.status}）")
    mk = card.match_key
    row = catalog_row(mk)
    if not row:
        raise RuntimeError(f"{mk} 无目录/成本")
    common_id = card.collect_box_detail_id or find_common_id(
        str(row["product_id"]), mk=mk, seller_sku=str(row["seller_sku"])
    )
    if not common_id:
        raise RuntimeError(f"{mk} 妙手采集箱尚无链接，请先采集 PH product_id={row['product_id']}")

    def _package_cm() -> tuple[int, int, int] | None:
        known = {**KNOWN_BY_MATCH_KEY.get(mk, {}), **load_overrides().get(mk, {})}
        if known.get("l"):
            return int(known["l"]), int(known["w"]), int(known["h"])
        return None

    tk_map = claim_common_to_tiktok([int(common_id)])
    tk = tk_map[int(common_id)]
    q = quote_match_key(mk, cny_mxn=fetch_cny_mxn())
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
        package_cm=_package_cm(),
        pop_quote=q,
        volumetric_confirmed=True,
        skip_user_confirm=True,
        spanish_copy=True,
        confirm_token=token,
    )
    log_path = ROOT / "data" / "mx_confirm" / "web_publish.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {mk} token={token} rc={rc} tk={tk} list={q.list_price_ceil_mxn}\n"
    log_path.open("a", encoding="utf-8").write(line)
    if rc != 0:
        raise RuntimeError(f"上架失败 exit={rc}，见 web_publish.log")
    mark_published(token)
    return {
        "ok": True,
        "match_key": mk,
        "tk_collect_id": tk,
        "list_price_ceil_mxn": q.list_price_ceil_mxn,
    }
