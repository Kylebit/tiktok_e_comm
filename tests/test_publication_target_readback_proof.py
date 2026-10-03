"""Private persisted-report counterexample; no provider or approval execution.

The existing fixture restores synthetic prior-run records, with a real temporary
report store and exact candidate/approval relation. It does not supply an
official observation for the target. A report-wide readback flag must not turn
that missing fact into target-level official success.
"""

import pytest

from shared_platform.product_publication_runner import ProductPublicationRunner
from test_r3_status_projection import approved_context, seed_historical_report, state


def test_global_readback_without_target_observation_is_not_official_success(
    tmp_path, monkeypatch
):
    store, data, common_io, market, snapshot = approved_context(tmp_path, monkeypatch)
    mutation_count = common_io.mutations
    monkeypatch.setattr(
        ProductPublicationRunner,
        "run",
        lambda *args, **kwargs: pytest.fail("read-only projection dispatched a runner"),
    )
    receipt = seed_historical_report(
        store,
        data,
        market,
        snapshot,
        run_id="global-readback-missing-target-observation",
        state="PUBLISHED",
    )
    assert receipt.report["summary"]["evidence"]["readback_completed"] is True
    assert receipt.report["targets"][0]["evidence"] == {
        "target_label": market["targets"][0],
        "status": "PUBLISHED",
        "stage": "PUBLISH",
        "provider_code": "ACCEPTED",
        "provider_reason": "Synthetic bounded result",
        "request_attempted": True,
        "outcome_unknown": False,
        "external_write_count": 0,
    }
    from modules.products import server
    report_path = server._product_publication_report_store().reports_root / receipt.stored.report_path
    historical_bytes = report_path.read_bytes()
    projected = state(data)
    target = projected["target_results"][0]
    # Keep the immutable legacy claim visible. Missing observation is not a
    # reason to discard its report, synthesize proof, or retry publication.
    assert target["reported_status"] == "PUBLISHED"
    assert target["source"]["report_id"] == receipt.report["report_id"]
    assert common_io.mutations == mutation_count
    assert target["official_success"] is False
    assert target["status"] == "RECONCILIATION_REQUIRED"
    assert target["next_action"] == "RECONCILE_EXISTING_RUN"
    assert target["readback_completed"] is None
    assert target["reported_readback_completed"] is True
    assert "TARGET_OFFICIAL_READBACK_UNAVAILABLE" in target["blockers"]
    assert target["history"][0]["reported_readback_completed"] is True
    assert report_path.read_bytes() == historical_bytes


def test_processing_global_readback_does_not_certify_target_observation(
    tmp_path, monkeypatch
):
    store, data, common_io, market, snapshot = approved_context(tmp_path, monkeypatch)
    mutation_count = common_io.mutations
    monkeypatch.setattr(
        ProductPublicationRunner,
        "run",
        lambda *args, **kwargs: pytest.fail("read-only projection dispatched a runner"),
    )
    receipt = seed_historical_report(
        store, data, market, snapshot,
        run_id="processing-global-readback-missing-target-observation",
        state="PROCESSING",
    )
    assert receipt.report["summary"]["evidence"]["readback_completed"] is True
    assert receipt.report["targets"][0]["evidence"]["stage"] == "PUBLISH"
    assert receipt.report["targets"][0]["evidence"]["provider_code"] == "ACCEPTED"
    from modules.products import server
    report_path = server._product_publication_report_store().reports_root / receipt.stored.report_path
    historical_bytes = report_path.read_bytes()
    projected = state(data)
    target = projected["target_results"][0]
    assert target["reported_status"] == "PROCESSING"
    assert target["source"]["report_id"] == receipt.report["report_id"]
    assert projected["execution_summary"]["external_write_count"] == 2
    assert common_io.mutations == mutation_count
    assert target["official_success"] is False
    # A round-level declaration is not an observed target fact, including when
    # an asynchronous provider result is still PROCESSING.
    assert target["readback_completed"] is None
    assert target["reported_readback_completed"] is True
    assert target["lifecycle"] == "RECONCILIATION_REQUIRED"
    assert target["next_action"] == "RECONCILE_EXISTING_RUN"
    assert target["status"] == "RECONCILIATION_REQUIRED"
    assert "TARGET_OFFICIAL_READBACK_UNAVAILABLE" in target["blockers"]
    assert report_path.read_bytes() == historical_bytes
