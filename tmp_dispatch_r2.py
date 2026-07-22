# -*- coding: utf-8 -*-
"""Round-2 LinkFox 调研派发：直派 Codex(无头唤醒) + Cursor(收件箱)。"""
import json
import urllib.request

ORCH = "http://127.0.0.1:8773"
WEBHOOK = "http://127.0.0.1:8774"
REPO = r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm"


def post(url, obj):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return "ERR", str(e)


CODEX_PROMPT = """你是 Orbit Codex。任务：调研 GitHub 上【开源、公开、可自托管】的 skill / 项目 / agent，能复刻 LinkFox 的以下场景（尽量不依赖付费 API）：

1) 1688 货源相关：开源的 1688 商品搜索 / 以图搜图 / 供应商查询 / 采购下单 的项目或 agent skill（含 MCP server、Playwright 爬虫、API wrapper、agent-skills 仓库）。
2) 图像生成相关：开源的 图生图(image-to-image) / 商品图生成 / 虚拟试穿(virtual try-on) / 换背景 / 局部重绘 项目（ComfyUI 自定义节点、Fooocus、SD WebUI 插件、相关 GitHub repo）。
3) agent-skills 生态：类似 linkfox-ai/linkfox-skills 这种开放 skill 标准（Agent Skills / MCP / Claude skills / Cursor skills）里，有没有电商 / 图像 / 货源方向的现成开源 skill 仓库或 awesome 列表。

对每个找到的项目，给出：仓库 URL、一句话功能、开源协议、成熟度（stars / 最近更新 / 是否维护中）、它对应 LinkFox 的哪个功能、是否真能本地跑。最后给一张【LinkFox 功能 → 开源替代】映射表。

注意：本机 linkfox.com 可能直连不通（返回 000），但 github.com 直连 200。优先用 `gh` CLI 或 `curl` 抓 GitHub 搜索结果 / repo 页面 / awesome 列表；用 WebSearch 辅助。若某项实在找不到开源替代，明确写「暂无成熟开源替代」并说明原因，不要编造仓库 URL。

产出：把完整调研写入 `reports/linkfox_r2_codex.md`（中文，含表格与链接）。
回报：完成后依次运行：
  python agent_comms/stage3/codex_adapter.py --report --task-id linkfox-r2-codex --text "调研完成，见 reports/linkfox_r2_codex.md"
  python agent_comms/stage3/codex_adapter.py --complete --task-id linkfox-r2-codex
最后：
  git add reports/linkfox_r2_codex.md && git commit -m "ORB-TASK-linkfox-r2-codex(Codex): 开源替代 LinkFox 的 GitHub skill 调研" && git push origin master
并把 commit hash 通过 --report 回传。
绝对不要伪造 DONE / commit hash / 测试通过。若无法完成，运行 --report 写明 BLOCKED 及原因。
"""

CURSOR_PROMPT = """你是 Orbit Cursor。任务：调研【以 GPT Image（OpenAI gpt-image-1）+ Google Gemini Nano Banana（gemini-2.5-flash-image，俗称 banana）为生成底座，搭配 Codex 这类编程 agent 做编排，能否复刻 LinkFox 的生图功能】。

覆盖：
1) 两个底座模型各自能力边界：文生图、图生图、局部编辑(inpainting)、风格/角色一致性、版面与文字渲染（商品图常需干净背景+文字）、速度、定价、API 接入方式（OpenAI Images API / Google Gemini API）、是否需要 key。
2) Codex 如何编排：写 Python 脚本调用 OpenAI/Gemini SDK、批量生图、串联多步编辑（如先换背景→再局部重绘→再加文字）、用文件系统做中间产物、把流程沉淀成可复用脚本——即【agent 驱动的生图流水线】。
3) 映射：LinkFox 的 商品套图 / 换背景 / 局部重绘 / AI模特换装 / 相似图裂变 / 智能修图，哪些用这两个底座+Codex 就能做，哪些仍需开源（如强一致性人脸用 PuLID、强姿态控制用 ControlNet）。
4) 成本对比：GPT Image / Banana 每张约多少（美元/张），对比 LinkFox 0.11–1 元/张、对比本地开源≈0。

产出：把完整调研写入 `reports/linkfox_r2_cursor.md`（中文，含表格与链接）。
回报：完成后依次运行：
  python agent_comms/stage3/cursor_adapter.py --report --task-id linkfox-r2-cursor --text "调研完成，见 reports/linkfox_r2_cursor.md"
  python agent_comms/stage3/cursor_adapter.py --complete --task-id linkfox-r2-cursor
最后：
  git add reports/linkfox_r2_cursor.md && git commit -m "ORB-TASK-linkfox-r2-cursor(Cursor): GPT Image/Banana+Codex 复刻 LinkFox 生图调研" && git push origin master
绝对不要伪造 DONE / commit hash。若无法完成，运行 --report 写明 BLOCKED 及原因。
"""


def main():
    # ---- Codex: 直派 + 无头唤醒 ----
    tid_c = "linkfox-r2-codex"
    print("== dispatch Codex ==")
    s, b = post(ORCH + "/dispatch", {
        "assignee": "codex", "task_id": tid_c,
        "title": "开源 GitHub 替代 LinkFox 的 skill 调研",
        "prompt": CODEX_PROMPT, "mode": "direct"})
    print("  /dispatch ->", s, b[:200])
    s2, b2 = post(WEBHOOK + "/exec", {
        "task_id": tid_c, "title": "开源 GitHub 替代 LinkFox 的 skill 调研",
        "prompt": CODEX_PROMPT})
    print("  /exec ->", s2, b2[:200])

    # ---- Cursor: 直派（收件箱 + ACK + 唤醒 tick）----
    tid_u = "linkfox-r2-cursor"
    print("== dispatch Cursor ==")
    s3, b3 = post(ORCH + "/dispatch", {
        "assignee": "cursor", "task_id": tid_u,
        "title": "GPT Image/Banana + Codex 复刻 LinkFox 生图调研",
        "prompt": CURSOR_PROMPT, "mode": "direct"})
    print("  /dispatch ->", s3, b3[:200])


if __name__ == "__main__":
    main()
