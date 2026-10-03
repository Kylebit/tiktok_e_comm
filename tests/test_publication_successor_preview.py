from copy import deepcopy
import json

import pytest

from shared_platform import publication_successor_preview as preview_module
from shared_platform.publication_autopilot import _canonical_digest

from shared_platform.publication_successor_preview import (
    load_successor_preview,
    validate_successor_preview,
)


TARGETS = [
    "tiktok:LH_PH", "tiktok:LH_MY", "tiktok:LH_TH", "tiktok:LH_VN",
    "tiktok:HB_PH", "tiktok:HB_MY", "tiktok:HB_TH", "tiktok:HB_VN",
    "tiktok:MX", "tiktok:GB", "shopee:PH", "shopee:MY", "shopee:TH",
    "shopee:VN", "ozon:RU",
]


def _candidate(digest="old"):
    copy_sets = []
    for target in TARGETS:
        copy_sets.append({
            "language": target,
            "target_labels": [target],
            "title": "Frozen title " + target,
            "description": "Frozen description " + target,
        })
    return {
        "schema_version": "publication-release-candidate/v1",
        "status": "READY_FOR_FINAL_REVIEW",
        "offer_id": "3956742887",
        "candidate_digest": digest,
        "snapshot_digest": "snapshot-" + digest,
        "target_labels": list(TARGETS),
        "platform_scope": ["TIKTOK", "SHOPEE", "OZON"],
        "write_budget": {"SHOPEE": {"target_labels": TARGETS[10:14]}},
        "durable_quality_evidence": {"status": "PASSED", "checks": []},
        "review_manifest": {
            "copy_sets": copy_sets,
            "variants": [{"model_sku": "0988"}],
            "targets": [{"target_label": target, "copy_set_id": target,
                         "prices": [{"model_sku": "0988"}]} for target in TARGETS],
            "image_sets": [{"image_set_id": "frozen"}],
        },
    }


def _document():
    predecessor = _candidate()
    successor = deepcopy(predecessor)
    successor.update(candidate_digest="new", snapshot_digest="snapshot-new")
    for group in successor["review_manifest"]["copy_sets"]:
        if group["target_labels"][0] in {"shopee:MY", "shopee:TH", "shopee:VN"}:
            group["description"] = "Localized " + group["target_labels"][0]
    return predecessor, {
        "schema_version": "localized-copy-successor-preview/v1",
        "offer_id": "3956742887",
        "status": "NOT_APPROVED",
        "predecessor_candidate_digest": "old",
        "changed_target_labels": ["shopee:MY", "shopee:TH", "shopee:VN"],
        "candidate": successor,
    }


def _ozon_document(monkeypatch):
    predecessor = _candidate()
    successor = deepcopy(predecessor)
    predecessor_content = {
        target: {"title": "Frozen title " + target,
                 "description": "Frozen description " + target}
        for target in TARGETS
    }
    predecessor_formal = {
        "product_id": "3956742887", "targets": list(TARGETS),
        "product_facts": {"content_by_target": predecessor_content},
        "digests": {"content": "sha256:" + _canonical_digest(predecessor_content)},
    }
    predecessor_formal["plan_id"] = "omnichannel:" + _canonical_digest(predecessor_formal)
    predecessor["plan_id"] = predecessor_formal["plan_id"]
    formal = deepcopy(predecessor_formal)
    formal["product_facts"]["content_by_target"]["ozon:RU"].update(
        title="Russian title", description="Russian description"
    )
    formal["digests"]["content"] = "sha256:" + _canonical_digest(
        formal["product_facts"]["content_by_target"]
    )
    formal.pop("plan_id")
    formal["plan_id"] = "omnichannel:" + _canonical_digest(formal)
    business = "sha256:" + "b" * 64
    successor.update(plan_id=formal["plan_id"], snapshot_digest=business,
                     business_snapshot_digest=business)
    for group in successor["review_manifest"]["copy_sets"]:
        if group["target_labels"] == ["ozon:RU"]:
            group["title"] = "Russian title"
            group["description"] = "Russian description"
    successor.update(
        blockers=[],
        automated_quality_gate={"status": "PASSED"},
        zero_write_simulation={
            "blocker_count": 0,
            "completed": True,
            "external_write_count": 0,
        },
    )
    successor.pop("candidate_digest", None)
    successor["candidate_digest"] = _canonical_digest(successor)
    compiled_successor = deepcopy(successor)
    monkeypatch.setattr(preview_module, "_preview_formal_plan", lambda value: {
        "plan_id": value["plan_id"], "payload_digest": "d" * 64,
    })
    monkeypatch.setattr(preview_module, "_snapshot_for_preview", lambda value: value)
    monkeypatch.setattr(preview_module, "_business_digest_for_snapshot", lambda _value: business)
    monkeypatch.setattr(
        preview_module, "_compile_candidate_for_snapshot",
        lambda snapshot, **_kwargs: deepcopy(
            compiled_successor if snapshot["plan_id"] == formal["plan_id"] else predecessor
        ),
    )
    return predecessor, {
        "schema_version": "publication-copy-successor-preview/v2",
        "offer_id": "3956742887",
        "status": "NOT_APPROVED",
        "predecessor_candidate_digest": "old",
        "predecessor_plan_id": predecessor["plan_id"],
        "changed_target_labels": ["ozon:RU"],
        "changed_copy_fields_by_target": {
            "ozon:RU": ["title", "description"],
        },
        "formal_plan": formal,
        "candidate": successor,
    }


def test_successor_preview_is_approval_neutral_and_retains_full_scope():
    predecessor, document = _document()
    result = validate_successor_preview(
        document, offer_id="3956742887", predecessor=predecessor
    )
    assert result["status"] == "NOT_APPROVED"
    assert result["target_labels"] == TARGETS
    assert result["predecessor_candidate_digest"] == "old"
    assert result["review_blocked"] is False


def test_successor_preview_exposes_non_passing_quality_as_review_blocker():
    predecessor, document = _document()
    document["candidate"]["durable_quality_evidence"]["status"] = "NOT_AVAILABLE"
    result = validate_successor_preview(
        document, offer_id="3956742887", predecessor=predecessor
    )
    assert result["status"] == "NOT_APPROVED"
    assert result["review_blocked"] is True
    assert result["review_blockers"] == ["DURABLE_QUALITY_EVIDENCE_NOT_PASSED"]


def test_generic_successor_allows_declared_ozon_title_and_description_only(monkeypatch):
    predecessor, document = _ozon_document(monkeypatch)
    result = validate_successor_preview(
        document, offer_id="3956742887", predecessor=predecessor
    )
    assert result["status"] == "NOT_APPROVED"
    assert result["changed_target_labels"] == ["ozon:RU"]
    assert result["changed_copy_fields_by_target"] == {
        "ozon:RU": ["title", "description"]
    }
    assert result["review_blocked"] is False


def test_generic_successor_rejects_undeclared_copy_change(monkeypatch):
    predecessor, document = _ozon_document(monkeypatch)
    document["changed_copy_fields_by_target"]["ozon:RU"] = ["description"]
    with pytest.raises(ValueError, match="copy change set drifted|predecessor formal identity drifted"):
        validate_successor_preview(
            document, offer_id="3956742887", predecessor=predecessor
        )


def test_generic_successor_requires_clean_zero_write_preflight(monkeypatch):
    predecessor, document = _ozon_document(monkeypatch)
    document["candidate"]["zero_write_simulation"]["external_write_count"] = 1
    body = deepcopy(document["candidate"])
    body.pop("candidate_digest")
    document["candidate"]["candidate_digest"] = _canonical_digest(body)
    with pytest.raises(ValueError, match="canonical candidate drifted"):
        validate_successor_preview(
            document, offer_id="3956742887", predecessor=predecessor
        )


def test_adopted_successor_sidecar_projects_active_approval_without_predecessor_error(tmp_path):
    predecessor, document = _document()
    active = deepcopy(document["candidate"])
    active["plan_id"] = "successor-plan"
    document["candidate"]["plan_id"] = "successor-plan"
    path = tmp_path / "3956742887" / "localized-copy-successor-preview.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document), encoding="utf-8")

    result = load_successor_preview(
        offer_id="3956742887", predecessor=active, reports_root=tmp_path
    )

    assert result["status"] == "APPROVED"
    assert result["successor_adopted"] is True
    assert result["candidate_digest"] == "new"
    assert result["predecessor_candidate_digest"] == "old"
    assert result["review_blocked"] is False


def test_generic_successor_never_projects_approved_without_execution_receipt(tmp_path, monkeypatch):
    _predecessor, document = _ozon_document(monkeypatch)
    active = deepcopy(document["candidate"])
    path = tmp_path / "3956742887" / "publication-successor-preview.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document), encoding="utf-8")

    result = load_successor_preview(
        offer_id="3956742887", predecessor=active, reports_root=tmp_path
    )

    assert result["status"] == "AWAITING_APPROVAL"
    assert result["successor_adopted"] is False
    assert result["review_blockers"] == ["EXECUTION_BINDING_REQUIRED"]


def test_active_generic_successor_rejects_tampered_formal_plan(tmp_path, monkeypatch):
    _predecessor, document = _ozon_document(monkeypatch)
    active = deepcopy(document["candidate"])
    document["formal_plan"]["tampered"] = True
    path = tmp_path / "3956742887" / "publication-successor-preview.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="formal_plan identity drifted"):
        load_successor_preview(
            offer_id="3956742887", predecessor=active, reports_root=tmp_path
        )


def test_generic_successor_rejects_missing_formal_plan(monkeypatch):
    predecessor, document = _ozon_document(monkeypatch)
    document.pop("formal_plan")
    with pytest.raises(ValueError, match="formal_plan is missing"):
        validate_successor_preview(document, offer_id="3956742887", predecessor=predecessor)


def test_generic_successor_rejects_tampered_formal_plan(monkeypatch):
    predecessor, document = _ozon_document(monkeypatch)
    document["formal_plan"]["tampered"] = True
    with pytest.raises(ValueError, match="formal_plan identity drifted"):
        validate_successor_preview(document, offer_id="3956742887", predecessor=predecessor)


def test_generic_successor_rejects_rebuilt_candidate_identity_mismatch(monkeypatch):
    predecessor, document = _ozon_document(monkeypatch)
    document["candidate"]["business_snapshot_digest"] = "sha256:" + "c" * 64
    body = deepcopy(document["candidate"])
    body.pop("candidate_digest")
    document["candidate"]["candidate_digest"] = _canonical_digest(body)
    with pytest.raises(ValueError, match="rebuilt identity mismatch"):
        validate_successor_preview(document, offer_id="3956742887", predecessor=predecessor)


def test_generic_successor_rejects_review_copy_that_differs_from_formal_plan(monkeypatch):
    predecessor, document = _ozon_document(monkeypatch)
    group = next(
        row for row in document["candidate"]["review_manifest"]["copy_sets"]
        if row["target_labels"] == ["ozon:RU"]
    )
    group["title"] = "Different reviewed title"
    body = deepcopy(document["candidate"])
    body.pop("candidate_digest")
    document["candidate"]["candidate_digest"] = _canonical_digest(body)
    with pytest.raises(ValueError, match="canonical candidate drifted"):
        validate_successor_preview(document, offer_id="3956742887", predecessor=predecessor)


def test_generic_successor_rejects_passed_claim_when_recompiled_candidate_is_blocked(monkeypatch):
    predecessor, document = _ozon_document(monkeypatch)
    blocked = deepcopy(document["candidate"])
    blocked["blockers"] = ["REAL_COMPILER_BLOCKER"]
    blocked["automated_quality_gate"] = {"status": "FAILED"}
    blocked["zero_write_simulation"] = {
        "blocker_count": 1, "completed": True, "external_write_count": 0,
    }
    body = deepcopy(blocked)
    body.pop("candidate_digest")
    blocked["candidate_digest"] = _canonical_digest(body)
    successor_plan = document["formal_plan"]["plan_id"]
    monkeypatch.setattr(
        preview_module, "_compile_candidate_for_snapshot",
        lambda snapshot, **_kwargs: deepcopy(
            blocked if snapshot["plan_id"] == successor_plan else predecessor
        ),
    )
    with pytest.raises(ValueError, match="canonical candidate drifted"):
        validate_successor_preview(
            document, offer_id="3956742887", predecessor=predecessor
        )


def test_adopted_successor_identity_drift_still_fails_closed(tmp_path):
    predecessor, document = _document()
    active = deepcopy(document["candidate"])
    active["plan_id"] = "successor-plan"
    document["candidate"]["plan_id"] = "successor-plan"
    document["candidate"]["snapshot_digest"] = "drifted"
    path = tmp_path / "3956742887" / "localized-copy-successor-preview.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="predecessor identity drifted"):
        load_successor_preview(
            offer_id="3956742887", predecessor=active, reports_root=tmp_path
        )


@pytest.mark.parametrize("change, match", [
    (lambda doc: doc.update(status="APPROVED"), "NOT_APPROVED"),
    (lambda doc: doc.update(approval={"approved_by": "fixture"}), "approval authority"),
    (lambda doc: doc["candidate"]["target_labels"].pop(), "15-target"),
    (lambda doc: doc["candidate"]["review_manifest"]["variants"].append({"model_sku": "drift"}), "variants"),
    (lambda doc: doc["candidate"]["review_manifest"]["copy_sets"][0].update(title="changed"), "title"),
])
def test_successor_preview_fails_closed(change, match):
    predecessor, document = _document()
    change(document)
    with pytest.raises(ValueError, match=match):
        validate_successor_preview(
            document, offer_id="3956742887", predecessor=predecessor
        )
