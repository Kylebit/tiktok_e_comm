from __future__ import annotations

import json

import pytest

from modules.sourcing.localized_image_auto_translation import (
    AUTO_TRANSLATION_LOCALES,
    LocalizedImageAutoTranslationError,
    translate_image_regions,
)


REGIONS = [
    {
        "region_id": "text-11111111111111111111",
        "source_text": "Easy setup 44 cm",
        "bbox": [0.1, 0.1, 0.8, 0.2],
        "confidence": 0.99,
        "origin": "rapidocr-local/v1",
    }
]


def _payload() -> dict:
    translations = {
        "ms-MY": "Mudah dipasang 44 cm",
        "th-TH": "ติดตั้งง่าย 44 cm",
        "vi-VN": "Dễ lắp đặt 44 cm",
        "ru-RU": "Простая установка 44 cm",
        "es-MX": "Fácil de instalar 44 cm",
    }
    return {
        "schema_version": "localized-image-auto-translation/v1",
        "translations": {
            locale: [
                {
                    "region_id": REGIONS[0]["region_id"],
                    "source_text": REGIONS[0]["source_text"],
                    "translated_text": text,
                }
            ]
            for locale, text in translations.items()
        },
    }


def test_translates_one_image_to_all_locales_in_one_strict_model_call():
    calls = []

    def model_call(messages, *, temperature, max_tokens):
        calls.append((messages, temperature, max_tokens))
        return json.dumps(_payload(), ensure_ascii=False)

    result = translate_image_regions(REGIONS, model_call=model_call)

    assert len(calls) == 1
    assert tuple(result["translations"]) == AUTO_TRANSLATION_LOCALES
    assert result["translations"]["th-TH"][0]["translated_text"] == "ติดตั้งง่าย 44 cm"
    assert result["receipt"]["status"] == "AUTO_TRANSLATED"
    assert result["receipt"]["model"] == "gpt-5.4-mini-official"
    assert result["receipt"]["model_calls"] == 1
    assert "Translate only the supplied source_text" in calls[0][0][0]["content"]
    assert calls[0][2] == 8000


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda payload: payload["translations"].pop("ru-RU"), "locale coverage"),
        (
            lambda payload: payload["translations"]["th-TH"][0].update(
                {"region_id": "text-22222222222222222222"}
            ),
            "region coverage",
        ),
        (
            lambda payload: payload["translations"]["es-MX"][0].update(
                {"translated_text": "Fácil de instalar"}
            ),
            "numeric tokens",
        ),
        (
            lambda payload: payload["translations"]["th-TH"][0].update(
                {"translated_text": "Easy setup 44 cm"}
            ),
            "target language",
        ),
    ],
)
def test_rejects_incomplete_or_untranslated_model_output(mutate, match):
    payload = _payload()
    mutate(payload)

    with pytest.raises(LocalizedImageAutoTranslationError, match=match):
        translate_image_regions(
            REGIONS,
            model_call=lambda *_args, **_kwargs: json.dumps(payload, ensure_ascii=False),
        )


def test_no_text_requires_no_model_call():
    result = translate_image_regions(
        [],
        model_call=lambda *_args, **_kwargs: pytest.fail("model must not be called"),
    )

    assert result["translations"] == {locale: [] for locale in AUTO_TRANSLATION_LOCALES}
    assert result["receipt"]["model_calls"] == 0
    assert result["receipt"]["status"] == "NO_TEXT_REUSE_BASE"


def test_allows_valid_single_word_loanword_in_latin_script_locale():
    regions = [
        {
            "region_id": "text-33333333333333333333",
            "source_text": "STANDARD",
        }
    ]
    payload = {
        "schema_version": "localized-image-auto-translation/v1",
        "translations": {
            "ms-MY": [{"region_id": regions[0]["region_id"], "source_text": "STANDARD", "translated_text": "STANDARD"}],
            "th-TH": [{"region_id": regions[0]["region_id"], "source_text": "STANDARD", "translated_text": "มาตรฐาน"}],
            "vi-VN": [{"region_id": regions[0]["region_id"], "source_text": "STANDARD", "translated_text": "TIÊU CHUẨN"}],
            "ru-RU": [{"region_id": regions[0]["region_id"], "source_text": "STANDARD", "translated_text": "СТАНДАРТ"}],
            "es-MX": [{"region_id": regions[0]["region_id"], "source_text": "STANDARD", "translated_text": "ESTÁNDAR"}],
        },
    }

    result = translate_image_regions(
        regions,
        model_call=lambda *_args, **_kwargs: json.dumps(payload, ensure_ascii=False),
    )

    assert result["translations"]["ms-MY"][0]["translated_text"] == "STANDARD"


def test_accepts_locale_digits_when_the_numeric_fact_is_unchanged():
    payload = _payload()
    payload["translations"]["th-TH"][0]["translated_text"] = "ติดตั้งง่าย ๔๔ cm"

    result = translate_image_regions(
        REGIONS,
        model_call=lambda *_args, **_kwargs: json.dumps(payload, ensure_ascii=False),
    )

    assert result["translations"]["th-TH"][0]["translated_text"].endswith("๔๔ cm")
