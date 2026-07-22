# -*- coding: utf-8 -*-
"""
给飞书战情室补发「LinkFox 生图能力第二轮调研」直达链接卡（v1 格式，含可点击链接）。
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
        "title": {"tag": "plain_text", "content": "🔍 LinkFox 生图能力第二轮调研 · HTML 直达"},
    },
    "elements": [
        {
            "tag": "markdown",
            "content": (
                "**① 肉肉 CEO：开源生图框架真实效果 + 补位 Cursor 的 Topic③**\n"
                "- Topic①：ComfyUI+SDXL+ControlNet+IP-Adapter / OOTDiffusion 虚拟试穿 / PuLID 人脸一致性 的真实效果，附**可直接点击的在线看图入口**（Civitai / HuggingFace / ComfyUI_examples / OpenArt）。\n"
                "- Topic③（补位 Cursor）：GPT Image + Gemini Nano Banana + Codex 编排，能否复刻 LinkFox 生图——结论：**能，且云模型比 LinkFox 更便宜**（GPT $0.01–0.17/张、Banana $0.03–0.08/张 vs LinkFox 0.11–1 元/张）。\n"
                "[➡️ 点击打开 CEO 报告](%s/reports/linkfox_r2_ceo.html)"
            ) % BASE,
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": (
                "**② Codex：开源 GitHub 替代 LinkFox 调研（Topic②）**\n"
                "- 1688：最贴近的是 `openclaw/skills` 的 `1688-product-search`（需官方凭据）；诚实缺口：**无成熟合规的免授权 1688 自动采购开源替代**。\n"
                "- 图像：ComfyUI / Fooocus / A1111 / rembg / CatVTON / IDM-VTON 等一整套可自托管。\n"
                "- Agent-Skills 生态：awesome-agent-skills 目录 + MCP 参考服务器。\n"
                "[➡️ 点击打开 Codex 报告](%s/reports/linkfox_r2_codex.html)"
            ) % BASE,
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": (
                "_说明：Cursor 子任务(task-linkfox-r2-cursor)已直派进收件箱，但其 IDE 自动执行桥(notify_on_output 唤醒)未触发、未自跑；Topic③ 由 CEO 依据公开资料补位撰写。两份均为 HTML，自包含可直开。_"
            ),
        },
    ],
}


def main():
    mid = task_card.push_card(CARD)
    print("MESSAGE_ID=%s" % mid)
    if mid:
        print("OK: 已发送 LinkFox 生图能力第二轮调研飞书卡（含 2 个可点 HTML 链接）到战情室")
    else:
        print("WARN: 未拿到 message_id，请检查 lark-cli 输出")


if __name__ == "__main__":
    main()
