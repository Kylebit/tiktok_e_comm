# 商品发布 Skills 中英对照版

> **非执行权威。** 本文件仅供 Kyle 阅读。真实执行只使用仓库中的三份英文 `SKILL.md`；英文 `SKILL.md` 是唯一执行权威。本文件由构建脚本机械嵌入当前英文原文和人工维护的中文参考译文，不包含 Skill frontmatter，也不会被 Skill 系统加载。过期译文仅供追溯，不能指导当前执行。

同步契约：`product-publication-skills-bilingual/v1`。任一英文源文件变化后，对应中文译文必须更新并绑定当前源 SHA-256，或明确标记为历史译本；未标记的过期译文会使构建与测试失败。

| Skill | 英文执行权威 | 中文翻译源 | 英文 SHA-256 | 译文状态 |
|---|---|---|---|---|
| `prepare-product-publication` | `skills/prepare-product-publication/SKILL.md` | `skill-translations/prepare-product-publication.zh-CN.md` | `473e41b19acb638829bd7b9774df5ffc2e31387155043b146b0b458d7556faf0` | 历史译本，勿用于执行 |
| `prepare-product-images` | `skills/prepare-product-images/SKILL.md` | `skill-translations/prepare-product-images.zh-CN.md` | `d25cd9ccc3bc53fdb3684c8c9ada3c0944cbe3720c9a1adac3445b4341eb73d4` | 历史译本，勿用于执行 |
| `publish-approved-product` | `skills/publish-approved-product/SKILL.md` | `skill-translations/publish-approved-product.zh-CN.md` | `f359cc04f5de0fe18934d1bfef201decadaef373d10e57cef70d4d750bb54bf0` | 历史译本，勿用于执行 |

---

# 1. `prepare-product-publication`

源 SHA-256：`473e41b19acb638829bd7b9774df5ffc2e31387155043b146b0b458d7556faf0`

## English source (verbatim)

````markdown
---
name: prepare-product-publication
description: "Prepare an auditable round-1 candidate for one Product Center Offer ID and exact target stores with zero external writes: collect authoritative SKU and parcel facts, resolve category and pricing candidates, generate title and variant-display candidates, and propose explicit image translation/generation decisions. Use when the user asks to start, prepare, inspect, resume, or redo product preparation before image work and the sole final marketplace review."
---

# Prepare Product Publication

## Explicit source binding

For a direct Agent CLI, select a complete Git source and explicit settings/data/
report profile as described in `docs/AGENT_ENTRY_BINDING.md` in that source.
Run `<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry preparation --check-binding`,
then pass the existing arguments after `--`. A personal physical Skill copy is
not a source root. Version 1 preserves source-relative data/reports and rejects
independent roots. Version 2 supports R1 existing captured inputs with explicit
state, data, source capture, content metadata and report paths. Missing captured
state is BLOCKED; this mode never imports a server or collects upstream inputs.
Its Lingshi path is metadata discovery only. Other v2 entries are unsupported.
A binding check grants no business authority and does not prove facts ready.

## Existing-product takeover

For inspect/resume, first use the commit-bound, read-only
`scripts/publication_takeover.py` in the explicitly selected project, following
`docs/PUBLICATION_SOURCE_CONTRACT.md`. Do not bootstrap or approve an existing
product merely to inspect it. That contract governs source selection and legacy
stage labels; it does not claim newer personal workflow modes are integrated.

Turn one exact Offer ID and exact target stores into a durable round-1 candidate
packet. `first-review` remains the persisted schema name for compatibility; it
is not a human approval gate. Reuse Product Center deterministic code for facts. Use agent judgment
only for documented category research, copy candidates, and recommendations.
Never guess a missing commercial or provider fact.

## Non-negotiable round boundary

The first round always has **zero external writes**. It never writes Miaoshou,
calls a paid image service, claims or creates a shop draft, or publishes.
The second round, `prepare-product-images`, generates and reviews images only;
it does not write Miaoshou. Third-round COMMON synchronization and official
readback belong to `publish-approved-product`. See
[round ownership](references/miaoshou-baseline-sync.md) when resuming legacy packets.

R1 must not ask Kyle to approve facts, copy, category, price, stock, or the image
plan as a normal stage transition. When the active autopilot policy resolves all
checks, write the existing digest-bound auto-decision and technical snapshot with
`human_approval=false`; these artifacts authorize only later preparation. Product
pages are editing and observation surfaces, not approval authorities. The sole
normal human gate is `FINAL_MARKETPLACE_PUBLISH` after the complete frozen
marketplace candidate exists.

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
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry preparation --check-binding
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry preparation -- --offer-id <OFFER_ID> --targets <COMMA_SEPARATED_TARGETS>
```

When image work is in scope, create one explicit
`first-review-image-plan/v1` JSON file and add `--image-plan <PATH>`. The plan
lists each chosen source position as KEEP, TRANSLATE, REMOVE, or REFERENCE,
exact target languages, and proposed net-new assets. Do not use OCR to select
images. Do not call a paid API in this round.

The client accepts `--candidate-plan <PATH>` for proposed category and copy
sidecars. When either plan argument is omitted, it reuses the corresponding
`first-review-image-plan.json` or `first-review-candidate-plan.json` from
`reports/product-preparation/<OFFER_ID>/` if present. Refreshing the report
preserves those input files. Offer IDs must contain ASCII digits only.
Candidate categories do not replace bound official category evidence, and
candidate copy remains PROPOSED. If the selected SEA stores include both
LivelyHive and HomeBloom, retain separate brand content and image plans;
single-brand targets do not imply an additional brand. These compatibility
rules do not install or enable personal manual-intake or autopilot workflows.

R1 retains per-SKU parcel facts and price calculations from the supplied
Product Center preview. Positive finite cost, weight, dimensions and prices
are required for material completeness. Partial per-SKU parcel facts cannot
be replaced silently by shared defaults. Variant price rows must cover the
known SKU identities; COMMON drafts have no market-price requirement.
These calculations remain candidates and do not prove provider readback.

The round-1 candidate must display the complete governed
`publication_stock_policy`: `publication-default-stock/v1`, 200 per selected SKU,
`EACH_SELECTED_SKU`, `SYSTEM_GOVERNED_DEFAULT`, `ROUND1`. Candidate validation
rejects a missing or changed policy. Never map the legacy
`stock_per_model_sku` field to this policy or silently rewrite an approved
snapshot; a different quantity needs a separately reviewed contract.

Only the original entry and Version 1 retain the existing bootstrap contract:
if no local workbench exists, the client may perform the existing upstream
read and a local workbench-state write once. These are not provider mutations.
Version 2 requires existing captured state; if it is missing, stop with
`R1_CAPTURED_STATE_MISSING` without reading upstream or creating state.
If requested targets are missing, return `DECISION_REQUIRED`; never silently
restore defaults.

Legacy `--execute-miaoshou` or `--confirm-miaoshou-write` arguments must fail
without writing. Their historical error wording is not a stage-routing authority.
`--skip-miaoshou` is a compatibility no-op; synchronization belongs to third-round COMMON.

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
and metadata. Zero or multiple safe category candidates are blockers; preserve
the evidence and smallest unresolved decision without inventing a category.

Derive translation positions and brand content groups from exact task scope,
source evidence and governed defaults. If a high-risk ambiguity cannot be
resolved, keep it as a blocker rather than turning R1 into an approval prompt.
Any explicit choice already supplied by Kyle remains binding within that scope.

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

`DEFERRED_TO_SECOND_ROUND` is the existing client's compatibility label, not
permission for R2 to synchronize Miaoshou. Preserve it in existing packets;
resolve the current stage through the Product Center contract described in
[round ownership](references/miaoshou-baseline-sync.md).

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
- explicit statement: first-round Miaoshou writes `0`; R2 handles images and R3 COMMON handles synchronization/readback;
- whether existing paid authority covers R2, or the exact missing authority if it does not.

Continue into R2 without asking for an intermediate approval when the active
policy already covers its paid purpose and budget. A new or expanded paid
purpose still needs explicit authority. R1 never authorizes Miaoshou or
marketplace writes. A new agent resumes from the durable packet and current
Product Center state, not conversation memory.

## Safety and evidence

- Keep first-round provider write count exactly zero.
- Never expose credentials, raw responses, provider URLs, or exception args.
- Keep confirmed write counts separate from attempted requests.
- Do not let stale technical state erase a valid final marketplace approval receipt.
- Update knowledge only when official facts, regression evidence, and readback
  agree; never promote a one-off hypothesis into a product-family rule.
````

## 中文参考译文

**`prepare-product-publication` 中文译文已过期：以下为历史译本，不得据此执行；请以本节当前英文 `SKILL.md` 原文为准。**

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

---

# 2. `prepare-product-images`

源 SHA-256：`d25cd9ccc3bc53fdb3684c8c9ada3c0944cbe3720c9a1adac3445b4341eb73d4`

## English source (verbatim)

````markdown
---
name: prepare-product-images
description: Consume an immutable round-1 technical snapshot and prepare auditable dual-brand master and localized image candidates through Lingshi AI, with a shared product budget, durable recovery, rework and automated QA. Use for round-2 product image preparation; do not create an intermediate human approval gate, write Miaoshou or publish.
---

# Prepare Product Images

For a direct Agent CLI, use the explicitly selected complete source's
`<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry images --check-binding`
(`--entry qa` for automated QA), then pass the original arguments after `--`.
See that source's `docs/AGENT_ENTRY_BINDING.md`: version 1 preserves source-relative
config/data/reports and rejects independent roots. Personal physical copies do
not establish source identity; the check does not grant paid or business authority.
Version 2 supports R1 captured-input preparation and the narrowly bound QA
`qa-existing-assessment` and images `images-captured-status` modes. Paid image
phases and provider/model QA remain unsupported by v2; an explicit Lingshi
discovery path does not propagate their paths.
For captured image status use `config/agent_entry.images-status-v2.example.json`.
It accepts only canonical `--offer-id`, consumes existing state, retained R1,
generation and optional translation reports, and writes only the original direct
CLI producer's phase lock; its result is stdout. Missing/invalid captures stop
before creating a lock. It does not bootstrap state, read checkpoint/paid history,
resolve embedded artifact paths, query providers or initialize a paid ledger.
The declared producer lock mapping has the same limited proof as QA below;
native/custom producers and unproven mapping reject. Preserve existing UNKNOWN
reports: this projection is not provider reconciliation or execution readiness.
For existing-assessment QA use `config/agent_entry.qa-assessment-v2.example.json`
from the selected source. It requires separate captured state, retained R1 and
R2 input reports, the existing assessment, QA output and the original direct
CLI producer's phase lock. The checker verifies only that declared source/lock
relationship; native/custom runtime producers and unverified mappings stop.
It does not prove captured report lineage or current actor state. Missing inputs
stop before a lock or output write. This mode writes the original phase lock,
normalized assessment, signed QA receipt and possible superseded signed attempt
archives; it does not call a provider or initialize a paid ledger.

Before resuming an existing product, use the selected project's commit-bound
`scripts/publication_takeover.py` as described in
`docs/PUBLICATION_SOURCE_CONTRACT.md`. Preserve prior paid receipts and QA;
the read-only result never starts a new task or converts generated assets to keep.

The internal CLIs are `scripts/prepare_product_images.py` for R2 and
`scripts/run_automated_image_qa.py` for visual QA. Direct Agent execution uses
the selected source wrapper, with the original phase arguments after `--`:

```powershell
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry images --check-binding
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry images -- --offer-id <OFFER_ID>
# Captured status has its own narrow v2 profile; no paid/translation/rework flags.
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_STATUS_PROFILE> --entry images --check-binding
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_STATUS_PROFILE> --entry images -- --offer-id <OFFER_ID>
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry qa --check-binding
# Provider/model QA below requires the original v1 layout and existing authority.
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_PROFILE> --entry qa -- --offer-id <OFFER_ID> --model <APPROVED_MODEL> --paid-policy <EXISTING_POLICY>
# Captured QA uses its own v2 assessment profile and the exact declared file.
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_ASSESSMENT_PROFILE> --entry qa --check-binding
<PYTHON> -I -B <SOURCE>/scripts/repo_bound_agent_entry.py --profile <ABSOLUTE_ASSESSMENT_PROFILE> --entry qa -- --offer-id <OFFER_ID> --assessment <ABSOLUTE_CAPTURED_ASSESSMENT>
```

Keep the frozen R1 product, brand, target and source identities intact. Read [the local budget and recovery contract](references/paid-recovery.md) before handling unknown results, legacy records or technical drift.

## Authority and inputs

Require a valid `round1-approved-snapshot/v1` and matching current Product Center technical freeze. The schema's `approved` wording is historical: an autopilot snapshot with `human_approval=false` is the normal input and grants no marketplace execution authority. Fact or target changes rebuild the candidate lineage and invalidate any later final marketplace approval bound to the old candidate.

Paid execution requires the existing applicable policy loaded by `shared_platform.publication_autopilot`. Its attributed authority, allowed purposes and product cap must cover this action. Record the selected model, current price, purpose and QA plan in the candidate evidence. Continue without an approval prompt when existing authority covers the exact paid purpose and budget. A new paid purpose, provider, or scope outside that authority requires explicit authorization before the request.

The bundled `historical-autopilot-policy.example.json` is inactive source evidence. Its original names, date and ACTIVE state are not current authority. Do not activate it as default configuration. Offline fixtures and green tests never establish live generation authority.

## Execute one phase

The generation/model-QA phase commands below retain the original v1 or
governed native contract. The v2 captured-status profile only enables step 1
when state/R1/generation captures already exist; missing captures stop without
upstream reads or state creation. Other phases are not enabled by that profile
or the v2 assessment profile.
The v2 assessment profile accepts only canonical `--offer-id` and `--assessment`; model,
paid-policy, upload, rework and alternate root/profile flags reject. Its existing
assessment branch does not consume a separate master-QA input field.

1. Run with only `--offer-id` to inspect local status. Help/status do not call paid providers; status can write its local business lock. Legacy UI consumers without the existing R1/budget bridge return `PAID_CONTEXT_REQUIRED` or `LEGACY_R2_BRIDGE_REQUIRED` before a provider call.
2. Validate the frozen brand roles and reuse plan. With applicable paid authority, run `--execute-brand-generation --paid-policy <existing-policy>`. Complete source bytes and frozen facts bind the technical plan and each checkpoint. Continue through automated master-image QA; a rendered review is an audit artifact, not a human gate.
3. Run the wrapper with `--entry qa -- --offer-id <id> --model <approved-model> --paid-policy <existing-policy>` for master QA after binding checks. Retain raw replies, exact artifact digests and the QA receipt. Passed QA belongs only to those artifacts and that R1 snapshot.
4. Freeze the policy-selected numbered-image scope with `--approve-translation-images <numbers> --dimension-only-images <numbers-or-none>`. The CLI defaults to the governed technical actor `orbit-product-publication-default-v1`; an explicit `--approved-by Kyle` is blocked because this command has no independent conversation-receipt binding. Existing Kyle plans retain their original bytes and digest as historical evidence, but cannot authorize new R2 paid execution without independently verifiable provenance. This runtime currently has no trusted historical approval registry or automatic successor migration, so affected products remain blocked for new paid translation work. These plans do not grant final marketplace approval. An existing plan with different scope or authority is preserved and requires an explicit successor rather than an in-place rewrite. Route locales within frozen R1 targets; dimension-only images create no translation tasks.
5. With matching paid authority, run `--execute-paid --paid-policy <existing-policy>`. OCR, text translation and localized image generation share the product ledger with masters and QA. Empty OCR regions are reused without a paid model request.
6. Run localized QA using the exact passed master QA receipt and current localized artifacts. Preserve failures in factual alignment, OCR language or duplication as blockers or bounded rework evidence. Passed artifacts flow to R3 candidate compilation without an intermediate approval prompt.

For wallpaper and wall stickers consume the existing product-family rule packs under `prepare-product-publication/references`. Do not invent missing facts in prompts.

## Budget and recovery

`reports/product-preparation/<id>/paid-requests/events.jsonl` is the accounting authority. Brand, locale, QA, retries, new processes and changed approval digests share it. A durable reservation occupies a slot before POST. Attempted, unknown, failed, superseded and completed work remains counted. The ledger lock is released before provider waits.

For a new product, code inventories complete known R1/R2 report and localized review/pack metadata roots. Ordinary review HTML/Markdown and local plans are evidence, not paid calls. Positive old receipts are imported with source hashes and same-provider task duplicates count once. Missing metadata, redirected roots, ambiguous ownership and unknown prior work cannot become zero.

Reports separate planned requests for the current phase, occupied, attempted, unknown, confirmed and new requests in this invocation. Asset counts and old receipt generation counts are lifetime artifact facts. Raw usage/cost is separate from price estimates. Prior R1 title/copy calls count only when the applicable product policy includes that purpose and a bound actual request receipt exists.

For SUBMITTING, UNKNOWN or post-submit timeout, inspect local request/checkpoint state. Existing task IDs resume by querying, never by creating again. Raw chat replies are durable before parsing and can be parsed again locally. Timeout without raw reply requires upstream reconciliation; changing prompt or retry number is not authorization to resend.

Use the local recovery CLI in `references/paid-recovery.md`. It checks exact product/request/attempt/revision, source hashes, verifier, timestamp and evidence reference. It does not discover or prove external billing facts. Preserve bad and legacy bytes; never manufacture a no-charge conclusion.

## Rework and technical rebuild

A completed paid artifact rejected by exact QA or explicitly selected for redo may create a new attempt under existing rework policy and remaining product budget. Use `--prepare-brand-rework` for the bounded brand request, or `--retry-localized-review-number <n> --retry-locale <locale> --retry-failure-code <code> --retry-authorized-by <existing-actor>` for one localized artifact. Bind old task/artifact, QA or user intent, current input and next attempt. A known failed exact artifact may be retried within the existing policy without another prompt; a new paid purpose or expanded scope may not. Maximum three retries applies; old successful work remains counted and needs no false no-charge proof.

TECHNICAL_PLAN_DRIFT writes a digest-bound proposal and preserves the active plan. Once existing approval provides valid current inputs, explicitly activate that proposal through the local CLI. Activation archives the old plan and affected R2 projections, emits R3 invalidation evidence and retains all paid events. Unknown product requests block activation. Never delete ledger/checkpoint files to make a new plan run.

R2 does not write Miaoshou, ReleaseStore, ReleasePlan, marketplace drafts or publication. R3 consumes the technically adopted image evidence and `round2-technical-invalidation.json` through its own authority. Offline tests, image QA and compatibility fields named `approved` do not authorize publication. Only the later digest-bound `FINAL_MARKETPLACE_PUBLISH` receipt does.
````

## 中文参考译文

**`prepare-product-images` 中文译文已过期：以下为历史译本，不得据此执行；请以本节当前英文 `SKILL.md` 原文为准。**

<!-- source_sha256: 5054f5f4145c46e623a31f93ca2c4898c7ff87e8c03e721640f5ce97a00556d2 -->

> 历史译本（2026-09-23 标注）：此 source_sha256 与当前英文 Skill 不一致；请读当前英文 `SKILL.md`，它是唯一执行权威。旧译文中的 R1 和图片审核步骤不代表当前一轮最终审核流程。

### 元数据

- `name`：`prepare-product-images`
- `description`：消费不可变第一轮快照，通过灵识 AI 准备双品牌母版与本地化图片，使用共享产品预算、持久化恢复、返工和自动 QA。用于第二轮商品图片准备；不重新批准事实、不写妙手、不发布。

### 准备商品图片

恢复已有商品前，按仓库根 `docs/PUBLICATION_SOURCE_CONTRACT.md` 使用所选项目中绑定提交的 `scripts/publication_takeover.py`。保留已有付费回执与 QA；只读结果不会启动新任务，也不会把已生成资产转为 `keep`。

R2 使用 `scripts/prepare_product_images.py`，视觉 QA 使用 `scripts/run_automated_image_qa.py`。保持冻结 R1 的商品、品牌、目标与来源身份。处理未知结果、旧记录或技术漂移前，读取 `references/paid-recovery.md` 中本地预算与恢复合同。此处 `scripts/`、`references/` 相对 canonical Skill 目录；`shared_platform` 和 `reports` 路径相对仓库根。

### 权威与输入

要求有效的 `round1-approved-snapshot/v1` 和匹配的当前商品发布中心批准。在原范围内复用批准。事实和目标变更仍走既有 R1 批准流程。

付费执行要求由 `shared_platform.publication_autopilot` 加载既有适用政策，其归属权威、允许用途和产品上限必须覆盖本动作。开始新授权的真实生成前，展示所选模型、当前价格、用途与 QA 计划，取得所需付费授权。已有匹配授权无需再次索取。

随包的 `historical-autopilot-policy.example.json` 是非活动的历史来源证据。其原名称、日期和 ACTIVE 状态不是当前权威，不能将其激活为默认配置。离线 fixture 和绿色测试不构成真实生成权限。

### 执行一个阶段

1. 仅带 `--offer-id` 运行以检查本地状态。help/status 不调用付费服务。缺少既有 R1/预算桥接的旧 UI 消费者在调用服务前返回 `PAID_CONTEXT_REQUIRED` 或 `LEGACY_R2_BRIDGE_REQUIRED`。
2. 校验已批准的品牌角色与复用计划。在适用付费授权下运行 `--execute-brand-generation --paid-policy <existing-policy>`。完整来源字节与冻结事实绑定技术计划及每个 checkpoint。完成整套母版图片审核后停止本阶段。
3. 使用 `run_automated_image_qa.py --offer-id <id> --model <approved-model> --paid-policy <existing-policy>` 执行母版 QA。保留原始回复、精确产物摘要和 QA 回执。通过结果只属于这些产物及该 R1 快照。
4. 用 `--approve-translation-images <numbers> --dimension-only-images <numbers-or-none> --approved-by <existing-actor>` 冻结已经批准的编号图片范围。在冻结 R1 目标内路由语言；仅尺寸图不创建翻译任务。
5. 在匹配的付费授权下运行 `--execute-paid --paid-policy <existing-policy>`。OCR、文字翻译、本地化图片生成与母版及 QA 共用产品账本。OCR 区域为空时直接复用，不发送付费模型请求。
6. 使用精确已通过的母版 QA 回执和当前本地化产物执行本地化 QA。保留事实对齐、OCR 语言或重复性失败供复核。

壁纸和墙贴消费 `prepare-product-publication/references` 下既有产品家族规则包；不能在提示词中编造缺失事实。

### 预算与恢复

`reports/product-preparation/<id>/paid-requests/events.jsonl` 是计数权威。品牌、语言、QA、重试、新进程及改变后的批准摘要共用该账本。POST 前先持久化预留并占用名额。已尝试、未知、失败、被替代与完成的工作仍计数；等待服务返回前释放账本锁。

对新商品，代码完整盘点已知 R1/R2 报告与本地化审核/包元数据根。普通审核 HTML/Markdown 和本地计划属于证据，不是付费调用。有效旧回执带来源 SHA 导入，同一服务商的重复任务仅计一次。缺元数据、重定向根、归属不明确或旧工作未知，均不能当作零。

报告分别显示本阶段计划请求数、已占用、已尝试、未知、已确认，以及本次调用新增请求数。资产数量和旧回执生成数量是全生命周期产物事实。原始 usage/成本与价格估算分开。只有适用产品政策包含该用途，且存在绑定的实际请求回执时，才计入先前 R1 标题/文案调用。

遇到 SUBMITTING、UNKNOWN 或提交后超时，检查本地请求/checkpoint 状态。已有 task ID 通过查询恢复，不能重新创建。原始聊天回复先持久化再解析，可在本地重新解析。超时且没有原始回复时需上游对账；修改提示词或重试编号不授予重发权限。

使用 `references/paid-recovery.md` 中的本地恢复 CLI。它检查精确商品/请求/attempt/revision、来源 SHA、核验者、时间戳和证据引用，但不会发现或证明外部计费事实。保留损坏及旧版本字节，绝不编造未计费结论。

### 返工与技术重建

已完成付费产物若被精确 QA 拒绝或被明确选中重做，可在既有返工政策与剩余产品预算内创建新 attempt。品牌请求用 `--prepare-brand-rework`；单个本地化产物用 `--retry-localized-review-number <n> --retry-locale <locale> --retry-failure-code <code> --retry-authorized-by <existing-actor>`。绑定旧任务/产物、QA 或用户意图、当前输入与下一 attempt。最多重试三次；旧成功工作仍计数，无需虚假的未计费证明。

TECHNICAL_PLAN_DRIFT 写出绑定摘要的提案，并保留活动计划。既有批准提供有效当前输入后，通过本地 CLI 显式激活提案。激活归档旧计划和受影响 R2 投影，输出 R3 失效证据并保留全部付费事件。商品请求仍未知时禁止激活。不能删除账本/checkpoint 让新计划运行。

R2 不写妙手、ReleaseStore、ReleasePlan、平台草稿或发布。R3 通过自己的权威消费已批准图片证据和 `round2-technical-invalidation.json`。离线测试和图片 QA 不授予发布权限。

---

# 3. `publish-approved-product`

源 SHA-256：`f359cc04f5de0fe18934d1bfef201decadaef373d10e57cef70d4d750bb54bf0`

## English source (verbatim)

````markdown
---
name: publish-approved-product
description: "Execute round 3 for a product with immutable round-1 facts and round-2 images: bind or update Miaoshou with official readback, freeze the release snapshot, present one final marketplace review, publish TikTok, Shopee and Ozon independently, and retain target-specific readback and confirmed incident lessons."
---

# Publish Approved Product

This Skill owns the entire third round. It may not re-open first-round fact,
category, price, copy, target or image-plan approval, and it may not regenerate
second-round images. It validates immutable snapshot identity and performs
technical preflight, Miaoshou write/readback, release compilation, the one
final marketplace approval, independent platform execution and readback.

Use the approved Product Center snapshot as the only shared input. TikTok,
Shopee and Ozon are independent tasks. Never let one platform's previous state,
failure, warning, or readback block another platform.

Target-scoped recovery is permitted only for one explicitly selected platform.
Every requested label must be unique, belong to that platform, and remain inside
the immutable approved target list. A successful scoped recovery never replaces
the full-product truth: the final UI and handoff must merge the newest result per
approved target and continue to show every unresolved target.

After the Miaoshou COMMON write and official readback, Kyle reviews the complete
frozen candidate for every selected target on the Product Publication frontend
and makes one final marketplace decision there. Once implemented and verified,
a trusted local bridge may carry that frontend decision in a server-verified
approval envelope; its durable receipt is the sole human approval. No separate
conversation confirmation is required. Bind the receipt to the candidate,
snapshot, ordered targets, and approved companion actions. The server must
verify the submitting Windows user and exact review identity through a
protected local channel. The local trust policy accepts programs running as
that verified Windows user without separate approval for each program. A bare
browser click or caller-supplied `user_approved`, `approved_by`, token, or digest
does not prove that identity or create authority. R1 auto-decisions, image
adoption, QA, and Miaoshou sync do not authorize marketplace publication. The
agent must persist the exact frozen revision and plan through the deterministic
Product Center boundary before publication; a missing or stale technical fact
may block execution, but the same unchanged decision must not be requested
again on another page, in a conversation, or for another platform.

At historical canonical `248a0403`, the legacy marketplace approval POST accepted
caller-declared approval fields without verifying a Windows user or a trusted
envelope; business execution was HOLD under that release's `identity_only` gate.
This historical observation does not select the current task's source or runtime.
Use the current Work Order and fresh runtime identity to verify the actual approval
path and execution gate. A legacy caller-declared path without a verified protected
channel remains unusable for approval; require server-side user verification, fresh
exact review binding, a durable receipt and rejection of ordinary browser POSTs.
If the selected runtime is still identity-only or business execution is unverified,
keep dependent marketplace execution blocked and continue independent authorized
work. This Skill change cannot enable the gate or grant approval. Recheck the actual
service before any business action; a source commit or local test is not deployment.

## One final-review gate

Load `config/product_publication_autopilot_policy.json` and the confirmed
`references/incident-registry.json` through
`shared_platform.publication_autopilot`. Before requesting marketplace
publication approval, run `scripts/compile_release_candidate.py` for the exact
approved `offer_id + plan_id`. The immutable
`publication-release-candidate/v1` must be `READY_FOR_FINAL_REVIEW`, bind the
exact snapshot, targets, variants, image routes, zero-write simulation, platform
write budgets, the machine-readable product-family quality gate, durable
image/translation/OCR/pricing evidence and confirmed
incident safeguards, and be visible on the Product
Publication frontend. This is the only normal human approval gate. A policy,
bare page button without a verified receipt, prior product approval, image
approval, or Miaoshou sync does not authorize marketplace publication.

The candidate must also contain one digest-bound `companion_actions` review.
It lists every known action needed to finish the approved publication, including
Ozon stock quantity per Model SKU and the warehouse-selection rule, selected
post-publication discount actions and their immutable pricing/selection policy,
and the readback that proves completion. The final approval covers those exact
actions plus bounded technical retries, read-only reconciliation and provider
asynchronous convergence. Do not split them into later approval prompts.
Resolve mutable provider IDs, such as the one current eligible warehouse or
ongoing discount activity, by official read-only lookup under the frozen rule;
that lookup does not create a new business decision.

The zero-write simulation must use `platform-preflight-report/v2`. A top-level
field-presence check is insufficient. Before any provider mutation it must
also prove: the exact per-target TikTok mainland-pickup warehouse semantic is
frozen for every selected TikTok store; the selected Shopee official category
and all required attribute selections are frozen rather than deferred; and
every Model SKU has one valid, unique HTTPS variation-image binding. Any
missing, duplicate or deferred value is a zero-write blocker. Warehouse names
are semantic facts only: runtime still reads the current official warehouse ID
for that exact store and sets every other active local warehouse to zero.

Counts and digests alone are never a complete final review. The exact
digest-bound candidate must contain and the primary frontend panel must show:
every frozen shopper-facing title and description grouped by the targets that
reuse it; every seller SKU, model SKU, specification, cost, parcel weight and
package size; every target's platform, store, locale, category and per-SKU
price (including the complete collapsible calculation evidence when present);
and every exact ordered target image route grouped only when the full route is
identical. The approval control must remain unavailable when this review
manifest is missing. Any change to these fields changes the candidate digest
and immediately invalidates the prior approval. Keep provider identities,
credentials, raw responses and hidden reasoning out of this projection.

When the plan identity contains `:shadow-`, require a frozen
`shadow-formal-conformance/v1` receipt before the candidate can become ready.
It must cover the complete ordered target list and bind per-target copy,
category, price, gallery, description-image and formal-route digests. Gallery
and description images are independent projections but must contain the same
complete ordered set. Also require the first-round
`publication-default-stock/v1` policy and show its per-SKU quantity in the
final review. Missing or drifted conformance is a zero-write blocker; the
formal publication center must never reconstruct these values from a shared
master or an agent conversation.

Keep SKU terminology exact in that review. Miaoshou `itemNum` is the one
product-level Seller SKU and must be shown once. Each sellable variant is
identified by its distinct Model SKU; label that value `SKU ID (Model SKU)` in
the frontend. Never repeat the product-level Seller SKU inside every variant
card or relabel a Model SKU as a second Miaoshou item identity. The variant SKU
IDs must remain unique and cover every frozen variant.

Before compiling that candidate, run the third-round preparation boundary. It
freezes `round2-image-snapshot/v1`, creates or binds a manual-intake Miaoshou
identity when needed, writes the approved common baseline exactly once, reads
it back, and freezes the release handoff. This is technical execution under the
standing policy, not another content approval:

```powershell
.venv\Scripts\python.exe skills\publish-approved-product\scripts\prepare_publication_execution.py --offer-id <OFFER_ID> --execute-miaoshou --finalize-release-handoff
```

For wallpaper or decorative wall stickers, `shared_platform.publication_quality`
must deterministically select the matching Chinese machine-readable product
family pack and fail closed on category drift, fewer than
seven unique target images, wrong localized-image locale, unsupported marketing
claims, or a higher-cost variant priced below a lower-cost variant. These are
deterministic validation errors, not new business-approval prompts.

For an exact `dual-brand:<offer_id>:<identity>` handoff, load and bind all four
durable sidecars before the candidate can become ready:
`workflow-handoff.json`, `dual-brand-publication-handoff.json`,
`brand-image-translation-plan.json`, and `brand-image-translation.json`.
Verify the same Offer, plan and snapshot digest; the exact approved ordered roles
and artifact digests for every target; approved translation tasks, completed
localized assets and source OCR evidence; and every frozen SKU price against
the handoff calculation result. New `publication-price-calculation/v2` rows
must also pass the independent Decimal recomputation boundary: SEA, MX and GB
use distinct formula kinds and retain every cost, logistics, fee/tax rate,
target margin and discount-reserve input. A same-source result comparison is
not formula evidence. Missing evidence or any drift is a zero-write
blocker. `NOT_AVAILABLE` remains visible only for legacy non-exact handoffs and
must never silently qualify a new dual-brand handoff.

The Runner passes the same candidate and platform budget to every executor.
Target-scope drift or a confirmed write count above budget is a contract
failure. Every platform transport must reserve the supplied shared or
target-scoped attempt budget immediately before each provider mutation. Budget
exhaustion stops before the network call. Timeout and unknown outcome consume
the reservation permanently because the provider may already have written.
The ordered reservations, limits and attempt totals must be stored in immutable
`product-publication-report/v3` and projected on the frontend; a post-result
confirmed-write check remains defense in depth.

## Required architecture

1. Require the exact approved `offer_id` and `plan_id`; do not derive either
   from the mutable dashboard.
2. Use `skills/publish-approved-product/scripts/product_center_publication.py`
   as the only production command.
   When more than one local Product Center service exists, verify the exact
   healthy service that loaded the current committed code and pass its URL
   explicitly with `--base-url`. Never rely on the script default while an
   older service is still reachable; compare the report execution identity
   with the intended commit before diagnosing or retrying a result.
3. Let Product Center resolve the frozen v4 snapshot and run each authorized
   platform through its server-owned async Runner and immutable report.
4. Continue to the next platform after any platform failure.
5. Expose only the four-state sanitized summary. Product Center retains the
   detailed redacted evidence and platform readback in its durable report.
6. Allow several exact `offer_id + plan_id` pairs to execute at the same
   time. Allocate a separate durable run and immutable report for every
   product-platform pair. A different product must never wait on a process-wide
   product lock; the same product/platform snapshot remains single-flight.
7. Source mode is provenance, not a publication shortcut. A frozen
   `manual-intake` snapshot may run only the platforms whose preflight is
   complete. Before TikTok, reconcile the exact manual source identity in the
   Miaoshou common collect box and create, populate and read it back when
   absent; never send the Product Center id as though it were a Miaoshou detail
   id. A still-unresolved identity blocks only TikTok and must not block an
   independently ready Shopee or Ozon run.
   When manual intake has no Miaoshou link, the required order is: create one
   Miaoshou common item, populate the approved variants/copy/gallery/description
   media, read the same identity back, persist the binding, and only then enter
   TikTok preparation. Never dispatch TikTok directly from a Product Center-only
   identity.

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

Treat each publication option name as shopper-facing content, never as a raw
supplier key. Before a candidate can become `READY_FOR_FINAL_REVIEW`, reject
names containing Seller/Model SKU IDs, structural supplier delimiters such as
`;` or `【】`, untranslated slash-packed supplier labels, empty values, or
more than 80 characters. Keep the raw variant key separately for lineage.
Corrections after a frozen snapshot must create and approve an immutable
SKU-display-name-only successor; never overwrite the predecessor snapshot.

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

Those four states are provider/business outcomes, not proof that a local
worker is still alive. `PROCESSING` with `readback_completed=true` is a stable
`READBACK_ONLY` result: the command has returned, the current official
readback budget is exhausted, and no local mutation remains. Persist it,
display it as stable provider processing, and allow only a later read-only
reconciliation. Never wait indefinitely for a worker and never repeat the
start/POST merely because the provider has not reached its final state.

After final approval or execution, the frontend must not continue to say
"等待最终审核" or use an empty legacy ReleaseRun as proof that nothing ran.
The digest-bound candidate distinguishes pending, approved and executed. Once
any platform run exists, immutable per-platform publication reports are the
primary execution ledger; compatibility ledgers may remain visible only with
an explicit statement that they do not authorize a retry.

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
count, plus the redacted mutation-budget ledger (platform, limits, attempt
counts and safe operation identifiers). Public reports remain four-state
results, may show that ledger, and strip target evidence and execution identity.
Raw responses, headers, URLs, tokens, exception arguments,
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
destination copy. Official readback accepts English only for PH, requires Malay
for MY, Thai for TH and Vietnamese for VN, and repairs only the exact existing
wrong-language MY/TH/VN item before reading it again. Never create a duplicate
for copy repair. Provider-derived MY/TH/VN copy is not trusted merely because
it contains one target-language token. Require every semantic description line to be
localized (dimension-only lines may remain invariant), reject residual English
phrases in the title except approved Latin material/unit tokens, and preserve
every approved material and every approved finished-size option. Route an
authorized repair through Lingshi text only, update the already identified
regional item in place, and read it back again. Do not invoke any unapproved
fallback provider for repair text.

An authorized regional-copy repair gets exactly one paid Lingshi request. Do
not retry automatically and do not split a failed result into extra paid
line-by-line calls. When the regional item already uses Shopee extended
description media, repair copy atomically: send the localized title, localized
text block, and the exact existing ordered description image IDs in the same
official update. A plain-text update that drops description images is forbidden.

For TikTok and Shopee alike, a populated product gallery does not satisfy the
description-media requirement. Project the complete ordered target image route
into the product description as well, using TikTok/Miaoshou rich `notes` or
Shopee regional `description_info.extended_description`. Final readback must
prove that every description image belongs to that exact target and matches
the target gallery count and order. Plain-text-only descriptions, missing
images, reordered images, and cross-country or cross-brand routes fail closed
and must be shown as a failed target step on the Product Publication frontend.

Before either TikTok or Shopee crosses its first provider-write boundary, run
the deterministic description-media preflight. It must prove non-empty approved
copy and one complete, unique, ordered HTTPS image route for every exact target,
with equal gallery and description counts. A failure is a zero-write target
failure with code `description_media_preflight_failed`; do not continue to a
provider to repair an invalid route later.

For Ozon, the first-round `publication-default-stock/v1` quantity is a frozen
publication fact, not a frontend-only suggestion. After every approved Model
SKU has an authoritative created-listing readback, resolve exactly one active
non-KGT seller warehouse, read the exact FBS stock for every approved offer,
and write all mismatches in one bounded `/v2/products/stocks` batch. A
`PUBLISHED` result requires a second exact warehouse-scoped readback proving
the frozen quantity for every Model SKU. Reserve one shared mutation attempt
for that batch in addition to one import attempt per Model SKU. If the stock
write outcome is unknown, never retry it blindly: read back first, keep the
confirmed write count unknown, and surface reconciliation even when the
desired stock is now visible. Missing or ambiguous warehouse facts, duplicate
stock rows, incomplete SKU coverage, or readback failure cannot be classified
as published.

Provider identities are operational evidence, not public report fields. Persist
TikTok detail/shop identities and Shopee global/region/image identities in the
server-owned run checkpoint before any later fallible action. The frontend may
show only whether the identity is bound; it must not expose the raw external ID.

## Business closure without rewriting platform truth

When the user explicitly ends a product after reviewing the target results,
use the server-owned closure through the loopback-only standard-library client
`scripts/close_product_publication.py`. Supply an explicit base URL and expected
runtime root; `doctor` identifies the service but does not establish business
authority. Follow [the closure reference](references/closure.md) for the
prepare, record and latest commands. Never retry an ambiguous record; reconcile
the expected closure ID and digest against the exact plan's latest result.
The closure references the exact source
reports and keeps their `PUBLISHED`, `PROCESSING`, `FAILED`, or capability block
facts unchanged. A user-accepted manual handoff is a separate resolution, never
a platform success. A closure with processing or failed targets is
`CLOSED_WITH_OPEN_ITEMS` even though the business workflow is complete.

The Product Publication frontend must prefer the latest valid closure as its
completion view: show the target matrix, verified/manual/processing/failed
counts, identity-bound flag, source run identities and manual handoff evidence.
Keep original execution history available behind a collapsed audit disclosure
and hide mutation controls for a closed product. Never overwrite the original
publication or repair reports.

## Production command

After the user authorizes the exact offer, plan and platforms, execute:

```powershell
.venv\Scripts\python.exe skills\publish-approved-product\scripts\product_center_publication.py --offer-id <OFFER_ID> --plan-id <EXACT_PLAN_ID> --platform all --base-url <VERIFIED_PRODUCT_CENTER_BASE_URL> --execute
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
After that gate is satisfied, also add one bounded CONFIRMED row to
`references/incident-registry.json` with its invariant, regression tests, fix
commit and readback requirement. The registry is the machine-readable index;
platform references retain the detailed evidence.

## Canonical Skill parity

Use `skills/publish-approved-product` in the current Work Order's verified complete
repository as the execution source. A personal thin routing installation may retain
older reference scripts and intentionally different instructions; read the selected
repository's English Skill and run its actual entry, never those personal scripts.
Whole installed-package parity is not a prerequisite for this routing scenario and
does not require installing the complete Skill set. Mixed loaded scripts or source
versions still fail; a routing note cannot override source/runtime identity guards.

When the Work Order instead selects a full installed Skill package, check its
complete manifest from the selected repository; the sync tool remains fail-closed:

```powershell
.venv\Scripts\python.exe scripts\sync_product_publication_skills.py --check
```

Only when the task explicitly authorizes full-package installation, run it and
then check again:

```powershell
.venv\Scripts\python.exe scripts\sync_product_publication_skills.py --install
.venv\Scripts\python.exe scripts\sync_product_publication_skills.py --check
```

Never install implicitly during publication, test execution or Skill validation.
A mismatch blocks use of the selected full installed package; preserve its exact
differences rather than declaring parity successful. It does not block separately
bound execution from the verified repository. Never execute scripts or apply
source instructions from another digest as though they belonged to that source.

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

For wallpaper, an enabled decorative-sticker fallback may be used only when
the frozen snapshot contains a durable `self_adhesive=true` product claim with
Kyle's conversation-approval evidence. Once that fact is recorded, inherit it
without asking Kyle to approve it again; shopper-facing title or description
wording is not the fact authority. Words proposed by title generation,
selected pending claims, or a visual guess are not verified facts. If the
ordinary wallpaper leaf is disabled and the durable claim is absent, fail
before provider dispatch with zero writes and show the exact missing fact on
the frontend. Always re-read the exact site's current category tree and exact
shop metadata; that technical verification is not a new human approval gate.

Never preserve a Miaoshou-prefilled category merely because metadata recognizes
it. A fallback is valid only because the user explicitly approved that product
family fallback and the official site tree currently permits it.

## User-facing result

Expose only: **发布成功**, **平台处理中**, **部分成功**, or **发布失败**.
Retain dispatch/readback evidence in the report without credentials or raw
provider responses.

## Frontend audit projection is part of completion

Every platform start, preparation outcome, dispatch, readback and final
classification must first be durable in the exact immutable publication report,
then be projected on the Product Publication frontend. The projection must show
the snapshot check, whether a platform request was attempted, readback state,
final classification, per-target status, confirmed external-write count, and
the pre-mutation budget limits, attempts and ordered safe operation names.
Show only sanitized evidence and concise decision results; never expose raw
provider responses, credentials, external item identities or hidden reasoning.

Do not claim the publication workflow is complete unless the frontend resolves
the same `offer_id + revision + plan_id + snapshot_digest` report and displays
its current platform state. A missing or stale frontend projection is a workflow
defect to fix before handoff, not a reason to repeat a platform write.

## One final approval and governed recovery

After exact Miaoshou write/readback, run the zero-write platform preflight and
persist `platform-preflight.json`. Only then compile the final candidate. The
sole human approval must bind `candidate_digest + snapshot_digest + complete
ordered target list`; any digest drift blocks execution pending reconciliation.
A material change to frozen facts, target scope, or companion actions requires
a new complete review; a read-only freshness check does not. No platform
executor may start without the current `final-publication-approval/v1` receipt
and a fresh execution-time validation of its identity and scope.

After that receipt exists, never ask for the same approval again because the
session or agent changed, a provider is still processing, a readback must be
refreshed, a bounded technical retry remains inside the frozen candidate and
budget, or a known companion action still has to converge. Revalidate and reuse
the receipt for those operations.

Require new explicit authority only when the frozen candidate changes, target
scope expands, a paid action falls outside existing paid authority, a new external
write class is introduced, or another commercial fact outside the candidate is
proposed. Changes to SKU, shopper-facing content, price or discount, stock
quantity, or warehouse-selection policy are candidate changes. Do not describe a
technical retry or read-only provider lookup as a new paid or external action.

If a required fact was present in immutable approved lineage but a projection
bug dropped it from a later snapshot, classify that as a system defect. Repair
the projection permanently and create a controlled continuation receipt that
binds the original candidate and approval digests, source evidence digest,
restored fact digest, exact target, operation kind, mutation budget and
`no_scope_expansion=true`. The continuation may resume under the same approval
only when the restored value is exactly reproducible from that lineage and adds
no new commercial choice. Missing, conflicting or newly chosen facts fail
closed and require a new review. Never overwrite the original candidate,
approval, snapshot or final report; persist successor/reconciliation lineage.

Every non-success result must be classified with
`config/publication_error_policy.json`. An unknown write has zero automatic
retries and requires readback reconciliation before any new mutation. A
transient failure may retry only when confirmed writes are zero and only within
the configured budget. Persist the classification and recovery budget in the
target report, then project the same evidence to the frontend.
````

## 中文参考译文

**`publish-approved-product` 中文译文已过期：以下为历史译本，不得据此执行；请以本节当前英文 `SKILL.md` 原文为准。**

<!-- source_sha256: b91bb9cccdd45eefb7e6b726bd4690e07c66b94139072baed90b8d389a9af7dc -->

> 历史译本（2026-09-22 标注）：此 source_sha256 与当前英文 Skill 不一致。旧 closure HTTP 命令不可执行；请读当前英文 `SKILL.md` 和 `docs/AGENT_HANDOFF.md`。

> 2026-09-29 合同提示（以下仍是历史译文，不是执行权威）：妙手 COMMON 技术写入及官方回读后，Kyle 在商品发布前端审阅完整冻结全目标候选并只批准一次。未来经服务端验证的本机前端回执可独立构成唯一终审，无需在会话中再次确认；裸页面按钮和客户端自报字段不构成授权。当前正式业务仍为 HOLD，具体条件以当前英文 `SKILL.md`、有效工单和现场门禁为准。

### 元数据

- `name`: `publish-approved-product`
- `description`: 对已经批准的商品发布中心 Offer，通过独立的 TikTok、Shopee 和 Ozon 工作流执行第 05–07 阶段；检查冻结事实，执行平台专属发布/回读，或准备并记录绑定精确计划的本地业务关闭。用于发布、重试、检查或关闭已完成 01–04 阶段的已批准 Offer ID。

### 发布已批准商品

接管时，按仓库根 `docs/PUBLICATION_SOURCE_CONTRACT.md` 使用所选项目中绑定提交的 `scripts/publication_takeover.py`。它只读取已有证据，不恢复发布 run，也不授予商城操作权限。其来源绑定须与历史数据分别保存。

本译文的脚本简称与 `references/` 相对 canonical `skills/publish-approved-product` 目录；显式 `skills/`、`scripts/sync_product_publication_skills.py` 和 `reports/` 路径相对仓库根。

只使用已批准的商品发布中心快照作为公共输入。TikTok、Shopee 和 Ozon 是独立任务。一个平台的历史状态、失败、警告或回读绝不能阻止另一个平台。

此历史段落的“会话是唯一批准权威”表述已失效；以开头的 2026-09-29 合同提示及当前英文 `SKILL.md` 为准。

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

### 本地业务关闭

使用 `scripts/close_product_publication.py` 执行只读 doctor/prepare/latest，以及显式本地 record 动作。它调用现有商品发布中心关闭服务，不执行发布。读取 canonical 的 `references/closure.md`，了解可移植命令、精确 prepared-input 交接与响应丢失恢复。

保留返回的完整目标阻断项与来源身份；下方发布四态摘要不能代替关闭证据。

### 用户可见结果

只能显示：**发布成功**、**平台处理中**、**部分成功**或**发布失败**。

dispatch/readback 证据保留在报告中，不得包含凭据或平台原始响应。
