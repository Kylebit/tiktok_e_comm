---
name: miaoshou-erp-tiktok-product-edit
description: Query, diagnose, and edit Miaoshou ERP TikTok collect box products before publishing, including list/detail queries, shop mode and site mode edits, title, description, SKU, price, stock, weight, images, category, attributes, warehouse, manufacturer, responsible person, and publish readiness checks. Use when the user mentions "TikTok采集箱", "TK采集箱", "TikTok商品编辑", "TK商品查询", "补全发布信息", or wants to prepare TikTok collect box products for publishing.
---

# Miaoshou ERP TikTok Collect Box

Query and edit TikTok collect box products before publishing. This skill is responsible for product data readiness, not final publishing.

## Typical User Requests

- "查询 TikTok 采集箱商品 12345"
- "帮我补全这个 TK 商品的标题和描述"
- "把商品 12345 的类目和属性改成推荐结果"
- "检查这个 TikTok 商品能不能发布"
- "把 US 站点这个商品的 SKU 重量补齐"
- "给这个商品设置店铺维度信息"
- "这个商品为什么发布不了，帮我诊断"

## Scope

Use this skill to:

- Query TikTok collect box list and detail.
- Edit product fields before publishing.
- Diagnose missing category, attributes, SKU, price, stock, weight, package dimensions, warehouse, and compliance fields.
- Apply confirmed category recommendations from `miaoshou-erp-tiktok-category-recommend`.
- Prepare products for `miaoshou-erp-tiktok-product-publish`.

Do not use this skill to perform final publish. Publishing belongs to `miaoshou-erp-tiktok-product-publish`.

## Safety Classification

| Operation | Safety level | Requirement |
| --- | --- | --- |
| List/detail query | Read-only | Can run when scope is clear |
| Diagnosis | Read-only | Report issues without writing |
| Save/edit | Write | Before/after preview and confirmation required |
| Applying AI-generated content | Write | Mark as recommendation, preview, confirmation required |

## Core Safety Rules

- Do not save any AI-generated title, description, category, or attribute without explicit user confirmation.
- Do not invent compliance values, certifications, manufacturers, responsible persons, warehouse, or regulated product data.
- Preserve existing fields that are not intentionally changed.
- Do not submit partial payloads that may erase existing data.
- Validate required fields before saving.
- Stop on edit failure; do not proceed to publish after failed save.
- Do not ask users to paste secrets into chat.

## Shop Mode vs Site Mode

| Mode | Detail API | Save API | Use when |
| --- | --- | --- | --- |
| Shop mode | `get_shop_collect_item_info` | `save_shop_collect_item_info` | Editing one shop-specific product view |
| Site mode | `get_site_collect_item_info` | `save_site_collect_item_info` | Editing shared site-level data for multiple shops |

Important distinction:

- `collectBoxDetailShopList` exists in site mode and stores shop-specific override data.
- Do not overwrite shop-specific overrides unless the user explicitly asks to modify shop-level differences.
- If the user has multiple shops, prefer site mode for shared base data and shop mode for one-shop exceptions.

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

1. Identify `detailId`, mode, site, and shop IDs.
2. Query current product detail.
3. Diagnose missing or invalid fields.
4. If category or attributes are missing, use `miaoshou-erp-tiktok-category-recommend` for recommendations.
5. Build a change plan with:
   - Current values.
   - Proposed values.
   - Affected mode and shops.
   - Required field validation status.
   - Risks and fields intentionally left unchanged.
6. Ask for explicit confirmation.
7. Save only after confirmation.
8. Re-query or report save result.
9. Tell the user whether the product appears ready for publishing.

## Publish Readiness Checklist

Before saying a product is ready for publish, check:

- Product has target shop data or has been claimed to target shop.
- Category is a valid leaf category for the target site.
- Required product attributes are filled.
- Required sale attributes are filled.
- SKU values are valid.
- Price and stock are valid.
- SKU weight is greater than 0 for all active SKUs.
- Package dimensions are filled when required.
- Warehouse is selected when required.
- Manufacturer, certification, responsible person, or other compliance fields are filled when required by metadata.

## Confirmation Template

```text
请确认 TikTok 采集箱修改计划：
- 商品ID：12345
- 模式：site
- 站点/店铺：US / 1001
- 修改字段：title, category, required attributes
- 不修改字段：price, stock, images, SKU, warehouse
- 风险检查：必填属性已填，SKU weight > 0，库存未超限

确认后才会保存。请回复“确认保存”或“取消”。
```

## Scenario Handling

| User request | Expected behavior |
| --- | --- |
| "查询商品" | Read detail and summarize key readiness fields |
| "补全标题描述" | Generate suggestion, preview, wait for confirmation before save |
| "应用类目推荐" | Show exact category/attributes to save; confirm before write |
| "检查能不能发布" | Run readiness checklist; do not save or publish |
| "多店铺一起改" | Use site mode unless shop-specific overrides are requested |
| "只改某个店铺" | Use shop mode |

## CLI

If bundled scripts are available:

```bash
python {base_dir}/scripts/tiktok_collectbox.py list --page 1 --size 20
python {base_dir}/scripts/tiktok_collectbox.py detail --detail-id 12345 --mode site --site US --shop-ids 1001
python {base_dir}/scripts/tiktok_collectbox.py save --detail-id 12345 --mode site --site US --shop-ids 1001 --payload payload.json
python {base_dir}/scripts/tiktok_collectbox.py --help
```

If scripts are unavailable, call the documented endpoints through the host HTTP client or connector.

## API Reference

Detailed request payloads, field definitions, mode behavior, and endpoint names are in `references/api-spec.md`. Load it before save operations or complex field edits.

## Failure Handling

- Required field missing: report field and recommended next step.
- Metadata mismatch: re-query category metadata and avoid saving stale attribute values.
- Save failure: show upstream message and do not publish.
- Partial shop update issue: separate site-level and shop-level data in the response.
- Signature/auth errors: check local config or connector; do not expose secrets.

## Related Skills

| Step | Skill |
| --- | --- |
| Recommend category/attributes | `miaoshou-erp-tiktok-category-recommend` |
| Find shop IDs | `miaoshou-erp-shop-query` |
| Publish ready products | `miaoshou-erp-tiktok-product-publish` |

## Configuration

Use `resources/config.json.example` as the template for local configuration. Do not distribute real `resources/config.json`.

