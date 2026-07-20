# -*- coding: utf-8 -*-
"""
AG-UI 事件 -> 飞书卡 映射原型（阶段 0 PoC）

职责：把「总控 A2A Orchestrator 收到的一条任务更新」翻译成 **标准 AG-UI 事件**
（spec-compliant，事件 schema 严格走 AG-UI 协议），再由飞书卡后端消费刷新字段。

设计要点（对应迁移计划 §5.2）：
- emit 官方 AG-UI 标准事件，**不引 CopilotKit React 运行时**；
- 不自造非标格式——事件本身就是 AG-UI spec 的 JSON；
- 本文件只实现「A2A 更新 -> AG-UI 事件」的薄映射 + 「AG-UI 事件 -> 飞书卡字段」的薄消费。

AG-UI 标准事件（节选我们用的子集，共 ~16 种）：
  RUN_STARTED / RUN_FINISHED / RUN_ERROR
  TEXT_MESSAGE_CONTENT          （流式进展文字）
  TOOL_CALL_START / TOOL_CALL_END（子 agent / 工具动作，实时可见）
  STATE_DELTA                  （RFC 6902 JSON Patch 增量，刷新卡片字段）
"""
import json
import copy


# ---- 标准 AG-UI 事件类型（与官方协议一致，直接用作事件的 type 字段）----
class AGUIEvent:
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_END = "TOOL_CALL_END"
    STATE_DELTA = "STATE_DELTA"


def _event(etype, **fields):
    """构造一个标准 AG-UI 事件字典（spec-compliant）。"""
    ev = {"type": etype}
    ev.update(fields)
    return ev


def map_a2a_update_to_ag_ui(update, thread_id="orb-thread-0033", run_id="orb-run-1"):
    """把一条 A2A 风格的任务更新映射成一组标准 AG-UI 事件。

    update 形如：
      {
        "task_id": "task-xxx",
        "status": "进行中" | "待审核" | "已完成" | "阻塞",
        "progress_text": "正在抓取 1688 数据…",   # 可选
        "tool": "scrape_1688",                     # 可选，子 agent 动作
        "tool_input": {"offer_id": "1003916001265"},# 可选
        "card_fields": {"状态": "进行中", "进度": "40%"}  # 可选，要刷新的卡片字段
      }
    返回：list[dict]（每个都是标准 AG-UI 事件 JSON）
    """
    events = []
    events.append(_event(AGUIEvent.RUN_STARTED, threadId=thread_id, runId=run_id))

    if update.get("tool"):
        events.append(_event(
            AGUIEvent.TOOL_CALL_START,
            toolCallId="tool_" + update.get("task_id", "x"),
            toolCallName=update["tool"],
            args=update.get("tool_input", {}),
        ))

    if update.get("progress_text"):
        # 按 AG-UI 惯例：TEXT_MESSAGE_CONTENT 携带增量文本（delta）
        events.append(_event(
            AGUIEvent.TEXT_MESSAGE_CONTENT,
            threadId=thread_id, runId=run_id,
            delta=update["progress_text"],
        ))

    # STATE_DELTA：用 RFC 6902 JSON Patch 增量刷新飞书卡字段，
    # 前端（飞书卡后端）据此重建/更新卡片状态——断线重连也能重建。
    if update.get("card_fields"):
        patch = [
            {"op": "replace", "path": f"/{k}", "value": v}
            for k, v in update["card_fields"].items()
        ]
        events.append(_event(
            AGUIEvent.STATE_DELTA,
            threadId=thread_id, runId=run_id,
            delta=patch,
        ))

    if update.get("tool"):
        events.append(_event(
            AGUIEvent.TOOL_CALL_END,
            toolCallId="tool_" + update.get("task_id", "x"),
            toolCallName=update["tool"],
        ))

    # 终态事件
    final_state = update.get("status")
    if final_state in ("已完成", "待审核"):
        events.append(_event(AGUIEvent.RUN_FINISHED, threadId=thread_id, runId=run_id))
    elif final_state == "阻塞":
        events.append(_event(AGUIEvent.RUN_ERROR, message="task blocked", code="BLOCKED",
                             threadId=thread_id, runId=run_id))
    else:
        # 仍进行中：先不结束，仅刷新状态
        pass

    return events


def apply_state_delta(card_state, delta_event):
    """飞书卡后端消费 STATE_DELTA：对卡片状态应用 JSON Patch（RFC 6902）。
    这是薄消费层——真实实现里会写飞书卡字段。
    返回更新后的卡片状态（副本）。"""
    state = copy.deepcopy(card_state)
    for op in delta_event.get("delta", []):
        if op.get("op") == "replace":
            state[op["path"].lstrip("/")] = op["value"]
    return state


def demo():
    print("=" * 60)
    print("AG-UI 映射原型演示：一条 A2A 任务更新 -> 标准 AG-UI 事件流")
    print("=" * 60)

    # 模拟 A2A Orchestrator 收到子 agent（Cursor）的回报
    a2a_update = {
        "task_id": "task-0033",
        "status": "进行中",
        "progress_text": "Cursor 正在抓取 1688 商品数据…",
        "tool": "scrape_1688",
        "tool_input": {"offer_id": "1003916001265"},
        "card_fields": {"状态": "进行中", "进度": "40%", "负责Agent": "Cursor"},
    }

    events = map_a2a_update_to_ag_ui(a2a_update)
    print("\n>>> 映射出的标准 AG-UI 事件流（spec-compliant JSON）：")
    for ev in events:
        print(json.dumps(ev, ensure_ascii=False))

    # 飞书卡后端消费：从空卡状态开始，依次应用 STATE_DELTA
    card_state = {"状态": "待办", "进度": "0%", "负责Agent": "—"}
    for ev in events:
        if ev["type"] == AGUIEvent.STATE_DELTA:
            card_state = apply_state_delta(card_state, ev)
    print("\n>>> 飞书卡后端消费 STATE_DELTA 后的卡片字段：")
    print(json.dumps(card_state, ensure_ascii=False, indent=2))
    print("\n结论：A2A 更新 -> AG-UI 标准事件 -> 飞书卡字段 衔接可行，"
          "事件严格走 AG-UI spec，无需 CopilotKit React。")
    return events, card_state


if __name__ == "__main__":
    demo()
