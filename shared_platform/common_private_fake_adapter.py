"""Closed in-memory fake provider for the private COMMON ledger contract.

This is intentionally not injectable into production release adapters. It has
no network or credential capability and every result denies real authority.
"""
from __future__ import annotations

import json

from shared_platform.common_offer_authority_store import (
    MODE, CommonAuthorityBlocked, PrivateCommonAuthorityStore, canonical_bytes,
)


class PrivateFakeCommonTransport:
    def __init__(self):
        self.writes: list[bytes] = []
        self.documents: dict[str, bytes] = {}

    def write(self, identity, mutation_bytes):
        self.writes.append(mutation_bytes)
        self.documents[json.dumps(identity, sort_keys=True)] = mutation_bytes

    def read(self, identity):
        return json.loads(self.documents[json.dumps(identity, sort_keys=True)])


def execute_fake_common(authority, reservation_id, transport):
    """Read actual bytes from the domain plan, consume once, fake write/readback.

    Neither the mutation payload nor a trusted identity is supplied by a caller.
    The exact fake type excludes arbitrary transport adapters in this seam.
    """
    if type(authority) is not PrivateCommonAuthorityStore or type(transport) is not PrivateFakeCommonTransport:
        raise CommonAuthorityBlocked("PRIVATE_FAKE_ADAPTER_REQUIRED")
    with authority._transaction(readonly=True) as db:
        row = db.execute("SELECT * FROM common_write_attempt_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if not row:
            raise CommonAuthorityBlocked("COMMON_RESERVATION_MISSING")
        binding = json.loads(row["binding_json"])
        plan = db.execute("SELECT payload_json FROM release_plans WHERE plan_id=?", (binding["plan_id"],)).fetchone()
        mutation_bytes = canonical_bytes(json.loads(plan["payload_json"])["common_private_contract"]["mutation"])
        state = row["state"]
    request = {key: binding[key] for key in ("plan_id", "run_id", "attempt", "product_revision",
                                           "payload_digest", "coverage_generation", "policy_generation")}
    if state == "VERIFIED":
        authority.verify_private_readback(reservation_id, request, mutation_bytes)
        return {"private_stage": "FINAL_REVIEW_READY", "fake_write_performed": False,
                "execution_authority": False, "final_review_available": False}
    receipt = authority.consume_private(reservation_id, request, mutation_bytes)
    if not receipt["consumed"]:
        return {"private_stage": "RECONCILIATION_REQUIRED", "fake_write_performed": False,
                "execution_authority": False, "final_review_available": False}
    transport.write(binding["identity"], mutation_bytes)
    evidence = {"schema_version": "common-private-readback/v1", "evidence_kind": MODE,
                "identity": binding["identity"], "mutation": transport.read(binding["identity"]),
                "external_id": binding["identity"]["common_item_id"],
                "reservation_id": reservation_id, "outcome": "VERIFIED"}
    result = authority.reconcile_private(reservation_id, evidence)
    return {"private_stage": "FINAL_REVIEW_READY", "fake_write_performed": True,
            "reservation": result, "execution_authority": False,
            "final_review_available": False, "external_writes_performed": []}
