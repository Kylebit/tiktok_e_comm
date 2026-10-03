"""The inert final-review decision must never authorize the old release path."""

import sqlite3
import threading

import pytest

from shared_platform.release_store import ReleaseAuthorizationError, ReleaseStore
from tests.test_final_review_decision_cas import _contract, _payload


def _counts(path):
    with sqlite3.connect(path) as connection:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "release_final_review_decisions",
                "release_final_review_stage_authorizations",
                "release_approvals",
                "release_runs",
                "release_target_runs",
            )
        )


def _approve(store, plan):
    return store.approve_plan(
        plan["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=plan["confirmation_token"],
    )


def _cas(store, payload):
    return store.cas_final_review_decision(
        _contract(payload), approved_by="Kyle", user_approved=True
    )


def test_cas_blocks_direct_legacy_approval_and_run_without_new_rows(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    payload = _payload()
    plan = store.create_plan(payload)
    _cas(store, payload)
    assert _counts(store.path) == (1, 1, 0, 0, 0)

    with pytest.raises(ReleaseAuthorizationError, match="final review"):
        _approve(store, plan)
    with pytest.raises(ReleaseAuthorizationError, match="final review"):
        store.start_run(plan["plan_id"])

    assert _counts(store.path) == (1, 1, 0, 0, 0)
    assert store.get_plan(plan["plan_id"])["status"] == "PENDING_APPROVAL"


def test_cas_blocks_begin_target_even_if_legacy_rows_were_inserted(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    payload = _payload()
    plan = store.create_plan(payload)
    _cas(store, payload)
    # Simulate an older writer or a restored inconsistent database. This is a
    # local synthetic fixture, never a provider call or a supported transition.
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE release_plans SET status = 'APPROVED' WHERE plan_id = ?",
            (plan["plan_id"],),
        )
        connection.execute(
            """INSERT INTO release_approvals
            (approval_id, plan_id, payload_digest, confirmation_token,
             approved_by, user_approved, status, approved_at)
            VALUES ('injected-approval', ?, ?, ?, 'Kyle', 1, 'APPROVED', 'synthetic')""",
            (plan["plan_id"], plan["payload_digest"], plan["confirmation_token"]),
        )
        connection.execute(
            """INSERT INTO release_runs
            (run_id, plan_id, approval_id, status, created_at, updated_at)
            VALUES ('injected-run', ?, 'injected-approval', 'PENDING',
                    'synthetic', 'synthetic')""",
            (plan["plan_id"],),
        )
        connection.execute(
            """INSERT INTO release_target_runs
            (run_id, target_label, idempotency_key, status, attempts,
             created_at, updated_at)
            VALUES ('injected-run', 'miaoshou:COMMON', 'synthetic', 'PENDING',
                    0, 'synthetic', 'synthetic')"""
        )
    before = _counts(store.path)
    with pytest.raises(ReleaseAuthorizationError, match="final review"):
        store.start_run(plan["plan_id"])
    with pytest.raises(ReleaseAuthorizationError, match="final review"):
        store.begin_target("injected-run", "miaoshou:COMMON")
    assert _counts(store.path) == before
    assert store.get_run("injected-run")["targets"][0]["status"] == "PENDING"


def test_legacy_without_decision_remains_idempotent_and_unrelated_offer_runs(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    decided_payload = _payload(offer_id="3956742887")
    store.create_plan(decided_payload)
    _cas(store, decided_payload)

    legacy = store.create_plan(_payload(offer_id="3956742888", seller_sku="0953"))
    assert _approve(store, legacy)["created"] is True
    assert _approve(store, legacy)["created"] is False
    first_run = store.start_run(legacy["plan_id"])
    repeated_run = store.start_run(legacy["plan_id"])
    assert first_run["run_id"] == repeated_run["run_id"]
    target = store.begin_target(first_run["run_id"], "miaoshou:COMMON")
    assert target["status"] == "RUNNING"
    assert _counts(store.path) == (1, 1, 1, 1, 1)


def test_prior_legacy_approval_prevents_later_cas(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    payload = _payload()
    plan = store.create_plan(payload)
    _approve(store, plan)
    first_run = store.start_run(plan["plan_id"])

    with pytest.raises(ReleaseAuthorizationError, match="unapproved pending plan"):
        _cas(store, payload)

    assert _counts(store.path) == (0, 0, 1, 1, 1)
    assert store.start_run(plan["plan_id"])["run_id"] == first_run["run_id"]


def test_cas_and_legacy_approval_interleave_without_dual_authority(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    payload = _payload()
    plan = store.create_plan(payload)
    gate = threading.Barrier(2)
    outcomes = []
    lock = threading.Lock()

    def attempt(kind):
        gate.wait()
        worker = ReleaseStore(store.path)
        try:
            result = _cas(worker, payload) if kind == "cas" else _approve(worker, plan)
            outcome = (kind, "accepted", result)
        except ReleaseAuthorizationError:
            outcome = (kind, "rejected", None)
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=attempt, args=(kind,)) for kind in ("cas", "old")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=35)
        assert not thread.is_alive()

    assert sorted(status for _, status, _ in outcomes) == ["accepted", "rejected"]
    decision_count, stage_count, approval_count, run_count, _ = _counts(store.path)
    assert decision_count == stage_count
    assert decision_count + approval_count == 1
    assert run_count == 0
