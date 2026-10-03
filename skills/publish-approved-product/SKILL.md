---
name: publish-approved-product
description: "Execute round 3 for a product with immutable round-1 facts and round-2 images: bind or update Miaoshou with official readback, freeze the release snapshot, present one final marketplace review, publish TikTok, Shopee and Ozon independently, and retain target-specific readback and confirmed incident lessons."
---

# Publish Approved Product

This Skill owns the entire third round. It may not re-open first-round fact,
category, price, copy, target or image-plan approval, and it may not regenerate
second-round images. It validates immutable snapshot identity and performs
technical preflight, Miaoshou write/readback, release compilation, the one
final marketplace approval, independent platform execution and readback.

Use the approved Product Center snapshot as the only shared input. TikTok,
Shopee and Ozon are independent tasks. Never let one platform's previous state,
failure, warning, or readback block another platform.

Target-scoped recovery is permitted only for one explicitly selected platform.
Every requested label must be unique, belong to that platform, and remain inside
the immutable approved target list. A successful scoped recovery never replaces
the full-product truth: the final UI and handoff must merge the newest result per
approved target and continue to show every unresolved target.

After the Miaoshou COMMON write and official readback, Kyle reviews the complete
frozen candidate for every selected target on the Product Publication frontend
and makes one final marketplace decision there. Once implemented and verified,
a trusted local bridge may carry that frontend decision in a server-verified
approval envelope; its durable receipt is the sole human approval. No separate
conversation confirmation is required. Bind the receipt to the candidate,
snapshot, ordered targets, and approved companion actions. The server must
verify the submitting Windows user and exact review identity through a
protected local channel. The local trust policy accepts programs running as
that verified Windows user without separate approval for each program. A bare
browser click or caller-supplied `user_approved`, `approved_by`, token, or digest
does not prove that identity or create authority. R1 auto-decisions, image
adoption, QA, and Miaoshou sync do not authorize marketplace publication. The
agent must persist the exact frozen revision and plan through the deterministic
Product Center boundary before publication; a missing or stale technical fact
may block execution, but the same unchanged decision must not be requested
again on another page, in a conversation, or for another platform.

At canonical `248a0403`, the legacy marketplace approval POST still accepts
caller-declared approval fields and does not verify a Windows user or a trusted
envelope. Treat business execution as HOLD under the current `identity_only`
runtime gate. Do not submit approval through that POST or turn on business
execution based on this Skill change. A future implementation must prove the
protected channel, server-side user verification, fresh exact review binding,
durable receipt, and fail-closed rejection of ordinary browser POSTs before
the gate can be reconsidered. Recheck the live service identity and gate just
before any business action; a source commit or a local test is not deployment.

## One final-review gate

Load `config/product_publication_autopilot_policy.json` and the confirmed
`references/incident-registry.json` through
`shared_platform.publication_autopilot`. Before requesting marketplace
publication approval, run `scripts/compile_release_candidate.py` for the exact
approved `offer_id + plan_id`. The immutable
`publication-release-candidate/v1` must be `READY_FOR_FINAL_REVIEW`, bind the
exact snapshot, targets, variants, image routes, zero-write simulation, platform
write budgets, the machine-readable product-family quality gate, durable
image/translation/OCR/pricing evidence and confirmed
incident safeguards, and be visible on the Product
Publication frontend. This is the only normal human approval gate. A policy,
bare page button without a verified receipt, prior product approval, image
approval, or Miaoshou sync does not authorize marketplace publication.

The candidate must also contain one digest-bound `companion_actions` review.
It lists every known action needed to finish the approved publication, including
Ozon stock quantity per Model SKU and the warehouse-selection rule, selected
post-publication discount actions and their immutable pricing/selection policy,
and the readback that proves completion. The final approval covers those exact
actions plus bounded technical retries, read-only reconciliation and provider
asynchronous convergence. Do not split them into later approval prompts.
Resolve mutable provider IDs, such as the one current eligible warehouse or
ongoing discount activity, by official read-only lookup under the frozen rule;
that lookup does not create a new business decision.

The zero-write simulation must use `platform-preflight-report/v2`. A top-level
field-presence check is insufficient. Before any provider mutation it must
also prove: the exact per-target TikTok mainland-pickup warehouse semantic is
frozen for every selected TikTok store; the selected Shopee official category
and all required attribute selections are frozen rather than deferred; and
every Model SKU has one valid, unique HTTPS variation-image binding. Any
missing, duplicate or deferred value is a zero-write blocker. Warehouse names
are semantic facts only: runtime still reads the current official warehouse ID
for that exact store and sets every other active local warehouse to zero.

Counts and digests alone are never a complete final review. The exact
digest-bound candidate must contain and the primary frontend panel must show:
every frozen shopper-facing title and description grouped by the targets that
reuse it; every seller SKU, model SKU, specification, cost, parcel weight and
package size; every target's platform, store, locale, category and per-SKU
price (including the complete collapsible calculation evidence when present);
and every exact ordered target image route grouped only when the full route is
identical. The approval control must remain unavailable when this review
manifest is missing. Any change to these fields changes the candidate digest
and immediately invalidates the prior approval. Keep provider identities,
credentials, raw responses and hidden reasoning out of this projection.

When the plan identity contains `:shadow-`, require a frozen
`shadow-formal-conformance/v1` receipt before the candidate can become ready.
It must cover the complete ordered target list and bind per-target copy,
category, price, gallery, description-image and formal-route digests. Gallery
and description images are independent projections but must contain the same
complete ordered set. Also require the first-round
`publication-default-stock/v1` policy and show its per-SKU quantity in the
final review. Missing or drifted conformance is a zero-write blocker; the
formal publication center must never reconstruct these values from a shared
master or an agent conversation.

Keep SKU terminology exact in that review. Miaoshou `itemNum` is the one
product-level Seller SKU and must be shown once. Each sellable variant is
identified by its distinct Model SKU; label that value `SKU ID (Model SKU)` in
the frontend. Never repeat the product-level Seller SKU inside every variant
card or relabel a Model SKU as a second Miaoshou item identity. The variant SKU
IDs must remain unique and cover every frozen variant.

Before compiling that candidate, run the third-round preparation boundary. It
freezes `round2-image-snapshot/v1`, creates or binds a manual-intake Miaoshou
identity when needed, writes the approved common baseline exactly once, reads
it back, and freezes the release handoff. This is technical execution under the
standing policy, not another content approval:

```powershell
.venv\Scripts\python.exe skills\publish-approved-product\scripts\prepare_publication_execution.py --offer-id <OFFER_ID> --execute-miaoshou --finalize-release-handoff
```

For wallpaper or decorative wall stickers, `shared_platform.publication_quality`
must deterministically select the matching Chinese machine-readable product
family pack and fail closed on category drift, fewer than
seven unique target images, wrong localized-image locale, unsupported marketing
claims, or a higher-cost variant priced below a lower-cost variant. These are
deterministic validation errors, not new business-approval prompts.

For an exact `dual-brand:<offer_id>:<identity>` handoff, load and bind all four
durable sidecars before the candidate can become ready:
`workflow-handoff.json`, `dual-brand-publication-handoff.json`,
`brand-image-translation-plan.json`, and `brand-image-translation.json`.
Verify the same Offer, plan and snapshot digest; the exact approved ordered roles
and artifact digests for every target; approved translation tasks, completed
localized assets and source OCR evidence; and every frozen SKU price against
the handoff calculation result. New `publication-price-calculation/v2` rows
must also pass the independent Decimal recomputation boundary: SEA, MX and GB
use distinct formula kinds and retain every cost, logistics, fee/tax rate,
target margin and discount-reserve input. A same-source result comparison is
not formula evidence. Missing evidence or any drift is a zero-write
blocker. `NOT_AVAILABLE` remains visible only for legacy non-exact handoffs and
must never silently qualify a new dual-brand handoff.

The Runner passes the same candidate and platform budget to every executor.
Target-scope drift or a confirmed write count above budget is a contract
failure. Every platform transport must reserve the supplied shared or
target-scoped attempt budget immediately before each provider mutation. Budget
exhaustion stops before the network call. Timeout and unknown outcome consume
the reservation permanently because the provider may already have written.
The ordered reservations, limits and attempt totals must be stored in immutable
`product-publication-report/v3` and projected on the frontend; a post-result
confirmed-write check remains defense in depth.

## Required architecture

1. Require the exact approved `offer_id` and `plan_id`; do not derive either
   from the mutable dashboard.
2. Use `skills/publish-approved-product/scripts/product_center_publication.py`
   as the only production command.
   When more than one local Product Center service exists, verify the exact
   healthy service that loaded the current committed code and pass its URL
   explicitly with `--base-url`. Never rely on the script default while an
   older service is still reachable; compare the report execution identity
   with the intended commit before diagnosing or retrying a result.
3. Let Product Center resolve the frozen v4 snapshot and run each authorized
   platform through its server-owned async Runner and immutable report.
4. Continue to the next platform after any platform failure.
5. Expose only the four-state sanitized summary. Product Center retains the
   detailed redacted evidence and platform readback in its durable report.
6. Allow several exact `offer_id + plan_id` pairs to execute at the same
   time. Allocate a separate durable run and immutable report for every
   product-platform pair. A different product must never wait on a process-wide
   product lock; the same product/platform snapshot remains single-flight.
7. Source mode is provenance, not a publication shortcut. A frozen
   `manual-intake` snapshot may run only the platforms whose preflight is
   complete. Before TikTok, reconcile the exact manual source identity in the
   Miaoshou common collect box and create, populate and read it back when
   absent; never send the Product Center id as though it were a Miaoshou detail
   id. A still-unresolved identity blocks only TikTok and must not block an
   independently ready Shopee or Ozon run.
   When manual intake has no Miaoshou link, the required order is: create one
   Miaoshou common item, populate the approved variants/copy/gallery/description
   media, read the same identity back, persist the binding, and only then enter
   TikTok preparation. Never dispatch TikTok directly from a Product Center-only
   identity.

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

Treat each publication option name as shopper-facing content, never as a raw
supplier key. Before a candidate can become `READY_FOR_FINAL_REVIEW`, reject
names containing Seller/Model SKU IDs, structural supplier delimiters such as
`;` or `【】`, untranslated slash-packed supplier labels, empty values, or
more than 80 characters. Keep the raw variant key separately for lineage.
Corrections after a frozen snapshot must create and approve an immutable
SKU-display-name-only successor; never overwrite the predecessor snapshot.

For every selected Shopee regional target, preserve both the CNSC
`global_original_price_cny` and the regional `local_original_price` with its
currency. Losing either price identity is a pre-dispatch contract failure.
In the v4 frozen snapshot, the regional price row is
`{amount: <local>, currency: <local ISO code>, global_original_price_cny: <CNY>}`
for every Model SKU and selected region. The additional CNY field is Shopee
specific; do not add it to TikTok or Ozon price rows.

## Production Runner and deprecated compatibility tools

`skills/publish-approved-product/scripts/product_center_publication.py` is the
production control wrapper. It sends
only `{offer_id, plan_id}` to one or more of these explicit Runner start routes:

- `/api/product-workspace/publish-tiktok`
- `/api/product-workspace/publish-shopee-global`
- `/api/product-workspace/publish-ozon`

It requires HTTP 202 with `product-publication-start/v1`, verifies the exact
platform/run/report identity, then polls `/api/product-workspace/publication-report`
until `PUBLISHED`, `PROCESSING`, `PARTIAL`, or `FAILED`. A platform failure does
not stop the other platform starts. A lost POST response is never blindly
reposted.

Those four states are provider/business outcomes, not proof that a local
worker is still alive. `PROCESSING` with `readback_completed=true` is a stable
`READBACK_ONLY` result: the command has returned, the current official
readback budget is exhausted, and no local mutation remains. Persist it,
display it as stable provider processing, and allow only a later read-only
reconciliation. Never wait indefinitely for a worker and never repeat the
start/POST merely because the provider has not reached its final state.

After final approval or execution, the frontend must not continue to say
"等待最终审核" or use an empty legacy ReleaseRun as proof that nothing ran.
The digest-bound candidate distinguishes pending, approved and executed. Once
any platform run exists, immutable per-platform publication reports are the
primary execution ledger; compatibility ledgers may remain visible only with
an explicit statement that they do not authorize a retry.

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
count, plus the redacted mutation-budget ledger (platform, limits, attempt
counts and safe operation identifiers). Public reports remain four-state
results, may show that ledger, and strip target evidence and execution identity.
Raw responses, headers, URLs, tokens, exception arguments,
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
destination copy. Official readback accepts English only for PH, requires Malay
for MY, Thai for TH and Vietnamese for VN, and repairs only the exact existing
wrong-language MY/TH/VN item before reading it again. Never create a duplicate
for copy repair. Provider-derived MY/TH/VN copy is not trusted merely because
it contains one target-language token. Require every semantic description line to be
localized (dimension-only lines may remain invariant), reject residual English
phrases in the title except approved Latin material/unit tokens, and preserve
every approved material and every approved finished-size option. Route an
authorized repair through Lingshi text only, update the already identified
regional item in place, and read it back again. Do not invoke any unapproved
fallback provider for repair text.

An authorized regional-copy repair gets exactly one paid Lingshi request. Do
not retry automatically and do not split a failed result into extra paid
line-by-line calls. When the regional item already uses Shopee extended
description media, repair copy atomically: send the localized title, localized
text block, and the exact existing ordered description image IDs in the same
official update. A plain-text update that drops description images is forbidden.

For TikTok and Shopee alike, a populated product gallery does not satisfy the
description-media requirement. Project the complete ordered target image route
into the product description as well, using TikTok/Miaoshou rich `notes` or
Shopee regional `description_info.extended_description`. Final readback must
prove that every description image belongs to that exact target and matches
the target gallery count and order. Plain-text-only descriptions, missing
images, reordered images, and cross-country or cross-brand routes fail closed
and must be shown as a failed target step on the Product Publication frontend.

Before either TikTok or Shopee crosses its first provider-write boundary, run
the deterministic description-media preflight. It must prove non-empty approved
copy and one complete, unique, ordered HTTPS image route for every exact target,
with equal gallery and description counts. A failure is a zero-write target
failure with code `description_media_preflight_failed`; do not continue to a
provider to repair an invalid route later.

For Ozon, the first-round `publication-default-stock/v1` quantity is a frozen
publication fact, not a frontend-only suggestion. After every approved Model
SKU has an authoritative created-listing readback, resolve exactly one active
non-KGT seller warehouse, read the exact FBS stock for every approved offer,
and write all mismatches in one bounded `/v2/products/stocks` batch. A
`PUBLISHED` result requires a second exact warehouse-scoped readback proving
the frozen quantity for every Model SKU. Reserve one shared mutation attempt
for that batch in addition to one import attempt per Model SKU. If the stock
write outcome is unknown, never retry it blindly: read back first, keep the
confirmed write count unknown, and surface reconciliation even when the
desired stock is now visible. Missing or ambiguous warehouse facts, duplicate
stock rows, incomplete SKU coverage, or readback failure cannot be classified
as published.

Provider identities are operational evidence, not public report fields. Persist
TikTok detail/shop identities and Shopee global/region/image identities in the
server-owned run checkpoint before any later fallible action. The frontend may
show only whether the identity is bound; it must not expose the raw external ID.

## Business closure without rewriting platform truth

When the user explicitly ends a product after reviewing the target results,
use the server-owned closure through the loopback-only standard-library client
`scripts/close_product_publication.py`. Supply an explicit base URL and expected
runtime root; `doctor` identifies the service but does not establish business
authority. Follow [the closure reference](references/closure.md) for the
prepare, record and latest commands. Never retry an ambiguous record; reconcile
the expected closure ID and digest against the exact plan's latest result.
The closure references the exact source
reports and keeps their `PUBLISHED`, `PROCESSING`, `FAILED`, or capability block
facts unchanged. A user-accepted manual handoff is a separate resolution, never
a platform success. A closure with processing or failed targets is
`CLOSED_WITH_OPEN_ITEMS` even though the business workflow is complete.

The Product Publication frontend must prefer the latest valid closure as its
completion view: show the target matrix, verified/manual/processing/failed
counts, identity-bound flag, source run identities and manual handoff evidence.
Keep original execution history available behind a collapsed audit disclosure
and hide mutation controls for a closed product. Never overwrite the original
publication or repair reports.

## Production command

After the user authorizes the exact offer, plan and platforms, execute:

```powershell
.venv\Scripts\python.exe skills\publish-approved-product\scripts\product_center_publication.py --offer-id <OFFER_ID> --plan-id <EXACT_PLAN_ID> --platform all --base-url <VERIFIED_PRODUCT_CENTER_BASE_URL> --execute
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
After that gate is satisfied, also add one bounded CONFIRMED row to
`references/incident-registry.json` with its invariant, regression tests, fix
commit and readback requirement. The registry is the machine-readable index;
platform references retain the detailed evidence.

## Canonical Skill parity

Treat the repository directory `skills/publish-approved-product` as the only
canonical Skill source. Check the installed copy before use:

```powershell
.venv\Scripts\python.exe scripts\sync_product_publication_skills.py --check
```

If review authorizes installation, run it explicitly and then check again:

```powershell
.venv\Scripts\python.exe scripts\sync_product_publication_skills.py --install
.venv\Scripts\python.exe scripts\sync_product_publication_skills.py --check
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

For wallpaper, an enabled decorative-sticker fallback may be used only when
the frozen snapshot contains a durable `self_adhesive=true` product claim with
Kyle's conversation-approval evidence. Once that fact is recorded, inherit it
without asking Kyle to approve it again; shopper-facing title or description
wording is not the fact authority. Words proposed by title generation,
selected pending claims, or a visual guess are not verified facts. If the
ordinary wallpaper leaf is disabled and the durable claim is absent, fail
before provider dispatch with zero writes and show the exact missing fact on
the frontend. Always re-read the exact site's current category tree and exact
shop metadata; that technical verification is not a new human approval gate.

Never preserve a Miaoshou-prefilled category merely because metadata recognizes
it. A fallback is valid only because the user explicitly approved that product
family fallback and the official site tree currently permits it.

## User-facing result

Expose only: **发布成功**, **平台处理中**, **部分成功**, or **发布失败**.
Retain dispatch/readback evidence in the report without credentials or raw
provider responses.

## Frontend audit projection is part of completion

Every platform start, preparation outcome, dispatch, readback and final
classification must first be durable in the exact immutable publication report,
then be projected on the Product Publication frontend. The projection must show
the snapshot check, whether a platform request was attempted, readback state,
final classification, per-target status, confirmed external-write count, and
the pre-mutation budget limits, attempts and ordered safe operation names.
Show only sanitized evidence and concise decision results; never expose raw
provider responses, credentials, external item identities or hidden reasoning.

Do not claim the publication workflow is complete unless the frontend resolves
the same `offer_id + revision + plan_id + snapshot_digest` report and displays
its current platform state. A missing or stale frontend projection is a workflow
defect to fix before handoff, not a reason to repeat a platform write.

## One final approval and governed recovery

After exact Miaoshou write/readback, run the zero-write platform preflight and
persist `platform-preflight.json`. Only then compile the final candidate. The
sole human approval must bind `candidate_digest + snapshot_digest + complete
ordered target list`; any digest drift blocks execution pending reconciliation.
A material change to frozen facts, target scope, or companion actions requires
a new complete review; a read-only freshness check does not. No platform
executor may start without the current `final-publication-approval/v1` receipt
and a fresh execution-time validation of its identity and scope.

After that receipt exists, never ask for the same approval again because the
session or agent changed, a provider is still processing, a readback must be
refreshed, a bounded technical retry remains inside the frozen candidate and
budget, or a known companion action still has to converge. Revalidate and reuse
the receipt for those operations.

Require new explicit authority only when the frozen candidate changes, target
scope expands, a paid action falls outside existing paid authority, a new external
write class is introduced, or another commercial fact outside the candidate is
proposed. Changes to SKU, shopper-facing content, price or discount, stock
quantity, or warehouse-selection policy are candidate changes. Do not describe a
technical retry or read-only provider lookup as a new paid or external action.

If a required fact was present in immutable approved lineage but a projection
bug dropped it from a later snapshot, classify that as a system defect. Repair
the projection permanently and create a controlled continuation receipt that
binds the original candidate and approval digests, source evidence digest,
restored fact digest, exact target, operation kind, mutation budget and
`no_scope_expansion=true`. The continuation may resume under the same approval
only when the restored value is exactly reproducible from that lineage and adds
no new commercial choice. Missing, conflicting or newly chosen facts fail
closed and require a new review. Never overwrite the original candidate,
approval, snapshot or final report; persist successor/reconciliation lineage.

Every non-success result must be classified with
`config/publication_error_policy.json`. An unknown write has zero automatic
retries and requires readback reconciliation before any new mutation. A
transient failure may retry only when confirmed writes are zero and only within
the configured budget. Persist the classification and recovery budget in the
target report, then project the same evidence to the frontend.
