from copy import deepcopy

import pytest

from domains.product_operations import (
    BLOCKED_SOURCE_IDENTITY,
    SOURCE_PRODUCT_IDENTITY_SCHEMA_VERSION,
    SourceIdentityEvidence,
    SourceProductIdentity,
    resolve_source_product_identity,
)


def _live_shape():
    return {
        "collect_box": {
            "source_item_id": "986159122616",
            "source_item_code": "JD5047（38*45cm）",
            "source_title": "Bear wall sticker display title",
        },
        "precollect": {
            "records": [
                {
                    "source_id": "986159122616",
                    "source_item_code": "JD5047（38*45cm）",
                    "name": "38*45cm display specification",
                }
            ]
        },
        "source": {
            "source_id": "986159122616",
            "source_item_code": "JD5047（38*45cm）",
            "title": "Display title must not establish identity",
        },
    }


def test_live_shaped_1688_identity_strictly_separates_offer_id_and_item_code():
    fixture = _live_shape()
    before = deepcopy(fixture)

    result = resolve_source_product_identity(
        collect_box=fixture["collect_box"],
        precollect=fixture["precollect"],
        source_record=fixture["source"],
    )

    assert result.ready is True
    assert result.status == "READY"
    identity = result.identity
    assert identity is not None
    assert identity.schema_version == SOURCE_PRODUCT_IDENTITY_SCHEMA_VERSION
    assert identity.source_offer_id == "986159122616"
    assert identity.source_item_code == "JD5047（38*45cm）"
    assert identity.source_authority == "1688"
    assert identity.identity_digest.startswith("sha256:")
    assert [row.path for row in identity.provenance] == [
        "collect_box.source_item_id",
        "precollect.records[0].source_id",
        "source_record.source_id",
    ]
    assert fixture == before


def test_identity_digest_is_stable_for_the_same_lineage():
    fixture = _live_shape()

    first = resolve_source_product_identity(
        collect_box=fixture["collect_box"],
        precollect=fixture["precollect"],
        source_record=fixture["source"],
    )
    second = resolve_source_product_identity(
        collect_box=deepcopy(fixture["collect_box"]),
        precollect=deepcopy(fixture["precollect"]),
        source_record=deepcopy(fixture["source"]),
    )

    assert first.identity is not None
    assert second.identity is not None
    assert first.identity.identity_digest == second.identity.identity_digest


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        986159122616.0,
        "986159A22616",
        "0",
        0,
        "JD5047（38*45cm）",
        " 986159122616 ",
        "١٢٣",
        "1" * 33,
    ],
)
def test_invalid_or_missing_offer_id_fails_closed(value):
    collect_box = {"source_item_code": "JD5047（38*45cm）"}
    if value is not None:
        collect_box["source_item_id"] = value

    result = resolve_source_product_identity(collect_box=collect_box)

    assert result.ready is False
    assert result.status == BLOCKED_SOURCE_IDENTITY
    assert result.identity is None
    assert all(
        blocker.startswith(f"{BLOCKED_SOURCE_IDENTITY}:")
        for blocker in result.blockers
    )


def test_null_primary_can_use_real_precollect_identity_but_never_item_code():
    result = resolve_source_product_identity(
        collect_box={
            "source_item_id": None,
            "source_item_code": "JD5047（38*45cm）",
        },
        precollect={"records": [{"source_id": "986159122616"}]},
    )

    assert result.ready is True
    assert result.identity is not None
    assert result.identity.source_offer_id == "986159122616"
    assert result.identity.source_item_code == "JD5047（38*45cm）"


def test_authoritative_offer_id_conflict_fails_closed():
    result = resolve_source_product_identity(
        collect_box={"source_item_id": "986159122616"},
        precollect={"records": [{"source_id": "986159122617"}]},
        source_record={"source_id": "986159122616"},
    )

    assert result.status == BLOCKED_SOURCE_IDENTITY
    assert result.identity is None
    assert "conflict" in result.blockers[0]
    assert "collect_box.source_item_id=986159122616" in result.blockers[0]
    assert "precollect.records[0].source_id=986159122617" in result.blockers[0]


def test_invalid_secondary_identity_cannot_be_ignored_when_primary_is_valid():
    result = resolve_source_product_identity(
        collect_box={"source_item_id": "986159122616"},
        source_record={"source_id": "JD5047（38*45cm）"},
    )

    assert result.status == BLOCKED_SOURCE_IDENTITY
    assert result.identity is None
    assert "source_record.source_id" in result.blockers[0]


def test_lineage_change_changes_digest_without_changing_offer_identity():
    collect_only = resolve_source_product_identity(
        collect_box={"source_item_id": "986159122616"},
    )
    with_matching_record = resolve_source_product_identity(
        collect_box={"source_item_id": "986159122616"},
        source_record={"source_id": "986159122616"},
    )

    assert collect_only.identity is not None
    assert with_matching_record.identity is not None
    assert collect_only.identity.source_offer_id == (
        with_matching_record.identity.source_offer_id
    )
    assert collect_only.identity.identity_digest != (
        with_matching_record.identity.identity_digest
    )


def test_display_and_item_code_changes_do_not_change_offer_identity_or_digest():
    first = resolve_source_product_identity(
        collect_box={
            "source_item_id": "986159122616",
            "source_item_code": "JD5047（38*45cm）",
            "source_title": "Old display title",
        }
    )
    second = resolve_source_product_identity(
        collect_box={
            "source_item_id": "986159122616",
            "source_item_code": "NEW-CODE / renamed specification",
            "source_title": "New display title",
        }
    )

    assert first.identity is not None
    assert second.identity is not None
    assert first.identity.source_offer_id == second.identity.source_offer_id
    assert first.identity.identity_digest == second.identity.identity_digest
    assert first.identity.source_item_code != second.identity.source_item_code


def test_builtin_integer_id_is_accepted_but_bool_and_subclasses_are_not():
    accepted = resolve_source_product_identity(
        collect_box={"source_item_id": 986159122616}
    )

    class StringId(str):
        pass

    rejected = resolve_source_product_identity(
        collect_box={"source_item_id": StringId("986159122616")}
    )

    assert accepted.ready is True
    assert accepted.identity is not None
    assert accepted.identity.source_offer_id == "986159122616"
    assert rejected.status == BLOCKED_SOURCE_IDENTITY


def test_contract_constructor_rejects_noncanonical_identity_or_forged_digest():
    evidence = (
        SourceIdentityEvidence(
            path="collect_box.source_item_id",
            source_offer_id="986159122616",
        ),
    )

    with pytest.raises(ValueError, match="canonical positive"):
        SourceProductIdentity(
            source_offer_id=True,
            source_item_code=None,
            source_authority="1688",
            provenance=evidence,
            identity_digest="sha256:forged",
        )
    with pytest.raises(ValueError, match="identity_digest"):
        SourceProductIdentity(
            source_offer_id="986159122616",
            source_item_code="JD5047（38*45cm）",
            source_authority="1688",
            provenance=evidence,
            identity_digest="sha256:forged",
        )
