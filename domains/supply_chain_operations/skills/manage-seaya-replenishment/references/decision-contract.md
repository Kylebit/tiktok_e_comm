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

`v = sum(READY channel velocities)`

Unavailable or pending channels remain visible and contribute no fabricated units. Use `PENDING_REFRESH` when the shop mapping and refresh token exist but the access token is expired and refresh/pull has not run. Reserve `BLOCKED_AUTH` for genuinely missing or rejected authorization.

## Quantity

- `lead_demand = ceil(v × lead_days)`
- `arrival_stock = max(0, available + trusted_inbound - lead_demand)`
- `target = ceil(v × (target_days + safety_days))`
- `recommended = max(0, target - arrival_stock)`

| Country | Lead | Target | Safety | Mode |
|---|---:|---:|---:|---|
| MY | 25 days | 30 days | 5 days | West Malaysia sea |
| TH | 15 days | 30 days | 3 days | Thailand land |
| VN | 15 days | 30 days | 3 days | Vietnam south land, conservative until warehouse city confirmed |
| PH | 25 days | 30 days | 5 days | Manila sea |

## Economics

- Use chargeable volume = max(physical volume, weight-equivalent volume).
- Apply the lane's minimum billable volume and surcharge to the final approved batch, not all demand-gap candidates.
- Iterate the approved set until every retained SKU has positive known unit advantage after allocated head freight and local handling.
- Apply tax savings only where the user approved a country rate.
- Use 20% of observed SKU-level cross-border shipping as the shipping saving; unavailable settlement freight contributes no fabricated saving.
- Treat 0–30 day Seaya storage as zero under the supplied tariff, and include outbound, shelving, packaging, and head freight.

## Dashboard

The dashboard is a local decision artifact:

`domains/supply_chain_operations/dashboard/index.html`

It may write reversible manual logistics overrides to browser `localStorage`. It must not write Seaya, TikTok, Shopee, order systems, or business databases.
