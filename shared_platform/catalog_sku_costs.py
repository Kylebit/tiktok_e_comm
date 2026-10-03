"""Current cost per proven SKU entity; immutable legacy sources remain intact."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from shared_platform.catalog_cost_projection import amount, digest, _table, _owners, _key, CatalogProjectionError
from shared_platform.internal_catalog_sku import internal_sku,aliases_for

SCHEMA = '''
CREATE TABLE IF NOT EXISTS catalog_sku_costs (
 entity_key TEXT PRIMARY KEY, amount TEXT NOT NULL, version INTEGER NOT NULL,
 source_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalog_sku_cost_history (
 entity_key TEXT NOT NULL, version INTEGER NOT NULL, amount TEXT NOT NULL,
 source_json TEXT NOT NULL, PRIMARY KEY(entity_key,version));
'''


def members(conn,sku=None):
    rows=[]
    aliases=aliases_for(sku) if sku is not None else []
    clause=" AND trim(p.seller_sku,' '||char(9)||char(13)||char(10)) IN ("+','.join('?' for _ in aliases)+')' if aliases else ''
    shop_columns = {row['name'] for row in conn.execute('PRAGMA table_info(shops)')}
    region = "COALESCE(s.region,'')" if 'region' in shop_columns else "''"
    for r in conn.execute("SELECT p.*,"+region+" AS catalog_region FROM products p LEFT JOIN shops s ON s.cipher=p.shop_cipher WHERE 1=1"+clause,aliases):
        r=dict(r);i=dict(platform='tiktok',shop_key=str(r['shop_cipher']),product_id=str(r['product_id']),variant_id=str(r['sku_id']),seller_sku=r['seller_sku'] or '')
        pair=[str(r.get('global_product_id') or ''),str(r.get('global_sku_id') or '')]
        old_key='tiktok-global:'+digest(pair) if all(pair) else 'unbound:'+digest({k:i[k] for k in ('platform','shop_key','product_id','variant_id')})
        code=internal_sku(i['seller_sku']);key='internal:'+digest(code) if code else 'unbound:'+digest({k:i[k] for k in ('platform','shop_key','product_id','variant_id')})
        rows.append(dict(identity=i,entity_key=key,legacy_entity_key=old_key,internal_sku=code,binding='INTERNAL_SKU' if code else 'MISSING_SKU',name=r.get('product_name') or '',spec=r.get('sku_name') or '',image_url=r.get('image_url') or '',region=r['catalog_region']))
    clause=" WHERE trim(seller_sku,' '||char(9)||char(13)||char(10)) IN ("+','.join('?' for _ in aliases)+')' if aliases else ''
    for r in conn.execute('SELECT * FROM shopee_products'+clause,aliases):
        r=dict(r);i=dict(platform='shopee',shop_key=str(r['shop_id']),product_id=str(r['item_id']),variant_id=str(r['model_id']),seller_sku=r['seller_sku'] or '')
        old_key='unbound:'+digest({k:i[k] for k in ('platform','shop_key','product_id','variant_id')});code=internal_sku(i['seller_sku'])
        rows.append(dict(identity=i,entity_key='internal:'+digest(code) if code else old_key,legacy_entity_key=old_key,internal_sku=code,binding='INTERNAL_SKU' if code else 'MISSING_SKU',name=r.get('product_name') or '',spec=r.get('model_name') or '',image_url=r.get('image_url') or '',region=r.get('region') or ''))
    if _table(conn,'catalog_ozon_products'):
        from shared_platform.catalog_ozon import identity
        for r in conn.execute('SELECT * FROM catalog_ozon_products'):
            i=identity(dict(platform='ozon',identity_kind='product_offer',shop_key=r['account_id'],product_id=r['product_id'],offer_id=r['offer_id'],seller_sku=r['offer_id']))
            code=internal_sku(i['seller_sku'])
            if sku is not None and code!=sku:continue
            listing=json.loads(r['document_json']);images=listing.get('images') or []
            cost=conn.execute('SELECT * FROM catalog_ozon_costs WHERE account_id=? AND product_id=?',(i['shop_key'],i['product_id'])).fetchone() if _table(conn,'catalog_ozon_costs') else None
            old_key='ozon:'+digest(i)
            rows.append(dict(identity=i,entity_key='internal:'+digest(code),legacy_entity_key=old_key,internal_sku=code,binding='INTERNAL_SKU',name=listing.get('name') or '',spec='',image_url=images[0] if images else '',region='RU',ozon_cost=dict(cost) if cost else None))
    from shared_platform.catalog_archive import archived_members,identity_key
    current={identity_key(row['identity']) for row in rows}
    for archived in archived_members(conn,sku):
        if identity_key(archived['identity']) not in current:
            rows.append(archived)
    return rows


def grouped(conn):
    result={}
    for row in members(conn):result.setdefault(row['entity_key'],[]).append(row)
    return result


def source_cost(conn,member):
    i=member['identity']
    if i['platform']=='internal':return member.get('archived_cost')
    if member.get('legacy_entity_key') and _table(conn,'catalog_sku_costs'):
        old=conn.execute('SELECT * FROM catalog_sku_costs WHERE entity_key=?',(member['legacy_entity_key'],)).fetchone()
        if old:return {'identity':i,'amount':amount(old['amount']),'source':'PRESERVED_ENTITY_MANUAL','source_ref':member['legacy_entity_key'],'version':old['version']}
    if i['platform']=='ozon':
        r=member.get('ozon_cost');return {'identity':i,'amount':r['amount'],'source':'OZON_SCOPED','version':r['version']} if r else None
    if _table(conn,'catalog_identity_costs'):
        r=conn.execute('SELECT * FROM catalog_identity_costs WHERE platform=? AND shop_key=? AND product_id=? AND variant_id=?',_key(i)).fetchone()
        if r:
            if r['seller_sku']!=i['seller_sku']:raise CatalogProjectionError('catalog_identity_drift')
            return {'identity':i,'amount':amount(r['amount']),'source':r['source_kind'],'version':r['version']}
    if _table(conn,'sku_costs') and _owners(conn,i['variant_id'])=={_key(i)}:
        # A legacy row is an observation, not an approved current SKU cost.
        # Inherit only a single undated, positive CNY observation. In
        # particular, fetchone() would silently choose one of conflicting
        # invoices and erase the conflict in the current catalog view.
        observed=conn.execute('SELECT * FROM sku_costs WHERE sku_id=?',(i['variant_id'],)).fetchall()
        if len(observed)==1:
            r=observed[0]
            if ((not 'currency' in r.keys() or r['currency'] in (None,'CNY'))
                    and all(field not in r.keys() or r[field] is None for field in ('valid_from','valid_to'))):
                try:
                    value=amount(r['cost_cny'])
                except CatalogProjectionError:
                    value=None
                if value is not None:
                    return {'identity':i,'amount':value,'source':'LEGACY_UNIQUE_IDENTITY','version':0}
    return member.get('archived_cost')


def state(conn,key,rows):
    sources=sorted([v for r in rows if (v:=source_cost(conn,r))],key=lambda r:digest(r['identity']))
    current=conn.execute('SELECT * FROM catalog_sku_costs WHERE entity_key=?',(key,)).fetchone() if _table(conn,'catalog_sku_costs') else None
    from decimal import Decimal
    values=sorted({format(Decimal(r['amount']).normalize(),'f') for r in sources},key=Decimal)
    value=current['amount'] if current else values[0] if len(values)==1 else None
    version=current['version'] if current else max((r['version'] for r in sources),default=0)
    revision=digest({'members':[r['identity'] for r in sorted(rows,key=lambda r:digest(r['identity']))],'sources':sources,'current':dict(current) if current else None})
    return dict(amount=value,version=version,revision=revision,status='SAVED' if current else 'CONFLICT' if len(values)>1 else 'INHERITED' if values else 'MISSING',choices=values,sources=sources)


def read_current(conn,identity):
    key=entity_for_identity(conn,identity)
    current=conn.execute('SELECT * FROM catalog_sku_costs WHERE entity_key=?',(key,)).fetchone() if key and _table(conn,'catalog_sku_costs') else None
    if current:return {**identity,'amount':current['amount'],'version':current['version'],'source_kind':'MANUAL','source_ref':'canonical:'+key}
    if key:
        i=identity;code=internal_sku(i['seller_sku'])
        rows=[r for r in members(conn,code if code else None) if r['entity_key']==key]
        resolved=state(conn,key,rows)
        if resolved['status']=='CONFLICT':return {**i,**resolved,'source_kind':'CONFLICT','source_ref':'canonical-conflict:'+key}
        if resolved['amount'] is not None:return {**i,**resolved,'source_kind':'INHERITED','source_ref':'canonical-inherited:'+key}
    return None


def entity_for_identity(conn,i):
    if i['platform']=='ozon':
        if not _table(conn,'catalog_ozon_products'):return None
        row=conn.execute('SELECT offer_id FROM catalog_ozon_products WHERE account_id=? AND product_id=?',(i['shop_key'],i['product_id'])).fetchone()
        return 'internal:'+digest(internal_sku(i['seller_sku'])) if row and row['offer_id']==i['offer_id'] else None
    if i['platform']=='tiktok':
        r=conn.execute('SELECT seller_sku FROM products WHERE shop_cipher=? AND product_id=? AND sku_id=?',(i['shop_key'],i['product_id'],i['variant_id'])).fetchone()
        if not r or (r['seller_sku'] or '')!=i['seller_sku']:return None
    else:
        r=conn.execute('SELECT seller_sku FROM shopee_products WHERE shop_id=? AND item_id=? AND model_id=?',(i['shop_key'],i['product_id'],i['variant_id'])).fetchone()
        if not r or (r['seller_sku'] or '')!=i['seller_sku']:return None
    code=internal_sku(i['seller_sku'])
    return 'internal:'+digest(code) if code else 'unbound:'+digest({k:i[k] for k in ('platform','shop_key','product_id','variant_id')})


def save(path,key,value,revision):
    value=amount(value)
    if not isinstance(key,str) or not isinstance(revision,str):raise CatalogProjectionError('sku_revision_required')
    if not Path(path).is_file():raise CatalogProjectionError('catalog_database_missing')
    conn=sqlite3.connect(path,timeout=30);conn.row_factory=sqlite3.Row
    try:
        conn.executescript(SCHEMA);conn.execute('BEGIN IMMEDIATE')
        rows=grouped(conn).get(key)
        if not rows:raise CatalogProjectionError('sku_entity_not_found')
        before=state(conn,key,rows)
        if before['revision']!=revision:raise CatalogProjectionError('sku_cost_revision_conflict')
        version=before['version']+1
        evidence=json.dumps({'previous':before,'members':[r['identity'] for r in rows]},ensure_ascii=False,sort_keys=True)
        conn.execute('INSERT INTO catalog_sku_costs VALUES(?,?,?,?) ON CONFLICT(entity_key) DO UPDATE SET amount=excluded.amount,version=excluded.version,source_json=excluded.source_json',(key,value,version,evidence))
        conn.execute('INSERT INTO catalog_sku_cost_history VALUES(?,?,?,?)',(key,version,value,evidence))
        after=state(conn,key,rows);conn.commit()
        return {'entity_key':key,**after,'consumer_count':len(rows)}
    except BaseException:conn.rollback();raise
    finally:conn.close()


def save_existing_identity(path,i,value,expected_version):
    conn=sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    try:
        current=read_current(conn,i)
        if current is None:return None
        if current['version']!=expected_version:raise CatalogProjectionError('cost_version_conflict')
        key=entity_for_identity(conn,i);rows=grouped(conn).get(key)
        if not rows:raise CatalogProjectionError('sku_entity_not_found')
        revision=state(conn,key,rows)['revision']
    finally:conn.close()
    result=save(path,key,value,revision)
    return {**result,'identity':i,'status':'SAVED','compatibility':'CANONICAL_SKU_CURRENT_COST'}
