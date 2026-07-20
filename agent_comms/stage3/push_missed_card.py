# -*- coding: utf-8 -*-
"""一次性补救：把错过的里程碑卡（如 Codex DONE 因 SSE 间隙未推送）补推到飞书。

用法：python push_missed_card.py <task_id> [<task_id2> ...]
"""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_PR = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
STAGE2 = os.path.join(os.path.dirname(HERE), "stage2")
for p in [AGENT_PR, STAGE2, HERE]:
    if p not in sys.path:
        sys.path.insert(0, p)

from ag_ui_feishu_adapter import AgUiFeishuAdapter  # noqa: E402
import task_card  # noqa: E402

ORCH = "http://127.0.0.1:8773"

A2A_TO_CN = {
    "submitted": "待办", "working": "进行中", "input-required": "待审核",
    "completed": "已完成", "failed": "失败", "canceled": "已取消", "blocked": "阻塞",
}


def get_task(tid):
    return json.loads(urllib.request.urlopen(ORCH + "/tasks/" + tid, timeout=5).read())


def push_task(tid):
    t = get_task(tid)
    ad = AgUiFeishuAdapter(live=True, title=t.get("title", tid), thread_id=tid)
    # 从 task 状态还原卡片字段
    cn = A2A_TO_CN.get(t.get("state"), "待审核")
    ad.card_state["状态"] = cn
    ad.card_state["进度"] = "100%" if cn in ("待审核", "已完成") else (t.get("progress_pct") or "0%")
    ad.card_state["负责Agent"] = t.get("assignee") or "Orbit Codex"
    ad.card_state["指令"] = (t.get("prompt") or "")[:4000]
    ad.card_state["飞书Record"] = t.get("feishu_record") or "—"
    ad.card_state["标题"] = t.get("title", tid)
    for h in t.get("history", []):
        if h.get("text"):
            ad._append_progress(h["text"])
    card = ad.render_card()
    task_card.push_card(card)
    print(">>> 已补推卡 (task=%s, 状态=%s)" % (tid, cn))


if __name__ == "__main__":
    tids = sys.argv[1:]
    if not tids:
        tids = ["task-60329d63c1"]
    for tid in tids:
        try:
            push_task(tid)
        except Exception as e:
            print("!!! push err for %s: %s" % (tid, e))
