from __future__ import annotations

import copy
import json
from pathlib import Path

from domains.content_operations.content_package_adapter import (
    EXPERIENCE_RECIPE_REVIEW_MODE,
    build_workbench_content_package_handoff,
)
from modules.sourcing import new_product_workbench as workbench


def _audit(url: str) -> dict:
    return {
        "shot_id": "sc1",
        "audit_id": "audit:sc1_r1",
        "download_verified": True,
        "final_response": {"result": {"data": [{"url": url}]}},
    }


def _review_package(content: dict) -> dict:
    return {
        "collect_box": {
            "image_urls": ["https://assets.example/source.jpg"],
        },
        "model_proposal": {
            "planning_source": "ai",
            "planning_signature": workbench._planning_recipe_signature(content),
        },
        "plan": {
            "suite": {
                "items": [
                    {"id": "sc1", "type": "scene", "selected": True},
                ]
            }
        },
    }


def test_saving_ai_content_adopts_current_storyboard_without_per_shot_review(
    tmp_path,
    monkeypatch,
):
    content = {
        "content_strategy": "ai_assisted",
        "collect_box_id": "3828540231",
        "fact_card_approved": True,
        "planning_scope_approved": False,
        "suite_approved": False,
        "identity_reference_urls": ["https://assets.example/source.jpg"],
        "primary_identity_url": "https://assets.example/source.jpg",
    }
    workbench._enable_experience_recipe_review(content)
    package = _review_package(content)
    (tmp_path / "review_package.json").write_text(
        json.dumps(package),
        encoding="utf-8",
    )
    state = {
        "offer_id": "3828540231",
        "_revision": 3,
        "content_package": {
            **content,
            "planning_scope_approved": False,
            "suite_approved": False,
            "planning_review_mode": "",
        },
    }
    saved_states: list[dict] = []

    monkeypatch.setattr(
        workbench,
        "resolve_offer_key",
        lambda _value: "3828540231",
    )
    monkeypatch.setattr(workbench, "load_state", lambda _offer: state)
    monkeypatch.setattr(
        workbench,
        "_content_package_dir",
        lambda _collect_box: Path(tmp_path),
    )
    monkeypatch.setattr(
        workbench,
        "save_state",
        lambda _offer, value: saved_states.append(copy.deepcopy(value)) or value,
    )
    monkeypatch.setattr(
        workbench,
        "content_package_summary",
        lambda _offer: {"ok": True},
    )

    workbench.save_content_package_review(
        "3828540231",
        {
            "planning_scope_approved": True,
            "storyboard_reviews": {
                "sc1": {"decision": "pending", "note": "legacy UI value"},
            }
        },
    )

    adopted = state["content_package"]
    assert adopted["planning_review_mode"] == EXPERIENCE_RECIPE_REVIEW_MODE
    assert adopted["planning_scope_approved"] is True
    assert adopted["suite_approved"] is True
    assert adopted["storyboard_reviews"]["sc1"]["decision"] == "auto_adopted"
    assert adopted.get("asset_decisions") in (None, {})
    assert len(saved_states) == 1


def test_auto_adopted_storyboard_does_not_approve_generated_image():
    image_url = "https://assets.example/generated.png"
    state = {
        "content_package": {
            "content_strategy": "ai_assisted",
            "planning_review_mode": EXPERIENCE_RECIPE_REVIEW_MODE,
            "fact_card_approved": True,
            "planning_scope_approved": True,
            "suite_approved": True,
            "storyboard_reviews": {
                "sc1": {"decision": "auto_adopted"},
            },
        },
        "review": {
            "image_actions": [],
            "image_order": [image_url],
        },
    }
    suite = {"suite": {"items": [{"id": "sc1", "selected": True}]}}

    pending = build_workbench_content_package_handoff(
        product_id="3828540231",
        state=state,
        suite_plan=suite,
        generation_audits={"sc1_r1": _audit(image_url)},
        copy={"en": "Source-supported product copy"},
    )

    assert pending.content_package.approval.status == "pending"
    assert pending.missing_shot_ids == ("sc1",)
    assert "lacks final content approval" in " ".join(pending.blockers)

    approved_state = copy.deepcopy(state)
    approved_state["content_package"]["asset_decisions"] = {
        "sc1_r1": {"decision": "approved"},
    }
    approved = build_workbench_content_package_handoff(
        product_id="3828540231",
        state=approved_state,
        suite_plan=suite,
        generation_audits={"sc1_r1": _audit(image_url)},
        copy={"en": "Source-supported product copy"},
    )

    assert approved.content_package.approval.status == "approved"
    assert approved.content_package.image_urls == (image_url,)
    assert approved.asset_lineage[0].decision_source == "asset_decisions.approved"
