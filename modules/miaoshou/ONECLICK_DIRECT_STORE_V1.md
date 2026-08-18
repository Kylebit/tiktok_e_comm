# Miaoshou direct-store one-click V1

`miaoshou-direct-store/v1` is the single channel adapter for the real TikTok,
Shopee, and Ozon storefront targets. The adapter does not register a synthetic
COMMON target, a Shopee GLOBAL target, or promotion targets.

## Preparation

Preparation is read-only. It accepts the server-owned approved plan, canonical
source offer identity, target label, adapter policy digest, payload digest, and
idempotency key. The canonical source offer is queried only through the
platform-specific Miaoshou collect-box list. Human item codes are not query
identities.

The prepared command is JSON-only and binds:

- exact target label, platform, fixed Miaoshou shop ID, and site;
- exact source identity, payload, adapter policy, and idempotency identities;
- approved copy, ordered images, parcel facts, seller/model SKUs, and price;
- the observed Miaoshou detail identity and snapshot, or an explicit
  `CREATE_AND_CLAIM` action when the site detail does not yet exist.

Miaoshou's returned site detail supplies platform defaults such as category,
attributes, brand, logistics, warehouse, and other fields not present in the
approved plan. Missing or malformed defaults block only that target.

## Dispatch and receipts

Dispatch rehydrates only `modules.miaoshou.client.post_open`. It performs the
target's audited sequence:

1. re-read the exact source/detail identity;
2. create the platform detail when the prepared action requires it;
3. save the target-specific approved detail;
4. re-read and verify every approved write field;
5. submit the detail to the fixed Miaoshou shop.

The current endpoint families are:

- TikTok save/publish:
  `/open/v1/product/collect_box/tiktok/collect_box/`
  `save_shop_collect_item_info` and `save_move_collect_task`;
- Shopee save/publish:
  `/open/v1/product/collect_box/shopee/collect_box/`
  `save_site_detail_data` and
  `/open/v1/product/collect_box/shopee/move_collect/`
  `save_move_collect_task`;
- Ozon save/publish:
  `/open/v1/product/collect_box/ozon/collect_box/`
  `save_site_detail_data` and
  `/open/v1/product/collect_box/ozon/move_collect/`
  `save_move_collect_task`.

An accepted Miaoshou submission is always `SUBMITTED_UNVERIFIED` and requires
manual acceptance because marketplace readback is intentionally disabled.
Pre-invocation errors report zero writes. Each invoked create/claim/update/
submission boundary is append-only. A transport or malformed response after
invocation retains the confirmed writes and the possible unknown write instead
of being downgraded to a pre-submit failure.

## Target independence

Every storefront is prepared and dispatched independently. A TikTok, Shopee,
or Ozon target failure does not create a dependency or blocker for any other
target. Retry policy and durable attempt allocation are owned by the shared
control plane; this adapter preserves the exact idempotency and write evidence
for that decision.

## Development safety

Tests use injected captured response shapes and fake transports. Development
tests must not call detail save, claim, move, publish, or task-creation
endpoints. The bounded discovery used to establish this contract was read-only
shop/detail enumeration with no credential refresh and no business write.
