"""Explore: product image understanding + LinkFox-style suite planning.

Does NOT generate images. Uses ToAPIs chat/completions (vision) to:
1. Analyze a product photo (subject, materials, colors, craft, brand DNA)
2. Propose a selectable image suite (selling points / scenes / white-bg)

Intended as an exploration building block — not a productized UI flow yet.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.config import ROOT
from modules.sourcing.image_generation_knowledge import profile_context, resolve_profile, supported_shot_types

DEFAULT_VISION_MODEL = "gemini-3-pro-official"
DEFAULT_PROXY = "http://127.0.0.1:10808"
VISION_PLANNING_TIMEOUT_SECONDS = 360

# This English contract supersedes the legacy generic recipe below. The
# category knowledge policy appended at call time chooses an evidence-safe suite.
PLANNING_CONTRACT = """You are an ecommerce product-image planner. Inspect the supplied product image and optional title. Return one strict JSON object only, with no markdown and no explanation.

Required JSON schema:
{
  "analysis": {
    "subject": "English product description",
    "category": "English category",
    "theme": "English theme or style",
    "materials": ["only visually supported materials"],
    "colors": ["visually supported colors"],
    "craft_details": ["visible construction or surface details"],
    "structure": "English form and construction description",
    "background_of_source": "English source-background description",
    "brand_dna": ["English identity-preservation keywords"],
    "style_lock": "English constraint that preserves the source product identity"
  },
  "suite": {
    "summary": "English suite summary",
    "items": [
      {
        "id": "stable ID required by the category policy",
        "type": "white_bg | scene | selling_point | macro_detail | size_card",
        "title": "English shot title",
        "focus": "English composition and evidence-safe focus",
        "operator_title_zh": "Chinese operator-facing shot title",
        "operator_focus_zh": "Chinese operator-facing translation of the focus, without adding claims",
        "aspect_ratio": "1:1, 2:3, 3:2, or another intentional ratio",
        "selected": true
      }
    ]
  }
}

Rules:
1. The supplied CATEGORY KNOWLEDGE POLICY and OPERATOR-APPROVED SUITE REQUEST are authoritative. The operator request overrides recommended default counts, while category prohibitions and evidence rules remain hard constraints.
2. Return analysis values, title, and focus in English. Also return operator_title_zh and operator_focus_zh as faithful Chinese translations for the operator UI only. Do not propose visible Chinese text inside generated images.
3. Separate visible observations from unsupported claims. Do not invent components, materials, certifications, performance claims, measurements, brands, logos, or use cases.
4. A size_card is allowed only when the source evidence or human review verifies dimensions. Its model-generated base must contain no text; final English labels and numbers are added deterministically later. When the operator asks for visible numbers, describe both layers clearly: the base remains text-free, while the final delivered image contains the verified English dimension overlay.
5. Keep the product itself identical to the approved reference. A component/dimension sheet is evidence only unless explicitly marked as an approved identity reference.
6. All suite items should default to selected=true unless a policy or source-evidence safety issue requires exclusion."""

ANALYZE_AND_PLAN_PROMPT = """你是跨境电商商品套图策划师（类似 LinkFox 的套图规划能力）。
请严格依据用户提供的产品图（可参考附带标题），完成两件事，并只输出一个 JSON 对象（不要 markdown 代码块，不要解释）。

JSON schema:
{
  "analysis": {
    "subject": "主体一句话",
    "category": "品类",
    "theme": "主题/节日/风格",
    "materials": ["材质1", "材质2"],
    "colors": ["主色", "辅色"],
    "craft_details": ["可观察的工艺/细节特征，至少3条"],
    "structure": "结构/形态描述",
    "background_of_source": "原图背景简述",
    "brand_dna": ["锁定后续生图不变的视觉关键词，6-10个"],
    "style_lock": "一句硬性约束：后续生成时产品本体必须保持哪些外观特征不变"
  },
  "suite": {
    "summary": "套图方案一句话（例如：3卖点+2场景+1白底）",
    "items": [
      {
        "id": "sp1",
        "type": "selling_point",
        "title": "短标题（中文）",
        "focus": "本张要突出的卖点/构图意图",
        "aspect_ratio": "1:1",
        "selected": true
      }
    ]
  }
}

硬性要求：
1. items 必须恰好包含：3 张 type=selling_point（id=sp1..sp3）、2 张 type=scene（id=sc1..sc2）、1 张 type=white_bg（id=wb1）。
2. selling_point 要覆盖不同角度：工艺细节 / 主题色彩 / 材质耐用或功能卖点（按商品真实特征调整，勿空话）。
3. scene 要给出具体使用场景（例如门廊悬挂、室内装饰），贴合该商品真实用法。
4. white_bg 固定为电商白底主图，focus 写清棚拍光与居中构图。
5. selected 默认全部为 true。
6. 不要编造原图里看不到的部件；不确定就写“原图未明确”。
7. 输出必须是严格合法 JSON：双引号键名、无注释、无尾随逗号、字符串内换行用 \\n。
"""


def _load_toapis_config() -> dict[str, Any]:
    path = ROOT / "config" / "toapis.local.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_proxy(proxy: str | None = DEFAULT_PROXY) -> None:
    if not proxy:
        return
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.setdefault(key, proxy)


def _root_url(cfg: dict[str, Any]) -> str:
    base = str(cfg.get("base_url") or "https://toapis.com/v1").rstrip("/")
    if base.endswith("/v1"):
        return base[: -len("/v1")]
    return base


def _repair_json_text(raw: str) -> str:
    """Best-effort cleanup for common vision-model JSON slips."""
    s = raw.strip()
    # trailing commas before } or ]
    s = re.sub(r",\s*([}\]])", r"\1", s)
    # smart quotes
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    return s


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty model content")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
    if fence:
        raw = fence.group(1).strip()
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    errors: list[str] = []
    for cand in candidates:
        for variant in (cand, _repair_json_text(cand)):
            try:
                obj = json.loads(variant)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError as exc:
                errors.append(str(exc))
    raise ValueError(
        f"model did not return JSON object ({'; '.join(errors[:2])}): {raw[:400]}"
    )

def chat_completions(
    messages: list[dict[str, Any]],
    *,
    model: str = DEFAULT_VISION_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    timeout: int = VISION_PLANNING_TIMEOUT_SECONDS,
    proxy: str | None = DEFAULT_PROXY,
) -> dict[str, Any]:
    """Call ToAPIs OpenAI-compatible chat endpoint. Returns full response JSON."""
    cfg = _load_toapis_config()
    key = str(cfg.get("api_key") or "").strip()
    if not key:
        raise RuntimeError("toapis.local.json missing api_key")
    _ensure_proxy(proxy)
    root = _root_url(cfg)
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{root}/v1/chat/completions",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "OrbitImageSuitePlan/0.1",
        },
    )
    handlers: list[Any] = [urllib.request.HTTPSHandler(context=ssl.create_default_context())]
    if proxy:
        handlers.insert(0, urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ToAPIs chat HTTP {exc.code}: {err[:500]}") from exc
    except TimeoutError as exc:
        raise RuntimeError(
            f"ToAPI 视觉规划等待超过 {timeout} 秒，未收到完整响应。"
            "为避免一次操作重复消耗 Token，系统没有自动重试；请稍后手动点击重试。"
        ) from exc
    except urllib.error.URLError as exc:
        route = f" via proxy {proxy}" if proxy else ""
        if isinstance(exc.reason, TimeoutError) or "timed out" in str(exc.reason).lower():
            raise RuntimeError(
                f"ToAPI 视觉规划等待超过 {timeout} 秒，未收到完整响应。"
                "为避免一次操作重复消耗 Token，系统没有自动重试；请稍后手动点击重试。"
            ) from exc
        raise RuntimeError(f"ToAPIs chat network error{route}: {exc.reason}") from exc


def message_content(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError(f"ToAPIs chat empty choices: {str(response)[:240]}")
    content = ((choices[0].get("message") or {}).get("content")) or ""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        content = "\n".join(parts)
    content = str(content).strip()
    if not content:
        raise RuntimeError("ToAPIs chat returned empty content")
    return content


def _normalize_suite(data: dict[str, Any]) -> dict[str, Any]:
    analysis = data.get("analysis") if isinstance(data.get("analysis"), dict) else {}
    suite = data.get("suite") if isinstance(data.get("suite"), dict) else {}
    items = suite.get("items") if isinstance(suite.get("items"), list) else []
    clean_items = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        itype = str(item.get("type") or "").strip()
        if itype not in supported_shot_types():
            continue
        clean_items.append(
            {
                "id": str(item.get("id") or f"item{i+1}"),
                "type": itype,
                "title": str(item.get("title") or "").strip(),
                "focus": str(item.get("focus") or "").strip(),
                "operator_title_zh": str(item.get("operator_title_zh") or "").strip(),
                "operator_focus_zh": str(item.get("operator_focus_zh") or "").strip(),
                "aspect_ratio": str(item.get("aspect_ratio") or "1:1").strip() or "1:1",
                "selected": bool(item.get("selected", True)),
            }
        )
    return {
        "analysis": analysis,
        "suite": {
            "summary": str(suite.get("summary") or "").strip(),
            "items": clean_items,
        },
    }


SHOT_TYPE_PREFIXES = {
    "white_bg": "wb",
    "scene": "sc",
    "selling_point": "sp",
    "macro_detail": "dt",
    "size_card": "sz",
}


def normalize_suite_request(
    requested_counts: dict[str, Any],
    *,
    title: str = "",
    category: str = "",
    size_card: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate the operator request before any model call."""
    profile = resolve_profile(title=title, category=category)
    clean_counts: dict[str, int] = {}
    for shot_type in SHOT_TYPE_PREFIXES:
        raw = requested_counts.get(shot_type, 0)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        clean_counts[shot_type] = max(0, min(value, 1 if shot_type == "size_card" else 6))
    if sum(clean_counts.values()) < 1:
        raise ValueError("select at least one image before requesting AI storyboard planning")
    profile_id = str(profile.get("id") or "")
    if profile_id in {"wall_decal", "product_sticker"}:
        prohibited = [shot_type for shot_type in ("white_bg", "macro_detail") if clean_counts.get(shot_type)]
        if prohibited:
            raise ValueError(
                f"{profile_id} local policy does not allow AI-generated "
                + ", ".join(prohibited)
            )
    size = size_card if isinstance(size_card, dict) else {}
    if clean_counts.get("size_card"):
        if not bool(size.get("confirmed")) or not str(size.get("dimensions") or "").strip():
            raise ValueError("confirm exact dimensions before requesting an AI size-card storyboard")
    return {
        "category_profile": profile_id,
        "type_counts": clean_counts,
        "total": sum(clean_counts.values()),
        "aspect_ratio": "1:1",
        "size_card": {
            "enabled": bool(clean_counts.get("size_card")),
            "dimensions": str(size.get("dimensions") or "").strip()[:240],
            "confirmed": bool(size.get("confirmed")),
        },
    }


def suite_request_prompt(request: dict[str, Any]) -> str:
    """Render a strict model-facing request after local validation."""
    counts = request.get("type_counts") if isinstance(request.get("type_counts"), dict) else {}
    lines = [
        "OPERATOR-APPROVED SUITE REQUEST (exact; overrides recommended default counts):",
        json.dumps(
            {
                "category_profile": request.get("category_profile"),
                "type_counts": counts,
                "total": request.get("total"),
                "aspect_ratio": "1:1",
                "verified_size_dimensions": (
                    (request.get("size_card") or {}).get("dimensions")
                    if (request.get("size_card") or {}).get("enabled")
                    else ""
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        "Return exactly the requested number of items for every type and no extra items.",
        "You choose every concrete scene, composition, title, and focus. Local code will not invent or fill missing storyboard items.",
        "Every item must include a faithful Chinese operator_title_zh and operator_focus_zh for human review.",
        "Every aspect_ratio must be 1:1.",
    ]
    return "\n".join(lines)


def enforce_category_policy(
    candidate: dict[str, Any],
    *,
    title: str = "",
    category: str = "",
    suite_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep only the category-approved shot IDs/types from a model proposal."""
    profile = resolve_profile(title=title, category=category)
    suite = candidate.get("suite") if isinstance(candidate.get("suite"), dict) else {}
    proposed = suite.get("items") if isinstance(suite.get("items"), list) else []
    if isinstance(suite_request, dict):
        request = normalize_suite_request(
            suite_request.get("type_counts") if isinstance(suite_request.get("type_counts"), dict) else {},
            title=title,
            category=category,
            size_card=suite_request.get("size_card") if isinstance(suite_request.get("size_card"), dict) else {},
        )
        locked_items: list[dict[str, Any]] = []
        rejected: list[str] = []
        for shot_type in SHOT_TYPE_PREFIXES:
            requested = int((request.get("type_counts") or {}).get(shot_type) or 0)
            candidates = [
                dict(row) for row in proposed
                if isinstance(row, dict)
                and str(row.get("type") or "") == shot_type
                and bool(row.get("selected", True))
            ]
            if len(candidates) < requested:
                raise ValueError(
                    f"AI storyboard returned {len(candidates)} {shot_type} item(s); "
                    f"the approved request requires exactly {requested}"
                )
            for extra in candidates[requested:]:
                rejected.append(str(extra.get("id") or f"extra_{shot_type}"))
            for index, proposal in enumerate(candidates[:requested], start=1):
                title_en = str(proposal.get("title") or "").strip()
                focus_en = str(proposal.get("focus") or "").strip()
                title_zh = str(proposal.get("operator_title_zh") or "").strip()
                focus_zh = str(proposal.get("operator_focus_zh") or "").strip()
                if not all((title_en, focus_en, title_zh, focus_zh)):
                    raise ValueError(
                        f"AI storyboard {shot_type} item {index} is missing an English or Chinese title/focus"
                    )
                row = {
                    "id": f"{SHOT_TYPE_PREFIXES[shot_type]}{index}",
                    "type": shot_type,
                    "title": title_en,
                    "focus": focus_en,
                    "operator_title_zh": title_zh,
                    "operator_focus_zh": focus_zh,
                    "focus_zh": focus_zh,
                    "aspect_ratio": "1:1",
                    "selected": True,
                    "ai_planned": True,
                }
                if shot_type == "size_card":
                    row.update(
                        {
                            "human_dimensions": str((request.get("size_card") or {}).get("dimensions") or ""),
                            "human_dimensions_confirmed": True,
                            "human_override": True,
                        }
                    )
                locked_items.append(row)
        allowed_types = {
            shot_type
            for shot_type, count in (request.get("type_counts") or {}).items()
            if int(count or 0) > 0
        }
        rejected.extend(
            str(row.get("id") or "unnamed")
            for row in proposed
            if isinstance(row, dict) and str(row.get("type") or "") not in allowed_types
        )
        return {
            "analysis": candidate.get("analysis") if isinstance(candidate.get("analysis"), dict) else {},
            "suite": {
                "summary": str(suite.get("summary") or "").strip(),
                "items": locked_items,
            },
            "_policy": {
                "category_profile": profile.get("id"),
                "rejected_item_ids": list(dict.fromkeys(rejected)),
                "requested_type_counts": request.get("type_counts"),
                "accepted_item_count": len(locked_items),
                "planning_source": "ai_with_local_pre_and_post_validation",
            },
        }
    by_id = {str(row.get("id") or ""): row for row in proposed if isinstance(row, dict)}
    locked_items: list[dict[str, Any]] = []
    rejected: list[str] = []
    allowed = {str(row.get("id") or ""): str(row.get("type") or "") for row in profile.get("recommended_suite") or []}
    for row in proposed:
        if not isinstance(row, dict):
            continue
        shot_id = str(row.get("id") or "")
        if shot_id not in allowed or str(row.get("type") or "") != allowed.get(shot_id):
            rejected.append(shot_id or "unnamed")
    for template in profile.get("recommended_suite") or []:
        if not isinstance(template, dict):
            continue
        shot_id = str(template.get("id") or "")
        proposal = by_id.get(shot_id) or {}
        row = dict(template)
        row["selected"] = bool(proposal.get("selected", True))
        row["title"] = str(proposal.get("title") or template.get("title") or "").strip()
        row["focus"] = str(proposal.get("focus") or "").strip()
        locked_items.append(row)
    return {
        "analysis": candidate.get("analysis") if isinstance(candidate.get("analysis"), dict) else {},
        "suite": {"summary": str(suite.get("summary") or "").strip(), "items": locked_items},
        "_policy": {
            "category_profile": profile.get("id"),
            "rejected_item_ids": rejected,
            "allowed_item_ids": [str(row.get("id") or "") for row in profile.get("recommended_suite") or [] if isinstance(row, dict)],
        },
    }


def analyze_and_plan_suite(
    image_url: str,
    *,
    title: str = "",
    model: str = DEFAULT_VISION_MODEL,
    proxy: str | None = DEFAULT_PROXY,
    max_tokens: int = 4096,
    reference_image_urls: list[str] | None = None,
    evidence_context: str = "",
    suite_request: dict[str, Any] | None = None,
    storyboard_revision_context: str = "",
) -> dict[str, Any]:
    """Vision read + suite plan in one call. Returns parsed JSON (+ meta)."""
    url = str(image_url or "").strip()
    if not url.startswith("https://"):
        raise ValueError("image_url must be a public https URL")

    profile = resolve_profile(title=title)
    urls = [url]
    for extra in reference_image_urls or []:
        clean = str(extra or "").strip()
        if clean.startswith("https://") and clean not in urls:
            urls.append(clean)
    user_text = PLANNING_CONTRACT + "\n\n" + profile_context(profile)
    if title.strip():
        user_text += f"\n\nProduct title evidence: {title.strip()}"
    if evidence_context.strip():
        user_text += "\n\nSOURCE EVIDENCE AND PROHIBITIONS (must not be contradicted):\n" + evidence_context.strip()
    if isinstance(suite_request, dict):
        normalized_request = normalize_suite_request(
            suite_request.get("type_counts") if isinstance(suite_request.get("type_counts"), dict) else {},
            title=title,
            category=str(profile.get("description") or ""),
            size_card=suite_request.get("size_card") if isinstance(suite_request.get("size_card"), dict) else {},
        )
        user_text += "\n\n" + suite_request_prompt(normalized_request)
    if storyboard_revision_context.strip():
        user_text += (
            "\n\nHUMAN STORYBOARD REVISION REQUEST (authoritative for composition, "
            "but it cannot override product facts, category prohibitions, exact counts, or 1:1 output):\n"
            + storyboard_revision_context.strip()
            + "\nReturn a complete revised suite, including unchanged items as well as revised items."
        )

    response = chat_completions(
        [
            {
                "role": "user",
                "content": ([{"type": "image_url", "image_url": {"url": item}} for item in urls]
                            + [{"type": "text", "text": user_text}]),
            }
        ],
        model=model,
        proxy=proxy,
        max_tokens=max_tokens,
    )
    content = message_content(response)
    try:
        parsed = _normalize_suite(_extract_json_object(content))
    except Exception as exc:
        raise RuntimeError(
            f"failed to parse suite plan JSON: {exc}\n--- raw ---\n{content[:3000]}"
        ) from exc
    parsed["_meta"] = {
        "model": model,
        "image_url": url,
        "reference_image_urls": urls,
        "title": title,
        "usage": response.get("usage"),
        "raw_content": content,
        "knowledge_version": "1.0.0",
        "category_profile": profile.get("id"),
    }
    return parsed


def render_plan_markdown(plan: dict[str, Any]) -> str:
    analysis = plan.get("analysis") or {}
    suite = plan.get("suite") or {}
    lines = ["# 商品读图理解 + 套图策划", ""]
    lines.append("## 读图理解")
    for key in (
        "subject",
        "category",
        "theme",
        "structure",
        "background_of_source",
        "style_lock",
    ):
        if analysis.get(key):
            lines.append(f"- **{key}**: {analysis[key]}")
    for key in ("materials", "colors", "craft_details", "brand_dna"):
        val = analysis.get(key)
        if isinstance(val, list) and val:
            lines.append(f"- **{key}**: " + "；".join(str(x) for x in val))
    lines.append("")
    lines.append("## 套图方案")
    if suite.get("summary"):
        lines.append(f"{suite['summary']}")
        lines.append("")
    type_label = {
        "selling_point": "卖点图",
        "scene": "场景图",
        "white_bg": "白底图",
    }
    for item in suite.get("items") or []:
        mark = "[x]" if item.get("selected") else "[ ]"
        label = type_label.get(item.get("type"), item.get("type"))
        lines.append(
            f"- {mark} `{item.get('id')}` **{label}** · {item.get('title')} "
            f"（{item.get('aspect_ratio')}）"
        )
        if item.get("focus"):
            lines.append(f"  - 焦点：{item['focus']}")
    meta = plan.get("_meta") or {}
    if meta.get("model"):
        lines.append("")
        lines.append(f"_model: {meta['model']}_")
    return "\n".join(lines) + "\n"


def save_plan(plan: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "suite_plan.json"
    md_path = out_dir / "suite_plan.md"
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_plan_markdown(plan), encoding="utf-8")
    return json_path, md_path
