# Ozon catalog identity and current cost

Ozon catalog entries use account Client-Id + official product `id` + exact approved
`offer_id`. The offer is the seller SKU. There is no invented variant ID. The
typed tables are additive; existing TikTok/Shopee tables and historical Ozon JSON
are not migrated by matching four-digit SKU suffixes.

The live publication transport resolves credentials once and freezes Client-Id
before any import. The server persists that account and the actual approved
projected offer mapping in the existing catalog intent. Credentials are never
stored in the intent. After the existing publisher classifies PUBLISHED, catalog
capture performs separate strict queries. Catalog errors cannot change that
publication result. Restart recovery uses only the persisted snapshot/account/
offer mapping; it never repeats import, stock writes, or credential refresh.

The query adapter permits POST to `/v3/product/info/list`,
`/v4/product/info/attributes`, and `/v1/product/info/description` only. Each
request is rebuilt with exactly one approved offer. Info and attributes must
each contain exactly one matching row, and all three responses must identify
the same positive official product ID and offer. Every approved offer must be
observed once, without duplicate product IDs. Empty/extra/duplicate rows,
identity changes, explicit continuation markers, and contradictory totals fail
closed. An empty response does not prove absence. Existing persisted account /
offer bindings cannot be replaced with another product, or vice versa.

This is a positive observation of specific entities, not a full account refresh,
absence check, or proof of global uniqueness. Existing repository response
shapes are info `{items:[...]}`, attributes `{result:[...]}`, and description
`{result:{id,offer_id,description}}`; no total field is required or synthesized.
Evidence anchors are the pre-change production parser and
`tests/test_product_publication_live_dependencies.py`'s authoritative ID/status/
parcel normalization test. The `total` assembled by `modules/ozon/sync.py` is a
local snapshot count, not an API contract. Official documentation retrieval
redirected repeatedly during this work; current official schema and real account
behavior were not verified. Tests exercise the existing producer shape offline.

Typed listing, current cost, and the common receipt commit atomically. The
approved frozen cost initializes only an absent cost. Existing first/late manual
costs are preserved and produce NEEDS_REVIEW. Directory edits use exact identity
and a version compare-and-swap. Replay cannot overwrite a later manual edit.
Durable observations support local-only retry after database failure; a lost
observation can be reconstructed with explicit official-readback using the
frozen intent. Price currency is retained as approved currency rather than
misrepresented as independently observed API currency; stock is not inferred.

I01 and profit display metadata preserve the original `statuses.status` string
and the complete provider `statuses` object with its source field. They label it
NOT_NORMALIZED: `status`, `status_failed`, and `is_created` alone are not a
publication or stock outcome. Missing or malformed status stays null with an
explicit reason; unknown provider strings are not replaced with a success label.
Observed sale currency remains null because this producer does not capture it.
Approved CNY currency and its frozen snapshot reference are separate metadata,
not a fallback for observed currency. These fields do not change profit rules.

The directory exposes one Ozon row per account/product/offer with its own cost
control. I01 audit and the profit local-catalog reader consume these same typed
records. Profit joins by seller SKU still reject ambiguity across accounts.
Historical Ozon JSON remains visible as an unbound reference, never contributes
to authoritative Ozon counts or cost authority, and is not silently merged into
the new rows. Business counts and historical reference counts are separate.

Validation uses temporary production-schema SQLite files, actual HTTP handlers,
actual provider parsing with synthetic POST responses, guarded fresh processes,
and a browser at desktop/mobile widths. No real platform, personal credentials,
production database, or user preview is used by these tests.
