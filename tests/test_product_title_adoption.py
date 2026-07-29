from __future__ import annotations

import copy

import pytest

from domains import content_operations
from domains.content_operations import listing_title_fact_signature
from modules.products import server as product_server
from modules.sourcing import new_product_workbench
from shared_platform import release_control, release_store
from shared_platform.release_store import ReleaseStore


OFFER_ID = "3838616043"
EN_MASTER = (
    "Cute Bear PVC Wall Sticker, Waterproof Flat Cartoon Decal for "
    "Living Room, Bedroom and Entryway, 3-Piece 30 x 40 cm"
)
SOURCE = {
    "title_source": "HS4489Q小熊躲猫猫墙贴",
    "attributes": {"材质": "PVC"},
    "skus": [
        {
            "key": ";HS4489Q;30*40CM*3排版;",
            "name": "HS4489Q;30*40CM*3排版",
            "price": 6.5,
        }
    ],
}


def _locked_state() -> dict:
    state = {
        "offer_id": OFFER_ID,
        "_revision": 15,
        "review": {
            "title": "小熊躲猫猫墙贴毛绒质感俏皮表情儿童房墙角墙面装饰贴纸",
            "seller_sku": "0954",
            "fields_locked": True,
            "category": {"id": "wall-decals", "name": "墙贴"},
            "cost_cny": 6,
            "weight_kg": 0.12,
            "package_cm": [40, 3, 3],
            "selected_sites": ["lh_ph"],
            "selected_sku_keys": [";HS4489Q;30*40CM*3排版;"],
            "sku_label_overrides": {
                ";HS4489Q;30*40CM*3排版;": "30 x 40 cm, 3 Pieces",
            },
        },
        "product_approval": {
            "approval_id": "product-approval:3838616043:0954:r15",
            "package_id": "product:3838616043:0954",
            "subject_type": "product",
            "subject_id": OFFER_ID,
            "seller_sku": "0954",
            "status": "approved",
            "approved_by": "Kyle",
            "approved_at": "2026-07-27T01:00:00+00:00",
            "input_fingerprint": "sha256:old-product-facts",
        },
    }
    facts = product_server._listing_title_facts(
        new_product_workbench,
        OFFER_ID,
        state,
        source=SOURCE,
    )
    state["listing_copy"] = {
        "schema_version": "listing-title-candidates-v4",
        "status": "draft_pending_kyle_review",
        "semantic_master_en": EN_MASTER,
        "input_signature": listing_title_fact_signature(facts),
        "model": "gpt-5.4-mini-official",
        "policy_version": "listing-copy-candidates-v4",
        "candidates": [],
    }
    return state


def _install(
    monkeypatch,
    tmp_path,
    *,
    initial: dict | None = None,
) -> tuple[dict, list[dict], ReleaseStore]:
    state = copy.deepcopy(initial or _locked_state())
    saves: list[dict] = []
    store = ReleaseStore(tmp_path / "orbit-platform.db")

    def load_state(_offer_id: str) -> dict:
        return copy.deepcopy(state)

    def save_state(_offer_id: str, next_state: dict) -> dict:
        expected_revision = int(next_state.get("_revision") or 0)
        current_revision = int(state.get("_revision") or 0)
        if expected_revision != current_revision:
            raise RuntimeError("state revision changed")
        saves.append(copy.deepcopy(next_state))
        state.clear()
        state.update(copy.deepcopy(next_state))
        state["_revision"] = current_revision + 1
        return copy.deepcopy(state)

    def dashboard(**_kwargs) -> dict:
        return {
            "ok": True,
            "product": {
                "offer_id": OFFER_ID,
                "revision": int(state.get("_revision") or 0),
                "title": str((state.get("review") or {}).get("title") or ""),
                "seller_sku_candidate": "0954",
                "actual_product_approved": False,
                "fields_locked": bool(
                    (state.get("review") or {}).get("fields_locked")
                ),
            },
            "listing_copy": copy.deepcopy(state.get("listing_copy") or {}),
        }

    monkeypatch.setattr(new_product_workbench, "load_state", load_state)
    monkeypatch.setattr(new_product_workbench, "save_state", save_state)
    monkeypatch.setattr(
        new_product_workbench,
        "_source_summary",
        lambda *_args, **_kwargs: copy.deepcopy(SOURCE),
    )
    monkeypatch.setattr(release_control, "build_release_dashboard", dashboard)
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(product_server, "_product_workspace_view", lambda value: value)
    return state, saves, store


def _request(state: dict, **overrides) -> dict:
    payload = {
        "offer_id": OFFER_ID,
        "expected_revision": int(state.get("_revision") or 0),
        "candidate_title": EN_MASTER,
        "input_signature": state["listing_copy"]["input_signature"],
        "approved_by": "Kyle",
        "user_approved": True,
    }
    payload.update(overrides)
    return payload


def _create_failed_release(store: ReleaseStore) -> tuple[dict, dict]:
    plan = store.create_plan(
        {
            "plan_id": "omnichannel:3838616043:r15",
            "product_id": OFFER_ID,
            "seller_sku": "0954",
            "product_package_id": "product:3838616043:0954:r15",
            "content_package_id": "content:3838616043:r15",
            "targets": ["miaoshou:COMMON", "tiktok:LH_PH"],
            "commercial_scope": {
                "cost_snapshot_id": "cost:3838616043:r15",
                "fx_snapshot_id": "fx:2026-07-27",
                "pricing_rule_version": "sea-v1",
            },
        }
    )
    store.approve_plan(
        plan["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=plan["confirmation_token"],
    )
    run = store.start_run(plan["plan_id"])
    store.begin_target(run["run_id"], "miaoshou:COMMON")
    store.record_target_failure(
        run["run_id"],
        "miaoshou:COMMON",
        error="English title gate failed",
    )
    return plan, store.get_run(run["run_id"])


def test_adopt_en_master_supersedes_product_approval_plan_and_failed_run(
    monkeypatch,
    tmp_path,
):
    state, saves, store = _install(monkeypatch, tmp_path)
    plan, failed_run = _create_failed_release(store)

    status, payload = product_server._adopt_product_workspace_title_candidate(
        _request(state)
    )

    assert status == 200
    assert payload["external_writes_performed"] == []
    assert payload["local_writes_performed"] == [
        "release_plan_supersession",
        "workbench_state",
    ]
    assert payload["next_action"] == "review_and_reapprove_product_facts"
    assert payload["revision"] == 16
    assert payload["dashboard"]["product"]["title"] == EN_MASTER
    assert len(saves) == 1
    assert state["review"]["title"] == EN_MASTER
    assert state["review"]["fields_locked"] is False
    assert state["product_approval"]["status"] == "superseded"
    assert state["product_approval"]["superseded_fields"] == ["title"]
    assert state["listing_copy"]["status"] == "adopted_in_product_facts"
    assert state["listing_copy"]["adopted_by"] == "Kyle"
    event = state["commercial_supersessions"][-1]
    assert event["prior_approval_id"] == "product-approval:3838616043:0954:r15"
    assert event["prior_release_plan_id"] == plan["plan_id"]
    assert event["input_signature"] == state["listing_copy"]["input_signature"]

    stored_plan = store.get_plan(plan["plan_id"])
    stored_run = store.get_run(failed_run["run_id"])
    assert stored_plan["status"] == "SUPERSEDED"
    assert stored_plan["approval"]["status"] == "SUPERSEDED"
    assert stored_plan["sku_reservation"]["status"] == "SUPERSEDED"
    assert stored_run["status"] == "SUPERSEDED"
    assert {
        row["target_label"]: row["status"] for row in stored_run["targets"]
    } == {
        "miaoshou:COMMON": "SUPERSEDED",
        "tiktok:LH_PH": "SUPERSEDED",
    }


def test_title_adoption_requires_literal_kyle_approval(monkeypatch, tmp_path):
    state, saves, store = _install(monkeypatch, tmp_path)

    status, payload = product_server._adopt_product_workspace_title_candidate(
        _request(state, user_approved=False)
    )

    assert status == 400
    assert payload["error_code"] == "explicit_approval_required"
    assert saves == []
    assert not store.path.exists()


def test_title_adoption_rejects_stale_revision_without_superseding_plan(
    monkeypatch,
    tmp_path,
):
    state, saves, store = _install(monkeypatch, tmp_path)
    plan, _run = _create_failed_release(store)

    status, payload = product_server._adopt_product_workspace_title_candidate(
        _request(state, expected_revision=14)
    )

    assert status == 409
    assert payload["error_code"] == "state_revision_conflict"
    assert saves == []
    assert store.get_plan(plan["plan_id"])["status"] == "APPROVED"


@pytest.mark.parametrize(
    ("override", "error_code"),
    [
        ({"candidate_title": "Another English title"}, "title_candidate_mismatch"),
        ({"input_signature": "sha256:another"}, "title_candidate_mismatch"),
    ],
)
def test_title_adoption_rejects_candidate_identity_mismatch(
    monkeypatch,
    tmp_path,
    override,
    error_code,
):
    state, saves, store = _install(monkeypatch, tmp_path)

    status, payload = product_server._adopt_product_workspace_title_candidate(
        _request(state, **override)
    )

    assert status == 409
    assert payload["error_code"] == error_code
    assert saves == []
    assert not store.path.exists()


def test_title_adoption_rejects_candidate_from_superseded_facts(
    monkeypatch,
    tmp_path,
):
    initial = _locked_state()
    initial["listing_copy"]["input_signature"] = "sha256:stale-facts"
    state, saves, store = _install(monkeypatch, tmp_path, initial=initial)

    status, payload = product_server._adopt_product_workspace_title_candidate(
        _request(state)
    )

    assert status == 409
    assert payload["error_code"] == "title_candidate_stale"
    assert saves == []
    assert not store.path.exists()


def test_title_adoption_ignores_miaoshou_attribute_enrichment(
    monkeypatch,
    tmp_path,
):
    state, saves, store = _install(monkeypatch, tmp_path)
    enriched_source = copy.deepcopy(SOURCE)
    enriched_source["attributes"] = {
        "\u6750\u8d28": "PVC",
        "\u54c1\u724c": "Miaoshou readback enrichment",
        "\u529f\u80fd": "Miaoshou readback enrichment",
        "\u7247\u6570": "3",
    }
    monkeypatch.setattr(
        new_product_workbench,
        "_source_summary",
        lambda *_args, **_kwargs: copy.deepcopy(enriched_source),
    )

    status, payload = product_server._adopt_product_workspace_title_candidate(
        _request(state)
    )

    assert status == 200
    assert payload["adopted_title"] == EN_MASTER
    assert len(saves) == 1
    assert state["listing_copy"]["status"] == "adopted_in_product_facts"
    assert not store.path.exists()


def test_same_title_adoption_preserves_product_approval_for_successor_plan(
    monkeypatch,
    tmp_path,
):
    initial = _locked_state()
    initial["review"]["title"] = EN_MASTER
    state, saves, store = _install(monkeypatch, tmp_path, initial=initial)

    status, payload = product_server._adopt_product_workspace_title_candidate(
        _request(state)
    )

    assert status == 200
    assert payload["product_approval_preserved"] is True
    assert payload["superseded_product_approval_id"] is None
    assert payload["next_action"] == "create_successor_release_plan"
    assert len(saves) == 1
    assert state["review"]["fields_locked"] is True
    assert state["product_approval"]["status"] == "approved"
    assert state["listing_copy"]["status"] == "adopted_in_product_facts"
    assert state["listing_copy"]["product_approval_preserved"] is True
    assert state["commercial_supersessions"][-1]["status"] == "reaffirmed"
    assert not store.path.exists()


def test_adopt_then_save_same_facts_keeps_candidate_current(
    monkeypatch,
    tmp_path,
):
    state, saves, _store = _install(monkeypatch, tmp_path)

    adopt_status, _adopt_payload = (
        product_server._adopt_product_workspace_title_candidate(_request(state))
    )
    assert adopt_status == 200
    enriched_source = copy.deepcopy(SOURCE)
    enriched_source["attributes"] = {
        "\u6750\u8d28": "PVC",
        "\u54c1\u724c": "Operational Miaoshou enrichment",
        "\u529f\u80fd": "Operational Miaoshou enrichment",
    }
    monkeypatch.setattr(
        new_product_workbench,
        "_source_summary",
        lambda *_args, **_kwargs: copy.deepcopy(enriched_source),
    )

    save_status, save_payload = product_server._save_product_workspace_facts_locally(
        {
            "offer_id": OFFER_ID,
            "expected_revision": 16,
            "title": EN_MASTER,
            "cost_cny": 6,
            "weight_kg": 0.12,
            "package_cm": [40, 3, 3],
            "selected_sku_keys": [";HS4489Q;30*40CM*3排版;"],
            "sku_label_overrides": {
                ";HS4489Q;30*40CM*3排版;": "30 x 40 cm, 3 Pieces",
            },
        }
    )

    assert save_status == 200
    assert save_payload["revision"] == 17
    assert len(saves) == 2
    assert state["listing_copy"]["status"] == "adopted_in_product_facts"
    assert state["listing_copy"]["adopted_title"] == EN_MASTER


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("cost_cny", 6.5),
        ("weight_kg", 0.14),
        ("package_cm", [41, 3, 3]),
    ],
)
def test_title_adoption_rejects_true_approved_fact_changes(
    monkeypatch,
    tmp_path,
    field,
    changed_value,
):
    initial = _locked_state()
    initial["review"][field] = changed_value
    state, saves, store = _install(monkeypatch, tmp_path, initial=initial)

    status, payload = product_server._adopt_product_workspace_title_candidate(
        _request(state)
    )

    assert status == 409
    assert payload["error_code"] == "title_candidate_stale"
    assert saves == []
    assert not store.path.exists()


def test_locked_stale_candidate_can_be_refreshed_with_explicit_kyle_approval(
    monkeypatch,
    tmp_path,
):
    initial = _locked_state()
    initial["listing_copy"]["status"] = "superseded_product_facts_changed"
    state, saves, store = _install(monkeypatch, tmp_path, initial=initial)
    plan, _run = _create_failed_release(store)
    generated = {
        "schema_version": "listing-copy-candidates-v4",
        "status": "draft_pending_kyle_review",
        "semantic_master_en": "Fresh English Master Title for the Approved Facts",
        "candidates": [],
        "policy_version": "listing-copy-candidates-v4",
        "model": "fixture-model",
    }
    monkeypatch.setattr(
        content_operations,
        "generate_title_candidates",
        lambda _facts: copy.deepcopy(generated),
    )

    status, payload = product_server._generate_product_workspace_title_draft(
        {
            "offer_id": OFFER_ID,
            "expected_revision": 15,
            "refresh_stale_locked_candidate": True,
            "user_approved": True,
            "approved_by": "Kyle",
        }
    )

    assert status == 200
    assert payload["locked_stale_refresh"] is True
    assert payload["superseded_release_plan_id"] == plan["plan_id"]
    assert payload["marketplace_writes_performed"] == []
    assert len(saves) == 1
    assert state["review"]["fields_locked"] is True
    assert state["product_approval"]["status"] == "approved"
    assert state["listing_copy"]["status"] == "draft_pending_kyle_review"
    assert state["listing_copy"]["refreshed_while_product_locked"] is True
    assert state["listing_copy"]["input_signature"] == listing_title_fact_signature(
        product_server._listing_title_facts(
            new_product_workbench,
            OFFER_ID,
            state,
            source=SOURCE,
        )
    )
    assert store.get_plan(plan["plan_id"])["status"] == "SUPERSEDED"


def test_locked_unadopted_candidate_can_be_regenerated_with_kyle_approval(
    monkeypatch,
    tmp_path,
):
    initial = _locked_state()
    initial["listing_copy"]["policy_version"] = "listing-copy-candidates-v6"
    state, saves, _store = _install(monkeypatch, tmp_path, initial=initial)
    generated = {
        "schema_version": "listing-copy-candidates-v6",
        "status": "draft_pending_kyle_review",
        "semantic_master_en": "Safer Replacement English Master",
        "candidates": [],
        "policy_version": "listing-copy-candidates-v6",
        "model": "fixture-model",
    }
    monkeypatch.setattr(
        content_operations,
        "generate_title_candidates",
        lambda _facts: copy.deepcopy(generated),
    )

    status, payload = product_server._generate_product_workspace_title_draft(
        {
            "offer_id": OFFER_ID,
            "expected_revision": 15,
            "replace_unadopted_locked_candidate": True,
            "user_approved": True,
            "approved_by": "Kyle",
        }
    )

    assert status == 200
    assert payload["locked_unadopted_refresh"] is True
    assert payload["marketplace_writes_performed"] == []
    assert len(saves) == 1
    assert state["listing_copy"]["locked_candidate_recovery"] == "unadopted"
    assert state["listing_copy"]["semantic_master_en"] == (
        "Safer Replacement English Master"
    )


def test_locked_stale_refresh_rejects_stale_revision_before_model_call(
    monkeypatch,
    tmp_path,
):
    initial = _locked_state()
    initial["listing_copy"]["status"] = "superseded_product_facts_changed"
    state, saves, store = _install(monkeypatch, tmp_path, initial=initial)
    monkeypatch.setattr(
        content_operations,
        "generate_title_candidates",
        lambda _facts: pytest.fail("stale CAS must reject before calling ToAPI"),
    )

    status, payload = product_server._generate_product_workspace_title_draft(
        {
            "offer_id": OFFER_ID,
            "expected_revision": 14,
            "refresh_stale_locked_candidate": True,
            "user_approved": True,
            "approved_by": "Kyle",
        }
    )

    assert status == 409
    assert payload["error"] == "state revision is stale"
    assert payload["current_revision"] == 15
    assert state["_revision"] == 15
    assert saves == []
    assert not store.path.exists()


def test_reaffirm_preserves_explicit_superseded_release_plan_identity(
    monkeypatch,
    tmp_path,
):
    initial = _locked_state()
    initial["review"]["title"] = EN_MASTER
    initial["listing_copy"][
        "superseded_release_plan_id"
    ] = "omnichannel:prior-explicit"
    state, saves, store = _install(monkeypatch, tmp_path, initial=initial)

    status, payload = product_server._adopt_product_workspace_title_candidate(
        _request(state)
    )

    assert status == 200
    assert payload["product_approval_preserved"] is True
    assert payload["superseded_release_plan_id"] == (
        "omnichannel:prior-explicit"
    )
    assert state["listing_copy"]["superseded_release_plan_id"] == (
        "omnichannel:prior-explicit"
    )
    assert state["commercial_supersessions"][-1][
        "prior_release_plan_id"
    ] == "omnichannel:prior-explicit"
    assert not store.path.exists()


def test_locked_stale_refresh_requires_literal_kyle_approval(
    monkeypatch,
    tmp_path,
):
    initial = _locked_state()
    initial["listing_copy"]["status"] = "superseded_product_facts_changed"
    state, saves, store = _install(monkeypatch, tmp_path, initial=initial)

    status, payload = product_server._generate_product_workspace_title_draft(
        {
            "offer_id": OFFER_ID,
            "expected_revision": 15,
            "refresh_stale_locked_candidate": True,
            "user_approved": False,
            "approved_by": "Kyle",
        }
    )

    assert status == 409
    assert payload["error_code"] == "locked_title_refresh_requires_kyle_approval"
    assert saves == []
    assert not store.path.exists()


@pytest.mark.parametrize(
    "invalid_title",
    [
        "小熊躲猫猫墙贴",
        "Cute Bear Wall Sticker 😀",
    ],
)
def test_title_adoption_revalidates_semantic_master_en(
    monkeypatch,
    tmp_path,
    invalid_title,
):
    initial = _locked_state()
    initial["listing_copy"]["semantic_master_en"] = invalid_title
    state, saves, store = _install(monkeypatch, tmp_path, initial=initial)

    status, payload = product_server._adopt_product_workspace_title_candidate(
        _request(state, candidate_title=invalid_title)
    )

    assert status == 409
    assert payload["error_code"] == "invalid_semantic_master_en"
    assert saves == []
    assert not store.path.exists()
