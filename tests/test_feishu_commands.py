from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.hub import feishu_commands


class FeishuCommandTests(unittest.TestCase):
    def test_parse_plain_sku_as_send_both(self) -> None:
        self.assertEqual(feishu_commands.parse_command("0927"), ("send_both", "0927"))

    def test_parse_sku_and_url_as_save_purchase_link(self) -> None:
        cmd, args = feishu_commands.parse_command("0927 https://qr.1688.com/s/A4vbPK9P")
        self.assertEqual(cmd, "save_purchase_link")
        self.assertIn("0927", args)
        self.assertIn("A4vbPK9P", args)

    def test_parse_phrase_get_purchase_link(self) -> None:
        self.assertEqual(feishu_commands.parse_command("閸欐垿鈧?003閻ㄥ嫰鍣扮拹顓㈡懠閹?), ("get_purchase_link", "0003"))    def test_send_both_reuses_image_and_link_flow(self) -> None:
        with (
            patch("modules.hub.feishu_commands._handle_send_main_image", return_value="鉁?宸插彂閫?SKU 990927 涓诲浘") as image_mock,
            patch("modules.hub.feishu_commands._handle_get_purchase_link", return_value="馃洅 990927 閲囪喘閾炬帴锛?026-07-03锛塡nhttps://qr.1688.com/s/A4vbPK9P") as link_mock,
        ):
            reply = feishu_commands.handle_command("0927", message_id="om_test")
        image_mock.assert_called_once_with("0927", "om_test")
        link_mock.assert_called_once_with("0927")
        self.assertIn("閲囪喘閾炬帴", reply)
        self.assertNotIn("宸插彂閫?SKU 990927 涓诲浘", reply)
    def test_save_purchase_link_upserts_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "shop.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE products (
                    sku_id TEXT,
                    seller_sku TEXT,
                    product_name TEXT,
                    image_url TEXT,
                    parent_sku TEXT
                )
                """
            )
            conn.commit()
            conn.close()

            with (
                patch("modules.hub.feishu_commands._get_db_path", return_value=str(db_path)),
                patch("modules.hub.feishu_app._find_sku", return_value=[("990927", "990927", "Test", "https://example.com/1.jpg")]),
            ):
                reply = feishu_commands._handle_save_purchase_link("0927|https://qr.1688.com/s/A4vbPK9P")
                self.assertIn("990927", reply)

                conn = sqlite3.connect(db_path)
                row = conn.execute(
                    "SELECT sku, url, parent_sku FROM purchasing_links WHERE sku = '990927'"
                ).fetchone()
                conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], "990927")
        self.assertEqual(row[1], "https://qr.1688.com/s/A4vbPK9P")
        self.assertEqual(row[2], "0927")


if __name__ == "__main__":
    unittest.main()
