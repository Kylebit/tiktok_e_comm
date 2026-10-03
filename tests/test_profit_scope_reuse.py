from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from shared_platform.workbench_engine import WorkbenchEngine


def test_profit_exact_scope_reuses_one_task_under_parallel_create(tmp_path):
    engine = WorkbenchEngine(tmp_path / "tasks.db", {"code_version": "profit-hub-test"})

    def create(key, shops):
        return engine.create({
            "template": "profit",
            "source_key": key,
            "title": "月度利润",
            "scope": {"month": "2026-08", "shops": shops},
            "reuse_existing_scope": True,
        })["task_id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda args: create(*args), [("first", ["shop-b", "shop-a"]), ("second", ["shop-a", "shop-b"])]))
    assert first == second
    assert create("third", ["shop-c"]) != first
    assert create("fourth", ["shop-a", "shop-b"]) == first
    assert len([task for task in engine.dashboard()["tasks"] if task["template"] == "profit"]) == 2


def test_profit_scope_reuse_does_not_merge_different_platform_scope_or_cancelled(tmp_path):
    engine = WorkbenchEngine(tmp_path / "tasks.db", {"code_version": "profit-hub-test"})
    first = engine.create({"template": "profit", "source_key": "one", "scope": {"month": "2026-08", "shops": [], "platforms": ["tiktok"]}})["task_id"]
    different = engine.create({"template": "profit", "source_key": "two", "scope": {"month": "2026-08", "shops": [], "platforms": ["shopee"]}, "reuse_existing_scope": True})["task_id"]
    assert different != first
    assert engine.create({"template": "profit", "source_key": "three", "scope": {"month": "2026-08", "shops": [], "platforms": ["tiktok"]}, "reuse_existing_scope": True})["task_id"] == first
    engine.user_action(first, "cancel")
    resumed = engine.create({"template": "profit", "source_key": "four", "scope": {"month": "2026-08", "shops": [], "platforms": ["tiktok"]}, "reuse_existing_scope": True})["task_id"]
    assert resumed != first


def test_profit_reuses_immutable_request_after_worker_resolves_scope(tmp_path):
    engine = WorkbenchEngine(tmp_path / "tasks.db", {"code_version": "profit-hub-test"})
    engine.register_executor("worker", ["profit"], engine.release)
    request = {"template": "profit", "title": "八月利润", "source_key": "one",
               "scope": {"month": "2026-08", "shops": ["shop-A"]},
               "reuse_existing_scope": True}
    first = engine.create(request)["task_id"]
    token = engine.claim(first, "worker")["lease_token"]
    engine.bind_scope(first, token, {"month": "2026-08", "shops": ["shop-A"],
                                     "platforms": ["tiktok"], "sites": ["MY"]})
    assert engine.get(first)["request_scope"] == {"month": "2026-08", "shops": ["shop-A"], "skus": []}
    assert engine.get(first)["scope"]["sites"] == ["MY"]
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE workbench_profit_requests SET request_scope_json='{}' WHERE task_id=?", (first,))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM workbench_profit_requests WHERE task_id=?", (first,))
    with ThreadPoolExecutor(max_workers=2) as pool:
        resumed = list(pool.map(lambda key: engine.create({**request, "source_key": key})["task_id"],
                                ["two", "three"]))
    assert resumed == [first, first]
    assert engine.create({**request, "source_key": "different-shop",
                          "scope": {"month": "2026-08", "shops": ["shop-B"]}})["task_id"] != first
    assert engine.create({**request, "source_key": "explicit-platform",
                          "scope": {"month": "2026-08", "shops": ["shop-A"],
                                    "platforms": ["tiktok"]}})["task_id"] != first


def test_legacy_resolved_scope_without_request_identity_fails_closed(tmp_path):
    db = tmp_path / "tasks.db"
    engine = WorkbenchEngine(db, {"code_version": "profit-hub-test"})
    engine.register_executor("worker", ["profit"], engine.release)
    first = engine.create({"template": "profit", "source_key": "old",
        "scope": {"month": "2026-08", "shops": ["shop-A"]}})["task_id"]
    token = engine.claim(first, "worker")["lease_token"]
    engine.bind_scope(first, token, {"month": "2026-08", "shops": ["shop-A"],
                                     "platforms": ["tiktok"], "sites": ["MY"]})
    # Simulate a database created before the immutable request table existed.
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE workbench_profit_requests")
    assert engine.get(first)["request_scope"] is None
    with pytest.raises(ValueError, match="original request scope unavailable"):
        engine.create({"template": "profit", "source_key": "same-after-upgrade",
            "scope": {"month": "2026-08", "shops": ["shop-A"]},
            "reuse_existing_scope": True})
    with pytest.raises(ValueError, match="original request scope unavailable"):
        engine.create({"template": "profit", "source_key": "disjoint-after-upgrade",
            "scope": {"month": "2026-08", "shops": ["shop-B"]},
            "reuse_existing_scope": True})
    # The old resolver could have recorded a formal identity instead of an
    # original user alias, so even apparently disjoint strings are ambiguous.
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE workbench_execution SET scope_json=? WHERE task_id=?",
                     ('{"month":"2026-08","shops":["formal-shop"],"skus":[]}', first))
    with pytest.raises(ValueError, match="original request scope unavailable"):
        engine.create({"template": "profit", "source_key": "alias-after-upgrade",
            "scope": {"month": "2026-08", "shops": ["alias-shop"]},
            "reuse_existing_scope": True})
    assert engine.create({"template": "profit", "source_key": "different-month",
        "scope": {"month": "2026-09", "shops": ["shop-B"]},
        "reuse_existing_scope": True})["task_id"] != first
