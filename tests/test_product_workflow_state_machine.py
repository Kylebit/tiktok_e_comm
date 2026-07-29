from __future__ import annotations

from copy import deepcopy

import pytest

from shared_platform.product_workflow import (
    SCHEMA_VERSION,
    assert_no_dead_end,
    project_product_workflow_next_action,
)


def _ready_view() -> dict:
    return {
        "product": {
            "offer_id": "3845133620",
            "title": "Approved title",
            "cost_cny": 5.2,
            "weight_kg": 0.2,
            "package_cm": [30, 20, 2],
            "selected_sites": ["MX"],
            "selected_sku_keys": ["sku-1"],
            "fact_evidence": {"ready": True},
            "actual_product_approved": True,
        },
        "content": {
            "approved": True,
            "image_count": 5,
            "images": [{"position": index} for index in range(1, 6)],
            "blockers": [],
        },
        "release_v1": {
            "eligible_for_plan_approval": True,
            "plan": {"plan_id": "plan-1"},
            "plan_approved": True,
            "miaoshou_prepared": True,
            "canonical_common_ready": True,
            "publish_ready": True,
            "adapter_blockers": [],
            "run": {
                "status": "RUNNING",
                "targets": [
                    {
                        "target_label": "miaoshou:COMMON",
                        "status": "SUCCEEDED",
                    },
                    {"target_label": "tiktok:MX", "status": "PENDING"},
                ],
            },
        },
    }


def _case(code: str) -> dict:
    view = _ready_view()
    if code == "complete_product_facts":
        view["product"]["weight_kg"] = None
        view["release_v1"]["plan"] = None
    elif code == "complete_content_review":
        view["content"]["approved"] = False
        view["release_v1"]["plan"] = None
    elif code == "approve_product_facts":
        view["product"]["actual_product_approved"] = False
    elif code == "refresh_release_plan":
        view["release_v1"].update(
            {
                "plan": None,
                "plan_approved": False,
                "eligible_for_plan_approval": False,
                "recovery_actions": [],
            }
        )
    elif code == "refresh_listing_copy":
        view["release_v1"].update(
            {
                "plan": None,
                "plan_approved": False,
                "eligible_for_plan_approval": False,
                "recovery_actions": [
                    {
                        "code": "refresh_listing_copy",
                        "label": "重新生成平台文案",
                        "detail": "采用新文案后重新核对计划。",
                    }
                ],
            }
        )
    elif code == "approve_release_plan":
        view["release_v1"]["plan_approved"] = False
    elif code == "prepare_miaoshou_common":
        view["release_v1"]["miaoshou_prepared"] = False
    elif code == "publish_selected_targets":
        pass
    elif code == "monitor_release_run":
        view["release_v1"]["run"]["targets"][1]["status"] = "RUNNING"
    elif code == "complete_manual_acceptance":
        view["release_v1"]["run"]["targets"][1][
            "status"
        ] = "SUBMITTED_UNVERIFIED"
        view["release_v1"]["publish_ready"] = False
    elif code == "resolve_release_reconciliation":
        view["release_v1"]["run"]["targets"][1][
            "status"
        ] = "RECONCILIATION_REQUIRED"
        view["release_v1"]["publish_ready"] = False
    elif code == "resolve_release_capability":
        view["release_v1"]["publish_ready"] = False
        view["release_v1"]["adapter_blockers"] = [
            "target adapter is not available"
        ]
    elif code == "release_complete":
        view["release_v1"]["run"]["status"] = "SUCCEEDED"
        view["release_v1"]["run"]["targets"][1]["status"] = "SUCCEEDED"
        view["release_v1"]["publish_ready"] = False
    else:
        raise AssertionError(f"unknown test case {code}")
    return view


@pytest.mark.parametrize(
    "expected_code",
    [
        "complete_product_facts",
        "complete_content_review",
        "approve_product_facts",
        "refresh_release_plan",
        "refresh_listing_copy",
        "approve_release_plan",
        "prepare_miaoshou_common",
        "publish_selected_targets",
        "monitor_release_run",
        "complete_manual_acceptance",
        "resolve_release_reconciliation",
        "resolve_release_capability",
        "release_complete",
    ],
)
def test_state_matrix_has_exactly_one_no_dead_end_next_action(expected_code):
    action = project_product_workflow_next_action(_case(expected_code))

    assert action["schema_version"] == SCHEMA_VERSION
    assert action["code"] == expected_code
    assert_no_dead_end(action)
    if action["terminal"]:
        assert action["actionable"] is False
    else:
        assert action["actionable"] is True
        assert bool(action.get("control_id") or action.get("href"))


@pytest.mark.parametrize(
    "bad_value",
    [None, True, False, "not-a-number", [], {}, float("inf")],
)
def test_malformed_product_numbers_fail_closed_to_fact_review(bad_value):
    view = _ready_view()
    view["product"]["cost_cny"] = bad_value
    view["release_v1"]["plan"] = None

    action = project_product_workflow_next_action(view)

    assert action["code"] == "complete_product_facts"
    assert_no_dead_end(action)


def test_projection_is_pure_and_does_not_mutate_dashboard():
    view = _ready_view()
    before = deepcopy(view)

    project_product_workflow_next_action(view)

    assert view == before


def test_mixed_release_results_keep_independent_first_attempt_actionable():
    view = _ready_view()
    view["release_v1"]["publish_ready"] = True
    view["release_v1"]["run"] = {
        "status": "PARTIAL_FAILED",
        "targets": [
            {"target_label": "miaoshou:COMMON", "status": "SUCCEEDED"},
            {"target_label": "tiktok:LH_MY", "status": "FAILED"},
            {"target_label": "tiktok:MX", "status": "SUBMITTED_UNVERIFIED"},
            {"target_label": "tiktok:GB", "status": "SUBMITTED_UNVERIFIED"},
            {"target_label": "shopee:VN", "status": "RECONCILIATION_REQUIRED"},
            {"target_label": "shopee:MY", "status": "PENDING"},
            {"target_label": "shopee:PH", "status": "SUCCEEDED"},
        ],
    }

    action = project_product_workflow_next_action(view)

    assert action["code"] == "publish_selected_targets"
    assert action["target_counts"] == {
        "running": 0,
        "reconciliation": 2,
        "manual_acceptance": 2,
        "pending": 1,
        "blocked_capability": 0,
    }
    assert action["control_id"] == "publishAllCheckbox"
    assert "1 个从未提交" in action["detail"]
    assert "2 个待对账" in action["detail"]
    assert "2 个待人工验收" in action["detail"]
    assert_no_dead_end(action)


def test_publish_ready_without_runnable_target_never_points_to_disabled_publish():
    view = _ready_view()
    view["release_v1"]["runnable_target_count"] = 0
    view["release_v1"]["run"] = {
        "status": "PARTIAL_FAILED",
        "targets": [
            {"target_label": "miaoshou:COMMON", "status": "SUCCEEDED"},
            {
                "target_label": "shopee:MY",
                "status": "PENDING",
                "attempts": 1,
                "external_id": "prior-external-identity",
            },
        ],
    }
    view["release_v1"]["target_recovery_actions"] = [
        {
            "target_label": "shopee:MY",
            "status": "PENDING",
            "action_kind": "BLOCKED_CAPABILITY",
            "runnable": False,
            "reason_code": "automatic_first_attempt_capability_unavailable",
        }
    ]

    action = project_product_workflow_next_action(view)

    assert action["code"] == "resolve_release_capability"
    assert action["control_id"] == "releaseRunLedger"
    assert action["focus_target_label"] == "shopee:MY"
    assert action["control_id"] != "publishAllCheckbox"
    assert action["target_counts"]["blocked_capability"] == 1
    assert_no_dead_end(action)


def test_manual_acceptance_action_focuses_first_real_manual_form():
    view = _case("complete_manual_acceptance")

    action = project_product_workflow_next_action(view)

    assert action["code"] == "complete_manual_acceptance"
    assert action["focus_target_label"] == "tiktok:MX"


def test_server_recovery_projection_overrides_misleading_pending_status():
    view = _ready_view()
    view["release_v1"]["runnable_target_count"] = 0
    view["release_v1"]["target_recovery_actions"] = [
        {
            "target_label": "shopee:VN",
            "status": "PENDING",
            "action_kind": "READONLY_RECONCILE",
            "runnable": False,
            "reason_code": "predecessor_external_outcome_requires_resolution",
        }
    ]

    action = project_product_workflow_next_action(view)

    assert action["code"] == "resolve_release_reconciliation"
    assert action["control_id"] == "releaseRunLedger"
    assert action["focus_target_label"] == "shopee:VN"
    assert action["target_counts"]["pending"] == 0
    assert action["target_counts"]["reconciliation"] == 1
    assert_no_dead_end(action)


def test_server_recovery_projection_makes_exact_safe_retry_runnable():
    view = _ready_view()
    view["release_v1"]["run"]["targets"][1]["status"] = "FAILED"
    view["release_v1"]["runnable_target_count"] = 1
    view["release_v1"]["target_recovery_actions"] = [
        {
            "target_label": "shopee:MY",
            "status": "FAILED",
            "action_kind": "SAFE_RETRY",
            "runnable": True,
            "reason_code": "exact_zero_write_pre_submit_failure",
        }
    ]

    action = project_product_workflow_next_action(view)

    assert action["code"] == "publish_selected_targets"
    assert action["target_counts"]["pending"] == 1
    assert action["target_counts"]["reconciliation"] == 0
    assert_no_dead_end(action)
