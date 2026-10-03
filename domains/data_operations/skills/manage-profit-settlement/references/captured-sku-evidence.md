# Captured single-SKU evidence

For the optional current fee explanation extension, see
[captured fee waterfalls](captured-waterfall.md). It adds independently captured
prior scenarios and sample fee observations without changing the base estimate.

The `/profit-review` **单 SKU 证据 / 估算** drawer reads the selected local profile.
It replaces the old `/sku-profit` navigation link; legacy source remains for
traceability. No legacy SKU controller, live FX, order pull or suffix lookup runs.

Use the existing local `POST /api/profit-center/captured-review` bridge with
`{"profile":PROFILE,"mode":"sku","sku_identity":IDENTITY}`. Omit identity to
read scoped choices. The existing Host/Origin, JSON and 64 KiB request checks apply.
The response schema is `profit-captured-sku/v1`; every response is `ESTIMATE_ONLY`.
No calculation occurs merely by opening `/profit-review` or importing/saving a profile.

## Explicit inputs

Keep the existing `profit-captured-profile/v1` scope and absolute input paths.
Add `sku_evidence_path` (local JSON), `sku_evidence_sha256` (exact lowercase SHA256),
and `sku_price_basis` (`listing` or `recent_median`). Supply `ad_rate` explicitly;
there is no implicit SKU advertising rate. Optional `fx_overrides` preserve the
base FX snapshot. The SKU branch never opts into temporary highest/default costs,
including when the weekly profile has `allow_temporary_cost_policy: true`.

The selected evidence JSON has this structure (values here describe fields, not
an executable business profile):

```json
{
  "schema_version": "profit-sku-capture/v1",
  "scope": {"platform":"tiktok","site":"TH","shop_id":"LOCAL_SHOP_KEY","start":"2026-08-03","end":"2026-08-09","timezone":"+07:00"},
  "catalog_sha256": "SHA256_OF_SELECTED_CATALOG_BYTES",
  "catalog_snapshot_id": "ACTUAL_LOCAL_CATALOG_SNAPSHOT_ID",
  "fx_sha256": "SHA256_OF_SELECTED_FX_FILE",
  "entries": [{
    "identity": {"platform":"tiktok","shop_key":"LOCAL_SHOP_KEY","product_id":"FULL_PRODUCT_ID","variant_id":"FULL_VARIANT_ID","seller_sku":"FULL_SELLER_SKU"},
    "listing": {"amount":"250","currency":"THB","source":"captured listing source","version":"listing-version","as_of":"2026-08-06T00:00:00+07:00"},
    "samples": {"source":"captured settled rows","version":"sample-version","as_of":"2026-08-10T00:00:00+07:00","time_basis":"settled_at","rows":[
      {"identity":{"platform":"tiktok","shop_key":"LOCAL_SHOP_KEY","product_id":"FULL_PRODUCT_ID","variant_id":"FULL_VARIANT_ID","seller_sku":"FULL_SELLER_SKU"},"order_id":"FULL_ORDER_ID","status":"settled","quantity":1,"paid_product_amount":"200","net_settlement_amount":"100","currency":"THB","settled_at":"2026-08-05T12:00:00+07:00","net_basis":"platform_net_before_external_cost_and_ads"}
    ]},
    "image": {"file":"captured.png","sha256":"SHA256_OF_PNG"}
  }]
}
```

`shop_key` is the exact local catalog key, not an inferred provider shop ID.
All five identity fields are required; aliases and clipped/suffix identifiers
cannot select a row. Catalog review supplies name, specification, identity,
currency, cost candidates/matching and its content snapshot. Site and shop scope
filter choices before selection; cross-shop/platform collisions remain unresolved
under the existing conservative cost policy. File SHA pins the whole catalog and
FX, while the catalog snapshot also checks the actual read transaction content.

Listing is a per-item captured price at `as_of`; it is displayed separately from
the median of each eligible sample's `paid_product_amount / quantity`. Sample
amounts refer to the selected product/variant units, never an unallocated order
total. Rows must have unique complete order IDs, exact identity/currency, aware
settlement timestamps within `[start 00:00, end+1 day 00:00)` in the explicit offset
and no later than their capture cutoff. Local normalized `settled`, `unsettled`
and `cancelled` are accepted; only settled rows enter the estimate. An invalid row
invalidates the sample set. Source/version and canonical hashes remain visible.
These rows do not prove complete market coverage or a full posterior return set.

`net_basis` explicitly means platform net **before external goods cost and the
advertising assumption**. Do not populate it from evidence with unknown fee basis.
Platform fees already included in net settlement are not deducted again. The
existing `sku_profit_model.enrich_comp` and `estimate_from_ratio` compute the
median net ratio and estimate using selected price, FX, goods cost and ad rate;
their existing float rounding is retained. Model source SHA, price basis and all
assumptions accompany the result. Sample profit statistics use these assumptions,
not realized historical costs. The model's default-zero ancillary shipping
statistic is projected to null because this capture has no shipping components.
The base capture alone does not infer affiliate groups, fee decomposition or sample
distribution. The optional waterfall extension requires its own explicit evidence.

Missing/invalid listing, empty samples, unresolved costs, unavailable FX or an
absent explicit model input produce concrete gaps and null estimates as relevant.
A missing picture does not block an otherwise supported estimate. No approval is
created: undated local costs remain observations for estimation, not dated facts.

## Local images and invalidation

An optional PNG must live in the evidence file's sibling `sku-assets` directory.
Only a single ASCII basename ending `.png` is accepted. Resolved paths must remain
inside that directory and the evidence folder, file reads are capped at 1 MiB,
SHA must match, and PNG dimensions must be at most 2048×2048. The browser decodes a
returned data URL, with an explicit unknown fallback if decoding fails. There is
no arbitrary image path endpoint, remote download or image URL fetch.

Changing profiles/parameters, selecting another identity, closing the drawer or
starting a refresh clears prior displayed results. Every read rechecks source
pins; a changed catalog/evidence source fails closed and changed FX removes the
estimate. There is no filesystem watcher: external file changes are detected on
the next explicit read. Re-import a deliberately repinned profile to accept new
capture inputs. This is local input selection, not business approval.

## Verification scope and remaining capability gaps

`test_u05_sku_evidence.py` exercises the real bridge and existing catalog/cost/pure
model against synthetic SQLite/JSON. `test_u05_finance_browser.py` runs the complete
original Handler AST with guarded synthetic inputs and installed Chrome at
1440/390; it also preserves the U05-B daily coverage/report version journeys.
This is not full application startup, APP deployment or live provider validation.

Q01-D's four legacy composite regressions remain tracked and unchanged. U05-C
restored the local single-SKU entry and price/image/cost evidence subset; the
optional U05-D extension now supplies captured prior/posterior waterfalls and
explicit affiliate scenarios. [U05-E sample audit](captured-sample-audit.md) exposes
all captured rows with display-only filters, pagination and distributions. The
old composite tests remain unchanged; provider completeness and old live
collector behavior are not inferred from these captured consumers.
