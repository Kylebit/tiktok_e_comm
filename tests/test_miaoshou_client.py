import gzip
import json
import unittest
from typing import Optional
from unittest.mock import patch

from modules.miaoshou.client import request_web, web_claim_to_shop


class _FakeResponse:
    def __init__(self, payload: dict, *, gzip_body: bool = False, headers: Optional[dict] = None):
        body = json.dumps(payload).encode("utf-8")
        self.payload = gzip.compress(body) if gzip_body else body
        self.headers = headers or {}

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class MiaoshouClientTests(unittest.TestCase):
    def test_request_web_posts_form_urlencoded(self):
        seen = {}

        def fake_urlopen(req, timeout=0):
            seen["url"] = req.full_url
            seen["method"] = req.get_method()
            seen["content_type"] = req.get_header("Content-type")
            seen["data"] = req.data.decode("utf-8")
            return _FakeResponse({"code": 0, "data": {"ok": True}})

        with patch("modules.miaoshou.client._load_config", return_value={}), patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ):
            result = request_web("POST", "/api/example", form={"a": 1, "b": "x"})

        self.assertEqual(result["data"]["ok"], True)
        self.assertEqual(seen["url"], "https://erp.91miaoshou.com/api/example")
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(
            seen["content_type"],
            "application/x-www-form-urlencoded; charset=UTF-8",
        )
        self.assertEqual(seen["data"], "a=1&b=x")

    def test_request_web_decodes_gzip_and_uses_reason(self):
        def fake_urlopen(_req, timeout=0):
            return _FakeResponse(
                {"result": "fail", "code": 50001, "reason": "session expired"},
                gzip_body=True,
                headers={"Content-Encoding": "gzip"},
            )

        with patch("modules.miaoshou.client._load_config", return_value={}), patch(
            "urllib.request.urlopen", side_effect=fake_urlopen
        ):
            with self.assertRaises(RuntimeError) as ctx:
                request_web("POST", "/api/example", form={"a": 1})

        self.assertIn("session expired", str(ctx.exception))

    def test_web_claim_to_shop_uses_indexed_fields(self):
        seen = {}

        def fake_request_web(method, path, **kwargs):
            seen["method"] = method
            seen["path"] = path
            seen["form"] = kwargs["form"]
            return {"code": 0, "data": {"ok": True}}

        with patch("modules.miaoshou.client.request_web", side_effect=fake_request_web):
            web_claim_to_shop([123], [7676267, 15173238])

        self.assertEqual(seen["method"], "POST")
        self.assertEqual(
            seen["path"],
            "/api/platform/tiktok/move/collect_box/claimToShop",
        )
        self.assertEqual(
            seen["form"],
            [
                ("detailIds[0]", 123),
                ("shopIds[0]", 7676267),
                ("shopIds[1]", 15173238),
            ],
        )


if __name__ == "__main__":
    unittest.main()
