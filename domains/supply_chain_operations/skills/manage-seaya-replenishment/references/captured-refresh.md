# Captured refresh: local source to dashboard output

Use the same `scripts/refresh_supply_chain.py` entry as preflight. Paths below
refer to the canonical Skill unless explicitly rooted at the project. This path
reads already captured, redacted files; it never discovers accounts, authenticates,
calls a provider, downloads images, installs a Skill, or changes a scheduler.
Raw source files must omit buyer details and credentials. A declared source ID
and complete-page envelope are local evidence, not proof of provider authenticity.

## Fixed profile and source contract

Keep existing profile fields `project_root`, `branch`, `source_commit`,
`source_sha256`, `artifact_root`, and `stable_release`. They pin the exact clean
source and its declared consumers. Use `schema=supply-chain-refresh-profile/v2`
and add `captured` with exactly:

```json
{
  "sources": {
    "inventory": {"path": "inventory.json", "sha256": "SHA256_OF_RAW_FILE_BYTES"},
    "orders": {"path": "orders.json", "sha256": "SHA256_OF_RAW_FILE_BYTES"},
    "inbound": {"path": "inbound.json", "sha256": "SHA256_OF_RAW_FILE_BYTES"},
    "seed": {"path": "seed.json", "sha256": "SHA256_OF_RAW_FILE_BYTES"}
  },
  "targets": {
    "MY": {"warehouse": "MY8803", "tiktok": "EXACT_MY_TARGET", "shopee": "EXACT_MY_TARGET"},
    "TH": {"warehouse": "TH8806", "tiktok": "EXACT_TH_TARGET", "shopee": "EXACT_TH_TARGET"},
    "VN": {"warehouse": "VN8805", "tiktok": "EXACT_VN_TARGET", "shopee": "EXACT_VN_TARGET"},
    "PH": {"warehouse": "PH8807", "tiktok": "EXACT_PH_TARGET", "shopee": "EXACT_PH_TARGET"}
  },
  "output_root": "applied-dashboard"
}
```

Replace placeholders with the actual non-secret identities and hashes; this
example supplies no business facts. File paths are under the explicitly selected
project `outputs/...` artifact root, outside `output_root`. The output is another
child of that artifact root. It must already contain every seed row's local
`assets/...` image. No source dashboard file or old image is overwritten.

`seed.json` is the complete dashboard data object (without its JavaScript wrapper),
including the four countries, exact SKU presentation and warehouse config. Use
the current destination data for a subsequent refresh: a new stage refuses a
stale seed. Original row metadata and settlement economics survive the existing
inventory/order apply consumers. Unknown inventory, order or inbound SKUs require
separate presentation intake; they cannot silently trigger image downloads.

Each of inventory/orders/inbound has `schema=supply-chain-captured-source/v1`,
its matching `kind`, a nonempty `sourceId`, a timezone-aware `capturedAt`, and
exactly MY/TH/VN/PH `regions`. Orders also have integer `days` (30–366). A region
contains a page envelope; orders contain separate `tiktok` and `shopee` envelopes:

```json
{
  "target": "EXACT_MATCHING_PROFILE_TARGET",
  "totalRows": 2,
  "pages": [
    {"cursor": "", "nextCursor": "page-2", "rows": ["FIRST_COMPLETE_RECORD"]},
    {"cursor": "page-2", "nextCursor": "", "rows": ["SECOND_COMPLETE_RECORD"]}
  ]
}
```

Rows above stand for complete JSON objects, not strings. Retain the observed
page sequence and totals; do not fabricate a terminal empty cursor. The first
cursor is empty, intermediate next cursors must match the following page, and
the final next cursor is empty. Exact row count must equal `totalRows`. An empty
result is represented by one terminal empty page and totalRows=0, only when the
captured source actually proves full coverage. Missing pages are not zero demand.

- Inventory rows use the existing validator's complete `warehouse`, `seller_sku`,
  `captured_at`, and nonnegative integer stock/available/allocated/frozen/inbound.
  Each row's warehouse must match the envelope. Any duplicate raw
  `(warehouse, seller_sku)` blocks as `BLOCKED_INVENTORY`: the captured shape has
  no source position identity, even when the quantity fields are identical.
- Order rows are the existing redacted TikTok order/detail or Shopee complete
  order-detail objects, including IDs, status, create time and item identities/
  quantities. This adapter consumes all captured pages and the existing aggregate
  and finalize functions; it does not simulate an HTTP collector. A Shopee capture
  must contain all details, not just list IDs. Order identity duplicates, invalid
  quantities, unresolved SKU identities and rows outside the declared window fail.
  Unpaid/cancelled exclusions still use the existing lifecycle rules.
- Inbound rows use complete `batchId`, `skuQuantities`, `totalUnits`, timezone-aware
  `createdAt`, and either actual `anchorAt` or explicitly estimated
  `estimatedAnchorAt`. Preserve other existing batch timing/confirmation fields;
  do not relabel an estimate as an actual warehouse event. When supplied,
  `estimatedSellableConfirmedAt` requires timezone and `estimatedSellableDate`
  cannot precede the anchor. Exact batch totals must reconcile with inventory
  inbound per SKU. No inferred allocation or new transport algorithm is added.

## Stage, inspect, apply, recover

```text
python scripts/refresh_supply_chain.py --project-root ROOT --profile PROFILE --captured-stage
python scripts/refresh_supply_chain.py --project-root ROOT --profile PROFILE --captured-apply EXACT_STAGE_DIGEST
```

Stage validates all sources before writing `captured-stages/<digest>/stage.json`,
the two planned JavaScript data files and `preview.json`. Preview retains per-SKU
changes, all three capture clocks, source hashes and actual consumer file hashes.
It does not apply data. The inventory snapshot date and order clock stay separate;
different source dates yield `needs_review`. Existing stale-inventory UI gates
remain applicable; even matching dates do not establish current live readiness.

Explicit apply revalidates profile/sources, stage bytes and the frozen destination
base, then writes only `data.js` and `inbound-plan.js` at the fixed output root.
Unrelated files/images and browser localStorage are untouched. Batch overrides
continue through the existing exact-plan/current-timing consumer: unchanged
bindings remain usable; new source/plan identities can make old overrides stale,
but their stored bytes are never silently erased or made newly authoritative.

The existing single-writer lock and atomic per-file writer protect a persistent
`captured-apply.json` journal. This is **not** a cross-file atomic transaction.
Do not serve/deploy this output while APPLYING. After interruption, apply the
original stage again; already matching files are retained and only missing
planned writes resume. A different stage cannot bypass an unfinished journal.
Unexpected destination changes require review, not overwriting. Identical
completed apply returns REUSED with zero data writes; it does not create a new
capture time or provider fact. Invalid stage inputs leave the last good output
unchanged; partial staging never authorizes apply without the final stage record.

`--dry-run`/`--fake-source` remain available under the existing v1 contract.
Captured fixtures in regression are explicitly artificial complete inputs, not
live data. Production collection, stable deployment target, serving activation,
account scope and any existing schedule cutover remain separate future work.
# Order completeness at the captured boundary

## Explicit COMPLETE serving (U04-H)

The product server accepts an optional `supply_chain_capture` argument to `serve`:
`{"artifact_root":"outputs/EXPLICIT_ARTIFACT","output_root":"EXPLICIT_OUTPUT"}`.
These are relative to the server project root and artifact root respectively.
There is no default activation, environment discovery or daily dashboard cutover.
The ordinary server startup has other platform initialization side effects; this
argument is an integration point, not permission to start a live platform runtime.

Successful captured apply verifies the output pair, then publishes an immutable
COMPLETE marker over the frozen stage pair and atomically updates a bounded
32-version index. REUSED can finish an interrupted marker/index publication.
The mutable output journal alone is insufficient for serving. Configured HTML
requires COMPLETE, matching journal/stage/index/output hashes and the writer
lease. APPLYING or missing/corrupt evidence returns 503 without zero facts or
legacy fallback. A previously issued version URL can serve its verified frozen
pair during APPLYING. Evicted versions fail closed; no stage directory discovery
or arbitrary paths are exposed.

The original HTML is served with one external same-origin bootstrap. Both data
scripts use the same immutable version URL and SHA-256 integrity; original page
consumers run sequentially only after the data scripts load. A failed resource
shows unavailable. Unversioned data requests return 409.
The existing script consumer allowlist also recognizes the dashboard's Boolean
`data-supply-asset` marker before `src`. That presentation marker does not add a
consumer, change a generation or relax integrity. Configured serving retains the
same bootstrap sequence and CSP; it does not enable inline event handlers.
Initialization also observes runtime errors and unhandled promise rejections
until the controlled script sequence finishes. A failure prevents later consumers
and cannot be overwritten by a subsequent load event. Listeners are then removed;
they do not suppress browser reporting or claim to monitor later application work.
Local images are served
from the configured output assets with the existing traversal boundary. Other
page assets, formulas and source HTML remain unchanged. Without configuration,
legacy routing remains unchanged. Bootstrap never writes localStorage; actual
isolated Chrome checks preserve prior overlay bytes across an interleaved refresh,
a new generation and a resource failure. Existing binding/staleness rules still
belong to the original consumer. Synthetic checks do not establish live data,
deployment, account authorization or daily/APP readiness.

## Runtime data outside the code checkout

The optional v2 refresh-profile field `runtime_root` selects an explicitly owned,
existing absolute local directory outside the source checkout. `project_root`,
branch, HEAD and `source_sha256` still pin the executing code. `artifact_root`
remains an `outputs/EXPLICIT_ARTIFACT` child, now relative to `runtime_root`;
`captured.output_root` remains a child of that artifact. Existing two-field
consumer configuration and refresh profiles without `runtime_root` retain their
project-relative behavior. This does not discover or copy another worktree.

The native `operations_web_entry.py --deployment DEPLOYMENT.json --log ABSOLUTE_LOG` launcher consumes
the explicit deployment field:

```json
{
  "supply_chain_capture": {
    "runtime_root": "D:/EXPLICIT_OWNED_SUPPLY_RUNTIME",
    "artifact_root": "outputs/EXPLICIT_ARTIFACT",
    "output_root": "EXPLICIT_OUTPUT"
  }
}
```

The example is a path contract, not an existing directory or activation command.
The producer profile uses the identical `runtime_root` / `artifact_root` and its
`captured.output_root` is the consumer's `output_root`. Stage/apply CLI and all
source/seed/local-image/COMPLETE hash checks remain unchanged. Runtime-root aliases,
junctions, traversal, roots containing the checkout, and roots inside it are
rejected. No directory is created by the consumer. The producer and consumer
share the data-root writer lease across code versions; changing checkout does
not create an independent lease for the same artifact.

The deployment key is absent by default. If explicitly present, null, ambiguous
or malformed configuration is rejected before platform initialization. Validly
shaped configuration whose root, journal, stage, index or output is missing or
invalid remains configured and yields the existing visible 503 page; it cannot
fall back to bundled historical data. Unversioned data still yields 409 only
when the capture context is valid. Ordinary page code comes from the frozen
checkout; versioned data and local images come from the verified runtime capture.

The ready receipt reports the selected non-secret capture configuration. It does
not assert freshness, live inventory or producer source authority. This change
does not enable schedules/provider reads, copy existing worktree data, install a
Skill, refresh inventory, activate a formal runtime root or alter demand math.

## Explicit order capture with independent block receipts (U04-E)

The existing `supply-chain-refresh-profile/v2` remains the captured profile.
Inventory/inbound and legacy orders retain `supply-chain-captured-source/v1`.
Only orders additionally accept `supply-chain-captured-source/v2`; these versions
are different contracts and must not be confused.

`pull_order_demand.capture_orders(...)` is an explicit in-memory API around the
same `pull_tiktok_region` and `pull_shopee_region` collector loops. It requires all
four country targets (TikTok shop ID + cipher, positive Shopee shop ID), explicitly
injected per-country sessions and both requesters. Missing dependencies fail
before requests. No account discovery, first-country-match selection, default
requester, auth refresh, file output, apply or new CLI live mode is added.
The old standalone `pull_order_demand.py` CLI is unchanged and can perform live
requests and credential refresh; it is not a shortcut to this explicit contract.

The caller supplies timezone-aware `cutoff`, `started_at`, `materialized_at` and
30–366 days; `cutoff <= started_at <= materialized_at` is required. `capturedAt`
means the frozen event-window cutoff, not a new capture timestamp inferred from
an old file. `collectionStartedAt` and `materializedAt` remain separate metadata
in the source and stage preview. Caller-declared clocks and target IDs are not
proof of a live account/session. Future runtime must supply observed clocks and
verify the exact ID/cipher relation before invoking this API.

Each region/platform envelope retains `target` and `blocks`. Blocks retain exact
`start`/`end` epoch seconds, split at seven days for TikTok and fourteen days for
Shopee. Each block has its own original cursor chain, including a terminal empty
page when that block is empty. A receipt contains endpoint, target, windowStart,
windowEnd, cursor, nextCursor, rows, rowCount and SHA-256 of the canonical redacted
receipt; Shopee list additionally retains explicit boolean `more`. These hashes
bind the supplied transcript, not provider signatures. Missing terminal fields,
repeated cursor or more than 10000 pages fail; the legacy aggregate path never
gets to silently interpret those responses as a complete empty result.

Shopee detail receipts are independent of list page chains and retain endpoint,
target, `requestedIds`, rows/count/hash. Every batch (at most 50 IDs) and the full
list/detail identity set must reconcile exactly. Equal counts with wrong IDs are
rejected. TikTok duplicate IDs with equal retained business content deduplicate;
different retained content fails. Every order create_time must fall in its own
requested half-open block, and paid_time when supplied must fall in the overall
window. A later payment in another block remains legal. Real provider endpoint
boundary semantics still require verification before live use; no live request
was made to establish them in this package.

The selected whitelist preserves order ID, status, event times and existing
exclusion flags; items preserve exact SKU/product/model identities and quantities
needed by the original aggregator. Buyer/contact/address fields, response headers,
tokens, image URLs and signed URLs are not retained. No full raw response is
persisted. Required lifecycle/details and original unresolved/quantity checks
fail before a source is returned. Stage revalidates both transcript hashes and
semantics, then feeds the same aggregation/apply consumers; it does not accept a
fabricated conversion from an older SKU aggregate JSON. Complete four-country
empty transcripts remain valid zero-order evidence. Seed/images and all existing
inventory/inbound/apply restrictions remain unchanged.

This implementation was exercised with synthetic requester responses only.
Real shop targets, valid sessions, read authority and source cutoff still need
explicit runtime verification. No scheduler readiness or daily-output switch is
implied by a successful offline stage.

Every captured order must carry a nonblank string lifecycle (`status` for TikTok,
`order_status` for Shopee). Before aggregation, demand-eligible records must carry
a nonempty list of object details (`line_items` / `item_list`). Missing lifecycle
or missing, empty, malformed details block staging and leave the last good output
unchanged. The existing order consumer's explicit exclusions retain their current
meaning and may omit details; no new lifecycle mapping or demand formula is added.

Override preservation is checked against confirmations saved from the old plan
before refreshing. An unchanged binding remains reusable; a changed binding is
stale and its stored bytes remain intact. File-backed Node consumer checks do not
constitute browser localStorage validation.
