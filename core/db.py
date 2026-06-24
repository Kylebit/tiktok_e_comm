"""SQLite 本地库：商品、成本、结算、广告消耗、联盟记录。"""

import sqlite3
from pathlib import Path

from core.config import ROOT, get

SCHEMA = """
CREATE TABLE IF NOT EXISTS shops (
    cipher TEXT PRIMARY KEY,
    shop_id TEXT,
    name TEXT,
    region TEXT,
    seller_type TEXT,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS products (
    sku_id TEXT NOT NULL,
    shop_cipher TEXT NOT NULL,
    product_id TEXT,
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

CREATE TABLE IF NOT EXISTS sku_costs (
    sku_id TEXT PRIMARY KEY,
    cost_cny REAL NOT NULL,
    note TEXT,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS settlement_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_cipher TEXT,
    region TEXT,
    statement_id TEXT,
    statement_date TEXT,
    line_type TEXT,
    order_id TEXT,
    sku_id TEXT,
    quantity REAL,
    currency TEXT,
    settlement_amount REAL,
    revenue REAL,
    subtotal_after_discount REAL,
    total_fees REAL,
    raw_json TEXT,
    synced_at INTEGER
);

CREATE TABLE IF NOT EXISTS ad_spend_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_region TEXT,
    spend_date TEXT,
    spend_local REAL,
    currency TEXT,
    spend_cny REAL,
    source TEXT DEFAULT 'ads_api',
    raw_json TEXT,
    synced_at INTEGER,
    UNIQUE(shop_region, spend_date, source)
);

CREATE TABLE IF NOT EXISTS affiliate_invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_cipher TEXT,
    product_id TEXT,
    sku_id TEXT,
    creator_id TEXT,
    commission_rate REAL,
    collaboration_id TEXT,
    status TEXT,
    created_at INTEGER,
    raw_json TEXT
);
"""


def db_path() -> Path:
    rel = get("database", "data/shop.db")
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
    for name, ddl in (
        ("global_product_id", "ALTER TABLE products ADD COLUMN global_product_id TEXT"),
        ("global_sku_id", "ALTER TABLE products ADD COLUMN global_sku_id TEXT"),
    ):
        if name not in cols:
            conn.execute(ddl)

    conn.executescript("""
CREATE TABLE IF NOT EXISTS shopee_shops (
    shop_id INTEGER PRIMARY KEY,
    region TEXT,
    shop_name TEXT,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS shopee_products (
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
CREATE INDEX IF NOT EXISTS idx_shopee_products_sku ON shopee_products(seller_sku);
CREATE INDEX IF NOT EXISTS idx_shopee_products_region ON shopee_products(region);

CREATE TABLE IF NOT EXISTS sku_logistics_weights (
    seller_sku TEXT PRIMARY KEY,
    weight_g INTEGER NOT NULL,
    package_count INTEGER NOT NULL DEFAULT 0,
    depth_mm INTEGER,
    width_mm INTEGER,
    height_mm INTEGER,
    updated_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sku_logistics_weights_updated ON sku_logistics_weights(updated_at);
""")


def init_db() -> Path:
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()
    return db_path()
