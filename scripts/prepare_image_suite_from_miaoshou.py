#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create a reviewable, no-generation image-suite package from a Miaoshou box.

This is the hand-off boundary between Orbit's listing workflow and the image
workflow. It reads a common collect-box detail, writes local review artifacts,
and never edits Miaoshou or calls an image-generation endpoint.

Example:
  python scripts/prepare_image_suite_from_miaoshou.py --collect-box-id 3825215286
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.miaoshou.client import post_open  # noqa: E402
from modules.sourcing.image_generation_knowledge import load_knowledge, resolve_profile  # noqa: E402
from modules.sourcing.image_shot_prompts import build_shot_prompts, save_shot_prompts  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _attrs(detail: dict[str, Any]) -> dict[str, str]:
    return {
        str(row.get("name") or "").strip(): str(row.get("value") or "").strip()
        for row in (detail.get("sourceAttrs") or [])
        if isinstance(row, dict) and str(row.get("name") or "").strip()
    }


def _cm_spec(value: str) -> tuple[float, float] | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*[*xX×]\s*(\d+(?:\.\d+)?)\s*CM", value.upper())
    if not match:
        return None
    return float(match.group(1)), float(match.group(2))


def _safe_image_urls(detail: dict[str, Any]) -> list[str]:
    return [
        str(url).strip()
        for url in (detail.get("imgUrls") or [])
        if str(url).strip().startswith("https://")
    ]


def build_review_package(detail_id: int, detail: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic plan from source-supported collect-box fields."""
    attrs = _attrs(detail)
    images = _safe_image_urls(detail)
    source_spec = attrs.get("规格") or ""
    dimensions = _cm_spec(source_spec)
    source_weight_g = attrs.get("净重（含opp袋子）") or ""
    box_weight_g = round(float(detail.get("weight") or 0) * 1000) if detail.get("weight") else 0

    # The English title and all analysis fields are intentionally evidence-only.
    # We do not pass the supplier brand into image prompts.
    english_title = "Tropical Botanical PVC Wall Decal"
    if dimensions:
        english_title += f", {dimensions[0]:g} x {dimensions[1]:g} cm"

    profile = resolve_profile(title=english_title, category="wall decal")
    analysis = {
        "subject": "One flat tropical botanical wall decal sheet",
        "category": "wall decal",
        "theme": "tropical botanical foliage and floral decoration",
        "structure": "flat self-adhesive decal layout; preserve the exact print, cut edges, scale, and flat application",
        "background_of_source": "Miaoshou common collect box source images",
        "style_lock": (
            "Use the first source image as the sole visual identity reference. Preserve the exact tropical plant "
            "composition, colors, flat decal geometry, print layout, and proportions. Do not turn it into framed art, "
            "a hanging ornament, a sculpture, or a different botanical design."
        ),
        "materials": ["PVC"],
        "colors": [],
        "craft_details": ["flat printed decal surface"],
        "brand_dna": [],
    }

    # The profile establishes the shot types; this product-specific map supplies
    # concrete composition without introducing claims beyond the source record.
    shot_focus = {
        "wb1": "Show the complete flat decal sheet clearly, with its exact botanical print and cut-edge arrangement visible on a seamless white background.",
        "sc1": "Show the exact decal applied flat to a clean, light living-room feature wall. Keep the full print recognizable and do not add a frame, shelf product, or door use.",
        "sc2": "Show the exact decal as a flat bedroom wall accent in a calm indoor setting. Preserve believable size and leave the decal unobstructed.",
        "sc3": "Show the exact decal applied flat as a bathroom wall accent in a clean, dry-looking bathroom setting. Keep the full print recognizable and do not make waterproof performance claims in the image.",
        "sp1": "Highlight the source-supported tropical botanical print and flat PVC surface only. Do not show an adhesive layer, peeling action, or unverified performance claim.",
        "dt1": "Use a close crop of the same flat printed PVC decal, preserving the exact source pattern and visible cut edge without inventing texture or separate parts.",
        "sz1": "Place the full flat decal sheet on a white technical background with generous margin for a later approved English 30 x 60 cm overlay. Generate no text or arrows.",
    }
    shot_focus_zh = {
        "wb1": "在纯白背景中完整展示整张平面墙贴，热带植物图案和原始切边排版必须清晰可见。",
        "sc1": "将同一张墙贴平整贴在干净、浅色的客厅背景墙上。完整图案应可辨识，不得改成装饰画、置物架产品或门贴。",
        "sc2": "将同一张墙贴作为卧室墙面点缀，保持合理尺寸，画面不得遮挡墙贴。",
        "sc3": "将同一张墙贴平整贴在干净、干爽感的浴室墙面上，完整图案应可辨识；图片中不得出现防水性能承诺。",
        "sp1": "仅突出已证实的热带植物印花与平面 PVC 表面；不得展示背胶剥离、揭起动作或没有证据的性能卖点。",
        "dt1": "近距离拍摄同一平面 PVC 墙贴的印花和可见切边；不得虚构纹理、厚度或独立零件。",
        "sz1": "整张墙贴置于白色技术底图，四周留足后期添加已确认英文“30 x 60 cm”尺寸标注的空间；模型不得生成文字或箭头。",
    }
    suite_items = []
    for item in profile.get("recommended_suite") or []:
        row = dict(item)
        row["focus"] = shot_focus.get(str(row.get("id") or ""), "")
        row["focus_zh"] = shot_focus_zh.get(str(row.get("id") or ""), "")
        if row.get("type") == "size_card" and not dimensions:
            row["selected"] = False
        else:
            row["selected"] = True
        suite_items.append(row)

    verified = [
        {"field": "来源类目", "value": "墙贴", "source": "妙手类目"},
        {"field": "材质", "value": "PVC", "source": "来源属性"},
        {"field": "产品形态", "value": "平面墙贴", "source": "来源属性"},
        {"field": "图案", "value": "热带植物、花卉与叶片图案", "source": "来源属性"},
        {"field": "片数", "value": attrs.get("片数") or "1", "source": "来源属性"},
        {"field": "有来源支持的描述", "value": "防水墙贴", "source": "来源属性"},
    ]
    if dimensions:
        verified.append(
            {
                "field": "排版尺寸",
                "value": f"{dimensions[0]:g} x {dimensions[1]:g} cm",
                "source": "来源属性；仅可在人审后作为后期英文标注",
            }
        )

    unresolved = [
        "精确配色、切边排版与最终贴附墙面，必须由人工查看来源图后确认。",
        "不可宣传可移除、无残胶、厚度或背胶性能：当前审核包没有可用证据支持这些说法。",
        "来源中存在品牌属性，但除非商品负责人明确允许，品牌不会进入生成图片或 Prompt。",
    ]
    if box_weight_g and source_weight_g:
        unresolved.append(
            f"重量冲突：采集箱字段为 {box_weight_g:g} g；来源属性为 {source_weight_g}（含 OPP 袋）。任何图片都不得出现重量信息。")

    source_item_id = ""
    source_url = ""
    source_list = detail.get("sourceList") or []
    if source_list and isinstance(source_list[0], dict):
        source_item_id = str(source_list[0].get("sourceItemId") or "")
        source_url = str(source_list[0].get("sourceItemUrl") or "")

    plan = {
        "analysis": analysis,
        "suite": {
            "summary": "Evidence-grounded wall decal image suite. Generate the white hero first; fund the remaining shots only after identity approval.",
            "items": suite_items,
        },
        "_meta": {
            "model": "deterministic_miaoshou_handoff_no_model_call",
            "image_url": images[0] if images else "",
            "title": english_title,
            "knowledge_version": load_knowledge().get("version"),
            "category_profile": profile.get("id"),
        },
    }
    return {
        "schema_version": "1.0.0",
        "created_at": _now(),
        "mode": "review_only_no_model_or_generation_call",
        "collect_box": {
            "detail_id": detail_id,
            "item_num": str(detail.get("itemNum") or ""),
            "source_title": str(detail.get("title") or ""),
            "source_item_id": source_item_id,
            "source_url": source_url,
            "image_urls": images,
            "primary_identity_image": images[0] if images else "",
            "image_count": len(images),
            "sku_count": len(detail.get("skuMap") or {}),
        },
        "fact_card": {
            "verified": verified,
            "inferred": [
                "来源标题和属性支持室内装饰墙面用途；具体贴附位置仍需人工视觉审核。",
            ],
            "unknown_or_forbidden": unresolved,
        },
        "review_gates": [
            "人工：确认第 1 张来源图是唯一的商品身份参考图。",
            "人工：确认英文商品名，并核对生成图与来源图的配色、图案和切边是否一致。",
            "人工：先审核白底主图；审核通过后，才可授权其余套图的付费生成。",
            "人工：确认 30 x 60 cm 尺寸后，才可在无文字尺寸底图上后期叠加英文尺寸标注。",
        ],
        "plan": plan,
    }


def _render_execution_results(out_dir: Path) -> str:
    """Collect all local, sanitized ToAPI audit files into the main review page."""
    paths = []
    legacy = out_dir / "generation_audit.json"
    if legacy.is_file():
        paths.append(legacy)
    paths.extend(sorted(out_dir.glob("generation_audit_*.json")))
    if not paths:
        return "<p>当前没有真实生成记录。</p>"

    names = {
        "wb1": "白底主图（历史记录）",
        "sc1": "客厅上墙场景图",
        "sc2": "卧室墙面场景图",
        "sc3": "浴室墙面场景图",
        "sp1": "图案与表面卖点图（首版，待人工审核）",
        "sp1_retry1": "图案与表面卖点图（重试版）",
        "dt1": "切边与印花细节图",
    }
    notes = {
        "wb1": "历史白底图保留用于追溯。墙贴类目当前不再自动生成白底图。",
        "sp1": "首版含有模型自行生成的英文、箭头和性能表达。保留为待人工审核素材：需核对英文准确性、表达是否真实且可用于店铺后，才能上架使用。",
        "sp1_retry1": "重试版已强制无文字、无箭头、无性能说法；仍需人工判断平面墙贴的视觉准确性。",
    }
    cards = []
    for path in paths:
        try:
            audit = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        artifact_id = "wb1" if path.name == "generation_audit.json" else path.stem.removeprefix("generation_audit_")
        shot_id = str(audit.get("shot_id") or artifact_id.split("_", 1)[0])
        image_path = out_dir / "generated" / f"{artifact_id}.png"
        image_block = (
            f"<img style='width:100%;background:#fff;border:1px solid #d6dde2' src='generated/{html.escape(image_path.name)}' alt='{html.escape(names.get(artifact_id, shot_id))}'>"
            if image_path.is_file() else "<p>没有通过本地核验的图片文件。</p>"
        )
        history = "".join(
            f"<li>{html.escape(str(row.get('at') or ''))}：{html.escape(str(row.get('status') or 'unknown'))}</li>"
            for row in (audit.get("status_history") or []) if isinstance(row, dict)
        ) or "<li>未记录到轮询状态。</li>"
        payload = html.escape(json.dumps(audit.get("payload") or {}, ensure_ascii=False, indent=2))
        created = html.escape(json.dumps(audit.get("create_response") or {}, ensure_ascii=False, indent=2))
        final = html.escape(json.dumps(audit.get("final_response") or {}, ensure_ascii=False, indent=2))
        technical = "已完成并通过本地图片文件核验" if audit.get("download_verified") else "未通过本地图片文件核验"
        cards.append(
            "<article style='background:#fff;border:1px solid #d6dde2;padding:16px;margin:16px 0'>"
            f"<h3>{html.escape(names.get(artifact_id, shot_id))}</h3>"
            f"<p><b>任务 ID：</b><code>{html.escape(str(audit.get('task_id') or '未创建'))}</code><br><b>技术状态：</b>{technical}<br><b>下载核验：</b>{html.escape(str(audit.get('download_note') or '无'))}</p>"
            f"{image_block}<p style='padding:10px 12px;background:#fff4dc;border-left:3px solid #9a6100'><b>审核备注：</b>{html.escape(notes.get(artifact_id, '等待人工判断商品身份、图案一致性和合规性。'))}</p>"
            f"<details><summary>轮询过程</summary><ul>{history}</ul></details>"
            f"<details><summary>脱敏后的真实请求 payload</summary><pre>{payload}</pre></details>"
            f"<details><summary>脱敏后的任务创建返回</summary><pre>{created}</pre></details>"
            f"<details><summary>脱敏后的最终 API 返回</summary><pre>{final}</pre></details></article>"
        )
    return "".join(cards)


def _render_report(package: dict[str, Any], prompts: dict[str, Any], out_dir: Path) -> str:
    box = package["collect_box"]
    facts = package["fact_card"]
    primary = html.escape(box.get("primary_identity_image") or "")
    img_html = f'<img class="hero" src="{primary}" alt="Source identity reference">' if primary else "<p>No source image available.</p>"

    def bullet_rows(rows: list[Any]) -> str:
        return "".join(f"<li>{html.escape(str(row))}</li>" for row in rows)

    verified_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(str(row.get("field") or "")),
            html.escape(str(row.get("value") or "")),
            html.escape(str(row.get("source") or "")),
        )
        for row in facts["verified"]
    )
    source_images = "".join(
        f'<a href="{html.escape(url)}" target="_blank" rel="noreferrer"><img src="{html.escape(url)}" alt="Source image"></a>'
        for url in box["image_urls"]
    )
    item_by_id = {
        str(item.get("id") or ""): item
        for item in ((package.get("plan") or {}).get("suite") or {}).get("items") or []
        if isinstance(item, dict)
    }
    shot_titles_zh = {
        "wb1": "白底主图",
        "sc1": "客厅上墙场景图",
        "sc2": "卧室墙面场景图",
        "sc3": "浴室墙面场景图",
        "sp1": "图案与表面卖点图",
        "dt1": "切边与印花细节图",
        "sz1": "尺寸底图",
    }
    type_instruction_zh = {
        "white_bg": "生成电商白底主图：纯白无缝背景，商品完整居中，柔和均匀棚拍光线，极轻阴影，无道具。",
        "scene": "生成真实生活化场景图：场景必须符合墙贴的实际用途，商品仍是画面主体。",
        "selling_point": "生成电商卖点图：只突出经过证实的一个视觉特征，画面干净，不做杂乱拼贴。",
        "macro_detail": "生成同一商品的高清局部细节图：只展示已有证据的材质、印花或切边。",
        "size_card": "生成白底技术底图：商品完整可见，四周留白，供后期添加经确认的英文尺寸标注。",
    }
    def chinese_prompt(shot: dict[str, Any]) -> str:
        item = item_by_id.get(str(shot.get("id") or ""), {})
        focus = str(item.get("focus_zh") or "")
        return "\n".join([
            type_instruction_zh.get(str(shot.get("type") or ""), "生成对应的电商商品图片。"),
            "商品主体：一张平面的热带植物 PVC 墙贴。",
            "身份锁定：必须以第 1 张来源图作为唯一商品身份参考，保留完全一致的植物排版、配色、平面形态、图案和比例。",
            "允许变化：仅允许调整镜头、光线、背景和本分镜需要的场景道具；不得重新设计商品。",
            "本分镜构图：" + focus,
            "文字规则：优先生成无文字底图；如后期必须添加可见文字，只能添加已审核的英文，不得出现中文、乱码、价格或模型生成的尺寸数字。",
            "事实规则：不得捏造品牌、认证、性能、组件、材质或规格；不得把墙贴变成装饰画、立体摆件、门挂或其他商品。",
            "画幅比例：" + str(shot.get("aspect_ratio") or "1:1") + "。",
        ])
    shots = "".join(
        "<article><h3>{} / {}</h3><p><b>画幅比例：</b>{}</p><h4>中文执行说明</h4><pre>{}</pre><details><summary>查看实际请求时使用的英文 Prompt（仅审计用）</summary><pre>{}</pre></details></article>".format(
            html.escape(shot_titles_zh.get(str(shot.get("id") or ""), "待审核分镜")),
            html.escape(str(shot.get("id") or "")),
            html.escape(str(shot.get("aspect_ratio") or "")),
            html.escape(chinese_prompt(shot)),
            html.escape(str(shot.get("prompt") or "")),
        )
        for shot in prompts.get("shots") or []
    )
    execution_results = _render_execution_results(out_dir)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Miaoshou Image Review - {html.escape(str(box['detail_id']))}</title>
<style>
:root{{--ink:#16212a;--muted:#52616b;--line:#d6dde2;--paper:#fff;--canvas:#f3f6f7;--blue:#086998;--amber:#9a6100;--green:#176b4d}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--canvas);color:var(--ink);font-family:"Microsoft YaHei","Segoe UI",Arial,sans-serif;line-height:1.65}} main{{max-width:1180px;margin:auto;padding:32px 24px 70px}} h1{{line-height:1.2;margin:0 0 8px}} h2{{margin-top:38px}} .muted{{color:var(--muted)}} .badge{{display:inline-block;padding:3px 8px;background:#e8f4ed;color:var(--green);font-weight:700;font-size:12px}} .warning{{border-left:4px solid var(--amber);padding:12px 16px;background:#fff4dc}} .grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}} .card,article{{background:var(--paper);border:1px solid var(--line);padding:16px}} .hero{{max-width:420px;width:100%;background:white;border:1px solid var(--line)}} table{{width:100%;border-collapse:collapse;background:white}} td,th{{padding:10px;border:1px solid var(--line);text-align:left;vertical-align:top}} th{{background:#edf2f5}} .sources{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}} .sources img{{width:100%;aspect-ratio:1;object-fit:contain;background:white;border:1px solid var(--line)}} pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f7;padding:12px;font:12px/1.5 Consolas,monospace}} details{{margin-top:10px}} @media(max-width:700px){{main{{padding:22px 14px 45px}}.grid{{grid-template-columns:1fr}}.sources{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
</style></head><body><main>
<p class="badge">已完成：采集箱审核包 + 真实生成审计汇总；每张图片的最终状态见本页结果区</p>
<h1>妙手采集箱到生图审核包</h1>
<p class="muted">采集箱 ID：{html.escape(str(box['detail_id']))} | SKU：{html.escape(box['item_num'])} | 类目规则：wall_decal | 创建时间：{html.escape(package['created_at'])}</p>
<section class="grid"><div class="card"><h2>唯一商品身份参考</h2>{img_html}<p>第一张来源图被设为唯一视觉身份锚点。其余图只作事实/细节证据，未自动混用为身份参考。</p></div>
<div class="card"><h2>交接状态</h2><p><b>输入：</b>Orbit 保存后的妙手采集箱。</p><p><b>输出：</b>事实卡、分镜、英文 Prompt、审核门，以及已执行任务的审计记录。</p><p><b>不包含：</b>妙手写回和上架动作。</p><p><b>当前规则：</b>墙贴的白底图和尺寸图由人工输入；仅场景、卖点和细节图可进入生成队列。</p></div></section>
<h2>事实卡</h2><table><thead><tr><th>已验证字段</th><th>值</th><th>来源</th></tr></thead><tbody>{verified_rows}</tbody></table>
<section class="grid"><div class="card"><h2>视觉推断（不可当卖点）</h2><ul>{bullet_rows(facts['inferred'])}</ul></div><div class="warning"><h2>未知/禁止写入图片</h2><ul>{bullet_rows(facts['unknown_or_forbidden'])}</ul></div></section>
<h2>人工审核门</h2><ol>{bullet_rows(package['review_gates'])}</ol>
<h2>来源图（{box['image_count']} 张）</h2><div class="sources">{source_images}</div>
<h2>待审批的分镜与生成说明</h2><p class="muted">本页优先展示中文执行说明。折叠区域保留实际使用的英文 Prompt，供审计或排查时核对；最终图片的可见文字仍遵循“只允许后期添加已审核英文”的规则。</p>{shots}
<h2>真实生成结果与完整过程</h2><p class="muted">汇总所有已创建的 ToAPI 任务，包括历史白底图、首版失败卖点图和重试版。每张保留任务 ID、轮询过程、脱敏请求、脱敏最终返回和本地下载核验；技术完成不等于人工审核通过。</p>{execution_results}
</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a no-generation image review package from Miaoshou.")
    parser.add_argument("--collect-box-id", type=int, required=True)
    parser.add_argument("--out", default="", help="Local output directory")
    args = parser.parse_args()

    response = post_open(
        "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail",
        {"commonCollectBoxDetailId": args.collect_box_id},
    )
    detail = (response.get("data") or {}).get("editCommonCollectBoxDetail") or {}
    if not detail:
        raise RuntimeError("Miaoshou response has no collect-box detail")

    package = build_review_package(args.collect_box_id, detail)
    prompts = build_shot_prompts(package["plan"])
    out_dir = Path(args.out) if args.out else ROOT / "outputs" / "image_suite_from_miaoshou" / str(args.collect_box_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "review_package.json").write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    save_shot_prompts(prompts, out_dir)
    (out_dir / "review_report.html").write_text(_render_report(package, prompts, out_dir), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "mode": package["mode"],
        "collect_box_id": args.collect_box_id,
        "profile": package["plan"]["_meta"]["category_profile"],
        "output_dir": str(out_dir),
        "files": ["review_package.json", "shot_prompts.json", "shot_prompts.md", "review_report.html"],
        "paid_generation_called": False,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
