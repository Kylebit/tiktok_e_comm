---
name: miaoshou-erp-tiktok-product-publish
description: Publish Miaoshou ERP TikTok collect box products to TikTok shops after readiness checks, including shop lookup, pending product listing, pre-claim checks, publish validation, and submit publish tasks. Use when the user mentions "发布商品到TK", "发布到TikTok", "TikTok发布", "TK采集箱发布", "待发布商品", "上架", or wants to publish TikTok collect box products to target shops.
---

# Miaoshou ERP TikTok Publish

Publish TikTok collect box products to TikTok shops after the product data is ready. This skill is a final workflow step and should not silently edit product data.

## Typical User Requests

- "把商品 12345 发布到 TikTok 店铺 1001"
- "检查这些 TK 商品能不能发布"
- "列出待发布商品"
- "帮我发布这批 TikTok 采集箱商品"
- "商品还没认领到店铺，先认领再发布"
- "发布后告诉我哪些成功哪些失败"

## Scope

Use this skill to:

- Query TikTok shops.
- List pending or not-yet-published products.
- Check whether products are associated with target shops.
- Verify publish readiness.
- Submit publish tasks after confirmation.
- Report task submission results and final-status limitations.

Do not use this skill to:

- Auto-edit title, description, price, stock, SKU, category, or attributes.
- Invent warehouse, responsible person, manufacturer, certification, or compliance data.
- Publish without a confirmed product/shop plan.

## Safety Classification

Publishing is a high-impact write operation. It may submit product data to TikTok shops and affect storefront listing status. It always requires explicit user confirmation.

If pre-publish claim is needed, that is also a write operation and requires a separate confirmation.

## Core Safety Rules

- Never publish until exact `detailIds`, target `shopIds`, and publish plan are confirmed.
- Do not auto-fix product data during publishing.
- If readiness checks fail, stop and route the user to `miaoshou-erp-tiktok-product-edit`.
- If a product is not associated with the target shop, ask whether to claim first.
- Do not invent or select regulated/compliance fields.
- Report asynchronous publish processing honestly.
- Do not ask users to paste secrets into chat.
- Do not print signed headers or credential-bearing request data.

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

1. Query TikTok shops and identify target `shopIds`.
2. List candidate products or query specified `detailIds`.
3. Check whether each product has target shop association.
4. Run publish readiness checks:
   - Category.
   - Required product attributes.
   - Required sale attributes and SKU values.
   - Price and stock.
   - SKU weight.
   - Package dimensions.
   - Warehouse.
   - Manufacturer/certification/responsible person/compliance fields when required.
5. If not ready, stop and explain missing fields.
6. If claim is needed, show a claim confirmation plan and execute only after confirmation.
7. Show final publish confirmation plan.
8. Submit publish only after explicit confirmation.
9. Report submitted, failed, skipped, and pending items separately.
10. Explain whether final status is immediate or server-asynchronous.

## Confirmation Template

```text
请确认 TikTok 发布计划：
- 商品ID：12345, 12346
- 目标店铺ID：1001
- 商品已关联目标店铺：是
- 发布前检查：类目、必填属性、SKU、价格、库存、重量、包裹尺寸均已通过
- 不会执行：自动改标题、改价格、改库存、补合规信息
- 说明：服务器可能异步处理发布任务，提交后需再次查询最终状态

确认后才会发布。请回复“确认发布”或“取消”。
```

Claim-before-publish template:

```text
商品尚未关联目标店铺，发布前需要先认领到店铺：
- 商品ID：12345
- 目标店铺ID：1001

认领和发布是两个独立写操作。请先确认是否执行认领。
```

## Scenario Handling

| User request | Expected behavior |
| --- | --- |
| "检查能不能发布" | Run readiness checks only; do not publish |
| "发布商品12345到店铺1001" | Query product/shop, check readiness, preview, confirm, then publish |
| "还没认领，先认领再发布" | Split into claim confirmation and publish confirmation |
| "帮我发布这批商品" | Ask for target shops if missing; show batch plan |
| Product has missing required fields | Stop and route to TikTok collect box edit |
| API returns task submitted | Report that final result may need later status check |

## Common Commands

If bundled scripts are available:

```bash
python {base_dir}/scripts/tiktok_publish.py shops
python {base_dir}/scripts/tiktok_publish.py list-products
python {base_dir}/scripts/tiktok_publish.py claim --detail-ids 12345 --shop-ids 1001
python {base_dir}/scripts/tiktok_publish.py publish --detail-ids 12345 --shop-ids 1001
python {base_dir}/scripts/tiktok_publish.py categories --site US
python {base_dir}/scripts/tiktok_publish.py attributes --site US --cid 12345 --shop-ids 1001
python {base_dir}/scripts/tiktok_publish.py warehouses --shop-ids 1001
python {base_dir}/scripts/tiktok_publish.py responsible-persons --shop-id 1001
```

If scripts are unavailable, call the documented endpoints through the host HTTP client or connector.

## Publish Readiness Details

| Check | Handling |
| --- | --- |
| Target shop missing | Query shops and ask user to choose |
| Product not associated with shop | Ask whether to claim first |
| Category missing or non-leaf | Route to category match and collect box edit |
| Required attributes missing | Route to collect box edit |
| SKU invalid or too many SKUs | Report issue and route to edit |
| Price/stock/weight invalid | Report exact field and route to edit |
| Package dimensions required | Route to edit |
| Warehouse/responsible person required | Query lists if available; do not invent values |

## API Reference

Detailed endpoint behavior is in `references/api_reference.md`, including:

- Category tree.
- Category metadata.
- Warehouse list.
- Responsible person list.
- Claim to shop.
- Submit publish task.

## Failure Handling

- Missing target shop: query shops and ask for selection.
- Required field missing: stop; do not publish.
- API error: show code/message and affected IDs.
- Partial success: separate submitted, skipped, and failed products.
- Asynchronous status: say that submission succeeded but final publish status must be checked later.
- Repeated failure: do not keep retrying writes without user instruction.

## Related Skills

| Step | Skill |
| --- | --- |
| Find authorized shops | `miaoshou-erp-shop-query` |
| Claim from common collect box to TikTok collect box | `miaoshou-erp-product-claim` |
| Edit product data | `miaoshou-erp-tiktok-product-edit` |
| Recommend category and attributes | `miaoshou-erp-tiktok-category-recommend` |

## Configuration

Use `resources/config.json.example` as the template for local configuration. Do not distribute real `resources/config.json`.

