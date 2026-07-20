# -*- coding: utf-8 -*-
"""
阶段3 演示：用「新 frame」完整驱动一张飞书卡（证明新链路可取代旧监听器）。

流程：
1) 向常驻 Orchestrator(PORT 8771) POST /ingest 一条合成 worker 回报
2) Orchestrator 映射成标准 AG-UI 事件流，经 SSE 广播
3) adapter_runner 订阅 SSE，把事件翻译成飞书卡并真实推送（STAGE3_LIVE=1）

用法：
  python run_stage3_demo.py          # 真实推送飞书卡（默认）
  python run_stage3_demo.py --mock   # 仅本地打印，不推飞书
服务与 runner 需先起：见 README 段注释。
"""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8773
ORCH = "http://127.0.0.1:%d" % PORT


def post_ingest(payload):
    req = urllib.request.Request(
        ORCH + "/ingest",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    mock = "--mock" in sys.argv
    # 合成一条「Cursor 回报」——模拟旧监听器本来要抓的 worker DM
    payload = {
        "agent": "Cursor",
        "text": "[阶段3 新frame 实测] 抓取 1688 商品数据完成，产物已生成，待审核",
        "tool": "scrape_1688",
        "tool_input": {"offer_id": "1003916001265"},
        "title": "阶段3 新frame 实测",
        "feishu_record": None,
    }
    print(">>> [stage3] POST /ingest (合成 Cursor 回报)")
    print("    ", json.dumps(payload, ensure_ascii=False))
    res = post_ingest(payload)
    print("<<< Orchestrator 响应:", json.dumps(res, ensure_ascii=False))
    print(">>> [stage3] 事件已广播到 /agui/events；adapter_runner 应已推送飞书卡")
    print("    （若 STAGE3_LIVE=1，请到飞书战情室查看由【新 frame】驱动的卡片）")
    if mock:
        print("    [mock] 未真实推送飞书")


if __name__ == "__main__":
    main()
