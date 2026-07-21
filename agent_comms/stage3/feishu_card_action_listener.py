# -*- coding: utf-8 -*-
"""
飞书卡按钮回调监听器（Stage3）
===========================
常驻进程：订阅飞书 `card.action.trigger` 事件（用户在「任务总览」卡上点按钮），
把动作回灌到 Orchestrator（8773）更新台账，从而实现「在飞书里直接审核通过 / 归档」。

- 用户点「✅ 审核通过」 -> 调 POST /approve  -> 台账翻绿、卡自动重绘。
- 用户点「🗄 归档」     -> 调 POST /archive  -> 任务移出活跃视图、停止催办。

因为动作经由 Orchestrator 统一处理，天然就「通知到我（总控）」：台账更新、卡重绘、
时间线记录全经过我，状态不再脱节。

依赖：
- lark-cli（bot 身份）。
- 飞书开放平台「应用 -> 事件与回调 -> 回调配置」已开启（否则 event consume 能启动但收不到事件）。
- 仅处理 action_value 结构为 {"action": "approve"|"archive", "task_id": "..."} 的事件，
  不会误伤聊天里其它卡片的按钮。
"""
import os
import sys
import json
import time
import subprocess
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_PR = os.path.abspath(os.path.join(HERE, "..", "..", ".."))  # Desktop/Agent_PR
for p in [AGENT_PR]:
    if p not in sys.path:
        sys.path.insert(0, p)

import task_card  # 复用 LARK / _NODE / _RUN，绕开 cmd.exe 对 & < > | 的元字符截断

ORCH_API = "http://127.0.0.1:8773"
SEEN = set()          # event_id 内存去重，防止重复投递重复处理
SEEN_CAP = 5000
LOG_DIR = os.path.join(HERE, "logs")
LOG = os.path.join(LOG_DIR, "feishu_card_action.log")


def _build_cmd():
    """构造 event consume 命令；与 task_card 一致走 node 直调规避 cmd.exe 元字符。"""
    raw = [task_card.LARK, "event", "consume", "card.action.trigger", "--as", "bot"]
    if raw and raw[0] == task_card.LARK:
        return [task_card._NODE, task_card._RUN] + raw[1:]
    return raw


def _call_orch(path, payload):
    req = urllib.request.Request(
        ORCH_API + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _handle(ev):
    """处理一条 card.action.trigger 事件。"""
    if ev.get("type") != "card.action.trigger":
        return
    # event_id 去重
    eid = ev.get("event_id")
    if eid:
        if eid in SEEN:
            return
        SEEN.add(eid)
        if len(SEEN) > SEEN_CAP:
            SEEN.clear()

    av = ev.get("action_value")
    if isinstance(av, str):
        try:
            val = json.loads(av)
        except Exception:
            return
    elif isinstance(av, dict):
        val = av
    else:
        return
    if not isinstance(val, dict):
        return

    action = val.get("action")
    task_id = val.get("task_id")
    if not task_id or action not in ("approve", "archive"):
        return

    try:
        if action == "approve":
            res = _call_orch("/approve", {"task_id": task_id})
            print("[action] approve %s -> %s" % (task_id, res.get("status")))
        else:
            res = _call_orch("/archive", {"task_id": task_id})
            print("[action] archive %s -> %s" % (task_id, res.get("status")))
    except Exception as e:
        print("[action] ERROR handling %s/%s: %s" % (action, task_id, e))


def _drain_stderr(p):
    """后台排空 stderr 到日志文件，避免管道写满阻塞子进程。"""
    try:
        for line in p.stderr:
            with open(LOG, "ab") as f:
                f.write(("[stderr] " + line).encode("utf-8", "replace"))
    except Exception:
        pass


def run_once():
    cmd = _build_cmd()
    print("[listener] spawn: %s" % " ".join(cmd))
    logf = open(LOG, "ab", buffering=0)
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=logf,
        # 保持 stdin 打开（不 EOF），否则 event consume 无界运行会因 stdin EOF 优雅退出
        stdin=subprocess.PIPE,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )
    threading.Thread(target=_drain_stderr, args=(p,), daemon=True).start()
    for line in p.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        try:
            _handle(ev)
        except Exception as e:
            print("[listener] handle error: %s" % e)
    rc = p.wait()
    print("[listener] event consume exited rc=%s" % rc)


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    print(">>> [stage3] 飞书卡按钮回调监听器启动 (orch=%s)" % ORCH_API)
    while True:
        t0 = time.time()
        try:
            run_once()
        except Exception as e:
            print("[listener] fatal: %s" % e)
        elapsed = time.time() - t0
        # 若子进程很快退出（多半是「回调未订阅」），拉长等待避免刷屏；
        # 订阅就绪后进程会常驻，退出即按 5s 快速重连。
        wait = 30 if elapsed < 10 else 5
        print("[listener] %ds 后重连 ..." % wait)
        time.sleep(wait)


if __name__ == "__main__":
    main()
