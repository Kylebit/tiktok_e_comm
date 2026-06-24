---
name: miaoshou-erp-shop-query
description: Query authorized shop lists from Miaoshou ERP JCOP Open Platform, including shop IDs, shop names, sites, authorization status, expiration time, and store identifiers needed before claim, edit, or publish workflows. Use when the user mentions "店铺列表", "授权店铺", "查询店铺", "我的店铺", "shop list", "shop ID", "serialNumber", "认领到哪个店铺", or needs to choose a target shop for downstream ERP operations.
---

# Miaoshou ERP Shop List

Query authorized shops in Miaoshou ERP and help the user choose the correct shop identifier for follow-up workflows.

## Typical User Requests

- "帮我查一下 TikTok 已授权店铺"
- "我有哪些 Shopee 店铺可以认领商品？"
- "查 US 站点的 TikTok 店铺 ID"
- "发布前先帮我看一下可用店铺"
- "哪个店铺的 serialNumber 是多少？"
- "帮我确认这个商品应该认领到哪个店铺"

## Scope

This skill is read-only. It may query and summarize shops, but it must not edit products, claim products, publish products, or change authorization data.

Use this skill before:

- Claiming common collect box products to a platform collect box.
- Editing TikTok collect box products for a specific shop or site.
- Publishing TikTok collect box products to a target shop.
- Troubleshooting invalid shop ID, invalid `serialNumber`, expired authorization, or wrong site selection.

## Safety Rules

- Do not perform write operations from this skill.
- Do not ask the user to paste `app_secret`, cookies, tokens, passwords, or signed headers into chat.
- Load credentials only from local `resources/config.json`, environment variables, or the host connector.
- Do not print secrets or full signed request headers.
- If a shop is expired, disabled, unauthorized, or site-mismatched, present it as unavailable for write workflows.
- If platform or site is ambiguous, ask a concise clarification unless the user explicitly asks for all shops.

## Platform Codes

| Platform | Code | Common sites |
| --- | --- | --- |
| TikTok Shop | `tiktok` | US, UK, TH, VN, MY, PH, SG, ID |
| Shopee | `shopee` | MY, TH, VN, PH, SG, ID, TW, BR |
| Lazada | `lazada` | MY, TH, VN, PH, SG, ID |
| Amazon | `amazon` | US, UK, DE, FR, JP, CA, AU |
| Ozon | `ozon` | RU |
| Temu | `pddkj` | US, UK, DE |
| Shein | `shein` | varies by account |

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

1. Parse the requested platform and site.
2. If the user asks for all shops, query common platforms and summarize by platform/site.
3. If the user asks for a specific platform or site, query only that scope.
4. Present shop identity fields clearly: `shopId`, shop name, platform, site, authorization status, and expiration time.
5. Explain which identifier is needed next:
   - Use `shopId` for TikTok collect box edit and publish APIs.
   - Use `serialNumber` when a claim workflow explicitly requires shop sequence number.
6. If the result set is large, group by platform and site and list only available shops first.

## Response Standard

For each shop, include:

| Field | Why it matters |
| --- | --- |
| `shopId` | Used by TikTok edit/publish workflows |
| shop name | Human-readable confirmation |
| platform | Prevents cross-platform mistakes |
| site | Prevents wrong-country edits or publish attempts |
| status | Indicates whether the shop can be used |
| expiration time | Helps identify authorization risks |
| `serialNumber` | Required by some claim workflows if returned |

## Scenario Handling

| Scenario | Expected behavior |
| --- | --- |
| User asks "有哪些店铺" without platform | Ask whether to query all common platforms or a specific platform |
| User asks for TikTok US shops | Query TikTok and filter/label US shops |
| User is preparing to publish | Return `shopId`; remind that publishing requires a separate confirmation |
| User is preparing to claim | Return `serialNumber` if available; otherwise explain how to obtain it |
| Shop is expired or disabled | Do not suggest it as a target; report the issue clearly |

## CLI

If bundled scripts are available, use:

```bash
python {base_dir}/scripts/shop_list.py list --platform tiktok
python {base_dir}/scripts/shop_list.py list --platform tiktok --site US
python {base_dir}/scripts/shop_list.py list-all
python {base_dir}/scripts/shop_list.py list --platform shopee --page 1 --size 50
```

If scripts are unavailable, call the JCOP shop-list endpoint through the host HTTP client or connector. See `references/api_reference.md`.

## Failure Handling

- `signExpired`: ask the user to check local system time or regenerate signature.
- `signInvalid`: check local app credentials and signing algorithm; never ask the user to paste the secret.
- `appNotFound`: verify app authorization.
- Empty response: report possible service, network, or whitelist issue.
- No shops found: report the queried platform/site and suggest checking authorization in ERP.

## Configuration

Use `resources/config.json.example` as the template for local configuration. Do not distribute real `resources/config.json`.

