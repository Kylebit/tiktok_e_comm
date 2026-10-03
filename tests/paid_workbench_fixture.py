"""Approved legacy review setup with real local store and fake external boundaries."""
import hashlib
from test_publication_paid_entry import OFFER,translator
from test_localized_image_review import _snapshot
from test_localized_image_workbench import _FakeOcr,_image_bytes
from modules.sourcing import new_product_workbench as wb,localized_image_ocr as ocr

def prepared_review(workflow,monkeypatch):
    w=workflow
    monkeypatch.setattr(ocr,'detect_english_text_regions',w.original_ocr)
    monkeypatch.setattr(wb,'resolve_offer_key',lambda value:str(value))
    monkeypatch.setattr(wb,'LOCALIZED_IMAGE_REVIEWS_DIR',w.root/'data/localized_image_reviews')
    snapshot=_snapshot();snapshot['offer_id']=OFFER
    store=wb._localized_image_review_store();project=store.initialize(snapshot,selected_positions=[1])
    url=project['tasks'][0]['source_url']
    bridge={'offer_id':OFFER,'round1_snapshot_digest':w.round1['snapshot_digest'],'legacy_snapshot_digest':project['approved_snapshot_digest'],
        'tasks':[{'source_url':url,'source_digest':hashlib.sha256(_image_bytes()).hexdigest(),'brand_id':'livelyhive-sea','role':'cover','locale':locale}
                 for locale in translator.AUTO_TRANSLATION_LOCALES]}
    kwargs=dict(expected_revision=project['revision'],source_bytes_by_url={url:_image_bytes()},
        confirm_paid_generation=True,ocr_engine=_FakeOcr(),paid_context=w.context(),approved_bridge=bridge)
    return store,project,kwargs
