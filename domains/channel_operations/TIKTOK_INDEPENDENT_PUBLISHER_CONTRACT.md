# TikTok Independent Publisher Contract

`TikTokPublisher` is the sole channel-owned TikTok publication boundary. It
does not import or call the legacy one-click control plane, Shopee, Ozon, or
`miaoshou:COMMON`.

## Approved input

The caller supplies `approved-tiktok-publish-snapshot/v2`. The snapshot binds:

- approved offer, plan, product revision, and immutable payload digest;
- only selected TikTok target labels;
- the exact Miaoshou detail/shop identity and its three durable identity
  digests (`target_identity_digest`, `publish_identity_digest`, and
  `receipt_digest`);
- approved target currency, exact `variant_key -> model_sku` lineage, and exact
  `model_sku -> price` mapping (the legacy store-level price is retained only
  for older single-SKU snapshots);
- the approved parent weight/package dimensions plus exact per-variant
  `weight_kg` and `package_cm` facts for every approved SKU. A legacy
  single-SKU plan may bind its approved parent parcel to its only variant;
  multi-SKU plans must carry complete exact per-SKU facts;
- approved product category ID and category evidence digest.

Category is product evidence. There is intentionally no category-by-site
production constant.

## Execution

Each target is independent. A rejection or unknown outcome is recorded for
that target and later targets continue.

1. Read the current target draft.
2. Bind every raw Miaoshou `skuMap` key to the approved variant/model lineage.
   Exact raw keys are preferred. Opaque keys must be decoded through
   `skuPropertyList`; `itemNum` is only a final unique fallback because live
   drafts may repeat the source item ID in every variant row. Never bind by
   list position or fuzzy text.
3. Compare parent parcel and every per-SKU parcel fact as well as approved
   price/category facts. A missing provider parent parcel is a repair
   requirement. Per-SKU dimensions may be provider-omitted only when all three
   dimension fields are absent together; partial omission is malformed.
4. SEA/MX: save only when approved per-SKU price/category/parcel facts drift.
5. GB: always save the approved category, parent parcel, per-SKU parcel and
   per-SKU prices before submission. The save obtains official category
   metadata and selects a mandatory attribute value only when the provider
   exposes exactly one legal value. There is no post-save readback gate.
6. Submit exactly one target through Miaoshou
   `save_move_collect_task`.
7. Explicit Miaoshou success is `ACCEPTED`; explicit provider rejection is
   `REJECTED`; transport ambiguity is `UNKNOWN`.

The receipt separates `write_request_count` from confirmed
`external_write_count`. Unknown outcomes use `external_write_count=null`.
Provider reason text is redacted before it reaches the receipt or logs.
An exact local variant/SKU binding failure occurs before the write boundary and
must be `REJECTED` with zero write requests and zero external writes. Only a
transport ambiguity after an HTTP write may be `UNKNOWN`.

## Public interfaces

- `modules.miaoshou.tiktok_publisher.production_tiktok_publisher()`
- `TikTokPublisher.preflight(snapshot)`
- `TikTokPublisher.publish(snapshot, preflight=None)`

Production calls `TikTokPublisher.publish(snapshot)` directly, so every target
has one write-time draft read. Passing a preflight receipt remains supported
for explicit diagnostic workflows and is then strictly rebound to the same
snapshot.

Preflight is read-only. Publish requires the same offer, plan, snapshot digest,
and exact target coverage returned by preflight.
