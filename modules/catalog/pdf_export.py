"""商品目录 PDF 导出：对齐码、商品名、SKU 名（中文）、主图。"""

from __future__ import annotations

import io
import json
import re
from datetime import datetime
from urllib.request import Request

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import urlopen as urlopen_retry
from core.llm import ai_config, chat_completion
from modules.catalog import listings as cat_mod

_FONT_REGISTERED = False

_TRANSLATE_SYSTEM = """你是电商商品翻译。将输入 JSON 数组中每条记录的 product_name、sku_name 译为简体中文（电商用语、简洁准确）。
若已是中文则保持原样。sku_name 为空则 sku_name_zh 留空字符串。
仅输出 JSON 数组，元素含 id、product_name_zh、sku_name_zh，顺序与输入一致。"""


def _ensure_font() -> str:
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return "ZhFont"
    for p in (
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ):
        try:
            pdfmetrics.registerFont(TTFont("ZhFont", p, subfontIndex=0))
            _FONT_REGISTERED = True
            return "ZhFont"
        except Exception:
            continue
    _FONT_REGISTERED = True
    return "Helvetica"


def _extract_export_row(item: dict) -> dict | None:
    tk = item.get("tiktok")
    sp = item.get("shopee")
    block = tk or sp
    if not block:
        return None

    match_key = (item.get("match_key") or "").strip()
    if not match_key:
        if item.get("missing_tk_sku"):
            match_key = (item.get("tk_sku_id") or "未填SKU").strip()
        elif item.get("missing_sp_sku"):
            match_key = (item.get("sp_model_id") or "非规范货号").strip()
        else:
            match_key = "—"

    product_name = (block.get("product_name") or "").strip()
    image_url = (block.get("image_url") or "").strip()
    if sp:
        if not image_url:
            image_url = (sp.get("image_url") or "").strip()
        if not product_name:
            product_name = (sp.get("product_name") or "").strip()

    sku_names: list[str] = []
    for source in (tk, sp):
        if not source:
            continue
        for r in source.get("regions") or []:
            mn = (r.get("model_name") or "").strip()
            if mn and mn not in sku_names:
                sku_names.append(mn)
        if sku_names:
            break

    return {
        "match_key": match_key,
        "product_name": product_name,
        "sku_name": " / ".join(sku_names),
        "image_url": image_url,
    }


def collect_export_rows(
    region: str | None = None,
    *,
    sku: str | None = None,
    match_only: bool = False,
    platform: str | None = None,
    limit: int = 500,
) -> list[dict]:
    data = cat_mod.list_products(
        region,
        sku=sku,
        match_only=match_only,
        platform=platform,
        limit=min(limit, 500),
        offset=0,
    )
    rows: list[dict] = []
    for item in data.get("items") or []:
        row = _extract_export_row(item)
        if row:
            rows.append(row)
    return rows


def _parse_json_array(text: str) -> list:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def translate_rows_to_zh(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    if not ai_config()["api_key"]:
        return [
            {**r, "product_name_zh": r["product_name"], "sku_name_zh": r["sku_name"]}
            for r in rows
        ]

    chunk_size = 25
    translated: dict[int, dict] = {}
    for base in range(0, len(rows), chunk_size):
        chunk = rows[base : base + chunk_size]
        payload = [
            {
                "id": base + i,
                "product_name": r["product_name"],
                "sku_name": r["sku_name"],
            }
            for i, r in enumerate(chunk)
        ]
        try:
            raw = chat_completion(
                [
                    {"role": "system", "content": _TRANSLATE_SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                max_tokens=min(4096, 150 * len(chunk) + 300),
            )
            for p in _parse_json_array(raw):
                pid = p.get("id")
                if pid is not None:
                    translated[int(pid)] = p
        except Exception:
            for i, r in enumerate(chunk):
                translated[base + i] = {
                    "product_name_zh": r["product_name"],
                    "sku_name_zh": r["sku_name"],
                }

    out: list[dict] = []
    for i, r in enumerate(rows):
        p = translated.get(i, {})
        out.append(
            {
                **r,
                "product_name_zh": p.get("product_name_zh") or r["product_name"],
                "sku_name_zh": p.get("sku_name_zh") or r["sku_name"],
            }
        )
    return out


def _download_image(url: str) -> ImageReader | None:
    if not url:
        return None
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen_retry(req, timeout=15, context=SSL_CTX) as resp:
            data = resp.read()
        if len(data) > 5_000_000:
            return None
        pil = PILImage.open(io.BytesIO(data)).convert("RGB")
        w, h = pil.size
        max_side = 400
        if max(w, h) > max_side:
            ratio = max_side / max(w, h)
            pil = pil.resize((int(w * ratio), int(h * ratio)), PILImage.Resampling.LANCZOS)
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        return ImageReader(buf)
    except Exception:
        return None


def _wrap_text(c: canvas.Canvas, text: str, font: str, size: float, max_width: float) -> list[str]:
    c.setFont(font, size)
    if not text:
        return [""]
    lines: list[str] = []
    line = ""
    for ch in text:
        test = line + ch
        if c.stringWidth(test, font, size) <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = ch
    if line:
        lines.append(line)
    return lines or [""]


def _draw_labeled_lines(
    c: canvas.Canvas,
    x: float,
    y: float,
    label: str,
    text: str,
    font: str,
    size: float,
    max_width: float,
    max_lines: int,
    *,
    line_step: float | None = None,
) -> float:
    step = line_step if line_step is not None else 5 * mm
    lines = _wrap_text(c, text, font, size, max_width - c.stringWidth(label, font, size))
    if not lines:
        return y
    c.setFont(font, size)
    c.drawString(x, y, label + lines[0])
    y -= step
    indent = x + c.stringWidth(label, font, size)
    for line in lines[1:max_lines]:
        c.drawString(indent, y, line)
        y -= step
    return y


def _draw_product_card(
    c: canvas.Canvas,
    row: dict,
    col_x: float,
    top_y: float,
    col_w: float,
    font: str,
) -> float:
    img_size = 16 * mm
    gap = 2 * mm
    text_x = col_x + img_size + gap
    text_w = col_w - img_size - gap
    line_step = 3.5 * mm

    img_y = top_y - img_size
    img = _download_image(row.get("image_url") or "")
    if img:
        try:
            c.drawImage(
                img,
                col_x,
                img_y,
                width=img_size,
                height=img_size,
                preserveAspectRatio=True,
                anchor="sw",
            )
        except Exception:
            c.setFont(font, 7)
            c.setFillColor(colors.grey)
            c.drawString(col_x, img_y + img_size / 2 - 1 * mm, "无图")
            c.setFillColor(colors.black)
    else:
        c.setFont(font, 7)
        c.setFillColor(colors.grey)
        c.drawString(col_x, img_y + img_size / 2 - 1 * mm, "无图")
        c.setFillColor(colors.black)

    ty = top_y - 3.5 * mm
    c.setFont(font, 8)
    c.drawString(text_x, ty, f"对齐码：{row['match_key']}")
    ty -= 4.5 * mm

    pname = row.get("product_name_zh") or row.get("product_name") or "—"
    ty = _draw_labeled_lines(
        c, text_x, ty, "商品：", pname, font, 7, text_w, 2, line_step=line_step
    )

    sku = row.get("sku_name_zh") or row.get("sku_name") or ""
    if sku:
        ty = _draw_labeled_lines(
            c, text_x, ty, "SKU：", sku, font, 7, text_w, 1, line_step=line_step
        )

    return min(ty, img_y) - 2 * mm


def build_catalog_pdf(rows: list[dict]) -> bytes:
    font = _ensure_font()
    buf = io.BytesIO()
    page_w, page_h = A4
    margin = 10 * mm
    col_gap = 4 * mm
    col_w = (page_w - 2 * margin - col_gap) / 2
    left_x = margin
    right_x = margin + col_w + col_gap
    card_gap = 3 * mm
    min_row_h = 28 * mm

    c = canvas.Canvas(buf, pagesize=A4)
    y = page_h - margin

    c.setFont(font, 12)
    c.drawString(margin, y, "商品目录导出")
    y -= 6 * mm
    c.setFont(font, 8)
    c.setFillColor(colors.grey)
    c.drawString(margin, y, f"共 {len(rows)} 条 · {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    c.setFillColor(colors.black)
    y -= 8 * mm

    row_top = y
    row_y_bottom = y

    for i, row in enumerate(rows):
        col = i % 2
        if col == 0:
            if i > 0:
                row_top = row_y_bottom - card_gap
            if row_top < margin + min_row_h:
                c.showPage()
                row_top = page_h - margin
            c.setStrokeColor(colors.lightgrey)
            c.line(margin, row_top + 1 * mm, page_w - margin, row_top + 1 * mm)

        col_x = left_x if col == 0 else right_x
        bottom = _draw_product_card(c, row, col_x, row_top, col_w, font)

        if col == 0:
            left_bottom = bottom
        else:
            row_y_bottom = min(left_bottom, bottom)

    c.save()
    return buf.getvalue()


def export_catalog_pdf(
    region: str | None = None,
    *,
    sku: str | None = None,
    match_only: bool = False,
    platform: str | None = None,
    limit: int = 500,
    translate: bool = True,
) -> tuple[bytes, str]:
    rows = collect_export_rows(
        region,
        sku=sku,
        match_only=match_only,
        platform=platform,
        limit=limit,
    )
    if translate:
        rows = translate_rows_to_zh(rows)
    else:
        rows = [
            {**r, "product_name_zh": r["product_name"], "sku_name_zh": r["sku_name"]}
            for r in rows
        ]
    pdf = build_catalog_pdf(rows)
    fname = f"catalog_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    return pdf, fname
