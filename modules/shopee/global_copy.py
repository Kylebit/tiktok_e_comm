"""CNSC global English copy with a factual, reviewable fallback."""

from __future__ import annotations

import json
import re
import unicodedata

GLOBAL_TITLE_MAX = 120
GLOBAL_DESC_MAX = 3000
GLOBAL_DESC_TARGET = 2400
TOAPI_COPY_MODEL = "gpt-5.4-mini-official"

# 铺货时优先用 PH 英文作为母版（TH/VN 仅作最后回退）
TK_SOURCE_ORDER = ("PH", "MY", "TH", "VN")

_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\udfff]")
_VIET_RE = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđĐ]"
)
# 马来文同为拉丁字母，需词表排除，否则会被当成英文母版
_MALAY_HINT_RE = re.compile(
    r"\b(yang|untuk|dengan|adalah|kertas|dinding|pelekat|diri|reka|bentuk|"
    r"hiasan|rumah|mudah|dipakai|sesuai|pengubahsuaian|dan|atau|sebuah|"
    r"kepada|warna|saiz|bahan|produk|kualiti|penghantaran)\b",
    re.I,
)
_CANONICAL_MATERIAL_RE = re.compile(
    r"(?<![A-Z0-9])("
    r"PVC|PET|EVA|ABS|MDF|PU|PE|PP|"
    r"acrylic|ceramic|wood(?:en)?|bamboo|metal|iron|plastic|glass|"
    r"fabric|cotton|resin"
    r")(?![A-Z0-9])",
    re.I,
)
_DIMENSION_PAIR_RE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:x|×|\*)\s*"
    r"(\d+(?:\.\d+)?)\s*(cm|mm|m|in|inch|inches)\b",
    re.I,
)
_PACKAGE_DIMENSION_HINT_RE = re.compile(
    r"\b(package|packaging|parcel|shipping|carton|box)\b",
    re.I,
)
_PRODUCT_DIMENSION_HINT_RE = re.compile(
    r"\b(finished|listed|product|item|decal|sticker|overall|assembled)\s+"
    r"(?:product\s+)?(?:size|dimensions?)\b"
    r"|\b(?:size|dimensions?)\s*:",
    re.I,
)
_LABELED_QUANTITY_RE = re.compile(
    r"\bquantity(?:\s+per\s+pack)?\s*[:=-]\s*(\d+)\b",
    re.I,
)
_COUNTED_ITEM_RE = re.compile(
    r"(?<!\d)(\d+)\s*(?:pieces?|pcs?|items?|units?|decals?|stickers?)\b",
    re.I,
)
_VIETNAMESE_LANGUAGE_FEATURES = frozenset(
    "ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬĐÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊ"
    "ÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ"
    "àáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệìíỉĩị"
    "òóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ"
)


def contains_vietnamese_language_features(value: str) -> bool:
    """Return whether text has a Vietnamese-specific accented letter.

    NFC normalization makes decomposed tone marks match the explicit, narrow
    Vietnamese set without treating arbitrary Latin-1 letters as Vietnamese.
    """

    return any(
        char in _VIETNAMESE_LANGUAGE_FEATURES
        for char in unicodedata.normalize("NFC", str(value or ""))
    )

_SYSTEM = """You are a Shopee CNSC cross-border listing copywriter. Write in English ONLY.

Rules:
- NEVER output Thai, Vietnamese, Malay, Chinese, or any non-English script or wording.
- If the source listing is already English (Philippines TikTok Shop), REUSE its wording and keywords; expand the description, do not replace good phrases.
- If the source is Thai/Vietnamese/Malay or any non-English language, translate accurately to natural ecommerce English, then expand.
- Title: 80-120 characters, searchable, no emoji, no ALL CAPS blocks, no markdown, no bullet options, no preamble.
- Description: plain text, target 1800-2800 characters when source description is short or missing; otherwise expand to at least 1200. Include product type, material, size/dimensions if known, quantity, features, usage scenes, installation/care, shipping note.
- Never expose brand names or internal/source/platform identifiers in buyer-facing copy, including Seller SKU, SKU, item code, product code, Product ID, Item ID, Offer ID, or source ID.
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
    if _MALAY_HINT_RE.search(text):
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
    d = str(desc or "").replace("\r\n", "\n").replace("\r", "\n")
    internal_identity = re.compile(
        r"^\s*(?:[-*]\s*)?(?:source\s+)?(?:brand(?:\s+name)?|seller\s+sku|sku|"
        r"item\s+code|product\s+code|product\s+id|item\s+id|offer\s+id|source\s+id)\s*[:=-]",
        re.I,
    )
    d = "\n".join(
        re.sub(r"[ \t]+", " ", line).strip()
        for line in d.split("\n")
        if not internal_identity.match(line)
    )
    d = re.sub(
        r"\b(?:source\s+)?(?:brand(?:\s+name)?|seller\s+sku|sku|item\s+code|"
        r"product\s+code|product\s+id|item\s+id|offer\s+id|source\s+id)"
        r"\s*[:=-]\s*[^.\n;]+[.;]?",
        "",
        d,
        flags=re.I,
    )
    d = re.sub(r"\n{3,}", "\n\n", d).strip()
    if len(d) < 120:
        d = (
            d
            + " Quality home product for daily use. Easy to use and suitable for modern living spaces."
        )
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
    from modules.sourcing.image_suite_plan import chat_completions, message_content

    return message_content(
        chat_completions(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=TOAPI_COPY_MODEL,
            max_tokens=max_tokens,
            temperature=0.2,
        )
    ).strip()


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
    source = f"{title} {strip_html(detail.get('description') or '')}".lower()
    if any(token in source for token in ("wall sticker", "wall decal", "wall decor")):
        body = (
            "PRODUCT OVERVIEW\n"
            f"{title}. Add a clear decorative accent to a suitable wall or flat "
            "surface while keeping the product design easy to coordinate with "
            "the surrounding room.\n\n"
            "VERIFIED PRODUCT DETAILS\n"
            "Product type: decorative wall sticker / wall decal.\n"
            f"{('Material: PVC. ' if 'pvc' in source else '')}"
            "Use the product photos as the visual reference for the printed "
            "design and colour.\n"
        )
        if specs:
            body += specs + ".\n"
        body += (
            "\nSUGGESTED SPACES\n"
            "Suitable as a decorative accent for a living room, bedroom, study, "
            "entryway or another space with an appropriate application surface.\n\n"
            "APPLICATION GUIDANCE\n"
            "Plan the position before application. Apply carefully to a clean, "
            "dry and smooth surface, then press from the centre toward the edges. "
            "Surface texture and condition can affect the finished result.\n\n"
            "PLEASE NOTE\n"
            "Check the product images and dimensions before ordering. Screen "
            "settings can make colours look slightly different. Waterproof, "
            "removable and residue-free performance is not promised unless it "
            "is separately verified in the approved product facts."
        )
    else:
        body = (
            "PRODUCT OVERVIEW\n"
            f"{title}. This listing is prepared from the verified source product "
            "facts and images.\n\n"
            "VERIFIED DETAILS\n"
            f"{specs or 'Review the product images for the confirmed design and contents.'}\n\n"
            "USE AND CARE\n"
            "Use the product only for its intended purpose. Review the dimensions, "
            "selected option and product images before ordering.\n\n"
            "PLEASE NOTE\n"
            "Screen settings can make colours look slightly different. No "
            "performance claim is made unless it is present in the verified "
            "product facts."
        )
    return _clamp_description(body, model_sku)


def build_factual_english_description(
    detail: dict,
    model_sku: str,
    *,
    title: str = "",
) -> str:
    """Return a deterministic buyer-facing fallback without marketplace writes."""

    clean_title = _clamp_title(
        title or strip_html(detail.get("title") or ""),
        model_sku,
    )
    return _generic_english_description(detail, model_sku, clean_title)


def _normalized_fact_number(raw: str) -> str:
    value = str(raw or "").strip()
    if "." not in value:
        return value
    return value.rstrip("0").rstrip(".")


def _approved_copy_required_facts(
    english_title: str,
    english_description: str,
) -> dict:
    """Extract only durable facts that localized copy must preserve.

    Product dimensions are taken from the approved commercial title first, or
    from an explicitly product-labelled description line.  Generic package,
    parcel, carton and shipping dimensions are deliberately ignored.
    """

    title = str(english_title or "").strip()
    description = str(english_description or "").strip()
    source = f"{title}\n{description}"

    material_tokens = list(
        dict.fromkeys(
            match.group(1)
            for match in _CANONICAL_MATERIAL_RE.finditer(source)
        )
    )
    if not material_tokens:
        raise ValueError(
            "approved English copy is missing a canonical material token"
        )

    size_match = None
    if not _PACKAGE_DIMENSION_HINT_RE.search(title):
        size_match = _DIMENSION_PAIR_RE.search(title)
    if size_match is None:
        for line in description.splitlines():
            if _PACKAGE_DIMENSION_HINT_RE.search(line):
                continue
            if not _PRODUCT_DIMENSION_HINT_RE.search(line):
                continue
            size_match = _DIMENSION_PAIR_RE.search(line)
            if size_match is not None:
                break
    if size_match is None:
        raise ValueError(
            "approved English copy is missing explicit finished product dimensions"
        )

    quantity_match = _LABELED_QUANTITY_RE.search(source)
    if quantity_match is None:
        quantity_match = _COUNTED_ITEM_RE.search(source)
    facts = {
        "material_tokens": material_tokens,
        "finished_dimensions": [
            _normalized_fact_number(size_match.group(1)),
            _normalized_fact_number(size_match.group(2)),
        ],
        "dimension_unit": size_match.group(3).lower(),
    }
    if quantity_match is not None and int(quantity_match.group(1)) > 0:
        facts["quantity"] = int(quantity_match.group(1))
    return facts


def _localized_copy_preserves_required_facts(
    title: str,
    description: str,
    required_facts: dict,
) -> None:
    combined = f"{title}\n{description}"
    combined_casefold = combined.casefold()

    for material in required_facts["material_tokens"]:
        if material.casefold() not in combined_casefold:
            raise RuntimeError(
                f"Shopee localized copy lost required material {material}"
            )

    first, second = required_facts["finished_dimensions"]
    first_pattern = re.escape(first).replace(r"\.", r"[\.,]")
    second_pattern = re.escape(second).replace(r"\.", r"[\.,]")
    dimension_pattern = re.compile(
        rf"(?<!\d){first_pattern}\s*(?:x|×|\*)\s*"
        rf"{second_pattern}(?!\d)",
        re.I,
    )
    if not dimension_pattern.search(combined):
        raise RuntimeError(
            "Shopee localized copy lost required finished dimensions "
            f"{first} x {second}"
        )

    quantity = required_facts.get("quantity")
    if quantity is not None and not re.search(rf"(?<!\d){quantity}(?!\d)", combined):
        raise RuntimeError(
            f"Shopee localized copy lost required quantity {quantity}"
        )


_SEMANTIC_LINE_SPLIT_RE = re.compile(r"(?:\r?\n|[\u2022\u25cf\u25aa\u25e6])+")
_MAX_UNLOCALIZED_SEMANTIC_LINES = 24


def localized_semantic_line_matches(line: str, *, site: str) -> bool:
    """Accept a target-language line, or a dimension-only non-copy line."""

    value = str(line or "").strip()
    if not value:
        return False
    if site == "TH":
        has_target_language = any("\u0e00" <= char <= "\u0e7f" for char in value)
    elif site == "VN":
        has_target_language = contains_vietnamese_language_features(value)
    else:
        return False
    if has_target_language:
        return True
    dimension_only = re.sub(
        r"(?i)(?<=\d)\s*(?:x|\N{MULTIPLICATION SIGN})\s*(?=\d)|(?<=\d)\s*(?:cm|mm|m|in|inch|inches)\b",
        "",
        value,
    )
    return not re.search(r"[A-Za-z]", dimension_only)


def _localized_description_has_target_language(
    description: str, *, site: str
) -> bool:
    lines = [
        line.strip()
        for line in _SEMANTIC_LINE_SPLIT_RE.split(description)
        if line.strip()
    ]
    if not lines:
        return False
    return all(localized_semantic_line_matches(line, site=site) for line in lines)


def _line_has_target_language(line: str, *, site: str) -> bool:
    return localized_semantic_line_matches(line, site=site)


def _translate_unlocalized_semantic_lines(
    description: str,
    *,
    site: str,
    language: str,
    approved_facts: str,
) -> str:
    normalized = re.sub(r"([\u2022\u25cf\u25aa\u25e6])", r"\n\1", description)
    lines = normalized.splitlines()
    missing = [
        index
        for index, line in enumerate(lines)
        if line.strip() and not _line_has_target_language(line, site=site)
    ]
    if not missing:
        return description
    if len(missing) > _MAX_UNLOCALIZED_SEMANTIC_LINES:
        raise RuntimeError(
            f"Shopee {site} localized description has too many untranslated semantic lines"
        )
    system = f"""Translate one ecommerce description line into {language}.
Return exactly one plain-text line: no JSON, markdown fence, explanation or extra line.
The result must contain {language} characters. Preserve every number, dimension and
Latin material token present in the source line exactly. Do not add product facts."""
    for index in missing:
        translated = _ai_chat(
            system,
            "Approved English facts:\n"
            + approved_facts
            + "\n\nSource line:\n"
            + lines[index].strip(),
            max_tokens=180,
        ).strip().strip('"')
        if "\n" in translated or not _line_has_target_language(
            translated, site=site
        ):
            raise RuntimeError(
                f"Shopee {site} localized description failed semantic-line language validation"
            )
        lines[index] = translated
    corrected = "\n".join(lines).strip()
    if not _localized_description_has_target_language(corrected, site=site):
        raise RuntimeError(
            f"Shopee {site} localized description failed semantic-line language validation"
        )
    return corrected


def localize_shopee_copy(
    *,
    english_title: str,
    english_description: str,
    region: str,
) -> dict:
    """Repair a local CNSC item when Shopee auto-translation did not run.

    Normal publication still sends only the English global master. This helper
    only updates an existing item and never creates or publishes another one.
    """

    site = str(region or "").upper()
    if site in {"PH", "MY"}:
        return {
            "title": str(english_title or "").strip(),
            "description": str(english_description or "").strip(),
            "region": site,
            "provider": "english_global_master",
            "model": None,
        }
    language = {"TH": "Thai", "VN": "Vietnamese"}.get(site)
    if not language:
        raise ValueError(f"unsupported Shopee localization region {site}")
    required_facts = _approved_copy_required_facts(
        english_title,
        english_description,
    )
    quantity_instruction = (
        f"Preserve the approved quantity {required_facts['quantity']} exactly."
        if "quantity" in required_facts
        else "The approved copy has no explicit quantity; do not invent a quantity or package count."
    )
    system = f"""You localize approved Shopee cross-border product copy into natural {language}.
Preserve every supplied product fact exactly and never invent claims.
Return ONLY JSON with keys title and description.
Title: 60-115 characters, natural ecommerce language for {site}, searchable, no emoji.
Description: 500-1800 characters, plain text with clear section headings and line breaks.
Preserve all approved materials, dimensions, package contents and application guidance.
{quantity_instruction}
Keep each source material token exactly as supplied in Latin characters somewhere
in the description, even when the surrounding copy is localized.
Every non-empty description semantic line must contain {language} characters.
Never emit an English-only heading, label, bullet or sentence. Keep any Latin
material or dimension token on the same line as its {language} label.
Do not add waterproof, removable, residue-free, reusable, durability, certification,
warranty, medical, safety or performance claims. Do not include a seller SKU."""
    source_user = (
        f"Approved English title:\n{str(english_title or '').strip()}\n\n"
        "Approved English description:\n"
        f"{str(english_description or '').strip()}"
    )
    raw = _ai_chat(system, source_user, max_tokens=1400)
    for attempt in range(2):
        parsed = _parse_ai_json(raw)
        title = re.sub(r"\s+", " ", str(parsed.get("title") or "")).strip()
        description = str(parsed.get("description") or "").strip()
        if not (60 <= len(title) <= GLOBAL_TITLE_MAX):
            raise RuntimeError(
                f"Shopee {site} localized title length is invalid: {len(title)}"
            )
        if not (500 <= len(description) <= GLOBAL_DESC_MAX):
            raise RuntimeError(
                f"Shopee {site} localized description length is invalid: "
                f"{len(description)}"
            )
        if site == "TH":
            title_language_ok = any("\u0e00" <= char <= "\u0e7f" for char in title)
        else:
            title_language_ok = contains_vietnamese_language_features(title)
        if not title_language_ok:
            raise RuntimeError(f"Shopee {site} localized title failed language validation")
        if _localized_description_has_target_language(description, site=site):
            _localized_copy_preserves_required_facts(
                title,
                description,
                required_facts,
            )
            return {
                "title": title,
                "description": description,
                "region": site,
                "provider": "toapi",
                "model": TOAPI_COPY_MODEL,
            }
        if attempt:
            description = _translate_unlocalized_semantic_lines(
                description,
                site=site,
                language=language,
                approved_facts=source_user,
            )
            _localized_copy_preserves_required_facts(
                title,
                description,
                required_facts,
            )
            return {
                "title": title,
                "description": description,
                "region": site,
                "provider": "toapi",
                "model": TOAPI_COPY_MODEL,
            }
        raw = _ai_chat(
            system
            + "\nCORRECTION: Rewrite the first draft. Every non-empty description "
            "semantic line must contain the target language; preserve all approved facts.",
            source_user
            + "\n\nFirst localized draft to correct:\n"
            + json.dumps({"title": title, "description": description}, ensure_ascii=False),
            max_tokens=1400,
        )
    raise AssertionError("unreachable localized copy correction state")


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
        user = (
            f"Seller SKU / match code: {model_sku}\n"
            f"TikTok source region: {source_region or 'unknown'}\n"
            f"Source already English (PH): {'yes' if ph_english else 'no'}\n"
            "IMPORTANT: Output English only. Never copy Thai/Vietnamese/Malay text.\n\n"
            f"Source title:\n{title_src[:500]}\n\n"
            f"Source description:\n{desc_src[:3500] or '(empty — expand from title and specs)'}\n\n"
            f"Extra product specs:\n{_extra_specs(detail) or '(none)'}\n"
        )
        raw = _ai_chat(_SYSTEM, user, max_tokens=2200)
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
    if ph_english and len(description) < 500:
        description = ""

    if not title:
        if ph_english and is_english_listing_text(title_src):
            title = title_src
        else:
            title = _generic_english_title(detail, model_sku, title_src)
    if not description or len(description) < 500:
        if (
            ph_english
            and len(desc_src) >= 500
            and is_english_listing_text(desc_src)
        ):
            description = desc_src
        else:
            description = _generic_english_description(detail, model_sku, title)

    title = _clamp_title(title, model_sku)
    description = _clamp_description(description, model_sku)
    if not is_english_listing_text(title):
        title = _clamp_title(f"Home Decor Product SKU {model_sku}", model_sku)
    if not is_english_listing_text(description):
        description = _clamp_description(
            f"{title}. Quality home product for daily use.",
            model_sku,
        )

    return {
        "title": title,
        "description": description,
        "source_region": source_region.upper(),
        "used_ph_english": ph_english,
    }
