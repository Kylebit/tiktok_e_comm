"""Allocate and validate short, owned Windows regression run directories."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "orbit-regression-envelope/v1"
PHASES = ("focused", "browser", "full")
PHASE_NAMES = {"focused": "f", "browser": "b", "full": "a"}
TERMINAL_STATES = {"passed", "failed", "interrupted"}
MAX_SCRATCH_ROOT_LENGTH = 12
MAX_PHASE_PATH_LENGTH = 40


class EnvelopeError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolved(path: str | os.PathLike[str], *, strict: bool = False) -> Path:
    return Path(path).expanduser().resolve(strict=strict)


def _is_reparse(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    attrs = getattr(details, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attrs & reparse)


def _assert_no_reparse_between(path: Path, stop: Path) -> None:
    current = path
    while True:
        if _is_reparse(current):
            raise EnvelopeError(f"reparse points are not allowed: {current}")
        if current == stop:
            return
        if current.parent == current:
            raise EnvelopeError(f"path is not below expected root: {path}")
        current = current.parent


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _local_absolute(path: Path) -> bool:
    return path.is_absolute() and bool(path.drive) and not str(path).startswith("\\\\")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvelopeError(f"invalid owner marker {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EnvelopeError(f"owner marker is not an object: {path}")
    return value


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    temp_path.write_text(payload, encoding="utf-8")
    os.replace(temp_path, path)


def _reject_sensitive_root(scratch: Path, repo_root: Path) -> None:
    drive_root = Path(scratch.anchor).resolve()
    home = Path.home().resolve()
    if scratch in {drive_root, home, repo_root}:
        raise EnvelopeError(f"scratch root is a protected path: {scratch}")
    if _is_relative_to(repo_root, scratch) or _is_relative_to(scratch, repo_root):
        raise EnvelopeError("scratch root and repository must not contain each other")
    inherited_ops = os.environ.get("ORBIT_OPERATIONS_DATA_ROOT")
    if inherited_ops:
        stable = _resolved(inherited_ops)
        if scratch == stable or _is_relative_to(scratch, stable) or _is_relative_to(stable, scratch):
            raise EnvelopeError("scratch root overlaps the inherited operations data root")


def validate_scratch_root(scratch_root: str, repo_root: str) -> Path:
    scratch = _resolved(scratch_root)
    repo = _resolved(repo_root, strict=True)
    if not _local_absolute(scratch):
        raise EnvelopeError("scratch root must be an absolute local-drive path")
    if len(str(scratch)) > MAX_SCRATCH_ROOT_LENGTH:
        raise EnvelopeError(
            f"scratch root is too long ({len(str(scratch))}>{MAX_SCRATCH_ROOT_LENGTH}): {scratch}"
        )
    _reject_sensitive_root(scratch, repo)
    scratch.mkdir(parents=True, exist_ok=True)
    _assert_no_reparse_between(scratch, Path(scratch.anchor).resolve())
    try:
        with tempfile.NamedTemporaryFile(dir=scratch, prefix="write-", delete=True):
            pass
    except OSError as exc:
        raise EnvelopeError(f"scratch root is not writable: {scratch}: {exc}") from exc
    return scratch


def resolve_gate_settings(repo_root: str, configured: str | None = None) -> dict[str, Any]:
    """Resolve the gate settings without silently accepting a missing file."""
    repo = _resolved(repo_root, strict=True)
    if configured is not None:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = repo / candidate
        inherited = True
    else:
        candidate = repo / "config" / "settings.example.json"
        inherited = False
    try:
        settings = candidate.resolve(strict=True)
    except OSError as exc:
        source = "configured ORBIT_HIVE_SETTINGS" if inherited else "repository default settings"
        raise EnvelopeError(f"{source} file does not exist: {candidate}") from exc
    if not settings.is_file():
        raise EnvelopeError(f"gate settings path is not a file: {settings}")
    return {"path": str(settings), "inherited": inherited}


def _path_budget_probe(phase_temp: Path) -> None:
    probe = phase_temp / "test_known_deep_fixture0" / "project" / "tikhub"
    probe = probe / ("a" * 64) / ("b" * 24)
    if len(str(probe / "manifest.json.tmp")) >= 260:
        raise EnvelopeError(f"Windows path budget unavailable below {phase_temp}")
    probe.mkdir(parents=True)
    temp_file = probe / "manifest.json.tmp"
    final_file = probe / "manifest.json"
    temp_file.write_text('{"probe": true}\n', encoding="utf-8")
    os.replace(temp_file, final_file)
    if json.loads(final_file.read_text(encoding="utf-8")) != {"probe": True}:
        raise EnvelopeError(f"path budget readback failed below {phase_temp}")
    final_file.unlink()
    shutil.rmtree(phase_temp / "test_known_deep_fixture0")


def allocate_envelope(scratch_root: str, repo_root: str, head: str, owner_pid: int) -> dict[str, Any]:
    scratch = validate_scratch_root(scratch_root, repo_root)
    head = head.strip().lower()
    if len(head) != 40 or any(char not in "0123456789abcdef" for char in head):
        raise EnvelopeError("HEAD must be a full 40-character hexadecimal commit")
    for _ in range(10):
        run_id = f"{owner_pid}-{uuid.uuid4().hex[:8]}"
        run_root = scratch / run_id
        try:
            run_root.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise EnvelopeError("could not allocate a unique regression run directory")

    if run_root.parent != scratch:
        raise EnvelopeError("allocated run root is not a direct scratch-root child")
    phases: dict[str, dict[str, str]] = {}
    try:
        for phase in PHASES:
            short = PHASE_NAMES[phase]
            phase_temp = run_root / f"{short}-t"
            phase_ops = run_root / f"{short}-o"
            if max(len(str(phase_temp)), len(str(phase_ops))) > MAX_PHASE_PATH_LENGTH:
                raise EnvelopeError(f"phase path exceeds short-path budget: {phase_temp}")
            phase_temp.mkdir()
            phase_ops.mkdir()
            phases[phase] = {"basetemp": str(phase_temp), "operations_root": str(phase_ops)}
            phase_marker = {
                "schema": SCHEMA,
                "kind": "phase",
                "run_id": run_id,
                "phase": phase,
                "run_root": str(run_root),
                "path": str(phase_ops),
                "repo_root": str(_resolved(repo_root, strict=True)),
                "head": head,
                "owner_pid": owner_pid,
                "created_at": _utc_now(),
            }
            _atomic_write_json(phase_ops / "owner.json", phase_marker)
            _path_budget_probe(phase_temp)
        (run_root / "logs").mkdir()
        marker = {
            "schema": SCHEMA,
            "kind": "run",
            "run_id": run_id,
            "path": str(run_root),
            "scratch_root": str(scratch),
            "repo_root": str(_resolved(repo_root, strict=True)),
            "head": head,
            "owner_pid": owner_pid,
            "created_at": _utc_now(),
            "status": "running",
            "phases": phases,
        }
        _atomic_write_json(run_root / "owner.json", marker)
        return marker
    except Exception:
        shutil.rmtree(run_root, ignore_errors=True)
        raise


def validate_test_operations_root(operations_root: str, run_id: str, phase: str) -> Path:
    root = _resolved(operations_root, strict=True)
    if not root.is_dir() or not _local_absolute(root):
        raise EnvelopeError("test operations root must be an existing absolute local directory")
    marker = _load_json(root / "owner.json")
    expected = {"schema": SCHEMA, "kind": "phase", "run_id": run_id, "phase": phase, "path": str(root)}
    for key, value in expected.items():
        if marker.get(key) != value:
            raise EnvelopeError(f"phase owner marker mismatch for {key}")
    run_root = _resolved(marker.get("run_root", ""), strict=True)
    if root.parent != run_root or run_root.parent == run_root:
        raise EnvelopeError("test operations root is not directly owned by its run root")
    run_marker = _load_json(run_root / "owner.json")
    if (
        run_marker.get("schema") != SCHEMA
        or run_marker.get("kind") != "run"
        or run_marker.get("run_id") != run_id
        or run_marker.get("path") != str(run_root)
        or run_marker.get("phases", {}).get(phase, {}).get("operations_root") != str(root)
    ):
        raise EnvelopeError("run owner marker does not bind the phase operations root")
    _assert_no_reparse_between(root, _resolved(run_marker["scratch_root"], strict=True))
    return root


def configure_test_operations_environment(environment: dict[str, str]) -> Path | None:
    """Clear workstation state and validate the marker-bound test ledger.

    The ledger remains opt-in for individual tests.  Exposing it as the
    process-wide operations data root changes product-domain behavior in
    otherwise unconfigured tests (for example, it enables publication task
    coordination).  Tests that need a ledger can use the validated path from
    ORBIT_TEST_OPERATIONS_DATA_ROOT explicitly.
    """
    inherited = environment.pop("ORBIT_OPERATIONS_DATA_ROOT", None)
    environment.pop("ORBIT_PUBLICATION_REQUIRE_VERIFIED_RUNTIME", None)
    requested = environment.get("ORBIT_TEST_OPERATIONS_DATA_ROOT")
    run_id = environment.get("ORBIT_TEST_RUN_ID")
    phase = environment.get("ORBIT_TEST_PHASE")
    if not any((requested, run_id, phase)):
        return None
    if not all((requested, run_id, phase)):
        raise EnvelopeError("incomplete regression envelope environment")
    validated = validate_test_operations_root(requested, run_id, phase)
    if inherited and _resolved(inherited) == validated:
        raise EnvelopeError("test operations root must differ from inherited stable root")
    return validated


def finalize_envelope(run_root: str, status: str, head: str) -> dict[str, Any]:
    root = _resolved(run_root, strict=True)
    marker_path = root / "owner.json"
    marker = _load_json(marker_path)
    if marker.get("schema") != SCHEMA or marker.get("kind") != "run":
        raise EnvelopeError("run owner marker schema mismatch")
    if marker.get("path") != str(root) or marker.get("head") != head.lower():
        raise EnvelopeError("run owner marker identity mismatch")
    if status not in TERMINAL_STATES:
        raise EnvelopeError(f"invalid terminal state: {status}")
    marker["status"] = status
    marker["finished_at"] = _utc_now()
    _atomic_write_json(marker_path, marker)
    return marker


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if process:
            ctypes.windll.kernel32.CloseHandle(process)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cleanup_envelope(run_root: str, scratch_root: str, head: str) -> None:
    scratch = _resolved(scratch_root, strict=True)
    root = _resolved(run_root, strict=True)
    if root.parent != scratch or root == scratch:
        raise EnvelopeError("cleanup target must be one direct child of the scratch root")
    _assert_no_reparse_between(root, scratch)
    marker = _load_json(root / "owner.json")
    expected = {"schema": SCHEMA, "kind": "run", "path": str(root), "scratch_root": str(scratch), "head": head.lower()}
    for key, value in expected.items():
        if marker.get(key) != value:
            raise EnvelopeError(f"cleanup owner marker mismatch for {key}")
    if marker.get("status") not in TERMINAL_STATES:
        raise EnvelopeError("cleanup refuses a non-terminal run")
    if _pid_alive(int(marker.get("owner_pid", 0))):
        raise EnvelopeError("cleanup refuses a run whose owner PID is still alive")
    for path in root.rglob("*"):
        if _is_reparse(path):
            raise EnvelopeError(f"cleanup refuses a descendant reparse point: {path}")
        if path.name == ".git":
            raise EnvelopeError("cleanup refuses a run containing a Git checkout")
    shutil.rmtree(root)


def _default_scratch_root() -> str:
    for candidate in (r"D:\orbt", r"C:\orbt"):
        if Path(candidate).drive and Path(Path(candidate).anchor).exists():
            return candidate
    raise EnvelopeError("no local short scratch drive is available")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    allocate = subparsers.add_parser("allocate")
    allocate.add_argument("--scratch-root", default=os.environ.get("ORBIT_REGRESSION_SCRATCH_ROOT") or _default_scratch_root())
    allocate.add_argument("--repo-root", required=True)
    allocate.add_argument("--head", required=True)
    allocate.add_argument("--owner-pid", required=True, type=int)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--run-root", required=True)
    finalize.add_argument("--status", choices=sorted(TERMINAL_STATES), required=True)
    finalize.add_argument("--head", required=True)
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--run-root", required=True)
    cleanup.add_argument("--scratch-root", required=True)
    cleanup.add_argument("--head", required=True)
    settings = subparsers.add_parser("settings")
    settings.add_argument("--repo-root", required=True)
    settings.add_argument("--configured")
    args = parser.parse_args(argv)
    try:
        if args.command == "allocate":
            value = allocate_envelope(args.scratch_root, args.repo_root, args.head, args.owner_pid)
            print(json.dumps(value, ensure_ascii=False))
        elif args.command == "finalize":
            value = finalize_envelope(args.run_root, args.status, args.head)
            print(json.dumps(value, ensure_ascii=False))
        elif args.command == "cleanup":
            cleanup_envelope(args.run_root, args.scratch_root, args.head)
            print(json.dumps({"deleted": str(_resolved(args.run_root))}, ensure_ascii=False))
        else:
            print(json.dumps(resolve_gate_settings(args.repo_root, args.configured), ensure_ascii=False))
        return 0
    except EnvelopeError as exc:
        print(f"regression envelope error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
