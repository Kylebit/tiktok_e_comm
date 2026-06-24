# API Reference - TikTok Product Publish

## Overview

This document describes the APIs for publishing products from TikTok collect box to TikTok shops.

## APIs

### 1. Get Category Tree

Get the category tree structure for a specific site.

**Endpoint:**
```
POST /open/v1/product/collect_box/tiktok/collect_box/get_category_tree_by_site
```

**Request:**
```json
{
  "site": "US"
}
```

**Response:**
```json
{
  "result": "success",
  "code": "200",
  "data": {
    "cateTree": {
      "10000": {
        "cid": 10000,
        "aid": 10000,
        "fid": 0,
        "name": "Women's Clothing",
        "nameChinese": "女装",
        "isLastLevel": "false",
        "disabled": false,
        "children": {
          "10001": {
            "cid": 10001,
            "aid": 10000,
            "fid": 10000,
            "name": "Dresses",
            "nameChinese": "连衣裙",
            "isLastLevel": "true",
            "disabled": false,
            "children": {}
          }
        }
      }
    }
  }
}
```

### 2. Get Category Metadata

Get attribute requirements and configurations for a category.

**Endpoint:**
```
POST /open/v1/product/collect_box/tiktok/collect_box/get_category_metadata
```

**Request:**
```json
{
  "site": "US",
  "cid": 12345,
  "shopIds": [10001]
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| categoryConfig | object | Category configuration |
| categoryConfig.sizeChartIsSupported | string | Whether size chart is supported |
| categoryConfig.sizeChartIsRequired | string | Whether size chart is required |
| categoryConfig.codIsSupported | string | Whether COD is supported |
| categoryConfig.packageDimensionIsRequired | string | Whether package dimensions are required |
| categoryConfig.eprIsRequired | string | Whether EPR is required |
| categoryConfig.responsiblePersonIsRequired | string | Whether responsible person is required |
| categoryConfig.manufacturerIsRequired | string | Whether manufacturer is required |
| categoryConfig.productCertifications | array | Required certifications |
| categorySaleAttrList | array | Sale attributes (color, size, etc.) |
| categoryProductAttrList | array | Product attributes |

### 3. Get Shop Warehouse List

Get warehouse information for shops.

**Endpoint:**
```
POST /open/v1/product/collect_box/tiktok/collect_box/get_shop_warehouse_list
```

**Request:**
```json
{
  "shopIds": [10001, 10002]
}
```

**Response:**
```json
{
  "result": "success",
  "code": "200",
  "data": {
    "shopWarehouseList": [
      {
        "shopId": 10001,
        "shopName": "My Shop",
        "platform": "tiktok",
        "site": "US",
        "warehouseList": [
          {
            "shopId": "10001",
            "warehouseId": "WH001",
            "warehouseName": "Default Warehouse",
            "warehouseSubType": "SELF_BUILT",
            "warehouseEffectStatus": "EFFECTIVE",
            "isDefault": "1"
          }
        ]
      }
    ]
  }
}
```

### 4. Get Responsible Person List

Get EU responsible person list for a shop.

**Endpoint:**
```
POST /open/v1/product/collect_box/tiktok/collect_box/get_responsible_person_list
```

**Request:**
```json
{
  "shopId": 10001,
  "refresh": 0
}
```

**Response:**
```json
{
  "result": "success",
  "code": "200",
  "data": {
    "responsiblePersonList": [
      {
        "id": "RP001",
        "name": "Responsible Person Name"
      }
    ]
  }
}
```

### 5. Claim to Shop

Claim products to pre-publish shops.

**Endpoint:**
```
POST /open/v1/product/collect_box/tiktok/collect_box/claim_to_shop
```

**Request:**
```json
{
  "detailIds": [12345, 12346],
  "shopIds": [10001]
}
```

**Response:**
```json
{
  "result": "success",
  "code": "200",
  "message": "Success"
}
```

### 6. Publish Products

Publish products to shops.

**Endpoint:**
```
POST /open/v1/product/collect_box/tiktok/collect_box/save_move_collect_task
```

**Request:**
```json
{
  "detailIds": [12345, 12346],
  "shopIds": [10001]
}
```

**Response:**
```json
{
  "result": "success",
  "code": "200",
  "message": "Success"
}
```

## Authentication

All APIs use HmacSHA256 signature authentication.

### Signature Algorithm

```python
sign = HmacSHA256(
    app_secret,
    app_secret + path + timestamp + app_key + body_json + app_secret
)
```

### Headers

| Header | Value |
|--------|-------|
| Content-Type | application/json |
| x-app-key | Your app key |
| x-timestamp | Unix timestamp (seconds) |
| x-sign | Generated signature |

### Signature Validity

- Valid for 5 minutes from timestamp

## Error Codes

| Code | Description |
|------|-------------|
| signExpired | Signature expired |
| signInvalid | Invalid signature |
| appNotFound | App not found |
| ipNotInWhitelist | IP not in whitelist |
| accountQpsRateLimit | Rate limit exceeded |
| categoryNotFound | Category not found |
| missingRequiredField | Missing required field |
| warehouseNotFound | Warehouse not found |
| responsiblePersonNotFound | Responsible person not found |
| productNotFound | Product not found |
| shopNotFound | Shop not found |

## Site Codes

| Code | Market |
|------|--------|
| US | United States |
| UK | United Kingdom |
| SG | Singapore |
| MY | Malaysia |
| TH | Thailand |
| PH | Philippines |
| VN | Vietnam |

