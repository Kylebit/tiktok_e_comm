"""Render an audit-friendly, columnar order-profit report from stable JSON."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from typing import Any


def render_profit_report_html(report: Mapping[str, Any]) -> str:
    platform = _text(report.get("platform")).upper()
    period = _map(report.get("period")); totals = _map(report.get("totals"))
    issues = _list(report.get("quality_issues")); warnings = _list(report.get("assumption_warnings"))
    lines = [line for line in _list(report.get("order_lines")) if isinstance(line, Mapping)]
    fee_columns = _fee_columns(lines)
    warning_by_sku = {str(item.get("canonical_sku") or ""): item for item in warnings if isinstance(item, Mapping)}
    cards = "".join(_card(label, totals.get(field)) for field, label in (
        ("settlement_cny", "净结算 CNY"), ("product_cost_cny", "商品成本 CNY"),
        ("advertising_cny", "广告成本 CNY"), ("external_costs_cny", "额外成本 CNY"),
        ("profit_cny", "利润 CNY"),
    ))
    issue_html = _messages(issues, "无阻断性质量问题")
    warning_html = _messages(warnings, "无临时假设")
    headers = _base_headers() + [label for _, label in fee_columns] + ["成本/FX/结算证据"]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    body_html = "".join(_order_row(line, fee_columns, warning_by_sku) for line in lines)
    footer_html = _footer(report, fee_columns, len(headers))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(platform)} 利润报表</title><style>
body{{font:13px/1.45 system-ui,sans-serif;margin:0;background:#f5f7f8;color:#172126}}main{{margin:auto;padding:20px}}h1{{margin:4px 0}}.meta{{color:#64748b}}.cards{{display:grid;grid-template-columns:repeat(5,minmax(150px,1fr));gap:10px;margin:16px 0}}.card,section{{background:#fff;border:1px solid #dfe6e9;border-radius:10px;padding:12px}}.card strong{{display:block;font-size:19px;margin-top:4px}}.status,.warning{{display:inline-block;padding:3px 8px;border-radius:999px;background:#fff3cd}}.warning{{background:#ffe4b5;color:#7c4700;font-size:11px}}.table{{overflow:auto;max-height:72vh;background:#fff;border:1px solid #dfe6e9;border-radius:10px}}table{{border-collapse:collapse;min-width:max-content;width:100%}}th,td{{padding:7px 9px;border-bottom:1px solid #edf1f2;text-align:left;vertical-align:top;white-space:nowrap}}th{{position:sticky;top:0;z-index:3;background:#eef3f4}}tfoot td{{position:sticky;bottom:0;background:#e8f4ff;font-weight:700;border-top:2px solid #4b9bd8}}td.num{{text-align:right;font-variant-numeric:tabular-nums}}td.product{{white-space:normal;min-width:220px;max-width:300px}}tr.assumption{{background:#fffaf0}}tr.negative{{background:#fff5f5}}img{{width:48px;height:48px;object-fit:cover;border-radius:7px;background:#eee}}code{{font-size:11px}}ul{{margin:6px 0;padding-left:20px}}@media(max-width:800px){{.cards{{grid-template-columns:1fr 1fr}}main{{padding:10px}}}}
</style></head><body><main>
<header><span class="status">{escape(_text(report.get('status')))}</span><h1>{escape(platform)} {escape(_text(report.get('period_kind')))} 订单级利润明细</h1><p class="meta">{escape(_text(period.get('start')))} 至 {escape(_text(period.get('end')))} · {escape(_text(report.get('calculation_kind')))} · {len(lines)} 个已结算订单行 · 页面金额统一显示两位小数，JSON 保留原始 Decimal 精度</p></header>
<div class="cards">{cards}</div>
<section><h2>阻断性质量问题</h2><ul>{issue_html}</ul><h2>临时假设警告</h2><ul>{warning_html}</ul><p class="meta">report_id={escape(_text(report.get('report_id')))} · idempotency_key={escape(_text(report.get('idempotency_key')))}</p></section>
<h2>订单明细（每项费用独立成列）</h2><div class="table"><table><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody><tfoot>{footer_html}</tfoot></table></div>
</main></body></html>"""


def _base_headers() -> list[str]:
    return [
        "结算时间", "订单 ID", "订单行 ID", "店铺/地区", "主图", "平台 SKU", "Seller SKU",
        "商品名称", "规格", "数量", "单件重量(g)", "计费重量(g)", "币种", "买家实付商品金额",
        "净结算(当地)", "汇率(CNY/当地)", "净结算(CNY)", "单件成本(CNY)", "商品总成本(CNY)",
        "广告基数(当地)", "广告比例", "广告费(当地)", "广告费(CNY)", "额外成本(CNY)",
        "利润(CNY)", "利润率",
    ]


def _order_row(line, fee_columns, warning_by_sku):
    identity = _map(line.get("identity")); product = _map(line.get("product")); settlement = _map(line.get("settlement"))
    cost = _map(line.get("cost")); ads = _map(line.get("advertising")); fx = _map(line.get("fx"))
    sku = _text(product.get("canonical_sku")); warning = warning_by_sku.get(sku)
    image = _text(product.get("image_url")); image_html = f'<img src="{escape(image, quote=True)}" alt="商品主图" loading="lazy">' if image.lower().startswith("https://") else '<span class="meta">无主图</span>'
    margin = _margin(line)
    cells = [
        _text(line.get("settled_at")), _text(identity.get("order_id")), _text(identity.get("order_line_id")),
        f"{_text(identity.get('shop_id'))} / {_text(identity.get('region'))}", image_html,
        _text(product.get("platform_sku")), _text(product.get("seller_sku")), _text(product.get("product_name")),
        _text(product.get("variant_name")), _quantity(product.get("quantity")), _money(product.get("unit_weight_g")),
        _money(product.get("billable_weight_g")), _text(settlement.get("currency")), _money(settlement.get("buyer_paid_product_amount_local")),
        _money(settlement.get("net_amount_local")), _money(fx.get("rate_cny_per_local")), _money(settlement.get("net_amount_cny")),
        _money(cost.get("unit_cost_cny")), _money(cost.get("total_cny")), _money(ads.get("basis_amount_local")),
        _percent_value(ads.get("rate")), _money(ads.get("amount_local")), _money(ads.get("amount_cny")),
        _money(line.get("external_costs_cny")), _money(line.get("profit_cny")), margin,
    ]
    classes = ["", "", "", "", "", "", "", "product", "product"] + ["num"] * 17
    output = []
    for index, value in enumerate(cells):
        output.append(f'<td class="{classes[index]}">{value if index == 4 else escape(value)}</td>')
    for code, _ in fee_columns:
        local, cny, currency = _fee_value(line, code)
        output.append(f'<td class="num">{escape(_money(local))} {escape(currency)}<br><small>CNY {escape(_money(cny))}</small></td>')
    evidence = f"成本: {_text(cost.get('source'))}<br>{_text(cost.get('version'))}<br>FX: {_text(fx.get('source'))} @ {_text(fx.get('as_of'))}<br>结算: {_text(line.get('source_snapshot_id'))}"
    if warning:
        evidence = f'<span class="warning">{escape(_text(warning.get("code")))}</span><br>{escape(_text(warning.get("message")))}<br>' + evidence
    else:
        evidence = escape(evidence).replace("&lt;br&gt;", "<br>")
    row_classes = []
    if warning: row_classes.append("assumption")
    if _decimal(line.get("profit_cny")) is not None and _decimal(line.get("profit_cny")) < 0: row_classes.append("negative")
    output.append(f'<td class="product">{evidence}</td>')
    return f'<tr class="{" ".join(row_classes)}">{"".join(output)}</tr>'


def _fee_columns(lines):
    values = {}
    for line in lines:
        for item in _list(line.get("fee_items")):
            if not isinstance(item, Mapping): continue
            code = _text(item.get("code"))
            if code:
                label = _text(item.get("label") or code)
                values.setdefault(code, f"{label} [{code}]")
    return sorted(values.items(), key=lambda item: item[0].lower())


def _fee_value(line, code):
    local = Decimal("0"); cny = Decimal("0"); currency = ""
    for item in _list(line.get("fee_items")):
        if not isinstance(item, Mapping) or _text(item.get("code")) != code: continue
        local += _decimal(item.get("amount")) or Decimal("0")
        cny += _decimal(item.get("amount_cny")) or Decimal("0")
        currency = _text(item.get("currency"))
    return local, cny, currency


def _footer(report, fee_columns, column_count):
    totals = _map(report.get("totals")); lines = [line for line in _list(report.get("order_lines")) if isinstance(line, Mapping)]
    cells = ["合计"] + [""] * 12
    cells += [_money(sum((_decimal(_map(line.get("settlement")).get("buyer_paid_product_amount_local")) or Decimal("0") for line in lines), Decimal("0")))]
    cells += ["", "", _money(totals.get("settlement_cny")), "", _money(totals.get("product_cost_cny")), "", "", "", _money(totals.get("advertising_cny")), _money(totals.get("external_costs_cny")), _money(totals.get("profit_cny")), ""]
    html = "".join(f'<td class="num">{escape(value)}</td>' for value in cells)
    for code, _ in fee_columns:
        local = sum((_fee_value(line, code)[0] for line in lines), Decimal("0")); cny = sum((_fee_value(line, code)[1] for line in lines), Decimal("0"))
        html += f'<td class="num">{escape(_money(local))}<br><small>CNY {escape(_money(cny))}</small></td>'
    html += "<td></td>"
    return f"<tr>{html}</tr>"


def _margin(line):
    settlement = _map(line.get("settlement")); fx = _map(line.get("fx"))
    basis = _decimal(settlement.get("buyer_paid_product_amount_local")); rate = _decimal(fx.get("rate_cny_per_local")); profit = _decimal(line.get("profit_cny"))
    if basis is None or rate is None or profit is None or basis * rate == 0: return "—"
    return f"{(profit / (basis * rate) * Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"


def _messages(items, empty):
    rendered = "".join(f"<li><code>{escape(_text(item.get('code')))}</code> · {escape(_text(item.get('record_id') or item.get('canonical_sku')))} · {escape(_text(item.get('message')))}</li>" for item in items if isinstance(item, Mapping))
    return rendered or f"<li>{escape(empty)}</li>"


def _card(label, value): return f'<div class="card"><span>{escape(label)}</span><strong>{escape(_money(value))}</strong></div>'
def _map(value): return value if isinstance(value, Mapping) else {}
def _list(value): return value if isinstance(value, list) else []
def _text(value): return str(value) if value not in (None, "") else "—"
def _quantity(value):
    number = _decimal(value)
    return str(int(number)) if number is not None and number == number.to_integral() else _money(value)
def _percent_value(value):
    number = _decimal(value)
    return f"{(number * Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%" if number is not None else "—"
def _money(value):
    number = _decimal(value)
    return f"{number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):f}" if number is not None else "—"
def _decimal(value):
    if value is None or isinstance(value, bool) or str(value).strip() == "": return None
    try: return Decimal(str(value))
    except (InvalidOperation, ValueError): return None
