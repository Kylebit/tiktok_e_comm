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
import shlex
import shutil
import subprocess
import sys
import threading
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

# --- 无头执行后端 ----------------------------------------------------------
# 根因：Cursor 的 GUI agent 在本环境无法被外部事件驱动（无 `cursor exec`、
# 且 `notify_on_output` 从未配置；adapter 又是 DETACHED 进程，tick 到不了
# Cursor 终端）。为让 "Orbit Cursor" 槽位真正自主回复，复用已验证的
# `codex exec` 无头引擎执行任务，但由本适配器独占管理身份/收件箱/回报。
def _detect_codex():
    _hard = [
        r"C:\Users\Windows11\.workbuddy\binaries\node\versions\22.22.2\codex.cmd",
        r"C:\Users\Windows11\.workbuddy\binaries\node\versions\22.22.2\codex.ps1",
    ]
    for c in _hard:
        if os.path.exists(c):
            return c
    for c in ("codex.cmd", "codex.ps1", "codex"):
        p = shutil.which(c)
        if p:
            if p.startswith("/") and len(p) > 2 and p[2] == "/":
                p = p[1].upper() + ":\\" + p[3:].replace("/", "\\")
            return p
    return "codex"


def _detect_bash():
    for b in (r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files\Git\usr\bin\bash.exe"):
        if os.path.exists(b):
            return b
    return shutil.which("bash") or "bash"


CODEX_BIN = _detect_codex()
BASH_BIN = _detect_bash()
REPO = r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm"
# 设 CURSOR_HEADLESS_EXEC=0 可退回纯桥接模式（留给未来接真实 GUI Cursor）
HEADLESS = os.environ.get("CURSOR_HEADLESS_EXEC", "1") != "0"


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


def set_status(task_id: str, status: str) -> None:
    """更新 inbox 任务状态，并累计执行尝试次数（防重启无限重跑）。"""
    p = _task_path(task_id)
    if not p.is_file():
        return
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        d["_status"] = status
        d["_exec_attempts"] = int(d.get("_exec_attempts", 0)) + 1
        d["_last_attempt_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print("[cursor] set_status 失败:", task_id, e, flush=True)


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

    # --- 无头执行后端：直接复用 codex exec 引擎真正执行任务 ---
    if HEADLESS:
        set_status(task_id, "executing")
        threading.Thread(target=run_headless, args=(task_id, prompt), daemon=True).start()
        print(f"[cursor] 已派无头执行引擎处理 {task_id}", flush=True)
    else:
        print("[cursor] 纯桥接模式：等待 Cursor GUI notify_on_output 唤醒（未配置则不会自跑）", flush=True)


def run_headless(task_id: str, prompt: str) -> None:
    """无头执行 Cursor 任务：复用 codex exec 引擎，回报走 Orbit Cursor 身份。

    由于本环境无法事件驱动 Cursor GUI agent，这里用已验证的 codex exec
    作为执行引擎真正完成任务；任务文件在 cursor_inbox/<id>.json，Codex 读
    dispatch.prompt 执行后通过 cursor_adapter.py 回报（而非 codex_adapter）。
    """
    try:
        set_status(task_id, "executing")
        full = (
            f"请读取文件 agent_comms/stage3/cursor_inbox/{task_id}.json 中的 dispatch.prompt 字段，"
            f"那就是你要执行的任务要求（任务由 Orbit Cursor 派发，执行引擎为无头 codex）。\n"
            f"在仓库 {REPO} 中真实执行（调研/写代码/跑命令/git 等）。任务通常要求产出某个 reports/*.md 并 git add/commit/push。\n"
            f"完成后必须运行以下命令回报给 Orbit Cursor（不要调用 codex_adapter）：\n"
            f"  python agent_comms/stage3/cursor_adapter.py report --task-id {task_id} --text \"DONE：<一句话简述>\"\n"
            f"  python agent_comms/stage3/cursor_adapter.py complete --task-id {task_id}\n"
            f"绝对不要伪造 DONE / commit hash / 测试通过。若确实无法完成，仍运行 report 写明 BLOCKED 及原因，并运行 complete。"
        )
        inner = "codex exec --sandbox workspace-write " + shlex.quote(full)
        cmd = [BASH_BIN, "-lc", inner]
        print(f"[cursor] 无头执行启动 task={task_id} (codex={CODEX_BIN})", flush=True)
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=1800)
        print(f"[cursor] 无头执行结束 task={task_id} rc={r.returncode}", flush=True)
        # 兜底：若 codex 未自行 complete，确保任务进入终态
        if _task_path(task_id).is_file():
            report(task_id, "BLOCKED/兜底：codex 未调用 complete，适配器兜底收尾")
            mark_done(task_id)
            print(f"[cursor] 兜底收尾 task={task_id}", flush=True)
    except subprocess.TimeoutExpired:
        print(f"[cursor] 无头执行超时 task={task_id}", flush=True)
        if _task_path(task_id).is_file():
            report(task_id, "BLOCKED：无头执行超时(>30min)")
            mark_done(task_id)
    except Exception as e:  # noqa: BLE001
        print(f"[cursor] 无头执行异常 task={task_id}: {e}", flush=True)
        if _task_path(task_id).is_file():
            try:
                report(task_id, f"BLOCKED：无头执行异常 {e}")
                mark_done(task_id)
            except Exception:
                pass


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
    """启动恢复：把本地仍 pending/executing 的落盘任务重新接上无头执行。"""
    for path in list_pending():
        try:
            dispatch = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print("[cursor] 读取 pending 失败:", path, e, flush=True)
            continue
        task_id = str(dispatch.get("task_id") or path.stem)
        status = dispatch.get("_status")
        attempts = int(dispatch.get("_exec_attempts", 0))
        if status in ("done", "deferred"):
            continue
        if attempts >= 3:
            print(f"[cursor] 跳过 {task_id}：已达最大重试({attempts})，需人工排查", flush=True)
            continue
        if HEADLESS:
            set_status(task_id, "executing")
            threading.Thread(target=run_headless, args=(task_id, ""), daemon=True).start()
            print(f"[cursor] 启动恢复：重新派无头执行 {task_id} (attempt {attempts + 1})", flush=True)
        else:
            wake = {
                "prompt": (
                    "A2A Orbit Cursor task: set ORCH_URL if needed (default http://127.0.0.1:8773); "
                    f"read {path}; execute the task for real (no fake scrape); "
                    "report progress via: python agent_comms/stage3/cursor_adapter.py report "
                    f"--task-id {task_id} --text \"...\"; "
                    "final text must contain DONE/待审核 or BLOCKED/阻塞; "
                    f"then: python agent_comms/stage3/cursor_adapter.py complete --task_id {task_id}; "
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
