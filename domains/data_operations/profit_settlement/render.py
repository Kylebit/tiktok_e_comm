"""Render an audit-friendly, columnar order-profit report from stable JSON."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.utils import parsedate_to_datetime
from html import escape
import json
from typing import Any


AMS_COMMISSION_FEE_CODE = "order_ams_commission_fee"


def render_profit_report_html(report: Mapping[str, Any]) -> str:
    platform = _text(report.get("platform")).upper()
    period = _map(report.get("period")); totals = _map(report.get("totals"))
    source = _map(report.get("source"))
    issues = _list(report.get("quality_issues")); warnings = _list(report.get("assumption_warnings"))
    lines = [line for line in _list(report.get("order_lines")) if isinstance(line, Mapping)]
    fee_columns = _fee_columns(lines)
    warning_by_sku = {str(item.get("canonical_sku") or ""): item for item in warnings if isinstance(item, Mapping)}
    buyer_cash_cny = sum((_buyer_cash_cny(line) for line in lines), Decimal("0"))
    local_fulfillment_cny = _decimal(totals.get("local_fulfillment_cost_cny")) or Decimal("0")
    external_costs_cny = _decimal(totals.get("external_costs_cny")) or Decimal("0")
    external_cost_label = (
        "本土履约费 CNY"
        if external_costs_cny == local_fulfillment_cny
        else "本土履约及其他外部成本 CNY"
    )
    cards = _card("总成交额（用户实付）CNY", buyer_cash_cny)
    cards += "".join(_card(label, totals.get(field)) for field, label in (
        ("settlement_cny", "净结算 CNY"), ("product_cost_cny", "商品成本 CNY"),
        ("advertising_cny", "广告成本 CNY"), ("external_costs_cny", external_cost_label),
        ("profit_cny", "利润 CNY"),
    ))
    cards += _text_card(
        "整体利润率（利润/用户实付）",
        escape(_profit_margin(totals.get("profit_cny"), buyer_cash_cny)),
    )
    fulfillment_charged_orders = int(_decimal(source.get("local_fulfillment_charged_order_count")) or Decimal("0"))
    if external_costs_cny == local_fulfillment_cny and fulfillment_charged_orders:
        per_order_fulfillment = local_fulfillment_cny / Decimal(fulfillment_charged_orders)
        external_cost_note = (
            f"本报表外部成本全部为本土履约费：{fulfillment_charged_orders} 个本土父订单 × "
            f"CNY {_money(per_order_fulfillment)} = CNY {_money(local_fulfillment_cny)}。"
            "平台佣金、交易费、税费等已包含在净结算中，不在此重复扣除。"
        )
    else:
        other_external = external_costs_cny - local_fulfillment_cny
        external_cost_note = (
            f"外部成本包括本土履约费 CNY {_money(local_fulfillment_cny)} 和其他未包含在净结算中的成本 "
            f"CNY {_money(other_external)}；平台已在净结算中扣除的费用不会重复扣除。"
        )
    affiliate = _map(source.get("affiliate_marketing"))
    if platform == "SHOPEE" and affiliate:
        cards += _text_card(
            "联盟营销订单占比",
            f"{_percent_value(affiliate.get('affiliate_order_share'))} "
            f"({escape(_text(affiliate.get('affiliate_parent_order_count')))}/"
            f"{escape(_text(affiliate.get('settled_parent_order_count')))})",
        )
    issue_html = _messages(issues, "无阻断性质量问题")
    warning_html = _messages(warnings, "无临时假设")
    headers = _visible_base_headers(platform) + [label for _, label in fee_columns] + ["成本/FX/结算证据", "商品名称"]
    header_html = "".join(
        (
            '<th><button type="button" class="sort-button" '
            'data-sort="order-created-at" aria-sort="none" '
            'title="点击按下单时间升序或降序排列">'
            f'{escape(label)} <span aria-hidden="true">↕</span></button></th>'
        )
        if index == 1 else f"<th>{escape(label)}</th>"
        for index, label in enumerate(headers)
    )
    body_html = "".join(_order_row(line, fee_columns, warning_by_sku, platform) for line in lines)
    footer_html = _footer(report, fee_columns, platform)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(platform)} 利润报表</title><style>
body{{font:13px/1.45 system-ui,sans-serif;margin:0;background:#f5f7f8;color:#172126}}main{{margin:auto;padding:20px}}h1{{margin:4px 0}}.meta{{color:#64748b}}.cards{{display:grid;grid-template-columns:repeat(5,minmax(150px,1fr));gap:10px;margin:16px 0}}.card,section{{background:#fff;border:1px solid #dfe6e9;border-radius:10px;padding:12px}}.card strong{{display:block;font-size:19px;margin-top:4px}}.status,.warning{{display:inline-block;padding:3px 8px;border-radius:999px;background:#fff3cd}}.warning{{background:#ffe4b5;color:#7c4700;font-size:11px}}.order-filter{{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin:10px 0;padding:10px 12px;background:#fff;border:1px solid #dfe6e9;border-radius:10px}}.order-filter label{{display:flex;align-items:center;gap:6px}}.order-filter input,.order-filter button{{font:inherit;padding:5px 8px;border:1px solid #b8c4ca;border-radius:6px;background:#fff}}.order-filter button{{cursor:pointer}}.filter-summary{{font-weight:700}}.daily-order-counts{{display:flex;flex-wrap:wrap;gap:6px;width:100%}}.daily-order-count{{padding:3px 7px;border-radius:999px;background:#e8f4ff;color:#164e78;font-variant-numeric:tabular-nums}}.table-scroll-top{{overflow-x:auto;overflow-y:hidden;height:16px;margin-bottom:4px;background:#eef3f4;border:1px solid #dfe6e9;border-radius:7px}}.table-scroll-top>div{{height:1px}}.table{{overflow:auto;max-height:72vh;background:#fff;border:1px solid #dfe6e9;border-radius:10px}}table{{border-collapse:collapse;min-width:max-content;width:100%}}th,td{{padding:7px 9px;border-bottom:1px solid #edf1f2;text-align:left;vertical-align:top;white-space:nowrap}}th{{position:sticky;top:0;z-index:3;background:#eef3f4}}.sort-button{{border:0;padding:0;background:transparent;color:inherit;font:inherit;font-weight:700;cursor:pointer;white-space:nowrap}}.sort-button:focus-visible{{outline:2px solid #2563eb;outline-offset:3px;border-radius:2px}}tfoot td{{position:sticky;bottom:0;background:#e8f4ff;font-weight:700;border-top:2px solid #4b9bd8}}td.num{{text-align:right;font-variant-numeric:tabular-nums}}td.product{{white-space:normal;min-width:220px;max-width:300px}}tr.assumption{{background:#fffaf0}}tr.local-fulfillment{{background:#effaf1}}tr.negative{{background:#fff5f5}}img{{width:48px;height:48px;object-fit:cover;border-radius:7px;background:#eee}}code{{font-size:11px}}ul{{margin:6px 0;padding-left:20px}}@media(max-width:800px){{.cards{{grid-template-columns:1fr 1fr}}main{{padding:10px}}}}
</style></head><body><main>
<header><span class="status">{escape(_text(report.get('status')))}</span><h1>{escape(platform)} {escape(_text(report.get('period_kind')))} 订单级利润明细</h1><p class="meta">{escape(_text(period.get('start')))} 至 {escape(_text(period.get('end')))} · {escape(_text(report.get('calculation_kind')))} · {len(lines)} 个已结算订单行 · 页面金额统一显示两位小数，JSON 保留原始 Decimal 精度</p></header>
<div class="cards">{cards}</div><p class="meta">{escape(external_cost_note)}</p>
<section><h2>阻断性质量问题</h2><ul>{issue_html}</ul><h2>临时假设警告</h2><ul>{warning_html}</ul><p class="meta">report_id={escape(_text(report.get('report_id')))} · idempotency_key={escape(_text(report.get('idempotency_key')))}</p></section>
<h2>订单明细（每项费用独立成列）</h2>
<div class="order-filter" data-role="order-date-filter">
<label>下单日期从 <input type="date" data-role="order-date-start"></label>
<label>至 <input type="date" data-role="order-date-end"></label>
<button type="button" data-role="order-date-reset">清除筛选</button>
<span class="filter-summary" data-role="filtered-order-summary"></span>
<span class="meta">筛选只改变页面显示和底部筛选合计，不修改审计 JSON 或整期合计。</span>
<div class="daily-order-counts" data-role="daily-order-counts" aria-live="polite"></div>
</div>
<div class="table-scroll-top" data-role="order-table-top-scroll" aria-label="订单明细顶部横向滚动条"><div></div></div>
<div class="table" data-role="order-table-scroll"><table><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody><tfoot>{footer_html}</tfoot></table></div>
</main><script>
(() => {{
  const top = document.querySelector('[data-role="order-table-top-scroll"]');
  const body = document.querySelector('[data-role="order-table-scroll"]');
  const spacer = top && top.firstElementChild;
  if (!top || !body || !spacer) return;
  let syncing = false;
  const resize = () => {{ spacer.style.width = `${{body.scrollWidth}}px`; }};
  const mirror = (source, target) => {{
    if (syncing) return;
    syncing = true;
    target.scrollLeft = source.scrollLeft;
    requestAnimationFrame(() => {{ syncing = false; }});
  }};
  top.addEventListener('scroll', () => mirror(top, body), {{passive: true}});
  body.addEventListener('scroll', () => mirror(body, top), {{passive: true}});
  window.addEventListener('resize', resize);
  if (window.ResizeObserver) new ResizeObserver(resize).observe(body.querySelector('table'));
  resize();

  const orderTimeButton = body.querySelector('[data-sort="order-created-at"]');
  const tbody = body.querySelector('tbody');
  const startInput = document.querySelector('[data-role="order-date-start"]');
  const endInput = document.querySelector('[data-role="order-date-end"]');
  const resetButton = document.querySelector('[data-role="order-date-reset"]');
  const filterSummary = document.querySelector('[data-role="filtered-order-summary"]');
  const dailyCounts = document.querySelector('[data-role="daily-order-counts"]');
  const totalLabel = body.querySelector('[data-role="visible-total-label"]');
  const totalCells = Array.from(body.querySelectorAll('tfoot [data-total-key]'));
  const wholePeriodTotals = new Map(totalCells.map(cell => [cell, cell.innerHTML]));
  const renderPair = (cell, local, cny, currency, localized) => {{
    cell.replaceChildren();
    if (localized) {{
      cell.append(document.createTextNode(`${{local.toFixed(2)}} ${{currency || '当地'}} / CNY ${{cny.toFixed(2)}}`));
      return;
    }}
    cell.append(document.createTextNode(local.toFixed(2)), document.createElement('br'));
    const detail = document.createElement('small');
    detail.textContent = `CNY ${{cny.toFixed(2)}}`;
    cell.append(detail);
  }};
  const updateVisibleTotals = (visibleRows, filterActive) => {{
    if (!filterActive) {{
      wholePeriodTotals.forEach((html, cell) => {{ cell.innerHTML = html; }});
      if (totalLabel) totalLabel.textContent = '合计';
      return;
    }}
    const totals = new Map();
    visibleRows.forEach(row => {{
      let values = {{}};
      try {{ values = JSON.parse(row.dataset.sumValues || '{{}}'); }} catch (_error) {{ values = {{}}; }}
      Object.entries(values).forEach(([key, value]) => {{
        const current = totals.get(key) || {{value: 0, local: 0, cny: 0, currency: ''}};
        current.value += Number(value.value || 0);
        current.local += Number(value.local || 0);
        current.cny += Number(value.cny || 0);
        current.currency ||= value.currency || '';
        totals.set(key, current);
      }});
    }});
    totalCells.forEach(cell => {{
      const value = totals.get(cell.dataset.totalKey) || {{value: 0, local: 0, cny: 0, currency: ''}};
      if (cell.dataset.totalFormat === 'margin') {{
        const profit = totals.get('profit_cny')?.value || 0;
        const buyerCash = totals.get('buyer_cash_cny')?.value || 0;
        cell.textContent = buyerCash ? `${{(profit / buyerCash * 100).toFixed(2)}}%` : '—';
      }} else if (cell.dataset.totalFormat === 'localized') renderPair(cell, value.local, value.cny, value.currency, true);
      else if (cell.dataset.totalFormat === 'pair') renderPair(cell, value.local, value.cny, value.currency, false);
      else cell.textContent = value.value.toFixed(2);
    }});
    if (totalLabel) totalLabel.textContent = '筛选合计';
  }};
  const applyDateFilter = () => {{
    if (!tbody) return;
    const start = startInput ? startInput.value : '';
    const end = endInput ? endInput.value : '';
    const filterActive = Boolean(start || end);
    const visibleRows = [];
    const ordersByDay = new Map();
    Array.from(tbody.querySelectorAll('tr')).forEach(row => {{
      const date = (row.dataset.orderCreatedAt || '').slice(0, 10);
      const visible = !filterActive || (Boolean(date) && (!start || date >= start) && (!end || date <= end));
      row.hidden = !visible;
      if (!visible) return;
      visibleRows.push(row);
      if (date) {{
        if (!ordersByDay.has(date)) ordersByDay.set(date, new Set());
        ordersByDay.get(date).add(row.dataset.orderId || row.dataset.sortTie || '');
      }}
    }});
    const uniqueOrders = new Set(visibleRows.map(row => row.dataset.orderId || row.dataset.sortTie || ''));
    if (filterSummary) filterSummary.textContent = `显示 ${{uniqueOrders.size}} 个订单，${{visibleRows.length}} 个商品单位行`;
    if (dailyCounts) {{
      dailyCounts.replaceChildren(...Array.from(ordersByDay.entries())
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([date, orderIds]) => {{
          const chip = document.createElement('span');
          chip.className = 'daily-order-count';
          chip.textContent = `${{date}}：${{orderIds.size}} 单`;
          return chip;
        }}));
    }}
    updateVisibleTotals(visibleRows, filterActive);
  }};
  if (startInput) startInput.addEventListener('input', applyDateFilter);
  if (endInput) endInput.addEventListener('input', applyDateFilter);
  if (resetButton) resetButton.addEventListener('click', () => {{
    if (startInput) startInput.value = '';
    if (endInput) endInput.value = '';
    applyDateFilter();
  }});
  applyDateFilter();
  if (orderTimeButton && tbody) {{
    orderTimeButton.addEventListener('click', () => {{
      const direction = orderTimeButton.getAttribute('aria-sort') === 'ascending'
        ? 'descending' : 'ascending';
      const multiplier = direction === 'ascending' ? 1 : -1;
      const rows = Array.from(tbody.querySelectorAll('tr'));
      rows.sort((left, right) => {{
        const leftTime = left.dataset.orderCreatedAt || '';
        const rightTime = right.dataset.orderCreatedAt || '';
        if (!leftTime && !rightTime) return (left.dataset.sortTie || '').localeCompare(right.dataset.sortTie || '');
        if (!leftTime) return 1;
        if (!rightTime) return -1;
        const timeOrder = leftTime.localeCompare(rightTime);
        return timeOrder ? timeOrder * multiplier : (left.dataset.sortTie || '').localeCompare(right.dataset.sortTie || '');
      }});
      rows.forEach(row => tbody.appendChild(row));
      orderTimeButton.setAttribute('aria-sort', direction);
      orderTimeButton.querySelector('span').textContent = direction === 'ascending' ? '↑' : '↓';
    }});
  }}
}})();
</script></body></html>"""


def _base_headers() -> list[str]:
    return [
        "结算时间", "下单时间", "订单 ID", "订单行 ID", "国家", "发货方式", "主图", "Seller SKU",
        "净结算(CNY)", "商品总成本(CNY)", "广告费(CNY)", "本土履约费(CNY)", "利润(CNY)", "利润率(利润/用户实付)",
        "规格", "数量", "单件重量(g)", "计费重量(g)", "联盟营销佣金(AMS)", "商品折后成交额", "买家现金实付商品金额",
        "净结算(当地)", "最新汇率(CNY/当地)", "汇率更新时间", "汇率来源", "单件成本(CNY)",
        "广告基数(当地)", "广告比例", "广告比例来源", "广告费(当地)", "外部成本合计(CNY)",
    ]


def _visible_base_headers(platform: str) -> list[str]:
    headers = _base_headers()
    if platform in {"SHOPEE", "TIKTOK"}:
        headers.pop(3)
    return headers


def _order_row(line, fee_columns, warning_by_sku, platform):
    identity = _map(line.get("identity")); product = _map(line.get("product")); settlement = _map(line.get("settlement"))
    cost = _map(line.get("cost")); ads = _map(line.get("advertising")); fx = _map(line.get("fx")); fulfillment = _map(line.get("fulfillment"))
    sku = _text(product.get("canonical_sku")); warning = warning_by_sku.get(sku)
    image = _text(product.get("image_url")); image_html = f'<img src="{escape(image, quote=True)}" alt="商品主图" loading="lazy">' if image.lower().startswith("https://") else '<span class="meta">无主图</span>'
    margin = _margin(line)
    ams_local, ams_cny, ams_currency = _fee_value(line, AMS_COMMISSION_FEE_CODE)
    cells = [
        _display_time(line.get("settled_at")), _display_time(line.get("occurred_at")), _text(identity.get("order_id")), _text(identity.get("order_line_id")),
        _text(identity.get("region")), _fulfillment_display(fulfillment, line.get("settlement_outcome")), image_html, _text(product.get("seller_sku")),
        _money(settlement.get("net_amount_cny")), _money(cost.get("total_cny")), _money(ads.get("amount_cny")), _money(fulfillment.get("local_fulfillment_cost_cny")), _money(line.get("profit_cny")), margin,
        _text(product.get("variant_name")), _quantity(product.get("quantity")), _money(product.get("unit_weight_g")),
        _money(product.get("billable_weight_g")), _localized_fee(ams_local, ams_cny, ams_currency), _money(settlement.get("product_sales_amount_local") or settlement.get("buyer_paid_product_amount_local")), _money(settlement.get("buyer_cash_paid_product_amount_local")),
        _money(settlement.get("net_amount_local")), _fx_rate(fx.get("rate_cny_per_local")),
        _display_time(fx.get("as_of")), _text(fx.get("source")), _money(cost.get("unit_cost_cny")), _money(ads.get("basis_amount_local")),
        _percent_value(ads.get("rate")), _ad_rate_source(ads.get("input_source")), _money(ads.get("amount_local")),
        _money(line.get("external_costs_cny")),
    ]
    classes = [
        "", "", "", "", "", "", "", "",
        "num", "num", "num", "num", "num", "num", "product", "num", "num", "num", "", "num", "num",
        "num", "num", "", "product", "num", "num", "num", "product", "num", "num",
    ]
    image_cell_index = 6
    if platform in {"SHOPEE", "TIKTOK"}:
        cells.pop(3)
        classes.pop(3)
        image_cell_index -= 1
    output = []
    for index, value in enumerate(cells):
        output.append(f'<td class="{classes[index]}">{value if index == image_cell_index else escape(value)}</td>')
    for code, _ in fee_columns:
        local, cny, currency = _fee_value(line, code)
        output.append(f'<td class="num">{escape(_money(local))} {escape(currency)}<br><small>CNY {escape(_money(cny))}</small></td>')
    facts = _list(line.get("source_settlement_facts"))
    fact_text = "; ".join(
        f"{_text(fact.get('fact_id'))}@{_text(fact.get('settled_at'))}"
        for fact in facts if isinstance(fact, Mapping)
    ) or "—"
    evidence = f"成本: {_text(cost.get('source'))}<br>{_text(cost.get('version'))}<br>FX: {_text(fx.get('source'))} @ {_text(fx.get('as_of'))}<br>结算: {_text(line.get('source_snapshot_id'))}<br>结算事实: {fact_text}"
    if warning:
        evidence = f'<span class="warning">{escape(_text(warning.get("code")))}</span><br>{escape(_text(warning.get("message")))}<br>' + evidence
    else:
        evidence = escape(evidence).replace("&lt;br&gt;", "<br>")
    row_classes = []
    if warning: row_classes.append("assumption")
    if fulfillment.get("mode") == "local": row_classes.append("local-fulfillment")
    if _decimal(line.get("profit_cny")) is not None and _decimal(line.get("profit_cny")) < 0: row_classes.append("negative")
    output.append(f'<td class="product">{evidence}</td>')
    output.append(f'<td class="product">{escape(_text(product.get("product_name")))}</td>')
    order_created_at = _optional_text(line.get("occurred_at"))
    sort_tie = f'{_text(identity.get("order_id"))}::{_text(identity.get("order_line_id"))}'
    sum_values = _row_sum_values(line, fee_columns)
    return (
    f'<tr class="{" ".join(row_classes)}" '
    f'data-order-created-at="{escape(order_created_at, quote=True)}" '
        f'data-order-id="{escape(_text(identity.get("order_id")), quote=True)}" '
        f'data-sort-tie="{escape(sort_tie, quote=True)}" '
        f'data-sum-values="{escape(json.dumps(sum_values, sort_keys=True, separators=(",", ":")), quote=True)}">{"".join(output)}</tr>'
    )


def _row_sum_values(line, fee_columns):
    settlement = _map(line.get("settlement")); cost = _map(line.get("cost"))
    ads = _map(line.get("advertising")); fulfillment = _map(line.get("fulfillment"))
    ams_local, ams_cny, ams_currency = _fee_value(line, AMS_COMMISSION_FEE_CODE)
    values = {
        "settlement_cny": {"value": _decimal_string(settlement.get("net_amount_cny"))},
        "product_cost_cny": {"value": _decimal_string(cost.get("total_cny"))},
        "advertising_cny": {"value": _decimal_string(ads.get("amount_cny"))},
        "local_fulfillment_cost_cny": {"value": _decimal_string(fulfillment.get("local_fulfillment_cost_cny"))},
        "profit_cny": {"value": _decimal_string(line.get("profit_cny"))},
        "buyer_cash_cny": {"value": _decimal_string(_buyer_cash_cny(line))},
        "ams": {"local": str(ams_local), "cny": str(ams_cny), "currency": ams_currency},
        "product_sales_local": {"value": _decimal_string(settlement.get("product_sales_amount_local") or settlement.get("buyer_paid_product_amount_local"))},
        "buyer_cash_local": {"value": _decimal_string(settlement.get("buyer_cash_paid_product_amount_local"))},
        "external_costs_cny": {"value": _decimal_string(line.get("external_costs_cny"))},
    }
    for code, _ in fee_columns:
        local, cny, currency = _fee_value(line, code)
        values[f"fee:{code}"] = {"local": str(local), "cny": str(cny), "currency": currency}
    return values


def _fee_columns(lines):
    values = {}
    for line in lines:
        for item in _list(line.get("fee_items")):
            if not isinstance(item, Mapping): continue
            code = _text(item.get("code"))
            if code and code != AMS_COMMISSION_FEE_CODE:
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


def _footer(report, fee_columns, platform):
    totals = _map(report.get("totals")); lines = [line for line in _list(report.get("order_lines")) if isinstance(line, Mapping)]
    cells = [""] * len(_base_headers())
    cells[0] = "合计"
    cells[8] = _money(totals.get("settlement_cny"))
    cells[9] = _money(totals.get("product_cost_cny"))
    cells[10] = _money(totals.get("advertising_cny"))
    cells[11] = _money(totals.get("local_fulfillment_cost_cny"))
    cells[12] = _money(totals.get("profit_cny"))
    buyer_cash_cny = sum((_buyer_cash_cny(line) for line in lines), Decimal("0"))
    cells[13] = _profit_margin(totals.get("profit_cny"), buyer_cash_cny)
    ams_local = sum((_fee_value(line, AMS_COMMISSION_FEE_CODE)[0] for line in lines), Decimal("0"))
    ams_cny = sum((_fee_value(line, AMS_COMMISSION_FEE_CODE)[1] for line in lines), Decimal("0"))
    ams_currency = next((_fee_value(line, AMS_COMMISSION_FEE_CODE)[2] for line in lines if _fee_value(line, AMS_COMMISSION_FEE_CODE)[2]), "")
    cells[18] = _localized_fee(ams_local, ams_cny, ams_currency)
    cells[19] = _money(sum((_decimal(_map(line.get("settlement")).get("product_sales_amount_local") or _map(line.get("settlement")).get("buyer_paid_product_amount_local")) or Decimal("0") for line in lines), Decimal("0")))
    cells[20] = _money(sum((_decimal(_map(line.get("settlement")).get("buyer_cash_paid_product_amount_local")) or Decimal("0") for line in lines), Decimal("0")))
    cells[30] = _money(totals.get("external_costs_cny"))
    total_keys = [""] * len(_base_headers())
    total_keys[8] = "settlement_cny"
    total_keys[9] = "product_cost_cny"
    total_keys[10] = "advertising_cny"
    total_keys[11] = "local_fulfillment_cost_cny"
    total_keys[12] = "profit_cny"
    total_keys[13] = "profit_margin"
    total_keys[18] = "ams"
    total_keys[19] = "product_sales_local"
    total_keys[20] = "buyer_cash_local"
    total_keys[30] = "external_costs_cny"
    if platform in {"SHOPEE", "TIKTOK"}:
        cells.pop(3)
        total_keys.pop(3)
    html_parts = []
    for index, (value, key) in enumerate(zip(cells, total_keys)):
        attributes = ' data-role="visible-total-label"' if index == 0 else ""
        if key:
            total_format = "localized" if key == "ams" else ("margin" if key == "profit_margin" else "money")
            attributes += f' data-total-key="{escape(key, quote=True)}" data-total-format="{total_format}"'
        html_parts.append(f'<td class="num"{attributes}>{escape(value)}</td>')
    html = "".join(html_parts)
    for code, _ in fee_columns:
        local = sum((_fee_value(line, code)[0] for line in lines), Decimal("0")); cny = sum((_fee_value(line, code)[1] for line in lines), Decimal("0"))
        html += f'<td class="num" data-total-key="{escape(f"fee:{code}", quote=True)}" data-total-format="pair">{escape(_money(local))}<br><small>CNY {escape(_money(cny))}</small></td>'
    html += "<td></td><td></td>"
    return f"<tr>{html}</tr>"


def _margin(line):
    return _profit_margin(line.get("profit_cny"), _buyer_cash_cny(line))


def _buyer_cash_cny(line):
    settlement = _map(line.get("settlement")); fx = _map(line.get("fx"))
    buyer_cash_local = _decimal(settlement.get("buyer_cash_paid_product_amount_local"))
    rate = _decimal(fx.get("rate_cny_per_local"))
    if buyer_cash_local is None or rate is None:
        return Decimal("0")
    return buyer_cash_local * rate


def _profit_margin(profit_value, buyer_cash_cny):
    profit = _decimal(profit_value); basis = _decimal(buyer_cash_cny)
    if profit is None or basis is None or basis == 0:
        return "—"
    return f"{(profit / basis * Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"


def _messages(items, empty):
    rendered = "".join(f"<li><code>{escape(_text(item.get('code')))}</code> · {escape(_text(item.get('record_id') or item.get('canonical_sku')))} · {escape(_text(item.get('message')))}</li>" for item in items if isinstance(item, Mapping))
    return rendered or f"<li>{escape(empty)}</li>"


def _card(label, value): return f'<div class="card"><span>{escape(label)}</span><strong>{escape(_money(value))}</strong></div>'
def _text_card(label, value): return f'<div class="card"><span>{escape(label)}</span><strong>{value}</strong></div>'
def _localized_fee(local, cny, currency): return f"{_money(local)} {currency or '当地'} / CNY {_money(cny)}"
def _map(value): return value if isinstance(value, Mapping) else {}
def _list(value): return value if isinstance(value, list) else []
def _text(value): return str(value) if value not in (None, "") else "—"
def _optional_text(value): return str(value) if value not in (None, "") else ""
def _display_time(value):
    raw = _optional_text(value).strip()
    if not raw:
        return "—"
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return raw
    rendered = parsed.strftime("%Y-%m-%d %H:%M:%S")
    offset = parsed.utcoffset()
    if offset is None:
        return f"{rendered}（时区未提供）"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"{rendered}（UTC{sign}{hours:02d}:{minutes:02d}）"
def _quantity(value):
    number = _decimal(value)
    return str(int(number)) if number is not None and number == number.to_integral() else _money(value)
def _percent_value(value):
    number = _decimal(value)
    return f"{(number * Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%" if number is not None else "—"
def _ad_rate_source(value):
    return {
        "default_22": "默认 22%",
        "operator_global_override": "人工全局覆盖",
        "operator_platform_override": "人工平台覆盖",
        "operator_monthly_override": "人工月报覆盖",
        "policy_config": "统一策略配置",
    }.get(str(value or ""), _text(value))
def _fulfillment_label(value):
    return {
        "local": "本土发货",
        "cross_border": "跨境发货",
        "unknown": "待核对",
    }.get(str(value or ""), _text(value) if value else "待核对")
def _fulfillment_display(fulfillment, settlement_outcome):
    label = _fulfillment_label(_map(fulfillment).get("mode"))
    outcome = _map(settlement_outcome)
    if outcome.get("classification") == "zero_settlement_unshipped":
        return f"{label} / 零结算未发货（仅计广告）"
    return label
def _money(value):
    number = _decimal(value)
    return f"{number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):f}" if number is not None else "—"
def _fx_rate(value):
    number = _decimal(value)
    return f"{number.quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP):f}" if number is not None else "—"
def _decimal(value):
    if value is None or isinstance(value, bool) or str(value).strip() == "": return None
    try: return Decimal(str(value))
    except (InvalidOperation, ValueError): return None


def _decimal_string(value):
    number = _decimal(value)
    return str(number if number is not None else Decimal("0"))
