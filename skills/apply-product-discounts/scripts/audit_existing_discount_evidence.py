from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from modules.shopee.auth import ensure_shop_token  # noqa: E402
from modules.shopee.shops import sync_shop_ids  # noqa: E402
from modules.shopee.client import shop_get  # noqa: E402


SOURCE = ROOT / "reports/product-discounts/shopee-uncovered-products-20260901.json"
OUTPUT = ROOT / "reports/product-discounts/shopee-existing-sku-discount-evidence-20260901.json"


def _shopee_discount_items(shop_id: int, token: str, discount_id: int) -> list[Mapping[str, Any]]:
    """Read-only helper migrated from r4; never import the retired executor."""
    path = "/api/v2/discount/get_discount"
    rows: list[Mapping[str, Any]] = []
    seen_items: set[int] = set()
    for page_no in range(1, 1001):
        payload = shop_get(path, shop_id, token, {"discount_id": discount_id, "page_no": page_no, "page_size": 100})
        if not isinstance(payload, Mapping) or str(payload.get("error") or "").strip() not in {"", "-"}:
            raise RuntimeError("Shopee discount read failed")
        response = payload.get("response")
        if not isinstance(response, Mapping) or type(response.get("more")) is not bool:
            raise RuntimeError("Shopee discount pagination marker unavailable")
        current = response.get("item_list")
        if not isinstance(current, list) or any(not isinstance(row, Mapping) for row in current):
            raise RuntimeError("Shopee discount item page malformed")
        for row in current:
            item_id = row.get("item_id")
            if type(item_id) is not int or item_id <= 0 or item_id in seen_items:
                raise RuntimeError("Shopee discount item pagination identity is ambiguous")
            seen_items.add(item_id)
        rows.extend(current)
        if response["more"] is False:
            return rows
        if not current:
            raise RuntimeError("Shopee discount pagination did not advance")
    raise RuntimeError("Shopee discount page limit exceeded")


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _identity_number(value: object, *, allow_zero: bool = False) -> int | None:
    if type(value) is int:
        number = value
    elif type(value) is str and value.isascii() and value.isdigit():
        number = int(value)
    else:
        return None
    return number if number >= (0 if allow_zero else 1) else None


def _discount_percent(model: Mapping[str, Any]) -> int | None:
    original = _decimal(model.get("model_original_price") or model.get("original_price"))
    promotion = _decimal(model.get("model_promotion_price") or model.get("promotion_price"))
    if original is None or promotion is None or original <= 0 or not Decimal("0") < promotion < original:
        return None
    raw = (Decimal("1") - promotion / original) * Decimal("100")
    rounded = int(raw.quantize(Decimal("1")))
    # Whole-currency provider quantization can move the observed rate slightly.
    return rounded if 0 < rounded < 100 and abs(raw - Decimal(rounded)) <= Decimal("0.55") else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read current Shopee discounts as reference evidence; never apply discounts.")
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--database", type=Path, default=ROOT / "data/shop.db")
    args = parser.parse_args(argv)
    source = json.loads(args.source.read_text(encoding="utf-8"))
    shop_ids = {region.upper(): int(shop_id) for region, shop_id in sync_shop_ids().items()}
    conn = sqlite3.connect(args.database.resolve().as_uri()+"?mode=ro", uri=True)
    model_skus: dict[tuple[int, int, int], set[str]] = defaultdict(set)
    try:
        cached_rows = conn.execute(
            "select shop_id, item_id, model_id, seller_sku from shopee_products where seller_sku<>''"
        ).fetchall()
    finally:
        conn.close()
    for shop_id_value, item_id_value, model_id_value, sku_value in cached_rows:
        shop_id_key = _identity_number(shop_id_value)
        item_id_key = _identity_number(item_id_value)
        model_id_key = _identity_number(model_id_value, allow_zero=True)
        sku = str(sku_value).strip()
        if shop_id_key is not None and item_id_key is not None and model_id_key is not None and sku:
            model_skus[(shop_id_key, item_id_key, model_id_key)].add(sku)
    observations: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    identity_gaps = []
    for store in source.get("stores", []):
        region = str(store.get("region") or "").upper()
        shop_id = shop_ids[region]
        token = ensure_shop_token(shop_id)
        for activity in store.get("activities", []):
            discount_id = int(activity.get("discount_id") or activity.get("activity_id") or 0)
            if not discount_id or activity.get("excluded_from_single_product_discount_audit") is True:
                continue
            for item in _shopee_discount_items(shop_id, token, discount_id):
                item_id = _identity_number(item.get("item_id"))
                models = item.get("model_list")
                if not isinstance(models, list) or not models:
                    identity_gaps.append({"shop_id":shop_id,"item_id":item_id,"model_id":None,"seller_sku":None,
                                          "reason":"OFFICIAL_MODEL_LIST_UNAVAILABLE","discount_id":discount_id})
                    continue
                for model in models:
                    if not isinstance(model, Mapping):
                        identity_gaps.append({"shop_id":shop_id,"item_id":item_id,"model_id":None,"seller_sku":None,
                                              "reason":"OFFICIAL_MODEL_ROW_INVALID","discount_id":discount_id})
                        continue
                    model_id = _identity_number(model.get("model_id"), allow_zero=True)
                    matches = model_skus.get((shop_id, item_id, model_id), set())
                    if model_id is None or len(matches) != 1:
                        identity_gaps.append({"shop_id":shop_id,"item_id":item_id,"model_id":model_id,"seller_sku":None,
                                              "reason":"MODEL_IDENTITY_CONFLICT" if len(matches)>1 else "MODEL_IDENTITY_UNBOUND",
                                              "discount_id":discount_id})
                        continue
                    sku = next(iter(matches))
                    percent = _discount_percent(model)
                    if sku and percent is not None:
                        observations[(region, sku)].append({
                            "region": region,
                            "seller_sku": sku,
                            "discount_percent": percent,
                            "shop_id": shop_id,
                            "discount_id": discount_id,
                            "item_id": item_id,
                            "model_id": model_id,
                            "model_original_price": str(model.get("model_original_price") or model.get("original_price")),
                            "model_promotion_price": str(model.get("model_promotion_price") or model.get("promotion_price")),
                            "source_kind": "OFFICIAL_DISCOUNT_LOCAL_MODEL_IDENTITY_REFERENCE",
                            "identity_source": "LOCAL_CACHED_SHOP_ITEM_MODEL",
                            "execution_authorized": False,
                        })
    evidence = []
    conflicts = []
    for (region, sku), rows in sorted(observations.items()):
        percents = sorted({row["discount_percent"] for row in rows})
        target = evidence if len(percents) == 1 else conflicts
        target.append({
            "target_label": f"shopee:{region}",
            "seller_sku": sku,
            "discount_percent": percents[0] if len(percents) == 1 else None,
            "observed_discount_percents": percents,
            "observations": rows,
        })
    result = {
        "schema_version": "official-existing-sku-discount-evidence/v1",
        "external_writes": 0,
        "approval_status": "NOT_APPROVED",
        "evidence": evidence,
        "conflicts": conflicts,
        "identity_gaps": identity_gaps,
        "summary": {"unique_evidence": len(evidence), "conflicts": len(conflicts), "identity_gaps":len(identity_gaps)},
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), **result["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
