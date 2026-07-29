# SKU lineage reservation v1

`sku-lineage-reservation/v1` is the product-domain preflight that must run
before a one-click ReleasePlan allocates any new Seller SKU or Model SKU.

## Public seam

```python
result = resolve_sku_lineage_reservation(
    source_identity=source_identity,
    predecessor_records=loaded_product_and_plan_records,
    existing_reservations=loaded_reservation_records,
)
```

The inputs are immutable, already-loaded records. The resolver performs no
database or API access.

For `INHERITED_PREDECESSOR`, plan construction must use
`result.assignment` exactly. It must not allocate a replacement range. The
reservation payload binds:

- `source_identity_digest`;
- predecessor ID and revision;
- `predecessor_digest`;
- exact Seller/Model SKU assignment;
- canonical reservation keys and `reservation_digest`.

Inserting the same payload again is idempotent. An overlapping reservation
with another source, predecessor, revision, or digest is
`BLOCKED_SKU_LINEAGE`.

For `NEW_SOURCE`, the resolver found no approved/released predecessor for the
canonical source. Only then may 00 call the existing allocation scan. The
result of that later allocation needs its own reservation before plan
approval.

## Required predecessor record shape

```json
{
  "predecessor_id": "release-plan:source-986159122616:v31",
  "revision": 31,
  "status": "RELEASED",
  "source_identity": {
    "source_offer_id": "986159122616",
    "source_authority": "1688",
    "identity_digest": "sha256:..."
  },
  "seller_sku": "0956",
  "model_skus": [
    {"variant_key": "38x45-natural", "model_sku": "0956"},
    {"variant_key": "38x45-white", "model_sku": "0957"}
  ]
}
```

If a stored `predecessor_digest` is supplied, it must match the resolver's
canonical digest.

## 00 wiring order

1. Resolve `source-product-identity/v1`.
2. Load product/SKU predecessor and reservation records read-only.
3. Resolve `sku-lineage-reservation/v1`.
4. Stop on `BLOCKED_SKU_LINEAGE`.
5. For inherited lineage, copy the exact assignment into plan memory and
   insert/confirm the deterministic reservation before plan approval.
6. Allocate a new SKU range only for `NEW_SOURCE`, then reserve it before
   exposing READY.
7. Recheck source identity, predecessor, revision and reservation digests at
   approval and execution.
