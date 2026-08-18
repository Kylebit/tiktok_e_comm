from copy import deepcopy

import pytest

from modules.sourcing import new_product_workbench as workbench


SOURCE_A = "https://assets.example/source-a.jpg"
SOURCE_B = "https://assets.example/source-b.jpg"
SOURCE_C = "https://assets.example/source-c.jpg"


def _source():
    return {
        "images": [
            {"url": SOURCE_A, "kind": "main"},
            {"url": SOURCE_B, "kind": "main"},
            {"url": SOURCE_C, "kind": "detail"},
        ],
        "video": {"url": ""},
    }


def _install(monkeypatch, state):
    captured = {}
    monkeypatch.setattr(workbench, "resolve_offer_key", lambda _value: "offer-1")
    monkeypatch.setattr(workbench, "load_state", lambda _offer_id: state)
    monkeypatch.setattr(workbench, "_source_summary", lambda _offer_id: _source())

    def save_state(_offer_id, next_state):
        next_state["_revision"] = int(next_state.get("_revision") or 0) + 1
        captured["state"] = deepcopy(next_state)
        return next_state

    monkeypatch.setattr(workbench, "save_state", save_state)
    monkeypatch.setattr(
        workbench,
        "build_preview",
        lambda _offer_id: {
            "ok": True,
            "offer_id": "offer-1",
            "revision": captured["state"]["_revision"],
        },
    )
    return captured


def _review(*, revision=4, order=None, approve=False):
    payload = {
        "expected_revision": revision,
        "image_actions": [
            {"url": SOURCE_A, "action": "keep", "note": "hero"},
            {"url": SOURCE_B, "action": "remove", "note": ""},
            {"url": SOURCE_C, "action": "keep", "note": "detail"},
        ],
        "image_order": order or [SOURCE_C, SOURCE_A],
    }
    if approve:
        payload.update(
            {
                "confirm_final_content_approval": True,
                "approved_by": "Kyle",
            }
        )
    return payload


def test_source_only_review_saves_and_explicitly_approves_exact_content(monkeypatch):
    state = {
        "_revision": 4,
        "review": {},
        "content_package": {
            "content_strategy": "ai_assisted",
            "force_regenerate_all": True,
            "remaining_images_preflight": {"status": "ready"},
        },
    }
    captured = _install(monkeypatch, state)

    result = workbench.save_source_only_review(
        "offer-1", _review(approve=True)
    )

    saved = captured["state"]
    assert result["revision"] == 5
    assert saved["review"]["image_order"] == [SOURCE_C, SOURCE_A]
    assert [row["action"] for row in saved["review"]["image_actions"]] == [
        "keep",
        "remove",
        "keep",
    ]
    assert saved["content_package"]["content_strategy"] == "source_only"
    assert saved["content_package"]["source_only_review_status"] == "approved"
    assert saved["content_package"]["source_only_review_signature"].startswith(
        "sha256:"
    )
    assert saved["content_package"]["source_only_external_writes"] == []
    assert saved["content_package"]["fact_card_approved"] is True
    assert saved["content_package"]["planning_scope_approved"] is True
    approval = saved["content_package"]["source_only_final_approval"]
    assert approval["schema_version"] == "source-only-final-content-approval/v1"
    assert approval["approved_by"] == "Kyle"
    assert approval["approval_digest"].startswith("sha256:")
    assert workbench.source_only_final_approval_valid(
        saved["content_package"], saved["review"]
    )
    assert "remaining_images_preflight" not in saved["content_package"]
    assert "force_regenerate_all" not in saved["content_package"]


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda payload: payload.update(expected_revision=3),
            "stale",
        ),
        (
            lambda payload: payload["image_actions"].append(
                {
                    "url": "https://assets.example/not-this-offer.jpg",
                    "action": "keep",
                }
            ),
            "only reference",
        ),
        (
            lambda payload: payload.update(
                image_order=[SOURCE_A, SOURCE_A]
            ),
            "duplicate",
        ),
        (
            lambda payload: payload.update(image_order=[SOURCE_A]),
            "every kept image",
        ),
    ],
)
def test_source_only_review_rejects_stale_or_untrusted_payload(
    monkeypatch, mutator, message
):
    state = {
        "_revision": 4,
        "review": {},
        "content_package": {"content_strategy": "source_only"},
    }
    _install(monkeypatch, state)
    payload = _review()
    mutator(payload)

    with pytest.raises(ValueError, match=message):
        workbench.save_source_only_review("offer-1", payload)


def test_source_only_draft_save_invalidates_prior_final_approval(monkeypatch):
    state = {
        "_revision": 4,
        "review": {},
        "content_package": {"content_strategy": "source_only"},
    }
    captured = _install(monkeypatch, state)
    workbench.save_source_only_review("offer-1", _review(approve=True))
    approved = captured["state"]
    assert approved["content_package"]["source_only_final_approval"]

    state.clear()
    state.update(deepcopy(approved))
    workbench.save_source_only_review(
        "offer-1", _review(revision=5, order=[SOURCE_A, SOURCE_C])
    )

    draft = captured["state"]
    assert draft["content_package"]["fact_card_approved"] is False
    assert draft["content_package"]["planning_scope_approved"] is False
    assert (
        draft["content_package"]["source_only_review_status"]
        == "ready_for_final_approval"
    )
    assert "source_only_final_approval" not in draft["content_package"]
    assert not workbench.source_only_final_approval_valid(
        draft["content_package"], draft["review"]
    )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update(confirm_final_content_approval=1),
        lambda payload: payload.update(
            confirm_final_content_approval="true", approved_by="Kyle"
        ),
        lambda payload: payload.update(
            confirm_final_content_approval=True, approved_by="NotKyle"
        ),
        lambda payload: payload.update(approved_by="Kyle"),
        lambda payload: payload.update(expected_revision="4"),
        lambda payload: payload.update(expected_revision=4.0),
        lambda payload: payload.update(expected_revision=True),
    ],
)
def test_source_only_review_rejects_ambiguous_approval_or_revision(
    monkeypatch, mutator
):
    state = {
        "_revision": 4,
        "review": {},
        "content_package": {"content_strategy": "source_only"},
    }
    _install(monkeypatch, state)
    payload = _review()
    mutator(payload)

    with pytest.raises(ValueError):
        workbench.save_source_only_review("offer-1", payload)


def test_source_only_workflow_requires_explicit_final_approval():
    workflow = workbench._product_workflow_summary(
        source={"title_source": "Product", "cost_cny": 5, "images": [SOURCE_A]},
        review={
            "title": "English title",
            "cost_cny": 5,
            "image_actions": [{"action": "keep", "url": SOURCE_A}],
            "image_order": [SOURCE_A],
            "weight_kg": 0.1,
            "package_cm": [1, 2, 3],
            "selected_sites": ["TH"],
        },
        content={
            "content_strategy": "source_only",
            "package_found": False,
            "fact_card_approved": False,
            "planning_scope_approved": False,
        },
        miaoshou_draft={},
        tiktok_claim={},
        site_drafts={},
    )

    assert workflow["content_ready"] is False
    assert workflow["image_review_ready"] is True
    assert any(
        "保存并批准最终内容" in blocker
        for blocker in workflow["blockers"]
    )


def test_source_only_workflow_accepts_current_explicit_approval(monkeypatch):
    state = {
        "_revision": 4,
        "review": {},
        "content_package": {"content_strategy": "source_only"},
    }
    captured = _install(monkeypatch, state)
    workbench.save_source_only_review("offer-1", _review(approve=True))
    saved = captured["state"]

    workflow = workbench._product_workflow_summary(
        source={"title_source": "Product", "cost_cny": 5, "images": [SOURCE_A]},
        review={
            **saved["review"],
            "title": "English title",
            "cost_cny": 5,
            "weight_kg": 0.1,
            "package_cm": [1, 2, 3],
            "selected_sites": ["TH"],
        },
        content=saved["content_package"],
        miaoshou_draft={},
        tiktok_claim={},
        site_drafts={},
    )

    assert workflow["content_ready"] is True
    assert workflow["image_review_ready"] is True
