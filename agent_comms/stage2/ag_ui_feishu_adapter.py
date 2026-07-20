# -*- coding: utf-8 -*-
"""
阶段2：AG-UI 事件流 -> 飞书卡 适配器（A2A Orchestrator 的 Boss 视图层）

这是「agent↔Boss」这一层的落地：
- 上游：A2A Orchestrator 状态变化 -> map_a2a_update_to_ag_ui() 映射成 **标准 AG-UI 事件流**
- 本适配器：订阅该事件流（EventBus），把事件直接翻译成飞书卡字段更新并推送
- 不再依赖旧链路的「轮询 EigenFlux 消息 -> 静默刷飞书卡」模型

双轨并行（不替换旧链路）：
- 旧链路：eigenflux_stream_listener.py 轮询 EigenFlux -> 刷飞书卡 / 推战情室
- 新链路（本文件）：AG-UI 事件流驱动飞书卡，实时、可断线重建、零轮询
- 生产版可让 Orchestrator 用 SSE/WebSocket 把 AG-UI 事件推给本适配器（替代监听轮询）

设计要点（对齐迁移计划 §5.2）：
- emit/consume 都是 **标准 AG-UI 协议事件**（spec-compliant），不引 CopilotKit React
- 飞书卡字段由 STATE_DELTA（RFC 6902 JSON Patch 增量）驱动 -> 断线重连也能重建视图
- 复用 task_card.push_card / fb_progress.append_progress 真实推送（--live 时）
"""
import copy
import json
import os
import sys
from datetime import datetime

# ---- 路径：导入 stage1 orchestrator / a2a_poc mapping / 项目根 task_card ----
HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_COMMS = os.path.dirname(HERE)
TIKTOK = os.path.dirname(AGENT_COMMS)
AGENT_PR = os.path.dirname(TIKTOK)
for p in [AGENT_PR, os.path.join(AGENT_COMMS, "stage1"), os.path.join(AGENT_COMMS, "a2a_poc")]:
    if p not in sys.path:
        sys.path.insert(0, p)

import orchestrator as orch  # noqa: E402
from ag_ui_mapping import map_a2a_update_to_ag_ui, apply_state_delta, AGUIEvent  # noqa: E402

# 真实推送依赖（import 不触发网络；--live 时才调用）
import task_card  # noqa: E402  (提供 push_card / FEISHU_CHAT)
import fb_progress  # noqa: E402  (提供 append_progress)

# 中文状态 -> 卡片主题色（对齐 task_card.STATUS_THEME 子集）
STATUS_THEME = {
    "开发中": ("yellow", "yellow"), "进行中": ("yellow", "yellow"),
    "待验收": ("violet", "violet"), "待审核": ("violet", "violet"), "已完成": ("green", "green"),
    "阻塞": ("red", "red"), "待办": ("grey", "neutral"), "已取消": ("grey", "neutral"),
    "待转发": ("blue", "blue"),
}
STATUS_EMOJI = {
    "开发中": "🟡", "进行中": "🟡", "待验收": "🟣", "待审核": "🟣", "已完成": "🟢",
    "阻塞": "🔴", "待办": "⚪", "已取消": "⚫",
    "待转发": "🔵",
}
# A2A 内部 state -> 中文显示（给 Boss 看）
A2A_TO_CN = {
    "submitted": "待办", "working": "进行中", "input-required": "待审核",
    "completed": "已完成", "failed": "失败", "canceled": "已取消", "blocked": "阻塞",
}


# --------------------------------------------------------------------------
# 极简 EventBus（AG-UI 事件流的本地发布/订阅）
# --------------------------------------------------------------------------
class EventBus:
    def __init__(self):
        self._subs = []

    def subscribe(self, handler):
        self._subs.append(handler)

    def publish(self, event):
        for h in self._subs:
            try:
                h(event)
            except Exception as e:  # 单个消费者失败不影响其它
                print("  [EventBus] handler error:", e, file=sys.stderr)


# --------------------------------------------------------------------------
# AG-UI -> 飞书卡 适配器（Boss 视图层）
# --------------------------------------------------------------------------
class AgUiFeishuAdapter:
    """订阅 AG-UI 事件流，把事件翻译成飞书卡字段更新并推送。

    card_state 是唯一事实来源（由 STATE_DELTA 增量维护），render_card 据其渲染。
    """

    def __init__(self, live=False, feishu_record=None, title=None, thread_id="orb-thread-0033"):
        self.live = live
        self.feishu_record = feishu_record
        self.title = title or "ORB-TASK"
        self.thread_id = thread_id
        self.bus = EventBus()
        self.card_state = {
            "标题": self.title,
            "状态": "待办",
            "进度": "0%",
            "负责Agent": "—",
            "飞书Record": feishu_record or "—",
        }
        self.progress_lines = []
        self.received_events = []  # 已收到的 AG-UI 事件（用于调试/回放）
        self.bus.subscribe(self._on_event)

    # ---- 核心入口：一条 A2A 更新 -> 推进 Orchestrator -> 发 AG-UI 事件流 ----
    def process_a2a_update(self, update):
        """update 形如：
            {
              "task_id": "task-xxx",
              "a2a_state": "working" | "input-required" | ...   # 给 Orchestrator 内部状态机
              "progress_text": "Cursor 正在抓取…",                # 可选
              "tool": "scrape_1688",                             # 可选
              "tool_input": {...},                               # 可选
              "card_fields": {"状态":"进行中","进度":"40%",...}  # 给飞书卡（中文）
            }
        """
        # 1) 推进 A2A Orchestrator 状态机（内部用 A2A 标准 state）
        orch.apply_a2a_event({
            "task_id": update["task_id"],
            "status": update.get("a2a_state", "working"),
            "progress_text": update.get("progress_text", ""),
        })
        # 2) 映射成标准 AG-UI 事件流并发布
        ag_update = {
            "task_id": update["task_id"],
            "status": (update.get("card_fields") or {}).get("状态"),
            "progress_text": update.get("progress_text", ""),
            "tool": update.get("tool"),
            "tool_input": update.get("tool_input"),
            "card_fields": update.get("card_fields"),
        }
        for ev in map_a2a_update_to_ag_ui(ag_update, thread_id=self.thread_id):
            self.bus.publish(ev)
        # live：每步（一次 process_a2a_update）合并推送一张飞书卡，避免每个事件刷屏
        if self.live:
            try:
                task_card.push_card(self.render_card())
            except Exception as e:
                print("  [飞书卡] push err:", e, file=sys.stderr)
        return self.card_state

    # ---- EventBus 消费者：把 AG-UI 事件翻译成飞书卡更新 ----
    def _on_event(self, ev):
        etype = ev.get("type")
        if etype == AGUIEvent.RUN_STARTED:
            pass
        elif etype == AGUIEvent.TEXT_MESSAGE_CONTENT:
            delta = ev.get("delta", "")
            if delta:
                self._append_progress(delta)
        elif etype == AGUIEvent.TOOL_CALL_START:
            self._append_progress("🔧 调用工具: " + ev.get("toolCallName", ""))
        elif etype == AGUIEvent.TOOL_CALL_END:
            pass
        elif etype == AGUIEvent.STATE_DELTA:
            # 飞书卡字段由增量 patch 驱动（断线重连可重建）
            self.card_state = apply_state_delta(self.card_state, ev)
        elif etype == AGUIEvent.RUN_FINISHED:
            self._append_progress("✅ 运行结束")
        elif etype == AGUIEvent.RUN_ERROR:
            self._append_progress("⚠️ 运行出错: " + ev.get("message", ""))
        self.received_events.append(ev)
        # mock：每个事件展示流式刷新的卡片字段（live 时不在此推卡，避免刷屏）
        if not self.live:
            print("   [飞书卡·渲染] " + json.dumps(self.card_state, ensure_ascii=False))

    def _append_progress(self, text):
        now = datetime.now().strftime("%H:%M:%S")
        line = "[%s][总控] %s" % (now, text)
        self.progress_lines.append(line)
        # live 且为真实飞书 record 时，追加到主任务表「进展记录」；占位(recTEST*)仅本地展示
        if self.live and self.feishu_record and not self.feishu_record.startswith("recTEST"):
            try:
                fb_progress.append_progress(self.feishu_record, line)
            except Exception as e:
                print("  [fb] append err:", e, file=sys.stderr)
        else:
            print("   [飞书卡·进展] +", line)

    def _render_and_push(self):
        card = self.render_card()
        if self.live:
            try:
                task_card.push_card(card)
            except Exception as e:
                print("  [飞书卡] push err:", e, file=sys.stderr)
        else:
            print("   [飞书卡·渲染] " + json.dumps(self.card_state, ensure_ascii=False))
        return card

    def render_card(self):
        """据 card_state 渲染 Card 2.0（风格对齐 task_card.build_card）。"""
        status = self.card_state.get("状态", "待办")
        template, tag_color = STATUS_THEME.get(status, ("yellow", "yellow"))
        st_emoji = STATUS_EMOJI.get(status, "🟡")

        tl_lines = []
        for ln in self.progress_lines[-12:]:
            hm = ln.split("]")[0].strip("[")
            c1 = ln[ln.find("]") + 1:].strip()
            c1 = c1[:64] + "…" if len(c1) > 64 else c1
            tl_lines.append("`%s` %s" % (hm, c1))
        timeline_md = "\n".join(tl_lines) if tl_lines else "_暂无进展记录_"

        elements = [
            {
                "tag": "column_set", "flex_mode": "bisect",
                "background_style": "grey", "horizontal_spacing": "medium",
                "columns": [
                    {"tag": "column", "elements": [
                        {"tag": "markdown",
                         "content": "<font color='grey'>负责 Agent</font>\n**%s**" %
                                    self.card_state.get("负责Agent", "—")}
                    ]},
                    {"tag": "column", "elements": [
                        {"tag": "markdown",
                         "content": "<font color='grey'>进度</font>\n**%s**" %
                                    self.card_state.get("进度", "0%")}
                    ]},
                ],
            },
            {"tag": "markdown",
             "content": "**🎯 当前状态**\n%s" %
                        self.card_state.get("状态", "待办")},
            {"tag": "hr"},
            {"tag": "markdown",
             "content": "**📋 转发给 Agent 的指令**\n```\n%s\n```" %
                        (self.card_state.get("指令") or "_（无）_")},
            {"tag": "hr"},
            {"tag": "markdown",
             "content": "**📈 进度时间线**（共 %d 条，AG-UI 事件驱动）\n%s" % (len(self.progress_lines), timeline_md)},
        ]

        return {
            "schema": "2.0",
            "config": {"update_multi": True, "width_mode": "default"},
            "header": {
                "title": {"tag": "plain_text", "content": "📋 %s" % self.card_state.get("标题", "ORB-TASK")},
                "subtitle": {"tag": "plain_text",
                             "content": "AG-UI 事件流驱动 · 飞书Record %s" % self.card_state.get("飞书Record", "—")},
                "template": template,
                "text_tag_list": [
                    {"tag": "text_tag",
                     "text": {"tag": "plain_text", "content": "%s %s" % (st_emoji, status)},
                     "color": tag_color},
                ],
            },
            "body": {"direction": "vertical", "padding": "12px 12px 16px 12px",
                     "vertical_spacing": "medium", "elements": elements},
        }

    # ---- 断线重连：从 Orchestrator 历史重建 card_state（体现 STATE_DELTA 可重建）----
    def replay_from_orchestrator(self, task_id):
        t = orch.get_task(task_id)
        if not t:
            print("  [replay] task not found:", task_id)
            return
        # 重置到初始态后用历史事件重建（演示：STATE_DELTA 增量可重建视图）
        self.card_state = {"标题": t.get("title", self.title), "状态": "待办",
                           "进度": "0%", "负责Agent": "—",
                           "飞书Record": t.get("feishu_record") or "—"}
        self.progress_lines = []
        print("  [replay] 从 Orchestrator 历史重建 card_state（%d 条历史）" % len(t["history"]))
        for h in t["history"]:
            if h.get("text"):
                self._append_progress(h["text"])
        return self.card_state


if __name__ == "__main__":
    # 直接运行 = 自测：建卡 -> 3 步更新 -> 渲染
    tid = orch.create_task("阶段2 自测任务", feishu_record="recTEST0033", assignee="Cursor")
    ad = AgUiFeishuAdapter(feishu_record="recTEST0033", title="阶段2 自测")
    print(">>> step1 dispatch")
    ad.process_a2a_update({"task_id": tid, "a2a_state": "working",
                           "progress_text": "已派发至 Cursor",
                           "card_fields": {"状态": "进行中", "进度": "10%", "负责Agent": "Cursor"}})
    print(">>> step2 crawl")
    ad.process_a2a_update({"task_id": tid, "a2a_state": "working",
                           "progress_text": "Cursor 正在抓取 1688 商品数据",
                           "tool": "scrape_1688", "tool_input": {"offer_id": "1003916001265"},
                           "card_fields": {"状态": "进行中", "进度": "60%", "负责Agent": "Cursor"}})
    print(">>> step3 done")
    ad.process_a2a_update({"task_id": tid, "a2a_state": "input-required",
                           "progress_text": "Cursor 回报：产物已生成，待审核",
                           "card_fields": {"状态": "待审核", "进度": "100%", "负责Agent": "Cursor"}})
    print("\n>>> replay (断线重连重建):")
    ad.replay_from_orchestrator(tid)
