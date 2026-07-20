# ORB-TASK-0033 阶段 0：A2A + AG-UI PoC 验证报告

- 日期：2026-07-20
- 负责人：CEO肉肉（总控）
- 决策背景：Boss Kyle 于 2026-07-20 确认采用推荐方案（迁移计划 §5）：**A2A 管 agent↔agent（总控 Orchestrator 为 A2A-native，EigenFlux/飞书作 transport adapter）；AG-UI 管 agent↔Boss（总控 emit 标准 AG-UI 事件，飞书卡后端消费）**。并要求**当前 EigenFlux 系统全程保留，双轨并行，待新系统搭好再决定是否替换**。
- 阶段 0 目标：纯 PoC，零生产风险，验证「A2A 传输契约（Agent Card + JSON-RPC `message/stream` SSE + TaskStatusUpdateEvent / TaskArtifactUpdateEvent 线格式）在总控侧可端到端跑通」，并验证「A2A 更新 → 标准 AG-UI 事件 → 飞书卡字段」映射可行。

## 1. 交付物（均在 `tiktok_e_comm/agent_comms/a2a_poc/`，与当前系统完全隔离）
| 文件 | 作用 |
|---|---|
| `server.py` | 最小 A2A 兼容 server（端口 8769）：GET `/.well-known/agent.json` 暴露 Agent Card；POST `/` 实现 `message/stream`（SSE 流式）与 `message/send`（非流式），依次推送 `TaskStatusUpdateEvent` + `TaskArtifactUpdateEvent`。 |
| `client.py` | A2A client 验证脚本：拉 Agent Card → 调 `message/stream` 消费 SSE → 把回报交给 AG-UI 映射层。 |
| `ag_ui_mapping.py` | 薄映射层：A2A 更新 → 标准 AG-UI 事件（RUN_STARTED / TEXT_MESSAGE_CONTENT / TOOL_CALL_* / STATE_DELTA / RUN_FINISHED），并演示 `STATE_DELTA`（RFC 6902 JSON Patch）如何刷新飞书卡字段。事件严格走 AG-UI spec，**不引 CopilotKit React**。 |
| `venv/` | 隔离 Python 3.13 venv（已 gitignore），装 `a2a-sdk==1.1.1` + `fastapi` + `uvicorn` + `sse-starlette`。 |
| `.gitignore` | 忽略 venv / `__pycache__` / `*.log`。 |

## 2. 如何运行
```bash
cd tiktok_e_comm/agent_comms/a2a_poc
venv/Scripts/python.exe server.py          # 后台常驻，端口 8769
venv/Scripts/python.exe client.py          # 另开终端，跑端到端验证
```

## 3. 验证结果（实测输出，exit 0）
```
Agent Card: Orbit Hive 总控 (A2A PoC)  (streaming=True)
Skills: ['echo-task']

>>> 调用 message/stream（SSE 流式）...
    <- A2A 事件: working | {"taskId":"task-...","contextId":"...","status":{"state":"working",...}}
    <- A2A 事件: artifact:ack | {"taskId":"...","artifact":{"artifactId":"...-art","name":"ack",...}}

>>> 端到端收到 2 个 A2A 事件（状态 + 产物），SSE 流式 OK
>>> 同一回报再映射成 4 个标准 AG-UI 事件；飞书卡字段 -> {'状态':'已完成','进度':'100%','负责Agent':'总控 A2A'}
```
- ✅ Agent Card 发现可用
- ✅ `message/stream` SSE 流式端到端跑通（状态事件 + 产物事件）
- ✅ A2A 更新 → 标准 AG-UI 事件 → 飞书卡字段 衔接可行，事件为 spec-compliant JSON

## 4. 关键结论
1. **架构可行**：A2A（传输契约）+ AG-UI（Boss 视图）在总控侧可独立跑通；bridge 适配器（EigenFlux 作其中一个 transport adapter）是官方推荐形态，不是妥协。
2. **当前系统零影响**：PoC 用独立端口（8769）与独立目录，未触碰任何 EigenFlux 监听 / 飞书卡 / 巡检脚本——符合「保留当前系统、双轨并行」要求。
3. **a2a-sdk 1.1.1 实测笔记**：库已安装且为 Linux Foundation 官方 A2A 实现；其事件类型为 protobuf 生成类、构造较脆。PoC 用「与 a2a-sdk `TaskStatusUpdateEvent`/`TaskArtifactUpdateEvent` 线格式一致」的等价 JSON 实现，专注验证传输契约。**生产阶段 1 将改为使用 a2a-sdk 官方 `AgentExecutor` / `DefaultRequestHandler` 脚手架**（线格式不变），并把 EigenFlux 收口为 `a2a-eigenflux bridge` 适配器。

## 5. 下一步（双轨，可回滚）
- **阶段 1**：总控实现 A2A Orchestrator（每任务=一个 A2A Task，taskId ↔ 飞书主任务表 record）；实现 `a2a-eigenflux bridge`（A2A Task ⇄ EigenFlux 消息互译），与现有 EigenFlux 链路双轨并行。
- **阶段 2**：飞书卡后端改由 AG-UI 事件流驱动（STATE_DELTA 实时刷新字段），Boss 审核回传走 AG-UI human-in-the-loop。
- **阶段 3**：稳定 ≥2 周后收敛下线旧链路（或留作降级）。

> 迁移总计划见 `Agent_PR/A2A_AGUI_MIGRATION_PLAN.md`。
