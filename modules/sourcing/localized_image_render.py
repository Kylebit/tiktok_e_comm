"""Deterministic local previews for operator-edited image translations."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont


RENDERER = "pillow-local-preview/v2"


class LocalizedImageRenderError(ValueError):
    """The local preview cannot be rendered safely."""


def _font(size: int, locale: str) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    locale_candidates = {
        "th-TH": (
            Path("C:/Windows/Fonts/LeelawUI.ttf"),
            Path("C:/Windows/Fonts/tahoma.ttf"),
        ),
    }
    candidates = (
        *locale_candidates.get(locale, ()),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for path in candidates:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _pixel_box(box: Sequence[float], width: int, height: int) -> tuple[int, int, int, int]:
    if len(box) != 4:
        raise LocalizedImageRenderError("translation region box is invalid")
    try:
        x0, y0, x1, y1 = [float(value) for value in box]
    except (TypeError, ValueError) as error:
        raise LocalizedImageRenderError("translation region box is invalid") from error
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise LocalizedImageRenderError("translation region box is invalid")
    return (
        max(0, min(width - 1, round(x0 * width))),
        max(0, min(height - 1, round(y0 * height))),
        max(1, min(width, round(x1 * width))),
        max(1, min(height, round(y1 * height))),
    )


def _sample_fill(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    width, height = image.size
    x0, y0, x1, y1 = box
    samples: list[tuple[int, int, int]] = []
    if y0 > 0:
        samples.extend(image.getpixel((x, y0 - 1))[:3] for x in range(x0, x1))
    if y1 < height:
        samples.extend(image.getpixel((x, y1))[:3] for x in range(x0, x1))
    if x0 > 0:
        samples.extend(image.getpixel((x0 - 1, y))[:3] for y in range(y0, y1))
    if x1 < width:
        samples.extend(image.getpixel((x1, y))[:3] for y in range(y0, y1))
    if not samples:
        samples = [image.getpixel((0, 0))[:3]]
    return tuple(
        round(sum(pixel[channel] for pixel in samples) / len(samples))
        for channel in range(3)
    )


def _wrap(text: str, max_chars: int) -> str:
    if max_chars < 2:
        return text
    words = text.split()
    if len(words) <= 1:
        return "\n".join(
            text[index : index + max_chars]
            for index in range(0, len(text), max_chars)
        )
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


def _fitted_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    locale: str,
) -> tuple[str, ImageFont.ImageFont]:
    x0, y0, x1, y1 = box
    box_width = max(1, x1 - x0 - 6)
    box_height = max(1, y1 - y0 - 6)
    for size in range(max(10, min(44, box_height)), 7, -1):
        font = _font(size, locale)
        max_chars = max(2, int(box_width / max(1, size * 0.56)))
        wrapped = _wrap(text, max_chars)
        bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=2)
        if bounds[2] - bounds[0] <= box_width and bounds[3] - bounds[1] <= box_height:
            return wrapped, font
    return _wrap(text, max(2, len(text))), _font(8, locale)


def render_translation_preview(
    image_bytes: bytes,
    *,
    regions: Sequence[Mapping[str, Any]],
    translations: Sequence[Mapping[str, Any]],
    locale: str,
) -> bytes:
    """Render a non-approved local preview over the reviewed OCR boxes."""

    if locale == "en-master" or not str(locale).strip():
        raise LocalizedImageRenderError("localized preview locale is invalid")
    try:
        with Image.open(BytesIO(image_bytes)) as opened:
            image = opened.convert("RGB")
    except Exception as error:
        raise LocalizedImageRenderError("preview source image is invalid") from error
    regions_by_id = {
        str(row.get("region_id") or ""): row for row in regions if isinstance(row, Mapping)
    }
    translated = {
        str(row.get("region_id") or ""): str(row.get("translated_text") or "").strip()
        for row in translations
        if isinstance(row, Mapping)
    }
    if not regions_by_id or set(translated) != set(regions_by_id) or not all(translated.values()):
        raise LocalizedImageRenderError("complete translated region coverage is required")
    draw = ImageDraw.Draw(image)
    for region_id, region in regions_by_id.items():
        box = _pixel_box(region.get("bbox") or [], *image.size)
        fill = _sample_fill(image, box)
        draw.rectangle(box, fill=fill)
        text, font = _fitted_text(draw, translated[region_id], box, locale)
        luminance = (0.299 * fill[0]) + (0.587 * fill[1]) + (0.114 * fill[2])
        color = (20, 26, 25) if luminance > 145 else (248, 248, 245)
        draw.multiline_text(
            (box[0] + 3, box[1] + 3),
            text,
            fill=color,
            font=font,
            spacing=2,
            align="left",
        )
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
