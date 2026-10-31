"""Exact per-target official TikTok readback; no refresh or catalog write.

Miaoshou's internal shop id is never passed to the official shop API. The
provider shop list and official shop list must join uniquely on name/region.
Missing, expired, ambiguous or changing authorization remains UNAVAILABLE.
"""
from collections.abc import Mapping
import hashlib
import json
from pathlib import Path

SHOP_PATH='/authorization/202309/shops'
MIAOSHOU_SHOPS='/open/v1/product/shop/shop/get_shop_list'
SEARCH='/product/202309/products/search'
DETAIL='/product/202309/products/{product_id}'

def _unavailable(label):
    return {'target_label':label,'authority':'UNAVAILABLE','status':'UNAVAILABLE','exact':False}

def _miaoshou_shop_name(row):
    """Resolve the ERP's observed name aliases without choosing a conflict."""
    names=[]
    for key in ('shopName','name','platformShopName'):
        value=row.get(key)
        if value is None or value=='':continue
        if type(value) is not str:raise ValueError('MIAOSHOU_SHOP_NAME_INVALID')
        if value.strip():names.append(value)
    if len(set(names))>1:raise ValueError('MIAOSHOU_SHOP_NAME_CONFLICT')
    return names[0] if names else ''

def _official_identity(miaoshou,official,*,internal_id,region):
    """A unique concrete two-provider join; region alone is never identity."""
    from modules.tiktok.oneclick_promotion import _unique_shop
    data=miaoshou.get('data') if isinstance(miaoshou,Mapping) else None
    rows=(data.get('list') or data.get('rows') or data.get('shopList')) if isinstance(data,Mapping) else data
    if (not isinstance(miaoshou,Mapping) or miaoshou.get('result')!='success'
            or not isinstance(rows,list) or any(not isinstance(row,Mapping) for row in rows)):
        raise ValueError('MIAOSHOU_EXACT_SHOP_LIST_UNAVAILABLE')
    exact=[r for r in rows if str(r.get('shopId') or r.get('id') or '')==internal_id
        and str(r.get('site') or r.get('region') or '').upper()==region]
    if len(exact)!=1:raise ValueError('MIAOSHOU_SHOP_IDENTITY_NOT_UNIQUE')
    row=exact[0];name=_miaoshou_shop_name(row)
    if not name or str(row.get('platform') or 'TIKTOK').upper()!='TIKTOK':
        raise ValueError('MIAOSHOU_SHOP_IDENTITY_UNAVAILABLE')
    if sum(str(r.get('site') or r.get('region') or '').upper()==region
            and _miaoshou_shop_name(r)==name for r in rows)!=1:
        raise ValueError('MIAOSHOU_SHOP_JOIN_AMBIGUOUS')
    data=official.get('data') if isinstance(official,Mapping) else None
    shops=(data.get('shops') or data.get('list')) if isinstance(data,Mapping) else None
    if not isinstance(shops,list) or any(not isinstance(r,Mapping) for r in shops):
        raise ValueError('OFFICIAL_SHOP_LIST_UNAVAILABLE')
    joined=[r for r in shops if str(r.get('name') or r.get('shop_name') or '')==name
        and str(r.get('region') or r.get('region_code') or '').upper()==region]
    if len(joined)!=1:raise ValueError('OFFICIAL_SHOP_JOIN_NOT_UNIQUE')
    shop_id=str(joined[0].get('id') or joined[0].get('shop_id') or '')
    if not shop_id:raise ValueError('OFFICIAL_SHOP_ID_UNAVAILABLE')
    # If the ERP also exposes an official id, it must agree with the join.
    explicit=row.get('platformShopId') or row.get('tiktokShopId')
    if explicit is not None and str(explicit)!=shop_id:
        raise ValueError('OFFICIAL_SHOP_JOIN_CONFLICT')
    return _unique_shop(official,shop_id=shop_id,shop_name=name,region=region)

def _sales_attributes_match(attrs, specification, target_label):
    """Same publisher projection, exact values; no fuzzy attribute aliases."""
    observed = {row['name']: row.get('value_name') for row in attrs}
    if observed == specification:
        return True
    if len(attrs) != 1:
        return False
    from modules.miaoshou.tiktok_publisher import MiaoshouTikTokTransport
    display = MiaoshouTikTokTransport._approved_variant_display(specification)
    region = target_label.rsplit('_', 1)[-1].split(':')[-1]
    # Official localized names of the single Specification property. Values
    # remain exact frozen text, never translated/truncated/whitespace-folded.
    localized_names = {'MY':'Spesifikasi','TH':'ข้อมูลจำเพาะ','VN':'Đặc điểm kỹ thuật'}
    allowed = {'Specification'}
    if region in localized_names:
        allowed.add(localized_names[region])
    return (type(display) is str and bool(display) and attrs[0]['name'] in allowed
            and attrs[0].get('value_name') == display)


def _detail_matches(detail,command,product_id,inventory_binding=None,required_product_attributes=()):
    from modules.products.release_adapters import _numbers_equal
    expected=command['publisher_snapshot']['targets'][0]
    wanted={r['model_sku']:r for r in command['skus']}
    if str(detail.get('id') or detail.get('product_id') or '')!=product_id:return False
    if command.get('target_label')=='tiktok:GB':
        if (len(required_product_attributes)!=1
                or required_product_attributes[0].get('attribute_id')!='102255'
                or required_product_attributes[0].get('value_id')!='1000256'):
            return None
        attrs=detail.get('product_attributes')
        if not isinstance(attrs,list) or any(not isinstance(a,Mapping) for a in attrs):return None
        ids=[str(a.get('id') or '') for a in attrs]
        if len(ids)!=len(set(ids)):return None
        exact=[a for a in attrs if str(a.get('id') or '')=='102255']
        if len(exact)!=1:return None
        values=exact[0].get('values')
        if not isinstance(values,list) or len(values)!=1 or not isinstance(values[0],Mapping):return None
        if str(values[0].get('id') or '')!='1000256':return None
    # Missing official physical/variant/stock facts cannot certify a publish.
    if any(key not in detail for key in ('description','package_weight','package_dimensions')):return None
    rows=detail.get('skus')
    if not isinstance(rows,list) or any(not isinstance(r,Mapping) for r in rows):return False
    by_sku={str(r.get('seller_sku') or ''):r for r in rows}
    if len(by_sku)!=len(rows) or set(by_sku)!=set(wanted):return False
    for model,row in wanted.items():
        price=by_sku[model].get('price') or {}
        if (not isinstance(price,Mapping) or not _numbers_equal(price.get('sale_price'),row['price'])
                or price.get('currency')!=row['currency']):return False
        attrs=by_sku[model].get('sales_attributes');inventory=by_sku[model].get('inventory')
        if not isinstance(attrs,list) or not isinstance(inventory,list):return None
        if any(not isinstance(a,Mapping) for a in attrs+inventory):return None
        if (any(type(a.get('name')) is not str or not a['name'] for a in attrs)
                or len({a['name'] for a in attrs})!=len(attrs)):return False
        if any(not str(a.get('warehouse_id') or '') or type(a.get('quantity')) is not int
                or a['quantity']<0 for a in inventory):return None
        if len({str(a['warehouse_id']) for a in inventory})!=len(inventory):return False
        if not _sales_attributes_match(attrs,row['specification'],command['target_label']):return False
        stock=expected.get('expected_warehouse_inventory')
        actual_inventory={str(a.get('warehouse_id') or ''):a.get('quantity') for a in inventory}
        if isinstance(stock,Mapping) and isinstance(stock.get('warehouses'),list):
            frozen_inventory={str(a.get('warehouse_id') or ''):a.get('stock') for a in stock['warehouses'] if isinstance(a,Mapping)}
        elif isinstance(inventory_binding,Mapping) and isinstance(inventory_binding.get(model),Mapping):
            frozen_inventory=dict(inventory_binding[model])
        else:return None
        if not frozen_inventory or actual_inventory!=frozen_inventory:return False
    weight=detail['package_weight'];package=detail['package_dimensions']
    if not isinstance(weight,Mapping) or not isinstance(package,Mapping):return None
    if str(weight.get('unit') or '').upper() not in {'KILOGRAM','KG'} or str(package.get('unit') or '').upper() not in {'CENTIMETER','CM'}:return None
    if not _numbers_equal(weight.get('value'),expected['expected_weight_kg']):return False
    expected_package=expected['expected_package_cm']
    if not all(_numbers_equal(package.get(k),v) for k,v in zip(('length','width','height'),expected_package)):return False
    images=detail.get('main_images') or detail.get('images') or []
    urls=[]
    for image in images:
        if not isinstance(image,Mapping):return False
        values=image.get('urls') or image.get('url_list') or []
        if not isinstance(values,list) or not values:return False
        urls.append(str(values[0]))
    chain=detail.get('category_chains') or []
    cid=str(chain[-1].get('id') if chain and isinstance(chain[-1],Mapping) else detail.get('category_id') or '')
    from shared_platform.product_description_media import html_image_urls, tiktok_description_text
    try:
        description_matches = tiktok_description_text(detail['description']) == tiktok_description_text(expected['expected_description'])
        description_images = html_image_urls(detail['description'])
    except (TypeError, ValueError):
        return None
    if not description_images:
        return None  # Missing description-media coverage is not an empty gallery.
    if description_images != tuple(urls):
        return False
    return (detail.get('title')==expected['expected_title'] and description_matches
        and urls==expected['expected_images'] and cid==str(expected['expected_category_id'])
        and str(detail.get('status') or detail.get('product_status') or '').upper() in {'ACTIVATE','LIVE'})

def _official_catalog_rows(detail, command, shop, region):
    """Consume the exact detail already matched above; never invent SKU IDs."""
    cipher=str(shop.get('cipher') or shop.get('shop_cipher') or '')
    shop_id=str(shop.get('id') or shop.get('shop_id') or '')
    name=str(shop.get('name') or shop.get('shop_name') or '')
    product_id=str(detail.get('id') or detail.get('product_id') or '')
    if not all((cipher,shop_id,name,product_id)):
        raise ValueError('OFFICIAL_CATALOG_FULL_IDENTITY_UNAVAILABLE')
    binding={'schema_version':'official-tiktok-shop-binding/v1',
        'control_shop_id':str(command['control']['shop_id']), 'official_shop_id':shop_id,
        'shop_cipher':cipher,'region':region,'name':name}
    wanted={r['model_sku']:r for r in command['skus']}
    rows=[];seen=set()
    for sku in detail.get('skus') or []:
        model=str(sku.get('seller_sku') or '');sku_id=str(sku.get('id') or sku.get('sku_id') or '')
        if model not in wanted or not sku_id.isdecimal() or int(sku_id)<=0 or sku_id in seen:
            raise ValueError('OFFICIAL_CATALOG_SKU_ID_UNAVAILABLE')
        seen.add(sku_id)
        price=sku['price'];attrs=sku['sales_attributes']
        images=detail.get('main_images') or detail.get('images') or []
        image=(images[0].get('urls') or images[0].get('url_list') or [''])[0] if images else ''
        rows.append({'authority':'OFFICIAL','verified':True,'target_label':command['target_label'],
            'model_sku':model,'variant_key':wanted[model]['variant_key'],
            'identity':{'platform':'tiktok','shop_key':cipher,'product_id':product_id,'variant_id':sku_id,'seller_sku':model},
            'shop_binding':dict(binding),
            'listing':{'name':detail['title'],'variant_name':' / '.join(a['value_name'] for a in attrs),
                'price':price['sale_price'],'currency':price['currency'],'region':region,
                'stock':sum(r['quantity'] for r in sku['inventory']),
                'status':detail.get('status') or detail.get('product_status'),'image_url':image}})
    if len(rows)!=len(wanted):
        raise ValueError('OFFICIAL_CATALOG_SKU_COVERAGE_UNAVAILABLE')
    return rows


class NativeTikTokExactStorefrontReadback:
    @staticmethod
    def local_observation_contract():
        """The actual DTO scope; absent zero rows have no completeness proof."""
        return {'schema_version':'native-tiktok-readback-contract/v1',
            'inventory_scope':'EXACT_RETURNED_DETAIL_ROWS',
            'omitted_zero_quantity_proven':False,'provider_access':'NOT_OBSERVED'}

    def __init__(self,*,shop_post=None,official_call=None,token_loader=None,inventory_bindings=None,evidence=None,required_product_attributes=None):
        self._shop_post=shop_post;self._official_call=official_call;self._token_loader=token_loader
        self._inventory_bindings=inventory_bindings if inventory_bindings is not None else {}
        self._evidence=evidence
        self._required_product_attributes=required_product_attributes or {}

    def _calls(self):
        from core import auth
        from core.api_client import _do_request_once
        from modules.miaoshou.client import post_open
        if self._token_loader is not None:
            token,guard=self._token_loader()
        else:
            path=auth.token_path().resolve(strict=True);raw=path.read_bytes()
            pin=hashlib.sha256(raw).digest();document=json.loads(raw)
            if auth.is_access_expired(document,buffer_sec=0) or not document.get('access_token'):
                raise ValueError('TIKTOK_EXISTING_AUTH_UNAVAILABLE')
            token=document['access_token']
            def guard():
                if hashlib.sha256(path.read_bytes()).digest()!=pin or auth.is_access_expired(document,buffer_sec=0):
                    raise ValueError('TIKTOK_EXISTING_AUTH_CHANGED')
        call=self._official_call
        def official(method,path,query=None,body=None):
            guard()
            if call is not None:return call(method,path,token,query,body)
            return _do_request_once(method,path,token,query,body,False,False,call_guard=guard)
        return self._shop_post or post_open,official

    def readback(self,*,command,dispatch):
        label=command.get('target_label')
        try:
            from modules.miaoshou.tiktok_publisher import EXPECTED_SHOP_ID_BY_TARGET
            from shared_platform.product_publication_live_dependencies import _TIKTOK_SITE_BY_TARGET
            internal=str(command['control']['shop_id']);region=_TIKTOK_SITE_BY_TARGET[label]
            if command['target']['site']!=label.split(':')[1]:raise ValueError('TARGET_SITE_CHANGED')
            if EXPECTED_SHOP_ID_BY_TARGET.get(label)!=internal:raise ValueError('TARGET_SHOP_CHANGED')
            shop_post,official=self._calls()
            # One page may certify the join only when it is complete. Do not
            # select a first row from a partial or truncated enumeration.
            response=shop_post(MIAOSHOU_SHOPS,{'platform':'TIKTOK','site':region,'pageNo':1,'pageSize':100})
            data=response.get('data') if isinstance(response,Mapping) else None
            if isinstance(data,Mapping):
                rows=data.get('list') or data.get('rows') or data.get('shopList') or []
                total=data.get('total') or data.get('totalCount')
                if len(rows)>=100 or (total is not None and int(total)!=len(rows)):
                    raise ValueError('MIAOSHOU_SHOP_LIST_PARTIAL')
            shop=_official_identity(response,official('GET',SHOP_PATH),internal_id=internal,region=region)
            cipher=str(shop.get('cipher') or shop.get('shop_cipher') or '')
            if not cipher:raise ValueError('OFFICIAL_SHOP_CIPHER_UNAVAILABLE')
            models=[row['model_sku'] for row in command['skus']]
            result=official('POST',SEARCH,{'shop_cipher':cipher,'page_size':100},{'status':'ACTIVATE','seller_skus':models})
            data=result.get('data') if isinstance(result,Mapping) else None
            products=(data.get('products') or data.get('product_list') or data.get('list') or []) if isinstance(data,Mapping) else None
            if result.get('code')!=0 or not isinstance(products,list) or any(not isinstance(p,Mapping) for p in products):
                raise ValueError('OFFICIAL_EXACT_SEARCH_UNAVAILABLE')
            if data.get('next_page_token') or (data.get('total_count') is not None and int(data['total_count'])!=len(products)):
                raise ValueError('OFFICIAL_EXACT_SEARCH_PARTIAL')
            exact=[p for p in products if {str(r.get('seller_sku') or '') for r in p.get('skus',[]) if isinstance(r,Mapping)}==set(models)]
            if not exact:return {'target_label':label,'authority':'OFFICIAL_STOREFRONT','status':'NOT_FOUND','exact':False}
            if len(exact)!=1:raise ValueError('OFFICIAL_EXACT_SKU_NOT_UNIQUE')
            product_id=str(exact[0].get('id') or exact[0].get('product_id') or '')
            if not product_id:raise ValueError('OFFICIAL_PRODUCT_ID_UNAVAILABLE')
            result=official('GET',DETAIL.format(product_id=product_id),{'shop_cipher':cipher})
            if result.get('code')!=0 or not isinstance(result.get('data'),Mapping):
                raise ValueError('OFFICIAL_PRODUCT_DETAIL_UNAVAILABLE')
            required=self._required_product_attributes.get(label,())
            matched=_detail_matches(result['data'],command,product_id,self._inventory_bindings.get(label),required)
            catalog_rows=_official_catalog_rows(result['data'],command,shop,region) if matched is True else None
            if self._evidence is not None:
                self._evidence[label]={'source':'official_tiktok_shop_api','shop_id':str(shop.get('id') or shop.get('shop_id')),
                    'product_id':product_id,'command_digest':command.get('command_digest'),
                    'detail_sha256':hashlib.sha256(json.dumps(result['data'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                    'complete_frozen_match':matched is True,'physical_variant_stock_unavailable':matched is None,
                    'required_product_attributes_verified':bool(required) and matched is True if label=='tiktok:GB' else True,
                    'catalog_write':False,'auth_refresh':False}
            return {'target_label':label,'authority':'OFFICIAL_STOREFRONT',
                'status':'VERIFIED' if matched is True else ('UNVERIFIED' if matched is None else 'MISMATCH'),
                'exact':matched is True,**({'official_catalog_rows':catalog_rows} if catalog_rows else {})}
        except (OSError,ValueError,KeyError,TypeError,RuntimeError):
            return _unavailable(label)
