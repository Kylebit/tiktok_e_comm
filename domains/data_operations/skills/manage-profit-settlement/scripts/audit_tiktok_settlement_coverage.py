"""Compare TikTok orders created in a period with official Finance settlement evidence."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from html import escape
import json
from pathlib import Path
import sys
import time


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "domains" / "data_operations" / "profit_settlement").is_dir():
            return parent
    raise RuntimeError("profit settlement repository root not found")


REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from domains.data_operations.profit_settlement.tiktok_coverage import build_coverage
import pull_settlement_evidence as settlement_pull


BANGKOK = timezone(timedelta(hours=7), name="Asia/Bangkok")


def _fetch_created_orders(token: str, cipher: str, start: date, end: date) -> tuple[list[dict], int]:
    from core.api_client import post

    orders = {}
    request_count = 0
    segment_start = start
    while segment_start <= end:
        segment_end = min(segment_start + timedelta(days=13), end)
        local_start = datetime.combine(segment_start, datetime.min.time(), tzinfo=BANGKOK)
        local_end = datetime.combine(segment_end + timedelta(days=1), datetime.min.time(), tzinfo=BANGKOK)
        page_token = ""
        while True:
            query = {"shop_cipher": cipher, "page_size": "100"}
            if page_token:
                query["page_token"] = page_token
            response = post(
                "/order/202309/orders/search",
                token,
                query,
                {
                    "create_time_ge": int(local_start.astimezone(timezone.utc).timestamp()),
                    "create_time_lt": int(local_end.astimezone(timezone.utc).timestamp()),
                },
                debug=False,
            )
            request_count += 1
            if response.get("code") != 0:
                raise RuntimeError(response.get("message") or str(response.get("code")))
            data = response.get("data") or {}
            for row in data.get("orders") or []:
                order_id = str(row.get("id") or "")
                created = row.get("create_time")
                if not order_id or created in (None, ""):
                    continue
                created_at = datetime.fromtimestamp(int(created), tz=timezone.utc).astimezone(BANGKOK)
                if not start <= created_at.date() <= end:
                    continue
                orders[order_id] = {
                    "order_id": order_id,
                    "order_created_at": created_at.isoformat(),
                    "order_status": str(row.get("status") or row.get("order_status") or "UNKNOWN"),
                }
            page_token = str(data.get("next_page_token") or "")
            if not page_token:
                break
            time.sleep(0.15)
        segment_start = segment_end + timedelta(days=1)
    return [orders[key] for key in sorted(orders)], request_count


def _html(payload: dict) -> str:
    def rows(values):
        return "".join(
            "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                escape(str(row.get("order_id") or "")),
                escape(str(row.get("order_created_at") or "")),
                escape(str(row.get("order_status") or "")),
            )
            for row in values
        ) or "<tr><td colspan='3'>无</td></tr>"

    counts = payload["counts"]
    return f"""<!doctype html><meta charset='utf-8'><title>TikTok TH 7月订单结算覆盖审计</title>
<style>body{{font-family:Arial,sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%;margin-bottom:24px}}th,td{{border:1px solid #ddd;padding:8px;text-align:left}}th{{background:#f3f5f7}}</style>
<h1>TikTok TH 订单结算覆盖审计</h1>
<p>下单区间：{payload['created_period']['start']} 至 {payload['created_period']['end']}（Asia/Bangkok）；结算观察截止：{payload['settlement_observed_through']}</p>
<ul><li>下单订单：{counts['created_orders']}</li><li>已找到 Finance 结算：{counts['settled_orders']}</li><li>已取消且无结算：{counts['cancelled_without_settlement']}</li><li>未取消但未找到结算：{counts['unsettled_non_cancelled']}</li></ul>
<h2>未取消但未找到结算</h2><table><tr><th>订单 ID</th><th>下单时间</th><th>官方状态</th></tr>{rows(payload['unsettled_non_cancelled_orders'])}</table>
<h2>已取消且无结算</h2><table><tr><th>订单 ID</th><th>下单时间</th><th>官方状态</th></tr>{rows(payload['cancelled_without_settlement_orders'])}</table>"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--settlement-evidence", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--as-of", required=True, type=date.fromisoformat)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-credential-refresh", action="store_true")
    args = parser.parse_args(argv)
    evidence = json.loads(args.settlement_evidence.read_text(encoding="utf-8"))
    if evidence.get("status") != "ready":
        raise RuntimeError("settlement evidence is not ready")
    settled_ids = {
        str(row.get("order_id") or "")
        for row in evidence.get("orders") or []
        if row.get("transaction_type") == "Order" and row.get("order_id")
    }
    settings = settlement_pull._settings(args.project_root)
    with settlement_pull._tiktok_credential_session(
        args.project_root,
        settings,
        allow_refresh=args.allow_credential_refresh,
    ) as (token_data, credential_source, credential_refresh_performed):
        token = str(token_data.get("access_token") or "")
        from core.shops import list_shops

        shop = next(
            (row for row in list_shops(token) if str(row.get("region") or "").upper() == "TH"),
            None,
        )
        if shop is None:
            raise RuntimeError("no authorized TikTok TH shop")
        cipher = str(shop.get("cipher") or shop.get("shop_cipher") or "")
        orders, request_count = _fetch_created_orders(token, cipher, args.start, args.end)
    payload = build_coverage(
        orders=orders,
        settled_order_ids=settled_ids,
        start=args.start,
        end=args.end,
        as_of=args.as_of,
        settlement_snapshot_id=str(evidence.get("snapshot_id") or evidence.get("checksum") or ""),
    )
    payload["receipt"]["external_reads_performed"] = [
        "order/202309/orders/search",
        "local settlement-evidence/v1 artifact",
    ]
    payload["receipt"]["credential_source"] = credential_source
    payload["receipt"]["credential_refresh_performed"] = credential_refresh_performed
    payload["receipt"]["order_search_request_count"] = request_count
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    args.output.with_suffix(".html").write_text(_html(payload), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "counts": payload["counts"], "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
