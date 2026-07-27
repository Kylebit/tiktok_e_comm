import sqlite3

import pytest

from shared_platform.release_store import (
    RELEASE_TARGET_LABELS,
    ImmutableReleaseError,
    ReleaseAuthorizationError,
    ReleaseStore,
    ReleaseStoreError,
    SkuReservationConflict,
)


def _plan(**overrides):
    payload = {
        "plan_id": "omnichannel:plan-v1",
        "product_id": "3828540231",
        "seller_sku": "0946",
        "product_package_id": "product:3828540231:0946",
        "content_package_id": "content:3828540231:r1",
        "targets": [
            "miaoshou:COMMON",
            "tiktok:LH_PH",
            "shopee:PH",
        ],
        "commercial_scope": {
            "cost_snapshot_id": "cost:3828540231:r1",
            "fx_snapshot_id": "fx:2026-07-26",
            "pricing_rule_version": "sea-v1",
        },
    }
    payload.update(overrides)
    return payload


def _approved_store(tmp_path, *, targets=None):
    store = ReleaseStore(tmp_path / "release.db")
    plan = store.create_plan(_plan(**({"targets": targets} if targets else {})))
    approval = store.approve_plan(
        plan["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=plan["confirmation_token"],
    )
    return store, plan, approval


def test_missing_store_reads_are_side_effect_free_and_allowlist_is_exact(tmp_path):
    path = tmp_path / "missing" / "release.db"
    store = ReleaseStore(path)

    assert store.get_plan("missing") is None
    assert store.get_run("missing") is None
    assert store.active_sku_reservations() == []
    assert store.database_health() == {"exists": False}
    assert store.available_targets() == RELEASE_TARGET_LABELS
    assert len(RELEASE_TARGET_LABELS) == 16
    assert not path.exists()


def test_plan_payload_digest_token_and_sku_reservation_are_immutable(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    created = store.create_plan(
        _plan(
            targets=[
                "shopee:PH",
                "miaoshou:COMMON",
                "tiktok:LH_PH",
            ]
        )
    )
    repeated = store.create_plan(
        _plan(
            targets=[
                "tiktok:LH_PH",
                "shopee:PH",
                "miaoshou:COMMON",
            ]
        )
    )

    assert created["created"] is True
    assert repeated["created"] is False
    assert created["targets"] == [
        "miaoshou:COMMON",
        "tiktok:LH_PH",
        "shopee:PH",
    ]
    assert created["payload_digest"] == repeated["payload_digest"]
    assert created["confirmation_token"] == (
        f"PUBLISH-{created['payload_digest'][:16].upper()}"
    )
    assert store.active_sku_reservations()[0]["sku_key"] == "0946"

    with pytest.raises(ImmutableReleaseError, match="different payload digest"):
        store.create_plan(_plan(content_package_id="content:changed"))

    with pytest.raises(SkuReservationConflict, match="already reserved"):
        store.create_plan(
            _plan(
                plan_id="omnichannel:other-product",
                product_id="9999999999",
                seller_sku="990946",
                product_package_id="product:9999999999:990946",
                content_package_id="content:9999999999:r1",
            )
        )


@pytest.mark.parametrize(
    "approved_by,user_approved,token,error",
    [
        ("Robot", True, "correct", "approved_by must be Kyle"),
        ("Kyle", False, "correct", "user_approved=True"),
        ("Kyle", True, "wrong", "confirmation token"),
    ],
)
def test_approval_requires_kyle_literal_boolean_and_exact_token(
    tmp_path,
    approved_by,
    user_approved,
    token,
    error,
):
    store = ReleaseStore(tmp_path / "release.db")
    plan = store.create_plan(_plan())
    candidate_token = (
        plan["confirmation_token"] if token == "correct" else token
    )

    with pytest.raises(ReleaseAuthorizationError, match=error):
        store.approve_plan(
            plan["plan_id"],
            approved_by=approved_by,
            user_approved=user_approved,
            confirmation_token=candidate_token,
        )


def test_approval_and_run_creation_are_idempotent(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    plan = store.create_plan(_plan())

    with pytest.raises(ReleaseAuthorizationError, match="active Kyle approval"):
        store.start_run(plan["plan_id"])

    first_approval = store.approve_plan(
        plan["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=plan["confirmation_token"],
    )
    repeated_approval = store.approve_plan(
        plan["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=plan["confirmation_token"],
    )
    first_run = store.start_run(plan["plan_id"])
    repeated_run = store.start_run(plan["plan_id"], run_id="ignored-on-repeat")

    assert first_approval["created"] is True
    assert repeated_approval["created"] is False
    assert first_run["run_id"] == repeated_run["run_id"]
    assert first_run["status"] == "PENDING"
    assert [row["target_label"] for row in first_run["targets"]] == plan["targets"]
    assert len({row["idempotency_key"] for row in first_run["targets"]}) == 3
    assert all(row["attempts"] == 0 for row in first_run["targets"])


def test_plan_and_run_support_the_complete_sixteen_target_matrix(tmp_path):
    store, plan, _approval = _approved_store(
        tmp_path,
        targets=list(RELEASE_TARGET_LABELS),
    )

    run = store.start_run(plan["plan_id"])

    assert plan["targets"] == list(RELEASE_TARGET_LABELS)
    assert [row["target_label"] for row in run["targets"]] == list(
        RELEASE_TARGET_LABELS
    )
    assert len({row["idempotency_key"] for row in run["targets"]}) == 16


def test_partial_failure_retry_preserves_success_and_idempotency_key(tmp_path):
    store, plan, _approval = _approved_store(
        tmp_path,
        targets=["miaoshou:COMMON", "tiktok:LH_PH"],
    )
    run = store.start_run(plan["plan_id"])
    run_id = run["run_id"]
    original_keys = {
        row["target_label"]: row["idempotency_key"]
        for row in run["targets"]
    }

    store.begin_target(run_id, "miaoshou:COMMON")
    store.record_target_success(
        run_id,
        "miaoshou:COMMON",
        external_id="common-detail-3828540231",
    )
    store.begin_target(run_id, "tiktok:LH_PH")
    store.record_target_failure(
        run_id,
        "tiktok:LH_PH",
        error="temporary remote timeout",
        external_id="task-1",
    )

    failed = store.get_run(run_id)
    assert failed["status"] == "PARTIAL_FAILED"
    assert {
        row["target_label"]: row["status"] for row in failed["targets"]
    } == {
        "miaoshou:COMMON": "SUCCEEDED",
        "tiktok:LH_PH": "FAILED",
    }

    retry = store.retry_failed_targets(run_id)
    by_label = {row["target_label"]: row for row in retry["targets"]}
    assert retry["status"] == "RUNNING"
    assert by_label["miaoshou:COMMON"]["status"] == "SUCCEEDED"
    assert by_label["miaoshou:COMMON"]["attempts"] == 1
    assert by_label["tiktok:LH_PH"]["status"] == "PENDING"
    assert by_label["tiktok:LH_PH"]["idempotency_key"] == original_keys["tiktok:LH_PH"]

    second_attempt = store.begin_target(run_id, "tiktok:LH_PH")
    assert second_attempt["attempts"] == 2
    assert second_attempt["external_id"] == "task-1"
    store.record_target_success(
        run_id,
        "tiktok:LH_PH",
        external_id="listing-2",
    )
    completed = store.get_run(run_id)
    assert completed["status"] == "SUCCEEDED"
    assert completed["completed_at"]


def test_api_less_submission_is_terminal_until_kyle_manual_verification(tmp_path):
    store, plan, _approval = _approved_store(
        tmp_path,
        targets=["miaoshou:COMMON", "tiktok:GB"],
    )
    run = store.start_run(plan["plan_id"])
    run_id = run["run_id"]
    store.begin_target(run_id, "miaoshou:COMMON")
    store.record_target_success(run_id, "miaoshou:COMMON", external_id="common")
    store.begin_target(run_id, "tiktok:GB")
    submitted = store.record_target_submission(
        run_id,
        "tiktok:GB",
        external_id="detail-1:shop-1",
        submission_evidence={
            "accepted": True,
            "source": "miaoshou_open_api",
            "pre_submit_audit": {"submission_fingerprint": "audit-1"},
        },
        detail="Miaoshou accepted GB; no authorised official readback exists",
    )

    assert submitted["status"] == "SUBMITTED_UNVERIFIED"
    waiting = store.get_run(run_id)
    assert waiting["status"] == "AWAITING_MANUAL_VERIFICATION"
    assert waiting["targets"][1]["storage_status"] == "FAILED"
    assert waiting["targets"][1]["submission"]["evidence"][
        "pre_submit_audit"
    ]["submission_fingerprint"] == "audit-1"
    with pytest.raises(ReleaseStoreError, match="no failed targets to retry"):
        store.retry_failed_targets(run_id)

    verified = store.record_manual_verification(
        run_id,
        "tiktok:GB",
        verified_by="Kyle",
        user_verified=True,
        verification_evidence={
            "marketplace_product_id": "1729881266953821106",
            "identity_matches": True,
            "seller_sku_matches": True,
            "single_listing_for_sku": True,
            "title_matches": True,
            "price_matches": True,
            "images_match": True,
            "logistics_match": True,
        },
    )
    assert verified["status"] == "MANUALLY_VERIFIED"
    completed = store.get_run(run_id)
    assert completed["status"] == "COMPLETED_WITH_MANUAL_VERIFICATION"
    assert completed["targets"][1]["submission"]["verified_by"] == "Kyle"


def test_legacy_accepted_no_readback_failure_is_never_retried(tmp_path):
    store, plan, _approval = _approved_store(
        tmp_path,
        targets=["miaoshou:COMMON", "tiktok:MX"],
    )
    run = store.start_run(plan["plan_id"])
    store.begin_target(run["run_id"], "miaoshou:COMMON")
    store.record_target_success(run["run_id"], "miaoshou:COMMON")
    store.begin_target(run["run_id"], "tiktok:MX")
    store.record_target_failure(
        run["run_id"],
        "tiktok:MX",
        external_id="3224868435:16265910",
        error=(
            "Miaoshou already accepted MX; retry did not resubmit. "
            "An authorised official TikTok readback is still unavailable."
        ),
    )

    inferred = store.get_run(run["run_id"])
    assert inferred["status"] == "AWAITING_MANUAL_VERIFICATION"
    assert inferred["targets"][1]["status"] == "SUBMITTED_UNVERIFIED"
    with pytest.raises(ReleaseStoreError, match="no failed targets to retry"):
        store.retry_failed_targets(run["run_id"])


def test_verified_readback_is_immutable_and_returned_with_target(tmp_path):
    store, plan, _approval = _approved_store(
        tmp_path,
        targets=["miaoshou:COMMON"],
    )
    run = store.start_run(plan["plan_id"])
    store.begin_target(run["run_id"], "miaoshou:COMMON")
    evidence = {
        "source": "official-api",
        "verified": True,
        "checks": {"title": True, "seller_sku": True},
    }
    store.record_target_success(
        run["run_id"],
        "miaoshou:COMMON",
        external_id="common-1",
        readback_evidence=evidence,
    )

    target = store.get_run(run["run_id"])["targets"][0]
    assert target["readback"]["evidence"] == evidence
    assert target["readback"]["evidence_digest"]

    with pytest.raises(ImmutableReleaseError, match="different readback evidence"):
        store.record_target_success(
            run["run_id"],
            "miaoshou:COMMON",
            external_id="common-1",
            readback_evidence={**evidence, "verified": False},
        )


def test_interrupted_target_recovery_preserves_attempt_and_idempotency(tmp_path):
    store, plan, _approval = _approved_store(
        tmp_path,
        targets=["miaoshou:COMMON", "tiktok:LH_PH"],
    )
    run = store.start_run(plan["plan_id"])
    store.begin_target(run["run_id"], "miaoshou:COMMON")
    store.record_target_success(
        run["run_id"],
        "miaoshou:COMMON",
        external_id="common",
    )
    running = store.begin_target(run["run_id"], "tiktok:LH_PH")

    recovered = store.recover_interrupted_targets(
        run["run_id"],
        ["tiktok:LH_PH"],
    )
    target = next(
        row
        for row in recovered["targets"]
        if row["target_label"] == "tiktok:LH_PH"
    )
    assert target["status"] == "PENDING"
    assert target["attempts"] == 1
    assert target["idempotency_key"] == running["idempotency_key"]
    assert "interrupted worker" in target["error"]


def test_supersede_invalidates_approval_unfinished_run_and_reservation(tmp_path):
    store, plan, _approval = _approved_store(
        tmp_path,
        targets=["miaoshou:COMMON", "tiktok:LH_PH"],
    )
    run = store.start_run(plan["plan_id"])
    store.begin_target(run["run_id"], "miaoshou:COMMON")
    store.record_target_success(
        run["run_id"],
        "miaoshou:COMMON",
        external_id="verified-common",
    )
    store.begin_target(run["run_id"], "tiktok:LH_PH")

    with pytest.raises(
        ReleaseAuthorizationError,
        match="cannot be superseded.*RUNNING",
    ):
        store.supersede_plan(plan["plan_id"], reason="price changed")
    still_running = store.get_run(run["run_id"])
    assert next(
        row
        for row in still_running["targets"]
        if row["target_label"] == "tiktok:LH_PH"
    )["status"] == "RUNNING"
    store.recover_interrupted_targets(
        run["run_id"],
        ["tiktok:LH_PH"],
    )
    superseded = store.supersede_plan(plan["plan_id"], reason="price changed")
    stored = store.get_plan(plan["plan_id"])
    stored_run = store.get_run(run["run_id"])

    assert superseded["status"] == "SUPERSEDED"
    assert superseded["supersede_reason"] == "price changed"
    assert stored["approval"]["status"] == "SUPERSEDED"
    assert stored["sku_reservation"]["status"] == "SUPERSEDED"
    assert stored_run["status"] == "SUPERSEDED"
    assert {
        row["target_label"]: row["status"] for row in stored_run["targets"]
    } == {
        "miaoshou:COMMON": "SUCCEEDED",
        "tiktok:LH_PH": "SUPERSEDED",
    }
    assert store.active_sku_reservations() == []

    with pytest.raises(ReleaseAuthorizationError, match="superseded"):
        store.begin_target(run["run_id"], "tiktok:LH_PH")


def test_unlinked_superseded_common_plan_can_be_linked_once_to_successor(
    tmp_path,
):
    store, predecessor, _approval = _approved_store(
        tmp_path,
        targets=["miaoshou:COMMON", "tiktok:LH_PH"],
    )
    run = store.start_run(predecessor["plan_id"])
    store.begin_target(run["run_id"], "miaoshou:COMMON")
    store.record_target_success(
        run["run_id"],
        "miaoshou:COMMON",
        external_id=predecessor["product_id"],
        readback_evidence={
            "verified": True,
            "source": "fixture",
            "offer_id": predecessor["product_id"],
            "checks": {"title": True},
            "image_count": 0,
        },
    )
    store.supersede_plan(
        predecessor["plan_id"],
        reason="title refresh happened before successor existed",
    )

    candidate = store.latest_unlinked_common_predecessor(
        product_id=predecessor["product_id"],
        seller_sku=predecessor["seller_sku"],
    )
    assert candidate["plan_id"] == predecessor["plan_id"]
    successor_payload = _plan(plan_id="omnichannel:plan-v2")
    successor = store.create_plan(
        successor_payload,
        supersedes_plan_id=predecessor["plan_id"],
    )

    assert store.predecessor_plan_for(successor["plan_id"])["plan_id"] == (
        predecessor["plan_id"]
    )
    linked = store.get_plan(predecessor["plan_id"])
    assert linked["status"] == "SUPERSEDED"
    assert linked["superseded_by_plan_id"] == successor["plan_id"]
    with pytest.raises(ReleaseStoreError, match="already superseded"):
        store.create_plan(
            _plan(plan_id="omnichannel:plan-v3"),
            supersedes_plan_id=predecessor["plan_id"],
        )


def test_failure_write_evidence_survives_retry_then_verified_success(tmp_path):
    store, plan, _approval = _approved_store(
        tmp_path,
        targets=["miaoshou:COMMON"],
    )
    run = store.start_run(plan["plan_id"])
    first_key = run["targets"][0]["idempotency_key"]
    store.begin_target(run["run_id"], "miaoshou:COMMON")
    store.record_target_failure(
        run["run_id"],
        "miaoshou:COMMON",
        error="save accepted but exact readback failed",
        external_id="3828540231",
        failure_evidence={
            "save_accepted": True,
            "verified": False,
            "external_writes_performed": ["miaoshou:COMMON:update"],
        },
    )
    failed = store.get_run(run["run_id"])
    event = failed["targets"][0]["latest_failure_evidence"]
    assert event["attempt"] == 1
    assert event["evidence"]["save_accepted"] is True

    store.retry_failed_targets(run["run_id"], ["miaoshou:COMMON"])
    retried = store.begin_target(run["run_id"], "miaoshou:COMMON")
    assert retried["idempotency_key"] == first_key
    store.record_target_success(
        run["run_id"],
        "miaoshou:COMMON",
        external_id="3828540231",
        readback_evidence={"verified": True, "source": "exact-readback"},
    )

    completed = store.get_run(run["run_id"])
    target = completed["targets"][0]
    assert target["status"] == "SUCCEEDED"
    assert target["attempts"] == 2
    assert target["readback"]["evidence"]["verified"] is True
    assert target["failure_events"][0]["evidence"]["save_accepted"] is True


def test_successor_creation_hands_off_same_sku_atomically(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    old = store.create_plan(_plan())
    new_payload = _plan(
        plan_id="omnichannel:plan-v2",
        product_package_id="product:3828540231:0946:r2",
        content_package_id="content:3828540231:r2",
    )

    new = store.create_plan(
        new_payload,
        supersedes_plan_id=old["plan_id"],
    )

    assert new["status"] == "PENDING_APPROVAL"
    assert store.get_plan(old["plan_id"])["status"] == "SUPERSEDED"
    active = store.active_sku_reservations()
    assert len(active) == 1
    assert active[0]["plan_id"] == new["plan_id"]
    assert active[0]["sku_key"] == "0946"


@pytest.mark.parametrize(
    "targets,error",
    [
        ([], "non-empty list"),
        (["miaoshou:COMMON", "miaoshou:COMMON"], "duplicates"),
        (["amazon:US"], "unsupported release targets"),
    ],
)
def test_plan_rejects_invalid_target_scope(tmp_path, targets, error):
    with pytest.raises(ValueError, match=error):
        ReleaseStore(tmp_path / "release.db").create_plan(
            _plan(targets=targets)
        )


def test_sqlite_guards_payload_integrity_fk_and_busy_timeout(tmp_path):
    store, plan, _approval = _approved_store(tmp_path)
    health = store.database_health()

    assert health == {
        "exists": True,
        "integrity_check": "ok",
        "foreign_key_violations": [],
        "busy_timeout": 30000,
    }

    connection = sqlite3.connect(store.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="payload is immutable"):
            connection.execute(
                "UPDATE release_plans SET payload_json = '{}' WHERE plan_id = ?",
                (plan["plan_id"],),
            )
    finally:
        connection.close()


def test_retry_scope_must_only_contain_failed_targets(tmp_path):
    store, plan, _approval = _approved_store(
        tmp_path,
        targets=["miaoshou:COMMON", "tiktok:LH_PH"],
    )
    run = store.start_run(plan["plan_id"])
    store.begin_target(run["run_id"], "miaoshou:COMMON")
    store.record_target_failure(
        run["run_id"],
        "miaoshou:COMMON",
        error="readback mismatch",
    )

    with pytest.raises(ReleaseStoreError, match="subset of failed"):
        store.retry_failed_targets(
            run["run_id"],
            ["tiktok:LH_PH"],
        )
