# Approved publication snapshot v4

`approved-publication-snapshot/v4` is the product-owned, JSON-only hand-off
from ReleasePlan approval to stages 05-07. It is built once from the immutable
approved plan. Execution must not read the current Product Center dashboard,
source page, content workspace, pricing preview, or category page to replace
any field in this document.

The module is pure. It does not open files, SQLite, HTTP, AI, or marketplace
clients and does not persist the result.

## Public interface

```python
from domains.product_operations import (
    approved_publication_snapshot_from_payload,
    build_approved_publication_snapshot,
    validate_approved_publication_snapshot,
)

snapshot = build_approved_publication_snapshot(approved_release_plan)
json_document = snapshot.payload()

# At a later process boundary:
verified = approved_publication_snapshot_from_payload(json_document)
assert validate_approved_publication_snapshot(verified) == verified
```

`snapshot.canonical_payload()` is the exact body covered by
`snapshot.snapshot_digest`. `snapshot.payload()` adds that digest for storage
or transport. Both return detached JSON values; mutating them cannot mutate
the typed snapshot.

## Schema

The root contains:

- `schema_version`, `snapshot_digest`;
- `offer_id`, `product_revision`, `plan_id`, `approved_at`, `approved_by`;
- `publication_targets`: exact target label plus separated platform and store;
- `bindings`: approved ReleasePlan payload digest and product/content package
  IDs;
- `product`: approved title, description, ordered images, the user-approved
  `main_category`, and strict source identity;
- `categories_by_target`: exact target label/platform/site/store identity and
  approved official provider category ID/name/path plus decision status/digest
  for every selected target;
- `skus`: ordered variant key, parent Seller SKU, Model SKU, specification,
  cost/currency, weight/package, every selected target price/currency, and
  variant images;
- `digests`: source, content, policy, category, pricing, and SKU-lineage
  evidence.

All money, weight, and dimension values are canonical positive decimal
strings. IDs reject booleans and non-canonical types. The builder requires
complete one-to-one coverage across selected variants, lineage Model SKUs,
commercial facts, and per-target per-SKU prices.

`main_category` records the user's one-time product taxonomy decision. It is
never a provider category and stages 05-07 must not send its ID to TikTok,
Shopee, or Ozon. Each Skill selects only
`categories_by_target[target_label].category` after validating the target's
platform/store identity and decision digest.

Every selected real publication target requires one `APPROVED` provider
category with a non-empty official path whose terminal ID/name equals the
category ID/name. Target coverage is exact: missing, extra, duplicated, or
cross-target identities fail closed. `miaoshou:COMMON` is a control-only
target and is represented explicitly by `category: null` and decision status
`NOT_APPLICABLE`; a fabricated category is rejected.

The canonical digest is SHA-256 over UTF-8 JSON with sorted keys, compact
separators, Unicode preserved, and non-finite numbers rejected. The digest is
not included in its own input.

## 00 wiring at approval

1. Complete the existing ProductPackage, ContentPackage, source identity, SKU
   lineage/reservation, category, pricing, and policy gates.
2. Create the immutable ReleasePlan payload with all selected platform/store
   targets, the approved main category, exact target category decisions, and
   evidence digests. The product adapter freezes supplied decisions only; it
   never invents or maps an official provider category.
3. Persist Kyle's existing ReleasePlan approval through the current CAS path.
4. In the same approval unit of work, pass the approved plan returned by the
   Store to `build_approved_publication_snapshot`.
5. Persist the returned JSON document next to that exact approval using
   `plan_id + product_revision + release_payload_digest` as the binding. A
   repeated approval must compare the full `snapshot_digest` and return the
   existing identical document; it must not overwrite a different document.
6. Stages 05-07 load only this document, call
   `approved_publication_snapshot_from_payload`, and derive platform commands
   from the verified frozen fields. Current dashboard/source/content reads may
   be shown as diagnostics but may not become execution inputs.
7. A legitimate upstream edit requires a successor product revision and
   ReleasePlan approval, producing a new snapshot digest. Never patch the old
   snapshot.

The persistence transaction and shared Store schema belong to 00 and are not
implemented in this product-domain change.

## Red/green evidence (2026-08-09)

Before the production module existed, the new focused test was run against
base `8168eadf355bedc8fb79ef9a9426c15b0a5e5b97`:

```text
pytest tests/test_approved_publication_snapshot.py -q
E ModuleNotFoundError: No module named
  'domains.product_operations.approved_publication_snapshot'
1 error in 0.20s
```

After implementation and full acceptance cases were added:

```text
pytest tests/test_approved_publication_snapshot.py -q
22 passed in 0.11s

pytest <focused snapshot + related product/release tests> -q
211 passed in 18.30s
```

Review follow-up red/green evidence on source commit `a43cc41`:

```text
pytest tests/test_approved_publication_snapshot.py::
  test_freezes_distinct_provider_categories_for_each_publication_target -q
F KeyError: 'main_category'
1 failed in 0.12s

pytest tests/test_approved_publication_snapshot.py -q
29 passed in 0.17s

pytest <focused snapshot + related product/release tests> -q
218 passed in 13.83s
```

## Fail-closed boundary

The contract rejects an unapproved plan, missing/timezone-free approval
audit, stale plan digest, source or SKU identity conflicts, duplicate variants
or Model SKUs, missing selected target/store, incomplete SKU facts, missing
per-SKU prices, missing/extra/cross-target official categories, provider path
identity drift, malformed currency/decimal/package values, unsupported schema,
unknown/missing serialized fields, and any payload digest tamper.
