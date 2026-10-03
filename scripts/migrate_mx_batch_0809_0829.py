"""已停用的历史 MX 工具；不创建审核卡或执行店铺动作。"""
from __future__ import annotations

import sys

# 历史映射仅作原件事实保留，本入口不消费这些数据。
STOCK = 200

JOBS: list[dict] = [
    {"keys": ["0809"], "common_id": 3579516303, "pid": "1733828085385693115", "region": "MY"},
    {"keys": ["0810"], "common_id": 3735437658, "pid": "1731475798019049403", "region": "PH"},
    {"keys": ["0811", "0812", "0813"], "common_id": 3735437690, "pid": "1732799684701226939", "region": "PH"},
    {"keys": ["0814", "0815"], "common_id": 3735437729, "pid": "1732510670006749115", "region": "PH"},
    {"keys": ["0823", "0824"], "common_id": 3735437644, "pid": "1731571872335628219", "region": "PH"},
    {"keys": ["0829"], "common_id": 3735437656, "pid": "1731484883560073147", "region": "PH"},
]

RETIRED_ENTRY_CODE = "LEGACY_MX_ENTRY_RETIRED"


def main() -> int:
    sys.stderr.write(RETIRED_ENTRY_CODE + ": 该历史工具已停用，请使用 Orbit 商品上架页面或当前上架 Skill\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
