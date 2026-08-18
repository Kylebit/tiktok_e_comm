from shared_platform.target_recovery import (
    SCHEMA_VERSION,
    classify_target_recovery,
    project_run_recovery_actions,
)


def test_channel_labels_do_not_change_recovery_classification():
    facts = {
        "status": "PENDING",
        "attempts": 0,
        "external_id": None,
    }

    actions = [
        classify_target_recovery({**facts, "target_label": label})
        for label in (
            "shopee:MY",
            "shopee:VN",
            "tiktok:GB",
            "ozon:RU",
        )
    ]

    assert {action["schema_version"] for action in actions} == {
        SCHEMA_VERSION
    }
    assert {action["action_kind"] for action in actions} == {"FIRST_ATTEMPT"}
    assert all(action["runnable"] is True for action in actions)


def test_prior_write_identity_never_becomes_first_attempt():
    action = classify_target_recovery(
        {
            "target_label": "shopee:VN",
            "status": "PENDING",
            "attempts": 0,
            "external_id": "regional-item-1",
        }
    )

    assert action["action_kind"] == "BLOCKED"
    assert action["runnable"] is False
    assert action["prior_write_evidence"] is True


def test_mixed_run_projects_one_runnable_without_reopening_other_targets():
    actions = project_run_recovery_actions(
        [
            {"target_label": "miaoshou:COMMON", "status": "SUCCEEDED"},
            {
                "target_label": "tiktok:LH_MY",
                "status": "FAILED",
                "attempts": 1,
            },
            {
                "target_label": "tiktok:GB",
                "status": "SUBMITTED_UNVERIFIED",
                "attempts": 1,
                "external_id": "submitted-1",
            },
            {
                "target_label": "shopee:VN",
                "status": "RECONCILIATION_REQUIRED",
                "attempts": 1,
                "external_id": "regional-1",
            },
            {
                "target_label": "shopee:MY",
                "status": "PENDING",
                "attempts": 0,
            },
        ]
    )
    by_label = {action["target_label"]: action for action in actions}

    assert by_label["tiktok:LH_MY"]["action_kind"] == "SAFE_REPAIR"
    assert by_label["tiktok:GB"]["action_kind"] == "MANUAL_ACCEPT"
    assert by_label["shopee:VN"]["action_kind"] == "READONLY_RECONCILE"
    assert by_label["shopee:MY"]["action_kind"] == "FIRST_ATTEMPT"
    assert [
        action["target_label"]
        for action in actions
        if action["runnable"]
    ] == ["shopee:MY"]


def test_exact_zero_write_failure_can_be_safe_retry_only_when_authorized():
    target = {
        "target_label": "tiktok:LH_MY",
        "status": "FAILED",
        "attempts": 1,
        "latest_failure_evidence": {
            "evidence": {
                "pre_submit_failure": True,
                "external_writes_performed": [],
            }
        },
    }

    blocked = classify_target_recovery(target)
    retry = classify_target_recovery(target, safe_retry_eligible=True)

    assert blocked["action_kind"] == "SAFE_REPAIR"
    assert blocked["runnable"] is False
    assert retry["action_kind"] == "SAFE_RETRY"
    assert retry["runnable"] is True


def test_successor_pending_target_never_reopens_predecessor_write():
    actions = project_run_recovery_actions(
        [
            {
                "target_label": "tiktok:GB",
                "status": "PENDING",
                "attempts": 0,
            },
            {
                "target_label": "shopee:MY",
                "status": "PENDING",
                "attempts": 0,
            },
        ],
        predecessor_targets=[
            {
                "target_label": "tiktok:GB",
                "status": "SUBMITTED_UNVERIFIED",
                "external_id": "submitted-1",
            },
            {
                "target_label": "shopee:MY",
                "status": "SUPERSEDED",
                "attempts": 0,
            },
        ],
    )
    by_label = {action["target_label"]: action for action in actions}

    assert by_label["tiktok:GB"]["action_kind"] == "READONLY_RECONCILE"
    assert by_label["tiktok:GB"]["runnable"] is False
    assert (
        by_label["tiktok:GB"]["reason_code"]
        == "predecessor_external_outcome_requires_resolution"
    )
    assert by_label["tiktok:GB"]["predecessor_status"] == (
        "SUBMITTED_UNVERIFIED"
    )
    assert by_label["shopee:MY"]["action_kind"] == "FIRST_ATTEMPT"
    assert by_label["shopee:MY"]["runnable"] is True


def test_successor_predecessor_write_requires_explicit_adapter_capability():
    current = [
        {
            "target_label": "shopee:PH",
            "status": "PENDING",
            "attempts": 0,
        },
        {
            "target_label": "tiktok:GB",
            "status": "PENDING",
            "attempts": 0,
        },
    ]
    predecessor = [
        {
            "target_label": "shopee:PH",
            "status": "SUCCEEDED",
            "external_id": "regional-item-1",
        },
        {
            "target_label": "tiktok:GB",
            "status": "SUBMITTED_UNVERIFIED",
            "external_id": "submission-1",
        },
    ]

    blocked = project_run_recovery_actions(
        current,
        predecessor_targets=predecessor,
    )
    governed = project_run_recovery_actions(
        current,
        predecessor_targets=predecessor,
        predecessor_recovery_labels={"shopee:PH"},
    )
    blocked_by_label = {
        action["target_label"]: action for action in blocked
    }
    governed_by_label = {
        action["target_label"]: action for action in governed
    }

    assert blocked_by_label["shopee:PH"]["runnable"] is False
    assert (
        governed_by_label["shopee:PH"]["action_kind"]
        == "GOVERNED_RECOVERY"
    )
    assert governed_by_label["shopee:PH"]["runnable"] is True
    assert (
        governed_by_label["shopee:PH"]["reason_code"]
        == "official_readback_then_bounded_write_recovery"
    )
    assert (
        governed_by_label["tiktok:GB"]["action_kind"]
        == "READONLY_RECONCILE"
    )
    assert governed_by_label["tiktok:GB"]["runnable"] is False


def test_pristine_target_can_be_blocked_by_missing_adapter_capability():
    actions = project_run_recovery_actions(
        [
            {
                "target_label": "shopee:MY",
                "status": "PENDING",
                "attempts": 0,
            },
            {
                "target_label": "ozon:RU",
                "status": "PENDING",
                "attempts": 0,
            },
        ],
        first_attempt_blocked_labels={"ozon:RU"},
    )
    by_label = {action["target_label"]: action for action in actions}

    assert by_label["shopee:MY"]["action_kind"] == "FIRST_ATTEMPT"
    assert by_label["shopee:MY"]["runnable"] is True
    assert by_label["ozon:RU"]["action_kind"] == "BLOCKED_CAPABILITY"
    assert by_label["ozon:RU"]["runnable"] is False
    assert (
        by_label["ozon:RU"]["reason_code"]
        == "automatic_first_attempt_capability_unavailable"
    )
