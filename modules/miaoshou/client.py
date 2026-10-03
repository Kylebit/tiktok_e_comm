"""Miaoshou API clients for both signed open endpoints and web endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
import gzip
import urllib.error
import urllib.parse
import urllib.request
import base64
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from core.config import ROOT

OPEN_BASE_URL = "https://openapi-erp.91miaoshou.com"
COMMON_DETAIL_OBSERVATION_PATH = (
    "/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail"
)
COMMON_OBSERVATION_MAX_BYTES = 4 * 1024 * 1024
WEB_BASE_URL = "https://erp.91miaoshou.com"
MAIN_REPO = Path(os.environ.get("TIKTOK_ECOMM_HOME") or r"C:\Users\Windows11\Desktop\Agent_PR\tiktok_e_comm")
CONFIG_CANDIDATES = (
    ROOT / "config" / "miaoshou.local.json",
    MAIN_REPO / "config" / "miaoshou.local.json",
)
OPEN_REQUEST_INTERVAL_SECONDS = 1.1
_open_request_lock = threading.Lock()
_last_open_request_at = 0.0


class MiaoshouBusinessRejectedError(RuntimeError):
    """The API returned a parsed business rejection, so transport is not unknown."""

    def __init__(
        self,
        message: str,
        *,
        code: object = None,
        field_path: object = None,
    ) -> None:
        reason = sanitize_provider_reason(message)
        super().__init__(reason)
        self.code = sanitize_provider_code(code)
        self.field_path = sanitize_provider_field_path(field_path)
        self.business_rejected = True


def sanitize_provider_code(value: object) -> str:
    code = str(value or "business_rejected").strip()
    if not code or not code.isascii() or len(code) > 80 or any(
        not (character.isalnum() or character in {"_", "-", ".", ":"})
        for character in code
    ):
        return "business_rejected"
    return code


def sanitize_provider_field_path(value: object) -> str:
    field_path = str(value or "").strip()
    if (
        not field_path
        or not field_path.isascii()
        or len(field_path) > 160
        or any(
            not (character.isalnum() or character in {"_", "-", ".", "[", "]"})
            for character in field_path
        )
    ):
        return ""
    return field_path


def sanitize_provider_reason(value: object) -> str:
    reason = " ".join(str(value or "Miaoshou request rejected").split())
    if not reason:
        reason = "Miaoshou request rejected"
    reason = re.sub(r"https?://\S+", "[redacted-url]", reason, flags=re.IGNORECASE)
    reason = re.sub(
        r"(?i)\bauthorization\b\s*[:=]?\s*(?:bearer\s+)?\S+",
        "authorization=[redacted]",
        reason,
    )
    reason = re.sub(
        r"(?i)\bbearer\b\s+\S+",
        "bearer=[redacted]",
        reason,
    )
    reason = re.sub(
        r"(?i)\b(cookie|password|token|secret|api[_-]?key|app[_-]?key|client[_-]?secret)\b"
        r"\s*[:=]?\s*\S+",
        r"\1=[redacted]",
        reason,
    )
    return reason[:240]


def _business_rejection_details(result: Mapping[str, object]) -> tuple[str, str, str]:
    sources: list[Mapping[str, object]] = [result]
    data = result.get("data")
    if isinstance(data, Mapping):
        sources.append(data)
    for key in ("errors", "errorList", "error_list"):
        rows = result.get(key)
        if isinstance(rows, list) and rows and isinstance(rows[0], Mapping):
            sources.append(rows[0])
    code = next(
        (source.get(key) for source in sources for key in ("code", "errorCode", "error_code") if source.get(key) is not None),
        None,
    )
    field_path = next(
        (source.get(key) for source in sources for key in ("field_path", "fieldPath", "field", "path") if source.get(key)),
        None,
    )
    reason = next(
        (source.get(key) for source in sources for key in ("reason", "message", "msg", "errorMessage", "error_message") if source.get(key)),
        code or "Miaoshou request failed",
    )
    return (
        sanitize_provider_code(code),
        sanitize_provider_field_path(field_path),
        sanitize_provider_reason(reason),
    )


class MiaoshouWebAuthUnavailableError(RuntimeError):
    """The authenticated Miaoshou Web session is not configured."""


def ensure_web_batch_price_auth_available(
    cfg: dict[str, Any] | None = None,
) -> None:
    """Fail before writes unless a secure Web Cookie is available."""

    try:
        config = cfg or _load_config()
    except (FileNotFoundError, KeyError, TypeError, ValueError) as error:
        raise MiaoshouWebAuthUnavailableError(
            "Miaoshou web auth is unavailable"
        ) from error
    cookie = str(
        config.get("web_cookie")
        or config.get("erp_cookie")
        or os.environ.get("MIAOSHOU_WEB_COOKIE")
        or ""
    ).strip()
    configured_headers = config.get("web_headers")
    header_cookie = ""
    if isinstance(configured_headers, dict):
        header_cookie = next(
            (
                str(value).strip()
                for key, value in configured_headers.items()
                if str(key).strip().casefold() == "cookie" and value is not None
            ),
            "",
        )
    if not cookie and not header_cookie:
        raise MiaoshouWebAuthUnavailableError(
            "Miaoshou web auth is unavailable"
        )


class MiaoshouLocalConfigMissing(FileNotFoundError):
    """No local credentials file exists; raised before transport setup."""


def _load_config() -> dict[str, Any]:
    for path in CONFIG_CANDIDATES:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise MiaoshouLocalConfigMissing("Miaoshou local config was not found in the workspace or main project.")


def load_config(path: Path | None = None) -> dict[str, Any]:
    if path is not None:
        return json.loads(path.read_text(encoding="utf-8"))
    return _load_config()


def _open_sign(secret: str, path: str, timestamp: int, key: str, body_json: str) -> str:
    content = f"{secret}{path}{timestamp}{key}{body_json}{secret}"
    return hmac.new(secret.encode(), content.encode(), hashlib.sha256).hexdigest()


def _wait_for_open_slot() -> None:
    """Space signed Open API requests below the account's one-second limit."""

    global _last_open_request_at
    with _open_request_lock:
        now = time.monotonic()
        remaining = OPEN_REQUEST_INTERVAL_SECONDS - (now - _last_open_request_at)
        if _last_open_request_at and remaining > 0:
            time.sleep(remaining)
            now = time.monotonic()
        _last_open_request_at = now


def generate_sign(
    app_secret: str,
    path: str,
    timestamp: int,
    app_key: str,
    body_json: str = "",
) -> str:
    return _open_sign(app_secret, path, timestamp, app_key, body_json)


def _decode_json_response(response: urllib.request.addinfourl) -> dict[str, Any]:
    raw_bytes = response.read()
    encoding = str(response.headers.get("Content-Encoding") or "").lower()
    if encoding == "gzip" or raw_bytes[:2] == b"\x1f\x8b":
        try:
            raw_bytes = gzip.decompress(raw_bytes)
        except OSError:
            pass
    raw = raw_bytes.decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Miaoshou returned non-JSON data: {raw[:300]}") from exc


def post_open(
    path: str,
    body: dict[str, Any] | None = None,
    *,
    app_key: str | None = None,
    app_secret: str | None = None,
    base_url: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from shared_platform.native_common_edit_boundary import require_edit_dispatch
    require_edit_dispatch(path, body or {})
    config = cfg or _load_config()
    key = str(app_key or config["app_id"])
    secret = str(app_secret or config["app_secret"])
    root = str(base_url or config.get("base_url") or OPEN_BASE_URL).rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    payload = body or {}
    body_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    _wait_for_open_slot()
    timestamp = int(time.time())
    request = urllib.request.Request(
        root + path,
        data=body_json.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-app-key": key,
            "x-timestamp": str(timestamp),
            "x-sign": _open_sign(secret, path, timestamp, key, body_json),
        },
    )
    try:
        from shared_platform.native_common_edit_boundary import native_edit_response_capture
        open_request = (urllib.request.build_opener(_RefuseCommonDetailRedirect()).open
                        if native_edit_response_capture(path) is not None else urllib.request.urlopen)
        with open_request(request, timeout=30) as response:
            result = (_capture_native_edit_response(request, response, key, secret)
                      if native_edit_response_capture(path) is not None else
                      _decode_json_response(response))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Miaoshou HTTP {exc.code}: {raw[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Miaoshou network error: {exc}") from exc
    if str(result.get("result") or "").lower() != "success":
        code, field_path, reason = _business_rejection_details(result)
        raise MiaoshouBusinessRejectedError(
            reason,
            code=code,
            field_path=field_path,
        )
    return result


def _capture_native_edit_response(request, response, key, secret):
    """Retain original accepted EDIT bytes before the caller can attempt READ."""
    from shared_platform.native_common_edit_boundary import EDIT_PATH, retain_native_edit_response
    if response.geturl() != OPEN_BASE_URL + EDIT_PATH or response.status != 200:
        raise ValueError('COMMON_EDIT_RESPONSE_IDENTITY_INVALID')
    wire = response.read(COMMON_OBSERVATION_MAX_BYTES + 1)
    encoding = str(response.headers.get('Content-Encoding') or '').lower()
    if len(wire) > COMMON_OBSERVATION_MAX_BYTES or encoding not in ('', 'identity', 'gzip'):
        raise ValueError('COMMON_EDIT_RESPONSE_BODY_INVALID')
    compressed = encoding == 'gzip' or wire[:2] == b'\x1f\x8b'
    if compressed:
        with gzip.GzipFile(fileobj=io.BytesIO(wire)) as stream:
            business = stream.read(COMMON_OBSERVATION_MAX_BYTES + 1)
    else:
        business = wire
    def unique(pairs):
        value = {}
        for name, item in pairs:
            if name in value:
                raise ValueError('COMMON_EDIT_RESPONSE_DUPLICATE_KEY')
            value[name] = item
        return value
    result = json.loads(business.decode('utf-8', errors='strict'), object_pairs_hook=unique)
    if len(business) > COMMON_OBSERVATION_MAX_BYTES or type(result) is not dict:
        raise ValueError('COMMON_EDIT_RESPONSE_BODY_INVALID')
    # Refuse secrets instead of rewriting bytes and mislabelling them original.
    sensitive = {'authorization','access_token','refresh_token','app_secret',
                 'client_secret','x-sign','x-app-key','signature'}
    pending = [result]
    while pending:
        item = pending.pop()
        if type(item) is dict:
            if any(str(name).lower() in sensitive for name in item):
                raise ValueError('COMMON_EDIT_RESPONSE_SECRET_REFUSED')
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
    if any(value.encode() in business for value in (key, secret, request.get_header('X-sign'))):
        raise ValueError('COMMON_EDIT_RESPONSE_SECRET_REFUSED')
    if result.get('result') == 'success':
        packet = {'schema_version':'native-common-edit-wire/v1', 'endpoint':request.full_url,
            'http_status':200, 'credential_scope_digest':hashlib.sha256(
                ('miaoshou-app-scope:' + key).encode()).hexdigest(),
            'content_encoding':'gzip' if compressed else 'identity',
            'request_base64':base64.b64encode(request.data).decode('ascii'),
            'request_sha256':hashlib.sha256(request.data).hexdigest(),
            'wire_base64':base64.b64encode(wire).decode('ascii'),
            'wire_sha256':hashlib.sha256(wire).hexdigest(),
            'business_base64':base64.b64encode(business).decode('ascii'),
            'business_sha256':hashlib.sha256(business).hexdigest()}
        retain_native_edit_response(EDIT_PATH, request.data, packet)
    return result


@dataclass(frozen=True)
class CommonDetailObservation:
    """Business response bytes; no request credentials or approval authority.

    This observation is produced by the explicitly installed native reader.
    Retaining it does not supply a standing policy, current-lineage budget or
    execution grant. Legacy parsed JSON is never converted into this receipt.
    """
    detail_id: str
    endpoint: str
    credential_scope_digest: str
    wire_bytes: bytes
    business_bytes: bytes
    content_encoding: str
    http_status: int

    def response(self):
        return json.loads(self.business_bytes)

    def receipt(self):
        return {
            'schema_version': 'native-common-detail-observation/v1',
            'evidence_kind': 'NATIVE_TRANSPORT_OBSERVATION_NOT_APPROVAL',
            'execution_authority': False,
            'detail_id': self.detail_id, 'endpoint': self.endpoint,
            # Hash identifies the configured app scope without disclosing its
            # key. It is not an independent claim of an authenticated account.
            'credential_scope_digest': self.credential_scope_digest,
            'http_status': self.http_status, 'content_encoding': self.content_encoding,
            'wire_sha256': hashlib.sha256(self.wire_bytes).hexdigest(),
            'business_sha256': hashlib.sha256(self.business_bytes).hexdigest(),
            'wire_base64': base64.b64encode(self.wire_bytes).decode('ascii'),
            'business_base64': base64.b64encode(self.business_bytes).decode('ascii'),
        }


class _RefuseCommonDetailRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse before urllib can forward a COMMON signed request anywhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('COMMON_OBSERVER_REDIRECT_REFUSED')


class NativeCommonDetailObserver:
    """Explicit service-owned, fixed-endpoint read; never installed by default."""
    def __init__(self, cfg):
        if not isinstance(cfg, dict):
            raise ValueError('COMMON_OBSERVER_CONFIG_REQUIRED')
        self._config = dict(cfg)

    def observe(self, detail_id):
        value = str(detail_id)
        if len(value)>19 or not value.isascii() or not value.isdigit() or int(value) <= 0:
            raise ValueError('COMMON_OBSERVER_DETAIL_ID_INVALID')
        value = str(int(value))
        config = self._config
        key, secret = str(config.get('app_id') or ''), str(config.get('app_secret') or '')
        root = str(config.get('base_url') or OPEN_BASE_URL).rstrip('/')
        if root != OPEN_BASE_URL or not key or not secret:
            raise ValueError('COMMON_OBSERVER_SOURCE_CONFIG_INVALID')
        path = COMMON_DETAIL_OBSERVATION_PATH
        body = json.dumps({'commonCollectBoxDetailId': int(value)}, separators=(',', ':'))
        _wait_for_open_slot()
        stamp = int(time.time())
        signature = _open_sign(secret, path, stamp, key, body)
        request = urllib.request.Request(root + path, data=body.encode(), method='POST',
            headers={'Content-Type': 'application/json', 'x-app-key': key,
                'x-timestamp': str(stamp), 'x-sign': signature})
        try:
            # geturl() below still checks the final response identity, but that
            # is too late to prevent urllib's default automatic redirect from
            # forwarding x-app-key/x-sign. Refuse in the redirect handler before
            # a second request, including redirects to a different local path.
            opener = urllib.request.build_opener(_RefuseCommonDetailRedirect())
            with opener.open(request, timeout=30) as response:
                if response.geturl() != root + path or response.status != 200:
                    raise ValueError('COMMON_OBSERVER_TRANSPORT_IDENTITY_INVALID')
                wire = response.read(COMMON_OBSERVATION_MAX_BYTES + 1)
                encoding = str(response.headers.get('Content-Encoding') or '').lower()
                if len(wire) > COMMON_OBSERVATION_MAX_BYTES or encoding not in ('', 'identity', 'gzip'):
                    raise ValueError('COMMON_OBSERVER_BODY_INVALID')
                compressed = encoding == 'gzip' or wire[:2] == b'\x1f\x8b'
                if compressed:
                    with gzip.GzipFile(fileobj=io.BytesIO(wire)) as stream:
                        decoded = stream.read(COMMON_OBSERVATION_MAX_BYTES + 1)
                else:
                    decoded = wire
                if len(decoded) > COMMON_OBSERVATION_MAX_BYTES:
                    raise ValueError('COMMON_OBSERVER_BODY_INVALID')
                result = json.loads(decoded.decode('utf-8', errors='strict'))
                if not isinstance(result, dict):
                    raise ValueError('COMMON_OBSERVER_BODY_INVALID')
                # Never persist a provider echo of request credentials. Fail
                # closed rather than redact bytes and call them original.
                pending = [result]
                sensitive = {'authorization','access_token','refresh_token','app_secret',
                    'client_secret','x-sign','x-app-key','signature'}
                contains_secret = False
                while pending:
                    item = pending.pop()
                    if isinstance(item, dict):
                        if any(str(k).lower() in sensitive for k in item):
                            contains_secret = True
                            break
                        pending.extend(item.keys())
                        pending.extend(item.values())
                    elif isinstance(item, list):
                        pending.extend(item)
                    elif isinstance(item, str) and (key in item or secret in item or signature in item):
                        contains_secret = True
                        break
                if key.encode() in decoded or secret.encode() in decoded or signature.encode() in decoded or contains_secret:
                    raise ValueError('COMMON_OBSERVER_SENSITIVE_RESPONSE_REFUSED')
                if str(result.get('result') or '').lower() != 'success':
                    code, field_path, reason = _business_rejection_details(result)
                    raise MiaoshouBusinessRejectedError(reason, code=code, field_path=field_path)
        except MiaoshouBusinessRejectedError:
            raise
        except ValueError as error:
            if str(error).startswith('COMMON_OBSERVER_'):
                raise
            raise ValueError('COMMON_OBSERVER_BODY_INVALID') from None
        except Exception:
            raise RuntimeError('COMMON_OBSERVER_TRANSPORT_OR_BODY_UNAVAILABLE') from None
        return CommonDetailObservation(value, root + path,
            hashlib.sha256(('miaoshou-app-scope:' + key).encode()).hexdigest(),
            wire, decoded, 'gzip' if compressed else 'identity', 200)


def validate_common_observation_receipt(value, *, detail_id):
    """Validate retained transport bytes, without declaring them authority."""
    expected = {'schema_version','evidence_kind','execution_authority','detail_id',
        'endpoint','credential_scope_digest','http_status','content_encoding',
        'wire_sha256','business_sha256','wire_base64','business_base64','comparison_sha256'}
    try:
        if (type(value) is not dict or set(value) != expected
                or value['schema_version'] != 'native-common-detail-observation/v1'
                or value['evidence_kind'] != 'NATIVE_TRANSPORT_OBSERVATION_NOT_APPROVAL'
                or value['execution_authority'] is not False
                or value['detail_id'] != str(detail_id)
                or value['endpoint'] != OPEN_BASE_URL + COMMON_DETAIL_OBSERVATION_PATH
                or value['http_status'] != 200
                or value['content_encoding'] not in ('identity','gzip')
                or not re.fullmatch(r'[0-9a-f]{64}', value['credential_scope_digest'])):
            raise ValueError('receipt identity')
        if any(type(value[k]) is not str or len(value[k]) > (COMMON_OBSERVATION_MAX_BYTES+2)*4//3
               for k in ('wire_base64','business_base64')):
            raise ValueError('receipt body size')
        wire = base64.b64decode(value['wire_base64'], validate=True)
        business = base64.b64decode(value['business_base64'], validate=True)
        if value['content_encoding']=='gzip':
            with gzip.GzipFile(fileobj=io.BytesIO(wire)) as stream:
                decoded = stream.read(COMMON_OBSERVATION_MAX_BYTES+1)
        else:
            decoded = wire
        parsed = json.loads(business.decode('utf-8',errors='strict'))
        if (len(wire)>COMMON_OBSERVATION_MAX_BYTES or len(business)>COMMON_OBSERVATION_MAX_BYTES
                or decoded != business or hashlib.sha256(wire).hexdigest()!=value['wire_sha256']
                or hashlib.sha256(business).hexdigest()!=value['business_sha256']
                or type(parsed) is not dict or parsed.get('result')!='success'
                or not isinstance((parsed.get('data') or {}).get('editCommonCollectBoxDetail'),dict)):
            raise ValueError('receipt bytes')
    except Exception:
        raise ValueError('COMMON_OBSERVATION_RECEIPT_INVALID') from None


def get_shop_list(
    platform: str,
    site: str,
    page_no: int = 1,
    page_size: int = 20,
    **kwargs: Any,
) -> dict[str, Any]:
    return post_open(
        "/open/v1/product/shop/shop/get_shop_list",
        {
            "platform": platform,
            "site": site,
            "pageNo": page_no,
            "pageSize": page_size,
        },
        **kwargs,
    )


def _default_web_headers(cfg: dict[str, Any], path: str) -> dict[str, str]:
    root = str(cfg.get("web_base_url") or cfg.get("erp_base_url") or WEB_BASE_URL).rstrip("/")
    referer = str(cfg.get("web_referer") or f"{root}/")
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": root,
        "Referer": referer,
        "User-Agent": str(cfg.get("web_user_agent") or cfg.get("user_agent") or "Mozilla/5.0"),
        "X-Timestamp": str(int(time.time() * 1000)),
    }
    cookie = str(
        cfg.get("web_cookie")
        or cfg.get("erp_cookie")
        or os.environ.get("MIAOSHOU_WEB_COOKIE")
        or ""
    ).strip()
    if cookie:
        headers["Cookie"] = cookie
    for key, value in (cfg.get("web_headers") or {}).items():
        if value is None:
            continue
        headers[str(key)] = str(value)
    return headers


def _web_ok(result: dict[str, Any]) -> bool:
    if result.get("success") is True:
        return True
    code = result.get("code")
    if code in (0, "0", 200, "200"):
        return True
    status = str(result.get("status") or "").lower()
    return status in {"success", "ok"}


def request_web(
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    form: dict[str, Any] | list[tuple[str, Any]] | str | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    cfg = _load_config()
    root = str(cfg.get("web_base_url") or cfg.get("erp_base_url") or WEB_BASE_URL).rstrip("/")
    url = root + path
    if query:
        qs = urllib.parse.urlencode(query, doseq=True)
        url = f"{url}?{qs}"
    if isinstance(form, str):
        data = form.encode("utf-8")
    elif form is None:
        data = None
    else:
        data = urllib.parse.urlencode(form, doseq=True).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method.upper())
    merged_headers = _default_web_headers(cfg, path)
    if headers:
        merged_headers.update(headers)
    for key, value in merged_headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = _decode_json_response(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Miaoshou web HTTP {exc.code}: {raw[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Miaoshou web network error: {exc}") from exc
    if not _web_ok(result):
        code, field_path, reason = _business_rejection_details(result)
        raise MiaoshouBusinessRejectedError(
            reason,
            code=code,
            field_path=field_path,
        )
    return result


def web_claim_common_to_platform(detail_id: int | str, *, platform: str = "tiktok", serial_number: int = 1) -> dict[str, Any]:
    return request_web(
        "POST",
        "/api/move/common_collect_box/claimed",
        form={
            "detailSerialNumberPlatformList[0][detailId]": int(detail_id),
            "detailSerialNumberPlatformList[0][platform]": platform,
            "detailSerialNumberPlatformList[0][serialNumber]": int(serial_number),
        },
    )


def web_claim_to_shop(detail_ids: list[int | str], shop_ids: list[int | str]) -> dict[str, Any]:
    form: list[tuple[str, Any]] = []
    for index, detail_id in enumerate(detail_ids):
        form.append((f"detailIds[{index}]", int(detail_id)))
    for index, shop_id in enumerate(shop_ids):
        form.append((f"shopIds[{index}]", int(shop_id)))
    return request_web("POST", "/api/platform/tiktok/move/collect_box/claimToShop", form=form)


def web_get_collect_item_info(detail_id: int | str) -> dict[str, Any]:
    return request_web(
        "POST",
        "/api/platform/tiktok/move/collect_box/getCollectItemInfo",
        form={"detailId": int(detail_id)},
    )


def web_get_site_and_claimed_shops_map(detail_id: int | str) -> dict[str, Any]:
    return request_web(
        "GET",
        "/api/platform/tiktok/move/collect_box/getSiteAndClaimedShopsMap",
        query={"detailId": int(detail_id)},
    )


def web_get_global_shop_warehouse_list(*, status: str = "enable") -> dict[str, Any]:
    return request_web(
        "GET",
        "/api/platform/tiktok/move/collect_box/getGlobalShopWarehouseList",
        query={"status": status},
    )


def web_get_shop_warehouse_list(shop_ids: list[int | str]) -> dict[str, Any]:
    query = [("shopIds[]", int(shop_id)) for shop_id in shop_ids]
    return request_web(
        "GET",
        "/api/platform/tiktok/move/collect_box/getShopWarehouseList",
        query=query,
    )


def web_check_sku_price_include_vat_covering_base_cost(
    *,
    package_weight: float,
    sku_map: dict[str, Any],
    site: str,
) -> dict[str, Any]:
    return request_web(
        "POST",
        "/api/platform/tiktok/move/collect_box/checkSkuPriceIncludeVatCoveringBaseCost",
        form={
            "packageWeight": package_weight,
            "skuMap": json.dumps(sku_map, ensure_ascii=False, separators=(",", ":")),
            "site": site,
        },
    )


def web_save_shop_collect_item_info(shop_collect_item_info: dict[str, Any]) -> dict[str, Any]:
    return request_web(
        "POST",
        "/api/platform/tiktok/move/collect_box/saveShopCollectItemInfo",
        form={
            "shopCollectItemInfo": json.dumps(
                shop_collect_item_info,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        },
    )


def web_batch_set_tiktok_price(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply an exact local TikTok price through Miaoshou's web protocol."""

    return request_web(
        "POST",
        "/api/platform/tiktok/move/collect_box/batchSetPrice",
        form=json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        headers={"Content-Type": "application/json;charset=UTF-8"},
    )
