"""
联盟定向建联：指定商品 + 指定达人列表 → 批量 Target Collaboration。

API（需 Affiliate Scope）:
  - 搜索达人 marketplace
  - POST 创建 target collaboration
  - 邀请达人列表

达人列表示例 CSV（data/creator_lists/my_creators.csv）:
  creator_id,username,note
  7123456789,creator_a,美妆类
"""

import csv
from pathlib import Path

from core.config import ROOT, get


def creator_lists_dir() -> Path:
    rel = get("affiliate.creator_list_dir", "data/creator_lists")
    p = Path(rel)
    d = p if p.is_absolute() else ROOT / p
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_creator_list(name: str) -> list[dict]:
    path = creator_lists_dir() / f"{name}.csv"
    if not path.is_file():
        raise FileNotFoundError(f"未找到达人列表 {path}")
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def invite_creators(
    product_ids: list[str],
    creator_list: str,
    commission_rate_pct: float | None = None,
    shop_cipher: str | None = None,
) -> None:
    rate = commission_rate_pct or float(get("affiliate.default_commission_rate_pct", 15))
    creators = load_creator_list(creator_list)
    print(f"  [affiliate] 待实现 Target Collaboration")
    print(f"    商品: {len(product_ids)} 个")
    print(f"    达人: {len(creators)} 人（列表 {creator_list}）")
    print(f"    佣金: {rate}%")
    print(f"    店铺 cipher: {shop_cipher or '（默认全部/交互选择）'}")
    print("  API: Affiliate Target Collaboration Create + Invite")


def list_creator_lists() -> None:
    d = creator_lists_dir()
    files = sorted(d.glob("*.csv"))
    if not files:
        sample = d / "example.csv"
        sample.write_text(
            "creator_id,username,note\n7123456789012345678,example_creator,示例行请删除\n",
            encoding="utf-8",
        )
        print(f"  已创建示例列表: {sample}")
        return
    for f in files:
        n = sum(1 for _ in open(f, encoding="utf-8-sig")) - 1
        print(f"  {f.stem}  ({max(n, 0)} 人)")
