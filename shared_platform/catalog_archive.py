"""Durable catalog membership, independent of replaceable channel caches."""
import json
import sqlite3
from pathlib import Path
from shared_platform.catalog_cost_projection import digest, CatalogProjectionError, _table
from shared_platform.internal_catalog_sku import internal_sku

SCHEMA = '''CREATE TABLE IF NOT EXISTS catalog_archive_members (
 identity_key TEXT PRIMARY KEY, internal_sku TEXT NOT NULL,
 document_json TEXT NOT NULL, evidence_ref TEXT NOT NULL,
 evidence_digest TEXT NOT NULL, captured_at TEXT NOT NULL);'''

def weight_grams(row):
    from decimal import Decimal,InvalidOperation
    source=row.get('weight_source')
    if not isinstance(source,dict) or isinstance(source.get('value'),bool):return None
    if (source.get('field'),source.get('unit')) not in {('weight_kg','kg'),('weight_g','g')}:return None
    try:
        value=Decimal(str(source.get('value')))
        if not value.is_finite() or value<=0:return None
        return value*1000 if source['unit']=='kg' else value
    except (InvalidOperation,ValueError,TypeError):return None

def identity_key(identity):
    return digest({**{k:identity[k] for k in ('platform','shop_key','product_id')},
                   'variant_id':identity.get('variant_id',identity.get('offer_id',''))})

def archived_members(conn, sku=None):
    if not _table(conn,'catalog_archive_members'):
        return []
    query='SELECT document_json FROM catalog_archive_members'
    args=()
    if sku is not None:query+=' WHERE internal_sku=?';args=(sku,)
    return [json.loads(row[0]) for row in conn.execute(query,args)]

def import_members(path, records, *, expected_digest, evidence_ref, captured_at):
    """Import an exact frozen local evidence packet; never updates channel rows."""
    if digest(records)!=expected_digest or not evidence_ref or not captured_at:
        raise CatalogProjectionError('archive_evidence_binding_required')
    if not isinstance(records,list) or not records or len(records)>10000:
        raise CatalogProjectionError('archive_records_invalid')
    validated={}
    for row in records:
        i=row.get('identity') or {}
        if set(i)!={'platform','shop_key','product_id','variant_id','seller_sku'}:
            raise CatalogProjectionError('archive_identity_invalid')
        if i['platform'] not in {'tiktok','shopee','internal'} or any(not isinstance(v,str) or (not v and k!='seller_sku') for k,v in i.items()):
            raise CatalogProjectionError('archive_identity_invalid')
        code=internal_sku(i['seller_sku'])
        expected_key='internal:'+digest(code) if code else 'unbound:'+identity_key(i)
        if code!=row.get('internal_sku') or row.get('entity_key')!=expected_key:
            raise CatalogProjectionError('archive_sku_mismatch')
        if row.get('binding')!=('INTERNAL_SKU' if code else 'MISSING_SKU') or row.get('evidence_kind') not in {'OFFICIAL_PRODUCT_READBACK','PRESERVED_LOCAL_CATALOG','APPROVED_INTERNAL_PRODUCT'}:
            raise CatalogProjectionError('archive_evidence_kind_invalid')
        if not row.get('observed_at') or not row.get('name'):
            raise CatalogProjectionError('archive_facts_missing')
        if any(not isinstance(row.get(k),str) for k in ('name','spec','image_url','region','observed_at')):
            raise CatalogProjectionError('archive_facts_invalid')
        from datetime import datetime
        try:
            observed=datetime.fromisoformat(row['observed_at'].replace('Z','+00:00'))
            if observed.tzinfo is None:raise ValueError('timezone_required')
        except (ValueError,TypeError):raise CatalogProjectionError('archive_observed_at_invalid')
        if row.get('archived_cost') is not None:
            from shared_platform.catalog_cost_projection import amount
            cost=row['archived_cost']
            if not isinstance(cost,dict) or cost.get('identity')!=i or type(cost.get('version')) is not int or cost['version']<0 or not cost.get('source'):
                raise CatalogProjectionError('archive_cost_evidence_invalid')
            amount(cost.get('amount'))
        key=identity_key(i)
        if key in validated and validated[key]!=row:
            raise CatalogProjectionError('archive_ambiguous_identity')
        validated[key]=row
    if not Path(path).is_file():raise CatalogProjectionError('catalog_database_missing')
    conn=sqlite3.connect(path,timeout=30)
    try:
        conn.execute('BEGIN IMMEDIATE');conn.execute(SCHEMA)
        inserted=0
        for key,row in validated.items():
            old=conn.execute('SELECT document_json FROM catalog_archive_members WHERE identity_key=?',(key,)).fetchone()
            if old:
                if json.loads(old[0])!=row:raise CatalogProjectionError('archive_identity_conflict')
                continue
            conn.execute('INSERT INTO catalog_archive_members VALUES(?,?,?,?,?,?)',
                (key,row['internal_sku'],json.dumps(row,ensure_ascii=False,sort_keys=True),evidence_ref,expected_digest,captured_at))
            inserted+=1
        conn.commit()
        return {'inserted':inserted,'records':len(validated),'internal_skus':len({r['internal_sku'] for r in validated.values() if r['internal_sku']}),'evidence_digest':expected_digest}
    except BaseException:conn.rollback();raise
    finally:conn.close()
