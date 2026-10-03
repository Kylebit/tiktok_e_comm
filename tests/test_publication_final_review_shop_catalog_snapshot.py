"""The pending final-review seam must not open source shop.db with SQLite."""

from copy import deepcopy
from pathlib import Path
import gc
import json
import os
import sqlite3

import pytest

from modules.products import server
from shared_platform import release_control
from shared_platform import publication_final_review_catalog as catalog_module
from shared_platform import publication_final_review_pending as pending_adapter
from shared_platform.publication_final_review_catalog import catalog_snapshot
from shared_platform.release_store import ReleaseStore
from test_publication_final_review_pre_adapter_read import inventory, ready
from test_release_control import _release_fixture


def make_catalog(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE products(product_id TEXT, seller_sku TEXT)")
        connection.execute("CREATE TABLE shopee_products(item_id TEXT, seller_sku TEXT)")
        connection.execute("INSERT INTO products VALUES ('t1', '0001')")
        connection.commit()
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    finally:
        connection.close()
    gc.collect()
    assert not Path(str(path) + "-wal").exists()


def test_catalog_snapshot_is_db_only_and_cleans_connections(tmp_path):
    source = tmp_path / "data" / "shop.db"
    make_catalog(source)
    before = inventory(tmp_path)
    with catalog_snapshot(source) as copied:
        assert copied != source and copied.exists()
        assert release_control._known_seller_skus(copied, strict=True) == ("0001",)
        assert release_control._known_tiktok_seller_skus(copied) == ("0001",)
        assert inventory(tmp_path) == before
    assert not copied.parent.exists()
    assert inventory(tmp_path) == before
    print("CATALOG_IMAGE " + json.dumps({"before": before, "after": inventory(tmp_path),
                                           "image_removed": not copied.parent.exists()}, sort_keys=True))


def test_catalog_snapshot_rejects_latest_committed_wal_without_stale_read(tmp_path):
    source = tmp_path / "shop.db"
    make_catalog(source)
    writer = sqlite3.connect(source)
    try:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO products VALUES ('t2', '0002')")
        writer.commit()
        assert writer.execute("SELECT seller_sku FROM products ORDER BY seller_sku").fetchall() == [
            ("0001",), ("0002",),
        ]
        before = inventory(tmp_path)
        assert "shop.db-wal" in before
        with pytest.raises(ValueError, match="FINAL_REVIEW_CATALOG_SOURCE_UNSTABLE"):
            with catalog_snapshot(source):
                pytest.fail("active WAL was admitted")
        assert inventory(tmp_path) == before
        print("CATALOG_ACTIVE_WAL " + json.dumps({"before": before,
                                                 "after": inventory(tmp_path)}, sort_keys=True))
    finally:
        writer.close()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_catalog_snapshot_rejects_any_sidecar(tmp_path, suffix):
    source = tmp_path / "shop.db"
    make_catalog(source)
    Path(str(source) + suffix).write_bytes(b"synthetic sidecar")
    before = inventory(tmp_path)
    with pytest.raises(ValueError, match="FINAL_REVIEW_CATALOG_SOURCE_UNSTABLE"):
        with catalog_snapshot(source):
            pytest.fail("sidecar was admitted")
    assert inventory(tmp_path) == before


def test_catalog_snapshot_rejects_aliases_and_missing(tmp_path):
    source = tmp_path / "shop.db"
    with pytest.raises(ValueError, match="FINAL_REVIEW_CATALOG_SOURCE_UNSTABLE"):
        with catalog_snapshot(source):
            pass
    make_catalog(source)
    alias = tmp_path / "alias.db"
    os.link(source, alias)
    before = inventory(tmp_path)
    with pytest.raises(ValueError, match="FINAL_REVIEW_CATALOG_SOURCE_UNSTABLE"):
        with catalog_snapshot(source):
            pass
    assert inventory(tmp_path) == before


def test_catalog_snapshot_rejects_reparse_source(tmp_path):
    source = tmp_path / "shop.db"
    make_catalog(source)
    alias = tmp_path / "reparse.db"
    alias.symlink_to(source)
    before = inventory(tmp_path)
    with pytest.raises(ValueError, match="FINAL_REVIEW_CATALOG_SOURCE_UNSTABLE"):
        with catalog_snapshot(alias):
            pass
    assert inventory(tmp_path) == before


def test_catalog_snapshot_rejects_copy_digest_drift_and_cleans(tmp_path, monkeypatch):
    source = tmp_path / "shop.db"
    make_catalog(source)
    before = inventory(tmp_path)
    copied = []
    original_digest = catalog_module._file_digest

    def changed_digest(path):
        copied.append(path)
        assert original_digest(path)
        return "different digest"

    monkeypatch.setattr(catalog_module, "_file_digest", changed_digest)
    with pytest.raises(ValueError, match="FINAL_REVIEW_CATALOG_SOURCE_UNSTABLE"):
        with catalog_snapshot(source):
            pass
    assert len(copied) == 1 and not copied[0].parent.exists()
    assert inventory(tmp_path) == before


def test_catalog_snapshot_rejects_source_identity_drift(tmp_path, monkeypatch):
    source = tmp_path / "shop.db"
    make_catalog(source)
    before = inventory(tmp_path)
    original_identity = catalog_module._path_identity
    calls = 0

    def changed_identity(path):
        nonlocal calls
        calls += 1
        identity = original_identity(path)
        return identity if calls == 1 else (*identity[:2], identity[2] + 1, identity[3])

    monkeypatch.setattr(catalog_module, "_path_identity", changed_identity)
    with pytest.raises(ValueError, match="FINAL_REVIEW_CATALOG_SOURCE_UNSTABLE"):
        with catalog_snapshot(source):
            pass
    assert calls == 2
    assert inventory(tmp_path) == before


def test_pending_seam_injects_catalog_image_and_keeps_both_copies(tmp_path, monkeypatch):
    _, dashboard, store, request, _ = ready(tmp_path, monkeypatch)
    source = tmp_path / "data" / "synthetic-shop.db"
    make_catalog(source)
    before = inventory(tmp_path)
    seen = []

    def dashboard_on_snapshots(**kwargs):
        copied = Path(kwargs["database_path"])
        assert copied != source and copied.exists()
        assert kwargs["strict_catalog"] is True
        assert kwargs["report_store_path"] == kwargs["report_store"].path
        assert kwargs["report_store_path"] != store.path
        assert release_control._known_seller_skus(copied, strict=True) == ("0001",)
        seen.append((copied, kwargs["report_store_path"]))
        return deepcopy(dashboard)

    monkeypatch.setattr(release_control, "build_release_dashboard", dashboard_on_snapshots)
    preview = server._build_pending_final_review_preview_readonly(
        offer_id=request["offer_id"], source_store=store, root=tmp_path,
        database_path=source,
    )
    assert preview.as_dict()["status"] == "UNAPPROVED_PREVIEW"
    assert len(seen) == 1
    assert all(not path.parent.exists() for path in seen[0])
    assert inventory(tmp_path) == before
    print("CATALOG_SEAM " + json.dumps({"before": before, "after": inventory(tmp_path),
                                       "images_removed": True}, sort_keys=True))


def test_actual_dashboard_uses_both_images_and_leaves_source_tree_exact(tmp_path, monkeypatch):
    root, source_catalog = _release_fixture(tmp_path)
    source_store = root / "data" / "orbit_platform.db"
    connection = sqlite3.connect(source_store)
    try:
        connection.execute("CREATE TABLE synthetic_marker(value TEXT)")
        connection.commit()
    finally:
        connection.close()
    connection = sqlite3.connect(source_catalog)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    finally:
        connection.close()
    gc.collect()  # _release_fixture uses a transaction context, which does not close.
    before = inventory(root)
    original_connect = sqlite3.connect
    source_strings = tuple(str(path).replace("\\", "/").lower()
                           for path in (source_catalog, source_store))

    def audited_connect(database, *args, **kwargs):
        normalized = str(database).replace("\\", "/").lower()
        if any(source in normalized for source in source_strings):
            pytest.fail("dashboard opened a source SQLite database")
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", audited_connect)
    with pending_adapter.pending_store_snapshot(ReleaseStore(source_store)) as store_image:
        with catalog_snapshot(source_catalog) as catalog_image:
            result = release_control.build_release_dashboard(
                root=root, database_path=catalog_image,
                report_store_path=store_image.path, report_store=store_image,
                offer_id="3828811808", seller_sku="0946", strict_catalog=True,
                frozen_pricing={
                    "schema_version": "synthetic/v1", "selected_store_prices": [],
                    "workbench_exchange_rates": {}, "shopee_exchange_rates": {},
                    "ozon_exchange_rates": {}, "target_pricing": {},
                },
            )
            assert result["product"]["offer_id"] == "3828811808"
            assert catalog_image.exists() and store_image.path.exists()
    assert not catalog_image.parent.exists() and not store_image.path.parent.exists()
    assert inventory(root) == before
    print("CATALOG_ACTUAL_DASHBOARD " + json.dumps({"before": before,
        "after": inventory(root), "images_removed": True}, sort_keys=True))


def test_strict_catalog_query_error_blocks_instead_of_empty_skus(tmp_path):
    source = tmp_path / "shop.db"
    connection = sqlite3.connect(source)
    try:
        connection.execute("CREATE TABLE shopee_products(item_id TEXT, seller_sku TEXT)")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(ValueError, match="FINAL_REVIEW_CATALOG_QUERY_UNAVAILABLE"):
        release_control._known_seller_skus(source, strict=True)
    with pytest.raises(ValueError, match="FINAL_REVIEW_CATALOG_QUERY_UNAVAILABLE"):
        release_control._known_tiktok_seller_skus(source, strict=True)
    assert release_control._known_seller_skus(source) == ()


def test_strict_catalog_ownership_query_error_blocks(tmp_path):
    source = tmp_path / "shop.db"
    connection = sqlite3.connect(source)
    try:
        connection.execute("CREATE TABLE products(product_id TEXT, seller_sku TEXT)")
        connection.execute("INSERT INTO products VALUES ('t1', '0001')")
        connection.commit()
    finally:
        connection.close()

    class ApprovedRun:
        def active_plan_for_product(self, product_id):
            return {"status": "APPROVED", "seller_sku": "0001", "payload_digest": "abc"}

        def get_run(self, run_id):
            return {"targets": []}

    with pytest.raises(ValueError, match="FINAL_REVIEW_CATALOG_QUERY_UNAVAILABLE"):
        release_control._catalog_sku_is_owned_by_release(
            source, ApprovedRun(), product_id="offer", seller_sku="0001", strict=True,
        )


def test_catalog_copy_removed_after_body_exception(tmp_path):
    source = tmp_path / "shop.db"
    make_catalog(source)
    before = inventory(tmp_path)
    copied = None
    with pytest.raises(RuntimeError, match="synthetic failure"):
        with catalog_snapshot(source) as copied:
            assert release_control._known_seller_skus(copied, strict=True) == ("0001",)
            assert release_control._known_tiktok_seller_skus(copied, strict=True) == ("0001",)
            raise RuntimeError("synthetic failure")
    assert copied is not None and not copied.parent.exists()
    assert inventory(tmp_path) == before
