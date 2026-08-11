---
name: manage-profit-settlement
description: Build, audit, approve, and retrieve detailed settled-order profit reports for TikTok, Shopee, or Ozon. Use when Codex must pull or adapt the latest settlement evidence, calculate weekly profit with an explicit or default advertising rate, calculate TikTok/Shopee monthly profit with actual advertising spend, apply the temporary Ozon 22% advertising rule, inspect order/SKU cost waterfalls, or store and query an explicitly approved monthly report in the local profit knowledge base.
---

# Manage Profit Settlement

Keep TikTok, Shopee, and Ozon execution independent. Never use one platform's settlement, advertising, SKU mapping, cursor, or result to complete another platform. Share only versioned cost and FX snapshots.

## Workflow

### Stage 1: official settlement evidence

1. Accept an explicit platform, site, inclusive start date, and inclusive end date. Confirm the period is closed in the site's reporting timezone.
2. Read [references/report-contract.md](references/report-contract.md) and the matching platform section in [references/platform-sources.md](references/platform-sources.md).
3. Pull and save settlement evidence before doing any profit calculation. Run `scripts/pull_settlement_evidence.py --platform PLATFORM --site SITE --start YYYY-MM-DD --end YYYY-MM-DD --project-root ROOT --output OUTPUT`. Pull only official settled/released finance records; reject pending, processing, cancelled, delivered-but-unsettled, or unknown states. For an expired TikTok access token, add `--allow-credential-refresh` only after explicit operator approval; refresh a disposable token copy and leave every production credential file unchanged.
4. Review the saved `settlement-evidence/v1` JSON and HTML with the user. Verify period/timezone, source counts, financial component names, detail failures, snapshot checksum, redaction, and `external_writes_performed=[]`. If the user requested only this first stage, stop here.
5. Keep API results in memory or an explicitly named redacted snapshot. Never run a legacy wrapper that cleans directories or overwrites CSV/HTML during a report preview.

### Stage 2: order-level profit

6. Build a versioned cost snapshot keyed by canonical seller SKU and one immutable FX snapshot for the whole run. Apply temporary cost policy `temporary-cost-policy/default-5-conflict-high/v1`: use CNY 5 per unit when no positive cost exists and select the highest positive value when catalog costs conflict. Retain an assumption warning on every affected report/SKU; never present either selection as catalog fact.
7. Adapt only reviewed `settlement-evidence/v1` artifacts. Run `scripts/build_weekly_from_evidence.py --evidence-dir EVIDENCE --project-root ROOT --output OUTPUT --start YYYY-MM-DD --end YYYY-MM-DD --ad-rate 0.22`. The script reads `shop.db` with SQLite `mode=ro`, requires a live FX source, and writes report artifacts only.
8. For TikTok/Shopee weekly reports, default the advertising fraction to `0.22` (22%) unless the user supplies an explicit platform/region override. Apply it to buyer-paid product amount after seller discount, excluding buyer shipping. Preserve the rate and basis as an estimate in every report; never label it as actual spend. Exclude an actual TikTok advertising adjustment from the weekly order-settlement basis before applying the estimate, and reconcile the exclusion separately so advertising is never deducted twice.
9. For TikTok/Shopee monthly reports, require actual advertising evidence. Prefer order attribution; otherwise allocate an auditable actual total by buyer-paid GMV. Never silently fall back to zero or the weekly rate.
10. Use fixed estimated advertising rate `0.22` for Ozon weekly and monthly V1 reports. Derive the advertising basis from positive `OperationAgentDeliveredToCustomer` product-sale components and label it `ozon-fixed-ad-rate/v1`; never label it actual spend. When the finance artifact contains only platform SKU and no quantity, `--allow-ozon-read-enrichment` may read `/v3/product/info/list` and `/v3/posting/fbs/get` to obtain seller SKU and fulfilled quantity. Retain only redacted mappings/counts, never raw responses or credentials.
11. Generate one independent JSON/HTML report per platform. Consolidate multiple same-period settlement facts for one order line only when product identity and quantity agree and no more than one fact carries a positive advertising basis; otherwise fail closed. Sum settlement and fee facts, charge product cost and estimated advertising once, and retain every source fact identity/time in JSON and HTML. Sort order lines by settlement timestamp descending; break equal timestamps deterministically by order ID and order-line ID. Follow the legacy order-profit table pattern: render order/product/quantity, buyer-paid amount, settlement, FX, unit and total cost, advertising basis/rate/amount, external cost, profit, margin, and every fee component as its own column. Show all price and cost values to two decimal places in HTML while retaining full Decimal precision in JSON. Review totals, every quality issue and assumption warning, negative-profit lines, fee inclusion semantics, source counts, settlement reconciliation, cost conflicts, and rejected rows.
12. Perform two audit passes from each JSON artifact. Recompute totals from order lines and confirm only settled rows were included. Treat absolute reconciliation differences up to `1e-12` local currency as Decimal allocation noise while retaining the exact difference and tolerance; larger differences remain blocking. Iterate with a failing fixture/test when an issue is reproducible.
13. Approve only a ready monthly report after explicit human confirmation. Use `scripts/profit_report.py approve-monthly`; never approve a weekly or needs-review report.

## Safety gates

- Treat platform reads as external reads, never writes. Require `external_writes_performed=[]` from an injected live adapter receipt.
- Default to no credential refresh and save a blocked receipt when a read credential is missing or expired. After explicit operator approval, TikTok may refresh only a disposable copy for that read run; record `credential_refresh_performed=true` and never overwrite the configured or fallback token source.
- Do not persist previews, mutate production databases, silently refresh credentials, send notifications, retry payments, or modify platform orders.
- Preserve raw fee names and normalized codes. Subtract only costs not already included in net settlement.
- Represent missing money as a quality issue, never numeric zero. The only current exception is the explicit temporary missing-cost policy, which uses CNY 5 and must emit an assumption warning.
- Keep approved knowledge artifacts immutable. Create a new report version for corrections.
- Do not store tokens, cookies, authorization headers, API keys, or raw responses in reports or knowledge artifacts.

## Iteration rule

Treat this skill as a versioned operating procedure. After each real report, capture reproducible adapter, status, fee, allocation, or reconciliation failures as synthetic/redacted fixtures; add a failing test; update the domain engine and the relevant reference; validate the skill; then rerun focused and full tests. Never encode a one-off order ID or marketplace response as a permanent rule.
