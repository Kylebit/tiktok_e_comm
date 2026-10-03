import json
import sqlite3

import pytest

from shared_platform.report_store import ReportRunStore


def _report(**overrides):
    payload = {
        "run_id": "weekly-profit-abc",
        "idempotency_key": "weekly_profit_digest:abc",
        "calculation_kind": "weekly_profit_digest",
        "status": "ready",
        "period": {
            "start": "2026-07-20T00:00:00+08:00",
            "end": "2026-07-26T23:59:59.999999+08:00",
            "timezone": "Asia/Shanghai",
        },
        "quality_issues": [],
        "negative_profit_skus": [{"sku_id": "0021"}],
        "generated_at": "2026-07-27T01:00:00+08:00",
    }
    payload.update(overrides)
    return payload


def test_store_persists_one_run_without_notification_table_idempotently(tmp_path):
    path = tmp_path / "orbit.db"
    store = ReportRunStore(path)

    first = store.store_report_run(_report())
    repeated_payload = _report(generated_at="2026-07-27T01:01:00+08:00")
    repeated = store.store_report_run(repeated_payload)

    assert first.report_created is True
    assert repeated.report_created is False
    assert len(store.list_report_runs()) == 1
    with sqlite3.connect(path) as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='orbit_inbox'").fetchall()


def test_store_survives_reopen_and_flags_review_runs(tmp_path):
    path = tmp_path / "orbit.db"
    ReportRunStore(path).store_report_run(
        _report(
            run_id="weekly-profit-review",
            idempotency_key="weekly_profit_digest:review",
            status="needs_review",
            quality_issues=[{"code": "missing_cost"}],
        )
    )

    reopened = ReportRunStore(path)
    assert reopened.list_report_runs()[0]["status"] == "needs_review"
    assert reopened.list_report_runs()[0]["payload"]["quality_issues"] == [{"code": "missing_cost"}]


def test_reading_missing_store_is_side_effect_free(tmp_path):
    path = tmp_path / "missing" / "orbit.db"
    store = ReportRunStore(path)

    assert store.list_report_runs() == []
    assert not path.exists()


@pytest.mark.parametrize("has_report_table", [False, True])
def test_list_report_runs_closes_readonly_connection(
    tmp_path, monkeypatch, has_report_table
):
    path = tmp_path / "orbit.db"
    if has_report_table:
        ReportRunStore(path).store_report_run(_report())
    else:
        connection = sqlite3.connect(path)
        connection.close()
    store = ReportRunStore(path)
    opened = []
    original_connect = store._connect_readonly

    def tracked_connect():
        connection = original_connect()
        opened.append(connection)
        return connection

    monkeypatch.setattr(store, "_connect_readonly", tracked_connect)
    try:
        assert len(store.list_report_runs()) == int(has_report_table)
        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            opened[0].execute("SELECT 1")
    finally:
        for connection in opened:
            connection.close()


def test_new_report_preserves_existing_historical_inbox_rows(tmp_path):
    path = tmp_path / "historical.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE orbit_inbox (inbox_id TEXT PRIMARY KEY, payload_json TEXT)")
        conn.execute("INSERT INTO orbit_inbox VALUES ('historical', '{\"fact\":1}')")
    ReportRunStore(path).store_report_run(_report())
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM orbit_inbox").fetchall() == [('historical', '{"fact":1}')]


@pytest.mark.parametrize(
    "payload,error",
    [
        ({}, "missing report fields"),
        (_report(status="pending"), "unsupported report status"),
        (_report(period={}), "report period requires start and end"),
    ],
)
def test_store_rejects_incomplete_or_unsupported_reports(tmp_path, payload, error):
    with pytest.raises(ValueError, match=error):
        ReportRunStore(tmp_path / "orbit.db").store_report_run(payload)


def test_idempotency_key_cannot_point_to_another_run(tmp_path):
    store = ReportRunStore(tmp_path / "orbit.db")
    store.store_report_run(_report())

    with pytest.raises(ValueError, match="different run_id"):
        store.store_report_run(_report(run_id="weekly-profit-other"))


def test_run_id_cannot_point_to_another_idempotency_key(tmp_path):
    store = ReportRunStore(tmp_path / "orbit.db")
    store.store_report_run(_report())

    with pytest.raises(ValueError, match="different idempotency_key"):
        store.store_report_run(_report(idempotency_key="weekly_profit_digest:other"))
