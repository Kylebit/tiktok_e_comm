# Source product identity v1

`source-product-identity/v1` is the product-domain hand-off for source lookup
identity. It prevents a merchant item number or specification label from being
used as a 1688 offer ID.

## Public seam

Import `resolve_source_product_identity` from `domains.product_operations`.
Pass already-loaded mappings only:

```python
resolution = resolve_source_product_identity(
    collect_box=collect_box,
    precollect=source.get("precollect"),
    source_record=source,
    source_authority="1688",
)
```

When `resolution.ready` is true, use only
`resolution.identity.source_offer_id` for source API lookup or release-plan
memory. `source_item_code` is display/audit data and must never be substituted
for `source_offer_id`.

When the status is `BLOCKED_SOURCE_IDENTITY`, the caller must stop before
building a READY one-click plan. Missing, malformed, or conflicting IDs are
not migration defaults.

## Authority and lineage

The resolver accepts identity only from:

1. `collect_box.source_item_id`;
2. `precollect.source_id` or `precollect.records[].source_id`;
3. `source_record.source_id`.

All populated observations must be valid and exactly equal. The identity
digest covers schema version, authority, canonical offer ID, and provenance.
It intentionally excludes merchant codes, titles, names, and specification
labels.

## 00 integration wiring

The shared-platform release builder should call this seam before constructing
the one-click plan:

- persist `identity.payload()` in plan memory;
- add the resolver blockers unchanged when status is
  `BLOCKED_SOURCE_IDENTITY`;
- require the stored identity digest to match on approval/execution;
- pass only `source_offer_id` to a source adapter;
- keep `source_item_code` available for operator display, never lookup.

This module performs no database, network, source-platform, or channel action.
