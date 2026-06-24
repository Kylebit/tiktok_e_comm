"""Ozon 文案清洗：纯俄语、禁止搜索词堆砌、禁止 «оригинал» 表述。"""

from __future__ import annotations

import re

_HASHTAG_RE = re.compile(r"#\w+", re.UNICODE)
_LATIN_RE = re.compile(r"[a-zA-Z]+")
_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")
_ORIGINAL_WORD_RE = re.compile(
    r"\b(original|authentic|genuine|official|vintage|vintaj|sticker|wallpaper|"
    r"оригинал\w*|подлинн\w*|фирменн\w*|лиценз\w*|"
    r"100\s*%\s*original)\b",
    re.IGNORECASE | re.UNICODE,
)
_KEYWORD_SECTION_RE = re.compile(
    r"^\s*(особенности|где\s+использовать|как\s+(?:клеить|использовать))\s*:?\s*$",
    re.IGNORECASE | re.UNICODE,
)
_KEYWORD_SECTION_INLINE_RE = re.compile(
    r"особенности\s*\(\s*по\s+ключев\w*\s+слов\w*\s*\)\s*:?",
    re.IGNORECASE | re.UNICODE,
)
_SOURCE_TITLE_LINE_RE = re.compile(
    r"^\s*(оригинальное\s+название|original\s+title|название\s*\(\s*ms\s*\))\s*[:(]",
    re.IGNORECASE | re.UNICODE,
)
_DRAFT_HEADER_RE = re.compile(
    r"^\s*черновик\s+(описания|заголовка)\b.*$",
    re.IGNORECASE | re.UNICODE,
)
_DRAFT_TITLE_SUFFIX_RE = re.compile(
    r"\s*\(\s*черновик[^)]*\)\s*",
    re.IGNORECASE | re.UNICODE,
)
_SEE_ORIGINAL_RE = re.compile(
    r"см\.\s*оригинал\w*|see\s+original",
    re.IGNORECASE | re.UNICODE,
)
_BULLET_LINE_RE = re.compile(r"^\s*[—\-•]\s*(.+)$")

OZON_TITLE_MIN_LEN = 60
OZON_TITLE_TARGET_LEN = 78
OZON_TITLE_MAX_LEN = 200

_TRAIT_WORDS = frozenset({"самоклеящаяся", "съёмная", "водостойкая"})
_COLOR_WORDS = frozenset({"белый", "зелёный", "золотой", "розовый", "серебристый", "разноцветный"})
_PATTERN_SKIP = frozenset({"винил", "декор", "стена", "окно", "имитация", "шт", "набор"})
_TITLE_FILLERS = (
    "для стен и мебели",
    "интерьерная декоративная плёнка",
    "декоративная отделка",
)
_TABLECLOTH_HINTS = (
    "tablecloth",
    "table cloth",
    "table cover",
    "tablecloths",
    "скатерть",
    "taplak meja",
    "meja cover",
)


def is_tablecloth_title(title: str) -> bool:
    t = (title or "").lower()
    return any(h in t for h in _TABLECLOTH_HINTS)


def _strip_original_words(text: str) -> str:
    return _ORIGINAL_WORD_RE.sub("", text)


def _strip_latin(text: str) -> str:
    return _LATIN_RE.sub("", text)


def _clean_spaces(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" \n,—")


def _size_label(len_cm: str = "", wid_cm: str = "") -> str:
    a = (len_cm or "").strip()
    b = (wid_cm or "").strip()
    if a and b:
        return f"{a}×{b} см"
    if a:
        return f"{a} см"
    if b:
        return f"{b} см"
    return ""


def _dedupe_title_segments(text: str) -> str:
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(part)
    return ", ".join(out)


def _normalize_title_sizes(text: str, *, len_cm: str = "", wid_cm: str = "") -> str:
    t = _clean_spaces(text)
    t = re.sub(r",?\s*\b\d{4,}\s*см\b", "", t)
    correct = _size_label(len_cm, wid_cm)
    if correct:
        t = re.sub(r"\d+\s*[×xX]\s*\d+\s*см", correct, t)
        if correct not in t:
            pass  # ensure_ozon_title_length will append
    return _clean_spaces(t)


def ensure_ozon_title_length(
    title: str,
    *,
    len_cm: str = "",
    wid_cm: str = "",
) -> str:
    """标题 ≥60 字符、≤200，去重复词。"""
    t = _normalize_title_sizes(_dedupe_title_segments(_clean_spaces(title)), len_cm=len_cm, wid_cm=wid_cm)
    t = re.sub(r"(?i)\bсамоклеящаяся\b(?:,\s*(?=\bсамоклеящаяся\b))+", "самоклеящаяся", t)
    t = re.sub(r"(,\s*ПВХ)+", ", ПВХ", t, flags=re.IGNORECASE)

    size = _size_label(len_cm, wid_cm)
    if size and size not in t:
        if ", ПВХ" in t:
            t = t.replace(", ПВХ", f", {size}, ПВХ", 1)
        else:
            t = f"{t}, {size}, ПВХ"

    for filler in _TITLE_FILLERS:
        if len(t) >= OZON_TITLE_TARGET_LEN:
            break
        if filler.lower() in t.lower():
            continue
        if ", ПВХ" in t:
            t = t.replace(", ПВХ", f", {filler}, ПВХ", 1)
        else:
            t = f"{t}, {filler}"

    if len(t) < OZON_TITLE_MIN_LEN:
        t = f"{t}, интерьерный декор"

    return t[:OZON_TITLE_MAX_LEN]


def build_ozon_sticker_title(
    hits: list[str],
    *,
    len_cm: str = "",
    wid_cm: str = "",
) -> str:
    """Ozon 贴纸：类型 + 颜色/图案 + 尺寸 + 材质（60–100 字符）。"""
    traits = [h for h in hits if h.lower() in _TRAIT_WORDS]
    deco = [
        h for h in hits
        if h.lower() not in _TRAIT_WORDS
        and h.lower() not in _PATTERN_SKIP
        and "Наклейка" not in h
        and "/" not in h
        and len(h) > 2
    ]
    color = next((h for h in deco if h.lower() in _COLOR_WORDS), "")
    patterns = [h for h in deco if h.lower() not in _COLOR_WORDS]
    pattern = max(patterns, key=len) if patterns else ""

    attrs: list[str] = []
    if color and pattern:
        if color.lower() in pattern.lower():
            attrs.append(pattern)
        else:
            attrs.append(f"{color} {pattern}")
    elif color:
        attrs.append(color)
    elif pattern:
        attrs.append(pattern)

    if "съёмная" in traits and "съёмная" not in " ".join(attrs).lower():
        attrs.append("съёмная")
    if "водостойкая" in traits and "водостойкая" not in " ".join(attrs).lower():
        attrs.append("водостойкая")
    if "влагостойкая" in traits and "влагостойкая" not in " ".join(attrs).lower():
        attrs.append("влагостойкая")

    size = _size_label(len_cm, wid_cm)
    segments = ["Самоклеящаяся наклейка на стену"]
    if attrs:
        segments.append(", ".join(attrs))
    if size:
        segments.append(size)
    segments.append("ПВХ")
    return ensure_ozon_title_length(", ".join(segments), len_cm=len_cm, wid_cm=wid_cm)


def _simplify_keyword_title(text: str, *, len_cm: str = "", wid_cm: str = "") -> str:
    t = _clean_spaces(text)
    if t.count(",") < 3:
        return ensure_ozon_title_length(t, len_cm=len_cm, wid_cm=wid_cm)
    parts = [p.strip() for p in t.split(",") if p.strip()]
    hits = [
        p for p in parts
        if p.lower() not in ("пвх",) and "плёнка пвх" not in p.lower() and "гостин" not in p.lower()
    ]
    return build_ozon_sticker_title(hits, len_cm=len_cm, wid_cm=wid_cm)


def _bullets_to_prose(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    bullets: list[str] = []
    prose_lines: list[str] = []
    for line in lines:
        m = _BULLET_LINE_RE.match(line.strip())
        if m:
            bullets.append(m.group(1).strip(" ."))
        else:
            prose_lines.append(line)
    if not bullets:
        return text
    feat = ", ".join(bullets[:8])
    intro = _clean_spaces("\n".join(prose_lines))
    sentence = f"Изделие: {feat}."
    if intro:
        return f"{intro}\n\n{sentence}".strip()
    return sentence


def polish_variant_label(label: str) -> str:
    raw = (label or "").strip()
    if not raw:
        return ""
    if _LATIN_RE.search(raw) and not _CYRILLIC_RE.search(raw):
        return ""
    cleaned = _strip_latin(_strip_original_words(raw))
    return _clean_spaces(cleaned)


def _is_keyword_spam_title(text: str) -> bool:
    t = _clean_spaces(text)
    if _DRAFT_TITLE_SUFFIX_RE.search(t):
        return True
    if _ORIGINAL_WORD_RE.search(t):
        return True
    # 已是规范 Ozon 标题（含尺寸、够长）→ 不要重写成短标题
    if len(t) >= OZON_TITLE_MIN_LEN and re.search(r"\d+\s*[×xX]\s*\d+", t):
        if t.startswith(("Самоклеящаяся", "Наклейка", "Декоратив", "Обои", "Стеклянный")):
            return False
    parts = [p.strip() for p in t.split(",") if p.strip()]
    if len(parts) < 6:
        return False
    short = sum(
        1 for p in parts
        if len(p) < 12 and p.lower() not in ("пвх",) and not re.search(r"\d+\s*[×xX]\s*\d+", p)
    )
    return short >= 4


def sanitize_ozon_title(text: str, *, len_cm: str = "", wid_cm: str = "") -> str:
    if not text:
        return build_ozon_sticker_title([], len_cm=len_cm, wid_cm=wid_cm)
    t = _DRAFT_TITLE_SUFFIX_RE.sub(" ", text)
    t = _strip_original_words(t)
    if _is_keyword_spam_title(t):
        t = _strip_latin(t)
        t = _simplify_keyword_title(t, len_cm=len_cm, wid_cm=wid_cm)
    else:
        t = re.sub(r"\b3[Dd]\b", "объёмный", t)
        t = _strip_latin(t)
        t = _clean_spaces(t)
        if len(t) < OZON_TITLE_MIN_LEN or not _CYRILLIC_RE.search(t):
            return build_ozon_sticker_title([], len_cm=len_cm, wid_cm=wid_cm)
        t = ensure_ozon_title_length(t, len_cm=len_cm, wid_cm=wid_cm)
    return t


def sanitize_ozon_description(text: str) -> str:
    if not text:
        return (
            "Декоративная плёнка для оформления стен и мебели. "
            "Материал самоклеящаяся, монтаж простой. "
            "В комплекте одна наклейка. Материал ПВХ. Страна производства Китай."
        )
    out: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        raw = line.strip()
        if not raw:
            continue
        if _DRAFT_HEADER_RE.match(raw):
            continue
        if _SOURCE_TITLE_LINE_RE.match(raw):
            continue
        if _KEYWORD_SECTION_RE.match(raw):
            continue
        if _SEE_ORIGINAL_RE.search(raw):
            continue
        cleaned = _HASHTAG_RE.sub("", raw)
        cleaned = _KEYWORD_SECTION_INLINE_RE.sub("", cleaned)
        cleaned = _strip_original_words(cleaned)
        cleaned = _strip_latin(cleaned)
        cleaned = _clean_spaces(cleaned)
        if not cleaned or cleaned.lower().startswith("hashtag"):
            continue
        out.append(cleaned)
    body = _bullets_to_prose("\n".join(out))
    body = _clean_spaces(body)
    if len(body) < 40 or not _CYRILLIC_RE.search(body):
        return (
            "Декоративная плёнка для оформления стен и мебели. "
            "Материал самоклеящаяся, монтаж простой. "
            "В комплекте одна наклейка. Материал ПВХ. Страна производства Китай."
        )
    return body


def _fix_ai_product_terms(text: str, *, migrate_profile: str = "") -> str:
    t = (text or "").strip()
    if migrate_profile == "sticker":
        t = re.sub(r"(?i)самоклеящиеся\s+обои", "Самоклеящаяся наклейка на стену", t)
        t = re.sub(r"(?i)\bобои\b", "наклейка на стену", t)
    elif migrate_profile == "tablecloth":
        t = re.sub(r"(?i)наклейк\w*", "скатерть", t)
    return t


def polish_ozon_title(
    text: str,
    *,
    len_cm: str = "",
    wid_cm: str = "",
    migrate_profile: str = "",
) -> str:
    return sanitize_ozon_title(
        _fix_ai_product_terms(text, migrate_profile=migrate_profile),
        len_cm=len_cm,
        wid_cm=wid_cm,
    )


def polish_ozon_description(text: str) -> str:
    return sanitize_ozon_description(text)


def tablecloth_hashtags() -> str:
    return "#скатерть #декордлядома #интерьер"
