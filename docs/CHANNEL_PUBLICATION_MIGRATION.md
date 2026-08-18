# Channel publication migration inventory

`domains.channel_operations.publication_planner` is the Phase 1 planning seam.
It consumes immutable `ApprovedProductPackage` and optional `ContentPackage`
contracts, and returns only `ChannelListing` drafts plus missing conditions.
It is always dry-run and always requires an explicit approval before any future
adapter is permitted to submit work.  It imports no legacy channel adapter,
database module, or network client.

## Existing real-write entry points to migrate behind this seam

| Channel | Existing entry point | Mutation currently performed | Migration target |
| --- | --- | --- |
| TikTok / Miaoshou MX | `modules/miaoshou/mx_publish.py:publish_mx_listing` and `publish_mx_multi_listing` | claims, saves collect-box edits, submits publish task | Add an approval-checked adapter that consumes a reviewed draft; preserve the present entry point as a compatibility wrapper. |
| TikTok / Miaoshou UK | `modules/miaoshou/uk_publish.py` and `modules/miaoshou/uk_web_approval.py` | saves collect-box edits and submits publish work | Route only an approved plan into an explicit adapter; retain current web approval semantics. |
| Shopee | `modules/shopee/publish.py:publish_match_key` and `modules/shopee/publish_group.py:publish_group_to_shop` | uploads images, creates global/local listings, submits publish task | Separate read-only payload preparation from an approval-gated submit adapter. |
| Ozon | `modules/ozon/migrate_batch.py:migrate_one` / `migrate_batch` | processes images, imports product, writes daily summary | Make draft and image preparation separate from an explicit approved import adapter. |

## Explicitly out of scope

This planner does not invoke any entry above and cannot publish, claim, change
price or promotion, deactivate a listing, write SQLite, or write channel state.
The migration remains an inventory until the integrator approves adapter and
contract changes.
