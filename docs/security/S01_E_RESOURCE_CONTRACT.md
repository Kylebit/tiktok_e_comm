# S01-E：四个资源消费者的有界下载

`core/resource_download.py` 只接入下面四个具名资源 GET。`core/http_retry.py` 与 `core/api_client.py` 的 S01-D 合同及实现保持原样；凭据 provider 请求不会因此自动跟跳。

| 入口与实际下游 | 具名来源 | 整次调用字节上限 | 成功输出 / 失败合同 |
| --- | --- | --- | --- |
| `modules/sourcing/pipeline._download_url` → `download_images` | `sourcing_image` | 10,000,000 | 保存原图字节；失败抛错，旧图保留，上游不把本次失败加入成功清单 |
| `modules/sourcing/scrape_1688._fetch` → `parse_html` / `_detail_images` | `sourcing_html` | 5,000,000 | HTML 字符串，保持既有 UTF-8 replacement 解码；失败抛错，由原有上游处理 |
| `modules/catalog/pdf_export._download_image` → `_draw_product_card` / `build_catalog_pdf` | `catalog_image` | 5,000,000 | 原有 RGB/400px/JPEG 转换后的 ReportLab ImageReader；失败仍返回 None |
| `modules/shopee/publish._download_image` → `_upload_images` / `_upload_images_exact` | `shopee_image` | 10,000,000 | 原图字节原子落盘后返回 Path；失败抛错，不把错误内容送到上传前消费者 |

## 传输与来源

每次下载最多 4 次实际 HTTP，最多 3 次重定向，redirect 和 transient transport/read retry 共用这 4 次预算。HTTP 非 200（含 206、429、5xx）不作为成功、不重试；301/302/303/307/308 必须通过逐跳校验。循环提前停止；超出预算不会再发下一跳。每次调用 core 明确 `attempts=1`、`allow_curl_fallback=False`，使用原私有 opener、默认环境/系统 ProxyHandler 和严格 SSLContext。每次构造新 Request，避免代理修改过的对象被下一跳复用。超时沿用消费者原值，作用于每次传输；这不是整个工作流的墙钟时间保证。

只允许 bodyless GET；拒绝 Authorization、Cookie、provider token、Host、代理认证等调用方认证/路由头。仅允许 User-Agent、Accept、identity 编码及既有静态 1688 Referer。默认代理的自身认证继续由 urllib 管理。资源下载不读 provider 凭据、不会设置 CookieHandler，不使用 curl/curlrc。

初始 URL、每次 redirect 以及每次 retry 均校验 HTTP(S)、标准端口、URL userinfo、控制字符和 HTTPS 降级。拒绝内网/回环/保留 IP、localhost/local/internal 名称，以及 DNS 返回集合中存在非公网地址的目标。保留原有公开 HTTP 起点及其 HTTPS 升级；升级后禁止退回 HTTP。没有扩大全平台域名白名单。

**DNS 限制**：这是每次调用前的公网 DNS 检查。core/操作系统和配置代理随后自行连接；本包没有 DNS 地址钉住、代理端解析或网络防火墙保证。代理自身的配置和解析属于运行环境信任边界。若环境只能在代理端解析目标、而本地无法验证公网 DNS，本合同会拒绝下载，不能把它报告为资源可用。

普通商品/CDN 参数（如 width）保留原编码。三条图片来源允许原始上游 URL 的 `sign`、`signature`、`x-signature`、`auth_key`、AWS/OSS 具名签名字段，但只允许原始精确 HTTPS origin 使用这些字段；HTML 来源不能启用此例外。API token/key/secret/password 类字段仍拒绝。相对跳转保留 Location 中的原编码，不重新编码签名；跨 CDN 跳转不自动复制 query，也不把当前 URL 放进 Referer。带签名的跨 origin Location 被拒绝，需要后续具名来源合同与来源证据，不能泛化放行所有第三方签名 URL。无签名跨 CDN HTTPS 跳转可用。这些是本地来源政策，不声称验证了远程签名或真实 CDN 当前可用性。

## 字节与实际内容

以最多 64 KiB 的 read 逐块读取；实际总字节预算包含失败尝试已读取的部分，超限哨兵最多多读 1 字节。声明 Content-Length 超限时不读正文；缺 Content-Length 仍执行实际预算。非 identity Content-Encoding 拒绝，避免压缩正文绕过限制。长度不完整作为有界读取失败处理。

图片只接受匹配 JPEG/PNG/WebP/GIF 实际格式的 MIME，经过 Pillow verify 和所有帧 load，限制最多 100 帧及总 4,000 万像素。进程若启用了 Pillow 的截断图片容忍开关则拒绝下载，不修改该全局开关。HTML 限于 text/html 或 application/xhtml+xml，要求解码后有 HTML 元素且没有 NUL；反爬、商品身份与业务解析仍由既有消费者负责。

图片全部验证后才在目标同目录创建唯一临时文件，flush/fsync 后 replace。传输、类型、解码、字节预算或 replace 失败保留旧目标；只尝试清理本调用创建的临时文件。清理自身失败时保留原写入/replace 异常，并尽可能附加说明，所属 .part 文件可能留存；不会扩大清理范围。replace 已成功时不再 unlink 已消耗的临时路径，不能将已改变的目标误报为失败。HTTPError close 失败不覆盖已有状态/目标校验失败；合法 redirect 的 close 单独失败则归一为安全错误，停止下一跳。PDF 仍遵循既有缺图回退，不把 None 声称为有图。资源异常不回显 URL、签名、响应文本或原始 provider 错误。

## 离线证据和组合边界

新增 `tests/test_resource_consumers.py` 使用真实消费者和真实 urllib opener/redirect/proxy handler，仅最低 HTTP response、DNS 与上传外部边界使用合成替身。覆盖最终文件 SHA、真实 HTML 解析、PDF ImageReader/RGB 数据与 PDF Image XObject、Shopee 两条上传前入口解码、逐跳次数和预算、错误页/断流/超限/旧图保全。认证头与 method 的拒绝补充在资源入口直接验证；已有 `tests/test_http_redirect_contract.py` 继续验证 core 凭据单跳和代理/TLS。

审计运行器在 pytest 和候选导入前安装 `sitecustomize` guard，禁真实 socket/DNS、进程启动、真实配置/业务数据库与 fixture 外写入。原 7 个业务红例及启动问题独立保存于外部审计目录 `outputs/orbithive-audit-20260905/security/s01e-validation/`；最终回执只统计最后一个唯一去重测试集合，不累计中间绿测。

Shopee 仅修改 `_download_image` 函数体，保留文件其他 imports/发布计数/UNKNOWN 合同供 B4B owner 组合，交付函数级 patch；不能整文件覆盖。R2 `prepare_product_images._download_source`、Lingshi `_download_result`、付费 checkpoint/任务/receipt/账本未改。真实下载、上传、发布、服务切换、数据库、凭据刷新及付费均未执行；最终 UI 和组合验收留给协调者。
