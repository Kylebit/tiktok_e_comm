"""TikTok Shop Open API 客户端（签名、GET/POST、分页）。"""

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from core.config import get as config_get, load_settings
from core.http_retry import DEFAULT_SSL_CTX as SSL_CTX
from core.http_retry import urlopen as urlopen_retry

BASE_URL = "https://open-api.tiktokglobalshop.com"
RATE_LIMIT_API_CODES = {36009037}
INVALID_TIMESTAMP_CODE = 36009004
_clock_skew_sec = int((config_get("api", {}) or {}).get("timestamp_skew_sec", 0) or 0)


def _api_timestamp(extra_skew: int = 0) -> str:
    return str(int(time.time()) - _clock_skew_sec - extra_skew)


def _is_invalid_timestamp_error(err: str) -> bool:
    return f'"code":{INVALID_TIMESTAMP_CODE}' in err.replace(" ", "")


def _rate_limit_retries() -> int:
    cfg = config_get("api", {}) or {}
    return int(cfg.get("rate_limit_retries", 5))


def _rate_limit_backoff() -> list[float]:
    cfg = config_get("api", {}) or {}
    raw = cfg.get("rate_limit_backoff_sec", [2, 4, 8, 16, 30])
    return [float(x) for x in raw]


def _is_rate_limited_http(code: int) -> bool:
    return code == 429


def _is_rate_limited_response(result: dict) -> bool:
    return int(result.get("code") or 0) in RATE_LIMIT_API_CODES


def _sleep_rate_limit(attempt: int) -> None:
    delays = _rate_limit_backoff()
    delay = delays[min(attempt, len(delays) - 1)]
    time.sleep(delay)


def _credentials():
    s = load_settings()
    return s["app_key"], s["app_secret"]


def sign(path: str, params: dict, secret: str, body: str = "") -> str:
    keys = sorted(k for k in params if k not in ("sign", "access_token"))
    base = secret + path + "".join(k + str(params[k]) for k in keys) + body + secret
    return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()


def _do_request_once(
    method: str,
    path: str,
    access_token: str,
    query: dict | None,
    body: dict | None,
    debug: bool,
    _retry_on_401: bool,
    *,
    extra_skew: int = 0,
) -> dict:
    app_key, app_secret = _credentials()
    params = {"app_key": app_key, "timestamp": _api_timestamp(extra_skew)}
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
        print(f"  → {method.upper()} {url[:120]}...")
    try:
        with urlopen_retry(req, timeout=45, context=SSL_CTX) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        if e.code == 401 and _retry_on_401:
            from core import auth

            refreshed = auth.refresh_access_token(force=True)
            return request(
                method,
                path,
                refreshed["access_token"],
                query=query,
                body=body,
                debug=debug,
                _retry_on_401=False,
            )
        if _is_rate_limited_http(e.code):
            raise
        if e.code == 400 and _is_invalid_timestamp_error(err):
            raise RuntimeError(f"HTTP {e.code}: {err[:400]}") from e
        raise RuntimeError(f"HTTP {e.code}: {err[:400]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络错误: {e.reason or e}") from e

    if _retry_on_401 and result.get("code") == 105002:
        from core import auth

        refreshed = auth.refresh_access_token(force=True)
        return request(
            method,
            path,
            refreshed["access_token"],
            query=query,
            body=body,
            debug=debug,
            _retry_on_401=False,
        )
    return result


def request(
    method: str,
    path: str,
    access_token: str,
    query: dict | None = None,
    body: dict | None = None,
    debug: bool = False,
    _retry_on_401: bool = True,
) -> dict:
    global _clock_skew_sec
    max_retries = _rate_limit_retries()
    ts_skew_steps = (0, 30, 45, 60)
    last_err: Exception | None = None

    for ts_skew in ts_skew_steps:
        for attempt in range(max_retries + 1):
            try:
                result = _do_request_once(
                    method,
                    path,
                    access_token,
                    query,
                    body,
                    debug,
                    _retry_on_401,
                    extra_skew=ts_skew,
                )
            except RuntimeError as e:
                err = str(e)
                if _is_invalid_timestamp_error(err) and ts_skew != ts_skew_steps[-1]:
                    last_err = e
                    break
                raise
            except urllib.error.HTTPError as e:
                if _is_rate_limited_http(e.code) and attempt < max_retries:
                    _sleep_rate_limit(attempt)
                    last_err = RuntimeError(f"HTTP {e.code}: rate limited")
                    continue
                err = e.read().decode("utf-8", errors="ignore") if e.fp else ""
                if _is_invalid_timestamp_error(err) and ts_skew != ts_skew_steps[-1]:
                    last_err = RuntimeError(f"HTTP {e.code}: {err[:400]}")
                    break
                raise RuntimeError(f"HTTP {e.code}: {err[:400]}") from e
            else:
                if _is_rate_limited_response(result):
                    if attempt < max_retries:
                        _sleep_rate_limit(attempt)
                        last_err = RuntimeError(result.get("message", "rate limited"))
                        continue
                    return result
                if ts_skew and _clock_skew_sec < ts_skew:
                    _clock_skew_sec = ts_skew
                return result
        else:
            continue
    else:
        if last_err:
            raise last_err
        raise RuntimeError("请求失败：时间戳无效且重试已用尽")


def get(path, access_token, query=None, debug=False):
    return request("GET", path, access_token, query=query, debug=debug)


def post(path, access_token, query=None, body=None, debug=False):
    return request("POST", path, access_token, query=query, body=body, debug=debug)


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
