# Catalog publication and current costs

The standalone `/costs` page and generator are retired. Both `/costs` and
`/costs.html` redirect to `/catalog`. Catalog cost inputs remain in the existing
cost column and are separate for each platform/shop/product/official variant.
Shared cost APIs, CLI commands and CSV files remain available. An ambiguous
legacy SKU ID must be edited with its full catalog identity.

## Authority and current-cost behavior

Offer ID, approved internal `variant_key`, local SellerSKU/Model-SKU, platform
product ID and official variant ID are distinct. Only an exact official
observation tied to the frozen approved snapshot can create a projection.
No `item_<id>` or product-ID-as-variant fallback is accepted.

`catalog_identity_costs` is the current per-identity authority. Directory edits
use a version compare-and-swap. The actual profit `local_catalog` consumer reads
this authority through `catalog_database_audit`. Duplicate SellerSKU identities
remain blocked for a global profit join; each record retains its scoped cost.
These current costs do not rewrite approved snapshots or historical profit facts.

Publication has a conservative first-binding policy: any existing scoped or
uniquely owned legacy cost is preserved, including a manual edit made after
dispatch began. The observation reports `COST_CONFLICT_PRESERVED` and requires
review. A uniquely owned legacy value is carried into the scoped authority with
manual provenance; the publication amount does not overwrite it. Missing costs
can be filled from the approved CNY amount. Replaying a receipt is idempotent.

Catalog rows, observation, costs and local receipt commit in one SQLite
transaction. The publication service and catalog are not a distributed atomic
transaction. A catalog failure cannot change a confirmed platform result.

## Producer and platform coverage

| Platform | Automatic producer and identity | Current support |
| --- | --- | --- |
| Shopee | Actual `readback_dispatched_regions` through the publication executor: context shop ID, official item ID, each exact model ID + Model-SKU, verified frozen facts | Directory identity/current cost projection and GET-only recovery connected through the HTTP publication runner |
| TikTok | Default live storefront readback is unavailable; no exact official variant observation is currently emitted to this sink | Explicit pending/unbound; the low-level catalog table adapter is not evidence of live producer support |
| Ozon | Current readback item ID does not establish a separate official variant identity | Explicit pending/unbound; no guessed variant or Ozon JSON rewrite |

Shopee projects observed name, optional model name, region, status, currency and
price plus approved cost and exact identity. This is not a full catalog refresh:
image and stock are not inferred. Missing/empty observation fields preserve
existing catalog values. Generic standalone calls to `ProductPublicationRunner`
must supply `catalog_sink`; the production HTTP background runner does so.

## Durable recovery

Before dispatch, `begin` stores the validated immutable snapshot, digest, run and
report IDs and target scope in the catalog-sync outbox under the report root.
Before each regional dispatch batch, the sink additionally stores exact
region/shop/merchant/global-item query identities. If this persistence fails,
regional dispatch is stopped. Once dispatch returns, accepted provider task IDs
or exact existing item IDs are persisted before official readback.

If the observation file exists but catalog projection failed, POST
`/api/catalog/publication-sync` with only `receipt_id` retries the local SQLite
projection. It performs no provider calls.

If official publication succeeded but observation persistence failed, POST
`/api/catalog/publication-sync/official-readback` with only the intent
`receipt_id` reconstructs the request from server-owned persisted facts. The
dedicated adapter permits only these official GET paths:

- `/api/v2/global_product/get_publish_task_result`
- `/api/v2/global_product/get_global_model_list`
- `/api/v2/product/get_item_base_info`
- `/api/v2/product/get_model_list`
- `/api/v2/global_product/get_global_item_id`

It never calls the publication readback/repair executor. Credentials are loaded
read-only from the existing cache; missing, mismatched or expired credentials
leave pending evidence, without refresh. Exact target coverage, shop/merchant
query context, item/global linkage, model set/IDs, frozen prices/copy/category,
gallery and description checks precede persistence and projection. Browser
supplied IDs, costs, snapshots or claimed official evidence are not accepted.
Repeated completed recovery is local and does not dispatch or query again.

Localized galleries additionally require the existing server-owned global SKU
map's shop/item binding and image-route digest to match the frozen ordered
image route. The mapping is read-only. Missing, replaced or mismatched mappings
remain pending; recovery does not regenerate images or overwrite the mapping.
Thus localized recovery is not self-contained in the intent alone.

If persistence of the dispatch task/item ID itself failed, or an older intent
does not contain a frozen snapshot and exact query scope, there is no safe
automatic query identity. Recovery remains `PENDING_OFFICIAL_EVIDENCE` with an
explicit reason; it does not search for likely matches or publish again.
GET errors, missing IDs and mismatched facts likewise remain pending.

GET `/api/catalog/publication-sync` exposes local state and recovery reasons.
`COMPLETE`, `NEEDS_REVIEW`, `PENDING_LOCAL_PROJECTION`, and
`PENDING_OFFICIAL_EVIDENCE` describe catalog synchronization only, separately
from the marketplace publication result.

## Validation boundary

Tests use temporary production-schema SQLite databases, the actual HTTP Handler,
publication executor, profit consumer and browser. Provider responses are fake
transport fixtures; no live marketplace call is claimed. Fresh guarded child
processes exercise persisted recovery, GET failure, item/global/model mismatch,
duplicate/missing variants, credential failures, manual edit conflicts, legacy
intents and missing/replaced localized mappings. Production database, personal
credential and provider access remain outside these tests.
