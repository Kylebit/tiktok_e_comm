from io import BytesIO

import pytest
from PIL import Image

from modules.sourcing.localized_image_toapis_generation import (
    build_localized_reference_prompt,
    generate_localized_reference_image,
)
from modules.sourcing.toapis_client import ToAPIsClientError


def _image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (1200, 1200), "white").save(output, format="PNG")
    return output.getvalue()


class _Client:
    def __init__(self):
        self.created = []
        self.waited = []
        self.downloaded = []

    def create_generation(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "task-123"}

    def wait_for_generation(self, task_id, **kwargs):
        self.waited.append((task_id, kwargs))
        return {"status": "completed", "result": {"data": [{"url": "https://result.example/image.png"}]}}

    def download_result(self, result, destination):
        self.downloaded.append(result)
        destination.write_bytes(_image_bytes())
        return destination


def test_reference_generation_uses_exact_translation_and_is_durable(tmp_path):
    client = _Client()
    translations = [
        {
            "region_id": "r1",
            "source_text": "Size 45 x 70 cm",
            "translated_text": "ขนาด 45 x 70 ซม.",
        }
    ]

    first = generate_localized_reference_image(
        source_url="https://assets.example/source.png",
        source_bytes=_image_bytes(),
        locale="th-TH",
        translations=translations,
        checkpoint_dir=tmp_path,
        client=client,
    )
    second = generate_localized_reference_image(
        source_url="https://assets.example/source.png",
        source_bytes=_image_bytes(),
        locale="th-TH",
        translations=translations,
        checkpoint_dir=tmp_path,
        client=client,
    )

    assert len(client.created) == 1
    payload = client.created[0]
    assert payload["allow_generation"] is True
    assert payload["model"] == "gpt-image-2-official"
    assert payload["reference_images"] == ["https://assets.example/source.png"]
    assert "Size 45 x 70 cm" in payload["prompt"]
    assert "ขนาด 45 x 70 ซม." in payload["prompt"]
    assert first["receipt"] == second["receipt"]
    assert first["receipt"]["external_generation_count"] == 1
    assert first["image_bytes"].startswith(b"\x89PNG\r\n\x1a\n")


def test_failed_generation_is_not_blindly_resubmitted(tmp_path):
    class _Failing(_Client):
        def wait_for_generation(self, task_id, **kwargs):
            raise RuntimeError("ToAPIs generation failed: provider failed")

    client = _Failing()
    kwargs = {
        "source_url": "https://assets.example/source.png",
        "source_bytes": _image_bytes(),
        "locale": "ms-MY",
        "translations": [
            {"region_id": "r1", "source_text": "Easy", "translated_text": "Mudah"}
        ],
        "checkpoint_dir": tmp_path,
        "client": client,
    }
    with pytest.raises(RuntimeError, match="provider failed"):
        generate_localized_reference_image(**kwargs)
    with pytest.raises(RuntimeError, match="previous ToAPIs"):
        generate_localized_reference_image(**kwargs)
    assert len(client.created) == 1


def test_interrupted_poll_resumes_the_same_paid_task(tmp_path):
    class _Interrupted(_Client):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def wait_for_generation(self, task_id, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise TimeoutError("poll interrupted")
            return super().wait_for_generation(task_id, **kwargs)

    client = _Interrupted()
    kwargs = {
        "source_url": "https://assets.example/source.png",
        "source_bytes": _image_bytes(),
        "locale": "es-MX",
        "translations": [
            {"region_id": "r1", "source_text": "Easy", "translated_text": "Fácil"}
        ],
        "checkpoint_dir": tmp_path,
        "client": client,
    }
    with pytest.raises(TimeoutError, match="interrupted"):
        generate_localized_reference_image(**kwargs)
    completed = generate_localized_reference_image(**kwargs)

    assert len(client.created) == 1
    assert completed["receipt"]["task_id"] == "task-123"


def test_result_download_retries_without_resubmitting_paid_generation(tmp_path):
    class _InterruptedDownload(_Client):
        def __init__(self):
            super().__init__()
            self.download_attempts = 0

        def download_result(self, result, destination):
            self.download_attempts += 1
            if self.download_attempts < 3:
                raise ToAPIsClientError("ToAPIs image download failed: reset")
            return super().download_result(result, destination)

    client = _InterruptedDownload()
    completed = generate_localized_reference_image(
        source_url="https://assets.example/source.png",
        source_bytes=_image_bytes(),
        locale="ru-RU",
        translations=[
            {"region_id": "r1", "source_text": "Easy", "translated_text": "Легко"}
        ],
        checkpoint_dir=tmp_path,
        client=client,
    )

    assert len(client.created) == 1
    assert client.download_attempts == 3
    assert completed["receipt"]["task_id"] == "task-123"


def test_unknown_submission_queries_business_id_before_safe_resubmit(tmp_path):
    class _ResetBeforeReceipt(_Client):
        def __init__(self):
            super().__init__()
            self.create_attempts = 0
            self.lookups = []

        def create_generation(self, **kwargs):
            self.create_attempts += 1
            if self.create_attempts == 1:
                raise ConnectionResetError(10054, "connection reset")
            return super().create_generation(**kwargs)

        def get_generation(self, task_id):
            self.lookups.append(task_id)
            raise ToAPIsClientError('ToAPIs HTTP 404: {"code":"task_not_exist"}')

    client = _ResetBeforeReceipt()
    kwargs = {
        "source_url": "https://assets.example/source.png",
        "source_bytes": _image_bytes(),
        "locale": "th-TH",
        "translations": [
            {"region_id": "r1", "source_text": "Easy", "translated_text": "ง่าย"}
        ],
        "checkpoint_dir": tmp_path,
        "client": client,
    }
    with pytest.raises(ConnectionResetError):
        generate_localized_reference_image(**kwargs)
    completed = generate_localized_reference_image(**kwargs)

    assert client.lookups == [completed["receipt"]["client_business_id"]]
    assert client.create_attempts == 2
    assert completed["receipt"]["task_id"] == "task-123"


def test_prompt_rejects_empty_replacements():
    with pytest.raises(ValueError, match="incomplete"):
        build_localized_reference_prompt(
            locale="vi-VN",
            translations=[{"source_text": "Easy", "translated_text": ""}],
        )
