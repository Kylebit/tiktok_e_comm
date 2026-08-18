"""WO-107 red tests for the last known one-click compatibility gaps.

These tests intentionally describe the required product behaviour before the
production fix exists.  Keep them in the permanent regression suite.
"""

from copy import deepcopy
import sqlite3

import pytest

from modules.products import server as product_server
from shared_platform.oneclick_release_controlplane import (
    OneClickReleaseStore,
    OneClickReleaseWorker,
    SystemicIdentityError,
)
from tests.test_oneclick_release_controlplane import (
    _approved_context,
    _mvp_miaoshou_registry,
)
from modules.miaoshou import oneclick_release as miaoshou
from tests.test_oneclick_miaoshou_direct_store import (
    DirectStoreFake,
    _command,
    _detail,
    _dispatch_request,
    _expected,
    _gb_category_metadata_response,
    _plan_payload,
)


@pytest.mark.parametrize(
    ("starter", "legacy_targets"),
    [
        (
            "_start_shopee_global_release",
            ["shopee:MY", "shopee:VN", "shopee:promotion:MY"],
        ),
        ("_start_ozon_release", ["ozon:promotion:RU"]),
    ],
)
def test_isolated_platform_button_ignores_legacy_oneclick_job(
    monkeypatch,
    starter,
    legacy_targets,
):
    """Shopee and Ozon buttons never inherit a pre-isolation shared job."""

    plan = {"plan_id": "omnichannel:legacy-job"}
    wakes = []

    class ReleaseStore:
        def start_run(self, plan_id):
            assert plan_id == plan["plan_id"]
            return {"run_id": "release-run:legacy"}

        def get_plan(self, plan_id):
            assert plan_id == plan["plan_id"]
            return plan

    class ControlStore:
        def ensure_job(self, **_kwargs):
            return {
                "job_id": "oneclick-job:legacy",
                "phase": "PENDING",
                "targets": [
                    {"target_label": label} for label in legacy_targets
                ],
            }

    context = {
        "plan": plan,
        "payload": {"product_revision": 31},
        "store": ReleaseStore(),
    }
    monkeypatch.setattr(
        product_server,
        "_oneclick_approved_context",
        lambda _data: (context, None),
    )
    monkeypatch.setattr(
        product_server,
        "_oneclick_adapter_registry",
        lambda: {"miaoshou-direct-store/v1": object()},
    )
    monkeypatch.setattr(
        product_server, "_oneclick_control_store", lambda: ControlStore()
    )
    monkeypatch.setattr(
        product_server,
        "_wake_oneclick_worker",
        lambda job_id: wakes.append(job_id),
    )
    monkeypatch.setattr(
        product_server,
        "_start_oneclick_release",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("isolated platform buttons must ignore legacy jobs")
        ),
    )

    status, body = getattr(product_server, starter)(
        {"confirm_publish": True, "plan_id": plan["plan_id"]}
    )

    assert status == 409
    assert body["success"] is False
    assert wakes == []


def test_empty_platform_scope_never_appends_zero_target_batch_event(tmp_path):
    """A shared-control-only legacy job is not an executable batch."""

    release, plan, run = _approved_context(
        tmp_path,
        targets=["miaoshou:COMMON"],
    )
    registry = _mvp_miaoshou_registry(("miaoshou:COMMON",))
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )

    with pytest.raises(
        Exception, match="explicit batch target scope is empty"
    ):
        control.start_explicit_batch(job["job_id"])

    with sqlite3.connect(control.path) as connection:
        rows = connection.execute(
            """
            SELECT event_json FROM oneclick_release_events
            WHERE job_id = ? AND event_type = 'EXPLICIT_BATCH_STARTED'
            """,
            (job["job_id"],),
        ).fetchall()
    assert rows == []


def test_worker_prepare_systemic_identity_error_is_durable_and_public():
    """Prepare-time systemic failure must not escape or disappear."""

    recorded = []

    class Store:
        def get_job(self, *, job_id):
            return {"job_id": job_id, "phase": "PENDING"}

        def prepare_job(self, job_id, registry):
            assert registry == {}
            raise SystemicIdentityError("fixture immutable identity drift")

        def record_systemic_stop(self, job_id, error):
            recorded.append((job_id, str(error)))

    worker = OneClickReleaseWorker(
        Store(),
        lambda: {},
        dispatch_enabled=lambda: True,
    )

    assert worker.advance_once("oneclick-job:systemic") is True
    assert recorded == [
        ("oneclick-job:systemic", "fixture immutable identity drift")
    ]


def test_tiktok_partial_collectbox_allows_five_successes_and_gb_waiver(
    monkeypatch,
):
    """GB title/attribute mismatch is waived for this round, not batch-fatal."""

    class CollectboxStore:
        @staticmethod
        def status(*, plan_id):
            assert plan_id == "omnichannel:tiktok-partial"
            successes = [
                "tiktok:LH_PH",
                "tiktok:LH_MY",
                "tiktok:LH_TH",
                "tiktok:LH_VN",
                "tiktok:MX",
            ]
            return {
                "action": {
                    "status": "PARTIAL_FAILED",
                    "platforms": [
                        {
                            "platform": "TIKTOK",
                            "status": "RECONCILIATION_REQUIRED",
                            "target_outcomes": [
                                *(
                                    {
                                        "target_label": label,
                                        "status": "SUCCEEDED",
                                        "error_code": None,
                                        "detail_digest": None,
                                    }
                                    for label in successes
                                ),
                                {
                                    "target_label": "tiktok:GB",
                                    "status": "FAILED",
                                    "error_code": (
                                        "approved_detail_readback_mismatch"
                                    ),
                                    "detail_digest": "a" * 64,
                                },
                            ],
                        }
                    ],
                }
            }

    monkeypatch.setattr(
        product_server,
        "_collectbox_action_store",
        lambda: CollectboxStore(),
    )

    assert product_server._collectbox_platform_succeeded(
        "omnichannel:tiktok-partial",
        "TIKTOK",
    ) is True


def test_gb_collectbox_keeps_title_but_writes_approved_category_and_price():
    """GB skips post-save validation, not approved category preparation."""

    target = "tiktok:GB"
    payload = _plan_payload(target)
    payload["pricing"]["selected_targets"][target]["store_prices"][0].update(
        {"list_price": "42", "currency": "GBP"}
    )
    fake = DirectStoreFake(target)
    current_title = "Vendor current GB title"
    current_category = "VENDOR-GB-CATEGORY"
    current_attributes = [
        {
            "attributeId": "vendor-attribute",
            "attributeName": "Vendor retained attribute",
            "attributeValues": [{"valueId": "vendor-value"}],
        }
    ]
    fake.detail["title"] = current_title
    fake.detail["cid"] = current_category
    fake.detail["productAttributes"] = deepcopy(current_attributes)
    saved = []

    def post(path, body):
        if path == miaoshou.SHOP_CLAIM_PATH:
            fake.calls.append((path, deepcopy(body)))
            return {"result": "success"}
        if path == fake.config["save_path"]:
            saved.append(deepcopy(body["shopCollectItemInfo"]))
            # Reproduce GB retaining these vendor-controlled fields while
            # persisting the exact approved price from the submitted update.
            candidate = deepcopy(body["shopCollectItemInfo"])
            candidate["title"] = current_title
            candidate["cid"] = current_category
            candidate["productAttributes"] = deepcopy(current_attributes)
            fake.detail = candidate
            fake.detail["detailId"] = 77
            fake.detail["shopId"] = str(fake.config["shop_id"])
            fake.detail["sourceOfferId"] = "986159122616"
            return {"result": "success"}
        return fake.post(path, body)

    result = miaoshou.prepare_selected_platform_collectbox(
        platform="tiktok",
        common_detail_id="7",
        initial_platform_detail_id="77",
        initial_claim_written=True,
        approved_plan_payload=payload,
        approved_targets=(target,),
        post=post,
        web_post=fake.web_post,
    )

    assert result["target_results"] == [
        {
            "target_label": target,
            "status": "SUCCEEDED",
            "error_code": None,
            "detail_digest": None,
        }
    ]
    assert len(saved) == 1
    assert saved[0]["title"] == current_title
    assert saved[0]["cid"] == "600338"
    assert saved[0]["productAttributes"] != current_attributes
    assert saved[0]["skuMap"]["default"]["price"] == 42

    # The waiver must not leak to MX or SEA verification.
    for strict_target in ("tiktok:MX", "tiktok:LH_PH"):
        strict_readback = _detail(strict_target)
        strict_readback["title"] = "mismatched title"
        with pytest.raises(miaoshou.MiaoshouOneClickPreDispatchError):
            miaoshou._verify_expected_detail(
                strict_readback,
                _expected(strict_target),
                platform="tiktok",
                strict_collectbox_tiktok=True,
                draft_mode=miaoshou.DIRECT_STORE_CONFIG[strict_target][
                    "draft_mode"
                ],
            )


def test_gb_dispatch_skips_post_write_readback_and_submits_directly():
    """The explicit GB waiver must remove the readback transport dependency."""

    target = "tiktok:GB"
    command = _command(target)
    required = _gb_category_metadata_response()["data"][
        "categoryMetadata"
    ]["categoryProductAttrList"][0]
    command["expected"].update(
        {
            "category_id": "600338",
            "product_attributes": [
                {
                    "attributeId": required["attrId"],
                    "attributeName": required["name"],
                    "attributeNameAlias": required["attributeNameAlias"],
                    "attributeValues": [
                        {
                            "valueName": "1",
                            "valueId": "1000256",
                            "valueNameAlias": "1",
                        }
                    ],
                }
            ],
        }
    )
    fake = DirectStoreFake(target)
    audit_calls = []
    publish_calls = []

    def unavailable_audit(*_args):
        audit_calls.append(True)
        raise RuntimeError("post-save GET unavailable")

    def publish(detail_id, shop_id):
        publish_calls.append((detail_id, shop_id))
        return {"result": "success"}

    def post(path, body):
        if path == miaoshou.CATEGORY_METADATA_PATH:
            return _gb_category_metadata_response()
        if path == miaoshou.WAREHOUSE_GET_PATH:
            return {
                "result": "success",
                "data": {
                    "shopWarehouseList": [
                        {
                            "shopId": "10204699",
                            "warehouseList": [
                                {
                                    "warehouseId": "WAREHOUSE-1",
                                    "warehouseEffectStatus": "1",
                                    "isDefault": "1",
                                }
                            ],
                        }
                    ]
                },
            }
        return fake.post(path, body)

    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(
            post=post,
            audit_detail=unavailable_audit,
            publish=publish,
        )
    )

    result = miaoshou.dispatch_tiktok_miaoshou_prepared_target(
        _dispatch_request(command)
    )

    assert result["canonical_status"] == "SUBMITTED_UNVERIFIED"
    assert audit_calls == []
    assert len(publish_calls) == 1
    assert result["external_writes"] == (
        "miaoshou:tiktok_detail:update",
        "miaoshou:tiktok_publish:submission",
    )


def test_collectbox_start_response_exposes_canonical_publishability(monkeypatch):
    """POST and GET projections must obey the same strict UI contract."""

    identity = {
        "offer_id": "3846511157",
        "plan_id": "omnichannel:collectbox-post",
        "product_revision": 31,
        "payload_digest": "a" * 64,
        "targets_digest": "b" * 64,
    }
    context = {
        "approved_plan_identity": identity,
        "plan": {"plan_id": identity["plan_id"]},
        "common_collect_box_detail_id": "7",
    }
    projection = {
        "schema_version": "collectbox-action-status/v1",
        "ok": True,
        "persisted": True,
        "action": {
            "status": "SUCCEEDED",
            "platforms": [
                {
                    "platform": "TIKTOK",
                    "status": "SUCCEEDED",
                    "target_outcomes": [],
                }
            ],
        },
    }

    class Store:
        @staticmethod
        def start(**_kwargs):
            return deepcopy(projection)

    monkeypatch.setattr(
        product_server,
        "_collectbox_identity_context",
        lambda _data, require_token: (context, None),
    )
    monkeypatch.setattr(
        product_server, "_collectbox_platform_adapter", lambda: object()
    )
    monkeypatch.setattr(
        product_server, "_collectbox_action_timing", lambda: (1, lambda: None)
    )
    monkeypatch.setattr(
        product_server, "_collectbox_action_store", lambda: Store()
    )

    status, body = product_server._start_collectbox_action(
        {
            **identity,
            "confirm_collectbox_action": True,
            "approved_by": "Kyle",
        }
    )

    assert status == 200
    assert body["action"]["platforms"][0]["publishable"] is True
