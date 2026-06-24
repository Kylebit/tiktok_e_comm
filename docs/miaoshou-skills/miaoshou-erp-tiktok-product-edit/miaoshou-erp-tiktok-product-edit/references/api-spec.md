# 妙手ERP TikTok采集箱 OpenAPI 完整规范

---

## 认证说明

JCOP Open Platform HmacSHA256 签名认证：

```
sign = HmacSHA256(appSecret, appSecret + path + timestamp + appKey + bodyJson + appSecret)
```

- timestamp = **秒级** Unix 时间戳
- headers: `x-app-key`, `x-timestamp`, `x-sign`, `Content-Type: application/json`
- 签名有效期：5分钟

---

## 站点代码参考

| 代码 | 站点 |
|------|------|
| MY | 马来西亚 |
| SG | 新加坡 |
| PH | 菲律宾 |
| TH | 泰国 |
| VN | 越南 |
| ID | 印度尼西亚 |

---

## API 1: 获取采集箱列表

**接口**: `POST /open/v1/product/collect_box/tiktok/collect_box/search_collect_box_detail_list`

### 请求体
```json
{
  "pageNo": 1,
  "pageSize": 20,
  "filter": {
    "status": "notPublished",
    "sourceItemIdKeyword": ""
  }
}
```

### status 枚举
- `notPublished` - 未发布
- `timingPublish` - 定时发布
- `published` - 已发布

### 响应 data[] 字段
| 字段 | 类型 | 说明 |
|------|------|------|
| collectBoxDetailId | string | 采集箱详情ID（主键） |
| itemNum | string | 商品编号 |
| stock | string | 库存 |
| price | string | 价格 |
| thumbnail | string | 缩略图URL |
| title | string | 商品标题 |
| status | string | 状态 |
| editModel | string | 编辑模式（shop/site） |
| commonCollectBoxDetailId | string | 公共采集箱详情ID |
| platform | string | 平台 |
| gmtCreate | string | 创建时间 |
| remark | string | 备注 |
| collectBoxDetailShopList | array | 关联店铺列表（站点模式有） |
| collectBoxDetailShopList[].shopId | string | 店铺ID |
| isSupportReplicateProduct | boolean | 是否支持新链路 |

---

## API 2: 获取采集箱店铺模式详情

**接口**: `POST /open/v1/product/collect_box/tiktok/collect_box/get_shop_collect_item_info`

### 请求体
```json
{
  "detailId": 12345,
  "shopId": 1001
}
```

### 响应 data 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| ossMd5 | string | OSS MD5值（**保存时必传**） |
| editModel | string | 编辑模式 |
| claimToShopIds | array | 已认领的店铺ID列表 |
| isSupportMultiWarehouse | integer | 是否支持多仓库 |
| shopCollectItemInfo | object | 店铺模式数据 |

### shopCollectItemInfo 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| site | string | 站点代码（如 MY、SG） |
| shopId | integer | 店铺ID |
| detailId | integer | 详情ID |
| title | string | 商品标题 |
| oriTitle | string | 原始标题 |
| notes | string | 商品描述 |
| cid | string | 类目ID |
| brandId | string | 品牌ID |
| brandName | string | 品牌名称 |
| imgUrls | array | 商品图片URL列表 |
| weight | number | 重量（kg） |
| packageLength | integer | 包裹长度（cm） |
| packageWidth | integer | 包裹宽度（cm） |
| packageHeight | integer | 包裹高度（cm） |
| isCodOpen | string | 货到付款（1=是，0=否） |
| mainImgVideoUrl | string | 主图视频URL |
| mainImgPlatformVideoId | string | 主图平台视频ID |
| sizeChartType | string | 尺码表类型 |
| sizeChart | string | 尺码表图片URL |
| deliveryOptionSetType | string | 发货方式（仅 default） |
| manufacturerIds | array | 制造商ID列表 |
| responsiblePersonIds | array | 责任人ID列表 |
| skuPropertyList | array | SKU规格属性列表 |
| skuMap | object | SKU Map（key=规格组合，value=SKU数据） |
| productAttributes | array | 商品属性列表 |
| productCertifications | array | 商品认证列表 |

### skuPropertyList[] 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| attrId | string | 属性ID |
| attrName | string | 属性名称（Color、Size 等） |
| attrValueList | array | 属性值列表 |
| attrValueList[].attrValueId | string | 属性值ID |
| attrValueList[].attrValue | string | 属性值名称 |
| attrValueList[].imgUrl | string | 属性值图片URL（规格颜色缩略图） |
| attrValueList[].supplementarySkuImageUrls | array | 补充SKU图片URL列表 |

### skuMap{key} 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| price | number | 销售价格 |
| priceIncludeVat | number | 本地展示价 |
| originPrice | number | 货源价格 |
| itemNum | string | 货品编号（平台SKU） |
| stock | integer | 库存数量 |
| identifierCode | string | 识别码 |
| identifierCodeType | string | 识别码类型 |
| isDelete | string | 是否删除（0-否，1-是） |
| weight | number | 重量（kg） |
| shopIdToWarehouseIdAndStockMap | object | 店铺→仓库→库存映射 |

### productAttributes[] 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| attributeId | string | 属性ID |
| attributeName | string | 属性名称 |
| attributeNameAlias | string | 属性名称别名（中文） |
| attributeValues | array | 属性值列表 |
| attributeValues[].valueName | string | 属性值名称 |
| attributeValues[].valueId | string | 属性值ID |

---

## API 3: 保存采集箱店铺模式详情

**接口**: `POST /open/v1/product/collect_box/tiktok/collect_box/save_shop_collect_item_info`

### 请求体
```json
{
  "ossMd5": "abc123...",
  "detailId": 12345,
  "shopId": 1001,
  "shopCollectItemInfo": {
    "title": "商品标题",
    "notes": "商品描述",
    "imgUrls": ["url1", "url2"],
    "brandId": "",
    "weight": 0.5,
    "packageLength": 10,
    "packageWidth": 10,
    "packageHeight": 5,
    "isCodOpen": "0",
    "cid": "12345",
    "editModel": "shop",
    "deliveryOptionSetType": "default",
    "sizeChartType": "image",
    "sizeChart": "",
    "skuMap": {},
    "skuPropertyList": [],
    "productAttributes": [],
    "manufacturerIds": [],
    "responsiblePersonIds": [],
    "productCertifications": [],
    "mainImgVideoUrl": "",
    "version": ""
  }
}
```

### 必填字段（shopCollectItemInfo 内）
- `title`
- `notes`
- `imgUrls`
- `weight`
- `packageLength`
- `packageWidth`
- `packageHeight`
- `isCodOpen`
- `deliveryOptionSetType`

### skuMap{key} 必填子字段
- `weight`（**必填**）

### 响应
```json
{
  "result": "success",
  "code": "0",
  "data": {
    "ossMd5": "new_md5_value..."
  }
}
```

---

## API 4: 获取采集箱站点模式详情

**接口**: `POST /open/v1/product/collect_box/tiktok/collect_box/get_site_collect_item_info`

### 请求体
```json
{
  "detailId": 12345,
  "site": "MY"
}
```

### 响应结构

与店铺模式类似，区别在于：

- 顶层数据为 `siteCollectItemInfo`（而非 `shopCollectItemInfo`）
- `siteCollectItemInfo` 包含 `collectBoxDetailShopList[]`（店铺特殊配置列表）

### collectBoxDetailShopList[] 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| shopId | string | 店铺ID |
| site | string | 站点代码 |
| brandId | string | 品牌ID（可覆盖） |
| brandName | string | 品牌名称 |
| deliveryOptionSetType | string | 发货方式 |
| deliveryOptionIds | array | 配送选项ID列表 |
| manufacturerIds | array | 制造商ID列表（店铺级覆盖） |
| responsiblePersonIds | array | 责任人ID列表（店铺级覆盖） |
| sizeChartTemplateId | string | 尺码表模板ID |

> 注意：站点模式中 `siteCollectItemInfo` 的 `manufacturerIds` / `responsiblePersonIds` 为**站点全局配置**，而 `collectBoxDetailShopList[]` 中的是**店铺级覆盖配置**。

---

## API 5: 保存采集箱站点模式详情

**接口**: `POST /open/v1/product/collect_box/tiktok/collect_box/save_site_collect_item_info`

### 请求体
```json
{
  "ossMd5": "abc123...",
  "site": "MY",
  "detailId": 12345,
  "siteCollectItemInfo": {
    "title": "商品标题",
    "notes": "商品描述",
    "imgUrls": [],
    "weight": 0.5,
    "packageLength": 10,
    "packageWidth": 10,
    "packageHeight": 5,
    "isCodOpen": "0",
    "cid": "12345",
    "editModel": "site",
    "deliveryOptionSetType": "default",
    "skuMap": {},
    "skuPropertyList": [],
    "productAttributes": [],
    "collectBoxDetailShopList": [],
    "productCertifications": [],
    "mainImgVideoUrl": "",
    "sizeChartType": "image",
    "sizeChart": ""
  }
}
```

### 必填字段（siteCollectItemInfo 内）
同店铺模式：`title`, `notes`, `imgUrls`, `weight`, `packageLength`, `packageWidth`, `packageHeight`, `isCodOpen`, `deliveryOptionSetType`

---

## API 6: 认领预发布店铺

**接口**: `POST /open/v1/product/collect_box/tiktok/collect_box/claim_to_shop`

### 请求体
```json
{
  "shopIds": [1001, 1002],
  "detailIds": [12345, 12346]
}
```

### 响应
```json
{
  "result": "success",
  "code": "0",
  "message": ""
}
```

> 商品在认领到具体店铺之前不可编辑。

---

## API 7: 获取店铺仓库列表

**接口**: `POST /open/v1/product/collect_box/tiktok/collect_box/get_shop_warehouse_list`

### 请求体
```json
{
  "shopIds": [1001]
}
```

### 响应 data 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| shopWarehouseList | array | 店铺仓库列表 |

### shopWarehouseList[] 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| shopId | integer | 店铺ID |
| shopName | string | 店铺名称 |
| platform | string | 平台 |
| site | string | 站点 |
| warehouseList | array | 仓库列表 |

### warehouseList[] 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| shopId | string | 店铺ID |
| warehouseId | string | 仓库ID |
| warehouseName | string | 仓库名称 |
| warehouseSubType | string | 仓库子类型 |
| warehouseEffectStatus | string | 仓库生效状态 |
| isDefault | string | 是否默认仓库 |
| inventoryRule | object | 库存规则 |

---

## API 8: 获取制造商列表

**接口**: `POST /open/v1/product/collect_box/tiktok/collect_box/get_manufacturer_list`

### 请求体
```json
{
  "shopId": 1001,
  "refresh": 0
}
```

### refresh 参数
- `0`：返回缓存数据
- `1`：重新刷新数据

### 响应 data 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| manufacturerList | array | 制造商列表 |

### manufacturerList[] 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 制造商ID |
| name | string | 制造商名称 |

---

## API 9: 获取欧盟责任人列表

**接口**: `POST /open/v1/product/collect_box/tiktok/collect_box/get_responsible_person_list`

### 请求体
```json
{
  "shopId": 1001,
  "refresh": 0
}
```

### 响应 data 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| responsiblePersonList | array | 责任人列表 |

### responsiblePersonList[] 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 责任人ID |
| name | string | 责任人名称 |

---

## 错误响应格式

```json
{
  "result": "fail",
  "code": "signInvalid",
  "message": "签名验证不通过",
  "data": null
}
```

### 常见错误码

| code | 含义 | 处理 |
|------|------|------|
| signExpired | 签名过期 | 检查系统时钟，timestamp 用秒级 |
| signInvalid | 签名无效 | 检查 app_secret |
| productNotFound | 商品不存在 | 确认 detailId |
| ossMd5Mismatch | ossMd5 不一致 | 重新获取详情（其他客户端已修改） |
| requiredFieldMissing | 必填字段缺失 | 展示缺失字段，阻断保存 |

---

## 踩坑经验

### 空响应判断

当服务器返回 `HTTP 200 + Content-Type: text/html + Content-Length: 0` 时，这是反向代理层（Nginx）的假200，表示上游 JCOP 平台不可达。

常见原因：
- VPN 已断开
- JCOP 平台服务不可用（维护/故障）
- IP 不在白名单

**判断方法**：不要只看 `resp.status_code`，要查 `Content-Type` 和 `Content-Length`。

### ossMd5 必须从最新详情获取

每次调用 `get_shop_collect_item_info` / `get_site_collect_item_info` 时，响应中的 `ossMd5` 值会变化（其他客户端修改后也会变）。保存时必须传入最新获取的 ossMd5，否则报 `ossMd5Mismatch`。

### skuMap.weight 必填

在店铺模式和站点模式中，每个 SKU 的 `weight`（重量）都是必填字段。保存前必须确保所有未删除的 SKU 都有 `weight > 0`，否则 API 报错。

### 店铺模式和站点模式二选一

同一商品不能同时使用两种模式。`get_shop_collect_item_info` 和 `get_site_collect_item_info` 返回的数据结构不同（`shopCollectItemInfo` vs `siteCollectItemInfo`），对应不同的保存接口。

