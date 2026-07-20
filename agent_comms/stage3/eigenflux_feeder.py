# -*- coding: utf-8 -*-
"""
阶段3 feeder：替代旧监听器（eigenflux_stream_listener.py）的「抓 worker 回报 -> 推飞书」职责。

旧链路：eigenflux stream -> 直接 append_progress + 推聊天摘要 + render_and_push(进度卡)
新链路（本文件）：eigenflux stream -> POST /ingest 给 Orchestrator
                                      -> Orchestrator 发 AG-UI 事件
                                      -> adapter_runner 订阅并推【新 frame 进度卡】
即：进度卡这一核心可视性，完全改由新 frame 驱动；旧监听器已下线（代码保留回滚）。

为不丢失「派发约定：记录进展到飞书主任务表」这条审计要求，本 feeder 仍会把
worker 回报首行追加到对应任务的「进展记录」字段（复用 fb_progress）。

运行：python eigenflux_feeder.py   （需 Orchestrator 8773 已起）
"""
import json
import os
import re
import sys
import threading
import time
import subprocess
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_COMMS = os.path.dirname(HERE)
TIKTOK = os.path.dirname(AGENT_COMMS)
AGENT_PR = os.path.dirname(TIKTOK)
for p in [AGENT_PR, AGENT_COMMS, HERE]:
    if p not in sys.path:
        sys.path.insert(0, p)

import task_card  # noqa: E402  (TASK_MAP: task_no -> feishu record)
import fb_progress  # noqa: E402  (append_progress: 记录进展到飞书主任务表)

BIN = r"C:\Users\Windows11\AppData\Local\EigenFlux\bin\eigenflux.exe"
HOME = r"C:\Users\Windows11\.eigenflux-workbuddy\.eigenflux"
ORCH_URL = os.environ.get("ORCH_URL", "http://127.0.0.1:8773/ingest")
WORKERS = {"Orbit Codex", "Orbit Cursor"}
MAPFILE = os.path.join(HERE, "feeder_task_map.json")

# ORB-TASK-XXXX -> orchestrator task_id（持久化，跨重启保留上下文）
TASK_MAP = {}


def _load_map():
    global TASK_MAP
    try:
        with open(MAPFILE, "r", encoding="utf-8") as f:
            TASK_MAP = json.load(f) or {}
    except Exception:
        TASK_MAP = {}


def _save_map():
    try:
        with open(MAPFILE, "w", encoding="utf-8") as f:
            json.dump(TASK_MAP, f, ensure_ascii=False)
    except Exception:
        pass


def _extract_task(content):
    m = re.search(r"ORB-TASK-\d+", content or "")
    return m.group(0) if m else ""


def _now():
    return time.strftime("%Y-%m-%d %H:%M")


def _first_line(content):
    c = (content or "").strip()
    first = c.splitlines()[0] if c else ""
    return first[:220]


def ingest(agent, content):
    """把一个 worker 回报灌入 Orchestrator（新 frame 驱动飞书卡）。

    返回：(orchestrator_task_id, feishu_record)
    """
    task = _extract_task(content)
    task_no = task.replace("ORB-TASK-", "") if task else ""
    rec_id = task_card.TASK_MAP.get(task_no) if task_no else None

    # 解析/复用该 ORB 任务对应的 orchestrator task_id，避免每次汇报都建新卡
    orch_tid = TASK_MAP.get(task_no) if task_no else None

    body = {
        "agent": agent,
        "text": content,
        "title": task_no or agent,
        "feishu_record": rec_id,
    }
    if orch_tid:
        body["task_id"] = orch_tid

    try:
        req = urllib.request.Request(
            ORCH_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode("utf-8"))
        new_tid = out.get("task_id")
        if new_tid and new_tid != orch_tid:
            if task_no:
                TASK_MAP[task_no] = new_tid
                _save_map()
        print("   [feeder] -> /ingest ok task=%s orch=%s rec=%s" % (task_no or "-", new_tid, rec_id), flush=True)
    except Exception as e:
        print("   [feeder] /ingest failed: %s" % e, flush=True)
        return None, rec_id

    # 保留审计：把首行追加到飞书主任务表「进展记录」（派发约定）
    if rec_id and task_no:
        try:
            line = "[%s][%s] %s" % (_now(), agent, _first_line(content))
            fb_progress.append_progress(rec_id, line)
        except Exception as e:
            print("   [feeder] append_progress failed: %s" % e, flush=True)

    return new_tid, rec_id


def _handle_pm_push(data):
    msgs = data.get("messages", []) if isinstance(data, dict) else []
    for m in msgs:
        name = m.get("sender_name") or ""
        if name not in WORKERS:
            continue
        c = m.get("content", "")
        ingest(name, c)


def run_stream():
    proc = subprocess.Popen(
        [BIN, "stream", "--format", "json", "--homedir", HOME],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    print("[feeder] eigenflux stream started pid=%s" % proc.pid, flush=True)

    def stderr_reader():
        try:
            for raw in proc.stderr:
                line = raw.decode("utf-8", errors="ignore")
                if "401" in line or "unauthorized" in line.lower():
                    print("[feeder] EigenFlux 登录过期，停止", flush=True)
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    return
        except Exception:
            pass

    threading.Thread(target=stderr_reader, daemon=True).start()

    for raw in proc.stdout:
        line = raw.decode("utf-8", errors="ignore").strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "pm_push":
            _handle_pm_push(ev.get("data", {}))
    return proc.returncode


def main():
    _load_map()
    print("[feeder] 启动：抓取 EigenFlux worker 回报 -> Orchestrator %s（新 frame 驱动飞书卡）" % ORCH_URL, flush=True)
    while True:
        try:
            rc = run_stream()
            print("[feeder] stream exited rc=%s, 5s 后重连" % rc, flush=True)
            time.sleep(5)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("[feeder] loop error: %s, 10s 后重连" % e, flush=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
