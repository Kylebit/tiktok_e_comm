# -*- coding: utf-8 -*-
"""后台监控: 等 linkfox-poc-codex / linkfox-poc-cursor 走到 done 且片段报告产出。"""
import json, time, os
from pathlib import Path

BASE = Path(".")
CURSOR_IN = BASE / "agent_comms/stage3/cursor_inbox"
CURSOR_DONE = CURSOR_IN / "done"
CODEX_IN = BASE / "agent_comms/stage3/codex_inbox"
CODEX_DONE = CODEX_IN / "done"

TASKS = {
    "linkfox-poc-codex": {
        "inbox": CODEX_IN, "done": CODEX_DONE,
        "frag": BASE / "reports/linkfox_poc_frag_codex.md",
    },
    "linkfox-poc-cursor": {
        "inbox": CURSOR_IN, "done": CURSOR_DONE,
        "frag": BASE / "reports/linkfox_poc_frag_cursor.md",
    },
}


def done(t):
    c = TASKS[t]
    moved = (c["done"] / f"{t}.json").is_file()
    gone = not (c["inbox"] / f"{t}.json").is_file()
    frag = c["frag"].is_file()
    return (moved or gone) and frag


deadline = time.time() + 20 * 60
while time.time() < deadline:
    states = {}
    for t in TASKS:
        c = TASKS[t]
        inbox_f = c["inbox"] / f"{t}.json"
        status = "?"
        if (c["done"] / f"{t}.json").is_file():
            status = "DONE"
        elif inbox_f.is_file():
            try:
                status = json.loads(inbox_f.read_text(encoding="utf-8")).get("_status", "?") if "cursor" in t else \
                         json.loads(inbox_f.read_text(encoding="utf-8")).get("status", "?")
            except Exception:
                status = "?"
        states[t.split("-")[-1]] = f"{status}|frag={'Y' if c['frag'].is_file() else 'N'}"
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] " + "  ".join(f"{k}={v}" for k, v in states.items()), flush=True)
    if all(done(t) for t in TASKS):
        print(f"[{ts}] ALL DONE — 两 agent 片段均已产出，可合并。", flush=True)
        break
    time.sleep(30)
else:
    print("[TIMEOUT] 20min 内未全部完成，需人工排查。", flush=True)
