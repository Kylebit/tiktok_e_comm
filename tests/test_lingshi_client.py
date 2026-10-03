import json
from pathlib import Path

import pytest

from modules.sourcing.lingshi_client import LingshiClient, LingshiClientError


class FakeResponse:
    def __init__(self, payload, *, status_code=200, headers=None, lines=None):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = json.dumps(payload)
        self.headers = headers or {"Content-Type": "application/json"}
        self._lines = list(lines or [])

    def json(self):
        return self._payload

    def iter_lines(self):
        return iter(self._lines)


class RecordingSession:
    def __init__(self, responses=None):
        self.responses = list(responses or [FakeResponse({"ok": True})])
        self.calls = []
        self.trust_env = True

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def test_discovery_uses_bearer_auth_and_read_only_endpoint():
    session = RecordingSession([FakeResponse({"skills": []})])
    client = LingshiClient(api_key="secret-value", session=session)

    assert client.list_skills() == {"skills": []}
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url == "https://api.lk888.ai/api/v1/skills"
    assert kwargs["headers"]["Authorization"] == "Bearer secret-value"
    assert session.trust_env is False


def test_paid_calls_are_blocked_by_default():
    session = RecordingSession()
    client = LingshiClient(api_key="secret-value", session=session)

    with pytest.raises(PermissionError, match="allow_paid_request=True"):
        client.chat_completions(
            model="gpt-5.4-mini",
            messages=[{"role": "user", "content": "hello"}],
        )

    assert session.calls == []


def test_hosted_agent_preview_is_network_free_and_paid_call_is_guarded():
    session = RecordingSession()
    client = LingshiClient(api_key="secret-value", session=session)

    preview = client.preview_agent_chat(
        model="gpt-5.4",
        message="plan this product",
        settings={"memory": False},
    )
    assert preview["url"] == "https://api.lk888.ai/v1/agent/chat"
    assert preview["payload"]["settings"]["memory"] is False
    with pytest.raises(PermissionError, match="allow_paid_request=True"):
        client.agent_chat(model="gpt-5.4", message="plan this product")
    assert session.calls == []


def test_hosted_agent_sse_keeps_business_content_and_discards_reasoning():
    lines = [
        'data: {"type":"meta","conversation_id":"conv-1","effective_settings":{"memory":false}}',
        'data: {"id":"x","choices":[{"delta":{"reasoning_content":"hidden"}}]}',
        'data: {"id":"x","choices":[{"delta":{"content":"{\\"action\\":\\"BLOCKED\\","}}]}',
        'data: {"type":"content","content":"\\"code\\":\\"MISSING_FACT\\",\\"question\\":\\"尺寸？\\"}"}',
        'data: {"type":"usage","cost":0.01,"total_tokens":20}',
        'data: {"type":"done","status":"completed"}',
        "data: [DONE]",
    ]
    response = FakeResponse(
        {}, headers={"Content-Type": "text/event-stream; charset=utf-8"}, lines=lines
    )
    session = RecordingSession([response])
    client = LingshiClient(api_key="secret-value", session=session)

    result = client.agent_chat(
        model="gpt-5.4",
        message="plan this product",
        allow_paid_request=True,
    )

    assert json.loads(result["content"])["action"] == "BLOCKED"
    assert "hidden" not in json.dumps(result, ensure_ascii=False)
    assert result["conversation_id"] == "conv-1"
    assert result["usage"]["cost"] == 0.01
    assert session.calls[0][2]["stream"] is True


def test_image_preview_is_network_free_and_uses_verified_openai_endpoint():
    session = RecordingSession()
    client = LingshiClient(api_key="secret-value", session=session)

    preview = client.preview_image_generation(
        prompt="plain white product cover",
        model="gpt-image-2",
        size="1024x1024",
    )

    assert preview["mode"] == "preview_only_no_network"
    assert preview["url"] == "https://api.lk888.ai/v1/images/generations"
    assert preview["payload"]["model"] == "gpt-image-2"
    assert session.calls == []


def test_missing_key_does_not_make_a_network_request(monkeypatch):
    monkeypatch.delenv("LINGSHI_API_KEY", raising=False)
    monkeypatch.delenv("LK888_API_KEY", raising=False)
    session = RecordingSession()
    client = LingshiClient(api_key="", session=session)

    with pytest.raises(LingshiClientError, match="LINGSHI_API_KEY"):
        client.balance()

    assert session.calls == []


def test_lk888_api_key_environment_alias_is_supported(monkeypatch):
    monkeypatch.delenv("LINGSHI_API_KEY", raising=False)
    monkeypatch.setenv("LK888_API_KEY", "alias-secret")
    session = RecordingSession([FakeResponse({"data": {"balance": "1"}})])

    result = LingshiClient(session=session).balance()

    assert result == {"data": {"balance": "1"}}
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer alias-secret"


def test_http_error_never_exposes_api_key():
    key = "super-secret-key"
    session = RecordingSession(
        [FakeResponse({"error": {"message": f"bad request {key}"}}, status_code=400)]
    )
    client = LingshiClient(api_key=key, session=session)

    with pytest.raises(LingshiClientError) as exc_info:
        client.balance()

    assert key not in str(exc_info.value)


def test_local_config_loader_accepts_ignored_json(tmp_path: Path):
    config = tmp_path / "lingshi.local.json"
    config.write_text(
        json.dumps({"api_key": "from-file", "base_url": "https://api.lk888.ai"}),
        encoding="utf-8",
    )

    client = LingshiClient.from_config(config, session=RecordingSession())

    assert client.has_api_key is True


def test_media_generation_preview_uses_live_discovery_contract():
    session = RecordingSession()
    client = LingshiClient(api_key="secret-value", session=session)

    preview = client.preview_media_generation(
        model="gemini-3.1-flash-image-preview",
        prompt="translate the approved product image",
        params={"image": "https://example.com/source.png"},
    )

    assert preview["url"] == "https://api.lk888.ai/api/v1/media/generate"
    assert preview["external_upload_required"] is True
    assert session.calls == []


def test_media_upload_requires_both_paid_and_upload_gates():
    session = RecordingSession()
    client = LingshiClient(api_key="secret-value", session=session)

    with pytest.raises(PermissionError, match="external_upload"):
        client.create_media_generation(
            model="gemini-3.1-flash-image-preview",
            prompt="translate the approved product image",
            params={"image": "https://example.com/source.png"},
            allow_paid_request=True,
        )

    assert session.calls == []


def test_model_pricing_can_request_active_channels_only():
    session = RecordingSession([FakeResponse({"channel_groups": []})])
    client = LingshiClient(api_key="secret-value", session=session)

    client.model_pricing("gpt-image-2", active_only=True)

    assert session.calls[0][1].endswith(
        "/api/v1/skills/models/gpt-image-2/pricing?status=active"
    )


def test_media_task_uses_live_task_status_path():
    session = RecordingSession([FakeResponse({"is_final": False})])
    client = LingshiClient(api_key="secret-value", session=session)

    client.get_media_task(111164842)

    assert session.calls[0][1].endswith(
        "/api/v1/skills/task-status?task_id=111164842"
    )
