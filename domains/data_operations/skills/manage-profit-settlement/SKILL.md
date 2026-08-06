---
name: manage-profit-settlement
description: Build, audit, approve, and retrieve detailed settled-order profit reports for TikTok, Shopee, or Ozon. Use when Codex must pull or adapt the latest settlement evidence, calculate weekly profit with an explicit or default advertising rate, calculate monthly profit with actual advertising spend, inspect order/SKU cost waterfalls, or store and query an explicitly approved monthly report in the local profit knowledge base.
---

# Manage Profit Settlement

Keep TikTok, Shopee, and Ozon execution independent. Never use one platform's settlement, advertising, SKU mapping, cursor, or result to complete another platform. Share only versioned cost and FX snapshots.

## Workflow

1. Resolve one platform and one closed reporting period.
2. Read [references/report-contract.md](references/report-contract.md).
3. When obtaining current platform data, read the matching section in [references/platform-sources.md](references/platform-sources.md).
4. Pull only official settled/released finance records. Reject pending, processing, cancelled, delivered-but-unsettled, or unknown states.
5. Keep API results in memory or an explicitly named redacted snapshot. Never run a legacy wrapper that cleans directories or overwrites CSV/HTML during a report preview.
6. Build a versioned cost snapshot keyed by canonical seller SKU and one immutable FX snapshot for the whole run.
7. For TikTok/Shopee weekly reports, default the advertising fraction to `0.22` (22%) unless the user supplies an explicit platform/region override. Apply it to buyer-paid product amount after seller discount, excluding buyer shipping. Preserve the rate and basis as an estimate in every report; never label it as actual spend.
8. For TikTok/Shopee monthly reports, require actual advertising evidence. Prefer order attribution; otherwise allocate an auditable actual total by buyer-paid GMV. Never silently fall back to zero or the weekly rate.
9. For Ozon, require actual advertising on every included order in V1.
10. Generate the JSON report with `scripts/profit_report.py build`. Review totals, every quality issue, negative-profit lines, fee inclusion semantics, source counts, and reconciliation.
11. Perform a second audit pass from the JSON artifact. Recompute totals from order lines and confirm only settled rows were included. Iterate with a failing fixture/test when an issue is reproducible.
12. Approve only a ready monthly report after explicit human confirmation. Use `scripts/profit_report.py approve-monthly`; never approve a weekly or needs-review report.

## Safety gates

- Treat platform reads as external reads, never writes. Require `external_writes_performed=[]` from an injected live adapter receipt.
- Do not persist previews, mutate production databases, refresh credentials, send notifications, retry payments, or modify platform orders.
- Preserve raw fee names and normalized codes. Subtract only costs not already included in net settlement.
- Represent missing money as a quality issue, never numeric zero.
- Keep approved knowledge artifacts immutable. Create a new report version for corrections.
- Do not store tokens, cookies, authorization headers, API keys, or raw responses in reports or knowledge artifacts.

## Iteration rule

Treat this skill as a versioned operating procedure. After each real report, capture reproducible adapter, status, fee, allocation, or reconciliation failures as synthetic/redacted fixtures; add a failing test; update the domain engine and the relevant reference; validate the skill; then rerun focused and full tests. Never encode a one-off order ID or marketplace response as a permanent rule.
