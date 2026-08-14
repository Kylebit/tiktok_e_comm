# Profit report contract

## Inclusion

Include only rows normalized to `settlement_status=settled`. Use the settlement/release date for the reporting period. Do not infer settlement from order creation, shipment, delivery, or completion alone.

## Formula

`profit_cny = net_settlement_cny - product_cost_cny - advertising_cny - external_costs_cny`

Net settlement may already include commissions, transaction fees, platform logistics, refunds, taxes, and adjustments. Preserve all fee lines for display, but subtract a fee again only when `included_in_net_settlement=false`.

When multiple settlement facts in the same reporting period refer to the same order line, consolidate them only if platform/SKU/quantity/currency identity agrees and at most one fact has a positive buyer-paid advertising basis. Sum net settlement and fee facts, preserve every fact ID and timestamp, and charge product cost plus estimated advertising once. Conflicting identity, quantity, currency, or multiple positive advertising bases is blocking; never make duplicate line IDs unique while silently charging cost twice.

## Required order-line evidence

- platform, shop, region, order and order-line identities
- platform SKU, seller SKU, canonical cost SKU, product/variant names, main image
- quantity, unit/package/billable weight and weight source when available
- official order-created/occurred timestamp when available, settled timestamp, and settlement status; TikTok order creation and Finance statement settlement remain distinct, and Shopee order creation and escrow release remain distinct
- product sales amount after seller discount, buyer cash-paid product amount, net settlement, currency; for Shopee these are distinct because Shopee-funded vouchers reduce buyer cash without reducing seller product sales
- every source fee code/label/amount and net-settlement inclusion flag
- unit and total product cost, cost version/effective time/source/snapshot
- advertising amount, mode, basis/source/as-of/snapshot/allocation version
- live FX rate/source/provider as-of/snapshot; one immutable snapshot per report run
- external costs, profit, source snapshot identity, quality issues
- Shopee fulfillment mode and official import-tax evidence; for local orders, the allocated combined CNY shipping-and-warehouse cost plus its per-parent-order policy
- TikTok official `fulfillment_type` and its order-detail evidence source; retain supporting delivery/shipping/warehouse fields when returned

## Status

- `ready`: all included lines have settlement, positive quantity, cost, FX, and required advertising evidence.
- `needs_review`: any required evidence is missing or invalid. Never approve this state.

Operator-approved cost assumptions are non-blocking warnings, not silent catalog facts. Under `temporary-cost-policy/default-5-conflict-high/v1`, a missing positive unit cost becomes CNY 5 and conflicting positive costs resolve to the highest candidate. The report must retain selected value, candidates, affected canonical SKU, and policy version.

Rows rejected for missing cost, SKU mapping, quantity, FX, settlement, or advertising evidence must not contribute numeric profit. Any aggregate over the remaining calculated rows is a partial diagnostic total, not a complete period result. Report calculated and rejected row counts and identify the blocking seller SKU whenever it is known.

Weekly reports use `realized_settlement_with_estimated_ads`. Each platform defaults to `0.22` in the unified `report-policy.json`; an explicit global or platform input may override it. Shopee weekly advertising uses official `order_discounted_price` (product sales after seller discount, before Shopee-funded voucher), not buyer cash-paid amount; retain both values and their lineage. Monthly TikTok/Shopee reports use `realized_settlement_with_actual_ads`. Current Ozon weekly and monthly V1 reports use `realized_settlement_with_estimated_ads`, default to `0.22`, and accept an explicit operator override. Every estimated payload must retain the rate, product-sales/buyer-paid basis, policy version, and whether the value came from unified policy configuration, a global override, or a platform override so it cannot be confused with actual advertising spend.

For Shopee, classify fulfillment with site-specific official settlement evidence. For MY, a present non-zero `sales_tax_on_lvg` charge is cross-border and a present zero value is local. For VN, a present non-zero `vat_on_imported_goods` charge is cross-border and a present zero value is local. Every PH order is cross-border. For TH, both import VAT and import duty must be present and non-zero for cross-border; if both fields are present but either is zero, classify local. Missing evidence required by the site rule is blocking. Local orders default to one combined CNY 4 shipping-and-warehouse cost per parent order. Allocate that cost deterministically across its item lines, retain the allocation in `local_fulfillment_cost_cny`, include it in `external_costs_cny`, and include the policy input in the idempotency fingerprint.

Order lines are initially sorted by settlement timestamp descending, with order ID and order-line ID as deterministic ascending tie-breakers. HTML is a display projection: let the order-created header toggle ascending and descending order while keeping missing times last and breaking ties by order ID/order-line ID; do not mutate the JSON audit order. Hide the Shopee order-line ID column in HTML, but retain it in JSON and in the HTML row sort metadata for audit, deduplication, and deterministic ties. Display settlement time, order-created time, and FX as-of time as `YYYY-MM-DD HH:MM:SS（UTC±HH:MM）`; keep their original timezone-aware values in JSON and keep the raw order-created value in the HTML sort attribute. Show only Seller SKU, put net settlement CNY, total product cost CNY, advertising CNY, combined local-fulfillment CNY, profit, and margin immediately after it, and put product name at the far right. Replace the standalone currency column with `联盟营销佣金(AMS)`, showing `order_ams_commission_fee` in local currency and CNY exactly once; omit that code from dynamic fee columns. Define affiliate-order share as settled parent orders whose report lines have a positive aggregate CNY AMS fee divided by all calculated settled parent orders; never count expanded item lines as separate orders. Retain numerator, denominator, non-affiliate count, exact share and AMS totals in JSON. Show the country/region code without the internal shop identifier, render a valid HTTPS main-image URL as an image, show the live CNY-per-local FX rate, provider and provider as-of time as independent columns, every other platform fee component as an independent column, FX rates at eight decimal places, and all price/cost values at exactly two decimal places. Wide tables expose synchronized horizontal scrollbars above and below the order rows. JSON remains the audit artifact and retains full Decimal precision plus FX snapshot identity.

Settlement reconciliation preserves the exact local-currency difference. An absolute difference no greater than `1e-12` is treated as Decimal allocation noise; any larger difference produces `settlement_reconciliation_mismatch`.

TikTok follows the same hidden order-line ID display contract as Shopee: omit the column from HTML, but retain it in JSON and HTML sort metadata.

## Knowledge

Store only explicitly approved monthly reports. Keep immutable JSON artifacts under platform/year/month plus a local index. Reject secret/raw-response fields. Corrections produce a new report and approval; never overwrite an approved artifact.
