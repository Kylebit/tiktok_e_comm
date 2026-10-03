"""Private SQLite + real current Windows Token/ACL; no other login claimed."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import sqlite3
import threading

import pytest

from test_common_offer_authority_store import _authority, _plan
from shared_platform.common_offer_authority_store import CommonAuthorityBlocked, PrivateCommonAuthorityStore
from shared_platform.common_private_fake_adapter import PrivateFakeCommonTransport, execute_fake_common
from shared_platform.local_operator_session import (
    LocalOperatorSessions, PrivateOperatorGrant, create_owner_only_directory,
    current_windows_owner_sid, verify_owner_only,
)
from shared_platform.private_final_decision_store import (
    PrivateFinalDecisionStore, PrivateFinalFakeTransport, execute_private_final_successor,
)


def _ready(tmp_path, *, verified=True):
    authority = _authority(tmp_path)
    request, mutation = _plan(authority)
    authority.reserve(request, mutation, reservation_id="private-reservation")
    if verified:
        execute_fake_common(authority, "private-reservation", PrivateFakeCommonTransport())
    store = PrivateFinalDecisionStore(authority)
    store.migrate()
    store.sessions.initialize_owner()
    grant = store.sessions.grant_same_user()
    return store, grant, request


def _approved(tmp_path):
    store, grant, request = _ready(tmp_path)
    prepared = store.prepare(grant, reservation_id="private-reservation")
    approval = store.decide(grant, nonce=prepared["nonce"], review_digest=prepared["review_digest"])
    return store, grant, request, prepared, approval


def _counts(store):
    with store.authority._transaction(readonly=True) as db:
        return {t: db.execute("SELECT COUNT(*) FROM " + t).fetchone()[0] for t in
                ("private_final_decisions", "private_domain_final_approvals")}


def test_real_effective_windows_sid_and_owner_only_acl(tmp_path):
    sid = current_windows_owner_sid()
    assert sid.startswith("S-1-")
    folder = tmp_path / "owner"
    receipt = create_owner_only_directory(folder)
    assert receipt == {"owner_sid": sid, "owner_only_ace_count": 1, "protected_dacl": True}
    child = folder / "plain.txt"
    child.write_text("fixture no secret")
    assert verify_owner_only(child, sid, protected=False)["owner_only_ace_count"] == 1
    with pytest.raises(CommonAuthorityBlocked, match="ACL_INVALID"):
        verify_owner_only(folder, "S-1-5-18")


def test_no_common_readback_cannot_create_final_candidate(tmp_path):
    store, grant, _ = _ready(tmp_path, verified=False)
    with pytest.raises(CommonAuthorityBlocked, match="VERIFIED_COMMON_REQUIRED"):
        store.prepare(grant, reservation_id="private-reservation")
    assert all(value == 0 for value in _counts(store).values())


@pytest.mark.parametrize("kind", ["sid-string", "caller-approved-by", "wrong-capability", "wrong-csrf"])
def test_caller_identity_and_capability_substitutions_refused(tmp_path, kind):
    store, grant, _ = _ready(tmp_path)
    bad = {"sid-string": current_windows_owner_sid(), "caller-approved-by": {"approved_by": "Kyle"},
           "wrong-capability": replace(grant, capability="0" * 64),
           "wrong-csrf": replace(grant, csrf="0" * 64)}[kind]
    with pytest.raises(CommonAuthorityBlocked, match="LOCAL_CAPABILITY"):
        store.prepare(bad, reservation_id="private-reservation")
    assert all(value == 0 for value in _counts(store).values())


def test_same_user_bootstrap_and_one_approval_survive_restart(tmp_path):
    store, grant, _, prepared, approval = _approved(tmp_path)
    assert grant.capability not in repr(grant) and grant.csrf not in repr(grant)
    reopened = PrivateFinalDecisionStore(PrivateCommonAuthorityStore.open(store.authority.root))
    assert reopened.decide(grant, nonce=prepared["nonce"], review_digest=prepared["review_digest"]) == approval
    # Another program under the same real user can obtain its own capability.
    other_grant = reopened.sessions.grant_same_user()
    second = reopened.prepare(other_grant, reservation_id="private-reservation")
    assert reopened.decide(other_grant, nonce=second["nonce"], review_digest=second["review_digest"]) == approval
    assert set(_counts(store).values()) == {1}
    fake = PrivateFinalFakeTransport()
    done = execute_private_final_successor(reopened, other_grant, review_digest=prepared["review_digest"], transport=fake)
    assert done["private_stage"] == "COMPLETE" and done["execution_authority"] is False
    again = execute_private_final_successor(store, grant, review_digest=prepared["review_digest"], transport=fake)
    assert again["decision_id"] == approval["decision_id"] and len(fake.writes) == 1


def test_concurrent_single_click_has_one_durable_decision(tmp_path):
    store, grant, _ = _ready(tmp_path)
    prepared = store.prepare(grant, reservation_id="private-reservation")
    barrier = threading.Barrier(2)
    def click(_):
        barrier.wait()
        return store.decide(grant, nonce=prepared["nonce"], review_digest=prepared["review_digest"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(click, [1, 2]))
    assert receipts[0] == receipts[1] and set(_counts(store).values()) == {1}


def test_approval_and_nonce_consumption_rollback_in_same_database(tmp_path):
    store, grant, _ = _ready(tmp_path)
    prepared = store.prepare(grant, reservation_id="private-reservation")
    with sqlite3.connect(store.authority.store.path) as db:
        db.execute("CREATE TRIGGER fixture_nonce_failure BEFORE UPDATE ON private_final_nonces BEGIN SELECT RAISE(ABORT,'fixture abort'); END")
    with pytest.raises(sqlite3.IntegrityError, match="fixture abort"):
        store.decide(grant, nonce=prepared["nonce"], review_digest=prepared["review_digest"])
    assert set(_counts(store).values()) == {0}
    with sqlite3.connect(store.authority.store.path) as db:
        assert db.execute("SELECT decision_id FROM private_final_nonces").fetchone()[0] is None
        db.execute("DROP TRIGGER fixture_nonce_failure")
    assert store.decide(grant, nonce=prepared["nonce"], review_digest=prepared["review_digest"])["private_stage"] == "APPROVED"


def test_nonce_cannot_cross_session_or_candidate(tmp_path):
    store, grant, _ = _ready(tmp_path)
    prepared = store.prepare(grant, reservation_id="private-reservation")
    other = store.sessions.grant_same_user()
    with pytest.raises(CommonAuthorityBlocked, match="NONCE_BINDING"):
        store.decide(other, nonce=prepared["nonce"], review_digest=prepared["review_digest"])
    with pytest.raises(CommonAuthorityBlocked, match="NONCE_BINDING"):
        store.decide(grant, nonce=prepared["nonce"], review_digest="sha256:" + "0" * 64)
    assert set(_counts(store).values()) == {0}


def test_corrupt_common_readback_after_approval_blocks_successor(tmp_path):
    store, grant, request, prepared, _ = _approved(tmp_path)
    with sqlite3.connect(store.authority.store.path) as db:
        db.execute("UPDATE release_target_readbacks SET evidence_json='{}' WHERE run_id=?", (request["run_id"],))
    fake = PrivateFinalFakeTransport()
    with pytest.raises(CommonAuthorityBlocked, match="COMMON_READBACK"):
        execute_private_final_successor(store, grant, review_digest=prepared["review_digest"], transport=fake)
    assert fake.writes == [] and set(_counts(store).values()) == {1}


def test_fake_unknown_does_not_ask_again_or_repeat_write(tmp_path, monkeypatch):
    store, grant, _, prepared, approval = _approved(tmp_path)
    fake = PrivateFinalFakeTransport()
    original = PrivateFinalFakeTransport.write
    def lost_response(self, key, raw):
        original(self, key, raw)
        raise TimeoutError("synthetic lost response")
    with monkeypatch.context() as patch:
        patch.setattr(PrivateFinalFakeTransport, "write", lost_response)
        with pytest.raises(TimeoutError):
            execute_private_final_successor(store, grant, review_digest=prepared["review_digest"], transport=fake)
    reopened = PrivateFinalDecisionStore(PrivateCommonAuthorityStore.open(store.authority.root))
    assert reopened.approval(grant, review_digest=prepared["review_digest"]) == approval
    done = execute_private_final_successor(reopened, grant, review_digest=prepared["review_digest"], transport=fake)
    assert done["private_stage"] == "COMPLETE" and done["fake_write_performed"] is False and len(fake.writes) == 1


def test_fake_unknown_without_readback_preserves_approval(tmp_path, monkeypatch):
    store, grant, _, prepared, approval = _approved(tmp_path)
    def failed(*_):
        raise TimeoutError("synthetic no confirmed result")
    fake = PrivateFinalFakeTransport()
    with monkeypatch.context() as patch:
        patch.setattr(PrivateFinalFakeTransport, "write", failed)
        with pytest.raises(TimeoutError):
            execute_private_final_successor(store, grant, review_digest=prepared["review_digest"], transport=fake)
    result = execute_private_final_successor(store, grant, review_digest=prepared["review_digest"], transport=fake)
    assert result["private_stage"] == "RECONCILIATION_REQUIRED" and fake.writes == []
    assert store.approval(grant, review_digest=prepared["review_digest"]) == approval


def test_default_legacy_admission_still_unavailable(tmp_path):
    from shared_platform.final_review_server_admission import FinalApprovalAdmission, ApprovalBlocked, reject_http_approval
    with pytest.raises(ApprovalBlocked, match="TRUSTED_CHANNEL_REQUIRED"):
        reject_http_approval({"approved_by": "Kyle", "user_approved": True})
    assert FinalApprovalAdmission()._source is None


def test_private_migration_is_idempotent_and_does_not_drop_rows(tmp_path):
    store, grant, _, prepared, approval = _approved(tmp_path)
    assert store.migrate()["status"] == "ALREADY_MIGRATED_PRIVATE"
    assert store.approval(grant, review_digest=prepared["review_digest"]) == approval


def test_local_cli_bootstrap_keeps_capability_out_of_output(tmp_path, capsys):
    from scripts.private_local_final_review import main
    store, _, _ = _ready(tmp_path)
    prefix = ["--private-root", str(store.authority.root)]
    assert main([*prefix, "bootstrap", "--handoff-name", "grant.json"]) == 0
    client = store.sessions.grant_from_private_file(filename="grant.json")
    output = capsys.readouterr().out
    assert client.capability not in output and client.csrf not in output
    assert main([*prefix, "prepare", "--handoff-name", "grant.json", "--reservation-id", "private-reservation"]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert main([*prefix, "decide", "--handoff-name", "grant.json", "--nonce", prepared["nonce"], "--review-digest", prepared["review_digest"]]) == 0
    assert json.loads(capsys.readouterr().out)["private_stage"] == "APPROVED"
    with pytest.raises(CommonAuthorityBlocked, match="HANDOFF_NAME"):
        store.sessions.bootstrap_to_private_file(filename="../outside.json")


def test_non_windows_identity_fails_closed(monkeypatch):
    import shared_platform.local_operator_session as native
    with monkeypatch.context() as patch:
        patch.setattr(native.os, "name", "posix")
        with pytest.raises(CommonAuthorityBlocked, match="WINDOWS_IDENTITY_REQUIRED"):
            current_windows_owner_sid()


def test_changed_frozen_candidate_is_not_authorized_by_old_receipt(tmp_path):
    from shared_platform.common_offer_authority_store import canonical_bytes, digest
    store, grant, _, prepared, _ = _approved(tmp_path)
    with sqlite3.connect(store.authority.store.path) as db:
        trigger_sql = db.execute("SELECT sql FROM sqlite_master WHERE name='private_final_candidates_no_update'").fetchone()[0]
        db.execute("DROP TRIGGER private_final_candidates_no_update")
        old = json.loads(db.execute("SELECT frozen_json FROM private_final_candidates").fetchone()[0])
        old["critical_content_digest"] = "sha256:" + "1" * 64
        changed = canonical_bytes(old)
        db.execute("UPDATE private_final_candidates SET frozen_json=?,frozen_sha256=?", (changed.decode(), digest(changed)))
        db.execute(trigger_sql)
    fake = PrivateFinalFakeTransport()
    with pytest.raises(CommonAuthorityBlocked, match="CANDIDATE_BINDING"):
        execute_private_final_successor(store, grant, review_digest=prepared["review_digest"], transport=fake)
    assert fake.writes == [] and set(_counts(store).values()) == {1}


def test_grant_file_requires_actual_owner_acl_not_filename(tmp_path, monkeypatch):
    import shared_platform.local_operator_session as native
    store, _, _ = _ready(tmp_path)
    store.sessions.bootstrap_to_private_file(filename="grant.json")
    verify = native.verify_owner_only
    def invalid(path, sid, **kwargs):
        if str(path).endswith("grant.json"):
            raise CommonAuthorityBlocked("OWNER_ONLY_ACL_INVALID")
        return verify(path, sid, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(native, "verify_owner_only", invalid)
        with pytest.raises(CommonAuthorityBlocked, match="ACL_INVALID"):
            store.sessions.grant_from_private_file(filename="grant.json")


@pytest.mark.parametrize("filename", ["CON", "con.json", "NUL", "PRN.txt", "AUX", "COM1.txt", "LPT9.json", "grant.", "grant ", "OWNER.JSON"])
def test_reserved_handoff_names_refused_before_grant_or_write(tmp_path, monkeypatch, filename):
    store, _, _ = _ready(tmp_path)
    def no_grant():
        raise AssertionError("bad name must be rejected before issuing any secret")
    monkeypatch.setattr(store.sessions, "grant_same_user", no_grant)
    with pytest.raises(CommonAuthorityBlocked, match="HANDOFF_NAME_INVALID"):
        store.sessions.bootstrap_to_private_file(filename=filename)


def test_nonce_expiry_blocks_without_consuming_approval(tmp_path, monkeypatch):
    import shared_platform.private_final_decision_store as ledger
    store, grant, _ = _ready(tmp_path)
    prepared = store.prepare(grant, reservation_id="private-reservation")
    now = int(ledger.time.time())
    with monkeypatch.context() as patch:
        patch.setattr(ledger.time, "time", lambda: now + 301)
        with pytest.raises(CommonAuthorityBlocked, match="NONCE_EXPIRED"):
            store.decide(grant, nonce=prepared["nonce"], review_digest=prepared["review_digest"])
    assert set(_counts(store).values()) == {0}


def test_session_expiry_does_not_destroy_persisted_decision(tmp_path, monkeypatch):
    import shared_platform.local_operator_session as native
    store, grant, _, prepared, approval = _approved(tmp_path)
    now = int(native.time.time())
    with monkeypatch.context() as patch:
        patch.setattr(native.time, "time", lambda: now + 3601)
        with pytest.raises(CommonAuthorityBlocked, match="LOCAL_OPERATOR_SESSION_EXPIRED"):
            store.approval(grant, review_digest=prepared["review_digest"])
    new_grant = store.sessions.grant_same_user()
    assert store.approval(new_grant, review_digest=prepared["review_digest"]) == approval


def test_unapproved_candidate_cannot_execute_fake_successor(tmp_path):
    store, grant, _ = _ready(tmp_path)
    prepared = store.prepare(grant, reservation_id="private-reservation")
    fake = PrivateFinalFakeTransport()
    with pytest.raises(CommonAuthorityBlocked, match="DOMAIN_APPROVAL_INVALID"):
        execute_private_final_successor(store, grant, review_digest=prepared["review_digest"], transport=fake)
    assert fake.writes == [] and set(_counts(store).values()) == {0}


def test_completed_successor_requires_persisted_exact_readback(tmp_path):
    store, grant, _, prepared, _ = _approved(tmp_path)
    fake = PrivateFinalFakeTransport()
    execute_private_final_successor(store, grant, review_digest=prepared["review_digest"], transport=fake)
    with sqlite3.connect(store.authority.store.path) as db:
        db.execute("UPDATE private_final_successors SET readback_json='{}'")
    with pytest.raises(CommonAuthorityBlocked, match="SUCCESSOR_READBACK_INVALID"):
        execute_private_final_successor(store, grant, review_digest=prepared["review_digest"], transport=fake)
    assert len(fake.writes) == 1


def test_private_fake_transport_type_cannot_install_real_writer(tmp_path):
    store, grant, _, prepared, _ = _approved(tmp_path)
    class UntrustedAdapter(PrivateFinalFakeTransport):
        def write(self, *_):
            raise AssertionError("caller adapter must not be invoked")
    with pytest.raises(CommonAuthorityBlocked, match="PRIVATE_FINAL_FAKE_REQUIRED"):
        execute_private_final_successor(store, grant, review_digest=prepared["review_digest"], transport=UntrustedAdapter())
