# Shopee connection restoration checklist

This is an execution plan, not an authorization record. Nothing below was run in this package.

## Exact current bindings

| Site | Shop ID | Shop name | Historical access-token expiry (UTC) |
|---|---:|---|---|
| MY | 1561117812 | LivelyHive | 2026-09-07T19:02:08Z |
| PH | 1527371343 | LivelyHive | 2026-09-07T19:02:58Z |
| TH | 1561124013 | LivelyHive | 2026-09-07T19:03:02Z |
| VN | 1723948773 | WangLuck6.vn | 2026-09-07T19:03:05Z |

Historical credential sources, read-only in this package:

- Settings: `D:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm\config\settings.json`
- Token store: `D:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm\shopee_tokens.json`

The expired access tokens do not prove that merchant authorization is permanently invalid. Refresh tokens are present for all four shops.

## Controlled refresh boundary requiring root approval

1. Create a private disposable credential session containing exact copies of the two files above. The copy must be outside Git and inaccessible to report artifacts.
2. Point both `--project-root` and `ORBIT_HIVE_SETTINGS` at that disposable session. Do not bind the original token store.
3. For each exact shop ID, call the existing `modules.shopee.auth.refresh_token(shop_id)` path once. Its provider endpoint is `POST /api/v2/auth/access_token/get`.
4. Verify that only the disposable `shopee_tokens.json` changed. Record before/after file hashes and shop ID, but never token values.

Expected side effects per shop:

- One external authentication POST.
- One local write to the disposable token store.
- Possible provider-side access/refresh-token rotation.
- No catalog, order, price, inventory, ad, or settlement mutation.

Do not run refresh concurrently for the same shop. Unknown transport outcome requires reconciliation against the disposable token store before retry.

## Minimal official read preflight

The high-level pull Skill is **not** the minimal probe: it uses page size 100, follows all pages, then fans out to escrow and order details. Before running it, execute a dedicated read-only request for each shop:

- Endpoint: `GET /api/v2/payment/get_escrow_list`
- `page_size=1`, `page_no=1`
- `release_time_from`: 2026-07-01 00:00:00 in the site's timezone
- `release_time_to`: 2026-08-01 00:00:00 in the site's timezone
- Retain only redacted status, response error/code, `more`, returned row count, release timestamp range, request bounds, site/shop binding, and a digest of returned order IDs.

Expected side effects: four external GET reads, zero platform writes, zero token-store writes. No paid/metered charge should be claimed until provider/account billing evidence is checked; this package made zero calls and incurred zero call scope.

## Full source recovery after minimal reads pass

Run the managed Skill separately for MY, PH, TH, and VN with the controlled credential session. Each site gets a separate immutable output directory and receipt. The command shape is:

```text
python domains/data_operations/skills/manage-profit-settlement/scripts/pull_settlement_evidence.py --platform shopee --site SITE --start 2026-07-01 --end 2026-07-31 --project-root CONTROLLED_ROOT --output NEW_IMMUTABLE_OUTPUT
```

Do not add `--allow-credential-refresh` during the full pull if the controlled refresh already succeeded. The full pull performs paginated escrow-list GETs, one escrow-detail GET per unique order, and order-detail GETs in batches of 50. Record the exact request counts from the receipt.

## Acceptance gates

- Site-local `escrow_release_time`, not order creation time, defines July inclusion.
- Each site is independently complete and independently checksummed.
- Pagination ends with retained terminal `more=false` evidence.
- TH fresh order-ID digest/count is reconciled against the historical candidate; differences remain explicit.
- MY/PH/VN must come from official reads because no local historical Shopee actual source was found.
- Missing historical cost, FX, buyer payment, or fee components remain missing. Never substitute zero, current catalog cost, current FX, or 22% advertising as actual.
- No report is labelled complete or profitable until source, cost, FX, and fee lineage all pass review.
