# Complete captured sample audit (U05-E)

The `/profit` single-SKU drawer exposes every row in the selected capture's
`samples.rows` array. It uses the existing pinned local profile, captured-review
HTTP bridge, full catalog identity and FX/cost assumptions. There is no new file
path, live collector, write authority, approval or calculation formula.

The response adds `sample_audit` with schema `profit-captured-sample-audit/v1`.
All rows are returned without server truncation; browser pages display ten rows
each. Full return means this particular capture file, not complete platform,
market or period coverage. The existing 16 MiB input capture limit still applies.
Missing/non-array samples have unknown captured/eligible counts and an explicit
missing state, rather than pretending to be an empty valid capture.

## Counts and identities

| Field | Meaning |
| --- | --- |
| captured | Original array length, including rejected and unsettled rows |
| settled_in_window | Records with selected identity/currency, a complete order ID, settled status and an aware timestamp within the selected interval/cutoff |
| eligible | Records also satisfying uniqueness, source metadata, quantity, finite amounts and the existing net-basis contract |
| returned | Every original row; equals captured for an available array |
| filtered / current_page | Browser-only matching population and its current page size |

Each record has `capture_index` and a unique `row_key` combining sample content
SHA and index. Even duplicate or malformed raw records retain an audit position;
they are not silently deduplicated. Full order and SKU identity, timestamp,
quantity, currency, paid/net totals and per-item amounts remain separately named.
The selected sample/capture SHA, source/version/cutoff, timezone window, model SHA
and current cost/FX/ad assumptions accompany the audit. Raw record details preserve
the source values; nonfinite JSON numeric tokens are represented with an explicit
`invalid_numeric` marker so they cannot leak back as valid money or a default zero.

Rejected rows retain reasons (identity, duplicate ID, date/window, metadata,
quantity, paid/net amount or net basis) and null derived profits. A malformed row
does not discard the entire audit view. The existing aggregate consumer still
invalidates its sample set; no previously rejected record enters its estimate.

## Derived values and actual estimator membership

Per-item profits reuse `sku_profit_model.enrich_comp`, with the same per-item
float inputs and rounding used by the existing capture consumer. They are current
assumption estimates, not historical realized profits. Missing cost, FX or explicit
advertising input keeps profit and margin null while raw samples remain visible.
Positive, negative, exactly zero and unknown profits have distinct categories.

Outlier flags reuse `is_outlier_comp`: nonpositive net or the enriched four-decimal
net/paid ratio below 0.1. With complete model inputs the actual enriched record is
passed to that rule. If only the required financial values are known, the same
ratio precision is used for the flag; unknown required values leave the flag null.
The rule/model version is visible. It does not remove samples from either existing
estimate. In particular, flagged outliers may still have `used_in_base_estimate`
or `used_in_waterfall_all_estimate` true because U05-C/D used those populations.
These flags mirror actual returned model populations, not the audit's display
filter or an inferred adoption decision.

Affiliate with/without/unknown is taken only from the validated U05-D fee capture.
Missing or invalid affiliation evidence stays unknown. There is no `!affiliate`
fallback, zero default, fee-quantile inference or assumption that unknown means no
affiliate. U05-D's caller-declared non-overlap limitation remains unchanged.

## Display-only filtering, pagination and histogram

The browser composes affiliate, profit-sign, outlier and eligibility selectors
with order/source text search. Every control changes only the audit display.
No re-estimation request is sent, and the prior/posterior results above remain
bound to their original populations. Changing a filter resets the page to one;
pagination preserves capture order, clamps to the valid range and never drops
or repeats row identities. Raw details reset when the display changes.

The histogram consumes all filtered finite per-item CNY profits, not just the
current page. It shows input population, finite count, excluded unknown count,
bucket intervals and counts. Buckets are left-closed/right-open except the final
right-inclusive bucket. All-equal values occupy one closed point bucket. Empty
and entirely unknown sets have no fabricated zero bucket. Signed values and both
endpoints are conserved; scaled indexing avoids overflow for extreme finite
bounds. Display labels use compact numeric precision while the returned browser
histogram data retains actual boundaries and counts.

The desktop drawer widens to display all sample columns at 1440; on 390 the table
has an explicitly labeled, keyboard-focusable scroll container. Page and modal
widths stay bounded. Filters and pagination use native labeled form controls.
Changing profile/identity/parameters, starting a read, closing the drawer or a
failed read removes the whole sample component and its local filter/page/chart
state. Disk changes are detected on the next explicit source read, as in U05-C.

## Verification boundaries

The regression includes 37 actual captured rows across four browser pages, mixed
affiliate/sign/outlier classes, combination search, count/identity conservation,
last-page and keyboard behavior, unchanged model result during filters, source
invalidation, missing inputs, empty/equal/negative/endpoint histogram controls.
It uses the actual captured consumer, pure model, complete original Handler AST
and installed Chrome with owned synthetic inputs at 1440 and 390.

Prior U05-B/C/D journeys remain. Original Q01-D four composite static tests are
preserved; their literal old API/selectors are not changed merely to produce green
results. The new audit restores the captured equivalent of the fourth group's
display capabilities, without the old live collectors or silent affiliation
inference. Global model asymmetry, live/provider completeness, full application
startup and deployment remain outside this package. The prior HTTP fixture
Win10053 and opt-in guard skip are retained as limitations, not repeatedly rerun.
