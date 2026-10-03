# OrbitHive 部署与切换入口

本页只说明**现行封存部署的核验顺序**。正式端口、PID、代码版本、数据绑定和可执行能力必须按本次工单现场确认；历史控制台迁移步骤在[旧部署指南](legacy/platform/DEPLOY_OLD_CONSOLE.md)，不得直接套用。接手任务先读[项目索引](../AGENTS.md)与[线程治理](THREAD_OPERATING_MODEL.md)，部署工单再读[任务运行合同](OPERATIONS_TASK_RUNTIME.md)和[数据库治理](DATABASE_GOVERNANCE.md)。

## 组成与边界

- 商品等业务代码及原审核页在本仓库；任务首页只是统一入口。五个一级业务页面及 Skill 入口见[根 README](../README.md)。
- 封存网页服务由 `scripts/operations_web_entry.py --deployment <绝对部署 JSON> --log <绝对日志路径>` 启动。入口要求部署文件的 `execution_mode=web-only` 且 `code_root` 与入口所在工作树一致；它不会启动任务 worker。`scripts/start_operations_web.py` 调用同一服务装配，适合受控本地检查，不绕过部署预检。
- 部署 JSON 明确绑定代码 HEAD、manifest、配置、商品库、发布/报告库、历史利润资产、Ozon 数据、审核来源及独立的 operations 账本。`shared_platform/operations_launch.py::preflight_deployment` 检查代码根、干净工作树、绝对路径和已有 `tasks.db`；不创建空账本替代原数据。
- web-only 稳定模式只允许服务端白名单内的本地 POST；预览模式有独立端口、独立 operations 数据根且禁止所有 POST。它们都不能证明 worker、平台写入或财务任务已运行。

部署配置与实际机器路径可能包含业务资料，本仓库不提供可直接运行的正式部署 JSON。`config/settings.example.json` 只是无密钥模板；不得因源码工作树没有 `config/settings.json` 就改用空库、复制生产凭据或初始化新库。运营数据与批准回执按本次部署文件的原绑定核验。

## 受控升级顺序

1. 核验本次工单授权、精确 Git 根/HEAD/状态、现有服务的 PID/端口与 `/api/health`、`/api/orbit/operations-runtime` 回执；把观测时间和证据路径记录下来。不要用旧任务标题或某个端口推断当前版本。
2. 在隔离工作树、端口和任务账本快照上检查候选配置与代码指纹。运行适用的测试和真实浏览器验收；预览不能绑定正式 operations 根或真实执行器。检查五个业务页面、原商品审核页、任务/事件账本指纹与商品库来源。
3. 若计划切换正式网页服务，先对原业务库与任务账本做可验证备份，再明确新旧部署文件、回退版本、计划任务归属及单监听端口切换窗口。保持旧服务与数据可恢复；不得在预览成功后直接覆盖正式目录或抢占端口。
4. 切换后从新进程回读版本、manifest、路径、worker 状态、允许的本地操作和页面，再与切换前基线对账。页面 200、测试通过和计划任务“运行中”都不证明经营任务或外部平台动作完成。

Windows 计划任务 `OrbitHive-Operations-Web` 曾用于登录后托管固定版本网页服务；它的当前定义、触发器、动作和实际进程须现场查询。历史记录中的端口 `49289` 只是维护部署线索，不是本页承诺的目标端口。**正式服务切换、端口占用处理、数据迁移或 worker 激活是单独的部署决定**，本文不授权自动执行。

## Worker 与历史业务流程

`scripts/worker_admission_preflight.py` 只读核验封存的 worker-only 候选、CLI 文件、账本与未结域动作；其 `ok` 不等于 worker 启动。`scripts/operations_worker_entry.py --run` 目前明确拒绝激活。上架、下架、利润模板的未完成接线与旧任务准入门禁见[任务运行合同](OPERATIONS_TASK_RUNTIME.md)；不要用任务按钮、网页 POST 或修改 JSON 绕过批准和逐目标回读。

商品发布仍按[R1](../skills/prepare-product-publication/SKILL.md)、[R2](../skills/prepare-product-images/SKILL.md)与[R3](../skills/publish-approved-product/SKILL.md)原文及冻结候选执行：R1/R2 属于准备阶段，唯一正常人工审核是最终市场发布候选；已批准候选的技术恢复不重复索要同范围批准。旧 `main.py init/auth/serve`、8765 和 Ozon 兄弟目录桥接保存在[历史部署指南](legacy/platform/DEPLOY_OLD_CONSOLE.md)；旧通知与妙手 UI 指令另见[历史通知资料](legacy/notifications/README.md)和[历史平台参考](LEGACY_PLATFORM_REFERENCE.md)。当前发布协议使用已审 Skill/API adapter 与正式回读，历史步骤不能授予执行权。

实际部署仍须由运营者确定**候选版本、切换时机、稳定业务数据绑定、备份与回退点**；本文没有这些现场值，也不会猜测。配置中若有真实凭据、店铺资料或原始回包，勿提交、打包或复制进报告。
