from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm")
sys.path.insert(0, str(ROOT))

from modules.miaoshou.client import post_open  # type: ignore


def _pick(data: Any) -> Any:
    if isinstance(data, dict):
        out = {}
        for key in list(data.keys())[:12]:
            value = data[key]
            if isinstance(value, (str, int, float, type(None), bool)):
                out[key] = value
            elif isinstance(value, list):
                out[key] = f"<list len={len(value)}>"
            elif isinstance(value, dict):
                out[key] = {k: ("<list>" if isinstance(v, list) else "<dict>" if isinstance(v, dict) else v) for k, v in list(value.items())[:8]}
            else:
                out[key] = str(type(value))
        return out
    return data


def main() -> None:
    keyword = sys.argv[1] if len(sys.argv) > 1 else "601099550390497"
    payloads = [
        {
            "name": "status+sourceItemIdKeyword",
            "body": {
                "pageNo": 1,
                "pageSize": 50,
                "filter": {
                    "sourceItemIdKeyword": keyword,
                    "status": "notPublished",
                },
            },
        },
        {
            "name": "sourceItemIdKeyword only",
            "body": {
                "pageNo": 1,
                "pageSize": 50,
                "filter": {
                    "sourceItemIdKeyword": keyword,
                },
            },
        },
        {
            "name": "titleKeyword guess",
            "body": {
                "pageNo": 1,
                "pageSize": 50,
                "filter": {
                    "titleKeyword": keyword,
                },
            },
        },
        {
            "name": "blank filter",
            "body": {
                "pageNo": 1,
                "pageSize": 10,
                "filter": {},
            },
        },
    ]
    out = []
    for entry in payloads:
        resp = post_open(
            "/open/v1/product/collect_box/tiktok/collect_box/search_collect_box_detail_list",
            entry["body"],
        )
        data = resp.get("data") or {}
        items = data.get("list") or data.get("detailList") or []
        rows = []
        for item in items[:10]:
            rows.append(
                {
                    "detail_id": item.get("collectBoxDetailId") or item.get("detailId"),
                    "title": item.get("title"),
                    "status": item.get("status"),
                    "edit_model": item.get("editModel"),
                    "price": item.get("price"),
                    "stock": item.get("stock"),
                    "source_item_id": item.get("sourceItemId"),
                    "gmt_modified": item.get("gmtModified"),
                    "create_time": item.get("createTime"),
                    "claim_time": item.get("claimTime"),
                    "raw_keys": sorted(item.keys()),
                    "shops": [
                        {
                            "shop_id": row.get("shopId"),
                            "site": row.get("site"),
                            "shop_nick": row.get("shopNick"),
                        }
                        for row in (item.get("collectBoxDetailShopList") or item.get("prePublishShopList") or [])
                    ],
                }
            )
        out.append({
            "probe": entry["name"],
            "body": entry["body"],
            "result": resp.get("result"),
            "message": resp.get("message"),
            "http_status": resp.get("_http_status"),
            "data_shape": _pick(data),
            "total": data.get("total"),
            "rows": rows,
        })
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
