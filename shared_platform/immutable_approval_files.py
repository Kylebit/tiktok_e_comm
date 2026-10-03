"""Local immutable file publication and evidence-preserving repair primitives."""

from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

class ImmutableFileError(ValueError):
    pass


def require_local_path(path: Path, *, root: Path, allow_directory: bool = False):
    """Check lexical root and every link/reparse component before opening data."""
    if ".." in Path(path).parts or ".." in Path(root).parts:
        raise ImmutableFileError("local path contains ambiguous parent traversal")
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    try:
        path.relative_to(root)
    except ValueError:
        raise ImmutableFileError("path is outside the declared local root")
    components = [*reversed(path.parents), path]
    final = None
    for component in components:
        try:
            info = component.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ImmutableFileError("local path identity is unreadable") from error
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ImmutableFileError("local path contains a link or reparse point")
        if component != path and not stat.S_ISDIR(info.st_mode):
            raise ImmutableFileError("local path ancestor is not a directory")
        final = info
    if final is not None and not (stat.S_ISREG(final.st_mode) or allow_directory and stat.S_ISDIR(final.st_mode)):
        raise ImmutableFileError("local authority is not a regular file")
    return final


@contextmanager
def authority_lock(path: Path, *, root: Path):
    from modules.sourcing.image_generation_checkpoint import business_lock

    require_local_path(path, root=root)
    key = hashlib.sha256(path.name.encode("utf-8")).hexdigest()
    lock = path.parent / f".lingshi-{key[:24]}.lock"
    require_local_path(lock, root=root)
    with business_lock(path.parent, key):
        require_local_path(path, root=root)
        yield


def stage_complete(path: Path, content: bytes, *, root: Path) -> Path:
    temporary = path.parent / f".authority-{uuid.uuid4().hex}.tmp"
    require_local_path(temporary, root=root)
    with temporary.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    if temporary.read_bytes() != content:
        raise ImmutableFileError("staged authority bytes failed full verification")
    return temporary


def publish_without_overwrite(path: Path, content: bytes, *, root: Path) -> Path:
    """Caller holds authority_lock; link publishes only a complete synced file."""
    require_local_path(path, root=root)
    temporary = stage_complete(path, content, root=root)
    try:
        os.link(temporary, path)
    except FileExistsError:
        require_local_path(path, root=root)
        if path.read_bytes() != content:
            raise ImmutableFileError("immutable authority file conflicts")
    else:
        if path.read_bytes() != content:
            raise ImmutableFileError("published authority bytes failed full verification")
    temporary.unlink()
    return path


def persist_immutable_bytes(path: Path, content: bytes, *, root: Path) -> Path:
    with authority_lock(path, root=root):
        if require_local_path(path, root=root) is not None:
            if path.read_bytes() != content:
                raise ImmutableFileError("immutable authority file conflicts")
            return path
        return publish_without_overwrite(path, content, root=root)


def _sealed_document(content: bytes) -> bool:
    """Never replace any complete self-consistent authority, even a foreign one."""
    from shared_platform.publication_autopilot import _canonical_digest

    try:
        document = json.loads(content)
        if not isinstance(document, dict):
            return False
        key = "approval_digest" if "approval_digest" in document else "candidate_digest"
        supplied = document.pop(key)
        return supplied == _canonical_digest(document)
    except (KeyError, TypeError, ValueError, UnicodeError):
        return False


def inspect_approval_recovery(
    *, kind: str, candidate: dict, reports_root: Path,
    evidence_path: Path | None = None, evidence_root: Path | None = None,
    expected_evidence_sha256: str | None = None,
    expected_document_digest: str | None = None,
) -> dict:
    """Read-only preview. Complete original bytes and both digests are required."""
    from shared_platform import publication_autopilot as authority

    if kind not in {"candidate", "approval"}:
        raise ImmutableFileError("recovery kind must be candidate or approval")
    if kind == "approval":
        unsigned_candidate = dict(candidate)
        supplied_candidate_digest = unsigned_candidate.pop("candidate_digest", None)
        if supplied_candidate_digest != authority._canonical_digest(unsigned_candidate):
            raise ImmutableFileError("approval recovery candidate digest conflicts")
    path = (authority.release_candidate_path if kind == "candidate" else authority.final_approval_receipt_path)(candidate, reports_root=reports_root)
    present = require_local_path(path, root=reports_root)
    current = path.read_bytes() if present is not None else None
    result = {
        "status": "MISSING_BLOCKED" if current is None else "CORRUPT_BLOCKED",
        "kind": kind, "path": str(path),
        "actual_sha256": hashlib.sha256(current).hexdigest() if current is not None else None,
        "expected_evidence_sha256": expected_evidence_sha256,
        "expected_document_digest": expected_document_digest,
        "missing_evidence": [], "writes_performed": [],
    }
    if current is not None and _sealed_document(current):
        result["status"] = "COMPLETE_IMMUTABLE"
    for name, value in (("evidence_path", evidence_path), ("evidence_root", evidence_root),
                        ("expected_evidence_sha256", expected_evidence_sha256),
                        ("expected_document_digest", expected_document_digest)):
        if not value:
            result["missing_evidence"].append(name)
    if result["missing_evidence"]:
        return result
    if require_local_path(evidence_path, root=evidence_root) is None:
        result["missing_evidence"].append("complete_original_evidence_file")
        return result
    evidence = evidence_path.read_bytes()
    if hashlib.sha256(evidence).hexdigest() != expected_evidence_sha256:
        raise ImmutableFileError("original evidence byte digest conflicts")
    try:
        document = json.loads(evidence)
        if not isinstance(document, dict):
            raise ValueError("document is not an object")
        canonical = (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        if canonical != evidence:
            raise ValueError("evidence must retain original persistence bytes")
        if kind == "candidate":
            unsigned = dict(document)
            supplied = unsigned.pop("candidate_digest")
            if supplied != authority._canonical_digest(unsigned):
                raise ValueError("candidate digest conflicts")
            if authority.release_candidate_path(document, reports_root=reports_root) != path:
                raise ValueError("candidate path identity conflicts")
        else:
            document = authority.validate_final_approval_receipt(document, candidate)
        key = "candidate_digest" if kind == "candidate" else "approval_digest"
        if document[key] != expected_document_digest:
            raise ValueError("original document digest conflicts")
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        raise ImmutableFileError("original complete approval evidence is invalid: " + str(error)) from error
    result["evidence_path"] = str(evidence_path)
    result["approval_evidence"] = {key: document.get(key) for key in (
        "offer_id", "plan_id", "candidate_digest", "approval_digest", "approved_by", "approved_at", "target_labels"
    )}
    if current == evidence:
        result["status"] = "ALREADY_PRESENT"
    elif current is not None and _sealed_document(current):
        result["status"] = "COMPLETE_CONFLICT_BLOCKED"
    else:
        result["status"] = "RECOVERABLE_SAME_CONTENT"
    return result


def recover_approval_file(*, expected_damaged_sha256: str | None, **kwargs) -> dict:
    """Restore supplied original bytes; retain corrupt bytes and an intent record."""
    preview = inspect_approval_recovery(**kwargs)
    if preview["status"] == "ALREADY_PRESENT":
        return preview
    if preview["status"] != "RECOVERABLE_SAME_CONTENT":
        raise ImmutableFileError("recovery is blocked: " + preview["status"])
    path, root = Path(preview["path"]), kwargs["reports_root"]
    with authority_lock(path, root=root):
        current = inspect_approval_recovery(**kwargs)
        if current["status"] == "ALREADY_PRESENT":
            return current
        if current["status"] != "RECOVERABLE_SAME_CONTENT" or current["actual_sha256"] != expected_damaged_sha256:
            raise ImmutableFileError("damaged authority changed since inspect")
        evidence = kwargs["evidence_path"].read_bytes()
        if hashlib.sha256(evidence).hexdigest() != kwargs["expected_evidence_sha256"]:
            raise ImmutableFileError("original evidence changed since inspect")
        backup = None
        if current["actual_sha256"] is not None:
            backup = path.with_name(".damaged-" + current["actual_sha256"] + ".bin")
            publish_without_overwrite(backup, path.read_bytes(), root=root)
        intent = {
            "schema_version": "immutable-approval-recovery/v1", "action": "RESTORE_ORIGINAL_BYTES",
            "path": str(path), "damaged_sha256": current["actual_sha256"],
            "original_sha256": kwargs["expected_evidence_sha256"],
            "document_digest": kwargs["expected_document_digest"],
            "backup": str(backup) if backup else None,
        }
        intent_bytes = (json.dumps(intent, sort_keys=True, indent=2) + "\n").encode("utf-8")
        # Candidate discovery scans *.json; recovery evidence is not a candidate.
        intent_path = path.with_name(".recovery-" + hashlib.sha256(intent_bytes).hexdigest() + ".receipt")
        publish_without_overwrite(intent_path, intent_bytes, root=root)
        if backup is None:
            publish_without_overwrite(path, evidence, root=root)
        else:
            temporary = stage_complete(path, evidence, root=root)
            require_local_path(path, root=root)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_damaged_sha256:
                raise ImmutableFileError("damaged authority changed before restoration")
            os.replace(temporary, path)
        result = inspect_approval_recovery(**kwargs)
        if result["status"] != "ALREADY_PRESENT":
            raise ImmutableFileError("restored approval did not read back exactly")
        return {**result, "status": "RESTORED_ORIGINAL_BYTES", "backup": str(backup) if backup else None,
                "intent_path": str(intent_path), "writes_performed": [str(path), str(intent_path), *([str(backup)] if backup else [])]}
