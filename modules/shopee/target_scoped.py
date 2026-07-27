"""No-refresh, existing-global-only primitives for target-scoped recovery."""
from __future__ import annotations

from modules.shopee.client import shop_get


def scan_prepared_shop_sku(*, shop_id: int, access_token: str, seller_sku: str) -> dict:
    found = []
    completeness = {}
    for status in ("NORMAL", "UNLIST", "BANNED"):
        offset = 0
        while True:
            page = shop_get("/api/v2/product/get_item_list", shop_id, access_token, {"offset": offset, "page_size": 100, "item_status": status}).get("response") or {}
            ids = [str(x.get("item_id") or "") for x in page.get("item") or () if x.get("item_id")]
            for start in range(0, len(ids), 50):
                rows = shop_get("/api/v2/product/get_item_base_info", shop_id, access_token, {"item_id_list": ",".join(ids[start:start+50])}).get("response", {}).get("item_list", ())
                for row in rows:
                    if str(row.get("item_sku") or "") == seller_sku:
                        found.append({"item_id": str(row.get("item_id") or ""), "scope": "item"})
                    if row.get("has_model"):
                        for model in shop_get("/api/v2/product/get_model_list", shop_id, access_token, {"item_id": int(row["item_id"])}).get("response", {}).get("model", ()):
                            if str(model.get("model_sku") or "") == seller_sku:
                                found.append({"item_id": str(row.get("item_id") or ""), "model_id": str(model.get("model_id") or ""), "scope": "model"})
            if not page.get("has_next_page"):
                completeness[status] = {"pages": completeness.get(status, {}).get("pages", 0) + 1, "complete": True}
                break
            next_offset = page.get("next_offset")
            if not isinstance(next_offset, int) or next_offset <= offset:
                raise RuntimeError(f"Shopee {status} pagination cursor is non-terminating")
            completeness[status] = {"pages": completeness.get(status, {}).get("pages", 0) + 1, "complete": False}
            offset = next_offset
    return {"matches": found, "statuses": completeness, "complete": all(row.get("complete") for row in completeness.values())}


def compatible_prepared_logistics(*, shop_id: int, access_token: str, parcel: dict, excluded_ids=()) -> list[int]:
    rows = shop_get("/api/v2/logistics/get_channel_list", shop_id, access_token).get("response", {}).get("logistics_channel_list", ())
    from modules.shopee.publish import _channel_supports_parcel
    excluded = {int(value) for value in excluded_ids}
    weight = float(parcel["weight_kg"]); dimensions = tuple(float(x) for x in parcel["dimensions_cm"])
    return [int(row.get("logistics_channel_id") or row.get("logistic_id")) for row in rows if row.get("enabled") and int(row.get("logistics_channel_id") or row.get("logistic_id") or 0) not in excluded and _channel_supports_parcel(row, region="VN" if 50052 in excluded else "MY", weight_kg=weight, dimensions_cm=dimensions)]


def inspect_existing_global(*, shop_id: int, access_token: str, global_item_id: str, model_sku: str, approved_master_digest: str) -> dict:
    """GET-only unique global/model proof; no map mutation or token refresh."""
    from modules.shopee.client import merchant_get
    from modules.shopee.auth import load_tokens
    store = load_tokens()
    merchant_id = int((store.get("shops", {}).get(str(shop_id), {}) or {}).get("merchant_id") or 0)
    merchant = (store.get("merchants", {}).get(str(merchant_id), {}) or {})
    token = str(merchant.get("access_token") or "")
    if not merchant_id or not token:
        raise RuntimeError("prepared merchant token is required")
    item_response = merchant_get("/api/v2/global_product/get_global_item_info", merchant_id, token, {"global_item_id_list": str(global_item_id)})
    if item_response.get("error"):
        raise RuntimeError("official global item GET failed")
    item_rows = (item_response.get("response") or {}).get("global_item_list") or []
    if len(item_rows) != 1:
        raise RuntimeError("official global item must be unique")
    item = item_rows[0]
    title = item.get("global_item_name")
    description = item.get("description") or item.get("global_item_description")
    images = item.get("image") or {}
    urls = images.get("image_url_list") if isinstance(images, dict) else None
    if not isinstance(urls, list):
        raise RuntimeError("official global item images are unavailable")
    from shared_platform.target_scoped_release_contracts import approved_shopee_channel_master_digest
    if approved_shopee_channel_master_digest(title, description, urls) != approved_master_digest:
        raise RuntimeError("official global master digest does not match immutable command")
    model_response = merchant_get("/api/v2/global_product/get_global_model_list", merchant_id, token, {"global_item_id": int(global_item_id)})
    if model_response.get("error"): raise RuntimeError("official global model GET failed")
    rows = (model_response.get("response") or {}).get("global_model") or []
    matches = [row for row in rows if str(row.get("global_model_sku") or "") == model_sku]
    if len(matches) != 1:
        raise RuntimeError("global model SKU must be unique")
    # The digest is supplied by the immutable command; production callers can
    # only proceed after the official master GET has returned a unique model.
    if not approved_master_digest:
        raise RuntimeError("approved master digest is required")
    row = matches[0]
    model_id = str(row.get("global_model_id") or "")
    tier = list(row.get("tier_index") or [])
    if not model_id.isdigit() or not tier or any(isinstance(v, bool) or not isinstance(v, int) for v in tier): raise RuntimeError("official global model identity is invalid")
    return {"global_model_id": model_id, "tier_index": tier}


def publish_existing_global_site(*, request, evidence: dict) -> dict:
    """One create_publish_task from the immutable command; bounded/explicit."""
    from modules.shopee.client import merchant_get, merchant_post
    from modules.shopee.auth import load_tokens
    command = request.planned_command
    shop_id, _shop_token = __import__("domains.channel_operations.target_scoped_retry_adapters", fromlist=["_prepared_shopee_credentials"])._prepared_shopee_credentials(command["region"])
    store = load_tokens(); merchant_id = int((store.get("shops", {}).get(str(shop_id), {}) or {}).get("merchant_id") or 0)
    token = str((store.get("merchants", {}).get(str(merchant_id), {}) or {}).get("access_token") or "")
    if not merchant_id or not token: raise RuntimeError("prepared merchant token is required")
    body = {"global_item_id": int(evidence["global_item_id"]), "shop_id": int(shop_id), "shop_region": command["region"], "item": {"item_status": command["item_status"], "original_price": command["local_original_price"], "logistic": [{"logistic_id": x, "enabled": True} for x in evidence["selected_logistics_ids"]], "model": [{"tier_index": evidence["global_tier_index"], "original_price": command["local_original_price"]}]}}
    response = merchant_post("/api/v2/global_product/create_publish_task", merchant_id, token, body)
    task_id = (response.get("response") or {}).get("publish_task_id")
    if not task_id: raise RuntimeError("create_publish_task response is ambiguous")
    result = merchant_get("/api/v2/global_product/get_publish_task_result", merchant_id, token, {"publish_task_id": int(task_id)}).get("response") or {}
    item_id = str(result.get("item_id") or (result.get("success") or {}).get("item_id") or "")
    return {"item_id": item_id, "verified": bool(item_id and result.get("publish_status") == "success"), "task_status": str(result.get("publish_status") or "unknown")}
