"""Six-column directory with complete, evidence-based SKU entity coverage."""
from collections import defaultdict
from core.db import connect_readonly
from shared_platform.catalog_sku_costs import grouped,state
from shared_platform.catalog_cost_projection import _table,digest
from shared_platform.internal_catalog_sku import internal_sku


def list_skus(*,query='',region='',cost_status='',limit=50,offset=0):
    conn=connect_readonly()
    try:
        entities=grouped(conn);seller_entities=defaultdict(set)
        for key,rows in entities.items():
            for row in rows:
                if row['identity']['seller_sku']:seller_entities[row['identity']['seller_sku']].add(key)
        weights={r['seller_sku']:r['weight_g'] for r in conn.execute('SELECT seller_sku,weight_g FROM sku_logistics_weights')} if _table(conn,'sku_logistics_weights') else {}
        items=[];coverage={'source_identities':sum(map(len,entities.values())),'entities':len(entities),'internal_skus':0,'missing_sku_records':0,'conflicts':0,'missing_cost':0}
        for key,rows in entities.items():
            rows=sorted(rows,key=lambda r:(r['region']!='MY',r['identity']['seller_sku'],digest(r['identity'])))
            first=rows[0];aliases=sorted({r['identity']['seller_sku'] for r in rows if r['identity']['seller_sku']})
            cost=state(conn,key,rows);regions=sorted({r['region'] for r in rows if r['region']})
            coverage['internal_skus']+=first['binding']=='INTERNAL_SKU';coverage['missing_sku_records']+=first['binding']=='MISSING_SKU'
            coverage['conflicts']+=cost['status']=='CONFLICT';coverage['missing_cost']+=cost['status']=='MISSING'
            evidence_weights={weights[sku] for sku in aliases if len(seller_entities[sku])==1 and sku in weights}
            weight_source='LOGISTICS_RECORD' if evidence_weights else 'MISSING'
            if not evidence_weights:
                from shared_platform.catalog_archive import weight_grams
                evidence_weights={value for r in rows if (value:=weight_grams(r)) is not None}
                if evidence_weights:weight_source='ARCHIVED_EXPLICIT_UNIT'
            # Image/name/spec come from the same exact listing; never borrow a
            # visually similar image from a suffix-aligned or unrelated row.
            image_row=next((r for r in rows if r['image_url']),first)
            image_urls=list(dict.fromkeys(r['image_url'] for r in rows if r['image_url']))
            item=dict(entity_key=key,sku=first['internal_sku'],aliases=aliases,name=image_row['name'],spec=image_row['spec'],image_url=image_row['image_url'],image_key=digest(image_row['image_url']) if image_row['image_url'] else None,image_candidates=[{'key':digest(url),'url':url} for url in image_urls],binding=first['binding'],regions=regions,member_count=len(rows),cost=cost,weight_g=next(iter(evidence_weights)) if len(evidence_weights)==1 else None,weight_status='CONFLICT' if len(evidence_weights)>1 else 'KNOWN' if evidence_weights else 'MISSING')
            item['channel_evidence']=[{'platform':r['identity']['platform'],'product_status':r.get('product_status'),
                'variant_status':r.get('variant_status'),'observed_at':r.get('observed_at'),
                'evidence_kind':r.get('evidence_kind'),'shop_key':r['identity']['shop_key'],
                'product_id':r['identity']['product_id'],'variant_id':r['identity']['variant_id']}
                for r in rows if r.get('evidence_kind')]
            item['weight_source']=weight_source
            if item['weight_g'] is not None:item['weight_g']=float(item['weight_g'])
            q=internal_sku(query).casefold()
            if q and not any(q in str(value).casefold() for value in [item['sku'],*aliases,item['name'],item['spec']]):continue
            if region and region!='ALL' and region not in regions:continue
            if cost_status and cost_status!=cost['status']:continue
            items.append(item)
        items.sort(key=lambda r:(r['cost']['status']!='CONFLICT',r['sku'],r['entity_key']))
        limit=max(1,min(int(limit),100));offset=max(0,int(offset))
        return {'items':items[offset:offset+limit],'total':len(items),'limit':limit,'offset':offset,'coverage':coverage}
    finally:conn.close()
