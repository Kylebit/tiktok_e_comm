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
