from pathlib import Path
import sqlite3

import pytest

from core.database_maintenance import backup_database, inspect_database
from core.db import connect_readonly
from domains.product_operations.catalog_database_audit import audit_catalog_database
from scripts.database_maintenance import main as database_maintenance_main


def _database(path: Path, *, wal: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute("INSERT INTO records (value) VALUES ('committed')")
    connection.commit()
    return connection


def test_readonly_connection_queries_but_cannot_write(tmp_path):
    path = tmp_path / "source.db"
    _database(path).close()

    connection = connect_readonly(path)
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("SELECT value FROM records").fetchone()[0] == "committed"
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO records (value) VALUES ('forbidden')")
    finally:
        connection.close()


def test_readonly_connection_does_not_create_a_missing_database(tmp_path):
    path = tmp_path / "missing" / "source.db"

    with pytest.raises(FileNotFoundError):
        connect_readonly(path)

    assert not path.exists()
    assert not path.parent.exists()


def test_health_check_is_side_effect_free_and_reports_integrity(tmp_path):
    path = tmp_path / "source.db"
    _database(path).close()
    before = path.stat()

    report = inspect_database(path, full_integrity=True)

    assert report.ok is True
    assert report.quick_check == ("ok",)
    assert report.integrity_check == ("ok",)
    assert report.row_counts == {"records": 1}
    assert report.table_count == 1
    assert report.trigger_count == 0
    assert path.stat().st_size == before.st_size
    assert path.stat().st_mtime_ns == before.st_mtime_ns


def test_online_backup_includes_committed_wal_data_and_is_verified(tmp_path):
    source = tmp_path / "live.db"
    live_connection = _database(source, wal=True)
    live_connection.execute("INSERT INTO records (value) VALUES ('in-wal')")
    live_connection.commit()
    destination = tmp_path / "backups" / "snapshot.db"

    result = backup_database(destination, source=source)

    assert result.destination_path == destination.resolve()
    assert result.integrity_check == ("ok",)
    assert len(result.sha256) == 64
    restored = connect_readonly(destination)
    try:
        assert restored.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
    finally:
        restored.close()
        live_connection.close()


def test_backup_never_overwrites_an_existing_file(tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "snapshot.db"
    _database(source).close()
    destination.write_bytes(b"keep-me")

    with pytest.raises(FileExistsError):
        backup_database(destination, source=source)

    assert destination.read_bytes() == b"keep-me"


def test_catalog_quality_audit_reports_identity_cost_and_derived_orphans(tmp_path):
    path = tmp_path / "catalog.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE shops (cipher TEXT PRIMARY KEY);
        CREATE TABLE products (
            sku_id TEXT,
            shop_cipher TEXT,
            product_id TEXT,
            seller_sku TEXT,
            currency TEXT
        );
        CREATE TABLE sku_costs (sku_id TEXT PRIMARY KEY, cost_cny REAL);
        CREATE TABLE product_analytics (product_id TEXT, shop_cipher TEXT);
        CREATE TABLE sku_logistics_weights (seller_sku TEXT);
        CREATE TABLE shopee_products (price REAL);
        INSERT INTO shops VALUES ('TH');
        INSERT INTO products VALUES ('P1', 'TH', 'ITEM1', '990017', 'THB');
        INSERT INTO products VALUES ('P2', 'TH', 'ITEM2', '990017', 'THB');
        INSERT INTO products VALUES ('P3', 'TH', 'ITEM3', '18', 'THB');
        INSERT INTO products VALUES ('P4', 'TH', 'ITEM4', '0018', 'THB');
        INSERT INTO products VALUES ('P5', 'MISSING', 'ITEM5', '0019', 'PHP');
        INSERT INTO sku_costs VALUES ('P1', 3);
        INSERT INTO sku_costs VALUES ('P3', 5);
        INSERT INTO sku_costs VALUES ('P4', 5.5);
        INSERT INTO product_analytics VALUES ('ITEM1', 'TH');
        INSERT INTO product_analytics VALUES ('GONE', 'TH');
        INSERT INTO sku_logistics_weights VALUES ('660017');
        INSERT INTO sku_logistics_weights VALUES ('0099');
        INSERT INTO shopee_products VALUES (0);
        """
    )
    connection.commit()
    connection.close()

    report = audit_catalog_database(path)

    assert report.product_count == 5
    assert report.direct_missing_cost_rows == 2
    assert report.fallback_resolved_cost_rows == 1
    assert report.unresolved_cost_rows == 1
    assert report.unresolved_cost_key_count == 1
    assert report.cost_conflicts == (
        {"seller_sku_key": "0018", "costs_cny": ("5.0", "5.5")},
    )
    assert len(report.same_shop_seller_sku_duplicates) == 1
    assert report.product_shop_orphans == 1
    assert report.analytics_orphans == 1
    assert report.logistics_exact_unmatched == 2
    assert report.logistics_canonical_unmatched == 1
    assert report.shopee_nonpositive_prices == 1
    assert report.needs_review is True


def test_orbit_build_rejects_packaged_runtime_databases_and_credentials():
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "build_orbit_desktop.ps1"
    ).read_text(encoding="utf-8")

    assert 'Join-Path $BundleInternal "data"' in script
    assert "Resolve-Path -LiteralPath $PackagedData" in script
    assert "Remove-Item -LiteralPath $ResolvedData -Recurse -Force" in script
    for forbidden_name in ("shop.db", "orbit_platform.db", "Cookies", "Login Data"):
        assert forbidden_name in script


def test_quality_cli_can_fail_a_release_gate_on_review_items(tmp_path):
    path = tmp_path / "catalog.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE shops (cipher TEXT PRIMARY KEY);
        CREATE TABLE products (
            sku_id TEXT,
            shop_cipher TEXT,
            product_id TEXT,
            seller_sku TEXT,
            currency TEXT
        );
        CREATE TABLE sku_costs (sku_id TEXT PRIMARY KEY, cost_cny REAL);
        CREATE TABLE product_analytics (product_id TEXT, shop_cipher TEXT);
        CREATE TABLE sku_logistics_weights (seller_sku TEXT);
        CREATE TABLE shopee_products (price REAL);
        INSERT INTO shops VALUES ('TH');
        INSERT INTO products VALUES ('P1', 'TH', 'ITEM1', '0001', 'THB');
        """
    )
    connection.commit()
    connection.close()

    assert (
        database_maintenance_main(
            ["quality", "--database", str(path), "--fail-on-review"]
        )
        == 2
    )
