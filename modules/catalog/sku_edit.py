"""各平台商家 SKU 本地维护，可选推送到 TikTok / Shopee。"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from core.db import connect, init_db
from modules.catalog.sku_key import parse_search_key, shopee_match_key, tk_match_key, tk_region
from modules.ozon.config import ozon_data_dir


def _tk_row_dict(r, region: str | None = None) -> dict:
    reg = region or tk_region(r["shop_cipher"])
    return {
        "platform": "tiktok",
        "region": reg,
        "seller_sku": (r["seller_sku"] or "").strip(),
        "sku_id": r["sku_id"] or "",
        "shop_cipher": r["shop_cipher"] or "",
        "product_id": r["product_id"] or "",
        "global_product_id": (r["global_product_id"] or "").strip(),
        "global_sku_id": (r["global_sku_id"] or "").strip(),
        "product_name": (r["product_name"] or "").strip(),
        "model_name": (r["sku_name"] or "").strip(),
        "can_push": bool((r["product_id"] or "").strip()),
    }


def _tk_rows_for_key(conn, key: str) -> list[dict]:
    rows = conn.execute(
        """SELECT sku_id, shop_cipher, product_id, seller_sku, product_name, sku_name,
                  global_product_id, global_sku_id, status
           FROM products WHERE status = 'ACTIVATE'"""
    ).fetchall()
    matched: list[dict] = []
    gsids: set[str] = set()
    for r in rows:
        sk = (r["seller_sku"] or "").strip()
        if sk and tk_match_key(sk) == key:
            matched.append(dict(r))
            gsid = (r["global_sku_id"] or "").strip()
            if gsid:
                gsids.add(gsid)
    if gsids:
        for r in rows:
            sk = (r["seller_sku"] or "").strip()
            if sk:
                continue
            gsid = (r["global_sku_id"] or "").strip()
            if gsid and gsid in gsids:
                matched.append(dict(r))
    seen = set()
    out: list[dict] = []
    for r in matched:
        uid = (r["sku_id"], r["shop_cipher"])
        if uid in seen:
            continue
        seen.add(uid)
        out.append(_tk_row_dict(r))
    return sorted(out, key=lambda x: (x["region"], x["sku_id"]))


def _shopee_row_dict(r) -> dict:
    reg = (r["region"] or "?").upper()
    sk = (r["seller_sku"] or "").strip()
    return {
        "platform": "shopee",
        "region": reg,
        "seller_sku": sk,
        "model_id": r["model_id"] or "",
        "shop_id": int(r["shop_id"]),
        "product_id": r["item_id"] or "",
        "product_name": (r["product_name"] or "").strip(),
        "model_name": (r["model_name"] or "").strip(),
        "can_push": True,
        "sku_label": "规格货号",
    }


def _shopee_rows_for_key(conn, key: str) -> list[dict]:
    out: list[dict] = []
    seen_regions: set[str] = set()
    for r in conn.execute(
        """SELECT model_id, shop_id, region, item_id, seller_sku, product_name, model_name, status
           FROM shopee_products"""
    ):
        sk = (r["seller_sku"] or "").strip()
        if not sk or shopee_match_key(sk) != key:
            continue
        out.append(_shopee_row_dict(r))
        seen_regions.add((r["region"] or "?").upper())
    from modules.shopee.global_sku_map import rows_for_match_key

    for row in rows_for_match_key(key):
        reg = row.get("region", "?").upper()
        if reg in seen_regions:
            continue
        out.append(row)
    return sorted(out, key=lambda x: (x["region"], x.get("model_id") or ""))


def find_shopee_rows(query: str, *, live: bool = True) -> dict:
    """按规格货号片段 / item_id / 规格名查找 Shopee 行（含未对齐商品）。"""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "请输入查询条件", "rows": []}

    init_db()
    conn = connect()
    like = f"%{q}%"
    seen: set[tuple] = set()
    rows: list[dict] = []

    def _add(r) -> None:
        uid = (r["model_id"], int(r["shop_id"]))
        if uid in seen:
            return
        seen.add(uid)
        rows.append(_shopee_row_dict(r))

    for r in conn.execute(
        """SELECT model_id, shop_id, region, item_id, seller_sku, product_name, model_name
           FROM shopee_products
           WHERE seller_sku LIKE ? OR item_id LIKE ? OR model_name LIKE ? OR product_name LIKE ?
           ORDER BY region, model_id LIMIT 50""",
        (like, like, like, like),
    ):
        _add(r)

    prefix = q.split("_", 1)[0].strip()
    if prefix and prefix != q:
        for r in conn.execute(
            """SELECT model_id, shop_id, region, item_id, seller_sku, product_name, model_name
               FROM shopee_products WHERE seller_sku LIKE ? LIMIT 20""",
            (prefix + "%",),
        ):
            _add(r)

    conn.close()

    digits = re.sub(r"\D", "", q)
    if live and digits and len(digits) >= 8:
        from modules.shopee.publish import ensure_shop_token
        from modules.shopee.client import shop_get
        from modules.shopee.shops import sync_shop_ids

        for item_id in {int(digits), int(digits[-11:]) if len(digits) > 11 else int(digits)}:
            for _reg, sid in sync_shop_ids().items():
                shop_id = int(sid)
                try:
                    tok = ensure_shop_token(shop_id)
                    resp = shop_get(
                        "/api/v2/product/get_model_list", shop_id, tok, {"item_id": item_id}
                    )
                    if resp.get("error") and resp.get("error") != "-":
                        continue
                    base = shop_get(
                        "/api/v2/product/get_item_base_info",
                        shop_id,
                        tok,
                        {"item_id_list": str(item_id)},
                    )
                    items = (base.get("response") or {}).get("item_list") or []
                    pname = (items[0].get("item_name") or "") if items else ""
                    for m in (resp.get("response") or {}).get("model") or []:
                        sk = (m.get("model_sku") or "").strip()
                        mid = str(m.get("model_id") or "")
                        if not mid:
                            continue
                        uid = (mid, shop_id)
                        if uid in seen:
                            continue
                        seen.add(uid)
                        rows.append({
                            "platform": "shopee",
                            "region": _reg,
                            "seller_sku": sk,
                            "model_id": mid,
                            "shop_id": shop_id,
                            "product_id": str(item_id),
                            "product_name": pname,
                            "model_name": (m.get("model_name") or "").strip(),
                            "can_push": True,
                            "sku_label": "规格货号",
                            "live": True,
                        })
                except Exception:
                    continue

    return {"ok": True, "query": q, "rows": rows}


def _ozon_row_for_key(key: str) -> dict | None:
    from modules.catalog.ozon_data import load_ozon_by_key

    oz = load_ozon_by_key().get(key)
    if not oz:
        return None
    return {
        "platform": "ozon",
        "region": "RU",
        "seller_sku": (oz.get("seller_sku") or "").strip(),
        "offer_id": oz.get("offer_id") or "",
        "product_id": oz.get("product_id") or "",
        "product_name": (oz.get("product_name") or "").strip(),
        "migrated": bool(oz.get("migrated")),
        "can_push": False,
        "note": "写入 tk_sku_map.json（Ozon 本地映射）",
    }


def get_edit_rows(match_key: str = "", sku_id: str = "") -> dict:
    key = parse_search_key(match_key) if match_key else ""
    init_db()
    conn = connect()
    rows: list[dict] = []
    if key:
        rows = _tk_rows_for_key(conn, key) + _shopee_rows_for_key(conn, key)
    if sku_id:
        r = conn.execute(
            """SELECT sku_id, shop_cipher, product_id, seller_sku, product_name, sku_name,
                      global_product_id, global_sku_id, status
               FROM products WHERE sku_id = ? LIMIT 1""",
            (sku_id.strip(),),
        ).fetchone()
        if r:
            tk_row = _tk_row_dict(r)
            if not any(x.get("sku_id") == tk_row["sku_id"] for x in rows):
                rows.insert(0, tk_row)
    conn.close()
    if key:
        oz = _ozon_row_for_key(key)
        if oz:
            rows.append(oz)
    if not key and not rows:
        return {"ok": False, "error": "无效对齐码或未找到 SKU", "match_key": "", "rows": []}
    return {"ok": True, "match_key": key, "rows": rows}


def _save_tk_local(sku_id: str, shop_cipher: str, seller_sku: str) -> None:
    init_db()
    conn = connect()
    conn.execute(
        """UPDATE products SET seller_sku = ?, updated_at = ?
           WHERE sku_id = ? AND shop_cipher = ?""",
        (seller_sku, int(time.time()), sku_id, shop_cipher),
    )
    if conn.total_changes == 0:
        conn.close()
        raise ValueError(f"未找到 TK SKU {sku_id}")
    conn.commit()
    conn.close()


def _leaf_category(detail: dict) -> tuple[str, str]:
    chains = detail.get("category_chains") or []
    if chains:
        leaf = chains[-1]
        return str(leaf.get("id") or ""), str(leaf.get("category_version") or "v2")
    cat = detail.get("category") or {}
    return str(cat.get("id") or ""), str(cat.get("category_version") or "v1")


def _build_local_product_edit_body(detail: dict, sku_id: str, seller_sku: str) -> dict:
    cat_id, cat_ver = _leaf_category(detail)
    skus: list[dict] = []
    for s in detail.get("skus") or []:
        sid = str(s.get("id") or "")
        item: dict = {
            "id": sid,
            "seller_sku": seller_sku if sid == str(sku_id) else (s.get("seller_sku") or ""),
        }
        attrs = []
        for a in s.get("sales_attributes") or []:
            sa: dict = {
                "id": a["id"],
                "value_id": a.get("value_id"),
                "value_name": a.get("value_name"),
                "name": a.get("name"),
            }
            uri = (a.get("sku_img") or {}).get("uri")
            if uri:
                sa["sku_img"] = {"uri": uri}
            attrs.append(sa)
        if attrs:
            item["sales_attributes"] = attrs
        price = s.get("price") or {}
        amount = price.get("sale_price") or price.get("amount")
        currency = price.get("currency")
        if amount and currency:
            item["price"] = {"amount": str(amount), "currency": currency}
        inv = s.get("inventory") or []
        if inv:
            item["inventory"] = [
                {
                    "warehouse_id": i.get("warehouse_id"),
                    "quantity": i.get("quantity"),
                }
                for i in inv
                if i.get("warehouse_id") is not None
            ]
        skus.append(item)

    body: dict = {
        "title": detail["title"],
        "description": detail.get("description") or "<p></p>",
        "category_id": cat_id,
        "category_version": cat_ver,
        "main_images": [
            {"uri": img["uri"]} for img in detail.get("main_images") or [] if img.get("uri")
        ],
        "skus": skus,
    }
    if detail.get("package_weight"):
        body["package_weight"] = detail["package_weight"]
    if detail.get("package_dimensions"):
        body["package_dimensions"] = detail["package_dimensions"]
    if detail.get("product_attributes"):
        body["product_attributes"] = [
            {"id": a["id"], "values": a.get("values") or []}
            for a in detail["product_attributes"]
        ]
    if detail.get("manufacturer_ids"):
        body["manufacturer_ids"] = detail["manufacturer_ids"]
    if detail.get("responsible_person_ids"):
        body["responsible_person_ids"] = detail["responsible_person_ids"]
    return body


def _push_tk_local(
    shop_cipher: str,
    product_id: str,
    sku_id: str,
    seller_sku: str,
) -> tuple[bool, str]:
    from core import auth
    from core.api_client import get as api_get, put as api_put

    token = auth.access_token()
    detail = api_get(
        f"/product/202309/products/{product_id}",
        token,
        {"shop_cipher": shop_cipher},
    ).get("data") or {}
    if not detail:
        return False, "无法获取 TikTok 商品详情"
    body = _build_local_product_edit_body(detail, sku_id, seller_sku)
    resp = api_put(
        f"/product/202309/products/{product_id}",
        token,
        {"shop_cipher": shop_cipher},
        body,
    )
    if resp.get("code") == 0:
        return True, ""
    return False, str(resp.get("message") or resp.get("code") or resp)[:300]


def _save_shopee_local(model_id: str, shop_id: int, seller_sku: str) -> None:
    init_db()
    conn = connect()
    conn.execute(
        """UPDATE shopee_products SET seller_sku = ?, updated_at = ?
           WHERE model_id = ? AND shop_id = ?""",
        (seller_sku, int(time.time()), model_id, shop_id),
    )
    if conn.total_changes == 0:
        conn.close()
        raise ValueError(f"未找到 Shopee model {model_id}")
    conn.commit()
    conn.close()


def _push_shopee_model(shop_id: int, item_id: str, model_id: str, model_sku: str) -> tuple[bool, str, str]:
    """推送规格货号；返回 (ok, error_msg, push_via)。"""
    from modules.shopee.auth import ensure_shop_token
    from modules.shopee.client import shop_post
    from modules.shopee.publish import _shop_meta

    token = ensure_shop_token(shop_id)
    meta = _shop_meta(shop_id, token)
    merchant_id = int(meta.get("merchant_id") or 0)
    if merchant_id:
        ok, msg, via = _push_shopee_cnsc_global(
            shop_id, item_id, model_id, model_sku, token, merchant_id
        )
        if ok:
            return True, "", via
        if msg and "非 CNSC" not in msg:
            return False, msg, ""

    body = {
        "item_id": int(item_id),
        "model": [{"model_id": int(model_id), "model_sku": model_sku}],
    }
    resp = shop_post("/api/v2/product/update_model", shop_id, token, body)
    err = (resp.get("error") or "").strip()
    if not err or err == "-":
        return True, "", "shop"
    return False, str(resp.get("message") or err), ""


def _push_shopee_cnsc_global(
    shop_id: int,
    item_id: str,
    model_id: str,
    model_sku: str,
    shop_token: str,
    merchant_id: int,
) -> tuple[bool, str, str]:
    """CNSC 跨境店：改全球商品 Global SKU（merchant token + global_model_id）。"""
    from modules.shopee.auth import ensure_merchant_token
    from modules.shopee.client import merchant_get, merchant_post, shop_get
    from modules.shopee.global_sku_map import (
        global_item_id_for_shop_item,
        update_global_model_sku_in_map,
    )
    from modules.catalog.sku_key import shopee_match_key

    mtoken = ensure_merchant_token(merchant_id, shop_id=shop_id)
    gid = global_item_id_for_shop_item(shop_id=shop_id, item_id=item_id, model_id=model_id)
    if not gid:
        try:
            from modules.shopee.client import resolve_global_item_id

            resolved = resolve_global_item_id(shop_id, merchant_id, mtoken, item_id)
            gid = str(resolved) if resolved else ""
        except Exception as exc:
            return False, str(exc), ""
    if not gid:
        return False, "未找到全球商品 global_item_id（请确认该商品已发布到 CNSC 全球）", ""

    g_resp = merchant_get(
        "/api/v2/global_product/get_global_model_list",
        merchant_id,
        mtoken,
        {"global_item_id": int(gid)},
    )
    g_err = (g_resp.get("error") or "").strip()
    if g_err and g_err != "-":
        return False, str(g_resp.get("message") or g_err), ""
    global_models = (g_resp.get("response") or {}).get("global_model") or []
    if not global_models:
        return False, "全球商品无规格，请先在 CNSC 后台确认", ""

    global_model_id: int | None = None
    if len(global_models) == 1:
        global_model_id = int(global_models[0]["global_model_id"])
    else:
        tier_index = [0]
        try:
            sm_resp = shop_get(
                "/api/v2/product/get_model_list",
                shop_id,
                shop_token,
                {"item_id": int(item_id)},
            )
            shop_models = (sm_resp.get("response") or {}).get("model") or []
            for m in shop_models:
                if str(m.get("model_id")) == str(model_id):
                    tier_index = m.get("tier_index") or [0]
                    break
        except Exception:
            shop_models = []
        for gm in global_models:
            if (gm.get("tier_index") or [0]) == tier_index:
                global_model_id = int(gm["global_model_id"])
                break
        if global_model_id is None:
            global_model_id = int(global_models[0]["global_model_id"])

    bodies = [
        {
            "global_item_id": int(gid),
            "global_model": [
                {"global_model_id": global_model_id, "global_model_sku": model_sku}
            ],
        },
        {
            "global_item_id": int(gid),
            "model_list": [
                {"global_model_id": global_model_id, "global_model_sku": model_sku}
            ],
        },
    ]
    last_msg = ""
    for body in bodies:
        try:
            u_resp = merchant_post(
                "/api/v2/global_product/update_global_model",
                merchant_id,
                mtoken,
                body,
            )
        except Exception as exc:
            last_msg = str(exc)
            continue
        u_err = (u_resp.get("error") or "").strip()
        if not u_err or u_err == "-":
            mk = shopee_match_key(model_sku)
            update_global_model_sku_in_map(str(gid), model_sku, match_key=mk or "")
            return True, "", "cnsc_global"
        last_msg = str(u_resp.get("message") or u_err or u_resp)

    return False, last_msg or "全球规格货号更新失败", ""


def _find_ozon_map_key(data: dict, match_key: str) -> str | None:
    for k, row in data.items():
        if not isinstance(row, dict):
            continue
        sk = str(row.get("seller_sku") or "").strip()
        if tk_match_key(sk) == match_key or str(k).zfill(4) == match_key:
            return str(k)
    return None


def _save_ozon_local(match_key: str, seller_sku: str) -> None:
    base = ozon_data_dir()
    if not base:
        raise ValueError("未配置 feishu.ozon_data_dir")
    path = Path(base) / "tk_sku_map.json"
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"tk_sku_map.json 解析失败: {e}") from e
    map_key = _find_ozon_map_key(data, match_key)
    if map_key is None:
        map_key = match_key.lstrip("0") or match_key
        data[map_key] = {"seller_sku": seller_sku, "title": "", "image_urls": []}
    else:
        entry = data.setdefault(map_key, {})
        if not isinstance(entry, dict):
            entry = {"seller_sku": seller_sku}
            data[map_key] = entry
        else:
            entry["seller_sku"] = seller_sku
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_seller_sku(
    platform: str,
    seller_sku: str,
    *,
    push: bool = False,
    **ids,
) -> dict:
    """保存商家 SKU；push=True 时尝试推送到平台 API。"""
    plat = (platform or "").strip().lower()
    sku = (seller_sku or "").strip()
    if not sku:
        raise ValueError("seller_sku 不能为空")

    result: dict = {"platform": plat, "seller_sku": sku, "local_saved": True, "pushed": False, "push_error": ""}

    if plat == "tiktok":
        sku_id = str(ids.get("sku_id") or "").strip()
        shop_cipher = str(ids.get("shop_cipher") or "").strip()
        if not sku_id or not shop_cipher:
            raise ValueError("TikTok 需要 sku_id 与 shop_cipher")
        _save_tk_local(sku_id, shop_cipher, sku)
        if push:
            product_id = str(ids.get("product_id") or "").strip()
            if not product_id:
                init_db()
                conn = connect()
                row = conn.execute(
                    """SELECT product_id FROM products
                       WHERE sku_id = ? AND shop_cipher = ?""",
                    (sku_id, shop_cipher),
                ).fetchone()
                conn.close()
                if row:
                    product_id = (row["product_id"] or "").strip()
            if product_id:
                ok, msg = _push_tk_local(shop_cipher, product_id, sku_id, sku)
                result["pushed"] = ok
                if not ok:
                    result["push_error"] = msg
            else:
                result["push_error"] = "无 product_id，仅已保存本地库"
        return result

    if plat == "shopee":
        model_id = str(ids.get("model_id") or "").strip()
        shop_id = int(ids.get("shop_id") or 0)
        item_id = str(ids.get("product_id") or ids.get("item_id") or "").strip()
        if not model_id or not shop_id:
            raise ValueError("Shopee 需要 model_id 与 shop_id")
        _save_shopee_local(model_id, shop_id, sku)
        if push:
            if not item_id:
                init_db()
                conn = connect()
                row = conn.execute(
                    "SELECT item_id FROM shopee_products WHERE model_id = ? AND shop_id = ?",
                    (model_id, shop_id),
                ).fetchone()
                conn.close()
                item_id = (row["item_id"] or "").strip() if row else ""
            if item_id:
                ok, msg, via = _push_shopee_model(shop_id, item_id, model_id, sku)
                result["pushed"] = ok
                if via:
                    result["push_via"] = via
                if not ok:
                    result["push_error"] = msg
            else:
                result["push_error"] = "无 item_id，仅已保存本地库"
        return result

    if plat == "ozon":
        match_key = parse_search_key(str(ids.get("match_key") or ""))
        if not match_key:
            raise ValueError("Ozon 需要 match_key")
        _save_ozon_local(match_key, sku)
        result["note"] = "已更新 tk_sku_map.json"
        return result

    raise ValueError(f"未知平台: {platform}")
