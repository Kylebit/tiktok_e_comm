from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.hub import feishu_app, feishu_commands


class _FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DownloadImageTests(unittest.TestCase):
    def test_download_image_success(self) -> None:
        payload = b"x" * 2048

        def fake_urlopen(_req, timeout=0, context=None):
            return _FakeResponse(payload)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            data = feishu_app.download_image("https://example.com/image.jpg")

        self.assertEqual(data, payload)

    def test_download_image_retries_then_succeeds(self) -> None:
        payload = b"y" * 2048
        seen_timeouts: list[int] = []
        state = {"count": 0}

        def fake_urlopen(_req, timeout=0, context=None):
            seen_timeouts.append(timeout)
            state["count"] += 1
            if state["count"] <= 3:
                raise socket.timeout("timed out")
            return _FakeResponse(payload)

        with (
            patch("urllib.request.urlopen", side_effect=fake_urlopen),
            patch("time.sleep", return_value=None),
        ):
            data = feishu_app.download_image("https://example.com/image.jpg", timeout=10)

        self.assertEqual(data, payload)
        self.assertEqual(seen_timeouts[:3], [10, 10, 10])
        self.assertEqual(seen_timeouts[3], 20)

    def test_send_main_image_degrades_after_all_retries_fail(self) -> None:
        with (
            patch(
                "modules.hub.feishu_app._find_sku",
                return_value=[("990013", "990013", "Test Product", "https://example.com/image.jpg")],
            ),
            patch("modules.hub.feishu_app.download_image", side_effect=RuntimeError("urlopen error timed out")),
            patch("modules.hub.feishu_app.upload_image") as upload_mock,
            patch("modules.hub.feishu_app.reply_image") as reply_mock,
        ):
            reply = feishu_commands._handle_send_main_image("0013", "om_test")

        self.assertEqual(reply, "[Test Product] 图片暂时无法加载，请稍后再试")
        upload_mock.assert_not_called()
        reply_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
