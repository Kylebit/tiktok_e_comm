# Captured profit review (I05)

`profit_settlement` remains the calculation implementation. Captured review uses
explicit input files; it never pulls, refreshes credentials/FX, starts a scheduler,
writes a business database or approves knowledge. CLI output writes are explicit.

## Entry points

```text
python -m domains.data_operations.profit_settlement.cli review-captured --profile PROFILE.json --output REPORT.json --html REPORT.html
python domains/data_operations/skills/manage-profit-settlement/scripts/build_weekly_from_evidence.py --project-root PROJECT --evidence-dir EVIDENCE --platform tiktok --site TH --start 2026-08-03 --end 2026-08-09 --timezone +07:00 --fx-input FX.json --output OUTPUT
```

The existing weekly script requires either `--fx-input` (captured
`rates_cny/source/as_of`, optional snapshot_id) or explicit `--live-fx`, and an
explicit calculation UTC offset. Optional Ozon enrichment remains an explicit
external action. The policy file remains the source of weekly ad and fulfillment
settings; per-run overrides remain assumptions. Existing monthly commands retain
their own external-read behavior and are not silently converted to captured mode.

`GET /api/profit-center/captured-review` returns `input_required` only.
`POST` accepts `{"profile": ...}` and calls the same captured consumer as CLI.
Local Host/Origin, JSON and the existing 64 KiB limit apply. No default business
paths are selected. U05 owns the normal project/profile picker; users should not
need to type storage paths in routine operation. The original weekly/pre_release
and legacy settlement routes remain; this does not make their FX reads offline.

## Profile

Required `profit-captured-profile/v1` fields: platform, site, shop_id, start, end,
timezone (`+HH:MM`/`-HH:MM`), and absolute catalog_path, evidence_path, fx_path.
Evidence platform/site/shop must match. Optional `ad_rate`, `fx_overrides` and
`seller_sku_mapping` are explicit inputs. `allow_temporary_cost_policy: true`
selects the existing temporary policy; absent/false never opts in implicitly.
This profile selects evidence and assumptions; it is not an approval manifest.

## Identity and money

The catalog shares I01's review inside one caller-owned read transaction. Raw
Seller SKU and full platform/shop/product/variant identity are retained; no tail
four or prefix inference. A globally unique local observation does not authorize
another shop/platform. Scope mismatch removes cost/weight applicability, not only
display metadata. Relevant full-identity I01 issues enter each report; unrelated
product issues remain visible in the top-level catalog review without blocking it.

All consumed cost candidates, identities, issues and metadata enter the catalog
digest. Temporary policy v2 includes its candidates, selected assumptions and
calculation interval in its version. Dated costs must cover the declared half-open
interval; observation time/DB mtime is not validity. Ambiguity/currency mismatch
or unusable dated evidence cannot fall through to CNY 5. The compatibility weekly
runner reuses this review and does not select highest/default assumptions.

Cost and FX report identities include content checksums even when caller labels
are fixed. FX overrides retain the original snapshot and operator provenance.
Declared timezone converts source timestamps before period selection. Missing or
nonfinite money is unknown; no calculable lines yields null totals, no_data (or
needs_review for rejected input), and `no_calculated_facts`. Some rejected rows
yield `partial_diagnostic`: shown totals cover only calculated rows, not the whole
period. HTML preserves unknown values and labels diagnostic scope. Estimated ads
remain distinct from actual-advertising monthly calculations. This code does not
independently authenticate the external provider's saved artifact.

## Knowledge and verification boundaries

For the current `/profit-review` single-SKU drawer, explicit capture schema and pure
estimate boundaries, see [captured SKU evidence](captured-sku-evidence.md).

For optional local daily coverage and immutable monthly index lookup, see
[captured coverage and history](captured-coverage-history.md). These additions
preserve review-only status and the existing report calculations.

`profit-knowledge-reference/v1` exposes a report-content digest, report_refs,
report/version/period, source, assumptions and quality issues with usage=reference.
It grants neither fresh business-fact verification nor current execution authority.
K00's `product-publication-knowledge-review/v1` requires an existing upper-level
review bound to exact document bytes and report_refs. No such manifest is generated
here. The existing explicit ProfitKnowledgeBase monthly approval remains separate
and immutable; weekly/draft/needs_review reports do not become approved entries.

K00 contract reference: fixed C5 ff7ba453b6521571521681a03cde4c80baefb82d,
docs/knowledge/README.md and modules/product_agent/knowledge.py. K00 code was not
copied into I05, and the original Vault was not read.

The isolated HTTP verification compiles the complete original Handler unchanged
while excluding module-level business startup; it uses a loopback-only fixture
server and real domain engines. It is not a full application boot or deployed UI
acceptance. Legacy settlement/config still has its historical FX call pattern;
no current real provider-call count or live financial correctness is claimed.

## Legacy local CSV/HTML script migration (I05-C)

`scripts/generate_local_weekly.py` is a separate compatibility entry for existing
local income CSV/Shopee HTML snapshots. Existing invocations must add the site's
explicit UTC offset; there is no machine-timezone default:

```text
python domains/data_operations/skills/manage-profit-settlement/scripts/generate_local_weekly.py --root PROJECT --start 2026-08-03 --end 2026-08-09 --timezone +07:00 --output OUTPUT
```

Run from the project Python environment with the project root on `PYTHONPATH`,
as with the other direct scripts above. `+07:00` is an example for the selected
TH period, not a default for all sites. Source date-only/naive times are interpreted
in that explicit offset; aware settled timestamps are converted before selection.
Both source rows and cost validity use `[start 00:00, end+1 day 00:00)` in that zone.

This entry reuses `resolve_temporary_cost_policy` and never opts into temporary
highest/default costs. Missing costs, conflicting unselected costs, or records
outside the calculation interval produce null money with related policy issues.
The resolved cost snapshot feeds both adaptation and the report engine; policy
snapshot, selection=false and interval are retained in report source and identity.
Use an explicit captured profile with `allow_temporary_cost_policy: true` when
that separate assumption selection is intended; this legacy CLI has no such flag.
Raw store/platform/SKU scope checks still apply. This change does **not** make
the compatibility script offline: its existing live FX lookup remains unchanged.
