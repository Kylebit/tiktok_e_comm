# API Reference - Get Shop List

## Endpoint

```
POST /open/v1/product/shop/shop/get_shop_list
```

## Request

### Headers

| Header | Required | Description |
|--------|----------|-------------|
| Content-Type | Yes | `application/json` |
| x-app-key | Yes | App key from JCOP platform |
| x-timestamp | Yes | Unix timestamp (seconds) |
| x-sign | Yes | HmacSHA256 signature |

### Body Parameters

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| platform | string | Yes | Platform code (tiktok, shopee, lazada, etc.) |
| site | string | Yes | Site code (US, UK, etc.). Pass `""` for all sites |
| pageNo | integer | No | Page number (default: 1) |
| pageSize | integer | No | Page size (default: 100) |

### Request Example

```json
{
  "platform": "tiktok",
  "site": "",
  "pageNo": 1,
  "pageSize": 100
}
```

## Response

### Success Response (200)

```json
{
  "result": "success",
  "code": "200",
  "data": {
    "shopList": [
      {
        "shopId": 12345,
        "site": "US",
        "siteName": "United States",
        "shopNick": "MyTikTokShop",
        "platform": "tiktok",
        "isCb": 1,
        "isCnsc": 0,
        "status": "normal",
        "gmtExpire": "2026-12-31 23:59:59",
        "gmtLastAuth": "2026-01-15 10:30:00"
      }
    ]
  }
}
```

### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| shopId | integer | Shop ID |
| site | string | Site code |
| siteName | string | Site display name |
| shopNick | string | Shop name/nickname |
| platform | string | Platform code |
| isCb | integer | 1 = cross-border shop, 0 = not |
| isCnsc | integer | 1 = global shop, 0 = not |
| status | string | Shop status (normal, expired, etc.) |
| gmtExpire | string | Authorization expiry time |
| gmtLastAuth | string | Last authorization time |

### Error Response (500)

```json
{
  "result": "fail",
  "code": "ERROR_CODE",
  "message": "Error description"
}
```

## Authentication

### Signature Algorithm

```python
sign = HmacSHA256(
    app_secret,
    app_secret + path + timestamp + app_key + body_json + app_secret
)
```

Same signing method as all other Miaoshou ERP open APIs.

## Error Codes

| Code | Description | Solution |
|------|-------------|----------|
| signExpired | Signature expired | Check system clock |
| signInvalid | Invalid signature | Verify app_secret |
| appNotFound | App not found | Check app_key and approval status |
| ipNotInWhitelist | IP not whitelisted | Add current IP to whitelist |

