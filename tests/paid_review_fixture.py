"""Real offline checkpoint receipts for existing pure review/approval tests."""
import hashlib
from pathlib import Path
import uuid
from test_publication_paid_entry import round1,policy,png,ROLES,FixtureClient
from shared_platform.publication_paid_requests import PaidRequestContext
from modules.sourcing.localized_image_lingshi_generation import generate_localized_reference_image

def generate_review(store,project,*,expected_revision=None):
    offer=project['offer_id']
    root=store._path(offer).parent.parent.parent/('q'+uuid.uuid4().hex[:8])
    context=PaidRequestContext(offer_id=offer,round1=round1(offer),policy=policy(),reports_root=root)
    source=png()
    bridge={'offer_id':offer,'round1_snapshot_digest':context.round1['snapshot_digest'],
        'tasks':[{'source_url':task['source_url'],'source_digest':hashlib.sha256(source).hexdigest(),
                  'brand_id':'livelyhive-sea','role':ROLES[task['position']-1],'locale':task['locale']} for task in project['tasks']]}
    items=[]
    client=FixtureClient();client.calls=[]
    for task in project['tasks']:
        translations=[{'region_id':'text-'+'a'*20,'source_text':'Decor','translated_text':{'th-TH':'สวย','ru-RU':'Декор'}.get(task['locale'],'Decor')}]
        result=generate_localized_reference_image(source_url=task['source_url'],source_bytes=source,locale=task['locale'],translations=translations,
            checkpoint_dir=root/'c',client=client,result_loader=lambda url:png(int(url.rsplit('/',1)[-1].split('.')[0])),paid_context=context,
            business_identity={'offer_id':offer,'brand_id':'livelyhive-sea','role':ROLES[task['position']-1],'locale':task['locale']})
        items.append({'task_id':task['task_id'],'translations':translations,'image_bytes':result['image_bytes'],'receipt':result['receipt']})
    return store.save_generation_bundle(offer,expected_revision=expected_revision or project['revision'],items=items,
        paid_context=context,approved_bridge=bridge)
