import pytest

from shared_platform.product_description_media import (
    html_image_urls,
    miaoshou_rich_description,
    shopee_description_image_ids,
    shopee_description_text,
    shopee_extended_description,
    tiktok_rich_description,
    validate_description_media_preflight,
)


def test_miaoshou_description_contains_every_ordered_image_once() -> None:
    images = ["https://example.test/1.jpg", "https://example.test/2.jpg"]

    result = miaoshou_rich_description("Line 1\nLine 2", images)

    assert result == (
        "<p>Line 1<br>Line 2</p>"
        '<p><img src="https://example.test/1.jpg"></p>'
        '<p><img src="https://example.test/2.jpg"></p>'
    )


def test_shopee_description_round_trips_every_ordered_image_id() -> None:
    description_info = shopee_extended_description(
        "Approved description", ("image-1", "image-2")
    )

    assert shopee_description_image_ids(
        {"description_info": description_info}
    ) == ("image-1", "image-2")
    assert shopee_description_text(
        {"description_type": "extended", "description_info": description_info}
    ) == "Approved description"


def test_tiktok_description_uses_platform_urls_dimensions_and_exact_order() -> None:
    result = tiktok_rich_description(
        "Approved & localized",
        (
            {"urls": ["https://p16-oec.test/1.jpeg"], "width": 1024, "height": 1024},
            {"urls": ["https://p16-oec.test/2.jpeg"], "width": 800, "height": 900},
        ),
    )

    assert result == (
        "<p>Approved &amp; localized</p>"
        '<p><img src="https://p16-oec.test/1.jpeg" width="1024" height="1024"></p>'
        '<p><img src="https://p16-oec.test/2.jpeg" width="800" height="900"></p>'
    )
    assert html_image_urls(result) == (
        "https://p16-oec.test/1.jpeg",
        "https://p16-oec.test/2.jpeg",
    )


def test_tiktok_description_rejects_non_platform_readback_shape() -> None:
    with pytest.raises(ValueError):
        tiktok_rich_description("Approved", [{"url": "https://example.test/1.jpg"}])


@pytest.mark.parametrize("builder", [miaoshou_rich_description, shopee_extended_description])
def test_description_media_rejects_missing_or_duplicate_images(builder) -> None:
    with pytest.raises(ValueError):
        builder("Approved description", [])
    with pytest.raises(ValueError):
        builder("Approved description", ["same", "same"])


def test_description_media_preflight_proves_target_count_and_order() -> None:
    snapshot = {
        "product": {
            "description": "Approved description",
            "images": ["https://example.test/1.jpg", "https://example.test/2.jpg"],
        },
        "publication_targets": [
            {
                "target_label": "tiktok:LH_PH",
                "platform": "tiktok",
                "site": "LH_PH",
                "store": "LH_PH",
            }
        ],
    }

    result = validate_description_media_preflight(
        snapshot, platform="TIKTOK", target_labels=("tiktok:LH_PH",)
    )

    assert result["passed"] is True
    assert result["targets"][0]["gallery_image_count"] == 2
    assert result["targets"][0]["description_image_count"] == 2
    assert result["targets"][0]["ordered_route_digest"].startswith("sha256:")


def test_description_media_preflight_fails_before_write_for_duplicate_route() -> None:
    snapshot = {
        "product": {
            "description": "Approved description",
            "images": ["https://example.test/same.jpg", "https://example.test/same.jpg"],
        },
        "publication_targets": [
            {
                "target_label": "shopee:PH",
                "platform": "shopee",
                "site": "PH",
                "store": "PH",
            }
        ],
    }

    with pytest.raises(ValueError, match="duplicates"):
        validate_description_media_preflight(
            snapshot, platform="SHOPEE", target_labels=("shopee:PH",)
        )
