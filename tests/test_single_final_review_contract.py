"""Behavioral contract for one human marketplace review after R1/R2."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from shared_platform.publication_autopilot import (
    PublicationAutopilotContractError,
    validate_autopilot_policy,
)
from shared_platform.publication_rounds import build_round1_auto_decision


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "product_publication_autopilot_policy.json"


def production_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_round1_ready_candidate_is_technically_adopted_without_human_approval():
    policy = validate_autopilot_policy(production_policy())
    decision = build_round1_auto_decision(
        {
            "schema_version": "publication-preparation-decision/v1",
            "status": "FIRST_REVIEW_READY",
            "offer_id": "123456",
            "external_write_count": 0,
        },
        policy=policy,
    )

    assert decision["status"] == "AUTO_APPROVED"
    assert decision["decided_by"] == "product-publication-autopilot"
    assert decision["human_approval"] is False
    assert decision["external_write_count"] == 0


def test_production_policy_has_one_final_gate_and_explicit_receipt_reuse_scope():
    policy = validate_autopilot_policy(production_policy())
    review = policy["review_contract"]
    final = policy["final_marketplace_publish"]

    assert review["intermediate_human_approval_required"] is False
    assert review["intermediate_artifacts_are_execution_authority"] is False
    assert review["sole_human_gate"] == final["approval_kind"] == "FINAL_MARKETPLACE_PUBLISH"
    assert final["single_review_per_frozen_candidate"] is True
    assert final["valid_receipt_must_not_be_reprompted"] is True
    assert set(review["reuse_existing_final_approval_for"]) == {
        "READ_ONLY_RECONCILIATION",
        "BOUNDED_TECHNICAL_RETRY",
        "ASYNC_PROVIDER_CONVERGENCE",
        "SYSTEM_DEFECT_CONTINUATION_WITH_EXACT_APPROVED_LINEAGE",
    }
    assert set(review["new_explicit_authority_required_for"]) == {
        "FROZEN_CANDIDATE_CHANGE",
        "TARGET_SCOPE_EXPANSION",
        "PAID_ACTION_OUTSIDE_EXISTING_AUTHORITY",
        "NEW_EXTERNAL_WRITE_CLASS",
    }


def test_production_policy_cannot_omit_the_single_final_review_contract():
    policy = production_policy()
    policy.pop("review_contract")

    with pytest.raises(PublicationAutopilotContractError, match="production policy"):
        validate_autopilot_policy(policy)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("intermediate_human_approval_required", True),
        ("intermediate_artifacts_are_execution_authority", True),
        ("sole_human_gate", "ROUND2_IMAGE_REVIEW"),
        ("reuse_existing_final_approval_for", ["READ_ONLY_RECONCILIATION"]),
        ("new_explicit_authority_required_for", ["TARGET_SCOPE_EXPANSION"]),
    ],
)
def test_policy_rejects_additional_review_gate_or_ambiguous_reuse(field, invalid):
    policy = production_policy()
    policy["review_contract"][field] = invalid

    with pytest.raises(PublicationAutopilotContractError, match="single final-review"):
        validate_autopilot_policy(policy)


@pytest.mark.parametrize(
    "field",
    ["single_review_per_frozen_candidate", "valid_receipt_must_not_be_reprompted"],
)
def test_policy_rejects_final_gate_that_can_reprompt_same_candidate(field):
    policy = deepcopy(production_policy())
    policy["final_marketplace_publish"][field] = False

    with pytest.raises(PublicationAutopilotContractError, match="single final-review"):
        validate_autopilot_policy(policy)


def test_retired_guides_do_not_prescribe_intermediate_approval_commands():
    collaboration = (
        ROOT / "docs" / "PRODUCT_PUBLICATION_COLLABORATION_GUIDE.zh-CN.md"
    ).read_text(encoding="utf-8")
    release_v1 = (ROOT / "docs" / "PRODUCT_RELEASE_V1.md").read_text(encoding="utf-8")

    assert "--approve-all" not in collaboration
    assert "第一轮通过，开始第二轮" not in collaboration
    assert "再次勾选" not in release_v1
    assert "FINAL_MARKETPLACE_PUBLISH" in collaboration
    assert "FINAL_MARKETPLACE_PUBLISH" in release_v1
