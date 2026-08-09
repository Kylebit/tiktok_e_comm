from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping


DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_REPO = Path(r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_text(value: object, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub(r'''https?://[^\s"'<>]+''', "[url]", text)
    text = re.sub(
        r'''(?i)((?:access[_-]?token|refresh[_-]?token|partner[_-]?key|api[_-]?key|client[_-]?secret|secret|signature|authorization|token)\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,}\]]+)''',
        r"\1[redacted]",
        text,
    )
    return text[:limit]


def json_request(
    url: str,
    *,
    payload: Mapping[str, Any] | None = None,
    timeout_seconds: float = 120,
) -> tuple[int, dict[str, Any]]:
    data = None
    method = "GET"
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        method = "POST"
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else {}
            return response.status, parsed if isinstance(parsed, dict) else {"response": parsed}
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"message": raw[:500]}
        return error.code, parsed if isinstance(parsed, dict) else {"response": parsed}
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return 0, {"transport_error": True, "message": safe_text(error)}


def dashboard(base_url: str, offer_id: str, timeout_seconds: float) -> dict[str, Any]:
    query = urllib.parse.urlencode({"offer_id": str(offer_id)})
    status, payload = json_request(
        f"{base_url.rstrip('/')}/api/product-workspace/dashboard?{query}",
        timeout_seconds=timeout_seconds,
    )
    if status != 200 or payload.get("ok") is False:
        reason = safe_text(payload.get("message") or payload.get("error"))
        raise RuntimeError(f"dashboard read failed with HTTP {status}: {reason}".rstrip())
    return payload


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON document must be an object")
    return payload


def write_json(path: str | Path | None, payload: Mapping[str, Any]) -> None:
    if path is None:
        return
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def emit(payload: Mapping[str, Any], output: str | None = None) -> None:
    import sys

    write_json(output, payload)
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    print(json.dumps(dict(payload), ensure_ascii=False, indent=2))


def repo_path(value: str | None = None) -> Path:
    candidate = Path(value).expanduser().resolve() if value else DEFAULT_REPO
    if not (candidate / "modules" / "products" / "server.py").is_file():
        raise RuntimeError(f"Product Center repository was not found at {candidate}")
    return candidate


def add_repo_to_path(value: str | None = None) -> Path:
    import sys

    candidate = repo_path(value)
    clean = str(candidate)
    if clean not in sys.path:
        sys.path.insert(0, clean)
    return candidate


def approved_request(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    request = snapshot.get("request")
    if not isinstance(request, dict):
        raise ValueError("approved snapshot request is missing")
    return dict(request)


def platform_selected(snapshot: Mapping[str, Any], platform: str) -> bool:
    platforms = snapshot.get("platforms")
    row = platforms.get(platform) if isinstance(platforms, dict) else None
    return isinstance(row, dict) and row.get("selected") is True


def dispatch_fact(
    *,
    platform: str,
    endpoint: str,
    snapshot: Mapping[str, Any],
    base_url: str,
    timeout_seconds: float,
    execute: bool,
) -> dict[str, Any]:
    if not platform_selected(snapshot, platform):
        return {
            "schema_version": "platform-dispatch-fact/v1",
            "platform": platform,
            "attempted": False,
            "accepted": False,
            "write_outcome": "NOT_ATTEMPTED",
            "reason": "platform is not selected in the approved snapshot",
        }
    if not execute:
        raise RuntimeError("dispatch requires --execute")
    status, response = json_request(
        f"{base_url.rstrip('/')}{endpoint}",
        payload=approved_request(snapshot),
        timeout_seconds=timeout_seconds,
    )
    accepted = response.get("success") is True or response.get("ok") is True
    unknown = status == 0
    return {
        "schema_version": "platform-dispatch-fact/v1",
        "platform": platform,
        "attempted": True,
        "http_status": status,
        "accepted": accepted,
        "write_outcome": "UNKNOWN" if unknown else ("ACCEPTED" if accepted else "REJECTED"),
        "external_write_count": response.get("external_write_count")
        if type(response.get("external_write_count")) is int
        else None,
        "provider_task_id": response.get("task_id"),
        "platform_item_id": response.get("global_item_id") or response.get("product_id"),
        "message": safe_text(response.get("message") or response.get("error")),
        "safe_response": safe_response(response),
        "observed_at": utc_now(),
    }


def safe_response(payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "ok",
        "success",
        "platform",
        "target_count",
        "successful_target_count",
        "accepted_target_count",
        "rejected_target_count",
        "unknown_target_count",
        "not_attempted_target_count",
        "global_item_id",
        "product_id",
        "task_id",
        "already_published",
        "external_write_count",
        "retryable",
        "flow",
    }
    result = {key: payload.get(key) for key in allowed if key in payload}
    rows = payload.get("targets")
    if isinstance(rows, list):
        result["targets"] = [
            {
                "target_label": safe_text(row.get("target_label"), 100),
                "outcome": safe_text(row.get("outcome"), 40),
                "provider_code": safe_text(row.get("provider_code"), 80),
                "provider_reason": safe_text(row.get("provider_reason"), 240),
            }
            for row in rows
            if isinstance(row, dict)
        ]
    return result


def positive(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def expected_model_skus(snapshot: Mapping[str, Any]) -> list[str]:
    rows = snapshot.get("skus")
    return [
        str(row.get("seller_sku") or "").strip()
        for row in rows or []
        if isinstance(row, dict) and str(row.get("seller_sku") or "").strip()
    ]
