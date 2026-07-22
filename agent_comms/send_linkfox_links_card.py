# -*- coding: utf-8 -*-
"""
给飞书战情室补发「LinkFox 全面调研 · 三方 HTML 报告」直达链接卡（v1 格式，含可点击链接）。

复用 task_card._run 的 node 直调通道，规避 cmd.exe 的 GBK 元字符截断。
链接由本机 Orbit Hive 交付物静态服务(8790)托管，点击即本机浏览器弹出。
"""
import os
import sys

AGENT_PR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if AGENT_PR not in sys.path:
    sys.path.insert(0, AGENT_PR)

import task_card  # noqa: E402

BASE = "http://127.0.0.1:8790"

CARD = {
    "header": {
        "title": {"tag": "plain_text", "content": "📑 LinkFox 全面调研 · 三方 HTML 报告直达"},
    },
    "elements": [
        {
            "tag": "markdown",
            "content": (
                "**① Codex · 商品图功能调研（Topic 1）**\n"
                "[➡️ 点击在此打开报告](%s/reports/linkfox_research_codex.html)"
            ) % BASE,
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": (
                "**② CEO 肉肉 · 总控视角（三方合订 + 自建平替方案）**\n"
                "[➡️ 点击在此打开报告](%s/reports/linkfox_research_ceo.html)"
            ) % BASE,
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": (
                "**③ Cursor 任务补位 · Skills 体系与自建小系统（Topic 2）**\n"
                "[➡️ 点击在此打开报告](%s/reports/linkfox_research_cursor.html)"
            ) % BASE,
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": (
                "_说明：Cursor 子任务(task-linkfox-02)本机自动执行桥(notify_on_output 唤醒)未触发，未自跑；"
                "Topic 2 由 CEO 依据公开资料补位撰写。三份均为 HTML，自包含可直开。_"
            ),
        },
    ],
}


def main():
    mid = task_card.push_card(CARD)
    print("MESSAGE_ID=%s" % mid)
    if mid:
        print("OK: 已发送 LinkFox 调研飞书卡（含 3 个可点 HTML 链接）到战情室")
    else:
        print("WARN: 未拿到 message_id，请检查 lark-cli 输出")


if __name__ == "__main__":
    main()
