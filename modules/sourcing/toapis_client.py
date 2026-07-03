"""Controlled ToAPIs image-generation client.

Read-only account/model/task calls are available by default. Uploading a local
image and creating a generation task each require an explicit runtime gate.
The API key is read from ``KEY`` and is never persisted by this module.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

import requests

BASE_URL = "https://toapis.com"
API_KEY_ENV = "KEY"
DEFAULT_MODEL = "gemini-2.5-flash-image-preview"
TASK_MODELS = {
    "background_concept": "gemini-2.5-flash-image-preview",
    "size_card": "gpt-image-2",
    "text_localization": "gpt-image-2",
    "identity_edit": "gpt-image-2",
    "product_scene": "gpt-image-2",
}
ALLOWED_MODELS = frozenset(
    {
        "gemini-2.5-flash-image-preview",
        "gpt-image-2",
        "gpt-image-2-high",
        "gpt-image-2-official",
        "gpt-image-2-vip",
    }
)
STANDARD_SIZES = frozenset({"1:1", "3:2", "2:3"})
STANDARD_RESOLUTIONS = frozenset({"1k"})
GEMINI_SIZES = frozenset({"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"})
ALL_SIZES = frozenset(
    {"1:1", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5", "16:9", "9:16", "2:1", "1:2", "21:9", "9:21"}
)
ALL_RESOLUTIONS = frozenset({"1k", "2k", "4k"})
UPLOAD_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


class ToAPIsClientError(RuntimeError):
    pass


def model_for_task(task: str) -> str:
    value = str(task or "").strip().lower()
    try:
        return TASK_MODELS[value]
    except KeyError as exc:
        raise ValueError(f"unsupported ToAPIs image task: {value}") from exc


def _validate_model(model: str) -> str:
    value = str(model or "").strip()
    if value not in ALLOWED_MODELS:
        raise ValueError(f"unsupported ToAPIs image model: {value}")
    return value


def build_generation_payload(
    *,
    prompt: str,
    model: str = DEFAULT_MODEL,
    size: str = "1:1",
    resolution: str = "1k",
    reference_images: list[str] | None = None,
    client_business_id: str | None = None,
    n: int = 1,
) -> dict:
    model = _validate_model(model)
    prompt = str(prompt or "").strip()
    prompt_limit = 1_000 if model == DEFAULT_MODEL else 32_000
    if not prompt or len(prompt) > prompt_limit:
        raise ValueError(f"prompt must contain between 1 and {prompt_limit} characters")
    if model == DEFAULT_MODEL and int(n) != 1:
        raise ValueError(f"n must be 1 for {model}")
    if not 1 <= int(n) <= 4:
        raise ValueError("n must be between 1 and 4")

    size = str(size or "").lower()
    resolution = str(resolution or "").lower()
    if model == DEFAULT_MODEL:
        allowed_sizes = GEMINI_SIZES
        allowed_resolutions = STANDARD_RESOLUTIONS
    elif model == "gpt-image-2":
        allowed_sizes = STANDARD_SIZES
        allowed_resolutions = STANDARD_RESOLUTIONS
    else:
        allowed_sizes = ALL_SIZES
        allowed_resolutions = ALL_RESOLUTIONS
    if size not in allowed_sizes:
        raise ValueError(f"size {size!r} is not supported by {model}")
    if resolution not in allowed_resolutions:
        raise ValueError(f"resolution {resolution!r} is not supported by {model}")

    refs = []
    for raw_url in reference_images or []:
        parsed = urllib.parse.urlparse(str(raw_url or "").strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("reference_images must contain public HTTPS URLs")
        refs.append(parsed.geturl())
    if len(refs) > 16:
        raise ValueError("reference_images cannot exceed 16 items")

    payload = {"model": model, "prompt": prompt, "n": int(n), "size": size}
    if model == DEFAULT_MODEL:
        payload["metadata"] = {"resolution": resolution.upper()}
        if refs:
            payload["image_urls"] = [{"url": url} for url in refs]
    else:
        payload["resolution"] = resolution
        payload["response_format"] = "url"
        if refs:
            payload["reference_images"] = refs
    if client_business_id:
        business_id = str(client_business_id).strip()
        if not _TASK_ID_RE.fullmatch(business_id):
            raise ValueError("client_business_id contains unsupported characters")
        payload["client_business_id"] = business_id
    return payload


class ToAPIsClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        opener: Callable | None = None,
        session=None,
        timeout: float = 90,
    ):
        self._api_key = (api_key or os.environ.get(API_KEY_ENV) or "").strip()
        self._opener = opener
        self._session = session or requests.Session()
        if session is None:
            self._session.trust_env = False
        self._timeout = timeout

    def _require_key(self) -> None:
        if not self._api_key:
            raise ToAPIsClientError(f"missing {API_KEY_ENV} environment variable")

    def _request_json(self, path: str, *, method: str = "GET", body: bytes | None = None, headers: dict | None = None) -> dict:
        self._require_key()
        request_headers = {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": "Orbit-Hive-ToAPIs-Adapter/1.0",
        }
        request_headers.update(headers or {})
        req = urllib.request.Request(
            f"{BASE_URL}{path}", data=body, method=method, headers=request_headers
        )
        try:
            if self._opener is None:
                resp = self._session.request(
                    method,
                    req.full_url,
                    headers=request_headers,
                    data=body,
                    timeout=self._timeout,
                )
                if not resp.ok:
                    raise ToAPIsClientError(
                        f"ToAPIs HTTP {resp.status_code}: {resp.text[:500]}"
                    )
                result = resp.json()
            else:
                with self._opener(
                    req,
                    timeout=self._timeout,
                    context=ssl.create_default_context(),
                ) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise ToAPIsClientError(f"ToAPIs HTTP {exc.code}: {detail}") from exc
        except (requests.RequestException, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ToAPIsClientError(f"ToAPIs request failed: {exc}") from exc
        if not isinstance(result, dict):
            raise ToAPIsClientError("ToAPIs returned a non-object response")
        return result

    def balance(self) -> dict:
        return self._request_json("/v1/balance")

    def list_image_models(self) -> dict:
        return self._request_json("/v1/models?type=image")

    def preview_generation(self, **kwargs) -> dict:
        payload = build_generation_payload(**kwargs)
        return {
            "mode": "preview_only_no_network",
            "method": "POST",
            "url": f"{BASE_URL}/v1/images/generations",
            "payload": payload,
            "requires": ["allow_generation=True", API_KEY_ENV],
            "external_upload_required": bool(
                payload.get("reference_images") or payload.get("image_urls")
            ),
        }

    def upload_image(self, path: str | Path, *, allow_external_upload: bool = False) -> dict:
        if not allow_external_upload:
            raise PermissionError("ToAPIs upload requires allow_external_upload=True")
        self._require_key()
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(source)
        size = source.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise ValueError("ToAPIs image upload cannot exceed 10MB")
        mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        if mime not in UPLOAD_MIME_TYPES:
            raise ValueError(f"unsupported ToAPIs upload type: {mime}")

        boundary = f"----OrbitHive{uuid.uuid4().hex}"
        prefix = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{source.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
        suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
        body = prefix + source.read_bytes() + suffix
        result = self._request_json(
            "/v1/uploads/images",
            method="POST",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        if not result.get("success") or not ((result.get("data") or {}).get("url")):
            raise ToAPIsClientError("ToAPIs upload did not return an image URL")
        return result

    def create_generation(self, *, allow_generation: bool = False, **kwargs) -> dict:
        if not allow_generation:
            raise PermissionError("ToAPIs generation requires allow_generation=True")
        payload = build_generation_payload(**kwargs)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        result = self._request_json(
            "/v1/images/generations",
            method="POST",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        if not result.get("id"):
            raise ToAPIsClientError("ToAPIs generation did not return a task ID")
        return result

    def get_generation(self, task_id: str) -> dict:
        value = str(task_id or "").strip()
        if not _TASK_ID_RE.fullmatch(value):
            raise ValueError("invalid ToAPIs task ID")
        return self._request_json(f"/v1/images/generations/{urllib.parse.quote(value)}")

    def wait_for_generation(
        self,
        task_id: str,
        *,
        timeout: float = 120,
        poll_interval: float = 3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            result = self.get_generation(task_id)
            status = result.get("status")
            if status == "completed":
                return result
            if status == "failed":
                error = result.get("error") or {}
                raise ToAPIsClientError(f"ToAPIs generation failed: {error.get('message') or error}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"ToAPIs generation timed out: {task_id}")
            sleeper(poll_interval)

    def download_result(self, result: dict, destination: str | Path) -> Path:
        data = ((result.get("result") or {}).get("data") or [])
        if not data or not data[0].get("url"):
            raise ValueError("generation result does not contain an image URL")
        url = str(data[0]["url"])
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("generation result URL must use HTTPS")
        try:
            response = self._session.get(url, timeout=self._timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ToAPIsClientError(f"ToAPIs image download failed: {exc}") from exc
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
        return path
