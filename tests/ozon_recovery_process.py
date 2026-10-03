"""Runs only inside catalog_recovery_process's source/DB/network guards."""
import json
import sqlite3
import threading
import http.client
from decimal import Decimal
from copy import deepcopy
from http.server import ThreadingHTTPServer


def run(scenario,temp,ports):
    from modules.ozon import config,client
    from modules.products import server
    from shared_platform.catalog_publication_sync import CatalogPublicationSync
    from shared_platform.catalog_ozon import catalog_rows,save_manual,SCHEMA
    from shared_platform.catalog_cost_projection import SCHEMA as COMMON_SCHEMA
    from shared_platform.catalog_ozon_readback import QUERY_PATHS
    mode=scenario['mode'];calls=[]
    config.ozon_credentials=lambda:('102' if mode=='account_drift' else '101','synthetic-only')
    def post(path,body,*,client_id,api_key,**kwargs):
        assert path in QUERY_PATHS and client_id=='101' and api_key=='synthetic-only'
        calls.append({'method':'POST','path':path,'body':body,'account_id':client_id})
        if mode=='timeout':raise TimeoutError('synthetic')
        offer=body.get('filter',{}).get('offer_id',[None])[0] if 'filter' in body else body['offer_id']
        if isinstance(offer,list):offer=offer[0]
        expected=scenario['responses'][offer][path]
        assert body==expected['body']
        return deepcopy(expected['response'])
    client.ozon_post_bound=post
    def forbidden(*a,**kw):raise AssertionError('unbound transport or mutation forbidden')
    client.ozon_post=forbidden
    if mode=='manual':
        i=scenario['identity']
        with sqlite3.connect(temp/'shop.db') as conn:
            conn.executescript(COMMON_SCHEMA+SCHEMA)
            conn.execute('INSERT INTO catalog_ozon_products VALUES(?,?,?,?,?)',(i['shop_key'],i['product_id'],i['offer_id'],'{}','synthetic-prior-observation'))
        save_manual(temp/'shop.db',i,'43',0)
    server._catalog_publication_sync=lambda:CatalogPublicationSync(temp/'shop.db',temp/'outbox')
    httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);ports.add(httpd.server_port)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    try:
        def request():
            connection=http.client.HTTPConnection('127.0.0.1',httpd.server_port)
            connection.request('POST','/api/catalog/publication-sync/official-readback',json.dumps({'receipt_id':scenario['receipt_id']}),{'Content-Type':'application/json'})
            response=connection.getresponse();result=json.loads(response.read());connection.close()
            assert response.status==200,result
            return result
        result=request();count=len(calls)
        with sqlite3.connect(temp/'shop.db') as conn:
            conn.row_factory=sqlite3.Row;rows=catalog_rows(conn)
        if mode in {'normal','manual'}:
            assert result['state']==('COMPLETE' if mode=='normal' else 'NEEDS_REVIEW'),result
            assert len(rows)==len(scenario['responses'])
            from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
            actual=load_local_catalog(temp/'shop.db')
            for row in rows:
                assert actual.costs_by_sku[row['identity']['offer_id']]==Decimal(row['cost']['amount'])
            if mode=='manual':assert rows[0]['cost']['amount']=='43'
            else:assert request()['state']=='COMPLETE' and len(calls)==count
        else:
            assert result['state']=='PENDING_OFFICIAL_EVIDENCE',result
            assert not rows
            if mode=='account_drift':assert not calls
        return result,calls
    finally:
        httpd.shutdown();httpd.server_close();thread.join(3)
