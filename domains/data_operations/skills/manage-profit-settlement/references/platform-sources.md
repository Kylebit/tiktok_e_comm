# Platform source rules

## TikTok

Use Finance statements and statement transactions. A statement transaction is settlement evidence; normalize its statement timestamp as the settled/occurred date. Expand an order transaction to order-item rows without duplicating the transaction total. Preserve commission, transaction, affiliate, shipping, tax, refund, and adjustment fields. Do not call file cleanup or legacy report-generation helpers during preview.

## Shopee

Use payment escrow list filtered by `escrow_release_time`, then escrow detail. Normalize only released escrow rows to settled. Preserve the release timestamp, payout/escrow amount, item quantity, item price, platform/service/commission/shipping/refund components, and item allocation method. Do not treat an ordinary completed order as settled without released escrow evidence.

## Ozon

Use finance transaction list and group operations by posting. Include only postings satisfying the existing official settled predicate. Preserve every operation/service line and whether it contributes to net settlement. Do not include pending postings merely because fulfillment or delivery completed.

## Live adapter receipt

Require a redacted receipt containing platform, period, snapshot ID/checksum, fetched-at time, source row count, normalized settled row count, excluded unsettled count, rejected count, API cursor/page summary, and `external_writes_performed=[]`. Never include credentials or raw API bodies.
