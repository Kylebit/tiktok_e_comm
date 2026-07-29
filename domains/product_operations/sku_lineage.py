"""Pure SKU-lineage inheritance and idempotent reservation preflight."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Literal

from .source_identity import SourceProductIdentity


SKU_LINEAGE_SCHEMA_VERSION = "sku-lineage-reservation/v1"
NEW_SOURCE_SKU_RESERVATION_SCHEMA_VERSION = "new-source-sku-reservation/v1"
BLOCKED_SKU_LINEAGE = "BLOCKED_SKU_LINEAGE"
_ACTIVE_PREDECESSOR_STATUSES = frozenset({"APPROVED", "RELEASED"})
_ACTIVE_RESERVATION_STATUSES = frozenset({"ACTIVE", "RESERVED"})
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SKU = re.compile(r"[0-9]{1,32}\Z")


@dataclass(frozen=True)
class ModelSkuAssignment:
    variant_key: str
    model_sku: str

    def __post_init__(self) -> None:
        if _required_text(self.variant_key) is None:
            raise ValueError("variant_key must be a canonical non-empty built-in str")
        if type(self.model_sku) is not str or not _SKU.fullmatch(self.model_sku):
            raise ValueError("model_sku must be a 1-32 digit built-in str")

    def payload(self) -> dict[str, str]:
        return {
            "variant_key": self.variant_key,
            "model_sku": self.model_sku,
        }


@dataclass(frozen=True)
class SkuAssignment:
    seller_sku: str
    model_skus: tuple[ModelSkuAssignment, ...]

    def __post_init__(self) -> None:
        if type(self.seller_sku) is not str or not _SKU.fullmatch(self.seller_sku):
            raise ValueError("seller_sku must be a 1-32 digit built-in str")
        if type(self.model_skus) is not tuple or any(
            type(row) is not ModelSkuAssignment for row in self.model_skus
        ):
            raise ValueError("model_skus must be a tuple of ModelSkuAssignment")
        if not self.model_skus:
            raise ValueError("at least one inherited model SKU is required")
        if len({row.variant_key for row in self.model_skus}) != len(self.model_skus):
            raise ValueError("model SKU variant keys must be unique")
        if len({row.model_sku for row in self.model_skus}) != len(self.model_skus):
            raise ValueError("model SKU values must be unique")

    def payload(self) -> dict[str, Any]:
        return {
            "seller_sku": self.seller_sku,
            "model_skus": [row.payload() for row in self.model_skus],
        }


@dataclass(frozen=True)
class SkuLineageReservation:
    source_identity_digest: str
    predecessor_id: str
    predecessor_revision: int
    predecessor_digest: str
    assignment: SkuAssignment
    reservation_digest: str
    reservation_keys: tuple[str, ...]
    idempotent: bool
    schema_version: str = SKU_LINEAGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SKU_LINEAGE_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be {SKU_LINEAGE_SCHEMA_VERSION!r}"
            )
        if any(
            type(value) is not str or not _SHA256.fullmatch(value)
            for value in (
                self.source_identity_digest,
                self.predecessor_digest,
                self.reservation_digest,
            )
        ):
            raise ValueError("lineage reservation digests must be canonical SHA-256")
        if _required_text(self.predecessor_id) is None:
            raise ValueError("predecessor_id must be a canonical built-in str")
        if type(self.predecessor_revision) is not int or self.predecessor_revision < 0:
            raise ValueError("predecessor_revision must be a non-negative built-in int")
        if type(self.assignment) is not SkuAssignment:
            raise ValueError("assignment must be an exact SkuAssignment")
        if self.reservation_keys != _reservation_keys(self.assignment):
            raise ValueError("reservation_keys do not match the inherited assignment")
        if type(self.idempotent) is not bool:
            raise ValueError("idempotent must be a built-in bool")
        expected_digest = _digest(
            {
                "schema_version": SKU_LINEAGE_SCHEMA_VERSION,
                "source_identity_digest": self.source_identity_digest,
                "predecessor_id": self.predecessor_id,
                "predecessor_revision": self.predecessor_revision,
                "predecessor_digest": self.predecessor_digest,
                "assignment": self.assignment.payload(),
            }
        )
        if self.reservation_digest != expected_digest:
            raise ValueError("reservation_digest does not match SKU lineage")

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_identity_digest": self.source_identity_digest,
            "predecessor_id": self.predecessor_id,
            "predecessor_revision": self.predecessor_revision,
            "predecessor_digest": self.predecessor_digest,
            "assignment": self.assignment.payload(),
            "reservation_digest": self.reservation_digest,
            "reservation_keys": list(self.reservation_keys),
            "idempotent": self.idempotent,
        }


@dataclass(frozen=True)
class SkuLineageResolution:
    status: Literal["READY", "BLOCKED_SKU_LINEAGE"]
    source_identity_digest: str
    lineage_mode: Literal["INHERITED_PREDECESSOR", "NEW_SOURCE", "BLOCKED"]
    assignment: SkuAssignment | None
    predecessor_id: str | None
    predecessor_revision: int | None
    predecessor_digest: str | None
    reservation: SkuLineageReservation | None
    blockers: tuple[str, ...] = ()
    schema_version: str = SKU_LINEAGE_SCHEMA_VERSION

    @property
    def ready(self) -> bool:
        return self.status == "READY"

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "ready": self.ready,
            "source_identity_digest": self.source_identity_digest,
            "lineage_mode": self.lineage_mode,
            "assignment": self.assignment.payload() if self.assignment else None,
            "predecessor_id": self.predecessor_id,
            "predecessor_revision": self.predecessor_revision,
            "predecessor_digest": self.predecessor_digest,
            "reservation": self.reservation.payload() if self.reservation else None,
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class NewSourceSkuReservation:
    """A reservation created after allocation for a source without a predecessor."""

    source_identity_digest: str
    assignment: SkuAssignment
    reservation_digest: str
    reservation_keys: tuple[str, ...]
    idempotent: bool
    schema_version: str = NEW_SOURCE_SKU_RESERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != NEW_SOURCE_SKU_RESERVATION_SCHEMA_VERSION:
            raise ValueError(
                "schema_version must be "
                f"{NEW_SOURCE_SKU_RESERVATION_SCHEMA_VERSION!r}"
            )
        if (
            type(self.source_identity_digest) is not str
            or not _SHA256.fullmatch(self.source_identity_digest)
        ):
            raise ValueError("source_identity_digest must be canonical SHA-256")
        if type(self.assignment) is not SkuAssignment:
            raise ValueError("assignment must be an exact SkuAssignment")
        if self.reservation_keys != _reservation_keys(self.assignment):
            raise ValueError("reservation_keys do not match the assignment")
        if type(self.idempotent) is not bool:
            raise ValueError("idempotent must be a built-in bool")
        expected = new_source_sku_reservation_digest(
            source_identity_digest=self.source_identity_digest,
            assignment=self.assignment,
        )
        if self.reservation_digest != expected:
            raise ValueError("reservation_digest does not match the assignment")

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_identity_digest": self.source_identity_digest,
            "assignment": self.assignment.payload(),
            "reservation_digest": self.reservation_digest,
            "reservation_keys": list(self.reservation_keys),
            "idempotent": self.idempotent,
        }


@dataclass(frozen=True)
class NewSourceSkuReservationResolution:
    status: Literal["READY", "BLOCKED_SKU_LINEAGE"]
    source_identity_digest: str
    reservation: NewSourceSkuReservation | None
    blockers: tuple[str, ...] = ()
    schema_version: str = NEW_SOURCE_SKU_RESERVATION_SCHEMA_VERSION

    @property
    def ready(self) -> bool:
        return self.status == "READY"

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "ready": self.ready,
            "source_identity_digest": self.source_identity_digest,
            "reservation": self.reservation.payload() if self.reservation else None,
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class _Predecessor:
    predecessor_id: str
    revision: int
    status: str
    assignment: SkuAssignment
    digest: str


def resolve_sku_lineage_reservation(
    *,
    source_identity: SourceProductIdentity,
    predecessor_records: Sequence[Mapping[str, Any]],
    existing_reservations: Sequence[Mapping[str, Any]] = (),
) -> SkuLineageResolution:
    """Resolve predecessor SKU inheritance before any new SKU allocation.

    Inputs must be immutable snapshots already loaded by the caller.  This
    function performs no database lookup and does not persist a reservation.
    The returned reservation payload is deterministic and can be inserted
    idempotently by the shared-platform owner.
    """

    if type(source_identity) is not SourceProductIdentity:
        return _blocked(
            "",
            "source_identity must be an exact SourceProductIdentity contract",
        )
    source_digest = source_identity.identity_digest
    records, record_blockers = _predecessors(
        source_identity=source_identity,
        records=predecessor_records,
    )
    if record_blockers:
        return _blocked(source_digest, *record_blockers)
    if len(records) > 1:
        return _blocked(
            source_digest,
            "multiple approved/released predecessors match the canonical source identity",
        )
    reservation_rows, reservation_blockers = _reservation_rows(
        existing_reservations
    )
    if reservation_blockers:
        return _blocked(source_digest, *reservation_blockers)
    if not records:
        return SkuLineageResolution(
            status="READY",
            source_identity_digest=source_digest,
            lineage_mode="NEW_SOURCE",
            assignment=None,
            predecessor_id=None,
            predecessor_revision=None,
            predecessor_digest=None,
            reservation=None,
        )

    predecessor = records[0]
    desired_digest = _digest(
        {
            "schema_version": SKU_LINEAGE_SCHEMA_VERSION,
            "source_identity_digest": source_digest,
            "predecessor_id": predecessor.predecessor_id,
            "predecessor_revision": predecessor.revision,
            "predecessor_digest": predecessor.digest,
            "assignment": predecessor.assignment.payload(),
        }
    )
    desired_keys = _reservation_keys(predecessor.assignment)
    matching: list[Mapping[str, Any]] = []
    conflicts: list[str] = []
    for index, row in enumerate(reservation_rows):
        row_keys = tuple(row["reservation_keys"])
        if not set(desired_keys).intersection(row_keys):
            continue
        if (
            row["source_identity_digest"] == source_digest
            and row["predecessor_id"] == predecessor.predecessor_id
            and row["predecessor_revision"] == predecessor.revision
            and row["predecessor_digest"] == predecessor.digest
            and row["reservation_digest"] == desired_digest
            and row_keys == desired_keys
        ):
            matching.append(row)
            continue
        conflicts.append(
            f"reservation[{index}] overlaps inherited SKU keys but belongs "
            "to another source, predecessor, revision, or digest"
        )
    if conflicts:
        return _blocked(source_digest, *conflicts)
    if len(matching) > 1:
        return _blocked(
            source_digest,
            "multiple active reservations claim the inherited SKU lineage",
        )

    reservation = SkuLineageReservation(
        source_identity_digest=source_digest,
        predecessor_id=predecessor.predecessor_id,
        predecessor_revision=predecessor.revision,
        predecessor_digest=predecessor.digest,
        assignment=predecessor.assignment,
        reservation_digest=desired_digest,
        reservation_keys=desired_keys,
        idempotent=bool(matching),
    )
    return SkuLineageResolution(
        status="READY",
        source_identity_digest=source_digest,
        lineage_mode="INHERITED_PREDECESSOR",
        assignment=predecessor.assignment,
        predecessor_id=predecessor.predecessor_id,
        predecessor_revision=predecessor.revision,
        predecessor_digest=predecessor.digest,
        reservation=reservation,
    )


def new_source_sku_reservation_digest(
    *,
    source_identity_digest: str,
    assignment: SkuAssignment,
) -> str:
    """Return the canonical digest a Store must recompute before persisting."""

    if (
        type(source_identity_digest) is not str
        or not _SHA256.fullmatch(source_identity_digest)
    ):
        raise ValueError("source_identity_digest must be canonical SHA-256")
    if type(assignment) is not SkuAssignment:
        raise ValueError("assignment must be an exact SkuAssignment")
    return _digest(
        {
            "schema_version": NEW_SOURCE_SKU_RESERVATION_SCHEMA_VERSION,
            "source_identity_digest": source_identity_digest,
            "assignment": assignment.payload(),
            "reservation_keys": list(_reservation_keys(assignment)),
        }
    )


def finalize_new_source_sku_reservation(
    *,
    source_identity: SourceProductIdentity,
    assignment: SkuAssignment,
    existing_reservations: Sequence[Mapping[str, Any]] = (),
) -> NewSourceSkuReservationResolution:
    """Validate and finalize a newly allocated assignment without persistence."""

    if type(source_identity) is not SourceProductIdentity:
        return _blocked_new_source(
            "",
            "source_identity must be an exact SourceProductIdentity contract",
        )
    source_digest = source_identity.identity_digest
    if type(assignment) is not SkuAssignment:
        return _blocked_new_source(
            source_digest,
            "assignment must be an exact SkuAssignment contract",
        )
    claims, blockers = _active_reservation_claims(existing_reservations)
    if blockers:
        return _blocked_new_source(source_digest, *blockers)

    desired_keys = _reservation_keys(assignment)
    desired_digest = new_source_sku_reservation_digest(
        source_identity_digest=source_digest,
        assignment=assignment,
    )
    matching: list[Mapping[str, Any]] = []
    conflicts: list[str] = []
    for index, claim in enumerate(claims):
        if not set(desired_keys).intersection(claim["reservation_keys"]):
            continue
        if (
            claim["schema_version"]
            == NEW_SOURCE_SKU_RESERVATION_SCHEMA_VERSION
            and claim["source_identity_digest"] == source_digest
            and claim["assignment"] == assignment
            and claim["reservation_keys"] == desired_keys
            and claim["reservation_digest"] == desired_digest
        ):
            matching.append(claim)
            continue
        conflicts.append(
            f"existing_reservations[{index}] overlaps allocated SKU keys but "
            "belongs to another source or assignment"
        )
    if conflicts:
        return _blocked_new_source(source_digest, *conflicts)
    if len(matching) > 1:
        return _blocked_new_source(
            source_digest,
            "multiple active reservations claim the same new-source assignment",
        )

    reservation = NewSourceSkuReservation(
        source_identity_digest=source_digest,
        assignment=assignment,
        reservation_digest=desired_digest,
        reservation_keys=desired_keys,
        idempotent=bool(matching),
    )
    return NewSourceSkuReservationResolution(
        status="READY",
        source_identity_digest=source_digest,
        reservation=reservation,
    )


def _predecessors(
    *,
    source_identity: SourceProductIdentity,
    records: Sequence[Mapping[str, Any]],
) -> tuple[tuple[_Predecessor, ...], tuple[str, ...]]:
    if not _mapping_sequence(records):
        return (), ("predecessor_records must be a sequence of mappings",)
    matched: list[_Predecessor] = []
    blockers: list[str] = []
    for index, row in enumerate(records):
        status = _required_text(row.get("status"))
        identity = row.get("source_identity")
        if status is None or not isinstance(identity, Mapping):
            blockers.append(
                f"predecessor_records[{index}] has an invalid status or source_identity"
            )
            continue
        source_offer_id = identity.get("source_offer_id")
        source_authority = identity.get("source_authority")
        source_digest = identity.get("identity_digest")
        if not all(
            type(value) is str and value
            for value in (source_offer_id, source_authority, source_digest)
        ):
            blockers.append(
                f"predecessor_records[{index}].source_identity is incomplete"
            )
            continue
        same_canonical = (
            source_offer_id == source_identity.source_offer_id
            and source_authority == source_identity.source_authority
        )
        if source_digest == source_identity.identity_digest and not same_canonical:
            blockers.append(
                f"predecessor_records[{index}] reuses the source identity digest "
                "for a different canonical source"
            )
            continue
        if same_canonical and source_digest != source_identity.identity_digest:
            blockers.append(
                f"predecessor_records[{index}] has a different lineage digest "
                "for the same canonical source"
            )
            continue
        if not same_canonical:
            continue
        normalized_status = status.upper()
        if normalized_status not in _ACTIVE_PREDECESSOR_STATUSES:
            continue
        predecessor_id = _required_text(row.get("predecessor_id"))
        revision = row.get("revision")
        assignment, assignment_error = _assignment(row)
        if (
            predecessor_id is None
            or type(revision) is not int
            or revision < 0
            or assignment_error
            or assignment is None
        ):
            blockers.append(
                f"predecessor_records[{index}] has invalid predecessor/revision/SKU facts"
            )
            continue
        digest = _digest(
            {
                "schema_version": SKU_LINEAGE_SCHEMA_VERSION,
                "source_identity_digest": source_identity.identity_digest,
                "predecessor_id": predecessor_id,
                "revision": revision,
                "assignment": assignment.payload(),
            }
        )
        supplied_digest = row.get("predecessor_digest")
        if supplied_digest not in (None, "") and supplied_digest != digest:
            blockers.append(
                f"predecessor_records[{index}].predecessor_digest does not match"
            )
            continue
        matched.append(
            _Predecessor(
                predecessor_id=predecessor_id,
                revision=revision,
                status=normalized_status,
                assignment=assignment,
                digest=digest,
            )
        )
    return tuple(matched), tuple(dict.fromkeys(blockers))


def _assignment(
    row: Mapping[str, Any],
) -> tuple[SkuAssignment | None, str | None]:
    seller_sku = row.get("seller_sku")
    if type(seller_sku) is not str or not _SKU.fullmatch(seller_sku):
        return None, "seller_sku must be a canonical digit string"
    raw_models = row.get("model_skus")
    if not _mapping_sequence(raw_models):
        return None, "model_skus must be a sequence of mappings"
    models: list[ModelSkuAssignment] = []
    for raw in raw_models:
        variant_key = _required_text(raw.get("variant_key"))
        model_sku = raw.get("model_sku")
        if (
            variant_key is None
            or type(model_sku) is not str
            or not _SKU.fullmatch(model_sku)
        ):
            return None, "model SKU facts are invalid"
        models.append(ModelSkuAssignment(variant_key=variant_key, model_sku=model_sku))
    models.sort(key=lambda value: value.variant_key)
    if not models:
        return None, "at least one inherited model SKU is required"
    if len({model.variant_key for model in models}) != len(models):
        return None, "model SKU variant keys must be unique"
    if len({model.model_sku for model in models}) != len(models):
        return None, "model SKU values must be unique"
    return SkuAssignment(seller_sku=seller_sku, model_skus=tuple(models)), None


def _reservation_rows(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    if not _mapping_sequence(rows):
        return (), ("existing_reservations must be a sequence of mappings",)
    normalized: list[dict[str, Any]] = []
    blockers: list[str] = []
    for index, row in enumerate(rows):
        status = _required_text(row.get("status"))
        if status is None:
            blockers.append(f"existing_reservations[{index}].status is invalid")
            continue
        if status.upper() not in _ACTIVE_RESERVATION_STATUSES:
            continue
        digest_fields = (
            "source_identity_digest",
            "predecessor_digest",
            "reservation_digest",
        )
        if any(
            type(row.get(field)) is not str
            or not _SHA256.fullmatch(row[field])
            for field in digest_fields
        ):
            blockers.append(
                f"existing_reservations[{index}] has an invalid digest"
            )
            continue
        predecessor_id = _required_text(row.get("predecessor_id"))
        predecessor_revision = row.get("predecessor_revision")
        keys = row.get("reservation_keys")
        if (
            predecessor_id is None
            or type(predecessor_revision) is not int
            or predecessor_revision < 0
            or not _text_sequence(keys)
        ):
            blockers.append(
                f"existing_reservations[{index}] has invalid lineage fields"
            )
            continue
        clean_keys = tuple(keys)
        if len(set(clean_keys)) != len(clean_keys):
            blockers.append(
                f"existing_reservations[{index}] has duplicate reservation keys"
            )
            continue
        normalized.append(
            {
                "source_identity_digest": row["source_identity_digest"],
                "predecessor_id": predecessor_id,
                "predecessor_revision": predecessor_revision,
                "predecessor_digest": row["predecessor_digest"],
                "reservation_digest": row["reservation_digest"],
                "reservation_keys": clean_keys,
            }
        )
    return tuple(normalized), tuple(dict.fromkeys(blockers))


def _active_reservation_claims(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    """Normalize active inherited and new-source reservations fail closed."""

    if not _mapping_sequence(rows):
        return (), ("existing_reservations must be a sequence of mappings",)
    normalized: list[dict[str, Any]] = []
    blockers: list[str] = []
    for index, row in enumerate(rows):
        status = _required_text(row.get("status"))
        if status is None:
            blockers.append(f"existing_reservations[{index}].status is invalid")
            continue
        if status.upper() not in _ACTIVE_RESERVATION_STATUSES:
            continue
        schema_version = row.get("schema_version")
        source_digest = row.get("source_identity_digest")
        reservation_digest = row.get("reservation_digest")
        keys = row.get("reservation_keys")
        if schema_version not in (
            SKU_LINEAGE_SCHEMA_VERSION,
            NEW_SOURCE_SKU_RESERVATION_SCHEMA_VERSION,
        ):
            blockers.append(
                f"existing_reservations[{index}].schema_version is unsupported"
            )
            continue
        if (
            type(source_digest) is not str
            or not _SHA256.fullmatch(source_digest)
            or type(reservation_digest) is not str
            or not _SHA256.fullmatch(reservation_digest)
            or not _text_sequence(keys)
            or len(set(keys)) != len(keys)
        ):
            blockers.append(
                f"existing_reservations[{index}] has invalid digest or reservation keys"
            )
            continue
        clean_keys = tuple(keys)
        if schema_version == SKU_LINEAGE_SCHEMA_VERSION:
            inherited, inherited_blockers = _reservation_rows((row,))
            if inherited_blockers or not inherited:
                blockers.append(
                    f"existing_reservations[{index}] has invalid inherited lineage"
                )
                continue
            normalized.append(
                {
                    **inherited[0],
                    "schema_version": schema_version,
                    "assignment": None,
                }
            )
            continue

        raw_assignment = row.get("assignment")
        if not isinstance(raw_assignment, Mapping):
            blockers.append(
                f"existing_reservations[{index}].assignment is invalid"
            )
            continue
        parsed_assignment, assignment_error = _assignment(raw_assignment)
        if parsed_assignment is None or assignment_error:
            blockers.append(
                f"existing_reservations[{index}].assignment is invalid"
            )
            continue
        expected_keys = _reservation_keys(parsed_assignment)
        expected_digest = new_source_sku_reservation_digest(
            source_identity_digest=source_digest,
            assignment=parsed_assignment,
        )
        if clean_keys != expected_keys or reservation_digest != expected_digest:
            blockers.append(
                f"existing_reservations[{index}] does not match its assignment"
            )
            continue
        normalized.append(
            {
                "schema_version": schema_version,
                "source_identity_digest": source_digest,
                "assignment": parsed_assignment,
                "reservation_digest": reservation_digest,
                "reservation_keys": clean_keys,
            }
        )
    return tuple(normalized), tuple(dict.fromkeys(blockers))


def _reservation_keys(assignment: SkuAssignment) -> tuple[str, ...]:
    keys = [_sku_key(assignment.seller_sku)]
    keys.extend(_sku_key(row.model_sku) for row in assignment.model_skus)
    return tuple(dict.fromkeys(keys))


def _sku_key(value: str) -> str:
    return value[-4:].zfill(4)


def _mapping_sequence(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and all(isinstance(row, Mapping) for row in value)
    )


def _text_sequence(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and all(type(item) is str and bool(item) for item in value)
    )


def _required_text(value: Any) -> str | None:
    if type(value) is not str or not value or value != value.strip():
        return None
    return value


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _blocked(
    source_identity_digest: str,
    *details: str,
) -> SkuLineageResolution:
    return SkuLineageResolution(
        status=BLOCKED_SKU_LINEAGE,
        source_identity_digest=source_identity_digest,
        lineage_mode="BLOCKED",
        assignment=None,
        predecessor_id=None,
        predecessor_revision=None,
        predecessor_digest=None,
        reservation=None,
        blockers=tuple(
            dict.fromkeys(f"{BLOCKED_SKU_LINEAGE}: {detail}" for detail in details)
        ),
    )


def _blocked_new_source(
    source_identity_digest: str,
    *details: str,
) -> NewSourceSkuReservationResolution:
    return NewSourceSkuReservationResolution(
        status=BLOCKED_SKU_LINEAGE,
        source_identity_digest=source_identity_digest,
        reservation=None,
        blockers=tuple(
            dict.fromkeys(f"{BLOCKED_SKU_LINEAGE}: {detail}" for detail in details)
        ),
    )


__all__ = [
    "BLOCKED_SKU_LINEAGE",
    "NEW_SOURCE_SKU_RESERVATION_SCHEMA_VERSION",
    "SKU_LINEAGE_SCHEMA_VERSION",
    "ModelSkuAssignment",
    "NewSourceSkuReservation",
    "NewSourceSkuReservationResolution",
    "SkuAssignment",
    "SkuLineageReservation",
    "SkuLineageResolution",
    "finalize_new_source_sku_reservation",
    "new_source_sku_reservation_digest",
    "resolve_sku_lineage_reservation",
]
