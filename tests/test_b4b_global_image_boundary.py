"""Default global download/prepare/upload boundary, with only lowest I/O replaced."""
from types import SimpleNamespace
import pytest
from modules.shopee import client, oneclick_release
from modules.shopee.global_v4_live_runtime import OfficialShopeeGlobalV4Runtime, _default_image_upload
from modules.shopee.global_v4_executor import ShopeeGlobalV4Resolver
from shared_platform.publication_write_budget import PublicationWriteBudgetLedger
from test_shopee_global_v4_executor import _request, _Runtime


@pytest.mark.parametrize('stage,index,actual,unknown', [
    ('download',1,0,False), ('download',2,1,False),
    ('decode',1,0,False), ('decode',2,1,False),
    ('post_timeout',2,2,True), ('post_response',2,2,True),
])
def test_default_global_image_failure_truthful_counts(tmp_path, monkeypatch, stage, index, actual, unknown):
    base=_request()
    ledger=PublicationWriteBudgetLedger(platform='SHOPEE',target_labels=base.target_labels,
        budget={'shared_maximum':4,'per_target_maximum':0})
    request=SimpleNamespace(run_id=base.run_id,report_id=base.report_id,snapshot=base.snapshot,
        platform=base.platform,target_labels=base.target_labels,write_budget_ledger=ledger,
        release_candidate={'fixture':True})
    downloads=[]; uploads=[]
    def download(url):
        downloads.append(url)
        if stage=='download' and len(downloads)==index:
            raise OSError('synthetic download failure')
        content = b'not-an-image' * 1000000 if stage=='decode' and len(downloads)==index else b'fixture-image'
        return SimpleNamespace(content=content,suffix='.jpg')
    def upload(path, *, scene):
        uploads.append(scene)
        assert ledger.shared_attempt_count==len(uploads)
        if stage=='post_timeout' and len(uploads)==index:
            raise TimeoutError('synthetic provider response lost')
        if stage=='post_response' and len(uploads)==index:
            return {}
        return {'image_info':{'image_id':'fixture-'+str(len(uploads))}}
    monkeypatch.setattr(oneclick_release,'_download_public_https_image',download)
    monkeypatch.setattr(client,'upload_image',upload)
    official=OfficialShopeeGlobalV4Runtime(context_resolver=lambda _: {}, official_fact_reader=lambda *_: {},
        mapping_lookup=lambda _:None,image_upload_transport=_default_image_upload,checkpoint_root=tmp_path)
    runtime=_Runtime()
    original_lookup=runtime.lookup_global_item_ids
    def lookup(command):
        official.lookup_global_item_ids(command)
        return original_lookup(command)
    runtime.lookup_global_item_ids=lookup
    runtime.checkpointed_upload_global_images=official.checkpointed_upload_global_images
    resolver=ShopeeGlobalV4Resolver(runtime=runtime)
    with pytest.raises((OSError, RuntimeError)) as caught:
        resolver(request)
    assert len(uploads)==actual
    assert ledger.shared_attempt_count==actual
    assert resolver.write_count(request)==(None if unknown else actual)
    assert caught.value.image_upload_outcome_unknown is unknown
    assert caught.value.completed_image_upload_count == (actual-1 if unknown else actual)
    assert 'create' not in runtime.calls
