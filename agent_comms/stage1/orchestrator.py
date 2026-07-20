# -*- coding: utf-8 -*-
"""
A2A Orchestrator（阶段1）：总控侧 A2A-native 协调层

职责：
- 每个任务 = 一个 A2A Task（taskId <-> 飞书主任务表 record 映射）
- Task 生命周期状态机：submitted -> working -> input-required -> completed/failed/canceled/blocked
- 暴露 A2A 兼容 HTTP（Agent Card + message/stream SSE）供 AG-UI 层 / 上游订阅
- 通过 bridge 派发/回收（EigenFlux 作为 transport adapter），双轨并行、不替换旧链路

设计说明：
- 线格式（Agent Card / JSON-RPC message/stream / TaskStatusUpdateEvent / TaskArtifactUpdateEvent）
  与 a2a-sdk 1.1.1 的 JSON schema 完全一致；生产版可直接换 a2a-sdk 官方
  AgentExecutor/DefaultRequestHandler 脚手架，上层零改动。
- 当前 EigenFlux 系统完全不受影响（独立端口 8770、独立目录）。
"""
import json
import os
import threading
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

PORT = 8770
AGENT_URL = f"http://127.0.0.1:{PORT}"
HERE = os.path.dirname(os.path.abspath(__file__))
STORE_FILE = os.path.join(HERE, "orchestrator_tasks.json")

# ---- A2A TaskState 子集（与官方一致）----
STATE_SUBMITTED = "submitted"
STATE_WORKING = "working"
STATE_INPUT_REQUIRED = "input-required"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_CANCELED = "canceled"
STATE_BLOCKED = "blocked"

_lock = threading.Lock()
_tasks = {}


def _now():
    return datetime.now(timezone.utc).isoformat()


def load():
    global _tasks
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE, encoding="utf-8") as f:
                _tasks = json.load(f)
        except Exception:
            _tasks = {}


def save():
    with open(STORE_FILE, "w", encoding="utf-8") as f:
        json.dump(_tasks, f, ensure_ascii=False, indent=2)


load()


# --------------------------------------------------------------------------
# 任务状态机
# --------------------------------------------------------------------------
def create_task(title, feishu_record=None, assignee=None, agent_id=None,
                agent_conv_id=None, prompt=None):
    """创建一个 A2A Task，返回 task_id。taskId 与飞书主任务表 record 通过 feishu_record 关联。"""
    task_id = "task-" + uuid.uuid4().hex[:10]
    context_id = task_id
    with _lock:
        _tasks[task_id] = {
            "task_id": task_id,
            "context_id": context_id,
            "title": title,
            "feishu_record": feishu_record,
            "assignee": assignee,
            "agent_id": agent_id,
            "agent_conv_id": agent_conv_id,
            "prompt": prompt,
            "state": STATE_SUBMITTED,
            "progress_text": "",
            "progress_pct": 0,
            "history": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        save()
    return task_id


def get_task(task_id):
    return _tasks.get(task_id)


def list_tasks():
    return list(_tasks.values())


def apply_a2a_event(event):
    """消费一条 A2A 事件（TaskStatusUpdateEvent / TaskArtifactUpdateEvent），
    推进 Task 状态机。返回更新后的 task。

    兼容两种线格式：
      - 原始 A2A 事件：{"taskId","contextId","status":{"state","message":{"parts":[...]}},"final"}
                        {"taskId","artifact":{"name","parts":[...]},"final"}
      - 高层 update：{"task_id","status","progress_text","card_fields"}
    """
    task_id = event.get("taskId") or event.get("task_id")
    with _lock:
        t = _tasks.get(task_id)
        if not t:
            return None
        context_id = event.get("contextId") or t["context_id"]

        # status 事件
        if "status" in event:
            st = event["status"]
            state = st.get("state")
            msg = st.get("message", {}) or {}
            text = ""
            for p in msg.get("parts", []) or []:
                if p.get("type") == "text":
                    text += p.get("text", "")
            if state:
                t["state"] = state
            if text:
                t["progress_text"] = text
                t["history"].append({"ts": _now(), "role": "agent", "text": text})

        # 高层 update 兼容
        if "progress_text" in event and event["progress_text"]:
            t["progress_text"] = event["progress_text"]
            if not any(h.get("text") == event["progress_text"] for h in t["history"]):
                t["history"].append({"ts": _now(), "role": "agent", "text": event["progress_text"]})
        if event.get("status") and isinstance(event.get("status"), str):
            t["state"] = event["status"]

        # artifact 事件 -> 终态判定
        if "artifact" in event:
            art = event["artifact"]
            name = art.get("name")
            text = ""
            for p in art.get("parts", []) or []:
                if p.get("type") == "text":
                    text += p.get("text", "")
            t["history"].append({"ts": _now(), "role": "agent",
                                 "artifact": name, "text": text})
            # 终态约定：artifact.name == "待审核" -> input-required；"DONE"/"completed" -> completed
            if name == "待审核":
                t["state"] = STATE_INPUT_REQUIRED
            elif name in ("DONE", "completed", "done"):
                t["state"] = STATE_COMPLETED

        t["updated_at"] = _now()
        save()
    return t


# --------------------------------------------------------------------------
# A2A 兼容 HTTP 层（供 AG-UI 层 / 上游订阅）
# --------------------------------------------------------------------------
AGENT_CARD = {
    "schemaVersion": "1.0",
    "name": "Orbit Hive 总控 Orchestrator (A2A Stage1)",
    "description": "A2A-native 协调层：每任务一个 A2A Task，经 bridge 派发/回收子 agent，双轨并行。",
    "url": AGENT_URL,
    "provider": {"organization": "Orbit Hive", "url": "https://orbit.hive"},
    "version": "0.2.0",
    "capabilities": {"streaming": True, "pushNotifications": True, "stateTransitionHistory": True},
    "skills": [
        {
            "id": "orchestrate-task",
            "name": "Orchestrate Task",
            "description": "创建并协调一个跨 agent 的任务（A2A Task 生命周期）",
            "tags": ["orchestration", "a2a"],
            "examples": ["创建任务并派发 Cursor"],
        }
    ],
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain"],
    "note": "transport adapter: EigenFlux（双轨并行，旧链路保留）",
}

app = FastAPI(title="Orbit Hive A2A Orchestrator")


@app.get("/.well-known/agent.json")
async def agent_card():
    return JSONResponse(AGENT_CARD)


@app.get("/tasks/{task_id}")
async def get_task_http(task_id: str):
    t = _tasks.get(task_id)
    return JSONResponse(t or {"error": "not found"})


@app.get("/tasks")
async def list_tasks_http():
    return JSONResponse(list(_tasks.values()))


@app.post("/")
async def rpc(request: Request):
    body = await request.json()
    method = body.get("method")
    params = body.get("params", {}) or {}
    rpc_id = body.get("id")

    if method == "message/stream":
        # 订阅某 task 的状态流：先推历史快照，再推终态
        task_id = params.get("taskId") or (params.get("message", {}) or {}).get("taskId")
        t = _tasks.get(task_id)

        async def gen():
            if t:
                for h in t["history"]:
                    ev = {
                        "taskId": task_id,
                        "contextId": t["context_id"],
                        "status": {
                            "state": t["state"],
                            "message": {"role": "agent",
                                        "parts": [{"type": "text", "text": h.get("text", "")}]},
                        },
                        "final": False,
                    }
                    yield {"data": json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": ev},
                                              ensure_ascii=False)}
            # 终态快照
            snap = {
                "taskId": task_id,
                "contextId": (t or {}).get("context_id"),
                "status": {"state": (t or {}).get("state", "unknown")},
                "final": True,
            }
            yield {"data": json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": snap},
                                      ensure_ascii=False)}

        return EventSourceResponse(gen())

    if method == "message/send":
        task_id = params.get("taskId")
        t = _tasks.get(task_id)
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": t or {"error": "not found"}})

    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "method not found"}}
    )


if __name__ == "__main__":
    import uvicorn

    print(f"A2A Orchestrator on {AGENT_URL}  (Agent Card at /.well-known/agent.json)")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
