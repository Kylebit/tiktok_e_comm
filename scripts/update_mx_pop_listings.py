"""已停用的历史 MX 工具；不创建审核卡或执行店铺动作。"""
from __future__ import annotations

import sys

# 历史映射仅作原件事实保留，本入口不消费这些数据。
MX_LISTINGS = [
    {
        "collect_box_detail_id": 1742250495,
        "seller_sku": "770002",
        "ph_product_id": "1731673032762296251",
    },
    {
        "collect_box_detail_id": 2059296237,
        "seller_sku": "770003",
        "ph_product_id": "1732379849767749563",
    },
]

STOCK = 200

RETIRED_ENTRY_CODE = "LEGACY_MX_ENTRY_RETIRED"


def main() -> int:
    sys.stderr.write(RETIRED_ENTRY_CODE + ": 该历史工具已停用，请使用 Orbit 商品上架页面或当前上架 Skill\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
