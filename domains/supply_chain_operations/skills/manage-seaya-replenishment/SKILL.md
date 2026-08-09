---
name: manage-seaya-replenishment
description: Audit Seaya/雅仓 inventory and inbound supply, combine valid TikTok and Shopee order demand, use settlement facts only for savings, calculate country-level replenishment and first-stock recommendations, maintain the local supply-chain dashboard, and diagnose shortage orders. Use for Seaya inventory reads, complete SKU mapping, MY/TH/VN/PH replenishment, logistics data completion, landed-cost comparisons, or any update to domains/supply_chain_operations/dashboard.
---

# Manage Seaya Replenishment

Produce auditable, read-only, SKU-level supply decisions for MY, TH, VN, and PH.

## Enforce exact identifiers

- Read the complete source value for every SKU, warehouse code, barcode, order ID, snapshot ID, and revision.
- Reject values containing `…`, `...`, masked characters, clipped prefixes/suffixes, or synthetic wildcards.
- Never infer a SKU from its visible prefix, last four digits, title similarity, image, row position, or another country's mapping.
- Never create placeholder identifiers such as `082X`.
- Normalize aliases only through an explicit, evidence-backed mapping that preserves every original identifier. Current approved PH evidence includes `770820 → 0820`, `770821 → 0821`, and `770822 → 0822`.
- If a list cell is visually clipped, inspect its DOM value, detail page, official export, or read-only API response. If none exposes the full value, return `BLOCKED_IDENTITY` and exclude that record from stock totals and SKU decisions.
- Do not combine rows merely because their titles or images match.

Run `scripts/validate_inventory_snapshot.py SNAPSHOT.json` before consuming a newly captured inventory file.

## Follow the workflow

1. Verify the authorized worktree, branch, ownership rules, and clean/dirty state.
2. Read inventory from the strongest available current source: audited read-only API, logged-in Seaya browser page, or official export.
3. Capture complete identity and inventory fields. Read `references/decision-contract.md`.
4. Reconcile exact aliases. Preserve source identifiers and mapping evidence.
   - Validate a captured browser/export snapshot with `scripts/validate_inventory_snapshot.py SNAPSHOT.json`, then merge it with `scripts/apply_inventory_snapshot.py SNAPSHOT.json`. Exact four-digit and approved country-prefixed inventory rows are summed only within the same warehouse while preserving every source alias.
5. Build two separate ledgers by exact country and SKU: valid order facts determine quantity; settlement facts determine economics. Never use settlement rows, released escrow rows, or payout timing as demand velocity. Combine TikTok and Shopee order demand only within the same country. Distinguish `PENDING_REFRESH` (mapping and refresh credential exist, access token expired, pull not run) from `BLOCKED_AUTH`; never interpret missing order demand as zero. After a successful complete pull, every included channel fact must be `READY`, retain its exact event-time window and coverage evidence, and replace pending placeholders rather than layering data on top of them.
   - Reuse another country's row only for product presentation metadata such as name, image, dimensions, weight, and cost. Never copy its channel demand, inventory, warehouse binding, or SKU aliases.
   - Require each channel source identity to match the target country before calculation. Replace a mismatched country fact with an explicit local `no_sku_fact` record; never treat it as local demand.
   - Pull the current 31-day order snapshot with `scripts/pull_order_demand.py`, then merge it with `scripts/apply_order_demand.py SNAPSHOT`. The pull output is ignored runtime data and contains no order IDs or buyer fields; the dashboard keeps only SKU aggregates and local main images.
6. For Shopee demand, accept a canonical 4-digit SKU or the explicitly approved `77xxxx` / `99xxxx` aliases. When a model SKU has another shape, resolve it only through an exact `(item_id, model_id) -> catalog seller_sku` relation. Never use title or image similarity. Keep unresolved item lines visible as excluded evidence.
7. Calculate lead-time demand, time-phased arrival stock, target coverage, and recommended quantity from eligible orders plus Seaya supply. Read every inbound batch identity, its exact per-SKU quantity, and its exact `已入库（Reach the domestic warehouse）` operation-log time when present. Otherwise retain `NOT_YET_INBOUND` and estimate the anchor as creation time plus four days. Estimate each existing batch's sellable date from the effective anchor plus the current country transport policy, with no extra sign-and-shelve buffer, and let a user confirm actual inbound timing locally. For a new replenishment decision, use the current user-approved sequence of 3 goods-preparation days, 4 days to the domestic warehouse, and the country transport period; include the full three-stage lead in both lead demand and the new-goods expected-sellable date. Calculate fixed head freight, handling cost, and known savings from settlement economics. Apply the current user-approved head-freight policy of CNY 1 per unit to every country, site, and SKU.
8. Keep quantity recommendations fail-closed when exact identity, inventory, demand, country lead-time policy, or the approved fixed-freight policy is unavailable. Dimensions, weight, cost, and profitability are presentation or execution-readiness evidence; they must not hide a SKU or block an otherwise valid demand-and-inventory quantity recommendation.
9. Update the local dashboard without external business writes.
10. Verify each country, SKU main image, source evidence, blockers, and batch totals in a browser.
11. Run focused and full relevant tests, commit on the authorized branch, and do not push unless requested.

## Preserve recent demand coverage

- Keep every exact SKU with positive `recent30Units` on the dashboard, including SKUs with zero Seaya stock and SKUs whose logistics fields are incomplete.
- Sum recent demand across READY TikTok and Shopee facts only within the same country.
- Provide a dedicated recent-30-day filter.
- Render existing-stock replenishment and first-stock recommendations in one SKU ledger. Distinguish them with `REPLENISH` and `FIRST_STOCK` labels and shared filters; do not split them into separate tables.
- Provide a four-country summary view that first calculates each country independently and then includes only rows whose recommended quantity is strictly greater than 10. Preserve the country identity on every row; never aggregate the same SKU across countries.
- When logistics fields are incomplete, display the SKU, image, demand, inventory and calculated quantity. Mark only the affected output as pending: dimensions affect volume, weight affects local handling and net benefit, and cost affects working capital.
- Do not use known benefit as a readiness gate. Display positive or negative benefit alongside the demand-and-inventory recommendation.

## Separate quantity from economics

- Treat paid or platform-confirmed orders as demand only when their event time, country, channel, exact SKU, quantity, and lifecycle status are auditable.
- Exclude unpaid, cancelled, fraudulent, failed, and test orders. Track refunds and returns separately; apply an explicit approved adjustment instead of waiting for settlement.
- Use order creation/payment/confirmation event time for 7/15/30-day demand windows. Never use settlement, escrow release, payout, or remittance time for replenishment velocity.
- Calculate quantity as `eligible order demand + Seaya available/trusted inbound`. Settlement facts must not contribute units to this calculation.
- Calculate savings from settlement facts such as customer paid amount, tax, platform deductions, and observed cross-border freight. Order facts must not fabricate settled money.
- Persist `quantity_basis=valid_order` and `economics_basis=settlement` with independent source identity, captured-at time, window, completeness, and digest.
- When a current channel has settlement data but no eligible order snapshot, keep its economics visible and set quantity to `BLOCKED_ORDER_DATA`; do not present settlement-derived quantities as recommendations.

## Forecast volatile demand

- Calculate TikTok and Shopee independently, then sum their approved daily velocities at exact country + SKU.
- When exact daily eligible-order facts exist, use three non-overlapping windows: last 7 days at 60%, days 8–15 at 30%, and days 16–30 at 10%.
- Divide by verified sellable days when an audited stockout calendar exists. Otherwise divide by calendar days and disclose `calendar_days_no_stockout_adjustment`; never invent in-stock days.
- Classify a 30-day series as `SPIKE` when it has at least 5 units and either no more than 2 active sales days or one day contributes at least 60% of units.
- For a `SPIKE` first-stock SKU, use 15 arrival-coverage days instead of the normal country target. Do not apply this first-stock guard to existing local stock.
- Use the segmented trend only when its exact window units reconcile with `recent30Units`. Otherwise fail over to the declared 30-day + long-window or long-window method and surface the fallback.
- Preserve method, weights, window units, denominator basis, velocity, trend class, confidence, and spike-protection days in the aggregate contract.

## Maintain manual completion

- Allow manual entry only for missing package dimensions, weight, unit cost, and an optional source note.
- Require finite positive numbers; reject booleans, strings, zero, negative, `NaN`, and infinity.
- Store manual values only in browser-local reversible storage unless the user explicitly authorizes another destination.
- Do not use logistics entry to resolve an identity or alias blocker.
- Show the manual source and allow clearing the override.
- Never require these manual fields before calculating a quantity. They are required only before relying on the affected volume, handling, benefit, or capital output.

## Time-phase inbound supply

- Never treat `inbound` as available on the snapshot date.
- For the newly recommended shipment, use `new_replenishment_lead_days = 3 preparation days + 4 domestic-warehouse days + country transport days`. Apply the same full lead to demand consumption and the displayed new-goods expected-sellable date. Do not add the preparation or domestic-warehouse stages to existing Seaya inbound batches whose effective anchor is already the domestic-warehouse event or its explicit creation-plus-four-day fallback.
- Prefer the exact Seaya operation-log event `已入库（Reach the domestic warehouse）` as the batch clock. When it is absent, retain status `NOT_YET_INBOUND` and use the user-approved estimate `created_at + 4 days`; never label that fallback as actual inbound.
- Estimate `expected_sellable_at = effective_anchor_at + country_transport_days`, where effective anchor is actual inbound first and the explicit creation-plus-four-day estimate second. The user cancelled the former 2-day sign-and-shelve buffer. Keep verification, loading, customs, sign, and shelving dates as evidence only; none may silently add an unapproved buffer.
- Improve country transport days from complete historical Seaya batches using the pure `transport_history.derive_transport_policy` rule. Exclude supplier-marked abnormal rows and rows without a named first-mile carrier. Require at least five eligible completed batches. Use nearest-rank P80 of `created_at -> signed_at`, subtract the approved 3 preparation and 4 domestic-warehouse days, and never lower the approved baseline unless a later explicit country-level business approval overrides the model. Persist sample count, sorted observed total days, P80, derived days, effective days, source capture time, state, and approval reference. With fewer than five samples, use `FALLBACK_INSUFFICIENT_SAMPLE`. Current Thailand policy is the user-approved 15-day transport override and 33-day target coverage; retain the historical 20-day transport result as advisory evidence only.
- Consume current available stock until each inbound event's expected sellable date, add only that event's quantity on that date, then continue consuming demand until the new replenishment arrival date.
- Emit the same auditable projection trace for every country and SKU: each consumption interval records dates, days, rounded demand, stock before/after, and unmet demand; each arrival records exact batch identity, date, quantity, and stock before/after. The dashboard must never special-case one SKU for explanation.
- Exclude an inbound event from stock projected at the new replenishment arrival when its expected sellable date is later than that arrival.
- Bind every inbound event to one complete batch identity and one exact SKU quantity. A SKU may have multiple simultaneous inbound events with different dates.
- Read every page of each inbound-batch detail. Repeated exact SKU rows may represent separate boxes; sum all eligible rows within that same complete batch before reconciling the SKU aggregate. Never stop at the first page.
- Require the sum of exact batch-SKU quantities to equal the SKU's aggregate `inbound`. If a multi-batch allocation is missing, non-integer, or does not reconcile, fail closed: display the unmatched quantity but do not count it as supply. Never collapse it onto one SKU-level date.
- When exactly one active inbound batch exists for a country, the SKU aggregate may be bound to that sole batch with explicit `SINGLE_ACTIVE_BATCH` lineage.
- Allow a per-country + exact-batch manual expected-sellable-date override with an optional source note. The override applies to every SKU line in that batch, persists only in reversible browser `localStorage`, can be cleared, and never writes to Seaya or a database.
- Maintain batch ETA confirmation on the dedicated `dashboard/inbound-batches.html` page. Keep the SKU replenishment ledger read-only for ETA editing: show effective batch dates and link to the confirmation page instead of embedding per-row date controls.

## Keep the skill synchronized

Treat dashboard calculations, fields, policies, normalization, demand-source behavior, this skill, and its references as one change set.

The installed Codex skill must be a directory junction to this repository folder, created by `scripts/install_local_skill.ps1`. Do not maintain a copied installation: the junction makes committed source updates immediately visible to Codex. Refuse to overwrite an unrelated existing skill path.

After any such update:

1. Update `SKILL.md` and the relevant reference.
2. Run:

```powershell
python scripts/verify_dashboard_sync.py --update
python scripts/verify_dashboard_sync.py --check
```

3. Run `scripts/install_local_skill.ps1` to verify the linked installation still targets this canonical source.
4. Commit the dashboard and skill changes together.

Do not declare the feature complete when the sync check fails.

## Preserve safety

- Default to read-only operations.
- Never log, display, persist, or commit credentials, tokens, cookies, or raw sensitive responses.
- Persist only redacted SKU aggregates in dashboard source. Keep order numbers and raw escrow responses in ignored local runtime files.
- Do not use production mutations to test inventory logic.
- Report network reads, auth writes, business writes, rollback, unknown outcomes, and external writes separately.
- Distinguish captured facts, user-approved policy, conservative assumptions, and blockers.
