# Profit report contract

## Inclusion

Include only rows normalized to `settlement_status=settled`. Use the settlement/release date for the reporting period. Do not infer settlement from order creation, shipment, delivery, or completion alone.

## Formula

`profit_cny = net_settlement_cny - product_cost_cny - advertising_cny - external_costs_cny`

Net settlement may already include commissions, transaction fees, platform logistics, refunds, taxes, and adjustments. Preserve all fee lines for display, but subtract a fee again only when `included_in_net_settlement=false`.

## Required order-line evidence

- platform, shop, region, order and order-line identities
- platform SKU, seller SKU, canonical cost SKU, product/variant names, main image
- quantity, unit/package/billable weight and weight source when available
- occurred/settled timestamps and settlement status
- buyer-paid product amount, net settlement, currency
- every source fee code/label/amount and net-settlement inclusion flag
- unit and total product cost, cost version/effective time/source/snapshot
- advertising amount, mode, basis/source/as-of/snapshot/allocation version
- FX rate/source/as-of/snapshot
- external costs, profit, source snapshot identity, quality issues

## Status

- `ready`: all included lines have settlement, positive quantity, cost, FX, and required advertising evidence.
- `needs_review`: any required evidence is missing or invalid. Never approve this state.

Operator-approved cost assumptions are non-blocking warnings, not silent catalog facts. Under `temporary-cost-policy/default-5-conflict-high/v1`, a missing positive unit cost becomes CNY 5 and conflicting positive costs resolve to the highest candidate. The report must retain selected value, candidates, affected canonical SKU, and policy version.

Rows rejected for missing cost, SKU mapping, quantity, FX, settlement, or advertising evidence must not contribute numeric profit. Any aggregate over the remaining calculated rows is a partial diagnostic total, not a complete period result. Report calculated and rejected row counts and identify the blocking seller SKU whenever it is known.

Weekly TikTok/Shopee reports use `realized_settlement_with_estimated_ads`. Their default advertising fraction is `0.22`; an explicit platform/region input may override it. Monthly TikTok/Shopee reports use `realized_settlement_with_actual_ads`. Current Ozon weekly and monthly V1 reports use `realized_settlement_with_estimated_ads` with fixed `0.22` policy. Every estimated payload must retain both the rate and buyer-paid product basis so it cannot be confused with actual advertising spend.

Order lines are sorted by settlement timestamp descending, with order ID and order-line ID as deterministic ascending tie-breakers. HTML is a display projection: show every platform fee component as an independent column and format all price/cost values to exactly two decimal places. JSON remains the audit artifact and retains full Decimal precision.

## Knowledge

Store only explicitly approved monthly reports. Keep immutable JSON artifacts under platform/year/month plus a local index. Reject secret/raw-response fields. Corrections produce a new report and approval; never overwrite an approved artifact.
