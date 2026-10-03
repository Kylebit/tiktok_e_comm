"""Offline keyword CLI contracts: no credentials, API, or business state."""
import importlib.util
from pathlib import Path
import json
import sys
from types import ModuleType
import pytest


@pytest.fixture
def keyword(monkeypatch):
    core = ModuleType('core'); auth = ModuleType('core.auth')
    api = ModuleType('core.api_client'); shops = ModuleType('core.shops')
    sourcing = ModuleType('modules.sourcing'); workbench = ModuleType('modules.sourcing.new_product_workbench')
    def denied(*args, **kwargs):
        raise AssertionError('unexpected unmocked external/state call')
    auth.access_token = denied; api.post = denied; shops.list_shops = denied
    workbench.load_state = denied; workbench.save_state = denied; workbench.SEA_MARKETS=[]
    core.auth=auth; sourcing.new_product_workbench=workbench
    for name,value in {'core':core,'core.auth':auth,'core.api_client':api,'core.shops':shops,
                       'modules.sourcing':sourcing,'modules.sourcing.new_product_workbench':workbench}.items():
        monkeypatch.setitem(sys.modules,name,value)
    path=Path(__file__).resolve().parents[1]/'skills/prepare-product-publication/scripts/read_tiktok_keyword_evidence.py'
    spec=importlib.util.spec_from_file_location('r1_keyword_contract',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_keyword_cli_requires_explicit_read_flag_without_io(keyword):
    with pytest.raises(SystemExit) as error:
        keyword.main(['--offer-id','9990000001','--expected-revision','2'])
    assert error.value.code==2


def test_stale_revision_blocks_before_credentials(keyword,monkeypatch):
    monkeypatch.setattr(keyword.np_mod,'load_state',lambda offer:{'_revision':3})
    with pytest.raises(RuntimeError,match='revision changed'):
        keyword.read_keyword_evidence('9990000001',expected_revision=2)


@pytest.mark.parametrize('concurrent_change', [False, True])
def test_keyword_read_preserves_identity_and_labels_inheritance(keyword,tmp_path,monkeypatch,concurrent_change):
    monkeypatch.setattr(keyword,'REPO_ROOT',tmp_path)
    folder=tmp_path/'reports/product-preparation/9990000001';folder.mkdir(parents=True)
    packet={'product_center_revision':2,'target_selection':{'requested':['tiktok:LH_PH','tiktok:HB_PH']},
            'targets':[{'target':'tiktok:LH_PH','category':{'candidate':'600338 · Fixtures'}}]}
    (folder/'first-review.json').write_text(json.dumps(packet),encoding='utf8')
    state={'_revision':2,'review':{'title':'Approved item'}}
    monkeypatch.setattr(keyword.np_mod,'load_state',lambda offer:dict(state))
    monkeypatch.setattr(keyword.np_mod,'SEA_MARKETS',[{'id':'lh_ph','shop_id':'shop-ph'}])
    monkeypatch.setattr(keyword.auth,'access_token',lambda:'synthetic-token')
    monkeypatch.setattr(keyword,'list_shops',lambda token:[{'id':'shop-ph','name':'LivelyHive','region':'PH','cipher':'synthetic-cipher'}])
    calls=[];saved=[]
    def request(path,token,query,body):
        calls.append((path,query,body))
        if concurrent_change:
            state['_revision'] = 3
        return {'code':0,'data':{'diagnoses':[{'field':'TITLE','suggestion':{'seo_words':[{'text':'Waterproof'}]}}]}}
    def save(offer,value):
        if value['_revision'] != state['_revision']:
            raise RuntimeError('revision changed during official read')
        saved.append((offer,value));return {**value,'_revision':3}
    monkeypatch.setattr(keyword,'api_post',request);monkeypatch.setattr(keyword.np_mod,'save_state',save)
    if concurrent_change:
        with pytest.raises(RuntimeError, match='revision changed during official read'):
            keyword.read_keyword_evidence('9990000001',expected_revision=2)
        assert len(calls) == 1 and saved == []
        assert 'tiktok_keyword_evidence' not in state
        return
    result=keyword.read_keyword_evidence('9990000001',expected_revision=2)
    assert len(calls)==len(saved)==1
    assert calls[0][0]=='/product/202411/products/diagnose_optimize'
    assert calls[0][2]=={'category_id':'600338','title':'Approved item','optimization_fields':['TITLE']}
    assert saved[0][0]=='9990000001' and saved[0][1]['review']==state['review']
    assert result['revision']==3 and result['external_write_count']==0
    direct,follower=result['evidence']['targets']
    assert direct['status']=='OFFICIAL_READ_COMPLETED'
    assert follower['status']=='INHERITED_LIVELYHIVE_SEA_EVIDENCE'
    assert follower['source_target_labels']==['tiktok:LH_PH']
    assert direct['keywords'][0]['fact_verified'] is False
    assert 'synthetic-token' not in json.dumps(result) and 'synthetic-cipher' not in json.dumps(result)


def test_keyword_category_conflict_and_cross_brand_match_fail_closed(keyword):
    with pytest.raises(RuntimeError,match='shared TikTok leaf'):
        keyword._tiktok_category_id({'targets':[{'target':'tiktok:LH_PH','category':{'candidate':'1 · A'}},
                                                {'target':'tiktok:LH_MY','category':{'candidate':'2 · B'}}]})
    assert keyword._official_shop_for_target('tiktok:LH_PH',{'shop_id':'legacy'},[
        {'id':'one','name':'HomeBloom','region':'PH'},{'id':'two','name':'LivelyHive','region':'MY'}]) is None


@pytest.mark.parametrize('offer', ['', ' ', '１２３', '../123', 'C:/123', '/123', '123/456', '1'*33, 123, None])
def test_invalid_offer_rejected_before_any_io(keyword, offer):
    with pytest.raises(ValueError, match='offer_id'):
        keyword.read_keyword_evidence(offer, expected_revision=2)


@pytest.mark.parametrize('revision', [-1, True, False, 1.5, '2', None])
def test_invalid_revision_rejected_before_any_io(keyword, revision):
    with pytest.raises(ValueError, match='expected_revision'):
        keyword.read_keyword_evidence('9990000001', expected_revision=revision)
