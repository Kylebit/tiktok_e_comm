from __future__ import annotations

import pytest

from domains.content_operations.tiktok_keyword_evidence import (
    DEFAULT_PENDING_CLAIMS,
    build_prelisting_diagnosis_request,
    build_review_workflow,
    inherit_livelyhive_sea_evidence,
    parse_diagnosis_response,
    verified_keywords_for_title,
    review_candidate_keywords_for_title,
)


def test_prelisting_request_uses_official_title_diagnosis_without_product_id():
    request = build_prelisting_diagnosis_request(
        target_label="tiktok:LH_PH",
        shop_cipher="cipher-ph",
        category_id="600338",
        title="PVC Floral Bird Wallpaper Roll",
    )

    assert request["method"] == "POST"
    assert request["path"] == "/product/202411/products/diagnose_optimize"
    assert request["query"] == {"shop_cipher": "cipher-ph"}
    assert request["body"] == {
        "category_id": "600338",
        "title": "PVC Floral Bird Wallpaper Roll",
        "optimization_fields": ["TITLE"],
    }
    assert "product_id" not in request["body"]
    assert request["required_scope"] == "serller.product.optimize"


def test_official_seo_words_remain_keyword_evidence_not_product_facts():
    evidence = parse_diagnosis_response(
        {
            "code": 0,
            "request_id": "request-1",
            "data": {
                "diagnoses": [
                    {
                        "field": "TITLE",
                        "suggestion": {
                            "seo_words": [
                                {"text": "Peel and Stick Wallpaper"},
                                {"text": "Waterproof Wallpaper"},
                                {"text": "Peel and Stick Wallpaper"},
                            ]
                        },
                    }
                ]
            },
        },
        target_label="tiktok:LH_PH",
        category_id="600338",
        observed_at="2026-08-21T00:00:00+00:00",
    )

    assert evidence["status"] == "OFFICIAL_READ_COMPLETED"
    assert [row["text"] for row in evidence["keywords"]] == [
        "Peel and Stick Wallpaper",
        "Waterproof Wallpaper",
    ]
    assert all(row["product_fact_status"] == "UNVERIFIED" for row in evidence["keywords"])
    assert evidence["search_volume_available"] is False
    assert evidence["sales_volume_available"] is False
    assert "request-1" not in str(evidence)


def test_only_user_selected_fact_verified_keywords_reach_lingshi():
    rows = [
        {"text": "Floral Wallpaper", "selected": True, "fact_verified": True},
        {"text": "Waterproof", "selected": True, "fact_verified": False},
        {"text": "Removable", "selected": False, "fact_verified": True},
    ]

    assert verified_keywords_for_title(rows) == ["Floral Wallpaper"]


def test_review_candidates_are_bounded_and_deduplicate_equivalent_claims():
    rows = [
        {"text": "Self-Adhesive", "selected": True, "fact_verified": False},
        {"text": "Peel and Stick", "selected": True, "fact_verified": False},
        {"text": "Waterproof", "selected": True, "fact_verified": False},
        {"text": "Removable", "selected": True, "fact_verified": False},
        {"text": "Washable", "selected": True, "fact_verified": False},
    ]

    assert review_candidate_keywords_for_title(rows) == [
        "Self-Adhesive",
        "Waterproof",
        "Removable",
    ]


def test_workflow_routes_api_less_targets_to_livelyhive_sea_evidence():
    workflow = build_review_workflow(
        ["tiktok:LH_PH", "tiktok:HB_PH", "shopee:PH"],
    )

    assert workflow["schema_version"] == "tiktok-keyword-review-workflow/v1"
    assert workflow["targets"] == [
        {
            "target_label": "tiktok:LH_PH",
            "status": "READY_FOR_EXPLICIT_OFFICIAL_READ",
            "reason_code": "prelisting_diagnosis_supported",
        },
        {
            "target_label": "tiktok:HB_PH",
            "status": "FOLLOW_LIVELYHIVE_SEA_EVIDENCE",
            "reason_code": "user_directed_livelyhive_sea_inheritance",
        },
    ]
    assert [row["text"] for row in workflow["pending_claims"]] == list(
        DEFAULT_PENDING_CLAIMS
    )
    assert all(row["status"] == "PENDING_FACT_REVIEW" for row in workflow["pending_claims"])


def test_inheritance_uses_same_country_for_homebloom_and_sea_union_for_gb():
    direct = [
        {
            "target_label": "tiktok:LH_PH",
            "status": "OFFICIAL_READ_COMPLETED",
            "keywords": [{"text": "PH wallpaper"}],
        },
        {
            "target_label": "tiktok:LH_MY",
            "status": "OFFICIAL_READ_COMPLETED",
            "keywords": [{"text": "MY wallpaper"}, {"text": "PH wallpaper"}],
        },
    ]

    inherited = inherit_livelyhive_sea_evidence(
        direct,
        ["tiktok:HB_PH", "tiktok:GB"],
    )

    assert inherited[0]["source_target_labels"] == ["tiktok:LH_PH"]
    assert [row["text"] for row in inherited[0]["keywords"]] == ["PH wallpaper"]
    assert inherited[1]["source_target_labels"] == ["tiktok:LH_PH", "tiktok:LH_MY"]
    assert [row["text"] for row in inherited[1]["keywords"]] == [
        "PH wallpaper",
        "MY wallpaper",
    ]
    assert all(
        row["source_status"] == "INHERITED_LIVELYHIVE_SEA_OFFICIAL_RECOMMENDATION"
        for item in inherited
        for row in item["keywords"]
    )


@pytest.mark.parametrize("field", ["shop_cipher", "category_id", "title"])
def test_prelisting_request_rejects_missing_identity(field):
    values = {
        "target_label": "tiktok:LH_PH",
        "shop_cipher": "cipher-ph",
        "category_id": "600338",
        "title": "PVC Floral Bird Wallpaper Roll",
    }
    values[field] = ""
    with pytest.raises(ValueError):
        build_prelisting_diagnosis_request(**values)
