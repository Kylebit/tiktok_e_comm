# 阶段2 报告：AG-UI 事件流驱动飞书卡（Boss 视图层）

> ORB-TASK-0033 · 阶段2 · 双轨并行（不替换旧链路）
> 日期：2026-07-20

## 目标
把「飞书卡后端」从旧链的 **「轮询 EigenFlux 消息 → 静默刷飞书卡」** 模型，
改为 **「订阅 AG-UI 标准事件流 → 直接推送飞书卡」** 模型。

- agent↔agent 层 = A2A（阶段1 已落地 Orchestrator + bridge）
- agent↔Boss 层 = AG-UI（本阶段落地：事件流驱动飞书卡）

## 双轨说明（不替换旧链路）
| 链路 | 机制 | 状态 |
|---|---|---|
| 旧 | `eigenflux_stream_listener.py` 轮询 EigenFlux → 静默刷飞书卡/推战情室 | **保留运行** |
| 新 | `ag_ui_feishu_adapter.py` 订阅 AG-UI 事件流 → 直接推送飞书卡 | **本阶段新增** |

两层并行，互不影响；新链路验证稳定后再考虑收敛（阶段3）。

## 实现
### `ag_ui_feishu_adapter.py`
- `EventBus`：AG-UI 事件流的本地发布/订阅（极简 pub/sub）
- `AgUiFeishuAdapter`：订阅 AG-UI 事件，把事件翻译成飞书卡字段更新
  - `RUN_STARTED` / `RUN_FINISHED` / `RUN_ERROR` → 运行生命周期标记
  - `TEXT_MESSAGE_CONTENT` → 追加进展（mock 打印 / `--live` 真写飞书主任务表）
  - `TOOL_CALL_START/END` → 标注子 agent 工具动作（实时可见）
  - `STATE_DELTA`（RFC 6902 JSON Patch 增量）→ **刷新 card_state**（飞书卡唯一事实来源）
  - 每次 `process_a2a_update` 末尾推送一张合并卡（`--live`），避免每个事件刷屏
- `card_state` 由 STATE_DELTA 增量维护，`render_card()` 据其渲染 Card 2.0
- `replay_from_orchestrator(task_id)`：从 Orchestrator 历史重建 card_state，**证明断线重连可重建视图**

### `run_stage2_demo.py`
模拟完整任务流：创建 Task → 派发 → Cursor 抓取（工具调用）→ 产物完成（待审核），
经 `adapter.process_a2a_update()` 触发 AG-UI 事件流驱动飞书卡。
- 默认 `mock`（打印事件流 + 卡片字段）
- `--live` 真实推送飞书卡到战情室
- `--live --live-eigenflux` 额外经 stage1 bridge 真发 EigenFlux 派发

## 验证结果
### mock 闭环（exit 0）
- 派发 → 状态`进行中`/进度`10%`/负责Agent`Cursor`
- 抓取（🔧 scrape_1688）→ `进行中`/`60%`
- 产物完成 → `待审核`/`100%`
- 收到 12 个标准 AG-UI 事件（RUN_STARTED/TEXT_MESSAGE_CONTENT/STATE_DELTA/TOOL_CALL_*/RUN_FINISHED）
- STATE_DELTA patch 正确刷新字段；断线重连从 Orchestrator 历史重建成功
- Orchestrator 内部 `a2a_state=input-required` → Boss 视图`待审核`

### live 真实推送（exit 0）
- 真实推送飞书卡到战情室成功（`ok:true`，探针卡 `message_id om_x100b6aef2a713ca4df67a831f5c51ad`）
- 验证「AG-UI 事件流 → 真实飞书卡」全链路可达

## 附带修复（stage1 代码）
`orchestrator.apply_a2a_event` 对「高层 str status」兼容分支有 bug：
在 `status` 为 str 时先按 dict 取值崩 `AttributeError`。已修正为 dict/str 双分支判断，
使 A2A 事件线格式与高层 update 格式都能正确推进状态机。

## 设计要点（对齐迁移计划 §5.2）
- emit/consume 均为 **标准 AG-UI 协议事件**（spec-compliant），**不引 CopilotKit React**
- 飞书卡字段由 STATE_DELTA 增量驱动 → 断线重连也能重建（对比旧链路的「整段重查飞书行」）
- 复用 `task_card.push_card` / `fb_progress.append_progress` 真实推送（--live）

## 下一步
- **阶段3**：稳定 ≥2 周后，将旧 `eigenflux_stream_listener` 收敛下线（或留作降级），
  让飞书卡完全由 AG-UI 事件流驱动；Orchestrator 可用 SSE/WebSocket 直推事件给本适配器（替代监听轮询）。
- 映射层后续可优化 run 生命周期（单个 RUN_STARTED/RUN_FINISHED 包裹一次任务流，而非每子更新一组）。

## 交付
- `agent_comms/stage2/ag_ui_feishu_adapter.py`
- `agent_comms/stage2/run_stage2_demo.py`
- `agent_comms/stage2/STAGE2_REPORT.md`
- `agent_comms/stage1/orchestrator.py`（bug fix：str status 兼容）
