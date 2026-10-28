# U00-B1 高密度前端源与消费者交付矩阵

> **REFERENCE_ONLY / 2026-09-05 快照。** 本矩阵只用于审计当时的前端源与消费者，不能作为当前能力目录或执行入口；当前能力以 `config/capability_catalog.json` 为准。

基线 `8457cf2246c7520bade525957cba2893a9bf0526`；候选 `C:\Users\Windows11\Desktop\Agent_PR\outputs\orbithive-u00-dense-20260905`。

本包重构主工作台、七域入口表现和商品中心；未宣布全部业务页或 APP 已完成。源证据包含全文件字节比较、r4/8457cf22 差异、必要真实 schema 和改动函数审阅。函数摘要按现有两空格顶层约定提取，不能代替语义证明。

保留原商品 JS 的 223 个函数名；其中 194 个函数正文摘要不变，29 个函数有修改，新增 13 个。原 API 字面量、HTML 控件 ID 无删除，无重复 ID。

## 页面与真实消费者

| 功能 | 当前行为及消费者 |
|---|---|
| 工作台/七域/知识工具 | GET /api/orbit/entry-catalog; GET /api/orbit/navigation; runtime identity only。保留29项首审能力和12工具目录；高密度列表、简短关联流程；不将目录状态当业务完成。 |
| 任务/报告集合 | GET /api/orbit/inbox?limit=50; GET /api/orbit/report-runs?limit=50。只读按需读取；查询、空、503分别显示，不调用会读token的 /api/status。 |
| 商品队列/选择/刷新 | GET /api/product-workspace/dashboard?offer_id=...; ordinary collectIfMissing:false。URL明确选择优先；无URL不选历史首项、不自动刷新队列、不采集。每次响应校验Offer；慢A不可覆盖B。库存字段缺失显示待核对并链接供应，成本0与null分别处理。 |
| 浏览器队列批选/导出/移出 | 只读批刷 dashboard；其余仅 localStorage/Clipboard/Blob下载。0选择隐藏批量栏，撤销独立可达；不调用 reset-test-offer；导出不含批准令牌。旧 resetTestOffer 定义保留但普通UI无调用。 |
| 商品事实/审批 | 现有 facts/approve 及标题采用、审核消费者和身份校验原位保留（全部 API 字面量见 contract_preservation.api_literals）。未保存阻止切换/刷新/移出；新增本地确认放弃恢复最后读取事实/图片决定；取消保留草稿；409保留编辑。仅导航动作不隐式保存或批准。 |
| 图片选择/本地化结果 | GET /api/product-flow/preview; existing embedded-image/localized-image/proxy consumers。真实 preview 合同顶层 offer_id 必须与 cleanOfferId 严格相等后才能采用/保存副本，缺失或错商品拒绝；既有保存/审核消费者保留；加载错误可见且保留选择；503退出保存/SKU loading。新图生成依赖当前R2/付费上下文接线，不冒称就绪。 |
| 付费标题生成 | generateTitleDraft 原消费者保留但当前入口明确禁用。当前候选未接付费调用上下文；显示需接通后使用，保持现有候选/采用入口。不把旧ToAPI入口改名宣称已接新提供方。 |
| 店铺/定价 | dashboard publication_scope/pricing_review; existing governed category/price consumers。全选数量由真实可用范围生成；不扩目标、不把缺折扣/售价/利润变0；明细显示币种与来源。 |
| 逐目标账本/历史回读 | dashboard release_v1.run.targets plus existing governed recovery functions。解除隐藏账本的 operator-clutter 和 unifiedAuthority 隐藏逻辑；无当前计划仍保留历史记录。历史不授予当前批准；null attempts 显示未记录；0保留0；JSON证据转义可查看。 |
| 逐平台202发布消费者 | POST /api/product-workspace/publish-tiktok; publish-shopee-global; publish-ozon。沿用 currentReleaseBody 和服务端批准合同；新增按当前不可变plan_id账本的UI+函数前置阻断。成功/未知/执行中或同平台混合记录不能整个平台重发，不偷偷裁目标；其他计划历史不误伤当前候选，独立全PENDING平台保留。报告/恢复入口保留，当前页提交后需核对记录。 |
| 退役旧发布/其他业务页 | POST /api/product-workspace/publish returns HTTP410; other explicit legacy consumers unchanged。未接回退役写入口；原标题/图片/MX/UK、catalog/cost、供应等真实路径仍可达。其内部页面重构另包，不称全系统完成。 |

## 29 项首审能力与 12 项知识工具

29 项是已审入口集合，不是系统功能上限。以下保留真实页面/CLI/Skill 入口；文件存在不代表已配置、已安装或已完成业务验收。完整依赖和 SHA 在配套 JSON。

| 区域 | 能力 | 实际入口 |
|---|---|---|
| workbench | 统一工作台 | / |
| workbench | 任务看板 | /workbench |
| workbench | 运行检查 | /?view=system |
| workbench | 桌面 APP | python scripts/start_orbit_desktop.py |
| product | 商品发布中心 | /product-workspace |
| product | 商品目录 | /catalog |
| product | 成本维护 | /costs |
| product | 商品代理实验 | None |
| product | 1688 选品工具 | /sourcing |
| content | AI 图片工作室 | /ai-image-studio |
| content | 多语言图片结果 | /localized-image-review |
| content | 标题工具 | /titles |
| content | 历史图片工具 | /images |
| channel | 审核与渠道发布 | /product-workspace |
| channel | 妙手 ERP 接线 | /product-workspace |
| channel | Ozon 运营台 | python scripts/product_publication_runtime.py --status --include-rus |
| channel | 墨西哥历史审批 | /mx |
| channel | 英国历史审批 | /uk |
| channel | 促销与折扣 | /promotions |
| channel | 下架巡检 | /deactivate |
| channel | 达人合作 | python main.py affiliate lists |
| supply-chain | 库存与补货 | /supply-chain/ |
| supply-chain | 入库批次 | /supply-chain/inbound-batches.html |
| data | 利润中心 | /profit |
| data | SKU 利润估算 | /sku-profit |
| data | 结算与账单 | /settlement |
| data | 经营分析 | /analytics |
| data | 广告分析 | python main.py ads --help |
| knowledge | 已退役的外部通知实验 | 无当前入口；历史证据见 `docs/legacy/notifications/` |

| 工具 | 实际入口 | 安装/配置状态 |
|---|---|---|
| 第一轮 · 商品事实 | 使用 $prepare-product-publication；先阅读 skills/prepare-product-publication/SKILL.md | repository / unverified；本包未执行安装或在线验证 |
| 第二轮 · 商品图片 | 使用 $prepare-product-images；先核对当前安装 Skill 的版本与依赖，仓库流程尚待整合。 | repository / unverified；本包未执行安装或在线验证 |
| 第三轮 · 审核后发布 | 使用 $publish-approved-product；先阅读 skills/publish-approved-product/SKILL.md | repository / unverified；本包未执行安装或在线验证 |
| 精确 SKU 下架 | 使用 $delist-products-by-sku；先阅读 skills/delist-products-by-sku/SKILL.md | repository / unverified；本包未执行安装或在线验证 |
| 发布后折扣 | 使用 $apply-product-discounts；先检查折扣计划 | not_bundled / unverified；本包未执行安装或在线验证 |
| 供应与补货 Skill | 阅读 domains/supply_chain_operations/skills/manage-seaya-replenishment/SKILL.md | repository / unverified；本包未执行安装或在线验证 |
| 结算与知识归档 | 阅读 domains/data_operations/skills/manage-profit-settlement/SKILL.md | repository / unverified；本包未执行安装或在线验证 |
| TikHub 参考研究 | python <EXISTING_PROJECT>/tikhub_reference_probe.py --keyword <QUERY> --region TH --video-count 20 --output-dir <NEW_DIR> | not_bundled / unverified；本包未执行安装或在线验证 |
| DuoPlus 云手机 | python <SKILL_DIR>/scripts/duoplus_client.py preview devices | not_bundled / unverified；本包未执行安装或在线验证 |
| MiniMax H3 视频 | node <PLUGIN>/runtime/scripts/h3-cloud.mjs status --config <PROFILE> | not_bundled / unverified；本包未执行安装或在线验证 |
| Lingshi AI | 使用 $use-lingshi-ai；先检查模型、用途与生成预览 | repository / unverified；本包未执行安装或在线验证 |
| LinkFox / EchoTik 调研 | LinkfoxClient.preview("echotik_product_search", {"region":"TH","keyword":"<QUERY>","pageSize":10}) | repository / unverified；本包未执行安装或在线验证 |

## 源身份与差量合并

原始扫描 23 个 HTML、46 个文件、202 个 API 字面消费者、258 个 route 条件。所有页面逐项 SHA、8 份 r4 对比及完整原函数保留清单在 JSON。初始差异记录的 current SHA 是8457cf22未修改底座，最终SHA单独列示。

- Apply this focused diff on 8457cf22; never replace complete product_workspace.js or server.py with frozen r4 copies.
- Keep all current approval, immutable identity, category, Shopee/Ozon, polling and target recovery contracts; all 223 original top-level function names and API literals/control IDs remain.
- Merge B4B changes by affected function: renderReleaseV1/updateReleasePrimaryAction/publishPlatformBatch are deliberate overlap. New UI guard must consume current snapshot evidence from the future composed projection.
- S02-B/U02 paid context and R2 consumers are separate integration work; do not re-enable disabled title generation before actual connection and validation.
- D00 remains clean on 8457cf22; no native WebView validation or packaging claimed.
- Root-provided Sep5 video addendum: original outputs/660003_replica_compare_site commit197f4ed8580f5dd79996dd825fab9b57b5ee38a6 is newer than r4; preserve launcher/8788 per-rank video links. No access/change/start/stop to3000/8788 in this package.

## 离线复跑与浏览器验收

运行 `python -B -m pytest -q -p no:cacheprovider --basetemp <独立短路径> tests/test_dense_workspace_browser.py`。测试自动启动自己的随机端口，进程只允许该loopback和生成SQLite，禁止请求8765/8766及外部网络；浏览器二次拒绝其他origin。需要当前已安装 Chrome 和现有 Node/Playwright，可用 ORBIT_NODE_BIN / ORBIT_PLAYWRIGHT 指定，不自动安装。

按同一合成队列核验：无URL保持未选；批量只读刷新5商品及503部分失败；选择准确URL、0/null成本与未知库存；四页签、3目标范围、图片失败、慢A切B、409保留编辑、取消/确认放弃；成功/对账/PENDING账本和历史计划；已完成/未知按钮及函数不发送POST，独立Ozon只到显式fixture拦截；1440/1280/390菜单、表格和详情。

最新浏览器 103 项通过；最终逐用例合并 224 个 pytest 用例通过、0skip。原始红例及测试夹具修正前失败全部保留，详见 verification-summary.json。

## 限制

- Only homepage/seven-region entry presentation and product workspace rebuilt; other business pages remain for subsequent packages.
- No real stock quantity exists in this dashboard projection. No zero-stock display scenario fabricated; tested cost0 versus null and inventory unknown.
- Browser evidence is installed Chrome, not native WebView. Generated ReleaseStore records/readback are fixture evidence only, never proof of actual publication.
- Real Handler and product view projection used with bounded release dashboard/authority/image-plan fixture substitutions; no provider or business database access.
- No installation, paid generation, actual credential read, platform write, production restart, push, or deployment.
