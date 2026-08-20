"""Check or install the complete Product Publication Skill set.

The repository copies are authoritative.  Installation is deliberately
fail-closed: all three source trees and every destination are preflighted
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
)


SKILL_NAMES = (
    "prepare-product-publication",
    "prepare-product-images",
    "publish-approved-product",
)
DEFAULT_SOURCE_ROOT = ROOT / "skills"
DEFAULT_DESTINATION_ROOT = Path.home() / ".codex" / "skills"


class SkillSetInstallError(ValueError):
    """The complete Skill set cannot be installed without unsafe cleanup."""


def _suite_digest(manifests: dict[str, SkillManifest]) -> str:
    payload = {name: manifests[name].digest for name in SKILL_NAMES}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _resolved_roots(
    source_root: str | Path,
    destination_root: str | Path,
) -> tuple[Path, Path]:
    source = Path(source_root).expanduser().resolve()
    destination = Path(destination_root).expanduser().resolve()
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


def _canonical_manifests(source_root: Path) -> dict[str, SkillManifest]:
    manifests: dict[str, SkillManifest] = {}
    for name in SKILL_NAMES:
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
) -> dict[str, object]:
    """Return one deterministic parity report for all production Skills."""

    source, destination = _resolved_roots(source_root, destination_root)
    canonical = _canonical_manifests(source)
    skills: dict[str, dict[str, object]] = {}
    for name in SKILL_NAMES:
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
    for name in SKILL_NAMES:
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
) -> dict[str, object]:
    """Install all canonical Skills after a suite-wide no-write preflight."""

    source, destination = _resolved_roots(source_root, destination_root)
    canonical = _canonical_manifests(source)
    _preflight_destinations(canonical, destination)
    for name in SKILL_NAMES:
        try:
            sync_install(canonical[name].root, destination / name)
        except (OSError, ValueError) as error:
            raise SkillSetInstallError(f"failed to install {name}: {error}") from error
    result = check_all(source_root=source, destination_root=destination)
    if not result["ok"]:
        raise SkillSetInstallError(
            "installed Skill set failed parity: "
            + json.dumps(result, ensure_ascii=False, sort_keys=True)
        )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check or install all Product Publication Skills."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="check only (default)")
    mode.add_argument("--install", action="store_true", help="install all three Skills")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument(
        "--destination-root", type=Path, default=DEFAULT_DESTINATION_ROOT
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.install:
            result = install_all(
                source_root=args.source_root,
                destination_root=args.destination_root,
            )
        else:
            result = check_all(
                source_root=args.source_root,
                destination_root=args.destination_root,
            )
    except (OSError, ValueError) as error:
        result = {"ok": False, "error": str(error)}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
