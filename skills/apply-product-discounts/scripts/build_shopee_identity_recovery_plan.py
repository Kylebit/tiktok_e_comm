from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.shopee.auth import ensure_shop_token  # noqa: E402
from modules.shopee.client import shop_get  # noqa: E402
from modules.shopee.shops import sync_shop_ids  # noqa: E402


MODEL_PATH = "/api/v2/product/get_model_list"
ITEM_PATH = "/api/v2/product/get_item_base_info"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _unwrap(payload: Mapping[str, Any], path: str) -> Mapping[str, Any]:
    error = str(payload.get("error") or "").strip()
    if error and error != "-":
        raise RuntimeError(f"{path}: {error}: {payload.get('message') or ''}")
    response = payload.get("response")
    if not isinstance(response, Mapping):
        raise RuntimeError(f"{path}: response object missing")
    return response


def build(source_plan: Path, execution_report: Path, product_ids: set[str]) -> dict[str, Any]:
    plan = json.loads(source_plan.read_text(encoding="utf-8"))
    execution = json.loads(execution_report.read_text(encoding="utf-8"))
    blocked = {
        (str(row.get("target_label")), str(row.get("product_id"))): row
        for row in execution.get("results", [])
        if isinstance(row, Mapping)
        and row.get("status") == "BLOCKED"
        and "SKU set drifted" in str(row.get("error") or "")
    }
    shop_ids = {region.upper(): int(value) for region, value in sync_shop_ids().items()}
    recovered: list[dict[str, Any]] = []
    for action in plan.get("actions", []):
        if not isinstance(action, Mapping):
            continue
        product_id = str(action.get("product_id") or "")
        if product_id not in product_ids:
            continue
        if (str(action.get("target_label")), product_id) not in blocked or not str(action.get("target_label") or "").startswith("shopee:"):
            raise RuntimeError(f"{product_id}: no matching fail-closed SKU drift result")
        region = str(action["target_label"]).split(":", 1)[1].upper()
        shop_id = shop_ids[region]
        token = ensure_shop_token(shop_id)
        model_response = _unwrap(
            shop_get(MODEL_PATH, shop_id, token, {"item_id": int(product_id)}),
            MODEL_PATH,
        )
        models = model_response.get("model")
        observed_skus = sorted(
            str(row.get("model_sku") or "").strip()
            for row in models or [] if isinstance(row, Mapping)
        )
        if not isinstance(models, list) or not observed_skus or any(not sku for sku in observed_skus):
            raise RuntimeError(f"{product_id}: current model identity unavailable")
        if len(observed_skus) != len(set(observed_skus)):
            raise RuntimeError(f"{product_id}: duplicate current model SKU")
        item_response = _unwrap(
            shop_get(ITEM_PATH, shop_id, token, {"item_id_list": product_id}),
            ITEM_PATH,
        )
        items = item_response.get("item_list")
        item = items[0] if isinstance(items, list) and len(items) == 1 and isinstance(items[0], Mapping) else None
        if not item or str(item.get("item_id") or "") != product_id or str(item.get("item_status") or "").upper() != "NORMAL":
            raise RuntimeError(f"{product_id}: item identity or status drifted")
        refreshed = dict(action)
        refreshed["status"] = "REFERENCE_REQUIRES_FROZEN_PLAN"
        refreshed["approval_status"] = "NOT_APPROVED"
        refreshed["suggested_seller_skus"] = observed_skus
        refreshed["reference_discount_percent"] = refreshed.pop("discount_percent", None)
        refreshed["discount_percent"] = None
        refreshed["identity_evidence"] = {
            "source": "SHOPEE_OFFICIAL_CURRENT_ITEM_AND_MODEL_READBACK",
            "previous_seller_skus": list(action.get("seller_skus") or []),
            "observed_seller_skus": observed_skus,
            "item_status": "NORMAL",
        }
        refreshed.pop("action_digest", None)
        refreshed["action_digest"] = _digest(refreshed)
        recovered.append(refreshed)
    if {row["product_id"] for row in recovered} != product_ids:
        raise RuntimeError("not every requested product received exact current identity")
    return {
        "schema_version": "shopee-identity-recovery-reference/v2",
        "approval_status": "NOT_APPROVED",
        "source_plan": source_plan.as_posix(),
        "source_execution_report": execution_report.as_posix(),
        "external_writes": 0,
        "actions": recovered,
        "plan_digest": _digest(recovered),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-plan", type=Path, required=True)
    parser.add_argument("--execution-report", type=Path, required=True)
    parser.add_argument("--product-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build(args.source_plan, args.execution_report, set(args.product_id))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"count": len(payload["actions"]), "plan_digest": payload["plan_digest"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
