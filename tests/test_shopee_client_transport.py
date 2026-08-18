import json
import json

import pytest

import core.http_retry as http_retry
import modules.shopee.client as shopee_client


class _Response:
    def __init__(self, value):
        self._payload = json.dumps(value).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def _merchant_transport(monkeypatch):
    monkeypatch.setattr(
        shopee_client,
        "shopee_config",
        lambda: {
            "host": "https://partner.test",
            "partner_id": 1,
            "partner_key": "not-a-real-key",
        },
    )


def _shop_transport(monkeypatch):
    monkeypatch.setattr(
        shopee_client,
        "shopee_config",
        lambda: {
            "host": "https://partner.test",
            "partner_id": 1,
            "partner_key": "not-a-real-key",
        },
    )
    monkeypatch.setattr(
        shopee_client,
        "sign_shop",
        lambda *_args: (1234567890, "fixture-signature"),
    )
    monkeypatch.setattr(
        shopee_client,
        "sign_merchant",
        lambda *_args: (1234567890, "fixture-signature"),
    )


@pytest.mark.parametrize("failure", [TimeoutError("timeout"), ConnectionResetError("reset")])
def test_merchant_mutation_post_sends_once_without_curl_fallback(monkeypatch, failure):
    _merchant_transport(monkeypatch)
    sends = []
    curl_fallbacks = []

    def fail_once(request, **_kwargs):
        sends.append(request)
        raise failure

    monkeypatch.setattr(http_retry.urllib.request, "urlopen", fail_once)
    monkeypatch.setattr(
        http_retry,
        "_curl_urlopen",
        lambda *_args, **_kwargs: curl_fallbacks.append(True),
    )
    monkeypatch.setattr(http_retry.time, "sleep", lambda _seconds: None)

    with pytest.raises(type(failure)):
        shopee_client.merchant_post(
            "/api/v2/global_product/update_global_model",
            99,
            "fixture-token",
            {"global_item_id": 1, "global_model": []},
        )

    assert len(sends) == 1
    assert curl_fallbacks == []


def test_merchant_get_retains_transport_retry(monkeypatch):
    _merchant_transport(monkeypatch)
    sends = []

    def eventually_succeed(request, **_kwargs):
        sends.append(request)
        if len(sends) < 3:
            raise TimeoutError("timeout")
        return _Response({"error": "", "response": {}})

    monkeypatch.setattr(http_retry.urllib.request, "urlopen", eventually_succeed)
    monkeypatch.setattr(http_retry.time, "sleep", lambda _seconds: None)

    assert shopee_client.merchant_get(
        "/api/v2/global_product/get_global_model_list",
        99,
        "fixture-token",
        {"global_item_id": 1},
    ) == {"error": "", "response": {}}
    assert len(sends) == 3


@pytest.mark.parametrize("failure", [TimeoutError("timeout"), ConnectionResetError("reset")])
def test_shop_mutation_post_sends_once_without_curl_fallback(monkeypatch, failure):
    _shop_transport(monkeypatch)
    sends = []
    curl_fallbacks = []

    def fail_once(request, **_kwargs):
        sends.append(request)
        raise failure

    monkeypatch.setattr(http_retry.urllib.request, "urlopen", fail_once)
    monkeypatch.setattr(
        http_retry,
        "_curl_urlopen",
        lambda *_args, **_kwargs: curl_fallbacks.append(True),
    )
    monkeypatch.setattr(http_retry.time, "sleep", lambda _seconds: None)

    with pytest.raises(type(failure)):
        shopee_client.shop_post(
            "/api/v2/product/update_item",
            99,
            "fixture-token",
            {"item_id": 1, "image": {"image_id_list": ["image-1"]}},
        )

    assert len(sends) == 1
    assert curl_fallbacks == []


def test_image_upload_sends_once_without_curl_fallback(monkeypatch, tmp_path):
    _shop_transport(monkeypatch)
    monkeypatch.setattr(
        shopee_client,
        "sign_partner",
        lambda *_args: (1234567890, "fixture-signature"),
    )
    image = tmp_path / "image.jpg"
    image.write_bytes(b"fixture-image")
    sends = []
    curl_fallbacks = []

    def fail_once(request, **_kwargs):
        sends.append(request)
        raise TimeoutError("timeout")

    monkeypatch.setattr(http_retry.urllib.request, "urlopen", fail_once)
    monkeypatch.setattr(
        http_retry,
        "_curl_urlopen",
        lambda *_args, **_kwargs: curl_fallbacks.append(True),
    )
    monkeypatch.setattr(http_retry.time, "sleep", lambda _seconds: None)

    with pytest.raises(TimeoutError):
        shopee_client.upload_image(image)

    assert len(sends) == 1
    assert curl_fallbacks == []
