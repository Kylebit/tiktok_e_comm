"""已停用的历史 MX 工具；不创建审核卡或执行店铺动作。"""
from __future__ import annotations

import sys

# 历史映射仅作原件事实保留，本入口不消费这些数据。
STOCK = 200

MX_QUEUE: list[dict] = [
    {"match_key": "0016", "tk_detail_id": 1842250865, "master_sku": "770016", "master_product_id": "1731886576733095867", "master_region": "PH"},
    {"match_key": "0017", "tk_detail_id": 2439741653, "master_sku": "770017", "master_product_id": "1731814164151109563", "master_region": "PH"},
    {"match_key": "0018", "tk_detail_id": 1666910516, "master_sku": "770018", "master_product_id": "1731502168399316923", "master_region": "PH"},
    {"match_key": "0021", "tk_detail_id": 2227606548, "master_sku": "770021", "master_product_id": "1732993365279541179", "master_region": "PH"},
    {"match_key": "0022", "tk_detail_id": 1903332860, "master_sku": "770022", "master_product_id": "1731994556145371067", "master_region": "PH"},
    {"match_key": "0023", "tk_detail_id": 2166177827, "master_sku": "770023", "master_product_id": "1732753170443765691", "master_region": "PH"},
    {"match_key": "0025", "tk_detail_id": 1783989545, "master_sku": "770025", "master_product_id": "1731762445353322427", "master_region": "PH"},
    {"match_key": "0026", "tk_detail_id": 2742689723, "master_sku": "660026", "master_product_id": "1734659190752577467", "master_region": "MY"},
]

MX_GROUP: list[dict] = [
    {
        "match_keys": ["0010", "0011", "0012", "0013"],
        "tk_detail_id": 1693450013,
        "master_product_id": "1731565249412499387",
        "master_region": "PH",
    },
]

BLOCKED: list[dict] = [
    {"match_key": "0027", "reason": "shop.db 无目录/成本，请先录入"},
]

RETIRED_ENTRY_CODE = "LEGACY_MX_ENTRY_RETIRED"


def main() -> int:
    sys.stderr.write(RETIRED_ENTRY_CODE + ": 该历史工具已停用，请使用 Orbit 商品上架页面或当前上架 Skill\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
