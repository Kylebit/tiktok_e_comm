---
name: miaoshou-erp-common-collectbox-manage
description: Query, create, edit, and delete Miaoshou ERP common collect box products, including SPU fields, SKU fields, images, price, stock, weight, source attributes, and collect box product records. Use when the user mentions "公共采集箱", "创建采集箱商品", "编辑采集箱", "修改商品信息", "删除采集箱商品", "批量删除", "新增商品到采集箱", "改价", "改库存", "SKU编辑", or wants CRUD operations on common collect box products.
---

# Miaoshou ERP Common Collect Box CRUD

Manage products in the Miaoshou ERP common collect box. This skill contains the operational rules needed to safely query, create, edit, and delete collect box products.

## Typical User Requests

- "查询公共采集箱商品 12345"
- "把商品 12345 的价格改成 69.8"
- "批量把这些商品库存改成 999"
- "把这几个 SKU 的价格涨 10%"
- "删除公共采集箱里的 12345"
- "新增一个公共采集箱商品"
- "删除第3张图片"
- "把标题里的某个词替换掉"

## Scope

Use this skill for common collect box records only. It does not edit platform-specific TikTok/Shopee collect boxes after claim, and it does not publish products.

Supported operation types:

- Read product list or detail.
- Create a common collect box product.
- Edit SPU fields such as title, price, stock, weight, dimensions, images, and notes.
- Edit SKU fields such as SKU price, stock, item number, weight, and property values.
- Delete products or remove selected product assets when supported by API.

## Safety Classification

| Operation | Safety level | Requirement |
| --- | --- | --- |
| Query | Read-only | Can run after required ID/scope is clear |
| Create | Write | Preview and confirmation required |
| Edit | Write | Query current values, validate risks, preview, confirmation required |
| Delete | Destructive | Strict confirmation with exact IDs/count required |

## Core Safety Rules

- Never execute create, edit, or delete from a vague instruction.
- Query current product detail before every edit or delete.
- Show current value, target value, affected fields, and validation risks before writing.
- For batch operations, show item count and affected product IDs.
- For delete operations, require explicit confirmation that includes the product IDs.
- Do not ask users to paste secrets into chat. Credentials must come from local config, environment, or host connector.
- Do not print signed headers or credential-bearing request data.
- Stop on API errors and report partial success/failure clearly.

## Important Business Rules

### 1. Query all fields that can affect save validation

Do not query only the field the user wants to change. Some API validations depend on related fields.

Examples:

| User wants to edit | Also inspect |
| --- | --- |
| SPU `price` | SPU `stock`, SKU stock |
| SPU `stock` | SPU price, SKU stock |
| SKU price | SKU stock, original stock |
| SKU stock | SKU price, original stock |
| SKU fields | Complete SKU object, not only changed field |

Known validation rule:

- Stock values above `99999` may block save operations. If current SPU/SKU/original stock exceeds this limit, present the risk and ask whether to correct, skip, or cancel.

### 2. SKU edits must preserve complete SKU records

When editing `skuMap`, do not submit only the changed field. Preserve all required SKU fields returned by detail API.

Correct approach:

```json
{
  "price": 71.28,
  "stock": 99999,
  "oriPrice": 80.00,
  "oriStock": 99999,
  "itemNum": "SKU-RED-S",
  "weight": 0.3
}
```

Incorrect approach:

```json
{
  "price": 71.28
}
```

### 3. Present risks once, not one by one

After querying product detail, summarize all detected risks in a single preview:

- Current values.
- Proposed values.
- Related field risks.
- Fields that may be automatically preserved or corrected.
- Confirmation options.

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

1. Classify the request: query, create, edit, delete, or batch operation.
2. Collect required IDs, target fields, and target values.
3. Query current product detail for every affected item.
4. Build a change plan:
   - Product ID and title.
   - Current values.
   - Target values.
   - Affected SPU/SKU/image fields.
   - Validation risks and recommended handling.
5. Ask for explicit confirmation for write operations.
6. Execute the API or script after confirmation.
7. Report result per product. Separate success, skipped, and failed items.

## Confirmation Templates

Edit:

```text
请确认修改计划：
- 商品ID：12345
- 标题：示例商品
- 修改字段：price
- 当前值：64.80
- 目标值：69.80
- 关联检查：SPU库存=99999，SKU库存未超限
- 不修改字段：标题、图片、类目、SKU属性

确认后才会写入。请回复“确认修改”或“取消”。
```

Delete:

```text
请确认删除计划：
- 删除商品数：3
- 商品ID：12345, 12346, 12347
- 操作结果：从公共采集箱删除，可能不可撤销

请回复“确认删除 12345,12346,12347”后再执行。
```

## Scenario Handling

| User request | Expected behavior |
| --- | --- |
| "把价格改成100" | Ask whether SPU price or SKU price if ambiguous; query current detail |
| "价格涨10%" | Calculate proposed values from current values, preview before writing |
| "删除SKU" | Show SKU list and ask which SKU to delete |
| "删除第3张图片" | Query image list, show the target image, ask for confirmation |
| "批量改库存" | Query all affected products, detect invalid current stock, present one batch plan |
| "新增商品" | Collect required fields and preview full product structure before create |

## CLI

If bundled scripts are available, use script help to confirm exact commands:

```bash
python {base_dir}/scripts/collectbox_crud.py --help
python {base_dir}/scripts/collectbox_crud.py get --id 12345
python {base_dir}/scripts/collectbox_crud.py list --page 1 --size 20
python {base_dir}/scripts/collectbox_crud.py edit --id 12345 --field price --value 69.8
python {base_dir}/scripts/collectbox_crud.py delete --ids 12345,12346
```

If scripts are unavailable, call the documented API endpoints through the host HTTP client or connector.

## API Reference

Detailed endpoint behavior, field definitions, and payload structures are in `references/api_reference.md`. Load it before non-trivial create, edit, SKU, or delete operations.

## Failure Handling

- Validation error: identify the blocking field and current value.
- Partial batch failure: report successful, failed, and skipped products separately.
- Stock limit error: suggest correcting over-limit stock or skipping the affected SKU/product.
- Signature/auth errors: check local config or connector; do not expose secrets.
- Unknown API response: stop and show the upstream message; do not continue with additional writes.

## Related Skills

| Next step | Skill |
| --- | --- |
| Claim edited products to platform | `miaoshou-erp-product-claim` |
| Edit TikTok platform collect box after claim | `miaoshou-erp-tiktok-product-edit` |
| Publish TikTok products | `miaoshou-erp-tiktok-product-publish` |

## Configuration

Use `resources/config.json.example` as the template for local configuration. Do not distribute real `resources/config.json`.

