# Domain ownership

This document defines the current ownership boundaries. It was introduced in
Phase 1 without moving production logic. The compatibility inventory below is
historical context and must be re-verified against the current work order; it
does not by itself declare a capability ready or authorize execution.

## Domain ownership

| Domain | Owns | Current legacy locations | Primary hand-off |
| --- | --- | --- | --- |
| Product operations | Product master data, SKU identity, intake, package approval | `modules/catalog`, `modules/sourcing/new_product_workbench` | `ApprovedProductPackage` |
| Content operations | Copy, images, future video | `modules/products/titles`, `modules/products/images`, `modules/sourcing/image_workbench` | `ContentPackage` |
| Channel operations | TikTok, Shopee, Ozon, Miaoshou publishing; affiliate outreach, price, promotion, deactivation | `modules/ozon`, `modules/shopee`, `modules/miaoshou`, `modules/affiliate` | `ChannelListing` |
| Supply-chain operations | Supplier sources, Yacang/Seaya, inventory, receiving, replenishment | New domain; `modules/catalog/logistics_weights` is the first legacy adapter | `InventorySnapshot` |
| Data operations | Cost, settlement, profit, ads, analytics | `modules/finance`, `modules/ads`, `modules/pricing`; legacy compatibility adapter `modules/products/costs` | `FinancialFact` |

## Shared platform

`shared_platform` owns cross-domain contracts, approval/audit conventions,
jobs, notifications, health interfaces, and the registration seam. Its stable
contracts are `ProductRecord`, `ApprovedProductPackage`, `ContentPackage`,
`ChannelListing`, `InventorySnapshot`, `FinancialFact`, and `ApprovalRecord`.
They are immutable Python dataclasses and intentionally independent of SQLite
rows, HTTP payloads, credentials, and channel clients.

`FinancialFact` keeps `product_id`, `sku_id`, `channel`, and `region` as
separate optional identities. Adapters must not put a SKU into `product_id` or
a market region into `channel` merely because a source row lacks the other
field.

Existing table ownership is assigned as follows: product operations owns
`products`; channel operations owns `shops`, `shopee_shops`,
`shopee_products`, and `affiliate_invites`; supply-chain operations owns
`purchasing_links`, `sku_logistics_weights`, and future warehouse/inventory
extensions; data operations owns `sku_costs`, `settlement_lines`,
`ad_spend_daily`, and `product_analytics`; shared platform owns future
approval, audit, jobs, notification, and health tables. Cross-domain code must
exchange a stable contract or call an explicitly documented adapter, not query
another domain's table directly.

For one canonical internal SKU, one effective time/range, one currency, and
one cost basis, only one active authoritative current-cost fact is allowed.
Multiple sources enter reconciliation or a versioned supersession; they do not
become parallel current costs. Product operations consumes the cost contract
and must not copy or recompute the data-domain cost truth. Platform listing
identities, country warehouse balances, settlement lines, and historical cost
versions remain separately sourced facts.

## Entry-point compatibility

`main.py` publishes `CLI_DOMAIN_REGISTRY`; `modules.products.server` publishes
`HTTP_DOMAIN_REGISTRY`. Both derive from `shared_platform.registry`. They do
not replace `argparse` or the existing `Handler`, so all paths, ports, and
commands remain unchanged. The current registry has ownership metadata only;
no live shop, channel, or production-data action is introduced by it.

The current `sourcing` CLI, `/sourcing`, `/api/sourcing`, and port 8766
Treasury workflow are legacy cross-domain orchestration hotspots. Product
operations is their temporary primary owner because it owns the end-to-end
intake and approval workflow. Content-, channel-, and supply-chain-specific
steps must be extracted behind contracts rather than editing this shared
workbench concurrently. Supply-chain operations intentionally has no exclusive
CLI or HTTP entry point in Phase 1; its first deliverable is a read-only Seaya
inventory adapter.

## Parallel delivery and integration

The five domains are logical ownership boundaries, not five permanent tasks.
The current work order and coordination state identify the writer, exact source,
allowed modules and producer/consumer hand-off. Cross-domain contract changes
receive the assigned integrator review; this does not add a new user approval
for implementation already authorized by that work order.

Current task assignments, Work Order lifecycle, single-writer rule, UI
acceptance split, and external-write authority are normative in
[`THREAD_OPERATING_MODEL.md`](THREAD_OPERATING_MODEL.md) and
[`pm/DISPATCH_CONVENTION.md`](pm/DISPATCH_CONVENTION.md).

The legacy A2A, EigenFlux, and multi-agent systems are retained as code for
compatibility but are disabled by default for this architecture. They are not
required to start a domain workflow and must not become a cross-domain runtime
dependency without a current Work Order, named owner, isolated scope, and
single-writer constraint. A current explicit delegation does not require a
second approval merely because these historical systems remain disabled.

## Historical Phase 1 extraction backlog

The following items record the original Phase 1 direction. They are not an
active task list; a current Work Order and present code evidence are required
before reopening one.

1. Add read adapters from existing SQLite rows and payloads to the contracts.
2. Move one command or route at a time behind a domain-owned adapter while
   preserving its legacy entry point.
3. Introduce platform-owned migrations for approval, audit, job, notification,
   and health tables only after a contract consumer exists.
4. Replace registry metadata with explicit domain routers after route-level
   regression coverage is in place.
