# Shopee EXISTING_GLOBAL current-facts contract

`EXISTING_GLOBAL` may use an official current global item as the immutable
candidate that Kyle reviews. This does not weaken `NEW_GLOBAL` and does not
introduce an inventory-system dependency.

## Meaning of the official seller-stock source

`shopee-official-existing-global-seller-stock/v1` means:

- Shopee's official global-item GET returned one exact item-level
  `seller_stock` row;
- the row's `location_id` is a non-empty string and `stock` is a positive
  built-in integer;
- the quantity and location will be preserved, not created or changed;
- the fact is not physical available inventory and is not a Yacang/Seaya
  decision;
- it is valid only in `EXISTING_GLOBAL`.

The shared pure helper
`build_shopee_official_existing_global_seller_stock(...)` accepts the official
observation digest, exact global item identity evidence, and a projected
`seller_stock_rows` list. The v1 plan represents exactly one official seller
location. Missing, malformed, boolean, non-positive, additional, or multiple
rows fail closed.

The helper returns the paired `seller_stock` and `location` candidate fields.
Their digests bind:

- official Open API authority and observation schema;
- official observation evidence digest;
- exact existing global item identity and identity-evidence digest;
- the exact item-level location and positive current quantity.

The helper does not approve the row. The complete current candidate still
requires literal Kyle approval through the existing append-only Shopee global
plan approval seam. That approval additionally binds copy, source images,
parcel, CNY price, category path, full attributes, brand, condition, preorder,
variation tiers, and global models.

## Required channel behavior

The channel-owned observer must:

1. use prepared credentials without refresh;
2. resolve exactly one existing global item and exact global model identities;
3. obtain category, attributes, brand, item-level seller stock/location,
   condition, preorder, variation tiers, and models from official GETs;
4. project the single stock row through the shared helper;
5. return the complete shared candidate without persisting raw responses.

The currently audited global-item response supplies `category_id` and the
item's current `attribute_list`, but it does not by itself prove the complete
category path or the category attribute-tree identity required by
`approved-shopee-global-plan/v1`. A channel adapter must not manufacture a
category name/path or label an item snapshot digest as an attribute-tree
digest. Until an audited official category/path and attribute-tree read is
available, the adapter remains typed
`shopee_existing_global_approved_facts_unavailable`; the stock/location helper
alone does not make the candidate READY.

If Shopee does not expose those facts, a future reviewed
`approved-shopee-existing-current-snapshot/v1` may separate preserve-only
current item facts from NEW-global creation facts. That would be a new schema
and policy decision, not an interpretation of the current `path_complete` or
`attribute_tree_digest` fields.

Before any marketplace mutation, channel dispatch must re-read and exact-match
the approved existing item/model and current-facts candidate. Drift is a typed
pre-dispatch failure with zero marketplace writes.

An official-current stock source never authorizes global create, global update,
model initialization, or stock update. The existing global item is reused and
the approved current quantity is preserved. Regional publish retains the
existing truthful write-boundary and reconciliation rules.

## Redaction

Public preview, approval, job, outcome, and API projections contain only
status, counts, rule/check codes, and digests. Raw global item/model IDs,
location IDs, copy, URLs, tokens, and platform responses remain in the
server-internal immutable approval record or in-memory official observation
only.

## NEW_GLOBAL boundary

`NEW_GLOBAL` cannot use
`shopee-official-existing-global-seller-stock/v1`. It still requires its own
approved seller-stock source, category/attribute/brand/location decisions, and
complete variation/model plan. No current existing-item observation may fill
those missing NEW facts.
