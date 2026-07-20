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
3. 收到任务 -> execute() 执行 -> 通过 POST /ingest 回报进度/完成

【最终形态】
Cursor 与 CEO肉肉(总控) 经新 frame 自主沟通；Boss 仍能在飞书看到进度卡，
但不再需要他当传话筒。
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

AGENT_NAME = "Orbit Cursor"
ORCH = os.environ.get("ORCH_URL", "http://127.0.0.1:8773").rstrip("/")


def report(task_id, text, **extra):
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
        print("[cursor] 回报失败:", e)
        return None


def execute(dispatch):
    """执行任务。

    真实接入时，把下面这段占位逻辑替换成 Cursor 的实际动作
    （例如调用你的工具、改代码、跑命令），并在关键节点调用 report() 回报。
    """
    task_id = dispatch.get("task_id")
    prompt = dispatch.get("prompt", "")
    print("\n[cursor] 收到任务 %s：" % task_id)
    print("        %s" % prompt)

    report(task_id, "Cursor 已开始执行：" + prompt[:40])
    time.sleep(0.4)

    # 模拟一次工具调用（真实接入替换为你的工具调用）
    report(task_id, "Cursor 调用工具 scrape_1688 完成",
           tool="scrape_1688", tool_input=prompt[:60])
    time.sleep(0.4)

    # 终态：text 含「待审核/DONE」= 完成、待 Boss 审核
    report(task_id, "DONE：已完成抓取与预览生成，产物待审核")
    print("[cursor] 已回报完成 ✅ (task %s)" % task_id)


def run():
    name_q = urllib.parse.quote(AGENT_NAME)

    # 1) 先消费收件箱里未拉取的任务
    try:
        with urllib.request.urlopen(ORCH + "/agent/%s/tasks?consume=1" % name_q, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        for d in data.get("tasks", []):
            execute(d)
    except Exception as e:  # noqa: BLE001
        print("[cursor] 拉取 pending 失败:", e)

    # 2) 订阅派发 SSE，实时接收（readline 逐行 + 断线自动重连，兼容 Windows）
    url = ORCH + "/agent/%s/stream" % name_q
    while True:
        print("[cursor] 订阅总控派发流: %s" % url)
        try:
            with urllib.request.urlopen(url) as s:
                print("[cursor] 已连接总控 ✅")
                while True:
                    raw = s.readline()
                    if not raw:
                        print("[cursor] SSE 连接关闭，2s 后重连...")
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
            print("[cursor] SSE 断线:", e)
        time.sleep(2)


if __name__ == "__main__":
    print("=== Orbit Cursor A2A 客户端（新 frame 自主通道）===")
    print("=== 总控 Orchestrator: %s ===" % ORCH)
    run()
