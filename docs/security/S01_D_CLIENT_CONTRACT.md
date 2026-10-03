# TikTok 请求链与核心单跳传输

当前 `core/api_client.request` 把 API 尝试放在同一个循环，低层 `_do_request_once` 每次固定 `attempts=1`、禁 curl。读取链最多 `1 + rate_limit_retries` 次 API HTTP（配置范围 0–10），至多一次独立 auth refresh HTTP；刷新仍使用 S01-B 的单次传输与完整响应后保存。两种计数分列，不把 auth write 当作新的商业写批准。每条逻辑 request 独立计数，本包没有声称全工作流、分页集合或多个调用者共享一个总额度。

明确的 105002 是凭据过期。只有已解析到这个业务码且 HTTP 为 200/401 时才允许该链恢复一次；新的 token 留在该链中，不递归、不退回原 token。105005 为 scope 问题；36009043/36009044 为非活跃 shop/seller；其他 401、HTML 或无法解析的错误均不推定过期。对写操作，明确的 105002 是业务处理前的拒绝，保留一次 auth 恢复及新 token 提交；如果这次提交未知，必须由现有持久账本对账，不再发写请求。

一般 HTTP 429 / 36009002 与 sandbox 36009037 分开处理。前者仅在有界读取链中按 Retry-After 最小等待与原配置退避加 jitter 重试。有效 Retry-After 超过 60 秒时返回等待信息，不提前重试或阻塞进程数小时；36009037 直接报告 sandbox_hourly_limit，留待小时重置后的上层调度，不用短退避反复请求。Retry-After 支持秒数和带时区 HTTP 日期，非有限或无效值不用作服务端等待事实。

POST/PUT/DELETE、带 body 的 GET、携带 idempotency_key 本身均不构成安全读取。仅下列精确 POST 搜索合同默认可作有界读重试，`retry_read=False` 可关闭；`retry_read=True` 对其余 method/path 在发送前拒绝。不是 `/search` 后缀匹配，不为所有写 API 建立幂等框架。

| 精确端点 | 实际消费者与依据 |
|---|---|
| `/product/202309/products/search` | `modules/products/sync.py` 商品检索的既有版本合同；本轮完整正文未从官方索引重新取得，保留此已审 legacy 路径的读取语义，不声称当前官方版本可用性已验证 |
| `/product/202309/global_products/search` | 既有全局商品检索；[官方 Search Global Products](https://partner.tiktokshop.com/docv2/page/search-global-products-202309) 搜索索引正文 |
| `/order/202309/orders/search` | `modules/products/sales.fetch_orders`，本轮真实分页链覆盖；[官方订单公告](https://partner.tiktokshop.com/docv2/page/uc1ds0td) 对 202309 Get Order List 的路径说明 |
| `/promotion/202309/activities/search` | 既有活动搜索；[官方 Search Activities](https://partner.tiktokshop.com/docv2/page/search-activities-202309) 搜索索引正文 |

`_do_request_once` 保留原七参数入口以供 `modules/tiktok/oneclick_promotion._default_transport` 使用，最后 `_retry_on_401` 参数在这个单次 primitive 内不再触发恢复；恢复只能由 request 持有预算。签名排除 sign/access_token 的原公式、JSON 默认 ASCII 转义和紧凑分隔、实际发送字节、查询编码及分页输出保持。错误映射保留数值业务码、HTTP 状态、具名 error_kind 和 method/path 的不可逆 request_ref；不返回任意 provider 回显、URL/query、headers/body 或嵌套错误数据。APIRequestError 的展示不串接原始异常。成功 data 继续按原业务合同返回。

`core/http_retry` 使用私有单跳 opener，仍由 urllib 默认 ProxyHandler 选择环境/系统代理，保持显式 SSLContext。不是全局空代理，也没有全局猴补 urllib。所有自动 redirect（包括同源）均返回 HTTPError；每次调用仍只预算实际一次 HTTP。curl 不跟跳，3xx 同样不是成功资源；S01-A 原 `-q`、严格 status、代理选择、TLS 校验、自定义 CA 不转 curl、非读不重发均保留。

## 资源下载的下一接缝

本轮默认单跳会影响依赖 core 的合法 redirect 资源。以下是具名待接通消费者，不能把本包绿测当作它们已完成资源下载：

| 消费者 | 本轮影响 / 后续动作 |
|---|---|
| `modules/sourcing/pipeline._download_url` → `download_images` | 3xx 抛错，上层记录失败并不纳入 raw 图列表；需要无凭据、有界资源下载合同后恢复合法图片跳转 |
| `modules/sourcing/scrape_1688._fetch` | core 3xx 抛错；需要单独网页内容类型和总字节上限，不能复用只接受图片的 policy |
| `modules/catalog/pdf_export._download_image` | core 3xx 在既有 catch 中成为 None/缺图；后续须有实际 PDF 图片消费者成功/失败证据 |
| `modules/shopee/publish._download_image` | 先走既有 `curl.exe -L --noproxy *`，仅退出 0/非空验收，后才 core fallback；前段并未被 S01-D 修复，仍可能自动跟跳、绕代理和接收错误正文。下一包只按此函数与 B4B owner 协调，不复制整份 publish.py |
| `skills/prepare-product-images/scripts/prepare_product_images._download_source` | 独立 HTTPS-only redirect opener、image 类型及 MAX_SOURCE_BYTES，未调用 core，本轮行为不变；后续补显式 hop、目标边界、超限拒绝及真实资源链 |
| `modules/sourcing/localized_image_lingshi_generation._download_result` | 付费任务完成后的 URL→bytes，独立 requests.Session.get/trust_env=False；`brand_image_lingshi_generation` 同样消费该 helper。未直接受 core 单跳影响；下一包必须保留既有 task/receipt/paid binding，下载重试不能再次生图 |

下一合同应只处理显式无凭据 GET 资源：不接受 Authorization、Cookie、URL userinfo 或敏感 query；初始及每次跳转检查 scheme/目标范围，禁止降级/内网/越界，显式总跳数与总请求次数、总字节/内容类型/图像可解码性，保留代理/TLS，成功后原子落盘。重定向失败、HTTP 错误、超限、坏内容都保留原资源/原 receipt，不伪造下载成功或触发新的收费生成。合法签名下载 URL 的敏感 query 不能靠泛化“无凭据”绕过，需要单独具名来源和不跨域传递合同。此段是下一包接口清单，不声称已实现。

既有 R2 测试大量注入 `_download_source` / `result_loader` / `_download_result`，证明预算与恢复，不能证明真实 redirect 下载链。下包至少将 `tests/test_publication_paid_entry.py` 的 workbench 真实入口与最低 HTTP fake 配对，覆盖 brand/translation 下载失败后恢复原任务，新增真实 pipeline/PDF/Shopee 消费者资源边界。精确源码 SHA、函数范围和现有测试指针见 S01-D 审计回执的 resource-download-seams.json。

## 证据日期和当前边界

2026-09-05 核对官方 [Common errors](https://partner.tiktokshop.com/docv2/page/common-errors)、[Rate limits](https://partner.tiktokshop.com/docv2/page/rate-limits)、[Sandbox/Inactive 更新](https://partner.tiktokshop.com/docv2/page/p9x5je85) 的可搜索索引正文；直接 open 返回 19 行 JavaScript 壳，不能声称完整浏览器正文核验。更新公告发布于 2026-06-15，非活跃身份规则写明 2026-07-20 生效。本包不据此判断真实用户账户状态。

本包实际验证 Windows Python 3.12.8 / pytest 8.4.2；未安装依赖、未调用真实平台、未读取/刷新凭据、未使用生产 DB。测试从进程启动即禁外网，只有本包实际成功 bind 的 loopback 端口可连；既有 OpenSSL 仅精确本地证书参数和本包输出目录。源码测试的最低传输注入从 urllib 全局 urlopen 移到具名 `_urlopen_once`，原 TLS/代理/curl/未知写断言保持。
