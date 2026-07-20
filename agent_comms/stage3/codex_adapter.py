# -*- coding: utf-8 -*-
"""
Orbit Codex Stage3 A2A client.

This file is intentionally a safe bridge, not an autonomous fake executor.
It receives tasks for "Orbit Codex" from the Stage3 Orchestrator, sends ACK/progress
events to /ingest, and persists task payloads into a local inbox for the live Codex
session to execute. It never fabricates DONE, commit hashes, tests, or pushes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AGENT_NAME = "Orbit Codex"
ORCH = os.environ.get("ORCH_URL", "http://127.0.0.1:8773").rstrip("/")
HERE = Path(__file__).resolve().parent
INBOX_DIR = HERE / "codex_inbox"
STATE_FILE = HERE / "codex_adapter_state.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _request_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: int = 10) -> Any:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


def health() -> dict[str, Any]:
    return _request_json("GET", f"{ORCH}/health", timeout=5)


def report(task_id: str | None, text: str, **extra: Any) -> Any:
    payload: dict[str, Any] = {"agent": AGENT_NAME, "text": text}
    if task_id:
        payload["task_id"] = task_id
    payload.update(extra)
    return _request_json("POST", f"{ORCH}/ingest", payload=payload, timeout=10)


def fetch_tasks(consume: bool = True) -> list[dict[str, Any]]:
    name = urllib.parse.quote(AGENT_NAME)
    consume_flag = "1" if consume else "0"
    data = _request_json("GET", f"{ORCH}/agent/{name}/tasks?consume={consume_flag}", timeout=10)
    return list(data.get("tasks", []))


def persist_task(dispatch: dict[str, Any]) -> Path:
    task_id = str(dispatch.get("task_id") or f"task_{int(time.time())}")
    payload = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "agent": AGENT_NAME,
        "orchestrator": ORCH,
        "dispatch": dispatch,
        "status": "ACKED_LOCAL_INBOX",
    }
    target = INBOX_DIR / f"{task_id}.json"
    _write_json(target, payload)
    return target


def ack_task(dispatch: dict[str, Any]) -> Path:
    task_id = str(dispatch.get("task_id") or "")
    title = dispatch.get("title") or ""
    prompt = dispatch.get("prompt") or ""
    # 去重：本地 inbox 已有该任务文件则只补一次 ACK，避免重复持久化/回报
    if task_id and (INBOX_DIR / f"{task_id}.json").is_file():
        report(task_id, "ACK(去重): Orbit Codex 已接收（本地 inbox 已有）",
               tool="stage3_a2a_ack_dedup", title=title or "Orbit Codex ACK")
        return INBOX_DIR / f"{task_id}.json"
    inbox_file = persist_task(dispatch)
    text = (
        "ACK: Orbit Codex 已接收 Stage3 A2A 任务，已写入本地 inbox；"
        f"task={task_id} title={title[:80]}"
    )
    report(
        task_id,
        text,
        tool="stage3_a2a_ack",
        tool_input=str(inbox_file),
        title=title or "Orbit Codex ACK",
    )
    if prompt:
        print(f"[codex-stage3] ACK {task_id}: {prompt[:120]}")
    else:
        print(f"[codex-stage3] ACK {task_id}")
    return inbox_file


def poll_once(consume: bool = True, ack: bool = True) -> list[dict[str, Any]]:
    tasks = fetch_tasks(consume=consume)
    state = _read_json(STATE_FILE, {})
    state["last_poll_at"] = datetime.now(timezone.utc).isoformat()
    state["last_count"] = len(tasks)
    state["orchestrator"] = ORCH
    _write_json(STATE_FILE, state)

    if ack:
        for task in tasks:
            ack_task(task)
    return tasks


def stream_forever() -> None:
    name = urllib.parse.quote(AGENT_NAME)
    url = f"{ORCH}/agent/{name}/stream"

    # First consume pending tasks so restart does not miss work.
    try:
        poll_once(consume=True, ack=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[codex-stage3] pending poll failed: {exc}", file=sys.stderr)

    while True:
        print(f"[codex-stage3] subscribing {url}")
        try:
            with urllib.request.urlopen(url, timeout=60) as stream:
                print("[codex-stage3] connected")
                for raw in stream:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    try:
                        event = json.loads(data)
                    except Exception:
                        continue
                    if event.get("type") == "connected":
                        continue
                    if event.get("task_id"):
                        ack_task(event)
        except Exception as exc:  # noqa: BLE001
            print(f"[codex-stage3] stream disconnected: {exc}", file=sys.stderr)
            time.sleep(3)


def main() -> int:
    parser = argparse.ArgumentParser(description="Orbit Codex Stage3 A2A client")
    parser.add_argument("--health", action="store_true", help="Check orchestrator health and exit")
    parser.add_argument("--poll-once", action="store_true", help="Fetch pending tasks once, ACK them, and exit")
    parser.add_argument("--peek", action="store_true", help="Fetch pending tasks without consuming or ACKing")
    parser.add_argument("--stream", action="store_true", help="Subscribe to task stream forever")
    args = parser.parse_args()

    if args.health:
        print(json.dumps(health(), ensure_ascii=False, indent=2))
        return 0
    if args.peek:
        print(json.dumps(fetch_tasks(consume=False), ensure_ascii=False, indent=2))
        return 0
    if args.poll_once:
        print(json.dumps(poll_once(consume=True, ack=True), ensure_ascii=False, indent=2))
        return 0

    # Default is stream mode because this file is the long-running adapter.
    stream_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
