# Confirmed incident knowledge

Permanent rules enter this file only through this gate:

1. Stabilize reproduction.
2. Capture code or provider evidence.
3. Confirm one root cause.
4. Add a failing regression test.
5. Fix and verify the focused and related tests.
6. Record the confirmed pattern in the platform reference.

Do not turn a one-off timeout, guessed cause, malformed screenshot, or stale UI
message into a permanent Skill rule. Keep unconfirmed observations in the
current execution report only.

## Confirmed patterns

- Production control path: a new approved offer must start only the three
  explicit Product Center frozen-v4 Runner endpoints with exact
  `offer_id + plan_id`, validate `product-publication-start/v1`, and poll the
  server-owned `publication-report`. The old mutable-dashboard orchestrator
  and direct dispatch/readback scripts are deprecated compatibility only.
- Skill v4 boundary: `approved-publication-snapshot/v4` must never enter the
  legacy ReleasePlan parser or legacy collect-box start route. The schemas are
  not compatible, and the legacy sequence can claim a provider object before
  failing draft preparation.
- TikTok claim identity: a client idempotency key is not provider idempotency.
  Persist the returned platform detail ID before the next fallible step; when
  an outcome is missing or ambiguous, reconcile the official provider list
  before any retry.
- Platform isolation: `platform_scope` must create rows only for selected
  platforms. Pending rows for unselected platforms are forbidden because they
  create false completion dependencies.
- TikTok fridge magnets: the exact official `cid=854536` (`冰箱贴`) was
  confirmed enabled for PH/MY/TH/VN/MX/GB only after an exact per-site tree
  check and exact-shop metadata validation. Revalidate those two facts for
  every new run; never infer availability from one site or a cached draft.

- Shopee: a stale local global-item mapping can point to official `DELETED`;
  local mapping alone is never success.
- Shopee: master images and a complete Model-SKU set do not prove variation
  images. Bind each option to an uploaded image ID and verify it from
  `get_global_model_list.tier_variation.option_list`.
- Shopee image identity: provider readback may replace source URLs with CDN
  URLs. Verify the persisted source-URL-to-image-ID binding against official
  ordered master and variation image IDs; never compare CDN URL strings, and
  never accept count-only evidence when the binding is unavailable.
- Shopee global Model readback may omit a per-Model status field. Require a
  NORMAL parent item plus one unique positive global_model_id and exact facts
  for every SKU; enforce Model NORMAL only when the provider actually returns
  that field.
- Shopee category recommendation: choose one exact publishable official leaf
  from the frozen main-category semantic identity, then read only that leaf's
  attribute tree. A malformed unrelated recommendation must not erase a valid
  exact candidate; never fall back to title guessing.
- Shopee non-preorder global items require `days_to_ship=1`; zero is rejected
  by CNSC `add_global_item`. Freeze the provider-valid value in a successor v4
  snapshot rather than changing it silently inside the transport adapter.
- Ozon: current identity/state facts are `item.id` and `item.statuses`.
- TikTok: store draft identity is target-specific; positional or first-draft
  matching breaks when the selected store set changes.
- TikTok: store-level pricing must not overwrite approved per-model-SKU prices.
- TikTok: opaque draft keys must be decoded through `skuPropertyList`; repeated
  source `itemNum` values are not model identities, and a local binding failure
  is a confirmed zero-write rejection rather than an unknown outcome.
- TikTok: compare approved and provider option signatures after removing only
  structural semicolon delimiters. Preserve both original keys; never use
  position or fuzzy matching when the provider key is opaque.
- TikTok: every saved SKU needs positive provider stock plus an exact-shop
  `shopIdToWarehouseIdAndStockMap`. Reuse one exact existing binding or read
  that shop's official active/default warehouse; never invent stock or reuse a
  warehouse across shops.
- TikTok GB: a category with no mandatory attributes is valid; optional-only
  metadata must not block draft repair, and metadata preparation failures are
  zero-write rejections.
- TikTok GB: the independent approved snapshot and repair payload must retain
  the approved parent parcel plus every SKU's exact weight and dimensions;
  omitting them causes a deterministic zero-write provider rejection such as
  `shopCollectItemInfo.packageLength` required.
- TikTok Skill: preserve an explicit per-target `UNKNOWN`; never convert it to
  `REJECTED` or advertise a safe retry.
- TikTok Skill: a scoped store dispatch must read back only the target labels
  returned by that dispatch, not every TikTok target in the approved plan.
- TikTok Skill: a pristine `READY` plan with a `PENDING`, zero-attempt,
  zero-write TikTok row is the first batch. Start it without
  `restart_collectbox_action` or `reimport_request_id`; requesting a reimport
  before the first batch finishes deterministically returns HTTP 409.
- TikTok: a local Miaoshou `SUCCEEDED` ledger is not storefront verification.
- TikTok: post-submit Miaoshou reads may normalize the empty size-chart and
  delivery fields and omit all per-model dimensions; only a dedicated
  post-submit matcher may accept those confirmed omissions while retaining
  exact category, parent parcel, SKU, model price and model weight checks.
- Ozon: approved `table runner`/`table flag` copy is table-textile evidence and
  must win before a stale festive-decoration-to-sticker category mapping.
- Ozon: the approved generic platform title must not be regenerated from
  shipping `package_cm`; that changes approved wording and can advertise the
  parcel size instead of the product size.
- Ozon: the existing-product shortcut must compare approved title, price and
  image count exactly; merely finding non-empty fields must never return
  `already_published` or suppress a required convergent update.
- Ozon: selecting the tablecloth profile is not enough; table-textile title and
  description must bypass the legacy sticker generator, and product dimensions
  come from the approved per-SKU variant label rather than the shipping parcel.
- Ozon: a declined wrong-category item must be archived by official `item.id`
  before it can be deleted by offer ID; direct delete returns
  `ITEM_IS_NOT_ARCHIVED` and must not be retried blindly.
- All platforms: provider acceptance and official verification are separate
  facts.
- All platforms: an exact readback mismatch is not merely a reason to repair
  the current item. It must identify the deterministic boundary that admitted
  the drift, gain a red regression there, and become a pre-dispatch or
  convergence invariant for every later Offer ID.
- All platforms: local submission ledgers can prove that a request was sent or
  accepted; they cannot prove exact title, variants, prices, parcel, images or
  final provider state.

## Shopee model write looked rejected but was already effective

Symptom: the model-init endpoint returned an error, while an exact official
readback showed that the approved model already existed.

Confirmed cause: provider mutation acknowledgement and provider read model
can disagree. Repeating the mutation then produces a tier-level conflict.

Permanent rule: after any ambiguous or rejected-looking model mutation,
read the exact global item and model identities first. Exact official state
wins; otherwise retain UNKNOWN/RECONCILIATION_REQUIRED and never blind-retry.
