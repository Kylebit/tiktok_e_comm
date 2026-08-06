"""Offline CLI for platform-isolated profit reports and approved knowledge."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from domains.data_operations.profit_settlement.knowledge_base import ProfitKnowledgeBase
from domains.data_operations.profit_settlement.shared_inputs import CostSnapshot, FxSnapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or approve detailed settled-order profit reports")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--platform", required=True, choices=("tiktok", "shopee", "ozon"))
    build.add_argument("--period-kind", required=True, choices=("weekly", "monthly"))
    build.add_argument("--start", required=True)
    build.add_argument("--end", required=True)
    build.add_argument("--input", required=True, type=Path)
    build.add_argument(
        "--ad-rate",
        default="0.22",
        help="weekly TikTok/Shopee advertising fraction (default: 0.22)",
    )
    build.add_argument("--output", type=Path)

    approve = commands.add_parser("approve-monthly")
    approve.add_argument("--report", required=True, type=Path)
    approve.add_argument("--knowledge-root", required=True, type=Path)
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--approved-at", required=True)
    approve.add_argument("--note", default="")

    audit = commands.add_parser("audit")
    audit.add_argument("--report", required=True, type=Path)

    render = commands.add_parser("render")
    render.add_argument("--report", required=True, type=Path)
    render.add_argument("--output", required=True, type=Path)

    listing = commands.add_parser("list-approved")
    listing.add_argument("--knowledge-root", required=True, type=Path)
    listing.add_argument("--platform", choices=("tiktok", "shopee", "ozon"))
    listing.add_argument("--year", type=int)
    listing.add_argument("--month", type=int)

    args = parser.parse_args(argv)
    if args.command == "build":
        payload = _build(args)
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        return 0
    if args.command == "approve-monthly":
        report = _read_json(args.report)
        result = ProfitKnowledgeBase(args.knowledge_root).approve_monthly_report(
            report,
            approved_by=args.approved_by,
            approved_at=args.approved_at,
            approval_note=args.note,
        )
        print(json.dumps({"knowledge_id": result.knowledge_id, "path": str(result.path), "created": result.created}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "audit":
        from domains.data_operations.profit_settlement.audit import audit_profit_report
        result = audit_profit_report(_read_json(args.report))
        print(json.dumps(result.payload(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.status == "PASSED" else 2
    if args.command == "render":
        from domains.data_operations.profit_settlement.render import render_profit_report_html
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render_profit_report_html(_read_json(args.report)), encoding="utf-8")
        print(json.dumps({"output": str(args.output)}, ensure_ascii=False))
        return 0
    rows = ProfitKnowledgeBase(args.knowledge_root).list_reports(platform=args.platform, year=args.year, month=args.month)
    print(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _build(args: argparse.Namespace) -> dict[str, Any]:
    source = _read_json(args.input)
    cost_data = source.get("costs") or {}
    fx_data = source.get("fx") or {}
    costs = CostSnapshot.from_mapping(
        cost_data.get("records") or {},
        snapshot_id=cost_data.get("snapshot_id"),
        default_version=str(cost_data.get("version") or "unspecified"),
        default_effective_at=str(cost_data.get("effective_at") or ""),
        source=str(cost_data.get("source") or "caller_supplied"),
    )
    fx = FxSnapshot.from_mapping(
        fx_data.get("rates_cny") or {},
        source=str(fx_data.get("source") or ""),
        as_of=str(fx_data.get("as_of") or ""),
        snapshot_id=fx_data.get("snapshot_id"),
    )
    common = {
        "period_start": args.start,
        "period_end": args.end,
        "costs": costs,
        "fx": fx,
        "generated_at": _datetime(source.get("generated_at")),
        "code_version": str(source.get("code_version") or "unknown"),
    }
    rows = source.get("rows") or []
    if args.platform == "tiktok":
        from domains.data_operations.profit_settlement import tiktok
        report = tiktok.build_weekly_report(rows, ad_rate=_required_ad_rate(args), **common) if args.period_kind == "weekly" else tiktok.build_monthly_report(rows, actual_advertising=source.get("actual_advertising"), **common)
    elif args.platform == "shopee":
        from domains.data_operations.profit_settlement import shopee
        report = shopee.build_weekly_report(rows, ad_rate=_required_ad_rate(args), **common) if args.period_kind == "weekly" else shopee.build_monthly_report(rows, actual_advertising=source.get("actual_advertising"), **common)
    else:
        from domains.data_operations.profit_settlement import ozon
        report = ozon.build_weekly_report(rows, **common) if args.period_kind == "weekly" else ozon.build_monthly_report(rows, **common)
    return report.payload()


def _required_ad_rate(args: argparse.Namespace) -> str:
    return args.ad_rate or "0.22"


def _datetime(value: object) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected a JSON object: {path}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
