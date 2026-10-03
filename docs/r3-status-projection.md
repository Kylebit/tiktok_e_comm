# R3 read-only status projection

`GET /api/product-workspace/publication-stages` projects the complete target list
of the selected ReleaseStore plan. It reads the existing approved snapshot,
persisted final candidate/approval, async run store and publication report store.
It does not write closure, create a ledger, dispatch, or contact providers.

- `final_review` distinguishes awaiting approval, recorded approval, recorded
  execution and superseded history. `execution_recorded: null` means the run or
  report index could not be verified; it does not mean no execution occurred.
- `target_results` always follows the approved target list. `NOT_RUN` explicitly
  means no execution evidence for that target; unknown counters remain null.
- Each target keeps `reported_status` separate from `lifecycle`. A verified report
  with PROCESSING and completed readback has lifecycle READBACK_ONLY. This says
  local readback finished and does not claim marketplace publication.
- `official_success` requires PUBLISHED plus verified snapshot and completed
  readback, with no unknown outcome, manual handoff, or unresolved source conflict.
- `source` and `history` identify the run, report, timestamps and validated
  digests. Exact offer, plan, revision, snapshot, execution identity, target scope,
  summary counts and approval packet must agree. Run/report time linkage is
  checked, including execution after final approval.
- Ordering uses validated run creation times within the exact approved plan and
  target. Newer failure supersedes older success. Equal times remain conflicted;
  old unknown, unfinished or corrupt evidence cannot be erased by later success.
  The existing async store has no durable per-target recovery/supersession edge,
  so chronology alone is not treated as such an edge.
- Reports with an existing different scoped candidate use the original durable
  authority loader. Missing or invalid packets require reconciliation; the
  execution gate and candidate scope rules are unchanged.
- `execution_summary.runs` retains each report's full run-level write count and
  mutation budgets, including shared prefix writes. These are distinct from
  per-target counts. Totals remain null if any contributing count is unknown or
  evidence integrity fails. An intact index with no runs has total zero.
- Untouched targets on a partially executed platform request a scoped action
  review; they do not suggest rerunning the entire platform. Explicitly selected
  superseded plans preserve original evidence and only expose READ_ONLY_HISTORY.

Current R2/config changes do not replace frozen approved execution evidence.
Missing/corrupt reports and orphan report index entries are explicit reconciliation
states. No projection field is a business closure receipt or a new authorization.

Tests use real ReleaseStore, compiler, persisted approval, runner, report/run
stores and in-memory HTTP Handler requests with synthetic executor outputs.
They establish local projection behavior, not live provider outcomes or UI QA.
