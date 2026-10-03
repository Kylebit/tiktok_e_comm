from __future__ import annotations

import json
import sqlite3
from threading import Event, Thread

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


def test_recovery_request_identity_is_persisted_and_tamper_checked(tmp_path):
    store = _store(tmp_path)
    created = store.create_run(
        run_id="recovery-run", offer_id="3838616043", revision=42,
        plan_id="omnichannel:" + "a" * 64,
        snapshot_digest="sha256:" + "b" * 64,
        platform_scope=("SHOPEE",), target_count=3,
        execution_identity={"skill_digest": "1" * 64, "git_commit": "2" * 40,
                            "code_digest": "3" * 64},
        request_identity={"kind": "SHOPEE_RECOVERY",
                          "authority_digest": "sha256:" + "4" * 64},
    )
    assert store.get_run_by_id(run_id=created.run_id)["request_identity"] == {
        "kind": "SHOPEE_RECOVERY", "authority_digest": "sha256:" + "4" * 64}
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE product_publication_runs SET request_identity_json = ? WHERE run_id = ?",
            (json.dumps({"kind": "SHOPEE_RECOVERY",
                         "authority_digest": "sha256:" + "5" * 64}), created.run_id),
        )
        conn.commit()
    with pytest.raises(ProductPublicationRunIntegrityError, match="identity digest"):
        store.get_run_by_id(run_id=created.run_id)


def test_reportless_continuation_request_identity_is_exact_and_idempotent(tmp_path):
    store = _store(tmp_path)
    identity = {
        "kind": "SHOPEE_RECOVERY_CONTINUATION",
        "authority_digest": "sha256:" + "4" * 64,
        "predecessor_run_id": "product-center-shopee-4bc697d923e8cbb569e8533a3c63e932",
        "source_receipt_digest": "sha256:" + "5" * 64,
        "preflight_digest": "sha256:" + "6" * 64,
        "evidence_relocation_digest": "sha256:" + "7" * 64,
    }
    arguments = dict(
        run_id="continuation-run", offer_id="3956742887", revision=5,
        plan_id="omnichannel:" + "a" * 64,
        snapshot_digest="sha256:" + "b" * 64,
        platform_scope=("SHOPEE",), target_count=3,
        execution_identity={"skill_digest": "1" * 64, "git_commit": "2" * 40,
                            "code_digest": "3" * 64},
        request_identity=identity,
        retry_of_run_id=identity["predecessor_run_id"],
    )
    first = store.create_run(**arguments)
    second = store.create_run(**arguments)
    assert first.created is True and second.created is False
    assert store.get_run_by_id(run_id=first.run_id)["request_identity"] == identity

    for field in ("authority_digest", "predecessor_run_id",
                  "source_receipt_digest", "preflight_digest"):
        changed = dict(identity)
        changed[field] = (
            "different-predecessor" if field == "predecessor_run_id"
            else "sha256:" + "7" * 64
        )
        with pytest.raises(ValueError, match="different facts"):
            store.create_run(**{**arguments, "request_identity": changed})


def test_legacy_database_without_request_identity_migrates_as_standard(tmp_path):
    from shared_platform.product_publication_runs import _digest, _event_payload, _identity_payload

    store = _store(tmp_path)
    run = {
        "run_id": "legacy-standard", "report_id": "publication-report:legacy-standard",
        "offer_id": "3838616043", "revision": 42,
        "plan_id": "omnichannel:" + "a" * 64,
        "snapshot_digest": "sha256:" + "b" * 64,
        "platform_scope": ["TIKTOK"], "target_count": 6,
        "execution_identity": {"skill_digest": "1" * 64, "git_commit": "2" * 40,
                               "code_digest": "3" * 64},
    }
    legacy_identity = _identity_payload(
        run_id=run["run_id"], report_id=run["report_id"], offer_id=run["offer_id"],
        revision=run["revision"], plan_id=run["plan_id"],
        snapshot_digest=run["snapshot_digest"], platform_scope=tuple(run["platform_scope"]),
        target_count=run["target_count"], execution_identity=run["execution_identity"],
    )
    legacy_digest = _digest(legacy_identity)
    created_at = "2026-09-13T00:00:00+00:00"
    event_payload = _event_payload(
        run_id=run["run_id"], sequence=1, state="QUEUED",
        final_report_id=None, failure_code=None, created_at=created_at,
        run_identity_digest=legacy_digest,
    )
    with sqlite3.connect(store.path) as conn:
        conn.executescript("""
            CREATE TABLE product_publication_runs (
                run_id TEXT PRIMARY KEY, report_id TEXT NOT NULL UNIQUE, offer_id TEXT NOT NULL,
                revision INTEGER NOT NULL, plan_id TEXT NOT NULL,
                snapshot_schema_version TEXT NOT NULL, snapshot_digest TEXT NOT NULL,
                platform_scope_json TEXT NOT NULL, target_count INTEGER NOT NULL,
                execution_identity_json TEXT, state TEXT NOT NULL, final_report_id TEXT,
                failure_code TEXT, identity_digest TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE product_publication_run_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL, state TEXT NOT NULL, final_report_id TEXT,
                failure_code TEXT, created_at TEXT NOT NULL, run_identity_digest TEXT,
                event_digest TEXT NOT NULL, UNIQUE(run_id, sequence)
            );
        """)
        conn.execute(
            "INSERT INTO product_publication_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run["run_id"], run["report_id"], run["offer_id"], run["revision"],
             run["plan_id"], "approved-publication-snapshot/v4", run["snapshot_digest"],
             json.dumps(run["platform_scope"]), run["target_count"],
             json.dumps(run["execution_identity"]), "QUEUED", None, None,
             legacy_digest, created_at, created_at),
        )
        conn.execute(
            "INSERT INTO product_publication_run_events "
            "(run_id,sequence,state,final_report_id,failure_code,created_at,run_identity_digest,event_digest) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (run["run_id"], 1, "QUEUED", None, None, created_at,
             legacy_digest, _digest(event_payload)),
        )
        conn.commit()
        assert "request_identity_json" not in {
            row[1] for row in conn.execute("PRAGMA table_info(product_publication_runs)")}

    replayed = store.create_run(
        run_id="new-standard-id", offer_id=run["offer_id"], revision=run["revision"],
        plan_id=run["plan_id"], snapshot_digest=run["snapshot_digest"],
        platform_scope=run["platform_scope"], target_count=run["target_count"],
        execution_identity=run["execution_identity"], approved_request_guard=lambda _prior: None,
    )
    assert replayed.created is False and replayed.run_id == run["run_id"]
    migrated = store.get_run_by_id(run_id=run["run_id"])
    assert migrated["request_identity"] == {"kind": "STANDARD", "authority_digest": None}


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


def test_plan_read_keeps_each_runs_events_and_newest_first(tmp_path):
    store = _store(tmp_path)
    first = _create(store, run_id="first-run")
    store.mark_failed(run_id=first.run_id, failure_code="SYNTHETIC_FAILURE")
    second = _create(store, run_id="second-run")

    runs = store.list_runs_for_plan(
        offer_id="3838616043", plan_id="omnichannel:" + "a" * 64
    )
    assert [(run["run_id"], run["state"], run["event_count"]) for run in runs] == [
        (second.run_id, "QUEUED", 1),
        (first.run_id, "FAILED", 2),
    ]


@pytest.mark.parametrize("read_by", ["run_id", "report_id", "plan"])
@pytest.mark.parametrize("journal_mode", ["delete", "wal"])
def test_read_uses_one_snapshot_when_worker_commits_between_cursor_and_events(
    tmp_path, monkeypatch, read_by, journal_mode
):
    store = _store(tmp_path)
    created = _create(store)
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(f"PRAGMA journal_mode={journal_mode}").fetchone()[0] == journal_mode

    selected = Event()
    resume = Event()
    reader_result = {}
    original = store._row_to_run

    def pause_before_events(conn, row, **kwargs):
        selected.set()
        if not resume.wait(5):
            raise TimeoutError("worker did not commit")
        return original(conn, row, **kwargs)

    monkeypatch.setattr(store, "_row_to_run", pause_before_events)

    def read():
        try:
            if read_by == "run_id":
                runs = [store.get_run_by_id(run_id=created.run_id)]
            elif read_by == "report_id":
                runs = [store.get_run(report_id=created.report_id, offer_id="3838616043")]
            else:
                runs = store.list_runs_for_plan(
                    offer_id="3838616043", plan_id="omnichannel:" + "a" * 64
                )
            reader_result["runs"] = runs
        except Exception as error:
            reader_result["error"] = error

    reader = Thread(target=read, daemon=True)
    reader.start()
    try:
        assert selected.wait(5)
        # A separate store uses a separate connection, as the worker does.
        writer = _store(tmp_path)
        assert writer.mark_failed(
            run_id=created.run_id, failure_code="SYNTHETIC_FAILURE"
        )["state"] == "FAILED"
    finally:
        resume.set()
        reader.join(5)

    assert not reader.is_alive()
    assert "error" not in reader_result, reader_result.get("error")
    assert len(reader_result["runs"]) == 1
    assert reader_result["runs"][0]["state"] == "QUEUED"
    assert reader_result["runs"][0]["event_count"] == 1
    assert _store(tmp_path).get_run_by_id(run_id=created.run_id)["state"] == "FAILED"

