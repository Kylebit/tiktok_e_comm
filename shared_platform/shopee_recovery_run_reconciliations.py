"""Immutable proof that a Shopee recovery run failed before provider access.

Unlike the generic publication zero-write receipt, a recovery retry must not
prove that the product is absent.  It proves that the exact known provider
baseline is unchanged across the failed run instead.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping, Sequence

from shared_platform.product_publication_runs import (
    DEFAULT_PRODUCT_PUBLICATION_RUN_DB,
    ProductPublicationRunIntegrityError,
    ProductPublicationRunStore,
)


SCHEMA_VERSION = "shopee-recovery-run-zero-write-reconciliation/v1"
BASELINE_SCHEMA_VERSION = "shopee-recovery-provider-baseline/v1"
_MANIFEST_SCHEMA_VERSION = "shopee-regional-recovery/v1"
_AUTHORITY = "SHOPEE_OFFICIAL_API"
_FAILURES = frozenset({"RUNNER_INFRASTRUCTURE_FAILED", "WORKER_LAUNCH_FAILED"})
_RECOVERY_LABELS = ("shopee:MY", "shopee:TH", "shopee:VN")
_ALL_LABELS = ("shopee:PH", "shopee:MY", "shopee:TH", "shopee:VN")
_EXPECTED_STATUS = {
    "shopee:PH": "NORMAL",
    "shopee:MY": "UNLIST",
    "shopee:TH": "UNLIST",
    "shopee:VN": "UNLIST",
}
_HEX = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS shopee_recovery_run_zero_write_reconciliations (
 run_id TEXT PRIMARY KEY,
 receipt_json TEXT NOT NULL,
 receipt_digest TEXT NOT NULL,
 created_at TEXT NOT NULL,
 FOREIGN KEY (run_id) REFERENCES product_publication_runs(run_id)
);
"""


class ShopeeRecoveryRunReconciliationError(RuntimeError):
    pass


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _digest_value(value: object, label: str) -> str:
    if type(value) is not str or not _HEX.fullmatch(value):
        raise ValueError(f"{label} is invalid")
    return "sha256:" + value.removeprefix("sha256:")


def _timestamp(value: object, label: str) -> datetime:
    if type(value) is not str:
        raise ValueError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _positive_id(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text.isdecimal() or int(text) <= 0:
        raise ValueError(f"{label} is invalid")
    return text


def _verified_document(value: Mapping[str, Any], digest_field: str, label: str) -> dict[str, Any]:
    result = deepcopy(dict(value))
    supplied = _digest_value(result.pop(digest_field, None), f"{label} {digest_field}")
    if supplied != _digest(result):
        raise ValueError(f"{label} digest conflicts")
    return {**result, digest_field: supplied}


def _manifest(value: Mapping[str, Any], run: Mapping[str, Any]) -> dict[str, Any]:
    manifest = _verified_document(value, "manifest_digest", "recovery manifest")
    if manifest.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
        raise ValueError("recovery manifest schema is invalid")
    expected = {
        "offer_id": run["offer_id"], "product_revision": run["revision"],
        "plan_id": run["plan_id"], "execution_snapshot_digest": run["snapshot_digest"],
    }
    if any(manifest.get(key) != expected[key] for key in expected):
        raise ValueError("recovery manifest conflicts with failed run")
    labels = manifest.get("target_labels")
    targets = manifest.get("targets")
    if labels != list(_RECOVERY_LABELS) or not isinstance(targets, list) \
            or [row.get("target_label") for row in targets if isinstance(row, Mapping)] != labels:
        raise ValueError("recovery manifest target scope is invalid")
    if run["target_count"] != len(labels):
        raise ValueError("recovery manifest target count conflicts")
    return manifest


def _baseline(value: Mapping[str, Any], *, manifest: Mapping[str, Any], label: str) -> dict[str, Any]:
    baseline = _verified_document(value, "evidence_digest", label)
    if (baseline.get("schema_version") != BASELINE_SCHEMA_VERSION
            or baseline.get("authority") != _AUTHORITY
            or baseline.get("manifest_digest") != manifest["manifest_digest"]
            or baseline.get("product_writes") != 0):
        raise ValueError(f"{label} identity is invalid")
    observed_min = _timestamp(baseline.get("observed_min"), f"{label} observed_min")
    observed_max = _timestamp(baseline.get("observed_max"), f"{label} observed_max")
    if observed_min > observed_max or baseline.get("observed_at") != baseline.get("observed_max"):
        raise ValueError(f"{label} observation range is invalid")
    global_row = baseline.get("global")
    if not isinstance(global_row, Mapping):
        raise ValueError(f"{label} global identity is invalid")
    normalized_global = {
        "global_item_id": _positive_id(global_row.get("global_item_id"), f"{label} global_item_id"),
        "global_model_id": _positive_id(global_row.get("global_model_id"), f"{label} global_model_id"),
        "model_sku": str(global_row.get("model_sku") or "").strip(),
        "item_status": str(global_row.get("item_status") or "").upper(),
        "model_status": str(global_row.get("model_status") or "").upper(),
        "model_presence": str(global_row.get("model_presence") or "").upper(),
        "model_status_authority": str(global_row.get("model_status_authority") or "").upper(),
        "title": str(global_row.get("title") or ""),
        "description": str(global_row.get("description") or ""),
        "gallery_image_ids": list(global_row.get("gallery_image_ids") or []),
        "price": str(global_row.get("price") or ""),
        "currency": str(global_row.get("currency") or "").upper(),
    }
    if (normalized_global["model_sku"] != "0988"
            or normalized_global["item_status"] != "NORMAL"
            or normalized_global["model_status"] != ""
            or normalized_global["model_presence"] != "EXACT"
            or normalized_global["model_status_authority"] != "NOT_RETURNED_BY_DIRECT_GET"):
        raise ValueError(f"{label} global state is invalid")
    rows = baseline.get("regions")
    if not isinstance(rows, list) or [row.get("target_label") for row in rows
                                      if isinstance(row, Mapping)] != list(_ALL_LABELS):
        raise ValueError(f"{label} regional scope is invalid")
    manifest_by_label = {row["target_label"]: row for row in manifest["targets"]}
    normalized_rows = []
    for row in rows:
        normalized = {
            "target_label": row.get("target_label"),
            "shop_id": _positive_id(row.get("shop_id"), f"{label} shop_id"),
            "item_id": _positive_id(row.get("item_id"), f"{label} item_id"),
            "model_id": _positive_id(row.get("model_id"), f"{label} model_id"),
            "model_sku": str(row.get("model_sku") or "").strip(),
            "global_item_id": _positive_id(row.get("global_item_id"), f"{label} global_item_id"),
            "global_model_id": _positive_id(row.get("global_model_id"), f"{label} global_model_id"),
            "item_status": str(row.get("item_status") or "").upper(),
            "model_status": str(row.get("model_status") or "").upper(),
            "category_id": str(row.get("category_id") or ""),
            "title": str(row.get("title") or ""),
            "description": str(row.get("description") or ""),
            "description_type": str(row.get("description_type") or "").lower(),
            "gallery_image_ids": list(row.get("gallery_image_ids") or []),
            "description_image_ids": list(row.get("description_image_ids") or []),
            "price": str(row.get("price") or ""),
            "currency": str(row.get("currency") or "").upper(),
        }
        target_label = normalized["target_label"]
        if (normalized["model_sku"] != normalized_global["model_sku"]
                or normalized["global_item_id"] != normalized_global["global_item_id"]
                or normalized["global_model_id"] != normalized_global["global_model_id"]
                or normalized["item_status"] != _EXPECTED_STATUS.get(target_label)
                or normalized["model_status"] not in {"NORMAL", "MODEL_NORMAL"}):
            raise ValueError(f"{label} provider baseline differs from required state")
        frozen = manifest_by_label.get(target_label)
        if frozen is not None and any(normalized[key] != str(frozen.get(key) or "") for key in (
                "shop_id", "item_id", "model_id", "model_sku", "global_item_id", "global_model_id")):
            raise ValueError(f"{label} conflicts with recovery manifest")
        if frozen is not None:
            differences = []
            desired = {
                "item.status": "NORMAL",
                "category": str(frozen.get("category_id") or ""),
                "copy.title": str((frozen.get("approved_copy") or {}).get("title") or ""),
                "copy.description": str((frozen.get("approved_copy") or {}).get("description") or ""),
                "gallery.images": list((frozen.get("approved_image_route") or {}).get("existing_media_ids") or []),
                "description.type": "extended",
                "description.images": list((frozen.get("approved_image_route") or {}).get("existing_media_ids") or []),
                "model.price": str((frozen.get("local_original_price") or {}).get("amount") or ""),
                "model.currency": str((frozen.get("local_original_price") or {}).get("currency") or "").upper(),
            }
            actual = {
                "item.status": normalized["item_status"], "category": normalized["category_id"],
                "copy.title": normalized["title"], "copy.description": normalized["description"],
                "gallery.images": normalized["gallery_image_ids"],
                "description.type": normalized["description_type"],
                "description.images": normalized["description_image_ids"],
                "model.price": normalized["price"], "model.currency": normalized["currency"],
            }
            differences = [key for key in desired if actual[key] != desired[key]]
            supplied_differences = row.get("manifest_differences")
            if supplied_differences != differences:
                raise ValueError(f"{label} manifest differences conflict")
            normalized["manifest_differences"] = differences
        normalized_rows.append(normalized)
    source_digests = baseline.get("source_digests")
    if (not isinstance(source_digests, list) or not source_digests
            or len(source_digests) != len(set(source_digests))):
        raise ValueError(f"{label} source digests are invalid")
    safe_sources = [_digest_value(item, f"{label} source digest") for item in source_digests]
    source_files = baseline.get("source_files")
    if not isinstance(source_files, list) or not source_files:
        raise ValueError(f"{label} source files are invalid")
    safe_files = []
    for item in source_files:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
            raise ValueError(f"{label} source files are invalid")
        path = str(item.get("path") or "")
        if not path:
            raise ValueError(f"{label} source file path is invalid")
        safe_files.append({"path": path, "sha256": _digest_value(item.get("sha256"), f"{label} source file")})
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "authority": _AUTHORITY,
        "manifest_digest": manifest["manifest_digest"],
        "observed_at": baseline["observed_at"],
        "observed_min": baseline["observed_min"],
        "observed_max": baseline["observed_max"],
        "product_writes": 0,
        "global": normalized_global,
        "regions": normalized_rows,
        "source_digests": safe_sources,
        "source_files": safe_files,
        "evidence_digest": baseline["evidence_digest"],
    }


def _baseline_facts(value: Mapping[str, Any]) -> dict[str, Any]:
    global_row = value["global"]
    global_identity = {key: deepcopy(global_row[key]) for key in (
        "global_item_id", "global_model_id", "model_sku", "item_status", "model_status",
        "model_presence", "model_status_authority")}
    regions = []
    for row in value["regions"]:
        keys = (
            "target_label", "shop_id", "item_id", "model_id", "model_sku",
            "global_item_id", "global_model_id", "item_status", "model_status", "price", "currency",
        )
        if row["target_label"] in _RECOVERY_LABELS:
            keys += (
                "category_id", "title", "description", "description_type",
                "gallery_image_ids", "description_image_ids", "manifest_differences",
            )
        regions.append({key: deepcopy(row[key]) for key in keys})
    return {"global": global_identity, "regions": regions}


def _inside(path: Path, roots: Sequence[str | Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(Path(root).resolve())
            return True
        except ValueError:
            continue
    return False


def _load_json_source(path_value: str | Path, *, roots: Sequence[str | Path],
                      expected_sha256: str | None = None, label: str) -> tuple[dict[str, Any], str]:
    path = Path(path_value).resolve()
    if not path.is_file() or not _inside(path, roots):
        raise ValueError(f"{label} is outside allowed evidence roots")
    raw = path.read_bytes()
    actual = "sha256:" + hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and actual != _digest_value(expected_sha256, f"{label} sha256"):
        raise ValueError(f"{label} bytes digest conflicts")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} JSON is invalid") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} JSON is invalid")
    return dict(value), actual


def baseline_from_manifest_sources(*, old_manifest: Mapping[str, Any],
                                   prior_ph_readback_path: str | Path,
                                   prior_global_identity_path: str | Path,
                                   allowed_evidence_roots: Sequence[str | Path]) -> dict[str, Any]:
    """Derive the pre-run provider state only from manifest-bound evidence bytes."""
    paths = old_manifest.get("source_file_paths") or {}
    hashes = old_manifest.get("source_file_sha256") or {}
    loaded = {}
    source_digests = []
    source_files = []
    for name in ("global_readback", "global_model_readback", "regional_readback"):
        value, sha = _load_json_source(paths.get(name), roots=allowed_evidence_roots,
                                       expected_sha256=hashes.get(name), label=f"manifest {name}")
        loaded[name] = value
        source_digests.append(sha)
        source_files.append({"path": str(Path(paths[name]).resolve()), "sha256": sha})
    ph, ph_sha = _load_json_source(prior_ph_readback_path, roots=allowed_evidence_roots,
                                   label="prior PH readback")
    global_identity, global_sha = _load_json_source(
        prior_global_identity_path, roots=allowed_evidence_roots, label="prior Global identity readback")
    source_digests.extend((ph_sha, global_sha))
    source_files.extend((
        {"path": str(Path(prior_ph_readback_path).resolve()), "sha256": ph_sha},
        {"path": str(Path(prior_global_identity_path).resolve()), "sha256": global_sha},
    ))
    if (ph.get("schema_version") != "shopee-successor-regional-item-readback/v1"
            or ph.get("product_writes") != 0):
        raise ValueError("prior PH readback identity is invalid")
    ph_core = deepcopy(ph)
    ph_digest = _digest_value(ph_core.pop("evidence_digest", None), "prior PH evidence digest")
    if ph_digest != _digest(ph_core):
        raise ValueError("prior PH evidence digest conflicts")
    if (global_identity.get("schema_version") != "shopee-successor-official-readback/v1"
            or global_identity.get("product_writes") != 0):
        raise ValueError("prior Global identity readback is invalid")
    global_readback = loaded["global_readback"]
    global_models = loaded["global_model_readback"]
    regional = loaded["regional_readback"]
    if (global_readback.get("schema_version") != "shopee-global-published-list-readback/v1"
            or global_readback.get("product_writes") != 0
            or global_models.get("schema_version") != "shopee-global-model-readback/v1"
            or global_models.get("product_writes") != 0
            or regional.get("schema_version") != "shopee-regional-recovery-preflight/v1"
            or regional.get("product_writes") != 0):
        raise ValueError("manifest prior provider evidence is invalid")
    manifest_digest = _digest_value(old_manifest.get("manifest_digest"), "manifest digest")
    global_id = str(old_manifest.get("global_item_id") or "")
    global_model_id = str(old_manifest.get("global_model_id") or "")
    official_global = global_identity.get("global") or {}
    model_rows = global_models.get("models") or []
    if (official_global.get("complete") is not True
            or str(official_global.get("global_item_id") or "") != global_id
            or str(official_global.get("item_status") or "").upper() != "NORMAL"
            or len(model_rows) != 1
            or str(model_rows[0].get("global_model_id") or "") != global_model_id
            or str(model_rows[0].get("global_model_sku") or "") != "0988"):
        raise ValueError("prior Global identity conflicts")
    regional_rows = {row.get("target_label"): row for row in regional.get("targets") or []}
    ph_rows = {row.get("target_label"): row for row in ph.get("targets") or []}
    published = {
        "shopee:" + str(row.get("shop_region") or "").upper(): row
        for row in ((global_readback.get("response") or {}).get("response") or {}).get("published_item") or []
    }
    manifest_by_label = {row["target_label"]: row for row in old_manifest.get("targets") or []}
    rows = []
    for label in _ALL_LABELS:
        identity_row = ph_rows.get(label)
        if not isinstance(identity_row, Mapping) or identity_row.get("complete") is not True \
                or identity_row.get("global_linkage_exact") is not True:
            raise ValueError("prior regional identity coverage is incomplete")
        models = identity_row.get("models") or []
        if len(models) != 1:
            raise ValueError("prior regional model identity is ambiguous")
        model = models[0]
        price_rows = model.get("price_info") or []
        if len(price_rows) != 1:
            raise ValueError("prior regional model price is ambiguous")
        published_row = published.get(label)
        expected_code = 1 if label == "shopee:PH" else 8
        if (not isinstance(published_row, Mapping)
                or int(published_row.get("item_status") or 0) != expected_code
                or str(published_row.get("item_id") or "") != str(identity_row.get("item_id") or "")
                or str(published_row.get("shop_id") or "") != str(identity_row.get("shop_id") or "")):
            raise ValueError("prior published-list identity conflicts")
        detail = regional_rows.get(label) if label in _RECOVERY_LABELS else None
        if label in _RECOVERY_LABELS and not isinstance(detail, Mapping):
            raise ValueError("prior recovery-target detail is missing")
        row = {
            "target_label": label, "shop_id": str(identity_row.get("shop_id") or ""),
            "item_id": str(identity_row.get("item_id") or ""),
            "model_id": str(model.get("model_id") or ""), "model_sku": str(model.get("model_sku") or ""),
            "global_item_id": str(identity_row.get("resolved_global_item_id") or ""),
            "global_model_id": global_model_id,
            "item_status": str(identity_row.get("item_status") or ""),
            "model_status": str(model.get("model_status") or ""),
            "category_id": str((detail or {}).get("category_id") or ""),
            "title": str((detail or {}).get("title") or ""),
            "description": str((detail or {}).get("description") or ""),
            "description_type": str((detail or {}).get("description_type") or ""),
            "gallery_image_ids": list((detail or {}).get("gallery_image_ids") or []),
            "description_image_ids": list((detail or {}).get("description_image_ids") or []),
            "price": str(price_rows[0].get("original_price") or ""),
            "currency": str(price_rows[0].get("currency") or ""),
        }
        if label in _RECOVERY_LABELS:
            frozen = manifest_by_label[label]
            desired = {
                "item.status": "NORMAL", "category": str(frozen["category_id"]),
                "copy.title": frozen["approved_copy"]["title"],
                "copy.description": frozen["approved_copy"]["description"],
                "gallery.images": frozen["approved_image_route"]["existing_media_ids"],
                "description.type": "extended",
                "description.images": frozen["approved_image_route"]["existing_media_ids"],
                "model.price": str(frozen["local_original_price"]["amount"]),
                "model.currency": str(frozen["local_original_price"]["currency"]).upper(),
            }
            actual = {
                "item.status": row["item_status"].upper(), "category": row["category_id"],
                "copy.title": row["title"], "copy.description": row["description"],
                "gallery.images": row["gallery_image_ids"], "description.type": row["description_type"].lower(),
                "description.images": row["description_image_ids"], "model.price": row["price"],
                "model.currency": row["currency"].upper(),
            }
            row["manifest_differences"] = [key for key in desired if actual[key] != desired[key]]
        rows.append(row)
    times = []
    for source in (global_readback, global_models, regional, ph):
        if source.get("observed_at"):
            times.append((source["observed_at"], _timestamp(source["observed_at"], "prior observed_at")))
        for row in source.get("targets") or []:
            if isinstance(row, Mapping) and row.get("observed_at"):
                times.append((row["observed_at"], _timestamp(row["observed_at"], "prior target observed_at")))
    if global_identity.get("started_at"):
        times.append((global_identity["started_at"], _timestamp(global_identity["started_at"], "prior started_at")))
    if not times:
        raise ValueError("prior observation times are unavailable")
    minimum = min(times, key=lambda value: value[1])[0]
    maximum = max(times, key=lambda value: value[1])[0]
    value = {
        "schema_version": BASELINE_SCHEMA_VERSION, "authority": _AUTHORITY,
        "manifest_digest": manifest_digest, "observed_at": maximum,
        "observed_min": minimum, "observed_max": maximum, "product_writes": 0,
        "global": {"global_item_id": global_id, "global_model_id": global_model_id,
                   "model_sku": "0988", "item_status": "NORMAL",
                   "model_status": "", "model_presence": "EXACT",
                   "model_status_authority": "NOT_RETURNED_BY_DIRECT_GET",
                   "title": "", "description": "", "gallery_image_ids": [],
                   "price": str((model_rows[0].get("price_info") or {}).get("original_price") or ""),
                   "currency": str((model_rows[0].get("price_info") or {}).get("currency") or "")},
        "regions": rows, "source_digests": source_digests, "source_files": source_files,
    }
    return {**value, "evidence_digest": _digest(value)}


def baseline_from_direct_id_readbacks(*, old_manifest: Mapping[str, Any],
                                      readback_paths: Sequence[str | Path],
                                      allowed_evidence_roots: Sequence[str | Path],
                                      expected_run: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild the provider baseline from immutable raw GET envelopes."""
    manifest_digest = _digest_value(old_manifest.get("manifest_digest"), "manifest digest")
    by_label: dict[str, dict[str, Any]] = {}
    source_digests: list[str] = []
    observed: list[datetime] = []
    observed_text: list[str] = []
    global_facts: list[dict[str, Any]] = []
    source_files: list[dict[str, str]] = []
    label_groups: list[list[str]] = []
    expected_top = {
        "run_id": expected_run["run_id"],
        "report_id": expected_run["report_id"],
        "manifest_digest": manifest_digest,
    }
    for readback_path in readback_paths:
        path = Path(readback_path).resolve()
        if not path.is_file() or not _inside(path, allowed_evidence_roots):
            raise ValueError("direct-ID readback is outside allowed evidence roots")
        top_raw = path.read_bytes()
        top = json.loads(top_raw.decode("utf-8"))
        checked_top = _verified_document(top, "evidence_digest", "direct-ID readback")
        if (checked_top.get("schema_version") != "shopee-recovery-official-direct-id-readback/v1"
                or checked_top.get("authority") != _AUTHORITY
                or checked_top.get("product_writes") != 0
                or any(checked_top.get(key) != value for key, value in expected_top.items())):
            raise ValueError("direct-ID readback identity is invalid")
        top_sha = "sha256:" + hashlib.sha256(top_raw).hexdigest()
        source_digests.append(top_sha)
        source_files.append({"path": str(path), "sha256": top_sha})
        observed.append(_timestamp(checked_top.get("observed_at"), "direct-ID readback observed_at"))
        observed_text.append(checked_top["observed_at"])
        label_groups.append([str(row.get("target_label") or "") for row in checked_top.get("targets") or []])
        for target in checked_top.get("targets") or []:
            label = str(target.get("target_label") or "")
            if label in by_label or label not in _ALL_LABELS:
                raise ValueError("direct-ID readback target scope is invalid")
            target_core = deepcopy(dict(target))
            target_digest = _digest_value(target_core.pop("target_evidence_digest", None),
                                          "target evidence digest")
            if target_digest != _digest(target_core):
                raise ValueError("direct-ID target evidence digest conflicts")
            query = target.get("query") or {}
            reads = target.get("reads") or {}
            if query.get("method") != "GET" or query.get("mode") != "DIRECT_ID" \
                    or set(reads) != {"item", "models", "global_linkage", "global_item", "global_models"}:
                raise ValueError("direct-ID GET coverage is incomplete")
            raw_by_resource = {}
            for resource, meta in reads.items():
                evidence_path = Path(str(meta.get("response_ref") or "")).resolve()
                if (meta.get("complete") is not True or not evidence_path.is_file()
                        or not _inside(evidence_path, allowed_evidence_roots)):
                    raise ValueError("raw direct-ID GET evidence is unavailable")
                raw = evidence_path.read_bytes()
                if _digest_value(meta.get("response_digest"), "raw GET digest") \
                        != "sha256:" + hashlib.sha256(raw).hexdigest():
                    raise ValueError("raw direct-ID GET bytes digest conflicts")
                envelope = json.loads(raw.decode("utf-8"))
                expected_request = {**query, "resource": resource}
                if (envelope.get("schema_version") != "shopee-official-direct-id-get-evidence/v1"
                        or envelope.get("complete") is not True
                        or envelope.get("request") != expected_request
                        or envelope.get("observed_at") != meta.get("observed_at")):
                    raise ValueError("raw direct-ID GET envelope conflicts")
                raw_by_resource[resource] = envelope.get("raw_response")
                source_digests.append("sha256:" + hashlib.sha256(raw).hexdigest())
                source_files.append({"path": str(evidence_path),
                                     "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()})
                observed.append(_timestamp(envelope.get("observed_at"), "raw GET observed_at"))
                observed_text.append(envelope["observed_at"])
            item = raw_by_resource["item"]
            models = raw_by_resource["models"]
            linkage = raw_by_resource["global_linkage"]
            global_item = raw_by_resource["global_item"]
            global_models = raw_by_resource["global_models"]
            if not isinstance(item, Mapping) or not isinstance(linkage, Mapping) \
                    or not isinstance(global_item, Mapping) or not isinstance(models, list) \
                    or not isinstance(global_models, list) or len(models) != 1 or len(global_models) != 1:
                raise ValueError("raw direct-ID GET response shape is invalid")
            model = models[0]
            global_model = global_models[0]
            exact = {key: str(query.get(key) or "") for key in (
                "shop_id", "item_id", "model_id", "model_sku", "global_item_id", "global_model_id")}
            if any((
                str(item.get("shop_id") or "") != exact["shop_id"],
                str(item.get("item_id") or "") != exact["item_id"],
                str(model.get("model_id") or "") != exact["model_id"],
                str(model.get("model_sku") or "") != exact["model_sku"],
                str(linkage.get("global_item_id") or "") != exact["global_item_id"],
                str(global_item.get("global_item_id") or "") != exact["global_item_id"],
                str(global_model.get("global_model_id") or "") != exact["global_model_id"],
                str(global_model.get("model_sku") or "") != exact["model_sku"],
            )):
                raise ValueError("raw direct-ID provider identity conflicts")
            global_facts.append({
                "global_item_id": str(global_item.get("global_item_id") or ""),
                "global_model_id": str(global_model.get("global_model_id") or ""),
                "model_sku": str(global_model.get("model_sku") or ""),
                "item_status": str(global_item.get("status") or "").upper(),
                "model_status": str(global_model.get("status") or "").upper(),
                "model_presence": "EXACT",
                "model_status_authority": "NOT_RETURNED_BY_DIRECT_GET",
                "title": str(global_item.get("title") or ""),
                "description": str(global_item.get("description") or ""),
                "gallery_image_ids": list(global_item.get("gallery_image_ids") or []),
                "price": str(global_model.get("price") or ""),
                "currency": str(global_model.get("currency") or "").upper(),
            })
            by_label[label] = {
                "target_label": label, **exact,
                "item_status": str(item.get("status") or ""),
                "model_status": str(model.get("status") or ""),
                "category_id": str(item.get("category_id") or ""),
                "title": str(item.get("title") or ""),
                "description": str(item.get("description") or ""),
                "description_type": str(item.get("description_type") or ""),
                "gallery_image_ids": list(item.get("gallery_image_ids") or []),
                "description_image_ids": list(item.get("description_image_ids") or []),
                "price": str(model.get("price") or ""),
                "currency": str(model.get("currency") or ""),
            }
    if label_groups != [list(_RECOVERY_LABELS), ["shopee:PH"]]:
        raise ValueError("direct-ID readback source roles are invalid")
    if set(by_label) != set(_ALL_LABELS):
        raise ValueError("direct-ID readback must cover PH, MY, TH and VN")
    manifest_targets = {row["target_label"]: row for row in old_manifest.get("targets") or []}
    for label in _RECOVERY_LABELS:
        row = by_label[label]
        frozen = manifest_targets[label]
        desired = {
            "item.status": "NORMAL", "category": str(frozen["category_id"]),
            "copy.title": frozen["approved_copy"]["title"],
            "copy.description": frozen["approved_copy"]["description"],
            "gallery.images": frozen["approved_image_route"]["existing_media_ids"],
            "description.type": "extended",
            "description.images": frozen["approved_image_route"]["existing_media_ids"],
            "model.price": str(frozen["local_original_price"]["amount"]),
            "model.currency": str(frozen["local_original_price"]["currency"]).upper(),
        }
        actual = {
            "item.status": row["item_status"].upper(), "category": row["category_id"],
            "copy.title": row["title"], "copy.description": row["description"],
            "gallery.images": row["gallery_image_ids"], "description.type": row["description_type"].lower(),
            "description.images": row["description_image_ids"], "model.price": row["price"],
            "model.currency": row["currency"].upper(),
        }
        row["manifest_differences"] = [key for key in desired if actual[key] != desired[key]]
    first = by_label["shopee:PH"]
    # All five-method observations were checked above and bind the same ids.
    for label in _ALL_LABELS:
        if by_label[label]["global_item_id"] != first["global_item_id"] \
                or by_label[label]["global_model_id"] != first["global_model_id"]:
            raise ValueError("regional readbacks do not share the frozen Global identity")
    if not global_facts or any(row != global_facts[0] for row in global_facts[1:]):
        raise ValueError("direct-ID Global observations conflict")
    if (global_facts[0]["global_item_id"] != first["global_item_id"]
            or global_facts[0]["global_model_id"] != first["global_model_id"]
            or global_facts[0]["model_sku"] != first["model_sku"]
            or global_facts[0]["item_status"] != "NORMAL"
            or global_facts[0]["model_status"] != ""):
        raise ValueError("direct-ID Global state conflicts")
    value = {
        "schema_version": BASELINE_SCHEMA_VERSION, "authority": _AUTHORITY,
        "manifest_digest": manifest_digest,
        "observed_at": observed_text[observed.index(max(observed))],
        "observed_min": observed_text[observed.index(min(observed))],
        "observed_max": observed_text[observed.index(max(observed))],
        "product_writes": 0,
        "global": global_facts[0],
        "regions": [by_label[label] for label in _ALL_LABELS],
        "source_digests": source_digests, "source_files": source_files,
    }
    return {**value, "evidence_digest": _digest(value)}


class ShopeeRecoveryRunReconciliationStore:
    """Validate and append one baseline-unchanged receipt per failed recovery run."""

    def __init__(self, path: str | Path = DEFAULT_PRODUCT_PUBLICATION_RUN_DB) -> None:
        self.path = Path(path)

    def _readonly(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    def _rebuild(self, *, run_id: str, source_refs: Mapping[str, Any], registered_by: str,
                 allowed_evidence_roots: Sequence[str | Path], snapshot: Mapping[str, Any],
                 candidate: Mapping[str, Any], approval: Mapping[str, Any]) -> dict[str, Any]:
        run = ProductPublicationRunStore(self.path).get_run_by_id(run_id=run_id)
        if run is None:
            raise ValueError("publication run not found")
        if (run["state"] != "FAILED" or run["final_report_id"] is not None
                or run["failure_code"] not in _FAILURES
                or run["platform_scope"] != ["SHOPEE"]):
            raise ValueError("only a pre-provider failed Shopee run may be reconciled")
        if not isinstance(source_refs, Mapping) or set(source_refs) != {
                "old_manifest_path", "prior_ph_readback_path", "prior_global_identity_path",
                "fresh_readback_paths"}:
            raise ValueError("recovery reconciliation source refs are invalid")
        fresh_ref_values = source_refs["fresh_readback_paths"]
        if not isinstance(fresh_ref_values, list) or len(fresh_ref_values) != 2:
            raise ValueError("fresh direct-ID readback paths are invalid")
        safe_refs = {
            "old_manifest_path": str(Path(source_refs["old_manifest_path"]).resolve()),
            "prior_ph_readback_path": str(Path(source_refs["prior_ph_readback_path"]).resolve()),
            "prior_global_identity_path": str(Path(source_refs["prior_global_identity_path"]).resolve()),
            "fresh_readback_paths": [str(Path(item).resolve()) for item in fresh_ref_values],
        }
        old_manifest, manifest_file_sha = _load_json_source(
            safe_refs["old_manifest_path"], roots=allowed_evidence_roots,
            label="recovery manifest")
        manifest = _manifest(old_manifest, run)
        from shared_platform.shopee_regional_recovery import validate_recovery_manifest
        checked = validate_recovery_manifest(
            old_manifest, snapshot=snapshot, candidate=candidate, approval=approval,
            allowed_source_roots=allowed_evidence_roots)
        if checked != old_manifest:
            raise ValueError("deep recovery manifest validation changed immutable facts")
        expected_request = {"kind": "SHOPEE_RECOVERY", "authority_digest": manifest["manifest_digest"]}
        if run.get("request_identity") != expected_request:
            raise ValueError("failed run request identity conflicts with recovery manifest")
        before = baseline_from_manifest_sources(
            old_manifest=old_manifest,
            prior_ph_readback_path=safe_refs["prior_ph_readback_path"],
            prior_global_identity_path=safe_refs["prior_global_identity_path"],
            allowed_evidence_roots=allowed_evidence_roots)
        after = baseline_from_direct_id_readbacks(
            old_manifest=old_manifest, readback_paths=safe_refs["fresh_readback_paths"],
            allowed_evidence_roots=allowed_evidence_roots, expected_run=run)
        before = _baseline(before, manifest=manifest, label="prior baseline")
        after = _baseline(after, manifest=manifest, label="fresh readback")
        if _baseline_facts(before) != _baseline_facts(after):
            raise ValueError("fresh provider baseline changed")
        if _timestamp(before["observed_max"], "prior baseline observed_max") >= _timestamp(run["created_at"], "run created_at"):
            raise ValueError("prior baseline must predate the failed run")
        if _timestamp(after["observed_min"], "fresh readback observed_min") <= _timestamp(run["updated_at"], "run updated_at"):
            raise ValueError("fresh readback must postdate the failed run")
        if type(registered_by) is not str or not registered_by.strip() or len(registered_by) > 128:
            raise ValueError("registered_by is invalid")
        with self._readonly() as conn:
            events = conn.execute(
                "SELECT sequence,state,failure_code,event_digest FROM product_publication_run_events "
                "WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
            if [(row["sequence"], row["state"]) for row in events] != [(1, "QUEUED"), (2, "FAILED")]:
                raise ValueError("recovery run did not fail directly from QUEUED")
            if events[-1]["failure_code"] != run["failure_code"]:
                raise ProductPublicationRunIntegrityError("terminal event conflicts with run")
            try:
                report = conn.execute(
                    "SELECT 1 FROM product_publication_reports WHERE run_id=? LIMIT 1", (run_id,)
                ).fetchone()
            except sqlite3.OperationalError:
                report = None
            if report is not None:
                raise ValueError("immutable publication report already exists")
            event_digests = [_digest_value(row["event_digest"], "run event digest") for row in events]
        run_identity = {key: deepcopy(run[key]) for key in (
            "run_id", "report_id", "offer_id", "revision", "plan_id", "snapshot_digest",
            "platform_scope", "target_count", "execution_identity", "request_identity")}
        return {
            "schema_version": SCHEMA_VERSION,
            "run_identity": run_identity,
            "failure_code": run["failure_code"],
            "failure_boundary": "PRE_RUNNING",
            "ordered_event_digests": event_digests,
            "terminal_event_digest": event_digests[-1],
            "recovery_manifest_digest": manifest["manifest_digest"],
            "old_manifest": manifest,
            "old_manifest_file_sha256": manifest_file_sha,
            "source_refs": safe_refs,
            "deep_manifest_validated": True,
            "ordered_target_labels": list(_RECOVERY_LABELS),
            "provider_request_attempted": False,
            "external_write_count": 0,
            "baseline_unchanged": True,
            "prior_baseline": before,
            "fresh_readback": after,
            "registered_by": registered_by.strip(),
        }

    def prepare(self, **kwargs: Any) -> dict[str, Any]:
        core = self._rebuild(**kwargs)
        return {**core, "receipt_digest": _digest(core)}

    def register(self, **kwargs: Any) -> dict[str, Any]:
        receipt = self.prepare(**kwargs)
        now = datetime.now(timezone.utc).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT receipt_json,receipt_digest FROM shopee_recovery_run_zero_write_reconciliations "
                "WHERE run_id=?", (kwargs["run_id"],)).fetchone()
            if existing is not None:
                if (existing["receipt_digest"] != receipt["receipt_digest"]
                        or json.loads(existing["receipt_json"]) != receipt):
                    raise ShopeeRecoveryRunReconciliationError(
                        "recovery zero-write receipt conflicts with append-only record")
                return receipt
            conn.execute(
                "INSERT INTO shopee_recovery_run_zero_write_reconciliations"
                "(run_id,receipt_json,receipt_digest,created_at) VALUES(?,?,?,?)",
                (kwargs["run_id"], _canonical(receipt), receipt["receipt_digest"], now))
            conn.commit()
        return receipt

    def get(self, *, run_id: str, allowed_evidence_roots: Sequence[str | Path],
            snapshot: Mapping[str, Any], candidate: Mapping[str, Any],
            approval: Mapping[str, Any]) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            with self._readonly() as conn:
                row = conn.execute(
                    "SELECT receipt_json,receipt_digest FROM shopee_recovery_run_zero_write_reconciliations "
                    "WHERE run_id=?", (run_id,)).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        receipt = json.loads(row["receipt_json"])
        core = dict(receipt)
        digest = core.pop("receipt_digest", None)
        if digest != row["receipt_digest"] or digest != _digest(core):
            raise ShopeeRecoveryRunReconciliationError("recovery zero-write receipt digest conflicts")
        if receipt.get("deep_manifest_validated") is not True:
            raise ShopeeRecoveryRunReconciliationError("deep recovery manifest validation is missing")
        verified = self._rebuild(
            run_id=run_id, source_refs=receipt["source_refs"], registered_by=receipt["registered_by"],
            allowed_evidence_roots=allowed_evidence_roots,
            snapshot=snapshot, candidate=candidate, approval=approval)
        if verified != core:
            raise ShopeeRecoveryRunReconciliationError("recovery zero-write receipt facts conflict")
        return receipt


def validate_stored_recovery_run_receipt(receipt: Mapping[str, Any], *, run_store_path: str | Path,
                                         allowed_evidence_roots: Sequence[str | Path],
                                         snapshot: Mapping[str, Any], candidate: Mapping[str, Any],
                                         approval: Mapping[str, Any]) -> dict[str, Any]:
    run_id = str((receipt.get("run_identity") or {}).get("run_id") or "")
    stored = ShopeeRecoveryRunReconciliationStore(run_store_path).get(
        run_id=run_id, allowed_evidence_roots=allowed_evidence_roots,
        snapshot=snapshot, candidate=candidate, approval=approval)
    if stored is None or stored != dict(receipt):
        raise ShopeeRecoveryRunReconciliationError("stored recovery zero-write receipt is unavailable")
    return stored


__all__ = [
    "BASELINE_SCHEMA_VERSION", "SCHEMA_VERSION", "ShopeeRecoveryRunReconciliationError",
    "ShopeeRecoveryRunReconciliationStore", "validate_stored_recovery_run_receipt",
    "baseline_from_direct_id_readbacks", "baseline_from_manifest_sources",
]
