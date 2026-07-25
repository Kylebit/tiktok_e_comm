from __future__ import annotations

import re
import shutil
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont


_LABELS = {"长": "L", "宽": "W", "高": "H", "厚": "D"}


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ):
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def dimension_items(value: str) -> list[tuple[str, str, str]]:
    pairs = re.findall(
        r"([长宽高厚LWH])\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(cm|mm|m|in)?",
        str(value or ""),
        re.I,
    )
    if pairs:
        return [
            (
                _LABELS.get(label.upper(), _LABELS.get(label, label.upper())),
                number,
                (unit or "cm").lower(),
            )
            for label, number, unit in pairs
        ]
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(cm|mm|m|in)?\s*[x×*]\s*"
        r"(\d+(?:\.\d+)?)\s*(cm|mm|m|in)?",
        str(value or ""),
        re.I,
    )
    if match:
        first_unit = (match.group(2) or match.group(4) or "cm").lower()
        second_unit = (match.group(4) or match.group(2) or "cm").lower()
        return [
            ("L", match.group(1), first_unit),
            ("W", match.group(3), second_unit),
        ]
    raise ValueError("confirmed dimensions must contain recognizable length and width values")


def _content_bbox(image: Image.Image) -> tuple[int, int, int, int]:
    width, height = image.size
    core = image.crop((0, 0, int(width * 0.79), int(height * 0.82))).convert("RGB")
    white = Image.new("RGB", core.size, "white")
    difference = ImageChops.difference(core, white).convert("L")
    mask = difference.point(lambda value: 255 if value > 22 else 0)
    bbox = mask.getbbox()
    if not bbox:
        raise ValueError("could not locate the product on the size-card base")
    padding = max(4, int(min(width, height) * 0.012))
    return (
        max(0, bbox[0] - padding),
        max(0, bbox[1] - padding),
        min(core.width, bbox[2] + padding),
        min(core.height, bbox[3] + padding),
    )


def _fit_product(
    image: Image.Image,
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    width, height = image.size
    bbox = _content_bbox(image)
    product = image.crop(bbox).convert("RGB")
    max_width = int(width * 0.58)
    max_height = int(height * 0.62)
    scale = min(max_width / product.width, max_height / product.height, 1.0)
    product = product.resize(
        (max(1, int(product.width * scale)), max(1, int(product.height * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGB", (width, height), "white")
    left_area_width = int(width * 0.72)
    x = max(int(width * 0.07), (left_area_width - product.width) // 2)
    y = max(int(height * 0.08), int(height * 0.40 - product.height / 2))
    canvas.paste(product, (x, y))
    return canvas, (x, y, x + product.width, y + product.height)


def _double_arrow(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    color: tuple[int, int, int],
    line_width: int,
    arrow_size: int,
) -> None:
    draw.line((start, end), fill=color, width=line_width)
    x1, y1 = start
    x2, y2 = end
    if y1 == y2:
        draw.polygon(
            [(x1, y1), (x1 + arrow_size, y1 - arrow_size // 2), (x1 + arrow_size, y1 + arrow_size // 2)],
            fill=color,
        )
        draw.polygon(
            [(x2, y2), (x2 - arrow_size, y2 - arrow_size // 2), (x2 - arrow_size, y2 + arrow_size // 2)],
            fill=color,
        )
    else:
        draw.polygon(
            [(x1, y1), (x1 - arrow_size // 2, y1 + arrow_size), (x1 + arrow_size // 2, y1 + arrow_size)],
            fill=color,
        )
        draw.polygon(
            [(x2, y2), (x2 - arrow_size // 2, y2 - arrow_size), (x2 + arrow_size // 2, y2 - arrow_size)],
            fill=color,
        )


def _label_chip(
    text: str,
    *,
    font: ImageFont.ImageFont,
    color: tuple[int, int, int],
    rotate: bool = False,
) -> Image.Image:
    probe = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    box = draw.textbbox((0, 0), text, font=font)
    padding_x, padding_y = 22, 12
    chip = Image.new(
        "RGBA",
        (box[2] - box[0] + padding_x * 2, box[3] - box[1] + padding_y * 2),
        (0, 0, 0, 0),
    )
    chip_draw = ImageDraw.Draw(chip)
    chip_draw.rounded_rectangle(
        (0, 0, chip.width - 1, chip.height - 1),
        radius=8,
        fill=(*color, 255),
    )
    chip_draw.text(
        (padding_x - box[0], padding_y - box[1]),
        text,
        font=font,
        fill=(255, 255, 255, 255),
    )
    return chip.rotate(90, expand=True, resample=Image.Resampling.BICUBIC) if rotate else chip


def render_dimension_overlay(
    source_path: Path,
    destination_path: Path,
    dimensions: str,
) -> dict[str, object]:
    items = dimension_items(dimensions)
    if len(items) < 2:
        raise ValueError("a size card requires at least two confirmed dimensions")
    with Image.open(source_path) as source:
        base, product_bbox = _fit_product(source.convert("RGB"))

    width, height = base.size
    x0, y0, x1, y1 = product_bbox
    length_color = (224, 70, 50)
    width_color = (37, 99, 235)
    guide_color = (189, 199, 210)
    line_width = max(4, int(width * 0.004))
    arrow_size = max(18, int(width * 0.018))
    horizontal_y = min(height - int(height * 0.11), y1 + int(height * 0.095))
    vertical_x = min(width - int(width * 0.105), x1 + int(width * 0.105))
    inset = max(8, int(width * 0.008))

    draw = ImageDraw.Draw(base)
    draw.line((x0, y1 + inset, x0, horizontal_y + arrow_size), fill=guide_color, width=max(2, line_width // 2))
    draw.line((x1, y1 + inset, x1, horizontal_y + arrow_size), fill=guide_color, width=max(2, line_width // 2))
    draw.line((x1 + inset, y0, vertical_x + arrow_size, y0), fill=guide_color, width=max(2, line_width // 2))
    draw.line((x1 + inset, y1, vertical_x + arrow_size, y1), fill=guide_color, width=max(2, line_width // 2))
    _double_arrow(
        draw,
        (x0, horizontal_y),
        (x1, horizontal_y),
        color=length_color,
        line_width=line_width,
        arrow_size=arrow_size,
    )
    _double_arrow(
        draw,
        (vertical_x, y0),
        (vertical_x, y1),
        color=width_color,
        line_width=line_width,
        arrow_size=arrow_size,
    )

    font = _font(max(32, int(width * 0.048)))
    # Product size cards use the first confirmed value horizontally and the
    # second vertically, regardless of the source system's 长/宽 field names.
    horizontal_text = f"WIDTH {items[0][1]} {items[0][2]}"
    vertical_text = f"HEIGHT {items[1][1]} {items[1][2]}"
    horizontal_chip = _label_chip(horizontal_text, font=font, color=length_color)
    vertical_chip = _label_chip(vertical_text, font=font, color=width_color, rotate=True)
    base.paste(
        horizontal_chip,
        (
            int((x0 + x1 - horizontal_chip.width) / 2),
            min(height - horizontal_chip.height - 12, horizontal_y + arrow_size),
        ),
        horizontal_chip,
    )
    base.paste(
        vertical_chip,
        (
            min(width - vertical_chip.width - 12, vertical_x + arrow_size),
            int((y0 + y1 - vertical_chip.height) / 2),
        ),
        vertical_chip,
    )
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    base.save(destination_path, format="PNG", optimize=True)
    return {
        "labels": [horizontal_text, vertical_text],
        "product_bbox": list(product_bbox),
        "horizontal_arrow": [x0, horizontal_y, x1, horizontal_y],
        "vertical_arrow": [vertical_x, y0, vertical_x, y1],
        "overlay_version": "deterministic_dimension_overlay_v4",
    }


def apply_dimension_overlay(path: Path, dimensions: str) -> dict[str, object]:
    backup = path.with_name(path.stem + "_model" + path.suffix)
    if not backup.is_file():
        shutil.copy2(path, backup)
    result = render_dimension_overlay(backup, path, dimensions)
    result["model_backup"] = str(backup)
    return result
