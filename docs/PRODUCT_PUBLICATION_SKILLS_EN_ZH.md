# 商品发布 Skills 中英对照版

> **非执行权威。** 本文件仅供 Kyle 阅读。真实执行只使用仓库中的三份英文 `SKILL.md`；英文 `SKILL.md` 是唯一执行权威。本文件由构建脚本机械嵌入英文原文并附上人工维护的完整中文翻译，不包含 Skill frontmatter，也不会被 Skill 系统加载。

同步契约：`product-publication-skills-bilingual/v1`。任一英文源文件变化后，如果对应中文翻译未更新源 SHA-256，构建与测试都会失败。

| Skill | 英文执行权威 | 中文翻译源 | SHA-256 |
|---|---|---|---|
| `prepare-product-publication` | `skills/prepare-product-publication/SKILL.md` | `skill-translations/prepare-product-publication.zh-CN.md` | `5a95ab0d4aa70088882f0b590b9f9941a2e6b4b7614dc92fbaae150a6f6294ff` |
| `prepare-product-images` | `skills/prepare-product-images/SKILL.md` | `skill-translations/prepare-product-images.zh-CN.md` | `6266a2b4ce72886935f4a715b2c01f59996336ff12db0d01cfd5ff16202f7e22` |
| `publish-approved-product` | `skills/publish-approved-product/SKILL.md` | `skill-translations/publish-approved-product.zh-CN.md` | `f5845610f65792aa4bc1abc4723cc23eb6f9672a32aaaf2136c0c9cc80c22d5e` |

---

# 1. `prepare-product-publication`

源 SHA-256：`5a95ab0d4aa70088882f0b590b9f9941a2e6b4b7614dc92fbaae150a6f6294ff`

## English source (verbatim)

````markdown
---
name: prepare-product-publication
description: "Prepare the first human review for one Product Center Offer ID and exact target stores with zero external writes: collect authoritative SKU and parcel facts, resolve category and pricing candidates, generate title and variant-display candidates, and propose explicit image translation/generation decisions. Use when the user asks to start, prepare, inspect, resume, or redo the first round before paid image generation, Miaoshou synchronization, and publication."
---

# Prepare Product Publication

Turn one exact Offer ID and exact target stores into a durable first-review
packet. Reuse Product Center deterministic code for facts. Use agent judgment
only for documented category research, copy candidates, and recommendations.
Never guess a missing commercial or provider fact.

## Non-negotiable round boundary

The first round always has **zero external writes**. It never writes Miaoshou,
calls a paid image service, claims or creates a shop draft, or publishes.
Miaoshou synchronization belongs only to the second round in
`prepare-product-images`.

Kyle's explicit approval in the conversation is the only human approval entry.
Record it independently of page buttons and technical readiness. Product pages
are editing and observation surfaces, not approval authorities.

## Required input

Require:

- one exact `offer_id`;
- every intended target store, not only a platform or country;
- optional explicit user choices for translation positions, generated image
  concepts, and LivelyHive/HomeBloom content groups.

Preserve the exact store list. Never infer HomeBloom from LivelyHive, Shopee or
Ozon from TikTok, or all stores from an Offer ID.

## Workflow

### 1. Build the deterministic preview

Run:

```powershell
.venv\Scripts\python.exe skills\prepare-product-publication\scripts\prepare_product_publication.py --offer-id <OFFER_ID> --targets <COMMA_SEPARATED_TARGETS>
```

When image work is in scope, create one explicit
`first-review-image-plan/v1` JSON file and add `--image-plan <PATH>`. The plan
lists each chosen source position as KEEP, TRANSLATE, REMOVE, or REFERENCE,
exact target languages, and proposed net-new assets. Do not use OCR to select
images. Do not call a paid API in this round.

If no local workbench exists, the client may perform the existing upstream
read and a local workbench-state write once. These are not provider mutations.
If requested targets are missing, return `DECISION_REQUIRED`; never silently
restore defaults.

Legacy `--execute-miaoshou` or `--confirm-miaoshou-write` arguments must fail
with a clear second-round boundary error. `--skip-miaoshou` is a compatibility
no-op because Miaoshou is always deferred.

### 2. Resolve first-review facts

For each selected target retain evidence and provenance for:

1. supplier SKU and proposed seller/Model SKU;
2. exact publishable category and required attributes;
3. reviewed price and currency;
4. platform title and final publication specification name;
5. cost, weight, and package dimensions;
6. user-selected translation positions and locale routes;
7. common content or a user-requested LivelyHive/HomeBloom split.

Read `references/knowledge-base-schema.md` before category or content work.
Prefer confirmed product-family facts, then official read-only provider trees
and metadata. Zero or multiple safe category candidates require user review.

Do not automatically choose translation positions or dual content groups.
Propose the image plan before first approval unless Kyle explicitly approves
the frozen scope earlier; then persist approval intent and reconcile the plan
later without asking again.

### 3. Persist the first-review packet

Follow `references/decision-contract.md`. Store the packet under
`reports/product-preparation/<offer_id>/first-review.json`; this runtime state
must not be committed. It contains the exact revision, targets, decisions,
image plan, blockers, and:

```json
{
  "status": "FIRST_REVIEW_READY",
  "miaoshou_sync": {"status": "DEFERRED_TO_SECOND_ROUND"},
  "external_write_count": 0,
  "request_attempted": false,
  "readback_verified": false
}
```

Missing or contradictory facts yield `DECISION_REQUIRED` with the smallest
actionable decision. Never persist raw provider payloads, credentials, URLs,
or provider item identities.

### 4. Hand off

Return a compact summary with:

- Offer ID and exact Product Center revision;
- requested and observed stores;
- shared facts and per-target decisions;
- source image actions, locale routes, and proposed generated images;
- unresolved decisions;
- explicit statement: first-round Miaoshou writes `0`, deferred to second round;
- next phrase: `第一轮通过，开始第二轮`.

Do not start paid generation, Miaoshou synchronization, or publication without
the corresponding user instruction. A new agent resumes from the durable
packet and current Product Center state, not conversation memory.

## Safety and evidence

- Keep first-round provider write count exactly zero.
- Never expose credentials, raw responses, provider URLs, or exception args.
- Keep confirmed write counts separate from attempted requests.
- Do not let stale technical state erase a recorded conversation approval.
- Update knowledge only when official facts, regression evidence, and readback
  agree; never promote a one-off hypothesis into a product-family rule.
````

## 中文完整翻译

<!-- source_sha256: 5a95ab0d4aa70088882f0b590b9f9941a2e6b4b7614dc92fbaae150a6f6294ff -->

### 元数据

- `name`：`prepare-product-publication`
- `description`：以一个商品发布中心 Offer ID 和精确目标店铺为输入，用零外部写入准备第一轮人工审核；采集权威 SKU 与包裹事实，解析类目和价格候选，生成标题与发布规格候选，并提出明确的图片翻译/生成决定。适用于开始、准备、检查、恢复或重做付费图片生成、妙手同步和正式发布之前的第一轮。

### 准备商品发布

把一个精确 Offer ID 和精确目标店铺清单转成持久化第一轮审核包。商品事实必须复用商品发布中心的确定性代码；Agent 判断只用于有文档依据的类目研究、文案候选和建议。缺失的商业或平台事实绝不能猜。

### 不可违反的轮次边界

第一轮外部写入始终为零：不写妙手、不调用付费图片服务、不认领或创建店铺草稿、不发布。妙手同步只属于第二轮 `prepare-product-images`。

Kyle 在会话中的明确批准是唯一人工批准入口。批准独立于页面按钮和技术状态记录；商品页面只是编辑和观察界面。

### 必需输入

必须取得一个精确 `offer_id`、全部精确目标店铺，以及可选的图片翻译位置、拟生成图片概念和 LivelyHive/HomeBloom 内容组选择。必须保持原始店铺清单；不能从 LivelyHive 推断 HomeBloom，不能从 TikTok 推断 Shopee/Ozon，也不能从 Offer ID 推断全部店铺。

### 工作流

#### 1. 构建确定性预览

```powershell
.venv\Scripts\python.exe skills\prepare-product-publication\scripts\prepare_product_publication.py --offer-id <OFFER_ID> --targets <COMMA_SEPARATED_TARGETS>
```

需要图片工作时，建立一个 `first-review-image-plan/v1` JSON 并增加 `--image-plan <PATH>`。计划必须列出每个来源位置的 `KEEP`、`TRANSLATE`、`REMOVE` 或 `REFERENCE`、精确目标语言和拟新增资产。本轮不得用 OCR 选择图片，也不得调用付费 API。

本地工作台不存在时，客户端可以执行现有上游读取和一次本地状态写入；这不是平台写入。请求目标缺失时返回 `DECISION_REQUIRED`，不能恢复默认选择。

旧参数 `--execute-miaoshou` 或 `--confirm-miaoshou-write` 必须明确报错；`--skip-miaoshou` 仅为兼容空操作，因为妙手始终延后到第二轮。

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

返回 Offer ID、revision、请求与观察到的店铺、公共事实、逐目标决定、来源图动作/语言路由/拟生成图片、未解决决定，并明确“第一轮妙手写入 0，延后到第二轮”。下一句提示为：`第一轮通过，开始第二轮`。

没有对应用户指令时，不得开始付费生成、妙手同步或发布。新 Agent 必须从持久化审核包和当前商品发布中心状态恢复，而不是依赖聊天上下文。

### 安全与证据

- 第一轮平台写入严格为零。
- 不暴露凭据、原始响应、平台 URL 或异常参数。
- 已确认写入数与尝试请求必须分开。
- 过期技术状态不得删除已记录的会话批准。
- 只有官方事实、回归测试和回读一致时才更新知识；一次性假设不能升级为产品家族规则。

---

# 2. `prepare-product-images`

源 SHA-256：`6266a2b4ce72886935f4a715b2c01f59996336ff12db0d01cfd5ff16202f7e22`

## English source (verbatim)

````markdown
---
name: prepare-product-images
description: Lock a conversation-approved first-round Product Center scope, optionally generate only the user-selected localized product images through ToAPIs, synchronize and verify the common Miaoshou baseline exactly once, record conversation approval, and atomically freeze the approved ReleasePlan handoff without publishing. Use after the user finishes the first-round review and asks to start, resume, approve, or finish the second round.
---

# Prepare Product Images

Use the deterministic script in `scripts/prepare_product_images.py`. Do not reconstruct this workflow with ad hoc API calls.

## Workflow

1. Require the exact Offer ID and current Kyle-approved first-round Product Center revision.
2. Require `reports/product-preparation/<offer_id>/first-review.json` to be `FIRST_REVIEW_READY` and to match the current revision.
3. Treat the first-review image plan as authoritative. Do not use OCR or model judgment to add translation positions.
4. Exclude source images marked `REMOVE`. Remap retained source positions deterministically.
5. Run the script without paid flags first. Report the frozen input digest, selected positions, locale routes, and paid task count.
6. If no positions were selected for translation, continue with zero paid tasks. Do not invent image work. Otherwise start paid generation only after explicit user authorization by supplying both `--execute-paid` and `--confirm-paid-generation`.
7. Require one completed ToAPIs receipt per task and an exact output count. Do not retry blindly after an unknown outcome; inspect the durable generation checkpoints first.
8. Write the English master image set to the common Miaoshou collect box exactly once and require official readback before publication execution. Use both `--execute-miaoshou` and `--confirm-miaoshou-write`. This technical condition must not block or erase conversation approval.
9. Keep localized artifacts frozen by target route. Do not place several country image sets in the common collect box; the publication workflow projects them into their matching site drafts later. Persist the public HTTPS result URL returned by ToAPIs. For a legacy artifact without that fact, require an explicit uploaded-assets manifest; never fabricate or silently re-upload it.
10. Product Center `/new-product?offer_id=<offer_id>#localizedImageResults` is the only human review surface. The page `/localized-image-review?offer_id=<offer_id>` is a technical result view with one action: refresh. It must not contain generation, per-image PASS, retry, paid-confirmation, or final-approval buttons.
11. Treat Kyle's explicit approval in the conversation as the only human approval entry. Record it immediately even when generation, Miaoshou sync, or another technical check is incomplete.
12. The approval intent automatically accepts ready artifacts and reconciles later artifacts under the same frozen input. Technical blockers prevent execution only; they never invalidate the approval intent or require Kyle to approve again.
13. After one verified Miaoshou sync and conversation approval, run the final handoff. Reuse an exact active approved base plan or locally freeze the current exact plan. If localized tasks exist, atomically create and approve an image-routing-only successor. If no localized tasks exist, retain the exact base plan. Persist `workflow-handoff.json` only after the frozen v4 snapshot and route coverage read back exactly.

## Safety boundaries

- ReleasePlan changes are local only: freeze the exact base plan and, when needed, atomically create one image-routing-only approved successor. Never leave the predecessor superseded without a usable approved successor.
- Do not write Miaoshou during paid generation. Run the separate common-baseline sync only after generation receipts are complete and the conversation has authorized that write.
- Do not publish, claim, create drafts, or update listing images.
- Do not generate for an unapproved or stale revision.
- Do not generate a locale that is absent from the approved first-review plan and selected targets.
- Treat paid image calls as external writes and report their exact confirmed count.
- OCR is allowed only to extract text inside a user-selected image; it must never choose which images are translated.
- Do not publish from this Skill. A `READY_TO_PUBLISH` handoff is consumed only by `publish-approved-product`.

## Commands

Preflight:

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id>
```

Paid generation after explicit approval:

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --execute-paid --confirm-paid-generation
```

Synchronize and verify the common Miaoshou baseline before review:

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --execute-miaoshou --confirm-miaoshou-write
```

Persist Kyle's explicit conversation approval at any point in the frozen round:

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --approve-all --approved-by Kyle
```

Freeze the exact approved handoff after Miaoshou verification and conversation approval:

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --finalize-release-handoff
```

For legacy generated artifacts whose ToAPIs receipt predates persisted public
result URLs, add `--uploaded-assets <PATH>`. The JSON file must contain an
exact `uploaded_assets` map of approved artifact ID to its matching digest and
public HTTPS URL. Extra, missing, or drifted entries fail closed.

The command emits one JSON summary. Approval and execution readiness are separate fields: approval may be recorded while the result still reports `MIAOSHOU_SYNC_REQUIRED` or another technical blocker. A verified baseline sync reports `miaoshou_external_write_count: 1`, `platform_writes: 0`. Finalization reports `READY_TO_PUBLISH`, the exact plan and snapshot identities, and never asks for another page approval.
````

## 中文完整翻译

<!-- source_sha256: 6266a2b4ce72886935f4a715b2c01f59996336ff12db0d01cfd5ff16202f7e22 -->

### 元数据

- `name`：`prepare-product-images`
- `description`：冻结会话已批准的第一轮范围；可选地只对用户选中的图片执行 ToAPIs 本地化；精确同步并验证一次妙手公共基线；记录会话批准；在不发布的前提下原子冻结已批准 ReleasePlan 交接。用于第二轮的开始、恢复、批准或完成。

### 准备商品图片

必须使用 `scripts/prepare_product_images.py`，不得用临时 API 调用重建流程。

### 工作流

1. 要求精确 Offer ID 和 Kyle 已批准的第一轮商品发布中心 revision。
2. 要求 `reports/product-preparation/<offer_id>/first-review.json` 为 `FIRST_REVIEW_READY` 且 revision 一致。
3. 第一轮图片计划是权威；OCR 和模型不能额外选择翻译位置。
4. 排除 `REMOVE` 来源图，并确定性重排保留位置。
5. 先不带付费参数运行，报告冻结输入 digest、位置、语言路由和付费任务数。
6. 若没有翻译位置，允许零付费任务继续，不能虚构图片工作。否则必须同时使用 `--execute-paid` 和 `--confirm-paid-generation` 才能开始付费生成。
7. 每个任务必须有完整 ToAPIs 回执和精确输出数量；未知结果不能盲目重试，应先检查持久化 checkpoint。
8. 妙手公共采集箱只写入英语母版一次，并执行官方回读。必须同时使用 `--execute-miaoshou` 和 `--confirm-miaoshou-write`。该技术条件不能阻止或删除会话批准。
9. 本地化图片按目标路由冻结，不能把多国语言图片同时写入公共采集箱。必须保留 ToAPIs 返回的公开 HTTPS 地址；旧资产缺失该事实时，需要明确 uploaded-assets manifest，不能伪造或静默重复上传。
10. 商品发布中心的 `#localizedImageResults` 是唯一人工审核面；独立结果页只是技术查看页，只保留刷新动作。
11. Kyle 会话批准是唯一批准入口，即使技术检查未完成也应立即记录。
12. 批准意图自动接受同一冻结输入下已经就绪或稍后就绪的资产；技术阻断不要求重复批准。
13. 一次妙手验证和会话批准完成后，执行最终交接：复用精确已批准基础 plan，或本地冻结当前精确 plan。有本地化任务时，原子创建并批准只改变图片路由的 successor；无任务时保留基础 plan。只有 v4 快照和目标路由回读精确后才写 `workflow-handoff.json`。

### 安全边界

- ReleasePlan 只能发生本地变更：冻结基础 plan，并在需要时原子创建一个已批准的图片路由 successor。不能让 predecessor 已被 supersede 却没有可用 successor。
- 付费生成期间不写妙手；生成回执完整且会话授权后才执行独立公共基线同步。
- 不发布、不认领、不创建店铺草稿、不直接更新平台图片。
- 不为未批准或过期 revision 生成图片。
- 不生成第一轮计划和目标店铺之外的语言。
- 付费调用按外部写入计数并报告确认数量。
- OCR 只提取用户已选图片中的文字，绝不能决定选图。
- 本 Skill 不发布；`READY_TO_PUBLISH` 只由 `publish-approved-product` 消费。

### 命令

预检：

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id>
```

明确授权后的付费生成：

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --execute-paid --confirm-paid-generation
```

第二轮唯一妙手同步和验证：

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --execute-miaoshou --confirm-miaoshou-write
```

记录 Kyle 会话批准：

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --approve-all --approved-by Kyle
```

冻结发布交接：

```powershell
.venv\Scripts\python.exe skills\prepare-product-images\scripts\prepare_product_images.py --offer-id <offer_id> --finalize-release-handoff
```

旧资产没有 ToAPIs 公开结果地址时增加 `--uploaded-assets <PATH>`。文件必须给出精确 `uploaded_assets` 映射：已批准 artifact ID、匹配 digest 和公开 HTTPS URL。缺失、额外或漂移均失败关闭。

命令只输出一个 JSON 摘要。批准和技术就绪分离；批准后仍可显示 `MIAOSHOU_SYNC_REQUIRED`。最终成功显示 `READY_TO_PUBLISH`、精确 plan/snapshot 身份，不要求另一次页面批准。

---

# 3. `publish-approved-product`

源 SHA-256：`f5845610f65792aa4bc1abc4723cc23eb6f9672a32aaaf2136c0c9cc80c22d5e`

## English source (verbatim)

````markdown
---
name: publish-approved-product
description: "Execute stages 05-07 for an approved Product Center offer through three independent workflows: inspect the approved per-SKU snapshot, dispatch TikTok through Miaoshou, create a Shopee global product, publish Ozon through the official API, perform platform-specific readback, classify truthful results, and retain only confirmed incident lessons. Use when the user asks to publish, retry, inspect, or diagnose an already-approved Offer ID after stages 01-04."
---

# Publish Approved Product

Use the approved Product Center snapshot as the only shared input. TikTok,
Shopee and Ozon are independent tasks. Never let one platform's previous state,
failure, warning, or readback block another platform.

Kyle's explicit approval in the conversation is the only human approval
authority. Page buttons are never approval authorities. The agent must persist
the exact approved revision and plan through the deterministic Product Center
boundary before publication; a missing or stale technical fact may block
execution, but it must not ask Kyle to repeat the same approval on another page.

## Required architecture

1. Require the exact approved `offer_id` and `plan_id`; do not derive either
   from the mutable dashboard.
2. Use `skills/publish-approved-product/scripts/product_center_publication.py`
   as the only production command.
3. Let Product Center resolve the frozen v4 snapshot and run each authorized
   platform through its server-owned async Runner and immutable report.
4. Continue to the next platform after any platform failure.
5. Expose only the four-state sanitized summary. Product Center retains the
   detailed redacted evidence and platform readback in its durable report.

## Frozen v4 execution boundary

Treat `approved-publication-snapshot/v4` as the only production input for a
new publication run. Send it only through the v4 platform executors. Never
feed it into a legacy ReleasePlan parser or a legacy collect-box start route;
those readers expect different fields and may claim a provider object before
failing to prepare any target drafts.

For a provider create/claim call, a client idempotency key is only local
evidence unless the provider explicitly guarantees idempotency. Persist the
returned platform detail ID before category preparation, target creation, or
any other fallible step. Before retrying a call whose result is missing or
ambiguous, reconcile the official provider list and bind the exact existing
identity. Never retry a claim merely by reusing the client key.

Keep platform scope structural: a TikTok-only run may create only TikTok rows,
and a Shopee-only run may create only Shopee rows. Never create pending rows or
completion dependencies for unselected platforms.

## Turn readback failures into permanent prevention

Use readback as a measurement boundary, not as a recurring manual repair loop.
When an exact approved fact differs from the provider:

1. Preserve the approved snapshot and sanitized provider observation.
2. Add a failing regression at the lowest deterministic boundary that allowed
   the drift: snapshot projection, payload construction, reuse/convergence, or
   result classification.
3. Fix that boundary so future dispatches cannot emit or accept the same drift.
4. Keep executable readback as the final assertion that the permanent fix
   works against the provider.
5. Record the root cause in the platform reference only after the red test,
   fix, related regression and provider readback all agree.

Never add a provider-specific repair only to the current Offer ID. A confirmed
incident must become an invariant for every later approved offer.

## Inspect the approved snapshot

Use the Product Center-approved plan and its exact identity. The production
command must not call a dashboard endpoint or rebuild a snapshot. Product
Center binds `offer_id + plan_id` to the immutable v4 snapshot before a run is
queued. Use `inspect_snapshot.py` only to diagnose old compatibility data; its
output is never a production publication input.

The snapshot must include each selected SKU's seller SKU, option name, cost,
weight, package dimensions and price context, plus images, description and
category. Stop only for a missing or contradictory fact that the requested
provider truly requires. Category-ID absence is a warning when an approved
platform candidate can supply it.

For every selected Shopee regional target, preserve both the CNSC
`global_original_price_cny` and the regional `local_original_price` with its
currency. Losing either price identity is a pre-dispatch contract failure.
In the v4 frozen snapshot, the regional price row is
`{amount: <local>, currency: <local ISO code>, global_original_price_cny: <CNY>}`
for every Model SKU and selected region. The additional CNY field is Shopee
specific; do not add it to TikTok or Ozon price rows.

## Production Runner and deprecated compatibility tools

`skills/publish-approved-product/scripts/product_center_publication.py` is the
production control wrapper. It sends
only `{offer_id, plan_id}` to one or more of these explicit Runner start routes:

- `/api/product-workspace/publish-tiktok`
- `/api/product-workspace/publish-shopee-global`
- `/api/product-workspace/publish-ozon`

It requires HTTP 202 with `product-publication-start/v1`, verifies the exact
platform/run/report identity, then polls `/api/product-workspace/publication-report`
until `PUBLISHED`, `PROCESSING`, `PARTIAL`, or `FAILED`. A platform failure does
not stop the other platform starts. A lost POST response is never blindly
reposted.

The following scripts are deprecated compatibility and diagnostics only:

- `inspect_snapshot.py`
- `dispatch_tiktok.py` / `readback_tiktok.py`
- `dispatch_shopee.py` / `readback_shopee.py`
- `dispatch_shopee_regions.py` / `readback_shopee_regions.py`
- `dispatch_ozon.py` / `readback_ozon.py`

Do not use those deprecated direct scripts for a new production run. They may
support historical incident reproduction, but they read the old mutable data
shape and do not own the frozen-v4 async lifecycle.

Server-owned frozen-v4 executors own provider request construction, transport,
credential redaction, polling and readback. The thin Skill client owns only
the exact start identity, independent platform order, public-report polling and
sanitized four-state projection. Do not move provider payload assembly into
agent prose or into this client.

At run creation, Product Center freezes the canonical repository Skill
manifest digest, exact Git commit, and a content digest of the production
execution files for the selected platform. Dirty execution code therefore
changes identity even when the commit is unchanged. The worker verifies this
identity before RUNNING or provider dispatch; drift is a durable zero-write
failure and never triggers an implicit Skill install.

The immutable internal report may retain only the fixed sanitized target
evidence fields: target label, status, stage, safe provider code, redacted
reason, request-attempted flag, unknown-outcome flag, and confirmed write
count. Public reports remain four-state counts and strip target evidence and
execution identity. Raw responses, headers, URLs, tokens, exception arguments,
and external item identities are forbidden.

HomeBloom SEA stores are TikTok targets owned by the Miaoshou Open API path,
not Shopee regional targets and not direct TikTok API targets. When the frozen
snapshot selects `tiktok:HB_PH`, `tiktok:HB_MY`, `tiktok:HB_TH`, or
`tiktok:HB_VN`, keep each as an independent execution target bound to its exact
HomeBloom shop identity. The executor must not use the TikTok official API for
these stores, must not substitute the same-region LivelyHive shop, and must not
collapse the four targets into one shared result.

For Shopee, finish and verify the global product first. Then, only when the
approved snapshot explicitly selects `shopee:PH`, `shopee:MY`, `shopee:TH`,
or `shopee:VN`, the server-owned Shopee executor handles regional dispatch and
readback after Global verification. Treat every selected region independently.
A global-only run has zero regional targets. Only exact official shop-item,
model, price and global-linkage readback may record that a region is published.
Keep the approved English copy on the verified Global master. Regional create
requests must omit `item_name` and `description` so Shopee can derive the
destination copy. Official readback accepts English for PH/MY, requires Thai
for TH and Vietnamese for VN, and repairs only the exact existing wrong-language
TH/VN item before reading it again. Never create a duplicate for copy repair.

## Production command

After the user authorizes the exact offer, plan and platforms, execute:

```powershell
.venv\Scripts\python.exe skills\publish-approved-product\scripts\product_center_publication.py --offer-id <OFFER_ID> --plan-id <EXACT_PLAN_ID> --platform all --execute
```

Use `--platform tiktok`, `shopee`, or `ozon` for an isolated retry. Authorization
for one platform does not authorize another.

Product Center, not the Skill client, allocates the run identity and writes the
immutable report under `reports/product-publication/<offer>/<revision>/<run>`.
The client validates `product-publication-start/v1`, polls the exact returned
`publication-report:<run_id>`, and emits no full snapshot, confirmation token,
raw response, credential, URL, external item ID, or mutable dashboard fact.

`publish_approved_product.py` and the direct `dispatch_*.py` / `readback_*.py`
scripts are deprecated compatibility only. Never invoke them as the production
path for a new approved offer.

## Platform knowledge

Read only the relevant reference before that platform:

- `references/tiktok.md`
- `references/shopee.md`
- `references/ozon.md`

Read `references/incident-patterns.md` before adding a permanent lesson. Never
record an unconfirmed hypothesis as policy.

## Canonical Skill parity

Treat the repository directory `skills/publish-approved-product` as the only
canonical Skill source. Check the installed copy before use:

```powershell
.venv\Scripts\python.exe scripts\sync_product_publication_skills.py --check
```

If review authorizes installation, run it explicitly and then check again:

```powershell
.venv\Scripts\python.exe scripts\sync_product_publication_skills.py --install
.venv\Scripts\python.exe scripts\sync_product_publication_skills.py --check
```

Never install implicitly during publication, test execution, or Skill
validation. A parity mismatch is a deployment/configuration failure; do not
silently mix canonical instructions with installed scripts from another
digest.

## TikTok category decision

Treat the approved product type as the semantic authority and the Miaoshou
draft category as an untrusted candidate. Before TikTok dispatch:

1. Read the approved title, description, product type, use, material and
   product images from the snapshot.
2. Query the official category tree independently for every selected site and
   exact shop.
3. Rank leaf candidates by product type and use first, material second, and
   decorative theme or season only as secondary evidence.
4. Query official metadata for the best candidates. A category existing in
   metadata proves only that the ID is recognized; it does not prove semantic
   fitness or that the category is enabled for publishing.
5. Prefer one semantically exact enabled candidate. If TikTok disables that
   exact leaf, consult only the explicit user-approved fallback table in
   `references/tiktok.md`. Accept a fallback only when its official tree node
   is enabled for the exact site and its metadata is valid for the exact shop.
   For approved table-mat/placemat/coaster and tablecloth/table-runner products,
   Kyle authorizes direct use of `cid=600009` Festive Decoration. Do not first
   select `cid=600033` or `cid=600204`; the live site tree must still show
   `600009` enabled and the exact shop metadata must validate.
   Do not invent any other broad fallback.
6. If neither the exact candidate nor an approved fallback is available,
   return `CATEGORY_CONFIRMATION_REQUIRED` with the top candidates and ask for
   one main-category decision.
7. Use deterministic code to write the confirmed site category and required
   attributes, then read back the exact draft before dispatch.

Never preserve a Miaoshou-prefilled category merely because metadata recognizes
it. A fallback is valid only because the user explicitly approved that product
family fallback and the official site tree currently permits it.

## User-facing result

Expose only: **发布成功**, **平台处理中**, **部分成功**, or **发布失败**.
Retain dispatch/readback evidence in the report without credentials or raw
provider responses.
````

## 中文完整翻译

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
