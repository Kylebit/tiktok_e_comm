"""结算拉取 + 利润报表。"""

from datetime import date, datetime, timedelta, timezone

from core import auth, shops
from core.db import init_db


def sync_settlement(target: date | None = None) -> None:
    """TODO: 封装 tiktok_settlement 逻辑，写入 settlement_lines + 利润汇总。"""
    init_db()
    token = auth.access_token()
    shop_list = shops.list_shops(token)
    day = target or (datetime.now(timezone.utc).date() - timedelta(days=1))
    print(f"  [finance] 待实现：拉取 {day.isoformat()} 结算（{len(shop_list)} 店）")
    print("  临时可用旧脚本: python3 tiktok_settlement.py")


def show_profit_summary(days: int = 1) -> None:
    print(f"  [finance] 待实现：近 {days} 天利润汇总（依赖 settlement_lines + ad_spend_daily + sku_costs）")
