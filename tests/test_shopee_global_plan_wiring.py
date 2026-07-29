from __future__ import annotations

from copy import deepcopy
import json
from http.server import ThreadingHTTPServer
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from modules.products import server as product_server
from shared_platform import release_control, release_store
from shared_platform.oneclick_release_controlplane import (
    AdapterContractError,
    OneClickReleaseStore,
    _approved_selected_image_count,
    build_batch_preview,
)
from shared_platform.release_store import (
    ImmutableReleaseError,
    ReleaseStore,
)
from shared_platform.shopee_global_plan import (
    EXISTING_GLOBAL,
    ShopeeGlobalPlanObservationError,
    build_shopee_official_existing_global_seller_stock,
    build_shopee_global_plan_candidate,
)
from tests.test_oneclick_release_controlplane import (
    _approved_context,
    _registry,
)
from tests.test_product_release_v1 import _single_shopee_dashboard
from tests.test_shopee_global_plan import _base_args


def _dashboard() -> dict:
    dashboard = deepcopy(_single_shopee_dashboard())
    dashboard["product"].update(
        {
            "weight_kg": 0.2,
            "package_cm": [43, 5, 5],
            "cost_cny": 8.1,
        }
    )
    return dashboard


def _observer(
    *,
    category_name: str = "Wall Stickers",
    existing_global: bool = False,
):
    def observe(request):
        assert request["schema_version"] == (
            "shopee-global-plan-observer-request/v1"
        )
        seed = deepcopy(request["candidate_seed"])
        args = _base_args()
        args.update(seed)
        args["selected_image_positions"] = list(
            range(1, len(seed["ordered_approved_images"]) + 1)
        )
        args["category"] = {
            "category_id": 101157,
            "path": [
                {"category_id": 100000, "name": "Home & Living"},
                {"category_id": 101157, "name": category_name},
            ],
            "path_complete": True,
            "evidence_digest": "1" * 64,
        }
        args["variations"] = [
            {
                "name": "Style",
                "option_list": [
                    {
                        "option": "Default",
                        "approved_image_position": 1,
                    }
                ],
            }
        ]
        assignment = request["sku_lineage"]["assignment"]
        model_sku = assignment["model_skus"][0]["model_sku"]
        quantity = args["seller_stock"]["quantity"]
        args["models"] = [
            {
                "global_model_sku": model_sku,
                "tier_index": [0],
                "original_price_cny": seed["target_pricing"][
                    "global_original_price"
                ],
                "seller_stock_quantity": quantity,
            }
        ]
        if existing_global:
            args.update(
                {
                    "mode": EXISTING_GLOBAL,
                    "existing_global_item_id": 57115039489,
                    "existing_global_identity_evidence_digest": "8" * 64,
                }
            )
            args.update(
                build_shopee_official_existing_global_seller_stock(
                    observation_evidence_digest=args[
                        "observation_evidence_digest"
                    ],
                    existing_global_item_id=args["existing_global_item_id"],
                    existing_global_identity_evidence_digest=args[
                        "existing_global_identity_evidence_digest"
                    ],
                    seller_stock_rows=[
                        {
                            "location_id": args["location"]["location_id"],
                            "stock": quantity,
                        }
                    ],
                )
            )
        return build_shopee_global_plan_candidate(**args)

    return observe


@pytest.fixture
def governed_context(tmp_path, monkeypatch):
    dashboard = _dashboard()
    store = ReleaseStore(tmp_path / "release.db")
    monkeypatch.setattr(release_store, "default_release_store", lambda: store)
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **_kwargs: deepcopy(dashboard),
    )
    def observe_payload(payload):
        try:
            seed = product_server._shopee_global_plan_seed(payload)
        except (TypeError, ValueError):
            return product_server._blocked_shopee_global_plan_candidate()
        return _observer()(
            {
                "schema_version": (
                    "shopee-global-plan-observer-request/v1"
                ),
                "offer_id": payload["product_id"],
                "product_revision": payload["product_revision"],
                "targets": list(seed.pop("targets")),
                "source_identity": payload["source_product_identity"],
                "sku_lineage": payload["sku_lineage"],
                "candidate_seed": seed,
            }
        )

    monkeypatch.setattr(
        product_server,
        "_observe_shopee_global_plan_candidate",
        observe_payload,
    )
    return dashboard, store


def _approve_body(dashboard: dict, candidate_digest: str) -> dict:
    return {
        "offer_id": dashboard["product"]["offer_id"],
        "expected_product_revision": dashboard["product"]["revision"],
        "expected_candidate_digest": candidate_digest,
        "approved_by": "Kyle",
        "confirm_approved_shopee_global_plan": True,
    }


def test_store_persists_canonical_record_idempotently_and_restarts(
    governed_context,
):
    dashboard, store = governed_context
    payload, _state, candidate, _approval = (
        product_server._shopee_global_plan_preview_for_dashboard(dashboard)
    )
    status, approved = product_server._approve_shopee_global_plan_locally(
        _approve_body(dashboard, candidate.candidate_digest)
    )
    assert status == 200
    status, replay = product_server._approve_shopee_global_plan_locally(
        _approve_body(dashboard, candidate.candidate_digest)
    )
    assert status == 200
    assert replay["record_digest"] == approved["record_digest"]

    restarted = ReleaseStore(store.path)
    row = restarted.shopee_global_plan_approval(
        product_id=payload["product_id"],
        product_revision=payload["product_revision"],
        candidate_digest=candidate.candidate_digest,
    )
    assert row is not None
    assert row["record_digest"] == approved["record_digest"]
    assert row["approved"].candidate_digest == candidate.candidate_digest


def test_store_rejects_record_identity_tamper(governed_context):
    dashboard, store = governed_context
    _payload, _state, candidate, _approval = (
        product_server._shopee_global_plan_preview_for_dashboard(dashboard)
    )
    status, approved = product_server._approve_shopee_global_plan_locally(
        _approve_body(dashboard, candidate.candidate_digest)
    )
    assert status == 200
    with store._connect() as connection:
        connection.execute(
            "DROP TRIGGER trg_release_shopee_global_plan_immutable"
        )
        connection.execute(
            """
            UPDATE release_shopee_global_plan_approvals
            SET record_digest = ?
            """,
            ("0" * 64,),
        )
    with pytest.raises(ImmutableReleaseError):
        store.shopee_global_plan_approval(
            product_id=dashboard["product"]["offer_id"]
        )


def test_approval_rows_are_append_only_and_revision_exact(governed_context):
    dashboard, store = governed_context
    payload, _state, candidate, _approval = (
        product_server._shopee_global_plan_preview_for_dashboard(dashboard)
    )
    status, response = product_server._approve_shopee_global_plan_locally(
        _approve_body(dashboard, candidate.candidate_digest)
    )
    assert status == 200
    first = store.shopee_global_plan_approval(
        product_id=payload["product_id"],
        product_revision=payload["product_revision"],
        candidate_digest=candidate.candidate_digest,
    )
    assert first is not None
    second = store.persist_shopee_global_plan_approval(
        product_id=payload["product_id"],
        product_revision=payload["product_revision"] + 1,
        source_identity_digest=payload["source_product_identity"][
            "identity_digest"
        ],
        sku_lineage_digest=payload["sku_lineage"]["reservation"][
            "reservation_digest"
        ],
        serialized_record=first["record_json"],
    )
    assert second["record_digest"] == response["record_digest"]
    assert second["approval_record_id"] != first["approval_record_id"]
    replay = store.persist_shopee_global_plan_approval(
        product_id=payload["product_id"],
        product_revision=payload["product_revision"],
        source_identity_digest=payload["source_product_identity"][
            "identity_digest"
        ],
        sku_lineage_digest=payload["sku_lineage"]["reservation"][
            "reservation_digest"
        ],
        serialized_record=first["record_json"],
    )
    assert replay["created"] is False
    current_revision = store.shopee_global_plan_approval(
        product_id=payload["product_id"],
        product_revision=payload["product_revision"] + 1,
    )
    assert current_revision["approval_record_id"] == second["approval_record_id"]
    with pytest.raises(Exception, match="append-only"):
        with store._transaction() as connection:
            connection.execute(
                """
                DELETE FROM release_shopee_global_plan_approvals
                WHERE approval_record_id = ?
                """,
                (first["approval_record_id"],),
            )


def test_release_plan_requires_current_exact_approval(governed_context):
    dashboard, _store = governed_context
    before, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard
    )
    assert "approved_shopee_global_plan" not in before
    assert any("review_shopee_global_plan:" in row for row in blockers)
    assert product_server._release_plan_recovery_actions(
        dashboard, blockers
    ) == [
        {
            "code": "review_shopee_global_plan",
            "label": "核对并批准 Shopee 全球商品方案",
            "detail": (
                "系统将重新读取当前官方候选；只有 Kyle 对当前精确"
                "候选完成批准后，ReleasePlan 才会开放。"
            ),
            "next_codes": ["review_shopee_global_plan"],
            "marketplace_writes_performed": [],
        }
    ]

    _base, _state, candidate, _approval = (
        product_server._shopee_global_plan_preview_for_dashboard(dashboard)
    )
    status, _response = product_server._approve_shopee_global_plan_locally(
        _approve_body(dashboard, candidate.candidate_digest)
    )
    assert status == 200
    after, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard
    )
    assert blockers == []
    assert set(after["approved_shopee_global_plan"]) == {
        "schema_version",
        "mode",
        "candidate_digest",
        "approved_plan_digest",
        "selected_image_positions",
        "selected_source_image_manifest_digest",
        "record_digest",
    }
    assert type(after["_approved_shopee_global_plan_record"]) is str
    assert _approved_selected_image_count(after) == 1


def test_existing_global_official_current_facts_can_be_approved_and_bound(
    governed_context, monkeypatch
):
    dashboard, store = governed_context

    def observe_payload(payload):
        seed = product_server._shopee_global_plan_seed(payload)
        return _observer(existing_global=True)(
            {
                "schema_version": (
                    "shopee-global-plan-observer-request/v1"
                ),
                "offer_id": payload["product_id"],
                "product_revision": payload["product_revision"],
                "targets": list(seed.pop("targets")),
                "source_identity": payload["source_product_identity"],
                "sku_lineage": payload["sku_lineage"],
                "candidate_seed": seed,
            }
        )

    monkeypatch.setattr(
        product_server,
        "_observe_shopee_global_plan_candidate",
        observe_payload,
    )
    status, preview = product_server._preview_shopee_global_plan(
        dashboard["product"]["offer_id"]
    )
    assert status == 200
    assert preview["candidate"]["status"] == "READY"
    assert preview["candidate"]["mode"] == EXISTING_GLOBAL
    encoded_preview = json.dumps(preview, ensure_ascii=False)
    for forbidden in (
        "57115039489",
        "CN-WAREHOUSE-APPROVED",
        "shopee-official-existing-global-seller-stock/v1",
    ):
        assert forbidden not in encoded_preview

    status, response = product_server._approve_shopee_global_plan_locally(
        _approve_body(
            dashboard,
            preview["candidate"]["digests"]["candidate_digest"],
        )
    )
    assert status == 200
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard
    )
    assert blockers == []
    assert payload["approved_shopee_global_plan"]["mode"] == EXISTING_GLOBAL
    stored = store.shopee_global_plan_approval(
        product_id=payload["product_id"],
        product_revision=payload["product_revision"],
        candidate_digest=response["approval"]["digests"]["candidate_digest"],
    )
    assert stored is not None
    internal = stored["approved"].server_owned_execution_payload(
        observe_payload(payload)
    )["plan"]
    assert internal["seller_stock"]["source"] == (
        "shopee-official-existing-global-seller-stock/v1"
    )
    assert internal["seller_stock"]["quantity"] == 200


def test_dashboard_plan_binding_never_calls_official_observer(
    governed_context, monkeypatch
):
    dashboard, _store = governed_context
    calls = []
    monkeypatch.setattr(
        product_server,
        "_observe_shopee_global_plan_candidate",
        lambda _payload: calls.append("called"),
    )
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard
    )
    assert calls == []
    assert "_approved_shopee_global_plan_record" not in payload
    assert any("review_shopee_global_plan:" in row for row in blockers)


def test_dashboard_with_local_approval_never_calls_official_observer(
    governed_context, monkeypatch
):
    dashboard, _store = governed_context
    _payload, _state, candidate, _approval = (
        product_server._shopee_global_plan_preview_for_dashboard(dashboard)
    )
    status, _response = product_server._approve_shopee_global_plan_locally(
        _approve_body(dashboard, candidate.candidate_digest)
    )
    assert status == 200
    calls = []
    monkeypatch.setattr(
        product_server,
        "_observe_shopee_global_plan_candidate",
        lambda _payload: calls.append("called"),
    )
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard
    )
    assert calls == []
    assert blockers == []
    assert "_approved_shopee_global_plan_record" in payload


def test_server_seed_selects_every_approved_image_when_within_shopee_limit(
    governed_context,
):
    dashboard, _store = governed_context
    first = dashboard["content"]["images"][0]
    dashboard["content"]["images"] = [
        {
            **first,
            "position": position,
            "image_url": f"https://assets.example/global-{position}.jpg",
            "artifact_id": f"source-{position}",
            "audit_id": f"review-{position}",
        }
        for position in range(1, 4)
    ]
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard,
        bind_shopee_global_plan=False,
    )

    assert blockers == []
    seed = product_server._shopee_global_plan_seed(payload)
    assert seed["selected_image_positions"] == [1, 2, 3]


def test_local_binding_rejects_observer_selected_image_subset(
    governed_context,
):
    dashboard, _store = governed_context
    first = dashboard["content"]["images"][0]
    dashboard["content"]["images"] = [
        {
            **first,
            "position": position,
            "image_url": f"https://assets.example/global-{position}.jpg",
            "artifact_id": f"source-{position}",
            "audit_id": f"review-{position}",
        }
        for position in range(1, 3)
    ]
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard,
        bind_shopee_global_plan=False,
    )
    assert blockers == []
    seed = product_server._shopee_global_plan_seed(payload)
    seed.pop("targets")
    candidate = _observer()(
        {
            "schema_version": "shopee-global-plan-observer-request/v1",
            "candidate_seed": seed,
            "sku_lineage": payload["sku_lineage"],
        }
    )
    observed = candidate._plan.payload()
    observed["selected_image_positions"] = [1]

    assert not product_server._shopee_global_plan_matches_local_payload(
        payload,
        observed,
    )


def test_server_seed_requires_explicit_selection_above_shopee_limit(
    governed_context,
):
    dashboard, _store = governed_context
    first = dashboard["content"]["images"][0]
    dashboard["content"]["images"] = [
        {
            **first,
            "position": position,
            "image_url": f"https://assets.example/global-{position}.jpg",
            "artifact_id": f"source-{position}",
            "audit_id": f"review-{position}",
        }
        for position in range(1, 11)
    ]
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard,
        bind_shopee_global_plan=False,
    )

    assert blockers == []
    with pytest.raises(
        ValueError,
        match="image selection requires explicit approval",
    ):
        product_server._shopee_global_plan_seed(payload)


def test_candidate_or_revision_drift_cannot_reuse_approval(
    governed_context, monkeypatch
):
    dashboard, _store = governed_context
    _payload, _state, candidate, _approval = (
        product_server._shopee_global_plan_preview_for_dashboard(dashboard)
    )
    status, _response = product_server._approve_shopee_global_plan_locally(
        _approve_body(dashboard, candidate.candidate_digest)
    )
    assert status == 200

    original = product_server._observe_shopee_global_plan_candidate

    def drifted(payload):
        request = {
            "schema_version": "shopee-global-plan-observer-request/v1",
            "offer_id": payload["product_id"],
            "product_revision": payload["product_revision"],
            "targets": [
                label
                for label in payload["targets"]
                if label.startswith("shopee:")
            ],
            "source_identity": payload["source_product_identity"],
            "sku_lineage": payload["sku_lineage"],
            "candidate_seed": {
                key: value
                for key, value in product_server._shopee_global_plan_seed(
                    payload
                ).items()
                if key != "targets"
            },
        }
        return _observer(category_name="Decorative Stickers")(request)

    monkeypatch.setattr(
        product_server, "_observe_shopee_global_plan_candidate", drifted
    )
    status, preview = product_server._preview_shopee_global_plan(
        dashboard["product"]["offer_id"]
    )
    assert status == 200
    assert preview["approval_current"] is False
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard
    )
    # Ordinary dashboard reads are local-only; official drift is enforced
    # again by the background preparation seam before any claim.
    assert "_approved_shopee_global_plan_record" in payload
    assert not any("review_shopee_global_plan:" in row for row in blockers)

    monkeypatch.setattr(
        product_server, "_observe_shopee_global_plan_candidate", original
    )
    stale_body = _approve_body(dashboard, candidate.candidate_digest)
    stale_body["expected_product_revision"] += 1
    status, response = product_server._approve_shopee_global_plan_locally(
        stale_body
    )
    assert status == 409
    assert "revision" in response["error"]


def test_non_shopee_plan_never_calls_observer_or_requires_approval(
    governed_context, monkeypatch
):
    dashboard, _store = governed_context
    dashboard["publication_scope"]["selected_labels"] = [
        "miaoshou:COMMON"
    ]
    dashboard["pricing_review"]["target_pricing"] = {
        "miaoshou:COMMON": {"status": "ready"}
    }
    dashboard["omnichannel_preview"]["targets"] = [
        dashboard["omnichannel_preview"]["targets"][0]
    ]
    monkeypatch.setattr(
        product_server,
        "_observe_shopee_global_plan_candidate",
        lambda _payload: pytest.fail("observer called for non-Shopee plan"),
    )
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard
    )
    assert not any("review_shopee_global_plan:" in row for row in blockers)
    assert "approved_shopee_global_plan" not in payload


def test_public_plan_projection_removes_private_execution_payload(
    governed_context,
):
    dashboard, store = governed_context
    _payload, _state, candidate, _approval = (
        product_server._shopee_global_plan_preview_for_dashboard(dashboard)
    )
    status, _response = product_server._approve_shopee_global_plan_locally(
        _approve_body(dashboard, candidate.candidate_digest)
    )
    assert status == 200
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard
    )
    assert blockers == []
    persisted = store.create_plan(payload)
    projection = product_server._public_release_plan_projection(persisted)
    encoded = json.dumps(projection, ensure_ascii=False)
    assert "_approved_shopee_global_plan_record" not in encoded
    assert '"listing_copy"' not in encoded
    assert '"images"' not in encoded
    assert '"seller_sku"' not in encoded
    assert '"seller_stock"' not in encoded
    assert '"existing_global_item_id"' not in encoded
    assert projection["payload"]["approved_shopee_global_plan"][
        "record_digest"
    ]


@pytest.fixture
def http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(url, *, method="GET", payload=None):
    body = (
        json.dumps(payload).encode("utf-8") if payload is not None else None
    )
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers=(
            {"Content-Type": "application/json"}
            if body is not None
            else {}
        ),
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_http_dashboard_get_is_local_only(
    governed_context, http_server, monkeypatch
):
    dashboard, _store = governed_context
    monkeypatch.setattr(
        product_server,
        "_observe_shopee_global_plan_candidate",
        lambda _payload: pytest.fail(
            "ordinary dashboard GET called the official observer"
        ),
    )

    status, response = _request(
        http_server
        + "/api/product-workspace/dashboard?"
        + urllib.parse.urlencode(
            {"offer_id": dashboard["product"]["offer_id"]}
        )
    )

    assert status == 200
    assert response["ok"] is True


def test_http_preview_approval_drift_and_redaction(
    governed_context, http_server, monkeypatch
):
    dashboard, _store = governed_context
    offer_id = dashboard["product"]["offer_id"]
    preview_url = (
        http_server
        + "/api/product-workspace/shopee-global-plan-preview?"
        + urllib.parse.urlencode({"offer_id": offer_id})
    )
    status, preview = _request(preview_url)
    assert status == 200
    assert preview == {
        "ok": True,
        "schema_version": "shopee-global-plan-preview/v1",
        "offer_id": offer_id,
        "product_revision": dashboard["product"]["revision"],
        "candidate": preview["candidate"],
        "approval": None,
        "approval_current": False,
        "external_writes_performed": [],
    }
    assert preview["candidate"]["status"] == "READY"
    encoded_preview = json.dumps(preview, ensure_ascii=False)
    for forbidden in (
        "Approved factual English product description",
        "assets.example",
        "CN-WAREHOUSE-APPROVED",
        "Kyle/global-plan",
        "global_item_id",
    ):
        assert forbidden not in encoded_preview

    body = _approve_body(
        dashboard, preview["candidate"]["digests"]["candidate_digest"]
    )
    status, approved = _request(
        http_server + "/api/product-workspace/shopee-global-plan-approval",
        method="POST",
        payload=body,
    )
    assert status == 200
    assert approved["schema_version"] == (
        "shopee-global-plan-approval-response/v1"
    )
    assert approved["external_writes_performed"] == []
    assert len(approved["record_digest"]) == 64
    assert "record_json" not in approved

    status, current = _request(preview_url)
    assert status == 200
    assert current["approval_current"] is True
    assert current["approval"]["status"] == "APPROVED"

    stale = dict(body)
    stale["expected_candidate_digest"] = "0" * 64
    status, response = _request(
        http_server + "/api/product-workspace/shopee-global-plan-approval",
        method="POST",
        payload=stale,
    )
    assert status == 409
    assert response["external_writes_performed"] == []


def test_observation_auth_and_capability_are_distinct_and_redacted(
    governed_context, monkeypatch
):
    dashboard, _store = governed_context
    offer_id = dashboard["product"]["offer_id"]
    raw_secret = "raw credential detail must never be returned"

    def blocked_auth(_payload):
        try:
            raise RuntimeError(raw_secret)
        except RuntimeError:
            raise ShopeeGlobalPlanObservationError(
                category="AUTH",
                code="shopee_prepared_credentials_unavailable",
            )

    monkeypatch.setattr(
        product_server,
        "_observe_shopee_global_plan_candidate",
        blocked_auth,
    )
    status, preview = product_server._preview_shopee_global_plan(offer_id)
    assert status == 200
    assert preview["candidate"]["status"] == "BLOCKED_AUTH"
    assert preview["candidate"]["reason_category"] == "AUTH"
    assert raw_secret not in json.dumps(preview)
    status, approval = product_server._approve_shopee_global_plan_locally(
        _approve_body(dashboard, "0" * 64)
    )
    assert status == 409
    assert approval["canonical_next_action"] == {
        "action": "restore_channel_authorization",
        "target_focus": "shopee:GLOBAL",
    }
    assert raw_secret not in json.dumps(approval)

    def blocked_capability(_payload):
        raise ShopeeGlobalPlanObservationError(
            category="CAPABILITY",
            code="shopee_official_global_candidate_fixture_required",
        )

    monkeypatch.setattr(
        product_server,
        "_observe_shopee_global_plan_candidate",
        blocked_capability,
    )
    status, preview = product_server._preview_shopee_global_plan(offer_id)
    assert status == 200
    assert preview["candidate"]["status"] == "BLOCKED_CAPABILITY"
    assert preview["candidate"]["reason_category"] == "CAPABILITY"


def test_get_preview_and_job_creation_do_not_call_prepare_worker_does_once(
    tmp_path,
):
    prepare_calls = []
    targets = ["shopee:PH"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    registry = _registry(targets, prepare_calls=prepare_calls)
    preview = build_batch_preview(
        plan=plan,
        run=run,
        product_revision=plan["payload"]["product_revision"],
        registry=registry,
    )
    assert prepare_calls == []
    assert preview["runnable_target_count"] == 0
    assert preview["preparation_pending_count"] == 1
    assert preview["prepare_pending"] == ["shopee:PH"]
    assert preview["start_allowed"] is True
    ph = next(
        row
        for row in preview["targets"]
        if row["target_label"] == "shopee:PH"
    )
    assert ph["classification"] == "PREPARE_PENDING"
    assert ph["next_action"] == "prepare_batch"
    assert ph["next_action_target"] == "shopee:GLOBAL"

    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=plan["payload"]["product_revision"],
        registry=registry,
    )
    assert job["phase"] == "PENDING"
    assert prepare_calls == []
    prepared = control.prepare_job(job["job_id"], registry)
    assert prepared["phase"] in {"READY", "BLOCKED"}
    assert prepare_calls == ["shopee:PH"]


def test_http_publish_preview_is_local_only_and_prepare_pending(
    tmp_path, http_server, monkeypatch
):
    prepare_calls = []
    targets = ["shopee:PH"]
    release, plan, _run = _approved_context(tmp_path, targets=targets)
    registry = _registry(targets, prepare_calls=prepare_calls)
    context = {
        "dashboard": {},
        "payload": plan["payload"],
        "plan": plan,
        "store": release,
    }
    monkeypatch.setattr(
        product_server,
        "_oneclick_approved_context",
        lambda _data, require_token=False: (context, None),
    )
    monkeypatch.setattr(
        product_server, "_oneclick_adapter_registry", lambda: registry
    )
    monkeypatch.setattr(
        product_server,
        "_oneclick_dispatch_capability",
        lambda: {
            "schema_version": "oneclick-dispatch-capability/v1",
            "enabled": True,
            "source": "test",
            "reason_code": "enabled",
            "next_action": None,
        },
    )

    status, response = _request(
        http_server
        + "/api/product-workspace/publish-preview?"
        + urllib.parse.urlencode({"plan_id": plan["plan_id"]})
    )

    assert status == 200
    assert prepare_calls == []
    preview = response["preview"]
    assert preview["runnable_target_count"] == 0
    assert preview["preparation_pending_count"] == 1
    assert preview["prepare_pending"] == ["shopee:PH"]
    assert preview["start_allowed"] is True


def test_publish_post_returns_202_before_background_prepare(
    tmp_path, monkeypatch
):
    prepare_calls = []
    wake_calls = []
    targets = ["shopee:PH"]
    release, plan, _run = _approved_context(tmp_path, targets=targets)
    registry = _registry(targets, prepare_calls=prepare_calls)
    context = {
        "dashboard": {},
        "payload": plan["payload"],
        "plan": plan,
        "store": release,
    }
    monkeypatch.setattr(
        product_server,
        "_oneclick_approved_context",
        lambda _data, require_token=True: (context, None),
    )
    monkeypatch.setattr(
        product_server, "_oneclick_adapter_registry", lambda: registry
    )
    monkeypatch.setattr(
        product_server,
        "_oneclick_control_store",
        lambda: OneClickReleaseStore(release.path),
    )
    monkeypatch.setattr(
        product_server,
        "_wake_oneclick_worker",
        lambda job_id: wake_calls.append(job_id),
    )
    status, response = product_server._start_oneclick_release(
        {"confirm_publish": True}
    )
    assert status == 202
    assert response["accepted"] is True
    assert prepare_calls == []
    assert wake_calls == [response["job"]["job_id"]]


@pytest.mark.parametrize(
    "mutator",
    [
        lambda dashboard: dashboard["product"].update({"title": 123}),
        lambda dashboard: dashboard["product"].update({"weight_kg": True}),
        lambda dashboard: dashboard["product"].update({"weight_kg": 0}),
        lambda dashboard: dashboard["product"].update(
            {"package_cm": [43, float("inf"), 5]}
        ),
        lambda dashboard: dashboard["content"].update({"images": {}}),
        lambda dashboard: dashboard["content"]["images"].append(
            dict(dashboard["content"]["images"][0], position=2)
        ),
    ],
)
def test_malformed_server_facts_never_become_ready(
    governed_context, monkeypatch, mutator
):
    dashboard, _store = governed_context
    mutator(dashboard)
    payload, _blockers = product_server._release_plan_payload_from_dashboard(
        dashboard,
        bind_shopee_global_plan=False,
    )
    candidate = product_server._observe_shopee_global_plan_candidate(payload)
    assert candidate.status == "BLOCKED_CAPABILITY"
    assert candidate.planning_allowed is False
