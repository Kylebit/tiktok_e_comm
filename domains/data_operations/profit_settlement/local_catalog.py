"""Read-only profit catalog observations using the shared I01 identity review."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from core.db import connect_readonly
from domains.product_operations.catalog_database_audit import audit_catalog_connection


@dataclass(frozen=True)
class CatalogQualityIssue:
    code: str
    record_id: str
    field: str
    message: str
    identity: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LocalCatalogSnapshot:
    seller_sku_by_platform_sku: Mapping[str, str]
    costs_by_sku: Mapping[str, Decimal]
    cost_candidates_by_sku: Mapping[str, tuple[Decimal, ...]]
    product_by_platform_sku: Mapping[str, Mapping[str, Any]]
    product_by_seller_sku: Mapping[str, Mapping[str, Any]]
    weight_by_seller_sku: Mapping[str, Mapping[str, Any]]
    snapshot_id: str
    effective_at: str
    issues: tuple[CatalogQualityIssue, ...]
    review: Mapping[str, Any] = field(default_factory=dict)
    cost_records_by_sku: Mapping[str, tuple[Mapping[str, Any], ...]] = field(default_factory=dict)
    blocked_skus: tuple[str, ...] = ()


def _metadata_rows(connection, table, optional):
    columns = {row["name"] for row in connection.execute(f'PRAGMA main.table_info("{table}")')}
    fields = ", ".join(['rowid AS _rowid', *('"' + name + '"' for name in optional if name in columns)])
    return [dict(row) for row in connection.execute(f'SELECT {fields} FROM main."{table}" ORDER BY rowid')]


def load_local_catalog(database_path: str | Path, *, cost_view: str = "historical") -> LocalCatalogSnapshot:
    """One caller-owned read transaction for I01 review and display metadata.

    Legacy maps contain only unambiguous raw Seller SKUs. Costs are local
    observations, not approved facts; full candidates and issues remain exposed.
    Historical profit reads preserve original candidate dates and conflicts.
    Callers intentionally presenting current directory costs may explicitly pass
    cost_view="current"; that does not establish historical applicability.
    """
    path = Path(database_path)
    connection = connect_readonly(path)
    try:
        connection.execute("BEGIN")
        review = audit_catalog_connection(connection, cost_view=cost_view).payload()
        if review["status"] == "check_failed":
            raise ValueError("catalog review failed: " + review["error"]["code"])
        metadata = {
            table: _metadata_rows(connection, table, columns)
            for table, columns in {
                "products": ("product_name", "image_url"),
                "shopee_products": ("product_name", "image_url", "region"),
                "sku_logistics_weights": ("seller_sku", "weight_g", "package_count", "depth_mm", "width_mm", "height_mm", "updated_at"),
            }.items()
        }
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_ozon_products'").fetchone():
            metadata['catalog_ozon_products']=[]
            for row in connection.execute('SELECT rowid AS _rowid,document_json FROM catalog_ozon_products'):
                listing=json.loads(row['document_json'])
                metadata['catalog_ozon_products'].append({'_rowid':row['_rowid'],'product_name':listing.get('name'),'image_url':(listing.get('images') or [None])[0],'region':'RU'})
    finally:
        connection.close()

    if cost_view == "historical":
        from .historical_internal_costs import attach_internal_history
        review = attach_internal_history(review)

    by_row = {table: {r["_rowid"]: r for r in rows} for table, rows in metadata.items()}
    by_sku, by_variant = defaultdict(list), defaultdict(list)
    for row in review["records"]:
        if not row["identity_valid"]:
            continue
        identity = row["identity"]
        extra = by_row[row["source"]["table"]][row["source"]["row_locator"]["rowid"]]
        row["display_metadata"] = {
            "identity": deepcopy(identity), "seller_sku": identity["seller_sku"],
            "product_name": _text(extra.get("product_name")), "variant_name": row["specification"],
            "image_url": _text(extra.get("image_url")), "currency": row["sale_currency"],
            "shop_id": identity["shop_key"], "region": row["shop"].get("region") or extra.get("region"),
            "provider_shop_id": row["shop"].get("shop_id"),
        }
        if identity['platform']=='ozon':
            row['display_metadata'].update(
                listing_status=row['listing_status'],
                listing_status_evidence=deepcopy(row['listing_status_evidence']),
                approved_currency=row['approved_sale_currency'],
                currency_evidence=deepcopy(row['sale_currency_evidence']))
        by_sku[identity["seller_sku"]].append(row)
        if identity["platform"] == "tiktok":
            by_variant[identity["variant_id"]].append(row)

    mapping, by_platform, by_seller = {}, {}, {}
    for variant, rows in by_variant.items():
        if len(rows) == 1:
            mapping[variant] = rows[0]["identity"]["seller_sku"]
            by_platform[variant] = deepcopy(rows[0]["display_metadata"])

    issues = [
        CatalogQualityIssue(item["code"], _issue_record_id(item), item["matching_basis"],
                            "I01 catalog review: " + item["code"], deepcopy(item.get("identity") or {}))
        for item in review["issues"]
    ]
    costs, candidates, cost_records = {}, {}, {}
    blocked = set()
    for sku, rows in sorted(by_sku.items()):
        if len(rows) != 1:
            # The user's internal-SKU rule shares current cost across targets;
            # display/settlement identity metadata still requires exact scope.
            resolved=all(any(c.get('historical_internal_sku') for c in row['cost']['candidates']) for row in rows) if cost_view == "historical" else all(row['cost']['candidates'] and all(str(c['source'].get('source_ref','')).startswith('canonical') for c in row['cost']['candidates']) for row in rows)
            if not resolved:blocked.add(sku)
            issues.append(CatalogQualityIssue("ambiguous_seller_sku_scope", sku, "identity",
                                             "Multiple full identities cannot share an inferred global cost or display record"))
        else:
            by_seller[sku] = deepcopy(rows[0]["display_metadata"])
        records = {}
        for row in rows:
            for candidate in row["cost"]["candidates"]:
                # Keep all source observations that actually claim this variant,
                # not amounts retrieved merely by suffix or a cross-shop SKU.
                if candidate["matching_basis"] in {"exact_variant_identity", "exact_product_offer_identity", "ambiguous_variant_key", "user_internal_sku_history"}:
                    records[candidate["candidate_id"]] = deepcopy(candidate)
        ordered = tuple(records[key] for key in sorted(records))
        cost_records[sku] = ordered
        values = tuple(sorted({_decimal(c["amount"]) for c in ordered if c["valid_value"]}))
        candidates[sku] = values
        if any(c["matching_basis"] == "ambiguous_variant_key" or c["currency"] != "CNY" for c in ordered):
            blocked.add(sku)
        if any((item["code"] in {"invalid_variant_identity", "missing_shop_mapping", "ambiguous_full_identity_or_alias", "canonical_sku_cost_conflict"} or (item["code"] == "current_cost_without_historical_applicability" and not ordered))
               and (item.get("identity") or {}).get("seller_sku") == sku for item in review["issues"]):
            blocked.add(sku)
        if cost_view == "historical" and len(values) > 1 and not _disjoint_dated_costs(ordered):
            # Only complete, nonoverlapping dated intervals can be resolved by
            # a caller's exact period. Undated or overlapping prices stay blocked.
            blocked.add(sku)
        if sku not in blocked and len(values) == 1:
            valid = [c for c in ordered if c["valid_value"]]
            newest = max(valid, key=lambda c: (_source_time(c["source"]["source_updated_at"]), -c["source"]["row_locator"]["rowid"]))
            costs[sku] = _decimal(newest["amount"])
        if len(values) > 1:
            issues.append(CatalogQualityIssue("conflicting_cost", sku, "cost_cny",
                                             "Multiple local cost observations; any highest-value selection is an explicit temporary assumption"))

    if cost_view == "historical":
        from shared_platform.internal_catalog_sku import internal_sku
        for raw_sku, original_records in list(cost_records.items()):
            code = internal_sku(raw_sku)
            if code == raw_sku or not any(c.get('historical_internal_sku') == code for c in original_records):
                continue
            merged = {c['candidate_id']: c for c in (*cost_records.get(code, ()), *original_records) if c.get('historical_internal_sku') == code and c['matching_basis'] == 'user_internal_sku_history'}
            ordered = tuple(merged[k] for k in sorted(merged))
            cost_records[code] = ordered
            values = tuple(sorted({_decimal(c['amount']) for c in ordered if c['valid_value']}))
            candidates[code] = values
            if any(c['matching_basis'] == 'ambiguous_variant_key' or c['currency'] != 'CNY' for c in ordered):
                blocked.add(code)
            if len(values) > 1 and not _disjoint_dated_costs(ordered):
                blocked.add(code)
            if code not in blocked and len(values) == 1:
                valid = [c for c in ordered if c['valid_value']]
                newest = max(valid, key=lambda c: (_source_time(c['source']['source_updated_at']), -c['source']['row_locator']['rowid']))
                costs[code] = _decimal(newest['amount'])

    weight_by_seller = {}
    weight_groups = defaultdict(list)
    for row in metadata["sku_logistics_weights"]:
        if _text(row.get("seller_sku")):
            weight_groups[_text(row["seller_sku"])].append(row)
    for sku, rows in weight_groups.items():
        if len(rows) != 1:
            issues.append(CatalogQualityIssue("ambiguous_weight_source", sku, "weight", "Multiple weight rows require source review"))
            continue
        row = rows[0]
        weight_by_seller[sku] = {
            "unit_weight_g": row.get("weight_g"), "package_count": row.get("package_count"),
            "depth_mm": row.get("depth_mm"), "width_mm": row.get("width_mm"), "height_mm": row.get("height_mm"),
            "weight_source": "catalog:sku_logistics_weights:rowid:" + str(row["_rowid"]),
        }

    canonical = {
        "schema": "profit-local-catalog/v2", "mapping": mapping,
        "costs": {key: str(value) for key, value in costs.items()},
        "cost_candidates_by_sku": {key: [str(value) for value in values] for key, values in candidates.items()},
        "cost_records_by_sku": _stable(cost_records), "issues": [asdict(issue) for issue in issues],
        "review": _stable(review), "metadata": metadata, "blocked_skus": sorted(blocked),
    }
    digest = sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    effective_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    return LocalCatalogSnapshot(mapping, costs, candidates, by_platform, by_seller, weight_by_seller,
                                f"shop-db-catalog:{digest}", effective_at, tuple(issues), review, cost_records, tuple(sorted(blocked)))


def enrich_settlement_row(row: Mapping[str, Any], catalog: LocalCatalogSnapshot) -> dict[str, Any]:
    """Attach catalog data only after verifying the source identity.

    Raw identity and amounts stay available. A failed binding clears the
    derived canonical key so downstream cost snapshots cannot retry globally.
    """
    from .catalog_scope import catalog_scope_issue
    output = dict(row)
    platform_sku = _text(row.get("platform_sku"))
    seller_sku = _text(row.get("canonical_sku") or row.get("seller_sku"))
    metadata = catalog.product_by_platform_sku.get(platform_sku) or catalog.product_by_seller_sku.get(seller_sku) or {}
    issue = catalog_scope_issue(metadata, platform=row.get("platform"),
        shop_id=row.get("shop_id"), site=row.get("region"), currency=row.get("currency"),
        product_id=row.get("product_id"), seller_sku=seller_sku,
        variant_id=platform_sku if row.get("platform") == "tiktok" else "", require_identity=True)
    if seller_sku in catalog.blocked_skus:
        issue = "catalog_scope_unverified"
    if row.get("source_platform") and row["source_platform"] != row.get("platform"):
        issue = "catalog_scope_mismatch"
    if issue:
        output["catalog_scope_issue"] = issue
        output["canonical_sku"] = ""
        output["catalog_identity"] = {}
        return output
    output["catalog_identity"] = dict(metadata["identity"])
    for name in ("product_name", "variant_name", "image_url"):
        if not _text(output.get(name)) and _text(metadata.get(name)):
            output[name] = metadata[name]
    if seller_sku not in catalog.blocked_skus:
        for name, value in (catalog.weight_by_seller_sku.get(seller_sku) or {}).items():
            if output.get(name) in (None, ""):
                output[name] = value
    return output


def _stable(value):
    if isinstance(value, Mapping):
        return {key: _stable(item) for key, item in value.items() if key not in {"observed_at", "database_path", "issue_id"}}
    if isinstance(value, (tuple, list)):
        return [_stable(item) for item in value]
    return value


def _issue_record_id(item):
    identity = item.get("identity") or {}
    return json.dumps(identity, sort_keys=True, separators=(",", ":")) if identity else str(item["source"].get("row_locator") or "catalog")


def _source_time(value):
    number = _decimal(value)
    if number is not None:
        return number
    try:
        return Decimal(str(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()))
    except (TypeError, ValueError, OverflowError):
        return Decimal("0")


def _disjoint_dated_costs(records) -> bool:
    """Different historical prices may coexist only in verified disjoint windows."""
    intervals = []
    for record in records:
        if not record.get("valid_value") or record.get("currency") != "CNY":
            return False
        try:
            start = datetime.fromisoformat(str(record["valid_from"]).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(record["valid_to"]).replace("Z", "+00:00"))
            if start.tzinfo is None or end.tzinfo is None or start >= end:
                return False
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        intervals.append((start, end, _decimal(record.get("amount"))))
    return all(left[2] == right[2] or left[1] <= right[0] or right[1] <= left[0]
               for index, left in enumerate(intervals) for right in intervals[index + 1:])


def _decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value)) if value not in (None, "") else None
        return result if result is not None and result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _text(value: object) -> str:
    return str(value) if value is not None else ""
