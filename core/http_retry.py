"""带重试的 HTTP 请求（urllib + curl 回退，应对 SSL EOF / 代理问题）。"""

from __future__ import annotations

import io
import ssl
import subprocess
import time
import urllib.error
import urllib.request

DEFAULT_SSL_CTX = ssl.create_default_context()
DEFAULT_SSL_CTX.check_hostname = False
DEFAULT_SSL_CTX.verify_mode = ssl.CERT_NONE

_CURL_CODE_MARKER = b"\n__CURL_HTTP_CODE__:"


class _CurlResponse:
    """与 urllib 响应兼容的最小包装。"""

    def __init__(self, data: bytes, status: int):
        self._data = data
        self.status = status

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            return self._data
        return self._data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            return True
        msg = str(reason or exc).lower()
        return any(
            k in msg
            for k in (
                "ssl",
                "eof",
                "timed out",
                "timeout",
                "connection reset",
                "broken pipe",
                "connection refused",
                "network is unreachable",
            )
        )
    if isinstance(exc, OSError):
        msg = str(exc).lower()
        return "timed out" in msg or "connection reset" in msg
    return False


def _curl_fallback_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            return True
        msg = str(reason or exc).lower()
        return "ssl" in msg or "eof" in msg
    if isinstance(exc, ssl.SSLError):
        return True
    msg = str(exc).lower()
    return "ssl" in msg and "eof" in msg


def _curl_urlopen(req: urllib.request.Request, timeout: float) -> _CurlResponse:
    """curl --noproxy *：绕过本机代理导致的 Python SSL EOF。"""
    method = (req.get_method() or "GET").upper()
    url = req.full_url
    cmd = [
        "curl",
        "-sS",
        "--noproxy",
        "*",
        "-m",
        str(max(1, int(timeout))),
        "-X",
        method,
        "-w",
        "\n__CURL_HTTP_CODE__:%{http_code}",
    ]
    for header, value in req.header_items():
        cmd.extend(["-H", f"{header}: {value}"])
    body = req.data
    if body is not None:
        cmd.extend(["--data-binary", "@-"])
    cmd.append(url)

    try:
        proc = subprocess.run(
            cmd,
            input=body,
            capture_output=True,
            timeout=timeout + 30,
        )
    except subprocess.TimeoutExpired as e:
        raise urllib.error.URLError("timed out") from e

    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise urllib.error.URLError(err or f"curl exit {proc.returncode}")

    raw = proc.stdout
    if _CURL_CODE_MARKER in raw:
        payload, _, code_part = raw.rpartition(_CURL_CODE_MARKER)
        code_str = code_part.decode("utf-8", errors="replace").strip()
        try:
            status = int(code_str.split(":")[-1])
        except ValueError:
            status = 200
    else:
        payload = raw
        status = 200

    if status >= 400:
        raise urllib.error.HTTPError(
            url,
            status,
            "HTTP Error",
            hdrs=None,
            fp=io.BytesIO(payload),
        )
    return _CurlResponse(payload, status)


def urlopen(
    req: urllib.request.Request,
    *,
    timeout: float = 30,
    context: ssl.SSLContext | None = None,
    attempts: int = 4,
    backoff: tuple[float, ...] = (1.0, 2.0, 4.0),
):
    """urllib 优先；SSL/EOF 失败时自动改用 curl（与 ozon/webapp 一致）。"""
    ctx = context or DEFAULT_SSL_CTX
    last_err: BaseException | None = None
    for i in range(attempts):
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except urllib.error.HTTPError:
            raise
        except Exception as e:
            last_err = e
            if not _retryable(e) or i >= attempts - 1:
                break
            delay = backoff[min(i, len(backoff) - 1)]
            time.sleep(delay)

    if last_err and _curl_fallback_error(last_err):
        return _curl_urlopen(req, timeout)
    if last_err:
        raise last_err
    raise RuntimeError("urlopen failed")
