"""Check or safely synchronize the repository-owned publication Skill.

The repository tree is authoritative.  Runtime cache directories are ignored;
every other file participates in the manifest and aggregate digest.  The
installer writes files atomically and never deletes unmanaged destination
files, so unexpected drift remains visible to the parity gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import NamedTuple
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / 'shared_platform' / 'capability_runtime.py').is_file():
    raise RuntimeError('Skill sync requires the bundled shared_platform/capability_runtime.py beside its trusted launcher')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from shared_platform.capability_runtime import checked_path, preflight_write_paths, ToolPlanError
DEFAULT_SOURCE = ROOT / "skills" / "publish-approved-product"
# Resolve the legacy personal default only when that CLI route is selected.
# Importing full-file manifest helpers must not discover a personal root.
DEFAULT_DESTINATION = Path("~") / ".codex" / "skills" / "publish-approved-product"
_IGNORED_DIRS = frozenset({"__pycache__", ".pytest_cache"})
_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo"})
_NORMALIZED_TEXT_SUFFIXES = frozenset({".md", ".py", ".yaml", ".yml", ".json"})


class SkillManifest(NamedTuple):
    root: Path
    files: tuple[str, ...]
    hashes: dict[str, str]
    digest: str


def _skill_files(root: Path) -> list[Path]:
    source = root.resolve()
    if not (source / "SKILL.md").is_file():
        raise ValueError(f"not a Skill tree: {source}")
    files: list[Path] = []

    def onerror(error: OSError) -> None:
        raise error

    for current, directories, filenames in os.walk(source, onerror=onerror):
        directories[:] = sorted(
            name for name in directories if name not in _IGNORED_DIRS
        )
        current_path = Path(current)
        for name in sorted(filenames):
            path = current_path / name
            if path.suffix.lower() in _IGNORED_SUFFIXES:
                continue
            if path.is_symlink():
                raise ValueError(f"Skill manifest refuses symlink: {path}")
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(source).as_posix())


def build_manifest(root: str | Path) -> SkillManifest:
    source = checked_path(Path(root), '.').resolve()
    hashes: dict[str, str] = {}
    for path in _skill_files(source):
        relative = path.relative_to(source).as_posix()
        content = path.read_bytes()
        if path.suffix.lower() in _NORMALIZED_TEXT_SUFFIXES:
            try:
                content = (
                    content.decode("utf-8-sig")
                    .replace("\r\n", "\n")
                    .replace("\r", "\n")
                    .encode("utf-8")
                )
            except UnicodeDecodeError as error:
                raise ValueError(f"managed text file is not UTF-8: {path}") from error
        hashes[relative] = hashlib.sha256(content).hexdigest()
    files = tuple(hashes)
    manifest_bytes = json.dumps(
        hashes, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    return SkillManifest(source, files, hashes, digest)


def check_parity(source: str | Path, destination: str | Path) -> dict[str, object]:
    canonical = build_manifest(source)
    installed = build_manifest(destination)
    canonical_files = set(canonical.files)
    installed_files = set(installed.files)
    changed = sorted(
        name
        for name in canonical_files & installed_files
        if canonical.hashes[name] != installed.hashes[name]
    )
    return {
        "ok": canonical.digest == installed.digest,
        "canonical_digest": canonical.digest,
        "installed_digest": installed.digest,
        "missing_files": sorted(canonical_files - installed_files),
        "extra_files": sorted(installed_files - canonical_files),
        "changed_files": changed,
    }


def installation_paths(canonical: SkillManifest, target: Path):
    """All final and same-directory atomic temporary filenames, before any write."""
    paths = [('directory', target)]
    for relative in canonical.files:
        path = target.joinpath(*PureRelativePath(relative).parts)
        paths.append(('file', path))
        paths.append(('temporary_file_template', path.with_name('.'+path.name+'.'+'0'*32+'.tmp')))
    return paths


def preflight_install(canonical: SkillManifest, target: Path) -> dict:
    return preflight_write_paths(target, installation_paths(canonical, target), operation='skill-install')


def sync_install(source: str | Path, destination: str | Path) -> SkillManifest:
    canonical = build_manifest(source)
    target = checked_path(Path(destination), '.').resolve()
    preflight_install(canonical, target)
    completed = []; temp_file = None
    try:
        target.mkdir(parents=True, exist_ok=True)
        for relative in canonical.files:
            source_file = canonical.root.joinpath(*PureRelativePath(relative).parts)
            destination_file = target.joinpath(*PureRelativePath(relative).parts)
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            temp_file = destination_file.with_name(f".{destination_file.name}.{uuid4().hex}.tmp")
            temp_file.write_bytes(source_file.read_bytes())
            os.replace(temp_file, destination_file)
            completed.append(relative)
    except OSError as error:
        raise ToolPlanError('SKILL_INSTALL_IO_INTERRUPTED', destination_root=str(target),
                            completed_files=completed, possible_partial_temp=str(temp_file) if temp_file else None,
                            errno=error.errno, winerror=getattr(error, 'winerror', None),
                            recovery='Retain installation and any temporary file. Inspect source/target parity and the listed completed files before selecting an explicit restore or new destination; no automatic cleanup or retry.') from error
    parity = check_parity(canonical.root, target)
    if not parity["ok"]:
        raise ValueError(
            "installed Skill contains unmanaged or divergent files: "
            + json.dumps(parity, ensure_ascii=False, sort_keys=True)
        )
    return build_manifest(target)


class PureRelativePath:
    """Validated POSIX manifest path independent of host separators."""

    def __init__(self, value: str) -> None:
        if type(value) is not str or not value:
            raise ValueError("manifest path must be a non-empty string")
        parts = tuple(value.split("/"))
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("manifest path is not relative")
        self.parts = parts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--install", action="store_true")
    args = parser.parse_args()
    args.destination = args.destination.expanduser()

    try:
        if args.install:
            manifest = sync_install(args.source, args.destination)
            result = {"ok": True, "installed_digest": manifest.digest}
        else:
            preflight_install(build_manifest(args.source), checked_path(args.destination, '.'))
            result = check_parity(args.source, args.destination)
    except ToolPlanError as error:
        result = error.diagnostic
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
