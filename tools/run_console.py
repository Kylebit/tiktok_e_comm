from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
LOG_DIR = ROOT / "data"
OUT_LOG = LOG_DIR / "new_product_server.log"
ERR_LOG = LOG_DIR / "new_product_server.err.log"


def _netstat_listeners() -> set[int]:
    proc = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    pids: set[int] = set()
    needle = f"127.0.0.1:{PORT}"
    for line in proc.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[1] == needle and fields[3].upper() == "LISTENING":
            try:
                pids.add(int(fields[-1]))
            except ValueError:
                pass
    return pids


def _taskkill(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def stop_existing() -> list[int]:
    pids = sorted(_netstat_listeners())
    for pid in pids:
        if pid != os.getpid():
            _taskkill(pid)
    deadline = time.time() + 5
    while time.time() < deadline:
        remaining = _netstat_listeners()
        if not remaining:
            return pids
        time.sleep(0.2)
    return pids


def _python_executable() -> str:
    preferred = Path(r"C:\Users\Windows11\AppData\Local\Programs\Python\Python39\python.exe")
    return str(preferred) if preferred.is_file() else sys.executable


def start_server() -> subprocess.Popen:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("ORBIT_STARTUP_REFRESH", None)
    out = OUT_LOG.open("ab")
    err = ERR_LOG.open("ab")
    return subprocess.Popen(
        [
            _python_executable(),
            "-u",
            "main.py",
            "serve",
            "--port",
            str(PORT),
            "--page",
            "new_product",
            "--no-browser",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=out,
        stderr=err,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def wait_health(timeout_sec: int = 15) -> dict:
    deadline = time.time() + timeout_sec
    last_error = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok") and data.get("new_product"):
                return data
            last_error = str(data)
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
        time.sleep(0.4)
    raise RuntimeError(f"health check failed: {last_error}")


def run_smoke() -> int:
    return subprocess.call([_python_executable(), "tools\\frontend_smoke.py"], cwd=str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the Orbit Hive local web console safely.")
    parser.add_argument("--smoke", action="store_true", help="Run frontend smoke tests after startup.")
    args = parser.parse_args()

    stopped = stop_existing()
    if stopped:
        print("stopped old listeners:", ",".join(str(x) for x in stopped))
    proc = start_server()
    print("started pid:", proc.pid)
    try:
        health = wait_health()
    except Exception as e:
        print("startup failed:", e)
        if ERR_LOG.is_file():
            print("stderr tail:")
            print("\n".join(ERR_LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]))
        return 1
    print("health:", json.dumps(health, ensure_ascii=False))
    if args.smoke:
        return run_smoke()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
