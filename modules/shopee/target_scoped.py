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


def compatible_prepared_logistics(*, shop_id: int, access_token: str) -> list[int]:
    rows = shop_get("/api/v2/logistics/get_channel_list", shop_id, access_token).get("response", {}).get("logistics_channel_list", ())
    return [int(row.get("logistics_channel_id") or row.get("logistic_id")) for row in rows if row.get("enabled") and int(row.get("logistics_channel_id") or row.get("logistic_id") or 0) != 50052]


def publish_existing_global_site(*, request, evidence: dict) -> dict:
    """Intentionally explicit seam: no global create/update, sync, or refresh."""
    raise RuntimeError("existing-global regional publisher must be injected with prepared payload")
