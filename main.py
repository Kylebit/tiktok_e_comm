#!/usr/bin/env python3
"""
TikTok Shop 控制台 — 唯一入口

  python3 main.py              交互菜单
  python3 main.py init         初始化配置与数据库
  python3 main.py status       授权与店铺状态
  python3 main.py sync yesterday   一键同步昨日数据
  python3 main.py serve          打开 Web 控制台（推荐）
  python3 main.py products sync
  python3 main.py finance sync --date 2026-06-01
  python3 main.py ads sync
  python3 main.py affiliate invite --products ID1,ID2 --creators my_list
"""

import argparse
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.config import CONFIG_PATH, EXAMPLE_PATH, load_settings
from core.db import init_db


def cmd_init(_):
    if not CONFIG_PATH.is_file():
        shutil.copy(EXAMPLE_PATH, CONFIG_PATH)
        print(f"✅ 已创建 {CONFIG_PATH}")
        _maybe_fill_from_legacy()
    else:
        print(f"ℹ️  配置已存在: {CONFIG_PATH}")
    p = init_db()
    print(f"✅ 数据库: {p}")
    (ROOT / "data" / "creator_lists").mkdir(parents=True, exist_ok=True)
    (ROOT / "exports").mkdir(exist_ok=True)
    print("\n下一步:")
    print("  1. 编辑 config/settings.json（app_key、汇率、ads.advertiser_id）")
    print("  2. python3 main.py auth   或继续用 tiktok_auth.py")
    print("  3. python3 main.py status")


def _maybe_fill_from_legacy():
    """从旧脚本 tiktok_data.py 读取 app_key（若 example 仍是占位符）。"""
    legacy = ROOT / "tiktok_data.py"
    if not legacy.is_file():
        return
    text = legacy.read_text(encoding="utf-8")
    import json
    import re
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for key, pat in [("app_key", r'APP_KEY\s*=\s*"([^"]+)"'), ("app_secret", r'APP_SECRET\s*=\s*"([^"]+)"')]:
        if cfg.get(key, "").startswith("YOUR_"):
            m = re.search(pat, text)
            if m:
                cfg[key] = m.group(1)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  （已从 tiktok_data.py 填入 app_key / app_secret）")


def cmd_auth(_):
    script = ROOT / "tiktok_auth.py"
    if script.is_file():
        subprocess.run([sys.executable, str(script)], cwd=str(ROOT))
    else:
        print("请运行 OAuth 授权流程（tiktok_auth.py 待接入 core/auth）")


def cmd_status(_):
    from datetime import datetime

    from core import auth, shops
    try:
        tok = auth.ensure_valid_token()
        token = tok["access_token"]
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return
    except RuntimeError as e:
        print(f"❌ {e}")
        return
    print(f"卖家: {tok.get('seller_name', '?')}")
    print(f"Token 文件: {auth.token_path()}")
    access_exp = auth.access_expires_at(tok)
    refresh_exp = auth.refresh_expires_at(tok)
    if access_exp:
        left = (access_exp - datetime.now()).total_seconds()
        state = "已过期" if left <= 0 else f"剩余 {int(left // 3600)}h"
        print(f"Access Token 过期: {access_exp:%Y-%m-%d %H:%M} ({state})")
    if refresh_exp:
        print(f"Refresh Token 过期: {refresh_exp:%Y-%m-%d}（到期前无需重新浏览器授权）")
    try:
        shop_list = shops.list_shops(token)
        print(f"店铺 {len(shop_list)} 个:")
        for s in shop_list:
            print(f"  - {s.get('name')} [{s.get('region')}]  {s.get('cipher', '')[:24]}...")
    except Exception as e:
        print(f"❌ 拉取店铺失败: {e}")


def cmd_sync_yesterday(_):
    print("══ 同步昨日数据 ══")
    from modules import ads, finance, products
    from modules.ads import service as ads_svc
    from modules.finance import service as fin_svc
    from modules.products import service as prod_svc

    prod_svc.sync_products()
    fin_svc.sync_settlement()
    ads_svc.sync_daily_spend()
    print("\n完成（部分模块待实现，见 docs/ARCHITECTURE.md）")


def cmd_keywords_build(args):
    from modules.products.keyword_build import build_all
    regions = [args.region.upper()] if args.region else None
    paths = build_all(regions)
    for reg, p in sorted(paths.items()):
        n = len(p.read_text(encoding="utf-8").strip().splitlines()) - 1
        print(f"  ✅ {reg}: {p} ({n} 个类目)")


def build_parser():
    p = argparse.ArgumentParser(
        description="TikTok Shop 控制台",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="command")

    sub.add_parser("init", help="初始化配置与数据库").set_defaults(func=cmd_init)
    sub.add_parser("auth", help="OAuth 授权").set_defaults(func=cmd_auth)
    sub.add_parser("status", help="查看授权与店铺").set_defaults(func=cmd_status)

    psv = sub.add_parser("serve", help="启动 Web 控制台（浏览器操作）")
    psv.add_argument("--port", type=int, default=8765)
    psv.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    psv.add_argument("--page", choices=["index", "costs", "titles", "promotions", "analytics", "deactivate"], default="index")
    psv.set_defaults(
        func=lambda a: __import__("modules.products.server", fromlist=["serve"]).serve(
            port=a.port, open_browser=not a.no_browser, page=a.page
        )
    )

    sync = sub.add_parser("sync", help="批量同步")
    sync.add_argument("scope", choices=["yesterday"], nargs="?", default="yesterday")
    sync.set_defaults(func=cmd_sync_yesterday)

    prod = sub.add_parser("products", help="商品与成本")
    prod_sub = prod.add_subparsers(dest="products_cmd")
    ps = prod_sub.add_parser("sync", help="同步商品并生成成本页")
    ps.add_argument("--no-images", action="store_true", help="不拉详情图（更快）")
    ps.add_argument("--no-import", action="store_true", help="不导入 CURSOR 成本")
    ps.add_argument("--fill-seller-sku", action="store_true", help="同步后自动补全缺失商家 SKU")
    ps.set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["sync_products"]).sync_products(
            fetch_images=not a.no_images,
            import_cursor=not a.no_import,
            fill_seller_sku=a.fill_seller_sku,
        )
    )
    prod_sub.add_parser("page", help="重新生成成本页").set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["rebuild_page"]).rebuild_page()
    )
    prod_sub.add_parser("serve", help="启动 Web 控制台（同 main.py serve）").set_defaults(
        func=lambda a: __import__("modules.products.server", fromlist=["serve"]).serve(page="costs")
    )
    pss = prod_sub.add_parser("fill-seller-sku", help="为缺失商家 SKU 的商品自动分配编码")
    pss.add_argument("--dry-run", action="store_true", help="仅预览，不写入")
    pss.add_argument("--push", action="store_true", help="尝试通过 API 推送到 TikTok 全球商品")
    pss.add_argument("--no-export", action="store_true", help="不导出 CSV")
    pss.set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["fill_seller_sku"]).fill_seller_sku(
            dry_run=a.dry_run,
            push=a.push,
            export=not a.no_export,
        )
    )
    pxe = prod_sub.add_parser("export-seller-sku-xlsx", help="导出 Seller Center 批量编辑 xlsx")
    pxe.add_argument("--region", help="站点 MY/VN/TH/PH")
    pxe.add_argument("--sku-id", help="指定 SKU ID（测试用）")
    pxe.add_argument("--limit", type=int, default=1, help="导出条数，默认 1")
    pxe.set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["export_seller_sku_xlsx"]).export_seller_sku_xlsx(
            region=a.region, sku_id=a.sku_id, limit=a.limit
        )
    )
    pkb = prod_sub.add_parser("keywords-build", help="从本地商品库自动生成热搜词 CSV")
    pkb.add_argument("--region", help="仅 MY/VN/TH/PH")
    pkb.set_defaults(func=cmd_keywords_build)
    pts = prod_sub.add_parser("title-scan", help="扫描低动销商品并生成标题建议")
    pts.add_argument("--days", type=int, default=30, help="统计天数，默认 30")
    pts.add_argument("--max-units", type=int, default=1, help="销量上限（含），默认 1")
    pts.add_argument("--limit", type=int, default=30, help="最多处理商品数")
    pts.add_argument("--region", help="仅指定站点 MY/VN/TH/PH")
    pts.set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["scan_titles"]).scan_titles(
            days=a.days, max_units=a.max_units, limit=a.limit, region=a.region
        )
    )
    pla = prod_sub.add_parser("listing-scan", help="Analytics A类：高CTR 0单 → AI 标题+详情")
    pla.add_argument("--limit", type=int, default=20)
    pla.add_argument("--region", help="仅 MY/VN/TH/PH")
    pla.set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["scan_listings_analytics"]).scan_listings_analytics(
            limit=a.limit, region=a.region
        )
    )
    pas = prod_sub.add_parser("analytics-sync", help="同步 28 天商品 Analytics")
    pas.add_argument("--region", help="仅 MY/VN/TH/PH")
    pas.set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["sync_analytics"]).sync_analytics(
            region=a.region
        )
    )
    pds = prod_sub.add_parser("deactivate-scan", help="扫描零销下架候选（90天0单+低CTR）")
    pds.add_argument("--limit", type=int, default=50)
    pds.add_argument("--region", help="仅 MY/VN/TH/PH")
    pds.set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["scan_deactivate"]).scan_deactivate(
            limit=a.limit, region=a.region
        )
    )
    pdp = prod_sub.add_parser("deactivate-push", help="CLI 推送已确认的下架")
    pdp.set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["push_deactivate_cli"]).push_deactivate_cli()
    )
    ptr = prod_sub.add_parser("title-serve", help="打开标题页（同 main.py serve --page titles）")
    ptr.add_argument("--port", type=int, default=8765)
    ptr.set_defaults(
        func=lambda a: __import__("modules.products.server", fromlist=["serve"]).serve(
            port=a.port, page="titles"
        )
    )
    ptp = prod_sub.add_parser("title-push", help="CLI 推送已确认的标题（无需浏览器）")
    ptp.set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["push_titles"]).push_titles()
    )
    pps = prod_sub.add_parser("promo-scan", help="扫描促销活动中低动销商品并生成折扣建议")
    pps.add_argument("--days", type=int, default=30)
    pps.add_argument("--max-units", type=int, default=1)
    pps.add_argument("--limit", type=int, default=30)
    pps.add_argument("--region", help="仅 MY/VN/TH/PH")
    pps.add_argument("--scope", choices=["adjust", "add", "flash", "all"], default="adjust")
    pps.add_argument("--mode", choices=["velocity", "analytics"], default="velocity",
                     help="analytics = A类高CTR 0单")
    pps.set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["scan_promos"]).scan_promos(
            days=a.days, max_units=a.max_units, limit=a.limit, region=a.region,
            scope=a.scope, mode=a.mode
        )
    )
    ppp = prod_sub.add_parser("promo-push", help="CLI 推送已确认的促销折扣")
    ppp.set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["push_promos"]).push_promos()
    )
    pcc = prod_sub.add_parser("coupon-scan", help="生成优惠券建议（需手动到后台创建）")
    pcc.add_argument("--region", help="仅 MY/VN/TH/PH")
    pcc.add_argument("--limit", type=int, default=4)
    pcc.set_defaults(
        func=lambda a: __import__("modules.products.promotions", fromlist=["scan_coupon_suggestions"]).scan_coupon_suggestions(
            region=a.region, limit=a.limit
        )
    )
    pc = prod_sub.add_parser("cost", help="成本维护")
    pc_sub = pc.add_subparsers(dest="cost_cmd")
    pcs = pc_sub.add_parser("set", help="设置 SKU 成本")
    pcs.add_argument("sku_id")
    pcs.add_argument("cost_cny", type=float)
    pcs.add_argument("--note", default="")
    pcs.set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["set_cost"]).set_cost(
            a.sku_id, a.cost_cny, a.note
        )
    )
    pc_sub.add_parser("list", help="列出成本").set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["list_costs"]).list_costs()
    )

    fin = sub.add_parser("finance", help="结算与利润")
    fin_sub = fin.add_subparsers(dest="finance_cmd")
    fs = fin_sub.add_parser("sync", help="拉取结算")
    fs.add_argument("--date", help="YYYY-MM-DD，默认昨天")
    fs.set_defaults(
        func=lambda a: __import__("modules.finance.service", fromlist=["sync_settlement"]).sync_settlement(
            datetime.strptime(a.date, "%Y-%m-%d").date() if a.date else None
        )
    )
    fp = fin_sub.add_parser("profit", help="利润汇总")
    fp.add_argument("--days", type=int, default=7)
    fp.set_defaults(
        func=lambda a: __import__("modules.finance.service", fromlist=["show_profit_summary"]).show_profit_summary(
            a.days
        )
    )

    ads = sub.add_parser("ads", help="广告数据（Marketing API）")
    ads_sub = ads.add_subparsers(dest="ads_cmd")
    ads_sub.add_parser("sync", help="同步广告消耗").set_defaults(
        func=lambda a: __import__("modules.ads.service", fromlist=["sync_daily_spend"]).sync_daily_spend()
    )
    ar = ads_sub.add_parser("report", help="查看报表")
    ar.add_argument("--days", type=int, default=7)
    ar.set_defaults(
        func=lambda a: __import__("modules.ads.service", fromlist=["show_report"]).show_report(a.days)
    )

    aff = sub.add_parser("affiliate", help="联盟定向建联")
    aff_sub = aff.add_subparsers(dest="affiliate_cmd")
    aff_sub.add_parser("lists", help="达人列表").set_defaults(
        func=lambda a: __import__("modules.affiliate.service", fromlist=["list_creator_lists"]).list_creator_lists()
    )
    inv = aff_sub.add_parser("invite", help="批量定向邀请")
    inv.add_argument("--products", required=True, help="商品 ID，逗号分隔")
    inv.add_argument("--creators", required=True, help="达人列表名（不含 .csv）")
    inv.add_argument("--commission", type=float, help="佣金 %")
    inv.add_argument("--shop", help="shop_cipher")
    inv.set_defaults(
        func=lambda a: __import__("modules.affiliate.service", fromlist=["invite_creators"]).invite_creators(
            a.products.split(","), a.creators, a.commission, a.shop
        )
    )

    return p


MENU = """
╔══════════════════════════════════════╗
║     TikTok Shop 控制台               ║
╠══════════════════════════════════════╣
║  1  初始化 (init)                    ║
║  2  授权 (auth)                      ║
║  3  状态 (status)                    ║
║  4  同步昨日全套数据                 ║
║  5  商品同步 + 成本页                ║
║  6  打开 Web 控制台                  ║
║  7  结算同步                         ║
║  8  广告同步                         ║
║  9  联盟达人列表                     ║
║  0  退出                             ║
╚══════════════════════════════════════╝
"""


def interactive():
    actions = {
        "1": lambda: cmd_init(None),
        "2": lambda: cmd_auth(None),
        "3": lambda: cmd_status(None),
        "4": lambda: cmd_sync_yesterday(None),
        "5": lambda: __import__("modules.products.service", fromlist=["sync_products"]).sync_products(),
        "6": lambda: __import__("modules.products.server", fromlist=["serve"]).serve(),
        "7": lambda: __import__("modules.finance.service", fromlist=["sync_settlement"]).sync_settlement(),
        "8": lambda: __import__("modules.ads.service", fromlist=["sync_daily_spend"]).sync_daily_spend(),
        "9": lambda: __import__("modules.affiliate.service", fromlist=["list_creator_lists"]).list_creator_lists(),
    }
    while True:
        print(MENU)
        choice = input("请选择: ").strip()
        if choice in ("0", "9", "q", "quit", "exit"):
            break
        fn = actions.get(choice)
        if fn:
            try:
                fn()
            except Exception as e:
                print(f"❌ {e}")
        else:
            print("无效选项")
        print()


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.command:
        if hasattr(args, "func"):
            args.func(args)
        else:
            parser.print_help()
        return
    try:
        load_settings()
    except FileNotFoundError:
        print("首次使用请先运行: python3 main.py init\n")
    interactive()


if __name__ == "__main__":
    main()
