from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared_platform.product_publication_reports import (
    ProductPublicationReportIntegrityError,
    ProductPublicationReportStore,
)


def _report_payload(
    *,
    report_id: str = "publication-report:run-001",
    run_id: str = "run-001",
    offer_id: str = "3838616043",
    revision: int = 31,
    status: str = "PUBLISHED",
) -> dict:
    return {
        "schema_version": "product-publication-report/v1",
        "report_id": report_id,
        "run_id": run_id,
        "offer_id": offer_id,
        "revision": revision,
        "plan_id": "omnichannel:" + "a" * 64,
        "snapshot": {
            "schema_version": "approved-publication-snapshot/v4",
            "digest": "b" * 64,
        },
        "status": status,
        "summary": {
            "schema_version": "product-publication-summary/v1",
            "overall_status": status,
            "platforms": [
                {
                    "platform": "TIKTOK",
                    "status": status,
                    "target_count": 6,
                    "verified_count": 6 if status == "PUBLISHED" else 0,
                    "processing_count": 0,
                    "failed_count": 0,
                }
            ],
            "evidence": {
                "snapshot_verified": True,
                "dispatch_attempted": True,
                "readback_completed": True,
                "external_write_count": 6,
            },
            "requires_human_action": False,
        },
    }


def _store(tmp_path: Path) -> ProductPublicationReportStore:
    return ProductPublicationReportStore(
        tmp_path / "orbit_platform.db",
        reports_root=tmp_path / "reports" / "product-publication",
    )


def test_store_is_idempotent_and_reopens_with_canonical_path(tmp_path):
    store = _store(tmp_path)
    payload = _report_payload()

    first = store.store_report(payload)
    second = store.store_report(payload)

    assert first.created is True
    assert second.created is False
    assert first.report_id == payload["report_id"]
    assert first.report_path == "3838616043/31/run-001/report.json"

    reopened = _store(tmp_path)
    report = reopened.get_report(
        report_id=payload["report_id"], offer_id="3838616043"
    )
    assert report["report_id"] == payload["report_id"]
    assert report["report_path"] == first.report_path
    assert report["summary"] == payload["summary"]
    assert report["summary_digest"] == first.summary_digest

    listing = reopened.list_reports(offer_id="3838616043", revision=31)
    assert [item["report_id"] for item in listing] == [payload["report_id"]]
    assert reopened.latest_report(offer_id="3838616043", revision=31) == report


@pytest.mark.parametrize("snapshot_digest", ["b" * 64, "sha256:" + "b" * 64])
def test_store_accepts_and_canonicalizes_v4_snapshot_digest(
    tmp_path, snapshot_digest
):
    payload = _report_payload()
    payload["snapshot"]["digest"] = snapshot_digest

    stored = _store(tmp_path).store_report(payload)
    report = _store(tmp_path).get_report(
        report_id=stored.report_id,
        offer_id=payload["offer_id"],
    )

    assert report["snapshot"]["digest"] == "sha256:" + "b" * 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "../escape"),
        ("run_id", "C:\\absolute"),
        ("offer_id", "../3838616043"),
        ("revision", True),
    ],
)
def test_server_owned_path_rejects_traversal_absolute_and_malformed_parts(
    tmp_path, field, value
):
    payload = _report_payload()
    payload[field] = value

    with pytest.raises((TypeError, ValueError)):
        _store(tmp_path).store_report(payload)

    assert not (tmp_path / "orbit_platform.db").exists()


def test_report_envelope_rejects_client_path_and_sensitive_summary(tmp_path):
    store = _store(tmp_path)
    with_path = {**_report_payload(), "report_path": "../../outside.json"}
    with pytest.raises(ValueError, match="fields"):
        store.store_report(with_path)

    leaked = _report_payload(report_id="publication-report:leaked", run_id="run-leaked")
    leaked["summary"]["raw_response"] = {"token": "SECRET"}
    with pytest.raises(ValueError):
        store.store_report(leaked)

    assert b"SECRET" not in (tmp_path / "orbit_platform.db").read_bytes() if (tmp_path / "orbit_platform.db").exists() else True


def test_cross_offer_read_is_not_authorized(tmp_path):
    store = _store(tmp_path)
    stored = store.store_report(_report_payload())

    assert store.get_report(report_id=stored.report_id, offer_id="9999999999") is None
    with pytest.raises(ValueError):
        store.get_report_by_path(
            offer_id="9999999999",
            report_path="3838616043/31/run-001/report.json",
        )
    with pytest.raises(ValueError):
        store.get_report_by_path(
            offer_id="3838616043",
            report_path="../3838616043/31/run-001/report.json",
        )


def test_file_or_summary_tampering_fails_integrity_check(tmp_path):
    store = _store(tmp_path)
    stored = store.store_report(_report_payload())
    report_file = store.reports_root / stored.report_path
    tampered = json.loads(report_file.read_text(encoding="utf-8"))
    tampered["summary"]["evidence"]["external_write_count"] = 999
    report_file.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(ProductPublicationReportIntegrityError):
        store.get_report(report_id=stored.report_id, offer_id="3838616043")


def test_internal_v2_rejects_extra_or_unsafe_target_evidence(tmp_path):
    payload = _report_payload()
    payload["schema_version"] = "product-publication-report/v2"
    payload["execution_identity"] = {
        "skill_digest": "1" * 64,
        "git_commit": "2" * 40,
        "code_digest": "3" * 64,
    }
    payload["targets"] = [
        {
            "target_label": "tiktok:LH_PH",
            "status": "FAILED",
            "evidence": {
                "target_label": "tiktok:LH_PH",
                "status": "FAILED",
                "stage": "PUBLISH",
                "provider_code": "rejected",
                "provider_reason": "safe reason",
                "request_attempted": True,
                "outcome_unknown": False,
                "external_write_count": 0,
                "raw_response": {"token": "SECRET"},
            },
        }
    ]

    with pytest.raises(ValueError, match="fields"):
        _store(tmp_path).store_report(payload)
    assert not (tmp_path / "reports").exists()

    payload["targets"][0]["evidence"].pop("raw_response")
    payload["targets"][0]["evidence"]["provider_reason"] = "https://provider.invalid/item"
    with pytest.raises(ValueError, match="URL"):
        _store(tmp_path).store_report(payload)

    for secret_reason in (
        "Authorization: Bearer top-secret",
        "access_token=top-secret",
        "Cookie: session=top-secret",
    ):
        payload["targets"][0]["evidence"]["provider_reason"] = secret_reason
        with pytest.raises(ValueError, match="secret-shaped"):
            _store(tmp_path).store_report(payload)


def test_internal_v2_file_execution_identity_tamper_is_detected(tmp_path):
    payload = _report_payload()
    payload.update(
        {
            "schema_version": "product-publication-report/v2",
            "execution_identity": {
                "skill_digest": "1" * 64,
                "git_commit": "2" * 40,
                "code_digest": "3" * 64,
            },
            "targets": [],
        }
    )
    store = _store(tmp_path)
    stored = store.store_report(payload)
    report_file = store.reports_root / stored.report_path
    body = json.loads(report_file.read_text(encoding="utf-8"))
    body["execution_identity"]["code_digest"] = "4" * 64
    report_file.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(ProductPublicationReportIntegrityError, match="digest"):
        store.get_report(report_id=stored.report_id, offer_id=payload["offer_id"])


def test_read_only_methods_do_not_create_database(tmp_path):
    store = _store(tmp_path)
    assert store.get_report(report_id="missing", offer_id="3838616043") is None
    assert store.list_reports(offer_id="3838616043", revision=31) == []
    assert store.latest_report(offer_id="3838616043", revision=31) is None
    assert not store.path.exists()
