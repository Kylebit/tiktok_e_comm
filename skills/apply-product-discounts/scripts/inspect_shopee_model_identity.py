from __future__ import annotations

import argparse
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


def inspect(execution_report: Path) -> dict[str, Any]:
    report = json.loads(execution_report.read_text(encoding="utf-8"))
    shop_ids = {region.upper(): int(value) for region, value in sync_shop_ids().items()}
    rows: list[dict[str, Any]] = []
    for result in report.get("results", []):
        if not isinstance(result, Mapping) or result.get("status") != "BLOCKED":
            continue
        target = str(result.get("target_label") or "")
        if not target.startswith("shopee:"):
            continue
        region = target.split(":", 1)[1].upper()
        item_id = int(result["product_id"])
        shop_id = shop_ids[region]
        token = ensure_shop_token(shop_id)
        payload = shop_get(MODEL_PATH, shop_id, token, {"item_id": item_id})
        item_payload = shop_get(ITEM_PATH, shop_id, token, {"item_id_list": str(item_id)})
        response = payload.get("response") if isinstance(payload, Mapping) else None
        models = response.get("model") if isinstance(response, Mapping) else None
        item_response = item_payload.get("response") if isinstance(item_payload, Mapping) else None
        item_list = item_response.get("item_list") if isinstance(item_response, Mapping) else None
        item = item_list[0] if isinstance(item_list, list) and len(item_list) == 1 and isinstance(item_list[0], Mapping) else None
        rows.append({
            "target_label": target,
            "product_id": str(item_id),
            "expected_seller_skus": result.get("seller_skus") or [],
            "api_error": payload.get("error") if isinstance(payload, Mapping) else "malformed_payload",
            "api_message": payload.get("message") if isinstance(payload, Mapping) else "",
            "response_keys": sorted(response) if isinstance(response, Mapping) else [],
            "model_count": len(models) if isinstance(models, list) else None,
            "observed_models": [
                {
                    "model_id": str(model.get("model_id") or ""),
                    "model_sku": str(model.get("model_sku") or ""),
                    "status": str(model.get("status") or ""),
                }
                for model in models or [] if isinstance(model, Mapping)
            ],
            "item_readback": {
                "item_id": str(item.get("item_id") or "") if item else "",
                "item_sku": str(item.get("item_sku") or "") if item else "",
                "item_status": str(item.get("item_status") or "") if item else "",
                "price_info": item.get("price_info") if item else None,
            },
        })
    return {
        "schema_version": "shopee-model-identity-inspection/v1",
        "external_writes": 0,
        "source_report": execution_report.as_posix(),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execution-report",
        type=Path,
        default=ROOT / "reports/product-discounts/shopee-discount-execution-20260901.json",
    )
    args = parser.parse_args()
    print(json.dumps(inspect(args.execution_report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
