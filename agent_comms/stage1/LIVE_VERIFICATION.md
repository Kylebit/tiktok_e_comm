# 阶段1 真机验证报告（A2A Orchestrator ↔ EigenFlux bridge）

> ORB-TASK-0033 · 阶段1 · 真机 live 探测
> 日期：2026-07-20
> 验证目标：确认 bridge 在**真实 EigenFlux 网络**上能完成 下行派发 / 上行回收 / A2A+AG-UI 转换，且不影响现有旧链路（双轨）。

---

## 1. 环境与身份（真机实测确认）

| 项 | 值 |
|---|---|
| EigenFlux CLI | `C:\Users\Windows11\AppData\Local\EigenFlux\bin\eigenflux.exe` |
| 数据目录（已登录） | `~/.eigenflux`（默认 home，含 `servers/eigenflux/credentials.json`） |
| 总控(CEO肉肉) 身份 | **Orbit Codex**，agent_id `336693310271782912` |
| 子 agent Cursor | **Orbit Cursor**，agent_id `336745353602662400` |
| Cursor 真实 DM 会话 | **conv_id `336750845745954816`** |

> ⚠️ **修正历史记录**：此前记忆里写的 Cursor conv `336761709374996480` 是错的，
> 以本次真实 `--receiver-id 336745353602662400` 发送落点 `336750845745954816` 为准。
> 另外确认：总控在 EigenFlux 上的注册身份即 "Orbit Codex"，故「Codex（指挥中心）」不是独立子 agent，
> `AGENT_ROSTER` 中已移除该条目，仅保留真实下级 agent（Cursor + Claude 占位）。

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
- `bridge.py`：`AGENT_ROSTER` 修正 Cursor 真实 conv=`336750845745954816`，移除 Codex 条目并加身份注释
- `run_stage1_demo.py`：`--live` 改为给真实子 agent **Cursor** 发（非 Codex），支持 `--no-send` 复用会话

---

## 4. 结论

✅ **阶段1 bridge 在真实 EigenFlux 网络上三通道全通**：下行派发、上行读取、A2A/AG-UI 转换。
✅ **当前 EigenFlux 旧链路（监听器/飞书卡/巡检）零改动、双轨保留**。
⏳ **待 Cursor 自然回复 PONG-OK 后**，bridge 自动把该回报转 A2A 事件回灌 Orchestrator（转换逻辑已在本验证中跑通，仅缺一次真实子 agent 回复触发）。

> 说明：验证期间向 Cursor 会话发送了多条「低风险 ping」（均标注无需执行任务、回 PONG-OK 即可），
> 仅为验证框架收发，不会触发 Cursor 执行任何业务动作。
