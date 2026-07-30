from __future__ import annotations

import json
import sqlite3

import pytest

from shared_platform.channel_category_decisions import (
    ChannelCategoryDecisionError,
    approve_category_decision,
    build_category_options,
    category_decision_execution_payload,
    category_decision_plan_binding,
    decision_matches_global_plan,
    digest_json,
    public_options_projection,
    serialize_category_decision,
    validate_category_decision,
)
from shared_platform.release_store import ReleaseStore


def _digest(label: str) -> str:
    return digest_json({"fixture": label})


def _context(*, revision: int = 7) -> dict:
    return {
        "schema_version": "channel-category-observer-request/v1",
        "product_id": "3845131687",
        "product_revision": revision,
        "channel": "shopee",
        "mode": "NEW_GLOBAL",
        "source_identity_digest": _digest("source"),
        "sku_lineage_digest": _digest("lineage"),
        "approved_copy_digest": _digest("copy"),
        "targets_digest": _digest("targets"),
    }


def _attribute(
    attribute_id: int,
    value_id: int,
    name: str,
) -> dict:
    return {
        "attribute_id": attribute_id,
        "attribute_value_list": [
            {
                "value_id": value_id,
                "original_value_name": name,
            }
        ],
    }


def _option(
    category_id: int,
    name: str,
    *,
    attribute_id: int,
    value_id: int,
) -> dict:
    attributes = [_attribute(attribute_id, value_id, name)]
    return {
        "category_id": category_id,
        "name": name,
        "path": [
            {"category_id": 10, "name": "Home"},
            {"category_id": category_id, "name": name},
        ],
        "path_complete": True,
        "category_evidence_digest": _digest(f"category-{category_id}"),
        "selected_attributes": attributes,
        "attributes_complete": True,
        "attribute_tree_digest": digest_json(attributes),
        "required_attribute_count": 1,
        "required_values_complete": True,
        "missing_required_attributes": [],
    }


def _observation() -> dict:
    return {
        "schema_version": "channel-category-options-observation/v1",
        "channel": "shopee",
        "mode": "NEW_GLOBAL",
        "authority": "shopee_official_category_get",
        "recommendation_source": {
            "authority": "approved_copy_category_recommendation/v1",
            "evidence_digest": _digest("recommendation"),
        },
        "recommended_category_id": 101,
        "options": [
            _option(
                101,
                "Wall Stickers",
                attribute_id=501,
                value_id=601,
            ),
            _option(
                102,
                "Decorative Stickers",
                attribute_id=502,
                value_id=602,
            ),
        ],
    }


def _options(*, context: dict | None = None) -> dict:
    return build_category_options(
        _observation(),
        context=context or _context(),
    )


def _approve(
    options: dict,
    *,
    option_index: int = 0,
) -> dict:
    option = options["options"][option_index]
    return approve_category_decision(
        options,
        product_id="3845131687",
        product_revision=7,
        selected_category_identity_digest=option[
            "category_identity_digest"
        ],
        approved_by="Kyle",
        confirm_channel_category_selection=True,
    )


def test_recommendation_is_visible_but_never_implicit_approval():
    snapshot = _options()
    public = public_options_projection(snapshot)

    assert public["status"] == "READY_FOR_SELECTION"
    assert public["selection"] is None
    assert public["recommendation"]["source"] == {
        "authority": "approved_copy_category_recommendation/v1",
        "evidence_digest": _digest("recommendation"),
    }
    assert sum(row["recommended"] for row in public["options"]) == 1
    assert public["next_action"]["action"] == "select_channel_category"


def test_user_can_explicitly_approve_nonrecommended_offered_category():
    snapshot = _options()
    nonrecommended = next(
        index
        for index, row in enumerate(snapshot["options"])
        if row["category_id"] == 102
    )
    decision = _approve(snapshot, option_index=nonrecommended)
    public = public_options_projection(snapshot, decision=decision)

    assert decision["selected_is_recommended"] is False
    assert public["status"] == "SELECTED"
    assert public["selection"]["decision_digest"] == decision[
        "decision_digest"
    ]
    assert public["selection"]["selected_is_recommended"] is False
    assert public["next_action"]["action"] == "review_shopee_global_plan"


def test_missing_required_attributes_are_actionable_but_raw_free():
    observed = _observation()
    row = observed["options"][0]
    row["selected_attributes"] = []
    row["attributes_complete"] = False
    row["required_values_complete"] = False
    row["missing_required_attributes"] = [
        {
            "attribute_id": 991,
            "label": "Material",
            "selection_kind": "single",
            "option_values": [
                {
                    "value_id": 992,
                    "original_value_name": "PVC",
                }
            ],
        }
    ]
    snapshot = build_category_options(observed, context=_context())
    public = public_options_projection(snapshot)
    recommended = next(row for row in public["options"] if row["recommended"])

    assert recommended["approval_ready"] is False
    assert recommended["missing_required_attributes"] == [
        {
            "label": "Material",
            "selection_kind": "single",
            "option_identity_digest": recommended[
                "missing_required_attributes"
            ][0]["option_identity_digest"],
        }
    ]
    encoded = json.dumps(public, ensure_ascii=False, sort_keys=True)
    assert "attribute_id" not in encoded
    assert "value_id" not in encoded
    assert "original_value_name" not in encoded
    assert "PVC" not in encoded
    internal = next(
        row for row in snapshot["options"] if row["recommended"]
    )
    with pytest.raises(
        ChannelCategoryDecisionError,
        match="lacks official attribute tree",
    ):
        approve_category_decision(
            snapshot,
            product_id="3845131687",
            product_revision=7,
            selected_category_identity_digest=internal[
                "category_identity_digest"
            ],
            approved_by="Kyle",
            confirm_channel_category_selection=True,
        )


def test_option_or_context_drift_invalidates_prior_selection():
    snapshot = _options()
    decision = _approve(snapshot)
    changed = _observation()
    changed["options"][0]["selected_attributes"][0][
        "attribute_value_list"
    ][0]["original_value_name"] = "Vinyl"
    changed_snapshot = build_category_options(changed, context=_context())
    revised_snapshot = _options(context=_context(revision=8))

    assert changed_snapshot["options_digest"] != snapshot["options_digest"]
    assert revised_snapshot["context_digest"] != snapshot["context_digest"]
    assert public_options_projection(
        changed_snapshot,
        decision=decision,
    )["selection"] is None


def test_approval_rejects_product_identity_drift_and_false_recommendation():
    snapshot = _options()
    with pytest.raises(
        ChannelCategoryDecisionError,
        match="product identity changed",
    ):
        approve_category_decision(
            snapshot,
            product_id="999",
            product_revision=7,
            selected_category_identity_digest=snapshot["options"][0][
                "category_identity_digest"
            ],
            approved_by="Kyle",
            confirm_channel_category_selection=True,
        )
    decision = _approve(snapshot)
    decision["selected_is_recommended"] = not decision[
        "selected_is_recommended"
    ]
    decision_without_digest = dict(decision)
    decision_without_digest.pop("decision_digest")
    decision["decision_digest"] = digest_json(decision_without_digest)
    with pytest.raises(
        ChannelCategoryDecisionError,
        match="not truthful",
    ):
        validate_category_decision(decision)


def test_execution_payload_matches_exact_new_global_plan_shape():
    snapshot = _options()
    decision = _approve(snapshot)
    execution = category_decision_execution_payload(decision)
    plan = {
        "mode": "NEW_GLOBAL",
        "category": execution["category"],
        "attribute_list": execution["attribute_list"],
        "attributes_complete": True,
        "attribute_tree_digest": execution["attribute_tree_digest"],
    }

    assert set(execution["category"]) == {
        "category_id",
        "path",
        "path_complete",
        "evidence_digest",
    }
    assert decision_matches_global_plan(decision, plan) is True
    plan["attribute_tree_digest"] = _digest("drift")
    assert decision_matches_global_plan(decision, plan) is False


def test_store_persists_reload_and_exact_replay(tmp_path):
    store = ReleaseStore(tmp_path / "release.sqlite3")
    snapshot = _options()
    decision = _approve(snapshot)
    record = serialize_category_decision(decision)

    first = store.persist_channel_category_decision(record)
    replay = store.persist_channel_category_decision(record)
    restored = store.channel_category_decision(
        product_id="3845131687",
        product_revision=7,
        channel="shopee",
        mode="NEW_GLOBAL",
        context_digest=decision["context_digest"],
    )

    assert first["created"] is True
    assert replay["created"] is False
    assert restored["decision"] == decision
    assert restored["record_json"] == record
    assert store.channel_category_decision(
        product_id="3845131687",
        product_revision=7,
        channel="shopee",
        mode="NEW_GLOBAL",
        context_digest=_digest("stale-context"),
    ) is None


def test_switch_selection_updates_pointer_but_keeps_immutable_history(
    tmp_path,
):
    store = ReleaseStore(tmp_path / "release.sqlite3")
    snapshot = _options()
    first = _approve(snapshot, option_index=0)
    second = _approve(snapshot, option_index=1)
    store.persist_channel_category_decision(
        serialize_category_decision(first)
    )
    store.persist_channel_category_decision(
        serialize_category_decision(second)
    )

    current = store.channel_category_decision(
        product_id="3845131687",
        product_revision=7,
        channel="shopee",
        mode="NEW_GLOBAL",
    )
    with sqlite3.connect(store.path) as connection:
        rows = connection.execute(
            "SELECT decision_digest FROM "
            "release_channel_category_decisions"
        ).fetchall()

    assert current["decision"]["decision_digest"] == second[
        "decision_digest"
    ]
    assert {row[0] for row in rows} == {
        first["decision_digest"],
        second["decision_digest"],
    }
    assert category_decision_plan_binding(first) != (
        category_decision_plan_binding(second)
    )


def test_immutable_decision_row_cannot_be_updated_or_deleted(tmp_path):
    store = ReleaseStore(tmp_path / "release.sqlite3")
    decision = _approve(_options())
    store.persist_channel_category_decision(
        serialize_category_decision(decision)
    )

    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE release_channel_category_decisions "
                "SET options_digest = ? WHERE decision_digest = ?",
                (_digest("tampered"), decision["decision_digest"]),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM release_channel_category_decisions "
                "WHERE decision_digest = ?",
                (decision["decision_digest"],),
            )
