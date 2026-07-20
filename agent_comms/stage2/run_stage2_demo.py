# -*- coding: utf-8 -*-
"""
阶段2 端到端演示：AG-UI 事件流驱动飞书卡（双轨、不替换旧链路）

演示链路：
  ① A2A Orchestrator 创建 Task（taskId <-> 飞书主任务表 record）
  ② 派发（--live-eigenflux 时经 stage1 bridge 真发；默认 mock 派发，由 AG-UI 事件体现）
  ③ 子 agent 回报序列 -> adapter.process_a2a_update() -> 标准 AG-UI 事件流
  ④ AgUiFeishuAdapter 订阅事件流 -> 实时刷新并推送飞书卡（--live 真推）

对比旧链路：
  旧：eigenflux_stream_listener 轮询 EigenFlux 消息 -> 静默刷飞书卡/推战情室（被动、轮询）
  新：Orchestrator 状态变化 -> AG-UI SSE 事件流 -> 本适配器直接推送（实时、可重连、零轮询）

用法:
  python run_stage2_demo.py                  # mock（打印 AG-UI 事件 + 飞书卡字段，不真发）
  python run_stage2_demo.py --live           # 真实推送飞书卡到战情室
  python run_stage2_demo.py --live --live-eigenflux   # 额外经 bridge 真发 EigenFlux 派发
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_COMMS = os.path.dirname(HERE)
for p in [HERE, os.path.join(AGENT_COMMS, "stage1")]:
    if p not in sys.path:
        sys.path.insert(0, p)

import orchestrator as orch  # noqa: E402
from ag_ui_feishu_adapter import AgUiFeishuAdapter, A2A_TO_CN  # noqa: E402


def build_prompt(title):
    return ("【Orbit Hive 任务】%s\n"
            "1) 别把用户拉进流程：执行期间直接回报 CEO肉肉(总控) 确认。\n"
            "2) 记录进展（飞书主任务表「进展记录」字段）。\n"
            "3) 代码提交策略：长期维护工程需 git commit+push 并回传 hash。\n" % title)


def run(live, live_eigenflux):
    print("=" * 64)
    print("阶段2 演示：AG-UI 事件流驱动飞书卡（双轨并行）")
    print("=" * 64)
    print("模式: %s" % ("LIVE(真推飞书卡)" if live else "MOCK(仅打印)"))

    # ① 创建 Task（关联飞书主任务表 ORB-TASK-0033 行 recTEST0033 占位）
    feishu_record = "recTEST0033"
    title = "阶段2 演示任务：AG-UI 驱动飞书卡"
    tid = orch.create_task(title, feishu_record=feishu_record, assignee="Cursor",
                           agent_id="336745353602662400",
                           agent_conv_id="336761709374996480",
                           prompt=build_prompt(title))
    print("\n[1] Orchestrator 创建 Task: %s  <->  飞书Record %s" % (tid, feishu_record))

    # 适配器：订阅 AG-UI 事件流，驱动飞书卡
    adapter = AgUiFeishuAdapter(live=live, feishu_record=feishu_record, title=title)

    # ② 派发
    print("\n[2] 派发（经 A2A 事件流）...")
    if live_eigenflux:
        from bridge import dispatch  # noqa: E402
        dispatch(tid, live=True)
    adapter.process_a2a_update({
        "task_id": tid, "a2a_state": "working",
        "progress_text": "已派发至 Cursor（EigenFlux transport adapter）",
        "card_fields": {"状态": "进行中", "进度": "10%", "负责Agent": "Cursor"},
    })

    # ③ 子 agent 回报：抓取
    print("\n[3] Cursor 回报-抓取中（AG-UI 事件流驱动刷新）...")
    adapter.process_a2a_update({
        "task_id": tid, "a2a_state": "working",
        "progress_text": "Cursor 正在抓取 1688 商品数据…",
        "tool": "scrape_1688", "tool_input": {"offer_id": "1003916001265"},
        "card_fields": {"状态": "进行中", "进度": "60%", "负责Agent": "Cursor"},
    })

    # ④ 子 agent 回报：产物生成，待审核
    print("\n[4] Cursor 回报-产物完成（进入待审核）...")
    adapter.process_a2a_update({
        "task_id": tid, "a2a_state": "input-required",
        "progress_text": "Cursor 回报：产物已生成，待 Boss 审核",
        "card_fields": {"状态": "待审核", "进度": "100%", "负责Agent": "Cursor"},
    })

    # ⑤ 展示收到的 AG-UI 事件流
    print("\n[5] 本次收到的标准 AG-UI 事件流（spec-compliant）:")
    for i, ev in enumerate(adapter.received_events, 1):
        print("  %d. %s" % (i, ev["type"]))
        if ev["type"] == "STATE_DELTA":
            print("       patch:", json.dumps(ev.get("delta"), ensure_ascii=False))

    # ⑥ 断线重连演示
    print("\n[6] 断线重连：从 Orchestrator 历史重建 card_state（STATE_DELTA 增量可重建）")
    adapter.replay_from_orchestrator(tid)

    # ⑦ Orchestrator 内部最终态
    t = orch.get_task(tid)
    print("\n[7] Orchestrator 内部 Task 终态: a2a_state=%s -> Boss视图=%s" %
          (t["state"], A2A_TO_CN.get(t["state"], t["state"])))

    print("\n=== 阶段2 结论 ===")
    print("AG-UI 事件流已驱动飞书卡（状态/进度/负责Agent/时间线），")
    print("替代旧链路『轮询 EigenFlux -> 静默刷卡』模型；当前旧链路零改动、双轨并行。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="真实推送飞书卡到战情室")
    ap.add_argument("--live-eigenflux", action="store_true", help="额外经 stage1 bridge 真发 EigenFlux 派发")
    args = ap.parse_args()
    run(args.live, args.live_eigenflux)
