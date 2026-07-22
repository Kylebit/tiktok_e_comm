# LinkFox 两个集成 Skill 用途调查

> 调查对象：用户在本机 WorkBuddy 找到并已集成的两个 LinkFox skill —— **`linkfox-amazon-product-selection`（亚马逊选品）** 与 **`linkfox-1688-sourcing`（1688 货源）**。
> 调查目的：这两个 skill 到底能干什么、对 Orbit Hive（Orbit OS / Treasury 上品 / Ozon / Eyes 选品情报）有什么用处、调用前置与成本、安全红线如何对齐。
> 调查时间：2026-07-22 · CEO 肉肉

## 1. 结论速览

| 维度 | 亚马逊选品 skill | 1688 货源 skill |
|---|---|---|
| 子能力数量 | 12 类底层工具 / **33 项子能力** | 3 类底层工具 / **4 项子能力** |
| 安装完整度 | 完整（34 脚本 + 33 references） | 完整（40 脚本 + 4 references） |
| 当前可调用 | ❌ 缺 `LINKFOX_AGENT_API_KEY` | ❌ 缺 `LINKFOX_AGENT_API_KEY` |
| 计费方式 | 按工具独立计费 / 限频（含高价 AI 报告） | 搜索≈9 积分、图搜≈4.5 积分、采购按操作计费 |
| 对 Orbit Hive 直接用处 | **低（当前无 Amazon 业务块）** | **高（直接喂 Treasury 上品 + 选品情报）** |
| 安全红线关注点 | 只读为主，无写操作 | 含**高风险下单/支付/收货写操作**，须走确认闸 |
| 建议定位 | 作为 Eyes 选品模块的**参考架构**存档；Amazon 扩张时启用 | 作为 Treasury 上品**货源情报增强**接入（只读部分）；采购走 Boss 确认闸 |

**一句话**：1688 货源 skill 是当下就能用上的"找货/比价/以图搜货源"利器，与 Treasury 上品直接咬合；亚马逊选品 skill 是当前用不上的"成熟选品能力样板"，留作 Eyes 重建参考与未来扩张。两者都要先解决 API key + 积分。

## 2. 安装状态核实（本机实测）

通过扫描 `~/.workbuddy/skills/` 目录核实，两个 skill 均**真实落地、非空壳**：

- `linkfox-amazon-product-selection`：存在，34 个可执行脚本、33 个 references 文档、SKILL.md 23KB。
- `linkfox-1688-sourcing`：存在，40 个可执行脚本（含 12 个采购操作脚本 + 共享模块 + 图片上传）、4 个 references 文档、SKILL.md 20KB。
- 缺失项：`skills-version.json`、`onboarding.md`（仅影响本地计费清单与自助注册引导，不影响已装能力的调用逻辑）。

**调用前置（两者共通，当前未满足）**：

1. 环境变量 `LINKFOX_AGENT_API_KEY`（或 `LINKFOXAGENT_API_KEY`）—— 实测**未设置**。
2. 网关地址 `LINKFOX_TOOL_GATEWAY` —— 实测**未设置**（正常由 onboarding/登录后写入）。
3. 1688 采购类操作还需当前用户完成 **1688 OAuth 授权**（ACTIVE 且未过期）。
4. 注册引导依赖 `linkfox-onboarding` skill（本机未装，需从 `agent-files.linkfox.com` 下载安装；渠道须传 `workbuddy`）。

> 结论：工具已就绪，**钥匙没配**。要真调通，Boss 需先开 LinkFox 账号拿 API key 并配置环境变量。

## 3. 亚马逊选品 skill 用途

### 3.1 能力分层（12 类工具 / 33 子能力）

| 层 | 代表子能力 | 能拿到什么 |
|---|---|---|
| 前台实时 | `amazon_search`（搜索/SERP）、`amazon_product_detail`（详情/五点/A+/变体）、`amazon_reviews`（按星级评论）、`amazon_alexa_assistant`（自然语言导购）、`amazon_search_by_image`（以图搜图） | 实时排名、价格、评分、变体、评论原文、相似商品 |
| 历史时序 | `keepa`（价格/BSR/月销/卖家数历史）、`sorftime`（FBA 利润/Deal 历史/快照） | 12 个月销量趋势、利润、BSR 曲线 |
| 关键词流量 | `aba`（官方搜索词）、`jungle_scout`（反查/拓展/历史/声量份额）、`sif`（ASIN 流量结构/竞争概览）、`sellersprite`（流量词反查） | 搜索量、供需比、PPC 竞价、流量来源拆解 |
| 利基市场 | `jiimore`（细分市场指标/评论/潜力爆品）、`sellersprite`（选市场看板） | 垄断度、品牌数、新品成功率、蓝海赛道 |
| 商业洞察 | `amazon_business_insight`（六维 AI 报告 / 反向按指标筛赛道） | 市场潜力/产品特征/评论/客户画像/搜索趋势/定价，AI 生成 |

覆盖美国、英国、德国、日本等 15+ 站点。

### 3.2 对 Orbit Hive 的用处评估

- **直接用处：低。** 当前 Orbit Hive 业务块是 TikTok / Ozon(8767) / Shopee / Treasury 上品，**没有 Amazon 块**，所以这套亚马逊选品能力暂无对应业务可落。
- **间接用处：中高（参考架构价值）。** Eyes(8768 选品情报) 目前处于 **⚠️ 降级**状态。这个亚马逊 skill 是一份"成熟选品能力"的活样本 —— 12 数据源、33 子能力、严格的能力边界与计费约束、统一的网关+脚本+references 结构。重建/增强 Eyes 时，可直接借鉴它的**分层建模思路**（前台实时 → 历史时序 → 关键词流量 → 利基 → 商业洞察）和**输出规范**（来源标注、币种提示、不可用值标 N/A）。
- **未来用处：高。** 若 Boss 决定把业务扩到 Amazon，该 skill 即插即用，无需自研。

> 建议：当下**不消耗积分**去跑它；把它的 SKILL.md + references 当作 Eyes 重建的设计范本存档。

## 4. 1688 货源 skill 用途

### 4.1 能力分层（3 类工具 / 4 子能力）

| 子能力 | 端点 | 能拿到什么 | 对上品的用处 |
|---|---|---|---|
| `dld_product_search`（商品搜索） | POST /dld/productSearch | 按关键词/商品链接/ID 搜 1688 批发货，返回**批发价、代发价、销售笔数/件数、预估销售额、起批量、供应商资质、店铺/商品链接**；支持工厂/代发/跨境/新品筛选与排序 | ★★★ 直接喂上品"找货"：先发现货源再上架 |
| `dld_product_billboard`（热销榜单） | POST /dld/productBillboard | 周榜/月榜（按销售笔数/额/量排序），发现爆款与趋势货源 | ★★★ 选品风向标：什么在 1688 端走热 |
| `alibaba1688_image_search`（以图搜图） | POST /alibaba1688/imageSearch | 用图片 URL/Base64 视觉搜同款/相似货源，返回标题/批发价/代发价/月销/起批量/复购率/商家身份（超级工厂/实力商家） | ★★★ 我们已有"商品图"能力，可反向：用成品图回搜 1688 同款货源 |
| `alibaba1688_procurement`（采购履约） | POST /alibaba1688/{operation} | 授权检查、SKU/规格、下单预览、创建订单、支付链接、订单/物流状态、取消、确认收货（12 个操作） | ★ 仅经 Boss 确认闸使用（见 §6 安全红线） |

### 4.2 对 Orbit Hive 的用处评估

- **直接用处：高，且与现有架构咬合紧密。** MEMORY 已固化"上品采集路径"：前端**上品**页 → 粘 1688 链接/offer_id/妙手采集箱 ID → 妙手采集。这个 skill 的**只读部分**（搜索/榜单/图搜）恰好补上"上品"流程最前端的**货源发现与比价情报**——当前是人工去 1688 找，未来可由 skill 自动拉货源候选、比批发价/代发价/销量、再以图搜确认同款，再进入上架。
- **与自建迷你 LinkFox 的关系**：昨天调研结论是"图像部分自建（ComfyUI+SDXL），非图像部分白嫖 LinkFox 开放 skill"。**1688 货源 skill 正是那个该"白嫖"的非图像能力**——找货/比价/以图搜货源，无需自己爬 1688。
- **数据口径注意**：价格一律人民币（¥/CNY），须区分批发价 vs 代发价；销量按 `cycle=7/30` 或周/月榜标明时间窗。

## 5. 调用前置与成本

| 项 | 亚马逊选品 | 1688 货源 |
|---|---|---|
| 鉴权 | `LINKFOX_AGENT_API_KEY`（必需） | 同左（必需） |
| 计费 | 各工具独立计费/限频；AI 报告、反向筛赛道较贵 | 搜索≈9 积分(≈18000 token)、图搜≈4.5 积分(≈9000 token)、采购按操作 |
| 缓存 | 同参数 24h 本地缓存，避免重复计费 | 搜索/榜单/图搜同参数 24h 本地缓存 |
| 失败约束 | 空结果/失败不得自动换词翻页试探 | 同左；不得自动连续试探 |
| 输出落盘 | `<cwd>/linkfox/<日期>/<session>/data/*.json` | 同左；采购类默认脱敏不落盘，可 `--save` |

> 关键点：**每次调用都烧积分**。在 Orbit Hive 里集成时，应在编排层加"调用前预估成本 + 超阈值请示 Boss"，避免 agent 自主连发把积分烧光（这与我们"需 Boss 拍板走飞书"的纪律一致）。

## 6. 安全红线对齐（重要）

`linkfox-1688-sourcing` 的**采购履约**部分含**高风险写操作**，设计上就要求"用户单独中文确认 + Agent 注入 boolean 安全字段"：

| 操作 | 风险 | 确认字段 |
|---|---|---|
| `createOrder`（创建订单） | 高 | `confirmCreateOrder=true` |
| `paymentUrl`（获取支付链接） | 高 | `confirmGetPaymentUrl=true` |
| `cancelOrder`（取消订单） | 高 | `confirmCancel=true` |
| `confirmReceive`（确认收货） | 高 | `confirmReceive=true` |

这与 Orbit Hive **委派铁律的安全红线完全对齐**：真实店铺下单/支付 = 必须 Boss 一次性确认。

> **集成铁律**：1688 采购履约**绝不**走 agent 自主执行；必须路由到与"真实店铺发布/改价/下架"同级别的 Boss 确认闸（飞书请示 + 按钮确认）。只读的搜索/榜单/图搜可放开给编排层自动调用（仍受积分阈值约束）。

## 7. 集成建议（按优先级）

1. **P0 — 先配钥匙**：Boss 开 LinkFox 账号 → 拿到 `LINKFOX_AGENT_API_KEY` 并写入环境；可选装 `linkfox-onboarding` 做自助注册（渠道传 `workbuddy`）。
2. **P1 — 只读 1688 货源接入 Treasury 上品**：把 `dld_product_search` / `dld_product_billboard` / `alibaba1688_image_search` 封装成一个"货源情报"子 agent，向上品流程供给货源候选 + 比价。低风险、直接产生价值。
3. **P1 — 积分护栏**：编排层加"单次/单任务积分预算 + 超阈飞书请示"，防止 agent 烧光额度。
4. **P2 — 亚马逊 skill 存档为 Eyes 范本**：不调用，仅把其分层建模与输出规范沉淀进 Eyes 重建设计。
5. **P3 — 1688 采购履约（高风险）**：仅在 Boss 明确要求"自动下单"时启用，且强制走 Boss 确认闸；默认不接。

## 8. 待 Boss 拍板

1. 是否现在就去开 LinkFox 账号拿 API key，把 1688 货源只读能力接进 Treasury 上品？
2. 1688 采购履约（自动下单）是否授权接入？若授权，确认走哪一级 Boss 确认闸。
3. 积分预算设多少（单次任务 / 每日上限），超出如何请示？
4. 亚马逊选品 skill 是否也要在注册时一并开通（为未来 Amazon 扩张铺路）？

---
*注：本报告基于两个 skill 的 SKILL.md / references 结构实测与内容分析；实际调用需 API key + 积分，未经真实请求验证。*
