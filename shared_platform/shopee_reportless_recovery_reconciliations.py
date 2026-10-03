"""Reconcile a Shopee recovery that lost its final report after execution.

The run cursor cannot prove a write count once it entered RUNNING.  This
boundary therefore preserves UNKNOWN write accounting, rebuilds all official
GET evidence, and only closes the attempt when those reads identify the exact
remaining differences.  It never calls a provider transport.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import uuid
from typing import Any
from datetime import datetime, timezone, timedelta

from shared_platform.shopee_recovery_reconciliations import (
    ShopeeRecoveryReconciliationError,
    _manifest_targets,
    _validate_direct_readback,
)


SCHEMA_VERSION = "shopee-reportless-recovery-reconciliation/v1"
_FAILING_COMMIT = "d4d105e2408df9f89126ec4f76759b22929e86bd"
_FIX_COMMIT = "3b20719a3a8a201aaad456229dc3e2d2c736b1e7"


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_meta(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()),
            "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}


def _load_ref(meta: Mapping[str, str], roots: Sequence[str | Path],
              evidence_resolver=None) -> tuple[dict, dict]:
    if not isinstance(meta, Mapping) or set(meta) != {"path", "sha256"}:
        raise ShopeeRecoveryReconciliationError("reportless source ref is invalid")
    try:
        path = _safe_root(meta["path"], roots)
        if not path.is_file() or path.suffix.lower() != ".json":
            raise ShopeeRecoveryReconciliationError("reportless source file is invalid")
        raw = path.read_bytes()
        safe_path = str(path)
    except ShopeeRecoveryReconciliationError:
        if evidence_resolver is None:
            raise
        raw = evidence_resolver(dict(meta), roots)
        if not isinstance(raw, bytes):
            raise ShopeeRecoveryReconciliationError("reportless relocated source is invalid")
        safe_path = str(meta["path"])
    actual = "sha256:" + hashlib.sha256(raw).hexdigest()
    if actual != meta["sha256"]:
        raise ShopeeRecoveryReconciliationError("reportless source bytes digest conflicts")
    try: value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ShopeeRecoveryReconciliationError("reportless source JSON is invalid") from error
    if not isinstance(value, Mapping):
        raise ShopeeRecoveryReconciliationError("reportless source JSON is invalid")
    return dict(value), {"path": safe_path, "sha256": actual}


def _reportless_relocation_resolver(*, attestation_ref: Mapping[str, str],
                                    receipt: Mapping[str, Any],
                                    preflight: Mapping[str, Any],
                                    original_manifest: Mapping[str, Any],
                                    roots: Sequence[str | Path]):
    """Bind the exact 34-file sr4bc/sr590e logical evidence closure."""
    from shared_platform.evidence_relocation_attestations import (
        EvidenceRelocationError, EvidenceRelocationResolver,
        validate_evidence_relocation_attestation,
    )

    attestation, _ = _load_ref(attestation_ref, roots)
    target_root = Path(str(attestation_ref.get("path") or "")).resolve().parent
    expected_authorities = {
        "reportless_receipt": receipt.get("receipt_digest"),
        "domain_closure": preflight.get("closure_digest"),
        "unauthorized_continuation": preflight.get("unauthorized_continuation_digest"),
        "continuation_preflight": preflight.get("preflight_digest"),
        "original_recovery_manifest": original_manifest.get("manifest_digest"),
    }
    try:
        provisional = EvidenceRelocationResolver(
            attestation, allowed_target_root=target_root,
            expected_authority_digests=expected_authorities,
        )
        logical = []
        old_inputs = receipt.get("validation_inputs") or {}
        fresh_sources = preflight.get("source_refs") or {}
        for top_meta, authority_meta in (
            (old_inputs.get("official_readback_ref"), old_inputs.get("authority_ref")),
            (fresh_sources.get("fresh_readback"), fresh_sources.get("collector_authority")),
        ):
            top, _ = provisional.load(top_meta, allowed_roots=roots)
            logical.extend((top_meta, authority_meta))
            for target in top.get("targets") or []:
                reads = target.get("reads") if isinstance(target, Mapping) else None
                if not isinstance(reads, Mapping):
                    raise EvidenceRelocationError("relocated readback target is invalid")
                for name in ("item", "models", "global_linkage", "global_item", "global_models"):
                    meta = reads.get(name)
                    if not isinstance(meta, Mapping):
                        raise EvidenceRelocationError("relocated raw GET ref is invalid")
                    logical.append({"path": meta.get("response_ref"),
                                    "sha256": meta.get("response_digest")})
        by_key = {
            (row["original"]["path"], row["original"]["sha256"]): row["original"]
            for row in attestation.get("entries") or [] if isinstance(row, Mapping)
        }
        keys = [(str(meta.get("path")), str(meta.get("sha256")))
                for meta in logical if isinstance(meta, Mapping)]
        if len(keys) != 34 or len(set(keys)) != 34 or set(keys) != set(by_key):
            raise EvidenceRelocationError("relocation evidence closure is not exact")
        required = [by_key[key] for key in keys]
        validate_evidence_relocation_attestation(
            attestation, allowed_target_root=target_root,
            expected_authority_digests=expected_authorities,
            required_original_refs=required,
        )
        return EvidenceRelocationResolver(
            attestation, allowed_target_root=target_root,
            expected_authority_digests=expected_authorities,
            required_original_refs=required,
        )
    except EvidenceRelocationError as error:
        raise ShopeeRecoveryReconciliationError(
            "reportless evidence relocation conflicts"
        ) from error


def _safe_root(path: str | Path, roots: Sequence[str | Path]) -> Path:
    candidate = Path(path).resolve()
    allowed = [Path(root).resolve(strict=True) for root in roots]
    if not any(candidate == root or root in candidate.parents for root in allowed):
        raise ShopeeRecoveryReconciliationError("reportless evidence path is outside allowed roots")
    return candidate


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (_canonical(value) + "\n").encode("utf-8")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ShopeeRecoveryReconciliationError("reportless immutable evidence conflicts")
        return
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def _claim_and_publish(path: Path, value: Mapping[str, Any]) -> bool:
    """Atomically create one run slot without ever replacing existing bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"receipt.{uuid.uuid4().hex}.tmp")
    _write_once(temporary, value)
    try:
        os.link(temporary, path)
    except FileExistsError:
        return False
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _run_events(run_store: object, run_id: str) -> list[dict[str, Any]]:
    # get_run_by_id validates the run identity and every append-only event
    # digest before this direct read exposes the already-validated sequence.
    run = run_store.get_run_by_id(run_id=run_id)
    if not isinstance(run, Mapping):
        raise ShopeeRecoveryReconciliationError("reportless recovery run is unavailable")
    connection = sqlite3.connect(Path(run_store.path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT sequence,state,final_report_id,failure_code,created_at,"
            "run_identity_digest,event_digest FROM product_publication_run_events "
            "WHERE run_id=? ORDER BY sequence", (run_id,),
        ).fetchall()
    finally:
        connection.close()
    events = [dict(row) for row in rows]
    if ([row["sequence"] for row in events] != [1, 2, 3]
            or [row["state"] for row in events] != ["QUEUED", "RUNNING", "FAILED"]
            or any(row["final_report_id"] is not None for row in events)
            or [row["failure_code"] for row in events]
                != [None, None, "RUNNER_INFRASTRUCTURE_FAILED"]
            or any(row["run_identity_digest"] != events[0]["run_identity_digest"]
                   for row in events)
            or events[-1]["created_at"] != run.get("updated_at")):
        raise ShopeeRecoveryReconciliationError("reportless recovery lifecycle is not exact")
    return events


def _code_fix_provenance(run: Mapping[str, Any], *, repo_root: str | Path,
                         incident: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(repo_root).resolve(strict=True)
    failing = str((run.get("execution_identity") or {}).get("git_commit") or "")
    path = "shared_platform/product_publication_reports.py"
    fix_commit = _FIX_COMMIT
    if failing != _FAILING_COMMIT or incident != {
        "schema_version": "shopee-recovery-runtime-failure/v1",
        "run_id": run.get("run_id"), "failure_code": "RUNNER_INFRASTRUCTURE_FAILED",
        "error_type": "NameError", "missing_symbol": "deepcopy",
        "failing_commit": _FAILING_COMMIT, "fix_commit": _FIX_COMMIT,
        "log_line": f"publication background run failed run_id={run.get('run_id')} error_type=NameError",
    }:
        raise ShopeeRecoveryReconciliationError("failure code provenance is invalid")
    try:
        old = subprocess.check_output(["git", "show", f"{failing}:{path}"], cwd=root)
        fixed = subprocess.check_output(["git", "show", f"{fix_commit}:{path}"], cwd=root)
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", failing, fix_commit], cwd=root,
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
    except (OSError, subprocess.CalledProcessError) as error:
        raise ShopeeRecoveryReconciliationError("failure code provenance is unavailable") from error
    marker = b"from copy import deepcopy"
    if marker in old or marker not in fixed or not ancestor:
        raise ShopeeRecoveryReconciliationError("NameError fix provenance conflicts")
    return {
        "classification": "AUDITED_CODE_DEFECT_WITH_OPERATOR_OBSERVATION",
        "operator_observation": deepcopy(dict(incident)),
        "failure_code": "RUNNER_INFRASTRUCTURE_FAILED", "error_type": "NameError",
        "module_path": path, "missing_symbol": "deepcopy",
        "failing_commit": failing, "fix_commit": fix_commit,
        "failing_blob_sha256": "sha256:" + hashlib.sha256(old).hexdigest(),
        "fixed_blob_sha256": "sha256:" + hashlib.sha256(fixed).hexdigest(),
    }


def _validate_collector_authority(
    authority: Mapping[str, Any], *, safe_authority_ref: Mapping[str, str],
    safe_manifest_ref: Mapping[str, str], manifest: Mapping[str, Any],
    run: Mapping[str, Any], events: Sequence[Mapping[str, Any]],
    official_readback: Mapping[str, Any],
) -> None:
    expected_keys = {
        "schema_version", "authority_digest", "authorized_method", "expected_report_id",
        "immutable_report_present", "manifest", "manifest_digest", "observed_at",
        "ordered_events", "product_writes", "run",
    }
    if set(authority) != expected_keys:
        raise ShopeeRecoveryReconciliationError("reportless collector authority schema conflicts")
    body = dict(authority); supplied = body.pop("authority_digest", None)
    if (authority.get("schema_version") != "shopee-get-only-collector-authority/v1"
            or supplied != _digest(body)
            or authority.get("authorized_method") != "GET"
            or authority.get("manifest") != dict(safe_manifest_ref)
            or authority.get("manifest_digest") != manifest["manifest_digest"]
            or authority.get("run") != dict(run)
            or authority.get("ordered_events") != [{key: row[key] for key in (
                "sequence", "state", "final_report_id", "failure_code", "created_at", "event_digest")}
                for row in events]
            or authority.get("expected_report_id") != run["report_id"]
            or authority.get("immutable_report_present") is not False
            or authority.get("product_writes") != 0
            or authority.get("observed_at") != official_readback.get("observed_at")):
        raise ShopeeRecoveryReconciliationError("reportless collector authority conflicts")


def build_reportless_reconciliation_receipt(
    *, run_store: object, report_store: object, run_id: str,
    manifest_ref: Mapping[str, str], snapshot: Mapping[str, Any],
    candidate: Mapping[str, Any], approval: Mapping[str, Any],
    recovery_retry_receipt_digest: str,
    official_readback_ref: Mapping[str, str], incident_ref: Mapping[str, str],
    authority_ref: Mapping[str, str], evidence_root: str | Path,
    allowed_evidence_roots: Sequence[str | Path], repo_root: str | Path,
    evidence_resolver=None,
) -> dict[str, Any]:
    """Build a terminal REMAINING_DIFF receipt from durable sources."""
    if not allowed_evidence_roots:
        raise ShopeeRecoveryReconciliationError("reportless evidence roots are required")
    root = _safe_root(evidence_root, allowed_evidence_roots)
    manifest, safe_manifest_ref = _load_ref(manifest_ref, allowed_evidence_roots, evidence_resolver)
    official_readback, safe_readback_ref = _load_ref(
        official_readback_ref, allowed_evidence_roots, evidence_resolver)
    incident, safe_incident_ref = _load_ref(incident_ref, allowed_evidence_roots, evidence_resolver)
    authority, safe_authority_ref = _load_ref(authority_ref, allowed_evidence_roots, evidence_resolver)
    from shared_platform.shopee_regional_recovery import validate_recovery_manifest
    checked_manifest = validate_recovery_manifest(
        manifest, snapshot=snapshot, candidate=candidate, approval=approval,
        allowed_source_roots=allowed_evidence_roots,
    )
    if dict(checked_manifest) != dict(manifest):
        raise ShopeeRecoveryReconciliationError("reportless manifest validation changed facts")
    from shared_platform.shopee_recovery_run_reconciliations import (
        ShopeeRecoveryRunReconciliationStore,
    )
    retry_source_run = str((run_store.get_run_by_id(run_id=run_id) or {}).get("request_identity", {}).get("retry_of_run_id") or "")
    checked_retry = ShopeeRecoveryRunReconciliationStore(run_store.path).get(
        run_id=retry_source_run, allowed_evidence_roots=allowed_evidence_roots,
        snapshot=snapshot, candidate=candidate, approval=approval,
    )
    if checked_retry is None or checked_retry.get("receipt_digest") != recovery_retry_receipt_digest:
        raise ShopeeRecoveryReconciliationError("recovery retry receipt validation changed facts")
    recovery_retry_receipt = checked_retry
    run = run_store.get_run_by_id(run_id=run_id)
    if (not isinstance(run, Mapping) or run.get("state") != "FAILED"
            or run.get("failure_code") != "RUNNER_INFRASTRUCTURE_FAILED"
            or run.get("final_report_id") is not None
            or run.get("platform_scope") != ["SHOPEE"]
            or run.get("target_count") != len(manifest.get("target_labels") or [])
            or run.get("offer_id") != manifest.get("offer_id")
            or run.get("revision") != manifest.get("product_revision")
            or run.get("plan_id") != manifest.get("plan_id")
            or run.get("snapshot_digest") != manifest.get("execution_snapshot_digest")):
        raise ShopeeRecoveryReconciliationError("reportless recovery run identity conflicts")
    expected_request = {
        "kind": "SHOPEE_RECOVERY_RETRY",
        "authority_digest": manifest.get("manifest_digest"),
        "reconciliation_receipt_digest": recovery_retry_receipt.get("receipt_digest"),
        "retry_of_run_id": recovery_retry_receipt.get("run_identity", {}).get("run_id"),
    }
    if run.get("request_identity") != expected_request:
        raise ShopeeRecoveryReconciliationError("reportless recovery request identity conflicts")
    if report_store.get_report_by_run(run_id=run_id) is not None:
        raise ShopeeRecoveryReconciliationError("reportless recovery unexpectedly has a final report")
    events = _run_events(run_store, run_id)
    _validate_collector_authority(
        authority, safe_authority_ref=safe_authority_ref,
        safe_manifest_ref=safe_manifest_ref, manifest=manifest, run=run,
        events=events, official_readback=official_readback,
    )
    provenance = _code_fix_provenance(run, repo_root=repo_root, incident=incident)

    labels, frozen_targets = _manifest_targets(manifest)
    identity = {
        "run_id": run_id, "report_id": run["report_id"], "offer_id": run["offer_id"],
        "revision": run["revision"], "plan_id": run["plan_id"],
        "snapshot_digest": run["snapshot_digest"], "attempt_report_digest": None,
        "ambiguity_at": run["updated_at"],
    }
    write_counts = [{"target_label": label, "attempt_status": "PROCESSING",
                     "request_attempted": True, "outcome_unknown": True,
                     "confirmed_external_write_count": None} for label in labels]
    normalized = _validate_direct_readback(
        official_readback, identity=identity, manifest=manifest, labels=labels,
        manifest_targets=frozen_targets, write_counts=write_counts,
        allowed_evidence_roots=allowed_evidence_roots,
        allow_unobservable_global_model_status=True,
        evidence_resolver=evidence_resolver,
    )
    observed = datetime.fromisoformat(str(official_readback["observed_at"]).replace("Z", "+00:00"))
    if observed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ShopeeRecoveryReconciliationError("reportless official readback is future-dated")
    if any(row["classification"] == "UNKNOWN" for row in normalized):
        raise ShopeeRecoveryReconciliationError("reportless official state is still unknown")
    remaining = [{"target_label": row["target_label"], "differences": row["differences"]}
                 for row in normalized if row["differences"]]
    if not remaining:
        raise ShopeeRecoveryReconciliationError("reportless recovery has no remaining differences")
    allowed = {"copy.title", "copy.description", "description.type",
               "description.images", "item.status"}
    if ([row["target_label"] for row in remaining] != manifest["target_labels"]
            or any(set(row["differences"]) != allowed for row in remaining)):
        raise ShopeeRecoveryReconciliationError("reportless recovery differences exceed safe continuation")
    continuation = {
        "required": True, "authorized": False, "fresh_exact_preflight_required": True,
        "direct_predecessor_run_id": run_id,
        "original_publication_run_id": manifest["prior_run_id"],
        "pre_running_retry_run_id": recovery_retry_receipt["run_identity"]["run_id"],
        "pre_running_retry_receipt_digest": recovery_retry_receipt["receipt_digest"],
        "target_scope": list(manifest["target_labels"]),
        "exact_remaining_differences": deepcopy(remaining),
        "allowed_actions": {label: ["update_copy_and_description_media", "list_existing_item"]
                            for label in manifest["target_labels"]},
        "mutation_budgets": {label: {"shared_maximum": 0, "target_maximum": 2,
                                      "maximum_confirmed_writes": 2}
                             for label in manifest["target_labels"]},
        "forbidden_actions": ["create_publish_task", "upload_images", "global_mutation",
                              "update_images_existing_media", "update_price", "update_category",
                              "update_logistics"],
    }
    core = {
        "schema_version": SCHEMA_VERSION, "result": "REMAINING_DIFF",
        "attempt_closed": True, "mutation_lock": False,
        "attempt": {"run_id": run_id, "report_id": run["report_id"],
                    "ambiguity_at": run["updated_at"],
                    "provider_mutation_attempts": "UNKNOWN",
                    "external_write_count": None},
        "manifest_digest": manifest["manifest_digest"],
        "recovery_retry_receipt_digest": recovery_retry_receipt["receipt_digest"],
        "run_events": events, "code_fix_provenance": provenance,
        "authority_identity": {
            "offer_id": manifest["offer_id"], "plan_id": manifest["plan_id"],
            "snapshot_digest": manifest["execution_snapshot_digest"],
            "target_labels": list(manifest["target_labels"]),
            "prior_run_id": manifest["prior_run_id"],
            "prior_report_digest": manifest["prior_report_digest"],
        },
        "official_reconciliation": {
            "evidence_digest": official_readback["evidence_digest"],
            "observed_at": official_readback["observed_at"],
            "reconciliation_collector_product_writes": 0,
            "targets": normalized,
        },
        "new_manifest": continuation,
        "source_refs": {"manifest": safe_manifest_ref, "official_readback": safe_readback_ref,
                        "collector_authority": safe_authority_ref, "incident": safe_incident_ref},
        "validation_inputs": {
            "run_id": run_id, "manifest_ref": safe_manifest_ref,
            "recovery_retry_receipt_digest": recovery_retry_receipt["receipt_digest"],
            "official_readback_ref": safe_readback_ref, "authority_ref": safe_authority_ref,
            "incident_ref": safe_incident_ref,
            "snapshot": deepcopy(dict(snapshot)), "candidate": deepcopy(dict(candidate)),
            "approval": deepcopy(dict(approval)),
            "evidence_root": str(root), "repo_root": str(Path(repo_root).resolve()),
        },
    }
    return {**core, "receipt_digest": _digest(core)}


def validate_reportless_reconciliation_receipt(
    receipt: Mapping[str, Any], *, run_store: object, report_store: object,
    snapshot: Mapping[str, Any], candidate: Mapping[str, Any], approval: Mapping[str, Any],
    allowed_evidence_roots: Sequence[str | Path],
    evidence_resolver=None,
) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise ShopeeRecoveryReconciliationError("reportless receipt is invalid")
    value = deepcopy(dict(receipt)); supplied = value.pop("receipt_digest", None)
    if supplied != _digest(value) or value.get("schema_version") != SCHEMA_VERSION:
        raise ShopeeRecoveryReconciliationError("reportless receipt digest conflicts")
    inputs = value.get("validation_inputs")
    if not isinstance(inputs, Mapping) or set(inputs) != {
        "run_id", "manifest_ref", "recovery_retry_receipt_digest", "official_readback_ref",
        "authority_ref", "incident_ref", "snapshot", "candidate", "approval",
        "evidence_root", "repo_root",
    }:
        raise ShopeeRecoveryReconciliationError("reportless validation inputs are invalid")
    rebuilt = build_reportless_reconciliation_receipt(
        run_store=run_store, report_store=report_store, run_id=inputs["run_id"],
        manifest_ref=inputs["manifest_ref"], snapshot=inputs["snapshot"],
        candidate=inputs["candidate"], approval=inputs["approval"],
        recovery_retry_receipt_digest=inputs["recovery_retry_receipt_digest"],
        official_readback_ref=inputs["official_readback_ref"],
        authority_ref=inputs["authority_ref"], incident_ref=inputs["incident_ref"],
        evidence_root=inputs["evidence_root"],
        allowed_evidence_roots=allowed_evidence_roots,
        evidence_resolver=evidence_resolver,
        repo_root=inputs["repo_root"],
    )
    checked = {**value, "receipt_digest": supplied}
    if rebuilt != checked:
        raise ShopeeRecoveryReconciliationError("reportless receipt semantics conflict")
    return checked


class ShopeeReportlessRecoveryReconciliationStore:
    """Append-only receipts whose authority is rebuilt on every read."""

    def __init__(self, root: str | Path, *, run_store: object, report_store: object,
                 snapshot: Mapping[str, Any], candidate: Mapping[str, Any],
                 approval: Mapping[str, Any],
                 allowed_evidence_roots: Sequence[str | Path],
                 evidence_resolver=None) -> None:
        self.root = _safe_root(root, allowed_evidence_roots)
        self.run_store = run_store; self.report_store = report_store
        self.snapshot = deepcopy(dict(snapshot)); self.candidate = deepcopy(dict(candidate))
        self.approval = deepcopy(dict(approval))
        self.allowed_evidence_roots = tuple(allowed_evidence_roots)
        self.evidence_resolver = evidence_resolver

    def _path(self, run_id: str) -> Path:
        key = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return self.root / key / "receipt.json"

    def _validate(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return validate_reportless_reconciliation_receipt(
            value, run_store=self.run_store, report_store=self.report_store,
            snapshot=self.snapshot, candidate=self.candidate, approval=self.approval,
            allowed_evidence_roots=self.allowed_evidence_roots,
            evidence_resolver=self.evidence_resolver,
        )

    def register(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        checked = self._validate(receipt)
        run_id = checked["attempt"]["run_id"]
        path = self._path(run_id)
        acquired = _claim_and_publish(path, checked)
        if not acquired:
            stored = self._validate(json.loads(path.read_text(encoding="utf-8")))
            if stored != checked:
                raise ShopeeRecoveryReconciliationError(
                    "reportless recovery attempt already has a terminal receipt"
                )
            return stored
        stored = self._validate(json.loads(path.read_text(encoding="utf-8")))
        if stored != checked:
            raise ShopeeRecoveryReconciliationError(
                "reportless recovery attempt already has a terminal receipt"
            )
        return stored

    def get(self, *, run_id: str, receipt_digest: str) -> dict[str, Any] | None:
        path = self._path(run_id)
        if not path.is_file():
            return None
        checked = self._validate(json.loads(path.read_text(encoding="utf-8")))
        if checked["receipt_digest"] != receipt_digest:
            return None
        return checked


def build_reportless_continuation_preflight(
    *, receipt: Mapping[str, Any], run_store: object, report_store: object,
    snapshot: Mapping[str, Any], candidate: Mapping[str, Any], approval: Mapping[str, Any],
    closure_ref: Mapping[str, str], continuation_ref: Mapping[str, str],
    fresh_readback_ref: Mapping[str, str], fresh_authority_ref: Mapping[str, str],
    operations_db: str | Path, allowed_evidence_roots: Sequence[str | Path],
    evidence_resolver=None,
) -> dict[str, Any]:
    """Authorize only the unchanged, freshly re-read remaining regional work."""
    checked = validate_reportless_reconciliation_receipt(
        receipt, run_store=run_store, report_store=report_store, snapshot=snapshot,
        candidate=candidate, approval=approval, allowed_evidence_roots=allowed_evidence_roots,
        evidence_resolver=evidence_resolver,
    )
    closure, safe_closure = _load_ref(closure_ref, allowed_evidence_roots, evidence_resolver)
    continuation, safe_continuation = _load_ref(
        continuation_ref, allowed_evidence_roots, evidence_resolver)
    readback, safe_readback = _load_ref(
        fresh_readback_ref, allowed_evidence_roots, evidence_resolver)
    authority, safe_authority = _load_ref(
        fresh_authority_ref, allowed_evidence_roots, evidence_resolver)
    closure_body = dict(closure); closure_digest = closure_body.pop("closure_digest", None)
    target_skus = {str(row.get("model_sku") or "")
                   for row in checked["official_reconciliation"]["targets"]}
    if len(target_skus) != 1 or not next(iter(target_skus)).isdigit():
        raise ShopeeRecoveryReconciliationError("reportless recovery SKU identity is invalid")
    sku = next(iter(target_skus))
    expected_resources = [f'product:["{label}","{sku}"]'
                          for label in checked["new_manifest"]["target_scope"]]
    operation = closure.get("operation")
    if (set(closure) != {"schema_version", "closure_digest", "observed_at", "operation",
                         "receipt", "receipt_digest", "remaining_lock_count", "run_id"}
            or closure.get("schema_version") != "shopee-reportless-recovery-domain-closure/v1"
            or closure_digest != _digest(closure_body)
            or closure.get("run_id") != checked["attempt"]["run_id"]
            or closure.get("receipt_digest") != checked["receipt_digest"]
            or closure.get("remaining_lock_count") != 0
            or not isinstance(operation, Mapping) or operation.get("state") != "completed"
            or json.loads(operation.get("resources_json") or "null") != expected_resources
            or not str(operation.get("readback_ref") or "").startswith(
                "shopee-reportless-recovery-reconciliation:"
                + checked["receipt_digest"].removeprefix("sha256:") + ":REMAINING_DIFF:")):
        raise ShopeeRecoveryReconciliationError("reportless recovery closure evidence conflicts")
    receipt_source, safe_receipt_source = _load_ref(closure.get("receipt"), allowed_evidence_roots)
    if receipt_source != checked:
        raise ShopeeRecoveryReconciliationError("reportless recovery closure receipt conflicts")
    database = _safe_root(operations_db, allowed_evidence_roots)
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True); connection.row_factory = sqlite3.Row
    try:
        current = connection.execute(
            "SELECT * FROM workbench_domain_operations WHERE operation_id=?",
            (operation["operation_id"],),).fetchone()
        locks = connection.execute(
            "SELECT COUNT(*) FROM workbench_domain_locks WHERE operation_id=?",
            (operation["operation_id"],),).fetchone()[0]
    finally:
        connection.close()
    if current is None or dict(current) != dict(operation) or locks != 0:
        raise ShopeeRecoveryReconciliationError("reportless recovery domain closure drifted")
    continuation_body = dict(continuation); continuation_digest = continuation_body.pop("continuation_digest", None)
    expected_continuation = {
        "schema_version": "shopee-reportless-recovery-continuation/v1",
        "source_receipt_digest": checked["receipt_digest"], **checked["new_manifest"],
    }
    if continuation_digest != _digest(continuation_body) or continuation_body != expected_continuation:
        raise ShopeeRecoveryReconciliationError("reportless recovery continuation conflicts")

    expected_authority_keys = {
        "schema_version", "authority_digest", "authorized_method", "closure", "continuation",
        "expected_report_id", "immutable_report_present", "manifest", "manifest_digest",
        "observed_at", "ordered_events", "product_writes", "run",
    }
    authority_body = dict(authority); authority_digest = authority_body.pop("authority_digest", None)
    if (set(authority) != expected_authority_keys
            or authority.get("schema_version") != "shopee-get-only-collector-authority/v1"
            or authority_digest != _digest(authority_body)
            or authority.get("authorized_method") != "GET"
            or authority.get("closure") != {"path": safe_closure["path"],
                "sha256": safe_closure["sha256"], "digest": closure_digest,
                "observed_at": closure["observed_at"]}
            or authority.get("continuation") != {"path": safe_continuation["path"],
                "sha256": safe_continuation["sha256"], "digest": continuation_digest}
            or authority.get("run") != dict(run_store.get_run_by_id(run_id=checked["attempt"]["run_id"]))
            or authority.get("ordered_events") != [{key: row[key] for key in (
                "sequence", "state", "final_report_id", "failure_code", "created_at", "event_digest")}
                for row in checked["run_events"]]
            or authority.get("manifest_digest") != checked["manifest_digest"]
            or authority.get("expected_report_id") != checked["attempt"]["report_id"]
            or authority.get("immutable_report_present") is not False
            or authority.get("product_writes") != 0
            or authority.get("observed_at") != readback.get("observed_at")):
        raise ShopeeRecoveryReconciliationError("fresh continuation collector authority conflicts")

    manifest, safe_manifest = _load_ref(authority.get("manifest"), allowed_evidence_roots)
    if manifest.get("manifest_digest") != checked["manifest_digest"]:
        raise ShopeeRecoveryReconciliationError("fresh continuation manifest conflicts")
    labels, targets = _manifest_targets(manifest)
    if (readback.get("closure_digest") != closure_digest
            or readback.get("closure_observed_at") != closure.get("observed_at")
            or readback.get("continuation_digest") != continuation_digest):
        raise ShopeeRecoveryReconciliationError("fresh continuation readback lineage conflicts")
    identity = {"run_id": checked["attempt"]["run_id"], "report_id": checked["attempt"]["report_id"],
                "ambiguity_at": closure["observed_at"]}
    writes = [{"target_label": label, "outcome_unknown": True} for label in labels]
    normalized = _validate_direct_readback(
        readback, identity=identity, manifest=manifest, labels=labels,
        manifest_targets=targets, write_counts=writes,
        allowed_evidence_roots=allowed_evidence_roots,
        allow_unobservable_global_model_status=True,
        evidence_resolver=evidence_resolver,
    )
    observed = datetime.fromisoformat(str(readback["observed_at"]).replace("Z", "+00:00"))
    if observed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ShopeeRecoveryReconciliationError("fresh continuation readback is future-dated")
    remaining = [{"target_label": row["target_label"], "differences": row["differences"]}
                 for row in normalized]
    if (remaining != checked["new_manifest"]["exact_remaining_differences"]
            or any(row["classification"] != "DIFF" for row in normalized)):
        raise ShopeeRecoveryReconciliationError("fresh continuation differences drifted")
    labels = checked["new_manifest"]["target_scope"]
    core = {
        "schema_version": "shopee-reportless-recovery-continuation-preflight/v1",
        "status": "READY_ZERO_WRITE_PREFLIGHT", "authorized": True,
        "authorization_basis": "ORIGINAL_EXACT_HUMAN_APPROVAL_REUSE",
        "offer_id": checked["authority_identity"]["offer_id"],
        "plan_id": checked["authority_identity"]["plan_id"],
        "execution_snapshot_digest": checked["authority_identity"]["snapshot_digest"],
        "candidate_digest": manifest["candidate_digest"], "approval_digest": manifest["approval_digest"],
        "source_receipt_digest": checked["receipt_digest"], "closure_digest": closure_digest,
        "unauthorized_continuation_digest": continuation_digest,
        "target_scope": list(labels), "exact_remaining_differences": remaining,
        "allowed_actions": deepcopy(checked["new_manifest"]["allowed_actions"]),
        "mutation_budgets": deepcopy(checked["new_manifest"]["mutation_budgets"]),
        "forbidden_actions": deepcopy(checked["new_manifest"]["forbidden_actions"]),
        "zero_write_preflight": {"completed": True, "external_write_count": 0,
                                  "observed_at": readback["observed_at"]},
        "fresh_official_readback": {"evidence_digest": readback["evidence_digest"],
                                    "targets": normalized},
        "source_refs": {"receipt": safe_receipt_source, "closure": safe_closure,
                        "unauthorized_continuation": safe_continuation,
                        "manifest": safe_manifest, "fresh_readback": safe_readback,
                        "collector_authority": safe_authority},
    }
    return {**core, "preflight_digest": _digest(core)}


def validate_reportless_continuation_preflight(
    value: Mapping[str, Any], *, receipt: Mapping[str, Any], run_store: object,
    report_store: object, snapshot: Mapping[str, Any], candidate: Mapping[str, Any],
    approval: Mapping[str, Any], operations_db: str | Path,
    allowed_evidence_roots: Sequence[str | Path],
    evidence_resolver=None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ShopeeRecoveryReconciliationError("continuation preflight is invalid")
    checked = deepcopy(dict(value)); supplied = checked.pop("preflight_digest", None)
    if supplied != _digest(checked):
        raise ShopeeRecoveryReconciliationError("continuation preflight digest conflicts")
    refs = checked.get("source_refs")
    if not isinstance(refs, Mapping) or set(refs) != {
        "receipt", "closure", "unauthorized_continuation", "manifest",
        "fresh_readback", "collector_authority",
    }:
        raise ShopeeRecoveryReconciliationError("continuation preflight sources conflict")
    rebuilt = build_reportless_continuation_preflight(
        receipt=receipt, run_store=run_store, report_store=report_store,
        snapshot=snapshot, candidate=candidate, approval=approval,
        closure_ref=refs["closure"], continuation_ref=refs["unauthorized_continuation"],
        fresh_readback_ref=refs["fresh_readback"],
        fresh_authority_ref=refs["collector_authority"], operations_db=operations_db,
        allowed_evidence_roots=allowed_evidence_roots,
        evidence_resolver=evidence_resolver,
    )
    result = {**checked, "preflight_digest": supplied}
    if rebuilt != result:
        raise ShopeeRecoveryReconciliationError("continuation preflight semantics conflict")
    return result


def build_reportless_continuation_manifest(
    *, preflight_ref: Mapping[str, str], receipt: Mapping[str, Any], run_store: object,
    report_store: object, snapshot: Mapping[str, Any], candidate: Mapping[str, Any],
    approval: Mapping[str, Any], operations_db: str | Path,
    allowed_evidence_roots: Sequence[str | Path],
    evidence_resolver=None,
    evidence_relocation_ref: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    preflight, safe_preflight = _load_ref(
        preflight_ref, allowed_evidence_roots, evidence_resolver)
    source_manifest_ref = (preflight.get("source_refs") or {}).get("manifest")
    old_manifest, safe_old_manifest = _load_ref(
        source_manifest_ref, allowed_evidence_roots, evidence_resolver)
    safe_relocation = None
    if evidence_relocation_ref is not None:
        relocation, safe_relocation = _load_ref(
            evidence_relocation_ref, allowed_evidence_roots)
        evidence_resolver = _reportless_relocation_resolver(
            attestation_ref=safe_relocation, receipt=receipt, preflight=preflight,
            original_manifest=old_manifest, roots=allowed_evidence_roots,
        )
    checked = validate_reportless_continuation_preflight(
        preflight, receipt=receipt, run_store=run_store, report_store=report_store,
        snapshot=snapshot, candidate=candidate, approval=approval,
        operations_db=operations_db, allowed_evidence_roots=allowed_evidence_roots,
        evidence_resolver=evidence_resolver,
    )
    labels = checked["target_scope"]
    old_targets = {row["target_label"]: row for row in old_manifest.get("targets") or []}
    if set(old_targets) != set(labels):
        raise ShopeeRecoveryReconciliationError("continuation target facts are incomplete")
    targets = []
    for label in labels:
        row = deepcopy(old_targets[label])
        row["allowed_actions"] = list(checked["allowed_actions"][label])
        row["mutation_budget"] = {"shared_maximum": 0, "target_maximum": 2}
        targets.append(row)
    continuation = {
        "schema_version": "shopee-recovery-continuation/v1",
        "receipt_digest": checked["preflight_digest"], "target_scope": list(labels),
        "exact_remaining_differences": deepcopy(checked["exact_remaining_differences"]),
        "action_budgets": deepcopy(checked["allowed_actions"]),
    }
    core = {
        "schema_version": "shopee-reportless-recovery-execution-manifest/v1",
        "status": "READY_ZERO_WRITE_PREFLIGHT", "authorized": True,
        "offer_id": checked["offer_id"], "product_revision": old_manifest["product_revision"],
        "plan_id": checked["plan_id"],
        "business_snapshot_digest": old_manifest["business_snapshot_digest"],
        "execution_snapshot_digest": checked["execution_snapshot_digest"],
        "candidate_digest": checked["candidate_digest"],
        "approval_digest": checked["approval_digest"],
        "direct_predecessor_run_id": receipt["attempt"]["run_id"],
        "original_publication_run_id": old_manifest["prior_run_id"],
        "source_receipt_digest": checked["source_receipt_digest"],
        "preflight_digest": checked["preflight_digest"],
        "target_labels": list(labels), "targets": targets,
        "continuation": continuation,
        "forbidden_operations": ["create_publish_task", "upload_image", "global_mutation",
                                 "update_images_existing_media", "update_price",
                                 "update_category", "update_logistics"],
        "zero_write_preflight": deepcopy(checked["zero_write_preflight"]),
        "source_refs": {"preflight": safe_preflight, "original_manifest": safe_old_manifest},
    }
    if safe_relocation is not None:
        core["evidence_relocation_digest"] = relocation["attestation_digest"]
        core["source_refs"]["evidence_relocation"] = safe_relocation
    return {**core, "manifest_digest": _digest(core)}


def validate_reportless_continuation_manifest(
    value: Mapping[str, Any], *, receipt: Mapping[str, Any], run_store: object,
    report_store: object, snapshot: Mapping[str, Any], candidate: Mapping[str, Any],
    approval: Mapping[str, Any], operations_db: str | Path,
    allowed_evidence_roots: Sequence[str | Path],
    evidence_resolver=None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ShopeeRecoveryReconciliationError("continuation execution manifest is invalid")
    checked = deepcopy(dict(value)); supplied = checked.pop("manifest_digest", None)
    if supplied != _digest(checked):
        raise ShopeeRecoveryReconciliationError("continuation execution manifest digest conflicts")
    refs = checked.get("source_refs")
    if not isinstance(refs, Mapping) or set(refs) not in (
        {"preflight", "original_manifest"},
        {"preflight", "original_manifest", "evidence_relocation"},
    ):
        raise ShopeeRecoveryReconciliationError("continuation execution sources conflict")
    relocation_ref = refs.get("evidence_relocation")
    if relocation_ref is not None:
        preflight, _ = _load_ref(refs["preflight"], allowed_evidence_roots)
        old_manifest, _ = _load_ref(refs["original_manifest"], allowed_evidence_roots)
        evidence_resolver = _reportless_relocation_resolver(
            attestation_ref=relocation_ref, receipt=receipt, preflight=preflight,
            original_manifest=old_manifest, roots=allowed_evidence_roots,
        )
    rebuilt = build_reportless_continuation_manifest(
        preflight_ref=refs["preflight"], receipt=receipt, run_store=run_store,
        report_store=report_store, snapshot=snapshot, candidate=candidate, approval=approval,
        operations_db=operations_db, allowed_evidence_roots=allowed_evidence_roots,
        evidence_resolver=evidence_resolver,
        evidence_relocation_ref=relocation_ref,
    )
    result = {**checked, "manifest_digest": supplied}
    if rebuilt != result:
        raise ShopeeRecoveryReconciliationError("continuation execution manifest semantics conflict")
    return result


__all__ = ["SCHEMA_VERSION", "ShopeeReportlessRecoveryReconciliationStore",
           "build_reportless_reconciliation_receipt",
           "build_reportless_continuation_preflight",
           "build_reportless_continuation_manifest",
           "validate_reportless_continuation_preflight",
           "validate_reportless_continuation_manifest",
           "validate_reportless_reconciliation_receipt"]
