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
# Wake prefix mirrored by Codex notify_on_output.
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
            "report progress via: python agent_comms/stage3/codex_adapter.py --report "
            f"--task-id {task_id} --text \"...\"; "
            "final text must contain DONE or BLOCKED; "
            f"then: python agent_comms/stage3/codex_adapter.py --complete --task-id {task_id}; "
            "do not contact the human user; report only to Orchestrator/CEO."
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
    if task_id and inbox_file.is_file():
        report(
            task_id,
            "ACK(dedup): Orbit Codex already has this task in local inbox.",
            tool="stage3_a2a_ack_dedup",
            title=title or "Orbit Codex ACK",
        )
        return inbox_file
    inbox_file = persist_task(dispatch)
    text = (
        "ACK: Orbit Codex received Stage3 A2A task and wrote it to local inbox; "
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
    _emit_wake(dispatch, inbox_file)
    return inbox_file


DONE_LOCAL_STATES = {"DONE", "COMPLETED", "CANCELED", "FAILED"}


def local_pending_files() -> list[Path]:
    """Return local inbox tasks that were ACKed but not completed yet."""
    if not INBOX_DIR.is_dir():
        return []
    pending: list[Path] = []
    for path in sorted(INBOX_DIR.glob("*.json")):
        try:
            payload = _read_json(path, {})
        except Exception as exc:  # noqa: BLE001
            print(f"[codex-stage3] failed to read pending inbox file: {path} {exc}", file=sys.stderr)
            continue
        if str(payload.get("status") or "").upper() in DONE_LOCAL_STATES:
            continue
        pending.append(path)
    return pending


def emit_wake_for_pending_files() -> list[Path]:
    """Emit wake signals for unfinished local inbox tasks."""
    pending = local_pending_files()
    for path in pending:
        payload = _read_json(path, {})
        dispatch = payload.get("dispatch", payload)
        task_id = str(dispatch.get("task_id") or path.stem)
        print(f"[codex-stage3] local pending task: {task_id}", flush=True)
        _emit_wake(dispatch, path)
    return pending


def drain_once(consume: bool = True, ack: bool = True) -> list[dict[str, Any]]:
    """Drain the HTTP inbox once.

    This is only a startup/reconnect recovery path. SSE is the live receive path.
    """
    tasks = fetch_tasks(consume=consume)
    state = _read_json(STATE_FILE, {})
    state["last_drain_at"] = datetime.now(timezone.utc).isoformat()
    state["last_count"] = len(tasks)
    state["orchestrator"] = ORCH
    _write_json(STATE_FILE, state)

    if ack:
        for task in tasks:
            ack_task(task)
    return tasks


def poll_once(consume: bool = True, ack: bool = True) -> list[dict[str, Any]]:
    """Compatibility helper for manual troubleshooting; not the primary receive loop."""
    tasks = drain_once(consume=consume, ack=ack)
    if ack:
        emit_wake_for_pending_files()
    return tasks


def stream_forever() -> None:
    name = urllib.parse.quote(AGENT_NAME)
    url = f"{ORCH}/agent/{name}/stream"

    # First consume pending tasks so restart does not miss work.
    try:
        drain_once(consume=True, ack=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[codex-stage3] startup drain failed: {exc}", file=sys.stderr)

    # Re-emit wake signals for tasks already persisted locally before this run.
    emit_wake_for_pending_files()

    while True:
        print(f"[codex-stage3] subscribing {url}")
        try:
            with urllib.request.urlopen(url, timeout=None) as stream:
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
            try:
                drain_once(consume=True, ack=True)
                emit_wake_for_pending_files()
            except Exception as drain_exc:  # noqa: BLE001
                print(f"[codex-stage3] reconnect drain failed: {drain_exc}", file=sys.stderr)
            time.sleep(3)


def cmd_report(task_id: str, text: str, tool: str, tool_input: str) -> int:
    if not task_id:
        print("missing --task-id", file=sys.stderr)
        return 2
    if not text:
        print("missing --text", file=sys.stderr)
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
        print(f"inbox task not found: {task_id}", file=sys.stderr)
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
    parser.add_argument("--scan-local-inbox", action="store_true", help="Emit wake signals for unfinished local inbox tasks and exit")
    parser.add_argument("--peek", action="store_true", help="Fetch pending tasks without consuming or ACKing")
    parser.add_argument("--stream", action="store_true", help="Subscribe to task stream forever")
    parser.add_argument("--report", action="store_true", help="POST progress/final text to /ingest")
    parser.add_argument("--complete", action="store_true", help="Mark a local inbox task DONE")
    parser.add_argument("--task-id", default="", help="Task id for --report/--complete")
    parser.add_argument("--text", default="", help="Report text for --report")
    parser.add_argument("--tool", default="", help="Optional tool field for /ingest")
    parser.add_argument("--tool-input", default="", help="Optional tool_input field for /ingest")
    args = parser.parse_args()

    if args.health:
        print(json.dumps(health(), ensure_ascii=False, indent=2))
        return 0
    if args.peek:
        print(json.dumps(fetch_tasks(consume=False), ensure_ascii=False, indent=2))
        return 0
    if args.scan_local_inbox:
        pending = emit_wake_for_pending_files()
        print(json.dumps({"pending_local_inbox": [str(p) for p in pending]}, ensure_ascii=False, indent=2))
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

