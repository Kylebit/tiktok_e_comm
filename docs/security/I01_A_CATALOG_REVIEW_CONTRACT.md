# I01-A catalog review contract

The existing `scripts/database_maintenance.py quality` consumer now returns
`catalog-database-review/v1`: local read-only observations and review candidates
for I05 cost work and U01/U05 review interfaces. It does not approve identities,
choose a cost, repair rows, migrate schemas, or authorize a marketplace action.

## Consumer and machine states

Use an explicitly selected database in an authorized environment:

```text
python scripts/database_maintenance.py quality --database <synthetic-or-authorized-database> --fail-on-review
python scripts/database_maintenance.py quality --database <synthetic-or-authorized-database> --identity-evidence <reference-json> --fail-on-review
```

The Python consumer remains `audit_catalog_database(path, review_evidence=None)`
and `.payload()`. The CLI uses that actual checker and `core.db.connect_readonly`;
it does not call `init_db`. `check` and WAL-safe `backup` remain unchanged.

I05 adds `audit_catalog_connection(connection, review_evidence=None, observed_at=None)`
in its separate candidate. It requires an active caller-owned transaction,
`sqlite3.Row`, `query_only=ON`, and file-backed `main`; it never begins, commits,
rolls back, closes, or reconfigures that connection, including review failures.
The path API owns BEGIN/close and delegates to this projection. I05 reads its
metadata in that same transaction; main-qualified tables prevent temp shadowing.
This extension does not change I01's reference/authority or four-state contract.

| JSON status | Meaning | Default exit | `--fail-on-review` exit |
| --- | --- | --- | --- |
| `no_data` | Required schema was read, but there are no product, cost, analytics, or logistics facts. Shop metadata alone is insufficient. | 0 | 0 |
| `verified` | Existing local facts passed this check; this is not business approval or official provider readback. | 0 | 0 |
| `needs_review` | The read completed and review issues or unverified alias references exist. | 0 | 2 |
| `check_failed` | Missing/inaccessible database, unsupported schema, or invalid/unreadable reference input. Counts are unknown (`null`). | 3 | 3 |

Default exit 0 for review items preserves the existing CLI contract. Consumers
must inspect `status`; an HTTP 200 or a process exit 0 cannot mean all healthy.
`verification_scope` is `local_readonly_checks_not_business_authority`.
`check_completed` is false only on `check_failed`. All envelopes carry
`dry_run: true`, `apply_allowed: false`, and `business_writes: 0`.

## Identity and issue records

`records[].identity` has five scoped components: `platform`, `shop_key`,
`product_id`, `variant_id`, and `seller_sku`. TikTok uses shop cipher, product ID,
and SKU ID; Shopee uses shop ID, item ID, and model ID. Identifiers represented
as database integers are stringified; missing or malformed identities, leading or
trailing whitespace, and control characters are not silently repaired. `raw_identity` keeps
the observed fields. Separate shop ID/cipher/region, specification, sale currency,
listing status, and source timestamp remain available where recorded.

Every database observation has database path, table, rowid locator, and observed
time. Database issues retain that locator; reference issues instead identify the
reference and its digest. Issues include `issue_id`, full available identity,
`source`, `observed_at`, `severity`, `matching_basis`, `processing_status`, complete
candidate records, and a review-only suggestion. Rowids and issue IDs belong to
this database snapshot; they are not durable business identity or write tokens.

Matching separates exact full identity, declared alias reference, unavailable
reference, ambiguity, same-Seller-SKU candidates, and suffix candidates. ASCII
numeric tail-4 comparison retrieves candidates only. It never joins identities,
resolves costs, rewrites six-digit B lineage, or repairs display names. Same-shop
Seller SKU duplicates retain all product/variant identities and row sources.
Cross-shop observations stay separate.

## Alias and historical reference input

The optional file uses `catalog-review-evidence/v1` with `aliases` and
`historical_products` arrays. Each alias declares two complete identities,
`status: APPROVED`, `approval_ref`, `approved_by`, timezone-aware `approved_at`,
optional timezone-aware `valid_from`/`valid_until`, and `evidence_digest`.
The digest is `sha256:` plus SHA-256 of UTF-8 JSON of the alias excluding that
digest, using sorted keys, ASCII escapes, compact separators, and finite values.
Malformed dates, incomplete identity/provenance, and digest drift fail the check.

This validates content and binding, not approval authority. **No trusted catalog
alias authority source is connected in this package.** All alias outputs have:

```json
{
  "verification": {
    "level": "content_and_identity_binding_only",
    "authority_verified": false,
    "scope_status": "exact_identity_bound",
    "source_observed": true,
    "target_observed": true,
    "approval_claim": "caller_supplied_reference_not_a_verified_signature"
  }
}
```

The illustrated scope means both endpoints were uniquely observed. Other scope
states distinguish expiry, future effectiveness, absent endpoints, and ambiguous
endpoints. Every reference produces a review issue even with exact binding.
`alias_reference` cost candidates never qualify as a resolved cost. The existing
77xxxx/99xxxx rule in `shopee_demand` applies to replenishment demand; it is not
extended into catalog or cost approval here.

Historical references require platform, shop key, product ID, timezone-aware
`retired_at`, and `source_ref`. They classify a reference for review, not an
authenticated retirement. Analytics are product-level facts: current variants
are listed without treating multiple variants as duplicate analytics identity.
Classification separates `exact_product`, `alias_reference`,
`ambiguous_mapping`, `historical_product`, `missing_mapping`, and
`invalid_identity`. Historical references remain `retained_pending_review`;
all analytics retain `suggested_action: retain_original_fact`. No deletion or
automatic reassignment follows from a current-catalog mismatch.

## Cost observations for I05

Each product exposes all related cost candidates, their amount as decimal text,
raw amount, currency, source column and row locator, note/source reference,
source time, applicable identities/specifications, and recorded validity bounds.
The `cost_cny` column implies CNY when no currency column is present; contradictory
currency is invalid and remains visible. Sale currency is a different field.
Unknown/nonfinite values remain unknown, not zero. Decimal text serialization
does not round long decimal strings.

`sku_costs.sku_id` is only a local exact observation when it has one current
TikTok variant owner. Repeated variant IDs across shops or products remain
ambiguous. `cost.status` distinguishes `available`, `missing`, `invalid`,
`unresolved_candidates`, and `conflicting`. `available` means consistent valid
local exact observations exist, not an approved economic fact. Even then
`selected_amount` is null, `selection_policy` is `none_fact_review_only`, and
each candidate has `authority_verified: false`.

Scoped conflicting observations appear in `cost_coverage.conflicts`. Price or
currency disagreement among unproven suffix/same-SKU candidates separately
appears in `candidate_conflicts` and review issues; this does not prove they
belong to one variant. No maximum/latest-cost choice or currency conversion is
performed. Absent cost validity times remain `not_recorded`, preserving the
legacy local-observation contract. Explicit bounds are parsed and classified as
`expired`, `not_yet_effective`, `invalid`, or `current_declared_interval` for a
valid interval covering the observation time. `date_validation` is `passed` for
well-formed ordered intervals, `failed` for malformed/timezone-free/inverted
bounds, or `not_recorded`. Start is inclusive and end is exclusive. Past, future,
and invalid bounds produce review issues and exclude those candidates from
usable/resolved observations; amounts and raw dates remain visible. A current
declared interval permits a local observation without approving its amount:
`authority_verified` stays false and `selected_amount` stays null. The review
observation time is not a downstream calculation date. I05 must apply its own
source, temporal, currency, and policy checks before calculation.

Legacy summary sections remain. `counts.products`, cost coverage row counts,
and product-shop orphans retain TikTok scope; `counts.platform_products` and
`records` expose both platforms. `unresolved_key_count` now counts full scoped
identities, not suffix keys. `fallback_resolved_rows` is zero while alias
authority is unconnected. Analytics-orphan counts now mean non-exact current
mapping, not disposable data. Logistics suffix counts are candidate coverage,
not validated mapping. Consumers must not infer zero unresolved issues from
any single legacy summary count.

## Snapshot and integration boundaries

One read transaction explicitly selects required and available optional columns
from `shops`, `products`, `sku_costs`, `shopee_shops`, `shopee_products`,
`product_analytics`, and `sku_logistics_weights`. Missing tables/required identity
columns produce `unsupported_schema`; missing optional metadata remains null.
The supported SQLite schema has rowid locators. No alternate database or
migration framework is introduced.

This package changes only the checker, existing quality CLI, direct tests, and
this contract. It leaves B2 source identity, SKU lineage, variant display,
reservation/release controls, I05 local catalog/cost policy, and HTTP server
integration unchanged. U01 can project this envelope after its separate server
ownership review. No live catalog counts or historical audit counts are baked
into the consumer.

## Validation receipt

The final `final-contract` run uses only owned synthetic SQLite fixtures and a
startup guard installed before candidate imports. Its 51 distinct cases comprise
46 contract cases, two existing quality-consumer regressions, two existing
read-only connection cases, and one guard case with six denial probes. The two
old quality fixtures were expanded from price-only Shopee rows to required
identity columns; their historical tail-4 resolution assertion now requires
unresolved candidate presentation.

The audit-side I01 validation receipt records exact commands, source snapshots,
SHA-256 hashes, JUnit outcomes, guard logs, preserved red runs, and JSON samples
captured from actual CLI stdout. Later commits bind those tested bytes to Git
blobs; green counts from earlier runs are not accumulated. The tests do not
read real business data, credentials, or provider endpoints.
