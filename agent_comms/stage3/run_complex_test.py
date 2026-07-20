# -*- coding: utf-8 -*-
"""
阶段3 复杂工作流测试驱动。

说明：本脚本演示「新 frame 自主通道」全链路——dispatch(mode=direct) 不依赖 Boss 转发，
经 SSE 直达 worker 收件箱，worker 桥接进程 ACK 回 Orchestrator，飞书卡实时刷新；
最后 Boss「审核通过」由总控调 /approve 把卡片翻成 🟢 已完成（关闭）。

worker 真正「干活+回报」这一步在本测试里为【模拟】（用户原话：模拟出一条复杂工作流）；
dispatch / SSE 投递 / 收件箱 / ACK / 飞书卡 / 审核关闭 均为真实链路。

用法：
  python run_complex_test.py dispatch    # 派 3 个任务（2 给 Cursor 含依赖，1 给 Codex 并行），写 ids 到 test_task_ids.json
  python run_complex_test.py simulate     # 模拟各 worker 回报（DONE/待审核），卡片翻 🟣 待审核
  python run_complex_test.py approve      # 模拟 Boss 审核通过 -> 卡片翻 🟢 已完成（关闭）
  python run_complex_test.py status       # 打印当前 3 个测试任务状态
"""
import json
import os
import sys
import urllib.parse
import urllib.request

ORCH = os.environ.get("ORCH_URL", "http://127.0.0.1:8773").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
IDS_FILE = os.path.join(HERE, "test_task_ids.json")

TASKS = [
    ("Orbit Cursor", "报告你的身份与工作上下文（你是谁/角色/可访问仓库与工具/当前状态）"),
    ("Orbit Cursor", "基于你的上下文，检查 tiktok_e_comm 的 Ozon 结算页是否显示下单/结算日期，回报结论"),
    ("Orbit Codex", "在 tiktok_e_comm 跑一次 py_compile 自检，回报结果"),
]

# 模拟各 worker 的回报内容（仅「干活」这步为模拟；回报动作本身真实打到 /ingest）
SIM = [
    ("Orbit Cursor",
     "DONE：我是 Orbit Cursor（TK MX/UK 子 agent / worker）。可访问 tiktok_e_comm 仓库、"
     "1688/妙手采集、TikTok 上架链路；已接入新 A2A frame，经 /agent/Orbit%20Cursor/stream "
     "自主收任务。身份与上下文已回报，待审核"),
    ("Orbit Cursor",
     "DONE：已检查 Ozon 结算页（web/ozon.html），「下单日期」「结算日期」两列均已渲染，"
     "且只显示已结算订单。结论符合预期，待审核"),
    ("Orbit Codex",
     "DONE：py_compile 自检通过（tiktok_e_comm 下源码全部编译 OK），待审核"),
]


def _post(path, payload):
    req = urllib.request.Request(
        ORCH + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(path):
    with urllib.request.urlopen(ORCH + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def cmd_dispatch():
    ids = []
    for i, (agent, prompt) in enumerate(TASKS):
        r = _post("/dispatch", {
            "assignee": agent, "prompt": prompt, "mode": "direct",
            "title": "测试%d: %s" % (i + 1, prompt[:18]),
        })
        ids.append([r["task_id"], agent])
        print("  dispatch #%d -> %-12s task_id=%s status=%s"
              % (i + 1, agent, r["task_id"], r.get("status")))
    with open(IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False, indent=2)
    print("已写入 %s" % IDS_FILE)
    return ids


def _load_ids():
    with open(IDS_FILE, encoding="utf-8") as f:
        return json.load(f)


def cmd_simulate():
    ids = _load_ids()
    for (tid, agent), (_, text) in zip(ids, SIM):
        r = _post("/ingest", {"agent": agent, "task_id": tid, "text": text})
        print("  simulate report -> %s (%s) status=%s" % (tid, agent, r.get("status")))


def cmd_approve():
    ids = _load_ids()
    for tid, agent in ids:
        r = _post("/approve", {"task_id": tid, "agent": agent})
        print("  approve -> %s (%s) status=%s" % (tid, agent, r.get("status")))


def cmd_status():
    ids = _load_ids()
    data = _get("/tasks")
    tasks = data if isinstance(data, list) else data.get("tasks", [])
    by_id = {t["task_id"]: t for t in tasks}
    for tid, agent in ids:
        t = by_id.get(tid, {})
        # 从 history 取最新状态描述
        hist = t.get("history") or []
        last = hist[-1].get("text", "") if hist else ""
        print("  %s (%s) -> status=%s | last=%s"
              % (tid, agent, t.get("status"), last[:60]))


if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "dispatch"
    {"dispatch": cmd_dispatch, "simulate": cmd_simulate,
     "approve": cmd_approve, "status": cmd_status}[c]()
