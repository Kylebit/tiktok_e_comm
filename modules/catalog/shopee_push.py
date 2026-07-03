"""Catalog helpers for TikTok -> Shopee global sync and direct shop publish."""

from __future__ import annotations

from modules.catalog.sku_key import parse_search_key
from modules.shopee.global_sku_map import global_item_id_for_match_key, load_map
from modules.shopee.publish import publish_match_key, update_global_match_key
from modules.shopee.publish_group import (
    load_tk_group,
    publish_group_to_shop,
    publish_tk_group,
    sync_tk_group,
)


def _sibling_keys_for_match_key(match_key: str, region: str) -> list[str] | None:
    key = parse_search_key(match_key)
    if not key:
        return None
    try:
        group = load_tk_group([key], region)
    except (RuntimeError, ValueError):
        return None
    keys = group.get("match_keys") or []
    if len(keys) < 2:
        return None
    if any(global_item_id_for_match_key(k) for k in keys):
        return None
    return keys


def _group_keys_for_match_key(match_key: str, region: str) -> list[str] | None:
    key = parse_search_key(match_key)
    if not key:
        return None
    gid = global_item_id_for_match_key(key)
    if gid:
        entry = load_map().get(str(gid)) or {}
        keys = entry.get("match_keys") or []
        parsed = [parse_search_key(str(k)) for k in keys if parse_search_key(str(k))]
        if len(parsed) >= 2:
            return sorted(set(parsed))
    try:
        group = load_tk_group([key], region)
        keys = group.get("match_keys") or []
        return keys if len(keys) >= 2 else None
    except (RuntimeError, ValueError):
        return None


def sync_tk_group_to_shopee(match_keys: str | list[str], *, region: str = "PH") -> dict:
    if isinstance(match_keys, str):
        keys = [parse_search_key(k) for k in match_keys.replace(";", ",").split(",") if parse_search_key(k)]
    else:
        keys = [parse_search_key(str(k)) for k in match_keys if parse_search_key(str(k))]
    if len(keys) < 2:
        raise ValueError("整组同步至少需要 2 个对齐码")
    result = sync_tk_group(keys, region=region)
    publish_result = publish_group_to_shop(keys, region=region)
    result["shop_publish"] = publish_result
    result["message"] = (
        f"{result.get('message') or '全球商品同步完成'}；"
        f"已发布到 Shopee {str(region).upper()} 店铺 item {publish_result.get('item_id') or ''}"
    )
    return result


def sync_tk_to_shopee_global(match_key: str, *, region: str = "PH") -> dict:
    key = parse_search_key(match_key)
    if not key:
        raise ValueError("无效对齐码")

    group_keys = _group_keys_for_match_key(key, region)
    if group_keys and len(group_keys) >= 2:
        gid = global_item_id_for_match_key(key)
        if gid:
            result = sync_tk_group(group_keys, region=region)
        else:
            result = publish_tk_group(group_keys, region=region)
            result["action"] = "create_global_group"
        publish_result = publish_group_to_shop(group_keys, region=region)
        result["shop_publish"] = publish_result
        gid = result.get("global_item_id") or global_item_id_for_match_key(key) or ""
        result["message"] = (
            f"已同步全球多规格商品 {gid}；"
            f"已发布到 Shopee {str(region).upper()} 店铺 item {publish_result.get('item_id') or ''}"
        )
        return result

    gid = global_item_id_for_match_key(key)
    if gid:
        result = update_global_match_key(key, region)
        publish_result = publish_match_key(key, region, global_only=False, publish_shops=True)
        result["action"] = "update_global"
        result["shop_publish"] = publish_result
        result["message"] = (
            f"已更新全球商品 {gid} 英文信息；"
            f"已发布到 Shopee {str(region).upper()} 店铺 item {publish_result.get('item_id') or ''}"
        )
        return result

    sibling_keys = _sibling_keys_for_match_key(key, region)
    if sibling_keys:
        result = publish_tk_group(sibling_keys, region=region)
        publish_result = publish_group_to_shop(sibling_keys, region=region)
        result["action"] = "create_global_group"
        result["shop_publish"] = publish_result
        gid = result.get("global_item_id")
        result["message"] = (
            f"已创建全球多规格商品 {gid}（{len(sibling_keys)} 个对齐码），"
            f"并发布到 Shopee {str(region).upper()} 店铺 item {publish_result.get('item_id') or ''}"
            if gid
            else "全球多规格商品创建并发布完成"
        )
        return result

    result = publish_match_key(key, region, global_only=False, publish_shops=True)
    result["action"] = "create_global"
    gid = result.get("global_item_id")
    result["message"] = (
        f"已创建全球商品 {gid} 并发布到 Shopee {str(region).upper()} 店铺 item {result.get('item_id') or ''}"
        if gid
        else "全球商品创建并发布完成"
    )
    return result
