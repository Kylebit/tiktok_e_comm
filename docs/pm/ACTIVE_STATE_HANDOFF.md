# OrbitHive 活状态接管入口

本页只定义**如何核验**，不保存当前 HEAD、端口、会话 owner 或业务执行状态。项目文件、长期记忆、浏览器标签和协调表都有各自时点；只有本次有效 Work Order、后继回执和现场 readback 能支持当前结论。授权和状态定义见[线程治理](../THREAD_OPERATING_MODEL.md)，回执字段见[派单约定](DISPATCH_CONVENTION.md#resume)。

1. 从本次消息/工单取 `work_order_id`、revision、owner、scope、base、`current_state_source`、last receipt 与 `next_action`。按 `current_state_source` 找到当期计划或状态表，先读其顶端的 `as_of`、supersedes、后继回执引用；不从旧表的一行 `RUNNING`、固定侧栏角色或会话图标恢复任务。若没有有效工单或状态源，先只读核对协调者的最新交接，不自行认领外部写入。
2. 在工单指定 checkout 中核 `git rev-parse --show-toplevel`、branch、HEAD、`git status --porcelain=v1 -uall`，与工单 base 和允许写入范围对齐。仓库根可能在不同磁盘和隔离工作树；旧 C/D 路径只能帮忙定位，不能替代这一步。保全已有 WIP，不因 checkout 名称推断 canonical。
3. 仅在任务需要服务时，先从工单与部署回执确认服务种类，再只读核其实际入口、配置、PID、代码及 health/readback：
   - 正式 web-only：核 Windows Task 的实际动作、进程出生时间与命令行，以及该动作指定的部署配置和日志。[operations_web_entry](../../scripts/operations_web_entry.py) 只接受必需的 `--deployment` / `--log`，核对配置的 `execution_mode=web-only`、`code_root`、端口与业务/worker 开关；它是启动入口，没有状态查询参数，不为核查而运行。当前服务身份以这组事实及当期部署回执为准。
   - 私有预览：核本次预览包的冻结入口、独立配置、私有数据路径、端口及进程归属，不能套用正式 Task 或独立商品 runtime 的状态结果。
   - 独立商品 runtime：仅在工单明确指定时使用 [product_publication_runtime](../../scripts/product_publication_runtime.py) 的 `--takeover-check` / `--status`，先核实际 profile。其默认商品/可选 Ozon 服务配置不代表正式工作台或预览入口。
   源码提交、`READY`、页面 HTTP 200 与真实平台结果分开记；查询结果只证明实际检查的字段。不把曾用过的端口、路径或 Task 名写成永久入口，不自动启动、抢占或重启服务。
4. 对比本次范围的最新具名 source commit、独立复审、组合回执、正式部署回执和 provider/readback。输出三栏：**已完成且不重复、继续、待决定/证据缺口**。若旧状态表与后继回执冲突，以精确后继证据及现场核验为准，保留旧表作追溯；若两者都不足，标未知，不替任何 agent 声称在运行。
5. 按[派单恢复段](DISPATCH_CONVENTION.md#resume)回 ACK 并做首个实际动作。批准沿用须逐项核 actor、对象、阶段、目标、动作、digest、预算与撤销条件；技术参数和项目文字不生成新的业务批准。交付时回精确提交/测试/未覆盖/下一 owner，不能用任务结束或心跳代替回执。

`CURRENT_WORK_INDEX.md` 是 **2026-09-08 历史快照**，不再承担当期五核心进度入口。`WORK_PLAN.md`、`PLATFORM_STATUS_20260924.md` 与其他带日期的表也只能在其 `as_of` 和后继链范围内使用。2026-09-27 的 canonical/正式服务结论须另核，不固化在本页。逻辑 00–05 是职责域，实际执行者由本次有效工单指定，不永久对应六个会话。
