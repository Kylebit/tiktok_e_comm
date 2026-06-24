"""识别 Shopee 店铺站点，仅保留 MY/VN/TH/PH 主店。"""

from __future__ import annotations

import time

from modules.shopee.auth import load_tokens, save_tokens
from modules.shopee.client import get_shop_info
from modules.shopee.config import shopee_config

SEA_REGIONS = frozenset({"MY", "VN", "TH", "PH"})


def _pick_token_entry(shops: dict) -> tuple[int, dict] | None:
    for sid, entry in shops.items():
        if entry.get("access_token"):
            try:
                return int(sid), entry
            except ValueError:
                continue
    return None


def refresh_shop_regions(*, quiet: bool = False) -> list[dict]:
    """调用 get_shop_info 填充 region，并标记 sync_enabled。"""
    store = load_tokens()
    shops = store.get("shops") or {}
    if not shops:
        raise RuntimeError("无已授权店铺，请先 shopee token")

    allowed = set(r.upper() for r in shopee_config()["regions"])
    picked: dict[str, dict] = {}
    region_primary: dict[str, str] = {}  # region -> shop_id (first wins)

    for sid, entry in shops.items():
        if not str(sid).isdigit():
            continue
        shop_id = int(sid)
        token = entry.get("access_token") or ""
        if not token:
            continue
        try:
            resp = get_shop_info(shop_id, token)
        except Exception as e:
            if not quiet:
                print(f"  ⚠ shop_id={shop_id} 查询失败: {e}")
            entry["region"] = entry.get("region") or "?"
            entry["sync_enabled"] = False
            picked[sid] = entry
            continue

        if resp.get("error"):
            entry["region"] = "?"
            entry["sync_enabled"] = False
            entry["shop_name"] = resp.get("message", "")[:80]
            picked[sid] = entry
            continue

        info = resp.get("response") or resp.get("shop_info") or resp
        region = (info.get("region") or info.get("country") or "").upper()
        name = info.get("shop_name") or info.get("name") or ""
        entry["region"] = region
        entry["shop_name"] = name
        entry["updated_at"] = int(time.time())

        in_sea = region in allowed
        # 每国只保留第一个 shop_id（主店）；其余同国店视为附属
        if in_sea and region not in region_primary:
            region_primary[region] = sid
            entry["sync_enabled"] = True
        else:
            entry["sync_enabled"] = False

        picked[sid] = entry
        if not quiet:
            flag = "✓ 同步" if entry["sync_enabled"] else "· 跳过"
            print(f"  {flag} [{region or '?'}] {name[:40]}  shop_id={shop_id}")

    store["shops"] = picked
    store["sync_shop_ids"] = {r: int(region_primary[r]) for r in sorted(region_primary)}
    save_tokens(store)
    return list_sync_shops()


def list_sync_shops() -> list[dict]:
    store = load_tokens()
    sync_map = store.get("sync_shop_ids") or {}
    shops = store.get("shops") or {}
    out: list[dict] = []
    for region, sid in sync_map.items():
        entry = shops.get(str(sid), {})
        out.append(
            {
                "region": region,
                "shop_id": sid,
                "shop_name": entry.get("shop_name") or "",
            }
        )
    return sorted(out, key=lambda x: x["region"])


def sync_shop_ids() -> dict[str, int]:
    """region → shop_id，仅 MY/VN/TH/PH 主店。"""
    store = load_tokens()
    m = store.get("sync_shop_ids")
    if m:
        return dict(m)
    refresh_shop_regions(quiet=True)
    return dict(load_tokens().get("sync_shop_ids") or {})


def status_lines() -> list[str]:
    sync = list_sync_shops()
    store = load_tokens()
    all_shops = store.get("shops") or {}
    lines = [
        f"Shopee ({shopee_config()['environment']}) partner_id={shopee_config()['partner_id']}",
    ]
    if store.get("main_account_id"):
        lines.append(f"主账号 main_account_id={store['main_account_id']}")
    lines.append(f"授权店铺 {len(all_shops)} 个 · 同步 {len(sync)} 个（MY/VN/TH/PH 主店）")
    for s in sync:
        lines.append(f"  ✓ [{s['region']}] {s.get('shop_name') or '?'}  shop_id={s['shop_id']}")
    skipped = sum(1 for e in all_shops.values() if not e.get("sync_enabled"))
    if skipped:
        lines.append(f"  （附属/重复站点 {skipped} 个已跳过）")
    return lines
