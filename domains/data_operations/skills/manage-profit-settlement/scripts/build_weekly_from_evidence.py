"""Build detailed weekly reports from already-reviewed settlement evidence."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
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
from domains.data_operations.profit_settlement.cost_policy import resolve_temporary_cost_policy
from domains.data_operations.profit_settlement.render import render_profit_report_html
from domains.data_operations.profit_settlement.settlement_evidence_adapter import adapt_settlement_evidence
from domains.data_operations.profit_settlement.shared_inputs import CostSnapshot, FxSnapshot
from domains.data_operations.profit_settlement.weekly_evidence_bundle import build_weekly_evidence_bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--platform", choices=("tiktok", "shopee", "ozon"))
    parser.add_argument("--site", help="site paired with --platform, for example MY")
    parser.add_argument("--policy-config", type=Path, default=Path(__file__).resolve().parents[1] / "report-policy.json", help="single JSON policy for advertising rates and platform local fulfillment fees")
    parser.add_argument("--ad-rate", help="global estimated advertising fraction; default 0.22")
    parser.add_argument("--tiktok-ad-rate", help="TikTok-only override, for example 0.18")
    parser.add_argument("--shopee-ad-rate", help="Shopee-only override, for example 0.20")
    parser.add_argument("--ozon-ad-rate", help="Ozon-only override, for example 0.25")
    parser.add_argument("--tiktok-local-fulfillment-fee-cny", help="TikTok local fulfillment cost per parent order in CNY; overrides policy config")
    parser.add_argument("--shopee-local-fulfillment-fee-cny", help="Shopee combined local shipping and warehouse cost per parent order in CNY; overrides policy config")
    parser.add_argument("--ozon-sku-map", type=Path)
    parser.add_argument("--allow-ozon-read-enrichment", action="store_true")
    args = parser.parse_args()
    if bool(args.platform) != bool(args.site):
        parser.error("--platform and --site must be supplied together")

    policy = _load_policy(args.policy_config)
    evidence = _load_evidence(
        args.evidence_dir,
        args.start,
        args.end,
        platform=args.platform,
        site=args.site,
    )
    catalog = load_local_catalog(args.project_root / "data" / "shop.db")
    live_fx = _live_fx()
    fx = FxSnapshot.from_mapping(live_fx["rates"], source=live_fx["provider"], as_of=live_fx["as_of"])
    ozon_map = _load_mapping(args.ozon_sku_map)
    ozon_quantities = {}
    ozon_enrichment = {"status": "not_requested", "external_reads_performed": [], "external_writes_performed": []}
    if args.allow_ozon_read_enrichment and "ozon" in evidence:
        live_map, ozon_quantities, ozon_enrichment = _read_ozon_enrichment(
            args.project_root, evidence["ozon"]
        )
        ozon_map = {**live_map, **ozon_map}
    required_skus = set()
    for platform, platform_evidence in evidence.items():
        preview = adapt_settlement_evidence(
            platform_evidence,
            catalog,
            period_kind="weekly",
            seller_sku_by_platform_sku=ozon_map if platform == "ozon" else None,
            quantity_by_order_platform_sku=ozon_quantities if platform == "ozon" else None,
        )
        required_skus.update(str(row.get("canonical_sku") or "") for row in preview.rows)
    cost_policy = resolve_temporary_cost_policy(catalog, required_skus)
    resolved_costs = {
        sku: Decimal(str(value["unit_cost_cny"])) for sku, value in cost_policy.values.items()
    }
    resolved_catalog = replace(catalog, costs_by_sku=resolved_costs)
    costs = CostSnapshot.from_mapping(cost_policy.values)
    configured_rates = policy["weekly_ad_rates"]
    if args.ad_rate is not None:
        global_ad_rate = Decimal(args.ad_rate)
        global_ad_source = "operator_global_override"
        platform_ad_rates = {}
        platform_ad_sources = {}
    else:
        global_ad_rate = Decimal(configured_rates["default"])
        global_ad_source = "policy_config"
        platform_ad_rates = {
            platform: Decimal(configured_rates[platform])
            for platform in ("tiktok", "shopee", "ozon")
        }
        platform_ad_sources = {platform: "policy_config" for platform in platform_ad_rates}
    for platform, value in (
        ("tiktok", args.tiktok_ad_rate),
        ("shopee", args.shopee_ad_rate),
        ("ozon", args.ozon_ad_rate),
    ):
        if value is not None:
            platform_ad_rates[platform] = Decimal(value)
            platform_ad_sources[platform] = "operator_platform_override"
    tiktok_local_fulfillment_fee = Decimal(
        args.tiktok_local_fulfillment_fee_cny
        if args.tiktok_local_fulfillment_fee_cny is not None
        else policy["tiktok"]["local_fulfillment_fee_cny_per_order"]
    )
    shopee_local_fulfillment_fee = Decimal(
        args.shopee_local_fulfillment_fee_cny
        if args.shopee_local_fulfillment_fee_cny is not None
        else policy["shopee"]["local_fulfillment_fee_cny_per_order"]
    )
    bundle = build_weekly_evidence_bundle(
        evidence,
        resolved_catalog,
        period_start=args.start,
        period_end=args.end,
        costs=costs,
        fx=fx,
        ad_rate=global_ad_rate,
        ad_rates=platform_ad_rates,
        ad_rate_sources=platform_ad_sources,
        ad_rate_source=global_ad_source,
        tiktok_local_fulfillment_fee_cny=tiktok_local_fulfillment_fee,
        shopee_local_fulfillment_fee_cny=shopee_local_fulfillment_fee,
        seller_sku_by_ozon_sku=ozon_map,
        quantity_by_ozon_order_sku=ozon_quantities,
        cost_assumption_warnings=cost_policy.warnings,
        generated_at=datetime.now(timezone.utc),
        code_version="profit-settlement-v1-stage2",
        platforms=tuple(evidence),
    )
    catalog_issues = [_catalog_issue(item) for item in catalog.issues]
    bundle["catalog_quality_issues"] = catalog_issues
    bundle["policy_config"] = {
        "schema_version": policy["schema_version"],
        "snapshot_id": policy["snapshot_id"],
        "source_path": str(args.policy_config.resolve()),
        "weekly_ad_rates": policy["weekly_ad_rates"],
        "tiktok": policy["tiktok"],
        "shopee": policy["shopee"],
    }
    bundle["external_reads"] = [
        "settlement-evidence/v1 JSON artifacts",
        "shop.db via SQLite mode=ro",
        live_fx["provider"],
        *ozon_enrichment["external_reads_performed"],
    ]
    bundle["ozon_enrichment"] = ozon_enrichment
    bundle["external_writes_performed"] = []

    args.output.mkdir(parents=True, exist_ok=True)
    site_suffix = f"_{args.site.upper()}" if args.site else ""
    for platform, item in bundle["reports"].items():
        report = item.get("report")
        if isinstance(report, dict):
            (args.output / f"{platform}{site_suffix}_{args.start}_{args.end}.html").write_text(
                render_profit_report_html(report), encoding="utf-8"
            )
            (args.output / f"{platform}{site_suffix}_{args.start}_{args.end}.json").write_text(
                json.dumps(item, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
            )
    bundle_path = args.output / f"weekly_profit{site_suffix}_{args.start}_{args.end}.json"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": bundle["status"],
        "bundle": str(bundle_path),
        "reports": {platform: item["status"] for platform, item in bundle["reports"].items()},
        "quality_issue_counts": dict(sorted(Counter(item["code"] for item in bundle["quality_issues"]).items())),
        "catalog_quality_issue_counts": dict(sorted(Counter(item["code"] for item in catalog_issues).items())),
        "assumption_warning_counts": dict(sorted(Counter(item.code for item in cost_policy.warnings).items())),
        "external_writes_performed": [],
    }, ensure_ascii=False, indent=2))
    return 0 if bundle["status"] == "ready" else 2


def _load_evidence(
    directory: Path,
    start: date,
    end: date,
    *,
    platform: str | None = None,
    site: str | None = None,
) -> dict[str, dict]:
    if platform and site:
        normalized_site = site.upper()
        path = directory / f"{platform}_{normalized_site}_{start}_{end}.settlement.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("platform") != platform or payload.get("site") != normalized_site:
            raise ValueError(f"evidence identity mismatch: expected {platform}/{normalized_site}")
        return {platform: payload}
    output = {}
    for platform, site in (("tiktok", "TH"), ("shopee", "TH"), ("ozon", "RU")):
        path = directory / f"{platform}_{site}_{start}_{end}.settlement.json"
        output[platform] = json.loads(path.read_text(encoding="utf-8"))
    return output


def _load_policy(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "profit-settlement-policy/v1":
        raise ValueError("policy config must use profit-settlement-policy/v1")
    rates = payload.get("weekly_ad_rates")
    tiktok = payload.get("tiktok", {"local_fulfillment_fee_cny_per_order": "4"})
    shopee = payload.get("shopee")
    if not isinstance(rates, dict) or not isinstance(tiktok, dict) or not isinstance(shopee, dict):
        raise ValueError("policy config requires weekly_ad_rates and shopee objects")
    for key in ("default", "tiktok", "shopee", "ozon"):
        try:
            value = Decimal(str(rates[key]))
        except (KeyError, ValueError, ArithmeticError) as exc:
            raise ValueError(f"policy weekly_ad_rates.{key} must be a decimal fraction") from exc
        if value < 0 or value > 1:
            raise ValueError(f"policy weekly_ad_rates.{key} must be between 0 and 1")
    local_fees = {}
    for platform, config in (("tiktok", tiktok), ("shopee", shopee)):
        try:
            local_fee = Decimal(str(config["local_fulfillment_fee_cny_per_order"]))
        except (KeyError, ValueError, ArithmeticError) as exc:
            raise ValueError(f"policy {platform}.local_fulfillment_fee_cny_per_order must be a CNY amount") from exc
        if local_fee < 0:
            raise ValueError(f"policy {platform}.local_fulfillment_fee_cny_per_order must be non-negative")
        local_fees[platform] = str(local_fee)
    return {
        "schema_version": payload["schema_version"],
        "snapshot_id": f"sha256:{sha256(raw).hexdigest()}",
        "weekly_ad_rates": {key: str(Decimal(str(rates[key]))) for key in ("default", "tiktok", "shopee", "ozon")},
        "tiktok": {"local_fulfillment_fee_cny_per_order": local_fees["tiktok"]},
        "shopee": {"local_fulfillment_fee_cny_per_order": local_fees["shopee"]},
    }


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
