"""Shopee 订单拉取 + 月度利润计算（四国主店）。"""

from __future__ import annotations

import html
import json
from calendar import monthrange
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from core.config import ROOT
from modules.catalog.sku_key import shopee_match_key
from modules.finance.profit_engine import ProfitLine, calc_line
from modules.shopee.auth import ensure_shop_token
from modules.shopee import client as shopee_client
from modules.shopee.shops import list_sync_shops

REGION_CURRENCY = {"MY": "MYR", "VN": "VND", "TH": "THB", "PH": "PHP"}
PRIMARY_SHOPS = {
    "MY": 1561117812,
    "PH": 1527371343,
    "TH": 1561124013,
    "VN": 1723948773,
}
DETAIL_BATCH = 50
LIST_PAGE_SIZE = 100
MAX_RANGE_SEC = 15 * 24 * 3600 - 1
DETAIL_OPTIONAL = (
    "item_list,total_amount,pay_time,buyer_user_id,actual_shipping_fee,"
    "estimated_shipping_fee,payment_method,order_status"
)
SKIP_STATUSES = frozenset({"UNPAID", "CANCELLED", "IN_CANCEL"})


@dataclass
class OrderProfitRow:
    order_sn: str
    region: str
    currency: str
    order_status: str
    sku: str
    product_name: str
    match_key: str
    sale_price_local: float
    product_cost_cny: float
    ad_cost_local: float
    commission_local: float
    transaction_fee_local: float
    settlement_local: float
    profit_cny: float
    margin_pct: float | None
    create_time: int = 0
    shop_id: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.margin_pct is not None:
            d["margin_pct"] = round(self.margin_pct, 2)
        for k in (
            "sale_price_local",
            "product_cost_cny",
            "ad_cost_local",
            "commission_local",
            "transaction_fee_local",
            "settlement_local",
            "profit_cny",
        ):
            d[k] = round(float(d[k] or 0), 4)
        return d


@dataclass
class ProfitReport:
    month: str
    rows: list[OrderProfitRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def order_count(self) -> int:
        return len(self.rows)

    @property
    def gmv_cny(self) -> float:
        total = 0.0
        for r in self.rows:
            rate = _rate_for_region(r.region)
            total += float(r.sale_price_local or 0) * rate
        return total

    @property
    def profit_cny(self) -> float:
        return sum(float(r.profit_cny or 0) for r in self.rows)

    @property
    def margin_pct(self) -> float | None:
        gmv = self.gmv_cny
        if not gmv:
            return None
        return self.profit_cny / gmv * 100


def _rate_for_region(region: str) -> float:
    from modules.finance.profit_engine import exchange_rate_for

    return exchange_rate_for(REGION_CURRENCY.get(region, ""))


def parse_month(month: str) -> tuple[int, int]:
    """Return [time_from, time_to) unix seconds for a YYYY-MM calendar month (UTC)."""
    parts = (month or "").strip().split("-")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValueError(f"月份格式应为 YYYY-MM，收到: {month!r}")
    year, mon = int(parts[0]), int(parts[1])
    if mon < 1 or mon > 12:
        raise ValueError(f"非法月份: {month!r}")
    start = datetime(year, mon, 1, tzinfo=timezone.utc)
    last_day = monthrange(year, mon)[1]
    end = datetime(year, mon, last_day, 23, 59, 59, tzinfo=timezone.utc)
    return int(start.timestamp()), int(end.timestamp())


def iter_time_windows(time_from: int, time_to: int, max_span: int = MAX_RANGE_SEC) -> list[tuple[int, int]]:
    """Split a range into ≤15-day Shopee API windows (inclusive ends)."""
    if time_to < time_from:
        return []
    windows: list[tuple[int, int]] = []
    cur = time_from
    while cur <= time_to:
        end = min(cur + max_span, time_to)
        windows.append((cur, end))
        cur = end + 1
    return windows


def resolve_primary_shops() -> list[dict]:
    """Prefer token sync map; fall back to hardcoded primary shop_ids."""
    shops = list_sync_shops()
    by_region = {s["region"]: s for s in shops if s.get("region") in PRIMARY_SHOPS}
    out: list[dict] = []
    for region, shop_id in PRIMARY_SHOPS.items():
        if region in by_region:
            out.append(by_region[region])
        else:
            out.append({"region": region, "shop_id": shop_id, "shop_name": ""})
    return out


def _raise_api_error(resp: dict, ctx: str) -> None:
    err = (resp.get("error") or "").strip()
    if err and err not in {"-", "0"}:
        msg = resp.get("message") or err
        raise RuntimeError(f"Shopee {ctx} 失败: {msg}")


def fetch_order_list(
    shop_id: int,
    access_token: str,
    *,
    time_from: int,
    time_to: int,
    time_range_field: str = "create_time",
) -> list[str]:
    """Paginate get_order_list for one time window; return order_sn list."""
    sns: list[str] = []
    cursor = ""
    while True:
        params = {
            "time_range_field": time_range_field,
            "time_from": int(time_from),
            "time_to": int(time_to),
            "page_size": LIST_PAGE_SIZE,
            "cursor": cursor,
            "response_optional_fields": "order_status",
        }
        resp = shopee_client.get_order_list(shop_id, access_token, params)
        _raise_api_error(resp, "get_order_list")
        body = resp.get("response") or {}
        for row in body.get("order_list") or []:
            sn = (row.get("order_sn") or "").strip()
            if sn:
                sns.append(sn)
        if not body.get("more"):
            break
        cursor = body.get("next_cursor") or ""
        if not cursor:
            break
    return sns


def fetch_order_details(
    shop_id: int,
    access_token: str,
    order_sns: Iterable[str],
) -> list[dict]:
    """Batch get_order_detail (max 50 order_sn per call)."""
    sns = [s for s in order_sns if s]
    out: list[dict] = []
    for i in range(0, len(sns), DETAIL_BATCH):
        chunk = sns[i : i + DETAIL_BATCH]
        params = {
            "order_sn_list": ",".join(chunk),
            "response_optional_fields": DETAIL_OPTIONAL,
        }
        resp = shopee_client.get_order_detail(shop_id, access_token, params)
        _raise_api_error(resp, "get_order_detail")
        body = resp.get("response") or {}
        out.extend(body.get("order_list") or [])
    return out


def fetch_escrow_detail(shop_id: int, access_token: str, order_sn: str) -> dict:
    resp = shopee_client.get_escrow_detail(shop_id, access_token, order_sn)
    _raise_api_error(resp, f"get_escrow_detail/{order_sn}")
    return resp.get("response") or {}


def load_match_key_costs() -> dict[str, float]:
    """match_key (后四位) → cost_cny，复用 catalog listings 索引。"""
    from modules.catalog.listings import _cost_index

    key_cost, _ = _cost_index()
    return dict(key_cost)


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _items_from_order(order: dict, escrow: dict) -> list[dict]:
    income = (escrow.get("order_income") or {}) if escrow else {}
    items = list(income.get("items") or [])
    if items:
        return items
    return list(order.get("item_list") or [])


def _sku_and_name(items: list[dict]) -> tuple[str, str, str]:
    skus: list[str] = []
    names: list[str] = []
    keys: list[str] = []
    for it in items:
        sku = (
            it.get("model_sku")
            or it.get("item_sku")
            or it.get("model_sku_name")
            or ""
        )
        sku = str(sku).strip()
        name = str(it.get("item_name") or it.get("model_name") or "").strip()
        if sku:
            skus.append(sku)
            key = shopee_match_key(sku)
            if key:
                keys.append(key)
        if name:
            names.append(name)
    sku_text = ", ".join(dict.fromkeys(skus)) if skus else ""
    name_text = names[0] if names else ""
    match_key = keys[0] if keys else shopee_match_key(sku_text)
    return sku_text, name_text, match_key


def _product_cost_cny(items: list[dict], key_costs: dict[str, float]) -> tuple[float, str]:
    total = 0.0
    first_key = ""
    for it in items:
        sku = str(it.get("model_sku") or it.get("item_sku") or "").strip()
        key = shopee_match_key(sku)
        qty = int(it.get("quantity_purchased") or it.get("model_quantity_purchased") or 1)
        cost = float(key_costs.get(key) or 0)
        if key and not first_key:
            first_key = key
        total += cost * max(qty, 1)
    return total, first_key


def _sale_price_local(order: dict, escrow: dict, items: list[dict]) -> float:
    income = escrow.get("order_income") or {}
    for key in ("buyer_total_amount", "original_price", "order_original_price"):
        if income.get(key) is not None:
            return _f(income.get(key))
    if order.get("total_amount") is not None:
        return _f(order.get("total_amount"))
    total = 0.0
    for it in items:
        qty = int(it.get("quantity_purchased") or it.get("model_quantity_purchased") or 1)
        unit = _f(
            it.get("discounted_price")
            or it.get("selling_price")
            or it.get("sale_price")
            or it.get("model_discounted_price")
            or it.get("model_original_price")
        )
        total += unit * max(qty, 1)
    return total


def build_order_profit_row(
    *,
    order: dict,
    escrow: dict,
    region: str,
    shop_id: int,
    key_costs: dict[str, float],
) -> OrderProfitRow:
    income = escrow.get("order_income") or {}
    items = _items_from_order(order, escrow)
    sku, name, match_key = _sku_and_name(items)
    cost_cny, cost_key = _product_cost_cny(items, key_costs)
    if not match_key:
        match_key = cost_key

    sale = _sale_price_local(order, escrow, items)
    settlement = _f(income.get("escrow_amount"))
    commission = abs(_f(income.get("commission_fee")))
    txn_fee = abs(_f(income.get("transaction_fee") or income.get("seller_transaction_fee")))
    # 不接广告 API：优先用 escrow 的 campaign_fee，否则 0
    ad_cost = abs(_f(income.get("campaign_fee")))

    currency = REGION_CURRENCY.get(region, "")
    line: ProfitLine = calc_line(
        settlement_local=settlement,
        revenue_local=sale,
        subtotal_local=sale,
        product_cost_cny=cost_cny,
        ad_cost_local=ad_cost,
        currency=currency,
        seller_shipping_fee=_f(income.get("actual_shipping_fee")),
        sst=_f(income.get("final_shipping_fee") or income.get("shipping_fee_sst")),
    )
    return OrderProfitRow(
        order_sn=str(order.get("order_sn") or escrow.get("order_sn") or ""),
        region=region,
        currency=currency,
        order_status=str(order.get("order_status") or ""),
        sku=sku,
        product_name=name,
        match_key=match_key,
        sale_price_local=sale,
        product_cost_cny=cost_cny,
        ad_cost_local=ad_cost,
        commission_local=commission,
        transaction_fee_local=txn_fee,
        settlement_local=settlement,
        profit_cny=line.profit_cny,
        margin_pct=line.margin_pct,
        create_time=int(order.get("create_time") or 0),
        shop_id=int(shop_id),
    )


def collect_month_orders(
    month: str,
    *,
    shops: list[dict] | None = None,
    key_costs: dict[str, float] | None = None,
    include_skipped_status: bool = False,
    fetch_escrow: bool = True,
) -> ProfitReport:
    """Pull all primary-shop orders for a calendar month and compute per-order profit."""
    time_from, time_to = parse_month(month)
    windows = iter_time_windows(time_from, time_to)
    shops = shops or resolve_primary_shops()
    key_costs = key_costs if key_costs is not None else load_match_key_costs()
    report = ProfitReport(month=month)

    for shop in shops:
        region = str(shop.get("region") or "").upper()
        shop_id = int(shop.get("shop_id") or 0)
        if not shop_id or region not in PRIMARY_SHOPS:
            report.errors.append(f"跳过无效店铺: {shop}")
            continue
        try:
            token = ensure_shop_token(shop_id)
        except Exception as exc:
            report.errors.append(f"[{region}] token 失败 shop_id={shop_id}: {exc}")
            continue

        print(f"  [{region}] shop_id={shop_id} 拉取订单…", flush=True)
        seen: set[str] = set()
        for w_from, w_to in windows:
            try:
                sns = fetch_order_list(shop_id, token, time_from=w_from, time_to=w_to)
            except Exception as exc:
                report.errors.append(f"[{region}] order_list {w_from}-{w_to}: {exc}")
                continue
            for sn in sns:
                seen.add(sn)

        print(f"  [{region}] 订单号 {len(seen)} 个，拉详情/escrow…", flush=True)
        if not seen:
            continue

        try:
            details = fetch_order_details(shop_id, token, sorted(seen))
        except Exception as exc:
            report.errors.append(f"[{region}] order_detail: {exc}")
            continue

        done = 0
        for order in details:
            status = str(order.get("order_status") or "").upper()
            if not include_skipped_status and status in SKIP_STATUSES:
                continue
            sn = str(order.get("order_sn") or "")
            escrow: dict = {}
            if fetch_escrow:
                try:
                    escrow = fetch_escrow_detail(shop_id, token, sn)
                except Exception as exc:
                    report.errors.append(f"[{region}] escrow {sn}: {exc}")
                    continue
            try:
                row = build_order_profit_row(
                    order=order,
                    escrow=escrow,
                    region=region,
                    shop_id=shop_id,
                    key_costs=key_costs,
                )
                report.rows.append(row)
                done += 1
                if done % 20 == 0:
                    print(f"  [{region}] 已算利润 {done}/{len(details)}", flush=True)
            except Exception as exc:
                report.errors.append(f"[{region}] profit {sn}: {exc}")
        print(f"  [{region}] 完成 {done} 笔", flush=True)

    report.rows.sort(key=lambda r: (r.region, r.create_time, r.order_sn))
    return report


def render_profit_html(report: ProfitReport) -> str:
    rows_html = []
    for r in report.rows:
        d = r.to_dict()
        margin = "" if d["margin_pct"] is None else f"{d['margin_pct']:.2f}%"
        rows_html.append(
            "<tr>"
            f"<td>{html.escape(d['order_sn'])}</td>"
            f"<td>{html.escape(d['sku'])}</td>"
            f"<td>{html.escape(d['product_name'][:80])}</td>"
            f"<td>{html.escape(d['region'])}</td>"
            f"<td>{html.escape(d['order_status'])}</td>"
            f"<td class='num'>{d['sale_price_local']:.2f}</td>"
            f"<td class='num'>{d['product_cost_cny']:.2f}</td>"
            f"<td class='num'>{d['ad_cost_local']:.2f}</td>"
            f"<td class='num'>{d['commission_local']:.2f}</td>"
            f"<td class='num'>{d['transaction_fee_local']:.2f}</td>"
            f"<td class='num'>{d['settlement_local']:.2f}</td>"
            f"<td class='num'>{d['profit_cny']:.2f}</td>"
            f"<td class='num'>{margin}</td>"
            "</tr>"
        )
    margin_all = report.margin_pct
    margin_txt = "—" if margin_all is None else f"{margin_all:.2f}%"
    err_block = ""
    if report.errors:
        err_block = (
            "<h2>Warnings</h2><ul>"
            + "".join(f"<li>{html.escape(e)}</li>" for e in report.errors[:200])
            + "</ul>"
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>Shopee 利润明细 {html.escape(report.month)}</title>
<style>
body {{ font-family: "Segoe UI", sans-serif; margin: 24px; color: #1a1a1a; }}
h1 {{ font-size: 22px; margin-bottom: 8px; }}
.summary {{ margin: 12px 0 20px; line-height: 1.6; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; }}
th {{ background: #f5f5f5; position: sticky; top: 0; }}
td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
tr:nth-child(even) {{ background: #fafafa; }}
</style>
</head>
<body>
<h1>Shopee 订单利润明细 — {html.escape(report.month)}</h1>
<div class="summary">
  <div>总订单数：{report.order_count}</div>
  <div>总 GMV (CNY)：{report.gmv_cny:.2f}</div>
  <div>总利润 (CNY)：{report.profit_cny:.2f}</div>
  <div>总利润率：{margin_txt}</div>
  <div>覆盖站点：{", ".join(PRIMARY_SHOPS.keys())}</div>
</div>
{err_block}
<table>
<thead>
<tr>
  <th>订单号</th><th>SKU</th><th>商品名</th><th>站点</th><th>状态</th>
  <th>售价(本地)</th><th>产品成本(CNY)</th><th>广告费</th>
  <th>佣金</th><th>交易费</th><th>结算金额</th><th>利润CNY</th><th>利润率%</th>
</tr>
</thead>
<tbody>
{"".join(rows_html) if rows_html else "<tr><td colspan='13'>无订单</td></tr>"}
</tbody>
</table>
</body>
</html>
"""


def write_profit_report(report: ProfitReport, out_path: Path | None = None) -> Path:
    out = out_path or (ROOT / "outputs" / f"shopee_profit_{report.month}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_profit_html(report), encoding="utf-8")
    json_path = out.with_suffix(".json")
    json_path.write_text(
        json.dumps(
            {
                "month": report.month,
                "order_count": report.order_count,
                "gmv_cny": round(report.gmv_cny, 2),
                "profit_cny": round(report.profit_cny, 2),
                "margin_pct": None if report.margin_pct is None else round(report.margin_pct, 2),
                "errors": report.errors,
                "rows": [r.to_dict() for r in report.rows],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


def print_summary(report: ProfitReport) -> None:
    margin = "—" if report.margin_pct is None else f"{report.margin_pct:.2f}%"
    print(f"[shopee profit] month={report.month}")
    print(f"  总订单数: {report.order_count}")
    print(f"  总GMV(CNY): {report.gmv_cny:.2f}")
    print(f"  总利润(CNY): {report.profit_cny:.2f}")
    print(f"  总利润率: {margin}")
    by_region: dict[str, int] = {}
    for r in report.rows:
        by_region[r.region] = by_region.get(r.region, 0) + 1
    for region in PRIMARY_SHOPS:
        print(f"  {region}: {by_region.get(region, 0)} 单")
    if report.errors:
        print(f"  warnings: {len(report.errors)}")


def run_month_profit(month: str, *, out_path: Path | None = None) -> Path:
    report = collect_month_orders(month)
    path = write_profit_report(report, out_path)
    print_summary(report)
    print(f"  HTML: {path}")
    return path
