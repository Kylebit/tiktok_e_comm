"""结算拉取 + 利润报表。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from core.db import init_db


def sync_settlement(target: date | None = None) -> None:
    """拉取 TikTok 结算订单到本地 Income_Data（默认四国）。"""
    init_db()
    from modules.finance import th_orders_pull as pull

    day = target or (datetime.now(timezone.utc).date() - timedelta(days=1))
    result = pull.run_pull(
        platforms=["tiktok"],
        regions=["TH", "MY", "VN", "PH"],
        start=day,
        end=day,
        lookback_days=1,
    )
    if result.get("ok"):
        tk = (result.get("platforms") or {}).get("tiktok") or {}
        print(
            f"  [finance] 已拉取 {day.isoformat()} · "
            f"shops={tk.get('shop_count')} · regions={tk.get('regions')}"
        )
    else:
        print(f"  [finance] 拉取失败: {result.get('error')}")


def show_profit_summary(days: int = 1) -> None:
    print(f"  [finance] 待实现：近 {days} 天利润汇总（依赖 settlement_lines + ad_spend_daily + sku_costs）")
    print("  可用：python scripts/pull_th_orders.py  与  /sku-profit 探针")
