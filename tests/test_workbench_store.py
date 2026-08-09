from __future__ import annotations

import pytest

from shared_platform.workbench_store import WorkbenchStore


def test_task_lifecycle_dashboard_and_audit(tmp_path):
    store = WorkbenchStore(tmp_path / "workbench.db")
    task = store.create_task(
        {
            "title": "Review the weekly margin report",
            "project": "ecommerce",
            "priority": "P1",
            "due_date": "2026-07-27",
            "definition_of_done": ["decision recorded"],
            "is_top3": True,
        }
    )
    assert task["status"] == "todo"
    task = store.transition(task["task_id"], "in_progress")
    assert task["status"] == "in_progress"
    task = store.transition(task["task_id"], "waiting_approval")
    assert task["status"] == "waiting_approval"
    dashboard = store.dashboard(today="2026-07-28")
    assert dashboard["top3"][0]["task_id"] == task["task_id"]
    assert dashboard["overdue"][0]["task_id"] == task["task_id"]
    assert [event["event_type"] for event in store.events(task["task_id"])] == [
        "status_changed",
        "status_changed",
        "created",
    ]


def test_invalid_transition_and_feishu_source_are_safe(tmp_path):
    store = WorkbenchStore(tmp_path / "workbench.db")
    first = store.create_task({"title": "Message from Feishu", "status": "inbox", "source_key": "feishu:om_1"})
    same = store.create_task({"title": "duplicate", "status": "inbox", "source_key": "feishu:om_1"})
    assert same["task_id"] == first["task_id"]
    with pytest.raises(ValueError, match="invalid transition"):
        store.transition(first["task_id"], "done")
    assert store.transition(first["task_id"], "triage")["status"] == "triage"


def test_weekly_review_requires_monday_and_uses_current_tasks(tmp_path):
    store = WorkbenchStore(tmp_path / "workbench.db")
    task = store.create_task({"title": "Blocked task", "status": "blocked"})
    review = store.weekly_review("2026-07-27")
    assert review["content"]["blocked"][0]["task_id"] == task["task_id"]
    with pytest.raises(ValueError, match="Monday"):
        store.weekly_review("2026-07-28")


def test_deep_operations_assigns_five_store_groups_to_weekdays(tmp_path):
    store = WorkbenchStore(tmp_path / "workbench.db")
    monday = store.deep_ops("2026-07-27")
    assert monday["today"]["group_name"] == "墨西哥 + 英国"
    assert [item["group_name"] for item in monday["schedule"]] == [
        "墨西哥 + 英国",
        "虾皮",
        "Ozon",
        "TikTok SEA · Lively Hive",
        "TikTok SEA · HomeBloom",
    ]
    session = store.update_deep_ops_session("2026-07-27", {"status": "in_progress", "notes": "review pricing"})
    assert session["status"] == "in_progress"
    with pytest.raises(ValueError, match="Monday through Friday"):
        store.update_deep_ops_session("2026-07-26", {"status": "planned"})


def test_parked_work_does_not_count_as_active(tmp_path):
    store = WorkbenchStore(tmp_path / "workbench.db")
    task = store.create_task({"title": "Explore later", "status": "todo"})
    task = store.transition(task["task_id"], "in_progress")
    task = store.transition(task["task_id"], "parked")
    assert store.dashboard()["counts"]["parked"] == 1
    assert store.dashboard()["top3"] == []
