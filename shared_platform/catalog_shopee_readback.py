"""Exact-ID Shopee catalog reconciliation. No publish or repair dependency."""
import time
from modules.shopee import skill_regions as regions

SHOP_GETS=frozenset({'/api/v2/product/get_item_base_info','/api/v2/product/get_model_list'})
MERCHANT_GETS=frozenset({regions.TASK_RESULT_PATH,'/api/v2/global_product/get_global_model_list','/api/v2/global_product/get_global_item_id'})


def positive(value):
    value=str(value)
    if not value.isdecimal() or int(value)<=0:raise ValueError('official_query_identity_invalid')
    return value


class CatalogShopeeQueries:
    """Construction never refreshes credentials; every transport is allowlisted GET."""
    def context(self,binding):
        from modules.shopee.auth import load_tokens
        sid=positive(binding['shop_id']);mid=positive(binding['merchant_id'])
        tokens=load_tokens()
        shop=tokens.get('shops',{}).get(sid,{})
        merchant=tokens.get('merchants',{}).get(mid,{})
        if str(shop.get('shop_id'))!=sid or str(merchant.get('merchant_id'))!=mid:
            raise ValueError('cached_credential_identity_missing')
        if shop.get('region') and shop['region'].upper()!=binding['region']:
            raise ValueError('cached_shop_region_conflict')
        for entry in (shop,merchant):
            if not entry.get('access_token') or int(entry.get('expire_at') or 0)<=time.time()+30:
                raise ValueError('cached_credential_missing_or_expired')
        return regions.RegionContext(region=binding['region'],shop_id=int(sid),merchant_id=int(mid),shop_token=shop['access_token'],merchant_token=merchant['access_token'])

    def get(self,context,path,params):
        from modules.shopee.client import shop_get,merchant_get
        if path in SHOP_GETS:
            response=shop_get(path,context.shop_id,context.shop_token,params)
        elif path in MERCHANT_GETS:
            response=merchant_get(path,context.merchant_id,context.merchant_token,params)
        else:raise ValueError('catalog_readback_get_path_denied')
        if not isinstance(response,dict) or response.get('error') not in (None,'','-'):
            raise ValueError('official_get_failed')
        payload=response.get('response')
        if not isinstance(payload,dict):raise ValueError('official_get_malformed')
        return payload


def exact_rows(payload,key):
    rows=payload.get(key)
    if not isinstance(rows,list) or not rows or any(not isinstance(row,dict) for row in rows):
        raise ValueError('official_rows_missing')
    return rows


def read_catalog_observations(snapshot,targets,recovery,dispatch):
    queries=CatalogShopeeQueries();gid=positive(recovery['global_item_id'])
    bindings={row['target_label']:row for row in recovery['bindings']}
    tasks={row['target_label']:row for row in dispatch}
    if len(bindings)!=len(recovery['bindings']) or len(tasks)!=len(dispatch) or set(bindings)!=set(targets) or set(tasks)!=set(targets):
        raise ValueError('persisted_target_scope_conflict')
    rows=[]
    for target in targets:
        binding=bindings[target];task=tasks[target]
        if target!='shopee:'+binding['region'] or task.get('accepted') is not True:
            raise ValueError('persisted_target_identity_unavailable')
        context=queries.context(binding)
        if task.get('existing_item_id'):
            item_id=positive(task['existing_item_id'])
        else:
            task_id=positive(task.get('provider_task_id'))
            task_result=queries.get(context,regions.TASK_RESULT_PATH,{'publish_task_id':int(task_id)})
            if task_result.get('publish_status')!='success':raise ValueError('official_publish_not_confirmed')
            item_id=positive(task_result.get('item_id'))
        items=exact_rows(queries.get(context,'/api/v2/product/get_item_base_info',{'item_id_list':item_id}),'item_list')
        if len(items)!=1 or str(items[0].get('item_id'))!=item_id:raise ValueError('official_item_identity_conflict')
        item=items[0]
        models=exact_rows(queries.get(context,'/api/v2/product/get_model_list',{'item_id':int(item_id)}),'model')
        links=exact_rows(queries.get(context,'/api/v2/global_product/get_global_item_id',{'shop_id':context.shop_id,'item_id_list':item_id}),'item_id_map')
        if len(links)!=1 or str(links[0].get('item_id'))!=item_id or str(links[0].get('global_item_id'))!=gid:
            raise ValueError('official_global_linkage_conflict')
        approved=regions._approved_models(snapshot,target)
        globals_=exact_rows(queries.get(context,'/api/v2/global_product/get_global_model_list',{'global_item_id':int(gid)}),'global_model')
        tiers=regions._exact_global_tiers(globals_,approved)
        title,description=regions._approved_copy(snapshot,target)
        base_images=tuple(regions._approved_images(snapshot));images=tuple(regions.publication_images_for_target(snapshot,target))
        expected_ids=None
        if images!=base_images:
            # Existing server-owned mapping only; never infer localized lineage
            # from whatever gallery the provider currently happens to return.
            from modules.shopee.global_sku_map import load_map
            entry=load_map().get(gid,{})
            shop_item=entry.get('shop_items',{}).get(context.region,{})
            if str(shop_item.get('shop_id'))!=str(context.shop_id) or str(shop_item.get('item_id'))!=item_id:
                raise ValueError('localized_gallery_identity_conflict')
            saved=shop_item.get('localized_images')
            if not saved or saved.get('image_route_digest')!=regions._digest({'ordered_images':list(images)}):
                raise ValueError('localized_gallery_binding_missing')
            expected_ids=tuple(regions._image_ids(saved.get('image_ids'),'stored regional image binding'))
        image_ids=expected_ids if expected_ids is not None else regions._official_image_ids(item)
        if len(image_ids)!=len(images):raise ValueError('official_gallery_coverage_missing')
        checks=regions._official_readback_checks(item=item,models=models,resolved_global_item_id=gid,expected_global_item_id=gid,expected_models=approved,expected_tiers=tiers,expected_logistics=[],expected_image_count=len(images),expected_item_sku=regions._expected_item_sku(snapshot),expected_category_id=regions._expected_category_id(snapshot,target),expected_title=title,expected_description=description,expected_region=context.region,repaired_copy=None,expected_image_ids=expected_ids,expected_description_image_ids=image_ids,item_id=item_id)
        if not all(checks.values()):raise ValueError('official_frozen_facts_mismatch')
        observations=regions._catalog_observation_rows(target,context.shop_id,item_id,context.region,item,models,approved)
        if len(observations)!=len(approved) or any(row.get('verified') is not True for row in observations):
            raise ValueError('official_variant_identity_conflict')
        rows.extend(observations)
    return rows
