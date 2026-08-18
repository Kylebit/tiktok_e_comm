from copy import deepcopy

import pytest

from modules.sourcing import new_product_workbench as workbench


def _source(*, conflicting=False):
    skus = []
    if conflicting:
        skus = [
            {"key": "normal", "name": "Normal", "price": "8.1"},
            {
                "key": "custom",
                "name": "需要其他材质咨询客服定制",
                "price": "0.2",
            },
        ]
    return {
        "title_source": "PVC wall decal",
        "cost_cny": 8.1,
        "weight_kg": 0.2,
        "package_cm": [20, 20, 3],
        "category": {"name": "Wall decals"},
        "video": {"action": "none", "url": ""},
        "images": [],
        "skus": skus,
    }


def _install_isolated_workbench(monkeypatch, state, *, source=None):
    captured = {}
    monkeypatch.setattr(workbench, "resolve_offer_key", lambda _value: "product-1")
    monkeypatch.setattr(workbench, "load_state", lambda _offer_id: state)
    monkeypatch.setattr(
        workbench,
        "_source_summary",
        lambda _offer_id: deepcopy(source or _source()),
    )
    monkeypatch.setattr(
        workbench,
        "_generated_review_images",
        lambda _offer_id, _content, _package_dir: [],
    )
    monkeypatch.setattr(workbench, "content_package_summary", lambda _offer_id: {})
    monkeypatch.setattr(
        workbench,
        "_product_workflow_summary",
        lambda **_kwargs: {
            "content_ready": True,
            "generation_ready": True,
            "image_review_ready": True,
            "current_stage": "commercial",
            "blockers": [],
        },
    )

    def save_state(_offer_id, next_state):
        captured["state"] = deepcopy(next_state)
        return next_state

    monkeypatch.setattr(workbench, "save_state", save_state)
    monkeypatch.setattr(
        workbench,
        "build_preview",
        lambda _offer_id: {"ok": True, "offer_id": "product-1"},
    )
    return captured


def _locked_state():
    return {
        "_revision": 7,
        "review": {
            "title": "Approved title",
            "cost_cny": 8.1,
            "fields_locked": True,
        },
        "product_approval": {
            "approval_id": "approval-1",
            "status": "approved",
        },
    }


def test_unlocked_legacy_review_remains_compatible(monkeypatch):
    state = {
        "_revision": 2,
        "review": {"title": "Draft", "fields_locked": False},
    }
    captured = _install_isolated_workbench(monkeypatch, state)

    result = workbench.save_review(
        "product-1",
        {"title": "Revised draft", "cost_cny": 8.1},
    )

    assert result["ok"] is True
    assert captured["state"]["review"]["title"] == "Revised draft"
    assert captured["state"]["review"]["fields_locked"] is False


@pytest.mark.parametrize(
    "update",
    [
        {"cost_cny": 9.2},
        {"fields_locked": False},
    ],
)
def test_locked_review_rejects_commercial_change_without_explicit_supersede(
    monkeypatch, update
):
    state = _locked_state()
    captured = _install_isolated_workbench(monkeypatch, state)

    with pytest.raises(ValueError, match="require supersede=true"):
        workbench.save_review("product-1", update)

    assert "state" not in captured
    assert state["product_approval"]["status"] == "approved"


def test_active_product_approval_prevents_false_unlock_even_if_legacy_flag_is_false(
    monkeypatch,
):
    state = _locked_state()
    state["review"]["fields_locked"] = False
    captured = _install_isolated_workbench(monkeypatch, state)

    with pytest.raises(ValueError, match="require supersede=true"):
        workbench.save_review("product-1", {"fields_locked": False})

    assert "state" not in captured
    assert state["product_approval"]["status"] == "approved"


def test_locked_review_still_allows_content_review_updates(monkeypatch):
    state = _locked_state()
    captured = _install_isolated_workbench(monkeypatch, state)

    workbench.save_review(
        "product-1",
        {
            "image_actions": [
                {
                    "action": "keep",
                    "url": "https://assets.example/source.jpg",
                }
            ],
            "video_action": "keep",
            "video_url": "https://assets.example/product.mp4",
        },
    )

    saved = captured["state"]
    assert saved["review"]["fields_locked"] is True
    assert saved["review"]["image_order"] == [
        "https://assets.example/source.jpg"
    ]
    assert saved["review"]["video_url"] == "https://assets.example/product.mp4"
    assert saved["product_approval"]["status"] == "approved"


def test_explicit_supersede_requires_matching_revision_and_writes_audit(monkeypatch):
    state = _locked_state()
    captured = _install_isolated_workbench(monkeypatch, state)

    workbench.save_review(
        "product-1",
        {
            "cost_cny": 9.2,
            "supersede": True,
            "expected_revision": 7,
            "supersede_reason": "supplier price changed",
        },
    )

    saved = captured["state"]
    assert saved["review"]["cost_cny"] == 9.2
    assert saved["review"]["fields_locked"] is False
    assert "supersede" not in saved["review"]
    assert "expected_revision" not in saved["review"]
    assert saved["product_approval"]["status"] == "superseded"
    assert saved["product_approval"]["superseded_fields"] == ["cost_cny"]
    event = saved["commercial_supersessions"][-1]
    assert event["expected_revision"] == 7
    assert event["prior_approval_id"] == "approval-1"
    assert event["reason"] == "supplier price changed"


@pytest.mark.parametrize("expected_revision", [None, 6, True])
def test_explicit_supersede_rejects_missing_or_stale_revision(
    monkeypatch, expected_revision
):
    state = _locked_state()
    captured = _install_isolated_workbench(monkeypatch, state)
    update = {"cost_cny": 9.2, "supersede": True}
    if expected_revision is not None:
        update["expected_revision"] = expected_revision

    with pytest.raises(ValueError, match="expected_revision"):
        workbench.save_review("product-1", update)

    assert "state" not in captured


def test_first_lock_is_blocked_by_selected_custom_placeholder(monkeypatch):
    state = {
        "_revision": 3,
        "review": {
            "title": "Approved title",
            "cost_cny": 0.2,
            "fields_locked": False,
        },
    }
    captured = _install_isolated_workbench(
        monkeypatch,
        state,
        source=_source(conflicting=True),
    )

    with pytest.raises(ValueError, match="customer-service/custom placeholder"):
        workbench.save_review(
            "product-1",
            {
                "fields_locked": True,
                "selected_sku_keys": ["normal", "custom"],
            },
        )

    assert "state" not in captured
