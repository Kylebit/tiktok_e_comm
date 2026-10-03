"""Append-only closure authority for a known zero-dispatch Shopee recovery run."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping

SCHEMA_VERSION = "shopee-known-zero-recovery-reconciliation/v1"


class ShopeeKnownZeroRecoveryError(ValueError):
    pass


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def build_known_zero_receipt(*, run_id: str, manifest: Mapping[str, Any],
                             run_store: object, report_store: object,
                             manifest_validator: Callable[[Mapping[str, Any]], Mapping[str, Any]],
                             registered_by: str) -> dict[str, Any]:
    """Rebuild all authority from the durable run/report and deep manifest gate."""
    checked_manifest = manifest_validator(manifest)
    if checked_manifest != dict(manifest):
        raise ShopeeKnownZeroRecoveryError("recovery manifest failed deep validation")
    if checked_manifest.get("schema_version") != "shopee-reportless-recovery-execution-manifest/v1":
        raise ShopeeKnownZeroRecoveryError("recovery manifest schema conflicts")
    run = run_store.get_run_by_id(run_id=run_id)
    report = report_store.get_report_by_run(run_id=run_id)
    if not isinstance(run, Mapping) or not isinstance(report, Mapping):
        raise ShopeeKnownZeroRecoveryError("durable run or report is unavailable")
    labels = checked_manifest.get("target_labels")
    request_identity = {
        "kind": "SHOPEE_RECOVERY_CONTINUATION",
        "authority_digest": checked_manifest["manifest_digest"],
        "predecessor_run_id": checked_manifest["direct_predecessor_run_id"],
        "source_receipt_digest": checked_manifest["source_receipt_digest"],
        "preflight_digest": checked_manifest["preflight_digest"],
        "evidence_relocation_digest": checked_manifest["evidence_relocation_digest"],
    }
    expected_report_id = "publication-report:" + run_id
    if (run.get("state") != "COMPLETED" or run.get("final_report_id") != expected_report_id
            or run.get("report_id") != expected_report_id
            or run.get("offer_id") != checked_manifest.get("offer_id")
            or run.get("plan_id") != checked_manifest.get("plan_id")
            or run.get("snapshot_digest") != checked_manifest.get("execution_snapshot_digest")
            or run.get("target_count") != len(labels or [])
            or run.get("request_identity") != request_identity):
        raise ShopeeKnownZeroRecoveryError("durable recovery run identity conflicts")
    if (report.get("schema_version") != "product-publication-report/v2"
            or report.get("report_id") != expected_report_id
            or report.get("offer_id") != checked_manifest.get("offer_id")
            or report.get("plan_id") != checked_manifest.get("plan_id")
            or (report.get("snapshot") or {}).get("digest") != checked_manifest.get("execution_snapshot_digest")
            or report.get("status") != "FAILED"
            or report.get("recovery_authorization") != checked_manifest
            or [row.get("target_label") for row in report.get("targets") or []] != labels):
        raise ShopeeKnownZeroRecoveryError("immutable recovery report identity conflicts")
    summary_evidence = (report.get("summary") or {}).get("evidence") or {}
    summary = report.get("summary") or {}
    platforms = summary.get("platforms") or []
    if (summary.get("overall_status") != "FAILED"
            or summary_evidence.get("dispatch_attempted") is not False
            or summary_evidence.get("external_write_count") != 0
            or summary_evidence.get("snapshot_verified") is not True
            or summary_evidence.get("readback_completed") is not True
            or platforms != [{"platform":"SHOPEE","status":"FAILED","target_count":len(labels),
                              "verified_count":0,"processing_count":0,"failed_count":len(labels)}]):
        raise ShopeeKnownZeroRecoveryError("report does not prove zero dispatch")
    for row in report["targets"]:
        evidence = row.get("evidence") or {}
        if (row.get("status") != "FAILED" or evidence.get("status") != "FAILED"
                or evidence.get("target_label") != row.get("target_label")
                or evidence.get("provider_identity_bound") is not True
                or evidence.get("stage") != "PREFLIGHT"
                or evidence.get("request_attempted") is not False
                or evidence.get("outcome_unknown") is not False
                or evidence.get("external_write_count") != 0
                or evidence.get("provider_code") != "shopee_recovery_preflight_failed"):
            raise ShopeeKnownZeroRecoveryError("target does not prove known zero-dispatch preflight failure")
    budgets = report.get("mutation_budgets") or []
    if (len(budgets) != 1 or budgets[0].get("platform") != "SHOPEE"
            or budgets[0].get("reservations") != []
            or (budgets[0].get("attempts") or {}).get("total") != 0
            or (budgets[0].get("attempts") or {}).get("shared") != 0
            or (budgets[0].get("attempts") or {}).get("per_target") != {label: 0 for label in labels}):
        raise ShopeeKnownZeroRecoveryError("mutation ledger does not prove zero writes")
    with sqlite3.connect(Path(run_store.path).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        events = conn.execute("SELECT sequence,state,final_report_id,failure_code,event_digest FROM product_publication_run_events WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
        report_row = conn.execute("SELECT report_digest,envelope_digest FROM product_publication_reports WHERE run_id=?", (run_id,)).fetchone()
    if ([(row["sequence"], row["state"], row["final_report_id"], row["failure_code"]) for row in events]
            != [(1, "QUEUED", None, None), (2, "RUNNING", None, None),
                (3, "COMPLETED", expected_report_id, None)] or report_row is None):
        raise ShopeeKnownZeroRecoveryError("durable recovery event chain conflicts")
    if type(registered_by) is not str or not registered_by.strip() or len(registered_by) > 128:
        raise ShopeeKnownZeroRecoveryError("registered_by is invalid")
    core = {
        "schema_version": SCHEMA_VERSION, "result": "KNOWN_ZERO_PREFLIGHT",
        "attempt_closed": True, "mutation_lock": False,
        "provider_mutation_dispatch_attempted": False, "external_write_count": 0,
        "run_identity": {"run_id": run_id, "report_id": expected_report_id,
                         "offer_id": run["offer_id"], "plan_id": run["plan_id"],
                         "snapshot_digest": run["snapshot_digest"],
                         "request_identity": request_identity,
                         "ordered_event_digests": [row["event_digest"] for row in events],
                         "terminal_event_digest": events[-1]["event_digest"],
                         "report_digest": report_row["report_digest"],
                         "report_envelope_digest": report_row["envelope_digest"]},
        "manifest": deepcopy(checked_manifest),
        "new_manifest": {"authorized": False, "fresh_exact_preflight_required": True,
                         "target_scope": list(labels),
                         "exact_remaining_differences": deepcopy(checked_manifest["continuation"]["exact_remaining_differences"]),
                         "action_budgets": deepcopy(checked_manifest["continuation"]["action_budgets"])},
        "registered_by": registered_by.strip(),
    }
    return {**core, "receipt_digest": _digest(core)}


def validate_known_zero_receipt(value: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ShopeeKnownZeroRecoveryError("known-zero receipt is invalid")
    checked = deepcopy(dict(value)); supplied = checked.pop("receipt_digest", None)
    if supplied != _digest(checked) or checked.get("schema_version") != SCHEMA_VERSION:
        raise ShopeeKnownZeroRecoveryError("known-zero receipt digest conflicts")
    rebuilt = build_known_zero_receipt(
        run_id=checked["run_identity"]["run_id"], manifest=checked["manifest"],
        registered_by=checked["registered_by"], **kwargs)
    result = {**checked, "receipt_digest": supplied}
    if rebuilt != result:
        raise ShopeeKnownZeroRecoveryError("known-zero receipt facts conflict")
    return result


class ShopeeKnownZeroRecoveryStore:
    def __init__(self, root: str | Path, *, allowed_root: str | Path, **validation: Any) -> None:
        from shared_platform.immutable_approval_files import require_local_path
        self.root = Path(root); self.allowed_root = Path(allowed_root)
        require_local_path(self.root, root=self.allowed_root, allow_directory=True)
        self.validation = validation

    def _path(self, run_id: str) -> Path:
        return self.root / hashlib.sha256(run_id.encode()).hexdigest() / "receipt.json"

    def register(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        from shared_platform.immutable_approval_files import (
            persist_immutable_bytes, require_local_path,
        )
        checked = validate_known_zero_receipt(receipt, **self.validation)
        self.root.mkdir(parents=True, exist_ok=True)
        require_local_path(self.root, root=self.allowed_root, allow_directory=True)
        path = self._path(checked["run_identity"]["run_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        require_local_path(path.parent, root=self.allowed_root, allow_directory=True)
        require_local_path(path, root=self.allowed_root)
        encoded = (_canonical(checked) + "\n").encode()
        try:
            persist_immutable_bytes(path, encoded, root=self.allowed_root)
        except ValueError as error:
            raise ShopeeKnownZeroRecoveryError(
                "append-only known-zero receipt conflicts") from error
        stored = self.get(run_id=checked["run_identity"]["run_id"])
        if stored != checked:
            raise ShopeeKnownZeroRecoveryError("append-only known-zero receipt conflicts")
        return stored

    def get(self, *, run_id: str) -> dict[str, Any] | None:
        from shared_platform.immutable_approval_files import require_local_path
        path = self._path(run_id)
        require_local_path(path, root=self.allowed_root)
        if not path.is_file(): return None
        return validate_known_zero_receipt(json.loads(path.read_text(encoding="utf-8")), **self.validation)


__all__ = ["SCHEMA_VERSION", "ShopeeKnownZeroRecoveryError", "ShopeeKnownZeroRecoveryStore",
           "build_known_zero_receipt", "validate_known_zero_receipt"]
