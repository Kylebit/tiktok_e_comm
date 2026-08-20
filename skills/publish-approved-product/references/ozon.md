# Ozon publication contract

Ozon uses the official Seller API and is independent of TikTok and Shopee.
Send every approved selected SKU and read every expected SKU back.

## Current official response shape

- Product identity is `item.id`.
- Product state is inside `item.statuses`.
- Creation is read from `item.statuses.is_created`.
- Validation/moderation/failure facts are read from the corresponding fields
  inside `statuses`.

Do not treat legacy top-level `status`, `is_created`, or `product_id` as the
authoritative current contract. In particular, `product_id` must not replace
`item.id`.

## Confirmed incident: current IDs hid stale listing content

An Ozon item can have a current `item.id` and nonfailed `statuses` while still
containing a title or price from an older product family. Therefore creation
state alone is not verification. Compare each approved SKU's exact platform
title and price, require images, and classify any mismatch as `MISMATCH` even
when the provider identity exists. Fix payload construction before retrying;
do not bless stale content because import was accepted.

The pre-dispatch existing-product shortcut follows the same rule. A non-empty
title, price, or image list is not an exact match. It must compare the title
and price with the approved snapshot and compare the approved/observed image
count before it may skip an import. A stale existing item must enter the
deliberate update path and official readback instead of returning
`already_published`.

## Confirmed incident: approved generic title was rebuilt from the parcel

Symptom: Ozon accepted and created the approved SKU, while official readback
showed the right price and images but a different title whose displayed size
came from the shipping parcel (for example 29x3 instead of 29x90).

Confirmed root cause: the approved-snapshot path labelled the title source as
approved but still ran the legacy generic sticker title generator using
`package_cm`. For non-table-textile products, submit the exact approved Ozon
title. Keep the deterministic per-variant title builder only for approved
table-textile variants. A title mismatch on an existing item requires a
deliberate convergent update and official readback, never a blind duplicate.

## Multi-SKU verification

Query all approved seller SKUs. Verification succeeds only when every expected
SKU has a current `item.id`, is created according to `statuses`, and has no
official failed status. An accepted asynchronous import with incomplete
readback is **平台处理中**, not success.

## Sanitized target evidence

Every Ozon result, including a zero-write profile, localized-copy, or payload
preflight failure, must carry exactly the runner-safe target evidence fields:
`target_label`, `status`, `stage`, `provider_code`, `provider_reason`,
`request_attempted`, `outcome_unknown`, and `external_write_count`. Use a
stable, sanitized reason and code; never retain provider response bodies,
credentials, request headers, or raw URLs. The runner persists this evidence
only in its internal report, so a preflight failure remains explainable while
truthfully recording zero attempted external writes.

## Confirmed incident: table runner fell through to the historical sticker map

Symptom: all approved prices and images reached Ozon, but all variants were
declined because the title/type still described a self-adhesive wall sticker.

Confirmed root cause: approved English copy used `table runner`; the product
classifier recognized `tablecloth` but not `table runner`, so it fell through
to the historical TikTok festive-decoration mapping and generated the sticker
profile.

Required handling: `table runner`, `table runners`, and `table flag` are exact
table-textile evidence and resolve to the approved tablecloth profile before a
historical TikTok category map is considered. Readback must expose the safe
official failure class and error codes (for example `DECLINED` and
`DESCRIPTION_DECLINE`) and must never call such a terminal result processing.
When an existing rejected offer has the wrong category/type, use the existing
official lifecycle reset, re-import all approved SKUs, and verify exact title,
price, images and created state for every SKU.

## Confirmed incident: the correct tablecloth profile still reused sticker copy

Symptom: category recognition selected the tablecloth profile, but the request
title and description still contained self-adhesive wall-sticker language and
used the 20x20 cm shipping parcel as the product size. Ozon declined every SKU
with `DESCRIPTION_DECLINE`.

Confirmed root cause: the approved-snapshot builder selected the category by
profile, then unconditionally called the legacy sticker-only title and
description generator. It also derived displayed product dimensions from
`package_cm` and ignored the approved per-SKU `variant_label`.

Permanent rule: table-textile copy must never pass through a wall-sticker
generator. Bind displayed product dimensions to each approved variant label
(for example 35x140, 35x200, 35x300) and keep `package_cm` exclusively for
shipping dimensions. Reject any tablecloth draft that retains sticker terms.
The regression contract must cover every approved SKU, not only the first one.

## Confirmed incident: delete requires archive first

The official delete endpoint can return `ITEM_IS_NOT_ARCHIVED` for a declined,
not-created item. A category-changing retry must use the current official
`item.id`, call `/v1/product/archive`, confirm the same offer is archived, and
only then call `/v2/products/delete` by offer ID. Direct delete is forbidden.
If archive is rejected or not visible on readback, stop that Ozon retry without
submitting the replacement item.

## Executable asynchronous readback

An accepted import commonly returns `OFFER_VALIDATED` or `IMPORTED` before
creation finishes. The executable readback performs a bounded poll and stops
as soon as all SKUs verify or any terminal mismatch appears. The user must not
have to run another publish merely to discover the final state; polling is
read-only and never resubmits the import.

## Confirmed incident: provider units are integer grams and millimetres

`/v3/product/import` rejects decimal kilograms in the integer `weight` field
(for example `0.1`) with HTTP 400. Convert each frozen SKU parcel exactly at
the provider boundary: kilograms × `1000` to integer grams and centimetres ×
`10` to integer millimetres. Reject a value that is not exactly representable;
never round or reuse one SKU's parcel for another. Readback converts the
official integer units back to the frozen kg/cm representation before
comparison.

## Confirmed incident: this category requires Russian copy

## Exact semantic profile resolution

Resolve an Ozon profile from the frozen main-category semantic and official
tree, type, required attributes, and dictionary values. Do not apply the
fridge-magnet profile to another product. For approved `餐具 > 餐垫、杯垫` /
`coaster` semantics, the current official exact profile is `House & Garden >
Drinking Utensils & Accessories`, `description_category_id=17027926`, type
`Mug Coaster` (`type_id=96376`); required attributes are Brand (85), Model
name (9048), and Type (8229). Any missing, disabled, or ambiguous official
fact is a zero-write blocker.

The frozen-v4 import builder has a closed profile registry: only the confirmed
Fridge Magnet `(17028743, 93785)`, Mug Coaster `(17027926, 96376)`, Wallpaper
`(17028954, 95819)`, and Interior Sticker `(17027906, 91971)` pairs may form an
import payload. The Wallpaper
pair is valid only for the approved wallpaper semantic and the official path
`Construction & Renovation > Wallpaper & Wall Coatings`; its required
attributes are likewise Brand (85), Model name (9048), and Type (8229), with
the official Wallpaper dictionary value `95819`. It must bind the profile category/type and all
required attributes exactly. Attribute 9048 is profile-specific (for example
`0968-mug-coaster` or `0969-wallpaper`), never a hard-coded fridge-magnet suffix. Preserve the
frozen parcel for that SKU at the Ozon boundary: `0.8 cm` is `8 mm`, not a
Shopee envelope-rounded value. A local builder failure is a zero-write
PREPARATION failure; only a submitted provider business rejection is a
DISPATCH attempt.

For the exact frozen `贴饰 > 墙贴` / wall-sticker semantic, the current official
enabled profile is `House & Garden > Decor & Interior`,
`description_category_id=17027906`, type `Interior Sticker`
(`type_id=91971`). Required attributes are Brand (85), Model name (9048), and
Type (8229); the exact Type dictionary value is `91971`. Build attribute 9048
with the stable `interior-sticker` profile suffix. Re-read the tree, attributes,
and dictionary before dispatch; missing, disabled, or ambiguous facts are a
zero-write blocker.

For the exact enabled `House & Garden > Souvenirs and Gifts > Fridge Magnet`
profile (`description_category_id=17028743`, `type_id=93785`), wholly Latin
title and description are declined with `DESCRIPTION_DECLINE` on attributes
4180 and 4191. Produce a digest-bound `ozon-localized-copy/v1` receipt from the
frozen approved semantics before dispatch. It must contain Russian title and
description and must not introduce product IDs, brand claims, or new facts.

Ozon silently removes the multiplication sign from a title: `7 × 7` becomes
`7 7`, which breaks exact readback. Reject `×` before dispatch and use stable
Russian wording such as `7 на 7 см`.

## Confirmed incident: image and parcel facts span three official reads

The first approved image can be returned through `color_image` while the
remaining gallery stays in `images`. Count the ordered union of both roles;
six gallery images plus one `color_image` is seven images, not a missing-image
failure. Provider CDN URLs are not the approved source URLs, so compare the
persisted image-role binding/count rather than URL strings.

`/v3/product/info/list` may omit parcel and description after creation. Read
weight, dimensions, type and attributes from
`/v4/product/info/attributes`, and read the exact description from
`/v1/product/info/description`. Bind all three responses to the same
`offer_id` and authoritative `item.id` before reporting publication success.

After an accepted update, those official reads can briefly expose the prior
stored copy. A nonterminal mismatch after `ACCEPTED` is **平台处理中** until
bounded readback converges; an official failed status is **发布失败**. Never
resubmit merely because the first read is stale.
