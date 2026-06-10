#!/usr/bin/env python3
"""
从 TikTok Shop Finance API 拉取结算明细，导出 CURSOR 兼容 CSV。

用法:
  python3 tiktok_settlement.py              # 拉取昨天
  python3 tiktok_settlement.py 2026-06-01   # 拉取指定日期
  python3 tiktok_settlement.py --days 7     # 拉取近 7 天（按天拆分文件）
"""

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tiktok_data import api_get, get_shops, load_token

INCOME_DIR = Path("CURSOR/Income_Data")
OUTPUT_HTML = Path("settlement_summary.html")

TYPE_LABELS = {
    "ORDER": "Order",
    "GMV_PAYMENT_FOR_TIKTOK_ADS": "GMV Payment for TikTok Ads",
    "LOGISTICS_REIMBURSEMENT": "Logistics reimbursement",
}

CSV_COLUMNS = [
    "Statement Date", "Statement ID", "Currency", "Type ", "Order/adjustment ID  ",
    "SKU ID", "Quantity", "Product name", "SKU name",
    "Total settlement amount", "Total Revenue", "Subtotal after seller discounts",
    "Subtotal before discounts", "Seller discounts",
    "Total Fees", "Transaction fee", "TikTok Shop commission fee",
    "Actual shipping fee", "Customer shipping fee",
    "Affiliate Commission", "Affiliate Shop Ads commission",
    "Customer payment", "Customer refund", "Platform discounts",
    "Ajustment amount", "Related order ID",
]


def parse_args():
    p = argparse.ArgumentParser(description="拉取 TikTok Shop 结算并导出 CSV")
    p.add_argument("date", nargs="?", help="目标日期 YYYY-MM-DD，默认昨天")
    p.add_argument("--days", type=int, help="拉取近 N 天（忽略 date）")
    p.add_argument("--no-profit", action="store_true", help="不自动生成 CURSOR 利润页")
    return p.parse_args()


def day_range_utc(day: datetime.date):
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def fmt_date(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y/%m/%d")


def yymmdd(day: datetime.date) -> str:
    return day.strftime("%y%m%d")


def fnum(val, ndigits=2):
    if val in (None, "", "/"):
        return 0.0
    try:
        return round(float(val), ndigits)
    except (TypeError, ValueError):
        return 0.0


def paginate_get(access_token, path, query_params, list_key=None):
    """GET 分页，返回合并后的 data 列表或 dict 列表。"""
    items = []
    page_token = ""
    while True:
        qp = dict(query_params)
        if page_token:
            qp["page_token"] = page_token
        result = api_get(path, access_token, qp, debug=False)
        if result.get("code") != 0:
            raise RuntimeError(f"{path} 失败: {result.get('message', result)}")
        data = result.get("data") or {}
        if list_key:
            batch = data.get(list_key, [])
            if isinstance(batch, list):
                items.extend(batch)
        else:
            items.append(data)
        page_token = data.get("next_page_token") or ""
        if not page_token:
            break
        time.sleep(0.15)
    return items if list_key else items


def fetch_statements(access_token, cipher, ge, lt):
    return paginate_get(
        access_token,
        "/finance/202309/statements",
        {
            "shop_cipher": cipher,
            "sort_field": "statement_time",
            "statement_time_ge": str(ge),
            "statement_time_lt": str(lt),
            "page_size": "50",
        },
        list_key="statements",
    )


def fetch_statement_transactions(access_token, cipher, statement_id):
    return paginate_get(
        access_token,
        f"/finance/202309/statements/{statement_id}/statement_transactions",
        {
            "shop_cipher": cipher,
            "sort_field": "order_create_time",
            "page_size": "100",
        },
        list_key="statement_transactions",
    )


def fetch_orders_batch(access_token, cipher, order_ids: list) -> dict:
    """批量拉订单详情，返回 order_id -> order dict。"""
    out = {}
    ids = [oid for oid in order_ids if oid]
    for i in range(0, len(ids), 20):
        chunk = ids[i : i + 20]
        result = api_get(
            "/order/202309/orders",
            access_token,
            {"shop_cipher": cipher, "ids": ",".join(chunk)},
            debug=False,
        )
        if result.get("code") != 0:
            continue
        for order in result.get("data", {}).get("orders", []):
            out[str(order.get("id", ""))] = order
        time.sleep(0.15)
    return out


def split_amount(total, weights):
    if not weights:
        return []
    if len(weights) == 1:
        return [total]
    s = sum(weights)
    if s == 0:
        return [total / len(weights)] * len(weights)
    return [total * w / s for w in weights]


def tx_type_label(tx_type: str) -> str:
    return TYPE_LABELS.get(tx_type or "", tx_type or "Unknown")


def tx_order_id(tx) -> str:
    t = tx.get("type", "")
    if t == "ORDER":
        return str(tx.get("order_id") or "")
    return str(tx.get("adjustment_id") or tx.get("id") or tx.get("order_id") or "")


def base_row(statement_id, statement_time, currency, tx):
    return {
        "Statement Date": fmt_date(statement_time),
        "Statement ID": str(statement_id),
        "Currency": currency or tx.get("currency", ""),
        "Type ": tx_type_label(tx.get("type", "")),
        "Order/adjustment ID  ": tx_order_id(tx),
        "Total settlement amount": fnum(tx.get("settlement_amount")),
        "Total Revenue": fnum(tx.get("revenue_amount")),
        "Subtotal after seller discounts": fnum(tx.get("after_seller_discounts_subtotal_amount")),
        "Subtotal before discounts": fnum(tx.get("gross_sales_amount")),
        "Seller discounts": fnum(tx.get("seller_discount_amount")),
        "Total Fees": fnum(tx.get("fee_amount")),
        "Transaction fee": fnum(tx.get("transaction_fee_amount")),
        "TikTok Shop commission fee": fnum(tx.get("platform_commission_amount")),
        "Actual shipping fee": fnum(tx.get("actual_shipping_fee_amount")),
        "Customer shipping fee": fnum(tx.get("customer_shipping_fee_amount")),
        "Affiliate Commission": fnum(tx.get("affiliate_commission_amount")),
        "Affiliate Shop Ads commission": fnum(tx.get("affiliate_ads_commission_amount")),
        "Customer payment": fnum(tx.get("customer_payment_amount")),
        "Customer refund": fnum(tx.get("customer_refund_amount")),
        "Platform discounts": fnum(tx.get("platform_discount_amount")),
        "Ajustment amount": fnum(tx.get("adjustment_amount")),
        "Related order ID": str(tx.get("adjustment_order_id") or tx.get("order_id") or ""),
    }


def expand_order_rows(row, tx, order):
    items = order.get("line_items") or []
    if not items:
        row["SKU ID"] = "/"
        row["Quantity"] = ""
        row["Product name"] = "/"
        row["SKU name"] = "/"
        return [row]

    weights = [max(fnum(it.get("sale_price")), 0.01) for it in items]
    numeric_keys = [
        "Total settlement amount", "Total Revenue", "Subtotal after seller discounts",
        "Subtotal before discounts", "Seller discounts", "Total Fees", "Transaction fee",
        "TikTok Shop commission fee", "Actual shipping fee", "Customer shipping fee",
        "Affiliate Commission", "Affiliate Shop Ads commission",
        "Customer payment", "Customer refund", "Platform discounts", "Ajustment amount",
    ]
    splits = {k: split_amount(row[k], weights) for k in numeric_keys}
    rows = []
    for i, item in enumerate(items):
        r = dict(row)
        r["SKU ID"] = str(item.get("sku_id") or "")
        r["Quantity"] = "1"
        r["Product name"] = item.get("product_name") or ""
        r["SKU name"] = item.get("sku_name") or ""
        for k in numeric_keys:
            r[k] = round(splits[k][i], 2)
        rows.append(r)
    return rows


def expand_non_order_row(row):
    row["SKU ID"] = "/"
    row["Quantity"] = ""
    row["Product name"] = "/"
    row["SKU name"] = "/"
    return [row]


def collect_shop_rows(access_token, shop, ge, lt):
    cipher = shop.get("cipher") or shop.get("shop_cipher", "")
    region = shop.get("region", "?")
    statements = fetch_statements(access_token, cipher, ge, lt)
    if not statements:
        return region, [], statements

    txs = []
    for st in statements:
        sid = st.get("id")
        st_time = st.get("statement_time")
        currency = st.get("currency", "")
        txs.extend(
            (sid, st_time, currency, tx)
            for tx in fetch_statement_transactions(access_token, cipher, sid)
        )

    order_ids = [tx[3].get("order_id") for tx in txs if tx[3].get("type") == "ORDER"]
    orders = fetch_orders_batch(access_token, cipher, order_ids)

    rows = []
    for sid, st_time, currency, tx in txs:
        base = base_row(sid, st_time, currency, tx)
        if tx.get("type") == "ORDER":
            order = orders.get(str(tx.get("order_id", "")))
            if order:
                rows.extend(expand_order_rows(base, tx, order))
            else:
                rows.extend(expand_non_order_row(base))
        else:
            rows.extend(expand_non_order_row(base))
    return region, rows, statements


def write_csv(path: Path, rows: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def summarize(rows: list) -> dict:
    total_settlement = sum(r["Total settlement amount"] for r in rows)
    order_settlement = sum(
        r["Total settlement amount"] for r in rows if r["Type "] == "Order"
    )
    gmv = sum(
        r["Total settlement amount"] for r in rows
        if "GMV Payment" in r["Type "]
    )
    logistics = sum(
        r["Total settlement amount"] for r in rows
        if r["Type "] == "Logistics reimbursement"
    )
    subtotal = sum(r["Subtotal after seller discounts"] for r in rows if r["Type "] == "Order")
    return {
        "rows": len(rows),
        "total_settlement": round(total_settlement, 2),
        "order_settlement": round(order_settlement, 2),
        "gmv_ads": round(gmv, 2),
        "logistics": round(logistics, 2),
        "subtotal": round(subtotal, 2),
    }


def build_summary_html(day: datetime.date, all_stats: list, csv_files: list):
    date_label = day.strftime("%Y-%m-%d")
    ymd = yymmdd(day)
    rows_json = json.dumps(all_stats, ensure_ascii=False)
    files_html = "".join(f"<li><code>{p}</code></li>" for p in csv_files)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>结算汇总 {date_label}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 16px; background: #f5f5f5; }}
    h1 {{ font-size: 22px; }}
    .hint {{ color: #666; font-size: 13px; }}
    table {{ border-collapse: collapse; width: 100%; max-width: 900px; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #eee; font-size: 13px; }}
    th {{ background: #fafafa; }}
    td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    ul {{ background: #fff; padding: 12px 28px; border-radius: 8px; max-width: 900px; }}
  </style>
</head>
<body>
  <h1>TikTok Shop 结算汇总 · {date_label}</h1>
  <p class="hint">数据来源：Finance API（statements + statement_transactions）。CSV 已写入 CURSOR/Income_Data/，可对接原有利润脚本。</p>
  <table>
    <thead>
      <tr>
        <th>站点</th><th>币种</th><th class="num">明细行</th><th class="num">总结算</th>
        <th class="num">订单结算</th><th class="num">GMV 广告扣款</th><th class="num">物流赔付</th><th class="num">卖家折扣后小计</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
  <h2 style="font-size:16px;margin-top:24px;">导出文件</h2>
  <ul>{files_html or "<li>无</li>"}</ul>
  <p class="hint">刷新命令：<code>python3 tiktok_settlement.py {date_label}</code></p>
  <script>
    var data = {rows_json};
    document.getElementById("tbody").innerHTML = data.map(function (r) {{
      return "<tr><td>" + r.region + "</td><td>" + r.currency + "</td><td class='num'>" + r.rows +
        "</td><td class='num'>" + r.total_settlement + "</td><td class='num'>" + r.order_settlement +
        "</td><td class='num'>" + r.gmv_ads + "</td><td class='num'>" + r.logistics +
        "</td><td class='num'>" + r.subtotal + "</td></tr>";
    }}).join("");
  </script>
</body>
</html>"""


def run_profit_pages():
    script = Path("CURSOR/build_order_profit_page.py")
    if not script.is_file():
        return
    print("\n[3] 生成 CURSOR 订单利润页...")
    subprocess.run([sys.executable, script.name], cwd=str(script.parent), check=False)


def pull_day(access_token, shops, day: datetime.date, run_profit: bool):
    ge, lt = day_range_utc(day)
    ymd = yymmdd(day)
    print(f"\n── {day.isoformat()} (UTC) ge={ge} lt={lt} ──")

    all_stats = []
    csv_files = []
    raw_dump = {}

    for shop in shops:
        name = shop.get("name", shop.get("shop_name", "?"))
        print(f"  {name} [{shop.get('region')}]...", end=" ", flush=True)
        try:
            region, rows, statements = collect_shop_rows(access_token, shop, ge, lt)
        except Exception as e:
            print(f"❌ {e}")
            continue

        raw_dump[region] = {"statements": statements, "row_count": len(rows)}
        if not rows:
            print("无结算明细")
            continue

        stats = summarize(rows)
        stats["region"] = region
        stats["currency"] = rows[0]["Currency"] if rows else ""
        all_stats.append(stats)

        out = INCOME_DIR / f"income_{region}_{ymd}_{ymd}.csv"
        write_csv(out, rows)
        csv_files.append(str(out))
        print(f"✅ {len(rows)} 行 → {out.name}  结算 {stats['total_settlement']} {stats['currency']}")

    raw_path = Path(f"settlement_raw_{ymd}.json")
    raw_path.write_text(json.dumps(raw_dump, ensure_ascii=False, indent=2), encoding="utf-8")

    if all_stats:
        OUTPUT_HTML.write_text(build_summary_html(day, all_stats, csv_files), encoding="utf-8")
        print(f"\n✅ 汇总页: {OUTPUT_HTML.resolve()}")

    if run_profit and csv_files:
        run_profit_pages()

    return all_stats


def main():
    args = parse_args()
    print("=" * 50)
    print("  TikTok Shop 结算拉取 (Finance API)")
    print("=" * 50)

    tokens = load_token()
    access_token = tokens["access_token"]
    shops = get_shops(access_token)
    if not shops:
        print("❌ 未获取到店铺")
        return 1

    today = datetime.now(timezone.utc).date()
    if args.days:
        days = [today - timedelta(days=i) for i in range(1, args.days + 1)]
        days.reverse()
    elif args.date:
        days = [datetime.strptime(args.date, "%Y-%m-%d").date()]
    else:
        days = [today - timedelta(days=1)]

    print(f"卖家: {tokens.get('seller_name', '?')} · 店铺 {len(shops)} 个 · 目标 {len(days)} 天")

    for day in days:
        pull_day(access_token, shops, day, run_profit=not args.no_profit and len(days) == 1)

    print("\n完成。可将 CURSOR/Income_Data/income_* 对接到 build_order_profit_page.py / build_total_profit_page.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
