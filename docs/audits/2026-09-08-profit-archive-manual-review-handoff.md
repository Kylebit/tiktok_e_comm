# Profit archive manual review handoff

Date checked: 2026-09-08 (Asia/Shanghai)

## Preview

- Local Handler URL: `http://127.0.0.1:8877/profit`
- Read-only reachability check: HTTP `200`.
- Page title: `已结算利润报表 · OrbitHive`.
- This is a local recovery preview. It does not replace the formal `49289` service.

## Synthetic fixture to select manually

Absolute path:

`C:\Users\Windows11\Desktop\Agent_PR\_codex_worktrees\05-profit-july-restore-20260908\tests\fixtures\profit_archive\shopee_july_synthetic.monthly-profit.json`

Synthetic identity:

- schema: `profit-report/shopee/v1`
- report_id: `shopee-profit-july-synthetic`
- idempotency_key: `synthetic-only`
- source snapshot: `synthetic-snapshot-v1`
- file SHA-256: `44e86fd7a861090ed92032844ac7374c88716bbc9f6ae232789e4deb32b1daa1`

Every identity and amount in this file is synthetic test data. It is not a recovered July platform
snapshot and must not be copied into business reporting or the knowledge base.

## Manual steps and expected result

1. On the preview page, select `选择利润报表 JSON`.
2. Choose exactly the synthetic fixture above.
3. Expected shelf state: one report card, report count `1 份`, and report_id
   `shopee-profit-july-synthetic`.
4. Open the report card. Expected order-line count: `2`.
5. Expected period: `2026-07-01` through `2026-07-31`.
6. Expected complete totals:

   - net settlement: CNY `180.00`
   - product cost: CNY `35.00`
   - advertising: CNY `39.60`
   - local fulfillment / external cost: CNY `8.00`
   - profit: CNY `97.40`

7. Expected order-created dates and filtered totals:

   - `2026-07-25`: one line; settlement `100.00`, product cost `20.00`, advertising
     `22.00`, local fulfillment `4.00`, profit `54.00`.
   - `2026-07-26`: one line; settlement `80.00`, product cost `15.00`, advertising
     `17.60`, local fulfillment `4.00`, profit `43.40`.

8. Select the exact same file again. Expected: report count remains `1 份`; the status reports one
   identical-content file skipped.
9. A different file body that deliberately reuses the same report_id would be retained as a second
   SHA-256 version with a warning. No such second fixture is provided in this handoff, to avoid
   creating additional fake July data.

## Acceptance boundary

The manual file-selection step is the only remaining browser acceptance gap. Automated evidence
covers Handler POST/CSP, renderer details, archive asset identity, and manifest consistency. A direct
fixture POST or a preloaded test page would bypass the archive's user-file selection and parsing path,
so it is not an acceptable substitute and has not been presented as one.

Focused JUnit log:

`C:\Users\Windows11\Desktop\Agent_PR\_codex_worktrees\05-profit-july-restore-20260908\artifacts\test_logs\pytest-profit-archive-focused-20260908.xml`

