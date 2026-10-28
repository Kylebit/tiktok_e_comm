# OrbitHive Agent 入口

本文件是项目接手索引；规则各有唯一出处，按任务读取，不把历史副本当当前指令。

## 接管

- 核验当前工单指定的 Git top-level、branch、HEAD、`status --porcelain=v1 -uall`、负责人和允许写入范围；历史盘符、任务标题及端口只能用于查找。
- 路径均相对已核验的仓库根。保护已有 WIP 与唯一产物，核验无竞争 writer 后再编辑；职责、授权和 Git 规则以[线程治理](docs/THREAD_OPERATING_MODEL.md)为准。
- 中断或上下文压缩后执行[派单恢复段](docs/pm/DISPATCH_CONVENTION.md#resume)，不要把旧问题或摘要中的待办当成当前工单。

## 长期产品决定

- 当前任务首页统一组织并行工作，五个业务区域继续保留。任务版本、审核接续、执行器和预览隔离的唯一说明见[任务运行方式](docs/OPERATIONS_TASK_RUNTIME.md)。

- 用户当前认可的一级业务入口是：商品目录、商品上架、供应链、利润、知识工具。它们是产品信息架构，不等同于侧栏任务名、固定线程或所有后台模块；新增或恢复一级入口必须有新的明确需求决定。
- 商品目录以内部 SKU 为业务主键：同一内部 SKU 跨国家、平台只保留一条目录记录和一个当前成本。平台商品身份、国家仓库存量、逐笔订单/结算及历史成本版本仍分别保留来源，不合成无来源总数，也不把当前成本倒写成历史实际成本。
- 接手前先读本次工单引用的最新计划、验收回执和 handoff，列出“已完成、不重复”“继续”“待决定”。已由后续提交或验收关闭的旧工单只保留来源，不因旧状态、标题或历史摘要重新开工。

## 按需入口

- 需要服务时，按本次工单区分正式、私有预览与独立商品服务，依[活状态接管入口](docs/pm/ACTIVE_STATE_HANDOFF.md)只读核验实际入口、部署配置、Task、PID 和 health。正式 web-only 的 [operations_web_entry](scripts/operations_web_entry.py) 是启动入口，只有 `--deployment` / `--log`，没有 `--status`；不要为核查而运行它。[product_publication_runtime](scripts/product_publication_runtime.py) 的 `--takeover-check` / `--status` 仅用于工单明确指定的独立商品 runtime，不证明正式工作台或私有预览的身份。启动、端口和页面身份按本次工单核验，不默认已有服务属于此 HEAD，也不自动抢占或重启。
- 商品发布按当前已实现的 workflow mode、冻结快照和回执选择对应 Skill：[事实准备](skills/prepare-product-publication/SKILL.md)、[图片准备](skills/prepare-product-images/SKILL.md)、[已批准发布](skills/publish-approved-product/SKILL.md)。执行该 Skill 前读英文 `SKILL.md` 原文；中文译文和双语汇编是只读镜像，只有其 `source_sha256` 与当前英文源一致时才可辅助阅读，绝不替代执行权威。历史批准不能跨商品、阶段或目标套用。
- 仅当工单属于关闭客户端、portable 新项目或 Skill 同步时，才选[接手指南](docs/AGENT_HANDOFF.md)中的对应只读场景；普通新 agent 或领域任务直接按当前 Work Order 和派单恢复段接管。命令与依赖只见[工具入口](docs/tools/README.md)和接手指南中的关闭场景；不使用个人安装版旧 HTTP closure 文档，不默认个人安装目录。
- 其他业务按当前任务选择领域文档/Skill；不为一次接管启动全部服务、安装全部工具或读取私有配置。模板为 [settings.example.json](config/settings.example.json)，实际凭据和运行数据库不进源码或报告。

## 推进与验收

- 在系统/开发者边界内，当前用户明确要求优先于仓库默认。已授权的本地可恢复实施持续推进；批准复用、缺失证据与宿主权限依[授权规则](docs/THREAD_OPERATING_MODEL.md#authority)，不从旧 Skill 例子增加批准轮次。
- 本任务职责内的实现缺口继续修复；上游未交付则完成独立部分并报告精确缺口。外部结果未知时按[恢复规则](docs/THREAD_OPERATING_MODEL.md#recovery)对账，不重发可能已执行的请求。
- 修改保持必要范围与模块边界。按[测试治理](docs/TESTING_GOVERNANCE.md)验证：真实缺陷先失败后修复，UI 使用真实浏览器，文档核对内容/链接。未运行、skip、环境失败和线上未验证分开报告。
- 用精确文件交付；不要为工作区洁净清理唯一报告或临时测试目录。持续交付和 push 权限见[Git 规则](docs/THREAD_OPERATING_MODEL.md#git)。
- 端口、HEAD、数据库连接、凭据有效期、任务 active 图标和外部平台状态都是时点观测；引用时带 `as_of` 与证据路径，执行前现场复核。不要把旧观察写成长期现状。

## 权威索引

| 事项 | 入口 |
| --- | --- |
| 逻辑职责、负责人确定方法、授权、状态和 Git | [THREAD_OPERATING_MODEL](docs/THREAD_OPERATING_MODEL.md) |
| 本次任务的活状态、证据和接管顺序 | [ACTIVE_STATE_HANDOFF](docs/pm/ACTIVE_STATE_HANDOFF.md)（先核当前 Work Order 与现场身份） |
| 2026-09-08 的五核心与旧任务决定 | [CURRENT_WORK_INDEX](docs/pm/CURRENT_WORK_INDEX.md)（仅历史快照，不是执行队列） |
| 领域 producer/consumer 与模块边界 | [DOMAIN_OWNERSHIP](docs/DOMAIN_OWNERSHIP.md) |
| 工单、恢复 ACK 与回执字段 | [DISPATCH_CONVENTION](docs/pm/DISPATCH_CONVENTION.md) |
| 验证层级、离线预算与最终发布门禁 | [TESTING_GOVERNANCE](docs/TESTING_GOVERNANCE.md) |
| 数据库保护与目录变更原则 | [DATABASE_GOVERNANCE](docs/DATABASE_GOVERNANCE.md)、[CATALOG_UPDATE_GOVERNANCE](docs/CATALOG_UPDATE_GOVERNANCE.md) |
| 旧平台参数、历史基线与来源 | [LEGACY_PLATFORM_REFERENCE](docs/LEGACY_PLATFORM_REFERENCE.md)（不授予当前执行权） |

核心源码入口包括 [HTTP 服务](modules/products/server.py)、[共享平台](shared_platform/)、[业务领域](domains/)。存在性不等于功能就绪；实际入口与部署按当前候选检查。[部署说明](docs/DEPLOY.md)同样需要核对本次配置。
