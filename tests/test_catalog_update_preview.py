from domains.product_operations.catalog_update_preview import (
    preview_catalog_update,
    reservations_from_documents,
)


def _row(sku_id, seller_sku, *, price=10):
    return {
        "sku_id": sku_id,
        "shop_cipher": "SHOP-TH",
        "product_id": f"P-{sku_id}",
        "seller_sku": seller_sku,
        "price": price,
        "currency": "THB",
    }


def test_preview_is_dry_run_deterministic_and_lists_exact_changes():
    current = [_row("1", "0945"), _row("2", "0944")]
    incoming = [_row("1", "0945", price=12), _row("3", "0952")]

    first = preview_catalog_update(
        current,
        incoming,
        source_revision="tiktok-search:2026-07-26T01:00:00Z",
        complete_snapshot=True,
    )
    second = preview_catalog_update(
        list(reversed(current)),
        list(reversed(incoming)),
        source_revision="tiktok-search:2026-07-26T01:00:00Z",
        complete_snapshot=True,
    )

    assert first.preview_id == second.preview_id
    assert first.payload()["dry_run"] is True
    assert first.payload()["apply_allowed"] is False
    assert [change.action for change in first.changes] == ["update", "remove", "add"]
    assert first.changes[0].changed_fields == ("price",)
    assert first.payload()["counts"] == {
        "add": 1,
        "update": 1,
        "remove": 1,
        "unchanged": 0,
    }


def test_missing_source_revision_and_unconfirmed_removal_are_blocked():
    preview = preview_catalog_update([_row("1", "0945")], [])

    assert preview.ready_for_review is False
    assert "source revision/version is required" in preview.blockers
    assert any("removals are blocked" in blocker for blocker in preview.blockers)
    assert "incoming snapshot is empty while the current catalog is not" in preview.blockers


def test_duplicate_identity_is_an_explicit_conflict():
    preview = preview_catalog_update(
        [],
        [_row("1", "0945"), _row("1", "0946")],
        source_revision="snapshot-1",
    )

    assert preview.conflicts[0]["code"] == "duplicate_catalog_identity"
    assert "duplicate_catalog_identity" in preview.blockers[0]


def test_legacy_locks_and_verified_claim_numbering_are_reservations():
    states = {
        "3749982947": {"review": {"seller_sku": "0946", "fields_locked": True}},
        "3828540231": {"review": {"seller_sku": "", "fields_locked": False}},
        "ignored": {"review": {"seller_sku": "0952", "fields_locked": False}},
        "approved": {
            "review": {"seller_sku": "", "fields_locked": False},
            "product_approval": {"seller_sku": "0953", "status": "approved"},
        },
    }
    claims = {
        "3749982947": {
            "claimed": True,
            "sku_numbering": {
                "base_sku": "0946",
                "sku_item_nums": ["0946", "0947"],
                "verified": True,
            },
        },
        "3749982951": {
            "claimed": True,
            "sku_numbering": {
                "base_sku": "0946",
                "sku_item_nums": {
                    "variant-a": "0946",
                    "variant-b": "0951",
                },
                "verified": True,
            },
        },
    }

    reservations = reservations_from_documents(states, claims)

    assert {item.seller_sku for item in reservations} == {
        "0946",
        "0947",
        "0951",
        "0953",
    }
    assert any(
        item.offer_id == "approved" and item.source == "product_approval"
        for item in reservations
    )
    assert not any(item.offer_id == "3828540231" for item in reservations)


def test_overlapping_reservations_block_and_next_block_skips_through_0951():
    states = {
        offer_id: {"review": {"seller_sku": "0946", "fields_locked": True}}
        for offer_id in (
            "3749982947",
            "3749982951",
            "3749982953",
            "780091850593",
        )
    }
    claims = {
        "3749982947": {
            "claimed": True,
            "sku_numbering": {
                "base_sku": "0946",
                "sku_item_nums": ["0946", "0947"],
                "verified": True,
            },
        },
        "3749982951": {
            "claimed": True,
            "sku_numbering": {
                "base_sku": "0946",
                "sku_item_nums": ["0946", "0947", "0948", "0949", "0950", "0951"],
                "verified": True,
            },
        },
    }
    current = [_row("1", "0945")]

    preview = preview_catalog_update(
        current,
        current,
        workbench_states=states,
        tiktok_claims=claims,
        source_revision="catalog-before-update",
        requested_sku_count=2,
    )

    overlap = next(
        item
        for item in preview.conflicts
        if item["code"] == "overlapping_seller_sku_reservation"
        and item["seller_sku"] == "0946"
    )
    assert set(overlap["offer_ids"]) == set(states) | {"3749982951"}
    assert preview.next_seller_skus == ("0952", "0953")
    assert preview.ready_for_review is False


def test_catalog_occupancy_and_reservation_cannot_silently_overlap():
    preview = preview_catalog_update(
        [_row("1", "990946")],
        [_row("1", "990946")],
        workbench_states={
            "3828540231": {
                "review": {"seller_sku": "0946", "fields_locked": True}
            }
        },
        source_revision="snapshot-1",
    )

    assert any(
        item["code"] == "reserved_seller_sku_already_in_catalog"
        for item in preview.conflicts
    )


def test_next_candidate_uses_catalog_sequence_without_skipping_a_future_gap():
    preview = preview_catalog_update(
        [_row("1", "0945")],
        [_row("1", "0945")],
        tiktok_claims={
            "future": {
                "claimed": True,
                "sku_numbering": {
                    "base_sku": "0950",
                    "sku_item_nums": ["0950"],
                    "verified": True,
                },
            }
        },
        source_revision="snapshot-1",
        requested_sku_count=2,
    )

    assert preview.next_seller_skus == ("0946", "0947")
