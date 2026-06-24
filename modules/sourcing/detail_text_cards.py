"""详情页文字卡：模板合成 v2（Pillow + AI 白底产品图 + 短文案）。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from core.config import ROOT
from modules.sourcing.pipeline import (
    _load_existing_assets,
    _public_url,
    load_draft,
    load_scrape,
    offer_dir,
)

MANIFEST_NAME = "detail_text_cards.json"
OUT_DIR_NAME = "detail_text_cards"
DESIGN_VERSION = "v2"

CARD_SIZE = (800, 800)
WIDE_SIZE = (800, 520)

# 暖色电商详情风（避免 v1 那种 PPT 蓝条）
THEME = {
    "bg": (250, 248, 245),
    "panel": (255, 255, 255),
    "ink": (28, 28, 30),
    "sub": (110, 110, 115),
    "line": (230, 228, 224),
    "accent": (196, 92, 62),
    "accent_soft": (255, 241, 235),
    "check": (47, 125, 84),
}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        ("/System/Library/Fonts/SFNS.ttf", None),
        ("/System/Library/Fonts/Helvetica.ttc", 1 if bold else 0),
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf", None),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", None),
    ]
    for path, idx in candidates:
        if not Path(path).is_file():
            continue
        try:
            if idx is not None:
                return ImageFont.truetype(path, size, index=idx)
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _parse_bullets(html: str, limit: int = 5) -> list[str]:
    items = re.findall(r"<li[^>]*>(.*?)</li>", html or "", flags=re.I | re.S)
    out: list[str] = []
    for raw in items:
        text = re.sub(r"<[^>]+>", "", raw)
        text = re.sub(r"\s+", " ", text).strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _shorten(text: str, max_len: int = 52) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1].rsplit(" ", 1)[0]
    return (cut or text[: max_len - 1]) + "…"


def _color_list_en(attrs: dict) -> str:
    raw = (attrs.get("颜色") or "").strip()
    mapping = {
        "鳄鱼": "Crocodile",
        "长颈鹿": "Giraffe",
        "火烈鸟": "Flamingo",
        "黄牛": "Yellow Cow",
        "紫牛": "Purple Cow",
        "白企鹅": "White Penguin",
    }
    parts = [mapping.get(p.strip(), p.strip()) for p in raw.split(",") if p.strip()]
    return ", ".join(parts[:4]) + ("…" if len(parts) > 4 else "")


def build_snippets(*, locale: str, copy: dict, attrs: dict) -> dict:
    loc = (locale or "en").lower()
    material = attrs.get("材质") or ""
    if material == "棉麻":
        material_en = "Cotton Linen"
    else:
        material_en = material or "Cotton Linen"
    colors = _color_list_en(attrs)

    if loc in ("ru", "ozon"):
        return {
            "locale": "ru",
            "features_title": "Почему выбирают",
            "specs_title": "Параметры",
            "scene_title": "Детская комната",
            "scene_caption": "Настенный органайзер для книг и мелочей",
            "steps_title": "3 шага",
            "steps": [
                "Повесьте на дверь или стену",
                "Разложите вещи по карманам",
                "Поддерживайте порядок",
            ],
            "bullets": [
                "Мультяшный дизайн для детей",
                f"Материал: {material or 'хлопок и лен'}",
                "Несколько карманов",
                "6 вариантов расцветки",
            ],
            "specs": [
                ("Материал", material or "Хлопок и лен"),
                ("Стиль", "Объёмный мультяшный"),
                ("Место", "Спальня / дверь"),
                ("Подходит", "Книги, журналы"),
                ("Дизайны", "6 вариантов"),
            ],
        }

    block = (copy.get("shopee") or {}) if copy else {}
    if not block and copy:
        block = (copy.get("tiktok") or {}).get("MY") or {}
    raw_bullets = _parse_bullets(block.get("description_html") or "", 4)

    # 卡片用短句，不用 listing 长段落
    default_bullets = [
        "Soft cotton linen · durable daily use",
        "6 cute cartoon animal designs",
        "Multi-pocket layered storage",
        "Hangs on door or wall easily",
    ]
    bullets = []
    for i, b in enumerate(raw_bullets[:4]):
        if ":" in b and len(b) > 40:
            bullets.append(_shorten(b.split(":", 1)[-1].strip(), 48))
        else:
            bullets.append(_shorten(b, 48))
    while len(bullets) < 4:
        bullets.append(default_bullets[len(bullets)])

    return {
        "locale": "en",
        "features_title": "Why You'll Love It",
        "specs_title": "Product Details",
        "scene_title": "Kids Room Essential",
        "scene_caption": "Organize books & daily items · Saves space",
        "steps_title": "3 Easy Steps",
        "steps": [
            "Hook on door or nail on wall",
            "Sort books into each pocket",
            "Keep essentials within reach",
        ],
        "bullets": bullets[:4],
        "specs": [
            ("Material", material_en),
            ("Style", "3D cartoon · layered"),
            ("Use", "Bedroom · door / wall"),
            ("Holds", "Books · magazines · cards"),
            ("Designs", colors or "6 options"),
        ],
    }


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        test = f"{cur} {w}"
        if draw.textlength(test, font=font) <= max_width:
            cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _load_image(path: Path | None) -> Image.Image | None:
    if not path or not path.is_file():
        return None
    try:
        return Image.open(path).convert("RGBA")
    except OSError:
        return None


def _fit_cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    iw, ih = img.size
    tw, th = size
    scale = max(tw / iw, th / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def _blurred_bg(source: Path | None, size: tuple[int, int], tint: tuple[int, int, int] = THEME["bg"]) -> Image.Image:
    base = Image.new("RGB", size, tint)
    img = _load_image(source)
    if not img:
        return base
    cover = _fit_cover(img.convert("RGB"), size)
    blurred = cover.filter(ImageFilter.GaussianBlur(radius=28))
    # 叠一层暖色蒙版，降低 supplier 图杂乱感
    wash = Image.new("RGB", size, (252, 248, 244))
    return Image.blend(blurred, wash, alpha=0.55)


def _paste_product(
    base: Image.Image,
    product_path: Path | None,
    box: tuple[int, int, int, int],
    *,
    shadow: bool = True,
) -> None:
    prod = _load_image(product_path)
    if not prod:
        return
    x0, y0, x1, y1 = box
    max_w, max_h = x1 - x0, y1 - y0
    prod.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
    px = x0 + (max_w - prod.width) // 2
    py = y1 - prod.height
    if shadow:
        sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        cx = px + prod.width // 2
        sd.ellipse((cx - prod.width // 3, y1 - 18, cx + prod.width // 3, y1 + 8), fill=(0, 0, 0, 38))
        base.paste(Image.alpha_composite(base.convert("RGBA"), sh).convert("RGB"), (0, 0))
    base.paste(prod, (px, py), prod)


def _draw_check(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    draw.ellipse((x, y, x + 22, y + 22), fill=THEME["check"])
    draw.line([(x + 6, y + 11), (x + 10, y + 15), (x + 16, y + 7)], fill=(255, 255, 255), width=2)


def _frosted_panel(base: Image.Image, rect: tuple[int, int, int, int], radius: int = 24) -> None:
    x0, y0, x1, y1 = rect
    region = base.crop(rect).filter(ImageFilter.GaussianBlur(radius=12))
    overlay = Image.new("RGBA", (x1 - x0, y1 - y0), (255, 255, 255, 215))
    region = Image.alpha_composite(region.convert("RGBA"), overlay)
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, x1 - x0, y1 - y0), radius=radius, fill=255)
    base.paste(region.convert("RGB"), (x0, y0), mask)


def render_features_card(
    snippets: dict,
    *,
    product_path: Path | None = None,
    bg_path: Path | None = None,
    size: tuple[int, int] = CARD_SIZE,
) -> Image.Image:
    img = _blurred_bg(bg_path, size)
    _paste_product(img, product_path, (120, 80, size[0] - 120, 460), shadow=True)

    panel_y = 480
    _frosted_panel(img, (32, panel_y, size[0] - 32, size[1] - 32), radius=20)
    draw = ImageDraw.Draw(img)
    title = snippets.get("features_title") or "Highlights"
    draw.text((56, panel_y + 28), title, fill=THEME["ink"], font=_font(30, True))

    y = panel_y + 78
    body = _font(21)
    for line in (snippets.get("bullets") or [])[:3]:
        _draw_check(draw, 56, y + 2)
        for i, row in enumerate(_wrap_text(draw, line, body, size[0] - 120)):
            draw.text((88, y + i * 28), row, fill=THEME["sub"], font=body)
        y += 28 * max(1, len(_wrap_text(draw, line, body, size[0] - 120))) + 16
    return img


def render_specs_card(
    snippets: dict,
    *,
    product_path: Path | None = None,
    size: tuple[int, int] = WIDE_SIZE,
) -> Image.Image:
    img = Image.new("RGB", size, THEME["panel"])
    draw = ImageDraw.Draw(img)
    draw.text((48, 40), snippets.get("specs_title") or "Details", fill=THEME["ink"], font=_font(32, True))
    draw.line([(48, 92), (size[0] - 48, 92)], fill=THEME["line"], width=1)

    _paste_product(img, product_path, (48, 110, 280, 480), shadow=True)

    x_label, x_val = 310, 430
    y = 130
    label_f = _font(17)
    val_f = _font(20, True)
    for label, value in (snippets.get("specs") or [])[:5]:
        draw.text((x_label, y), str(label).upper(), fill=THEME["sub"], font=label_f)
        wrapped = _wrap_text(draw, str(value), val_f, size[0] - x_val - 40)
        for i, row in enumerate(wrapped):
            draw.text((x_val, y + i * 26), row, fill=THEME["ink"], font=val_f)
        y += max(26, 26 * len(wrapped)) + 28
        draw.line([(x_label, y - 10), (size[0] - 48, y - 10)], fill=THEME["line"], width=1)
    return img


def render_scene_overlay(
    scene_path: Path,
    snippets: dict,
    *,
    size: tuple[int, int] = CARD_SIZE,
) -> Image.Image:
    if scene_path.is_file():
        base = _fit_cover(Image.open(scene_path).convert("RGB"), size)
    else:
        base = Image.new("RGB", size, THEME["bg"])

    # 轻微暗角
    vignette = Image.new("RGBA", size, (0, 0, 0, 0))
    vd = ImageDraw.Draw(vignette)
    for i in range(120):
        alpha = int(90 * (i / 120))
        vd.line([(0, size[1] - 120 + i), (size[0], size[1] - 120 + i)], fill=(0, 0, 0, alpha))
    base = Image.alpha_composite(base.convert("RGBA"), vignette).convert("RGB")

    _frosted_panel(base, (40, size[1] - 168, size[0] - 40, size[1] - 40), radius=18)
    draw = ImageDraw.Draw(base)
    tag_f = _font(14, True)
    draw.rounded_rectangle((56, size[1] - 152, 56 + 88, size[1] - 128), radius=8, fill=THEME["accent_soft"])
    draw.text((68, size[1] - 150), "SCENE", fill=THEME["accent"], font=tag_f)

    title = snippets.get("scene_title") or ""
    draw.text((56, size[1] - 118), title, fill=THEME["ink"], font=_font(28, True))
    cap_f = _font(20)
    for i, line in enumerate(_wrap_text(draw, snippets.get("scene_caption") or "", cap_f, size[0] - 120)):
        draw.text((56, size[1] - 78 + i * 26), line, fill=THEME["sub"], font=cap_f)
    return base


def render_steps_card(
    snippets: dict,
    *,
    product_path: Path | None = None,
    bg_path: Path | None = None,
    size: tuple[int, int] = CARD_SIZE,
) -> Image.Image:
    img = _blurred_bg(bg_path, size)
    draw = ImageDraw.Draw(img)
    _frosted_panel(img, (32, 32, size[0] - 32, size[1] - 32), radius=22)
    draw = ImageDraw.Draw(img)

    draw.text((56, 56), snippets.get("steps_title") or "How to Use", fill=THEME["ink"], font=_font(30, True))
    _paste_product(img, product_path, (size[0] - 260, 48, size[0] - 56, 240), shadow=False)

    steps = snippets.get("steps") or []
    y = 120
    num_f = _font(22, True)
    txt_f = _font(22)
    for idx, step in enumerate(steps[:3], 1):
        draw.ellipse((56, y, 96, y + 40), fill=THEME["accent"])
        draw.text((68, y + 7), str(idx), fill=(255, 255, 255), font=num_f)
        wrapped = _wrap_text(draw, step, txt_f, size[0] - 320)
        for i, row in enumerate(wrapped):
            draw.text((112, y + 6 + i * 30), row, fill=THEME["ink"], font=txt_f)
        y += max(52, 30 * len(wrapped) + 22)
    return img


def _save_card(img: Image.Image, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, format="JPEG", quality=94, optimize=True)
    return str(dest.relative_to(ROOT))


def _resolve_product_image(offer_id: str, draft: dict | None) -> Path | None:
    """优先 AI 白底主图，其次 raw main。"""
    heroes = ((draft or {}).get("assets") or {}).get("hero_candidates") or []
    for h in heroes:
        if h.get("recipe") == "main_white" and h.get("path"):
            p = ROOT / h["path"]
            if p.is_file():
                return p
    for h in heroes:
        if h.get("path"):
            p = ROOT / h["path"]
            if p.is_file():
                return p
    assets = _load_existing_assets(offer_id)
    raw = assets.get("raw_main") or []
    return ROOT / raw[0] if raw else None


def build_detail_text_cards(
    offer_id: str,
    *,
    locales: list[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    def _log(msg: str) -> None:
        if progress:
            progress(msg)

    draft = load_draft(offer_id)
    data = (draft or {}).get("source") if draft else load_scrape(offer_id)
    if not data:
        data = load_scrape(offer_id)
    copy = (draft or {}).get("copy") or {}
    attrs = data.get("attributes") or {}

    product_path = _resolve_product_image(offer_id, draft)
    raw_dir = offer_dir(offer_id) / "raw"
    scene_path = raw_dir / "detail_01.jpg"
    if not scene_path.is_file():
        details = sorted(raw_dir.glob("detail_*.*"))
        scene_path = details[0] if details else Path()
    bg_path = scene_path if scene_path.is_file() else product_path

    locs = locales or ["en", "ru"]
    out_dir = offer_dir(offer_id) / OUT_DIR_NAME
    cards: list[dict] = []

    for loc in locs:
        snippets = build_snippets(locale=loc, copy=copy, attrs=attrs)
        _log(f"生成 {loc.upper()} 文字详情卡 ({DESIGN_VERSION})…")

        jobs = [
            ("features", "卖点亮点卡", lambda s: render_features_card(s, product_path=product_path, bg_path=bg_path)),
            ("specs", "规格参数卡", lambda s: render_specs_card(s, product_path=product_path)),
            ("scene_overlay", "场景标注卡", lambda s: render_scene_overlay(scene_path, s)),
            ("steps", "使用步骤卡", lambda s: render_steps_card(s, product_path=product_path, bg_path=bg_path)),
        ]
        for tid, label, fn in jobs:
            dest = out_dir / f"{tid}_{loc}.jpg"
            try:
                img = fn(snippets)
                rel = _save_card(img, dest)
                cards.append(
                    {
                        "id": f"{tid}_{loc}",
                        "template": tid,
                        "label": f"{label} ({loc.upper()})",
                        "locale": loc,
                        "status": "ok",
                        "path": rel,
                        "url": _public_url(rel),
                        "size": list(img.size),
                        "design_version": DESIGN_VERSION,
                    }
                )
            except Exception as e:
                cards.append(
                    {
                        "id": f"{tid}_{loc}",
                        "template": tid,
                        "label": f"{label} ({loc.upper()})",
                        "locale": loc,
                        "status": "error",
                        "error": str(e)[:200],
                    }
                )

    manifest = {
        "offer_id": offer_id,
        "title": data.get("title") or "",
        "design_version": DESIGN_VERSION,
        "note": "文字来自 DeepSeek 文案；视觉为 Pillow 模板 + Photoroom 白底产品图（非 AI 整图生成）",
        "locales": locs,
        "cards": cards,
        "snippets": {loc: build_snippets(locale=loc, copy=copy, attrs=attrs) for loc in locs},
        "summary": {"total": len(cards), "ok": sum(1 for c in cards if c.get("status") == "ok")},
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path(offer_id).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def manifest_path(offer_id: str) -> Path:
    return offer_dir(offer_id) / MANIFEST_NAME


def load_detail_text_cards(offer_id: str) -> dict | None:
    p = manifest_path(offer_id)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
