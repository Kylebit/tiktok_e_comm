# Shopee Global category decision UI

This UI is a human-decision surface inside the existing Shopee Global
pre-approval panel. It does not infer a category, approve a Global plan, or
publish a listing.

The surface follows four rules:

1. A recommendation is evidence, not approval. The recommended category and
   its server-declared source are shown separately from Kyle's persisted
   selection.
2. Category, required attribute, brand, seller-location and NEW_GLOBAL
   creation choices come only from the server projection. The browser never
   manufactures an ID, path, option, stock quantity or fallback. Official
   recommendations remain visible but are never silently approved.
3. One explicit save binds the category, opaque SINGLE/MULTI/TEXT attribute
   selections, brand, location, positive stock, condition/preorder and
   single-SKU variation summary. The browser echoes the current revision and
   server-issued identities, then reloads the read-only projection. A stale
   response cannot replace a newer offer or revision. `RECHECK_REQUIRED`
   performs GET-only reconciliation and never repeats the POST.
4. Final Shopee Global approval is rendered only after the server projection
   confirms that the complete decision is current and official attribute
   recheck passed.

## Reusable category-choice hook

The visual component is intentionally channel-neutral at its boundary:

- target label;
- server-owned recommendation and recommendation source;
- server-owned alternatives;
- persisted selection identity;
- official required attribute options or validated text inputs;
- brand, location, stock, condition/preorder and variation summary;
- disabled reason and next-action label;
- save callback supplied by the channel-specific integration.

TikTok/Miaoshou may reuse this component after their owning platform/channel
contracts expose an equivalent versioned projection and persistence seam.
Until then there is deliberately no TikTok/Miaoshou category endpoint, route,
payload, or fallback implementation in the product workspace.
