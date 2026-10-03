"""GET-only coordination for an ambiguous Shopee regional recovery attempt.

This module deliberately does not authorize or execute a continuation.  It
loads the immutable attempt and manifest from disk, captures direct-ID facts,
registers the append-only reconciliation receipt, and returns the small gate
which the claim and domain guards can bind.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from shared_platform.shopee_recovery_reconciliations import (
    READBACK_SCHEMA_VERSION,
    ShopeeRecoveryReconciliationError,
    ShopeeRecoveryReconciliationStore,
    build_reconciliation_receipt,
    reconciliation_gate,
)


_RESOURCES = ("item", "models", "global_linkage", "global_item", "global_models")


class OfficialShopeeRecoveryGetter:
    """Five-method official adapter.  Every provider operation is a GET."""

    def __init__(self, manifest: Mapping[str, Any], runtime: object) -> None:
        self.runtime = runtime
        self.targets = {str(row["shop_id"]): dict(row) for row in manifest.get("targets") or []}
        self.contexts = {
            shop_id: runtime.context(str(row["region"])) for shop_id, row in self.targets.items()
        }
        if set(self.contexts) != set(self.targets) or any(
            str(getattr(self.contexts[key], "shop_id", "")) != key for key in self.targets
        ):
            raise ShopeeRecoveryReconciliationError("official Shopee context conflicts with frozen shops")

    def __call__(self, resource: str, query: Mapping[str, str]) -> object:
        target = self.targets.get(query["shop_id"])
        context = self.contexts.get(query["shop_id"])
        if target is None or context is None:
            raise ShopeeRecoveryReconciliationError("official GET shop is outside frozen scope")
        exact = {key: str(target.get(key) or "") for key in (
            "shop_id", "item_id", "global_item_id", "model_id", "global_model_id", "model_sku"
        )}
        if dict(query) != exact:
            raise ShopeeRecoveryReconciliationError("official GET identity conflicts with frozen target")
        if resource == "item":
            row = self.runtime.regional_item(context, query["item_id"])
            if not isinstance(row, Mapping):
                return None
            from modules.shopee.skill_regions import (
                _official_image_ids, shopee_description_image_ids, shopee_description_text,
            )
            logistics = row.get("logistic_info") or []
            return {
                "shop_id": query["shop_id"], "item_id": str(row.get("item_id") or ""),
                "status": str(row.get("item_status") or row.get("status") or ""),
                "category_id": str(row.get("category_id") or ""),
                "title": str(row.get("item_name") or ""),
                "description": shopee_description_text(row),
                "description_type": str(row.get("description_type") or ""),
                "gallery_image_ids": list(_official_image_ids(row)),
                "description_image_ids": list(shopee_description_image_ids(row)),
                "enabled_logistics_ids": sorted(int(value["logistic_id"]) for value in logistics
                    if isinstance(value, Mapping) and value.get("enabled") is True
                    and type(value.get("logistic_id")) is int and value["logistic_id"] > 0),
            }
        if resource == "models":
            rows = self.runtime.regional_models(context, query["item_id"])
            result = []
            for row in rows:
                prices = [value for value in row.get("price_info") or [] if isinstance(value, Mapping)]
                expected_currency = str(target["local_original_price"]["currency"]).upper()
                prices = [value for value in prices if str(value.get("currency") or "").upper() == expected_currency]
                result.append({
                    "shop_id": query["shop_id"], "item_id": query["item_id"],
                    "model_id": str(row.get("model_id") or ""), "model_sku": str(row.get("model_sku") or ""),
                    "status": str(row.get("model_status") or row.get("status") or ""),
                    "price": str(prices[0].get("original_price") or "") if len(prices) == 1 else "",
                    "currency": expected_currency if len(prices) == 1 else "",
                })
            return result
        if resource == "global_linkage":
            return {"shop_id": query["shop_id"], "item_id": query["item_id"],
                    "global_item_id": self.runtime.resolved_global_item_id(context, query["item_id"])}
        if resource == "global_models":
            rows = self.runtime.global_models(context, query["global_item_id"])
            return [{"global_item_id": query["global_item_id"],
                     "global_model_id": str(row.get("global_model_id") or ""),
                     "model_sku": str(row.get("global_model_sku") or row.get("model_sku") or ""),
                     "status": str(row.get("global_model_status") or row.get("status") or "")}
                    for row in rows]
        if resource == "global_item":
            from modules.shopee.client import merchant_get
            response = merchant_get(
                "/api/v2/global_product/get_global_item_info", context.merchant_id,
                context.merchant_token, {"global_item_id_list": query["global_item_id"]},
            )
            if response.get("error") or response.get("message"):
                raise RuntimeError("official global item GET failed")
            rows = (response.get("response") or {}).get("global_item_list") or []
            matches = [row for row in rows if isinstance(row, Mapping)
                       and str(row.get("global_item_id") or "") == query["global_item_id"]]
            if len(matches) != 1:
                return None
            return {"global_item_id": query["global_item_id"],
                    "status": str(matches[0].get("global_item_status") or matches[0].get("status") or "")}
        raise ShopeeRecoveryReconciliationError("unsupported official GET resource")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _file_meta(path: Path) -> dict[str, str]:
    raw = path.read_bytes()
    return {"path": str(path), "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()}


def _load_mapping(path: str | Path, *, roots: Sequence[str | Path], name: str) -> tuple[dict, Path]:
    candidate = Path(path).resolve(strict=True)
    allowed = [Path(root).resolve(strict=True) for root in roots]
    if not candidate.is_file() or candidate.suffix.lower() != ".json" or not any(
        candidate == root or root in candidate.parents for root in allowed
    ):
        raise ShopeeRecoveryReconciliationError(f"{name} source is outside allowed evidence roots")
    try:
        value = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ShopeeRecoveryReconciliationError(f"{name} source is invalid") from error
    if not isinstance(value, Mapping):
        raise ShopeeRecoveryReconciliationError(f"{name} source is invalid")
    return dict(value), candidate


def _output_root(path: str | Path, *, roots: Sequence[str | Path], name: str) -> Path:
    candidate = Path(path).resolve()
    allowed = [Path(root).resolve(strict=True) for root in roots]
    if not any(candidate == root or root in candidate.parents for root in allowed):
        raise ShopeeRecoveryReconciliationError(f"{name} is outside allowed evidence roots")
    return candidate


def _immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (_canonical(value) + "\n").encode("utf-8")
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ShopeeRecoveryReconciliationError("immutable GET evidence file conflicts")


def collect_direct_id_readback(
    *, attempt: Mapping[str, Any], manifest: Mapping[str, Any], evidence_root: str | Path,
    getter: Callable[[str, Mapping[str, str]], object], observed_at: str | None = None,
    manifest_validator: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    allowed_evidence_roots: Sequence[str | Path],
) -> dict[str, Any]:
    """Capture exactly five GET facts per frozen target.

    ``getter`` is intentionally one narrow seam.  It receives only a resource
    name and the complete frozen direct-ID query and must return the normalized
    body derived from that GET.  Any exception or ``None`` becomes incomplete
    evidence; the collector never retries and never calls a mutation method.
    """
    checked_manifest = manifest_validator(manifest)
    if dict(checked_manifest) != dict(manifest):
        raise ShopeeRecoveryReconciliationError("deep manifest validation changed immutable facts")
    labels = checked_manifest.get("target_labels")
    targets = checked_manifest.get("targets")
    if not isinstance(labels, list) or not isinstance(targets, list) or [
        row.get("target_label") for row in targets if isinstance(row, Mapping)
    ] != labels:
        raise ShopeeRecoveryReconciliationError("recovery manifest targets are invalid")
    run_id = str(attempt.get("run_id") or "")
    report_id = str(attempt.get("report_id") or "")
    if not run_id or not report_id or attempt.get("recovery_authorization") != dict(manifest):
        raise ShopeeRecoveryReconciliationError("attempt does not bind the recovery manifest")
    when = observed_at or datetime.now(timezone.utc).isoformat()
    short = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]
    observation = hashlib.sha256(when.encode("utf-8")).hexdigest()[:12]
    root = _output_root(evidence_root, roots=allowed_evidence_roots, name="GET evidence root")
    rows: list[dict[str, Any]] = []
    for index, frozen in enumerate(targets):
        if not isinstance(frozen, Mapping):
            raise ShopeeRecoveryReconciliationError("recovery manifest target is invalid")
        query = {key: str(frozen.get(key) or "") for key in (
            "shop_id", "item_id", "global_item_id", "model_id", "global_model_id", "model_sku"
        )}
        if any(not value for value in query.values()):
            raise ShopeeRecoveryReconciliationError("recovery direct-ID identity is incomplete")
        reads: dict[str, dict[str, Any]] = {}
        for resource in _RESOURCES:
            complete = True
            try:
                response = getter(resource, deepcopy(query))
                if response is None:
                    complete = False
            except Exception:
                response = None
                complete = False
            envelope = {
                "schema_version": "shopee-official-direct-id-get-evidence/v1",
                "request": {"method": "GET", "mode": "DIRECT_ID", "resource": resource, **query},
                "complete": complete,
                "observed_at": when,
                "raw_response": response if complete else None,
            }
            path = root / "sr" / short / observation / f"{index}-{resource}.json"
            _immutable_json(path, envelope)
            meta = _file_meta(path)
            reads[resource] = {"complete": complete, "response_ref": meta["path"],
                               "response_digest": meta["sha256"], "observed_at": when}
        row = {"target_label": labels[index], "query": {"method": "GET", "mode": "DIRECT_ID", **query},
               "reads": reads}
        row["target_evidence_digest"] = _digest(row)
        rows.append(row)
    result = {
        "schema_version": READBACK_SCHEMA_VERSION, "authority": "SHOPEE_OFFICIAL_API",
        "run_id": run_id, "report_id": report_id,
        "manifest_digest": manifest.get("manifest_digest"), "observed_at": when,
        "product_writes": 0, "targets": rows,
    }
    result["evidence_digest"] = _digest(result)
    return result


def reconcile_recovery_attempt(
    *, attempt_path: str | Path, manifest_path: str | Path,
    evidence_root: str | Path, receipt_root: str | Path,
    allowed_evidence_roots: Sequence[str | Path],
    manifest_validator: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    getter: Callable[[str, Mapping[str, str]], object], observed_at: str | None = None,
) -> dict[str, Any]:
    """Load durable sources, collect GET facts and register one receipt."""
    attempt, safe_attempt = _load_mapping(attempt_path, roots=allowed_evidence_roots, name="attempt")
    manifest, safe_manifest = _load_mapping(manifest_path, roots=allowed_evidence_roots, name="manifest")
    _output_root(receipt_root, roots=allowed_evidence_roots, name="receipt root")
    readback = collect_direct_id_readback(
        attempt=attempt, manifest=manifest, evidence_root=evidence_root,
        getter=getter, observed_at=observed_at, manifest_validator=manifest_validator,
        allowed_evidence_roots=allowed_evidence_roots,
    )
    receipt = build_reconciliation_receipt(
        attempt=attempt, manifest=manifest, recovery_authorization=manifest,
        official_readback=readback,
        source_files={"attempt": _file_meta(safe_attempt), "manifest": _file_meta(safe_manifest)},
        allowed_evidence_roots=allowed_evidence_roots,
    )
    stored = ShopeeRecoveryReconciliationStore(
        receipt_root, allowed_evidence_roots=allowed_evidence_roots,
    ).register(receipt)
    reconciliation_gate(
        stored, run_id=attempt["run_id"], report_id=attempt["report_id"],
        manifest_digest=manifest["manifest_digest"],
        allowed_evidence_roots=allowed_evidence_roots,
    )
    return stored


def continuation_identity(receipt: Mapping[str, Any], *, run_id: str, report_id: str,
                          manifest_digest: str, allowed_evidence_roots: Sequence[str | Path],
                          target_scope: Sequence[str],
                          action_budgets: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    """Bind a fresh continuation to the terminal receipt and exact remaining work."""
    gate = reconciliation_gate(receipt, run_id=run_id, report_id=report_id,
                               manifest_digest=manifest_digest,
                               allowed_evidence_roots=allowed_evidence_roots)
    if gate.get("result") != "REMAINING_DIFF" or gate.get("attempt_closed") is not True \
            or gate.get("mutation_lock") is not False:
        raise ShopeeRecoveryReconciliationError("recovery continuation requires terminal remaining-diff evidence")
    new = gate.get("new_manifest")
    expected = new.get("target_scope") if isinstance(new, Mapping) else None
    remaining = new.get("exact_remaining_differences") if isinstance(new, Mapping) else None
    labels = list(target_scope)
    if labels != expected or not isinstance(remaining, list) or set(action_budgets) != set(labels):
        raise ShopeeRecoveryReconciliationError("recovery continuation scope conflicts")
    for row in remaining:
        label = row.get("target_label") if isinstance(row, Mapping) else None
        differences = row.get("differences") if isinstance(row, Mapping) else None
        difference_set = set(differences or [])
        understood = {"copy.title", "copy.description", "description.type", "description.images",
                      "gallery.images", "item.status"}
        required: set[str] = set()
        if difference_set - understood:
            required.add("")
        if difference_set & {"copy.title", "copy.description"}:
            required.add("update_copy_and_description_media")
        elif difference_set & {"description.type", "description.images"}:
            required.add("update_description_media")
        if "gallery.images" in difference_set:
            required.add("update_images_existing_media")
        if "item.status" in difference_set:
            required.add("list_existing_item")
        if not label or "" in required or sorted(action_budgets[label]) != sorted(required):
            raise ShopeeRecoveryReconciliationError("recovery continuation actions exceed exact differences")
    return {"schema_version": "shopee-recovery-continuation/v1",
            "receipt_digest": gate["receipt_digest"], "target_scope": labels,
            "exact_remaining_differences": deepcopy(remaining),
            "action_budgets": {label: list(action_budgets[label]) for label in labels}}


__all__ = ["OfficialShopeeRecoveryGetter", "collect_direct_id_readback", "continuation_identity",
           "reconcile_recovery_attempt"]
