from copy import deepcopy
import json

import pytest

from shared_platform import publication_successor_adoption as adoption_module
from shared_platform import release_store as release_store_module
from shared_platform.release_store import ImmutableReleaseError, ReleaseStore
from domains.product_operations import build_approved_publication_snapshot
from shared_platform.publication_autopilot import _business_snapshot_digest
from shared_platform.publication_autopilot import (
    _canonical_digest,
    persist_release_candidate,
)
from test_approved_publication_snapshot_integration import _approved_full_store

from shared_platform.publication_successor_adoption import (
    PublicationSuccessorAdoptionError,
    validate_successor_adoption_inputs,
)


TARGETS = [
    "tiktok:LH_PH", "tiktok:LH_MY", "tiktok:LH_TH", "tiktok:LH_VN",
    "tiktok:HB_PH", "tiktok:HB_MY", "tiktok:HB_TH", "tiktok:HB_VN",
    "tiktok:MX", "tiktok:GB", "shopee:PH", "shopee:MY", "shopee:TH",
    "shopee:VN", "ozon:RU",
]


def inputs():
    plan = {"product_id": "3956742887", "plan_id": "omnichannel:new", "targets": TARGETS}
    candidate = {
        "offer_id": "3956742887",
        "plan_id": "omnichannel:new",
        "candidate_digest": "a" * 64,
        "snapshot_digest": "sha256:" + "b" * 64,
        "target_labels": TARGETS,
        "status": "NOT_APPROVED",
        "review_blocked": False,
        "review_blockers": [],
        "zero_write_simulation": {"blocker_count": 0, "completed": True, "external_write_count": 0},
    }
    return plan, candidate


def validate(plan, candidate):
    return validate_successor_adoption_inputs(
        formal_plan=plan,
        successor=candidate,
        expected_offer_id="3956742887",
        expected_plan_id="omnichannel:new",
        expected_candidate_digest="a" * 64,
        expected_snapshot_digest="sha256:" + "b" * 64,
        expected_target_labels=TARGETS,
    )


def test_exact_successor_identity_is_accepted_without_mutation():
    plan, candidate = inputs()
    assert validate(plan, candidate) == (plan, candidate)


@pytest.mark.parametrize("drift", ["candidate", "snapshot", "plan", "target_order", "review", "preflight"])
def test_successor_identity_drift_fails_closed(drift):
    plan, candidate = inputs()
    plan, candidate = deepcopy(plan), deepcopy(candidate)
    if drift == "candidate":
        candidate["candidate_digest"] = "c" * 64
    elif drift == "snapshot":
        candidate["snapshot_digest"] = "sha256:" + "c" * 64
    elif drift == "plan":
        plan["plan_id"] = "omnichannel:other"
    elif drift == "target_order":
        candidate["target_labels"] = list(reversed(TARGETS))
    elif drift == "review":
        candidate["review_blocked"] = True
    else:
        candidate["zero_write_simulation"]["external_write_count"] = 1
    with pytest.raises(PublicationSuccessorAdoptionError):
        validate(plan, candidate)


def successor_payload(payload):
    value = deepcopy(payload)
    value["plan_id"] = "omnichannel:reviewed-successor"
    return value


def expected_business(store, payload):
    preview = store.preview_plan(payload)
    when = "2000-01-01T00:00:00+00:00"
    snapshot = build_approved_publication_snapshot({
        **preview, "status": "APPROVED", "approved_at": when,
        "approval": {"status": "APPROVED", "approved_by": "Kyle", "approved_at": when,
                     "user_approved": True, "plan_id": preview["plan_id"],
                     "payload_digest": preview["payload_digest"]},
    }).payload()
    return _business_snapshot_digest(snapshot)


def reviewed_candidate(payload, business_digest):
    candidate = {
        "schema_version": "publication-release-candidate/v1",
        "status": "READY_FOR_FINAL_REVIEW",
        "offer_id": payload["product_id"],
        "plan_id": payload["plan_id"],
        "snapshot_digest": business_digest,
        "business_snapshot_digest": business_digest,
        "target_labels": list(payload["targets"]),
        "blockers": [],
        "zero_write_simulation": {
            "blocker_count": 0,
            "completed": True,
            "external_write_count": 0,
        },
    }
    candidate["candidate_digest"] = _canonical_digest(candidate)
    return candidate


def successor_kwargs(store, payload):
    business = expected_business(store, payload)
    candidate = reviewed_candidate(payload, business)
    candidate_path = persist_release_candidate(
        candidate, reports_root=store.path.parent / "reviewed-candidates"
    )
    return {
        "expected_business_snapshot_digest": business,
        "expected_target_labels": payload["targets"],
        "expected_candidate_digest": candidate["candidate_digest"],
        "reviewed_candidate": candidate,
        "reviewed_candidate_path": candidate_path,
    }


def test_real_store_atomically_preserves_predecessor_and_freezes_successor(tmp_path, monkeypatch):
    store, payload, _response = _approved_full_store(tmp_path, monkeypatch)
    predecessor_before = deepcopy(store.get_plan(payload["plan_id"]))
    result = store.create_and_approve_reviewed_successor(
        payload["plan_id"],
        payload=successor_payload(payload),
        approved_by="Kyle",
        user_approved=True,
        **successor_kwargs(store, successor_payload(payload)),
    )
    predecessor_after = store.get_plan(payload["plan_id"])
    assert predecessor_after["payload"] == predecessor_before["payload"]
    assert predecessor_after["status"] == "SUPERSEDED"
    assert predecessor_after["superseded_by_plan_id"] == "omnichannel:reviewed-successor"
    assert result["plan"]["status"] == "APPROVED"
    assert result["publication_snapshot"]["schema_version"] == "approved-publication-snapshot/v4"
    assert store.get_run("release-run:" + result["plan"]["payload_digest"][:24]) is None


def test_reviewed_successor_rejects_unbound_candidate_digest(tmp_path, monkeypatch):
    store, payload, _response = _approved_full_store(tmp_path, monkeypatch)
    successor = successor_payload(payload)
    kwargs = successor_kwargs(store, successor)
    kwargs["expected_candidate_digest"] = "0" * 64
    with pytest.raises(Exception, match="candidate digest drifted"):
        store.create_and_approve_reviewed_successor(
            payload["plan_id"],
            payload=successor,
            approved_by="Kyle",
            user_approved=True,
            **kwargs,
        )
    assert store.get_plan(successor["plan_id"]) is None
    assert store.get_plan(payload["plan_id"])["status"] == "APPROVED"


def test_real_store_exact_replay_is_idempotent_and_drift_fails_closed(tmp_path, monkeypatch):
    store, payload, _response = _approved_full_store(tmp_path, monkeypatch)
    successor = successor_payload(payload)
    first = store.create_and_approve_reviewed_successor(
        payload["plan_id"], payload=successor, approved_by="Kyle", user_approved=True,
        **successor_kwargs(store, successor)
    )
    repeated = store.create_and_approve_reviewed_successor(
        payload["plan_id"], payload=successor, approved_by="Kyle", user_approved=True,
        **successor_kwargs(store, successor)
    )
    assert first["publication_snapshot"] == repeated["publication_snapshot"]
    assert repeated["created"] is False
    drifted = deepcopy(successor)
    drifted["product_facts"]["description"] += " changed"
    with pytest.raises(ImmutableReleaseError):
        store.create_and_approve_reviewed_successor(
            payload["plan_id"], payload=drifted, approved_by="Kyle", user_approved=True,
            **successor_kwargs(store, drifted)
        )


def test_snapshot_failure_rolls_back_successor_and_predecessor(tmp_path, monkeypatch):
    store, payload, _response = _approved_full_store(tmp_path, monkeypatch)
    predecessor_before = deepcopy(store.get_plan(payload["plan_id"]))
    monkeypatch.setattr(
        release_store_module,
        "_persist_publication_snapshot_in_transaction",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("snapshot failed")),
    )
    with pytest.raises(RuntimeError, match="snapshot failed"):
        store.create_and_approve_reviewed_successor(
            payload["plan_id"],
            payload=successor_payload(payload),
            approved_by="Kyle",
            user_approved=True,
            **successor_kwargs(store, successor_payload(payload)),
        )
    assert store.get_plan(payload["plan_id"]) == predecessor_before
    assert store.get_plan("omnichannel:reviewed-successor") is None
    assert ReleaseStore(store.path).approved_publication_snapshot(
        offer_id=payload["product_id"], plan_id="omnichannel:reviewed-successor"
    ) is None


def test_candidate_persist_failure_happens_before_release_store_approval(tmp_path, monkeypatch):
    candidate = {
        "schema_version": "publication-release-candidate/v1",
        "status": "READY_FOR_FINAL_REVIEW",
        "offer_id": "3956742887",
        "plan_id": "omnichannel:new",
        "candidate_digest": "a" * 64,
        "snapshot_digest": "sha256:" + "b" * 64,
        "target_labels": TARGETS,
        "platform_scope": ["TIKTOK", "SHOPEE", "OZON"],
        "blockers": [],
        "zero_write_simulation": {
            "blocker_count": 0, "completed": True, "external_write_count": 0,
        },
    }
    sidecar = tmp_path / "3956742887" / "publication-successor-preview.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(json.dumps({"candidate": candidate}), encoding="utf-8")
    formal = tmp_path / "formal.json"
    formal.write_text(json.dumps({
        "product_id": "3956742887", "plan_id": "omnichannel:new", "targets": TARGETS,
    }), encoding="utf-8")

    class Snapshot:
        def payload(self):
            return {"snapshot_digest": "sha256:" + "c" * 64}

    class Store:
        approved = 0

        def get_plan(self, _plan_id):
            return {"status": "APPROVED"}

        def preview_plan(self, plan):
            return {**plan, "payload_digest": "d" * 64}

        def create_and_approve_reviewed_successor(self, *_args, **_kwargs):
            self.approved += 1
            raise AssertionError("DB approval must not be reached")

    store = Store()
    monkeypatch.setattr(adoption_module, "load_release_candidate", lambda *_a, **_k: {
        "offer_id": "3956742887", "plan_id": "omnichannel:old", "candidate_digest": "e" * 64,
    })
    monkeypatch.setattr(adoption_module, "load_successor_preview", lambda **_k: {
        **candidate, "status": "NOT_APPROVED", "review_blocked": False, "review_blockers": [],
    })
    monkeypatch.setattr(adoption_module, "build_approved_publication_snapshot", lambda *_a, **_k: Snapshot())
    monkeypatch.setattr(adoption_module, "validate_release_candidate_for_execution", lambda *a, **k: None)
    monkeypatch.setattr(adoption_module, "persist_release_candidate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("candidate persist failed")))

    with pytest.raises(RuntimeError, match="candidate persist failed"):
        adoption_module.adopt_reviewed_successor(
            release_store=store,
            reports_root=tmp_path,
            formal_plan_path=formal,
            offer_id="3956742887",
            predecessor_plan_id="omnichannel:old",
            predecessor_candidate_digest="e" * 64,
            expected_plan_id="omnichannel:new",
            expected_candidate_digest="a" * 64,
            expected_snapshot_digest="sha256:" + "b" * 64,
            expected_target_labels=TARGETS,
            approved_by="Kyle",
            user_approved=True,
        )
    assert store.approved == 0
