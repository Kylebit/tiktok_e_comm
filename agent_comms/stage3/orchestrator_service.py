# -*- coding: utf-8 -*-
"""
阶段3：持久化 A2A Orchestrator + AG-UI 事件总线（新 frame 的常驻大脑）

这是「让新 frame 成为唯一运营通道」的服务端：
- 复用 stage1 orchestrator 的任务状态机（taskId <-> 飞书 record）
- 内置 asyncio EventBus，把任务更新映射成标准 AG-UI 事件，经 SSE `/agui/events` 实时广播
- `POST /ingest` 接收真实 worker（Codex/Cursor/Claude）回报 -> 创建/推进 Task -> 发 AG-UI 事件
- 独立端口 8771，与旧链路（8765 / eigenflux_stream_listener.py）完全隔离；
  先并行验证，确认能完整驱动飞书卡后，再退役旧监听器（保留回滚）。

本服务是「agent↔Boss」视图层的事实来源：飞书卡由这里的 AG-UI 事件流驱动，
不再由旧监听器轮询 EigenFlux 驱动。
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
    出站只做「建 task + 推飞书指令卡」，由 Boss(人类) 看到卡后人工转发给目标 agent；
    目标 agent 的回报也由 Boss 转回，经 /ingest 灌入本 Orchestrator。
    body: {"assignee","prompt","task_id"?,"title"?,"feishu_record"?}
    """
    body = await request.json()
    assignee = body.get("assignee") or body.get("agent") or "Cursor"
    prompt = body.get("prompt") or body.get("text") or ""
    task_id = body.get("task_id")
    title = body.get("title") or ("派发 %s：%s" % (assignee, prompt[:24]))
    feishu_record = body.get("feishu_record")

    if not task_id:
        task_id = orch.create_task(title, feishu_record=feishu_record, assignee=assignee)

    orch.apply_a2a_event({"task_id": task_id, "status": "submitted",
                          "progress_text": "待 Boss 转发给 " + assignee})

    ag_update = {
        "task_id": task_id,
        "status": "待转发",
        "progress_text": "已生成指令卡，请 Boss 转发给 %s" % assignee,
        "tool": None,
        "tool_input": None,
        "card_fields": {"状态": "待转发", "进度": "0%", "负责Agent": assignee,
                        "飞书Record": feishu_record or "—", "指令": prompt},
    }
    for ev in map_a2a_update_to_ag_ui(ag_update, thread_id=task_id):
        await BUS.publish(ev)

    return JSONResponse({"ok": True, "task_id": task_id, "status": "待转发", "assignee": assignee})


if __name__ == "__main__":
    import uvicorn

    print(f"[stage3] Orchestrator 常驻服务 on {AGENT_URL}  (AG-UI 事件流: /agui/events)")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
