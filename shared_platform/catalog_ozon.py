"""Typed Ozon product/offer identities and current costs; no synthetic variant."""
import json
import sqlite3
from pathlib import Path
from shared_platform.catalog_cost_projection import SCHEMA as COMMON_SCHEMA, canonical, digest, amount, _table

SCHEMA='''
CREATE TABLE IF NOT EXISTS catalog_ozon_products (
 account_id TEXT NOT NULL, product_id TEXT NOT NULL, offer_id TEXT NOT NULL,
 document_json TEXT NOT NULL, source_ref TEXT NOT NULL,
 PRIMARY KEY(account_id,product_id), UNIQUE(account_id,offer_id));
CREATE TABLE IF NOT EXISTS catalog_ozon_costs (
 account_id TEXT NOT NULL, product_id TEXT NOT NULL, offer_id TEXT NOT NULL,
 amount TEXT NOT NULL, version INTEGER NOT NULL, source_kind TEXT NOT NULL, source_ref TEXT NOT NULL,
 PRIMARY KEY(account_id,product_id), UNIQUE(account_id,offer_id));
'''


def identity(raw):
    if not isinstance(raw,dict) or raw.get('platform')!='ozon' or raw.get('identity_kind')!='product_offer':
        raise ValueError('ozon_product_offer_identity_required')
    if raw.get('variant_id') is not None:raise ValueError('ozon_does_not_invent_variant_id')
    result={k:raw.get(k) for k in ('platform','identity_kind','shop_key','product_id','offer_id','seller_sku')}
    if any(type(v) is not str or not v or v!=v.strip() or any(ord(c)<32 for c in v) for v in result.values()):
        raise ValueError('ozon_exact_identity_required')
    if any(not result[k].isdecimal() or int(result[k])<=0 for k in ('shop_key','product_id')):
        raise ValueError('ozon_official_identity_required')
    if result['seller_sku']!=result['offer_id']:raise ValueError('ozon_offer_binding_conflict')
    return result


def _key(i):return (i['shop_key'],i['product_id'])


def _connect(path):
    if not Path(path).is_file():raise ValueError('catalog_database_missing')
    connection=sqlite3.connect(path,timeout=30);connection.row_factory=sqlite3.Row
    connection.executescript(COMMON_SCHEMA+SCHEMA);connection.execute('BEGIN IMMEDIATE')
    return connection


def read_cost(connection,raw):
    i=identity(raw)
    from shared_platform.catalog_sku_costs import read_current
    current=read_current(connection,i)
    if current is not None:return current
    if not _table(connection,'catalog_ozon_costs'):return None
    row=connection.execute('SELECT * FROM catalog_ozon_costs WHERE account_id=? AND product_id=?',_key(i)).fetchone()
    if row is None:return None
    row=dict(row)
    if row['offer_id']!=i['offer_id'] or type(row['version']) is not int or row['version']<1:raise ValueError('ozon_cost_identity_conflict')
    amount(row['amount']);return row


def save_manual(path,raw,value,expected_version):
    i=identity(raw);value=amount(value)
    if type(expected_version) is not int or expected_version<0:raise ValueError('cost_version_required')
    from shared_platform.catalog_sku_costs import save_existing_identity
    shared=save_existing_identity(path,i,value,expected_version)
    if shared is not None:return shared
    connection=_connect(path)
    try:
        row=connection.execute('SELECT offer_id FROM catalog_ozon_products WHERE account_id=? AND product_id=?',_key(i)).fetchone()
        if row is None or row['offer_id']!=i['offer_id']:raise ValueError('ozon_catalog_identity_not_found')
        old=read_cost(connection,i);version=old['version'] if old else 0
        if version!=expected_version:raise ValueError('cost_version_conflict')
        connection.execute('INSERT INTO catalog_ozon_costs VALUES(?,?,?,?,?,?,?) ON CONFLICT(account_id,product_id) DO UPDATE SET amount=excluded.amount,version=excluded.version,source_kind=excluded.source_kind,source_ref=excluded.source_ref',(*_key(i),i['offer_id'],value,version+1,'MANUAL','catalog-edit:'+str(version+1)))
        connection.commit();return {'status':'SAVED','identity':i,'amount':value,'version':version+1,'compatibility':'TYPED_OZON_NO_LEGACY_BROADCAST'}
    except BaseException:connection.rollback();raise
    finally:connection.close()


def project(path,packet,*,fault=None):
    if packet.get('schema')!='catalog-ozon-observation/v1' or not packet.get('rows'):raise ValueError('ozon_observation_required')
    snapshot_hash=packet.get('snapshot_digest')
    snapshot_hash=snapshot_hash.removeprefix('sha256:') if isinstance(snapshot_hash,str) else ''
    if not isinstance(packet.get('receipt_id'),str) or not packet['receipt_id'] or len(snapshot_hash)!=64 or any(c not in '0123456789abcdef' for c in snapshot_hash):raise ValueError('ozon_frozen_reference_required')
    connection=_connect(path);receipt=packet['receipt_id'];input_digest=digest(packet)
    try:
        old=connection.execute('SELECT * FROM catalog_projection_receipts WHERE receipt_id=?',(receipt,)).fetchone()
        if old:
            if old['input_digest']!=input_digest:raise ValueError('receipt_identity_conflict')
            connection.rollback();return {**json.loads(old['result_json']),'replayed':True}
        prepared=[];seen=set();offers=set()
        for row in packet['rows']:
            if row.get('authority')!='OFFICIAL' or row.get('verified') is not True:raise ValueError('ozon_official_observation_unverified')
            i=identity(row['identity']);value=amount(row['approved_cost'])
            if not row.get('variant_key') or not isinstance(row.get('listing'),dict):raise ValueError('ozon_approved_binding_required')
            if _key(i) in seen or (i['shop_key'],i['offer_id']) in offers:raise ValueError('ozon_duplicate_identity')
            seen.add(_key(i));offers.add((i['shop_key'],i['offer_id']))
            existing=connection.execute('SELECT * FROM catalog_ozon_products WHERE account_id=? AND (product_id=? OR offer_id=?)',(*_key(i),i['offer_id'])).fetchall()
            if any(r['product_id']!=i['product_id'] or r['offer_id']!=i['offer_id'] for r in existing):raise ValueError('ozon_existing_identity_conflict')
            listing=json.loads(existing[0]['document_json']) if existing else {}
            listing.update({k:v for k,v in row['listing'].items() if v is not None and v!=''})
            prepared.append((i,value,listing))
        results=[]
        for i,value,listing in prepared:
            connection.execute('INSERT INTO catalog_ozon_products VALUES(?,?,?,?,?) ON CONFLICT(account_id,product_id) DO UPDATE SET document_json=excluded.document_json,source_ref=excluded.source_ref',(*_key(i),i['offer_id'],canonical(listing),packet['snapshot_digest']))
            if fault:fault('after_observation')
            old=read_cost(connection,i)
            if old:
                results.append({'identity':i,'status':'COST_CONFLICT_PRESERVED','version':old['version']})
            else:
                connection.execute('INSERT INTO catalog_ozon_costs VALUES(?,?,?,?,?,?,?)',(*_key(i),i['offer_id'],value,1,'PUBLICATION',packet['snapshot_digest']))
                if fault:fault('after_cost')
                results.append({'identity':i,'status':'PROJECTED','version':1})
        result={'receipt_id':receipt,'status':'COMPLETE' if all(r['status']=='PROJECTED' for r in results) else 'NEEDS_REVIEW','rows':results,'replayed':False,'external_write_count':0}
        if fault:fault('before_receipt')
        connection.execute('INSERT INTO catalog_projection_receipts VALUES(?,?,?)',(receipt,input_digest,canonical(result)))
        connection.commit();return result
    except BaseException:connection.rollback();raise
    finally:connection.close()


def catalog_rows(connection):
    if not _table(connection,'catalog_ozon_products'):return []
    rows=[]
    for raw in connection.execute('SELECT rowid AS _rowid,* FROM catalog_ozon_products ORDER BY account_id,product_id'):
        i=identity({'platform':'ozon','identity_kind':'product_offer','shop_key':raw['account_id'],'product_id':raw['product_id'],'offer_id':raw['offer_id'],'seller_sku':raw['offer_id']})
        listing=json.loads(raw['document_json']);cost=read_cost(connection,i)
        if not isinstance(listing,dict):raise ValueError('ozon_catalog_document_invalid')
        rows.append({'identity':i,'listing':listing,'cost':cost,'rowid':raw['_rowid'],'source_ref':raw['source_ref']})
    return rows
