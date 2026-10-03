"""Synthetic pre-adapter Store reads must stay on one isolated database copy."""

from copy import deepcopy
from hashlib import sha256
import gc
import json
from pathlib import Path
import sqlite3
import stat

import pytest

from modules.products import server
from shared_platform import publication_final_review_pending as pending_adapter
from shared_platform import release_control
from shared_platform import release_store
from shared_platform import report_store
from test_b4b_common_stage import context
from test_release_control import _release_fixture


def inventory(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        item = path.lstat()
        entry = {"mode": item.st_mode, "inode": item.st_ino, "nlink": item.st_nlink,
                 "size": item.st_size, "mtime_ns": item.st_mtime_ns,
                 "attributes": getattr(item, "st_file_attributes", 0)}
        if stat.S_ISREG(item.st_mode) and not entry["attributes"] & 0x400:
            entry["sha256"] = sha256(path.read_bytes()).hexdigest()
        result[str(path.relative_to(directory))] = entry
    return result


def ready(tmp_path, monkeypatch):
    documents, dashboard, store, request = context(tmp_path, monkeypatch)
    catalog = tmp_path / "data" / "shop.db"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(catalog)
    try:
        connection.execute("CREATE TABLE products(product_id TEXT, seller_sku TEXT)")
        connection.execute("CREATE TABLE shopee_products(item_id TEXT, seller_sku TEXT)")
        connection.commit()
    finally:
        connection.close()
    status, view = server._preview_r3_common_stage(request)
    assert status == 200, view
    plan = store.create_plan(view["common"]["plan"]["payload"])
    gc.collect()
    connection = sqlite3.connect(store.path)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    gc.collect()
    assert "release.db-wal" not in inventory(tmp_path)
    assert "release.db-shm" not in inventory(tmp_path)
    return documents, dashboard, store, request, plan


def deny_source_store(monkeypatch, source_path):
    original_connect = sqlite3.connect

    def audited_connect(database, *args, **kwargs):
        if source_path.as_posix().lower() in str(database).replace("\\", "/").lower():
            raise AssertionError("pre-adapter path opened source SQLite")
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", audited_connect)
    monkeypatch.setattr(release_store, "default_release_store", lambda: pytest.fail(
        "pre-adapter path requested default source Store"))


def test_actual_dashboard_uses_injected_report_store(tmp_path, monkeypatch):
    root, database = _release_fixture(tmp_path)
    path = root / "data" / "missing-orbit.db"
    injected = release_store.ReleaseStore(path)
    calls = []
    original = injected.active_plan_for_product

    def tracked(product_id):
        calls.append(product_id)
        return original(product_id)

    monkeypatch.setattr(injected, "active_plan_for_product", tracked)
    monkeypatch.setattr(release_store, "ReleaseStore", lambda path: pytest.fail(
        "dashboard constructed a second Store"))
    result = release_control.build_release_dashboard(
        root=root, database_path=database, report_store_path=path,
        report_store=injected, offer_id="3828811808", seller_sku="0946",
        frozen_pricing={
            "schema_version": "synthetic/v1", "selected_store_prices": [],
            "workbench_exchange_rates": {}, "shopee_exchange_rates": {},
            "ozon_exchange_rates": {}, "target_pricing": {},
        },
    )
    assert result["product"]["offer_id"] == "3828811808"
    assert len(calls) >= 1 and set(calls) == {"3828811808"}


def test_actual_dashboard_reads_isolated_source_image(tmp_path, monkeypatch):
    root, database = _release_fixture(tmp_path)
    source_path = root / "data" / "orbit_platform.db"
    connection = sqlite3.connect(source_path)
    try:
        connection.execute("CREATE TABLE synthetic_marker(value TEXT)")
        connection.execute("INSERT INTO synthetic_marker VALUES ('present')")
        connection.commit()
    finally:
        connection.close()
    connection = sqlite3.connect(source_path)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    finally:
        connection.close()
    assert "orbit_platform.db-wal" not in inventory(source_path.parent)
    source = release_store.ReleaseStore(source_path)
    before = inventory(source_path.parent)
    deny_source_store(monkeypatch, source_path)
    weekly_connections = []
    original_report_connect = report_store.ReportRunStore._connect_readonly

    def tracked_report_connect(self):
        connection = original_report_connect(self)
        weekly_connections.append(connection)
        return connection

    monkeypatch.setattr(report_store.ReportRunStore, "_connect_readonly",
                        tracked_report_connect)
    with pending_adapter.pending_store_snapshot(source) as snapshot:
        copied = snapshot.path
        result = release_control.build_release_dashboard(
            root=root, database_path=database, report_store_path=copied,
            report_store=snapshot, offer_id="3828811808", seller_sku="0946",
            frozen_pricing={
                "schema_version": "synthetic/v1", "selected_store_prices": [],
                "workbench_exchange_rates": {}, "shopee_exchange_rates": {},
                "ozon_exchange_rates": {}, "target_pricing": {},
            },
        )
        assert result["product"]["offer_id"] == "3828811808"
        assert copied != source_path and copied.exists()
        assert len(weekly_connections) == 1
    assert not copied.parent.exists()
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        weekly_connections[0].execute("SELECT 1")
    assert inventory(source_path.parent) == before
    print("PRE_ADAPTER_DASHBOARD " + json.dumps({
        "before": before, "after": inventory(source_path.parent),
        "copy_removed": not copied.parent.exists(),
    }, sort_keys=True))


def test_full_dashboard_common_pending_uses_one_copy_and_source_is_unchanged(
    tmp_path, monkeypatch
):
    documents, dashboard, store, request, plan = ready(tmp_path, monkeypatch)
    before = inventory(tmp_path)
    paths = []

    def dashboard_on_snapshot(**kwargs):
        path = Path(kwargs["report_store_path"])
        snapshot = kwargs["report_store"]
        assert path == snapshot.path and path != store.path
        assert snapshot.get_plan(plan["plan_id"])["status"] == "PENDING_APPROVAL"
        assert snapshot.active_plan_for_product(request["offer_id"])["plan_id"] == plan["plan_id"]
        paths.append(path)
        return deepcopy(dashboard)

    monkeypatch.setattr(release_control, "build_release_dashboard", dashboard_on_snapshot)
    deny_source_store(monkeypatch, store.path)
    preview = server._build_pending_final_review_preview_readonly(
        offer_id=request["offer_id"], source_store=store, root=tmp_path
    )
    assert preview.as_dict()["status"] == "UNAPPROVED_PREVIEW"
    assert preview.as_dict()["common"]["plan_id"] == plan["plan_id"]
    assert len(paths) == 1 and not paths[0].parent.exists()
    assert inventory(tmp_path) == before
    print("PRE_ADAPTER_HAPPY " + json.dumps({
        "before": before, "after": inventory(tmp_path),
        "copy_removed": not paths[0].parent.exists(),
    }, sort_keys=True))


def test_active_wal_is_rejected_before_dashboard_or_source_mutation(tmp_path, monkeypatch):
    _, dashboard, store, request, _ = ready(tmp_path, monkeypatch)
    connection = sqlite3.connect(store.path)
    try:
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE latest_fact(value TEXT NOT NULL)")
        connection.execute("INSERT INTO latest_fact VALUES ('committed')")
        connection.commit()
        before = inventory(tmp_path)
        assert "release.db-wal" in before
        deny_source_store(monkeypatch, store.path)
        calls = []
        monkeypatch.setattr(release_control, "build_release_dashboard",
                            lambda **kwargs: calls.append(kwargs) or deepcopy(dashboard))
        with pytest.raises(ValueError, match="FINAL_REVIEW_PENDING_SOURCE_UNSTABLE"):
            server._build_pending_final_review_preview_readonly(
                offer_id=request["offer_id"], source_store=store, root=tmp_path
            )
        assert calls == []
        assert inventory(tmp_path) == before
        print("PRE_ADAPTER_ACTIVE_WAL " + json.dumps({
            "before": before, "after": inventory(tmp_path), "dashboard_calls": len(calls),
        }, sort_keys=True))
    finally:
        connection.close()


def test_dashboard_exception_closes_and_removes_snapshot(tmp_path, monkeypatch):
    _, _, store, request, _ = ready(tmp_path, monkeypatch)
    before = inventory(tmp_path)
    copies = []
    original = pending_adapter.tempfile.TemporaryDirectory

    def track(*args, **kwargs):
        copy = original(*args, **kwargs)
        copies.append(Path(copy.name))
        return copy

    monkeypatch.setattr(pending_adapter.tempfile, "TemporaryDirectory", track)

    def failing_dashboard(**kwargs):
        snapshot = kwargs["report_store"]
        assert snapshot.active_plan_for_product(request["offer_id"])
        raise RuntimeError("synthetic dashboard failure")

    monkeypatch.setattr(release_control, "build_release_dashboard", failing_dashboard)
    deny_source_store(monkeypatch, store.path)
    with pytest.raises(RuntimeError, match="synthetic dashboard failure"):
        server._build_pending_final_review_preview_readonly(
            offer_id=request["offer_id"], source_store=store, root=tmp_path
        )
    assert len(copies) == 2 and all(not copy.exists() for copy in copies)
    assert inventory(tmp_path) == before
    print("PRE_ADAPTER_EXCEPTION " + json.dumps({
        "before": before, "after": inventory(tmp_path),
        "copies_removed": all(not copy.exists() for copy in copies),
    }, sort_keys=True))
