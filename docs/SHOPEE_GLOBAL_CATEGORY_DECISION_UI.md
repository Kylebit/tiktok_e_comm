# Shopee Global category decision UI

This UI is a human-decision surface inside the existing Shopee Global
pre-approval panel. It does not infer a category, complete marketplace
attributes, approve a Global plan, or publish a listing.

The surface follows four rules:

1. A recommendation is evidence, not approval. The recommended category and
   its server-declared source are shown separately from Kyle's persisted
   selection.
2. Category choices come only from the server projection. The browser never
   manufactures a category ID, path, attribute, default, or fallback.
3. Saving a selection is a local governed decision write. The browser echoes
   the current product revision and server-issued decision identities, then
   reloads the read-only projection. A stale response cannot replace a newer
   offer or revision.
4. Final Shopee Global approval is rendered only after the server projection
   confirms that the selected category is current and all required attributes
   are complete. Missing attributes are displayed with the server-owned next
   action; the UI does not guess values.

## Reusable category-choice hook

The visual component is intentionally channel-neutral at its boundary:

- target label;
- server-owned recommendation and recommendation source;
- server-owned alternatives;
- persisted selection identity;
- missing required attribute summaries;
- disabled reason and next-action label;
- save callback supplied by the channel-specific integration.

TikTok/Miaoshou may reuse this component after their owning platform/channel
contracts expose an equivalent versioned projection and persistence seam.
Until then there is deliberately no TikTok/Miaoshou category endpoint, route,
payload, or fallback implementation in the product workspace.

