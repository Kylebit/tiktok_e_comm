from __future__ import annotations

import sqlite3

import pytest

from shared_platform.product_publication_runs import (
    ProductPublicationRunIntegrityError,
    ProductPublicationRunStore,
    public_publication_run_status,
)


def _store(tmp_path):
    return ProductPublicationRunStore(tmp_path / "orbit_platform.db")


def _create(store, *, run_id="run-async-001"):
    return store.create_run(
        run_id=run_id,
        offer_id="3838616043",
        revision=42,
        plan_id="omnichannel:" + "a" * 64,
        snapshot_digest="sha256:" + "b" * 64,
        platform_scope=("TIKTOK",),
        target_count=6,
        execution_identity={
            "skill_digest": "1" * 64,
            "git_commit": "2" * 40,
            "code_digest": "3" * 64,
        },
    )


def test_execution_identity_is_part_of_replay_and_tamper_checked(tmp_path):
    store = _store(tmp_path)
    created = _create(store)
    run = store.get_run_by_id(run_id=created.run_id)
    assert run["execution_identity"] == {
        "skill_digest": "1" * 64,
        "git_commit": "2" * 40,
        "code_digest": "3" * 64,
    }

    with pytest.raises(ValueError, match="different facts"):
        store.create_run(
            run_id=created.run_id,
            offer_id="3838616043",
            revision=42,
            plan_id="omnichannel:" + "a" * 64,
            snapshot_digest="sha256:" + "b" * 64,
            platform_scope=("TIKTOK",),
            target_count=6,
            execution_identity={
                "skill_digest": "9" * 64,
                "git_commit": "2" * 40,
                "code_digest": "3" * 64,
            },
        )

    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE product_publication_runs SET execution_identity_json = ? WHERE run_id = ?",
            ('{"skill_digest":"' + "9" * 64 + '","git_commit":"' + "2" * 40 + '","code_digest":"' + "3" * 64 + '"}', created.run_id),
        )
        conn.commit()
    with pytest.raises(ProductPublicationRunIntegrityError, match="identity digest"):
        store.get_run_by_id(run_id=created.run_id)


def test_queued_and_running_survive_reopen_as_processing_without_success(tmp_path):
    store = _store(tmp_path)
    created = _create(store)

    queued = _store(tmp_path).get_run(
        report_id=created.report_id,
        offer_id="3838616043",
    )
    queued_view = public_publication_run_status(queued)
    assert queued["state"] == "QUEUED"
    assert queued["event_count"] == 1
    assert queued_view["status"] == "PROCESSING"
    assert queued_view["summary"]["evidence"]["dispatch_attempted"] is False

    store.mark_running(run_id=created.run_id)
    after_restart = _store(tmp_path).get_run(
        report_id=created.report_id,
        offer_id="3838616043",
    )
    running_view = public_publication_run_status(after_restart)
    assert after_restart["state"] == "RUNNING"
    assert after_restart["event_count"] == 2
    assert running_view["status"] == "PROCESSING"
    assert running_view["summary"]["evidence"]["dispatch_attempted"] is None
    assert running_view["summary"]["evidence"]["external_write_count"] is None


def test_final_report_identity_is_bound_once_and_completed_cursor_is_not_a_fake_report(
    tmp_path,
):
    store = _store(tmp_path)
    created = _create(store)
    store.mark_running(run_id=created.run_id)
    completed = store.mark_completed(
        run_id=created.run_id,
        final_report_id=created.report_id,
    )

    assert completed["state"] == "COMPLETED"
    assert completed["event_count"] == 3
    with pytest.raises(ProductPublicationRunIntegrityError, match="immutable final report"):
        public_publication_run_status(completed)
    with pytest.raises(ValueError, match="cannot transition"):
        store.mark_failed(run_id=created.run_id, failure_code="LATE_FAILURE")


def test_failed_run_has_redacted_terminal_projection_and_events_detect_tampering(tmp_path):
    store = _store(tmp_path)
    created = _create(store)
    store.mark_failed(run_id=created.run_id, failure_code="WORKER_LAUNCH_FAILED")

    failed = store.get_run_by_id(run_id=created.run_id)
    public = public_publication_run_status(failed)
    assert public["status"] == "FAILED"
    assert public["summary"]["requires_human_action"] is True
    assert "WORKER_LAUNCH_FAILED" not in str(public)

    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE product_publication_run_events SET state = 'COMPLETED' WHERE run_id = ? AND sequence = 2",
            (created.run_id,),
        )
        conn.commit()
    with pytest.raises(ProductPublicationRunIntegrityError, match="event digest"):
        store.get_run_by_id(run_id=created.run_id)

