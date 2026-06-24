"""AI 标题优化：基于商品详情生成符合 TikTok Shop 规范的标题。"""

from __future__ import annotations

import json
import re

from core.llm import chat_completion

TITLE_MAX = 255

REGION_LANGUAGE = {
    "MY": "Bahasa Malaysia（马来西亚站，可保留当地常见的英文品类词）",
    "VN": "Tiếng Việt（越南语）",
    "TH": "ภาษาไทย（泰语）",
    "PH": "English（菲律宾站）",
}

SYSTEM_PROMPT = """你是 TikTok Shop 东南亚跨境卖家的资深 Listing 优化专家。
你的任务：根据提供的真实商品信息，写出高转化、可搜索的商品标题。

## 标题结构（按优先级排列，根据商品灵活组合，不要机械套模板）
1. **核心搜索词**：类目词 + 产品类型（放最前，前 40 字符内应包含买家最常搜的词）
2. **产品主体**：清晰说明是什么商品
3. **关键属性**：尺寸、数量、颜色、材质、款式等（仅使用提供的数据，不可编造）
4. **使用场景**：1–2 个真实适用场景（如 kitchen, bedroom, bathroom）
5. **差异化卖点**：仅当数据中有依据时写（如 waterproof, self-adhesive, removable）

## 硬性规则
- 总长度 ≤ 255 字符（含空格）
- 使用指定站点的语言撰写
- 若提供 hot_keywords：优先自然融入前 40 字符内的搜索词，不要堆砌
- 若提供 competitor_titles：仅参考其结构、关键词顺序和长度，禁止照抄句子
- 禁止：虚假夸大、医疗功效、绝对化用语（best #1）、无关堆砌、重复同义词
- 禁止：emoji、全大写、过多标点或特殊符号
- 不得编造未在商品信息中出现的规格、材质、数量
- 若原标题已有有效信息，保留并优化表达，不要丢弃关键 SKU 规格

## 输出
只输出一行最终标题，不要引号、不要解释、不要 Markdown。"""


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _category_path(detail: dict) -> tuple[str, str]:
    chain = detail.get("category_chains") or []
    names = [c.get("local_name") or "" for c in chain if c.get("local_name")]
    leaf = names[-1] if names else ""
    return " > ".join(names), leaf


def _sku_variants(detail: dict) -> list[dict]:
    out: list[dict] = []
    for sku in detail.get("skus") or []:
        attrs = [
            a.get("value_name") or a.get("name") or ""
            for a in sku.get("sales_attributes") or []
        ]
        attrs = [x for x in attrs if x]
        out.append({
            "seller_sku": sku.get("seller_sku") or "",
            "variant": " / ".join(attrs),
            "price": (sku.get("price") or {}).get("sale_price"),
        })
    return out[:10]


def _product_properties(detail: dict) -> list[dict]:
    props: list[dict] = []
    for attr in detail.get("product_attributes") or []:
        vals = [v.get("name") or "" for v in attr.get("values") or [] if v.get("name")]
        if vals:
            props.append({
                "name": attr.get("name") or "",
                "values": vals,
            })
    return props[:15]


def build_product_context(detail: dict, item: dict, intel: dict | None = None) -> dict:
    category_path, category_leaf = _category_path(detail)
    desc = _strip_html(detail.get("description") or "")
    if len(desc) > 600:
        desc = desc[:600] + "…"
    ctx = {
        "region": (item.get("region") or "").upper(),
        "language": REGION_LANGUAGE.get((item.get("region") or "").upper(), "English"),
        "current_title": item.get("old_title") or detail.get("title") or "",
        "category_path": category_path,
        "category_leaf": category_leaf,
        "description": desc,
        "skus": _sku_variants(detail),
        "product_attributes": _product_properties(detail),
        "seller_sku": item.get("seller_sku") or "",
        "units_sold_last_30_days": item.get("units_sold", 0),
        "stock": item.get("stock", 0),
    }
    if intel:
        ctx["hot_keywords"] = intel.get("hot_keywords") or []
        ctx["competitor_titles"] = intel.get("competitor_titles") or []
        if intel.get("matched_categories"):
            ctx["keyword_matched_categories"] = intel["matched_categories"]
    return ctx


def _clean_ai_title(raw: str) -> str:
    title = raw.strip().strip('"\'""''')
    title = re.sub(r"^#+\s*", "", title)
    title = re.sub(r"\s+", " ", title).strip(" ,-|")
    if len(title) > TITLE_MAX:
        title = title[: TITLE_MAX - 1].rsplit(" ", 1)[0]
    return title[:TITLE_MAX]


def suggest_title_ai(context: dict) -> str:
    region = context.get("region") or "?"
    lang = context.get("language") or "English"
    user_prompt = f"""请为以下 TikTok Shop 商品生成优化标题。

站点：{region}
撰写语言：{lang}

商品信息（JSON）：
{json.dumps(context, ensure_ascii=False, indent=2)}

请输出优化后的标题（一行，≤255 字符）。"""

    raw = chat_completion([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ])
    title = _clean_ai_title(raw)
    if len(title) < 10:
        raise RuntimeError(f"AI 标题过短或无效: {title!r}")
    return title


LISTING_SYSTEM_PROMPT = """你是 TikTok Shop 东南亚跨境卖家的 Listing 优化专家。
根据真实商品信息，同时优化「标题」和「详情描述（HTML）」以提升转化。

## 标题规则
- 总长度 ≤ 255 字符，使用指定站点语言
- 前 40 字符包含核心搜索词；禁止 emoji、全大写、虚假夸大
- 不得编造未提供的规格、材质、数量

## 详情描述规则
- 输出 TikTok 兼容 HTML：1 段开场 `<p>` + 3–5 条卖点 `<ul><li>`
- 总长度 ≤ 2000 字符（含标签）
- 使用指定站点语言；强调尺寸、材质、用途、包装内容（仅基于提供的数据）
- 禁止医疗功效、绝对化用语、外链、图片标签
- 若原描述有有效信息，保留并优化表达

## 输出格式（严格 JSON，无 Markdown 代码块）
{"title": "...", "description": "<p>...</p><ul><li>...</li></ul>"}"""


def _parse_listing_json(raw: str) -> tuple[str, str]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise RuntimeError(f"AI 未返回有效 JSON: {text[:120]!r}")
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise RuntimeError("AI 返回格式错误")
    title = _clean_ai_title(str(data.get("title") or ""))
    desc = str(data.get("description") or "").strip()
    if len(title) < 10:
        raise RuntimeError(f"AI 标题过短: {title!r}")
    if len(desc) < 30:
        raise RuntimeError("AI 详情过短")
    if len(desc) > 4000:
        desc = desc[:4000]
    return title, desc


def suggest_listing_ai(context: dict) -> tuple[str, str]:
    from core.config import get

    region = context.get("region") or "?"
    lang = context.get("language") or "English"
    listing_tokens = int(get("ai.listing_max_tokens", 1200))
    user_prompt = f"""请为以下 TikTok Shop 商品生成优化标题 + 详情描述。

站点：{region}
撰写语言：{lang}
场景：该商品近 28 天有点击兴趣但转化偏低，请重点优化详情卖点与标题搜索词。

商品信息（JSON）：
{json.dumps(context, ensure_ascii=False, indent=2)}

请严格输出 JSON：{{"title": "...", "description": "<p>...</p><ul>...</ul>"}}"""

    raw = chat_completion(
        [
            {"role": "system", "content": LISTING_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=listing_tokens,
    )
    return _parse_listing_json(raw)


def ai_enabled() -> bool:
    from core.llm import ai_config
    return bool(ai_config()["api_key"])
