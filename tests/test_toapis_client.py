import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import requests

from modules.sourcing.toapis_client import ToAPIsClient, build_generation_payload, model_for_task
from core.http_retry import _curl_fallback_error, _retryable


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class ToAPIsClientTests(unittest.TestCase):
    def test_default_transport_uses_the_known_local_proxy(self):
        client = ToAPIsClient(api_key="secret")
        self.assertEqual(
            client._session.proxies,
            {
                "http": "http://127.0.0.1:10808",
                "https": "http://127.0.0.1:10808",
            },
        )

    def test_task_router_uses_gpt_for_text_and_gemini_for_backgrounds(self):
        self.assertEqual(model_for_task("size_card"), "gpt-image-2")
        self.assertEqual(model_for_task("text_localization"), "gpt-image-2")
        self.assertEqual(
            model_for_task("background_concept"),
            "gemini-2.5-flash-image-preview",
        )

    def test_windows_connection_reset_uses_retry_and_curl_fallback(self):
        error = ConnectionResetError(10054, "localized Windows reset message")
        self.assertTrue(_retryable(error))
        self.assertTrue(_curl_fallback_error(error))

    def test_preview_does_not_call_network(self):
        calls = []
        client = ToAPIsClient(api_key="secret", opener=lambda *a, **k: calls.append((a, k)))
        result = client.preview_generation(prompt="Clean product hero")
        self.assertEqual(result["mode"], "preview_only_no_network")
        self.assertEqual(result["payload"]["model"], "gemini-2.5-flash-image-preview")
        self.assertEqual(result["payload"]["metadata"]["resolution"], "1K")
        self.assertEqual(calls, [])

    def test_generation_is_blocked_without_explicit_gate(self):
        client = ToAPIsClient(api_key="secret", opener=lambda *_a, **_k: FakeResponse({}))
        with self.assertRaises(PermissionError):
            client.create_generation(prompt="Clean product hero")

    def test_standard_channel_rejects_documented_unsupported_size(self):
        with self.assertRaises(ValueError):
            build_generation_payload(prompt="x", model="gpt-image-2", size="16:9")
        payload = build_generation_payload(
            prompt="x", model="gpt-image-2-official", size="16:9", resolution="2k"
        )
        self.assertEqual(payload["size"], "16:9")

    def test_gemini_uses_documented_reference_image_shape(self):
        payload = build_generation_payload(
            prompt="Keep the exact product shape",
            reference_images=["https://files.toapis.com/product.png"],
        )
        self.assertEqual(
            payload["image_urls"], [{"url": "https://files.toapis.com/product.png"}]
        )
        self.assertNotIn("reference_images", payload)

    def test_gemini_is_single_image_only(self):
        with self.assertRaises(ValueError):
            build_generation_payload(prompt="x", n=2)

    def test_read_only_balance_uses_bearer_auth(self):
        seen = {}

        def opener(req, **_kwargs):
            seen["authorization"] = req.get_header("Authorization")
            seen["url"] = req.full_url
            return FakeResponse({"success": True, "unlimited_quota": True})

        result = ToAPIsClient(api_key="secret", opener=opener).balance()
        self.assertTrue(result["success"])
        self.assertEqual(seen["authorization"], "Bearer secret")
        self.assertEqual(seen["url"], "https://toapis.com/v1/balance")

    def test_upload_requires_external_transfer_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "product.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n")
            client = ToAPIsClient(api_key="secret")
            with self.assertRaises(PermissionError):
                client.upload_image(path)

    def test_generation_task_can_be_polled(self):
        responses = iter(
            [
                {"id": "task_1", "status": "in_progress"},
                {"id": "task_1", "status": "completed", "result": {"data": [{"url": "https://example.com/a.png"}]}},
            ]
        )
        client = ToAPIsClient(api_key="secret", opener=lambda *_a, **_k: FakeResponse(next(responses)))
        result = client.wait_for_generation("task_1", sleeper=lambda _seconds: None)
        self.assertEqual(result["status"], "completed")

    def test_generation_can_be_reconciled_by_client_business_id(self):
        seen = {}

        def opener(req, **_kwargs):
            seen["url"] = req.full_url
            return FakeResponse({"id": "task_1", "status": "in_progress"})

        result = ToAPIsClient(api_key="secret", opener=opener).get_generation(
            "localized-0123456789abcdef"
        )
        self.assertEqual(result["id"], "task_1")
        self.assertEqual(
            seen["url"],
            "https://toapis.com/v1/images/generations/localized-0123456789abcdef",
        )

    def test_result_download_uses_proxy_aware_fallback_after_direct_reset(self):
        class ResetSession:
            def get(self, *_args, **_kwargs):
                raise requests.ConnectionError("direct reset")

        class DownloadResponse:
            content = b"image-bytes"

            def raise_for_status(self):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "result.png"
            client = ToAPIsClient(api_key="secret", session=ResetSession())
            with patch(
                "modules.sourcing.toapis_client.requests.get",
                return_value=DownloadResponse(),
            ) as fallback:
                result = client.download_result(
                    {
                        "result": {
                            "data": [{"url": "https://files.example/result.png"}]
                        }
                    },
                    destination,
                )

            self.assertEqual(result.read_bytes(), b"image-bytes")
            fallback.assert_called_once_with(
                "https://files.example/result.png", timeout=90
            )


if __name__ == "__main__":
    unittest.main()
