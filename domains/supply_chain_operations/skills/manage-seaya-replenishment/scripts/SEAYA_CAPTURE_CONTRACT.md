# Local Seaya evidence adapter (U04-K)

`seaya_capture.capture(evidence, inventory_source=None)` accepts the local
`seaya-capture-evidence/v1` input contract and returns the existing captured
consumer's v2 envelope. This is not an API client, a discovered provider schema,
or proof of live collection. It performs no network, auth, DB, or default-path
lookup. `ValueError` means NOT_READY; callers must preserve that result.

The caller supplies exact `tenantId`, `sourceId`, `kind` (inventory/inbound),
`capturedAt` (source cutoff), `collectionStartedAt`, `materializedAt`, and four
`regions`: MY/MY8803, TH/TH8806, VN/VN8805, PH/PH8807. Clocks require timezone
and source cutoff <= collection start <= materialization. Inventory record
`captured_at` must equal its source cutoff; later materialization does not
advance the dashboard's snapshot date.

Each region is a packet:

```json
{
  "scope": {"warehouse": "MY8803", "selection": "all_inventory"},
  "coverage": {
    "kind": "observed_full_query", "reference": "explicit-local-receipt-identity",
    "totalRows": 0, "pageIds": ["p1"], "terminalPage": "p1"
  },
  "pages": [{"id": "p1", "next": null, "observedAt": "2026-09-05T08:00:00+00:00",
             "rows": [], "sha256": "canonical-digest-of-this-page-excluding-sha256"}]
}
```

Use `seaya_capture.digest` for canonical SHA. Ordered pages must have unique IDs,
an exact next chain and terminal receipt, matching row count, and monotonically
ordered observation clocks within collection/materialization. A total alone,
missing page, or unsupported coverage assertion cannot establish an empty or
complete scope. The reference is an explicit caller attestation; local hashes
bind supplied evidence but do not authenticate the provider or discover omitted
records. Only an independently captured full-query receipt can support the claim.

Inventory rows use the already saved business-export shape: `seller_sku`,
`warehouse`, `stock`, `available`, `allocated`, `frozen`, `inbound`, `captured_at`.
Original validator/aggregator semantics remain authoritative: every duplicate raw
inventory identity blocks as `BLOCKED_INVENTORY` because this saved shape has no
source position identity. Supported SKU aliases use the existing canonicalizer.
No replacement quantity algorithm.

Inbound packets select `all_in_transit_batches`. Rows explicitly carry
`batch_id`, `country`, `warehouse`, `status: IN_TRANSIT`, `created_at`,
`domestic_inbound_at` or `estimated_anchor_at`, `transport_days`,
`expected_sellable_date`, `total_units`. This is the narrow saved normalized
business shape, not an inferred mapping of every provider lifecycle.

The packet also has `details`, with exactly one key per listed batch and no
obsolete extra batches. Each detail has `status: DETAIL_COMPLETE` and another
complete packet selecting `all_batch_details`, with warehouse and batch_id in
scope. Detail observations cannot predate the list's last observation. Rows
use saved `box_no`, `seller_sku`, `quantity`; each canonical box/SKU pair is unique,
while one box may contain distinct SKUs. `original_quantity` is never substituted
for zero quantity. Details must sum to each batch's total and reconcile by SKU
to the existing inventory aggregator's inbound totals, under the same tenant
and source cutoff. Complete empty inbound lists require complete empty evidence
and no detail keys; the seed cannot supply a missing old batch.

To stage, save both returned envelopes and their exact file SHA values in the
existing four-source captured profile. v2 inventory/inbound must be paired.
The existing `refresh_supply_chain.py --captured-stage` recomputes normalization
from the embedded evidence before using the original validators/consumers.
Evidence, collection/materialization clocks, and batch detail clocks are retained.
Legacy paired v1 inputs retain their existing caller-attested contract; they are
not upgraded to v2 or reclassified as verified collection. No new live/apply entry.

`tests/test_u04_seaya_capture.py` contains runnable synthetic inputs and the
actual stage connection, with network/auth guards. Synthetic outputs must stay
in the test artifact tree. Do not apply these fixtures to a real seed copy.

## Current seed copy

`extract_current_seed.py --source <explicit-dashboard> --destination <new-directory>`
reads strict JSON data.js, preserves every field/country/SKU, copies all local
assets plus raw data.js and optional inbound-plan.js, and compares source hashes
before/after. It refuses existing destinations, redirected paths, missing images,
and source drift. The final manifest says `COPIED_NOT_REFRESHED`; partial failures
have no manifest. `seed.json` is semantically identical and raw JS/images remain
byte-identical. This command does not execute JS, query sources, or change clocks.

## Real evidence gap (2026-09-07 inspection)

Saved inventory page1 has 100 rows without pagination/clock/tenant receipts.
The 2026-09-06 105-row snapshot is a normalized aggregate, without saved full-query
pages or terminal evidence. Its materializer builds hardcoded rows and stamps
local execution time; do not run it to claim fresh collection.

Saved 2026-09-02 PH and TH details lack independently recorded collection clocks
and full-detail page receipts. The 2026-09-01 normalized inbound export also lacks
complete list/detail receipts; its quantity=0/original_quantity fields do not
authorize a fallback rule. Current D inbound explicitly retains historically
audited PH detail and has removed the old TH batch.

Still needed: independently captured inventory full-query/export receipts for
each exact tenant/warehouse, including scope, page chain/terminal and source
clocks; a complete active-inbound list including proven-empty country scopes;
and batch-scoped full-detail receipts with lifecycle, observation clocks and
quantity totals. Save the original artifacts and their identity bindings.
Until supplied, real Seaya integration is NOT_READY. This package proves the
explicit local contract and synthetic consumer connection only.
