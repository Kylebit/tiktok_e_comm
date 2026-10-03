"""The inert decision must bind the sole current COMMON plan in one Store transaction."""

import re
import sqlite3

import pytest

from shared_platform import release_store as rs
from shared_platform.release_store import ReleaseAuthorizationError, ReleaseStore
from tests.test_final_review_decision_cas import _contract, _payload, _record


def _decision_count(store):
    with sqlite3.connect(store.path) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM release_final_review_decisions"
        ).fetchone()[0]


def _store_with_permissive_status(tmp_path, table):
    """Model an older Store table left intact by CREATE TABLE IF NOT EXISTS."""
    db = tmp_path / "release.db"
    schema = rs._SCHEMA
    marker = f"CREATE TABLE IF NOT EXISTS {table}"
    before, table_and_after = schema.split(marker, 1)
    table_and_after, replaced = re.subn(
        r"status TEXT NOT NULL CHECK\s*\(\s*status IN \([^)]+\)\s*\)",
        "status TEXT",
        table_and_after,
        count=1,
    )
    assert replaced == 1
    with sqlite3.connect(db) as connection:
        connection.executescript(before + marker + table_and_after)
    return ReleaseStore(db)


@pytest.mark.parametrize("status", [None, "", "UNKNOWN"])
def test_ambiguous_competing_plan_status_blocks_cas(tmp_path, status):
    store = _store_with_permissive_status(tmp_path, "release_plans")
    candidate = _payload(suffix="candidate")
    other = _payload(suffix="other", seller_sku="0953")
    store.create_plan(candidate)
    store.create_plan(other)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE release_plans SET status = ? WHERE plan_id = ?",
            (status, other["plan_id"]),
        )

    with pytest.raises(ReleaseAuthorizationError, match="active current plan"):
        _record(store, _contract(candidate))
    assert _decision_count(store) == 0


@pytest.mark.parametrize("status", [None, "", "UNKNOWN"])
def test_ambiguous_prior_run_status_blocks_cas(tmp_path, status):
    store = _store_with_permissive_status(tmp_path, "release_runs")
    old = _payload(suffix="old")
    current = _payload(suffix="current", seller_sku="0953")
    old_plan = store.create_plan(old)
    store.create_plan(current)
    store.approve_plan(
        old_plan["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=old_plan["confirmation_token"],
    )
    store.start_run(old_plan["plan_id"])
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE release_plans SET status = 'SUPERSEDED' WHERE plan_id = ?",
            (old["plan_id"],),
        )
        connection.execute(
            "UPDATE release_approvals SET status = 'SUPERSEDED' WHERE plan_id = ?",
            (old["plan_id"],),
        )
        connection.execute(
            "UPDATE release_runs SET status = ? WHERE plan_id = ?",
            (status, old["plan_id"]),
        )

    with pytest.raises(ReleaseAuthorizationError, match="COMMON_RECONCILIATION_REQUIRED"):
        _record(store, _contract(current))
    assert _decision_count(store) == 0


def test_stale_pending_plan_cannot_record_after_another_plan_becomes_current(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    stale = _payload(suffix="a-stale")
    current = _payload(suffix="z-current", seller_sku="0953")
    store.create_plan(stale)
    store.create_plan(current)
    assert store.active_plan_for_product(stale["product_id"])["plan_id"] == current["plan_id"]

    with pytest.raises(ReleaseAuthorizationError, match="active current plan"):
        _record(store, _contract(stale))
    assert _decision_count(store) == 0

    # Two live plans for one Offer are ambiguous even when the named one sorts last.
    with pytest.raises(ReleaseAuthorizationError, match="active current plan"):
        _record(store, _contract(current))
    assert _decision_count(store) == 0


def test_current_successor_of_superseded_pending_plan_can_record(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    old = _payload(suffix="old")
    current = _payload(suffix="current")
    store.create_plan(old)
    store.create_plan(current, supersedes_plan_id=old["plan_id"])

    receipt = _record(store, _contract(current))
    assert receipt["created"] is True
    assert receipt["decision"]["common_plan_id"] == current["plan_id"]
    assert _decision_count(store) == 1


@pytest.mark.parametrize("start_unknown_run", [False, True])
def test_active_old_legacy_approval_or_unknown_run_blocks_cas(tmp_path, start_unknown_run):
    store = ReleaseStore(tmp_path / "release.db")
    old = _payload(suffix="a-old")
    current = _payload(suffix="z-current", seller_sku="0953")
    old_plan = store.create_plan(old)
    store.create_plan(current)
    store.approve_plan(
        old_plan["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=old_plan["confirmation_token"],
    )
    if start_unknown_run:
        run = store.start_run(old_plan["plan_id"])
        store.begin_target(run["run_id"], "miaoshou:COMMON")

    with pytest.raises(ReleaseAuthorizationError):
        _record(store, _contract(current))
    assert _decision_count(store) == 0


def test_superseded_old_plan_with_unknown_common_attempt_blocks_cas(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    old = _payload(suffix="a-old")
    current = _payload(suffix="z-current", seller_sku="0953")
    old_plan = store.create_plan(old)
    store.create_plan(current)
    store.approve_plan(
        old_plan["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=old_plan["confirmation_token"],
    )
    run = store.start_run(old_plan["plan_id"])
    store.begin_target(run["run_id"], "miaoshou:COMMON")
    # Simulate a restored older DB where plan supersession did not reconcile
    # the already-started provider attempt. This is only a local Store fixture.
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE release_plans SET status = 'SUPERSEDED' WHERE plan_id = ?",
            (old["plan_id"],),
        )
        connection.execute(
            "UPDATE release_approvals SET status = 'SUPERSEDED' WHERE plan_id = ?",
            (old["plan_id"],),
        )
        connection.execute(
            "UPDATE release_runs SET status = 'SUPERSEDED' WHERE plan_id = ?",
            (old["plan_id"],),
        )

    with pytest.raises(ReleaseAuthorizationError, match="COMMON_RECONCILIATION_REQUIRED"):
        _record(store, _contract(current))
    assert _decision_count(store) == 0


def test_exact_replay_survives_later_successor_without_a_second_decision(tmp_path):
    store = ReleaseStore(tmp_path / "release.db")
    original = _payload(suffix="original")
    successor = _payload(suffix="successor")
    store.create_plan(original)
    first = _record(store, _contract(original))
    store.create_plan(successor, supersedes_plan_id=original["plan_id"])

    replay = _record(store, _contract(original))
    assert replay["created"] is False
    assert replay["decision"] == first["decision"]
    assert _decision_count(store) == 1


def test_exact_replay_survives_ambiguous_competing_plan_status(tmp_path):
    store = _store_with_permissive_status(tmp_path, "release_plans")
    original = _payload(suffix="original")
    other = _payload(suffix="other", seller_sku="0953")
    store.create_plan(original)
    first = _record(store, _contract(original))
    store.create_plan(other)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE release_plans SET status = NULL WHERE plan_id = ?",
            (other["plan_id"],),
        )

    replay = _record(store, _contract(original))
    assert replay["created"] is False
    assert replay["execution_authority"] is False
    assert replay["decision"] == first["decision"]
    assert _decision_count(store) == 1
