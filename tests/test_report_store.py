import json

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


def test_store_persists_one_run_and_one_local_inbox_item_idempotently(tmp_path):
    path = tmp_path / "orbit.db"
    store = ReportRunStore(path)

    first = store.store_report_run(_report())
    repeated_payload = _report(generated_at="2026-07-27T01:01:00+08:00")
    repeated = store.store_report_run(repeated_payload)

    assert first.report_created is True
    assert first.inbox_created is True
    assert repeated.report_created is False
    assert repeated.inbox_created is False
    assert len(store.list_report_runs()) == 1
    inbox = store.list_inbox(status="unread")
    assert len(inbox) == 1
    assert inbox[0]["report_run_id"] == "weekly-profit-abc"
    assert inbox[0]["title"] == "周度利润简报已生成 · 2026-07-20 – 2026-07-26"
    assert inbox[0]["payload"]["negative_profit_count"] == 1
    json.dumps(inbox, ensure_ascii=False)


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
    item = reopened.list_inbox()[0]
    assert item["severity"] == "warning"
    assert item["payload"]["quality_issue_count"] == 1


def test_reading_missing_store_is_side_effect_free(tmp_path):
    path = tmp_path / "missing" / "orbit.db"
    store = ReportRunStore(path)

    assert store.list_report_runs() == []
    assert store.list_inbox() == []
    assert not path.exists()


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
