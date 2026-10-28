# July profit page recovery evidence

## Scope and safety

- Exact base: `94ae7e2e3e8211d3cb855470986b0961d1dee4a4`.
- Isolated checkout: `C:\Users\Windows11\Desktop\Agent_PR\_codex_worktrees\05-profit-july-restore-20260908`.
- No finance database, marketplace API, settlement pull, FX refresh, production service, or supply-chain code was read or changed for this recovery.

## What the historical page was

The July 2026 pages were standalone HTML files rendered from immutable profit-report JSON by
`domains/data_operations/profit_settlement/render.py`. They were not the generic captured-review
workspace now served at `/profit`.

Git history retains the complete feature sequence:

- `5c4ac4f` introduced skill-based settled-profit reports.
- `187c71a` expanded order-level cost details.
- `825c7c0` added live FX lineage.
- `4da033a` added order-time sorting.
- `f8ec03f` separated Shopee sales from buyer cash paid.
- `cfbc1b9` through `6b0ee07` added country-specific Shopee fulfillment policies.
- `0f6e8df` made the footer total follow the visible date filter.
- `372fc4a` added buyer-paid GMV and profit margins.
- `ee185c5` added the four-site Shopee monthly estimated-profit builder.

The retained renderer still implements the reviewed interaction: order-created-at filtering and
sorting, daily order counts, synchronized top and body horizontal scrollbars, a sticky filtered
footer, two-decimal money, eight-decimal FX, main images, Seller SKU, buyer cash paid, detailed fee
columns, source identities, and product names at the far right. It hides order-line IDs for Shopee
and TikTok.

## What is missing

The former worktree path
`C:\Users\Windows11\Desktop\Agent_PR\_codex_worktrees\05-data-ops\artifacts\profit_reports_monthly`
no longer exists. A scoped filesystem search found no `*.monthly-profit.html` or corresponding July
Shopee report JSON under the current workspace. Git history contains the generators and Skill
changes but not the financial report artifacts. No approved `profit-knowledge-entry/v1` index was
found either.

Consequences:

- The page capability is recoverable exactly from source.
- The July monetary facts cannot be reconstructed or declared current without an original JSON
  artifact or a new, separately authorized settlement pull.
- Date chips in the old page are **order-created dates**, not settlement dates. A last chip of
  `2026-07-25` does not prove that July 26-31 had no settlements.

### Second-pass backup search

An additional exact-name, read-only search on 2026-09-08 checked only these known locations:

- `C:\Users\Windows11\Desktop\Agent_PR\_codex_worktrees\05-data-ops\artifacts\profit_reports_monthly`
- `C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm\artifacts\profit_reports_monthly`
- `C:\Users\Windows11\Desktop\Agent_PR\outputs\orbithive-u01-recovery-20260906\artifacts\profit_reports_monthly`
- `C:\Users\Windows11\Desktop\Agent_PR\_codex_worktrees\05-profit-july-restore-20260908\artifacts\profit_reports_monthly`

All four directories were absent. Exact recursive searches for
`shopee_*2026-07-01_2026-07-31.monthly-profit.*` and
`tiktok_*2026-07-01_2026-07-31.monthly-profit.*` under the formal checkout, recovery repository,
and current worktree set returned no files. No broader user-profile, credential, database, or
platform scan was performed.

## Reviewer locations

- Worktree: `C:\Users\Windows11\Desktop\Agent_PR\_codex_worktrees\05-profit-july-restore-20260908`
- Restored shelf HTML: `C:\Users\Windows11\Desktop\Agent_PR\_codex_worktrees\05-profit-july-restore-20260908\web\profit_archive.html`
- Evidence report: `C:\Users\Windows11\Desktop\Agent_PR\_codex_worktrees\05-profit-july-restore-20260908\docs\audits\2026-09-08-july-profit-page-recovery.md`
- English Skill: `C:\Users\Windows11\Desktop\Agent_PR\_codex_worktrees\05-profit-july-restore-20260908\domains\data_operations\skills\manage-profit-settlement\SKILL.md`
- Historical Shopee monthly generator source: commit `ee185c5`, path
  `domains/data_operations/skills/manage-profit-settlement/scripts/build_shopee_monthly_from_evidence.py`
- Historical detailed renderer evidence: commits `5c4ac4f`, `187c71a`, `825c7c0`, `4da033a`,
  `0f6e8df`, and `372fc4a`, path `domains/data_operations/profit_settlement/render.py`.

## Current gap

`/profit` currently opens `web/profit_center.html`, a generic captured-snapshot review screen. It can
render an already selected report, but it does not present the old report-first experience and does
not provide a simple local shelf for the previously generated monthly JSON files.

## Recovery plan

1. Restore `/profit` as a local, report-first archive shelf; keep the generic workspace at
   `/profit-review`.
2. Accept only operator-selected local report JSON in browser memory. Do not persist report contents,
   scan arbitrary server paths, read a database, refresh FX, or contact a marketplace.
3. Validate report identity and make source/snapshot gaps visible before allowing the retained
   detailed renderer to open the report.
   Preserve different file-content SHA-256 versions even when they reuse one `report_id`; skip only
   byte-identical content and report that decision to the operator.
4. Keep historical costs frozen. New calculations continue to bind one canonical internal SKU to
   one current procurement cost shared across platforms and countries; platform settlement and
   country fee facts remain isolated.
5. A future refreshed July run must be a new immutable snapshot/version and must compare settlement
   identities and settlement dates with the old artifact rather than overwrite it.

The isolated renderer's CSP permits only `data:` images. The recovered surface therefore displays
only strictly validated embedded PNG/JPEG/WebP/GIF sources and labels remote HTTPS images unavailable;
it does not claim to restore or fetch historical remote main images.
