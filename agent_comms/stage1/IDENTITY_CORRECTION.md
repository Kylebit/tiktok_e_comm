# 身份纠正记录（2026-07-20，Boss Kyle 指出 + 真机复核）

## 错误结论（初版 live 验证时误判）
- 误称「总控(CEO肉肉)在 EigenFlux 的身份 = Orbit Codex，agent_id 336693310271782912」，并据此认为「Codex（指挥中心）不是独立子 agent」。
- 误把 Cursor 与总控的 DM 会话记成 `336750845745954816`。

## 根因
系统里存在 **4 个独立 EigenFlux home**，每个 agent 一套数据目录、各自登录身份；身份由「用哪个 home 的凭证」决定：

| home 目录 | agent_id | 邮箱 | 身份 |
|---|---|---|---|
| `~/.eigenflux-workbuddy/.eigenflux` | `336760502698901504` | kylewangluck6@126.com | **总控 = CEO肉肉** ✅ |
| `~/.eigenflux` (默认) | `336693310271782912` | 1017387507@qq.com | Orbit Codex（子agent/指挥中心） |
| `~/.eigenflux-cursor` | `336745353602662400` | kylewangluck6@163.com | Orbit Cursor（子agent） |
| `~/.eigenflux-codex` | `336693310271782912` | （同 Codex） | Codex 子agent 专用 home（当前未登录） |

bridge 初版调 CLI 时**未带 `--homedir`**，默认落到 `~/.eigenflux`（= Orbit Codex 子agent），
所以是以「子agent」身份在发——这就是误判来源。

## 正确结论（权威）
- **总控(CEO肉肉) = agent_id `336760502698901504`**，数据目录 `~/.eigenflux-workbuddy/.eigenflux`（生产监听器 `eigenflux_stream_listener.py` 同此 home）。
- **Orbit Codex (`336693310271782912`) 是真正的下级子 agent（指挥中心）**，不是总控。
- **同一对端对不同发送方有独立 DM 会话**：
  - CEO肉肉 → Cursor：`336761709374996480`（以 CEO肉肉 身份发送产生）
  - Orbit Codex → Cursor：`336750845745954816`（旧，以 Codex 身份发送产生，CEO肉肉 无权限读）

## 代码修正（已提交）
- `eigenflux_client.py`：固定 `HOME = ~/.eigenflux-workbuddy/.eigenflux`，所有 CLI 调用带 `--homedir`；env `EIGENFLUX_HOME` 可覆盖。
- `bridge.py`：`dispatch(live=True)` 返回真实 `conv_id`；`ingest_live(task_id, conv_id=...)` 优先用发送产生的 conv_id；`AGENT_ROSTER` 恢复 Codex 为子agent。
- `run_stage1_demo.py`：`--live` 显式校验会话参与者含 `336760502698901504`，三通道真机验证通过。

## 真机复核结果
`run_stage1_demo.py --live` 末次输出：
```
[live] 会话参与者 sender_ids={'336745353602662400', '336760502698901504'}  （期望含 336760502698901504=CEO肉肉）-> ✅ 身份正确
[live] 结论：下行(CEO肉肉 发送) + 上行(历史读取) + 转换(A2A/AG-UI) 三通道真机可用。
```
