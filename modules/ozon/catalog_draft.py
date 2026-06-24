"""基于商品目录的 Ozon 草稿（TikTok 价格 + TK 类目映射 + 动态 profile）。"""

from __future__ import annotations

import sys

from modules.catalog.sku_key import tk_match_key
from modules.ozon.catalog_source import (
    catalog_item_by_seller_sku,
    sync_catalog_to_tk_map,
    to_4digit_offer_id,
    _map_entry_from_item,
    _pick_tk_row,
)
from modules.ozon.category_match import (
    fetch_tk_category_info,
    load_category_options,
    lookup_category_names,
    match_category,
)
from modules.ozon.migrate_attrs import resolve_profile
from modules.ozon.price_convert import old_price_cny, pick_tk_price
from modules.ozon.listing_text import (
    polish_ozon_description,
    polish_ozon_title,
    polish_variant_label,
    tablecloth_hashtags,
)
from modules.ozon.tk_variant import apply_variant_to_draft, parse_variant_dims, tk_group_info
from modules.ozon.logistics_weight import lookup_logistics_weight
from modules.catalog.logistics_weights import lookup_stored
from modules.ozon.webapp_bridge import webapp_dir


def _ozon_translate():
    wd = webapp_dir()
    if str(wd) not in sys.path:
        sys.path.insert(0, str(wd))
    import translate  # noqa: WPS433

    return translate


def _ozon_deepseek():
    wd = webapp_dir()
    if str(wd) not in sys.path:
        sys.path.insert(0, str(wd))
    import deepseek_draft  # noqa: WPS433

    return deepseek_draft


def _lookup_material_dict_id(material: str, category_id: int, type_id: int) -> int:
    wd = webapp_dir()
    if str(wd) not in sys.path:
        sys.path.insert(0, str(wd))
    from app import lookup_material_dict_id  # noqa: WPS433

    return lookup_material_dict_id(material, category_id, type_id)


def _lookup_color_dict_id(color_name: str, category_id: int, type_id: int) -> int:
    wd = webapp_dir()
    if str(wd) not in sys.path:
        sys.path.insert(0, str(wd))
    from app import lookup_color_dict_id  # noqa: WPS433

    return lookup_color_dict_id(color_name, category_id, type_id)


def _sanitize_dim(val: str, default: str, max_cm: int = 300) -> str:
    s = (val or "").strip()
    if not s:
        return default
    try:
        n = int(float(s.replace(",", ".")))
    except ValueError:
        return default
    if n <= 0 or n > max_cm:
        return default
    return str(n)


def _effective_profile(type_id: int, explicit: str | None = None) -> str:
    profile = (explicit or "").strip() or resolve_profile(type_id)
    if profile == "generic":
        return resolve_profile(type_id)
    return profile


def _product_type_hint(migrate_profile: str, type_id: int) -> str:
    if migrate_profile == "tablecloth":
        return "tablecloth"
    if migrate_profile == "sticker" or int(type_id) == 91971:
        return "sticker"
    return ""


def _pre_dims(tpl: dict, variant_label: str) -> tuple[str, str]:
    len_cm = _sanitize_dim(str(tpl["len_cm"]), str(tpl["len_cm"]))
    wid_cm = _sanitize_dim(str(tpl["wid_cm"]), str(tpl["wid_cm"]))
    if variant_label:
        v_len, v_wid = parse_variant_dims(variant_label)
        if v_len:
            len_cm = _sanitize_dim(v_len, len_cm)
        if v_wid:
            wid_cm = _sanitize_dim(v_wid, wid_cm)
    return len_cm, wid_cm


def _invoke_deepseek(
    *,
    title_ms: str,
    offer_id: str,
    price_info: dict | None,
    tk_cat: dict,
    cat_match: dict,
    type_id: int,
    migrate_profile: str,
    rule_context: dict | None = None,
) -> dict:
    match_method = cat_match.get("match_method") or ""
    if match_method in ("tk_category_map", "title_tablecloth"):
        candidates: list = []
    else:
        candidates = cat_match.get("candidates") or load_category_options()

    deepseek_draft = _ozon_deepseek()
    return deepseek_draft.generate_draft(
        title_ms,
        offer_id,
        price_local=price_info["amount"] if price_info else None,
        price_currency=price_info["currency"] if price_info else None,
        tk_category_path=tk_cat["path"],
        tk_category_leaf=tk_cat["leaf"],
        category_candidates=candidates,
        product_type_hint=_product_type_hint(migrate_profile, type_id),
        rule_context=rule_context,
    )


def build_draft(seller_sku: str) -> dict:
    sync_catalog_to_tk_map(max_items=50)

    cat_item = catalog_item_by_seller_sku(seller_sku)
    if not cat_item:
        return {"error": f"商品目录中未找到 TikTok 商品 seller_sku={seller_sku}"}

    match_key = tk_match_key(seller_sku)
    entry = _map_entry_from_item(
        cat_item, fetch_detail=True, match_key=match_key, seller_sku=seller_sku.strip()
    )
    if not entry:
        return {"error": "无法解析 TikTok 商品行"}

    row = _pick_tk_row(cat_item, match_key=match_key, seller_sku=seller_sku.strip())
    offer_id = to_4digit_offer_id(entry["seller_sku"])
    title_ms = entry.get("title") or ""

    price_info = pick_tk_price(cat_item)
    entry, price_info, variant_label = apply_variant_to_draft(cat_item, match_key, entry, price_info)
    title_ms = entry.get("title") or title_ms
    if variant_label:
        title_ms = f"{title_ms} [{variant_label}]"

    price_cny = price_info["cny"] if price_info else None
    price_label = price_info["label"] if price_info else ""

    product_id = (row or {}).get("product_id") or entry.get("tk_id") or ""
    shop_cipher = (row or {}).get("shop_cipher") or ""
    tk_cat = fetch_tk_category_info(product_id, shop_cipher)

    cat_match = match_category(
        title=title_ms,
        tk_path=tk_cat["path"],
        tk_leaf=tk_cat["leaf"],
        tk_category_id=tk_cat["category_id"],
    )

    translate = _ozon_translate()
    tpl = translate.CATEGORY_TEMPLATES["default"]
    default_category_id = tpl["category_id"]
    default_type_id = tpl["type_id"]

    pre_len, pre_wid = _pre_dims(tpl, variant_label or "")
    sku_name = ""
    if row:
        sku_name = (row.get("sku_name") or row.get("model_name") or "").strip()
    rule_context = translate.build_rule_context(
        title_ms,
        offer_id,
        len_cm=pre_len,
        wid_cm=pre_wid,
        kit=tpl["kit"],
        variant_label=variant_label or "",
        sku_name=sku_name,
    )

    source = "fallback_template"
    d: dict = {}
    deepseek_ok = False

    if cat_match.get("match_method") == "tk_category_map":
        type_id = int(cat_match["type_id"])
        category_id = int(cat_match["category_id"])
        migrate_profile = _effective_profile(type_id, cat_match.get("migrate_profile"))
        try:
            d = _invoke_deepseek(
                title_ms=title_ms,
                offer_id=offer_id,
                price_info=price_info,
                tk_cat=tk_cat,
                cat_match=cat_match,
                type_id=type_id,
                migrate_profile=migrate_profile,
                rule_context=rule_context,
            )
            d["type_id"] = type_id
            deepseek_ok = True
            source = "tk_category_map+deepseek"
        except Exception as e:
            d = {"error": str(e)}
            source = "tk_category_map+fallback"
    elif cat_match.get("match_method") == "title_tablecloth":
        type_id = int(cat_match["type_id"])
        category_id = int(cat_match["category_id"])
        migrate_profile = "tablecloth"
        try:
            d = _invoke_deepseek(
                title_ms=title_ms,
                offer_id=offer_id,
                price_info=price_info,
                tk_cat=tk_cat,
                cat_match=cat_match,
                type_id=type_id,
                migrate_profile=migrate_profile,
                rule_context=rule_context,
            )
            d["type_id"] = 92692
            if not d.get("material"):
                d["material"] = "Полиэстер"
            deepseek_ok = True
            source = "deepseek_tablecloth"
        except Exception as e:
            d = {"error": str(e)}
            source = "title_tablecloth+fallback"
    else:
        type_id = default_type_id
        category_id = default_category_id
        migrate_profile = "generic"
        try:
            d = _invoke_deepseek(
                title_ms=title_ms,
                offer_id=offer_id,
                price_info=price_info,
                tk_cat=tk_cat,
                cat_match=cat_match,
                type_id=type_id,
                migrate_profile=migrate_profile,
                rule_context=rule_context,
            )
            deepseek_ok = True
            source = "deepseek"
        except Exception as e:
            d = {"error": str(e)}
            source = "fallback_template"

        if cat_match.get("match_method") == "rule_auto" and cat_match.get("suggested"):
            sug = cat_match["suggested"]
            d["type_id"] = sug["type_id"]
            source = "rule_auto+deepseek" if deepseek_ok else "rule_auto+fallback"

        ai_type_id = int(d.get("type_id", default_type_id))
        cat_entry = next((c for c in load_category_options() if c["type_id"] == ai_type_id), None)
        category_id = cat_entry["cat_id"] if cat_entry else default_category_id
        type_id = ai_type_id if cat_entry else default_type_id
        migrate_profile = _effective_profile(type_id, cat_match.get("migrate_profile"))

    if variant_label and price_cny:
        price_cny_str = str(int(price_cny))
        old_price_str = str(old_price_cny(price_cny))
    elif d.get("price_cny"):
        price_cny_str = str(int(float(d["price_cny"])))
        old_price_str = str(int(float(d.get("old_price_cny", float(d["price_cny"]) * 1.3))))
    elif price_cny:
        price_cny_str = str(price_cny)
        old_price_str = str(old_price_cny(price_cny))
    else:
        price_cny_str, old_price_str = "45", "62"

    color_name = d.get("color_name", tpl["color"][0])
    material_name = d.get("material", "Полиэстер" if migrate_profile == "tablecloth" else "ПВХ (поливинилхлорид)")
    cat_names = lookup_category_names(category_id, type_id)

    len_cm = _sanitize_dim(str(d.get("len_cm", tpl["len_cm"])), str(tpl["len_cm"]))
    wid_cm = _sanitize_dim(str(d.get("wid_cm", tpl["wid_cm"])), str(tpl["wid_cm"]))
    if variant_label:
        v_len, v_wid = parse_variant_dims(variant_label)
        if v_len:
            len_cm = _sanitize_dim(v_len, len_cm)
        if v_wid:
            wid_cm = _sanitize_dim(v_wid, wid_cm)

    rule_title = translate.draft_title(title_ms, offer_id, len_cm=len_cm, wid_cm=wid_cm)
    rule_desc = translate.draft_description(
        title_ms,
        len_cm=len_cm,
        wid_cm=wid_cm,
        kit=d.get("kit", tpl["kit"]),
    )
    ai_title = (d.get("title") or "").strip()
    ai_desc = (d.get("description") or "").strip()

    if ai_title:
        draft_title = polish_ozon_title(
            ai_title,
            len_cm=len_cm,
            wid_cm=wid_cm,
            migrate_profile=migrate_profile,
        )
        title_source = "deepseek"
    else:
        draft_title = polish_ozon_title(
            rule_title,
            len_cm=len_cm,
            wid_cm=wid_cm,
            migrate_profile=migrate_profile,
        )
        title_source = "rule_fallback"

    if variant_label:
        variant_label = polish_variant_label(variant_label)
    if variant_label and variant_label not in draft_title:
        draft_title = polish_ozon_title(
            f"{draft_title}, {variant_label}",
            len_cm=len_cm,
            wid_cm=wid_cm,
            migrate_profile=migrate_profile,
        )

    if ai_desc:
        draft_description = polish_ozon_description(ai_desc)
        desc_source = "deepseek"
    else:
        draft_description = polish_ozon_description(rule_desc)
        desc_source = "rule_fallback"
    hashtags = d.get("hashtags") or (tablecloth_hashtags() if migrate_profile == "tablecloth" else "#декордлядома")

    group = tk_group_info(cat_item)

    weight = d.get("weight_g", tpl["weight"])
    depth = d.get("depth_mm", tpl["depth"])
    width = d.get("width_mm", tpl["width"])
    height = d.get("height_mm", tpl["height"])
    weight_source = "template"
    logistics_meta: dict = {}

    lw = lookup_stored(entry["seller_sku"]) or lookup_logistics_weight(entry["seller_sku"], shop_cipher)
    if lw:
        weight = lw["weight_g"]
        weight_source = "logistics"
        logistics_meta = {
            "logistics_package_count": lw.get("package_count", 0),
            "logistics_sample_package_id": lw.get("sample_package_id", ""),
        }
        if lw.get("depth"):
            depth = lw["depth"]
        if lw.get("width"):
            width = lw["width"]
        if lw.get("height"):
            height = lw["height"]

    return {
        "offer_id": offer_id,
        "seller_sku": entry["seller_sku"],
        "title_ms": title_ms,
        "images": entry.get("image_urls") or [],
        "draft_title": draft_title,
        "draft_description": draft_description,
        "hashtags": hashtags,
        "material": material_name,
        "material_dict_id": _lookup_material_dict_id(material_name, category_id, type_id),
        "category_id": category_id,
        "type_id": type_id,
        "category_name_zh": cat_names["category_name_zh"],
        "type_name_zh": cat_names["type_name_zh"],
        "migrate_profile": migrate_profile,
        "color_name": color_name,
        "color_dict_id": _lookup_color_dict_id(color_name, category_id, type_id),
        "kit": d.get("kit", tpl["kit"]),
        "weight": weight,
        "depth": depth,
        "width": width,
        "height": height,
        "len_cm": len_cm,
        "wid_cm": wid_cm,
        "price": price_cny_str,
        "old_price": old_price_str,
        "source": source,
        "title_source": title_source,
        "desc_source": desc_source,
        "deepseek_used": deepseek_ok,
        "price_source": price_info["source"] if price_info else "",
        "price_label": price_label,
        "price_local": price_info["amount"] if price_info else None,
        "price_currency": price_info["currency"] if price_info else "",
        "price_cny_computed": price_cny,
        "tk_category_id": tk_cat["category_id"],
        "tk_category_path": tk_cat["path"],
        "tk_category_leaf": tk_cat["leaf"],
        "category_match_method": cat_match.get("match_method"),
        "category_match_score": cat_match.get("best_score"),
        "variant_label": variant_label,
        "tk_group_id": group["group_id"] if group else "",
        "tk_group_keys": group["match_keys"] if group else [],
        "weight_source": weight_source,
        **logistics_meta,
        "error": d.get("error"),
    }
