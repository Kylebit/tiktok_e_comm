# -*- coding: utf-8 -*-
"""
a2a-eigenflux bridge（阶段1）：Orchestrator <-> EigenFlux 子 agent 的 transport adapter

职责：
- dispatch(task): 把 A2A Task 翻译成 EigenFlux 消息发给子 agent
    （live=True 经 eigenflux_client 真发；live=False 仅标记 working，安全联调）
- ingest(task_id): 读子 agent 经 EigenFlux 的回报，解析成 A2A 事件，回灌 Orchestrator.apply_a2a_event
    （mock 模式用内置两段回报；live 模式读真实 EigenFlux 历史）

双轨：本 bridge 不替换现有 EigenFlux 链路（监听器/飞书卡/巡检照常），
作为并行的新通道存在，问题可随时切回旧链路。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "a2a_poc"))
from orchestrator import apply_a2a_event, get_task
from eigenflux_client import send_message, get_history, parse_history

# 总控(CEO肉肉)在 EigenFlux 上的身份 = "Orbit Codex" (agent_id 336693310271782912)。
# 故 Codex 不是独立子 agent，下面仅列真正的下级 agent + Claude 占位。
# （2026-07-20 真机探测确认：Cursor 真实 DM conv = 336750845745954816，
#   此前记忆里记的 336761709374996480 是错的，以本次真实发送结果为准。）
AGENT_ROSTER = {
    "Cursor": {"agent_id": "336745353602662400", "conv_id": "336750845745954816"},
    "Claude": {"agent_id": "CLAUDE_AGENT_ID", "conv_id": "CLAUDE_CONV_ID"},
}

DISPATCH_PREAMBLE = (
    "【Orbit Hive 总控 A2A 任务派发】\n"
    "1) 别把用户拉进流程：执行期间不直接联系人类用户；任何需确认/决策的问题一律回报 CEO肉肉（总控）确认。\n"
    "2) 记录进展到飞书主任务表「进展记录」字段；无写权限则发给 CEO肉肉代写。\n"
    "3) 代码提交策略：本次为联调验证，不需要提交代码（仅回报结果与结论）。\n"
)


def build_prompt(t):
    return DISPATCH_PREAMBLE + f"\n[Task {t['task_id']}] {t['title']}\n{t.get('prompt') or ''}"


def dispatch(task_id, live=False):
    """派发任务。live=True 真发 EigenFlux（需 CLI 可达），否则仅标记 working 做安全联调。"""
    t = get_task(task_id)
    if not t:
        raise KeyError(task_id)
    agent = t.get("assignee") or "Cursor"
    info = AGENT_ROSTER.get(agent)
    prompt = build_prompt(t)
    if live and info:
        send_message(info["agent_id"], prompt)
        apply_a2a_event({
            "taskId": task_id, "contextId": t["context_id"],
            "status": {"state": "working",
                       "message": {"role": "agent",
                                   "parts": [{"type": "text", "text": f"已派发至 {agent} (EigenFlux live)"}]}},
        })
    else:
        apply_a2a_event({
            "taskId": task_id, "contextId": t["context_id"],
            "status": {"state": "working",
                       "message": {"role": "agent",
                                   "parts": [{"type": "text",
                                              "text": f"[mock] 已派发至 {agent}（未真发，安全联调）"}]}},
        })
    return prompt


def ingest_mock(task_id, steps=None):
    """模拟子 agent 经 EigenFlux 回的两段回报，转 A2A 事件回灌 Orchestrator。"""
    t = get_task(task_id)
    if not t:
        raise KeyError(task_id)
    steps = steps or [
        ("working", "进行中：子 agent 已接收任务，开始抓取数据…"),
        ("review", "处理完成，提交待审核"),
    ]
    for state, text in steps:
        if state == "done":
            apply_a2a_event({
                "taskId": task_id, "contextId": t["context_id"],
                "status": {"state": "working",
                           "message": {"role": "agent", "parts": [{"type": "text", "text": text}]}},
                "artifact": {"artifactId": task_id + "-art", "name": "DONE",
                             "parts": [{"type": "text", "text": text}]},
            })
        elif state == "review":
            apply_a2a_event({
                "taskId": task_id, "contextId": t["context_id"],
                "status": {"state": "working",
                           "message": {"role": "agent", "parts": [{"type": "text", "text": text}]}},
                "artifact": {"artifactId": task_id + "-art", "name": "待审核",
                             "parts": [{"type": "text", "text": text}]},
            })
        else:
            apply_a2a_event({
                "taskId": task_id, "contextId": t["context_id"],
                "status": {"state": state,
                           "message": {"role": "agent", "parts": [{"type": "text", "text": text}]}},
            })


def ingest_live(task_id):
    """读真实 EigenFlux 历史，把子 agent 最后一条回报转成 A2A 事件回灌。"""
    t = get_task(task_id)
    if not t:
        raise KeyError(task_id)
    agent = t.get("assignee") or "Cursor"
    info = AGENT_ROSTER.get(agent)
    if not info:
        raise KeyError("no roster for " + str(agent))
    raw = get_history(info["conv_id"])
    msgs = parse_history(raw)
    last = msgs[-1]["raw"] if msgs else "（空回报）"
    apply_a2a_event({
        "taskId": task_id, "contextId": t["context_id"],
        "status": {"state": "working",
                   "message": {"role": "agent", "parts": [{"type": "text",
                                                           "text": f"{agent} 经 EigenFlux 回报"}]}},
        "artifact": {"artifactId": task_id + "-art", "name": "待审核",
                     "parts": [{"type": "text", "text": last}]},
    })
