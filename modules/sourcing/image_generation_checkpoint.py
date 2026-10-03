"""Local, single-writer image checkpoints. No provider or credential access."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlparse


SCHEMA = "image-generation-checkpoint/v2"
PROVIDER = "lingshi-media/v1"
_STATES = {"READY", "SUBMITTING", "SUBMISSION_UNKNOWN", "SUBMITTED", "FAILED", "SUPERSEDED",
           "REJECTED_BEFORE_TASK", "COMPLETED", "RECOVERY_REQUIRED"}
_UNRESOLVED = {"SUBMITTING", "SUBMISSION_UNKNOWN", "SUBMITTED", "RECOVERY_REQUIRED", "REJECTED_BEFORE_TASK"}
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_NAME = re.compile(r"lingshi-(brand|localized)-v2-([0-9a-f]{24})\.json")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CheckpointRecoveryRequired(RuntimeError):
    def __init__(self, path: Path, reason: str):
        self.checkpoint_path = str(path)
        super().__init__(f"{reason}; inspect/reconcile local checkpoint {path}")


class CheckpointPersistenceError(RuntimeError):
    def __init__(self, path: Path, task_id: int | None):
        self.checkpoint_path = str(path)
        self.task_id = task_id
        super().__init__(f"checkpoint persistence failed; reconcile before retry (task_id={task_id})")


class CheckpointOwnershipTimeError(ValueError):
    """Local timestamp diagnostics only; no new ownership or execution authority."""

    def __init__(self, reason: str, diagnostics: Mapping[str, Any]):
        self.reason = reason
        self.diagnostics = dict(diagnostics)
        super().__init__(f"ownership verification time rejected: {reason}; {canonical(self.diagnostics)}")


class ImageTaskFailed(RuntimeError):
    """Structured terminal task failure; provider prose is not a state signal."""


class ImageSubmissionRejected(RuntimeError):
    """Structured non-success create response without a task ID; reconcile before retry."""


def atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    for attempt in range(20):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            # Windows peer ownership reads may briefly hold the destination open.
            # Retry only the local atomic rename; never a paid request or journal append.
            if os.name != "nt" or attempt == 19:
                raise
            time.sleep(0.005)


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_bytes(path, (canonical(value) + "\n").encode("utf-8"))


@contextmanager
def business_lock(root: Path, business_digest: str, *, timeout: float = 30):
    """Kernel lock releases on process exit; its persistent file is never deleted."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / f".lingshi-{business_digest[:24]}.lock"
    with _LOCKS_GUARD:
        local = _THREAD_LOCKS.setdefault(str(path.resolve()), threading.Lock())
    if not local.acquire(timeout=timeout):
        raise CheckpointRecoveryRequired(path, "another local writer holds this business identity")
    stream = None
    acquired = False
    failure = None
    try:
        stream = path.open("a+b")
        # Windows permits locking past EOF. Initializing a byte before locking
        # races with another process's lock; the persistent file needs no content.
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise CheckpointRecoveryRequired(path, "another process holds this business identity")
                time.sleep(0.05)
        yield
    except BaseException as error:
        failure = error
        raise
    finally:
        cleanup_errors = []
        try:
            try:
                if stream is not None and acquired:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except BaseException as error:
                cleanup_errors.append(error)
            if stream is not None:
                try:
                    stream.close()
                except BaseException as error:
                    cleanup_errors.append(error)
        finally:
            local.release()
        if cleanup_errors:
            primary = failure if failure is not None else cleanup_errors.pop(0)
            for error in cleanup_errors:
                primary.add_note(f"Lock cleanup also failed: {type(error).__name__}: {error}")
            if failure is None:
                raise primary


def _sealed(record: Mapping[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in record.items() if key != "record_digest"}
    result["record_digest"] = digest(result)
    return result


def _task_id(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _file_sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    except FileNotFoundError:
        # A peer's atomic .tmp -> snapshot rename may finish between stat and read.
        # Absence is not ownership proof: independent sealed records are still required.
        return None


def _audit_reference(value: Any) -> str:
    reference = str(value or "")
    if not re.fullmatch(r"(?:audit|fixture)://[A-Za-z0-9_./:-]{1,220}", reference):
        raise ValueError("evidence reference must be a non-secret audit:// identifier")
    return reference


def _ownership_binding(path: Path) -> dict[str, Any]:
    return {"checkpoint_name": path.name, "checkpoint_sha256": _file_sha(path),
            "identity_sha256": _file_sha(path.with_suffix(".identity")),
            "journal_sha256": _file_sha(path.with_suffix(".events.jsonl")),
            "output_sha256": _file_sha(path.with_suffix(".png")),
            "pending_sha256": _file_sha(path.with_suffix(".json.tmp"))}


def _ownership_path(path: Path) -> Path:
    # Both full identities are retained and checked inside this short path.
    return path.parent / f"owner-{digest(path.name)[:16]}-{digest(_ownership_binding(path))[:16]}.json"


def _read_ownership(path: Path) -> dict[str, Any] | None:
    if not any(path.parent.glob(f"owner-{digest(path.name)[:16]}-*.json")):
        return None
    receipt_path = _ownership_path(path)
    if not receipt_path.exists():
        return None
    try:
        row = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (row != _sealed(row) or row["schema_version"] != "image-checkpoint-ownership/v1"
            or row["source_binding"] != _ownership_binding(path)
            or row["provider"] != PROVIDER
            or row["kind"] not in {"brand", "localized"}
            or (row["kind"] == "brand") != path.name.startswith("lingshi-brand-")
            or not re.fullmatch(r"[0-9a-f]{64}", row["business_digest"])
            or not re.fullmatch(r"[0-9a-f]{64}", row["request_digest"])):
            raise ValueError("ownership receipt binding differs")
        return row
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise CheckpointRecoveryRequired(path, "ownership receipt is damaged or conflicts with its full source binding") from None


def _validated_v2_owner(path: Path) -> dict[str, str] | None:
    """Attribute a peer from independent sealed facts, without repairing it."""
    match = _NAME.fullmatch(path.name)
    if not match:
        raise ValueError("ownership inspection requires an exact checkpoint filename")
    kind, prefix = match.groups()
    owners = []
    metadata = []
    for source in (path.with_suffix(".identity"), path):
        try:
            row = json.loads(source.read_text(encoding="utf-8"))
            if row != _sealed(row):
                continue
            if (row["kind"] != kind or not re.fullmatch(r"[0-9a-f]{64}", row["business_digest"])
                or not re.fullmatch(r"[0-9a-f]{64}", row["request_digest"])
                or row["request_digest"][:24] != prefix):
                raise CheckpointRecoveryRequired(path, "sealed checkpoint identity conflicts with its full path binding")
            if source == path and (row.get("schema_version") != SCHEMA or row.get("provider") != PROVIDER):
                raise CheckpointRecoveryRequired(path, "sealed checkpoint schema/provider conflicts with its identity")
            owners.append({key: row[key] for key in ("kind", "business_digest", "request_digest")})
            metadata.append({key: row.get(key) for key in ("model", "source_identity_complete")})
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    mapped = _read_ownership(path)
    if mapped:
        owners.append({key: mapped[key] for key in ("kind", "business_digest", "request_digest")})
    if owners and any(row != owners[0] for row in owners[1:]):
        raise CheckpointRecoveryRequired(path, "independent checkpoint ownership evidence conflicts; preserve every source")
    if metadata and any(row != metadata[0] for row in metadata[1:]):
        raise CheckpointRecoveryRequired(path, "independent checkpoint identity metadata conflicts; preserve every source")
    return owners[0] if owners else None


def inspect_checkpoint_ownership(path: str | Path) -> dict[str, Any]:
    """Read source hashes for explicit local attribution; no task/charge finding."""
    path = Path(path)
    if (not any(_ownership_binding(path)[key] for key in _ownership_binding(path) if key != "checkpoint_name")
        or not path.name.startswith("lingshi-") or path.suffix != ".json"):
        raise ValueError("ownership requires an existing exact image checkpoint file")
    match = _NAME.fullmatch(path.name)
    owner = _validated_v2_owner(path) if match else _read_ownership(path)
    return {"source_binding": _ownership_binding(path),
            "existing_ownership": {key: owner[key] for key in ("kind", "business_digest", "request_digest")} if owner else None}


def _epoch_microseconds(value: datetime) -> int:
    """Exact UTC microseconds; datetime.timestamp() would introduce float rounding."""
    delta = value.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _ownership_verification_time(path: Path, evidence: Mapping[str, Any]) -> tuple[datetime, dict[str, Any]]:
    """Compare within the verified_at format's microsecond precision, with no time window.

    Filesystem nanoseconds in the same microsecond cannot be ordered by this receipt
    format. Hash binding remains exact; the preceding microsecond is still stale.
    """
    observed_at = datetime.now(timezone.utc)
    newest_ns = max(source.stat().st_mtime_ns for source in (
        path, path.with_suffix(".identity"), path.with_suffix(".events.jsonl"),
        path.with_suffix(".png"), path.with_suffix(".json.tmp")) if source.is_file())
    diagnostic = {"precision": "utc-microsecond-floor/v1", "verified_at": None,
                  "verified_epoch_us": None, "observed_at": observed_at.isoformat(),
                  "observed_epoch_us": _epoch_microseconds(observed_at),
                  "newest_source_mtime_ns": newest_ns, "newest_source_epoch_us": newest_ns // 1000}
    try:
        verified_at = datetime.fromisoformat(str(evidence["verified_at"]))
        if verified_at.tzinfo is None or verified_at.utcoffset() is None:
            raise ValueError("timezone required")
    except (KeyError, TypeError, ValueError):
        raise CheckpointOwnershipTimeError("INVALID_VERIFICATION_TIME", {
            **diagnostic, "action": "PROVIDE_TIMEZONE_AWARE_VERIFICATION_AFTER_INSPECTION"}) from None
    verified_us = _epoch_microseconds(verified_at)
    diagnostic.update(verified_at=verified_at.isoformat(), verified_epoch_us=verified_us)
    if verified_us > diagnostic["observed_epoch_us"]:
        raise CheckpointOwnershipTimeError("FUTURE_VERIFICATION_TIME", {
            **diagnostic, "action": "VERIFY_LOCAL_CLOCK_AND_REINSPECT_SOURCE"})
    if verified_us < diagnostic["newest_source_epoch_us"]:
        raise CheckpointOwnershipTimeError("VERIFICATION_PREDATES_SOURCE", {
            **diagnostic, "action": "REINSPECT_SOURCE_AND_VERIFY_CURRENT_BYTES"})
    return verified_at, diagnostic


def bind_checkpoint_ownership(path: str | Path, *, business_identity: Mapping[str, Any], request_digest: str,
                              evidence: Mapping[str, Any], legacy_identity: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Bind upstream-verified ownership only; never clear UNKNOWN or attach a task.

    v1 needs the complete original identity whose hash the old producer stored.
    All original checkpoint/sidecar/journal/output bytes remain unchanged.
    """
    path = Path(path)
    match = _NAME.fullmatch(path.name)
    kind = match.group(1) if match else ("brand" if path.name.startswith("lingshi-brand-") else "localized")
    required = {"offer_id", "brand_id", "role"} if kind == "brand" else {"source_url", "locale"}
    if kind=='localized' and match and set(business_identity)=={'offer_id','brand_id','role','locale'}:
        required={'offer_id','brand_id','role','locale'}
    if (set(business_identity) != required or any(not isinstance(v, str) or not v.strip() for v in business_identity.values())
        or not re.fullmatch(r"[0-9a-f]{64}", str(request_digest))):
        raise ValueError("ownership requires the complete stable business identity and request SHA256")
    business_digest = digest({"kind": kind, **business_identity})
    with business_lock(path.parent, digest("checkpoint-ownership-registry")):
        observed = inspect_checkpoint_ownership(path)
        bound = {**observed, "business_digest": business_digest, "request_digest": request_digest, "provider": PROVIDER}
        if any(evidence.get(key) != value or key not in evidence for key, value in bound.items()):
            raise ValueError("ownership evidence does not bind every current source hash and exact target")
        existing = observed["existing_ownership"]
        expected_owner = {"kind": kind, "business_digest": business_digest, "request_digest": request_digest}
        if existing is not None and existing != expected_owner:
            raise ValueError("ownership cannot override a proven different business/request")
        verified_at, time_validation = _ownership_verification_time(path, evidence)
        if (not str(evidence.get("verified_by") or "").strip()
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(evidence.get("evidence_sha256") or ""))):
            raise ValueError("ownership requires a verifier and hashed evidence")
        reference = _audit_reference(evidence.get("evidence_ref"))
        legacy_digest = None
        if match:
            if match.group(2) != request_digest[:24] or legacy_identity is not None:
                raise ValueError("v2 ownership request does not match the exact path")
        else:
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
                old_identity = dict(legacy_identity or {})
                required_identity = ({"schema_version", "offer_id", "brand_id", "role", "brief", "source_identities",
                                      "product_reference_count", "model", "size", "quality"} if kind == "brand" else
                                     {"schema_version", "source_url", "source_digest", "locale", "translations", "model", "retry_attempt"})
                if set(old_identity) != required_identity:
                    raise ValueError("legacy ownership requires every original producer identity field")
                legacy_digest = digest(old_identity)
                expected_name = f"lingshi-{'brand-' if kind == 'brand' else ''}{legacy_digest}.json"
                expected_schema = f"{'brand' if kind == 'brand' else 'localized'}-image-lingshi-checkpoint/v1"
                expected_renderer = "lingshi-brand-image/v1" if kind == "brand" else "lingshi-localized-image/v1"
                receipt = old.get("receipt") if isinstance(old.get("receipt"), dict) else {}
                if (path.name != expected_name or old.get("schema_version") != expected_schema
                    or old.get("identity_digest") != legacy_digest
                    or old.get("client_business_id") != f"{kind}-{legacy_digest[:40]}"
                    or old_identity.get("schema_version") != expected_renderer
                    or any(old_identity.get(key) != value for key, value in business_identity.items())
                    or receipt.get("provider", PROVIDER) != PROVIDER):
                    raise ValueError("original v1 identity does not match source or business")
                tasks = {_task_id(old.get("task_id")), _task_id(receipt.get("task_id"))} - {None}
                if len(tasks) > 1:
                    raise ValueError("one legacy checkpoint contains conflicting task IDs")
                target = ImageCheckpoint.from_path(path.parent / f"lingshi-{kind}-v2-{request_digest[:24]}.json")
                if target.business_digest != business_digest or target.request_digest != request_digest:
                    raise ValueError("legacy ownership target checkpoint differs")
            except (OSError, TypeError, AttributeError):
                raise ValueError("legacy ownership requires its complete original identity and an existing v2 target") from None
        row = _sealed({"schema_version": "image-checkpoint-ownership/v1", "provider": PROVIDER, **expected_owner,
            "source_binding": observed["source_binding"], "legacy_identity_digest": legacy_digest,
            "verified_at": verified_at.isoformat(), "time_validation": time_validation,
            "verifier_digest": digest(str(evidence["verified_by"])),
            "evidence_ref": reference, "evidence_sha256": evidence["evidence_sha256"],
            "evidence_binding_digest": digest(dict(evidence)), "authority": "OWNERSHIP_ONLY_NO_TASK_OR_CHARGE_FINDING"})
        destination = _ownership_path(path)
        current = _read_ownership(path)
        if current:
            if any(current.get(key) != row[key] for key in ("kind", "business_digest", "request_digest", "source_binding", "legacy_identity_digest")):
                raise ValueError("immutable ownership receipt already binds a different identity")
            return current
        atomic_json(destination, row)
        if _ownership_binding(path) != observed["source_binding"]:
            raise CheckpointRecoveryRequired(path, "source changed while recording ownership; receipt is now stale")
        return row


class ImageCheckpoint:
    def __init__(self, root: str | Path, *, kind: str, business_identity: Mapping[str, Any],
                 request_identity: Mapping[str, Any], model: str, source_identity_complete: bool):
        if kind not in {"brand", "localized"}:
            raise ValueError("unsupported checkpoint kind")
        self.root = Path(root)
        self.kind = kind
        self.business_digest = digest({"kind": kind, **business_identity})
        self.request_digest = digest({"business_digest": self.business_digest, **request_identity})
        self.path = self.root / f"lingshi-{kind}-v2-{self.request_digest[:24]}.json"
        self.model = model
        self.source_identity_complete = source_identity_complete

    @classmethod
    def from_path(cls, path: str | Path) -> "ImageCheckpoint":
        path = Path(path)
        match = _NAME.fullmatch(path.name)
        if not match:
            raise ValueError("recovery requires an exact v2 checkpoint path")
        self = cls.__new__(cls)
        self.path, self.root = path, path.parent
        self.kind, prefix = match.groups()
        try:
            identity = json.loads(self.identity_path.read_text(encoding="utf-8"))
            if (identity != _sealed(identity) or identity["kind"] != self.kind
                or identity["request_digest"][:24] != prefix
                or not re.fullmatch(r"[0-9a-f]{64}", identity["request_digest"])
                or not re.fullmatch(r"[0-9a-f]{64}", identity["business_digest"])):
                raise ValueError("identity mismatch")
            self.business_digest = identity["business_digest"]
            self.request_digest = identity["request_digest"]
            self.model = identity["model"]
            self.source_identity_complete = identity["source_identity_complete"]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise CheckpointRecoveryRequired(path, "identity sidecar is missing or damaged; preserve and restore its verified original") from None
        return self

    @property
    def identity_path(self) -> Path:
        return self.path.with_suffix(".identity")

    def _identity(self) -> dict[str, Any]:
        return _sealed({"kind": self.kind, "business_digest": self.business_digest,
                        "request_digest": self.request_digest, "model": self.model,
                        "source_identity_complete": self.source_identity_complete})

    @property
    def journal_path(self) -> Path:
        return self.path.with_suffix(".events.jsonl")

    @property
    def output_path(self) -> Path:
        return self.path.with_suffix(".png")

    def _initial(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA, "kind": self.kind, "provider": PROVIDER,
                "business_digest": self.business_digest, "request_digest": self.request_digest,
                "identity_digest": self.request_digest, "model": self.model,
                "source_identity_complete": self.source_identity_complete,
                "status": "READY", "attempt": 0, "revision": 0, "task_id": None,
                "updated_at": _now(), "resolved_legacy": {},
                "client_business_id": f"{self.kind}-{self.request_digest[:32]}-a0"}

    def _events(self, *, allow_valid_prefix: bool = False) -> list[dict[str, Any]]:
        if not self.journal_path.exists():
            return []
        events = []
        previous = None
        try:
            for line in self.journal_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                unsigned = {key: value for key, value in event.items() if key != "event_digest"}
                if (event.get("event_digest") != digest(unsigned) or event.get("previous") != previous
                    or event.get("business_digest") != self.business_digest
                    or event.get("request_digest") != self.request_digest
                    or event.get("revision") != len(events) + 1):
                    raise ValueError("journal identity/order mismatch")
                previous = event["event_digest"]
                events.append(event)
        except (ValueError, TypeError, AttributeError, UnicodeError):
            if allow_valid_prefix:
                return events
            raise CheckpointRecoveryRequired(self.path, "damaged or incomplete event journal") from None
        return events

    def read(self) -> dict[str, Any]:
        try:
            if json.loads(self.identity_path.read_text(encoding="utf-8")) != self._identity():
                raise ValueError("full identity differs from its path")
            record = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(record, dict) or record != _sealed(record)
                or record.get("schema_version") != SCHEMA
                or record.get("business_digest") != self.business_digest
                or record.get("request_digest") != self.request_digest
                or record.get("status") not in _STATES
                or type(record.get("revision")) is not int
                or type(record.get("attempt")) is not int
                or not 0 <= record["attempt"] <= 3
                or record.get("model") != self.model):
                raise ValueError("record identity/schema mismatch")
        except (OSError, ValueError, TypeError, UnicodeError):
            raise CheckpointRecoveryRequired(self.path, "damaged or incomplete checkpoint") from None
        events = self._events()
        if record["revision"] > len(events):
            raise CheckpointRecoveryRequired(self.path, "checkpoint is ahead of its durable journal")
        if record["revision"] and events[record["revision"] - 1]["record_digest"] != record["record_digest"]:
            raise CheckpointRecoveryRequired(self.path, "checkpoint/journal revision disagreement")
        for event in events[record["revision"]:]:
            # A task acknowledgement is durable even if the following snapshot
            # replacement failed. Receipt-less COMPLETED must query that task.
            record.update(event["transition"])
            if record["status"] == "COMPLETED":
                record["status"] = "SUBMITTED"
                record.pop("receipt", None)
        return record

    def persist(self, record: Mapping[str, Any], **updates: Any) -> dict[str, Any]:
        events = self._events()
        new = _sealed({**record, **updates, "revision": len(events) + 1, "updated_at": _now()})
        fields = ("status", "attempt", "revision", "task_id", "client_business_id", "updated_at",
                  "failure_kind", "reconciliation", "resolved_legacy", "legacy_checkpoints")
        event = {"business_digest": self.business_digest, "request_digest": self.request_digest,
                 "revision": new["revision"], "previous": events[-1]["event_digest"] if events else None,
                 "record_digest": new["record_digest"],
                 "transition": {key: new[key] for key in fields if key in new}}
        event["event_digest"] = digest(event)
        try:
            with self.journal_path.open("ab") as stream:
                stream.write((canonical(event) + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            atomic_json(self.path, new)
        except OSError:
            raise CheckpointPersistenceError(self.path, _task_id(new.get("task_id"))) from None
        return new

    def legacy_records(self) -> dict[str, dict[str, Any]]:
        records = {}
        for path in sorted(self.root.glob("lingshi-*.json")):
            if "-v2-" in path.name:
                continue
            if (self.kind == "brand") != path.name.startswith("lingshi-brand-"):
                continue
            owner = _read_ownership(path)
            if owner:
                if owner["business_digest"] != self.business_digest:
                    continue
                if owner["request_digest"] != self.request_digest:
                    # Do not let deletion of the mapped v2 target make an old
                    # unresolved task disappear from this business's guard.
                    target = ImageCheckpoint.from_path(self.root / f"lingshi-{self.kind}-v2-{owner['request_digest'][:24]}.json")
                    if target.business_digest != self.business_digest or target.request_digest != owner["request_digest"]:
                        raise CheckpointRecoveryRequired(path, "legacy ownership target differs from its full identity")
                    state = target.read()
                    if (state["status"] in _UNRESOLVED
                        or state.get("resolved_legacy", {}).get(path.name) != _file_sha(path)):
                        raise CheckpointRecoveryRequired(path, "same business legacy task still requires its mapped request recovery")
                    continue
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
                receipt = old.get("receipt") if isinstance(old.get("receipt"), dict) else {}
                task_ids = sorted({_task_id(old.get("task_id")), _task_id(receipt.get("task_id"))} - {None})
                task_id = task_ids[0] if len(task_ids) == 1 else None
                status = old.get("status") if old.get("status") in _STATES else "INVALID"
            except (ValueError, AttributeError, UnicodeError):
                task_id, task_ids, status = None, [], "INVALID"
            records[path.name] = {"sha256": _file_sha(path), "task_id": task_id, "task_ids": task_ids, "status": status}
        return records

    def open_for_execution(self) -> dict[str, Any]:
        if self.path.exists():
            record = self.read()
        else:
            if self.journal_path.exists() or self.output_path.exists() or self.identity_path.exists() or self.path.with_suffix(".json.tmp").exists():
                raise CheckpointRecoveryRequired(self.path, "orphaned checkpoint artifacts require explicit recovery")
            record = self._initial()
            atomic_json(self.identity_path, self._identity())
            atomic_json(self.path, _sealed(record))
        # A lost snapshot must not hide a peer's durable sidecar/journal/output.
        peers = set()
        for artifact in self.root.glob(f"lingshi-{self.kind}-v2-*"):
            match = re.fullmatch(r"(lingshi-(?:brand|localized)-v2-[0-9a-f]{24})\.(?:json|identity|events\.jsonl|png|json\.tmp)", artifact.name)
            if match:
                peers.add(self.root / (match.group(1) + ".json"))
        for peer in sorted(peers):
            if peer == self.path:
                continue
            owner = _validated_v2_owner(peer)
            if owner is None:
                raise CheckpointRecoveryRequired(peer, "checkpoint owner is unproven; bind source hashes and verified ownership locally")
            if owner["business_digest"] != self.business_digest:
                continue
            peer_checkpoint = ImageCheckpoint.from_path(peer)
            other = peer_checkpoint.read()
            if other["status"] in _UNRESOLVED:
                raise CheckpointRecoveryRequired(peer, "same business has an unresolved request; reconcile before changing prompt")
        legacy = self.legacy_records()
        if any(record.get("resolved_legacy", {}).get(name) != item["sha256"] for name, item in legacy.items()):
            if record["status"] != "RECOVERY_REQUIRED" or record.get("legacy_checkpoints") != legacy:
                record = self.persist(record, status="RECOVERY_REQUIRED", legacy_checkpoints=legacy)
            raise CheckpointRecoveryRequired(self.path, "legacy v1 identity is incomplete; caller must verify its relationship")
        return record


def inspect_image_checkpoint(path: str | Path) -> dict[str, Any]:
    """Read-only local evidence for recovery; works without acquiring a writer lock."""
    checkpoint = ImageCheckpoint.from_path(path)
    try:
        state = checkpoint.read()
        valid = True
    except CheckpointRecoveryRequired:
        state, valid = {}, False
    known_tasks = {_task_id(state.get("task_id"))} - {None}
    try:
        snapshot = json.loads(checkpoint.path.read_text(encoding="utf-8"))
        if (snapshot == _sealed(snapshot) and snapshot.get("request_digest") == checkpoint.request_digest
            and snapshot.get("business_digest") == checkpoint.business_digest):
            if not valid:
                state = {key: snapshot.get(key) for key in ("attempt", "revision", "task_id", "updated_at", "resolved_legacy")}
            if snapshot.get("attempt") == state.get("attempt"):
                known_tasks.update({_task_id(snapshot.get("task_id"))} - {None})
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    try:
        events = checkpoint._events(allow_valid_prefix=True)
        if not valid and events and events[-1]["revision"] >= (state.get("revision") or 0):
            state.update({key: events[-1]["transition"].get(key) for key in
                          ("attempt", "revision", "task_id", "updated_at", "resolved_legacy")})
            known_tasks = {_task_id(state.get("task_id"))} - {None}
        for event in events:
            if state.get("attempt") is None or event["transition"].get("attempt") == state.get("attempt"):
                known_tasks.update({_task_id(event["transition"].get("task_id"))} - {None})
    except CheckpointRecoveryRequired:
        pass
    return {"checkpoint_path": str(checkpoint.path), "record_valid": valid,
            "business_digest": checkpoint.business_digest, "request_digest": checkpoint.request_digest,
            "provider": PROVIDER, "checkpoint_digest": _file_sha(checkpoint.path),
            "identity_digest": _file_sha(checkpoint.identity_path),
            "journal_digest": _file_sha(checkpoint.journal_path),
            "status": state.get("status", "DAMAGED_OR_INCOMPLETE"),
            "revision": state.get("revision"), "attempt": state.get("attempt"),
            "task_id": state.get("task_id"), "updated_at": state.get("updated_at"),
            "known_task_ids": sorted(known_tasks), "output_digest": state.get("output_digest"),
            "resolved_legacy": state.get("resolved_legacy", {}),
            "legacy_checkpoint_digests": {name: row["sha256"] for name, row in checkpoint.legacy_records().items()}}


def reconcile_image_checkpoint(path: str | Path, *, evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Apply upstream-verified evidence locally. Never queries or creates a task."""
    checkpoint = ImageCheckpoint.from_path(path)
    with business_lock(checkpoint.root, checkpoint.business_digest):
        observed = inspect_image_checkpoint(path)
        bound = ("business_digest", "request_digest", "provider", "checkpoint_digest", "journal_digest", "identity_digest",
                 "revision", "attempt", "status", "known_task_ids", "output_digest", "legacy_checkpoint_digests")
        if any(key not in evidence or evidence[key] != observed[key] for key in bound):
            raise ValueError("reconciliation evidence does not bind the current checkpoint revision and request")
        try:
            verified_at = datetime.fromisoformat(str(evidence["verified_at"]))
            if verified_at.tzinfo is None or verified_at > datetime.now(timezone.utc):
                raise ValueError("invalid verification time")
            if observed["updated_at"] and verified_at < datetime.fromisoformat(observed["updated_at"]):
                raise ValueError("verification predates checkpoint")
        except (ValueError, KeyError, TypeError):
            raise ValueError("reconciliation requires a current timezone-aware verification time") from None
        if (not str(evidence.get("verified_by") or "").strip()
            or not str(evidence.get("evidence_ref") or "").strip()
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(evidence.get("evidence_sha256") or ""))):
            raise ValueError("reconciliation requires verifier and a hashed evidence reference")
        evidence_reference = _audit_reference(evidence["evidence_ref"])
        outcome = evidence.get("outcome")
        supplied_task = _task_id(evidence.get("task_id"))
        known_tasks = set(observed["known_task_ids"])
        known_tasks.update(task for name, row in checkpoint.legacy_records().items()
                           if observed["resolved_legacy"].get(name) != row["sha256"] for task in row["task_ids"])
        if outcome == "task_verified_for_request":
            if not supplied_task or (known_tasks and known_tasks != {supplied_task}):
                raise ValueError("verified task ID conflicts with known task identity")
        elif outcome == "no_task_no_charge":
            if evidence.get("task_id") is not None or evidence.get("charge_status") != "none" or known_tasks:
                raise ValueError("no-task/no-charge evidence contradicts a known task")
        elif outcome == "authorized_rework":
            if (not observed["record_valid"] or observed["status"] not in {"COMPLETED", "FAILED"}
                or not supplied_task or known_tasks != {supplied_task}
                or evidence.get("rework_reason") not in {"qa_rejected", "user_requested_rework"}
                or not str(evidence.get("authorization_ref") or "").strip()
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(evidence.get("authorization_sha256") or ""))):
                raise ValueError("rework requires a terminal prior attempt and bound upstream authorization")
            _audit_reference(evidence["authorization_ref"])
            if evidence["rework_reason"] == "qa_rejected" and not re.fullmatch(
                r"sha256:[0-9a-f]{64}", str(evidence.get("qa_review_sha256") or "")
            ):
                raise ValueError("QA rework requires the review evidence digest")
        else:
            raise ValueError("unsupported reconciliation outcome")
        if outcome in {"no_task_no_charge", "authorized_rework"} and (observed["attempt"] or 0) >= 3:
            raise ValueError("image generation retry attempt limit reached")
        recovery_attempt = observed["attempt"]
        if not observed["record_valid"] and recovery_attempt is None:
            recovery_attempt = evidence.get("verified_attempt")
            if type(recovery_attempt) is not int or not 0 <= recovery_attempt <= 3:
                raise ValueError("unrecoverable local attempt metadata requires an upstream-verified attempt")
        if outcome in {"no_task_no_charge", "authorized_rework"} and recovery_attempt >= 3:
            raise ValueError("image generation retry attempt limit reached")
        if not observed["record_valid"]:
            # Explicit recovery preserves both raw artifacts before replacement;
            # no malformed record is silently normalized by execution.
            for artifact in (checkpoint.path, checkpoint.journal_path):
                if artifact.is_file():
                    atomic_bytes(checkpoint.root / ("preserved-" + _file_sha(artifact)[:24] + ".blob"), artifact.read_bytes())
            state = checkpoint._initial()
            state["attempt"] = recovery_attempt
            state["client_business_id"] = f"{checkpoint.kind}-{checkpoint.request_digest[:32]}-a{recovery_attempt}"
            state["status"] = "RECOVERY_REQUIRED"
            # The old journal is retained by content digest before explicit reset.
            atomic_bytes(checkpoint.journal_path, b"")
            atomic_json(checkpoint.path, _sealed(state))
        else:
            state = checkpoint.read()
        # Raw evidence/verifier/ref text may contain secrets. Retain only its
        # digest plus the minimal binding and outcome, never provider prose.
        audit = {key: evidence[key] for key in bound}
        audit.update(outcome=outcome, task_id=supplied_task, verified_at=verified_at.isoformat(),
                     evidence_sha256=evidence["evidence_sha256"], evidence_binding_digest=digest(dict(evidence)),
                     verifier_digest=digest(str(evidence["verified_by"])), evidence_ref=evidence_reference)
        if outcome == "authorized_rework":
            audit.update(rework_reason=evidence["rework_reason"], authorization_ref=evidence["authorization_ref"],
                          authorization_sha256=evidence["authorization_sha256"], qa_review_sha256=evidence.get("qa_review_sha256"))
            successor = evidence.get("successor_request_digest")
            if successor is not None:
                if not re.fullmatch(r"[0-9a-f]{64}", str(successor)) or successor == checkpoint.request_digest:
                    raise ValueError("rework successor must bind a distinct complete request digest")
                audit["successor_request_digest"] = successor
        if outcome == "authorized_rework":
            archive = checkpoint.root / f"attempt-{checkpoint.request_digest[:24]}-{state['attempt']}"
            atomic_bytes(archive.with_suffix(".json"), checkpoint.path.read_bytes())
            if checkpoint.output_path.is_file():
                atomic_bytes(archive.with_suffix(".png"), checkpoint.output_path.read_bytes())
        attempt = state["attempt"] + (outcome in {"no_task_no_charge", "authorized_rework"})
        attached_task = supplied_task if outcome == "task_verified_for_request" else None
        status = "SUPERSEDED" if outcome == "authorized_rework" and evidence.get("successor_request_digest") else "SUBMITTED" if attached_task else "READY"
        checkpoint.persist(state, status=status, task_id=attached_task,
                           attempt=attempt, client_business_id=f"{checkpoint.kind}-{checkpoint.request_digest[:32]}-a{attempt}",
                           failure_kind=None, reconciliation=audit, receipt=None, output_digest=None,
                           resolved_legacy=observed["legacy_checkpoint_digests"])
        return inspect_image_checkpoint(path)


def prepare_image_rework(checkpoint: ImageCheckpoint, *, basis: Mapping[str, Any]) -> int:
    """Consume an upper-layer authorized rework basis, including a changed prompt.

    This verifies local old artifact/request/attempt bindings. It does not verify
    user identity or external billing. No no-charge assertion is made for rework.
    Repeated invocation of the same basis resumes its single successor attempt.
    """
    old_path = Path(str(basis.get("checkpoint_path") or "")).resolve()
    if old_path.parent != checkpoint.root.resolve():
        raise ValueError("rework source checkpoint must be in the same governed root")
    old = ImageCheckpoint.from_path(old_path)
    if old.business_digest != checkpoint.business_digest:
        raise ValueError("rework source belongs to another business")
    authorization = str(basis.get("authorization_sha256") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", authorization):
        raise ValueError("rework requires a bound existing authorization reference")
    old_state = inspect_image_checkpoint(old_path)
    expected_attempt = basis.get("next_attempt")
    if type(expected_attempt) is not int or not 1 <= expected_attempt <= 3:
        raise ValueError("rework requires the exact next attempt within the retry limit")
    audit = old.read().get("reconciliation") or {}
    old_output = str(basis.get("old_artifact_digest") or "").removeprefix("sha256:")
    already = (old_state["attempt"] == expected_attempt and audit.get("authorization_sha256") == authorization
               and audit.get("output_digest") == old_output
               and audit.get("task_id") == basis.get("old_task_id"))
    if not already:
        if (old_state["attempt"] != expected_attempt-1 or old_state["output_digest"] != old_output
                or old_state["known_task_ids"] != [basis.get("old_task_id")]):
            raise ValueError("rework basis does not match the exact prior artifact/task/attempt")
        evidence = {**old_state, "outcome":"authorized_rework", "task_id":basis["old_task_id"],
                    "rework_reason":basis["reason"], "authorization_ref":basis["authorization_ref"],
                    "authorization_sha256":authorization, "qa_review_sha256":basis.get("qa_digest"),
                    "verified_at":_now(), "verified_by":"R2-local-authorized-rework-contract",
                    "evidence_ref":basis["authorization_ref"], "evidence_sha256":authorization}
        if checkpoint.request_digest != old.request_digest:
            evidence["successor_request_digest"] = checkpoint.request_digest
        reconcile_image_checkpoint(old_path, evidence=evidence)
        audit = old.read()["reconciliation"]
    if old.request_digest != checkpoint.request_digest:
        if audit.get("successor_request_digest") != checkpoint.request_digest:
            raise ValueError("rework authorization belongs to another successor request")
        with business_lock(checkpoint.root,checkpoint.business_digest):
            state=checkpoint.open_for_execution()
            if state.get("reconciliation") == audit and state["attempt"] == expected_attempt:
                return expected_attempt
            if state["status"]!='READY' or state["attempt"]!=0 or state.get("task_id"):
                raise ValueError("rework successor already has a different attempt")
            checkpoint.persist(state,attempt=expected_attempt,reconciliation=audit,
                client_business_id=f"{checkpoint.kind}-{checkpoint.request_digest[:32]}-a{expected_attempt}")
    return expected_attempt


def execute_image_checkpoint(checkpoint: ImageCheckpoint, *, retry_attempt: int, submit: Callable[[], Mapping[str, Any]],
                             poll: Callable[[int], Mapping[str, Any]], load_result: Callable[[str], bytes],
                             normalize_image: Callable[[bytes], bytes], paid_context=None,
                             paid_purpose: str | None = None) -> dict[str, Any]:
    if type(retry_attempt) is not int or not 0 <= retry_attempt <= 3:
        raise ValueError("image retry attempt must be between zero and three")
    with business_lock(checkpoint.root, checkpoint.business_digest):
        state = checkpoint.open_for_execution()
        if state["status"] == "SUPERSEDED":
            raise CheckpointRecoveryRequired(checkpoint.path, "this request was superseded by an explicitly authorized rework successor")
        if paid_context is not None:
            recovered_task = paid_context.image_recovery(checkpoint, state, paid_purpose)
            if recovered_task and not state.get("task_id"):
                state = checkpoint.persist(state, status="SUBMITTED", task_id=recovered_task)
        task_id = _task_id(state.get("task_id"))
        if state["status"] in {"SUBMISSION_UNKNOWN", "SUBMITTING", "RECOVERY_REQUIRED", "REJECTED_BEFORE_TASK"} and not task_id:
            raise CheckpointRecoveryRequired(checkpoint.path, "submission outcome is unknown; reconcile before retry")
        if state["status"] == "FAILED":
            raise CheckpointRecoveryRequired(checkpoint.path, "previous generation failed; review before retry")
        if state["status"] == "COMPLETED":
            receipt = state.get("receipt")
            if (not isinstance(receipt, dict) or receipt.get("request_digest") != checkpoint.request_digest
                or receipt.get("business_digest") != checkpoint.business_digest
                or receipt.get("status") != "COMPLETED" or receipt.get("provider") != PROVIDER
                or receipt.get("retry_attempt") != state["attempt"]
                or receipt.get("client_business_id") != state["client_business_id"]
                or receipt.get("model") != checkpoint.model or receipt.get("task_id") != task_id
                or receipt.get("output_digest") != "sha256:" + str(state.get("output_digest"))):
                raise CheckpointRecoveryRequired(checkpoint.path, "completed receipt does not bind the current request")
            if checkpoint.output_path.is_file():
                try:
                    data = normalize_image(checkpoint.output_path.read_bytes())
                except ValueError:
                    raise CheckpointRecoveryRequired(checkpoint.path, "completed output is invalid; preserve before recovery") from None
                if hashlib.sha256(data).hexdigest() != state.get("output_digest"):
                    raise CheckpointRecoveryRequired(checkpoint.path, "completed output digest drifted")
                if paid_context is not None:
                    paid_context.image_completed(checkpoint, state, paid_purpose, receipt)
                return {"image_bytes": data, "receipt": dict(receipt), "local_path": str(checkpoint.output_path)}
            if not task_id:
                raise CheckpointRecoveryRequired(checkpoint.path, "completed output is missing and no task ID is available")
        if not task_id:
            if paid_context is not None:
                retry_attempt=paid_context.image_resume_attempt(checkpoint,state,paid_purpose,retry_attempt)
            if state["status"] != "READY" or retry_attempt != state["attempt"]:
                raise CheckpointRecoveryRequired(checkpoint.path, "attempt number is not local authorization to submit")
            reservation = paid_context.image_reservation(checkpoint, state, paid_purpose) if paid_context is not None else None
            state = checkpoint.persist(state, status="SUBMITTING")
            try:
                created = paid_context.invoke(reservation, submit) if paid_context is not None else submit()
                raw_task = (created.get("data") or {}).get("task_id")
                if isinstance(raw_task, str) and raw_task.isdecimal():
                    raw_task = int(raw_task)
                task_id = _task_id(raw_task)
                if task_id:
                    state = checkpoint.persist(state, status="SUBMITTED", task_id=task_id)
                elif created.get("code") != 200:
                    if paid_context is not None:
                        paid_context.record(reservation, "UNKNOWN")
                    state = checkpoint.persist(state, status="REJECTED_BEFORE_TASK", failure_kind="PROVIDER_REJECTED_BEFORE_TASK")
                    raise ImageSubmissionRejected("Lingshi paid submission rejected before task creation; reconcile before retry")
                else:
                    if paid_context is not None:
                        paid_context.record(reservation, "UNKNOWN")
                    raise RuntimeError("Lingshi paid submission did not return data.task_id")
            except (CheckpointPersistenceError, ImageSubmissionRejected):
                raise
            except Exception:
                checkpoint.persist(state, status="SUBMISSION_UNKNOWN", task_id=task_id,
                                   failure_kind="OUTCOME_REQUIRES_RECONCILIATION")
                raise
        try:
            completed = poll(task_id)
            public_url = str(completed.get("result_url") or "").strip()
            parsed = urlparse(public_url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("completed task requires an HTTPS result without URL credentials")
            data = normalize_image(load_result(public_url))
            output_digest = hashlib.sha256(data).hexdigest()
            if state["status"] == "COMPLETED" and state.get("output_digest") != output_digest:
                raise CheckpointRecoveryRequired(checkpoint.path, "retrieved completed output digest differs from the frozen receipt")
            receipt = {"status": "COMPLETED", "provider": PROVIDER, "model": checkpoint.model,
                       "task_id": task_id, "client_business_id": state["client_business_id"],
                       "request_digest": checkpoint.request_digest, "business_digest": checkpoint.business_digest,
                       "source_identity_complete": checkpoint.source_identity_complete,
                       "request_attempted": True, "outcome_unknown": False, "external_generation_count": 1,
                       "output_digest": "sha256:" + output_digest, "public_url": public_url,
                       "cost": completed.get("cost"), "channel_group": completed.get("channel_group"),
                       "retry_attempt": state["attempt"]}
            atomic_bytes(checkpoint.output_path, data)
            checkpoint.persist(state, status="COMPLETED", task_id=task_id, output_digest=output_digest, receipt=receipt)
            if paid_context is not None:
                paid_context.image_completed(checkpoint, state, paid_purpose, receipt)
            return {"image_bytes": data, "receipt": receipt, "local_path": str(checkpoint.output_path)}
        except (CheckpointPersistenceError, CheckpointRecoveryRequired):
            raise
        except ImageTaskFailed:
            checkpoint.persist(state, status="FAILED", task_id=task_id, failure_kind="PROVIDER_FAILED")
            raise
        except Exception:
            checkpoint.persist(state, status="SUBMITTED", task_id=task_id, failure_kind="OUTCOME_REQUIRES_RECONCILIATION")
            raise
