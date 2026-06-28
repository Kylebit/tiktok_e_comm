"""UK 上架审批 — Web 收件箱（同 MX 流程）。"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from core.config import ROOT
from modules.miaoshou.uk_confirm import (
    CONFIRM_DIR,
    UkConfirmCard,
    approve_confirm,
    approve_group_confirm,
    get_confirm,
    get_group_confirm,
    mark_published,
    mark_group_published,
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
    "uk_published_skip.json",
}


def uk_approval_url(token: str | None = None, *, port: int = 8765) -> str:
    base = f"http://127.0.0.1:{port}/uk"
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
        list_gbp = variants[0].get("list_price_ceil_gbp") if variants else None
    else:
        mk = str(data.get("match_key") or "????")
        label = mk
        list_gbp = data.get("list_price_ceil_gbp")
    return {
        "token": token,
        "kind": kind,
        "status": data.get("status") or "unknown",
        "match_key": mk,
        "label": label,
        "product_name": str(data.get("product_name") or "")[:120],
        "main_image_url": data.get("main_image_url") or "",
        "list_price_ceil_gbp": list_gbp,
        "sale_price_gbp": data.get("sale_price_gbp"),
        "net_profit_gbp": data.get("net_profit_gbp"),
        "volumetric_dominates": bool(data.get("volumetric_dominates")),
        "created_at": data.get("created_at"),
        "approved_at": data.get("approved_at"),
        "rejected_at": data.get("rejected_at"),
        "web_url": uk_approval_url(token),
    }


def published_match_keys() -> set[str]:
    skip: set[str] = set()
    if not CONFIRM_DIR.is_dir():
        return skip
    web_log = CONFIRM_DIR / "web_publish.log"
    if web_log.is_file():
        for line in web_log.read_text(encoding="utf-8", errors="ignore").splitlines():
            if " rc=0 " not in line:
                continue
            head = line.split(" rc=0")[0].strip().split()
            if len(head) >= 3:
                skip.add(str(head[2]).zfill(4)[-4:])
    for path in CONFIRM_DIR.glob("*.json"):
        parsed = _card_path(path)
        if not parsed:
            continue
        kind, _ = parsed
        data = _load_raw(path)
        if not data or str(data.get("status") or "") != "published":
            continue
        if kind == "group":
            for mk in data.get("match_keys") or []:
                skip.add(str(mk).zfill(4)[-4:])
        else:
            mk = str(data.get("match_key") or "").strip()
            if mk:
                skip.add(mk.zfill(4)[-4:])
    skip_file = CONFIRM_DIR / "uk_published_skip.json"
    if skip_file.is_file():
        try:
            extra = json.loads(skip_file.read_text(encoding="utf-8"))
            for mk in extra.get("match_keys") or []:
                skip.add(str(mk).zfill(4)[-4:])
        except Exception:
            pass
    return skip


def archive_stale_pending(*, dry_run: bool = False) -> dict[str, Any]:
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
            mk = str(keys[0] if keys else "????").zfill(4)[-4:]
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
                        if kind == "group":
                            reject_group_confirm(token)
                        else:
                            reject_confirm(token)
                    except Exception:
                        pass
            continue
        for _, token, kind in entries[1:]:
            archived.append({"match_key": mk, "token": token, "reason": "duplicate"})
            if not dry_run:
                try:
                    if kind == "group":
                        reject_group_confirm(token)
                    else:
                        reject_confirm(token)
                except Exception:
                    pass

    return {"archived": archived, "count": len(archived), "published_skip": sorted(published)}


def _reject_any(*, kind: str, token: str) -> None:
    if kind == "group":
        reject_group_confirm(token)
    else:
        reject_confirm(token)


def clear_pending_inbox(*, reason: str = "cleared") -> dict[str, Any]:
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
        data["web_url"] = uk_approval_url(token)
        data["total_expense_gbp"] = round(
            card.cost_gbp
            + card.seller_ship_net_gbp
            + card.vat_gbp
            + card.platform_commission_gbp
            + card.smart_promotion_gbp
            + card.ad_gbp,
            2,
        )
        return data
    group = get_group_confirm(token)
    if group:
        data = asdict(group)
        data["variants"] = [asdict(v) for v in group.variants]
        data["kind"] = "group"
        data["web_url"] = uk_approval_url(token)
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
    if get_group_confirm(token):
        updated = reject_group_confirm(token)
        return {"ok": True, "kind": "group", "status": updated.status, "match_keys": updated.match_keys}
    updated = reject_confirm(token)
    return {"ok": True, "status": updated.status, "match_key": updated.match_key}


def apply_override(token: str, *, length_cm: int, width_cm: int, height_cm: int, note: str = "") -> dict[str, Any]:
    from modules.miaoshou.uk_manual_overrides import save_override
    from scripts.uk_pop_pricing import fetch_cny_gbp, quote_match_key

    card = get_confirm(token)
    if not card:
        raise KeyError(f"确认单不存在: {token}")
    if card.status != "pending":
        raise RuntimeError(f"确认单状态为 {card.status}，无法修改")
    mk = card.match_key
    save_override(mk, {"l": length_cm, "w": width_cm, "h": height_cm}, note=note or f"Web 修改 {length_cm}×{width_cm}×{height_cm} cm")
    q = quote_match_key(mk, cny_gbp=fetch_cny_gbp(), cargo=card.cargo)  # type: ignore[arg-type]
    card.package_cm = str(q.package_cm)
    card.weight_kg = float(q.weight_kg)
    card.weight_source = str(q.weight_source)
    card.volumetric_kg = float(q.volumetric_kg)
    card.billable_kg = float(q.billable_kg)
    card.merchant_logistics_gbp = float(q.merchant_logistics_gbp)
    card.seller_ship_net_gbp = float(q.seller_ship_net_gbp)
    card.sale_price_gbp = float(q.sale_price_gbp)
    card.list_price_ceil_gbp = int(q.list_price_ceil_gbp)
    card.list_price_gbp = float(q.list_price_gbp)
    card.net_profit_gbp = float(q.net_profit_gbp)
    card.cost_gbp = float(q.cost_gbp)
    card.platform_commission_gbp = float(q.platform_commission_gbp)
    card.vat_gbp = float(q.vat_gbp)
    card.smart_promotion_gbp = float(q.smart_promotion_gbp)
    card.affiliate_gbp = float(q.affiliate_gbp)
    card.ad_gbp = float(q.ad_gbp)
    card.net_income_gbp = float(q.net_income_gbp)
    card.profit_margin_on_sale_pct = float(q.profit_margin_on_sale_pct)
    card.shipping_band_max_kg = float(q.shipping_band_max_kg)
    card.volumetric_dominates = float(q.volumetric_kg) > float(q.weight_kg)
    card.uk_category = str(q.uk_category)
    card.uk_sub_category = str(q.uk_sub_category)
    card.commission_pct = float(q.commission_pct)
    card.commission_label = str(q.commission_label)
    card.vat_rate_pct = float(q.vat_rate_pct)
    from modules.miaoshou.uk_confirm import _write  # noqa: PLC2701

    _write(card)
    return {"ok": True, "match_key": mk, "list_price_ceil_gbp": card.list_price_ceil_gbp, "package_cm": card.package_cm}


def publish_token(token: str) -> dict[str, Any]:
    from modules.miaoshou.mx_migrate import MxSkuVariantWrite, claim_common_to_tiktok
    from modules.miaoshou.uk_manual_overrides import load_overrides
    from modules.miaoshou.uk_publish import publish_uk_listing, publish_uk_multi_listing
    from scripts.mx_pop_pricing import KNOWN_BY_MATCH_KEY
    from scripts.orbit_uk_migrate_prep import catalog_row, find_common_id
    from scripts.uk_pop_pricing import fetch_cny_gbp, quote_match_key

    group = get_group_confirm(token)
    if group:
        if group.status != "approved":
            raise RuntimeError(f"请先批准（当前: {group.status}）")
        common_id = group.collect_box_detail_id or find_common_id(
            group.master_product_id, mk=group.match_keys[0], seller_sku=""
        )
        if not common_id:
            raise RuntimeError(f"整组 {group.match_keys} 妙手采集箱尚无链接")
        tk_map = claim_common_to_tiktok([int(common_id)])
        tk = tk_map[int(common_id)]
        mk0 = group.match_keys[0]
        known = {**KNOWN_BY_MATCH_KEY.get(mk0, {}), **load_overrides().get(mk0, {})}
        pkg = None
        if known.get("l"):
            pkg = (int(known["l"]), int(known["w"]), int(known["h"]))
        writes = [
            MxSkuVariantWrite(
                match_key=v.match_key,
                seller_sku=v.seller_sku,
                mxn_list_price=v.list_price_ceil_gbp,
                weight_kg=v.weight_kg,
                variant_label=v.variant_label,
            )
            for v in group.variants
        ]
        rc = publish_uk_multi_listing(
            collect_box_detail_id=tk,
            ph_product_id=group.master_product_id,
            variant_writes=writes,
            publish=True,
            stock=group.stock,
            master_region=group.master_region,
            package_cm=pkg,
            confirm_token=token,
            skip_user_confirm=True,
        )
        log_path = ROOT / "data" / "uk_confirm" / "web_publish.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        label = ",".join(group.match_keys)
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {label} token={token} rc={rc} tk={tk} group_keys={label}\n"
        log_path.open("a", encoding="utf-8").write(line)
        if rc != 0:
            raise RuntimeError(f"整组上架失败 exit={rc}，见 web_publish.log")
        mark_group_published(token)
        return {"ok": True, "kind": "group", "match_keys": group.match_keys, "tk_collect_id": tk}

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
    q = quote_match_key(mk, cny_gbp=fetch_cny_gbp(), cargo=card.cargo)  # type: ignore[arg-type]
    rc = publish_uk_listing(
        collect_box_detail_id=tk,
        seller_sku=row["seller_sku"],
        ph_product_id=str(row["product_id"]),
        master_region=row["region"] or "PH",
        publish=True,
        gbp_sale=q.sale_price_gbp,
        gbp_list=q.list_price_ceil_gbp,
        stock=200,
        weight_kg=q.weight_kg,
        package_cm=_package_cm(),
        pop_quote=q,
        volumetric_confirmed=True,
        skip_user_confirm=True,
        confirm_token=token,
    )
    log_path = ROOT / "data" / "uk_confirm" / "web_publish.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {mk} token={token} rc={rc} tk={tk} list={q.list_price_ceil_gbp}\n"
    log_path.open("a", encoding="utf-8").write(line)
    if rc != 0:
        raise RuntimeError(f"上架失败 exit={rc}，见 web_publish.log")
    mark_published(token)
    return {
        "ok": True,
        "match_key": mk,
        "tk_collect_id": tk,
        "list_price_ceil_gbp": q.list_price_ceil_gbp,
    }
