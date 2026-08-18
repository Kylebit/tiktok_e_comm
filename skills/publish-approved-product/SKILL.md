---
name: publish-approved-product
description: "Execute stages 05-07 for an approved Product Center offer through three independent workflows: inspect the approved per-SKU snapshot, dispatch TikTok through Miaoshou, create a Shopee global product, publish Ozon through the official API, perform platform-specific readback, classify truthful results, and retain only confirmed incident lessons. Use when the user asks to publish, retry, inspect, or diagnose an already-approved Offer ID after stages 01-04."
---

# Publish Approved Product

Use the approved Product Center snapshot as the only shared input. TikTok,
Shopee and Ozon are independent tasks. Never let one platform's previous state,
failure, warning, or readback block another platform.

## Required architecture

1. Require the exact approved `offer_id` and `plan_id`; do not derive either
   from the mutable dashboard.
2. Use `scripts/product_center_publication.py` as the only production command.
3. Let Product Center resolve the frozen v4 snapshot and run each authorized
   platform through its server-owned async Runner and immutable report.
4. Continue to the next platform after any platform failure.
5. Expose only the four-state sanitized summary. Product Center retains the
   detailed redacted evidence and platform readback in its durable report.

## Frozen v4 execution boundary

Treat `approved-publication-snapshot/v4` as the only production input for a
new publication run. Send it only through the v4 platform executors. Never
feed it into a legacy ReleasePlan parser or a legacy collect-box start route;
those readers expect different fields and may claim a provider object before
failing to prepare any target drafts.

For a provider create/claim call, a client idempotency key is only local
evidence unless the provider explicitly guarantees idempotency. Persist the
returned platform detail ID before category preparation, target creation, or
any other fallible step. Before retrying a call whose result is missing or
ambiguous, reconcile the official provider list and bind the exact existing
identity. Never retry a claim merely by reusing the client key.

Keep platform scope structural: a TikTok-only run may create only TikTok rows,
and a Shopee-only run may create only Shopee rows. Never create pending rows or
completion dependencies for unselected platforms.

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

Use the Product Center-approved plan and its exact identity. The production
command must not call a dashboard endpoint or rebuild a snapshot. Product
Center binds `offer_id + plan_id` to the immutable v4 snapshot before a run is
queued. Use `inspect_snapshot.py` only to diagnose old compatibility data; its
output is never a production publication input.

The snapshot must include each selected SKU's seller SKU, option name, cost,
weight, package dimensions and price context, plus images, description and
category. Stop only for a missing or contradictory fact that the requested
provider truly requires. Category-ID absence is a warning when an approved
platform candidate can supply it.

For every selected Shopee regional target, preserve both the CNSC
`global_original_price_cny` and the regional `local_original_price` with its
currency. Losing either price identity is a pre-dispatch contract failure.
In the v4 frozen snapshot, the regional price row is
`{amount: <local>, currency: <local ISO code>, global_original_price_cny: <CNY>}`
for every Model SKU and selected region. The additional CNY field is Shopee
specific; do not add it to TikTok or Ozon price rows.

## Production Runner and deprecated compatibility tools

`product_center_publication.py` is the production control wrapper. It sends
only `{offer_id, plan_id}` to one or more of these explicit Runner start routes:

- `/api/product-workspace/publish-tiktok`
- `/api/product-workspace/publish-shopee-global`
- `/api/product-workspace/publish-ozon`

It requires HTTP 202 with `product-publication-start/v1`, verifies the exact
platform/run/report identity, then polls `/api/product-workspace/publication-report`
until `PUBLISHED`, `PROCESSING`, `PARTIAL`, or `FAILED`. A platform failure does
not stop the other platform starts. A lost POST response is never blindly
reposted.

The following scripts are deprecated compatibility and diagnostics only:

- `inspect_snapshot.py`
- `dispatch_tiktok.py` / `readback_tiktok.py`
- `dispatch_shopee.py` / `readback_shopee.py`
- `dispatch_shopee_regions.py` / `readback_shopee_regions.py`
- `dispatch_ozon.py` / `readback_ozon.py`

Do not use those deprecated direct scripts for a new production run. They may
support historical incident reproduction, but they read the old mutable data
shape and do not own the frozen-v4 async lifecycle.

Server-owned frozen-v4 executors own provider request construction, transport,
credential redaction, polling and readback. The thin Skill client owns only
the exact start identity, independent platform order, public-report polling and
sanitized four-state projection. Do not move provider payload assembly into
agent prose or into this client.

At run creation, Product Center freezes the canonical repository Skill
manifest digest, exact Git commit, and a content digest of the production
execution files for the selected platform. Dirty execution code therefore
changes identity even when the commit is unchanged. The worker verifies this
identity before RUNNING or provider dispatch; drift is a durable zero-write
failure and never triggers an implicit Skill install.

The immutable internal report may retain only the fixed sanitized target
evidence fields: target label, status, stage, safe provider code, redacted
reason, request-attempted flag, unknown-outcome flag, and confirmed write
count. Public reports remain four-state counts and strip target evidence and
execution identity. Raw responses, headers, URLs, tokens, exception arguments,
and external item identities are forbidden.

HomeBloom SEA stores are TikTok targets owned by the Miaoshou Open API path,
not Shopee regional targets and not direct TikTok API targets. When the frozen
snapshot selects `tiktok:HB_PH`, `tiktok:HB_MY`, `tiktok:HB_TH`, or
`tiktok:HB_VN`, keep each as an independent execution target bound to its exact
HomeBloom shop identity. The executor must not use the TikTok official API for
these stores, must not substitute the same-region LivelyHive shop, and must not
collapse the four targets into one shared result.

For Shopee, finish and verify the global product first. Then, only when the
approved snapshot explicitly selects `shopee:PH`, `shopee:MY`, `shopee:TH`,
or `shopee:VN`, the server-owned Shopee executor handles regional dispatch and
readback after Global verification. Treat every selected region independently.
A global-only run has zero regional targets. Only exact official shop-item,
model, price and global-linkage readback may record that a region is published.
Keep the approved English copy on the verified Global master. Regional create
requests must omit `item_name` and `description` so Shopee can derive the
destination copy. Official readback accepts English for PH/MY, requires Thai
for TH and Vietnamese for VN, and repairs only the exact existing wrong-language
TH/VN item before reading it again. Never create a duplicate for copy repair.

## Production command

After the user authorizes the exact offer, plan and platforms, execute:

```powershell
python scripts/product_center_publication.py --offer-id <OFFER_ID> --plan-id <EXACT_PLAN_ID> --platform all --execute
```

Use `--platform tiktok`, `shopee`, or `ozon` for an isolated retry. Authorization
for one platform does not authorize another.

Product Center, not the Skill client, allocates the run identity and writes the
immutable report under `reports/product-publication/<offer>/<revision>/<run>`.
The client validates `product-publication-start/v1`, polls the exact returned
`publication-report:<run_id>`, and emits no full snapshot, confirmation token,
raw response, credential, URL, external item ID, or mutable dashboard fact.

`publish_approved_product.py` and the direct `dispatch_*.py` / `readback_*.py`
scripts are deprecated compatibility only. Never invoke them as the production
path for a new approved offer.

## Platform knowledge

Read only the relevant reference before that platform:

- `references/tiktok.md`
- `references/shopee.md`
- `references/ozon.md`

Read `references/incident-patterns.md` before adding a permanent lesson. Never
record an unconfirmed hypothesis as policy.

## Canonical Skill parity

Treat the repository directory `skills/publish-approved-product` as the only
canonical Skill source. Check the installed copy before use:

```powershell
python scripts/sync_publish_approved_product_skill.py --check
```

If review authorizes installation, run it explicitly and then check again:

```powershell
python scripts/sync_publish_approved_product_skill.py --install
python scripts/sync_publish_approved_product_skill.py --check
```

Never install implicitly during publication, test execution, or Skill
validation. A parity mismatch is a deployment/configuration failure; do not
silently mix canonical instructions with installed scripts from another
digest.

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
   For approved table-mat/placemat/coaster and tablecloth/table-runner products,
   Kyle authorizes direct use of `cid=600009` Festive Decoration. Do not first
   select `cid=600033` or `cid=600204`; the live site tree must still show
   `600009` enabled and the exact shop metadata must validate.
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
