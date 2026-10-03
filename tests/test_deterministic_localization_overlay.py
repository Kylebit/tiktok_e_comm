from pathlib import Path
import importlib.util

from PIL import Image, ImageDraw


SCRIPT = Path(__file__).parents[1] / "tools" / "deterministic_localization_overlay.py"
SPEC = importlib.util.spec_from_file_location("overlay", SCRIPT)
overlay = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(overlay)


def test_footer_overlay_preserves_product_artwork(tmp_path: Path):
    source = tmp_path / "source.png"
    output = tmp_path / "out" / "localized.png"
    image = Image.new("RGB", (2048, 2048), "white")
    for x in range(100, 200):
        for y in range(100, 200):
            image.putpixel((x, y), (20, 30, 40))
    image.save(source)
    result = overlay.render(source, "58 cm x 22 cm cada · 4 pzas", output)
    assert output.exists()
    assert result["provider_calls"] == 0
    assert result["protected_region"] == [0, 0, 2048, 1500]
    assert Image.open(source).crop((0, 0, 2048, 1500)).tobytes() == Image.open(output).crop((0, 0, 2048, 1500)).tobytes()


def test_thai_uses_a_thai_capable_font():
    image = Image.new("RGB", (2048, 2048), "white")
    font = overlay.font_for(ImageDraw.Draw(image), "58 cm x 22 cm แต่ละ · 4 ชิ้น")
    assert Path(font.path).name.casefold() == "leelawui.ttf"
