"""Malay/English TikTok title → Russian Ozon listing (natural prose, no keyword spam)."""

from __future__ import annotations

import re

KEYWORDS = [
    ("pelekat dinding tumbuhan", "растительная наклейка на стену"),
    ("wall sticker", "Наклейка на стену"),
    ("wall decal", "Наклейка на стену"),
    ("plant wall sticker", "растительная наклейка"),
    ("monstera deliciosa", "листья монстеры Deliciosa"),
    ("leaf pattern", "листьевой узор"),
    ("corak daun", "листьевой узор"),
    ("3d terang", "яркий объёмный 3D"),
    ("3d vivid", "яркий объёмный 3D"),
    ("self-adhesive", "самоклеящаяся"),
    ("self adhesive", "самоклеящаяся"),
    ("pelekat dinding", "Наклейка на стену"),
    ("pelekat tingkap", "Наклейка на окно"),
    ("pelekat jubin", "Наклейка на плитку"),
    ("pelekat kaca", "Наклейка на стекло"),
    ("pelekat sendiri", "самоклеящаяся"),
    ("kalis lembapan", "влагостойкая"),
    ("kalis air", "водостойкая"),
    ("waterproof", "водостойкая"),
    ("boleh ditanggalkan", "съёмная"),
    ("boleh tanggal", "съёмная"),
    ("boleh alih", "съёмная"),
    ("removable", "съёмная"),
    ("gaya kontemporari", "современный стиль"),
    ("gaya moden", "современный стиль"),
    ("gaya eropah", "европейский стиль"),
    ("gaya eropa", "европейский стиль"),
    ("corak bunga", "цветочный узор"),
    ("wood grain", "древесный узор"),
    ("bijian kayu", "древесный узор"),
    ("kayu putih", "белый деревянный узор"),
    ("papan vintaj", "винтажная доска"),
    ("3d vinil", "объёмный 3D эффект"),
    ("geometric", "геометрический узор"),
    ("geometri", "геометрический узор"),
    ("merah jambu", "розовый"),
    ("rama-rama", "бабочки"),
    ("rama rama", "бабочки"),
    ("dandelion", "одуванчики"),
    ("mandala", "мандала"),
    ("tumbuhan", "растительный мотив"),
    ("monstera", "листья монстеры"),
    ("tropical", "тропический"),
    ("tropika", "тропический"),
    ("tropis", "тропический"),
    ("plant", "растительный мотив"),
    ("terang", "яркий реалистичный"),
    ("vivid", "яркий реалистичный"),
    ("faux", "эффект имитации"),
    ("tiruan", "эффект имитации"),
    ("decal", "декоративная наклейка"),
    ("sticker", "декоративная наклейка"),
    ("mudah pasang", "лёгкий монтаж"),
    ("bilik mandi", "ванная"),
    ("bilik tidur", "спальня"),
    ("ruang tamu", "гостиная"),
    ("living room", "гостиная"),
    ("bedroom", "спальня"),
    ("bathroom", "ванная"),
    ("marble", "мраморный узор"),
    ("marmer", "мраморный узор"),
    ("unicorn", "единорог"),
    ("angsa", "лебеди"),
    ("balet", "балет"),
    ("bunga", "цветы"),
    ("putih", "белый"),
    ("hijau", "зелёный"),
    ("green", "зелёный"),
    ("emas", "золотой"),
    ("perak", "серебристый"),
    ("besar", "большой"),
    ("keping", "шт"),
    ("vinil", "винил"),
    ("hiasan", "декор"),
    ("dapur", "кухня"),
    ("kitchen", "кухня"),
    ("daun", "листья"),
    ("leaf", "листья"),
    ("pasu", "горшок"),
    ("pokok", "растение"),
    ("tingkap", "окно"),
    ("dinding", "стена"),
    ("rumah", "дом"),
    ("love", "сердечки"),
    ("heart", "сердечки"),
    ("kucing", "кошки"),
    ("bulan", "луна"),
    ("bintang", "звёзды"),
    ("star", "звёзды"),
    ("cloud", "облака"),
    ("awan", "облака"),
    ("set", "набор"),
    ("3d", "объёмный 3D эффект"),
]

DESIGN_PHRASES: list[tuple[str, str]] = [
    (r"monstera\s+deliciosa", "листья монстеры Deliciosa"),
    (r"\b3\s*d\b", "объёмный 3D эффект"),
    (r"3\s*มิติ", "объёмный 3D эффект"),
    (r"sống\s+động", "яркий реалистичный эффект"),
    (r"corak\s+daun|leaf\s+pattern", "листьевой узор"),
    (r"tumbuhan|plant\s+(?:wall|sticker|decal)|cây\s+họa", "растительный мотив"),
    (r"terang|vivid|realistic|realistis", "яркий реалистичный эффект"),
    (r"faux|tiruan|imitation|simulasi", "эффект имитации"),
    (r"hijau|green\s+plant|tropical\s+plant", "зелёный растительный"),
    (r"butterfly|rama[\s-]?rama", "узор с бабочками"),
    (r"marble|marmer", "мраморный узор"),
    (r"wood\s+grain|kayu", "древесный узор"),
]

_TRAITS = frozenset({"самоклеящаяся", "водостойкая", "съёмная", "влагостойкая", "лёгкий монтаж"})
_COLORS = frozenset({"белый", "зелёный", "золотой", "розовый", "серебристый", "разноцветный"})
_ROOMS = frozenset({"кухня", "спальня", "гостиная", "ванная", "дом"})
_STYLES = frozenset({"современный стиль", "европейский стиль"})
_DESIGN = frozenset({
    "объёмный 3D эффект", "яркий объёмный 3D", "листья монстеры", "листья монстеры Deliciosa",
    "растительный мотив", "растительная наклейка", "растительная наклейка на стену",
    "листья", "листьевой узор", "эффект имитации", "яркий реалистичный", "яркий реалистичный эффект",
    "тропический", "зелёный растительный", "цветочный узор", "древесный узор", "мраморный узор",
    "геометрический узор", "бабочки", "одуванчики", "лебеди", "кошки", "единорог", "мандала",
    "монстера", "декоративная наклейка", "цветы", "растение",
})
_DESC_SKIP = frozenset({"декор", "стена", "окно", "шт", "набор", "большой", "винил", "лёгкий монтаж"})
_SHORT_WORD_KEYS = frozenset({"3d", "leaf", "star", "love", "heart", "set", "faux", "vinil", "plant"})

CATEGORY_TEMPLATES = {
    "default": {
        "category_id": 17027906,
        "type_id": 91971,
        "title_prefix": "Наклейка на стену",
        "color": ("разноцветный", 369939085),
        "kit": "1 штука",
        "weight": 140, "depth": 60, "width": 60, "height": 360,
        "len_cm": "90", "wid_cm": "45",
    }
}


def _keyword_in_title(ms: str, title_lower: str) -> bool:
    if ms in _SHORT_WORD_KEYS or (len(ms) <= 4 and ms.isascii()):
        return bool(re.search(r"\b" + re.escape(ms) + r"\b", title_lower))
    return ms in title_lower


def extract_design_motifs(title: str) -> list[str]:
    """从标题提取视觉/样式特征（3D、植物、 узор等）。"""
    t = (title or "").lower()
    out: list[str] = []
    seen: set[str] = set()
    for pattern, label in DESIGN_PHRASES:
        if re.search(pattern, t, re.IGNORECASE):
            key = label.lower()
            if key not in seen:
                seen.add(key)
                out.append(label)
    return out


def guess_keywords(title_ms: str) -> list[str]:
    t = (title_ms or "").lower()
    hits: list[str] = []
    seen: set[str] = set()
    for ms, ru in sorted(KEYWORDS, key=lambda x: -len(x[0])):
        if not _keyword_in_title(ms, t):
            continue
        key = ru.lower()
        if key in seen:
            continue
        seen.add(key)
        hits.append(ru)
    for label in extract_design_motifs(title_ms):
        key = label.lower()
        if key not in seen:
            seen.add(key)
            hits.append(label)
    return hits


def _infer_plant_color(title: str, colors: list[str]) -> list[str]:
    if colors:
        return colors
    t = (title or "").lower()
    if re.search(
        r"tumbuhan|monstera|plant|daun|leaf|pokok|cây|ต้นไม้|hijau|green",
        t,
        re.IGNORECASE,
    ):
        return ["зелёный"]
    return colors


def _split_hits(hits: list[str]) -> tuple[list[str], list[str], list[str], list[str], list[str], list[str]]:
    traits = [h for h in hits if h in _TRAITS]
    colors = [h for h in hits if h in _COLORS]
    rooms = [h for h in hits if h in _ROOMS]
    styles = [h for h in hits if h in _STYLES]
    designs = [h for h in hits if h in _DESIGN]
    patterns: list[str] = []
    seen: set[str] = set()
    for h in hits:
        if h in _TRAITS or h in _COLORS or h in _ROOMS or h in _STYLES or h in _DESC_SKIP:
            continue
        if h in _DESIGN:
            continue
        if h.startswith("Наклейка"):
            continue
        key = h.lower()
        if key in seen:
            continue
        seen.add(key)
        patterns.append(h)
    return traits, colors, rooms, styles, designs, patterns


def build_visual_style_ru(
    title: str,
    *,
    colors: list[str],
    designs: list[str],
    patterns: list[str],
) -> str:
    """合并为一句俄语视觉描述，供 DeepSeek 写入标题。"""
    colors = _infer_plant_color(title, colors)
    bits: list[str] = []
    seen: set[str] = set()
    for item in colors + designs + patterns:
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        bits.append(item)
    if not bits:
        return ""
    if len(bits) == 1:
        return bits[0]
    return ", ".join(bits[:5])


def draft_title(title_ms: str, offer_id: str, len_cm: str = "", wid_cm: str = "") -> str:
    from modules.ozon.listing_text import build_ozon_sticker_title

    tpl = CATEGORY_TEMPLATES["default"]
    hits = guess_keywords(title_ms)
    traits, colors, _rooms, _styles, designs, patterns = _split_hits(hits)
    visual = build_visual_style_ru(title_ms, colors=colors, designs=designs, patterns=patterns)
    title_hits = hits[:]
    if visual and visual not in title_hits:
        title_hits.insert(0, visual)
    return build_ozon_sticker_title(
        title_hits,
        len_cm=len_cm or tpl["len_cm"],
        wid_cm=wid_cm or tpl["wid_cm"],
    )


def _join_ru(items: list[str], conj: str = "и") -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {conj} {items[1]}"
    return ", ".join(items[:-1]) + f" {conj} " + items[-1]


def draft_description(
    title_ms: str,
    len_cm: str = "",
    wid_cm: str = "",
    kit: str = "1 штука",
) -> str:
    """从 TikTok 标题关键词生成较完整的俄语商品描述（规则模板，无 API）。"""
    hits = guess_keywords(title_ms)
    traits, colors, rooms, styles, designs, patterns = _split_hits(hits)
    colors = _infer_plant_color(title_ms, colors)
    product = next((h for h in hits if h.startswith("Наклейка")), "Декоративная самоклеящаяся плёнка")

    design_bits: list[str] = []
    if colors:
        design_bits.append(colors[0])
    design_bits.extend(designs[:3])
    design_bits.extend(patterns[:3])
    if styles:
        design_bits.append(styles[0])
    design = _join_ru(list(dict.fromkeys(design_bits))) if design_bits else "стильный декоративный узор"

    intro = (
        f"{product} с дизайном «{design}» — украшение для интерьера, "
        "которое освежит стены, мебель, двери или стекло без сложного ремонта."
    )

    trait_list = traits or ["самоклеящаяся"]
    extras: list[str] = []
    if "водостойкая" in trait_list or "влагостойкая" in trait_list:
        extras.append("не боится влаги и подойдёт для зон с повышенной влажностью")
    if "съёмная" in trait_list:
        extras.append("при необходимости аккуратно снимается без следов клея")
    if "лёгкий монтаж" in hits:
        extras.append("монтаж не требует специальных инструментов")
    extra_str = f" {'; '.join(extras)}." if extras else "."
    traits_text = ", ".join(dict.fromkeys(trait_list))
    features = f"Изделие {traits_text}, легко клеится на ровную чистую поверхность{extra_str}"

    if rooms:
        usage = f"Отлично подходит для оформления: {_join_ru(rooms[:5])}."
    else:
        usage = "Подходит для гостиной, спальни, кухни, прихожей и детской комнаты."

    a, b = (len_cm or "").strip(), (wid_cm or "").strip()
    if a and b:
        size = (
            f"Размер листа — {a}×{b} см; можно комбинировать несколько листов "
            "или подрезать материал ножницами под нужный участок."
        )
    elif a:
        size = f"Длина листа — {a} см; при необходимости наклейку можно подрезать ножницами."
    else:
        size = "Гибкий формат: плёнку можно подрезать ножницами под нужный размер и форму."

    install = (
        "Перед монтажом очистите и высушите поверхность, снимите защитную плёнку "
        "и разгладьте материал от центра к краям, выгоняя пузырьки воздуха."
    )
    footer = f"В комплекте: {kit or '1 штука'}. Материал — ПВХ (поливинилхлорид). Страна производства — Китай."

    return " ".join([intro, features, usage, size, install, footer])


def build_rule_context(
    title_ms: str,
    offer_id: str = "",
    *,
    len_cm: str = "",
    wid_cm: str = "",
    kit: str = "1 штука",
    variant_label: str = "",
    sku_name: str = "",
) -> dict:
    """从 TikTok 标题提取结构化特征 + 规则草稿，供 DeepSeek 作输入。"""
    tpl = CATEGORY_TEMPLATES["default"]
    len_cm = len_cm or tpl["len_cm"]
    wid_cm = wid_cm or tpl["wid_cm"]
    hits = guess_keywords(title_ms)
    traits, colors, rooms, styles, designs, patterns = _split_hits(hits)
    colors = _infer_plant_color(title_ms, colors)
    product_type = next((h for h in hits if h.startswith("Наклейка")), "Декоративная самоклеящаяся плёнка")
    visual_style = build_visual_style_ru(title_ms, colors=colors, designs=designs, patterns=patterns)

    return {
        "source_title": (title_ms or "").strip(),
        "sku_name": (sku_name or "").strip(),
        "keywords": hits,
        "traits": traits,
        "colors": colors,
        "patterns": patterns,
        "designs": designs,
        "visual_style": visual_style,
        "rooms": rooms,
        "styles": styles,
        "product_type": product_type,
        "len_cm": len_cm,
        "wid_cm": wid_cm,
        "kit": kit or tpl["kit"],
        "variant_label": (variant_label or "").strip(),
        "rule_title": draft_title(title_ms, offer_id, len_cm=len_cm, wid_cm=wid_cm),
        "rule_description": draft_description(title_ms, len_cm=len_cm, wid_cm=wid_cm, kit=kit or tpl["kit"]),
    }


def format_rule_context_for_prompt(ctx: dict | None) -> str:
    if not ctx:
        return ""
    lines = [
        "\n\n--- Разбор исходного названия (вспомогательно; основа — полное TikTok-название выше) ---",
    ]
    if ctx.get("sku_name"):
        lines.append(f"Название варианта SKU: {ctx['sku_name']}")
    if ctx.get("visual_style"):
        lines.append(f"Визуальный стиль (из названия): {ctx['visual_style']}")
    if ctx.get("product_type"):
        lines.append(f"Тип: {ctx['product_type']}")
    if ctx.get("designs"):
        lines.append(f"Дизайн / мотив: {', '.join(ctx['designs'])}")
    if ctx.get("traits"):
        lines.append(f"Свойства: {', '.join(ctx['traits'])}")
    if ctx.get("colors"):
        lines.append(f"Цвета: {', '.join(ctx['colors'])}")
    if ctx.get("patterns"):
        lines.append(f"Узоры: {', '.join(ctx['patterns'])}")
    if ctx.get("rooms"):
        lines.append(f"Комнаты: {', '.join(ctx['rooms'])}")
    if ctx.get("styles"):
        lines.append(f"Стиль интерьера: {', '.join(ctx['styles'])}")
    if ctx.get("len_cm") and ctx.get("wid_cm"):
        lines.append(f"Размер: {ctx['len_cm']}×{ctx['wid_cm']} см")
    if ctx.get("kit"):
        lines.append(f"Комплектация: {ctx['kit']}")
    if ctx.get("variant_label"):
        lines.append(f"Вариант SKU: {ctx['variant_label']}")
    if ctx.get("rule_title"):
        lines.append(
            f"\nЧерновик заголовка (по разбору названия, можно перефразировать, ≥60 символов):\n"
            f"{ctx['rule_title']}"
        )
    if ctx.get("rule_description"):
        lines.append(
            f"\nЧерновик описания (по разбору названия, можно перефразировать):\n"
            f"{ctx['rule_description']}"
        )
    return "\n".join(lines)
