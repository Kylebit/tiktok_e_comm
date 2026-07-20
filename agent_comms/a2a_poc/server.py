# -*- coding: utf-8 -*-
"""
最小 A2A 兼容 server（阶段 0 PoC，端口 8769）

证明：A2A 传输契约在总控侧可跑通 ——
  1) Agent Card 发现：GET /.well-known/agent.json
  2) 流式任务：POST /  (JSON-RPC method=message/stream) 返回 SSE 流，
     依次推送 TaskStatusUpdateEvent + TaskArtifactUpdateEvent（与 a2a-sdk 1.1.1 线格式一致）。
  3) 非流式：POST / (method=message/send) 返回最终 Task（含 artifact）。

说明：生产版将用 a2a-sdk 官方 AgentExecutor / DefaultRequestHandler 脚手架；
本 PoC 仅验证「协议线格式 + SSE 流式」这一架构关键点，事件字段名与 a2a-sdk
TaskStatusUpdateEvent / TaskArtifactUpdateEvent 的 JSON schema 完全一致。

当前 EigenFlux 系统完全不受影响（独立端口、独立目录）。
"""
import json
import uuid
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

PORT = 8769
AGENT_URL = f"http://127.0.0.1:{PORT}"

AGENT_CARD = {
    "schemaVersion": "1.0",
    "name": "Orbit Hive 总控 (A2A PoC)",
    "description": "PoC A2A agent：接收任务并流式回报状态/产物",
    "url": AGENT_URL,
    "provider": {"organization": "Orbit Hive", "url": "https://orbit.hive"},
    "version": "0.1.0",
    "capabilities": {"streaming": True, "pushNotifications": False, "stateTransitionHistory": True},
    "skills": [
        {
            "id": "echo-task",
            "name": "Echo Task",
            "description": "回显任务并流式回报状态与产物（PoC 用）",
            "tags": ["poc", "a2a"],
            "examples": ["ping"],
        }
    ],
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["text/plain"],
}

app = FastAPI(title="Orbit Hive A2A PoC")


@app.get("/.well-known/agent.json")
async def agent_card():
    return JSONResponse(AGENT_CARD)


def _extract_text(params):
    msg = params.get("message", {})
    parts = msg.get("parts", [])
    if parts and isinstance(parts[0], dict):
        return parts[0].get("text", "")
    return ""


def build_events(task_id, context_id, text):
    """构造 A2A 流式事件（线格式与 a2a-sdk 一致）。"""
    status_event = {
        "taskId": task_id,
        "contextId": context_id,
        "status": {
            "state": "working",
            "message": {"role": "agent", "parts": [{"type": "text", "text": text}]},
        },
        "final": False,
    }
    artifact_event = {
        "taskId": task_id,
        "contextId": context_id,
        "artifact": {
            "artifactId": task_id + "-art",
            "name": "ack",
            "parts": [{"type": "text", "text": "ACK: " + text}],
        },
        "append": False,
        "lastChunk": True,
        "final": True,
    }
    return [status_event, artifact_event]


@app.post("/")
async def rpc(request: Request):
    body = await request.json()
    method = body.get("method")
    params = body.get("params", {}) or {}
    rpc_id = body.get("id")

    if method == "message/stream":
        text = _extract_text(params)
        task_id = params.get("taskId") or ("task-" + uuid.uuid4().hex[:8])
        context_id = params.get("contextId") or task_id
        events = build_events(task_id, context_id, text)

        async def gen():
            for ev in events:
                yield {
                    "data": json.dumps(
                        {"jsonrpc": "2.0", "id": rpc_id, "result": ev},
                        ensure_ascii=False,
                    )
                }

        return EventSourceResponse(gen())

    if method == "message/send":
        text = _extract_text(params)
        task_id = params.get("taskId") or ("task-" + uuid.uuid4().hex[:8])
        context_id = params.get("contextId") or task_id
        events = build_events(task_id, context_id, text)
        task = {
            "id": task_id,
            "contextId": context_id,
            "status": {"state": "completed"},
            "artifacts": [events[-1]["artifact"]],
            "history": [events[0]["status"]["message"]],
        }
        return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": task})

    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": "method not found"}}
    )


if __name__ == "__main__":
    import uvicorn

    print(f"A2A PoC server on {AGENT_URL}  (Agent Card at /.well-known/agent.json)")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
