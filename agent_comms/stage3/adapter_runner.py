# -*- coding: utf-8 -*-
"""
阶段3 适配 runner：订阅 Orchestrator 的 AG-UI SSE，实时刷飞书卡。

这取代了旧监听器（eigenflux_stream_listener.py）「抓 worker DM -> 刷飞书卡」的职责：
- 旧链路：轮询 EigenFlux -> 静默刷卡 / 推战情室
- 新链路（本文件）：订阅 Orchestrator 的 AG-UI 事件流 -> 翻译飞书卡字段 -> 推卡
  实时、可断线重连、零轮询，且事件严格走 AG-UI 标准协议。

运行：STAGE3_LIVE=1 python adapter_runner.py
"""
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE2 = os.path.join(os.path.dirname(HERE), "stage2")
AGENT_COMMS = os.path.dirname(HERE)
TIKTOK = os.path.dirname(AGENT_COMMS)
AGENT_PR = os.path.dirname(TIKTOK)
for p in [AGENT_PR, STAGE2, AGENT_COMMS]:
    if p not in sys.path:
        sys.path.insert(0, p)

import task_card  # noqa: E402
from ag_ui_feishu_adapter import AgUiFeishuAdapter, AGUIEvent  # noqa: E402

ORCH_URL = os.environ.get("ORCH_URL", "http://127.0.0.1:8773/agui/events")
LIVE = os.environ.get("STAGE3_LIVE", "1") == "1"


def main():
    adapters = {}

    def get_ad(thread_id, record):
        if thread_id not in adapters:
            adapters[thread_id] = AgUiFeishuAdapter(
                live=LIVE, feishu_record=record, title=thread_id, thread_id=thread_id
            )
        return adapters[thread_id]

    print(">>> [stage3] 适配 runner 启动，订阅 %s (live=%s)" % (ORCH_URL, LIVE))
    while True:
        try:
            req = urllib.request.Request(ORCH_URL)
            with urllib.request.urlopen(req, timeout=60) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace")
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    try:
                        ev = json.loads(data)
                    except Exception:
                        continue
                    etype = ev.get("type")
                    if etype in (None, "connected"):
                        continue
                    tid = ev.get("threadId") or ev.get("taskId") or "default"
                    ad = get_ad(tid, None)
                    ad._on_event(ev)
                    if LIVE and etype in (AGUIEvent.STATE_DELTA, AGUIEvent.RUN_FINISHED):
                        try:
                            task_card.push_card(ad.render_card())
                            print("   [飞书卡] 已推卡 (thread=%s, 状态=%s)" % (tid, ad.card_state.get("状态")))
                        except Exception as e:
                            print("   [飞书卡] push err:", e)
        except Exception as e:
            print("   [stage3] SSE 断开，3s 后重连:", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
