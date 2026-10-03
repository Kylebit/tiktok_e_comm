"""Run a pinned web-only Orbit deployment with a durable startup/error log.

This entrypoint is suitable for a per-user Windows Task Scheduler action. It
does not enable the operations worker or change deployment configuration.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    args = parser.parse_args()
    deployment = json.loads(args.deployment.read_text(encoding="utf-8"))
    if deployment.get("execution_mode") != "web-only":
        raise SystemExit("scheduled web entry requires execution_mode=web-only")
    if Path(deployment.get("code_root", "")).resolve() != ROOT.resolve():
        raise SystemExit("deployment code_root differs from this entrypoint")
    from domains.supply_chain_operations.captured_serving import deployment_capture
    try:
        deployment_capture(deployment)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if "shared_platform.workbench_store" in sys.modules:
        raise SystemExit("application store imported before runtime pin")
    from scripts.stable_runtime_bootstrap import prebind_workbench_store

    try:
        prebind_workbench_store(deployment)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open("a", encoding="utf-8", buffering=1) as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            print(f"[{datetime.now(timezone.utc).isoformat()}] starting {ROOT}")
            try:
                from shared_platform.operations_launch import serve

                serve(deployment, ROOT)
            except BaseException:
                traceback.print_exc()
                raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
