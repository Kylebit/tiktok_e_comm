from copy import deepcopy
from types import SimpleNamespace

import pytest

from modules.ozon.approved_publication_v4 import project_ozon_v4_variants
from shared_platform.product_publication_readonly_reconciliation import (
    ProductPublicationReadonlyReconciliationError,
    reconcile_completed_processing_run_readonly,
)
from test_ozon_approved_publication_v4 import _published_item, _snapshot


RUN_ID = "product-center-ozon-" + "8" * 32


class ReportStore:
    def __init__(self, root, report):
        self.reports_root = root
        self.report = report

    def get_report_by_run(self, *, run_id):
        return deepcopy(self.report) if run_id == RUN_ID else None


class RunStore:
    def __init__(self, report_id, state="COMPLETED"):
        self.report_id = report_id
        self.state = state

    def get_run_by_id(self, *, run_id):
        if run_id != RUN_ID:
            return None
        return {
            "run_id": run_id,
            "state": self.state,
            "final_report_id": self.report_id,
        }


class ReleaseStore:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def approved_publication_snapshot(self, *, offer_id, plan_id):
        assert offer_id == self.snapshot["offer_id"]
        assert plan_id == self.snapshot["plan_id"]
        return deepcopy(self.snapshot)


def _report(snapshot):
    return {
        "schema_version": "product-publication-report/v2",
        "report_id": "publication-report:" + RUN_ID,
        "run_id": RUN_ID,
        "offer_id": snapshot["offer_id"],
        "revision": snapshot["product_revision"],
        "plan_id": snapshot["plan_id"],
        "snapshot": {"digest": snapshot["snapshot_digest"]},
        "status": "PROCESSING",
        "summary_digest": "a" * 64,
        "targets": [{"target_label": "ozon:RU", "status": "PROCESSING"}],
    }


def test_completed_processing_run_refreshes_to_published_unstocked_idempotently(tmp_path):
    snapshot = _snapshot()
    report = _report(snapshot)
    original = deepcopy(report)
    projected = project_ozon_v4_variants(snapshot, target_labels=("ozon:RU",))
    provider_calls = []
    mutation_calls = []

    def readback_variants(offer_ids):
        provider_calls.append(("listing", offer_ids))
        return [
            _published_item(row, item_id="product-" + row["offer_id"])
            for row in projected
        ]

    def readback_stocks(offer_ids):
        provider_calls.append(("stock", offer_ids))
        return []

    dependencies = SimpleNamespace(
        readback_variants=readback_variants,
        readback_stocks=readback_stocks,
        official_profile_resolver=None,
        localized_copy_resolver=None,
        dispatch_variant=lambda _row: mutation_calls.append("import"),
        update_stocks=lambda _rows: mutation_calls.append("stock"),
        prepare_stock_update=lambda _rows: mutation_calls.append("prepare-stock"),
    )
    reports = ReportStore(tmp_path / "reports", report)
    kwargs = {
        "run_id": RUN_ID,
        "report_store": reports,
        "run_store": RunStore(report["report_id"]),
        "release_store": ReleaseStore(snapshot),
        "ozon_dependencies": dependencies,
    }
    first = reconcile_completed_processing_run_readonly(**kwargs)
    second = reconcile_completed_processing_run_readonly(**kwargs)

    assert first["evidence"]["derived"]["derived_current_status"] == "PUBLISHED"
    assert first["evidence"]["derived"]["inventory"] == {
        "status": "UNSTOCKED_UNAPPROVED",
        "approved_policy": False,
        "observed_row_count": 0,
    }
    assert first["evidence"]["external_writes_performed"] == []
    assert first["evidence"]["mutation_reservations"] == []
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert second["evidence"]["evidence_digest"] == first["evidence"]["evidence_digest"]
    assert second["evidence_path"] == first["evidence_path"]
    assert mutation_calls == []
    assert report == original
    assert provider_calls == [
        ("listing", ("0967", "0968", "0969")),
        ("stock", ("0967", "0968", "0969")),
        ("listing", ("0967", "0968", "0969")),
        ("stock", ("0967", "0968", "0969")),
    ]


def test_refresh_requires_completed_processing_run(tmp_path):
    snapshot = _snapshot()
    report = _report(snapshot)
    dependencies = SimpleNamespace(
        readback_variants=lambda _ids: [],
        readback_stocks=lambda _ids: [],
        official_profile_resolver=None,
        localized_copy_resolver=None,
    )
    with pytest.raises(
        ProductPublicationReadonlyReconciliationError,
        match="completed PROCESSING",
    ):
        reconcile_completed_processing_run_readonly(
            run_id=RUN_ID,
            report_store=ReportStore(tmp_path / "reports", report),
            run_store=RunStore(report["report_id"], state="RUNNING"),
            release_store=ReleaseStore(snapshot),
            ozon_dependencies=dependencies,
        )


def test_refresh_keeps_processing_when_approved_stock_has_not_converged(tmp_path):
    snapshot = _snapshot()
    snapshot["product"]["stock_policy"] = {
        "schema_version": "publication-default-stock/v1",
        "quantity_per_sku": 200,
        "scope": "EACH_SELECTED_SKU",
        "source": "SYSTEM_GOVERNED_DEFAULT",
    }
    projected = project_ozon_v4_variants(snapshot, target_labels=("ozon:RU",))
    report = _report(snapshot)
    result = reconcile_completed_processing_run_readonly(
        run_id=RUN_ID,
        report_store=ReportStore(tmp_path / "reports", report),
        run_store=RunStore(report["report_id"]),
        release_store=ReleaseStore(snapshot),
        ozon_dependencies=SimpleNamespace(
            readback_variants=lambda _ids: [
                _published_item(row, item_id="product-" + row["offer_id"])
                for row in projected
            ],
            readback_stocks=lambda _ids: [],
            official_profile_resolver=None,
            localized_copy_resolver=None,
        ),
    )
    assert result["evidence"]["derived"]["listing_current_status"] == "PUBLISHED"
    assert result["evidence"]["derived"]["derived_current_status"] == "PROCESSING"
    assert result["evidence"]["derived"]["inventory"]["status"] == "MISMATCH"


def test_http_refresh_contract_rejects_any_scope_beyond_exact_run_id():
    from modules.products import server

    status, payload = server._reconcile_product_publication_readonly(
        {"run_id": RUN_ID, "offer_id": "3882722296"}
    )
    assert status == 400
    assert payload["external_writes_performed"] == []
