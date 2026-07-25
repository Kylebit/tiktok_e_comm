# -*- coding: utf-8 -*-
"""
给飞书战情室补发「HTML 交付物直达链接」卡（v1 格式，含可点击链接）。

复用 task_card._run 的 node 直调通道，规避 cmd.exe 的 GBK 元字符截断。
链接由本机 Orbit Hive 交付物静态服务(8790)托管。
"""
import json
import os
import sys

AGENT_PR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if AGENT_PR not in sys.path:
    sys.path.insert(0, AGENT_PR)

import task_card  # noqa: E402

BASE = "http://127.0.0.1:8790"

CARD = {
    "header": {
        "title": {"tag": "plain_text", "content": "📑 HTML 交付物直达链接（#0037 / #0038）"},
    },
    "elements": [
        {
            "tag": "markdown",
            "content": (
                "**#0037 探索跟进：图片功能 + AI 电商图片集成建议（HTML 报告）**\n"
                "[➡️ 点击在此打开报告](%s/reports/cursor_image_features_report.html)"
            ) % BASE,
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": (
                "**#0038 探索跟进：用内置浏览器做店铺日常运营巡检（可行性评估 HTML 报告）**\n"
                "[➡️ 点击在此打开报告](%s/reports/codex_browser_ops_report.html)"
            ) % BASE,
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": "_链接由本机 Orbit Hive 交付物服务(端口 8790)托管，点击即在你本机浏览器直接弹出，无需找原文件。_",
        },
    ],
}


def main():
    mid = task_card.push_card(CARD)
    print("MESSAGE_ID=%s" % mid)
    if mid:
        print("OK: 已发送带链接的飞书卡到战情室")
    else:
        print("WARN: 未拿到 message_id，请检查 lark-cli 输出")


if __name__ == "__main__":
    main()
