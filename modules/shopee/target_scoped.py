"""No-refresh, existing-global-only primitives for target-scoped recovery."""
from __future__ import annotations

from modules.shopee.client import shop_get


class ShopeeRegionalPublishReconciliationError(RuntimeError):
    external_write_evidence = {"external_writes_performed": ["shopee:regional_publish"], "durable_state_uncertain": True, "reconciliation_required": True}

    def __init__(self, message, *, task_id="", item_id="", checks=None):
        super().__init__(message); self.external_reference = str(item_id or "") or None
        self.external_write_evidence = {**self.external_write_evidence, "task_id": str(task_id), "item_id": str(item_id), "error_type": type(self).__name__, "checks": dict(checks or {})}


def scan_prepared_shop_sku(*, shop_id: int, access_token: str, seller_sku: str) -> dict:
    found = []
    completeness = {}
    for status in ("NORMAL", "UNLIST", "BANNED"):
        offset = 0
        seen = set()
        pages = item_count = base_rows = model_rows = 0
        while True:
            if offset in seen or len(seen) >= 100:
                raise RuntimeError(f"Shopee {status} pagination is non-terminating")
            seen.add(offset); pages += 1
            raw = shop_get("/api/v2/product/get_item_list", shop_id, access_token, {"offset": offset, "page_size": 100, "item_status": status})
            if not isinstance(raw, dict) or raw.get("error") or not isinstance(raw.get("response"), dict): raise RuntimeError(f"Shopee {status} list response is invalid")
            page = raw["response"]; entries = page.get("item")
            if not isinstance(entries, list): raise RuntimeError(f"Shopee {status} item list is invalid")
            ids = [str(x.get("item_id") or "") for x in entries if isinstance(x, dict) and x.get("item_id")]
            if len(ids) != len(entries) or len(set(ids)) != len(ids): raise RuntimeError(f"Shopee {status} item ids are incomplete")
            item_count += len(ids)
            for start in range(0, len(ids), 50):
                expected = ids[start:start+50]
                raw_base = shop_get("/api/v2/product/get_item_base_info", shop_id, access_token, {"item_id_list": ",".join(expected)})
                if not isinstance(raw_base, dict) or raw_base.get("error") or not isinstance(raw_base.get("response"), dict) or not isinstance(raw_base["response"].get("item_list"), list): raise RuntimeError("Shopee base-info response is invalid")
                rows = raw_base["response"]["item_list"]
                returned = [str(row.get("item_id") or "") for row in rows if isinstance(row, dict)]
                if len(returned) != len(rows) or set(returned) != set(expected) or len(set(returned)) != len(returned): raise RuntimeError("Shopee base-info batch is incomplete")
                base_rows += len(rows)
                for row in rows:
                    if str(row.get("item_sku") or "") == seller_sku:
                        found.append({"item_id": str(row.get("item_id") or ""), "scope": "item"})
                    if row.get("has_model"):
                        raw_model = shop_get("/api/v2/product/get_model_list", shop_id, access_token, {"item_id": int(row["item_id"])})
                        if not isinstance(raw_model, dict) or raw_model.get("error") or not isinstance(raw_model.get("response"), dict) or not isinstance(raw_model["response"].get("model"), list): raise RuntimeError("Shopee model response is invalid")
                        models = raw_model["response"]["model"]; model_rows += len(models)
                        for model in models:
                            if str(model.get("model_sku") or "") == seller_sku:
                                found.append({"item_id": str(row.get("item_id") or ""), "model_id": str(model.get("model_id") or ""), "scope": "model"})
            if not page.get("has_next_page"):
                completeness[status] = {"pages": pages, "item_count": item_count, "base_rows": base_rows, "model_rows": model_rows, "count": len(found), "digest": __import__("hashlib").sha256(repr((status, ids, item_count, base_rows, model_rows)).encode()).hexdigest(), "complete": True}
                break
            next_offset = page.get("next_offset")
            if not isinstance(next_offset, int) or next_offset <= offset:
                raise RuntimeError(f"Shopee {status} pagination cursor is non-terminating")
            offset = next_offset
    return {"matches": found, "statuses": completeness, "complete": all(row.get("complete") for row in completeness.values())}


def compatible_prepared_logistics(*, shop_id: int, access_token: str, parcel: dict, excluded_ids=()) -> list[int]:
    rows = shop_get("/api/v2/logistics/get_channel_list", shop_id, access_token).get("response", {}).get("logistics_channel_list", ())
    from modules.shopee.publish import _channel_supports_parcel
    excluded = {int(value) for value in excluded_ids}
    weight = float(parcel["weight_kg"]); dimensions = tuple(float(x) for x in parcel["package_cm"])
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


def runtime_global_master(*, shop_id: int, merchant_id: int, merchant_token: str, global_item_id: str, approved_master_digest: str) -> dict:
    """Read exact source copy for in-memory evaluation; caller persists only summary."""
    from modules.shopee.client import merchant_get
    from shared_platform.target_scoped_release_contracts import approved_shopee_channel_master_digest
    response = merchant_get("/api/v2/global_product/get_global_item_info", merchant_id, merchant_token, {"global_item_id_list": str(global_item_id)})
    rows = (response.get("response") or {}).get("global_item_list") if isinstance(response, dict) else None
    if response.get("error") or not isinstance(rows, list) or len(rows) != 1: raise RuntimeError("official global master response is invalid")
    row = rows[0]; image = row.get("image") or {}; urls = image.get("image_url_list") if isinstance(image, dict) else None
    title, description = row.get("global_item_name"), row.get("description") or row.get("global_item_description")
    digest = approved_shopee_channel_master_digest(title, description, urls)
    if digest != approved_master_digest: raise RuntimeError("official global master drift")
    return {"title": title, "description": description, "urls": list(urls), "digest": digest, "summary": {"global_item_id": str(global_item_id), "master_digest": digest, "image_count": len(urls)}}


def publish_existing_global_site(*, request, evidence: dict) -> dict:
    """One create_publish_task from the immutable command; bounded/explicit."""
    from modules.shopee.client import merchant_get, merchant_post
    from modules.shopee.auth import load_tokens
    command = request.planned_command
    shop_id, _shop_token = __import__("domains.channel_operations.target_scoped_retry_adapters", fromlist=["_prepared_shopee_credentials"])._prepared_shopee_credentials(command["region"])
    store = load_tokens(); merchant_id = int((store.get("shops", {}).get(str(shop_id), {}) or {}).get("merchant_id") or 0)
    token = str((store.get("merchants", {}).get(str(merchant_id), {}) or {}).get("access_token") or "")
    if not merchant_id or not token: raise RuntimeError("prepared merchant token is required")
    master = runtime_global_master(shop_id=shop_id, merchant_id=merchant_id, merchant_token=token, global_item_id=str(evidence["global_item_id"]), approved_master_digest=command["approved_master_digest"])
    body = {"global_item_id": int(evidence["global_item_id"]), "shop_id": int(shop_id), "shop_region": command["region"], "item": {"item_status": command["item_status"], "original_price": command["local_original_price"], "logistic": [{"logistic_id": x, "enabled": True} for x in evidence["selected_logistics_ids"]], "model": [{"tier_index": evidence["global_tier_index"], "original_price": command["local_original_price"]}]}}
    response = merchant_post("/api/v2/global_product/create_publish_task", merchant_id, token, body)
    task_id = (response.get("response") or {}).get("publish_task_id")
    if not task_id: raise RuntimeError("create_publish_task response is ambiguous")
    item_id = ""
    result = {}
    for _ in range(3):
        raw_task = merchant_get("/api/v2/global_product/get_publish_task_result", merchant_id, token, {"publish_task_id": int(task_id)})
        if not isinstance(raw_task, dict) or raw_task.get("error") or not isinstance(raw_task.get("response"), dict): raise ShopeeRegionalPublishReconciliationError("accepted publish task readback is unknown", task_id=task_id)
        result = raw_task["response"]
        if result.get("publish_status") in {"success", "failed"}: break
    item_id = str(result.get("item_id") or (result.get("success") or {}).get("item_id") or "")
    if result.get("publish_status") != "success" or not item_id: raise ShopeeRegionalPublishReconciliationError("accepted publish task did not verify", task_id=task_id, item_id=item_id)
    # Regional identity is read through the prepared shop token, never token refresh.
    base = shop_get("/api/v2/product/get_item_base_info", shop_id, _shop_token, {"item_id_list": item_id})
    rows = (base.get("response") or {}).get("item_list") if isinstance(base, dict) else None
    if not isinstance(rows, list) or len(rows) != 1: raise ShopeeRegionalPublishReconciliationError("regional readback unknown", task_id=task_id, item_id=item_id)
    item = rows[0]
    from modules.shopee.client import resolve_global_item_id
    resolved = str(resolve_global_item_id(shop_id, merchant_id, token, item_id) or "")
    models = shop_get("/api/v2/product/get_model_list", shop_id, _shop_token, {"item_id": int(item_id)})
    model_rows = (models.get("response") or {}).get("model") if isinstance(models, dict) else None
    logistics = item.get("logistic_info") or []
    enabled = {int(row.get("logistic_id")) for row in logistics if isinstance(row, dict) and row.get("enabled") and row.get("logistic_id") is not None}
    expected = set(int(x) for x in evidence["selected_logistics_ids"])
    image_urls = ((item.get("image") or {}).get("image_url_list") if isinstance(item.get("image"), dict) else None)
    planned_models = [row for row in (model_rows or []) if str(row.get("model_sku") or "") == str(command["model_sku"])]
    price_rows = (planned_models[0].get("price_info") or []) if len(planned_models) == 1 else []
    currency_rows = [row for row in price_rows if str(row.get("currency") or "").upper() == str(command["local_currency"]).upper()]
    from decimal import Decimal, InvalidOperation
    try: price_exact = len(currency_rows) == 1 and Decimal(str(currency_rows[0].get("original_price"))) == Decimal(str(command["local_original_price"]))
    except (InvalidOperation, ValueError): price_exact = False
    hard_checks = {"global_linkage": resolved == str(evidence["global_item_id"]), "normal_status": str(item.get("item_status") or "") == "NORMAL", "unique_model_sku": len(planned_models) == 1 and str(planned_models[0].get("model_id") or "").isdigit(), "price_currency_exact": price_exact, "logistics_exact": enabled == expected, "images_present": isinstance(image_urls, list) and len(image_urls) == int(command["approved_image_count"]) and bool(image_urls and image_urls[0])}
    hard = all(hard_checks.values())
    from shared_platform.target_scoped_release_contracts import evaluate_shopee_regional_copy_observation, evaluate_shopee_regional_image_observation, shopee_regional_observation_outcome
    # Source master is re-read by the verifier boundary; never persist text/URLs.
    copy = evaluate_shopee_regional_copy_observation(source_title=master["title"], source_description=master["description"], regional_title=item.get("item_name"), regional_description=item.get("description"), source_global_master_digest=command["approved_master_digest"], site=command["region"])
    image = evaluate_shopee_regional_image_observation(approved_count=command["approved_image_count"], regional_image_urls=image_urls or [], global_linkage_verified=hard_checks["global_linkage"])
    outcome = shopee_regional_observation_outcome(listing_hard_exact=hard, copy_observation=copy, image_observation=image)
    return {"item_id": item_id, "verified": outcome.get("outcome") == "SUCCEEDED", "manual_review_required": outcome.get("manual_review_required") is True, "task_status": "success", "hard_checks": hard_checks, "enabled_logistics_count": len(enabled), "image_count": len(image_urls or []), "master_digest": master["digest"], "observation_outcome": outcome.get("outcome"), "reconciliation_required": outcome.get("reconciliation_required") is True, "external_writes_performed": ["shopee:regional_publish"]}
