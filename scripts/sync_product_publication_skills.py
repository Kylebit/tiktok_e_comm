"""Check or install all or explicitly selected Product Publication Skills.

The repository copies are authoritative.  Installation is deliberately
fail-closed: all source trees and every destination are preflighted
before the first file is written, and unmanaged destination files are never
deleted or overwritten as a side effect of resolving drift.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sync_publish_approved_product_skill import (  # noqa: E402
    SkillManifest,
    build_manifest,
    check_parity,
    sync_install,
    installation_paths,
)
from shared_platform.capability_runtime import preflight_write_paths, ToolPlanError


SKILL_NAMES = (
    "prepare-product-publication",
    "prepare-product-images",
    "publish-approved-product",
    "apply-product-discounts",
)
DEFAULT_SOURCE_ROOT = ROOT / "skills"
DEFAULT_DESTINATION_ROOT = Path("~") / ".codex" / "skills"


class SkillSetInstallError(ValueError):
    """The complete Skill set cannot be installed without unsafe cleanup."""


def _selected_skill_names(names: Iterable[str] | None = None) -> tuple[str, ...]:
    selected = tuple(names or SKILL_NAMES)
    if not selected or len(selected) != len(set(selected)):
        raise SkillSetInstallError("selected Skills must be unique and nonempty")
    unknown = set(selected) - set(SKILL_NAMES)
    if unknown:
        raise SkillSetInstallError(
            "selected Skill is not in the Product Publication Skill set: "
            + ", ".join(sorted(unknown))
        )
    return tuple(name for name in SKILL_NAMES if name in selected)


def _suite_digest(manifests: dict[str, SkillManifest]) -> str:
    payload = {
        name: manifests[name].digest
        for name in SKILL_NAMES
        if name in manifests
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _resolved_roots(
    source_root: str | Path,
    destination_root: str | Path,
) -> tuple[Path, Path]:
    from shared_platform.capability_runtime import checked_path
    source = checked_path(Path(source_root).expanduser(), '.').resolve()
    destination = checked_path(Path(destination_root).expanduser(), '.').resolve()
    if source == destination:
        raise SkillSetInstallError("source and destination Skill roots must differ")
    return source, destination


def _refuse_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise SkillSetInstallError(f"Skill root must not be a symlink: {root}")
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_symlink():
            raise SkillSetInstallError(f"Skill tree contains a symlink: {path}")


def _canonical_manifests(
    source_root: Path,
    names: Iterable[str] | None = None,
) -> dict[str, SkillManifest]:
    manifests: dict[str, SkillManifest] = {}
    for name in _selected_skill_names(names):
        skill = source_root / name
        _refuse_symlinks(skill)
        try:
            manifests[name] = build_manifest(skill)
        except (OSError, ValueError) as error:
            raise SkillSetInstallError(
                f"canonical Skill {name!r} is invalid: {error}"
            ) from error
    return manifests


def _existing_paths(root: Path) -> tuple[str, ...]:
    if not root.exists():
        return ()
    return tuple(
        sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and ".pytest_cache" not in path.parts
            and path.suffix.lower() not in {".pyc", ".pyo"}
        )
    )


def _missing_destination_result(
    canonical: SkillManifest,
    destination: Path,
) -> dict[str, object]:
    actual = set(_existing_paths(destination))
    expected = set(canonical.files)
    return {
        "ok": False,
        "canonical_digest": canonical.digest,
        "installed_digest": None,
        "missing_files": sorted(expected - actual),
        "extra_files": sorted(actual - expected),
        "changed_files": [],
    }


def check_all(
    *,
    source_root: str | Path = DEFAULT_SOURCE_ROOT,
    destination_root: str | Path = DEFAULT_DESTINATION_ROOT,
    skill_names: Iterable[str] | None = None,
) -> dict[str, object]:
    """Return one deterministic parity report for the selected Skills."""

    source, destination = _resolved_roots(source_root, destination_root)
    selected = _selected_skill_names(skill_names)
    canonical = _canonical_manifests(source, selected)
    preflight_write_paths(destination, [item for name, manifest in canonical.items()
                                      for item in installation_paths(manifest, destination/name)],
                          operation='skill-suite-check')
    skills: dict[str, dict[str, object]] = {}
    for name in selected:
        installed = destination / name
        try:
            _refuse_symlinks(installed)
            if not (installed / "SKILL.md").is_file():
                result = _missing_destination_result(canonical[name], installed)
            else:
                result = check_parity(canonical[name].root, installed)
        except (OSError, ValueError) as error:
            result = _missing_destination_result(canonical[name], installed)
            result["error"] = f"destination Skill is invalid: {error}"
        skills[name] = result
    return {
        "ok": all(bool(row.get("ok")) for row in skills.values()),
        "suite_digest": _suite_digest(canonical),
        "skills": skills,
    }


def _preflight_destinations(
    canonical: dict[str, SkillManifest],
    destination_root: Path,
) -> None:
    errors: list[str] = []
    for name in canonical:
        destination = destination_root / name
        _refuse_symlinks(destination)
        existing = set(_existing_paths(destination))
        extras = sorted(existing - set(canonical[name].files))
        if extras:
            errors.append(f"{name}: unmanaged files: {', '.join(extras)}")
        elif existing and not (destination / "SKILL.md").is_file():
            errors.append(f"{name}: destination is not a valid Skill tree")
    if errors:
        raise SkillSetInstallError("; ".join(errors))


def install_all(
    *,
    source_root: str | Path = DEFAULT_SOURCE_ROOT,
    destination_root: str | Path = DEFAULT_DESTINATION_ROOT,
    skill_names: Iterable[str] | None = None,
) -> dict[str, object]:
    """Install selected canonical Skills after a complete no-write preflight."""

    source, destination = _resolved_roots(source_root, destination_root)
    selected = _selected_skill_names(skill_names)
    canonical = _canonical_manifests(source, selected)
    _preflight_destinations(canonical, destination)
    preflight_write_paths(destination, [item for name, manifest in canonical.items()
                                      for item in installation_paths(manifest, destination/name)],
                          operation='skill-suite-install')
    completed = []
    for name in selected:
        try:
            sync_install(canonical[name].root, destination / name)
            completed.append(name)
        except (OSError, ValueError) as error:
            raise ToolPlanError('SKILL_SUITE_WRITE_INTERRUPTED', failed_skill=name,
                                completed_skills=completed, destination_root=str(destination),
                                cause=error.diagnostic if isinstance(error, ToolPlanError) else str(error),
                                recovery='Retain all results; inspect suite check/parity before explicitly restoring or selecting a new destination.') from error
    result = check_all(
        source_root=source,
        destination_root=destination,
        skill_names=selected,
    )
    if not result["ok"]:
        raise SkillSetInstallError(
            "installed Skill set failed parity: "
            + json.dumps(result, ensure_ascii=False, sort_keys=True)
        )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check or install Product Publication Skills."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="check only (default)")
    mode.add_argument(
        "--install",
        action="store_true",
        help="install the complete or explicitly selected Skill set",
    )
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--registry", action="store_true", help="use the complete capability registry")
    parser.add_argument("--runtime-root", type=Path, default=ROOT)
    parser.add_argument("--skill", action="append", default=[], help="explicit Skill ID; repeatable")
    parser.add_argument(
        "--destination-root", type=Path, default=None
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.registry:
            if args.destination_root is None:
                raise SkillSetInstallError('registry mode requires an explicit --destination-root')
            result = sync_registered(runtime_root=args.runtime_root, destination_root=args.destination_root,
                                     names=args.skill, install=args.install)
        elif args.install:
            result = install_all(
                source_root=args.source_root,
                destination_root=args.destination_root or DEFAULT_DESTINATION_ROOT,
                skill_names=args.skill or None,
            )
        else:
            result = check_all(
                source_root=args.source_root,
                destination_root=args.destination_root or DEFAULT_DESTINATION_ROOT,
                skill_names=args.skill or None,
            )
    except (OSError, ValueError) as error:
        result = error.diagnostic if isinstance(error, ToolPlanError) else {"ok": False, "error": str(error)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") is True else 1


def sync_registered(*, runtime_root: Path, destination_root: Path, names: list[str], install: bool = False) -> dict:
    """Reuse full Skill manifests, preserving extras and immutable previous versions."""
    from shared_platform.capability_runtime import catalog, checked_path, tree_files, validate_runtime
    import shutil
    runtime_root = checked_path(runtime_root, ".").resolve()
    destination_root = checked_path(destination_root, ".").resolve()
    validate_runtime(runtime_root)
    entries = {r['id']: r for r in catalog(runtime_root)['skills'] if r.get('installable')}
    names = names or sorted(entries)
    if len(names) != len(set(names)) or set(names) - set(entries):
        raise SkillSetInstallError('selected Skill is not an installable current registry source')
    sources = {}; results = {}
    # Full source and destination checks happen before any backup or installation.
    for name in names:
        source = checked_path(runtime_root, entries[name]['source_path'])
        destination = checked_path(destination_root, name)
        tree_files(source); tree_files(destination)
        canonical = build_manifest(source); sources[name] = canonical
        expected = entries[name]['file_digests']
        actual = {source.relative_to(runtime_root).as_posix()+'/'+p: sha for p, sha in canonical.hashes.items()}
        if actual != expected:
            raise SkillSetInstallError('Skill source differs from the registered full-file digest: '+name)
        if not (destination/'SKILL.md').is_file(): result = _missing_destination_result(canonical, destination)
        else: result = check_parity(source, destination)
        results[name] = result
        if install and result['extra_files']:
            raise SkillSetInstallError('unmanaged files retained; inspect extras before install: '+name+': '+', '.join(result['extra_files']))
    # Compute every selected install and complete previous tree before the first
    # backup/copy. Backup includes cache/empty directories that copytree preserves.
    planned = []; previous_trees = {}
    for name in names:
        destination = checked_path(destination_root, name)
        planned.extend(installation_paths(sources[name], destination))
        if destination.exists() and any(destination.iterdir()):
            previous = build_manifest(destination)
            backup = checked_path(destination_root, '.history/'+name+'/'+previous.digest)
            previous_trees[name] = (previous, backup)
            planned.append(('directory', backup))
            pending = [destination]
            while pending:
                folder = pending.pop()
                for child in folder.iterdir():
                    child = checked_path(destination, child)
                    is_directory = child.is_dir()
                    planned.append(('directory' if is_directory else 'file', backup/child.relative_to(destination)))
                    if is_directory: pending.append(child)
    path_preflight = preflight_write_paths(destination_root, planned, operation='registered-skill-install')
    for name, (previous, backup) in previous_trees.items():
        if backup.exists():
            tree_files(backup)
            try: actual = build_manifest(backup)
            except (OSError, ValueError): actual = None
            if actual is None or actual.digest != previous.digest:
                raise ToolPlanError('PARTIAL_BACKUP_REQUIRES_REVIEW', skill=name, backup_path=str(backup),
                                    expected_digest=previous.digest, actual_digest=actual.digest if actual else None,
                                    writes_performed=[], recovery='Retain this partial backup and current installation. Inspect exact manifest/parity; select a new explicit shallow destination if needed. Do not delete or overwrite this backup automatically.')
    backups = {}
    if install:
        completed = []; active_name = None; stage = 'not-started'
        try:
            for name in names:
                active_name = name
                destination = checked_path(destination_root, name)
                if destination.exists() and any(destination.iterdir()):
                    previous, backup = previous_trees[name]
                    if not backup.exists():
                        stage = 'creating-backup'
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(destination, backup)
                    tree_files(backup)
                    if build_manifest(backup).digest != previous.digest: raise SkillSetInstallError('previous-version backup mismatch')
                    backups[name] = backup.relative_to(destination_root).as_posix()
                stage = 'installing'
                sync_install(sources[name].root, destination)
                completed.append(name)
                results[name] = check_parity(sources[name].root, destination)
        except (OSError, ValueError) as error:
            raise ToolPlanError('REGISTERED_SKILL_WRITE_INTERRUPTED', failed_skill=active_name,
                                stage=stage, completed_skills=completed, destination_root=str(destination_root),
                                possible_backup_paths={name:str(value[1]) for name,value in previous_trees.items()},
                                verified_backups=backups,
                                cause=error.diagnostic if isinstance(error, ToolPlanError) else str(error),
                                recovery='Retain current installation and all partial backup/temp files. Rerun check to inspect path support and exact backup manifest; choose explicit restoration from a verified complete backup or a new shallow destination. No automatic deletion/retry.') from error
    return {'ok': all(r['ok'] for r in results.values()), 'mode': 'install' if install else 'check',
            'skills': results, 'backups': backups, 'destination_root': str(destination_root),
            'path_preflight': path_preflight}


if __name__ == "__main__":
    raise SystemExit(main())
