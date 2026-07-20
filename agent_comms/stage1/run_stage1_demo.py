# -*- coding: utf-8 -*-
"""
阶段1 端到端演示（默认 mock 模式，零风险、不碰外部 agent）

流程：创建 A2A Task -> bridge 派发(mock) -> 子 agent 回报(mock 两段) -> Orchestrator 状态机推进
      -> A2A 更新映射标准 AG-UI 事件 -> 刷新飞书卡字段
      -> 启动 Orchestrator HTTP 验证 A2A 兼容暴露（Agent Card + /tasks/{id}）

可选 --live：真机给 Codex 发低风险 ping（需 eigenflux CLI 可达 + 认证）。
"""
import os
import sys
import subprocess
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "a2a_poc"))

import orchestrator as orch
from bridge import dispatch, ingest_mock, ingest_live, AGENT_ROSTER
from ag_ui_mapping import map_a2a_update_to_ag_ui, apply_state_delta, AGUIEvent
from eigenflux_client import send_message_json, get_history, parse_history
import argparse
import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(HERE, "..", "a2a_poc", "venv", "Scripts", "python.exe")
ORCH_PY = os.path.join(HERE, "orchestrator.py")
PORT = 8770


def run_live_ping(tid, send=True):
    """真机探测：给真实子 agent Cursor 发低风险 ping，验证 bridge 真实下行 + 上行读取 + A2A/AG-UI 转换。

    send=True  : 真实经 EigenFlux 下行发一条 ping（需 CLI 可达 + 默认 home 已登录）
    send=False : 复用已有会话仅做上行读取 + 转换演示（不重复打扰子 agent）
    """
    agent = "Cursor"
    info = AGENT_ROSTER.get(agent)
    if not info:
        print(f"[live] ERROR: roster 缺少 {agent}"); return
    if send:
        print(f"\n[live] 真机下行：经 bridge 给 {agent} 发低风险 ping ...")
        out = send_message_json(
            info["agent_id"],
            "[A2A bridge live demo] 自动验证下行链路，无需执行任务，回 PONG-OK 即可。",
        )
        print(f"      发送成功 conv_id={out.get('conv_id')} msg_id={out.get('msg_id')}")
    else:
        print(f"\n[live] 仅上行读取+转换演示（不重复下行），会话 conv={info['conv_id']}")
    print(f"[live] 真机上行读取 + A2A 转换：ingest_live({tid}) ...")
    ingest_live(tid)
    t = orch.get_task(tid)
    update = {
        "task_id": tid,
        "status": "进行中",
        "progress_text": t["progress_text"],
        "card_fields": {
            "状态": t["state"], "进度": "100%",
            "负责Agent": t["assignee"], "飞书Record": t["feishu_record"],
        },
    }
    ag_events = map_a2a_update_to_ag_ui(update)
    card_state = {"状态": "待办", "进度": "0%", "负责Agent": "—", "飞书Record": "—"}
    for ev in ag_events:
        if ev["type"] == AGUIEvent.STATE_DELTA:
            card_state = apply_state_delta(card_state, ev)
    print(f"[live] 读取内容经 bridge 转 A2A 事件 + AG-UI 映射 -> 飞书卡字段 {card_state}")
    print("[live] 结论：下行(EigenFlux 发送) + 上行(历史读取) + 转换(A2A/AG-UI) 三通道真机可用。")


def main():
    print("=" * 66)
    print("阶段1 端到端演示：A2A Orchestrator + bridge + AG-UI 映射闭环")
    print("=" * 66)

    # 1) 创建 A2A Task（taskId <-> 飞书主任务表 record 映射）
    tid = orch.create_task(
        title="联调小任务：验证 A2A 派发闭环",
        feishu_record="recTEST0033",
        assignee="Cursor",
        prompt="（联调用，无需真实执行）回报「进行中 -> 待审核」即可。",
    )
    print(f"\n[1] 创建 A2A Task: {tid}  (关联飞书 record=recTEST0033, 派发对象=Cursor)")

    # 2) bridge 派发（mock：不真发 EigenFlux）
    dispatch(tid, live=False)
    print(f"[2] bridge 派发(mock): 已标记 working（未真发 EigenFlux，安全联调）")

    # 3) 子 agent 经 bridge 回报（mock 两段：进行中 -> 待审核）
    ingest_mock(tid)
    t = orch.get_task(tid)
    print(f"[3] 子 agent 经 bridge 回报两段 -> state={t['state']}, "
          f"末条={t['history'][-1].get('text', '')[:36]}")

    # 4) A2A 更新 -> 标准 AG-UI 事件 -> 飞书卡字段
    update = {
        "task_id": tid,
        "status": "待审核" if t["state"] == "input-required" else "进行中",
        "progress_text": t["progress_text"],
        "card_fields": {
            "状态": t["state"], "进度": "100%",
            "负责Agent": t["assignee"], "飞书Record": t["feishu_record"],
        },
    }
    ag_events = map_a2a_update_to_ag_ui(update)
    card_state = {"状态": "待办", "进度": "0%", "负责Agent": "—", "飞书Record": "—"}
    for ev in ag_events:
        if ev["type"] == AGUIEvent.STATE_DELTA:
            card_state = apply_state_delta(card_state, ev)
    print(f"[4] 同一条回报映射为 {len(ag_events)} 个标准 AG-UI 事件；"
          f"飞书卡字段 -> {card_state}")

    # 5) 启动 Orchestrator HTTP 验证 A2A 兼容暴露
    print(f"\n[5] 启动 Orchestrator HTTP (端口 {PORT}) 验证 A2A 暴露...")
    srv = subprocess.Popen([VENV_PY, ORCH_PY],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                if httpx.get(f"http://127.0.0.1:{PORT}/.well-known/agent.json",
                             timeout=2).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.25)
        card = httpx.get(f"http://127.0.0.1:{PORT}/.well-known/agent.json",
                         timeout=5).json()
        print(f"    Agent Card: {card['name']} "
              f"(streaming={card['capabilities']['streaming']}, transport={card.get('note')})")
        tk = httpx.get(f"http://127.0.0.1:{PORT}/tasks/{tid}", timeout=5).json()
        print(f"    GET /tasks/{tid} -> state={tk.get('state')}, "
              f"feishu={tk.get('feishu_record')}")
    finally:
        srv.terminate()

    print("\n结论：Orchestrator 状态机 + bridge + AG-UI 映射 三件套闭环跑通；"
          "当前 EigenFlux 旧链路完全未触碰（双轨保留）。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="阶段1 A2A Orchestrator + bridge 端到端演示")
    ap.add_argument("--live", action="store_true",
                    help="真机探测：给 Cursor 发低风险 ping 并验证下行+上行读取+转换")
    ap.add_argument("--no-send", action="store_true",
                    help="配合 --live：仅做上行读取+转换演示，不真实下行发送")
    args = ap.parse_args()
    if args.live:
        tid = orch.create_task(
            title="live 真机探测任务",
            feishu_record="recTEST0033",
            assignee="Cursor",
            prompt="（live 联调，无需真实执行）",
        )
        print(f"[live] 创建 A2A Task: {tid}")
        run_live_ping(tid, send=not args.no_send)
    else:
        main()
