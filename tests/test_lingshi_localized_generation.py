from io import BytesIO

from PIL import Image
import pytest

from modules.sourcing.localized_image_lingshi_generation import (
    generate_localized_reference_image,
)


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (1024, 1024), "white").save(output, format="PNG")
    return output.getvalue()


class FakeClient:
    def __init__(self):
        self.created = 0
        self.queried = 0

    def create_media_generation(self, **kwargs):
        self.created += 1
        assert kwargs["allow_paid_request"] is True
        assert kwargs["allow_external_upload"] is True
        assert kwargs["params"]["images"] == ["https://example.com/source.png"]
        return {"code": 200, "data": {"task_id": 42}}

    def get_media_task(self, task_id):
        self.queried += 1
        assert task_id == 42
        return {
            "task_id": 42,
            "is_final": True,
            "status_group": "已完成",
            "result_url": "https://example.com/result.png",
            "cost": 0.0629,
            "channel_group": "price-first",
        }


def test_lingshi_localized_generation_is_checkpointed_and_reused(tmp_path):
    client = FakeClient()
    kwargs = {
        "source_url": "https://example.com/source.png",
        "source_bytes": _png(),
        "locale": "th-TH",
        "translations": [
            {"source_text": "CLEAN", "translated_text": "ทำความสะอาด"}
        ],
        "checkpoint_dir": tmp_path,
        "client": client,
        "result_loader": lambda _url: _png(),
    }

    first = generate_localized_reference_image(**kwargs)
    second = generate_localized_reference_image(**kwargs)

    assert first["receipt"]["provider"] == "lingshi-media/v1"
    assert first["receipt"]["external_generation_count"] == 1
    assert second["receipt"] == first["receipt"]
    assert client.created == 1
    assert client.queried == 1


def test_lingshi_localized_attempt_number_alone_cannot_recreate_completed_task(tmp_path):
    client = FakeClient()
    kwargs = {
        "source_url": "https://example.com/source.png",
        "source_bytes": _png(),
        "locale": "th-TH",
        "translations": [
            {"source_text": "CLEAN", "translated_text": "ทำความสะอาด"}
        ],
        "checkpoint_dir": tmp_path,
        "client": client,
        "result_loader": lambda _url: _png(),
    }

    original = generate_localized_reference_image(**kwargs)
    retry = generate_localized_reference_image(**kwargs, retry_attempt=1)
    repeated = generate_localized_reference_image(**kwargs, retry_attempt=1)

    assert original["receipt"]["retry_attempt"] == 0
    assert retry["receipt"]["retry_attempt"] == 0
    assert retry["receipt"] == original["receipt"]
    assert repeated["receipt"] == retry["receipt"]
    assert client.created == 1
    with pytest.raises(ValueError, match="between zero and three"):
        generate_localized_reference_image(**kwargs, retry_attempt=4)
