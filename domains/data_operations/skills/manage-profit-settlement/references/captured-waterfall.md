# Captured fee waterfalls (U05-D)

The existing `/profit-review` single-SKU drawer now separates prior estimates, posterior
estimates and captured sample observations. It does not produce approved financial
facts or call live pricing rules. Its entry remains the U05-C local captured-review
bridge. No HTTP permission, file path or profile field is added.

## Optional capture extension

Add `waterfall` to an existing full-identity capture entry. The entire existing file
is still pinned by `sku_evidence_sha256`, with catalog and FX bindings preserved.
The extension has its own canonical SHA in the response. A pre-U05-D capture lacking
this field retains its original base estimate and explicitly lacks waterfall evidence.

Required metadata: exact `identity`, matching `currency`, nonblank `source` and
`version`, timezone-aware `as_of`, `valid_from`, `valid_to`, and
`creator_affiliate_basis: "distinct_non_overlapping"`. Validity must cover the full
profile interval `[start 00:00, end+1 day 00:00)`; capture time must reach its end and
the sample capture cutoff. This validates the declared historical interval, not
current market freshness. Cross-identity/currency, invalid/nonfinite money, duplicate
or unknown order IDs, missing provenance and insufficient validity are rejected.
Changes on disk invalidate previous results on the next explicit read.

`creator_local` is the separately captured creator commission; `affiliate_local`
is a separately captured affiliate/partner commission. They must be distinct,
non-overlapping charges under the source's definitions. These names do not prove
that a particular provider has two charges. If the source treats them as aliases,
do not populate both or claim the required distinct basis: this adapter rejects an
ambiguous basis. A single charge may be mapped once, with explicit zero evidence
for an absent independent fee; absence of evidence is null, not zero.

The historical code has separate prior rule fields `creator_rate` and
`affiliate_rate`, whereas the old TikTok sample importer reads `Affiliate Commission`
and the Shopee importer defaults it to zero. Those old import paths are not used
here and cannot establish the new capture's fee identity or affiliation state.

## Prior scenarios

`prior` may contain separately sourced `with_affiliate` and `no_affiliate` objects.
Missing scenarios stay unknown; one is never produced by zeroing the other.
Each object has `sale_local`, `include_tax` (boolean), `components`, and `extra_cap`.
All amounts are per selected item in the extension's currency.

Components are `goods_local`, `logistics_local`, `commission_local`,
`transaction_local`, `extra_local`, `creator_local`, `affiliate_local`, `ad_local`,
`seller_tax_local`, `fixed_fee_local`. Every component must be a finite nonnegative
amount for a complete estimate. Missing/null components remain visible as unknown;
known components remain visible, with no complete estimate. Goods must agree with
selected CNY cost / FX, and ads with selected sale × explicit ad rate, within 0.01
local units. Captured sale must equal the current selected listing/median basis.
Tax explicitly excluded from the scenario is not deducted; the original component
is still retained in source details. No-affiliate requires creator and affiliate
components to be explicitly zero, not nonzero values with a flag toggled off.

`extra_cap` example:

```json
{"uncapped_amount":"25","cap_amount":"20","extra_cap_hit":true}
```

The captured applied fee is `components.extra_local`. With a positive cap below
the captured uncapped amount, the flag must be true and applied amount equal the
cap. Otherwise the flag is false and applied amount equals the uncapped amount.
A cap of zero means uncapped in this contract. Unknown cap evidence stays unknown
and does not become false. This checks only captured amount consistency at the
selected sale, never a live rate or a new cap formula.

Only complete, consistent components instantiate the existing `PriorBreakdown`
and call `prior_profit_from_breakdown`. Expected equations for accepted scenarios:

```text
estimated net = sale - included platform/logistics/creator/affiliate/tax/fixed fees
prior profit local = estimated net - goods - ads
prior profit CNY = prior profit local × selected FX
```

Existing pure-model limitation: `PriorBreakdown.deductions` includes affiliate fees
unconditionally, but `prior_profit_from_breakdown` includes them in estimated net
only when `include_creator` is true. A nonzero affiliate with false creator flag can
therefore produce inconsistent net/profit. U05-D rejects this contradictory capture
and checks the model return equation (0.02 local rounding tolerance). It does **not**
repair the global formula or claim that all legacy consumers are corrected.

## Observed sample fee totals and posterior estimates

`sample_fees` is an array of objects, each with exact `identity`, exact eligible
`order_id`, `currency`, `affiliate_state` (`with`, `without`, `unknown`) and
`components`. These components are the eight platform/logistics fields above,
excluding goods and ads. Amounts refer to the selected SKU units on that order,
not an unallocated whole-order fee. Signed deductions preserve refunds/credits.
The basis remains U05-C `platform_net_before_external_cost_and_ads`.

All eligible source samples have passed U05-C full identity, unique order ID,
quantity, currency, time and net-basis checks. Fee rows may cover fewer samples;
missing rows or fields stay unknown. `without` with a nonzero creator or affiliate
charge is rejected. No inference from missing/default fee fields or a quantile
split is permitted. Unknown affiliation stays out of both named groups.

The observation panel uses raw captured sample totals:

```text
unexplained delta = sum(paid product amount) - sum(net settlement) - known fee subtotal
```

It exposes each component's fully known amount or null, its known-row subtotal,
known row count, captured gross/net, quantity and sample count. No known fees means
a null known-fee total. A complete set reconciles within 0.01 local units; complete
but unequal totals show mismatch. Partial evidence stays partial even if its delta
is zero. The delta is unclassified and is never manufactured into an extra fee.
These are local sample observations, not approved or complete-period facts.

Posterior all/with/without groups call the existing `enrich_comp` and
`estimate_from_ratio` on eligible per-item paid/net sample values. The same current
goods/FX/ad assumptions apply. The net estimate already includes captured platform
fees: displayed observed fees are never deducted from it again. The posterior
waterfall shows selected sale, aggregate sale/net difference, estimated net,
explicit goods cost, ads and estimated profit. The aggregate difference is not a
fee decomposition. Missing model inputs keep estimates null while retaining actual
sample counts. Ancillary shipping statistics remain null rather than inheriting
the old model's zero defaults. Existing float rounding and model SHA are retained.

This estimator does not infer full sample coverage, filter outliers, select a prior
fallback as a posterior result, or apply a quantile-based affiliation split. The
[complete sample audit](captured-sample-audit.md) now supplies separate display-only
filters, pagination and distributions without altering these estimator populations.

## Validation and legacy mapping

`test_u05_waterfall.py` uses the actual captured bridge and pure models, including
the preserved global-model asymmetry and adapter rejection. The installed-Chrome
journey uses the complete original Handler AST with only owned synthetic inputs,
at 1440 and 390 widths, alongside U05-B/C journeys. This is not full application
startup or live provider verification. The existing HTTP fixture Win10053 and
startup guard skip are recorded as prior limitations, not silently fixed or omitted.

Q01-D's old waterfall assertion group maps to the new dynamic prior rows, explicit
posterior net waterfall, captured cap flag, missing values and observed fee totals.
Original composite static tests remain unchanged for traceability; their old
literal selectors/GET API requirements are not proof of current capability. No
legacy assertion is deleted or skipped to produce a green result. Sample audit and
distribution are handled by the separate U05-E display consumer linked above.
