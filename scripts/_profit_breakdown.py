"""一次性利润拆解展示。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.mx_pop_pricing import fetch_cny_mxn, quote_sku

rate = fetch_cny_mxn()
for sku in ("770002", "770003"):
    r = quote_sku(sku, cny_mxn=rate)
    sale = r.sale_price_mxn
    print("=" * 62)
    tag = sku[-4:]
    print(f"SKU {sku}（{tag}）")
    if r.sfp_adjustment:
        print(f"  POP 测算 {r.pop_sale_mxn:.2f} → SFP 抬价后 {sale:.2f} MXN")
    else:
        print(f"  折后售价 {sale:.2f} MXN")
    print(f"  折前原价 {r.list_price_mxn:.2f} MXN | 成本 ¥{r.cost_cny} = {r.cost_mxn:.2f} MXN")
    print("-" * 62)
    lines = [
        ("买家实付（折后售价）", sale, "+"),
        ("藏价物流", r.logistics_hidden_mxn, "−"),
        ("进口税 MAX(0,…)", r.import_tax_mxn, "−"),
        ("平台佣金 6%", r.platform_commission_mxn, "−"),
        ("SFP 项目费 8%", r.sfp_fee_mxn, "−"),
        ("达人佣金 8%", r.affiliate_mxn, "−"),
        ("广告投入 10%", r.ad_mxn, "−"),
        ("每件成交费", r.per_item_fee_mxn, "−"),
    ]
    subtotal = sale
    for label, val, op in lines[1:]:
        subtotal -= val
    for label, val, op in lines:
        mark = {"+": " ", "−": "−", "=": "="}[op]
        prefix = "" if op == "+" else mark + " "
        print(f"  {prefix}{label:<22} {val:>10.2f} MXN")
    print(f"  {'= 商家实际收入':<24} {r.net_income_mxn:>10.2f} MXN")
    print(f"  − 商品成本              {r.cost_mxn:>10.2f} MXN")
    print(f"  {'= 净利润':<24} {r.net_profit_mxn:>10.2f} MXN")
    print(f"  利润率（净利 ÷ 实付）   {r.profit_margin_on_sale_pct:>10.2f} %")
    print()
