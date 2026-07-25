"""Build per-shot image-generation prompts from a suite_plan.json.

Takes selected suite items + analysis (style_lock / brand_dna) and expands each
into a concrete ToAPIs-ready prompt. Does NOT call image generation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from modules.sourcing.image_generation_knowledge import prompt_rules, resolve_profile

TYPE_DIRECTIVES = {
    "white_bg": (
        "Create a clean e-commerce white-background hero photo. "
        "Pure seamless white backdrop, product fully visible and centered, "
        "even soft studio lighting, minimal shadow, no props, no text overlays."
    ),
    "scene": (
        "Create a realistic lifestyle scene photo showing the product in actual use. "
        "Natural environment matching the shot intent, cinematic soft lighting, "
        "believable interior/exterior context, product must remain the visual hero."
    ),
    "selling_point": (
        "Create an e-commerce selling-point marketing photo that highlights one feature. "
        "Commercial product photography, clear focal emphasis on the stated benefit, "
        "no cluttered collage unless the shot intent explicitly needs a simple inset action."
    ),
    "macro_detail": (
        "Create a high-resolution macro e-commerce detail photograph of the exact product. "
        "Show only source-supported material, texture, craft, or component details."
    ),
    "size_card": (
        "Create a clean white-background technical base image with the full product visible and "
        "generous empty margin for a later deterministic English measurement overlay."
    ),
}


def english_dimension_label(value: str) -> str:
    """Format verified operator dimensions for deterministic English overlay."""
    labels = {"长": "L", "宽": "W", "高": "H", "厚": "D"}
    pairs = re.findall(
        r"([长宽高厚LWH])\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(cm|mm|m|in)?",
        str(value or ""),
        re.I,
    )
    if pairs:
        return "  |  ".join(
            f"{labels.get(label.upper(), labels.get(label, label.upper()))} "
            f"{number} {(unit or 'cm').lower()}"
            for label, number, unit in pairs
        )
    if re.search(r"[\u4e00-\u9fff]", str(value or "")):
        raise ValueError("size dimensions must contain recognizable length/width/height values")
    return re.sub(r"\s+", " ", str(value or "").strip())


def load_suite_plan(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("suite_plan must be a JSON object")
    return data


def selected_items(plan: dict[str, Any]) -> list[dict[str, Any]]:
    suite = plan.get("suite") if isinstance(plan.get("suite"), dict) else {}
    items = suite.get("items") if isinstance(suite.get("items"), list) else []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("selected", True)):
            continue
        itype = str(item.get("type") or "").strip()
        if itype not in TYPE_DIRECTIVES:
            continue
        out.append(item)
    return out


def _join_list(values: Any, sep: str = ", ") -> str:
    if not isinstance(values, list):
        return ""
    parts = [str(v).strip() for v in values if str(v).strip()]
    return sep.join(parts)


def _ascii_safe(text: str) -> str:
    """ToAPIs image backends have previously rejected some non-ASCII punctuation."""
    repl = {
        "\u2014": "-",
        "\u2013": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u2022": "*",
        "\u00a0": " ",
    }
    s = text
    for k, v in repl.items():
        s = s.replace(k, v)
    # Keep letters of all languages; only drop control chars
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
    return s


def build_shot_prompt(
    item: dict[str, Any],
    analysis: dict[str, Any],
    *,
    title: str = "",
    reference_image_url: str = "",
) -> dict[str, Any]:
    itype = str(item.get("type") or "").strip()
    directive = TYPE_DIRECTIVES[itype]
    subject = str(analysis.get("subject") or "").strip()
    category = str(analysis.get("category") or "").strip()
    theme = str(analysis.get("theme") or "").strip()
    structure = str(analysis.get("structure") or "").strip()
    style_lock = str(analysis.get("style_lock") or "").strip()
    materials = _join_list(analysis.get("materials"))
    colors = _join_list(analysis.get("colors"))
    craft = _join_list(analysis.get("craft_details"), sep="; ")
    dna = _join_list(analysis.get("brand_dna"))
    shot_title = str(item.get("title") or "").strip()
    focus = str(item.get("focus") or "").strip()
    # Miaoshou product images use a square canvas. Treat category/model ratios
    # as advisory composition hints only; the rendered asset is always 1:1.
    ratio = "1:1"
    profile = resolve_profile(title=title, category=category)
    category_rules = prompt_rules(profile=profile)
    human_dimensions = str(item.get("human_dimensions") or "").strip()
    human_size_override = itype == "size_card" and bool(item.get("human_override")) and bool(item.get("human_dimensions_confirmed")) and bool(human_dimensions)
    human_dimensions_prompt = ""
    if human_size_override:
        try:
            human_dimensions_prompt = english_dimension_label(human_dimensions)
        except ValueError:
            human_dimensions_prompt = "operator-verified dimensions"
    no_text_requirement = (
        "ABSOLUTE NO-TEXT REQUIREMENT: Return a single image with no letters, words, typography, "
        "labels, callouts, badges, split panels, captions, prices, numeric measurements, or claim text. "
        "Simple black dimension guide lines and arrowheads explicitly requested by the approved shot "
        "composition are allowed, but do not add any numbers. Exact English dimensions are applied "
        "deterministically after generation."
        if itype == "size_card"
        else
        "ABSOLUTE NO-TEXT REQUIREMENT: Return a single image with no letters, words, typography, "
        "labels, arrows, callouts, badges, split panels, captions, prices, measurements, or claim text. "
        "Leave all copy for deterministic post-processing."
    )
    if human_size_override:
        category_rules = [
            re.sub(
                r"Do not generate white-background hero images, size-card images, or macro-detail images for (?:wall decals|stickers)\. (?:White and size images are supplied and approved by the human operator\.|Those assets are supplied and approved by the human operator\.)",
                "For this operator-confirmed exception, generate a size-card technical base only; do not generate white-background hero or macro-detail images.",
                rule,
            )
            for rule in category_rules
        ]

    lines = [
        directive,
        f"Product subject: {subject}." if subject else "",
        f"Category: {category}." if category else "",
        f"Theme: {theme}." if theme else "",
        f"Structure / form: {structure}." if structure else "",
        f"Materials: {materials}." if materials else "",
        f"Colors: {colors}." if colors else "",
        f"Craft details: {craft}." if craft else "",
        f"Brand DNA keywords to preserve: {dna}." if dna else "",
        f"Listing title hint: {title}." if title else "",
        "",
        f"CRITICAL STYLE-LOCK: {style_lock}" if style_lock else (
            "CRITICAL STYLE-LOCK: Reproduce the exact product appearance from the reference image. "
            "Do not redesign pattern, color, shape, material, or print."
        ),
        "You may only change camera framing, lighting, background, and scene props required by this shot.",
        "The product itself must stay visually identical and recognizable.",
        *category_rules,
        (
            "HUMAN-APPROVED SIZE-CARD EXCEPTION: The operator has explicitly requested a size-card base "
            f"using these verified dimensions: {human_dimensions_prompt}. Generate no text, numbers, or labels; "
            "simple black dimension guide lines and arrowheads may be used when requested by the approved "
            "composition. The exact English dimension text is applied deterministically after generation."
        ) if human_size_override else "",
        "",
        f"Shot id: {item.get('id')}. Shot type: {itype}.",
        f"Shot title: {shot_title}.",
        f"Shot intent / composition: {focus}",
        f"Output aspect ratio: {ratio}.",
        no_text_requirement,
        "High-quality commercial product photography, sharp detail, no watermark, no brand logo invention, no unreadable gibberish text.",
    ]
    if itype == "white_bg":
        lines.append("No lifestyle props. Full product silhouette clear against pure white.")
    elif itype == "scene":
        lines.append("Show believable scale and placement; avoid distorting the product print.")
    elif itype == "selling_point":
        lines.append(
            "Keep the product print/pattern unchanged even in close-ups; emphasize the feature without inventing new product parts."
        )
    elif itype == "macro_detail":
        lines.append("Keep the crop tied to the same product. Do not invent micro-details or separate components.")
    elif itype == "size_card":
        lines.append(
            "Do not render numbers, prices, labels, or text. You may render simple black dimension "
            "guide lines and arrowheads requested by the approved composition; exact dimension text "
            "is added after human verification."
        )

    prompt = _ascii_safe("\n".join(x for x in lines if x is not None).strip())
    prompt_zh = _ascii_safe(
        "\n".join(
            [
                f"【类型】{itype}",
                f"【标题】{shot_title}",
                f"【焦点】{focus}",
                f"【主体】{subject}",
                f"【样式锁】{style_lock}",
                f"【品牌基因】{dna}",
                f"【比例】{ratio}",
            ]
        )
    )

    return {
        "id": str(item.get("id") or ""),
        "type": itype,
        "title": shot_title,
        "focus": focus,
        "aspect_ratio": ratio,
        "selected": True,
        "reference_image_url": reference_image_url,
        "category_profile": profile.get("id"),
        "prompt": prompt,
        "prompt_zh": prompt_zh,
        "negatives": (
            "redesigned product, wrong pattern, wrong colors, warped logo text, "
            "extra unrelated objects covering the product, watermark, low quality"
        ),
    }


def build_shot_prompts(
    plan: dict[str, Any],
    *,
    only_ids: list[str] | None = None,
) -> dict[str, Any]:
    analysis = plan.get("analysis") if isinstance(plan.get("analysis"), dict) else {}
    meta = plan.get("_meta") if isinstance(plan.get("_meta"), dict) else {}
    title = str(meta.get("title") or "").strip()
    ref = str(meta.get("image_url") or "").strip()
    profile = resolve_profile(title=title, category=str(analysis.get("category") or ""))

    items = selected_items(plan)
    if only_ids:
        allow = {str(x) for x in only_ids}
        items = [it for it in items if str(it.get("id") or "") in allow]

    shots = [
        build_shot_prompt(it, analysis, title=title, reference_image_url=ref) for it in items
    ]
    return {
        "shots": shots,
        "count": len(shots),
        "suite_summary": ((plan.get("suite") or {}) if isinstance(plan.get("suite"), dict) else {}).get(
            "summary"
        ),
        "analysis_subject": analysis.get("subject"),
        "style_lock": analysis.get("style_lock"),
        "reference_image_url": ref,
        "_source_meta": {
            "plan_model": meta.get("model"),
            "plan_title": title,
            "knowledge_version": meta.get("knowledge_version"),
            "category_profile": profile.get("id"),
        },
    }


def render_shots_markdown(bundle: dict[str, Any]) -> str:
    lines = ["# 分镜 Prompt（勾选项）", ""]
    if bundle.get("suite_summary"):
        lines.append(f"套图：{bundle['suite_summary']}")
    if bundle.get("analysis_subject"):
        lines.append(f"主体：{bundle['analysis_subject']}")
    if bundle.get("style_lock"):
        lines.append(f"样式锁：{bundle['style_lock']}")
    lines.append(f"共 {bundle.get('count', 0)} 张")
    lines.append("")
    type_label = {
        "selling_point": "卖点图",
        "scene": "场景图",
        "white_bg": "白底图",
    }
    for shot in bundle.get("shots") or []:
        label = type_label.get(shot.get("type"), shot.get("type"))
        lines.append(f"## `{shot.get('id')}` {label} · {shot.get('title')}")
        lines.append(f"- 比例：{shot.get('aspect_ratio')}")
        if shot.get("reference_image_url"):
            lines.append(f"- 参考图：{shot['reference_image_url']}")
        lines.append("")
        lines.append("### prompt")
        lines.append("```")
        lines.append(str(shot.get("prompt") or ""))
        lines.append("```")
        lines.append("")
    return "\n".join(lines) + "\n"


def save_shot_prompts(bundle: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "shot_prompts.json"
    md_path = out_dir / "shot_prompts.md"
    json_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_shots_markdown(bundle), encoding="utf-8")
    return json_path, md_path
