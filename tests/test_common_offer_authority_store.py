"""Synthetic only; no production database, browser, service or provider."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import threading
import time

import pytest

from shared_platform.common_offer_authority_store import (
    MODE, CommonAuthorityBlocked, PrivateCommonAuthorityStore, canonical_bytes,
    digest, review_private_import,
)
from shared_platform.common_private_fake_adapter import PrivateFakeCommonTransport, execute_fake_common
from shared_platform.release_store import ReleaseStore

IDENTITY = {"tenant_id": "fixture-tenant", "account_id": "fixture-account",
            "offer_id": "fixture-offer", "common_item_id": "fixture-item"}


def _packet(authority, *, history_change=None, policy_change=None, coverage_generation=1, identity=None):
    now = authority.marker["created_at_epoch"]
    identity = identity or IDENTITY
    history = {"schema_version": "common-history-fixture/v1", "evidence_kind": MODE,
               "identity": identity, "lifecycle_start_epoch": now - 86400,
               "covered_through_epoch": authority.marker["created_at_epoch"],
               "pages": [{"page_number": 1, "cursor": "", "next_cursor": None, "events": []}],
               "declared_event_count": 0, "retention_complete": True, "gaps": []}
    policy = {"schema_version": "common-policy-fixture/v1", "evidence_kind": MODE,
              "identity": identity, "generation": 1, "parent_generation": 0,
              "operation_classes": ["COMMON_EDIT", "COMMON_CLAIM", "COMMON_FETCH"],
              "maximum_attempts": 1,
              "history_count_rules": {"CONFIRMED": 1, "UNKNOWN": 1, "PROVEN_NOT_DISPATCHED": 0},
              "reservation_count_rules": {"RESERVED": 1, "UNKNOWN": 1, "VERIFIED": 1, "PROVEN_NOT_DISPATCHED": 1},
              "effective_at_epoch": now - 60, "expires_at_epoch": now + 3600,
              "revoked": False, "approval_evidence_ref": "approval.json"}
    if history_change:
        history_change(history)
    if policy_change:
        policy_change(policy)
    policy_raw = canonical_bytes(policy)
    approval = {"schema_version": "common-approval-fixture/v1", "evidence_kind": MODE,
                "identity": identity, "policy_sha256": digest(policy_raw), "approved": True}
    attachments = {"history.json": canonical_bytes(history), "policy.json": policy_raw,
                   "approval.json": canonical_bytes(approval)}
    packet = {"schema_version": "common-authority-import/v1", "evidence_kind": MODE,
              "identity": identity, "coverage_ref": "history.json", "coverage_generation": coverage_generation,
              "policy_ref": "policy.json", "attachment_manifest": {k: digest(v) for k, v in attachments.items()}}
    return canonical_bytes(packet), attachments


def _review(authority, **kwargs):
    raw, attachments = _packet(authority, **kwargs)
    return review_private_import(raw, attachments, expected_packet_digest=digest(raw), review_ref="fixture-review:unit-test")


def _authority(tmp_path, *, migrate=True, imported=True):
    authority = PrivateCommonAuthorityStore.create((tmp_path / "private-common").resolve())
    if migrate:
        authority.migrate()
    if imported:
        authority.import_reviewed(_review(authority))
    return authority


def _plan(authority, *, suffix="1", operation="COMMON_EDIT", identity=None):
    identity = identity or IDENTITY
    payload = {"plan_id": "fixture-plan:" + suffix, "product_id": IDENTITY["offer_id"],
               "seller_sku": "10" + suffix.zfill(2), "product_package_id": "fixture-product:" + suffix,
               "content_package_id": "fixture-content:" + suffix, "product_revision": 7,
               "targets": ["miaoshou:COMMON"],
               "common_private_contract": {"schema_version": "common-private-plan/v1", "identity": identity,
                                           "operation_class": operation, "mutation": {"title": "合成商品", "stock": 5}}}
    # Existing legacy test APIs create the fixture run only. The new private
    # authority API never creates user approval or a production run itself.
    plan = authority.store.create_plan(payload)
    authority.store.approve_plan(plan["plan_id"], approved_by="Kyle", user_approved=True,
                                 confirmation_token=plan["confirmation_token"])
    run = authority.store.start_run(plan["plan_id"], run_id="fixture-run:" + suffix)
    request = {"plan_id": plan["plan_id"], "run_id": run["run_id"], "attempt": 1,
               "product_revision": 7, "payload_digest": plan["payload_digest"],
               "coverage_generation": 1, "policy_generation": 1}
    return request, canonical_bytes(payload["common_private_contract"]["mutation"])


def _evidence(reservation="reservation:1", outcome="VERIFIED"):
    return {"schema_version": "common-private-readback/v1", "evidence_kind": MODE,
            "identity": IDENTITY, "mutation": {"title": "合成商品", "stock": 5},
            "external_id": IDENTITY["common_item_id"], "reservation_id": reservation, "outcome": outcome}


def test_missing_schema_never_auto_migrates_and_preserves_old_release_rows(tmp_path):
    authority = _authority(tmp_path, migrate=False, imported=False)
    request, _ = _plan(authority)
    before = authority.store.get_run(request["run_id"])
    with pytest.raises(CommonAuthorityBlocked, match="NOT_EXPLICITLY_MIGRATED"):
        authority.inspect()
    authority.migrate()
    assert authority.store.get_run(request["run_id"]) == before
    assert authority.migrate()["status"] == "ALREADY_MIGRATED_PRIVATE"
    assert authority.store.database_health()["foreign_key_violations"] == []


def test_private_root_cannot_adopt_existing_directory_or_ordinary_database(tmp_path):
    existing = tmp_path / "exists"
    existing.mkdir()
    (existing / "release.db").write_bytes(b"unique-user-artifact")
    with pytest.raises(FileExistsError):
        PrivateCommonAuthorityStore.create(existing.resolve())
    assert (existing / "release.db").read_bytes() == b"unique-user-artifact"
    with pytest.raises(OSError):
        PrivateCommonAuthorityStore.open(existing.resolve())
    with pytest.raises(CommonAuthorityBlocked, match="PATH_MISMATCH"):
        authority = _authority(tmp_path)
        ReleaseStore(tmp_path / "ordinary.db").private_common_authority(private_root=authority.root)


@pytest.mark.parametrize("case", ["packet_digest", "attachment_bytes", "approval", "count", "duplicate_event", "official", "http_cap"])
def test_reviewed_actual_attachments_required_not_caller_assertions(tmp_path, case):
    authority = _authority(tmp_path, imported=False)
    def change(history):
        if case == "count":
            history["declared_event_count"] = 99
        if case == "duplicate_event":
            event = {"event_id": "same", "operation_class": "COMMON_EDIT", "outcome": "CONFIRMED", "legacy_attempt": None}
            history["pages"][0]["events"] = [event, event]
            history["declared_event_count"] = 2
    raw, attachments = _packet(authority, history_change=change)
    packet = json.loads(raw)
    expected = digest(raw)
    if case == "packet_digest":
        expected = "0" * 64
    elif case == "attachment_bytes":
        attachments["history.json"] += b" "
    elif case == "approval":
        approval = json.loads(attachments["approval.json"])
        approval["policy_sha256"] = "0" * 64
        attachments["approval.json"] = canonical_bytes(approval)
        packet["attachment_manifest"]["approval.json"] = digest(attachments["approval.json"])
        raw = canonical_bytes(packet)
        expected = digest(raw)
    elif case == "official":
        packet["evidence_kind"] = "OFFICIAL_COMPLETE"
        raw = canonical_bytes(packet)
        expected = digest(raw)
    elif case == "http_cap":
        packet["cap"] = 1000
        packet["history_complete"] = True
        raw = canonical_bytes(packet)
        expected = digest(raw)
    with pytest.raises(CommonAuthorityBlocked):
        authority.import_reviewed(review_private_import(raw, attachments, expected_packet_digest=expected, review_ref="review:fixture"))
    assert authority.inspect()["imports"] == 0


@pytest.mark.parametrize("case", ["terminal", "retention", "gaps", "unknown"])
def test_incomplete_history_stays_waiting_before_reservation(tmp_path, case):
    authority = _authority(tmp_path, imported=False)
    def change(history):
        if case == "terminal":
            history["pages"][-1]["next_cursor"] = "missing-next-page"
        elif case == "retention":
            history["retention_complete"] = False
        elif case == "gaps":
            history["gaps"] = ["old account activity missing"]
        else:
            history["pages"][0]["events"] = [{"event_id": "unknown", "operation_class": "COMMON_EDIT", "outcome": "UNKNOWN", "legacy_attempt": None}]
            history["declared_event_count"] = 1
    authority.import_reviewed(_review(authority, history_change=change))
    request, mutation = _plan(authority)
    before = authority.store.get_run(request["run_id"])
    with pytest.raises(CommonAuthorityBlocked, match="HISTORY_INCOMPLETE_OR_UNKNOWN"):
        authority.reserve(request, mutation, reservation_id="reservation:1")
    assert authority.store.get_run(request["run_id"]) == before
    assert authority.inspect()["reservations"] == []


def test_incomplete_source_can_be_completed_same_task_without_manual_approval(tmp_path):
    authority = _authority(tmp_path, imported=False)
    first = _review(authority, history_change=lambda h: h.update(gaps=["missing page"]))
    authority.import_reviewed(first)
    request, mutation = _plan(authority)
    with pytest.raises(CommonAuthorityBlocked):
        authority.reserve(request, mutation, reservation_id="reservation:1")
    # Preserve the actual original policy bytes; the source generation alone
    # advances. Import attachment review never creates another user approval.
    raw, attachments = _packet(authority, coverage_generation=2)
    attachments["policy.json"] = dict(first.attachments)["policy.json"]
    attachments["approval.json"] = dict(first.attachments)["approval.json"]
    packet = json.loads(raw)
    packet["attachment_manifest"] = {k: digest(v) for k, v in attachments.items()}
    raw = canonical_bytes(packet)
    authority.import_reviewed(review_private_import(raw, attachments, expected_packet_digest=digest(raw), review_ref="review:complete-source"))
    request["coverage_generation"] = 2
    assert authority.reserve(request, mutation, reservation_id="reservation:1")["created"] is True
    assert authority.store.get_run(request["run_id"])["targets"][0]["attempts"] == 1


@pytest.mark.parametrize("case", ["mutation", "plan", "attempt", "revision", "digest", "policy_generation", "coverage_generation", "extra_cap", "operation"])
def test_plan_bytes_operation_and_exact_caller_binding_before_claim(tmp_path, case):
    authority = _authority(tmp_path)
    request, mutation = _plan(authority, operation="COMMON_CLAIM" if case == "operation" else "COMMON_EDIT")
    if case == "operation":
        authority.import_reviewed(_review(authority, policy_change=lambda p: p.update(generation=2, parent_generation=1, operation_classes=["COMMON_EDIT"])))
        request["policy_generation"] = 2
    elif case == "mutation":
        mutation = canonical_bytes({"title": "changed", "stock": 5})
    elif case == "extra_cap":
        request["cap"] = 1000
    elif case == "attempt":
        request["attempt"] = 2
    elif case == "revision":
        request["product_revision"] = 8
    elif case == "digest":
        request["payload_digest"] = "0" * 64
    elif case == "plan":
        request["plan_id"] = "different-plan"
    else:
        request[case] = 2
    with pytest.raises(CommonAuthorityBlocked):
        authority.reserve(request, mutation, reservation_id="reservation:1")
    assert authority.inspect()["reservations"] == []


def test_actual_stored_bytes_checked_even_if_old_digest_unchanged(tmp_path):
    authority = _authority(tmp_path)
    request, mutation = _plan(authority)
    with sqlite3.connect(authority.store.path) as db:
        db.execute("DROP TRIGGER trg_release_plan_immutable")
        raw = db.execute("SELECT payload_json FROM release_plans WHERE plan_id=?", (request["plan_id"],)).fetchone()[0]
        payload = json.loads(raw)
        payload["common_private_contract"]["mutation"]["stock"] = 99
        db.execute("UPDATE release_plans SET payload_json=? WHERE plan_id=?", (canonical_bytes(payload).decode(), request["plan_id"]))
    with pytest.raises(CommonAuthorityBlocked, match="STORED_PAYLOAD_BYTES_INVALID"):
        authority.reserve(request, mutation, reservation_id="reservation:1")
    assert authority.inspect()["reservations"] == []


def test_two_plans_race_for_one_slot_and_replay_is_not_a_dispatch(tmp_path):
    authority = _authority(tmp_path)
    inputs = [_plan(authority, suffix=str(i)) for i in (1, 2)]
    barrier = threading.Barrier(2)
    def claim(index):
        private = PrivateCommonAuthorityStore.open(authority.root)
        barrier.wait(timeout=10)
        try:
            return private.reserve(*inputs[index], reservation_id="reservation:" + str(index + 1))
        except CommonAuthorityBlocked as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, (0, 1)))
    assert sum(type(v) is dict for v in outcomes) == 1
    assert len(authority.inspect()["reservations"]) == 1
    winner = next(i for i, v in enumerate(outcomes) if type(v) is dict)
    repeated = authority.reserve(*inputs[winner], reservation_id="reservation:" + str(winner + 1))
    assert repeated["created"] is False and repeated["dispatch_allowed"] is False
    with pytest.raises(CommonAuthorityBlocked, match="REPLAY_CONFLICT"):
        authority.reserve(*inputs[winner], reservation_id="reservation:another")


def test_failed_claim_rolls_back_reservation_target_and_run_together(tmp_path):
    authority = _authority(tmp_path)
    request, mutation = _plan(authority)
    before = authority.store.get_run(request["run_id"])
    with sqlite3.connect(authority.store.path) as db:
        db.execute("""CREATE TRIGGER fixture_fail_claim BEFORE UPDATE OF status ON release_target_runs
            WHEN NEW.status='RUNNING' BEGIN SELECT RAISE(ABORT,'synthetic claim failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic claim failure"):
        authority.reserve(request, mutation, reservation_id="reservation:1")
    assert authority.inspect()["reservations"] == []
    assert authority.store.get_run(request["run_id"]) == before


def test_unknown_after_consumption_survives_restart_and_never_frees_capacity(tmp_path):
    authority = _authority(tmp_path)
    request, mutation = _plan(authority)
    reserved = authority.reserve(request, mutation, reservation_id="reservation:1")
    first = authority.consume_private("reservation:1", request, mutation)
    assert first["consumed"] is True and first["state"] == "UNKNOWN"
    reopened = PrivateCommonAuthorityStore.open(authority.root)
    second = reopened.consume_private("reservation:1", request, mutation)
    assert second["consumed"] is False and second["state"] == "UNKNOWN"
    assert second["authority_digest"] == reserved["authority_digest"]
    other_request, other_mutation = _plan(authority, suffix="2")
    with pytest.raises(CommonAuthorityBlocked, match="RECONCILIATION_REQUIRED"):
        reopened.reserve(other_request, other_mutation, reservation_id="reservation:2")
    assert len(reopened.inspect()["reservations"]) == 1


@pytest.mark.parametrize("outcome", ["VERIFIED", "PROVEN_NOT_DISPATCHED"])
def test_readback_recovery_is_atomic_stable_and_never_refunds(outcome, tmp_path):
    authority = _authority(tmp_path)
    request, mutation = _plan(authority)
    reserved = authority.reserve(request, mutation, reservation_id="reservation:1")
    authority.consume_private("reservation:1", request, mutation)
    evidence = _evidence(outcome=outcome)
    result = authority.reconcile_private("reservation:1", evidence)
    assert result["state"] == outcome
    assert result["authority_digest"] == reserved["authority_digest"]
    assert authority.reconcile_private("reservation:1", evidence)["state"] == outcome
    other_request, other_mutation = _plan(authority, suffix="2")
    with pytest.raises(CommonAuthorityBlocked, match="BUDGET_EXHAUSTED"):
        authority.reserve(other_request, other_mutation, reservation_id="reservation:2")
    target = authority.store.get_run(request["run_id"])["targets"][0]
    assert target["status"] == ("SUCCEEDED" if outcome == "VERIFIED" else "FAILED")
    assert target["attempts"] == 1


def test_changed_policy_generation_blocks_consume_before_fake_write(tmp_path):
    authority = _authority(tmp_path)
    request, mutation = _plan(authority)
    authority.reserve(request, mutation, reservation_id="reservation:1")
    authority.import_reviewed(_review(authority, policy_change=lambda p: p.update(generation=2, parent_generation=1)))
    fake = PrivateFakeCommonTransport()
    with pytest.raises(CommonAuthorityBlocked, match="GENERATION_CHANGED"):
        execute_fake_common(authority, "reservation:1", fake)
    assert fake.writes == []
    assert authority.inspect()["reservations"][0]["state"] == "RESERVED"


@pytest.mark.parametrize("change", ["mutation", "identity", "external_id", "source"])
def test_wrong_readback_preserves_unknown_without_success(tmp_path, change):
    authority = _authority(tmp_path)
    request, mutation = _plan(authority)
    authority.reserve(request, mutation, reservation_id="reservation:1")
    authority.consume_private("reservation:1", request, mutation)
    evidence = copy.deepcopy(_evidence())
    if change == "mutation":
        evidence["mutation"]["stock"] = 99
    elif change == "identity":
        evidence["identity"]["account_id"] = "different"
    elif change == "external_id":
        evidence["external_id"] = "different"
    else:
        evidence["evidence_kind"] = "OFFICIAL_COMPLETE"
    with pytest.raises(CommonAuthorityBlocked):
        authority.reconcile_private("reservation:1", evidence)
    assert authority.inspect()["reservations"][0]["state"] == "UNKNOWN"
    assert authority.store.get_run(request["run_id"])["targets"][0]["status"] == "RUNNING"


def test_legacy_unreserved_attempt_requires_exact_history_reconciliation(tmp_path):
    authority = _authority(tmp_path)
    previous, _ = _plan(authority)
    authority.store.begin_target(previous["run_id"], "miaoshou:COMMON")
    authority.store.record_target_failure(previous["run_id"], "miaoshou:COMMON", error="old unknown attempt")
    request, mutation = _plan(authority, suffix="2")
    with pytest.raises(CommonAuthorityBlocked, match="UNRESERVED_LEGACY_ATTEMPT_UNKNOWN"):
        authority.reserve(request, mutation, reservation_id="reservation:2")
    assert authority.inspect()["reservations"] == []


def test_private_roundtrip_consumes_domain_bytes_once_with_no_real_authority(tmp_path):
    authority = _authority(tmp_path)
    request, mutation = _plan(authority)
    reserved = authority.reserve(request, mutation, reservation_id="reservation:1")
    fake = PrivateFakeCommonTransport()
    done = execute_fake_common(authority, "reservation:1", fake)
    assert done["private_stage"] == "FINAL_REVIEW_READY"
    assert fake.writes == [mutation]
    assert done["execution_authority"] is False and done["final_review_available"] is False
    assert done["reservation"]["authority_digest"] == reserved["authority_digest"]
    assert authority.store.get_run(request["run_id"])["targets"][0]["readback"]["evidence"]["mutation"] == json.loads(mutation)
    repeated = execute_fake_common(PrivateCommonAuthorityStore.open(authority.root), "reservation:1", fake)
    assert repeated["fake_write_performed"] is False and fake.writes == [mutation]
    from shared_platform.publication_common_write_admission import inspect_common_write_admission
    assert inspect_common_write_admission(authority.store.get_plan(request["plan_id"]))["execution_authority"] is False


def test_count_rules_require_integers_not_bool_aliases(tmp_path):
    authority = _authority(tmp_path, imported=False)
    raw, attachments = _packet(authority, policy_change=lambda p: p["reservation_count_rules"].update(UNKNOWN=True))
    with pytest.raises(CommonAuthorityBlocked, match="COUNT_RULES"):
        review_private_import(raw, attachments, expected_packet_digest=digest(raw), review_ref="review:bool-cap")


def test_existing_migration_version_does_not_hide_missing_table(tmp_path):
    authority = _authority(tmp_path)
    with sqlite3.connect(authority.store.path) as db:
        db.execute("DROP TABLE common_write_attempt_events")
    with pytest.raises(CommonAuthorityBlocked, match="SCHEMA"):
        authority.migrate()


def test_verified_fake_repeat_requires_intact_persisted_readback(tmp_path):
    authority = _authority(tmp_path)
    request, mutation = _plan(authority)
    authority.reserve(request, mutation, reservation_id="reservation:1")
    fake = PrivateFakeCommonTransport()
    execute_fake_common(authority, "reservation:1", fake)
    with sqlite3.connect(authority.store.path) as db:
        db.execute("UPDATE release_target_readbacks SET evidence_json='{}' WHERE run_id=?", (request["run_id"],))
    with pytest.raises(CommonAuthorityBlocked, match="READBACK"):
        execute_fake_common(authority, "reservation:1", fake)
    assert fake.writes == [mutation]


def test_cli_validate_apply_inspect_use_only_explicit_private_root(tmp_path, capsys):
    from scripts.import_common_authority import main
    authority = _authority(tmp_path, imported=False)
    raw, attachments = _packet(authority)
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    packet_path = packet_dir / "packet.json"
    packet_path.write_bytes(raw)
    for name, content in attachments.items():
        (packet_dir / name).write_bytes(content)
    args = ["--packet", str(packet_path), "--expected-packet-sha256", digest(raw), "--review-ref", "review:cli-fixture"]
    assert main(["validate", *args]) == 0
    assert authority.inspect()["imports"] == 0
    assert main(["apply-private", "--private-root", str(authority.root), *args]) == 0
    assert authority.inspect()["imports"] == 1
    assert main(["inspect", "--private-root", str(authority.root)]) == 0
    receipts = [json.loads(v) for v in capsys.readouterr().out.splitlines()]
    assert all(v["execution_authority"] is False for v in receipts)


def test_cli_oversize_rejected_before_open_and_attachment_traversal_denied(tmp_path, monkeypatch):
    from scripts.import_common_authority import _read_bounded, main
    from shared_platform.common_offer_authority_store import MAX_ATTACHMENT_BYTES
    giant = tmp_path / "huge.json"
    with giant.open("wb") as file:
        file.seek(MAX_ATTACHMENT_BYTES)
        file.write(b"x")
    def no_read(self, *args, **kwargs):
        raise AssertionError("oversize must be rejected before opening")
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", no_read)
        with pytest.raises(CommonAuthorityBlocked, match="SIZE_INVALID"):
            _read_bounded(giant.resolve())
    authority = _authority(tmp_path, imported=False)
    raw, attachments = _packet(authority)
    packet = json.loads(raw)
    packet["attachment_manifest"]["../outside.json"] = "0" * 64
    path = tmp_path / "traversal.json"
    raw = canonical_bytes(packet)
    path.write_bytes(raw)
    assert main(["validate", "--packet", str(path), "--expected-packet-sha256", digest(raw), "--review-ref", "review:traversal"]) == 2


def test_recreated_item_cannot_reset_offer_budget_or_consume_old_identity(tmp_path):
    authority = _authority(tmp_path)
    request, mutation = _plan(authority)
    authority.reserve(request, mutation, reservation_id="reservation:1")
    recreated = {**IDENTITY, "common_item_id": "fixture-item-recreated"}
    reviewed = _review(authority, identity=recreated, coverage_generation=2,
                       policy_change=lambda p: p.update(generation=2, parent_generation=1))
    with pytest.raises(CommonAuthorityBlocked, match="ITEM_IDENTITY_CHANGE"):
        authority.import_reviewed(reviewed)
    newer_request, newer_mutation = _plan(authority, suffix="2", identity=recreated)
    with pytest.raises(CommonAuthorityBlocked, match="PHYSICAL_IDENTITY"):
        authority.reserve(newer_request, newer_mutation, reservation_id="reservation:2")
    assert len(authority.inspect()["reservations"]) == 1
