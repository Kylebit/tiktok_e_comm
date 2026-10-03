"""Exact catalog identity costs and atomic local publication projections.

This module has no provider client. Unknown identities cannot become catalog
rows. Frozen publication facts are inputs, never mutable destinations.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from pathlib import Path

KEYS = ('platform', 'shop_key', 'product_id', 'variant_id')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS catalog_identity_costs (
 platform TEXT NOT NULL, shop_key TEXT NOT NULL, product_id TEXT NOT NULL,
 variant_id TEXT NOT NULL, seller_sku TEXT NOT NULL, amount TEXT NOT NULL,
 version INTEGER NOT NULL, source_kind TEXT NOT NULL, source_ref TEXT NOT NULL,
 PRIMARY KEY(platform,shop_key,product_id,variant_id));
CREATE TABLE IF NOT EXISTS catalog_projection_receipts (
 receipt_id TEXT PRIMARY KEY, input_digest TEXT NOT NULL, result_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalog_official_observations (
 platform TEXT NOT NULL, shop_key TEXT NOT NULL, product_id TEXT NOT NULL,
 variant_id TEXT NOT NULL, seller_sku TEXT NOT NULL, document_json TEXT NOT NULL,
 source_ref TEXT NOT NULL, PRIMARY KEY(platform,shop_key,product_id,variant_id));
'''


class CatalogProjectionError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identity(value):
    if not isinstance(value, dict):
        raise CatalogProjectionError('full_identity_required')
    result = {k: value.get(k) for k in (*KEYS, 'seller_sku')}
    if result['platform'] not in {'tiktok', 'shopee', 'ozon'}:
        raise CatalogProjectionError('platform_required')
    if any(type(v) is not str or not v or v != v.strip() or any(ord(c) < 32 for c in v) for v in result.values()):
        raise CatalogProjectionError('full_identity_required')
    if result['variant_id'].startswith('item_'):
        raise CatalogProjectionError('official_variant_required')
    return result


def amount(value):
    try:
        n = Decimal(str(value))
        if isinstance(value, bool) or not n.is_finite() or n <= 0:
            raise ValueError()
        return format(n, 'f')
    except (InvalidOperation, ValueError):
        raise CatalogProjectionError('positive_cost_required') from None


def _key(i):
    return tuple(i[k] for k in KEYS)


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def read_cost(conn, raw):
    i = identity(raw)
    from shared_platform.catalog_sku_costs import read_current
    current=read_current(conn,i)
    if current is not None:return current
    if not _table(conn, 'catalog_identity_costs'):
        return None
    row = conn.execute('SELECT * FROM catalog_identity_costs WHERE platform=? AND shop_key=? AND product_id=? AND variant_id=?', _key(i)).fetchone()
    if row is None:
        return None
    result = dict(row)
    if result['seller_sku'] != i['seller_sku']:
        raise CatalogProjectionError('catalog_identity_drift')
    if type(result['version']) is not int or result['version'] < 1:
        raise CatalogProjectionError('catalog_cost_version_invalid')
    amount(result['amount'])
    return result


def _owners(conn, variant):
    owners = set()
    if _table(conn, 'products'):
        owners.update(('tiktok', str(r[0]), str(r[1]), str(variant)) for r in conn.execute('SELECT shop_cipher,product_id FROM products WHERE sku_id=?', (variant,)))
    if _table(conn, 'shopee_products'):
        owners.update(('shopee', str(r[0]), str(r[1]), str(variant)) for r in conn.execute('SELECT shop_id,item_id FROM shopee_products WHERE model_id=?', (variant,)))
    if _table(conn, 'catalog_official_observations'):
        owners.update(tuple(r) for r in conn.execute('SELECT platform,shop_key,product_id,variant_id FROM catalog_official_observations WHERE variant_id=?', (variant,)))
    return owners


def legacy_cost(conn, raw):
    i = identity(raw)
    if not _table(conn, 'sku_costs') or _owners(conn, i['variant_id']) != {_key(i)}:
        return None
    row = conn.execute('SELECT cost_cny FROM sku_costs WHERE sku_id=?', (i['variant_id'],)).fetchone()
    return amount(row[0]) if row else None


def _compat(conn, i, value):
    if _owners(conn, i['variant_id']) != {_key(i)}:
        return 'AMBIGUOUS_LEGACY_KEY_NOT_PROJECTED'
    conn.execute('INSERT INTO sku_costs(sku_id,cost_cny,note,updated_at) VALUES(?,?,?,0) ON CONFLICT(sku_id) DO UPDATE SET cost_cny=excluded.cost_cny,note=excluded.note',
                 (i['variant_id'], value, 'scoped:' + digest(i)))
    return 'PROJECTED_UNIQUE_IDENTITY'


def _connect(path):
    path = Path(path)
    if not path.is_file():
        raise CatalogProjectionError('catalog_database_missing')
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute('BEGIN IMMEDIATE')
    return conn


def _upsert_listing(conn, i, row):
    """Write only observed fields; never invent stock, price or official IDs."""
    observed = row.get('listing')
    if not isinstance(observed, dict):
        raise CatalogProjectionError('official_listing_fields_missing')
    if i['platform'] == 'tiktok':
        table = 'products';key_columns = ('sku_id', 'shop_cipher')
        data = {'sku_id': i['variant_id'], 'shop_cipher': i['shop_key'], 'product_id': i['product_id'], 'seller_sku': i['seller_sku']}
        names = {'name':'product_name','variant_name':'sku_name','image_url':'image_url','price':'price','currency':'currency','stock':'stock','status':'status'}
    elif i['platform'] == 'shopee':
        table = 'shopee_products';key_columns = ('model_id', 'shop_id')
        data = {'model_id': i['variant_id'], 'shop_id': i['shop_key'], 'item_id': i['product_id'], 'seller_sku': i['seller_sku']}
        names = {'name':'product_name','variant_name':'model_name','image_url':'image_url','price':'price','currency':'currency','stock':'stock','status':'status','region':'region'}
    else:
        raise CatalogProjectionError('ozon_variant_binding_not_connected')
    for source, target in names.items():
        if source in observed and observed[source] not in (None,''):
            data[target] = observed[source]
    existing = conn.execute(f'SELECT * FROM {table} WHERE {key_columns[0]}=? AND {key_columns[1]}=?', (i['variant_id'], i['shop_key'])).fetchone()
    product_field = 'product_id' if i['platform'] == 'tiktok' else 'item_id'
    if existing and (str(existing[product_field]) != i['product_id'] or existing['seller_sku'] != i['seller_sku']):
        raise CatalogProjectionError('existing_official_identity_conflict')
    if i['platform'] == 'shopee' and 'region' in observed:
        conn.execute('INSERT INTO shopee_shops(shop_id,region) VALUES(?,?) ON CONFLICT(shop_id) DO NOTHING', (i['shop_key'], observed['region']))
    columns = list(data)
    updates = ','.join(f'{c}=excluded.{c}' for c in columns if c not in key_columns)
    conn.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) ON CONFLICT({','.join(key_columns)}) DO UPDATE SET {updates}", tuple(data.values()))


def save_manual(path, raw, value, expected_version):
    i = identity(raw);value = amount(value)
    if type(expected_version) is not int or expected_version < 0:
        raise CatalogProjectionError('cost_version_required')
    from shared_platform.catalog_sku_costs import save_existing_identity
    shared=save_existing_identity(path,i,value,expected_version)
    if shared is not None:return shared
    conn = _connect(path)
    try:
        if _key(i) not in _owners(conn, i['variant_id']):
            raise CatalogProjectionError('catalog_identity_not_found')
        table, shop, variant, product = ('products','shop_cipher','sku_id','product_id') if i['platform']=='tiktok' else ('shopee_products','shop_id','model_id','item_id')
        if i['platform'] not in {'tiktok','shopee'}:
            raise CatalogProjectionError('catalog_identity_not_supported')
        listing=conn.execute(f'SELECT seller_sku,{product} FROM {table} WHERE {shop}=? AND {variant}=?',(i['shop_key'],i['variant_id'])).fetchone()
        if not listing or listing['seller_sku']!=i['seller_sku'] or str(listing[product])!=i['product_id']:
            raise CatalogProjectionError('catalog_identity_drift')
        old = read_cost(conn, i)
        version = old['version'] if old else 0
        if version != expected_version:
            raise CatalogProjectionError('cost_version_conflict')
        conn.execute('INSERT INTO catalog_identity_costs VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(platform,shop_key,product_id,variant_id) DO UPDATE SET amount=excluded.amount,version=excluded.version,source_kind=excluded.source_kind,source_ref=excluded.source_ref',
                     (*_key(i), i['seller_sku'], value, version + 1, 'MANUAL', 'catalog-edit:' + str(version + 1)))
        compatibility = _compat(conn, i, value)
        conn.commit()
        return {'status': 'SAVED', 'identity': i, 'amount': value, 'version': version + 1, 'compatibility': compatibility}
    except BaseException:
        conn.rollback();raise
    finally:
        conn.close()


def project(path, packet, *, fault=None):
    """Consume a durable server-owned observation; no network or dispatch.

    First/late binding never overwrites an existing cost. A later approved
    version requires an explicitly persisted baseline, never a fresh readback
    timestamp. Receipt and all eligible rows commit together.
    """
    if isinstance(packet,dict) and packet.get('schema')=='catalog-ozon-observation/v1':
        from shared_platform.catalog_ozon import project as project_ozon
        return project_ozon(path,packet,fault=fault)
    if not isinstance(packet, dict) or packet.get('schema') != 'catalog-publication-observation/v1':
        raise CatalogProjectionError('observation_schema_required')
    reference = packet.get('snapshot_digest')
    if type(reference) is not str or len(reference.removeprefix('sha256:')) != 64:
        raise CatalogProjectionError('frozen_snapshot_reference_required')
    rows = packet.get('rows')
    if not isinstance(rows, list) or not rows:
        raise CatalogProjectionError('observation_rows_required')
    input_digest = digest(packet);receipt_id = packet.get('receipt_id')
    if type(receipt_id) is not str or not receipt_id:
        raise CatalogProjectionError('receipt_identity_required')
    conn = _connect(path)
    try:
        old = conn.execute('SELECT * FROM catalog_projection_receipts WHERE receipt_id=?', (receipt_id,)).fetchone()
        if old:
            if old['input_digest'] != input_digest:
                raise CatalogProjectionError('receipt_identity_conflict')
            conn.rollback();return {**json.loads(old['result_json']), 'replayed': True}
        results=[];seen=set()
        for row in rows:
            try:
                if row.get('authority') != 'OFFICIAL' or row.get('verified') is not True:
                    raise CatalogProjectionError('official_readback_unverified')
                i=identity(row['identity']);key=_key(i)
                if key in seen:raise CatalogProjectionError('duplicate_official_identity')
                seen.add(key);value=amount(row.get('approved_cost'))
                if not row.get('variant_key'):raise CatalogProjectionError('approved_variant_binding_missing')
            except (KeyError,CatalogProjectionError) as error:
                results.append({'status':'UNBOUND','code':str(error)});continue
            try:
                _upsert_listing(conn,i,row)
            except CatalogProjectionError as error:
                results.append({'identity':i,'status':'UNBOUND','code':str(error)});continue
            prior=read_cost(conn,i)
            legacy=legacy_cost(conn,i)
            baseline=row.get('expected_cost_version')
            # Missing baseline on first official binding is intentionally
            # conservative: preserve any already recorded cost.
            conflict=(prior is not None and (baseline is None or prior['version']!=baseline or prior['source_kind'] in {'MANUAL','CONFLICT','INHERITED'})) or (prior is None and legacy is not None)
            observation={k:v for k,v in row.items() if k not in {'approved_cost','expected_cost_version'}}
            conn.execute('INSERT INTO catalog_official_observations VALUES(?,?,?,?,?,?,?) ON CONFLICT(platform,shop_key,product_id,variant_id) DO UPDATE SET document_json=excluded.document_json,source_ref=excluded.source_ref',(*key,i['seller_sku'],canonical(observation),reference))
            if fault:fault('after_observation')
            if conflict:
                if prior is None:
                    # Carry forward the uniquely owned legacy amount, never the
                    # publication amount, so actual identity consumers agree.
                    conn.execute('INSERT INTO catalog_identity_costs VALUES(?,?,?,?,?,?,?,?,?)',(*key,i['seller_sku'],legacy,1,'MANUAL','preserved-unique-legacy:'+digest(i)))
                    if fault:fault('after_cost')
                results.append({'identity':i,'status':'COST_CONFLICT_PRESERVED','version':prior['version'] if prior else 1});continue
            version=prior['version']+1 if prior else 1
            conn.execute('INSERT INTO catalog_identity_costs VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(platform,shop_key,product_id,variant_id) DO UPDATE SET amount=excluded.amount,version=excluded.version,source_kind=excluded.source_kind,source_ref=excluded.source_ref',(*key,i['seller_sku'],value,version,'PUBLICATION',reference))
            if fault:fault('after_cost')
            results.append({'identity':i,'status':'PROJECTED','version':version,'compatibility':_compat(conn,i,value)})
        result={'receipt_id':receipt_id,'status':'COMPLETE' if all(r['status']=='PROJECTED' for r in results) else 'NEEDS_REVIEW','rows':results,'replayed':False,'external_write_count':0}
        if fault:fault('before_receipt')
        conn.execute('INSERT INTO catalog_projection_receipts VALUES(?,?,?)',(receipt_id,input_digest,canonical(result)));conn.commit();return result
    except BaseException:
        conn.rollback();raise
    finally:
        conn.close()
