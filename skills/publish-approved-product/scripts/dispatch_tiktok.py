#!/usr/bin/env python3
"""DEPRECATED COMPATIBILITY: direct TikTok dispatch for incident diagnosis."""

from __future__ import annotations

import argparse
from typing import Any, Mapping
from urllib.parse import urlencode
from uuid import uuid4

from _common import (
    DEFAULT_BASE_URL,
    approved_request,
    emit,
    json_request,
    load_json,
    platform_selected,
    safe_response,
    safe_text,
    utc_now,
)


def _publish(
    snapshot: Mapping[str, Any], *, base_url: str, timeout_seconds: float,
    target_labels: list[str] | None = None,
) -> tuple[int, dict[str, Any]]:
    request = approved_request(snapshot)
    if target_labels is not None:
        request["tiktok_target_scope"] = target_labels
    return json_request(
        f"{base_url.rstrip('/')}/api/product-workspace/publish-tiktok",
        payload=request,
        timeout_seconds=timeout_seconds,
    )


def _fact(status: int, response: Mapping[str, Any], *, prepared: bool) -> dict[str, Any]:
    accepted = response.get("success") is True or response.get("ok") is True
    target_rows = response.get("targets")
    target_unknown = isinstance(target_rows, list) and any(
        isinstance(row, Mapping)
        and str(row.get("outcome") or "").strip().upper() == "UNKNOWN"
        for row in target_rows
    )
    explicit_unknown = (
        type(response.get("unknown_target_count")) is int
        and response["unknown_target_count"] > 0
    ) or target_unknown
    write_outcome = (
        "UNKNOWN"
        if status == 0 or explicit_unknown
        else ("ACCEPTED" if accepted else "REJECTED")
    )
    return {
        "schema_version": "platform-dispatch-fact/v1",
        "platform": "tiktok",
        "attempted": True,
        "http_status": status,
        "accepted": accepted,
        "write_outcome": write_outcome,
        "external_write_count": response.get("external_write_count") if type(response.get("external_write_count")) is int else None,
        "message": safe_text(response.get("message") or response.get("error")),
        "safe_response": safe_response(response),
        "fresh_draft_batch_created": prepared,
        "observed_at": utc_now(),
    }


def _tiktok_preparation_row(
    response: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    action = response.get("action")
    platforms = action.get("platforms") if isinstance(action, Mapping) else None
    if not isinstance(platforms, list):
        return None
    matches = [
        row
        for row in platforms
        if isinstance(row, Mapping)
        and str(row.get("platform") or "").strip().upper() == "TIKTOK"
    ]
    return matches[0] if len(matches) == 1 else None


def _preparation_failure_fact(
    status: int,
    response: Mapping[str, Any],
    row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    error = row.get("error") if isinstance(row, Mapping) else response.get("error")
    code = error.get("code") if isinstance(error, Mapping) else None
    message = safe_text(
        code
        or (error.get("detail") if isinstance(error, Mapping) else None)
        or response.get("message")
        or response.get("error")
        or "TikTok fresh draft preparation did not become publishable"
    )
    count = response.get("external_write_count")
    if type(count) is not int and isinstance(row, Mapping):
        count = row.get("external_write_count")
    return {
        "schema_version": "platform-dispatch-fact/v1",
        "platform": "tiktok",
        "attempted": True,
        "http_status": status,
        "accepted": False,
        "write_outcome": "UNKNOWN" if type(count) is not int or count > 0 else "REJECTED",
        "external_write_count": count if type(count) is int else None,
        "message": message,
        "safe_response": safe_response(response),
        "fresh_draft_batch_created": False,
        "observed_at": utc_now(),
    }


def _preflight_failure_fact(
    status: int,
    response: Mapping[str, Any],
    row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    safe_response_with_zero_writes = dict(response)
    safe_response_with_zero_writes["external_write_count"] = 0
    fact = _preparation_failure_fact(
        status,
        safe_response_with_zero_writes,
        row,
    )
    fact["attempted"] = False
    fact["write_outcome"] = "REJECTED"
    fact["external_write_count"] = 0
    if row is None:
        fact["message"] = safe_text(
            response.get("message")
            or response.get("error")
            or "TikTok preflight did not return exactly one TikTok preparation row"
        )
    return fact


def _selected_tiktok_targets(snapshot: Mapping[str, Any]) -> list[str]:
    platforms = snapshot.get("platforms")
    row = platforms.get("tiktok") if isinstance(platforms, Mapping) else None
    targets = row.get("targets") if isinstance(row, Mapping) else None
    return [str(target) for target in targets or [] if isinstance(target, str)]


def _validated_target_scope(
    snapshot: Mapping[str, Any], target_labels: list[str] | None,
) -> list[str] | None:
    if target_labels is None:
        return None
    if (
        type(target_labels) is not list
        or not target_labels
        or any(type(label) is not str or not label.strip() for label in target_labels)
        or len(set(target_labels)) != len(target_labels)
    ):
        raise ValueError("target labels must be a non-empty unique list")
    approved = set(_selected_tiktok_targets(snapshot))
    if any(label not in approved for label in target_labels):
        raise ValueError("target labels may contain only approved TikTok targets")
    return list(target_labels)


def _row_is_ready_for_snapshot(
    row: Mapping[str, Any] | None,
    snapshot: Mapping[str, Any],
    target_labels: list[str] | None = None,
) -> bool:
    if not isinstance(row, Mapping) or row.get("publishable") is not True:
        return False
    ready = row.get("publishable_targets")
    if ready is None:
        # Compatibility with older Product Center responses.
        return True
    expected = target_labels or _selected_tiktok_targets(snapshot)
    return isinstance(ready, list) and all(label in ready for label in expected)


def _start_fresh_tiktok_batch(
    snapshot: Mapping[str, Any],
    *,
    base_url: str,
    timeout_seconds: float,
) -> tuple[int, dict[str, Any], Mapping[str, Any] | None]:
    request = approved_request(snapshot)
    prepare_request = {
        **request,
        "confirm_collectbox_action": True,
        "approved_by": "Kyle",
        "restart_collectbox_action": True,
        "reimport_request_id": str(uuid4()),
        "platform_scope": "TIKTOK",
    }
    status, response = json_request(
        f"{base_url.rstrip('/')}/api/product-workspace/collectbox-action/start",
        payload=prepare_request,
        timeout_seconds=timeout_seconds,
    )
    return status, response, _tiktok_preparation_row(response)


def _start_initial_tiktok_batch(
    snapshot: Mapping[str, Any],
    *,
    base_url: str,
    timeout_seconds: float,
) -> tuple[int, dict[str, Any], Mapping[str, Any] | None]:
    """Start the first TikTok preparation without inventing a reimport."""

    prepare_request = {
        **approved_request(snapshot),
        "confirm_collectbox_action": True,
        "approved_by": "Kyle",
        "platform_scope": "TIKTOK",
    }
    status, response = json_request(
        f"{base_url.rstrip('/')}/api/product-workspace/collectbox-action/start",
        payload=prepare_request,
        timeout_seconds=timeout_seconds,
    )
    return status, response, _tiktok_preparation_row(response)


def _is_pristine_initial_action(
    response: Mapping[str, Any],
    row: Mapping[str, Any] | None,
) -> bool:
    """Return true only when no TikTok preparation attempt has begun."""

    action = response.get("action")
    writes = row.get("external_writes") if isinstance(row, Mapping) else None
    return (
        isinstance(action, Mapping)
        and action.get("status") == "READY"
        and action.get("start_allowed") is True
        and isinstance(row, Mapping)
        and row.get("status") == "PENDING"
        and row.get("attempt_count") == 0
        and isinstance(writes, Mapping)
        and writes.get("count") in {None, 0}
        and writes.get("classes") == []
    )


def dispatch_with_fresh_drafts(
    snapshot: Mapping[str, Any], *, base_url: str,
    timeout_seconds: float, execute: bool,
    target_labels: list[str] | None = None,
) -> dict[str, Any]:
    if not platform_selected(snapshot, "tiktok"):
        return {"platform": "tiktok", "attempted": False, "accepted": False, "write_outcome": "NOT_ATTEMPTED"}
    if not execute:
        raise RuntimeError("dispatch requires --execute")
    target_labels = _validated_target_scope(snapshot, target_labels)
    request = approved_request(snapshot)
    status_query = urlencode(
        {"offer_id": request["offer_id"], "plan_id": request["plan_id"]}
    )
    preflight_status, preflight_response = json_request(
        f"{base_url.rstrip('/')}/api/product-workspace/collectbox-action/status?{status_query}",
        timeout_seconds=timeout_seconds,
    )
    preflight_row = _tiktok_preparation_row(preflight_response)
    if preflight_status != 200 or preflight_row is None:
        return _preflight_failure_fact(
            preflight_status,
            preflight_response,
            preflight_row,
        )
    if (
        not _row_is_ready_for_snapshot(preflight_row, snapshot, target_labels)
    ):
        if target_labels is not None:
            return _preparation_failure_fact(
                preflight_status,
                preflight_response,
                preflight_row,
            )
        starter = (
            _start_initial_tiktok_batch
            if _is_pristine_initial_action(preflight_response, preflight_row)
            else _start_fresh_tiktok_batch
        )
        prepare_status, prepare_response, preparation_row = starter(
            snapshot,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
        if (
            prepare_status != 200
            or not _row_is_ready_for_snapshot(preparation_row, snapshot)
        ):
            return _preparation_failure_fact(
                prepare_status,
                prepare_response,
                preparation_row,
            )
        status, response = _publish(
            snapshot, base_url=base_url, timeout_seconds=timeout_seconds,
            target_labels=target_labels,
        )
        return _fact(status, response, prepared=True)
    status, response = _publish(
        snapshot, base_url=base_url, timeout_seconds=timeout_seconds,
        target_labels=target_labels,
    )
    if response.get("success") is True or response.get("ok") is True:
        return _fact(status, response, prepared=False)
    message = safe_text(response.get("message") or response.get("error")).lower()
    if not any(marker in message for marker in ("草稿身份不完整", "draft identity", "collect-box")):
        return _fact(status, response, prepared=False)
    if target_labels is not None:
        return _fact(status, response, prepared=False)
    prepare_status, prepare_response, preparation_row = _start_fresh_tiktok_batch(
        snapshot,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
    )
    if (
        prepare_status != 200
        or not _row_is_ready_for_snapshot(preparation_row, snapshot)
    ):
        return _preparation_failure_fact(
            prepare_status,
            prepare_response,
            preparation_row,
        )
    status, response = _publish(
        snapshot, base_url=base_url, timeout_seconds=timeout_seconds,
    )
    return _fact(status, response, prepared=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--target-label", action="append", dest="target_labels")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = dispatch_with_fresh_drafts(
            load_json(args.snapshot), base_url=args.base_url,
            timeout_seconds=args.timeout_seconds, execute=args.execute,
            target_labels=args.target_labels,
        )
        emit(result, args.output)
        return 0 if result.get("accepted") is True else 1
    except Exception as error:
        emit({"platform": "tiktok", "attempted": False, "accepted": False, "write_outcome": "NOT_ATTEMPTED", "message": safe_text(error)}, args.output)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
