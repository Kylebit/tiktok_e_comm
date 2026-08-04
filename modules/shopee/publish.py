"""TikTok → Shopee 铺货（首版：单 SKU、无变体）。"""

from __future__ import annotations

import hashlib
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
from modules.shopee.global_sku_map import (
    global_item_id_for_match_key,
    record_shop_item,
    replace_deleted_global_entry,
    upsert_global_entry,
)
from modules.shopee.pricing import REGION_CURRENCY, tk_local_to_cny
from modules.shopee.shops import sync_shop_ids

# TH 墙贴类目（category_recommend + 同类商品实测）
DEFAULT_CATEGORY = {
    "TH": 101157,
}


class ShopeeGlobalMasterReconciliationError(RuntimeError):
    """A global-master update may have written and requires official readback."""

    def __init__(
        self,
        message: str,
        *,
        global_item_id: int,
        write_class: str = "shopee:global_master:update",
        reason: str = "global_master_update_requires_reconciliation",
        write_confirmed: bool = True,
    ) -> None:
        super().__init__(message)
        writes = [str(write_class)] if write_confirmed else []
        possible_writes = [] if write_confirmed else [str(write_class)]
        self.external_reference = str(global_item_id)
        self.external_write_evidence = {
            "source": "official_shopee_partner_api",
            "verified": False,
            "durable_state_uncertain": True,
            "submission_accepted": bool(write_confirmed),
            "reason": str(reason),
            "global_item_identity_digest": hashlib.sha256(
                str(global_item_id).encode("utf-8")
            ).hexdigest(),
            "external_writes_performed": writes,
            "possible_external_writes_performed": possible_writes,
        }


class ShopeeRegionalPublishReconciliationError(RuntimeError):
    """A regional publish was invoked and its durable outcome is uncertain."""

    def __init__(
        self,
        message: str,
        *,
        global_item_id: int,
        external_writes_performed: list[str] | tuple[str, ...] = (),
        possible_external_writes_performed: (
            list[str] | tuple[str, ...]
        ) = (),
        submission_accepted: bool = False,
        reason: str = "regional_publish_requires_reconciliation",
    ) -> None:
        super().__init__(message)
        writes = list(dict.fromkeys(str(value) for value in external_writes_performed))
        possible_writes = list(
            dict.fromkeys(
                str(value)
                for value in possible_external_writes_performed
                if str(value) not in writes
            )
        )
        self.external_reference = str(global_item_id)
        self.external_write_evidence = {
            "source": "official_shopee_partner_api",
            "verified": False,
            "durable_state_uncertain": True,
            "submission_accepted": bool(submission_accepted),
            "reason": str(reason),
            "global_item_identity_digest": hashlib.sha256(
                str(global_item_id).encode("utf-8")
            ).hexdigest(),
            "external_writes_performed": writes,
            "possible_external_writes_performed": possible_writes,
        }


def _merge_shopee_publish_write_evidence(
    error: Exception,
    *,
    global_item_id: int,
    global_master_receipt: dict | None,
) -> ShopeeRegionalPublishReconciliationError:
    """Preserve every known write when a later publish stage fails."""

    prior_writes = []
    if isinstance(global_master_receipt, dict):
        prior_writes.extend(
            str(value)
            for value in global_master_receipt.get(
                "external_writes_performed"
            )
            or ()
        )
    error_evidence = getattr(error, "external_write_evidence", None)
    later_writes = (
        list(error_evidence.get("external_writes_performed") or ())
        if isinstance(error_evidence, dict)
        else []
    )
    possible_writes = (
        list(
            error_evidence.get(
                "possible_external_writes_performed"
            )
            or ()
        )
        if isinstance(error_evidence, dict)
        else []
    )
    writes = list(dict.fromkeys([*prior_writes, *later_writes]))
    return ShopeeRegionalPublishReconciliationError(
        "Shopee publish sequence requires reconciliation",
        global_item_id=global_item_id,
        external_writes_performed=writes,
        possible_external_writes_performed=possible_writes,
        submission_accepted=bool(
            isinstance(error_evidence, dict)
            and error_evidence.get("submission_accepted") is True
        ),
        reason=(
            str(error_evidence.get("reason") or "")
            if isinstance(error_evidence, dict)
            else "post_global_master_publish_failure"
        )
        or "post_global_master_publish_failure",
    )


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
    """全球商品母版：优先 PH 英文（TK_SOURCE_ORDER），跳过非英文 TH/VN/MY 标题。"""
    from modules.shopee.global_copy import is_english_listing_text

    # 保持 PH→MY→TH→VN；不要把 --region 挪到队尾（否则会先吃到马来文）
    order = list(TK_SOURCE_ORDER)
    fb = (fallback_region or "").upper()
    if fb and fb not in order:
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


_UNSUPPORTED_PARCEL_FACT_LOGISTICS = {
    # Observed official VN create_publish_task rejection: this checkout
    # channel cannot consume global-item weight/dimension facts.
    ("VN", 50052): "channel does not accept global-item parcel facts",
}


def _channel_supports_parcel(
    channel: dict,
    *,
    region: str,
    weight_kg: float,
    dimensions_cm: tuple[float, float, float],
) -> bool:
    logistic_id = channel.get("logistics_channel_id") or channel.get("logistic_id")
    if logistic_id is None:
        return False
    if (region.upper(), int(logistic_id)) in _UNSUPPORTED_PARCEL_FACT_LOGISTICS:
        return False
    limits = channel.get("weight_limit") or {}
    minimum = float(limits.get("item_min_weight") or 0)
    maximum = float(limits.get("item_max_weight") or 0)
    if minimum and weight_kg < minimum:
        return False
    if maximum and weight_kg > maximum:
        return False
    dimension = channel.get("item_max_dimension") or {}
    unit = str(dimension.get("unit") or "CM").upper()
    multiplier = 0.1 if unit == "MM" else 1.0
    actual_dimensions = sorted(
        (float(value) for value in dimensions_cm),
        reverse=True,
    )
    dimension_limits = sorted(
        (
            float(dimension.get("length") or 0) * multiplier,
            float(dimension.get("width") or 0) * multiplier,
            float(dimension.get("height") or 0) * multiplier,
        ),
        reverse=True,
    )
    if any(
        limit and actual > limit
        for actual, limit in zip(actual_dimensions, dimension_limits)
    ):
        return False
    dimension_sum = float(dimension.get("dimension_sum") or 0) * multiplier
    return not dimension_sum or sum(dimensions_cm) <= dimension_sum


def _logistic_info(
    shop_id: int,
    token: str,
    ref: dict | None,
    *,
    region: str,
    weight_kg: float,
    dimensions_cm: tuple[float, float, float],
) -> list[dict]:
    """Enable every shop channel proven compatible with the parcel facts."""

    resp = shop_get("/api/v2/logistics/get_channel_list", shop_id, token)
    channels = (resp.get("response") or {}).get("logistics_channel_list") or []
    reference_rows = {
        int(row["logistic_id"]): row
        for row in ((ref or {}).get("logistic_info") or ())
        if row.get("logistic_id") is not None
    }
    seen: set[int] = set()
    out = []
    for ch in channels:
        if not ch.get("enabled"):
            continue
        lid = ch.get("logistics_channel_id") or ch.get("logistic_id")
        if (
            lid is None
            or int(lid) in seen
            or not _channel_supports_parcel(
                ch,
                region=region,
                weight_kg=weight_kg,
                dimensions_cm=dimensions_cm,
            )
        ):
            continue
        seen.add(int(lid))
        reference = reference_rows.get(int(lid)) or {}
        out.append(
            {
                "logistic_id": int(lid),
                "enabled": True,
                "shipping_fee": reference.get("shipping_fee", 0),
                "size_id": reference.get("size_id", 0),
                "is_free": bool(reference.get("is_free", False)),
            }
        )
    if not out:
        raise RuntimeError("无可用物流渠道")
    return out


def _item_base_info(shop_id: int, token: str, item_id: int | str) -> dict:
    response = shop_get(
        "/api/v2/product/get_item_base_info",
        shop_id,
        token,
        {"item_id_list": str(item_id)},
    )
    items = (response.get("response") or {}).get("item_list") or []
    return dict(items[0]) if len(items) == 1 else {}


def enable_all_applicable_logistics(
    shop_id: int,
    token: str,
    item_id: int | str,
) -> dict:
    """Enable every item channel accepted by Shopee for the current parcel facts.

    Disabled rows are tried one at a time.  A channel rejected by Shopee for
    weight, dimensions, route or shop eligibility is preserved as an audited
    rejection instead of failing the whole listing update.
    """

    item = _item_base_info(shop_id, token, item_id)
    current = item.get("logistic_info") or []
    if not current:
        raise RuntimeError(f"Shopee item {item_id} has no applicable logistics")

    def payload_rows(rows: list[dict], enable_id: int) -> list[dict]:
        return [
            {
                "logistic_id": int(row["logistic_id"]),
                "enabled": bool(row.get("enabled"))
                or int(row["logistic_id"]) == enable_id,
                "shipping_fee": row.get("shipping_fee", 0),
                "size_id": row.get("size_id", 0),
                "is_free": bool(row.get("is_free", False)),
            }
            for row in rows
            if row.get("logistic_id") is not None
        ]

    enabled_now = {
        int(row["logistic_id"])
        for row in current
        if row.get("logistic_id") is not None and row.get("enabled")
    }
    newly_enabled: list[int] = []
    rejected: list[dict] = []
    for candidate in current:
        logistic_id = candidate.get("logistic_id")
        if logistic_id is None or int(logistic_id) in enabled_now:
            continue
        response = shop_post(
            "/api/v2/product/update_item",
            shop_id,
            token,
            {
                "item_id": int(item_id),
                "logistic_info": payload_rows(current, int(logistic_id)),
            },
        )
        error = str(response.get("error") or "").strip()
        if error and error != "-":
            rejected.append(
                {
                    "logistic_id": int(logistic_id),
                    "logistic_name": candidate.get("logistic_name"),
                    "reason": str(response.get("message") or error),
                }
            )
            continue
        verified_candidate = _item_base_info(shop_id, token, item_id)
        current = list(verified_candidate.get("logistic_info") or current)
        if any(
            int(row.get("logistic_id") or -1) == int(logistic_id)
            and row.get("enabled")
            for row in current
        ):
            enabled_now.add(int(logistic_id))
            newly_enabled.append(int(logistic_id))
        else:
            rejected.append(
                {
                    "logistic_id": int(logistic_id),
                    "logistic_name": candidate.get("logistic_name"),
                    "reason": "Shopee accepted the request but kept the channel disabled",
                }
            )

    verified = _item_base_info(shop_id, token, item_id)
    rows = verified.get("logistic_info") or []
    disabled = [
        {
            "logistic_id": row.get("logistic_id"),
            "logistic_name": row.get("logistic_name"),
        }
        for row in rows
        if not row.get("enabled")
    ]
    return {
        "source": "official_shopee_partner_api",
        "item_id": str(item_id),
        "enabled_logistic_ids": sorted(
            int(row.get("logistic_id"))
            for row in rows
            if row.get("logistic_id") is not None and row.get("enabled")
        ),
        "newly_enabled_logistic_ids": sorted(newly_enabled),
        "disabled_logistics": disabled,
        "rejected_logistics": rejected,
        "verified": True,
    }


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
    item_status: str = "UNLIST",
) -> dict:
    clean_status = str(item_status or "").strip().upper()
    if clean_status not in {"UNLIST", "NORMAL"}:
        raise ValueError("Shopee item_status must be UNLIST or NORMAL")
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
    if len(desc) < 100:
        desc = (detail.get("title") or "") + " " + desc
    desc = desc.strip()[:3000]
    if len(desc) < 100:
        desc = (
            f"{desc} Please review the product images and specifications "
            "before purchase."
        ).strip()[:3000]

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
        "logistic_info": _logistic_info(
            shop_id,
            token,
            ref,
            region=region,
            weight_kg=max(weight, 0.01),
            dimensions_cm=(
                max(float(length), 1),
                max(float(width), 1),
                max(float(height), 1),
            ),
        ),
        "attribute_list": _attribute_list(shop_id, token, int(category_id), ref),
        "brand": brand,
        "condition": "NEW",
        "item_dangerous": 0,
        "pre_order": {"is_pre_order": False, "days_to_ship": 2},
        "item_status": clean_status,
        "image": {"image_id_list": image_ids[:9]},
        "seller_stock": [{"location_id": "CNZ", "stock": stock}],
    }
    return payload


def _shop_meta(shop_id: int, token: str) -> dict:
    info = get_shop_info(shop_id, token)
    return info.get("response") or info


def _global_attribute_list(
    merchant_id: int,
    token: str,
    category_id: int,
    ref: dict | None,
    *,
    detail: dict | None = None,
) -> list[dict]:
    """Copy only attributes supported by the current product facts.

    Reference products remain useful for category IDs and accepted attribute
    IDs, but their values must not leak into a different product.  In
    particular, an older floral/waterproof wall sticker must not make a dog
    decal floral or waterproof.
    """

    source = " ".join(
        (
            str((detail or {}).get("title") or ""),
            _strip_html(str((detail or {}).get("description") or "")),
        )
    ).lower()
    material = "PVC" if "pvc" in source else ""
    pattern = (
        "Dog"
        if "dog" in source
        else "Floral and Butterfly"
        if any(token in source for token in ("floral", "flower", "butterfly"))
        else ""
    )
    if ref and ref.get("attribute_list"):
        attrs = []
        for a in ref["attribute_list"]:
            name = str(a.get("original_attribute_name") or "").strip()
            normalised_name = name.casefold()
            vals = a.get("attribute_value_list") or []
            if not vals:
                continue
            if normalised_name == "material" and material:
                vals = [{"value_id": 0, "original_value_name": material}]
            elif normalised_name == "pattern" and pattern:
                vals = [{"value_id": 0, "original_value_name": pattern}]
            elif normalised_name == "seasonal decoration":
                if not any(
                    str(value.get("original_value_name") or "").casefold() == "no"
                    for value in vals
                ):
                    continue
            elif normalised_name == "quantity per pack":
                if not any(
                    str(value.get("original_value_name") or "").strip() == "1"
                    for value in vals
                ):
                    continue
            else:
                # Unsupported performance claims and reference-only values are
                # deliberately omitted.  A marketplace-required attribute must
                # be resolved from explicit facts instead of guessed.
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
    if len(title) < 15 and str(model_sku or "").strip():
        title = (title + f" SKU{model_sku}").strip()[:max_len]
    return title


def _english_safe_sku(raw: str) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if digits:
        return digits[-8:]
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
    if len(desc) < 100:
        desc = ((detail.get("title") or "") + " " + desc).strip()
    local_desc = desc[:3000]
    if len(local_desc) < 100:
        local_desc = (
            f"{local_desc} Please review the product images and "
            "specifications before purchase."
        ).strip()[:3000]
    title = _shopee_title(detail.get("title") or "", model_sku, max_len=180)
    return title, local_desc, price


def _parcel_facts(detail: dict) -> tuple[float, tuple[float, float, float]]:
    sku = (detail.get("skus") or [{}])[0]
    weight_value = sku.get("sku_weight") or detail.get("package_weight") or {}
    weight_kg = float(weight_value.get("value") or 0.2)
    if str(weight_value.get("unit") or "").upper() == "GRAM":
        weight_kg /= 1000.0
    dimensions = sku.get("sku_dimensions") or detail.get("package_dimensions") or {}
    return (
        max(weight_kg, 0.01),
        (
            max(float(dimensions.get("length") or 30), 1),
            max(float(dimensions.get("width") or 20), 1),
            max(float(dimensions.get("height") or 2), 1),
        ),
    )


def _regional_listing_detail(
    regional_detail: dict,
    semantic_detail: dict,
    *,
    title_override: str = "",
) -> dict:
    """Combine regional commercial facts with the approved semantic master.

    Prices must come from the target-region TikTok listing.  Copy and images
    may come from the PH English semantic master, but must never drag the PH
    price into MY/TH/VN publication tasks.
    """

    merged = dict(regional_detail)
    merged["title"] = (
        str(title_override or "").strip()
        or str(semantic_detail.get("title") or "").strip()
        or str(regional_detail.get("title") or "").strip()
    )
    if semantic_detail.get("description"):
        merged["description"] = semantic_detail["description"]
    if semantic_detail.get("main_images"):
        merged["main_images"] = semantic_detail["main_images"]
    return merged


def _run_publish_task(
    *,
    global_item_id: int,
    detail: dict,
    region: str,
    shop_id: int,
    token: str,
    model_sku: str,
    ref: dict | None,
    item_status: str = "UNLIST",
    create_model_when_missing: bool = True,
    global_original_price_cny_override: float | None = None,
    local_original_price_override: float | None = None,
    local_price_currency_override: str = "",
    logistics_override: list[dict] | None = None,
) -> dict:
    clean_status = str(item_status or "").strip().upper()
    if clean_status not in {"UNLIST", "NORMAL"}:
        raise ValueError("Shopee item_status must be UNLIST or NORMAL")
    meta = _shop_meta(shop_id, token)
    merchant_id = int(meta.get("merchant_id") or 0)
    if not merchant_id:
        raise RuntimeError("店铺无 merchant_id，无法走 CNSC 全球商品流程")
    mtoken = _merchant_token(shop_id, token)

    _title, _local_desc, source_price = _local_item_fields(
        detail, shop_id=shop_id, token=token, model_sku=model_sku, ref=ref
    )
    expected_currency = REGION_CURRENCY.get(region.upper())
    local_currency = str(local_price_currency_override or expected_currency or "").upper()
    if local_currency != expected_currency:
        raise ValueError(
            f"Shopee {region.upper()} local price must use {expected_currency}"
        )
    price = float(
        local_original_price_override
        if local_original_price_override is not None
        else source_price
    )
    if price <= 0:
        raise ValueError("Shopee local original price must be positive")
    global_price_cny = float(
        global_original_price_cny_override
        if global_original_price_cny_override is not None
        else tk_local_to_cny(price, region=region)
    )
    if global_price_cny <= 0:
        raise ValueError("Shopee global original price must be positive CNY")
    weight_kg, dimensions_cm = _parcel_facts(detail)
    logistics = (
        list(logistics_override)
        if logistics_override is not None
        else _logistic_info(
            shop_id,
            token,
            ref,
            region=region,
            weight_kg=weight_kg,
            dimensions_cm=dimensions_cm,
        )
    )
    if not logistics:
        raise RuntimeError("no Shopee logistics channel supports the approved parcel")
    pre_publish_logistics = list(logistics)
    sku = (detail.get("skus") or [{}])[0]
    stock = (
        sum(int(row.get("quantity") or 0) for row in sku.get("inventory") or [])
        or 50
    )
    global_model = ensure_single_global_model(
        global_item_id=int(global_item_id),
        merchant_id=merchant_id,
        merchant_token=mtoken,
        detail=detail,
        model_sku=model_sku,
        original_price=global_price_cny,
        stock=stock,
        create_when_missing=create_model_when_missing,
    )
    manual_models = [
        {
            "tier_index": list(row["tier_index"]),
            "original_price": price,
        }
        for row in (global_model.get("publish_models") or ())
    ]
    pub_body = {
        "global_item_id": int(global_item_id),
        "shop_id": int(shop_id),
        "shop_region": region.upper(),
        "item": {
            "item_status": clean_status,
            "original_price": price,
            "logistic": logistics,
            **({"model": manual_models} if manual_models else {}),
        },
    }
    try:
        p_resp = merchant_post(
            "/api/v2/global_product/create_publish_task",
            merchant_id,
            mtoken,
            pub_body,
        )
    except Exception as error:
        raise ShopeeRegionalPublishReconciliationError(
            "regional publish transport outcome is unknown",
            global_item_id=global_item_id,
            possible_external_writes_performed=[
                "shopee:regional_publish"
            ],
            reason="regional_publish_dispatch_unknown",
        ) from error
    if not isinstance(p_resp, dict):
        raise ShopeeRegionalPublishReconciliationError(
            "regional publish receipt is malformed",
            global_item_id=global_item_id,
            possible_external_writes_performed=[
                "shopee:regional_publish"
            ],
            reason="regional_publish_receipt_malformed",
        )
    if p_resp.get("error"):
        raise RuntimeError(p_resp.get("message") or p_resp.get("error") or p_resp)
    task_id = (p_resp.get("response") or {}).get("publish_task_id")
    if type(task_id) is not int or task_id <= 0:
        raise ShopeeRegionalPublishReconciliationError(
            "regional publish task identity is unavailable",
            global_item_id=global_item_id,
            possible_external_writes_performed=[
                "shopee:regional_publish"
            ],
            reason="regional_publish_task_identity_missing",
        )
    item_id = None
    last_status = None
    for _ in range(20):
        time.sleep(3)
        try:
            st = merchant_get(
                "/api/v2/global_product/get_publish_task_result",
                merchant_id,
                mtoken,
                {"publish_task_id": int(task_id)},
            )
            if not isinstance(st, dict) or st.get("error"):
                raise ValueError("regional publish task readback is invalid")
            res = st.get("response")
            if not isinstance(res, dict):
                raise ValueError("regional publish task response is invalid")
        except Exception as error:
            raise ShopeeRegionalPublishReconciliationError(
                "regional publish task readback is unavailable",
                global_item_id=global_item_id,
                external_writes_performed=[
                    "shopee:regional_publish"
                ],
                submission_accepted=True,
                reason="regional_publish_task_readback_unavailable",
            ) from error
        last_status = res.get("publish_status")
        if last_status == "success":
            success = res.get("success") or {}
            item_id = res.get("item_id") or success.get("item_id")
            break
        if last_status == "failed":
            failed = res.get("failed") or {}
            reason = failed.get("failed_reason") or st
            raise ShopeeRegionalPublishReconciliationError(
                f"regional publish task failed: {reason}",
                global_item_id=global_item_id,
                external_writes_performed=[
                    "shopee:regional_publish"
                ],
                submission_accepted=True,
                reason="regional_publish_task_failed",
            )
    if item_id is None:
        raise ShopeeRegionalPublishReconciliationError(
            "regional publish task did not converge",
            global_item_id=global_item_id,
            external_writes_performed=["shopee:regional_publish"],
            submission_accepted=True,
            reason="regional_publish_task_nonconvergent",
        )
    logistics = {}
    if item_id:
        for attempt in range(6):
            try:
                logistics = enable_all_applicable_logistics(
                    shop_id,
                    token,
                    item_id,
                )
                break
            except RuntimeError:
                if attempt == 5:
                    raise ShopeeRegionalPublishReconciliationError(
                        "regional logistics readback did not converge",
                        global_item_id=global_item_id,
                        external_writes_performed=[
                            "shopee:regional_publish"
                        ],
                        submission_accepted=True,
                        reason="regional_logistics_readback_unavailable",
                    )
                time.sleep(2)
    return {
        "ok": bool(item_id),
        "global_item_id": global_item_id,
        "publish_task_id": task_id,
        "item_id": item_id,
        "publish_status": last_status,
        "copy_mode": "shopee_global_master_auto_translation",
        "price_contract": {
            "source": (
                "immutable_release_plan"
                if local_original_price_override is not None
                else "regional_tiktok_detail"
            ),
            "global_original_price_cny": global_price_cny,
            "local_original_price": price,
            "local_currency": local_currency,
            "manual_model_price_count": len(manual_models),
        },
        "global_model": global_model,
        "pre_publish_logistics": pre_publish_logistics,
        "logistics": logistics,
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
    item_status: str = "UNLIST",
    global_original_price_cny_override: float | None = None,
    local_original_price_override: float | None = None,
    local_price_currency_override: str = "",
    logistics_override: list[dict] | None = None,
) -> dict:
    result = _run_publish_task(
        global_item_id=global_item_id,
        detail=detail,
        region=region,
        shop_id=shop_id,
        token=token,
        model_sku=model_sku,
        ref=ref,
        item_status=item_status,
        create_model_when_missing=False,
        global_original_price_cny_override=global_original_price_cny_override,
        local_original_price_override=local_original_price_override,
        local_price_currency_override=local_price_currency_override,
        logistics_override=logistics_override,
    )
    if result.get("item_id"):
        record_shop_item(
            str(global_item_id),
            region,
            shop_id=shop_id,
            item_id=result["item_id"],
        )
    return {**result, "flow": "publish_existing_global"}


def _single_variant_label(detail: dict) -> str:
    sku = (detail.get("skus") or [{}])[0]
    for attribute in sku.get("sales_attributes") or []:
        value = str(attribute.get("value_name") or attribute.get("name") or "").strip()
        if value:
            return value[:50]
    dimension = sku.get("sku_dimensions") or detail.get("package_dimensions") or {}
    length = dimension.get("length")
    width = dimension.get("width")
    if length and width:
        values = sorted((float(length), float(width)))
        rendered = [
            str(int(value)) if value.is_integer() else f"{value:g}"
            for value in values
        ]
        return f"{rendered[0]} x {rendered[1]} cm"
    return "Standard"


def ensure_global_models(
    *,
    global_item_id: int,
    merchant_id: int,
    merchant_token: str,
    detail: dict,
    original_price: float,
    stock: int,
    create_when_missing: bool = True,
    tier_name: str = "Variation",
) -> dict:
    """Bind every approved SKU and option label to one Shopee global model."""

    raw_skus = detail.get("skus")
    if not isinstance(raw_skus, list) or not raw_skus:
        raise ValueError("Shopee global model variants are unavailable")
    expected: list[dict[str, object]] = []
    for index, sku in enumerate(raw_skus):
        if not isinstance(sku, dict):
            raise ValueError("Shopee global model variant is invalid")
        model_sku = str(sku.get("seller_sku") or "").strip()
        option = str(sku.get("variation_option") or "").strip()
        if not option:
            option = _single_variant_label({**detail, "skus": [sku]})
        if not model_sku or not option:
            raise ValueError("Shopee global model SKU or option is unavailable")
        expected.append({
            "model_sku": model_sku,
            "option": option[:30],
            "tier_index": [index],
        })
    expected_skus = [str(row["model_sku"]) for row in expected]
    if len(set(expected_skus)) != len(expected_skus):
        raise ValueError("Shopee global model SKUs are not unique")

    existing = merchant_get(
        "/api/v2/global_product/get_global_model_list",
        merchant_id,
        merchant_token,
        {"global_item_id": int(global_item_id)},
    )
    models = (existing.get("response") or {}).get("global_model") or []
    if models:
        by_sku = {
            str(model.get("global_model_sku") or "").strip(): model
            for model in models
            if isinstance(model, dict)
        }
        if set(by_sku) != set(expected_skus) or len(models) != len(expected):
            raise RuntimeError(
                f"global item {global_item_id} model SKU mismatch: {sorted(by_sku)}"
            )
        publish_models = []
        for row in expected:
            model_sku = str(row["model_sku"])
            tier_index = by_sku[model_sku].get("tier_index")
            if (
                not isinstance(tier_index, (list, tuple))
                or not tier_index
                or any(not isinstance(value, int) for value in tier_index)
            ):
                raise RuntimeError(
                    f"global item {global_item_id} model tier index is unavailable"
                )
            publish_models.append({
                "global_model_sku": model_sku,
                "tier_index": list(tier_index),
            })
        return {
            "created": False,
            "global_item_id": int(global_item_id),
            "model_skus": expected_skus,
            "publish_models": publish_models,
            "variant_labels": [str(row["option"]) for row in expected],
            "legacy_item_sku": False,
        }

    if not create_when_missing:
        return {
            "created": False,
            "global_item_id": int(global_item_id),
            "model_skus": [],
            "variant_labels": [str(row["option"]) for row in expected],
            "legacy_item_sku": True,
        }
    response = merchant_post(
        "/api/v2/global_product/init_tier_variation",
        merchant_id,
        merchant_token,
        {
            "global_item_id": int(global_item_id),
            "tier_variation": [{
                "name": str(tier_name or "Variation")[:14],
                "option_list": [
                    {"option": str(row["option"])} for row in expected
                ],
            }],
            "global_model": [
                {
                    "tier_index": list(row["tier_index"]),
                    "global_model_sku": _english_safe_sku(str(row["model_sku"])),
                    "original_price": float(original_price),
                    "seller_stock": [{
                        "location_id": "CNZ",
                        "stock": int(stock),
                    }],
                }
                for row in expected
            ],
        },
    )
    error = str(response.get("error") or "").strip()
    if error and error != "-":
        raise RuntimeError(response.get("message") or error)
    verified = merchant_get(
        "/api/v2/global_product/get_global_model_list",
        merchant_id,
        merchant_token,
        {"global_item_id": int(global_item_id)},
    )
    models = (verified.get("response") or {}).get("global_model") or []
    by_sku = {
        str(model.get("global_model_sku") or "").strip(): model
        for model in models
        if isinstance(model, dict)
    }
    if set(by_sku) != set(expected_skus) or len(models) != len(expected):
        raise RuntimeError(
            f"global item {global_item_id} did not expose all approved Model SKUs"
        )
    publish_models = []
    for model_sku in expected_skus:
        tier_index = by_sku[model_sku].get("tier_index")
        if (
            not isinstance(tier_index, (list, tuple))
            or not tier_index
            or any(not isinstance(value, int) for value in tier_index)
        ):
            raise RuntimeError(
                f"global item {global_item_id} did not expose a model tier index"
            )
        publish_models.append({
            "global_model_sku": model_sku,
            "tier_index": list(tier_index),
        })
    return {
        "created": True,
        "global_item_id": int(global_item_id),
        "model_skus": expected_skus,
        "publish_models": publish_models,
        "variant_labels": [str(row["option"]) for row in expected],
        "legacy_item_sku": False,
    }


def ensure_single_global_model(
    *,
    global_item_id: int,
    merchant_id: int,
    merchant_token: str,
    detail: dict,
    model_sku: str,
    original_price: float,
    stock: int,
    create_when_missing: bool = True,
) -> dict:
    """Ensure even a one-option global product has an auditable Model SKU."""
    single_detail = {
        **detail,
        "skus": [{
            **((detail.get("skus") or [{}])[0]),
            "seller_sku": model_sku,
            "variation_option": _single_variant_label(detail),
        }],
    }
    result = ensure_global_models(
        global_item_id=global_item_id,
        merchant_id=merchant_id,
        merchant_token=merchant_token,
        detail=single_detail,
        original_price=original_price,
        stock=stock,
        create_when_missing=create_when_missing,
        tier_name="Size",
    )
    result["variant_label"] = result["variant_labels"][0]
    result.pop("variant_labels", None)
    return result


def update_global_master(
    *,
    global_item_id: int,
    merchant_id: int,
    merchant_token: str,
    detail: dict,
    title: str,
    description: str,
    ref: dict | None,
    original_price: float | None = None,
) -> dict:
    """Update the English master and remove reference-product fact leakage."""

    category_id = int(
        (ref or {}).get("category_id")
        or DEFAULT_CATEGORY.get("TH")
        or 101157
    )
    body = {
        "global_item_id": int(global_item_id),
        "global_item_name": _shopee_title(title, "", max_len=120),
        "description": str(description or "").strip()[:3000],
        "attribute_list": _global_attribute_list(
            merchant_id,
            merchant_token,
            category_id,
            ref,
            detail=detail,
        ),
    }
    if original_price is not None:
        if float(original_price) <= 0:
            raise ValueError("Shopee global original price must be positive CNY")
        body["original_price"] = float(original_price)
    if len(body["description"]) < 500:
        raise ValueError("Shopee global master description must be at least 500 characters")
    try:
        response = merchant_post(
            "/api/v2/global_product/update_global_item",
            merchant_id,
            merchant_token,
            body,
        )
    except Exception as error:
        raise ShopeeGlobalMasterReconciliationError(
            "global master update transport outcome is unknown",
            global_item_id=global_item_id,
            write_confirmed=False,
        ) from error
    if not isinstance(response, dict):
        raise ShopeeGlobalMasterReconciliationError(
            "global master update receipt is malformed",
            global_item_id=global_item_id,
            write_confirmed=False,
        )
    error = str(response.get("error") or "").strip()
    if error and error != "-":
        raise RuntimeError(response.get("message") or error)
    try:
        readback = merchant_get(
            "/api/v2/global_product/get_global_item_info",
            merchant_id,
            merchant_token,
            {"global_item_id_list": str(global_item_id)},
        )
        items = (readback.get("response") or {}).get("global_item_list") or []
        if len(items) != 1:
            raise ValueError("global master readback row count is not exact")
        item = items[0]
        verified = (
            str(item.get("global_item_name") or "") == body["global_item_name"]
            and str(item.get("description") or "") == body["description"]
            and (
                "original_price" not in body
                or abs(
                    float(item.get("original_price") or 0)
                    - float(body["original_price"])
                )
                < 0.000001
            )
        )
        if not verified:
            raise ValueError("global master copy readback mismatch")
    except Exception as error:
        raise ShopeeGlobalMasterReconciliationError(
            f"global item {global_item_id} readback did not converge",
            global_item_id=global_item_id,
        ) from error
    attributes = [
        {
            "name": row.get("original_attribute_name"),
            "values": [
                value.get("original_value_name")
                for value in row.get("attribute_value_list") or []
            ],
        }
        for row in item.get("attribute_list") or []
    ]
    return {
        "source": "official_shopee_partner_api",
        "global_item_id": int(global_item_id),
        "title": item.get("global_item_name"),
        "description_length": len(str(item.get("description") or "")),
        "attributes": attributes,
        "verified": True,
    }


def ensure_global_master(
    *,
    global_item_id: int,
    merchant_id: int,
    merchant_token: str,
    detail: dict,
    title: str,
    description: str,
    ref: dict | None,
    original_price: float | None = None,
) -> dict:
    """Update copy once only when strict official readback proves drift."""

    expected_title = _shopee_title(title, "", max_len=120)
    expected_description = str(description or "").strip()[:3000]
    expected_price = (
        float(original_price) if original_price is not None else None
    )
    if expected_price is not None and expected_price <= 0:
        raise ValueError("Shopee global original price must be positive CNY")
    current = merchant_get(
        "/api/v2/global_product/get_global_item_info",
        merchant_id,
        merchant_token,
        {"global_item_id_list": str(global_item_id)},
    )
    error = (
        str(current.get("error") or "").strip()
        if isinstance(current, dict)
        else "malformed_response"
    )
    response = current.get("response") if isinstance(current, dict) else None
    items = (
        response.get("global_item_list")
        if isinstance(response, dict)
        else None
    )
    if (
        error not in {"", "-"}
        or not isinstance(items, list)
        or len(items) != 1
        or not isinstance(items[0], dict)
        or str(items[0].get("global_item_id") or "")
        != str(global_item_id)
        or not isinstance(items[0].get("global_item_name"), str)
        or not isinstance(items[0].get("description"), str)
    ):
        raise RuntimeError(
            f"global item {global_item_id} copy preflight is invalid"
        )
    item = items[0]
    if (
        item["global_item_name"] == expected_title
        and item["description"] == expected_description
        and (
            expected_price is None
            or abs(float(item.get("original_price") or 0) - expected_price)
            < 0.000001
        )
    ):
        return {
            "source": "official_shopee_partner_api",
            "global_item_id": int(global_item_id),
            "verified": True,
            "updated": False,
            "external_writes_performed": [],
        }
    updated = update_global_master(
        global_item_id=global_item_id,
        merchant_id=merchant_id,
        merchant_token=merchant_token,
        detail=detail,
        title=expected_title,
        description=expected_description,
        ref=ref,
        original_price=expected_price,
    )
    return {
        **updated,
        "updated": True,
        "external_writes_performed": ["shopee:global_master:update"],
    }


def _official_global_item_status(
    *,
    global_item_id: int,
    merchant_id: int,
    merchant_token: str,
) -> str:
    """Return one exact official global status or fail before any write."""

    response = merchant_get(
        "/api/v2/global_product/get_global_item_info",
        merchant_id,
        merchant_token,
        {"global_item_id_list": str(global_item_id)},
    )
    error = (
        str(response.get("error") or "").strip()
        if isinstance(response, dict)
        else "malformed_response"
    )
    body = response.get("response") if isinstance(response, dict) else None
    rows = body.get("global_item_list") if isinstance(body, dict) else None
    if (
        error not in {"", "-"}
        or not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], dict)
        or str(rows[0].get("global_item_id") or "")
        != str(global_item_id)
    ):
        raise RuntimeError("official global item identity is unavailable")
    status = rows[0].get("global_item_status")
    if not isinstance(status, str) or not status.strip():
        raise RuntimeError("official global item status is unavailable")
    normalized = status.strip().upper()
    if normalized not in {"NORMAL", "DELETED"}:
        raise RuntimeError(
            f"official global item status is not executable: {normalized}"
        )
    return normalized


def update_local_listing_copy(
    *,
    shop_id: int,
    token: str,
    item_id: int,
    title: str,
    description: str,
) -> dict:
    """Repair one already-published local listing without creating a duplicate."""

    clean_title = str(title or "").strip()
    clean_description = str(description or "").strip()
    if not clean_title:
        raise ValueError("Shopee local title is required")
    if len(clean_description) < 500:
        raise ValueError("Shopee local description must be at least 500 characters")
    response = shop_post(
        "/api/v2/product/update_item",
        shop_id,
        token,
        {
            "item_id": int(item_id),
            "item_name": clean_title,
            "description": clean_description[:3000],
        },
    )
    error = str(response.get("error") or "").strip()
    if error and error != "-":
        raise RuntimeError(response.get("message") or error)
    logistics = enable_all_applicable_logistics(shop_id, token, item_id)
    readback = _item_base_info(shop_id, token, item_id)
    verified = (
        str(readback.get("item_name") or "") == clean_title
        and str(readback.get("description") or "") == clean_description[:3000]
    )
    if not verified:
        raise RuntimeError(f"Shopee item {item_id} local repair readback mismatch")
    return {
        "source": "official_shopee_partner_api",
        "item_id": str(item_id),
        "title": readback.get("item_name"),
        "description_length": len(str(readback.get("description") or "")),
        "logistics_enabled": sum(
            1
            for row in readback.get("logistic_info") or []
            if row.get("enabled")
        ),
        "logistics": logistics,
        "has_model": bool(readback.get("has_model")),
        "verified": True,
    }


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
    title_override: str = "",
    description_override: str = "",
    global_original_price_cny_override: float | None = None,
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
    price = float(
        global_original_price_cny_override
        if global_original_price_cny_override is not None
        else tk_local_to_cny(local_price, region=region)
    )
    if price <= 0:
        raise ValueError("Shopee global original price must be positive CNY")
    stock = sum(int(i.get("quantity") or 0) for i in (sku.get("inventory") or [])) or 50
    w = sku.get("sku_weight") or detail.get("package_weight") or {}
    weight = float(w.get("value") or 0.2)
    if (w.get("unit") or "").upper() == "GRAM":
        weight = weight / 1000.0
    dim = sku.get("sku_dimensions") or detail.get("package_dimensions") or {}
    length = int(float(dim.get("length") or 30))
    width = int(float(dim.get("width") or 20))
    height = int(float(dim.get("height") or 2))

    global_copy = (
        {
            "title": str(title_override).strip(),
            "description": str(description_override).strip(),
            "source_region": tk_source_region,
            "used_ph_english": True,
        }
        if str(title_override).strip() and str(description_override).strip()
        else build_global_copy(detail, model_sku, source_region=tk_source_region)
    )
    global_title = _shopee_title(
        title_override or global_copy["title"],
        model_sku,
        max_len=120,
    )
    global_desc = (
        str(description_override or "").strip()
        or global_copy["description"]
    )[:3000]

    global_body = {
        "category_id": int(category_id),
        "global_item_name": global_title,
        "description": global_desc,
        "original_price": price,
        "weight": max(weight, 0.01),
        "dimension": {
            "package_length": max(length, 1),
            "package_width": max(width, 1),
            "package_height": max(height, 1),
        },
        "image": {"image_id_list": image_ids[:9]},
        "attribute_list": _global_attribute_list(
            merchant_id,
            mtoken,
            int(category_id),
            ref,
            detail=detail,
        ),
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
    try:
        global_model = ensure_global_models(
            global_item_id=int(global_item_id),
            merchant_id=merchant_id,
            merchant_token=mtoken,
            detail=detail,
            original_price=price,
            stock=stock,
        )
    except Exception as error:
        raise ShopeeGlobalMasterReconciliationError(
            "global master was created but its model did not verify",
            global_item_id=int(global_item_id),
            write_class="shopee:global_master:create",
            reason="global_master_create_requires_reconciliation",
        ) from error
    return {
        "ok": True,
        "flow": "global_only",
        "global_item_id": global_item_id,
        "model_sku": model_sku,
        "global_title": global_title,
        "global_description_len": len(global_desc),
        "global_model": global_model,
        "tk_source_region": tk_source_region,
        "used_ph_english": global_copy.get("used_ph_english"),
    }


def _publish_global(
    detail: dict,
    *,
    local_detail: dict | None = None,
    region: str,
    shop_id: int,
    token: str,
    model_sku: str,
    image_ids: list[str],
    ref: dict | None,
    tk_source_region: str = "",
    item_status: str = "UNLIST",
    title_override: str = "",
    description_override: str = "",
    global_original_price_cny_override: float | None = None,
    local_original_price_override: float | None = None,
    local_price_currency_override: str = "",
    logistics_override: list[dict] | None = None,
    map_match_key: str = "",
    replaced_deleted_global_item_id: int | None = None,
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
        title_override=title_override,
        description_override=description_override,
        global_original_price_cny_override=global_original_price_cny_override,
    )
    global_item_id = int(created["global_item_id"])
    master_receipt = {
        "verified": True,
        "created": True,
        "updated": False,
        "external_writes_performed": [
            "shopee:global_master:create"
        ],
    }
    if map_match_key:
        try:
            if replaced_deleted_global_item_id is not None:
                replace_deleted_global_entry(
                    str(replaced_deleted_global_item_id),
                    str(global_item_id),
                    match_key=map_match_key,
                    global_model_sku=model_sku,
                    title=created.get("global_title") or "",
                )
            else:
                upsert_global_entry(
                    str(global_item_id),
                    match_key=map_match_key,
                    global_model_sku=model_sku,
                    title=created.get("global_title") or "",
                    published_regions=[],
                )
        except Exception as error:
            raise ShopeeRegionalPublishReconciliationError(
                "global master was created but its durable mapping failed",
                global_item_id=global_item_id,
                external_writes_performed=[
                    "shopee:global_master:create"
                ],
                submission_accepted=True,
                reason="global_master_mapping_persistence_failed",
            ) from error
    try:
        result = _run_publish_task(
            global_item_id=global_item_id,
            detail=local_detail or detail,
            region=region,
            shop_id=shop_id,
            token=token,
            model_sku=model_sku,
            ref=ref,
            item_status=item_status,
            global_original_price_cny_override=(
                global_original_price_cny_override
            ),
            local_original_price_override=local_original_price_override,
            local_price_currency_override=local_price_currency_override,
            logistics_override=logistics_override,
        )
    except Exception as error:
        raise _merge_shopee_publish_write_evidence(
            error,
            global_item_id=global_item_id,
            global_master_receipt=master_receipt,
        ) from error
    if map_match_key and result.get("item_id"):
        try:
            record_shop_item(
                str(global_item_id),
                region,
                shop_id=shop_id,
                item_id=result["item_id"],
            )
        except Exception as error:
            raise ShopeeRegionalPublishReconciliationError(
                "regional publish succeeded but its durable mapping failed",
                global_item_id=global_item_id,
                external_writes_performed=[
                    "shopee:global_master:create",
                    "shopee:regional_publish",
                ],
                submission_accepted=True,
                reason="regional_mapping_persistence_failed",
            ) from error
    return {
        **created,
        **result,
        "flow": "global_product",
        "global_master": master_receipt,
    }


def publish_match_key(
    match_key: str,
    region: str,
    *,
    dry_run: bool = False,
    global_only: bool = True,
    publish_shops: bool = False,
    item_status: str = "UNLIST",
    title_override: str = "",
    description_override: str = "",
    global_original_price_cny_override: float | None = None,
    local_original_price_override: float | None = None,
    local_price_currency_override: str = "",
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

    tk_row, tk_detail, tk_source = _find_tk_for_global(key, reg)
    try:
        row = _find_tk_row(key, reg)
        regional_detail = _fetch_tk_detail(row)
    except RuntimeError:
        if local_original_price_override is None:
            raise
        # The approved ReleasePlan supplies the exact regional commercial
        # price.  A missing TikTok source-row must not prevent Shopee from
        # publishing the approved semantic master, and must never substitute
        # a guessed price.
        regional_detail = dict(tk_detail)
    detail = dict(tk_detail)
    if str(title_override or "").strip():
        detail["title"] = str(title_override).strip()
    if str(description_override or "").strip():
        detail["description"] = str(description_override).strip()
    local_detail = _regional_listing_detail(
        regional_detail,
        detail,
        title_override=title_override,
    )

    urls = _collect_image_urls(detail)

    model_sku = key  # Shopee 4 位码
    token = ensure_shop_token(shop_id)

    existing_gid = global_item_id_for_match_key(key)
    global_preview = (
        {
            "title": str(title_override).strip(),
            "description": str(description_override).strip(),
            "source_region": tk_source,
            "used_ph_english": True,
        }
        if str(title_override).strip() and str(description_override).strip()
        else build_global_copy(detail, model_sku, source_region=tk_source)
    )
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
            "price": (local_detail.get("skus") or [{}])[0].get("price"),
        }
        if existing_gid:
            out["mode"] = "publish_existing_global" if not global_only else "existing_global"
            out["global_item_id"] = existing_gid
        elif global_only:
            out["mode"] = "global_only"
        return out

    ref = _reference_item(reg, shop_id, token)
    global_master_receipt = None
    preflight_logistics = None
    if not global_only:
        expected_currency = REGION_CURRENCY.get(reg)
        override_currency = str(local_price_currency_override or "").upper()
        if local_original_price_override is not None:
            if override_currency != expected_currency:
                raise ValueError(
                    f"Shopee {reg} local price must use {expected_currency}"
                )
            if float(local_original_price_override) <= 0:
                raise ValueError("Shopee local original price must be positive")
        if (
            global_original_price_cny_override is not None
            and float(global_original_price_cny_override) <= 0
        ):
            raise ValueError("Shopee global original price must be positive CNY")
        weight_kg, dimensions_cm = _parcel_facts(local_detail)
        preflight_logistics = _logistic_info(
            shop_id,
            token,
            ref,
            region=reg,
            weight_kg=weight_kg,
            dimensions_cm=dimensions_cm,
        )
    meta = _shop_meta(shop_id, token)
    if meta.get("is_cb") or meta.get("is_upgraded_cbsc"):
        retired_global_item_id = None
        merchant_id = int(meta.get("merchant_id") or 0)
        if not merchant_id:
            raise RuntimeError("Shopee merchant identity is unavailable")
        merchant_token = _merchant_token(shop_id, token)
        if existing_gid:
            existing_status = _official_global_item_status(
                global_item_id=int(existing_gid),
                merchant_id=merchant_id,
                merchant_token=merchant_token,
            )
            if existing_status == "DELETED":
                retired_global_item_id = int(existing_gid)
                existing_gid = None
        if existing_gid and not global_only:
            if str(title_override).strip() and str(description_override).strip():
                global_master_receipt = ensure_global_master(
                    global_item_id=int(existing_gid),
                    merchant_id=merchant_id,
                    merchant_token=merchant_token,
                    detail=detail,
                    title=title_override,
                    description=description_override,
                    ref=ref,
                    original_price=global_original_price_cny_override,
                )
            try:
                result = _publish_existing_global(
                    int(existing_gid),
                    local_detail,
                    region=reg,
                    shop_id=shop_id,
                    token=token,
                    model_sku=model_sku,
                    ref=ref,
                    item_status=item_status,
                    global_original_price_cny_override=(
                        global_original_price_cny_override
                    ),
                    local_original_price_override=local_original_price_override,
                    local_price_currency_override=local_price_currency_override,
                    logistics_override=preflight_logistics,
                )
            except Exception as error:
                if (
                    isinstance(global_master_receipt, dict)
                    and global_master_receipt.get("updated") is True
                ) or hasattr(error, "external_write_evidence"):
                    raise _merge_shopee_publish_write_evidence(
                        error,
                        global_item_id=int(existing_gid),
                        global_master_receipt=global_master_receipt,
                    ) from error
                raise
        elif existing_gid and global_only:
            if not (
                str(title_override).strip()
                and str(description_override).strip()
                and global_original_price_cny_override is not None
            ):
                raise ValueError(
                    "approved Shopee global title, description, and price are required"
                )
            global_master_receipt = ensure_global_master(
                global_item_id=int(existing_gid),
                merchant_id=merchant_id,
                merchant_token=merchant_token,
                detail=detail,
                title=title_override,
                description=description_override,
                ref=ref,
                original_price=global_original_price_cny_override,
            )
            return {
                "ok": True,
                "flow": "existing_global",
                "global_item_id": int(existing_gid),
                "global_master": global_master_receipt,
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
                    title_override=title_override,
                    description_override=description_override,
                    global_original_price_cny_override=(
                        global_original_price_cny_override
                    ),
                )
                try:
                    if retired_global_item_id is not None:
                        replace_deleted_global_entry(
                            str(retired_global_item_id),
                            str(result["global_item_id"]),
                            match_key=key,
                            global_model_sku=model_sku,
                            title=(
                                result.get("global_title")
                                or global_preview["title"]
                            ),
                        )
                    else:
                        upsert_global_entry(
                            str(result["global_item_id"]),
                            match_key=key,
                            global_model_sku=model_sku,
                            title=(
                                result.get("global_title")
                                or global_preview["title"]
                            ),
                        )
                except Exception as error:
                    raise ShopeeGlobalMasterReconciliationError(
                        (
                            "global master was created but its durable "
                            "mapping failed"
                        ),
                        global_item_id=int(result["global_item_id"]),
                        write_class="shopee:global_master:create",
                        reason="global_master_mapping_persistence_failed",
                    ) from error
            else:
                result = _publish_global(
                    detail,
                    local_detail=local_detail,
                    region=reg,
                    shop_id=shop_id,
                    token=token,
                    model_sku=model_sku,
                    image_ids=image_ids,
                    ref=ref,
                    tk_source_region=tk_source,
                    item_status=item_status,
                    title_override=title_override,
                    description_override=description_override,
                    global_original_price_cny_override=(
                        global_original_price_cny_override
                    ),
                    local_original_price_override=local_original_price_override,
                    local_price_currency_override=local_price_currency_override,
                    logistics_override=preflight_logistics,
                    map_match_key=key,
                    replaced_deleted_global_item_id=retired_global_item_id,
                )
        return {
            **result,
            "region": reg,
            "shop_id": shop_id,
            "match_key": key,
            "model_sku": model_sku,
            "global_master": (
                result.get("global_master")
                if isinstance(result, dict)
                and isinstance(result.get("global_master"), dict)
                else global_master_receipt
            ),
        }

    image_ids = _upload_images(urls)
    payload = build_payload(
        local_detail,
        region=reg,
        shop_id=shop_id,
        token=token,
        model_sku=model_sku,
        image_ids=image_ids,
        item_status=item_status,
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
