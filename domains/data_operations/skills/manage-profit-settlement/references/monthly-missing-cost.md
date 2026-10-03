# TikTok monthly missing-cost recovery

Use this for an exact reporting period whose official settled-order evidence is
already frozen. Keep the original JSON and HTML; write a separate output version.

The monthly builder supports:

```text
--allow-missing-cost-default
--allow-shared-sku-missing-cost-default
--fx-input PATH_TO_MONTHLY_AVERAGE_FX_JSON
```

Both cost flags require the user's missing-cost authorization for this exact
run. The second flag narrows the legacy shared-SKU catalog block only when the
same official order supplies a unique platform SKU to Seller SKU mapping under
the exact shop/site and created-order period. It uses CNY 5 per unit and retains
`missing_cost_default_5_selected` warnings and colored report rows. Neither flag
approves a report or a marketplace action. Catalog and order identity metadata
are not rewritten.

Only truly missing cost evidence is eligible. Existing conflicting costs,
invalid variants, missing shop mapping, conflicting aliases, ambiguous cost
variant keys, foreign-currency cost records and unknown identity issues remain
blocked. A cost candidate that cannot be applied is not the same as no candidate.
Do not replace conflicting 5/5.5 or 8/8.5 observations with either CNY 5 or the
highest candidate under a missing-cost authorization. Such calculations need a
separately labeled scenario or exact new cost evidence/authority.

`--fx-input` reads an exact-period `monthly-average-fx/v1` artifact, verifies
calendar sample coverage and positive rates, retains its byte SHA256, and makes
no FX request. Omitting it preserves the legacy monthly live-FX read. For a
strictly offline run, always pass it and run with external network disabled;
keep the explicit catalog/project root isolated. The parameter is an input
selection, not permission to refresh tokens or call other APIs.

Example (supply the existing reviewed evidence, coverage and advertising paths):

```text
python scripts/build_tiktok_monthly_from_evidence.py --evidence EVIDENCE.json --coverage COVERAGE.json --project-root ISOLATED_PROJECT --output NEW_OUTPUT --start 2026-08-01 --end 2026-08-31 --site TH --actual-advertising-json ADS.json --fx-input FX.json --allow-missing-cost-default --allow-shared-sku-missing-cost-default
```

The command runs from this Skill directory. An explicit zero advertising override
uses the existing `--ad-rate 0` instead of the advertising file, only when that
zero was authorized. Never infer zero from missing advertising data.

Reproduce the old numeric baseline first. Fixing a cost gap restores the whole
previously rejected settlement line, so settlement and fulfillment totals may
change as well as costs; actual advertising totals stay fixed while allocation
can change. Audit affected parent orders separately from newly included parent
orders, retain negative adjustments, and recompute every line and total.

Complete arithmetic does not prove complete monthly settlement or actual
historical costs. Keep unsettled coverage, cost conflicts, source reconciliation
differences and temporary assumptions visible. Catalog observation time does not
establish historical applicability. Do not approve a `needs_review` report.

## Authority correction from the August recovery

The recorded September 15 approval concerned Shopee credential refresh, not a
blanket highest-cost policy. September 16 explicitly authorized CNY 5 temporary
missing costs with color. A later agent assertion that “conflict-high was already
approved” did not establish that authority. Reuse the user's exact scoped
instruction; do not reuse that historical assertion as permission in a new run.
# Historical cost evidence boundary

TikTok monthly profit reads use the historical catalog view. Current directory manual/publication cost is not automatically a historical cost. Original legacy cost rows are grouped only under the existing internal-SKU rule (1–4 digit SKU, or 66/77/88/99 plus four digits); arbitrary platform IDs and other prefixes are not truncated. All original identities, currencies, validity dates, and competing candidates remain in `source.historical_cost_inputs`.

The requested site's explicit monthly interval filters dated costs in both monthly branches. Conflicting applicable prices are blocked; missing-cost authorization never authorizes selecting the higher conflicting price. Undated legacy observations remain usable as explicitly unperiodized inputs and are labeled `undated_legacy_cost_observation`, not confirmed historical costs. Current-only records without historical evidence are blocked instead of being treated as missing and replaced with CNY 5.

The TikTok ordinary/shared-missing entries and the Shopee monthly entry now pass the site's exact requested interval and prohibit conflict-high selection. Shopee's existing missing-cost-default behavior is unchanged; this code repair does not grant new cost, advertising, or platform authorization. Existing original reports remain preserved; new historical-view outputs require separate review and registration. For 0018 and 0810, the user has explicitly chosen to retain the cost conflicts pending reconciliation: do not select the high value, substitute CNY 5, or repeatedly request confirmation.
