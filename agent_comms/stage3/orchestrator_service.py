# -*- coding: utf-8 -*-
"""
阶段3：持久化 A2A Orchestrator + AG-UI 事件总线（新 frame 的常驻大脑）

这是「让新 frame 成为唯一运营通道」的服务端：
- 复用 stage1 orchestrator 的任务状态机（taskId <-> 飞书 record）
- 内置 asyncio EventBus，把任务更新映射成标准 AG-UI 事件，经 SSE `/agui/events` 实时广播
- `POST /ingest` 接收真实 worker（Codex/Cursor/Claude）回报 -> 创建/推进 Task -> 发 AG-UI 事件
- 端口 8773，独立运行，已弃用 EigenFlux；旧监听器已退役，本服务是唯一的运营通道。
- 新增 per-agent 收件箱 + 派发 SSE（/agent/{name}/stream、/agent/{name}/tasks），
  让 worker(Cursor/Codex) 经「agent↔agent」自主通道直接收发，无需 Boss 人工转发（最终形态）。

本服务是「agent↔Boss」视图层与「agent↔agent」派发层的事实来源：
飞书卡由 AG-UI 事件流驱动，agent 间沟通由 per-agent 收件箱驱动。
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE1 = os.path.join(os.path.dirname(HERE), "stage1")
STAGE2 = os.path.join(os.path.dirname(HERE), "stage2")
A2A_POC = os.path.join(os.path.dirname(HERE), "a2a_poc")
AGENT_COMMS = os.path.dirname(HERE)
TIKTOK = os.path.dirname(AGENT_COMMS)
AGENT_PR = os.path.dirname(TIKTOK)
for p in [AGENT_PR, STAGE1, STAGE2, A2A_POC, AGENT_COMMS]:
    if p not in sys.path:
        sys.path.insert(0, p)

import orchestrator as orch  # noqa: E402
from ag_ui_mapping import map_a2a_update_to_ag_ui  # noqa: E402

PORT = 8773
AGENT_URL = f"http://127.0.0.1:{PORT}"


# --------------------------------------------------------------------------
# asyncio EventBus：AG-UI 事件流广播（支持多 SSE 订阅者）
# --------------------------------------------------------------------------
class AgUiBus:
    def __init__(self):
        self._queues = []
        self._lock = asyncio.Lock()

    async def subscribe(self):
        q = asyncio.Queue()
        async with self._lock:
            self._queues.append(q)
        return q

    async def publish(self, event):
        async with self._lock:
            for q in list(self._queues):
                await q.put(event)


BUS = AgUiBus()


# --------------------------------------------------------------------------
# per-agent 收件箱 + 派发 SSE：让 worker(如 Cursor) 自主接收总控派发的任务，
# 无需 Boss 人工转发。回报仍走 POST /ingest。
# 这是 A2A「agent↔agent」通道的最小实现（最终形态：各 agent 自主沟通）。
# --------------------------------------------------------------------------
class AgentChannel:
    def __init__(self):
        self._queues = []
        self._pending = []
        self._lock = asyncio.Lock()

    async def subscribe(self):
        q = asyncio.Queue()
        async with self._lock:
            self._queues.append(q)
        return q

    async def publish(self, dispatch):
        async with self._lock:
            self._pending.append(dispatch)
            for q in list(self._queues):
                await q.put(dispatch)

    def take_pending(self):
        items = self._pending
        self._pending = []
        return items


channels = {}  # agent_name -> AgentChannel


def channel_of(name):
    return channels.setdefault(name, AgentChannel())


app = FastAPI(title="Orbit Hive A2A Orchestrator (Stage3)")


# --------------------------------------------------------------------------
# 复用 stage1 的任务路由 + Agent Card
# --------------------------------------------------------------------------
@app.get("/.well-known/agent.json")
async def agent_card():
    return JSONResponse({
        **orch.AGENT_CARD,
        "name": "Orbit Hive 总控 Orchestrator (A2A Stage3)",
        "url": AGENT_URL,
        "version": "0.3.0",
        "note": "新 frame 常驻：AG-UI 事件总线的唯一来源；旧监听器即将退役",
    })


@app.get("/tasks")
async def list_tasks():
    return JSONResponse(orch.list_tasks())


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    return JSONResponse(orch.get_task(task_id) or {"error": "not found"})


@app.get("/health")
async def health():
    return JSONResponse({"ok": True, "role": "stage3-orchestrator", "port": PORT})


# --------------------------------------------------------------------------
# SSE：AG-UI 事件流（飞书卡适配 runner 订阅此端点）
# --------------------------------------------------------------------------
@app.get("/agui/events")
async def agui_events():
    async def gen():
        q = await BUS.subscribe()
        yield {"event": "ready", "data": json.dumps({"type": "connected"}, ensure_ascii=False)}
        while True:
            ev = await q.get()
            yield {"data": json.dumps(ev, ensure_ascii=False)}

    return EventSourceResponse(gen())


# --------------------------------------------------------------------------
# 接收真实 worker 回报 -> 创建/推进 Task -> 发 AG-UI 事件
# body: {"agent","text","task_id"?,"title"?,"feishu_record"?,"final"?:bool}
# --------------------------------------------------------------------------
@app.post("/ingest")
async def ingest(request: Request):
    body = await request.json()
    agent = body.get("agent") or body.get("assignee") or "未知Agent"
    text = body.get("text") or ""
    feishu_record = body.get("feishu_record")
    task_id = body.get("task_id")
    title = body.get("title") or f"{agent} 回报"
    final = body.get("final", False)

    if not task_id:
        task_id = orch.create_task(title, feishu_record=feishu_record, assignee=agent)

    # 终态判定
    if "待审核" in text or "DONE" in text.upper() or final:
        a2a_state = "input-required"
        cn_status = "待审核"
        pct = "100%"
    elif "阻塞" in text or "BLOCKED" in text.upper():
        a2a_state = "blocked"
        cn_status = "阻塞"
        pct = "100%"
    else:
        a2a_state = "working"
        cn_status = "进行中"
        pct = "50%"

    orch.apply_a2a_event({"task_id": task_id, "status": a2a_state, "progress_text": text})

    ag_update = {
        "task_id": task_id,
        "status": cn_status,
        "progress_text": text,
        "tool": body.get("tool"),
        "tool_input": body.get("tool_input"),
        "card_fields": {"状态": cn_status, "进度": pct, "负责Agent": agent,
                        "飞书Record": feishu_record or "—"},
    }
    for ev in map_a2a_update_to_ag_ui(ag_update, thread_id=task_id):
        await BUS.publish(ev)

    return JSONResponse({"ok": True, "task_id": task_id, "status": cn_status})


@app.post("/dispatch")
async def dispatch_task(request: Request):
    """总部派发任务（出站）。

    新契约（2026-07-20）：已放弃 EigenFlux，不再经它触达 Cursor/Codex。
    两种出站模式：
    - mode="boss-relay"（过渡期默认）：建 task + 推飞书指令卡，由 Boss 看到后人工转发给目标 agent；
      同时把任务放进目标 agent 的收件箱（agent 也可自主拉，但 Boss 仍可见/转发）。
    - mode="direct"（最终形态）：不依赖 Boss 转发，任务直接进目标 agent 收件箱，
      目标 agent 经 /agent/{name}/stream 或 /agent/{name}/tasks 自主接收；
      飞书卡只同步「已直派」状态，Boss 无需转发。
    body: {"assignee","prompt","task_id"?,"title"?,"feishu_record"?,"mode"?}
    """
    body = await request.json()
    assignee = body.get("assignee") or body.get("agent") or "Cursor"
    prompt = body.get("prompt") or body.get("text") or ""
    task_id = body.get("task_id")
    title = body.get("title") or ("派发 %s：%s" % (assignee, prompt[:24]))
    feishu_record = body.get("feishu_record")
    mode = body.get("mode") or "boss-relay"

    if not task_id:
        task_id = orch.create_task(title, feishu_record=feishu_record, assignee=assignee)

    # 进目标 agent 收件箱（两种模式都进，方便 agent 自主拉）
    dispatch_obj = {
        "task_id": task_id,
        "title": title,
        "prompt": prompt,
        "assignee": assignee,
        "feishu_record": feishu_record,
        "mode": mode,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    await channel_of(assignee).publish(dispatch_obj)

    if mode == "direct":
        # 最终形态：直派，飞书卡只同步「已派发」，Boss 无需转发
        orch.apply_a2a_event({"task_id": task_id, "status": "submitted",
                              "progress_text": "已直派 %s（自主通道，无需 Boss 转发）" % assignee})
        cn_status, pct = "已派发", "10%"
        prog = "已直派给 %s，等待其自主回报" % assignee
    else:
        # 过渡期：推指令卡给 Boss 转发
        orch.apply_a2a_event({"task_id": task_id, "status": "submitted",
                              "progress_text": "待 Boss 转发给 " + assignee})
        cn_status, pct = "待转发", "0%"
        prog = "已生成指令卡，请 Boss 转发给 %s" % assignee

    ag_update = {
        "task_id": task_id,
        "status": cn_status,
        "progress_text": prog,
        "tool": None,
        "tool_input": None,
        "card_fields": {"状态": cn_status, "进度": pct, "负责Agent": assignee,
                        "飞书Record": feishu_record or "—", "指令": prompt},
    }
    for ev in map_a2a_update_to_ag_ui(ag_update, thread_id=task_id):
        await BUS.publish(ev)

    return JSONResponse({"ok": True, "task_id": task_id, "status": cn_status,
                         "assignee": assignee, "mode": mode,
                         "agent_inbox": "/agent/%s/tasks" % assignee})


# --------------------------------------------------------------------------
# agent↔agent 自主通道：worker(Cursor/Codex) 拉取/订阅总控派给自己的任务
# --------------------------------------------------------------------------
@app.get("/agent/{agent_name}/tasks")
async def agent_pending_tasks(agent_name: str, consume: int = 0):
    """worker 轮询自己的收件箱。consume=1 取走后清空（一次性拉取）。"""
    chan = channel_of(agent_name)
    items = chan.take_pending() if consume else list(chan._pending)
    return JSONResponse({"ok": True, "agent": agent_name, "count": len(items), "tasks": items})


@app.get("/agent/{agent_name}/stream")
async def agent_stream(agent_name: str):
    """worker 订阅自己的派发 SSE，实时收到总控派发的任务。"""
    chan = channel_of(agent_name)

    async def gen():
        q = await chan.subscribe()
        yield {"event": "ready", "data": json.dumps({"type": "connected", "agent": agent_name}, ensure_ascii=False)}
        while True:
            d = await q.get()
            yield {"event": "dispatch", "data": json.dumps(d, ensure_ascii=False)}

    return EventSourceResponse(gen())


if __name__ == "__main__":
    import uvicorn

    print(f"[stage3] Orchestrator 常驻服务 on {AGENT_URL}  (AG-UI 事件流: /agui/events)")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
