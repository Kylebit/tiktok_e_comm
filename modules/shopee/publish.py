"""TikTok → Shopee 铺货（首版：单 SKU、无变体）。"""

from __future__ import annotations

import re
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from core import auth
from core.api_client import get as tk_get
from core.db import connect, init_db
from core.http_retry import DEFAULT_SSL_CTX, urlopen
from modules.catalog.sku_key import parse_search_key, tk_match_key, tk_region
from modules.shopee.auth import ensure_merchant_token, ensure_shop_token
from modules.shopee.client import (
    get_shop_info,
    merchant_get,
    merchant_post,
    shop_get,
    shop_post,
    upload_image,
)
from modules.shopee.global_copy import TK_SOURCE_ORDER, build_global_copy
from modules.shopee.global_sku_map import global_item_id_for_match_key, record_shop_item, upsert_global_entry
from modules.shopee.pricing import tk_local_to_cny
from modules.shopee.shops import sync_shop_ids

# TH 墙贴类目（category_recommend + 同类商品实测）
DEFAULT_CATEGORY = {
    "TH": 101157,
}


def _merchant_token(shop_id: int, shop_token: str) -> str:
    meta = _shop_meta(shop_id, shop_token)
    merchant_id = int(meta.get("merchant_id") or 0)
    if not merchant_id:
        return shop_token
    return ensure_merchant_token(merchant_id, shop_id=shop_id)


def _first_url(images) -> str:
    for img in images or []:
        for key in ("urls", "thumb_urls"):
            urls = img.get(key) or []
            if urls:
                return urls[0]
    return ""


def _collect_image_urls(detail: dict) -> list[str]:
    urls: list[str] = []
    for img in detail.get("main_images") or []:
        u = _first_url([img])
        if u and u not in urls:
            urls.append(u)
    if urls:
        return urls
    for sku in detail.get("skus") or []:
        for attr in sku.get("sales_attributes") or []:
            img = attr.get("sku_img")
            if not img:
                continue
            u = _first_url([img])
            if u and u not in urls:
                urls.append(u)
    return urls


def _download_image(url: str, dest: Path) -> Path:
    try:
        proc = subprocess.run(
            [
                "curl.exe",
                "-L",
                "-sS",
                "--noproxy",
                "*",
                "-m",
                "90",
                "-A",
                "Mozilla/5.0",
                "-o",
                str(dest),
                url,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            return dest
    except Exception:
        pass

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=60, context=DEFAULT_SSL_CTX, attempts=4) as resp:
        dest.write_bytes(resp.read())
    return dest


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def _find_tk_row(match_key: str, region: str):
    init_db()
    conn = connect()
    row = None
    for r in conn.execute(
        """SELECT p.*, s.region, s.cipher FROM products p
           JOIN shops s ON s.cipher = p.shop_cipher
           WHERE p.status = 'ACTIVATE' AND p.seller_sku != ''"""
    ):
        if tk_region(r["cipher"]) != region.upper():
            continue
        if tk_match_key(r["seller_sku"]) != match_key:
            continue
        row = r
        break
    conn.close()
    if not row:
        raise RuntimeError(f"未找到 TK [{region}] 对齐码 {match_key}")
    return row


def _find_tk_for_global(match_key: str, fallback_region: str) -> tuple:
    """全球商品母版：优先 PH 英文，跳过非英文 TH/VN 标题。"""
    from modules.shopee.global_copy import is_english_listing_text

    order = list(TK_SOURCE_ORDER)
    fb = fallback_region.upper()
    if fb in order:
        order.remove(fb)
        order.append(fb)
    last_err = ""
    best: tuple | None = None
    for reg in order:
        try:
            row = _find_tk_row(match_key, reg)
            detail = _fetch_tk_detail(row)
            title = (detail.get("title") or "").strip()
            if is_english_listing_text(title):
                return row, detail, reg
            if best is None:
                best = (row, detail, reg)
        except RuntimeError as e:
            last_err = str(e)
            continue
    if best:
        return best
    raise RuntimeError(last_err or f"未找到 TK 对齐码 {match_key}")


def _fetch_tk_detail(row) -> dict:
    token = auth.ensure_valid_token()["access_token"]
    pid = row["product_id"]
    cipher = row["cipher"]
    resp = tk_get(f"/product/202309/products/{pid}", token, {"shop_cipher": cipher})
    if resp.get("code") != 0:
        raise RuntimeError(resp.get("message") or f"TK 详情失败 {pid}")
    return resp.get("data") or {}


def _reference_item(region: str, shop_id: int, token: str) -> dict | None:
    """从已有 Shopee 墙贴 SKU 复制类目/物流/属性模板。"""
    init_db()
    conn = connect()
    item_id = None
    for r in conn.execute(
        """SELECT item_id FROM shopee_products
           WHERE region = ? AND seller_sku GLOB '[0-9][0-9][0-9][0-9]'
           ORDER BY seller_sku LIMIT 1""",
        (region.upper(),),
    ):
        item_id = int(r["item_id"])
        break
    conn.close()
    if not item_id:
        return None
    resp = shop_get(
        "/api/v2/product/get_item_base_info",
        shop_id,
        token,
        {"item_id_list": str(item_id)},
    )
    items = (resp.get("response") or {}).get("item_list") or []
    return items[0] if items else None


def _logistic_info(shop_id: int, token: str, ref: dict | None) -> list[dict]:
    exclude_logistic = {50039}  # VN Viettel Smartbox 对部分 SKU 不支持 weight/size
    if ref and ref.get("logistic_info"):
        out = []
        for lg in ref["logistic_info"]:
            lid = lg.get("logistic_id")
            if lid is None or int(lid) in exclude_logistic:
                continue
            out.append(
                {
                    "logistic_id": lid,
                    "enabled": bool(lg.get("enabled", True)),
                    "shipping_fee": lg.get("shipping_fee", 0),
                    "size_id": lg.get("size_id", 0),
                    "is_free": bool(lg.get("is_free", False)),
                }
            )
        enabled = [x for x in out if x["enabled"]]
        if enabled:
            return enabled
    resp = shop_get("/api/v2/logistics/get_channel_list", shop_id, token)
    channels = (resp.get("response") or {}).get("logistics_channel_list") or []
    out = []
    for ch in channels:
        if not ch.get("enabled"):
            continue
        lid = ch.get("logistics_channel_id") or ch.get("logistic_id")
        if lid is None:
            continue
        out.append(
            {
                "logistic_id": lid,
                "enabled": True,
                "shipping_fee": 0,
                "size_id": 0,
                "is_free": False,
            }
        )
        if len(out) >= 2:
            break
    if not out:
        raise RuntimeError("无可用物流渠道")
    return out


def _attribute_list(shop_id: int, token: str, category_id: int, ref: dict | None) -> list[dict]:
    if ref and ref.get("attribute_list"):
        attrs = []
        for a in ref["attribute_list"]:
            vals = a.get("attribute_value_list") or []
            if not vals:
                continue
            attrs.append(
                {
                    "attribute_id": a["attribute_id"],
                    "attribute_value_list": [
                        {
                            k: v
                            for k, v in {
                                "value_id": v.get("value_id", 0),
                                "original_value_name": v.get("original_value_name"),
                                "value_unit": v.get("value_unit", ""),
                            }.items()
                            if v is not None and v != ""
                        }
                        for v in vals
                    ],
                }
            )
        if attrs:
            return attrs
    tree = shop_post(
        "/api/v2/product/get_attribute_tree",
        shop_id,
        token,
        {"category_id": category_id, "language": "th"},
    )
    attrs = []
    for grp in (tree.get("response") or {}).get("list") or []:
        for a in grp.get("attribute_list") or []:
            if not a.get("is_mandatory"):
                continue
            vals = a.get("attribute_value_list") or []
            pick = vals[0] if vals else {}
            attrs.append(
                {
                    "attribute_id": a["attribute_id"],
                    "attribute_value_list": [
                        {
                            "value_id": pick.get("value_id", 0),
                            "original_value_name": pick.get("name") or pick.get("value") or "-",
                        }
                    ],
                }
            )
    return attrs


def _upload_images(urls: list[str], *, max_images: int = 8) -> list[str]:
    ids: list[str] = []
    with tempfile.TemporaryDirectory(prefix="shopee_img_") as tmp:
        for i, url in enumerate(urls[:max_images]):
            if not url:
                continue
            try:
                path = Path(tmp) / f"img_{i}.jpg"
                _download_image(url, path)
                resp = upload_image(path, scene="normal" if i == 0 else "desc")
                info = resp.get("image_info") or {}
                img_id = info.get("image_id")
                if not img_id and resp.get("image_info_list"):
                    info = (resp["image_info_list"][0] or {}).get("image_info") or {}
                    img_id = info.get("image_id")
                if not img_id:
                    continue
                ids.append(img_id)
                time.sleep(0.3)
            except Exception:
                continue
    if not ids:
        raise RuntimeError("无可用主图")
    return ids


def build_payload(
    detail: dict,
    *,
    region: str,
    shop_id: int,
    token: str,
    model_sku: str,
    image_ids: list[str],
) -> dict:
    ref = _reference_item(region, shop_id, token)
    category_id = (ref or {}).get("category_id") or DEFAULT_CATEGORY.get(region.upper())
    if not category_id:
        rec = shop_post(
            "/api/v2/product/category_recommend",
            shop_id,
            token,
            {"item_name": (detail.get("title") or "")[:200]},
        )
        cats = (rec.get("response") or {}).get("category_id") or []
        if cats:
            category_id = cats[0]
    if not category_id:
        raise RuntimeError(f"未配置 {region} category_id")

    sku = (detail.get("skus") or [{}])[0]
    local_price = float((sku.get("price") or {}).get("sale_price") or 0)
    price = tk_local_to_cny(local_price, region=region)
    stock = sum(int(i.get("quantity") or 0) for i in (sku.get("inventory") or [])) or 50

    w = sku.get("sku_weight") or detail.get("package_weight") or {}
    weight = float(w.get("value") or 0.2)
    if (w.get("unit") or "").upper() == "GRAM":
        weight = weight / 1000.0
    dim = sku.get("sku_dimensions") or detail.get("package_dimensions") or {}
    length = int(float(dim.get("length") or 30))
    width = int(float(dim.get("width") or 20))
    height = int(float(dim.get("height") or 2))

    desc = _strip_html(detail.get("description") or "")
    if len(desc) < 60:
        desc = (detail.get("title") or "") + " " + desc
    desc = desc.strip()[:3000]
    if len(desc) < 60:
        desc = desc + " " * (60 - len(desc))

    brand = (ref or {}).get("brand") or {"brand_id": 0, "original_brand_name": "NoBrand"}

    payload = {
        "category_id": int(category_id),
        "item_name": _shopee_title(detail.get("title") or "", model_sku, max_len=180),
        "description": desc,
        "description_type": "normal",
        "item_sku": model_sku,
        "original_price": price,
        "normal_stock": stock,
        "weight": max(weight, 0.01),
        "dimension": {
            "package_length": max(length, 1),
            "package_width": max(width, 1),
            "package_height": max(height, 1),
        },
        "logistic_info": _logistic_info(shop_id, token, ref),
        "attribute_list": _attribute_list(shop_id, token, int(category_id), ref),
        "brand": brand,
        "condition": "NEW",
        "item_dangerous": 0,
        "pre_order": {"is_pre_order": False, "days_to_ship": 2},
        "item_status": "UNLIST",
        "image": {"image_id_list": image_ids[:9]},
        "seller_stock": [{"location_id": "CNZ", "stock": stock}],
    }
    return payload


def _shop_meta(shop_id: int, token: str) -> dict:
    info = get_shop_info(shop_id, token)
    return info.get("response") or info


def _global_attribute_list(merchant_id: int, token: str, category_id: int, ref: dict | None) -> list[dict]:
    if ref and ref.get("attribute_list"):
        attrs = []
        for a in ref["attribute_list"]:
            vals = a.get("attribute_value_list") or []
            if not vals:
                continue
            attrs.append(
                {
                    "attribute_id": a["attribute_id"],
                    "attribute_value_list": [
                        {
                            k: v
                            for k, v in {
                                "value_id": v.get("value_id", 0),
                                "original_value_name": v.get("original_value_name"),
                                "value_unit": v.get("value_unit", ""),
                            }.items()
                            if v is not None and v != ""
                        }
                        for v in vals
                    ],
                }
            )
        if attrs:
            return attrs
    tree = merchant_post(
        "/api/v2/global_product/get_attribute_tree",
        merchant_id,
        token,
        {"category_id": category_id, "language": "zh-hans"},
    )
    attrs = []
    for grp in (tree.get("response") or {}).get("list") or []:
        for a in grp.get("attribute_list") or []:
            if not a.get("is_mandatory"):
                continue
            vals = a.get("attribute_value_list") or []
            pick = vals[0] if vals else {}
            attrs.append(
                {
                    "attribute_id": a["attribute_id"],
                    "attribute_value_list": [
                        {
                            "value_id": pick.get("value_id", 0),
                            "original_value_name": pick.get("name") or pick.get("value") or "-",
                        }
                    ],
                }
            )
    return attrs


def _global_title(local_title: str, model_sku: str) -> str:
    """兼容旧调用；全球商品请用 build_global_copy。"""
    copy = build_global_copy({"title": local_title, "description": ""}, model_sku, source_region="")
    return copy["title"]


def _shopee_title(raw: str, model_sku: str, *, max_len: int = 120) -> str:
    title = (raw or "").strip()
    if len(title) > max_len:
        title = title[: max_len - 3].rstrip() + "..."
    if len(title) < 15:
        title = (title + f" SKU{model_sku}").strip()[:max_len]
    return title


def _english_safe_sku(raw: str) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if digits:
        return f"SKU{digits[-8:]}"
    token = "".join(ch for ch in str(raw or "").upper() if ch.isalnum())
    return f"SKU{token[:8] or '0000'}"


def _local_item_fields(
    detail: dict,
    *,
    shop_id: int,
    token: str,
    model_sku: str,
    ref: dict | None,
) -> tuple[str, str, float]:
    sku = (detail.get("skus") or [{}])[0]
    price = float((sku.get("price") or {}).get("sale_price") or 0)
    desc = _strip_html(detail.get("description") or "")
    if len(desc) < 60:
        desc = ((detail.get("title") or "") + " " + desc).strip()
    local_desc = desc[:3000]
    if len(local_desc) < 60:
        local_desc = local_desc + " " * (60 - len(local_desc))
    title = _shopee_title(detail.get("title") or "", model_sku, max_len=180)
    return title, local_desc, price


def _run_publish_task(
    *,
    global_item_id: int,
    detail: dict,
    region: str,
    shop_id: int,
    token: str,
    model_sku: str,
    ref: dict | None,
) -> dict:
    meta = _shop_meta(shop_id, token)
    merchant_id = int(meta.get("merchant_id") or 0)
    if not merchant_id:
        raise RuntimeError("店铺无 merchant_id，无法走 CNSC 全球商品流程")
    mtoken = _merchant_token(shop_id, token)

    title, local_desc, price = _local_item_fields(
        detail, shop_id=shop_id, token=token, model_sku=model_sku, ref=ref
    )
    pub_body = {
        "global_item_id": int(global_item_id),
        "shop_id": int(shop_id),
        "shop_region": region.upper(),
        "item": {
            "item_name": title,
            "description": local_desc,
            "item_status": "UNLIST",
            "original_price": price,
            "item_sku": _english_safe_sku(model_sku),
            "logistic": _logistic_info(shop_id, token, ref),
        },
    }
    p_resp = merchant_post(
        "/api/v2/global_product/create_publish_task",
        merchant_id,
        mtoken,
        pub_body,
    )
    if p_resp.get("error"):
        raise RuntimeError(p_resp.get("message") or p_resp.get("error") or p_resp)
    task_id = (p_resp.get("response") or {}).get("publish_task_id")
    item_id = None
    last_status = None
    for _ in range(20):
        time.sleep(3)
        st = merchant_get(
            "/api/v2/global_product/get_publish_task_result",
            merchant_id,
            mtoken,
            {"publish_task_id": int(task_id)},
        )
        res = st.get("response") or {}
        last_status = res.get("publish_status")
        if last_status == "success":
            success = res.get("success") or {}
            item_id = res.get("item_id") or success.get("item_id")
            break
        if last_status == "failed":
            failed = res.get("failed") or {}
            reason = failed.get("failed_reason") or st
            raise RuntimeError(f"发布失败: {reason}")
    return {
        "ok": bool(item_id),
        "global_item_id": global_item_id,
        "publish_task_id": task_id,
        "item_id": item_id,
        "publish_status": last_status,
        "raw_publish": p_resp,
    }


def _publish_existing_global(
    global_item_id: int,
    detail: dict,
    *,
    region: str,
    shop_id: int,
    token: str,
    model_sku: str,
    ref: dict | None,
) -> dict:
    result = _run_publish_task(
        global_item_id=global_item_id,
        detail=detail,
        region=region,
        shop_id=shop_id,
        token=token,
        model_sku=model_sku,
        ref=ref,
    )
    if result.get("item_id"):
        record_shop_item(
            str(global_item_id),
            region,
            shop_id=shop_id,
            item_id=result["item_id"],
        )
    return {**result, "flow": "publish_existing_global"}


def _create_global_item(
    detail: dict,
    *,
    region: str,
    shop_id: int,
    token: str,
    model_sku: str,
    image_ids: list[str],
    ref: dict | None,
    tk_source_region: str = "",
) -> dict:
    """仅创建 CNSC 全球商品，不发布到国家店（由卖家在后台手动发布）。"""
    meta = _shop_meta(shop_id, token)
    merchant_id = int(meta.get("merchant_id") or 0)
    if not merchant_id:
        raise RuntimeError("店铺无 merchant_id，无法走 CNSC 全球商品流程")
    mtoken = _merchant_token(shop_id, token)

    category_id = (ref or {}).get("category_id") or DEFAULT_CATEGORY.get(region.upper()) or 101157
    sku = (detail.get("skus") or [{}])[0]
    local_price = float((sku.get("price") or {}).get("sale_price") or 0)
    price = tk_local_to_cny(local_price, region=region)
    stock = sum(int(i.get("quantity") or 0) for i in (sku.get("inventory") or [])) or 50
    w = sku.get("sku_weight") or detail.get("package_weight") or {}
    weight = float(w.get("value") or 0.2)
    if (w.get("unit") or "").upper() == "GRAM":
        weight = weight / 1000.0
    dim = sku.get("sku_dimensions") or detail.get("package_dimensions") or {}
    length = int(float(dim.get("length") or 30))
    width = int(float(dim.get("width") or 20))
    height = int(float(dim.get("height") or 2))

    global_copy = build_global_copy(detail, model_sku, source_region=tk_source_region)
    global_title = global_copy["title"]
    global_desc = global_copy["description"]

    global_body = {
        "category_id": int(category_id),
        "global_item_name": global_title,
        "description": global_desc,
        "global_item_sku": _english_safe_sku(model_sku),
        "original_price": price,
        "weight": max(weight, 0.01),
        "dimension": {
            "package_length": max(length, 1),
            "package_width": max(width, 1),
            "package_height": max(height, 1),
        },
        "image": {"image_id_list": image_ids[:9]},
        "attribute_list": _global_attribute_list(merchant_id, mtoken, int(category_id), ref),
        "brand": (ref or {}).get("brand") or {"brand_id": 0, "original_brand_name": "NoBrand"},
        "condition": "NEW",
        "seller_stock": [{"location_id": "CNZ", "stock": stock}],
        "pre_order": {"days_to_ship": 2},
    }
    g_resp = merchant_post("/api/v2/global_product/add_global_item", merchant_id, mtoken, global_body)
    if g_resp.get("error"):
        raise RuntimeError(g_resp.get("message") or g_resp.get("error") or g_resp)
    global_item_id = (g_resp.get("response") or {}).get("global_item_id")
    if not global_item_id:
        raise RuntimeError(f"add_global_item 无 global_item_id: {g_resp}")
    return {
        "ok": True,
        "flow": "global_only",
        "global_item_id": global_item_id,
        "model_sku": model_sku,
        "global_title": global_title,
        "global_description_len": len(global_desc),
        "tk_source_region": tk_source_region,
        "used_ph_english": global_copy.get("used_ph_english"),
    }


def _publish_global(
    detail: dict,
    *,
    region: str,
    shop_id: int,
    token: str,
    model_sku: str,
    image_ids: list[str],
    ref: dict | None,
    tk_source_region: str = "",
) -> dict:
    created = _create_global_item(
        detail,
        region=region,
        shop_id=shop_id,
        token=token,
        model_sku=model_sku,
        image_ids=image_ids,
        ref=ref,
        tk_source_region=tk_source_region,
    )
    global_item_id = int(created["global_item_id"])
    result = _run_publish_task(
        global_item_id=global_item_id,
        detail=detail,
        region=region,
        shop_id=shop_id,
        token=token,
        model_sku=model_sku,
        ref=ref,
    )
    return {**created, **result, "flow": "global_product"}


def publish_match_key(
    match_key: str,
    region: str,
    *,
    dry_run: bool = False,
    global_only: bool = True,
    publish_shops: bool = False,
) -> dict:
    """将 TK 商品发布到 Shopee。默认仅建全球商品，不自动发国家店。"""
    if publish_shops:
        global_only = False
    key = parse_search_key(match_key)
    reg = region.upper()
    shop_map = sync_shop_ids()
    if reg not in shop_map:
        raise RuntimeError(f"无 Shopee 主店: {reg}")
    shop_id = int(shop_map[reg])

    row = _find_tk_row(key, reg)
    tk_row, tk_detail, tk_source = _find_tk_for_global(key, reg)
    detail = tk_detail

    urls = _collect_image_urls(detail)

    model_sku = key  # Shopee 4 位码
    token = ensure_shop_token(shop_id)

    existing_gid = global_item_id_for_match_key(key)
    global_preview = build_global_copy(detail, model_sku, source_region=tk_source)
    if dry_run:
        out = {
            "dry_run": True,
            "region": reg,
            "shop_id": shop_id,
            "match_key": key,
            "tk_seller_sku": tk_row["seller_sku"],
            "tk_source_region": tk_source,
            "title": detail.get("title"),
            "global_title": global_preview["title"],
            "global_description_preview": global_preview["description"][:400] + "...",
            "global_description_len": len(global_preview["description"]),
            "used_ph_english": global_preview.get("used_ph_english"),
            "image_urls": urls[:8],
            "model_sku": model_sku,
            "price": (detail.get("skus") or [{}])[0].get("price"),
        }
        if existing_gid:
            out["mode"] = "publish_existing_global" if not global_only else "existing_global"
            out["global_item_id"] = existing_gid
        elif global_only:
            out["mode"] = "global_only"
        return out

    ref = _reference_item(reg, shop_id, token)
    meta = _shop_meta(shop_id, token)
    if meta.get("is_cb") or meta.get("is_upgraded_cbsc"):
        if existing_gid and not global_only:
            result = _publish_existing_global(
                int(existing_gid),
                detail,
                region=reg,
                shop_id=shop_id,
                token=token,
                model_sku=model_sku,
                ref=ref,
            )
        elif existing_gid and global_only:
            return {
                "ok": True,
                "flow": "existing_global",
                "global_item_id": int(existing_gid),
                "region": reg,
                "shop_id": shop_id,
                "match_key": key,
                "model_sku": model_sku,
                "message": "全球商品已存在，请在 CNSC 后台手动发布到各站点",
            }
        else:
            image_ids = _upload_images(urls)
            if global_only:
                result = _create_global_item(
                    detail,
                    region=reg,
                    shop_id=shop_id,
                    token=token,
                    model_sku=model_sku,
                    image_ids=image_ids,
                    ref=ref,
                    tk_source_region=tk_source,
                )
                upsert_global_entry(
                    str(result["global_item_id"]),
                    match_key=key,
                    global_model_sku=model_sku,
                    title=result.get("global_title") or global_preview["title"],
                )
            else:
                result = _publish_global(
                    detail,
                    region=reg,
                    shop_id=shop_id,
                    token=token,
                    model_sku=model_sku,
                    image_ids=image_ids,
                    ref=ref,
                    tk_source_region=tk_source,
                )
        return {
            **result,
            "region": reg,
            "shop_id": shop_id,
            "match_key": key,
            "model_sku": model_sku,
        }

    image_ids = _upload_images(urls)
    payload = build_payload(
        detail,
        region=reg,
        shop_id=shop_id,
        token=token,
        model_sku=model_sku,
        image_ids=image_ids,
    )
    resp = shop_post("/api/v2/product/add_item", shop_id, token, payload)
    if resp.get("error"):
        raise RuntimeError(resp.get("message") or resp.get("error") or resp)
    item_id = (resp.get("response") or {}).get("item_id")
    return {
        "ok": True,
        "region": reg,
        "shop_id": shop_id,
        "match_key": key,
        "model_sku": model_sku,
        "item_id": item_id,
        "item_status": payload.get("item_status"),
        "raw": resp,
    }


def update_global_match_key(match_key: str, region: str = "PH") -> dict:
    """更新已有 CNSC 全球商品英文名/描述（优先 PH TK + DeepSeek）。"""
    from modules.shopee.global_sku_map import load_map, save_map

    key = parse_search_key(match_key)
    gid = global_item_id_for_match_key(key)
    if not gid:
        raise RuntimeError(f"未找到 {key} 的全球商品映射，请先 publish 或写入 shopee_global_sku_map.json")

    reg = region.upper()
    shop_id = int(sync_shop_ids()[reg])
    token = ensure_shop_token(shop_id)
    meta = _shop_meta(shop_id, token)
    merchant_id = int(meta.get("merchant_id") or 0)
    if not merchant_id:
        raise RuntimeError("无 merchant_id")
    mtoken = _merchant_token(shop_id, token)

    _, detail, tk_source = _find_tk_for_global(key, reg)
    copy = build_global_copy(detail, key, source_region=tk_source)
    body = {
        "global_item_id": int(gid),
        "global_item_name": copy["title"],
        "description": copy["description"],
    }
    resp = merchant_post("/api/v2/global_product/update_global_item", merchant_id, mtoken, body)
    if resp.get("error"):
        raise RuntimeError(resp.get("message") or resp.get("error") or resp)

    data = load_map()
    entry = data.get(str(gid))
    if isinstance(entry, dict):
        entry["title"] = copy["title"]
        data[str(gid)] = entry
        save_map(data)

    return {
        "ok": True,
        "global_item_id": int(gid),
        "match_key": key,
        "tk_source_region": tk_source,
        "global_title": copy["title"],
        "global_description_len": len(copy["description"]),
        "used_ph_english": copy.get("used_ph_english"),
        "raw": resp,
    }
