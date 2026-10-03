"""Immutable attestations for relocating evidence without changing logical refs."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "evidence-relocation-attestation/v1"
_HEX = frozenset("0123456789abcdef")
OFFER_395_REQUIRED_AUTHORITY_DIGESTS = {
    "reportless_receipt": "sha256:1b4971324753573ab9ec3eb9dd762dafe9453bf5d1c78c799ad262fb92ae2a7c",
    "domain_closure": "sha256:31a5a24765cf5b7888f7d55e1194a112eff6ec317117eea348d38ca3e7029bdc",
    "unauthorized_continuation": "sha256:590e07b7687dc2cb112928f725368f9cc12d157df156dd05609cd4153e87ea1e",
    "continuation_preflight": "sha256:879766dea25c24b0136248542ae52e59a8972dbbf85d873bd2026fc4c7057c72",
    "original_recovery_manifest": "sha256:769c8e77d8748eeceb3d11ee77082689bbda5f5d2e6455da84e251d4ae401f36",
}


class EvidenceRelocationError(RuntimeError):
    pass


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: object) -> str:
    if type(value) is not str:
        raise EvidenceRelocationError("sha256 is invalid")
    raw = value.removeprefix("sha256:")
    if len(raw) != 64 or any(char not in _HEX for char in raw):
        raise EvidenceRelocationError("sha256 is invalid")
    return "sha256:" + raw


def _is_reparse(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _reject_reparse_chain(path: Path, *, stop: Path | None = None) -> None:
    current = path
    while True:
        if current.exists() and _is_reparse(current):
            raise EvidenceRelocationError("evidence path uses a symlink or reparse point")
        if stop is not None and current == stop:
            return
        if current.parent == current:
            return
        current = current.parent


def _absolute_normal_path(value: object, name: str) -> Path:
    if type(value) is not str or not value:
        raise EvidenceRelocationError(f"{name} is invalid")
    path = Path(value)
    if not path.is_absolute():
        raise EvidenceRelocationError(f"{name} must be absolute")
    if path.exists() and _is_reparse(path):
        raise EvidenceRelocationError("evidence path uses a symlink or reparse point")
    resolved = path.resolve(strict=False)
    if str(path) != str(resolved):
        raise EvidenceRelocationError(f"{name} is not normalized")
    return resolved


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _file_ref(path: Path, data: bytes) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


def _safe_ref(value: object, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256", "size"}:
        raise EvidenceRelocationError(f"{name} ref is invalid")
    path = _absolute_normal_path(value.get("path"), f"{name} path")
    size = value.get("size")
    if type(size) is not int or size < 0:
        raise EvidenceRelocationError(f"{name} size is invalid")
    return {"path": str(path), "sha256": _sha(value.get("sha256")), "size": size}


def _authority_digests(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise EvidenceRelocationError("authority digests are invalid")
    if any(type(key) is not str or not key for key in value):
        raise EvidenceRelocationError("authority digest key is invalid")
    return {key: _sha(value[key]) for key in sorted(value)}


def _read_plain_file(path: Path) -> bytes:
    if not path.exists() or not path.is_file():
        raise EvidenceRelocationError("evidence file is missing")
    _reject_reparse_chain(path)
    return path.read_bytes()


def _write_exclusive_or_verify(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        if _read_plain_file(path) != data:
            raise EvidenceRelocationError("content-addressed destination conflicts")
        return
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _required_refs(value: Sequence[Mapping[str, object]] | None) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise EvidenceRelocationError("required original refs are invalid")
    refs = [_safe_ref(row, name="required original") for row in value]
    keys = [(row["path"], row["sha256"], row["size"]) for row in refs]
    if len(keys) != len(set(keys)):
        raise EvidenceRelocationError("required original refs contain duplicates")
    return refs


def build_evidence_relocation_attestation(
    original_refs: Sequence[Mapping[str, object]], *, target_root: str | Path,
    allowed_target_root: str | Path, authority_digests: Mapping[str, object],
    required_original_refs: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    """Copy exact bytes into one fixed root and atomically attest the mapping."""
    target = Path(target_root).resolve(strict=False)
    allowed = Path(allowed_target_root).resolve(strict=False)
    if not target.is_absolute() or target != allowed:
        raise EvidenceRelocationError("target root must equal the fixed allowed target root")
    target.mkdir(parents=True, exist_ok=True)
    _reject_reparse_chain(target)
    if isinstance(original_refs, (str, bytes, bytearray)) or not isinstance(
        original_refs, Sequence
    ):
        raise EvidenceRelocationError("original refs are invalid")
    originals = [_safe_ref(row, name="original") for row in original_refs]
    required = _required_refs(required_original_refs)
    if required is not None and originals != required:
        raise EvidenceRelocationError("original refs do not exactly match required refs")
    original_keys = [(row["path"], row["sha256"], row["size"]) for row in originals]
    if len(original_keys) != len(set(original_keys)):
        raise EvidenceRelocationError("original refs contain duplicates")

    entries = []
    relocated_paths: set[str] = set()
    for original in originals:
        source = Path(original["path"])
        data = _read_plain_file(source)
        actual = _file_ref(source, data)
        if actual != original:
            raise EvidenceRelocationError("original evidence bytes conflict with its ref")
        suffix = source.suffix.lower() if source.suffix.lower() in {".json", ".jsonl", ".txt"} else ".bin"
        relocated = target / f"{original['sha256'].removeprefix('sha256:')}{suffix}"
        if not _within(relocated, target) or str(relocated) in relocated_paths:
            raise EvidenceRelocationError("relocated evidence is not one-to-one")
        relocated_paths.add(str(relocated))
        _write_exclusive_or_verify(relocated, data)
        relocated_ref = _file_ref(relocated, data)
        entries.append({"original": original, "relocated": relocated_ref})

    core = {
        "schema_version": SCHEMA_VERSION,
        "allowed_target_root": str(allowed),
        "authority_digests": _authority_digests(authority_digests),
        "entries": entries,
    }
    attestation = {**core, "attestation_digest": _digest(core)}
    encoded = (_canonical(attestation) + "\n").encode("utf-8")
    path = target / f"attestation-{attestation['attestation_digest'].removeprefix('sha256:')}.json"
    _write_exclusive_or_verify(path, encoded)
    return attestation


def validate_evidence_relocation_attestation(
    value: Mapping[str, Any], *, allowed_target_root: str | Path,
    expected_authority_digests: Mapping[str, object] | None = None,
    required_original_refs: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EvidenceRelocationError("relocation attestation is invalid")
    checked = deepcopy(dict(value))
    supplied = checked.pop("attestation_digest", None)
    if supplied != _digest(checked):
        raise EvidenceRelocationError("relocation attestation digest conflicts")
    if checked.get("schema_version") != SCHEMA_VERSION or set(checked) != {
        "schema_version", "allowed_target_root", "authority_digests", "entries"
    }:
        raise EvidenceRelocationError("relocation attestation fields are invalid")
    root = Path(allowed_target_root).resolve(strict=False)
    if checked["allowed_target_root"] != str(root):
        raise EvidenceRelocationError("relocation target root conflicts")
    _reject_reparse_chain(root)
    authorities = _authority_digests(checked["authority_digests"])
    if expected_authority_digests is not None and authorities != _authority_digests(
        expected_authority_digests
    ):
        raise EvidenceRelocationError("relocation authority digests conflict")
    entries = checked["entries"]
    if not isinstance(entries, list) or not entries:
        raise EvidenceRelocationError("relocation entries are invalid")
    originals, relocated = [], []
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {"original", "relocated"}:
            raise EvidenceRelocationError("relocation entry is invalid")
        original = _safe_ref(entry["original"], name="original")
        target_ref = _safe_ref(entry["relocated"], name="relocated")
        target_path = Path(target_ref["path"])
        if (target_path.parent != root or not _within(target_path, root)
                or target_ref["sha256"].removeprefix("sha256:") not in target_path.name):
            raise EvidenceRelocationError("relocated path conflicts")
        data = _read_plain_file(target_path)
        if _file_ref(target_path, data) != target_ref:
            raise EvidenceRelocationError("relocated evidence bytes conflict")
        if original["sha256"] != target_ref["sha256"] or original["size"] != target_ref["size"]:
            raise EvidenceRelocationError("relocated evidence does not match original")
        originals.append(original)
        relocated.append(target_ref)
    original_keys = [(row["path"], row["sha256"], row["size"]) for row in originals]
    relocated_keys = [(row["path"], row["sha256"], row["size"]) for row in relocated]
    if len(original_keys) != len(set(original_keys)) or len(relocated_keys) != len(set(relocated_keys)):
        raise EvidenceRelocationError("relocation entries are not one-to-one")
    required = _required_refs(required_original_refs)
    if required is not None and originals != required:
        raise EvidenceRelocationError("relocation entries do not exactly match required refs")
    return {**checked, "attestation_digest": supplied}


class EvidenceRelocationResolver:
    """Resolve relocated bytes while retaining the original logical ref."""

    def __init__(self, attestation: Mapping[str, Any], *, allowed_target_root: str | Path,
                 expected_authority_digests: Mapping[str, object] | None = None,
                 required_original_refs: Sequence[Mapping[str, object]] | None = None) -> None:
        self.attestation = validate_evidence_relocation_attestation(
            attestation, allowed_target_root=allowed_target_root,
            expected_authority_digests=expected_authority_digests,
            required_original_refs=required_original_refs,
        )
        self._target_root = Path(self.attestation["allowed_target_root"])
        self._by_original = {
            (row["original"]["path"], row["original"]["sha256"]): row
            for row in self.attestation["entries"]
        }

    def __call__(self, meta: Mapping[str, object], allowed_roots: Sequence[str | Path]) -> bytes:
        """Callable adapter used by existing evidence readers."""
        if not isinstance(meta, Mapping) or set(meta) != {"path", "sha256"}:
            raise EvidenceRelocationError("logical original ref is invalid")
        return self.resolve_bytes(
            str(meta["path"]), str(meta["sha256"]), allowed_roots=allowed_roots)

    def resolve_bytes(self, path: str, sha256: str, *, allowed_roots: Sequence[str | Path]) -> bytes:
        logical_path = _absolute_normal_path(path, "logical original path")
        roots = [Path(root).resolve(strict=False) for root in allowed_roots]
        if not roots or not any(_within(self._target_root, root) for root in roots):
            raise EvidenceRelocationError("relocated evidence root is not allowed")
        key = (str(logical_path), _sha(sha256))
        entry = self._by_original.get(key)
        if entry is None:
            raise EvidenceRelocationError("logical original ref is not attested")
        relocated = entry["relocated"]
        data = _read_plain_file(Path(relocated["path"]))
        if _file_ref(Path(relocated["path"]), data) != relocated:
            raise EvidenceRelocationError("relocated evidence bytes conflict")
        return data

    def load(self, meta: Mapping[str, object], *, allowed_roots: Sequence[str | Path]) -> tuple[dict[str, Any], dict[str, str]]:
        if not isinstance(meta, Mapping) or set(meta) != {"path", "sha256"}:
            raise EvidenceRelocationError("logical original ref is invalid")
        data = self(meta, allowed_roots)
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise EvidenceRelocationError("relocated evidence is not JSON") from error
        if not isinstance(parsed, dict):
            raise EvidenceRelocationError("relocated JSON evidence is not an object")
        return parsed, {"path": str(meta["path"]), "sha256": _sha(meta["sha256"])}


__all__ = [
    "EvidenceRelocationError", "EvidenceRelocationResolver",
    "OFFER_395_REQUIRED_AUTHORITY_DIGESTS", "SCHEMA_VERSION",
    "build_evidence_relocation_attestation",
    "validate_evidence_relocation_attestation",
]
