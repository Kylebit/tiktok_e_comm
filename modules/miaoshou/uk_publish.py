"""执行 UK 店 save / publish（供 scripts / Web 审批调用）。"""
from __future__ import annotations

import copy
import json
import math

from core.db import connect, init_db
from modules.miaoshou.client import post_open
from modules.miaoshou.mx_migrate import (
    apply_mx_shop_collect_info,
    clean_notes,
    collect_master_images_and_product,
    collect_package_context,
    ensure_mx_claimed,
    mx_item_num,
)
from modules.miaoshou.uk_checks import (
    UK_EXIT_VOLUMETRIC_NEEDS_CONFIRM,
    VolumetricWeightNeedsConfirmation,
    assert_volumetric_confirmed,
)
from modules.miaoshou.uk_confirm import (
    UK_EXIT_NEEDS_USER_CONFIRM,
    UserConfirmRequired,
    assert_user_approved,
)
from modules.catalog.logistics_weights import weight_index_by_match_key
from modules.catalog.sku_key import tk_match_key

UK_SHOP_ID = 10204699
GET_PATH = "/open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info"
SAVE_PATH = "/open/v1/product/collect_box/tiktok/collect_box/save_shop_collect_item_info"
PUBLISH_PATH = "/open/v1/product/collect_box/tiktok/collect_box/save_move_collect_task"


def publish_uk_listing(
    *,
    collect_box_detail_id: int,
    seller_sku: str,
    ph_product_id: str,
    publish: bool = True,
    gbp_sale: float | None = None,
    gbp_list: float | int | None = None,
    stock: int | None = None,
    master_region: str = "PH",
    weight_kg: float | None = None,
    package_cm: tuple[int, int, int] | None = None,
    pop_quote: object | None = None,
    volumetric_confirmed: bool = False,
    confirm_token: str | None = None,
    skip_user_confirm: bool = False,
) -> int:
    init_db()
    if gbp_sale is None and gbp_list is None:
        raise RuntimeError(f"缺少 POP 售价: seller_sku={seller_sku}")
    gbp_price = round(float(gbp_sale or gbp_list), 2)
    gbp_list_price = int(math.ceil(float(gbp_list))) if gbp_list is not None else int(math.ceil(gbp_price))

    print(f"seller_sku={seller_sku} → UK itemNum={mx_item_num(seller_sku)}")
    print(f"collectBoxDetailId={collect_box_detail_id} UK shopId={UK_SHOP_ID}")
    print(f"GBP list(ceil)={gbp_list_price} sale={gbp_price} (POP/manual)")
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
            return UK_EXIT_VOLUMETRIC_NEEDS_CONFIRM

    ensure_mx_claimed(collect_box_detail_id, mx_shop_id=UK_SHOP_ID)

    rd = post_open(GET_PATH, {"detailId": collect_box_detail_id, "shopId": UK_SHOP_ID})
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
        print(f"⚠ 母版 API 拉取失败，使用采集箱文案: {exc}")
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

    apply_mx_shop_collect_info(
        info,
        seller_sku=seller_sku,
        mxn_list_price=gbp_list_price,
        mxn_sale_price=gbp_price,
        package_length=pl,
        package_width=pw,
        package_height=ph,
        good_image_urls=good_urls,
        stock=stock,
        weight_kg=weight_kg,
        mx_shop_id=UK_SHOP_ID,
    )

    info["notes"] = clean_notes(info.get("notes") or "", good_urls)
    title = info.get("title") or ""
    if master_product and master_product.get("title"):
        info["title"] = str(master_product["title"])[:255]
    elif len(title) > 255:
        info["title"] = title[:255].rstrip()
    print(f"UK title: {(info.get('title') or '')[:120]}")

    sr = post_open(
        SAVE_PATH,
        {
            "ossMd5": oss_md5,
            "detailId": collect_box_detail_id,
            "shopId": UK_SHOP_ID,
            "shopCollectItemInfo": info,
        },
    )
    print("\nSave:", json.dumps(sr, ensure_ascii=False, indent=2))
    if sr.get("result") != "success":
        return 2

    rd2 = post_open(GET_PATH, {"detailId": collect_box_detail_id, "shopId": UK_SHOP_ID})
    info2 = (rd2.get("data") or {}).get("shopCollectItemInfo") or {}
    print("\nSaved itemNum:", mx_item_num(seller_sku))
    print("Saved skuMap:", json.dumps(info2.get("skuMap"), ensure_ascii=False))

    if not publish:
        return 0

    if not skip_user_confirm:
        try:
            assert_user_approved(confirm_token)
        except UserConfirmRequired as exc:
            print(f"\n⚠ 需先在 Web 确认上架:\n  {exc}")
            return UK_EXIT_NEEDS_USER_CONFIRM

    pr = post_open(
        PUBLISH_PATH,
        {"detailIds": [collect_box_detail_id], "shopIds": [UK_SHOP_ID]},
    )
    print("\nPublish:", json.dumps(pr, ensure_ascii=False, indent=2))
    return 0 if pr.get("result") == "success" else 3


def publish_uk_multi_listing(
    *,
    collect_box_detail_id: int,
    ph_product_id: str,
    variant_writes: list,
    publish: bool = True,
    stock: int | None = None,
    master_region: str = "PH",
    package_cm: tuple[int, int, int] | None = None,
    confirm_token: str | None = None,
    skip_user_confirm: bool = False,
) -> int:
    """同链接多规格 UK 上架：每个 skuMap 行独立货号与 ceil 原价 GBP。"""
    from modules.miaoshou.mx_migrate import (
        apply_mx_multi_shop_collect_info,
        clean_notes,
        fetch_site_collect_info,
        index_sku_map_keys_by_match_key,
        is_ok_image,
    )
    from modules.miaoshou.uk_confirm import UserConfirmRequired, get_group_confirm

    if not variant_writes:
        raise RuntimeError("variant_writes 不能为空")

    init_db()
    mks = [v.match_key for v in variant_writes]
    print(f"UK multi listing · match_keys={','.join(mks)}")
    print(f"collectBoxDetailId={collect_box_detail_id} UK shopId={UK_SHOP_ID}")

    ensure_mx_claimed(collect_box_detail_id, mx_shop_id=UK_SHOP_ID)
    good_urls, master_product = collect_master_images_and_product(ph_product_id, region=master_region)
    print(f"{master_region} images: {len(good_urls)}")

    site_info = fetch_site_collect_info(collect_box_detail_id, site="MY") or {}
    sku_map_key_by_match_key = index_sku_map_keys_by_match_key(
        site_collect_info=site_info,
        tiktok_product=master_product,
    )
    missing = [v.match_key for v in variant_writes if v.match_key not in sku_map_key_by_match_key]
    if missing:
        raise RuntimeError(f"skuMap 未匹配对齐码: {', '.join(missing)}")

    rd = post_open(GET_PATH, {"detailId": collect_box_detail_id, "shopId": UK_SHOP_ID})
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
                val["imgUrl"] = good_urls[0] if good_urls else ""

    apply_mx_multi_shop_collect_info(
        info,
        variants=variant_writes,
        package_length=pl,
        package_width=pw,
        package_height=ph,
        sku_map_key_by_match_key=sku_map_key_by_match_key,
        good_image_urls=good_urls,
        stock=stock,
        mx_shop_id=UK_SHOP_ID,
    )

    info["notes"] = clean_notes(info.get("notes") or "", good_urls)
    if master_product and master_product.get("title"):
        info["title"] = str(master_product["title"])[:255]
    print(f"UK title: {(info.get('title') or '')[:120]}")

    sr = post_open(
        SAVE_PATH,
        {
            "ossMd5": oss_md5,
            "detailId": collect_box_detail_id,
            "shopId": UK_SHOP_ID,
            "shopCollectItemInfo": info,
        },
    )
    print("\nSave:", json.dumps(sr, ensure_ascii=False, indent=2))
    if sr.get("result") != "success":
        return 2

    if not publish:
        return 0

    if not skip_user_confirm:
        group = get_group_confirm(confirm_token) if confirm_token else None
        if not group or group.status != "approved":
            raise UserConfirmRequired(f"整组确认单未通过（token={confirm_token}）")

    pr = post_open(
        PUBLISH_PATH,
        {"detailIds": [collect_box_detail_id], "shopIds": [UK_SHOP_ID]},
    )
    print("\nPublish:", json.dumps(pr, ensure_ascii=False, indent=2))
    return 0 if pr.get("result") == "success" else 3
