"""Bounded public GETs for four named resource consumers, never provider APIs.

Keep core.http_retry's private single-hop opener, verified TLS and default proxy.
No curl fallback: that response cannot enforce streaming limits or MIME checks.
"""
from __future__ import annotations

import http.client
import io
import ipaddress
import os
from pathlib import Path
import re
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import warnings

from PIL import Image, ImageFile

from core import http_retry


class ResourceDownloadError(ValueError):
    """Safe to display: never contains source URLs, signatures or remote text."""


# This is a consumer contract, not a wildcard provider retry policy.
_SOURCES = {
    'sourcing_image': ('image', 10_000_000),
    'sourcing_html': ('html', 5_000_000),
    'catalog_image': ('image', 5_000_000),
    'shopee_image': ('image', 10_000_000),
}
MAX_ATTEMPTS = 4
MAX_REDIRECTS = 3
MAX_IMAGE_PIXELS = 40_000_000  # Total across all frames, before decoding them.
_IMAGE_TYPES = {'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/webp': 'WEBP', 'image/gif': 'GIF'}
_SIGNATURE_KEYS = {'sign', 'signature', 'x-signature', 'auth_key', 'x-amz-signature', 'x-amz-credential', 'x-amz-security-token', 'ossaccesskeyid'}
_CREDENTIAL_KEY = re.compile(r'(?:token|secret|password|passwd|authorization|credential|api[_-]?key|access[_-]?key)', re.I)


def _origin(url):
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


def _validate_url(url: str, *, kind: str, signed_origin, previous: str | None) -> str:
    if not isinstance(url, str) or not url or len(url) > 16_384 or re.search(r'[\s\\\x00-\x1f\x7f]', url):
        raise ResourceDownloadError('invalid resource URL')
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
        host = parsed.hostname
        if parsed.scheme not in {'http', 'https'} or not host or parsed.username is not None or parsed.password is not None:
            raise ValueError
        if '%' in host or port not in {None, 80 if parsed.scheme == 'http' else 443}:
            raise ValueError
        if previous and urllib.parse.urlsplit(previous).scheme == 'https' and parsed.scheme != 'https':
            raise ValueError
        origin = _origin(url)
        for key, _ in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=100):
            key = key.lower()
            if key in _SIGNATURE_KEYS:
                # The three image callers supply their upstream resource URL.
                # Its signature is valid only at that exact HTTPS origin. A
                # redirect cannot carry it to a new host or upgrade HTML auth.
                if kind != 'image' or parsed.scheme != 'https' or origin != signed_origin:
                    raise ValueError
            elif _CREDENTIAL_KEY.search(key):
                raise ValueError
        host = host.rstrip('.').lower()
        if '.' not in host and ':' not in host or host.endswith(('.localhost', '.local', '.internal')) or host == 'localhost':
            raise ValueError
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            addresses = [ipaddress.ip_address(row[4][0]) for row in socket.getaddrinfo(host, port or origin[2], type=socket.SOCK_STREAM)]
        if not addresses or any(not address.is_global or (getattr(address, 'ipv4_mapped', None) and not address.ipv4_mapped.is_global) for address in addresses):
            raise ValueError
    except (ValueError, OSError):
        raise ResourceDownloadError('resource URL target or query is not permitted') from None
    return urllib.parse.urldefrag(url)[0]


def _headers(req: urllib.request.Request) -> dict[str, str]:
    if req.get_method() != 'GET' or req.data is not None or req.has_proxy() or getattr(req, '_tunnel_host', None):
        raise ResourceDownloadError('resource requests require a bodyless public GET')
    headers = {}
    for name, value in req.header_items():
        lower = name.lower()
        if lower not in {'user-agent', 'accept', 'referer', 'accept-encoding'} or re.search(r'[\r\n\x00]', value):
            raise ResourceDownloadError('resource authentication or custom routing headers are not permitted')
        if lower == 'referer' and value != 'https://detail.1688.com/':
            raise ResourceDownloadError('resource referer is not permitted')
        if lower == 'accept-encoding' and value.lower() != 'identity':
            raise ResourceDownloadError('encoded resources are not permitted')
        headers[name] = value
    headers['Accept-Encoding'] = 'identity'
    return headers


def _read_body(response, *, kind: str, remaining: list[int]) -> tuple[bytes, str]:
    if response.status != 200:
        raise ResourceDownloadError('resource HTTP status is not 200')
    mime = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
    if mime not in (_IMAGE_TYPES if kind == 'image' else {'text/html', 'application/xhtml+xml'}):
        raise ResourceDownloadError('resource content type is not permitted')
    if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
        raise ResourceDownloadError('encoded resources are not permitted')
    lengths = response.headers.get_all('Content-Length', [])
    length = None
    if lengths:
        if len(lengths) != 1 or not re.fullmatch(r'[0-9]+', lengths[0]) or len(lengths[0]) > 12:
            raise ResourceDownloadError('invalid resource content length')
        length = int(lengths[0])
        if length > remaining[0]:
            raise ResourceDownloadError('resource exceeds total byte budget')
    chunks = []
    received = 0
    while True:
        try:
            chunk = response.read(min(65_536, remaining[0] + 1))
        except http.client.IncompleteRead as error:
            remaining[0] -= len(error.partial)
            raise
        remaining[0] -= len(chunk)
        if remaining[0] < 0:
            raise ResourceDownloadError('resource exceeds total byte budget')
        if not chunk:
            break
        chunks.append(chunk)
        received += len(chunk)
    if length is not None and received != length:
        raise http.client.IncompleteRead(b'', length - received)
    if not received:
        raise ResourceDownloadError('resource body is empty')
    return b''.join(chunks), mime


def _validate_content(data: bytes, mime: str, kind: str) -> None:
    if kind == 'html':
        text = data.decode('utf-8', errors='replace')
        if '\x00' in text or not re.search(r'<(?:!doctype\s+html|html\b|body\b|div\b|p\b|img\b|script\b|table\b)', text, re.I):
            raise ResourceDownloadError('resource is not HTML content')
        return
    try:
        # Fail closed if another caller opted the whole process into partial
        # Pillow decodes. Do not mutate this process-wide switch here.
        if ImageFile.LOAD_TRUNCATED_IMAGES:
            raise ValueError
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as picture:
                if picture.format != _IMAGE_TYPES[mime] or picture.width * picture.height > MAX_IMAGE_PIXELS:
                    raise ValueError
                picture.verify()
            with Image.open(io.BytesIO(data)) as picture:
                frames = getattr(picture, 'n_frames', 1)
                if frames > 100 or picture.width * picture.height * frames > MAX_IMAGE_PIXELS:
                    raise ValueError
                for frame in range(frames):
                    picture.seek(frame)
                    picture.load()
    except Exception:
        raise ResourceDownloadError('resource image cannot be fully decoded') from None


def read_resource(req: urllib.request.Request, *, source: str, timeout: float) -> bytes:
    """Return validated bytes; at most four single-hop GETs and three redirects.

    A shared byte budget includes partial bodies from failed attempts. Only
    transient transport/read failures retry; HTTP errors and bad content fail.
    Signed queries are image-source-origin scoped, never copied cross-origin.
    """
    if source not in _SOURCES or not isinstance(req, urllib.request.Request):
        raise ResourceDownloadError('unknown resource consumer or request')
    http_retry._positive_number(timeout, 'timeout')
    kind, max_bytes = _SOURCES[source]
    headers = _headers(req)
    try:
        signed_origin = _origin(req.full_url)
    except ValueError:
        raise ResourceDownloadError('invalid resource URL') from None
    current = req.full_url
    previous = None
    visited = set()
    redirects = 0
    remaining = [max_bytes]
    for attempt in range(MAX_ATTEMPTS):
        current = _validate_url(current, kind=kind, signed_origin=signed_origin, previous=previous)
        visited.add(current)
        # Always rebuild: urllib's proxy handler mutates a Request during open.
        request = urllib.request.Request(current, headers=headers, method='GET')
        try:
            with http_retry.urlopen(request, timeout=timeout, context=http_retry.DEFAULT_SSL_CTX, attempts=1, allow_curl_fallback=False) as response:
                data, mime = _read_body(response, kind=kind, remaining=remaining)
            _validate_content(data, mime, kind)
            return data
        except urllib.error.HTTPError as error:
            try:
                if error.code not in {301, 302, 303, 307, 308}:
                    raise ResourceDownloadError('resource HTTP request failed') from None
                targets = error.headers.get_all('Location', []) if error.headers else []
                if len(targets) != 1 or not targets[0] or re.search(r'[\s\\\x00-\x1f\x7f]', targets[0]):
                    raise ResourceDownloadError('invalid resource redirect') from None
                target = urllib.parse.urljoin(current, targets[0])
                target = _validate_url(target, kind=kind, signed_origin=signed_origin, previous=current)
                redirects += 1
                if target in visited or redirects > MAX_REDIRECTS:
                    raise ResourceDownloadError('resource redirect loop or budget exhausted') from None
                previous, current = current, target
            except Exception:
                # Closing an HTTPError response must not replace the validated
                # redirect/status failure (or expose arbitrary transport text).
                try:
                    error.close()
                except Exception:
                    pass
                raise
            try:
                error.close()
            except Exception:
                raise ResourceDownloadError('resource response close failed') from None
        except Exception as error:
            if isinstance(error, ResourceDownloadError):
                raise
            retryable = isinstance(error, http.client.IncompleteRead) or http_retry._retryable(error)
            if not retryable or http_retry._certificate_error(error) or remaining[0] <= 0:
                raise ResourceDownloadError('resource transport failed') from None
            if attempt + 1 < MAX_ATTEMPTS:
                http_retry.time.sleep(0.25)
    raise ResourceDownloadError('resource total attempt budget exhausted')


def save_resource_image(req: urllib.request.Request, dest: Path, *, source: str, timeout: float) -> Path:
    """Replace only after full image validation; a failed download preserves dest."""
    if source not in _SOURCES or _SOURCES[source][0] != 'image':
        raise ResourceDownloadError('image consumer required')
    data = read_resource(req, source=source, timeout=timeout)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=dest.parent, prefix='.' + dest.name + '.', suffix='.part', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, dest)
    except Exception as error:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                if hasattr(error, 'add_note'):
                    error.add_note('Resource temporary-file cleanup failed; the owned .part file may remain.')
        raise
    # replace consumed the temporary path. Do not perform a second filesystem
    # operation that could misreport this committed replacement as a failure.
    return dest
