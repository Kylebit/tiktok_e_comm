from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PORTABLE_NODE_ROOT = ROOT / "tools" / "runtime" / "node-v22.23.1-win-x64"


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> int:
    print("$", " ".join(cmd))
    env = dict(os.environ)
    current = env.get("PYTHONPATH", "")
    root_text = str(ROOT)
    env["PYTHONPATH"] = root_text if not current else root_text + os.pathsep + current
    if PORTABLE_NODE_ROOT.exists():
        env["PATH"] = str(PORTABLE_NODE_ROOT) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(cmd, cwd=str(cwd or ROOT), env=env)
    if check and result.returncode != 0:
        raise SystemExit(result.returncode)
    return result.returncode


def python_smoke() -> int:
    return run([sys.executable, str(ROOT / "tools" / "frontend_smoke.py")], check=False)


def python_units() -> int:
    tests = [
        "tests/test_catalog_listings.py",
        "tests/test_miaoshou_client.py",
        "tests/test_new_product_workbench.py",
        "tests/test_shopee_orders.py",
    ]
    worst = 0
    for rel in tests:
        code = run([sys.executable, "-X", "utf8", str(ROOT / rel)], check=False)
        worst = max(worst, code)
    return worst


def playwright_smoke() -> int:
    portable_npm = PORTABLE_NODE_ROOT / "npm.cmd"
    npm = str(portable_npm) if portable_npm.exists() else shutil.which("npm")
    if not npm:
        print("! npm not found; skip Playwright smoke. Install Node.js first.")
        return 2
    return run([npm, "run", "pw:test:smoke"], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Orbit minimal regression checks.")
    parser.add_argument(
        "--mode",
        choices=["python", "playwright", "all"],
        default="all",
        help="python = smoke + unit, playwright = browser smoke, all = both",
    )
    args = parser.parse_args()

    results: list[tuple[str, int]] = []

    if args.mode in {"python", "all"}:
        results.append(("python-smoke", python_smoke()))
        results.append(("python-units", python_units()))

    if args.mode in {"playwright", "all"}:
        results.append(("playwright-smoke", playwright_smoke()))

    print("\nSummary")
    for name, code in results:
        status = "OK" if code == 0 else ("SKIP" if code == 2 else "FAIL")
        print(f"- {name}: {status} ({code})")

    hard_failures = [code for _, code in results if code not in (0, 2)]
    return 1 if hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
