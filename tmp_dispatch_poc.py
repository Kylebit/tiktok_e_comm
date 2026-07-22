# -*- coding: utf-8 -*-
"""派发《小规模商品套图验证方案》三人协作任务：Codex(§4§5) + Cursor(§1§2)。
CEO 肉肉亲自做 §0§3§6。最终合并为单报告。"""
import json, urllib.request, urllib.error

ORCH = "http://127.0.0.1:8773"
WEBHOOK = "http://127.0.0.1:8774"


def _post(url, payload, timeout=15):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa
        return 0, str(e)


CODEX_PROMPT = """你是 Orbit Codex，参与一份三人协作的《小规模商品套图验证方案》报告。请独立完成你负责的章节，写入文件 `reports/linkfox_poc_frag_codex.md`（仓库根目录下的相对路径）。

【你负责的章节】
## 4. Linkfox 商品套图功能调研与对比
- 调研 Linkfox 当前"商品套图"功能的完整能力：技术栈、核心能力、支持生成的图片类型（卖点图/场景图/模特图/细节图/白底图等）、生成流程（输入→处理→输出）、定价与限制。
- 与我们方案（见其他章节：采集→提取→GPT Image/Gemini 生成）做逐项详细对比（能力 / 成本 / 可控性 / 外部依赖）。
- 搜索 GitHub 上相关的开源项目作为参考（如 ComfyUI 商品图工作流、背景替换、虚拟试穿 OOTDiffusion/CatVTON、以图生图、IP-Adapter 等），列出项目名 + 链接 + 可借鉴点。

## 5. 可行性评估（API 调用能力确认）
- 确认当前环境是否具备 GPT Image (OpenAI gpt-image-1) 或 Gemini (Nano Banana / gemini-2.5-flash-image) 的 API 调用能力。
- 仅做非付费的环境核查：检查本机环境变量（OPENAI_API_KEY / GEMINI_API_KEY / GOOGLE_API_KEY 是否设置、长度是否合理）、项目内是否有调用客户端、公开文档的当前价格与配额。不要实际发起任何付费 API 调用。
- 若不具备，给出明确替代方案（如申请 key、用 Google AI Studio 免费额度、或本地 ComfyUI/SDXL 兜底）。

【输出要求】
- 只写上述两章，使用 `## 4.` `## 5.` 二级标题，内部用 `###` 三级标题细分。
- 引用链接用 markdown `[文字](url)`。
- 结论要具体、可落地，避免空话；给出可操作的下一步。
- 写完后：git add 该文件并 commit（Commit 信息 `ORB-TASK-linkfox-poc(Bot): Codex 商品套图方案 §4§5`），push origin/master。
- 回报：运行 `python agent_comms/stage3/codex_adapter.py report --task-id linkfox-poc-codex --text "DONE：§4 Linkfox 调研+对比、§5 可行性评估已完成"` 然后 `python agent_comms/stage3/codex_adapter.py complete --task-id linkfox-poc-codex`。
- 绝对不要伪造 DONE / commit hash / 测试通过。
"""

CURSOR_PROMPT = """你是 Orbit Cursor，参与一份三人协作的《小规模商品套图验证方案》报告。请独立完成你负责的章节，写入文件 `reports/linkfox_poc_frag_cursor.md`（仓库根目录下的相对路径）。

【你负责的章节】
## 1. 商品信息采集方案
- 目标：接收 SKUID 或 1688 / Temu 商品链接，获取商品标题与主图 / 详情图。
- 评估两种采集方式并选型：
  (a) 直接采集：调用平台 API / 公开页面解析（需要哪些凭据、稳定性、成本、合规风险）；
  (b) 妙手采集：复用项目已有的"上品"采集路径（前端粘链接 → 妙手采集箱 → 生成预览）。
- 结论：选更简单、更稳的方案，给出落地步骤与接口 / 入口。

## 2. 商品信息提取与图片类型设计
- 基于获取的标题 + 图片，设计"信息提取"逻辑：从标题 / 图片提取哪些关键字段（品类、材质、卖点、适用场景、风格、目标人群等）。
- 总结需要生成的图片类型（如：卖点图、场景图、白底图、模特图、细节特写、尺寸图、对比图等），并为每种类型写出**可喂给 GPT Image / Gemini 的生成描述模板**（含风格 / 构图 / 光影 / 文字叠加等指示）。
- 给出"提取 → 生成描述"的映射流程。

【输出要求】
- 只写上述两章，使用 `## 1.` `## 2.` 二级标题，内部用 `###` 三级标题细分。
- 生成描述模板要具体、可直接复用到 API prompt。
- 写完后：git add 该文件并 commit（Commit 信息 `ORB-TASK-linkfox-poc(Bot): Cursor 商品套图方案 §1§2`），push origin/master。
- 回报：运行 `python agent_comms/stage3/cursor_adapter.py report --task-id linkfox-poc-cursor --text "DONE：§1 采集选型、§2 信息提取与图片类型设计已完成"` 然后 `python agent_comms/stage3/cursor_adapter.py complete --task-id linkfox-poc-cursor`。
- 绝对不要伪造 DONE / commit hash / 测试通过。
"""


def dispatch_codex():
    tid = "linkfox-poc-codex"
    print("== Codex /dispatch ==")
    s, b = _post(ORCH + "/dispatch", {
        "assignee": "Orbit Codex", "task_id": tid,
        "title": "商品套图验证方案 §4 Linkfox调研对比 + §5 可行性",
        "prompt": CODEX_PROMPT, "mode": "direct"})
    print("  /dispatch ->", s, b[:120])
    print("== Codex /exec (webhook 无头唤醒) ==")
    s, b = _post(WEBHOOK + "/exec", {
        "task_id": tid, "title": "商品套图验证方案 §4§5", "prompt": CODEX_PROMPT})
    print("  /exec ->", s, b[:160])


def dispatch_cursor():
    tid = "linkfox-poc-cursor"
    print("== Cursor /dispatch (direct) ==")
    s, b = _post(ORCH + "/dispatch", {
        "assignee": "Orbit Cursor", "task_id": tid,
        "title": "商品套图验证方案 §1 采集选型 + §2 信息提取与图片类型",
        "prompt": CURSOR_PROMPT, "mode": "direct"})
    print("  /dispatch ->", s, b[:160])


if __name__ == "__main__":
    dispatch_codex()
    dispatch_cursor()
    print("DONE dispatch")
