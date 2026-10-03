# R1 category capture and offline consumption

The local chain is now `capture → immutable internal observation → reference resolver → prepare → freeze`. Capture is explicit. Ordinary preparation and freezing never refresh credentials, start a provider request, or register caller-supplied observation JSON.

This extends the local binding package at 9bc76ce. Its v2 receipt behavior remains available; new service captures use `round1-shopee-category-observation/v2` and `shopee-category-official-review/v3`. Already frozen round1 snapshots and the existing channel category observer/v2 retain their prior formats and consumers.

## Capture

POST `/api/product-workspace/round1-category/capture` with exactly:

```json
{
  "offer_id": "<exact Product Center offer>",
  "product_center_revision": 7,
  "requested_targets": ["shopee:MY", "shopee:PH"],
  "source_region": "MY",
  "account_identity_digest": "sha256:<intended account digest>",
  "category_id": 101,
  "selected_attributes": []
}
```

These are illustrative placeholders, not a real product capture command. Category ID and attribute values are explicit intent, not claims of official evidence. Mandatory attributes cannot be defaulted or omitted. `selected_attributes` uses the existing official `attribute_id` / `attribute_value_list` contract, preserving `value_id`, `original_value_name` and optional `value_unit`.

The service reads current Product Center facts and exact selected targets under the per-product lock. It checks the expected revision, uses the transport for the explicitly selected source region, and compares the actual transport region and account digest before any official GET. Account digest is the canonical `sha256:` digest of `{region, shop_id, merchant_id}`; it contains no token. No four-country scan or PH default occurs. The no-refresh transport path requires already prepared, unexpired shop and merchant credentials; failure returns `shopee_category_prepared_credentials_required`, with no auth-refresh fallback.

Only the selected category path and attribute tree are fetched. Existing official parsers reconstruct the path and revalidate selected attributes, including SINGLE_SELECT, MULTI_SELECT, TEXT, units and value_id=0. Capture does not query brands or warehouses and does not choose missing mandatory values. It records actual completion time, actual transport account/region, endpoint-response digests and GET count, then rechecks current product inputs before persistence. No extra TTL is imposed.

## Internal storage and trust

`ReleaseStore` owns `round1_category_observations` and explicit `round1_category_observation_invalidations` in its fixed internal database. Dedicated observation transactions initialize only these tables; they do not run unrelated release migrations. Records are immutable by reference, with exact indexed identity and canonical record/digest readback. An exact replay is idempotent; conflicting content is rejected. Explicit internal invalidation is append-only and has no public arbitrary-invalidation route.

Trust comes from the service capture call chain and protected internal store, not from a label, self-consistent hash, path name or table name. HTTP provides no endpoint for registering raw observations. CLI cannot choose a store path. The resolver checks reference, integrity, input identity, account scope and explicit invalidation. It works after reopening the store without provider or credential access. Offline account validation binds the intended digest to the recorded account; it does not silently inspect a potentially different live login. A new intended account requires its own matching capture.

This trusts the local service and its OS-level storage access. It does not claim protection against a process with the same filesystem/database write authority. Complete record and response digests contain no persisted credentials, authorization headers or raw provider responses.

## Resolve, prepare and freeze

POST `/api/product-workspace/round1-category/resolve` with the five common capture identity fields plus `observer_reference`, omitting category ID and selected attributes. This validates current server facts and returns the stored receipt without new official reads. Both routes require the bound loopback Host, loopback peer, permitted Origin, bounded unambiguous JSON framing and POST. Duplicate JSON keys, foreign authority fields and caller observation blobs are rejected.

The preparation CLI consumes the same fixed internal store:

```text
prepare_product_publication.py --offer-id <offer> --targets <exact ordered targets> --category-source-region <region> --category-observation <returned reference> --category-account-digest <intended account digest>
```

The existing image-plan and output arguments remain available. This command never triggers capture. Missing, changed, revoked or corrupt records remain actionable blockers. Passing a v3 receipt through the old untrusted file option still does not establish a trusted source.

Preparation embeds the verified receipt and projects its category/digest into Shopee target rows. Freeze automatically resolves v3 records from the same internal store, validates the embedded reviewed object, and reads the record once. It does not reread mutable sidecars. The resulting snapshot includes a deep copy of the receipt. Existing frozen snapshot loading/R2 identity validation is unchanged, including old snapshot bytes without category binding. Non-Shopee behavior and approval actors/policies are unchanged.

## Validation and remaining production step

Offline tests use the actual capture/parser/ReleaseStore/Handler/CLI/freeze consumers, synthetic lowest-level GET responses and temporary SQLite. A controlled loopback port exercises a real Handler. Tests cover provider-disabled store reopening, all official attribute input kinds, region/account/product/target drift, capture-time fact changes, immutable concurrency conflicts, corruption and revocation, no added TTL, single freeze read, HTTP framing, no implicit capture or credential refresh, and existing v2 observer/decision/snapshot regressions.

The evidence runner binds source hashes before tests and again at actual source import, suppresses environment repr, and denies real credential/provider access. Runtime config and runtime Git identity are fixture-controlled before server import. This proves the local chain; no real product was captured, no production credentials or database were read, and no personal installation or marketplace write was performed.

The remaining production operation is one separately scoped capture: freeze exact offer/revision/ordered targets/source region/intended account digest/category intent, verify prepared credentials without refresh, run the two narrow official GETs, read back the internal record, then consume its reference. Do not substitute a synthetic fixture product, assume PH, or infer publication readiness: global category evidence remains `regional_publishability=NOT_VERIFIED`.
