from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from domains.product_operations import build_approved_publication_snapshot
from shared_platform.product_publication_reports import ProductPublicationReportStore
from shared_platform.product_publication_runner import (
    ProductPublicationRunConflictError,
    ProductPublicationRunner,
)
from test_approved_publication_snapshot import _approved_plan


class _SnapshotStore:
    def __init__(self, snapshot: dict | None) -> None:
        self.snapshot = deepcopy(snapshot)
        self.calls: list[dict[str, object]] = []

    def approved_publication_snapshot(self, **kwargs):
        self.calls.append(dict(kwargs))
        return deepcopy(self.snapshot)


def _snapshot() -> dict:
    return build_approved_publication_snapshot(_approved_plan()).payload()


def _report_store(tmp_path: Path) -> ProductPublicationReportStore:
    return ProductPublicationReportStore(
        tmp_path / "orbit_platform.db",
        reports_root=tmp_path / "reports" / "product-publication",
    )


def _platform_result(
    platform: str,
    targets: list[tuple[str, str]],
    *,
    writes: int | None,
    dispatch_attempted: bool = True,
    readback_completed: bool = True,
    requires_human_action: bool = False,
) -> dict:
    return {
        "schema_version": "product-publication-platform-result/v1",
        "platform": platform,
        "targets": [
            {"target_label": target, "status": status}
            for target, status in targets
        ],
        "dispatch_attempted": dispatch_attempted,
        "readback_completed": readback_completed,
        "external_write_count": writes,
        "requires_human_action": requires_human_action,
    }


def test_runner_uses_v4_snapshot_once_per_independent_platform_and_persists_redacted_report(
    tmp_path,
):
    snapshot = _snapshot()
    snapshot_store = _SnapshotStore(snapshot)
    report_store = _report_store(tmp_path)
    observed_titles: dict[str, str] = {}

    def tiktok(request):
        observed_titles[request.platform] = request.snapshot["product"]["title"]
        request.snapshot["product"]["title"] = "mutated inside TikTok"
        return _platform_result(
            "TIKTOK",
            [
                ("tiktok:LH_PH", "PUBLISHED"),
                ("tiktok:LH_MY", "PUBLISHED"),
            ],
            writes=2,
        )

    def shopee(request):
        observed_titles[request.platform] = request.snapshot["product"]["title"]
        return _platform_result(
            "SHOPEE", [("shopee:PH", "PROCESSING")], writes=1
        )

    def ozon(request):
        observed_titles[request.platform] = request.snapshot["product"]["title"]
        return _platform_result(
            "OZON",
            [("ozon:RU", "FAILED")],
            writes=0,
            requires_human_action=True,
        )

    receipt = ProductPublicationRunner(
        release_store=snapshot_store,
        report_store=report_store,
    ).run(
        run_id="run-001",
        offer_id=snapshot["offer_id"],
        plan_id=snapshot["plan_id"],
        platform_scope=("OZON", "TIKTOK", "SHOPEE"),
        platform_executors={
            "TIKTOK": tiktok,
            "SHOPEE": shopee,
            "OZON": ozon,
        },
    )

    assert snapshot_store.calls == [
        {
            "offer_id": snapshot["offer_id"],
            "plan_id": snapshot["plan_id"],
            "snapshot_digest": None,
        }
    ]
    assert observed_titles == {
        "TIKTOK": snapshot["product"]["title"],
        "SHOPEE": snapshot["product"]["title"],
        "OZON": snapshot["product"]["title"],
    }
    assert receipt.replayed is False
    assert receipt.report["report_id"] == "publication-report:run-001"
    assert receipt.report["run_id"] == "run-001"
    assert receipt.report["status"] == "PARTIAL"
    assert [row["platform"] for row in receipt.report["summary"]["platforms"]] == [
        "TIKTOK",
        "SHOPEE",
        "OZON",
    ]
    assert receipt.report["summary"]["evidence"] == {
        "snapshot_verified": True,
        "dispatch_attempted": True,
        "readback_completed": True,
        "external_write_count": 3,
    }
    assert receipt.report["summary"]["requires_human_action"] is True

    report_file = report_store.reports_root / receipt.stored.report_path
    encoded = report_file.read_text(encoding="utf-8")
    for forbidden in (
        snapshot["product"]["title"],
        snapshot["product"]["description"],
        snapshot["product"]["images"][0],
        snapshot["skus"][0]["model_sku"],
        "mutated inside TikTok",
    ):
        assert forbidden not in encoded


def test_platform_exception_is_target_local_and_later_platforms_still_run(tmp_path):
    snapshot = _snapshot()
    called: list[str] = []

    def tiktok(_request):
        called.append("TIKTOK")
        raise RuntimeError("provider error containing SECRET-TOKEN")

    def shopee(_request):
        called.append("SHOPEE")
        return _platform_result(
            "SHOPEE", [("shopee:PH", "PUBLISHED")], writes=1
        )

    def ozon(_request):
        called.append("OZON")
        return _platform_result("OZON", [("ozon:RU", "PUBLISHED")], writes=1)

    report_store = _report_store(tmp_path)
    receipt = ProductPublicationRunner(
        release_store=_SnapshotStore(snapshot),
        report_store=report_store,
    ).run(
        run_id="run-exception",
        offer_id=snapshot["offer_id"],
        snapshot_digest=snapshot["snapshot_digest"],
        platform_scope=("TIKTOK", "SHOPEE", "OZON"),
        platform_executors={
            "TIKTOK": tiktok,
            "SHOPEE": shopee,
            "OZON": ozon,
        },
    )

    assert called == ["TIKTOK", "SHOPEE", "OZON"]
    assert receipt.report["status"] == "PARTIAL"
    tiktok_row = receipt.report["summary"]["platforms"][0]
    assert tiktok_row == {
        "platform": "TIKTOK",
        "status": "FAILED",
        "target_count": 2,
        "verified_count": 0,
        "processing_count": 0,
        "failed_count": 2,
    }
    assert receipt.report["summary"]["evidence"]["external_write_count"] is None
    report_file = report_store.reports_root / receipt.stored.report_path
    assert "SECRET-TOKEN" not in report_file.read_text(encoding="utf-8")


def test_exact_replay_does_not_reinvoke_platform_and_scope_drift_fails_before_call(
    tmp_path,
):
    snapshot = _snapshot()
    calls = 0

    def tiktok(_request):
        nonlocal calls
        calls += 1
        return _platform_result(
            "TIKTOK",
            [
                ("tiktok:LH_PH", "PUBLISHED"),
                ("tiktok:LH_MY", "PUBLISHED"),
            ],
            writes=2,
        )

    runner = ProductPublicationRunner(
        release_store=_SnapshotStore(snapshot),
        report_store=_report_store(tmp_path),
    )
    arguments = {
        "run_id": "run-replay",
        "offer_id": snapshot["offer_id"],
        "plan_id": snapshot["plan_id"],
        "platform_scope": ("TIKTOK",),
        "platform_executors": {"TIKTOK": tiktok},
    }
    first = runner.run(**arguments)
    second = runner.run(**arguments)

    assert calls == 1
    assert first.replayed is False
    assert second.replayed is True
    assert second.report == first.report

    with pytest.raises(ProductPublicationRunConflictError, match="platform scope"):
        runner.run(
            **{
                **arguments,
                "platform_scope": ("TIKTOK", "SHOPEE"),
                "platform_executors": {
                    "TIKTOK": tiktok,
                    "SHOPEE": lambda _request: pytest.fail(
                        "drifted replay must fail before a platform call"
                    ),
                },
            }
        )
    assert calls == 1


def test_unavailable_snapshot_stops_before_platform_or_report(tmp_path):
    called = False

    def executor(_request):
        nonlocal called
        called = True
        raise AssertionError("executor must not run")

    report_store = _report_store(tmp_path)
    runner = ProductPublicationRunner(
        release_store=_SnapshotStore(None),
        report_store=report_store,
    )
    with pytest.raises(ValueError, match="approved publication snapshot"):
        runner.run(
            run_id="run-missing",
            offer_id="3838616043",
            plan_id="release-plan:missing",
            platform_scope=("TIKTOK",),
            platform_executors={"TIKTOK": executor},
        )
    assert called is False
    assert not report_store.path.exists()


def test_platform_result_rejects_extra_provider_detail_without_persisting_it(tmp_path):
    snapshot = _snapshot()

    def leaking_result(_request):
        result = _platform_result(
            "OZON", [("ozon:RU", "PUBLISHED")], writes=1
        )
        result["raw_response"] = {"token": "SHOULD-NOT-PERSIST"}
        return result

    report_store = _report_store(tmp_path)
    receipt = ProductPublicationRunner(
        release_store=_SnapshotStore(snapshot),
        report_store=report_store,
    ).run(
        run_id="run-redaction",
        offer_id=snapshot["offer_id"],
        plan_id=snapshot["plan_id"],
        platform_scope=("OZON",),
        platform_executors={"OZON": leaking_result},
    )

    assert receipt.report["status"] == "FAILED"
    encoded = (report_store.reports_root / receipt.stored.report_path).read_text(
        encoding="utf-8"
    )
    assert "SHOULD-NOT-PERSIST" not in encoded
    assert "raw_response" not in encoded


def test_runner_requires_exactly_one_snapshot_identity_before_platform_call(tmp_path):
    snapshot = _snapshot()
    runner = ProductPublicationRunner(
        release_store=_SnapshotStore(snapshot),
        report_store=_report_store(tmp_path),
    )
    with pytest.raises(ValueError, match="exactly one"):
        runner.run(
            run_id="run-ambiguous",
            offer_id=snapshot["offer_id"],
            plan_id=snapshot["plan_id"],
            snapshot_digest=snapshot["snapshot_digest"],
            platform_scope=("OZON",),
            platform_executors={
                "OZON": lambda _request: pytest.fail("must not execute")
            },
        )


@pytest.mark.parametrize(
    ("target_statuses", "expected_status"),
    [
        (("PUBLISHED", "PUBLISHED"), "PUBLISHED"),
        (("PROCESSING", "PROCESSING"), "PROCESSING"),
        (("FAILED", "FAILED"), "FAILED"),
        (("PUBLISHED", "PROCESSING"), "PARTIAL"),
    ],
)
def test_runner_projects_all_four_public_overall_states(
    tmp_path_factory, target_statuses, expected_status
):
    snapshot = _snapshot()
    # Keep the nested report filename below Windows MAX_PATH even when this
    # parametrized test is part of a long related-suite basetemp.
    tmp_path = tmp_path_factory.mktemp(f"state-{expected_status.lower()}")
    receipt = ProductPublicationRunner(
        release_store=_SnapshotStore(snapshot),
        report_store=_report_store(tmp_path),
    ).run(
        run_id=f"run-state-{expected_status.lower()}",
        offer_id=snapshot["offer_id"],
        plan_id=snapshot["plan_id"],
        platform_scope=("TIKTOK",),
        platform_executors={
            "TIKTOK": lambda _request: _platform_result(
                "TIKTOK",
                [
                    ("tiktok:LH_PH", target_statuses[0]),
                    ("tiktok:LH_MY", target_statuses[1]),
                ],
                writes=0,
                requires_human_action=expected_status == "FAILED",
            )
        },
    )

    assert receipt.report["status"] == expected_status
    assert receipt.report["summary"]["overall_status"] == expected_status
    assert receipt.report["summary"]["platforms"][0]["status"] == expected_status


def test_report_store_can_find_run_identity_without_offer_for_pre_dispatch_collision_check(
    tmp_path,
):
    snapshot = _snapshot()
    store = _report_store(tmp_path)
    receipt = ProductPublicationRunner(
        release_store=_SnapshotStore(snapshot),
        report_store=store,
    ).run(
        run_id="run-identity",
        offer_id=snapshot["offer_id"],
        plan_id=snapshot["plan_id"],
        platform_scope=("OZON",),
        platform_executors={
            "OZON": lambda _request: _platform_result(
                "OZON", [("ozon:RU", "PUBLISHED")], writes=1
            )
        },
    )

    assert store.get_report_by_run(run_id="run-identity") == receipt.report
    assert store.get_report_by_run(run_id="missing") is None
