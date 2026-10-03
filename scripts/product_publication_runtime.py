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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ServiceSpec(NamedTuple):
    name: str
    port: int
    health_url: str
    expected_service: str
    command: tuple[str, ...]
    root: str = str(ROOT)
    profile_path: str | None = None
    settings_path: str | None = None


def service_specs(
    *, root: str | Path = ROOT, executable: str | Path | None = None,
    include_rus: bool = False, profile_path: str | Path | None = None,
    settings_path: str | Path | None = None,
    product_port: int = 8765,
) -> tuple[ServiceSpec, ...]:
    repository = Path(root).resolve()
    if type(product_port) is not int or not 1 <= product_port <= 65535:
        raise ValueError('product port must be between 1 and 65535')
    if settings_path is not None and profile_path is None:
        raise ValueError('settings path requires an explicit runtime profile')
    python = str(executable or sys.executable)
    specs = (
        ServiceSpec(
            name="product-center",
            port=product_port,
            health_url=f"http://127.0.0.1:{product_port}/api/health",
            expected_service="orbit-hive-local-console",
            command=(
                python,
                str(repository / "main.py"),
                "serve",
                "--port",
                str(product_port),
                "--page",
                "product",
                "--no-browser",
            ),
            root=str(repository),
            profile_path=str(profile_path) if profile_path else None,
            settings_path=str(settings_path) if settings_path else None,
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
            root=str(repository),
            profile_path=str(profile_path) if profile_path else None,
            settings_path=str(settings_path) if settings_path else None,
        ),
    )
    if include_rus:
        specs += (ServiceSpec('orbit-rus', 8767, 'http://127.0.0.1:8767/health',
                              'orbit_rus', (python, str(repository / 'scripts/start_rus_server.py'), '8767'),
                              str(repository), str(profile_path) if profile_path else None,
                              str(settings_path) if settings_path else None),)
    return specs


def _fetch_json(url: str, timeout: float) -> object:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "orbit-runtime-health/1"},
    )
    # Loopback health must not inherit an external HTTP proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
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
        "healthy": state == "READY",
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
    from shared_platform.runtime_identity import compare_identity, expected_identity

    expected = expected_identity(spec.root, spec.expected_service, profile_path=spec.profile_path)
    state = compare_identity(payload, expected)
    return {**service_result(spec, state), 'identity': payload,
            'expected': expected, 'business_execution_verified': False}


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
        "ok": all(row.get("state") == "READY" for row in services.values()),
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


def takeover_publication(
    *,
    expected_commit: str,
    data_root: str | Path,
    offer_id: str,
    source_binder: Callable[[Path, str], dict[str, object]] | None = None,
    inspector: Callable[..., dict[str, object]] | None = None,
) -> dict[str, object]:
    """Bind one existing packet to this exact source without runtime startup.

    ``data_root`` remains an explicit evidence location.  This intentionally
    does not consume ``--profile``: a runtime profile identifies a running
    service's configured stores and must never redirect a historical packet.
    """

    from shared_platform.publication_takeover import inspect_publication, source_binding

    repository = ROOT.resolve()
    binding = (source_binder or source_binding)(repository, expected_commit)
    packet = (inspector or inspect_publication)(offer_id=offer_id, data_root=data_root)
    return {
        **packet,
        "source_binding": binding,
        "runtime_started": False,
        "runtime_profile_consumed": False,
        "data_root_selection": "EXPLICIT_CLI_ARGUMENT",
    }


def runtime_profile_check(
    *,
    profile_path: str | Path | None,
    settings_path: str | Path | None,
    profile_reader: Callable[[Path, str | Path | None], dict[str, object] | None] | None = None,
) -> dict[str, object]:
    """Validate a persistent identity profile without initializing configuration.

    The caller must name both files.  The profile only describes the selected
    configuration and stores; it does not create, copy, or redirect them.
    """

    if profile_path is None or settings_path is None:
        raise ValueError("--profile and --settings-path are required together")
    from shared_platform.runtime_identity import read_profile

    selected = Path(settings_path).expanduser().resolve()
    if not selected.is_file():
        raise ValueError("explicit settings path is not an existing file")
    profile = (profile_reader or read_profile)(ROOT.resolve(), profile_path)
    if profile is None or profile.get("settings_path") != str(selected):
        raise ValueError("runtime profile does not describe the explicit settings path")
    return {
        "ok": True,
        "status": "PROFILE_BOUND",
        "profile_id": profile["profile_id"],
        "settings_path": str(selected),
        "stores": profile["stores"],
        "configuration_initialized": False,
        "configuration_read": False,
        "database_access": False,
        "provider_calls": 0,
        "local_write_count": 0,
        "runtime_started": False,
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

    if spec.settings_path and not spec.profile_path:
        raise ValueError('settings path requires an explicit runtime profile')
    directory = Path(runtime_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stdout_path = directory / f"{spec.name}.stdout.log"
    stderr_path = directory / f"{spec.name}.stderr.log"
    stdout_handle = stdout_path.open("ab", buffering=0)
    stderr_handle = stderr_path.open("ab", buffering=0)
    kwargs: dict[str, object] = {
        "cwd": spec.root,
        "stdin": subprocess.DEVNULL,
        "stdout": stdout_handle,
        "stderr": stderr_handle,
        "close_fds": True,
    }
    if spec.profile_path:
        kwargs['env'] = {**os.environ, 'ORBIT_RUNTIME_PROFILE': spec.profile_path}
    if spec.settings_path:
        kwargs['env'] = {**kwargs.get('env', os.environ), 'ORBIT_HIVE_SETTINGS': spec.settings_path}
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
    if any(
        row.get("state") not in {"STOPPED", "READY"}
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
    mode.add_argument(
        "--takeover-offer-id",
        help="read one existing Offer from an explicit data root; no runtime startup",
    )
    mode.add_argument(
        "--profile-check",
        action="store_true",
        help="read-only validation that an explicit profile describes explicit settings",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME_DIR)
    parser.add_argument("--include-rus", action="store_true", help="also verify the optional Ozon service")
    parser.add_argument("--profile", type=Path, help="explicit non-secret runtime identity profile")
    parser.add_argument("--settings-path", type=Path, help="explicit existing settings file paired with --profile")
    parser.add_argument("--expected-commit", help="exact source commit required by --takeover-offer-id")
    parser.add_argument("--data-root", type=Path, help="absolute historical evidence root required by --takeover-offer-id")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.takeover_offer_id:
        if args.profile is not None or args.include_rus:
            result = {
                "status": "TAKEOVER_REJECTED",
                "reason": "takeover uses only explicit source, commit, data root, and Offer arguments",
                "paid_requests": 0,
                "business_writes": 0,
            }
        elif not args.expected_commit or args.data_root is None:
            result = {
                "status": "TAKEOVER_REJECTED",
                "reason": "--expected-commit and --data-root are required for takeover",
                "paid_requests": 0,
                "business_writes": 0,
            }
        else:
            try:
                result = takeover_publication(
                    expected_commit=args.expected_commit,
                    data_root=args.data_root,
                    offer_id=args.takeover_offer_id,
                )
            except (OSError, ValueError, RuntimeError) as error:
                result = {
                    "status": "TAKEOVER_REJECTED",
                    "reason": str(error),
                    "paid_requests": 0,
                    "business_writes": 0,
                }
    else:
        if args.profile_check:
            try:
                result = runtime_profile_check(profile_path=args.profile, settings_path=args.settings_path)
            except (OSError, ValueError, RuntimeError) as error:
                result = {
                    "ok": False,
                    "status": "PROFILE_REJECTED",
                    "reason": str(error),
                    "provider_calls": 0,
                    "local_write_count": 0,
                    "runtime_started": False,
                }
        else:
            try:
                if args.settings_path is not None:
                    runtime_profile_check(profile_path=args.profile, settings_path=args.settings_path)
                specs = service_specs(
                    include_rus=args.include_rus,
                    profile_path=args.profile,
                    settings_path=args.settings_path,
                )
                if args.takeover_check:
                    result = takeover_check(runtime_probe=lambda: runtime_status(specs=specs))
                elif args.start:
                    result = start_runtime(
                        specs=specs,
                        runtime_dir=args.runtime_dir,
                        timeout_seconds=args.timeout,
                    )
                else:
                    result = runtime_status(specs=specs)
            except (OSError, ValueError, RuntimeError) as error:
                result = {
                    "ok": False,
                    "status": "PROFILE_REJECTED",
                    "reason": str(error),
                    "provider_calls": 0,
                    "local_write_count": 0,
                    "runtime_started": False,
                }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result.get("status") == "READ_ONLY_BOUND":
        return 0
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
