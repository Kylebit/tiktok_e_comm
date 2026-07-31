from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
import urllib.error

import pytest

from domains.channel_operations import oneclick_channel_preparation
from domains.channel_operations.oneclick_release_adapters import (
    build_shopee_prepare_seed,
    dispatch_oneclick_target,
    prepare_oneclick_target,
    production_adapter_registry,
)
from domains.product_operations import (
    ModelSkuAssignment,
    SkuAssignment,
    finalize_new_source_sku_reservation,
    resolve_source_product_identity,
)
from modules.miaoshou import oneclick_release as miaoshou
from modules.shopee import oneclick_release as shopee
from tests import test_oneclick_miaoshou_direct_store as direct_store_fixture
from shared_platform.oneclick_release_controlplane import (
    DispatchInvocationError,
    DispatchTargetResult,
    OneClickReleaseStore,
    OneClickReleaseWorker,
    PreDispatchInvocationError,
    PrepareTargetRequest,
)
from shared_platform.release_store import ReleaseStore
from shared_platform.shopee_global_plan import (
    approve_shopee_global_plan,
    build_shopee_existing_current_snapshot_candidate,
    build_shopee_global_plan_candidate,
    serialize_approved_shopee_global_plan,
)
from shared_platform.target_scoped_release_contracts import (
    approved_shopee_copy_digest,
    approved_source_image_manifest_digest,
)


@pytest.fixture(autouse=True)
def _reset_oneclick_channel_transport_factories():
    """Keep transport injection local to one test.

    Production prepare must be able to rebuild its official clients without
    inheriting a fixture callback from an earlier test.
    """
    miaoshou.configure_prepare_post_factory(None)
    miaoshou.configure_runtime_transport_factory(None)
    shopee.configure_prepare_transport_factory(None)
    shopee.configure_runtime_transport_factory(None)
    yield
    miaoshou.configure_prepare_post_factory(None)
    miaoshou.configure_runtime_transport_factory(None)
    shopee.configure_prepare_transport_factory(None)
    shopee.configure_runtime_transport_factory(None)


def _request(command, *, recorder=None):
    binding = (
        command.get("identity_binding")
        if isinstance(command.get("identity_binding"), dict)
        else {}
    )
    return SimpleNamespace(
        target_label=command.get("target_label"),
        idempotency_key=binding.get("idempotency_key"),
        source_identity_digest=binding.get("source_identity_digest"),
        payload_digest=binding.get("payload_digest"),
        adapter_policy_digest=binding.get("adapter_policy_digest"),
        command={"payload": {"provider_command": command}},
        progress_recorder=recorder,
    )


def _miaoshou_expected(*, target="tiktok:MX", api_less=True):
    config = miaoshou.SITE_CONFIG[target]
    currency_by_region = {
        "MX": "MXN",
        "GB": "GBP",
        "PH": "PHP",
        "MY": "MYR",
        "TH": "THB",
        "VN": "VND",
        "OZON": "RUB",
    }
    return {
        "common_detail_id": "7",
        "source_offer_id": "986159122616",
        "title": "Approved title",
        "item_num": "0954",
        "weight": "0.2",
        "package_cm": ["30", "20", "1"],
        "images": ["https://assets.example/one.jpg"],
        "notes": '<p>Exact</p><p><img src="https://assets.example/one.jpg"></p>',
        "video_url": "",
        "selected_sku_keys": ["default"],
        "model_skus": {"default": "0954"},
        "target_label": target,
        "shop_name": config["shop"],
        "shop_id": str(config["shop_id"]),
        "region": config["region"],
        "price": "33",
        "currency": currency_by_region[str(config["region"])],
    }


def _miaoshou_detail(expected, *, detail_id=77):
    return {
        "detailId": detail_id,
        "shopId": expected["shop_id"],
        "sourceOfferId": expected["source_offer_id"],
        "title": expected["title"],
        "itemNum": expected["item_num"],
        "weight": 0.2,
        "packageLength": 30,
        "packageWidth": 20,
        "packageHeight": 1,
        "imgUrls": list(expected["images"]),
        "notes": expected["notes"],
        "mainImgVideoUrl": "",
        "skuMap": {
            "default": {
                "itemNum": "0954",
                "weight": 0.2,
                "packageLength": 30,
                "packageWidth": 20,
                "packageHeight": 1,
                "price": 33,
                "priceIncludeVat": 33,
            }
        },
    }


def _miaoshou_command(*, target="tiktok:MX", action="USE_EXISTING"):
    expected = _miaoshou_expected(target=target)
    detail = _miaoshou_detail(expected)
    return {
        "schema_version": "oneclick-miaoshou-tiktok-command/v1",
        "kind": "TIKTOK_SITE",
        "target_label": target,
        "source_offer_id": expected["source_offer_id"],
        "common_detail_id": expected["common_detail_id"],
        "shop_id": expected["shop_id"],
        "action": action,
        "detail_id": "77" if action == "USE_EXISTING" else None,
        "api_less": miaoshou.SITE_CONFIG[target]["api"] is not True,
        "expected": expected,
        "observed_snapshot_digest": (
            miaoshou._digest(miaoshou._detail_snapshot(detail))
            if action == "USE_EXISTING"
            else None
        ),
    }


class MiaoshouFake:
    def __init__(self, command, *, fail_shop_read=False):
        self.command = command
        self.expected = command["expected"]
        self.detail = _miaoshou_detail(self.expected)
        self.common_detail = deepcopy(self.detail)
        self.fail_shop_read = fail_shop_read
        self.calls = []

    def post(self, path, body):
        self.calls.append((path, deepcopy(body)))
        if path == miaoshou.COMMON_GET_PATH:
            detail = deepcopy(self.common_detail)
            detail["commonCollectBoxDetailId"] = 7
            detail["sourceOfferId"] = self.expected["source_offer_id"]
            return {
                "result": "success",
                "data": {
                    "editCommonCollectBoxDetail": detail,
                    "ossMd5": "fixture-common-md5",
                },
            }
        if path == miaoshou.COMMON_EDIT_PATH:
            self.common_detail = deepcopy(
                body["editCommonCollectBoxDetail"]
            )
            return {"result": "success"}
        if path == miaoshou.SOURCE_LIST_PATH:
            rows = []
            if self.command["action"] == "USE_EXISTING":
                rows = [{
                    "collectBoxDetailId": 77,
                    "commonCollectBoxDetailId": 7,
                    "collectBoxDetailShopList": [
                        {"shopId": self.expected["shop_id"]}
                    ],
                }]
            return {
                "result": "success",
                "data": {
                    "detailList": rows,
                    "totalCount": len(rows),
                    "hasNextPage": False,
                },
            }
        if path == miaoshou.DETAIL_CREATE_PATH:
            return {
                "result": "success",
                "data": {
                    "platformCollectBoxDetailIdMap": {
                        "tiktok": {"7": 77}
                    }
                },
            }
        if path == miaoshou.SHOP_CLAIM_PATH:
            return {"result": "success"}
        if path == miaoshou.SHOP_GET_PATH:
            if self.fail_shop_read:
                raise TimeoutError("read after claim failed")
            return {
                "result": "success",
                "data": {
                    "shopCollectItemInfo": deepcopy(self.detail),
                    "ossMd5": "fixture-md5",
                },
            }
        if path == miaoshou.SHOP_SAVE_PATH:
            self.detail = deepcopy(body["shopCollectItemInfo"])
            self.detail["detailId"] = 77
            self.detail["shopId"] = self.expected["shop_id"]
            self.detail["sourceOfferId"] = self.expected["source_offer_id"]
            return {"result": "success"}
        if path == miaoshou.PUBLISH_PATH:
            return {"result": "success"}
        raise AssertionError(path)


def _immutable_payload(target="tiktok:MX"):
    config = miaoshou.SITE_CONFIG[target]
    currency_by_region = {
        "MX": "MXN",
        "GB": "GBP",
        "PH": "PHP",
        "MY": "MYR",
        "TH": "THB",
        "VN": "VND",
    }
    return {
        "product_id": "7",
        "seller_sku": "0954",
        "product_facts": {
            "title": "Approved title",
            "weight_kg": "0.2",
            "package_cm": [30, 20, 1],
            "selected_sku_keys": ["default"],
        },
        "images": [
            {"image_url": "https://assets.example/one.jpg"}
        ],
        "video_urls": [],
        "listing_copy": {
            "shopee_description_en": "Exact",
            "candidates": [{
                "channel": config["platform"],
                "site": config["region"],
                "policy_check": "passed",
                "title": "Approved title",
            }],
        },
        "pricing": {
            "selected_targets": {
                target: {
                    "store_prices": [{
                        "target_key": config["key"],
                        "list_price": "33",
                        "currency": currency_by_region[str(config["region"])],
                    }]
                }
            }
        },
    }


def _approved_worker_context(tmp_path, target="tiktok:MX"):
    identity_resolution = resolve_source_product_identity(
        collect_box={
            "source_item_id": "986159122616",
            "source_item_code": "JD5047",
        },
        precollect={
            "records": [
                {
                    "source_id": "986159122616",
                    "source_item_code": "JD5047",
                }
            ]
        },
        source_authority="1688",
    )
    assert identity_resolution.ready
    identity = identity_resolution.identity
    assignment = SkuAssignment(
        seller_sku="0954",
        model_skus=(
            ModelSkuAssignment(
                variant_key="default", model_sku="0954"
            ),
        ),
    )
    reservation = finalize_new_source_sku_reservation(
        source_identity=identity, assignment=assignment
    )
    assert reservation.ready
    payload = {
        **_immutable_payload(target),
        "plan_id": "omnichannel:oneclick-channel-cross",
        "product_id": "7",
        "seller_sku": "0954",
        "product_package_id": "product:7:0954",
        "content_package_id": "content:7:r31",
        "targets": [target],
        "product_revision": 31,
        "source_product_identity": identity.payload(),
        "sku_lineage": {
            "schema_version": "sku-lineage-reservation/v1",
            "status": "READY",
            "ready": True,
            "source_identity_digest": identity.identity_digest,
            "lineage_mode": "NEW_SOURCE",
            "assignment": assignment.payload(),
            "predecessor_id": None,
            "predecessor_revision": None,
            "predecessor_digest": None,
            "reservation": reservation.reservation.payload(),
            "blockers": [],
        },
        "commercial_scope": {"policy": "fixture-only"},
    }
    release = ReleaseStore(tmp_path / "release.db")
    created = release.create_plan(payload)
    release.approve_plan(
        created["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=created["confirmation_token"],
    )
    return (
        release,
        release.get_plan(created["plan_id"]),
        release.start_run(created["plan_id"]),
    )


def test_miaoshou_live_prepare_binds_source_and_json_command():
    command = _miaoshou_command()
    fake = MiaoshouFake(command)
    miaoshou.configure_prepare_post_factory(lambda: fake.post)
    seed = SimpleNamespace(
        target_label="tiktok:MX",
        idempotency_key="publish:tiktok:MX",
        source_identity_digest="a" * 64,
        command={
            "source_query": {"source_offer_id": "986159122616"}
        },
    )
    request = SimpleNamespace(
        immutable_plan_payload=_immutable_payload(),
        payload_digest="b" * 64,
        adapter_policy_digest="c" * 64,
    )
    prepared = miaoshou.prepare_tiktok_miaoshou_target(seed, request)
    restored = json.loads(json.dumps(prepared["command"], sort_keys=True))
    assert restored["kind"] == "DIRECT_STORE"
    assert restored["source_offer_id"] == "986159122616"
    assert restored["shop_id"] == "16265910"
    assert restored["identity_binding"]["idempotency_key"] == (
        "publish:tiktok:MX"
    )
    assert all("JD5047" not in repr(body) for _, body in fake.calls)
    assert prepared["external_writes_performed"] == []


def test_miaoshou_default_prepare_rehydrates_miaoshou_client(
    monkeypatch,
):
    command = _miaoshou_command()
    fake = MiaoshouFake(command)
    monkeypatch.setattr(
        "modules.miaoshou.client.post_open",
        fake.post,
    )
    seed = SimpleNamespace(
        target_label="tiktok:MX",
        idempotency_key="publish:tiktok:MX",
        source_identity_digest="a" * 64,
        command={
            "source_query": {"source_offer_id": "986159122616"}
        },
    )

    prepared = miaoshou.prepare_tiktok_miaoshou_target(
        seed,
        SimpleNamespace(
            immutable_plan_payload=_immutable_payload(),
            payload_digest="b" * 64,
            adapter_policy_digest="c" * 64,
        ),
    )

    assert prepared["command"]["kind"] == "DIRECT_STORE"
    assert prepared["command"]["source_offer_id"] == "986159122616"
    assert prepared["external_writes_performed"] == []
    assert fake.calls
    assert all("JD5047" not in repr(call) for call in fake.calls)


def test_prepare_credential_failures_are_typed_auth_blockers():
    miaoshou.configure_prepare_post_factory(
        lambda: (_ for _ in ()).throw(
            FileNotFoundError("credential fixture missing")
        )
    )
    with pytest.raises(miaoshou.MiaoshouOneClickPrepareBlocked) as mia_error:
        miaoshou.prepare_tiktok_miaoshou_target(
            SimpleNamespace(
                target_label="tiktok:MX",
                command={
                    "source_query": {
                        "source_offer_id": "986159122616"
                    }
                },
            ),
            SimpleNamespace(immutable_plan_payload=_immutable_payload()),
        )
    assert mia_error.value.reason_category == "AUTH"
    assert mia_error.value.reason_code == "miaoshou_credentials_unavailable"

    shopee.configure_prepare_transport_factory(
        lambda _region: (_ for _ in ()).throw(
            shopee.ShopeeOneClickPreDispatchError(
                "prepared Shopee no-refresh credentials are unavailable"
            )
        )
    )
    fake = ShopeePrepareFake()
    seed, request = _shopee_seed_and_request(fake)
    with pytest.raises(shopee.ShopeeOneClickPrepareBlocked) as shopee_error:
        shopee.prepare_plan_native_target(seed, request)
    assert shopee_error.value.reason_category == "AUTH"
    assert shopee_error.value.reason_code == (
        "shopee_prepared_credentials_unavailable"
    )


def test_miaoshou_common_is_independent_prepare_and_write_readback():
    payload = _immutable_payload()
    expected = _miaoshou_expected()
    fake = MiaoshouFake(_miaoshou_command())
    miaoshou.configure_prepare_post_factory(lambda: fake.post)
    seed = SimpleNamespace(
        target_label="miaoshou:COMMON",
        command={
            "source_query": {"source_offer_id": "986159122616"}
        },
    )
    prepared = miaoshou.prepare_tiktok_miaoshou_target(
        seed, SimpleNamespace(immutable_plan_payload=payload)
    )
    assert prepared["command"]["kind"] == "COMMON"
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(post=fake.post)
    )
    result = miaoshou.dispatch_tiktok_miaoshou_prepared_target(
        _request(json.loads(json.dumps(prepared["command"])))
    )
    assert result["canonical_status"] == "SUCCEEDED"
    assert result["external_writes"] == (miaoshou.COMMON_WRITE,)


def test_miaoshou_json_restart_apiless_submission_and_replay_command():
    command = json.loads(json.dumps(_miaoshou_command(), sort_keys=True))
    fake = MiaoshouFake(command)
    progress = []
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(post=fake.post)
    )
    result = miaoshou.dispatch_tiktok_miaoshou_prepared_target(
        _request(
            command,
            recorder=lambda _request, writes, external_id, _evidence: (
                progress.append((writes, external_id))
            ),
        )
    )
    assert result["canonical_status"] == "SUBMITTED_UNVERIFIED"
    assert result["external_writes"] == (
        miaoshou.DETAIL_UPDATE_WRITE,
        miaoshou.PUBLISH_WRITE,
    )
    assert [row[0] for row in progress] == [
        (miaoshou.DETAIL_UPDATE_WRITE,),
        (miaoshou.DETAIL_UPDATE_WRITE, miaoshou.PUBLISH_WRITE),
    ]
    assert "986159122616" in repr(fake.calls[0][1])
    assert "JD5047" not in repr(fake.calls)


def test_homebloom_api_less_target_set_matches_fixed_shop_contract():
    expected = {
        "tiktok:HB_PH",
        "tiktok:HB_MY",
        "tiktok:HB_TH",
        "tiktok:HB_VN",
    }
    assert set(miaoshou.HOMEBLOOM_API_LESS_TARGETS) == expected
    assert expected <= set(miaoshou.API_LESS_TIKTOK_TARGETS)
    assert all(
        miaoshou.SITE_CONFIG[target]["shop"] == "HomeBloom"
        and miaoshou.SITE_CONFIG[target]["api"] is False
        for target in expected
    )


@pytest.mark.parametrize(
    ("target", "shop_id", "currency"),
    [
        ("tiktok:HB_PH", "15173238", "PHP"),
        ("tiktok:HB_MY", "16770639", "MYR"),
        ("tiktok:HB_TH", "16770557", "THB"),
        ("tiktok:HB_VN", "16783702", "VND"),
    ],
)
def test_homebloom_prepare_and_submit_are_fixed_apiless_manual_contracts(
    target,
    shop_id,
    currency,
):
    command = _miaoshou_command(target=target)
    fake = MiaoshouFake(command)
    miaoshou.configure_prepare_post_factory(lambda: fake.post)
    request = SimpleNamespace(
        target_label=target,
        idempotency_key=f"fixture:{target}",
        source_identity={
            "schema_version": "source-product-identity/v1",
            "source_offer_id": "986159122616",
            "identity_digest": "a" * 64,
        },
        source_identity_digest="a" * 64,
        payload_digest="b" * 64,
        adapter_policy_digest="c" * 64,
        immutable_plan_payload=_immutable_payload(target),
    )

    prepared = prepare_oneclick_target(request)
    stored = json.loads(
        json.dumps(
            prepared["command"]["provider_command"],
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    assert prepared["classification"] == "READY_SUBMIT_MANUAL"
    assert prepared["manual_after_submit"] is True
    assert stored["target_label"] == target
    assert stored["shop_id"] == shop_id
    assert stored["expected"]["shop_name"] == "HomeBloom"
    assert stored["expected"]["currency"] == currency
    assert stored["api_less"] is True
    assert prepared["write_occurrence_plan"]["occurrences"] == [
        {
            "occurrence_id": "detail_update-1",
            "write_class": miaoshou.DETAIL_UPDATE_WRITE,
        },
        {
            "occurrence_id": "publish_submit-1",
            "write_class": miaoshou.PUBLISH_WRITE,
        },
    ]

    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(post=fake.post)
    )
    receipt = miaoshou.dispatch_tiktok_miaoshou_prepared_target(
        _request(stored)
    )
    assert receipt["canonical_status"] == "SUBMITTED_UNVERIFIED"
    assert receipt["external_writes"] == (
        miaoshou.DETAIL_UPDATE_WRITE,
        miaoshou.PUBLISH_WRITE,
    )
    assert receipt["submission_accepted"] is True
    assert receipt["readback_verified"] is False
    assert receipt["evidence"]["manual_acceptance_required"] is True
    assert sum(path == miaoshou.PUBLISH_PATH for path, _ in fake.calls) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda command: command.__setitem__("target_label", "tiktok:MX"),
        lambda command: command.__setitem__("shop_id", "16265910"),
        lambda command: command["expected"].__setitem__(
            "shop_id", "16265910"
        ),
        lambda command: command["expected"].__setitem__(
            "source_offer_id", "986159122617"
        ),
        lambda command: command.__setitem__("api_less", False),
    ],
)
def test_homebloom_wrong_target_shop_or_source_fails_before_client(
    mutation,
):
    command = _miaoshou_command(target="tiktok:HB_MY")
    mutation(command)
    client_calls = []
    miaoshou.configure_runtime_transport_factory(
        lambda: (
            client_calls.append("factory")
            or miaoshou.MiaoshouRuntimeTransport(
                post=lambda *_args: pytest.fail(
                    "identity drift must stop before client use"
                )
            )
        )
    )
    request = _request(command)
    request.target_label = "tiktok:HB_MY"

    with pytest.raises(
        miaoshou.MiaoshouOneClickPreDispatchError,
        match="identity|drift",
    ):
        miaoshou.dispatch_tiktok_miaoshou_prepared_target(request)

    assert client_calls == []


def test_production_registry_store_worker_restart_and_terminal_replay(
    tmp_path,
):
    release, plan, run = _approved_worker_context(tmp_path)
    command = _miaoshou_command()
    fake = MiaoshouFake(command)
    miaoshou.configure_prepare_post_factory(lambda: fake.post)
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(post=fake.post)
    )
    registry = production_adapter_registry()
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    for _ in range(8):
        if not worker.advance_once(job["job_id"]):
            break
    public = control.get_job(job_id=job["job_id"])
    by_target = {
        row["target_label"]: row for row in public["targets"]
    }
    assert set(by_target) == {"tiktok:MX"}
    assert by_target["tiktok:MX"]["status"] == "SUBMITTED_UNVERIFIED"
    assert by_target["tiktok:MX"]["classification"] == (
        "READY_SUBMIT_MANUAL"
    )
    assert sum(
        path == miaoshou.PUBLISH_PATH for path, _body in fake.calls
    ) == 1
    before = len(fake.calls)

    restarted = OneClickReleaseStore(release.path)
    restarted_worker = OneClickReleaseWorker(
        restarted, lambda: production_adapter_registry(),
        dispatch_enabled=lambda: True,
    )
    assert restarted_worker.recover() == 0
    assert restarted_worker.advance_once(job["job_id"]) is False
    assert len(fake.calls) == before


@pytest.mark.parametrize(
    "target",
    [
        "tiktok:HB_PH",
        "tiktok:HB_MY",
        "tiktok:HB_TH",
        "tiktok:HB_VN",
    ],
)
def test_homebloom_worker_records_manual_pending_and_restart_replays_zero(
    tmp_path,
    target,
):
    release, plan, run = _approved_worker_context(tmp_path, target)
    fake = MiaoshouFake(_miaoshou_command(target=target))
    miaoshou.configure_prepare_post_factory(lambda: fake.post)
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(post=fake.post)
    )
    registry = production_adapter_registry()
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    for _ in range(8):
        if not worker.advance_once(job["job_id"]):
            break

    public = control.get_job(job_id=job["job_id"])
    homebloom = next(
        row for row in public["targets"] if row["target_label"] == target
    )
    assert homebloom["status"] == "SUBMITTED_UNVERIFIED"
    assert homebloom["classification"] == "READY_SUBMIT_MANUAL"
    assert homebloom["manual_after_submit"] is True
    assert homebloom["requires_human"] is True
    assert homebloom["dispatch_count"] == 1
    assert homebloom["dispatch_ledger"][
        "cumulative_external_write_classes"
    ] == [
        miaoshou.DETAIL_UPDATE_WRITE,
        miaoshou.PUBLISH_WRITE,
    ]
    assert public["phase"] == "WAITING_MANUAL_ACCEPTANCE"
    assert sum(
        path == miaoshou.PUBLISH_PATH for path, _body in fake.calls
    ) == 1

    with release._connect_readonly() as connection:
        physical = connection.execute(
            """
            SELECT status, attempts
            FROM release_target_runs
            WHERE run_id = ? AND target_label = ?
            """,
            (run["run_id"], target),
        ).fetchone()
        submissions = connection.execute(
            """
            SELECT status, COUNT(*) AS receipt_count
            FROM release_target_submissions
            WHERE run_id = ? AND target_label = ?
            GROUP BY status
            """,
            (run["run_id"], target),
        ).fetchone()
        outcome = connection.execute(
            """
            SELECT receipt_json
            FROM oneclick_release_outcomes
            WHERE job_id = ? AND target_label = ? AND attempt = 1
            """,
            (job["job_id"], target),
        ).fetchone()
        prepared_row = connection.execute(
            """
            SELECT command_json, command_digest, proof_json, proof_digest
            FROM oneclick_release_targets
            WHERE job_id = ? AND target_label = ?
            """,
            (job["job_id"], target),
        ).fetchone()
    assert physical["status"] == "FAILED"
    assert physical["attempts"] == 1
    assert submissions["status"] == "SUBMITTED_UNVERIFIED"
    assert submissions["receipt_count"] == 1
    outcome_receipt = json.loads(outcome["receipt_json"])
    assert outcome_receipt["outcome"]["class"] == "SUBMITTED_UNVERIFIED"
    assert outcome_receipt["manual"]["status"] == "PENDING"
    assert outcome_receipt["reconciliation"]["status"] == "NOT_REQUIRED"
    assert outcome_receipt["dispatch"] == {
        "boundary": "ACCEPTED",
        "external_write_count": 2,
        "external_write_classes": [
            miaoshou.DETAIL_UPDATE_WRITE,
            miaoshou.PUBLISH_WRITE,
        ],
        "confirmed_external_write_count_lower_bound": 2,
    }
    stored_command = json.loads(prepared_row["command_json"])
    stored_proof = json.loads(prepared_row["proof_json"])
    provider_command = stored_command["payload"]["provider_command"]
    provider_proof = stored_proof["payload"]["provider_proof"]
    assert provider_command["action"] == "USE_EXISTING"
    assert provider_command["detail_id"] == "77"
    assert provider_command["observed_snapshot_digest"] == (
        provider_proof["observed_snapshot_digest"]
    )
    assert prepared_row["command_digest"] == hashlib.sha256(
        json.dumps(
            stored_command,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert prepared_row["proof_digest"] == hashlib.sha256(
        json.dumps(
            stored_proof,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    encoded = json.dumps(public, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "986159122616",
        str(miaoshou.SITE_CONFIG[target]["shop_id"]),
        "77:",
        "Approved title",
        "https://assets.example/one.jpg",
    ):
        assert forbidden not in encoded

    calls_before_restart = len(fake.calls)
    restarted = OneClickReleaseStore(release.path)
    restarted_worker = OneClickReleaseWorker(
        restarted,
        lambda: production_adapter_registry(),
        dispatch_enabled=lambda: True,
    )
    assert restarted_worker.recover() == 0
    assert restarted_worker.advance_once(job["job_id"]) is False
    assert len(fake.calls) == calls_before_restart


def test_homebloom_write_boundaries_preserve_one_and_two_write_truth():
    command = _miaoshou_command(target="tiktok:HB_MY")
    audit_fake = MiaoshouFake(command)
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(
            post=audit_fake.post,
            audit_detail=lambda *_args: False,
        )
    )
    with pytest.raises(miaoshou.MiaoshouOneClickDispatchError) as one:
        miaoshou.dispatch_tiktok_miaoshou_prepared_target(
            _request(command)
        )
    assert one.value.external_writes == (miaoshou.DETAIL_UPDATE_WRITE,)
    assert one.value.external_write_count == 1
    assert one.value.confirmed_external_write_count_lower_bound == 1
    assert one.value.possible_external_write_count_upper_bound == 1
    assert one.value.dispatch_outcome_unknown is False
    assert not any(
        path == miaoshou.PUBLISH_PATH for path, _ in audit_fake.calls
    )

    publish_fake = MiaoshouFake(command)
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(
            post=publish_fake.post,
            publish=lambda *_args: (_ for _ in ()).throw(
                TimeoutError("publish transport outcome unknown")
            ),
        )
    )
    with pytest.raises(miaoshou.MiaoshouOneClickDispatchError) as two:
        miaoshou.dispatch_tiktok_miaoshou_prepared_target(
            _request(command)
        )
    assert two.value.external_writes == (
        miaoshou.DETAIL_UPDATE_WRITE,
        miaoshou.PUBLISH_WRITE,
    )
    assert two.value.external_write_count is None
    assert two.value.confirmed_external_write_count_lower_bound == 1
    assert two.value.possible_external_write_count_upper_bound == 2


def test_homebloom_one_write_reconciliation_is_terminal_across_restart(
    tmp_path,
):
    target = "tiktok:HB_TH"
    release, plan, run = _approved_worker_context(tmp_path, target)
    fake = MiaoshouFake(_miaoshou_command(target=target))
    miaoshou.configure_prepare_post_factory(lambda: fake.post)
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(
            post=fake.post,
            audit_detail=lambda *_args: False,
        )
    )
    registry = production_adapter_registry()
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    for _ in range(8):
        if not worker.advance_once(job["job_id"]):
            break

    public = control.get_job(job_id=job["job_id"])
    homebloom = next(
        row for row in public["targets"] if row["target_label"] == target
    )
    assert homebloom["status"] == "RECONCILIATION_REQUIRED"
    assert homebloom["dispatch_count"] == 1
    assert homebloom["dispatch_ledger"][
        "cumulative_external_write_classes"
    ] == [miaoshou.DETAIL_UPDATE_WRITE]
    assert homebloom["dispatch_ledger"][
        "cumulative_external_write_count"
    ] == 1
    assert not any(
        path == miaoshou.PUBLISH_PATH for path, _ in fake.calls
    )
    with release._connect_readonly() as connection:
        physical = connection.execute(
            """
            SELECT status, attempts
            FROM release_target_runs
            WHERE run_id = ? AND target_label = ?
            """,
            (run["run_id"], target),
        ).fetchone()
        outcome = connection.execute(
            """
            SELECT receipt_json
            FROM oneclick_release_outcomes
            WHERE job_id = ? AND target_label = ? AND attempt = 1
            """,
            (job["job_id"], target),
        ).fetchone()
    assert physical["status"] == "FAILED"
    assert physical["attempts"] == 1
    outcome_receipt = json.loads(outcome["receipt_json"])
    assert outcome_receipt["outcome"]["class"] == (
        "RECONCILIATION_REQUIRED"
    )
    assert outcome_receipt["reconciliation"]["status"] == "REQUIRED"
    assert outcome_receipt["dispatch"] == {
        "boundary": "SUBMITTED",
        "external_write_count": 1,
        "external_write_classes": [miaoshou.DETAIL_UPDATE_WRITE],
        "confirmed_external_write_count_lower_bound": 1,
    }

    calls_before_restart = len(fake.calls)
    restarted = OneClickReleaseStore(release.path)
    restarted_worker = OneClickReleaseWorker(
        restarted,
        lambda: production_adapter_registry(),
        dispatch_enabled=lambda: True,
    )
    assert restarted_worker.recover() == 0
    assert restarted_worker.advance_once(job["job_id"]) is False
    assert len(fake.calls) == calls_before_restart


def test_homebloom_publish_unknown_settles_both_ledgers_without_replay(
    tmp_path,
):
    target = "tiktok:HB_VN"
    release, plan, run = _approved_worker_context(tmp_path, target)
    fake = MiaoshouFake(_miaoshou_command(target=target))
    miaoshou.configure_prepare_post_factory(lambda: fake.post)
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(
            post=fake.post,
            publish=lambda *_args: (_ for _ in ()).throw(
                TimeoutError("publish transport outcome unknown")
            ),
        )
    )
    registry = production_adapter_registry()
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    for _ in range(8):
        if not worker.advance_once(job["job_id"]):
            break

    public = control.get_job(job_id=job["job_id"])
    homebloom = next(
        row for row in public["targets"] if row["target_label"] == target
    )
    expected_classes = [
        miaoshou.DETAIL_UPDATE_WRITE,
        miaoshou.PUBLISH_WRITE,
        "UNKNOWN",
    ]
    assert homebloom["status"] == "RECONCILIATION_REQUIRED"
    assert homebloom["dispatch_count"] == 1
    assert homebloom["dispatch_ledger"][
        "cumulative_external_write_classes"
    ] == expected_classes
    assert homebloom["dispatch_ledger"][
        "cumulative_external_write_count"
    ] is None
    assert homebloom["dispatch_ledger"][
        "confirmed_external_write_count_lower_bound"
    ] == 1
    assert homebloom["dispatch_ledger"][
        "possible_external_write_count_upper_bound"
    ] == 2
    assert homebloom["result"][
        "cumulative_external_write_classes"
    ] == expected_classes

    with release._connect_readonly() as connection:
        physical = connection.execute(
            """
            SELECT status, attempts
            FROM release_target_runs
            WHERE run_id = ? AND target_label = ?
            """,
            (run["run_id"], target),
        ).fetchone()
        outcome = connection.execute(
            """
            SELECT receipt_json
            FROM oneclick_release_outcomes
            WHERE job_id = ? AND target_label = ? AND attempt = 1
            """,
            (job["job_id"], target),
        ).fetchone()
    assert physical["status"] == "FAILED"
    assert physical["attempts"] == 1
    outcome_receipt = json.loads(outcome["receipt_json"])
    assert outcome_receipt["dispatch"]["external_write_count"] is None
    assert outcome_receipt["dispatch"][
        "external_write_classes"
    ] == expected_classes
    assert outcome_receipt["dispatch"][
        "confirmed_external_write_count_lower_bound"
    ] == 1

    calls_before_restart = len(fake.calls)
    restarted = OneClickReleaseStore(release.path)
    restarted_worker = OneClickReleaseWorker(
        restarted,
        lambda: production_adapter_registry(),
        dispatch_enabled=lambda: True,
    )
    assert restarted_worker.recover() == 0
    assert restarted_worker.advance_once(job["job_id"]) is False
    assert len(fake.calls) == calls_before_restart


def test_production_registry_auth_blocker_keeps_canonical_targets_pristine(
    tmp_path,
):
    release, plan, run = _approved_worker_context(tmp_path)
    miaoshou.configure_prepare_post_factory(
        lambda: (_ for _ in ()).throw(
            FileNotFoundError("credential fixture missing")
        )
    )
    registry = production_adapter_registry()
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    assert worker.advance_once(job["job_id"]) is True
    public = control.get_job(job_id=job["job_id"])
    assert all(
        row["status"] == "BLOCKED_AUTH" for row in public["targets"]
    )
    assert all(
        row["next_action"] == "restore_channel_authorization"
        for row in public["targets"]
    )
    canonical = release.get_run(run["run_id"])
    assert all(
        row["status"] == "PENDING" and row["attempts"] == 0
        for row in canonical["targets"]
    )


def test_miaoshou_sea_submission_waits_for_manual_without_platform_readback():
    command = _miaoshou_command(target="tiktok:LH_MY")
    fake = MiaoshouFake(command)
    observed = []
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(
            post=fake.post,
            tiktok_readback=lambda expected: (
                observed.append(deepcopy(expected)) or True
            ),
        )
    )
    result = miaoshou.dispatch_tiktok_miaoshou_prepared_target(
        _request(json.loads(json.dumps(command)))
    )
    assert result["canonical_status"] == "SUBMITTED_UNVERIFIED"
    assert result["readback_verified"] is False
    assert observed == []
    assert result["external_writes"] == (
        miaoshou.DETAIL_UPDATE_WRITE,
        miaoshou.PUBLISH_WRITE,
    )


def test_miaoshou_create_claim_then_read_fault_preserves_two_writes():
    command = _miaoshou_command(action="CREATE_AND_CLAIM")
    fake = MiaoshouFake(command, fail_shop_read=True)
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(post=fake.post)
    )
    with pytest.raises(miaoshou.MiaoshouOneClickDispatchError) as error:
        miaoshou.dispatch_tiktok_miaoshou_prepared_target(_request(command))
    assert error.value.external_writes == (
        miaoshou.DETAIL_CREATE_WRITE,
        miaoshou.SHOP_CLAIM_WRITE,
    )
    assert error.value.dispatch_outcome_unknown is True
    assert not any(path == miaoshou.PUBLISH_PATH for path, _ in fake.calls)


def test_miaoshou_audit_after_update_preserves_exact_first_write():
    command = _miaoshou_command()
    fake = MiaoshouFake(command)
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(
            post=fake.post,
            audit_detail=lambda *_args: False,
        )
    )
    with pytest.raises(miaoshou.MiaoshouOneClickDispatchError) as error:
        miaoshou.dispatch_tiktok_miaoshou_prepared_target(_request(command))
    assert error.value.external_writes == (miaoshou.DETAIL_UPDATE_WRITE,)
    assert not any(path == miaoshou.PUBLISH_PATH for path, _ in fake.calls)


def test_miaoshou_source_pagination_only_uses_canonical_offer():
    calls = []

    def post(_path, body):
        calls.append(body)
        return {
            "result": "success",
            "data": {
                "detailList": [],
                "totalCount": 0,
                "hasNextPage": False,
            },
        }

    assert len(
        miaoshou.read_source_offer_pages("986159122616", post=post)
    ) == 1
    assert calls[0]["filter"] == {
        "sourceItemIdKeyword": "986159122616"
    }
    with pytest.raises(miaoshou.MiaoshouOneClickPreDispatchError):
        miaoshou.read_source_offer_pages("JD5047", post=post)


@pytest.mark.parametrize(
    "response",
    [
        None,
        {"result": "error"},
        {
            "result": "success",
            "data": {
                "detailList": None,
                "totalCount": 0,
                "hasNextPage": False,
            },
        },
        {
            "result": "success",
            "data": {
                "detailList": [],
                "totalCount": 0,
                "hasNextPage": True,
                "nextPage": 1,
            },
        },
    ],
)
def test_miaoshou_source_shape_faults_are_prewrite(response):
    with pytest.raises(miaoshou.MiaoshouOneClickPreDispatchError):
        miaoshou.read_source_offer_pages(
            "986159122616", post=lambda *_args: response
        )


def _shopee_global_create_facts():
    return {
        "category_id": 101157,
        "attribute_list": [
            {
                "attribute_id": 1,
                "attribute_value_list": [
                    {"value_id": 2, "original_value_name": "PVC"}
                ],
            }
        ],
        "brand": {
            "brand_id": 0,
            "original_brand_name": "NoBrand",
        },
        "seller_stock": {"location_id": "CNZ", "stock": 200},
        "original_price_cny": "9.5",
        "condition": "NEW",
        "pre_order": {"days_to_ship": 2},
    }


def _shopee_plan_command(*, with_global_create=False, image_count=1):
    command = {
        "target_label": "shopee:MY",
        "seller_sku": "0954",
        "model_sku": "0954",
        "listing_copy": {
            "title": "Approved English title",
            "description": (
                "D" * 500
                if with_global_create
                else "Approved factual English description"
            ),
        },
        "images": [
            {
                "position": position,
                "image_url": (
                    f"https://assets.example/image-{position}.jpg"
                ),
            }
            for position in range(1, image_count + 1)
        ],
        "parcel": {"weight_kg": "0.2", "package_cm": [30, 20, 1]},
        "target_pricing": {
            "local_original_price": "33",
            "currency": "MYR",
        },
        "policy": {
            "schema_version": "shopee-policy/v1",
            "policy_digest": "a" * 64,
        },
    }
    if with_global_create:
        command["global_create"] = _shopee_global_create_facts()
    return command


def _shopee_approved(*, with_global_create=False):
    return oneclick_channel_preparation.prepare_shopee_plan_native_first_attempt(
        _shopee_plan_command(with_global_create=with_global_create)
    )["approved"]


class ShopeePrepareFake:
    def __init__(
        self,
        *,
        candidate=True,
        list_fault=None,
        item_mutator=None,
        model_mutator=None,
    ):
        self.approved = _shopee_approved()
        self.candidate = candidate
        self.list_fault = list_fault
        self.item_mutator = item_mutator
        self.model_mutator = model_mutator
        self.calls = []
        self.credentials = shopee.ShopeeCredentials(
            region="MY",
            shop_id=123,
            shop_token="shop-token",
            merchant_id=456,
            merchant_token="merchant-token",
        )

    def merchant_get(self, path, params):
        self.calls.append(("merchant", path, deepcopy(params)))
        if path == shopee.GLOBAL_LIST_PATH:
            if self.list_fault is not None:
                return deepcopy(self.list_fault)
            rows = (
                [{"global_item_id": 9}]
                if self.candidate
                else []
            )
            return {
                "error": "",
                "response": {
                    "global_item_list": rows,
                    "total_count": len(rows),
                    "has_next_page": False,
                },
            }
        if path == shopee.GLOBAL_MODEL_PATH:
            rows = [{
                "global_model_id": 7,
                "global_model_sku": "0954",
                "tier_index": [0],
            }]
            if self.model_mutator is not None:
                self.model_mutator(rows)
            return {
                "error": "",
                "response": {
                    "global_model": rows
                },
            }
        if path == shopee.GLOBAL_ITEM_PATH:
            copy = self.approved["listing_copy"]
            item = {
                "global_item_id": 9,
                "global_item_name": copy["title"],
                "description": copy["description"],
                "image": {
                    "image_url_list": [
                        "https://shopee-rehost.example/one"
                    ],
                    "image_id_list": ["stable-image-1"],
                },
                "category_id": 101157,
                "attribute_list": [
                    {
                        "attribute_id": 1,
                        "attribute_value_list": [
                            {
                                "value_id": 2,
                                "original_value_name": "PVC",
                            }
                        ],
                    }
                ],
                "brand": {
                    "brand_id": 0,
                    "original_brand_name": "NoBrand",
                },
                "seller_stock": [
                    {"location_id": "CNZ", "stock": 200}
                ],
                "condition": "NEW",
                "pre_order": {
                    "is_pre_order": False,
                    "days_to_ship": 0,
                },
                "tier_variation": [
                    {
                        "name": "Style",
                        "option_list": [{"option": "Default"}],
                    }
                ],
            }
            if self.item_mutator is not None:
                self.item_mutator(item)
            return {
                "error": "",
                "response": {
                    "global_item_list": [item]
                },
            }
        raise AssertionError((path, params))

    def shop_get(self, path, params=None):
        self.calls.append(("shop", path, deepcopy(params)))
        if path == "/api/v2/product/get_item_list":
            return {
                "error": "",
                "response": {
                    "item": [],
                    "total_count": 0,
                    "has_next_page": False,
                },
            }
        if path == "/api/v2/logistics/get_channel_list":
            return {
                "error": "",
                "response": {
                    "logistics_channel_list": [{
                        "logistics_channel_id": 1,
                        "enabled": True,
                    }]
                },
            }
        raise AssertionError((path, params))

    def transport(self):
        return shopee.ShopeePrepareTransport(
            credentials=self.credentials,
            merchant_get=self.merchant_get,
            shop_get=self.shop_get,
        )


def _existing_v2_request(
    fake,
    *,
    target="shopee:GLOBAL",
    source_digest=None,
    lineage_digest=None,
):
    source_url = "https://assets.example/one.jpg"
    source_digest = source_digest or shopee._digest(
        {"source": "official-1688"}
    )
    lineage_digest = lineage_digest or shopee._digest(
        {"lineage": "0954"}
    )
    approved_copy_digest = approved_shopee_copy_digest(
        fake.approved["listing_copy"]["title"],
        fake.approved["listing_copy"]["description"],
    )
    current = shopee._observe_existing_global_candidate_availability(
        fake.transport(),
        global_item_id="9",
        seed={
            "approved_copy_digest": approved_copy_digest,
            "selected_image_positions": [1],
            "ordered_approved_images": [
                {
                    "source_url": source_url,
                    "source_image_digest": shopee._digest(
                        {"source_image": 1}
                    ),
                }
            ],
        },
        expected_model_skus=("0954",),
    )
    candidate = build_shopee_existing_current_snapshot_candidate(
        observation_authority="shopee_official_open_api",
        observation_schema_version=(
            "shopee-official-global-plan-observation/v1"
        ),
        observation_evidence_digest=current[
            "observation_evidence_digest"
        ],
        source_identity_schema_version="source-product-identity/v1",
        source_identity_digest=source_digest,
        sku_lineage_schema_version="new-source-sku-reservation/v1",
        sku_lineage_digest=lineage_digest,
        content_package_digest=shopee._digest({"content": "approved"}),
        title=fake.approved["listing_copy"]["title"],
        description=fake.approved["listing_copy"]["description"],
        approved_copy_digest=approved_copy_digest,
        ordered_approved_images=[
            {
                "source_url": source_url,
                "source_image_digest": shopee._digest(
                    {"source_image": 1}
                ),
            }
        ],
        approved_source_image_manifest_digest=(
            approved_source_image_manifest_digest([source_url])
        ),
        selected_image_positions=[1],
        parcel={
            "weight_kg": "0.2",
            "length_cm": "30",
            "width_cm": "20",
            "height_cm": "1",
            "contract_digest": shopee._digest({"parcel": "approved"}),
        },
        target_pricing={
            "currency": "CNY",
            "global_original_price": "9.5",
            "contract_digest": shopee._digest({"pricing": "approved"}),
        },
        policy_digest=shopee._digest({"policy": "approved"}),
        expected_model_skus=["0954"],
        existing_global_item=current["existing_global_item"],
        existing_global_models=current["existing_global_models"],
        existing_global_identity_evidence_digest=current[
            "existing_global_identity_evidence_digest"
        ],
    )
    assert candidate.status == "READY"
    approved = approve_shopee_global_plan(
        candidate,
        approved_by="Kyle",
        confirm_approved_shopee_global_plan=True,
        expected_candidate_digest=candidate.candidate_digest,
    )
    record = serialize_approved_shopee_global_plan(approved)
    projection = approved.public_projection()
    plan = approved.server_owned_execution_payload(candidate)["plan"]
    compact = {
        "schema_version": approved.schema_version,
        "mode": approved.mode,
        "candidate_digest": approved.candidate_digest,
        "approved_plan_digest": approved.approved_plan_digest,
        "selected_image_positions": list(
            plan["selected_image_positions"]
        ),
        "selected_source_image_manifest_digest": plan[
            "selected_source_image_manifest_digest"
        ],
        "record_digest": hashlib.sha256(record.encode("utf-8")).hexdigest(),
    }
    immutable_payload = {
        "product_id": "7",
        "seller_sku": "0954",
        "targets": ["shopee:GLOBAL", "shopee:MY"],
        "approved_shopee_global_plan": compact,
        "_approved_shopee_global_plan_record": record,
        "pricing": {
            "selected_targets": {
                "shopee:MY": {
                    "store_prices": [
                        {
                            "list_price": "33",
                            "currency": "MYR",
                        }
                    ]
                }
            }
        },
    }
    digest = shopee._digest
    return PrepareTargetRequest(
        schema_version="oneclick-prepare-target-request/v1",
        plan_id="omnichannel:existing-v2",
        run_id="release-run:existing-v2",
        target_label=target,
        product_revision=31,
        payload_digest=digest(immutable_payload),
        confirmation_token_digest=digest({"confirmation": "approved"}),
        targets_digest=digest({"targets": immutable_payload["targets"]}),
        idempotency_key=f"test:{target}",
        source_identity_digest=source_digest,
        source_identity_payload_digest=digest(
            {
                "schema_version": "source-product-identity/v1",
                "identity_digest": source_digest,
                "source_offer_id": "986159122616",
            }
        ),
        source_identity={
            "schema_version": "source-product-identity/v1",
            "identity_digest": source_digest,
            "source_offer_id": "986159122616",
        },
        sku_lineage_digest=lineage_digest,
        sku_lineage_payload_digest=digest({"lineage": lineage_digest}),
        adapter_policy_digest=digest({"adapter": "shopee-v2"}),
        immutable_plan_payload=immutable_payload,
    )


def _approved_shopee_existing_worker_context(tmp_path, fake):
    identity_resolution = resolve_source_product_identity(
        collect_box={
            "source_item_id": "986159122616",
            "source_item_code": "JD5047",
        },
        precollect={
            "records": [
                {
                    "source_id": "986159122616",
                    "source_item_code": "JD5047",
                }
            ]
        },
        source_authority="1688",
    )
    assert identity_resolution.ready
    identity = identity_resolution.identity
    assignment = SkuAssignment(
        seller_sku="0954",
        model_skus=(
            ModelSkuAssignment(
                variant_key="default", model_sku="0954"
            ),
        ),
    )
    finalized = finalize_new_source_sku_reservation(
        source_identity=identity,
        assignment=assignment,
    )
    assert finalized.ready
    reservation = finalized.reservation
    request = _existing_v2_request(
        fake,
        source_digest=identity.identity_digest.removeprefix("sha256:"),
        lineage_digest=reservation.reservation_digest.removeprefix(
            "sha256:"
        ),
    )
    compact = request.immutable_plan_payload[
        "approved_shopee_global_plan"
    ]
    record = request.immutable_plan_payload[
        "_approved_shopee_global_plan_record"
    ]
    payload = {
        **_immutable_payload(),
        "plan_id": "omnichannel:existing-v2-worker",
        "product_id": "7",
        "seller_sku": "0954",
        "product_package_id": "product:7:0954",
        "content_package_id": "content:7:r31",
        "targets": ["shopee:MY"],
        "product_revision": 31,
        "source_product_identity": identity.payload(),
        "sku_lineage": {
            "schema_version": "sku-lineage-reservation/v1",
            "status": "READY",
            "ready": True,
            "source_identity_digest": identity.identity_digest,
            "lineage_mode": "NEW_SOURCE",
            "assignment": assignment.payload(),
            "predecessor_id": None,
            "predecessor_revision": None,
            "predecessor_digest": None,
            "reservation": reservation.payload(),
            "blockers": [],
        },
        "commercial_scope": {"policy": "fixture-only"},
        "product_facts": {
            "title": fake.approved["listing_copy"]["title"],
            "weight_kg": "0.2",
            "package_cm": [30, 20, 1],
            "selected_sku_keys": ["default"],
        },
        "images": [
            {
                "position": 1,
                "image_url": "https://assets.example/one.jpg",
            }
        ],
        "listing_copy": {
            "shopee_description_en": fake.approved[
                "listing_copy"
            ]["description"],
            "candidates": [],
        },
        "pricing": {
            "selected_targets": {
                "shopee:MY": {
                    "store_prices": [
                        {
                            "list_price": "33",
                            "currency": "MYR",
                        }
                    ]
                }
            }
        },
        "approved_shopee_global_plan": dict(compact),
        "_approved_shopee_global_plan_record": record,
    }
    release = ReleaseStore(tmp_path / "release.db")
    created = release.create_plan(payload)
    release.approve_plan(
        created["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=created["confirmation_token"],
    )
    return (
        release,
        release.get_plan(created["plan_id"]),
        release.start_run(created["plan_id"]),
        json.loads(record)["approved_plan"]["plan"],
    )


def _shopee_seed_and_request(
    fake, *, with_global_create=False, image_count=1
):
    prepared = (
        oneclick_channel_preparation.prepare_shopee_plan_native_first_attempt(
            _shopee_plan_command(
                with_global_create=with_global_create,
                image_count=image_count,
            )
        )
    )
    seed = SimpleNamespace(
        command={"prepared": prepared}, target_label="shopee:MY"
    )
    request = SimpleNamespace(
        immutable_plan_payload={"product_facts": {}}
    )
    return seed, request


def test_shopee_live_prepare_full_status_proof_is_json_restart_safe():
    fake = ShopeePrepareFake()
    shopee.configure_prepare_transport_factory(
        lambda region: (
            fake.transport()
            if region == "MY"
            else pytest.fail("wrong region")
        )
    )
    seed, request = _shopee_seed_and_request(fake)
    result = shopee.prepare_plan_native_target(seed, request)
    command = json.loads(json.dumps(result["command"], sort_keys=True))
    assert command["kind"] == "EXISTING_GLOBAL"
    assert command["global_item_id"] == "9"
    assert command["selected_logistics_ids"] == [1]
    assert command["global_image_outcome"][
        "global_image_status"
    ] == "warning"
    assert [
        call[2]
        for call in fake.calls
        if call[1] == shopee.GLOBAL_LIST_PATH
    ] == [{"page_size": 50}]
    assert "publish_match_key" not in repr(result)
    assert "shop-token" not in repr(result)


def test_shopee_default_prepare_rehydrates_no_refresh_clients(
    monkeypatch,
):
    fake = ShopeePrepareFake()
    monkeypatch.setattr(
        shopee,
        "_current_credentials",
        lambda region: (
            fake.credentials
            if region == "MY"
            else pytest.fail("wrong region")
        ),
    )
    monkeypatch.setattr(
        "modules.shopee.client.merchant_get",
        lambda path, merchant_id, token, params: (
            fake.merchant_get(path, params)
            if (merchant_id, token) == (456, "merchant-token")
            else pytest.fail("wrong merchant credentials")
        ),
    )
    monkeypatch.setattr(
        "modules.shopee.client.shop_get",
        lambda path, shop_id, token, params: (
            fake.shop_get(path, params)
            if (shop_id, token) == (123, "shop-token")
            else pytest.fail("wrong shop credentials")
        ),
    )
    seed, request = _shopee_seed_and_request(fake)

    result = shopee.prepare_plan_native_target(seed, request)

    assert result["command"]["kind"] == "EXISTING_GLOBAL"
    assert result["command"]["global_item_id"] == "9"
    assert result["proof"]["no_refresh"] is True
    assert result["external_writes_performed"] == []
    assert fake.calls


def test_shopee_direct_store_prepare_is_miaoshou_read_only_and_json_safe():
    target = "shopee:MY"
    fake = direct_store_fixture.DirectStoreFake(target)
    miaoshou.configure_prepare_post_factory(lambda: fake.post)

    prepared = prepare_oneclick_target(
        direct_store_fixture._prepare_request(target)
    )

    provider = json.loads(
        json.dumps(
            prepared["command"]["provider_command"],
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    assert prepared["classification"] == "READY_SUBMIT_MANUAL"
    assert prepared["manual_after_submit"] is True
    assert provider["kind"] == "DIRECT_STORE"
    assert provider["platform"] == "shopee"
    assert provider["shop_id"] == "13295318"
    assert prepared["write_occurrence_plan"]["occurrences"] == [
        {
            "occurrence_id": "detail_update-1",
            "write_class": "miaoshou:shopee_detail:update",
        },
        {
            "occurrence_id": "publish_submit-1",
            "write_class": "miaoshou:shopee_publish:submission",
        },
    ]
    assert all(
        path
        in {
            miaoshou.DIRECT_STORE_CONFIG[target]["search_path"],
            miaoshou.DIRECT_STORE_CONFIG[target]["get_path"],
        }
        for path, _body in fake.calls
    )


def test_shopee_legacy_v1_existing_is_readable_but_not_oneclick_executable():
    from tests.test_shopee_global_plan import _base_args

    request = _existing_v2_request(ShopeePrepareFake())
    args = _base_args()
    args.update(
        {
            "mode": "EXISTING_GLOBAL",
            "source_identity_digest": request.source_identity_digest,
            "sku_lineage_digest": request.sku_lineage_digest,
            "existing_global_item_id": 9,
            "existing_global_identity_evidence_digest": "8" * 64,
        }
    )
    candidate = build_shopee_global_plan_candidate(**args)
    assert candidate.status == "READY"
    approved = approve_shopee_global_plan(
        candidate,
        approved_by="Kyle",
        confirm_approved_shopee_global_plan=True,
        expected_candidate_digest=candidate.candidate_digest,
    )
    assert approved.schema_version == "approved-shopee-global-plan/v1"
    record = serialize_approved_shopee_global_plan(approved)
    plan = approved.server_owned_execution_payload(candidate)["plan"]
    request.immutable_plan_payload[
        "_approved_shopee_global_plan_record"
    ] = record
    request.immutable_plan_payload["approved_shopee_global_plan"] = {
        "schema_version": approved.schema_version,
        "mode": approved.mode,
        "candidate_digest": approved.candidate_digest,
        "approved_plan_digest": approved.approved_plan_digest,
        "selected_image_positions": list(
            plan["selected_image_positions"]
        ),
        "selected_source_image_manifest_digest": plan[
            "selected_source_image_manifest_digest"
        ],
        "record_digest": hashlib.sha256(record.encode("utf-8")).hexdigest(),
    }

    prepared = prepare_oneclick_target(request)

    assert prepared["classification"] == "BLOCKED_CAPABILITY"
    assert prepared["reason_category"] == "CONTENT"
    assert prepared["reason_code"] == (
        "approved_shopee_existing_v2_required"
    )
    assert prepared["command"] is None
    assert prepared["proof"] is None


@pytest.mark.parametrize(
    "field",
    ["title", "images", "model", "parcel", "price", "notes", "video"],
)
def test_shopee_miaoshou_snapshot_drift_blocks_before_any_write(field):
    target = "shopee:MY"
    fake = direct_store_fixture.DirectStoreFake(target)
    miaoshou.configure_prepare_post_factory(lambda: fake.post)
    prepared = prepare_oneclick_target(
        direct_store_fixture._prepare_request(target)
    )
    provider = prepared["command"]["provider_command"]
    fake.calls.clear()
    if field == "title":
        fake.detail["title"] = "Drifted title"
    elif field == "images":
        fake.detail["imgUrls"] = ["https://assets.example/drift.jpg"]
    elif field == "model":
        fake.detail["skuMap"]["default"]["itemNum"] = "drifted"
    elif field == "parcel":
        fake.detail["packageLength"] = 31
    elif field == "price":
        fake.detail["skuMap"]["default"]["price"] = 34
    elif field == "notes":
        fake.detail["notes"] = "<p>Drifted</p>"
    else:
        fake.detail["mainImgVideoUrl"] = "https://assets.example/drift.mp4"
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(post=fake.post)
    )

    with pytest.raises(
        PreDispatchInvocationError,
        match="changed after preparation",
    ):
        dispatch_oneclick_target(
            direct_store_fixture._dispatch_request(provider)
        )

    assert not any(
        path
        in {
            miaoshou.DIRECT_STORE_CONFIG[target]["save_path"],
            miaoshou.DIRECT_STORE_CONFIG[target]["publish_path"],
        }
        for path, _body in fake.calls
    )


def test_shopee_miaoshou_publish_unknown_never_invents_global_write():
    target = "shopee:MY"
    command = direct_store_fixture._command(target)
    fake = direct_store_fixture.DirectStoreFake(
        target, malformed_publish=True
    )
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(post=fake.post)
    )

    with pytest.raises(DispatchInvocationError) as error:
        dispatch_oneclick_target(
            direct_store_fixture._dispatch_request(command)
        )

    assert error.value.external_writes == (
        "miaoshou:shopee_detail:update",
        "miaoshou:shopee_publish:submission",
    )
    assert error.value.external_write_count is None
    assert error.value.confirmed_external_write_count_lower_bound == 1
    assert error.value.possible_external_write_count_upper_bound == 2
    assert all(
        "global" not in write.casefold()
        for write in error.value.external_writes
    )


def test_shopee_miaoshou_worker_restart_never_repeats_submission(
    tmp_path,
):
    target = "shopee:MY"
    release, plan, run = _approved_worker_context(tmp_path, target)
    fake = direct_store_fixture.DirectStoreFake(target)
    miaoshou.configure_prepare_post_factory(lambda: fake.post)
    miaoshou.configure_runtime_transport_factory(
        lambda: miaoshou.MiaoshouRuntimeTransport(post=fake.post)
    )
    registry = production_adapter_registry()
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    worker = OneClickReleaseWorker(
        control,
        lambda: registry,
        dispatch_enabled=lambda: True,
    )
    for _ in range(8):
        if not worker.advance_once(job["job_id"]):
            break

    public = control.get_job(job_id=job["job_id"])
    assert public["shared_controls"] == []
    target_row = public["targets"][0]
    assert target_row["target_label"] == target
    assert target_row["status"] == "SUBMITTED_UNVERIFIED"
    assert target_row["manual_after_submit"] is True
    publish_path = miaoshou.DIRECT_STORE_CONFIG[target]["publish_path"]
    assert sum(path == publish_path for path, _body in fake.calls) == 1
    canonical = release.get_run(run["run_id"])
    my_target = next(
        row
        for row in canonical["targets"]
        if row["target_label"] == target
    )
    assert my_target["status"] == "SUBMITTED_UNVERIFIED"
    assert my_target["attempts"] == 1

    calls_before_restart = len(fake.calls)
    restarted = OneClickReleaseStore(release.path)
    restarted_worker = OneClickReleaseWorker(
        restarted,
        lambda: production_adapter_registry(),
        dispatch_enabled=lambda: True,
    )
    assert restarted_worker.recover() == 0
    assert restarted_worker.advance_once(job["job_id"]) is False
    assert len(fake.calls) == calls_before_restart


def test_shopee_new_global_prepare_is_plan_native_and_json_restart_safe():
    fake = ShopeePrepareFake(candidate=False)
    shopee.configure_prepare_transport_factory(lambda _region: fake.transport())
    seed, request = _shopee_seed_and_request(
        fake, with_global_create=True
    )
    result = shopee.prepare_plan_native_target(seed, request)
    command = json.loads(json.dumps(result["command"], sort_keys=True))
    assert command["kind"] == "NEW_GLOBAL"
    assert command["global_create_payload"]["category_id"] == 101157
    assert command["global_create_payload"]["seller_stock"] == [
        {"location_id": "CNZ", "stock": 200}
    ]
    assert command["global_create_payload"]["image"] == {
        "image_id_list": []
    }
    assert command["global_model_payload"]["global_model"][0][
        "global_model_sku"
    ] == "0954"
    assert result["external_writes_performed"] == []
    assert "shop-token" not in repr(result)


def test_shopee_absent_global_without_exact_create_facts_is_content_blocked():
    fake = ShopeePrepareFake(candidate=False)
    shopee.configure_prepare_transport_factory(lambda _region: fake.transport())
    seed, request = _shopee_seed_and_request(fake)
    result = shopee.prepare_plan_native_target(seed, request)
    assert result["classification"] == "BLOCKED_CAPABILITY"
    assert result["reason_category"] == "CONTENT"
    assert result["reason_code"] == "shopee_global_create_facts_missing"
    assert result["external_writes_performed"] == []


@pytest.mark.parametrize(
    "list_fault",
    [
        {
            "error": "",
            "response": {
                "global_item_list": [],
                "total_count": True,
                "has_next_page": False,
            },
        },
        {
            "error": "",
            "response": {
                "global_item_list": [{"global_item_id": 9}],
                "total_count": 2,
                "has_next_page": False,
            },
        },
        {
            "error": "",
            "response": {
                "global_item_list": [{"global_item_id": 9}, False],
                "total_count": 2,
                "has_next_page": False,
            },
        },
    ],
)
def test_shopee_global_scan_shape_or_completeness_fault_is_prewrite(
    list_fault,
):
    fake = ShopeePrepareFake(list_fault=list_fault)
    shopee.configure_prepare_transport_factory(lambda _region: fake.transport())
    seed, request = _shopee_seed_and_request(fake)
    with pytest.raises(shopee.ShopeeOneClickPrepareBlocked) as error:
        shopee.prepare_plan_native_target(seed, request)
    assert error.value.classification == "BLOCKED_CAPABILITY"
    assert error.value.reason_category == "CAPABILITY"
    assert error.value.reason_code == (
        "shopee_official_prepare_proof_unavailable"
    )


def _shopee_dispatch_command():
    fake = ShopeePrepareFake()
    shopee.configure_prepare_transport_factory(lambda _region: fake.transport())
    seed, request = _shopee_seed_and_request(fake)
    return shopee.prepare_plan_native_target(seed, request)["command"]


def _shopee_new_global_dispatch_command(*, image_count=1):
    fake = ShopeePrepareFake(candidate=False)
    shopee.configure_prepare_transport_factory(lambda _region: fake.transport())
    seed, request = _shopee_seed_and_request(
        fake,
        with_global_create=True,
        image_count=image_count,
    )
    return shopee.prepare_plan_native_target(seed, request)["command"]


def _verified_shopee_readback():
    return {
        "verified": True,
        "manual_review_required": True,
        "derived_translation_status": "warning",
        "derived_image_status": "warning",
        "matched_rule_ids": [
            "copy:language_signal_weak",
            "image:linked_count_verified_order_unverifiable",
        ],
        "observation_evidence_digest": "b" * 64,
    }


def _created_global_evidence(image_ids):
    return {
        "verified": True,
        "global_model_id": "7",
        "tier_index": [0],
        "image_snapshot_digest": shopee._image_id_snapshot_digest(
            image_ids
        ),
        "image_count": len(image_ids),
        "image_outcome": {
            "manual_review_required": True,
            "matched_rule_ids": [
                "global_image:rehosted_order_unverifiable"
            ],
            "global_image_status": "warning",
            "global_image_verification_scope": (
                "linked_count_verified_order_unverifiable"
            ),
            "global_image_approved_order_exact": False,
            "evidence_digest": "c" * 64,
        },
    }


def _runtime_image() -> shopee.ShopeePreparedImage:
    return shopee.ShopeePreparedImage(
        content=b"\xff\xd8\xfffixture-jpeg",
        media_type="image/jpeg",
        suffix=".jpg",
    )


def test_shopee_new_global_json_restart_exact_writes_then_regional_success():
    command = json.loads(
        json.dumps(_shopee_new_global_dispatch_command(), sort_keys=True)
    )
    calls = []
    progress = []

    def upload(url, position):
        calls.append(("upload", position, url))
        return {"image_info": {"image_id": f"image-{position}"}}

    def add_global(body):
        calls.append(("add", deepcopy(body)))
        return {"error": "", "response": {"global_item_id": 9}}

    def init_model(global_item_id, body):
        calls.append(("init", global_item_id, deepcopy(body)))
        return {"error": "", "response": {}}

    shopee.configure_runtime_transport_factory(
        lambda: shopee.ShopeeRuntimeTransport(
            verify_pre_dispatch=lambda _command: True,
            prepare_image=lambda _url, _position: _runtime_image(),
            upload_image=upload,
            add_global_item=add_global,
            init_global_model=init_model,
            verify_created_global=lambda _global_id, _command: (
                _created_global_evidence(["image-1"])
            ),
            regional_publish=lambda body: (
                calls.append(("regional", deepcopy(body)))
                or {"accepted": True, "external_id": "88"}
            ),
            readback=lambda _external_id, _command: (
                _verified_shopee_readback()
            ),
        )
    )
    result = shopee.dispatch_plan_native_target(
        _request(
            command,
            recorder=lambda _request, writes, external_id, evidence: (
                progress.append((writes, external_id, deepcopy(evidence)))
            ),
        )
    )
    assert result["canonical_status"] == "SUCCEEDED_MANUAL_REVIEW"
    assert result["external_writes"] == (
        shopee.IMAGE_UPLOAD_WRITE,
        shopee.GLOBAL_WRITE,
        shopee.GLOBAL_MODEL_WRITE,
        shopee.REGIONAL_WRITE,
    )
    assert [row[0] for row in calls] == [
        "upload",
        "add",
        "init",
        "regional",
    ]
    assert calls[1][1]["image"]["image_id_list"] == ["image-1"]
    assert calls[2][2]["global_item_id"] == 9
    assert calls[3][1]["global_item_id"] == 9
    assert result["evidence"]["image_upload_invocation_count"] == 1
    assert len(result["evidence"]["image_upload_evidence_digest"]) == 64
    assert progress[-1][0] == result["external_writes"]


def test_shopee_new_global_runtime_reuses_exact_unique_master_without_global_write():
    command = _shopee_new_global_dispatch_command()
    evidence = _created_global_evidence(["stable-image-1"])
    evidence["global_item_id"] = "9"
    shopee.configure_runtime_transport_factory(
        lambda: shopee.ShopeeRuntimeTransport(
            verify_pre_dispatch=lambda _command: True,
            resolve_existing_global=lambda _command: evidence,
            prepare_image=lambda *_args: pytest.fail("must not download"),
            upload_image=lambda *_args: pytest.fail("must not upload"),
            add_global_item=lambda _body: pytest.fail("must not create"),
            init_global_model=lambda *_args: pytest.fail("must not init"),
            verify_created_global=lambda *_args: pytest.fail(
                "must not verify a new create"
            ),
            regional_publish=lambda body: {
                "accepted": True,
                "external_id": "88",
            },
            readback=lambda *_args: _verified_shopee_readback(),
        )
    )
    result = shopee.dispatch_plan_native_target(_request(command))
    assert result["external_writes"] == (shopee.REGIONAL_WRITE,)
    assert result["evidence"]["image_upload_invocation_count"] == 0
    assert result["canonical_status"] == "SUCCEEDED_MANUAL_REVIEW"


@pytest.mark.parametrize("fail_position", [1, 2])
def test_shopee_new_global_prefetch_fault_is_zero_marketplace_write(
    fail_position,
):
    command = _shopee_new_global_dispatch_command(image_count=2)
    uploads = []

    def prepare_image(url, position):
        if position == fail_position:
            raise TimeoutError("source image unavailable")
        return _runtime_image()

    shopee.configure_runtime_transport_factory(
        lambda: shopee.ShopeeRuntimeTransport(
            verify_pre_dispatch=lambda _command: True,
            prepare_image=prepare_image,
            upload_image=lambda payload, position: (
                uploads.append((payload, position))
                or {"image_info": {"image_id": f"image-{position}"}}
            ),
            add_global_item=lambda _body: pytest.fail("must not create"),
            init_global_model=lambda *_args: pytest.fail("must not init"),
            verify_created_global=lambda *_args: pytest.fail(
                "must not read back"
            ),
            regional_publish=lambda _body: pytest.fail("must not publish"),
            readback=lambda *_args: pytest.fail("must not read back"),
        )
    )
    with pytest.raises(shopee.ShopeeOneClickPreDispatchError):
        shopee.dispatch_plan_native_target(_request(command))
    assert uploads == []


class _ImageResponse:
    def __init__(
        self,
        payload=b"\xff\xd8\xfffixture-jpeg",
        *,
        content_type="image/jpeg",
    ):
        self.payload = payload
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(payload)),
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        return self.payload[:limit]


class _ImageOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = 0

    def open(self, *_args, **_kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


def test_shopee_image_downloader_rejects_private_before_network(monkeypatch):
    opener = _ImageOpener(_ImageResponse())
    monkeypatch.setattr(
        shopee.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (None, None, None, None, ("127.0.0.1", 443))
        ],
    )
    with pytest.raises(ValueError, match="not public"):
        shopee._download_public_https_image(
            "https://localhost/image.jpg", opener=opener
        )
    assert opener.calls == 0


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (_ImageResponse(content_type="text/html"), None),
        (_ImageResponse(content_type="image/svg+xml"), None),
        (_ImageResponse(payload=b"x" * 11), None),
        (
            None,
            urllib.error.HTTPError(
                "https://assets.example/image.jpg",
                302,
                "redirect disabled",
                {},
                None,
            ),
        ),
    ],
)
def test_shopee_image_downloader_rejects_redirect_nonimage_and_oversize(
    monkeypatch, response, error
):
    monkeypatch.setattr(
        shopee.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (None, None, None, None, ("93.184.216.34", 443))
        ],
    )
    opener = _ImageOpener(response, error)
    with pytest.raises((ValueError, urllib.error.HTTPError)):
        shopee._download_public_https_image(
            "https://assets.example/image.jpg",
            max_bytes=10,
            opener=opener,
        )


@pytest.mark.parametrize(
    ("content_type", "payload", "suffix"),
    [
        ("image/jpeg", b"\xff\xd8\xffjpeg", ".jpg"),
        ("image/png", b"\x89PNG\r\n\x1a\npng", ".png"),
        ("image/webp", b"RIFF\x04\x00\x00\x00WEBPdata", ".webp"),
    ],
)
def test_shopee_image_downloader_binds_magic_mime_and_upload_suffix(
    monkeypatch, content_type, payload, suffix
):
    monkeypatch.setattr(
        shopee.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (None, None, None, None, ("93.184.216.34", 443))
        ],
    )
    image = shopee._download_public_https_image(
        "https://assets.example/image",
        max_bytes=100,
        opener=_ImageOpener(
            _ImageResponse(payload=payload, content_type=content_type)
        ),
    )
    assert image.media_type == content_type
    assert image.suffix == suffix
    assert image.content == payload


@pytest.mark.parametrize(
    ("content_type", "payload"),
    [
        ("image/jpeg", b"\x89PNG\r\n\x1a\npng"),
        ("image/png", b"\xff\xd8\xffjpeg"),
        ("image/gif", b"GIF89a"),
        ("image/avif", b"\x00\x00\x00\x18ftypavif"),
    ],
)
def test_shopee_image_downloader_rejects_mime_magic_mismatch_and_unsupported(
    monkeypatch, content_type, payload
):
    monkeypatch.setattr(
        shopee.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (None, None, None, None, ("93.184.216.34", 443))
        ],
    )
    with pytest.raises(ValueError):
        shopee._download_public_https_image(
            "https://assets.example/image",
            max_bytes=100,
            opener=_ImageOpener(
                _ImageResponse(payload=payload, content_type=content_type)
            ),
        )


@pytest.mark.parametrize(
    ("fault_step", "expected_writes", "unknown"),
    [
        ("upload_transport", (shopee.IMAGE_UPLOAD_WRITE,), True),
        ("upload_parse", (shopee.IMAGE_UPLOAD_WRITE,), True),
        (
            "add_transport",
            (shopee.IMAGE_UPLOAD_WRITE, shopee.GLOBAL_WRITE),
            True,
        ),
        (
            "add_parse",
            (shopee.IMAGE_UPLOAD_WRITE, shopee.GLOBAL_WRITE),
            True,
        ),
        (
            "model_transport",
            (
                shopee.IMAGE_UPLOAD_WRITE,
                shopee.GLOBAL_WRITE,
                shopee.GLOBAL_MODEL_WRITE,
            ),
            True,
        ),
        (
            "model_parse",
            (
                shopee.IMAGE_UPLOAD_WRITE,
                shopee.GLOBAL_WRITE,
                shopee.GLOBAL_MODEL_WRITE,
            ),
            True,
        ),
        (
            "global_readback_transport",
            (
                shopee.IMAGE_UPLOAD_WRITE,
                shopee.GLOBAL_WRITE,
                shopee.GLOBAL_MODEL_WRITE,
            ),
            True,
        ),
        (
            "global_readback_mismatch",
            (
                shopee.IMAGE_UPLOAD_WRITE,
                shopee.GLOBAL_WRITE,
                shopee.GLOBAL_MODEL_WRITE,
            ),
            False,
        ),
        (
            "regional_transport",
            (
                shopee.IMAGE_UPLOAD_WRITE,
                shopee.GLOBAL_WRITE,
                shopee.GLOBAL_MODEL_WRITE,
                shopee.REGIONAL_WRITE,
            ),
            True,
        ),
    ],
)
def test_shopee_new_global_each_write_boundary_is_truthful(
    fault_step, expected_writes, unknown
):
    command = _shopee_new_global_dispatch_command()

    def upload(_url, _position):
        if fault_step == "upload_transport":
            raise TimeoutError("unknown")
        if fault_step == "upload_parse":
            return {}
        return {"image_info": {"image_id": "image-1"}}

    def add_global(_body):
        if fault_step == "add_transport":
            raise TimeoutError("unknown")
        if fault_step == "add_parse":
            return {"error": "", "response": {}}
        return {"error": "", "response": {"global_item_id": 9}}

    def init_model(_global_item_id, _body):
        if fault_step == "model_transport":
            raise TimeoutError("unknown")
        if fault_step == "model_parse":
            return {"error": "malformed"}
        return {"error": "", "response": {}}

    def verify_created(_global_item_id, _command):
        if fault_step == "global_readback_transport":
            raise TimeoutError("unknown")
        if fault_step == "global_readback_mismatch":
            value = _created_global_evidence(["different-image"])
            return value
        return _created_global_evidence(["image-1"])

    def regional(_body):
        if fault_step == "regional_transport":
            raise TimeoutError("unknown")
        return {"accepted": True, "external_id": "88"}

    shopee.configure_runtime_transport_factory(
        lambda: shopee.ShopeeRuntimeTransport(
            verify_pre_dispatch=lambda _command: True,
            prepare_image=lambda _url, _position: _runtime_image(),
            upload_image=upload,
            add_global_item=add_global,
            init_global_model=init_model,
            verify_created_global=verify_created,
            regional_publish=regional,
            readback=lambda *_args: _verified_shopee_readback(),
        )
    )
    with pytest.raises(shopee.ShopeeOneClickDispatchError) as error:
        shopee.dispatch_plan_native_target(_request(command))
    assert error.value.external_writes == expected_writes
    assert error.value.dispatch_outcome_unknown is unknown


def test_shopee_existing_global_dispatch_has_only_regional_write():
    command = json.loads(json.dumps(_shopee_dispatch_command()))
    progress = []
    shopee.configure_runtime_transport_factory(
        lambda: shopee.ShopeeRuntimeTransport(
            verify_pre_dispatch=lambda _command: True,
            regional_publish=lambda _body: {
                "accepted": True,
                "external_id": "88",
            },
            readback=lambda _external_id, _command: (
                _verified_shopee_readback()
            ),
        )
    )
    result = shopee.dispatch_plan_native_target(
        _request(
            command,
            recorder=lambda _request, writes, external_id, _evidence: (
                progress.append((writes, external_id))
            ),
        )
    )
    assert result["canonical_status"] == "SUCCEEDED_MANUAL_REVIEW"
    assert result["external_writes"] == (shopee.REGIONAL_WRITE,)
    assert result["evidence"]["manual_review"] is True
    assert result["evidence"]["rule_ids"]
    assert progress == [
        ((shopee.REGIONAL_WRITE,), "shopee_regional_publish")
    ]


def test_shopee_prewrite_drift_is_zero_and_postwrite_fault_retains_write():
    command = _shopee_dispatch_command()
    shopee.configure_runtime_transport_factory(
        lambda: shopee.ShopeeRuntimeTransport(
            verify_pre_dispatch=lambda _command: False,
            regional_publish=lambda _body: pytest.fail("must not write"),
            readback=lambda *_args: False,
        )
    )
    with pytest.raises(shopee.ShopeeOneClickPreDispatchError):
        shopee.dispatch_plan_native_target(_request(command))
    shopee.configure_runtime_transport_factory(
        lambda: shopee.ShopeeRuntimeTransport(
            verify_pre_dispatch=lambda _command: True,
            regional_publish=lambda _body: (_ for _ in ()).throw(
                TimeoutError("unknown")
            ),
            readback=lambda *_args: False,
        )
    )
    with pytest.raises(shopee.ShopeeOneClickDispatchError) as error:
        shopee.dispatch_plan_native_target(_request(command))
    assert error.value.external_writes == (shopee.REGIONAL_WRITE,)
    assert error.value.dispatch_outcome_unknown is True


def test_shopee_regional_hard_and_observation_readback():
    command = _shopee_dispatch_command()
    approved = command["approved"]
    item = {
        "item_id": 88,
        "item_status": "NORMAL",
        "item_sku": "0954",
        "has_model": True,
        "item_name": "Tampalan dinding PVC",
        "description": "Penerangan produk yang lengkap dalam Bahasa Melayu",
        "price_info": [{"currency": "MYR", "original_price": "33"}],
        "logistic_info": [{"logistic_id": 1, "enabled": True}],
        "image": {
            "image_url_list": ["https://regional-rehost.example/one"]
        },
    }
    models = [{
        "model_id": 99,
        "model_sku": "0954",
        "tier_index": [0],
    }]
    evidence = shopee._regional_readback_evidence(
        item=item,
        models=models,
        resolved_global_item_id=9,
        command=command,
        item_id="88",
    )
    assert evidence["verified"] is True
    assert evidence["manual_review_required"] in {True, False}
    assert len(evidence["observation_evidence_digest"]) == 64
    bad = deepcopy(item)
    bad["price_info"].append(
        {"currency": "USD", "original_price": "not-a-number"}
    )
    with pytest.raises(shopee.ShopeeOneClickPreDispatchError):
        shopee._regional_readback_evidence(
            item=bad,
            models=models,
            resolved_global_item_id=9,
            command=command,
            item_id="88",
        )
    assert approved["listing_copy"]["approved_master_digest"] != approved[
        "listing_copy"
    ]["approved_copy_digest"]
