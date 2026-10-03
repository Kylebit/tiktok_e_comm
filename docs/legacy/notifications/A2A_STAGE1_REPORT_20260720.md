# 阶段 1 报告：A2A Orchestrator + a2a-eigenflux bridge（双轨并行，mock 验证通过）

> ORB-TASK-0033 · 阶段 1 · 2026-07-20
> 代码：`tiktok_e_comm/agent_comms/stage1/`
> 提交：`1954a6b`（快进 `a0daa89..1954a6b`，推 `origin/master`）

## 1. 本阶段交付

| 文件 | 职责 |
|---|---|
| `orchestrator.py` | **A2A Orchestrator（总控侧 A2A-native 协调层）**。Task 状态机（submitted→working→input-required→completed/failed/canceled/blocked）；`taskId` ↔ 飞书主任务表 `record` 映射；JSON 持久化；A2A 兼容 HTTP（端口 **8770**，Agent Card + `message/stream` SSE + `GET /tasks/{id}`）。线格式与 a2a-sdk 1.1.1 JSON schema 完全一致。 |
| `bridge.py` | **a2a-eigenflux bridge（transport adapter）**。`dispatch()` 把 A2A Task 翻译成 EigenFlux 消息（含派发三约定 preamble）发给子 agent；`ingest_mock()` 模拟子 agent 两段回报转 A2A 事件；`ingest_live()` 读真实 EigenFlux 历史转 A2A 事件。花名册复用既有 Codex/Cursor conv/agent_id。 |
| `eigenflux_client.py` | EigenFlux CLI 封装。**延迟解析 CLI 路径**（import 时不触发，无 CLI 环境 mock 不崩）。 |
| `run_stage1_demo.py` | mock 端到端演示 + 启动 Orchestrator HTTP 验证 A2A 暴露。 |

## 2. 实测结果（mock 模式，exit 0）

```
[1] 创建 A2A Task: task-1a9308d025  (关联飞书 record=recTEST0033, 派发对象=Cursor)
[2] bridge 派发(mock): 已标记 working（未真发 EigenFlux，安全联调）
[3] 子 agent 经 bridge 回报两段 -> state=input-required, 末条=处理完成，提交待审核
[4] 同一条回报映射为 4 个标准 AG-UI 事件；飞书卡字段 -> {状态:input-required, 进度:100%, 负责Agent:Cursor, 飞书Record:recTEST0033}
[5] Orchestrator HTTP(8770) 暴露验证：
    Agent Card: Orbit Hive 总控 Orchestrator (A2A Stage1) (streaming=True, transport=transport adapter: EigenFlux)
    GET /tasks/task-1a9308d025 -> state=input-required, feishu=recTEST0033
```

**闭环已证**：创建 Task → 派发 → 子 agent 回报 → 状态机推进 → AG-UI 标准事件 → 飞书卡字段刷新 → Orchestrator 对外 A2A 暴露，五环全通。当前 EigenFlux 旧链路**零触碰**（双轨保留）。

## 3. 真机 live 探测（待 Boss 协助）

`run_stage1_demo.py --live` 已写好：经 bridge 给 **Codex** 发低风险 ping（"联调测试，请回 pong 即可"），读 EigenFlux 历史转 A2A 事件回灌 Orchestrator。但本环境**无法实跑**：

- 当前 WorkBuddy Bash 环境的 `PATH` 无 `eigenflux` CLI（之前对话里的 CLI 调用依赖另一套环境）。
- EigenFlux 是远程服务：`https://www.eigenflux.ai` + `wss://stream.eigenflux.ai`，**需认证**；本地 `config.json` 仅含 endpoint，无 token。

**需 Boss 协助任一**：
1. 提供 `eigenflux` CLI 的可执行路径（或确保 `eigenflux` 命令在本 Bash 可达）；
2. 提供 EigenFlux 认证方式（token / 登录态）；
3. 或直接确认"可以真发低风险 ping"，我据此选择可行路径。

获得后即可一条命令跑通真机验证，闭环从 mock 升级为真实收发。

## 4. 下一步（不阻塞，继续推进）

- **阶段 2**：飞书卡后端改由 AG-UI 事件流驱动（Orchestrator emit 标准 AG-UI 事件 → 飞书卡刷新），替代当前轮询/静默刷卡。
- **阶段 3**：稳定 ≥2 周后收敛下线旧链路（或留作降级）。

## 5. 双轨安全边界

- 新系统全部在 `agent_comms/`（a2a_poc / stage1），独立端口（8769/8770），与现有 EigenFlux 监听器 / 飞书卡 / 巡检脚本完全隔离。
- 旧链路继续作为生产主用；新链路验证期并行，出问题随时切回。
