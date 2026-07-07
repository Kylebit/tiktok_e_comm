import io
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from modules.hub import feishu_bot
from modules.hub import feishu_commands as cmd_mod


def build_test_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku_id TEXT,
            seller_sku TEXT,
            product_name TEXT,
            image_url TEXT,
            parent_sku TEXT
        );
        CREATE TABLE purchasing_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL,
            platform TEXT DEFAULT '1688',
            url TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            parent_sku TEXT,
            UNIQUE(sku, platform)
        );
        """
    )
    conn.execute(
        "INSERT INTO products (sku_id, seller_sku, product_name, image_url, parent_sku) VALUES (?, ?, ?, ?, ?)",
        ("990927", "990927", "测试商品0927", "https://img.example.com/990927.jpg", "0927"),
    )
    conn.commit()
    conn.close()


class FeishuBot1688ReplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "shop.db")
        build_test_db(self.db_path)

        self.db_patcher = mock.patch.object(cmd_mod, "_get_db_path", return_value=self.db_path)
        self.db_patcher.start()

        self.find_patcher = mock.patch(
            "modules.hub.feishu_app._find_sku",
            return_value=[("990927", "990927", "测试商品0927", "https://img.example.com/990927.jpg")],
        )
        self.find_patcher.start()

    def tearDown(self) -> None:
        self.find_patcher.stop()
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    def test_import_and_restart_bot_without_syntax_error(self) -> None:
        fake_lark = types.ModuleType("lark_oapi")

        class FakeBuilder:
            def register_p2_im_message_receive_v1(self, fn):
                self.fn = fn
                return self

            def build(self):
                return object()

        class FakeDispatcher:
            @staticmethod
            def builder(encrypt, verify):
                return FakeBuilder()

        class FakeClient:
            starts = 0

            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                FakeClient.starts += 1

        fake_lark.EventDispatcherHandler = FakeDispatcher
        fake_lark.LogLevel = types.SimpleNamespace(INFO="INFO")
        fake_lark.ws = types.SimpleNamespace(Client=FakeClient)

        model_mod = types.ModuleType("lark_oapi.api.im.v1.model.p2_im_message_receive_v1")
        model_mod.P2ImMessageReceiveV1 = object

        modules = {
            "lark_oapi": fake_lark,
            "lark_oapi.api": types.ModuleType("lark_oapi.api"),
            "lark_oapi.api.im": types.ModuleType("lark_oapi.api.im"),
            "lark_oapi.api.im.v1": types.ModuleType("lark_oapi.api.im.v1"),
            "lark_oapi.api.im.v1.model": types.ModuleType("lark_oapi.api.im.v1.model"),
            "lark_oapi.api.im.v1.model.p2_im_message_receive_v1": model_mod,
        }

        with mock.patch.dict(sys.modules, modules, clear=False):
            with mock.patch.object(feishu_bot, "app_ready", return_value=True), mock.patch.object(
                feishu_bot,
                "app_config",
                return_value={
                    "enabled": True,
                    "app_id": "cli_x",
                    "app_secret": "sec",
                    "verification_token": "",
                    "encrypt_key": "",
                },
            ), mock.patch.object(feishu_bot, "_disable_proxy_in_runtime"), mock.patch.object(
                feishu_bot, "_patch_websocket_ssl"
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    feishu_bot.run_websocket_bot()
                    feishu_bot.run_websocket_bot()
                out = buf.getvalue()

        self.assertIn("Feishu websocket bot connected", out)
        self.assertEqual(FakeClient.starts, 2)

    def test_at_orbithive_sku_returns_fixed_image_plus_purchase_link(self) -> None:
        with mock.patch(
            "modules.hub.feishu_app.reply_product_image",
            return_value="✅ 已发送 SKU 990927 主图（测试商品0927）",
        ) as reply_image, mock.patch(
            "modules.hub.feishu_app.reply_product_link",
            return_value="🛒 990927 采购链接\nhttps://qr.1688.com/s/demo",
        ):
            reply = cmd_mod.handle_command("@OrbitHive 0927", message_id="m-1")

        reply_image.assert_called_once_with("m-1", "0927")
        self.assertIn("✅ 已发送 SKU 990927 主图", reply or "")
        self.assertIn("🛒 990927 采购链接", reply or "")

    def test_at_orbithive_sku_still_returns_purchase_link_when_image_download_fails(self) -> None:
        with mock.patch(
            "modules.hub.feishu_app.reply_product_image",
            return_value="[测试商品0927] 图片暂时无法加载，请稍后再试",
        ) as reply_image, mock.patch(
            "modules.hub.feishu_app.reply_product_link",
            return_value="📦 990927 采购链接\nhttps://qr.1688.com/s/demo",
        ):
            reply = cmd_mod.handle_command("@OrbitHive 0927", message_id="m-1")

        reply_image.assert_called_once_with("m-1", "0927")
        self.assertEqual(
            reply,
            "[测试商品0927] 图片暂时无法加载，请稍后再试\n\n📦 990927 采购链接\nhttps://qr.1688.com/s/demo",
        )

    def test_save_purchase_link_message_persists_url(self) -> None:
        reply = cmd_mod.handle_command("0927 采购链接 https://qr.1688.com/s/ABC123", message_id="m-2")
        self.assertIn("已保存", reply or "")

        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT sku, url, parent_sku FROM purchasing_links LIMIT 1").fetchone()
        conn.close()

        self.assertEqual(row[0], "990927")
        self.assertEqual(row[1], "https://qr.1688.com/s/ABC123")
        self.assertEqual(row[2], "0927")

    def test_search_1688_returns_top_three_results(self) -> None:
        fake_rows = [
            {"title": "浴室垫 A", "price": "5.20", "seller": "店铺A", "url": "https://a.example"},
            {"title": "浴室垫 B", "price": "6.20", "seller": "店铺B", "url": "https://b.example"},
            {"title": "浴室垫 C", "price": "7.20", "seller": "店铺C", "url": "https://c.example"},
        ]
        with mock.patch("modules.miaoshou.api_1688.search_1688", return_value=fake_rows):
            reply = cmd_mod.handle_command("/1688 浴室垫", message_id="m-3")

        self.assertIn("1688 搜索：浴室垫", reply or "")
        self.assertIn("1. 浴室垫 A", reply or "")
        self.assertIn("2. 浴室垫 B", reply or "")
        self.assertIn("3. 浴室垫 C", reply or "")

    def test_search_1688_no_result_returns_clear_hint(self) -> None:
        with mock.patch("modules.miaoshou.api_1688.search_1688", return_value=[]):
            reply = cmd_mod.handle_command("/1688 不存在的关键词", message_id="m-4")
        self.assertIn("未找到", reply or "")


if __name__ == "__main__":
    unittest.main()
