"""已停用的历史 MX 工具；不创建审核卡或执行店铺动作。"""
from __future__ import annotations

import sys

# 历史映射仅作原件事实保留，本入口不消费这些数据。
STOCK = 200

SKIP_MK = {"0808"}

SKIP_GROUP_DE = {"0818", "0819", "0820", "0821", "0822"}

COMMON_JOBS: list[dict] = [
    {
        "keys": ["0800", "0801"],
        "common_id": 3729935844,
        "pid": "1733510891803150267",
        "region": "PH",
    },
    {
        "keys": ["0802"],
        "common_id": 3729935846,
        "pid": "1733510761614903227",
        "region": "PH",
    },
    {
        "keys": ["0803", "0804"],
        "common_id": 3729935852,
        "pid": "1734083720865220539",
        "region": "PH",
    },
    {
        "keys": ["0805", "0806", "0807"],
        "common_id": 3729935797,
        "pid": "1733291356586084283",
        "region": "PH",
    },
    {
        "keys": ["0827"],
        "common_id": 3729935853,
        "pid": "1733828087686137787",
        "region": "PH",
    },
]

RETIRED_ENTRY_CODE = "LEGACY_MX_ENTRY_RETIRED"


def main() -> int:
    sys.stderr.write(RETIRED_ENTRY_CODE + ": 该历史工具已停用，请使用 Orbit 商品上架页面或当前上架 Skill\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
