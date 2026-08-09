# Decision contract

## Inventory identity

Every consumed record must contain:

- `seller_sku`: complete, nonempty built-in string;
- `warehouse`: complete country-specific warehouse code;
- `stock`, `available`, `allocated`, `frozen`, `inbound`: built-in integers, not booleans, each at least zero;
- `captured_at`;
- source/snapshot identity and revision or digest when available.

Reject clipped or masked identifiers. Keep excluded records outside inventory totals until their complete identities are recovered.

When both a canonical four-digit row and its approved country-prefixed alias exist in the same warehouse, sum their quantity fields into one canonical inventory position and preserve both complete source aliases. Never merge across warehouses or countries.

An alias mapping must contain the complete source SKU, complete canonical SKU, country, warehouse, evidence source, and approval/reference. Suffix stripping and title matching are not alias evidence.

Current user-confirmed Seaya PH8807 mappings:

| Complete source SKU | Canonical SKU | Available |
|---|---|---:|
| 770820 | 0820 | 0 |
| 770821 | 0821 | 2 |
| 770822 | 0822 | 0 |

## Demand

Calculate per country and exact SKU. TikTok and Shopee may share one local warehouse within a country, but inventory and demand never cross countries. A channel fact whose declared source country differs from the decision country is ineligible. Cross-country templates may contribute presentation metadata only; discard their demand, inventory, warehouse binding, and aliases.

Use two immutable evidence ledgers:

- `quantity_basis=valid_order`: eligible paid or platform-confirmed order lines, using the order event time. Each fact carries country, channel, exact SKU, ordered units, lifecycle filter, window, captured-at time, completeness, source identity, and digest.
- `economics_basis=settlement`: settled customer payment, tax, fees, and observed cross-border freight, using the settlement event time. Each fact carries its own window, captured-at time, completeness, source identity, and digest.

Never substitute one ledger for the other. Settlement, escrow-release, payout, and remittance dates are ineligible for 7/15/30-day replenishment velocity because their lag distorts recent demand. Order facts are ineligible for realized savings unless the corresponding monetary fields are settled. If a current TikTok or Shopee channel has no complete eligible order snapshot, return `BLOCKED_ORDER_DATA` for quantity while retaining independently valid settlement economics.

Eligible order status policy:

- include paid or platform-confirmed orders, including ready-to-ship, shipped, and completed;
- exclude unpaid, cancelled, fraudulent, failed, and test orders;
- keep refunds and returns as a separate adjustment with explicit lineage; do not silently wait for settlement or subtract an undocumented rate.

The combined MY/TH/VN/PH summary is a projection over completed country decisions, not a new inventory calculation. Include a row only when its country-specific `recommended` value is a built-in positive number strictly greater than 10. Keep country + SKU as the row identity; do not merge quantities for matching SKUs across countries.

When recent 30-day demand exists:

`v_channel = 70% × recent_30_day_daily + 30% × longer_window_daily`

Otherwise:

`v_channel = longer_window_units / longer_window_days`

The two formulas above are fallback methods when exact daily segmentation is unavailable. When exact daily facts exist and reconcile to the 30-day total:

- `r7 = units age 0–7 / verified sellable days or 7 calendar days`
- `r8_15 = units age 8–15 / verified sellable days or 8 calendar days`
- `r16_30 = units age 16–30 / verified sellable days or 15 calendar days`
- `v_channel = 60% × r7 + 30% × r8_15 + 10% × r16_30`

Calculate each channel independently. A valid trend decision carries method `segmented_7_8_15_v1`, exact non-overlapping units, denominator days and basis, daily velocity, 30-day forecast, `RISING|STABLE|FALLING|SPIKE`, confidence, active sales days, maximum daily units, and optional spike protection.

`SPIKE` requires at least 5 units and either at most 2 active sales days or a maximum day representing at least 60% of the 30-day units. For first-stock only, `SPIKE` changes arrival target coverage to 15 days. It does not change the lead-time demand calculation.

`v = sum(READY channel velocities)`

Unavailable or pending channels remain visible and contribute no fabricated units. Use `PENDING_REFRESH` when the shop mapping and refresh token exist but the access token is expired and refresh/pull has not run. Reserve `BLOCKED_AUTH` for genuinely missing or rejected authorization.

Every exact SKU with a positive `recent30Units` value in at least one READY channel must remain visible in the dashboard. Missing dimensions, weight, or cost must not remove the demand row or block the quantity.

For Shopee settlement economics, a complete pull means every listed settlement page and every escrow detail succeeded for the declared window. Aggregate discounted customer payment and allocated actual shipping fee by exact SKU, but never feed its `quantity_purchased` or release timestamp into replenishment demand. Approved channel aliases are only four digits, `77+four digits`, and `99+four digits`. Any other model SKU must resolve through the exact Shopee `(item_id, model_id)` catalog key. Unresolved lines remain excluded evidence and must never be matched by title or image similarity.

## Quantity

Quantity is executable only when every in-scope channel is backed by a complete `quantity_basis=valid_order` snapshot. Settlement-only history may be displayed as legacy context but cannot produce a recommended quantity.

- `lead_demand = ceil(v × lead_days)`
- Project supply chronologically: consume available stock to the first inbound `expected_sellable_at`, add that inbound quantity, then repeat until the new replenishment arrival.
- `arrival_stock = floor(time_phased_projected_stock_at_new_replenishment_arrival)`
- `target = ceil(v × (target_days + safety_days))`
- `recommended = max(0, target - arrival_stock)`

Every inbound event must retain a complete nonempty `batch_id`, quantity, complete country + SKU identity, shipment anchor date/type, estimated sellable date, estimate policy, source capture time, and manual-override lineage when present. `标记发货` is the preferred anchor. Creation date is a labeled fallback and must never be presented as an actual shipment date. One SKU may have multiple events when it belongs to multiple batches.

For each country + SKU, `sum(batch_sku_quantity)` must equal the Seaya aggregate `inbound`. Batch identity and quantity must be exact built-in values; null, boolean, string, negative, masked, or clipped values are ineligible. If a multi-batch allocation is incomplete or does not reconcile, show the unmatched aggregate but count zero of it in projected supply. Never assign a multi-batch aggregate to one latest date. A sole active batch may consume the aggregate only with explicit `SINGLE_ACTIVE_BATCH` lineage.

A manual override is keyed by country + complete `batch_id`, not SKU. It changes only the local decision projection, applies consistently to every SKU in the batch, and never changes the supplier record or another batch.

Current estimated-sellable policy uses the approved country transit days plus a 2-day sign-and-shelve buffer. A creation-date fallback remains an estimate. Missing multi-batch SKU allocation is a reconciliation blocker, not permission to invent one aggregate arrival date.

Quantity is valid without dimensions, weight, or unit cost. Those fields affect separate outputs:

- missing dimensions → batch and unit volume are `PENDING_DATA`;
- missing weight → local handling and net benefit are `PENDING_DATA`;
- missing unit cost → working capital is `PENDING_DATA`.

Known portions may be shown with an explicit pending-item count, but a partial amount must never be presented as the complete batch total.

| Country | Lead | Target | Safety | Mode |
|---|---:|---:|---:|---|
| MY | 25 days | 30 days | 5 days | West Malaysia sea |
| TH | 15 days | 30 days | 3 days | Thailand land |
| VN | 15 days | 30 days | 3 days | Vietnam south land, conservative until warehouse city confirmed |
| PH | 25 days | 30 days | 5 days | Manila sea |

## Economics

- Apply the user-approved fixed head freight of CNY 1 per unit to every country, site, and SKU.
- Preserve physical and chargeable volume for packing and shipment planning only; do not use it to calculate the dashboard's head-freight amount.
- Do not use positive known unit advantage as a recommendation gate. Show positive or negative benefit without hiding the SKU or changing an otherwise valid demand-and-inventory recommendation.
- Apply tax savings only where the user approved a country rate.
- Use 20% of observed SKU-level cross-border shipping as the shipping saving; unavailable settlement freight contributes no fabricated saving.
- Treat 0–30 day Seaya storage as zero under the supplied tariff, and include outbound, shelving, packaging, and head freight.

## Dashboard

The dashboard is a local decision artifact:

`domains/supply_chain_operations/dashboard/index.html`

Render `REPLENISH` and `FIRST_STOCK` rows in one sortable/filterable SKU ledger. The status label preserves the inventory distinction; separate tables must not fragment the recommendation view.

It may write reversible manual logistics overrides to browser `localStorage`. It must not write Seaya, TikTok, Shopee, order systems, or business databases.
