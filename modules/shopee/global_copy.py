"""CNSC 全球商品英文标题/描述：优先菲律宾 TK，无 PH 则 AI 翻译并写满。"""

from __future__ import annotations

import json
import re

GLOBAL_TITLE_MAX = 120
GLOBAL_DESC_MAX = 3000
GLOBAL_DESC_TARGET = 2400

# 铺货时优先用 PH 英文作为母版（TH/VN 仅作最后回退）
TK_SOURCE_ORDER = ("PH", "MY", "TH", "VN")

_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\udfff]")
_VIET_RE = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđĐ]"
)

_SYSTEM = """You are a Shopee CNSC cross-border listing copywriter. Write in English ONLY.

Rules:
- NEVER output Thai, Vietnamese, Malay, Chinese, or any non-English script or wording.
- If the source listing is already English (Philippines TikTok Shop), REUSE its wording and keywords; expand the description, do not replace good phrases.
- If the source is Thai/Vietnamese/Malay or any non-English language, translate accurately to natural ecommerce English, then expand.
- Title: 80-120 characters, searchable, no emoji, no ALL CAPS blocks, no markdown, no bullet options, no preamble.
- Description: plain text, target 1800-2800 characters when source description is short or missing; otherwise expand to at least 1200. Include product type, material, size/dimensions if known, quantity, features, usage scenes, installation/care, shipping note. End with "Seller SKU: __SKU__".
- Do not invent specs not in the source. Do not write "Here are options" or multiple titles.

Output ONLY valid JSON with keys title and description (no markdown)."""

_TRANSLATE_LABEL_SYSTEM = (
    "Translate the ecommerce product variant label to concise English (2-5 words). "
    "Output ONLY the English label, no quotes or explanation. Examples: สีน้ำเงิน→Blue, ขาว→White."
)


def strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def is_mostly_english(text: str) -> bool:
    if not text:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    ascii_letters = sum(1 for c in letters if ord(c) < 128)
    return ascii_letters / len(letters) >= 0.85


def contains_non_english_script(text: str) -> bool:
    t = text or ""
    return bool(_THAI_RE.search(t) or _CJK_RE.search(t) or _VIET_RE.search(t))


def is_english_listing_text(text: str) -> bool:
    if not (text or "").strip():
        return False
    if contains_non_english_script(text):
        return False
    return is_mostly_english(text)


def _parse_ai_json(raw: str) -> dict:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {}


def _clamp_title(title: str, model_sku: str) -> str:
    t = re.sub(r"\s+", " ", (title or "").strip()).strip('"').replace("**", "")
    # 去掉模型偶尔输出的前言
    for bad in (
        r"^Here are.*?[:：]\s*",
        r"^Option \d+[:：]\s*",
        r"^Title[:：]\s*",
    ):
        t = re.sub(bad, "", t, flags=re.I)
    if len(t) > GLOBAL_TITLE_MAX:
        t = t[: GLOBAL_TITLE_MAX - 3].rstrip() + "..."
    if len(t) < 20:
        t = f"Home Decor Product SKU {model_sku} {t}".strip()[:GLOBAL_TITLE_MAX]
    return t


def _clamp_description(desc: str, model_sku: str) -> str:
    d = re.sub(r"\s+", " ", (desc or "").strip())
    if len(d) < 120:
        d = (
            d
            + f" Quality home product for daily use. Easy to use and suitable for modern living spaces. "
            f"Seller SKU: {model_sku}."
        )
    if f"SKU {model_sku}" not in d and f"SKU: {model_sku}" not in d:
        d = d.rstrip() + f" Seller SKU: {model_sku}."
    if len(d) > GLOBAL_DESC_MAX:
        d = d[: GLOBAL_DESC_MAX - 3].rstrip() + "..."
    return d


def _extra_specs(detail: dict) -> str:
    lines: list[str] = []
    sku = (detail.get("skus") or [{}])[0]
    dim = sku.get("sku_dimensions") or detail.get("package_dimensions") or {}
    if dim:
        lines.append(
            f"Dimensions (cm): {dim.get('length')} x {dim.get('width')} x {dim.get('height')}"
        )
    w = sku.get("sku_weight") or detail.get("package_weight") or {}
    if w:
        lines.append(f"Weight: {w.get('value')} {w.get('unit') or ''}".strip())
    for attr in (detail.get("product_attributes") or [])[:12]:
        vals = [v.get("name") or "" for v in attr.get("values") or [] if v.get("name")]
        if vals and attr.get("name"):
            lines.append(f"{attr.get('name')}: {', '.join(vals[:5])}")
    return "\n".join(lines)


def _ai_chat(system: str, user: str, *, max_tokens: int = 120) -> str:
    from core.llm import ai_config, chat_completion

    cfg = ai_config()
    if not cfg.get("api_key"):
        return ""
    return (chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=max_tokens,
        temperature=0.2,
    ) or "").strip()


def _guess_material(title_src: str, detail: dict) -> str:
    hay = f"{title_src} {strip_html(detail.get('description') or '')}".lower()
    for token, label in (
        ("acrylic", "Acrylic"),
        ("akrilik", "Acrylic"),
        ("ceramic", "Ceramic"),
        ("wood", "Wooden"),
        ("bamboo", "Bamboo"),
        ("metal", "Metal"),
        ("iron", "Iron"),
        ("pvc", "PVC"),
        ("plastic", "Plastic"),
        ("glass", "Glass"),
        ("fabric", "Fabric"),
        ("cotton", "Cotton"),
        ("resin", "Resin"),
    ):
        if token in hay:
            return label
    return ""


def _generic_english_title(detail: dict, model_sku: str, title_src: str) -> str:
    material = _guess_material(title_src, detail)
    parts = [material, "Home Decor Ornament"] if material else ["Home Decor Ornament"]
    sku = (detail.get("skus") or [{}])[0]
    dim = sku.get("sku_dimensions") or detail.get("package_dimensions") or {}
    size_note = ""
    if dim.get("length") and dim.get("width"):
        size_note = f", {dim.get('length')}x{dim.get('width')} cm"
    title = " ".join(parts) + f" for Bedroom, Living Room and Desk Display{size_note}"
    return _clamp_title(title, model_sku)


def _generic_english_description(detail: dict, model_sku: str, title: str) -> str:
    specs = _extra_specs(detail)
    body = (
        f"{title}. Suitable for home decoration, shelf styling and desktop display. "
        f"Designed for bedroom, living room, office and gift scenes. "
        f"Please review product photos and size information before publishing."
    )
    if specs:
        body += f" {specs}."
    body += f" Seller SKU: {model_sku}."
    return _clamp_description(body, model_sku)


def english_variant_label(raw: str, fallback: str = "") -> str:
    """Color/规格选项名 → 英文（全球商品 tier option）。"""
    name = (raw or "").strip().split("/")[0].strip()[:80]
    fb = (fallback or "Variant").strip()
    if name and is_english_listing_text(name):
        return name[:50]
    if name:
        translated = _ai_chat(_TRANSLATE_LABEL_SYSTEM, name, max_tokens=32)
        translated = re.sub(r"\s+", " ", translated).strip('"').strip()[:50]
        if translated and is_english_listing_text(translated):
            return translated
    return f"Variant {fb}"[:50]


def build_global_copy(
    detail: dict,
    model_sku: str,
    *,
    source_region: str = "",
) -> dict:
    """生成全球商品英文名与长描述。"""
    title_src = strip_html(detail.get("title") or "")
    desc_src = strip_html(detail.get("description") or "")
    if len(desc_src) < 80:
        desc_src = f"{title_src}. {desc_src}".strip()

    ph_english = source_region.upper() == "PH" and is_english_listing_text(title_src)
    title = ""
    description = ""

    try:
        from core.config import load_settings
        from core.llm import ai_config, chat_completion

        cfg = ai_config()
        if cfg.get("api_key"):
            listing_tokens = int((load_settings().get("ai") or {}).get("listing_max_tokens") or 1200)
            if len(desc_src) < 300:
                listing_tokens = max(listing_tokens, 2000)
            user = (
                f"Seller SKU / match code: {model_sku}\n"
                f"TikTok source region: {source_region or 'unknown'}\n"
                f"Source already English (PH): {'yes' if ph_english else 'no'}\n"
                f"IMPORTANT: Output English only. Never copy Thai/Vietnamese/Malay text.\n\n"
                f"Source title:\n{title_src[:500]}\n\n"
                f"Source description:\n{desc_src[:3500] or '(empty — expand from title and specs)'}\n\n"
                f"Extra product specs:\n{_extra_specs(detail) or '(none)'}\n"
            )
            raw = chat_completion(
                [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": user},
                ],
                max_tokens=listing_tokens,
                temperature=0.35,
            )
            parsed = _parse_ai_json(raw)
            title = str(parsed.get("title") or "").strip()
            description = str(parsed.get("description") or "").strip()
            if title and not is_english_listing_text(title):
                title = ""
            if description and not is_english_listing_text(description):
                description = ""
    except Exception:
        pass

    if ph_english and not title:
        title = title_src
    if ph_english and len(description) < 400:
        description = ""

    if not title:
        if ph_english and is_english_listing_text(title_src):
            title = title_src
        else:
            title = _generic_english_title(detail, model_sku, title_src)
    if not description or len(description) < 150:
        if ph_english and is_english_listing_text(desc_src):
            description = desc_src
        else:
            description = _generic_english_description(detail, model_sku, title)

    title = _clamp_title(title, model_sku)
    description = _clamp_description(description, model_sku)
    if not is_english_listing_text(title):
        title = _clamp_title(f"Home Decor Product SKU {model_sku}", model_sku)
    if not is_english_listing_text(description):
        description = _clamp_description(
            f"{title}. Quality home product for daily use. Seller SKU: {model_sku}.",
            model_sku,
        )

    return {
        "title": title,
        "description": description,
        "source_region": source_region.upper(),
        "used_ph_english": ph_english,
    }
