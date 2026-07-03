import json
import unittest

from modules.sourcing.external_intel import build_intel_plan, normalize_intel, run_intel_plan
from modules.sourcing.linkfox_client import LinkfoxClient, validate_public_https_url


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class LinkfoxAdapterTests(unittest.TestCase):
    def test_default_plan_is_preview_only_for_four_markets(self):
        plan = build_intel_plan(keyword_cn="墙贴", page_size=10)
        self.assertEqual(plan["mode"], "preview_only_no_network")
        self.assertEqual(plan["regions"], ["PH", "MY", "TH", "VN"])
        self.assertEqual(plan["call_count"], 5)
        self.assertEqual(plan["safety"]["feedback_api"], "disabled_not_implemented")

    def test_preview_never_calls_network(self):
        calls = []

        def opener(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("network must not be called")

        client = LinkfoxClient(api_key="unused", opener=opener)
        result = run_intel_plan(build_intel_plan(keyword_cn="墙贴"), client=client)
        self.assertEqual(result["mode"], "preview_only_no_network")
        self.assertEqual(len(result["requests"]), 5)
        self.assertEqual(calls, [])

    def test_paid_gate_blocks_even_when_key_exists(self):
        client = LinkfoxClient(api_key="secret", opener=lambda *_a, **_k: FakeResponse({}))
        with self.assertRaises(PermissionError):
            client.execute(
                "echotik_product_search",
                {"region": "PH", "categoryKeywordCN": "墙贴", "pageSize": 10},
            )

    def test_image_search_rejects_local_and_private_urls(self):
        for value in ("file:///tmp/a.jpg", "http://example.com/a.jpg", "https://127.0.0.1/a.jpg"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_public_https_url(value)
        self.assertEqual(validate_public_https_url("https://example.com/a.jpg"), "https://example.com/a.jpg")

    def test_plan_rejects_invalid_page_size_and_date(self):
        with self.assertRaises(ValueError):
            build_intel_plan(keyword_cn="墙贴", page_size=5)
        with self.assertRaises(ValueError):
            build_intel_plan(keyword_cn="墙贴", new_rank_date="2026/06/27")

    def test_normalize_combines_demand_and_supply(self):
        normalized = normalize_intel(
            {
                "echotik_products_PH": {"products": [{"totalSale30dCnt": 12}]},
                "alibaba1688_suppliers": {"products": [{"price": 4.5}]},
            }
        )
        self.assertEqual(normalized["markets"]["PH"]["top_30d_sales"], 12)
        self.assertEqual(normalized["supply"]["min_price_cny"], 4.5)


if __name__ == "__main__":
    unittest.main()
