from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

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


def test_source_decisions_and_identity_references_save_atomically_with_revision(
    tmp_path, monkeypatch
):
    first = "https://assets.example/source.jpg"
    second = "https://assets.example/alternate.jpg"
    state = {
        "offer_id": "3828540231",
        "_revision": 8,
        "review": {"image_actions": []},
        "content_package": {
            "content_strategy": "ai_assisted",
            "collect_box_id": "3828540231",
            "fact_card_approved": True,
            "planning_scope_approved": True,
        },
    }
    package = _review_package(state["content_package"])
    package["collect_box"]["image_urls"] = [first, second]
    (tmp_path / "review_package.json").write_text(json.dumps(package), encoding="utf-8")
    saves = []
    monkeypatch.setattr(workbench, "resolve_offer_key", lambda _value: "3828540231")
    monkeypatch.setattr(workbench, "load_state", lambda _offer: state)
    monkeypatch.setattr(workbench, "_content_package_dir", lambda _id: tmp_path)
    monkeypatch.setattr(workbench, "_source_summary", lambda _offer: {"images": [
        {"url": first, "kind": "main"}, {"url": second, "kind": "detail"},
    ]})
    monkeypatch.setattr(workbench, "save_state", lambda _offer, value: saves.append(copy.deepcopy(value)) or value)
    monkeypatch.setattr(workbench, "content_package_summary", lambda _offer: {"ok": True})

    workbench.save_content_package_review("3828540231", {
        "expected_revision": 8,
        "identity_reference_urls": [first, second],
        "primary_identity_url": second,
        "image_actions": [
            {"url": first, "action": "keep"}, {"url": second, "action": "keep"},
        ],
        "image_order": [second, first],
    })

    assert state["content_package"]["identity_reference_urls"] == [first, second]
    assert state["content_package"]["primary_identity_url"] == second
    assert state["review"]["image_order"] == [second, first]
    assert len(saves) == 1
    with pytest.raises(ValueError, match="stale"):
        workbench.save_content_package_review("3828540231", {"expected_revision": 7})

    invalid_reviews = [
        {"expected_revision": 8.9},
        {"expected_revision": "8"},
        {"expected_revision": True},
        {"expected_revision": 8, "image_actions": [
            {"url": first, "action": "keep"}, {"url": first, "action": "remove"},
        ]},
        {"expected_revision": 8, "identity_reference_urls": [first],
         "primary_identity_url": first, "image_actions": [
             {"url": first, "action": "remove"}, {"url": second, "action": "keep"},
         ]},
        {"expected_revision": 8, "identity_reference_urls": [first],
         "primary_identity_url": second, "image_actions": [
             {"url": first, "action": "keep"}, {"url": second, "action": "keep"},
         ]},
    ]
    for invalid in invalid_reviews:
        before_saves = len(saves)
        with pytest.raises(ValueError):
            workbench.save_content_package_review("3828540231", invalid)
        assert len(saves) == before_saves


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


def _legacy_migration_fixture(tmp_path: Path, monkeypatch):
    first = "https://assets.example/source.jpg"
    second = "https://assets.example/alternate.jpg"
    content = {
        "content_strategy": "ai_assisted",
        "collect_box_id": "3828540231",
        "fact_card_approved": True,
        "planning_scope_approved": True,
        "suite_approved": False,
        "identity_reference_urls": [first],
        "primary_identity_url": first,
        "suite_customization": {
            "type_counts": {"scene": 1},
            "size_card": {
                "enabled": False,
                "dimensions": "",
                "confirmed": False,
            },
        },
    }
    package = {
        "collect_box": {
            "source_title": "Watercolour floral wall decal",
            "primary_identity_image": first,
            "image_urls": [first, second],
        },
        "fact_card": {
            "verified": [{"field": "material", "value": "PVC"}],
        },
        "model_proposal": {
            "planning_source": "ai",
            "planning_signature": workbench._planning_recipe_signature(content),
            "model": "legacy-planner",
        },
        "plan": {
            "analysis": {
                "subject": "Watercolour floral wall decal",
                "category": "wall decal",
                "style_lock": "Preserve the exact printed pattern.",
            },
            "_meta": {"category_profile": "wall_decal"},
            "suite": {
                "items": [
                    {
                        "id": "sc1",
                        "type": "scene",
                        "title": "Living Room Application",
                        "focus": "Show the exact decal on a living-room wall.",
                        "selected": True,
                    }
                ]
            },
        },
    }
    package_path = tmp_path / "review_package.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    state = {
        "offer_id": "3828540231",
        "_revision": 13,
        "content_package": copy.deepcopy(content),
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
        lambda _collect_box: tmp_path,
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
    return state, package_path, saved_states, first, second


@pytest.mark.parametrize(
    "review_update",
    [
        {
            "suite_customization": {
                "type_counts": {"scene": 2},
                "size_card": {
                    "enabled": False,
                    "dimensions": "",
                    "confirmed": False,
                },
            }
        },
        {
            "identity_reference_urls": ["https://assets.example/alternate.jpg"],
            "primary_identity_url": "https://assets.example/alternate.jpg",
        },
        {"fact_card_approved": False},
    ],
    ids=("recipe_changed", "reference_changed", "fact_changed"),
)
def test_legacy_proposal_migration_rejects_recipe_reference_or_fact_drift(
    tmp_path,
    monkeypatch,
    review_update,
):
    state, _package_path, _saved, _first, _second = _legacy_migration_fixture(
        tmp_path,
        monkeypatch,
    )

    workbench.save_content_package_review("3828540231", review_update)

    content = state["content_package"]
    assert content["planning_review_mode"] == EXPERIENCE_RECIPE_REVIEW_MODE
    assert content["suite_approved"] is False
    assert content["storyboard_reviews"] == {}
    assert "storyboard_recipe_signature" not in content


def test_matching_legacy_proposal_migrates_then_builds_local_suite_preflight_only(
    tmp_path,
    monkeypatch,
):
    state, package_path, saved, _first, _second = _legacy_migration_fixture(
        tmp_path,
        monkeypatch,
    )
    before_package = package_path.read_bytes()

    workbench.save_content_package_review("3828540231", {})

    content = state["content_package"]
    assert content["planning_review_mode"] == EXPERIENCE_RECIPE_REVIEW_MODE
    assert content["suite_approved"] is True
    assert content["storyboard_reviews"]["sc1"]["decision"] == "auto_adopted"

    thread_calls: list[tuple] = []
    subprocess_calls: list[tuple] = []
    monkeypatch.setattr(
        workbench.threading,
        "Thread",
        lambda *args, **kwargs: thread_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        workbench.subprocess,
        "run",
        lambda *args, **kwargs: subprocess_calls.append((args, kwargs)),
    )
    from modules.sourcing import toapis_client

    payload_calls: list[dict] = []
    monkeypatch.setattr(
        toapis_client,
        "build_generation_payload",
        lambda **kwargs: payload_calls.append(dict(kwargs))
        or {
            "model": kwargs["model"],
            "prompt": kwargs["prompt"],
            "reference_images": kwargs["reference_images"],
        },
    )

    result = workbench.prepare_suite_image_generations("3828540231")

    assert result["ok"] is True
    assert result["preflight"]["status"] == "ready_for_explicit_paid_confirmation"
    assert [row["id"] for row in result["preflight"]["shots"]] == ["sc1"]
    assert len(payload_calls) == 1
    assert "remaining_images_preflight" in content
    assert "remaining_images_generation" not in content
    assert thread_calls == []
    assert subprocess_calls == []
    assert package_path.read_bytes() == before_package
    assert len(saved) == 2
