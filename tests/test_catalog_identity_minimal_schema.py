import sqlite3

import pytest

from shared_platform.catalog_sku_costs import digest, entity_for_identity


@pytest.mark.parametrize('changed_field', [None, 'shop_key', 'product_id', 'variant_id', 'seller_sku'])
def test_tiktok_identity_read_needs_only_identity_columns(changed_field):
    """Optional global linkage columns are not prerequisites for exact identity."""
    with sqlite3.connect(':memory:') as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE products(shop_cipher TEXT, product_id TEXT, sku_id TEXT, seller_sku TEXT)')
        conn.execute("INSERT INTO products VALUES ('shop-1', 'product-1', 'variant-1', '660018')")
        conn.commit()
        conn.execute('PRAGMA query_only=ON')
        before = conn.total_changes
        identity = dict(platform='tiktok', shop_key='shop-1', product_id='product-1',
                        variant_id='variant-1', seller_sku='660018')
        if changed_field:
            identity[changed_field] = 'other'
        result = entity_for_identity(conn, identity)
        assert result == (None if changed_field else 'internal:' + digest('0018'))
        assert conn.total_changes == before
