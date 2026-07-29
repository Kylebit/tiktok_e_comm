from copy import deepcopy

import pytest

from domains.product_operations import (
    BLOCKED_SKU_LINEAGE,
    NEW_SOURCE_SKU_RESERVATION_SCHEMA_VERSION,
    ModelSkuAssignment,
    SkuAssignment,
    finalize_new_source_sku_reservation,
    new_source_sku_reservation_digest,
    resolve_sku_lineage_reservation,
    resolve_source_product_identity,
)


def _identity(source_offer_id="986159122616"):
    resolution = resolve_source_product_identity(
        collect_box={"source_item_id": source_offer_id}
    )
    assert resolution.identity is not None
    return resolution.identity


def _predecessor(identity=None, **changes):
    identity = identity or _identity()
    record = {
        "predecessor_id": "release-plan:source-986159122616:v31",
        "revision": 31,
        "status": "RELEASED",
        "source_identity": {
            "source_offer_id": identity.source_offer_id,
            "source_authority": identity.source_authority,
            "identity_digest": identity.identity_digest,
        },
        "seller_sku": "0956",
        "model_skus": [
            {"variant_key": "38x45-natural", "model_sku": "0956"},
            {"variant_key": "38x45-white", "model_sku": "0957"},
        ],
    }
    record.update(changes)
    return record


def test_current_shaped_predecessor_is_inherited_before_new_allocation():
    identity = _identity()
    record = _predecessor(identity)
    before = deepcopy(record)

    result = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(record,),
    )

    assert result.ready is True
    assert result.lineage_mode == "INHERITED_PREDECESSOR"
    assert result.assignment is not None
    assert result.assignment.seller_sku == "0956"
    assert [row.model_sku for row in result.assignment.model_skus] == [
        "0956",
        "0957",
    ]
    assert "0958" not in str(result.payload())
    assert "0959" not in str(result.payload())
    assert result.source_identity_digest == identity.identity_digest
    assert result.predecessor_id == record["predecessor_id"]
    assert result.predecessor_revision == 31
    assert result.predecessor_digest.startswith("sha256:")
    assert result.reservation is not None
    assert result.reservation.reservation_keys == ("0956", "0957")
    assert result.reservation.reservation_digest.startswith("sha256:")
    assert result.reservation.idempotent is False
    assert record == before


def test_exact_reservation_replay_is_idempotent_and_digest_stable():
    identity = _identity()
    first = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(_predecessor(identity),),
    )
    assert first.reservation is not None
    existing = {
        **first.reservation.payload(),
        "status": "ACTIVE",
    }

    second = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(_predecessor(identity),),
        existing_reservations=(existing,),
    )

    assert second.ready is True
    assert second.reservation is not None
    assert second.reservation.idempotent is True
    assert (
        second.reservation.reservation_digest
        == first.reservation.reservation_digest
    )
    assert second.predecessor_digest == first.predecessor_digest


def test_approved_to_released_status_transition_keeps_the_same_lineage_digest():
    identity = _identity()
    approved = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(_predecessor(identity, status="APPROVED"),),
    )
    assert approved.reservation is not None
    existing = {**approved.reservation.payload(), "status": "ACTIVE"}

    released = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(_predecessor(identity, status="RELEASED"),),
        existing_reservations=(existing,),
    )

    assert released.ready is True
    assert released.predecessor_digest == approved.predecessor_digest
    assert released.reservation is not None
    assert released.reservation.idempotent is True


def test_no_predecessor_allows_new_source_allocation_only_after_preflight():
    identity = _identity()

    result = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(),
    )

    assert result.ready is True
    assert result.lineage_mode == "NEW_SOURCE"
    assert result.assignment is None
    assert result.reservation is None


def test_multiple_same_source_predecessors_fail_closed():
    identity = _identity()

    result = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(
            _predecessor(identity),
            _predecessor(
                identity,
                predecessor_id="release-plan:source-986159122616:v30",
                revision=30,
                status="APPROVED",
            ),
        ),
    )

    assert result.status == BLOCKED_SKU_LINEAGE
    assert result.ready is False
    assert "multiple approved/released predecessors" in result.blockers[0]


def test_same_canonical_source_with_different_lineage_digest_fails_closed():
    identity = _identity()
    record = _predecessor(identity)
    record["source_identity"]["identity_digest"] = "sha256:" + "1" * 64

    result = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(record,),
    )

    assert result.status == BLOCKED_SKU_LINEAGE
    assert "different lineage digest" in result.blockers[0]


def test_digest_reuse_for_another_canonical_source_fails_closed():
    identity = _identity()
    record = _predecessor(identity)
    record["source_identity"]["source_offer_id"] = "986159122617"

    result = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(record,),
    )

    assert result.status == BLOCKED_SKU_LINEAGE
    assert "different canonical source" in result.blockers[0]


def test_cross_source_reservation_overlap_fails_closed():
    identity = _identity()
    first = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(_predecessor(identity),),
    )
    assert first.reservation is not None
    conflicting = {
        **first.reservation.payload(),
        "status": "ACTIVE",
        "source_identity_digest": "sha256:" + "2" * 64,
    }

    result = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(_predecessor(identity),),
        existing_reservations=(conflicting,),
    )

    assert result.status == BLOCKED_SKU_LINEAGE
    assert "belongs to another source" in result.blockers[0]


def test_tampered_predecessor_or_reservation_digest_fails_closed():
    identity = _identity()
    predecessor = _predecessor(
        identity,
        predecessor_digest="sha256:" + "3" * 64,
    )
    bad_predecessor = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(predecessor,),
    )

    assert bad_predecessor.status == BLOCKED_SKU_LINEAGE
    assert "predecessor_digest does not match" in bad_predecessor.blockers[0]

    clean = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(_predecessor(identity),),
    )
    assert clean.reservation is not None
    reservation = {
        **clean.reservation.payload(),
        "status": "ACTIVE",
        "reservation_digest": "sha256:" + "4" * 64,
    }
    bad_reservation = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(_predecessor(identity),),
        existing_reservations=(reservation,),
    )

    assert bad_reservation.status == BLOCKED_SKU_LINEAGE
    assert "another source, predecessor, revision, or digest" in (
        bad_reservation.blockers[0]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"revision": True},
        {"revision": 31.0},
        {"seller_sku": 956},
        {"seller_sku": "SKU-0956"},
        {"model_skus": "0956"},
        {"model_skus": []},
        {
            "model_skus": [
                {"variant_key": "same", "model_sku": "0956"},
                {"variant_key": "same", "model_sku": "0957"},
            ]
        },
    ],
)
def test_predecessor_type_or_assignment_errors_fail_closed(change):
    identity = _identity()

    result = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(_predecessor(identity, **change),),
    )

    assert result.status == BLOCKED_SKU_LINEAGE
    assert all(
        blocker.startswith(f"{BLOCKED_SKU_LINEAGE}:")
        for blocker in result.blockers
    )


def test_non_mapping_input_records_fail_closed():
    identity = _identity()

    predecessor_error = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=("not-a-record",),
    )
    reservation_error = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(),
        existing_reservations=(object(),),
    )

    assert predecessor_error.status == BLOCKED_SKU_LINEAGE
    assert reservation_error.status == BLOCKED_SKU_LINEAGE


def test_wrong_source_identity_contract_type_fails_closed():
    result = resolve_sku_lineage_reservation(
        source_identity={"identity_digest": "sha256:" + "1" * 64},
        predecessor_records=(),
    )

    assert result.status == BLOCKED_SKU_LINEAGE
    assert "exact SourceProductIdentity" in result.blockers[0]


def _new_assignment():
    return SkuAssignment(
        seller_sku="0958",
        model_skus=(
            ModelSkuAssignment(variant_key="38x45-natural", model_sku="0958"),
            ModelSkuAssignment(variant_key="38x45-white", model_sku="0959"),
        ),
    )


def test_new_source_assignment_is_finalized_with_a_dedicated_typed_contract():
    identity = _identity()
    preflight = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(),
    )
    assert preflight.lineage_mode == "NEW_SOURCE"

    result = finalize_new_source_sku_reservation(
        source_identity=identity,
        assignment=_new_assignment(),
    )

    assert result.ready is True
    assert result.reservation is not None
    payload = result.reservation.payload()
    assert payload["schema_version"] == NEW_SOURCE_SKU_RESERVATION_SCHEMA_VERSION
    assert payload["reservation_keys"] == ["0958", "0959"]
    assert "predecessor_id" not in payload
    assert "predecessor_revision" not in payload
    assert payload["reservation_digest"] == new_source_sku_reservation_digest(
        source_identity_digest=identity.identity_digest,
        assignment=_new_assignment(),
    )


def test_new_source_exact_replay_is_idempotent_and_store_verifiable():
    identity = _identity()
    first = finalize_new_source_sku_reservation(
        source_identity=identity,
        assignment=_new_assignment(),
    )
    assert first.reservation is not None
    stored = {**first.reservation.payload(), "status": "ACTIVE"}

    replay = finalize_new_source_sku_reservation(
        source_identity=identity,
        assignment=_new_assignment(),
        existing_reservations=(stored,),
    )

    assert replay.ready is True
    assert replay.reservation is not None
    assert replay.reservation.idempotent is True
    assert replay.reservation.reservation_digest == (
        new_source_sku_reservation_digest(
            source_identity_digest=identity.identity_digest,
            assignment=replay.reservation.assignment,
        )
    )


def test_new_source_cross_source_overlap_is_blocked_concurrency_shape():
    identity = _identity()
    other_identity = _identity("986159122617")
    other = finalize_new_source_sku_reservation(
        source_identity=other_identity,
        assignment=_new_assignment(),
    )
    assert other.reservation is not None

    result = finalize_new_source_sku_reservation(
        source_identity=identity,
        assignment=_new_assignment(),
        existing_reservations=(
            {**other.reservation.payload(), "status": "RESERVED"},
        ),
    )

    assert result.status == BLOCKED_SKU_LINEAGE
    assert "belongs to another source or assignment" in result.blockers[0]


def test_new_source_overlap_with_inherited_reservation_is_blocked():
    identity = _identity()
    inherited = resolve_sku_lineage_reservation(
        source_identity=identity,
        predecessor_records=(
            _predecessor(
                identity,
                seller_sku="0958",
                model_skus=[
                    {"variant_key": "38x45-natural", "model_sku": "0958"},
                    {"variant_key": "38x45-white", "model_sku": "0959"},
                ],
            ),
        ),
    )
    assert inherited.reservation is not None

    result = finalize_new_source_sku_reservation(
        source_identity=identity,
        assignment=_new_assignment(),
        existing_reservations=(
            {**inherited.reservation.payload(), "status": "ACTIVE"},
        ),
    )

    assert result.status == BLOCKED_SKU_LINEAGE
    assert "belongs to another source or assignment" in result.blockers[0]


def test_duplicate_new_source_replays_are_blocked_as_concurrent_ambiguity():
    identity = _identity()
    first = finalize_new_source_sku_reservation(
        source_identity=identity,
        assignment=_new_assignment(),
    )
    assert first.reservation is not None
    stored = {**first.reservation.payload(), "status": "ACTIVE"}

    result = finalize_new_source_sku_reservation(
        source_identity=identity,
        assignment=_new_assignment(),
        existing_reservations=(stored, deepcopy(stored)),
    )

    assert result.status == BLOCKED_SKU_LINEAGE
    assert "multiple active reservations" in result.blockers[0]


@pytest.mark.parametrize(
    "existing_change",
    [
        {"schema_version": "invented/v1"},
        {"reservation_digest": "sha256:" + "8" * 64},
        {"reservation_keys": ["0958"]},
        {"assignment": {"seller_sku": "0958", "model_skus": []}},
        {"status": True},
    ],
)
def test_malformed_new_source_reservations_fail_closed(existing_change):
    identity = _identity()
    first = finalize_new_source_sku_reservation(
        source_identity=identity,
        assignment=_new_assignment(),
    )
    assert first.reservation is not None
    stored = {**first.reservation.payload(), "status": "ACTIVE"}
    stored.update(existing_change)

    result = finalize_new_source_sku_reservation(
        source_identity=identity,
        assignment=_new_assignment(),
        existing_reservations=(stored,),
    )

    assert result.status == BLOCKED_SKU_LINEAGE


@pytest.mark.parametrize("assignment", [{"seller_sku": "0958"}, True, None])
def test_new_source_assignment_must_be_the_exact_typed_contract(assignment):
    result = finalize_new_source_sku_reservation(
        source_identity=_identity(),
        assignment=assignment,
    )

    assert result.status == BLOCKED_SKU_LINEAGE
    assert "exact SkuAssignment" in result.blockers[0]
