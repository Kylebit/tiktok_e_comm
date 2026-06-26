"""TikTok MX 店 Listing 文案：英文母版 → 西班牙语（墨西哥）。"""
from __future__ import annotations

import json
import re
from typing import Any

TITLE_MAX = 255

_SYSTEM = """Eres redactor de listings para TikTok Shop México. Escribe SOLO en español (México).

Reglas:
- Nunca dejes título o descripción en inglés.
- Título: ≤255 caracteres, buscable, sin emoji, sin MAYÚSCULAS completas, sin markdown.
- Descripción: HTML con <p> y <ul><li>; ≤1800 caracteres; incluye material, tamaño, uso y contenido del paquete si consta en la fuente.
- No inventes especificaciones que no estén en la fuente.
- Traduce también nombres de variantes (talla, estilo, color) al español natural.

Salida: JSON válido únicamente con claves:
{
  "title": "...",
  "description_html": "<p>...</p><ul><li>...</li></ul>",
  "sku_properties": [{"attr_name": "Tamaño", "attr_values": ["..."]}]
}
sku_properties puede ser [] si no hay variantes en la fuente."""


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _parse_json(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise RuntimeError(f"AI 未返回有效 JSON: {text[:120]!r}")
    return json.loads(m.group(0))


def _sku_property_hints(product: dict) -> list[dict]:
    hints: list[dict] = []
    for prop in product.get("sales_attributes") or []:
        if not isinstance(prop, dict):
            continue
        hints.append(
            {
                "name": prop.get("name") or "",
                "value": prop.get("value_name") or "",
            }
        )
    # 采集箱常见结构：从 skus 汇总
    seen: set[tuple[str, str]] = set()
    for sku in product.get("skus") or []:
        for attr in sku.get("sales_attributes") or []:
            name = str(attr.get("name") or "").strip()
            value = str(attr.get("value_name") or "").strip()
            key = (name, value)
            if key in seen or not value:
                continue
            seen.add(key)
            hints.append({"name": name, "value": value})
    return hints


def _collect_site_property_hints(info: dict) -> list[dict]:
    out: list[dict] = []
    for prop in info.get("skuPropertyList") or []:
        name = str(prop.get("attrName") or "").strip()
        for val in prop.get("attrValueList") or []:
            label = str(val.get("attrValue") or "").strip()
            if label:
                out.append({"name": name, "value": label})
    return out


def build_mx_spanish_copy(
    product: dict,
    *,
    seller_sku: str = "",
    site_collect_info: dict | None = None,
) -> dict[str, Any]:
    """生成 MX 西班牙语标题、描述 HTML、规格翻译。"""
    title_src = _strip_html(product.get("title") or "")
    desc_src = _strip_html(product.get("description") or "")
    if len(desc_src) < 80:
        desc_src = f"{title_src}. {desc_src}".strip()

    variant_hints = _collect_site_property_hints(site_collect_info or {})
    if not variant_hints:
        variant_hints = _sku_property_hints(product)

    from core.config import load_settings
    from core.llm import ai_config, chat_completion

    cfg = ai_config()
    if not cfg.get("api_key"):
        raise RuntimeError("未配置 AI API Key，无法生成西班牙语文案")

    listing_tokens = int((load_settings().get("ai") or {}).get("listing_max_tokens") or 1200)
    user = (
        f"Seller SKU / código: {seller_sku}\n\n"
        f"Título fuente (inglés u otro idioma):\n{title_src[:500]}\n\n"
        f"Descripción fuente:\n{desc_src[:3500] or '(vacía)'}\n\n"
        f"Variantes / atributos:\n{json.dumps(variant_hints, ensure_ascii=False)}\n"
    )
    raw = chat_completion(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        max_tokens=max(listing_tokens, 1500),
        temperature=0.35,
    )
    parsed = _parse_json(raw)
    title = str(parsed.get("title") or "").strip()
    description_html = str(parsed.get("description_html") or "").strip()
    if len(title) < 10:
        raise RuntimeError(f"西班牙语标题过短: {title!r}")
    if len(title) > TITLE_MAX:
        title = title[:TITLE_MAX].rstrip()
    if len(description_html) < 30:
        raise RuntimeError("西班牙语描述过短")
    return {
        "title": title,
        "description_html": description_html,
        "sku_properties": parsed.get("sku_properties") or [],
    }


def spanish_notes(description_html: str, good_urls: list[str]) -> str:
    """西语描述 + 主图（保留 TikTok 兼容 HTML）。"""
    body = (description_html or "").strip()
    imgs = "".join(f'<img src="{u}">' for u in good_urls[:6])
    if body and imgs:
        return f"<div>{body}{imgs}</div>"
    if body:
        return f"<div>{body}</div>"
    return f"<div>{imgs}</div>" if imgs else "<div></div>"


def product_context_for_spanish(
    master_product: dict | None,
    site_collect_info: dict | None,
) -> dict:
    """TikTok API 不可用时，用采集箱已有英文标题作翻译源。"""
    if isinstance(master_product, dict) and master_product.get("title"):
        return master_product
    info = site_collect_info or {}
    title = str(info.get("title") or "").strip()
    notes = str(info.get("notes") or "")
    desc = _strip_html(notes)
    return {"title": title, "description": desc, "skus": []}


def apply_mx_spanish_listing(
    info: dict,
    product: dict | None,
    *,
    good_urls: list[str],
    seller_sku: str = "",
) -> dict:
    """写入 shopCollectItemInfo 的西语标题、描述与规格名。"""
    ctx = product_context_for_spanish(product, info)
    copy = build_mx_spanish_copy(ctx, seller_sku=seller_sku, site_collect_info=info)
    info["title"] = copy["title"]
    info["notes"] = spanish_notes(copy["description_html"], good_urls)

    # 按顺序匹配 skuPropertyList 规格值翻译
    props = copy.get("sku_properties") or []
    site_props = info.get("skuPropertyList") or []
    for i, prop in enumerate(site_props):
        if i >= len(props):
            break
        translated = props[i] if isinstance(props[i], dict) else {}
        if translated.get("attr_name"):
            prop["attrName"] = str(translated["attr_name"]).strip()
        tvals = translated.get("attr_values") or []
        for j, val in enumerate(prop.get("attrValueList") or []):
            if j < len(tvals) and tvals[j]:
                val["attrValue"] = str(tvals[j]).strip()
    return info
