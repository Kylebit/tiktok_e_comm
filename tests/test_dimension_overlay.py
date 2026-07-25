import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from modules.sourcing.dimension_overlay import (
    apply_dimension_overlay,
    dimension_items,
)


class DimensionOverlayTests(unittest.TestCase):
    def test_parses_operator_dimensions(self):
        self.assertEqual(
            dimension_items("长34cm 宽58cm"),
            [("L", "34", "cm"), ("W", "58", "cm")],
        )
        self.assertEqual(
            dimension_items("34 x 58 cm"),
            [("L", "34", "cm"), ("W", "58", "cm")],
        )

    def test_renders_separate_length_and_width_annotations(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "size.png"
            image = Image.new("RGB", (600, 600), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((150, 95, 355, 365), outline="black", width=12)
            draw.ellipse((215, 155, 290, 230), outline="black", width=10)
            image.save(image_path)

            result = apply_dimension_overlay(image_path, "长34cm 宽58cm")

            self.assertEqual(result["labels"], ["WIDTH 34 cm", "HEIGHT 58 cm"])
            self.assertEqual(result["overlay_version"], "deterministic_dimension_overlay_v4")
            self.assertTrue(image_path.with_name("size_model.png").is_file())
            with Image.open(image_path) as rendered:
                self.assertEqual(rendered.size, (600, 600))
                colors = rendered.convert("RGB").getcolors(maxcolors=600 * 600)
            self.assertIsNotNone(colors)
            palette = {color for _, color in colors}
            self.assertIn((224, 70, 50), palette)
            self.assertIn((37, 99, 235), palette)

    def test_reapply_keeps_original_model_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "size.png"
            image = Image.new("RGB", (500, 500), "white")
            ImageDraw.Draw(image).rectangle((120, 90, 300, 320), outline="black", width=10)
            image.save(image_path)

            apply_dimension_overlay(image_path, "长34cm 宽58cm")
            backup = image_path.with_name("size_model.png")
            before = hashlib.sha256(backup.read_bytes()).hexdigest()
            apply_dimension_overlay(image_path, "长35cm 宽59cm")
            after = hashlib.sha256(backup.read_bytes()).hexdigest()

            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
