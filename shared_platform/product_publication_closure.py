"""Immutable, sanitized business-closure reports for one publication plan.

Closure never changes a platform result. It records the user's disposition of
verified, manual-handoff, processing, or failed targets and references the
immutable reports that supplied those facts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any

from core.config import ROOT
from shared_platform.immutable_approval_files import persist_immutable_bytes, require_local_path


CLOSURE_SCHEMA_VERSION = "product-publication-closure/v1"
DEFAULT_CLOSURE_ROOT = ROOT / "reports" / "product-publication"
_SOURCE_STATUSES = frozenset({"PUBLISHED", "PROCESSING", "PARTIAL", "FAILED", "BLOCKED"})
_RESOLUTIONS = frozenset(
    {
        "OFFICIAL_READBACK_VERIFIED",
        "MANUAL_HANDOFF_ACCEPTED",
        "OPEN_PROCESSING",
        "FAILED",
    }
)
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}\Z")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(value: object, name: str, *, max_length: int = 320) -> str:
    if type(value) is not str or not value.strip() or value != value.strip() or len(value) > max_length:
        raise ValueError(f"{name} is invalid")
    return value


def _timestamp(value: object, name: str) -> str:
    text = _text(value, name, max_length=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include timezone")
    return text


def _safe_relative_path(value: object) -> str:
    text = _text(value, "source report path", max_length=512)
    pure = PurePosixPath(text)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("source report path is invalid")
    return pure.as_posix()


def validate_publication_closure(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("publication closure must be a mapping")
    expected = {
        "schema_version", "closure_id", "offer_id", "revision", "plan_id",
        "snapshot_digest", "business_complete", "closure_status", "recorded_at",
        "recorded_by", "summary", "targets", "source_reports", "closure_digest",
    }
    if set(value) != expected or value.get("schema_version") != CLOSURE_SCHEMA_VERSION:
        raise ValueError("publication closure fields are invalid")
    closure_id = _text(value.get("closure_id"), "closure_id", max_length=160)
    if not _SAFE_ID.fullmatch(closure_id) or not closure_id.startswith("closure:"):
        raise ValueError("closure_id is invalid")
    offer_id = _text(value.get("offer_id"), "offer_id", max_length=32)
    if not offer_id.isdigit() or int(offer_id) <= 0:
        raise ValueError("offer_id is invalid")
    revision = value.get("revision")
    if type(revision) is not int or revision <= 0:
        raise ValueError("revision is invalid")
    plan_id = _text(value.get("plan_id"), "plan_id")
    snapshot_digest = _text(value.get("snapshot_digest"), "snapshot_digest", max_length=71)
    if not _DIGEST.fullmatch(snapshot_digest):
        raise ValueError("snapshot_digest is invalid")
    if value.get("business_complete") is not True:
        raise ValueError("business_complete must be true")
    closure_status = value.get("closure_status")
    if closure_status not in {"CLOSED", "CLOSED_WITH_OPEN_ITEMS"}:
        raise ValueError("closure_status is invalid")
    recorded_at = _timestamp(value.get("recorded_at"), "recorded_at")
    recorded_by = _text(value.get("recorded_by"), "recorded_by", max_length=80)

    raw_targets = value.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError("publication closure targets are required")
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_targets:
        if not isinstance(raw, Mapping) or set(raw) != {
            "target_label", "source_status", "resolution", "evidence_code",
            "source_run_id", "platform_identity_bound", "manual_handoff",
        }:
            raise ValueError("publication closure target fields are invalid")
        label = _text(raw.get("target_label"), "target_label")
        if label in seen or ":" not in label:
            raise ValueError("publication closure target identity is invalid")
        seen.add(label)
        status = _text(raw.get("source_status"), "source_status", max_length=16)
        resolution = _text(raw.get("resolution"), "resolution", max_length=40)
        if status not in _SOURCE_STATUSES or resolution not in _RESOLUTIONS:
            raise ValueError("publication closure target state is invalid")
        if resolution == "OFFICIAL_READBACK_VERIFIED" and status != "PUBLISHED":
            raise ValueError("verified closure target must be published")
        identity_bound = raw.get("platform_identity_bound")
        if type(identity_bound) is not bool:
            raise TypeError("platform_identity_bound must be boolean")
        handoff = raw.get("manual_handoff")
        if resolution == "MANUAL_HANDOFF_ACCEPTED":
            if not isinstance(handoff, Mapping) or set(handoff) != {"accepted_by", "accepted_at", "note"}:
                raise ValueError("manual handoff evidence is required")
            handoff = {
                "accepted_by": _text(handoff.get("accepted_by"), "manual accepted_by", max_length=80),
                "accepted_at": _timestamp(handoff.get("accepted_at"), "manual accepted_at"),
                "note": _text(handoff.get("note"), "manual handoff note"),
            }
        elif handoff is not None:
            raise ValueError("manual handoff is only valid for manual resolution")
        targets.append(
            {
                "target_label": label,
                "source_status": status,
                "resolution": resolution,
                "evidence_code": _text(raw.get("evidence_code"), "evidence_code", max_length=80),
                "source_run_id": _text(raw.get("source_run_id"), "source_run_id", max_length=160),
                "platform_identity_bound": identity_bound,
                "manual_handoff": handoff,
            }
        )

    raw_sources = value.get("source_reports")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("source reports are required")
    sources: list[dict[str, str]] = []
    source_runs: set[str] = set()
    for raw in raw_sources:
        if not isinstance(raw, Mapping) or set(raw) != {"run_id", "report_path", "report_digest"}:
            raise ValueError("source report fields are invalid")
        run_id = _text(raw.get("run_id"), "source run_id", max_length=160)
        digest = _text(raw.get("report_digest"), "source report digest", max_length=71)
        if run_id in source_runs or not _DIGEST.fullmatch(digest):
            raise ValueError("source report identity is invalid")
        source_runs.add(run_id)
        sources.append({"run_id": run_id, "report_path": _safe_relative_path(raw.get("report_path")), "report_digest": digest})
    if any(row["source_run_id"] not in source_runs for row in targets):
        raise ValueError("closure target references an unknown source report")

    summary = {
        "target_count": len(targets),
        "verified_count": sum(row["resolution"] == "OFFICIAL_READBACK_VERIFIED" for row in targets),
        "manual_handoff_count": sum(row["resolution"] == "MANUAL_HANDOFF_ACCEPTED" for row in targets),
        "processing_count": sum(row["resolution"] == "OPEN_PROCESSING" for row in targets),
        "failed_count": sum(row["resolution"] == "FAILED" for row in targets),
    }
    if value.get("summary") != summary:
        raise ValueError("publication closure summary conflicts")
    expected_status = "CLOSED" if summary["processing_count"] == summary["failed_count"] == 0 else "CLOSED_WITH_OPEN_ITEMS"
    if closure_status != expected_status:
        raise ValueError("publication closure status conflicts")
    body = {
        "schema_version": CLOSURE_SCHEMA_VERSION,
        "closure_id": closure_id,
        "offer_id": offer_id,
        "revision": revision,
        "plan_id": plan_id,
        "snapshot_digest": snapshot_digest,
        "business_complete": True,
        "closure_status": closure_status,
        "recorded_at": recorded_at,
        "recorded_by": recorded_by,
        "summary": summary,
        "targets": targets,
        "source_reports": sources,
    }
    digest = _text(value.get("closure_digest"), "closure_digest", max_length=71)
    if digest != _sha256(body):
        raise ValueError("publication closure digest conflicts")
    return {**body, "closure_digest": digest}


def build_publication_closure(
    *,
    offer_id: str,
    revision: int,
    plan_id: str,
    snapshot_digest: str,
    recorded_at: str,
    recorded_by: str,
    targets: Sequence[Mapping[str, object]],
    source_reports: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    target_rows = [deepcopy(dict(row)) for row in targets]
    source_rows = [deepcopy(dict(row)) for row in source_reports]
    summary = {
        "target_count": len(target_rows),
        "verified_count": sum(row.get("resolution") == "OFFICIAL_READBACK_VERIFIED" for row in target_rows),
        "manual_handoff_count": sum(row.get("resolution") == "MANUAL_HANDOFF_ACCEPTED" for row in target_rows),
        "processing_count": sum(row.get("resolution") == "OPEN_PROCESSING" for row in target_rows),
        "failed_count": sum(row.get("resolution") == "FAILED" for row in target_rows),
    }
    core = {
        "offer_id": offer_id,
        "revision": revision,
        "plan_id": plan_id,
        "snapshot_digest": snapshot_digest,
        "recorded_at": recorded_at,
        "recorded_by": recorded_by,
        "targets": target_rows,
        "source_reports": source_rows,
    }
    closure_id = "closure:" + hashlib.sha256(_canonical(core).encode("utf-8")).hexdigest()[:24]
    body = {
        "schema_version": CLOSURE_SCHEMA_VERSION,
        "closure_id": closure_id,
        **core,
        "business_complete": True,
        "closure_status": "CLOSED" if summary["processing_count"] == summary["failed_count"] == 0 else "CLOSED_WITH_OPEN_ITEMS",
        "summary": summary,
    }
    ordered = {
        key: body[key]
        for key in (
            "schema_version", "closure_id", "offer_id", "revision", "plan_id",
            "snapshot_digest", "business_complete", "closure_status", "recorded_at",
            "recorded_by", "summary", "targets", "source_reports",
        )
    }
    return validate_publication_closure({**ordered, "closure_digest": _sha256(ordered)})


def store_publication_closure(
    closure: Mapping[str, object], *, root: str | os.PathLike[str] = DEFAULT_CLOSURE_ROOT
) -> Path:
    safe = validate_publication_closure(closure)
    folder = safe["closure_id"].replace(":", "-")
    path = Path(root) / safe["offer_id"] / str(safe["revision"]) / folder / "closure-report.json"
    # Schema-only compatibility storage. Application consumers use the bound
    # service below; immutable bytes alone do not establish source truth.
    content = (json.dumps(safe, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return persist_immutable_bytes(path, content, root=Path(root))


def latest_publication_closure(
    offer_id: str, *, root: str | os.PathLike[str] = DEFAULT_CLOSURE_ROOT,
    plan_id: str | None = None,
    source_report_store=None,
) -> dict[str, Any] | None:
    if not isinstance(offer_id, str) or not offer_id.isdigit() or int(offer_id) <= 0:
        raise ValueError("offer_id is invalid")
    if plan_id is not None:
        _text(plan_id, "plan_id")
    base = Path(root) / str(offer_id)
    candidates: list[dict[str, Any]] = []
    if require_local_path(base, root=Path(root), allow_directory=True) is None:
        return None
    for revision in base.iterdir():
        if not revision.name.isdigit():
            continue
        require_local_path(revision, root=Path(root), allow_directory=True)
        if not revision.is_dir():
            raise ValueError("closure revision directory is invalid")
        for folder in revision.iterdir():
            if not folder.name.startswith("closure-"):
                continue
            path = folder / "closure-report.json"
            if require_local_path(path, root=Path(root)) is None:
                if source_report_store is not None:
                    source = source_report_store.get_report_by_path(offer_id=offer_id,
                        report_path=(folder / 'report.json').relative_to(Path(root)).as_posix())
                    if (source is not None and source['offer_id']==offer_id
                            and str(source['revision'])==revision.name and source['run_id']==folder.name):
                        continue
                raise ValueError("closure history report is missing")
            row = validate_publication_closure(json.loads(path.read_text(encoding="utf-8")))
            if (row["offer_id"] != offer_id or str(row["revision"]) != revision.name
                    or row["closure_id"].replace(":", "-") != folder.name):
                raise ValueError("closure history path identity conflicts")
            if plan_id is None or row["plan_id"] == plan_id:
                candidates.append(row)
    candidates.sort(key=lambda row: _instant(row["recorded_at"]), reverse=True)
    if len(candidates)>1 and _instant(candidates[0]["recorded_at"]) == _instant(candidates[1]["recorded_at"]):
        raise ValueError("closure history ordering conflicts")
    return candidates[0] if candidates else None


def _instant(value):
    return datetime.fromisoformat(_timestamp(value, "evidence time").replace("Z", "+00:00")).astimezone(timezone.utc)


class PublicationClosureService:
    """Evidence-bound application boundary; all stores and roots are explicit.

    prepare is read-only. record re-prepares from durable evidence before using
    the original immutable closure store. No provider or business dispatch path.
    """

    def __init__(self, *, release_store, run_store, report_store, authority_root, closure_root):
        self.release_store = release_store
        self.run_store = run_store
        self.report_store = report_store
        self.authority_root = Path(authority_root)
        self.closure_root = Path(closure_root)

    def prepare(self, *, offer_id, plan_id, recorded_by, recorded_at, manual_handoffs=None):
        from shared_platform import publication_autopilot as authority
        from shared_platform.publication_status_projection import project_execution
        from shared_platform.product_publication_reports import _digest

        for store in (self.release_store, self.run_store, self.report_store):
            require_local_path(store.path, root=store.path.parent)
        require_local_path(self.authority_root, root=self.authority_root, allow_directory=True)
        require_local_path(self.report_store.reports_root, root=self.report_store.reports_root, allow_directory=True)
        _text(recorded_by, "recorded_by", max_length=80)
        recorded = _instant(recorded_at)
        handoffs = deepcopy(manual_handoffs) if manual_handoffs is not None else {}
        if not isinstance(handoffs, dict):
            raise ValueError("manual_handoffs must be target-bound inputs")
        plan = self.release_store.get_plan(plan_id)
        if plan is None or plan['product_id'] != offer_id:
            raise ValueError("closure plan identity conflicts")
        if set(handoffs) - set(plan['targets']):
            raise ValueError("manual handoff target is outside approved scope")
        snapshot = self.release_store.approved_publication_snapshot(offer_id=offer_id, plan_id=plan_id)
        platforms = tuple(dict.fromkeys(label.split(':')[0].upper() for label in plan['targets']))
        candidate, approval = authority.resolve_persisted_execution_authority(snapshot=snapshot,
            platform_scope=platforms, target_labels=plan['targets'], reports_root=self.authority_root)
        projection = project_execution(plan=plan, snapshot=snapshot, candidate=candidate, approval=approval,
            run_store=self.run_store, report_store=self.report_store, authority_root=self.authority_root)
        blockers = []
        if plan['status'] != 'APPROVED':
            blockers.append({'target_label':None,'code':'PLAN_NOT_ACTIVE_APPROVED'})
        targets, sources = [], {}
        for row in projection['target_results']:
            label = row['target_label']
            if (row['status'] not in {'PUBLISHED','PROCESSING','FAILED'}
                    or row['outcome_unknown'] is not False or row['readback_completed'] is not True
                    or not row['source'] or not row['source'].get('report_digest')):
                blockers.append({'target_label':label,'code':'TARGET_EVIDENCE_NOT_CLOSABLE',
                                 'status':row['status'],'evidence_blockers':row['blockers']})
                continue
            source = row['source']
            report = self.report_store.get_report_by_run(run_id=source['run_id'])
            if report is None or _digest(report) != source['report_digest']:
                raise ValueError("closure source changed during preparation")
            if recorded < _instant(report['created_at']):
                blockers.append({'target_label':label,'code':'CLOSURE_PRECEDES_SOURCE_REPORT'})
                continue
            handoff = handoffs.get(label)
            if label in handoffs:
                if not isinstance(handoff, Mapping) or set(handoff) != {'accepted_by','accepted_at','note'}:
                    raise ValueError("manual handoff evidence is required")
                _text(handoff['accepted_by'], 'manual accepted_by', max_length=80)
                _text(handoff['note'], 'manual handoff note')
                accepted = _instant(handoff['accepted_at'])
                if accepted > recorded or accepted < _instant(report['created_at']):
                    raise ValueError("manual handoff time conflicts with source evidence")
                resolution = 'MANUAL_HANDOFF_ACCEPTED'
            elif row['official_success']:
                resolution = 'OFFICIAL_READBACK_VERIFIED'
            elif row['reported_status'] == 'PROCESSING':
                resolution = 'OPEN_PROCESSING'
            else:
                resolution = 'FAILED'
            evidence = next(t['evidence'] for t in report['targets'] if t['target_label']==label)
            targets.append({'target_label':label,'source_status':row['reported_status'],
                'resolution':resolution,'evidence_code':evidence['provider_code'],
                'source_run_id':source['run_id'],'platform_identity_bound':row['official_success'],
                'manual_handoff':handoff})
            sources[source['run_id']] = {'run_id':source['run_id'],'report_path':report['report_path'],
                                         'report_digest':'sha256:' + source['report_digest'].removeprefix('sha256:')}
        result = {'status':'BLOCKED' if blockers else 'READY_TO_RECORD','target_results':projection['target_results'],
                  'blockers':blockers,'closure':None,'input_digest':None,'writes_performed':[]}
        if blockers:
            return result
        result['closure'] = build_publication_closure(offer_id=offer_id,revision=snapshot['product_revision'],
            plan_id=plan_id,snapshot_digest=snapshot['snapshot_digest'],recorded_by=recorded_by,
            recorded_at=recorded_at,targets=targets,source_reports=list(sources.values()))
        result['input_digest'] = _sha256({'closure':result['closure'], 'projection':projection,
            'candidate_digest':candidate['candidate_digest'],'approval_digest':approval['approval_digest']})
        return result

    def _refresh(self, document):
        safe = validate_publication_closure(document)
        current = self.prepare(offer_id=safe['offer_id'],plan_id=safe['plan_id'],recorded_by=safe['recorded_by'],
            recorded_at=safe['recorded_at'], manual_handoffs={row['target_label']:row['manual_handoff']
                for row in safe['targets'] if row['resolution']=='MANUAL_HANDOFF_ACCEPTED'})
        if current['status'] != 'READY_TO_RECORD' or current['closure'] != safe:
            raise ValueError("closure does not match current bound publication evidence")
        return current

    def record(self, prepared):
        if not isinstance(prepared, Mapping) or prepared.get('status') != 'READY_TO_RECORD' or not prepared.get('input_digest'):
            raise ValueError("a bound ready closure preview is required")
        current = self._refresh(prepared['closure'])
        if current['input_digest'] != prepared['input_digest']:
            raise ValueError("closure evidence changed since preparation")
        return store_publication_closure(current['closure'],root=self.closure_root)

    def latest(self, *, offer_id, plan_id):
        require_local_path(self.report_store.path,root=self.report_store.path.parent)
        document = latest_publication_closure(offer_id,plan_id=plan_id,root=self.closure_root,
            source_report_store=self.report_store if self.closure_root==self.report_store.reports_root else None)
        if document is not None:
            self._refresh(document)
        return document


__all__ = [
    "CLOSURE_SCHEMA_VERSION",
    "build_publication_closure",
    "latest_publication_closure",
    "store_publication_closure",
    "validate_publication_closure",
    "PublicationClosureService",
]
