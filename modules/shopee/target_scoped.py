"""No-refresh, existing-global-only primitives for target-scoped recovery."""
from __future__ import annotations

from modules.shopee.client import shop_get


class ShopeeRegionalPublishReconciliationError(RuntimeError):
    external_write_evidence = {"external_writes_performed": ["shopee:regional_publish"], "durable_state_uncertain": True, "reconciliation_required": True}

    def __init__(self, message, *, task_id="", item_id="", checks=None):
        super().__init__(message); self.external_reference = str(item_id or "") or None
        self.external_write_evidence = {**self.external_write_evidence, "task_id": str(task_id), "item_id": str(item_id), "error_type": type(self).__name__, "checks": dict(checks or {})}


class ShopeeRegionalPreSubmitError(RuntimeError):
    external_write_evidence = {"external_writes_performed": [], "pre_submit_failure": True}


class ShopeeRegionalDispatchUnknownError(ShopeeRegionalPublishReconciliationError):
    pass


def reconcile_existing_global_site(*, request) -> dict:
    """Official GET-only readback for a durable regional item; no refresh/write."""
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal, InvalidOperation

    from domains.channel_operations.target_scoped_retry_adapters import _prepared_shopee_credentials
    from modules.shopee.auth import load_tokens
    from modules.shopee.client import resolve_global_item_id
    from shared_platform.target_scoped_release_contracts import (
        canonical_digest,
        evaluate_shopee_regional_copy_observation,
        evaluate_shopee_regional_image_observation,
        shopee_regional_observation_outcome,
    )

    operation = request.operation_request
    command = operation.planned_command
    original = request.original_proof_evidence
    item_id = str(request.external_id or "")
    if not item_id.isdigit():
        raise RuntimeError("durable external item identity is required")

    expected_global_item_id = str(original.get("global_item_id") or "")
    expected_logistics = original.get("selected_logistics_ids")
    if (
        not expected_global_item_id.isdigit()
        or not isinstance(expected_logistics, (list, tuple))
        or not expected_logistics
    ):
        raise RuntimeError("original proof identity is incomplete")

    shop_id, token = _prepared_shopee_credentials(command["region"])
    token_store = load_tokens()
    shop_auth = (
        token_store.get("shops", {}).get(str(shop_id), {}) or {}
    )
    merchant_id = int(shop_auth.get("merchant_id") or 0)
    merchant_auth = (
        token_store.get("merchants", {}).get(str(merchant_id), {}) or {}
    )
    merchant_token = str(merchant_auth.get("access_token") or "")
    if not merchant_id or not merchant_token:
        raise RuntimeError("prepared merchant token is required")

    base = shop_get(
        "/api/v2/product/get_item_base_info",
        shop_id,
        token,
        {"item_id_list": item_id},
    )
    if (
        not isinstance(base, dict)
        or base.get("error")
        or not isinstance(base.get("response"), dict)
    ):
        raise RuntimeError("official regional item response is invalid")
    rows = base["response"].get("item_list")
    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], dict)
    ):
        raise RuntimeError("official regional item must be unique")
    item = rows[0]

    models = shop_get(
        "/api/v2/product/get_model_list",
        shop_id,
        token,
        {"item_id": int(item_id)},
    )
    if (
        not isinstance(models, dict)
        or models.get("error")
        or not isinstance(models.get("response"), dict)
    ):
        raise RuntimeError("official regional model response is invalid")
    model_rows = models["response"].get("model")
    if not isinstance(model_rows, list):
        raise RuntimeError("official regional model list is invalid")
    matches = [
        row
        for row in model_rows
        if isinstance(row, dict)
        and str(row.get("model_sku") or "") == str(command["model_sku"])
    ]

    resolved_global_item_id = str(
        resolve_global_item_id(
            shop_id,
            merchant_id,
            merchant_token,
            item_id,
        )
        or ""
    )
    resolved_global_digest = canonical_digest(
        {
            "provider": "shopee",
            "global_item_id": resolved_global_item_id,
        }
    )
    global_linkage_exact = bool(
        resolved_global_item_id
        and resolved_global_item_id == expected_global_item_id
        and resolved_global_digest
        == request.global_item_identity_digest
    )

    runtime_master = _official_global_master(
        merchant_id=merchant_id,
        merchant_token=merchant_token,
        global_item_id=expected_global_item_id,
        model_sku=str(command["model_sku"]),
        command=dict(command),
    )

    unique_model = bool(
        len(matches) == 1
        and str(matches[0].get("model_id") or "").isdigit()
    )
    prices = (
        matches[0].get("price_info")
        if unique_model
        and isinstance(matches[0].get("price_info"), list)
        else []
    )
    price_rows = [
        row
        for row in prices
        if isinstance(row, dict)
        and str(row.get("currency") or "").upper()
        == str(command["local_currency"]).upper()
    ]
    try:
        price_exact = bool(
            len(price_rows) == 1
            and Decimal(str(price_rows[0].get("original_price")))
            == Decimal(str(command["local_original_price"]))
        )
    except (InvalidOperation, TypeError, ValueError):
        price_exact = False

    image = item.get("image")
    image_urls = (
        image.get("image_url_list")
        if isinstance(image, dict)
        else None
    )
    image_count_primary_exact = bool(
        isinstance(image_urls, list)
        and len(image_urls) == int(command["approved_image_count"])
        and image_urls
        and isinstance(image_urls[0], str)
        and bool(image_urls[0].strip())
    )

    logistic_rows = item.get("logistic_info")
    logistics_shape_exact = isinstance(logistic_rows, list)
    enabled_ids: list[int] = []
    if logistics_shape_exact:
        for row in logistic_rows:
            if not isinstance(row, dict):
                logistics_shape_exact = False
                break
            if row.get("enabled") is not True:
                continue
            logistic_id = row.get("logistic_id")
            if (
                isinstance(logistic_id, bool)
                or not isinstance(logistic_id, int)
                or logistic_id < 1
            ):
                logistics_shape_exact = False
                break
            enabled_ids.append(logistic_id)
    enabled_ids = sorted(enabled_ids)
    selected_logistics_exact = bool(
        logistics_shape_exact
        and len(enabled_ids) == len(set(enabled_ids))
        and enabled_ids == list(expected_logistics)
        and canonical_digest({"ids": enabled_ids})
        == original.get("selected_logistics_digest")
    )

    hard_checks = {
        "existing_item_unique": (
            str(item.get("item_id") or "") == item_id
        ),
        "existing_model_unique": unique_model,
        "global_linkage_exact": global_linkage_exact,
        "planned_status_exact": (
            str(item.get("item_status") or "")
            == str(command["item_status"])
        ),
        "local_price_currency_exact": price_exact,
        "image_count_primary_exact": image_count_primary_exact,
        "selected_logistics_exact": selected_logistics_exact,
    }
    hard_exact = all(hard_checks.values())
    copy_observation = evaluate_shopee_regional_copy_observation(
        source_title=runtime_master["title"],
        source_description=runtime_master["description"],
        source_global_master_digest=command["approved_master_digest"],
        regional_title=item.get("item_name"),
        regional_description=item.get("description"),
        site=command["region"],
    )
    image_observation = evaluate_shopee_regional_image_observation(
        approved_count=command["approved_image_count"],
        regional_image_urls=image_urls,
        global_linkage_verified=global_linkage_exact,
    )
    observation_outcome = shopee_regional_observation_outcome(
        listing_hard_exact=hard_exact,
        copy_observation=copy_observation,
        image_observation=image_observation,
    )
    observation_acceptable = (
        observation_outcome.get("outcome") == "SUCCEEDED"
    )
    checks = {
        **hard_checks,
        "derived_observation_acceptable": observation_acceptable,
    }
    now = datetime.now(timezone.utc)
    evidence = {
        "schema_version": "shopee-regional-readback-redacted/v1",
        "authority": "shopee_official_get",
        "target_label": operation.target_label,
        "external_identity_digest": request.external_identity_digest,
        "resolved_global_item_identity_digest": resolved_global_digest,
        "listing_identity_verified": hard_exact,
        "derived_translation_status": observation_outcome.get(
            "derived_translation_status"
        ),
        "derived_image_status": observation_outcome.get(
            "derived_image_status"
        ),
        "manual_review_required": (
            observation_outcome.get("manual_review_required") is True
        ),
        "semantic_equivalence": "unverified",
        "profit_status": "unverified",
        "matched_rule_ids": list(
            observation_outcome.get("matched_rule_ids") or ()
        ),
        "observation_evidence_digest": observation_outcome.get(
            "evidence_digest"
        ),
        "image_observation_evidence_digest": image_observation.get(
            "evidence_digest"
        ),
        "image_count": len(image_urls) if isinstance(image_urls, list) else 0,
        "enabled_logistics_count": len(enabled_ids),
        "status": (
            "exact_with_manual_review"
            if observation_acceptable
            else "needs_reconciliation"
        ),
    }
    return {
        "checks": checks,
        "evidence": evidence,
        "summary": {
            "target_label": operation.target_label,
            "status": evidence["status"],
            "listing_identity_verified": hard_exact,
            "derived_translation_status": evidence[
                "derived_translation_status"
            ],
            "derived_image_status": evidence["derived_image_status"],
            "manual_review_required": evidence[
                "manual_review_required"
            ],
            "image_count": evidence["image_count"],
            "enabled_logistics_count": evidence[
                "enabled_logistics_count"
            ],
        },
        "observed_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
    }


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
            if entries is None and "item" not in page:
                total = page.get("total_count")
                if type(total) is int and total == 0 and page.get("has_next_page") is False:
                    entries = []
                else:
                    raise RuntimeError(f"Shopee {status} item list is invalid")
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


def _official_global_master(*, merchant_id: int, merchant_token: str, global_item_id: str, model_sku: str, command: dict) -> dict:
    """Read one exact global item/model pair; retain copy only in memory."""
    from modules.shopee.client import merchant_get
    from shared_platform.target_scoped_release_contracts import (
        approved_shopee_copy_digest,
        evaluate_shopee_global_image_observation,
        shopee_global_image_observation_outcome,
    )
    item_response = merchant_get("/api/v2/global_product/get_global_item_info", merchant_id, merchant_token, {"global_item_id_list": str(global_item_id)})
    if not isinstance(item_response, dict) or item_response.get("error"):
        raise RuntimeError("official global item GET failed")
    response = item_response.get("response")
    item_rows = response.get("global_item_list") if isinstance(response, dict) else None
    if not isinstance(item_rows, list) or len(item_rows) != 1 or not isinstance(item_rows[0], dict):
        raise RuntimeError("official global item must be unique")
    item = item_rows[0]
    if str(item.get("global_item_id") or "") != str(global_item_id):
        raise RuntimeError("official global item identity is invalid")
    title = item.get("global_item_name")
    description = item.get("description") or item.get("global_item_description")
    if (not isinstance(title, str) or not title.strip()
            or not isinstance(description, str) or not description.strip()):
        raise RuntimeError("official global copy shape is invalid")
    images = item.get("image") or {}
    urls = images.get("image_url_list") if isinstance(images, dict) else None
    image_ids = images.get("image_id_list") if isinstance(images, dict) else None
    copy_digest = approved_shopee_copy_digest(title, description)
    if copy_digest != command["approved_copy_digest"]:
        raise RuntimeError("official global copy digest does not match immutable command")
    observation = evaluate_shopee_global_image_observation(
        approved_count=command["approved_image_count"], official_image_urls=urls,
        official_image_ids=image_ids,
        prior_mapping_digest=command.get("approved_global_image_mapping_digest"),
    )
    outcome = shopee_global_image_observation_outcome(
        global_hard_facts_exact=True, image_observation=observation,
    )
    if outcome["execution_allowed"] is not True:
        raise RuntimeError("official global image evidence is not executable")
    model_response = merchant_get("/api/v2/global_product/get_global_model_list", merchant_id, merchant_token, {"global_item_id": int(global_item_id)})
    if not isinstance(model_response, dict) or model_response.get("error"): raise RuntimeError("official global model GET failed")
    model_data = model_response.get("response")
    rows = model_data.get("global_model") if isinstance(model_data, dict) else None
    if not isinstance(rows, list): raise RuntimeError("official global model response is invalid")
    matches = [row for row in rows if str(row.get("global_model_sku") or "") == model_sku]
    if len(matches) != 1:
        raise RuntimeError("global model SKU must be unique")
    row = matches[0]
    model_id = str(row.get("global_model_id") or "")
    tier = list(row.get("tier_index") or [])
    if not model_id.isdigit() or not tier or any(isinstance(v, bool) or not isinstance(v, int) for v in tier): raise RuntimeError("official global model identity is invalid")
    return {"title": title, "description": description, "urls": list(urls), "global_model_id": model_id, "tier_index": tier, "image_observation": observation, "image_outcome": outcome,
            "summary": {"global_item_id": str(global_item_id), "copy_digest": copy_digest, "image_count": len(urls), "global_image_snapshot_digest": observation["official_image_id_snapshot_digest"], "global_image_observation_digest": observation["evidence_digest"]}}


def inspect_existing_global(*, shop_id: int, access_token: str, global_item_id: str, model_sku: str, command: dict) -> dict:
    """GET-only proof.  Rehosted URLs are observation-only, never plan copy."""
    from modules.shopee.auth import load_tokens
    store = load_tokens(); merchant_id = int((store.get("shops", {}).get(str(shop_id), {}) or {}).get("merchant_id") or 0)
    token = str((store.get("merchants", {}).get(str(merchant_id), {}) or {}).get("access_token") or "")
    if not merchant_id or not token: raise RuntimeError("prepared merchant token is required")
    return _official_global_master(merchant_id=merchant_id, merchant_token=token, global_item_id=global_item_id, model_sku=model_sku, command=command)


def runtime_global_master(*, merchant_id: int, merchant_token: str, global_item_id: str, model_sku: str, command: dict, expected_image_snapshot_digest: str) -> dict:
    """Re-read proof-bound official facts before dispatch; URL rehosting may vary."""
    master = _official_global_master(merchant_id=merchant_id, merchant_token=merchant_token, global_item_id=global_item_id, model_sku=model_sku, command=command)
    if master["summary"]["global_image_snapshot_digest"] != expected_image_snapshot_digest:
        raise RuntimeError("official global image ID snapshot drift")
    return master


def _publish_existing_global_site(*, request, evidence: dict) -> dict:
    """One create_publish_task from the immutable command; bounded/explicit."""
    from modules.shopee.client import merchant_get, merchant_post
    from modules.shopee.auth import load_tokens
    command = request.planned_command
    shop_id, _shop_token = __import__("domains.channel_operations.target_scoped_retry_adapters", fromlist=["_prepared_shopee_credentials"])._prepared_shopee_credentials(command["region"])
    store = load_tokens(); merchant_id = int((store.get("shops", {}).get(str(shop_id), {}) or {}).get("merchant_id") or 0)
    token = str((store.get("merchants", {}).get(str(merchant_id), {}) or {}).get("access_token") or "")
    if not merchant_id or not token: raise RuntimeError("prepared merchant token is required")
    master = runtime_global_master(
        merchant_id=merchant_id, merchant_token=token,
        global_item_id=str(evidence["global_item_id"]),
        model_sku=command["model_sku"], command=command,
        expected_image_snapshot_digest=str(evidence["global_image_snapshot_digest"]),
    )
    if (master["global_model_id"] != str(evidence["global_model_id"])
            or list(master["tier_index"]) != list(evidence["global_tier_index"])):
        raise RuntimeError("official global model identity drift")
    body = {"global_item_id": int(evidence["global_item_id"]), "shop_id": int(shop_id), "shop_region": command["region"], "item": {"item_status": command["item_status"], "original_price": command["local_original_price"], "logistic": [{"logistic_id": x, "enabled": True} for x in evidence["selected_logistics_ids"]], "model": [{"tier_index": evidence["global_tier_index"], "original_price": command["local_original_price"]}]}}
    evidence["_dispatch_invoked"] = True
    try:
        response = merchant_post("/api/v2/global_product/create_publish_task", merchant_id, token, body)
    except Exception as error:
        raise ShopeeRegionalDispatchUnknownError(type(error).__name__) from error
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
    global_outcome = master["image_outcome"]
    verified = outcome.get("outcome") == "SUCCEEDED" and global_outcome.get("execution_allowed") is True
    return {"item_id": item_id, "verified": verified, "source_copy_verified": True, "manual_review_required": outcome.get("manual_review_required") is True or global_outcome.get("manual_review_required") is True, "task_status": "success", "hard_checks": hard_checks, "enabled_logistics_count": len(enabled), "image_count": len(image_urls or []), "copy_digest": master["summary"]["copy_digest"], "observation_outcome": outcome.get("outcome"), "derived_translation_status": outcome.get("derived_translation_status"), "derived_image_status": outcome.get("derived_image_status"), "global_image_status": global_outcome.get("global_image_status"), "global_image_verification_scope": global_outcome.get("global_image_verification_scope"), "global_image_url_identity_exact": False, "global_image_approved_order_exact": global_outcome.get("global_image_approved_order_exact") is True, "matched_rule_ids": sorted(set(list(outcome.get("matched_rule_ids") or []) + list(global_outcome.get("matched_rule_ids") or []))), "observation_evidence_digest": outcome.get("evidence_digest"), "global_image_observation_digest": master["image_observation"].get("evidence_digest"), "global_image_outcome_digest": global_outcome.get("evidence_digest"), "reconciliation_required": not verified, "external_writes_performed": ["shopee:regional_publish"]}


def publish_existing_global_site(*, request, evidence: dict) -> dict:
    """Classify failures at the single merchant POST dispatch boundary."""
    try:
        return _publish_existing_global_site(request=request, evidence=evidence)
    except (ShopeeRegionalPublishReconciliationError, ShopeeRegionalPreSubmitError):
        raise
    except Exception as error:
        if evidence.get("_dispatch_invoked"):
            raise ShopeeRegionalPublishReconciliationError(type(error).__name__) from error
        raise ShopeeRegionalPreSubmitError(type(error).__name__) from error
