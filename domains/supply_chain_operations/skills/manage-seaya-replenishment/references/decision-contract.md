# Decision contract

## Inventory identity

Every consumed record must contain:

- `seller_sku`: complete, nonempty built-in string;
- `warehouse`: complete country-specific warehouse code;
- `stock`, `available`, `allocated`, `frozen`, `inbound`: built-in integers, not booleans, each at least zero;
- `captured_at`;
- source/snapshot identity and revision or digest when available.

Reject clipped or masked identifiers. Keep excluded records outside inventory totals until their complete identities are recovered.

An alias mapping must contain the complete source SKU, complete canonical SKU, country, warehouse, evidence source, and approval/reference. Suffix stripping and title matching are not alias evidence.

Current user-confirmed Seaya PH8807 mappings:

| Complete source SKU | Canonical SKU | Available |
|---|---|---:|
| 770820 | 0820 | 0 |
| 770821 | 0821 | 2 |
| 770822 | 0822 | 0 |

## Demand

Calculate per country and exact SKU. TikTok and Shopee may share one local warehouse within a country, but inventory and demand never cross countries.

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

For Shopee settlement demand, a complete pull means every listed order page and every escrow detail succeeded for the declared window. Aggregate `quantity_purchased`, discounted customer payment, and allocated actual shipping fee by exact SKU. Approved channel aliases are only four digits, `77+four digits`, and `99+four digits`. Any other model SKU must resolve through the exact Shopee `(item_id, model_id)` catalog key. Unresolved lines remain excluded evidence and must never be matched by title or image similarity.

## Quantity

- `lead_demand = ceil(v × lead_days)`
- `arrival_stock = max(0, available + trusted_inbound - lead_demand)`
- `target = ceil(v × (target_days + safety_days))`
- `recommended = max(0, target - arrival_stock)`

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
