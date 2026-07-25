#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render the Chinese operator-facing image-generation knowledge report."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = ROOT / "knowledge" / "image_generation" / "v1.json"
OUT = ROOT / "reports" / "image_generation_knowledge_and_pipeline.html"


def main() -> int:
    knowledge = json.loads(KNOWLEDGE.read_text(encoding="utf-8"))
    version = str(knowledge.get("version") or "unknown")
    body = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Orbit 生图知识库与运营流程</title><style>
:root{{--ink:#14212b;--muted:#52616b;--line:#d7dfe4;--paper:#fff;--canvas:#f3f6f7;--blue:#086998;--green:#176b4d;--amber:#9a6100;--red:#a43030}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--canvas);color:var(--ink);font-family:"Microsoft YaHei","Segoe UI",Arial,sans-serif;line-height:1.65}}main{{max-width:1180px;margin:auto;padding:34px 24px 72px}}h1{{margin:0;font-size:31px;line-height:1.2}}h2{{margin:42px 0 14px;font-size:22px}}h3{{margin:0 0 7px;font-size:16px}}p{{margin:7px 0}}.lede,.muted{{color:var(--muted)}}.tag{{display:inline-block;padding:3px 8px;font-size:12px;font-weight:700}}.done{{color:var(--green);background:#e8f4ed}}.human{{color:var(--amber);background:#fff4dc}}.paid{{color:var(--red);background:#fbeaea}}.system{{color:#17455e;background:#e6f1f8}}.llm{{color:#5a3e7c;background:#f1eafa}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}.grid.three{{grid-template-columns:repeat(3,minmax(0,1fr))}}article,.callout{{background:var(--paper);border:1px solid var(--line);padding:16px}}.callout{{border-left:4px solid var(--blue);margin:16px 0}}.callout.warn{{border-left-color:var(--amber);background:#fff8e8}}table{{width:100%;border-collapse:collapse;background:var(--paper)}}td,th{{padding:10px 11px;border:1px solid var(--line);vertical-align:top;text-align:left}}th{{background:#edf2f5;font-size:13px}}ol,ul{{padding-left:22px}}li{{margin:6px 0}}code{{padding:1px 4px;background:#edf2f5;color:#17455e;font-family:Consolas,monospace}}.flow{{counter-reset:step}}.flow article{{position:relative;padding-left:70px;margin:10px 0}}.flow article::before{{counter-increment:step;content:counter(step);position:absolute;left:17px;top:16px;width:32px;height:32px;border-radius:50%;display:grid;place-items:center;background:#e6f1f8;color:var(--blue);font-weight:800}}details{{margin:10px 0;background:#f6f8f9;border:1px solid var(--line);padding:9px 12px}}summary{{cursor:pointer;font-weight:700}}@media(max-width:760px){{main{{padding:24px 14px 50px}}.grid,.grid.three{{grid-template-columns:1fr}}h1{{font-size:27px}}td,th{{padding:8px}}}}
</style></head><body><main>
<header><p class="tag done">当前生效版本：{version}</p><h1>生图知识库与运营流程</h1><p class="lede">这是给运营审核与 Agent 执行共用的总规则页。它说明什么能自动做、什么必须人工确认，以及每张真实生成图如何被追溯。</p></header>

<section><h2>一、总原则</h2><div class="grid three">
<article><span class="tag done">已固化</span><h3>证据优先</h3><p>商品事实分为“已验证、视觉推断、未知/禁止”。品牌、认证、材质、性能、组件、尺寸、价格都不能靠模型猜。</p></article>
<article><span class="tag done">已固化</span><h3>商品身份锁定</h3><p>指定的一张来源图是商品身份锚点。模型只能改变镜头、光线、背景与必要场景，不能重画产品设计。</p></article>
<article><span class="tag human">人工把关</span><h3>生成不等于可上架</h3><p>下载成功只代表技术完成。商品一致性、英文文案、卖点与合规性，仍需人工审核后才能进入上架素材。</p></article>
</div><aside class="callout"><b>可见文字规则：</b>默认生成无文字底图，英文卖点、箭头、尺寸和标签优先后期确定性叠加。若模型意外生成可读英文，不自动直接丢弃，而是进入人工“文案与声明审核”；未审核前不能用于上架。</aside></section>

<section><h2>二、谁参与：职责边界</h2><div class="grid three">
<article><span class="tag human">你 / 运营负责人</span><h3>决定与放行</h3><p>选择商品、补充人工素材、确认事实、批准付费、审核商品身份与文案、批准写回妙手及最终发布。你不需要逐个操作 API。</p></article>
<article><span class="tag system">Orbit Treasury / 本地脚本</span><h3>编排与留痕</h3><p>创建发布档案，读取妙手采集箱，校验必填项，保存状态、素材、审核动作和审计记录；只在已放行的范围内调用外部 API。</p></article>
<article><span class="tag llm">大模型</span><h3>受约束的理解与创作</h3><p>可辅助来源图分析、类目匹配、分镜规划、英文 Prompt、图像生成和标题/描述草案；不能自行确定商品事实、做最终审核或直接发布。</p></article>
<article><span class="tag system">妙手 API</span><h3>采集箱与草稿系统</h3><p>提供/读取采集箱内容，创建和更新草稿，并在写回后回读验证。它不判断图片是否真实、文案是否合规。</p></article>
<article><span class="tag system">ToAPI 图像接口</span><h3>真实生成服务</h3><p>接收已批准的英文 Prompt 与公开参考图，返回任务和图片。它只负责生成，不负责商品事实、平台规则或人工审批。</p></article>
<article><span class="tag system">下游 Agent</span><h3>分渠道发布与迁移</h3><p>Cursor、Claude Code 等只接收已发布的标准化发布包，按各自站点规则生成草稿或迁移；不得跳过上游审核擅自使用待审核素材。</p></article>
</div><aside class="callout warn"><b>必须牢记：</b>模型输出、API 成功、文件下载成功都不是“上架批准”。唯一能把素材或草稿状态从“待审核”变为“可发布”的，是你在 Treasury 的明确审核动作。</aside></section>

<section><h2>三、从 Orbit 到生图 Agent 的交接</h2><div class="flow">
<article><h3>Orbit 完成妙手采集箱</h3><p>交接锚点是妙手采集箱 ID，而不是聊天里散落的标题、图片和规格。生图 Agent 只读采集箱，不会擅自写回妙手或上架。</p></article>
<article><h3>读取并建立事实卡</h3><p>采集标题、来源图、SKU、规格、材质、属性和来源链接；同时标出冲突字段，例如不同来源重量不一致时禁止写入图片。</p></article>
<article><h3>匹配类目规则与分镜</h3><p>根据类目选择受约束的套图，不套用通用“六图模板”。输出中文审核页，以及实际调用时使用的英文 Prompt。</p></article>
<article><h3>人工批准付费范围</h3><p>默认不调用 <code>/v1/images/generations</code>。批准后只生成批准的分镜；首次生成与重试都分别留下记录。</p></article>
<article><h3>真实任务、轮询与下载验证</h3><p>每个任务保存脱敏 payload、任务 ID、创建返回、轮询状态、最终返回、下载结果和图片文件核验。不得伪造任何成功状态。</p></article>
<article><h3>人工验收与后期交付</h3><p>审核通过的无文字素材可进入后期英文叠字与上架素材；待审核或不适合的结果保留在报告中，但不自动进入上架。</p></article>
</div></section>

<section><h2>四、完整步骤与参与矩阵</h2><p class="lede">这是未来“Orbit Treasury 新品发布台 + 生图系统”应实现的标准流程。标为“必须”的人工点会阻断下一步；其他步骤由系统执行并留下可审计记录。</p><table><thead><tr><th>步骤</th><th>主要执行者</th><th>API / 大模型介入</th><th>你需要做什么</th><th>门禁与交付物</th></tr></thead><tbody>
<tr><td>1. 新建发布档案</td><td>Treasury</td><td>无；只写本地状态</td><td>输入或确认来源链接、店铺、目标渠道、采集箱 ID。</td><td>建立唯一 <code>offer_id</code>，关联采集箱 ID；未建档不得进入后续链路。</td></tr>
<tr><td>2. 采集箱读取与快照</td><td>Treasury + 妙手 API</td><td>妙手读取 API；无需模型</td><td>通常无需操作；读取异常或字段缺失时补充来源。</td><td>保存原始标题、SKU、图片、规格与读取时间，形成不可覆盖的来源快照。</td></tr>
<tr><td>3. 事实卡与风险识别</td><td>Treasury + 大模型辅助</td><td>模型可提取和归类；规则引擎校验冲突</td><td><b>必须审核</b>已验证事实、视觉推断、未知/禁止项，以及唯一商品身份图。</td><td>人工确认的事实卡。未确认时，不可生成带卖点、尺寸或材质主张的图片。</td></tr>
<tr><td>4. 选图与类目分镜</td><td>Treasury + 大模型辅助</td><td>系统基于事实卡和知识库规划不同场景；不调用生图 API</td><td><b>必须审核</b>五类图片的本次数量、参考图、人工尺寸和不允许的内容。</td><td>默认一次生成完整批准套图。墙贴推荐三场景 + 一卖点；白底、尺寸和细节默认关闭，但可由运营修改数量。</td></tr>
<tr><td>5. 批准付费生成</td><td>你</td><td>无</td><td><b>必须明确批准</b>具体分镜和可接受的付费范围。</td><td>写入批准事件与时间。未批准时系统只能生成预览、Prompt 和报告，不能发起真实图像任务。</td></tr>
<tr><td>6. 生成 Prompt 与真实出图</td><td>Treasury + 大模型 + ToAPI</td><td>模型生成受规则约束的英文 Prompt；ToAPI <code>/v1/images/generations</code> 真实调用</td><td>无需逐张等待；系统遇到错误会标记并通知你。</td><td>每张图的脱敏请求、任务 ID、轮询、最终返回、下载验证和原始文件；不得伪造成功。</td></tr>
<tr><td>7. 技术质检</td><td>本地脚本</td><td>文件尺寸/可打开性校验；可选模型辅助风险提示</td><td>通常无需操作。</td><td>区分“技术完成”和“待人工审核”。技术通过不等于能上架。</td></tr>
<tr><td>8. 商品与文案审核</td><td>你 / 运营负责人</td><td>可使用模型给出风险提示，但不代替审核</td><td><b>必须逐张审核</b>商品身份、平面属性、场景合理性、英文拼写、卖点证据与平台合规。</td><td>每张素材：采用、待后期、重做或弃用。模型意外生成的英文进入此关，不自动否决也不自动放行。</td></tr>
<tr><td>9. 后期确定性叠字</td><td>Treasury / 本地脚本</td><td>无需模型；使用你已批准的英文文案与已验证尺寸</td><td><b>必须审核</b>首次采用的英文文案、尺寸、箭头和最终排版。</td><td>保留无文字底图和带字成品的对应关系。没有证据的性能词不得进入成品。</td></tr>
<tr><td>10. 形成上架素材包</td><td>Treasury</td><td>无；可对图像清单做格式校验</td><td><b>必须批准</b>主图、详情图顺序、标题、卖点与描述最终版本。</td><td>版本化素材包：文件、顺序、文案、事实卡版本、审批记录和适用渠道。</td></tr>
<tr><td>11. 写回妙手并回读</td><td>Treasury + 妙手 API</td><td>妙手写入/读取 API；无需模型</td><td><b>必须批准写回</b>，并在回读页面确认草稿内容。</td><td>同一采集箱/草稿的写回结果与回读快照。写入失败或内容不一致时自动阻断发布。</td></tr>
<tr><td>12. TikTok 东南亚发布</td><td>Orbit / Treasury + 平台自动化</td><td>平台 API 或本地已授权自动化；模型仅可辅助渠道化文案</td><td><b>必须完成二次上架审核</b>后才允许提交。</td><td>店铺、站点、平台 SKU、发布时间和发布结果；失败可重试，已提交不可静默覆盖。</td></tr>
<tr><td>13. 下游渠道分发</td><td>Hive 大脑 + Cursor / Claude Code</td><td>GitHub/稳定任务队列或消息系统；各渠道 API/脚本</td><td>确认下游范围与优先级；处理异常或本地权限请求。</td><td>只发送“已发布/已批准的发布包”。Cursor 处理 TikTok MX/英国；Claude Code 处理 Shopee/Ozon，并回传各自草稿结果。</td></tr>
<tr><td>14. 归档、监控与复用</td><td>Treasury + 各 Agent</td><td>无必需模型；可选模型用于异常归因</td><td>查看异常、抽检数据，决定哪些规则沉淀到知识库。</td><td>完整审计档案、渠道状态、素材复用记录和可追踪的规则更新。</td></tr>
</tbody></table></section>

<section><h2>五、全类目通用边界</h2><table><thead><tr><th>事项</th><th>规则</th><th>处理方式</th></tr></thead><tbody>
<tr><td>来源图版本不同</td><td>不能混合作为同一商品身份依据。</td><td>指定唯一身份参考；其余只作规格或组件证据。</td></tr>
<tr><td>整套生成</td><td>商品身份参考、事实卡和完整图片配方审核通过后，一次生成全部批准分镜。</td><td>预检绑定配方版本；配方变化后旧预检立即失效，禁止按旧方案付费。</td></tr>
<tr><td>尺寸图</td><td>模型不得生成数字、箭头、价格或尺寸文字。</td><td>先生成留白技术底图，再由已确认尺寸进行英文后期叠字。</td></tr>
<tr><td>模型生成的英文</td><td>英文可读不代表声明真实或可用。</td><td>逐条人工核对拼写、含义、来源证据与平台合规性。</td></tr>
<tr><td>付费与审计</td><td>无明确批准不发起真实生图。</td><td>真实调用必须可追溯，凭据与临时签名 URL 不写入报告。</td></tr>
</tbody></table></section>

<section><h2>六、墙贴类目专属规则</h2><div class="callout warn"><b>墙贴不是默认六图类目。</b>墙贴推荐“3 张不同场景图 + 1 张卖点图”，白底、尺寸和细节默认关闭。运营可调整五类图片数量；当原尺寸图不合格时，可锁定准确尺寸并例外生成无文字底图，再由本地程序确定性添加英文尺寸。</div>
<table><thead><tr><th>分镜</th><th>是否自动生成</th><th>规则</th></tr></thead><tbody>
<tr><td>白底图</td><td>否，人工输入</td><td>由运营提供并审核；系统不为墙贴付费生成白底图。</td></tr>
<tr><td>尺寸图</td><td>默认否，可人工例外开启</td><td>必须先确认准确尺寸。模型只生成无文字底图，本地程序添加英文尺寸并重新上传；后续仍需逐张人工审核。</td></tr>
<tr><td>客厅场景</td><td>是</td><td>墙贴应平整贴在合理墙面，完整图案可辨识，不做门贴、装饰画或立体摆件。</td></tr>
<tr><td>卧室/玄关场景</td><td>是</td><td>依据来源支持的使用空间安排，保持合理尺寸与无遮挡画面。</td></tr>
<tr><td>浴室场景</td><td>是，前提是来源支持</td><td>可使用来源标题或属性支持的浴室用途；场景表现干爽，不自行添加防水性能文案。</td></tr>
<tr><td>卖点图</td><td>是</td><td>只突出来源支持的印花和表面。出现英文或卖点声明时，进入人工文案审核，不自动判废。</td></tr>
<tr><td>细节图</td><td>否</td><td>墙贴默认取消细节图；避免近景把平面印花误表现成真实立体植物或其他材质。</td></tr>
</tbody></table>
<p><b>墙贴不变形规则：</b>保持平面贴纸的几何关系与印花排版；不得凭空宣传可移除、无残胶、胶层、防水、厚度或其他性能。除非有来源证据，否则不出现这些主张。</p></section>

<section><h2>七、审核状态语言</h2><div class="grid"><article><span class="tag done">技术完成</span><h3>已创建、已轮询、已下载</h3><p>任务与文件都成功，只说明系统链路正常。</p></article><article><span class="tag human">待人工审核</span><h3>可见英文、卖点、身份或平面属性有争议</h3><p>素材可留存并展示，不自动上架；运营确认后决定采用、后期修图或重做。</p></article><article><span class="tag paid">不适合上架</span><h3>证据不足或违反明确规则</h3><p>例如虚构认证、错误产品、不可接受的文字或错误规格。保留审计，不进入上架素材。</p></article></div></section>

<section><h2>八、运营查看入口</h2><p>每个采集箱会生成一份中文 <code>review_report.html</code>，其中集中展示来源图、事实卡、分镜、真实图片、任务 ID、轮询、重试与脱敏 API 过程。英文原始 Prompt 仅在折叠区保留，方便审计，不作为运营页面的主语言。</p><p>当前案例报告：<code>outputs/image_suite_from_miaoshou/&lt;采集箱ID&gt;/review_report.html</code>。</p></section>
</main></body></html>"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(body, encoding="utf-8")
    print(OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
