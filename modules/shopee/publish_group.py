"""TikTok 多 SKU（同 product_id）→ Shopee CNSC 全球商品（单链接多规格）。"""

from __future__ import annotations

from modules.catalog.sku_key import parse_search_key, tk_match_key
from modules.shopee.auth import ensure_shop_token
from modules.shopee.client import merchant_get, merchant_post
from modules.shopee.global_copy import TK_SOURCE_ORDER, build_global_copy, english_variant_label, is_english_listing_text
from modules.shopee.global_sku_map import global_item_id_for_match_key, load_map, upsert_global_group_entry
from modules.shopee.pricing import REGION_CURRENCY, tk_local_to_cny
from modules.shopee.publish import (
    DEFAULT_CATEGORY,
    _english_safe_sku,
    _fetch_tk_detail,
    _run_publish_task,
    _find_tk_row,
    _first_url,
    _global_attribute_list,
    _merchant_token,
    _reference_item,
    _shop_meta,
    _upload_images,
)
from modules.shopee.global_sku_map import record_shop_item
from modules.shopee.shops import sync_shop_ids


def _parse_keys(raw: str | list[str]) -> list[str]:
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    else:
        parts = [str(p).strip() for p in raw if str(p).strip()]
    keys: list[str] = []
    seen: set[str] = set()
    for p in parts:
        k = parse_search_key(p)
        if not k or k in seen:
            continue
        seen.add(k)
        keys.append(k)
    return keys


def _sku_image_url(sku: dict) -> str:
    for attr in sku.get("sales_attributes") or []:
        img = attr.get("sku_img")
        if img:
            u = _first_url([img])
            if u:
                return u
    return ""


def _option_label(sku: dict, fallback: str) -> str:
    attrs = sku.get("sales_attributes") or []
    raw = ""
    if attrs:
        raw = (attrs[0].get("value_name") or "").strip()
    return english_variant_label(raw, fallback)


def _sku_metrics(sku: dict, detail: dict, *, region: str) -> tuple[float, int, float, dict, float]:
    local_price = float((sku.get("price") or {}).get("sale_price") or 0)
    currency = (sku.get("price") or {}).get("currency") or REGION_CURRENCY.get(region.upper(), "")
    price_cny = tk_local_to_cny(local_price, region=region, currency=currency)
    stock = sum(int(i.get("quantity") or 0) for i in (sku.get("inventory") or [])) or 50
    w = sku.get("sku_weight") or detail.get("package_weight") or {}
    weight = float(w.get("value") or 0.2)
    if (w.get("unit") or "").upper() == "GRAM":
        weight /= 1000.0
    dim = sku.get("sku_dimensions") or detail.get("package_dimensions") or {}
    return local_price, stock, max(weight, 0.01), dim, price_cny


def load_tk_group(match_keys: list[str], source_region: str = "PH") -> dict:
    """按对齐码加载同一 TK 商品的多 SKU（须属同一 product_id）。"""
    keys = _parse_keys(match_keys)
    if len(keys) < 2:
        raise ValueError("至少需要 2 个对齐码，如 0402,0403,0404,0405")

    reg_order = list(TK_SOURCE_ORDER)
    src = (source_region or "").upper()
    if src in reg_order:
        reg_order.remove(src)
        reg_order.insert(0, src)
    row = None
    detail = None
    used_region = ""
    fallback: tuple | None = None
    for reg in reg_order:
        try:
            cand_row = _find_tk_row(keys[0], reg)
            cand_detail = _fetch_tk_detail(cand_row)
            title = (cand_detail.get("title") or "").strip()
            if is_english_listing_text(title):
                row, detail, used_region = cand_row, cand_detail, reg
                break
            if fallback is None:
                fallback = (cand_row, cand_detail, reg)
        except RuntimeError:
            continue
    if not row and fallback:
        row, detail, used_region = fallback
    if not row or not detail:
        raise RuntimeError(f"未找到 TK 对齐码 {keys[0]}")

    sku_by_key: dict[str, dict] = {}
    for s in detail.get("skus") or []:
        mk = tk_match_key(s.get("seller_sku") or "")
        if mk in keys:
            sku_by_key[mk] = s
    missing = [k for k in keys if k not in sku_by_key]
    if missing:
        raise RuntimeError(f"TK 商品缺少对齐码: {', '.join(missing)}")

    variants = []
    for k in keys:
        s = sku_by_key[k]
        local_price, stock, weight, dim, price_cny = _sku_metrics(s, detail, region=used_region)
        variants.append(
            {
                "match_key": k,
                "seller_sku": (s.get("seller_sku") or "").strip(),
                "sku_id": str(s.get("id") or ""),
                "model_name": _option_label(s, k),
                "price": local_price,
                "price_cny": price_cny,
                "stock": stock,
                "weight": weight,
                "dimension": dim,
                "image_url": _sku_image_url(s),
            }
        )

    return {
        "match_keys": keys,
        "source_region": used_region,
        "product_id": row["product_id"],
        "shop_cipher": row["cipher"],
        "detail": detail,
        "variants": variants,
    }


def _detail_with_primary_sku(detail: dict, primary_key: str) -> dict:
    skus = list(detail.get("skus") or [])
    if not skus:
        return detail
    primary = None
    others = []
    for sku in skus:
        if primary is None and tk_match_key(sku.get("seller_sku") or "") == primary_key:
            primary = sku
        else:
            others.append(sku)
    if not primary:
        return detail
    out = dict(detail)
    out["skus"] = [primary, *others]
    return out


def _tier_options_with_images(variants: list[dict]) -> list[dict]:
    """Color 规格选项 + TK sku_img 上传为 Shopee image_id。"""
    options: list[dict] = []
    for v in variants:
        opt: dict = {"option": v["model_name"]}
        url = (v.get("image_url") or "").strip()
        if url:
            try:
                img_ids = _upload_images([url], max_images=1)
                if img_ids:
                    opt["image"] = {"image_id": img_ids[0]}
            except Exception:
                pass
        options.append(opt)
    return options


def _merchant_ctx(region: str) -> tuple[int, int, str, str]:
    shop_map = sync_shop_ids()
    reg = region.upper()
    if reg not in shop_map:
        raise RuntimeError(f"无 Shopee 主店: {reg}")
    shop_id = int(shop_map[reg])
    token = ensure_shop_token(shop_id)
    meta = _shop_meta(shop_id, token)
    merchant_id = int(meta.get("merchant_id") or 0)
    if not merchant_id:
        raise RuntimeError("店铺无 merchant_id，无法走 CNSC 全球商品")
    mtoken = _merchant_token(shop_id, token)
    return shop_id, merchant_id, token, mtoken


def publish_tk_group(
    match_keys: str | list[str],
    *,
    region: str = "PH",
    dry_run: bool = False,
    tier_name: str = "Color",
) -> dict:
    """
    将一个 TK 多 SKU 链接发布为 1 个 Shopee 全球商品（init_tier_variation）。
    默认用 PH 英文母版；各国店铺请在 CNSC 后台手动发布。
    """
    group = load_tk_group(_parse_keys(match_keys) if isinstance(match_keys, str) else match_keys, region)
    keys = group["match_keys"]
    detail = group["detail"]
    tk_source = group["source_region"]

    for k in keys:
        existing = global_item_id_for_match_key(k)
        if existing:
            raise RuntimeError(f"对齐码 {k} 已有全球商品 {existing}，请勿重复创建")

    reg = tk_source
    shop_id, merchant_id, token, mtoken = _merchant_ctx(reg)

    urls = [_first_url(detail.get("main_images"))]
    for img in detail.get("main_images") or []:
        u = _first_url([img])
        if u and u not in urls:
            urls.append(u)

    primary = keys[0]
    copy = build_global_copy(detail, primary, source_region=tk_source)
    v0 = group["variants"][0]
    dim = v0["dimension"]

    if dry_run:
        return {
            "dry_run": True,
            "match_keys": keys,
            "tk_source_region": tk_source,
            "product_id": group["product_id"],
            "shop_id": shop_id,
            "global_title": copy["title"],
            "global_description_len": len(copy["description"]),
            "variants": [
                {
                    **v,
                    "price_note": f"{v['price']} {REGION_CURRENCY.get(tk_source, '')} → ¥{v['price_cny']}",
                }
                for v in group["variants"]
            ],
            "image_urls": urls[:8],
            "tier_name": tier_name,
        }

    ref = _reference_item(reg, shop_id, token)
    category_id = int((ref or {}).get("category_id") or DEFAULT_CATEGORY.get(reg) or 101157)
    image_ids = _upload_images(urls)

    global_body = {
        "category_id": category_id,
        "global_item_name": copy["title"][:180],
        "description": copy["description"],
        "global_item_sku": _english_safe_sku(primary),
        "original_price": float(v0["price_cny"] or 99),
        "weight": float(v0["weight"] or 0.2),
        "dimension": {
            "package_length": max(int(float(dim.get("length") or 30)), 1),
            "package_width": max(int(float(dim.get("width") or 20)), 1),
            "package_height": max(int(float(dim.get("height") or 2)), 1),
        },
        "image": {"image_id_list": image_ids[:9]},
        "attribute_list": _global_attribute_list(merchant_id, mtoken, category_id, ref),
        "brand": (ref or {}).get("brand") or {"brand_id": 0, "original_brand_name": "NoBrand"},
        "condition": "NEW",
        "seller_stock": [{"location_id": "CNZ", "stock": int(v0["stock"] or 50)}],
        "pre_order": {"days_to_ship": 2},
    }
    g_resp = merchant_post("/api/v2/global_product/add_global_item", merchant_id, mtoken, global_body)
    if g_resp.get("error"):
        raise RuntimeError(g_resp.get("message") or g_resp.get("error") or g_resp)
    gid = (g_resp.get("response") or {}).get("global_item_id")
    if not gid:
        raise RuntimeError(f"add_global_item 无 global_item_id: {g_resp}")

    options = _tier_options_with_images(group["variants"])
    models = [
        {
            "tier_index": [i],
            "global_model_sku": _english_safe_sku(v["match_key"]),
            "original_price": float(v["price_cny"] or v0["price_cny"]),
            "seller_stock": [{"location_id": "CNZ", "stock": int(v["stock"] or 50)}],
        }
        for i, v in enumerate(group["variants"])
    ]
    init_body = {
        "global_item_id": int(gid),
        "tier_variation": [{"name": tier_name[:14], "option_list": options}],
        "global_model": models,
    }
    init_resp = merchant_post(
        "/api/v2/global_product/init_tier_variation",
        merchant_id,
        mtoken,
        init_body,
    )
    if (init_resp.get("error") or "").strip() not in ("", "-"):
        raise RuntimeError(init_resp.get("message") or init_resp.get("error") or init_resp)

    gml = merchant_get(
        "/api/v2/global_product/get_global_model_list",
        merchant_id,
        mtoken,
        {"global_item_id": int(gid)},
    )
    global_models = (gml.get("response") or {}).get("global_model") or []
    model_map = []
    for gm in global_models:
        model_map.append(
            {
                "global_model_id": str(gm.get("global_model_id") or ""),
                "global_model_sku": (gm.get("global_model_sku") or "").strip(),
                "tier_index": gm.get("tier_index") or [],
            }
        )

    upsert_global_group_entry(
        str(gid),
        match_keys=keys,
        title=copy["title"],
        tier_name=tier_name,
        models=[
            {
                "model_name": v["model_name"],
                "global_model_sku": v["match_key"],
                "tk_sku_id": v["sku_id"],
                "tk_seller_sku": v["seller_sku"],
            }
            for v in group["variants"]
        ],
        tk_product_id=str(group["product_id"]),
        tk_source_region=tk_source,
    )

    return {
        "ok": True,
        "action": "create_global_group",
        "global_item_id": int(gid),
        "match_keys": keys,
        "tk_source_region": tk_source,
        "tk_product_id": group["product_id"],
        "global_title": copy["title"],
        "global_models": model_map,
        "message": f"已创建全球商品 {gid}（{len(keys)} 规格），请在 CNSC 后台发布到各国店铺",
        "raw_add": g_resp,
        "raw_init": init_resp,
    }


def register_tk_group(
    match_keys: str | list[str],
    global_item_id: int | str,
    *,
    region: str = "PH",
    tier_name: str = "Color",
) -> dict:
    """全球商品已存在时，从 API 拉取规格并写入 shopee_global_sku_map.json。"""
    gid = int(global_item_id)
    if gid <= 0:
        raise ValueError("需要有效的 global_item_id")

    group = load_tk_group(_parse_keys(match_keys) if isinstance(match_keys, str) else match_keys, region)
    keys = group["match_keys"]
    detail = group["detail"]
    tk_source = group["source_region"]

    reg = tk_source
    shop_map = sync_shop_ids()
    shop_id = int(shop_map[reg])
    token = ensure_shop_token(shop_id)
    meta = _shop_meta(shop_id, token)
    merchant_id = int(meta.get("merchant_id") or 0)
    if not merchant_id:
        raise RuntimeError("店铺无 merchant_id")
    mtoken = _merchant_token(shop_id, token)

    copy = build_global_copy(detail, keys[0], source_region=tk_source)
    gml = merchant_get(
        "/api/v2/global_product/get_global_model_list",
        merchant_id,
        mtoken,
        {"global_item_id": gid},
    )
    if gml.get("error"):
        raise RuntimeError(gml.get("message") or gml.get("error") or gml)
    global_models = (gml.get("response") or {}).get("global_model") or []
    if not global_models:
        raise RuntimeError(f"全球商品 {gid} 无规格列表")

    sku_by_key = {v["match_key"]: v for v in group["variants"]}
    model_rows = []
    model_map = []
    for gm in global_models:
        sk = parse_search_key(str(gm.get("global_model_sku") or ""))
        v = sku_by_key.get(sk) or {}
        model_rows.append(
            {
                "model_name": v.get("model_name") or sk,
                "global_model_sku": sk,
                "tk_sku_id": v.get("sku_id", ""),
                "tk_seller_sku": v.get("seller_sku", ""),
            }
        )
        model_map.append(
            {
                "global_model_id": str(gm.get("global_model_id") or ""),
                "global_model_sku": sk,
                "tier_index": gm.get("tier_index") or [],
            }
        )

    upsert_global_group_entry(
        str(gid),
        match_keys=keys,
        title=copy["title"],
        tier_name=tier_name,
        models=model_rows,
        tk_product_id=str(group["product_id"]),
        tk_source_region=tk_source,
    )

    return {
        "ok": True,
        "action": "register_global_group",
        "global_item_id": gid,
        "match_keys": keys,
        "tk_source_region": tk_source,
        "global_title": copy["title"],
        "global_models": model_map,
        "message": f"已写入全球商品 {gid} 映射（{len(keys)} 规格）",
    }


def sync_tk_group(
    match_keys: str | list[str],
    *,
    region: str = "PH",
    tier_name: str = "",
) -> dict:
    """
    整组同步：更新已有全球商品的英文文案、Color 规格图、人民币价与库存。
    无全球映射时走 publish_tk_group 新建。
    """
    group = load_tk_group(_parse_keys(match_keys) if isinstance(match_keys, str) else match_keys, region)
    keys = group["match_keys"]
    tk_source = group["source_region"]
    gid = global_item_id_for_match_key(keys[0])
    if not gid:
        return publish_tk_group(keys, region=region, tier_name=tier_name or "Color")

    reg = tk_source
    shop_id, merchant_id, _token, mtoken = _merchant_ctx(reg)
    detail = group["detail"]
    primary = keys[0]
    copy = build_global_copy(detail, primary, source_region=tk_source)

    map_entry = load_map().get(str(gid)) or {}
    tier = (tier_name or map_entry.get("tier_name") or "Color")[:14]

    upd = merchant_post(
        "/api/v2/global_product/update_global_item",
        merchant_id,
        mtoken,
        {
            "global_item_id": int(gid),
            "global_item_name": copy["title"][:180],
            "description": copy["description"],
        },
    )
    if (upd.get("error") or "").strip() not in ("", "-"):
        raise RuntimeError(upd.get("message") or upd.get("error") or upd)

    options = _tier_options_with_images(group["variants"])
    tier_resp = merchant_post(
        "/api/v2/global_product/update_tier_variation",
        merchant_id,
        mtoken,
        {
            "global_item_id": int(gid),
            "tier_variation": [{"name": tier, "option_list": options}],
        },
    )
    tier_err = (tier_resp.get("error") or "").strip()
    if tier_err and tier_err != "-":
        raise RuntimeError(tier_resp.get("message") or tier_err or tier_resp)

    gml = merchant_get(
        "/api/v2/global_product/get_global_model_list",
        merchant_id,
        mtoken,
        {"global_item_id": int(gid)},
    )
    global_models = (gml.get("response") or {}).get("global_model") or []
    by_sku = {parse_search_key(str(m.get("global_model_sku") or "")): m for m in global_models}

    model_updates = []
    price_rows = []
    for v in group["variants"]:
        mk = v["match_key"]
        gm = by_sku.get(mk)
        if not gm:
            continue
        cny = float(v["price_cny"])
        model_updates.append(
            {
                "global_model_id": int(gm["global_model_id"]),
                "global_model_sku": mk,
                "original_price": cny,
                "seller_stock": [{"location_id": "CNZ", "stock": int(v["stock"] or 50)}],
            }
        )
        price_rows.append(
            {
                "match_key": mk,
                "local_price": v["price"],
                "currency": REGION_CURRENCY.get(tk_source, ""),
                "price_cny": cny,
            }
        )

    model_resp = {}
    if model_updates:
        model_resp = merchant_post(
            "/api/v2/global_product/update_global_model",
            merchant_id,
            mtoken,
            {"global_item_id": int(gid), "global_model": model_updates},
        )
        m_err = (model_resp.get("error") or "").strip()
        if m_err and m_err != "-":
            raise RuntimeError(model_resp.get("message") or m_err or model_resp)

    upsert_global_group_entry(
        str(gid),
        match_keys=keys,
        title=copy["title"],
        tier_name=tier,
        models=[
            {
                "model_name": v["model_name"],
                "global_model_sku": v["match_key"],
                "tk_sku_id": v["sku_id"],
                "tk_seller_sku": v["seller_sku"],
            }
            for v in group["variants"]
        ],
        tk_product_id=str(group["product_id"]),
        tk_source_region=tk_source,
    )

    return {
        "ok": True,
        "action": "sync_global_group",
        "global_item_id": int(gid),
        "match_keys": keys,
        "tk_source_region": tk_source,
        "global_title": copy["title"],
        "prices_cny": price_rows,
        "variant_images": sum(1 for o in options if o.get("image")),
        "message": f"已整组同步全球商品 {gid}（{len(keys)} 规格 · Color 图 · ¥价 · 库存）",
        "raw_tier": tier_resp,
        "raw_models": model_resp,
    }


def publish_group_to_shop(
    match_keys: str | list[str],
    *,
    region: str = "PH",
) -> dict:
    group = load_tk_group(_parse_keys(match_keys) if isinstance(match_keys, str) else match_keys, region)
    keys = group["match_keys"]
    gid = global_item_id_for_match_key(keys[0])
    if not gid:
        raise RuntimeError(f"未找到 {keys[0]} 的全球商品映射，请先同步全球商品")

    shop_id, _merchant_id, token, _mtoken = _merchant_ctx(region)
    ref = _reference_item(region.upper(), shop_id, token)
    detail = _detail_with_primary_sku(group["detail"], keys[0])
    result = _run_publish_task(
        global_item_id=int(gid),
        detail=detail,
        region=region.upper(),
        shop_id=shop_id,
        token=token,
        model_sku=keys[0],
        ref=ref,
    )
    if result.get("item_id"):
        record_shop_item(str(gid), region.upper(), shop_id=shop_id, item_id=result["item_id"])
    return {
        **result,
        "flow": "publish_group_to_shop",
        "match_keys": keys,
        "region": region.upper(),
        "shop_id": shop_id,
    }
