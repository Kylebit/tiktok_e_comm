# -*- coding: utf-8 -*-
"""
Orbit Codex Stage3 A2A client.

This file is intentionally a safe bridge, not an autonomous fake executor.
It receives tasks for "Orbit Codex" from the Stage3 Orchestrator, sends ACK/progress
events to /ingest, and persists task payloads into a local inbox for the live Codex
session to execute. It never fabricates DONE, commit hashes, tests, or pushes.

On receiving a task it prints a wake signal "AGENT_A2A_TICK_codex {json}" (mirroring
Cursor's AGENT_A2A_TICK_cursor) so a configured Codex session auto-wakes and executes
the task for real; the live Codex session does the actual work and reports back.
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
# 对齐 Cursor 的唤醒钩子：真实 Codex 会话若配了 notify_on_output 匹配此前缀即自动被唤醒执行
WAKE_PREFIX = "AGENT_A2A_TICK_codex"


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


def _wake_payload(dispatch: dict[str, Any], path: Path) -> dict[str, Any]:
    task_id = str(dispatch.get("task_id") or "")
    return {
        "prompt": (
            "A2A Orbit Codex task: set ORCH_URL if needed (default http://127.0.0.1:8773); "
            f"read {path}; execute the task for real (no fake scrape); "
            "report progress via: python agent_comms/stage3/codex_adapter.py report "
            f"--task-id {task_id} --text \"...\"; "
            "final text must contain DONE/待审核 or BLOCKED/阻塞; "
            "do not contact the human user; report only to Orchestrator/CEO肉肉."
        ),
        "task_id": task_id,
        "path": str(path).replace("\\", "/"),
        "title": dispatch.get("title") or "",
    }


def _emit_wake(dispatch: dict[str, Any], path: Path) -> None:
    wake = _wake_payload(dispatch, path)
    print(f"{WAKE_PREFIX} {json.dumps(wake, ensure_ascii=False)}", flush=True)


def ack_task(dispatch: dict[str, Any]) -> Path:
    task_id = str(dispatch.get("task_id") or "")
    title = dispatch.get("title") or ""
    prompt = dispatch.get("prompt") or ""
    inbox_file = INBOX_DIR / f"{task_id}.json"
    # 去重：本地 inbox 已有该任务文件则只补一次 ACK，避免重复持久化/回报
    if task_id and inbox_file.is_file():
        report(task_id, "ACK(去重): Orbit Codex 已接收（本地 inbox 已有）",
               tool="stage3_a2a_ack_dedup", title=title or "Orbit Codex ACK")
        return inbox_file
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
    # 唤醒真实 Codex 会话执行（对齐 Cursor 的 AGENT_A2A_TICK_cursor 行为）
    _emit_wake(dispatch, inbox_file)
    return inbox_file


def emit_wake_for_pending_files() -> None:
    """本地 pending 落盘任务也打一次唤醒（重启恢复 / 漏 notify 时）。"""
    if not INBOX_DIR.is_dir():
        return
    for path in sorted(INBOX_DIR.glob("*.json")):
        try:
            payload = _read_json(path, {})
        except Exception as exc:  # noqa: BLE001
            print(f"[codex-stage3] 读取 pending 失败: {path} {exc}", file=sys.stderr)
            continue
        dispatch = payload.get("dispatch", payload)
        task_id = str(dispatch.get("task_id") or path.stem)
        if payload.get("status") in ("DONE", "COMPLETED", "CANCELED", "FAILED"):
            continue
        print(f"[codex-stage3] 本地 pending 待执行: {task_id}", flush=True)
        _emit_wake(dispatch, path)


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

    # 对本地已落盘但仍 pending 的任务补打唤醒信号（重启恢复 / 漏 notify 兜底）
    emit_wake_for_pending_files()

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


def cmd_report(task_id: str, text: str, tool: str, tool_input: str) -> int:
    if not task_id:
        print("需要 --task-id", file=sys.stderr)
        return 2
    if not text:
        print("需要 --text", file=sys.stderr)
        return 2
    result = report(
        task_id, text,
        tool=tool or None,
        tool_input=tool_input or None,
    )
    print(json.dumps(result or {"ok": False}, ensure_ascii=False))
    return 0 if result is not None else 1


def cmd_complete(task_id: str) -> int:
    target = INBOX_DIR / f"{task_id}.json"
    if not target.is_file():
        print(f"inbox 任务不存在: {task_id}", file=sys.stderr)
        return 1
    payload = _read_json(target, {})
    payload["status"] = "DONE"
    _write_json(target, payload)
    print(f"marked DONE: {target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Orbit Codex Stage3 A2A client")
    parser.add_argument("--health", action="store_true", help="Check orchestrator health and exit")
    parser.add_argument("--poll-once", action="store_true", help="Fetch pending tasks once, ACK them, and exit")
    parser.add_argument("--peek", action="store_true", help="Fetch pending tasks without consuming or ACKing")
    parser.add_argument("--stream", action="store_true", help="Subscribe to task stream forever")
    parser.add_argument("--report", action="store_true", help="POST /ingest 回报总控（配合 --task-id/--text）")
    parser.add_argument("--complete", action="store_true", help="把 inbox 任务标记 DONE（配合 --task-id）")
    parser.add_argument("--task-id", default="", help="任务 id（用于 --report/--complete）")
    parser.add_argument("--text", default="", help="回报文本（用于 --report）")
    parser.add_argument("--tool", default="", help="回报 tool 字段")
    parser.add_argument("--tool-input", default="", help="回报 tool_input 字段")
    args = parser.parse_args()

    if args.health:
        print(json.dumps(health(), ensure_ascii=False, indent=2))
        return 0
    if args.peek:
        print(json.dumps(fetch_tasks(consume=False), ensure_ascii=False, indent=2))
        return 0
    if args.report:
        return cmd_report(args.task_id, args.text, args.tool, args.tool_input)
    if args.complete:
        return cmd_complete(args.task_id)
    if args.poll_once:
        print(json.dumps(poll_once(consume=True, ack=True), ensure_ascii=False, indent=2))
        return 0

    # Default is stream mode because this file is the long-running adapter.
    stream_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
