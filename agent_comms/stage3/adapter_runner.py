# -*- coding: utf-8 -*-
"""
阶段3 适配 runner（单卡汇总模式）
=================================
订阅 Orchestrator 的 AG-UI SSE，把「当前所有未 done 任务」汇总到 **一张** 飞书卡；
任何任务状态/进度变化都 **PATCH 原地更新** 这张卡（不新发、不刷屏）。

核心约定（Boss 2026-07-20 要求）：
- 只维护一张卡片：message_id 持久化到 .summary_card.json，首次 send、之后 PATCH 更新。
- 卡上列出每个未 done 任务：标题 + 状态(emoji) + 负责Agent + 最近进度时间线。
- 已删除「转发给 Agent 的指令」区块（不再需要 Boss 手动转发）。

去重/降频：SSE 事件只置脏标志，后台 2s 定时器合并刷新，避免每条事件都打飞书。
"""
import json
import os
import sys
import time
import threading
import datetime as _dt
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

ORCH_URL = os.environ.get("ORCH_URL", "http://127.0.0.1:8773/agui/events")
ORCH_API = os.environ.get("ORCH_API", "http://127.0.0.1:8773")
LIVE = os.environ.get("STAGE3_LIVE", "1") == "1"
SUMMARY_FILE = os.path.join(HERE, ".summary_card.json")

A2A_TO_CN = {
    "submitted": "待办", "working": "进行中", "input-required": "待审核",
    "completed": "已完成", "failed": "失败", "canceled": "已取消", "blocked": "阻塞",
    "relay": "待转发",
}
# 终态（不列入汇总卡）；其余（待办/进行中/待审核/阻塞/待转发）均视为「仍需关注」
DONE_STATES = {"completed", "canceled", "failed"}

STATUS_EMOJI = {
    "待办": "⚪", "进行中": "🟡", "待审核": "🟣", "阻塞": "🔴", "已完成": "🟢",
    "失败": "⚫", "已取消": "⚫", "待转发": "🔵",
}
STATUS_THEME = {
    "待办": ("grey", "neutral"), "进行中": ("yellow", "yellow"), "待审核": ("violet", "violet"),
    "阻塞": ("red", "red"), "已完成": ("green", "green"), "失败": ("red", "red"),
    "已取消": ("grey", "neutral"), "待转发": ("blue", "blue"),
}

BJ = _dt.timezone(_dt.timedelta(hours=8))


def _fmt_ts(ts):
    """2026-07-20T09:15:19.832592+00:00 -> 17:15（北京时间）"""
    if not ts:
        return ""
    try:
        s = ts.replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(BJ)
        return dt.strftime("%H:%M")
    except Exception:
        return ""


def _load_mid():
    try:
        with open(SUMMARY_FILE, encoding="utf-8") as f:
            return json.load(f).get("message_id")
    except Exception:
        return None


def _save_mid(mid):
    try:
        with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
            json.dump({"message_id": mid}, f)
    except Exception as e:
        print("   [summary] 保存 message_id 失败:", e)


def fetch_tasks():
    try:
        data = json.loads(urllib.request.urlopen(ORCH_API + "/tasks", timeout=5).read())
    except Exception as e:
        print("   [summary] 拉取 /tasks 失败:", e)
        return []
    tasks = data.get("tasks", []) if isinstance(data, dict) else data
    out = []
    for t in tasks:
        st = t.get("state")
        if st in DONE_STATES:
            continue
        tid = t.get("task_id") or t.get("context_id")
        cn = A2A_TO_CN.get(st, "待办")
        # 进度时间线：最近 3 条 history（带时间）
        tl = []
        for h in (t.get("history") or [])[-3:]:
            txt = (h.get("text") or "").strip()
            if txt:
                tl.append((_fmt_ts(h.get("ts")), txt))
        out.append({
            "tid": tid,
            "seq": t.get("seq") or "",
            "title": t.get("title", tid),
            "status_cn": cn,
            "assignee": t.get("assignee") or "—",
            "timeline": tl,
        })
    return out


def render_summary_card(tasks):
    """渲染 **v1 旧版卡片格式** 的汇总卡。

    关键约束（2026-07-20 实测）：飞书 im/v1/messages 直接塞 schema:2.0 内联 JSON 会被
    服务端强制降级成「请升级客户端」占位图；必须用 v1 格式（无 schema/config/body 包装、
    header.title 为 {tag:plain_text,content} 对象）才能正常渲染，且支持 PATCH 原地更新。
    """
    total = len(tasks)
    cnt = {}
    for t in tasks:
        cnt[t["status_cn"]] = cnt.get(t["status_cn"], 0) + 1
    stat_parts = []
    for k in ["进行中", "待审核", "阻塞", "待转发", "待办"]:
        if cnt.get(k):
            stat_parts.append("%s%s×%d" % (STATUS_EMOJI.get(k, ""), k, cnt[k]))
    stat_line = "　".join(stat_parts) if stat_parts else "无活跃任务"

    # 整体配色：有阻塞→红，无活跃→绿，否则蓝
    if cnt.get("阻塞"):
        template = "red"
    elif total == 0:
        template = "green"
    else:
        template = "blue"

    elements = [
        {"tag": "markdown",
         "content": "**📊 活跃任务总览**：共 **%d** 个　%s" % (total, stat_line)},
    ]

    if not tasks:
        elements.append({"tag": "markdown",
                        "content": "_当前没有进行中的任务，所有事项均已关闭。_"})
    else:
        for t in tasks:
            emoji = STATUS_EMOJI.get(t["status_cn"], "🟡")
            tl_lines = []
            for hm, txt in t["timeline"][-3:]:
                # 进度文本原样展示，避免截断导致 blocked/错误原因看不全
                tl_lines.append("`%s` %s" % (hm, txt))
            tl_md = "\n".join(tl_lines) if tl_lines else "_暂无进展_"
            block = (
                "**%s [#%s] %s**　`%s`\n"
                "负责：**%s**　状态：%s\n"
                "📈 %s"
            ) % (emoji, t["seq"] or "?", t["title"], t["tid"], t["assignee"], t["status_cn"], tl_md)
            elements.append({"tag": "hr"})
            elements.append({"tag": "markdown", "content": block})
            # 飞书卡按钮：审核通过 / 归档。点击即回灌 Orchestrator（经由按钮回调监听器），
            # 无需 Boss 人工转发，状态自动闭环。
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "✅ 审核通过"},
                        "type": "primary",
                        "value": {"action": "approve", "task_id": t["tid"]},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🗄 归档"},
                        "type": "default",
                        "value": {"action": "archive", "task_id": t["tid"]},
                    },
                ],
            })
        elements.append({"tag": "hr"})
        elements.append({"tag": "markdown",
                         "content": "_本卡随任务状态自动更新；点卡片上的「✅ 审核通过 / 🗄 归档」按钮即可处理，无需转发。_"})

    return {
        "header": {
            "title": {"tag": "plain_text", "content": "📋 Orbit Hive 任务总览"},
            "template": template,
        },
        "elements": elements,
    }


def push_summary():
    """刷新汇总卡：有 message_id 则 PATCH 原地更新，否则新发并保存 message_id。"""
    tasks = fetch_tasks()
    card = render_summary_card(tasks)
    mid = _load_mid()
    if mid and LIVE:
        ok, _ = task_card.update_card(mid, card)
        if ok:
            print("   [summary] PATCH 更新汇总卡 (任务数=%d)" % len(tasks))
            return
        print("   [summary] PATCH 失败，回退为新发")
    if LIVE:
        new_mid = task_card.push_card(card)
        if new_mid:
            _save_mid(new_mid)
            print("   [summary] 新发汇总卡 mid=%s (任务数=%d)" % (new_mid, len(tasks)))
        else:
            print("   [summary] 新发但未能获取 message_id")
    else:
        print(json.dumps(card, ensure_ascii=False))


def main():
    print(">>> [stage3] 单卡汇总 adapter 启动，订阅 %s (live=%s)" % (ORCH_URL, LIVE))
    dirty = {"v": True}  # 启动即刷一次

    def refresher():
        while True:
            time.sleep(2)
            if dirty["v"]:
                dirty["v"] = False
                try:
                    push_summary()
                except Exception as e:
                    print("   [summary] 刷新失败:", e)

    threading.Thread(target=refresher, daemon=True).start()

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
                        if etype == "connected":
                            dirty["v"] = True  # 重连补刷
                        continue
                    # 任意事件都置脏，后台 2s 合并刷新
                    dirty["v"] = True
        except Exception as e:
            print("   [stage3] SSE 断开，3s 后重连:", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
