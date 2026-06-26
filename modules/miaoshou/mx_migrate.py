"""TikTok PH 母版 → 墨西哥店：定价、商家 SKU（后四位）、包裹尺寸。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from core import auth, shops
from core.config import get
from modules.catalog.sku_key import tk_match_key
from modules.products.sync import _fetch_product_detail

MX_SHOP_ID = 16265910
GET_SHOP_PATH = "/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info"
CLAIM_PATH = "/open/v1/product/collect_box/tiktok/collect_box/claim_to_shop"

COMMON_DETAIL_PATH = (
    "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail"
)
SITE_INFO_PATH = "/open/v1/product/collect_box/tiktok/collect_box/get_site_collect_item_info"
TK_LIST_PATH = "/open/v1/product/collect_box/tiktok/collect_box/search_collect_box_detail_list"

ALLOWED_IMAGE_HOSTS = ("ibyteimg.com", "tiktokcdn.com", "tiktok.com")
BLOCKED_IMAGE_HOSTS = ("kwcdn.com", "tosoiot.com")


def mx_item_num(seller_sku: str) -> str:
    """MX 商家货号：只保留数字后四位（770002 → 0002）。"""
    return tk_match_key(seller_sku)


def is_ok_image(url: str) -> bool:
    u = (url or "").lower()
    if not u.startswith("http"):
        return False
    if any(b in u for b in BLOCKED_IMAGE_HOSTS):
        return False
    if "algo_check" in u:
        return False
    if any(h in u for h in ALLOWED_IMAGE_HOSTS):
        return True
    ext = u.split("?", 1)[0]
    return ext.endswith((".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp"))


def _positive_float(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _dims_from_mapping(data: dict | None) -> tuple[float | None, float | None, float | None]:
    if not isinstance(data, dict):
        return None, None, None
    length = _positive_float(data.get("packageLength") or data.get("length"))
    width = _positive_float(data.get("packageWidth") or data.get("width"))
    height = _positive_float(data.get("packageHeight") or data.get("height"))
    if length and width and height:
        return length, width, height
    for sku in (data.get("skuMap") or {}).values():
        if not isinstance(sku, dict):
            continue
        sl = _positive_float(sku.get("packageLength"))
        sw = _positive_float(sku.get("packageWidth"))
        sh = _positive_float(sku.get("packageHeight"))
        if sl and sw and sh:
            return sl, sw, sh
    return None, None, None


def _dims_from_tiktok_product(product: dict | None) -> tuple[float | None, float | None, float | None]:
    if not isinstance(product, dict):
        return None, None, None
    dim = product.get("package_dimensions") or {}
    length = _positive_float(dim.get("length"))
    width = _positive_float(dim.get("width"))
    height = _positive_float(dim.get("height"))
    if length and width and height:
        return length, width, height
    for sku in product.get("skus") or []:
        if not isinstance(sku, dict):
            continue
        sdim = sku.get("sku_dimensions") or {}
        sl = _positive_float(sdim.get("length"))
        sw = _positive_float(sdim.get("width"))
        sh = _positive_float(sdim.get("height"))
        if sl and sw and sh:
            return sl, sw, sh
    return None, None, None


def resolve_package_dims_cm(
    *,
    common_detail: dict | None = None,
    site_collect_info: dict | None = None,
    tiktok_product: dict | None = None,
) -> tuple[int, int, int]:
    """包裹尺寸(cm)：公共采集箱 → TK 站点采集 → PH TikTok 母版（不读 MX 店铺视图，避免被旧 save 污染）。"""
    for source in (common_detail, site_collect_info):
        length, width, height = _dims_from_mapping(source)
        if length and width and height:
            return int(round(length)), int(round(width)), int(round(height))
    length, width, height = _dims_from_tiktok_product(tiktok_product)
    if length and width and height:
        return int(round(length)), int(round(width)), int(round(height))
    raise RuntimeError("未找到有效包裹尺寸（采集箱与 TikTok 母版均无 packageLength/Width/Height）")


def fetch_common_collect_detail(common_collect_box_detail_id: int) -> dict | None:
    from modules.miaoshou.client import post_open

    resp = post_open(
        COMMON_DETAIL_PATH,
        {"commonCollectBoxDetailId": int(common_collect_box_detail_id)},
    )
    if resp.get("result") != "success":
        return None
    detail = (resp.get("data") or {}).get("editCommonCollectBoxDetail") or {}
    return detail if isinstance(detail, dict) and detail else None


def fetch_site_collect_info(detail_id: int, site: str = "MY") -> dict | None:
    from modules.miaoshou.client import post_open

    resp = post_open(SITE_INFO_PATH, {"detailId": int(detail_id), "site": site.upper()})
    if resp.get("result") != "success":
        return None
    info = (resp.get("data") or {}).get("siteCollectItemInfo") or {}
    return info if isinstance(info, dict) and info else None


def find_common_collect_box_detail_id(collect_box_detail_id: int) -> int | None:
    from modules.miaoshou.client import post_open

    target = str(collect_box_detail_id)
    for status in ("notPublished", "published", "inReview", "draft", "fail"):
        for page in range(1, 30):
            resp = post_open(
                TK_LIST_PATH,
                {
                    "pageNo": page,
                    "pageSize": 50,
                    "filter": {"status": status, "sourceItemIdKeyword": ""},
                },
            )
            items = (resp.get("data") or {}).get("detailList") or []
            for it in items:
                if str(it.get("collectBoxDetailId") or "") == target:
                    raw = it.get("commonCollectBoxDetailId")
                    if raw is not None and str(raw).strip():
                        return int(raw)
            if len(items) < 50:
                break
    return None


def collect_package_context(
    *,
    collect_box_detail_id: int,
    tiktok_product: dict | None = None,
    site: str = "MY",
) -> dict:
    """汇总尺寸来源，供 save 前写入 shopCollectItemInfo。优先 TikTok 原链接 package_dimensions。"""
    common_id = find_common_collect_box_detail_id(collect_box_detail_id)
    common_detail = fetch_common_collect_detail(common_id) if common_id else None
    site_info = fetch_site_collect_info(collect_box_detail_id, site=site)

    package_source = "PH TikTok 母版"
    length, width, height = _dims_from_tiktok_product(tiktok_product)
    if length and width and height:
        package_source = "TikTok 原链接 listing"
    else:
        length = width = height = None
        for label, source in (
            ("公共采集箱", common_detail),
            ("TK 站点采集", site_info),
        ):
            length, width, height = _dims_from_mapping(source)
            if length and width and height:
                package_source = label
                break
        if not (length and width and height):
            raise RuntimeError("未找到有效包裹尺寸（原链接与采集箱均无尺寸）")

    return {
        "common_collect_box_detail_id": common_id,
        "package_length": int(round(length)),
        "package_width": int(round(width)),
        "package_height": int(round(height)),
        "package_source": package_source,
    }


def _norm_variant_label(label: str) -> str:
    return re.sub(r"\s+", " ", (label or "").strip().lower())


def _sku_map_attr_value_id(sku_map_key: str) -> str | None:
    parts = [p for p in (sku_map_key or "").split(";") if p]
    return parts[0] if parts else None


def _attr_value_index(site_collect_info: dict | None) -> dict[str, str]:
    """attrValueId → 规格文案（如 140*140 cm）。"""
    out: dict[str, str] = {}
    for prop in (site_collect_info or {}).get("skuPropertyList") or []:
        for val in prop.get("attrValueList") or []:
            vid = str(val.get("attrValueId") or "").strip()
            label = str(val.get("attrValue") or "").strip()
            if vid and label:
                out[vid] = label
    return out


def _ph_label_to_match_key(tiktok_product: dict | None) -> dict[str, str]:
    """规格文案 → 对齐码（PH 母版 seller_sku 后四位）。"""
    out: dict[str, str] = {}
    for sku in (tiktok_product or {}).get("skus") or []:
        if not isinstance(sku, dict):
            continue
        mk = tk_match_key(sku.get("seller_sku") or "")
        if not mk:
            continue
        attrs = sku.get("sales_attributes") or []
        label = str((attrs[0] if attrs else {}).get("value_name") or "").strip()
        if label:
            out[_norm_variant_label(label)] = mk
    return out


def index_sku_map_keys_by_match_key(
    *,
    site_collect_info: dict | None,
    tiktok_product: dict | None,
) -> dict[str, str]:
    """对齐码 → 妙手 skuMap key（`;attrValueId;`）。"""
    attr_labels = _attr_value_index(site_collect_info)
    label_to_mk = _ph_label_to_match_key(tiktok_product)
    out: dict[str, str] = {}
    for sku_map_key in (site_collect_info or {}).get("skuMap") or {}:
        vid = _sku_map_attr_value_id(sku_map_key)
        if not vid:
            continue
        label = attr_labels.get(vid)
        if not label:
            continue
        mk = label_to_mk.get(_norm_variant_label(label))
        if mk:
            out[mk] = sku_map_key
    return out


@dataclass
class MxSkuVariantWrite:
    match_key: str
    seller_sku: str
    mxn_list_price: int
    weight_kg: float | None = None
    variant_label: str = ""


def _set_sku_stock(sku: dict, stock: int, *, mx_shop_id: int = MX_SHOP_ID) -> None:
    sku["stock"] = int(stock)
    wh_root = sku.get("shopIdToWarehouseIdAndStockMap")
    if not isinstance(wh_root, dict) or not wh_root:
        return
    shop_key = str(mx_shop_id)
    bucket = wh_root.get(shop_key) or wh_root.get(mx_shop_id)
    if not isinstance(bucket, dict):
        return
    for wh_id in bucket:
        bucket[wh_id] = str(int(stock))


def apply_mx_shop_collect_info(
    info: dict,
    *,
    seller_sku: str,
    mxn_list_price: float | int,
    mxn_sale_price: float | None = None,
    package_length: int,
    package_width: int,
    package_height: int,
    good_image_urls: list[str] | None = None,
    stock: int | None = None,
    weight_kg: float | None = None,
    mx_shop_id: int = MX_SHOP_ID,
) -> dict:
    """写入 MX 店 save 所需字段（货号后四位 + 采集尺寸 + 原价/库存）。

    妙手 skuMap 只写折前原价 ceil：price = priceIncludeVat = mxn_list_price。
    店铺折扣由用户在 TikTok 后台自行设置；mxn_sale_price 仅用于 POP 测算与确认卡片。
    """
    item_num = mx_item_num(seller_sku)
    list_px = int(math.ceil(float(mxn_list_price)))
    if good_image_urls:
        info["imgUrls"] = good_image_urls

    info["packageLength"] = package_length
    info["packageWidth"] = package_width
    info["packageHeight"] = package_height
    if weight_kg and weight_kg > 0:
        info["weight"] = round(float(weight_kg), 3)

    for sku in (info.get("skuMap") or {}).values():
        sku["price"] = list_px
        sku["priceIncludeVat"] = list_px
        sku["itemNum"] = item_num
        sku["packageLength"] = package_length
        sku["packageWidth"] = package_width
        sku["packageHeight"] = package_height
        if weight_kg and weight_kg > 0:
            sku["weight"] = round(float(weight_kg), 3)
        if stock is not None:
            _set_sku_stock(sku, stock, mx_shop_id=mx_shop_id)

    title = info.get("title") or ""
    if len(title) > 255:
        info["title"] = title[:255].rstrip()
    info["sizeChart"] = ""
    info["sizeChartType"] = ""
    video_url = info.get("mainImgVideoUrl") or ""
    if isinstance(video_url, str) and len(video_url) > 255:
        info["mainImgVideoUrl"] = ""
    info["deliveryOptionSetType"] = info.get("deliveryOptionSetType") or "default"
    return info


def apply_mx_multi_shop_collect_info(
    info: dict,
    *,
    variants: list[MxSkuVariantWrite],
    package_length: int,
    package_width: int,
    package_height: int,
    sku_map_key_by_match_key: dict[str, str],
    good_image_urls: list[str] | None = None,
    stock: int | None = None,
    mx_shop_id: int = MX_SHOP_ID,
) -> dict:
    """同链接多规格：按 skuMap key 分别写入货号与折前原价 ceil。"""
    if good_image_urls:
        info["imgUrls"] = good_image_urls

    info["packageLength"] = package_length
    info["packageWidth"] = package_width
    info["packageHeight"] = package_height

    sku_map = info.get("skuMap") or {}
    weights = [v.weight_kg for v in variants if v.weight_kg and v.weight_kg > 0]
    if weights:
        info["weight"] = round(max(weights), 3)

    for variant in variants:
        sku_key = sku_map_key_by_match_key.get(variant.match_key)
        if not sku_key:
            raise RuntimeError(f"skuMap 未匹配对齐码 {variant.match_key}")
        sku = sku_map.get(sku_key)
        if not isinstance(sku, dict):
            raise RuntimeError(f"skuMap 缺少 key {sku_key!r}（{variant.match_key}）")
        list_px = int(variant.mxn_list_price)
        sku["price"] = list_px
        sku["priceIncludeVat"] = list_px
        sku["itemNum"] = mx_item_num(variant.seller_sku)
        sku["packageLength"] = package_length
        sku["packageWidth"] = package_width
        sku["packageHeight"] = package_height
        if variant.weight_kg and variant.weight_kg > 0:
            sku["weight"] = round(float(variant.weight_kg), 3)
        if stock is not None:
            _set_sku_stock(sku, stock, mx_shop_id=mx_shop_id)

    title = info.get("title") or ""
    if len(title) > 255:
        info["title"] = title[:255].rstrip()
    info["sizeChart"] = ""
    info["sizeChartType"] = ""
    video_url = info.get("mainImgVideoUrl") or ""
    if isinstance(video_url, str) and len(video_url) > 255:
        info["mainImgVideoUrl"] = ""
    info["deliveryOptionSetType"] = info.get("deliveryOptionSetType") or "default"
    return info


def php_to_mxn(php_price: float) -> float:
    mxn_cny = 0.36
    php_cny = float((get("exchange_rates") or {}).get("PHP") or 0.118)
    return round(php_price * 1.2 * php_cny / mxn_cny, 2)


def fetch_tiktok_product(product_id: str, *, region: str = "PH") -> dict:
    token = auth.access_token()
    reg = (region or "PH").upper()
    shop = next(
        (s for s in shops.list_shops(token) if (s.get("region") or "").upper() == reg),
        None,
    )
    if not shop:
        raise RuntimeError(f"未找到 {reg} 店铺授权")
    cipher = shop.get("cipher") or shop.get("shop_cipher")
    return _fetch_product_detail(token, cipher, product_id)


def fetch_ph_product(ph_product_id: str) -> dict:
    return fetch_tiktok_product(ph_product_id, region="PH")


def collect_master_images_and_product(
    product_id: str,
    *,
    region: str = "PH",
) -> tuple[list[str], dict]:
    product = fetch_tiktok_product(product_id, region=region)
    urls: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        if not is_ok_image(u):
            return
        key = u.split("?", 1)[0]
        if key in seen:
            return
        seen.add(key)
        urls.append(u)

    for img in product.get("main_images") or []:
        for u in (img.get("urls") or img.get("thumb_urls") or []):
            add(u)
    for sku in product.get("skus") or []:
        for attr in sku.get("sales_attributes") or []:
            img = attr.get("sku_img")
            if img:
                for u in (img.get("urls") or img.get("thumb_urls") or []):
                    add(u)
    if not urls:
        raise RuntimeError(f"{region} TikTok 商品未取到可用主图")
    return urls[:9], product


def collect_ph_images_and_product(ph_product_id: str) -> tuple[list[str], dict]:
    return collect_master_images_and_product(ph_product_id, region="PH")


CLAIM_COMMON_PATH = (
    "/open/v1/product/common_collect_box/common_collect_box/claimed"
)


def claim_common_to_tiktok(common_collect_box_detail_ids: list[int]) -> dict[int, int]:
    """公共采集箱 → TikTok 平台采集箱，返回 common_id → tk collectBoxDetailId。"""
    from modules.miaoshou.client import post_open

    ids = [int(x) for x in common_collect_box_detail_ids if int(x) > 0]
    if not ids:
        return {}
    body = {
        "detailSerialNumberPlatformList": [
            {"detailId": cid, "platform": "tiktok", "serialNumber": 1} for cid in ids
        ]
    }
    resp = post_open(CLAIM_COMMON_PATH, body)
    if resp.get("result") != "success":
        raise RuntimeError(f"公共箱认领到 TikTok 失败: {resp.get('message') or resp}")
    platform_map = (resp.get("data") or {}).get("platformCollectBoxDetailIdMap") or {}
    tk_map = platform_map.get("tiktok") or {}
    out: dict[int, int] = {}
    for cid in ids:
        raw = tk_map.get(str(cid)) or tk_map.get(cid)
        if raw is not None and str(raw).strip():
            out[cid] = int(raw)
    if len(out) < len(ids):
        missing = [i for i in ids if i not in out]
        raise RuntimeError(f"认领后缺少 TikTok detailId: {missing} · {resp}")
    return out


def ensure_mx_claimed(collect_box_detail_id: int, *, mx_shop_id: int = MX_SHOP_ID) -> None:
    from modules.miaoshou.client import post_open

    rd = post_open(GET_SHOP_PATH, {"detailId": collect_box_detail_id, "shopId": mx_shop_id})
    claim_ids = [int(x) for x in ((rd.get("data") or {}).get("claimToShopIds") or [])]
    if mx_shop_id in claim_ids:
        return
    cr = post_open(
        CLAIM_PATH,
        {"detailIds": [collect_box_detail_id], "shopIds": [mx_shop_id]},
    )
    if cr.get("result") != "success":
        raise RuntimeError(f"认领到 MX 失败: {cr.get('message') or cr}")


def clean_notes(notes: str, good_urls: list[str]) -> str:
    imgs = "".join(f'<img src="{u}">' for u in good_urls[:6])
    text = re.sub(r"<[^>]+>", " ", notes or "")
    text = re.sub(r"\s+", " ", text).strip()[:500]
    return f"<div><p>{text}</p>{imgs}</div>" if text else f"<div>{imgs}</div>"
