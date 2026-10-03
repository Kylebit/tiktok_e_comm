"""GET cannot turn an approved attribute intent into a durable decision."""

from copy import deepcopy
import sqlite3
import pytest

from modules.products import server as product_server
from shared_platform.channel_category_decisions import build_category_options
from tests.test_channel_category_decision_http import (
    category_context,
    http_server,
    _approval_body,
    _observed_options,
    _preview_url,
    _request,
)


def _logical_category_state(store):
    with sqlite3.connect(f"file:{store.path.as_posix()}?mode=ro", uri=True) as connection:
        return {
            table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall())
            for table in (
                "release_channel_category_decisions", "release_active_channel_category_decisions",
                "release_channel_category_attribute_selections", "release_active_channel_category_attribute_selections",
            )
        }


def _pending_intent(category_context, http_server, monkeypatch):
    dashboard, store = category_context
    ready = {"value": False}

    def observe(payload, *, attribute_selection=None):
        observed = _observed_options(missing_required=True)
        current = product_server._category_decision_from_payload(payload)
        selected = attribute_selection or current
        if selected is not None and ready["value"]:
            row = observed["options"][0]
            row["selected_attributes"] = deepcopy(selected["selected_attributes"])
            row["attributes_complete"] = True
            row["required_values_complete"] = True
            row["missing_required_attributes"] = []
        return build_category_options(
            observed,
            context=product_server._channel_category_context(payload),
            creation_seed=product_server._channel_category_creation_seed(payload),
        )

    monkeypatch.setattr(product_server, "_observe_channel_category_options", observe)
    offer = dashboard["product"]["offer_id"]
    preview_url = _preview_url(http_server, offer)
    status, preview = _request(preview_url)
    assert status == 200
    offered = next(row for row in preview["options"] if row["recommended"])
    attribute = offered["missing_required_attributes"][0]
    body = _approval_body(preview, offered["category_identity_digest"])
    body["required_attribute_selections"] = [{
        "attribute_identity_digest": attribute["attribute_identity_digest"],
        "selection_kind": "SINGLE",
        "selected_option_identity_digests": [
            attribute["option_values"][0]["option_identity_digest"]
        ],
        "text_value": None,
        "confirm_attribute_selection": True,
    }]
    endpoint = http_server + "/api/product-workspace/channel-category-decision"
    status, pending = _request(endpoint, method="POST", payload=body)
    assert status == 200 and pending["status"] == "RECHECK_REQUIRED"
    assert pending["selection"] is None
    assert store.channel_category_decision(
        product_id=offer, product_revision=dashboard["product"]["revision"],
        channel="shopee", mode="NEW_GLOBAL",
    ) is None
    ready["value"] = True
    return dashboard, store, preview_url, endpoint, body


def test_get_with_matching_approved_intent_is_readonly_and_server_owned_resume_is_idempotent(
    category_context, http_server, monkeypatch,
):
    dashboard, store, preview_url, endpoint, body = _pending_intent(
        category_context, http_server, monkeypatch,
    )
    before = store.path.read_bytes()
    logical_before = _logical_category_state(store)
    status, preview = _request(preview_url)
    assert status == 200
    assert store.path.read_bytes() == before, "category preview GET persisted a decision"
    assert _logical_category_state(store) == logical_before
    assert preview["status"] == "RECHECK_REQUIRED"
    assert preview["selection"] is None
    assert preview["attribute_selection"]["selection_count"] == 1
    assert preview["external_writes_performed"] == []
    assert preview["next_action"]["action"] == "resume_channel_category_attributes"
    resume_endpoint = http_server + "/api/product-workspace/channel-category-decision/resume"
    resume = {
        "offer_id": dashboard["product"]["offer_id"],
        "target_label": "shopee:GLOBAL",
        "expected_product_revision": dashboard["product"]["revision"],
        "selection_digest": preview["attribute_selection"]["selection_digest"],
    }
    # Resume consumes the server's persisted authorization, never a caller Kyle string.
    status, resumed = _request(resume_endpoint, method="POST", payload=resume)
    assert status == 200 and resumed["status"] == "SELECTED"
    decision = store.channel_category_decision(
        product_id=dashboard["product"]["offer_id"],
        product_revision=dashboard["product"]["revision"],
        channel="shopee", mode="NEW_GLOBAL",
    )
    assert decision is not None
    status, replay = _request(resume_endpoint, method="POST", payload=resume)
    assert status == 200 and replay["created"] is False
    assert replay["selection"] == resumed["selection"]
    # The original explicit decision route keeps its existing idempotent contract.
    status, legacy_replay = _request(endpoint, method="POST", payload=body)
    assert status == 200 and legacy_replay["created"] is False
    assert legacy_replay["selection"] == resumed["selection"]
    before_reload = store.path.read_bytes()
    logical_reload = _logical_category_state(store)
    status, reloaded = _request(preview_url)
    assert status == 200 and reloaded["status"] == "SELECTED"
    assert reloaded["selection"] == resumed["selection"]
    assert store.path.read_bytes() == before_reload
    assert _logical_category_state(store) == logical_reload


@pytest.mark.parametrize("resume", [False, True])
def test_registered_r3_cannot_replay_old_category_post_or_resume(
    category_context, http_server, monkeypatch, resume,
):
    _, store, _, endpoint, body = _pending_intent(
        category_context, http_server, monkeypatch,
    )
    from shared_platform import publication_r2_review as review

    monkeypatch.setattr(review, "has_registration", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(product_server, "_registered_r3_post_allowed", lambda *_: False)
    before = store.path.read_bytes()
    if resume:
        intent = product_server._active_channel_category_attribute_selection(
            product_server._release_plan_payload_from_dashboard(category_context[0], bind_shopee_global_plan=False)[0]
        )
        endpoint += "/resume"
        body = {"offer_id": body["offer_id"], "target_label": body["target_label"],
                "expected_product_revision": body["expected_product_revision"], "selection_digest": intent["selection_digest"]}
    status, result = _request(endpoint, method="POST", payload=body)
    assert status == 409
    assert result["external_writes_performed"] == []
    assert store.path.read_bytes() == before


def test_resume_cas_refuses_changed_active_intent_before_any_decision_insert(
    category_context, http_server, monkeypatch,
):
    from shared_platform.channel_category_decisions import (
        approve_category_decision, digest_json,
        serialize_attribute_selection, serialize_category_decision,
    )
    from shared_platform.release_store import ImmutableReleaseError
    dashboard, store, _, _, _ = _pending_intent(category_context, http_server, monkeypatch)
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard, bind_shopee_global_plan=False,
    )
    assert blockers == []
    intent = product_server._active_channel_category_attribute_selection(payload)
    snapshot = product_server._observe_channel_category_options(payload, attribute_selection=intent)
    decision = approve_category_decision(
        snapshot, product_id=payload["product_id"], product_revision=payload["product_revision"],
        selected_category_identity_digest=intent["category_identity_digest"],
        selected_brand_identity_digest=intent["selected_brand_identity_digest"],
        selected_location_identity_digest=intent["selected_location_identity_digest"],
        selected_creation_fact_identity_digest=intent["selected_creation_fact_identity_digest"],
        attribute_selection_digest=intent["selection_digest"], approved_by="Kyle",
        confirm_channel_category_selection=True, confirm_seller_stock_quantity=True,
        confirm_condition_and_preorder=True,
    )
    newer = deepcopy(intent)
    newer["approval_request_digest"] = "f"*64
    newer.pop("selection_digest")
    newer["selection_digest"] = digest_json(newer)
    store.persist_channel_category_attribute_selection(serialize_attribute_selection(newer))
    before = store.path.read_bytes()
    with pytest.raises(ImmutableReleaseError, match="attribute intent changed"):
        store.persist_channel_category_decision(
            serialize_category_decision(decision),
            expected_selection_digest=intent["selection_digest"],
        )
    assert store.path.read_bytes() == before
    assert store.channel_category_decision(
        product_id=payload["product_id"], product_revision=payload["product_revision"],
        channel="shopee", mode="NEW_GLOBAL",
    ) is None


def test_preview_without_saved_intent_creates_no_local_state(
    category_context, http_server,
):
    dashboard, store = category_context
    assert not store.path.exists()
    for _ in range(2):
        status, preview = _request(_preview_url(http_server, dashboard["product"]["offer_id"]))
        assert status == 200 and preview["status"] == "READY_FOR_SELECTION"
        assert preview["attribute_selection"] is None
        assert preview["next_action"]["action"] != "resume_channel_category_attributes"
        assert not store.path.exists()
    status, result = _request(
        http_server + "/api/product-workspace/channel-category-decision/resume",
        method="POST", payload={"offer_id": dashboard["product"]["offer_id"],
                                 "target_label": "shopee:GLOBAL",
                                 "expected_product_revision": dashboard["product"]["revision"],
                                 "selection_digest": "0"*64},
    )
    assert status == 409 and result["external_writes_performed"] == []
    assert not store.path.exists()


def test_resume_requires_matching_server_owned_intent_and_current_options(
    category_context, http_server, monkeypatch,
):
    dashboard, store, _, _, _ = _pending_intent(category_context, http_server, monkeypatch)
    intent = store.channel_category_attribute_selection(
        product_id=dashboard["product"]["offer_id"],
        product_revision=dashboard["product"]["revision"],
        channel="shopee", mode="NEW_GLOBAL",
    )["selection"]
    resume_endpoint = http_server + "/api/product-workspace/channel-category-decision/resume"
    resume = {
        "offer_id": dashboard["product"]["offer_id"],
        "target_label": "shopee:GLOBAL",
        "expected_product_revision": dashboard["product"]["revision"],
        "selection_digest": intent["selection_digest"],
    }
    before = store.path.read_bytes()
    status, _ = _request(resume_endpoint, method="POST", payload={**resume, "approved_by": "Kyle"})
    assert status == 400
    status, _ = _request(resume_endpoint, method="POST", payload={**resume, "selection_digest": "0"*64})
    assert status == 409

    def changed_options(payload, **_kwargs):
        return build_category_options(
            _observed_options(missing_required=True),
            context=product_server._channel_category_context(payload),
            creation_seed=product_server._channel_category_creation_seed(payload),
        )

    monkeypatch.setattr(product_server, "_observe_channel_category_options", changed_options)
    status, result = _request(resume_endpoint, method="POST", payload=resume)
    assert status == 409 and result["external_writes_performed"] == []
    assert store.path.read_bytes() == before
