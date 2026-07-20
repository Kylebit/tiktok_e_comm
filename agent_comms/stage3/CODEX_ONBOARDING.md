# Orbit Codex 接入新 Frame 的说明（致 Orbit Codex）

> 这份说明由 CEO肉肉（总控）生成，请 Boss Kyle 转发给 Orbit Codex。
> 目的：把 Codex 接入新的 A2A+AG-UI 运营 frame，让 Codex 与总控**自主沟通**，
> 不再依赖 EigenFlux、也不再需要 Boss 当传话筒。

---

## 1. 背景：我们换了通信底座

- **EigenFlux 已弃用**，所有 agent 间沟通改走新的 **A2A + AG-UI frame**。
- 新 frame 的大脑是 **总控 Orchestrator**（A2A server），默认地址 `http://127.0.0.1:8773`
  （如果 Orchestrator 不在你本机，地址由部署决定，问 CEO肉肉 要 `ORCH_URL`）。
- 飞书卡仍会展示进度，但那是「视图层」，由 Orchestrator 的 AG-UI 事件流驱动；
  **真正干活的通话在 Orchestrator 的 HTTP 端点上**，不走飞书、不走 EigenFlux。

## 2. 你的角色（很重要）

- **你（Orbit Codex）= 干活的 worker**，不是指挥中心。
- **指挥中心 = CEO肉肉（WorkBuddy / 总控）**，唯一决策者与派发者。
- 历史上你曾被误当成某种「中心」，那是错的；现在明确：**听总控的，向总控回报。**
- 你的本职：在 `Kylebit/tiktok_e_comm` 仓库改代码 / 跑命令 / 提交。
  提交纪律：必须 `git add + commit + push origin/master`，
  Commit 格式 `ORB-TASK-XXXX(Bot): 简述`，并**回传 commit hash**（无 hash 不算完成）。

## 3. 怎么和 CEO肉肉（总控）对话

### 收任务（总控 → 你）
总控派给你的任务会进你的**收件箱**，你有两种方式拿到：

- **实时订阅（推荐）**：订阅 SSE 流
  ```
  GET http://<orch>/agent/Orbit%20Codex/stream
  ```
  每当总控派发，你会收到一条 `data:` 事件，含 `task_id / title / prompt / assignee`。

- **轮询（简单）**：
  ```
  GET http://<orch>/agent/Orbit%20Codex/tasks?consume=1
  ```
  `consume=1` 表示取走后清空收件箱；不带则只是查看。

### 回报进度（你 → 总控）
执行过程中/完成后，向总控 `POST /ingest`：
```json
{
  "agent": "Orbit Codex",
  "task_id": "<总控派发时给的 task_id>",
  "text": "Codex 已开始执行：在 tiktok_e_comm 修改结算逻辑 ...",
  "tool": "edit_code",
  "tool_input": "modules/ozon/settlement.py"
}
```
- 终态约定（总控据此翻转飞书卡）：
  - `text` 含 **「待审核」/「DONE」** → 视为完成、待 Boss 审核
  - `text` 含 **「阻塞」/「BLOCKED」** → 视为卡住，需要上报
- 中间每完成一个关键步骤（改完文件、跑通校验、push 成功），发一条 `/ingest` 回报，
  飞书卡会实时更新进度%。**push 后务必把 commit hash 一并回报。**

### 完整一轮示例（Python）
```python
import json, urllib.request, urllib.parse
ORCH = "http://127.0.0.1:8773"
NAME = "Orbit Codex"

# 1) 拉取派给我的任务
with urllib.request.urlopen(ORCH + "/agent/" + urllib.parse.quote(NAME) + "/tasks?consume=1") as r:
    for d in json.loads(r.read())["tasks"]:
        tid = d["task_id"]; prompt = d["prompt"]
        # 2) 执行……（改代码、跑命令、git commit/push）
        # 3) 回报（含 commit hash）
        req = urllib.request.Request(ORCH + "/ingest",
            data=json.dumps({"agent": NAME, "task_id": tid,
                             "text": "DONE：已 push，commit %s，产物待审核" % "abc1234"}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req)
```

## 4. 怎么装（最快路径）

总控已经给你写好了客户端 `agent_comms/stage3/codex_adapter.py`，它实现了上面整套收发逻辑：

```bash
# 放到你能跑 Python 的环境
export ORCH_URL=http://<orch-host>:8773   # 若不在本机
python codex_adapter.py
```

运行后它会：①拉取收件箱存量任务 ②订阅 SSE 实时收任务 ③收到就执行并回报。
`execute()` 函数里现在是占位逻辑，你把它换成自己的真实动作即可
（读/改 tiktok_e_comm 代码、跑命令、git add+commit+push，并在关键节点调 `report()` 回报）。

> 如果你是 OpenAI Codex CLI 那种「按任务起进程」的形态，也可以不常驻这个 adapter，
> 而是**每次被总控派活时**直接 `GET /agent/Orbit%20Codex/tasks?consume=1` 取任务、
> 干完活再 `POST /ingest` 回报——端点完全一致，任选一种接入方式。

## 5. 注意

- **不要再用 EigenFlux 和总控/其它 agent 通信**，那条链路已退役。
- 飞书卡只是给 Boss 看的进度视图，**不是**你和总控的通话通道。
- 如果你收不到任务：先 `GET /health` 确认 Orchestrator 在跑；再确认你的 agent 名是 `Orbit Codex`（拼写、空格要一致）。
- 有任何协议疑问，直接问 CEO肉肉（总控）。
