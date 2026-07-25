# Domain ownership (Phase 1)

This document introduces code ownership boundaries without moving production
logic. Existing CLI names, HTTP paths, ports, SQLite tables, and integrations
remain behavior-compatible. The registry in `shared_platform.registry` is the
temporary adapter seam; it describes ownership but does not dispatch calls.

## Domain ownership

| Domain | Owns | Current legacy locations | Primary hand-off |
| --- | --- | --- | --- |
| Product operations | Product master data, SKU, intake, package approval | `modules/catalog`, `modules/products/costs`, `modules/sourcing/new_product_workbench` | `ApprovedProductPackage` |
| Content operations | Copy, images, future video | `modules/products/titles`, `modules/products/images`, `modules/sourcing/image_workbench` | `ContentPackage` |
| Channel operations | TikTok, Shopee, Ozon, Miaoshou publishing; affiliate outreach, price, promotion, deactivation | `modules/ozon`, `modules/shopee`, `modules/miaoshou`, `modules/affiliate` | `ChannelListing` |
| Supply-chain operations | Supplier sources, Yacang/Seaya, inventory, receiving, replenishment | New domain; `modules/catalog/logistics_weights` is the first legacy adapter | `InventorySnapshot` |
| Data operations | Cost, settlement, profit, ads, analytics | `modules/finance`, `modules/ads`, `modules/pricing` | `FinancialFact` |

## Shared platform

`shared_platform` owns cross-domain contracts, approval/audit conventions,
jobs, notifications, health interfaces, and the registration seam. Its stable
contracts are `ProductRecord`, `ApprovedProductPackage`, `ContentPackage`,
`ChannelListing`, `InventorySnapshot`, `FinancialFact`, and `ApprovalRecord`.
They are immutable Python dataclasses and intentionally independent of SQLite
rows, HTTP payloads, credentials, and channel clients.

Existing table ownership is assigned as follows: product operations owns
`products`; channel operations owns `shops`, `shopee_shops`,
`shopee_products`, and `affiliate_invites`; supply-chain operations owns
`purchasing_links`, `sku_logistics_weights`, and future warehouse/inventory
extensions; data operations owns `sku_costs`, `settlement_lines`,
`ad_spend_daily`, and `product_analytics`; shared platform owns future
approval, audit, jobs, notification, and health tables. Cross-domain code must
exchange a stable contract or call an explicitly documented adapter, not query
another domain's table directly.

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

Five Codex workstreams map one-to-one to the five domains. Each workstream may
change only its owned modules plus a contract proposal and tests. The CEO/
integrator owns `shared_platform`, schema migration ordering, contract version
approval, cross-domain dependency resolution, and final integration testing.
No domain may change another domain's tables or public CLI/HTTP entry points
without an approved adapter and an integrator review.

The legacy A2A, EigenFlux, and multi-agent systems are retained as code for
compatibility but are disabled by default for this architecture. They are not
required to start a domain workflow and must not become a cross-domain runtime
dependency without an explicit future approval.

## Next extraction steps

1. Add read adapters from existing SQLite rows and payloads to the contracts.
2. Move one command or route at a time behind a domain-owned adapter while
   preserving its legacy entry point.
3. Introduce platform-owned migrations for approval, audit, job, notification,
   and health tables only after a contract consumer exists.
4. Replace registry metadata with explicit domain routers after route-level
   regression coverage is in place.
