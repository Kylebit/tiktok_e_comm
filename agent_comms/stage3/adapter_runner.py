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
    # 里程碑卡策略：仅这些状态变化才推飞书卡（过滤「进行中」等高频进度刷新）
    MILESTONE = {"已派发", "待转发", "待审核", "已完成", "阻塞"}
    last_pushed_status = {}  # thread_id -> 上次已推的里程碑状态（去重，避免重复推同状态）

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
                    # 里程碑卡策略：只在状态切换到「里程碑节点」时推卡，
                    # 过滤中间「进行中」等高频进度更新，避免群刷屏；
                    # 同时通过 render_card 的完整时间线保留全部信息。
                    if LIVE and etype == AGUIEvent.STATE_DELTA:
                        st = ad.card_state.get("状态")
                        if st in MILESTONE and last_pushed_status.get(tid) != st:
                            try:
                                task_card.push_card(ad.render_card())
                                last_pushed_status[tid] = st
                                print("   [飞书卡·里程碑] 已推卡 (thread=%s, 状态=%s)" % (tid, st))
                            except Exception as e:
                                print("   [飞书卡] push err:", e)
        except Exception as e:
            print("   [stage3] SSE 断开，3s 后重连:", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
