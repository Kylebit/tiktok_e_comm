# API Reference - Claim to Platform Collect Box

## Endpoint

```
POST /open/v1/product/common_collect_box/common_collect_box/claimed
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
| detailSerialNumberPlatformList | array | Yes | List of products to claim |

#### detailSerialNumberPlatformList Item

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| detailId | integer | Yes | Common collect box detail ID |
| platform | string | Yes | Target platform code |
| serialNumber | integer | Yes | Internal API compatibility field. Use default value `1`; do not ask business users for this value. |

### Supported Platforms

| Platform | Code |
|----------|------|
| TikTok Shop | `tiktok` |
| Shopee | `shopee` |
| Lazada | `lazada` |
| Amazon | `amazon` |
| Ozon | `ozon` |
| Temu | `temu` |
| Shein | `shein` |

### Request Example

```json
{
  "detailSerialNumberPlatformList": [
    {
      "detailId": 12345,
      "platform": "tiktok",
      "serialNumber": 1
    },
    {
      "detailId": 12346,
      "platform": "tiktok",
      "serialNumber": 1
    }
  ]
}
```

## Response

### Success Response (200)

```json
{
  "result": "success",
  "code": "200",
  "data": {
    "platformCollectBoxDetailIdMap": {
      "tiktok": {
        "12345": 67890,
        "12346": 67891
      }
    }
  }
}
```

#### Response Fields

| Field | Type | Description |
|-------|------|-------------|
| result | string | `"success"` or `"fail"` |
| code | string | HTTP status code or error code |
| data | object | Response data |
| data.platformCollectBoxDetailIdMap | object | Platform → ID mapping |

#### platformCollectBoxDetailIdMap Structure

```
{
  "<platform>": {
    "<commonCollectBoxDetailId>": <platformCollectBoxDetailId>,
    ...
  },
  ...
}
```

- Key: Platform name (e.g., "tiktok", "shopee")
- Value: Object mapping common collect box ID to platform collect box ID

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

### Signature Parameters

| Parameter | Description |
|-----------|-------------|
| app_secret | Your app secret from JCOP platform |
| path | API path (e.g., `/open/v1/product/common_collect_box/common_collect_box/claimed`) |
| timestamp | Current Unix timestamp (seconds) |
| app_key | Your app key from JCOP platform |
| body_json | Request body as JSON string (compact format) |

### Headers

```
x-app-key: {app_key}
x-timestamp: {timestamp}
x-sign: {sign}
```

### Signature Validity

- Valid for 5 minutes from timestamp
- Must use current system time

## Error Codes

| Code | Description | Solution |
|------|-------------|----------|
| signExpired | Signature expired | Check system clock, regenerate signature |
| signInvalid | Invalid signature | Verify app_secret and signature algorithm |
| appNotFound | App not found | Check app_key, verify app is approved |
| ipNotInWhitelist | IP not in whitelist | Add current IP to JCOP whitelist |
| accountQpsRateLimit | Rate limit exceeded | Reduce request frequency |
| productNotFound | Product not found | Verify detailId exists |
| alreadyClaimed | Already claimed | Product is already in platform collect box |

