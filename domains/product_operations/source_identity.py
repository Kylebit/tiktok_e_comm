"""Strict source-product identity contract for product publication hand-offs.

The adapter consumes already-loaded legacy mappings only.  It never derives a
source offer identity from a merchant item code, title, SKU/specification
label, collect-box identifier, or product name.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Literal


SCHEMA_VERSION = "source-product-identity/v1"
BLOCKED_SOURCE_IDENTITY = "BLOCKED_SOURCE_IDENTITY"
_ASCII_POSITIVE_ID = re.compile(r"[0-9]{1,32}\Z")


@dataclass(frozen=True)
class SourceIdentityEvidence:
    """One authoritative observation of the canonical source offer ID."""

    path: str
    source_offer_id: str

    def __post_init__(self) -> None:
        if type(self.path) is not str or not self.path.strip():
            raise ValueError("source identity evidence path must be a non-empty built-in str")
        normalized, error = _source_offer_id(self.source_offer_id)
        if error or type(self.source_offer_id) is not str or normalized != self.source_offer_id:
            raise ValueError("source identity evidence must contain a canonical source_offer_id")

    def payload(self) -> dict[str, str]:
        return {
            "path": self.path,
            "source_offer_id": self.source_offer_id,
        }


@dataclass(frozen=True)
class SourceProductIdentity:
    """Versioned, immutable identity safe for release-plan memory."""

    source_offer_id: str
    source_item_code: str | None
    source_authority: str
    provenance: tuple[SourceIdentityEvidence, ...]
    identity_digest: str
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        normalized, error = _source_offer_id(self.source_offer_id)
        if error or type(self.source_offer_id) is not str or normalized != self.source_offer_id:
            raise ValueError("source_offer_id must be a canonical positive ASCII digit string")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION!r}")
        authority = _source_authority(self.source_authority)
        if authority is None or authority != self.source_authority:
            raise ValueError("source_authority must be a canonical non-empty built-in str")
        if not self.provenance:
            raise ValueError("source identity provenance is required")
        if any(
            evidence.source_offer_id != self.source_offer_id
            for evidence in self.provenance
        ):
            raise ValueError("all provenance must match source_offer_id exactly")
        if self.source_item_code is not None and (
            type(self.source_item_code) is not str or not self.source_item_code
        ):
            raise ValueError("source_item_code must be None or a non-empty built-in str")
        expected_digest = _identity_digest(
            source_offer_id=self.source_offer_id,
            source_authority=self.source_authority,
            provenance=self.provenance,
        )
        if self.identity_digest != expected_digest:
            raise ValueError("identity_digest does not match source identity lineage")

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_offer_id": self.source_offer_id,
            "source_item_code": self.source_item_code,
            "source_authority": self.source_authority,
            "provenance": [evidence.payload() for evidence in self.provenance],
            "identity_digest": self.identity_digest,
        }


@dataclass(frozen=True)
class SourceProductIdentityResolution:
    """READY identity or an explicit fail-closed product blocker."""

    status: Literal["READY", "BLOCKED_SOURCE_IDENTITY"]
    identity: SourceProductIdentity | None
    blockers: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status == "READY" and self.identity is not None

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "ready": self.ready,
            "identity": self.identity.payload() if self.identity else None,
            "blockers": list(self.blockers),
        }


def resolve_source_product_identity(
    *,
    collect_box: Mapping[str, Any] | None = None,
    precollect: Mapping[str, Any] | None = None,
    source_record: Mapping[str, Any] | None = None,
    source_authority: str = "1688",
) -> SourceProductIdentityResolution:
    """Resolve only authoritative source IDs from production-shaped mappings.

    Authority order is ``collect_box.source_item_id`` followed by
    ``precollect/source_record.source_id``.  Lower-priority observations remain
    provenance and must exactly match the higher-priority canonical digits.
    Invalid populated identity fields block the result instead of being
    skipped.  Missing/null/empty fields may fall through to the next
    authoritative source, but never to display or merchant-code fields.
    """

    authority = _source_authority(source_authority)
    if authority is None:
        return _blocked("source_authority must be a non-empty built-in str")

    observations: list[tuple[str, Any]] = []
    _append_observation(
        observations,
        "collect_box.source_item_id",
        collect_box,
        "source_item_id",
    )
    _append_observation(
        observations,
        "precollect.source_id",
        precollect,
        "source_id",
    )
    for index, record in enumerate(_records(precollect)):
        _append_observation(
            observations,
            f"precollect.records[{index}].source_id",
            record,
            "source_id",
        )
    _append_observation(
        observations,
        "source_record.source_id",
        source_record,
        "source_id",
    )

    evidence: list[SourceIdentityEvidence] = []
    blockers: list[str] = []
    for path, raw_value in observations:
        normalized, error = _source_offer_id(raw_value)
        if error:
            blockers.append(f"{BLOCKED_SOURCE_IDENTITY}: {path} {error}")
            continue
        if normalized is not None:
            evidence.append(
                SourceIdentityEvidence(
                    path=path,
                    source_offer_id=normalized,
                )
            )
    if blockers:
        return SourceProductIdentityResolution(
            status=BLOCKED_SOURCE_IDENTITY,
            identity=None,
            blockers=tuple(dict.fromkeys(blockers)),
        )
    if not evidence:
        return _blocked(
            "no authoritative source_offer_id was found in "
            "collect_box.source_item_id or precollect/source_record.source_id"
        )

    canonical = evidence[0].source_offer_id
    conflicts = tuple(
        observation
        for observation in evidence[1:]
        if observation.source_offer_id != canonical
    )
    if conflicts:
        values = ", ".join(
            f"{observation.path}={observation.source_offer_id}"
            for observation in evidence
        )
        return _blocked(
            "authoritative source_offer_id values conflict; " + values
        )

    provenance = tuple(evidence)
    identity = SourceProductIdentity(
        source_offer_id=canonical,
        source_item_code=_source_item_code(
            collect_box=collect_box,
            precollect=precollect,
            source_record=source_record,
        ),
        source_authority=authority,
        provenance=provenance,
        identity_digest=_identity_digest(
            source_offer_id=canonical,
            source_authority=authority,
            provenance=provenance,
        ),
    )
    return SourceProductIdentityResolution(status="READY", identity=identity)


def _append_observation(
    observations: list[tuple[str, Any]],
    path: str,
    mapping: Mapping[str, Any] | None,
    key: str,
) -> None:
    if not isinstance(mapping, Mapping) or key not in mapping:
        return
    value = mapping.get(key)
    if value is None or (type(value) is str and value == ""):
        return
    observations.append((path, value))


def _records(precollect: Mapping[str, Any] | None) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(precollect, Mapping):
        return ()
    raw_records = precollect.get("records")
    if (
        not isinstance(raw_records, Sequence)
        or isinstance(raw_records, (str, bytes, bytearray))
    ):
        return ()
    return tuple(record for record in raw_records if isinstance(record, Mapping))


def _source_offer_id(value: Any) -> tuple[str | None, str | None]:
    if type(value) is int:
        text = str(value)
    elif type(value) is str:
        text = value
    else:
        return None, "must be a built-in str or int containing 1-32 ASCII digits"
    if not _ASCII_POSITIVE_ID.fullmatch(text) or int(text) <= 0:
        return None, "must be a positive 1-32 digit ASCII decimal ID"
    return text, None


def _source_authority(value: Any) -> str | None:
    if type(value) is not str:
        return None
    normalized = value.strip().casefold()
    return normalized or None


def _source_item_code(
    *,
    collect_box: Mapping[str, Any] | None,
    precollect: Mapping[str, Any] | None,
    source_record: Mapping[str, Any] | None,
) -> str | None:
    candidates: list[Any] = []
    for mapping in (collect_box, source_record, precollect, *_records(precollect)):
        if not isinstance(mapping, Mapping):
            continue
        candidates.extend(
            mapping.get(key)
            for key in ("source_item_code", "itemNum", "item_num")
            if key in mapping
        )
    for value in candidates:
        if type(value) is str and value.strip():
            return value.strip()
        if type(value) is int:
            return str(value)
    return None


def _identity_digest(
    *,
    source_offer_id: str,
    source_authority: str,
    provenance: tuple[SourceIdentityEvidence, ...],
) -> str:
    digest_payload = {
        "schema_version": SCHEMA_VERSION,
        "source_authority": source_authority,
        "source_offer_id": source_offer_id,
        "provenance": [evidence.payload() for evidence in provenance],
    }
    encoded = json.dumps(
        digest_payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _blocked(detail: str) -> SourceProductIdentityResolution:
    return SourceProductIdentityResolution(
        status=BLOCKED_SOURCE_IDENTITY,
        identity=None,
        blockers=(f"{BLOCKED_SOURCE_IDENTITY}: {detail}",),
    )


__all__ = [
    "BLOCKED_SOURCE_IDENTITY",
    "SCHEMA_VERSION",
    "SourceIdentityEvidence",
    "SourceProductIdentity",
    "SourceProductIdentityResolution",
    "resolve_source_product_identity",
]
