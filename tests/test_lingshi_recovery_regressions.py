"""Regressions originally reproduced as three failures on the B3A base."""
from io import BytesIO
import json

from PIL import Image
import pytest

from modules.sourcing import brand_image_lingshi_generation as brand
from modules.sourcing import localized_image_lingshi_generation as localized
from modules.sourcing.lingshi_client import LingshiClientError


def png():
    buffer=BytesIO()
    Image.new('RGB',(16,16),'white').save(buffer,format='PNG')
    return buffer.getvalue()


class FixtureClient:
    def __init__(self):
        self.created=0
        self.fail=True
    def create_media_generation(self,**kwargs):
        self.created+=1
        if self.fail:
            raise LingshiClientError('fixture transport timeout')
        return {'code':200,'data':{'task_id':42}}
    def get_media_task(self,task_id):
        return {'is_final':True,'result_url':'https://assets.example/result.png'}


def arguments(tmp_path, client):
    return dict(source_url='https://assets.example/source.png',source_bytes=png(),locale='th-TH',
                translations=[{'source_text':'CLEAN','translated_text':'สะอาด'}],checkpoint_dir=tmp_path,
                client=client,result_loader=lambda _url:png())


def test_s02_unknown_attempt_cannot_be_bypassed_by_incrementing_retry(tmp_path):
    client=FixtureClient()
    kwargs=arguments(tmp_path,client)
    with pytest.raises(LingshiClientError):
        localized.generate_localized_reference_image(**kwargs)
    client.fail=False
    try:
        localized.generate_localized_reference_image(**kwargs,retry_attempt=1)
    except RuntimeError:
        pass
    assert client.created==1,'S02: retry_attempt bypassed unresolved SUBMISSION_UNKNOWN and submitted a second task'


def test_s02_submitting_checkpoint_requires_reconciliation_before_resubmit(tmp_path):
    client=FixtureClient()
    kwargs=arguments(tmp_path,client)
    with pytest.raises(LingshiClientError):
        localized.generate_localized_reference_image(**kwargs)
    path=next(tmp_path.glob('lingshi-*.json'))
    state=json.loads(path.read_text(encoding='utf-8'))
    state['status']='SUBMITTING'
    path.write_text(json.dumps(state),encoding='utf-8')
    client.fail=False
    try:
        localized.generate_localized_reference_image(**kwargs)
    except RuntimeError:
        pass
    assert client.created==1,'S02: crash-recovered SUBMITTING state created another task without reconciliation'


def test_s02_brand_prompt_basis_change_must_not_reuse_old_completed_output(tmp_path,monkeypatch):
    client=FixtureClient()
    client.fail=False
    monkeypatch.setattr(brand,'_download_result',lambda _url:png())
    kwargs=dict(offer_id='fixture-product',brand_id='fixture-brand',brand_label='Fixture',positioning='plain',
                role='cover',brief='verified object',product_identity='original verified object',
                source_urls=['https://assets.example/source.png'],checkpoint_dir=tmp_path,client=client)
    first=brand.generate_brand_image(**kwargs)
    kwargs['product_identity']='changed verified product fact'
    try:
        changed=brand.generate_brand_image(**kwargs)
    except RuntimeError:
        return
    assert changed['receipt']['client_business_id']!=first['receipt']['client_business_id'], \
        'S02: changed product_identity reused checkpoint because full prompt basis is absent from identity digest'
