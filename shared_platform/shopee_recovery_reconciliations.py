"""Append-only, GET-only closure for an ambiguous Shopee recovery attempt.

This module deliberately owns no provider client.  It accepts already-normalized
official observations, binds them to the immutable recovery manifest and the
finished publication report, and emits one of three decisions.  Consumers must
not use an UNKNOWN receipt to unlock another mutation attempt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = "shopee-recovery-reconciliation/v1"
READBACK_SCHEMA_VERSION = "shopee-recovery-official-direct-id-readback/v1"
_MANIFEST_SCHEMA = "shopee-regional-recovery/v1"
_REPORT_SCHEMAS = frozenset({"product-publication-report/v2", "product-publication-report/v3"})
_TARGET = re.compile(r"^shopee:(PH|MY|TH|VN)$")
_DIGEST = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_TERMINAL = frozenset({"CONVERGED", "REMAINING_DIFF"})


class ShopeeRecoveryReconciliationError(ValueError):
    pass


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _digest_value(value: object, label: str) -> str:
    text = str(value or "")
    if not _DIGEST.fullmatch(text):
        raise ShopeeRecoveryReconciliationError(f"{label} is invalid")
    return "sha256:" + text.removeprefix("sha256:")


def _positive_id(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text.isdecimal() or int(text) <= 0:
        raise ShopeeRecoveryReconciliationError(f"{label} is invalid")
    return text


def _timestamp(value: object, label: str) -> str:
    if type(value) is not str:
        raise ShopeeRecoveryReconciliationError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ShopeeRecoveryReconciliationError(f"{label} is invalid") from error
    if parsed.tzinfo is None:
        raise ShopeeRecoveryReconciliationError(f"{label} must be timezone-aware")
    return value


def _parsed_timestamp(value: object, label: str) -> datetime:
    safe = _timestamp(value, label)
    return datetime.fromisoformat(safe.replace("Z", "+00:00"))


def _verified_digest(value: Mapping[str, Any], field: str, label: str) -> str:
    body = deepcopy(dict(value))
    supplied = _digest_value(body.pop(field, None), f"{label} {field}")
    if supplied != _digest(body):
        raise ShopeeRecoveryReconciliationError(f"{label} digest conflicts")
    return supplied


def _manifest_targets(manifest: Mapping[str, Any]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if manifest.get("schema_version") != _MANIFEST_SCHEMA:
        raise ShopeeRecoveryReconciliationError("recovery manifest schema is invalid")
    _verified_digest(manifest, "manifest_digest", "recovery manifest")
    labels = manifest.get("target_labels")
    targets = manifest.get("targets")
    if (not isinstance(labels, list) or not labels or len(labels) != len(set(labels))
            or any(type(label) is not str or not _TARGET.fullmatch(label) for label in labels)
            or not isinstance(targets, list)
            or [row.get("target_label") for row in targets if isinstance(row, Mapping)] != labels):
        raise ShopeeRecoveryReconciliationError("recovery manifest target scope is invalid")
    indexed: dict[str, dict[str, Any]] = {}
    for row in targets:
        if not isinstance(row, Mapping):
            raise ShopeeRecoveryReconciliationError("recovery manifest target is invalid")
        item = deepcopy(dict(row))
        for key in ("shop_id", "item_id", "global_item_id", "model_id", "global_model_id"):
            item[key] = _positive_id(item.get(key), f"manifest {key}")
        model_sku = str(item.get("model_sku") or "").strip()
        if not model_sku:
            raise ShopeeRecoveryReconciliationError("manifest model_sku is invalid")
        item["model_sku"] = model_sku
        indexed[item["target_label"]] = item
    return list(labels), indexed


def _attempt_identity(attempt: Mapping[str, Any], manifest: Mapping[str, Any], labels: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if attempt.get("schema_version") not in _REPORT_SCHEMAS:
        raise ShopeeRecoveryReconciliationError("recovery attempt report schema is invalid")
    run_id = str(attempt.get("run_id") or "").strip()
    report_id = str(attempt.get("report_id") or "").strip()
    snapshot = attempt.get("snapshot")
    if (not run_id or report_id != f"publication-report:{run_id}"
            or not isinstance(snapshot, Mapping)):
        raise ShopeeRecoveryReconciliationError("recovery attempt identity is invalid")
    expected = {
        "offer_id": manifest.get("offer_id"),
        "plan_id": manifest.get("plan_id"),
        "revision": manifest.get("product_revision"),
        "snapshot_digest": manifest.get("execution_snapshot_digest"),
    }
    actual = {
        "offer_id": attempt.get("offer_id"),
        "plan_id": attempt.get("plan_id"),
        "revision": attempt.get("revision"),
        "snapshot_digest": snapshot.get("digest"),
    }
    if actual != expected:
        raise ShopeeRecoveryReconciliationError("recovery attempt conflicts with manifest identity")
    ambiguity_at = _timestamp(attempt.get("updated_at"), "recovery attempt updated_at")
    targets = attempt.get("targets")
    if (attempt.get("status") not in {"PARTIAL", "PROCESSING"}
            or not isinstance(targets, list)
            or [row.get("target_label") for row in targets if isinstance(row, Mapping)] != labels):
        raise ShopeeRecoveryReconciliationError("recovery attempt target scope conflicts")
    normalized_counts: list[dict[str, Any]] = []
    unknown_count = 0
    for row in targets:
        if not isinstance(row, Mapping) or not isinstance(row.get("evidence"), Mapping):
            raise ShopeeRecoveryReconciliationError("recovery attempt target evidence is missing")
        evidence = row["evidence"]
        count = evidence.get("external_write_count")
        unknown = evidence.get("outcome_unknown")
        attempted = evidence.get("request_attempted")
        if type(attempted) is not bool or type(unknown) is not bool:
            raise ShopeeRecoveryReconciliationError("recovery attempt write evidence is invalid")
        if count is not None and (type(count) is not int or count < 0):
            raise ShopeeRecoveryReconciliationError("recovery attempt write count is invalid")
        if unknown and count is not None:
            raise ShopeeRecoveryReconciliationError("unknown recovery outcome must retain an unknown write count")
        status = str(row.get("status") or "").upper()
        if unknown:
            unknown_count += 1
            if status != "PROCESSING" or attempted is not True:
                raise ShopeeRecoveryReconciliationError("unknown target status conflicts with attempt evidence")
        elif status != "PUBLISHED":
            raise ShopeeRecoveryReconciliationError("known target in recovery scope must already be PUBLISHED")
        normalized_counts.append({
            "target_label": row["target_label"],
            "attempt_status": status,
            "request_attempted": attempted,
            "outcome_unknown": unknown,
            "confirmed_external_write_count": count,
        })
    if unknown_count == 0:
        raise ShopeeRecoveryReconciliationError("recovery attempt contains no unknown target")
    return ({
        "run_id": run_id,
        "report_id": report_id,
        **actual,
        "attempt_report_digest": _digest(attempt),
        "ambiguity_at": ambiguity_at,
    }, normalized_counts)


def _decimal(value: object, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ShopeeRecoveryReconciliationError(f"{label} is invalid") from error
    if not result.is_finite() or result <= 0:
        raise ShopeeRecoveryReconciliationError(f"{label} is invalid")
    return result


def _equal_decimal(left: object, right: object) -> bool:
    try:
        return _decimal(left, "official model price") == _decimal(right, "manifest model price")
    except ShopeeRecoveryReconciliationError:
        return False


def _evidence_path(value: object, *, roots: Sequence[str | Path]) -> Path:
    if type(value) is not str or not value.strip():
        raise ShopeeRecoveryReconciliationError("official response_ref is invalid")
    candidate = Path(value).resolve()
    if candidate.suffix.lower() != ".json" or not candidate.is_file():
        raise ShopeeRecoveryReconciliationError("official response evidence is unavailable")
    allowed = False
    for root in roots:
        try:
            candidate.relative_to(Path(root).resolve())
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        raise ShopeeRecoveryReconciliationError("official response evidence is outside allowed roots")
    return candidate


def _mapping_source(meta: object, name: str, *, roots: Sequence[str | Path]) -> tuple[dict[str, Any], dict[str, str]]:
    if not isinstance(meta, Mapping) or set(meta) != {"path", "sha256"}:
        raise ShopeeRecoveryReconciliationError(f"{name} immutable source is invalid")
    path = _evidence_path(meta.get("path"), roots=roots)
    raw = path.read_bytes()
    supplied = _digest_value(meta.get("sha256"), f"{name} source sha256")
    if supplied != "sha256:" + hashlib.sha256(raw).hexdigest():
        raise ShopeeRecoveryReconciliationError(f"{name} source bytes digest conflicts")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ShopeeRecoveryReconciliationError(f"{name} immutable source is invalid") from error
    if not isinstance(value, Mapping):
        raise ShopeeRecoveryReconciliationError(f"{name} immutable source is invalid")
    return dict(value), {"path": str(path), "sha256": supplied}


def _read_evidence(read: object, name: str, *, query: Mapping[str, Any],
                   roots: Sequence[str | Path],
                   evidence_resolver=None) -> tuple[dict[str, Any], object]:
    if not isinstance(read, Mapping):
        raise ShopeeRecoveryReconciliationError(f"official {name} GET evidence is missing")
    complete = read.get("complete")
    if type(complete) is not bool:
        raise ShopeeRecoveryReconciliationError(f"official {name} GET completeness is invalid")
    response_digest = _digest_value(read.get("response_digest"), f"official {name} response_digest")
    safe_response_ref = str(read.get("response_ref") or "")
    try:
        path = _evidence_path(read.get("response_ref"), roots=roots)
        raw = path.read_bytes()
        safe_response_ref = str(path)
    except ShopeeRecoveryReconciliationError:
        if evidence_resolver is None:
            raise
        raw = evidence_resolver(
            {"path": read.get("response_ref"), "sha256": response_digest}, roots
        )
        if not isinstance(raw, bytes):
            raise ShopeeRecoveryReconciliationError(
                f"official {name} relocated response is invalid"
            )
    if response_digest != "sha256:" + hashlib.sha256(raw).hexdigest():
        raise ShopeeRecoveryReconciliationError(f"official {name} response bytes digest conflicts")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ShopeeRecoveryReconciliationError(f"official {name} response evidence is invalid") from error
    expected_request = {"method": "GET", "mode": "DIRECT_ID", "resource": name, **dict(query)}
    if (not isinstance(envelope, Mapping)
            or envelope.get("schema_version") != "shopee-official-direct-id-get-evidence/v1"
            or envelope.get("request") != expected_request
            or envelope.get("complete") is not complete
            or envelope.get("observed_at") != read.get("observed_at")):
        raise ShopeeRecoveryReconciliationError(f"official {name} response envelope conflicts")
    response = envelope.get("raw_response")
    if complete and response is None:
        raise ShopeeRecoveryReconciliationError(f"official {name} response body is missing")
    return ({
        "complete": complete,
        "response_ref": safe_response_ref,
        "response_digest": response_digest,
        "observed_at": _timestamp(read.get("observed_at"), f"official {name} observed_at"),
    }, response)


def _computed_differences(observed: Mapping[str, Any], frozen: Mapping[str, Any]) -> list[str]:
    differences: list[str] = []
    item = observed.get("item")
    linkage = observed.get("global_linkage")
    global_item = observed.get("global_item")
    models = observed.get("models")
    global_models = observed.get("global_models")
    if (not isinstance(item, Mapping) or not isinstance(linkage, Mapping)
            or not isinstance(global_item, Mapping) or not isinstance(models, list)
            or not isinstance(global_models, list)):
        raise ShopeeRecoveryReconciliationError("complete official readback facts are missing")
    expected_copy = frozen.get("approved_copy")
    route = frozen.get("approved_image_route")
    price = frozen.get("local_original_price")
    if not all(isinstance(value, Mapping) for value in (expected_copy, route, price)):
        raise ShopeeRecoveryReconciliationError("manifest comparison facts are missing")
    expected_ids = [str(value) for value in route.get("existing_media_ids") or []]
    checks = (
        ("item.shop", str(item.get("shop_id") or "") == frozen["shop_id"]),
        ("item.id", str(item.get("item_id") or "") == frozen["item_id"]),
        ("item.status", str(item.get("status") or "").upper() == "NORMAL"),
        ("item.category", str(item.get("category_id") or "") == str(frozen.get("category_id") or "")),
        ("copy.title", str(item.get("title") or "") == str(expected_copy.get("title") or "")),
        ("copy.description", str(item.get("description") or "") == str(expected_copy.get("description") or "")),
        ("description.type", str(item.get("description_type") or "").lower() == "extended"),
        ("gallery.images", [str(value) for value in item.get("gallery_image_ids") or []] == expected_ids),
        ("description.images", [str(value) for value in item.get("description_image_ids") or []] == expected_ids),
        ("logistics.enabled", sorted(item.get("enabled_logistics_ids") or []) == sorted(frozen.get("enabled_logistics_ids") or [])),
        ("linkage.global_item", str(linkage.get("global_item_id") or "") == frozen["global_item_id"]),
        ("linkage.shop", str(linkage.get("shop_id") or "") == frozen["shop_id"]),
        ("linkage.item", str(linkage.get("item_id") or "") == frozen["item_id"]),
        ("global_item.id", str(global_item.get("global_item_id") or "") == frozen["global_item_id"]),
        ("global_item.status", str(global_item.get("status") or "").upper() == "NORMAL"),
    )
    differences.extend(field for field, exact in checks if not exact)
    if len(models) != 1 or not isinstance(models[0], Mapping):
        differences.append("models.coverage")
    else:
        model = models[0]
        model_checks = (
            ("model.shop", str(model.get("shop_id") or "") == frozen["shop_id"]),
            ("model.item", str(model.get("item_id") or "") == frozen["item_id"]),
            ("model.id", str(model.get("model_id") or "") == frozen["model_id"]),
            ("model.sku", str(model.get("model_sku") or "") == frozen["model_sku"]),
            ("model.status", str(model.get("status") or "").upper() in {"NORMAL", "MODEL_NORMAL"}),
            ("model.currency", str(model.get("currency") or "").upper() == str(price.get("currency") or "").upper()),
            ("model.price", _equal_decimal(model.get("price"), price.get("amount"))),
        )
        differences.extend(field for field, exact in model_checks if not exact)
    if len(global_models) != 1 or not isinstance(global_models[0], Mapping):
        differences.append("global_models.coverage")
    else:
        model = global_models[0]
        global_checks = (
            ("global_model.global_item", str(model.get("global_item_id") or "") == frozen["global_item_id"]),
            ("global_model.id", str(model.get("global_model_id") or "") == frozen["global_model_id"]),
            ("global_model.sku", str(model.get("model_sku") or "") == frozen["model_sku"]),
            ("global_model.status", str(model.get("status") or "").upper() in {"NORMAL", "MODEL_NORMAL"}),
        )
        differences.extend(field for field, exact in global_checks if not exact)
    return differences


def _validate_direct_readback(readback: Mapping[str, Any], *, identity: Mapping[str, Any],
                              manifest: Mapping[str, Any], labels: list[str],
                              manifest_targets: Mapping[str, Mapping[str, Any]],
                              write_counts: Sequence[Mapping[str, Any]],
                              allowed_evidence_roots: Sequence[str | Path],
                              allow_unobservable_global_model_status: bool = False,
                              evidence_resolver=None,
                              ) -> list[dict[str, Any]]:
    _verified_digest(readback, "evidence_digest", "official readback")
    if (readback.get("schema_version") != READBACK_SCHEMA_VERSION
            or readback.get("authority") != "SHOPEE_OFFICIAL_API"
            or readback.get("run_id") != identity["run_id"]
            or readback.get("report_id") != identity["report_id"]
            or readback.get("manifest_digest") != manifest.get("manifest_digest")
            or readback.get("product_writes") != 0):
        raise ShopeeRecoveryReconciliationError("official readback identity is invalid")
    observed_at = _timestamp(readback.get("observed_at"), "official readback observed_at")
    rows = readback.get("targets")
    if (not isinstance(rows, list)
            or [row.get("target_label") for row in rows if isinstance(row, Mapping)] != labels):
        raise ShopeeRecoveryReconciliationError("official readback target scope conflicts")
    normalized: list[dict[str, Any]] = []
    all_read_times: list[datetime] = []
    ambiguity_time = _parsed_timestamp(identity["ambiguity_at"], "recovery attempt ambiguity_at")
    for row in rows:
        if not isinstance(row, Mapping):
            raise ShopeeRecoveryReconciliationError("official readback target is invalid")
        label = row["target_label"]
        frozen = manifest_targets[label]
        query = row.get("query")
        if not isinstance(query, Mapping) or query.get("method") != "GET" or query.get("mode") != "DIRECT_ID":
            raise ShopeeRecoveryReconciliationError("official reconciliation must be GET-only direct-ID readback")
        exact = {
            "shop_id": _positive_id(query.get("shop_id"), "readback shop_id"),
            "item_id": _positive_id(query.get("item_id"), "readback item_id"),
            "global_item_id": _positive_id(query.get("global_item_id"), "readback global_item_id"),
            "model_id": _positive_id(query.get("model_id"), "readback model_id"),
            "global_model_id": _positive_id(query.get("global_model_id"), "readback global_model_id"),
            "model_sku": str(query.get("model_sku") or "").strip(),
        }
        if any(exact[key] != frozen[key] for key in exact):
            raise ShopeeRecoveryReconciliationError("official direct-ID query conflicts with frozen target identity")
        reads = row.get("reads")
        required_reads = ("item", "models", "global_linkage", "global_item", "global_models")
        if not isinstance(reads, Mapping) or set(reads) != set(required_reads):
            raise ShopeeRecoveryReconciliationError("official direct-ID GET evidence set conflicts")
        loaded = {name: _read_evidence(
            reads[name], name, query=exact, roots=allowed_evidence_roots,
            evidence_resolver=evidence_resolver,
        ) for name in required_reads}
        safe_reads = {name: value[0] for name, value in loaded.items()}
        read_times = [
            _parsed_timestamp(value["observed_at"], f"official {name} observed_at")
            for name, value in safe_reads.items()
        ]
        if any(value <= ambiguity_time for value in read_times):
            raise ShopeeRecoveryReconciliationError("official GET evidence predates the ambiguous attempt")
        all_read_times.extend(read_times)
        complete = all(value["complete"] for value in safe_reads.values())
        observed = ({
            "item": loaded["item"][1],
            "models": loaded["models"][1],
            "global_linkage": loaded["global_linkage"][1],
            "global_item": loaded["global_item"][1],
            "global_models": loaded["global_models"][1],
        } if complete else None)
        differences = _computed_differences(observed, frozen) if complete else []
        if complete and allow_unobservable_global_model_status:
            global_models = loaded["global_models"][1]
            if (isinstance(global_models, list) and len(global_models) == 1
                    and isinstance(global_models[0], Mapping)):
                global_model = global_models[0]
                exact_identity = (
                    str(global_model.get("global_item_id") or "") == frozen["global_item_id"]
                    and str(global_model.get("global_model_id") or "") == frozen["global_model_id"]
                    and str(global_model.get("model_sku") or "") == frozen["model_sku"]
                )
                if exact_identity and str(global_model.get("status") or "") == "":
                    differences = [field for field in differences
                                   if field != "global_model.status"]
        prior = next(value for value in write_counts if value["target_label"] == label)
        if differences and prior["outcome_unknown"] is not True:
            raise ShopeeRecoveryReconciliationError("known PUBLISHED target official readback must MATCH")
        classification = "UNKNOWN" if not complete else ("DIFF" if differences else "MATCH")
        target_body = deepcopy(dict(row))
        supplied_target_digest = _digest_value(
            target_body.pop("target_evidence_digest", None),
            "target evidence digest",
        )
        if supplied_target_digest != _digest(target_body):
            raise ShopeeRecoveryReconciliationError("target evidence digest conflicts")
        normalized.append({
            "target_label": label,
            **exact,
            "classification": classification,
            "complete": complete,
            "differences": differences,
            "reads": safe_reads,
            "observed_digest": _digest(observed) if complete else None,
            "target_evidence_digest": supplied_target_digest,
            "observed_at": observed_at,
        })
    if _parsed_timestamp(observed_at, "official readback observed_at") != max(all_read_times):
        raise ShopeeRecoveryReconciliationError("official readback observed_at is not derived from GET evidence")
    return normalized


def build_reconciliation_receipt(*, attempt: Mapping[str, Any], manifest: Mapping[str, Any],
                                 recovery_authorization: Mapping[str, Any],
                                 official_readback: Mapping[str, Any],
                                 source_files: Mapping[str, Mapping[str, str]],
                                 allowed_evidence_roots: Sequence[str | Path]) -> dict[str, Any]:
    """Validate one normalized readback and return a self-digesting receipt."""

    if not all(isinstance(value, Mapping) for value in
               (attempt, manifest, recovery_authorization, official_readback)):
        raise ShopeeRecoveryReconciliationError("reconciliation inputs must be mappings")
    if not allowed_evidence_roots:
        raise ShopeeRecoveryReconciliationError("official evidence roots are required")
    if not isinstance(source_files, Mapping) or set(source_files) != {"attempt", "manifest"}:
        raise ShopeeRecoveryReconciliationError("immutable source file set conflicts")
    source_attempt, safe_attempt_source = _mapping_source(
        source_files["attempt"], "attempt", roots=allowed_evidence_roots,
    )
    source_manifest, safe_manifest_source = _mapping_source(
        source_files["manifest"], "manifest", roots=allowed_evidence_roots,
    )
    if source_attempt != dict(attempt) or source_manifest != dict(manifest):
        raise ShopeeRecoveryReconciliationError("immutable source facts conflict with reconciliation input")
    labels, targets = _manifest_targets(manifest)
    if dict(recovery_authorization) != dict(manifest):
        raise ShopeeRecoveryReconciliationError("recovery authorization does not equal the immutable manifest")
    if attempt.get("recovery_authorization") != dict(recovery_authorization):
        raise ShopeeRecoveryReconciliationError("attempt does not bind the recovery authorization")
    identity, write_counts = _attempt_identity(attempt, manifest, labels)
    readbacks = _validate_direct_readback(
        official_readback, identity=identity, manifest=manifest, labels=labels,
        manifest_targets=targets, write_counts=write_counts,
        allowed_evidence_roots=allowed_evidence_roots,
    )
    classes = [row["classification"] for row in readbacks]
    if "UNKNOWN" in classes:
        result = "UNKNOWN"
    elif "DIFF" in classes:
        result = "REMAINING_DIFF"
    else:
        result = "CONVERGED"
    remaining = [
        {"target_label": row["target_label"], "differences": row["differences"]}
        for row in readbacks if row["differences"]
    ]
    core = {
        "schema_version": SCHEMA_VERSION,
        "result": result,
        "attempt_closed": result in _TERMINAL,
        "mutation_lock": result == "UNKNOWN",
        "new_manifest": {
            "required": result == "REMAINING_DIFF",
            "authorized": False,
            "fresh_exact_preflight_required": result == "REMAINING_DIFF",
            "target_scope": [row["target_label"] for row in remaining],
            "exact_remaining_differences": remaining,
        },
        "attempt": identity,
        "manifest_digest": manifest["manifest_digest"],
        "recovery_authorization_digest": _digest(recovery_authorization),
        "ordered_target_labels": labels,
        "write_counts": write_counts,
        "official_direct_id_readback": {
            "evidence_digest": official_readback["evidence_digest"],
            "observed_at": official_readback["observed_at"],
            "product_writes": 0,
            "targets": readbacks,
        },
        "validation_inputs": {
            "attempt": deepcopy(dict(attempt)),
            "manifest": deepcopy(dict(manifest)),
            "recovery_authorization": deepcopy(dict(recovery_authorization)),
            "official_readback": deepcopy(dict(official_readback)),
            "source_files": {"attempt": safe_attempt_source, "manifest": safe_manifest_source},
        },
    }
    return {**core, "receipt_digest": _digest(core)}


def validate_reconciliation_receipt(receipt: Mapping[str, Any], *,
                                    allowed_evidence_roots: Sequence[str | Path]) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise ShopeeRecoveryReconciliationError("reconciliation receipt is invalid")
    value = deepcopy(dict(receipt))
    supplied = _digest_value(value.pop("receipt_digest", None), "receipt_digest")
    if supplied != _digest(value) or value.get("schema_version") != SCHEMA_VERSION:
        raise ShopeeRecoveryReconciliationError("reconciliation receipt digest conflicts")
    result = value.get("result")
    if result not in {"CONVERGED", "REMAINING_DIFF", "UNKNOWN"}:
        raise ShopeeRecoveryReconciliationError("reconciliation receipt result is invalid")
    if value.get("attempt_closed") != (result in _TERMINAL) or value.get("mutation_lock") != (result == "UNKNOWN"):
        raise ShopeeRecoveryReconciliationError("reconciliation receipt closure state conflicts")
    inputs = value.get("validation_inputs")
    if not isinstance(inputs, Mapping) or set(inputs) != {
        "attempt", "manifest", "recovery_authorization", "official_readback", "source_files",
    }:
        raise ShopeeRecoveryReconciliationError("reconciliation receipt validation inputs are missing")
    rebuilt = build_reconciliation_receipt(
        attempt=inputs["attempt"], manifest=inputs["manifest"],
        recovery_authorization=inputs["recovery_authorization"],
        official_readback=inputs["official_readback"],
        source_files=inputs["source_files"],
        allowed_evidence_roots=allowed_evidence_roots,
    )
    checked = {**value, "receipt_digest": supplied}
    if rebuilt != checked:
        raise ShopeeRecoveryReconciliationError("reconciliation receipt semantics conflict")
    return checked


def reconciliation_gate(receipt: Mapping[str, Any], *, run_id: str,
                        report_id: str, manifest_digest: str,
                        allowed_evidence_roots: Sequence[str | Path]) -> dict[str, Any]:
    """Return the small server/claim decision for one exact recovery attempt."""

    checked = validate_reconciliation_receipt(
        receipt, allowed_evidence_roots=allowed_evidence_roots,
    )
    attempt = checked.get("attempt")
    if (not isinstance(attempt, Mapping)
            or attempt.get("run_id") != run_id
            or attempt.get("report_id") != report_id
            or checked.get("manifest_digest") != manifest_digest):
        raise ShopeeRecoveryReconciliationError("reconciliation gate identity conflicts")
    return {
        "result": checked["result"],
        "attempt_closed": checked["attempt_closed"],
        "mutation_lock": checked["mutation_lock"],
        "new_manifest": deepcopy(checked["new_manifest"]),
        "receipt_digest": checked["receipt_digest"],
    }


class ShopeeRecoveryReconciliationStore:
    """Content-addressed append-only receipt files; never touches production DBs."""

    def __init__(self, root: str | Path, *,
                 allowed_evidence_roots: Sequence[str | Path]) -> None:
        self.root = Path(root)
        if not allowed_evidence_roots:
            raise ShopeeRecoveryReconciliationError("official evidence roots are required")
        self.allowed_evidence_roots = tuple(allowed_evidence_roots)

    def _attempt_dir(self, run_id: str) -> Path:
        key = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return self.root / key

    def list_attempt(self, *, run_id: str) -> list[dict[str, Any]]:
        directory = self._attempt_dir(run_id)
        if not directory.is_dir():
            return []
        receipts: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                receipt = validate_reconciliation_receipt(
                    json.loads(path.read_text(encoding="utf-8")),
                    allowed_evidence_roots=self.allowed_evidence_roots,
                )
            except (OSError, json.JSONDecodeError) as error:
                raise ShopeeRecoveryReconciliationError("stored reconciliation receipt is invalid") from error
            if receipt["attempt"]["run_id"] != run_id or path.stem != receipt["receipt_digest"].removeprefix("sha256:"):
                raise ShopeeRecoveryReconciliationError("stored reconciliation receipt identity conflicts")
            receipts.append(receipt)
        return sorted(receipts, key=lambda row: row["official_direct_id_readback"]["observed_at"])

    def register(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        checked = validate_reconciliation_receipt(
            receipt, allowed_evidence_roots=self.allowed_evidence_roots,
        )
        run_id = str(checked.get("attempt", {}).get("run_id") or "")
        if not run_id:
            raise ShopeeRecoveryReconciliationError("reconciliation attempt run_id is invalid")
        existing = self.list_attempt(run_id=run_id)
        for prior in existing:
            if prior["receipt_digest"] == checked["receipt_digest"]:
                return prior
            if prior["attempt"] != checked["attempt"] or prior["manifest_digest"] != checked["manifest_digest"]:
                raise ShopeeRecoveryReconciliationError("append-only attempt identity conflicts")
            if prior["attempt_closed"]:
                raise ShopeeRecoveryReconciliationError("recovery attempt is already closed")
        if existing:
            prior_targets = {
                row["target_label"]: row
                for row in existing[-1]["official_direct_id_readback"]["targets"]
            }
            for row in checked["official_direct_id_readback"]["targets"]:
                prior = prior_targets[row["target_label"]]
                for resource, read in row["reads"].items():
                    if (_parsed_timestamp(read["observed_at"], "official GET observed_at")
                            <= _parsed_timestamp(prior["reads"][resource]["observed_at"],
                                                 "prior official GET observed_at")):
                        raise ShopeeRecoveryReconciliationError(
                            "every official GET must be newer than the prior receipt"
                        )
        directory = self._attempt_dir(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{checked['receipt_digest'].removeprefix('sha256:')}.json"
        encoded = (_canonical(checked) + "\n").encode("utf-8")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            stored = validate_reconciliation_receipt(
                json.loads(path.read_text(encoding="utf-8")),
                allowed_evidence_roots=self.allowed_evidence_roots,
            )
            if stored != checked:
                raise ShopeeRecoveryReconciliationError("append-only receipt file conflicts")
            return stored
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        return checked


__all__ = [
    "READBACK_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "ShopeeRecoveryReconciliationError",
    "ShopeeRecoveryReconciliationStore",
    "build_reconciliation_receipt",
    "reconciliation_gate",
    "validate_reconciliation_receipt",
]
