"""1688 货源 → 多平台上架文案（DeepSeek）。"""

from __future__ import annotations

import json
import re
from typing import Any

from core.config import load_settings
from core.llm import chat_completion, require_api_key

SYSTEM_PROMPT = """你是跨境电商 Listing 专家，熟悉 TikTok Shop 东南亚、Shopee CNSC 全球店、Ozon 俄罗斯站规则。

根据提供的 1688 货源信息，生成可直接上架的文案。不得编造未提供的规格、材质、尺寸。

## 输出格式（严格 JSON，无 Markdown 代码块）
{
  "tiktok": {
    "MY": {"title": "≤255字符", "description_html": "<p>...</p>"},
    "PH": {"title": "≤255字符", "description_html": "<p>...</p>"}
  },
  "shopee": {
    "title": "English title ≤255 chars",
    "description_html": "<p>English description with bullet points</p>"
  },
  "ozon": {
    "title": "≤200字符 俄语标题",
    "description_html": "<p>俄语描述</p>"
  },
  "cost_cny": 6.74,
  "notes": "简短中文备注：卖点、注意项"
}

## 语言
- TikTok MY：英文为主，可含少量马来常用词
- TikTok PH：英文
- Shopee：英文（CNSC 全球）
- Ozon：俄语

## 标题规则
- 前 40 字符含核心搜索词
- 禁止 emoji、全大写、虚假夸大
- 自然融入颜色/材质/场景（仅基于提供数据）

## 描述规则
- HTML 用 <p>、<ul><li>，简洁可读
- 含尺寸、材质、适用场景、包装内容（如有）
- 6 色变体需在描述中说明可选图案"""


def _strip_json_fence(text: str) -> str:
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        return m.group(1).strip()
    return text


def _fallback_copy(data: dict) -> dict:
    attrs = data.get("attributes") or {}
    title_cn = data.get("title") or "Home Organizer"
    colors = (attrs.get("颜色") or "").replace(",", " / ")
    material = attrs.get("材质") or ""
    item_no = attrs.get("货号") or ""
    price_min = (data.get("price") or {}).get("min") or "0"
    base = (
        f"Cartoon Wall Hanging Storage Organizer Cotton Linen Pocket "
        f"{colors} Bedroom Door Organizer"
    )[:255]
    desc_en = (
        f"<p>Multi-pocket wall hanging organizer for bedroom, door, or closet.</p>"
        f"<ul><li>Material: {material or 'cotton linen'}</li>"
        f"<li>Colors: {colors or 'multiple designs'}</li>"
        f"<li>Style: cartoon animal design</li>"
        f"<li>Item no.: {item_no}</li></ul>"
        f"<p>Source title: {title_cn}</p>"
    )
    return {
        "tiktok": {
            "MY": {"title": base, "description_html": desc_en},
            "PH": {"title": base, "description_html": desc_en},
        },
        "shopee": {"title": base, "description_html": desc_en},
        "ozon": {
            "title": "Настенный органайзер карманами детский мультяшный",
            "description_html": f"<p>Настенный органайзер из {material or 'хлопок-лен'}.</p>",
        },
        "cost_cny": float(price_min) if price_min else None,
        "notes": "规则模板（未调用 AI）",
    }


def build_product_context(data: dict) -> str:
    attrs = data.get("attributes") or {}
    skus = data.get("skus") or []
    price = data.get("price") or {}
    lines = [
        f"原标题: {data.get('title') or ''}",
        f"货号: {attrs.get('货号') or ''}",
        f"供应商: {(data.get('seller') or {}).get('company') or ''}",
        f"采购价: ¥{price.get('display') or ''} (MOQ {price.get('moq') or 1})",
        f"销量: {data.get('sale_count') or 0}",
    ]
    for k, v in attrs.items():
        lines.append(f"{k}: {v}")
    if skus:
        specs = [s.get("spec") or "" for s in skus if s.get("spec")]
        lines.append(f"SKU 颜色/规格: {', '.join(specs)}")
    return "\n".join(lines)


def ai_enabled() -> bool:
    cfg = load_settings().get("ai") or {}
    return bool(cfg.get("api_key"))


def generate_copy(data: dict) -> dict[str, Any]:
    """生成 TikTok / Shopee / Ozon 文案。"""
    if not ai_enabled():
        return _fallback_copy(data)

    require_api_key()
    ctx = build_product_context(data)
    raw = chat_completion(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ctx},
        ],
        max_tokens=int((load_settings().get("ai") or {}).get("listing_max_tokens") or 1200),
        temperature=0.35,
    )
    try:
        parsed = json.loads(_strip_json_fence(raw))
        if isinstance(parsed, dict) and parsed.get("tiktok"):
            return parsed
    except json.JSONDecodeError:
        pass
    fb = _fallback_copy(data)
    fb["notes"] = f"AI 解析失败，已用模板。原始: {raw[:200]}"
    return fb
