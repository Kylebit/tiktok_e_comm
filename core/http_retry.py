"""Verified HTTP transport with bounded retries for safe reads only."""

from __future__ import annotations

import io
import math
import os
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_SSL_CTX = ssl.create_default_context()

_CURL_CODE_MARKER = b"\n__CURL_HTTP_CODE__:"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Each invocation spends one HTTP hop, including same-origin requests."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _urlopen_once(req, *, timeout, context):
    # Keep urllib's normal environment/system ProxyHandler and explicit TLS trust.
    # A private opener prevents process-global opener/redirect state from adding hops.
    opener = urllib.request.build_opener(
        _NoRedirect(), urllib.request.HTTPSHandler(context=context)
    )
    return opener.open(req, timeout=timeout)


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


def _certificate_error(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, BaseException) and reason is not exc:
        return _certificate_error(reason)
    message = str(reason).lower()
    return any(token in message for token in (
        "certificate_verify_failed", "certificate verify failed", "hostname mismatch",
        "certificate has expired", "unable to get local issuer certificate",
    ))


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError) or _certificate_error(exc):
        return False
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, ConnectionResetError):
        return True
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, BaseException):
            return _retryable(reason)
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
    if isinstance(exc, urllib.error.HTTPError) or _certificate_error(exc):
        return False
    if isinstance(exc, ConnectionResetError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ConnectionResetError):
            return True
        if isinstance(reason, ssl.SSLError):
            return True
        msg = str(reason or exc).lower()
        return "ssl" in msg or "eof" in msg
    if isinstance(exc, ssl.SSLError):
        return True
    msg = str(exc).lower()
    return "ssl" in msg and "eof" in msg


def _positive_number(value, name: str, *, allow_zero: bool = False) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero)):
        raise ValueError(f"{name} must be a finite {'nonnegative' if allow_zero else 'positive'} number")


def _proxy_for_request(req: urllib.request.Request) -> str:
    """Mirror urllib's environment/system proxy selection, including bypass."""
    parsed = urllib.parse.urlsplit(req.full_url)
    proxy = urllib.request.getproxies().get(parsed.scheme)
    return proxy if proxy and not urllib.request.proxy_bypass(parsed.netloc) else ""


def _curl_urlopen(req: urllib.request.Request, timeout: float, *, proxy: str | None = None) -> _CurlResponse:
    """One verified curl read, using urllib's selected proxy instead of curlrc."""
    _positive_number(timeout, "timeout")
    method = (req.get_method() or "GET").upper()
    if method not in {"GET", "HEAD"} or req.data is not None:
        raise ValueError("curl fallback is limited to GET/HEAD without a request body")
    url = req.full_url
    if urllib.parse.urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("curl fallback requires an HTTP(S) URL")
    cmd = [
        "curl",
        "-q",  # Must be first: curlrc cannot add retries, insecure TLS or routing.
        "-sS",
        "--proxy",
        _proxy_for_request(req) if proxy is None else proxy,
        "--noproxy",
        "",  # Bypass was already evaluated by urllib, not by curl's environment.
        "--proto",
        "=http,https",
        "-m",
        str(timeout),
        "-X",
        method,
        "-w",
        "\n__CURL_HTTP_CODE__:%{http_code}",
    ]
    for header, value in req.header_items():
        cmd.extend(["-H", f"{header}: {value}"])
    if method == "HEAD":
        cmd.extend(["--head", "--output", os.devnull])
    cmd.append(url)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout + 5,
        )
    except subprocess.TimeoutExpired:
        # TimeoutExpired contains argv (potentially authentication headers).
        raise urllib.error.URLError("curl transport timed out") from None
    except OSError:
        raise urllib.error.URLError("curl transport could not be started") from None

    if proc.returncode != 0:
        # stderr may echo URLs or credentials; retain only the transport code.
        raise urllib.error.URLError(f"curl transport failed (exit {proc.returncode})")

    raw = proc.stdout
    payload, marker, code_part = raw.rpartition(_CURL_CODE_MARKER)
    if not marker or not re.fullmatch(rb"[2-5][0-9]{2}", code_part):
        raise urllib.error.URLError("curl response missing a valid final HTTP status")
    status = int(code_part)

    if status >= 300:
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
    allow_curl_fallback: bool = True,
):
    """Send once for writes; attempts bounds all transports for safe reads.

    Redirects are never followed, even for same-origin GETs. Consumers needing
    asset relocation must resolve a validated final URL outside this request.
    Only bodyless GET/HEAD requests may retry. Curl can occupy the final retry
    slot, never an extra attempt. Explicit SSL contexts or custom CA environment
    settings stay on urllib because their trust policy cannot be copied to curl.
    An unknown write outcome raises the original error for caller reconciliation.
    """
    _positive_number(timeout, "timeout")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ValueError("attempts must be a positive integer")
    if not isinstance(backoff, (tuple, list)) or not backoff:
        raise ValueError("backoff must be a nonempty sequence")
    for delay in backoff:
        _positive_number(delay, "backoff delay", allow_zero=True)
    if not isinstance(allow_curl_fallback, bool):
        raise ValueError("allow_curl_fallback must be boolean")
    ctx = DEFAULT_SSL_CTX if context is None else context
    if not isinstance(ctx, ssl.SSLContext):
        raise TypeError("context must be an SSLContext")
    if ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname:
        raise ValueError("TLS context must require certificate and hostname verification")
    if isinstance(req, str):
        req = urllib.request.Request(req)
    safe_read = req.get_method().upper() in {"GET", "HEAD"} and req.data is None
    budget = attempts if safe_read else 1
    can_fallback = (
        safe_read and allow_curl_fallback and context is None
        and not req.has_proxy() and not getattr(req, "_tunnel_host", None)
        and not any(os.environ.get(key) for key in ("SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE"))
    )
    proxy = _proxy_for_request(req) if can_fallback else ""
    last_err: Exception | None = None
    for i in range(budget):
        if i and i == budget - 1 and can_fallback and _curl_fallback_error(last_err):
            return _curl_urlopen(req, timeout, proxy=proxy)
        try:
            return _urlopen_once(req, timeout=timeout, context=ctx)
        except urllib.error.HTTPError:
            raise
        except Exception as e:
            last_err = e
            if _certificate_error(e) or not _retryable(e) or i >= budget - 1:
                raise
            delay = backoff[min(i, len(backoff) - 1)]
            time.sleep(delay)
