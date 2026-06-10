#!/usr/bin/env python3
"""从 tiktok_orders.json 生成订单展示页面 orders.html"""

import json
from datetime import datetime, timezone
from pathlib import Path

ORDERS_FILE = Path("tiktok_orders.json")
OUTPUT_FILE = Path("orders.html")

STATUS_LABELS = {
    "UNPAID": "待付款",
    "ON_HOLD": "暂停",
    "AWAITING_SHIPMENT": "待发货",
    "AWAITING_COLLECTION": "待揽收",
    "IN_TRANSIT": "运输中",
    "DELIVERED": "已送达",
    "COMPLETED": "已完成",
    "CANCELLED": "已取消",
}


def fmt_time(ts):
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def flatten_orders(raw: dict) -> list:
    rows = []
    for shop_key, orders in raw.items():
        region = shop_key.rsplit("_", 1)[-1] if "_" in shop_key else shop_key
        for order in orders:
            payment = order.get("payment") or {}
            items = []
            for item in order.get("line_items") or []:
                items.append({
                    "product_name": item.get("product_name", ""),
                    "sku_name": item.get("sku_name", ""),
                    "seller_sku": item.get("seller_sku", ""),
                    "sale_price": item.get("sale_price", ""),
                    "currency": item.get("currency", payment.get("currency", "")),
                    "sku_image": item.get("sku_image", ""),
                    "display_status": item.get("display_status", ""),
                })
            rows.append({
                "shop": shop_key,
                "region": region,
                "order_id": order.get("id", ""),
                "status": order.get("status", ""),
                "status_label": STATUS_LABELS.get(order.get("status", ""), order.get("status", "")),
                "create_time": order.get("create_time"),
                "create_time_fmt": fmt_time(order.get("create_time")),
                "update_time_fmt": fmt_time(order.get("update_time")),
                "total_amount": payment.get("total_amount", ""),
                "sub_total": payment.get("sub_total", ""),
                "currency": payment.get("currency", ""),
                "payment_method": order.get("payment_method_name", ""),
                "is_cod": order.get("is_cod", False),
                "tracking_number": order.get("tracking_number", ""),
                "shipping_provider": order.get("shipping_provider", ""),
                "item_count": len(items),
                "items": items,
            })
    rows.sort(key=lambda r: r.get("create_time") or 0, reverse=True)
    return rows


def build_html(rows: list) -> str:
    data_json = json.dumps(rows, ensure_ascii=False)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TikTok Shop 订单</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 16px; background: #f5f5f5; color: #1a1a1a; }}
    h1 {{ font-size: 22px; margin: 0 0 4px; }}
    .hint {{ color: #666; font-size: 13px; margin: 0 0 16px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 16px; }}
    .card {{ background: #fff; border-radius: 10px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    .card .label {{ font-size: 12px; color: #666; }}
    .card .value {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
    .toolbar {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; align-items: center; }}
    .toolbar input, .toolbar select {{ padding: 8px 10px; border: 1px solid #ccc; border-radius: 6px; font-size: 14px; background: #fff; }}
    .toolbar input {{ min-width: 220px; flex: 1; }}
    .table-wrap {{ background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); overflow: auto; }}
    table {{ border-collapse: collapse; width: 100%; min-width: 1100px; }}
    th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #eee; font-size: 13px; vertical-align: top; }}
    th {{ background: #fafafa; font-weight: 600; color: #555; position: sticky; top: 0; z-index: 1; }}
    td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
    .product {{ display: flex; gap: 10px; align-items: flex-start; max-width: 360px; }}
    .product img {{ width: 48px; height: 48px; object-fit: cover; border-radius: 6px; background: #f0f0f0; flex-shrink: 0; }}
    .product .name {{ font-size: 12px; line-height: 1.4; color: #333; }}
    .product .sku {{ font-size: 11px; color: #888; margin-top: 4px; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }}
    .badge-delivered {{ background: #dcfce7; color: #166534; }}
    .badge-transit {{ background: #dbeafe; color: #1d4ed8; }}
    .badge-cancelled {{ background: #fee2e2; color: #b91c1c; }}
    .badge-default {{ background: #f3f4f6; color: #374151; }}
    .empty {{ padding: 40px; text-align: center; color: #888; }}
    .footer {{ margin-top: 12px; font-size: 12px; color: #888; }}
    tr:hover td {{ background: #fafcff; }}
  </style>
</head>
<body>
  <h1>TikTok Shop 订单</h1>
  <p class="hint">数据来源：tiktok_orders.json · 生成时间：{generated_at}</p>

  <div class="cards" id="cards"></div>

  <div class="toolbar">
    <select id="regionFilter"><option value="">全部站点</option></select>
    <select id="statusFilter"><option value="">全部状态</option></select>
    <input id="searchInput" type="search" placeholder="搜索订单号 / SKU / 商品名 / 物流单号">
    <span id="countLabel" style="font-size:13px;color:#666;"></span>
  </div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>站点</th>
          <th>订单号</th>
          <th>状态</th>
          <th>下单时间</th>
          <th>商品</th>
          <th class="num">金额</th>
          <th>支付方式</th>
          <th>物流</th>
        </tr>
      </thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>
  <p class="footer">提示：先运行 <code>python3 tiktok_data.py</code> 拉取最新订单，再运行 <code>python3 build_orders_page.py</code> 刷新本页。</p>

  <script>
    var allRows = {data_json};

    function badgeClass(status) {{
      if (status === 'DELIVERED' || status === 'COMPLETED') return 'badge-delivered';
      if (status === 'IN_TRANSIT' || status === 'AWAITING_SHIPMENT' || status === 'AWAITING_COLLECTION') return 'badge-transit';
      if (status === 'CANCELLED') return 'badge-cancelled';
      return 'badge-default';
    }}

    function fmtAmount(row) {{
      if (!row.total_amount) return '—';
      return row.currency + ' ' + Number(row.total_amount).toLocaleString('zh-CN', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
    }}

    function primaryItem(row) {{
      if (!row.items || !row.items.length) return {{ name: '—', sku: '', image: '' }};
      var item = row.items[0];
      var suffix = row.item_count > 1 ? ' 等 ' + row.item_count + ' 件' : '';
      return {{
        name: (item.product_name || '—') + suffix,
        sku: [item.seller_sku, item.sku_name].filter(Boolean).join(' · '),
        image: item.sku_image || ''
      }};
    }}

    function matchesSearch(row, q) {{
      if (!q) return true;
      q = q.toLowerCase();
      var parts = [row.order_id, row.tracking_number, row.payment_method];
      (row.items || []).forEach(function (it) {{
        parts.push(it.product_name, it.sku_name, it.seller_sku);
      }});
      return parts.some(function (p) {{ return p && String(p).toLowerCase().indexOf(q) >= 0; }});
    }}

    function renderCards(rows) {{
      var byRegion = {{}};
      rows.forEach(function (r) {{
        byRegion[r.region] = (byRegion[r.region] || 0) + 1;
      }});
      var cards = document.getElementById('cards');
      cards.innerHTML =
        '<div class="card"><div class="label">订单总数</div><div class="value">' + rows.length + '</div></div>' +
        Object.keys(byRegion).sort().map(function (region) {{
          return '<div class="card"><div class="label">' + region + ' 站点</div><div class="value">' + byRegion[region] + '</div></div>';
        }}).join('');
    }}

    function fillFilters() {{
      var regions = {{}}, statuses = {{}};
      allRows.forEach(function (r) {{
        regions[r.region] = true;
        statuses[r.status] = r.status_label || r.status;
      }});
      var regionSel = document.getElementById('regionFilter');
      Object.keys(regions).sort().forEach(function (r) {{
        var opt = document.createElement('option');
        opt.value = r; opt.textContent = r;
        regionSel.appendChild(opt);
      }});
      var statusSel = document.getElementById('statusFilter');
      Object.keys(statuses).sort().forEach(function (s) {{
        var opt = document.createElement('option');
        opt.value = s; opt.textContent = statuses[s];
        statusSel.appendChild(opt);
      }});
    }}

    function renderTable() {{
      var region = document.getElementById('regionFilter').value;
      var status = document.getElementById('statusFilter').value;
      var q = document.getElementById('searchInput').value.trim();
      var rows = allRows.filter(function (r) {{
        if (region && r.region !== region) return false;
        if (status && r.status !== status) return false;
        return matchesSearch(r, q);
      }});

      renderCards(rows);
      document.getElementById('countLabel').textContent = '显示 ' + rows.length + ' / ' + allRows.length + ' 条';

      var tbody = document.getElementById('tbody');
      if (!rows.length) {{
        tbody.innerHTML = '<tr><td colspan="8" class="empty">没有匹配的订单</td></tr>';
        return;
      }}

      tbody.innerHTML = rows.map(function (row) {{
        var item = primaryItem(row);
        var img = item.image
          ? '<img src="' + item.image + '" alt="" loading="lazy">'
          : '<div style="width:48px;height:48px;border-radius:6px;background:#f0f0f0;"></div>';
        var logistics = [row.shipping_provider, row.tracking_number].filter(Boolean).join('<br>');
        return '<tr>' +
          '<td>' + row.region + '</td>' +
          '<td>' + row.order_id + '</td>' +
          '<td><span class="badge ' + badgeClass(row.status) + '">' + (row.status_label || row.status) + '</span></td>' +
          '<td>' + (row.create_time_fmt || '—') + '</td>' +
          '<td><div class="product">' + img + '<div><div class="name">' + item.name + '</div><div class="sku">' + item.sku + '</div></div></div></td>' +
          '<td class="num">' + fmtAmount(row) + '</td>' +
          '<td>' + (row.payment_method || '—') + (row.is_cod ? ' (COD)' : '') + '</td>' +
          '<td style="font-size:12px;">' + (logistics || '—') + '</td>' +
          '</tr>';
      }}).join('');
    }}

    ['regionFilter', 'statusFilter'].forEach(function (id) {{
      document.getElementById(id).addEventListener('change', renderTable);
    }});
    document.getElementById('searchInput').addEventListener('input', renderTable);

    fillFilters();
    renderTable();
  </script>
</body>
</html>
"""


def main():
    if not ORDERS_FILE.exists():
        raise SystemExit(f"未找到 {ORDERS_FILE}，请先运行 python3 tiktok_data.py")

    raw = json.loads(ORDERS_FILE.read_text(encoding="utf-8"))
    rows = flatten_orders(raw)
    OUTPUT_FILE.write_text(build_html(rows), encoding="utf-8")
    print(f"✅ 已生成 {OUTPUT_FILE}，共 {len(rows)} 条订单")
    print(f"   用浏览器打开: file://{OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()
