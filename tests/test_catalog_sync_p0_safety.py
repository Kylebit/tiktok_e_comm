from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from modules.ozon import sync as ozon_sync
from modules.products import sync as tiktok_sync
from modules.shopee import sync as shopee_sync


@pytest.fixture(autouse=True)
def _block_repository_cache_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "modules.catalog.sync_cache.save_tk_detail",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "modules.catalog.sync_cache.save_shopee_manifest",
        lambda *_args, **_kwargs: None,
    )


def _connection_factory(path: Path):
    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

    return connect


def _create_tiktok_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE shops (
            cipher TEXT PRIMARY KEY,
            shop_id TEXT,
            name TEXT,
            region TEXT,
            seller_type TEXT,
            updated_at INTEGER
        );
        CREATE TABLE products (
            sku_id TEXT NOT NULL,
            shop_cipher TEXT NOT NULL,
            product_id TEXT,
            global_product_id TEXT,
            global_sku_id TEXT,
            seller_sku TEXT,
            product_name TEXT,
            sku_name TEXT,
            image_url TEXT,
            price REAL,
            currency TEXT,
            stock INTEGER,
            status TEXT,
            updated_at INTEGER,
            PRIMARY KEY (sku_id, shop_cipher)
        );
        """
    )
    connection.execute(
        """
        INSERT INTO products (
            sku_id, shop_cipher, product_id, seller_sku, product_name,
            sku_name, image_url, price, currency, stock, status, updated_at
        ) VALUES ('old-sku', 'cipher-1', 'old-product', '0001', 'Old', '',
                  '', 1, 'THB', 1, 'ACTIVATE', 1)
        """
    )
    connection.commit()
    connection.close()


def _tiktok_shop() -> dict:
    return {
        "cipher": "cipher-1",
        "id": "shop-1",
        "name": "Test Shop",
        "region": "TH",
        "seller_type": "LOCAL_TO_LOCAL",
    }


def _active_tiktok_detail(product_id: str = "new-product") -> dict:
    return {
        "id": product_id,
        "title": "New product",
        "product_status": "ACTIVATE",
        "main_images": [],
        "skus": [
            {
                "id": "new-sku",
                "seller_sku": "0002",
                "status_info": {"status": "NORMAL"},
                "price": {"sale_price": "10.5", "currency": "THB"},
                "inventory": [{"quantity": 3}],
            }
        ],
    }


def test_tiktok_empty_remote_snapshot_does_not_delete_existing_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "shop.db"
    _create_tiktok_database(database)
    monkeypatch.setattr(tiktok_sync, "connect", _connection_factory(database))
    monkeypatch.setattr(
        tiktok_sync,
        "post",
        lambda *_args, **_kwargs: {
            "code": 0,
            "data": {"products": [], "next_page_token": ""},
        },
    )

    with pytest.raises(RuntimeError, match="empty product list"):
        tiktok_sync.sync_shop(
            "token",
            _tiktok_shop(),
            use_cache=False,
            force_refresh=True,
        )

    connection = sqlite3.connect(database)
    assert connection.execute("SELECT sku_id FROM products").fetchall() == [
        ("old-sku",)
    ]
    connection.close()


def test_tiktok_incomplete_no_detail_mode_is_rejected_before_database_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "shop.db"
    _create_tiktok_database(database)
    monkeypatch.setattr(tiktok_sync, "connect", _connection_factory(database))

    with pytest.raises(ValueError, match="requires product details"):
        tiktok_sync.sync_shop(
            "token",
            _tiktok_shop(),
            fetch_images=False,
            use_cache=False,
            force_refresh=True,
        )

    connection = sqlite3.connect(database)
    assert connection.execute("SELECT sku_id FROM products").fetchall() == [
        ("old-sku",)
    ]
    connection.close()


def test_tiktok_detail_failure_leaves_existing_shop_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "shop.db"
    _create_tiktok_database(database)
    monkeypatch.setattr(tiktok_sync, "connect", _connection_factory(database))
    monkeypatch.setattr(
        tiktok_sync,
        "post",
        lambda *_args, **_kwargs: {
            "code": 0,
            "data": {
                "products": [{"id": "new-product"}],
                "next_page_token": "",
            },
        },
    )
    monkeypatch.setattr(
        tiktok_sync,
        "_fetch_product_detail",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("detail failed")
        ),
    )

    with pytest.raises(RuntimeError, match="detail failed"):
        tiktok_sync.sync_shop(
            "token",
            _tiktok_shop(),
            use_cache=False,
            force_refresh=True,
        )

    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT sku_id, product_id FROM products"
    ).fetchall() == [("old-sku", "old-product")]
    connection.close()


def test_tiktok_successful_full_refresh_replaces_stale_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "shop.db"
    _create_tiktok_database(database)
    monkeypatch.setattr(tiktok_sync, "connect", _connection_factory(database))
    monkeypatch.setattr(
        tiktok_sync,
        "post",
        lambda *_args, **_kwargs: {
            "code": 0,
            "data": {
                "products": [{"id": "new-product"}],
                "next_page_token": "",
            },
        },
    )
    monkeypatch.setattr(
        tiktok_sync,
        "_fetch_product_detail",
        lambda *_args, **_kwargs: _active_tiktok_detail(),
    )

    result = tiktok_sync.sync_shop(
        "token",
        _tiktok_shop(),
        use_cache=False,
        force_refresh=True,
    )

    assert result["products"] == 1
    assert result["skus"] == 1
    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT sku_id, product_id FROM products"
    ).fetchall() == [("new-sku", "new-product")]
    connection.close()


def test_tiktok_database_failure_rolls_back_shop_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "shop.db"
    _create_tiktok_database(database)
    monkeypatch.setattr(tiktok_sync, "connect", _connection_factory(database))
    monkeypatch.setattr(
        tiktok_sync,
        "post",
        lambda *_args, **_kwargs: {
            "code": 0,
            "data": {
                "products": [{"id": "new-product"}],
                "next_page_token": "",
            },
        },
    )
    monkeypatch.setattr(
        tiktok_sync,
        "_fetch_product_detail",
        lambda *_args, **_kwargs: _active_tiktok_detail(),
    )
    real_upsert = tiktok_sync._upsert_products

    def insert_then_fail(connection, rows):
        real_upsert(connection, rows)
        raise RuntimeError("database write failed")

    monkeypatch.setattr(tiktok_sync, "_upsert_products", insert_then_fail)

    with pytest.raises(RuntimeError, match="database write failed"):
        tiktok_sync.sync_shop(
            "token",
            _tiktok_shop(),
            use_cache=False,
            force_refresh=True,
        )

    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT sku_id, product_id FROM products"
    ).fetchall() == [("old-sku", "old-product")]
    connection.close()


def _create_shopee_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE shopee_shops (
            shop_id INTEGER PRIMARY KEY,
            region TEXT,
            shop_name TEXT,
            updated_at INTEGER
        );
        CREATE TABLE shopee_products (
            model_id TEXT NOT NULL,
            shop_id INTEGER NOT NULL,
            region TEXT,
            item_id TEXT,
            seller_sku TEXT,
            product_name TEXT,
            model_name TEXT,
            image_url TEXT,
            price REAL,
            currency TEXT,
            stock INTEGER,
            status TEXT,
            updated_at INTEGER,
            PRIMARY KEY (model_id, shop_id)
        );
        """
    )
    connection.execute(
        """
        INSERT INTO shopee_products (
            model_id, shop_id, region, item_id, seller_sku, product_name,
            model_name, image_url, price, currency, stock, status, updated_at
        ) VALUES ('old-model', 101, 'TH', 'old-item', '0001', 'Old', '',
                  '', 1, 'THB', 1, 'NORMAL', 1)
        """
    )
    connection.commit()
    connection.close()


def _shopee_row() -> dict:
    return {
        "model_id": "new-model",
        "shop_id": 101,
        "region": "TH",
        "item_id": "202",
        "seller_sku": "0002",
        "product_name": "New",
        "model_name": "",
        "image_url": "",
        "price": 20.0,
        "currency": "THB",
        "stock": 2,
        "status": "NORMAL",
        "updated_at": 2,
    }


def _prepare_shopee(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    *,
    item_ids: list[int],
    items: list[dict],
) -> None:
    monkeypatch.setattr(shopee_sync, "connect", _connection_factory(database))
    monkeypatch.setattr(shopee_sync, "_token_for_shop", lambda _shop_id: "token")
    monkeypatch.setattr(
        shopee_sync,
        "_fetch_item_ids",
        lambda _shop_id, _token: list(item_ids),
    )
    monkeypatch.setattr(
        shopee_sync,
        "_fetch_items_base",
        lambda _shop_id, _token, _ids: list(items),
    )
def test_shopee_empty_remote_snapshot_does_not_delete_existing_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "shop.db"
    _create_shopee_database(database)
    _prepare_shopee(monkeypatch, database, item_ids=[], items=[])

    with pytest.raises(RuntimeError, match="empty item list"):
        shopee_sync.sync_shop(
            101,
            "TH",
            "Shopee Test",
            use_cache=False,
            force_refresh=True,
        )

    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT model_id FROM shopee_products"
    ).fetchall() == [("old-model",)]
    connection.close()


def test_shopee_detail_failure_leaves_existing_shop_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "shop.db"
    _create_shopee_database(database)
    _prepare_shopee(
        monkeypatch,
        database,
        item_ids=[202],
        items=[{"item_id": 202, "has_model": True}],
    )
    monkeypatch.setattr(
        shopee_sync,
        "_rows_from_item",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("model detail failed")
        ),
    )

    with pytest.raises(RuntimeError, match="model detail failed"):
        shopee_sync.sync_shop(
            101,
            "TH",
            "Shopee Test",
            use_cache=False,
            force_refresh=True,
        )

    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT model_id, item_id FROM shopee_products"
    ).fetchall() == [("old-model", "old-item")]
    connection.close()


def test_shopee_missing_base_detail_leaves_existing_shop_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "shop.db"
    _create_shopee_database(database)
    _prepare_shopee(
        monkeypatch,
        database,
        item_ids=[202, 203],
        items=[{"item_id": 202, "has_model": False}],
    )

    with pytest.raises(RuntimeError, match="snapshot is incomplete"):
        shopee_sync.sync_shop(
            101,
            "TH",
            "Shopee Test",
            use_cache=False,
            force_refresh=True,
        )

    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT model_id, item_id FROM shopee_products"
    ).fetchall() == [("old-model", "old-item")]
    connection.close()


@pytest.mark.parametrize("force_refresh", [False, True])
def test_shopee_successful_snapshot_replaces_stale_items_and_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    force_refresh: bool,
) -> None:
    database = tmp_path / "shop.db"
    _create_shopee_database(database)
    _prepare_shopee(
        monkeypatch,
        database,
        item_ids=[202],
        items=[{"item_id": 202, "has_model": False}],
    )
    monkeypatch.setattr(
        shopee_sync,
        "_rows_from_item",
        lambda *_args, **_kwargs: ([_shopee_row()], False),
    )

    result = shopee_sync.sync_shop(
        101,
        "TH",
        "Shopee Test",
        use_cache=False,
        force_refresh=force_refresh,
    )

    assert result["items"] == 1
    assert result["skus"] == 1
    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT model_id, item_id FROM shopee_products"
    ).fetchall() == [("new-model", "202")]
    connection.close()


def test_shopee_database_failure_rolls_back_shop_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "shop.db"
    _create_shopee_database(database)
    _prepare_shopee(
        monkeypatch,
        database,
        item_ids=[202],
        items=[{"item_id": 202, "has_model": False}],
    )
    monkeypatch.setattr(
        shopee_sync,
        "_rows_from_item",
        lambda *_args, **_kwargs: ([_shopee_row()], False),
    )
    real_upsert = shopee_sync._upsert_products

    def insert_then_fail(connection, rows):
        real_upsert(connection, rows)
        raise RuntimeError("database write failed")

    monkeypatch.setattr(shopee_sync, "_upsert_products", insert_then_fail)

    with pytest.raises(RuntimeError, match="database write failed"):
        shopee_sync.sync_shop(
            101,
            "TH",
            "Shopee Test",
            use_cache=False,
            force_refresh=True,
        )

    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT model_id, item_id FROM shopee_products"
    ).fetchall() == [("old-model", "old-item")]
    connection.close()


def _existing_ozon_snapshot(path: Path) -> str:
    old = json.dumps(
        {"result": [{"id": 1, "offer_id": "0001"}], "total": 1},
        ensure_ascii=False,
    )
    path.write_text(old, encoding="utf-8")
    return old


def test_ozon_empty_product_list_preserves_nonempty_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = tmp_path / "all_products_attrs.json"
    old = _existing_ozon_snapshot(snapshot)
    monkeypatch.setattr(ozon_sync, "ready", lambda: True)
    monkeypatch.setattr(ozon_sync, "ozon_data_dir", lambda: tmp_path)
    monkeypatch.setattr(ozon_sync, "fetch_all_product_ids", lambda *_args: [])

    with pytest.raises(RuntimeError, match="empty product list"):
        ozon_sync.sync_catalog(use_cache=False, force_refresh=True)

    assert snapshot.read_text(encoding="utf-8") == old


def test_ozon_empty_attributes_preserve_nonempty_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = tmp_path / "all_products_attrs.json"
    old = _existing_ozon_snapshot(snapshot)
    monkeypatch.setattr(ozon_sync, "ready", lambda: True)
    monkeypatch.setattr(ozon_sync, "ozon_data_dir", lambda: tmp_path)
    monkeypatch.setattr(ozon_sync, "fetch_all_product_ids", lambda *_args: [2])
    monkeypatch.setattr(
        ozon_sync,
        "fetch_product_attributes",
        lambda *_args: [],
    )

    with pytest.raises(RuntimeError, match="no product attributes"):
        ozon_sync.sync_catalog(use_cache=False, force_refresh=True)

    assert snapshot.read_text(encoding="utf-8") == old


def test_ozon_unreadable_snapshot_is_never_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = tmp_path / "all_products_attrs.json"
    snapshot.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(ozon_sync, "ready", lambda: True)
    monkeypatch.setattr(ozon_sync, "ozon_data_dir", lambda: tmp_path)
    monkeypatch.setattr(ozon_sync, "fetch_all_product_ids", lambda *_args: [])

    with pytest.raises(RuntimeError, match="snapshot is unreadable"):
        ozon_sync.sync_catalog(use_cache=False, force_refresh=True)

    assert snapshot.read_text(encoding="utf-8") == "{not-json"


def test_ozon_json_replace_failure_preserves_previous_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = tmp_path / "all_products_attrs.json"
    old = _existing_ozon_snapshot(snapshot)
    monkeypatch.setattr(
        ozon_sync.os,
        "replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("replace failed")
        ),
    )

    with pytest.raises(OSError, match="replace failed"):
        ozon_sync._save_json(
            snapshot,
            {"result": [{"id": 2, "offer_id": "0002"}], "total": 1},
        )

    assert snapshot.read_text(encoding="utf-8") == old
    assert list(tmp_path.glob(f".{snapshot.name}.*.tmp")) == []
