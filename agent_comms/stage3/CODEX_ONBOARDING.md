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

运行后它会：①拉取收件箱存量任务 ②订阅 SSE 实时收任务 ③收到就把任务写入本地 `codex_inbox/<task_id>.json`
、向总控 `POST /ingest` 回 ACK、**并打印一条唤醒信号** `AGENT_A2A_TICK_codex {json}`（含 `prompt/path/task_id`），
然后它**自己退出执行环节**——真正的活儿要**由你来干**（和 Cursor 的行为完全一致）。

> 唤醒信号对齐 Cursor 的 `AGENT_A2A_TICK_cursor`：若你的环境配了
> `notify_on_output` / 钩子匹配 `AGENT_A2A_TICK_codex` 前缀，就会被自动唤醒执行；
> 没有自动钩子也不要紧，你只要**按下面的「正确做法」主动去读 inbox 任务并执行**即可。

### adapter 附带的使用子命令（方便你回报）
```bash
# 回报进度 / 完成（向总控 POST /ingest）
python codex_adapter.py --report --task-id <task_id> --text "DONE：已 push，commit abc1234，产物待审核" --tool edit_code --tool-input modules/ozon/settlement.py
# 把本地 inbox 任务标记为 DONE
python codex_adapter.py --complete --task-id <task_id>
# 只看收件箱、不消费
python codex_adapter.py --peek
```

> 如果你是 OpenAI Codex CLI 那种「按任务起进程」的形态，也可以不常驻这个 adapter，
> 而是**每次被总控派活时**直接 `GET /agent/Orbit%20Codex/tasks?consume=1` 取任务、
> 干完活再 `POST /ingest` 回报（`--report` 子命令就是干这件事）——端点完全一致，任选一种接入方式。

## 5. 正确做法（像 Cursor 那样自己主动干，不要等 Boss 来催）

总控派活后**不会**再由 Boss 人工转发给你（那是旧 EigenFlux 时代的做法，已弃用）。
你要和 Cursor 一样：**主动**发现并执行 inbox 里的任务。标准一轮：

1. **发现任务**：adapter 拉到你任务后会落盘 `codex_inbox/<task_id>.json`，并打印 `AGENT_A2A_TICK_codex ...`。
   你读到它（或自动钩子唤醒你）后，读该 json 里的 `dispatch.prompt` 拿到具体要求。
2. **真实执行**：在 `Kylebit/tiktok_e_comm` 仓库里改代码 / 跑命令，**不要造假**（不要伪造 DONE、commit hash、测试通过）。
3. **关键节点回报**：每完成一个里程碑（改完文件、跑通校验、push 成功），调
   `python codex_adapter.py --report --task-id <id> --text "..."` 回总控；**push 后务必把 commit hash 写进 text**。
4. **终态**：干完后在最后一条 `--report` 的 `text` 里写明 **「DONE」/「待审核」**（= 完成，等 Boss 审核）；
   若卡住写 **「BLOCKED」/「阻塞」** 并说明原因。终态回报后 `python codex_adapter.py --complete --task-id <id>` 收尾。
5. **git 纪律**：必须 `git add + commit + push origin/master`，Commit 格式 `ORB-TASK-XXXX(Bot): 简述`，并回传 hash。
6. **不要联系 Boss**：所有进度只回报给 Orchestrator / CEO肉肉（总控），不要去找人类用户。

> 一句话：Cursor 怎么被总控唤醒、自己干活、自己回报，你就怎么来。差别只是唤醒信号前缀是
> `AGENT_A2A_TICK_codex`（而不是 `_cursor`），其余收发、回报、git 纪律完全一致。

## 6. 注意

- **不要再用 EigenFlux 和总控/其它 agent 通信**，那条链路已退役。
- 飞书卡只是给 Boss 看的进度视图，**不是**你和总控的通话通道。
- 如果你收不到任务：先 `GET /health` 确认 Orchestrator 在跑；再确认你的 agent 名是 `Orbit Codex`（拼写、空格要一致）。
- 有任何协议疑问，直接问 CEO肉肉（总控）。

## 7. Codex heartbeat 防漏规则（必须执行）

每次 Orbit Codex 被唤醒或跑 heartbeat 时，不允许只看 HTTP 队列。正确顺序是：

1. `GET /health` 确认 Orchestrator 正常。
2. `GET /agent/Orbit%20Codex/tasks?consume=1` 拉取并 ACK 新任务。
3. 立刻运行 `python agent_comms/stage3/codex_adapter.py --scan-local-inbox`。
4. 如果输出任何 `pending_local_inbox` 或 `AGENT_A2A_TICK_codex`，必须读取对应 `codex_inbox/<task_id>.json`，执行 `dispatch.prompt`，并通过 `--report` 回报总控。

原因：adapter 会先消费 HTTP 队列并把任务落盘到 `codex_inbox`。此时 HTTP 队列会变空；如果 Codex 只查 HTTP 队列，就会漏掉已经 ACK 但尚未执行的本地任务。

## 8. Codex / Cursor 对齐：SSE 优先，不用定时轮询当主收件

Codex 的正确主路径与 Cursor 一致：

1. 常驻适配器只跑 `python -u agent_comms/stage3/codex_adapter.py --stream`。
2. 适配器启动时 drain 一次 `GET /agent/Orbit%20Codex/tasks?consume=1`，用于接走积压任务。
3. 之后长期订阅 `GET /agent/Orbit%20Codex/stream`，收到 SSE `data:` 事件后落盘 `codex_inbox/<task_id>.json`、ACK `/ingest`、打印 `AGENT_A2A_TICK_codex {...}`。
4. SSE 断线后 sleep 再重连；重连前可再 drain 一次，防止断线期间漏任务。
5. Codex 会话侧应配置 `notify_on_output` 匹配 `^AGENT_A2A_TICK_codex`，被唤醒后读取 wake JSON 中的 `path`，真实执行 `dispatch.prompt`。
6. `--poll-once` 只保留为手动排障命令，不作为主循环，不要再用每 N 分钟 `tasks?consume=1` 作为常规收件方式。
7. heartbeat 只做兜底健康检查和本地 inbox 防漏扫描；一旦 SSE + notify 工作正常，heartbeat 不应承担主收件职责。

执行端回报仍然固定：

```bash
python agent_comms/stage3/codex_adapter.py --report --task-id <id> --text "DONE: ... commit <hash>"
python agent_comms/stage3/codex_adapter.py --complete --task-id <id>
```

适配器只 ACK 和唤醒，不伪造 DONE、测试通过、commit hash 或 push 结果；终态必须由真实 Codex 会话在完成工作后回报。
