---
name: publish-approved-product
description: "Execute stages 05-07 for an approved Product Center offer through three independent workflows: inspect the approved per-SKU snapshot, dispatch TikTok through Miaoshou, create a Shopee global product, publish Ozon through the official API, perform platform-specific readback, classify truthful results, and retain only confirmed incident lessons. Use when the user asks to publish, retry, inspect, or diagnose an already-approved Offer ID after stages 01-04."
---

# Publish Approved Product

Use the approved Product Center snapshot as the only shared input. TikTok,
Shopee and Ozon are independent tasks. Never let one platform's previous state,
failure, warning, or readback block another platform.

## Required architecture

1. Run `scripts/inspect_snapshot.py` read-only.
2. Show the user the selected platforms, targets and every approved SKU fact.
3. For each authorized platform independently:
   - run its `dispatch_*.py` tool;
   - run its `readback_*.py` tool even when dispatch returned an error, because
     a write may have occurred;
   - classify the two facts using `references/result-classification.md`.
4. Continue to the next platform after any platform failure.
5. Generate one report with a simple label per platform and detailed redacted
   evidence internally.

## Turn readback failures into permanent prevention

Use readback as a measurement boundary, not as a recurring manual repair loop.
When an exact approved fact differs from the provider:

1. Preserve the approved snapshot and sanitized provider observation.
2. Add a failing regression at the lowest deterministic boundary that allowed
   the drift: snapshot projection, payload construction, reuse/convergence, or
   result classification.
3. Fix that boundary so future dispatches cannot emit or accept the same drift.
4. Keep executable readback as the final assertion that the permanent fix
   works against the provider.
5. Record the root cause in the platform reference only after the red test,
   fix, related regression and provider readback all agree.

Never add a provider-specific repair only to the current Offer ID. A confirmed
incident must become an invariant for every later approved offer.

## Inspect the approved snapshot

```powershell
python scripts/inspect_snapshot.py --offer-id <OFFER_ID> --output <SNAPSHOT_JSON>
```

The snapshot must include each selected SKU's seller SKU, option name, cost,
weight, package dimensions and price context, plus images, description and
category. Stop only for a missing or contradictory fact that the requested
provider truly requires. Category-ID absence is a warning when an approved
platform candidate can supply it.

For every selected Shopee regional target, preserve both the CNSC
`global_original_price_cny` and the regional `local_original_price` with its
currency. Losing either price identity is a pre-dispatch contract failure.

## Dispatch and readback tools

The seven tools are deterministic boundaries:

- `inspect_snapshot.py`
- `dispatch_tiktok.py` / `readback_tiktok.py`
- `dispatch_shopee.py` / `readback_shopee.py`
- `dispatch_ozon.py` / `readback_ozon.py`

Dispatch tools organize the approved request, call one endpoint, redact the
response, and report acceptance/write facts. Readback tools call the correct
Miaoshou or official provider read and report observed facts. They never decide
the next action or user-facing result.

The deterministic Python tools own request construction, API transport,
credential redaction, polling and factual readback summaries. The Skill/agent
owns execution order, whether a confirmed recovery rule applies, result
classification, user explanation and promotion of verified incidents into the
platform references. Do not move provider payload assembly into agent prose,
and do not hide policy decisions inside transport scripts.

## Unified command

Inspect first and present the plan. Only after the user authorizes the offer and
platforms, execute:

```powershell
python scripts/publish_approved_product.py publish --offer-id <OFFER_ID> --platform all --repo <PRODUCT_REPO> --execute --report <PRODUCT_REPO>/reports/product-publication/<OFFER_ID>/<REVISION>/<RUN_ID>/report.json
```

Use `--platform tiktok`, `shopee`, or `ozon` for an isolated retry. Authorization
for one platform does not authorize another.

`--report` is mandatory. Its path is immutable and must exactly follow the
offer/revision/run layout above. Never reuse a run directory and never delete a
dispatch, readback, or report fact after a child failure or timeout. The runner
always resolves a Product Center repository (the production repository is the
default when `--repo` is omitted) and passes that exact repository to every
readback, including TikTok exact-identity readback.

The report and stdout contain only the approved v3/v4 snapshot identity summary
and redacted platform facts. Never emit or persist the full snapshot,
`confirmation_token`, raw provider responses, credentials, or image/video URLs
in the report. A child timeout after a dispatch fact was written does not erase
that fact: retain it and continue the platform readback.

## Platform knowledge

Read only the relevant reference before that platform:

- `references/tiktok.md`
- `references/shopee.md`
- `references/ozon.md`

Read `references/incident-patterns.md` before adding a permanent lesson. Never
record an unconfirmed hypothesis as policy.

## TikTok category decision

Treat the approved product type as the semantic authority and the Miaoshou
draft category as an untrusted candidate. Before TikTok dispatch:

1. Read the approved title, description, product type, use, material and
   product images from the snapshot.
2. Query the official category tree independently for every selected site and
   exact shop.
3. Rank leaf candidates by product type and use first, material second, and
   decorative theme or season only as secondary evidence.
4. Query official metadata for the best candidates. A category existing in
   metadata proves only that the ID is recognized; it does not prove semantic
   fitness or that the category is enabled for publishing.
5. Prefer one semantically exact enabled candidate. If TikTok disables that
   exact leaf, consult only the explicit user-approved fallback table in
   `references/tiktok.md`. Accept a fallback only when its official tree node
   is enabled for the exact site and its metadata is valid for the exact shop.
   For approved tablecloth/table-runner products, Kyle authorizes
   `cid=600009` Festive Decoration when exact `cid=600204` is unavailable.
   Do not invent any other broad fallback.
6. If neither the exact candidate nor an approved fallback is available,
   return `CATEGORY_CONFIRMATION_REQUIRED` with the top candidates and ask for
   one main-category decision.
7. Use deterministic code to write the confirmed site category and required
   attributes, then read back the exact draft before dispatch.

Never preserve a Miaoshou-prefilled category merely because metadata recognizes
it. A fallback is valid only because the user explicitly approved that product
family fallback and the official site tree currently permits it.

## User-facing result

Expose only: **发布成功**, **平台处理中**, **部分成功**, or **发布失败**.
Retain dispatch/readback evidence in the report without credentials or raw
provider responses.
