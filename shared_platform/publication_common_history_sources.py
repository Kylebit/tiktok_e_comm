"""Offline, byte-bound adapter for caller-supplied COMMON history exports.

This reads only explicit JSON files below an explicit local evidence root. An
export's own COMPLETE claim and an expected SHA supplied by the same caller are
not proof of the Offer's complete history or of source authenticity. No provider,
database, worker, approval, or network entry point is available here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from shared_platform.publication_common_coverage_manifest import build_common_coverage_manifest


SCHEMA = "common-history-export/v1"
MAX_EXPORT_BYTES = 4 * 1024 * 1024
MAX_EXPORTS = 20
_KINDS = {"RELEASE_STORE_EXPORT", "MIAOSHOU_ACTIVITY_EXPORT"}
_META = ("source_id", "source_kind", "offer_id", "captured_at",
         "covered_from", "covered_until")
_SPEC_KEYS = frozenset((*_META, "path", "expected_sha256"))
_EXPORT_KEYS = frozenset(("schema_version", *_META, "coverage_claim",
                         "plan_runs", "attempts", "observed_edit_count"))
_PLAN_KEYS = frozenset(("plan_id", "run_id", "attempt_count"))
_ATTEMPT_KEYS = frozenset(("plan_id", "run_id", "attempt", "occurred_at",
                            "outcome", "evidence_digest"))


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _path(root: Path, relative: object) -> Path | None:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    name = Path(relative)
    if name.is_absolute() or name.suffix.lower() != ".json" or ".." in name.parts:
        return None
    try:
        candidate = (root / name).resolve(strict=True)
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() and candidate.suffix.lower() == ".json" else None


def _rows(export: Mapping[str, Any], source_id: str, offer_id: str,
          key: str, allowed: frozenset[str]) -> list[dict[str, Any]] | None:
    value = export.get(key)
    if type(value) is not list:
        return None
    result = []
    for row in value:
        if (type(row) is not dict or set(row) != allowed
                or any(field in row for field in ("source_id", "offer_id"))):
            return None
        result.append({**row, "source_id": source_id, "offer_id": offer_id})
    return result


def build_from_local_exports(
    evidence_root: str | Path,
    offer_id: str,
    lifecycle_started_at: str,
    as_of: str,
    source_specs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate original file bytes and classify local claims without authority.

    `source_specs` is an explicit, untrusted inventory. Its expected SHA and
    timestamps must match the raw JSON but do not attest provenance. Missing
    source files are never silently replaced by synthetic rows. The returned
    coverage, budget, and execution flags remain fail-closed for every input.
    """
    root = Path(evidence_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("evidence_root must be a local directory")
    if type(source_specs) not in (list, tuple):
        raise ValueError("source_specs must be an explicit list or tuple")
    envelope: dict[str, Any] = {
        "offer_id": offer_id, "lifecycle_started_at": lifecycle_started_at,
        "as_of": as_of, "sources": [], "plan_runs": [], "attempts": [],
        "out_of_band": {"status": "UNKNOWN"},
    }
    issues: set[str] = set()
    verified: dict[str, str] = {}
    observed: dict[str, str] = {}
    seen_ids: set[str] = set()
    source_spec_count = len(source_specs)
    if len(source_specs) > MAX_EXPORTS:
        issues.add("COMMON_SOURCE_LIMIT_EXCEEDED")
        source_specs = ()
    account_ids: list[str] = []
    account_edits: dict[str, int] = {}
    account_claims: dict[str, str] = {}
    for spec in source_specs:
        if type(spec) is not dict or set(spec) != _SPEC_KEYS:
            issues.add("COMMON_SOURCE_SPEC_INVALID")
            continue
        source_id = spec.get("source_id")
        if not isinstance(source_id, str) or not source_id.strip():
            issues.add("COMMON_SOURCE_SPEC_INVALID")
            continue
        if source_id in seen_ids:
            issues.add("COMMON_SOURCE_SPEC_DUPLICATE")
            continue
        seen_ids.add(source_id)
        source_kind = spec.get("source_kind")
        if (spec.get("offer_id") != offer_id
                or type(source_kind) is not str or source_kind not in _KINDS):
            issues.add("COMMON_SOURCE_METADATA_MISMATCH")
            continue
        raw_path = _path(root, spec.get("path"))
        if raw_path is None:
            issues.add("COMMON_SOURCE_PATH_REJECTED")
            continue
        expected = spec.get("expected_sha256")
        if (type(expected) is not str or len(expected) != 64
                or any(c not in "0123456789abcdef" for c in expected)):
            issues.add("COMMON_SOURCE_SPEC_INVALID")
            continue
        try:
            with raw_path.open("rb") as stream:
                raw = stream.read(MAX_EXPORT_BYTES + 1)
            if len(raw) > MAX_EXPORT_BYTES:
                issues.add("COMMON_SOURCE_SIZE_LIMIT_EXCEEDED")
                continue
            actual_sha = hashlib.sha256(raw).hexdigest()
            observed[source_id] = actual_sha
            if actual_sha != expected:
                issues.add("COMMON_SOURCE_BYTES_SHA_MISMATCH")
                continue
            export = json.loads(raw.decode("utf-8"),
                                object_pairs_hook=_pairs_without_duplicates)
        except (OSError, UnicodeError, ValueError, RecursionError):
            issues.add("COMMON_SOURCE_PARSE_FAILED")
            continue
        if (type(export) is not dict or not set(export) <= _EXPORT_KEYS
                or export.get("schema_version") != SCHEMA
                or any(export.get(field) != spec[field] for field in _META)
                or export.get("coverage_claim") not in ("COMPLETE", "PARTIAL", "UNKNOWN")):
            issues.add("COMMON_SOURCE_METADATA_MISMATCH")
            continue
        if spec["source_kind"] == "RELEASE_STORE_EXPORT":
            if ("observed_edit_count" in export
                    or set(export) != _EXPORT_KEYS - {"observed_edit_count"}):
                issues.add("COMMON_SOURCE_FORMAT_INVALID")
                continue
            plans = _rows(export, source_id, offer_id, "plan_runs", _PLAN_KEYS)
            attempts = _rows(export, source_id, offer_id, "attempts", _ATTEMPT_KEYS)
            if plans is None or attempts is None:
                issues.add("COMMON_SOURCE_FORMAT_INVALID")
                continue
            envelope["plan_runs"].extend(plans)
            envelope["attempts"].extend(attempts)
        else:
            if (set(export) != _EXPORT_KEYS - {"plan_runs", "attempts"}
                    or type(export.get("observed_edit_count")) is not int
                    or export["observed_edit_count"] < 0):
                issues.add("COMMON_SOURCE_FORMAT_INVALID")
                continue
            account_ids.append(source_id)
            account_edits[source_id] = export["observed_edit_count"]
            account_claims[source_id] = export["coverage_claim"]
        verified[source_id] = expected
        envelope["sources"].append({field: export[field] for field in _META}
                                   | {"evidence_digest": expected,
                                      "coverage_claim": export["coverage_claim"]})
    if len(account_ids) == 1 and account_claims[account_ids[0]] == "COMPLETE":
        envelope["out_of_band"] = {
            "status": "CLAIMED_COMPLETE", "source_id": account_ids[0],
            "observed_edit_count": account_edits[account_ids[0]],
        }
    result = build_common_coverage_manifest(envelope)
    if issues:
        result["blockers"] = sorted(set(result["blockers"]) | issues)
        result["recorded_scope_completeness"] = "INCOMPLETE"
    result["source_adapter"] = {
        "schema_version": SCHEMA,
        "verified_sha256_by_source": verified,
        "observed_sha256_by_source": observed,
        "source_spec_count": source_spec_count,
        "source_integrity": ("INCOMPLETE" if issues else
                             "BYTE_MATCH_ONLY" if verified else "NO_VERIFIED_SOURCES"),
        "authority": "UNVERIFIED",
    }
    return result


__all__ = ["SCHEMA", "build_from_local_exports"]
