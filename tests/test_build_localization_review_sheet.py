import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image


SCRIPT = Path(__file__).parents[1] / "tools" / "build_localization_review_sheet.py"
SPEC = importlib.util.spec_from_file_location("review_sheet", SCRIPT)
review_sheet = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(review_sheet)


def test_builds_eight_tile_review_sheet(tmp_path: Path, monkeypatch):
    rows = []
    for index, locale in enumerate(("es-MX", "ms-MY", "ru-RU", "th-TH", "vi-VN", "es-MX", "th-TH", "vi-VN")):
        candidate = tmp_path / f"candidate-{index}.png"
        Image.new("RGB", (32, 32), (index, index, index)).save(candidate)
        rows.append(
            {
                "brand_id": "livelyhive-sea" if index < 5 else "homebloom-sea",
                "review_number": 7 if index < 5 else 14,
                "locale": locale,
                "output_path": str(candidate),
            }
        )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    output = tmp_path / "review.png"
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--manifest", str(manifest), "--output", str(output)])

    assert review_sheet.main() == 0
    assert Image.open(output).size == (1680, 1074)
