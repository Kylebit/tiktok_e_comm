#!/usr/bin/env python3
"""Thin stage 05-07 orchestrator.

Policy lives here and in the Skill. Transport and readback facts are produced
by seven independent deterministic tools. A failure in one platform never
prevents another selected platform from running.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Mapping
from uuid import uuid4

from _classification import classify
from _common import (
    DEFAULT_BASE_URL,
    DEFAULT_REPO,
    emit,
    load_json,
    repo_path,
    safe_text,
    write_json,
)


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

REPORT_ROOT = Path("reports") / "product-publication"
SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


@dataclass(frozen=True)
class ReportLocation:
    path: Path
    run_id: str
    revision: int


def _resolved_repo(value: str | None) -> str:
    return str(repo_path(value or str(DEFAULT_REPO)))


def _validated_report_path(
    value: str | Path,
    *,
    repo: Path,
    offer_id: str,
    revision: int | None,
) -> ReportLocation:
    """Bind one immutable report to repo/offer/revision/run/report.json."""

    target = Path(value).expanduser().resolve()
    root = (Path(repo).expanduser().resolve() / REPORT_ROOT).resolve()
    try:
        parts = target.relative_to(root).parts
    except ValueError as error:
        raise ValueError("report path must stay under reports/product-publication") from error
    if len(parts) != 4 or parts[-1] != "report.json":
        raise ValueError(
            "report path must be reports/product-publication/<offer>/<revision>/<run>/report.json"
        )
    path_offer, path_revision, run_id, _ = parts
    if path_offer != str(offer_id):
        raise ValueError("report offer identity differs from --offer-id")
    if not path_revision.isdigit() or int(path_revision) < 1:
        raise ValueError("report revision must be a positive integer")
    parsed_revision = int(path_revision)
    if revision is not None and parsed_revision != revision:
        raise ValueError("report revision differs from the approved snapshot")
    if not SAFE_RUN_ID.fullmatch(run_id) or run_id in {".", ".."}:
        raise ValueError("report run id is invalid")
    return ReportLocation(path=target, run_id=run_id, revision=parsed_revision)


def _write_report_atomic(path: str | Path, payload: Mapping[str, Any]) -> None:
    """Atomically create an immutable report without replacing prior evidence."""

    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"publication report already exists: {target}")
    temporary = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(dict(payload), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # A same-filesystem hard link is atomic and refuses an existing target.
        os.link(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _snapshot_identity_summary(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    identity = snapshot.get("identity")
    identity = identity if isinstance(identity, Mapping) else {}
    revision = identity.get("product_revision")
    if type(revision) is not int:
        revision = identity.get("revision")
    result = {
        "schema_version": safe_text(snapshot.get("schema_version"), 100),
        "offer_id": safe_text(identity.get("offer_id"), 100),
        "revision": revision if type(revision) is int else None,
        "plan_id": safe_text(identity.get("plan_id"), 200),
        "snapshot_digest": safe_text(
            snapshot.get("digest") or snapshot.get("snapshot_digest"), 128
        ),
        "payload_digest": safe_text(identity.get("payload_digest"), 128),
        "targets_digest": safe_text(identity.get("targets_digest"), 128),
    }
    return {key: value for key, value in result.items() if value not in {None, ""}}


def _redacted_value(value: object, *, key: str = "") -> object:
    lowered = key.lower()
    denied_exact = {
        "snapshot",
        "request",
        "payload",
        "raw",
        "raw_response",
        "response_body",
        "images",
        "image_urls",
        "video_urls",
    }
    denied_fragments = (
        "snapshot",
        "raw_response",
        "raw_payload",
        "response_body",
        "confirmation_token",
        "token",
        "secret",
        "access_token",
        "refresh_token",
        "authorization",
        "credential",
        "client_secret",
        "partner_key",
        "api_key",
        "signature",
    )
    if (
        lowered not in {"snapshot_identity", "snapshot_digest"}
        and (
            lowered in denied_exact
            or any(fragment in lowered for fragment in denied_fragments)
        )
    ):
        return None
    if "url" in lowered:
        return None
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for child_key, child in value.items():
            clean_key = str(child_key)
            clean = _redacted_value(child, key=clean_key)
            if clean is not None:
                result[clean_key] = clean
        return result
    if isinstance(value, list):
        return [clean for child in value if (clean := _redacted_value(child)) is not None]
    if isinstance(value, str):
        text = safe_text(value)
        return re.sub(
            r"(?i)confirmation[_-]?token|access[_-]?token|refresh[_-]?token|"
            r"client[_-]?secret|partner[_-]?key|api[_-]?key|secret|token",
            "[redacted-field]",
            text,
        )
    if value is None or type(value) in {bool, int, float}:
        return value
    return safe_text(value)


def _redacted_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Project only identity and sanitized platform facts for disk/stdout."""

    safe: dict[str, Any] = {}
    for key in (
        "schema_version",
        "run_id",
        "generated_at",
        "executed",
        "ok",
        "summary",
        "error",
    ):
        if key in report:
            safe[key] = _redacted_value(report[key], key=key)
    snapshot = report.get("snapshot")
    if isinstance(snapshot, Mapping):
        safe["snapshot_identity"] = _snapshot_identity_summary(snapshot)
    elif isinstance(report.get("snapshot_identity"), Mapping):
        safe["snapshot_identity"] = _redacted_value(
            report["snapshot_identity"], key="snapshot_identity"
        )
    platforms = report.get("platforms")
    safe["platforms"] = (
        _redacted_value(platforms, key="platforms") if isinstance(platforms, list) else []
    )
    return safe


def _emit_report(report: Mapping[str, Any], location: ReportLocation) -> None:
    safe = _redacted_report(report)
    _write_report_atomic(location.path, safe)
    emit(safe)


def _snapshot_revision(snapshot: Mapping[str, Any]) -> int:
    identity = snapshot.get("identity")
    identity = identity if isinstance(identity, Mapping) else {}
    revision = identity.get("product_revision")
    if type(revision) is not int:
        revision = identity.get("revision")
    if type(revision) is not int or revision < 1:
        raise ValueError("approved snapshot revision is missing")
    return revision


def _prepare_evidence_directory(location: ReportLocation) -> Path:
    directory = location.path.parent
    if location.path.exists():
        raise FileExistsError(f"publication report already exists: {location.path}")
    if directory.exists():
        collisions = sorted(
            child.name
            for child in directory.iterdir()
            if child.name.endswith(("-dispatch.json", "-readback.json"))
        )
        if collisions:
            raise FileExistsError(
                "publication run evidence already exists: " + ", ".join(collisions)
            )
    directory.mkdir(parents=True, exist_ok=True)
    return directory


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
    try:
        process = subprocess.run(
            [str(executable), *arguments, "--output", str(output)],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        if output.is_file():
            fact = load_json(output)
            fact.setdefault("tool_warning", "child tool timed out after writing its fact")
            return fact
        fact = {
            "ok": False,
            "attempted": True,
            "accepted": False,
            "write_outcome": "UNKNOWN",
            "message": safe_text(error),
        }
        write_json(output, fact)
        return fact
    if output.is_file():
        return load_json(output)
    fact = {
        "ok": False,
        "message": safe_text(process.stderr or process.stdout or f"tool exited {process.returncode}"),
    }
    write_json(output, fact)
    return fact


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
    evidence_stem: str | None = None,
) -> dict[str, Any]:
    resolved_repo = _resolved_repo(getattr(args, "repo", None))
    executable = _tool_python(resolved_repo)
    stem = evidence_stem or platform
    dispatch_path = directory / f"{stem}-dispatch.json"
    readback_path = directory / f"{stem}-readback.json"
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
    if platform == "shopee":
        dispatch_arguments.extend(["--repo", resolved_repo])
    if retire_deleted_global_id and platform == "shopee":
        dispatch_arguments.extend(["--retire-deleted-global-id", retire_deleted_global_id])
    try:
        dispatch = _run_tool(
            dispatch_arguments, dispatch_path, executable=executable
        )
    except Exception as error:
        if not dispatch_path.is_file():
            raise
        dispatch = load_json(dispatch_path)
        dispatch.setdefault(
            "tool_warning", safe_text(error or "dispatch child failed after writing fact")
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
    readback_arguments.extend(["--repo", resolved_repo])
    try:
        readback = _run_tool(
            readback_arguments, readback_path, executable=executable
        )
    except Exception as error:
        if readback_path.is_file():
            readback = load_json(readback_path)
            readback.setdefault("tool_warning", safe_text(error))
        else:
            readback = {
                "verified": False,
                "complete": False,
                "status": "UNAVAILABLE",
                "message": safe_text(error),
                "retry_safe": False,
            }
            write_json(readback_path, readback)
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
        evidence_stem="shopee-recovery-1",
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
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    location: ReportLocation | None = None
    report: dict[str, Any] = {
        "schema_version": "approved-product-execution-report/v3",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "executed": False,
        "platforms": [],
    }
    try:
        args.repo = _resolved_repo(args.repo)
        location = _validated_report_path(
            args.report,
            repo=Path(args.repo),
            offer_id=args.offer_id,
            revision=None,
        )
        report["run_id"] = location.run_id
        with tempfile.TemporaryDirectory(prefix="publish-approved-product-") as temp:
            snapshot_path = Path(temp) / "snapshot.json"
            executable = _tool_python(args.repo)
            snapshot = _inspect(args, snapshot_path, executable=executable)
            report["snapshot"] = snapshot
            if snapshot.get("schema_version") not in {
                "approved-publication-snapshot/v3",
                "approved-publication-snapshot/v4",
            } or snapshot.get("ok") is False:
                raise RuntimeError(snapshot.get("error") or "approved snapshot inspection failed")
            location = _validated_report_path(
                args.report,
                repo=Path(args.repo),
                offer_id=args.offer_id,
                revision=_snapshot_revision(snapshot),
            )
            directory = _prepare_evidence_directory(location)
            if args.command == "inspect":
                report["ok"] = True
                _emit_report(report, location)
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
            _emit_report(report, location)
            return 0 if report["ok"] else 1
    except Exception as error:
        report["ok"] = False
        report["error"] = safe_text(error)
        safe = _redacted_report(report)
        if location is not None and not location.path.exists():
            try:
                _write_report_atomic(location.path, safe)
            except Exception as report_error:
                safe["report_error"] = safe_text(report_error)
        emit(safe)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
