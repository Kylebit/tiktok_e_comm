"""已停用的历史 MX 工具；不创建审核卡或执行店铺动作。"""
from __future__ import annotations

import sys

# 历史映射仅作原件事实保留，本入口不消费这些数据。
STOCK = 200

MX_BATCH = [
    {
        "match_key": "0001",
        "common_collect_box_detail_id": 3579516381,
        "master_sku": "660001",
        "master_product_id": "1731455264245712827",
        "master_region": "MY",
    },
    {
        "match_key": "0005",
        "common_collect_box_detail_id": 3579516409,
        "master_sku": "770005",
        "master_product_id": "1731708191695734715",
        "master_region": "PH",
    },
    {
        "match_key": "0006",
        "common_collect_box_detail_id": 3579516334,
        "master_sku": "770006",
        "master_product_id": "1733047965872588731",
        "master_region": "PH",
    },
    {
        "match_key": "0007",
        "common_collect_box_detail_id": 3579516410,
        "master_sku": "770007",
        "master_product_id": "1731502295464839099",
        "master_region": "PH",
    },
    {
        "match_key": "0008",
        "common_collect_box_detail_id": 3579516378,
        "master_sku": "770008",
        "master_product_id": "1731516982024767419",
        "master_region": "PH",
    },
]

RETIRED_ENTRY_CODE = "LEGACY_MX_ENTRY_RETIRED"


def main() -> int:
    sys.stderr.write(RETIRED_ENTRY_CODE + ": 该历史工具已停用，请使用 Orbit 商品上架页面或当前上架 Skill\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
