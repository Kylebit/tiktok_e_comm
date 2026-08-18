from __future__ import annotations

from types import SimpleNamespace

import pytest

from modules.miaoshou import mx_publish, uk_publish
from modules.miaoshou import mx_migrate


class _Connection:
    def execute(self, *_args, **_kwargs):
        return SimpleNamespace(fetchone=lambda: {"price": 100})


def _exercise_listing(module, monkeypatch, *, title: str, image_urls: list[str], **kwargs):
    saved: dict = {}
    reads = 0

    def fake_post(path, body):
        nonlocal reads
        if path == module.GET_PATH:
            reads += 1
            return {
                "result": "success",
                "data": {
                    "ossMd5": "md5",
                    "shopCollectItemInfo": {
                        "title": "old title",
                        "notes": "<p>old notes</p>",
                        "imgUrls": ["https://old.example/image.jpg"],
                        "skuPropertyList": [],
                        "skuMap": {},
                    },
                },
            }
        if path == module.SAVE_PATH:
            saved.update(body["shopCollectItemInfo"])
            return {"result": "success"}
        raise AssertionError(path)

    monkeypatch.setattr(module, "init_db", lambda: None)
    monkeypatch.setattr(module, "connect", lambda: _Connection())
    monkeypatch.setattr(module, "ensure_mx_claimed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        module,
        "collect_master_images_and_product",
        lambda *_args, **_kwargs: (["https://old.example/image.jpg"], {"title": "old master"}),
    )
    monkeypatch.setattr(module, "apply_mx_shop_collect_info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "post_open", fake_post)

    rc = kwargs.pop("call")(
        collect_box_detail_id=123,
        seller_sku="0952",
        ph_product_id="ph-product",
        publish=False,
        listing_title=title,
        image_urls=image_urls,
        weight_kg=0.14,
        package_cm=(30, 3, 3),
        **kwargs,
    )
    assert rc == 0
    assert reads == 2
    assert saved["title"] == title
    assert image_urls[0] in saved["notes"]


def test_mx_publish_uses_audited_title_and_images_without_copy_model(monkeypatch):
    _exercise_listing(
        mx_publish,
        monkeypatch,
        title="Calcomanía de pared floral autoadhesiva",
        image_urls=["https://assets.example/approved-1.jpg"],
        call=mx_publish.publish_mx_listing,
        mxn_sale=192.5,
        mxn_list=275,
        spanish_copy=False,
    )


def test_gb_publish_uses_audited_title_and_images(monkeypatch):
    _exercise_listing(
        uk_publish,
        monkeypatch,
        title="Self-Adhesive Watercolour Floral Wall Sticker",
        image_urls=["https://assets.example/approved-1.jpg"],
        call=uk_publish.publish_uk_listing,
        gbp_sale=11.25,
        gbp_list=15,
    )


@pytest.mark.parametrize(
    ("module", "call", "price_kwargs"),
    [
        (
            mx_publish,
            mx_publish.publish_mx_listing,
            {"mxn_sale": 192.5, "mxn_list": 275, "spanish_copy": False},
        ),
        (
            uk_publish,
            uk_publish.publish_uk_listing,
            {"gbp_sale": 11.25, "gbp_list": 15},
        ),
    ],
)
def test_audited_listing_rejects_empty_https_image_set(
    monkeypatch, module, call, price_kwargs
):
    monkeypatch.setattr(module, "init_db", lambda: None)
    monkeypatch.setattr(module, "connect", lambda: _Connection())
    monkeypatch.setattr(module, "ensure_mx_claimed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        module,
        "collect_master_images_and_product",
        lambda *_args, **_kwargs: (["https://old.example/image.jpg"], {"title": "old master"}),
    )
    monkeypatch.setattr(
        module,
        "post_open",
        lambda *_args, **_kwargs: {
            "result": "success",
            "data": {
                "ossMd5": "md5",
                "shopCollectItemInfo": {
                    "notes": "",
                    "imgUrls": [],
                    "skuPropertyList": [],
                    "skuMap": {},
                },
            },
        },
    )
    with pytest.raises(RuntimeError, match="did not contain any HTTPS image"):
        call(
            collect_box_detail_id=123,
            seller_sku="0952",
            ph_product_id="ph-product",
            publish=False,
            listing_title="Audited title",
            image_urls=["http://unsafe.example/image.jpg"],
            weight_kg=0.14,
            package_cm=(30, 3, 3),
            **price_kwargs,
        )


def test_claims_shop_when_miaoshou_reports_no_prepublication_shop(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def fake_post(path, body):
        calls.append((path, body))
        if path == mx_migrate.GET_SHOP_PATH:
            raise RuntimeError("未选择预发布店铺")
        if path == mx_migrate.CLAIM_PATH:
            return {"result": "success"}
        raise AssertionError(path)

    monkeypatch.setattr("modules.miaoshou.client.post_open", fake_post)
    mx_migrate.ensure_mx_claimed(3224225704, mx_shop_id=16265910)

    assert calls == [
        (
            mx_migrate.GET_SHOP_PATH,
            {"detailId": 3224225704, "shopId": 16265910},
        ),
        (
            mx_migrate.CLAIM_PATH,
            {"detailIds": [3224225704], "shopIds": [16265910]},
        ),
    ]
