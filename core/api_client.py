"""TikTok Shop signing, one bounded request chain, and safe diagnostics."""
from __future__ import annotations

import hashlib
import hmac
from http.client import HTTPException
import json
import math
import random
import time
from email.utils import parsedate_to_datetime
import urllib.error
import urllib.parse
import urllib.request

from core.config import get as config_get, load_settings
from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import _certificate_error, _retryable
from core.http_retry import urlopen as urlopen_retry

BASE_URL = "https://open-api.tiktokglobalshop.com"
RATE_LIMIT_API_CODES = {36009002}
SANDBOX_RATE_LIMIT_CODE = 36009037
MAX_RETRIES = 10
MAX_INLINE_WAIT = 60.0
# Exact reviewed search contracts, not a method/suffix heuristic. Version and
# source/migration notes live in docs/security/S01_D_CLIENT_CONTRACT.md.
READ_ONLY_POST_PATHS = frozenset({
    "/product/202309/products/search",
    "/product/202309/global_products/search",
    "/order/202309/orders/search",
    "/promotion/202309/activities/search",
})


def _rate_limit_retries() -> int:
    value = (config_get("api", {}) or {}).get("rate_limit_retries", 5)
    if type(value) is not int or not 0 <= value <= MAX_RETRIES:
        raise ValueError("rate_limit_retries must be an integer from 0 to 10")
    return value


def _rate_limit_backoff() -> list[float]:
    raw = (config_get("api", {}) or {}).get("rate_limit_backoff_sec", [2, 4, 8, 16, 30])
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError("rate_limit_backoff_sec requires a nonempty sequence")
    if any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= MAX_INLINE_WAIT for x in raw):
        raise ValueError("backoff must contain finite seconds from 0 to 60")
    return [float(x) for x in raw]


def _provider_code(result) -> int | None:
    if not isinstance(result, dict):
        return None
    value = result.get("code")
    if type(value) is int and 0 <= value <= 9999999999:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 10:
        return int(value)
    return None


def _kind(status, code) -> str:
    if code == 105002 and status in {200, 401}:
        return "credentials_expired"
    if code == 105005:
        return "scope_denied"
    if code in {36009043, 36009044}:
        return "inactive_shop" if code == 36009043 else "inactive_seller"
    if code == SANDBOX_RATE_LIMIT_CODE:
        return "sandbox_hourly_limit"
    if status == 401:
        return "authentication_unclassified"
    if status == 429 or code in RATE_LIMIT_API_CODES:
        return "rate_limited"
    if 300 <= status < 400:
        return "redirect_blocked"
    return "provider_error" if code else "http_error"


def _retry_after(headers) -> float | None:
    value = headers.get("Retry-After") if headers else None
    if not isinstance(value, str) or not value or len(value) > 100:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            seconds = date.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def _request_ref(method, path) -> str:
    return hashlib.sha256((method.upper()+" "+path).encode()).hexdigest()[:16]


class APIRequestError(RuntimeError):
    """Only bounded diagnostic fields; provider text, URLs and causes stay private."""

    def __init__(self, kind, *, method, path, status=None, code=None, retry_after=None, retryable=False):
        self.kind = kind
        self.http_status = status
        self.provider_code = code
        self.retry_after_seconds = retry_after
        self.retryable = retryable
        self.request_ref = _request_ref(method, path)
        super().__init__(f"TikTok {kind}; HTTP={status}; code={code}; request={self.request_ref}")


class _APIResponse(dict):
    """Transport metadata stays outside the existing serialized response mapping."""
    pass


def _credentials():
    settings = load_settings()
    return settings["app_key"], settings["app_secret"]


def sign(path: str, params: dict, secret: str, body: str = "") -> str:
    keys = sorted(k for k in params if k not in ("sign", "access_token"))
    base = secret + path + "".join(k + str(params[k]) for k in keys) + body + secret
    return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()


def _do_request_once(method: str, path: str, access_token: str, query: dict | None,
                     body: dict | None, debug: bool, _retry_on_401: bool, *, call_guard=None) -> dict:
    """Exactly one HTTP attempt; legacy last argument never enables recursion.

    Direct promotion callers keep their existing signature. Auth recovery belongs
    to request(), never this primitive or the transport's hidden retry budget.
    """
    app_key, app_secret = _credentials()
    params = {"app_key": app_key, "timestamp": str(int(time.time()))}
    if query:
        params.update(query)
    body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
    params["sign"] = sign(path, params, app_secret, body_str)
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    data = body_str.encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper())
    req.add_header("x-tts-access-token", access_token)
    req.add_header("Content-Type", "application/json")
    if debug:
        print(f"  → {method.upper()} TikTok request={_request_ref(method, path)}")
    if call_guard is not None:
        call_guard()
    try:
        with urlopen_retry(req, timeout=45, context=SSL_CTX, attempts=1, allow_curl_fallback=False) as response:
            raw = response.read()
            status = getattr(response, "status", 200)
            headers = getattr(response, "headers", None)
    except urllib.error.HTTPError as error:
        try:
            raw = error.read()
            try:
                code = _provider_code(json.loads(raw))
            except (ValueError, UnicodeError):
                code = None
            failure = APIRequestError(_kind(error.code, code), method=method, path=path,
                status=error.code, code=code, retry_after=_retry_after(error.headers))
        except (OSError, HTTPException):
            failure = APIRequestError("error_body_unavailable", method=method, path=path, status=error.code)
        finally:
            try:
                error.close()
            except (OSError, HTTPException):
                # Cleanup cannot replace the safe classified primary diagnostic.
                pass
        raise failure from None
    except (urllib.error.URLError, OSError, HTTPException) as error:
        kind = "certificate_verification_failed" if _certificate_error(error) else "transport_outcome_unknown"
        raise APIRequestError(kind, method=method, path=path, retryable=_retryable(error)) from None
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        raise APIRequestError("invalid_response", method=method, path=path, status=status) from None
    code = _provider_code(result)
    if code is None:
        raise APIRequestError("invalid_response", method=method, path=path, status=status) from None
    if code != 0:
        # Never return arbitrary provider echo (including nested headers/body).
        kind = _kind(status, code)
        result = {"code": code, "message": kind, "error_kind": kind,
                  "http_status": status, "request_ref": _request_ref(method, path)}
        retry_after = _retry_after(headers)
        if retry_after is not None:
            result["retry_after_seconds"] = retry_after
    value = _APIResponse(result)
    value.retry_after_seconds = _retry_after(headers)
    return value


def request(method: str, path: str, access_token: str, query: dict | None = None,
            body: dict | None = None, debug: bool = False, _retry_on_401: bool = True,
            *, retry_read: bool | None = None, call_guard=None) -> dict:
    """One chain: bounded API attempts and at most one auth refresh HTTP.

    Bodyless GET/HEAD and the exact reviewed POST searches are reads. retry_read
    can disable these POST retries; True is rejected for any other endpoint.
    A mutation may only recover one explicit pre-business 105002 rejection;
    a 429 or idempotency key never grants permission to replay it. Unknown writes are returned
    to the existing durable caller for reconciliation without automatic replay.
    """
    if retry_read is not None and type(retry_read) is not bool:
        raise ValueError("retry_read must be boolean or None")
    if call_guard is not None and not callable(call_guard):
        raise ValueError("call_guard must be callable or None")
    known_post_read = method.upper() == "POST" and path in READ_ONLY_POST_PATHS
    if retry_read is True and not known_post_read:
        raise ValueError("retry_read is limited to exact reviewed read-only POST endpoints")
    safe_read = (method.upper() in {"GET", "HEAD"} and body is None) or (known_post_read and retry_read is not False)
    # A verified expired-credential rejection occurs before business processing.
    # The second mutation slot is usable only by that recovery branch, never by
    # transport/429 retry. Auth writes remain a separate single-attempt operation.
    # A scoped native caller owns reconciliation and lease checks. Even a GET
    # remains one wire attempt; its next read must return through that caller.
    budget = 1 if call_guard is not None else 1 + _rate_limit_retries() if safe_read else 2 if _retry_on_401 else 1
    delays = _rate_limit_backoff() if safe_read and call_guard is None else []
    refreshed = False
    for attempt in range(budget):
        failure = None
        try:
            if call_guard is None:
                result = _do_request_once(method, path, access_token, query, body, debug, False)
            else:
                result = _do_request_once(method, path, access_token, query, body, debug, False,
                                          call_guard=call_guard)
            kind = result.get("error_kind")
            wait = getattr(result, "retry_after_seconds", None)
        except APIRequestError as error:
            failure = error
            kind, wait = error.kind, error.retry_after_seconds
        remaining = attempt + 1 < budget
        if kind == "credentials_expired" and _retry_on_401 and not refreshed and remaining:
            from core import auth
            # Mark before invocation. Unknown refresh does not recursively retry.
            refreshed = True
            try:
                updated = auth.refresh_access_token(force=True)
                fresh_token = updated["access_token"]
                if not isinstance(fresh_token, str) or not fresh_token.strip():
                    raise ValueError("invalid access token")
            except Exception:
                raise APIRequestError("auth_recovery_failed", method=method, path=path) from None
            access_token = fresh_token
            continue
        can_retry = kind == "rate_limited" or (failure is not None and failure.retryable)
        if safe_read and remaining and can_retry and (wait is None or wait <= MAX_INLINE_WAIT):
            generated = min(delays[min(attempt, len(delays)-1)] + random.uniform(0, 0.5), MAX_INLINE_WAIT)
            time.sleep(max(generated, wait or 0))
            continue
        if failure is not None:
            raise failure from None
        return result
    raise APIRequestError("request_budget_exhausted", method=method, path=path)


def get(path, access_token, query=None, debug=False):
    return request("GET", path, access_token, query=query, debug=debug)


def post(path, access_token, query=None, body=None, debug=False, *, retry_read=None):
    return request("POST", path, access_token, query=query, body=body, debug=debug, retry_read=retry_read)


def put(path, access_token, query=None, body=None, debug=False):
    return request("PUT", path, access_token, query=query, body=body, debug=debug)


def paginate_get(path, access_token, query: dict, list_key: str) -> list:
    items = []
    page_token = ""
    while True:
        q = dict(query)
        if page_token:
            q["page_token"] = page_token
        result = get(path, access_token, q)
        if result.get("code") != 0:
            raise RuntimeError(result.get("message", str(result)))
        data = result.get("data") or {}
        items.extend(data.get(list_key, []))
        page_token = data.get("next_page_token") or ""
        if not page_token:
            break
        time.sleep(0.2)
    return items
