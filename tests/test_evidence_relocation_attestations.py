from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from shared_platform.evidence_relocation_attestations import (
    EvidenceRelocationError,
    EvidenceRelocationResolver,
    OFFER_395_REQUIRED_AUTHORITY_DIGESTS,
    build_evidence_relocation_attestation,
    validate_evidence_relocation_attestation,
)
from shared_platform import evidence_relocation_attestations as module


def _ref(path: Path):
    data = path.read_bytes()
    return {"path": str(path.resolve()),
            "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
            "size": len(data)}


def _fixture(tmp_path):
    originals = tmp_path / "originals"
    originals.mkdir(parents=True)
    one = originals / "one.json"
    two = originals / "two.json"
    one.write_text('{"value":1}\n', encoding="utf-8")
    two.write_text('{"value":2}\n', encoding="utf-8")
    refs = [_ref(one), _ref(two)]
    target = tmp_path / "fixed-relocated"
    attestation = build_evidence_relocation_attestation(
        refs, target_root=target, allowed_target_root=target,
        authority_digests=OFFER_395_REQUIRED_AUTHORITY_DIGESTS,
        required_original_refs=refs,
    )
    return refs, target, attestation


def _redigest(attestation):
    value = deepcopy(attestation)
    value.pop("attestation_digest", None)
    return {**value, "attestation_digest": module._digest(value)}


def _validate(attestation, refs, target):
    return validate_evidence_relocation_attestation(
        attestation, allowed_target_root=target,
        expected_authority_digests=OFFER_395_REQUIRED_AUTHORITY_DIGESTS,
        required_original_refs=refs,
    )


def test_builder_is_content_addressed_exclusive_and_resolver_keeps_logical_ref(tmp_path):
    refs, target, attestation = _fixture(tmp_path)
    assert _validate(attestation, refs, target) == attestation
    assert attestation["authority_digests"] == OFFER_395_REQUIRED_AUTHORITY_DIGESTS
    assert len({row["relocated"]["path"] for row in attestation["entries"]}) == 2
    assert all(row["relocated"]["sha256"].removeprefix("sha256:")
               in Path(row["relocated"]["path"]).name
               for row in attestation["entries"])

    replay = build_evidence_relocation_attestation(
        refs, target_root=target, allowed_target_root=target,
        authority_digests=OFFER_395_REQUIRED_AUTHORITY_DIGESTS,
        required_original_refs=refs)
    assert replay == attestation

    resolver = EvidenceRelocationResolver(
        attestation, allowed_target_root=target,
        expected_authority_digests=OFFER_395_REQUIRED_AUTHORITY_DIGESTS,
        required_original_refs=refs)
    logical = {key: refs[0][key] for key in ("path", "sha256")}
    original_bytes = Path(refs[0]["path"]).read_bytes()
    parsed, safe_logical = resolver.load(logical, allowed_roots=[target])
    assert parsed == {"value": 1}
    assert safe_logical == logical
    Path(refs[0]["path"]).unlink()
    assert resolver(logical, [target]) == original_bytes
    assert resolver.resolve_bytes(**logical, allowed_roots=[target]) == original_bytes


def test_self_digest_tamper_and_authority_binding_fail(tmp_path):
    refs, target, attestation = _fixture(tmp_path)
    tampered = deepcopy(attestation)
    tampered["authority_digests"]["domain_closure"] = "sha256:" + "0" * 64
    with pytest.raises(EvidenceRelocationError, match="digest conflicts"):
        _validate(tampered, refs, target)
    with pytest.raises(EvidenceRelocationError, match="authority digests"):
        _validate(_redigest(tampered), refs, target)


def test_missing_and_extra_entries_fail_exact_required_refs(tmp_path):
    refs, target, attestation = _fixture(tmp_path)
    missing = deepcopy(attestation)
    missing["entries"].pop()
    with pytest.raises(EvidenceRelocationError, match="exactly match"):
        _validate(_redigest(missing), refs, target)
    extra = deepcopy(attestation)
    extra["entries"].append(deepcopy(extra["entries"][0]))
    with pytest.raises(EvidenceRelocationError, match="one-to-one"):
        _validate(_redigest(extra), refs, target)


def test_swap_duplicate_hash_path_and_size_fail(tmp_path):
    refs, target, attestation = _fixture(tmp_path)
    attacks = []
    swapped = deepcopy(attestation)
    swapped["entries"][0]["relocated"], swapped["entries"][1]["relocated"] = (
        swapped["entries"][1]["relocated"], swapped["entries"][0]["relocated"])
    attacks.append(swapped)
    duplicate = deepcopy(attestation)
    duplicate["entries"][1] = deepcopy(duplicate["entries"][0])
    attacks.append(duplicate)
    wrong_hash = deepcopy(attestation)
    wrong_hash["entries"][0]["relocated"]["sha256"] = "sha256:" + "0" * 64
    attacks.append(wrong_hash)
    escaped = deepcopy(attestation)
    escaped["entries"][0]["relocated"]["path"] = str((tmp_path / "outside.json").resolve())
    attacks.append(escaped)
    wrong_size = deepcopy(attestation)
    wrong_size["entries"][0]["relocated"]["size"] += 1
    attacks.append(wrong_size)
    for attack in attacks:
        with pytest.raises(EvidenceRelocationError):
            _validate(_redigest(attack), refs, target)


def test_missing_or_tampered_relocated_bytes_fail(tmp_path):
    refs, target, attestation = _fixture(tmp_path)
    first = Path(attestation["entries"][0]["relocated"]["path"])
    first.unlink()
    with pytest.raises(EvidenceRelocationError, match="missing"):
        _validate(attestation, refs, target)

    refs, target, attestation = _fixture(tmp_path / "second")
    first = Path(attestation["entries"][0]["relocated"]["path"])
    first.write_bytes(b"tampered")
    with pytest.raises(EvidenceRelocationError, match="bytes conflict"):
        _validate(attestation, refs, target)


def test_symlink_or_reparse_destination_is_rejected(tmp_path):
    refs, target, attestation = _fixture(tmp_path)
    relocated = Path(attestation["entries"][0]["relocated"]["path"])
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(relocated.read_bytes())
    relocated.unlink()
    try:
        os.symlink(replacement, relocated)
    except OSError:
        pytest.skip("host cannot create test symlink")
    with pytest.raises(EvidenceRelocationError, match="symlink or reparse"):
        _validate(attestation, refs, target)


def test_builder_rejects_duplicate_escape_and_source_symlink(tmp_path):
    originals = tmp_path / "originals"
    originals.mkdir()
    source = originals / "source.json"
    source.write_text("{}\n", encoding="utf-8")
    ref = _ref(source)
    target = tmp_path / "fixed"
    with pytest.raises(EvidenceRelocationError, match="duplicates"):
        build_evidence_relocation_attestation(
            [ref, ref], target_root=target, allowed_target_root=target,
            authority_digests=OFFER_395_REQUIRED_AUTHORITY_DIGESTS)
    with pytest.raises(EvidenceRelocationError, match="fixed allowed"):
        build_evidence_relocation_attestation(
            [ref], target_root=target / "child", allowed_target_root=target,
            authority_digests=OFFER_395_REQUIRED_AUTHORITY_DIGESTS)
    link = originals / "link.json"
    try:
        os.symlink(source, link)
    except OSError:
        return
    linked_ref = {"path": str(link.absolute()), "sha256": ref["sha256"], "size": ref["size"]}
    with pytest.raises(EvidenceRelocationError, match="symlink or reparse"):
        build_evidence_relocation_attestation(
            [linked_ref], target_root=target, allowed_target_root=target,
            authority_digests=OFFER_395_REQUIRED_AUTHORITY_DIGESTS)


def test_resolver_rejects_unattested_swap_but_allows_attested_external_logical_ref(tmp_path):
    refs, target, attestation = _fixture(tmp_path)
    resolver = EvidenceRelocationResolver(attestation, allowed_target_root=target)
    with pytest.raises(EvidenceRelocationError, match="not attested"):
        resolver.resolve_bytes(refs[0]["path"], refs[1]["sha256"],
                               allowed_roots=[target])
    expected = Path(attestation["entries"][0]["relocated"]["path"]).read_bytes()
    assert resolver.resolve_bytes(
        refs[0]["path"], refs[0]["sha256"], allowed_roots=[target]) == expected
    with pytest.raises(EvidenceRelocationError, match="root is not allowed"):
        resolver.resolve_bytes(refs[0]["path"], refs[0]["sha256"],
                               allowed_roots=[tmp_path / "elsewhere"])
