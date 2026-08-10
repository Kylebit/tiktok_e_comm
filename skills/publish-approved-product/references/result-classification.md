# Result classification

Platform-specific verification scope is authoritative. A TikTok
`miaoshou_collectbox_receipt` proves draft readiness only and cannot turn a
rejected final dispatch into success. Official Shopee/Ozon product readback may
still correct a false-negative dispatch result when it proves the exact current
product and complete SKU set exists.

Keep all detailed evidence internally. Show the user only one of four labels.

| Internal rule | User label |
|---|---|
| Official/Miaoshou readback verifies every required target or SKU | 发布成功 |
| Provider accepted and official processing/readback is not final | 平台处理中 |
| At least one required target/SKU verified and another did not | 部分成功 |
| Request rejected, official product absent/deleted, or nothing verified after a mismatch | 发布失败 |

HTTP 200 is transport evidence only. A provider `success` field is acceptance
evidence only. Neither is sufficient for **发布成功**.

For the Product Center control wrapper, HTTP 202 plus a valid
`product-publication-start/v1` means only that one frozen-v4 platform run was
queued. Poll its exact `publication-report:<run_id>`. If the POST result or
report identity is unavailable, keep **平台处理中** and never repost blindly;
only the durable report may promote it to another state.

Internal evidence may include request attempted, provider accepted, task/item
identity, official readback, exact SKU/content checks, external-write
possibility, and safe-retry classification. Do not expose credentials, raw
responses, URLs, buyer data, or secrets in the report.
