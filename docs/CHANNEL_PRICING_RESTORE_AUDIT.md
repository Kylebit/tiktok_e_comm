# Channel pricing restore audit

The release dashboard replays the previous workbench pricing algorithm from
persisted inputs only. It does not fetch live FX, read a marketplace listing,
or write a draft.

## Legacy product-workbench calculation

Source: `modules/sourcing/new_product_workbench.py:price_review`.

Legacy HTTP surface: `GET/POST /api/new-product/preview`.

Legacy UI: `/new-product`, rendered by the JavaScript `renderPricing` function.
That page consumed `pricing.input`, `pricing.sea`, `pricing.mx`, `pricing.uk`,
`pricing.rates`, and `pricing.audit.sections`.

The release payload preserves those facts under `pricing_review` and provides
selected-store summaries under `pricing_review.selected_store_prices`.

| Store / country | Currency | Core calculation and output |
| --- | --- | --- |
| LivelyHive PH/MY/TH/VN | PHP/MYR/THB/VND | Reverse price from goods, weight-based logistics, country fees, advertising, creator fee, tax and a 15% target margin. Reserve 35% discount; round by country; raise the list price until estimated profit is at least CNY 5. |
| HomeBloom PH/MY/TH/VN | PHP/MYR/THB/VND | Same country fee rules, but a 10% target margin. Prices remain separate store rows even when the country is identical. |
| LivelyHive MX | MXN | Goods + hidden shipping tier + MXN 6 item fee, divided by 1 minus import tax 13.96%, commission 6%, SFP 8%, affiliate 8%, ads 10% and target margin 21.11%. Reserve 30% discount, ceil, then enforce CNY 5 profit. |
| LivelyHive GB | GBP | Goods + local shipping tier, divided by 1 minus VAT-effective 1/6, commission 9%, Smart Promo 1.8%, ads 20% and target margin 16.95%. Reserve 25% discount, ceil, then enforce CNY 5 profit. |

For SEA, actual weight is rounded upward to 10 g. The logistics functions are
PH `rounded_g * 0.45`, MY `rounded_g * 0.015`, TH `rounded_g * 0.10`, and VN
`11700 + floor(max(0, rounded_g - 10) / 10) * 900`. The release payload keeps
the resulting fee values and `header_meta` rates rather than reconstructing
labels in the new UI.

## Existing adapter inheritance

TikTok/Miaoshou writes each selected store's `list_price` to `skuMap.*.price`
and `priceIncludeVat`; replicated stores receive their own country/store row.

Shopee is not independently repriced. Its existing global-product adapter
reads a TikTok SKU `sale_price` and computes:

`global_original_price_cny = round(tiktok_sale_price * settings.exchange_rates[currency], 2)`

The local publish task historically reuses the TikTok source numeric price.
The release dashboard therefore labels Shopee price rows
`awaiting_tiktok_readback`; its derived number is a preview, not an executable
price.

Ozon selects a valid TikTok region price, converts it using its configured
currency table, and computes:

`price_cny = round(tiktok_price * exchange_rate)`

`old_price_cny = round(price_cny * 1.3)`

Ozon is also `awaiting_tiktok_readback`. For the current multi-country flow,
the deterministic master priority is PH, MY, TH, VN, MX, then GB.

## Release contract and risks

`pricing_review.target_pricing` is joined into each
`omnichannel_preview.targets[]` row. Product approval fingerprints and
omnichannel confirmation tokens are bound to selected store prices and the
relevant FX tables, so a price/rate change invalidates the prior approval
scope.

Known risks intentionally remain visible:

- workbench, Shopee and Ozon may use different persisted/configured FX tables;
- Shopee/Ozon must recompute from the verified TikTok read-back rather than
  trusting the pre-publication preview;
- Ozon's legacy fields are named `price_cny` even though Ozon settlement is
  RUB; the existing semantic is preserved, not silently corrected;
- TikTok SEA adapter paths remain unaudited in the omnichannel execution gate,
  even though their pricing rows can be calculated;
- no target becomes executable merely because a price preview exists.
