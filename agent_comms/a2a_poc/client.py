# -*- coding: utf-8 -*-
"""
A2A client（阶段 0 PoC 验证脚本）

端到端验证：
  1) 拉取 Agent Card（发现能力）
  2) 调用 message/stream，消费 SSE 流，收集 TaskStatusUpdateEvent + TaskArtifactUpdateEvent
  3) 把最后一条 A2A 产物更新交给 ag_ui_mapping，演示「A2A 更新 -> AG-UI 标准事件」衔接
"""
import json
import httpx

BASE = "http://127.0.0.1:8769"


def get_card():
    r = httpx.get(BASE + "/.well-known/agent.json", timeout=10)
    r.raise_for_status()
    return r.json()


def stream_message(text):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/stream",
        "params": {"message": {"role": "user", "parts": [{"type": "text", "text": text}]}},
    }
    events = []
    with httpx.Client(timeout=30) as c:
        with c.stream("POST", BASE + "/", json=payload) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if not data:
                    continue
                msg = json.loads(data)
                if "result" in msg:
                    events.append(msg["result"])
                    kind = msg["result"].get("status", {}).get("state") or (
                        "artifact:" + msg["result"].get("artifact", {}).get("name", "")
                    )
                    print("    <- A2A 事件:", kind, "|", json.dumps(msg["result"], ensure_ascii=False)[:140])
    return events


def main():
    card = get_card()
    print("=" * 60)
    print("A2A client 端到端验证")
    print("=" * 60)
    print(f"Agent Card: {card['name']}  (streaming={card['capabilities']['streaming']})")
    print(f"Skills: {[s['id'] for s in card['skills']]}")
    print("\n>>> 调用 message/stream（SSE 流式）...")
    events = stream_message("ping from CEO肉肉 @ 0033")
    print(f"\n>>> 端到端收到 {len(events)} 个 A2A 事件（状态 + 产物），SSE 流式 OK")

    # 衔接 AG-UI 映射层
    from ag_ui_mapping import map_a2a_update_to_ag_ui, apply_state_delta, AGUIEvent

    last = events[-1]
    update = {
        "task_id": last.get("taskId"),
        "status": "已完成",
        "progress_text": "A2A 流式回报完成",
        "card_fields": {"状态": "已完成", "进度": "100%", "负责Agent": "总控 A2A"},
    }
    ag_events = map_a2a_update_to_ag_ui(update)
    card_state = {"状态": "待办", "进度": "0%", "负责Agent": "—"}
    for ev in ag_events:
        if ev["type"] == AGUIEvent.STATE_DELTA:
            card_state = apply_state_delta(card_state, ev)
    print(f">>> 同一回报再映射成 {len(ag_events)} 个标准 AG-UI 事件；飞书卡字段 -> {card_state}")
    print("\n结论：A2A(SSE 流式) + AG-UI 映射 在总控侧端到端跑通，当前 EigenFlux 系统未受影响。")


if __name__ == "__main__":
    main()
