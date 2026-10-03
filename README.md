# OrbitHive

OrbitHive 把日常运营任务放在首页，业务工作分为五个一级入口。仓库同时包含各领域代码、完整业务 Skill 和可分发的独立工具；**代码存在不代表正式服务已经启用该能力**。页面、执行器及平台结果须按当前部署身份与回执核验。

| 入口 | 页面 | 主要工作 |
| --- | --- | --- |
| 任务工作台 | `/` | 查看待办、审核接续、结果和运行状态 |
| 商品目录 | `/catalog` | 按内部 SKU 查看商品与维护一个当前成本 |
| 商品上架 | `/product-workspace` | 准备候选、查看原审核页及逐目标发布结果 |
| 供应链 | `/supply-chain/` | 库存、入库和补货 |
| 利润 | `/profit` | 结算覆盖、利润计算与月报 |
| 知识工具 | `/knowledge` | Skill、工具、方法和来源检索 |

首页是任务入口，不增加第六个业务领域。商品上架的 [R1 事实准备](skills/prepare-product-publication/SKILL.md)、[R2 图片准备](skills/prepare-product-images/SKILL.md)和 [R3 已批准发布](skills/publish-approved-product/SKILL.md)以各自英文原文为执行合同；正常流程只在冻结的最终市场发布候选上进行一次人工终审。后续技术恢复与逐平台回读复用同一精确批准，候选或目标变化才按原合同重新判定。其他业务 Skill 和工具见[能力目录](docs/tools/README.md)。

## 接手与部署

1. 从 [AGENTS.md](AGENTS.md)核验本次工单的仓库、分支、负责人和权限；状态、Git 与授权规则见[线程治理](docs/THREAD_OPERATING_MODEL.md)。
2. 理解任务、版本、审核和结果的关系时读[任务运行合同](docs/OPERATIONS_TASK_RUNTIME.md)；需要部署或切换服务时读[部署说明](docs/DEPLOY.md)。
3. 使用[能力目录](docs/tools/README.md)检查可移植工具与安装边界。完整业务 runtime、portable 包和个人 Skill 安装是不同范围。

当前维护网页服务使用固定版本和独立任务账本，部署模式为 **web-only**：页面可用于浏览及明确允许的本地操作，后台执行器未启用。它不会因页面显示任务而自动上架、下架或生成利润报告。服务端口、版本、数据库、执行器和平台结果都是时点事实，须现场 readback；不能从本文推定某个任务正在执行。

旧 `main.py serve --port 8765`、Ozon 搬运页与旧机器迁移步骤保留在[历史部署指南](docs/legacy/platform/DEPLOY_OLD_CONSOLE.md)和[历史架构](docs/ARCHITECTURE.md)，仅用于追溯，不是当前 OrbitHive 的操作入口。配置模板在 [config/settings.example.json](config/settings.example.json)；真实凭据和数据库不进入 Git 或 portable 包。
