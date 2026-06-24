"""目录：TikTok → Shopee CNSC 全球商品（单 SKU 或同链接多规格）。"""

from __future__ import annotations

from modules.catalog.sku_key import parse_search_key
from modules.shopee.global_sku_map import global_item_id_for_match_key, load_map
from modules.shopee.publish import publish_match_key, update_global_match_key
from modules.shopee.publish_group import load_tk_group, publish_tk_group, sync_tk_group


def _sibling_keys_for_match_key(match_key: str, region: str) -> list[str] | None:
    """若 TK 同 product 有多个对齐码且均未映射，返回整组 keys。"""
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


def _group_keys_for_match_key(match_key: str) -> list[str] | None:
    """从全球映射或 TK 加载整组对齐码。"""
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
        group = load_tk_group([key], "PH")
        keys = group.get("match_keys") or []
        return keys if len(keys) >= 2 else None
    except (RuntimeError, ValueError):
        return None


def sync_tk_group_to_shopee(match_keys: str | list[str], *, region: str = "PH") -> dict:
    """整组 TK→Shopee：Color 规格图 + 人民币价 + 库存 + 英文文案。"""
    if isinstance(match_keys, str):
        keys = [parse_search_key(k) for k in match_keys.replace(";", ",").split(",") if parse_search_key(k)]
    else:
        keys = [parse_search_key(str(k)) for k in match_keys if parse_search_key(str(k))]
    if len(keys) < 2:
        raise ValueError("整组同步至少需要 2 个对齐码")
    result = sync_tk_group(keys, region=region)
    return result


def sync_tk_to_shopee_global(match_key: str, *, region: str = "PH") -> dict:
    """
    有全球映射 → update_global（PH 英文 + DeepSeek 刷新标题/描述）
    同 TK 多 SKU 且无映射 → publish_group（单全球链接多规格）
    否则 → publish global_only（新建单规格全球 SKU）
    """
    key = parse_search_key(match_key)
    if not key:
        raise ValueError("无效对齐码")

    group_keys = _group_keys_for_match_key(key)
    if group_keys and len(group_keys) >= 2:
        gid = global_item_id_for_match_key(key)
        if gid:
            result = sync_tk_group(group_keys, region=region)
            return result

    gid = global_item_id_for_match_key(key)
    if gid:
        result = update_global_match_key(key, region)
        result["action"] = "update_global"
        result["message"] = f"已更新全球商品 {gid}（英文标题/描述）"
        return result

    sibling_keys = _sibling_keys_for_match_key(key, region)
    if sibling_keys:
        result = publish_tk_group(sibling_keys, region=region)
        result["action"] = "create_global_group"
        gid = result.get("global_item_id")
        result["message"] = (
            f"已创建全球多规格商品 {gid}（{len(sibling_keys)} 个对齐码），请在 CNSC 后台发布"
            if gid
            else "全球多规格商品创建完成"
        )
        return result

    result = publish_match_key(key, region, global_only=True)
    result["action"] = "create_global"
    gid = result.get("global_item_id")
    result["message"] = (
        f"已创建全球商品 {gid}，请在 CNSC 后台发布到各国店铺"
        if gid
        else "全球商品创建完成"
    )
    return result
