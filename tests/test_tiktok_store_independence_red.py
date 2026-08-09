"""Permanent regressions for independent TikTok store drafts and multi-SKU identity."""

import pytest

from modules.miaoshou import oneclick_release as miaoshou
from modules.miaoshou import collectbox_claim as claim_contract
from domains.channel_operations import tiktok_publisher
from domains.channel_operations import collectbox_action_adapters
from shared_platform import collectbox_action
from tests.test_oneclick_miaoshou_direct_store import DirectStoreFake, _plan_payload


def test_opaque_multisku_keys_bind_by_exact_miaoshou_property_labels():
    variants = [
        "D803;60cm宽x3米长【塑封出口包装】",
        "D805;60cm宽x3米长【塑封出口包装】",
        "D811;60cm宽x3米长【塑封出口包装】",
    ]
    detail = {
        "skuPropertyList": [
            {
                "attrValueList": [
                    {"attrValueId": "7fc56270e7", "attrValue": "D803"},
                    {"attrValueId": "9d5ed678fe", "attrValue": "D805"},
                    {"attrValueId": "0d61f8370c", "attrValue": "D811"},
                ]
            },
            {
                "attrValueList": [
                    {
                        "attrValueId": "ae79c59131",
                        "attrValue": "60cm宽x3米长【塑封出口包装】",
                    }
                ]
            },
        ],
        "skuMap": {
            "7fc56270e7;ae79c59131": {"itemNum": "899978827487"},
            "9d5ed678fe;ae79c59131": {"itemNum": "899978827487"},
            "0d61f8370c;ae79c59131": {"itemNum": "899978827487"},
        },
    }
    expected = {
        "selected_sku_keys": variants,
        "model_skus": {
            variants[0]: "0960",
            variants[1]: "0961",
            variants[2]: "0962",
        },
    }

    assert miaoshou._approved_variant_key_bindings(detail, expected) == {
        variants[0]: "7fc56270e7;ae79c59131",
        variants[1]: "9d5ed678fe;ae79c59131",
        variants[2]: "0d61f8370c;ae79c59131",
    }


def test_homebloom_target_is_a_first_class_independent_publish_target():
    target = {
        "target_label": "tiktok:HB_PH",
        "detail_id": "91001",
        "shop_id": "15173238",
        "expected_price": "90",
        "expected_weight_kg": "0.1",
        "expected_package_cm": ["20", "20", "3"],
        "expected_sku_parcels": {},
        "expected_currency": "PHP",
        "expected_category_id": "600338",
        "category_evidence_digest": "a" * 64,
        "target_identity_digest": "b" * 64,
        "publish_identity_digest": "c" * 64,
        "receipt_digest": "d" * 64,
    }
    snapshot = {
        "schema_version": "approved-tiktok-publish-snapshot/v2",
        "offer_id": "3838619319",
        "plan_id": "omnichannel:homebloom-only",
        "product_revision": 1,
        "payload_digest": "e" * 64,
        "targets": [target],
        "unavailable_targets": [],
    }

    approved = tiktok_publisher._validate_snapshot(snapshot)

    assert approved["targets"] == [target]


def test_one_missing_store_identity_does_not_block_another_store_snapshot():
    ready = {
        "target_label": "tiktok:LH_MY",
        "detail_id": "91002",
        "shop_id": "13295169",
        "expected_price": "20",
        "expected_weight_kg": "0.1",
        "expected_package_cm": ["20", "20", "3"],
        "expected_sku_parcels": {},
        "expected_currency": "MYR",
        "expected_category_id": "600338",
        "category_evidence_digest": "a" * 64,
        "target_identity_digest": "b" * 64,
        "publish_identity_digest": "c" * 64,
        "receipt_digest": "d" * 64,
    }
    snapshot = {
        "schema_version": "approved-tiktok-publish-snapshot/v2",
        "offer_id": "3838619319",
        "plan_id": "omnichannel:partial-identities",
        "product_revision": 1,
        "payload_digest": "e" * 64,
        "targets": [ready],
        "unavailable_targets": [
            {
                "target_label": "tiktok:LH_PH",
                "reason_code": "draft_identity_unavailable",
            }
        ],
    }

    class Transport:
        def read_draft(self, target):
            return {"target": target}

        def draft_matches(self, target, draft):
            return True

        def save_approved_draft(self, target, draft):
            raise AssertionError("exact draft must not be rewritten")

        def submit(self, target):
            return {"result": "success", "code": "200", "message": "Success"}

    receipt = tiktok_publisher.TikTokPublisher(Transport()).publish(snapshot)

    assert [(row["target_label"], row["outcome"]) for row in receipt["targets"]] == [
        ("tiktok:LH_MY", "ACCEPTED"),
        ("tiktok:LH_PH", "NOT_ATTEMPTED"),
    ]


def test_claimed_detail_identity_survives_later_target_validation_failure():
    target = "tiktok:LH_PH"
    fake = DirectStoreFake(target)
    fake.detail["skuMap"] = {
        "opaque-one": {"itemNum": "source-offer"},
        "opaque-two": {"itemNum": "source-offer"},
    }

    def post(path, body):
        if path == miaoshou.SHOP_CLAIM_PATH:
            return {"result": "success"}
        return fake.post(path, body)

    try:
        miaoshou.prepare_selected_platform_collectbox(
            platform="tiktok",
            common_detail_id="7",
            initial_platform_detail_id="77",
            initial_claim_written=True,
            approved_plan_payload=_plan_payload(target),
            approved_targets=(target,),
            post=post,
            web_post=fake.web_post,
        )
    except miaoshou.MiaoshouCollectBoxPreparationError as error:
        assert error.target_detail_identities == (
            {
                "target_label": target,
                "detail_id": "77",
                "shop_id": "7676267",
            },
        )
    else:  # pragma: no cover - the fixture is intentionally invalid
        raise AssertionError("invalid claimed draft must fail preparation")


def test_adapter_persists_claimed_identity_when_later_preparation_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    target = "tiktok:LH_PH"
    detail_id = 91003

    def claim(request):
        identity_digest = claim_contract._digest(
            {
                "identity_kind": "miaoshou_platform_collectbox_detail",
                "platform": "tiktok",
                "platform_detail_id": detail_id,
            }
        )
        result = claim_contract.PlatformClaimResult(
            platform="tiktok",
            status=claim_contract.ACCEPTED,
            attempt_count=1,
            dispatch_invoked=True,
            outcome_unknown=False,
            retry_safe=False,
            reconciliation_required=False,
            write_class="miaoshou:collectbox:claim:tiktok",
            write_outcome="ACCEPTED",
            reason_code="accepted",
            platform_detail_id=detail_id,
            platform_detail_identity_digest=identity_digest,
        )
        return claim_contract.PlatformClaimReceipt(
            request_digest=request.request_digest,
            common_detail_identity_digest=(
                request.common_detail_identity_digest
            ),
            result=result,
        )

    def prepare(**_kwargs):
        raise miaoshou.MiaoshouCollectBoxPreparationError(
            "approved multi-SKU identity is invalid",
            writes=("miaoshou:collectbox:claim:tiktok",),
            write_count=1,
            target_results=((target, "RECONCILIATION_REQUIRED"),),
            target_detail_identities=(
                {
                    "target_label": target,
                    "detail_id": str(detail_id),
                    "shop_id": "7676267",
                },
            ),
        )

    monkeypatch.setattr(
        collectbox_action_adapters,
        "claim_common_collectbox_platform",
        claim,
    )
    targets = (target,)
    request = collectbox_action.CollectBoxPlatformRequest(
        action_id="collectbox-action:test",
        plan_id="omnichannel:test",
        platform="TIKTOK",
        common_collect_box_detail_id="7",
        common_collectbox_identity_digest=(
            collectbox_action.common_collectbox_identity_digest(
                "omnichannel:test", "7"
            )
        ),
        payload_digest="a" * 64,
        targets_digest=collectbox_action._digest(list(targets)),
        idempotency_key="test-tiktok-claim",
        approved_plan_payload={"offer_id": "3838619319"},
        approved_targets=targets,
    )

    result = collectbox_action_adapters._execute_known_collectbox_platform(
        request,
        collectbox_action_adapters._contract(),
        platform="tiktok",
        prepare=prepare,
    )

    assert result.status == collectbox_action.RECONCILIATION_REQUIRED
    assert [
        identity.internal_payload()
        for identity in result.target_detail_identities
    ] == [
        {
            "schema_version": "collectbox-target-detail-identity/v1",
            "target_label": target,
            "detail_id": str(detail_id),
            "shop_id": "7676267",
            "identity_digest": collectbox_action._digest(
                {
                    "schema_version": "collectbox-target-detail-identity/v1",
                    "target_label": target,
                    "detail_id": str(detail_id),
                    "shop_id": "7676267",
                }
            ),
        }
    ]
