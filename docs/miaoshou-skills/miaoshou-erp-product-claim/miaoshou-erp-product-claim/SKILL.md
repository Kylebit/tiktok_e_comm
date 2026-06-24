---
name: miaoshou-erp-product-claim
description: Claim products from Miaoshou ERP common collect box to a platform-specific collect box such as TikTok, Shopee, Lazada, Amazon, Ozon, Temu, or Shein. Use when the user mentions "认领", "认领到平台", "采集箱认领", "claim to", "move to platform", "transfer to platform", or wants to move common collect box products into a target platform collect box before editing or publishing.
---

# Miaoshou ERP Claim To Platform

Claim products from the Miaoshou ERP common collect box into a platform collect box. This operation transfers product data according to ERP backend templates; it does not edit product content.

## Typical User Requests

- "把商品 12345 认领到 TikTok"
- "把 12345,12346 从公共采集箱转到 TK 采集箱"
- "这几个公共采集箱商品认领到 Shopee 店铺 10001"
- "认领一批商品到 Ozon"
- "认领完成后给我返回平台采集箱 ID"
- "这些货源采集完了，帮我认领到 TikTok 再编辑"

## Scope

Use this skill for cross-box transfer only:

- From common collect box to platform collect box.
- From common product detail ID to platform collect box detail ID.
- Before platform-specific editing and publishing.

Do not use this skill to:

- Edit title, price, stock, images, SKU, category, or attributes.
- Publish products to shops.
- Decide a target shop without user confirmation.

## Safety Classification

Claiming is a write operation. It changes where the product exists in the ERP workflow and may create platform-specific collect box records. It must always follow preview and confirmation.

## Required Inputs

| Input | Required | Notes |
| --- | --- | --- |
| `detailIds` | Yes | Common collect box product detail IDs |
| `platform` | Yes | `tiktok`, `shopee`, `lazada`, `amazon`, `ozon`, `temu`, `shein` |

`serialNumber` is an internal API compatibility field. Do not ask the user for it. Always use the default value `1` unless a developer or connector explicitly supplies another value.

## Platform Aliases

| Platform | Code | Common aliases |
| --- | --- | --- |
| TikTok Shop | `tiktok` | `tt`, `tk`, `tiktok`, `TikTok` |
| Shopee | `shopee` | `sp`, `shopee` |
| Lazada | `lazada` | `lz`, `lazada` |
| Amazon | `amazon` | `amz`, `amazon` |
| Ozon | `ozon` | `ozon` |
| Temu | `temu` | `temu` |
| Shein | `shein` | `shein` |

## API Authorization (Required)

Before calling any Miaoshou ERP Open Platform API, make sure the customer has authorized the skill with an approved Open Platform app.

1. Ask the customer to log in to Miaoshou ERP, open 「开放平台」, create an app, submit it for review, and use it only after approval.
2. Obtain the app credentials: `AppKey` and `AppSecret`. The customer may provide them through local configuration or a secure host connector. Do not ask the customer to paste `AppSecret` directly into chat.
3. Configure credentials in one of these ways:
   - Copy `resources/config.json.example` to `resources/config.json` and fill `app_key` and `app_secret`.
   - Or set environment variables `MIAOSHOU_APP_KEY` and `MIAOSHOU_APP_SECRET`. Optional: set `MIAOSHOU_BASE_URL`, otherwise use `https://openapi-erp.91miaoshou.com`.
4. If the customer enabled the account-level IP whitelist, confirm the machine or host running this skill is in that whitelist. The whitelist is shared by all apps under the same Miaoshou account.
5. Every API request must be `POST` with `Content-Type: application/json` and signed request headers: `x-app-key`, `x-timestamp`, and `x-sign`.

Signing contract from the Open Platform quick-start:

```text
base_url = https://openapi-erp.91miaoshou.com
sign = HmacSHA256(appSecret, appSecret + path + timestamp + appKey + bodyJson + appSecret)
```

Important details:

- `path` is only the API path, for example `/open/v1/order/create`. Do not include the domain or query string in the signature content.
- `timestamp` is a seconds-level Unix timestamp. Requests expire after 300 seconds of clock drift.
- `bodyJson` must be the exact JSON string sent in the POST body; use an empty string only when there is no body.
- `x-sign` is lowercase hex HmacSHA256 output.
- Never print `AppSecret`, signed headers, or full credential-bearing requests in the final answer or logs.

If authorization fails, handle these quick-start codes explicitly: `signMissing` means missing headers, `signExpired` means local clock or seconds timestamp problem, `signInvalid` means signature/body/path/secret mismatch, `appNotFound` means the app key is wrong, disabled, or not approved, `appNoPermission` means the app lacks endpoint permission, and `ipNotInWhitelist` means the caller IP is not allowed.

## Standard Workflow

1. Parse product IDs and target platform.
2. Ask for missing product IDs or platform only. Do not ask the user for `serialNumber`; use internal default `1`.
3. Query product detail for every common collect box `detailId`.
4. Show a product preview with ID, title, price, SKU count, and image count when available.
5. Show a claim plan including product count, target platform, and transfer behavior. Do not display `serialNumber` in user-facing confirmation text.
6. Execute the claim API only after explicit user confirmation.
7. Report success and failure separately.
8. Preserve the mapping from common collect box ID to platform collect box ID for downstream editing.
9. Offer the relevant platform collect box editing skill only for successful items.

## Confirmation Template

```text
请确认认领计划：
- 商品数量：N
- 公共采集箱商品ID：12345, 12346
- 目标平台：TikTok
- 将迁移：标题、价格、图片、SKU、属性
- 不会执行：编辑、改价、改库存、改类目、发布

确认后才会调用认领接口。请回复“确认认领”或“取消”。
```

## Scenario Handling

| User request | Expected behavior |
| --- | --- |
| "认领商品12345到TikTok" | Preview and confirm; use internal `serialNumber=1` |
| "把这5个商品认领到Shopee" | Confirm the five IDs, preview, and use internal `serialNumber=1` |
| "把商品A和B认领到Ozon" | Resolve/confirm actual detail IDs before any API call |
| "认领一批商品" | Ask for product IDs and platform |
| "商品12345认领" | Ask for platform |

## API Summary

- Endpoint: `POST /open/v1/product/common_collect_box/common_collect_box/claimed`
- Request field: `detailSerialNumberPlatformList`
- Key item fields: `detailId`, `platform`, `serialNumber`
- Response field: `platformCollectBoxDetailIdMap`

See `references/api_reference.md` for full request, response, and error-code details.

## CLI

If bundled scripts are available, use:

```bash
python {base_dir}/scripts/claim_to_platform.py platforms
python {base_dir}/scripts/claim_to_platform.py detail --detail-id 12345
python {base_dir}/scripts/claim_to_platform.py claim --detail-ids 12345,12346 --platform tiktok
```

If scripts are unavailable, call the endpoint through the host HTTP client or connector.

## Failure Handling

| Error | Meaning | Safe response |
| --- | --- | --- |
| `productNotFound` | Product ID is invalid or unavailable | Ask user to confirm detail ID |
| `alreadyClaimed` | Product has already been claimed to the platform | Report existing state and suggest checking platform collect box |
| `invalidSerialNumber` | Internal serial number rejected by API | Report a technical configuration issue; do not ask normal business users for this value |
| signature errors | Auth/signing failure | Check local config and signing; do not expose secrets |

For partial failures, show successful mappings and failed IDs separately. Do not retry failed writes automatically.

## Related Skills

| Step | Skill |
| --- | --- |
| Find target shop | `miaoshou-erp-shop-query` |
| Edit common collect box before claim | `miaoshou-erp-common-collectbox-manage` |
| Edit TikTok platform collect box after claim | `miaoshou-erp-tiktok-product-edit` |
| Publish TikTok product after edit | `miaoshou-erp-tiktok-product-publish` |

## Configuration

Use `resources/config.json.example` as the template for local configuration. Do not distribute real `resources/config.json`.

