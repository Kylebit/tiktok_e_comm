# -*- coding: utf-8 -*-
"""
Orbit Helper —— CEO 肉肉的「后台只读子Agent」文件信箱客户端。

定位（方案二）：
- 这是一个服务 CEO 肉肉（总控）的**只读**助手，跑在一个独立的 WorkBuddy 窗口里。
- 职责：读代码 / 查任务状态 / 起草报告等「读」类杂活，替 CEO 分担串行开销。
- 它**不**走 Boss 面向的 Orchestrator(8773)/飞书卡通道，避免污染 Boss 视图；
  改用本地文件信箱，属于 CEO<->Helper 的内部通道。

红线（Helper 必须遵守）：
- 只读：绝不改代码、不 git commit/push、不派发任务、不写项目记忆、不联系 Boss/飞书。
- 需要写代码/提交的活，一律回报 BLOCKED，说明应转交 Codex/Cursor。

通道目录（均在本文件同级）：
- inbox/    CEO 写入待办任务（<task_id>.json）
- working/  Helper 认领后移入（避免重复认领）
- outbox/   Helper 写回结果（<task_id>.json，CEO 读取）
- done/     结果回报后归档的任务

两端命令：
  CEO 侧：
    dispatch --title "..." --prompt "..."     派活（自动分配 H0001 编号，写 inbox）
    collect [--consume]                        收取 outbox 里的全部回报（--consume 收后清理）
  Helper 侧：
    wait [--timeout 480] [--poll 3]            阻塞等待下一条任务；有则打印并认领，超时打印 idle
    report --task-id H0001 --status done --text "..." [--file report.html]
                                               写回结果到 outbox
    peek                                       查看当前 inbox/working 概况（调试用）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
INBOX = HERE / "inbox"
WORKING = HERE / "working"
OUTBOX = HERE / "outbox"
DONE = HERE / "done"
SEQ_FILE = HERE / ".helper_seq.json"

for d in (INBOX, WORKING, OUTBOX, DONE):
    d.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _next_seq() -> str:
    n = int(_read_json(SEQ_FILE, {}).get("seq", 0)) + 1
    _write_json(SEQ_FILE, {"seq": n})
    return "H%04d" % n


# ----------------------------- CEO 侧 -----------------------------
def cmd_dispatch(title: str, prompt: str, task_id: str = "") -> int:
    if not prompt:
        print("missing --prompt", file=sys.stderr)
        return 2
    tid = task_id or _next_seq()
    payload = {
        "task_id": tid,
        "title": title or prompt[:40],
        "prompt": prompt,
        "created_at": _now(),
        "from": "CEO 肉肉",
        "to": "Orbit Helper",
    }
    _write_json(INBOX / f"{tid}.json", payload)
    print(json.dumps({"ok": True, "dispatched": tid, "inbox": str(INBOX / f"{tid}.json")}, ensure_ascii=False))
    return 0


def cmd_collect(consume: bool = False) -> int:
    reports = []
    for path in sorted(OUTBOX.glob("*.json")):
        rep = _read_json(path, None)
        if rep is None:
            continue
        rep["_file"] = str(path)
        reports.append(rep)
        if consume:
            try:
                path.unlink()
            except Exception:
                pass
    print(json.dumps({"ok": True, "count": len(reports), "reports": reports}, ensure_ascii=False, indent=2))
    return 0


# ---------------------------- Helper 侧 ----------------------------
def _claim(path: Path) -> Path | None:
    """把 inbox 文件原子移到 working，认领成功返回新路径，失败(被别人抢走)返回 None。"""
    target = WORKING / path.name
    try:
        os.replace(path, target)
        return target
    except Exception:
        return None


def _pick_one() -> dict[str, Any] | None:
    for path in sorted(INBOX.glob("*.json")):
        claimed = _claim(path)
        if claimed is None:
            continue
        task = _read_json(claimed, {})
        task["_working_file"] = str(claimed)
        return task
    return None


def cmd_wait(timeout: int, poll: int) -> int:
    deadline = time.time() + max(1, timeout)
    while True:
        task = _pick_one()
        if task is not None:
            print(json.dumps({"idle": False, "task": task}, ensure_ascii=False))
            return 0
        if time.time() >= deadline:
            print(json.dumps({"idle": True, "hint": "no task; re-run wait to keep listening"}, ensure_ascii=False))
            return 0
        time.sleep(max(1, poll))


def cmd_report(task_id: str, status: str, text: str, file: str = "") -> int:
    if not task_id:
        print("missing --task-id", file=sys.stderr)
        return 2
    status = (status or "done").lower()
    payload = {
        "task_id": task_id,
        "status": status,  # done | progress | blocked
        "text": text or "",
        "report_file": file or "",
        "reported_at": _now(),
        "from": "Orbit Helper",
        "to": "CEO 肉肉",
    }
    _write_json(OUTBOX / f"{task_id}.json", payload)
    # 终态则把 working 里的任务归档到 done
    if status in ("done", "blocked"):
        wf = WORKING / f"{task_id}.json"
        if wf.is_file():
            try:
                os.replace(wf, DONE / f"{task_id}.json")
            except Exception:
                pass
    print(json.dumps({"ok": True, "reported": task_id, "status": status}, ensure_ascii=False))
    return 0


def cmd_peek() -> int:
    inbox = [p.name for p in sorted(INBOX.glob("*.json"))]
    working = [p.name for p in sorted(WORKING.glob("*.json"))]
    outbox = [p.name for p in sorted(OUTBOX.glob("*.json"))]
    print(json.dumps({"inbox": inbox, "working": working, "outbox": outbox}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Orbit Helper file-mailbox client")
    sub = ap.add_subparsers(dest="cmd")

    d = sub.add_parser("dispatch", help="[CEO] 派活")
    d.add_argument("--title", default="")
    d.add_argument("--prompt", default="")
    d.add_argument("--task-id", default="")

    c = sub.add_parser("collect", help="[CEO] 收回报")
    c.add_argument("--consume", action="store_true")

    w = sub.add_parser("wait", help="[Helper] 等下一条任务")
    w.add_argument("--timeout", type=int, default=480)
    w.add_argument("--poll", type=int, default=3)

    r = sub.add_parser("report", help="[Helper] 回报结果")
    r.add_argument("--task-id", default="")
    r.add_argument("--status", default="done")
    r.add_argument("--text", default="")
    r.add_argument("--file", default="")

    sub.add_parser("peek", help="查看信箱概况")

    args = ap.parse_args()
    if args.cmd == "dispatch":
        return cmd_dispatch(args.title, args.prompt, args.task_id)
    if args.cmd == "collect":
        return cmd_collect(args.consume)
    if args.cmd == "wait":
        return cmd_wait(args.timeout, args.poll)
    if args.cmd == "report":
        return cmd_report(args.task_id, args.status, args.text, args.file)
    if args.cmd == "peek":
        return cmd_peek()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
