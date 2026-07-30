from __future__ import annotations

from copy import deepcopy
import json
from http.server import ThreadingHTTPServer
from threading import Thread
import urllib.error
import urllib.parse
import urllib.request

import pytest

from modules.products import server as product_server
from shared_platform import release_control, release_store
from shared_platform.channel_category_decisions import (
    build_category_options,
    digest_json,
)
from shared_platform.release_store import ReleaseStore
from tests.test_shopee_global_plan_wiring import _dashboard


def _digest(label: str) -> str:
    return digest_json({"fixture": label})


def _observed_options(*, missing_required: bool = False) -> dict:
    def option(category_id: int, name: str, value_id: int) -> dict:
        selected = [
            {
                "attribute_id": category_id + 1000,
                "attribute_value_list": [
                    {
                        "value_id": value_id,
                        "original_value_name": name,
                    }
                ],
            }
        ]
        return {
            "category_id": category_id,
            "name": name,
            "path": [
                {"category_id": 100000, "name": "Home & Living"},
                {"category_id": category_id, "name": name},
            ],
            "path_complete": True,
            "category_evidence_digest": _digest(
                f"category-{category_id}"
            ),
            "selected_attributes": selected,
            "attributes_complete": True,
            "attribute_tree_digest": digest_json(selected),
            "required_attribute_count": 1,
            "required_values_complete": True,
            "missing_required_attributes": [],
        }

    recommended = option(101157, "Wall Stickers", 2001)
    if missing_required:
        recommended["selected_attributes"] = []
        recommended["attributes_complete"] = False
        recommended["required_values_complete"] = False
        recommended["missing_required_attributes"] = [
            {
                "attribute_id": 9001,
                "label": "Material",
                "selection_kind": "SINGLE",
                "option_values": [
                    {
                        "value_id": 9002,
                        "original_value_name": "PVC",
                        "recommended": True,
                    }
                ],
                "text_value_id": None,
            }
        ]
    return {
        "schema_version": "channel-category-options-observation/v2",
        "channel": "shopee",
        "mode": "NEW_GLOBAL",
        "authority": "shopee_official_category_get",
        "recommendation_source": {
            "authority": "approved_copy_category_recommendation/v1",
            "evidence_digest": _digest("recommendation"),
        },
        "recommended_category_id": 101157,
        "options": [
            recommended,
            option(101158, "Decorative Stickers", 2002),
        ],
        "brand_options": [
            {
                "brand_id": 0,
                "original_brand_name": "NoBrand",
                "evidence_digest": _digest("brand"),
                "recommended": True,
            }
        ],
        "location_options": [
            {
                "location_id": "CN-A",
                "display_name": "中国仓库",
                "evidence_digest": _digest("location"),
                "recommended": True,
            }
        ],
        "creation_defaults": {
            "seller_stock_quantity": 200,
            "condition": "NEW",
            "preorder": {"is_pre_order": False, "days_to_ship": 0},
            "evidence_digest": _digest("creation-defaults"),
        },
    }


@pytest.fixture
def category_context(tmp_path, monkeypatch):
    dashboard = _dashboard()
    store = ReleaseStore(tmp_path / "release.sqlite3")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: deepcopy(dashboard),
    )

    def observe(payload, **_kwargs):
        return build_category_options(
            _observed_options(),
            context=product_server._channel_category_context(payload),
            creation_seed=(
                product_server._channel_category_creation_seed(payload)
            ),
        )

    monkeypatch.setattr(
        product_server,
        "_observe_channel_category_options",
        observe,
    )
    return dashboard, store


@pytest.fixture
def http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        data=(
            json.dumps(payload).encode("utf-8")
            if payload is not None
            else None
        ),
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _preview_url(base: str, offer_id: str) -> str:
    return (
        base
        + "/api/product-workspace/channel-category-decision-preview?"
        + urllib.parse.urlencode(
            {
                "offer_id": offer_id,
                "target_label": "shopee:GLOBAL",
            }
        )
    )


def _approval_body(preview: dict, selected_digest: str) -> dict:
    brand = next(
        row for row in preview["brand_options"] if row["recommended"]
    )
    location = next(
        row for row in preview["location_options"] if row["recommended"]
    )
    return {
        "offer_id": preview["offer_id"],
        "target_label": "shopee:GLOBAL",
        "expected_product_revision": preview["product_revision"],
        "expected_options_digest": preview["options_digest"],
        "selected_category_identity_digest": selected_digest,
        "selected_brand_identity_digest": brand[
            "brand_identity_digest"
        ],
        "selected_location_identity_digest": location[
            "location_identity_digest"
        ],
        "selected_creation_fact_identity_digest": preview[
            "creation_fact_option"
        ]["creation_fact_identity_digest"],
        "approved_by": "Kyle",
        "confirm_channel_category_selection": True,
        "confirm_seller_stock_quantity": True,
        "confirm_condition_and_preorder": True,
        "required_attribute_selections": [],
        "confirm_required_attribute_selections": True,
    }


def test_http_get_post_reload_and_exact_key_schema(
    category_context,
    http_server,
):
    dashboard, _store = category_context
    url = _preview_url(http_server, dashboard["product"]["offer_id"])

    status, preview = _request(url)
    assert status == 200
    assert set(preview) == {
        "ok",
        "schema_version",
        "offer_id",
        "product_revision",
        "target_label",
        "mode",
        "status",
        "options_digest",
        "recommendation",
        "options",
        "brand_options",
        "location_options",
        "creation_fact_option",
            "selection",
            "attribute_selection",
        "blocker",
        "next_action",
        "external_writes_performed",
    }
    assert preview["status"] == "READY_FOR_SELECTION"
    assert preview["selection"] is None
    alternate = next(
        row for row in preview["options"] if row["recommended"] is False
    )

    status, approved = _request(
        http_server
        + "/api/product-workspace/channel-category-decision",
        method="POST",
        payload=_approval_body(
            preview,
            alternate["category_identity_digest"],
        ),
    )
    assert status == 200
    assert approved["persisted"] is True
    assert approved["created"] is True
    assert approved["selection"]["selected_is_recommended"] is False
    assert approved["external_writes_performed"] == []

    status, reloaded = _request(url)
    assert status == 200
    assert reloaded["status"] == "SELECTED"
    assert reloaded["selection"] == approved["selection"]

    status, replay = _request(
        http_server
        + "/api/product-workspace/channel-category-decision",
        method="POST",
        payload=_approval_body(
            preview,
            alternate["category_identity_digest"],
        ),
    )
    assert status == 200
    assert replay["created"] is False
    assert replay["selection"] == approved["selection"]


def test_client_metadata_injection_and_stale_digest_fail_before_store(
    category_context,
    http_server,
):
    dashboard, store = category_context
    status, preview = _request(
        _preview_url(http_server, dashboard["product"]["offer_id"])
    )
    assert status == 200
    body = _approval_body(
        preview,
        preview["options"][0]["category_identity_digest"],
    )
    body["category_id"] = 123
    status, response = _request(
        http_server
        + "/api/product-workspace/channel-category-decision",
        method="POST",
        payload=body,
    )
    assert status == 400
    assert response["external_writes_performed"] == []

    body.pop("category_id")
    body["expected_options_digest"] = "0" * 64
    status, response = _request(
        http_server
        + "/api/product-workspace/channel-category-decision",
        method="POST",
        payload=body,
    )
    assert status == 409
    assert response["external_writes_performed"] == []
    assert store.channel_category_decision(
        product_id=dashboard["product"]["offer_id"],
        product_revision=dashboard["product"]["revision"],
        channel="shopee",
        mode="NEW_GLOBAL",
    ) is None


def test_missing_required_attribute_is_public_and_not_approvable(
    category_context,
    http_server,
    monkeypatch,
):
    dashboard, store = category_context

    def observe(payload, **_kwargs):
        return build_category_options(
            _observed_options(missing_required=True),
            context=product_server._channel_category_context(payload),
            creation_seed=(
                product_server._channel_category_creation_seed(payload)
            ),
        )

    monkeypatch.setattr(
        product_server,
        "_observe_channel_category_options",
        observe,
    )
    status, preview = _request(
        _preview_url(http_server, dashboard["product"]["offer_id"])
    )
    assert status == 200
    recommended = next(
        row for row in preview["options"] if row["recommended"]
    )
    assert recommended["approval_ready"] is False
    assert recommended["missing_required_attributes"][0]["label"] == (
        "Material"
    )
    encoded = json.dumps(preview, ensure_ascii=False)
    assert '"attribute_id":' not in encoded
    assert '"value_id":' not in encoded
    assert "original_value_name" not in encoded

    status, response = _request(
        http_server
        + "/api/product-workspace/channel-category-decision",
        method="POST",
        payload=_approval_body(
            preview,
            recommended["category_identity_digest"],
        ),
    )
    assert status == 409
    assert response["external_writes_performed"] == []
    assert store.channel_category_decision(
        product_id=dashboard["product"]["offer_id"],
        product_revision=dashboard["product"]["revision"],
        channel="shopee",
        mode="NEW_GLOBAL",
    ) is None


def test_local_selection_binds_final_plan_and_switch_invalidates_policy(
    category_context,
):
    dashboard, _store = category_context
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard,
        bind_shopee_global_plan=False,
    )
    assert blockers == []
    assert "approved_channel_category_decisions" not in payload

    snapshot = product_server._observe_channel_category_options(payload)
    first = snapshot["options"][0]["category_identity_digest"]
    status, response = (
        product_server._approve_channel_category_decision_locally(
            {
                "offer_id": dashboard["product"]["offer_id"],
                "target_label": "shopee:GLOBAL",
                "expected_product_revision": dashboard["product"][
                    "revision"
                ],
                "expected_options_digest": snapshot["options_digest"],
                "selected_category_identity_digest": first,
                "selected_brand_identity_digest": snapshot[
                    "brand_options"
                ][0]["brand_identity_digest"],
                "selected_location_identity_digest": snapshot[
                    "location_options"
                ][0]["location_identity_digest"],
                "selected_creation_fact_identity_digest": snapshot[
                    "creation_fact_option"
                ]["creation_fact_identity_digest"],
                "approved_by": "Kyle",
                "confirm_channel_category_selection": True,
                "confirm_seller_stock_quantity": True,
                "confirm_condition_and_preorder": True,
                "required_attribute_selections": [],
                "confirm_required_attribute_selections": True,
            }
        )
    )
    assert status == 200
    selected_payload, blockers = (
        product_server._release_plan_payload_from_dashboard(
            dashboard,
            bind_shopee_global_plan=False,
        )
    )
    assert blockers == []
    binding = selected_payload["approved_channel_category_decisions"][
        "shopee:GLOBAL"
    ]
    first_policy = product_server._shopee_global_plan_seed(
        selected_payload
    )["policy_digest"]
    assert binding["decision_digest"] == response["selection"][
        "decision_digest"
    ]
    assert product_server._shopee_global_plan_seed(
        selected_payload
    )["category_decision_execution"]["decision_digest"] == binding[
        "decision_digest"
    ]

    second = snapshot["options"][1]["category_identity_digest"]
    status, second_response = (
        product_server._approve_channel_category_decision_locally(
            {
                "offer_id": dashboard["product"]["offer_id"],
                "target_label": "shopee:GLOBAL",
                "expected_product_revision": dashboard["product"][
                    "revision"
                ],
                "expected_options_digest": snapshot["options_digest"],
                "selected_category_identity_digest": second,
                "selected_brand_identity_digest": snapshot[
                    "brand_options"
                ][0]["brand_identity_digest"],
                "selected_location_identity_digest": snapshot[
                    "location_options"
                ][0]["location_identity_digest"],
                "selected_creation_fact_identity_digest": snapshot[
                    "creation_fact_option"
                ]["creation_fact_identity_digest"],
                "approved_by": "Kyle",
                "confirm_channel_category_selection": True,
                "confirm_seller_stock_quantity": True,
                "confirm_condition_and_preorder": True,
                "required_attribute_selections": [],
                "confirm_required_attribute_selections": True,
            }
        )
    )
    assert status == 200
    switched_payload, blockers = (
        product_server._release_plan_payload_from_dashboard(
            dashboard,
            bind_shopee_global_plan=False,
        )
    )
    assert blockers == []
    assert product_server._shopee_global_plan_seed(
        switched_payload
    )["policy_digest"] != first_policy
    assert switched_payload["approved_channel_category_decisions"][
        "shopee:GLOBAL"
    ]["decision_digest"] == second_response["selection"][
        "decision_digest"
    ]


def test_required_attribute_single_post_rechecks_and_replay_is_local(
    category_context,
    http_server,
    monkeypatch,
):
    dashboard, store = category_context
    calls = []

    def observe(payload, *, attribute_selection=None):
        calls.append(attribute_selection is not None)
        observed = _observed_options(missing_required=True)
        current_decision = product_server._category_decision_from_payload(
            payload
        )
        if attribute_selection is not None or current_decision is not None:
            row = observed["options"][0]
            row["selected_attributes"] = (
                attribute_selection["selected_attributes"]
                if attribute_selection is not None
                else current_decision["selected_attributes"]
            )
            row["attributes_complete"] = True
            row["required_values_complete"] = True
            row["missing_required_attributes"] = []
        return build_category_options(
            observed,
            context=product_server._channel_category_context(payload),
            creation_seed=(
                product_server._channel_category_creation_seed(payload)
            ),
        )

    monkeypatch.setattr(
        product_server,
        "_observe_channel_category_options",
        observe,
    )
    status, preview = _request(
        _preview_url(http_server, dashboard["product"]["offer_id"])
    )
    assert status == 200
    offered = next(
        row for row in preview["options"] if row["recommended"]
    )
    attribute = offered["missing_required_attributes"][0]
    body = _approval_body(
        preview,
        offered["category_identity_digest"],
    )
    body["required_attribute_selections"] = [
        {
            "attribute_identity_digest": attribute[
                "attribute_identity_digest"
            ],
            "selection_kind": "SINGLE",
            "selected_option_identity_digests": [
                attribute["option_values"][0][
                    "option_identity_digest"
                ]
            ],
            "text_value": None,
            "confirm_attribute_selection": True,
        }
    ]
    endpoint = (
        http_server
        + "/api/product-workspace/channel-category-decision"
    )
    status, selected = _request(
        endpoint,
        method="POST",
        payload=body,
    )
    assert status == 200
    assert selected["status"] == "SELECTED"
    assert selected["attribute_selection"]["selection_count"] == 1
    assert selected["external_writes_performed"] == []
    assert calls == [False, False, True]
    status, replay = _request(
        endpoint,
        method="POST",
        payload=body,
    )
    assert status == 200
    assert replay["status"] == "SELECTED"
    assert replay["created"] is False
    assert store.channel_category_decision(
        product_id=dashboard["product"]["offer_id"],
        product_revision=dashboard["product"]["revision"],
        channel="shopee",
        mode="NEW_GLOBAL",
    ) is not None
    status, reloaded = _request(
        _preview_url(http_server, dashboard["product"]["offer_id"])
    )
    assert status == 200
    assert reloaded["status"] == "SELECTED"
    assert calls[-1] is False


def test_recheck_required_is_resumed_by_get_without_second_post(
    category_context,
    http_server,
    monkeypatch,
):
    dashboard, _store = category_context
    allow_recheck = {"value": False}

    def observe(payload, *, attribute_selection=None):
        observed = _observed_options(missing_required=True)
        if attribute_selection is not None and allow_recheck["value"]:
            row = observed["options"][0]
            row["selected_attributes"] = attribute_selection[
                "selected_attributes"
            ]
            row["attributes_complete"] = True
            row["required_values_complete"] = True
            row["missing_required_attributes"] = []
        return build_category_options(
            observed,
            context=product_server._channel_category_context(payload),
            creation_seed=(
                product_server._channel_category_creation_seed(payload)
            ),
        )

    monkeypatch.setattr(
        product_server,
        "_observe_channel_category_options",
        observe,
    )
    preview_url = _preview_url(
        http_server,
        dashboard["product"]["offer_id"],
    )
    status, preview = _request(preview_url)
    assert status == 200
    offered = next(
        row for row in preview["options"] if row["recommended"]
    )
    attribute = offered["missing_required_attributes"][0]
    body = _approval_body(
        preview,
        offered["category_identity_digest"],
    )
    body["required_attribute_selections"] = [
        {
            "attribute_identity_digest": attribute[
                "attribute_identity_digest"
            ],
            "selection_kind": "SINGLE",
            "selected_option_identity_digests": [
                attribute["option_values"][0][
                    "option_identity_digest"
                ]
            ],
            "text_value": None,
            "confirm_attribute_selection": True,
        }
    ]
    status, pending = _request(
        http_server
        + "/api/product-workspace/channel-category-decision",
        method="POST",
        payload=body,
    )
    assert status == 200
    assert pending["status"] == "RECHECK_REQUIRED"
    assert pending["selection"] is None
    allow_recheck["value"] = True
    status, completed = _request(preview_url)
    assert status == 200
    assert completed["status"] == "SELECTED"
    assert completed["attribute_selection"]["selection_count"] == 1
    assert completed["external_writes_performed"] == []


def test_get_query_shape_and_wrong_target_are_rejected(
    category_context,
    http_server,
):
    dashboard, _store = category_context
    offer = dashboard["product"]["offer_id"]
    base = (
        http_server
        + "/api/product-workspace/channel-category-decision-preview"
    )
    for query in (
        {"offer_id": offer},
        {
            "offer_id": offer,
            "target_label": "shopee:GLOBAL",
            "metadata": "injected",
        },
    ):
        status, response = _request(
            base + "?" + urllib.parse.urlencode(query)
        )
        assert status == 400
        assert response["external_writes_performed"] == []

    status, response = _request(
        base
        + "?"
        + urllib.parse.urlencode(
            {"offer_id": offer, "target_label": "shopee:MY"}
        )
    )
    assert status == 400
    assert response["external_writes_performed"] == []
