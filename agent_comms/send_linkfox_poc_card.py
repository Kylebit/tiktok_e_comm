#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 LinkFox 商品套图验证方案（三人协作单报告）发到飞书战情室，带可点 HTML 直开链接。"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_PR = HERE.parent.parent  # agent_comms -> tiktok_e_comm -> Agent_PR
sys.path.insert(0, str(AGENT_PR))

import task_card  # noqa: E402

WAR_ROOM = "oc_98de01670b5de146734f7530e0a1f83c"
URL = "http://127.0.0.1:8790/reports/linkfox_poc.html"
TITLE = "📦 商品套图验证方案（三人协作·单报告）"

CARD = {
    "config": {"wide_screen_mode": True},
    "header": {
        "title": {"tag": "plain_text", "content": "📦 LinkFox 商品套图·小规模验证方案（三人协作）"},
        "template": "blue",
    },
    "elements": [
        {"tag": "div", "text": {"tag": "lark_md", "content": (
            "**三人同一份报告**：CEO 肉肉（§0/§3/§6）＋ Orbit Cursor（§1/§2）＋ Orbit Codex（§4/§5）。\n"
            "流水线：**妙手采集 → 信息提取 → GPT Image/Gemini 生成 → LinkFox 对比 → 可行性评估**。"
        )}},
        {"tag": "div", "text": {"tag": "lark_md", "content": (
            f"🔗 [{TITLE}]({URL})\n"
            "（点击在本机浏览器弹出，需 8790 静态服务在线）"
        )}},
        {"tag": "div", "text": {"tag": "lark_md", "content": (
            "**结构**：§0 概述｜§1 采集(妙手)｜§2 提取+图片类型模板｜§3 生成方案与成本｜"
            "§4 LinkFox 调研对比｜§5 可行性(API 能力)｜§6 落地建议与待拍板"
        )}},
        {"tag": "hr"},
        {"tag": "div", "text": {"tag": "lark_md", "content": (
            "**关键结论**：①采集选妙手(已实现入口)；②GPT Image 1 Mini ~$0.005/张、Gemini Flash ~$0.024/张，"
            "仅为 LinkFox 1/10~1/3；③`OPENAI_API_KEY` 已设可起步，`GEMINI_API_KEY` 未设；④图像端点权限/计费未验证，**仅可开发、不可声称已具备出图能力**。"
        )}},
        {"tag": "div", "text": {"tag": "lark_md", "content": (
            "**待 Boss 拍板**：①授权 OpenAI 图像冒烟测试？②补 GEMINI_API_KEY？③MVP 范围(10 SKU×4图类)？④让 Codex 写生成流水线脚本？"
        )}},
    ],
}

if __name__ == "__main__":
    mid = task_card.push_card(CARD)
    print("MESSAGE_ID=%s" % mid)
    if mid:
        print("OK: 已发送 LinkFox 商品套图验证方案飞书卡（含可点 HTML 链接）到战情室")
    else:
        print("WARN: 未拿到 message_id，请检查 lark-cli 输出")
