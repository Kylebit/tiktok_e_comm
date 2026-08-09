#!/usr/bin/env python3
"""Thin stage 05-07 orchestrator.

Policy lives here and in the Skill. Transport and readback facts are produced
by seven independent deterministic tools. A failure in one platform never
prevents another selected platform from running.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
from uuid import uuid4

from _classification import classify
from _common import DEFAULT_BASE_URL, emit, load_json, safe_text


SCRIPTS = Path(__file__).resolve().parent
DISPATCHERS = {
    "tiktok": "dispatch_tiktok.py",
    "shopee": "dispatch_shopee.py",
    "ozon": "dispatch_ozon.py",
}
READERS = {
    "tiktok": "readback_tiktok.py",
    "shopee": "readback_shopee.py",
    "ozon": "readback_ozon.py",
}


def _tool_python(repo: str | None) -> Path:
    """Use the product repository runtime for deterministic tools."""

    root = Path(repo).expanduser().resolve() if repo else Path(
        r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm"
    )
    for candidate in (
        root / ".venv" / "Scripts" / "python.exe",
        root.parent / "Python312" / "python.exe",
    ):
        if candidate.is_file():
            return candidate
    if sys.version_info >= (3, 10):
        return Path(sys.executable).resolve()
    raise RuntimeError("Product Center Python 3.10+ runtime is unavailable")


def _run_tool(
    arguments: list[str], output: Path, *, executable: Path
) -> dict[str, Any]:
    process = subprocess.run(
        [str(executable), *arguments, "--output", str(output)],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=300,
        check=False,
    )
    if output.is_file():
        return load_json(output)
    return {
        "ok": False,
        "message": safe_text(process.stderr or process.stdout or f"tool exited {process.returncode}"),
    }


def _inspect(
    args: argparse.Namespace, output: Path, *, executable: Path
) -> dict[str, Any]:
    command = [
        str(SCRIPTS / "inspect_snapshot.py"),
        "--offer-id",
        args.offer_id,
        "--base-url",
        args.base_url,
        "--timeout-seconds",
        str(args.timeout_seconds),
    ]
    if args.dashboard_fixture:
        command.extend(["--dashboard-fixture", args.dashboard_fixture])
    return _run_tool(command, output, executable=executable)


def _platform_run(
    platform: str,
    *,
    snapshot_path: Path,
    directory: Path,
    args: argparse.Namespace,
    retire_deleted_global_id: str | None = None,
) -> dict[str, Any]:
    executable = _tool_python(args.repo)
    dispatch_path = directory / f"{platform}-dispatch.json"
    readback_path = directory / f"{platform}-readback.json"
    dispatch_arguments = [
            str(SCRIPTS / DISPATCHERS[platform]),
            "--snapshot",
            str(snapshot_path),
            "--base-url",
            args.base_url,
            "--timeout-seconds",
            str(args.timeout_seconds),
            "--execute",
        ]
    if args.repo:
        dispatch_arguments.extend(["--repo", args.repo]) if platform == "shopee" else None
    if retire_deleted_global_id and platform == "shopee":
        dispatch_arguments.extend(["--retire-deleted-global-id", retire_deleted_global_id])
    dispatch = _run_tool(
        dispatch_arguments, dispatch_path, executable=executable
    )
    readback_arguments = [
        str(SCRIPTS / READERS[platform]),
        "--snapshot",
        str(snapshot_path),
        "--dispatch",
        str(dispatch_path),
        "--base-url",
        args.base_url,
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--execute-readback",
    ]
    if args.repo:
        readback_arguments.extend(["--repo", args.repo])
    readback = _run_tool(
        readback_arguments, readback_path, executable=executable
    )
    result = classify(dispatch, readback)
    return {
        "platform": platform,
        "result": result,
        "dispatch": dispatch,
        "readback": readback,
    }


def _bounded_recovery(
    row: dict[str, Any],
    *,
    snapshot_path: Path,
    directory: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Apply only confirmed, single-attempt recovery patterns."""

    if row.get("platform") != "shopee":
        return row
    readback = row.get("readback")
    if not isinstance(readback, dict) or readback.get("status") != "DELETED":
        return row
    stale_id = str(readback.get("global_item_id") or "").strip()
    if not stale_id:
        return row
    recovered = _platform_run(
        "shopee",
        snapshot_path=snapshot_path,
        directory=directory,
        args=args,
        retire_deleted_global_id=stale_id,
    )
    recovered["recovery_history"] = [{
        "pattern": "official_global_status_deleted",
        "previous_dispatch": row.get("dispatch"),
        "previous_readback": row.get("readback"),
        "attempts": 1,
    }]
    return recovered


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish an approved offer through three independent platform tasks")
    parser.add_argument("command", choices=("inspect", "publish"))
    parser.add_argument("--offer-id", required=True)
    parser.add_argument("--platform", choices=("all", "tiktok", "shopee", "ozon"), default="all")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--repo")
    parser.add_argument("--dashboard-fixture")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--report")
    args = parser.parse_args()
    report: dict[str, Any] = {
        "schema_version": "approved-product-execution-report/v3",
        "run_id": f"skill-run:{uuid4().hex}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "executed": False,
        "platforms": [],
    }
    try:
        with tempfile.TemporaryDirectory(prefix="publish-approved-product-") as temp:
            directory = Path(temp)
            snapshot_path = directory / "snapshot.json"
            executable = _tool_python(args.repo)
            snapshot = _inspect(args, snapshot_path, executable=executable)
            report["snapshot"] = snapshot
            if snapshot.get("schema_version") != "approved-publication-snapshot/v3" or snapshot.get("ok") is False:
                raise RuntimeError(snapshot.get("error") or "approved snapshot inspection failed")
            if args.command == "inspect":
                report["ok"] = True
                emit(report, args.report)
                return 0
            if not args.execute:
                raise RuntimeError("publication requires --execute")
            requested = list(DISPATCHERS) if args.platform == "all" else [args.platform]
            selected = snapshot.get("platforms") or {}
            report["executed"] = True
            for platform in requested:
                plan = selected.get(platform) if isinstance(selected, dict) else None
                if not isinstance(plan, dict) or plan.get("selected") is not True:
                    continue
                if plan.get("blocking_reasons"):
                    report["platforms"].append({
                        "platform": platform,
                        "result": {"code": "FAILED", "label_zh": "发布失败", "retry_safe": False},
                        "dispatch": {"attempted": False, "accepted": False},
                        "readback": {"status": "NOT_ATTEMPTED"},
                        "blocking_reasons": list(plan["blocking_reasons"]),
                    })
                    continue
                # Each platform is a complete independent call. Exceptions are
                # captured per platform so the remaining platforms still run.
                try:
                    row = _platform_run(
                        platform,
                        snapshot_path=snapshot_path,
                        directory=directory,
                        args=args,
                    )
                    row = _bounded_recovery(
                        row,
                        snapshot_path=snapshot_path,
                        directory=directory,
                        args=args,
                    )
                except Exception as error:
                    row = {
                        "platform": platform,
                        "result": {"code": "FAILED", "label_zh": "发布失败", "retry_safe": False},
                        "dispatch": {"attempted": False, "accepted": False},
                        "readback": {"status": "UNAVAILABLE", "message": safe_text(error)},
                    }
                report["platforms"].append(row)
            codes = [row["result"]["code"] for row in report["platforms"]]
            report["ok"] = bool(codes) and all(code == "SUCCEEDED" for code in codes)
            report["summary"] = {
                "发布成功": sum(code == "SUCCEEDED" for code in codes),
                "平台处理中": sum(code == "PROCESSING" for code in codes),
                "部分成功": sum(code == "PARTIAL" for code in codes),
                "发布失败": sum(code == "FAILED" for code in codes),
            }
            emit(report, args.report)
            return 0 if report["ok"] else 1
    except Exception as error:
        report["ok"] = False
        report["error"] = safe_text(error)
        emit(report, args.report)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
