"""执行 MX 店 save / publish（供 scripts 调用）。"""
from __future__ import annotations

import copy
import json
import math

from core.db import connect, init_db
from modules.miaoshou.client import post_open
from modules.miaoshou.mx_checks import (
    MX_EXIT_VOLUMETRIC_NEEDS_CONFIRM,
    VolumetricWeightNeedsConfirmation,
    assert_volumetric_confirmed,
)
from modules.miaoshou.mx_confirm import (
    MX_EXIT_NEEDS_USER_CONFIRM,
    UserConfirmRequired,
    assert_user_approved,
)
from modules.miaoshou.mx_migrate import (
    apply_mx_shop_collect_info,
    apply_mx_multi_shop_collect_info,
    clean_notes,
    collect_package_context,
    collect_master_images_and_product,
    fetch_site_collect_info,
    index_sku_map_keys_by_match_key,
    is_ok_image,
    mx_item_num,
    MxSkuVariantWrite,
    php_to_mxn,
    ensure_mx_claimed,
)
from modules.miaoshou.mx_copy import apply_mx_spanish_listing
from modules.catalog.logistics_weights import weight_index_by_match_key
from modules.catalog.sku_key import tk_match_key

MX_SHOP_ID = 16265910
GET_PATH = "/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info"
SAVE_PATH = "/open/v1/product/collect_box/tiktok/collect_box/save_shop_collect_item_info"
PUBLISH_PATH = "/open/v1/product/collect_box/tiktok/collect_box/save_move_collect_task"


def publish_mx_listing(
    *,
    collect_box_detail_id: int,
    seller_sku: str,
    ph_product_id: str,
    publish: bool = True,
    mxn_sale: float | None = None,
    mxn_list: float | int | None = None,
    stock: int | None = None,
    master_region: str = "PH",
    weight_kg: float | None = None,
    package_cm: tuple[int, int, int] | None = None,
    pop_quote: object | None = None,
    volumetric_confirmed: bool = False,
    confirm_token: str | None = None,
    skip_user_confirm: bool = False,
    spanish_copy: bool = True,
) -> int:
    init_db()
    row = connect().execute(
        """
        SELECT price FROM products p
        JOIN shops s ON p.shop_cipher = s.cipher
        WHERE p.seller_sku = ? AND UPPER(s.region) = 'PH'
        LIMIT 1
        """,
        (seller_sku,),
    ).fetchone()
    php_price = float(row["price"]) if row else 0.0
    if mxn_sale is None and php_price <= 0:
        raise RuntimeError(f"shop.db 未找到 PH 价格且无 POP 售价: seller_sku={seller_sku}")
    mxn_price = round(float(mxn_sale), 2) if mxn_sale is not None else php_to_mxn(php_price)
    mxn_list_price = int(math.ceil(float(mxn_list))) if mxn_list is not None else int(math.ceil(mxn_price))

    print(f"seller_sku={seller_sku} → MX itemNum={mx_item_num(seller_sku)}")
    print(f"collectBoxDetailId={collect_box_detail_id} MX shopId={MX_SHOP_ID}")
    if mxn_sale is not None:
        print(f"MXN list(ceil)={mxn_list_price} sale={mxn_price} (POP/manual)")
    else:
        print(f"PH price={php_price} PHP → MXN={mxn_price}")
    if stock is not None:
        print(f"stock={stock}")

    if pop_quote is not None:
        try:
            assert_volumetric_confirmed(
                pop_quote,
                volumetric_confirmed=volumetric_confirmed,
                publish=publish,
            )
        except VolumetricWeightNeedsConfirmation as exc:
            print(f"\n⚠ 体积重 > 实重，需确认后再上架:\n  {exc}")
            return MX_EXIT_VOLUMETRIC_NEEDS_CONFIRM

    ensure_mx_claimed(collect_box_detail_id, mx_shop_id=MX_SHOP_ID)

    rd = post_open(GET_PATH, {"detailId": collect_box_detail_id, "shopId": MX_SHOP_ID})
    if rd.get("result") != "success":
        print("get failed:", json.dumps(rd, ensure_ascii=False))
        return 1

    data = rd.get("data") or {}
    info = copy.deepcopy(data.get("shopCollectItemInfo") or {})
    oss_md5 = data.get("ossMd5", "")

    master_product: dict | None = None
    good_urls: list[str] = []
    try:
        good_urls, master_product = collect_master_images_and_product(
            ph_product_id, region=master_region
        )
        print(f"{master_region} images: {len(good_urls)}")
    except Exception as exc:
        print(f"⚠ 母版 API 拉取失败，使用采集箱文案翻译: {exc}")
        for u in info.get("imgUrls") or []:
            if isinstance(u, str) and u.startswith("http"):
                good_urls.append(u)

    if weight_kg is None:
        mk = tk_match_key(seller_sku)
        wentry = weight_index_by_match_key().get(mk) or {}
        if wentry.get("weight_g"):
            weight_kg = int(wentry["weight_g"]) / 1000
            print(f"weight: {weight_kg}kg (match_key {mk} 四国合并)")

    if package_cm:
        pl, pw, ph = package_cm
        print(f"package(cm): {pl} x {pw} x {ph} (manual)")
    else:
        ctx = collect_package_context(
            collect_box_detail_id=collect_box_detail_id,
            tiktok_product=master_product,
        )
        pl, pw, ph = ctx["package_length"], ctx["package_width"], ctx["package_height"]
        print(f"package(cm): {pl} x {pw} x {ph} ({ctx['package_source']})")

    for prop in info.get("skuPropertyList") or []:
        for val in prop.get("attrValueList") or []:
            if not is_ok_image(val.get("imgUrl") or ""):
                val["imgUrl"] = good_urls[0]

    apply_mx_shop_collect_info(
        info,
        seller_sku=seller_sku,
        mxn_list_price=mxn_list_price,
        mxn_sale_price=mxn_price,
        package_length=pl,
        package_width=pw,
        package_height=ph,
        good_image_urls=good_urls,
        stock=stock,
        weight_kg=weight_kg,
        mx_shop_id=MX_SHOP_ID,
    )

    if spanish_copy:
        try:
            apply_mx_spanish_listing(
                info, master_product, good_urls=good_urls, seller_sku=seller_sku
            )
            print(f"MX Spanish title: {(info.get('title') or '')[:120]}")
        except Exception as exc:
            print(f"⚠ 西语文案失败，回退英文 notes: {exc}")
            info["notes"] = clean_notes(info.get("notes") or "", good_urls)
    else:
        info["notes"] = clean_notes(info.get("notes") or "", good_urls)

    sr = post_open(
        SAVE_PATH,
        {
            "ossMd5": oss_md5,
            "detailId": collect_box_detail_id,
            "shopId": MX_SHOP_ID,
            "shopCollectItemInfo": info,
        },
    )
    print("\nSave:", json.dumps(sr, ensure_ascii=False, indent=2))
    if sr.get("result") != "success":
        return 2

    rd2 = post_open(GET_PATH, {"detailId": collect_box_detail_id, "shopId": MX_SHOP_ID})
    info2 = (rd2.get("data") or {}).get("shopCollectItemInfo") or {}
    print("\nSaved itemNum:", mx_item_num(seller_sku))
    print("Saved pkg:", info2.get("packageLength"), info2.get("packageWidth"), info2.get("packageHeight"))
    print("Saved skuMap:", json.dumps(info2.get("skuMap"), ensure_ascii=False))

    if not publish:
        return 0

    if not skip_user_confirm:
        try:
            assert_user_approved(confirm_token)
        except UserConfirmRequired as exc:
            print(f"\n⚠ 需先在对话框确认上架:\n  {exc}")
            return MX_EXIT_NEEDS_USER_CONFIRM

    pr = post_open(
        PUBLISH_PATH,
        {"detailIds": [collect_box_detail_id], "shopIds": [MX_SHOP_ID]},
    )
    print("\nPublish:", json.dumps(pr, ensure_ascii=False, indent=2))
    return 0 if pr.get("result") == "success" else 3


def publish_mx_multi_listing(
    *,
    collect_box_detail_id: int,
    ph_product_id: str,
    variant_writes: list[MxSkuVariantWrite],
    publish: bool = True,
    stock: int | None = None,
    master_region: str = "PH",
    package_cm: tuple[int, int, int] | None = None,
    confirm_token: str | None = None,
    skip_user_confirm: bool = False,
    spanish_copy: bool = True,
) -> int:
    """同链接多规格 MX 上架：每个 skuMap 行独立货号与折前原价 ceil。"""
    if not variant_writes:
        raise RuntimeError("variant_writes 不能为空")

    init_db()
    mks = [v.match_key for v in variant_writes]
    print(f"MX multi listing · match_keys={','.join(mks)}")
    print(f"collectBoxDetailId={collect_box_detail_id} MX shopId={MX_SHOP_ID}")
    for v in variant_writes:
        print(
            f"  {v.match_key} {v.seller_sku} → itemNum={mx_item_num(v.seller_sku)} "
            f"list={v.mxn_list_price} MXN"
            + (f" weight={v.weight_kg}kg" if v.weight_kg else "")
            + (f" ({v.variant_label})" if v.variant_label else "")
        )
    if stock is not None:
        print(f"stock={stock}")

    ensure_mx_claimed(collect_box_detail_id, mx_shop_id=MX_SHOP_ID)
    good_urls, master_product = collect_master_images_and_product(
        ph_product_id, region=master_region
    )
    print(f"{master_region} images: {len(good_urls)}")

    site_info = fetch_site_collect_info(collect_box_detail_id, site="MY") or {}
    sku_map_key_by_match_key = index_sku_map_keys_by_match_key(
        site_collect_info=site_info,
        tiktok_product=master_product,
    )
    missing = [v.match_key for v in variant_writes if v.match_key not in sku_map_key_by_match_key]
    if missing:
        raise RuntimeError(f"skuMap 未匹配对齐码: {', '.join(missing)}")

    rd = post_open(GET_PATH, {"detailId": collect_box_detail_id, "shopId": MX_SHOP_ID})
    if rd.get("result") != "success":
        print("get failed:", json.dumps(rd, ensure_ascii=False))
        return 1

    data = rd.get("data") or {}
    info = copy.deepcopy(data.get("shopCollectItemInfo") or {})
    oss_md5 = data.get("ossMd5", "")

    if package_cm:
        pl, pw, ph = package_cm
        print(f"package(cm): {pl} x {pw} x {ph} (manual)")
    else:
        ctx = collect_package_context(
            collect_box_detail_id=collect_box_detail_id,
            tiktok_product=master_product,
        )
        pl, pw, ph = ctx["package_length"], ctx["package_width"], ctx["package_height"]
        print(f"package(cm): {pl} x {pw} x {ph} ({ctx['package_source']})")

    for prop in info.get("skuPropertyList") or []:
        for val in prop.get("attrValueList") or []:
            if not is_ok_image(val.get("imgUrl") or ""):
                val["imgUrl"] = good_urls[0]

    apply_mx_multi_shop_collect_info(
        info,
        variants=variant_writes,
        package_length=pl,
        package_width=pw,
        package_height=ph,
        sku_map_key_by_match_key=sku_map_key_by_match_key,
        good_image_urls=good_urls,
        stock=stock,
        mx_shop_id=MX_SHOP_ID,
    )

    if spanish_copy:
        try:
            apply_mx_spanish_listing(
                info,
                master_product,
                good_urls=good_urls,
                seller_sku=variant_writes[0].seller_sku,
            )
            print(f"MX Spanish title: {(info.get('title') or '')[:120]}")
        except Exception as exc:
            print(f"⚠ 西语文案失败，回退英文 notes: {exc}")
            info["notes"] = clean_notes(info.get("notes") or "", good_urls)
    else:
        info["notes"] = clean_notes(info.get("notes") or "", good_urls)

    sr = post_open(
        SAVE_PATH,
        {
            "ossMd5": oss_md5,
            "detailId": collect_box_detail_id,
            "shopId": MX_SHOP_ID,
            "shopCollectItemInfo": info,
        },
    )
    print("\nSave:", json.dumps(sr, ensure_ascii=False, indent=2))
    if sr.get("result") != "success":
        return 2

    rd2 = post_open(GET_PATH, {"detailId": collect_box_detail_id, "shopId": MX_SHOP_ID})
    info2 = (rd2.get("data") or {}).get("shopCollectItemInfo") or {}
    print("\nSaved skuMap:", json.dumps(info2.get("skuMap"), ensure_ascii=False))

    if not publish:
        return 0

    if not skip_user_confirm:
        try:
            assert_user_approved(confirm_token)
        except UserConfirmRequired as exc:
            print(f"\n⚠ 需先在对话框确认上架:\n  {exc}")
            return MX_EXIT_NEEDS_USER_CONFIRM

    pr = post_open(
        PUBLISH_PATH,
        {"detailIds": [collect_box_detail_id], "shopIds": [MX_SHOP_ID]},
    )
    print("\nPublish:", json.dumps(pr, ensure_ascii=False, indent=2))
    return 0 if pr.get("result") == "success" else 3
