#!/usr/bin/env python3
"""Production control wrapper for Product Center frozen-v4 publication runs.

This client never constructs platform payloads.  It starts one server-owned
async Runner per selected platform and polls the immutable public report.  The
only stdout/file artifact is a sanitized four-state summary.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import time
from typing import Any, Callable, Mapping
import urllib.parse

from _common import DEFAULT_BASE_URL, emit, json_request


START_SCHEMA = "product-publication-start/v1"
REPORT_API_SCHEMA = "product-publication-report-api/v1"
SNAPSHOT_SCHEMA = "approved-publication-snapshot/v4"
SUMMARY_SCHEMA = "product-center-skill-publication-summary/v1"

PLATFORM_ENDPOINTS = {
    "TIKTOK": "/api/product-workspace/publish-tiktok",
    "SHOPEE": "/api/product-workspace/publish-shopee-global",
    "OZON": "/api/product-workspace/publish-ozon",
}
PLATFORM_ORDER = tuple(PLATFORM_ENDPOINTS)
PUBLIC_STATUSES = frozenset({"PUBLISHED", "PROCESSING", "PARTIAL", "FAILED"})
TERMINAL_STATUSES = frozenset({"PUBLISHED", "PARTIAL", "FAILED"})
STATUS_LABELS = {
    "PUBLISHED": "发布成功",
    "PROCESSING": "平台处理中",
    "PARTIAL": "部分成功",
    "FAILED": "发布失败",
}
SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
REPORT_SCHEMAS = frozenset(
    {"product-publication-report/v1", "product-publication-run-status/v1"}
)

Request = Callable[..., tuple[int, dict[str, Any]]]


class IdentityError(ValueError):
    """A Product Center start or report identity conflicted with the command."""


def _offer_id(value: object) -> str:
    if type(value) is not str or value != value.strip() or not value.isdigit():
        raise ValueError("offer_id must be a positive decimal string")
    if int(value) <= 0 or len(value) > 32:
        raise ValueError("offer_id must be a positive decimal string")
    return value


def _plan_id(value: object) -> str:
    if type(value) is not str or value != value.strip() or not value:
        raise ValueError("plan_id must be an exact non-empty string")
    if len(value) > 512:
        raise ValueError("plan_id is too long")
    return value


def _platforms(value: str) -> tuple[str, ...]:
    normalized = str(value).strip().upper()
    if normalized == "ALL":
        return PLATFORM_ORDER
    if normalized not in PLATFORM_ENDPOINTS:
        raise ValueError("platform must be all, tiktok, shopee, or ozon")
    return (normalized,)


def _start_identity(payload: Mapping[str, Any], *, platform: str) -> tuple[str, str]:
    if payload.get("ok") is not True or payload.get("schema_version") != START_SCHEMA:
        raise IdentityError("start response schema is invalid")
    if payload.get("platform") != platform:
        raise IdentityError("start response platform conflicts with request")
    run_id = payload.get("run_id")
    report_id = payload.get("report_id")
    if type(run_id) is not str or not SAFE_RUN_ID.fullmatch(run_id):
        raise IdentityError("start response run_id is invalid")
    if report_id != f"publication-report:{run_id}":
        raise IdentityError("start response report_id conflicts with run_id")
    return run_id, report_id


def _report_status(
    payload: Mapping[str, Any],
    *,
    offer_id: str,
    plan_id: str,
    platform: str,
    run_id: str,
    report_id: str,
) -> str:
    if payload.get("ok") is not True or payload.get("schema_version") != REPORT_API_SCHEMA:
        raise IdentityError("publication report envelope is invalid")
    report = payload.get("report")
    if not isinstance(report, Mapping):
        raise IdentityError("publication report is missing")
    if report.get("schema_version") not in REPORT_SCHEMAS:
        raise IdentityError("publication report schema is invalid")
    expected = {
        "offer_id": offer_id,
        "plan_id": plan_id,
        "run_id": run_id,
        "report_id": report_id,
    }
    if any(report.get(field) != value for field, value in expected.items()):
        raise IdentityError("publication report identity conflicts with start response")
    snapshot = report.get("snapshot")
    if (
        not isinstance(snapshot, Mapping)
        or snapshot.get("schema_version") != SNAPSHOT_SCHEMA
        or type(snapshot.get("digest")) is not str
        or not SHA256_DIGEST.fullmatch(snapshot["digest"])
    ):
        raise IdentityError("publication report is not bound to frozen v4")
    status = report.get("status")
    if status not in PUBLIC_STATUSES:
        raise IdentityError("publication report status is invalid")
    summary = report.get("summary")
    rows = summary.get("platforms") if isinstance(summary, Mapping) else None
    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], Mapping)
        or rows[0].get("platform") != platform
        or rows[0].get("status") != status
        or summary.get("overall_status") != status
    ):
        raise IdentityError("publication report platform scope is invalid")
    return status


def _row(
    platform: str,
    status: str,
    *,
    report_id: str | None = None,
    run_id: str | None = None,
    reason_code: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "platform": platform,
        "status": status,
        "label": STATUS_LABELS[status],
    }
    if report_id:
        result["report_id"] = report_id
    if run_id:
        result["run_id"] = run_id
    if reason_code:
        result["reason_code"] = reason_code
    return result


def _poll_report(
    *,
    base_url: str,
    offer_id: str,
    plan_id: str,
    platform: str,
    run_id: str,
    report_id: str,
    request: Request,
    request_timeout_seconds: float,
    poll_interval_seconds: float,
    poll_timeout_seconds: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    deadline = monotonic() + poll_timeout_seconds
    query = urllib.parse.urlencode({"offer_id": offer_id, "report_id": report_id})
    url = (
        f"{base_url.rstrip('/')}/api/product-workspace/publication-report?{query}"
    )
    last_status: str | None = None
    while True:
        http_status, payload = request(
            url,
            timeout_seconds=request_timeout_seconds,
        )
        if http_status == 200:
            try:
                last_status = _report_status(
                    payload,
                    offer_id=offer_id,
                    plan_id=plan_id,
                    platform=platform,
                    run_id=run_id,
                    report_id=report_id,
                )
            except IdentityError:
                # The start was accepted.  Conflicting readback cannot prove
                # failure and must never promote success.
                return _row(
                    platform,
                    "PROCESSING",
                    report_id=report_id,
                    run_id=run_id,
                    reason_code="REPORT_IDENTITY_INVALID",
                )
            if last_status in TERMINAL_STATUSES:
                return _row(
                    platform,
                    last_status,
                    report_id=report_id,
                    run_id=run_id,
                )
        if monotonic() >= deadline:
            return _row(
                platform,
                "PROCESSING",
                report_id=report_id,
                run_id=run_id,
                reason_code=(
                    "REPORT_STILL_PROCESSING"
                    if last_status == "PROCESSING"
                    else "REPORT_UNAVAILABLE"
                ),
            )
        sleep(min(poll_interval_seconds, max(0.0, deadline - monotonic())))


def _run_platform(
    *,
    base_url: str,
    offer_id: str,
    plan_id: str,
    platform: str,
    request: Request,
    request_timeout_seconds: float,
    poll_interval_seconds: float,
    poll_timeout_seconds: float,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    endpoint = PLATFORM_ENDPOINTS[platform]
    http_status, payload = request(
        f"{base_url.rstrip('/')}{endpoint}",
        payload={"offer_id": offer_id, "plan_id": plan_id},
        timeout_seconds=request_timeout_seconds,
    )
    if http_status == 0:
        # A timed-out POST can still have reached the server.  Never repost it
        # from this run and never claim a deterministic failure.
        return _row(platform, "PROCESSING", reason_code="START_OUTCOME_UNKNOWN")
    if http_status != 202:
        return _row(platform, "FAILED", reason_code="START_REJECTED")
    try:
        run_id, report_id = _start_identity(payload, platform=platform)
    except IdentityError:
        # HTTP 202 can mean the server queued work even if the response identity
        # is malformed.  Without a trustworthy report key the only truthful
        # public state is processing/unknown, never deterministic failure.
        return _row(platform, "PROCESSING", reason_code="START_IDENTITY_INVALID")
    return _poll_report(
        base_url=base_url,
        offer_id=offer_id,
        plan_id=plan_id,
        platform=platform,
        run_id=run_id,
        report_id=report_id,
        request=request,
        request_timeout_seconds=request_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        poll_timeout_seconds=poll_timeout_seconds,
        monotonic=monotonic,
        sleep=sleep,
    )


def _overall_status(rows: list[Mapping[str, Any]]) -> str:
    statuses = [row.get("status") for row in rows]
    if not statuses:
        return "FAILED"
    if len(statuses) == 1:
        return str(statuses[0])
    if all(status == "PUBLISHED" for status in statuses):
        return "PUBLISHED"
    if all(status == "FAILED" for status in statuses):
        return "FAILED"
    if "PROCESSING" in statuses and not (
        "PUBLISHED" in statuses and "FAILED" in statuses
    ):
        return "PROCESSING"
    return "PARTIAL"


def run_publication(
    *,
    offer_id: str,
    plan_id: str,
    platform: str = "all",
    base_url: str = DEFAULT_BASE_URL,
    request: Request = json_request,
    request_timeout_seconds: float = 30,
    poll_interval_seconds: float = 1,
    poll_timeout_seconds: float = 300,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    safe_offer = _offer_id(offer_id)
    safe_plan = _plan_id(plan_id)
    selected = _platforms(platform)
    if request_timeout_seconds <= 0:
        raise ValueError("request_timeout_seconds must be positive")
    if poll_interval_seconds <= 0 or poll_timeout_seconds < 0:
        raise ValueError("polling durations are invalid")
    rows: list[dict[str, Any]] = []
    for selected_platform in selected:
        try:
            row = _run_platform(
                base_url=base_url,
                offer_id=safe_offer,
                plan_id=safe_plan,
                platform=selected_platform,
                request=request,
                request_timeout_seconds=request_timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                poll_timeout_seconds=poll_timeout_seconds,
                monotonic=monotonic,
                sleep=sleep,
            )
        except Exception:
            # No exception detail enters the public artifact.  Other platform
            # runs must still start.
            row = _row(
                selected_platform,
                "FAILED",
                reason_code="CONTROL_WRAPPER_FAILED",
            )
        rows.append(row)
    overall = _overall_status(rows)
    return {
        "schema_version": SUMMARY_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "offer_id": safe_offer,
        "plan_id": safe_plan,
        "overall_status": overall,
        "overall_label": STATUS_LABELS[overall],
        "platforms": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start and poll frozen-v4 Product Center publication runs"
    )
    parser.add_argument("--offer-id", required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument(
        "--platform", choices=("all", "tiktok", "shopee", "ozon"), default="all"
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--request-timeout-seconds", type=float, default=30)
    parser.add_argument("--poll-interval-seconds", type=float, default=1)
    parser.add_argument("--poll-timeout-seconds", type=float, default=300)
    parser.add_argument("--output")
    parser.add_argument("--execute", action="store_true", required=True)
    args = parser.parse_args()
    result = run_publication(
        offer_id=args.offer_id,
        plan_id=args.plan_id,
        platform=args.platform,
        base_url=args.base_url,
        request_timeout_seconds=args.request_timeout_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        poll_timeout_seconds=args.poll_timeout_seconds,
    )
    emit(result, args.output)
    return 0 if result["overall_status"] in {"PUBLISHED", "PROCESSING"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
