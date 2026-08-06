"""Render a detailed, standalone, read-only profit report from stable JSON."""

from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import Any


def render_profit_report_html(report: Mapping[str, Any]) -> str:
    platform = _text(report.get("platform")).upper()
    period = report.get("period") if isinstance(report.get("period"), Mapping) else {}
    totals = report.get("totals") if isinstance(report.get("totals"), Mapping) else {}
    issues = report.get("quality_issues") if isinstance(report.get("quality_issues"), list) else []
    lines = report.get("order_lines") if isinstance(report.get("order_lines"), list) else []
    cards = "".join(
        _card(label, totals.get(field))
        for field, label in (
            ("settlement_cny", "净结算 CNY"),
            ("product_cost_cny", "商品成本 CNY"),
            ("advertising_cny", "广告成本 CNY"),
            ("external_costs_cny", "额外费用 CNY"),
            ("profit_cny", "利润 CNY"),
        )
    )
    issue_html = "".join(
        f"<li><code>{escape(_text(item.get('code')))}</code> · {escape(_text(item.get('record_id')))} · {escape(_text(item.get('message')))}</li>"
        for item in issues if isinstance(item, Mapping)
    ) or "<li>无质量问题</li>"
    rows = "".join(_order_row(line) for line in lines if isinstance(line, Mapping))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(platform)} 利润报表</title><style>
body{{font:14px/1.5 system-ui,sans-serif;margin:0;background:#f5f7f8;color:#172126}}main{{max-width:1800px;margin:auto;padding:24px}}h1{{margin:0}}.meta{{color:#64748b}}.cards{{display:grid;grid-template-columns:repeat(5,minmax(150px,1fr));gap:12px;margin:20px 0}}.card,section{{background:#fff;border:1px solid #dfe6e9;border-radius:12px;padding:14px}}.card strong{{display:block;font-size:20px;margin-top:5px}}.status{{display:inline-block;padding:4px 9px;border-radius:999px;background:#fff3cd}}.table{{overflow:auto;background:#fff;border:1px solid #dfe6e9;border-radius:12px}}table{{border-collapse:collapse;min-width:1750px;width:100%}}th,td{{padding:9px;border-bottom:1px solid #edf1f2;text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#eef3f4}}td.num{{text-align:right;font-variant-numeric:tabular-nums}}img{{width:56px;height:56px;object-fit:cover;border-radius:8px;background:#eee}}details{{max-width:340px}}code{{font-size:12px}}@media(max-width:800px){{.cards{{grid-template-columns:1fr 1fr}}main{{padding:12px}}}}
</style></head><body><main>
<header><span class="status">{escape(_text(report.get('status')))}</span><h1>{escape(platform)} {escape(_text(report.get('period_kind')))} 利润报表</h1><p class="meta">{escape(_text(period.get('start')))} 至 {escape(_text(period.get('end')))} · {escape(_text(report.get('calculation_kind')))} · {len(lines)} 个已结算订单行</p></header>
<div class="cards">{cards}</div>
<section><h2>数据质量与审计</h2><ul>{issue_html}</ul><p class="meta">report_id={escape(_text(report.get('report_id')))} · idempotency_key={escape(_text(report.get('idempotency_key')))}</p></section>
<h2>订单级利润明细</h2><div class="table"><table><thead><tr><th>主图</th><th>订单/店铺</th><th>SKU/商品</th><th>数量/重量</th><th>净结算</th><th>货本</th><th>广告</th><th>额外费用</th><th>利润</th><th>全部费用项目</th><th>证据</th></tr></thead><tbody>{rows}</tbody></table></div>
</main></body></html>"""


def _card(label: str, value: object) -> str:
    return f'<div class="card"><span>{escape(label)}</span><strong>{escape(_text(value))}</strong></div>'


def _order_row(line: Mapping[str, Any]) -> str:
    identity=_map(line.get("identity"));product=_map(line.get("product"));settlement=_map(line.get("settlement"));cost=_map(line.get("cost"));ads=_map(line.get("advertising"));fx=_map(line.get("fx"))
    image=_text(product.get("image_url")); image_html=f'<img src="{escape(image,quote=True)}" alt="商品主图" loading="lazy">' if image.lower().startswith("https://") else '<span class="meta">无主图</span>'
    fees="".join(f"<li>{escape(_text(fee.get('label') or fee.get('code')))}: {escape(_text(fee.get('amount')))} {escape(_text(fee.get('currency')))} / CNY {escape(_text(fee.get('amount_cny')))} · {'已含于净结算' if fee.get('included_in_net_settlement') else '额外扣除'}</li>" for fee in line.get("fee_items",[]) if isinstance(fee,Mapping)) or "<li>无费用明细</li>"
    weights=f"单件 {escape(_text(product.get('unit_weight_g')))}g<br>包裹 {escape(_text(product.get('package_weight_g')))}g<br>计费 {escape(_text(product.get('billable_weight_g')))}g<br><small>{escape(_text(product.get('weight_source')))}</small>"
    evidence=f"成本 {escape(_text(cost.get('version')))}<br>FX {escape(_text(fx.get('source')))} @ {escape(_text(fx.get('as_of')))}<br>结算 {escape(_text(line.get('source_snapshot_id')))}"
    return f'''<tr><td>{image_html}</td><td><strong>{escape(_text(identity.get('order_id')))}</strong><br>{escape(_text(identity.get('order_line_id')))}<br>{escape(_text(identity.get('shop_id')))} · {escape(_text(identity.get('region')))}<br><small>{escape(_text(line.get('settled_at')))}</small></td><td><strong>{escape(_text(product.get('seller_sku')))}</strong><br>{escape(_text(product.get('canonical_sku')))} / {escape(_text(product.get('platform_sku')))}<br>{escape(_text(product.get('product_name')))}<br><small>{escape(_text(product.get('variant_name')))}</small></td><td>{escape(_text(product.get('quantity')))}<br>{weights}</td><td class="num">{escape(_text(settlement.get('net_amount_local')))} {escape(_text(settlement.get('currency')))}<br>CNY {escape(_text(settlement.get('net_amount_cny')))}</td><td class="num">{escape(_text(cost.get('unit_cost_cny')))} × {escape(_text(cost.get('quantity')))}<br>CNY {escape(_text(cost.get('total_cny')))}</td><td class="num">CNY {escape(_text(ads.get('amount_cny')))}<br><small>{escape(_text(ads.get('mode')))}</small></td><td class="num">CNY {escape(_text(line.get('external_costs_cny')))}</td><td class="num"><strong>CNY {escape(_text(line.get('profit_cny')))}</strong></td><td><details><summary>查看费用</summary><ul>{fees}</ul></details></td><td>{evidence}</td></tr>'''


def _map(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value) if value is not None else "—"
