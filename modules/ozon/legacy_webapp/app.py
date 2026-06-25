import json
import os
import sys
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, render_template

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")


def _ensure_tiktok_root() -> str:
    env = os.environ.get("TIKTOK_E_COMM_ROOT", "").strip()
    if env and os.path.isdir(env):
        root = os.path.abspath(env)
    else:
        # legacy_webapp 现已内嵌于 tiktok_e_comm/modules/ozon/legacy_webapp，向上 3 层即仓库根目录
        root = os.path.abspath(os.path.join(BASE_DIR, "..", "..", ".."))
    if root not in sys.path:
        sys.path.insert(0, root)
    return root


_TIKTOK_ROOT = _ensure_tiktok_root()

from core import auth as tk_auth  # noqa: E402
from core.api_client import get as tk_get  # noqa: E402

import translate
import deepseek_draft
from img_to_34 import download, to_3x4, upload as upload_image

MYR_CNY_RATE = 1.55  # 1 MYR = 1.55 CNY


def _ozon_credentials() -> tuple[str, str]:
    cid = os.environ.get("OZON_CLIENT_ID", "").strip()
    key = os.environ.get("OZON_API_KEY", "").strip()
    if cid and key:
        return cid, key
    try:
        from modules.ozon.config import ozon_credentials

        return ozon_credentials()
    except Exception:
        pass
    cred_path = os.path.join(DATA_DIR, "credentials.local.json")
    if os.path.isfile(cred_path):
        with open(cred_path, encoding="utf-8") as f:
            d = json.load(f)
        return str(d.get("client_id") or ""), str(d.get("api_key") or "")
    return "", ""


CLIENT_ID, API_KEY = _ozon_credentials()

from modules.ozon.catalog_source import to_4digit_offer_id  # noqa: E402
TMP_DIR = "/tmp/ozon_webapp"
os.makedirs(TMP_DIR, exist_ok=True)

MIGRATED_PATH = os.path.join(DATA_DIR, "migrated_offers.json")
LAST_RED_PATH = os.path.join(DATA_DIR, "last_red_offers.json")
TK_MAP_PATH = os.path.join(DATA_DIR, "tk_sku_map.json")
EXISTING_ATTRS_PATH = os.path.join(DATA_DIR, "all_products_attrs.json")
PRICE_LOG_PATH = os.path.join(DATA_DIR, "price_update_log.json")
MIGRATE_LOG_PATH = os.path.join(DATA_DIR, "migrate_log.json")
PENDING_REVIEW_PATH = os.path.join(DATA_DIR, "pending_price_review.json")
DAILY_SUMMARY_PATH = os.path.join(DATA_DIR, "daily_summary.json")
PENDING_PROMO_PATH = os.path.join(DATA_DIR, "pending_promo_review.json")
PROMO_LOG_PATH = os.path.join(DATA_DIR, "promo_log.json")
PRICE_SUPPRESS_PATH = os.path.join(DATA_DIR, "price_suppress.json")
CATEGORY_SUGGESTIONS_PATH = os.path.join(DATA_DIR, "category_suggestions.json")
OFFER_PRICES_CACHE_PATH = os.path.join(DATA_DIR, "offer_prices_cache.json")
CATEGORY_OPTIONS_PATH = os.path.join(DATA_DIR, "category_options.json")

PRICE_SUPPRESS_DAYS = 14

ELASTIC_BOOST_ACTION_ID = 1977747

ANALYTICS_METRICS = [
    "hits_view_search", "hits_view_pdp", "hits_view",
    "hits_tocart_search", "hits_tocart_pdp",
    "session_view_search", "session_view_pdp",
    "conv_tocart_pdp", "revenue", "ordered_units",
]

app = Flask(__name__)


def ozon_post(path, body):
    url = "https://api-seller.ozon.ru" + path
    cmd = [
        "curl", "-s", "--noproxy", "*", "-X", "POST", url,
        "-H", "Client-Id: " + CLIENT_ID,
        "-H", "Api-Key: " + API_KEY,
        "-H", "Content-Type: application/json",
        "-d", json.dumps(body),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return json.loads(out)


def ozon_get(path):
    url = "https://api-seller.ozon.ru" + path
    cmd = [
        "curl", "-s", "--noproxy", "*", "-X", "GET", url,
        "-H", "Client-Id: " + CLIENT_ID,
        "-H", "Api-Key: " + API_KEY,
        "-H", "Content-Type: application/json",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return json.loads(out)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def attr(id_, val, dict_id=0):
    return {"complex_id": 0, "id": id_, "values": [{"dictionary_value_id": dict_id, "value": val}]}


_DICT_CACHE = {}


def _lookup_dict_id(attribute_id, value, fallback, category_id=17027906, type_id=91971):
    """返回 (dict_id, canonical_value, matched)。matched=False 表示未在 Ozon 字典中找到匹配，
    dict_id/canonical_value 此时为调用方传入的 fallback 值，调用方应自行替换为已知合法组合。"""
    cache_key = (attribute_id, value)
    if cache_key in _DICT_CACHE:
        return _DICT_CACHE[cache_key]
    # Ozon 字典统一用 "е"，俄语文本里常见的 "ё" 会导致搜索无匹配（如 "зелёный"→无结果，需查"зеленый"）
    search_value = value.replace("ё", "е").replace("Ё", "Е")
    try:
        r = ozon_post("/v1/description-category/attribute/values/search", {
            "attribute_id": attribute_id,
            "description_category_id": category_id,
            "type_id": type_id,
            "language": "DEFAULT",
            "limit": 5,
            "value": search_value,
        })
        results = r.get("result", [])
        if results:
            dict_id = results[0]["id"]
            canonical = results[0].get("value", value)
            matched = True
        else:
            dict_id = fallback
            canonical = value
            matched = False
    except Exception:
        dict_id = fallback
        canonical = value
        matched = False
    _DICT_CACHE[cache_key] = (dict_id, canonical, matched)
    return (dict_id, canonical, matched)


def lookup_color(color_name, category_id=17027906, type_id=91971):
    """返回 (dict_id, canonical_value)"""
    dict_id, canonical, matched = _lookup_dict_id(10096, color_name, 61571, category_id, type_id)
    if not matched:
        # 未在 Ozon 字典中找到匹配，使用已验证的 "белый"/61571 组合，避免 value 与 dict_id 不一致
        return (61571, "белый")
    return (dict_id, canonical)


_MATERIAL_FALLBACK_CANONICAL = "ПВХ (поливинилхлорид)"

_MATERIAL_KNOWN = {
    "ПВХ (поливинилхлорид)": 61996,
    "Полиэстер": 62040,
    "Нетканый материал (спанбонд)": 61999,
    "Хлопок": 62005,
    "Акрил": 62000,
    "Силикон": 62043,
    "Бумага": 62002,
    "Стекло": 62006,
    "Металл": 62009,
    "Дерево": 62007,
    "Резина": 62042,
    "Вискоза": 62003,
    "ПВХ": 61996,  # 缩写自动映射到全称
}
_MATERIAL_CANONICAL_MAP = {
    "ПВХ": "ПВХ (поливинилхлорид)",
}


def lookup_material(material_name, category_id=17027906, type_id=91971):
    """返回 (dict_id, canonical_value)"""
    # 先查本地已知映射，避免 Ozon 搜索返回错误 dict_id
    canonical = _MATERIAL_CANONICAL_MAP.get(material_name, material_name)
    if canonical in _MATERIAL_KNOWN:
        return (_MATERIAL_KNOWN[canonical], canonical)
    dict_id, found_canonical, matched = _lookup_dict_id(6383, material_name, 61996, category_id, type_id)
    if not matched:
        # 未在 Ozon 字典中找到匹配，fallback 到已验证的 "ПВХ (поливинилхлорид)" 组合
        return (61996, _MATERIAL_FALLBACK_CANONICAL)
    return (dict_id, found_canonical)


def _deepseek_json(prompt, temperature=1.0):
    key = deepseek_draft.deepseek_api_key()
    if not key:
        raise RuntimeError("未配置 DeepSeek API Key（tiktok settings.json ai.api_key 或 data/config.json）")
    body = {"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}, "temperature": temperature}
    cmd = ["curl", "-s", "--noproxy", "*", "-m", "120", "https://api.deepseek.com/chat/completions",
           "-H", "Authorization: Bearer " + key, "-H", "Content-Type: application/json",
           "-d", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return json.loads(json.loads(out)["choices"][0]["message"]["content"])


def build_rich_json(title, gen, images):
    """构造 Ozon Rich-контент JSON。padding 必须是 'type2'(无下划线)，否则被异步清空。"""
    blocks = [{
        "widgetName": "raShowcase", "type": "billboard",
        "blocks": [{"imgLink": "", "img": {"src": images[0], "srcMobile": images[0], "alt": title[:60],
                    "position": "width_full", "positionMobile": "width_full",
                    "widthMobile": 1478, "heightMobile": 1970}}],
    }, {
        "widgetName": "raTextBlock", "theme": "default", "padding": "type2", "gapSize": "m",
        "text": {"size": "size4", "align": "left", "color": "color1", "content": [gen["usage"]]},
        "title": {"content": [title[:80]], "size": "size2", "align": "left", "color": "color1"},
    }]
    for adv in gen["advantages"]:
        blocks.append({
            "widgetName": "raTextBlock", "theme": "default", "padding": "type2", "gapSize": "s",
            "text": {"size": "size4", "align": "left", "color": "color1", "content": [adv["text"]]},
            "title": {"content": [adv["title"]], "size": "size3", "align": "left", "color": "color1"},
        })
    if len(images) > 1:
        blocks.append({
            "widgetName": "raShowcase", "type": "roll",
            "blocks": [{"imgLink": "", "img": {"src": u, "srcMobile": u, "alt": title[:60],
                        "position": "width_full", "positionMobile": "width_full"}} for u in images[1:4]],
        })
    return {"content": blocks, "version": 0.3}


def add_rich_content(offer_id, title, description):
    """为已导入的商品生成并提交 Rich-контент。等图片转为 Ozon 自有域名后再用其作图源。
    返回状态字符串(imported/no_images/error...)。"""
    try:
        images = []
        for _ in range(8):  # 等 Ozon 处理图片(最长~2分钟)，拿到自有域名图片
            r = ozon_post("/v3/product/info/list", {"offer_id": [offer_id]})
            items = r.get("items", [])
            imgs = items[0].get("images", []) if items else []
            if imgs and any(("ozone.ru" in u or "ozonstatic" in u) for u in imgs):
                images = imgs
                break
            time.sleep(15)
        if not images:
            return "no_images"

        gen = _deepseek_json(
            f"""为Ozon家居装饰品商品卡片生成Rich内容。
标题: {title}
描述: {(description or '')[:800]}
返回JSON: "advantages"(3个对象{{"title":"...","text":"..."}}，标题≤40字符，正文≤120字符) 和 "usage"(一段使用方法，≤250字符)。
只返回JSON，俄语，避免模板化套话。""")
        rich = build_rich_json(title, gen, images)
        upd = ozon_post("/v1/product/attributes/update", {"items": [{
            "offer_id": offer_id,
            "attributes": [{"complex_id": 0, "id": 11254, "values": [{"value": json.dumps(rich, ensure_ascii=False)}]}],
        }]})
        task_id = upd.get("task_id")
        status = "submitted"
        if task_id:
            for _ in range(8):
                time.sleep(2)
                info = ozon_post("/v1/product/import/info", {"task_id": task_id})
                its = info.get("result", {}).get("items", [])
                if its and its[0]["status"] != "pending":
                    status = its[0]["status"]
                    break
        return status
    except Exception as e:
        return f"error: {e}"


# ---------------------------------------------------------------- 上品 ----

@app.route("/api/unmigrated")
def api_unmigrated():
    tk_map = load_json(TK_MAP_PATH, {})
    migrated = set(load_json(MIGRATED_PATH, []))
    existing = load_json(EXISTING_ATTRS_PATH, {"result": []})
    existing_offers = {it["offer_id"] for it in existing["result"]}

    # 已上线/已搬运商品占用的 tk_id（同一 tk_id = 同一商品，Ozon 禁止重复铺货）
    used_tk = set()
    for v in tk_map.values():
        if v["seller_sku"] in migrated or v["seller_sku"] in existing_offers:
            used_tk.add(v.get("tk_id"))

    items = []
    seen_offers = set()
    seen_tk = set()
    for k, v in tk_map.items():
        offer_id = v["seller_sku"]
        tk_id = v.get("tk_id")
        if offer_id in migrated or offer_id in existing_offers or offer_id in seen_offers:
            continue
        # 跳过 tk_id 重复的（已被占用，或本列表内已出现过）——避免搬运重复品被 Ozon 判 SPU_ALREADY_EXISTS
        if tk_id in used_tk or tk_id in seen_tk:
            continue
        seen_offers.add(offer_id)
        seen_tk.add(tk_id)
        items.append({
            "offer_id": to_4digit_offer_id(offer_id),
            "seller_sku": offer_id,  # 保留原始seller_sku供draft接口查找
            "tk_id": tk_id,
            "title": v["title"],
            "image": v["image_urls"][0] if v["image_urls"] else "",
            "image_count": len(v["image_urls"]),
        })
    items.sort(key=lambda x: x["offer_id"])
    return jsonify(items)


@app.route("/api/draft/<offer_id>")
def api_draft(offer_id):
    try:
        from modules.ozon.catalog_draft import build_draft

        draft = build_draft(offer_id)
        if draft.get("error") and not draft.get("draft_title"):
            return jsonify({"error": draft["error"]}), 404
        return jsonify(draft)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/process_images/<offer_id>", methods=["POST"])
def api_process_images(offer_id):
    body = request.get_json()
    src_urls = body.get("images", [])[:6]

    seen_out = []
    for i, url in enumerate(src_urls):
        src = os.path.join(TMP_DIR, f"src_{offer_id}_{i}.jpg")
        out = os.path.join(TMP_DIR, f"out_{offer_id}_{i}.jpg")
        ok = False
        for attempt in range(5):
            try:
                download(url, src)
                to_3x4(src, out)
                new_url = upload_image(out)
                if new_url not in seen_out:
                    seen_out.append(new_url)
                ok = True
                break
            except Exception:
                time.sleep(2 * (attempt + 1))
        if not ok:
            pass
        time.sleep(1.0)

    return jsonify({"images": seen_out})


@app.route("/api/migrate", methods=["POST"])
def api_migrate():
    p = request.get_json()
    offer_id = p["offer_id"]
    images = p["images"]
    if not images:
        return jsonify({"error": "no images"}), 400

    # 防重复：仅当 Ozon 店铺已存在该 offer_id 时跳过（attrs 同步）
    # migrated_offers 只作记录；若未在店铺中（卡片失败/已删），允许重传
    migrated_list = load_json(MIGRATED_PATH, [])
    migrated_set = set(migrated_list)
    existing = load_json(EXISTING_ATTRS_PATH, {"result": []})
    existing_offers = {str(it["offer_id"]) for it in existing.get("result", []) if it.get("offer_id")}

    from modules.ozon.product_lifecycle import ensure_offer_reset

    want_cat = int(p["category_id"])
    want_type = int(p["type_id"])
    reset = ensure_offer_reset(ozon_post, offer_id, category_id=want_cat, type_id=want_type)
    if reset.get("action") == "delete_failed":
        return jsonify({
            "status": "error",
            "offer_id": offer_id,
            "error": f"无法删除 Ozon 旧卡片以便改类目：{reset.get('detail')}",
            "reset": reset,
        }), 400

    if reset.get("action") == "keep" and offer_id in existing_offers:
        return jsonify({
            "status": "skipped_duplicate",
            "offer_id": offer_id,
            "error": f"offer_id {offer_id} 已在 Ozon 店铺（正式商品），请用改价或后台编辑",
        }), 200

    if offer_id in migrated_set and offer_id not in existing_offers:
        migrated_list = [o for o in migrated_list if o != offer_id]
        save_json(MIGRATED_PATH, migrated_list)

    from modules.ozon.migrate_attrs import build_import_attributes, resolve_profile
    from modules.ozon.tk_category_map import record_mapping
    from modules.ozon.listing_text import polish_ozon_description, polish_ozon_title

    p = dict(p)
    p["title"] = polish_ozon_title(
        p.get("title") or "",
        len_cm=str(p.get("len_cm") or ""),
        wid_cm=str(p.get("wid_cm") or ""),
        migrate_profile=str(p.get("migrate_profile") or ""),
    )
    p["description"] = polish_ozon_description(p.get("description") or "")

    migrate_profile = p.get("migrate_profile")
    attributes = build_import_attributes({**p, "migrate_profile": migrate_profile})

    item = {
        "attributes": attributes,
        "description_category_id": int(p["category_id"]),
        "type_id": int(p["type_id"]),
        "color_image": images[0],
        "currency_code": "CNY",
        "depth": int(p["depth"]), "width": int(p["width"]), "height": int(p["height"]),
        "dimension_unit": "mm",
        "weight": int(p["weight"]), "weight_unit": "g",
        "images": images,
        "name": p["title"],
        "offer_id": offer_id,
        "old_price": str(p["old_price"]),
        "price": str(p["price"]),
        "vat": "0",
        # 同步即关闭"快速收集评价"付费推广(新品默认会被自动开启)
        "promotions": [{"operation": "DISABLE", "type": "REVIEWS_PROMO"}],
    }

    result = ozon_post("/v3/product/import", {"items": [item]})
    task_id = result.get("result", {}).get("task_id")

    status = "unknown"
    errors = []
    if task_id:
        for _ in range(10):
            time.sleep(3)
            r = ozon_post("/v1/product/import/info", {"task_id": task_id})
            items_status = r.get("result", {}).get("items", [])
            if items_status:
                status = items_status[0]["status"]
                errors = items_status[0].get("errors", [])
                if status != "pending":
                    break

    rich_status = "skipped"
    import_ok = status == "imported" and not errors
    if import_ok:
        migrated = load_json(MIGRATED_PATH, [])
        if offer_id not in migrated:
            migrated.append(offer_id)
            save_json(MIGRATED_PATH, migrated)
        log = load_json(MIGRATE_LOG_PATH, [])
        log.append({"date": str(date.today()), "offer_id": offer_id, "title": p["title"]})
        save_json(MIGRATE_LOG_PATH, log)
        # 同步链接即直接填入富内容
        rich_status = add_rich_content(offer_id, p["title"], p["description"])
        if p.get("tk_category_id"):
            record_mapping(
                tk_category_id=str(p["tk_category_id"]),
                tk_category_name=str(p.get("tk_category_leaf") or ""),
                type_id=int(p["type_id"]),
                category_id=int(p["category_id"]),
                profile=resolve_profile(int(p["type_id"]), p.get("migrate_profile")),
                source="migrate",
            )

    return jsonify({
        "task_id": task_id,
        "status": status,
        "errors": errors,
        "rich_status": rich_status,
        "reset": reset,
        "import_ok": import_ok,
    })


# ---------------------------------------------------------------- 改价 ----

def active_suppressions():
    """返回当前仍在'14天内不变'有效期内的 offer_id 集合，并顺手清理过期项。"""
    data = load_json(PRICE_SUPPRESS_PATH, {})
    today = date.today().isoformat()
    active = {oid: until for oid, until in data.items() if until > today}
    if len(active) != len(data):
        save_json(PRICE_SUPPRESS_PATH, active)
    return set(active.keys())


@app.route("/api/price_suppress", methods=["POST"])
def api_price_suppress():
    """把选中的 offer_id 标记为未来 PRICE_SUPPRESS_DAYS 天内不再推送到改价预警。"""
    offer_ids = request.get_json().get("offer_ids", [])
    data = load_json(PRICE_SUPPRESS_PATH, {})
    until = (date.today() + timedelta(days=PRICE_SUPPRESS_DAYS)).isoformat()
    for oid in offer_ids:
        data[oid] = until
    save_json(PRICE_SUPPRESS_PATH, data)

    # 同时从当前缓存的待改价列表里移除，前端刷新即看不到
    pending = load_json(PENDING_REVIEW_PATH, None)
    if pending and pending.get("rows"):
        sup = set(offer_ids)
        pending["rows"] = [r for r in pending["rows"] if r["offer_id"] not in sup]
        save_json(PENDING_REVIEW_PATH, pending)

    return jsonify({"ok": True, "suppressed_until": until, "count": len(offer_ids)})


@app.route("/api/red_prices")
def api_red_prices():
    all_ids = []
    last_id = ""
    while True:
        body = {"filter": {"visibility": "ALL"}, "last_id": last_id, "limit": 1000}
        r = ozon_post("/v3/product/list", body)
        items = r["result"]["items"]
        all_ids.extend([it["product_id"] for it in items])
        last_id = r["result"]["last_id"]
        if not last_id or not items:
            break

    price_results = []
    for i in range(0, len(all_ids), 50):
        chunk = all_ids[i:i + 50]
        r = ozon_post("/v5/product/info/prices", {"filter": {"product_id": chunk}, "limit": 100})
        price_results.extend(r["items"])

    red_items = [it for it in price_results if it["price_indexes"]["color_index"] == "RED"]

    # 过滤掉用户标记"14天内不变"且仍在有效期内的商品
    suppressed = active_suppressions()
    red_items = [it for it in red_items if it["offer_id"] not in suppressed]

    prev_red = set(load_json(LAST_RED_PATH, []))

    product_ids = [it["product_id"] for it in red_items]
    info_by_id = {}
    if product_ids:
        attrs = ozon_post("/v4/product/info/attributes", {
            "filter": {"product_id": [str(p) for p in product_ids], "visibility": "ALL"},
            "limit": 100,
        })
        for it in attrs["result"]:
            info_by_id[it["id"]] = {
                "name": it.get("name", ""),
                "image": it.get("primary_image", "") or (it.get("images", [""])[0] if it.get("images") else ""),
            }

    rows = []
    for it in red_items:
        offer_id = it["offer_id"]
        pid = it["product_id"]
        cur_price = it["price"]["price"]
        cur_old_price = it["price"]["old_price"]
        idx = it["price_indexes"]["ozon_index_data"]["price_index_value"]
        is_new = offer_id not in prev_red

        new_price = round(cur_price / idx * 0.95, 2) if idx else cur_price
        sugg_old_price = round(new_price / 0.72, 2)

        info = info_by_id.get(pid, {"name": "?", "image": ""})
        rows.append({
            "offer_id": offer_id,
            "name": info["name"],
            "image": info["image"],
            "cur_price": cur_price,
            "cur_old_price": cur_old_price,
            "price_index": idx,
            "suggested_price": new_price,
            "suggested_old_price": sugg_old_price,
            "is_new": is_new,
        })

    rows.sort(key=lambda r: (not r["is_new"], -r["price_index"]))

    save_json(PENDING_REVIEW_PATH, {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": rows,
    })

    return jsonify(rows)


@app.route("/api/pending_price_review")
def api_pending_price_review():
    return jsonify(load_json(PENDING_REVIEW_PATH, None))


@app.route("/api/daily_summary", methods=["GET", "POST"])
def api_daily_summary():
    if request.method == "POST":
        save_json(DAILY_SUMMARY_PATH, request.get_json())
        return jsonify({"ok": True})
    return jsonify(load_json(DAILY_SUMMARY_PATH, None))


@app.route("/api/apply_prices", methods=["POST"])
def api_apply_prices():
    items = request.get_json()["items"]
    payload = []
    for it in items:
        payload.append({
            "offer_id": it["offer_id"],
            "price": str(it["price"]),
            "old_price": str(it["old_price"]),
            "min_price": "0",
            "currency_code": "CNY",
        })
    result = ozon_post("/v1/product/import/prices", {"prices": payload})

    log = load_json(PRICE_LOG_PATH, [])
    for it in items:
        log.append({"date": str(date.today()), **it})
    save_json(PRICE_LOG_PATH, log)

    # update last_red snapshot: remove items we just fixed (assume they leave RED)
    applied_offers = {it["offer_id"] for it in items}
    last_red = load_json(LAST_RED_PATH, [])
    last_red = [o for o in last_red if o not in applied_offers]
    save_json(LAST_RED_PATH, last_red)

    return jsonify(result)


@app.route("/api/snapshot_red", methods=["POST"])
def api_snapshot_red():
    """Persist current red offer_id list as the baseline for 'is_new' comparisons."""
    offer_ids = request.get_json()["offer_ids"]
    save_json(LAST_RED_PATH, offer_ids)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- 数据分析 ----

@app.route("/api/analytics")
def api_analytics():
    days = int(request.args.get("days", 7))
    today = date.today()
    date_from = (today - timedelta(days=days)).isoformat()
    date_to = (today - timedelta(days=1)).isoformat()

    # build sku -> product info map
    all_ids = []
    last_id = ""
    while True:
        r = ozon_post("/v3/product/list", {"filter": {"visibility": "ALL"}, "last_id": last_id, "limit": 1000})
        items = r["result"]["items"]
        all_ids.extend([it["product_id"] for it in items])
        last_id = r["result"]["last_id"]
        if not last_id or not items:
            break

    sku_map = {}
    for i in range(0, len(all_ids), 100):
        chunk = all_ids[i:i + 100]
        time.sleep(0.5)
        info = ozon_post("/v3/product/info/list", {"product_id": chunk})
        for it in info.get("items", []):
            image = it["images"][0] if it.get("images") else ""
            for src in it.get("sources", []):
                sku_map[src["sku"]] = {
                    "offer_id": it["offer_id"],
                    "name": it["name"],
                    "image": image,
                    "price": it.get("price", ""),
                }

    # fetch analytics, paginated
    rows_by_sku = {}
    offset = 0
    while True:
        body = {
            "date_from": date_from, "date_to": date_to,
            "metrics": ANALYTICS_METRICS, "dimension": ["sku"],
            "limit": 1000, "offset": offset,
        }
        r = None
        for attempt in range(5):
            r = ozon_post("/v1/analytics/data", body)
            if "result" in r:
                break
            time.sleep(2)
        data = r.get("result", {}).get("data", []) if r else []
        for d in data:
            sku = d["dimensions"][0]["id"]
            rows_by_sku[int(sku)] = d["metrics"]
        if len(data) < 1000:
            break
        offset += 1000
        time.sleep(1)

    rows = []
    for sku, metrics in rows_by_sku.items():
        m = dict(zip(ANALYTICS_METRICS, metrics))
        info = sku_map.get(sku, {"offer_id": str(sku), "name": "", "image": "", "price": ""})
        impressions = m["hits_view_search"]
        pdp_views = m["hits_view_pdp"]
        ctr = round(pdp_views / impressions * 100, 2) if impressions else 0
        rows.append({
            "sku": sku,
            "offer_id": info["offer_id"],
            "name": info["name"],
            "image": info["image"],
            "price": info["price"],
            "impressions": impressions,
            "visitors": m["session_view_pdp"],
            "pdp_views": pdp_views,
            "ctr": ctr,
            "add_to_cart": m["hits_tocart_search"] + m["hits_tocart_pdp"],
            "conv_to_cart": m["conv_tocart_pdp"],
            "orders": m["ordered_units"],
            "revenue": m["revenue"],
        })

    rows.sort(key=lambda r: -r["impressions"])
    return jsonify({"date_from": date_from, "date_to": date_to, "rows": rows})


# ---------------------------------------------------------------- 促销活动 ----

def clamp_action_price(c):
    """活动价不低于当前在售价(最终售价)，并夹在弹性区间[min,max]内。
    当前价通常>=区间上限，此时取区间上限(price_max_elastic=最小折扣，最贴近当前价)。"""
    price = float(c["price"])
    lo = float(c["price_min_elastic"])
    hi = float(c["price_max_elastic"])
    val = min(max(price, lo), hi)
    return round(val, 2)


@app.route("/api/promotions/auto_activate", methods=["POST"])
def api_promotions_auto_activate():
    """每日任务调用：扫描所有可参加弹性提升的商品，按'不破当前售价'的活动价自动全部激活。"""
    api_promotions()  # 刷新候选缓存
    pending = load_json(PENDING_PROMO_PATH, None)
    rows = (pending or {}).get("rows", [])
    if not rows:
        return jsonify({"ok": True, "activated": 0, "items": []})

    payload = [{"product_id": r["product_id"], "action_price": r["suggested_action_price"]} for r in rows]
    result = ozon_post("/v1/actions/products/activate",
                       {"action_id": ELASTIC_BOOST_ACTION_ID, "products": payload})

    log = load_json(PROMO_LOG_PATH, [])
    for r in rows:
        log.append({"date": str(date.today()), "action_id": ELASTIC_BOOST_ACTION_ID,
                    "product_id": r["product_id"], "offer_id": r["offer_id"],
                    "action_price": r["suggested_action_price"], "auto": True})
    save_json(PROMO_LOG_PATH, log)

    # 清空已激活的待审列表
    save_json(PENDING_PROMO_PATH, {**(pending or {}), "rows": []})

    activated = len((result.get("result", {}) or {}).get("product_ids", []) or [])
    return jsonify({"ok": True, "activated": activated, "submitted": len(payload),
                    "result": result})


@app.route("/api/promotions")
def api_promotions():
    actions = ozon_get("/v1/actions").get("result", [])
    boost = next((a for a in actions if a["id"] == ELASTIC_BOOST_ACTION_ID), None)

    candidates = []
    if boost:
        offset = 0
        while True:
            r = ozon_post("/v1/actions/candidates", {"action_id": ELASTIC_BOOST_ACTION_ID, "limit": 100, "offset": offset})
            products = r.get("result", {}).get("products", [])
            candidates.extend(products)
            if len(products) < 100:
                break
            offset += 100
            time.sleep(0.5)

    rows = []
    if candidates:
        ids = [c["id"] for c in candidates]
        time.sleep(0.5)
        info = ozon_post("/v3/product/info/list", {"product_id": ids})
        info_by_id = {it["id"]: it for it in info.get("items", [])}
        for c in candidates:
            it = info_by_id.get(c["id"], {})
            image = it["images"][0] if it.get("images") else ""
            rows.append({
                "product_id": c["id"],
                "offer_id": it.get("offer_id", ""),
                "name": it.get("name", ""),
                "image": image,
                "price": c["price"],
                "price_min_elastic": c["price_min_elastic"],
                "price_max_elastic": c["price_max_elastic"],
                "min_boost": c["min_boost"],
                "max_boost": c["max_boost"],
                # 建议活动价：不低于当前在售价，且夹在弹性区间内（取尽量贴近当前价=最小折扣的合法值）
                "suggested_action_price": clamp_action_price(c),
            })

    save_json(PENDING_PROMO_PATH, {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "action": {"id": ELASTIC_BOOST_ACTION_ID, "title": boost["title"] if boost else "",
                    "participating_products_count": boost["participating_products_count"] if boost else 0},
        "rows": rows,
    })

    return jsonify(load_json(PENDING_PROMO_PATH, None))


@app.route("/api/pending_promo_review")
def api_pending_promo_review():
    return jsonify(load_json(PENDING_PROMO_PATH, None))


@app.route("/api/promotions/activate", methods=["POST"])
def api_promotions_activate():
    items = request.get_json()["items"]
    payload = [{"product_id": it["product_id"], "action_price": it["action_price"]} for it in items]
    result = ozon_post("/v1/actions/products/activate", {"action_id": ELASTIC_BOOST_ACTION_ID, "products": payload})

    log = load_json(PROMO_LOG_PATH, [])
    for it in items:
        log.append({"date": str(date.today()), "action_id": ELASTIC_BOOST_ACTION_ID, **it})
    save_json(PROMO_LOG_PATH, log)

    # remove activated products from pending review
    pending = load_json(PENDING_PROMO_PATH, None)
    if pending:
        activated_ids = {it["product_id"] for it in items}
        pending["rows"] = [r for r in pending["rows"] if r["product_id"] not in activated_ids]
        save_json(PENDING_PROMO_PATH, pending)

    return jsonify(result)


# ---------------------------------------------------------------- 历史 ----

@app.route("/api/history")
def api_history():
    return jsonify({
        "migrated": load_json(MIGRATED_PATH, []),
        "migrate_log": load_json(MIGRATE_LOG_PATH, []),
        "price_log": load_json(PRICE_LOG_PATH, []),
    })


# ---------------------------------------------------------------- 类目核对 ----

@app.route("/api/category_options")
def api_category_options():
    return jsonify(load_json(CATEGORY_OPTIONS_PATH, []))


@app.route("/api/category_review")
def api_category_review():
    suggestions = load_json(CATEGORY_SUGGESTIONS_PATH, [])
    prices = load_json(OFFER_PRICES_CACHE_PATH, {})
    rows = []
    for s in suggestions:
        if s.get("error"):
            continue
        p = prices.get(s["offer_id"], {})
        rows.append({**s, "price": p.get("price", ""), "old_price": p.get("old_price", "")})
    rows.sort(key=lambda r: (not r.get("changed"), r["offer_id"]))
    return jsonify(rows)


@app.route("/api/category_review/apply", methods=["POST"])
def api_category_review_apply():
    body = request.get_json()
    items = body.get("items", [])  # [{offer_id, category_id, type_id, price, old_price}]

    errors = []
    price_items = []
    category_items = []

    # split into price-only vs category-change items
    for it in items:
        if it.get("change_category"):
            category_items.append(it)
        if it.get("change_price"):
            price_items.append(it)

    # ---- 价格更新 (快速接口) ----
    price_result = None
    if price_items:
        payload = [{"offer_id": it["offer_id"], "price": str(it["price"]),
                    "old_price": str(it["old_price"]), "min_price": "0", "currency_code": "CNY"}
                   for it in price_items]
        price_result = ozon_post("/v1/product/import/prices", {"prices": payload})
        # update local cache
        cache = load_json(OFFER_PRICES_CACHE_PATH, {})
        for it in price_items:
            cache[it["offer_id"]] = {"price": str(it["price"]), "old_price": str(it["old_price"])}
        save_json(OFFER_PRICES_CACHE_PATH, cache)

    # ---- 类目更新 (full re-import) ----
    category_result = None
    if category_items:
        offer_ids = [it["offer_id"] for it in category_items]
        attrs_resp = ozon_post("/v4/product/info/attributes",
                               {"filter": {"offer_id": offer_ids, "visibility": "ALL"}, "limit": 100})
        price_resp = ozon_post("/v3/product/info/list", {"offer_id": offer_ids})
        price_by_offer = {i["offer_id"]: i for i in price_resp.get("items", [])}
        it_map = {it["offer_id"]: it for it in category_items}

        import_items = []
        for prod in attrs_resp.get("result", []):
            oid = prod["offer_id"]
            req = it_map[oid]
            p = price_by_offer.get(oid, {})
            attrs = [{"complex_id": a.get("complex_id", 0), "id": a["id"], "values": a["values"]}
                     for a in prod["attributes"]]
            for ca in prod.get("complex_attributes", []):
                for a in ca.get("attributes", []):
                    if a["values"]:
                        attrs.append({"complex_id": a.get("complex_id", 0), "id": a["id"], "values": a["values"]})
            import_items.append({
                "attributes": attrs,
                "description_category_id": int(req["category_id"]),
                "type_id": int(req["type_id"]),
                "color_image": prod.get("color_image") or (prod["images"][0] if prod["images"] else ""),
                "currency_code": "CNY",
                "depth": prod["depth"], "width": prod["width"], "height": prod["height"],
                "dimension_unit": prod.get("dimension_unit", "mm"),
                "weight": prod["weight"], "weight_unit": prod.get("weight_unit", "g"),
                "images": prod["images"],
                "name": prod["name"],
                "offer_id": oid,
                "old_price": str(req.get("old_price") or p.get("old_price", "0")),
                "price": str(req.get("price") or p.get("price", "0")),
                "vat": "0",
            })
        category_result = ozon_post("/v3/product/import", {"items": import_items})

        # update suggestions cache with new category
        suggestions = load_json(CATEGORY_SUGGESTIONS_PATH, [])
        for s in suggestions:
            if s["offer_id"] in it_map:
                req = it_map[s["offer_id"]]
                s["current_category_id"] = int(req["category_id"])
                s["current_type_id"] = int(req["type_id"])
                s["changed"] = False
        save_json(CATEGORY_SUGGESTIONS_PATH, suggestions)

    return jsonify({"price_result": price_result, "category_result": category_result})


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(port=5055, debug=True, use_reloader=True)
