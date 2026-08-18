from copy import deepcopy

import pytest

from shared_platform.category_correction_successor import (
    build_category_correction_successor_payload,
)


OLD = {
    "id": "product-semantic:" + "a" * 64,
    "name": "Home Supplies > Home Decor > Statues & Figurines",
}
WALLPAPER = {
    "id": "product-semantic:dd51045d33dc76d3ad2df9c46af1a1295f10644274cd3b709f0d3170276b74f5",
    "name": "背景墙 > 墙纸、壁纸",
}


def _payload():
    return {
        "plan_id": "omnichannel:" + "1" * 64,
        "product_facts": {
            "title": "Self-Adhesive PVC Wallpaper Roll",
            "description": "Self-adhesive PVC wallpaper for smooth walls.",
            "category": deepcopy(OLD),
        },
        "digests": {"category": "sha256:" + "2" * 64, "other": "keep"},
        "targets": ["ozon:RU"],
        "localized_image_routing": {"routes": {"ozon:RU": "keep"}},
        "pricing": {"frozen": True},
    }


def test_category_correction_changes_only_category_digest_and_plan_identity():
    predecessor = _payload()
    successor = build_category_correction_successor_payload(
        predecessor,
        expected_previous_category=OLD,
        corrected_category=WALLPAPER,
        corrected_category_digest="3" * 64,
    )

    unchanged = deepcopy(successor)
    unchanged["plan_id"] = predecessor["plan_id"]
    unchanged["product_facts"]["category"] = deepcopy(OLD)
    unchanged["digests"]["category"] = predecessor["digests"]["category"]
    assert unchanged == predecessor
    assert successor["plan_id"] != predecessor["plan_id"]
    assert successor["product_facts"]["category"] == WALLPAPER
    assert successor["digests"]["category"] == "sha256:" + "3" * 64


def test_category_correction_rejects_wrong_predecessor_and_non_wallpaper_copy():
    with pytest.raises(ValueError, match="predecessor drifted"):
        build_category_correction_successor_payload(
            _payload(),
            expected_previous_category={"id": "x", "name": "wrong"},
            corrected_category=WALLPAPER,
            corrected_category_digest="3" * 64,
        )
    payload = _payload()
    payload["product_facts"]["title"] = "Decorative figurine"
    payload["product_facts"]["description"] = "Home decor"
    with pytest.raises(ValueError, match="lacks exact product evidence"):
        build_category_correction_successor_payload(
            payload,
            expected_previous_category=OLD,
            corrected_category=WALLPAPER,
            corrected_category_digest="3" * 64,
        )
