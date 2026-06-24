# 妙手ERP 货源链接采集 OpenAPI 完整规范

> 本文档为原始OpenAPI规范，供详细查阅使用。

---

## 采集货源

**接口**: `POST /open/v1/product/common_collect_box/common_collect_box/fetch_item`

**操作ID**: `6362ecb4b6c74dc5fde307cafc98050b`

### 请求头

对外使用时应通过本地配置、运行环境或正式授权流程提供认证信息，不要在对话中粘贴 Cookie、Token 或其他敏感凭证。测试环境专用参数仅限内部调试使用，生产环境不要默认携带。

| 参数 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| x-account-id | Header | integer | 是 | 账号ID |
| x-trace-id | Header | string | 否 | 链路追踪ID |
| cookie / authorization | Header/Cookie | string | 视环境而定 | 由本地配置或运行环境注入，禁止在聊天中明文传递 |

### 请求体
```yaml
collectLinks:
  - "https://www.1688.com/product/xxx.html"
  - "https://www.aliexpress.com/item/xxx.html"
```

### 请求参数说明
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| collectLinks | array[string] | 是 | 货源链接列表 |

### 响应成功示例
```yaml
{
  "result": "success",
  "code": "0",
  "data": {
    "sourceItemIdAndDetailIdMap": {
      "1688_src_123456": 100001,
      "ali_src_789012": 100002
    }
  }
}
```

### 响应字段说明
| 字段 | 类型 | 说明 |
|------|------|------|
| result | string | 请求结果（success/fail） |
| code | string | 状态码（0=成功） |
| data.sourceItemIdAndDetailIdMap | object | 货源ID与采集箱详情ID映射 |

### 错误响应格式
```yaml
{
  "result": "fail",
  "code": "500",
  "message": "参数错误"
}
```

---

## 数据流转示意

```
货源链接 → fetch_item API → 妙手ERP后台采集 → 公共采集箱
                                    ↓
                         返回 sourceItemId (货源ID)
                         返回 detailId (采集箱详情ID)
                                    ↓
                         用于后续认领到平台采集箱
```

---

## 链接格式参考

### 1688
```
https://detail.1688.com/offer/123456789.html
https://www.1688.com/product/xxx.html
```

### AliExpress
```
https://www.aliexpress.com/item/123456789.html
```

### 淘宝/天猫
```
https://item.taobao.com/item.htm?id=123456789
https://detail.tmall.com/item.htm?id=123456789
```

### 拼多多
```
https://mobile.yangkeduo.com/product.html?goods_id=123456789
```

