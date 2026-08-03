# TikTok Independent Publisher Contract

`TikTokPublisher` is the sole channel-owned TikTok publication boundary. It
does not import or call the legacy one-click control plane, Shopee, Ozon, or
`miaoshou:COMMON`.

## Approved input

The caller supplies `approved-tiktok-publish-snapshot/v1`. The snapshot binds:

- approved offer, plan, product revision, and immutable payload digest;
- only selected TikTok target labels;
- the exact Miaoshou detail/shop identity and its three durable identity
  digests (`target_identity_digest`, `publish_identity_digest`, and
  `receipt_digest`);
- approved target price/currency;
- approved product category ID and category evidence digest.

Category is product evidence. There is intentionally no category-by-site
production constant.

## Execution

Each target is independent. A rejection or unknown outcome is recorded for
that target and later targets continue.

1. Read the current target draft.
2. SEA/MX: save only when approved price/category facts drift.
3. GB: always save the approved category/price before submission. The save
   obtains official category metadata and selects a mandatory attribute value
   only when the provider exposes exactly one legal value. There is no
   post-save readback gate.
4. Submit exactly one target through Miaoshou
   `save_move_collect_task`.
5. Explicit Miaoshou success is `ACCEPTED`; explicit provider rejection is
   `REJECTED`; transport ambiguity is `UNKNOWN`.

The receipt separates `write_request_count` from confirmed
`external_write_count`. Unknown outcomes use `external_write_count=null`.
Provider reason text is redacted before it reaches the receipt or logs.

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
