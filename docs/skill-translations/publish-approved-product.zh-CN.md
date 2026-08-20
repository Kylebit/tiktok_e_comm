<!-- source_sha256: f5845610f65792aa4bc1abc4723cc23eb6f9672a32aaaf2136c0c9cc80c22d5e -->

### 元数据

- `name`: `publish-approved-product`
- `description`: 对已经批准的商品发布中心 Offer 执行第 05–07 阶段，使用三个独立工作流检查已批准的逐 SKU 快照、通过妙手发布 TikTok、创建 Shopee 全球商品、通过官方 API 发布 Ozon、执行平台专属回读、如实分类结果，并只保留已经确认的事故经验。当用户要求发布、重试、检查或诊断已完成 01–04 阶段的 Offer ID 时使用。

### 发布已批准商品

只使用已批准的商品发布中心快照作为公共输入。TikTok、Shopee 和 Ozon 是独立任务。一个平台的历史状态、失败、警告或回读绝不能阻止另一个平台。

Kyle 在会话中的明确批准是唯一人工批准权威。页面按钮绝不是批准权威。发布前，Agent 必须通过确定性商品发布中心边界持久化精确批准 revision 与 plan；缺失或过期技术事实可以阻止执行，但不得要求 Kyle 在另一个页面重复同一批准。

### 必需架构

1. 必须取得精确已批准的 `offer_id` 和 `plan_id`；不能从可变仪表盘推导任一值。
2. 只允许使用 `skills/publish-approved-product/scripts/product_center_publication.py` 作为生产命令。
3. 由商品发布中心解析冻结 v4 快照，并通过服务端拥有的异步 Runner 和不可变报告运行每个已授权平台。
4. 任一平台失败后继续下一个平台。
5. 对外只展示经过净化的四态摘要。商品发布中心在持久化报告中保留已脱敏详细证据和平台回读。

### 冻结 v4 执行边界

新发布 run 只接受 `approved-publication-snapshot/v4` 作为生产输入，并且只能送入 v4 平台 executor。绝不能把它送入旧 ReleasePlan parser 或旧采集箱 start route；旧 reader 预期的字段不同，可能先认领平台对象，然后在任何目标草稿准备前失败。

对平台 create/claim 调用，客户端幂等键只能算本地证据，除非平台明确保证幂等。任何可能失败的类目准备、目标创建或其他步骤之前，先持久化平台返回的 detail ID。响应缺失或不明确时，重试前必须核对平台官方列表并绑定精确现有身份。绝不能仅因为复用客户端 key 就重试认领。

平台范围必须保持结构化：TikTok-only run 只能创建 TikTok 行；Shopee-only run 只能创建 Shopee 行。不得为未选择平台创建 pending 行或完成依赖。

### 把回读失败转化为永久预防

把回读当作测量边界，而不是反复人工修复循环。当任一精确批准事实与平台不同时：

1. 保留批准快照和净化后的平台观察事实。
2. 在允许漂移发生的最低确定性边界添加失败回归：快照投影、payload 构建、复用/收敛或结果分类。
3. 修复该边界，使以后 dispatch 不能发出或接受相同漂移。
4. 保留可执行回读，作为永久修复对平台生效的最终断言。
5. 只有红测、修复、相关回归和平台回读全部一致后，才把根因记录进平台 reference。

绝不能只为当前 Offer ID 添加平台专属修复。已确认事故必须成为以后所有已批准 Offer 的不变量。

### 检查已批准快照

使用商品发布中心已批准 plan 和其精确身份。生产命令不能调用仪表盘 endpoint，也不能重建快照。商品发布中心在排队 run 前，把 `offer_id + plan_id` 绑定到不可变 v4 快照。`inspect_snapshot.py` 只能用于诊断旧兼容数据，其输出绝不能作为生产发布输入。

快照必须包含每个已选 SKU 的 Seller SKU、选项名、成本、重量、包裹尺寸和价格上下文，以及图片、描述和类目。只有当请求平台确实要求的事实缺失或矛盾时才停止。若已批准的平台候选可以提供 category ID，则快照没有 category ID 只是警告。

每个已选 Shopee 区域目标必须同时保留 CNSC `global_original_price_cny` 和含币种的区域 `local_original_price`。丢失任一价格身份都是 dispatch 前契约失败。在 v4 冻结快照中，每个 Model SKU 和已选区域的价格行必须为 `{amount: <local>, currency: <local ISO code>, global_original_price_cny: <CNY>}`。附加的 CNY 字段是 Shopee 专属，不能加入 TikTok 或 Ozon 价格行。

### 生产 Runner 与弃用兼容工具

`product_center_publication.py` 是生产控制 wrapper。它只把 `{offer_id, plan_id}` 发往一个或多个明确 Runner 启动路由：

- `/api/product-workspace/publish-tiktok`
- `/api/product-workspace/publish-shopee-global`
- `/api/product-workspace/publish-ozon`

它要求 HTTP 202 和 `product-publication-start/v1`，验证精确平台/run/report 身份，然后轮询 `/api/product-workspace/publication-report`，直到 `PUBLISHED`、`PROCESSING`、`PARTIAL` 或 `FAILED`。一个平台失败不能阻止其他平台启动。POST 响应丢失后绝不能盲目再次 POST。

下列脚本只属于已弃用兼容和诊断工具：

- `inspect_snapshot.py`
- `dispatch_tiktok.py` / `readback_tiktok.py`
- `dispatch_shopee.py` / `readback_shopee.py`
- `dispatch_shopee_regions.py` / `readback_shopee_regions.py`
- `dispatch_ozon.py` / `readback_ozon.py`

新生产 run 不得使用这些弃用的直接脚本。它们可以用于复现历史事故，但读取旧的可变数据形状，也不拥有冻结 v4 异步生命周期。

服务端冻结 v4 executor 负责平台请求构建、transport、凭据脱敏、轮询和回读。薄 Skill 客户端只负责精确启动身份、独立平台顺序、公共报告轮询和净化后的四态投影。不得把平台 payload 组装移动到 Agent 文本或该客户端中。

创建 run 时，商品发布中心冻结 canonical 仓库 Skill manifest digest、精确 Git commit 和所选平台生产执行文件的内容 digest。因此即使 commit 不变，脏执行代码仍会改变身份。worker 在进入 RUNNING 或平台 dispatch 前验证该身份；漂移必须产生持久化零写入失败，且绝不能触发隐式 Skill 安装。

不可变内部报告只允许保留固定净化目标证据字段：目标标签、状态、阶段、安全 provider code、脱敏原因、是否尝试请求、结果是否未知、已确认写入次数。公共报告保持四态计数，并剥离目标证据和执行身份。禁止原始响应、headers、URLs、tokens、异常参数和外部 item identities。

HomeBloom SEA 店铺是由妙手 Open API 路径拥有的 TikTok 目标，不是 Shopee 区域目标，也不是 TikTok 直接 API 目标。冻结快照选择 `tiktok:HB_PH`、`tiktok:HB_MY`、`tiktok:HB_TH` 或 `tiktok:HB_VN` 时，每个都必须作为绑定精确 HomeBloom 店铺身份的独立执行目标。executor 不得对这些店使用 TikTok 官方 API，不得替换为同区域 LivelyHive 店铺，也不得把四个目标合并成一个共享结果。

Shopee 必须先完成并验证全球商品。只有已批准快照明确选择 `shopee:PH`、`shopee:MY`、`shopee:TH` 或 `shopee:VN` 时，服务端 Shopee executor 才能在 Global 验证后处理区域 dispatch 和回读。每个已选区域必须独立处理。Global-only run 的区域目标数必须为零。只有精确官方 shop-item、model、price 和 global-linkage 回读才能记录区域发布成功。

已验证 Global 母版保留批准的英文文案。区域创建请求必须省略 `item_name` 和 `description`，让 Shopee 派生目标文案。官方回读允许 PH/MY 使用英文；TH 必须为泰语；VN 必须为越南语。只有现有 TH/VN 商品语言错误时才能修复后再次读取，绝不能为修复文案创建重复商品。

### 生产命令

用户授权精确 Offer、plan 和平台后执行：

```powershell
.venv\Scripts\python.exe skills\publish-approved-product\scripts\product_center_publication.py --offer-id <OFFER_ID> --plan-id <EXACT_PLAN_ID> --platform all --execute
```

隔离重试时使用 `--platform tiktok`、`shopee` 或 `ozon`。对一个平台的授权不代表授权另一个平台。

商品发布中心而不是 Skill 客户端分配 run identity，并把不可变报告写入 `reports/product-publication/<offer>/<revision>/<run>`。客户端验证 `product-publication-start/v1`，轮询精确返回的 `publication-report:<run_id>`，并且不输出完整快照、确认 token、原始响应、凭据、URL、外部 item ID 或可变仪表盘事实。

`publish_approved_product.py` 和直接 `dispatch_*.py` / `readback_*.py` 脚本只属于弃用兼容。新已批准 Offer 的生产路径绝不能调用它们。

### 平台知识

每个平台只读取相关 reference：

- `references/tiktok.md`
- `references/shopee.md`
- `references/ozon.md`

添加永久经验前读取 `references/incident-patterns.md`。绝不能把未经确认的假设记录成政策。

### Canonical Skill 一致性

仓库目录 `skills/publish-approved-product` 是唯一 canonical Skill 来源。使用前检查 installed copy：

```powershell
.venv\Scripts\python.exe scripts\sync_product_publication_skills.py --check
```

审核明确授权安装后，显式执行安装并再次检查：

```powershell
.venv\Scripts\python.exe scripts\sync_product_publication_skills.py --install
.venv\Scripts\python.exe scripts\sync_product_publication_skills.py --check
```

发布、测试或 Skill 验证期间绝不能隐式安装。一致性不匹配是部署/配置失败；不能静默混用 canonical 指令和另一个 digest 的 installed 脚本。

### TikTok 类目决定

把已批准产品类型作为语义权威，把妙手预填草稿类目当作不可信候选。TikTok dispatch 前：

1. 从快照读取已批准标题、描述、产品类型、用途、材质和商品图片。
2. 针对每个已选站点和精确店铺，独立查询官方类目树。
3. 候选叶子排序先看产品类型和用途，再看材质；装饰主题或季节只能作为次要证据。
4. 查询最佳候选的官方元数据。类目存在于元数据中，只能证明 ID 被识别，不能证明语义适合，也不能证明类目已启用发布。
5. 优先一个语义精确且启用的候选。如果 TikTok 禁用精确叶子，只能查阅 `references/tiktok.md` 中用户明确批准的 fallback 表。只有当官方树对精确站点显示该 fallback 已启用，并且精确店铺元数据有效时，才能接受 fallback。对已批准的桌垫/餐垫/杯垫和桌布/桌旗产品，Kyle 授权直接使用 `cid=600009` Festive Decoration。不得先选择 `cid=600033` 或 `cid=600204`；实时站点树仍必须显示 `600009` 已启用，精确店铺元数据也必须验证。不得创造任何其他宽泛 fallback。
6. 精确候选和已批准 fallback 都不可用时，返回 `CATEGORY_CONFIRMATION_REQUIRED`，列出最佳候选，并要求一个主类目决定。
7. 使用确定性代码写入已确认站点类目和必填属性，然后在 dispatch 前回读精确草稿。

不能仅因为元数据识别妙手预填类目就保留它。fallback 有效的原因只能是：用户已明确批准该产品家族 fallback，且官方站点树当前允许。

### 用户可见结果

只能显示：**发布成功**、**平台处理中**、**部分成功**或**发布失败**。

dispatch/readback 证据保留在报告中，不得包含凭据或平台原始响应。
