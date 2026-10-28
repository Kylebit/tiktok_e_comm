# Shopee 2026-07 settlement source preflight

## Result

July's final days are not entirely missing from the recovered TH source. The recovered Shopee TH escrow evidence contains 214 unique settled orders released on 2026-07-31 in `Asia/Bangkok`. This snapshot set records no release batch on July 26-30; that is not proof that the platform had no settlement on those dates. The UI screenshot is grouped by order creation date, while monthly settlement inclusion is governed by `escrow_release_time`; the two timelines must not be conflated.

The accepted runtime on port 49289 has no local settlement source files (`file_count=0`) and no saved Shopee reports. Its empty report store therefore cannot prove that Shopee had no late-July settlements.

## Exact shop bindings and credential state

The historical live configuration binds:

- MY: shop `1561117812`, `LivelyHive`
- PH: shop `1527371343`, `LivelyHive`
- TH: shop `1561124013`, `LivelyHive`
- VN: shop `1723948773`, `WangLuck6.vn`

All four access tokens expired on 2026-09-07 UTC. Refresh tokens exist, but refreshing would rewrite the token store. This read-only preflight therefore made no Shopee API call and performed no credential refresh.

## Recovered actual sources

Only TH has recoverable local Shopee escrow JSON: 47 overlapping `shopee_escrow_TH_*.json` snapshots. After deduplicating by order ID and classifying `release_ts` in Thailand local time, July contains 781 unique settled orders:

- 2026-07-01: 54
- 2026-07-07: 103
- 2026-07-14: 215
- 2026-07-21: 195
- 2026-07-31: 214

Representative late-July source:

`D:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm\outputs\shopee_escrow_TH_20260718_20260731.json`

SHA-256: `d37e4ef610e11ff2d62c8fdcc7b9e1e1a5a62d3108353dbd14de5e1021e9b24e`

The representative file was generated at `2026-07-31T23:34:50.287835+00:00`, is 375,001 bytes, and contains 428 product lines across 409 unique orders. Its local July 31 segment contains 226 product lines across 214 unique orders. The same 214-order segment is repeated consistently in multiple overlapping snapshots.

This is historical actual escrow evidence, not an estimate. It is not yet a proof of complete July coverage until reconciled against a fresh official readback. No equivalent Shopee escrow JSON was found for MY, PH, or VN. `CURSOR/Income_Data/income_{SITE}_*.csv` belongs to the TikTok source contract and must not be relabelled as Shopee.

## Producer and completeness audit

The legacy producer called `payment/get_escrow_list` with `page_size=100`, incremented `page_no` while the response's `more` flag was true, then called `payment/get_escrow_detail` for each deduplicated order. Within one run it deduplicated by `order_sn`, keeping the greatest `escrow_release_time`.

The JSON does **not** retain page count, the terminal `more=false` response, raw list/detail responses, epoch request bounds, timezone, or generator code version. Consequently its pagination completion cannot be independently proven from the artifact. The old producer also constructed date bounds in UTC rather than site-local time and rendered `release_time` text in UTC. The audit count therefore uses `release_ts` converted to `Asia/Bangkok`; file-name dates alone are not safe local-date evidence.

The snapshot can recover actual region/currency/order ID/Seller SKU/product name/quantity/release timestamp/sale amount/net settlement/commission/service fee. It cannot recover order creation time, stable order-line/model identity, authoritative buyer-payment component, the complete fee/tax/shipping/discount/refund ledger, weight, image identity, cost/FX snapshot lineage, pagination receipt, or producer version.

Although 404 of 428 lines contain embedded product cost, those values have no cost snapshot ID or as-of time. They must not be presented as authoritative historical cost. Twenty-four lines have no embedded cost at all. No estimated fee or current catalog cost was used in this audit.

Machine-readable field/type/presence evidence and line-numbered producer excerpts are retained beside this report as `STRUCTURE_EVIDENCE.json` and `PRODUCER_EXCERPTS.md`. Independent verification is also available at `D:\OrbitEvidence\th-settlement-independent-20260909\STRUCTURE.json`; this package does not copy or modify that independent record.

A separate partial candidate is retained under `artifacts/historical_settlement_candidates/2026-07`. It contains only legacy-normalized actual source fields, replaces raw order IDs with deterministic SHA-256 identities, converts `release_ts` to Thailand local time, and leaves historical cost, FX, and profit null. It is not wired into the accepted runtime and cannot replace the formal example until independent review passes.

## Safe restoration checklist

1. In a controlled writable credential copy, explicitly authorize one Shopee token refresh for the four exact shop IDs above. Preserve before/after token-store checksums and never log token values.
2. Before a full pull, call `payment/get_escrow_list` once per site with page size 1 and site-local July bounds. Record response status, returned count metadata, shop/site identity, and request timestamps; do not fetch details yet.
3. If all four minimal reads pass, pull each site independently into a new immutable source directory. Use `escrow_release_time` and the site's local timezone for July inclusion. Do not use order creation time for inclusion.
4. For TH, reconcile the fresh official order-ID set against the recovered 781-order set. Keep both missing-on-left and missing-on-right lists as digests/counts before declaring complete.
5. MY, PH, and VN require fresh official pulls because no historical local Shopee actual source was recovered. Do not substitute estimates or TikTok income CSVs.
6. Only after source completeness succeeds should the profit report be rebuilt. The UI may then show both settlement-date and order-date daily counts, clearly labelled.

## Side effects

- Shopee API reads: 0
- Credential refreshes: 0
- Platform writes: 0
- Production database writes: 0
- Output/report overwrites: 0
