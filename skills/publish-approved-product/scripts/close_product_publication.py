"""Portable, loopback-only client for the server-owned publication closure."""
from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

LIMIT = 2 * 1024 * 1024
PREFIX = "/api/product-workspace/publication-closure/"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("REDIRECT_REJECTED")


def _base_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        parsed, port = None, None
    if (parsed is None or parsed.scheme != "http" or not port
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment
            or value != f"http://{parsed.netloc}"
            or parsed.netloc not in {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}):
        raise ValueError("EXPLICIT_LOOPBACK_BASE_URL_REQUIRED")
    return value


def request(base, path, payload=None):
    base = _base_url(base)
    if not path.startswith("/api/"):
        raise ValueError("API_PATH_REQUIRED")
    body = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    if body is not None and len(body) > LIMIT:
        raise ValueError("REQUEST_TOO_LARGE")
    req = urllib.request.Request(base + path, data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="GET" if body is None else "POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    raw = response.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("RESPONSE_TOO_LARGE")
    result = json.loads(raw.decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("INVALID_JSON_RESPONSE")
    return response.code, result


def _output(value, code=2):
    print(json.dumps(value, ensure_ascii=True, sort_keys=True))
    return code


def _blocked(code, **extra):
    return _output({"ok": False, "status": "BLOCKED", "code": code, **extra})


def _runtime(base, expected_root):
    try:
        status, runtime = request(base, "/api/health")
    except (OSError, TimeoutError, http.client.HTTPException, ValueError, UnicodeError):
        return None, _blocked("RUNTIME_UNAVAILABLE")
    if status != 200 or runtime.get("service") != "orbit-hive-local-console":
        return None, _blocked("RUNTIME_NOT_IDENTIFIED")
    actual_root = runtime.get("root")
    if (not isinstance(actual_root, str) or not Path(actual_root).is_absolute()
            or Path(actual_root).resolve() != Path(expected_root).resolve()):
        return None, _blocked("RUNTIME_ROOT_MISMATCH")
    missing = runtime.get("missing_dependencies")
    if not isinstance(missing, list) or any(not isinstance(x, str) for x in missing):
        return None, _blocked("RUNTIME_NOT_IDENTIFIED")
    view = {"root": runtime["root"], "state": runtime.get("state"),
            "missing_dependencies": missing, "business_authority": "NOT_CHECKED"}
    if missing:
        return None, _output({"ok": False, "status": "DEPENDENCY_UNAVAILABLE", "runtime": view})
    # Health identity may be UNKNOWN in isolated test and offline runtimes.
    # This command does not grant business authority; the server still owns
    # exact-source validation and will block a closure without evidence.
    return view, None


def _read_object(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("INPUT_MUST_BE_OBJECT")
    return value


def _result(status, result):
    if status != 200 or result.get("ok") is not True:
        return _output({"ok": False, "status": "BLOCKED", "http_status": status,
            "code": result.get("code", "CLOSURE_REQUEST_FAILED"), "result": result})
    return _output({"ok": True, "status": result.get("status"), "result": result}, 0)


def _unknown(closure):
    return _output({"ok": False, "status": "RECORD_OUTCOME_UNKNOWN", "automatic_retry": False,
        "expected_closure_id": closure["closure_id"],
        "expected_closure_digest": closure["closure_digest"]})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url")
    parser.add_argument("--expected-root")
    actions = parser.add_subparsers(dest="action")
    actions.add_parser("doctor")
    prepare = actions.add_parser("prepare")
    prepare.add_argument("--input", required=True)
    record = actions.add_parser("record")
    record.add_argument("--prepared", required=True)
    latest = actions.add_parser("latest")
    latest.add_argument("--offer-id", required=True)
    latest.add_argument("--plan-id", required=True)
    args = parser.parse_args(argv)
    if not args.base_url or not args.expected_root or not args.action:
        return _output({"ok": False, "status": "CONFIG_REQUIRED",
                        "code": "EXPLICIT_RUNTIME_CONFIGURATION_REQUIRED"})
    try:
        base = _base_url(args.base_url)
    except ValueError as error:
        return _blocked(str(error))
    runtime, failure = _runtime(base, args.expected_root)
    if failure is not None:
        return failure
    if args.action == "doctor":
        return _output({"ok": True, "status": "RUNTIME_IDENTIFIED", "runtime": runtime}, 0)
    try:
        if args.action == "prepare":
            inputs = _read_object(args.input)
            status, result = request(base, PREFIX + "prepare", inputs)
            if status != 200 or result.get("ok") is not True:
                return _result(status, result)
            if (result.get("status") != "READY_TO_RECORD"
                    or not isinstance(result.get("closure"), dict)
                    or not isinstance(result.get("input_digest"), str)):
                return _blocked("CLOSURE_RESPONSE_INVALID")
            return _output({"ok": True, "status": "READY_TO_RECORD",
                            "request": inputs, "result": result}, 0)
        if args.action == "latest":
            query = urllib.parse.urlencode({"offer_id": args.offer_id, "plan_id": args.plan_id})
            return _result(*request(base, PREFIX + "latest?" + query))
        prepared = _read_object(args.prepared)
        inputs, preview = prepared.get("request"), prepared.get("result")
        if (not isinstance(inputs, dict) or not isinstance(preview, dict)
                or preview.get("status") != "READY_TO_RECORD"
                or not isinstance(preview.get("closure"), dict)
                or not isinstance(preview.get("input_digest"), str)):
            return _blocked("INVALID_PREPARED_CLOSURE")
        closure = preview["closure"]
        if not isinstance(closure.get("closure_id"), str) or not isinstance(closure.get("closure_digest"), str):
            return _blocked("INVALID_PREPARED_CLOSURE")
        if any(inputs.get(field) != closure.get(field) for field in
               ("offer_id", "plan_id", "recorded_by", "recorded_at")):
            return _blocked("PREPARED_IDENTITY_MISMATCH")
        # Never retry record after transport failure or malformed success.
        try:
            status, result = request(base, PREFIX + "record",
                                     {**inputs, "input_digest": preview["input_digest"]})
        except (OSError, TimeoutError, http.client.HTTPException, ValueError, UnicodeError):
            return _unknown(closure)
        if status != 200 or result.get("ok") is not True:
            return _result(status, result)
        if result.get("status") != "RECORDED" or result.get("closure") != closure:
            return _unknown(closure)
        return _result(status, result)
    except (OSError, ValueError, TypeError, UnicodeError, http.client.HTTPException) as error:
        return _blocked("CLOSURE_INPUT_OR_TRANSPORT_INVALID", detail=type(error).__name__)


if __name__ == "__main__":
    raise SystemExit(main())
