from __future__ import annotations

import json

import pytest

from shared_platform.product_publication_closure import (
    build_publication_closure,
    latest_publication_closure,
    store_publication_closure,
    validate_publication_closure,
)


def _closure():
    return build_publication_closure(
        offer_id="3912004828",
        revision=4,
        plan_id="dual-brand:3912004828:approved",
        snapshot_digest="sha256:" + "a" * 64,
        recorded_at="2026-08-21T10:00:00+08:00",
        recorded_by="Kyle",
        targets=[
            {
                "target_label": "tiktok:LH_PH",
                "source_status": "PUBLISHED",
                "resolution": "OFFICIAL_READBACK_VERIFIED",
                "evidence_code": "description_media_verified",
                "source_run_id": "tiktok-readback-1",
                "platform_identity_bound": True,
                "manual_handoff": None,
            },
            {
                "target_label": "tiktok:HB_PH",
                "source_status": "BLOCKED",
                "resolution": "MANUAL_HANDOFF_ACCEPTED",
                "evidence_code": "official_storefront_edit_unavailable",
                "source_run_id": "tiktok-readback-1",
                "platform_identity_bound": False,
                "manual_handoff": {
                    "accepted_by": "Kyle",
                    "accepted_at": "2026-08-21T10:00:00+08:00",
                    "note": "HomeBloom is handled manually",
                },
            },
            {
                "target_label": "ozon:RU",
                "source_status": "PROCESSING",
                "resolution": "OPEN_PROCESSING",
                "evidence_code": "ozon_result",
                "source_run_id": "ozon-run-1",
                "platform_identity_bound": False,
                "manual_handoff": None,
            },
        ],
        source_reports=[
            {
                "run_id": "tiktok-readback-1",
                "report_path": "3912004828/4/tiktok-readback-1/report.json",
                "report_digest": "sha256:" + "b" * 64,
            },
            {
                "run_id": "ozon-run-1",
                "report_path": "3912004828/4/ozon-run-1/report.json",
                "report_digest": "sha256:" + "c" * 64,
            },
        ],
    )


def test_closure_preserves_platform_truth_and_manual_disposition() -> None:
    closure = _closure()

    assert closure["closure_status"] == "CLOSED_WITH_OPEN_ITEMS"
    assert closure["business_complete"] is True
    assert closure["summary"] == {
        "target_count": 3,
        "verified_count": 1,
        "manual_handoff_count": 1,
        "processing_count": 1,
        "failed_count": 0,
    }
    homebloom = closure["targets"][1]
    assert homebloom["source_status"] == "BLOCKED"
    assert homebloom["resolution"] == "MANUAL_HANDOFF_ACCEPTED"


def test_closure_store_is_immutable_and_latest_is_readable(tmp_path) -> None:
    closure = _closure()
    first = store_publication_closure(closure, root=tmp_path)
    second = store_publication_closure(closure, root=tmp_path)

    assert first == second
    assert json.loads(first.read_text(encoding="utf-8"))["closure_digest"] == closure["closure_digest"]
    assert latest_publication_closure("3912004828", root=tmp_path) == closure


def test_closure_rejects_manual_handoff_without_evidence() -> None:
    closure = _closure()
    closure["targets"][1]["manual_handoff"] = None

    with pytest.raises(ValueError, match="manual handoff evidence"):
        validate_publication_closure(closure)
