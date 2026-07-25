from __future__ import annotations

import sqlite3

from modules.finance import sku_profit_shopee, sku_profit_tk


def _database(tmp_path):
    path = tmp_path / "shop.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE products (
            sku_id TEXT, seller_sku TEXT, product_id TEXT, product_name TEXT,
            sku_name TEXT, image_url TEXT, price REAL, currency TEXT, shop_cipher TEXT
        );
        CREATE TABLE sku_costs (sku_id TEXT, cost_cny REAL, updated_at INTEGER);
        CREATE TABLE shopee_products (
            model_id TEXT, item_id TEXT, seller_sku TEXT, product_name TEXT,
            model_name TEXT, image_url TEXT, price REAL, currency TEXT,
            status TEXT, region TEXT
        );
        INSERT INTO products VALUES
            ('1732993420424480699', '990021', 'p1', 'TikTok dog', 'large', '', 100, 'THB', 'th');
        INSERT INTO sku_costs VALUES ('1732993420424480699', 4.4, 1);
        INSERT INTO shopee_products VALUES
            ('9876543210000021', 'i1', '0021', 'Shopee dog', 'large', '', 120, 'THB', 'NORMAL', 'TH');
        INSERT INTO shopee_products VALUES
            ('item_40481686607', '40481686607', '0033', 'Shopee single model', '', '', 88, 'THB', 'NORMAL', 'TH');
        """
    )
    connection.commit()
    connection.close()
    return path


def _readonly(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def test_tiktok_long_id_is_exact_and_unknown_id_never_tail_falls_back(tmp_path, monkeypatch):
    path = _database(tmp_path)
    monkeypatch.setattr(sku_profit_tk, "connect_readonly", lambda: _readonly(path))
    monkeypatch.setattr(
        sku_profit_tk.auth,
        "ensure_valid_token",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    assert sku_profit_tk.resolve_product("1732993420424480699")["seller_sku"] == "990021"
    assert sku_profit_tk.resolve_product("9999999999990021") is None


def test_shopee_long_model_id_is_exact_and_unknown_id_never_tail_falls_back(tmp_path, monkeypatch):
    path = _database(tmp_path)
    monkeypatch.setattr(sku_profit_shopee, "connect_readonly", lambda: _readonly(path))

    assert sku_profit_shopee.resolve_product("9876543210000021")["seller_sku"] == "0021"
    assert sku_profit_shopee.resolve_product("item_40481686607")["seller_sku"] == "0033"
    assert sku_profit_shopee.resolve_product("40481686607")["seller_sku"] == "0033"
    assert sku_profit_shopee.resolve_product("9999999999990021") is None
