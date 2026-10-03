from io import BytesIO

from PIL import Image

from modules.sourcing.brand_image_lingshi_generation import generate_brand_image


def _jpeg(path):
    Image.new("RGB", (800, 1200), "white").save(path, format="JPEG", quality=90)


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2048, 2048), "white").save(output, format="PNG")
    return output.getvalue()


class FakeClient:
    def __init__(self):
        self.created = 0
        self.queried = 0

    def create_media_generation(self, **kwargs):
        self.created += 1
        assert kwargs["model"] == "gpt-image-2"
        assert kwargs["allow_paid_request"] is True
        assert kwargs["allow_external_upload"] is True
        assert kwargs["params"]["size"] == "2048x2048"
        assert kwargs["params"]["quality"] == "medium"
        assert kwargs["params"]["images"][0].startswith("data:image/jpeg;base64,")
        return {"code": 200, "data": {"task_id": 314}}

    def get_media_task(self, task_id):
        self.queried += 1
        assert task_id == 314
        return {
            "task_id": 314,
            "is_final": True,
            "status_group": "已完成",
            "result_url": "https://example.com/result.png",
            "cost": 0.1438,
            "channel_group": "price-first",
        }


def test_lingshi_brand_generation_uploads_local_reference_and_reuses_checkpoint(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.jpg"
    _jpeg(source)
    client = FakeClient()
    monkeypatch.setattr(
        "modules.sourcing.brand_image_lingshi_generation._download_result",
        lambda _url: _png(),
    )
    kwargs = {
        "offer_id": "8303674417505583242",
        "brand_id": "livelyhive-sea",
        "brand_label": "LivelyHive SEA",
        "positioning": "modern and clear",
        "role": "cover",
        "brief": "accurate floral bird wallpaper cover",
        "product_identity": "PVC floral bird wallpaper roll",
        "source_paths": [source],
        "checkpoint_dir": tmp_path / "checkpoints",
        "client": client,
    }

    first = generate_brand_image(**kwargs)
    second = generate_brand_image(**kwargs)

    assert first["receipt"]["provider"] == "lingshi-media/v1"
    assert first["receipt"]["model"] == "gpt-image-2"
    assert first["receipt"]["external_generation_count"] == 1
    assert second["receipt"] == first["receipt"]
    assert client.created == 1
    assert client.queried == 1
