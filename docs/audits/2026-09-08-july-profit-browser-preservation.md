# July profit page browser preservation record

Date observed: 2026-09-08 (Asia/Shanghai)

## Last-seen historical page

- Last browser URL supplied by the Codex in-app browser state before preservation:
  `file:///C:/Users/Windows11/Desktop/Agent_PR/_codex_worktrees/05-data-ops/artifacts/profit_reports_monthly/2026-07/tiktok_TH_2026-07-01_2026-07-31.monthly-profit.html`
- Visible page heading in the retained user screenshot/conversation evidence:
  `TIKTOK monthly 订单级利润明细`
- Visible reporting period: `2026-07-01` through `2026-07-31`.
- Visible calculation label: `realized_settlement_with_estimated_ads`.
- Visible settled order-line count: `2429`.
- Visible daily chips were order-created-date counts, not settlement-date counts.
- The page was a local generated HTML artifact under the former `05-data-ops` worktree. It was
  not a live platform page and was not evidence of a fresh API read at preservation time.

The historical tab was no longer present when the browser inventory was read. The exact HTML path
and its referenced temporary screenshot path were then checked read-only and were both absent. No
loaded DOM, embedded report JSON, or browser cache payload remained available through the supported
browser interface. No page was refreshed or recreated in its place.

## Current surviving browser page

- URL: `http://127.0.0.1:8876/profit_archive.html`
- Title: `已结算利润报表 · OrbitHive`
- Source: the restored static archive shelf in this worktree.
- Current visible state: `0 份`, `尚未导入报表`, and `没有可展示的历史报表`.

This current page contains no historical July monetary data. It must not be treated as a recovered
July snapshot.

## Missing historical fields

Because neither the historical HTML nor its immutable monthly-profit JSON is available, this
preservation record cannot recover or verify:

- per-order settlement amounts or fee lines;
- buyer-paid totals, product costs, advertising allocations, or fulfillment costs;
- FX values and FX provenance;
- order identities, product identities, or report checksum/idempotency identity;
- daily settlement-date counts for July 26 through July 31.

The retained visible period/count labels are evidence only of what was displayed. They are not a
replacement for the original report payload and must not be used to reconstruct monetary totals.

## Browser acceptance boundary

The synthetic report upload needed for a real Handler import-to-iframe browser acceptance test was
not performed. The computer-use file chooser required user approval and the request was declined by
the tool/user interaction boundary. That refusal was respected: no alternate browser, DevTools,
CDP, scripted upload, or other workaround was attempted. Handler POST/CSP behavior remains covered
by automated tests; the end-to-end browser upload flow remains unverified.

## Test evidence boundary

- Focused run A: 21 passed. It included the archive surface tests, one detailed renderer test, and
  the runtime identity negative matrix. Durable JUnit evidence is at
  `C:\Users\Windows11\Desktop\Agent_PR\_codex_worktrees\05-profit-july-restore-20260908\artifacts\test_logs\pytest-profit-archive-focused-20260908.xml`.
- Focused run B: 20 passed. It included the archive surface tests and runtime identity negative
  matrix, but omitted that one separately selected detailed renderer test.
- The earlier full-suite terminal session reached approximately 98%, but its final stdout and exit
  summary were not retained. No JUnit file was produced, so it must not be reported as a completed
  full-suite result.
- A later attempt to create a durable full-suite JUnit run was stopped immediately after independent
  review instructed that the suite should not be rerun in this narrow preservation pass. It stopped
  near 4% and produced no JUnit file. This interrupted attempt is not a test result.
