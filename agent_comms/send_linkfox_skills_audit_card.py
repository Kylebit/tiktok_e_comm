# -*- coding: utf-8 -*-
"""
给飞书战情室补发「LinkFox 两个集成 Skill 用途调查」直达链接卡（v1 格式，含可点击链接）。

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
        "title": {"tag": "plain_text", "content": "🔍 LinkFox 两集成 Skill 用途调查 · HTML 直达"},
    },
    "elements": [
        {
            "tag": "markdown",
            "content": (
                "**调查结论速览**\n"
                "- `linkfox-1688-sourcing`（1688 货源）：**直接有用**，只读搜索/榜单/图搜可接入 Treasury 上品喂货源情报；采购下单为高风险写操作，须走 Boss 确认闸。\n"
                "- `linkfox-amazon-product-selection`（亚马逊选品）：当前**无 Amazon 业务块**，直接用处低；留作 Eyes 选品模块重建范本 + 未来扩张储备。\n"
                "- 两者均已完整安装（34/40 脚本），但**缺 `LINKFOX_AGENT_API_KEY` 暂不可调**，按次烧积分。"
            ),
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": (
                "**📑 完整用途调查报告（HTML，可直开）**\n"
                "[➡️ 点击在此打开报告](%s/reports/linkfox_skills_audit.html)"
            ) % BASE,
        },
        {"tag": "hr"},
        {
            "tag": "markdown",
            "content": (
                "_待 Boss 拍板：①是否开 LinkFox 账号拿 key 接 1688 只读货源；"
                "②1688 采购履约是否授权（授权则走哪级确认闸）；③积分预算阈值；④亚马逊 skill 是否一并开通。_"
            ),
        },
    ],
}


def main():
    mid = task_card.push_card(CARD)
    print("MESSAGE_ID=%s" % mid)
    if mid:
        print("OK: 已发送 LinkFox Skill 用途调查飞书卡（含可点 HTML 链接）到战情室")
    else:
        print("WARN: 未拿到 message_id，请检查 lark-cli 输出")


if __name__ == "__main__":
    main()
