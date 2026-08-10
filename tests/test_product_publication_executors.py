from __future__ import annotations

import inspect
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from shared_platform.product_publication_executors import (
    OzonV4ExecutorDependencies,
    ShopeeRegionExecutorDependencies,
    TikTokV4ExecutorDependencies,
    build_product_publication_platform_executors,
    build_shopee_region_executor,
    build_tiktok_v4_executor,
)
from shared_platform.product_publication_runner import PublicationPlatformRequest
from shared_platform.product_publication_runner import ProductPublicationRunner


def _request(platform: str, targets: tuple[str, ...]) -> PublicationPlatformRequest:
    return PublicationPlatformRequest(
        run_id="run-composition",
        report_id="publication-report:run-composition",
        platform=platform,
        target_labels=targets,
        snapshot={
            "schema_version": "approved-publication-snapshot/v4",
            "publication_targets": [
                {
                    "target_label": label,
                    "platform": label.split(":", 1)[0],
                    "site": label.split(":", 1)[1],
                    "store": label.split(":", 1)[1],
                }
                for label in targets
            ],
        },
    )


class _SnapshotStore:
    def __init__(self, snapshot: dict) -> None:
        self.snapshot = deepcopy(snapshot)

    def approved_publication_snapshot(self, **_kwargs):
        return deepcopy(self.snapshot)


class _ReportStore:
    def __init__(self) -> None:
        self.report = None

    def get_report_by_run(self, *, run_id):
        return None

    def store_report(self, report):
        self.report = deepcopy(report)
        return SimpleNamespace(report_path="memory.json")

    def get_report(self, *, report_id, offer_id):
        return deepcopy(self.report)


class ProductPublicationExecutorCompositionTests(unittest.TestCase):
    def test_tiktok_composes_v4_plan_without_legacy_collectbox_start(self) -> None:
        request = _request("TIKTOK", ("tiktok:LH_PH", "tiktok:LH_MY"))
        contexts = {"tiktok:LH_MY": {"detail_id": "draft-my"}}
        category_resolver = SimpleNamespace(resolve=lambda **_kwargs: {})
        publisher = SimpleNamespace(
            preflight=lambda _snapshot: {},
            publish=lambda _snapshot, _preflight=None: {},
        )
        readback = SimpleNamespace(readback=lambda **_kwargs: {})

        with (
            patch(
                "shared_platform.product_publication_executors."
                "project_tiktok_v4_execution_plan",
                return_value={"plan": "v4"},
            ) as project,
            patch(
                "shared_platform.product_publication_executors."
                "execute_tiktok_v4_plan",
                return_value={
                    "external_write_count": 1,
                    "targets": [
                        {
                            "target_label": "tiktok:LH_PH",
                            "status": "FAILED",
                            "dispatch_attempted": False,
                            "readback_status": "NOT_ATTEMPTED",
                            "external_write_count": 0,
                        },
                        {
                            "target_label": "tiktok:LH_MY",
                            "status": "PUBLISHED",
                            "dispatch_attempted": True,
                            "readback_status": "VERIFIED",
                            "external_write_count": 1,
                        },
                    ],
                },
            ) as execute,
        ):
            executor = build_tiktok_v4_executor(
                collectbox_context_resolver=lambda observed: (
                    contexts if observed is request else {}
                ),
                category_resolver=category_resolver,
                publisher=publisher,
                storefront_readback=readback,
            )
            result = executor(request)

        project.assert_called_once_with(
            request.snapshot,
            collectbox_contexts=contexts,
            category_resolver=category_resolver,
        )
        execute.assert_called_once_with(
            {"plan": "v4"},
            publisher=publisher,
            storefront_readback=readback,
        )
        self.assertEqual(
            result,
            {
                "schema_version": "product-publication-platform-result/v1",
                "platform": "TIKTOK",
                "targets": [
                    {"target_label": "tiktok:LH_PH", "status": "FAILED"},
                    {"target_label": "tiktok:LH_MY", "status": "PUBLISHED"},
                ],
                "dispatch_attempted": True,
                "readback_completed": True,
                "external_write_count": 1,
                "requires_human_action": True,
            },
        )
        source = inspect.getsource(
            __import__(
                "shared_platform.product_publication_executors", fromlist=["*"]
            )
        ).casefold()
        self.assertNotIn("collectbox-action/start", source)
        self.assertNotIn("start_collectbox_action", source)
        self.assertNotIn("collectboxactionstore", source)

    def test_tiktok_context_resolution_failure_is_zero_write_and_target_local(
        self,
    ) -> None:
        request = _request("TIKTOK", ("tiktok:LH_PH", "tiktok:LH_MY"))
        executor = build_tiktok_v4_executor(
            collectbox_context_resolver=lambda _request: (_ for _ in ()).throw(
                LookupError("durable draft identity unavailable")
            ),
            category_resolver=None,
            publisher=SimpleNamespace(
                preflight=lambda _snapshot: {},
                publish=lambda _snapshot, _preflight=None: {},
            ),
            storefront_readback=SimpleNamespace(readback=lambda **_kwargs: {}),
        )

        with patch(
            "shared_platform.product_publication_executors."
            "project_tiktok_v4_execution_plan"
        ) as project:
            result = executor(request)

        project.assert_not_called()
        self.assertEqual(
            result["targets"],
            [
                {"target_label": "tiktok:LH_PH", "status": "FAILED"},
                {"target_label": "tiktok:LH_MY", "status": "FAILED"},
            ],
        )
        self.assertFalse(result["dispatch_attempted"])
        self.assertFalse(result["readback_completed"])
        self.assertEqual(result["external_write_count"], 0)

    def test_shopee_always_reads_back_and_keeps_targets_independent(self) -> None:
        request = _request("SHOPEE", ("shopee:PH", "shopee:MY"))
        runtime = object()
        dispatch = {
            "targets": [
                {
                    "target_label": "shopee:PH",
                    "attempted": False,
                    "accepted": False,
                    "outcome": "NOT_ATTEMPTED",
                },
                {
                    "target_label": "shopee:MY",
                    "attempted": True,
                    "accepted": True,
                    "outcome": "ACCEPTED",
                },
            ]
        }
        readback = {
            "targets": [
                {"target_label": "shopee:PH", "outcome": "NOT_DISPATCHED"},
                {"target_label": "shopee:MY", "outcome": "PUBLISHED"},
            ]
        }

        with (
            patch(
                "shared_platform.product_publication_executors."
                "dispatch_selected_regions",
                return_value=dispatch,
            ) as dispatch_call,
            patch(
                "shared_platform.product_publication_executors."
                "readback_dispatched_regions",
                return_value=readback,
            ) as readback_call,
        ):
            result = build_shopee_region_executor(
                global_item_id_resolver=lambda observed: (
                    "60000001" if observed is request else ""
                ),
                runtime=runtime,
                poll_attempts=1,
            )(request)

        dispatch_call.assert_called_once_with(
            request.snapshot,
            global_item_id="60000001",
            runtime=runtime,
        )
        readback_call.assert_called_once_with(
            request.snapshot,
            dispatch,
            global_item_id="60000001",
            runtime=runtime,
            poll_attempts=1,
        )
        self.assertEqual(
            result,
            {
                "schema_version": "product-publication-platform-result/v1",
                "platform": "SHOPEE",
                "targets": [
                    {"target_label": "shopee:PH", "status": "FAILED"},
                    {"target_label": "shopee:MY", "status": "PUBLISHED"},
                ],
                "dispatch_attempted": True,
                "readback_completed": True,
                "external_write_count": 1,
                "requires_human_action": True,
            },
        )

    def test_shopee_unknown_dispatch_is_processing_with_unknown_write_count(
        self,
    ) -> None:
        request = _request("SHOPEE", ("shopee:PH",))
        dispatch = {
            "targets": [
                {
                    "target_label": "shopee:PH",
                    "attempted": True,
                    "accepted": False,
                    "outcome": "UNKNOWN",
                }
            ]
        }
        with (
            patch(
                "shared_platform.product_publication_executors."
                "dispatch_selected_regions",
                return_value=dispatch,
            ),
            patch(
                "shared_platform.product_publication_executors."
                "readback_dispatched_regions",
                return_value={
                    "targets": [
                        {
                            "target_label": "shopee:PH",
                            "outcome": "NOT_DISPATCHED",
                        }
                    ]
                },
            ),
        ):
            result = build_shopee_region_executor(
                global_item_id_resolver=lambda _request: "60000001",
                runtime=object(),
            )(request)

        self.assertEqual(result["targets"][0]["status"], "PROCESSING")
        self.assertIsNone(result["external_write_count"])
        self.assertFalse(result["requires_human_action"])

    def test_factory_composes_only_selected_platforms(self) -> None:
        tiktok = TikTokV4ExecutorDependencies(
            collectbox_context_resolver=lambda _request: {},
            category_resolver=None,
            publisher=SimpleNamespace(
                preflight=lambda _snapshot: {},
                publish=lambda _snapshot, _preflight=None: {},
            ),
            storefront_readback=SimpleNamespace(readback=lambda **_kwargs: {}),
        )
        shopee = ShopeeRegionExecutorDependencies(
            global_item_id_resolver=lambda _request: "60000001",
            runtime=object(),
        )
        ozon = OzonV4ExecutorDependencies(
            dispatch_variant=lambda _variant: None,
            readback_variants=lambda _offer_ids: [],
        )
        ozon_executor = lambda _request: {"platform": "OZON"}

        with patch(
            "shared_platform.product_publication_executors.build_ozon_v4_executor",
            return_value=ozon_executor,
        ) as build_ozon:
            executors = build_product_publication_platform_executors(
                platform_scope=("OZON", "TIKTOK", "SHOPEE"),
                tiktok=tiktok,
                shopee=shopee,
                ozon=ozon,
            )

        self.assertEqual(list(executors), ["TIKTOK", "SHOPEE", "OZON"])
        self.assertTrue(
            all(callable(executor) for executor in executors.values())
        )
        self.assertIs(executors["OZON"], ozon_executor)
        build_ozon.assert_called_once_with(
            dispatch_variant=ozon.dispatch_variant,
            readback_variants=ozon.readback_variants,
        )
        self.assertEqual(
            list(
                build_product_publication_platform_executors(
                    platform_scope=("OZON",),
                    ozon=ozon,
                )
            ),
            ["OZON"],
        )

    def test_runner_continues_to_shopee_after_composed_tiktok_zero_write_failure(
        self,
    ) -> None:
        snapshot = {
            "schema_version": "approved-publication-snapshot/v4",
            "offer_id": "3838616043",
            "plan_id": "release-plan:3838616043:r42",
            "product_revision": 42,
            "snapshot_digest": "sha256:" + "a" * 64,
            "publication_targets": [
                {
                    "target_label": "tiktok:LH_PH",
                    "platform": "tiktok",
                    "site": "LH_PH",
                    "store": "LH_PH",
                },
                {
                    "target_label": "shopee:PH",
                    "platform": "shopee",
                    "site": "PH",
                    "store": "PH",
                },
            ],
        }
        tiktok = TikTokV4ExecutorDependencies(
            collectbox_context_resolver=lambda _request: (_ for _ in ()).throw(
                LookupError("no durable draft")
            ),
            category_resolver=None,
            publisher=SimpleNamespace(
                preflight=lambda _snapshot: {},
                publish=lambda _snapshot, _preflight=None: {},
            ),
            storefront_readback=SimpleNamespace(readback=lambda **_kwargs: {}),
        )
        shopee = ShopeeRegionExecutorDependencies(
            global_item_id_resolver=lambda _request: "60000001",
            runtime=object(),
            poll_attempts=1,
        )
        executors = build_product_publication_platform_executors(
            platform_scope=("TIKTOK", "SHOPEE"),
            tiktok=tiktok,
            shopee=shopee,
        )
        shopee_dispatch = {
            "targets": [
                {
                    "target_label": "shopee:PH",
                    "attempted": True,
                    "accepted": True,
                    "outcome": "ACCEPTED",
                }
            ]
        }

        with (
            patch(
                "shared_platform.product_publication_runner."
                "validate_approved_publication_snapshot",
                return_value=SimpleNamespace(payload=lambda: deepcopy(snapshot)),
            ),
            patch(
                "shared_platform.product_publication_executors."
                "dispatch_selected_regions",
                return_value=shopee_dispatch,
            ) as dispatch_call,
            patch(
                "shared_platform.product_publication_executors."
                "readback_dispatched_regions",
                return_value={
                    "targets": [
                        {"target_label": "shopee:PH", "outcome": "PUBLISHED"}
                    ]
                },
            ) as readback_call,
        ):
            receipt = ProductPublicationRunner(
                release_store=_SnapshotStore(snapshot),
                report_store=_ReportStore(),
            ).run(
                run_id="run-independent-composition",
                offer_id=snapshot["offer_id"],
                plan_id=snapshot["plan_id"],
                platform_scope=("TIKTOK", "SHOPEE"),
                platform_executors=executors,
            )

        dispatch_call.assert_called_once()
        readback_call.assert_called_once()
        self.assertEqual(receipt.report["status"], "PARTIAL")
        self.assertEqual(
            receipt.report["summary"]["platforms"],
            [
                {
                    "platform": "TIKTOK",
                    "status": "FAILED",
                    "target_count": 1,
                    "verified_count": 0,
                    "processing_count": 0,
                    "failed_count": 1,
                },
                {
                    "platform": "SHOPEE",
                    "status": "PUBLISHED",
                    "target_count": 1,
                    "verified_count": 1,
                    "processing_count": 0,
                    "failed_count": 0,
                },
            ],
        )
        self.assertEqual(
            receipt.report["summary"]["evidence"]["external_write_count"], 1
        )

    def test_composed_ozon_projection_failure_is_exact_and_zero_write(self) -> None:
        calls = []
        executors = build_product_publication_platform_executors(
            platform_scope=("OZON",),
            ozon=OzonV4ExecutorDependencies(
                dispatch_variant=lambda _variant: calls.append("dispatch"),
                readback_variants=lambda _offer_ids: calls.append("readback"),
            ),
        )

        result = executors["OZON"](_request("OZON", ("ozon:RU",)))

        self.assertEqual(calls, [])
        self.assertEqual(
            result,
            {
                "schema_version": "product-publication-platform-result/v1",
                "platform": "OZON",
                "targets": [{"target_label": "ozon:RU", "status": "FAILED"}],
                "dispatch_attempted": False,
                "readback_completed": False,
                "external_write_count": 0,
                "requires_human_action": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
