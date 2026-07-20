# -*- coding: utf-8 -*-
"""
Orbit Cursor 接入新 frame 的 A2A 客户端（自主通道版）

【这是什么】
总控(CEO肉肉 / WorkBuddy)已弃用 EigenFlux，所有 agent 沟通走新 A2A+AG-UI frame，
大脑是 Orchestrator（默认 http://127.0.0.1:8773）。本文件让 Cursor 作为「干活的 worker」
通过新 frame 与总控【自主】沟通——收任务、回报进度，全程不需要 Boss 人工转发。

【安装】
1. 把本文件放到 Cursor 能跑 Python 的环境（与 Orchestrator 网络可达即可）。
2. 若 Orchestrator 不在本机，设置环境变量 ORCH_URL=http://<host>:8773
3. 运行：python cursor_adapter.py

【行为】
1. 先拉取收件箱里未消费的任务（/agent/Orbit%20Cursor/tasks?consume=1）
2. 订阅派发 SSE（/agent/Orbit%20Cursor/stream），实时接收总控派发的任务
3. 收到任务 -> 写入本地 cursor_inbox/ -> POST /ingest ACK -> 打印 AGENT_A2A_TICK_cursor
   （唤醒 Cursor 对话执行真实动作；本进程不假装 DONE）
4. Cursor 执行端完成后：python cursor_adapter.py report --task-id <id> --text "DONE：..."

【最终形态】
Cursor 与 CEO肉肉(总控) 经新 frame 自主沟通；Boss 仍能在飞书看到进度卡，
但不再需要他当传话筒。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AGENT_NAME = "Orbit Cursor"
ORCH = os.environ.get("ORCH_URL", "http://127.0.0.1:8773").rstrip("/")
HERE = Path(__file__).resolve().parent
INBOX = HERE / "cursor_inbox"
DONE_DIR = INBOX / "done"
WAKE_PREFIX = "AGENT_A2A_TICK_cursor"


def _ensure_dirs() -> None:
    INBOX.mkdir(parents=True, exist_ok=True)
    DONE_DIR.mkdir(parents=True, exist_ok=True)


def report(task_id: str, text: str, **extra):
    """向总控回报进度/完成（agent↔agent 出站）。"""
    payload = {"agent": AGENT_NAME, "task_id": task_id, "text": text}
    payload.update(extra)
    req = urllib.request.Request(
        ORCH + "/ingest",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        print("[cursor] 回报失败:", e, flush=True)
        return None


def _task_path(task_id: str) -> Path:
    return INBOX / f"{task_id}.json"


def save_dispatch(dispatch: dict) -> Path:
    _ensure_dirs()
    task_id = str(dispatch.get("task_id") or "").strip() or f"unknown_{int(time.time())}"
    path = _task_path(task_id)
    payload = dict(dispatch)
    payload["_received_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    payload["_status"] = "pending"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def mark_done(task_id: str) -> Path | None:
    _ensure_dirs()
    src = _task_path(task_id)
    if not src.is_file():
        return None
    data = json.loads(src.read_text(encoding="utf-8"))
    data["_status"] = "done"
    data["_done_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    dst = DONE_DIR / src.name
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    src.unlink(missing_ok=True)
    return dst


def list_pending() -> list[Path]:
    _ensure_dirs()
    return sorted(INBOX.glob("*.json"))


def execute(dispatch: dict) -> None:
    """收到总控派发：落盘 + ACK + 唤醒 Cursor 对话（不做假执行）。"""
    task_id = str(dispatch.get("task_id") or "").strip()
    prompt = str(dispatch.get("prompt") or dispatch.get("title") or "")
    if not task_id:
        print("[cursor] 忽略无 task_id 的派发:", dispatch, flush=True)
        return

    path = _task_path(task_id)
    if path.is_file():
        # 已存在（重复投递 / 重启恢复）：避免重复唤醒 Cursor 执行端
        print(f"[cursor] 跳过已存在的任务（去重）: {task_id}", flush=True)
        return
    path = save_dispatch(dispatch)
    print(f"\n[cursor] 收到任务 {task_id}", flush=True)
    print(f"        prompt: {prompt[:200]}", flush=True)
    print(f"        inbox:  {path}", flush=True)

    report(
        task_id,
        "ACK：Cursor 已接收，交由执行端处理（新 frame / cursor_inbox）",
    )

    # Cursor 对话侧用 notify_on_output 匹配此前缀并唤醒处理
    wake = {
        "prompt": (
            "A2A Orbit Cursor task: set ORCH_URL if needed (default http://127.0.0.1:8773); "
            f"read {path}; execute the task for real (no fake scrape); "
            "report progress via: python agent_comms/stage3/cursor_adapter.py report "
            f"--task-id {task_id} --text \"...\"; "
            "final text must contain DONE/待审核 or BLOCKED/阻塞; "
            f"then: python agent_comms/stage3/cursor_adapter.py complete --task-id {task_id}; "
            "do not contact the human user; report only to Orchestrator/CEO肉肉."
        ),
        "task_id": task_id,
        "path": str(path).replace("\\", "/"),
        "title": dispatch.get("title") or "",
    }
    print(f"{WAKE_PREFIX} {json.dumps(wake, ensure_ascii=False)}", flush=True)


def drain_pending() -> None:
    name_q = urllib.parse.quote(AGENT_NAME)
    try:
        with urllib.request.urlopen(
            ORCH + f"/agent/{name_q}/tasks?consume=1", timeout=10
        ) as r:
            data = json.loads(r.read().decode("utf-8"))
        for d in data.get("tasks") or []:
            execute(d)
    except Exception as e:  # noqa: BLE001
        print("[cursor] 拉取 pending 失败:", e, flush=True)


def subscribe_sse() -> None:
    name_q = urllib.parse.quote(AGENT_NAME)
    url = ORCH + f"/agent/{name_q}/stream"
    while True:
        print(f"[cursor] 订阅总控派发流: {url}", flush=True)
        try:
            with urllib.request.urlopen(url, timeout=None) as s:
                print("[cursor] 已连接总控 OK", flush=True)
                while True:
                    raw = s.readline()
                    if not raw:
                        print("[cursor] SSE 连接关闭，2s 后重连...", flush=True)
                        break
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    try:
                        d = json.loads(data_str)
                    except Exception:
                        continue
                    if d.get("type") == "connected":
                        continue
                    if "task_id" in d:
                        execute(d)
        except Exception as e:  # noqa: BLE001
            print("[cursor] SSE 断线:", e, flush=True)
        time.sleep(2)


def emit_wake_for_pending_files() -> None:
    """本地 pending 落盘任务也打一次唤醒（重启恢复 / 漏 notify 时）。"""
    for path in list_pending():
        try:
            dispatch = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print("[cursor] 读取 pending 失败:", path, e, flush=True)
            continue
        task_id = str(dispatch.get("task_id") or path.stem)
        if dispatch.get("_status") in ("done", "deferred"):
            continue
        wake = {
            "prompt": (
                "A2A Orbit Cursor task: set ORCH_URL if needed (default http://127.0.0.1:8773); "
                f"read {path}; execute the task for real (no fake scrape); "
                "report progress via: python agent_comms/stage3/cursor_adapter.py report "
                f"--task-id {task_id} --text \"...\"; "
                "final text must contain DONE/待审核 or BLOCKED/阻塞; "
                f"then: python agent_comms/stage3/cursor_adapter.py complete --task-id {task_id}; "
                "do not contact the human user; report only to Orchestrator/CEO肉肉."
            ),
            "task_id": task_id,
            "path": str(path).replace("\\", "/"),
            "title": dispatch.get("title") or "",
        }
        print(f"[cursor] 本地 pending 待执行: {task_id}", flush=True)
        print(f"{WAKE_PREFIX} {json.dumps(wake, ensure_ascii=False)}", flush=True)


def run() -> None:
    print("=== Orbit Cursor A2A 客户端（新 frame 自主通道）===", flush=True)
    print(f"=== 总控 Orchestrator: {ORCH} ===", flush=True)
    print(f"=== 本地 inbox: {INBOX} ===", flush=True)
    _ensure_dirs()
    try:
        with urllib.request.urlopen(ORCH + "/health", timeout=5) as r:
            print("[cursor] /health:", r.read().decode("utf-8", errors="replace"), flush=True)
    except Exception as e:  # noqa: BLE001
        print("[cursor] WARNING: Orchestrator /health 不可达:", e, flush=True)
    drain_pending()
    emit_wake_for_pending_files()
    subscribe_sse()


def cmd_report(args: argparse.Namespace) -> int:
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if not text:
        print("需要 --text 或 --file", file=sys.stderr)
        return 2
    extra = {}
    if args.tool:
        extra["tool"] = args.tool
    if args.tool_input:
        extra["tool_input"] = args.tool_input
    result = report(args.task_id, text, **extra)
    print(json.dumps(result or {"ok": False}, ensure_ascii=False))
    return 0 if result is not None else 1


def cmd_complete(args: argparse.Namespace) -> int:
    dst = mark_done(args.task_id)
    if not dst:
        print(f"pending 任务不存在: {args.task_id}", file=sys.stderr)
        return 1
    print(f"moved -> {dst}")
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    pending = list_pending()
    print(json.dumps([p.name for p in pending], ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Orbit Cursor A2A adapter")
    sub = parser.add_subparsers(dest="cmd")

    p_report = sub.add_parser("report", help="POST /ingest 回报总控")
    p_report.add_argument("--task-id", required=True)
    p_report.add_argument("--text", default="")
    p_report.add_argument("--file", default="")
    p_report.add_argument("--tool", default="")
    p_report.add_argument("--tool-input", default="")
    p_report.set_defaults(func=cmd_report)

    p_done = sub.add_parser("complete", help="把 inbox 任务移到 done/")
    p_done.add_argument("--task-id", required=True)
    p_done.set_defaults(func=cmd_complete)

    p_list = sub.add_parser("list", help="列出 pending inbox")
    p_list.set_defaults(func=cmd_list)

    p_run = sub.add_parser("run", help="常驻：拉 pending + 订阅 SSE（默认）")
    p_run.set_defaults(func=lambda _a: (run() or 0))

    args = parser.parse_args()
    if not args.cmd:
        run()
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
