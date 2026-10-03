"""Read-only catalog facts: scoped raw identities, candidates, provenance and states."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from core.db import connect_readonly

SCHEMA_VERSION = "catalog-database-review/v1"
IDENTITY_KEYS = ("platform", "shop_key", "product_id", "variant_id", "seller_sku")
_TABLES = {
    "shops": (("cipher",), ("shop_id", "region", "updated_at")),
    "products": (("sku_id", "shop_cipher", "product_id", "seller_sku", "currency"), ("sku_name", "status", "updated_at")),
    "sku_costs": (("sku_id", "cost_cny"), ("currency", "note", "updated_at", "source_ref", "valid_from", "valid_to")),
    "shopee_shops": (("shop_id",), ("region", "updated_at")),
    "shopee_products": (("model_id", "shop_id", "item_id", "seller_sku", "currency", "price"), ("model_name", "status", "updated_at")),
    "product_analytics": (("product_id", "shop_cipher"), ("synced_at",)),
    "sku_logistics_weights": (("seller_sku",), ("weight_g", "updated_at")),
}


@dataclass(frozen=True)
class CatalogDatabaseAudit:
    # Keep the original public attributes; unknown counts are None on failure.
    product_count: int | None = None
    shop_count: int | None = None
    cost_count: int | None = None
    products_by_currency: dict[str, int] = field(default_factory=dict)
    direct_missing_cost_rows: int | None = None
    direct_missing_cost_by_currency: dict[str, int] = field(default_factory=dict)
    fallback_resolved_cost_rows: int | None = None
    unresolved_cost_rows: int | None = None
    unresolved_cost_key_count: int | None = None
    cost_conflicts: tuple[dict[str, Any], ...] = ()
    candidate_cost_conflicts: tuple[dict[str, Any], ...] = ()
    same_shop_seller_sku_duplicates: tuple[dict[str, Any], ...] = ()
    product_shop_orphans: int | None = None
    analytics_orphans: int | None = None
    logistics_exact_unmatched: int | None = None
    logistics_canonical_unmatched: int | None = None
    shopee_nonpositive_prices: int | None = None
    status: str = "check_failed"
    observed_at: str = ""
    source: dict = field(default_factory=dict)
    records: tuple[dict, ...] = ()
    issues: tuple[dict, ...] = ()
    analytics: tuple[dict, ...] = ()
    logistics: tuple[dict, ...] = ()
    aliases: tuple[dict, ...] = ()
    error: dict | None = None

    @property
    def needs_review(self) -> bool:
        return self.status == "needs_review"

    def payload(self) -> dict[str, Any]:
        return deepcopy({
            "schema_version": SCHEMA_VERSION, "status": self.status,
            "verified": self.status == "verified", "needs_review": self.needs_review,
            "verification_scope": "local_readonly_checks_not_business_authority",
            "check_completed": self.status != "check_failed",
            "observed_at": self.observed_at, "source": self.source,
            "dry_run": True, "apply_allowed": False, "business_writes": 0,
            "counts": {"products": self.product_count, "shops": self.shop_count, "costs": self.cost_count,
                       "products_by_currency": self.products_by_currency,
                       "platform_products": dict(Counter(r["identity"]["platform"] for r in self.records)) if self.status != "check_failed" else None},
            "cost_coverage": {
                "direct_missing_rows": self.direct_missing_cost_rows,
                "direct_missing_by_currency": self.direct_missing_cost_by_currency,
                "fallback_resolved_rows": self.fallback_resolved_cost_rows,
                "fallback_semantics": "no_authority_verified_alias_source_connected",
                "unresolved_rows": self.unresolved_cost_rows, "unresolved_key_count": self.unresolved_cost_key_count,
                "conflicts": list(self.cost_conflicts), "candidate_conflicts": list(self.candidate_cost_conflicts)},
            "identity": {"same_shop_seller_sku_duplicates": list(self.same_shop_seller_sku_duplicates),
                         "product_shop_orphans": self.product_shop_orphans},
            "derived_data": {
                "analytics_orphans": self.analytics_orphans, "logistics_exact_unmatched": self.logistics_exact_unmatched,
                "logistics_canonical_unmatched": self.logistics_canonical_unmatched,
                "shopee_nonpositive_prices": self.shopee_nonpositive_prices},
            "records": list(self.records), "issues": list(self.issues), "analytics": list(self.analytics),
            "logistics": list(self.logistics), "alias_references": list(self.aliases), "error": self.error,
        })


def _digest(value) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def _id(value):
    return str(value) if type(value) in (str, int) else None


def _valid_text(value):
    return type(value) is str and bool(value) and value == value.strip() and not re.search(r"[\x00-\x1f\x7f]", value)


def _key(identity):
    return tuple(identity.get(key) for key in IDENTITY_KEYS)


def _valid_identity(identity):
    if identity.get('platform')=='ozon':
        from shared_platform.catalog_ozon import identity as ozon_identity
        try:ozon_identity(identity);return True
        except ValueError:return False
    return identity.get("platform") in {"tiktok", "shopee"} and all(_valid_text(identity.get(key)) for key in IDENTITY_KEYS)


def _canonical_seller_sku(value: object) -> str:
    """Candidate retrieval only; never an identity or cost resolution rule."""
    raw = str(value or "")
    return raw[-4:].zfill(4) if re.fullmatch(r"[0-9]+", raw) else raw


def _number(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _decimal_text(value):
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _timestamp(value):
    if not _valid_text(value):
        raise ValueError("timezone-aware timestamp required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp timezone required")
    return parsed


def _cost_time_applicability(raw, observed):
    start, end = raw.get("valid_from"), raw.get("valid_to")
    if start is None and end is None:
        return "not_recorded"
    try:
        start = _timestamp(start) if start is not None else None
        end = _timestamp(end) if end is not None else None
        now = _timestamp(observed)
    except (TypeError, ValueError):
        return "invalid"
    if start is not None and end is not None and start >= end:
        return "invalid"
    if end is not None and end <= now:
        return "expired"
    if start is not None and start > now:
        return "not_yet_effective"
    # Valid local declaration at observation time; not monetary authority or
    # proof of applicability to a downstream calculation date/policy.
    return "current_declared_interval"


def _evidence(value):
    if value is None:
        return (), ()
    if not isinstance(value, dict) or value.get("schema_version") != "catalog-review-evidence/v1":
        raise ValueError("unsupported review evidence schema")
    _digest(value)  # Content integrity only, never a signature or authority check.
    aliases, history = value.get("aliases", []), value.get("historical_products", [])
    if not isinstance(aliases, list) or not isinstance(history, list):
        raise ValueError("review evidence arrays required")
    for row in aliases:
        if not isinstance(row, dict) or row.get("status") != "APPROVED":
            raise ValueError("alias must carry an approved evidence record")
        if not all(isinstance(row.get(k), dict) and _valid_identity(row[k]) for k in ("alias_identity", "canonical_identity")):
            raise ValueError("alias requires two complete scoped variant identities")
        if not all(_valid_text(row.get(k)) for k in ("approval_ref", "approved_by", "approved_at")):
            raise ValueError("alias approval provenance is incomplete")
        _timestamp(row["approved_at"])
        for key in ("valid_from", "valid_until"):
            if row.get(key) is not None:
                _timestamp(row[key])
        if row.get("evidence_digest") != _digest({k: v for k, v in row.items() if k != "evidence_digest"}):
            raise ValueError("alias evidence digest mismatch")
    for row in history:
        if not isinstance(row, dict) or row.get("platform") not in {"tiktok", "shopee"} or not all(_valid_text(row.get(k)) for k in ("shop_key", "product_id", "retired_at", "source_ref")):
            raise ValueError("historical product provenance is incomplete")
        _timestamp(row["retired_at"])
    return tuple(deepcopy(aliases)), tuple(deepcopy(history))


class _SchemaError(ValueError):
    pass


def _snapshot(connection):
    snapshot = {}
    for table, (required, optional) in _TABLES.items():
        columns = {row["name"] for row in connection.execute(f'PRAGMA main.table_info("{table}")')}
        missing = set(required) - columns
        if missing:
            raise _SchemaError(f'{table}: missing {", ".join(sorted(missing))}')
        selected = [*required, *(name for name in optional if name in columns)]
        fields = ", ".join('"' + name + '"' for name in selected)
        snapshot[table] = [dict(row) for row in connection.execute(f'SELECT rowid AS _rowid, {fields} FROM main."{table}" ORDER BY rowid')]
    return snapshot


def _source(table, row, **extra):
    return {"table": table, "row_locator": {"rowid": row["_rowid"]},
            "source_updated_at": row.get("updated_at", row.get("synced_at")), **extra}


def failed_catalog_audit(path, code, *, detail=None):
    """Failures use the same machine envelope, never an empty success."""
    return CatalogDatabaseAudit(observed_at=datetime.now(timezone.utc).isoformat(),
                                source={"database_path": str(Path(path).resolve()), "access_mode": "read_only"},
                                error={"code": code, "detail": detail})


def audit_catalog_database(path: str | Path, *, review_evidence: dict | None = None,
                           cost_view: str = "current") -> CatalogDatabaseAudit:
    """No initialization, migration, inferred approval, cost selection or repair."""
    try:
        _evidence(review_evidence)
    except (ValueError, TypeError):
        return failed_catalog_audit(path, "invalid_review_evidence")
    connection = None
    try:
        connection = connect_readonly(path)
        connection.execute("BEGIN")
        return audit_catalog_connection(connection, review_evidence=review_evidence,
                                        cost_view=cost_view)
    except FileNotFoundError:
        return failed_catalog_audit(path, "database_missing")
    except (OSError, sqlite3.Error):
        return failed_catalog_audit(path, "database_read_failed")
    finally:
        if connection is not None:
            connection.close()


def audit_catalog_connection(
    connection: sqlite3.Connection, *, review_evidence: dict | None = None,
    observed_at: str | None = None, cost_view: str = "current",
) -> CatalogDatabaseAudit:
    """Project main tables inside a caller-owned read-only transaction.

    Requires SQLite Row results, query_only=ON, and an active transaction.
    Never BEGIN, COMMIT, ROLLBACK, close, or change connection configuration.
    The caller may read additional metadata in the same snapshot afterwards.
    Source path comes from SQLite, not an asserted input label. Reference input
    remains non-authoritative under the original I01 evidence contract.
    """
    if cost_view not in {"current", "historical"}:
        raise ValueError("cost_view must be current or historical")
    if (not connection.in_transaction or connection.row_factory is not sqlite3.Row
            or connection.execute("PRAGMA query_only").fetchone()[0] != 1):
        raise ValueError("an active caller-owned read-only transaction with sqlite3.Row is required")
    databases = connection.execute("PRAGMA database_list").fetchall()
    path = next((row[2] for row in databases if row[1] == "main"), "")
    if not path:
        raise ValueError("a file-backed main database is required")
    observed = observed_at or datetime.now(timezone.utc).isoformat()
    _timestamp(observed)
    try:
        aliases, history = _evidence(review_evidence)
    except (ValueError, TypeError):
        return failed_catalog_audit(path, "invalid_review_evidence")
    try:
        snapshot = _snapshot(connection)
        from shared_platform.catalog_ozon import catalog_rows
        snapshot['catalog_ozon_rows']=catalog_rows(connection)
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_identity_costs'").fetchone():
            snapshot['catalog_identity_costs'] = [dict(r) for r in connection.execute('SELECT * FROM catalog_identity_costs')]
        from shared_platform.catalog_sku_costs import read_current, entity_for_identity
        has_manual_current = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_sku_costs'").fetchone()
        snapshot['catalog_current_sku_costs']=[]
        for platform,table,shop,product,variant in [('tiktok','products','shop_cipher','product_id','sku_id'),('shopee','shopee_products','shop_id','item_id','model_id')]:
            for row in snapshot[table]:
                full=dict(platform=platform,shop_key=str(row[shop]),product_id=str(row[product]),variant_id=str(row[variant]),seller_sku=row['seller_sku'] or '')
                if cost_view == 'historical':
                    # Detect explicitly stored current-only values without
                    # invoking inherited-cost aggregation for a historical read.
                    key = entity_for_identity(connection, full) if has_manual_current else None
                    manual = connection.execute('SELECT * FROM catalog_sku_costs WHERE entity_key=?', (key,)).fetchone() if key else None
                    current = {**full, 'amount': manual['amount'], 'version': manual['version'], 'source_kind': 'MANUAL', 'source_ref': 'canonical:'+key} if manual else None
                else:
                    current=read_current(connection,full)
                if current:snapshot['catalog_current_sku_costs'].append(current)
    except _SchemaError as error:
        return failed_catalog_audit(path, "unsupported_schema", detail=str(error))
    except (OSError, sqlite3.Error):
        return failed_catalog_audit(path, "database_read_failed")
    except ValueError:
        return failed_catalog_audit(path,'invalid_ozon_identity_or_cost')
    source = {"database_path": str(Path(path).resolve()), "access_mode": "read_only",
              "consistency": "one_read_transaction", "tables": list(_TABLES),
              "review_evidence_digest": _digest(review_evidence) if review_evidence is not None else None}
    return _review(snapshot, source, observed, aliases, history, cost_view=cost_view)


def _review(snapshot, source, observed, aliases, history, *, cost_view="current"):
    records, issues = [], []
    scoped_costs = {tuple(r[k] for k in ('platform','shop_key','product_id','variant_id')): r
                    for r in snapshot.get('catalog_identity_costs', [])}
    scoped_costs.update({tuple(r[k] for k in ('platform','shop_key','product_id','variant_id')):r for r in snapshot.get('catalog_current_sku_costs',[])})
    ozon_costs={_key(r['identity']):r['cost'] for r in snapshot.get('catalog_ozon_rows',[])}

    def issue(code, identity, evidence, *, basis, candidates=()):
        evidence = {"database_path": source["database_path"], **evidence}
        item = {"code": code, "identity": identity, "source": evidence, "observed_at": observed,
                "severity": "review", "matching_basis": basis, "processing_status": "needs_review",
                "candidates": list(candidates), "suggested_action": "review_source_evidence", "apply_allowed": False}
        item["issue_id"] = _digest({"code": code, "identity": identity, "source": evidence})
        issues.append(item)

    shops = {str(row["cipher"]): row for row in snapshot["shops"]}
    sp_shops = {str(row["shop_id"]): row for row in snapshot["shopee_shops"]}
    for platform, table, cols, store in [
        ("tiktok", "products", ("shop_cipher", "product_id", "sku_id"), shops),
        ("shopee", "shopee_products", ("shop_id", "item_id", "model_id"), sp_shops),
    ]:
        for raw in snapshot[table]:
            skey, pid, vid = (_id(raw[col]) for col in cols)
            identity = dict(zip(IDENTITY_KEYS, (platform, skey, pid, vid, _id(raw["seller_sku"]))))
            shop = store.get(skey)
            row = {
                "identity": identity, "raw_identity": {key: raw[key] for key in (*cols, "seller_sku")},
                "shop": {"shop_id": _id(shop.get("shop_id")) if shop else None,
                         "region": shop.get("region") if shop else None, "shop_cipher": raw.get("shop_cipher")},
                "specification": raw.get("sku_name", raw.get("model_name")), "sale_currency": _id(raw["currency"]),
                "source": _source(table, raw, database_path=source["database_path"]), "observed_at": observed, "listing_status": raw.get("status"),
                "identity_valid": _valid_identity(identity), "processing_status": "observed",
                "price": str(raw["price"]) if raw.get("price") is not None else None,
            }
            records.append(row)
            if not row["identity_valid"]:
                issue("invalid_variant_identity", identity, row["source"], basis="raw_fields")
            if shop is None:
                issue("missing_shop_mapping", identity, row["source"], basis="exact_shop_key")
            if platform == "shopee" and (_number(raw["price"]) is None or _number(raw["price"]) <= 0):
                issue("nonpositive_or_unknown_price", identity, row["source"], basis="raw_price")

    for raw in snapshot.get('catalog_ozon_rows',[]):
        identity={**raw['identity'],'variant_id':None}
        listing=raw['listing']
        statuses=listing.get('statuses')
        state=statuses.get('status') if isinstance(statuses,dict) else None
        state=state if isinstance(state,str) and state.strip() else None
        records.append({'identity':identity,'raw_identity':deepcopy(identity),
                        'shop':{'shop_id':identity['shop_key'],'region':'RU','shop_cipher':None},
                        'specification':' / '.join(str(v) for v in listing.get('specification',{}).values()),
                        'sale_currency':None,'approved_sale_currency':listing.get('approved_currency'),
                        'sale_currency_evidence':{'observed':None,'approved':listing.get('approved_currency'),
                                                  'approved_source_ref':raw['source_ref'],'reason':'official_currency_not_captured'},
                        'price':str(listing.get('price')) if listing.get('price') is not None else None,
                        'source':_source('catalog_ozon_products',{'_rowid':raw['rowid']},database_path=source['database_path'],source_ref=raw['source_ref']),
                        'observed_at':observed,'listing_status':state,
                        'listing_status_evidence':{'raw':deepcopy(statuses),'source_field':'catalog_ozon_products.document_json.statuses',
                                                   'normalization':'NOT_NORMALIZED',
                                                   'reason':'provider_status_is_not_a_publication_or_stock_outcome' if state is not None else 'provider_status_missing_or_invalid'},
                        'identity_valid':_valid_identity(identity),'processing_status':'observed'})
    by_identity, by_sku, by_suffix, by_variant = (defaultdict(list) for _ in range(4))
    for row in records:
        if row["identity_valid"]:
            identity = row["identity"]
            by_identity[_key(identity)].append(row)
            by_sku[identity["seller_sku"]].append(row)
            by_suffix[_canonical_seller_sku(identity["seller_sku"])].append(row)
            if identity["platform"] == "tiktok":
                by_variant[identity["variant_id"]].append(row)
    alias_targets = defaultdict(list)
    for alias in aliases:
        targets = by_identity.get(_key(alias["canonical_identity"]), [])
        origins = by_identity.get(_key(alias["alias_identity"]), [])
        now = _timestamp(observed)
        if alias.get("valid_until") and _timestamp(alias["valid_until"]) <= now:
            scope = "expired"
        elif _timestamp(alias.get("valid_from") or alias["approved_at"]) > now:
            scope = "not_yet_effective"
        elif len(origins) != 1:
            scope = "source_not_observed" if not origins else "source_ambiguous"
        elif len(targets) != 1:
            scope = "target_not_observed" if not targets else "target_ambiguous"
        else:
            scope = "exact_identity_bound"
        alias["verification"] = {
            "level": "content_and_identity_binding_only", "authority_verified": False,
            "scope_status": scope, "source_observed": len(origins) == 1, "target_observed": len(targets) == 1,
            "approval_claim": "caller_supplied_reference_not_a_verified_signature",
        }
        alias_targets[_key(alias["alias_identity"])].append(alias)
        issue("alias_authority_unverified" if scope == "exact_identity_bound" else "alias_reference_" + scope,
              alias["alias_identity"], {"approval_ref": alias["approval_ref"], "review_evidence_digest": source["review_evidence_digest"]},
              basis="declared_approved_alias", candidates=[alias])
        if len(targets) != 1:
            issue("alias_reference_target_unavailable_or_ambiguous", alias["alias_identity"],
                  {"approval_ref": alias["approval_ref"]}, basis="declared_approved_alias", candidates=[r["identity"] for r in targets])

    cost_rows = []
    for raw in snapshot["sku_costs"]:
        owners = by_variant.get(_id(raw["sku_id"]), [])
        amount = _number(raw["cost_cny"])
        currency = _id(raw["currency"]) if raw.get("currency") is not None else "CNY"
        time_state = _cost_time_applicability(raw, observed)
        candidate = {
            "candidate_id": "sku_costs:" + str(raw["_rowid"]),
            "amount": _decimal_text(amount) if amount is not None else None,
            "raw_amount": str(raw["cost_cny"]) if raw["cost_cny"] is not None else None,
            "currency": currency, "column_currency": "CNY",
            "valid_value": amount is not None and amount > 0 and currency == "CNY",
            "source": _source("sku_costs", raw, column="cost_cny", primary_key={"sku_id": raw["sku_id"]},
                              source_ref=raw.get("source_ref"), note=raw.get("note"), database_path=source["database_path"]),
            "applicable_identities": [row["identity"] for row in owners],
            "applicable_specifications": [row["specification"] for row in owners],
            "valid_from": raw.get("valid_from"), "valid_to": raw.get("valid_to"),
            "time_applicability": time_state,
            "date_validation": "failed" if time_state == "invalid" else "not_recorded" if time_state == "not_recorded" else "passed",
            "applicability": "unique_current_variant_key" if len(owners) == 1 else "unresolved_variant_key",
        }
        cost_rows.append((candidate, owners))
        if time_state not in {"not_recorded", "current_declared_interval"}:
            for owner in owners or [None]:
                issue("cost_time_" + candidate["time_applicability"], owner["identity"] if owner else None,
                      candidate["source"], basis="recorded_time_bounds_require_review", candidates=[candidate])
        if not owners:
            issue("cost_source_without_current_variant", None, candidate["source"], basis="unmatched_variant_key", candidates=[candidate])

    for row in records:
        identity, ident_key = row["identity"], _key(row["identity"])
        exact_rows = by_identity.get(ident_key, []) if row["identity_valid"] else []
        approved = alias_targets.get(ident_key, [])
        target_keys = {_key(alias["canonical_identity"]) for alias in approved}
        usable_alias = len(target_keys) == 1 and all(a["verification"]["scope_status"] == "exact_identity_bound" for a in approved)
        suffix_rows = by_suffix.get(_canonical_seller_sku(identity["seller_sku"]), []) if row["identity_valid"] else []
        peers = by_sku.get(identity["seller_sku"], []) if row["identity_valid"] else []
        row["matching"] = {
            "basis": ("invalid_identity" if not row["identity_valid"] else "ambiguous" if len(exact_rows) > 1 or len(target_keys) > 1 else
                      "declared_approved_alias" if usable_alias else "unusable_alias_reference" if approved else "exact_identity"),
            "exact_identity": [r["source"] for r in exact_rows], "alias_references": approved,
            "same_seller_sku_candidates": [r["identity"] for r in peers if r is not row],
            "suffix_candidates": [r["identity"] for r in suffix_rows if r["identity"]["seller_sku"] != identity["seller_sku"]],
            "candidate_use": "review_only_no_merge",
        }
        candidates = []
        related = {_key(r["identity"]) for r in suffix_rows + peers} | target_keys | {ident_key}
        for candidate, owners in cost_rows:
            owner_keys = {_key(owner["identity"]) for owner in owners}
            if not owner_keys.intersection(related):
                continue
            entry = deepcopy(candidate)
            if len(owners) != 1:
                basis = "ambiguous_variant_key"
            elif ident_key in owner_keys and len(exact_rows) == 1:
                basis = "exact_variant_identity"
            elif usable_alias and owner_keys.intersection(target_keys):
                basis = "alias_reference"
            elif any(owner["identity"]["seller_sku"] == identity["seller_sku"] for owner in owners):
                basis = "same_seller_sku_candidate"
            else:
                basis = "suffix_candidate"
            entry["matching_basis"] = basis
            entry["usable_identity_match"] = (basis == "exact_variant_identity" and entry["valid_value"]
                                              and entry["time_applicability"] in {"not_recorded", "current_declared_interval"})
            entry["authority_verified"] = False
            candidates.append(entry)
        if row["identity_valid"]:
            scoped = scoped_costs.get(tuple(identity[k] for k in ('platform','shop_key','product_id','variant_id')))
            if cost_view == "historical" and identity['platform'] != 'ozon':
                # A present-day projection cannot erase dated observations or
                # conflicts when calculating a past period. Nor does a current
                # manual/publication amount prove a past cost or a missing cost.
                if scoped and not any(c['matching_basis'] == 'exact_variant_identity' for c in candidates):
                    issue('current_cost_without_historical_applicability', identity, row['source'], basis='historical_cost_evidence')
                scoped = None
            cost_table='catalog_identity_costs';cost_basis='exact_variant_identity'
            if identity['platform']=='ozon':
                candidates=[];scoped=ozon_costs.get(_key(identity))
                if scoped is not None:scoped={**scoped,'seller_sku':scoped['offer_id']}
                cost_table='catalog_ozon_costs';cost_basis='exact_product_offer_identity'
            if scoped and scoped.get('source_ref','').startswith('canonical'):
                cost_table='catalog_sku_costs' if scoped['source_kind']=='MANUAL' else 'catalog_sku_resolution'
            if scoped and scoped.get('source_kind')=='CONFLICT':
                candidates=[]
                for original in scoped['sources']:
                    candidates.append({'candidate_id':'canonical-conflict:'+_digest(original),'amount':original['amount'],'raw_amount':original['amount'],'currency':'CNY','column_currency':'CNY','valid_value':True,
                        'source':{'table':original['source'],'row_locator':{'rowid':original['version']},'source_updated_at':None,'source_ref':scoped['source_ref'],'version':original['version'],'source_kind':original['source'],'database_path':source['database_path']},
                        'applicable_identities':[identity],'applicable_specifications':[row['specification']],'valid_from':None,'valid_to':None,'time_applicability':'not_recorded','date_validation':'not_recorded','applicability':'user_defined_internal_sku','matching_basis':'exact_variant_identity','usable_identity_match':False,'authority_verified':False})
                issue('canonical_sku_cost_conflict',identity,row['source'],basis='user_defined_internal_sku',candidates=candidates)
                scoped=None
            if scoped is not None and (scoped['seller_sku'] != identity['seller_sku'] or _number(scoped['amount']) is None or _number(scoped['amount']) <= 0):
                scoped = None
                candidates = []
                issue("scoped_cost_identity_conflict", identity, row["source"], basis="full_identity")
            if scoped is not None:
                # Current full-identity facts supersede the unscoped legacy
                # observation for this view only; stored history is untouched.
                candidates = [{
                    "candidate_id": cost_table + ':' + _digest(identity) + ':' + str(scoped['version']),
                    "amount": scoped['amount'], "raw_amount": scoped['amount'],
                    "currency": "CNY", "column_currency": "CNY", "valid_value": True,
                    "source": {"table": cost_table, "row_locator": {"rowid": scoped['version']},
                               "source_updated_at": None, "source_ref": scoped['source_ref'],
                               "version": scoped['version'], "source_kind": scoped['source_kind'],
                               "database_path": source['database_path']},
                    "applicable_identities": [identity], "applicable_specifications": [row['specification']],
                    "valid_from": None, "valid_to": None, "time_applicability": "not_recorded",
                    "date_validation": "not_recorded", "applicability": "exact_full_identity",
                    "matching_basis": cost_basis, "usable_identity_match": True,
                    "authority_verified": False,
                }]
        eligible = [c for c in candidates if c["usable_identity_match"]]
        values = {(c["amount"], c["currency"]) for c in eligible}
        invalid_match = any(c["matching_basis"] == "exact_variant_identity" and not c["valid_value"] for c in candidates)
        cost_status = ("conflicting" if len(values) > 1 else "invalid" if invalid_match else
                       "available" if values else "unresolved_candidates" if candidates else "missing")
        row["cost"] = {"status": cost_status, "selected_amount": None, "selection_policy": "none_fact_review_only",
                       "candidates": candidates, "resolved_candidate_ids": [c["candidate_id"] for c in eligible] if cost_status == "available" else []}
        if cost_status != "available":
            issue("cost_" + cost_status, identity, row["source"], basis=row["matching"]["basis"], candidates=candidates)
            row["processing_status"] = "needs_review"
        candidate_values = {(c["amount"], c["currency"]) for c in candidates if c["amount"] is not None}
        if len(candidate_values) > 1 and cost_status != "conflicting":
            # Disagreement is review evidence, not proof of one shared variant
            # or a reason to replace a local observation by the highest value.
            issue("candidate_cost_disagreement", identity, row["source"], basis="unresolved_candidate_identity_or_currency", candidates=candidates)
        if len(exact_rows) > 1 or len(target_keys) > 1:
            issue("ambiguous_full_identity_or_alias", identity, row["source"], basis="ambiguous", candidates=approved)

    duplicates, groups = [], defaultdict(list)
    for row in records:
        if row["identity_valid"]:
            ident = row["identity"]
            groups[(ident["platform"], ident["shop_key"], ident["seller_sku"])].append(row)
    for (platform, shop_key, seller_sku), group in sorted(groups.items()):
        if len(group) < 2:
            continue
        item = {"platform": platform, "shop_key": shop_key, "shop_cipher": shop_key if platform == "tiktok" else None,
                "seller_sku": seller_sku, "row_count": len(group),
                "product_count": len({r["identity"]["product_id"] for r in group}),
                "sku_count": len({r["identity"]["variant_id"] for r in group}),
                "identities": [r["identity"] for r in group], "sources": [r["source"] for r in group],
                "disposition": "review_only_never_delete_or_merge"}
        duplicates.append(item)
        for row in group:
            issue("same_shop_seller_sku_duplicate", row["identity"], row["source"], basis="exact_seller_sku_within_shop", candidates=item["identities"])

    analytics = []
    for raw in snapshot["product_analytics"]:
        pid, shop_key = _id(raw["product_id"]), _id(raw["shop_cipher"])
        identity = {"platform": "tiktok", "shop_key": shop_key, "product_id": pid,
                    "variant_id": None, "seller_sku": None, "granularity": "product"}
        current = [r for r in records if r["identity_valid"] and
                   (r["identity"]["platform"], r["identity"]["shop_key"], r["identity"]["product_id"]) == ("tiktok", shop_key, pid)]
        historical = [h for h in history if (h["platform"], h["shop_key"], h["product_id"]) == ("tiktok", shop_key, pid)]
        approved = [a for a in aliases if tuple(a["alias_identity"][k] for k in ("platform", "shop_key", "product_id")) == ("tiktok", shop_key, pid)]
        canonical = {tuple(a["canonical_identity"][k] for k in ("platform", "shop_key", "product_id")) for a in approved}
        alias_ambiguous = approved and (len(canonical) != 1 or any(len(by_identity.get(_key(a["canonical_identity"]), [])) != 1 for a in approved))
        state = ("invalid_identity" if not _valid_text(pid) or not _valid_text(shop_key) else
                 "exact_product" if current else "ambiguous_mapping" if alias_ambiguous else "alias_reference" if approved else
                 "historical_product" if historical else "missing_mapping")
        item = {
            "identity": identity, "source": _source("product_analytics", raw, database_path=source["database_path"]), "observed_at": observed,
            "classification": state, "matching_basis": state, "current_variants": [r["identity"] for r in current],
            "historical_evidence": historical, "alias_references": approved,
            "classification_level": "exact_local_product_binding" if state == "exact_product" else "reference_or_unresolved",
            "authority_verified": False,
            "candidate_products": [r["identity"] for r in records if r["identity"]["product_id"] == pid and r["identity"]["shop_key"] != shop_key],
            "processing_status": "retained_pending_review" if state == "historical_product" else ("observed" if state == "exact_product" else "needs_review"),
            "suggested_action": "retain_original_fact", "apply_allowed": False,
        }
        analytics.append(item)
        if state != "exact_product":
            issue("analytics_" + state, identity, item["source"], basis=state, candidates=item["candidate_products"])

    logistics = []
    for raw in snapshot["sku_logistics_weights"]:
        seller_sku = _id(raw["seller_sku"])
        exact = by_sku.get(seller_sku, [])
        suffix = by_suffix.get(_canonical_seller_sku(seller_sku), [])
        item = {"seller_sku": seller_sku, "source": _source("sku_logistics_weights", raw, database_path=source["database_path"]),
                "exact_candidates": [r["identity"] for r in exact], "suffix_candidates": [r["identity"] for r in suffix if r not in exact],
                "matching_basis": "exact_seller_sku_candidate" if exact else "suffix_candidate", "apply_allowed": False}
        logistics.append(item)
        if len(exact) != 1:
            issue("logistics_mapping_needs_review", None, item["source"], basis=item["matching_basis"], candidates=item["exact_candidates"] + item["suffix_candidates"])

    affected = {_key(item["identity"]) for item in issues if item["identity"] is not None}
    for row in records:
        if _key(row["identity"]) in affected:
            row["processing_status"] = "needs_review"
    tk = [r for r in records if r["identity"]["platform"] == "tiktok"]
    missing = [r for r in tk if not any(c["usable_identity_match"] and c["matching_basis"] == "exact_variant_identity" for c in r["cost"]["candidates"])]
    resolved = [r for r in missing if r["cost"]["status"] == "available"]
    unresolved = [r for r in missing if r["cost"]["status"] != "available"]
    conflicts = tuple({
        "seller_sku_key": r["identity"]["seller_sku"], "identity": r["identity"],
        "costs_cny": tuple(sorted({c["amount"] for c in r["cost"]["candidates"] if c["usable_identity_match"]}, key=Decimal)),
        "candidates": r["cost"]["candidates"],
    } for r in tk if r["cost"]["status"] == "conflicting")
    has_facts = any(snapshot[t] for t in ("products", "shopee_products", "sku_costs", "product_analytics", "sku_logistics_weights")) or bool(snapshot.get('catalog_ozon_rows'))
    status = "needs_review" if issues else ("verified" if has_facts else "no_data")
    return CatalogDatabaseAudit(
        product_count=len(snapshot["products"]), shop_count=len(snapshot["shops"]), cost_count=len(snapshot["sku_costs"]),
        products_by_currency=dict(sorted(Counter(str(r["sale_currency"] or "") for r in tk).items())),
        direct_missing_cost_rows=len(missing), direct_missing_cost_by_currency=dict(sorted(Counter(str(r["sale_currency"] or "") for r in missing).items())),
        fallback_resolved_cost_rows=len(resolved), unresolved_cost_rows=len(unresolved),
        unresolved_cost_key_count=len({_key(r["identity"]) for r in unresolved}),
        cost_conflicts=conflicts,
        candidate_cost_conflicts=tuple({"identity": i["identity"], "source": i["source"], "matching_basis": i["matching_basis"], "candidates": i["candidates"]}
                                       for i in issues if i["code"] == "candidate_cost_disagreement"),
        same_shop_seller_sku_duplicates=tuple(duplicates),
        product_shop_orphans=sum(r["identity"]["shop_key"] not in shops for r in tk),
        analytics_orphans=sum(i["classification"] != "exact_product" for i in analytics),
        logistics_exact_unmatched=sum(not i["exact_candidates"] for i in logistics),
        logistics_canonical_unmatched=sum(not i["exact_candidates"] and not i["suffix_candidates"] for i in logistics),
        shopee_nonpositive_prices=sum(i["code"] == "nonpositive_or_unknown_price" for i in issues),
        status=status, observed_at=observed, source=source, records=tuple(records), issues=tuple(issues),
        analytics=tuple(analytics), logistics=tuple(logistics), aliases=aliases,
    )
