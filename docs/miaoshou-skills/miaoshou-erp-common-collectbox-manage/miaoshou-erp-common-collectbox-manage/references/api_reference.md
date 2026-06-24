# API Reference - Common Collect Box CRUD

## Overview

This document describes the APIs for CRUD operations on Miaoshou ERP common collect box products.

## APIs

### 1. Get Product List (Read)

Get paginated product list with optional filters.

**Endpoint:**
```
POST /open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_list
```

**Request:**
```json
{
  "pageNo": 1,
  "pageSize": 20,
  "filter": {
    "tabPaneName": "all",
    "sourceItemIdKeyword": "ABC123"
  }
}
```

**Filter Options:**
| Field | Type | Values |
|-------|------|--------|
| tabPaneName | string | all, noClaimed, claimed, collectFail, collectSucess |
| sourceItemIdKeyword | string | Keyword to search |

**Response:**
```json
{
  "result": "success",
  "code": "200",
  "data": {
    "detailList": [
      {
        "commonCollectBoxDetailId": 12345,
        "itemNum": "SKU-001",
        "title": "Product Title",
        "thumbnail": "https://...",
        "price": 19.99,
        "minSkuPrice": 19.99,
        "maxSkuPrice": 29.99,
        "stock": 100,
        "remark": "",
        "status": "noClaimed",
        "reason": "",
        "gmtCreate": "2026-04-28 10:00:00",
        "gmtModified": "2026-04-28 10:00:00",
        "weight": 0.5,
        "sourceList": [
          {
            "source": "1688",
            "sourceSite": "cn",
            "sourceItemId": "12345",
            "sourceItemUrl": "https://..."
          }
        ]
      }
    ],
    "total": 100,
    "isCommonCollectHuger": false
  }
}
```

### 2. Get Product Detail (Read)

Get full product detail by ID.

**Endpoint:**
```
POST /open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail
```

**Request:**
```json
{
  "commonCollectBoxDetailId": 12345
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| commonCollectBoxDetailId | integer | Common collect box ID |
| title | string | Product title |
| itemNum | string | Product number |
| notesText | string | Simple description |
| notes | string | Detailed HTML description |
| price | number | SPU price |
| stock | integer | Total stock |
| weight | number | Weight (kg) |
| imgUrls | array | Image URL list |
| sourceAttrs | array | Product attributes |
| colorPropName | string | Color property name |
| colorMap | object | Color map |
| sizePropName | string | Size property name |
| sizeMap | object | Size map |
| skuMap | object | SKU details map |
| packageLength | integer | Package length (cm) |
| packageWidth | integer | Package width (cm) |
| packageHeight | integer | Package height (cm) |
| sizeChart | string | Size chart URL |
| mainImgVideoUrl | string | Main image video URL |
| productCertifications | array | Product certifications |
| sourceList | array | Source platform info |

### 3. Create Product (Create)

Create a new product in common collect box.

**Endpoint:**
```
POST /open/v1/product/common_collect_box/common_collect_box/add_common_collect_box_detail
```

**Request:**
```json
{
  "title": "Product Title (required)",
  "itemNum": "SKU-001",
  "notesText": "Simple description",
  "notes": "Detailed description",
  "price": 19.99,
  "stock": 100,
  "weight": 0.5,
  "imgUrls": ["https://example.com/img1.jpg"],
  "sourceList": [
    {
      "source": "1688",
      "sourceItemId": "12345",
      "sourceItemUrl": "https://..."
    }
  ]
}
```

**Response:**
```json
{
  "result": "success",
  "code": "200",
  "data": {
    "commonCollectBoxDetailId": 67890
  }
}
```

### 4. Edit Product (Update)

Edit an existing product.

**Endpoint:**
```
POST /open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail
```

**Request:**
```json
{
  "commonCollectBoxDetailId": 12345,
  "editCommonCollectBoxDetail": {
    "title": "Updated Title",
    "price": 29.99,
    "stock": 200
  },
  "ossMd5": "md5_string_here"
}
```

**Note:** `ossMd5` must be obtained from the `get_common_collect_box_detail` API response.

**Response:**
```json
{
  "result": "success",
  "code": "200",
  "data": {
    "ossMd5": "new_md5_string"
  }
}
```

### 5. Batch Delete Products (Delete)

Delete multiple products by IDs.

**Endpoint:**
```
POST /open/v1/product/common_collect_box/common_collect_box/batch_delete_common_collect_box_detail
```

**Request:**
```json
{
  "commonCollectBoxDetailIds": [12345, 12346, 12347]
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

## Product Data Structure

### sourceAttrs (Product Attributes)

```json
[
  {"name": "Material", "value": "Cotton"},
  {"name": "Style", "value": "Casual"}
]
```

### colorMap (Color Map)

```json
{
  "Red": {
    "name": "Red",
    "imgUrls": ["https://.../red.jpg"],
    "imgUrl": "https://.../red_main.jpg"
  }
}
```

### skuMap (SKU Map)

```json
{
  "Red-L": {
    "itemNum": "SKU-RED-L",
    "price": "19.99",
    "stock": 50,
    "weight": 0.3,
    "packageLength": 30,
    "packageWidth": 20,
    "packageHeight": 5
  }
}
```

### sourceList (Source Info)

```json
[
  {
    "source": "1688",
    "sourceSite": "cn",
    "sourceItemId": "12345",
    "sourceItemUrl": "https://detail.1688.com/offer/12345.html"
  }
]
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

## Error Codes

| Code | Description |
|------|-------------|
| signExpired | Signature expired |
| signInvalid | Invalid signature |
| appNotFound | App not found |
| ipNotInWhitelist | IP not in whitelist |
| accountQpsRateLimit | Rate limit exceeded |
| productNotFound | Product not found |
| missingRequiredField | Missing required field (e.g., title) |

