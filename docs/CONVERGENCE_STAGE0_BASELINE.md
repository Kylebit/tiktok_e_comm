# One-click release convergence: stage 0 baseline

This document freezes the observed baseline before another production fix is
attempted.  It does not authorize a state-machine, UI, database, or external
platform change.

## Observed production-shaped state

- The approved plan has fourteen durable collect-box batches and one durable
  one-click job.  It is not a clean first attempt.
- The latest TikTok collect-box result has five successful targets and one GB
  failure with `target_preparation_failed`.
- The latest Shopee collect-box result is
  `RECONCILIATION_REQUIRED` with
  `collectbox_platform_preparation_failed`.
- The persisted one-click job is `BLOCKED`, its canonical run remains
  `PENDING`, and the last active explicit scope is Ozon.
- The formal database and running service were inspected read-only.  No POST,
  marketplace call, or external write was used to establish this baseline.

## Exact current 409 path

`POST /api/product-workspace/publish-tiktok` first calls the collect-box
publishability gate.  The GB exception currently accepts only
`approved_detail_readback_mismatch`.  The observed batch contains
`target_preparation_failed`, so the platform is judged not publishable and the
server returns HTTP 409 with `step1_collectbox_required` before a run, claim,
worker wake, or external write.

The browser currently replaces this structured reason with a generic
"HTTP 409 but expected 202" message.  That presentation issue is recorded but
is not repaired in stage 0.

## Permanent gates frozen in this stage

1. `tests/test_convergence_stage0_baseline.py` preserves the sanitized legacy
   topology and uses the real HTTP handler to reproduce the exact 409.
2. The desired GB non-blocking behavior is an explicit strict xfail.  Run it
   with `--runxfail` to prove the pre-fix failure before any repair.
3. The TikTok, Shopee-global, and Ozon POST routes are independently routed;
   one click cannot invoke another platform starter.
4. The existing WO-107 gates remain mandatory for the already accepted MX and
   GB behavior:
   - `test_gb_collectbox_waives_copy_category_attributes_but_keeps_exact_price`
   - `test_gb_dispatch_skips_post_write_readback_and_submits_directly`
   - `test_collectbox_start_response_exposes_canonical_publishability`
   - strict MX/SEA verification inside the GB-waiver test

## Not done in stage 0

- No 409 rule was changed.
- No old job or batch was migrated, reset, deleted, or rewritten.
- No new retry/reconciliation semantics were introduced.
- No service restart or deployment behavior was changed.
- No Miaoshou, TikTok, Shopee, Ozon, or formal database write was performed.

The next stage may start only after this baseline and the proposed minimal
successor state model are explicitly accepted.
