"""TikTok Marketing API — GMV Max 等真实广告消耗。"""

import json
from datetime import date, timedelta
from pathlib import Path

from core.config import ROOT, get


def ads_token_path() -> Path:
    rel = get("ads.access_token_file", "tiktok_ads_tokens.json")
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


def sync_daily_spend(target: date | None = None) -> None:
    """
    从 TikTok Marketing API 拉取 GMV Max 报表（spend / cost）。
    文档: GET /open_api/v1.3/gmv_max/report/get/
    需在 ads.tiktok.com 申请 Marketing API，与 Shop Open API 授权分离。
    """
    day = target or (date.today() - timedelta(days=1))
    advertiser_id = get("ads.advertiser_id", "")
    if not advertiser_id:
        print("  ⚠️  请在 config/settings.json 填写 ads.advertiser_id")
    if not ads_token_path().is_file():
        print(f"  ⚠️  未找到 {ads_token_path()}，请先完成 Marketing API 授权")
        print("  文档: https://business-api.tiktok.com/portal/docs")
        return
    print(f"  [ads] 待实现：拉取 {day.isoformat()} 广告消耗 (advertiser={advertiser_id or '?'})")
    print("  接口: gmv_max/report/get  metrics=[spend,cost,orders,gross_revenue]")


def show_report(days: int = 7) -> None:
    print(f"  [ads] 待实现：近 {days} 天广告报表（读 ad_spend_daily 表）")
