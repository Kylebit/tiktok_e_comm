"""Build one local visual review sheet from immutable candidate images."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


LOCALES = {
    "es-MX": "Spanish · MX",
    "ms-MY": "Malay · MY",
    "ru-RU": "Russian · RU",
    "th-TH": "Thai · TH",
    "vi-VN": "Vietnamese · VN",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists(), "review sheet already exists"
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    rows = sorted(manifest["rows"], key=lambda row: (row["brand_id"], row["review_number"], row["locale"]))
    assert len(rows) == 8
    tile, label = 420, 72
    sheet = Image.new("RGB", (tile * 4, (tile + label) * 2 + 90), "#f5f2eb")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 22)
    heading = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 28)
    draw.text((24, 25), "Offer 3956742887 · deterministic footer-localization candidates · review only", fill="#201510", font=heading)
    for index, row in enumerate(rows):
        x = (index % 4) * tile
        y = 90 + (index // 4) * (tile + label)
        image = Image.open(row["output_path"]).convert("RGB").resize((tile, tile))
        sheet.paste(image, (x, y))
        brand = "LivelyHive" if row["brand_id"] == "livelyhive-sea" else "HomeBloom"
        text = f"#{row['review_number']} · {brand} · {LOCALES[row['locale']]}"
        draw.rectangle((x, y + tile, x + tile, y + tile + label), fill="#fffdf9")
        draw.text((x + 12, y + tile + 22), text, fill="#281711", font=font)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output, format="PNG")
    print(json.dumps({"rows": len(rows), "output": str(args.output), "external_write_count": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
