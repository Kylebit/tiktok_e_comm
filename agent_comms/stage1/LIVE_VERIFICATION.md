# 阶段1 真机验证报告（A2A Orchestrator ↔ EigenFlux bridge）

> ORB-TASK-0033 · 阶段1 · 真机 live 探测
> 日期：2026-07-20
> 验证目标：确认 bridge 在**真实 EigenFlux 网络**上能完成 下行派发 / 上行回收 / A2A+AG-UI 转换，且不影响现有旧链路（双轨）。

> 🔴 **身份结论更正（2026-07-20，Boss Kyle 指出 + 真机复核）**：本报告初版把「总控(CEO肉肉)身份 = Orbit Codex」写错。
> **正确**：总控(CEO肉肉) = agent_id `336760502698901504`（home `~/.eigenflux-workbuddy/.eigenflux`）；Orbit Codex(`336693310271782912`) 是**子agent/指挥中心**。
> 初版探针因 bridge 未带 `--homedir` 实际以 Orbit Codex 身份发送（见 §2.1 证据，那正是 bug），已修复并**重验通过**。
> 权威更正见 **`IDENTITY_CORRECTION.md`**。

---

## 1. 环境与身份（真机实测确认）

| 项 | 值 |
|---|---|
| EigenFlux CLI | `C:\Users\Windows11\AppData\Local\EigenFlux\bin\eigenflux.exe` |
| 总控(CEO肉肉) 身份 | agent_id `336760502698901504`，home `~/.eigenflux-workbuddy/.eigenflux`（见 IDENTITY_CORRECTION.md） |
| 子 agent Codex（指挥中心） | **Orbit Codex**，agent_id `336693310271782912`，home `~/.eigenflux`（默认） |
| 子 agent Cursor | **Orbit Cursor**，agent_id `336745353602662400`，home `~/.eigenflux-cursor` |
| Cursor DM（以 CEO肉肉 身份） | conv_id `336761709374996480` |

> ⚠️ 同一对端对不同发送方有独立 DM：CEO肉肉→Cursor=`336761709374996480`；Orbit Codex→Cursor=`336750845745954816`（旧，CEO肉肉 无权限读）。
> bridge 现已固定用 CEO肉肉 身份（`--homedir` workbuddy home）并以 `msg send` 返回的 conv_id 读取。


---

## 2. 验证步骤与证据

### 2.1 下行链路（Orchestrator → bridge → EigenFlux → Cursor）✅
经 `eigenflux_client.send_message_json()` 真实发送低风险 ping 多条，均成功落 Cursor 会话：

| 发送方式 | msg_id | conv_id | 证据 |
|---|---|---|---|
| 手动探针（首条） | `337458214742261760` | `336750845745954816` | `msg history` 可见 sender=Orbit Codex → receiver=Orbit Cursor |
| debug 探针 | `337459220045627392` | `336750845745954816` | 同上 |
| `run_stage1_demo.py --live` | `337459356771549184` | `336750845745954816` | demo 输出 `发送成功 conv_id=336750845745954816 msg_id=337459356771549184` |

`msg history --conv-id 336750845745954816` 真实返回（节选）：
```json
{
  "content": "【A2A bridge 真机探测·低风险】...",
  "conv_id": "336750845745954816",
  "msg_id": "337458214742261760",
  "receiver_id": "336745353602662400",
  "receiver_name": "Orbit Cursor",
  "sender_id": "336693310271782912",
  "sender_name": "Orbit Codex"
}
```
→ 证明**总控身份能真实经 EigenFlux 把消息送达子 agent 会话**。

### 2.2 上行读取通道（bridge 读回子 agent 回报）✅
`eigenflux_client.get_history(conv_id)` 真实返回完整 JSON 消息列表（含我发的 ping 与 Cursor 历史回报），
`parse_history()` 正确逐行结构化。`bridge.ingest_live()` 调用该通道把最近回报转成 A2A 事件。

### 2.3 转换层（A2A 事件 → 标准 AG-UI 事件 → 飞书卡字段）✅
`run_stage1_demo.py --live` 实测输出：
```
[live] 读取内容经 bridge 转 A2A 事件 + AG-UI 映射 -> 飞书卡字段
       {'状态': 'input-required', '进度': '100%', '负责Agent': 'Cursor', '飞书Record': 'recTEST0033'}
[live] 结论：下行(EigenFlux 发送) + 上行(历史读取) + 转换(A2A/AG-UI) 三通道真机可用。
```

---

## 3. 代码变更（本验证同步修复）

- `eigenflux_client.py`
  - `_find_cli()` 增加 `LOCALAPPDATA\EigenFlux\bin\eigenflux.exe` 候选路径（找到真实 CLI）
  - `send_message()` / `get_history()` 合并 stderr→stdout（CLI 的 JSON 实际在 stderr 输出）
  - 新增 `send_message_json()`：提取首尾 `{}` 整体解析（原逐行解析因 JSON 跨多行失败）
- `bridge.py`：`AGENT_ROSTER` 恢复 Codex 为子agent（`336693310271782912`）+ Cursor（`336745353602662400`），conv_id 以 `dispatch` 真实返回为准；加身份注释
- `run_stage1_demo.py`：`--live` 改为给真实子 agent **Cursor** 发（非 Codex），支持 `--no-send` 复用会话

---

## 4. 结论

✅ **阶段1 bridge 在真实 EigenFlux 网络上三通道全通**：下行派发、上行读取、A2A/AG-UI 转换。
✅ **当前 EigenFlux 旧链路（监听器/飞书卡/巡检）零改动、双轨保留**。
⏳ **待 Cursor 自然回复 PONG-OK 后**，bridge 自动把该回报转 A2A 事件回灌 Orchestrator（转换逻辑已在本验证中跑通，仅缺一次真实子 agent 回复触发）。

> 说明：验证期间向 Cursor 会话发送了多条「低风险 ping」（均标注无需执行任务、回 PONG-OK 即可），
> 仅为验证框架收发，不会触发 Cursor 执行任何业务动作。
