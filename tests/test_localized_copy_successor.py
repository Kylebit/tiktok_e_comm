from copy import deepcopy
import hashlib
import json

import pytest

from shared_platform.localized_copy_successor import (
    build_localized_copy_successor_candidate,
    build_localized_copy_successor_payload,
)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _facts():
    content = {
        "tiktok:PH": {"title": "PH title", "description": "English", "locale": "en-PH"},
        "shopee:PH": {"title": "PH title", "description": "English", "locale": "en-PH"},
        "shopee:MY": {"title": "MY title", "description": "English", "locale": "ms-MY"},
        "shopee:TH": {"title": "TH title", "description": "English", "locale": "th-TH"},
        "shopee:VN": {"title": "VN title", "description": "English", "locale": "vi-VN"},
    }
    payload = {
        "plan_id": "omnichannel:" + "1" * 64,
        "targets": list(content),
        "product_facts": {"content_by_target": content, "images": ["https://x.test/1.jpg"]},
        "digests": {"content": "sha256:" + "2" * 64, "images": "sha256:" + "3" * 64},
        "pricing": {"shopee:MY": "37"},
    }
    snapshot = {
        "plan_id": payload["plan_id"],
        "publication_targets": [
            {"target_label": target} for target in payload["targets"]
        ],
        "product": deepcopy(payload["product_facts"]),
        "business_snapshot_digest": "sha256:" + "4" * 64,
    }
    return payload, snapshot


def _localized():
    return {
        "shopee:MY": "Huraian Bahasa Melayu",
        "shopee:TH": "คำอธิบายภาษาไทย",
        "shopee:VN": "Mô tả bằng tiếng Việt",
    }


def test_localized_copy_successor_changes_only_bound_descriptions_and_identities():
    payload, snapshot = _facts()
    original_payload, original_snapshot = deepcopy(payload), deepcopy(snapshot)
    successor = build_localized_copy_successor_payload(
        payload,
        predecessor_business_snapshot=snapshot,
        localized_descriptions=_localized(),
    )

    expected = deepcopy(payload)
    for target, description in _localized().items():
        expected["product_facts"]["content_by_target"][target]["description"] = description
    expected["digests"]["content"] = "sha256:" + _digest(
        expected["product_facts"]["content_by_target"]
    )
    expected.pop("plan_id")
    expected["plan_id"] = "omnichannel:" + _digest(expected)
    assert successor == expected
    assert successor["product_facts"]["content_by_target"]["shopee:PH"] == payload["product_facts"]["content_by_target"]["shopee:PH"]
    assert payload == original_payload
    assert snapshot == original_snapshot


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda p, s, d: d.pop("shopee:VN"), "coverage"),
        (lambda p, s, d: s.update(plan_id="omnichannel:" + "9" * 64), "identity"),
        (lambda p, s, d: s["product"]["content_by_target"]["shopee:MY"].update(title="drift"), "content drifted"),
        (lambda p, s, d: p["product_facts"]["content_by_target"]["shopee:TH"].update(locale="en-TH"), "content drifted"),
        (lambda p, s, d: d.update({"shopee:MY": "English"}), "did not change"),
    ],
)
def test_localized_copy_successor_fails_closed_on_drift(mutate, match):
    payload, snapshot = _facts()
    descriptions = _localized()
    mutate(payload, snapshot, descriptions)
    with pytest.raises(ValueError, match=match):
        build_localized_copy_successor_payload(
            payload,
            predecessor_business_snapshot=snapshot,
            localized_descriptions=descriptions,
        )


def _review_candidate():
    groups = []
    targets = []
    for target, locale in (("shopee:PH", "en-PH"), ("shopee:MY", "ms-MY"), ("shopee:TH", "th-TH"), ("shopee:VN", "vi-VN")):
        copy_id = "copy-" + target[-2:]
        groups.append({"copy_set_id": copy_id, "language": locale, "title": target + " title", "description": "English", "target_labels": [target]})
        targets.append({"target_label": target, "copy_set_id": copy_id, "locale": locale, "image_set_id": "frozen", "prices": [{"calculation": {"kind": "frozen"}}]})
    body = {
        "schema_version": "publication-release-candidate/v1", "status": "READY_FOR_FINAL_REVIEW",
        "offer_id": "3956742887", "product_revision": 5, "plan_id": "old", "snapshot_digest": "sha256:" + "4" * 64,
        "target_labels": [x["target_label"] for x in targets], "platform_scope": ["SHOPEE"], "variant_count": 1, "blockers": [],
        "durable_quality_evidence": {"status": "PASSED", "checks": [{"code": "frozen"}]},
        "review_manifest": {"copy_sets": groups, "targets": targets, "variants": [{"model_sku": "0988"}], "image_sets": [{"images": [{"role": "cover"}]}]},
    }
    body["review_manifest"]["manifest_digest"] = _digest(body["review_manifest"])
    body["candidate_digest"] = _digest(body)
    return body


def test_successor_candidate_preserves_complete_review_metadata():
    old = _review_candidate()
    compiled = deepcopy(old)
    compiled.update(plan_id="new", snapshot_digest="sha256:" + "5" * 64)
    for group in compiled["review_manifest"]["copy_sets"]:
        target = group["target_labels"][0]
        if target in _localized():
            group["description"] = _localized()[target]
    result = build_localized_copy_successor_candidate(old, compiled_candidate=compiled, localized_descriptions=_localized())
    assert result["durable_quality_evidence"] == old["durable_quality_evidence"]
    assert result["review_manifest"]["image_sets"] == old["review_manifest"]["image_sets"]
    assert result["review_manifest"]["variants"] == old["review_manifest"]["variants"]
    for before, after in zip(old["review_manifest"]["targets"], result["review_manifest"]["targets"]):
        before = deepcopy(before); after = deepcopy(after)
        before.pop("copy_set_id"); after.pop("copy_set_id")
        assert after == before
