from __future__ import annotations

import json

import pytest

from domains.content_operations.listing_title_candidates import (
    EXPECTED_TARGETS,
    TOAPI_TITLE_MODEL,
    fact_signature,
    generate_title_candidates,
    model_input_signature,
)


def _facts() -> dict:
    return {
        "offer_id": "3828540231",
        "source_title_zh": "水彩复古花卉蝴蝶墙贴",
        "category": {"name": "墙贴"},
        "cost_cny": 9,
        "weight_kg": 0.14,
        "package_cm": [30, 3, 3],
        "selected_skus": [{"key": "30x90cm", "label": "30x90cm"}],
        "verified_attributes": {"材质": "PVC"},
    }


def _model_payload() -> str:
    candidates = []
    titles = {
        ("tiktok", "MY"): "Watercolour Floral Butterfly PVC Wall Decal",
        ("tiktok", "PH"): "Watercolour Floral Butterfly PVC Wall Decal",
        ("tiktok", "TH"): "สติกเกอร์ติดผนังลายดอกไม้และผีเสื้อสีน้ำ PVC",
        ("tiktok", "VN"): "Decal dán tường PVC hoa và bướm màu nước",
        ("tiktok", "MX"): "Calcomanía de pared PVC con flores y mariposas acuarela",
        ("tiktok", "GB"): "Watercolour Floral Butterfly PVC Wall Decal",
        ("shopee", "CNSC"): "Watercolour Floral Butterfly PVC Wall Decal",
        ("ozon", "RU"): "ПВХ наклейка на стену с цветами и бабочками акварель",
    }
    for channel, site, language, _limit in EXPECTED_TARGETS:
        candidates.append(
            {
                "channel": channel,
                "site": site,
                "language": language,
                "title": titles[(channel, site)],
            }
        )
    return json.dumps(
        {
            "semantic_master_en": "Watercolour Floral Butterfly PVC Wall Decal",
            "shopee_description_en": (
                "PRODUCT OVERVIEW\n"
                "Add a soft watercolour floral and butterfly accent to a clean, "
                "smooth wall with this decorative PVC wall decal. The design is "
                "intended for simple home styling without adding unsupported "
                "performance claims.\n\n"
                "VERIFIED DETAILS\n"
                "Product type: decorative wall decal\n"
                "Material: PVC\n"
                "Design: watercolour flowers and butterflies\n"
                "Selected size: 30 x 90 cm\n\n"
                "SUITABLE SPACES\n"
                "Use the decal as a decorative accent in a living room, bedroom, "
                "study, hallway or another suitable indoor space. Review the "
                "product images and measured size before choosing a position.\n\n"
                "APPLICATION GUIDANCE\n"
                "Plan the position before application. Apply carefully to a "
                "clean, dry and smooth surface, then press from the centre toward "
                "the edges. Surface condition can affect the finished result.\n\n"
                "PACKAGE AND NOTES\n"
                "The package contains the selected wall decal design. Colours may "
                "look slightly different on different screens. No waterproof, "
                "removable or residue-free promise is made unless separately "
                "verified. Seller SKU: 0952."
            ),
            "candidates": candidates,
            "notes_zh": "仅使用已验证的 PVC 与视觉主题。",
        },
        ensure_ascii=False,
    )


def test_model_candidates_are_platform_specific_and_auditable():
    calls = []

    def model_call(messages, **kwargs):
        calls.append((messages, kwargs))
        return _model_payload()

    result = generate_title_candidates(_facts(), model_call=model_call)

    assert result["status"] == "draft_pending_kyle_review"
    assert result["provider"] == "toapi"
    assert result["model"] == TOAPI_TITLE_MODEL
    assert result["schema_version"] == "listing-copy-candidates-v4"
    assert result["generation_attempts"] == 1
    assert result["repair_performed"] is False
    assert len(result["shopee_description_en"]) >= 500
    assert result["input_signature"] == fact_signature(_facts())
    assert result["fact_snapshot"]["cost_cny"] == "9"
    assert result["fact_snapshot"]["selected_skus"] == [
        {"key": "30x90cm", "label": "30x90cm"}
    ]
    assert result["model_input_signature"] == model_input_signature(_facts())
    assert len(result["candidates"]) == len(EXPECTED_TARGETS)
    assert {(row["channel"], row["site"]) for row in result["candidates"]} == {
        (channel, site) for channel, site, _language, _limit in EXPECTED_TARGETS
    }
    assert result["marketplace_writes_performed"] == []
    assert calls and "source_title_zh" in calls[0][0][1]["content"]
    assert "not a literal translation task" in calls[0][0][0]["content"]
    assert "platform-native product titles" in calls[0][0][0]["content"]


def test_invalid_english_description_is_repaired_once():
    rejected = json.loads(_model_payload())
    rejected["shopee_description_en"] = (
        rejected["shopee_description_en"]
        + "\n中文说明：这行不应出现在英语描述中。"
    )
    calls = []

    def model_call(_messages, **_kwargs):
        calls.append(True)
        return json.dumps(
            rejected if len(calls) == 1 else json.loads(_model_payload()),
            ensure_ascii=False,
        )

    result = generate_title_candidates(_facts(), model_call=model_call)

    assert len(calls) == 2
    assert result["generation_attempts"] == 2
    assert result["repair_performed"] is True
    assert "博冉优品" not in result["shopee_description_en"]


def test_non_english_source_brand_line_is_omitted_from_english_description():
    payload = json.loads(_model_payload())
    payload["shopee_description_en"] += "\n- Brand: BRUP / 博冉优品"

    result = generate_title_candidates(
        _facts(),
        model_call=lambda *_args, **_kwargs: json.dumps(
            payload,
            ensure_ascii=False,
        ),
    )

    assert "博冉优品" not in result["shopee_description_en"]
    assert result["generation_attempts"] == 1


def test_model_prompt_excludes_chinese_and_english_brand_attributes():
    facts = {
        **_facts(),
        "verified_attributes": {
            "\u54c1\u724c": "SECRET-CHINESE-BRAND",
            "Brand Name": "SECRET-ENGLISH-BRAND",
            "\u6750\u8d28": "PVC",
        },
    }
    calls = []

    def model_call(messages, **_kwargs):
        calls.append(messages)
        return _model_payload()

    generate_title_candidates(facts, model_call=model_call)

    prompt = calls[0][1]["content"]
    assert "SECRET-CHINESE-BRAND" not in prompt
    assert "SECRET-ENGLISH-BRAND" not in prompt
    assert "PVC" in prompt


def test_missing_platform_candidate_is_rejected():
    payload = json.loads(_model_payload())
    payload["candidates"].pop()
    with pytest.raises(ValueError, match="omitted ozon:RU"):
        generate_title_candidates(
            _facts(),
            model_call=lambda *_args, **_kwargs: json.dumps(
                payload,
                ensure_ascii=False,
            ),
        )


def test_short_or_non_english_shopee_description_is_rejected():
    short = json.loads(_model_payload())
    short["shopee_description_en"] = "Too short."
    with pytest.raises(ValueError, match="too short"):
        generate_title_candidates(
            _facts(),
            model_call=lambda *_args, **_kwargs: json.dumps(
                short,
                ensure_ascii=False,
            ),
        )

    non_english = json.loads(_model_payload())
    non_english["shopee_description_en"] = "中文" + (
        non_english["shopee_description_en"]
    )
    with pytest.raises(ValueError, match="must be English"):
        generate_title_candidates(
            _facts(),
            model_call=lambda *_args, **_kwargs: json.dumps(
                non_english,
                ensure_ascii=False,
            ),
        )


def test_wrong_platform_language_is_rejected():
    payload = json.loads(_model_payload())
    thai = next(
        row
        for row in payload["candidates"]
        if row["channel"] == "tiktok" and row["site"] == "TH"
    )
    thai["title"] = "English title incorrectly returned for Thailand"
    with pytest.raises(ValueError, match="not Thai"):
        generate_title_candidates(
            _facts(),
            model_call=lambda *_args, **_kwargs: json.dumps(
                payload,
                ensure_ascii=False,
            ),
        )


def test_fact_signature_ignores_readback_enrichment_but_tracks_approved_facts():
    base = _facts()
    enriched = {
        **base,
        "verified_attributes": {
            **base["verified_attributes"],
            "\u54c1\u724c": "Miaoshou readback enrichment",
            "\u529f\u80fd": "Miaoshou readback enrichment",
        },
    }
    source_price_changed = {
        **base,
        "selected_skus": [
            {**base["selected_skus"][0], "price_cny": 999}
        ],
    }
    assert fact_signature(base) == fact_signature(enriched)
    assert fact_signature(base) == fact_signature(source_price_changed)
    assert model_input_signature(base) != model_input_signature(enriched)

    changes = [
        {**base, "source_title_zh": "\u4e0d\u540c\u6765\u6e90\u6807\u9898"},
        {**base, "cost_cny": 9.5},
        {**base, "weight_kg": 0.15},
        {**base, "package_cm": [31, 3, 3]},
        {
            **base,
            "selected_skus": [
                {"key": "another", "label": "Another specification"}
            ],
        },
    ]
    assert all(fact_signature(base) != fact_signature(changed) for changed in changes)
