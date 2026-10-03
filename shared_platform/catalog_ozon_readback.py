"""Ozon exact-offer read-only POST adapter and typed observations."""
from copy import deepcopy
from shared_platform.catalog_ozon import identity

INFO='/v3/product/info/list'
ATTRIBUTES='/v4/product/info/attributes'
DESCRIPTION='/v1/product/info/description'
QUERY_PATHS=frozenset({INFO,ATTRIBUTES,DESCRIPTION})


def account_identity(value):
    if not isinstance(value,dict) or set(value)!={'account_id','credential_ref'}:raise ValueError('ozon_frozen_account_required')
    if type(value['account_id']) is not str or not value['account_id'].isdecimal() or int(value['account_id'])<=0 or value['credential_ref']!='ozon-config-account':
        raise ValueError('ozon_frozen_account_invalid')
    return dict(value)


class ExactOzonQueries:
    """Only three POST query paths. Single exact offer per request; no search."""
    def __init__(self,post,offers):
        self.post=post;self.offers=set(offers);self.product_ids={}
        if not self.offers or len(self.offers)!=len(offers) or any(type(v) is not str or not v or v!=v.strip() for v in offers):
            raise ValueError('ozon_exact_offer_scope_required')

    def __call__(self,path,body):
        if path not in QUERY_PATHS:raise ValueError('ozon_query_path_denied')
        raw=body.get('filter',{}).get('offer_id') if path==ATTRIBUTES else body.get('offer_id')
        offers=[raw] if path==DESCRIPTION else raw
        if not isinstance(offers,list) or len(offers)!=1 or offers[0] not in self.offers:raise ValueError('ozon_query_scope_conflict')
        offer=offers[0]
        payload=({'offer_id':[offer]} if path==INFO else {'filter':{'offer_id':[offer],'visibility':'ALL'},'limit':1000} if path==ATTRIBUTES else {'offer_id':offer})
        response=self.post(path,payload)
        if not isinstance(response,dict) or response.get('error'):raise ValueError('ozon_query_rejected')
        if any(response.get(k) for k in ('has_next','has_next_page','next_cursor','next_page','last_id')):raise ValueError('ozon_query_incomplete')
        if path==DESCRIPTION:
            row=response.get('result')
            if not isinstance(row,dict) or row.get('offer_id')!=offer or str(row.get('id'))!=self.product_ids.get(offer):raise ValueError('ozon_description_identity_conflict')
        else:
            rows=response.get('items' if path==INFO else 'result')
            if not isinstance(rows,list) or len(rows)!=1 or any(not isinstance(r,dict) or r.get('offer_id')!=offer for r in rows):raise ValueError('ozon_query_identity_set_conflict')
            # Existing producer parser uses items/result without total. Exact
            # single-offer requests must return exactly that identity; explicit
            # pagination/count contradictions still fail closed.
            for key in ('total','total_count'):
                if key in response and (type(response[key]) is not int or response[key]!=len(rows)):raise ValueError('ozon_query_total_conflict')
            if path==INFO:
                if rows:
                    pid=rows[0].get('id')
                    if type(pid) not in (str,int) or not str(pid).isdecimal() or int(pid)<=0:raise ValueError('ozon_official_product_id_missing')
                    self.product_ids[offer]=str(pid)
            elif len(rows)!=1 or str(rows[0].get('id'))!=self.product_ids.get(offer):raise ValueError('ozon_attribute_identity_conflict')
        return response


def read_exact_offers(offers,post):
    from shared_platform.product_publication_live_dependencies import OfficialOzonV4Transport
    queries=ExactOzonQueries(post,offers);parser=OfficialOzonV4Transport(post=queries)
    rows=[]
    for offer in offers:rows.extend(parser.readback_variants((offer,)))
    return rows


def bound_query_transport(account):
    """Resolve once, compare Client-Id, then bind credentials without refresh."""
    account=account_identity(account)
    from modules.ozon.config import ozon_credentials
    from modules.ozon.client import ozon_post_bound
    cid,key=ozon_credentials()
    if str(cid)!=account['account_id'] or not key:raise ValueError('ozon_credential_account_unavailable')
    def query(path,body):
        if path not in QUERY_PATHS:raise ValueError('ozon_query_path_denied')
        return ozon_post_bound(path,body,client_id=str(cid),api_key=key)
    return query


def observations(account,variants,items):
    from modules.ozon.approved_publication_v4 import _classify_variant,OzonDispatchFact
    account=account_identity(account)
    expected={v['offer_id']:v for v in variants}
    if len(expected)!=len(variants) or not expected or not isinstance(items,(list,tuple)):raise ValueError('ozon_frozen_offer_set_invalid')
    actual={}
    for item in items:
        if not isinstance(item,dict) or item.get('offer_id') not in expected or item['offer_id'] in actual:raise ValueError('ozon_official_offer_set_conflict')
        actual[item['offer_id']]=item
    if set(actual)!=set(expected):raise ValueError('ozon_official_offer_set_incomplete')
    rows=[];ids=set()
    for offer,variant in expected.items():
        item=actual[offer]
        full=identity({'platform':'ozon','identity_kind':'product_offer','shop_key':account['account_id'],'product_id':str(item.get('id') or ''),'offer_id':offer,'seller_sku':offer})
        if full['product_id'] in ids:raise ValueError('ozon_duplicate_product_id')
        ids.add(full['product_id'])
        # UNKNOWN is not a fabricated successful dispatch. Complete official
        # stored facts alone must satisfy the shared pure classifier.
        if _classify_variant(variant,item,OzonDispatchFact('UNKNOWN'))!='PUBLISHED':raise ValueError('ozon_official_frozen_facts_mismatch')
        rows.append({'authority':'OFFICIAL','verified':True,'target_label':'ozon:RU','model_sku':offer,'identity':full,
                     'listing':{'name':item['name'],'specification':deepcopy(variant['specification']),'price':item['price'],'approved_currency':variant['currency'],'statuses':deepcopy(item['statuses']),'images':deepcopy(item['images'])}})
    return rows


def recover_observations(account,variants):
    rows=read_exact_offers(tuple(v['offer_id'] for v in variants),bound_query_transport(account))
    return observations(account,variants,rows)
