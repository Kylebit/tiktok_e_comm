from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[3]
if __name__ == "__main__" and (not (REPO_ROOT / '.git').exists() or not all((REPO_ROOT / name).is_file() for name in (
    "core/config.py", "modules/sourcing/new_product_workbench.py",
    "shared_platform/publication_rounds.py",
))):
    raise SystemExit("COMPLETE_AGENT_SOURCE_REQUIRED: use the selected repository's scripts/repo_bound_agent_entry.py --profile <absolute-profile> --entry delist")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
ALL_TARGETS = (
    "tiktok:LH_PH", "tiktok:LH_MY", "tiktok:LH_TH", "tiktok:LH_VN",
    "tiktok:HB_PH", "tiktok:HB_MY", "tiktok:HB_TH", "tiktok:HB_VN",
    "tiktok:MX", "tiktok:GB",
    "shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN",
    "ozon:RU",
)
DIRECT_TIKTOK_TARGETS = {
    "PH": "tiktok:LH_PH", "MY": "tiktok:LH_MY",
    "TH": "tiktok:LH_TH", "VN": "tiktok:LH_VN",
}
MIAOSHOU_TIKTOK_TARGETS = {
    "tiktok:HB_PH", "tiktok:HB_MY", "tiktok:HB_TH", "tiktok:HB_VN",
    "tiktok:MX", "tiktok:GB",
}
DOWN_TIKTOK = {
    "DEACTIVATE", "DEACTIVATED", "SELLER_DEACTIVATED",
    "SUSPENDED", "FROZEN", "DRAFT",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _tail4(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-4:].zfill(4) if digits else ""


def _wanted(values: Iterable[str]) -> tuple[str, ...]:
    cleaned = tuple(dict.fromkeys(_tail4(value) for value in values if _tail4(value)))
    if not cleaned:
        raise ValueError("at least one numeric Seller SKU is required")
    return cleaned


def _report_dir(skus: tuple[str, ...]) -> Path:
    return REPO_ROOT / "reports" / "product-delisting" / "-".join(skus)


def _shop_target(name: str, region: str) -> str:
    folded = name.casefold()
    if "homebloom" in folded:
        return f"tiktok:HB_{region}"
    if region in DIRECT_TIKTOK_TARGETS:
        return DIRECT_TIKTOK_TARGETS[region]
    if region == "MX":
        return "tiktok:MX"
    if region in {"GB", "UK"}:
        return "tiktok:GB"
    return ""


def _local_tiktok_rows(skus: tuple[str, ...]) -> list[dict[str, Any]]:
    from core.db import connect_readonly

    conn = connect_readonly()
    try:
        shops = {
            str(row["cipher"]): dict(row)
            for row in conn.execute("SELECT cipher, shop_id, name, region FROM shops")
        }
        rows = [dict(row) for row in conn.execute(
            "SELECT sku_id, shop_cipher, product_id, seller_sku, product_name, sku_name, status FROM products"
        )]
    finally:
        conn.close()
    selected_products = {
        (str(row.get("shop_cipher") or ""), str(row.get("product_id") or ""))
        for row in rows if _tail4(row.get("seller_sku")) in skus
    }
    out: list[dict[str, Any]] = []
    for cipher, product_id in sorted(selected_products):
        product_rows = [
            row for row in rows
            if str(row.get("shop_cipher") or "") == cipher
            and str(row.get("product_id") or "") == product_id
        ]
        meta = shops.get(cipher, {})
        region = str(meta.get("region") or "").upper()
        target = _shop_target(str(meta.get("name") or ""), region)
        all_skus = sorted({_tail4(row.get("seller_sku")) for row in product_rows if _tail4(row.get("seller_sku"))})
        out.append({
            "target_label": target or f"tiktok:UNKNOWN_{region or 'REGION'}",
            "platform": "tiktok",
            "store_name": str(meta.get("name") or ""),
            "shop_id": str(meta.get("shop_id") or ""),
            "shop_cipher": cipher,
            "product_id": product_id,
            "title": next((str(row.get("product_name") or "") for row in product_rows if row.get("product_name")), ""),
            "requested_skus": sorted(set(all_skus) & set(skus)),
            "all_product_skus": all_skus,
            "current_status": next((str(row.get("status") or "") for row in product_rows), ""),
            "identity_source": "readonly_catalog_db",
            "action": "DEACTIVATE_PRODUCT",
        })
    return out


def _historical_tiktok_rows(skus: tuple[str, ...]) -> list[dict[str, Any]]:
    """Recover exact storefront identities from durable publication evidence.

    Historical evidence is only an identity seed. Every recovered product is
    still re-read from TikTok before it can become executable.
    """
    matching_offers: set[str] = set()
    prep_root = REPO_ROOT / "reports" / "product-preparation"
    def collect_skus(value: object) -> set[str]:
        found: set[str] = set()
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key) in {"seller_sku", "model_sku", "global_model_sku"}:
                    sku = _tail4(child)
                    if sku:
                        found.add(sku)
                found.update(collect_skus(child))
        elif isinstance(value, list):
            for child in value:
                found.update(collect_skus(child))
        return found

    for offer_dir in prep_root.iterdir() if prep_root.is_dir() else ():
        if not offer_dir.is_dir():
            continue
        found: set[str] = set()
        for path in offer_dir.glob("*.json"):
            try:
                found.update(collect_skus(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, json.JSONDecodeError):
                continue
        if set(skus).issubset(found):
            matching_offers.add(offer_dir.name)
    if not matching_offers:
        return []

    identities: dict[str, dict[str, Any]] = {}
    for offer_id in matching_offers:
        root = REPO_ROOT / "reports" / "product-publication" / offer_id
        paths = sorted(root.rglob("description-media-repair.json"), key=lambda p: p.stat().st_mtime)
        for path in paths:
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for target in report.get("targets") or []:
                if not isinstance(target, Mapping):
                    continue
                label = str(target.get("target_label") or "")
                product_id = str(target.get("product_id") or "")
                if label.startswith("tiktok:LH_") and product_id:
                    identities[label] = {
                        "target_label": label,
                        "platform": "tiktok",
                        "store_name": str(target.get("shop_name") or "LivelyHive"),
                        "product_id": product_id,
                        "title": "",
                        "requested_skus": list(skus),
                        "all_product_skus": list(skus),
                        "current_status": str((target.get("after") or target.get("before") or {}).get("status") or ""),
                        "identity_source": str(path.relative_to(REPO_ROOT)).replace("\\", "/"),
                        "action": "DEACTIVATE_PRODUCT",
                    }
    if not identities:
        return []

    try:
        from core import auth, shops
        authorized = shops.list_shops(auth.access_token())
    except Exception:
        authorized = []
    by_target: dict[str, Mapping[str, Any]] = {}
    for shop in authorized:
        region = str(shop.get("region") or "").upper()
        label = _shop_target(str(shop.get("name") or shop.get("shop_name") or ""), region)
        if label:
            by_target[label] = shop
    out: list[dict[str, Any]] = []
    for label, row in identities.items():
        shop = by_target.get(label)
        if not shop:
            continue
        row["shop_id"] = str(shop.get("id") or shop.get("shop_id") or "")
        row["shop_cipher"] = str(shop.get("cipher") or shop.get("shop_cipher") or "")
        if row["shop_cipher"]:
            out.append(row)
    return out


def _live_tiktok_rows(skus: tuple[str, ...], targets=None, *, call_guard=None, tiktok_runtime=None) -> list[dict[str, Any]]:
    """Discover exact active products from the official API, independent of DB sync."""
    from core import auth, shops
    from core.api_client import post

    out: list[dict[str, Any]] = []
    if call_guard: call_guard()
    token = tiktok_runtime.tiktok_token() if tiktok_runtime else auth.access_token()
    for shop in (tiktok_runtime.tiktok_shops(token) if tiktok_runtime else shops.list_shops(token)):
        name = str(shop.get("name") or shop.get("shop_name") or "")
        region = str(shop.get("region") or shop.get("region_code") or "").upper()
        target = _shop_target(name, region)
        cipher = str(shop.get("cipher") or shop.get("shop_cipher") or "")
        if not target or not cipher:
            continue
        if targets is not None and target not in targets:
            continue
        products: list[Mapping[str, Any]] = []
        page_token = ""
        seen_tokens: set[str] = set()
        declared_total: int | None = None
        while True:
            if page_token in seen_tokens:
                raise RuntimeError("TikTok active-product cursor repeated")
            seen_tokens.add(page_token)
            query = {"shop_cipher": cipher, "page_size": "100"}
            if page_token:
                query["page_token"] = page_token
            if call_guard: call_guard()
            send = (lambda path, token, query, body: tiktok_runtime.tiktok_request('POST',path,token,query,body)) if tiktok_runtime else post
            response = send(
                "/product/202309/products/search",
                token,
                query,
                {"status": "ACTIVATE"},
            )
            if response.get("code") != 0:
                raise RuntimeError("TikTok active-product search failed")
            data = response.get("data")
            rows = data.get("products") if isinstance(data, Mapping) else None
            total = data.get("total_count") if isinstance(data, Mapping) else None
            next_token = data.get("next_page_token") if isinstance(data, Mapping) else None
            if (
                not isinstance(rows, list)
                or any(not isinstance(row, Mapping) for row in rows)
                or type(total) is not int
                or total < 0
                or type(next_token) is not str
                or (declared_total is not None and total != declared_total)
            ):
                raise RuntimeError("TikTok active-product search shape invalid")
            declared_total = total
            products.extend(rows)
            print(
                f"live-plan {target}: {len(products)}/{declared_total} active products",
                file=sys.stderr,
                flush=True,
            )
            if not next_token:
                if len(products) != declared_total:
                    raise RuntimeError("TikTok active-product search incomplete")
                break
            page_token = next_token
        for product in products:
            sku_rows = product.get("skus")
            if not isinstance(sku_rows, list) or any(
                not isinstance(row, Mapping) for row in sku_rows
            ):
                raise RuntimeError("TikTok active-product SKU shape invalid")
            all_skus = sorted({
                _tail4(row.get("seller_sku"))
                for row in sku_rows if _tail4(row.get("seller_sku"))
            })
            if not set(all_skus) & set(skus):
                continue
            out.append({
                "target_label": target,
                "platform": "tiktok",
                "store_name": name,
                "shop_id": str(shop.get("id") or shop.get("shop_id") or ""),
                "shop_cipher": cipher,
                "product_id": str(product.get("id") or product.get("product_id") or ""),
                "title": str(product.get("title") or ""),
                "requested_skus": sorted(set(all_skus) & set(skus)),
                "all_product_skus": all_skus,
                "current_status": "ACTIVATE",
                "identity_source": "tiktok_official_active_product_search",
                "action": "DEACTIVATE_PRODUCT",
            })
    return out


def _durable_miaoshou_tiktok_rows(skus: tuple[str, ...]) -> list[dict[str, Any]]:
    """Recover exact API-less storefront identities from the newest saved evidence."""
    paths = sorted(
        (REPO_ROOT / "reports" / "product-discounts").glob(
            "miaoshou-product-identity-evidence-*.json"
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not paths:
        return []
    try:
        payload = json.loads(paths[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[dict[str, Any]] = []
    for item in payload.get("rows") or []:
        if not isinstance(item, Mapping):
            continue
        target = str(item.get("target_label") or "")
        product_id = str(item.get("product_id") or "")
        all_skus = sorted({_tail4(value) for value in item.get("seller_skus") or [] if _tail4(value)})
        if (
            target not in MIAOSHOU_TIKTOK_TARGETS
            or not product_id
            or str(item.get("status") or "") != "EXACT_IDENTITY"
            or not set(all_skus) & set(skus)
        ):
            continue
        out.append({
            "target_label": target,
            "platform": "tiktok",
            "store_name": "HomeBloom" if ":HB_" in target else "LivelyHive",
            "product_id": product_id,
            "title": str(item.get("title") or ""),
            "requested_skus": sorted(set(all_skus) & set(skus)),
            "all_product_skus": all_skus,
            "current_status": "PROVIDER_READBACK_REQUIRED",
            "identity_source": str(paths[0].relative_to(REPO_ROOT)).replace("\\", "/"),
            "action": "MIAOSHOU_DELIST",
        })
    return out


def _live_shopee_rows(skus: tuple[str, ...], targets=None, *, call_guard=None, tiktok_runtime=None) -> list[dict[str, Any]]:
    """Discover exact active Shopee items and models through the official API."""
    from concurrent.futures import ThreadPoolExecutor

    from modules.shopee.auth import ensure_shop_token
    from modules.shopee.shops import SEA_REGIONS, sync_shop_ids
    from modules.shopee.sync import _fetch_item_ids, _fetch_items_base, _rows_from_item

    if call_guard: call_guard()
    shop_ids = {key.upper(): int(value) for key, value in sync_shop_ids().items()}
    out: list[dict[str, Any]] = []
    for region in sorted(SEA_REGIONS):
        if targets is not None and f'shopee:{region}' not in targets:
            continue
        shop_id = shop_ids.get(region)
        if not shop_id:
            continue
        if call_guard: call_guard()
        token = ensure_shop_token(shop_id)
        if call_guard: call_guard()
        item_ids = _fetch_item_ids(shop_id, token,call_guard=call_guard) if call_guard else _fetch_item_ids(shop_id, token)
        print(
            f"live-plan shopee:{region}: {len(item_ids)} active item identities",
            file=sys.stderr,
            flush=True,
        )
        if call_guard: call_guard()
        items = _fetch_items_base(shop_id, token, item_ids,call_guard=call_guard) if call_guard else _fetch_items_base(shop_id, token, item_ids)
        if {
            int(item.get("item_id")) for item in items if item.get("item_id") is not None
        } != set(item_ids):
            raise RuntimeError("Shopee active-item snapshot is incomplete")
        def fetch_models(item):
            if call_guard: call_guard()
            model_rows, _ = _rows_from_item(
                shop_id, region, token, item, use_cache=False, **({'call_guard':call_guard} if call_guard else {})
            )
            return item, model_rows

        # These are independent provider reads. Keep concurrency bounded to
        # reduce wall time without changing write budgets or using cache state.
        with ThreadPoolExecutor(max_workers=4) as pool:
            observed_items = pool.map(fetch_models, items)
            for item_index, observed in enumerate(observed_items, 1):
                item, model_rows = observed
                all_skus = sorted({
                    _tail4(row.get("seller_sku"))
                    for row in model_rows if _tail4(row.get("seller_sku"))
                })
                if item_index % 50 == 0 or item_index == len(items):
                    print(
                        f"live-plan shopee:{region}: {item_index}/{len(items)} item details",
                        file=sys.stderr,
                        flush=True,
                    )
                if not set(all_skus) & set(skus):
                    continue
                out.append({
                    "target_label": f"shopee:{region}",
                    "platform": "shopee",
                    "store_name": f"Shopee {region}",
                    "shop_id": str(shop_id),
                    "product_id": str(item.get("item_id") or ""),
                    "title": str(item.get("item_name") or ""),
                    "requested_skus": sorted(set(all_skus) & set(skus)),
                    "all_product_skus": all_skus,
                    "current_status": "NORMAL",
                    "identity_source": "shopee_official_active_item_and_models",
                    "action": "UNLIST_ITEM",
                })
    return out


def _shopee_rows(skus: tuple[str, ...]) -> list[dict[str, Any]]:
    path = REPO_ROOT / "data" / "shopee_global_sku_map.json"
    if not path.is_file():
        # This file is a legacy identity cache, not provider evidence.  A clean
        # checkout may legitimately omit it; live planning must still continue
        # through the official Shopee discovery performed above.
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for global_id, entry in data.items():
        if not isinstance(entry, Mapping):
            continue
        models = entry.get("models") or []
        all_skus = sorted({
            _tail4(row.get("global_model_sku"))
            for row in models if isinstance(row, Mapping) and _tail4(row.get("global_model_sku"))
        })
        if not set(all_skus) & set(skus):
            continue
        for region, item in (entry.get("shop_items") or {}).items():
            if not isinstance(item, Mapping):
                continue
            out.append({
                "target_label": f"shopee:{str(region).upper()}",
                "platform": "shopee",
                "store_name": f"Shopee {str(region).upper()}",
                "shop_id": str(item.get("shop_id") or ""),
                "product_id": str(item.get("item_id") or ""),
                "global_product_id": str(global_id),
                "title": str(entry.get("title") or ""),
                "requested_skus": sorted(set(all_skus) & set(skus)),
                "all_product_skus": all_skus,
                "current_status": "LOCAL_IDENTITY_ONLY",
                "identity_source": "shopee_global_sku_map",
                "action": "UNLIST_ITEM",
            })
    return out


def _ozon_rows(skus: tuple[str, ...]) -> list[dict[str, Any]]:
    from modules.catalog.ozon_data import load_ozon_by_key

    out: list[dict[str, Any]] = []
    for key, row in load_ozon_by_key().items():
        if _tail4(key) not in skus and _tail4(row.get("seller_sku")) not in skus:
            continue
        out.append({
            "target_label": "ozon:RU",
            "platform": "ozon",
            "store_name": "Ozon RU",
            "product_id": str(row.get("product_id") or ""),
            "offer_id": str(row.get("offer_id") or row.get("seller_sku") or ""),
            "title": str(row.get("product_name") or ""),
            "requested_skus": sorted(set(skus) & {_tail4(key), _tail4(row.get("seller_sku"))}),
            "all_product_skus": [_tail4(key)],
            "current_status": str(row.get("status") or ""),
            "identity_source": "ozon_catalog_snapshot",
            "action": "ARCHIVE_PRODUCT",
        })
    return out


def _live_verify(row: dict[str, Any], *, call_guard=None, tiktok_runtime=None) -> dict[str, Any]:
    platform = row["platform"]
    try:
        if call_guard: call_guard()
        if platform == "tiktok":
            from core import auth
            from modules.products.sync import _fetch_product_detail
            detail = (tiktok_runtime.tiktok_detail(tiktok_runtime.tiktok_token(),row['shop_cipher'],row['product_id'])
                      if tiktok_runtime else _fetch_product_detail(auth.access_token(), row["shop_cipher"], row["product_id"]))
            live_skus = sorted({_tail4(sku.get("seller_sku")) for sku in detail.get("skus") or [] if _tail4(sku.get("seller_sku"))})
            row["all_product_skus"] = live_skus
            row["requested_skus"] = sorted(set(live_skus) & set(row["requested_skus"]))
            row["current_status"] = str(detail.get("product_status") or detail.get("status") or "")
            row["identity_source"] = "tiktok_official_product_detail"
        elif platform == "shopee":
            from modules.shopee.auth import ensure_shop_token
            from modules.shopee.client import shop_get
            shop_id = int(row["shop_id"])
            token = ensure_shop_token(shop_id)
            if call_guard: call_guard()
            base = shop_get("/api/v2/product/get_item_base_info", shop_id, token, {"item_id_list": row["product_id"]},
                            **({'call_guard':call_guard} if call_guard else {}))
            if base.get("error"):
                raise RuntimeError(base.get("message") or str(base))
            items = (base.get("response") or {}).get("item_list") or []
            if len(items) != 1:
                raise RuntimeError("official item identity not unique")
            item = items[0]
            if call_guard: call_guard()
            models = shop_get("/api/v2/product/get_model_list", shop_id, token, {"item_id": int(row["product_id"])},
                              **({'call_guard':call_guard} if call_guard else {}))
            if models.get("error"):
                raise RuntimeError(models.get("message") or str(models))
            model_rows = (models.get("response") or {}).get("model") or []
            live_skus = sorted({_tail4(m.get("model_sku")) for m in model_rows if _tail4(m.get("model_sku"))})
            if not live_skus and _tail4(item.get("item_sku")):
                live_skus = [_tail4(item.get("item_sku"))]
            row["all_product_skus"] = live_skus
            row["requested_skus"] = sorted(set(live_skus) & set(row["requested_skus"]))
            row["current_status"] = str(item.get("item_status") or "")
            row["identity_source"] = "shopee_official_item_and_models"
        elif platform == "ozon":
            from modules.ozon.client import ozon_post
            from modules.ozon.product_lifecycle import fetch_offer_info
            def bound_post(*args,**kwargs):
                if call_guard: call_guard()
                return ozon_post(*args,**kwargs)
            info = fetch_offer_info(bound_post, row["offer_id"])
            if info is None:
                row["current_status"] = "NOT_FOUND"
            else:
                row["product_id"] = str(info.get("id") or row.get("product_id") or "")
                row["current_status"] = "ARCHIVED" if info.get("is_archived") else "ACTIVE"
                row["identity_source"] = "ozon_official_info_list"
    except Exception as exc:
        row["live_read_error"] = str(exc)[:500]
    return row


def build_plan(skus: tuple[str, ...], *, live: bool = True) -> dict[str, Any]:
    live_tiktok = _live_tiktok_rows(skus) if live else []
    live_tiktok_labels = {row["target_label"] for row in live_tiktok}
    local_tiktok = [
        row for row in _local_tiktok_rows(skus)
        if row["target_label"] not in live_tiktok_labels
    ]
    known_tiktok_labels = live_tiktok_labels | {row["target_label"] for row in local_tiktok}
    historical_tiktok = [
        row for row in _historical_tiktok_rows(skus)
        if row["target_label"] not in known_tiktok_labels
    ]
    known_tiktok_labels.update(row["target_label"] for row in historical_tiktok)
    miaoshou_tiktok = [
        row for row in _durable_miaoshou_tiktok_rows(skus)
        if row["target_label"] not in known_tiktok_labels
    ]
    live_shopee = _live_shopee_rows(skus) if live else []
    live_shopee_labels = {row["target_label"] for row in live_shopee}
    mapped_shopee = [
        row for row in _shopee_rows(skus)
        if row["target_label"] not in live_shopee_labels
    ]
    found = (
        live_tiktok + local_tiktok + historical_tiktok + miaoshou_tiktok
        + live_shopee + mapped_shopee + _ozon_rows(skus)
    )
    by_target: dict[str, list[dict[str, Any]]] = {}
    for row in found:
        by_target.setdefault(row["target_label"], []).append(row)
    targets: list[dict[str, Any]] = []
    for label in ALL_TARGETS:
        matches = by_target.get(label, [])
        if not matches:
            needs_provider_discovery = (
                label.startswith("tiktok:HB_")
                or (label.startswith("shopee:") and not live)
            )
            targets.append({
                "target_label": label,
                "platform": label.split(":", 1)[0],
                "requested_skus": list(skus),
                "status": "NEEDS_PROVIDER_DISCOVERY" if needs_provider_discovery else "NOT_FOUND",
                "executable": False,
                "reason": (
                    "official provider discovery is required before a negative result"
                    if needs_provider_discovery
                    else "no exact listing identity in current canonical evidence"
                ),
            })
            continue
        if len(matches) != 1:
            targets.append({
                "target_label": label, "platform": label.split(":", 1)[0],
                "requested_skus": list(skus), "status": "BLOCKED_AMBIGUOUS_IDENTITY",
                "executable": False, "candidates": matches,
            })
            continue
        row = matches[0]
        provider_only = row["target_label"] in MIAOSHOU_TIKTOK_TARGETS
        if live and not provider_only and not str(row.get("identity_source") or "").startswith((
            "tiktok_official_", "shopee_official_",
        )):
            row = _live_verify(row)
        mixed = bool(set(row.get("all_product_skus") or []) - set(skus))
        complete = set(row.get("requested_skus") or []) == set(skus)
        row["status"] = (
            "BLOCKED_MIXED_PRODUCT" if mixed else
            "BLOCKED_INCOMPLETE_SKU_SET" if not complete else
            "NEEDS_PROVIDER_EXECUTION" if provider_only else
            "BLOCKED_LIVE_READ" if row.get("live_read_error") else
            "READY"
        )
        row["executable"] = row["status"] == "READY"
        targets.append(row)
    body = {
        "schema_version": "product-delist-plan/v1",
        "created_at": _now(),
        "scope": "all",
        "requested_skus": list(skus),
        "expected_targets": list(ALL_TARGETS),
        "targets": targets,
    }
    body["plan_digest"] = _digest(body)
    return body


def _verify_plan(plan: Mapping[str, Any]) -> None:
    supplied = str(plan.get("plan_digest") or "")
    payload = dict(plan)
    payload.pop("plan_digest", None)
    if not supplied or supplied != _digest(payload):
        raise ValueError("plan digest mismatch")


def _execute_one(row: Mapping[str, Any], *, call_guard=None, tiktok_runtime=None) -> dict[str, Any]:
    result = {"target_label": row["target_label"], "product_id": row.get("product_id"),
              "requested_skus": list(row.get("requested_skus") or []),
              "all_product_skus": list(row.get("all_product_skus") or []),
              "attempted": False, "external_write_count": 0}
    if row.get("status") != "READY" or row.get("executable") is not True:
        return {**result, "outcome": row.get("status") or "BLOCKED", "verified": False}
    try:
        if call_guard: call_guard()
        if row["platform"] == "tiktok":
            from core import auth
            from modules.products.deactivate import push_deactivate
            from modules.products.sync import _fetch_product_detail
            current = str(row.get("current_status") or "").upper()
            if current in DOWN_TIKTOK:
                return {**result, "outcome": "VERIFIED_ALREADY_DELISTED", "verified": True}
            result["attempted"] = True
            if call_guard: call_guard()
            if tiktok_runtime:
                response=tiktok_runtime.tiktok_request('POST','/product/202309/products/deactivate',
                    tiktok_runtime.tiktok_token(),{'shop_cipher':row['shop_cipher']},{'product_ids':[row['product_id']]})
                ok,error=response.get('code')==0,'official TikTok deactivation not confirmed'
            else:
                ok, error = push_deactivate(auth.access_token(), row["shop_cipher"], [row["product_id"]])
            result["external_write_count"] = 1
            if not ok:
                raise RuntimeError(error)
            if call_guard: call_guard()
            detail = (tiktok_runtime.tiktok_detail(tiktok_runtime.tiktok_token(),row['shop_cipher'],row['product_id'])
                      if tiktok_runtime else _fetch_product_detail(auth.access_token(), row["shop_cipher"], row["product_id"]))
            status = str(detail.get("product_status") or detail.get("status") or "").upper()
            result["readback_status"] = status
            result["verified"] = status in DOWN_TIKTOK
        elif row["platform"] == "shopee":
            from modules.shopee.auth import ensure_shop_token
            from modules.shopee.client import shop_get, shop_post
            shop_id, item_id = int(row["shop_id"]), int(row["product_id"])
            token = ensure_shop_token(shop_id)
            if call_guard: call_guard()
            if str(row.get("current_status") or "").upper() == "UNLIST":
                return {**result, "outcome": "VERIFIED_ALREADY_DELISTED", "verified": True}
            result["attempted"] = True
            response = shop_post("/api/v2/product/unlist_item", shop_id, token, {"item_list": [{"item_id": item_id, "unlist": True}]},
                                 **({'call_guard':call_guard} if call_guard else {}))
            result["external_write_count"] = 1
            if response.get("error"):
                raise RuntimeError(response.get("message") or str(response))
            if call_guard: call_guard()
            base = shop_get("/api/v2/product/get_item_base_info", shop_id, token, {"item_id_list": str(item_id)},
                            **({'call_guard':call_guard} if call_guard else {}))
            if base.get("error"):
                raise RuntimeError(base.get("message") or str(base))
            items = (base.get("response") or {}).get("item_list") or []
            status = str(items[0].get("item_status") or "").upper() if len(items) == 1 else ""
            result["readback_status"] = status
            result["verified"] = status == "UNLIST"
        elif row["platform"] == "ozon":
            from modules.ozon.client import ozon_post
            from modules.ozon.product_lifecycle import archive_offer, fetch_offer_info
            if str(row.get("current_status") or "").upper() == "ARCHIVED":
                return {**result, "outcome": "VERIFIED_ALREADY_DELISTED", "verified": True}
            result["attempted"] = True
            def bound_post(*args,**kwargs):
                if call_guard: call_guard()
                return ozon_post(*args,**kwargs)
            response = archive_offer(bound_post, int(row["product_id"]))
            result["external_write_count"] = 1
            if response.get("result") is not True:
                raise RuntimeError(str(response))
            info = fetch_offer_info(bound_post, row["offer_id"])
            result["readback_status"] = "ARCHIVED" if info and info.get("is_archived") else "ACTIVE_OR_UNKNOWN"
            result["verified"] = bool(info and info.get("is_archived"))
        if result.get("verified"):
            result["outcome"] = "VERIFIED_DELISTED"
        else:
            result["outcome"] = "UNKNOWN"
    except Exception as exc:
        result["outcome"] = "UNKNOWN" if result["attempted"] else "BLOCKED"
        result["verified"] = False
        result["error"] = str(exc)[:500]
    return result


def execute(plan: Mapping[str, Any], *, operation_owner=None, call_guard=None, tiktok_runtime=None) -> dict[str, Any]:
    _verify_plan(plan)
    from shared_platform.operations_domain_guard import begin_delisting, finish_delisting
    guard = begin_delisting(plan, REPO_ROOT, operation_owner=operation_owner)
    if guard is not None and not guard[2]['acquired']:
        raise ValueError('existing delisting operation requires readback, not replay')
    results = [(_execute_one(row,call_guard=call_guard,tiktok_runtime=tiktok_runtime) if call_guard or tiktok_runtime else _execute_one(row)) for row in plan.get("targets") or []]
    result = {
        "schema_version": "product-delist-execution/v1",
        "created_at": _now(),
        "plan_digest": plan["plan_digest"],
        "requested_skus": plan["requested_skus"],
        "external_write_count": sum(int(row.get("external_write_count") or 0) for row in results),
        "verified_count": sum(row.get("verified") is True for row in results),
        "targets": results,
    }
    finish_delisting(
        guard,
        result,
        target_labels={
            row["target_label"]
            for row in plan.get("targets") or []
            if row.get("status") == "READY" and row.get("executable") is True
        },
    )
    return result


def readback(plan: Mapping[str, Any], *, call_guard=None, tiktok_runtime=None) -> dict[str, Any]:
    """Read current provider state without rebuilding or replacing the plan.

    The immutable plan digest identifies the authorized target set.  Dynamic
    provider status belongs only in this readback artifact.
    """
    _verify_plan(plan)
    results: list[dict[str, Any]] = []
    for frozen in plan.get("targets") or []:
        row = dict(frozen)
        if not row.get("product_id") or row.get("status") in {
            "NOT_FOUND", "NEEDS_PROVIDER_DISCOVERY", "BLOCKED_AMBIGUOUS_IDENTITY",
        }:
            results.append({
                "target_label": row.get("target_label"),
                "product_id": row.get("product_id"),
                "outcome": row.get("status") or "BLOCKED",
                "verified": False,
                "current_status": row.get("current_status"),
            })
            continue
        live = _live_verify(row,call_guard=call_guard,tiktok_runtime=tiktok_runtime) if call_guard or tiktok_runtime else _live_verify(row)
        status = str(live.get("current_status") or "").upper()
        platform = str(live.get("platform") or "")
        verified = not live.get('live_read_error') and (
            status in DOWN_TIKTOK if platform == "tiktok" else
            status == "UNLIST" if platform == "shopee" else
            status == "ARCHIVED" if platform == "ozon" else
            False
        )
        results.append({
            "target_label": live.get("target_label"),
            "product_id": live.get("product_id"),
            "requested_skus": live.get("requested_skus"),
            "all_product_skus": live.get("all_product_skus"),
            "identity_source": live.get("identity_source"),
            "current_status": live.get("current_status"),
            "read_error": live.get("live_read_error"),
            "outcome": "VERIFIED_DELISTED" if verified else "NOT_VERIFIED_DELISTED",
            "verified": verified,
        })
    return {
        "schema_version": "product-delist-readback/v1",
        "created_at": _now(),
        "plan_digest": plan["plan_digest"],
        "requested_skus": plan["requested_skus"],
        "external_write_count": 0,
        "verified_count": sum(row.get("verified") is True for row in results),
        "targets": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan_cmd = sub.add_parser("plan")
    plan_cmd.add_argument("--sku", action="append", required=True)
    plan_cmd.add_argument("--scope", default="all", choices=("all",))
    plan_cmd.add_argument("--no-live", action="store_true")
    execute_cmd = sub.add_parser("execute")
    execute_cmd.add_argument("--plan", required=True)
    readback_cmd = sub.add_parser("readback")
    readback_cmd.add_argument("--plan", required=True)
    args = parser.parse_args()

    if args.command == "plan":
        skus = _wanted(args.sku)
        output = _report_dir(skus) / "delist-plan.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = build_plan(skus, live=not args.no_live)
    else:
        source = Path(args.plan).resolve()
        payload_in = json.loads(source.read_text(encoding="utf-8"))
        if args.command == "readback":
            payload = readback(payload_in)
            output = source.parent / "delist-readback.json"
        else:
            payload = execute(payload_in)
            output = source.parent / "delist-execution.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    print(json.dumps({
        "plan_digest": payload.get("plan_digest"),
        "external_write_count": payload.get("external_write_count", 0),
        "verified_count": payload.get("verified_count", 0),
        "targets": len(payload.get("targets") or []),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
