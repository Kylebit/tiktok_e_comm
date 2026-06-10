"""带重试的 HTTP 请求（应对 SSL EOF、超时等瞬时网络错误）。"""

from __future__ import annotations

import ssl
import time
import urllib.error
import urllib.request

DEFAULT_SSL_CTX = ssl.create_default_context()
DEFAULT_SSL_CTX.check_hostname = False
DEFAULT_SSL_CTX.verify_mode = ssl.CERT_NONE


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


def urlopen(
    req: urllib.request.Request,
    *,
    timeout: float = 30,
    context: ssl.SSLContext | None = None,
    attempts: int = 4,
    backoff: tuple[float, ...] = (1.0, 2.0, 4.0),
):
    """urllib.request.urlopen 的包装：瞬时 SSL/网络错误自动重试。"""
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
                raise
            delay = backoff[min(i, len(backoff) - 1)]
            time.sleep(delay)
    if last_err:
        raise last_err
    raise RuntimeError("urlopen failed")
