from __future__ import annotations

import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest


SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import product_center_publication as publication


OFFER_ID = "3882722296"
PLAN_ID = "omnichannel:" + "a" * 64


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _start(platform: str, run_id: str) -> tuple[int, dict]:
    return 202, {
        "ok": True,
        "schema_version": "product-publication-start/v1",
        "platform": platform,
        "run_id": run_id,
        "report_id": f"publication-report:{run_id}",
        # Any server-only or provider-shaped field must not reach stdout.
        "confirmation_token": "must-not-leak",
    }


def _report(
    platform: str,
    run_id: str,
    status: str,
    *,
    offer_id: str = OFFER_ID,
    plan_id: str = PLAN_ID,
) -> tuple[int, dict]:
    return 200, {
        "ok": True,
        "schema_version": "product-publication-report-api/v1",
        "report": {
            "schema_version": "product-publication-report/v1",
            "offer_id": offer_id,
            "plan_id": plan_id,
            "run_id": run_id,
            "report_id": f"publication-report:{run_id}",
            "snapshot": {
                "schema_version": "approved-publication-snapshot/v4",
                "digest": "sha256:" + "b" * 64,
            },
            "status": status,
            "summary": {
                "overall_status": status,
                "platforms": [{"platform": platform, "status": status}],
                "raw_response": {"access_token": "must-not-leak"},
            },
        },
    }


def test_all_uses_only_explicit_v4_runner_routes_and_public_report_polling() -> None:
    calls: list[tuple[str, dict | None]] = []
    run_ids = {
        "TIKTOK": "product-center-tiktok-aaa",
        "SHOPEE": "product-center-shopee-bbb",
        "OZON": "product-center-ozon-ccc",
    }
    endpoints = {
        "/api/product-workspace/publish-tiktok": "TIKTOK",
        "/api/product-workspace/publish-shopee-global": "SHOPEE",
        "/api/product-workspace/publish-ozon": "OZON",
    }

    def request(url: str, *, payload=None, timeout_seconds=0):
        del timeout_seconds
        calls.append((url, payload))
        for endpoint, platform in endpoints.items():
            if url.endswith(endpoint):
                assert payload == {"offer_id": OFFER_ID, "plan_id": PLAN_ID}
                return _start(platform, run_ids[platform])
        assert "/api/product-workspace/publication-report?" in url
        assert payload is None
        run_id = next(value for value in run_ids.values() if value in url)
        platform = next(key for key, value in run_ids.items() if value == run_id)
        return _report(platform, run_id, "PUBLISHED")

    result = publication.run_publication(
        offer_id=OFFER_ID,
        plan_id=PLAN_ID,
        platform="all",
        request=request,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=1,
    )

    assert result["overall_status"] == "PUBLISHED"
    assert [row["platform"] for row in result["platforms"]] == [
        "TIKTOK",
        "SHOPEE",
        "OZON",
    ]
    assert all(row["status"] == "PUBLISHED" for row in result["platforms"])
    assert sum(payload is not None for _, payload in calls) == 3
    serialized = json.dumps(result, ensure_ascii=False)
    for forbidden in (
        "confirmation_token",
        "access_token",
        "raw_response",
        "snapshot",
        '"summary":',
        "must-not-leak",
    ):
        assert forbidden not in serialized
    assert not any("collectbox-action/start" in url for url, _ in calls)
    assert not any(url.endswith("/api/product-workspace/publish") for url, _ in calls)
    assert not any("dashboard" in url for url, _ in calls)


def test_one_platform_rejection_does_not_block_the_other_two() -> None:
    posts: list[str] = []

    def request(url: str, *, payload=None, timeout_seconds=0):
        del timeout_seconds
        if payload is not None:
            posts.append(url)
        if url.endswith("/publish-tiktok"):
            return 409, {"ok": False, "error": "private provider detail"}
        if url.endswith("/publish-shopee-global"):
            return _start("SHOPEE", "product-center-shopee-ok")
        if url.endswith("/publish-ozon"):
            return _start("OZON", "product-center-ozon-ok")
        if "product-center-shopee-ok" in url:
            return _report("SHOPEE", "product-center-shopee-ok", "PUBLISHED")
        if "product-center-ozon-ok" in url:
            return _report("OZON", "product-center-ozon-ok", "FAILED")
        raise AssertionError(url)

    result = publication.run_publication(
        offer_id=OFFER_ID,
        plan_id=PLAN_ID,
        platform="all",
        request=request,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=1,
    )

    assert len(posts) == 3
    assert [(row["platform"], row["status"]) for row in result["platforms"]] == [
        ("TIKTOK", "FAILED"),
        ("SHOPEE", "PUBLISHED"),
        ("OZON", "FAILED"),
    ]
    assert result["overall_status"] == "PARTIAL"
    assert "private provider detail" not in json.dumps(result)


def test_start_identity_mismatch_is_processing_without_polling_or_repost() -> None:
    calls = 0

    def request(url: str, *, payload=None, timeout_seconds=0):
        nonlocal calls
        del url, payload, timeout_seconds
        calls += 1
        return _start("OZON", "product-center-ozon-wrong")

    result = publication.run_publication(
        offer_id=OFFER_ID,
        plan_id=PLAN_ID,
        platform="tiktok",
        request=request,
    )

    assert calls == 1
    assert result["platforms"] == [
        {
            "platform": "TIKTOK",
            "status": "PROCESSING",
            "label": "平台处理中",
            "reason_code": "START_IDENTITY_INVALID",
        }
    ]


def test_accepted_run_that_does_not_finish_remains_processing_without_repost() -> None:
    clock = FakeClock()
    post_count = 0

    def request(url: str, *, payload=None, timeout_seconds=0):
        nonlocal post_count
        del timeout_seconds
        if payload is not None:
            post_count += 1
            return _start("TIKTOK", "product-center-tiktok-pending")
        assert "publication-report" in url
        return _report("TIKTOK", "product-center-tiktok-pending", "PROCESSING")

    result = publication.run_publication(
        offer_id=OFFER_ID,
        plan_id=PLAN_ID,
        platform="tiktok",
        request=request,
        poll_interval_seconds=0.25,
        poll_timeout_seconds=0.5,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert post_count == 1
    assert result["platforms"][0]["status"] == "PROCESSING"
    assert result["platforms"][0]["reason_code"] == "REPORT_STILL_PROCESSING"


def test_initial_processing_projection_without_plan_keeps_accepted_run_identity() -> None:
    clock = FakeClock()
    post_count = 0

    def request(url: str, *, payload=None, timeout_seconds=0):
        nonlocal post_count
        del timeout_seconds
        if payload is not None:
            post_count += 1
            return _start("SHOPEE", "product-center-shopee-pending")
        _status, response = _report(
            "SHOPEE", "product-center-shopee-pending", "PROCESSING"
        )
        response["report"]["schema_version"] = "product-publication-run-status/v1"
        response["report"].pop("plan_id")
        return 200, response

    result = publication.run_publication(
        offer_id=OFFER_ID,
        plan_id=PLAN_ID,
        platform="shopee",
        request=request,
        poll_interval_seconds=0.25,
        poll_timeout_seconds=0.5,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert post_count == 1
    assert result["platforms"][0]["status"] == "PROCESSING"
    assert result["platforms"][0]["reason_code"] == "REPORT_STILL_PROCESSING"


def test_accepted_run_with_temporarily_missing_report_is_processing() -> None:
    clock = FakeClock()

    def request(url: str, *, payload=None, timeout_seconds=0):
        del url, timeout_seconds
        if payload is not None:
            return _start("OZON", "product-center-ozon-pending")
        return 404, {"ok": False, "error": "internal path must not leak"}

    result = publication.run_publication(
        offer_id=OFFER_ID,
        plan_id=PLAN_ID,
        platform="ozon",
        request=request,
        poll_interval_seconds=0.25,
        poll_timeout_seconds=0.5,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert result["platforms"][0]["status"] == "PROCESSING"
    assert result["platforms"][0]["reason_code"] == "REPORT_UNAVAILABLE"
    assert "internal path" not in json.dumps(result)


def test_report_identity_conflict_never_promotes_success_or_leaks_report() -> None:
    def request(url: str, *, payload=None, timeout_seconds=0):
        del timeout_seconds
        if payload is not None:
            return _start("SHOPEE", "product-center-shopee-id")
        return _report(
            "SHOPEE",
            "product-center-shopee-id",
            "PUBLISHED",
            offer_id="9999999999",
        )

    result = publication.run_publication(
        offer_id=OFFER_ID,
        plan_id=PLAN_ID,
        platform="shopee",
        request=request,
        poll_interval_seconds=0.01,
        poll_timeout_seconds=1,
    )

    assert result["platforms"] == [
        {
            "platform": "SHOPEE",
            "status": "PROCESSING",
            "label": "平台处理中",
            "report_id": "publication-report:product-center-shopee-id",
            "run_id": "product-center-shopee-id",
            "reason_code": "REPORT_IDENTITY_INVALID",
        }
    ]
    assert "9999999999" not in json.dumps(result)


def test_cli_requires_execute_and_exact_plan_id() -> None:
    with patch.object(sys, "argv", ["product_center_publication.py", "--offer-id", OFFER_ID, "--plan-id", PLAN_ID]):
        with pytest.raises(SystemExit) as missing_execute:
            publication.main()
    assert missing_execute.value.code == 2

    with pytest.raises(ValueError, match="plan_id"):
        publication.run_publication(
            offer_id=OFFER_ID,
            plan_id="",
            platform="tiktok",
            request=lambda *_args, **_kwargs: pytest.fail("must not call HTTP"),
        )
