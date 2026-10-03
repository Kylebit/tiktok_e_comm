"""Read-only identities for the seven OrbitHive business Skills.

This compares repository and personal-install files.  It never installs,
selects an execution version, opens credentials, or infers business readiness.
"""

from __future__ import annotations

from datetime import datetime, timezone
import codecs
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import threading
import time


CORE_SKILLS = {
    "prepare-product-publication": "skills/prepare-product-publication",
    "prepare-product-images": "skills/prepare-product-images",
    "publish-approved-product": "skills/publish-approved-product",
    "delist-products-by-sku": "skills/delist-products-by-sku",
    "apply-product-discounts": "skills/apply-product-discounts",
    "manage-seaya-replenishment": "domains/supply_chain_operations/skills/manage-seaya-replenishment",
    "manage-profit-settlement": "domains/data_operations/skills/manage-profit-settlement",
}
_IGNORED_DIRS = {"__pycache__", ".pytest_cache"}
_IGNORED_SUFFIXES = {".pyc", ".pyo"}
_NORMALIZED_TEXT = {".md", ".py", ".yaml", ".yml", ".json"}
_MAX_DIRECTORIES = 256
_MAX_ENTRIES = 4096
_MAX_FILES = 2048
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_BYTES = 64 * 1024 * 1024
_MAX_SECONDS = 5.0
_CACHE_SECONDS = 3.0
_SCAN_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], tuple[float, dict, dict[str, tuple | None]]] = {}


class ScanLimit(RuntimeError):
    """The read-only identity budget was exhausted; all states become UNKNOWN."""


class _Budget:
    def __init__(self):
        self.deadline = time.monotonic() + _MAX_SECONDS
        self.directories = self.entries = self.files = self.bytes = 0
        self.watches: dict[str, tuple | None] = {}

    def check(self):
        if time.monotonic() > self.deadline:
            raise ScanLimit("time")

    def directory(self):
        self.check()
        self.directories += 1
        if self.directories > _MAX_DIRECTORIES:
            raise ScanLimit("directories")

    def entry(self):
        self.check()
        self.entries += 1
        if self.entries > _MAX_ENTRIES:
            raise ScanLimit("entries")

    def file(self, size):
        self.check()
        self.files += 1
        if self.files > _MAX_FILES:
            raise ScanLimit("files")
        if size > _MAX_FILE_BYTES:
            raise ScanLimit("single_file_bytes")
        if self.bytes + size > _MAX_BYTES:
            raise ScanLimit("total_bytes")

    def chunk(self, size):
        self.check()
        self.bytes += size
        if self.bytes > _MAX_BYTES:
            raise ScanLimit("total_bytes")

    def watch(self, path: Path):
        self.check()
        try:
            stat = path.lstat()
            signature = (stat.st_mode, stat.st_size, stat.st_mtime_ns,
                         getattr(stat, "st_file_attributes", 0))
        except FileNotFoundError:
            signature = None
        self.watches[str(path)] = signature


def _watch_valid(watches: dict[str, tuple | None]) -> bool:
    deadline = time.monotonic() + 0.5
    for name, expected in watches.items():
        if time.monotonic() > deadline:
            return False
        try:
            stat = Path(name).lstat()
            actual = (stat.st_mode, stat.st_size, stat.st_mtime_ns,
                      getattr(stat, "st_file_attributes", 0))
        except FileNotFoundError:
            actual = None
        except OSError:
            return False
        if actual != expected:
            return False
    return True


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(os.path, "isjunction", lambda _p: False)(path))


def _digest(mapping: dict[str, str]) -> str:
    payload = json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _hash_file(path: Path, budget: _Budget) -> tuple[str, str]:
    initial = path.stat()
    budget.watch(path)
    budget.file(initial.st_size)
    raw_hasher = hashlib.sha256()
    normalized_hasher = hashlib.sha256()
    is_text = path.suffix.lower() in _NORMALIZED_TEXT
    decoder = codecs.getincrementaldecoder("utf-8-sig")() if is_text else None
    pending_cr = False

    def normalize(piece: str, *, final: bool = False):
        nonlocal pending_cr
        if pending_cr:
            if piece.startswith("\n"):
                piece = piece[1:]
            normalized_hasher.update(b"\n")
            pending_cr = False
        if piece.endswith("\r") and not final:
            piece = piece[:-1]
            pending_cr = True
        normalized_hasher.update(piece.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))

    read_bytes = 0
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            budget.chunk(len(chunk))
            read_bytes += len(chunk)
            if read_bytes > _MAX_FILE_BYTES:
                raise ScanLimit("single_file_bytes")
            raw_hasher.update(chunk)
            if is_text:
                normalize(decoder.decode(chunk))
            else:
                normalized_hasher.update(chunk)
        if is_text:
            normalize(decoder.decode(b"", final=True), final=True)
            if pending_cr:
                normalized_hasher.update(b"\n")
        final = os.fstat(stream.fileno())
    if (initial.st_size, initial.st_mtime_ns) != (final.st_size, final.st_mtime_ns) or read_bytes != initial.st_size:
        raise ValueError("Skill file changed during identity scan")
    return raw_hasher.hexdigest(), normalized_hasher.hexdigest()


def _manifest(root: Path, budget: _Budget | None = None) -> dict:
    budget = budget or _Budget()
    if not (root / "SKILL.md").is_file():
        raise ValueError("SKILL.md missing")
    raw_hashes: dict[str, str] = {}
    normalized_hashes: dict[str, str] = {}
    total = 0
    pending = [root]
    while pending:
        folder = pending.pop()
        budget.directory()
        budget.watch(folder)
        children = []
        with os.scandir(folder) as stream:
            for entry in stream:
                budget.entry()
                children.append(entry)
        children.sort(key=lambda item: item.name)
        for entry in children:
            budget.check()
            path = Path(entry.path)
            if _is_link(path):
                raise ValueError("nested link in Skill tree")
            if entry.is_dir(follow_symlinks=False):
                if entry.name not in _IGNORED_DIRS:
                    pending.append(path)
                continue
            if not entry.is_file(follow_symlinks=False):
                raise ValueError("non-file entry in Skill tree")
            if path.suffix.lower() in _IGNORED_SUFFIXES:
                continue
            size = entry.stat(follow_symlinks=False).st_size
            total += size
            raw, normalized = _hash_file(path, budget)
            relative = path.relative_to(root).as_posix()
            raw_hashes[relative] = raw
            normalized_hashes[relative] = normalized
    raw_hashes = dict(sorted(raw_hashes.items()))
    normalized_hashes = dict(sorted(normalized_hashes.items()))
    return {
        "file_count": len(raw_hashes),
        "byte_count": total,
        "raw_digest": _digest(raw_hashes),
        "normalized_digest": _digest(normalized_hashes),
        "skill_md_sha256": raw_hashes["SKILL.md"],
        "files": [
            {"path": name, "sha256": sha, "normalized_sha256": normalized_hashes[name]}
            for name, sha in raw_hashes.items()
        ],
    }


def _hashes(manifest: dict, key: str) -> dict[str, str]:
    return {row["path"]: row[key] for row in manifest["files"]}


def _installed(path: Path, skill_id: str, budget: _Budget) -> dict:
    budget.check()
    budget.watch(path)
    if not path.exists() and not _is_link(path):
        return {"state": "NOT_INSTALLED", "source_type": "missing", "path": str(path), "resolved_target": None}
    source_type = "junction" if bool(getattr(os.path, "isjunction", lambda _p: False)(path)) else "symlink" if path.is_symlink() else "copy"
    try:
        target = path.resolve(strict=True)
        if not target.is_dir() or target.name != skill_id:
            raise ValueError("installed target is not the named Skill directory")
        return {"state": "PRESENT", "source_type": source_type, "path": str(path), "resolved_target": str(target), "manifest": _manifest(target, budget)}
    except (OSError, ValueError, UnicodeError) as error:
        return {"state": "INVALID", "source_type": source_type, "path": str(path), "resolved_target": None,
                "error": type(error).__name__}


def _comparison(project: dict, installed: dict) -> dict:
    if installed["state"] != "PRESENT":
        return {"status": installed["state"], "missing_files": [], "extra_files": [], "changed_files": []}
    left = _hashes(project, "sha256")
    right = _hashes(installed["manifest"], "sha256")
    left_normalized = _hashes(project, "normalized_sha256")
    right_normalized = _hashes(installed["manifest"], "normalized_sha256")
    missing = sorted(left.keys() - right.keys())
    extra = sorted(right.keys() - left.keys())
    changed = sorted(name for name in left.keys() & right.keys() if left[name] != right[name])
    normalized_changed = sorted(name for name in left_normalized.keys() & right_normalized.keys()
                                if left_normalized[name] != right_normalized[name])
    status = "CONFLICT" if missing or extra or normalized_changed else "RAW_DRIFT" if changed else "MATCH"
    return {"status": status, "missing_files": missing, "extra_files": extra,
            "changed_files": changed, "normalized_changed_files": normalized_changed}


def _unknown_snapshot(repository: Path, personal: Path, reason: str) -> dict:
    return {"schema": "orbit-skill-identity/v1", "observed_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(repository), "installed_root": str(personal), "execution_authority": False,
            "reason": reason, "skills": {
                skill_id: {"project": {"path": relative, "registry_verified": False},
                           "installed": {"state": "UNKNOWN", "source_type": "unknown"},
                           "comparison": {"status": "UNKNOWN"}, "execution_authority": False}
                for skill_id, relative in CORE_SKILLS.items()}}


def _inspect_uncached(repository: Path, personal: Path, budget: _Budget) -> dict:
    registry_path = repository / "config/capability_catalog.json"
    budget.watch(registry_path)
    registry_size = registry_path.stat().st_size
    if registry_size > 1024 * 1024:
        raise ScanLimit("registry_bytes")
    budget.file(registry_size)
    registry_bytes = registry_path.read_bytes()
    budget.chunk(len(registry_bytes))
    registry = json.loads(registry_bytes.decode("utf-8"))
    registered = {row.get("id"): row for row in registry.get("skills", []) if isinstance(row, dict)}
    results = {}
    for skill_id, relative in CORE_SKILLS.items():
        budget.check()
        row = registered.get(skill_id)
        source = repository / relative
        budget.watch(source)
        project = {"path": relative, "resolved_target": str(source.resolve()), "source_type": "project_directory"}
        try:
            if row is None or row.get("source_path") != relative or _is_link(source):
                raise ValueError("registered source identity mismatch")
            source_resolved = source.resolve(strict=True)
            source_resolved.relative_to(repository)
            project["manifest"] = _manifest(source_resolved, budget)
            expected = row.get("file_digests")
            observed = {relative + "/" + name: sha for name, sha in _hashes(project["manifest"], "normalized_sha256").items()}
            project["registry_verified"] = isinstance(expected, dict) and observed == expected
            if not project["registry_verified"]:
                project["error"] = "registry_digest_mismatch"
        except (OSError, ValueError, UnicodeError, KeyError) as error:
            project["registry_verified"] = False
            project["error"] = type(error).__name__
        installed = _installed(personal / skill_id, skill_id, budget)
        comparison = _comparison(project["manifest"], installed) if project["registry_verified"] else {
            "status": "SOURCE_UNVERIFIED", "missing_files": [], "extra_files": [], "changed_files": []}
        results[skill_id] = {"project": project, "installed": installed, "comparison": comparison,
                             "installable_in_registry": bool(row.get("installable")) if row else False,
                             "execution_authority": False}
    return {"schema": "orbit-skill-identity/v1", "observed_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(repository), "installed_root": str(personal),
            "execution_authority": False, "skills": results}


def inspect_skill_identities(*, root: Path, installed_root: Path | None = None) -> dict:
    """Snapshot exact source and installed bytes, bounded and never execution authority."""
    repository = Path(root).resolve(strict=True)
    personal = Path(installed_root) if installed_root is not None else Path.home() / ".codex" / "skills"
    key = (str(repository), str(personal))
    now = time.monotonic()
    cached = _CACHE.get(key)
    if _SCAN_LOCK.locked():
        return _unknown_snapshot(repository, personal, "SCAN_BUSY")
    if cached and now - cached[0] <= _CACHE_SECONDS and _watch_valid(cached[2]):
        return deepcopy(cached[1])
    if not _SCAN_LOCK.acquire(blocking=False):
        return _unknown_snapshot(repository, personal, "SCAN_BUSY")
    try:
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] <= _CACHE_SECONDS and _watch_valid(cached[2]):
            return deepcopy(cached[1])
        budget = _Budget()
        try:
            result = _inspect_uncached(repository, personal, budget)
        except ScanLimit:
            result = _unknown_snapshot(repository, personal, "SCAN_LIMIT")
        if len(_CACHE) >= 4:
            _CACHE.clear()
        _CACHE[key] = (time.monotonic(), result, budget.watches)
        return deepcopy(result)
    finally:
        _SCAN_LOCK.release()


def public_skill_identities(*, root: Path, installed_root: Path | None = None) -> dict:
    """UI projection: no personal path, resolved target or file names/digests."""
    snapshot = inspect_skill_identities(root=root, installed_root=installed_root)
    skills = {}
    for skill_id, row in snapshot["skills"].items():
        comparison = row["comparison"]
        skills[skill_id] = {
            "project": {"registry_verified": row["project"]["registry_verified"]},
            "installed": {"state": row["installed"]["state"],
                          "source_type": row["installed"]["source_type"]},
            "comparison": {"status": comparison["status"],
                           "changed_count": len(comparison.get("changed_files", [])),
                           "normalized_changed_count": len(comparison.get("normalized_changed_files", [])),
                           "missing_count": len(comparison.get("missing_files", [])),
                           "extra_count": len(comparison.get("extra_files", []))},
            "installable_in_registry": row.get("installable_in_registry", False),
            "execution_authority": False,
        }
    return {"schema": snapshot["schema"], "observed_at": snapshot["observed_at"],
            "execution_authority": False, "skills": skills}
