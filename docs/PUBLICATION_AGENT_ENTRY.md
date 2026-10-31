# Agent 商品上架入口

本页面向以 Codex 对话为主的工作方式。正常新品使用同一个持久任务、完整冻结候选和一次终审；R1、R2、COMMON、平台执行不是需要用户分别管理的会话。本文描述候选代码的合同，不证明该版本已经部署或获得商家写入权限。

## 选择来源一次，保持同一任务

先按当前 Work Order 和 ORBIT_STATUS 核验完整 Git 根、分支、HEAD、dirty、配置、数据库、数据根及运行来源。个人 Skill 路径、默认 cwd、旧端口和健康页面均不是来源权威。使用该源码内的 `scripts/orbit_publish.py`；其 profile 模板为 [publication_execution_profile.example.json](../config/publication_execution_profile.example.json)。模板中的根、提交、配置、端口均须替换成现场核实值，不能直接执行。

profile 绑定入口输入、输出、报告、checkpoint、ReleaseStore 和实际 runtime。输入、输出、报告、checkpoint 必须位于源码之外的任务数据根；所有声明的路径必须已存在且不能是目录联接/符号链接。它不移动文件、不初始化数据库、不启动服务，也不把既有 R1/R2 的源码相对路径重定向到新根。原生 worker 的 state、R1/R2 reports、配置、catalog 和 ReleaseStore 仍须在启动部署中显式核验，不能靠填写 profile 改变正在运行的服务。

`native-sole-final` 是正常新任务模式；`standard-v4` 仅用于已有标准执行包。必须显式选择，不能在原生执行失败后切换模式绕过原批准。prepare 对现有服务的显式新任务 POST 建立唯一来源；更改 profile/source-key 不是重发 UNKNOWN 的手段。

```powershell
<PYTHON> -I -B <SOURCE>/scripts/orbit_publish.py --profile <PROFILE> binding
<PYTHON> -I -B <SOURCE>/scripts/orbit_publish.py --profile <PROFILE> prepare --offer-id <OFFER> --targets <EXACT_COMMA_SEPARATED_LABELS> --source-key <STABLE_TASK_KEY>
<PYTHON> -I -B <SOURCE>/scripts/orbit_publish.py --profile <PROFILE> status --task-id <ORIGINAL_TASK>
<PYTHON> -I -B <SOURCE>/scripts/orbit_publish.py --profile <PROFILE> review --offer-id <OFFER>
# 用户只在原商品上架前端对完整候选做一次终审；本命令不批准商品。
<PYTHON> -I -B <SOURCE>/scripts/orbit_publish.py --profile <PROFILE> execute --offer-id <OFFER> --decision-id <ORIGINAL_STORED_DECISION>
<PYTHON> -I -B <SOURCE>/scripts/orbit_publish.py --profile <PROFILE> reconcile --offer-id <OFFER> --decision-id <ORIGINAL_STORED_DECISION>
<PYTHON> -I -B <SOURCE>/scripts/orbit_publish.py --profile <PROFILE> result --task-id <ORIGINAL_TASK>
```

`prepare` 是创建原持久任务并让已有 worker 处理的入口，不是同步完成所有准备的承诺。status 提供原任务与原阶段；缺资料时 Agent 继续该任务的实际生产器，不创建第二条会话/批准链。`status`、`result` 和 `review` 不 POST 执行、不续期、不建库或生成付费图片；服务投影本身仍可能进行既有本机身份核验。binding 是本地来源检查，其他 runtime 命令还核对真实 health 的 root/commit/config，以及 home/runtime 的 version/manifest/data_root。

单独执行“检查商品/R1 事实准备”仍遵循 R1 零外部写边界，使用该 Skill 的窄入口。不能因为本页存在而把只读检查扩成完整上架、付费生图、COMMON 写入或发布。完整上架任务的后续动作依原有效授权及对应 Skill 执行。

## 一次终审前要齐备

- 精确 Offer、全部 target、每个规格的 source key/Model SKU、成本和已知/估计/未知包裹事实。尺寸未知不能由生图编造。
- 真正可执行的类目、必填属性、每平台 adapter/readback 能力及原账号绑定；静态预检不能保证在线凭证/平台权限仍有效。
- 按适用产品规则的完整图库和自动 QA；无专用规则时每品牌至少七张不同合格图片。每个规格 hero 必须由冻结的单一 `variant_keys` 关系指向真实 R2 输出，不能按数组位置、相似名字或多规格拼图猜测。已本地化图片保持同角色/来源/目标语言证据链。
- 各店实际消费的原价、币种、税费/FX 来源、折扣比例、折后价、币种最小单位及舍入接受规则；Shopee CNSC 同一 Model 的全球原价必须来自同一个冻结主价来源。缺率或冲突应在终审前一次列清，不能默认套用上一个商品。
- 已冻结的库存量及完整仓范围；没有观察到其他仓不等于其他仓为零。COMMON 全字段实际写入及官方回读完成。
- 发布后必要 companion：库存、已选折扣/活动及其范围、预算/维护策略和确认方式。应在终审前明确实际消费字段，不在发布后重新向用户做商业选择。

原生服务在准备终审和首次保存决定时消费同源预检；离线执行包还可用 `preflight --inputs <JSON> --inputs-sha256 <SHA> --task-id <TASK> [--output <BOUND_OUTPUT>]` 编译。预检报告、执行包或 checksum 都不产生人类批准。

## 批准后继续，失败进入诊断

execute 只消费原服务保存的当前决定，不能用调用方 JSON 生成批准。技术修复不变更已冻结商品/目标/商业动作时，继续同一决定；关键内容变化才产生真实候选变化问题。已接受的控制面 202 不等于发布完成；以原任务、原请求和每目标官方回读确认。三个平台可以独立发起，某平台异步等待不能阻挡其余平台首发；最终结果仍展示全部目标，不能用某一平台成功宣称全店完成。

结果 UNKNOWN 时先查原请求。入口在 POST 前留 intent、解析前保留原 HTTP bytes；变更 profile 不能绕过 UNKNOWN。显式 reconcile 仅派发原决定的只读核对，可刷新已知的旧 202；不会重新执行发布。原 provider task/item、预算和事故证据保留。凭证续期、付费和平台例外按现有有效范围处理；无新权限时列具体缺口，不把真正的新商家授权变化当技术修复跳过。

正常每件商品运行固定资料/能力预检和必要官方回读，不重跑软件发版全套。仅复现实际失败时进入独立诊断包，保留失败案例并做相应软件回归。软件验收、候选部署和实际新品结果分别记录；本页不要求重启 Windows，也不授权停启服务。

## Agent 默认读取摘要

`status`、`result`、`review` 默认只返回白名单摘要：原任务及阶段/范围、逐目标状态与下一步/阻塞、plan/candidate/review/decision 的原标识和摘要、冻结图库与规格数量。`source_stage.url` 指向完整原 GET，`read_digest` 是该次完整 JSON 的规范化 SHA256；原审核页面仍展示全部冻结事实。摘要不包含原 checkpoint、证据正文、manifest 或审核大正文，不产生批准。执行判断仍读取完整原 stage，不能拿摘要代替原决定或证明平台成功。

## 最小激活检查（未执行部署）

实际 launcher 是 [operations_web_entry.py](../scripts/operations_web_entry.py)，仅接受 `--deployment` 和 `--log`；不存在 `--status`。下列命令是未来已有明确启动授权时的入口说明，本轮未运行它，也不需要重启 Windows：

```powershell
<PYTHON> -I -B <SOURCE>/scripts/operations_web_entry.py --deployment <ABSOLUTE_DEPLOYMENT_JSON> --log <ABSOLUTE_LOG>
```

1. 在当前工单中固定干净源码 root/HEAD/manifest、Python、实际配置和既有数据角色。deployment 的 `execution_mode` 必须是 `web-only`，`code_root`/`code_version`/`manifest_digest` 必须匹配候选。正常新任务使用 stable 环境与 `native_service_scope=explicit-new-task-and-decision/v1`；原 original-prepared/adopted scope 不会开启正常新任务 worker，不能拿旧 scope 代替。
2. 核对 deployment 已存在的 `settings`、`catalog_database`、`catalog_weight_overrides`、`release_store_path`、`report_store_path`、`operations_data_root/tasks.db`、`original_profit_asset_root`、`ozon_data_root`，以及独立 `workbench_store_path`、`runtime_profile_path`、`ready_path` 和 `r3_config_root`。operations tasks.db 与旧 workbench 库不能指向同一文件。实际原生商品 state 仍在源码绑定的 `data/new_product_workbench`，R1/R2 仍在 `reports/product-preparation`；CLI profile 的 output_root 不会重定向这些生产器。保留原数据来源，不新建空库冒充迁移完成。
3. 由既有维护流程确认 ReleaseStore 中 COMMON technical、native sole-final decision、mutation budget 等必需 schema，以及 operations ledger 的版本/对象。launcher 的 `preflight_operations_schema` 和 `installed_native_services` 只接受已安装结构；本页不授权初始化、迁移或清理。固定 Agent executable、COMMON 配置与受限 native runtime 根、R2 review/release evidence 等实际部署引用，在线凭证/付费 readiness 仍须按有效范围单独核实。缺失应报告具体能力，不能宣称 REGISTERED 即全部可执行。
4. 获得真实启动授权后按同一 deployment 启动一次，再只读核对日志/ready、`/api/health` 的 root/commit/config，以及 `/api/orbit/home/runtime` 的 version/manifest/data_root；核对原任务、原生 capability、R1/R2 和最终图片实际 GET。最后才绑定 CLI profile 并使用同一 source-key 发起新任务。本段不是已部署回执，不授权抢占端口、停服务或重发 UNKNOWN。
