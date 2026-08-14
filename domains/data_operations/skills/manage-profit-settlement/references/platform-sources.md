# Platform source rules

## TikTok

Use Finance statements and statement transactions. A statement transaction is settlement evidence; normalize its statement timestamp as the settled/occurred date. Expand an order transaction to order-item rows without duplicating the transaction total. Preserve commission, transaction, affiliate, shipping, tax, refund, and adjustment fields. Do not call file cleanup or legacy report-generation helpers during preview.

For local credential discovery, check the configured token file first, then `tiktok_tokens_livelyhive.json`. Skip missing or empty files. Do not silently restore a backup or refresh an expired access token. With explicit operator approval, copy the selected token into a disposable directory, temporarily bind `core.auth.token_path()` to that copy, call `tiktok_settlement.load_token()`, and keep the binding for the read session. Record whether refresh occurred, delete the disposable copy, and never overwrite either source file.

TikTok Finance statement query bounds are inclusive UTC calendar days, matching `tiktok_settlement.pull_period()`. Convert each returned `statement_time` to the site's reporting timezone for `settled_at`, then re-filter the requested local date range. Group expanded item rows by statement ID, transaction type, and order/adjustment ID; sum allocated money and fee components while retaining every item row. Report settled order, adjustment, and item-line counts separately.

For the settled TikTok order identities, read `/order/202309/orders` in batches and preserve the official `create_time` as `order_created_at`. Missing or invalid creation time is a quality issue. Reporting-period inclusion remains based on Finance settlement evidence; never substitute payment, shipment, completion, statement, settlement, or pull time for missing order creation time.

From the same official order response, preserve `fulfillment_type` as the authoritative TikTok fulfillment method. Also retain `delivery_type`, `shipping_type`, `delivery_option_name`, and `warehouse_id` when returned. Known values such as `FULFILLMENT_BY_SELLER` and `FULFILLMENT_BY_TIKTOK` may receive Chinese display labels, but JSON must keep the exact platform enum and evidence source. A newly pulled settled order without `fulfillment_type` is a quality issue; never infer fulfillment from fees, carrier, tracking number, shop name, or warehouse text.

The same TikTok order may have a positive sale statement followed by a negative refund statement in the same reporting period. Preserve both statement identities. If the repeated item SKU and quantity agree and only the sale statement carries a positive buyer-paid basis, consolidate them into one order line so settlement/refund components sum while product cost and estimated advertising are charged once. Any disagreement or multiple positive bases is blocking.

## Shopee

Use payment escrow list filtered by `escrow_release_time`, then escrow detail. Normalize only released escrow rows to settled. Preserve the release timestamp, payout/escrow amount, item quantity, item price, platform/service/commission/shipping/refund components, and item allocation method. Do not treat an ordinary completed order as settled without released escrow evidence.

Use each authorized shop's reporting timezone for inclusive period bounds: MY `Asia/Kuala_Lumpur` (UTC+08:00), PH `Asia/Manila` (UTC+08:00), TH `Asia/Bangkok` (UTC+07:00), and VN `Asia/Ho_Chi_Minh` (UTC+07:00). Never reuse another site's timezone or combine sites into one evidence artifact.

Read `/api/v2/order/get_order_detail` in batches for the same settled order identities and preserve official `create_time` as `order_created_at`. Treat a missing create time as a quality issue. Keep reporting-period inclusion based on escrow release time; never use order creation time to include an unsettled order and never substitute release time for a missing create time.

Shopee may release many older orders in one settlement batch, so a weekly report can legitimately contain only one release date. Treat this as valid only when every included timestamp comes directly from `escrow_release_time`, remains inside the requested period, and the evidence reports the parent-order and expanded item-line counts. Never replace the release date with order creation, completion, API request, or pull time merely to spread rows across the week.

Classify fulfillment from escrow-detail import-tax evidence, not shipping fee. Read `vat_on_imported_goods` and `th_import_duty` (or an explicitly mapped regional import-duty alias). A non-zero VAT or duty is cross-border evidence; two present exact zeros mean local fulfillment. Missing either field is a quality issue. If only one charge is non-zero, retain cross-border classification but emit `incomplete_cross_border_tax_pair`. Apply the configured combined local shipping-and-warehouse charge once per parent order, not once per expanded item line.

Treat `order_ams_commission_fee` as an actual Affiliate Marketing Solutions settlement deduction already included in escrow, not as the operator's estimated advertising spend. Shopee Thailand defines seller affiliate commission as the seller-selected rate multiplied by Net Completed Purchase Value after discounts, shipping, vouchers, and rebates; rates can differ by product/category/affiliate, indirect orders use 30% of the direct-order rate, and applicable tax is added. Therefore a naive fee divided by listed product sales can vary widely. Retain the official amount and compute only a clearly labelled observed effective rate when useful; do not infer the configured rate or direct/indirect classification unless the source provides those fields.

Escrow-detail fan-out is sequential and can exceed a short command timeout. Size the execution window from the escrow-list parent count, allow a generous bounded timeout, and never start an overlapping retry while the first run is active. The script writes the final evidence only after all requested details finish; absence of the final JSON means the run is incomplete, not an empty or successful settlement period.

## Ozon

Use finance transaction list and group operations by posting. Include only postings satisfying the existing official settled predicate. Preserve every operation/service line and whether it contributes to net settlement. Do not include pending postings merely because fulfillment or delivery completed.

For local credential discovery, check `config/ozon.local.json`, then `modules/ozon/legacy_webapp/data/credentials.local.json`; inject the credential in memory and never copy it. Re-filter returned operations by the requested inclusive business-date range because the finance endpoint can return boundary rows outside the requested local dates.

Do not label Ozon `in_process_at`, shipment, delivery, operation, or settlement timestamps as order creation time. Leave the order-created field empty unless an official order read returns an explicit creation-time field.

Finance transaction items may expose only an Ozon platform SKU and omit quantity. For profit calculation, resolve the platform SKU to the authoritative `offer_id`/seller SKU with the read-only `/v3/product/info/list` endpoint and resolve fulfilled quantity with `/v3/posting/fbs/get`. Never infer quantity as one. Record requested/mapped SKU counts, requested postings, quantity keys, and typed failures; retain no raw API response.

For the current operator-approved Ozon V1 estimate, sum positive `OperationAgentDeliveredToCustomer` components per posting as buyer-paid product advertising basis and apply fixed rate `0.22`. Missing sale components remain blocking. Preserve the policy version and never describe the result as actual advertising spend.

## Live adapter receipt

Require a redacted receipt containing platform, period, snapshot ID/checksum, fetched-at time, source row count, normalized settled row count, excluded unsettled count, rejected count, API cursor/page summary, and `external_writes_performed=[]`. Never include credentials or raw API bodies.

The stage-1 artifact schema is `settlement-evidence/v1`. A `ready` evidence artifact means the official settlement read and redacted normalization completed; it does not mean profit is ready. A missing/expired credential or API failure must produce a `blocked` receipt rather than a cached report presented as current.

Shopee access-token refresh is permitted only after explicit operator authorization and must use `--allow-credential-refresh`. Because Shopee may rotate the refresh token, the configured token store is updated and the evidence receipt must record `credential_refresh_performed=true` and `credential_refresh_scope=configured_shopee_token_store`.

Shopee fulfillment classification is site-specific. For MY, use `sales_tax_on_lvg`: a present non-zero charge is cross-border and a present zero value is local. For VN, apply the same non-zero/zero rule to `vat_on_imported_goods`. Treat every PH order as cross-border. For TH, use the paired import-VAT and import-duty rule documented in the main Skill. Missing required evidence remains blocking.

Preserve every numeric settlement component with its source code. Until a platform-specific reconciliation contract classifies the component, set `included_in_net_settlement` to `unknown`; never infer fee arithmetic from the component name alone.
