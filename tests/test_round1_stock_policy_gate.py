"""The real R1 producer and approval gate bind one reviewable stock decision."""

from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared_platform.publication_rounds import (
    PublicationRoundContractError,
    build_round1_snapshot,
    canonical_digest,
    load_round1_snapshot,
    persist_round1_snapshot,
    validate_round1_reviewable,
)
from shared_platform.publication_stock_policy import default_publication_stock_policy


@pytest.fixture
def producer():
    path = (Path(__file__).resolve().parents[1] / "skills" / "prepare-product-publication"
            / "scripts" / "prepare_product_publication.py")
    spec = importlib.util.spec_from_file_location("r1_stock_policy_producer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _preview():
    return {
        "ok": True,
        "revision": 7,
        "publication_scope": {"selected_labels": ["tiktok:LH_MY"]},
        "product": {
            "title": "Fixture", "seller_sku_candidate": "0988", "cost_cny": 8,
            "weight_kg": 0.2, "package_cm": [20, 20, 3],
            "source_skus": [{"key": "a", "model_sku": "0988", "label": "20 cm",
                             "commercial_facts": {"cost_cny": 8, "weight_kg": 0.2,
                                                  "package_cm": [20, 20, 3]}}],
        },
        "pricing_review": {"target_pricing": {
            "tiktok:LH_MY": {"list_price": 20, "currency": "MYR"}
        }},
    }


def _packet(producer):
    return producer.prepare_offer(
        offer_id="3956742887", requested_targets=["tiktok:LH_MY"],
        preview_builder=lambda _: _preview(),
        image_execution_plan={"schema_version": "first-review-image-plan/v1",
                              "status": "PROPOSED", "source_actions": [],
                              "generated_assets": [],
                              "summary": {"translation_positions": [],
                                          "localized_output_count": 0,
                                          "net_new_output_count": 0,
                                          "paid_generation_required": False}},
        candidate_plan={
            "schema_version": "first-review-candidate-plan/v1",
            "target_candidates": [{"target": "tiktok:LH_MY",
                                   "category": {"status": "PROPOSED",
                                                "candidate": "Decoration",
                                                "authority": "Fixture proposal",
                                                "evidence_digest": "sha256:" + "a" * 64,
                                                "note": "Pending official review"},
                                   "copy": {"status": "PROPOSED", "language": "ms",
                                            "title": "Fixture",
                                            "description": "Fixture description",
                                            "specification_name": "Size",
                                            "variants": [{"seller_sku": "0988",
                                                          "display_name": "20 cm"}]}}],
            "content_group_options": [{"id": "livelyhive-sea",
                                       "label": "LivelyHive SEA",
                                       "description": "Selected brand", "recommended": True}],
        },
    )


def test_producer_exposes_governed_stock_policy_and_approval_gate_accepts_it(
    producer, tmp_path,
):
    packet = _packet(producer)
    assert packet["status"] == "FIRST_REVIEW_READY"
    assert packet["publication_stock_policy"] == default_publication_stock_policy()
    assert validate_round1_reviewable(packet, report_directory=tmp_path) == packet


@pytest.mark.parametrize("replacement", [
    None,
    {},
    {"stock_per_model_sku": 2},
    {**default_publication_stock_policy(), "quantity_per_sku": 2},
    {**default_publication_stock_policy(), "source": "UNVERIFIED_OVERRIDE"},
])
def test_approval_gate_rejects_missing_or_changed_stock_policy(
    producer, tmp_path, replacement,
):
    packet = deepcopy(_packet(producer))
    if replacement is None:
        packet.pop("publication_stock_policy", None)
    else:
        packet["publication_stock_policy"] = replacement
    with pytest.raises(PublicationRoundContractError, match="STOCK_POLICY"):
        validate_round1_reviewable(packet, report_directory=tmp_path)


def test_real_producer_frozen_digest_flows_to_r3_v4_stock_projection(
    producer, tmp_path, monkeypatch,
):
    from domains.product_operations.approved_publication_snapshot import _publication_stock_policy
    from domains import product_operations
    from shared_platform import approved_publication_snapshot_projection as projection
    from shared_platform import publication_r3_image_bridge as bridge
    from shared_platform import publication_rounds as rounds

    packet = _packet(producer)
    state = {"_revision": 8, "review": {"selected_sites": ["lh_my"]},
             "product_approval": {"status": "approved", "approved_by": "Kyle",
                                  "approval_id": "offline-approval",
                                  "input_fingerprint": "offline-fingerprint"}}
    snapshot = build_round1_snapshot(
        first_review=packet, state=state, approved_by="Kyle",
        report_directory=tmp_path,
    )
    assert snapshot["publication_stock_policy"] == packet["publication_stock_policy"]
    assert snapshot["fact_snapshot"]["publication_stock_policy"] == packet["publication_stock_policy"]
    assert snapshot["snapshot_digest"] == canonical_digest(
        {key: value for key, value in snapshot.items() if key != "snapshot_digest"}
    )
    monkeypatch.setattr(rounds, "REPORTS_ROOT", tmp_path)
    path = persist_round1_snapshot(snapshot)
    frozen = load_round1_snapshot(packet["offer_id"])
    assert frozen == snapshot

    identity = {"round1_snapshot_digest": snapshot["snapshot_digest"],
                "generation_identity_digest": "sha256:" + "d" * 64}
    target = "tiktok:LH_MY"
    route = {
        "target_facts": {target: {"category": {
            "candidate": "123 · Decoration", "evidence_digest": "sha256:" + "a" * 64},
            "price": {"sku_prices": []},
            "copy": {"title": "Fixture", "description": "Fixture description"}}},
        "route_locales": {target: "ms-MY"},
        "image_routes": {target: []},
        "bridge_digest": "sha256:" + "b" * 64,
    }
    monkeypatch.setattr(bridge, "validate_r2_identity", lambda documents: identity)
    monkeypatch.setattr(bridge, "build_dual_brand_publication_bridge", lambda **kwargs: route)
    monkeypatch.setattr(product_operations, "build_approved_publication_snapshot_inputs",
                        lambda **kwargs: {"offline": True})
    observed = []

    def v4_projection(payload, *, approved_inputs):
        assert approved_inputs == {"offline": True}
        observed.append(_publication_stock_policy(payload["product_facts"]["stock_policy"]))
        return SimpleNamespace(ready=True, payload=deepcopy(payload))

    monkeypatch.setattr(projection, "project_release_plan_for_publication_snapshot",
                        v4_projection)
    payload = bridge.build_marketplace_stage_payload(
        {"round1_snapshot": frozen, "first_review": packet,
         "generation_result": {}, "translation_result": {}},
        {"plan_id": "offline-common", "payload_digest": "sha256:" + "c" * 64,
         "payload": {"r3_stage_binding": {"r2_identity": identity},
                     "product_facts": {"title": "Fixture", "category": "Decoration",
                                       "selected_sku_keys": ["a"],
                                       "selected_skus": [{"key": "a"}],
                                       "sku_commercial_facts": {"a": {}}},
                     "product_id": packet["offer_id"],
                     "product_revision": 8, "product_package_id": "offline-package",
                     "content_package_id": "offline-content", "images": [],
                     "listing_copy": {}, "targets": ["miaoshou:COMMON"]}},
        {"plan_id": "offline-common", "run_id": "offline-run",
         "targets": [{"target_label": "miaoshou:COMMON", "readback": {}}]},
        policy={}, incidents=[],
    )
    assert observed == [default_publication_stock_policy()]
    assert payload["product_facts"]["stock_policy"] == observed[0]
    assert frozen == snapshot

    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["publication_stock_policy"]["quantity_per_sku"] = 2
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(PublicationRoundContractError, match="snapshot is invalid"):
        load_round1_snapshot(packet["offer_id"])
