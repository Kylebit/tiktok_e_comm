---
name: miaoshou-erp-tiktok-category-recommend
description: Recommend TikTok Shop product categories and required attribute values for Miaoshou ERP TikTok collect box products using product title, description, detail ID, site, shop IDs, category tree, and category metadata. Use when the user mentions "类目匹配", "AI类目推荐", "类目属性", "推荐属性值", "TikTok类目", "TK类目", or needs category and attribute guidance before editing or publishing.
---

# Miaoshou ERP TikTok Category Match

Recommend TikTok Shop categories and attribute values. This skill provides category guidance and structured recommendations; it does not save product data by itself.

## Typical User Requests

- "帮这个 TikTok 商品匹配类目"
- "商品 12345 应该放哪个 TikTok 类目？"
- "给我推荐类目和必填属性"
- "这个标题适合哪个 TK 类目？"
- "帮我看这个商品还缺哪些类目属性"
- "推荐结果确认后帮我写回采集箱"

## Scope

Use this skill to:

- Query TikTok category tree by site.
- Select likely leaf category candidates.
- Query category metadata.
- Recommend required and optional attributes.
- Explain missing information before editing or publishing.

Do not use this skill to save data directly. If the user wants to apply the recommendation, route the confirmed result to `miaoshou-erp-tiktok-product-edit`.

## Safety Rules

- Treat category and attribute outputs as recommendations until user confirms.
- Do not invent regulated values, product certifications, manufacturer data, warehouse, or EU responsible person information.
- Required attributes and optional attributes must be separated.
- If site or shop IDs are missing, ask for them or use shop-list guidance.
- Prefer leaf categories. If uncertain, present multiple candidates with reasons.
- Do not print secrets or signed headers.

## Inputs

The product context may come from either source:

| Input mode | Required data |
| --- | --- |
| Existing TikTok collect box product | `detailId`, mode, site, shop IDs |
| User-provided content | title, description, site, shop IDs |

If only a title is available, ask for product description or key attributes when needed.

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

1. Gather product title, description, images/keywords if available, site, and shop IDs.
2. If `detailId` is provided, query collect box detail first.
3. Query category tree for the site.
4. Use product information to shortlist top leaf category candidates.
5. Query metadata for each candidate category.
6. Recommend required attribute values and useful optional values.
7. Mark uncertain values and missing user-provided data.
8. Present a final recommendation that can be reviewed before editing.

## Recommendation Output

For each candidate category, include:

- Rank.
- Category path.
- `cid`.
- Why it matches.
- Confidence level.
- Required attributes and suggested values.
- Optional attributes worth filling.
- Missing or uncertain values.
- Whether it is ready to apply.

## Scenario Handling

| Scenario | Expected behavior |
| --- | --- |
| User provides `detailId` | Fetch product detail, then recommend category and attributes |
| User provides only title/description | Ask for site and shop IDs before querying metadata |
| Product fits multiple categories | Return top candidates with trade-offs; do not force one |
| Required compliance data is missing | Ask user to provide or query corresponding list; do not invent |
| User asks to save recommendation | Confirm exact category/attributes and hand off to TikTok collect box edit skill |

## CLI

If bundled scripts are available:

```bash
python {base_dir}/scripts/tiktok_category_match.py match --detail-id 12345 --mode site --site US --shop-ids 1001
python {base_dir}/scripts/tiktok_category_match.py match --title "women dress" --description "summer floral dress" --site US --shop-ids 1001
python {base_dir}/scripts/tiktok_category_match.py tree --site US
python {base_dir}/scripts/tiktok_category_match.py attributes --site US --cid 12345 --shop-ids 1001
```

If scripts are unavailable, call category tree and metadata endpoints through the host HTTP client or connector.

## Business Notes

- Category selection should prefer the most specific valid leaf category.
- Attribute suggestions should be conservative when product evidence is weak.
- Do not create fake brand, certification, material, origin, or compliance values.
- If the metadata says a field is required, surface it clearly even if no value can be recommended.

## Related Skills

| Next step | Skill |
| --- | --- |
| Apply confirmed category/attributes | `miaoshou-erp-tiktok-product-edit` |
| Find target shop IDs | `miaoshou-erp-shop-query` |
| Publish after data is complete | `miaoshou-erp-tiktok-product-publish` |

## Configuration

Use `resources/config.json.example` as the template for local configuration. Do not distribute real `resources/config.json`.

