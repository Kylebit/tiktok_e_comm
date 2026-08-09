# Shopee global-product publication contract

Shopee consumes the approved snapshot independently. First create or converge
the Shopee CNSC global product. Publish regional storefronts only when the user
explicitly authorizes those targets, and treat every storefront as an
independent operation.

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
3. Build one `create_publish_task` request with `global_item_id`, `shop_id`,
   `shop_region`, requested item status, target logistics, and one local-price
   row for every approved model tier.
4. Save the returned `publish_task_id` as the submission identity.
5. Poll `get_publish_task_result` until `success` or `failed`; a timeout or
   malformed response is an unknown accepted outcome, never a safe resubmit.
6. On success, obtain the regional `item_id`, enable applicable logistics if
   Shopee created them disabled, and read `get_item_base_info` plus
   `get_model_list`.
7. Resolve the regional item back to the same `global_item_id` and verify item
   state, all Model SKUs, each local price/currency, logistics, copy and images.
8. Report success for that shop only after the regional official readback.

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
