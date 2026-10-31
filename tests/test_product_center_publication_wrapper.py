"""Offline contracts for the public Product Center publication wrapper."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import threading
import time
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "publish-approved-product" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = spec_from_file_location(
    "product_center_publication_under_test",
    SCRIPT_DIR / "product_center_publication.py",
)
assert SPEC is not None and SPEC.loader is not None
PUBLICATION = module_from_spec(SPEC)
SPEC.loader.exec_module(PUBLICATION)


@pytest.fixture(autouse=True)
def _isolated_dispatch_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("ORBIT_PUBLICATION_DISPATCH_ROOT", str(tmp_path / "dispatch"))


def _published_report(platform):
    run_id = "speed-" + platform.lower()
    return {
        "ok": True, "schema_version": PUBLICATION.REPORT_API_SCHEMA,
        "report": {
            "schema_version": "product-publication-report/v1",
            "offer_id": "987650123", "plan_id": "fresh-three-model-plan",
            "run_id": run_id, "report_id": f"publication-report:{run_id}",
            "snapshot": {"schema_version": PUBLICATION.SNAPSHOT_SCHEMA,
                         "digest": "sha256:" + "9" * 64},
            "status": "PUBLISHED",
            "summary": {"overall_status": "PUBLISHED", "platforms": [
                {"platform": platform, "status": "PUBLISHED"}]},
        },
    }


def test_independent_platforms_start_before_any_platform_poll():
    calls = []

    def request(url, *, payload=None, timeout_seconds):
        if payload is not None:
            platform = next(p for p, endpoint in PUBLICATION.PLATFORM_ENDPOINTS.items()
                            if url.endswith(endpoint))
            calls.append(("POST", platform))
            run_id = "speed-" + platform.lower()
            return 202, {"ok": True, "schema_version": PUBLICATION.START_SCHEMA,
                         "platform": platform, "run_id": run_id,
                         "report_id": f"publication-report:{run_id}"}
        calls.append(("GET", url))
        platform = url.rsplit("speed-", 1)[1].upper()
        return 200, _published_report(platform)

    result = PUBLICATION.run_publication(
        offer_id="987650123", plan_id="fresh-three-model-plan", request=request,
        poll_timeout_seconds=0,
    )
    assert result["overall_status"] == "PUBLISHED"
    assert {item[1] for item in calls[:3]} == {"TIKTOK", "SHOPEE", "OZON"}
    assert all(item[0] == "POST" for item in calls[:3])


def test_independent_starts_respect_parallel_limit():
    lock = threading.Lock()
    active = 0
    peak = 0
    two_started = threading.Event()

    def request(url, *, payload=None, timeout_seconds):
        nonlocal active, peak
        if payload is not None:
            platform = next(p for p, endpoint in PUBLICATION.PLATFORM_ENDPOINTS.items()
                            if url.endswith(endpoint))
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    two_started.set()
            assert two_started.wait(2), "independent starts were serialized"
            time.sleep(0.01)
            with lock:
                active -= 1
            run_id = "speed-" + platform.lower()
            return 202, {"ok": True, "schema_version": PUBLICATION.START_SCHEMA,
                         "platform": platform, "run_id": run_id,
                         "report_id": f"publication-report:{run_id}"}
        platform = url.rsplit("speed-", 1)[1].upper()
        return 200, _published_report(platform)

    result = PUBLICATION.run_publication(
        offer_id="987650123", plan_id="fresh-three-model-plan", request=request,
        poll_timeout_seconds=0, max_parallel=2,
    )
    assert result["overall_status"] == "PUBLISHED"
    assert peak == 2


def test_all_platforms_continue_in_order_after_one_start_rejection():
    starts = []

    def request(url, *, payload=None, timeout_seconds):
        del timeout_seconds
        if payload is not None:
            platform = next(
                key
                for key, endpoint in PUBLICATION.PLATFORM_ENDPOINTS.items()
                if url.endswith(endpoint)
            )
            starts.append(platform)
            if platform == "TIKTOK":
                return 409, {"ok": False}
            run_id = f"run-{platform.lower()}"
            return 202, {
                "ok": True,
                "schema_version": PUBLICATION.START_SCHEMA,
                "platform": platform,
                "run_id": run_id,
                "report_id": f"publication-report:{run_id}",
            }

        report_id = url.split("report_id=", 1)[1]
        run_id = report_id.removeprefix("publication-report%3A")
        platform = run_id.removeprefix("run-").upper()
        return 200, {
            "ok": True,
            "schema_version": PUBLICATION.REPORT_API_SCHEMA,
            "report": {
                "schema_version": "product-publication-report/v1",
                "offer_id": "3956742887",
                "plan_id": "approved-plan",
                "run_id": run_id,
                "report_id": f"publication-report:{run_id}",
                "snapshot": {
                    "schema_version": PUBLICATION.SNAPSHOT_SCHEMA,
                    "digest": "sha256:" + "1" * 64,
                },
                "status": "PUBLISHED",
                "summary": {
                    "overall_status": "PUBLISHED",
                    "platforms": [
                        {"platform": platform, "status": "PUBLISHED"}
                    ],
                },
            },
        }

    result = PUBLICATION.run_publication(
        offer_id="3956742887",
        plan_id="approved-plan",
        platform="all",
        request=request,
        poll_interval_seconds=0.001,
        poll_timeout_seconds=0,
        sleep=lambda _seconds: None,
    )

    assert set(starts) == {"TIKTOK", "SHOPEE", "OZON"}
    assert [row["status"] for row in result["platforms"]] == [
        "FAILED",
        "PUBLISHED",
        "PUBLISHED",
    ]
    assert result["overall_status"] == "PARTIAL"
    assert result["scope_kind"] == "FULL_PRODUCT"
    assert result["selected_platforms"] == ["TIKTOK", "SHOPEE", "OZON"]
    assert result["target_scope"] == []


def test_scoped_success_is_labeled_as_this_scope_not_full_product():
    def request(url, *, payload=None, timeout_seconds):
        del timeout_seconds
        if payload is not None:
            return 202, {
                "ok": True,
                "schema_version": PUBLICATION.START_SCHEMA,
                "platform": "SHOPEE",
                "run_id": "run-shopee-scoped",
                "report_id": "publication-report:run-shopee-scoped",
            }
        return 200, {
            "ok": True,
            "schema_version": PUBLICATION.REPORT_API_SCHEMA,
            "report": {
                "schema_version": "product-publication-report/v1",
                "offer_id": "3956742887",
                "plan_id": "approved-plan",
                "run_id": "run-shopee-scoped",
                "report_id": "publication-report:run-shopee-scoped",
                "snapshot": {
                    "schema_version": PUBLICATION.SNAPSHOT_SCHEMA,
                    "digest": "sha256:" + "2" * 64,
                },
                "status": "PUBLISHED",
                "summary": {
                    "overall_status": "PUBLISHED",
                    "platforms": [
                        {"platform": "SHOPEE", "status": "PUBLISHED"}
                    ],
                },
            },
        }

    result = PUBLICATION.run_publication(
        offer_id="3956742887",
        plan_id="approved-plan",
        platform="shopee",
        target_scope=("shopee:MY", "shopee:TH", "shopee:VN"),
        request=request,
        poll_interval_seconds=0.001,
        poll_timeout_seconds=0,
        sleep=lambda _seconds: None,
    )

    assert result["overall_status"] == "PUBLISHED"
    assert result["overall_label"] == "本次范围完成"
    assert result["scope_kind"] == "TARGET_SCOPED"
    assert result["selected_platforms"] == ["SHOPEE"]
    assert result["target_scope"] == ["shopee:MY", "shopee:TH", "shopee:VN"]


def test_single_platform_summary_is_explicitly_platform_scoped():
    result = PUBLICATION.run_publication(
        offer_id="3956742887",
        plan_id="approved-plan",
        platform="ozon",
        request=lambda _url, **_kwargs: (409, {"ok": False}),
    )

    assert result["scope_kind"] == "PLATFORM"
    assert result["selected_platforms"] == ["OZON"]
    assert result["target_scope"] == []


@pytest.mark.parametrize("completed", [True, False, None, "true", 1])
def test_processing_readback_completion_stops_only_for_explicit_true(completed):
    calls = []
    sleeps = []
    clock = iter([0, 0.1, 0.2, 2])

    def request(url, *, payload=None, timeout_seconds):
        calls.append("POST" if payload is not None else "GET")
        if payload is not None:
            return 202, {
                "ok": True, "schema_version": PUBLICATION.START_SCHEMA,
                "platform": "OZON", "run_id": "bounded-run",
                "report_id": "publication-report:bounded-run",
            }
        return 200, {
            "ok": True, "schema_version": PUBLICATION.REPORT_API_SCHEMA,
            "report": {
                "schema_version": "product-publication-report/v1",
                "offer_id": "3956742887", "plan_id": "approved-plan",
                "run_id": "bounded-run", "report_id": "publication-report:bounded-run",
                "snapshot": {"schema_version": PUBLICATION.SNAPSHOT_SCHEMA,
                             "digest": "sha256:" + "3" * 64},
                "status": "PROCESSING",
                "summary": {
                    "overall_status": "PROCESSING",
                    "platforms": [{"platform": "OZON", "status": "PROCESSING"}],
                    "evidence": {} if completed is None else {"readback_completed": completed},
                },
            },
        }

    result = PUBLICATION.run_publication(
        offer_id="3956742887", plan_id="approved-plan", platform="ozon",
        request=request, poll_interval_seconds=0.1, poll_timeout_seconds=1,
        monotonic=lambda: next(clock), sleep=sleeps.append,
    )
    assert result["overall_status"] == "PROCESSING"
    row = result["platforms"][0]
    assert row["run_id"] == "bounded-run"
    assert row["report_id"] == "publication-report:bounded-run"
    if completed is True:
        assert row["reason_code"] == "READBACK_COMPLETE_PROCESSING"
        assert calls == ["POST", "GET"]
        assert sleeps == []
    else:
        assert row["reason_code"] == "REPORT_STILL_PROCESSING"
        assert calls == ["POST", "GET", "GET"]
        assert sleeps == [0.1]
