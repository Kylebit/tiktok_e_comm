<!-- source_sha256: b57fed3712b24fd324b16f05711c49a4f46034f6afe2e84ef3e32ad5b9720542 -->

> 历史译本（2026-09-23 标注）：此 source_sha256 与当前英文 Skill 不一致；请读当前英文 `SKILL.md`，它是唯一执行权威。

### 元数据

- `name`：`prepare-product-publication`
- `description`：以一个商品发布中心 Offer ID 和精确目标店铺为输入，用零外部写入准备第一轮人工审核；采集权威 SKU 与包裹事实，解析类目和价格候选，生成标题与发布规格候选，并提出明确的图片翻译/生成决定。适用于开始、准备、检查、恢复或重做付费图片生成、妙手同步和正式发布之前的第一轮。

### 准备商品发布

#### 已有商品接管

检查或恢复已有商品时，先在明确选定的项目中，按仓库根 `docs/PUBLICATION_SOURCE_CONTRACT.md` 使用绑定提交的只读 `scripts/publication_takeover.py`。不能仅为检查已有商品而重新初始化或批准。该合同规定来源选择和旧阶段标签的解释；它不证明个人安装副本中较新的工作流模式已经集成。

把一个精确 Offer ID 和精确目标店铺清单转成持久化第一轮审核包。商品事实必须复用商品发布中心的确定性代码；Agent 判断只用于有文档依据的类目研究、文案候选和建议。缺失的商业或平台事实绝不能猜。

### 不可违反的轮次边界

第一轮外部写入始终为零：不写妙手、不调用付费图片服务、不认领或创建店铺草稿、不发布。第二轮 `prepare-product-images` 只生成图片并做 QA，不写妙手。第三轮 `publish-approved-product` 负责 COMMON 同步和正式回读；恢复旧审核包时参见 canonical Skill 的 `references/miaoshou-baseline-sync.md`。

Kyle 在会话中的明确批准是唯一人工批准入口。批准独立于页面按钮和技术状态记录；商品页面只是编辑和观察界面。

### 必需输入

必须取得一个精确 `offer_id`、全部精确目标店铺，以及可选的图片翻译位置、拟生成图片概念和 LivelyHive/HomeBloom 内容组选择。必须保持原始店铺清单；不能从 LivelyHive 推断 HomeBloom，不能从 TikTok 推断 Shopee/Ozon，也不能从 Offer ID 推断全部店铺。

### 工作流

#### 1. 构建确定性预览

```powershell
.venv\Scripts\python.exe skills\prepare-product-publication\scripts\prepare_product_publication.py --offer-id <OFFER_ID> --targets <COMMA_SEPARATED_TARGETS>
```

需要图片工作时，建立一个 `first-review-image-plan/v1` JSON 并增加 `--image-plan <PATH>`。计划必须列出每个来源位置的 `KEEP`、`TRANSLATE`、`REMOVE` 或 `REFERENCE`、精确目标语言和拟新增资产。本轮不得用 OCR 选择图片，也不得调用付费 API。

客户端接受 `--candidate-plan <PATH>`，用于提出类目和文案候选附属文件。省略任一计划参数时，如果 `reports/product-preparation/<OFFER_ID>/` 中存在对应的 `first-review-image-plan.json` 或 `first-review-candidate-plan.json`，则复用它；刷新报告保留这些输入文件。Offer ID 只能包含 ASCII 数字。候选类目不能替代绑定的官方类目证据，候选文案仍是 `PROPOSED`。若所选 SEA 店铺同时包含 LivelyHive 和 HomeBloom，则保留独立品牌内容与图片计划；单品牌目标不隐含另一个品牌。这些兼容规则不会安装或启用个人副本的 manual-intake 或 autopilot 工作流。

R1 保留输入商品发布中心预览中的逐 SKU 包裹事实和价格计算。资料完整性要求成本、重量、尺寸和价格为正且有限的数值。逐 SKU 包裹事实只提供一部分时，不得静默用共享默认值替代。变体价格行必须覆盖已知 SKU 身份；COMMON 草稿没有市场价格要求。这些计算仍是候选，不能证明平台回读。

本地工作台不存在时，客户端可以执行现有上游读取和一次本地状态写入；这不是平台写入。请求目标缺失时返回 `DECISION_REQUIRED`，不能恢复默认选择。

旧参数 `--execute-miaoshou` 或 `--confirm-miaoshou-write` 必须拒绝写入；旧报错文字不是阶段路由依据。`--skip-miaoshou` 仅为兼容空操作，妙手同步属于第三轮 COMMON。

#### 2. 解析第一轮事实

每个目标都要保留证据和来源：供应商 SKU 与拟定 Seller/Model SKU、精确可发布类目及必填属性、价格与币种、平台标题和发布规格、成本/重量/包裹尺寸、用户选择的翻译位置及语言路由，以及公共内容或用户要求的双内容组。

类目或内容工作前读取 `references/knowledge-base-schema.md`。优先使用已确认产品家族事实，再使用官方只读树和元数据。零候选或多个安全候选必须交给用户审核。

不能自动选择翻译图片或双内容组。通常应在第一轮批准前提出图片计划；若 Kyle 已提前批准冻结范围，应先记录批准意图，稍后补齐计划，不再重复要求批准。

#### 3. 持久化审核包

遵循 `references/decision-contract.md`，写入 `reports/product-preparation/<offer_id>/first-review.json`，且该运行时文件不得提交 Git。包中保存精确 revision、目标、决定、图片计划、阻断和以下边界：

```json
{
  "status": "FIRST_REVIEW_READY",
  "miaoshou_sync": {"status": "DEFERRED_TO_SECOND_ROUND"},
  "external_write_count": 0,
  "request_attempted": false,
  "readback_verified": false
}
```

事实缺失或矛盾时返回 `DECISION_REQUIRED` 和最小可操作决定。不得存储平台原始 payload、凭据、URL 或平台商品身份。

#### 4. 交接

`DEFERRED_TO_SECOND_ROUND` 是现有客户端兼容标签，不授权 R2 同步妙手。保留已有包中的值，按 Product Center 当前阶段合同恢复。

返回 Offer ID、revision、请求与观察到的店铺、公共事实、逐目标决定、来源图动作/语言路由/拟生成图片、未解决决定，并明确“第一轮妙手写入 0；R2 处理图片，R3 COMMON 负责同步及回读”。下一句提示为：`第一轮通过，开始第二轮`。

没有对应用户指令时，不得开始付费生成、妙手同步或发布。新 Agent 必须从持久化审核包和当前商品发布中心状态恢复，而不是依赖聊天上下文。

### 安全与证据

- 第一轮平台写入严格为零。
- 不暴露凭据、原始响应、平台 URL 或异常参数。
- 已确认写入数与尝试请求必须分开。
- 过期技术状态不得删除已记录的会话批准。
- 只有官方事实、回归测试和回读一致时才更新知识；一次性假设不能升级为产品家族规则。
