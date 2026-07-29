# Shopee global plan approval contract v1

## Purpose and boundary

`shared_platform.shopee_global_plan` is a pure, versioned boundary between:

1. a channel-owned, read-only Shopee observation;
2. Kyle's explicit approval of every global-product execution fact; and
3. a later server-owned prepare/dispatch operation.

It does not read a database, load credentials, call Shopee, create a
`ReleasePlan`, expose an HTTP endpoint, or dispatch a write. The integration
order is intentionally:

```text
official observation
  -> shopee-global-plan-candidate/v1
  -> Kyle exact consent
  -> approved-shopee-global-plan/v1
  -> server-owned ReleasePlan binding (future integration)
  -> 03 prepare/dispatch (future integration)
```

The contract removes all historical implicit Shopee defaults. An unavailable
fact is a capability blocker, not permission to guess.

## Authority gate

The candidate records one explicit observation authority:

- `shopee_official_open_api`
- `generated_sdk`
- `community`
- `injected_unverified`

Only `shopee_official_open_api`, exact schema
`shopee-official-global-plan-observation/v1`, and a valid evidence digest can
produce `READY` / `planning_allowed=true`.

Generated SDKs, community examples, injected fixtures, an unknown authority,
an unaudited schema, or absent evidence always produce
`BLOCKED_CAPABILITY`. Complete-looking fields do not override this gate.
Fixtures may prove parser behavior but are not production authority.

## Immutable internal fields

The ready candidate and approved decision bind every field below into their
canonical SHA-256 digests.

| Area | Required v1 facts |
| --- | --- |
| Mode | Exact `NEW_GLOBAL` or `EXISTING_GLOBAL` |
| Product lineage | `source-product-identity/v1` digest |
| SKU lineage | `sku-lineage-reservation/v1` or `new-source-sku-reservation/v1` digest |
| Content | content-package digest, NFC+trim title, byte-exact description, recomputed approved-copy digest |
| Images | every ordered approved HTTPS source URL and content digest, recomputed full manifest digest, explicit increasing selected positions, recomputed selected manifest digest |
| Image bound | At least one selected image, at most nine; the full approved list remains bound and is never sliced |
| Parcel | positive finite weight and three dimensions plus the upstream contract digest |
| Pricing | positive finite global original price in exact `CNY` plus target-pricing digest |
| Policy | exact policy digest |
| Category | positive category ID, complete ordered ID/name path ending at that category, official evidence digest |
| Attributes | nonempty complete selected attribute/value rows plus official attribute-tree digest |
| Brand | explicit brand ID/name and official evidence digest; ID zero is permitted only when explicitly observed and approved |
| Seller stock | explicit approved source, source digest, positive quantity, and approval reference |
| Location | explicit location ID and official evidence digest |
| Listing state | explicit condition (`NEW` or `USED`) and explicit preorder boolean/days |
| Variations | one or two named tiers with nonempty unique options and optional references to selected approved images |
| Models | unique model SKU for every Cartesian tier combination, exact tier indices, CNY price, and seller-stock quantity |
| Existing mode | positive official global item ID and official identity evidence digest, held server-internally |

V1 requires every model price and stock quantity to equal the approved
plan-level price and stock decision. A future requirement for different
per-model prices or quantities requires a versioned contract rather than an
implicit exception.

The following are specifically forbidden:

- default category, brand, location, seller stock, condition, or preorder;
- selecting the first attribute value;
- silently truncating more than nine approved images;
- collapsing a multi-option product into one model;
- using source inventory as seller inventory without an approved stock
  decision;
- manufacturing existing-global identity from a browser, match key, or
  third-party SDK.

## Approval and drift semantics

`approve_shopee_global_plan(...)` requires all of:

- a `READY` authoritative candidate;
- `approved_by` exactly `Kyle`;
- literal built-in boolean
  `confirm_approved_shopee_global_plan=true`;
- an exact echo of the current candidate digest.

The result is immutable `approved-shopee-global-plan/v1`. Before a server
uses its raw execution payload, it must call
`validate_approved_shopee_global_plan(approved, current_candidate)` (the
`server_owned_execution_payload` method performs the same gate).

Any change to source identity, SKU lineage, content, copy, approved or
selected images, parcel, target pricing, policy, official evidence,
category, attributes, brand, stock, location, condition, preorder,
variations, models, or existing-global identity produces a different
candidate digest. The old approval then raises `ShopeeGlobalPlanDriftError`;
it cannot be reused or partially patched.

## Redaction

`public_projection()` is the only public projection. It contains:

- schema, mode, status, authority, and blocker codes;
- completeness checks and row counts;
- lineage, policy, evidence, candidate, and approval digests.

It does not contain raw title, description, source URL, image digest list,
category/attribute/brand/location/global-item IDs, model SKU, stock quantity,
approval reference, or raw observation.

The full approved payload is stored inside the immutable Python object with
`repr=False` and is returned only by
`ApprovedShopeeGlobalPlan.server_owned_execution_payload(current_candidate)`
after exact drift validation. A future Store must persist it as
server-internal evidence and expose only `public_projection()`.

## Integration requirements

Future `00` integration must:

1. build the candidate from the audited `03` official observer;
2. expose only the redacted projection for Kyle's decision;
3. persist the approved object or an exactly reconstructable canonical
   equivalent;
4. bind candidate and approval digests into the immutable `ReleasePlan`;
5. rebuild the current candidate before execution and reject drift;
6. pass only the validated server-owned approved payload to the synthetic
   Shopee global-owner prepare seam.

Future `03` integration must consume the approved payload exactly. It must
not fill missing fields from legacy defaults, generated SDK documentation,
reference products, browser state, or a regional shop response.
