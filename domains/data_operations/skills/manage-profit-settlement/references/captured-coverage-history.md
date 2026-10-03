# Captured coverage and report lookup

The `profit-captured-profile/v1` accepted by the existing captured consumer and
`POST /api/profit-center/captured-review` accepts two optional absolute local paths:
`coverage_path` and `knowledge_root`. Neither selects a live account or approves
a report. No default paths, FX reads or approval writes are added.

## Source mapping

| Existing source | This consumer |
|---|---|
| `captured_consumer` → weekly evidence bundle | Same settlement arithmetic, cost policy, FX and ad inputs |
| `tiktok_coverage.build_coverage` | Existing cancelled/settled/unsettled classification after complete local daily evidence |
| TikTok monthly builder script | Contract reference only; its real DB/live FX entry point is not called |
| `ProfitKnowledgeBase.list_reports` | Read the selected index and each original immutable monthly artifact |
| `render_profit_report_html` | Render the selected original report through a local frame endpoint |

## Coverage file

`profit-captured-coverage/v1` contains:

- `scope`: exact platform, site, shop_id, start, end, timezone from the profile.
- `evidence_sha256`: SHA-256 of the exact selected settlement evidence file.
- `streams.orders` and `streams.settlements`, each with nonblank `source_id`,
  timezone-aware `as_of`, `time_basis` (`order_created_at` or `settled_at`) and
  `days` records for the selected period.
- Each day: `date`, nonnegative integer `total_rows`, and nonempty `pages`.
  Each page has `cursor`, `next_cursor`, `rows`; the first cursor is empty,
  the final next cursor is empty, intermediate cursors form one complete unique
  chain, and row totals reconcile.
- Order rows: exact `order_id`, timezone-aware `order_created_at`, nonblank
  `order_status`. Settlement rows: exact `order_id` and `settled_at`, reconciled
  to the selected evidence rows for that local date. No financial values are
  copied into the coverage file.

A complete date requires capture as-of at or after the next local midnight,
all pages, exact identities, timestamps on that date and reconciled rows.
Zero is a count supported by a complete empty page, never inferred from absent
settlement rows. Missing days/pages, early capture or invalid scope/source yield
unknown counts while observed rows remain separately visible. The requested
period alone proves nothing about source completeness. Local file consistency
does not authenticate a provider capture.

The TikTok classification searches settlements **within the selected period**.
Its observed-through date is that period end; capture source as-of is displayed
separately. An unmatched non-cancelled order can have a settlement outside this
window: this result is not proof that it remains unpaid as of a later capture.
Other platforms retain daily evidence display without borrowing TikTok lifecycle
rules. Order/settlement clocks and timezone are retained separately. Empty profit
remains unknown even when complete zero settlement counts are available.

## Historical reports and versions

The existing approved index has platform/year/month but no site/shop columns.
Read original artifacts and verify report checksum, knowledge ID, approval/index
agreement and full report-line identities. Only exact site/shop matches appear;
empty or unproven scope is withheld. No folder-name scope inference, copied
knowledge store, reapproval or modification of the original artifact occurs.
An unavailable/corrupt index remains explicit; it cannot confer approval.

The current captured result always says `REVIEW_ONLY`. Its `review_id` binds
scope, source checksums, coverage/clocks, cost policy, FX and existing report
idempotency keys. Report IDs remain owned by the existing calculation engine.
Changed as-of may change review ID without changing monetary report ID; changed
cost/FX/ads retain their existing report identities and provenance.

The UI retains at most 20 full review results in session memory, filters by
platform/site/shop, and labels current versus older versions. Reloading loses
session reports; export JSON before closing. Approved monthly artifacts are read
again only with a selected local index. Old and current reports open with their
original stored values, without recomputation from today's inputs.

`POST /api/profit-center/report-view` receives one form field `report` containing
the selected JSON and only renders it. It reads no files and grants no approval.
Local Host/Origin and bounded body checks apply; the isolated frame permits the
existing renderer's inline style/script while blocking external resources.
The workbench's own CSP is unchanged.
