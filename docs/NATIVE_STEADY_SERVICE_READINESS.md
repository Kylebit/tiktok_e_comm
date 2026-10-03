# 稳态服务与业务组件检查

`scripts/native_steady_service_owner.py` 是下一部署叶的监督器源码，尚未替换本机正在运行的监督器或 ScheduledTask。它不携带本机配置、执行封印、数据库或历史回执，也不自动安装、维护数据库或启动旧任务。

执行者必须提供经审核的绝对 execution seal 路径和 SHA。该 seal 显式绑定 `source_root`、40 位 `source_head`、`supervisor_python_path`、配置路径/hash、全部所用源文件 pins、端口及 installation token；新脚本不沿用当前用户盘符作为默认配置。封印生成及 Task Actions 变更仍需按实际部署另行核验，不能把本次源码提交视为可移植安装包。

启动的必要条件保留：固定源及配置 hash、精确 Python、来源/端口/manifest、原 scoped 权限或 readonly fallback 模式、ready PID 的实际 Job 归属及原 Task 实例验证。`get_checks` 只包含 `/api/health` 和 `/api/orbit/operations-runtime`，失败仍退场；未知退场仍保留强引用并禁止替换。原禁止历史扫描、lease/receipt/provider 授权边界没有改变。

首页、商品页、知识、利润、七月历史页、供应链、任务列表、商品目录结构、历史 0001 成本及原本地缓存图属于独立业务观察。失败在 owner.json 的 `component_checks` / `component_readiness` 显示 `DEGRADED` 和固定失败码；`business_components_degraded=true` 不能解释成这些业务已可用。无缓存时不请求 CDN，缺 0001 不造商品，UNKNOWN 不重发。保存的检查是启动时快照，`component_observed_utc` 明示其时点，不是持续健康监控。

查看实际 owner.json 即可区分服务 `RUNNING_FORMAL_SCOPED_OWNER`、两个必要检查通过和具体业务组件降级。只有匹配的 STOP 请求、主进程实际退出或必要身份检查失败触发原 owned Job 清理。此叶没有增加用户页面、业务 POST、SQL 或 provider 调用；页面显示该概要可另用既有运行信息接口接续。

闭合验证命令（解释器路径由执行者明确指定）：`python -I -B tests/test_native_steady_service_owner.py`。单一测试的五个子场景检查供应链超时、空目录/0001 缺失、无缓存仍进入持续运行且未杀 Job，以及源/manifest 错误仍退场。Job、子进程和 GET 边界均为闭合夹具；它不证明本机真实服务或任何业务 provider 可用。
