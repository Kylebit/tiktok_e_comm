from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class ServiceSpec:
    key: str
    port: int
    health_url: str
    command: list[str]
    cwd: Path
    required: bool = True


def _user_agent() -> str:
    return "OrbitDesktopLauncher/1.0"


def _health_check(url: str, timeout: float = 2.5) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        if not body:
            return True, "empty-body"
        try:
            payload = json.loads(body)
            return True, json.dumps(payload, ensure_ascii=False)
        except Exception:
            return True, body[:160]
    except urllib.error.URLError as exc:
        return False, str(exc.reason or exc)
    except Exception as exc:
        return False, str(exc)


def _port_open(port: int, timeout: float = 0.6) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def _start_background(command: list[str], cwd: Path) -> subprocess.Popen:
    kwargs: dict[str, object] = {
        "cwd": str(cwd),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
            | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        )
    return subprocess.Popen(command, **kwargs)


def _service_specs(py_exe: str) -> list[ServiceSpec]:
    specs = [
        ServiceSpec(
            key="os",
            port=8765,
            health_url="http://127.0.0.1:8765/api/health",
            command=[py_exe, "main.py", "serve", "--port", "8765", "--no-browser"],
            cwd=ROOT,
        ),
        ServiceSpec(
            key="treasury",
            port=8766,
            health_url="http://127.0.0.1:8766/health",
            command=[py_exe, str(ROOT / "scripts" / "start_new_product_server.py"), "8766"],
            cwd=ROOT,
        ),
        ServiceSpec(
            key="rus",
            port=8767,
            health_url="http://127.0.0.1:8767/health",
            command=[py_exe, str(ROOT / "scripts" / "start_rus_server.py"), "8767"],
            cwd=ROOT,
        ),
    ]
    eyes_script = ROOT / "scripts" / "start_eyes_server.py"
    if eyes_script.is_file():
        specs.append(
            ServiceSpec(
                key="eyes",
                port=8768,
                health_url="http://127.0.0.1:8768/health",
                command=[py_exe, str(eyes_script), "8768"],
                cwd=ROOT,
                required=False,
            )
        )
    return specs


def ensure_services(py_exe: str, timeout: float = 25.0, verbose: bool = False) -> dict[str, dict[str, object]]:
    specs = _service_specs(py_exe)
    started: dict[str, bool] = {}

    for spec in specs:
        healthy, detail = _health_check(spec.health_url, timeout=1.2)
        if healthy:
            started[spec.key] = False
            if verbose:
                print(f"[Orbit Desktop] {spec.key} already healthy: {detail}")
            continue
        if _port_open(spec.port):
            started[spec.key] = False
            if verbose:
                print(f"[Orbit Desktop] {spec.key} port {spec.port} is open, waiting for health...")
            continue
        if verbose:
            print(f"[Orbit Desktop] starting {spec.key}: {' '.join(spec.command)}")
        _start_background(spec.command, spec.cwd)
        started[spec.key] = True

    deadline = time.time() + timeout
    results: dict[str, dict[str, object]] = {}
    while time.time() < deadline:
        pending = False
        results.clear()
        for spec in specs:
            healthy, detail = _health_check(spec.health_url, timeout=1.2)
            port_open = _port_open(spec.port)
            if spec.required and not healthy:
                pending = True
            results[spec.key] = {
                "healthy": healthy,
                "detail": detail,
                "port": spec.port,
                "port_open": port_open,
                "started_now": started.get(spec.key, False),
                "required": spec.required,
            }
        if not pending:
            return results
        time.sleep(1.0)
    return results


def _message_box(title: str, body: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, body, title, 0x10)
    except Exception:
        pass


def _run_shell() -> int:
    try:
        from desktop.orbit_desktop_webview import main as run_app
        print("[Orbit Desktop] using webview shell")
    except Exception as exc:
        _message_box(
            "启动失败",
            f"Orbit Desktop 无法启动 WebView 桌面壳:\n\n{exc}\n\n请检查 Python 运行时和 pywebview 依赖是否完整。",
        )
        raise
    return run_app()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start Orbit Desktop and bootstrap local services.")
    parser.add_argument("--bootstrap-only", action="store_true", help="Only start/wait for backend services.")
    parser.add_argument("--skip-bootstrap", action="store_true", help="Launch shell without checking services.")
    parser.add_argument("--timeout", type=float, default=25.0, help="Service bootstrap timeout in seconds.")
    parser.add_argument("--verbose", action="store_true", help="Print bootstrap progress.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    py_exe = sys.executable

    if not args.skip_bootstrap:
        results = ensure_services(py_exe, timeout=args.timeout, verbose=args.verbose)
        failed = [
            f"{key}:{info['detail']}"
            for key, info in results.items()
            if info.get("required") and not info.get("healthy")
        ]
        if args.verbose or args.bootstrap_only:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        if failed:
            joined = "\n".join(failed)
            _message_box(
                "启动失败",
                f"Orbit Desktop 后端服务未能全部启动:\n\n{joined}\n\n请检查本地 Python 进程和端口占用。",
            )
            return 1
        if args.bootstrap_only:
            return 0
    elif args.bootstrap_only:
        return 0

    return _run_shell()


if __name__ == "__main__":
    raise SystemExit(main())
