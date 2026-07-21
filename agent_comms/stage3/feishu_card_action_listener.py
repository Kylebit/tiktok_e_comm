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


def _parse_event(ev):
    """从事件中提取 (event_type, event_id, action_value)，兼容两种结构：
    - 飞书原始 OpenAPI：{header:{event_type, event_id}, event:{action:{value}}}
    - lark-cli 可能扁平化：{type, event_id, action_value}
    """
    etype = ev.get("type") or (ev.get("header") or {}).get("event_type")
    if etype != "card.action.trigger":
        return None, None, None
    eid = ev.get("event_id") or (ev.get("header") or {}).get("event_id")
    av = None
    if "action_value" in ev:
        av = ev.get("action_value")
    else:
        action = None
        ev_event = ev.get("event")
        if isinstance(ev_event, dict):
            action = ev_event.get("action")
        if not isinstance(action, dict) and isinstance(ev.get("action"), dict):
            action = ev.get("action")
        if isinstance(action, dict):
            av = action.get("value")
    return etype, eid, av


def _handle(ev):
    """处理一条 card.action.trigger 事件。"""
    etype, eid, av = _parse_event(ev)
    if etype is None:
        return
    # event_id 去重
    if eid:
        if eid in SEEN:
            return
        SEEN.add(eid)
        if len(SEEN) > SEEN_CAP:
            SEEN.clear()

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
    """后台排空 stderr 到日志文件，避免管道写满阻塞子进程。stderr 为二进制流。"""
    try:
        for raw in p.stderr:  # raw is bytes
            try:
                with open(LOG, "ab") as f:
                    f.write(b"[stderr] " + raw.rstrip(b"\r\n") + b"\n")
            except Exception:
                pass
    except Exception:
        pass


def run_once():
    cmd = _build_cmd()
    print("[listener] spawn: %s" % " ".join(cmd))
    logf = open(LOG, "ab", buffering=0)
    # 二进制模式读写：lark-cli 在中文 Windows 下输出 GBK，utf-8 解码会崩，
    # 故统一读 bytes，再 decode('utf-8','replace')，遇到 GBK 残留也能容错。
    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=logf,
        # 保持 stdin 打开（不 EOF），否则 event consume 无界运行会因 stdin EOF 优雅退出
        stdin=subprocess.PIPE,
        bufsize=0,
    )
    threading.Thread(target=_drain_stderr, args=(p,), daemon=True).start()
    for raw in p.stdout:
        line = raw.decode("utf-8", "replace").strip()
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
