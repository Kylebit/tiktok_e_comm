"""Exact original403 R2 image compatibility; never COMMON or approval authority.

Startup must pin the separate adoption record and historical six files. A caller
marker cannot replace the original continuation, current R2 byte checks, original
COMMON completion or protected final decision. Pure functions below also serve
owned no-network production-body fixtures.
"""
from copy import deepcopy
import hashlib,json
from pathlib import Path

OFFER='4039931504'
TASK='TASK-20261006-001'
TARGETS=('tiktok:LH_PH','tiktok:LH_MY','tiktok:LH_TH','tiktok:LH_VN','tiktok:HB_PH','tiktok:HB_MY','tiktok:HB_TH','tiktok:HB_VN',
         'tiktok:MX','tiktok:GB','shopee:PH','shopee:MY','shopee:TH','shopee:VN','ozon:RU')
COMMON_RUN='common-technical:7bd627478fbe904bf246c88daebad5c5'
OLD_MARKET='r3-marketplace:4039931504:7f896f40e75ff18107b03a4b'
OLD_MARKET_SHA='6d457a6c7747637dbc2a52b6a6231704578dab0608ee6adc8ce0468109690b6a'
GALLERY_PREDECESSOR={'plan_id':'r3-marketplace:4039931504:c6c6402ee225aece80e5b7dd',
    'payload_digest':'111ae31be4ae07940ea9f5508be0549d0f68ee6962add6c11e0d38123ce844f9',
    'candidate_digest':'2a70db959e64c8b7b2865b6e558aea67f9e7fbd7896640e17f11853a0ff8d7d7'}
COMMON_PLAN='r3-common:4039931504:fe60e2688df207eb81d4fb22'
SOURCE_SHA='sha256:781c407235a9b6e67ee6b829366b9750e45b907433e75e785c0409c9b146487d'

def deny(code):raise ValueError(code)
def require(value,code):
    if not value:deny(code)
def digest(value):return 'sha256:'+hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def image_relation(predecessor_documents,current_documents,common_plan,common_run,*,validate_identity,route_builder):
    """Keep actual old COMMON identity; accept only an image-only current R2."""
    old=validate_identity(predecessor_documents);new=validate_identity(current_documents)
    require(old['offer_id']==new['offer_id']==OFFER and old!=new,'IMAGE_SUCCESSOR_NEW_R2_REQUIRED')
    for key in ('round1_snapshot','first_review'):
        require(predecessor_documents[key]==current_documents[key],'IMAGE_SUCCESSOR_FROZEN_R1_FACTS_CHANGED')
    r1=current_documents['round1_snapshot'];require(len(r1['canonical_targets'])==15 and set(r1['canonical_targets'])==set(TARGETS),'IMAGE_SUCCESSOR_EXACT15_REQUIRED')
    def route(documents):
        return route_builder(offer_id=OFFER,first_review=documents['first_review'],generation=documents['generation_result'],
            translation=documents['translation_result'],approved_by=r1['approved_by'])
    prior=route(predecessor_documents);current=route(current_documents)
    binding=common_plan['payload']['r3_stage_binding']
    require(common_plan['plan_id']==COMMON_PLAN and common_run['run_id']==COMMON_RUN and common_run['plan_id']==COMMON_PLAN
        and common_run['status']=='SUCCEEDED' and binding['r2_identity']==old,'IMAGE_SUCCESSOR_OLD_COMMON_BINDING_CHANGED')
    target_rows=common_run.get('targets') or []
    require(len(target_rows)==1 and target_rows[0]['target_label']=='miaoshou:COMMON' and target_rows[0]['status']=='SUCCEEDED'
        and target_rows[0].get('readback'),'IMAGE_SUCCESSOR_RETAINED_COMMON_READBACK_REQUIRED')
    old_urls=[row['url'] for row in prior['common_miaoshou_baseline']]
    require([row['image_url'] for row in common_plan['payload']['images']]==old_urls,'IMAGE_SUCCESSOR_OLD_COMMON_GALLERY_CHANGED')
    from shared_platform.publication_gallery_contract import validate_gallery_documents
    gallery = validate_gallery_documents(current_documents, current)
    new_urls=[row['url'] for row in current['common_miaoshou_baseline']]
    require(len(old_urls)==1 and len(new_urls)>=gallery['policy']['minimum_per_brand']
        and not set(old_urls).intersection(new_urls),'IMAGE_SUCCESSOR_COMPLETE_GALLERY_MAPPING_REQUIRED')
    require(set(current['image_routes'])==set(TARGETS),'IMAGE_SUCCESSOR_ALL15_ROUTES_REQUIRED')
    for label,rows in current['image_routes'].items():
        family='homebloom-sea' if label.startswith('tiktok:HB_') else 'livelyhive-sea'
        require([row['position'] for row in rows]==list(range(1,len(rows)+1))
            and all(row['brand_id']==family for row in rows),'IMAGE_SUCCESSOR_ROUTE_BRAND_OR_ORDER_CHANGED')
        require(not any(row['url'] in old_urls for row in rows),'IMAGE_SUCCESSOR_OLD_IMAGE_LEAK')
    body={'schema_version':'original403-r2-image-relation/v1','offer_id':OFFER,'task_id':TASK,
        'predecessor_r2_identity':old,'current_r2_identity':new,'round1_snapshot_digest':new['round1_snapshot_digest'],
        'retained_COMMON_plan_id':COMMON_PLAN,'retained_COMMON_run_id':COMMON_RUN,
        'retained_COMMON_payload_digest':common_plan['payload_digest'],'retained_COMMON_readback':deepcopy(target_rows[0]['readback']),
        'COMMON_image_state':'VERIFIED_PREDECESSOR_ONLY_NOT_NEW_GENERATED_IMAGES','COMMON_confirmed_write_count':1,'COMMON_cap':1,
        'COMMON_write':0,'marketplace_approval':False,'source_image_digest':SOURCE_SHA,
        'old_base_urls':old_urls,'new_base_urls':new_urls,'current_route':current,'gallery_completeness':gallery}
    return {**body,'relation_digest':digest(body)}

def replace_final_image_fields(payload,relation):
    """Finite production image slots; no change to COMMON, facts or budgets."""
    require(tuple(payload['targets'])==TARGETS and payload['product_id']==OFFER,'IMAGE_SUCCESSOR_PAYLOAD_SCOPE_CHANGED')
    value=deepcopy(payload);old=relation['old_base_urls'];new=relation['new_base_urls']
    require(len(old)==1 and len(new)>=7,'IMAGE_SUCCESSOR_ROLE_MAPPING_REQUIRED')
    def mapped(rows):
        require(rows==old,'IMAGE_SUCCESSOR_UNMAPPED_VARIANT_IMAGE')
        return list(new)
    for row in value['images']:
        require(row['image_url'] in old,'IMAGE_SUCCESSOR_UNMAPPED_PRODUCT_IMAGE')
    facts=value['product_facts'];facts['image_urls']=mapped(facts.get('image_urls',old))
    require(len(facts['sku_commercial_facts'])==1,'IMAGE_SUCCESSOR_EXACT_MODEL0989_REQUIRED')
    for row in facts['sku_commercial_facts'].values():row['image_urls']=mapped(row.get('image_urls',old))
    master=value.get('shopee_global_master')
    if master:
        positions=master['variant_image_positions'];require(len(positions)==1 and positions[0]['model_sku']=='0989'
            and positions[0]['position']==0 and positions[0]['image_url'] in old,'IMAGE_SUCCESSOR_SHOPEE_VARIANT_BINDING_CHANGED')
        # Shopee's canonical single-model thumbnail is the new cover. Its seven
        # gallery/extended-description images remain independently mandatory.
        positions[0]['image_url']=new[0]
    require(set(value['localized_image_routing']['routes'])==set(TARGETS),'IMAGE_SUCCESSOR_ROUTE_COVERAGE_REQUIRED')
    for label in TARGETS:
        expected=[row['url'] for row in relation['current_route']['image_routes'][label]]
        require(value['localized_image_routing']['routes'][label]['ordered_images']==expected,'IMAGE_SUCCESSOR_ORDERED_ROUTE_CHANGED')
    return value

def assert_target_image_override(label,*,gallery,variant_images,description_images,relation,description_mode):
    """Check actual canonical mutation arguments before any provider transport."""
    require(label in TARGETS,'IMAGE_SUCCESSOR_UNKNOWN_TARGET')
    expected=[row['url'] for row in relation['current_route']['image_routes'][label]]
    expected_variants = expected[:1] if label.startswith('shopee:') else expected
    require(gallery==expected and variant_images==expected_variants,'IMAGE_SUCCESSOR_CANONICAL_IMAGE_OVERRIDE_MISSING')
    if label=='ozon:RU':
        require(description_mode=='PLAIN_TEXT_NO_IMAGE_FIELD' and description_images==[],
            'IMAGE_SUCCESSOR_OZON_DESCRIPTION_CONTRACT_CHANGED')
    elif description_mode=='ORDERED_IMAGES':require(description_images==expected,'IMAGE_SUCCESSOR_DESCRIPTION_IMAGE_ORDER_CHANGED')
    else:deny('IMAGE_SUCCESSOR_DESCRIPTION_CONTRACT_UNKNOWN')
    return {'target_label':label,'gallery':gallery,'variant_images':variant_images,'description_images':description_images,
        'description_mode':description_mode,'COMMON_images_consumed_as_final':False,'relation_digest':relation['relation_digest']}


def _read_pin(reference):
    from shared_platform.immutable_approval_files import require_local_path
    require(type(reference) is dict and set(reference)=={'path','sha256'},'IMAGE_SUCCESSOR_REFERENCE_REQUIRED')
    path=Path(reference['path']);require_local_path(path,root=path.parent)
    raw=path.read_bytes();require(hashlib.sha256(raw).hexdigest()==reference['sha256'],'IMAGE_SUCCESSOR_PIN_CHANGED')
    return json.loads(raw)


class OriginalImagesSuccessor:
    """Private startup-owned reader, retaining historical COMMON image truth."""
    def __init__(self,reference,continuation):
        from shared_platform.original_prepared_continuation import OriginalPreparedTaskContinuation
        from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS,validate_r2_identity,build_dual_brand_publication_bridge
        require(type(continuation) is OriginalPreparedTaskContinuation and continuation.pins.offer_id==OFFER
            and continuation.pins.task_id==TASK,'IMAGE_SUCCESSOR_CONCRETE_ORIGINAL_REQUIRED')
        self.reference=deepcopy(reference);self.continuation=continuation
        document=_read_pin(reference)
        keys={'schema_version','offer_id','task_id','status','source_ready_ref','predecessor_archive_ref','current_documents',
              'predecessor_r2_identity','current_r2_identity','COMMON_plan_id','COMMON_run_id','authority_ref','independent_acceptance_ref'}
        v3=document.get('schema_version')=='original403-images-successor-adoption/v3'
        v2=v3 or document.get('schema_version')=='original403-images-successor-adoption/v2'
        if v2:keys|={'marketplace_predecessor'}
        require(type(document) is dict and set(document)==keys
            and document['schema_version'] in {'original403-images-successor-adoption/v1','original403-images-successor-adoption/v2','original403-images-successor-adoption/v3'}
            and document['status']=='ADOPTED_IMAGES_ONLY_NOT_MARKETPLACE_APPROVED' and document['offer_id']==OFFER
            and document['task_id']==TASK and document['COMMON_plan_id']==COMMON_PLAN and document['COMMON_run_id']==COMMON_RUN,
            'IMAGE_SUCCESSOR_EXACT_ADOPTION_REQUIRED')
        expected_predecessor = ({'plan_id':'r3-marketplace:4039931504:190ca68f217a99ee6c0f7ec2',
            'payload_digest':'85fc4ef4d24018c12d0b5032a51c797d160bc096c79389202f0022fd08133abc'} if v3 else GALLERY_PREDECESSOR)
        self.marketplace_predecessor = expected_predecessor if v2 else {
            'plan_id':OLD_MARKET,'payload_digest':OLD_MARKET_SHA}
        if v2:
            require(document['marketplace_predecessor']==expected_predecessor,
                    'IMAGE_SUCCESSOR_EXACT_UNAPPROVED_GALLERY_PREDECESSOR_REQUIRED')
        source_ready=_read_pin(document['source_ready_ref']);archive=_read_pin(document['predecessor_archive_ref'])
        require(source_ready['original_task_id']==TASK and source_ready['COMMON_history']['run_id']==COMMON_RUN
            and source_ready['COMMON_history']['used']==1 and source_ready['COMMON_history']['cap']==1,
            'IMAGE_SUCCESSOR_OLD_COMMON_HISTORY_CHANGED')
        authority=_read_pin(document['authority_ref']);acceptance=_read_pin(document['independent_acceptance_ref'])
        require(authority['offer_id']==OFFER and authority['task_id']==TASK
            and authority.get('completion') is not None and acceptance.get('status')=='PASS'
            and acceptance.get('current_r2_identity')==document['current_r2_identity']
            and acceptance.get('COMMON_baseline_r2_identity' if v2 else 'predecessor_r2_identity')==document['predecessor_r2_identity']
            and acceptance.get('COMMON_write')==0 and acceptance.get('marketplace_approval') is False,
            'IMAGE_SUCCESSOR_AUDITED_COMPLETE_R2_REQUIRED')
        copies={Path(row['original']['path']).name:row for row in archive['refs']}
        old_ready_pins={Path(ref['path']).name:ref['sha256'] for ref in source_ready['retained_R1_R2_files_verified']}
        self.old_refs={}
        for name,filename in R2_DOCUMENTS.items():
            row=copies[filename]
            require(row['original']['sha256']==row['exclusive_copy']['sha256']==old_ready_pins[filename],
                'IMAGE_SUCCESSOR_PREDECESSOR_ARCHIVE_CHANGED')
            self.old_refs[name]=row['exclusive_copy']
        require(set(document['current_documents'])==set(R2_DOCUMENTS),'IMAGE_SUCCESSOR_CURRENT_SIX_REQUIRED')
        self.new_refs=document['current_documents']
        expected=continuation.r2_profile.root/'reports/product-preparation'/OFFER
        original_pins={str(file.path.resolve()):file.sha256 for file in continuation.pins.documents}
        for name,filename in R2_DOCUMENTS.items():
            ref=self.new_refs[name];path=Path(ref['path'])
            require(path.resolve()==(expected/filename).resolve() and original_pins.get(str(path.resolve()))==ref['sha256'],
                'IMAGE_SUCCESSOR_CURRENT_PRODUCER_PIN_CHANGED')
        self.old_identity=validate_r2_identity(self.predecessor_documents())
        self.current_identity=validate_r2_identity(self.current_documents())
        if v2:
            require('image_plan_successor_digest' in self.current_identity,
                    'IMAGE_SUCCESSOR_EXPLICIT_GALLERY_PLAN_REQUIRED')
            # The gallery predecessor is the qualified two-master R2, while
            # COMMON's factual/image receipt belongs to the older original R2.
            # The independent acceptance must name both; never relabel one as
            # the other merely to satisfy the old v1 equality.
            sidecar=_read_pin(self.current_documents()['generation_result']['gallery_image_plan_successor'])
            if v3:
                require(sidecar.get('schema_version')=='gallery-image-plan-successor/v2',
                        'IMAGE_SUCCESSOR_APPEND_GALLERY_PLAN_REQUIRED')
                gallery_archive=_read_pin(sidecar['retained_gallery_bundle_ref'])
                require(sidecar['retained_gallery_bundle_ref']['sha256']==
                    'a0c96cea9430af3bfe0878850974f239f6d51fd8b03e174b4adc9e080c895b13',
                    'IMAGE_SUCCESSOR_APPEND_PREDECESSOR_CHANGED')
                rows={row['role']:row for row in gallery_archive['documents'] if row['role'] in R2_DOCUMENTS}
                gallery_documents={}
                for name in R2_DOCUMENTS:
                    row=rows[name]
                    require(row['original']['sha256']==row['exclusive_copy']['sha256'],
                            'IMAGE_SUCCESSOR_GALLERY_PREDECESSOR_ARCHIVE_CHANGED')
                    gallery_documents[name]=_read_pin(row['exclusive_copy'])
            else:
                gallery_archive=_read_pin(sidecar['predecessor_bundle_ref'])
                gallery_copies={Path(row['original']['path']).name:row for row in gallery_archive['files']}
                gallery_documents={}
                for name,filename in R2_DOCUMENTS.items():
                    row=gallery_copies[filename]
                    require(row['original']['sha256']==row['exclusive_copy']['sha256'],
                            'IMAGE_SUCCESSOR_GALLERY_PREDECESSOR_ARCHIVE_CHANGED')
                    gallery_documents[name]=_read_pin(row['exclusive_copy'])
            self.gallery_predecessor_identity=validate_r2_identity(gallery_documents)
            require(self.gallery_predecessor_identity['identity_digest']==
                ('sha256:2f787564e9696aa34ff681694624f1a1c7a3b8fc92b1d6a91831eb7b85bebb15' if v3 else
                 'sha256:2f24bee18c8b68a90619c67233b86285bdd3ee3ffec7c81f8fee24865b812b25')
                and acceptance.get('predecessor_r2_identity')==self.gallery_predecessor_identity
                and self.gallery_predecessor_identity!=self.old_identity,
                'IMAGE_SUCCESSOR_TWO_DISTINCT_PREDECESSOR_LAYERS_REQUIRED')
        require(self.old_identity==document['predecessor_r2_identity'] and self.current_identity==document['current_r2_identity']
            and self.current_identity==json.loads(continuation.pins.r2_identity_json),'IMAGE_SUCCESSOR_IDENTITY_CHANGED')
        self._route_builder=build_dual_brand_publication_bridge
        self._validate_identity=validate_r2_identity
        # Original getter paths are read-only; no plan/create/initialize_schema.
        common=continuation.release_store.get_plan(COMMON_PLAN);run=continuation.release_store.get_run(COMMON_RUN)
        self.relation=image_relation(self.predecessor_documents(),self.current_documents(),common,run,
            validate_identity=validate_r2_identity,route_builder=build_dual_brand_publication_bridge)
        self.document=document
        self.retained_common(continuation.release_store)

    def predecessor_documents(self):return {name:_read_pin(ref) for name,ref in self.old_refs.items()}
    def current_documents(self):return {name:_read_pin(ref) for name,ref in self.new_refs.items()}
    def reduction_identity(self,identity):
        require(identity in (self.old_identity,self.current_identity),'IMAGE_SUCCESSOR_TARGET_REDUCTION_IDENTITY_CHANGED')
        self.current_documents();self.predecessor_documents()
        return self.old_identity
    def for_common(self,documents,common_plan,common_run):
        require(self._validate_identity(documents)==self.current_identity,'IMAGE_SUCCESSOR_CURRENT_DOCUMENTS_CHANGED')
        relation=image_relation(self.predecessor_documents(),documents,common_plan,common_run,
            validate_identity=self._validate_identity,route_builder=self._route_builder)
        require(relation==self.relation,'IMAGE_SUCCESSOR_RETAINED_COMMON_RELATION_CHANGED')
        return relation
    def payload_binding(self):return {'reference':deepcopy(self.reference),'relation':deepcopy(self.relation)}

    def validate_payload_binding(self,binding):
        require(binding.get('r2_identity')==self.current_identity
            and binding.get('image_successor')==self.payload_binding(),
            'IMAGE_SUCCESSOR_PAYLOAD_BINDING_CHANGED')
        require(binding.get('documents')==self.current_documents(),
            'IMAGE_SUCCESSOR_PAYLOAD_DOCUMENTS_CHANGED')
        self.predecessor_documents()
        return self.old_identity

    def retained_common(self,store):
        from shared_platform.native_common_retained_completion import read_retained_completion
        from shared_platform.r3_common_source_facts import NativeCommonSourceReader
        require(store.path.resolve()==self.continuation.release_store.path.resolve(),
            'IMAGE_SUCCESSOR_COMMON_STORE_CHANGED')
        with store._connect_readonly() as db:
            db.execute('BEGIN')
            completion=read_retained_completion(NativeCommonSourceReader(store),db,COMMON_PLAN,COMMON_RUN)
        plan=store.get_plan(COMMON_PLAN);run=store.get_run(COMMON_RUN)
        self.for_common(self.current_documents(),plan,run)
        require(completion.state=='CONFIRMED_WRITE' and completion.payload_digest==plan['payload_digest'],
            'IMAGE_SUCCESSOR_COMMON_COMPLETION_CHANGED')
        return plan,run

    def retained_common_view(self,store):
        plan,run=self.retained_common(store)
        return {'ok':True,'schema_version':'publication-stages/v1','offer_id':OFFER,
            'external_writes_performed':[],
            'common':{'status':'RETAINED_TECHNICAL_BASELINE','plan':plan,'run':run,'blockers':[],
                'execution_scope':['miaoshou:COMMON'],'new_common_human_approval_needed':False,
                'images_state':'VERIFIED_PREDECESSOR_ONLY_NOT_NEW_GENERATED_IMAGES',
                'image_successor':self.payload_binding()},
            'marketplace':{'status':'NOT_FROZEN','targets':list(TARGETS)}}

    def create_marketplace_successor(self,store,payload):
        """Existing atomic local-plan succession; no COMMON/provider capability."""
        self.validate_payload_binding(payload['r3_marketplace_binding'])
        self.retained_common(store)
        predecessor_id=self.marketplace_predecessor['plan_id']
        require(payload['plan_id']!=predecessor_id and tuple(payload['targets'])==TARGETS,
            'IMAGE_SUCCESSOR_NEW_MARKETPLACE_PLAN_REQUIRED')
        old=store.get_plan(predecessor_id)
        require(old and old['payload_digest']==self.marketplace_predecessor['payload_digest'] and not old.get('approval')
            and old['status'] in {'PENDING_APPROVAL','SUPERSEDED'}
            and old.get('superseded_by_plan_id') in {None,payload['plan_id']},
            'IMAGE_SUCCESSOR_OLD_CANDIDATE_NOT_UNAPPROVED')
        with store._connect_readonly() as db:
            db.execute('BEGIN')
            require(not db.execute('SELECT 1 FROM release_runs WHERE plan_id=?',(predecessor_id,)).fetchone()
                and not db.execute('SELECT 1 FROM native_sole_final_decisions WHERE plan_id=?',(predecessor_id,)).fetchone(),
                'IMAGE_SUCCESSOR_OLD_CANDIDATE_EXECUTION_OR_APPROVAL_PRESENT')
        return store.create_plan(payload,supersedes_plan_id=predecessor_id,image_successor=self)

    def validate_atomic_predecessor(self,db,predecessor,payload):
        """Recheck no approval/inflight under the original create_plan write lock."""
        self.validate_payload_binding(payload['r3_marketplace_binding'])
        predecessor_id=self.marketplace_predecessor['plan_id']
        require(predecessor is not None and predecessor['plan_id']==predecessor_id
            and predecessor['payload_digest']==self.marketplace_predecessor['payload_digest'] and predecessor['status']=='PENDING_APPROVAL'
            and payload['plan_id']!=predecessor_id and tuple(payload['targets'])==TARGETS,
            'IMAGE_SUCCESSOR_ATOMIC_PREDECESSOR_CHANGED')
        for table in ('release_approvals','release_runs','native_sole_final_decisions'):
            require(not db.execute('SELECT 1 FROM '+table+' WHERE plan_id=?',(predecessor_id,)).fetchone(),
                'IMAGE_SUCCESSOR_OLD_CANDIDATE_EXECUTION_OR_APPROVAL_PRESENT')


def installed_successor(offer_id):
    from shared_platform.original_prepared_continuation import configured_original_continuation
    original=configured_original_continuation(offer_id)
    value=getattr(original,'image_successor',None)
    if value is None:return None
    require(type(value) is OriginalImagesSuccessor and value.continuation is original,'IMAGE_SUCCESSOR_PRIVATE_BINDING_CHANGED')
    return value
