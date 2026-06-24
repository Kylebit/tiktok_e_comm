"""Ozon offer_id 规范化：6 位数字货号 → 后四位，并同步 Ozon API + 本地 JSON。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from modules.catalog.sku_key import tk_match_key
from modules.ozon.client import ozon_post
from modules.ozon.config import ozon_data_dir

_DIGITS = re.compile(r"\D")
_OFFER_ID_KEYS = frozenset({"offer_id", "new_offer_id"})
_SKIP_LOCAL_FILES = frozenset({"tk_sku_map.json"})


def normalize_offer_id(raw: str) -> str:
    """6 位纯数字 → 后四位；否则按 tk_match_key。"""
    s = (raw or "").strip()
    d = _DIGITS.sub("", s)
    if len(d) == 6:
        return d[-4:]
    mk = tk_match_key(s)
    return mk or s


def six_digit_offer_ids_from_attrs(base: Path) -> list[str]:
    path = base / "all_products_attrs.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("result") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for it in items:
        oid = str(it.get("offer_id") or "").strip()
        d = _DIGITS.sub("", oid)
        if len(d) == 6:
            out.append(oid)
    return sorted(set(out))


def build_update_map(offer_ids: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for oid in offer_ids:
        oid = str(oid).strip()
        d = _DIGITS.sub("", oid)
        if len(d) != 6:
            continue
        new_id = d[-4:]
        if new_id != oid:
            mapping[oid] = new_id
    return mapping


def find_mapping_conflicts(mapping: dict[str, str], existing: set[str]) -> list[str]:
    errors: list[str] = []
    rev: dict[str, list[str]] = {}
    for old, new in mapping.items():
        rev.setdefault(new, []).append(old)
    for new, olds in rev.items():
        if len(olds) > 1:
            errors.append(f"多条 6 位货号映射到同一 4 位 {new}: {', '.join(olds)}")
    for old, new in mapping.items():
        if new in existing and new not in mapping and new != old:
            errors.append(f"4 位货号 {new} 已被占用（来自 {old}）")
    return errors


def update_ozon_api(mapping: dict[str, str], *, dry_run: bool = False) -> dict:
    pairs = [{"offer_id": old, "new_offer_id": new} for old, new in sorted(mapping.items())]
    if not pairs:
        return {"updated": 0, "errors": []}
    if dry_run:
        return {"updated": 0, "dry_run": len(pairs), "sample": pairs[:5]}

    errors: list[dict] = []
    updated = 0
    succeeded: dict[str, str] = {}
    for i in range(0, len(pairs), 25):
        chunk = pairs[i : i + 25]
        time.sleep(0.35)
        resp = ozon_post("/v1/product/update/offer-id", {"update_offer_id": chunk})
        err_items = resp.get("errors") or []
        failed = {str(e.get("offer_id") or "") for e in err_items}
        if err_items:
            errors.extend(err_items)
        for pair in chunk:
            old = pair["offer_id"]
            if old not in failed:
                succeeded[old] = pair["new_offer_id"]
        updated += len(chunk) - len(err_items)
    return {"updated": updated, "errors": errors, "total": len(pairs), "succeeded": succeeded}


def _replace_offer_strings(obj: Any, mapping: dict[str, str]) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _OFFER_ID_KEYS and isinstance(v, str) and v in mapping:
                out[k] = mapping[v]
            else:
                out[k] = _replace_offer_strings(v, mapping)
        return out
    if isinstance(obj, list):
        new_list = []
        for v in obj:
            if isinstance(v, str) and v in mapping:
                new_list.append(mapping[v])
            else:
                new_list.append(_replace_offer_strings(v, mapping))
        return new_list
    return obj


def _patch_migrated_offers(path: Path, mapping: dict[str, str]) -> bool:
    if not path.is_file():
        return False
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return False
    changed = False
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        val = mapping.get(str(item), str(item))
        if val not in seen:
            out.append(val)
            seen.add(val)
        if str(item) in mapping:
            changed = True
    if changed:
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def patch_local_json_files(base: Path, mapping: dict[str, str]) -> list[str]:
    touched: list[str] = []
    if _patch_migrated_offers(base / "migrated_offers.json", mapping):
        touched.append("migrated_offers.json")

    skip = {"migrated_offers.json", * _SKIP_LOCAL_FILES}
    for path in sorted(base.glob("*.json")):
        if path.name in skip:
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            continue
        patched = _replace_offer_strings(data, mapping)
        if patched != data:
            path.write_text(json.dumps(patched, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            touched.append(path.name)
    return touched


def run_normalize(*, dry_run: bool = False, local_only: bool = False) -> dict:
    base = ozon_data_dir()
    if not base:
        raise RuntimeError("未配置 ozon.data_dir")

    six_digit = six_digit_offer_ids_from_attrs(base)
    mapping = build_update_map(six_digit)
    if not mapping:
        return {"ok": True, "message": "无 6 位 offer_id 需更新", "count": 0}

    attrs = json.loads((base / "all_products_attrs.json").read_text(encoding="utf-8"))
    items = attrs.get("result") if isinstance(attrs, dict) else attrs
    existing = {str(it.get("offer_id") or "") for it in (items or []) if isinstance(it, dict)}

    conflicts = find_mapping_conflicts(mapping, existing)
    if conflicts:
        return {"ok": False, "errors": conflicts, "mapping_count": len(mapping)}

    result: dict = {
        "ok": True,
        "mapping_count": len(mapping),
        "sample": dict(list(mapping.items())[:8]),
    }

    if dry_run:
        result["dry_run"] = True
        result["would_touch_local"] = True
        return result

    api_res = None
    if not local_only:
        api_res = update_ozon_api(mapping, dry_run=False)
        result["ozon_api"] = api_res
        ok_map = api_res.get("succeeded") or {}
        if api_res.get("errors"):
            result["ok"] = bool(ok_map)
        patch_map = ok_map
    else:
        patch_map = mapping

    touched = patch_local_json_files(base, patch_map)
    result["local_files"] = touched
    if api_res and api_res.get("errors"):
        result["failed_offer_ids"] = [e.get("offer_id") for e in api_res["errors"]]
    return result


def restore_tk_map_seller_skus() -> int:
    """从商品目录恢复 tk_sku_map 中的 TikTok 6 位 seller_sku。"""
    from modules.catalog import listings as cat_mod
    from modules.ozon.catalog_source import _load_tk_map, _save_tk_map

    data = _load_tk_map()
    if not data:
        return 0

    by_key: dict[str, str] = {}
    offset = 0
    while True:
        page = cat_mod.list_products(limit=500, offset=offset)
        for item in page.get("items") or []:
            tk = item.get("tiktok")
            if not tk:
                continue
            mk = item.get("match_key") or ""
            for r in tk.get("regions") or []:
                sk = (r.get("seller_sku") or "").strip()
                if sk and mk and mk not in by_key:
                    by_key[mk] = sk
                    break
        offset += 500
        if offset >= page.get("total", 0):
            break

    fixed = 0
    for map_key, row in data.items():
        if not isinstance(row, dict):
            continue
        mk = str(map_key).zfill(4)
        sk = by_key.get(mk)
        if not sk:
            continue
        if row.get("seller_sku") != sk:
            row["seller_sku"] = sk
            fixed += 1

    if fixed:
        _save_tk_map(data)
    return fixed
