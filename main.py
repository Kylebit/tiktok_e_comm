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


def _force_utf8_stdio() -> None:
    """Keep Unicode log messages from crashing Windows background services."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass


_force_utf8_stdio()

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
    (ROOT / "data" / "digest").mkdir(parents=True, exist_ok=True)
    (ROOT / "exports").mkdir(exist_ok=True)
    print("\n下一步:")
    print("  1. 编辑 config/settings.json（app_key、汇率、feishu.webhook_url）")
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
        subprocess.run([sys.executable, str(script), *sys.argv[2:]], cwd=str(ROOT))
    else:
        print("请运行 OAuth 授权流程（tiktok_auth.py 待接入 core/auth）")


def _shopee_publish(args) -> None:
    import json
    from modules.shopee.publish import publish_match_key

    result = publish_match_key(
        args.match_key,
        args.region,
        dry_run=args.dry_run,
        global_only=not args.publish_shops,
        publish_shops=args.publish_shops,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def _shopee_sync_group(args) -> None:
    import json
    from modules.shopee.publish_group import sync_tk_group

    keys = [k.strip() for k in args.keys.replace(";", ",").split(",") if k.strip()]
    result = sync_tk_group(keys, region=args.region)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def _shopee_publish_group(args) -> None:
    import json
    from modules.shopee.publish_group import publish_tk_group, register_tk_group

    keys = [k.strip() for k in args.keys.replace(";", ",").split(",") if k.strip()]
    if args.register:
        result = register_tk_group(keys, args.global_item_id, region=args.region)
    else:
        result = publish_tk_group(keys, region=args.region, dry_run=args.dry_run, tier_name=args.tier)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def _shopee_update_global(args) -> None:
    import json
    from modules.shopee.publish import update_global_match_key

    result = update_global_match_key(args.match_key, args.region)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def _shopee_sync() -> None:
    from modules.shopee.sync import sync_all

    print("\n══ Shopee 商品同步（MY/VN/TH/PH 主店）══")
    stats = sync_all()
    print(f"\n✅ 完成: {stats['shops']} 店 · {stats['items']} 商品 · {stats['skus']} SKU/变体")
    print("对比: python3 main.py shopee compare")


def _shopee_profit(args) -> None:
    from modules.shopee.orders import run_month_profit

    print(f"\n══ Shopee 月度利润 {args.month}（MY/VN/TH/PH 主店）══")
    run_month_profit(args.month)


def _shopee_auth_guide() -> None:
    from modules.shopee.auth import auth_partner_url
    print(auth_partner_url())
    print(
        """
下一步：
1. 复制上面链接 → 浏览器地址栏粘贴打开（不要当搜索词）
2. 中国大陆若 partner.shopeemobile.com 打不开，在 config/settings.json 的 shopee 里加：
   "auth_region": "cn"
   然后重新 python3 main.py shopee auth-url（会用 openplatform.shopee.cn）
3. 登录 Shopee 卖家主账号 → 勾选 Auth Merchant + 四国店铺 → 授权
4. 浏览器会跳到 open.shopee.com?code=xxx&main_account_id=yyy
5. 从地址栏复制 code，执行：
   python3 main.py shopee token --code <code> --main-account-id <id>
6. python3 main.py shopee status 确认"""
    )


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

    tok = sub.add_parser("tokens", help="各平台 Token 自动刷新")
    tok_sub = tok.add_subparsers(dest="tokens_cmd")
    tok_sub.add_parser("refresh", help="刷新 TikTok + Shopee access_token").set_defaults(
        func=lambda a: _tokens_refresh(a),
    )
    tok_sub.add_parser("status", help="查看 token 过期时间").set_defaults(
        func=lambda a: _tokens_status(a),
    )

    psv = sub.add_parser("serve", help="启动 Web 控制台（浏览器操作）")
    psv.add_argument("--port", type=int, default=8765)
    psv.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    psv.add_argument("--startup-refresh", action="store_true", help="启动后刷新 token；默认关闭以保证本地 UI 先可用")
    psv.add_argument(
        "--page",
        choices=["index", "catalog", "settlement", "costs", "titles", "images", "sourcing", "promotions", "analytics", "deactivate"],
        default="index",
    )
    psv.set_defaults(
        func=lambda a: __import__("modules.products.server", fromlist=["serve"]).serve(
            port=a.port, open_browser=not a.no_browser, page=a.page, startup_refresh=a.startup_refresh
        )
    )

    treasury = sub.add_parser("treasury", help="启动 Orbit Treasury（独立新品发布台）")
    treasury.add_argument("--port", type=int, default=8766)
    treasury.set_defaults(
        func=lambda a: __import__("modules.sourcing.new_product_server", fromlist=["serve"]).serve(port=a.port)
    )

    rus = sub.add_parser("rus", help="启动 Orbit Rus（独立俄罗斯业务台）")
    rus.add_argument("--port", type=int, default=8767)
    rus.set_defaults(
        func=lambda a: __import__("modules.ozon.rus_server", fromlist=["serve"]).serve(port=a.port)
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
    prod_sub.add_parser("serve", help="启动 Web 控制台（同 main.py serve）").set_defaults(
        func=lambda a: __import__("modules.products.server", fromlist=["serve"]).serve(page="index")
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
    pcg = prod_sub.add_parser(
        "ctr-gpm",
        help="MY LivelyHive CTR/GPM 双优选品 → 达人建联候选清单",
    )
    pcg.add_argument("--region", default="MY", help="本期仅 MY")
    pcg.add_argument("--days", type=int, default=30, help="窗口天数，默认 30")
    pcg.set_defaults(
        func=lambda a: __import__(
            "modules.products.service", fromlist=["run_ctr_gpm_boost"]
        ).run_ctr_gpm_boost(region=a.region, days=a.days)
    )
    pds = prod_sub.add_parser("deactivate-scan", help="扫描零销下架候选（90天0单+低CTR）")
    pds.add_argument("--limit", type=int, default=50)
    pds.add_argument("--region", help="仅 MY/VN/TH/PH")
    pds.set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["scan_deactivate"]).scan_deactivate(
            limit=a.limit, region=a.region
        )
    )
    pis = prod_sub.add_parser("image-scan", help="Analytics B类：低CTR 0单 → AI 主图候选")
    pis.add_argument("--limit", type=int, default=10)
    pis.add_argument("--variants", type=int, help="每商品候选张数，默认读 settings images.variants_per_product")
    pis.add_argument("--region", help="仅 MY/VN/TH/PH")
    pis.set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["scan_images"]).scan_images(
            limit=a.limit, region=a.region, variants=a.variants
        )
    )
    pdp = prod_sub.add_parser("deactivate-push", help="CLI 推送已确认的下架")
    pdp.set_defaults(
        func=lambda a: __import__("modules.products.service", fromlist=["push_deactivate_cli"]).push_deactivate_cli()
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

    dig = sub.add_parser("digest", help="运营日报（飞书）")
    dig_sub = dig.add_subparsers(dest="digest_cmd")
    dig_sub.add_parser("preview", help="终端预览日报").set_defaults(
        func=lambda a: __import__("modules.hub.service", fromlist=["preview_digest"]).preview_digest()
    )
    ds = dig_sub.add_parser("send", help="发送日报到飞书")
    ds.add_argument("--dry-run", action="store_true", help="只预览不发送")
    ds.set_defaults(
        func=lambda a: __import__("modules.hub.service", fromlist=["send_digest"]).send_digest(
            dry_run=a.dry_run
        )
    )

    fs = sub.add_parser("feishu", help="飞书双向机器人")
    fs_sub = fs.add_subparsers(dest="feishu_cmd")
    fs_sub.add_parser("setup", help="打印自建应用配置说明").set_defaults(
        func=lambda a: __import__("modules.hub.feishu_bot", fromlist=["print_setup_guide"]).print_setup_guide()
    )
    fs_sub.add_parser("bot", help="启动长连接（接收 @ 指令）").set_defaults(
        func=lambda a: __import__("modules.hub.feishu_bot", fromlist=["run_websocket_bot"]).run_websocket_bot()
    )

    sp = sub.add_parser("shopee", help="Shopee Open API")
    sp_sub = sp.add_subparsers(dest="shopee_cmd")
    sp_sub.add_parser("status", help="授权与 token 状态").set_defaults(
        func=lambda a: print(__import__("modules.shopee.auth", fromlist=["status_text"]).status_text())
    )
    sp_sub.add_parser("shops", help="识别 MY/VN/TH/PH 主店（跳过附属）").set_defaults(
        func=lambda a: __import__("modules.shopee.shops", fromlist=["refresh_shop_regions"]).refresh_shop_regions()
    )
    sp_sub.add_parser("sync", help="同步四国主店商品").set_defaults(
        func=lambda a: _shopee_sync()
    )
    sp_sub.add_parser("compare", help="对比 TK vs Shopee SKU").set_defaults(
        func=lambda a: print(__import__("modules.shopee.compare", fromlist=["compare_report"]).compare_report())
    )
    sppft = sp_sub.add_parser("profit", help="四国主店月度订单利润报表")
    sppft.add_argument("--month", required=True, help="自然月 YYYY-MM，如 2026-06")
    sppft.set_defaults(func=_shopee_profit)
    spp = sp_sub.add_parser("publish", help="TK → Shopee 铺货（单 SKU 试跑）")
    spp.add_argument("--match-key", required=True, help="对齐码，如 0026")
    spp.add_argument("--region", required=True, choices=["MY", "VN", "TH", "PH"])
    spp.add_argument("--dry-run", action="store_true")
    spp.add_argument(
        "--publish-shops",
        action="store_true",
        help="同时发布到指定国家店（默认仅创建全球商品）",
    )
    spp.set_defaults(func=_shopee_publish)
    spg = sp_sub.add_parser("publish-group", help="TK 多 SKU → Shopee 全球商品（单链接多规格）")
    spg.add_argument("--keys", required=True, help="对齐码列表，如 0402,0403,0404,0405")
    spg.add_argument("--region", default="PH", choices=["MY", "VN", "TH", "PH"])
    spg.add_argument("--tier", default="Color", help="规格维度名，默认 Color")
    spg.add_argument("--dry-run", action="store_true")
    spg.add_argument(
        "--register",
        action="store_true",
        help="仅写入映射（全球商品已在 CNSC 创建时用）",
    )
    spg.add_argument("--global-item-id", type=int, help="配合 --register 使用")
    spg.set_defaults(func=_shopee_publish_group)
    sgs = sp_sub.add_parser("sync-group", help="整组同步 TK→Shopee（Color 图 + ¥价 + 库存）")
    sgs.add_argument("--keys", required=True, help="对齐码列表，如 0402,0403,0404,0405")
    sgs.add_argument("--region", default="PH", choices=["MY", "VN", "TH", "PH"])
    sgs.set_defaults(func=_shopee_sync_group)
    spu = sp_sub.add_parser("update-global", help="更新 CNSC 全球商品英文标题/描述")
    spu.add_argument("--match-key", required=True, help="对齐码，如 0014")
    spu.add_argument("--region", default="PH", choices=["MY", "VN", "TH", "PH"])
    spu.set_defaults(func=lambda a: _shopee_update_global(a))
    sp_sub.add_parser("auth-url", help="打印店铺 OAuth 授权链接").set_defaults(
        func=lambda a: _shopee_auth_guide()
    )
    spt = sp_sub.add_parser("token", help="用回调 code 换 access_token")
    spt.add_argument("--code", required=True)
    spt.add_argument("--shop-id", type=int)
    spt.add_argument("--main-account-id", type=int)
    spt.set_defaults(
        func=lambda a: print(
            "✅",
            __import__("modules.shopee.auth", fromlist=["exchange_code_main"]).exchange_code_main(
                a.code, a.main_account_id
            )
            if a.main_account_id
            else __import__("modules.shopee.auth", fromlist=["exchange_code"]).exchange_code(
                a.code, a.shop_id
            ),
        )
        if a.main_account_id or a.shop_id
        else print("请提供 --shop-id 或 --main-account-id")
    )

    oz = sub.add_parser("ozon", help="Ozon Seller API")
    oz_sub = oz.add_subparsers(dest="ozon_cmd")
    oz_sub.add_parser("sync", help="从 API 拉取商品快照到 ozon_data_dir").set_defaults(
        func=lambda a: print(__import__("modules.ozon.sync", fromlist=["sync_catalog"]).sync_catalog())
    )
    oz_norm = oz_sub.add_parser(
        "normalize-offer-ids",
        help="6 位 Ozon offer_id 改为后四位（API + 本地 JSON）",
    )
    oz_norm.add_argument("--dry-run", action="store_true", help="仅预览映射，不写")
    oz_norm.add_argument("--local-only", action="store_true", help="只改本地 JSON，不调 Ozon API")
    oz_norm.add_argument("--restore-tk-map", action="store_true", help="从商品目录恢复 tk_sku_map 的 TikTok seller_sku")
    oz_norm.set_defaults(func=lambda a: _ozon_normalize_offer_ids(a))
    ozm = oz_sub.add_parser("migrate-batch", help="批量上品：草稿→3:4图→Ozon import")
    ozm.add_argument("--count", type=int, default=5, help="本次搬运数量（默认 5）")
    ozm.set_defaults(
        func=lambda a: _ozon_migrate_batch(a),
    )

    src = sub.add_parser("sourcing", help="1688 选品采集")
    src_sub = src.add_subparsers(dest="sourcing_cmd")
    sf = src_sub.add_parser("fetch", help="网页采集 1688 商品（无需万邦 API）")
    sf.add_argument("--url", required=True, help="1688 详情页链接或 offer id")
    sf.add_argument("--html", help="本地 HTML 文件（浏览器另存，用于绕过反爬）")
    sf.add_argument("--no-save", action="store_true", help="不写入 data/sourcing/")
    sf.set_defaults(
        func=lambda a: _sourcing_fetch(a),
    )
    sb = src_sub.add_parser("build", help="构建素材包：下载原图 + AI 文案 + 9 槽位")
    sb.add_argument("--url", required=True, help="1688 offer id 或链接")
    sb.add_argument("--skip-slots", action="store_true", help="跳过 Photoroom 槽位")
    sb.add_argument("--plan", choices=["v1", "v2"], default="v2", help="槽位方案（默认 v2 supplier 优先）")
    sb.set_defaults(func=lambda a: _sourcing_build(a))
    sp = src_sub.add_parser("photoroom-showcase", help="Photoroom 全 recipe 试跑（主图+详情样例）")
    sp.add_argument("--url", required=True, help="1688 offer id 或链接")
    sp.add_argument("--no-detail", action="store_true", help="跳过详情页试跑")
    sp.set_defaults(func=lambda a: _sourcing_photoroom_showcase(a))
    st = src_sub.add_parser("detail-text", help="生成文字详情卡（模板合成 EN/RU）")
    st.add_argument("--url", required=True, help="1688 offer id 或链接")
    st.set_defaults(func=lambda a: _sourcing_detail_text(a))

    si = src_sub.add_parser("intel", help="预览受控 EchoTik + 1688 情报请求")
    si.add_argument("--keyword-cn", required=True, help="中文类目或货源关键词")
    si.add_argument("--keyword-ph", default="", help="菲律宾市场关键词（可选）")
    si.add_argument("--keyword-my", default="", help="马来西亚市场关键词（可选）")
    si.add_argument("--keyword-th", default="", help="泰国市场关键词（可选）")
    si.add_argument("--keyword-vn", default="", help="越南市场关键词（可选）")
    si.add_argument("--image-url", help="1688 以图搜图使用的公开 HTTPS 图片 URL（可选）")
    si.add_argument("--new-rank-date", help="EchoTik 新品榜日期 YYYY-MM-DD（可选，增加四次调用）")
    si.add_argument("--page-size", type=int, default=20, help="每次返回数量，上限 20")
    si.add_argument("--output", help="JSON 输出路径（可选）")
    si.add_argument(
        "--execute-paid",
        action="store_true",
        help="执行付费只读 API；同时要求 LINKFOXAGENT_API_KEY",
    )
    si.set_defaults(func=_sourcing_external_intel)

    return p


def _sourcing_build(args) -> None:
    import json
    from modules.sourcing.onebound import parse_offer_id
    from modules.sourcing.pipeline import build_draft

    offer_id = parse_offer_id(args.url)
    draft = build_draft(
        offer_id,
        skip_slots=args.skip_slots,
        plan_version=args.plan,
        progress=lambda m: print(f"  … {m}"),
    )
    print(json.dumps({
        "offer_id": offer_id,
        "title": (draft.get("source") or {}).get("title"),
        "copy_platforms": list((draft.get("copy") or {}).get("tiktok", {}).keys()),
        "slots": len((draft.get("assets") or {}).get("slots") or []),
        "errors": draft.get("errors") or [],
    }, ensure_ascii=False, indent=2))
    print(f"\n✅ 草稿: data/sourcing/{offer_id}_draft.json")


def _tokens_refresh(_args) -> None:
    from modules.hub.tokens import refresh_all

    print("\n🔄 自动刷新 Token…")
    r = refresh_all(on_progress=print)
    if r.get("errors"):
        print("\n⚠️ 部分失败（refresh_token 过期则需重新浏览器授权一次）：")
        for e in r["errors"]:
            print(f"  · {e}")
    else:
        print("\n✅ 全部 token 有效")


def _tokens_status(_args) -> None:
    import json
    from modules.hub.tokens import status_summary

    print(json.dumps(status_summary(), ensure_ascii=False, indent=2))


def _ozon_normalize_offer_ids(args) -> None:
    import json
    from modules.ozon import offer_id_normalize as norm_mod

    if args.restore_tk_map:
        print(json.dumps({"fixed": norm_mod.restore_tk_map_seller_skus()}, ensure_ascii=False, indent=2))
        return
    print(json.dumps(
        norm_mod.run_normalize(dry_run=args.dry_run, local_only=args.local_only),
        ensure_ascii=False,
        indent=2,
    ))


def _ozon_migrate_batch(args) -> None:
    from modules.ozon.migrate_batch import migrate_batch
    print(f"\n🚀 Ozon 批量上品（{args.count} 个）…")
    result = migrate_batch(args.count)
    migrated = result.get("migrated") or []
    failed = result.get("failed") or []
    print(f"\n✅ 成功 {len(migrated)}: {', '.join(migrated) or '—'}")
    if failed:
        print(f"❌ 失败 {len(failed)}:")
        for f in failed:
            print(f"   {f.get('offer_id')}: {f.get('error') or f.get('status')}")
    print(f"   队列剩余约 {result.get('remaining', '?')} 个")


def _sourcing_fetch(args) -> None:
    import json
    from pathlib import Path

    from modules.sourcing.scrape_1688 import item_summary, scrape_offer

    html = None
    if args.html:
        html = Path(args.html).read_text(encoding="utf-8", errors="replace")
    data = scrape_offer(args.url, save=not args.no_save, html=html)
    print(json.dumps(item_summary(data), ensure_ascii=False, indent=2))
    if data.get("saved_to"):
        print(f"\n✅ 已保存: {data['saved_to']}")


def _sourcing_photoroom_showcase(args) -> None:
    import json

    from modules.sourcing.onebound import parse_offer_id
    from modules.sourcing.photoroom_showcase import build_showcase

    offer_id = parse_offer_id(args.url)

    def progress(msg: str) -> None:
        print(f"  … {msg}")

    manifest = build_showcase(offer_id, progress=progress, include_detail=not args.no_detail)
    print(json.dumps(manifest.get("summary") or {}, ensure_ascii=False, indent=2))
    print(f"\n✅ 试跑结果: data/sourcing/{offer_id}/photoroom_showcase.json")
    print(f"   预览: http://127.0.0.1:8765/sourcing/photoroom?offer_id={offer_id}")


def _sourcing_detail_text(args) -> None:
    import json

    from modules.sourcing.detail_text_cards import build_detail_text_cards
    from modules.sourcing.onebound import parse_offer_id

    offer_id = parse_offer_id(args.url)
    manifest = build_detail_text_cards(offer_id)
    print(json.dumps(manifest.get("summary") or {}, ensure_ascii=False, indent=2))
    print(f"\n✅ 文字详情卡: data/sourcing/{offer_id}/detail_text_cards/")
    print(f"   在选品页查看: http://127.0.0.1:8765/sourcing?offer_id={offer_id}")


def _sourcing_external_intel(args) -> None:
    import json
    from pathlib import Path

    from modules.sourcing.external_intel import build_intel_plan, run_intel_plan

    region_keywords = {
        region: value
        for region, value in {
            "PH": args.keyword_ph,
            "MY": args.keyword_my,
            "TH": args.keyword_th,
            "VN": args.keyword_vn,
        }.items()
        if value
    }
    plan = build_intel_plan(
        keyword_cn=args.keyword_cn,
        region_keywords=region_keywords,
        page_size=args.page_size,
        image_url=args.image_url,
        new_rank_date=args.new_rank_date,
    )
    result = run_intel_plan(plan, allow_paid=args.execute_paid)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
        mode = "result" if args.execute_paid else "preview"
        print(f"Saved external-intel {mode}: {output}")
    else:
        print(rendered)


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
