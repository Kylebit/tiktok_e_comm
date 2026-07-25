"""Build local, review-only image packages from Miaoshou collect boxes.

This module deliberately stops before any model or paid image-generation call.
It creates the evidence, fact card, and shot plan that a human must approve
before a separate generation step can be authorised.
"""

from __future__ import annotations

import html
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from modules.miaoshou.client import post_open
from modules.sourcing.image_generation_knowledge import load_knowledge, resolve_profile
from modules.sourcing.image_shot_prompts import build_shot_prompts, save_shot_prompts


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _attrs(detail: dict[str, Any]) -> dict[str, str]:
    return {
        str(row.get("name") or "").strip(): str(row.get("value") or "").strip()
        for row in (detail.get("sourceAttrs") or [])
        if isinstance(row, dict) and str(row.get("name") or "").strip()
    }


def _images(detail: dict[str, Any]) -> list[str]:
    return [
        str(url).strip()
        for url in (detail.get("imgUrls") or [])
        if str(url).strip().startswith(("https://", "http://"))
    ]


def _first(attrs: dict[str, str], *names: str) -> str:
    for name in names:
        value = attrs.get(name)
        if value:
            return value
    return ""


def _category_input(title: str, attrs: dict[str, str]) -> str:
    return " ".join(
        [
            title,
            _first(attrs, "类别", "类目", "墙贴类型", "款式"),
            _first(attrs, "图案", "风格", "功能"),
        ]
    )


def _profile_specific_plan(profile_id: str) -> tuple[str, str, str, str, dict[str, str], dict[str, str]]:
    """Return evidence-safe defaults; source images remain the identity lock."""
    if profile_id == "product_sticker":
        return (
            "Flat decorative helmet sticker",
            "decorative helmet sticker",
            "flat printed sticker artwork",
            "Use the first supplier image as the sole identity reference. Preserve the exact graphic, colors, cut edge, flat form, and proportions. Do not recreate or replace any visible artwork.",
            {
                "sc1": "Show the exact sticker applied flat to a clean motorcycle helmet in a realistic riding setting. Keep the complete artwork recognisable and do not add safety, durability, or waterproof claims.",
                "sc2": "Show the exact sticker applied flat to an electric-scooter helmet in a believable everyday urban setting. Keep the product unobstructed and the artwork unchanged.",
                "sc3": "Show the exact sticker on its intended helmet surface in a simple everyday riding context. Preserve believable scale and avoid unrelated products or extra stickers.",
                "sp1": "Highlight only the source-supported printed graphic and flat sticker form. Do not show adhesive backing, peeling, measurements, logos, or performance claims.",
            },
            {
                "sc1": "在真实摩托车骑行场景中，展示同一张贴纸平整贴附在干净头盔表面；完整图案清晰可辨，不出现安全、耐用或防水承诺。",
                "sc2": "在可信的日常城市电动车场景中，展示同一张贴纸平整贴附在头盔表面；商品不被遮挡，图案保持不变。",
                "sc3": "在简洁的日常骑行语境中，展示贴纸位于其预期头盔表面；比例可信，不添加无关商品或额外贴纸。",
                "sp1": "仅突出来源已支持的印刷图案和贴纸平面形态；不展示背胶、揭起动作、尺寸、logo 或性能承诺。",
            },
        )
    return (
        "Source-supported product",
        "product",
        "preserve the source-supported product form",
        "Use the first supplier image as the sole identity reference. Preserve the exact product shape, color, material appearance, print, components, and proportions. Do not redesign the product.",
        {
            "wb1": "Show the exact product clearly on a seamless white background with no props or text.",
            "sc1": "Show the exact product in one believable, source-supported use context without inventing a new use or feature.",
            "sp1": "Highlight one visible, source-supported visual characteristic only. Do not add claims, labels, or callouts.",
            "dt1": "Show a close crop of the same product only when the visible material or craft is supported by the source image.",
        },
        {
            "wb1": "在纯白无缝背景中清晰展示同一商品，不添加道具或文字。",
            "sc1": "在一个可信且由来源支持的使用场景中展示同一商品，不虚构用途或特性。",
            "sp1": "仅突出一个来源支持的可见视觉特征；不添加宣传承诺、标签或标注。",
            "dt1": "仅在来源图支持可见材质或工艺时，展示同一商品的局部近景。",
        },
    )


def build_review_package(detail_id: int, detail: dict[str, Any]) -> dict[str, Any]:
    """Produce an evidence-grounded package without model or generation calls."""
    attrs = _attrs(detail)
    images = _images(detail)
    source_title = str(detail.get("title") or "").strip()
    category_input = _category_input(source_title, attrs)
    profile = resolve_profile(title=category_input, category=category_input)
    subject, category, structure, style_lock, focus_en, focus_zh = _profile_specific_plan(str(profile.get("id") or ""))

    source_list = detail.get("sourceList") or []
    source_row = source_list[0] if source_list and isinstance(source_list[0], dict) else {}
    material = _first(attrs, "材质")
    form = _first(attrs, "款式", "墙贴类型")
    pattern = _first(attrs, "图案")
    dimensions = _first(attrs, "规格", "尺寸")
    sku_count = len(detail.get("skuMap") or {})
    suite_items: list[dict[str, Any]] = []
    for item in profile.get("recommended_suite") or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        shot_id = str(row.get("id") or "")
        row["selected"] = True
        row["focus"] = focus_en.get(shot_id, "Preserve the approved source identity and use only source-supported visual facts.")
        row["focus_zh"] = focus_zh.get(shot_id, "保持已批准的来源商品身份，仅使用来源支持的视觉事实。")
        suite_items.append(row)

    verified = [
        {"field": "来源标题", "value": source_title or "未提供", "source": "妙手采集箱"},
    ]
    if form:
        verified.append({"field": "来源属性：款式/类型", "value": form, "source": "来源属性；不等同于人工确认的商品形态"})
    for field, value in (("材质", material), ("图案", pattern), ("来源规格", dimensions)):
        if value:
            verified.append({"field": field, "value": value, "source": "来源属性"})
    inferred = [
        "第 1 张来源图仅作为待人工确认的商品身份参考图；在确认前，不能将其视为已批准的视觉身份。",
        "场景用途和商品可贴附表面需要人工对照来源图审核；分镜只提供待审核的拍摄方向。",
    ]
    if "头盔" in source_title and "墙贴" in form:
        inferred.append("来源标题指向头盔贴，但来源属性含“墙贴”分类词。系统已按更具体的头盔贴规则计划；请人工确认第 1 张来源图与实际适用表面。")
    forbidden = [
        "不得在生成图或卖点中使用供应商品牌、logo、认证、价格、尺寸数字或未经人工确认的性能承诺。",
        "不得展示背胶、揭起动作、可移除性、防水性、耐用性或安全性能，除非后续有清晰证据且人工明确批准。",
        "贴纸类目不生成白底图、尺寸图或细节图；这些图由人工输入和审核。",
    ]
    plan = {
        "analysis": {
            "subject": subject,
            "category": category,
            "theme": pattern or "source-supported decorative graphic",
            "structure": structure,
            "style_lock": style_lock,
            "materials": [material] if material else [],
            "colors": [],
            "craft_details": [form] if form else [],
            "brand_dna": [],
        },
        "suite": {
            "summary": "待人工审核的生图分镜。仅在事实卡和分镜范围均获通过后，才可以进入单独授权的付费生成步骤。",
            "items": suite_items,
        },
        "_meta": {
            "model": "deterministic_miaoshou_handoff_no_model_call",
            "image_url": images[0] if images else "",
            "title": source_title,
            "knowledge_version": load_knowledge().get("version"),
            "category_profile": profile.get("id"),
        },
    }
    return {
        "schema_version": "1.1.0",
        "created_at": _now(),
        "mode": "review_only_no_model_or_generation_call",
        "collect_box": {
            "detail_id": detail_id,
            "item_num": str(detail.get("itemNum") or ""),
            "source_title": source_title,
            "source_item_id": str(source_row.get("sourceItemId") or ""),
            "source_url": str(source_row.get("sourceItemUrl") or ""),
            "image_urls": images,
            "primary_identity_image": images[0] if images else "",
            "image_count": len(images),
            "sku_count": sku_count,
        },
        "fact_card": {"verified": verified, "inferred": inferred, "unknown_or_forbidden": forbidden},
        "review_gates": [
            "人工：确认第 1 张来源图是否为本商品唯一的视觉身份参考图。",
            "人工：确认贴纸图案、切边、颜色与计划场景中的商品一致。",
            "人工：确认分镜不包含未经证实的性能、尺寸或品牌表述。",
            "人工：事实卡和分镜均通过后，才可另外授权任何付费生图请求。",
        ],
        "plan": plan,
    }


def _render_report(package: dict[str, Any], prompts: dict[str, Any], out_dir: Path | None = None) -> str:
    box = package["collect_box"]
    facts = package["fact_card"]
    source_images = "".join(
        f'<a href="{html.escape(url)}" target="_blank" rel="noreferrer"><img src="{html.escape(url)}" alt="来源图"></a>'
        for url in box.get("image_urls") or []
    ) or "<p>未读取到可用来源图。</p>"
    verified = "".join(
        f"<tr><td>{html.escape(str(row.get('field') or ''))}</td><td>{html.escape(str(row.get('value') or ''))}</td><td>{html.escape(str(row.get('source') or ''))}</td></tr>"
        for row in facts.get("verified") or []
        if isinstance(row, dict)
    )
    items = {str(item.get("id") or ""): item for item in (package.get("plan", {}).get("suite", {}).get("items", []) or []) if isinstance(item, dict)}
    shots = "".join(
        "<article><h3>{}</h3><p>{}</p><details><summary>英文执行 Prompt（仅供审计）</summary><pre>{}</pre></details></article>".format(
            html.escape(str(items.get(str(shot.get("id") or ""), {}).get("title") or shot.get("title") or "")),
            html.escape(str(items.get(str(shot.get("id") or ""), {}).get("focus_zh") or "")),
            html.escape(str(shot.get("prompt") or "")),
        )
        for shot in prompts.get("shots") or []
        if isinstance(shot, dict)
    ) or "<p>没有可生成的分镜。</p>"
    def bullets(values: list[Any]) -> str:
        return "".join(f"<li>{html.escape(str(value))}</li>" for value in values)
    proposal = package.get("model_proposal") if isinstance(package.get("model_proposal"), dict) else {}
    proposal_html = "<section><h2>大模型候选分镜</h2><p>尚未调用大模型，本次分镜来自本地类目规则。</p></section>"
    if proposal:
        policy = proposal.get("policy") if isinstance(proposal.get("policy"), dict) else {}
        usage = proposal.get("usage") if isinstance(proposal.get("usage"), dict) else {}
        rejected = "、".join(str(x) for x in policy.get("rejected_item_ids") or []) or "无"
        candidate_rows = "".join(
            "<li><b>{}</b><br>中文翻译：{}<br><small>英文原文：{}</small></li>".format(
                html.escape(str(item.get("title_zh") or item.get("title") or item.get("id") or "")),
                html.escape(str(item.get("focus_zh") or "待补充中文翻译")),
                html.escape(str(item.get("focus") or "")),
            )
            for item in proposal.get("candidate_items") or []
            if isinstance(item, dict)
        ) or "<li>历史记录未保存结构化候选，请查看英文原文审计。</li>"
        proposal_html = (
            "<section><h2>大模型候选分镜与规则校验</h2>"
            "<p class=\"notice\">候选分镜由 ToAPI 视觉模型提出；系统只保留类目允许的镜头，且仍需人工审核。"
            "本阶段未生成任何商品图片。</p>"
            f"<p>模型：<code>{html.escape(str(proposal.get('model') or ''))}</code>；参考图：{html.escape(str(proposal.get('reference_count') or 0))} 张；"
            f"Token：{html.escape(str(usage.get('total_tokens') or 0))}；规则剔除镜头：{html.escape(rejected)}</p>"
            "<ol><li>来源审核包已完成。</li><li>ToAPI 视觉候选已完成。</li><li>本地类目规则已校验。</li><li>当前等待人工重新审核事实卡与分镜。</li></ol>"
            "<h3>ToAPI 原始候选分镜</h3><ul>" + candidate_rows + "</ul>"
            "<details><summary>英文候选原文与用量（审计）</summary><pre>"
            + html.escape(str(proposal.get("raw_content") or ""))
            + "</pre></details></section>"
        )
    execution_html = "<section><h2>真实生成结果</h2><p>尚未创建 ToAPI 图片任务。</p></section>"
    if out_dir:
        cards = []
        for audit_path in sorted(out_dir.glob("generation_audit_*.json")):
            try:
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            artifact_id = audit_path.stem.removeprefix("generation_audit_")
            image = out_dir / "generated" / f"{artifact_id}.png"
            image_html = f'<img src="generated/{html.escape(image.name)}" alt="已生成首图">' if image.is_file() else "<p>未通过本地图片核验。</p>"
            cards.append(
                f"<article><h3>{html.escape(str(audit.get('shot_id') or artifact_id))}</h3>"
                f"<p>任务 ID：<code>{html.escape(str(audit.get('task_id') or '未创建'))}</code>；下载核验：{html.escape(str(audit.get('download_note') or '未完成'))}</p>"
                + image_html + "<p class=\"notice\">技术完成不等于人工通过。请核对商品身份、图案、可见英文和任何宣称。</p></article>"
            )
        if cards:
            execution_html = "<section><h2>真实生成结果与人工复核</h2>" + "".join(cards) + "</section>"
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>生图审核包</title>
<style>body{{font:15px/1.65 Arial,sans-serif;margin:32px;color:#172033;background:#f7f9fc}}main{{max-width:1080px;margin:auto}}section,article{{background:#fff;border:1px solid #dbe3ef;border-radius:6px;padding:20px;margin:16px 0}}.images{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}img{{width:100%;border:1px solid #dbe3ef}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #dbe3ef;padding:8px;text-align:left;vertical-align:top}}.notice{{border-left:4px solid #bf6c00;background:#fff6df;padding:12px}}pre{{white-space:pre-wrap;word-break:break-word;background:#f4f7fb;padding:12px}}</style>
<main><h1>生图审核包（未调用模型或付费生图）</h1><p>采集箱：{html.escape(str(box.get('detail_id') or ''))}；创建时间：{html.escape(str(package.get('created_at') or ''))}</p>
<section><h2>执行边界</h2><p class="notice">本页只读取妙手采集箱并生成本地事实卡、分镜和 Prompt。未调用大模型、ToAPI 或任何付费生成接口；未下载或写回妙手图片。</p></section>
<section><h2>待审核商品身份参考图</h2><p>第 1 张为待人工确认的唯一视觉身份候选；请确认它确实对应当前商品后，再勾选 Treasury 中的事实卡审核。</p><div class="images">{source_images}</div></section>
<section><h2>事实卡</h2><h3>已验证</h3><table><tr><th>字段</th><th>值</th><th>证据</th></tr>{verified}</table><h3>视觉推断，待审</h3><ul>{bullets(facts.get('inferred') or [])}</ul><h3>未知或禁止</h3><ul>{bullets(facts.get('unknown_or_forbidden') or [])}</ul></section>
<section><h2>分镜计划</h2>{shots}</section>{proposal_html}{execution_html}<section><h2>人工审核门</h2><ul>{bullets(package.get('review_gates') or [])}</ul></section></main></html>"""


def create_package_from_miaoshou(collect_box_id: int, output_dir: Path) -> dict[str, Any]:
    """Fetch one collect box and write local review artifacts, with no paid calls."""
    response = post_open(
        "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail",
        {"commonCollectBoxDetailId": int(collect_box_id)},
    )
    detail = (response.get("data") or {}).get("editCommonCollectBoxDetail") or {}
    if not detail:
        raise RuntimeError("Miaoshou response has no collect-box detail")
    package = build_review_package(int(collect_box_id), detail)
    prompts = build_shot_prompts(package["plan"])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "review_package.json").write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    save_shot_prompts(prompts, output_dir)
    (output_dir / "review_report.html").write_text(_render_report(package, prompts, output_dir), encoding="utf-8")
    return {
        "collect_box_id": int(collect_box_id),
        "output_dir": str(output_dir),
        "profile": str(package["plan"]["_meta"].get("category_profile") or ""),
        "image_count": int(package["collect_box"].get("image_count") or 0),
        "shot_count": int(prompts.get("count") or 0),
        "paid_generation_called": False,
        "model_called": False,
    }


def create_model_suite_proposal(
    output_dir: Path,
    reference_urls: list[str],
    *,
    suite_request: dict[str, Any],
    planning_signature: str,
    storyboard_feedback: dict[str, str] | None = None,
    revision_target_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Validate local constraints, run AI planning, then enforce them again."""
    package_path = output_dir / "review_package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    collect_box = package.get("collect_box") if isinstance(package.get("collect_box"), dict) else {}
    fact_card = package.get("fact_card") if isinstance(package.get("fact_card"), dict) else {}
    allowed = {str(url) for url in collect_box.get("image_urls") or [] if str(url).startswith("https://")}
    refs = []
    for url in reference_urls:
        clean = str(url).strip()
        if clean in allowed and clean not in refs:
            refs.append(clean)
    if not refs:
        raise ValueError("at least one approved source reference image is required")
    evidence = "\n".join(
        "- VERIFIED: " + str(row.get("field") or "") + " = " + str(row.get("value") or "")
        for row in fact_card.get("verified") or []
        if isinstance(row, dict)
    )
    evidence += "\n" + "\n".join("- FORBIDDEN: " + str(item) for item in fact_card.get("unknown_or_forbidden") or [])
    from modules.sourcing.image_suite_plan import (
        analyze_and_plan_suite,
        enforce_category_policy,
        normalize_suite_request,
    )

    normalized_request = normalize_suite_request(
        suite_request.get("type_counts") if isinstance(suite_request.get("type_counts"), dict) else {},
        title=str(collect_box.get("source_title") or ""),
        size_card=suite_request.get("size_card") if isinstance(suite_request.get("size_card"), dict) else {},
    )
    previous_plan = package.get("plan") if isinstance(package.get("plan"), dict) else {}
    previous_items = [
        {
            "id": str(item.get("id") or ""),
            "type": str(item.get("type") or ""),
            "title": str(item.get("title") or ""),
            "focus": str(item.get("focus") or ""),
            "operator_title_zh": str(item.get("operator_title_zh") or ""),
            "operator_focus_zh": str(item.get("operator_focus_zh") or item.get("focus_zh") or ""),
        }
        for item in ((previous_plan.get("suite") or {}).get("items") or [])
        if isinstance(item, dict) and item.get("selected")
    ]
    clean_feedback = {
        str(shot_id): str(note).strip()[:1200]
        for shot_id, note in (storyboard_feedback or {}).items()
        if str(shot_id).strip() and str(note).strip()
    }
    target_ids = list(
        dict.fromkeys(
            str(shot_id).strip()
            for shot_id in (revision_target_ids or clean_feedback.keys())
            if str(shot_id).strip()
        )
    )
    unknown_targets = [shot_id for shot_id in target_ids if shot_id not in {row["id"] for row in previous_items}]
    if unknown_targets:
        raise ValueError(
            "revision targets are not present in the current storyboard: "
            + ", ".join(unknown_targets)
        )
    revision_context = ""
    if clean_feedback:
        revision_context = json.dumps(
            {
                "previous_storyboard": previous_items,
                "operator_revision_feedback_zh": clean_feedback,
                "revision_target_ids": target_ids,
                "instruction": (
                    "Revise only the target storyboard items. Return the complete suite for schema "
                    "validation, but do not intentionally alter non-target items."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    candidate = analyze_and_plan_suite(
        refs[0],
        reference_image_urls=refs[1:],
        title=str(collect_box.get("source_title") or ""),
        evidence_context=evidence,
        suite_request=normalized_request,
        storyboard_revision_context=revision_context,
    )
    locked = enforce_category_policy(
        candidate,
        title=str(collect_box.get("source_title") or ""),
        category=str((candidate.get("analysis") or {}).get("category") or ""),
        suite_request=normalized_request,
    )
    old_items = {
        str(item.get("id") or ""): item
        for item in ((package.get("plan") or {}).get("suite") or {}).get("items") or []
        if isinstance(item, dict)
    }
    if target_ids:
        revised_items = {
            str(item.get("id") or ""): item
            for item in ((locked.get("suite") or {}).get("items") or [])
            if isinstance(item, dict)
        }
        missing_targets = [shot_id for shot_id in target_ids if shot_id not in revised_items]
        if missing_targets:
            raise ValueError(
                "AI revision did not return the requested storyboard items: "
                + ", ".join(missing_targets)
            )
        merged_items = []
        for old_item in ((previous_plan.get("suite") or {}).get("items") or []):
            if not isinstance(old_item, dict):
                continue
            shot_id = str(old_item.get("id") or "")
            merged_items.append(
                deepcopy(revised_items[shot_id])
                if shot_id in target_ids
                else deepcopy(old_item)
            )
        locked["analysis"] = deepcopy(previous_plan.get("analysis") or {})
        locked.setdefault("suite", {})["summary"] = str(
            ((previous_plan.get("suite") or {}).get("summary") or "")
        )
        locked["suite"]["items"] = merged_items
    for item in locked["suite"]["items"]:
        old = old_items.get(str(item.get("id") or ""), {})
        item["focus_zh"] = str(
            item.get("operator_focus_zh")
            or item.get("focus_zh")
            or old.get("focus_zh")
            or "待人工审核 AI 分镜中文说明。"
        )
    locked["_meta"] = dict(candidate.get("_meta") or {})
    locked["_meta"]["knowledge_version"] = load_knowledge().get("version")
    locked["_meta"]["category_profile"] = locked["_policy"].get("category_profile")
    prompts = build_shot_prompts(locked)
    previous_proposal = package.get("model_proposal") if isinstance(package.get("model_proposal"), dict) else {}
    if previous_proposal:
        history = package.setdefault("model_proposal_history", [])
        if isinstance(history, list):
            archived = dict(previous_proposal)
            archived["superseded_at"] = _now()
            archived["revision_feedback"] = clean_feedback
            history.append(archived)
            del history[:-20]
    package["plan"] = locked
    package["model_proposal"] = {
        "created_at": _now(),
        "mode": "toapi_vision_candidate_then_local_policy_gate",
        "planning_source": "ai",
        "planning_signature": str(planning_signature or ""),
        "revision_feedback": clean_feedback,
        "revision_target_ids": target_ids,
        "unchanged_item_ids": [
            row["id"] for row in previous_items if row["id"] not in set(target_ids)
        ],
        "suite_request": normalized_request,
        "model": str((candidate.get("_meta") or {}).get("model") or ""),
        "reference_count": len(refs),
        "reference_urls": refs,
        "usage": (candidate.get("_meta") or {}).get("usage"),
        "candidate_items": [
            {
                "id": str(item.get("id") or ""),
                "type": str(item.get("type") or ""),
                "title": str(item.get("title") or ""),
                "focus": str(item.get("focus") or ""),
                "title_zh": str(item.get("operator_title_zh") or ""),
                "focus_zh": str(item.get("operator_focus_zh") or ""),
                "aspect_ratio": str(item.get("aspect_ratio") or ""),
                "selected": bool(item.get("selected", True)),
            }
            for item in ((candidate.get("suite") or {}).get("items") or [])
            if isinstance(item, dict)
        ],
        "accepted_items": [
            str(item.get("id") or "")
            for item in ((locked.get("suite") or {}).get("items") or [])
            if isinstance(item, dict) and item.get("selected")
        ],
        "policy": locked.get("_policy"),
        "raw_content": str((candidate.get("_meta") or {}).get("raw_content") or ""),
    }
    package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    save_shot_prompts(prompts, output_dir)
    (output_dir / "planning_audit.json").write_text(json.dumps(package["model_proposal"], ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "review_report.html").write_text(_render_report(package, prompts, output_dir), encoding="utf-8")
    return {
        "model": package["model_proposal"]["model"],
        "reference_count": len(refs),
        "usage": package["model_proposal"]["usage"],
        "policy": package["model_proposal"]["policy"],
        "candidate_items": package["model_proposal"]["candidate_items"],
        "accepted_items": package["model_proposal"]["accepted_items"],
        "paid_generation_called": False,
        "vision_model_called": True,
        "planning_signature": str(planning_signature or ""),
        "revision_feedback_applied": clean_feedback,
        "revision_target_ids": target_ids,
        "unchanged_item_ids": package["model_proposal"]["unchanged_item_ids"],
    }
