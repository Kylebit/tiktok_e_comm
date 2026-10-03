"""Append-only proof that a failed publication run reached no provider boundary."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from shared_platform.product_publication_runs import (
    DEFAULT_PRODUCT_PUBLICATION_RUN_DB,
    ProductPublicationRunIntegrityError,
    ProductPublicationRunStore,
)

SCHEMA_VERSION = "product-publication-zero-write-reconciliation/v1"
READBACK_SCHEMA_VERSION = "product-publication-zero-write-official-readback/v1"
_PRE_PROVIDER_FAILURES = frozenset({"RUNNER_INFRASTRUCTURE_FAILED", "WORKER_LAUNCH_FAILED"})
_AUTHORITY = {"OZON": "OZON_OFFICIAL_SELLER_API", "SHOPEE": "SHOPEE_OFFICIAL_SHOP_API"}
_SCOPES = {"OZON": ("ALL",), "SHOPEE": ("NORMAL", "UNLIST", "BANNED")}
_HEX = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS product_publication_run_reconciliations (
 run_id TEXT PRIMARY KEY,
 receipt_json TEXT NOT NULL,
 receipt_digest TEXT NOT NULL,
 created_at TEXT NOT NULL,
 FOREIGN KEY (run_id) REFERENCES product_publication_runs(run_id)
);
"""


class ProductPublicationRunReconciliationError(RuntimeError):
    pass


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _time(value: object, name: str) -> datetime:
    if type(value) is not str:
        raise ValueError(f"{name} is invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _hex(value: object, name: str) -> str:
    if type(value) is not str or not _HEX.fullmatch(value.removeprefix("sha256:")):
        raise ValueError(f"{name} is invalid")
    return value.removeprefix("sha256:")


class ProductPublicationRunReconciliationStore:
    """Validates and stores immutable zero-write reconciliation receipts."""

    def __init__(self, path: str | Path = DEFAULT_PRODUCT_PUBLICATION_RUN_DB) -> None:
        self.path = Path(path)

    def _readonly(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    def _validate_input(self, *, run_id: str, identity: Mapping[str, Any],
                        ordered_target_labels: Sequence[str], official_readback: Mapping[str, Any],
                        evidence_digests: Sequence[str], registered_by: str) -> dict[str, Any]:
        run = ProductPublicationRunStore(self.path).get_run_by_id(run_id=run_id)
        if run is None:
            raise ValueError("publication run not found")
        if run["state"] != "FAILED" or run["final_report_id"] is not None:
            raise ValueError("only a failed run without a final report may be reconciled")
        if run["failure_code"] not in _PRE_PROVIDER_FAILURES:
            raise ValueError("failure code is not a proven pre-provider boundary")
        expected_identity = {key: run[key] for key in (
            "run_id", "report_id", "offer_id", "revision", "plan_id", "snapshot_digest",
            "platform_scope", "target_count", "execution_identity")}
        if dict(identity) != expected_identity:
            raise ValueError("reconciliation run identity conflicts")
        labels = list(ordered_target_labels)
        platform = run["platform_scope"][0]
        if (len(labels) != run["target_count"] or len(set(labels)) != len(labels)
                or any(type(x) is not str or x.split(":", 1)[0].upper() != platform for x in labels)):
            raise ValueError("ordered target labels conflict with run scope")
        if platform not in _AUTHORITY:
            raise ValueError("platform has no supported official zero-write readback")
        rb = dict(official_readback)
        rb_digest = rb.pop("evidence_digest", None)
        if _hex(rb_digest, "official readback evidence_digest") != _digest(rb):
            raise ValueError("official readback evidence digest conflicts")
        if (rb.get("schema_version") != READBACK_SCHEMA_VERSION
                or rb.get("authority") != _AUTHORITY[platform]
                or rb.get("seller_sku") is None
                or _time(rb.get("observed_at"), "official readback observed_at") <= _time(run["updated_at"], "run updated_at")):
            raise ValueError("official readback identity or time conflicts")
        rows = rb.get("targets")
        if not isinstance(rows, list) or [r.get("target_label") for r in rows] != labels:
            raise ValueError("official readback ordered targets conflict")
        for row in rows:
            if (set(row) != {"target_label", "classification", "complete", "scopes", "evidence_digest"}
                    or row["classification"] != "NOT_FOUND" or row["complete"] is not True
                    or tuple(row["scopes"]) != _SCOPES[platform]):
                raise ValueError("official readback does not prove complete absence")
            _hex(row["evidence_digest"], "target evidence_digest")
        safe_evidence = [_hex(value, "evidence_digest") for value in evidence_digests]
        if not safe_evidence or len(set(safe_evidence)) != len(safe_evidence):
            raise ValueError("evidence_digests must be non-empty and unique")
        if type(registered_by) is not str or not registered_by.strip() or len(registered_by) > 128:
            raise ValueError("registered_by is invalid")
        with self._readonly() as conn:
            events = conn.execute("SELECT sequence,state,failure_code,event_digest FROM product_publication_run_events WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
            if [(e["sequence"], e["state"]) for e in events] != [(1, "QUEUED"), (2, "FAILED")]:
                raise ValueError("run did not fail directly from QUEUED before provider access")
            if events[-1]["failure_code"] != run["failure_code"]:
                raise ProductPublicationRunIntegrityError("terminal event conflicts with run")
            try:
                report = conn.execute("SELECT 1 FROM product_publication_reports WHERE run_id=? LIMIT 1", (run_id,)).fetchone()
            except sqlite3.OperationalError:
                report = None
            if report is not None:
                raise ValueError("immutable publication report already exists")
            terminal_digest = _hex(events[-1]["event_digest"], "terminal_event_digest")
        core = {
            "schema_version": SCHEMA_VERSION, "run_identity": expected_identity,
            "ordered_target_labels": labels, "failure_code": run["failure_code"],
            "failure_boundary": "PRE_RUNNING", "terminal_event_digest": terminal_digest,
            "provider_request_attempted": False, "external_write_count": 0,
            "official_readback": {**rb, "evidence_digest": rb_digest.removeprefix("sha256:")},
            "evidence_digests": safe_evidence, "registered_by": registered_by.strip(),
        }
        return core

    def prepare(self, **kwargs: Any) -> dict[str, Any]:
        core = self._validate_input(**kwargs)
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
            existing = conn.execute("SELECT receipt_json,receipt_digest FROM product_publication_run_reconciliations WHERE run_id=?", (kwargs["run_id"],)).fetchone()
            if existing:
                if existing["receipt_digest"] != receipt["receipt_digest"] or json.loads(existing["receipt_json"]) != receipt:
                    raise ProductPublicationRunReconciliationError("reconciliation receipt conflicts with existing append-only record")
                return receipt
            conn.execute("INSERT INTO product_publication_run_reconciliations(run_id,receipt_json,receipt_digest,created_at) VALUES(?,?,?,?)",
                         (kwargs["run_id"], _canonical(receipt), receipt["receipt_digest"], now))
            conn.commit()
        return receipt

    def get(self, *, run_id: str) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            with self._readonly() as conn:
                row = conn.execute("SELECT receipt_json,receipt_digest FROM product_publication_run_reconciliations WHERE run_id=?", (run_id,)).fetchone()
        except sqlite3.OperationalError:
            return None
        if row is None:
            return None
        receipt = json.loads(row["receipt_json"])
        core = dict(receipt); digest = core.pop("receipt_digest", None)
        if digest != row["receipt_digest"] or digest != _digest(core):
            raise ProductPublicationRunReconciliationError("reconciliation receipt digest does not match")
        verified = self._validate_input(
            run_id=receipt["run_identity"]["run_id"], identity=receipt["run_identity"],
            ordered_target_labels=receipt["ordered_target_labels"],
            official_readback=receipt["official_readback"],
            evidence_digests=receipt["evidence_digests"], registered_by=receipt["registered_by"])
        if verified != core:
            raise ProductPublicationRunReconciliationError("reconciliation receipt facts do not match durable run")
        return receipt


__all__ = ["ProductPublicationRunReconciliationError", "ProductPublicationRunReconciliationStore",
           "READBACK_SCHEMA_VERSION", "SCHEMA_VERSION"]
