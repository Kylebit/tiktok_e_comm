# Miaoshou Collect-box Claim V1

## Purpose

`collectbox_claim.py` owns one narrow write boundary: moving an existing
Miaoshou common collect-box detail into the TikTok and Shopee platform collect
boxes through:

`POST /open/v1/product/common_collect_box/common_collect_box/claimed`

It does not edit product content, claim a platform detail to a shop, create a
shop draft, or publish a listing.

## Requests

The batch contract requires:

- one positive canonical `common_detail_id`;
- platforms exactly `("tiktok", "shopee")`, in that order;
- one nonempty server-owned idempotency key.

The durable single-platform contract accepts exactly `tiktok` or `shopee`.
Each HTTP body contains one item and only these fields:

```json
{
  "detailSerialNumberPlatformList": [
    {
      "detailId": 12345,
      "platform": "tiktok",
      "serialNumber": 1
    }
  ]
}
```

The module-level lock serializes account calls. The control plane owns durable
spacing between different platform invocations. The service waits three
seconds only before its one allowed retry of the same platform after the exact
observed business code `accountApiQpsRateLimit`. Other business codes and
unknown transport outcomes are never retried here.

## Outcomes

- `ACCEPTED`: the success response contains the exact positive platform detail
  ID. The internal receipt retains that ID; public evidence contains only its
  digest.
- `ALREADY_PRESENT`: allowed only when an authoritative already-claimed
  observation also supplies the exact positive platform detail ID. A bare
  `alreadyClaimed` rejection is not promoted to success.
- `FAILED`: includes known business rejection, missing response identity, and
  unknown transport. `retry_safe` and `reconciliation_required` distinguish
  known zero-write rejection from an ambiguous invoked write.

Write classes are platform-specific:

- `miaoshou:collectbox:claim:tiktok`
- `miaoshou:collectbox:claim:shopee`

Unknown transport or malformed success evidence returns `write_outcome =
UNKNOWN` and `reconciliation_required = true`. The caller must not issue a
blind retry.

## Redaction

Public projections exclude the common detail ID, platform detail ID,
idempotency key, request body, raw response, credentials, and error message.
They retain stable status, write/recovery facts, identity/evidence digests, and
the deterministic receipt digest.

## Shared-platform bridge

`domains.channel_operations.collectbox_action_adapters` is the only dynamic
bridge consumed by the shared control plane. It exact-checks the server-owned
common identity before invoking this service and maps the typed receipt as
follows:

- `ACCEPTED` -> `SUCCEEDED / IMPORTED`, one platform-specific write;
- exact-ID `ALREADY_PRESENT` -> `SUCCEEDED / ALREADY_PRESENT`, zero writes;
- known retry-safe rejection -> `FAILED_RETRYABLE`, zero writes;
- unresolved known identity -> `RECONCILIATION_REQUIRED`, zero writes;
- unknown invoked outcome -> `RECONCILIATION_REQUIRED`, unknown write count
  and the exact platform-specific write class.

The bridge returns the raw platform detail ID only in the server-internal
typed result. Its receipt evidence remains the redacted service projection.
