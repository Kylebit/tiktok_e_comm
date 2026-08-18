from __future__ import annotations

from pathlib import Path
import io
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import _common
import publish_approved_product
from _classification import classify
from inspect_snapshot import build_snapshot
from dispatch_shopee import retire_deleted_entry
import dispatch_tiktok
import readback_shopee
import readback_tiktok
import readback_ozon


def dashboard_fixture() -> dict:
    keys = ["variant-a", "variant-b", "variant-c"]
    return {
        "product": {
            "offer_id": "3838619319",
            "seller_sku_candidate": "0960",
            "revision": 40,
            "selected_sku_keys": keys,
            "source_skus": [
                {"key": key, "label": f"Option {index}", "model_sku": f"096{index}", "price_cny": 10 + index}
                for index, key in enumerate(keys)
            ],
            "sku_commercial_facts": {
                key: {"cost_cny": 10 + index, "weight_kg": 0.2 + index / 10, "package_cm": [20 + index, 5, 5]}
                for index, key in enumerate(keys)
            },
            "category": {"id": "", "name": "Wall sticker"},
        },
        "release_v1": {
            "plan_approved": True,
            "plan": {
                "plan_id": "plan-1",
                "payload_digest": "a" * 64,
                "targets_digest": "b" * 64,
                "confirmation_token": "confirm-1",
                "targets": ["tiktok:LH_PH", "shopee:PH", "ozon:RU"],
            },
        },
        "listing_copy": {
            "semantic_master_en": "Approved title",
            "shopee_description_en": "Approved description",
            "candidates": [
                {"channel": "tiktok", "site": "PH", "title": "TikTok title", "policy_check": "passed"},
                {"channel": "shopee", "site": "CNSC", "title": "Shopee title", "policy_check": "passed"},
                {"channel": "ozon", "site": "RU", "title": "Ozon title", "policy_check": "passed"},
            ],
        },
        "content": {
            "images": [
                {"position": 1, "image_url": "https://example.test/1.jpg"},
                {"position": 2, "image_url": "https://example.test/2.jpg"},
            ],
            "video_urls": [],
        },
        "publication_scope": {
            "available_targets": [
                {"label": "tiktok:LH_PH", "target_key": "lh_ph"},
                {"label": "shopee:PH"},
                {"label": "ozon:RU"},
            ]
        },
        "pricing_review": {
            "selected_store_prices": [
                {"target_key": "lh_ph", "currency": "PHP", "list_price": 523, "sale_after_discount": 339.95}
            ],
            "target_pricing": {
                "tiktok:LH_PH": {
                    "sku_prices": [
                        {
                            "variant_key": key,
                            "model_sku": f"096{index}",
                            "currency": "PHP",
                            "list_price": price,
                        }
                        for index, (key, price) in enumerate(zip(keys, (523, 641, 777)))
                    ]
                },
                "shopee:PH": {
                    "derived_preview": {
                        "global_original_price_cny": 76.58,
                        "local_original_price": 523,
                        "source_currency": "PHP",
                    },
                    "sku_prices": [
                        {
                            "variant_key": key,
                            "model_sku": f"096{index}",
                            "derived_preview": {"global_original_price_cny": price},
                        }
                        for index, (key, price) in enumerate(
                            zip(keys, (76.58, 97.23, 121.66))
                        )
                    ],
                },
                "ozon:RU": {
                    "derived_preview": {"price_cny": 77, "old_price_cny": 100},
                    "sku_prices": [
                        {
                            "variant_key": key,
                            "model_sku": f"096{index}",
                            "derived_preview": {
                                "price_cny": price,
                                "old_price_cny": old_price,
                            },
                        }
                        for index, (key, price, old_price) in enumerate(
                            zip(keys, (77, 97, 122), (100, 126, 159))
                        )
                    ],
                },
            },
        },
    }


class SnapshotTests(unittest.TestCase):
    def test_snapshot_keeps_all_skus_and_per_sku_commercial_facts(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        self.assertEqual([row["seller_sku"] for row in snapshot["skus"]], ["0960", "0961", "0962"])
        self.assertEqual(snapshot["skus"][2]["option_name"], "Option 2")
        self.assertEqual(snapshot["skus"][1]["cost_cny"], 11)
        self.assertEqual(snapshot["skus"][1]["weight_kg"], 0.30000000000000004)
        self.assertEqual(snapshot["skus"][1]["package_cm"], [21, 5, 5])

    def test_snapshot_keeps_exact_per_model_tiktok_prices(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        self.assertEqual(
            snapshot["prices"]["tiktok:LH_PH"]["sku_prices"],
            {
                "0960": {"variant_key": "variant-a", "currency": "PHP", "list_price": 523},
                "0961": {"variant_key": "variant-b", "currency": "PHP", "list_price": 641},
                "0962": {"variant_key": "variant-c", "currency": "PHP", "list_price": 777},
            },
        )

    def test_snapshot_keeps_exact_shopee_and_ozon_per_model_prices(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        self.assertEqual(
            {
                key: snapshot["prices"]["shopee:PH"][key]
                for key in ("global_original_price_cny", "local_original_price", "currency")
            },
            {
                "global_original_price_cny": 76.58,
                "local_original_price": 523,
                "currency": "PHP",
            },
        )
        self.assertEqual(
            snapshot["prices"]["shopee:PH"]["sku_prices"],
            {
                "0960": {"variant_key": "variant-a", "list_price": 76.58},
                "0961": {"variant_key": "variant-b", "list_price": 97.23},
                "0962": {"variant_key": "variant-c", "list_price": 121.66},
            },
        )
        self.assertEqual(
            snapshot["prices"]["ozon:RU"]["sku_prices"],
            {
                "0960": {"variant_key": "variant-a", "price": 77, "old_price": 100},
                "0961": {"variant_key": "variant-b", "price": 97, "old_price": 126},
                "0962": {"variant_key": "variant-c", "price": 122, "old_price": 159},
            },
        )

    def test_snapshot_rejects_malformed_shopee_price_shapes(self) -> None:
        fixture = dashboard_fixture()
        fixture["pricing_review"]["target_pricing"]["shopee:PH"] = {
            "sku_prices": [{"platform_owned_shape": True}]
        }
        with self.assertRaisesRegex(ValueError, "Shopee per-SKU price"):
            build_snapshot(fixture, "3838619319")

    def test_three_platform_plans_are_independent(self) -> None:
        fixture = dashboard_fixture()
        fixture["release_v1"]["plan"]["targets"] = ["tiktok:LH_PH", "ozon:RU"]
        snapshot = build_snapshot(fixture, "3838619319")
        self.assertTrue(snapshot["platforms"]["tiktok"]["selected"])
        self.assertFalse(snapshot["platforms"]["shopee"]["selected"])
        self.assertTrue(snapshot["platforms"]["ozon"]["selected"])

    def test_category_without_platform_id_is_warning_not_blocker(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        for platform in ("tiktok", "shopee", "ozon"):
            self.assertEqual(snapshot["platforms"][platform]["blocking_reasons"], [])
            self.assertTrue(snapshot["platforms"][platform]["warnings"])


class DispatchTests(unittest.TestCase):
    def test_tiktok_non_200_preflight_blocks_publish(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        with patch.object(
            dispatch_tiktok,
            "json_request",
            return_value=(503, {"message": "status unavailable"}),
        ) as request:
            fact = dispatch_tiktok.dispatch_with_fresh_drafts(
                snapshot,
                base_url="http://local",
                timeout_seconds=1,
                execute=True,
            )

        self.assertFalse(fact["accepted"])
        self.assertEqual(fact["write_outcome"], "REJECTED")
        self.assertEqual(request.call_count, 1)

    def test_tiktok_preflight_without_one_tiktok_row_blocks_publish(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        with patch.object(
            dispatch_tiktok,
            "json_request",
            return_value=(200, {"action": {"platforms": []}}),
        ) as request:
            fact = dispatch_tiktok.dispatch_with_fresh_drafts(
                snapshot,
                base_url="http://local",
                timeout_seconds=1,
                execute=True,
            )

        self.assertFalse(fact["accepted"])
        self.assertEqual(fact["write_outcome"], "REJECTED")
        self.assertEqual(request.call_count, 1)

    def test_tiktok_dispatch_fact_preserves_explicit_unknown_target(self) -> None:
        fact = dispatch_tiktok._fact(
            200,
            {
                "ok": False,
                "success": False,
                "unknown_target_count": 1,
                "external_write_count": None,
                "targets": [
                    {"target_label": "tiktok:GB", "outcome": "UNKNOWN"}
                ],
            },
            prepared=False,
        )
        self.assertEqual(fact["write_outcome"], "UNKNOWN")

    def test_tiktok_target_scope_is_sent_only_to_publish_request(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        responses = iter([
            (
                200,
                {
                    "action": {
                        "platforms": [
                            {
                                "platform": "TIKTOK",
                                "publishable": True,
                                "publishable_targets": ["tiktok:LH_PH"],
                            }
                        ]
                    }
                },
            ),
            (200, {"ok": True, "success": True, "target_count": 1}),
        ])
        calls = []

        def request(url, **kwargs):
            calls.append((url, kwargs.get("payload")))
            return next(responses)

        with patch.object(dispatch_tiktok, "json_request", side_effect=request):
            result = dispatch_tiktok.dispatch_with_fresh_drafts(
                snapshot,
                base_url="http://local",
                timeout_seconds=1,
                execute=True,
                target_labels=["tiktok:LH_PH"],
            )

        self.assertTrue(result["accepted"])
        self.assertIsNone(calls[0][1])
        self.assertEqual(
            calls[1][1]["tiktok_target_scope"],
            ["tiktok:LH_PH"],
        )

    def test_tiktok_target_scope_rejects_unapproved_store_before_request(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        with patch.object(dispatch_tiktok, "json_request") as request:
            with self.assertRaisesRegex(ValueError, "approved TikTok targets"):
                dispatch_tiktok.dispatch_with_fresh_drafts(
                    snapshot,
                    base_url="http://local",
                    timeout_seconds=1,
                    execute=True,
                    target_labels=["tiktok:GB"],
                )
        request.assert_not_called()

    def test_tiktok_preflights_partial_batch_before_any_store_submission(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        selected = list(snapshot["platforms"]["tiktok"]["targets"])
        selected.append("tiktok:LH_MY")
        snapshot["platforms"]["tiktok"]["targets"] = selected
        responses = iter(
            [
                (
                    200,
                    {
                        "action": {
                            "platforms": [
                                {
                                    "platform": "TIKTOK",
                                    "publishable": True,
                                    "publishable_targets": selected[:1],
                                }
                            ]
                        }
                    },
                ),
                (
                    200,
                    {
                        "action": {
                            "platforms": [
                                {
                                    "platform": "TIKTOK",
                                    "publishable": True,
                                    "publishable_targets": selected,
                                }
                            ]
                        }
                    },
                ),
                (200, {"ok": True, "success": True, "target_count": 6}),
            ]
        )
        calls = []

        def request(url, **kwargs):
            calls.append((url, kwargs.get("payload")))
            return next(responses)

        with patch.object(dispatch_tiktok, "json_request", side_effect=request):
            result = dispatch_tiktok.dispatch_with_fresh_drafts(
                snapshot,
                base_url="http://local",
                timeout_seconds=1,
                execute=True,
            )

        self.assertTrue(result["accepted"])
        self.assertEqual(
            [row[0].split("http://local", 1)[1].split("?", 1)[0] for row in calls],
            [
                "/api/product-workspace/collectbox-action/status",
                "/api/product-workspace/collectbox-action/start",
                "/api/product-workspace/publish-tiktok",
            ],
        )

    def test_tiktok_pristine_plan_starts_first_batch_without_restart(self) -> None:
        """A never-run plan must not be misclassified as a reimport."""

        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        selected = list(snapshot["platforms"]["tiktok"]["targets"])
        responses = iter(
            [
                (
                    200,
                    {
                        "persisted": False,
                        "action": {
                            "status": "READY",
                            "start_allowed": True,
                            "platforms": [
                                {
                                    "platform": "TIKTOK",
                                    "status": "PENDING",
                                    "attempt_count": 0,
                                    "publishable": False,
                                    # Durable pristine rows use null until the
                                    # first attempt records an exact count.
                                    "external_writes": {"count": None, "classes": []},
                                }
                            ],
                        },
                    },
                ),
                (
                    200,
                    {
                        "action": {
                            "platforms": [
                                {
                                    "platform": "TIKTOK",
                                    "status": "SUCCEEDED",
                                    "publishable": True,
                                    "publishable_targets": selected,
                                }
                            ]
                        }
                    },
                ),
                (200, {"ok": True, "success": True, "target_count": 6}),
            ]
        )
        calls = []

        def request(url, **kwargs):
            calls.append((url, kwargs.get("payload")))
            return next(responses)

        with patch.object(dispatch_tiktok, "json_request", side_effect=request):
            result = dispatch_tiktok.dispatch_with_fresh_drafts(
                snapshot,
                base_url="http://local",
                timeout_seconds=1,
                execute=True,
            )

        self.assertTrue(result["accepted"])
        prepare_request = calls[1][1]
        self.assertEqual(prepare_request["platform_scope"], "TIKTOK")
        self.assertNotIn("restart_collectbox_action", prepare_request)
        self.assertNotIn("reimport_request_id", prepare_request)

    def test_tiktok_stops_when_fresh_batch_preparation_is_not_publishable(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        responses = iter([
            (
                200,
                {
                    "action": {
                        "platforms": [
                            {
                                "platform": "TIKTOK",
                                "status": "SUCCEEDED",
                                "publishable": True,
                            }
                        ]
                    }
                },
            ),
            (409, {"ok": False, "message": "draft identity is incomplete"}),
            (
                200,
                {
                    "action": {
                        "platforms": [
                            {
                                "platform": "TIKTOK",
                                "status": "RECONCILIATION_REQUIRED",
                                "publishable": False,
                                "error": {
                                    "code": "collectbox_platform_preparation_partial"
                                },
                            }
                        ]
                    }
                },
            ),
        ])
        calls = []

        def request(url, **kwargs):
            calls.append(url)
            return next(responses)

        with patch.object(dispatch_tiktok, "json_request", side_effect=request):
            result = dispatch_tiktok.dispatch_with_fresh_drafts(
                snapshot,
                base_url="http://local",
                timeout_seconds=1,
                execute=True,
            )

        self.assertFalse(result["accepted"])
        self.assertFalse(result["fresh_draft_batch_created"])
        self.assertIn("collectbox_platform_preparation_partial", result["message"])
        self.assertEqual(
            [url.split("http://local", 1)[1].split("?", 1)[0] for url in calls],
            [
                "/api/product-workspace/collectbox-action/status",
                "/api/product-workspace/publish-tiktok",
                "/api/product-workspace/collectbox-action/start",
            ],
        )

    def test_tiktok_missing_draft_identity_creates_fresh_batch_then_publishes(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        responses = iter([
            (
                200,
                {
                    "action": {
                        "platforms": [
                            {
                                "platform": "TIKTOK",
                                "status": "SUCCEEDED",
                                "publishable": True,
                            }
                        ]
                    }
                },
            ),
            (409, {"ok": False, "message": "批准快照或妙手草稿身份不完整"}),
            (
                200,
                {
                    "action": {
                        "platforms": [
                            {
                                "platform": "TIKTOK",
                                "status": "SUCCEEDED",
                                "publishable": True,
                            }
                        ]
                    }
                },
            ),
            (200, {"ok": True, "success": True, "target_count": 1}),
        ])
        calls = []

        def request(url, **kwargs):
            calls.append((url, kwargs.get("payload")))
            return next(responses)

        with patch.object(dispatch_tiktok, "json_request", side_effect=request):
            result = dispatch_tiktok.dispatch_with_fresh_drafts(
                snapshot,
                base_url="http://local",
                timeout_seconds=1,
                execute=True,
            )

        self.assertTrue(result["accepted"])
        self.assertEqual(
            [row[0].split("http://local", 1)[1].split("?", 1)[0] for row in calls],
            [
                "/api/product-workspace/collectbox-action/status",
                "/api/product-workspace/publish-tiktok",
                "/api/product-workspace/collectbox-action/start",
                "/api/product-workspace/publish-tiktok",
            ],
        )
        self.assertEqual(calls[2][1]["platform_scope"], "TIKTOK")
        self.assertTrue(calls[2][1]["restart_collectbox_action"])

    def test_orchestrator_uses_repository_python_for_repo_importing_tools(self) -> None:
        expected = Path(r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm\.venv\Scripts\python.exe")
        self.assertEqual(
            publish_approved_product._tool_python(
                r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm"
            ),
            expected,
        )

    def test_orchestrator_always_passes_resolved_repo_to_tiktok_readback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            snapshot_path = directory / "snapshot.json"
            snapshot_path.write_text("{}", encoding="utf-8")
            args = type(
                "Args",
                (),
                {
                    "repo": None,
                    "base_url": "http://local",
                    "timeout_seconds": 1,
                },
            )()
            commands: list[list[str]] = []

            def run_tool(arguments, output, *, executable):
                commands.append(arguments)
                if "dispatch_tiktok.py" in arguments[0]:
                    return {"accepted": True, "write_outcome": "ACCEPTED"}
                return {"verified": False, "status": "UNAVAILABLE"}

            with (
                patch.object(
                    publish_approved_product,
                    "_tool_python",
                    return_value=Path(sys.executable),
                ),
                patch.object(
                    publish_approved_product,
                    "_run_tool",
                    side_effect=run_tool,
                ),
            ):
                publish_approved_product._platform_run(
                    "tiktok",
                    snapshot_path=snapshot_path,
                    directory=directory,
                    args=args,
                )

        readback_command = commands[1]
        self.assertIn("--repo", readback_command)
        self.assertEqual(
            readback_command[readback_command.index("--repo") + 1],
            str(_common.DEFAULT_REPO.resolve()),
        )

    def test_orchestrator_preserves_dispatch_fact_and_runs_readback_after_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            snapshot_path = directory / "snapshot.json"
            snapshot_path.write_text("{}", encoding="utf-8")
            args = type(
                "Args",
                (),
                {
                    "repo": str(_common.DEFAULT_REPO),
                    "base_url": "http://local",
                    "timeout_seconds": 1,
                },
            )()
            calls = 0

            def run_tool(arguments, output, *, executable):
                nonlocal calls
                calls += 1
                if calls == 1:
                    output.write_text(
                        json.dumps(
                            {
                                "platform": "tiktok",
                                "attempted": True,
                                "accepted": True,
                                "write_outcome": "ACCEPTED",
                            }
                        ),
                        encoding="utf-8",
                    )
                    raise subprocess.TimeoutExpired(arguments, timeout=1)
                self.assertTrue((directory / "tiktok-dispatch.json").is_file())
                return {"verified": False, "status": "UNAVAILABLE"}

            with (
                patch.object(
                    publish_approved_product,
                    "_tool_python",
                    return_value=Path(sys.executable),
                ),
                patch.object(
                    publish_approved_product,
                    "_run_tool",
                    side_effect=run_tool,
                ),
            ):
                row = publish_approved_product._platform_run(
                    "tiktok",
                    snapshot_path=snapshot_path,
                    directory=directory,
                    args=args,
                )

        self.assertEqual(calls, 2)
        self.assertTrue(row["dispatch"]["accepted"])
        self.assertEqual(row["readback"]["status"], "UNAVAILABLE")

    def test_runner_report_projection_excludes_snapshot_secrets_urls_and_raw_response(self) -> None:
        unsafe = {
            "schema_version": "approved-product-execution-report/v3",
            "error": (
                "confirmation_token=never-print-confirmation "
                "secret=never-print-secret https://private.example/error"
            ),
            "snapshot": {
                "schema_version": "approved-publication-snapshot/v3",
                "identity": {
                    "offer_id": "3838619319",
                    "revision": 40,
                    "plan_id": "plan-1",
                    "snapshot_digest": "a" * 64,
                },
                "request": {"confirmation_token": "never-print-this"},
                "content": {
                    "images": ["https://private.example/image.jpg"],
                    "video_urls": ["https://private.example/video.mp4"],
                },
            },
            "platforms": [
                {
                    "platform": "tiktok",
                    "result": {"code": "PROCESSING"},
                    "dispatch": {
                        "accepted": True,
                        "approved_snapshot": {"title": "full snapshot leak"},
                        "raw_response": {
                            "access_token": "never-print-token",
                            "image_url": "https://private.example/raw.jpg",
                        },
                    },
                    "readback": {
                        "status": "UNAVAILABLE",
                        "message": "provider https://private.example/detail",
                    },
                }
            ],
        }

        safe = publish_approved_product._redacted_report(unsafe)
        encoded = json.dumps(safe, ensure_ascii=False)

        self.assertNotIn("snapshot", safe)
        self.assertEqual(
            safe["snapshot_identity"]["schema_version"],
            "approved-publication-snapshot/v3",
        )
        for forbidden in (
            "confirmation_token",
            "never-print-this",
            "never-print-confirmation",
            "never-print-secret",
            "never-print-token",
            "full snapshot leak",
            "private.example",
            "raw_response",
            "video_urls",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_report_path_is_fixed_to_offer_revision_and_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp).resolve()
            valid = (
                repo
                / "reports"
                / "product-publication"
                / "3838619319"
                / "40"
                / "run-001"
                / "report.json"
            )
            location = publish_approved_product._validated_report_path(
                valid,
                repo=repo,
                offer_id="3838619319",
                revision=40,
            )
            self.assertEqual(location.path, valid)
            self.assertEqual(location.run_id, "run-001")
            with self.assertRaises(ValueError):
                publish_approved_product._validated_report_path(
                    repo / "report.json",
                    repo=repo,
                    offer_id="3838619319",
                    revision=40,
                )
            with self.assertRaises(ValueError):
                publish_approved_product._validated_report_path(
                    valid.parent.parent.parent / "41" / "run-001" / "report.json",
                    repo=repo,
                    offer_id="3838619319",
                    revision=40,
                )

    def test_report_is_atomic_and_never_overwrites_existing_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "report.json"
            publish_approved_product._write_report_atomic(
                report, {"ok": True, "run_id": "run-001"}
            )
            self.assertEqual(_common.load_json(report)["run_id"], "run-001")
            self.assertEqual(list(report.parent.glob(".report.json.*.tmp")), [])
            with self.assertRaises(FileExistsError):
                publish_approved_product._write_report_atomic(
                    report, {"ok": False, "run_id": "run-002"}
                )

    def test_unified_runner_requires_report_path(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        argv = [
            "publish_approved_product.py",
            "inspect",
            "--offer-id",
            "3838619319",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(
                publish_approved_product,
                "_tool_python",
                return_value=Path(sys.executable),
            ),
            patch.object(
                publish_approved_product,
                "_inspect",
                return_value=snapshot,
            ),
            patch.object(publish_approved_product, "emit"),
        ):
            with self.assertRaises(SystemExit) as raised:
                publish_approved_product.main()
        self.assertEqual(raised.exception.code, 2)

    def test_deleted_shopee_mapping_is_retired_without_touching_other_entries(self) -> None:
        original = {
            "101": {"match_key": "0960", "match_keys": ["0960"], "title": "old"},
            "202": {"match_key": "0999", "title": "other"},
        }
        result = retire_deleted_entry(
            original,
            old_global_item_id="101",
            seller_sku="0960",
        )
        self.assertEqual(result["101"]["match_key"], "")
        self.assertEqual(result["101"]["retired_match_key"], "0960")
        self.assertEqual(result["101"]["retired_reason"], "official_global_status_deleted")
        self.assertEqual(result["202"], original["202"])
        self.assertEqual(original["101"]["match_key"], "0960")

    def test_emit_supports_non_gbk_platform_titles(self) -> None:
        stream = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
        with patch.object(sys, "stdout", stream):
            _common.emit({"title": "สติ๊กเกอร์ติดผนัง"})
            stream.flush()
            stream.buffer.seek(0)
            self.assertIn("สติ๊กเกอร์ติดผนัง", stream.buffer.read().decode("utf-8"))

    def test_acceptance_is_only_dispatch_fact_not_success_classification(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        with patch.object(_common, "json_request", return_value=(200, {"ok": True, "success": True, "global_item_id": 99})):
            fact = _common.dispatch_fact(
                platform="shopee",
                endpoint="/publish",
                snapshot=snapshot,
                base_url="http://local",
                timeout_seconds=1,
                execute=True,
            )
        self.assertTrue(fact["accepted"])
        self.assertNotIn("result", fact)
        result = classify(fact, {"status": "UNAVAILABLE", "verified": False})
        self.assertEqual(result["label_zh"], "平台处理中")

    def test_one_platform_rejection_does_not_change_another_fact(self) -> None:
        first = classify({"accepted": False}, {"status": "NOT_FOUND", "exists": False})
        second = classify({"accepted": True}, {"status": "VERIFIED", "verified": True, "complete": True})
        self.assertEqual(first["label_zh"], "发布失败")
        self.assertEqual(second["label_zh"], "发布成功")


class DispatchPrecedenceTests(unittest.TestCase):
    def test_rejected_dispatch_cannot_be_overridden_by_draft_readback(self) -> None:
        result = classify(
            {"accepted": False, "write_outcome": "REJECTED"},
            {
                "status": "VERIFIED",
                "verified": True,
                "complete": True,
                "exists": True,
                "provider": "miaoshou_collectbox_receipt",
            },
        )

        self.assertEqual(result["code"], "FAILED")


class ReadbackClassificationTests(unittest.TestCase):
    def test_ozon_imported_is_processing_not_mismatch(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        response = {
            "items": [
                {
                    "offer_id": sku,
                    "id": str(4000 + index),
                    "name": "Ozon title",
                    "price": str(price),
                    "images": ["https://example.test/image.jpg"],
                    "statuses": {
                        "status": "IMPORTED",
                        "is_created": False,
                        "status_failed": "",
                    },
                }
                for index, (sku, price) in enumerate(
                    zip(("0960", "0961", "0962"), (77, 97, 122)), start=1
                )
            ]
        }
        args = type(
            "Args",
            (),
            {"repo": str(_common.DEFAULT_REPO), "timeout_seconds": 1},
        )()
        with patch("modules.ozon.client.ozon_post", return_value=response):
            fact = readback_ozon.readback(snapshot, {"accepted": True}, args)

        self.assertEqual(fact["status"], "PROCESSING")
        self.assertFalse(fact["mismatch"])

    def test_tiktok_exact_readback_uses_full_durable_plan_not_redacted_dashboard(self) -> None:
        redacted_plan = {"plan_id": "plan-1", "payload": {"product_revision": 9}}
        full_plan = {
            "plan_id": "plan-1",
            "payload": {
                "product_facts": {
                    "category": {"name": "居家布艺 > 桌旗", "confidence": "approved"}
                }
            },
        }
        current = {"release_v1": {"plan": redacted_plan}}
        captured = []

        class FakeReleaseStore:
            path = "memory.sqlite"

            def get_plan(self, plan_id):
                self_plan_id = plan_id
                self.last_plan_id = self_plan_id
                return full_plan

        class FakeCollectBoxStore:
            def __init__(self, _path):
                pass

            def internal_tiktok_publish_contexts(self, *, plan_id):
                self.plan_id = plan_id
                return {"tiktok:GB": {"detail_id": "123"}}

        class FakeTransport:
            def read_draft(self, target):
                self.target = target
                return {"info": {"detailId": "123"}}

            def post_submit_draft_matches(self, target, draft):
                self.observed = (target, draft)
                return True

        def build(plan, *, collectbox_contexts):
            captured.append((plan, collectbox_contexts))
            return {"targets": [{"target_label": "tiktok:GB"}]}

        args = type("Args", (), {"repo": str(_common.DEFAULT_REPO)})()
        _common.add_repo_to_path(args.repo)
        with (
            patch("shared_platform.release_store.default_release_store", return_value=FakeReleaseStore()),
            patch("shared_platform.collectbox_action.CollectBoxActionStore", FakeCollectBoxStore),
            patch("shared_platform.product_snapshot.build_approved_tiktok_publish_snapshot", side_effect=build),
            patch("modules.miaoshou.tiktok_publisher.MiaoshouTikTokTransport", FakeTransport),
        ):
            result = readback_tiktok._exact_draft_readback(current, args)

        self.assertEqual(result, {"tiktok:GB": "READY"})
        self.assertIs(captured[0][0], full_plan)

    def test_tiktok_scoped_dispatch_reads_back_only_dispatched_targets(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        snapshot["platforms"]["tiktok"]["targets"] = [
            "tiktok:LH_PH",
            "tiktok:GB",
        ]
        args = type(
            "Args",
            (),
            {"base_url": "http://local", "timeout_seconds": 1},
        )()
        current = {
            "history": [
                {
                    "platform": "TIKTOK",
                    "target_outcomes": [
                        {"target_label": "tiktok:LH_PH", "status": "SUCCEEDED"},
                        {"target_label": "tiktok:GB", "status": "SUCCEEDED"},
                    ],
                }
            ]
        }

        with patch.object(readback_tiktok, "dashboard", return_value=current):
            fact = readback_tiktok.readback(
                snapshot,
                {
                    "accepted": True,
                    "write_outcome": "ACCEPTED",
                    "safe_response": {
                        "targets": [
                            {"target_label": "tiktok:GB", "outcome": "ACCEPTED"}
                        ]
                    },
                },
                args,
            )

        self.assertEqual(fact["expected_count"], 1)
        self.assertEqual(
            [row["target_label"] for row in fact["targets"]],
            ["tiktok:GB"],
        )

    def test_tiktok_local_success_ledger_is_not_storefront_verification(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        args = type(
            "Args",
            (),
            {"base_url": "http://local", "timeout_seconds": 1},
        )()
        current = {
            "history": [
                {
                    "platform": "TIKTOK",
                    "target_outcomes": [
                        {"target_label": "tiktok:LH_PH", "status": "SUCCEEDED"}
                    ],
                }
            ]
        }

        with patch.object(readback_tiktok, "dashboard", return_value=current):
            fact = readback_tiktok.readback(
                snapshot,
                {
                    "accepted": True,
                    "write_outcome": "ACCEPTED",
                    "safe_response": {
                        "targets": [
                            {"target_label": "tiktok:LH_PH", "outcome": "ACCEPTED"}
                        ]
                    },
                },
                args,
            )

        self.assertEqual(fact["status"], "UNAVAILABLE")
        self.assertFalse(fact["verified"])
        self.assertFalse(fact["complete"])
        self.assertEqual(fact["verified_count"], 0)
        self.assertEqual(fact["targets"][0]["verification"], "UNAVAILABLE")
        self.assertEqual(
            fact["targets"][0]["price_category_variant_check"],
            "UNVERIFIED",
        )

    def test_shopee_wrong_title_or_model_price_is_not_verified(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        args = type(
            "Args",
            (),
            {"repo": str(_common.DEFAULT_REPO), "timeout_seconds": 1},
        )()
        item = {
            "global_item_id": 123,
            "global_item_status": "NORMAL",
            "global_item_name": "Wrong stale title",
            "description": "Approved description",
            "image": {"image_id_list": ["image-1"]},
            "tier_variation": [{
                "name": "Size",
                "option_list": [
                    {"option": "Option 0"},
                    {"option": "Option 1"},
                    {"option": "Option 2"},
                ],
            }],
        }
        models = [
            {"global_model_sku": "0960", "tier_index": [0], "original_price": 76.58},
            {"global_model_sku": "0961", "tier_index": [1], "original_price": 76.58},
            {"global_model_sku": "0962", "tier_index": [2], "original_price": 76.58},
        ]
        responses = iter([
            {"error": "", "response": {"global_item_list": [item]}},
            {"error": "", "response": {
                "global_model": models,
                "tier_variation": [{
                    "name": "Variation",
                    "option_list": [
                        {
                            "option": row["option_name"],
                            "image": {"image_id": f"variant-image-{index}"},
                        }
                        for index, row in enumerate(snapshot["skus"])
                    ],
                }],
            }},
        ])
        with (
            patch("modules.shopee.auth.ensure_shop_token", return_value="token"),
            patch("modules.shopee.shops.sync_shop_ids", return_value={"PH": 1}),
            patch("modules.shopee.publish._shop_meta", return_value={"merchant_id": 2}),
            patch("modules.shopee.publish._merchant_token", return_value="merchant-token"),
            patch("modules.shopee.client.merchant_get", side_effect=lambda *_a, **_k: next(responses)),
        ):
            fact = readback_shopee.readback(
                snapshot,
                {"platform_item_id": "123", "accepted": True},
                args,
            )

        self.assertEqual(fact["status"], "MISMATCH")
        self.assertFalse(fact["checks"]["title_exact"])
        self.assertFalse(fact["checks"]["model_prices_exact"])

    def test_shopee_provider_omitted_option_labels_use_exact_tier_binding(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        args = type(
            "Args",
            (),
            {"repo": str(_common.DEFAULT_REPO), "timeout_seconds": 1},
        )()
        title = next(
            row["title"]
            for row in snapshot["content"]["platform_titles"]
            if row["channel"] == "shopee"
        )
        prices = snapshot["prices"]["shopee:PH"]["sku_prices"]
        models = [
            {
                "global_model_sku": row["seller_sku"],
                "tier_index": [index],
                "price_info": {
                    "original_price": prices[row["seller_sku"]]["list_price"]
                },
            }
            for index, row in enumerate(snapshot["skus"])
        ]
        item = {
            "global_item_id": 123,
            "global_item_status": "NORMAL",
            "global_item_name": title,
            "description": snapshot["content"]["description"],
            "image": {"image_id_list": ["image-1"]},
            "tier_variation": None,
            "weight": max(float(row["weight_kg"]) for row in snapshot["skus"]),
            "dimension": {
                "package_length": max(float(row["package_cm"][0]) for row in snapshot["skus"]),
                "package_width": max(float(row["package_cm"][1]) for row in snapshot["skus"]),
                "package_height": max(float(row["package_cm"][2]) for row in snapshot["skus"]),
            },
        }
        responses = iter([
            {"error": "", "response": {"global_item_list": [item]}},
            {"error": "", "response": {
                "global_model": models,
                "tier_variation": [{
                    "name": "Variation",
                    "option_list": [
                        {
                            "option": row["option_name"],
                            "image": {"image_id": f"variant-image-{index}"},
                        }
                        for index, row in enumerate(snapshot["skus"])
                    ],
                }],
            }},
        ])
        with (
            patch("modules.shopee.auth.ensure_shop_token", return_value="token"),
            patch("modules.shopee.shops.sync_shop_ids", return_value={"PH": 1}),
            patch("modules.shopee.publish._shop_meta", return_value={"merchant_id": 2}),
            patch("modules.shopee.publish._merchant_token", return_value="merchant-token"),
            patch("modules.shopee.client.merchant_get", side_effect=lambda *_a, **_k: next(responses)),
        ):
            fact = readback_shopee.readback(
                snapshot,
                {"platform_item_id": "123", "accepted": True},
                args,
            )

        self.assertTrue(fact["verified"])
        self.assertTrue(fact["checks"]["option_names_exact"])
        self.assertTrue(fact["checks"]["tier_indexes_exact"])
        self.assertTrue(fact["checks"]["variant_images_present"])
        self.assertEqual(fact["observed"]["variant_image_count"], 3)
        self.assertEqual(
            fact["observed"]["option_names_check"],
            "EXACT",
        )

    def test_ozon_offer_validated_is_processing_not_mismatch(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        response = {
            "items": [
                {
                    "offer_id": sku,
                    "id": str(5000 + index),
                    "name": "Ozon title",
                    "price": str(price),
                    "images": ["https://example.test/image.jpg"],
                    "statuses": {
                        "status": "OFFER_VALIDATED",
                        "is_created": False,
                        "status_failed": "",
                    },
                }
                for index, (sku, price) in enumerate(
                    zip(("0960", "0961", "0962"), (77, 97, 122)), start=1
                )
            ]
        }
        repo = r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm"
        readback_ozon.add_repo_to_path(repo)
        args = type("Args", (), {"repo": repo, "timeout_seconds": 1})()
        with patch("modules.ozon.client.ozon_post", return_value=response):
            fact = readback_ozon.readback(snapshot, {"accepted": True}, args)

        self.assertEqual(fact["status"], "PROCESSING")
        self.assertFalse(fact["mismatch"])
        self.assertFalse(fact["retry_safe"])

    def test_ozon_executable_readback_polls_processing_until_verified(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        processing = {
            "items": [
                {
                    "offer_id": sku,
                    "id": str(5000 + index),
                    "name": "Ozon title",
                    "price": str(price),
                    "images": ["https://example.test/image.jpg"],
                    "statuses": {
                        "status": "OFFER_VALIDATED",
                        "is_created": False,
                        "status_failed": "",
                    },
                }
                for index, (sku, price) in enumerate(
                    zip(("0960", "0961", "0962"), (77, 97, 122)), start=1
                )
            ]
        }
        created = {
            "items": [
                {
                    **row,
                    "statuses": {
                        "status": "PRICE_SENT",
                        "is_created": True,
                        "status_failed": "",
                    },
                }
                for row in processing["items"]
            ]
        }
        repo = str(_common.DEFAULT_REPO)
        args = type(
            "Args",
            (),
            {
                "repo": repo,
                "timeout_seconds": 1,
                "poll_attempts": 2,
                "poll_interval_seconds": 0,
            },
        )()
        with patch(
            "modules.ozon.client.ozon_post",
            side_effect=(processing, created),
        ) as provider:
            fact = readback_ozon.readback(snapshot, {"accepted": True}, args)

        self.assertEqual(provider.call_count, 2)
        self.assertEqual(fact["status"], "VERIFIED")
        self.assertTrue(fact["verified"])

    def test_ozon_created_item_with_wrong_approved_title_is_mismatch(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        response = {
            "items": [
                {
                    "offer_id": sku,
                    "id": str(6000 + index),
                    "name": "Wrong wall sticker title",
                    "price": str(price),
                    "images": ["https://example.test/image.jpg"],
                    "statuses": {
                        "status": "CREATED",
                        "is_created": True,
                        "status_failed": "",
                    },
                }
                for index, (sku, price) in enumerate(
                    zip(("0960", "0961", "0962"), (77, 97, 122)), start=1
                )
            ]
        }
        repo = str(_common.DEFAULT_REPO)
        readback_ozon.add_repo_to_path(repo)
        args = type("Args", (), {"repo": repo, "timeout_seconds": 1})()
        with patch("modules.ozon.client.ozon_post", return_value=response):
            fact = readback_ozon.readback(snapshot, {"accepted": True}, args)

        self.assertEqual(fact["status"], "MISMATCH")
        self.assertFalse(fact["verified"])
        self.assertFalse(fact["variants"][0]["checks"]["title_exact"])

    def test_ozon_multisku_table_runner_readback_uses_each_variant_title(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        snapshot["content"]["platform_titles"] = [{
            "channel": "ozon",
            "site": "RU",
            "title": (
                "Белая ажурная дорожка на стол с краем в виде звёзд и луны, "
                "цветочный узор, 35х140 см"
            ),
        }]
        snapshot["content"]["source_category"] = {
            "id": "",
            "name": "居家布艺 > 桌旗",
        }
        for row, label in zip(snapshot["skus"], ("35*140", "35*200", "35*300")):
            row["option_name"] = label
        expected_titles = (
            "Белая ажурная дорожка на стол с краем в виде звёзд и луны, цветочный узор, 35х140 см",
            "Белая ажурная дорожка на стол с краем в виде звёзд и луны, цветочный узор, 35х200 см",
            "Белая ажурная дорожка на стол с краем в виде звёзд и луны, цветочный узор, 35х300 см",
        )
        response = {
            "items": [
                {
                    "offer_id": sku,
                    "id": str(8000 + index),
                    "name": title,
                    "price": str(price),
                    "images": ["https://example.test/image.jpg"],
                    "statuses": {
                        "status": "CREATED",
                        "is_created": True,
                        "status_failed": "",
                    },
                }
                for index, (sku, title, price) in enumerate(
                    zip(
                        ("0960", "0961", "0962"),
                        expected_titles,
                        (77, 97, 122),
                    ),
                    start=1,
                )
            ]
        }
        repo = str(_common.DEFAULT_REPO)
        args = type("Args", (), {"repo": repo, "timeout_seconds": 1})()
        with patch("modules.ozon.client.ozon_post", return_value=response):
            fact = readback_ozon.readback(snapshot, {"accepted": True}, args)

        self.assertEqual(fact["status"], "VERIFIED")
        self.assertTrue(fact["verified"])
        self.assertTrue(all(row["checks"]["title_exact"] for row in fact["variants"]))

    def test_ozon_decline_readback_exposes_safe_provider_error_codes(self) -> None:
        snapshot = build_snapshot(dashboard_fixture(), "3838619319")
        response = {
            "items": [
                {
                    "offer_id": sku,
                    "id": str(7000 + index),
                    "name": "Wrong wall sticker title",
                    "price": str(price),
                    "images": ["https://example.test/image.jpg"],
                    "statuses": {
                        "status": "VARIANT_WAIT",
                        "is_created": False,
                        "status_failed": "declined",
                        "status_tooltip": "Description could not be created",
                    },
                    "errors": [
                        {"code": "DESCRIPTION_DECLINE", "field": "description"},
                        {"code": "IMAGE_MISMATCH", "field": "images"},
                    ],
                }
                for index, (sku, price) in enumerate(
                    zip(("0960", "0961", "0962"), (77, 97, 122)), start=1
                )
            ]
        }
        repo = str(_common.DEFAULT_REPO)
        readback_ozon.add_repo_to_path(repo)
        args = type("Args", (), {"repo": repo, "timeout_seconds": 1})()
        with patch("modules.ozon.client.ozon_post", return_value=response):
            fact = readback_ozon.readback(snapshot, {"accepted": True}, args)

        self.assertEqual(fact["status"], "MISMATCH")
        self.assertEqual(fact["variants"][0]["provider_failure"], "DECLINED")
        self.assertEqual(
            fact["variants"][0]["provider_error_codes"],
            ["DESCRIPTION_DECLINE", "IMAGE_MISMATCH"],
        )

    def test_deleted_shopee_is_failure_even_after_acceptance(self) -> None:
        result = classify(
            {"accepted": True},
            {"status": "DELETED", "exists": True, "verified": False, "retry_safe": True},
        )
        self.assertEqual(result["label_zh"], "发布失败")
        self.assertTrue(result["retry_safe"])

    def test_partial_sku_readback_is_partial(self) -> None:
        result = classify(
            {"accepted": True},
            {"status": "MISMATCH", "verified_count": 2, "expected_count": 3, "mismatch": True},
        )
        self.assertEqual(result["label_zh"], "部分成功")


if __name__ == "__main__":
    unittest.main()
