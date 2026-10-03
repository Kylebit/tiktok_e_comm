from __future__ import annotations

import sqlite3

from modules.finance import sku_profit_shopee, sku_profit_tk
from shared_platform.catalog_sku_costs import SCHEMA as CATALOG_COST_SCHEMA, digest


def _database(tmp_path):
    path = tmp_path / "shop.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE shops (cipher TEXT, region TEXT);
        CREATE TABLE products (
            sku_id TEXT, seller_sku TEXT, product_id TEXT, product_name TEXT,
            sku_name TEXT, image_url TEXT, price REAL, currency TEXT, shop_cipher TEXT,
            global_product_id TEXT, global_sku_id TEXT
        );
        CREATE TABLE sku_costs (sku_id TEXT, cost_cny REAL, updated_at INTEGER);
        CREATE TABLE shopee_products (
            model_id TEXT, shop_id TEXT, item_id TEXT, seller_sku TEXT, product_name TEXT,
            model_name TEXT, image_url TEXT, price REAL, currency TEXT,
            status TEXT, region TEXT
        );
        INSERT INTO shops VALUES ('th', 'TH'), ('my', 'MY'), ('vn', 'VN');
        INSERT INTO products VALUES
            ('1732993420424480699', '990021', 'p1', 'TikTok dog', 'large', '', 100, 'THB', 'th', '', ''),
            ('tk-th-0023', '990023', 'p23', 'TikTok TH shared', '', '', 100, 'THB', 'th', '', ''),
            ('tk-my-0023', '660023', 'p23-my', 'TikTok MY shared', '', '', 100, 'MYR', 'my', '', ''),
            ('tk-th-0024', '990024', 'p24', 'TikTok TH conflict', '', '', 100, 'THB', 'th', '', ''),
            ('tk-my-0024', '660024', 'p24-my', 'TikTok MY conflict', '', '', 100, 'MYR', 'my', '', ''),
            ('tk-vn-0024', '880024', 'p24-vn', 'TikTok VN conflict', '', '', 100, 'VND', 'vn', '', ''),
            ('tk-th-0025', '990025', 'p25', 'TikTok TH isolated', '', '', 100, 'THB', 'th', '', ''),
            ('tk-my-0026', '660026', 'p26-my', 'TikTok MY other', '', '', 100, 'MYR', 'my', '', '');
        INSERT INTO sku_costs VALUES
            ('1732993420424480699', 4.4, 1),
            ('tk-my-0023', 5.5, 30),
            ('tk-my-0024', 5.5, 30),
            ('tk-vn-0024', 8.8, 40),
            ('tk-my-0026', 6.6, 50);
        INSERT INTO shopee_products VALUES
            ('9876543210000021', '100', 'i1', '0021', 'Shopee dog', 'large', '', 120, 'THB', 'NORMAL', 'TH'),
            ('item_40481686607', '100', '40481686607', '0033', 'Shopee single model', '', '', 88, 'THB', 'NORMAL', 'TH'),
            ('sp-th-0023', '100', 'i23', '0023', 'Shopee TH shared', '', '', 120, 'THB', 'NORMAL', 'TH'),
            ('sp-th-0024', '100', 'i24', '0024', 'Shopee TH conflict', '', '', 120, 'THB', 'NORMAL', 'TH'),
            ('sp-th-0025', '100', 'i25', '0025', 'Shopee TH isolated', '', '', 120, 'THB', 'NORMAL', 'TH');
        """
    )
    connection.commit()
    connection.close()
    return path


def _readonly(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _set_current_manual_cost(path, internal_sku: str, amount: float):
    connection = sqlite3.connect(path)
    connection.executescript(CATALOG_COST_SCHEMA)
    entity_key = "internal:" + digest(internal_sku)
    connection.execute(
        "INSERT INTO catalog_sku_costs VALUES (?, ?, ?, ?)",
        (entity_key, str(amount), 1, "{}"),
    )
    connection.commit()
    connection.close()


def _offline_tiktok(monkeypatch, path):
    monkeypatch.setattr(sku_profit_tk, "connect_readonly", lambda: _readonly(path))
    monkeypatch.setattr(
        sku_profit_tk.auth,
        "ensure_valid_token",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )


def _offline_shopee(monkeypatch, path):
    monkeypatch.setattr(sku_profit_shopee, "connect_readonly", lambda: _readonly(path))


def test_tiktok_long_id_is_exact_and_unknown_id_never_tail_falls_back(tmp_path, monkeypatch):
    path = _database(tmp_path)
    _offline_tiktok(monkeypatch, path)

    assert sku_profit_tk.resolve_product("1732993420424480699")["seller_sku"] == "990021"
    assert sku_profit_tk.resolve_product("9999999999990021") is None


def test_shared_internal_cost_crosses_country_and_platform_after_exact_listing_resolution(tmp_path, monkeypatch):
    path = _database(tmp_path)
    _offline_tiktok(monkeypatch, path)
    _offline_shopee(monkeypatch, path)

    tiktok = sku_profit_tk.resolve_product("990023")
    shopee = sku_profit_shopee.resolve_product("0023")

    assert (tiktok["shop_cipher"], tiktok["currency"], tiktok["cost_cny"]) == ("th", "THB", 5.5)
    assert (shopee["item_id"], shopee["currency"], shopee["cost_cny"]) == ("i23", "THB", 5.5)
    assert tiktok["cost_source"] == shopee["cost_source"] == "canonical_internal_sku_inherited"


def test_canonical_manual_cost_overrides_stale_inherited_sources_for_all_consumers(tmp_path, monkeypatch):
    path = _database(tmp_path)
    _set_current_manual_cost(path, "0023", 7.7)
    _offline_tiktok(monkeypatch, path)
    _offline_shopee(monkeypatch, path)
    monkeypatch.setattr(sku_profit_shopee, "_cost_from_weekly", lambda _seller_sku: 42.0)

    assert sku_profit_tk.resolve_product("990023")["cost_cny"] == 7.7
    assert sku_profit_shopee.resolve_product("0023")["cost_cny"] == 7.7
    assert sku_profit_tk.resolve_product("990023")["cost_source"] == "canonical_internal_sku_manual"


def test_conflicting_inherited_costs_remain_unresolved_without_latest_row_guess(tmp_path, monkeypatch):
    path = _database(tmp_path)
    _offline_tiktok(monkeypatch, path)
    _offline_shopee(monkeypatch, path)
    monkeypatch.setattr(sku_profit_shopee, "_cost_from_weekly", lambda _seller_sku: 42.0)

    for product in (sku_profit_tk.resolve_product("990024"), sku_profit_shopee.resolve_product("0024")):
        assert product["cost_cny"] is None
        assert product["cost_source"] == "canonical_internal_sku_conflict"


def test_different_internal_sku_cannot_borrow_another_shared_cost(tmp_path, monkeypatch):
    path = _database(tmp_path)
    _offline_tiktok(monkeypatch, path)
    _offline_shopee(monkeypatch, path)
    monkeypatch.setattr(sku_profit_shopee, "_cost_from_weekly", lambda _seller_sku: 42.0)

    assert sku_profit_tk.resolve_product("990025")["cost_cny"] is None
    assert sku_profit_shopee.resolve_product("0025")["cost_cny"] is None


def test_weekly_cost_cannot_replace_an_unresolved_canonical_cost(tmp_path, monkeypatch):
    path = _database(tmp_path)
    _offline_shopee(monkeypatch, path)
    monkeypatch.setattr(
        sku_profit_shopee,
        "read_current_internal_sku_cost",
        lambda _conn, _identity: (None, "canonical_internal_sku_unresolved"),
    )
    monkeypatch.setattr(sku_profit_shopee, "_cost_from_weekly", lambda _seller_sku: 42.0)

    product = sku_profit_shopee.resolve_product("0023")

    assert product["cost_cny"] is None
    assert product["cost_source"] == "canonical_internal_sku_unresolved"
    assert (product["item_id"], product["currency"]) == ("i23", "THB")


def test_hot_skus_use_the_same_current_internal_cost_reader(tmp_path, monkeypatch):
    path = _database(tmp_path)
    _set_current_manual_cost(path, "0023", 7.7)
    _offline_tiktok(monkeypatch, path)
    income_dir = tmp_path / "income"
    income_dir.mkdir()
    (income_dir / "income_TH_fixture.csv").write_text("Type ,SKU ID\nOrder,tk-th-0023\n", encoding="utf-8")
    monkeypatch.setattr(sku_profit_tk, "INCOME_DIR", income_dir)

    rows = sku_profit_tk.list_hot_skus()

    assert rows == [
        {
            "seller_sku": "990023",
            "sku_id": "tk-th-0023",
            "product_name": "TikTok TH shared",
            "image_url": "",
            "price": 100.0,
            "cost_cny": 7.7,
            "order_lines": 1,
            "platform": "tiktok",
        }
    ]


def test_shopee_long_model_id_is_exact_and_unknown_id_never_tail_falls_back(tmp_path, monkeypatch):
    path = _database(tmp_path)
    _offline_shopee(monkeypatch, path)

    assert sku_profit_shopee.resolve_product("9876543210000021")["seller_sku"] == "0021"
    assert sku_profit_shopee.resolve_product("item_40481686607")["seller_sku"] == "0033"
    assert sku_profit_shopee.resolve_product("40481686607")["seller_sku"] == "0033"
    assert sku_profit_shopee.resolve_product("9999999999990021") is None
