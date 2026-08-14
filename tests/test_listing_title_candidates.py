from __future__ import annotations

import json

import pytest

from domains.content_operations.listing_title_candidates import (
    EXPECTED_TARGETS,
    TOAPI_TITLE_MODEL,
    _deterministic_shopee_description,
    _deterministic_shopee_title,
    _validate_shopee_master_coverage,
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
    assert result["schema_version"] == "listing-copy-candidates-v8"
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


def test_second_non_english_description_uses_fact_bound_english_fallback():
    rejected = json.loads(_model_payload())
    rejected["shopee_description_en"] = (
        rejected["shopee_description_en"] + "\n中文内容不允许进入全球描述"
    )
    calls = []

    def model_call(_messages, **_kwargs):
        calls.append(True)
        return json.dumps(rejected, ensure_ascii=False)

    result = generate_title_candidates(_facts(), model_call=model_call)

    assert len(calls) == 2
    assert result["generation_attempts"] == 2
    assert result["repair_performed"] is True
    assert result["description_fallback_used"] is True
    assert len(result["shopee_description_en"]) >= 500
    assert "中文内容" not in result["shopee_description_en"]
    assert result["shopee_description_en"].startswith("PRODUCT OVERVIEW")
    assert len(result["candidates"]) == len(EXPECTED_TARGETS)


def test_second_response_can_repair_title_and_description_independently():
    rejected = json.loads(_model_payload())
    rejected["semantic_master_en"] = (
        "Large PVC Butterfly Floral Landscape Background Wall Sticker "
        "for Living Room and Bedroom, 1 Piece"
    )
    rejected["shopee_description_en"] += "\n中文内容不允许进入全球描述"
    shopee = next(
        row
        for row in rejected["candidates"]
        if row["channel"] == "shopee" and row["site"] == "CNSC"
    )
    shopee["title"] = "Large PVC Floral Wall Sticker"

    result = generate_title_candidates(
        _facts(),
        model_call=lambda *_args, **_kwargs: json.dumps(
            rejected,
            ensure_ascii=False,
        ),
    )
    safe_title = next(
        row["title"]
        for row in result["candidates"]
        if row["channel"] == "shopee" and row["site"] == "CNSC"
    )

    assert result["title_fallback_used"] is True
    assert result["description_fallback_used"] is True
    assert "Butterfly" in safe_title
    assert "Background" in safe_title
    assert "中文" not in result["shopee_description_en"]


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


def test_shopee_master_description_contains_only_product_information():
    payload = json.loads(_model_payload())
    payload["shopee_description_en"] += (
        "\nOrigin: Zhejiang, China"
        "\nPayment support: credit card, bank transfer and cash on delivery"
        "\nShipping service: contact the seller for delivery options"
        "\nAfter-sales service: refunds are handled by the storefront"
    )

    result = generate_title_candidates(
        _facts(),
        model_call=lambda *_args, **_kwargs: json.dumps(
            payload,
            ensure_ascii=False,
        ),
    )
    description = result["shopee_description_en"].casefold()

    for forbidden in (
        "origin:",
        "payment support",
        "bank transfer",
        "cash on delivery",
        "shipping service",
        "delivery options",
        "after-sales service",
        "refunds",
        "storefront",
    ):
        assert forbidden not in description


def test_deterministic_shopee_description_is_product_only():
    description = _deterministic_shopee_description(
        _facts(),
        semantic_master_en="Watercolour Floral Butterfly PVC Wall Decal",
    ).casefold()

    for forbidden in (
        "origin",
        "payment",
        "shipping",
        "delivery",
        "storefront",
        "seller",
        "refund",
        "approved",
        "verified",
        "global listing",
    ):
        assert forbidden not in description


def test_shopee_master_description_omits_all_internal_identity_and_brand_lines():
    payload = json.loads(_model_payload())
    payload["shopee_description_en"] += (
        "\nBrand: BRUP"
        "\nSeller SKU: 0952"
        "\nItem code: JD5047"
        "\nProduct ID: 3828540231"
        "\nOffer ID: 3828540231"
    )

    result = generate_title_candidates(
        _facts(),
        model_call=lambda *_args, **_kwargs: json.dumps(
            payload,
            ensure_ascii=False,
        ),
    )
    description = result["shopee_description_en"].casefold()

    for forbidden in (
        "brup",
        "0952",
        "jd5047",
        "3828540231",
        "seller sku",
        "item code",
        "product id",
        "offer id",
        "brand:",
    ):
        assert forbidden not in description


def test_model_prompt_excludes_chinese_and_english_brand_attributes():
    facts = {
        **_facts(),
        "verified_attributes": {
            "\u54c1\u724c": "SECRET-CHINESE-BRAND",
            "Brand Name": "SECRET-ENGLISH-BRAND",
            "Origin": "SECRET-ORIGIN",
            "Payment support": "SECRET-PAYMENT",
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
    assert "SECRET-ORIGIN" not in prompt
    assert "SECRET-PAYMENT" not in prompt
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
    repaired = generate_title_candidates(
        _facts(),
        model_call=lambda *_args, **_kwargs: json.dumps(
            non_english,
            ensure_ascii=False,
        )
    )
    assert repaired["description_fallback_used"] is True
    assert "中文" not in repaired["shopee_description_en"]


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


def test_shopee_candidate_dropping_window_identity_uses_safe_fallback():
    payload = json.loads(_model_payload())
    payload["semantic_master_en"] = (
        "Cute Cat PVC Static Cling Window Sticker Wall Decal, "
        "Flat Decorative Wall Sticker for Bedroom and Living Room, 50 x 43 cm"
    )
    shopee = next(
        row
        for row in payload["candidates"]
        if row["channel"] == "shopee" and row["site"] == "CNSC"
    )
    shopee["title"] = "Cute Cat PVC Static Cling Wall Sticker 50 x 43 cm"

    result = generate_title_candidates(
        _facts(),
        model_call=lambda *_args, **_kwargs: json.dumps(
            payload,
            ensure_ascii=False,
        ),
    )
    safe_title = next(
        row["title"]
        for row in result["candidates"]
        if row["channel"] == "shopee" and row["site"] == "CNSC"
    )

    assert result["generation_attempts"] == 2
    assert result["title_fallback_used"] is True
    assert "Window Sticker Wall Decal" in safe_title
    assert len(safe_title) <= 120


def test_long_shopee_identity_fallback_never_truncates_tail_identity_term():
    master = (
        "Large Premium Decorative PVC Floral Butterfly Wall Sticker Mural "
        "for Bedroom Living Room Rental Home Interior Background, "
        "Flat Wall Decal with Botanical Design"
    )

    safe_title = _deterministic_shopee_title(master)

    assert len(safe_title) <= 120
    assert "Background" in safe_title
    _validate_shopee_master_coverage(master, safe_title)


def test_shopee_fallback_keeps_natural_faux_window_product_phrase():
    master = (
        "Large PVC floral landscape background wall sticker, butterfly faux "
        "window wall decal, flat wall decor for living room and bedroom"
    )

    safe_title = _deterministic_shopee_title(master)

    assert len(safe_title) <= 120
    assert "butterfly faux window wall decal" in safe_title
    assert not safe_title.endswith((" and", " for"))
    _validate_shopee_master_coverage(master, safe_title)


def test_variant_label_noise_cannot_become_semantic_product_master():
    payload = json.loads(_model_payload())
    payload["semantic_master_en"] = (
        "Large PVC Floral Butterfly Wall Sticker, Picture Color"
    )

    with pytest.raises(
        ValueError,
        match="variant label instead of product identity",
    ):
        generate_title_candidates(
            _facts(),
            model_call=lambda *_args, **_kwargs: json.dumps(
                payload,
                ensure_ascii=False,
            ),
        )


def test_fact_signature_ignores_non_copy_facts_but_tracks_copy_identity():
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

    for changed in (
        {**base, "cost_cny": 9.5},
        {**base, "weight_kg": 0.15},
        {**base, "package_cm": [31, 3, 3]},
    ):
        assert fact_signature(base) == fact_signature(changed)

    changes = [
        {**base, "source_title_zh": "\u4e0d\u540c\u6765\u6e90\u6807\u9898"},
        {**base, "category": {"main": "\u4e0d\u540c\u7c7b\u76ee"}},
        {
            **base,
            "selected_skus": [
                {"key": "another", "label": "Another specification"}
            ],
        },
    ]
    assert all(fact_signature(base) != fact_signature(changed) for changed in changes)
