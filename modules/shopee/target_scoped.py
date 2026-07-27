"""No-refresh, existing-global-only primitives for target-scoped recovery."""
from __future__ import annotations

from modules.shopee.client import shop_get


def scan_prepared_shop_sku(*, shop_id: int, access_token: str, seller_sku: str) -> list[dict]:
    found = []
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
                return found
            offset = int(page.get("next_offset") or offset + len(ids))


def compatible_prepared_logistics(*, shop_id: int, access_token: str, parcel: dict, excluded_ids=()) -> list[int]:
    rows = shop_get("/api/v2/logistics/get_channel_list", shop_id, access_token).get("response", {}).get("logistics_channel_list", ())
    excluded = {int(value) for value in excluded_ids}
    return [int(row.get("logistics_channel_id") or row.get("logistic_id")) for row in rows if row.get("enabled") and int(row.get("logistics_channel_id") or row.get("logistic_id") or 0) not in excluded]


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
    rows = (merchant_get("/api/v2/global_product/get_global_model_list", merchant_id, token, {"global_item_id": int(global_item_id)}).get("response") or {}).get("model_list") or []
    matches = [row for row in rows if str(row.get("model_sku") or "") == model_sku]
    if len(matches) != 1:
        raise RuntimeError("global model SKU must be unique")
    # The digest is supplied by the immutable command; production callers can
    # only proceed after the official master GET has returned a unique model.
    if not approved_master_digest:
        raise RuntimeError("approved master digest is required")
    row = matches[0]
    return {"global_model_id": str(row.get("global_model_id") or row.get("model_id") or ""), "tier_index": list(row.get("tier_index") or [])}


def publish_existing_global_site(*, request, evidence: dict) -> dict:
    """One create_publish_task from the immutable command; bounded/explicit."""
    from modules.shopee.client import merchant_get, merchant_post
    from modules.shopee.auth import load_tokens
    command = request.planned_command
    shop_id, _shop_token = __import__("domains.channel_operations.target_scoped_retry_adapters", fromlist=["_prepared_shopee_credentials"])._prepared_shopee_credentials(command["region"])
    store = load_tokens(); merchant_id = int((store.get("shops", {}).get(str(shop_id), {}) or {}).get("merchant_id") or 0)
    token = str((store.get("merchants", {}).get(str(merchant_id), {}) or {}).get("access_token") or "")
    if not merchant_id or not token: raise RuntimeError("prepared merchant token is required")
    body = {"global_item_id": int(evidence["global_item_id"]), "shop_id": int(shop_id), "shop_region": command["region"], "item": {"item_status": command["item_status"], "original_price": command["local_original_price"], "logistic": [{"logistics_channel_id": x, "enabled": True} for x in evidence["selected_logistics_ids"]], "model": [{"tier_index": evidence["global_tier_index"], "original_price": command["local_original_price"]}]}}
    response = merchant_post("/api/v2/global_product/create_publish_task", merchant_id, token, body)
    task_id = (response.get("response") or {}).get("publish_task_id")
    if not task_id: raise RuntimeError("create_publish_task response is ambiguous")
    result = merchant_get("/api/v2/global_product/get_publish_task_result", merchant_id, token, {"publish_task_id": int(task_id)}).get("response") or {}
    item_id = str(result.get("item_id") or (result.get("success") or {}).get("item_id") or "")
    return {"item_id": item_id, "verified": bool(item_id and result.get("publish_status") == "success"), "task_status": str(result.get("publish_status") or "unknown")}
