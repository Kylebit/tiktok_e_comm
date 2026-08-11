# Shopee global-product publication contract

Shopee consumes the approved snapshot independently. First create or converge
the Shopee CNSC global product. Publish regional storefronts only when the user
explicitly authorizes those targets, and treat every storefront as an
independent operation.

## Frozen-v4 executor boundary

The executable CNSC master path consumes `approved-publication-snapshot/v4`
and its exact `shopee_global_master` only. It must not read the mutable Product
Center dashboard or parse a legacy ReleasePlan after approval.

Before the first provider write it must:

1. Resolve an exact local mapping for every frozen Model SKU, or confirm that
   none of the Model SKUs is mapped. Partial and ambiguous mappings are a
   zero-write conflict.
2. Read an existing mapped global item from Shopee. Reuse it only when the
   official item and model facts are exact. Retire an official `DELETED`
   identity before rebuilding.
3. For a new item, use official CNSC category recommendation/tree, required
   attributes and merchant warehouse-location reads. A deferred category must
   resolve uniquely; missing required attributes or warehouse identity blocks
   before upload.

Checkpoint provider identities in this order: uploaded image IDs, returned
`global_item_id`, then returned global model IDs. Each checkpoint happens
immediately after its provider call and before the next fallible operation.
An exception after a write must retain the known identities and an exact or
unknown write count; it must never be reported as a zero-write safe retry.

The resolver returns a `global_item_id` only after official item and model
readback proves `NORMAL`, exact title, description, ordered images, parcel
envelope, full Model-SKU coverage, exact variation dimensions/options, every
CNY model price and every variant image. Regional publication begins only
after this resolver returns.

## Dispatch

Send the approved English title and description, ordered images, every selected
variant, exact option names, model SKUs, per-SKU parcel facts and approved global
price. Do not obtain these facts from TikTok.

## Official readback

After dispatch:

1. Obtain the returned or locally mapped `global_item_id`.
2. Call the official `get_global_item_info` API.
3. Call the official `get_global_model_list` API.
4. Check official state, exact title and description, images, complete model
   SKU set, exact variation options, each variation option image, each model
   price and the approved global parcel envelope.
5. Only an existing non-deleted product with exact required facts is verified.

## Confirmed incident: a complete model set hid commercial drift

Symptom: all approved Model SKUs existed, but model prices repeated the first
SKU price or the global parcel retained the first SKU's smaller weight.

Confirmed cause: reuse checked only the Model-SKU set and copy. New creation
also preferred the first model's parcel even though CNSC exposes parcel fields
at global-item level.

Permanent handling:

1. Preserve every approved model price and per-SKU parcel in the snapshot.
2. Use the maximum approved weight and the per-dimension maximum as the CNSC
   global-item parcel envelope; retain per-SKU parcel facts internally.
3. Compare every official model price before reusing an existing global item.
4. Compare the official master parcel before reporting convergence.
5. Replace or update an inexact global item, then perform official readback.
6. Never report success solely because the Model-SKU set is complete.

## Confirmed incident: local mapping exists but official product is deleted

Symptom: the create endpoint or local map reports success, but the Shopee
backend has no usable product.

Confirmed cause: local `global_sku_map` retained a `global_item_id` whose
official state is `DELETED`.

Forbidden behavior: never report success from a local mapping alone.

Recovery order:

1. Perform official readback of `global_item_id`.
2. If state is `DELETED`, retire the stale mapping.
3. Recreate the global product from the same approved snapshot.
4. Save the new `global_item_id`.
5. Perform official readback again.
6. Report success only after the new product and complete variant set verify.

The deterministic readback tool reports `stale_local_mapping=true`; the Skill
chooses the recovery. A dispatcher or readback tool does not invent policy.

## Global product to regional shop product

Treat every PH/MY/TH/VN shop as an independent explicit task. A failure in one
shop must not stop another shop and must not alter the global product.

Use `dispatch_shopee_regions.py` only after the global product's official
readback succeeds. Pass its immutable dispatch fact to
`readback_shopee_regions.py`; never infer regional success from global-product
creation or from an accepted regional task alone. Unselected regions are not
called.

For each selected shop:

1. Read the exact global master and `get_global_model_list`; retain every
   Model SKU, tier index and variation image.
2. Read the target shop identity and `get_channel_list`; select only logistics
   channels compatible with the approved parcel.
3. Build one legacy CNSC `create_publish_task` request with `global_item_id`,
   `shop_id`, `shop_region`, the exact approved title/description/item SKU,
   target logistics, one local-price row for every approved model tier, and
   `item_status=UNLIST`.
4. Save the returned `publish_task_id` as the submission identity.
5. Poll `get_publish_task_result` until `success` or `failed`; a timeout or
   malformed response is an unknown accepted outcome, never a safe resubmit.
6. On success, obtain the regional `item_id`, read its initial official state,
   and list an `UNLIST` item with `/api/v2/product/unlist_item` and
   `unlist=false`. A listing transport exception has an unknown write result,
   so read the item again instead of blindly repeating it.
7. Enable applicable logistics if Shopee created them disabled, and read
   `get_item_base_info` plus `get_model_list` again.
8. Resolve the regional item back to the same `global_item_id` and verify item
   state, all Model SKUs, each local price/currency, logistics, copy and images.
9. Report success for that shop only after the regional official readback.

The approved snapshot must retain both price identities for every target:

- `global_original_price_cny` is the CNSC global-product price lineage;
- `local_original_price` plus its three-letter currency is the regional
  publish-task price.

Never substitute the CNY amount for the regional amount and never drop the
regional currency while projecting the approved snapshot.

The frozen v4 representation is one per-model regional row:
`{amount, currency, global_original_price_cny}`. `amount` and `currency` are
the exact local publish values; `global_original_price_cny` is the exact CNSC
model-price lineage. Missing either side blocks that region before any write.

Do not reuse the legacy single-model regional helper for a multi-SKU product:
it prepares one `model_sku` and one local model price. The regional Skill tool
must expand the complete approved model set before this operation is enabled.

## Confirmed incident: global mapping prefilled four regional successes

Symptom: a newly created global item appeared locally as published in
PH/MY/TH/VN before any regional `create_publish_task` or official shop-item
readback had run.

Confirmed cause: `upsert_global_entry` treated an explicit empty
`published_regions=[]` as false and replaced it with all four regions; the
multi-SKU group upsert also hard-coded all four regions.

Permanent handling:

1. A new global mapping always starts with an empty regional set.
2. Regional dispatch never edits `global_sku_map`.
3. An accepted `publish_task_id` is submission evidence, not publication.
4. Add a region only after `get_publish_task_result` succeeds and official
   item/model/global-linkage readback matches the approved SKU and price facts.
5. Use `record_shop_item` as that sole verified mutation boundary.
6. Global-only publication must leave `published_regions` empty.

## Confirmed incident: regional linkage readback used incomplete token metadata

Symptom: the regional item, Model SKU, local price, images, description and
logistics all read back correctly, but `get_global_item_id` returned no linkage
for PH/TH/VN while MY succeeded.

Confirmed cause: the readback obtained `merchant_id` directly from the local
token JSON. The MY entry happened to contain it; PH/TH/VN did not. Dispatch had
correctly resolved the same merchant through official shop information, so the
readback called the official endpoint with merchant ID zero.

Permanent handling:

1. Resolve each shop token normally.
2. Read the official shop information and obtain its merchant identity.
3. Resolve the merchant access token through the same runtime path used by
   dispatch.
4. Call `get_global_item_id` with that resolved merchant identity.
5. Never classify missing token-file metadata as missing global linkage.

Offer 3838608018 verified this rule across PH/MY/TH/VN: all four regional items
resolved to global item `48715697978` after official merchant resolution.

## Confirmed incident: regional create rejected direct NORMAL publication

Observed on Offer `3882722296` / SKU `0967` for the PH storefront:

- the full legacy CNSC regional body with `item_status=NORMAL` returned
  `product.error_param / parameter invalid`;
- the newer `shop_list`-only body was parsed but returned
  `product.error_busi / record not found` for this merchant mode;
- the same complete legacy body with `item_status=UNLIST` was accepted as task
  `202608111429373276` and created item `46465822597`;
- `/api/v2/product/unlist_item` with `unlist=false` then moved that exact item
  to official `NORMAL` state.

Permanent handling:

1. Keep the full legacy request shape for this CNSC merchant: approved copy,
   parent SKU, all model tiers/prices and compatible logistics.
2. Create the regional item as `UNLIST`; never request direct `NORMAL` in the
   create task.
3. Poll the task and persist its exact task/item identity before listing.
4. List the returned item through the official shop endpoint, then re-read it.
5. If the listing response is lost, trust only the second official item read;
   never blindly send the listing write again.
6. Only official `NORMAL` plus exact global linkage, model SKUs, model prices,
   tiers, logistics, copy and images is PUBLISHED.
7. For an official modeled item (`has_model=true`), Shopee may return an empty
   parent `item_sku`. Accept that omission only when the complete official
   Model-SKU set is exact; otherwise the SKU identity remains a mismatch.
8. On a later run, an exact stored regional item identity must enter official
   readback directly with zero create writes. Never create a duplicate merely
   because the prior run has already finished.

## Confirmed incident: regional numeric prices were serialized as strings

Observed on Offer `3882722296` for MY: the exact approved regional task was
rejected as `product.error_param / parameter invalid` while both
`item.original_price` and `item.model[].original_price` were JSON strings.
Changing only those two fields to JSON numbers made Shopee accept task
`202608111429426328`; official item `46615817790` then passed listing and exact
readback.

Permanent handling:

1. Keep monetary values as exact decimals inside the frozen snapshot.
2. At the provider adapter boundary, serialize regional item and model prices
   as finite positive JSON numbers, never numeric strings.
3. Do not change the approved amount or currency while changing its JSON type.
4. Regression-test integral and fractional prices; every model row must remain
   independently priced.
5. A provider acceptance is still not success: list and officially read back
   the exact regional item and models.

## Confirmed incident: shop-item logistics are a provider-selected subset

Observed on Offer `3882722296`: the preflight shop-channel list contained
24/14/19 compatible candidates for MY/TH/VN, but the created regional items
exposed only 12/5/7 applicable channels. Shopee also added and enabled VN
channel `50052`, which was not safe to send in the create task. Requiring the
final item to reproduce the preflight set therefore created false mismatches.

Permanent handling:

1. Use official preflight channels only to ensure the create task has at least
   one parcel-compatible option.
2. After creation, treat the exact item `logistic_info` as the authoritative
   applicable set; Shopee may remove candidates or add a default channel.
3. Enable every disabled channel returned on that exact item, one at a time,
   preserving provider rejections and unknown write outcomes.
4. Re-read the item and require at least one well-formed logistics row and all
   returned rows enabled. Do not require equality with the preflight set.
5. This relaxation applies only to logistics identity. SKU, model price,
   copy, images, status and global linkage remain exact checks.

## Confirmed incident: an unrelated recommendation blocked the exact category

Symptom: Shopee officially recommended `Fridge Magnets` first for an approved
fridge-magnet product, but preparation failed before creation.

Confirmed cause: the observer parsed every recommendation's attribute tree.
An unrelated `Refrigerators` candidate exposed a provider attribute shape the
old parser rejected, so that unrelated candidate erased the valid first
candidate.

Permanent handling:

1. Start only from the frozen, user-approved semantic main category.
2. Read every recommended official category path and discard non-leaf rows.
3. Select exactly one publishable leaf by an explicit semantic alias; never
   infer the category from title wording alone.
4. Read and validate the attribute tree only for that selected leaf.
5. An invalid unrelated candidate must not block the exact selected leaf.
6. For Offer 3882722296 the exact result is official CNSC category `101398`,
   `Hobbies & Collections > Souvenirs > Fridge Magnets`; it currently has no
   mandatory attributes. Re-read these official facts for every run.

## Confirmed incident: global variant exists without an option image

Symptom: the global product and Model SKU exist, but the variation image cell
is empty in Shopee Seller Center.

Confirmed cause: the approved publisher uploaded master images but initialized
or reused `tier_variation.option_list` with only `option`; it neither attached
`image.image_id` nor verified that field through `get_global_model_list`.

Permanent handling:

1. Preserve a deterministic approved image position per variant. For a
   one-variant product, position 0 is the approved main image.
2. Resolve that position to the uploaded Shopee master `image_id`.
3. Send `image.image_id` in `init_tier_variation` or repair it through
   `update_tier_variation`.
4. Read `tier_variation.option_list` from `get_global_model_list`.
5. Report verified only when every approved option has a non-empty image;
   for one variant, it must equal the first approved master image ID.
6. A complete SKU set and populated master image list do not prove variant
   images are complete.

## Confirmed incident: non-preorder DTS zero is rejected

Symptom: CNSC `add_global_item` rejects an otherwise valid global product with
`product.error_busi` and says Days to Ship must be 1, or 4 through 10.

Confirmed cause: the frozen global policy used
`{is_pre_order: false, days_to_ship: 0}`. Shopee does not accept zero for a
non-preorder global item.

Permanent handling:

1. Freeze non-preorder CNSC policy as
   `{is_pre_order: false, days_to_ship: 1}`.
2. Validate that exact provider-required value in the approved v4 snapshot.
3. Never silently translate an approved zero inside the transport adapter;
   generate and approve a successor snapshot instead.
4. A provider rejection creates no global item. Reconcile the official global
   list by every approved Model SKU before retrying.
5. Preserve any already-returned image IDs in the run checkpoint so a safe
   continuation does not upload the same approved images again.

## Confirmed incident: model initialization response contradicted official state

Observed on Offer `3882722296` / SKU `0967`:

- `init_tier_variation` returned `product.error_param` with `The level of
  tier-variation not change.`
- authoritative `get_global_item_info` nevertheless returned `has_model=true`;
- authoritative `get_global_model_list` returned the exact SKU, tier option,
  CNY price, parcel and variant-image identity.

Permanent handling:

1. Treat the immediate mutation response and official readback as separate
   facts.
2. After any model-initialization rejection or transport exception, read the
   exact `global_item_id` before deciding whether the write failed.
3. If official Model SKU coverage and identities are exact, converge and do
   not send `init_tier_variation` again.
4. If the readback is missing, partial or mismatched, preserve an unknown or
   reconciliation-required result; never blindly retry the write.

## Confirmed incident: provider CDN URLs caused a false image mismatch

Observed on Offer `3882722296` / SKU `0967` after the global item and Model
were already complete: official readback returned provider CDN image URLs,
while the frozen snapshot retained the original source URLs. The old verifier
compared those URL strings and falsely reported content drift.

Permanent handling:

1. Persist the exact source-URL-to-Shopee-`image_id` binding immediately after
   each accepted upload.
2. On continuation, recover that exact binding from the offer/revision run
   checkpoint; do not upload the images again.
3. Compare the official ordered master `image_id` list and every variation
   option `image_id` with the persisted binding.
4. Treat provider CDN URLs as delivery locations, not approved image identity.
   Never require a provider CDN URL to equal the original source URL.
5. Missing, duplicate, reordered or mismatched image IDs remain hard failures.
   If the exact persisted binding is unavailable, require reconciliation rather
   than accepting an image count alone.

## Confirmed incident: global Model status is absent from the official response

Observed on Offer `3882722296` / SKU `0967`: official
`get_global_model_list` returned a positive `global_model_id`, exact SKU, tier,
price, stock and option image, but no per-Model status field. The parent global
item itself was `NORMAL`. Requiring a synthetic Model `NORMAL` value falsely
rejected the exact official state.

Permanent handling:

1. Require the parent global item status to be `NORMAL`.
2. Require every approved Model SKU to have one unique positive official
   `global_model_id` and exact tier, price and variation-image identity.
3. If a Model status field is present, require `NORMAL`; if the official
   endpoint omits it, do not invent one or reject the Model solely for that
   omission.
4. Missing or ambiguous official Model identity remains a hard failure.
