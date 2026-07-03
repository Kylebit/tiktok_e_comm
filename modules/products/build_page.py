"""生成商品成本维护页面。"""

from __future__ import annotations

import json
from pathlib import Path

from core.config import ROOT
from core.db import connect, init_db

OUTPUT = ROOT / "web" / "costs.html"


def load_page_data() -> list[dict]:
    init_db()
    conn = connect()
    rows = conn.execute(
        """SELECT p.sku_id, p.product_name, p.sku_name, p.seller_sku,
                  p.image_url, s.region, p.price, p.currency, p.stock, p.status,
                  COALESCE(c.cost_cny, 0) AS cost_cny
           FROM products p
           LEFT JOIN shops s ON s.cipher = p.shop_cipher
           LEFT JOIN sku_costs c ON c.sku_id = p.sku_id
           WHERE p.status = 'ACTIVATE'
           ORDER BY s.region, p.product_name, p.sku_name"""
    ).fetchall()
    conn.close()

    merged: dict[str, dict] = {}
    for r in rows:
        sku = r["sku_id"]
        if sku not in merged:
            merged[sku] = {
                "sku_id": sku,
                "product_name": r["product_name"] or "",
                "sku_name": r["sku_name"] or "",
                "seller_sku": r["seller_sku"] or "",
                "image_url": r["image_url"] or "",
                "regions": [],
                "price_samples": [],
                "stock_total": 0,
                "status": r["status"] or "",
                "cost_cny": float(r["cost_cny"] or 0),
            }
        rec = merged[sku]
        if r["region"] and r["region"] not in rec["regions"]:
            rec["regions"].append(r["region"])
        if r["price"]:
            rec["price_samples"].append(f"{r['region']} {r['currency']} {r['price']}")
        rec["stock_total"] += int(r["stock"] or 0)
        if float(r["cost_cny"] or 0) > 0:
            rec["cost_cny"] = float(r["cost_cny"])

    items = list(merged.values())
    for it in items:
        it["regions"] = sorted(it["regions"])
        it["price_label"] = it["price_samples"][0] if it["price_samples"] else ""
    return items


def build_html(output: Path | None = None) -> Path:
    items = load_page_data()
    if not items:
        raise RuntimeError("暂无商品数据，请先运行: python3 main.py products sync")

    out = output or OUTPUT
    out.parent.mkdir(parents=True, exist_ok=True)
    data_json = json.dumps(items, ensure_ascii=False)
    generated = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>商品采购成本</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 16px; background: #f5f5f5; }}
    h1 {{ font-size: 1.25rem; margin: 0 0 4px; }}
    .hint {{ color: #666; font-size: 13px; margin-bottom: 12px; }}
    .toolbar {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; align-items: center; }}
    input, select {{ padding: 8px 10px; border: 1px solid #ccc; border-radius: 6px; font-size: 14px; }}
    .btn {{ padding: 8px 14px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; }}
    .btn-primary {{ background: #1a73e8; color: #fff; }}
    .btn-secondary {{ background: #fff; border: 1px solid #ccc; color: #333; }}
    .stats {{ font-size: 13px; color: #555; margin-left: auto; }}
    .toast {{ position: fixed; bottom: 20px; right: 20px; background: #323232; color: #fff; padding: 10px 16px; border-radius: 8px; display: none; z-index: 9; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid #eee; font-size: 13px; vertical-align: middle; }}
    th {{ background: #fafafa; position: sticky; top: 0; z-index: 1; }}
    .img {{ width: 52px; height: 52px; object-fit: cover; border-radius: 6px; background: #eee; }}
    .name {{ max-width: 360px; line-height: 1.35; }}
    .sub {{ color: #888; font-size: 12px; margin-top: 4px; }}
    .sku {{ font-family: monospace; font-size: 12px; color: #555; }}
    .cost-input {{ width: 88px; padding: 6px 8px; border: 1px solid #ccc; border-radius: 6px; }}
    .missing {{ background: #fffbeb; }}
    .saved {{ outline: 2px solid #22c55e; transition: outline .3s; }}
    .badge {{ display: inline-block; background: #eef2ff; color: #4338ca; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-right: 4px; }}
  </style>
</head>
<body>
  <h1>商品采购成本</h1>
  <p class="hint">仅展示在售商品（ACTIVATE）· 默认成本已从 CURSOR 导入 · 生成时间 {generated}<br>
  保存：点击「保存全部」写入本地数据库（需先 <code>python3 main.py products serve</code>）</p>
  <div class="toolbar">
    <input id="search" type="search" placeholder="搜索 SKU / 商品名 / 卖家 SKU">
    <select id="regionFilter"><option value="">全部站点</option></select>
    <select id="costFilter">
      <option value="">全部</option>
      <option value="missing">未填成本</option>
      <option value="filled">已填成本</option>
    </select>
    <button class="btn btn-primary" id="saveAll">保存全部</button>
    <button class="btn btn-secondary" id="exportCsv">导出 CSV</button>
    <span class="stats" id="stats"></span>
  </div>
  <table>
    <thead>
      <tr>
        <th>图</th><th>商品</th><th>SKU ID</th><th>站点</th><th>售价</th><th>库存</th><th>成本(¥)</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
  <div class="toast" id="toast"></div>
  <script>
    var items = {data_json};
    var API = window.location.origin;

    function toast(msg) {{
      var el = document.getElementById('toast');
      el.textContent = msg;
      el.style.display = 'block';
      setTimeout(function() {{ el.style.display = 'none'; }}, 2500);
    }}

    function fillRegions() {{
      var set = {{}};
      items.forEach(function(it) {{ (it.regions || []).forEach(function(r) {{ set[r] = 1; }}); }});
      var sel = document.getElementById('regionFilter');
      Object.keys(set).sort().forEach(function(r) {{
        var o = document.createElement('option');
        o.value = r; o.textContent = r;
        sel.appendChild(o);
      }});
    }}

    function filtered() {{
      var q = document.getElementById('search').value.trim().toLowerCase();
      var region = document.getElementById('regionFilter').value;
      var costF = document.getElementById('costFilter').value;
      return items.filter(function(it) {{
        if (region && (it.regions || []).indexOf(region) < 0) return false;
        if (costF === 'missing' && (it.cost_cny || 0) > 0) return false;
        if (costF === 'filled' && !(it.cost_cny > 0)) return false;
        if (!q) return true;
        var blob = [it.sku_id, it.product_name, it.sku_name, it.seller_sku].join(' ').toLowerCase();
        return blob.indexOf(q) >= 0;
      }});
    }}

    function render() {{
      var rows = filtered();
      var missing = rows.filter(function(it) {{ return !(it.cost_cny > 0); }}).length;
      document.getElementById('stats').textContent =
        '显示 ' + rows.length + ' / ' + items.length + ' · 未填成本 ' + missing;

      document.getElementById('tbody').innerHTML = rows.map(function(it, idx) {{
        var img = it.image_url
          ? '<img class="img" src="' + it.image_url + '" alt="" loading="lazy">'
          : '<div class="img"></div>';
        var regions = (it.regions || []).map(function(r) {{
          return '<span class="badge">' + r + '</span>';
        }}).join('');
        var miss = (it.cost_cny || 0) > 0 ? '' : ' missing';
        return '<tr class="row' + miss + '" data-sku="' + it.sku_id + '">' +
          '<td>' + img + '</td>' +
          '<td class="name"><div>' + (it.product_name || '-') + '</div>' +
            '<div class="sub">' + (it.sku_name || '') + (it.seller_sku ? ' · ' + it.seller_sku : '') + '</div></td>' +
          '<td class="sku">' + it.sku_id + '</td>' +
          '<td>' + regions + '</td>' +
          '<td>' + (it.price_label || '-') + '</td>' +
          '<td>' + (it.stock_total || 0) + '</td>' +
          '<td><input type="number" step="0.01" min="0" class="cost-input" data-sku="' + it.sku_id + '" value="' +
            ((it.cost_cny > 0) ? it.cost_cny : '') + '" placeholder="¥"></td></tr>';
      }}).join('');
    }}

    function collectCosts() {{
      var out = [];
      document.querySelectorAll('.cost-input').forEach(function(inp) {{
        var v = parseFloat(inp.value);
        if (!isNaN(v) && v > 0) out.push({{ sku_id: inp.dataset.sku, cost_cny: v }});
      }});
      return out;
    }}

    document.getElementById('saveAll').onclick = function() {{
      var costs = collectCosts();
      fetch(API + '/api/costs', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ costs: costs }})
      }}).then(function(r) {{ return r.json(); }}).then(function(res) {{
        if (res.ok) {{
          costs.forEach(function(c) {{
            var it = items.find(function(x) {{ return x.sku_id === c.sku_id; }});
            if (it) it.cost_cny = c.cost_cny;
          }});
          toast('已保存 ' + res.saved + ' 条');
          render();
        }} else toast(res.error || '保存失败');
      }}).catch(function() {{ toast('无法连接服务，请先运行 products serve'); }});
    }};

    document.getElementById('exportCsv').onclick = function() {{
      window.open(API + '/api/costs/export.csv', '_blank');
    }};

    ['search', 'regionFilter', 'costFilter'].forEach(function(id) {{
      document.getElementById(id).addEventListener('input', render);
      document.getElementById(id).addEventListener('change', render);
    }});

    fillRegions();
    render();
  </script>
</body>
</html>"""
    out.write_text(html, encoding="utf-8")
    return out
