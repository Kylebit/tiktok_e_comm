from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).with_name("prepare_product_publication.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("prepare_product_publication", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _preview(*, selected_sites: list[str] | None = None) -> dict:
    return {
        "ok": True,
        "offer_id": "3900000001",
        "revision": 12,
        "source": {
            "title_source": "Example product",
            "cost_cny": 8.5,
            "weight_kg": 0.2,
            "package_cm": [20, 10, 3],
            "seller_sku": "0970",
            "skus": [{"key": "source-a", "seller_sku": "0970"}],
            "images": [{"url": "https://images.example/source.jpg"}],
        },
        "review": {
            "selected_sites": selected_sites or ["lh_ph", "shopee_ph"],
            "title": "Reviewed product",
            "seller_sku": "0970",
            "cost_cny": 8.5,
            "weight_kg": 0.2,
            "package_cm": [20, 10, 3],
            "selected_sku_keys": ["source-a"],
            "sku_label_overrides": {"source-a": "20cm"},
            "fields_locked": True,
        },
        "pricing": {
            "sea": [
                {
                    "id": "lh_ph",
                    "region": "PH",
                    "currency": "PHP",
                    "list_price": 199,
                },
                {
                    "id": "shopee_ph",
                    "region": "PH",
                    "currency": "PHP",
                    "list_price": 189,
                },
            ]
        },
        "product_facts": {"ready": True, "blockers": []},
        "workflow": {"blockers": []},
        "content_package": {"generated_review_images": []},
    }


def test_first_round_is_always_zero_write_and_ready_for_human_review():
    module = _load_module()

    result = module.prepare_offer(
        offer_id="3900000001",
        requested_targets=["lh_ph", "shopee_ph"],
        preview_builder=lambda _offer: _preview(),
    )

    assert result["status"] == "FIRST_REVIEW_READY"
    assert result["miaoshou_sync"]["status"] == "DEFERRED_TO_SECOND_ROUND"
    assert result["external_write_count"] == 0
    assert result["request_attempted"] is False
    assert result["readback_verified"] is False


def test_legacy_skip_flag_is_a_zero_write_compatibility_alias():
    module = _load_module()

    result = module.prepare_offer(
        offer_id="3900000001",
        requested_targets=["lh_ph"],
        skip_miaoshou=True,
        preview_builder=lambda _offer: _preview(selected_sites=["lh_ph"]),
    )

    assert result["miaoshou_sync"]["status"] == "DEFERRED_TO_SECOND_ROUND"
    assert result["status"] == "FIRST_REVIEW_READY"
    assert result["external_write_count"] == 0


def test_first_round_rejects_any_miaoshou_execution_request():
    module = _load_module()

    with pytest.raises(module.PreparationError, match="second round"):
        module.prepare_offer(
            offer_id="3900000001",
            requested_targets=["lh_ph"],
            execute_miaoshou=True,
            confirm_miaoshou_write=True,
            preview_builder=lambda _offer: _preview(selected_sites=["lh_ph"]),
        )

def test_requested_target_missing_from_preview_is_decision_required():
    module = _load_module()

    result = module.prepare_offer(
        offer_id="3900000001",
        requested_targets=["lh_ph", "hb_th"],
        preview_builder=lambda _offer: _preview(selected_sites=["lh_ph"]),
    )

    assert result["status"] == "DECISION_REQUIRED"
    assert result["target_selection"]["missing_from_product_center"] == ["hb_th"]
    assert "requested targets are not selected in Product Center: hb_th" in result["blockers"]


def test_modern_release_dashboard_exact_target_labels_are_authoritative():
    module = _load_module()
    preview = _preview(selected_sites=[])
    preview.pop("review")
    preview["publication_scope"] = {
        "selected_labels": ["miaoshou:COMMON", "tiktok:LH_PH", "shopee:PH", "ozon:RU"]
    }
    preview["product"] = {
        "revision": 18,
        "title": "Modern product",
        "seller_sku_candidate": "0971",
        "category": {"semantic": "wallpaper"},
        "cost_cny": 9.2,
        "weight_kg": 0.3,
        "package_cm": [45, 7, 7],
        "source_skus": [{"key": "a", "model_sku": "0971", "label": "44cm*3m"}],
    }

    result = module.prepare_offer(
        offer_id="3900000001",
        requested_targets=["miaoshou:COMMON", "tiktok:LH_PH", "shopee:PH", "ozon:RU"],
        preview_builder=lambda _offer: preview,
    )

    assert result["target_selection"]["missing_from_product_center"] == []
    assert result["product_center_revision"] == 18
    assert result["product_facts"]["seller_sku"] == "0971"


def test_missing_workbench_state_bootstraps_offer_once_before_dashboard_retry():
    module = _load_module()
    dashboard_calls: list[str] = []
    bootstrap_calls: list[str] = []

    def dashboard(*, offer_id: str) -> dict:
        dashboard_calls.append(offer_id)
        if len(dashboard_calls) == 1:
            raise FileNotFoundError(f"required release evidence not found: {offer_id}.json")
        return {"ok": True, "product": {"offer_id": offer_id}}

    result = module._build_release_dashboard_with_bootstrap(
        "3882808027",
        dashboard_builder=dashboard,
        bootstrapper=lambda offer: bootstrap_calls.append(offer),
    )

    assert result == {"ok": True, "product": {"offer_id": "3882808027"}}
    assert dashboard_calls == ["3882808027", "3882808027"]
    assert bootstrap_calls == ["3882808027"]


def test_decision_packet_keeps_translation_and_dual_content_as_user_decisions():
    module = _load_module()

    result = module.prepare_offer(
        offer_id="3900000001",
        requested_targets=["lh_ph"],
        preview_builder=lambda _offer: _preview(selected_sites=["lh_ph"]),
    )

    assert result["image_decisions"]["translation_positions"] == []
    assert result["image_decisions"]["translation_status"] == "USER_DECISION_REQUIRED"
    assert result["content_groups"]["status"] == "USER_DECISION_REQUIRED"
    assert result["content_groups"]["groups"] == []


def test_explicit_image_execution_plan_is_validated_and_exposed_for_first_review():
    module = _load_module()
    plan = {
        "schema_version": "first-review-image-plan/v1",
        "status": "PROPOSED",
        "source_actions": [
            {
                "position": 6,
                "action": "TRANSLATE",
                "original_language": "en-master",
                "target_languages": ["ms-MY", "th-TH", "vi-VN", "es-MX", "ru-RU"],
                "output_count": 5,
                "reason": "Dimension copy must match each destination language.",
            }
        ],
        "generated_assets": [],
        "summary": {
            "translation_positions": [6],
            "localized_output_count": 5,
            "net_new_output_count": 0,
            "paid_generation_required": True,
        },
    }

    result = module.prepare_offer(
        offer_id="3900000001",
        requested_targets=["lh_ph"],
        image_execution_plan=plan,
        preview_builder=lambda _offer: _preview(selected_sites=["lh_ph"]),
    )

    assert result["image_execution_plan"] == plan
    assert result["image_decisions"]["translation_positions"] == [6]
    assert result["image_decisions"]["translation_status"] == "PROPOSED_FOR_USER_REVIEW"


def test_serialized_packet_has_no_provider_payload_or_secret_fields():
    module = _load_module()
    result = module.prepare_offer(
        offer_id="3900000001",
        requested_targets=["lh_ph"],
        preview_builder=lambda _offer: _preview(selected_sites=["lh_ph"]),
    )

    serialized = json.dumps(result, ensure_ascii=False).lower()
    assert "access_token" not in serialized
    assert "authorization" not in serialized
    assert "provider_payload" not in serialized
    assert "detail_id" not in serialized


def test_default_report_path_is_offer_scoped_runtime_state():
    module = _load_module()

    path = module._default_output_path("3900000001")

    assert path == (
        module.REPO_ROOT
        / "reports"
        / "product-preparation"
        / "3900000001"
        / "first-review.json"
    )
