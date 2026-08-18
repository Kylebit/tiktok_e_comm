"""Explicit, immutable local knowledge storage for approved monthly reports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


FORBIDDEN_KEY_FRAGMENTS = ("token", "cookie", "authorization", "raw_response", "api_key")


@dataclass(frozen=True)
class StoredKnowledgeReport:
    knowledge_id: str
    path: Path
    created: bool
    payload: Mapping[str, Any]


class ProfitKnowledgeBase:
    """Store only explicitly approved, audit-ready monthly report artifacts."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def approve_monthly_report(
        self,
        report: Mapping[str, Any],
        *,
        approved_by: str,
        approved_at: str,
        approval_note: str = "",
    ) -> StoredKnowledgeReport:
        payload = _copy_json(report)
        if str(payload.get("period_kind") or "") != "monthly":
            raise ValueError("only monthly profit reports can enter the knowledge base")
        if str(payload.get("status") or "") != "ready":
            raise ValueError("monthly profit report must be ready before approval")
        from domains.data_operations.profit_settlement.audit import audit_profit_report
        audit = audit_profit_report(payload)
        if audit.status != "PASSED":
            codes = ",".join(sorted({finding.code for finding in audit.findings}))
            raise ValueError(f"monthly profit report failed second-pass audit: {codes}")
        platform = str(payload.get("platform") or "").strip().lower()
        if platform not in {"tiktok", "shopee", "ozon"}:
            raise ValueError("unsupported profit report platform")
        approver = str(approved_by or "").strip()
        timestamp = str(approved_at or "").strip()
        if not approver or not timestamp:
            raise ValueError("approved_by and approved_at are required")
        _reject_secrets(payload)
        start = str((payload.get("period") or {}).get("start") or "")
        try:
            year, month = int(start[:4]), int(start[5:7])
        except (TypeError, ValueError):
            raise ValueError("monthly report has an invalid period start") from None
        report_checksum = _checksum(payload)
        approval = {
            "status": "APPROVED",
            "approved_by": approver,
            "approved_at": timestamp,
            "approval_note": str(approval_note or "").strip(),
            "report_checksum": report_checksum,
            "audit": audit.payload(),
        }
        knowledge_id = f"profit-knowledge-{_checksum({'report': report_checksum, 'approval': approval})[:20]}"
        artifact = {
            "schema_version": "profit-knowledge-entry/v1",
            "knowledge_id": knowledge_id,
            "platform": platform,
            "year": year,
            "month": month,
            "approval": approval,
            "report": payload,
        }
        destination = self.root / platform / f"{year:04d}" / f"{month:02d}" / f"{knowledge_id}.json"
        if destination.is_file():
            existing = json.loads(destination.read_text(encoding="utf-8"))
            if existing != artifact:
                raise RuntimeError("immutable knowledge artifact conflicts with existing content")
            return StoredKnowledgeReport(knowledge_id, destination, False, existing)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(destination, artifact)
        self._update_index(artifact, destination)
        return StoredKnowledgeReport(knowledge_id, destination, True, artifact)

    def list_reports(
        self,
        *,
        platform: str | None = None,
        year: int | None = None,
        month: int | None = None,
    ) -> list[dict[str, Any]]:
        index_path = self.root / "index.json"
        if not index_path.is_file():
            return []
        rows = json.loads(index_path.read_text(encoding="utf-8"))
        return [
            row
            for row in rows
            if (platform is None or row.get("platform") == platform.lower())
            and (year is None or row.get("year") == year)
            and (month is None or row.get("month") == month)
        ]

    def _update_index(self, artifact: Mapping[str, Any], destination: Path) -> None:
        path = self.root / "index.json"
        rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        if any(item.get("knowledge_id") == artifact["knowledge_id"] for item in rows):
            return
        report = artifact["report"]
        rows.append({
            "knowledge_id": artifact["knowledge_id"],
            "platform": artifact["platform"],
            "year": artifact["year"],
            "month": artifact["month"],
            "report_id": report.get("report_id"),
            "status": report.get("status"),
            "totals": report.get("totals"),
            "approval": artifact["approval"],
            "artifact_path": str(destination.relative_to(self.root)).replace("\\", "/"),
        })
        rows.sort(key=lambda item: (item["platform"], item["year"], item["month"], item["knowledge_id"]))
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_json(path, rows)


def _reject_secrets(value: object, path: str = "report") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(fragment in normalized for fragment in FORBIDDEN_KEY_FRAGMENTS):
                raise ValueError(f"knowledge report contains forbidden secret/raw field: {path}.{key}")
            _reject_secrets(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secrets(item, f"{path}[{index}]")


def _copy_json(value: object) -> dict[str, Any]:
    copied = json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if not isinstance(copied, dict):
        raise TypeError("profit report must be a mapping")
    return copied


def _checksum(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _atomic_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
