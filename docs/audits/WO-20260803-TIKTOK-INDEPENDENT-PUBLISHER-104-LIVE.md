# WO-20260803 TikTok Independent Publisher Live Evidence

Authorized scope: offer `3846511157`, TikTok via Miaoshou only. No Shopee,
Ozon, delete, or other-offer write was made.

## L2

The initial six-target read-only preflight returned READY for PH, MY, TH, VN,
MX, and GB under the original direct-submit policy. Subsequent provider
evidence proved GB needed a category-draft repair, so the final GB-only L2
correctly returned `REPAIR_REQUIRED`.

## L3 round one

- PH, MY, TH, VN, MX: `ACCEPTED` by Miaoshou.
- GB: `REJECTED`, provider code `fail`, safe reason indicated missing category.
- Mutation requests: 6.
- Confirmed external writes: 5.
- Unknown outcomes: 0.
- The GB rejection did not stop the other five targets.

## GB repair investigation and controlled retry

Read-only evidence showed the GB shop draft had a blank category and no
category attributes. Historical proven code showed that category `600338`
also requires the official mandatory `Batch Number` attribute and numeric shop
identity.

After adding the exact repair contract, the GB-only save was explicitly
rejected by Miaoshou with code `fail`; the safe reason named
`shopCollectItemInfo.deliveryOptionSetType` and contained garbled provider text.
The publisher therefore did not call GB submit and did not guess another path.

Historical code showed that an empty delivery option must be normalized to
`default`. A before-change regression failed on the empty value; after the
one-field correction, the next single GB save was explicitly rejected with a
new safe reason: the size-chart image URL format was unsupported. Work stopped
again without calling GB submit or changing another target.

The final GB contract audit proved that this product does not use a size chart:
the inherited draft carried an unsupported image-shaped value while the
approved product facts required neither an image nor a size-chart type. A
before-change regression reproduced that exact leak. The corrected GB payload
now clears both fields while retaining the already-valid no-brand, stock,
warehouse, category, mandatory category-attribute, and approved-price facts.
The single authorized GB-only L3 then returned explicit provider success for
both draft save and `save_move_collect_task` submission.

- Additional mutation requests: 4 (two rejected GB draft-save attempts, then
  one accepted draft save and one accepted submission).
- Additional confirmed external writes: 2.
- Additional unknown outcomes: 0.

## Cumulative external activity

- Read requests: 27.
- Mutation requests: 10.
- Confirmed external writes: 7.
- Unknown business outcomes: 0.
- Delete requests: 0.
- Shopee/Ozon requests: 0.

No credential, raw response, URL, or raw long identifier is stored in this
evidence file.
