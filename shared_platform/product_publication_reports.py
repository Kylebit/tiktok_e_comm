"""Durable, redacted publication reports produced by the publication Skill.

This module is storage and readback only.  It never launches a Skill, imports a
channel adapter, or performs an external request.  Report paths are derived by
the server from validated immutable identities rather than accepted from a
caller.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from uuid import uuid4

from core.config import ROOT


DEFAULT_PRODUCT_PUBLICATION_REPORT_DB = ROOT / "data" / "orbit_platform.db"
DEFAULT_PRODUCT_PUBLICATION_REPORT_ROOT = ROOT / "reports" / "product-publication"
REPORT_SCHEMA_VERSION = "product-publication-report/v1"
INTERNAL_REPORT_SCHEMA_VERSION = "product-publication-report/v2"
PUBLIC_REPORT_SCHEMA_VERSION = REPORT_SCHEMA_VERSION
SUMMARY_SCHEMA_VERSION = "product-publication-summary/v1"
SNAPSHOT_SCHEMA_VERSION = "approved-publication-snapshot/v4"
API_SCHEMA_VERSION = "product-publication-report-api/v1"

PUBLICATION_STATUSES = frozenset({"PUBLISHED", "PROCESSING", "PARTIAL", "FAILED"})
STATUS_LABELS = {
    "PUBLISHED": "发布成功",
    "PROCESSING": "平台处理中",
    "PARTIAL": "部分成功",
    "FAILED": "发布失败",
}
_PLATFORMS = frozenset({"TIKTOK", "SHOPEE", "OZON"})
_SAFE_RUN_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEX_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_LEGACY_REPORT_FIELDS = frozenset(
    {
        "schema_version",
        "report_id",
        "run_id",
        "offer_id",
        "revision",
        "plan_id",
        "snapshot",
        "status",
        "summary",
    }
)
_REPORT_FIELDS = _LEGACY_REPORT_FIELDS | {"execution_identity", "targets"}
_SUMMARY_FIELDS = frozenset(
    {
        "schema_version",
        "overall_status",
        "platforms",
        "evidence",
        "requires_human_action",
    }
)
_PLATFORM_FIELDS = frozenset(
    {
        "platform",
        "status",
        "target_count",
        "verified_count",
        "processing_count",
        "failed_count",
    }
)
_EVIDENCE_FIELDS = frozenset(
    {
        "snapshot_verified",
        "dispatch_attempted",
        "readback_completed",
        "external_write_count",
    }
)
_EXECUTION_IDENTITY_FIELDS = frozenset({"skill_digest", "git_commit", "code_digest"})
_OZON_EXECUTION_IDENTITY_FIELDS = frozenset({"ozon_account_id", "ozon_credentials_sha256"})
_TARGET_FIELDS = frozenset({"target_label", "status", "evidence"})
_TARGET_EVIDENCE_FIELDS = frozenset({"target_label", "status", "stage", "provider_code", "provider_reason", "request_attempted", "outcome_unknown", "external_write_count"})
_TARGET_EVIDENCE_OPTIONAL_FIELDS = frozenset({"provider_field_path", "provider_identity_bound"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS product_publication_reports (
    report_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    offer_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    plan_id TEXT NOT NULL,
    snapshot_schema_version TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    report_path TEXT NOT NULL UNIQUE,
    summary_digest TEXT NOT NULL,
    redacted_summary_json TEXT NOT NULL,
    envelope_digest TEXT NOT NULL,
    report_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (offer_id, revision, run_id)
);
CREATE INDEX IF NOT EXISTS idx_product_publication_reports_offer_revision
    ON product_publication_reports(offer_id, revision, created_at DESC, report_id DESC);
"""


class ProductPublicationReportError(RuntimeError):
    """Base error for the durable publication report boundary."""


class ProductPublicationReportIntegrityError(ProductPublicationReportError):
    """Stored metadata or the server-owned report file failed verification."""


@dataclass(frozen=True)
class StoredPublicationReport:
    report_id: str
    report_path: str
    summary_digest: str
    created: bool


def publication_report_id(run_id: object) -> str:
    """Derive the one server-owned report identity for a validated run."""

    return f"publication-report:{_run_id(run_id)}"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _exact_text(value: object, name: str, *, max_length: int = 512) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip() or len(value) > max_length:
        raise ValueError(f"{name} is invalid")
    return value


def _exact_nonnegative_int(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _offer_id(value: object) -> str:
    offer_id = _exact_text(value, "offer_id", max_length=32)
    if not offer_id.isascii() or not offer_id.isdigit():
        raise ValueError("offer_id is invalid")
    return offer_id


def _revision(value: object) -> int:
    revision = _exact_nonnegative_int(value, "revision")
    if revision == 0:
        raise ValueError("revision must be positive")
    return revision


def _run_id(value: object) -> str:
    run_id = _exact_text(value, "run_id", max_length=128)
    if not _SAFE_RUN_PART.fullmatch(run_id) or run_id in {".", ".."}:
        raise ValueError("run_id is not a safe report path component")
    return run_id


def _sha256(value: object, name: str) -> str:
    digest = _exact_text(value, name, max_length=71)
    if not _HEX_DIGEST.fullmatch(digest):
        raise ValueError(f"{name} must be a lowercase sha256 digest")
    return "sha256:" + digest.removeprefix("sha256:")


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], name: str) -> None:
    actual = set(value)
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        extra = sorted(actual - set(expected))
        raise ValueError(f"{name} fields are invalid; missing={missing}; extra={extra}")


def _execution_identity(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("execution_identity must be a mapping")
    fields = set(value)
    if fields != set(_EXECUTION_IDENTITY_FIELDS) and fields != set(
        _EXECUTION_IDENTITY_FIELDS | _OZON_EXECUTION_IDENTITY_FIELDS
    ):
        raise ValueError("execution_identity fields are invalid")
    result = {name: _exact_text(value[name], name, max_length=64) for name in _EXECUTION_IDENTITY_FIELDS}
    if not re.fullmatch(r"[0-9a-f]{40}", result["git_commit"]):
        raise ValueError("git_commit is invalid")
    for name in ("skill_digest", "code_digest"):
        if not re.fullmatch(r"[0-9a-f]{64}", result[name]):
            raise ValueError(f"{name} is invalid")
    if _OZON_EXECUTION_IDENTITY_FIELDS.issubset(fields):
        account = _exact_text(value["ozon_account_id"], "ozon_account_id", max_length=64)
        digest = _exact_text(value["ozon_credentials_sha256"], "ozon_credentials_sha256", max_length=64)
        if not account.isdecimal() or int(account) <= 0:
            raise ValueError("ozon_account_id is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("ozon_credentials_sha256 is invalid")
        result.update(ozon_account_id=account, ozon_credentials_sha256=digest)
    return result


def _safe_targets(value: object) -> list[dict[str, Any]]:
    if type(value) is not list:
        raise TypeError("report targets must be a list")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise TypeError(f"report targets[{index}] must be a mapping")
        _exact_fields(raw, _TARGET_FIELDS, f"report targets[{index}]")
        label = _exact_text(raw["target_label"], "target_label")
        status = _exact_text(raw["status"], "target status", max_length=16)
        if status not in {"PUBLISHED", "PROCESSING", "FAILED"} or label in seen:
            raise ValueError("report target is invalid or duplicated")
        seen.add(label)
        evidence = raw["evidence"]
        if evidence is not None:
            if not isinstance(evidence, Mapping):
                raise TypeError("target evidence must be a mapping or null")
            evidence_fields = set(evidence)
            if not _TARGET_EVIDENCE_FIELDS.issubset(evidence_fields) or not evidence_fields.issubset(
                _TARGET_EVIDENCE_FIELDS | _TARGET_EVIDENCE_OPTIONAL_FIELDS
            ):
                raise ValueError("target evidence fields are invalid")
            if evidence["target_label"] != label or evidence["status"] != status:
                raise ValueError("target evidence identity conflicts")
            stage = _exact_text(evidence["stage"], "stage", max_length=32)
            code = _exact_text(evidence["provider_code"], "provider_code", max_length=80)
            reason = _exact_text(evidence["provider_reason"], "provider_reason", max_length=240)
            field_path = evidence.get("provider_field_path", "")
            if type(field_path) is not str or len(field_path) > 160 or any(
                not (character.isalnum() or character in "_-.[]")
                for character in field_path
            ):
                raise ValueError("provider_field_path is unsafe")
            if not code.isascii() or any(not (c.isalnum() or c in "_-.:") for c in code):
                raise ValueError("provider_code is unsafe")
            if "http://" in reason.casefold() or "https://" in reason.casefold():
                raise ValueError("provider_reason contains a URL")
            if re.search(
                r"(?i)\b(authorization|bearer|access[_-]?token|refresh[_-]?token|token|secret|cookie|set-cookie|app[_-]?key)\b\s*[:=]?\s*\S+",
                reason,
            ):
                raise ValueError("provider_reason contains secret-shaped content")
            attempted = evidence["request_attempted"]
            unknown = evidence["outcome_unknown"]
            if type(attempted) is not bool or type(unknown) is not bool:
                raise TypeError("target evidence flags must be boolean")
            count = evidence["external_write_count"]
            if count is not None:
                count = _exact_nonnegative_int(count, "target evidence write count")
            evidence = {"target_label": label, "status": status, "stage": stage, "provider_code": code, "provider_reason": reason, "request_attempted": attempted, "outcome_unknown": unknown, "external_write_count": count}
            if "provider_identity_bound" in raw["evidence"]:
                bound = raw["evidence"]["provider_identity_bound"]
                if type(bound) is not bool:
                    raise TypeError("provider_identity_bound must be boolean")
                evidence["provider_identity_bound"] = bound
            if field_path:
                evidence["provider_field_path"] = field_path
        rows.append({"target_label": label, "status": status, "evidence": evidence})
    return rows


def _validated_summary(value: object, *, report_status: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("summary must be a mapping")
    _exact_fields(value, _SUMMARY_FIELDS, "summary")
    if value["schema_version"] != SUMMARY_SCHEMA_VERSION:
        raise ValueError("unsupported publication summary schema")
    if value["overall_status"] != report_status:
        raise ValueError("summary status does not match report status")
    if type(value["requires_human_action"]) is not bool:
        raise TypeError("requires_human_action must be a boolean")

    raw_platforms = value["platforms"]
    if type(raw_platforms) is not list:
        raise TypeError("summary platforms must be a list")
    platforms: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_platforms):
        if not isinstance(raw, Mapping):
            raise TypeError(f"summary platforms[{index}] must be a mapping")
        _exact_fields(raw, _PLATFORM_FIELDS, f"summary platforms[{index}]")
        platform = _exact_text(raw["platform"], "platform", max_length=16)
        status = _exact_text(raw["status"], "platform status", max_length=16)
        if platform not in _PLATFORMS or platform in seen:
            raise ValueError("summary platform is unsupported or duplicated")
        if status not in PUBLICATION_STATUSES:
            raise ValueError("summary platform status is unsupported")
        seen.add(platform)
        counts = {
            name: _exact_nonnegative_int(raw[name], name)
            for name in (
                "target_count",
                "verified_count",
                "processing_count",
                "failed_count",
            )
        }
        if sum(counts[name] for name in ("verified_count", "processing_count", "failed_count")) > counts["target_count"]:
            raise ValueError("summary platform counts exceed target_count")
        platforms.append({"platform": platform, "status": status, **counts})

    evidence = value["evidence"]
    if not isinstance(evidence, Mapping):
        raise TypeError("summary evidence must be a mapping")
    _exact_fields(evidence, _EVIDENCE_FIELDS, "summary evidence")
    safe_evidence: dict[str, Any] = {}
    for name in ("snapshot_verified", "dispatch_attempted", "readback_completed"):
        if type(evidence[name]) is not bool:
            raise TypeError(f"summary evidence {name} must be a boolean")
        safe_evidence[name] = evidence[name]
    external_write_count = evidence["external_write_count"]
    if external_write_count is not None:
        external_write_count = _exact_nonnegative_int(
            external_write_count, "external_write_count"
        )
    safe_evidence["external_write_count"] = external_write_count
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "overall_status": report_status,
        "platforms": platforms,
        "evidence": safe_evidence,
        "requires_human_action": value["requires_human_action"],
    }


def _safe_mutation_budgets(value: object) -> list[dict[str, Any]]:
    if type(value) is not list:
        raise TypeError("mutation_budgets must be a list")
    safe: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != {"schema_version", "platform", "limits", "attempts", "reservations"}:
            raise ValueError("mutation budget shape is invalid")
        platform = _exact_text(raw["platform"], "mutation platform", max_length=16)
        if platform not in _PLATFORMS or platform in seen or raw["schema_version"] != "publication-mutation-budget/v1":
            raise ValueError("mutation budget identity is invalid")
        limits = raw["limits"]
        attempts = raw["attempts"]
        reservations = raw["reservations"]
        if not isinstance(limits, Mapping) or set(limits) != {"shared_maximum", "per_target_maximum"}:
            raise ValueError("mutation budget limits are invalid")
        if not isinstance(attempts, Mapping) or set(attempts) != {"shared", "per_target", "total"}:
            raise ValueError("mutation budget attempts are invalid")
        shared_max = _exact_nonnegative_int(limits["shared_maximum"], "shared maximum")
        per_target_max = _exact_nonnegative_int(limits["per_target_maximum"], "per-target maximum")
        shared = _exact_nonnegative_int(attempts["shared"], "shared attempts")
        total = _exact_nonnegative_int(attempts["total"], "total attempts")
        per_target = attempts["per_target"]
        if not isinstance(per_target, Mapping) or any(type(label) is not str for label in per_target):
            raise ValueError("per-target attempts are invalid")
        safe_per_target = {_exact_text(label, "budget target", max_length=80): _exact_nonnegative_int(count, "target attempts") for label, count in per_target.items()}
        if shared > shared_max or any(count > per_target_max for count in safe_per_target.values()) or total != shared + sum(safe_per_target.values()):
            raise ValueError("mutation attempts exceed or conflict with limits")
        if type(reservations) is not list:
            raise ValueError("mutation reservations are invalid")
        safe_reservations = []
        reserved_total = 0
        reserved_shared = 0
        reserved_per_target = {label: 0 for label in safe_per_target}
        for index, row in enumerate(reservations, 1):
            if not isinstance(row, Mapping) or set(row) != {"sequence", "scope", "target_label", "operation", "attempt_count"}:
                raise ValueError("mutation reservation shape is invalid")
            count = _exact_nonnegative_int(row["attempt_count"], "attempt_count")
            operation = _exact_text(row["operation"], "operation", max_length=80)
            if not re.fullmatch(r"[a-z0-9_]+", operation):
                raise ValueError("mutation operation is unsafe")
            scope = row["scope"]
            target = row["target_label"]
            if type(row["sequence"]) is not int or row["sequence"] != index or count == 0 or scope not in {"shared", "target"}:
                raise ValueError("mutation reservation identity is invalid")
            if scope == "shared" and target is not None:
                raise ValueError("shared reservation cannot have a target")
            if scope == "target" and target not in safe_per_target:
                raise ValueError("target reservation is outside the budget")
            safe_reservations.append({"sequence": index, "scope": scope, "target_label": target, "operation": operation, "attempt_count": count})
            reserved_total += count
            if scope == "shared":
                reserved_shared += count
            else:
                reserved_per_target[target] += count
        if reserved_total != total or reserved_shared != shared or reserved_per_target != safe_per_target:
            raise ValueError("mutation reservations do not match attempts")
        seen.add(platform)
        safe.append({"schema_version": "publication-mutation-budget/v1", "platform": platform, "limits": {"shared_maximum": shared_max, "per_target_maximum": per_target_max}, "attempts": {"shared": shared, "per_target": safe_per_target, "total": total}, "reservations": safe_reservations})
    return safe


def _safe_recovery_retry_authorization(value: object) -> dict[str, Any]:
    """Validate the private lineage from a zero-write recovery failure."""
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "retry_of_run_id", "receipt_digest",
        "manifest_digest", "failed_run_identity", "successor_request_identity",
    }:
        raise ValueError("recovery retry authorization shape is invalid")
    if value["schema_version"] != "shopee-recovery-retry-authorization/v1":
        raise ValueError("recovery retry authorization schema is invalid")
    retry_of = _run_id(value["retry_of_run_id"])
    receipt_digest = _sha256(value["receipt_digest"], "recovery retry receipt digest")
    manifest_digest = _sha256(value["manifest_digest"], "recovery retry manifest digest")
    failed = value["failed_run_identity"]
    failed_fields = {
        "run_id", "report_id", "offer_id", "revision", "plan_id",
        "snapshot_digest", "platform_scope", "target_count",
        "execution_identity", "request_identity",
    }
    if not isinstance(failed, Mapping) or set(failed) != failed_fields:
        raise ValueError("failed recovery run identity shape is invalid")
    request = failed["request_identity"]
    if (
        failed["run_id"] != retry_of
        or failed["report_id"] != publication_report_id(retry_of)
        or failed["platform_scope"] != ["SHOPEE"]
        or not isinstance(request, Mapping)
        or request != {"kind": "SHOPEE_RECOVERY", "authority_digest": manifest_digest}
    ):
        raise ValueError("failed recovery run identity conflicts")
    safe_failed = {
        "run_id": retry_of,
        "report_id": publication_report_id(retry_of),
        "offer_id": _offer_id(failed["offer_id"]),
        "revision": _revision(failed["revision"]),
        "plan_id": _exact_text(failed["plan_id"], "failed recovery plan_id"),
        "snapshot_digest": _sha256(
            failed["snapshot_digest"], "failed recovery snapshot digest"
        ),
        "platform_scope": ["SHOPEE"],
        "target_count": _exact_nonnegative_int(
            failed["target_count"], "failed recovery target_count"
        ),
        "execution_identity": _execution_identity(failed["execution_identity"]),
        "request_identity": dict(request),
    }
    successor = value["successor_request_identity"]
    expected_successor = {
        "kind": "SHOPEE_RECOVERY_RETRY",
        "authority_digest": manifest_digest,
        "reconciliation_receipt_digest": receipt_digest,
        "retry_of_run_id": retry_of,
    }
    if not isinstance(successor, Mapping) or dict(successor) != expected_successor:
        raise ValueError("successor recovery request identity conflicts")
    return {
        "schema_version": "shopee-recovery-retry-authorization/v1",
        "retry_of_run_id": retry_of,
        "receipt_digest": receipt_digest,
        "manifest_digest": manifest_digest,
        "failed_run_identity": safe_failed,
        "successor_request_identity": expected_successor,
    }


def _safe_continuation_evidence(value: object) -> dict[str, Any]:
    base_fields = {
        "schema_version", "manifest_digest", "result_digest",
        "lineage",
        "confirmed_write_count", "unknown_write_count",
        "remaining_target_labels", "reservations", "targets",
        "automatic_retry_performed", "no_scope_expansion",
    }
    optional_fields = {"retry_source", "verification_summary"}
    if (not isinstance(value, Mapping) or not base_fields.issubset(value)
            or set(value) - base_fields - optional_fields):
        raise ValueError("continuation evidence shape is invalid")
    if value["schema_version"] != "tiktok-lineage-recovery-result/v2":
        raise ValueError("continuation evidence schema is invalid")
    result = deepcopy(dict(value))
    result["manifest_digest"] = _sha256(result["manifest_digest"], "continuation manifest digest")
    result["result_digest"] = _sha256(result["result_digest"], "continuation result digest")
    result["confirmed_write_count"] = _exact_nonnegative_int(result["confirmed_write_count"], "continuation confirmed writes")
    result["unknown_write_count"] = _exact_nonnegative_int(result["unknown_write_count"], "continuation unknown writes")
    if not isinstance(result["lineage"], Mapping):
        raise ValueError("continuation lineage is invalid")
    result["lineage"] = deepcopy(dict(result["lineage"]))
    if result["automatic_retry_performed"] is not False or result["no_scope_expansion"] is not True:
        raise ValueError("continuation governance evidence is invalid")
    if not isinstance(result["remaining_target_labels"], list) or any(type(label) is not str for label in result["remaining_target_labels"]):
        raise ValueError("continuation remaining targets are invalid")
    if not isinstance(result["reservations"], list):
        raise ValueError("continuation reservations are invalid")
    rows = result["targets"]
    if not isinstance(rows, list) or len(rows) != 10:
        raise ValueError("continuation target evidence is invalid")
    safe_rows = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "target_label", "status", "stage", "attempts",
            "confirmed_write_count", "unknown_write_count", "reason",
        }:
            raise ValueError("continuation target evidence shape is invalid")
        attempts = row["attempts"]
        if not isinstance(attempts, Mapping) or set(attempts) != {
            "save_draft", "publish_target", "official_readback",
        } or any(type(value) is not int or value not in {0, 1} for value in attempts.values()):
            raise ValueError("continuation target attempts are invalid")
        if row["status"] not in {"PUBLISHED", "PROCESSING", "FAILED", "UNKNOWN"}:
            raise ValueError("continuation target status is invalid")
        safe_rows.append({
            "target_label": _exact_text(row["target_label"], "continuation target label"),
            "status": row["status"],
            "stage": _exact_text(row["stage"], "continuation target stage"),
            "attempts": dict(attempts),
            "confirmed_write_count": _exact_nonnegative_int(row["confirmed_write_count"], "continuation target confirmed writes"),
            "unknown_write_count": _exact_nonnegative_int(row["unknown_write_count"], "continuation target unknown writes"),
            "reason": _exact_text(row["reason"], "continuation target reason"),
        })
    result["targets"] = safe_rows
    if "verification_summary" in result:
        verification = result["verification_summary"]
        fields = {"schema_version", "status", "target_count", "unique_asset_count", "failures", "verification_digest"}
        if not isinstance(verification, Mapping) or set(verification) != fields:
            raise ValueError("continuation verification summary shape is invalid")
        safe_verification = deepcopy(dict(verification))
        supplied_verification_digest = _sha256(
            safe_verification.pop("verification_digest"),
            "continuation verification digest",
        )
        failures = safe_verification.get("failures")
        safe_codes = {"ASSET_DIGEST_CONFLICT", "ASSET_DIGEST_MISMATCH", "ASSET_READ_UNAVAILABLE", "TARGET_VERIFY_FAILED"}
        if (safe_verification.get("schema_version") != "tiktok-scope-verification/v1"
                or safe_verification.get("status") not in {"VERIFIED", "FAILED"}
                or type(safe_verification.get("target_count")) is not int
                or safe_verification["target_count"] < 0
                or type(safe_verification.get("unique_asset_count")) is not int
                or safe_verification["unique_asset_count"] < 0
                or not isinstance(failures, list)
                or (safe_verification["status"] == "VERIFIED") != (failures == [])):
            raise ValueError("continuation verification summary is invalid")
        for failure in failures:
            if (not isinstance(failure, Mapping)
                    or set(failure) != {"target_label", "stage", "code"}
                    or type(failure.get("target_label")) is not str
                    or failure.get("stage") != "VERIFY"
                    or failure.get("code") not in safe_codes):
                raise ValueError("continuation verification failure is invalid")
        if "sha256:" + _digest(safe_verification) != supplied_verification_digest:
            raise ValueError("continuation verification digest conflicts")
        result["verification_summary"] = {
            **safe_verification, "verification_digest": supplied_verification_digest,
        }
    digest_body = {key: result[key] for key in (
        "schema_version", "manifest_digest", "lineage", "targets", "reservations",
        "confirmed_write_count", "unknown_write_count", "remaining_target_labels",
        "automatic_retry_performed", "no_scope_expansion",
    )}
    if "verification_summary" in result:
        digest_body["verification_summary"] = result["verification_summary"]
    if "sha256:" + _digest(digest_body) != result["result_digest"]:
        raise ValueError("continuation result digest conflicts")
    if "retry_source" in result:
        retry = result["retry_source"]
        fields = {
            "kind", "authority_digest", "manifest_digest", "retry_of_run_id",
            "source_report_digest", "source_result_digest", "source_manifest_digest",
            "binding_digest",
        }
        if not isinstance(retry, Mapping) or set(retry) != fields or retry.get("kind") != "TIKTOK_FIRST_COMPLETION_ZERO_WRITE_RETRY":
            raise ValueError("continuation retry source shape is invalid")
        safe_retry = {"kind": retry["kind"], "retry_of_run_id": _run_id(retry["retry_of_run_id"])}
        for field in fields - {"kind", "retry_of_run_id", "binding_digest"}:
            safe_retry[field] = _sha256(retry[field], "continuation retry " + field)
        expected_binding = _sha256(retry["binding_digest"], "continuation retry binding digest")
        if ("sha256:" + _digest(safe_retry) != expected_binding
                or safe_retry["manifest_digest"] != result["manifest_digest"]):
            raise ValueError("continuation retry source digest conflicts")
        result["retry_source"] = {**safe_retry, "binding_digest": expected_binding}
    return result


def validate_publication_report(value: object) -> dict[str, Any]:
    """Validate the version+digest envelope without interpreting product facts."""
    if not isinstance(value, Mapping):
        raise TypeError("publication report must be a mapping")
    schema_version = value.get("schema_version")
    fields = _REPORT_FIELDS if schema_version == INTERNAL_REPORT_SCHEMA_VERSION else _LEGACY_REPORT_FIELDS
    if schema_version == INTERNAL_REPORT_SCHEMA_VERSION:
        fields = fields | (set(value) & {"mutation_budgets", "release_authorization", "recovery_authorization", "recovery_retry_authorization", "continuation_evidence", "target_observations"})
    _exact_fields(value, fields, "publication report")
    if schema_version not in {REPORT_SCHEMA_VERSION, INTERNAL_REPORT_SCHEMA_VERSION}:
        raise ValueError("unsupported publication report schema")
    report_id = _exact_text(value["report_id"], "report_id")
    run_id = _run_id(value["run_id"])
    offer_id = _offer_id(value["offer_id"])
    revision = _revision(value["revision"])
    plan_id = _exact_text(value["plan_id"], "plan_id")
    status = _exact_text(value["status"], "status", max_length=16)
    if status not in PUBLICATION_STATUSES:
        raise ValueError("unsupported publication report status")
    snapshot = value["snapshot"]
    if not isinstance(snapshot, Mapping):
        raise TypeError("snapshot envelope must be a mapping")
    _exact_fields(
        snapshot, frozenset({"schema_version", "digest"}), "snapshot envelope"
    )
    if snapshot["schema_version"] != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported approved publication snapshot schema")
    snapshot_digest = _sha256(snapshot["digest"], "snapshot digest")
    summary = _validated_summary(value["summary"], report_status=status)
    execution_identity = _execution_identity(value["execution_identity"]) if schema_version == INTERNAL_REPORT_SCHEMA_VERSION else None
    targets = _safe_targets(value["targets"]) if schema_version == INTERNAL_REPORT_SCHEMA_VERSION else None
    extensions = {}
    if "mutation_budgets" in value:
        budgets = _safe_mutation_budgets(value["mutation_budgets"])
        labels_by_platform = {row["platform"]: set() for row in summary["platforms"]}
        for target in targets:
            platform = target["target_label"].split(":", 1)[0].upper()
            if platform in labels_by_platform:
                labels_by_platform[platform].add(target["target_label"])
        if {row["platform"] for row in budgets} != set(labels_by_platform):
            raise ValueError("mutation budget platform scope conflicts with report")
        for row in budgets:
            if set(row["attempts"]["per_target"]) != labels_by_platform[row["platform"]]:
                raise ValueError("mutation budget targets conflict with report")
        extensions["mutation_budgets"] = budgets
    if "release_authorization" in value:
        authority = value["release_authorization"]
        if not isinstance(authority, Mapping):
            raise ValueError("release authorization must be a mapping")
        _exact_fields(authority, {"candidate_digest", "approval_digest"}, "release authorization")
        extensions["release_authorization"] = {key:_sha256(authority[key], key).removeprefix("sha256:") for key in authority}
    if "continuation_evidence" in value:
        extensions["continuation_evidence"] = _safe_continuation_evidence(value["continuation_evidence"])
    if "target_observations" in value:
        from shared_platform.publication_target_observations import validate_references
        extensions["target_observations"] = validate_references(value["target_observations"], value)
    if "recovery_authorization" in value:
        recovery = value["recovery_authorization"]
        if not isinstance(recovery, Mapping):
            raise ValueError("recovery authorization must be a mapping")
        from shared_platform.shopee_regional_recovery import _digest as recovery_digest
        safe_recovery = deepcopy(dict(recovery))
        supplied = safe_recovery.pop("manifest_digest", None)
        if supplied != recovery_digest(safe_recovery):
            raise ValueError("recovery authorization digest conflicts")
        safe_recovery["manifest_digest"] = supplied
        extensions["recovery_authorization"] = safe_recovery
    if "recovery_retry_authorization" in value:
        retry_authority = _safe_recovery_retry_authorization(
            value["recovery_retry_authorization"]
        )
        if (
            "recovery_authorization" not in extensions
            or retry_authority["manifest_digest"]
            != extensions["recovery_authorization"]["manifest_digest"]
            or retry_authority["failed_run_identity"]["offer_id"] != offer_id
            or retry_authority["failed_run_identity"]["revision"] != revision
            or retry_authority["failed_run_identity"]["plan_id"] != plan_id
            or retry_authority["failed_run_identity"]["snapshot_digest"]
            != snapshot_digest
        ):
            raise ValueError("recovery retry authorization conflicts with report")
        extensions["recovery_retry_authorization"] = retry_authority
    return {
        **extensions,
        "schema_version": schema_version,
        "report_id": report_id,
        "run_id": run_id,
        "offer_id": offer_id,
        "revision": revision,
        "plan_id": plan_id,
        "snapshot": {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "digest": snapshot_digest,
        },
        **({"execution_identity": execution_identity, "targets": targets} if schema_version == INTERNAL_REPORT_SCHEMA_VERSION else {}),
        "status": status,
        "summary": summary,
    }


class ProductPublicationReportStore:
    """SQLite index plus server-owned JSON report files."""

    def __init__(
        self,
        path: str | Path = DEFAULT_PRODUCT_PUBLICATION_REPORT_DB,
        *,
        reports_root: str | Path = DEFAULT_PRODUCT_PUBLICATION_REPORT_ROOT,
        target_observation_reader: object | None = None,
    ) -> None:
        self.path = Path(path)
        self.reports_root = Path(reports_root)
        self.target_observation_reader = target_observation_reader

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _connect_readonly(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA)

    def _relative_report_path(self, report: Mapping[str, Any]) -> str:
        return PurePosixPath(
            report["offer_id"], str(report["revision"]), report["run_id"], "report.json"
        ).as_posix()

    def _resolved_report_file(self, report_path: str) -> Path:
        from shared_platform.immutable_approval_files import require_local_path
        pure = PurePosixPath(report_path)
        if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
            raise ValueError("report_path is invalid")
        require_local_path(self.reports_root.joinpath(*pure.parts), root=self.reports_root)
        root = self.reports_root.resolve()
        candidate = root.joinpath(*pure.parts).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise ValueError("report_path escapes the publication report root")
        return candidate

    def store_report(self, value: object) -> StoredPublicationReport:
        report = validate_publication_report(value)
        report_path = self._relative_report_path(report)
        report_file = self._resolved_report_file(report_path)
        summary_json = _canonical_json(report["summary"])
        summary_digest = _digest(report["summary"])
        envelope_digest = _digest(report)
        now = _utc_now()

        self.path.parent.mkdir(parents=True, exist_ok=True)
        report_file.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._ensure_schema(conn)
            existing = conn.execute(
                """
                SELECT report_id, run_id, offer_id, revision, report_path,
                       summary_digest, envelope_digest
                FROM product_publication_reports
                WHERE report_id = ? OR run_id = ? OR report_path = ?
                """,
                (report["report_id"], report["run_id"], report_path),
            ).fetchall()
            if existing:
                if len(existing) != 1 or any(
                    row["report_id"] != report["report_id"]
                    or row["run_id"] != report["run_id"]
                    or row["offer_id"] != report["offer_id"]
                    or row["revision"] != report["revision"]
                    or row["report_path"] != report_path
                    or row["summary_digest"] != summary_digest
                    or row["envelope_digest"] != envelope_digest
                    for row in existing
                ):
                    raise ValueError("publication report identity already stores different facts")
                # Reuse the ordinary verified read path so a replay cannot hide
                # a damaged or externally modified report file.
                self._row_to_report(
                    conn.execute(
                        "SELECT * FROM product_publication_reports WHERE report_id = ?",
                        (report["report_id"],),
                    ).fetchone()
                )
                return StoredPublicationReport(
                    report["report_id"], report_path, summary_digest, False
                )

            file_payload = {
                **report,
                "report_path": report_path,
                "summary_digest": summary_digest,
                "created_at": now,
                "updated_at": now,
            }
            report_digest = _digest(file_payload)
            encoded = (_canonical_json(file_payload) + "\n").encode("utf-8")
            temp_file = report_file.with_name(f".{report_file.name}.{uuid4().hex}.tmp")
            try:
                temp_file.write_bytes(encoded)
                temp_file.replace(report_file)
                conn.execute(
                    """
                    INSERT INTO product_publication_reports (
                        report_id, run_id, offer_id, revision, plan_id,
                        snapshot_schema_version, snapshot_digest, status,
                        report_path, summary_digest, redacted_summary_json,
                        envelope_digest, report_digest, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        report["report_id"],
                        report["run_id"],
                        report["offer_id"],
                        report["revision"],
                        report["plan_id"],
                        report["snapshot"]["schema_version"],
                        report["snapshot"]["digest"],
                        report["status"],
                        report_path,
                        summary_digest,
                        summary_json,
                        envelope_digest,
                        report_digest,
                        now,
                        now,
                    ),
                )
                conn.commit()
            finally:
                if temp_file.exists():
                    temp_file.unlink()
        return StoredPublicationReport(report["report_id"], report_path, summary_digest, True)

    def _row_to_report(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        try:
            summary = json.loads(row["redacted_summary_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise ProductPublicationReportIntegrityError(
                "stored publication summary is invalid"
            ) from error
        if _digest(summary) != row["summary_digest"]:
            raise ProductPublicationReportIntegrityError(
                "stored publication summary digest does not match"
            )
        report_file = self._resolved_report_file(row["report_path"])
        if not report_file.is_file():
            raise ProductPublicationReportIntegrityError(
                "server-owned publication report file is missing"
            )
        try:
            file_payload = json.loads(report_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProductPublicationReportIntegrityError(
                "server-owned publication report file is invalid"
            ) from error
        if _digest(file_payload) != row["report_digest"]:
            raise ProductPublicationReportIntegrityError(
                "server-owned publication report digest does not match"
            )
        expected = {
            "report_id": row["report_id"],
            "run_id": row["run_id"],
            "offer_id": row["offer_id"],
            "revision": row["revision"],
            "plan_id": row["plan_id"],
            "snapshot": {
                "schema_version": row["snapshot_schema_version"],
                "digest": row["snapshot_digest"],
            },
            **({"execution_identity": file_payload.get("execution_identity"), "targets": file_payload.get("targets"), **{name:file_payload[name] for name in ("mutation_budgets", "release_authorization", "recovery_authorization", "recovery_retry_authorization", "continuation_evidence", "target_observations") if name in file_payload}} if file_payload.get("schema_version") == INTERNAL_REPORT_SCHEMA_VERSION else {}),
            "status": row["status"],
            "summary": summary,
            "report_path": row["report_path"],
            "summary_digest": row["summary_digest"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for name, expected_value in expected.items():
            if file_payload.get(name) != expected_value:
                raise ProductPublicationReportIntegrityError(
                    f"server-owned publication report {name} does not match the index"
                )
        if file_payload.get("schema_version") not in {REPORT_SCHEMA_VERSION, INTERNAL_REPORT_SCHEMA_VERSION}:
            raise ProductPublicationReportIntegrityError(
                "server-owned publication report schema does not match"
            )
        envelope_fields = _REPORT_FIELDS if file_payload["schema_version"] == INTERNAL_REPORT_SCHEMA_VERSION else _LEGACY_REPORT_FIELDS
        if file_payload["schema_version"] == INTERNAL_REPORT_SCHEMA_VERSION:
            envelope_fields = envelope_fields | (set(file_payload) & {"mutation_budgets", "release_authorization", "recovery_authorization", "recovery_retry_authorization", "continuation_evidence", "target_observations"})
        if _digest({name: file_payload[name] for name in envelope_fields}) != row["envelope_digest"]:
            raise ProductPublicationReportIntegrityError(
                "server-owned publication report envelope does not match"
            )
        return {"schema_version": file_payload["schema_version"], **expected}

    def get_report(self, *, report_id: str, offer_id: str) -> dict[str, Any] | None:
        safe_report_id = _exact_text(report_id, "report_id")
        safe_offer_id = _offer_id(offer_id)
        if not self.path.is_file():
            return None
        with self._connect_readonly() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM product_publication_reports WHERE report_id = ? AND offer_id = ?",
                    (safe_report_id, safe_offer_id),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        return self._row_to_report(row)

    def get_report_by_run(self, *, run_id: str) -> dict[str, Any] | None:
        """Read one globally unique run for a pre-dispatch replay check.

        Unlike the offer-scoped public read API, this server-only lookup is
        intentionally keyed by the database UNIQUE run identity.  It lets the
        runner reject a cross-offer or changed-scope replay before invoking a
        platform adapter.
        """

        safe_run_id = _run_id(run_id)
        if not self.path.is_file():
            return None
        with self._connect_readonly() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM product_publication_reports WHERE run_id = ?",
                    (safe_run_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        return self._row_to_report(row)

    def get_report_by_path(
        self, *, offer_id: str, report_path: str
    ) -> dict[str, Any] | None:
        safe_offer_id = _offer_id(offer_id)
        if type(report_path) is not str:
            raise TypeError("report_path must be a string")
        pure = PurePosixPath(report_path)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts or pure.parts[0] != safe_offer_id:
            raise ValueError("report_path is outside the requested offer")
        self._resolved_report_file(report_path)
        if not self.path.is_file():
            return None
        with self._connect_readonly() as conn:
            try:
                row = conn.execute(
                    "SELECT * FROM product_publication_reports WHERE report_path = ? AND offer_id = ?",
                    (pure.as_posix(), safe_offer_id),
                ).fetchone()
            except sqlite3.OperationalError:
                return None
        return self._row_to_report(row)

    def list_reports(
        self, *, offer_id: str, revision: int | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        safe_offer_id = _offer_id(offer_id)
        safe_revision = _revision(revision) if revision is not None else None
        safe_limit = _exact_nonnegative_int(limit, "limit")
        if safe_limit == 0 or safe_limit > 100:
            raise ValueError("limit must be between 1 and 100")
        if not self.path.is_file():
            return []
        if safe_revision is None:
            query = "SELECT * FROM product_publication_reports WHERE offer_id = ? ORDER BY created_at DESC, report_id DESC LIMIT ?"
            parameters: tuple[object, ...] = (safe_offer_id, safe_limit)
        else:
            query = "SELECT * FROM product_publication_reports WHERE offer_id = ? AND revision = ? ORDER BY created_at DESC, report_id DESC LIMIT ?"
            parameters = (safe_offer_id, safe_revision, safe_limit)
        with self._connect_readonly() as conn:
            try:
                rows = conn.execute(query, parameters).fetchall()
            except sqlite3.OperationalError:
                return []
        return [self._row_to_report(row) for row in rows]

    def list_report_refs_for_plan(self, *, offer_id: str, plan_id: str) -> list[dict[str, str]]:
        """Enumerate an exact plan without an offer-wide limit hiding orphan reports.

        References are not result evidence; consumers still use get_report_by_run.
        """
        offer = _offer_id(offer_id)
        plan = _exact_text(plan_id, 'plan_id')
        if not self.path.is_file():
            return []
        with self._connect_readonly() as conn:
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'product_publication_reports'"
            ).fetchone()
            if table_exists is None:
                return []
            rows = conn.execute('SELECT run_id, report_id FROM product_publication_reports WHERE offer_id = ? AND plan_id = ?',
                                (offer, plan)).fetchall()
        return [dict(row) for row in rows]

    def latest_report(
        self, *, offer_id: str, revision: int | None = None
    ) -> dict[str, Any] | None:
        reports = self.list_reports(offer_id=offer_id, revision=revision, limit=1)
        return reports[0] if reports else None


def public_publication_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return the Product Center projection, intentionally excluding file paths."""
    return {
        "schema_version": PUBLIC_REPORT_SCHEMA_VERSION,
        "report_id": report["report_id"],
        "run_id": report["run_id"],
        "offer_id": report["offer_id"],
        "revision": report["revision"],
        "plan_id": report["plan_id"],
        "snapshot": dict(report["snapshot"]),
        "status": report["status"],
        "status_label": STATUS_LABELS[report["status"]],
        "summary": dict(report["summary"]),
        "summary_digest": report["summary_digest"],
        "created_at": report["created_at"],
        "updated_at": report["updated_at"],
    }


def default_product_publication_report_store() -> ProductPublicationReportStore:
    return ProductPublicationReportStore()
