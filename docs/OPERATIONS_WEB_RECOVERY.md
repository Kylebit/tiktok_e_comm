# Orbit 维护网页启动

`python scripts/start_operations_web.py --deployment <显式部署JSON>` 只恢复维护网页，默认和唯一模式为 `web-only`。部署必须绑定本源码根、精确Git HEAD和RuntimeProfile manifest，工作树须干净。需要现存catalog、report、release、operations/tasks.db及相关资产目录；缺失operations库拒绝启动，不创建空历史库。

此入口不要求Codex CLI，不组合平台执行器，不启动任务worker，明确关闭Shopee recovery。页面可以查看已有内容；只允许本机同源 `POST /api/catalog/cost` 与 `POST /api/orbit/tasks`。发布、同步、旧任务重试/输入/取消等POST明确拒绝。新任务仅入本地队列，不自动执行。其余HTTP方法没有新增写能力。

旧 `/api/workbench/*` 规划接口在维护模式明确不可用。其独立旧库不允许别名指向operations/tasks.db，也不因启动而创建缺失旧库。

`/api/orbit/operations-runtime` 和ready回执声明worker_enabled=false与execution_mode=web-only，不把旧executor租约误报为正在执行。维护模式不代表当前发布Skill分支已经统一或业务发布验收完成。

后台执行恢复属于后续精确任务：先对账inflight，再使用显式可信的绝对agent路径。`operations_service.get_runtime(..., worker_enabled=True)`在构造任务引擎前拒绝缺失/相对/不存在的agent路径；不会搜索PATH，也不会因环境中残留旧路径在维护模式启动agent。此维护启动器不提供启用worker开关。

部署JSON和ready/profile必须用新路径，保留旧部署回执。该文档不允许覆盖旧profile、清空队列、重试未知平台请求或更改业务审核合同。
