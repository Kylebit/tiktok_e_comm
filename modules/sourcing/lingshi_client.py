"""Guarded client for the Lingshi AI aggregation API.

Discovery calls are read-only. Model and image calls are paid external writes
and therefore require an explicit runtime gate. Credentials are loaded from an
environment variable or an ignored local JSON file and are never persisted by
this module.
"""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urlencode, urlparse

import requests


DEFAULT_BASE_URL = "https://api.lk888.ai"
API_KEY_ENV = "LINGSHI_API_KEY"
API_KEY_ENV_ALIAS = "LK888_API_KEY"
VERIFIED_OPENAI_IMAGE_MODELS = frozenset({"gpt-image-2", "gpt-image-2-guan"})
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
UPLOAD_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


def build_generation_payload(
    *,
    prompt: str,
    model: str,
    reference_images: list[str],
    size: str = "1:1",
    resolution: str = "1k",
    n: int = 1,
) -> dict[str, Any]:
    """Build the bounded Lingshi media payload used by local preflight screens."""

    del size, resolution
    if type(n) is not int or n != 1:
        raise ValueError("Lingshi image generation supports one output per task")
    selected_model = str(model or "").strip()
    if not selected_model or not str(prompt or "").strip():
        raise ValueError("model and prompt are required")
    if not 1 <= len(reference_images) <= 14:
        raise ValueError("Lingshi image generation requires 1-14 public HTTPS references")
    refs = []
    for value in reference_images:
        reference = str(value or "").strip()
        parsed = urlparse(reference)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("reference images must use public HTTPS without URL credentials")
        refs.append(reference)
    return {
        "model": selected_model,
        "prompt": str(prompt).strip(),
        "params": {"images": refs, "size": "2048x2048", "quality": "medium"},
    }


class LingshiClientError(RuntimeError):
    """Safe provider error that never includes the API key."""


def _validated_base_url(value: str) -> str:
    base = str(value or "").strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("Lingshi base_url must be an HTTPS origin")
    return base


class LingshiClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        session=None,
        timeout: float = 90,
    ):
        self._api_key = (
            api_key
            or os.environ.get(API_KEY_ENV)
            or os.environ.get(API_KEY_ENV_ALIAS)
            or ""
        ).strip()
        self._base_url = _validated_base_url(base_url)
        self._session = session or requests.Session()
        self._session.trust_env = False
        self._timeout = float(timeout)

    @classmethod
    def from_config(
        cls,
        path: str | Path,
        *,
        session=None,
        timeout: float | None = None,
    ) -> "LingshiClient":
        config_path = Path(path)
        if not config_path.is_file():
            raise FileNotFoundError(config_path)
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Lingshi config must be a JSON object")
        return cls(
            api_key=str(data.get("api_key") or ""),
            base_url=str(data.get("base_url") or DEFAULT_BASE_URL),
            session=session,
            timeout=float(timeout if timeout is not None else data.get("timeout_seconds") or 90),
        )

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    def _require_key(self) -> None:
        if not self._api_key:
            raise LingshiClientError(
                f"missing {API_KEY_ENV}/{API_KEY_ENV_ALIAS} or api_key in config/lingshi.local.json"
            )

    def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_key()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": "OrbitHive-Lingshi-Adapter/1.0",
        }
        try:
            response = self._session.request(
                method,
                f"{self._base_url}{path}",
                headers=headers,
                json=json_body,
                data=data,
                files=files,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise LingshiClientError(
                f"Lingshi request failed: {type(exc).__name__}"
            ) from exc
        if not response.ok:
            message = ""
            try:
                payload = response.json()
                error = payload.get("error") if isinstance(payload, dict) else None
                if isinstance(error, dict):
                    message = str(error.get("code") or error.get("message") or "")
                elif error:
                    message = str(error)
            except (ValueError, TypeError):
                pass
            message = message.replace(self._api_key, "[redacted]")[:160]
            suffix = f": {message}" if message else ""
            raise LingshiClientError(f"Lingshi HTTP {response.status_code}{suffix}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise LingshiClientError("Lingshi returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise LingshiClientError("Lingshi returned a non-object response")
        return payload

    # Read-only provider discovery.
    def list_skills(self) -> dict[str, Any]:
        return self._request_json("/api/v1/skills")

    def guide(self) -> dict[str, Any]:
        return self._request_json("/api/v1/skills/guide")

    def balance(self) -> dict[str, Any]:
        return self._request_json("/api/v1/skills/balance")

    def list_models(self, model_type: str | None = None) -> dict[str, Any]:
        path = "/api/v1/skills/models"
        if model_type:
            value = str(model_type).strip().lower()
            if value not in {"chat", "image", "video", "audio"}:
                raise ValueError("unsupported Lingshi model type")
            path = f"{path}?{urlencode({'type': value})}"
        return self._request_json(path)

    def model_detail(self, model: str) -> dict[str, Any]:
        value = str(model or "").strip()
        if not value:
            raise ValueError("model is required")
        return self._request_json(f"/api/v1/skills/models/{quote(value, safe='')}")

    def model_pricing(
        self, model: str, *, active_only: bool = False
    ) -> dict[str, Any]:
        value = str(model or "").strip()
        if not value:
            raise ValueError("model is required")
        suffix = "?status=active" if active_only else ""
        return self._request_json(
            f"/api/v1/skills/models/{quote(value, safe='')}/pricing{suffix}"
        )

    def usage(self, *, days: int = 1, detail: bool = False) -> dict[str, Any]:
        days = int(days)
        if not 1 <= days <= 30:
            raise ValueError("days must be between 1 and 30")
        query = urlencode({"scope": "key", "days": days, "detail": int(detail)})
        return self._request_json(f"/api/v1/skills/usage?{query}")

    def preview_chat(
        self, *, model: str, messages: list[dict[str, Any]], max_tokens: int = 1024
    ) -> dict[str, Any]:
        payload = {
            "model": str(model or "").strip(),
            "messages": messages,
            "max_tokens": int(max_tokens),
        }
        if not payload["model"] or not messages:
            raise ValueError("model and messages are required")
        return {
            "mode": "preview_only_no_network",
            "method": "POST",
            "url": f"{self._base_url}/v1/chat/completions",
            "payload": payload,
            "requires": ["allow_paid_request=True", API_KEY_ENV],
        }

    def chat_completions(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1024,
        allow_paid_request: bool = False,
    ) -> dict[str, Any]:
        preview = self.preview_chat(
            model=model, messages=messages, max_tokens=max_tokens
        )
        if not allow_paid_request:
            raise PermissionError("Lingshi paid call requires allow_paid_request=True")
        return self._request_json(
            "/v1/chat/completions", method="POST", json_body=preview["payload"]
        )

    def preview_agent_chat(
        self,
        *,
        model: str,
        message: str,
        conversation_id: str | None = None,
        attachments: list[str] | None = None,
        memory_id: str | None = None,
        system_prompt: str | None = None,
        settings: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": str(model or "").strip(),
            "message": str(message or "").strip(),
        }
        if not payload["model"] or not payload["message"]:
            raise ValueError("model and message are required")
        optional = {
            "conversation_id": str(conversation_id or "").strip(),
            "attachments": list(attachments or []),
            "memory_id": str(memory_id or "").strip(),
            "system_prompt": str(system_prompt or "").strip(),
            "settings": dict(settings or {}),
            "params": dict(params or {}),
        }
        payload.update({key: value for key, value in optional.items() if value})
        return {
            "mode": "preview_only_no_network",
            "method": "POST",
            "url": f"{self._base_url}/v1/agent/chat",
            "payload": payload,
            "requires": ["allow_paid_request=True", API_KEY_ENV],
        }

    @staticmethod
    def _parse_agent_frames(lines: Iterable[str | bytes]) -> dict[str, Any]:
        content: list[str] = []
        conversation_id = ""
        effective_settings: dict[str, Any] = {}
        usage: dict[str, Any] = {}
        done_status = ""
        tool_events: list[dict[str, Any]] = []
        error_message = ""
        for raw in lines:
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            line = line.strip()
            if not line.startswith("data:"):
                continue
            value = line[5:].strip()
            if not value or value == "[DONE]":
                continue
            try:
                frame = json.loads(value)
            except json.JSONDecodeError as exc:
                raise LingshiClientError("Lingshi agent returned an invalid SSE frame") from exc
            if not isinstance(frame, dict):
                continue
            frame_type = str(frame.get("type") or "")
            if not frame_type:
                choices = frame.get("choices")
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    delta = choices[0].get("delta")
                    if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                        content.append(delta["content"])
                continue
            if frame_type == "content" and isinstance(frame.get("content"), str):
                content.append(frame["content"])
            elif frame_type == "meta":
                conversation_id = str(frame.get("conversation_id") or conversation_id)
                if isinstance(frame.get("effective_settings"), dict):
                    effective_settings = dict(frame["effective_settings"])
            elif frame_type == "usage":
                usage = dict(frame)
            elif frame_type == "done":
                done_status = str(frame.get("status") or "")
            elif frame_type == "error":
                error_message = str(frame.get("message") or "Lingshi agent stream failed")
            elif frame_type.endswith("_status") or frame_type in {"init", "task_interrupted"}:
                tool_events.append(dict(frame))
            # thinking/reasoning frames are deliberately discarded: they are not
            # auditable business evidence and must never reach the product UI.
        if error_message:
            raise LingshiClientError(error_message[:200])
        return {
            "content": "".join(content),
            "conversation_id": conversation_id,
            "effective_settings": effective_settings,
            "usage": usage,
            "done_status": done_status,
            "tool_events": tool_events,
        }

    def agent_chat(
        self,
        *,
        allow_paid_request: bool = False,
        **kwargs,
    ) -> dict[str, Any]:
        preview = self.preview_agent_chat(**kwargs)
        if not allow_paid_request:
            raise PermissionError("Lingshi paid call requires allow_paid_request=True")
        self._require_key()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "OrbitHive-Lingshi-Adapter/1.0",
        }
        try:
            response = self._session.request(
                "POST",
                preview["url"],
                headers=headers,
                json=preview["payload"],
                timeout=self._timeout,
                stream=True,
            )
        except requests.RequestException as exc:
            raise LingshiClientError(
                f"Lingshi agent request failed: {type(exc).__name__}"
            ) from exc
        if not response.ok:
            raise LingshiClientError(f"Lingshi agent HTTP {response.status_code}")
        content_type = str(response.headers.get("Content-Type") or "").casefold()
        if "application/json" in content_type:
            payload = response.json()
            message = payload.get("msg") if isinstance(payload, dict) else ""
            code = payload.get("code") if isinstance(payload, dict) else ""
            raise LingshiClientError(f"Lingshi agent rejected request {code}: {message}"[:240])
        if "text/event-stream" not in content_type:
            raise LingshiClientError("Lingshi agent returned an unexpected content type")
        return self._parse_agent_frames(response.iter_lines())

    def preview_image_generation(
        self, *, prompt: str, model: str = "gpt-image-2", size: str = "1024x1024"
    ) -> dict[str, Any]:
        model = str(model or "").strip()
        prompt = str(prompt or "").strip()
        if model not in VERIFIED_OPENAI_IMAGE_MODELS:
            raise ValueError("image model is not verified for the OpenAI image endpoint")
        if not prompt:
            raise ValueError("prompt is required")
        payload = {"model": model, "prompt": prompt, "size": str(size or "").strip()}
        return {
            "mode": "preview_only_no_network",
            "method": "POST",
            "url": f"{self._base_url}/v1/images/generations",
            "payload": payload,
            "requires": ["allow_paid_request=True", API_KEY_ENV],
        }

    def create_image_generation(
        self, *, allow_paid_request: bool = False, **kwargs
    ) -> dict[str, Any]:
        preview = self.preview_image_generation(**kwargs)
        if not allow_paid_request:
            raise PermissionError("Lingshi paid call requires allow_paid_request=True")
        return self._request_json(
            "/v1/images/generations", method="POST", json_body=preview["payload"]
        )

    def preview_media_generation(
        self,
        *,
        model: str,
        prompt: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        model = str(model or "").strip()
        prompt = str(prompt or "").strip()
        if not model or not prompt:
            raise ValueError("model and prompt are required")
        payload = {"model": model, "prompt": prompt, "params": dict(params or {})}
        serialized_params = json.dumps(payload["params"], ensure_ascii=False)
        includes_upload = "data:" in serialized_params or "https://" in serialized_params
        return {
            "mode": "preview_only_no_network",
            "method": "POST",
            "url": f"{self._base_url}/api/v1/media/generate",
            "payload": payload,
            "requires": ["allow_paid_request=True", API_KEY_ENV],
            "external_upload_required": includes_upload,
        }

    def create_media_generation(
        self,
        *,
        allow_paid_request: bool = False,
        allow_external_upload: bool = False,
        **kwargs,
    ) -> dict[str, Any]:
        preview = self.preview_media_generation(**kwargs)
        if not allow_paid_request:
            raise PermissionError("Lingshi paid call requires allow_paid_request=True")
        if preview["external_upload_required"] and not allow_external_upload:
            raise PermissionError(
                "Lingshi media upload requires allow_external_upload=True"
            )
        return self._request_json(
            "/api/v1/media/generate", method="POST", json_body=preview["payload"]
        )

    def get_media_task(self, task_id: int) -> dict[str, Any]:
        value = int(task_id)
        if value < 1:
            raise ValueError("task_id must be a positive integer")
        return self._request_json(
            f"/api/v1/skills/task-status?{urlencode({'task_id': value})}"
        )

    def edit_image(
        self,
        image_path: str | Path,
        *,
        prompt: str,
        model: str = "gpt-image-2",
        allow_paid_request: bool = False,
        allow_external_upload: bool = False,
    ) -> dict[str, Any]:
        if not allow_paid_request:
            raise PermissionError("Lingshi paid call requires allow_paid_request=True")
        if not allow_external_upload:
            raise PermissionError("Lingshi image upload requires allow_external_upload=True")
        if model not in VERIFIED_OPENAI_IMAGE_MODELS:
            raise ValueError("image model is not verified for the OpenAI image endpoint")
        source = Path(image_path)
        if not source.is_file():
            raise FileNotFoundError(source)
        if source.stat().st_size > MAX_UPLOAD_BYTES:
            raise ValueError("Lingshi image upload cannot exceed 10MB")
        mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        if mime not in UPLOAD_MIME_TYPES:
            raise ValueError(f"unsupported Lingshi upload type: {mime}")
        with source.open("rb") as stream:
            return self._request_json(
                "/v1/images/edits",
                method="POST",
                data={"model": model, "prompt": str(prompt or "").strip()},
                files={"image": (source.name, stream, mime)},
            )
