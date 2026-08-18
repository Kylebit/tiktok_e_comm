from __future__ import annotations

import sqlite3
from pathlib import Path

from modules.products import server as product_server


def _database(path: Path) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE products (
            sku_id TEXT NOT NULL,
            shop_cipher TEXT NOT NULL,
            seller_sku TEXT,
            price REAL,
            PRIMARY KEY (sku_id, shop_cipher)
        );
        CREATE TABLE shopee_products (
            model_id TEXT NOT NULL,
            shop_id INTEGER NOT NULL,
            seller_sku TEXT,
            price REAL,
            PRIMARY KEY (model_id, shop_id)
        );
        CREATE TABLE sku_costs (
            sku_id TEXT PRIMARY KEY,
            cost_cny REAL NOT NULL
        );
        CREATE TABLE sku_logistics_weights (
            seller_sku TEXT PRIMARY KEY,
            weight_g INTEGER NOT NULL
        );
        INSERT INTO products VALUES ('tk-1', 'shop-1', '0001', 10.0);
        INSERT INTO shopee_products VALUES ('sp-1', 101, '0001', 11.0);
        INSERT INTO sku_costs VALUES ('tk-1', 3.0);
        INSERT INTO sku_logistics_weights VALUES ('0001', 100);
        """
    )
    connection.commit()
    connection.close()
    return path


def _patch_database(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(
        "core.database_maintenance.db_path",
        lambda: path,
    )


def test_catalog_preview_is_readonly_and_detects_same_count_content_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = _database(tmp_path / "shop.db")
    _patch_database(monkeypatch, database)

    first = product_server._catalog_database_baseline("full")
    connection = sqlite3.connect(database)
    connection.execute("UPDATE products SET price = 12 WHERE sku_id = 'tk-1'")
    connection.commit()
    connection.close()
    second = product_server._catalog_database_baseline("full")

    assert first["integrity"]["ok"] is True
    assert first["row_counts"] == second["row_counts"]
    assert first["content_sha256"] != second["content_sha256"]
    assert first["snapshot_id"] != second["snapshot_id"]
    assert first["backup_required"] is True
    assert first["business_quality"] == {
        "status": "ready",
        "issue_metric_count": 0,
        "metrics": {
            "same_shop_duplicate_seller_sku_groups": 0,
            "tiktok_rows_without_direct_cost": 0,
            "logistics_rows_without_tiktok_tail4": 0,
            "shopee_nonpositive_price_rows": 0,
        },
    }


def test_catalog_sync_requires_confirmation_and_matching_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = _database(tmp_path / "shop.db")
    _patch_database(monkeypatch, database)
    preview = product_server._catalog_database_baseline("fast")

    ok, message, status, details = product_server._start_catalog_sync(
        mode="fast",
        expected_snapshot_id=preview["snapshot_id"],
        confirm_catalog_update=False,
    )
    assert (ok, status, details) == (False, 400, {})
    assert "confirm_catalog_update" in message

    connection = sqlite3.connect(database)
    connection.execute("UPDATE products SET price = 13 WHERE sku_id = 'tk-1'")
    connection.commit()
    connection.close()
    ok, message, status, details = product_server._start_catalog_sync(
        mode="fast",
        expected_snapshot_id=preview["snapshot_id"],
        confirm_catalog_update=True,
    )
    assert ok is False
    assert status == 409
    assert "重新预检" in message
    assert details["current_preview"]["snapshot_id"] != preview["snapshot_id"]


def test_catalog_sync_creates_verified_backup_before_thread_start(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = _database(tmp_path / "shop.db")
    _patch_database(monkeypatch, database)
    monkeypatch.setattr(product_server, "ROOT", tmp_path)
    started: list[bool] = []

    class DeferredThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            started.append(True)

    monkeypatch.setattr(product_server.threading, "Thread", DeferredThread)
    product_server._catalog_sync_job.update(running=False, status="idle")
    preview = product_server._catalog_database_baseline("full")

    ok, message, status, details = product_server._start_catalog_sync(
        mode="full",
        expected_snapshot_id=preview["snapshot_id"],
        confirm_catalog_update=True,
    )

    assert ok is True
    assert status == 202
    assert started == [True]
    backup = Path(details["backup"]["destination_path"])
    assert backup.is_file()
    assert details["backup"]["integrity_check"] == ["ok"]
    assert len(details["backup"]["sha256"]) == 64
    assert product_server._catalog_sync_job["status"] == "running"
    product_server._catalog_sync_job.update(running=False, status="idle")
    persisted = product_server._load_latest_catalog_sync_status()
    assert persisted["run_id"] == details["run_id"]
    assert persisted["backup"]["sha256"] == details["backup"]["sha256"]


def test_catalog_sync_reports_partial_truthfully(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = _database(tmp_path / "shop.db")
    _patch_database(monkeypatch, database)
    monkeypatch.setattr(product_server, "ROOT", tmp_path)
    monkeypatch.setattr(
        "modules.catalog.sync.run_catalog_sync",
        lambda **_kwargs: {
            "tiktok": {"skus": 1},
            "shopee": {},
            "ozon": {},
            "errors": ["Shopee: unavailable"],
        },
    )
    product_server._catalog_sync_job.update(
        running=True,
        run_id="partial-test-run",
        mode="fast",
        status="running",
        started_at="2026-07-26T00:00:00+00:00",
        baseline=product_server._catalog_database_baseline("fast"),
    )

    product_server._run_catalog_sync()

    assert product_server._catalog_sync_job["running"] is False
    assert product_server._catalog_sync_job["status"] == "partial"
    assert product_server._catalog_sync_job["phase"] == "done"
    assert "部分失败" in product_server._catalog_sync_job["message"]
    assert product_server._catalog_sync_job["error"] == "Shopee: unavailable"
    persisted = product_server._load_latest_catalog_sync_status()
    assert persisted["status"] == "partial"
