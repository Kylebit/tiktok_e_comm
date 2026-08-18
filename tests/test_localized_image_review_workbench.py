from __future__ import annotations

from io import BytesIO

from PIL import Image

from modules.sourcing import new_product_workbench as workbench


class _ReleaseStore:
    def active_plan_for_product(self, offer_id: str) -> dict:
        return {
            "plan_id": "omnichannel:approved-wallpaper",
            "product_id": offer_id,
            "status": "APPROVED",
        }

    def approved_publication_snapshot(self, *, offer_id: str, plan_id: str) -> dict:
        return {
            "schema_version": "approved-publication-snapshot/v4",
            "offer_id": offer_id,
            "plan_id": plan_id,
            "snapshot_digest": f"sha256:{'b' * 64}",
            "product": {
                "images": [
                    f"https://assets.example/master-{position:02d}.png"
                    for position in range(1, 8)
                ]
            },
            "publication_targets": [
                {"target_label": "miaoshou:COMMON"},
                {"target_label": "tiktok:LH_PH"},
                {"target_label": "tiktok:LH_MY"},
                {"target_label": "tiktok:LH_TH"},
                {"target_label": "tiktok:LH_VN"},
                {"target_label": "tiktok:MX"},
                {"target_label": "tiktok:GB"},
                {"target_label": "shopee:PH"},
                {"target_label": "shopee:MY"},
                {"target_label": "shopee:TH"},
                {"target_label": "shopee:VN"},
                {"target_label": "ozon:RU"},
            ],
        }


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (24, 24), "orange").save(output, format="PNG")
    return output.getvalue()


def test_selected_review_generation_is_paid_but_has_zero_platform_writes(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        workbench, "LOCALIZED_IMAGE_REVIEWS_DIR", tmp_path / "localized_image_reviews"
    )
    prepared = workbench.initialize_localized_image_review(
        "3899705757", release_store=_ReleaseStore()
    )
    review = prepared["review"]
    source_urls = list(dict.fromkeys(row["source_url"] for row in review["tasks"]))
    calls: list[tuple[str, str]] = []

    from modules.sourcing import localized_image_auto_translation as translation
    from modules.sourcing import localized_image_ocr as ocr

    monkeypatch.setattr(
        ocr,
        "detect_english_text_regions",
        lambda _raw, engine=None: [
            {
                "region_id": "text-aaaaaaaaaaaaaaaaaaaa",
                "source_text": "Easy to install",
                "bbox": [0.1, 0.1, 0.8, 0.2],
                "confidence": 0.99,
                "origin": "rapidocr-local/v1",
            }
        ],
    )
    monkeypatch.setattr(
        translation,
        "translate_image_regions",
        lambda _regions, model_call=None: {
            "translations": {
                locale: [
                    {
                        "region_id": "text-aaaaaaaaaaaaaaaaaaaa",
                        "source_text": "Easy to install",
                        "translated_text": f"translated-{locale}",
                    }
                ]
                for locale in ("ms-MY", "th-TH", "vi-VN", "ru-RU", "es-MX")
            }
        },
    )

    def generator(**kwargs):
        calls.append((kwargs["source_url"], kwargs["locale"]))
        number = len(calls)
        return {
            "image_bytes": _png(),
            "receipt": {
                "status": "COMPLETED",
                "provider": "toapis-images/v1",
                "model": "gpt-image-2-official",
                "task_id": f"provider-{number}",
                "client_business_id": f"localized-{number}",
                "request_attempted": True,
                "outcome_unknown": False,
                "external_generation_count": 1,
            },
        }

    generated = workbench.generate_localized_image_review(
        "3899705757",
        expected_revision=review["revision"],
        source_bytes_by_url={url: b"source-bytes" for url in source_urls},
        confirm_paid_generation=True,
        image_generator=generator,
    )

    assert len(calls) == 20
    assert generated["review"]["external_generation_count"] == 20
    assert generated["review"]["status"] == "REVIEW_REQUIRED"
    assert generated["platform_writes"] == 0
    assert generated["product_center_mutated"] is False


def test_generation_cannot_start_without_explicit_paid_confirmation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        workbench, "LOCALIZED_IMAGE_REVIEWS_DIR", tmp_path / "localized_image_reviews"
    )
    prepared = workbench.initialize_localized_image_review(
        "3899705757", release_store=_ReleaseStore()
    )

    try:
        workbench.generate_localized_image_review(
            "3899705757",
            expected_revision=prepared["review"]["revision"],
            source_bytes_by_url={},
        )
    except ValueError as error:
        assert "explicit paid" in str(error)
    else:
        raise AssertionError("paid generation started without confirmation")


def test_retry_reuses_frozen_translations_instead_of_creating_a_new_job_identity(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        workbench, "LOCALIZED_IMAGE_REVIEWS_DIR", tmp_path / "localized_image_reviews"
    )
    prepared = workbench.initialize_localized_image_review(
        "3899705757", release_store=_ReleaseStore()
    )
    review = prepared["review"]
    source_urls = list(dict.fromkeys(row["source_url"] for row in review["tasks"]))

    from modules.sourcing import localized_image_auto_translation as translation
    from modules.sourcing import localized_image_ocr as ocr

    monkeypatch.setattr(
        ocr,
        "detect_english_text_regions",
        lambda _raw, engine=None: [
            {
                "region_id": "text-aaaaaaaaaaaaaaaaaaaa",
                "source_text": "Easy to install",
                "bbox": [0.1, 0.1, 0.8, 0.2],
                "confidence": 0.99,
                "origin": "rapidocr-local/v1",
            }
        ],
    )
    translation_calls = 0

    def changing_translation(_regions, model_call=None):
        nonlocal translation_calls
        translation_calls += 1
        return {
            "translations": {
                locale: [
                    {
                        "region_id": "text-aaaaaaaaaaaaaaaaaaaa",
                        "source_text": "Easy to install",
                        "translated_text": f"attempt-{translation_calls}-{locale}",
                    }
                ]
                for locale in ("ms-MY", "th-TH", "vi-VN", "ru-RU", "es-MX")
            }
        }

    monkeypatch.setattr(translation, "translate_image_regions", changing_translation)
    observed: list[list[dict]] = []

    def interrupted_generator(**kwargs):
        observed.append(kwargs["translations"])
        raise ConnectionResetError(10054, "connection reset")

    kwargs = {
        "offer_id_or_url": "3899705757",
        "expected_revision": review["revision"],
        "source_bytes_by_url": {url: b"source-bytes" for url in source_urls},
        "confirm_paid_generation": True,
        "image_generator": interrupted_generator,
    }
    for _attempt in range(2):
        try:
            workbench.generate_localized_image_review(**kwargs)
        except ConnectionResetError:
            pass
        else:
            raise AssertionError("the fake transport should interrupt both attempts")

    assert translation_calls == len(source_urls)
    assert observed[0] == observed[1]
