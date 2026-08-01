import hashlib
import json

import pytest

from shared_platform.collectbox_action import (
    ALREADY_PRESENT,
    FAILED,
    RECONCILIATION_REQUIRED,
    REPAIRED_SUCCEEDED,
    SUCCEEDED,
    CollectBoxActionStore,
    CollectBoxPlatformRequest,
    CollectBoxPlatformResult,
    CollectBoxTargetOutcome,
    common_collectbox_identity_digest,
)
from modules.miaoshou.collectbox_claim import (
    ACCEPTED,
    PlatformClaimReceipt,
    PlatformClaimResult,
)


def _detail_digest(detail: str) -> str:
    return hashlib.sha256(detail.encode("utf-8")).hexdigest()


def _canonical_digest(value):
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_real_channel_adapter_preserves_six_tiktok_target_outcomes(
    monkeypatch,
):
    from domains.channel_operations import collectbox_action_adapters

    targets = (
        "tiktok:LH_PH",
        "tiktok:LH_MY",
        "tiktok:LH_TH",
        "tiktok:LH_VN",
        "tiktok:MX",
        "tiktok:GB",
    )
    failure_digest = _detail_digest("redacted price mismatch")
    category_digest = _detail_digest("redacted category evidence missing")

    def claim(request):
        result = PlatformClaimResult(
            platform="tiktok",
            status=ACCEPTED,
            attempt_count=1,
            dispatch_invoked=True,
            outcome_unknown=False,
            retry_safe=False,
            reconciliation_required=False,
            write_class="miaoshou:collectbox:claim:tiktok",
            write_outcome="ACCEPTED",
            reason_code="accepted",
            platform_detail_id=71001,
            platform_detail_identity_digest=_canonical_digest(
                {
                    "identity_kind": "miaoshou_platform_collectbox_detail",
                    "platform": "tiktok",
                    "platform_detail_id": 71001,
                }
            ),
        )
        return PlatformClaimReceipt(
            request_digest=request.request_digest,
            common_detail_identity_digest=(
                request.common_detail_identity_digest
            ),
            result=result,
        )

    prepared_outcomes = [
        {
            "target_label": "tiktok:LH_PH",
            "status": SUCCEEDED,
            "error_code": None,
            "detail_digest": None,
        },
        {
            "target_label": "tiktok:LH_MY",
            "status": REPAIRED_SUCCEEDED,
            "error_code": None,
            "detail_digest": None,
        },
        {
            "target_label": "tiktok:LH_TH",
            "status": FAILED,
            "error_code": "approved_price_readback_mismatch",
            "detail_digest": failure_digest,
        },
        {
            "target_label": "tiktok:LH_VN",
            "status": SUCCEEDED,
            "error_code": None,
            "detail_digest": None,
        },
        {
            "target_label": "tiktok:MX",
            "status": SUCCEEDED,
            "error_code": None,
            "detail_digest": None,
        },
        {
            "target_label": "tiktok:GB",
            "status": FAILED,
            "error_code": "category_not_approved",
            "detail_digest": category_digest,
        },
    ]
    monkeypatch.setattr(
        collectbox_action_adapters,
        "claim_common_collectbox_platform",
        claim,
    )
    monkeypatch.setattr(
        collectbox_action_adapters,
        "prepare_selected_platform_collectbox",
        lambda **_kwargs: {
            "primary_platform_detail_id": 71001,
            "target_count": len(targets),
            "platform_detail_count": len(targets),
            "external_writes": (
                "miaoshou:collectbox:claim:tiktok",
                "miaoshou:collectbox:tiktok:detail:update",
            ),
            "external_write_count": 7,
            "target_results": prepared_outcomes,
            "checks": {
                "approved_targets_exact": True,
                "approved_prices_exact": True,
                "approved_content_exact": True,
                "readback_exact": False,
                "publish_not_invoked": True,
            },
        },
    )
    plan_id = "omnichannel:real-adapter-target-outcomes"
    request = CollectBoxPlatformRequest(
        action_id="collectbox-action-real-adapter",
        plan_id=plan_id,
        platform="TIKTOK",
        common_collect_box_detail_id="3846511157",
        common_collectbox_identity_digest=(
            common_collectbox_identity_digest(plan_id, "3846511157")
        ),
        payload_digest="a" * 64,
        targets_digest=_canonical_digest(list(targets)),
        idempotency_key="c" * 64,
        approved_plan_payload={"product_revision": 31},
        approved_targets=targets,
    )

    result = collectbox_action_adapters.execute_collectbox_platform(request)

    assert result.target_statuses == ()
    assert [outcome.public_payload() for outcome in result.target_outcomes] == (
        prepared_outcomes
    )


def _plan():
    return {
        "plan_id": "omnichannel:target-outcomes",
        "product_id": "3846511157",
        "payload_digest": "a" * 64,
        "payload": {"product_revision": 31},
        "targets": [
            "tiktok:LH_PH",
            "tiktok:LH_MY",
            "tiktok:MX",
            "tiktok:GB",
        ],
        "status": "APPROVED",
        "approval": {"status": "APPROVED", "approved_by": "Kyle"},
    }


def _shopee_already_present():
    return CollectBoxPlatformResult(
        status=SUCCEEDED,
        outcome=ALREADY_PRESENT,
        platform_detail_id="71002",
        external_writes=(),
        external_write_count=0,
    )


def test_mixed_tiktok_target_outcomes_are_terminal_ordered_and_redacted(
    tmp_path,
):
    raw_failure_detail = (
        "raw response included price=99.00 and category=forbidden"
    )
    failure_digest = _detail_digest(raw_failure_detail)

    def adapter(request):
        if request.platform == "SHOPEE":
            return _shopee_already_present()
        return CollectBoxPlatformResult(
            status=RECONCILIATION_REQUIRED,
            external_writes=(
                "miaoshou:collectbox:claim:tiktok",
                "miaoshou:collectbox:tiktok:detail:update:tiktok:LH_PH",
                "miaoshou:collectbox:tiktok:detail:update:tiktok:LH_MY",
                "miaoshou:collectbox:tiktok:detail:update:tiktok:MX",
                "miaoshou:collectbox:tiktok:detail:update:tiktok:GB",
            ),
            external_write_count=5,
            target_outcomes=(
                CollectBoxTargetOutcome(
                    target_label="tiktok:LH_PH",
                    status=SUCCEEDED,
                ),
                CollectBoxTargetOutcome(
                    target_label="tiktok:LH_MY",
                    status=FAILED,
                    error_code="target_readback_mismatch",
                    detail_digest=failure_digest,
                ),
                CollectBoxTargetOutcome(
                    target_label="tiktok:MX",
                    status=REPAIRED_SUCCEEDED,
                ),
                CollectBoxTargetOutcome(
                    target_label="tiktok:GB",
                    status=SUCCEEDED,
                ),
            ),
            error_category="CHANNEL",
            error_code="one_target_failed",
            error_detail="one approved TikTok target failed",
        )

    database_path = tmp_path / "collectbox.db"
    projection = CollectBoxActionStore(database_path).start(
        plan=_plan(),
        common_collect_box_detail_id="3846511157",
        adapter=adapter,
        now=lambda: 100.0,
        wait=lambda _seconds: None,
    )
    tiktok = next(
        row
        for row in projection["action"]["platforms"]
        if row["platform"] == "TIKTOK"
    )

    assert tiktok["targets"] == [
        {
            "target_label": target,
            "status": RECONCILIATION_REQUIRED,
        }
        for target in _plan()["targets"]
    ]
    assert tiktok["target_outcomes"] == [
        {
            "target_label": "tiktok:LH_PH",
            "status": SUCCEEDED,
            "error_code": None,
            "detail_digest": None,
        },
        {
            "target_label": "tiktok:LH_MY",
            "status": FAILED,
            "error_code": "target_readback_mismatch",
            "detail_digest": failure_digest,
        },
        {
            "target_label": "tiktok:MX",
            "status": REPAIRED_SUCCEEDED,
            "error_code": None,
            "detail_digest": None,
        },
        {
            "target_label": "tiktok:GB",
            "status": SUCCEEDED,
            "error_code": None,
            "detail_digest": None,
        },
    ]
    assert tiktok["external_writes"] == {
        "count": 5,
        "classes": [
            "miaoshou:collectbox:claim:tiktok",
            "miaoshou:collectbox:tiktok:detail:update:tiktok:LH_PH",
            "miaoshou:collectbox:tiktok:detail:update:tiktok:LH_MY",
            "miaoshou:collectbox:tiktok:detail:update:tiktok:MX",
            "miaoshou:collectbox:tiktok:detail:update:tiktok:GB",
        ],
    }
    serialized = json.dumps(projection, ensure_ascii=False)
    assert raw_failure_detail not in serialized
    assert "price=99.00" not in serialized
    assert "category=forbidden" not in serialized
    durable_bytes = database_path.read_bytes()
    assert raw_failure_detail.encode("utf-8") not in durable_bytes
    assert b"price=99.00" not in durable_bytes
    assert b"category=forbidden" not in durable_bytes


@pytest.mark.parametrize(
    "outcome",
    [
        CollectBoxTargetOutcome(
            target_label="tiktok:LH_PH",
            status=FAILED,
            error_code="target_failed",
            detail_digest="b" * 64,
        ),
    ],
)
def test_target_outcome_is_a_json_free_typed_value(outcome):
    assert outcome.target_label == "tiktok:LH_PH"
    assert outcome.status == FAILED


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "target_label": "tiktok:LH_PH",
            "status": "RECONCILIATION_REQUIRED",
        },
        {
            "target_label": "tiktok:LH_PH",
            "status": FAILED,
            "error_code": "target failed: raw response",
            "detail_digest": "b" * 64,
        },
        {
            "target_label": "tiktok:LH_PH",
            "status": FAILED,
            "error_code": "target_failed",
        },
        {
            "target_label": "tiktok:LH_PH",
            "status": SUCCEEDED,
            "error_code": "should_be_null",
            "detail_digest": "b" * 64,
        },
    ],
)
def test_target_outcome_rejects_invalid_or_leaky_terminal_shape(kwargs):
    with pytest.raises(ValueError):
        CollectBoxTargetOutcome(**kwargs)


def test_target_outcome_membership_or_order_drift_is_not_persisted(tmp_path):
    def adapter(request):
        if request.platform == "SHOPEE":
            return _shopee_already_present()
        reversed_targets = tuple(reversed(request.approved_targets))
        return CollectBoxPlatformResult(
            status=RECONCILIATION_REQUIRED,
            external_writes=("miaoshou:collectbox:claim:tiktok",),
            external_write_count=None,
            target_outcomes=(
                CollectBoxTargetOutcome(
                    target_label=reversed_targets[0],
                    status=FAILED,
                    error_code="target_failed",
                    detail_digest="b" * 64,
                ),
                *(
                    CollectBoxTargetOutcome(
                        target_label=target,
                        status=SUCCEEDED,
                    )
                    for target in reversed_targets[1:]
                ),
            ),
            error_category="UNKNOWN",
            error_code="result_unknown",
            error_detail="result unknown",
        )

    projection = CollectBoxActionStore(tmp_path / "collectbox.db").start(
        plan=_plan(),
        common_collect_box_detail_id="3846511157",
        adapter=adapter,
        now=lambda: 100.0,
        wait=lambda _seconds: None,
    )
    tiktok = next(
        row
        for row in projection["action"]["platforms"]
        if row["platform"] == "TIKTOK"
    )

    assert [row["target_label"] for row in tiktok["targets"]] == _plan()[
        "targets"
    ]
    assert all(
        row["status"] == RECONCILIATION_REQUIRED
        for row in tiktok["targets"]
    )
    assert not any("error_code" in row for row in tiktok["targets"])
    assert tiktok["target_outcomes"] == []


def test_legacy_target_statuses_remain_projection_compatible(tmp_path):
    plan = _plan()

    def adapter(request):
        if request.platform == "SHOPEE":
            return _shopee_already_present()
        return CollectBoxPlatformResult(
            status=SUCCEEDED,
            outcome=ALREADY_PRESENT,
            platform_detail_id="71001",
            external_writes=(),
            external_write_count=0,
            target_statuses=tuple(
                (target, SUCCEEDED) for target in request.approved_targets
            ),
        )

    projection = CollectBoxActionStore(tmp_path / "collectbox.db").start(
        plan=plan,
        common_collect_box_detail_id="3846511157",
        adapter=adapter,
        now=lambda: 100.0,
        wait=lambda _seconds: None,
    )
    tiktok = next(
        row
        for row in projection["action"]["platforms"]
        if row["platform"] == "TIKTOK"
    )

    assert tiktok["targets"] == [
        {"target_label": target, "status": SUCCEEDED}
        for target in plan["targets"]
    ]
    assert tiktok["target_outcomes"] == []
