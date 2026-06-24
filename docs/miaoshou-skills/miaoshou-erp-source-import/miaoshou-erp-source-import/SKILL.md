---
name: miaoshou-erp-source-import
description: Collect supplier/source product links into the Miaoshou ERP common collect box. Use when the user provides product source URLs or asks to collect, fetch, import, crawl, or batch collect products from 1688, AliExpress, supplier pages, or other source links into the common collect box.
---

# Miaoshou ERP Source Collect

Collect source product links into the Miaoshou ERP common collect box and return the collected product mapping for follow-up workflows.

## Typical User Requests

- "帮我采集这个 1688 链接到公共采集箱"
- "把这批 AliExpress 商品链接导入妙手ERP"
- "采集这些货源链接，成功后给我 detailId"
- "把这几个供应商商品抓到公共采集箱"
- "批量采集下面 10 个链接"
- "采集完之后帮我认领到 TikTok"

## Scope

Use this skill for importing source links into the common collect box. It should not edit, claim, or publish products by itself.

After collection succeeds, the returned common collect box detail IDs can be used by:

- `miaoshou-erp-common-collectbox-manage` for query/edit.
- `miaoshou-erp-product-claim` for platform claim.
- `miaoshou-erp-tiktok-product-edit` after TikTok claim.

## Safety Classification

Collection is a write/import operation because it creates product records in the ERP collect box. It requires user confirmation before submitting URLs.

## Safety Rules

- Submit only complete product detail URLs.
- Do not submit search pages, category pages, store pages, short links, or ambiguous URLs without clarification.
- Show the URL count and source domains before calling the API.
- Do not edit the collected product content in this skill.
- Do not ask the user to paste cookies, tokens, passwords, or secrets into chat.
- Credentials must come from local configuration, environment variables, or the host connector.
- Do not use test-only headers in production unless the user explicitly states they are working in a test environment.

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

1. Extract URLs from the user request.
2. Remove duplicates while preserving original order.
3. Validate URL shape and identify source domains.
4. Ask the user to confirm the collection plan.
5. Call the `fetch_item` API only after confirmation.
6. Present returned `sourceItemIdAndDetailIdMap`.
7. Ask whether the user wants to query, edit, or claim the collected products next.

## Confirmation Template

```text
请确认采集计划：
- 链接数量：N
- 来源域名：1688.com, aliexpress.com
- 目标位置：妙手ERP公共采集箱
- 不会执行：编辑、认领、发布

确认后才会提交采集。请回复“确认采集”或“取消”。
```

## Scenario Handling

| Scenario | Expected behavior |
| --- | --- |
| User provides valid product detail URLs | Preview domains and count, then collect after confirmation |
| User provides mixed valid and invalid links | Separate valid/invalid links and ask how to proceed |
| User provides a supplier store page | Ask for product detail URLs |
| User wants to claim after collection | Collect first, then pass successful detail IDs to claim skill |
| API returns partial success | Show successful mappings and failed URLs/items separately |

## API Summary

- Endpoint: `POST /open/v1/product/common_collect_box/common_collect_box/fetch_item`
- Request field: `collectLinks`
- Response field: `sourceItemIdAndDetailIdMap`

Example request:

```json
{
  "collectLinks": [
    "https://www.1688.com/product/example.html",
    "https://www.aliexpress.com/item/example.html"
  ]
}
```

See `references/api-spec.md` for detailed request headers, body, response, and data-flow notes.

## Failure Handling

- Invalid URL: ask for a product detail page.
- Duplicate URL: submit once and report de-duplication.
- Auth error: check local credentials; do not expose secrets.
- Empty or timeout response: report network/service risk and avoid repeated submissions without user approval.

## Configuration

Use environment or host-provided credentials. Do not distribute real cookies, tokens, or `resources/config.json`.

