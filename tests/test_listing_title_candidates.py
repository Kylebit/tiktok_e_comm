from __future__ import annotations

import json

import pytest

from domains.content_operations.listing_title_candidates import (
    EXPECTED_TARGETS,
    fact_signature,
    generate_title_candidates,
)


def _facts() -> dict:
    return {
        "offer_id": "3828540231",
        "source_title_zh": "水彩复古花卉蝴蝶墙贴",
        "category": {"name": "墙贴"},
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
            "candidates": candidates,
            "notes_zh": "仅使用已验证的 PVC 与视觉主题。",
        },
        ensure_ascii=False,
    )


def test_model_candidates_are_platform_specific_and_auditable(monkeypatch):
    monkeypatch.setattr(
        "domains.content_operations.listing_title_candidates.ai_config",
        lambda: {"model": "test-model"},
    )
    calls = []

    def model_call(messages, **kwargs):
        calls.append((messages, kwargs))
        return _model_payload()

    result = generate_title_candidates(_facts(), model_call=model_call)

    assert result["status"] == "draft_pending_kyle_review"
    assert result["model"] == "test-model"
    assert result["input_signature"] == fact_signature(_facts())
    assert len(result["candidates"]) == len(EXPECTED_TARGETS)
    assert {(row["channel"], row["site"]) for row in result["candidates"]} == {
        (channel, site) for channel, site, _language, _limit in EXPECTED_TARGETS
    }
    assert result["marketplace_writes_performed"] == []
    assert calls and "source_title_zh" in calls[0][0][1]["content"]


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


def test_fact_signature_ignores_price_but_changes_with_visual_facts():
    base = _facts()
    with_price = {**base, "cost_cny": 9}
    changed = {**base, "package_cm": [31, 3, 3]}
    assert fact_signature(base) == fact_signature(with_price)
    assert fact_signature(base) != fact_signature(changed)
