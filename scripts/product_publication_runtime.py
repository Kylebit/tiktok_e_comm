"""Read-only health checks and safe startup for Product Publication services.

This tool never stops or replaces a process.  A bound port whose health
identity is missing or unexpected is treated as a conflict and must be
investigated explicitly.  Only an actually unavailable port is startable.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, NamedTuple
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_DIR = ROOT / "logs" / "product-publication-runtime"
PUBLISH_ENTRY = ROOT / "skills" / "publish-approved-product" / "scripts" / "product_center_publication.py"


class ServiceSpec(NamedTuple):
    name: str
    port: int
    health_url: str
    expected_service: str
    command: tuple[str, ...]


def service_specs(
    *, root: str | Path = ROOT, executable: str | Path | None = None
) -> tuple[ServiceSpec, ...]:
    repository = Path(root).resolve()
    python = str(executable or sys.executable)
    return (
        ServiceSpec(
            name="product-center",
            port=8765,
            health_url="http://127.0.0.1:8765/api/health",
            expected_service="orbit-hive-local-console",
            command=(
                python,
                str(repository / "main.py"),
                "serve",
                "--port",
                "8765",
                "--page",
                "product",
                "--no-browser",
            ),
        ),
        ServiceSpec(
            name="new-product-workbench",
            port=8766,
            health_url="http://127.0.0.1:8766/health",
            expected_service="new_product",
            command=(
                python,
                str(repository / "scripts" / "start_new_product_server.py"),
                "8766",
            ),
        ),
    )


def _fetch_json(url: str, timeout: float) -> object:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "orbit-runtime-health/1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(65_537)
    if len(raw) > 65_536:
        raise ValueError("health response is too large")
    return json.loads(raw.decode("utf-8"))


def _port_is_open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def service_result(
    spec: ServiceSpec, state: str, *, detail: str | None = None
) -> dict[str, object]:
    result: dict[str, object] = {
        "healthy": state == "HEALTHY",
        "state": state,
        "port": spec.port,
        "health_url": spec.health_url,
    }
    if detail:
        result["detail"] = detail
    return result


def probe_service(
    spec: ServiceSpec,
    *,
    timeout: float = 1.0,
    fetch_json: Callable[[str, float], object] | None = None,
    port_check: Callable[[str, int, float], bool] | None = None,
) -> dict[str, object]:
    """Validate both HTTP availability and the expected service identity."""

    fetch = fetch_json or _fetch_json
    check_port = port_check or _port_is_open
    try:
        payload = fetch(spec.health_url, timeout)
    except Exception as error:
        if check_port("127.0.0.1", spec.port, timeout):
            return service_result(
                spec,
                "PORT_IN_USE",
                detail="port is bound but the expected health endpoint is unavailable",
            )
        return service_result(
            spec,
            "STOPPED",
            detail=f"health connection unavailable ({type(error).__name__})",
        )
    if not isinstance(payload, dict):
        return service_result(
            spec, "WRONG_SERVICE", detail="health endpoint returned an invalid payload"
        )
    if payload.get("ok") is not True or payload.get("service") != spec.expected_service:
        return service_result(
            spec, "WRONG_SERVICE", detail="health endpoint identity mismatch"
        )
    return service_result(spec, "HEALTHY")


def runtime_status(
    *,
    specs: Iterable[ServiceSpec] | None = None,
    probe: Callable[[ServiceSpec], dict[str, object]] | None = None,
) -> dict[str, object]:
    """Return service health without creating files or processes."""

    selected = tuple(specs or service_specs())
    check = probe or probe_service
    services = {spec.name: check(spec) for spec in selected}
    return {
        "ok": all(row.get("state") == "HEALTHY" for row in services.values()),
        "services": services,
    }


def _tracked_skill_files(repository: Path) -> tuple[str, ...]:
    result = subprocess.run(
        (
            "git",
            "ls-files",
            "--",
            "skills/prepare-product-publication",
            "skills/prepare-product-images",
            "skills/publish-approved-product",
        ),
        cwd=str(repository),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())


def takeover_check(
    *,
    repository: str | Path = ROOT,
    runtime_probe: Callable[[], dict[str, object]] | None = None,
    parity_check: Callable[[], dict[str, object]] | None = None,
    tracked_files: Callable[[Path], tuple[str, ...]] | None = None,
) -> dict[str, object]:
    """Read-only takeover gate for a fresh Agent or context.

    The check never reads an Offer, calls a provider, starts a process, or
    changes the Skill installation.  It only verifies the two local service
    identities, canonical/installed Skill parity, repository tracking, and the
    one real publication entrypoint.
    """

    repo = Path(repository).resolve()
    runtime = (runtime_probe or runtime_status)()
    if parity_check is None:
        from scripts.sync_product_publication_skills import check_all

        parity = check_all(source_root=repo / "skills")
    else:
        parity = parity_check()
    tracked = set((tracked_files or _tracked_skill_files)(repo))
    canonical_files = {
        path.relative_to(repo).as_posix()
        for name in (
            "prepare-product-publication",
            "prepare-product-images",
            "publish-approved-product",
        )
        for path in (repo / "skills" / name).rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix.lower() not in {".pyc", ".pyo"}
    }
    untracked = sorted(canonical_files - tracked)
    entry = repo / PUBLISH_ENTRY.relative_to(ROOT)
    checks = {
        "runtime": bool(runtime.get("ok")),
        "skill_parity": bool(parity.get("ok")),
        "canonical_skills_tracked": not untracked,
        "publish_entry": entry.is_file(),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "runtime": runtime,
        "skill_suite_digest": parity.get("suite_digest"),
        "untracked_canonical_files": untracked,
        "publish_entry": entry.relative_to(repo).as_posix(),
    }


def _write_pid_record(runtime_dir: Path, spec: ServiceSpec, pid: int) -> None:
    record = {
        "service": spec.name,
        "port": spec.port,
        "pid": int(pid),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    destination = runtime_dir / f"{spec.name}.pid.json"
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def launch_service(
    spec: ServiceSpec,
    *,
    runtime_dir: str | Path = DEFAULT_RUNTIME_DIR,
) -> subprocess.Popen[bytes]:
    """Launch one known service hidden, with durable logs and a PID receipt."""

    directory = Path(runtime_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stdout_path = directory / f"{spec.name}.stdout.log"
    stderr_path = directory / f"{spec.name}.stderr.log"
    stdout_handle = stdout_path.open("ab", buffering=0)
    stderr_handle = stderr_path.open("ab", buffering=0)
    kwargs: dict[str, object] = {
        "cwd": str(ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": stdout_handle,
        "stderr": stderr_handle,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(spec.command, **kwargs)
    finally:
        stdout_handle.close()
        stderr_handle.close()
    _write_pid_record(directory, spec, process.pid)
    return process


def start_runtime(
    *,
    specs: Iterable[ServiceSpec] | None = None,
    probe: Callable[[ServiceSpec], dict[str, object]] | None = None,
    launcher: Callable[[ServiceSpec], object] | None = None,
    runtime_dir: str | Path = DEFAULT_RUNTIME_DIR,
    timeout_seconds: float = 20.0,
    poll_seconds: float = 0.25,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Start only missing services, then wait for exact health identities."""

    selected = tuple(specs or service_specs())
    check = probe or probe_service
    initial = runtime_status(specs=selected, probe=check)
    conflict_states = {"PORT_IN_USE", "WRONG_SERVICE"}
    if any(
        row.get("state") in conflict_states
        for row in initial["services"].values()
    ):
        return {
            **initial,
            "started": {},
            "error": "runtime port conflict; no services were started",
        }

    run = launcher or (
        lambda spec: launch_service(spec, runtime_dir=runtime_dir)
    )
    started: dict[str, int] = {}
    try:
        for spec in selected:
            if initial["services"][spec.name].get("state") != "STOPPED":
                continue
            process = run(spec)
            pid = getattr(process, "pid", None)
            if not isinstance(pid, int) or pid <= 0:
                raise RuntimeError(f"launcher returned no PID for {spec.name}")
            started[spec.name] = pid
    except Exception as error:
        return {
            "ok": False,
            "services": initial["services"],
            "started": started,
            "error": f"service launch failed ({type(error).__name__}): {error}",
        }

    deadline = monotonic() + max(0.0, float(timeout_seconds))
    while True:
        current = runtime_status(specs=selected, probe=check)
        if current["ok"]:
            return {**current, "started": started}
        if monotonic() >= deadline:
            return {
                **current,
                "started": started,
                "error": "services did not become healthy before timeout",
            }
        sleep(max(0.0, float(poll_seconds)))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check or safely start the Product Publication local services."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true", help="read-only health check (default)")
    mode.add_argument("--start", action="store_true", help="start only missing services")
    mode.add_argument(
        "--takeover-check",
        action="store_true",
        help="read-only runtime, Skill parity, git tracking, and entrypoint gate",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME_DIR)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.takeover_check:
        result = takeover_check()
    elif args.start:
        result = start_runtime(
            runtime_dir=args.runtime_dir,
            timeout_seconds=args.timeout,
        )
    else:
        result = runtime_status()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
