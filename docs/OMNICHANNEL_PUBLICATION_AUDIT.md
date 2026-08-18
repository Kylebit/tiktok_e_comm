# Omnichannel publication audit

The `domains.channel_operations.omnichannel_orchestrator` boundary is a pure
planning and authorisation seam. It can show a single “Publish to all selected
sites” approval summary, but it never calls a channel adapter. Its confirmation
token is bound to the exact product package, approval IDs, content package,
ordered images, copy, and selected sites. A changed image order or target set
requires a new preview and approval.

## Existing real-write paths

| Target | Existing write path | Current confirmation posture | Integration risk |
| --- | --- | --- | --- |
| Miaoshou common collect box | `modules/sourcing/new_product_server.py` commit endpoints | Literal JSON `true` is required for draft/image writes | The boolean is request-scoped and is not an idempotency ledger. |
| TikTok GB / MX through Miaoshou | `modules/miaoshou/uk_publish.py`, `mx_publish.py` | Persisted UK/MX confirmation cards are checked before the final publish call | Both functions perform claim/save calls before their final publish-token check; the adapter must validate the omnichannel token before *any* write. |
| Shopee CNSC / local shop | `modules/shopee/publish.py`, `publish_group.py` | Dry-run exists for some global preparation, but real functions have no common user-approval token | Image upload, global-product creation, and local publication need to move behind the new gate. |
| Ozon RU | `modules/ozon/migrate_batch.py` and the legacy `/api/migrate` bridge | No dry-run or explicit publication approval at the import boundary | Image processing/uploads, product import, polling, and local summary writes are coupled. Split payload preparation from submit. |

TikTok sites other than GB/MX, Shopee sites other than MY/PH/TH/VN, and Ozon
sites other than RU have no audited adapter path in this repository and remain
blocked by preflight.

## Safe product UI

The normal product page may display one primary button when every target
preflight passes:

1. The first click opens the `SingleApprovalSummary`, including product/SKU,
   collect-box ID, image count, exact sites, and count of external mutations.
2. Explicit user confirmation sends literal `user_approved=true` and the exact
   preview `confirmation_token`.
3. The server rebuilds the plan from current immutable package revisions. It
   rejects a stale token before invoking any adapter.
4. Execution records one parent run and one idempotency key per site. Retrying
   only failed targets must reuse those keys; successful targets are not
   silently submitted again.
5. The UI shows per-site progress and results. “All published” is shown only
   after read-back verification from every selected platform.

Before this is wired to real adapters, the button must remain preview-only.

## Release-dashboard preview contract

`build_release_dashboard` now exposes a JSON-ready `omnichannel_preview`. The
default matrix is derived from the workbench `review.selected_sites`: legacy
keys such as `lh_ph` are normalised to `PH` and applied to both TikTok and
Shopee. The matrix also contains the Miaoshou `COMMON` draft and Ozon `RU`.

The lineage is explicit: Miaoshou is the shared draft, TikTok is the master
listing/read-back, and Shopee/Ozon are derived from that approved master
revision. This is a dependency description only; the preview never calls an
adapter. Every target reports its preflights, repository adapter audit status,
future execution steps, adapter gate, idempotency key, and dependency.

The repository currently audits TikTok only for GB/MX, Shopee for
MY/PH/TH/VN, Ozon for RU, and Miaoshou for COMMON. Consequently a SEA matrix
such as PH/MY/TH/VN correctly blocks the TikTok targets while still reporting
the audited Shopee and Ozon paths. The dashboard must not translate a draft or
recognisable legacy route into executable support.

If the product or content approval preview is not ready, the dashboard returns
an unavailable `omnichannel_preview` with structured blockers and the intended
site selection instead of raising or fabricating an execution plan.
