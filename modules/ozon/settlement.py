"""Ozon 结算汇总：拉取 /v3/finance/transaction/list，按订单和费用类型汇总。

Ozon 接口单次查询时间跨度不能超过一个月，所以按月分页拉取后再在本地合并。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from modules.ozon.client import ozon_post


def _month_ranges(start: datetime, end: datetime):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        range_start = datetime(y, m, 1)
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        range_end = min(datetime(ny, nm, 1) - timedelta(seconds=1), end)
        yield max(range_start, start), range_end
        y, m = ny, nm


def fetch_transactions(date_from: datetime, date_to: datetime) -> list[dict[str, Any]]:
    all_ops: list[dict[str, Any]] = []
    for start, end in _month_ranges(date_from, date_to):
        page = 1
        while True:
            body = {
                "filter": {
                    "date": {
                        "from": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                        "to": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    },
                    "transaction_type": "all",
                },
                "page": page,
                "page_size": 1000,
            }
            res = ozon_post("/v3/finance/transaction/list", body)
            result = res.get("result") or {}
            ops = result.get("operations") or []
            all_ops.extend(ops)
            page_count = result.get("page_count", 1)
            if not ops or page >= page_count:
                break
            page += 1
    return all_ops


def summarize_transactions(ops: list[dict[str, Any]]) -> dict[str, Any]:
    by_posting: dict[str, dict[str, Any]] = defaultdict(lambda: {"ops": [], "items": set()})
    fee_type_totals: dict[str, float] = defaultdict(float)
    fee_type_count: dict[str, int] = defaultdict(int)

    for op in ops:
        posting = (op.get("posting") or {}).get("posting_number") or "(无订单关联/服务费)"
        by_posting[posting]["ops"].append(op)
        for item in op.get("items") or []:
            name = item.get("name")
            if name:
                by_posting[posting]["items"].add(name)
        type_name = op.get("operation_type_name") or op.get("operation_type") or "未知"
        amount = float(op.get("amount") or 0)
        fee_type_totals[type_name] += amount
        fee_type_count[type_name] += 1

    orders = []
    for posting, data in by_posting.items():
        total = sum(float(o.get("amount") or 0) for o in data["ops"])
        delivered = any(
            (o.get("operation_type_name") or "").startswith("Доставка") for o in data["ops"]
        )
        orders.append(
            {
                "posting_number": posting,
                "products": sorted(data["items"]),
                "settled": delivered,
                "net_amount": round(total, 2),
                "operations": [
                    {
                        "date": o.get("operation_date"),
                        "type_name": o.get("operation_type_name"),
                        "amount": o.get("amount"),
                    }
                    for o in data["ops"]
                ],
            }
        )
    orders.sort(key=lambda o: o["operations"][0]["date"] if o["operations"] else "")

    fee_breakdown = [
        {"type_name": name, "count": fee_type_count[name], "total": round(total, 2)}
        for name, total in sorted(fee_type_totals.items(), key=lambda x: x[1])
    ]

    settled_orders = [o for o in orders if o["settled"]]
    pending_orders = [o for o in orders if not o["settled"]]

    return {
        "orders": orders,
        "settled_count": len(settled_orders),
        "pending_count": len(pending_orders),
        "fee_breakdown": fee_breakdown,
        "grand_total": round(sum(fee_type_totals.values()), 2),
        "settled_net_total": round(sum(o["net_amount"] for o in settled_orders), 2),
    }


def build_settlement_summary(months_back: int = 3) -> dict[str, Any]:
    now = datetime.utcnow()
    date_from = (now.replace(day=1) - timedelta(days=1)).replace(day=1)
    for _ in range(months_back - 1):
        date_from = (date_from - timedelta(days=1)).replace(day=1)
    ops = fetch_transactions(date_from, now)
    summary = summarize_transactions(ops)
    summary["date_from"] = date_from.strftime("%Y-%m-%d")
    summary["date_to"] = now.strftime("%Y-%m-%d")
    return summary
