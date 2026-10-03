"""Durable, credential-free progress for one Shopee publication run."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4


SCHEMA_VERSION = "shopee-publication-checkpoint/v1"
_SAFE_LABEL = re.compile(r"^shopee:[A-Z]{2}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")
_SAFE_CODE = re.compile(r"^[A-Za-z0-9_.:-]{0,80}$")
_STAGES = frozenset({"DISPATCH", "READBACK"})


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_message(value: object) -> str:
    text = " ".join(str(value or "").split())
    lowered = text.casefold()
    if any(marker in lowered for marker in (
        "access_token", "access-token", "access token", "refresh_token",
        "refresh-token", "refresh token", "partner_key", "partner-key",
        "partner key", "authorization", "bearer", "api_key", "api-key",
        "api key", "password", "passwd", "cookie", "set-cookie", "secret",
        "http://", "https://",
    )):
        return "provider detail redacted"
    return text[:240]


def _provider_identities(row: Mapping[str, object]) -> dict[str, object]:
    values = {
        "provider_task_id": str(row.get("provider_task_id") or "").strip(),
        "provider_item_id": str(
            row.get("item_id") or row.get("existing_item_id") or ""
        ).strip(),
    }
    model_ids: list[str] = []
    direct = str(row.get("model_id") or "").strip()
    if direct:
        model_ids.append(direct)
    catalog_rows = row.get("official_catalog_rows")
    if isinstance(catalog_rows, list):
        for catalog_row in catalog_rows:
            identity = catalog_row.get("identity") if isinstance(catalog_row, Mapping) else None
            model_id = str(identity.get("variant_id") or "").strip() if isinstance(identity, Mapping) else ""
            if model_id:
                model_ids.append(model_id)
    for value in (*values.values(), *model_ids):
        if value and not _SAFE_ID.fullmatch(str(value)):
            raise ValueError("Shopee provider identity is unsafe")
    return {**values, "provider_model_ids": sorted(set(model_ids))}


def _public_message(state: Mapping[str, object]) -> str:
    outcome = str(state.get("outcome") or "UNKNOWN").upper()
    stage = str(state.get("stage") or "QUEUED").upper()
    if outcome == "PUBLISHED":
        return "Shopee official readback matched the approved target"
    if outcome in {"ACCEPTED", "PROCESSING"}:
        return "Shopee accepted the target; official readback is pending"
    if outcome in {"QUEUED", "NOT_DISPATCHED"}:
        return "Shopee target has not entered provider dispatch"
    return f"Shopee target did not complete during {stage.casefold()}"


class ShopeePublicationCheckpointStore:
    """Atomic per-target journal; raw task IDs never enter public projections."""

    def __init__(self, root: str | Path, request: object) -> None:
        self.root = Path(root)
        self.identity = {
            "run_id": str(getattr(request, "run_id", "")),
            "report_id": str(getattr(request, "report_id", "")),
            "offer_id": str(getattr(request, "snapshot", {}).get("offer_id", "")),
            "revision": getattr(request, "snapshot", {}).get("product_revision"),
            "plan_id": str(getattr(request, "snapshot", {}).get("plan_id", "")),
            "snapshot_digest": str(getattr(request, "snapshot", {}).get("snapshot_digest", "")),
            "platform": "SHOPEE",
            "target_labels": list(getattr(request, "target_labels", ())),
        }
        if (
            not self.identity["run_id"]
            or not self.identity["report_id"]
            or not self.identity["offer_id"].isdigit()
            or type(self.identity["revision"]) is not int
            or self.identity["revision"] <= 0
            or not self.identity["plan_id"]
            or not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", self.identity["snapshot_digest"])
            or not self.identity["target_labels"]
            or any(not _SAFE_LABEL.fullmatch(label) for label in self.identity["target_labels"])
        ):
            raise ValueError("Shopee checkpoint identity is invalid")
        self.path = self.root / self.identity["offer_id"] / str(self.identity["revision"]) / self.identity["run_id"] / "shopee-publication-checkpoint.json"

    def _initial(self) -> dict[str, Any]:
        body = {"schema_version": SCHEMA_VERSION, **deepcopy(self.identity), "targets": {}, "events": []}
        body["checkpoint_digest"] = _digest(body)
        return body

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._initial()
        body = json.loads(self.path.read_text(encoding="utf-8"))
        supplied = body.pop("checkpoint_digest", None)
        if supplied != _digest(body) or any(body.get(key) != value for key, value in self.identity.items()):
            raise ValueError("Shopee checkpoint identity or digest conflicts")
        body["checkpoint_digest"] = supplied
        return body

    def record(self, stage: str, row: Mapping[str, object]) -> None:
        if stage not in _STAGES or not isinstance(row, Mapping):
            raise ValueError("Shopee checkpoint event is invalid")
        label = str(row.get("target_label") or "")
        if label not in self.identity["target_labels"]:
            raise ValueError("Shopee checkpoint target conflicts")
        identities = _provider_identities(row)
        code = str(row.get("provider_code") or "").strip()
        if not _SAFE_CODE.fullmatch(code):
            code = "provider_code_redacted"
        count = row.get("external_write_count")
        if count is not None and (type(count) is not int or count < 0):
            count = None
        attempted = bool(row.get("attempted"))
        outcome = str(row.get("outcome") or "UNKNOWN").upper()[:32]
        state = {
            "stage": stage,
            "outcome": outcome,
            "request_attempted": attempted,
            **identities,
            "provider_code": code,
            "provider_message": _safe_message(row.get("message")),
            "external_write_count": count,
        }
        body = self.load()
        body.pop("checkpoint_digest", None)
        targets = dict(body["targets"])
        previous = targets.get(label) if isinstance(targets.get(label), Mapping) else {}
        for identity_key in ("provider_task_id", "provider_item_id"):
            if not state[identity_key] and previous.get(identity_key):
                state[identity_key] = previous[identity_key]
        if not state["provider_model_ids"] and previous.get("provider_model_ids"):
            state["provider_model_ids"] = previous["provider_model_ids"]
        targets[label] = state
        body["targets"] = targets
        body["events"] = [*body["events"], {
            "sequence": len(body["events"]) + 1,
            "target_label": label,
            "stage": stage,
            "outcome": outcome,
            "request_attempted": attempted,
            "provider_identity_bound": bool(
                state["provider_task_id"]
                or state["provider_item_id"]
                or state["provider_model_ids"]
            ),
            "provider_code": code,
            "provider_message": state["provider_message"],
            "external_write_count": count,
        }]
        body["checkpoint_digest"] = _digest(body)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            temp.write_text(_canonical(body) + "\n", encoding="utf-8")
            temp.replace(self.path)
        finally:
            if temp.exists():
                temp.unlink()

    def public_progress(self) -> dict[str, Any]:
        body = self.load()
        targets = body.get("targets", {})
        return {
            "schema_version": "shopee-publication-progress/v1",
            "event_count": len(body.get("events", [])),
            "targets": [
                {
                    "target_label": label,
                    "stage": targets.get(label, {}).get("stage", "QUEUED"),
                    "outcome": targets.get(label, {}).get("outcome", "QUEUED"),
                    "request_attempted": bool(targets.get(label, {}).get("request_attempted")),
                    "provider_identity_bound": bool(
                        targets.get(label, {}).get("provider_task_id")
                        or targets.get(label, {}).get("provider_item_id")
                        or targets.get(label, {}).get("provider_model_ids")
                    ),
                    "provider_code": targets.get(label, {}).get("provider_code", ""),
                    "provider_message": _public_message(targets.get(label, {})),
                    "external_write_count": targets.get(label, {}).get("external_write_count"),
                }
                for label in self.identity["target_labels"]
            ],
        }


def public_shopee_progress_for_run(
    root: str | Path, run: Mapping[str, object]
) -> dict[str, Any] | None:
    """Read only the sanitized progress projection for an active exact run."""

    if run.get("platform_scope") != ["SHOPEE"]:
        return None
    path = (
        Path(root)
        / str(run.get("offer_id") or "")
        / str(run.get("revision") or "")
        / str(run.get("run_id") or "")
        / "shopee-publication-checkpoint.json"
    )
    if not path.is_file():
        return None
    body = json.loads(path.read_text(encoding="utf-8"))
    supplied = body.pop("checkpoint_digest", None)
    if supplied != _digest(body):
        raise ValueError("Shopee checkpoint digest conflicts")
    if any(
        body.get(key) != run.get(key)
        for key in ("run_id", "report_id", "offer_id", "revision", "plan_id")
    ) or body.get("snapshot_digest") != run.get("snapshot_digest"):
        raise ValueError("Shopee checkpoint run identity conflicts")
    targets = body.get("targets") if isinstance(body.get("targets"), Mapping) else {}
    labels = body.get("target_labels") if isinstance(body.get("target_labels"), list) else []
    return {
        "schema_version": "shopee-publication-progress/v1",
        "event_count": len(body.get("events", [])),
        "targets": [
            {
                "target_label": label,
                "stage": targets.get(label, {}).get("stage", "QUEUED"),
                "outcome": targets.get(label, {}).get("outcome", "QUEUED"),
                "request_attempted": bool(targets.get(label, {}).get("request_attempted")),
                "provider_identity_bound": bool(
                    targets.get(label, {}).get("provider_task_id")
                    or targets.get(label, {}).get("provider_item_id")
                    or targets.get(label, {}).get("provider_model_ids")
                ),
                "provider_code": targets.get(label, {}).get("provider_code", ""),
                "provider_message": _public_message(targets.get(label, {})),
                "external_write_count": targets.get(label, {}).get("external_write_count"),
            }
            for label in labels
        ],
    }


__all__ = [
    "SCHEMA_VERSION",
    "ShopeePublicationCheckpointStore",
    "public_shopee_progress_for_run",
]
