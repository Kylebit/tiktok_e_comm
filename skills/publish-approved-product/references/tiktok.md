# TikTok publication contract

## Independent workflow

TikTok consumes the approved snapshot and never reads Shopee or Ozon state.
For each selected store, keep an independent target row. A missing PH target,
an added HomeBloom target, or a failed GB target must not change another store.

## Draft identity

Persist and read one identity per target:

- `target_label` such as `tiktok:LH_PH` or `tiktok:HB_MY`
- Miaoshou `detail_id`
- configured shop identity
- approved offer identity and snapshot digest
- exact selected model SKU set

Never infer a target from list position, title, the first draft, or another
store's `detail_id`.

## Required evidence

For each target verify, when the provider exposes the fact:

- the remembered `detail_id` belongs to that target and approved offer;
- every approved model SKU and option name is present exactly once;
- every model SKU price equals its own approved per-SKU price;
- category equals the approved site candidate;
- the publish request was accepted;
- the Miaoshou receipt or store readback is available.

Provider omissions are evidence states, not automatic mismatches. Keep the
approved per-SKU package dimensions in the snapshot and request. For TikTok
draft readback:

- always require the parent package dimensions to match;
- if all three per-SKU package fields are present, require an exact match;
- if all three per-SKU package fields are absent, record
  `PROVIDER_FIELD_OMITTED` and continue;
- if only some of the three fields are present, fail that target as malformed.

Sites without an official storefront readback may finish as **平台处理中**
after Miaoshou accepts the request. They must not be reported as verified
storefront success.

Before the first store submission, read the current collect-box action status.
If its exact publishable target list is smaller than the approved TikTok target
list, create one fresh TikTok-only draft batch first. Do not publish a partial
old batch and only then discover that other selected stores lack usable drafts.
The status read itself must return HTTP 200 and exactly one TikTok platform row.
Any non-200 response, malformed response, or missing/duplicate TikTok row is a
zero-write preflight failure: do not call the publish endpoint.

## Confirmed rules

- Consume `approved-publication-snapshot/v4` only through the v4 projection
  and executor. Never pass it to `_approved_common`, `_approved_site`,
  `prepare_tiktok_collectbox`, or the legacy collect-box start route.
- Persist a newly returned Miaoshou platform detail ID before category
  preparation or target draft creation. A client idempotency key is not
  provider idempotency; reconcile the official list before any claim retry.
- A `platform_scope=TIKTOK` action contains only TikTok rows. It must not wait
  for, create, or inspect a pending Shopee row.

- Distinguish the first batch from a reimport before dispatch. A pristine
  `READY` action whose TikTok row is `PENDING` with zero attempts and zero
  writes must use the ordinary start request. Only a finished prior batch may
  use `restart_collectbox_action=true` with a new `reimport_request_id`.

- Multi-SKU drafts must verify the complete SKU set, not only the first SKU.
- A failure before one target's dispatch does not block other TikTok targets.
- A target with an unknown transport outcome is not automatically retried.
- Category and price readback are target-specific.
- Each selected shop is an independent execution target. A failed target must
  not prevent another ready target from being submitted.

## Category mapping workflow

Do not accept the current draft `cid` as the category decision. Use this
sequence for every selected site/shop:

1. Read approved product facts: product type, intended use, material, title,
   description and images.
2. Query the official TikTok category tree for that site.
3. Rank leaf candidates by semantic product identity. Product type/use outrank
   seasonal, colour and decorative keywords.
4. Query metadata for the top candidates and the exact shop.
5. Auto-map one enabled, semantically exact candidate whose required
   attributes are supported by approved facts.
6. If the exact candidate is disabled, consult only a confirmed product-family
   fallback below. Require that fallback's official site node to be enabled
   and its exact-shop metadata to be valid.
7. If neither the exact candidate nor an approved fallback is usable, return
   `CATEGORY_CONFIRMATION_REQUIRED`; never invent a broad category.
8. Write the confirmed category and attributes through deterministic code and
   read back the same target before publishing.

### Confirmed fridge-magnet category

- Exact category: `cid=854536` (`冰箱贴`).
- Confirmed site scope: `PH/MY/TH/VN/MX/GB`.
- The confirmation is not a timeless hard-coded entitlement. For every target,
  recursively locate the exact node in the current per-site tree, require it
  to be enabled with the fridge-magnet semantic label, then validate the
  current exact-shop metadata before saving the draft.
- A successful check for one site or shop cannot authorize another. A cached
  Miaoshou category, metadata-only recognition, or the previous run's result
  is insufficient.
- After saving, read the same target-specific `detail_id` and require the exact
  category plus the existing SKU, price, image, weight and parcel checks.

### Confirmed incident: v4 snapshot entered the legacy parser

Symptom: a v4-approved run made a Miaoshou platform claim, then every TikTok
target ended in reconciliation before any target draft identity was saved.

Confirmed root cause: the legacy collect-box preparation path expected
`source_product` and `sku_prices` through the legacy ReleasePlan parser, while
`approved-publication-snapshot/v4` carries frozen product, target and per-SKU
facts under a different contract. The platform claim happened before that
schema mismatch surfaced.

Required handling: project and execute v4 facts directly. Never adapt v4 by
calling the legacy parser or legacy collect-box start endpoint.

### Confirmed incident: client idempotency did not prevent duplicate claims

Symptom: repeating the same claim with the same local idempotency key returned
another accepted Miaoshou platform detail instead of `ALREADY_PRESENT`.

Confirmed root cause: the key protected only the local request ledger; the
provider did not enforce it as provider idempotency.

Required handling: immediately persist the returned platform detail ID before
any later fallible step. If the process loses that identity or the transport
outcome is ambiguous, read and reconcile the official provider list first.
Never repeat the mutation merely because the local key is unchanged.

### Confirmed incident: platform scope created an unrelated dependency

Symptom: a TikTok-only action stayed running after all TikTok work had reached
a terminal state because an unselected Shopee row remained pending.

Confirmed root cause: initial action creation inserted both platform rows even
when `platform_scope=TIKTOK`.

Required handling: create only the explicitly selected platform rows. Existing
attempted or terminal evidence is immutable; cleanup may remove only an
unselected row that is still pristine `PENDING` with zero attempts and writes.

### Confirmed tablecloth/table-runner fallback

- Primary: `cid=600204` (tablecloth/table runner / kitchen linens).
- Approved fallback: `cid=600009` (festive decoration), authorized by Kyle for
  this product family when TikTok disables or omits `600204` for a site.
- Never choose `600009` merely because the title contains holiday words.
- For each site, inspect the official tree in order: use enabled `600204`;
  otherwise use enabled `600009`; otherwise require category confirmation.
- Miaoshou returns `cateTree` as a nested hierarchy. Search recursively through
  each node's `children` by exact `cid`; never assume the requested cid is a
  top-level key. A flat lookup falsely reports both categories unavailable.
- Query exact-shop metadata and read the saved draft back before dispatch.
- When the approved product category deliberately defers to a site-resolved
  candidate, the final publisher must use the exact category already verified
  on that bound `detail_id`. A null plan-level category is not a missing
  category and must not be rejected before the draft read.

### Confirmed incident: nested category tree was treated as flat

Symptom: all selected TikTok stores reached draft creation but ended as
`CATEGORY_CONFIRMATION_REQUIRED`, even though the official site tree contained
enabled `600009`.

Confirmed root cause: production code called `cateTree.get("600009")`. The live
response nests that node below category ancestors, while `600204` is also
nested and disabled. The direct lookup therefore missed both nodes.

Required handling: recursively locate the exact cid, require a literal enabled
node and matching semantic label, then query exact-shop metadata. Preserve this
case as a red-before-green regression test using the production-shaped nested
tree.

### Confirmed incident: approved snapshot rejected a site-resolved category

Symptom: valid TikTok draft identities existed, but the publish endpoint
returned `approved product category has no TikTok projection` before any store
submission.

Confirmed root cause: the immutable snapshot projector only knew the older
wall-sticker category. For approved tablecloth/table-runner facts, preserve a
null per-site category decision so the publisher uses the exact cid already
resolved and saved from that site's official tree. Never replace an approved
fallback `600009` with disabled primary `600204` during final dispatch.

### Confirmed provider behavior: site titles may be localized asynchronously

After an exact site-draft save, TikTok/Miaoshou may localize or normalize the
title for both site drafts and shop drafts. This is not a submission rejection.
For every TikTok target, keep target identity, complete SKU/model set, price,
category, images, weight and parcel facts strict, but do not block submission
solely because the provider-owned title differs.

### Confirmed incident: saved draft materializes asynchronously

Symptom: the save API accepts the exact category, price, SKU, image, weight and
parcel payload, but the first immediate read still returns the previous draft.
A later read of the same `detail_id` contains the exact saved facts.

Required handling:

- retry transient read-only preparation failures in two bounded three-attempt
  windows
  before any write;
- after an accepted save, poll the same draft up to three bounded read-only
  attempts before classifying an exact-field mismatch;
- never issue a second save merely because the first immediate read is stale;
- if a claimed target is still unmaterialized after the first bounded window,
  finish claiming the other independent targets and revisit the same
  `detail_id` once without another create or claim;
- preserve target isolation: one target's timeout must not stop other stores.

### Confirmed incident: child-tool UTF-8 output decoded as Windows GBK

The Skill orchestrator must run deterministic child tools with explicit UTF-8
decoding and replacement for malformed bytes. A locale decoding exception must
never replace the platform's safe diagnostic output.

### Confirmed GB repair-before-submit policy

For `tiktok:GB`, the bound draft must be saved before submission so the exact
approved category, required category attributes, COD setting, delivery option,
size-chart clearing and per-SKU prices are materialized. Direct submission of
an un-repaired GB draft is forbidden. A rejected repair stops only GB; it does
not block another TikTok store.

### Confirmed incident: store-level price overwrote all model SKU prices

Symptom: an approved three-SKU product reached Miaoshou with three variants,
but every variant had the first/store-level price.

Confirmed root cause: the independent publish snapshot retained only one
`expected_price`; the Miaoshou save loop copied it into every `skuMap` row even
though approved pricing already contained exact `model_sku` rows.

Required handling: project `pricing.selected_targets[target].sku_prices` into
an immutable `model_sku -> price` mapping, require the Miaoshou draft model-SKU
set to match exactly, then write and compare each row independently. Never
fall back to a store-level price when a multi-SKU price mapping is present.

### Confirmed incident: opaque variant keys reused the source item ID

Symptom: the approved product had models `0963/0964/0965` and three different
GB prices, but the live Miaoshou draft returned three opaque `skuMap` keys and
the same source item ID in every row's `itemNum`. Matching by `itemNum` could
not identify the approved model and the pre-write preparation failed.

Confirmed root cause: the independent publisher did not use the mature
`skuPropertyList` decoder. The provider's opaque keys encode attribute value
IDs; `itemNum` is not guaranteed to be a model SKU before repair.

Required handling:

1. Preserve approved `variant_key -> model_sku -> price` facts in the snapshot.
2. Prefer an exact normalized raw-key match.
3. Otherwise decode each opaque key through exact `skuPropertyList`
   `attrValueId -> attrValue` relations and bind the resulting complete variant
   signature.
4. Use `itemNum` only when every value is unique and the complete approved
   model set matches exactly.
5. Write the exact model SKU and its own price into each bound row.
6. Never match by list position, title substring, or fuzzy similarity.
7. A local binding failure is a pre-write rejection with `0` write requests;
   it is not an unknown provider outcome and must not be retried automatically.

### Confirmed incident: structural variant delimiters caused a false mismatch

Symptom: the approved variant was stored as `;田园鲜花铺;;`, while Miaoshou's
official `skuPropertyList` decoded the opaque SKU key to `田园鲜花铺`. The
semantic identity was exact, but the binding fell through to the repeated
source `itemNum` and incorrectly reported an SKU mismatch.

Required handling: strip only Miaoshou's structural semicolon delimiters when
comparing complete variant signatures. Keep the original approved key in the
frozen facts and the original provider key in the save payload. Never remove
ordinary punctuation inside an option name, and never fall back to position,
title or fuzzy matching.

### Confirmed incident: saved TikTok draft lacked the exact shop warehouse

Symptom: category, SKU, price, parcel and images were valid, but Miaoshou
rejected a site draft with the exact message that the store warehouse was not
selected.

Confirmed root cause: the independent v4 save replaced `skuMap` without the
provider-required positive provider stock and
`shopIdToWarehouseIdAndStockMap` binding.

Required handling:

1. Bind every approved variant to its exact current provider SKU row first.
2. Preserve the positive provider stock from that exact row; the frozen
   product snapshot must not invent inventory.
3. Reuse one unambiguous warehouse already bound to the exact shop. If absent,
   read the official warehouse list for that shop and select the deterministic
   active/default warehouse using the proven provider ordering.
4. Set `shopIdToWarehouseIdAndStockMap` independently for every SKU and exact
   shop. Never reuse another shop's warehouse.
5. Missing stock, no active warehouse, or multiple observed warehouses is a
   zero-write preparation failure. Do not guess and do not classify it as a
   provider acceptance.

### Confirmed incident: optional-only GB category metadata blocked repair

Symptom: GB returned `UNKNOWN` before submission and the bound draft remained
unchanged. Read-only metadata for category `600009` returned eight attributes,
all with boolean `isMandatory=false`.

Confirmed root cause: the transport required at least one mandatory category
attribute and raised before calling the draft-save endpoint. The publisher then
misclassified that pre-write preparation error as an unknown write.

Required handling:

- An exact metadata response with zero mandatory attributes is valid; save
  `productAttributes=[]`.
- If a mandatory attribute exists, it still needs one unambiguous legal value.
- Metadata parsing and read-only metadata request failures occur before the
  draft-save boundary and must be a zero-write preparation rejection.
- Do not classify them as `UNKNOWN`, and do not count a save request that was
  never sent.
- Preserve a true `UNKNOWN` only after a mutation request may have crossed the
  transport boundary. The Skill must never downgrade an explicit target
  `UNKNOWN` to `REJECTED` or mark it safe to retry.

### Confirmed incident: local success ledger was reported as storefront success

The local TikTok ledger records Miaoshou draft/submission outcomes. It is not
official storefront readback. A local `SUCCEEDED` entry may support a
"platform processing" result, but must not become `VERIFIED` or "published
success" without an authoritative storefront read.

The executable draft readback must resolve the durable target-specific
`detail_id`, rebuild the immutable approved TikTok snapshot, and run the exact
Miaoshou draft preflight. Only an exact draft plus a successful local
submission may verify the fields Miaoshou exposes. If only the local ledger is
available, keep price/category/variant verification `UNVERIFIED`; never mark it
passed by inference.

### Confirmed incident: scoped GB retry read back all approved stores

Symptom: a GB-only dispatch was explicitly accepted, but the Skill readback
reported six expected targets and listed all approved TikTok stores as
unavailable.

Confirmed root cause: `readback_tiktok.py` iterated the original approved plan's
full TikTok target list instead of the target rows returned by the actual
scoped dispatch fact.

Required handling: when the dispatch fact contains target rows, use their
unique approved labels as the exact readback scope. Fall back to the full
approved target list only for legacy facts with no target rows. Never let a
store that was not attempted in this run distort its result or retry advice.

### Confirmed incident: retrying one unknown store resubmitted accepted stores

When a multi-store run has already returned formal Miaoshou acceptance for
some targets, retrying the complete approved TikTok set can duplicate those
submissions. A retry may therefore carry an explicit non-empty target scope,
but every scoped label must still belong to the immutable approved plan. The
server rebuilds the approved snapshot and filters it to that exact scope; the
client cannot inject draft identities or unapproved stores. A scoped retry
must not create a fresh all-store draft batch. If its exact existing draft is
not ready, report the preparation failure without touching the other stores.

### Confirmed incident: draft verification overrode a rejected dispatch

TikTok collect-box readback verifies draft identity, SKU, category and price;
it is not storefront publication evidence. If the final dispatch is rejected,
the Skill must report failure even when every draft is verified. Never allow a
fresh or historical draft receipt to override a rejected publish request.

### Confirmed incident: delivery option omitted from an otherwise valid draft

Miaoshou requires `deliveryOptionSetType` on both site and shop TikTok drafts;
the documented supported value is `default`. Category and price equality alone
does not make a draft publishable. Treat a missing or non-default value as a
repair requirement, save `default` on that exact draft, then submit the target.
This check is per target and must not stop the remaining stores.

### Confirmed incident: independent GB repair omitted parcel facts

Symptom: the bound GB draft, category, SKU identities and per-SKU prices were
valid, but Miaoshou rejected the repair before saving with
`shopCollectItemInfo.packageLength` required.

Confirmed root cause: the independent TikTok snapshot projected price and
identity facts but omitted the approved parent parcel and per-SKU parcel facts.
The independent transport consequently sent neither the parent
`weight/packageLength/packageWidth/packageHeight` nor each SKU row's own parcel
values. This was separate from the older one-click provider-omission rule.

Required handling:

1. Bind the approved parent `weight_kg` and `package_cm` into the immutable
   TikTok snapshot.
2. Bind every approved `variant_key` to its exact per-SKU weight and package
   dimensions; multi-SKU coverage must be complete and exact. For a legacy
   single-SKU approved plan with no duplicate per-SKU parcel object, bind the
   approved parent parcel to the only variant rather than blocking publication.
3. Write the parent parcel and each bound SKU row's own parcel fields in the
   repair request together with its own model SKU and price.
4. A malformed or incomplete approved parcel is a pre-write zero-write
   rejection. A provider draft missing its parent parcel requires repair, not
   an `UNKNOWN` outcome.
5. After an accepted save, apply the existing bounded readback policy. Do not
   issue another save merely because provider materialization is delayed.

### Confirmed incident: unsupported size-chart URL blocks submission

Miaoshou validates a non-empty size-chart URL even when the approved product
does not require a size chart. A legacy `.gif` value is rejected because only
JPG, JPEG, PNG and WEBP are accepted. For this workflow the approved snapshot
contains no size chart, so any residual `sizeChart` or `sizeChartType` value is
stale provider data: clear both on the exact target draft before submission.

### Confirmed incident: accepted drafts were reported as six readback failures

Symptom: all six store submissions returned formal Miaoshou success, but the
post-submit readback marked every target failed and a later retry wanted to
repair every draft again.

Confirmed live projection: after an accepted save and submit, Miaoshou keeps
the exact category, parent parcel, model SKU, per-model price and per-model
weight, but its read endpoint may project `deliveryOptionSetType` as null,
`sizeChartType` as `image` while `sizeChart` remains empty, and omit all three
per-model package dimensions. This is provider normalization, not a content
change.

Required handling: keep the pre-submit matcher strict so missing delivery and
size-chart fields are repaired. Use a separate post-submit matcher that accepts
only these confirmed projection omissions. Parent parcel, every model SKU,
every model price, every model weight and category must still match exactly;
partial dimension omission or any explicit different value remains a failure.
