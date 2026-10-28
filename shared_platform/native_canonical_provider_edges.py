"""Protected canonical typed outcomes over the durable mutation seam."""
from collections.abc import Mapping

def draft_factory(post,ledger):
    from shared_platform.product_publication_live_dependencies import MiaoshouTikTokV4DraftTransportFactory
    from modules.miaoshou.tiktok_v4_drafts import DraftWriteFact
    original=MiaoshouTikTokV4DraftTransportFactory(post=post)
    def factory(request,observer):
        def observe(label,fact):
            # The original observer checks exact typed identity and persists
            # its checkpoint before a known business outcome is recorded.
            observer(label,fact)
            if type(fact) is DraftWriteFact and fact.outcome in {'ACCEPTED','REJECTED'} and fact.operation!='IDENTITY_OBSERVED':
                operation={'CREATE_DRAFT':'create_platform_draft','CLAIM_TO_SHOP':'claim_draft','SAVE_DRAFT':'save_draft'}.get(fact.operation)
                if operation is None:raise ValueError('NATIVE_TIKTOK_TYPED_OPERATION_UNCLASSIFIED')
                ledger.complete_reserved(operation,fact.outcome)
        return original(request,observe)
    return factory

def tiktok_publisher(post,ledger,inventory_bindings=None):
    from modules.miaoshou.tiktok_publisher import MiaoshouTikTokTransport
    from domains.channel_operations.tiktok_publisher import TikTokPublisher,_provider_acceptance
    from modules.miaoshou.client import MiaoshouBusinessRejectedError
    class Transport(MiaoshouTikTokTransport):
        def _remember_inventory(self,target,draft):
            if inventory_bindings is None:return
            info=draft.get('info') or {};rows=info.get('skuMap') or {}
            expected=set(target['expected_sku_prices']);binding={}
            for row in rows.values():
                model=str(row.get('itemNum') or row.get('sellerSku') or '')
                maps=row.get('shopIdToWarehouseIdAndStockMap') or {}
                stock=maps.get(str(target['shop_id']))
                if model not in expected or not isinstance(stock,Mapping):raise ValueError('NATIVE_TIKTOK_PREWRITE_INVENTORY_UNAVAILABLE')
                if any(not str(v).isdigit() for v in stock.values()):raise ValueError('NATIVE_TIKTOK_PREWRITE_INVENTORY_INVALID')
                binding[model]={str(k):int(str(v)) for k,v in stock.items()}
            if set(binding)!=expected:raise ValueError('NATIVE_TIKTOK_PREWRITE_INVENTORY_INCOMPLETE')
            # The original matcher has already checked exact shop warehouse
            # semantics and other active warehouses zero. This is provider
            # control evidence under CURRENT_PROVIDER_SKU_STOCK, not new stock.
            inventory_bindings[target['target_label']]=binding
        def draft_matches(self,target,draft):
            matched=super().draft_matches(target,draft)
            if matched:self._remember_inventory(target,draft)
            return matched
        def prepare_approved_draft(self,target,draft):
            prepared=super().prepare_approved_draft(target,draft)
            from shared_platform.native_accepted_save_core_repair import _CoreLedger
            if type(ledger) is _CoreLedger:ledger.bind_prepared(target,draft,prepared)
            return prepared
        def post_save_draft_matches(self,target,draft):
            matched=super().post_save_draft_matches(target,draft)
            from shared_platform.native_known_rejected_save_repair import _RepairLedger
            if type(ledger) is _RepairLedger and matched:
                ledger.retain_post_save_match(target,draft,matched)
            from shared_platform.native_accepted_save_core_repair import _CoreLedger
            if type(ledger) is _CoreLedger:ledger.retain_match(target,draft,matched)
            if matched:self._remember_inventory(target,draft)
            return matched
        def save_prepared_draft(self,target,prepared):
            # Canonical before_publish reserves publish. An unexpected repair
            # SAVE remains a separate target mutation within the same ceiling.
            ledger.reserve_target(target['target_label'],'save_draft')
            value=super().save_prepared_draft(target,prepared)
            try:_provider_acceptance(value)
            except MiaoshouBusinessRejectedError:
                from shared_platform.native_accepted_save_core_repair import _CoreLedger
                if type(ledger) is _CoreLedger:ledger.complete_reserved('save_draft','REJECTED')
                raise
            ledger.complete_reserved('save_draft','ACCEPTED')
            return value
        def submit(self,target):
            from shared_platform.native_accepted_save_core_repair import _CoreLedger
            if type(ledger) is _CoreLedger:
                draft=self.read_draft(target)
                ledger.retain_match(target,draft,self.post_save_draft_matches(target,draft))
                ledger.require_publish_match(target)
            value=super().submit(target)
            try:_provider_acceptance(value)
            except MiaoshouBusinessRejectedError:
                ledger.complete_reserved('publish_target','REJECTED');raise
            ledger.complete_reserved('publish_target','ACCEPTED')
            return value
    return TikTokPublisher(transport=Transport(post=post,publish_interval_seconds=1.1))

def ozon_transport(post,ledger,account_id,references):
    from shared_platform.product_publication_live_dependencies import OfficialOzonV4Transport
    from modules.ozon.approved_publication_v4 import OzonDispatchFact,OzonStockDispatchFact
    class Transport(OfficialOzonV4Transport):
        def dispatch_variant(self,variant):
            result=super().dispatch_variant(variant)
            if type(result) is OzonDispatchFact and result.outcome in {'ACCEPTED','REJECTED'}:
                ledger.complete_reserved('import_variant',result.outcome)
                if result.outcome=='ACCEPTED':references.append('ozon:import-task:'+result.task_id)
            return result
        def readback_variants(self,offer_ids):
            rows=super().readback_variants(offer_ids)
            for row in rows:
                if isinstance(row,Mapping) and str(row.get('offer_id') or '') in offer_ids:
                    references.append('ozon:offer:'+str(row['offer_id']))
            return rows
        def prepare_stock_update(self,updates):
            from modules.ozon.approved_publication_v4 import PreparedStockUpdate
            prepared=super().prepare_stock_update(updates)
            def submit():
                result=prepared.submit()
                if type(result) is OzonStockDispatchFact and result.outcome in {'ACCEPTED','REJECTED'}:
                    ledger.complete_reserved('update_stock',result.outcome)
                return result
            return PreparedStockUpdate(warehouse_id=prepared.warehouse_id,submit=submit)
    return Transport(post=post,account_id=account_id)

def _existing_shopee_context(region,store=None):
    """Read the existing exact mapping/token rows; never sync or refresh."""
    import time
    from modules.shopee.auth import load_tokens
    from modules.shopee.skill_regions import RegionContext
    if store is None:store=load_tokens()
    mapping=store.get('sync_shop_ids') or {}
    shop_id=mapping.get(region)
    shop=(store.get('shops') or {}).get(str(shop_id)) or {}
    merchant_id=shop.get('merchant_id')
    merchant=(store.get('merchants') or {}).get(str(merchant_id)) or {}
    if (type(shop_id) is not int or shop_id<=0 or shop.get('region')!=region
            or type(merchant_id) is not int or merchant_id<=0
            or not shop.get('access_token') or not merchant.get('access_token')
            or type(shop.get('expire_at')) not in {int,float}
            or type(merchant.get('expire_at')) not in {int,float}
            or shop['expire_at']<=time.time() or merchant['expire_at']<=time.time()):
        raise ValueError('SHOPEE_EXISTING_EXACT_AUTH_UNAVAILABLE')
    return RegionContext(region,shop_id,merchant_id,shop['access_token'],merchant['access_token'])

def shopee_dependencies(ledger,root,references):
    """Same canonical global/regional parsers, existing credentials only.

    Any edge which cannot certify one finite mutation is held before network.
    A legacy multi-write logistics repair is deliberately not admitted.
    """
    from modules.shopee.global_v4_live_runtime import (
        OfficialShopeeGlobalV4Runtime,_default_official_fact_reader,
        _default_image_upload,ShopeeGlobalModelUpdateFailure)
    from modules.shopee.global_v4_executor import ShopeeGlobalV4Resolver
    from modules.shopee.global_sku_map import global_item_id_for_match_key
    from modules.shopee.skill_regions import OfficialShopeeRegionRuntime,_publish_task_id,_provider_error
    from modules.shopee.client import merchant_get,merchant_post
    from shared_platform.product_publication_live_dependencies import build_live_shopee_dependencies

    import hashlib,json
    from modules.shopee.auth import token_path
    token_file=token_path().resolve(strict=True);token_bytes=token_file.read_bytes()
    token_pin=hashlib.sha256(token_bytes).digest();token_store=json.loads(token_bytes)
    def guard():
        if hashlib.sha256(token_file.read_bytes()).digest()!=token_pin:
            raise ValueError('SHOPEE_EXISTING_AUTH_SOURCE_CHANGED')
        # All entries used by this scope must still be unexpired. No callback
        # may replace an authorization just because the file changed.
    def existing(region):guard();return _existing_shopee_context(region,token_store)
    def context(command):
        source=command.get('price_source') or {};ctx=existing(str(source.get('region') or '').upper())
        return vars(ctx)

    # The original global runtime parses each response and exact resulting
    # identity. Its injected transport persists dispatch before the wire;
    # typed method wrappers below complete only after that original parser.
    operations={'/api/v2/global_product/add_global_item':'create_global_item',
        '/api/v2/global_product/init_tier_variation':'initialize_models',
        '/api/v2/global_product/update_global_item':'update_global_item',
        '/api/v2/global_product/update_tier_variation':'update_global_tier',
        '/api/v2/global_product/update_price':'update_global_models'}
    def global_post(path,merchant_id,token,body):
        guard()
        operation=operations.get(path)
        if operation is None:raise ValueError('NATIVE_SHOPEE_GLOBAL_ENDPOINT_UNCLASSIFIED')
        value=ledger.dispatch_reserved(operation,lambda endpoint,payload:merchant_post(endpoint,merchant_id,token,payload),path,body)
        if isinstance(value,Mapping) and type(value.get('error')) is str and value['error'].strip() not in {'','-'}:
            # The original provider parser classifies this exact shape as a
            # business rejection; retain the original response for its caller.
            try:OfficialShopeeGlobalV4Runtime._provider_response(value,'native global mutation')
            except ValueError:ledger.complete_reserved(operation,'REJECTED')
        return value
    def image_upload(url,position):
        guard()
        value=ledger.dispatch_reserved('upload_image',_default_image_upload,url,position)
        if type(value) is not str or not value.strip():raise ValueError('NATIVE_SHOPEE_IMAGE_ID_UNAVAILABLE')
        ledger.complete_reserved('upload_image','ACCEPTED');return value
    class Global(OfficialShopeeGlobalV4Runtime):
        def _known(self,operation,call,*args):
            try:value=call(*args)
            except ShopeeGlobalModelUpdateFailure as error:
                if error.kind=='BUSINESS_REJECTED' and error.outcome_unknown is False:
                    ledger.complete_reserved(operation,'REJECTED')
                raise
            ledger.complete_reserved(operation,'ACCEPTED');return value
        def create_global_item(self,payload):return self._known('create_global_item',super().create_global_item,payload)
        def initialize_global_models(self,identity,payload):return self._known('initialize_models',super().initialize_global_models,identity,payload)
        def update_existing_global_item(self,identity,payload):return self._known('update_global_item',super().update_existing_global_item,identity,payload)
        def update_existing_global_tier_variation(self,identity,payload):return self._known('update_global_tier',super().update_existing_global_tier_variation,identity,payload)
        def update_existing_global_models(self,identity,payload):return self._known('update_global_models',super().update_existing_global_models,identity,payload)
    global_runtime=Global(context_resolver=context,official_fact_reader=_default_official_fact_reader,
        mapping_lookup=global_item_id_for_match_key,merchant_get_transport=merchant_get,
        merchant_post_transport=global_post,image_upload_transport=image_upload,checkpoint_root=root)
    class Regional(OfficialShopeeRegionRuntime):
        def context(self,region):return existing(region)
        def _known(self,operation,call,wire,*args,**kwargs):
            guard()
            value=ledger.dispatch_reserved(operation,lambda _:call(*args,**kwargs),wire)
            ledger.complete_reserved(operation,'ACCEPTED');return value
        def create_publish_task(self,ctx,body):
            existing(ctx.region)
            value=ledger.dispatch_reserved('create_publish_task',lambda payload:super(Regional,self).create_publish_task(ctx,payload),body)
            # Original parser rejects error/malformed/missing task identity.
            code,_=_provider_error(value)
            if code and code!='MALFORMED_RESPONSE':
                ledger.complete_reserved('create_publish_task','REJECTED');return value
            task=_publish_task_id(value)
            if not task.isdigit() or int(task)<=0:raise ValueError('NATIVE_SHOPEE_PUBLISH_TASK_ID_UNAVAILABLE')
            ledger.complete_reserved('create_publish_task','ACCEPTED')
            references.append('shopee:'+str(ctx.shop_id)+':publish-task:'+task);return value
        def regional_item(self,ctx,item_id):
            row=super().regional_item(ctx,item_id)
            if isinstance(row,Mapping) and str(row.get('item_id') or '')==str(item_id):
                references.append('shopee:'+str(ctx.shop_id)+':item:'+str(item_id))
            return row
        def list_item(self,ctx,item):return self._known('list_regional_item',super().list_item,{'item_id':item},ctx,item)
        def update_regional_images(self,ctx,item,*,image_ids):return self._known('update_regional_images',super().update_regional_images,{'item_id':item,'image_ids':image_ids},ctx,item,image_ids=image_ids)
        def update_regional_copy(self,ctx,item,*,title,description):return self._known('update_regional_copy',super().update_regional_copy,{'item_id':item,'title':title,'description':description},ctx,item,title=title,description=description)
        def update_regional_description(self,ctx,item,*,description,image_ids):return self._known('update_description_media',super().update_regional_description,{'item_id':item,'description':description,'image_ids':image_ids},ctx,item,description=description,image_ids=image_ids)
        def enable_applicable_logistics(self,*args,**kwargs):raise ValueError('NATIVE_SHOPEE_LOGISTICS_REPAIR_NOT_IN_FROZEN_BUDGET')
        def upload_regional_images(self,ctx,urls,*,before_upload=None):
            # Per-image calls consume each reservation made by the canonical
            # callback, and validated identities precede known completion.
            ids=[]
            for position,url in enumerate(urls):
                existing(ctx.region)
                if before_upload is not None:before_upload()
                value=ledger.dispatch_reserved('upload_regional_image',_default_image_upload,url,position)
                if type(value) is not str or not value.strip():raise ValueError('NATIVE_SHOPEE_IMAGE_ID_UNAVAILABLE')
                ledger.complete_reserved('upload_regional_image','ACCEPTED');ids.append(value)
            if len(set(ids))!=len(ids):raise ValueError('NATIVE_SHOPEE_IMAGE_ID_AMBIGUOUS')
            return {'image_ids':ids,'external_write_count':len(ids)}
    return build_live_shopee_dependencies(resolver=ShopeeGlobalV4Resolver(runtime=global_runtime),runtime=Regional())
