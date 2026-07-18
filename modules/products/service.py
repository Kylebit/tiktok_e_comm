"""商品模块入口。"""

from __future__ import annotations

from pathlib import Path

from modules.products import build_page, costs, seller_sku, server, sync


def sync_products(
    fetch_images: bool = True,
    import_cursor: bool = True,
    fill_seller_sku: bool = False,
) -> None:
    print("\n[1/3] 同步 TikTok 商品...")
    stats = sync.sync_all(fetch_images=fetch_images)
    print(f"  合计 {stats['skus']} 个 SKU")

    step = 2
    total = 4 if fill_seller_sku else 3

    if fill_seller_sku:
        print(f"\n[{step}/{total}] 补全缺失商家 SKU...")
        seller_sku.fill_missing()
        step += 1

    if import_cursor:
        print(f"\n[{step}/{total}] 导入 CURSOR 默认成本...")
        costs.import_from_cursor()
    else:
        print(f"\n[{step}/{total}] 跳过 CURSOR 成本导入")
    step += 1

    print(f"\n[{step}/{total}] 生成成本页...")
    path = build_page.build_html()
    print(f"  ✅ {path}")
    print("\n打开方式:")
    print("  python3 main.py serve")


def fill_seller_sku(dry_run: bool = False, push: bool = False, export: bool = True) -> None:
    print("\n补全商家 SKU...")
    seller_sku.fill_missing(dry_run=dry_run, push=push, export=export)


def export_seller_sku_xlsx(region: str | None = None, sku_id: str | None = None, limit: int = 1) -> None:
    from modules.products.batchedit_xlsx import export_batchedit_xlsx

    path = export_batchedit_xlsx(region=region, sku_id=sku_id, limit=limit)
    print(f"  ✅ {path}")
    print("  上传：Seller Center → 商品 → 批量工具 → 上传 Excel")


def set_cost(sku_id: str, cost_cny: float, note: str = "") -> None:
    costs.save_cost(sku_id, cost_cny, note)
    print(f"  ✅ SKU {sku_id} 成本 = ¥{cost_cny:.2f}")


def list_costs(limit: int = 20) -> None:
    from core.db import connect, init_db
    init_db()
    conn = connect()
    rows = conn.execute(
        """SELECT c.sku_id, c.cost_cny, c.note, p.product_name
           FROM sku_costs c LEFT JOIN products p ON p.sku_id = c.sku_id
           GROUP BY c.sku_id ORDER BY c.updated_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    if not rows:
        print("  （暂无成本记录）")
        return
    for r in rows:
        name = (r["product_name"] or "")[:40]
        print(f"  {r['sku_id']}  ¥{r['cost_cny']:.2f}  {name}")


def open_page() -> None:
    path = build_page.build_html()
    print(f"  已生成 {path}")
    server.serve(page="costs")


def open_dashboard() -> None:
    server.serve()


def scan_titles(days: int = 30, max_units: int = 1, limit: int = 30, region: str | None = None) -> None:
    from modules.products import titles
    print(f"\n扫描低动销商品（{days}天内销量 ≤ {max_units}，最多 {limit} 个）...")
    titles.scan_low_velocity(days=days, max_units=max_units, limit=limit, region=region)


def scan_listings_analytics(limit: int = 20, region: str | None = None) -> None:
    from modules.products import titles
    print(f"\nAnalytics A 类扫描（28天 CTR≥中位×1.5 · 0单，最多 {limit} 个）...")
    titles.scan_analytics_high_interest(limit=limit, region=region)


def sync_analytics(region: str | None = None) -> None:
    from modules.products import analytics
    print("\n同步 Analytics...")
    result = analytics.sync_all(region=region)
    print(f"  ✅ {result['total']} 条 · 分段 {result.get('by_segment')}")


def run_ctr_gpm_boost(region: str = "MY", days: int = 30) -> None:
    """MY LivelyHive CTR/GPM 双优 → 达人建联候选清单。"""
    from modules.products import analytics

    if (region or "MY").upper() != "MY":
        raise SystemExit("本期仅支持 --region MY")
    print(f"\n══ LivelyHive MY CTR/GPM 选品（近 {days} 天）══")
    analytics.run_my_ctr_gpm_boost(days=days, quiet=False)


def scan_deactivate(limit: int = 50, region: str | None = None) -> None:
    from modules.products import deactivate
    print(f"\n扫描下架候选（90天0单 + CTR低于中位，最多 {limit} 个）...")
    deactivate.scan_candidates(region=region, limit=limit)


def push_deactivate_cli() -> None:
    from modules.products import deactivate
    result = deactivate.push_approved()
    print(f"  下架完成: 成功 {result['ok']} · 失败 {result['fail']}")
    for e in result["errors"][:5]:
        print(f"    {e}")


def open_title_review(port: int = 8765) -> None:
    server.serve(port=port, page="titles")


def push_titles() -> None:
    from modules.products import titles
    result = titles.push_approved()
    print(f"  推送完成: 成功 {result['ok']} · 失败 {result['fail']} · 跳过 {result['skip']}")
    for e in result["errors"][:5]:
        print(f"    {e}")


def scan_promos(
    days: int = 30,
    max_units: int = 1,
    limit: int = 30,
    region: str | None = None,
    scope: str = "adjust",
    mode: str = "velocity",
) -> None:
    from modules.products import promotions
    if mode == "analytics":
        print(f"\nA 类促销扫描（scope={scope}，最多 {limit} 个）...")
        promotions.scan_analytics_high_interest(limit=limit, region=region, scope=scope)
    else:
        print(f"\n扫描促销（scope={scope}，{days}天内销量 ≤ {max_units}，最多 {limit} 个）...")
        promotions.scan_low_velocity(
            days=days, max_units=max_units, limit=limit, region=region, scope=scope
        )


def push_promos() -> None:
    from modules.products import promotions
    result = promotions.push_approved()
    print(f"  推送完成: 成功 {result['ok']} · 失败 {result['fail']} · 跳过 {result['skip']}")
    for e in result["errors"][:5]:
        print(f"    {e}")


def scan_images(limit: int = 10, region: str | None = None, variants: int | None = None) -> None:
    from modules.products import images
    print(f"\nAnalytics B 类主图扫描（基于 listing 原图，最多 {limit} 个）...")
    n = images.scan_b_class(limit=limit, region=region, variants=variants)
    print(f"  ✅ 已生成 {n} 个商品的主图候选")
    print("  预览: python3 main.py serve --page images")


def rebuild_page() -> None:
    path = build_page.build_html()
    print(f"  ✅ {path}")
