"""Make review-only footer-localization candidates from approved master images.

The tool deliberately changes only a bounded, separate specification footer.
It never edits the product artwork region, contacts a provider, or updates
publication state.  The caller supplies every input path explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


FOOTER_TOP = 1500
FOOTER_BOTTOM = 1710
MARGIN = 80
FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
)
THAI_FONT = Path("C:/Windows/Fonts/LeelawUI.ttf")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def footer_translation(report: dict, review_number: int, locale: str) -> str:
    inventory = next(
        row
        for row in report["text_inventories"]
        if int(row["review_number"]) == review_number
    )
    translated = inventory["translations"][locale]
    footer = next(
        row["translated_text"]
        for row in translated
        if "58 cm x 22 cm" in str(row.get("source_text") or "")
    )
    if not isinstance(footer, str) or not footer.strip():
        raise ValueError(f"missing footer translation for {review_number}/{locale}")
    return footer.strip()


def font_for(draw: ImageDraw.ImageDraw, text: str) -> ImageFont.FreeTypeFont:
    font_paths = (THAI_FONT, *FONT_CANDIDATES) if any("\u0e00" <= char <= "\u0e7f" for char in text) else FONT_CANDIDATES
    font_path = next((path for path in font_paths if path.exists()), None)
    if font_path is None:
        raise RuntimeError("no Windows UI font is available")
    for size in range(76, 35, -2):
        font = ImageFont.truetype(str(font_path), size=size)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= 2048 - 2 * MARGIN:
            return font
    raise ValueError("footer text does not fit in the bounded overlay")


def render(source_path: Path, text: str, output_path: Path) -> dict:
    source = Image.open(source_path).convert("RGBA")
    if source.size != (2048, 2048):
        raise ValueError("approved master must be a 2048x2048 image")
    protected = source.crop((0, 0, 2048, FOOTER_TOP)).tobytes()
    candidate = source.copy()
    draw = ImageDraw.Draw(candidate)
    draw.rectangle((0, FOOTER_TOP, 2048, FOOTER_BOTTOM), fill=(255, 255, 255, 255))
    font = font_for(draw, text)
    box = draw.textbbox((0, 0), text, font=font)
    x = (2048 - (box[2] - box[0])) // 2
    y = FOOTER_TOP + (FOOTER_BOTTOM - FOOTER_TOP - (box[3] - box[1])) // 2 - box[1]
    draw.text((x, y), text, fill=(54, 31, 21, 255), font=font)
    if candidate.crop((0, 0, 2048, FOOTER_TOP)).tobytes() != protected:
        raise AssertionError("product-artwork region changed")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    candidate.convert("RGB").save(output_path, format="PNG")
    return {
        "source_path": str(source_path),
        "source_sha256": sha256_bytes(source_path.read_bytes()),
        "protected_region": [0, 0, 2048, FOOTER_TOP],
        "protected_region_sha256": sha256_bytes(protected),
        "footer_region": [0, FOOTER_TOP, 2048, FOOTER_BOTTOM],
        "footer_text": text,
        "output_path": str(output_path),
        "output_sha256": sha256_bytes(output_path.read_bytes()),
        "provider_calls": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--translation-report", type=Path, required=True)
    parser.add_argument("--asset-reconciliation", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    assert not args.output_dir.exists(), "output directory already exists"
    translations = load_json(args.translation_report)
    reconciliation = load_json(args.asset_reconciliation)
    assert translations["offer_id"] == reconciliation["offer_id"] == "3956742887"
    rows = []
    for row in reconciliation["rows"]:
        review_number = int(row["review_number"])
        locale = str(row["locale"])
        output = args.output_dir / f"review-{review_number}-{locale}.png"
        rendered = render(Path(row["source"]["artifact_path"]), footer_translation(translations, review_number, locale), output)
        rows.append({"review_number": review_number, "locale": locale, "brand_id": row["brand_id"], **rendered})
    assert len(rows) == 8 and len({(row["review_number"], row["locale"]) for row in rows}) == 8
    manifest = {
        "schema_version": "deterministic-footer-localization-candidate/v1",
        "offer_id": "3956742887",
        "status": "VISUAL_QA_REQUIRED",
        "external_write_count": 0,
        "provider_calls": 0,
        "repair_contract": "Only the bounded footer region changed; product artwork pixels above it are preserved byte-for-byte.",
        "rows": rows,
    }
    (args.output_dir / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "rows": len(rows), "provider_calls": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
