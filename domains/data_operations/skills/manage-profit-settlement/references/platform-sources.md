# Platform source rules

## TikTok

Use Finance statements and statement transactions. A statement transaction is settlement evidence; normalize its statement timestamp as the settled/occurred date. Expand an order transaction to order-item rows without duplicating the transaction total. Preserve commission, transaction, affiliate, shipping, tax, refund, and adjustment fields. Do not call file cleanup or legacy report-generation helpers during preview.

For local credential discovery, check the configured token file first, then `tiktok_tokens_livelyhive.json`. Skip missing or empty files. Do not silently restore a backup or refresh an expired access token. With explicit operator approval, copy the selected token into a disposable directory, temporarily bind `core.auth.token_path()` to that copy, call `tiktok_settlement.load_token()`, and keep the binding for the read session. Record whether refresh occurred, delete the disposable copy, and never overwrite either source file.

TikTok Finance statement query bounds are inclusive UTC calendar days, matching `tiktok_settlement.pull_period()`. Convert each returned `statement_time` to the site's reporting timezone for `settled_at`, then re-filter the requested local date range. Group expanded item rows by statement ID, transaction type, and order/adjustment ID; sum allocated money and fee components while retaining every item row. Report settled order, adjustment, and item-line counts separately.

The same TikTok order may have a positive sale statement followed by a negative refund statement in the same reporting period. Preserve both statement identities. If the repeated item SKU and quantity agree and only the sale statement carries a positive buyer-paid basis, consolidate them into one order line so settlement/refund components sum while product cost and estimated advertising are charged once. Any disagreement or multiple positive bases is blocking.

## Shopee

Use payment escrow list filtered by `escrow_release_time`, then escrow detail. Normalize only released escrow rows to settled. Preserve the release timestamp, payout/escrow amount, item quantity, item price, platform/service/commission/shipping/refund components, and item allocation method. Do not treat an ordinary completed order as settled without released escrow evidence.

Shopee may release many older orders in one settlement batch, so a weekly report can legitimately contain only one release date. Treat this as valid only when every included timestamp comes directly from `escrow_release_time`, remains inside the requested period, and the evidence reports the parent-order and expanded item-line counts. Never replace the release date with order creation, completion, API request, or pull time merely to spread rows across the week.

Escrow-detail fan-out is sequential and can exceed a short command timeout. Size the execution window from the escrow-list parent count, allow a generous bounded timeout, and never start an overlapping retry while the first run is active. The script writes the final evidence only after all requested details finish; absence of the final JSON means the run is incomplete, not an empty or successful settlement period.

## Ozon

Use finance transaction list and group operations by posting. Include only postings satisfying the existing official settled predicate. Preserve every operation/service line and whether it contributes to net settlement. Do not include pending postings merely because fulfillment or delivery completed.

For local credential discovery, check `config/ozon.local.json`, then `modules/ozon/legacy_webapp/data/credentials.local.json`; inject the credential in memory and never copy it. Re-filter returned operations by the requested inclusive business-date range because the finance endpoint can return boundary rows outside the requested local dates.

Finance transaction items may expose only an Ozon platform SKU and omit quantity. For profit calculation, resolve the platform SKU to the authoritative `offer_id`/seller SKU with the read-only `/v3/product/info/list` endpoint and resolve fulfilled quantity with `/v3/posting/fbs/get`. Never infer quantity as one. Record requested/mapped SKU counts, requested postings, quantity keys, and typed failures; retain no raw API response.

For the current operator-approved Ozon V1 estimate, sum positive `OperationAgentDeliveredToCustomer` components per posting as buyer-paid product advertising basis and apply fixed rate `0.22`. Missing sale components remain blocking. Preserve the policy version and never describe the result as actual advertising spend.

## Live adapter receipt

Require a redacted receipt containing platform, period, snapshot ID/checksum, fetched-at time, source row count, normalized settled row count, excluded unsettled count, rejected count, API cursor/page summary, and `external_writes_performed=[]`. Never include credentials or raw API bodies.

The stage-1 artifact schema is `settlement-evidence/v1`. A `ready` evidence artifact means the official settlement read and redacted normalization completed; it does not mean profit is ready. A missing/expired credential or API failure must produce a `blocked` receipt rather than a cached report presented as current.

Preserve every numeric settlement component with its source code. Until a platform-specific reconciliation contract classifies the component, set `included_in_net_settlement` to `unknown`; never infer fee arithmetic from the component name alone.
