#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
完全按「SKU 信息表」（TikTok 后台导出的 *all_information*template*.csv）生成填写页：
一表展示所有 SKU（主图 + 商品名 + 采购成本输入），不依赖结算表。填好后导出 sku_costs.csv，再跑 build_profit_table.py 算利润。
若没有产品表则退回：从结算表提取 SKU 列表生成页面。

【旧成本保存说明】
- 保存文件：product_cost/sku_costs.csv（与产品表同目录）。
- 流程：页面里点「导出 sku_costs.csv」时写入该文件；下次运行本脚本或打开页面时，从该文件预填已填过的成本，新 SKU 留空。
- 浏览器还会把未导出的输入暂存到 localStorage（键 sku_costs_draft），关掉页面再打开仍会预填，但正式保存以导出到 sku_costs.csv 为准。
- 导出时：成本为 0 或未填的视为未更新，不会写入 CSV。
"""

import csv
import json
import re
from pathlib import Path

SETTLEMENT_CSV = "income_20260214145954(UTC+8).csv"
PRODUCT_COST_DIR = "product_cost"  # 产品表与 sku_costs.csv 所在目录
SKU_COSTS_CSV = "sku_costs.csv"
OUTPUT_HTML = "sku_cost_input.html"
PRODUCT_CSV_GLOB = "*all_information*template*.csv"


def norm_sku(s):
    if s is None:
        return None
    s = str(s).strip()
    if s == "" or s == "/":
        return None
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
        return str(f)
    except ValueError:
        return s


def load_settlement_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = [c.strip() for c in next(reader)]
        rows = list(reader)
    return header, rows


def extract_skus(header, rows):
    """提取所有订单行的 SKU，去重，保留 SKU ID、SKU name、Product name 用于展示。"""
    key_type = None
    key_sku = None
    key_sku_name = None
    key_product = None
    for i, c in enumerate(header):
        if c.strip() in ("Type ", "Type"):
            key_type = i
        if c.strip() == "SKU ID":
            key_sku = i
        if c.strip() == "SKU name":
            key_sku_name = i
        if c.strip() == "Product name":
            key_product = i
    if key_sku is None:
        return []

    seen = set()
    result = []
    for row in rows:
        if len(row) <= key_sku:
            continue
        typ = (row[key_type] or "").strip() if key_type is not None else ""
        if "Order" not in typ and "Refund" not in typ:
            continue
        sku_raw = row[key_sku].strip() if key_sku < len(row) else ""
        sku = norm_sku(sku_raw)
        if not sku or sku in seen:
            continue
        seen.add(sku)
        sku_name = (row[key_sku_name] or "").strip() if key_sku_name is not None else ""
        product = (row[key_product] or "").strip() if key_product is not None else ""
        result.append({
            "sku_id": sku,
            "sku_name": sku_name[:80] + "..." if len(sku_name) > 80 else sku_name,
            "product_name": product[:80] + "..." if len(product) > 80 else product,
        })
    return sorted(result, key=lambda x: x["sku_id"])


def load_existing_costs(path):
    """若已有 sku_costs.csv，读入用于预填。"""
    if not path.is_file():
        return {}
    out = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) >= 2:
                try:
                    sku_raw = str(row[0]).strip()
                    # 导出时用 ="SKU" 避免 Excel 科学计数法，读回时去掉公式外壳
                    if sku_raw.startswith('="') and sku_raw.endswith('"') and len(sku_raw) > 4:
                        sku_raw = sku_raw[2:-1].replace('""', '"').strip()
                    out[sku_raw] = str(row[1]).strip()
                except Exception:
                    pass
    return out


def _country_from_filename(path):
    """从文件名提取国家代码：末尾 _xx 两个字母（如 _vn -> VN），否则默认 MY。"""
    stem = path.stem
    suffix = stem.split("_")[-1].upper() if stem else ""
    return suffix if len(suffix) == 2 else "MY"


def _read_one_product_csv(path):
    """读单个产品表 CSV，返回 [ { sku_id, product_name, image_url, sku_name, country }, ... ]。"""
    country = _country_from_filename(path)
    result = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = [c.strip() for c in next(reader)]
        idx = {h: i for i, h in enumerate(header)}
        i_sku = idx.get("sku_id", -1)
        i_name = idx.get("product_name", -1)
        i_img = idx.get("main_image", -1)
        i_var = idx.get("variation_value", -1)
        if i_sku < 0:
            return []
        for row in reader:
            if len(row) <= max(i_sku, i_name, i_img):
                continue
            first = (row[0] or "").strip()
            if not re.match(r"^\d{10,}$", first):
                continue
            sku_raw = (row[i_sku] or "").strip()
            if not sku_raw or not re.match(r"^\d+$", sku_raw):
                continue
            name = (row[i_name] or "").strip() if i_name >= 0 else ""
            img = (row[i_img] or "").strip() if i_img >= 0 else ""
            var = (row[i_var] or "").strip() if i_var >= 0 else ""
            if name and len(name) > 600:
                name = name[:600] + "..."
            image_url = img if img and (img.startswith("http://") or img.startswith("https://")) else ""
            result.append({
                "sku_id": sku_raw,
                "sku_name": var,
                "product_name": name,
                "image_url": image_url,
                "country": country,
            })
    return result


def load_sku_list_from_product_table(base_dir):
    """
    合并多国产品表（*all_information*template*.csv）：按 sku_id 去重，同一 SKU 只保留一条。
    优先保留有主图的那条；若都有图则保留先读到的。收集每个 SKU 所属国家列表（countries）。
    最后按 sku_id 排序返回。
    """
    product_files = sorted(Path(base_dir).glob(PRODUCT_CSV_GLOB))
    if not product_files:
        return []
    by_sku = {}
    for path in product_files:
        try:
            rows = _read_one_product_csv(path)
            for r in rows:
                sid = r["sku_id"]
                country = r.pop("country", "MY")
                if sid not in by_sku:
                    by_sku[sid] = {**r, "countries": [country]}
                else:
                    if country not in by_sku[sid]["countries"]:
                        by_sku[sid]["countries"].append(country)
                    if not by_sku[sid].get("image_url") and r.get("image_url"):
                        by_sku[sid]["image_url"] = r["image_url"]
                    if not by_sku[sid].get("product_name") and r.get("product_name"):
                        by_sku[sid]["product_name"] = r["product_name"]
                    if not by_sku[sid].get("sku_name") and r.get("sku_name"):
                        by_sku[sid]["sku_name"] = r["sku_name"]
        except Exception as e:
            print(f"读取产品表 {path.name} 时出错: {e}")
    for v in by_sku.values():
        v["countries"] = sorted(v["countries"])
    return sorted(by_sku.values(), key=lambda x: x["sku_id"])


def load_product_table(base_dir):
    """
    从 TikTok 后台导出的「全部信息」产品表读取。
    返回 (by_sku_exact, by_prefix): 精确 sku_id -> info，前 6 位前缀 -> info（结算表常为科学计数法，只保留前几位有效数字）。
    """
    product_files = list(Path(base_dir).glob(PRODUCT_CSV_GLOB))
    if not product_files:
        return {}, {}
    path = product_files[0]
    by_sku = {}
    by_prefix = {}
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = [c.strip() for c in next(reader)]
            idx = {h: i for i, h in enumerate(header)}
            i_sku = idx.get("sku_id", -1)
            i_name = idx.get("product_name", -1)
            i_img = idx.get("main_image", -1)
            if i_sku < 0:
                return {}, {}
            for row in reader:
                if len(row) <= max(i_sku, i_name, i_img):
                    continue
                first = (row[0] or "").strip()
                if not re.match(r"^\d{10,}$", first):
                    continue
                sku_raw = (row[i_sku] or "").strip()
                if not sku_raw or not re.match(r"^\d+$", sku_raw):
                    continue
                name = (row[i_name] or "").strip() if i_name >= 0 else ""
                img = (row[i_img] or "").strip() if i_img >= 0 else ""
                if name:
                    name = name[:200] + "..." if len(name) > 200 else name
                info = {"product_name": name, "image_url": img if img and (img.startswith("http://") or img.startswith("https://")) else ""}
                by_sku[sku_raw] = info
                prefix = sku_raw[:6]
                if prefix not in by_prefix:
                    by_prefix[prefix] = info
    except Exception as e:
        print(f"读取产品表 {path} 时出错: {e}")
        return {}, {}
    return by_sku, by_prefix


def merge_sku_with_products(settlement_skus, product_by_sku, product_by_prefix):
    """把结算里的 SKU 列表与产品表合并；结算表 SKU 多为科学计数法转成的整数，用前 6 位与前缀表匹配。"""
    out = []
    for s in settlement_skus:
        sku_id = s["sku_id"]
        rec = {"sku_id": sku_id, "sku_name": s.get("sku_name", ""), "product_name": s.get("product_name", ""), "image_url": ""}
        p = product_by_sku.get(sku_id) or (product_by_prefix.get(sku_id[:6]) if len(sku_id) >= 6 else None)
        if p:
            if p.get("image_url"):
                rec["image_url"] = p["image_url"]
            if p.get("product_name"):
                rec["product_name"] = p["product_name"]
        out.append(rec)
    return out


def main():
    base = Path(__file__).resolve().parent
    product_dir = base / PRODUCT_COST_DIR
    if not product_dir.is_dir():
        product_dir = base
    settlement_path = base / SETTLEMENT_CSV
    sku_costs_path = product_dir / SKU_COSTS_CSV
    html_path = product_dir / OUTPUT_HTML  # 成本填写页生成到 product_cost/

    # 优先：合并多国产品表（按 sku_id 去重），生成列表；产品表与 sku_costs 均在 product_cost 下
    product_files = sorted(product_dir.glob(PRODUCT_CSV_GLOB))
    skus = load_sku_list_from_product_table(product_dir)
    if skus:
        if len(product_files) > 1:
            print(f"已合并 {len(product_files)} 个产品表（{', '.join(p.name for p in product_files)}），去重后共 {len(skus)} 个 SKU。")
        else:
            print(f"已按产品表（SKU 信息表）生成 {len(skus)} 个 SKU。")
        print("请填好采购成本后导出 sku_costs.csv。")
    else:
        # 没有产品表时，从结算表提取 SKU 列表
        if not settlement_path.is_file():
            print("未找到产品表（*all_information*template*.csv）且未找到结算表。请先放入产品表或结算表。")
            return
        header, rows = load_settlement_csv(settlement_path)
        settlement_skus = extract_skus(header, rows)
        product_by_sku, product_by_prefix = load_product_table(product_dir)
        skus = merge_sku_with_products(settlement_skus, product_by_sku, product_by_prefix)
        print(f"已从结算表提取 {len(skus)} 个 SKU 生成页面。")

    existing = load_existing_costs(sku_costs_path)
    all_countries = sorted(set(c for s in skus for c in s.get("countries", ["MY"])))
    product_cost_path_abs = str(product_dir.resolve())

    html_content = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>按 SKU 信息表填写采购成本</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 16px; background: #f5f5f5; }
    h1 { font-size: 1.25rem; color: #1a1a1a; margin-bottom: 8px; }
    .hint { color: #666; font-size: 0.875rem; margin-bottom: 16px; }
    .toolbar { margin-bottom: 12px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .filter-label { font-size: 14px; color: #333; }
    .filter-select { padding: 8px 12px; border: 1px solid #dadce0; border-radius: 6px; font-size: 14px; min-width: 100px; }
    .country-cell { font-size: 0.8125rem; color: #555; white-space: nowrap; }
    .btn { padding: 10px 16px; border-radius: 8px; border: none; font-size: 14px; cursor: pointer; }
    .btn-primary { background: #1a73e8; color: #fff; }
    .btn-primary:hover { background: #1557b0; }
    .btn-secondary { background: #fff; color: #333; border: 1px solid #dadce0; }
    .btn-secondary:hover { background: #f8f9fa; }
    table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
    th, td { padding: 12px 16px; text-align: left; border-bottom: 1px solid #eee; }
    th { background: #f8f9fa; font-weight: 600; color: #333; }
    td input { width: 100%; max-width: 120px; padding: 8px 10px; border: 1px solid #dadce0; border-radius: 6px; font-size: 14px; }
    td input:focus { outline: none; border-color: #1a73e8; }
    .product-img { width: 56px; height: 56px; object-fit: cover; border-radius: 6px; background: #eee; }
    .product-img-none { width: 56px; height: 56px; background: #eee; border-radius: 6px; color: #999; font-size: 11px; display: inline-flex; align-items: center; justify-content: center; }
    .sku-id { font-family: monospace; font-size: 0.875rem; color: #555; }
    .sku-desc { font-size: 0.8125rem; color: #333; max-width: 420px; white-space: normal; line-height: 1.4; }
    .sku-desc .spec { color: #666; font-size: 0.75rem; margin-top: 4px; }
    .export-hint { margin-top: 16px; padding: 12px; background: #e8f0fe; border-radius: 8px; font-size: 0.875rem; color: #1a73e8; }
    .path-box { margin-top: 8px; padding: 10px 12px; background: #fff; border: 1px solid #dadce0; border-radius: 6px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .path-box code { font-size: 0.8125rem; color: #333; word-break: break-all; flex: 1; min-width: 0; }
    .btn-small { padding: 6px 10px; font-size: 12px; }
  </style>
</head>
<body>
  <h1>按 SKU 信息表填写采购成本</h1>
  <p class="hint">产品表与成本表均在 <strong>product_cost</strong> 文件夹。已填数据会从 product_cost/sku_costs.csv 预填，新 SKU 留空；请定期导出以保存。导出时仅输出成本 &gt; 0 的 SKU（成本为 0 或未填视为未更新，不写入）。</p>
  <div class="toolbar">
    <label class="filter-label">国家筛选：</label>
    <select class="filter-select" id="filterCountry">
      <option value="">全部</option>
    </select>
    <label class="filter-label">采购成本：</label>
    <select class="filter-select" id="filterCost">
      <option value="">全部</option>
      <option value="filled">已填</option>
      <option value="empty">未填(0)</option>
    </select>
    <button class="btn btn-primary" id="btnExport">导出 sku_costs.csv</button>
    <button class="btn btn-secondary" id="btnImport">导入 sku_costs.csv</button>
    <input type="file" accept=".csv" id="fileImport" style="display:none">
    <button class="btn btn-secondary" id="btnClear">清空所有成本</button>
  </div>
  <table>
    <thead>
      <tr>
        <th>商品图</th>
        <th>SKU ID</th>
        <th>国家</th>
        <th>商品名称</th>
        <th>采购成本（单件）</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
  <p class="export-hint"><strong>导出说明：</strong>点击「导出」后，文件会下载到浏览器的<strong>「下载」文件夹</strong>，不会自动保存到 product_cost。导出的 SKU 列已按文本格式写入，用 Excel 打开时会完整显示数字（不会变成科学计数法、不丢失末尾几位）。请将下载的 sku_costs.csv <strong>移动到下方路径的 product_cost 文件夹</strong>中，下次打开页面或运行 build_profit_table.py 才会从该文件预填/读取成本。</p>
  <div class="path-box">
    <code id="productCostPath">""" + product_cost_path_abs.replace("<", "&lt;").replace(">", "&gt;") + """</code>
    <button type="button" class="btn btn-secondary btn-small" id="btnCopyPath">复制路径</button>
  </div>

  <script>
    const SKU_LIST = """ + json.dumps(skus, ensure_ascii=False) + """;
    const EXISTING = """ + json.dumps(existing, ensure_ascii=False) + """;
    const ALL_COUNTRIES = """ + json.dumps(all_countries, ensure_ascii=False) + """;
    const PRODUCT_COST_PATH = """ + json.dumps(product_cost_path_abs, ensure_ascii=False) + """;

    const STORAGE_KEY = 'sku_costs_draft';

    function escapeHtml(s) {
      if (s == null) return '';
      var div = document.createElement('div');
      div.textContent = s;
      return div.innerHTML;
    }

    function getStoredCost(skuId) {
      try {
        var raw = localStorage.getItem(STORAGE_KEY);
        if (raw) { var o = JSON.parse(raw); if (o[skuId] !== undefined) return o[skuId]; }
      } catch (e) {}
      return '';
    }
    function saveCost(skuId, value) {
      try {
        var o = {};
        var raw = localStorage.getItem(STORAGE_KEY);
        if (raw) o = JSON.parse(raw);
        o[skuId] = value;
        localStorage.setItem(STORAGE_KEY, JSON.stringify(o));
      } catch (e) {}
    }

    var filterSelect = document.getElementById('filterCountry');
    ALL_COUNTRIES.forEach(function (c) { var opt = document.createElement('option'); opt.value = c; opt.textContent = c; filterSelect.appendChild(opt); });

    const tbody = document.getElementById('tbody');
    SKU_LIST.forEach(function (item) {
      const tr = document.createElement('tr');
      const cost = (EXISTING[item.sku_id] !== undefined && EXISTING[item.sku_id] !== '') ? EXISTING[item.sku_id] : getStoredCost(item.sku_id);
      const countries = (item.countries || []).join(' ');
      const countryDisplay = (item.countries || []).join(', ') || '-';
      tr.setAttribute('data-country', countries);
      const imgCell = item.image_url
        ? '<td><img class="product-img" src="' + escapeHtml(item.image_url) + '" alt="" onerror="this.style.display=\\'none\\';this.nextElementSibling.style.display=\\'flex\\';"><span class="product-img-none" style="display:none">无图</span></td>'
        : '<td><span class="product-img-none">无图</span></td>';
      tr.innerHTML =
        imgCell +
        '<td class="sku-id">' + escapeHtml(item.sku_id) + '</td>' +
        '<td class="country-cell">' + escapeHtml(countryDisplay) + '</td>' +
        (function () { var name = item.product_name || ''; var spec = item.sku_name || ''; var full = (name && spec) ? (name + ' · 规格：' + spec) : (name || spec || '-'); var html = '<td class="sku-desc" title="' + escapeHtml(full) + '">' + escapeHtml(name || '-'); if (spec) html += '<div class="spec">规格：' + escapeHtml(spec) + '</div>'; html += '</td>'; return html; })() +
        '<td><input type="number" step="0.01" min="0" placeholder="0" data-sku="' + escapeHtml(item.sku_id) + '" value="' + escapeHtml(cost) + '"></td>';
      tbody.appendChild(tr);
    });

    function applyFilters() {
      var countryVal = document.getElementById('filterCountry').value.trim();
      var costVal = document.getElementById('filterCost').value;
      tbody.querySelectorAll('tr').forEach(function (tr) {
        var c = tr.getAttribute('data-country') || '';
        var countryMatch = countryVal === '' || c.indexOf(countryVal) >= 0;
        var input = tr.querySelector('input[data-sku]');
        var v = input ? input.value.trim() : '';
        var num = parseFloat(v);
        var costFilled = v !== '' && !isNaN(num) && num > 0;
        var costMatch = costVal === '' || (costVal === 'filled' && costFilled) || (costVal === 'empty' && !costFilled);
        tr.style.display = (countryMatch && costMatch) ? '' : 'none';
      });
    }

    filterSelect.addEventListener('change', applyFilters);
    document.getElementById('filterCost').addEventListener('change', applyFilters);

    tbody.addEventListener('input', function (e) {
      if (e.target.matches('input[data-sku]')) saveCost(e.target.getAttribute('data-sku'), e.target.value);
    });

    document.getElementById('btnExport').onclick = function () {
      var rows = [['SKU ID', '采购成本']];
      document.querySelectorAll('tbody input').forEach(function (input) {
        var sku = input.getAttribute('data-sku');
        var val = (input.value || '').trim();
        var num = parseFloat(val);
        if (sku && val !== '' && !isNaN(num) && num > 0) {
          // 用 ="SKU" 形式导出，Excel 打开时按文本显示，避免科学计数法丢失末尾数字
          rows.push(['=\"' + sku + '\"', val]);
        }
      });
      var csv = rows.map(function (r) { return r.map(function (c) { return '"' + String(c).replace(/"/g, '""') + '"'; }).join(','); }).join('\\n');
      var blob = new Blob(['\\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'sku_costs.csv';
      a.click();
      URL.revokeObjectURL(a.href);
    };

    document.getElementById('btnClear').onclick = function () {
      document.querySelectorAll('tbody input').forEach(function (input) { input.value = ''; });
      try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
    };

    document.getElementById('btnImport').onclick = function () { document.getElementById('fileImport').click(); };
    document.getElementById('fileImport').addEventListener('change', function (e) {
      var file = e.target.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function (ev) {
        var text = (ev.target.result || '').replace(/^\\ufeff/, '');
        var lines = text.split(/\\r?\\n/).filter(function (l) { return l.trim(); });
        if (lines.length < 2) { alert('文件为空或仅有表头'); e.target.value = ''; return; }
        var parsed = [];
        for (var i = 1; i < lines.length; i++) {
          var parts = lines[i].split(',');
          if (parts.length < 2) continue;
          var sku = (parts[0] || '').trim().replace(/^"|"$/g, '').replace(/""/g, '"');
          if (sku.indexOf('=\\"') === 0 && sku.length > 4 && sku.charAt(sku.length - 1) === '\"') sku = sku.slice(2, -1).replace(/""/g, '\"');
          var cost = (parts[1] || '').trim().replace(/^"|"$/g, '').replace(/""/g, '"');
          if (sku) parsed.push({ sku: sku, cost: cost });
        }
        var bySku = {};
        parsed.forEach(function (r) { bySku[r.sku] = r.cost; });
        var count = 0;
        document.querySelectorAll('tbody input[data-sku]').forEach(function (input) {
          var sku = input.getAttribute('data-sku');
          if (bySku[sku] !== undefined) { input.value = bySku[sku]; saveCost(sku, bySku[sku]); count++; }
        });
        alert('已导入 ' + count + ' 条成本数据');
        e.target.value = '';
      };
      reader.readAsText(file, 'UTF-8');
    });

    document.getElementById('btnCopyPath').onclick = function () {
      try {
        navigator.clipboard.writeText(PRODUCT_COST_PATH);
        var btn = this;
        var old = btn.textContent;
        btn.textContent = '已复制';
        setTimeout(function () { btn.textContent = old; }, 1500);
      } catch (e) {
        var code = document.getElementById('productCostPath');
        var sel = window.getSelection();
        var range = document.createRange();
        range.selectNodeContents(code);
        sel.removeAllRanges();
        sel.addRange(range);
        document.execCommand('copy');
        sel.removeAllRanges();
      }
    };
  </script>
</body>
</html>
"""
    html_path.write_text(html_content, encoding="utf-8")
    print(f"已生成：{html_path}")
    print("请用浏览器打开该文件，填写采购成本后点击「导出」；导出会写入 product_cost/sku_costs.csv（仅输出成本>0 的 SKU）。")


if __name__ == "__main__":
    main()
