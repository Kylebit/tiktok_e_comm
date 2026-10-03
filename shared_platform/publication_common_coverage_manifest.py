"""Pure, fail-closed inventory of claimed Offer-wide COMMON history evidence.

Inputs are caller-supplied records, not an authoritative history source.  Even
locally complete records cannot establish that earlier plans or Miaoshou edits
were absent.  This module has no database, provider, HTTP, or approval hooks.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Mapping


SCHEMA = "common-offer-history-coverage/v1"
_SOURCE_KINDS = {"RELEASE_STORE_EXPORT", "MIAOSHOU_ACTIVITY_EXPORT"}
_OUTCOMES = {"NOT_DISPATCHED", "VERIFIED_WRITE", "READBACK_REUSE",
             "SUBMITTED_UNVERIFIED", "RECEIPT_UNKNOWN"}
_SHA_HEX = set("0123456789abcdef")


def _sha(value: object) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= _SHA_HEX


def _instant(value: object) -> datetime | None:
    if type(value) is not str or not value.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None


def _text(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _lookup(rows: Mapping[str, Any], key: object) -> Any:
    return rows.get(key) if _text(key) else None


def _canonical_rows(value: object) -> list[object]:
    return sorted(value, key=lambda row: json.dumps(
        row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _digest(envelope: Mapping[str, Any]) -> str | None:
    try:
        material = dict(envelope)
        for key in ("sources", "plan_runs", "attempts"):
            if type(material.get(key)) is list:
                material[key] = _canonical_rows(material[key])
        raw = json.dumps(material, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        return None


def build_common_coverage_manifest(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Classify local evidence claims; never grant budget or execution authority.

    A digest is a stable fingerprint of supplied JSON, not a signature or proof
    that the supplied export covers the account's true history.
    """
    if not isinstance(envelope, Mapping):
        raise ValueError("COMMON history envelope must be a mapping")
    offer_id = envelope.get("offer_id")
    if not _text(offer_id):
        raise ValueError("exact offer_id is required")
    blockers: set[str] = {"COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN",
                          "COMMON_STANDING_POLICY_AUTHORITY_UNKNOWN",
                          "COMMON_HISTORY_AUTHORITY_UNVERIFIED"}
    incomplete = False
    unknown = False
    if set(envelope) - {"offer_id", "lifecycle_started_at", "as_of",
                        "sources", "plan_runs", "attempts", "out_of_band",
                        "policy_claim"}:
        blockers.add("COMMON_HISTORY_ENVELOPE_INVALID")
        incomplete = True
    input_digest = _digest(envelope)
    if input_digest is None:
        blockers.add("COMMON_HISTORY_ENVELOPE_INVALID")
        incomplete = True
    lifecycle = _instant(envelope.get("lifecycle_started_at"))
    as_of = _instant(envelope.get("as_of"))
    if lifecycle is None or as_of is None or lifecycle > as_of:
        blockers.add("COMMON_HISTORY_INTERVAL_UNKNOWN")
        unknown = True

    raw_sources = envelope.get("sources")
    raw_plans = envelope.get("plan_runs")
    raw_attempts = envelope.get("attempts")
    if not all(type(value) is list for value in
               (raw_sources, raw_plans, raw_attempts)):
        blockers.add("COMMON_HISTORY_ENVELOPE_INVALID")
        incomplete = True
    sources = raw_sources if type(raw_sources) is list else []
    plans = raw_plans if type(raw_plans) is list else []
    attempts = raw_attempts if type(raw_attempts) is list else []

    by_source: dict[str, Mapping[str, Any]] = {}
    intervals: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    for row in sources:
        if not isinstance(row, Mapping):
            blockers.add("COMMON_HISTORY_SOURCE_INVALID")
            incomplete = True
            continue
        source_id = row.get("source_id")
        kind = row.get("source_kind")
        start = _instant(row.get("covered_from"))
        end = _instant(row.get("covered_until"))
        captured = _instant(row.get("captured_at"))
        if (not _text(source_id) or type(kind) is not str
                or kind not in _SOURCE_KINDS
                or not _sha(row.get("evidence_digest"))
                or start is None or end is None or captured is None
                or start > end or captured < end
                or type(row.get("coverage_claim")) is not str
                or row.get("coverage_claim") not in {"COMPLETE", "PARTIAL", "UNKNOWN"}):
            blockers.add("COMMON_HISTORY_SOURCE_INVALID")
            incomplete = True
            continue
        if row.get("offer_id") != offer_id:
            blockers.add("COMMON_SOURCE_OFFER_MISMATCH")
            incomplete = True
            continue
        if source_id in by_source:
            blockers.add("COMMON_HISTORY_SOURCE_DUPLICATE")
            incomplete = True
            continue
        by_source[source_id] = row
        intervals[kind].append((start, end))
        if row["coverage_claim"] != "COMPLETE":
            blockers.add("COMMON_HISTORY_SOURCE_INCOMPLETE")
            incomplete = True

    for kind, missing_code in (
            ("RELEASE_STORE_EXPORT", "COMMON_RELEASE_HISTORY_SOURCE_MISSING"),
            ("MIAOSHOU_ACTIVITY_EXPORT", "COMMON_ACCOUNT_HISTORY_SOURCE_MISSING")):
        windows = sorted(intervals[kind])
        if not windows:
            blockers.add(missing_code)
            unknown = True
        elif lifecycle is not None and as_of is not None:
            reach = lifecycle
            for index, (start, end) in enumerate(windows):
                if index and start < reach:
                    blockers.add("COMMON_HISTORY_SOURCE_OVERLAP_REQUIRES_RECONCILIATION")
                    incomplete = True
                if start > reach:
                    blockers.add("COMMON_HISTORY_INTERVAL_GAP")
                    incomplete = True
                if end > reach:
                    reach = end
            if reach < as_of:
                blockers.add("COMMON_HISTORY_INTERVAL_GAP")
                incomplete = True

    plan_by_run: dict[str, Mapping[str, Any]] = {}
    run_by_plan: dict[str, str] = {}
    if not plans:
        blockers.add("COMMON_RECORDED_PLAN_HISTORY_UNKNOWN")
        unknown = True
    for row in plans:
        if not isinstance(row, Mapping):
            blockers.add("COMMON_PLAN_RUN_INVALID")
            incomplete = True
            continue
        run_id = row.get("run_id")
        count = row.get("attempt_count")
        source = _lookup(by_source, row.get("source_id"))
        if (not _text(row.get("plan_id")) or not _text(run_id)
                or type(count) is not int or count < 0
                or source is None or source.get("source_kind") != "RELEASE_STORE_EXPORT"):
            blockers.add("COMMON_PLAN_RUN_INVALID")
            incomplete = True
            continue
        if row.get("offer_id") != offer_id:
            blockers.add("COMMON_PLAN_RUN_OFFER_MISMATCH")
            incomplete = True
            continue
        if run_id in plan_by_run:
            blockers.add("COMMON_PLAN_RUN_DUPLICATE")
            incomplete = True
            continue
        prior_run = run_by_plan.get(row["plan_id"])
        if prior_run is not None and prior_run != run_id:
            # This input format has no authority for declaring a legitimate
            # second run of one immutable plan.  Preserve both rows, but do
            # not call the local plan history complete.
            blockers.add("COMMON_PLAN_MULTI_RUN_UNRECONCILED")
            incomplete = True
        run_by_plan[row["plan_id"]] = run_id
        plan_by_run[run_id] = row

    by_attempt: dict[tuple[str, int], Mapping[str, Any]] = {}
    receipt_count = 0
    for row in attempts:
        if not isinstance(row, Mapping):
            blockers.add("COMMON_ATTEMPT_INVALID")
            incomplete = True
            continue
        run_id = row.get("run_id")
        n = row.get("attempt")
        outcome = row.get("outcome")
        if type(outcome) is str and outcome in {
                "RECEIPT_UNKNOWN", "SUBMITTED_UNVERIFIED"}:
            blockers.add("COMMON_RECEIPT_RECONCILIATION_REQUIRED")
            incomplete = True
        key = (run_id, n) if _text(run_id) and type(n) is int else None
        prior = by_attempt.get(key) if key is not None else None
        if prior is not None:
            blockers.add("COMMON_ATTEMPT_DUPLICATE" if prior == row
                         else "COMMON_ATTEMPT_CONFLICT")
            incomplete = True
            continue
        if key is not None:
            by_attempt[key] = row
        plan = _lookup(plan_by_run, run_id)
        source = _lookup(by_source, row.get("source_id"))
        occurred = _instant(row.get("occurred_at"))
        if (key is None or n < 1 or plan is None
                or row.get("offer_id") != offer_id
                or row.get("plan_id") != plan.get("plan_id")
                or row.get("source_id") != plan.get("source_id")
                or source is None or occurred is None
                or not _sha(row.get("evidence_digest"))
                or type(outcome) is not str or outcome not in _OUTCOMES):
            blockers.add("COMMON_ATTEMPT_BINDING_MISMATCH")
            incomplete = True
            continue
        if (lifecycle is not None and occurred < lifecycle
                or as_of is not None and occurred > as_of):
            blockers.add("COMMON_ATTEMPT_OUTSIDE_INTERVAL")
            incomplete = True
        source_start = _instant(source.get("covered_from"))
        source_end = _instant(source.get("covered_until"))
        source_captured = _instant(source.get("captured_at"))
        if (source_start is None or source_end is None or source_captured is None
                or not source_start <= occurred <= source_end
                or occurred > source_captured):
            blockers.add("COMMON_ATTEMPT_OUTSIDE_SOURCE_WINDOW")
            incomplete = True
        if row["outcome"] == "VERIFIED_WRITE":
            receipt_count += 1
    for run_id, row in plan_by_run.items():
        if any((run_id, n) not in by_attempt
               for n in range(1, row["attempt_count"] + 1)):
            blockers.add("COMMON_RECORDED_ATTEMPT_MISSING")
            incomplete = True
        if any(key[0] == run_id and key[1] > row["attempt_count"]
               for key in by_attempt):
            blockers.add("COMMON_ATTEMPT_BINDING_MISMATCH")
            incomplete = True

    out_of_band = envelope.get("out_of_band")
    account_source_ids = {source_id for source_id, source in by_source.items()
                          if source.get("source_kind") == "MIAOSHOU_ACTIVITY_EXPORT"}
    if (not isinstance(out_of_band, Mapping)
            or out_of_band.get("status") != "CLAIMED_COMPLETE"):
        blockers.add("COMMON_OUT_OF_BAND_HISTORY_UNKNOWN")
        unknown = True
    elif (not _text(out_of_band.get("source_id"))
          or account_source_ids != {out_of_band.get("source_id")}
          or type(out_of_band.get("observed_edit_count")) is not int
          or out_of_band["observed_edit_count"] < 0):
        blockers.add("COMMON_OUT_OF_BAND_HISTORY_UNKNOWN")
        unknown = True
    elif out_of_band["observed_edit_count"]:
        blockers.add("COMMON_OUT_OF_BAND_EDITS_RECONCILIATION_REQUIRED")
        incomplete = True

    local_scope = ("INCOMPLETE" if incomplete else "UNKNOWN" if unknown
                   else "CLAIMED_COMPLETE_UNVERIFIED")
    return {
        "schema_version": SCHEMA,
        "offer_id": offer_id,
        "input_digest": input_digest,
        "status": "BLOCKED",
        "coverage": "UNKNOWN",
        "recorded_scope_completeness": local_scope,
        "source_ids": sorted(by_source),
        "recorded_plan_run_count": len(plan_by_run),
        "recorded_attempt_count": len(by_attempt),
        "observed_verified_write_receipt_count": receipt_count,
        "historical_write_count": None,
        "historical_write_budget_maximum": None,
        "blockers": sorted(blockers),
        "dispatch_allowed": False,
        "safe_to_retry": False,
        "final_review_available": False,
        "execution_authority": False,
        "external_writes_performed": [],
    }


__all__ = ["SCHEMA", "build_common_coverage_manifest"]
