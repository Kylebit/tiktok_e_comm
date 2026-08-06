"""Build detailed weekly reports from already-reviewed settlement evidence."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "domains" / "data_operations" / "profit_settlement").is_dir():
            return parent
    raise RuntimeError("profit settlement repository root not found")


ROOT = _repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
from domains.data_operations.profit_settlement.render import render_profit_report_html
from domains.data_operations.profit_settlement.shared_inputs import CostSnapshot, FxSnapshot
from domains.data_operations.profit_settlement.weekly_evidence_bundle import build_weekly_evidence_bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--ad-rate", default="0.22")
    parser.add_argument("--ozon-sku-map", type=Path)
    parser.add_argument("--allow-ozon-read-enrichment", action="store_true")
    args = parser.parse_args()

    evidence = _load_evidence(args.evidence_dir, args.start, args.end)
    catalog = load_local_catalog(args.project_root / "data" / "shop.db")
    costs = CostSnapshot.from_mapping(
        catalog.costs_by_sku,
        snapshot_id=catalog.snapshot_id,
        default_version=catalog.snapshot_id,
        default_effective_at=catalog.effective_at,
        source="shop.db:sku_costs:sqlite-mode-ro",
    )
    live_fx = _live_fx()
    fx = FxSnapshot.from_mapping(live_fx["rates"], source=live_fx["provider"], as_of=live_fx["as_of"])
    ozon_map = _load_mapping(args.ozon_sku_map)
    ozon_quantities = {}
    ozon_enrichment = {"status": "not_requested", "external_reads_performed": [], "external_writes_performed": []}
    if args.allow_ozon_read_enrichment:
        live_map, ozon_quantities, ozon_enrichment = _read_ozon_enrichment(
            args.project_root, evidence["ozon"]
        )
        ozon_map = {**live_map, **ozon_map}
    bundle = build_weekly_evidence_bundle(
        evidence,
        catalog,
        period_start=args.start,
        period_end=args.end,
        costs=costs,
        fx=fx,
        ad_rate=Decimal(args.ad_rate),
        seller_sku_by_ozon_sku=ozon_map,
        quantity_by_ozon_order_sku=ozon_quantities,
        generated_at=datetime.now(timezone.utc),
        code_version="profit-settlement-v1-stage2",
    )
    catalog_issues = [_catalog_issue(item) for item in catalog.issues]
    bundle["catalog_quality_issues"] = catalog_issues
    bundle["external_reads"] = [
        "settlement-evidence/v1 JSON artifacts",
        "shop.db via SQLite mode=ro",
        live_fx["provider"],
        *ozon_enrichment["external_reads_performed"],
    ]
    bundle["ozon_enrichment"] = ozon_enrichment
    bundle["external_writes_performed"] = []
    if catalog_issues:
        bundle["status"] = "needs_review"

    args.output.mkdir(parents=True, exist_ok=True)
    for platform, item in bundle["reports"].items():
        report = item.get("report")
        if isinstance(report, dict):
            (args.output / f"{platform}_{args.start}_{args.end}.html").write_text(
                render_profit_report_html(report), encoding="utf-8"
            )
            (args.output / f"{platform}_{args.start}_{args.end}.json").write_text(
                json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
            )
    bundle_path = args.output / f"weekly_profit_{args.start}_{args.end}.json"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": bundle["status"],
        "bundle": str(bundle_path),
        "reports": {platform: item["status"] for platform, item in bundle["reports"].items()},
        "quality_issue_counts": dict(sorted(Counter(item["code"] for item in bundle["quality_issues"]).items())),
        "catalog_quality_issue_counts": dict(sorted(Counter(item["code"] for item in catalog_issues).items())),
        "external_writes_performed": [],
    }, ensure_ascii=False, indent=2))
    return 0 if bundle["status"] == "ready" else 2


def _load_evidence(directory: Path, start: date, end: date) -> dict[str, dict]:
    output = {}
    for platform, site in (("tiktok", "TH"), ("shopee", "TH"), ("ozon", "RU")):
        path = directory / f"{platform}_{site}_{start}_{end}.settlement.json"
        output[platform] = json.loads(path.read_text(encoding="utf-8"))
    return output


def _load_mapping(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in payload.items()):
        raise ValueError("Ozon SKU mapping must be a JSON object of platform SKU to seller SKU")
    return payload


def _live_fx() -> dict:
    from modules.sourcing.fx_rates import _fetch_fawaz_jsdelivr, _fetch_open_er_api

    errors = []
    for fetcher in (_fetch_open_er_api_with_curl, _fetch_open_er_api, _fetch_fawaz_jsdelivr):
        try:
            return fetcher()
        except Exception as exc:  # fail closed instead of using configured defaults
            errors.append(f"{fetcher.__name__}: {type(exc).__name__}: {exc}")
    raise RuntimeError("all live FX reads failed; " + "; ".join(errors))


def _fetch_open_er_api_with_curl() -> dict:
    """Use the host TLS stack when Python's SSL handshake is unavailable."""
    completed = subprocess.run(
        ["curl.exe", "-L", "--fail", "--silent", "--show-error", "--max-time", "30", "https://open.er-api.com/v6/latest/CNY"],
        check=True,
        capture_output=True,
        text=True,
        timeout=35,
    )
    payload = json.loads(completed.stdout)
    if payload.get("result") != "success" or not isinstance(payload.get("rates"), dict):
        raise ValueError("open.er-api returned an invalid result")
    rates = {}
    for currency in ("PHP", "MYR", "THB", "VND", "USD", "RUB"):
        foreign_per_cny = Decimal(str(payload["rates"].get(currency) or "0"))
        if foreign_per_cny > 0:
            rates[currency] = Decimal("1") / foreign_per_cny
    if len(rates) < 3:
        raise ValueError("open.er-api missing required currencies")
    return {
        "provider": "open.er-api.com via curl (ExchangeRate-API free, no key)",
        "as_of": payload.get("time_last_update_utc") or str(payload.get("time_last_update_unix") or ""),
        "rates": rates,
    }


def _read_ozon_enrichment(project_root: Path, evidence: dict) -> tuple[dict[str, str], dict[str, object], dict]:
    """Read seller SKU and fulfilled quantity; retain no credentials or raw response."""
    from modules.ozon import client

    client_id, api_key, credential_source = _ozon_credentials(project_root)
    if not client_id or not api_key:
        raise RuntimeError("Ozon read enrichment credentials are missing")
    client.ozon_credentials = lambda: (client_id, api_key)
    orders = evidence.get("orders") if isinstance(evidence.get("orders"), list) else []
    skus = sorted({str(item.get("platform_sku") or "") for order in orders for item in (order.get("items") or []) if str(item.get("platform_sku") or "")})
    product_response = client.ozon_post("/v3/product/info/list", {"sku": skus})
    product_items = product_response.get("items") or (product_response.get("result") or {}).get("items") or []
    sku_map = {str(item.get("sku") or ""): str(item.get("offer_id") or "") for item in product_items if str(item.get("sku") or "") and str(item.get("offer_id") or "")}
    quantities: dict[str, object] = {}
    failures = []
    for order in orders:
        posting = str(order.get("order_id") or "")
        if not posting:
            continue
        try:
            response = client.ozon_post(
                "/v3/posting/fbs/get",
                {"posting_number": posting, "with": {"analytics_data": False, "barcodes": False, "financial_data": False, "translit": False}},
            )
            result = response.get("result") or response
            for product in result.get("products") or []:
                sku = str(product.get("sku") or "")
                if sku:
                    quantities[f"{posting}|{sku}"] = product.get("quantity")
        except Exception as exc:  # keep other postings auditable; never infer quantity
            failures.append({"order_id": posting, "error_type": type(exc).__name__})
    return sku_map, quantities, {
        "schema_version": "ozon-profit-read-enrichment/v1",
        "status": "ready" if len(sku_map) == len(skus) and not failures else "needs_review",
        "credential_source": credential_source,
        "requested_sku_count": len(skus),
        "mapped_sku_count": len(sku_map),
        "requested_posting_count": len(orders),
        "quantity_key_count": len(quantities),
        "failures": failures,
        "external_reads_performed": ["Ozon /v3/product/info/list", "Ozon /v3/posting/fbs/get"],
        "external_writes_performed": [],
        "raw_response_retained": False,
    }


def _ozon_credentials(root: Path) -> tuple[str, str, str]:
    candidates = (
        (root / "config" / "ozon.local.json", "config/ozon.local.json"),
        (root / "modules" / "ozon" / "legacy_webapp" / "data" / "credentials.local.json", "legacy_webapp/data/credentials.local.json"),
    )
    for path, label in candidates:
        if not path.is_file() or path.stat().st_size == 0:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        client_id = str(payload.get("client_id") or "").strip()
        api_key = str(payload.get("api_key") or "").strip()
        if client_id and api_key:
            return client_id, api_key, label
    return "", "", "missing"


def _catalog_issue(issue: object) -> dict[str, str]:
    return {
        "code": str(getattr(issue, "code", "catalog_quality_issue")),
        "record_id": str(getattr(issue, "record_id", "report")),
        "field": str(getattr(issue, "field", "catalog")),
        "message": str(getattr(issue, "message", "Catalog requires review")),
    }


if __name__ == "__main__":
    raise SystemExit(main())
