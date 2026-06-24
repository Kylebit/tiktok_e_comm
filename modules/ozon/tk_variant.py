"""TikTok 同链接多 SKU（tk_group）→ Ozon 逐规格草稿。"""

from __future__ import annotations

import re
from functools import lru_cache

from modules.catalog.sku_key import tk_match_key
from modules.ozon.price_convert import old_price_cny
from modules.products.image_ai import extract_listing_image_urls

_DIM_RE = re.compile(r"(\d+)\s*[x×*]\s*(\d+)", re.I)


def parse_variant_dims(label: str) -> tuple[str, str]:
    m = _DIM_RE.search(label or "")
    if not m:
        return "", ""
    return m.group(1), m.group(2)


def tk_group_info(cat_item: dict) -> dict | None:
    g = cat_item.get("tk_group")
    if not isinstance(g, dict):
        return None
    keys = [k for k in (g.get("match_keys") or []) if k]
    if len(keys) < 2:
        return None
    return {
        "match_keys": sorted(keys),
        "size": len(keys),
        "primary_key": g.get("primary_key") or keys[0],
        "group_id": "-".join(sorted(keys)),
    }


@lru_cache(maxsize=64)
def _load_group_cached(keys_tuple: tuple[str, ...]) -> dict | None:
    if len(keys_tuple) < 2:
        return None
    try:
        from modules.shopee.publish_group import load_tk_group

        return load_tk_group(list(keys_tuple), "PH")
    except Exception:
        return None


def variant_for_match_key(cat_item: dict, match_key: str) -> dict | None:
    info = tk_group_info(cat_item)
    if not info:
        return None
    group = _load_group_cached(tuple(info["match_keys"]))
    if not group:
        return None
    for v in group.get("variants") or []:
        if v.get("match_key") == match_key:
            return {
                **v,
                "group_keys": info["match_keys"],
                "group_id": info["group_id"],
                "spu_product_id": group.get("product_id") or "",
                "detail": group.get("detail") or {},
                "source_region": group.get("source_region") or "PH",
            }
    return None


def group_variant_index(keys: list[str]) -> dict[str, dict]:
    """match_key → {model_name, image_url, price_cny, price, seller_sku}。"""
    if len(keys) < 2:
        return {}
    group = _load_group_cached(tuple(sorted(keys)))
    if not group:
        return {}
    out: dict[str, dict] = {}
    for v in group.get("variants") or []:
        mk = v.get("match_key")
        if mk:
            out[mk] = v
    return out


def apply_variant_to_draft(cat_item: dict, match_key: str, entry: dict, price_info: dict | None) -> tuple[dict, dict | None, str]:
    """
    多规格组：覆盖图片、价格、尺寸。
    返回 (entry, price_info, variant_label)。
    """
    variant = variant_for_match_key(cat_item, match_key)
    if not variant:
        return entry, price_info, ""

    label = (variant.get("model_name") or "").strip()
    out = dict(entry)
    if variant.get("seller_sku"):
        out["seller_sku"] = variant["seller_sku"]

    images: list[str] = []
    sku_img = (variant.get("image_url") or "").strip()
    if sku_img:
        images.append(sku_img)
    detail = variant.get("detail") or {}
    for u in extract_listing_image_urls(detail):
        if u not in images:
            images.append(u)
    for u in entry.get("image_urls") or []:
        if u not in images:
            images.append(u)
    if images:
        out["image_urls"] = images[:6]

    pinfo = price_info
    pcny = variant.get("price_cny")
    if pcny is not None:
        try:
            cny = int(float(pcny))
        except (TypeError, ValueError):
            cny = None
        if cny and cny > 0:
            reg = (variant.get("source_region") or "PH").upper()
            pinfo = {
                "amount": float(variant.get("price") or 0),
                "currency": "PHP" if reg == "PH" else reg,
                "cny": cny,
                "source": f"tiktok_{reg}_sku",
                "label": f"TK {reg} 规格 {label} → ¥{cny}".strip(),
            }
    return out, pinfo, label
